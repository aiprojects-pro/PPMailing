"""
Sistema de scoring por segmento.

Aplica las reglas definidas en config.segmentos para ese segmento concreto.
Devuelve además la lista de motivos (útil para depurar y revisar leads dudosos).
"""

import re
from typing import List, Tuple

from core.modelos import Negocio
from config.segmentos import obtener_segmento

# Patrón para detectar sociedades en el nombre comercial
RE_SOCIEDAD = re.compile(r"\b(S\.?L\.?U\.?|S\.?L\.?|S\.?A\.?|S\.?C\.?P\.?|Coop\.)\b",
                         re.IGNORECASE)


def calcular_score(negocio: Negocio) -> Tuple[int, List[str]]:
    """
    Calcula el score 0-100 de un negocio y devuelve (score, motivos).
    Lee las reglas del segmento al que pertenece el negocio.
    """
    seg = obtener_segmento(negocio.segmento)
    reglas = seg["reglas_scoring"]

    score = 0
    motivos: List[str] = []

    if negocio.web:
        score += reglas.get("tiene_web", 0)
        motivos.append(f"+{reglas['tiene_web']} web")
    if negocio.telefono:
        score += reglas.get("tiene_telefono", 0)
        motivos.append(f"+{reglas['tiene_telefono']} teléfono")
    if negocio.email:
        score += reglas.get("tiene_email", 0)
        motivos.append(f"+{reglas['tiene_email']} email")
    if negocio.email_dominio_propio:
        score += reglas.get("email_corporativo", 0)
        motivos.append(f"+{reglas['email_corporativo']} email dominio propio")
    if negocio.rating and negocio.rating >= 4.0:
        score += reglas.get("rating_alto", 0)
        motivos.append(f"+{reglas['rating_alto']} rating {negocio.rating:.1f}")
    if negocio.num_resenas and negocio.num_resenas >= 10:
        score += reglas.get("muchas_resenas", 0)
        motivos.append(f"+{reglas['muchas_resenas']} {negocio.num_resenas} reseñas")
    if negocio.tiene_horario:
        score += reglas.get("horario_publico", 0)
        motivos.append(f"+{reglas['horario_publico']} horario público")
    if RE_SOCIEDAD.search(negocio.nombre or ""):
        score += reglas.get("es_sociedad_limitada", 0)
        motivos.append(f"+{reglas['es_sociedad_limitada']} forma jurídica visible")

    # Bonus por palabras clave (5 puntos cada una, máximo 20)
    if negocio.palabras_clave_encontradas:
        bonus = min(len(negocio.palabras_clave_encontradas) * 5, 20)
        score += bonus
        motivos.append(f"+{bonus} palabras clave web: {', '.join(negocio.palabras_clave_encontradas)}")

    # Bonus si ya menciona LOPIVI (puede ser señal de interés activo, o de competencia)
    if negocio.tiene_apartado_lopivi:
        # Aquí podríamos restar (ya tienen formación) o sumar (saben que necesitan).
        # Mi recomendación: sumar 5 (saben que existe, son target informado)
        score += 5
        motivos.append("+5 ya menciona LOPIVI")

    # Penalización por palabras de descarte
    if negocio.palabras_descarte_encontradas:
        penal = -30
        score += penal
        motivos.append(f"{penal} palabras descarte: {', '.join(negocio.palabras_descarte_encontradas)}")

    return min(max(score, 0), 100), motivos
