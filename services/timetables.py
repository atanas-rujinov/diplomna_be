from sqlalchemy.orm import Session
from db.models import Stop, RealisticStopTime, Route, Trip, TimetableEntry, CalendarDate
from db.connection import engine
from collections import defaultdict
from typing import Dict, List
from datetime import datetime, timedelta

class Timetables:
    """
    Loads GTFS timetable data into memory for fast RAPTOR routing.
    Uses realistic_stop_times instead of stop_times.
    """

    def __init__(self, db: Session):
        self.db = db
        # stops: stop_id -> {lat, lon, stop_name}
        self.stops: Dict[str, Dict] = {}
        # trips: trip_id -> Trip model
        self.trips: Dict[str, Trip] = {}
        # stop_times_by_trip: trip_id -> [RealisticStopTime models ordered by stop_sequence]
        self.stop_times_by_trip: Dict[str, List[RealisticStopTime]] = defaultdict(list)
        # routes: route_id -> Route model
        self.routes: Dict[str, Route] = {}
        # stop_routes: stop_id -> list of route_ids passing through
        self.stop_routes: Dict[str, List[str]] = defaultdict(list)

    def load(self):
        # Load stops
        self._load_stops()
        
        # Load routes
        self._load_routes()
        
        # Load trips
        self._load_trips()
        
        # Load stop_times (realistic)
        self._load_stop_times()

        print(f"Loaded {len(self.stops)} stops, {len(self.routes)} routes, {len(self.trips)} trips into timetable.")
        
        #TimetableEntry.__table__.create(bind=engine, checkfirst=True)
        #self.save_to_db()

    def _load_stops(self):
        stops_query = self.db.query(Stop).all()
        for stop in stops_query:
            self.stops[stop.stop_id] = {
                "stop_name": stop.stop_name,
                "lat": float(stop.stop_lat),
                "lon": float(stop.stop_lon)
            }

    def _load_routes(self):
        routes_query = self.db.query(Route).all()
        for route in routes_query:
            self.routes[route.route_id] = route

    def _load_trips(self):
        trips_query = self.db.query(Trip).all()
        for trip in trips_query:
            self.trips[trip.trip_id] = trip

    def _load_stop_times(self):
        # Changed to query RealisticStopTime instead of StopTime
        now = datetime.now()

        # If it's before 4:20 AM, use yesterday's date for service lookup
        if now.hour < 4 or (now.hour == 4 and now.minute < 20):
            service_date = (now - timedelta(days=1)).strftime("%Y%m%d")
        else:
            service_date = now.strftime("%Y%m%d")

        stop_times_query = (
            self.db.query(RealisticStopTime)
            .join(Trip, Trip.trip_id == RealisticStopTime.trip_id)
            .join(CalendarDate, CalendarDate.service_id == Trip.service_id)
            .filter(CalendarDate.date == service_date)
            .filter(CalendarDate.exception_type == "1")
            .order_by(
                RealisticStopTime.trip_id, 
                RealisticStopTime.stop_sequence
            )
            .all()
        )


        for st in stop_times_query:
            self.stop_times_by_trip[st.trip_id].append(st)
            
            # Also track which routes pass through each stop
            if st.trip_id in self.trips:
                route_id = self.trips[st.trip_id].route_id
                if route_id not in self.stop_routes[st.stop_id]:
                    self.stop_routes[st.stop_id].append(route_id)
                    
    def save_to_db(self):
        """
        Persist in-memory timetable into the 'timetable' table.
        """
        

        entries = []
        for trip_id, stop_times in self.stop_times_by_trip.items():
            route_id = self.trips[trip_id].route_id if trip_id in self.trips else None
            for st in stop_times:
                entry = TimetableEntry(
                    trip_id=trip_id,
                    route_id=route_id,
                    stop_id=st.stop_id,
                    stop_sequence=st.stop_sequence,
                    arrival_time=st.arrival_time,
                    departure_time=st.departure_time
                )
                entries.append(entry)

        # Bulk insert
        self.db.bulk_save_objects(entries)
        self.db.commit()
        print(f"Saved {len(entries)} timetable entries to the database.")