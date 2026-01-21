from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base
import os
from dotenv import load_dotenv

load_dotenv()
DB_URL = "postgresql+psycopg2://"+os.environ['DB_USER']+":"+os.environ['DB_PASSWORD']+"@localhost:5432/gtfs_static"

engine = create_engine(DB_URL, echo=False)  # echo=True for debugging
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


# Dependency for FastAPI
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
