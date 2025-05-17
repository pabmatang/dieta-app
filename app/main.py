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
from transformers import pipeline
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
# Cargar el pipeline de Hugging Face para generación de texto (GPT-2, GPT-3, etc.)
chatbot = pipeline("text-generation", model="gpt2")  # Puedes usar otros modelos, como GPT-3 si tienes acceso
# Tu token de la API de Hugging Face


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
    

@app.get("/perfil/analisis-nutricional")
async def get_analisis_nutricional_perfil(
    current_user: User = Depends(auth.get_current_user)
):
    if not current_user.last_generated_menu_json:
        raise HTTPException(status_code=404, detail="No hay menú guardado para analizar.")

    try:
        # last_generated_menu_json ALMACENA un objeto que tiene una CLAVE "menu"
        # y el VALOR de esa clave es el diccionario de días y comidas.
        # ej: {"menu": {"lunes": {"desayuno": {"selected": {...}, "options": [...]}}, ...}}
        parsed_json_object = json.loads(current_user.last_generated_menu_json)
        
        # Verificamos que el JSON parseado es un diccionario y contiene la clave "menu"
        if not isinstance(parsed_json_object, dict) or "menu" not in parsed_json_object:
            print(f"Formato inesperado de last_generated_menu_json. Contenido: {current_user.last_generated_menu_json[:500]}...") # Log para depurar
            raise HTTPException(status_code=500, detail="Formato de menú guardado no es el esperado. Falta la clave 'menu' principal.")

        menu_items = parsed_json_object["menu"] # Este es el diccionario de días: {"lunes": ..., "martes": ...}
        
        # Verificamos que menu_items (el contenido de "menu") sea un diccionario
        if not isinstance(menu_items, dict):
            print(f"La clave 'menu' no contiene un diccionario de días. Contenido de 'menu': {menu_items}") # Log para depurar
            raise HTTPException(status_code=500, detail="Formato de menú guardado incorrecto. La clave 'menu' debe ser un diccionario de días.")

    except json.JSONDecodeError:
        print(f"Error al decodificar JSON de last_generated_menu_json. Contenido: {current_user.last_generated_menu_json[:500]}...") # Log para depurar
        raise HTTPException(status_code=500, detail="Error al leer el menú guardado (JSON malformado).")


    analisis_diario = defaultdict(lambda: {
        "totalCalorias": 0.0,
        "macronutrientes": {
            "proteinas_g": 0.0,
            "grasas_g": 0.0,
            "carbohidratos_g": 0.0
        }
    })

    total_calorias_semana = 0.0
    total_proteinas_semana = 0.0
    total_grasas_semana = 0.0
    total_carbohidratos_semana = 0.0
    dias_con_datos_validos = 0 # Contador para días que realmente tienen datos de recetas procesables

    for dia_nombre, comidas_del_dia in menu_items.items():
        if not isinstance(comidas_del_dia, dict): continue

        calorias_dia_actual = 0.0
        proteinas_dia_actual = 0.0
        grasas_dia_actual = 0.0
        carbos_dia_actual = 0.0
        dia_tuvo_recetas_procesadas = False

        for tipo_comida, slot_comida in comidas_del_dia.items():
            if not slot_comida or not isinstance(slot_comida, dict): continue

            receta_seleccionada_data = None
            if "selected" in slot_comida and isinstance(slot_comida["selected"], dict):
                receta_seleccionada_data = slot_comida["selected"]
            elif "options" in slot_comida and isinstance(slot_comida["options"], list) and len(slot_comida["options"]) > 0 and isinstance(slot_comida["options"][0], dict):
                receta_seleccionada_data = slot_comida["options"][0]

            if receta_seleccionada_data:
                calorias = float(receta_seleccionada_data.get("calories", 0.0) or 0.0)
                proteinas = float(receta_seleccionada_data.get("protein_g", 0.0) or 0.0)
                grasas = float(receta_seleccionada_data.get("fat_g", 0.0) or 0.0)
                carbos = float(receta_seleccionada_data.get("carbs_g", 0.0) or 0.0)

                # Solo sumar si la receta tiene calorías (indicativo de datos válidos)
                if calorias > 0 : # Podríamos ser más estrictos (ej. si faltan macros)
                    calorias_dia_actual += calorias
                    proteinas_dia_actual += proteinas
                    grasas_dia_actual += grasas
                    carbos_dia_actual += carbos
                    dia_tuvo_recetas_procesadas = True
        
        if dia_tuvo_recetas_procesadas:
            analisis_diario[dia_nombre]["totalCalorias"] = round(calorias_dia_actual, 2)
            analisis_diario[dia_nombre]["macronutrientes"]["proteinas_g"] = round(proteinas_dia_actual, 2)
            analisis_diario[dia_nombre]["macronutrientes"]["grasas_g"] = round(grasas_dia_actual, 2)
            analisis_diario[dia_nombre]["macronutrientes"]["carbohidratos_g"] = round(carbos_dia_actual, 2)
            
            total_calorias_semana += calorias_dia_actual
            total_proteinas_semana += proteinas_dia_actual
            total_grasas_semana += grasas_dia_actual
            total_carbohidratos_semana += carbos_dia_actual
            dias_con_datos_validos += 1

    promedio_calorias_dia = round(total_calorias_semana / dias_con_datos_validos, 2) if dias_con_datos_validos > 0 else 0.0
    promedio_proteinas_dia = round(total_proteinas_semana / dias_con_datos_validos, 2) if dias_con_datos_validos > 0 else 0.0
    promedio_grasas_dia = round(total_grasas_semana / dias_con_datos_validos, 2) if dias_con_datos_validos > 0 else 0.0
    promedio_carbohidratos_dia = round(total_carbohidratos_semana / dias_con_datos_validos, 2) if dias_con_datos_validos > 0 else 0.0

    # Filtrar días sin datos del análisis_diario para no enviar entradas vacías
    analisis_diario_filtrado = {k: v for k, v in analisis_diario.items() if v["totalCalorias"] > 0 or \
                               v["macronutrientes"]["proteinas_g"] > 0 or \
                               v["macronutrientes"]["grasas_g"] > 0 or \
                               v["macronutrientes"]["carbohidratos_g"] > 0}

    return {
        "analisisSemanal": {
            "totalCalorias": round(total_calorias_semana, 2),
            "promedioCaloriasDia": promedio_calorias_dia,
            "diasConDatos": dias_con_datos_validos,
            "macronutrientes": {
                "total_proteinas_g": round(total_proteinas_semana, 2),
                "promedio_proteinas_g_dia": promedio_proteinas_dia,
                "total_grasas_g": round(total_grasas_semana, 2),
                "promedio_grasas_g_dia": promedio_grasas_dia,
                "total_carbohidratos_g": round(total_carbohidratos_semana, 2),
                "promedio_carbohidratos_g_dia": promedio_carbohidratos_dia,
            }
        },
        "analisisDiario": dict(analisis_diario_filtrado)
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
    # 'current_user' es el usuario del token. Necesitamos 'db_user' para operaciones de BD.
    db_user = db.query(User).filter(User.id == current_user.id).first()
    if not db_user:
        # Esto no debería ocurrir si el token es válido y el usuario existe.
        raise HTTPException(status_code=404, detail="Usuario no encontrado en la base de datos")

    user_favorites_list = []
    if db_user.recetas_favoritas: # Cargar desde el campo de la instancia de BD
        try:
            parsed_favorites = json.loads(db_user.recetas_favoritas)
            if isinstance(parsed_favorites, list):
                user_favorites_list = parsed_favorites
            else:
                print(f"Usuario: {current_user.username}. ADVERTENCIA: 'recetas_favoritas' no es una lista después de JSON.parse. Contenido: {parsed_favorites}")
                user_favorites_list = [] 
        except json.JSONDecodeError:
            print(f"Usuario: {current_user.username}. Error al decodificar JSON de recetas_favoritas. Contenido: {db_user.recetas_favoritas}")
            user_favorites_list = []

    print(f"Usuario: {current_user.username}. Recetas favoritas ANTES de eliminar (cargadas de db_user.recetas_favoritas): {user_favorites_list}")

    # El payload 'recipe' es un diccionario, esperamos que tenga 'recipe_url'
    url_a_eliminar = recipe.get("recipe_url")
    print(f"Usuario: {current_user.username}. Receta a eliminar (URL del payload): {url_a_eliminar}")

    recetas_actualizadas_python_list = list(user_favorites_list) # Copiar para modificar

    if url_a_eliminar:
        original_count = len(user_favorites_list)
        # Filtrar la lista usando la clave correcta "recipe_url"
        # Y asegurarse que cada elemento 'r' es un diccionario antes de llamar a .get()
        recetas_actualizadas_python_list = [
            r for r in user_favorites_list 
            if not (isinstance(r, dict) and r.get("recipe_url") == url_a_eliminar)
            ]
            
        if len(recetas_actualizadas_python_list) < original_count:
            print(f"Usuario: {current_user.username}. Receta con URL '{url_a_eliminar}' encontrada y eliminada de la lista local.")
        else:
            print(f"Usuario: {current_user.username}. ADVERTENCIA: No se encontró ninguna receta con URL '{url_a_eliminar}' en la lista actual de favoritos para eliminar.")
            current_urls_in_list = [fav.get('recipe_url') for fav in user_favorites_list if isinstance(fav, dict) and fav.get('recipe_url')]
            print(f"Usuario: {current_user.username}. URLs actualmente en la lista de favoritos: {current_urls_in_list}")

        # Actualizar el campo de db_user con la nueva lista (convertida a JSON string)
        print(f"[DEBUG] Usuario: {current_user.username}. Lista de favoritos (Python list) ANTES de json.dumps y guardar en db_user: {recetas_actualizadas_python_list}")
        db_user.recetas_favoritas = json.dumps(recetas_actualizadas_python_list)
        print(f"[DEBUG] Usuario: {current_user.username}. db_user.recetas_favoritas (JSON string) DESPUÉS de json.dumps, lista para guardar: {db_user.recetas_favoritas}")
    
    else:
        print(f"Usuario: {current_user.username}. ADVERTENCIA: No se proporcionó recipe_url en el payload para eliminar. No se realizarán cambios en los favoritos.")
        # Si no hay URL a eliminar, db_user.recetas_favoritas ya tiene el valor correcto (el original JSON string o el JSON string de la lista vacía si era None)
        # Si db_user.recetas_favoritas era None y se inicializó user_favorites_list como [], 
        # es bueno asegurarse que se guarda como '[]' en la DB.
        if db_user.recetas_favoritas is None: # Si originalmente era None
             db_user.recetas_favoritas = json.dumps([])


    try:
        db.add(db_user) # Asegura que la instancia db_user (con sus cambios) está en la sesión.
        db.commit()
        db.refresh(db_user) 
        print(f"Usuario: {current_user.username}. Favoritos guardados correctamente en la DB. Contenido de db_user.recetas_favoritas después de commit: {db_user.recetas_favoritas}")
        # El mensaje de retorno debe ser consistente con lo que espera el frontend.
        # Si la receta no se encontró, el estado de la UI ya se actualizó optimisticamente.
        # El backend simplemente procesó la solicitud.
        return {"message": "Operación de eliminación de favoritos procesada."}

    except Exception as e:
        db.rollback() 
        print(f"Usuario: {current_user.username}. Error al guardar en DB: {e}")
        raise HTTPException(status_code=500, detail="Error al guardar cambios en la base de datos.")

# Modelo Pydantic para el payload del request de menú recomendado
class RecommendedMenuRequestPayload(BaseModel):
    target_calories: Optional[int] = Field(None, gt=0) # Opcional, y si se provee, debe ser > 0
    # Podrías añadir otros campos aquí si son necesarios en el futuro


# Endpoint para generar menú semanal recomendado
@app.post("/generar-menu-recomendado", response_model=WeeklyMenuWithOptionsResponse)
async def generar_menu_recomendado_endpoint(
    payload: RecommendedMenuRequestPayload, # Usar el nuevo modelo para el payload
    db: Session = Depends(get_db), 
    current_user: User = Depends(auth.get_current_user) # Asegurar que es models.User
):
    try:
        # La importación diferida puede quedarse o moverse al inicio del archivo si prefieres
        # from app.services.menu_generator import generate_recommended_weekly_menu 
        
        default_meals = ["desayuno", "comida", "cena"]
        default_meal_ratios = {"desayuno": 0.30, "comida": 0.40, "cena": 0.30}
        num_options_per_meal = 3

        print(f"Payload recibido en /generar-menu-recomendado: {payload}") # Log para ver qué llega

        menu_dict = generate_recommended_weekly_menu(
            user=current_user,
            db_session=db,
            meals_config=default_meals,
            ratios_config=default_meal_ratios,
            num_options=num_options_per_meal,
            target_calories_override=payload.target_calories # Pasar las calorías del payload
        )
        return menu_dict
    except ValueError as ve:
        print(f"ValueError en generar_menu_recomendado_endpoint: {ve}")
        raise HTTPException(status_code=400, detail=str(ve))
    except Exception as e:
        print(f"Error inesperado generando menú recomendado: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error interno del servidor al generar el menú recomendado.")

