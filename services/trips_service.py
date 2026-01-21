from sqlalchemy.orm import Session
from db.models import StopTime
from services.vehicle_positions import fetch_vehicle_positions

class TripsService:
    def __init__(self, db: Session):
        self.db = db

    def get_trip(self, trip_id: str):
        """
        Return trip info with all stoptimes and current vehicle position if available.
        """

        # Get base trip ID from static GTFS (ignore suffixes in RT trip IDs)
        base_trip_id = trip_id

        # Fetch all stoptimes that match this base_trip_id
        stoptimes = (
            self.db.query(StopTime)
            .filter(StopTime.trip_id.like(f"{base_trip_id}%"))
            .order_by(StopTime.stop_sequence)
            .all()
        )

        print(f"DEBUG: base_trip_id = {base_trip_id}")
        print(f"DEBUG: Found {len(stoptimes)} stoptimes")
        for st in stoptimes:
            print(f"  - {st.stop_id} at seq {st.stop_sequence}")

        if not stoptimes:
            return None

        # Fetch realtime vehicle positions
        vehicle_positions = fetch_vehicle_positions()
        vehicle_position = vehicle_positions.get(trip_id)  # may be None

        # Build stops list
        stops = [
            {
                "stop_id": st.stop_id,
                "arrival_time": st.arrival_time,
                "departure_time": st.departure_time,
                "stop_sequence": st.stop_sequence,
                "stop_headsign": st.stop_headsign,
            }
            for st in stoptimes
        ]

        return {
            "trip_id": trip_id,
            "vehicle_position": vehicle_position,  # may be None
            "stops": stops,
        }
