from fastapi import APIRouter, HTTPException
from services.arrival_logger import arrival_logger

router = APIRouter()


@router.get("/vehicles/{trip_id}/latest-arrival")
async def get_latest_arrival(trip_id: str):
    """Get the latest arrival information for a specific trip"""
    arrival_info = arrival_logger.get_latest_arrival(trip_id)
    
    if not arrival_info:
        raise HTTPException(
            status_code=404,
            detail={
                'error': 'Vehicle not found or no arrivals recorded',
                'trip_id': trip_id
            }
        )
    
    return {
        'vehicle_id': trip_id,  # Using trip_id as vehicle_id
        'trip_id': arrival_info['trip_id'],
        'route_id': arrival_info.get('route_id'),
        'stop_id': arrival_info['stop_id'],
        'stop_name': arrival_info['stop_name'],
        'stop_sequence': arrival_info.get('stop_sequence'),
        'timestamp': arrival_info['timestamp'],
        'delay_seconds': arrival_info['delay_seconds']
    }