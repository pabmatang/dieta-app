from sqlalchemy import create_engine
from sqlalchemy.orm import  sessionmaker

SQLALCHEMY_DATABASE_URL = "sqlite:///./test.db"  # Asegúrate de usar la URL correcta para tu base de datos

engine = create_engine(SQLALCHEMY_DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()