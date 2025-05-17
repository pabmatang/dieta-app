import os
import requests
from dotenv import load_dotenv
from app.models.MenuRequest import MenuRequest
from typing import List, Dict, Optional, Any # Any para el retorno de datos de receta


load_dotenv()

APP_ID = os.getenv("EDAMAM_APP_ID")
APP_KEY = os.getenv("EDAMAM_APP_KEY")

# Edamam mealType values: Breakfast, Lunch, Dinner, Snack, Teatime
EDAMAM_MEAL_TYPE_MAP = {
    "desayuno": "Breakfast",
    "comida": "Lunch",
    "almuerzo": "Lunch", # Mapeo adicional
    "cena": "Dinner",
    "merienda": "Snack",
    # Añade otros mapeos según los uses
}
def fetch_recipes_from_edamam(
    calorie_range_str: str, # Ej: "500-700"
    num_recipes_to_get: int,
    diet_filter: Optional[str] = None,
    health_labels: Optional[List[str]] = None,
    excluded_items: Optional[List[str]] = None,
    included_keywords_q: Optional[List[str]] = None, # 'q' para búsqueda de texto
    edamam_meal_type: Optional[str] = None # Directamente el valor de Edamam (Breakfast, Lunch, etc.)
) -> List[Dict[str, Any]]:
    """
    Obtiene hasta `num_recipes_to_get` recetas de la API de Edamam basadas en los criterios.
    Devuelve una lista de diccionarios, donde cada diccionario es un objeto 'recipe' de Edamam.
    """
    if not APP_ID or not APP_KEY or APP_ID == "YOUR_EDAMAM_APP_ID": # Comprueba placeholders
        print("ERROR CRÍTICO: Credenciales de Edamam (APP_ID/APP_KEY) no configuradas.")
        return []

    base_url = "https://api.edamam.com/api/recipes/v2"
    
    params: Dict[str, Any] = {
        "type": "public",
        "app_id": APP_ID,
        "app_key": APP_KEY,
        "calories": calorie_range_str,
        "random": "true", # Para obtener variedad si se hacen múltiples llamadas idénticas
    }

    # Añadir parámetros opcionales
    if diet_filter:
        params["diet"] = diet_filter
    if health_labels: # requests maneja listas como parámetros repetidos
        params["health"] = health_labels
    if excluded_items:
        params["excluded"] = excluded_items
    if included_keywords_q: # Para el parámetro 'q' de Edamam (búsqueda de texto)
        params["q"] = " ".join(included_keywords_q)
    if edamam_meal_type:
        params["mealType"] = edamam_meal_type

    # Campos específicos a solicitar a Edamam para optimizar la respuesta
    # Ajusta según los campos que necesites para RecipeOption
    fields = ["uri", "label", "image", "source", "url", "yield", 
              "ingredientLines", "calories", "totalTime", "mealType", "totalNutrients"]
    params["field"] = fields

    # Edamam devuelve un número de 'hits' por página (por defecto 20).
    # No hay un parámetro 'count' directo para limitar el número exacto de resultados en la v2 como en v1.
    # Se piden los resultados y se procesan los primeros N del lado del cliente.
    # No se necesita `from` y `to` si solo se toma la primera página de resultados.

    # Añadir el encabezado de autenticación si es necesario
    headers = {
        "Edamam-Account-User": "TFG"  # Sustituye 'tu_usuario' por el valor correcto
    }
    
    print(f"Solicitando a Edamam con params: {params}") # Para depuración

    try:
        response = requests.get(base_url, params=params, headers=headers, timeout=20) # Timeout aumentado
        response.raise_for_status() # Lanza un HTTPError para respuestas 4xx/5xx
        data = response.json()
        
        # Extraer los datos de las recetas de los "hits"
        recipes_data = [hit.get("recipe") for hit in data.get("hits", []) if hit.get("recipe")]
        
        # Devolver hasta num_recipes_to_get (o menos si la API devuelve menos)
        return recipes_data[:num_recipes_to_get]

    except requests.exceptions.Timeout:
        print(f"Error: Timeout en la solicitud a Edamam API. URL: {response.url if 'response' in locals() else base_url}")
        return []
    except requests.exceptions.HTTPError as http_err:
        print(f"Error HTTP de Edamam API: {http_err}. URL: {http_err.request.url}")
        print(f"Response text: {http_err.response.text[:500] if http_err.response else 'N/A'}")
        return []
    except requests.exceptions.RequestException as req_err:
        print(f"Error en la solicitud a Edamam API: {req_err}")
        return []
    except ValueError as json_err: # Error al decodificar JSON
        print(f"Error decodificando JSON de Edamam: {json_err}. Text: {response.text[:200] if 'response' in locals() else 'N/A'}")
        return []

# La función `generate_menu` original que tenías es conceptualmente lo que
# ahora hará `menu_generator.py` al orquestar las llamadas a `Workspace_recipes_from_edamam`.
# Por lo tanto, esta función `generate_menu` ya no es necesaria aquí en esta forma.
