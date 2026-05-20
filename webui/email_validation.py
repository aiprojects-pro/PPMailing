"""
Validación de emails: MX (gratis, DNS), SMTP (gratis pero arriesgado),
Mailgun Email Validation API (de pago, fiable).

  - check_email_mx       → MX records vía nslookup. Default.
  - check_email_smtp     → handshake SMTP (HELO + MAIL FROM + RCPT TO).
  - check_email_mailgun  → llamada a Mailgun /v4/address/validate.

Cada función devuelve un dict con la misma forma:
    {
      "email": str,
      "status": str,         # ver lista abajo
      "reason": str,
      "checked_at": iso str,
      "method": str,         # 'mx' | 'smtp' | 'mailgun'
    }

Status posibles (alineados con el CHECK constraint de leads_master):
  ''             sin verificar
  'mx_ok'        MX records existen
  'mx_fail'      sin MX (dominio muerto, syntax incorrecta)
  'verified'    SMTP/Mailgun confirma que la dirección existe
  'invalid'     SMTP/Mailgun dice que no existe
  'catch_all'   Mailgun: el dominio acepta cualquier dirección
  'unknown'     Mailgun no pudo determinar
  'do_not_send' Mailgun: blacklist/spamtrap/role
"""

import re
import smtplib
import socket
import subprocess
from datetime import datetime


EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})$")


def _is_syntactically_valid(email: str) -> bool:
    """Comprobación rápida del formato del email."""
    if not email or len(email) > 254:
        return False
    return bool(EMAIL_RE.match(email))


def _has_mx_record(domain: str, timeout: float = 3.0) -> bool:
    """
    Devuelve True si el dominio tiene al menos un MX record o, en su defecto,
    un A record (fallback común: muchos dominios reciben mail directamente).

    Estrategia: usar `nslookup -type=MX` como subproceso. Es universal y no
    requiere dependencias. Si nslookup no está disponible, caemos a socket
    para resolver el A record (peor pero algo).
    """
    if not domain or "." not in domain:
        return False

    # Primer intento: nslookup MX
    try:
        proc = subprocess.run(
            ["nslookup", "-type=MX", domain],
            capture_output=True, text=True, timeout=timeout,
        )
        output = proc.stdout.lower()
        # nslookup output: "mail exchanger = mx.example.com"
        if "mail exchanger" in output or "mx preference" in output:
            return True
        # Si el dominio existe pero no tiene MX, muchos mailservers usan
        # el A record. Comprobamos eso también.
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass

    # Fallback: ¿tiene A record?
    socket.setdefaulttimeout(timeout)
    try:
        socket.gethostbyname(domain)
        return True
    except (socket.gaierror, socket.timeout, OSError):
        return False
    finally:
        socket.setdefaulttimeout(None)


def check_email_mx(email: str) -> dict:
    """
    Valida un email comprobando MX records de su dominio.

    Devuelve:
        {
          "email": <email>,
          "status": "mx_ok" | "mx_fail",
          "reason": <texto>,
          "checked_at": <iso timestamp>,
          "method": "mx",
        }

    No lanza excepciones; cualquier fallo se traduce en mx_fail con razón.
    """
    now = datetime.now().isoformat(timespec="seconds")

    if not _is_syntactically_valid(email):
        return {
            "email": email,
            "status": "mx_fail",
            "reason": "formato inválido",
            "checked_at": now,
            "method": "mx",
        }

    domain = email.split("@", 1)[1].lower().strip()
    if _has_mx_record(domain):
        return {
            "email": email,
            "status": "mx_ok",
            "reason": f"dominio {domain} resoluble",
            "checked_at": now,
            "method": "mx",
        }

    return {
        "email": email,
        "status": "mx_fail",
        "reason": f"dominio {domain} sin MX/A records",
        "checked_at": now,
        "method": "mx",
    }


def _get_mx_host(domain: str, timeout: float = 3.0) -> str | None:
    """Devuelve el primer MX host para un dominio, o None si no hay."""
    try:
        proc = subprocess.run(
            ["nslookup", "-type=MX", domain],
            capture_output=True, text=True, timeout=timeout,
        )
        for line in proc.stdout.splitlines():
            # nslookup output: "domain mail exchanger = N hostname."
            if "mail exchanger" in line.lower():
                parts = line.split("=")
                if len(parts) >= 2:
                    host = parts[1].strip().split()[-1].rstrip(".")
                    if host:
                        return host
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        pass
    return None


def check_email_smtp(email: str, timeout: float = 10.0,
                     hello_domain: str = "example.com",
                     from_address: str = "verify@example.com") -> dict:
    """
    Validación profunda vía handshake SMTP.

    Conecta al MX server del dominio, hace HELO + MAIL FROM + RCPT TO y
    lee la respuesta.

    ATENCIÓN:
      - Muchos servidores grey-listan o bloquean handshakes "huérfanos"
        (sin un MTA real detrás). Resultados pueden ser falsos positivos
        o falsos negativos en proveedores grandes (Gmail, Outlook).
      - El servidor puede aceptar TODO (catch-all): un OK no garantiza
        que la dirección sea real.
      - Recomendado solo para casos puntuales, no en lote masivo.

    Devuelve status 'verified', 'invalid' o 'unknown'.
    """
    now = datetime.now().isoformat(timespec="seconds")

    if not _is_syntactically_valid(email):
        return {
            "email": email, "status": "invalid", "reason": "formato inválido",
            "checked_at": now, "method": "smtp",
        }

    domain = email.split("@", 1)[1].lower().strip()
    mx_host = _get_mx_host(domain, timeout=3.0)
    if mx_host is None:
        # Fallback: el dominio mismo
        if _has_mx_record(domain):
            mx_host = domain
        else:
            return {
                "email": email, "status": "invalid",
                "reason": f"dominio {domain} sin MX",
                "checked_at": now, "method": "smtp",
            }

    try:
        with smtplib.SMTP(mx_host, 25, timeout=timeout) as smtp:
            smtp.set_debuglevel(0)
            code, _ = smtp.ehlo(hello_domain)
            if code >= 400:
                code, _ = smtp.helo(hello_domain)
                if code >= 400:
                    return {
                        "email": email, "status": "unknown",
                        "reason": f"servidor rechazó HELO ({code})",
                        "checked_at": now, "method": "smtp",
                    }
            code, _ = smtp.mail(from_address)
            if code >= 400:
                return {
                    "email": email, "status": "unknown",
                    "reason": f"MAIL FROM rechazado ({code})",
                    "checked_at": now, "method": "smtp",
                }
            code, msg = smtp.rcpt(email)
            try:
                smtp.quit()
            except Exception:
                pass
            if 200 <= code < 300:
                return {
                    "email": email, "status": "verified",
                    "reason": f"RCPT OK ({code})",
                    "checked_at": now, "method": "smtp",
                }
            elif code in (550, 551, 553):
                return {
                    "email": email, "status": "invalid",
                    "reason": f"dirección rechazada ({code})",
                    "checked_at": now, "method": "smtp",
                }
            else:
                return {
                    "email": email, "status": "unknown",
                    "reason": f"código inesperado {code}: {msg!r}"[:200],
                    "checked_at": now, "method": "smtp",
                }
    except (socket.timeout, socket.gaierror, ConnectionRefusedError,
            smtplib.SMTPException, OSError) as exc:
        return {
            "email": email, "status": "unknown",
            "reason": f"error de conexión: {exc}"[:200],
            "checked_at": now, "method": "smtp",
        }


def check_email_mailgun(email: str, api_key: str,
                        base_url: str = "https://api.mailgun.net",
                        timeout: float = 10.0) -> dict:
    """
    Validación vía Mailgun Email Validation API.

    Endpoint: GET {base_url}/v4/address/validate?address=<email>
    Auth: HTTP Basic con username='api' y password=<api_key>.

    Mailgun devuelve un campo `result` con valores:
      deliverable      → mapeamos a 'verified'
      undeliverable    → 'invalid'
      do_not_send      → 'do_not_send' (blacklist, role, spamtrap...)
      catch_all        → 'catch_all'
      unknown          → 'unknown'

    Si la llamada falla, devolvemos status 'unknown' con la razón.
    """
    import requests

    now = datetime.now().isoformat(timespec="seconds")
    if not _is_syntactically_valid(email):
        return {
            "email": email, "status": "invalid", "reason": "formato inválido",
            "checked_at": now, "method": "mailgun",
        }

    url = base_url.rstrip("/") + "/v4/address/validate"
    try:
        r = requests.get(
            url,
            params={"address": email},
            auth=("api", api_key),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        return {
            "email": email, "status": "unknown",
            "reason": f"error de red: {exc}"[:200],
            "checked_at": now, "method": "mailgun",
        }

    if r.status_code == 401:
        return {
            "email": email, "status": "unknown",
            "reason": "API key de Mailgun inválida",
            "checked_at": now, "method": "mailgun",
        }
    if r.status_code != 200:
        return {
            "email": email, "status": "unknown",
            "reason": f"Mailgun devolvió {r.status_code}",
            "checked_at": now, "method": "mailgun",
        }

    try:
        data = r.json()
    except ValueError:
        return {
            "email": email, "status": "unknown", "reason": "respuesta no JSON",
            "checked_at": now, "method": "mailgun",
        }

    result_map = {
        "deliverable": "verified",
        "undeliverable": "invalid",
        "do_not_send": "do_not_send",
        "catch_all": "catch_all",
        "unknown": "unknown",
    }
    raw_result = data.get("result", "unknown")
    status = result_map.get(raw_result, "unknown")
    risk = data.get("risk", "")
    reason = f"Mailgun: {raw_result}"
    if risk:
        reason += f" (riesgo {risk})"

    return {
        "email": email, "status": status, "reason": reason,
        "checked_at": now, "method": "mailgun",
    }
