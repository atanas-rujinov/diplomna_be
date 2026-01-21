from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from db.connection import get_db
from services.routes_service import RoutesService

router = APIRouter(prefix="/routes", tags=["routes"])

@router.get("/{route_id}/reallife-id")
def get_reallife_route_id(route_id: str, db: Session = Depends(get_db)):
    service = RoutesService(db)
    real_id = service.get_reallife_id(route_id)

    if real_id is None:
        raise HTTPException(status_code=404, detail="Route not found")

    return {
        "route_id": route_id,
        "reallife_id": real_id
    }
