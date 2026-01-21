import asyncio
import csv
import logging
import os
from datetime import datetime, timedelta, time as dtime
from math import radians, sin, cos, sqrt, atan2
from typing import Dict, Set, Optional
from threading import Lock
import requests
from google.transit import gtfs_realtime_pb2
from google.protobuf.message import DecodeError

try:
    from zoneinfo import ZoneInfo
except ImportError:
    raise RuntimeError("This script requires Python 3.9+ (zoneinfo).")

# Configuration
TZ = ZoneInfo("Europe/Sofia")
POLL_INTERVAL = 5  # seconds
REQUEST_TIMEOUT = 10  # seconds
DISTANCE_THRESHOLD_M = 30  # meters
GTFS_RT_URL = "https://gtfs.sofiatraffic.bg/api/v1/vehicle-positions"
GTFS_DIR = "gtfs_static"
LOG_FILE = "arrival_log.csv"
CACHE_TTL_SECONDS = 60  # Keep cached arrivals for 60 seconds after last seen

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class ArrivalLogger:
    """Background service that logs vehicle arrivals at stops"""
    
    def __init__(self):
        self.vehicle_arrivals: Dict[str, Set[str]] = {}  # {trip_id: set of stop_ids}
        self.vehicle_latest_arrival: Dict[str, dict] = {}  # {trip_id: arrival_info with last_seen}
        self.data_lock = Lock()
        
        # Load GTFS static data
        self.stops = self._load_stops()
        self.trip_stops = self._load_stop_times()
        self.trip_services = self._load_trips()
        self.service_days = self._load_calendar_dates()
        
        # Initialize CSV file with header if it doesn't exist
        self._init_csv_log()
        
        logger.info(f"Loaded {len(self.stops)} stops, {len(self.trip_stops)} trips")
        logger.info(f"Cache TTL: {CACHE_TTL_SECONDS} seconds")
    
    def _init_csv_log(self):
        """Initialize CSV log file with header if it doesn't exist"""
        try:
            if not os.path.exists(LOG_FILE):
                with open(LOG_FILE, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        'timestamp', 'vehicle_id', 'trip_id', 'route_id', 'stop_id', 'stop_name',
                        'scheduled_arrival', 'actual_arrival', 'delay_seconds', 'day_of_week', 'hour'
                    ])
        except Exception as e:
            logger.error(f"Error initializing CSV log: {e}")
    
    def _load_stops(self) -> dict:
        """Load stops.txt"""
        stops = {}
        try:
            with open(f"{GTFS_DIR}/stops.txt", 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    stop_id = row.get('stop_id')
                    if stop_id and row.get('stop_lat') and row.get('stop_lon'):
                        stops[stop_id] = {
                            'name': row.get('stop_name', ''),
                            'lat': float(row['stop_lat']),
                            'lon': float(row['stop_lon'])
                        }
        except Exception as e:
            logger.error(f"Error loading stops: {e}")
        return stops
    
    def _load_stop_times(self) -> dict:
        """Load stop_times.txt"""
        trip_stops = {}
        try:
            with open(f"{GTFS_DIR}/stop_times.txt", 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trip_id = row.get('trip_id')
                    stop_id = row.get('stop_id')
                    if trip_id and stop_id:
                        trip_stops.setdefault(trip_id, []).append({
                            'stop_id': stop_id,
                            'arrival_time': row.get('arrival_time', ''),
                            'stop_sequence': row.get('stop_sequence', '')
                        })
        except Exception as e:
            logger.error(f"Error loading stop_times: {e}")
        return trip_stops
    
    def _load_trips(self) -> dict:
        """Load trips.txt"""
        trip_services = {}
        try:
            with open(f"{GTFS_DIR}/trips.txt", 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    trip_id = row.get('trip_id')
                    if trip_id:
                        trip_services[trip_id] = row.get('service_id')
        except Exception as e:
            logger.error(f"Error loading trips: {e}")
        return trip_services
    
    def _load_calendar_dates(self) -> dict:
        """Load calendar_dates.txt"""
        service_days = {}
        try:
            with open(f"{GTFS_DIR}/calendar_dates.txt", 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    date_str = row.get('date')
                    service_id = row.get('service_id')
                    exception_type = row.get('exception_type')
                    if date_str and service_id and exception_type == "1":
                        date = datetime.strptime(date_str, "%Y%m%d").date()
                        service_days.setdefault(service_id, set()).add(date)
        except Exception as e:
            logger.error(f"Error loading calendar_dates: {e}")
        return service_days
    
    @staticmethod
    def haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
        """Calculate distance between two points in meters"""
        R = 6371e3  # Earth radius in meters
        phi1, phi2 = radians(lat1), radians(lat2)
        dphi = radians(lat2 - lat1)
        dlambda = radians(lon2 - lon1)
        a = sin(dphi/2)**2 + cos(phi1)*cos(phi2)*sin(dlambda/2)**2
        c = 2 * atan2(sqrt(a), sqrt(1 - a))
        return R * c
    
    def _parse_gtfs_time(self, time_str: str, trip_id: str, now: datetime) -> Optional[datetime]:
        """Parse GTFS time string (can have hours > 23) to datetime"""
        try:
            if not time_str:
                return None
            h, m, s = map(int, time_str.split(":"))
            
            # Get service day for this trip
            service_id = self.trip_services.get(trip_id)
            today = now.date()
            
            if service_id and service_id in self.service_days and today in self.service_days[service_id]:
                base = datetime.combine(today, dtime.min).replace(tzinfo=TZ)
            else:
                base = now.replace(hour=0, minute=0, second=0, microsecond=0)
            
            extra_days, hours_mod = divmod(h, 24)
            candidate_dt = base + timedelta(days=extra_days, hours=hours_mod, minutes=m, seconds=s)
            
            # Find closest candidate (yesterday, today, or tomorrow)
            candidates = [
                candidate_dt - timedelta(days=1),
                candidate_dt,
                candidate_dt + timedelta(days=1)
            ]
            return min(candidates, key=lambda dt: abs((dt - now).total_seconds()))
        except Exception as e:
            logger.error(f"Error parsing GTFS time {time_str}: {e}")
            return None
    
    def get_latest_arrival(self, trip_id: str) -> Optional[dict]:
        """Get latest arrival info for a trip_id (with cache TTL check)"""
        with self.data_lock:
            arrival = self.vehicle_latest_arrival.get(trip_id)
            
            if not arrival:
                return None
            
            # Check if cache has expired
            last_seen = arrival.get('last_seen')
            if last_seen:
                now = datetime.now(tz=TZ)
                age_seconds = (now - last_seen).total_seconds()
                
                if age_seconds > CACHE_TTL_SECONDS:
                    # Cache expired, remove it
                    logger.debug(f"Cache expired for trip {trip_id} (age: {age_seconds:.1f}s)")
                    del self.vehicle_latest_arrival[trip_id]
                    # Also clean up arrivals set
                    if trip_id in self.vehicle_arrivals:
                        del self.vehicle_arrivals[trip_id]
                    return None
            
            # Return a copy without the internal last_seen field
            return {k: v for k, v in arrival.items() if k != 'last_seen'}
    
    def _log_arrival_to_csv(self, now: datetime, trip_id: str, route_id: str, 
                            stop_id: str, stop_name: str, scheduled_dt: Optional[datetime], 
                            delay_sec: Optional[int]):
        """Log arrival to CSV file"""
        try:
            with open(LOG_FILE, 'a', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                writer.writerow([
                    now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    trip_id,  # Using trip_id as vehicle_id
                    trip_id,
                    route_id or '',
                    stop_id,
                    stop_name,
                    scheduled_dt.strftime("%Y-%m-%d %H:%M:%S %Z") if scheduled_dt else '',
                    now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                    delay_sec if delay_sec is not None else '',
                    now.strftime("%A"),
                    now.hour
                ])
        except Exception as e:
            logger.error(f"Error logging arrival to CSV: {e}")
    
    async def poll_vehicles(self):
        """Main polling loop - runs continuously"""
        logger.info("Starting GTFS-RT polling loop")
        
        while True:
            try:
                # Fetch vehicle positions
                response = requests.get(GTFS_RT_URL, timeout=REQUEST_TIMEOUT)
                
                if response.status_code != 200:
                    logger.error(f"GTFS-RT endpoint returned status {response.status_code}")
                    await asyncio.sleep(POLL_INTERVAL)
                    continue
                
                # Parse protobuf
                feed = gtfs_realtime_pb2.FeedMessage()
                feed.ParseFromString(response.content)
                
                now = datetime.now(tz=TZ)
                logged_count = 0
                
                # Update last_seen timestamp for vehicles present in this feed
                trip_ids_in_feed = set()
                
                # Process each vehicle
                for entity in feed.entity:
                    if not (entity.HasField('vehicle') and entity.vehicle.HasField('position')):
                        continue
                    
                    vehicle = entity.vehicle
                    
                    # Get trip_id (we'll use this as vehicle_id for consistency)
                    trip_id = vehicle.trip.trip_id if vehicle.trip.HasField('trip_id') else None
                    if not trip_id or trip_id not in self.trip_stops:
                        continue
                    
                    # Track that this trip_id is in the current feed
                    trip_ids_in_feed.add(trip_id)
                    
                    route_id = vehicle.trip.route_id if vehicle.trip.HasField('route_id') else None
                    lat = vehicle.position.latitude
                    lon = vehicle.position.longitude
                    
                    # Check each stop in this trip
                    for stop_info in self.trip_stops.get(trip_id, []):
                        stop_id = stop_info.get('stop_id')
                        if not stop_id or stop_id not in self.stops:
                            continue
                        
                        stop = self.stops[stop_id]
                        distance = self.haversine(lat, lon, stop['lat'], stop['lon'])
                        
                        if distance >= DISTANCE_THRESHOLD_M:
                            continue
                        
                        # Check if already logged this arrival
                        with self.data_lock:
                            if trip_id not in self.vehicle_arrivals:
                                self.vehicle_arrivals[trip_id] = set()
                            if stop_id in self.vehicle_arrivals[trip_id]:
                                continue
                        
                        # Calculate delay
                        scheduled_time_str = stop_info.get('arrival_time')
                        stop_sequence = stop_info.get('stop_sequence')
                        scheduled_dt = self._parse_gtfs_time(scheduled_time_str, trip_id, now)
                        
                        delay_sec = None
                        if scheduled_dt:
                            delay_sec = int((now - scheduled_dt).total_seconds())
                        
                        # Log to CSV
                        self._log_arrival_to_csv(now, trip_id, route_id, stop_id, 
                                                stop['name'], scheduled_dt, delay_sec)
                        
                        # Store arrival info
                        with self.data_lock:
                            self.vehicle_arrivals[trip_id].add(stop_id)
                            self.vehicle_latest_arrival[trip_id] = {
                                'stop_id': stop_id,
                                'stop_name': stop['name'],
                                'stop_sequence': stop_sequence,
                                'timestamp': now.strftime("%Y-%m-%d %H:%M:%S %Z"),
                                'delay_seconds': delay_sec,
                                'route_id': route_id,
                                'trip_id': trip_id,
                                'last_seen': now  # Track when we last saw this vehicle
                            }
                        
                        logged_count += 1
                
                # Update last_seen for vehicles that are still in the feed but didn't trigger new arrivals
                with self.data_lock:
                    for trip_id in trip_ids_in_feed:
                        if trip_id in self.vehicle_latest_arrival:
                            self.vehicle_latest_arrival[trip_id]['last_seen'] = now
                
                logger.info(f"Logged {logged_count} arrivals (feed entities: {len(feed.entity)})")
                
            except DecodeError:
                logger.error("DecodeError: Received non-protobuf or truncated GTFS-RT response")
            except Exception as e:
                logger.error(f"Error in polling loop: {e}")
            
            await asyncio.sleep(POLL_INTERVAL)


# Global instance
arrival_logger = ArrivalLogger()