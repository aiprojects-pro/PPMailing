"""
PPMailing — Application factory.

Esta es la entrada del paquete. Junta todos los blueprints y aplica la
configuración. Para arrancar:

  ./venv/bin/python -m webui.app

Variables de entorno:
  PPM_HOST           default 127.0.0.1
  PPM_PORT           default 5000
  PPM_DEBUG          1 para debug, 0 (default) para producción
  PPM_ADMIN_PASSWORD se usa SOLO en la primera ejecución (BD vacía).
  PPM_PROXIED        1 si está detrás de reverse proxy con HTTPS.
                     Activa SESSION_COOKIE_SECURE y ProxyFix.
"""

import logging
import os
import secrets
from datetime import timedelta
from pathlib import Path

from flask import Flask, render_template
from werkzeug.middleware.proxy_fix import ProxyFix

from . import admin, auth, dashboard, jobs, leads, mailgun_bp, paths, scheduling, segments
from .db import close_db, init_db, recover_orphan_jobs
from .security import init_security


# -----------------------------------------------------------------------------
# Secret key persistente
# -----------------------------------------------------------------------------

def _load_or_create_secret() -> bytes:
    # Leer del módulo paths dinámicamente para que los tests (monkeypatch)
    # puedan redirigir la ruta a un tmpdir.
    secret_path = paths.SECRET_KEY_PATH
    if secret_path.exists():
        return secret_path.read_bytes()
    key = secrets.token_bytes(32)
    secret_path.parent.mkdir(parents=True, exist_ok=True)
    secret_path.write_bytes(key)
    try:
        os.chmod(secret_path, 0o600)
    except OSError:
        pass
    return key


def create_app(test_config: dict | None = None) -> Flask:
    app = Flask(__name__, instance_path=str(paths.INSTANCE_DIR))

    # -------------------------------------------------------------------------
    # Configuración base
    # -------------------------------------------------------------------------
    proxied = os.environ.get("PPM_PROXIED", "0") == "1"

    app.config.update(
        SECRET_KEY=_load_or_create_secret(),
        SESSION_COOKIE_HTTPONLY=True,
        SESSION_COOKIE_SAMESITE="Lax",
        # Si está tras un reverse proxy HTTPS, fuerza cookies seguras
        SESSION_COOKIE_SECURE=proxied,
        # Sesión de 8 horas: equilibrio entre comodidad y exposición
        PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
        # CSRF tokens válidos durante 4 horas
        WTF_CSRF_TIME_LIMIT=4 * 3600,
        # Tamaño máximo de form (defensa contra DoS por upload)
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,  # 1 MB
        TESTING=False,
    )
    if test_config:
        app.config.update(test_config)

    # ProxyFix: si estamos detrás de Nginx/Caddy/Cloudflare, respetar X-Forwarded-*
    if proxied:
        app.wsgi_app = ProxyFix(  # type: ignore[assignment]
            app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1,
        )

    # -------------------------------------------------------------------------
    # Logging básico
    # -------------------------------------------------------------------------
    if not app.config.get("TESTING"):
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )

    # -------------------------------------------------------------------------
    # BD: init + recuperación de jobs huérfanos
    # -------------------------------------------------------------------------
    init_db(default_admin_password=os.environ.get("PPM_ADMIN_PASSWORD", "admin"))
    recovered = recover_orphan_jobs()
    if recovered:
        app.logger.warning(
            "Recuperados %d jobs huérfanos (marcados como error).", recovered,
        )

    app.teardown_appcontext(close_db)

    # -------------------------------------------------------------------------
    # Seguridad (CSRF + rate limiter + headers)
    # -------------------------------------------------------------------------
    init_security(app)

    # -------------------------------------------------------------------------
    # Blueprints
    # -------------------------------------------------------------------------
    app.register_blueprint(auth.bp)
    app.register_blueprint(dashboard.bp)
    app.register_blueprint(segments.bp)
    app.register_blueprint(jobs.bp)
    app.register_blueprint(leads.bp)
    app.register_blueprint(scheduling.bp)
    app.register_blueprint(mailgun_bp.bp)
    app.register_blueprint(admin.bp)

    # -------------------------------------------------------------------------
    # Context processor: expone `user` y flags globales a todas las plantillas
    # -------------------------------------------------------------------------
    @app.context_processor
    def inject_globals():
        from .mailgun import is_configured as mailgun_is_configured
        return {
            "user": auth.current_user(),
            "mailgun_configured": mailgun_is_configured(),
        }

    # -------------------------------------------------------------------------
    # Errors
    # -------------------------------------------------------------------------
    @app.errorhandler(400)
    def bad_request(_):
        return render_template("error.html", code=400,
                               message="Petición inválida."), 400

    @app.errorhandler(403)
    def forbidden(_):
        return render_template("error.html", code=403,
                               message="No tienes permiso para acceder a esto."), 403

    @app.errorhandler(404)
    def not_found(_):
        return render_template("error.html", code=404,
                               message="No se ha encontrado lo que buscas."), 404

    @app.errorhandler(429)
    def too_many(_):
        return render_template("error.html", code=429,
                               message="Demasiadas peticiones. Espera un momento."), 429

    @app.errorhandler(500)
    def internal(_):
        return render_template("error.html", code=500,
                               message="Error interno del servidor."), 500

    # Arrancar los workers en cuanto la app esté lista. Hacerlo aquí asegura
    # que también arrancan cuando la app se sirve por wsgi (gunicorn).
    # En modo TESTING NO los arrancamos: los tests pueden destruir la BD entre
    # ejecuciones y los workers seguirían intentando escribir en una BD que
    # ya no existe, ensuciando los logs con tracebacks. Los tests que
    # necesiten los workers los arrancan explícitamente.
    if not app.config.get("TESTING"):
        jobs.ensure_worker_running()
        jobs.ensure_retention_worker_running()
        scheduling.ensure_scheduler_running()

    return app


# -----------------------------------------------------------------------------
# Entry point
# -----------------------------------------------------------------------------

if __name__ == "__main__":
    app = create_app()
    host = os.environ.get("PPM_HOST", "127.0.0.1")
    port = int(os.environ.get("PPM_PORT", "5000"))
    debug = os.environ.get("PPM_DEBUG", "0") == "1"
    app.run(host=host, port=port, debug=debug, threaded=True)
