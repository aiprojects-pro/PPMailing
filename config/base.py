"""Configuración base compartida por todos los scripts."""

import os
from pathlib import Path

# Credenciales (variable de entorno)
GOOGLE_PLACES_API_KEY = os.environ.get("GOOGLE_PLACES_API_KEY", "")

# Identificación del bot
USER_AGENT = (
    "CGDFormacionBot/0.3 "
    "(+https://cgdformacion.com; contacto@cgdformacion.com)"
)

# Rate limits (segundos entre peticiones)
GOOGLE_API_RATE_LIMIT = 0.5
WEB_SCRAPING_RATE_LIMIT = 2.0
REQUEST_TIMEOUT = 20

# Rutas
PROJECT_ROOT = Path(__file__).parent.parent
DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# Umbral mínimo para que un lead pase a campaña
SCORE_MINIMO_CAMPANYA = 50
