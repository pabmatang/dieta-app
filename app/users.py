from sqlalchemy import Column, Integer, String, Text
from .base import Base

class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)

    # Nuevos campos de información personal
    edad = Column(Integer, nullable=True)
    genero = Column(String, nullable=True)
    altura = Column(Integer, nullable=True)
    peso = Column(Integer, nullable=True)
    actividad = Column(String, nullable=True)
    objetivo = Column(String, nullable=True)
    bmr = Column(Integer, nullable=True)
    last_generated_menu_json = Column(Text, nullable=True)
    recetas_favoritas = Column(Text, nullable=True)