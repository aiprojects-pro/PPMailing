"""
Cliente Mailgun: gestión de mailing lists, push de leads, verificación
de firmas de webhooks.

NO importa el SDK oficial de Mailgun. Usamos requests + Basic Auth. La
API de Mailgun es REST sencillo, no merece la pena la dependencia.

Endpoints relevantes:
  - GET    /v3/lists                                  → listar mailing lists
  - POST   /v3/lists                                  → crear lista
  - POST   /v3/lists/<address>/members                → añadir un miembro
  - POST   /v3/lists/<address>/members.json           → batch add (hasta 1000)
  - DELETE /v3/lists/<address>/members/<address>     → remover miembro

  - GET    /v4/address/validate?address=...           → validation API (en email_validation.py)

  - Webhooks: nos llegan a /webhooks/mailgun firmados con HMAC-SHA256.
    Hay que verificar signature ∈ POST body.
"""

import hashlib
import hmac
import json

import requests

from .settings import load_settings


def _get_config() -> dict:
    """Obtiene la config de Mailgun desde settings.json."""
    settings = load_settings()
    mg = settings.get("mailgun") or {}
    return {
        "api_key": mg.get("api_key", ""),
        "domain": mg.get("domain", ""),
        "base_url": mg.get("base_url", "https://api.mailgun.net"),
        "webhook_signing_key": mg.get("webhook_signing_key", ""),
    }


def is_configured() -> bool:
    cfg = _get_config()
    return bool(cfg["api_key"] and cfg["domain"])


# -----------------------------------------------------------------------------
# Mailing lists
# -----------------------------------------------------------------------------

def list_mailing_lists() -> tuple[list, str | None]:
    """
    Devuelve (lista_de_listas, error). Cada lista tiene al menos:
      {"address": "name@mg.midominio.com", "name": "...", "members_count": N}
    """
    cfg = _get_config()
    if not is_configured():
        return [], "Mailgun no configurado"
    try:
        r = requests.get(
            f"{cfg['base_url']}/v3/lists/pages",
            auth=("api", cfg["api_key"]),
            timeout=15,
        )
    except requests.RequestException as exc:
        return [], f"Error de red: {exc}"
    if r.status_code == 401:
        return [], "API key inválida"
    if r.status_code != 200:
        return [], f"Mailgun devolvió {r.status_code}: {r.text[:200]}"
    try:
        return r.json().get("items", []), None
    except ValueError:
        return [], "Respuesta no JSON"


def create_mailing_list(address: str, name: str = "",
                        description: str = "") -> tuple[dict | None, str | None]:
    """
    Crea una mailing list en Mailgun.

    `address` debe tener la forma `algo@mg.midominio.com`.
    Devuelve (data, error). `data` contiene info de la lista creada.
    """
    cfg = _get_config()
    if not is_configured():
        return None, "Mailgun no configurado"
    try:
        r = requests.post(
            f"{cfg['base_url']}/v3/lists",
            auth=("api", cfg["api_key"]),
            data={
                "address": address,
                "name": name or address,
                "description": description,
                "access_level": "readonly",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        return None, f"Error de red: {exc}"
    if r.status_code == 200:
        return r.json().get("list", {}), None
    if r.status_code == 400 and "already exists" in r.text.lower():
        return None, "Esa dirección de lista ya existe"
    return None, f"Mailgun devolvió {r.status_code}: {r.text[:200]}"


def push_members(list_address: str, members: list[dict]) -> tuple[int, str | None]:
    """
    Añade miembros en lote a una mailing list (hasta 1000 por request).
    Cada miembro: {"address": "x@y.com", "name": "...", "vars": {...}}.

    Devuelve (num_pushed, error). Si hay más de 1000, se hacen chunks.
    Mailgun re-acepta miembros existentes sin error (upsert con
    upsert=true).
    """
    cfg = _get_config()
    if not is_configured():
        return 0, "Mailgun no configurado"

    total = 0
    chunk_size = 1000
    for i in range(0, len(members), chunk_size):
        chunk = members[i:i + chunk_size]
        try:
            r = requests.post(
                f"{cfg['base_url']}/v3/lists/{list_address}/members.json",
                auth=("api", cfg["api_key"]),
                data={
                    "members": json.dumps(chunk, ensure_ascii=False),
                    "upsert": "yes",
                },
                timeout=30,
            )
        except requests.RequestException as exc:
            return total, f"Error de red tras {total}: {exc}"
        if r.status_code != 200:
            return total, (
                f"Mailgun devolvió {r.status_code} tras {total}: {r.text[:200]}"
            )
        total += len(chunk)
    return total, None


# -----------------------------------------------------------------------------
# Webhook signature verification
# -----------------------------------------------------------------------------

def verify_webhook_signature(timestamp: str, token: str,
                             signature: str) -> bool:
    """
    Verifica que un webhook viene realmente de Mailgun.

    Mailgun firma cada webhook con HMAC-SHA256 usando la "webhook signing
    key" (distinta de la API key principal). El payload firmado es:
        timestamp + token

    Sin esta verificación, cualquiera podría POST a /webhooks/mailgun
    y manipular el estado de los leads.

    Devuelve True si la firma es válida.
    """
    cfg = _get_config()
    signing_key = cfg["webhook_signing_key"]
    if not signing_key or not timestamp or not token or not signature:
        return False
    try:
        expected = hmac.new(
            key=signing_key.encode("utf-8"),
            msg=(str(timestamp) + str(token)).encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, signature)
    except Exception:
        return False


# -----------------------------------------------------------------------------
# Settings setter (usado por panel admin)
# -----------------------------------------------------------------------------

def set_mailgun_config(api_key: str, domain: str,
                      base_url: str = "https://api.mailgun.net",
                      webhook_signing_key: str = "") -> None:
    """Persiste la configuración de Mailgun en settings.json."""
    from .settings import load_settings, save_settings
    settings = load_settings()
    mg = settings.get("mailgun") or {}

    # Sólo sobreescribimos campos no vacíos para no borrar accidentalmente
    if api_key:
        mg["api_key"] = api_key
    if domain:
        mg["domain"] = domain
    if base_url:
        mg["base_url"] = base_url
    if webhook_signing_key:
        mg["webhook_signing_key"] = webhook_signing_key

    settings["mailgun"] = mg
    save_settings(settings)


def clear_mailgun_config() -> None:
    from .settings import load_settings, save_settings
    settings = load_settings()
    settings.pop("mailgun", None)
    save_settings(settings)


def mask_mailgun_config() -> dict:
    """Devuelve la config enmascarada (para mostrar en admin)."""
    from .settings import mask_api_key
    cfg = _get_config()
    return {
        "api_key_masked": mask_api_key(cfg["api_key"]),
        "domain": cfg["domain"],
        "base_url": cfg["base_url"],
        "webhook_signing_key_masked": mask_api_key(cfg["webhook_signing_key"]),
        "configured": is_configured(),
    }
