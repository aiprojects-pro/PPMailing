"""
Extractor de emails desde webs + análisis de palabras clave por segmento.

Diferencia respecto a v0.2: además del email, busca palabras clave que sirven
para el scoring (ej. "menores", "lopivi", "categorías base") y palabras de
descarte (ej. "solo adultos").
"""

import logging
import re
import time
from typing import Optional, Set, List
from urllib.parse import urlparse, urljoin

import requests
from bs4 import BeautifulSoup

from config.base import USER_AGENT, WEB_SCRAPING_RATE_LIMIT, REQUEST_TIMEOUT
from config.segmentos import obtener_segmento
from core.modelos import Negocio

log = logging.getLogger("emails")

# -----------------------------------------------------------------------------
# REGEX Y CONSTANTES
# -----------------------------------------------------------------------------

RE_EMAIL = re.compile(r"\b[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}\b")

KEYWORDS_CONTACTO = [
    "contacto", "contact", "contactar", "contacta",
    "aviso-legal", "aviso_legal", "aviso",
    "sobre-nosotros", "quienes-somos", "quienessomos", "nosotros",
    "equipo", "team",
    "ubicacion", "donde-estamos",
    "politica-privacidad", "politica-de-privacidad", "privacidad",
]

PREFIJOS_PREFERIDOS = [
    "info", "contacto", "contact", "hola",
    "comercial", "ventas", "administracion", "secretaria",
    "atencion", "clientes",
]

PREFIJOS_DESCARTAR = [
    "abuse", "postmaster", "webmaster", "noreply", "no-reply",
    "soporte-tecnico", "tech", "it", "wordpress", "admin",
]

DOMINIOS_GENERICOS = {
    "gmail.com", "hotmail.com", "yahoo.es", "yahoo.com",
    "outlook.com", "outlook.es", "live.com", "icloud.com",
    "telefonica.net", "movistar.es", "terra.es",
}

EMAILS_FALSOS_TIPICOS = {
    "tu-email@ejemplo.com", "ejemplo@ejemplo.com", "nombre@dominio.com",
    "tuemail@tudominio.com", "user@example.com",
}

EXT_FALSOS = (".jpg", ".jpeg", ".png", ".gif", ".webp", ".svg", ".pdf")


# -----------------------------------------------------------------------------
# DESCARGA
# -----------------------------------------------------------------------------

def descargar(url: str) -> Optional[str]:
    headers = {"User-Agent": USER_AGENT, "Accept-Language": "es-ES,es;q=0.9"}
    try:
        r = requests.get(url, headers=headers, timeout=REQUEST_TIMEOUT, allow_redirects=True)
        if r.status_code == 200:
            if not r.encoding or r.encoding.lower() == "iso-8859-1":
                r.encoding = r.apparent_encoding or "utf-8"
            return r.text
    except requests.RequestException:
        pass
    return None


# -----------------------------------------------------------------------------
# EMAIL EXTRACTION
# -----------------------------------------------------------------------------

def dominio_raiz(dominio: str) -> str:
    d = (dominio or "").lower().lstrip(".")
    return d[4:] if d.startswith("www.") else d


def emails_de_html(html: str) -> Set[str]:
    emails: Set[str] = set()
    soup = BeautifulSoup(html, "html.parser")
    for a in soup.find_all("a", href=True):
        if a["href"].lower().startswith("mailto:"):
            email = a["href"][7:].split("?")[0].strip().lower()
            if email and "@" in email:
                emails.add(email)
    for fuente in (html, soup.get_text(" ")):
        for match in RE_EMAIL.findall(fuente):
            emails.add(match.lower())

    limpios: Set[str] = set()
    for e in emails:
        e = e.rstrip(".,;:")
        if any(e.endswith(ext) for ext in EXT_FALSOS):
            continue
        if e in EMAILS_FALSOS_TIPICOS:
            continue
        local, _, dominio = e.partition("@")
        if len(local) > 50 or "sentry" in dominio:
            continue
        limpios.add(e)
    return limpios


def puntuar_email(email: str, dominio_web: str) -> int:
    local, _, dominio = email.partition("@")
    puntos = 0
    if dominio_web and dominio_raiz(dominio) == dominio_raiz(dominio_web):
        puntos += 50
    elif dominio in DOMINIOS_GENERICOS:
        puntos += 10
    else:
        puntos += 30

    if local in PREFIJOS_PREFERIDOS:
        puntos += 20
    elif any(local.startswith(p) for p in PREFIJOS_PREFERIDOS):
        puntos += 15
    if any(local.startswith(p) for p in PREFIJOS_DESCARTAR):
        puntos -= 50
    if "." in local and not local.startswith(tuple(PREFIJOS_PREFERIDOS)):
        puntos -= 5
    return puntos


def elegir_mejor_email(candidatos: Set[str], dominio_web: str) -> Optional[str]:
    if not candidatos:
        return None
    puntuados = sorted(
        ((puntuar_email(e, dominio_web), e) for e in candidatos),
        key=lambda x: -x[0],
    )
    mejor_punt, mejor_email = puntuados[0]
    return mejor_email if mejor_punt >= 0 else None


# -----------------------------------------------------------------------------
# DESCUBRIMIENTO DE PÁGINAS RELEVANTES
# -----------------------------------------------------------------------------

def encontrar_paginas_contacto(html_raiz: str, url_raiz: str) -> List[str]:
    soup = BeautifulSoup(html_raiz, "html.parser")
    candidatos = []
    for a in soup.find_all("a", href=True):
        href = a["href"].strip()
        texto = a.get_text(strip=True).lower()
        href_lower = href.lower()
        if href_lower.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        url_abs = urljoin(url_raiz, href)
        if urlparse(url_abs).netloc != urlparse(url_raiz).netloc:
            continue
        prioridad = 0
        for kw in KEYWORDS_CONTACTO:
            if kw in href_lower:
                prioridad = max(prioridad, 10)
            if kw in texto:
                prioridad = max(prioridad, 5)
        if prioridad > 0:
            candidatos.append((prioridad, url_abs))
    candidatos.sort(key=lambda x: -x[0])

    vistos: Set[str] = set()
    resultado: List[str] = []
    for _, url in candidatos:
        if url not in vistos:
            vistos.add(url)
            resultado.append(url)
        if len(resultado) >= 3:
            break
    return resultado


# -----------------------------------------------------------------------------
# DETECCIÓN DE PALABRAS CLAVE EN LA WEB
# -----------------------------------------------------------------------------

def analizar_palabras_clave(html: str, segmento_id: str) -> dict:
    """
    Busca en el texto de la web las palabras clave y de descarte del segmento.
    Devuelve un dict con listas (palabras_clave_encontradas, palabras_descarte_encontradas,
    tiene_apartado_lopivi).
    """
    seg = obtener_segmento(segmento_id)
    soup = BeautifulSoup(html, "html.parser")
    texto = soup.get_text(" ").lower()

    encontradas = []
    for kw in seg.get("palabras_clave_web", []):
        if kw.lower() in texto:
            encontradas.append(kw)

    descartes = []
    for kw in seg.get("palabras_descarte", []):
        if kw.lower() in texto:
            descartes.append(kw)

    # Bonus específico: ¿menciona explícitamente LOPIVI?
    tiene_lopivi = "lopivi" in texto or "ley orgánica de protección integral" in texto

    return {
        "palabras_clave_encontradas": encontradas,
        "palabras_descarte_encontradas": descartes,
        "tiene_apartado_lopivi": tiene_lopivi,
    }


# -----------------------------------------------------------------------------
# PROCESAMIENTO DE UN NEGOCIO
# -----------------------------------------------------------------------------

def procesar_web(negocio: Negocio) -> None:
    """Visita la web del negocio, extrae email y palabras clave. Modifica in-place."""
    if not negocio.web:
        return

    html_raiz = descargar(negocio.web)
    if not html_raiz:
        log.info("   ⚠️  No se pudo acceder a %s", negocio.web)
        return

    # Acumulamos HTML de raíz + páginas de contacto para análisis completo
    emails_acumulados: Set[str] = set(emails_de_html(html_raiz))
    html_total = html_raiz

    pags_contacto = encontrar_paginas_contacto(html_raiz, negocio.web)
    for url_extra in pags_contacto[:2]:
        time.sleep(WEB_SCRAPING_RATE_LIMIT)
        html_extra = descargar(url_extra)
        if html_extra:
            emails_acumulados |= emails_de_html(html_extra)
            html_total += " " + html_extra

    # Elegir mejor email
    dominio_web = urlparse(negocio.web).netloc
    mejor = elegir_mejor_email(emails_acumulados, dominio_web)
    if mejor:
        negocio.email = mejor
        dominio_email = mejor.partition("@")[2]
        negocio.email_dominio_propio = (
            dominio_raiz(dominio_email) == dominio_raiz(dominio_web)
        )

    # Análisis de palabras clave (sobre todo el HTML acumulado)
    analisis = analizar_palabras_clave(html_total, negocio.segmento)
    negocio.palabras_clave_encontradas = analisis["palabras_clave_encontradas"]
    negocio.palabras_descarte_encontradas = analisis["palabras_descarte_encontradas"]
    negocio.tiene_apartado_lopivi = analisis["tiene_apartado_lopivi"]

    if mejor:
        log.info("   ✅ %s | email: %s | claves: %s", negocio.nombre[:40],
                 mejor, len(negocio.palabras_clave_encontradas))
    else:
        log.info("   ⚠️  %s | sin email válido", negocio.nombre[:40])
