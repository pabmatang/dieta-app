from app.models.MenuRequest import MenuRequest
from typing import Dict, List, Optional, Any
from app.services.edamam_service import fetch_recipes_from_edamam, EDAMAM_MEAL_TYPE_MAP
from app.schemas import RecipeOption, MealSlotWithOptions, DayMealsWithOptions # Ajusta la ruta
import json

def _create_recipe_option_from_data(recipe_data: Dict[str, Any]) -> Optional[RecipeOption]:
    """Helper para crear un objeto RecipeOption desde los datos de Edamam."""
    try:
        # Edamam devuelve 'yield' para las raciones y 'calories' totales de la receta.
        total_calories = float(recipe_data.get("calories", 0))
        servings = float(recipe_data.get("yield", 1.0))
        if servings <= 0: servings = 1.0 # Evitar división por cero

        calories_per_serving = total_calories / servings

        return RecipeOption(
            label=str(recipe_data["label"]),
            image=recipe_data.get("image"),
            url=str(recipe_data["url"]),
            ingredients=[str(line) for line in recipe_data.get("ingredientLines", [])],
            calories=round(calories_per_serving, 2),
            # yield_servings=servings, # Descomentar si está en RecipeOption
            # totalTime=recipe_data.get("totalTime"), # Descomentar si está en RecipeOption
        )
    except (KeyError, ValueError, TypeError) as e:
        print(f"Error al procesar datos de receta para RecipeOption: {e}. Datos: {recipe_data}")
        return None

def generate_weekly_menu(base_request: MenuRequest) -> Dict[str, DayMealsWithOptions]:
    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    menu_semanal_con_opciones: Dict[str, DayMealsWithOptions] = {}

    if abs(sum(base_request.meal_ratios.values()) - 1.0) > 0.01:
        raise ValueError("La suma de las proporciones calóricas debe ser 1.0")

    # Calorías totales diarias base
    daily_calories = 2000
    if isinstance(base_request.calories, int):
        daily_calories = base_request.calories
    elif isinstance(base_request.calories, str) and base_request.calories.isdigit():
        daily_calories = int(base_request.calories)

    for dia_nombre in dias_semana:
        current_day_obj = DayMealsWithOptions()
        menu_semanal_con_opciones[dia_nombre] = current_day_obj

        for meal_name_key in base_request.meals:
            meal_ratio = base_request.meal_ratios.get(meal_name_key)
            if meal_ratio is None:
                continue

            target_cal = int(daily_calories * meal_ratio)
            margin = 0.15
            min_cal = int(target_cal * (1 - margin))
            max_cal = int(target_cal * (1 + margin))
            if min_cal < 50:
                min_cal = 50
            if max_cal <= min_cal:
                max_cal = min_cal + 100

            calorie_range = f"{min_cal}-{max_cal}"
            edamam_type = EDAMAM_MEAL_TYPE_MAP.get(meal_name_key.lower())

            # Intentos para encontrar recetas válidas
            max_attempts = 50
            all_valid_recipes = []
            seen_urls = set()

            for _ in range(max_attempts):
                raw_recipes_data = fetch_recipes_from_edamam(
                    calorie_range_str=calorie_range,
                    num_recipes_to_get=base_request.num_options_per_meal * 2,
                    diet_filter=base_request.diet,
                    health_labels=base_request.health,
                    excluded_items=base_request.excluded,
                    included_keywords_q=base_request.included,
                    edamam_meal_type=edamam_type
                )

                if not raw_recipes_data:
                    continue

                for recipe_data in raw_recipes_data:
                    if len(all_valid_recipes) >= base_request.num_options_per_meal:
                        break

                    url = recipe_data.get("url")
                    if url in seen_urls:
                        continue

                    option = _create_recipe_option_from_data(recipe_data)
                    if option and min_cal <= option.calories <= max_cal:
                        all_valid_recipes.append(option)
                        seen_urls.add(url)

                if len(all_valid_recipes) >= base_request.num_options_per_meal:
                    break

            current_meal_slot_obj = MealSlotWithOptions()
            if all_valid_recipes:
                current_meal_slot_obj.options = all_valid_recipes[:base_request.num_options_per_meal]
            else:
                current_meal_slot_obj.error = f"No se encontraron recetas dentro de {min_cal}-{max_cal} kcal para '{meal_name_key}'"

            setattr(current_day_obj, meal_name_key, current_meal_slot_obj)

    return menu_semanal_con_opciones

# Nueva función para generar menú recomendado
def generate_recommended_weekly_menu(
    user: Any, # El objeto User autenticado, contiene BMR, actividad, objetivo, recetas_favoritas
    db_session: Any, # Sesión de BD, por si se necesita
    meals_config: List[str], # Ej: ["desayuno", "comida", "cena"]
    ratios_config: Dict[str, float], # Ej: {"desayuno": 0.3, ...}
    num_options: int = 3
) -> Dict[str, DayMealsWithOptions]:
    
    # 1. Calcular Calorías Diarias Objetivo (TDEE ajustado)
    if not all([user.bmr, user.actividad, user.objetivo, user.genero, user.peso, user.altura, user.edad]):
        raise ValueError("Faltan datos del perfil del usuario para calcular calorías (BMR, actividad, objetivo, etc.)")

    activity_factors = {
        "sedentario": 1.2,
        "ligero": 1.375,
        "moderado": 1.55,
        "intenso": 1.725,
        "muy intenso": 1.9 # Añadido por si acaso
    }
    activity_factor = activity_factors.get(user.actividad.lower(), 1.4) # Default a 1.4 si no se encuentra
    
    # Recalcular BMR por si los datos base (peso, altura, edad, genero) han cambiado y BMR no está actualizado.
    # Esto es una salvaguarda, idealmente el BMR en user.bmr ya está correcto.
    try:
        # Necesitamos la función calcular_bmr, asumamos que está disponible en este contexto
        # Si no, necesitaríamos importarla o moverla a un utils.
        # from app.main import calcular_bmr # OJO: Evitar importaciones circulares si es el caso
        # Por ahora, asumimos que user.bmr es suficientemente actual o que esta función no se define aquí.
        # Para este ejemplo, usaré el user.bmr directamente.
        # Si es necesario, esta lógica de cálculo de BMR puede ser más robusta.
        pass 
    except Exception as e:
        print(f"Advertencia: No se pudo recalcular BMR, se usará el almacenado. Error: {e}")

    tdee = user.bmr * activity_factor

    calorie_adjustment = 0
    if user.objetivo.lower() == "bajar de peso":
        calorie_adjustment = -500
    elif user.objetivo.lower() == "subir de peso":
        calorie_adjustment = 300 # Un superávit más conservador para empezar
    
    daily_target_calories = round(tdee + calorie_adjustment)
    if daily_target_calories < 1200: # Mínimo calórico
        daily_target_calories = 1200

    print(f"Usuario: {user.username}, BMR: {user.bmr}, Actividad: {user.actividad} (factor: {activity_factor}), Objetivo: {user.objetivo}")
    print(f"TDEE Calculado: {tdee:.0f} kcal, Ajuste por objetivo: {calorie_adjustment} kcal")
    print(f"Calorías Diarias Objetivo para el menú: {daily_target_calories} kcal")

    # 2. Obtener y procesar recetas favoritas para palabras clave
    favorite_keywords = []
    if user.recetas_favoritas:
        try:
            fav_recipes = json.loads(user.recetas_favoritas) if isinstance(user.recetas_favoritas, str) else user.recetas_favoritas
            if isinstance(fav_recipes, list):
                for recipe in fav_recipes:
                    if isinstance(recipe, dict) and recipe.get("label"):
                        # Tomar las primeras 2-3 palabras significativas del nombre como keyword
                        keywords = recipe.get("label").split()[:3]
                        favorite_keywords.extend([kw.lower() for kw in keywords if len(kw) > 3]) # Palabras > 3 letras
                # Eliminar duplicados y tomar un número limitado de keywords para no saturar la búsqueda
                favorite_keywords = list(set(favorite_keywords))[:5] 
                print(f"Palabras clave de favoritos: {favorite_keywords}")
        except Exception as e:
            print(f"Error al procesar recetas favoritas para keywords: {e}")
            favorite_keywords = []
    
    # Parámetros para la búsqueda de Edamam (sin dieta específica, sin exclusiones por ahora)
    # Se podrían añadir desde el perfil si existen (ej. preferencias de dieta guardadas)
    base_search_params = {
        "diet_filter": None, # Podría ser user.preferencia_dieta si existe
        "health_labels": [], # Podría ser user.preferencias_salud si existen
        "excluded_items": "",
    }

    # Reutilizar la lógica de generate_weekly_menu pero con calorías y keywords dinámicas
    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    menu_semanal_con_opciones: Dict[str, DayMealsWithOptions] = {}

    if abs(sum(ratios_config.values()) - 1.0) > 0.01:
        raise ValueError("La suma de las proporciones calóricas para comidas debe ser 1.0")

    for dia_nombre in dias_semana:
        current_day_obj = DayMealsWithOptions()
        menu_semanal_con_opciones[dia_nombre] = current_day_obj

        for meal_name_key in meals_config:
            meal_ratio = ratios_config.get(meal_name_key)
            if meal_ratio is None: continue

            target_cal_meal = int(daily_target_calories * meal_ratio)
            margin = 0.20 # Un margen un poco más amplio para recomendaciones
            min_cal = int(target_cal_meal * (1 - margin))
            max_cal = int(target_cal_meal * (1 + margin))
            if min_cal < 50: min_cal = 50
            if max_cal <= min_cal: max_cal = min_cal + 150 # Asegurar rango

            calorie_range = f"{min_cal}-{max_cal}"
            edamam_type = EDAMAM_MEAL_TYPE_MAP.get(meal_name_key.lower())
            
            # Combinar keywords de favoritos con otras (si las hubiera)
            # Para la primera versión, solo usamos las de favoritos.
            # Se podría añadir una lógica para que el usuario ingrese keywords adicionales.
            current_keywords_q = " ".join(favorite_keywords) if favorite_keywords else None
            
            # Lógica de búsqueda (similar a generate_weekly_menu)
            max_attempts = 3 # Menos intentos para recomendaciones para no tardar demasiado
            all_valid_recipes = []
            seen_urls = set()

            # Primero, intentar con keywords si existen
            if current_keywords_q:
                for _ in range(max_attempts):
                    raw_recipes_data = fetch_recipes_from_edamam(
                        calorie_range_str=calorie_range,
                        num_recipes_to_get=num_options * 2, # Pedir más para filtrar
                        diet_filter=base_search_params["diet_filter"],
                        health_labels=base_search_params["health_labels"],
                        excluded_items=base_search_params["excluded_items"],
                        included_keywords_q=current_keywords_q,
                        edamam_meal_type=edamam_type
                    )
                    if raw_recipes_data:
                        for recipe_data in raw_recipes_data:
                            if len(all_valid_recipes) >= num_options: break
                            url = recipe_data.get("url")
                            if url in seen_urls: continue
                            option = _create_recipe_option_from_data(recipe_data)
                            if option and min_cal <= option.calories <= max_cal:
                                all_valid_recipes.append(option)
                                seen_urls.add(url)
                        if len(all_valid_recipes) >= num_options: break
            
            # Si no se encontraron suficientes con keywords, o no había keywords, buscar de forma general
            if len(all_valid_recipes) < num_options:
                print(f"Recbuscando para {dia_nombre}-{meal_name_key} sin keywords específicas o completando opciones.")
                needed_more = num_options - len(all_valid_recipes)
                for _ in range(max_attempts):
                    raw_recipes_data = fetch_recipes_from_edamam(
                        calorie_range_str=calorie_range,
                        num_recipes_to_get=needed_more * 2,
                        diet_filter=base_search_params["diet_filter"],
                        health_labels=base_search_params["health_labels"],
                        excluded_items=base_search_params["excluded_items"],
                        included_keywords_q=None, # Búsqueda general
                        edamam_meal_type=edamam_type
                    )
                    if raw_recipes_data:
                        for recipe_data in raw_recipes_data:
                            if len(all_valid_recipes) >= num_options: break # Ya completamos el total necesario
                            url = recipe_data.get("url")
                            if url in seen_urls: continue
                            option = _create_recipe_option_from_data(recipe_data)
                            if option and min_cal <= option.calories <= max_cal:
                                # Asegurarse de no añadir más de `needed_more` en esta etapa
                                if len(all_valid_recipes) < num_options:
                                    all_valid_recipes.append(option)
                                    seen_urls.add(url)
                            if len(all_valid_recipes) >= num_options: break 
                    if len(all_valid_recipes) >= num_options: break

            current_meal_slot_obj = MealSlotWithOptions()
            if all_valid_recipes:
                current_meal_slot_obj.options = all_valid_recipes[:num_options]
            else:
                current_meal_slot_obj.error = f"No se encontraron recetas dentro de {min_cal}-{max_cal} kcal para '{meal_name_key}' (Recomendado)"
            
            setattr(current_day_obj, meal_name_key, current_meal_slot_obj)
            print(f"-> {dia_nombre}, {meal_name_key}: {len(current_meal_slot_obj.options if current_meal_slot_obj.options else [])} opciones. Rango cal: {calorie_range}")

    return menu_semanal_con_opciones
