from sqlalchemy import Column, String, Integer
from .connection import Base

class Stop(Base):
    __tablename__ = "stops"

    stop_id = Column(String, primary_key=True, index=True)
    stop_code = Column(String, nullable=True)
    stop_name = Column(String, nullable=False, index=True)
    stop_desc = Column(String, nullable=True)
    stop_lat = Column(String, nullable=False)
    stop_lon = Column(String, nullable=False)
    location_type = Column(String, nullable=True)
    parent_station = Column(String, nullable=True)
    stop_timezone = Column(String, nullable=True)
    level_id = Column(String, nullable=True)
    
class StopTime(Base):
    __tablename__ = "stop_times"

    trip_id = Column(String, primary_key=True, index=True)
    stop_sequence = Column(Integer, primary_key=True, nullable=False)  # ADD THIS
    arrival_time = Column(String, nullable=False)
    departure_time = Column(String, nullable=False)
    stop_id = Column(String, index=True, nullable=False)
    stop_headsign = Column(String, nullable=True)
    pickup_type = Column(String, nullable=True)
    drop_off_type = Column(String, nullable=True)
    shape_dist_traveled = Column(String, nullable=True)
    continuous_pickup = Column(String, nullable=True)
    continuous_drop_off = Column(String, nullable=True)
    timepoint = Column(String, nullable=True)    
    
class RealisticStopTime(Base):
    __tablename__ = "realistic_stop_times"

    trip_id = Column(String, primary_key=True, index=True)
    stop_sequence = Column(Integer, primary_key=True, nullable=False)  # ADD THIS
    arrival_time = Column(String, nullable=False)
    departure_time = Column(String, nullable=False)
    stop_id = Column(String, index=True, nullable=False)
    stop_headsign = Column(String, nullable=True)
    pickup_type = Column(String, nullable=True)
    drop_off_type = Column(String, nullable=True)
    shape_dist_traveled = Column(String, nullable=True)
    continuous_pickup = Column(String, nullable=True)
    continuous_drop_off = Column(String, nullable=True)
    timepoint = Column(String, nullable=True)

class Route(Base):
    __tablename__ = "routes"

    route_id = Column(String, primary_key=True, index=True)
    agency_id = Column(String, nullable=True)
    route_short_name = Column(String, nullable=True)  # <-- A84
    route_long_name = Column(String, nullable=True)
    route_desc = Column(String, nullable=True)
    route_type = Column(String, nullable=True)
    route_url = Column(String, nullable=True)
    route_color = Column(String, nullable=True)
    route_text_color = Column(String, nullable=True)
    route_sort_order = Column(String, nullable=True)
    continuous_pickup = Column(String, nullable=True)
    continuous_drop_off = Column(String, nullable=True)

class Trip(Base):
    __tablename__ = "trips"

    trip_id = Column(String, primary_key=True, index=True)
    route_id = Column(String, index=True, nullable=False)
    service_id = Column(String, nullable=True)
    trip_headsign = Column(String, nullable=True)
    direction_id = Column(String, nullable=True)

class TimetableEntry(Base):
    __tablename__ = "timetable"

    id = Column(Integer, primary_key=True, autoincrement=True)
    trip_id = Column(String, index=True, nullable=False)
    route_id = Column(String, index=True, nullable=False)
    stop_id = Column(String, index=True, nullable=False)
    stop_sequence = Column(Integer, nullable=False)
    arrival_time = Column(String, nullable=True)
    departure_time = Column(String, nullable=True)

class CalendarDate(Base):
    __tablename__ = 'calendar_dates'
    
    service_id = Column(String, primary_key=True)
    date = Column(String, primary_key=True)  # Format: YYYYMMDD (e.g., "20260102")
    exception_type = Column(String)