import csv
import logging
import psycopg2
from pathlib import Path
from typing import Dict, List, Tuple
from datetime import datetime, timedelta
from collections import defaultdict
import statistics
import os
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

# Configuration
GTFS_DIR = "gtfs_static"
LOG_FILE = "arrival_log.csv"
IQR_MULTIPLIER = 3.0  # How many IQRs away to consider outlier (3.0 is more permissive than standard 1.5)
OUTPUT_FILE = f"{GTFS_DIR}/realistic_stop_times.txt"

DB_CONFIG = {
    "dbname": "postgres",
    "user": os.environ['DB_USER'],
    "password": os.environ['DB_PASSWORD'],
    "host": "localhost",
    "port": 5432,
}


class RealisticStopTimesService:
    """Service to calculate realistic stop times based on actual arrival data"""
    
    def __init__(self):
        self.stop_times_data = []
        self.delay_stats = defaultdict(list)
    
    def _load_static_stop_times(self):
        """Load the original stop_times.txt"""
        stop_times = []
        try:
            with open(f"{GTFS_DIR}/stop_times.txt", 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    stop_times.append(row)
            logger.info(f"Loaded {len(stop_times)} stop times from static GTFS")
            return stop_times
        except Exception as e:
            logger.error(f"Error loading stop_times.txt: {e}")
            return []
    
    def _parse_arrival_log(self):
        """Parse arrival_log.csv and calculate delay statistics"""
        if not Path(LOG_FILE).exists():
            logger.warning(f"Arrival log file {LOG_FILE} does not exist")
            return
        
        try:
            # First pass: collect all delays
            raw_delays = defaultdict(list)
            
            with open(LOG_FILE, 'r', encoding='utf-8') as f:
                reader = csv.DictReader(f)
                row_count = 0
                skipped_no_ids = 0
                skipped_no_delay = 0
                error_count = 0
                
                for row in reader:
                    row_count += 1
                    
                    if row_count <= 3:
                        logger.info(f"Sample row {row_count}: trip='{row.get('trip_id', '')}', stop='{row.get('stop_id', '')}', delay='{row.get('delay_seconds', '')}'")
                    
                    trip_id = row.get('trip_id', '').strip()
                    stop_id = row.get('stop_id', '').strip()
                    delay_str = row.get('delay_seconds', '').strip()
                    
                    if not trip_id or not stop_id:
                        skipped_no_ids += 1
                        continue
                    
                    if not delay_str:
                        skipped_no_delay += 1
                        continue
                    
                    try:
                        delay_seconds = int(float(delay_str))
                        raw_delays[(trip_id, stop_id)].append(delay_seconds)
                        
                    except (ValueError, TypeError) as e:
                        error_count += 1
                        if error_count <= 5:
                            logger.warning(f"Parse error: '{delay_str}' - {e}")
                        continue
            
            logger.info(f"Parsed {row_count} rows from arrival log")
            logger.info(f"Raw delays collected: {sum(len(v) for v in raw_delays.values())}")
            logger.info(f"Skipped - No IDs: {skipped_no_ids}, No delay: {skipped_no_delay}, Errors: {error_count}")
            
            # Second pass: filter outliers per (trip, stop) pair using IQR method
            total_before_filter = 0
            total_after_filter = 0
            outliers_removed = 0
            
            for key, delays in raw_delays.items():
                total_before_filter += len(delays)
                
                # Need at least 4 data points to calculate IQR meaningfully
                if len(delays) < 4:
                    self.delay_stats[key] = delays
                    total_after_filter += len(delays)
                    continue
                
                # Calculate Q1, Q3, and IQR
                sorted_delays = sorted(delays)
                q1_idx = len(sorted_delays) // 4
                q3_idx = (3 * len(sorted_delays)) // 4
                
                q1 = sorted_delays[q1_idx]
                q3 = sorted_delays[q3_idx]
                iqr = q3 - q1
                
                # Define outlier bounds
                lower_bound = q1 - (IQR_MULTIPLIER * iqr)
                upper_bound = q3 + (IQR_MULTIPLIER * iqr)
                
                # Filter outliers
                filtered_delays = [d for d in delays if lower_bound <= d <= upper_bound]
                
                outliers_removed += len(delays) - len(filtered_delays)
                
                if filtered_delays:
                    self.delay_stats[key] = filtered_delays
                    total_after_filter += len(filtered_delays)
            
            logger.info(f"Outlier filtering complete:")
            logger.info(f"  Before: {total_before_filter} delays")
            logger.info(f"  After: {total_after_filter} delays")
            logger.info(f"  Outliers removed: {outliers_removed} ({100*outliers_removed/total_before_filter:.1f}%)")
            logger.info(f"Found delay data for {len(self.delay_stats)} unique (trip, stop) pairs")
                
        except Exception as e:
            logger.error(f"Error parsing arrival log: {e}")
            import traceback
            traceback.print_exc()
    
    def _calculate_average_delay(self, trip_id, stop_id):
        """Calculate average delay for a specific trip and stop"""
        key = (trip_id, stop_id)
        
        if key not in self.delay_stats or not self.delay_stats[key]:
            return 0
        
        delays = self.delay_stats[key]
        return int(statistics.median(delays))
    
    def _adjust_gtfs_time(self, time_str, delay_seconds):
        """Adjust GTFS time string by adding delay_seconds"""
        try:
            if not time_str:
                return time_str
            
            parts = time_str.split(":")
            if len(parts) != 3:
                return time_str
            
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = int(parts[2])
            
            total_seconds = hours * 3600 + minutes * 60 + seconds
            adjusted_seconds = total_seconds + delay_seconds
            
            if adjusted_seconds < 0:
                adjusted_seconds = 0
            
            adj_hours = adjusted_seconds // 3600
            adj_minutes = (adjusted_seconds % 3600) // 60
            adj_secs = adjusted_seconds % 60
            
            return f"{adj_hours:02d}:{adj_minutes:02d}:{adj_secs:02d}"
            
        except Exception as e:
            logger.error(f"Error adjusting time {time_str}: {e}")
            return time_str
    
    def _write_realistic_stop_times(self):
        """Write realistic stop times to file"""
        try:
            if not self.stop_times_data:
                logger.error("No data to write")
                return False
            
            headers = list(self.stop_times_data[0].keys())
            
            with open(OUTPUT_FILE, 'w', newline='', encoding='utf-8') as f:
                writer = csv.DictWriter(f, fieldnames=headers)
                writer.writeheader()
                writer.writerows(self.stop_times_data)
            
            logger.info(f"Successfully wrote realistic stop times to {OUTPUT_FILE}")
            return True
            
        except Exception as e:
            logger.error(f"Error writing realistic stop times: {e}")
            return False
    
    def _create_table_from_csv(self, cursor, table_name, csv_path):
        """Create database table from CSV file"""
        with open(csv_path, newline="", encoding="utf-8") as f:
            reader = csv.reader(f)
            headers = next(reader)

        columns = ", ".join(f'"{h}" TEXT' for h in headers)

        cursor.execute(f"""
            DROP TABLE IF EXISTS "{table_name}";
            CREATE TABLE "{table_name}" (
                {columns}
            );
        """)
        logger.info(f"Created table '{table_name}'")
    
    def _load_csv_into_table(self, cursor, table_name, csv_path):
        """Load CSV data into database table"""
        with open(csv_path, "r", encoding="utf-8") as f:
            cursor.copy_expert(
                f'COPY "{table_name}" FROM STDIN WITH CSV HEADER',
                f
            )
        logger.info(f"Loaded data into table '{table_name}'")
    
    def _push_to_database(self):
        """Push realistic_stop_times.txt to database"""
        try:
            logger.info("Connecting to database...")
            conn = psycopg2.connect(**DB_CONFIG)
            cur = conn.cursor()
            
            table_name = "realistic_stop_times"
            
            logger.info(f"Creating and populating '{table_name}' table...")
            self._create_table_from_csv(cur, table_name, OUTPUT_FILE)
            self._load_csv_into_table(cur, table_name, OUTPUT_FILE)
            
            conn.commit()
            cur.close()
            conn.close()
            
            logger.info(f"Successfully pushed realistic stop times to database table '{table_name}'")
            return True
            
        except Exception as e:
            logger.error(f"Error pushing to database: {e}")
            return False
    
    def calculate_realistic_stop_times(self):
        """Main function to calculate realistic stop times"""
        logger.info("Starting realistic stop times calculation")
        
        self.stop_times_data = self._load_static_stop_times()
        if not self.stop_times_data:
            logger.error("No stop times data loaded")
            return False
        
        self._parse_arrival_log()
        
        # Group stop_times by trip_id to process each trip independently
        trips_dict = defaultdict(list)
        for stop_time in self.stop_times_data:
            trip_id = stop_time.get('trip_id', '')
            if trip_id:
                trips_dict[trip_id].append(stop_time)
        
        # Sort each trip's stops by stop_sequence
        for trip_id in trips_dict:
            trips_dict[trip_id].sort(key=lambda x: int(x.get('stop_sequence', 0)))
        
        adjusted_count = 0
        enforced_count = 0
        
        # Process each trip to ensure monotonic time progression
        for trip_id, stops in trips_dict.items():
            prev_time_seconds = None
            
            for stop_time in stops:
                stop_id = stop_time.get('stop_id', '')
                original_arrival = stop_time.get('arrival_time', '')
                
                if not original_arrival:
                    continue
                
                # Calculate the delay for this stop
                avg_delay = self._calculate_average_delay(trip_id, stop_id)
                
                # Apply the delay
                if avg_delay != 0:
                    adjusted_arrival = self._adjust_gtfs_time(original_arrival, avg_delay)
                    adjusted_count += 1
                else:
                    adjusted_arrival = original_arrival
                
                # Convert to seconds for comparison
                parts = adjusted_arrival.split(":")
                current_time_seconds = int(parts[0]) * 3600 + int(parts[1]) * 60 + int(parts[2])
                
                # Enforce monotonic progression: if current time <= previous time, set it to previous + 1 minute
                if prev_time_seconds is not None and current_time_seconds <= prev_time_seconds:
                    current_time_seconds = prev_time_seconds + 60  # Add 1 minute
                    enforced_count += 1
                    
                    # Convert back to time string
                    adj_hours = current_time_seconds // 3600
                    adj_minutes = (current_time_seconds % 3600) // 60
                    adj_secs = current_time_seconds % 60
                    adjusted_arrival = f"{adj_hours:02d}:{adj_minutes:02d}:{adj_secs:02d}"
                
                # Update the stop time
                stop_time['arrival_time'] = adjusted_arrival
                stop_time['departure_time'] = adjusted_arrival
                
                prev_time_seconds = current_time_seconds
        
        logger.info(f"Adjusted {adjusted_count} stop times based on actual data")
        logger.info(f"Enforced monotonic progression on {enforced_count} stops")
        
        if not self._write_realistic_stop_times():
            return False
        
        if not self._push_to_database():
            logger.warning("Failed to push to database, but file was created")
            return False
        
        return True
    
    def get_statistics(self):
        """Get statistics about the delay data"""
        if not self.delay_stats:
            return {}
        
        all_delays = []
        for delays in self.delay_stats.values():
            all_delays.extend(delays)
        
        if not all_delays:
            return {}
        
        return {
            'total_observations': len(all_delays),
            'unique_trip_stop_pairs': len(self.delay_stats),
            'median_delay_seconds': statistics.median(all_delays),
            'mean_delay_seconds': statistics.mean(all_delays),
            'min_delay_seconds': min(all_delays),
            'max_delay_seconds': max(all_delays),
            'stdev_delay_seconds': statistics.stdev(all_delays) if len(all_delays) > 1 else 0
        }


realistic_stop_times_service = RealisticStopTimesService()


def calculate_realistic_stop_times():
    """Public function to calculate realistic stop times"""
    return realistic_stop_times_service.calculate_realistic_stop_times()


def get_delay_statistics():
    """Get statistics about the delay data"""
    return realistic_stop_times_service.get_statistics()