"""
=============================================================================
Módulo de cruce con Google Places
=============================================================================
Recibe una lista de negocios identificados por (nombre, localidad) — sea de
donde sea (RAED, CGCAFE, Excel manual, federaciones autonómicas…) — y para
cada uno hace una búsqueda específica en Google Places para enriquecerlos con:

  - URL de la web
  - Teléfono
  - Rating + número de reseñas
  - Coordenadas
  - Estado del negocio
  - Place ID (para próximas consultas)

Estrategia clave: "búsqueda exacta + verificación de similitud".

  1. Buscamos en Places el texto "<nombre> <localidad>"
  2. De los resultados, escogemos el que tenga MAYOR similitud de nombre
     con el original, siempre que supere un umbral mínimo.
  3. Si ninguno supera el umbral → el negocio queda sin enriquecer (pero
     no se pierde la información original).

Esto es resistente a:
  - Variaciones tipográficas: "C.D. Triana" vs "Club Deportivo Triana"
  - Resultados parásitos: Google a veces devuelve negocios genéricos cercanos
  - Negocios homónimos en otras ciudades
=============================================================================
"""

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import GOOGLE_API_RATE_LIMIT
from core.places_client import text_search
from core.modelos import Negocio
from core.parser_y_dedup import place_a_negocio

log = logging.getLogger("cruce_places")


# -----------------------------------------------------------------------------
# CONFIGURACIÓN DE MATCHING
# -----------------------------------------------------------------------------

# Umbral mínimo de similitud (0-1) para considerar que un resultado de Google
# corresponde al negocio buscado. 0.55 es un buen equilibrio:
#   - "Club Deportivo Triana" vs "C.D. Triana" → ~0.7 (acepta)
#   - "Fincas García" vs "Fincas González" → ~0.45 (rechaza)
#
# DECISIÓN DE DISEÑO: preferimos falsos negativos (no matchear cuando debería)
# antes que falsos positivos (matchear con entidad equivocada). El falso
# positivo causa email a la entidad incorrecta, lo que daña reputación de envío.
UMBRAL_SIMILITUD = 0.55

# Tipos de Google Places que NUNCA corresponden a un negocio formativo / B2B
# que buscamos. Si el resultado tiene UNO de estos, lo descartamos aunque
# el nombre coincida (típico falso positivo: "Bar Atlético Triana").
TIPOS_INCOMPATIBLES = {
    "bar", "restaurant", "food", "meal_takeaway", "meal_delivery",
    "cafe", "bakery", "supermarket", "grocery_or_supermarket",
    "convenience_store", "store", "shopping_mall", "clothing_store",
    "shoe_store", "pharmacy", "drugstore",
    "gas_station", "car_dealer", "car_rental", "car_repair",
    "hospital", "doctor", "dentist", "veterinary_care",
    "bank", "atm", "post_office",
    "lodging", "hotel", "campground",  # excepto si el segmento es campamentos
    "tourist_attraction",
    "night_club", "casino",
    "church", "place_of_worship", "cemetery", "funeral_home",
}

# Para algunos segmentos hay tipos que normalmente serían "incompatibles" pero
# para ese segmento concreto SÍ son válidos.
TIPOS_EXCEPCIONES_POR_SEGMENTO = {
    "campamentos_verano": {"lodging", "campground", "tourist_attraction", "travel_agency"},
    "ludotecas_ocio_infantil": {"tourist_attraction"},  # alguna ludoteca aparece así
}

# Stopwords (palabras vacías) que se quitan al comparar nombres
STOPWORDS = {
    "el", "la", "los", "las", "de", "del", "y", "e", "o", "u",
    "club", "deportivo", "asociacion", "asociación", "sociedad",
    "sl", "s.l.", "slu", "s.a.", "sa", "cooperativa", "coop",
    "cd", "c.d.", "ad", "a.d.", "ud", "u.d.", "cf", "c.f.",
}


@dataclass
class EntradaParaCruce:
    """
    Estructura mínima de entrada al módulo de cruce.
    Esto es lo que el RAED, el CGCAFE o cualquier otra fuente debe
    proporcionar.
    """
    nombre: str
    localidad: str = ""
    provincia: str = ""
    # Identificador externo en la fuente origen (n° colegiado, n° inscripción RAED…)
    fuente_id_externo: str = ""
    # Datos adicionales que la fuente ya conoce y queremos conservar
    datos_origen: Dict[str, Any] = field(default_factory=dict)


# -----------------------------------------------------------------------------
# NORMALIZACIÓN Y SIMILITUD DE NOMBRES
# -----------------------------------------------------------------------------

def normalizar(texto: str) -> str:
    """Quita tildes, pasa a minúsculas, normaliza espacios y puntuación."""
    if not texto:
        return ""
    # NFD descompone acentos: "café" → "cafe" + acento agudo
    s = unicodedata.normalize("NFD", texto)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")  # quitar acentos
    s = s.lower()
    s = re.sub(r"[^\w\s]", " ", s)  # puntuación a espacios
    s = re.sub(r"\s+", " ", s).strip()
    return s


def tokens_significativos(nombre: str) -> set:
    """Devuelve el conjunto de palabras del nombre EXCLUYENDO stopwords."""
    tokens = normalizar(nombre).split()
    return set(t for t in tokens if t not in STOPWORDS and len(t) >= 2)


def similitud_nombres(a: str, b: str) -> float:
    """
    Similitud entre dos nombres comerciales. Devuelve un valor 0-1.

    Métrica usada: Jaccard sobre tokens significativos (sin stopwords).
    Robusto a variaciones tipográficas y abreviaturas típicas.
    """
    tokens_a = tokens_significativos(a)
    tokens_b = tokens_significativos(b)
    if not tokens_a or not tokens_b:
        return 0.0
    interseccion = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(interseccion) / len(union)


# -----------------------------------------------------------------------------
# BÚSQUEDA Y SELECCIÓN
# -----------------------------------------------------------------------------

def buscar_mejor_match(
    nombre: str,
    localidad: str,
    provincia: str = "",
    umbral: float = UMBRAL_SIMILITUD,
    segmento: str = "",
) -> Optional[Dict[str, Any]]:
    """
    Busca en Google Places y devuelve el resultado con mayor similitud al nombre.
    Si ninguno supera el umbral (o solo coinciden negocios de tipo incompatible),
    devuelve None.

    El parámetro `segmento` se usa para aplicar excepciones de tipos
    incompatibles (un campamento puede ser "lodging", una ludoteca puede ser
    "tourist_attraction"...).
    """
    query_partes = [nombre]
    if localidad:
        query_partes.append(localidad)
    if provincia and provincia.lower() not in (localidad or "").lower():
        query_partes.append(provincia)
    query = " ".join(query_partes)

    try:
        resp = text_search(query=query)
    except Exception as e:
        log.warning("Error en Places para '%s': %s", query, e)
        return None

    places = resp.get("places", [])
    if not places:
        log.debug("Sin resultados para '%s'", query)
        return None

    # Filtramos los resultados de tipo incompatible
    tipos_excluir = set(TIPOS_INCOMPATIBLES)
    if segmento in TIPOS_EXCEPCIONES_POR_SEGMENTO:
        tipos_excluir -= TIPOS_EXCEPCIONES_POR_SEGMENTO[segmento]

    candidatos = []
    for p in places:
        tipos = set(p.get("types", []))
        if tipos & tipos_excluir:
            log.debug("  Descartado por tipo incompatible: %s (tipos=%s)",
                      p.get("displayName", {}).get("text", "?"),
                      tipos & tipos_excluir)
            continue
        candidatos.append(p)

    if not candidatos:
        log.debug("Todos los resultados son de tipo incompatible para '%s'", query)
        return None

    # Evaluamos similitud de cada resultado con el nombre original
    mejor = None
    mejor_sim = 0.0
    for p in candidatos:
        nombre_devuelto = (p.get("displayName", {}) or {}).get("text", "")
        if not nombre_devuelto:
            continue
        sim = similitud_nombres(nombre, nombre_devuelto)
        if sim > mejor_sim:
            mejor_sim = sim
            mejor = p

    if mejor_sim >= umbral:
        log.debug("Match: '%s' ↔ '%s' (sim=%.2f)", nombre,
                  mejor["displayName"]["text"], mejor_sim)
        return mejor
    else:
        log.debug("Sin match aceptable para '%s' (mejor sim=%.2f)", nombre, mejor_sim)
        return None


# -----------------------------------------------------------------------------
# FUNCIÓN PRINCIPAL
# -----------------------------------------------------------------------------

def cruzar_con_places(
    entradas: List[EntradaParaCruce],
    segmento: str,
    umbral_similitud: float = UMBRAL_SIMILITUD,
    fuente_origen: str = "cruce_places",
) -> List[Negocio]:
    """
    Para cada entrada, busca su match en Google Places y devuelve una lista
    de Negocio (algunos enriquecidos, otros no).

    Args:
        entradas: lista de EntradaParaCruce con al menos nombre y localidad.
        segmento: ID del segmento ('clubes_deportivos', 'admin_fincas', etc.).
        umbral_similitud: 0-1, mínimo para aceptar un match.
        fuente_origen: cómo se etiqueta el origen ('raed', 'cgcafe', 'manual'...).

    Returns:
        Lista de objetos Negocio, todos los de entrada. Los matcheados llevan
        place_id y datos de Google. Los no matcheados llevan al menos el nombre
        y localidad originales para que se puedan trabajar manualmente.
    """
    log.info("Iniciando cruce de %d entradas con Google Places", len(entradas))

    resultados: List[Negocio] = []
    matcheados = 0
    sin_match = 0

    for i, entrada in enumerate(entradas, 1):
        if i % 50 == 0:
            log.info("Progreso: %d/%d (%.0f%%) - matches: %d",
                     i, len(entradas), 100*i/len(entradas), matcheados)

        match = buscar_mejor_match(
            nombre=entrada.nombre,
            localidad=entrada.localidad,
            provincia=entrada.provincia,
            umbral=umbral_similitud,
            segmento=segmento,
        )

        if match:
            # Enriquecemos con datos de Google
            negocio = place_a_negocio(
                place=match,
                segmento_id=segmento,
                query_origen=f"cruce: {entrada.nombre}",
                ciudad_origen=entrada.localidad,
            )
            # Sustituimos la fuente y conservamos el nombre original como referencia
            negocio.fuente = f"{fuente_origen}+google_places"
            negocio.fuente_id_externo = entrada.fuente_id_externo
            # Si conocemos provincia mejor de la fuente origen, no la sobrescribimos
            if not negocio.provincia and entrada.provincia:
                negocio.provincia = entrada.provincia
            matcheados += 1
        else:
            # Sin match: creamos Negocio con solo los datos de origen
            negocio = Negocio(
                segmento=segmento,
                fuente=fuente_origen,
                fuente_id_externo=entrada.fuente_id_externo,
                nombre=entrada.nombre,
                localidad=entrada.localidad,
                provincia=entrada.provincia,
            )
            sin_match += 1

        # Datos extras que la fuente original aportaba (ej. nº inscripción RAED,
        # deporte registrado, fecha alta) los preservamos en queries_origen como
        # marca informativa, sin sobrescribir nada importante.
        for k, v in entrada.datos_origen.items():
            if k == "telefono" and not negocio.telefono:
                negocio.telefono = v
            elif k == "web" and not negocio.web:
                negocio.web = v
            elif k == "direccion" and not negocio.direccion:
                negocio.direccion = v

        resultados.append(negocio)

        # Rate limit con Google
        time.sleep(GOOGLE_API_RATE_LIMIT)

    log.info("Cruce completado:")
    log.info("  Total entradas:     %d", len(entradas))
    log.info("  Matcheadas en Google: %d (%.1f%%)",
             matcheados, 100*matcheados/max(len(entradas), 1))
    log.info("  Sin match:          %d", sin_match)

    return resultados
