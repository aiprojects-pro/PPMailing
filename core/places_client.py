"""
Cliente de bajo nivel para Google Places API (Text Search v1).

NO conoce los segmentos: recibe queries y devuelve resultados crudos.
La lógica de "qué buscar" vive en config/segmentos.py.
"""

import logging
import time
from typing import Any, Dict, List, Optional

import requests

from config.base import (
    GOOGLE_API_RATE_LIMIT, GOOGLE_PLACES_API_KEY, REQUEST_TIMEOUT, USER_AGENT,
)

log = logging.getLogger("places_client")

PLACES_SEARCH_TEXT_URL = "https://places.googleapis.com/v1/places:searchText"

# Campos que pedimos. La API nueva (v1) devuelve TODO en una sola llamada,
# no hace falta Place Details aparte.
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.location",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.websiteUri",
    "places.rating",
    "places.userRatingCount",
    "places.businessStatus",
    "places.regularOpeningHours",
    "places.types",
    "nextPageToken",
])


def _comprobar_api_key() -> None:
    if not GOOGLE_PLACES_API_KEY or GOOGLE_PLACES_API_KEY == "pegar-aqui-la-clave-real-de-google-places":
        raise RuntimeError(
            "GOOGLE_PLACES_API_KEY no está configurada con una clave real. "
            "Define la variable de entorno antes de ejecutar el scraper."
        )


def text_search(
    query: str,
    page_token: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Hace una petición Text Search a Places API v1.

    Devuelve el JSON completo (dict). Incluye 'places' y opcionalmente 'nextPageToken'.
    """
    _comprobar_api_key()

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
        "User-Agent": USER_AGENT,
    }
    body: Dict[str, Any] = {
        "textQuery": query,
        "languageCode": "es",
        "regionCode": "ES",
        "maxResultCount": 20,
    }
    if page_token:
        body["pageToken"] = page_token

    for intento in range(1, 4):
        try:
            r = requests.post(
                PLACES_SEARCH_TEXT_URL,
                headers=headers,
                json=body,
                timeout=REQUEST_TIMEOUT,
            )
            if r.status_code == 200:
                return r.json()
            elif r.status_code == 429:
                wait = 5 * intento
                log.warning("Rate limit (429). Esperando %ds...", wait)
                time.sleep(wait)
                continue
            else:
                log.error("HTTP %s: %s", r.status_code, r.text[:300])
                r.raise_for_status()
        except requests.RequestException as e:
            log.warning("Intento %d fallido: %s", intento, e)
            time.sleep(2 ** intento)

    raise RuntimeError(f"No se pudo completar la búsqueda tras 3 intentos: {query}")


def text_search_completa(query: str, max_paginas: int = 5) -> List[Dict[str, Any]]:
    """
    Devuelve todos los resultados de una query, paginando hasta `max_paginas`.
    Google devuelve hasta 20 resultados por página y un máximo total de 60.
    """
    todos: List[Dict[str, Any]] = []
    page_token: Optional[str] = None
    pagina = 1
    while True:
        resp = text_search(query=query, page_token=page_token)
        places = resp.get("places", [])
        todos.extend(places)
        log.debug("   Página %d: %d resultados", pagina, len(places))

        page_token = resp.get("nextPageToken")
        if not page_token:
            break

        time.sleep(2)  # requerido por Google entre paginaciones
        pagina += 1
        if pagina > max_paginas:
            log.warning("   Límite de paginación alcanzado (%d).", max_paginas)
            break
        time.sleep(GOOGLE_API_RATE_LIMIT)

    return todos
