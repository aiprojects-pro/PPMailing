"""Modelo de datos genérico para todos los segmentos."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


@dataclass
class Negocio:
    """
    Representa cualquier negocio captado por el sistema.
    Es genérico: sirve para despachos, clubes, campamentos, federaciones, etc.
    El campo 'segmento' identifica de qué tipo es.
    """
    # === Identificación ===
    place_id: str = ""              # ID de Google (solo para fuentes Places)
    fuente_id_externo: str = ""     # ID en la fuente original (federaciones, etc.)
    segmento: str = ""              # admin_fincas, clubes_deportivos, etc.
    fuente: str = ""                # google_places, csd, junta_andalucia, ...

    # === Datos comerciales ===
    nombre: str = ""
    direccion: str = ""
    codigo_postal: str = ""
    localidad: str = ""
    provincia: str = ""
    ccaa: str = ""
    pais: str = "España"
    telefono: str = ""
    telefono_internacional: str = ""
    web: str = ""
    email: str = ""
    email_dominio_propio: bool = False

    # === Señales de calidad (de Google Places) ===
    rating: Optional[float] = None
    num_resenas: Optional[int] = None
    estado_negocio: str = ""        # OPERATIONAL / CLOSED_TEMPORARILY / CLOSED_PERMANENTLY
    tiene_horario: bool = False
    tipos: List[str] = field(default_factory=list)
    latitud: Optional[float] = None
    longitud: Optional[float] = None

    # === Análisis de la web ===
    palabras_clave_encontradas: List[str] = field(default_factory=list)
    palabras_descarte_encontradas: List[str] = field(default_factory=list)
    tiene_apartado_lopivi: bool = False   # bonus si ya hablan de LOPIVI

    # === Trazabilidad ===
    queries_origen: List[str] = field(default_factory=list)
    ciudades_origen: List[str] = field(default_factory=list)
    fecha_extraccion: str = ""

    # === Resultado del scoring ===
    score: int = 0
    motivos_score: List[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.fecha_extraccion:
            self.fecha_extraccion = datetime.now().isoformat(timespec="seconds")
