from pydantic import BaseModel, Field , validator
from typing import List, Optional, Union, Dict


class MenuRequest(BaseModel):
    calories: Optional[Union[str, int]] = None
    diet: Optional[str] = Field(None, description="Tipo de dieta")
    health: Optional[List[str]] = Field(default_factory=list, description="Etiquetas de salud (ej. ['vegan', 'gluten-free'])")
    excluded: Optional[List[str]] = Field(default_factory=list, description="Ingredientes a excluir")
    included: Optional[List[str]] = Field(default_factory=list, description="Ingredientes a incluir (keywords para búsqueda)")
    
    # Este campo es más para llamadas internas si se descompone la lógica.
    # No es típicamente parte de la solicitud de alto nivel para el menú semanal completo.
    # meal_type: Optional[str] = Field(None, description="Tipo de comida para una búsqueda específica")

    # Campos para la generación del menú semanal completo
    num_options_per_meal: int = Field(default=3, ge=1, le=4, description="Número de opciones por comida")
    meals: Optional[List[str]] = Field(default_factory=lambda: ["desayuno", "comida", "cena"], description="Tipos de comida en el día (ej. ['desayuno', 'comida', 'cena'])")
    meal_ratios: Optional[Dict[str, float]] = Field(
        default_factory=lambda: {"desayuno": 0.3, "comida": 0.4, "cena": 0.3},
        description="Proporción calórica para cada comida (ej. {'desayuno': 0.3, ...}). Debe sumar 1.0"
    )



    class Config:
        schema_extra = {
            "example": {
                "calories": "2000", # O 2200
                "diet": "balanced",
                "health": ["vegetarian"],
                "excluded": ["pork"],
                "included": ["quinoa", "avocado"],
                "num_options_per_meal": 2,
                "meals": ["desayuno", "comida", "cena"],
                "meal_ratios": {"desayuno": 0.25, "comida": 0.45, "cena": 0.30}
            }
        }
        extra = "ignore" # Ignorar campos extra en el request
