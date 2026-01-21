from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any
from sqlalchemy.orm import Session
from db.models import StopTime, Stop, Trip, RealisticStopTime, CalendarDate
from services.vehicle_positions import fetch_vehicle_positions
from services.routes_service import RoutesService
from services.arrival_logger import arrival_logger

# Threshold in minutes to consider a bus "ghost" if no realtime info
REALTIME_GRACE_MINUTES = 7

# How long (in seconds) we trust last seen GPS before downgrading
VEHICLE_POSITION_TTL_SECONDS = 30


class StopsService:
    def __init__(self, db: Session):
        self.db = db
        self.routes_service = RoutesService(db)

        # trip_id -> { "position": {...}, "last_seen": datetime }
        self.vehicle_cache: dict[str, dict[str, Any]] = {}

    def seconds_until(self, arrival_hms: str) -> int:
        """Compute seconds until arrival from HH:MM:SS string, handling hours > 23"""
        try:
            h, m, s = map(int, arrival_hms.split(":"))
        except ValueError:
            return 0

        now = datetime.now()
        extra_days, hour = divmod(h, 24)
        arrival = (
            now.replace(hour=hour, minute=m, second=s, microsecond=0)
            + timedelta(days=extra_days)
        )

        return int((arrival - now).total_seconds())

    def get_all_stops(self):
        return (
            self.db.query(Stop)
            .join(StopTime, Stop.stop_id == StopTime.stop_id)
            .distinct()
            .all()
        )

    def get_latest_arrival_from_logger(self, trip_id: str) -> dict | None:
        """Fetch latest arrival info from in-memory logger for a given trip_id"""
        return arrival_logger.get_latest_arrival(trip_id)

    def _calculate_historic_latency_from_cache(self, static_arrival_time: str, trip_id: str, stop_id: str, stop_sequence: int, realistic_times: dict) -> tuple[int | None, str]:
        """
        Calculate historic latency using pre-fetched realistic times.
        Returns (latency_minutes, relationship_status)
        """
        try:
            key = (trip_id, stop_id, stop_sequence)
            realistic_arrival_time = realistic_times.get(key)

            if not realistic_arrival_time:
                return None, "on time"

            # Parse both times (both use GTFS format with hours >= 24)
            static_h, static_m, static_s = map(int, static_arrival_time.split(":"))
            realistic_h, realistic_m, realistic_s = map(int, realistic_arrival_time.split(":"))

            # Convert to total seconds
            static_seconds = static_h * 3600 + static_m * 60 + static_s
            realistic_seconds = realistic_h * 3600 + realistic_m * 60 + realistic_s

            # Calculate difference in minutes (realistic - static)
            diff_seconds = realistic_seconds - static_seconds
            diff_minutes = round(diff_seconds / 60)

            # Determine relationship status
            if diff_minutes > 1:
                relationship = "late"
            elif diff_minutes < -1:
                relationship = "early"
            else:
                relationship = "on time"

            return diff_minutes, relationship

        except Exception:
            return None, "on time"

    def get_future_arrivals_by_stop(self, stop_code: str):
        # Check if this is a metro stop (starts with 'M' followed by digits)
        if stop_code.upper().startswith('M') and len(stop_code) > 1 and stop_code[1:].isdigit():
            return self.get_future_metro_arrivals(stop_code)
    
        # ===== BUS LOGIC BELOW =====
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")
    
        # Calculate time 1 hour ago to catch late buses
        one_hour_ago = (now - timedelta(hours=1)).strftime("%H:%M:%S")
    
        # Load arrivals from 1 hour ago to future (JOIN trips)
        now = datetime.now()

        # If it's before 4:20 AM, use yesterday's date for service lookup
        if now.hour < 4 or (now.hour == 4 and now.minute < 20):
            today = (now - timedelta(days=1)).strftime("%Y%m%d")
        else:
            today = now.strftime("%Y%m%d")
        
        static_arrivals = (
            self.db.query(StopTime, Trip)
            .join(Trip, Trip.trip_id == StopTime.trip_id)
            .join(CalendarDate, CalendarDate.service_id == Trip.service_id)
            .filter(StopTime.stop_id.ilike(f"%{stop_code[-4:]}"))
            .filter(StopTime.arrival_time >= one_hour_ago)
            .filter(CalendarDate.date == today)
            .filter(CalendarDate.exception_type == "1")  # Service is added on this date
            .order_by(StopTime.arrival_time)
            .all()
        )
        

        # Batch query all realistic stop times at once
        trip_ids = [a.trip_id for a, _ in static_arrivals]
        realistic_times = {}
        if trip_ids:
            realistic_results = (
                self.db.query(RealisticStopTime)
                .filter(
                    RealisticStopTime.trip_id.in_(trip_ids),
                    RealisticStopTime.stop_id.ilike(f"%{stop_code}")
                )
                .all()
            )
            # Create lookup dict: (trip_id, stop_id, stop_sequence) -> arrival_time
            for r in realistic_results:
                key = (r.trip_id, r.stop_id, r.stop_sequence)
                realistic_times[key] = r.arrival_time
    
        # Fetch latest live vehicle positions
        latest_positions = fetch_vehicle_positions()
    
        # -------------------------------
        # Update cache with fresh data (only if we got new positions)
        # Keep old entries if vehicle temporarily disappears
        # -------------------------------
        for trip_id, position in latest_positions.items():
            self.vehicle_cache[trip_id] = {
                "position": position,
                "last_seen": now,
            }
        
        # Clean up very old entries (older than 5 minutes)
        stale_threshold = now - timedelta(seconds=300)
        stale_trips = [
            trip_id for trip_id, data in self.vehicle_cache.items()
            if data["last_seen"] < stale_threshold
        ]
        for trip_id in stale_trips:
            del self.vehicle_cache[trip_id]
    
        output = []
    
        for a, trip in static_arrivals:
            cached = self.vehicle_cache.get(a.trip_id)
            has_vehicle = False
            vehicle_position = None
            vehicle_id = None
    
            if cached:
                age = (now - cached["last_seen"]).total_seconds()
                if age <= VEHICLE_POSITION_TTL_SECONDS:
                    has_vehicle = True
                    vehicle_position = cached["position"]
                    vehicle_id = vehicle_position.get("vehicle_id") if vehicle_position else None
    
            # Get latest arrival info from logger (has its own 60s TTL cache)
            latest_arrival = self.get_latest_arrival_from_logger(a.trip_id)

            # Calculate historic latency using pre-fetched data
            historic_latency, historic_relationship = self._calculate_historic_latency_from_cache(
                a.arrival_time, a.trip_id, a.stop_id, a.stop_sequence, realistic_times
            )
    
            certainty = "scheduled"
            include_arrival = False
            delay_seconds = None
    
            if latest_arrival:
                latest_stop_sequence = int(latest_arrival.get("stop_sequence", 0))
                current_stop_sequence = int(a.stop_sequence)
                delay_seconds = latest_arrival.get("delay_seconds")
    
                # Skip if bus has already passed this stop
                if latest_stop_sequence > current_stop_sequence:
                    continue
                
                # Bus hasn't reached this stop yet
                if latest_stop_sequence < current_stop_sequence:
                    certainty = "realtime"
                    include_arrival = True
                    if not has_vehicle:
                        has_vehicle = True
                # Bus is at or near this stop (same stop sequence)
                elif self.seconds_until(a.arrival_time) > 0:
                    certainty = "realtime" if (has_vehicle or latest_arrival) else "scheduled"
                    include_arrival = True
            else:
                # No realtime data - use scheduled time if it's in the future
                if self.seconds_until(a.arrival_time) > 0:
                    certainty = "scheduled" if not has_vehicle else "realtime"
                    include_arrival = True
    
            if not include_arrival:
                continue
            
            route_code = a.trip_id.split("-")[0]
            real_life_route_id = self.routes_service.get_reallife_id(route_code)
    
            expected_arrival_time = a.arrival_time
            if delay_seconds is not None:
                try:
                    h, m, s = map(int, a.arrival_time.split(":"))
                    # Keep GTFS format - don't normalize hours
                    total_seconds = h * 3600 + m * 60 + s + delay_seconds
                    new_h = total_seconds // 3600
                    new_m = (total_seconds % 3600) // 60
                    new_s = total_seconds % 60
                    expected_arrival_time = f"{new_h:02d}:{new_m:02d}:{new_s:02d}"
                except Exception:
                    expected_arrival_time = a.arrival_time
    
            schedule_relationship_status = "on time"
            if delay_seconds is not None:
                if delay_seconds > 60:
                    schedule_relationship_status = "late"
                elif delay_seconds < -60:
                    schedule_relationship_status = "early"
    
            # Filter out arrivals more than 2 hours in the future
            try:
                h, m, s = map(int, expected_arrival_time.split(":"))
                # Convert GTFS time to seconds for comparison
                expected_seconds = h * 3600 + m * 60 + s
                now_h = now.hour
                now_seconds = now_h * 3600 + now.minute * 60 + now.second
                # If expected time has hours >= 24, it's next day
                if h >= 24:
                    now_seconds += 24 * 3600  # Treat current time as if it's "yesterday"
                diff_seconds = expected_seconds - now_seconds
                if diff_seconds > 7200:  # 2 hours in seconds
                    continue
            except Exception:
                pass
            
            arrival_obj = {
                "trip_id": a.trip_id,
                "route_id": route_code,
                "real_life_route_id": real_life_route_id,
                "stop_id": a.stop_id,
                "scheduled_arrival_time": a.arrival_time,
                "expected_arrival_time": expected_arrival_time,
                "departure_time": a.departure_time,
                "stop_sequence": a.stop_sequence,
                "stop_headsign": a.stop_headsign,
                "trip_headsign": trip.trip_headsign,
                "vehicle_position": vehicle_position,
                "vehicle_id": vehicle_id,
                "certainty": certainty,
                "delay_seconds": delay_seconds,
                "schedule_relationship_status": schedule_relationship_status,
                "historic_latency": historic_latency,
                "historic_relationship": historic_relationship,
            }
    
            output.append(arrival_obj)
    
        output.sort(key=lambda x: x["scheduled_arrival_time"])
        output = self._filter_ghost_buses(output)
        return output

    def _filter_ghost_buses(self, arrivals: list) -> list:
        routes = defaultdict(list)
        for arrival in arrivals:
            routes[arrival["route_id"]].append(arrival)

        filtered = []

        for route_id, route_arrivals in routes.items():
            route_arrivals.sort(key=lambda x: x["scheduled_arrival_time"])
            has_any_realtime = any(a["certainty"] == "realtime" for a in route_arrivals)

            if not has_any_realtime:
                filtered.extend(route_arrivals)
                continue

            for i, arrival in enumerate(route_arrivals):
                if arrival["certainty"] == "realtime":
                    filtered.append(arrival)
                else:
                    has_realtime_after = any(
                        route_arrivals[j]["certainty"] == "realtime"
                        and route_arrivals[j]["scheduled_arrival_time"] > arrival["scheduled_arrival_time"]
                        for j in range(i + 1, len(route_arrivals))
                    )
                    if not has_realtime_after:
                        filtered.append(arrival)

        return filtered

    def get_future_metro_arrivals(self, stop_code: str):
        now = datetime.now()

        all_arrivals = (
            self.db.query(StopTime, Trip)
            .join(Trip, Trip.trip_id == StopTime.trip_id)
            .filter(StopTime.stop_id == stop_code)
            .order_by(StopTime.arrival_time)
            .all()
        )

        # Batch query realistic stop times for metro
        trip_ids = [a.trip_id for a, _ in all_arrivals]
        realistic_times = {}
        if trip_ids:
            realistic_results = (
                self.db.query(RealisticStopTime)
                .filter(
                    RealisticStopTime.trip_id.in_(trip_ids),
                    RealisticStopTime.stop_id == stop_code
                )
                .all()
            )
            for r in realistic_results:
                key = (r.trip_id, r.stop_id, r.stop_sequence)
                realistic_times[key] = r.arrival_time

        output = []

        for a, trip in all_arrivals:
            if self.seconds_until(a.arrival_time) <= 0:
                continue

            route_code = a.trip_id.split("-")[0]
            real_life_route_id = self.routes_service.get_reallife_id(route_code)

            # Calculate historic latency using pre-fetched data
            historic_latency, historic_relationship = self._calculate_historic_latency_from_cache(
                a.arrival_time, a.trip_id, a.stop_id, a.stop_sequence, realistic_times
            )

            arrival_obj = {
                "trip_id": a.trip_id,
                "route_id": route_code,
                "real_life_route_id": real_life_route_id,
                "stop_id": a.stop_id,
                "scheduled_arrival_time": a.arrival_time,
                "expected_arrival_time": a.arrival_time,
                "departure_time": a.departure_time,
                "stop_sequence": a.stop_sequence,
                "stop_headsign": a.stop_headsign,
                "trip_headsign": trip.trip_headsign,
                "vehicle_position": None,
                "vehicle_id": None,
                "certainty": "scheduled",
                "delay_seconds": None,
                "schedule_relationship_status": "on time",
                "historic_latency": historic_latency,
                "historic_relationship": historic_relationship,
            }

            output.append(arrival_obj)

        output.sort(key=lambda x: x["scheduled_arrival_time"])
        return output