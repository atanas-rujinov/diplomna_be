from fastapi import FastAPI
from contextlib import asynccontextmanager
import asyncio
from startup import run_startup
from api.stops import router as stops_router
from api.routes import router as routes_router
from api.trips import router as trips_router
from api.arrivals import router as arrivals_router
from api.navigation import router as navigation_router
from services.arrival_logger import arrival_logger
from services.navigation_service import NavigationService
from dotenv import load_dotenv

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Run startup and get the initialized objects
    timetable, raptor_service = run_startup()
    
    # 2. Store them in app.state so they live as long as the server
    app.state.timetable = timetable
    app.state.raptor_service = raptor_service
    
    # 3. Create a NavigationService once with the loaded timetable
    # This way we don't recreate it on every request
    from sqlalchemy.orm import sessionmaker
    from db.connection import engine
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()
    app.state.navigation_service = NavigationService(db_session)
    
    # Start background tasks
    logger_task = asyncio.create_task(arrival_logger.poll_vehicles())
    
    yield
    
    logger_task.cancel()
    db_session.close()

app = FastAPI(title="Sofia Transport API", lifespan=lifespan)

# Include routers
app.include_router(stops_router)
app.include_router(routes_router)
app.include_router(trips_router)
app.include_router(arrivals_router)
app.include_router(navigation_router)