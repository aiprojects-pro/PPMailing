"""Conversión de respuestas de Google Places a objetos Negocio + deduplicación."""

from typing import Any, Dict, List

from core.modelos import Negocio


def _extraer_localizacion(address_components: List[Dict[str, Any]]) -> Dict[str, str]:
    """Saca CP, localidad, provincia y CCAA de los componentes de Google."""
    out = {"codigo_postal": "", "localidad": "", "provincia": "", "ccaa": ""}
    for comp in address_components or []:
        types = comp.get("types", [])
        text = comp.get("longText", "")
        if "postal_code" in types:
            out["codigo_postal"] = text
        elif "locality" in types:
            out["localidad"] = text
        elif "administrative_area_level_2" in types:
            out["provincia"] = text
        elif "administrative_area_level_1" in types:
            out["ccaa"] = text
    return out


def place_a_negocio(
    place: Dict[str, Any],
    segmento_id: str,
    query_origen: str,
    ciudad_origen: str,
) -> Negocio:
    """Convierte un 'place' de Google en un Negocio del segmento dado."""
    loc = _extraer_localizacion(place.get("addressComponents", []))
    location = place.get("location", {}) or {}
    display_name = place.get("displayName", {}) or {}

    return Negocio(
        place_id=place.get("id", ""),
        segmento=segmento_id,
        fuente="google_places",
        nombre=display_name.get("text", ""),
        direccion=place.get("formattedAddress", ""),
        codigo_postal=loc["codigo_postal"],
        localidad=loc["localidad"],
        provincia=loc["provincia"],
        ccaa=loc["ccaa"],
        telefono=place.get("nationalPhoneNumber", ""),
        telefono_internacional=place.get("internationalPhoneNumber", ""),
        web=place.get("websiteUri", ""),
        rating=place.get("rating"),
        num_resenas=place.get("userRatingCount"),
        estado_negocio=place.get("businessStatus", ""),
        tiene_horario=bool(place.get("regularOpeningHours")),
        tipos=place.get("types", []),
        latitud=location.get("latitude"),
        longitud=location.get("longitude"),
        queries_origen=[query_origen],
        ciudades_origen=[ciudad_origen],
    )


def deduplicar(negocios: List[Negocio]) -> List[Negocio]:
    """
    Deduplica por place_id (Google) o por (nombre+CP) para fuentes sin place_id.
    Cuando un negocio aparece varias veces, fusiona queries_origen y ciudades_origen.
    """
    indice: Dict[str, Negocio] = {}
    for n in negocios:
        clave = n.place_id or f"{n.nombre.lower()}|{n.codigo_postal}"
        if not clave:
            continue
        if clave in indice:
            existente = indice[clave]
            for q in n.queries_origen:
                if q not in existente.queries_origen:
                    existente.queries_origen.append(q)
            for c in n.ciudades_origen:
                if c not in existente.ciudades_origen:
                    existente.ciudades_origen.append(c)
        else:
            indice[clave] = n
    return list(indice.values())
