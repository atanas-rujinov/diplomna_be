import requests
from google.transit import gtfs_realtime_pb2

VEHICLE_POSITIONS_URL = "https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions"


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