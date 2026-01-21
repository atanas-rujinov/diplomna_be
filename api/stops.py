from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db.connection import get_db
from db.models import Stop, StopTime
from services.stops_service import StopsService
from datetime import datetime, timedelta

router = APIRouter(prefix="/stops", tags=["stops"])


@router.get("/")
def get_all_stops(db: Session = Depends(get_db)):
    service = StopsService(db)
    stops = service.get_all_stops()
    return [
    {
        "stop_id": stop.stop_id,
        "stop_code": stop.stop_code if stop.stop_code else stop.stop_id[1:],
        "stop_name": stop.stop_name,
        "stop_desc": stop.stop_desc,
        "stop_lat": stop.stop_lat,
        "stop_lon": stop.stop_lon,
        "location_type": stop.location_type,
        "parent_station": stop.parent_station,
        "stop_timezone": stop.stop_timezone,
        "level_id": stop.level_id,
    }
    for stop in stops
    ]

@router.get("/{stop_id}/arrivals")
def get_arrivals(stop_id: str, db: Session = Depends(get_db)):
    service = StopsService(db)
    arrivals = service.get_arrivals_by_stop(stop_id)
    if not arrivals:
        raise HTTPException(status_code=404, detail="No arrivals found for this stop")

    return [
        {
            "trip_id": a.trip_id,
            "arrival_time": a.arrival_time,
            "departure_time": a.departure_time,
            "stop_sequence": a.stop_sequence,
            "stop_headsign": a.stop_headsign,
            "pickup_type": a.pickup_type,
            "drop_off_type": a.drop_off_type,
            "shape_dist_traveled": a.shape_dist_traveled,
            "continuous_pickup": a.continuous_pickup,
            "continuous_drop_off": a.continuous_drop_off,
            "timepoint": a.timepoint,
        }
        for a in arrivals
    ]

@router.get("/{stop_code}/future-arrivals")
def get_future_arrivals(stop_code: str, db: Session = Depends(get_db)):
    service = StopsService(db)
    
    # Debug logging to file
    now = datetime.now()
    current_time = now.strftime("%H:%M:%S")
    
    debug_msg = f"\n=== Request for stop: {stop_code} ===\n"
    debug_msg += f"Current time: {current_time}\n"
    
    # Check if this is a metro stop
    is_metro = stop_code.upper().startswith('M') and len(stop_code) > 1 and stop_code[1:].isdigit()
    debug_msg += f"Is metro stop: {is_metro}\n"
    
    # Check total StopTimes
    total_stop_times = db.query(StopTime).filter(StopTime.stop_id == stop_code).count()
    debug_msg += f"Total StopTimes in DB: {total_stop_times}\n"
    
    # Show sample times
    samples = db.query(StopTime).filter(StopTime.stop_id == stop_code).limit(5).all()
    debug_msg += f"Sample arrival times: {[s.arrival_time for s in samples]}\n"
    
    with open("metro_debug.log", "a") as f:
        f.write(debug_msg)
    
    arrivals = service.get_future_arrivals_by_stop(stop_code)
    
    debug_msg = f"Future arrivals returned: {len(arrivals)}\n"
    if arrivals:
        debug_msg += f"First 3 arrivals:\n"
        for arr in arrivals[:3]:
            debug_msg += f"  - {arr.get('real_life_route_id', 'N/A')} at {arr.get('scheduled_arrival_time', 'N/A')}\n"
    debug_msg += f"=== END REQUEST ===\n"
    
    with open("metro_debug.log", "a") as f:
        f.write(debug_msg)

    if not arrivals:
        return []

    return arrivals