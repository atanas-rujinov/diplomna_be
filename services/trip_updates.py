import requests
from google.transit import gtfs_realtime_pb2
import json

TRIP_UPDATES_URL = "https://gtfs.sofiatraffic.bg/api/v1/trip-updates"

def fetch_trip_updates() -> dict:
    """
    Returns:
        {
            trip_id: {
                stop_id: {
                    "arrival": { "time": arrival_ts, "delay": delay },
                    "departure": { "time": depart_ts, "delay": delay },
                },
                ...
            },
            ...
        }
    """
    response = requests.get(TRIP_UPDATES_URL, timeout=10)
    response.raise_for_status()

    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(response.content)

    trip_updates = {}

    for entity in feed.entity:
        if not entity.HasField("trip_update") or not entity.trip_update.trip.trip_id:
            continue

        update = entity.trip_update
        t_id = update.trip.trip_id

        stops = {}

        for stu in update.stop_time_update:
            if not stu.stop_id:
                continue

            stop_key = stu.stop_id

            arrival = {}
            departure = {}
            if stu.HasField("arrival"):
                arrival = {
                    "time": stu.arrival.time,
                    "delay": stu.arrival.delay if stu.arrival.HasField("delay") else None,
                }
            if stu.HasField("departure"):
                departure = {
                    "time": stu.departure.time,
                    "delay": stu.departure.delay if stu.departure.HasField("delay") else None,
                }

            stops[stop_key] = {
                "arrival": arrival,
                "departure": departure,
                "schedule_relationship": stu.schedule_relationship
            }

        trip_updates[t_id] = stops

    #print trip updates ina file
    with open("trip_updates_debug.json", "w") as f:
        json.dump(trip_updates, f, indent=2)

    return trip_updates
