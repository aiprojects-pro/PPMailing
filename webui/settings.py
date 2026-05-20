"""Gestión de la configuración persistente (clave de Google Places, etc.).

Se guarda en webui/instance/settings.json con permisos 600.
"""

import json
import os
import tempfile

from . import paths


def _atomic_write(path, content: str) -> None:
    """
    Escritura atómica: escribimos a un tmp y renombramos. Evita que un
    crash a mitad de escritura deje el JSON corrupto.
    """
    dirpath = os.path.dirname(path) or "."
    fd, tmp = tempfile.mkstemp(prefix=".tmp_", dir=dirpath)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def load_settings() -> dict:
    if not paths.SETTINGS_PATH.exists():
        return {}
    try:
        with open(paths.SETTINGS_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return {}


def save_settings(data: dict) -> None:
    _atomic_write(str(paths.SETTINGS_PATH),
                  json.dumps(data, ensure_ascii=False, indent=2))


def get_api_key() -> str:
    """Prioridad: settings.json > variable de entorno."""
    s = load_settings()
    if s.get("google_places_api_key"):
        return s["google_places_api_key"]
    return os.environ.get("GOOGLE_PLACES_API_KEY", "")


def set_api_key(key: str) -> None:
    s = load_settings()
    if key:
        s["google_places_api_key"] = key
    else:
        s.pop("google_places_api_key", None)
    save_settings(s)


def mask_api_key(key: str) -> str:
    if not key:
        return ""
    if len(key) <= 10:
        return "•" * len(key)
    return key[:6] + "•" * (len(key) - 10) + key[-4:]


# -----------------------------------------------------------------------------
# Retención automática de jobs
# -----------------------------------------------------------------------------
#
# Política:
#   - retention_enabled = False  -> nunca se borra nada automáticamente
#   - retention_enabled = True   -> jobs (y sus archivos/logs) de más de
#     `retention_days` días se borran al pasar la limpieza periódica
#
# Defaults: deshabilitado, 90 días.

def get_retention() -> dict:
    s = load_settings()
    return {
        "enabled": bool(s.get("retention_enabled", False)),
        "days": int(s.get("retention_days", 90)),
    }


def set_retention(enabled: bool, days: int) -> None:
    """Guarda la configuración de retención. days se clampa a [1, 3650]."""
    s = load_settings()
    s["retention_enabled"] = bool(enabled)
    s["retention_days"] = max(1, min(int(days), 3650))
    save_settings(s)
