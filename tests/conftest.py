"""
Fixtures comunes para los tests.

Cada test arranca con un directorio `instance/` fresco para no interferir
con otros tests ni con datos reales.

Truco clave: monkeypatcheamos las constantes del módulo `webui.paths`,
y todos los demás módulos las leen dinámicamente (vía `paths.DB_PATH`,
no `from .paths import DB_PATH`), así que el redireccionamiento funciona.
"""

import shutil
import sys
import tempfile
from pathlib import Path

import pytest


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture
def app(monkeypatch):
    """Crea una app Flask con un INSTANCE_DIR temporal aislado."""
    tmpdir = Path(tempfile.mkdtemp(prefix="ppmtest_"))

    # Crear la estructura que la app espera
    job_logs = tmpdir / "job_logs"
    job_logs.mkdir()
    job_outputs = tmpdir / "job_outputs"
    job_outputs.mkdir()
    extras = tmpdir / "extra_segments"
    extras.mkdir()

    # Parchear el módulo paths — todos los demás módulos leen de aquí
    from webui import paths as paths_mod
    monkeypatch.setattr(paths_mod, "INSTANCE_DIR", tmpdir)
    monkeypatch.setattr(paths_mod, "DB_PATH", tmpdir / "ppmailing.db")
    monkeypatch.setattr(paths_mod, "SETTINGS_PATH", tmpdir / "settings.json")
    monkeypatch.setattr(paths_mod, "SECRET_KEY_PATH", tmpdir / ".flask_secret")
    monkeypatch.setattr(paths_mod, "JOB_LOG_DIR", job_logs)
    monkeypatch.setattr(paths_mod, "JOB_OUTPUTS_DIR", job_outputs)
    monkeypatch.setattr(paths_mod, "EXTRA_SEGMENTS_DIR", extras)

    from webui.app import create_app
    app = create_app(test_config={
        "TESTING": True,
        "WTF_CSRF_ENABLED": False,   # CSRF se prueba en test_security.py
        "RATELIMIT_ENABLED": False,
    })

    yield app

    shutil.rmtree(tmpdir, ignore_errors=True)


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture
def admin_client(client):
    """Cliente ya autenticado como admin."""
    client.post("/login", data={"username": "admin", "password": "admin"})
    return client


@pytest.fixture
def user_client(app, admin_client):
    """Crea un usuario normal y devuelve un cliente autenticado como él."""
    admin_client.post("/admin/users/new", data={
        "username": "alice",
        "password": "alicealice",
        "role": "user",
    })
    c = app.test_client()
    c.post("/login", data={"username": "alice", "password": "alicealice"})
    return c


@pytest.fixture
def second_user_client(app, admin_client):
    """Segundo usuario para probar aislamiento entre cuentas."""
    admin_client.post("/admin/users/new", data={
        "username": "bob",
        "password": "bobbobbob",
        "role": "user",
    })
    c = app.test_client()
    c.post("/login", data={"username": "bob", "password": "bobbobbob"})
    return c
