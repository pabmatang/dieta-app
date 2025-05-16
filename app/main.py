from fastapi import FastAPI, Depends, HTTPException, Body, Request
from app.models.MenuRequest import MenuRequest
from fastapi.middleware.cors import CORSMiddleware
from app.services.menu_generator import generate_weekly_menu, _create_recipe_option_from_data, generate_recommended_weekly_menu
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from . import database, models, schemas, auth
from fastapi.security import OAuth2PasswordRequestForm
from .schemas import Token , WeeklyMenuWithOptionsResponse, RecipeOption, FavoritaRequest, FavoritasResponse
from app.base import Base
from .users import User
from fastapi.responses import JSONResponse
from typing import Dict, Union , List, Optional, Tuple , Any
import re
from collections import defaultdict
from pydantic import BaseModel, Field
import requests
import google.generativeai as genai
import json
import os
from dotenv import load_dotenv

# Cargar variables de entorno
load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")

app = FastAPI()
Base.metadata.create_all(bind=database.engine)



# Configura tu clave API de Google
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY")
FRONTEND_URL = os.getenv("FRONTEND_URL")
genai.configure(api_key=GOOGLE_API_KEY)


app.add_middleware(
    CORSMiddleware,
    allow_origins=FRONTEND_URL,  # dirección del frontend
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
def get_db():
    db = database.SessionLocal()
    try:
        yield db
    finally:
        db.close()

@app.post("/register")
def register(user: schemas.UserCreate, db: Session = Depends(get_db)):
    db_user = db.query(User).filter(User.username == user.username).first()
    if db_user:
        raise HTTPException(status_code=400, detail="Usuario ya registrado")
    created_user = auth.create_user(db, user.username, user.email, user.password)

    return JSONResponse(
        status_code=201,
        content={
            "message": "Usuario creado correctamente",
            "user": {
                "id": created_user.id,
                "username": created_user.username,
                "email": created_user.email
            }
        }
    )
    

@app.post("/login", response_model=Token)
def login(user: schemas.UserLogin, db: Session = Depends(get_db)):
    user_in_db = auth.authenticate_user(db, user.username, user.password)
    if not user_in_db:
        raise HTTPException(status_code=401, detail="Credenciales incorrectas")
    access_token = auth.create_access_token(data={"sub": user.username})
    return {"access_token": access_token, "token_type": "bearer"}

@app.post("/generate-weekly-menu", response_model=WeeklyMenuWithOptionsResponse)
async def weekly_menu_endpoint( # Lo hago async por si futuras llamadas internas lo son
    request: MenuRequest,
    # current_user: User = Depends(auth.get_current_user) # Descomentar para proteger
):
    try:
        # menu_generator.generate_weekly_menu ahora devuelve un Dict que Pydantic validará
        print(f"Received request in /generate-weekly-menu: {request.model_dump_json(indent=2)}")
        
        menu_dict = generate_weekly_menu(request)
        # print("Generated menu dict for response_model:", menu_dict) # Para depuración
        return menu_dict
    except ValueError as ve: # Errores de validación, ej. ratios no suman 1
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"Error inesperado generando menú semanal: {e}", exc_info=True) # Log completo del error
        raise HTTPException(status_code=500, detail=f"Error interno del servidor al generar el menú.")


@app.get("/perfil")
def get_user_profile(current_user: User = Depends(auth.get_current_user)):
    print(f"sale : ",current_user.recetas_favoritas)
    return {
        "usuario": current_user.username,
        "email": current_user.email,
        "edad": current_user.edad,
        "genero": current_user.genero,
        "altura": current_user.altura,
        "peso": current_user.peso,
        "actividad": current_user.actividad,
        "objetivo": current_user.objetivo,
        "bmr":current_user.bmr,
        "last_generated_menu_json":current_user.last_generated_menu_json,
        "recetas_favoritas":current_user.recetas_favoritas
    }
    

@app.patch("/actualizar-perfil")
def actualizar_parcial_perfil(
    cambios: schemas.PerfilUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user)
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    # Actualizar solo los campos enviados
    if cambios.peso is not None:
        user.peso = cambios.peso
    if cambios.actividad is not None:
        user.actividad = cambios.actividad
    if cambios.objetivo is not None:
        user.objetivo = cambios.objetivo

    # Recalcular BMR si hay cambios que lo afectan
    if cambios.peso is not None or cambios.actividad is not None or cambios.objetivo is not None:
        try:
            user.bmr = calcular_bmr(user.genero, user.peso, user.altura, user.edad)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    db.commit()
    return {"message": "Perfil actualizado correctamente"}


@app.post("/user-info")
def update_user_info(
    user_info: schemas.UserInfoUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user)
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    # Calcular BMR
    bmr = calcular_bmr(user_info.genero, user_info.peso, user_info.altura, user_info.edad)
    user.edad = user_info.edad
    user.genero = user_info.genero
    user.altura = user_info.altura
    user.peso = user_info.peso
    user.actividad = user_info.actividad
    user.objetivo = user_info.objetivo
    user.bmr = bmr
    

    db.commit()
    return {"message": "Información actualizada correctamente"}

def clean_ingredient(raw: str):
    import re

    # Diccionario para corregir errores comunes
    corrections = {
        "arlic": "garlic",
        "iol": "oil",
        "rapes": "grapes",
        "tumeric": "turmeric",
        "/head cauliflower": "cauliflower",
        "juice /lime": "lime juice",
        "caldo de pollo": "chicken broth",
        "white/white wine vinegar": "white wine vinegar"
    }

    # Frases que no deben considerarse ingredientes
    discard_phrases = [
        "for brushing vegetables", "into inch florets", "into /inchthick slices",
        "yield once processed", "with brush stems", "with tails thawed",
        "the root thinly", "halved lengthways thin", "into small cubes",
        "into inch pieces", "ribs seeds thinly"
    ]

    raw = raw.strip().lower()

    # Corregir errores ortográficos
    for wrong, right in corrections.items():
        raw = raw.replace(wrong, right)

    # Eliminar frases no relevantes
    for phrase in discard_phrases:
        if phrase in raw:
            return None, 0, ""

    # Limpieza general
    raw = re.sub(r"[\*\-]", "", raw)
    raw = re.sub(r"\([^)]*\)", "", raw)
    raw = re.sub(r"\b(optional|to taste|as desired|depending.*|divided)\b", "", raw)
    raw = re.sub(r"\b(can|cup|cups|tbsp|tsp|oz|ounce|tablespoon|teaspoon|g|kg|ml|l|container|pkg|bunch|head)\b", "", raw)
    raw = re.sub(r"\d+\.?\d*\s?(oz|g|ml|kg|lb|cup|cups|tbsp|tsp|tablespoon|teaspoon|container|pkg)?", "", raw)
    raw = re.sub(r",", "", raw)

    words = raw.split()
    if not words:
        return "unknown", 1.0, ""

    # Filtrado de palabras útiles
    ignore_words = {
        "and", "or", "with", "cut", "sliced", "diced", "peeled", "each", "few",
        "shakes", "removed", "washed", "dry", "dried", "thinly", "minced", "chopped"
    }

    keywords = [w for w in words if len(w) > 2 and w not in ignore_words]

    # Heurística para formar el nombre del ingrediente
    name = " ".join(keywords[-3:]) if keywords else "unknown"

    return name.strip(), 1.0, ""


class ShoppingListRequestPayload(BaseModel):
    # menu: Dict[str, Dict[str, RecipeOption]] # Si RecipeOption es el modelo Pydantic
    # O si el frontend envía un JSON genérico que se parece a RecipeOption:
    menu: Dict[str, Dict[str, Optional[Dict[str, Any]]]] # Día -> TipoComida -> Receta o None

@app.post("/generate-shopping-list")
async def generate_shopping_list_endpoint(payload: ShoppingListRequestPayload):
    # Tu defaultdict original
    # shopping_dict = defaultdict(float) -> Cambiado para manejar unidades
    aggregated_ingredients: Dict[str, Dict[str, Any]] = defaultdict(lambda: {"quantity": 0.0, "units": set()})

    # Acceder a `payload.menu` en lugar de `menu.get("menu", {})`
    selected_menu_data = payload.menu

    for dia, comidas_del_dia in selected_menu_data.items():
        for tipo_comida, receta_seleccionada_data in comidas_del_dia.items():
            # receta_seleccionada_data es un diccionario que debe tener 'ingredients'
            if receta_seleccionada_data and isinstance(receta_seleccionada_data.get("ingredients"), list):
                for raw_ingredient_line in receta_seleccionada_data["ingredients"]:
                    if isinstance(raw_ingredient_line, str): # Asegurar que es una string
                        name, qty, unit = clean_ingredient(raw_ingredient_line)
                        if name and name != "unknown": # Ignorar ingredientes no parseados
                            # Clave para agregar podría ser solo el nombre si manejas unidades de otra forma
                            # key = f"{name} ({unit})" if unit else name # Tu forma original
                            # shopping_dict[key] += qty # Tu forma original

                            # Nueva forma para agregar unidades
                            aggregated_ingredients[name]["quantity"] += qty
                            if unit: # Añadir la unidad si existe
                                aggregated_ingredients[name]["units"].add(unit)
                    # else: print(f"Advertencia: ingrediente no es string: {raw_ingredient_line}")
            # else: print(f"Advertencia: No hay ingredientes para {dia}-{tipo_comida} o no es una lista.")

    # Formatear la salida final
    final_list: Dict[str, Dict[str, Any]] = {}
    for name, data in aggregated_ingredients.items():
        unit_str = ", ".join(sorted(list(data["units"]))) if data["units"] else "unidad(es)"
        final_list[name] = {
            "amount": round(data["quantity"], 2),
            "unit": unit_str
        }
    
    return dict(sorted(final_list.items())) # Ordenar por nombre de ingrediente


def calcular_bmr(sexo: str, peso: float, altura: float, edad: int) -> int:
    if sexo == "masculino":
        bmr = 88.362 + (13.397 * peso) + (4.799 * altura) - (5.677 * edad)
    elif sexo == "femenino":
        bmr = 447.593 + (9.247 * peso) + (3.098 * altura) - (4.330 * edad)
    else:
        raise ValueError("Sexo no válido")
    return round(bmr)

class PromptInput(BaseModel):
    prompt: str

@app.post("/ia/alternativa")
async def get_alternativa(data: PromptInput):
    try:
        model = genai.GenerativeModel("gemini-2.0-flash")
        response = model.generate_content(f"Eres un nutricionista experto en hacer recetas saludables. Usuario: {data.prompt}. Responde con una receta alternativa más saludable, enfocada en reducir calorías, grasas y azúcares. Incluye información nutricional detallada total(calorías, grasas, azúcares) y las diferencias con la receta tradicional.")
        return {"resultado": response.text}
    except Exception as e:
        return {
            "error": "Error al generar contenido con Google Gemini",
            "detalle": str(e)
        }

# Ruta para guardar el menú del usuario
@app.post("/guardar-menu")
def guardar_menu_usuario(menu: dict, db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")
    try:
        #menu_string = str(menu)
        user.last_generated_menu_json = json.dumps(menu)
        db.commit()
        return {"message": "Menú guardado correctamente."}
    except Exception as e:
        db.rollback()  # Deshacer cualquier cambio en caso de error
        print(f"Error al guardar el menú: {e}")  # Depuración del error
        raise HTTPException(status_code=500, detail=f"Error al guardar el menú: {e}")
# Ruta para obtener el menú guardado del usuario
@app.get("/menu-guardado")
def obtener_menu_guardado(db: Session = Depends(get_db), user: User = Depends(auth.get_current_user)):
    if not user.last_generated_menu_json:
        raise HTTPException(status_code=404, detail="No hay menú guardado.")
    return json.loads(user.last_generated_menu_json)


@app.post("/marcar-favorita")
def marcar_receta_favorita(
    request: FavoritaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user)
):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    try:
        favoritas = json.loads(user.recetas_favoritas)
        if request.receta not in favoritas:
            favoritas.append(request.receta)
            user.recetas_favoritas = json.dumps(favoritas)
            db.commit()
        return {"message": "Receta marcada como favorita correctamente."}
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Error al guardar favorita: {e}")

@app.get("/favoritas", response_model=FavoritasResponse)
def obtener_recetas_favoritas(
    db: Session = Depends(get_db),
    current_user: User = Depends(auth.get_current_user)
):
    if not current_user.recetas_favoritas:
        return {"favoritas": []}
    try:
        favoritas = json.loads(current_user.recetas_favoritas)
        return {"favoritas": favoritas}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error al cargar favoritas: {e}")

@app.post("/guardar-favorita")
async def guardar_favorita(recipe: dict,db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    try:
        if not user.recetas_favoritas:
            user.recetas_favoritas = []
        elif isinstance(user.recetas_favoritas, str):
            user.recetas_favoritas = json.loads(user.recetas_favoritas)
        print(f"sale : ",user.recetas_favoritas)
        user.recetas_favoritas.append(recipe)
        user.recetas_favoritas = json.dumps(user.recetas_favoritas)
        print(f"sale despues : ",user.recetas_favoritas)
        db.commit()
        print(f"se guarda : ")
        return {"message": "Receta guardada como favorita"}
    except Exception as e:
        db.rollback()  # Deshacer cualquier cambio en caso de error
        print(f"Error al guardar la receta favorita: {e}")  # Depuración del error
        raise HTTPException(status_code=500, detail=f"Error al guardar la receta favorita: {e}")
    

@app.post("/eliminar-favorita")
async def eliminar_favorita(recipe: dict,db: Session = Depends(get_db), current_user: User = Depends(auth.get_current_user)):
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(status_code=404, detail="Usuario no encontrado")

    try:
        if not user.recetas_favoritas:
            # Si no hay favoritas, inicializar como una lista JSON vacía
            user.recetas_favoritas = json.dumps([])
        elif isinstance(user.recetas_favoritas, str):
            user.recetas_favoritas = json.loads(user.recetas_favoritas)
        
        # Asegurarse de que recetas_favoritas sea una lista después de cargar/parsear
        if not isinstance(user.recetas_favoritas, list):
            # Si por alguna razón no es una lista (ej. null de JSON, o mal formato), tratar como vacía
            print(f"Advertencia: recetas_favoritas no era una lista después de cargar: {user.recetas_favoritas}")
            user.recetas_favoritas = []

        print("Recetas antes de eliminar:", user.recetas_favoritas)
        print("Receta a eliminar (payload):", recipe)
        
        # Corregido: Comparar r.get("url") con recipe.get("recipe_url")
        # recipe.get("recipe_url") contiene la URL enviada desde el frontend.
        # Las recetas almacenadas en user.recetas_favoritas tienen su URL en la clave "url".
        url_a_eliminar = recipe.get("recipe_url")
        if url_a_eliminar:
            recetas_actualizadas = [
                r for r in user.recetas_favoritas if r.get("url") != url_a_eliminar
            ]
            
            if len(recetas_actualizadas) < len(user.recetas_favoritas):
                print(f"Receta con URL {url_a_eliminar} encontrada y eliminada.")
            else:
                print(f"Advertencia: No se encontró ninguna receta con URL {url_a_eliminar} para eliminar.")
                print(f"URLs en lista: {[r.get('url') for r in user.recetas_favoritas]}")


            user.recetas_favoritas = recetas_actualizadas
        else:
            print("Advertencia: No se proporcionó recipe_url en el payload para eliminar.")

        user.recetas_favoritas = json.dumps(user.recetas_favoritas)
        db.commit()
        print("Recetas después de eliminar y guardar:", user.recetas_favoritas)
        return {"message": "Receta eliminada de favoritos correctamente."}
    except Exception as e:
        db.rollback()  # Deshacer cualquier cambio en caso de error
        print(f"Error al eliminar la receta favorita: {e}")  # Depuración del error
        raise HTTPException(status_code=500, detail=f"Error al eliminar la receta favorita: {e}")

# Endpoint para generar menú semanal recomendado
@app.post("/generar-menu-recomendado", response_model=WeeklyMenuWithOptionsResponse)
async def generar_menu_recomendado_endpoint(
    db: Session = Depends(get_db), 
    current_user: User = Depends(auth.get_current_user)
):
    try:
        # Aquí llamaremos a una nueva función en menu_generator.py
        # que tomará current_user para acceder a BMR, actividad, objetivo y favoritos
        from app.services.menu_generator import generate_recommended_weekly_menu # Importación diferida
        
        # Podríamos definir aquí los tipos de comida y ratios por defecto, 
        # o permitir que el usuario los envíe si queremos más personalización en el futuro.
        default_meals = ["desayuno", "comida", "cena"]
        default_meal_ratios = {"desayuno": 0.30, "comida": 0.40, "cena": 0.30}
        num_options_per_meal = 3 # Opciones por comida, igual que en MenuSemanal

        menu_dict = generate_recommended_weekly_menu(
            user=current_user,
            db_session=db, # Por si se necesita acceder a más datos del usuario o relacionados
            meals_config=default_meals,
            ratios_config=default_meal_ratios,
            num_options=num_options_per_meal
        )
        return menu_dict
    except ValueError as ve:
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"Error inesperado generando menú recomendado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno del servidor al generar el menú recomendado.")
