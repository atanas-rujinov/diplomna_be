from fastapi import APIRouter, HTTPException, Depends, Query, Request
from sqlalchemy.orm import Session
from db.session import get_db
from typing import Optional

router = APIRouter()

@router.get("/navigate")
async def navigate(
    request: Request,
    origin_lat: float = Query(..., description="Origin latitude"),
    origin_lon: float = Query(..., description="Origin longitude"),
    dest_lat: float = Query(..., description="Destination latitude"),
    dest_lon: float = Query(..., description="Destination longitude"),
    departure_time: Optional[str] = Query(None, description="Departure time in HH:MM:SS format"),
    debug: bool = Query(False, description="Enable debug logs"),
    db: Session = Depends(get_db)
):
    """
    Find routes from origin to destination using the pre-loaded NavigationService.
    """
    
    # 1. Coordinate Validation
    if not (-90 <= origin_lat <= 90) or not (-180 <= origin_lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid origin coordinates")
    if not (-90 <= dest_lat <= 90) or not (-180 <= dest_lon <= 180):
        raise HTTPException(status_code=400, detail="Invalid destination coordinates")
    
    # 2. Time Parsing
    if departure_time:
        try:
            parts = departure_time.split(':')
            if len(parts) != 3: raise ValueError()
            h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
        except:
            raise HTTPException(status_code=400, detail="Invalid departure_time format. Use HH:MM:SS")

    try:
        # Use the NavigationService that was loaded at startup
        nav_service = request.app.state.navigation_service
        
        # Call navigate() which returns the fully formatted response
        result = nav_service.navigate(
            origin_lat, 
            origin_lon, 
            dest_lat, 
            dest_lon, 
            departure_time=departure_time,
            debug=debug
        )
        
        return result
    
    except Exception as e:
        print(f"Navigation Error: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Navigation error: {str(e)}")


@router.get("/nearby-stops")
async def get_nearby_stops(
    request: Request,
    lat: float = Query(..., description="Latitude"),
    lon: float = Query(..., description="Longitude"),
    max_distance: int = Query(500, description="Maximum distance in meters"),
    db: Session = Depends(get_db)
):
    """Find stops near a location"""
    raptor_service = request.app.state.raptor_service
    stops = raptor_service.find_nearby_stops(lat, lon, max_distance)
    
    return {
        'location': {'lat': lat, 'lon': lon},
        'max_distance_m': max_distance,
        'stops': stops
    }


@router.get("/debug-metro")
async def debug_metro(
    request: Request,
    origin_lat: float = Query(..., description="Origin latitude"),
    origin_lon: float = Query(..., description="Origin longitude"),
    db: Session = Depends(get_db)
):
    """Debug metro accessibility"""
    raptor_service = request.app.state.raptor_service
    
    nearby_stops = raptor_service.find_nearby_stops(origin_lat, origin_lon, 500)
    metro_stops = [s for s in nearby_stops if s['stop_id'].startswith('M')]
    
    route_prefixes = {}
    trips_with_m312 = []
    
    timetable = raptor_service.timetable
    trips_by_route = timetable.stop_times_by_trip
    
    for trip_id, stop_times in trips_by_route.items():
        trip = timetable.trips.get(trip_id)
        if trip and hasattr(trip, 'route_id'):
            route_id = trip.route_id
            prefix = route_id[:2] if len(route_id) >= 2 else route_id
            route_prefixes[prefix] = route_prefixes.get(prefix, 0) + 1
            
            if any(st.stop_id == 'M312' for st in stop_times):
                trips_with_m312.append({
                    'trip_id': trip_id,
                    'route_id': route_id,
                    'stop_count': len(stop_times)
                })
    
    return {
        'nearby_stops_total': len(nearby_stops),
        'metro_stops_found': len(metro_stops),
        'total_trips_in_system': len(trips_by_route),
        'route_id_prefixes': dict(sorted(route_prefixes.items(), key=lambda x: x[1], reverse=True)[:10]),
        'trips_servicing_m312': len(trips_with_m312)
    }