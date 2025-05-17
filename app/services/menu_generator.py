from app.models.MenuRequest import MenuRequest
from typing import Dict, List, Optional, Any
from app.services.edamam_service import fetch_recipes_from_edamam, EDAMAM_MEAL_TYPE_MAP
from app.schemas import RecipeOption, MealSlotWithOptions, DayMealsWithOptions # Ajusta la ruta
import json

def _create_recipe_option_from_data(recipe_data: Dict[str, Any]) -> Optional[RecipeOption]:
    """Helper para crear un objeto RecipeOption desde los datos de Edamam."""
    try:
        print(f"--- DEBUG: _create_recipe_option_from_data ---") # Log de entrada
        print(f"Raw recipe_data LABEL: {recipe_data.get('label')}")

        total_calories_recipe = float(recipe_data.get("calories", 0))
        servings = float(recipe_data.get("yield", 1.0))
        if servings <= 0: servings = 1.0 # Evitar división por cero

        calories_per_serving = total_calories_recipe / servings

        # Extraer macronutrientes (por ración)
        protein_g_per_serving: Optional[float] = None
        fat_g_per_serving: Optional[float] = None
        carbs_g_per_serving: Optional[float] = None
        
        total_nutrients_data = recipe_data.get("totalNutrients")
        
        # Loguear el objeto totalNutrients completo para esta receta
        print(f"TotalNutrients from Edamam for {recipe_data.get('label')}: {json.dumps(total_nutrients_data, indent=2)}")

        if isinstance(total_nutrients_data, dict):
            # Proteínas
            protein_data = total_nutrients_data.get("PROCNT")
            if isinstance(protein_data, dict) and "quantity" in protein_data:
                protein_g_per_serving = round(float(protein_data["quantity"]) / servings, 2)
                print(f"  PROTEIN_G per serving: {protein_g_per_serving}")
            else:
                print(f"  PROCNT data missing or malformed: {protein_data}")

            # Grasas
            fat_data = total_nutrients_data.get("FAT")
            if isinstance(fat_data, dict) and "quantity" in fat_data:
                fat_g_per_serving = round(float(fat_data["quantity"]) / servings, 2)
                print(f"  FAT_G per serving: {fat_g_per_serving}")
            else:
                print(f"  FAT data missing or malformed: {fat_data}")

            # Carbohidratos
            carbs_data = total_nutrients_data.get("CHOCDF") # Carbohidratos por diferencia
            if isinstance(carbs_data, dict) and "quantity" in carbs_data:
                carbs_g_per_serving = round(float(carbs_data["quantity"]) / servings, 2)
                print(f"  CARBS_G per serving: {carbs_g_per_serving}")
            else:
                print(f"  CHOCDF data missing or malformed: {carbs_data}")
        else:
            print(f"  totalNutrients_data is not a dict or is missing for {recipe_data.get('label')}")
        
        # El campo total_nutrients_raw almacenará el objeto completo tal como viene de Edamam,
        # pero correspondiente a la receta completa, no por porción.
        # Si se quiere por porción, habría que procesar cada nutriente dividiéndolo por 'servings'.
        # Por ahora, guardamos el original para flexibilidad.
        raw_total_nutrients_for_option = recipe_data.get("totalNutrients")

        created_option = RecipeOption(
            label=str(recipe_data["label"]),
            image=recipe_data.get("image"),
            url=str(recipe_data["url"]),
            ingredients=[str(line) for line in recipe_data.get("ingredientLines", [])],
            calories=round(calories_per_serving, 2),
            protein_g=protein_g_per_serving,
            fat_g=fat_g_per_serving,
            carbs_g=carbs_g_per_serving,
            total_nutrients_raw=raw_total_nutrients_for_option 
            # yield_servings=servings, # Descomentar si está en RecipeOption
            # totalTime=recipe_data.get("totalTime"), # Descomentar si está en RecipeOption
        )
        print(f"Created RecipeOption: P={created_option.protein_g}, F={created_option.fat_g}, C={created_option.carbs_g}")
        print(f"--- END DEBUG: _create_recipe_option_from_data for {recipe_data.get('label')} ---")
        return created_option

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
    user: Any, 
    db_session: Any, 
    meals_config: List[str], 
    ratios_config: Dict[str, float], 
    num_options: int = 3,
    target_calories_override: Optional[int] = None # Nuevo parámetro
) -> Dict[str, DayMealsWithOptions]:
    
    daily_target_calories_final = 0

    # Decidir las calorías objetivo
    if target_calories_override is not None and target_calories_override > 0:
        daily_target_calories_final = target_calories_override
        print(f"Usando target_calories_override del payload: {daily_target_calories_final} kcal para usuario {user.username}")
    else:
        # Si no hay override, calcular basado en el perfil del usuario
        if not all([user.bmr, user.actividad, user.objetivo]):
            # Log de advertencia en lugar de error crítico si faltan datos, para intentar generar algo genérico si es posible
            # o el frontend debería validar esto antes.
            print(f"Advertencia: Faltan datos del perfil (BMR, actividad, objetivo) para el usuario {user.username}. Se usará un valor por defecto de 2000 kcal.")
            daily_target_calories_final = 2000 # Valor por defecto si faltan datos del perfil
        else:
            activity_factors = {
                "sedentario": 1.2,
                "ligero": 1.375,
                "moderado": 1.55,
                "intenso": 1.725,
                "muy intenso": 1.9 
            }
            activity_factor = activity_factors.get(user.actividad.lower(), 1.4) # Default razonable
            
            tdee = user.bmr * activity_factor
            calorie_adjustment = 0
            if user.objetivo.lower() == "bajar de peso":
                calorie_adjustment = -500 # Déficit estándar
            elif user.objetivo.lower() == "subir de peso":
                calorie_adjustment = 300  # Superávit moderado
            
            calculated_target_calories = round(tdee + calorie_adjustment)
            # Asegurar un mínimo calórico sensato
            daily_target_calories_final = max(calculated_target_calories, 1200) 

            print(f"Usuario: {user.username}, BMR: {user.bmr}, Actividad: {user.actividad} (factor: {activity_factor}), Objetivo: {user.objetivo}")
            print(f"TDEE Calculado: {tdee:.0f} kcal, Ajuste por objetivo: {calorie_adjustment} kcal")
    
    print(f"Calorías Diarias Objetivo Finales para el menú: {daily_target_calories_final} kcal para usuario {user.username}")

    # 2. Obtener y procesar recetas favoritas para palabras clave (lógica existente)
    favorite_keywords = []
    if user.recetas_favoritas:
        try:
            # Asumiendo que recetas_favoritas es una lista de objetos ya parseada si viene de la DB, o un string JSON
            fav_recipes_data = user.recetas_favoritas
            if isinstance(user.recetas_favoritas, str):
                fav_recipes_data = json.loads(user.recetas_favoritas)
            
            if isinstance(fav_recipes_data, list):
                for recipe in fav_recipes_data:
                    if isinstance(recipe, dict) and recipe.get("label"):
                        keywords = recipe.get("label").split()[:3]
                        favorite_keywords.extend([kw.lower() for kw in keywords if len(kw) > 3])
                favorite_keywords = list(set(favorite_keywords))[:5]
                print(f"Palabras clave de favoritos para {user.username}: {favorite_keywords}")
        except Exception as e:
            print(f"Error al procesar recetas favoritas para keywords ({user.username}): {e}")
            favorite_keywords = []
    
    base_search_params = {
        "diet_filter": None, 
        "health_labels": [], 
        "excluded_items": "",
    }

    dias_semana = ["lunes", "martes", "miercoles", "jueves", "viernes", "sabado", "domingo"]
    menu_semanal_con_opciones: Dict[str, DayMealsWithOptions] = {}

    if abs(sum(ratios_config.values()) - 1.0) > 0.01:
        # Esto debería validarse antes, pero es una doble comprobación
        raise ValueError("La suma de las proporciones calóricas para comidas debe ser 1.0")

    for dia_nombre in dias_semana:
        current_day_obj = DayMealsWithOptions()
        menu_semanal_con_opciones[dia_nombre] = current_day_obj

        for meal_name_key in meals_config:
            meal_ratio = ratios_config.get(meal_name_key)
            if meal_ratio is None: 
                print(f"Advertencia: No se encontró ratio para {meal_name_key}. Se omitirá.")
                continue

            # Usar las calorías diarias finales para calcular las calorías de esta comida
            target_cal_meal = int(daily_target_calories_final * meal_ratio)
            margin = 0.20 
            min_cal = int(target_cal_meal * (1 - margin))
            max_cal = int(target_cal_meal * (1 + margin))
            if min_cal < 50: min_cal = 50
            if max_cal <= min_cal: max_cal = min_cal + 150

            calorie_range = f"{min_cal}-{max_cal}"
            edamam_type = EDAMAM_MEAL_TYPE_MAP.get(meal_name_key.lower())
            
            current_keywords_q = " ".join(favorite_keywords) if favorite_keywords else None
            
            max_attempts = 2 # Reducido para recomendaciones más rápidas
            all_valid_recipes = []
            seen_urls = set()

            # Lógica de búsqueda (similar a la original, pero más concisa para el ejemplo)
            # Intento 1: Con keywords de favoritos (si existen)
            if current_keywords_q:
                # print(f"Buscando para {dia_nombre}-{meal_name_key} con keywords: '{current_keywords_q}', rango: {calorie_range}")
                raw_recipes_data = fetch_recipes_from_edamam(
                    calorie_range_str=calorie_range, num_recipes_to_get=num_options * 2, 
                    diet_filter=base_search_params["diet_filter"], health_labels=base_search_params["health_labels"],
                    excluded_items=base_search_params["excluded_items"], included_keywords_q=current_keywords_q,
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
            
            # Intento 2: Búsqueda general si no se completaron opciones o no había keywords
            if len(all_valid_recipes) < num_options:
                needed_more = num_options - len(all_valid_recipes)
                # print(f"Recbuscando para {dia_nombre}-{meal_name_key} sin keywords o para completar {needed_more} opciones más.")
                raw_recipes_data = fetch_recipes_from_edamam(
                    calorie_range_str=calorie_range, num_recipes_to_get=needed_more * 2,
                    diet_filter=base_search_params["diet_filter"], health_labels=base_search_params["health_labels"],
                    excluded_items=base_search_params["excluded_items"], included_keywords_q=None,
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

            current_meal_slot_obj = MealSlotWithOptions()
            if all_valid_recipes:
                current_meal_slot_obj.options = all_valid_recipes[:num_options]
            else:
                current_meal_slot_obj.error = f"No recetas: {min_cal}-{max_cal} kcal para '{meal_name_key}'"
            
            setattr(current_day_obj, meal_name_key, current_meal_slot_obj)
            # print(f"-> {dia_nombre}, {meal_name_key}: {len(current_meal_slot_obj.options if current_meal_slot_obj.options else [])} opciones. Rango cal: {calorie_range}")

    return menu_semanal_con_opciones

