"""
Estimación de coste antes de lanzar una búsqueda.

Tarifas Google Places API (New) — referencia 2024:
  - Text Search (Pro):      $0.032 / req
  - Place Details (Pro):    $0.017 / req
  - Place Details (Basic):  $0.005 / req

El código original usa Text Search con FieldMask amplio (Pro). Para cada
ciudad y query lanza hasta 3 páginas. Cada página = 1 Text Search request
con paginación token. Después, el extractor de emails NO usa Place Details
(extrae de la web), así que el coste de Places se limita al Text Search.

Conversión a EUR: ~0.92 EUR/USD (aproximado, configurable).

Estos números son una estimación conservadora — el coste real puede variar
si Google cambia tarifas o si una página devuelve menos resultados de los
esperados (no se gasta el cupo entero).
"""

# Precios USD por request (Google Places API New, "Pro" tier)
COST_TEXT_SEARCH_USD = 0.032

# Tipo de cambio USD->EUR
USD_TO_EUR = 0.92


def estimate_cost(num_ciudades: int, num_queries: int, max_paginas: int) -> dict:
    """
    Devuelve un dict con la estimación de la búsqueda.

      - text_searches: número total de peticiones Text Search
      - cost_usd: estimación en USD
      - cost_eur: estimación en EUR
      - warnings: lista de strings con avisos relevantes

    Nota: el número de páginas real depende de cuántos resultados devuelva
    Google. Esta función asume el peor caso (todas las queries devuelven
    suficientes resultados para llenar max_paginas).
    """
    text_searches = num_ciudades * num_queries * max_paginas
    cost_usd = text_searches * COST_TEXT_SEARCH_USD
    cost_eur = cost_usd * USD_TO_EUR

    warnings = []
    if cost_eur > 50:
        warnings.append(
            f"Esta búsqueda puede costar hasta ~{cost_eur:.0f} € en API. "
            "Considera reducir el ámbito o bajar las páginas por query."
        )
    elif cost_eur > 20:
        warnings.append(
            f"Coste estimado: ~{cost_eur:.0f} €. Revisa que el ámbito sea correcto."
        )

    if text_searches > 1000:
        warnings.append(
            f"Vas a hacer {text_searches:,} peticiones a Google Places. "
            "La ejecución tardará al menos "
            f"~{text_searches * 0.5 / 60:.0f} minutos por el rate limit."
        )

    return {
        "text_searches": text_searches,
        "cost_usd": round(cost_usd, 2),
        "cost_eur": round(cost_eur, 2),
        "warnings": warnings,
    }
