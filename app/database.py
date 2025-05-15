from sqlalchemy import create_engine
from sqlalchemy.orm import  sessionmaker
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()

#SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"  # Asegúrate de usar la URL correcta para tu base de datos
DATABASE_URL = os.getenv("DATABASE_URL")
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
