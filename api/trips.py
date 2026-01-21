from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db.session import get_db
from services.trips_service import TripsService

router = APIRouter(prefix="/trips", tags=["trips"])

@router.get("/{trip_id}")
def get_trip(trip_id: str, db: Session = Depends(get_db)):
    service = TripsService(db)
    trip = service.get_trip(trip_id)
    if not trip:
        raise HTTPException(status_code=404, detail="Trip not found")
    return trip
