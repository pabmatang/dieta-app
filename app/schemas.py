from pydantic import BaseModel, EmailStr, Field
from typing import Optional , List, Dict

class UserCreate(BaseModel):
    username: str
    email: EmailStr
    password: str

class UserLogin(BaseModel):
    username: str
    password: str

class UserOut(BaseModel):
    id: int
    username: str
    email: EmailStr

    class Config:
        orm_mode = True

class Token(BaseModel):
    access_token: str
    token_type: str

class UserInfoUpdate(BaseModel):
    edad: int
    genero: str
    altura: int
    peso: int
    actividad: str
    objetivo: str

class PerfilUpdate(BaseModel):
    peso: Optional[float]
    actividad: Optional[str]
    objetivo: Optional[str]

# menu_schemas.py
from pydantic import BaseModel, Field
from typing import List, Dict, Optional

class RecipeOption(BaseModel):
    label: str
    image: Optional[str] = None
    url: str
    ingredients: List[str] = Field(description="Lista de líneas de ingredientes como strings")
    calories: float # Calorías por ración de la receta
    # Puedes añadir más campos que Edamam provee y quieras usar en el frontend
    # مثلاً: totalTime: Optional[float] = None
    # مثلاً: yield_servings: Optional[float] = Field(None, alias="yield") # raciones

class MealSlotWithOptions(BaseModel):
    options: Optional[List[RecipeOption]] = None
    error: Optional[str] = None

    class Config:
        extra = "allow"  # Permite atributos dinámicos (desayuno, comida, etc.)

class DayMealsWithOptions(BaseModel):
    # Estos nombres de campo deben coincidir con los valores en MenuRequest.meals
    # Si los nombres de comida son dinámicos, un Dict[str, MealSlotWithOptions] es mejor.
    # Por ahora, basado en tu default:
    desayuno: Optional[MealSlotWithOptions] = None
    comida: Optional[MealSlotWithOptions] = None
    cena: Optional[MealSlotWithOptions] = None

    # Para permitir campos extra si los nombres de comida son dinámicos y no fijos
    # Esto es útil si en MenuRequest.meals puedes tener "merienda", "almuerzo", etc.
    class Config:
        extra = "allow" # Permite campos adicionales que no están explícitamente definidos

class WeeklyMenuWithOptionsResponse(BaseModel):
    lunes: Optional[DayMealsWithOptions] = None
    martes: Optional[DayMealsWithOptions] = None
    # miércoles y sábado con tilde como en tu menu_generator.py
    # Usaremos alias para que el frontend pueda usar claves sin tilde si lo prefiere.
    miércoles: Optional[DayMealsWithOptions] = Field(None, alias="miercoles")
    jueves: Optional[DayMealsWithOptions] = None
    viernes: Optional[DayMealsWithOptions] = None
    sábado: Optional[DayMealsWithOptions] = Field(None, alias="sabado")
    domingo: Optional[DayMealsWithOptions] = None

    class Config:
        allow_population_by_field_name = True # Permite usar el alias al crear el modelo
        # json_encoders para manejar objetos no serializables si es necesario (ej. datetime)

class FavoritaRequest(BaseModel):
    receta: dict  # O usa un modelo específico si ya tienes uno para recetas
    
class FavoritasResponse(BaseModel):
    favoritas: List[dict]

