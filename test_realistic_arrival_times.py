#!/usr/bin/env python3
"""
Test script for realistic_stop_times_service.py
Run this from the project root directory.
"""

import sys
import logging
import csv
from pathlib import Path
from datetime import datetime

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Import the service
try:
    from services.realistic_stop_times_service import (
        calculate_realistic_stop_times,
        get_delay_statistics,
        realistic_stop_times_service
    )
except ImportError as e:
    logger.error(f"Failed to import service: {e}")
    logger.error("Make sure you're running this from the project root directory")
    sys.exit(1)


def check_files_exist():
    """Check if required files exist"""
    logger.info("=" * 60)
    logger.info("Checking required files...")
    logger.info("=" * 60)
    
    files_to_check = [
        "gtfs_static/stop_times.txt",
        "arrival_log_cleaned.csv"
    ]
    
    all_exist = True
    for file_path in files_to_check:
        exists = Path(file_path).exists()
        status = "✓" if exists else "✗"
        logger.info(f"{status} {file_path}: {'EXISTS' if exists else 'NOT FOUND'}")
        if not exists:
            all_exist = False
    
    return all_exist


def analyze_arrival_log():
    """Analyze the arrival log file"""
    logger.info("\n" + "=" * 60)
    logger.info("Analyzing arrival_log_cleaned.csv...")
    logger.info("=" * 60)
    
    log_file = Path("arrival_log_cleaned.csv")
    if not log_file.exists():
        logger.warning("arrival_log_cleaned.csv not found - no data to analyze")
        return
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            if not rows:
                logger.warning("arrival_log_cleaned.csv is empty (only headers)")
                return
            
            logger.info(f"Total rows in log: {len(rows)}")
            
            # Count valid delay entries
            valid_delays = [
                int(row['delay_seconds']) 
                for row in rows 
                if row.get('delay_seconds', '').strip() 
                and row['delay_seconds'].strip().lstrip('-').isdigit()
            ]
            
            if valid_delays:
                logger.info(f"Valid delay entries: {len(valid_delays)}")
                logger.info(f"Min delay: {min(valid_delays)} seconds ({min(valid_delays)/60:.1f} minutes)")
                logger.info(f"Max delay: {max(valid_delays)} seconds ({max(valid_delays)/60:.1f} minutes)")
                logger.info(f"Avg delay: {sum(valid_delays)/len(valid_delays):.1f} seconds")
                
                # Count how many would be filtered
                filtered = sum(1 for d in valid_delays if abs(d) > 1800)
                logger.info(f"Entries that will be filtered (>30 min): {filtered}")
            else:
                logger.warning("No valid delay entries found in log")
            
            # Show sample entries
            logger.info("\nSample entries (first 3):")
            for i, row in enumerate(rows[:3], 1):
                logger.info(f"  {i}. Trip: {row.get('trip_id', 'N/A')}, "
                          f"Stop: {row.get('stop_id', 'N/A')}, "
                          f"Delay: {row.get('delay_seconds', 'N/A')}s")
    
    except Exception as e:
        logger.error(f"Error analyzing arrival log: {e}")


def analyze_static_stop_times():
    """Analyze the static stop_times.txt file"""
    logger.info("\n" + "=" * 60)
    logger.info("Analyzing gtfs_static/stop_times.txt...")
    logger.info("=" * 60)
    
    stop_times_file = Path("gtfs_static/stop_times.txt")
    if not stop_times_file.exists():
        logger.error("stop_times.txt not found")
        return
    
    try:
        with open(stop_times_file, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            
            logger.info(f"Total stop times: {len(rows)}")
            
            # Count unique trips and stops
            trips = set(row.get('trip_id', '') for row in rows)
            stops = set(row.get('stop_id', '') for row in rows)
            
            logger.info(f"Unique trips: {len(trips)}")
            logger.info(f"Unique stops: {len(stops)}")
            
            # Show sample
            logger.info("\nSample entries (first 3):")
            for i, row in enumerate(rows[:3], 1):
                logger.info(f"  {i}. Trip: {row.get('trip_id', 'N/A')}, "
                          f"Stop: {row.get('stop_id', 'N/A')}, "
                          f"Arrival: {row.get('arrival_time', 'N/A')}")
    
    except Exception as e:
        logger.error(f"Error analyzing stop_times.txt: {e}")


def run_calculation():
    """Run the realistic stop times calculation"""
    logger.info("\n" + "=" * 60)
    logger.info("Running realistic stop times calculation...")
    logger.info("=" * 60)
    
    try:
        success = calculate_realistic_stop_times()
        
        if success:
            logger.info("✓ Calculation completed successfully!")
        else:
            logger.error("✗ Calculation failed")
            return False
        
        return True
    
    except Exception as e:
        logger.error(f"Error during calculation: {e}")
        import traceback
        traceback.print_exc()
        return False


def show_statistics():
    """Display statistics about the calculation"""
    logger.info("\n" + "=" * 60)
    logger.info("Delay Statistics")
    logger.info("=" * 60)
    
    stats = get_delay_statistics()
    
    if not stats:
        logger.warning("No statistics available (no delay data)")
        return
    
    logger.info(f"Total observations: {stats.get('total_observations', 0)}")
    logger.info(f"Unique (trip, stop) pairs: {stats.get('unique_trip_stop_pairs', 0)}")
    logger.info(f"Median delay: {stats.get('median_delay_seconds', 0)} seconds "
               f"({stats.get('median_delay_seconds', 0)/60:.1f} minutes)")
    logger.info(f"Mean delay: {stats.get('mean_delay_seconds', 0):.1f} seconds "
               f"({stats.get('mean_delay_seconds', 0)/60:.1f} minutes)")
    logger.info(f"Min delay: {stats.get('min_delay_seconds', 0)} seconds "
               f"({stats.get('min_delay_seconds', 0)/60:.1f} minutes)")
    logger.info(f"Max delay: {stats.get('max_delay_seconds', 0)} seconds "
               f"({stats.get('max_delay_seconds', 0)/60:.1f} minutes)")
    logger.info(f"Std deviation: {stats.get('stdev_delay_seconds', 0):.1f} seconds")


def compare_outputs():
    """Compare original and realistic stop times"""
    logger.info("\n" + "=" * 60)
    logger.info("Comparing original vs realistic stop times")
    logger.info("=" * 60)
    
    original_file = Path("gtfs_static/stop_times.txt")
    realistic_file = Path("gtfs_static/realistic_stop_times.txt")
    
    if not realistic_file.exists():
        logger.warning("realistic_stop_times.txt not found - calculation may have failed")
        return
    
    try:
        with open(original_file, 'r', encoding='utf-8') as f:
            original_rows = list(csv.DictReader(f))
        
        with open(realistic_file, 'r', encoding='utf-8') as f:
            realistic_rows = list(csv.DictReader(f))
        
        logger.info(f"Original entries: {len(original_rows)}")
        logger.info(f"Realistic entries: {len(realistic_rows)}")
        
        # Count differences
        differences = 0
        for orig, real in zip(original_rows[:100], realistic_rows[:100]):
            if orig.get('arrival_time') != real.get('arrival_time'):
                differences += 1
        
        logger.info(f"Time differences in first 100 entries: {differences}")
        
        # Show sample comparisons
        logger.info("\nSample comparisons (first 5 with differences):")
        shown = 0
        for orig, real in zip(original_rows, realistic_rows):
            if orig.get('arrival_time') != real.get('arrival_time'):
                logger.info(f"\n  Trip: {orig.get('trip_id')}, Stop: {orig.get('stop_id')}")
                logger.info(f"  Original:  {orig.get('arrival_time')}")
                logger.info(f"  Realistic: {real.get('arrival_time')}")
                shown += 1
                if shown >= 5:
                    break
        
        if shown == 0:
            logger.info("  No differences found in checked entries")
    
    except Exception as e:
        logger.error(f"Error comparing files: {e}")


def main():
    """Main test function"""
    logger.info("\n" + "=" * 60)
    logger.info("REALISTIC STOP TIMES SERVICE - TEST SCRIPT")
    logger.info("=" * 60)
    logger.info(f"Started at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Step 1: Check files
    if not check_files_exist():
        logger.error("\nTest cannot continue - required files missing")
        logger.info("\nTo fix:")
        logger.info("  1. Make sure gtfs_static/stop_times.txt exists")
        logger.info("  2. Make sure arrival_logger service has been running to generate arrival_log_cleaned.csv")
        sys.exit(1)
    
    # Step 2: Analyze input data
    analyze_arrival_log()
    analyze_static_stop_times()
    
    # Step 3: Run calculation
    if not run_calculation():
        logger.error("\nTest failed during calculation")
        sys.exit(1)
    
    # Step 4: Show statistics
    show_statistics()
    
    # Step 5: Compare outputs
    compare_outputs()
    
    # Summary
    logger.info("\n" + "=" * 60)
    logger.info("TEST COMPLETED SUCCESSFULLY")
    logger.info("=" * 60)
    logger.info(f"Completed at: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"\nOutput file: gtfs_static/realistic_stop_times.txt")
    logger.info("You can now use this file in place of the original stop_times.txt")


if __name__ == "__main__":
    main()