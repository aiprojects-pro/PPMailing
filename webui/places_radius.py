"""
Búsqueda por radio geográfico (coordenadas + radio en metros).

No modifica scripts/buscar.py ni core/places_client.py. Lo que hace es:

  1. Importar `text_search` y `place_a_negocio` del proyecto base.
  2. Llamar a Places API New con un body que incluye `locationRestriction`
     en lugar de añadir " <ciudad>" a la query.
  3. Devolver una lista de objetos `Negocio` con `ciudad_origen` =
     "punto_N (lat,lng,radio)" para mantener trazabilidad.

Se usa como reemplazo de `scripts/buscar.py` cuando el ámbito de un job es
de tipo "radio". El resto del pipeline (extraer_emails, generar_csv) sigue
funcionando sin cambios porque trabajan sobre el JSON que dejamos en
`data/<segmento>_<jobid>_<YYYYMMDD>.json`.

Decisión clave: usamos `locationRestriction` con un `circle` (lat,lng,radio).
Es lo que Google considera filtro estricto: los resultados están DENTRO del
círculo. Hay un máximo de 50 km de radio en la API (lo validamos antes).
"""

import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

import requests

from .paths import PROJECT_ROOT


# Importar piezas del proyecto base sin modificarlo
sys.path.insert(0, str(PROJECT_ROOT))
from config.base import (  # noqa: E402
    GOOGLE_API_RATE_LIMIT, REQUEST_TIMEOUT, USER_AGENT,
)
from core.places_client import FIELD_MASK, PLACES_SEARCH_TEXT_URL  # noqa: E402
from core.parser_y_dedup import place_a_negocio, deduplicar  # noqa: E402


log = logging.getLogger("places_radius")


# Límites de la API New
MAX_RADIUS_METERS = 50_000   # 50 km, máximo permitido por Google
MIN_RADIUS_METERS = 100      # 100 m, no tiene sentido bajar de aquí


def _text_search_with_radius(
    query: str,
    latitude: float,
    longitude: float,
    radius_meters: int,
    api_key: str,
    page_token: str | None = None,
) -> dict:
    """
    Hace una Text Search con locationRestriction de tipo círculo.
    Idéntico a `text_search` del proyecto base pero con el body modificado.
    """
    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": api_key,
        "X-Goog-FieldMask": FIELD_MASK,
        "User-Agent": USER_AGENT,
    }
    body: dict = {
        "textQuery": query,
        "languageCode": "es",
        "regionCode": "ES",
        "maxResultCount": 20,
        "locationRestriction": {
            "circle": {
                "center": {
                    "latitude": float(latitude),
                    "longitude": float(longitude),
                },
                "radius": float(radius_meters),
            },
        },
    }
    if page_token:
        body["pageToken"] = page_token

    resp = requests.post(
        PLACES_SEARCH_TEXT_URL,
        json=body, headers=headers, timeout=REQUEST_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def _text_search_radius_completa(
    query: str, latitude: float, longitude: float,
    radius_meters: int, api_key: str, max_paginas: int = 3,
) -> list[dict]:
    """Paginación igual que text_search_completa pero por radio."""
    todos: list[dict] = []
    page_token: str | None = None
    pagina = 1
    while True:
        resp = _text_search_with_radius(
            query, latitude, longitude, radius_meters, api_key,
            page_token=page_token,
        )
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


def validate_points(points: list[dict]) -> tuple[list[dict], list[str]]:
    """
    Valida una lista de puntos para búsqueda por radio.

    Cada punto debe ser: {"latitude": <float>, "longitude": <float>,
                          "radius": <int meters>, "label": <str opcional>}

    Devuelve (lista_normalizada, lista_de_errores). Si hay errores, la
    lista normalizada está vacía.
    """
    errors = []
    normalized = []

    if not points:
        return [], ["Debes indicar al menos un punto."]
    if len(points) > 20:
        return [], ["Máximo 20 puntos por búsqueda."]

    for i, p in enumerate(points, start=1):
        try:
            lat = float(p.get("latitude"))
            lng = float(p.get("longitude"))
            radius = int(p.get("radius", 5000))
        except (TypeError, ValueError):
            errors.append(f"Punto {i}: valores numéricos inválidos.")
            continue

        if not (-90 <= lat <= 90):
            errors.append(f"Punto {i}: latitud {lat} fuera de rango (-90..90).")
            continue
        if not (-180 <= lng <= 180):
            errors.append(f"Punto {i}: longitud {lng} fuera de rango (-180..180).")
            continue
        if not (MIN_RADIUS_METERS <= radius <= MAX_RADIUS_METERS):
            errors.append(
                f"Punto {i}: radio {radius}m fuera de rango "
                f"({MIN_RADIUS_METERS}..{MAX_RADIUS_METERS})."
            )
            continue

        label = (p.get("label") or f"punto_{i}").strip()[:80]
        normalized.append({
            "latitude": lat, "longitude": lng,
            "radius": radius, "label": label,
        })

    if errors:
        return [], errors
    return normalized, []


def run_radius_search(
    segmento_id: str,
    queries: list[str],
    points: list[dict],
    api_key: str,
    max_paginas: int = 3,
    output_json: Path | None = None,
    progress_callback=None,
) -> dict:
    """
    Ejecuta una búsqueda por radio combinando todas las queries con todos
    los puntos. Devuelve estadísticas y deja el JSON listo para
    `extraer_emails.py` en `output_json`.

    progress_callback: función opcional (current, total, message) para
    reportar progreso (la usamos desde el job runner).
    """
    if not output_json:
        raise ValueError("output_json es obligatorio")

    todos = []
    total_calls = len(points) * len(queries)
    contador = 0
    errores = []

    for point in points:
        for query_base in queries:
            contador += 1
            label = point["label"]
            if progress_callback:
                progress_callback(
                    contador, total_calls,
                    f"[{contador}/{total_calls}] {query_base} cerca de {label}"
                )

            try:
                places = _text_search_radius_completa(
                    query=query_base,
                    latitude=point["latitude"],
                    longitude=point["longitude"],
                    radius_meters=point["radius"],
                    api_key=api_key,
                    max_paginas=max_paginas,
                )
                for p in places:
                    todos.append(place_a_negocio(
                        place=p,
                        segmento_id=segmento_id,
                        query_origen=query_base,
                        ciudad_origen=label,
                    ))
            except Exception as exc:
                errores.append(f"{query_base} @ {label}: {exc}")
                log.exception("Error en punto %s, query %s", label, query_base)

            time.sleep(GOOGLE_API_RATE_LIMIT)

    # Deduplicar antes de escribir (igual que buscar.py)
    unicos = deduplicar(todos)

    # Volcar el JSON con el mismo formato que buscar.py
    output_json.parent.mkdir(parents=True, exist_ok=True)
    with open(output_json, "w", encoding="utf-8") as f:
        json.dump([asdict(n) for n in unicos], f, ensure_ascii=False, indent=2)

    return {
        "total_calls": total_calls,
        "total_results": len(todos),
        "unique_results": len(unicos),
        "errors": errores,
    }
