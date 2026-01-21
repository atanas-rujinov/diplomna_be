from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any
from sqlalchemy.orm import Session
from db.models import StopTime, Stop
from services.vehicle_positions import fetch_vehicle_positions
from services.routes_service import RoutesService

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

    def get_future_arrivals_by_stop(self, stop_code: str):
        now = datetime.now()
        current_time = now.strftime("%H:%M:%S")

        # Load future static arrivals
        static_arrivals = (
            self.db.query(StopTime)
            .filter(StopTime.stop_id.ilike(f"%{stop_code}"))
            .filter(StopTime.arrival_time >= current_time)
            .order_by(StopTime.arrival_time)
            .all()
        )

        # Fetch latest live vehicle positions
        latest_positions = fetch_vehicle_positions()

        # -------------------------------
        # Update cache with fresh data
        # -------------------------------
        for trip_id, position in latest_positions.items():
            self.vehicle_cache[trip_id] = {
                "position": position,
                "last_seen": now,
            }

        output = []

        for a in static_arrivals:
            seconds_to_arrival = self.seconds_until(a.arrival_time)

            cached = self.vehicle_cache.get(a.trip_id)
            has_vehicle = False
            vehicle_position = None
            vehicle_id = None

            if cached:
                age = (now - cached["last_seen"]).total_seconds()
                if age <= VEHICLE_POSITION_TTL_SECONDS:
                    has_vehicle = True
                    vehicle_position = cached["position"]
                    # Extract vehicle_id from the position data
                    vehicle_id = vehicle_position.get("vehicle_id") if vehicle_position else None

            # Skip likely ghost trips (UNCHANGED LOGIC)
            if seconds_to_arrival <= REALTIME_GRACE_MINUTES * 60 and not has_vehicle:
                continue

            route_code = a.trip_id.split("-")[0]
            real_life_route_id = self.routes_service.get_reallife_id(route_code)

            arrival_obj = {
                "trip_id": a.trip_id,
                "route_id": route_code,
                "real_life_route_id": real_life_route_id,
                "stop_id": a.stop_id,
                "scheduled_arrival_time": a.arrival_time,
                "departure_time": a.departure_time,
                "stop_sequence": a.stop_sequence,
                "stop_headsign": a.stop_headsign,
                "vehicle_position": vehicle_position,
                "vehicle_id": vehicle_id,
                "certainty": "realtime" if has_vehicle else "scheduled",
            }

            output.append(arrival_obj)

        

        # ----- Route-level ghost trip filtering -----
        output = self.filter_ghosts_by_route(output)

        output.sort(key=lambda x: x["scheduled_arrival_time"])
        return output

    def filter_ghosts_by_route(self, arrivals: list) -> list:
        routes = defaultdict(list)

        for a in arrivals:
            route_id = a["trip_id"].split("-")[0]
            routes[route_id].append(a)

        filtered = []

        for route_arrivals in routes.values():
            route_arrivals.sort(key=lambda x: x["scheduled_arrival_time"])
            has_realtime_seen = False

            for a in route_arrivals:
                if a["vehicle_position"]:
                    has_realtime_seen = True
                    filtered.append(a)
                else:
                    if not has_realtime_seen:
                        filtered.append(a)

        return filtered