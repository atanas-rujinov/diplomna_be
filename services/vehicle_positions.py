import requests
import logging
from google.transit import gtfs_realtime_pb2

VEHICLE_POSITIONS_URL = "https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions"

# Setup logging
logging.basicConfig(
    filename="vehicle_positions.log",
    level=logging.DEBUG,
    format="%(asctime)s | %(levelname)s | %(message)s",
)


def fetch_vehicle_positions() -> dict:
    """
    Returns:
        {
            trip_id: {
                lat, lon, bearing, speed, vehicle_id
            }
        }
    """
    response = requests.get(VEHICLE_POSITIONS_URL, timeout=10)
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    # Log the raw decoded protobuf for inspection
    logging.debug("Decoded vehicle positions feed:")
    for entity in feed.entity:
        if entity.HasField("vehicle"):
            vehicle = entity.vehicle
            trip_dict = {f.name: getattr(vehicle.trip, f.name) for f, _ in vehicle.trip.ListFields()}
            vehicle_info = {
                "trip": trip_dict,
                "vehicle_id": vehicle.vehicle.id if vehicle.HasField("vehicle") else None,
                "position": {
                    "lat": vehicle.position.latitude,
                    "lon": vehicle.position.longitude,
                    "bearing": vehicle.position.bearing if vehicle.position.HasField("bearing") else None,
                    "speed": vehicle.position.speed if vehicle.position.HasField("speed") else None,
                } if vehicle.HasField("position") else None,
            }
            logging.debug("Vehicle entity: %s", vehicle_info)
    

    positions = {}

    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue

        vehicle = entity.vehicle
        # Only store positions that have a trip_id and position
        if not vehicle.trip.trip_id or not vehicle.position:
            continue

        positions[vehicle.trip.trip_id] = {
            "lat": vehicle.position.latitude,
            "lon": vehicle.position.longitude,
            "speed": vehicle.position.speed if vehicle.position.HasField("speed") else None,
            "vehicle_id": vehicle.vehicle.id if vehicle.HasField("vehicle") else None,
        }

    return positions