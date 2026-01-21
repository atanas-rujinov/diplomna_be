# services/navigation_service.py
from datetime import datetime
from services.raptor_service import RaptorService
from services.timetables import Timetables
from services.routes_service import RoutesService
from math import radians, sin, cos, sqrt, atan2

MAX_WALKING_DISTANCE_M = 500

class NavigationService:
    def __init__(self, db):
        """
        db: SQLAlchemy session
        """
        self.db = db
        
        # Load timetable into memory
        self.timetable = Timetables(db)
        self.timetable.load()

        # Initialize RAPTOR service
        self.raptor = RaptorService(self.timetable)

    @staticmethod
    def haversine(lat1, lon1, lat2, lon2):
        """Distance in meters between two points"""
        R = 6371e3
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c

    @staticmethod
    def parse_time_to_seconds(time_str):
        """HH:MM:SS → seconds"""
        h, m, s = map(int, time_str.split(":"))
        return h*3600 + m*60 + s

    @staticmethod
    def seconds_to_time(seconds):
        h = seconds // 3600
        m = (seconds % 3600) // 60
        s = seconds % 60
        return f"{h:02d}:{m:02d}:{s:02d}"

    def navigate(self, origin_lat, origin_lon, dest_lat, dest_lon, departure_time=None, debug=False):
        """
        Returns top routes with legs from origin to destination
        
        Args:
            origin_lat: Origin latitude
            origin_lon: Origin longitude
            dest_lat: Destination latitude
            dest_lon: Destination longitude
            departure_time: Departure time as HH:MM:SS string (optional)
            debug: If True, returns debug logs showing algorithm execution
        """
        if not departure_time:
            now = datetime.now()
            departure_seconds = now.hour*3600 + now.minute*60 + now.second
            departure_time = now.strftime("%H:%M:%S")
        else:
            departure_seconds = self.parse_time_to_seconds(departure_time)

        # Run RAPTOR with debug flag
        raptor_result = self.raptor.run(
            origin_lat, origin_lon, dest_lat, dest_lon, 
            departure_seconds, debug=debug
        )
        
        # Handle debug mode - raptor returns dict with routes and logs
        if debug:
            results = raptor_result.get("routes", [])
            debug_logs = raptor_result.get("debug_logs", [])
        else:
            results = raptor_result
            debug_logs = None

        # Format for frontend
        formatted_routes = []
        
        routes_service = RoutesService(self.db)
        
        for r in results[:5]:  # top 5 routes
            formatted_legs = []
            for leg in r["legs"]:
                # Walking leg
                if leg["type"] == "walk":
                    formatted_legs.append({
                        "type": "walk",
                        "from": leg["from"],
                        "to": leg["to"],
                        "distance_m": round(leg["distance_m"], 1),
                        "duration_seconds": leg["duration_seconds"]
                    })
                # Transit leg
                elif leg["type"] == "transit":
                    formatted_legs.append({
                        "type": "transit",
                        "route_id": routes_service.get_reallife_id(leg["route_id"]),
                        "trip_id": leg["trip_id"],
                        "from_stop_id": leg["from_stop_id"],
                        "to_stop_id": leg.get("to_stop_id"),
                        "from_stop_name": leg["from_stop_name"],
                        "to_stop_name": leg["to_stop_name"],
                        "departure_time": leg["departure_time"],
                        "arrival_time": leg.get("arrival_time")
                    })

            formatted_routes.append({
                "total_time_seconds": r["total_time"],
                "total_time_minutes": round(r["total_time"]/60, 1),
                "legs": formatted_legs
            })

        straight_distance = self.haversine(origin_lat, origin_lon, dest_lat, dest_lon)

        response = {
            "origin": {"lat": origin_lat, "lon": origin_lon},
            "destination": {"lat": dest_lat, "lon": dest_lon},
            "straight_distance_m": round(straight_distance, 1),
            "departure_time": departure_time,
            "routes": formatted_routes,
            "message": "No routes found. Try increasing walking distance or adjusting departure time." if not formatted_routes else None
        }
        
        # Add debug logs if debug mode is enabled
        if debug and debug_logs:
            response["debug_logs"] = debug_logs
        
        return response