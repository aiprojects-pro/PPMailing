"""
=============================================================================
Scraper RAED (Registro Andaluz de Entidades Deportivas)
=============================================================================

ESTADO: Implementado a partir de la captura HTTP del formulario público.

Para completar este scraper hace falta:

1. Que los administradores de sistemas de CGD capturen el comportamiento real
   del formulario POST con DevTools (ver docs/guia_devtools_raed.md).

2. Pegar los datos capturados en las funciones marcadas con TODO.

3. Ajustar el parseo de la respuesta HTML según el formato real.

URL fuente:
  https://www.juntadeandalucia.es/deporte/dpweb/buscadorRaed/index

Datos esperados por cada entidad:
  - Nombre del club / asociación / sección deportiva
  - Número de inscripción RAED
  - Tipo de entidad (Club deportivo, Sección, Federación, etc.)
  - Deporte registrado
  - Provincia
  - Municipio
  - Fecha de inscripción

NOTA IMPORTANTE: El RAED no publica email, teléfono ni web. Tras este scraper
hay que EJECUTAR el módulo de cruce con Google Places para enriquecer cada
entidad con los datos comerciales (ver core/cruce_places.py).
=============================================================================
"""

import logging
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import requests
from bs4 import BeautifulSoup

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import USER_AGENT, REQUEST_TIMEOUT
from core.cruce_places import EntradaParaCruce

log = logging.getLogger("scraper_raed")

# =============================================================================
# CONFIGURACIÓN — RELLENAR CON DATOS DE LA CAPTURA DEVTOOLS
# =============================================================================

# URL del buscador (página inicial, donde se obtienen cookies y posible CSRF)
URL_BUSCADOR = "https://www.juntadeandalucia.es/deporte/dpweb/buscadorRaed/index"

URL_POST_BUSCAR = "https://www.juntadeandalucia.es/deporte/dpweb/buscadorRaed/result"

# Provincias de Andalucía con su código según el formulario (los valores que
# el <select> de "Provincia" tiene en sus <option value="...">).
PROVINCIAS_RAED = {
    "ALMERÍA": "4",
    "CÁDIZ": "11",
    "CÓRDOBA": "14",
    "GRANADA": "18",
    "HUELVA": "21",
    "JAÉN": "23",
    "MÁLAGA": "29",
    "SEVILLA": "41",
}

# Tipos de entidad de interés (valores del <select>)
TIPOS_ENTIDAD = {
    "Federacion deportiva": "2",
    "Club deportivo": "3",
    "Seccion deportiva": "4",
    "Sociedad anonima deportiva": "6",
    # Asociacion, Confederacion, etc. quedan fuera porque no son objetivo LOPIVI
}


# =============================================================================
# MODELO ESPECÍFICO RAED
# =============================================================================

@dataclass
class EntidadRAED:
    """Una entidad tal como aparece en el RAED."""
    numero_inscripcion: str = ""
    nombre: str = ""
    tipo_entidad: str = ""        # Club deportivo, Sección, etc.
    deporte: str = ""
    provincia: str = ""
    municipio: str = ""
    fecha_inscripcion: str = ""
    direccion: str = ""
    codigo_postal: str = ""
    telefono: str = ""
    email: str = ""
    estatutos_url: str = ""

    def to_entrada_cruce(self) -> EntradaParaCruce:
        """Convierte a una entrada para el módulo de cruce con Google."""
        return EntradaParaCruce(
            nombre=self.nombre,
            localidad=self.municipio,
            provincia=self.provincia,
            fuente_id_externo=f"RAED-{self.numero_inscripcion}",
            datos_origen={
                "tipo_entidad": self.tipo_entidad,
                "deporte": self.deporte,
                "fecha_inscripcion": self.fecha_inscripcion,
                "direccion": self.direccion,
                "codigo_postal": self.codigo_postal,
                "telefono": self.telefono,
                "email_raed": self.email,
                "estatutos_url": self.estatutos_url,
            },
        )


# =============================================================================
# SESIÓN HTTP CON COOKIES
# =============================================================================

def crear_sesion() -> requests.Session:
    """
    Crea una sesión HTTP que mantiene cookies entre peticiones.
    Hace primero un GET a la página inicial para obtener cookies de sesión
    y, si aplica, el token CSRF.
    """
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept-Language": "es-ES,es;q=0.9",
    })
    log.info("Obteniendo cookies iniciales de %s", URL_BUSCADOR)
    r = s.get(URL_BUSCADOR, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    log.debug("Cookies obtenidas: %s", list(s.cookies.keys()))

    return s


# =============================================================================
# BÚSQUEDA POR PROVINCIA Y TIPO
# =============================================================================

def buscar_pagina(
    sesion: requests.Session,
    provincia_value: str,
    tipo_entidad_value: str,
    pagina: int = 1,
) -> Dict[str, Any]:
    """
    Hace una petición de resultados y devuelve entidades + paginación.
    """
    payload = {
        "numinsc": "",
        "fecdesde": "",
        "fechasta": "",
        "nombre": "",
        "tiposentidad": tipo_entidad_value,
        "provincia": provincia_value,
        "deporte": "",
        "municipio": "",
    }
    headers = {
        "Content-Type": "application/x-www-form-urlencoded",
        "Referer": URL_BUSCADOR,
    }

    if pagina <= 1:
        r = sesion.post(URL_POST_BUSCAR, data=payload, headers=headers, timeout=REQUEST_TIMEOUT)
    else:
        params = {**payload, "offset": str((pagina - 1) * 10), "max": "10"}
        r = sesion.get(URL_POST_BUSCAR, params=params, headers={"Referer": URL_POST_BUSCAR}, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()

    soup = BeautifulSoup(r.text, "html.parser")
    siguiente = bool(soup.select_one("li.next a.step"))
    return {"entidades": parsear_html_resultados(r.text), "siguiente_pagina": siguiente}


def parsear_html_resultados(html: str) -> List[EntidadRAED]:
    """
    Parsea el HTML de una página de resultados y devuelve la lista de entidades.

    El RAED devuelve una tabla con filas alternas: resumen y panel de detalle
    oculto. El panel ya viene en el HTML, no requiere una petición adicional.
    """
    soup = BeautifulSoup(html, "html.parser")
    entidades: List[EntidadRAED] = []
    rows = soup.select("table.table tbody tr")
    i = 0
    while i < len(rows):
        resumen = rows[i]
        detalle = rows[i + 1] if i + 1 < len(rows) else None
        celdas = [td.get_text(" ", strip=True) for td in resumen.find_all("td")]
        if len(celdas) < 4:
            i += 1
            continue

        entidad = EntidadRAED(
            numero_inscripcion=celdas[1],
            nombre=celdas[2],
            municipio=celdas[3],
        )

        if detalle:
            panel = detalle.find("td", class_="panel") or detalle.find("td")
            if panel:
                campos = {}
                for strong in panel.find_all("strong"):
                    clave = strong.get_text(" ", strip=True).rstrip(":")
                    valor = []
                    for sibling in strong.next_siblings:
                        if getattr(sibling, "name", None) == "br":
                            break
                        valor.append(sibling.get_text(" ", strip=True) if hasattr(sibling, "get_text") else str(sibling))
                    campos[clave] = " ".join(v.strip() for v in valor if v and v.strip())

                entidad.fecha_inscripcion = campos.get("Fecha inscripción", "")
                entidad.direccion = campos.get("Dirección", "")
                entidad.provincia = campos.get("Provincia", "")
                entidad.municipio = campos.get("Municipio", entidad.municipio)
                entidad.codigo_postal = campos.get("C.P.", "")
                entidad.telefono = campos.get("Teléfono", "")
                entidad.email = campos.get("Email", "")
                entidad.tipo_entidad = campos.get("Entidad", "")
                entidad.deporte = campos.get("Deporte", "")
                link = panel.find("a", href=True)
                if link:
                    entidad.estatutos_url = requests.compat.urljoin(URL_BUSCADOR, link["href"])

        entidades.append(entidad)
        i += 2
    return entidades


# =============================================================================
# RECORRIDO COMPLETO DE UNA PROVINCIA × TIPO
# =============================================================================

def descargar_provincia_tipo(
    sesion: requests.Session,
    provincia_nombre: str,
    tipo_nombre: str,
    rate_limit_seg: float = 1.5,
    max_paginas: Optional[int] = None,
) -> List[EntidadRAED]:
    """
    Pagina todos los resultados para una provincia y tipo concretos.
    """
    provincia_value = PROVINCIAS_RAED.get(provincia_nombre, "")
    tipo_value = TIPOS_ENTIDAD.get(tipo_nombre, "")
    if not provincia_value or not tipo_value:
        log.error("Valores no configurados: provincia=%s, tipo=%s",
                  provincia_nombre, tipo_nombre)
        return []

    log.info("📍 %s — %s", provincia_nombre, tipo_nombre)
    todas: List[EntidadRAED] = []
    pagina = 1
    while True:
        resp = buscar_pagina(sesion, provincia_value, tipo_value, pagina)
        entidades = resp.get("entidades", [])
        todas.extend(entidades)
        log.info("   página %d: %d entidades", pagina, len(entidades))
        if not resp.get("siguiente_pagina"):
            break
        if max_paginas is not None and pagina >= max_paginas:
            log.info("Límite de páginas alcanzado (%d)", max_paginas)
            break
        pagina += 1
        time.sleep(rate_limit_seg)
        if pagina > 500:  # safety guard
            log.warning("Límite de 500 páginas alcanzado")
            break
    return todas


def descargar_andalucia_completa() -> List[EntidadRAED]:
    """Descarga TODAS las entidades de interés de TODAS las provincias de Andalucía."""
    sesion = crear_sesion()
    todas: List[EntidadRAED] = []
    for provincia in PROVINCIAS_RAED:
        for tipo in TIPOS_ENTIDAD:
            entidades = descargar_provincia_tipo(sesion, provincia, tipo)
            todas.extend(entidades)
    return todas
