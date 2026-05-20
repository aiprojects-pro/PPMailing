"""
Componentes de seguridad reutilizables.

Centraliza:
  - CSRF (Flask-WTF)
  - Rate limiting (Flask-Limiter)
  - Headers de seguridad (after_request)

Los componentes se exponen como singletons para que los blueprints los importen.
La inicialización contra `app` la hace `webui.app.create_app`.
"""

from flask import Flask
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from flask_wtf.csrf import CSRFProtect


csrf = CSRFProtect()

# storage_uri='memory://' está bien para 2-5 usuarios. Para producción seria,
# usar redis://. El warning de Flask-Limiter en stderr es esperado.
limiter = Limiter(
    key_func=get_remote_address,
    default_limits=[],
    storage_uri="memory://",
    headers_enabled=False,
)


# -----------------------------------------------------------------------------
# Headers de seguridad: aplicados a TODA respuesta
# -----------------------------------------------------------------------------

# CSP estricto: sólo se carga del propio origen y se permite 'unsafe-inline'
# para los scripts y estilos que vienen embebidos en las plantillas. Esto es
# un compromiso: idealmente moveríamos el JS a archivos externos y usaríamos
# hashes/nonces, pero para una UI mínima 'unsafe-inline' es aceptable.
CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'; "
    "form-action 'self'"
)


def _add_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("Content-Security-Policy", CSP)
    response.headers.setdefault("Permissions-Policy",
                                "geolocation=(), camera=(), microphone=()")
    # HSTS solo tiene sentido si la app se sirve por HTTPS. El reverse proxy
    # debería añadirlo, pero por defensa en profundidad lo declaramos aquí
    # también con max-age moderado.
    response.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=15552000; includeSubDomains",  # 180 días
    )
    return response


def init_security(app: Flask) -> None:
    """Inicializa CSRF, rate limiter y headers."""
    csrf.init_app(app)
    limiter.init_app(app)
    app.after_request(_add_security_headers)
