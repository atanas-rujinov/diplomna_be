from sqlalchemy.orm import Session
from db.models import Route

ROUTE_TYPE_PREFIX = {
    "0": "TM",
    "11": "TB",
    "1": "M",
    "3": "A",
}

class RoutesService:
    def __init__(self, db: Session):
        self.db = db

    def get_reallife_id(self, route_id: str) -> str | None:
        route = (
            self.db.query(Route)
            .filter(Route.route_id == route_id)
            .first()
        )

        if not route or not route.route_short_name:
            return None

        prefix = ROUTE_TYPE_PREFIX.get(str(route.route_type), "")
        return f"{prefix}{route.route_short_name}"
