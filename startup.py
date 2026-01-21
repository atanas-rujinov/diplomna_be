import os
import csv
import zipfile
import shutil
import requests
import psycopg2
from sqlalchemy.orm import Session
from db.models import Stop, StopTime, Trip, Route
from collections import defaultdict
from dotenv import load_dotenv

load_dotenv()

GTFS_DIR = "gtfs_static"
GTFS_ZIP_URL = "https://gtfs.sofiatraffic.bg/api/v1/static"
GTFS_ZIP_PATH = "gtfs_static.zip"

DB_NAME = "gtfs_static"
DB_ADMIN_CONFIG = {
    "dbname": "postgres",
    "user": os.environ['DB_USER'],
    "password": os.environ['DB_PASSWORD'],
    "host": "localhost",
    "port": 5432,
}

DB_CONFIG = {
    **DB_ADMIN_CONFIG,
    "dbname": DB_NAME,
}

REQUIRED_GTFS_FILES = {
    "agency.txt",
    "stops.txt",
    "routes.txt",
    "trips.txt",
    "stop_times.txt",
    "calendar_dates.txt",
    "shapes.txt",
    "levels.txt",
    "pathways.txt",
    "fare_attributes.txt",
    "feed_info.txt",
    "transfers.txt",
    "translations.txt",
}


# ---------- GTFS FILE HANDLING ----------

def gtfs_files_exist() -> bool:
    if not os.path.isdir(GTFS_DIR):
        return False
    return all(os.path.exists(os.path.join(GTFS_DIR, f)) for f in REQUIRED_GTFS_FILES)


def download_gtfs_zip():
    print("Downloading GTFS static data...")
    r = requests.get(GTFS_ZIP_URL, timeout=30)
    r.raise_for_status()
    with open(GTFS_ZIP_PATH, "wb") as f:
        f.write(r.content)


def extract_gtfs_zip():
    print("Extracting GTFS static data...")
    if os.path.exists(GTFS_DIR):
        shutil.rmtree(GTFS_DIR)
    os.makedirs(GTFS_DIR, exist_ok=True)

    with zipfile.ZipFile(GTFS_ZIP_PATH, "r") as zip_ref:
        zip_ref.extractall(GTFS_DIR)

    os.remove(GTFS_ZIP_PATH)


# ---------- DATABASE ----------

def ensure_database_exists():
    conn = psycopg2.connect(**DB_ADMIN_CONFIG)
    conn.autocommit = True
    cur = conn.cursor()

    cur.execute(
        "SELECT 1 FROM pg_database WHERE datname = %s;",
        (DB_NAME,)
    )

    exists = cur.fetchone() is not None

    if not exists:
        print(f"Creating database '{DB_NAME}'...")
        cur.execute(f'CREATE DATABASE "{DB_NAME}";')
    else:
        print(f"Database '{DB_NAME}' already exists.")

    cur.close()
    conn.close()


def get_db_connection():
    return psycopg2.connect(**DB_CONFIG)


def create_table_from_csv(cursor, table_name: str, csv_path: str):
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


def load_csv_into_table(cursor, table_name: str, csv_path: str):
    with open(csv_path, "r", encoding="utf-8") as f:
        cursor.copy_expert(
            f'COPY "{table_name}" FROM STDIN WITH CSV HEADER',
            f
        )


def load_gtfs_into_db():
    print("Loading GTFS data into database...")
    conn = get_db_connection()
    cur = conn.cursor()

    for file_name in REQUIRED_GTFS_FILES:
        table_name = file_name.replace(".txt", "")
        csv_path = os.path.join(GTFS_DIR, file_name)

        print(f"  → Importing {file_name} into table '{table_name}'")
        create_table_from_csv(cur, table_name, csv_path)
        load_csv_into_table(cur, table_name, csv_path)

    conn.commit()
    cur.close()
    conn.close()
    print("GTFS data loaded successfully.")


# ---------- ENTRY POINT ----------

def run_startup():
    print("Running startup checks...")

    # 1️⃣ Ensure GTFS files are present
    if not gtfs_files_exist():
        print("GTFS files missing, downloading...")
        download_gtfs_zip()
        extract_gtfs_zip()
    else:
        print("GTFS files already present.")

    # 2️⃣ Ensure database exists and load GTFS data
    ensure_database_exists()
    load_gtfs_into_db()
    
    
    print("Creating realistic_stop_times")
    from services.realistic_stop_times_service import calculate_realistic_stop_times
    print("\n" + "=" * 60)
    print("Running realistic stop times calculation...")
    print("=" * 60)
    
    try:
        success = calculate_realistic_stop_times()
        
        if success:
            print("✓ Calculation completed successfully!")
        else:
            print("✗ Calculation failed")
    
    except Exception as e:
        print(f"Error during calculation: {e}")
        import traceback
        traceback.print_exc()

    print("Loading RAPTOR timetable into memory...")
    from sqlalchemy.orm import sessionmaker
    from db.connection import engine
    from services.timetables import Timetables  # Import from services, not defined here
    from services.raptor_service import RaptorService
    
    SessionLocal = sessionmaker(bind=engine)
    db_session = SessionLocal()

    timetable = Timetables(db_session)
    timetable.load()

    # Initialize the service once here
    # This triggers the heavy transfer graph building and route grouping
    raptor_service = RaptorService(timetable)

    # Return BOTH so main.py can unpack them
    return timetable, raptor_service