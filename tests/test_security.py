"""
Tests específicos de seguridad: CSRF, headers HTTP, rate-limiting.

Estos tests crean apps con CSRF y rate-limiting habilitados (a diferencia
del fixture `app` por defecto, donde están deshabilitados para no estorbar
en el resto de tests).
"""

import shutil
import tempfile
from pathlib import Path

import pytest


def _make_app_with_security(monkeypatch, enable_csrf=True, enable_ratelimit=True):
    """Helper: crea una app temporal con seguridad activa."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ppmsec_"))
    for sub in ("job_logs", "job_outputs", "extra_segments"):
        (tmpdir / sub).mkdir()

    from webui import paths as paths_mod
    monkeypatch.setattr(paths_mod, "INSTANCE_DIR", tmpdir)
    monkeypatch.setattr(paths_mod, "DB_PATH", tmpdir / "ppmailing.db")
    monkeypatch.setattr(paths_mod, "SETTINGS_PATH", tmpdir / "settings.json")
    monkeypatch.setattr(paths_mod, "SECRET_KEY_PATH", tmpdir / ".flask_secret")
    monkeypatch.setattr(paths_mod, "JOB_LOG_DIR", tmpdir / "job_logs")
    monkeypatch.setattr(paths_mod, "JOB_OUTPUTS_DIR", tmpdir / "job_outputs")
    monkeypatch.setattr(paths_mod, "EXTRA_SEGMENTS_DIR", tmpdir / "extra_segments")

    from webui.app import create_app
    app = create_app(test_config={
        "TESTING": True,
        "WTF_CSRF_ENABLED": enable_csrf,
        "RATELIMIT_ENABLED": enable_ratelimit,
    })
    return app, tmpdir


# -----------------------------------------------------------------------------
# CSRF
# -----------------------------------------------------------------------------

def test_csrf_blocks_post_without_token(monkeypatch):
    """Con CSRF activo, un POST sin token debe rechazarse con 400."""
    app, tmpdir = _make_app_with_security(monkeypatch, enable_csrf=True)
    try:
        c = app.test_client()
        # Login (Flask-WTF NO requiere token si el form no incluye uno con WTF;
        # pero CSRFProtect SÍ valida todos los POSTs salvo los exentos).
        r = c.post("/login", data={
            "username": "admin", "password": "admin",
        })
        # Esperamos 400 (CSRF token missing) en cualquier endpoint POST
        assert r.status_code == 400
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_csrf_accepts_post_with_valid_token(monkeypatch):
    """Con token válido, el POST se acepta."""
    app, tmpdir = _make_app_with_security(monkeypatch, enable_csrf=True)
    try:
        c = app.test_client()
        # GET para obtener un token
        r = c.get("/login")
        # Extraer csrf_token del HTML
        import re
        m = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode())
        assert m, "No se encontró el csrf_token en la página de login"
        token = m.group(1)

        r = c.post("/login", data={
            "csrf_token": token,
            "username": "admin",
            "password": "admin",
        }, follow_redirects=False)
        assert r.status_code == 302  # redirect tras login OK
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def test_csrf_blocks_logout_without_token(monkeypatch):
    """Logout también requiere CSRF token (anti-CSRF logout)."""
    app, tmpdir = _make_app_with_security(monkeypatch, enable_csrf=True)
    try:
        c = app.test_client()
        # Login con token válido primero
        r = c.get("/login")
        import re
        token = re.search(r'name="csrf_token" value="([^"]+)"', r.data.decode()).group(1)
        c.post("/login", data={
            "csrf_token": token,
            "username": "admin",
            "password": "admin",
        })
        # Intentar logout sin token
        r = c.post("/logout")
        assert r.status_code == 400
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# -----------------------------------------------------------------------------
# Headers de seguridad
# -----------------------------------------------------------------------------

def test_security_headers_set_on_response(client):
    """Todas las respuestas deben llevar los headers de seguridad básicos."""
    r = client.get("/login")
    assert r.headers.get("X-Content-Type-Options") == "nosniff"
    assert r.headers.get("X-Frame-Options") == "DENY"
    assert "Content-Security-Policy" in r.headers
    csp = r.headers["Content-Security-Policy"]
    assert "frame-ancestors 'none'" in csp
    assert "form-action 'self'" in csp
    assert r.headers.get("Referrer-Policy") == "same-origin"
    assert "Permissions-Policy" in r.headers


def test_hsts_header_set(client):
    """HSTS está presente (defense in depth aunque el proxy también lo añada)."""
    r = client.get("/login")
    hsts = r.headers.get("Strict-Transport-Security", "")
    assert "max-age=" in hsts


def test_session_cookie_config(app):
    """Las cookies de sesión están configuradas correctamente."""
    assert app.config["SESSION_COOKIE_HTTPONLY"] is True
    assert app.config["SESSION_COOKIE_SAMESITE"] == "Lax"


# -----------------------------------------------------------------------------
# Rate limit en login
# -----------------------------------------------------------------------------

def test_login_rate_limit(monkeypatch):
    """11 intentos rápidos de login → el último devuelve 429."""
    app, tmpdir = _make_app_with_security(
        monkeypatch, enable_csrf=False, enable_ratelimit=True,
    )
    try:
        c = app.test_client()
        # 10 intentos fallidos están permitidos (límite "10/minute")
        for i in range(10):
            r = c.post("/login", data={
                "username": f"baduser{i}",
                "password": "wrong",
            })
            assert r.status_code in (200, 302)
        # El 11º debe ser rate-limited
        r = c.post("/login", data={
            "username": "baduser11",
            "password": "wrong",
        })
        assert r.status_code == 429
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


# -----------------------------------------------------------------------------
# Tamaño máximo de petición
# -----------------------------------------------------------------------------

def test_request_too_large_rejected(admin_client):
    """Una petición POST mayor que 1MB se rechaza con 413."""
    huge = "x" * (2 * 1024 * 1024)  # 2MB
    r = admin_client.post("/segments/new", data={
        "id": "huge",
        "nombre_humano": "huge",
        "queries": huge,
    })
    # Flask devuelve 413 (Request Entity Too Large)
    assert r.status_code == 413
