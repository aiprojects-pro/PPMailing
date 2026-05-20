"""
Tests del borrado manual de jobs y de la política de retención automática.
"""

from datetime import datetime, timedelta


def _create_job_in_db(app, status="done", days_ago=0, user_id=None, job_id="testjob1234"):
    """Helper: crea un job sintético con un queued_at controlado.

    Útil para probar la retención sin tener que esperar días.
    """
    from webui.db import new_connection
    queued_at = (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")
    finished_at = queued_at if status in ("done", "error", "cancelled") else ""
    conn = new_connection()
    try:
        if user_id is None:
            row = conn.execute("SELECT id FROM users WHERE username = 'admin'").fetchone()
            user_id = row["id"]
        conn.execute(
            "INSERT INTO jobs (id, user_id, segmento, ambito, max_paginas, "
            "status, queued_at, finished_at) "
            "VALUES (?, ?, 'admin_fincas', 'andalucia', 1, ?, ?, ?)",
            (job_id, user_id, status, queued_at, finished_at),
        )
    finally:
        conn.close()
    return job_id


# -----------------------------------------------------------------------------
# Borrado manual
# -----------------------------------------------------------------------------

def test_admin_can_delete_finished_job(app, admin_client):
    """Admin puede borrar un job terminado y sus archivos asociados."""
    from webui import paths
    job_id = _create_job_in_db(app, status="done")

    # Crear archivos asociados para verificar que se borran
    log_path = paths.JOB_LOG_DIR / f"{job_id}.log"
    log_path.write_text("log content")
    job_dir = paths.JOB_OUTPUTS_DIR / job_id
    job_dir.mkdir(exist_ok=True)
    (job_dir / "leads.csv").write_text("a,b,c")

    r = admin_client.post(f"/jobs/{job_id}/delete", follow_redirects=False)
    assert r.status_code == 302

    # Comprobar que se borró todo
    from webui.db import new_connection
    conn = new_connection()
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row is None, "El job sigue en la BD tras el borrado"
    assert not log_path.exists(), "El log del job no se borró"
    assert not job_dir.exists(), "La carpeta de outputs del job no se borró"


def test_user_can_delete_own_job(app, admin_client, user_client):
    """Un user puede borrar SUS PROPIOS jobs."""
    from webui.db import new_connection
    conn = new_connection()
    alice_id = conn.execute(
        "SELECT id FROM users WHERE username = 'alice'"
    ).fetchone()["id"]
    conn.close()

    job_id = _create_job_in_db(app, status="done", user_id=alice_id, job_id="alicejob1234")

    r = user_client.post(f"/jobs/{job_id}/delete", follow_redirects=False)
    assert r.status_code == 302  # OK, redirige al dashboard


def test_user_cannot_delete_other_users_job(app, user_client, second_user_client):
    """Alice NO puede borrar un job de Bob."""
    from webui.db import new_connection
    conn = new_connection()
    bob_id = conn.execute(
        "SELECT id FROM users WHERE username = 'bob'"
    ).fetchone()["id"]
    conn.close()

    job_id = _create_job_in_db(app, status="done", user_id=bob_id, job_id="bobjob1234")

    r = user_client.post(f"/jobs/{job_id}/delete")
    assert r.status_code == 403

    # Confirmar que el job sigue ahí
    conn = new_connection()
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row is not None


def test_admin_can_delete_any_users_job(app, admin_client, user_client):
    """Admin puede borrar jobs de cualquier usuario."""
    from webui.db import new_connection
    conn = new_connection()
    alice_id = conn.execute(
        "SELECT id FROM users WHERE username = 'alice'"
    ).fetchone()["id"]
    conn.close()

    job_id = _create_job_in_db(app, status="done", user_id=alice_id, job_id="alicejob5678")

    r = admin_client.post(f"/jobs/{job_id}/delete", follow_redirects=False)
    assert r.status_code == 302

    conn = new_connection()
    row = conn.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone()
    conn.close()
    assert row is None


def test_cannot_delete_running_job(app, admin_client):
    """No se puede borrar un job en estado running ni pending."""
    job_id_running = _create_job_in_db(app, status="running", job_id="runningjob01")
    r = admin_client.post(f"/jobs/{job_id_running}/delete", follow_redirects=True)
    body = r.data.decode()
    assert "No se puede borrar" in body or "en curso" in body

    job_id_pending = _create_job_in_db(app, status="pending", job_id="pendingjob01")
    r = admin_client.post(f"/jobs/{job_id_pending}/delete", follow_redirects=True)
    body = r.data.decode()
    assert "No se puede borrar" in body or "en cola" in body

    # Ambos deben seguir en BD
    from webui.db import new_connection
    conn = new_connection()
    rows = conn.execute(
        "SELECT id FROM jobs WHERE id IN ('runningjob01', 'pendingjob01')"
    ).fetchall()
    conn.close()
    assert len(rows) == 2


def test_delete_nonexistent_job_returns_404(admin_client):
    r = admin_client.post("/jobs/doesnotexist/delete")
    assert r.status_code == 404


def test_delete_job_function_is_idempotent(app):
    """Llamar delete_job dos veces no falla."""
    from webui.jobs import delete_job
    from webui import paths

    job_id = _create_job_in_db(app, status="done", job_id="idempotent01")
    log_path = paths.JOB_LOG_DIR / f"{job_id}.log"
    log_path.write_text("x")

    # Primera llamada: borra
    assert delete_job(job_id) is True
    # Segunda llamada: no encuentra nada, no falla
    assert delete_job(job_id) is False


# -----------------------------------------------------------------------------
# Retención automática
# -----------------------------------------------------------------------------

def test_retention_defaults_to_disabled(app):
    from webui.settings import get_retention
    cfg = get_retention()
    assert cfg["enabled"] is False
    assert cfg["days"] == 90


def test_admin_can_enable_retention(app, admin_client):
    r = admin_client.post("/admin/retention", data={
        "retention_enabled": "1",
        "retention_days": "30",
    }, follow_redirects=True)
    assert r.status_code == 200

    from webui.settings import get_retention
    cfg = get_retention()
    assert cfg["enabled"] is True
    assert cfg["days"] == 30


def test_admin_can_disable_retention(app, admin_client):
    # Primero activar
    admin_client.post("/admin/retention",
                      data={"retention_enabled": "1", "retention_days": "10"})
    # Luego desactivar (sin el checkbox)
    admin_client.post("/admin/retention", data={"retention_days": "10"})

    from webui.settings import get_retention
    cfg = get_retention()
    assert cfg["enabled"] is False


def test_retention_days_validated(admin_client):
    """Días fuera de rango deben rechazarse."""
    r = admin_client.post("/admin/retention", data={
        "retention_enabled": "1", "retention_days": "0",
    }, follow_redirects=True)
    assert "mínima es de 1" in r.data.decode()

    r = admin_client.post("/admin/retention", data={
        "retention_enabled": "1", "retention_days": "9999",
    }, follow_redirects=True)
    assert b"3650" in r.data


def test_user_cannot_change_retention(user_client):
    r = user_client.post("/admin/retention", data={
        "retention_enabled": "1", "retention_days": "30",
    })
    assert r.status_code == 403


def test_cleanup_old_jobs_removes_old_terminal_jobs(app):
    """REGRESIÓN: cleanup_old_jobs borra solo los jobs antiguos en estado terminal."""
    from webui.jobs import cleanup_old_jobs

    # 3 jobs viejos en estados terminales -> deben borrarse
    _create_job_in_db(app, status="done",      days_ago=100, job_id="old_done_01")
    _create_job_in_db(app, status="error",     days_ago=100, job_id="old_err__01")
    _create_job_in_db(app, status="cancelled", days_ago=100, job_id="old_canc_01")

    # 2 jobs nuevos -> deben mantenerse
    _create_job_in_db(app, status="done",  days_ago=5, job_id="new_done_01")
    _create_job_in_db(app, status="error", days_ago=5, job_id="new_err__01")

    # 2 jobs viejos pero en estado no terminal -> deben mantenerse
    # (en la práctica no deberían existir gracias a recover_orphan_jobs,
    # pero la lógica de cleanup tiene que respetarlo).
    _create_job_in_db(app, status="running", days_ago=100, job_id="old_run__01")
    _create_job_in_db(app, status="pending", days_ago=100, job_id="old_pend_01")

    n = cleanup_old_jobs(retention_days=30)
    assert n == 3, f"Esperados 3 borrados, fueron {n}"

    from webui.db import new_connection
    conn = new_connection()
    remaining = {r["id"] for r in conn.execute("SELECT id FROM jobs").fetchall()}
    conn.close()

    # Los 3 viejos terminales NO están
    assert "old_done_01" not in remaining
    assert "old_err__01" not in remaining
    assert "old_canc_01" not in remaining
    # Los nuevos SÍ
    assert "new_done_01" in remaining
    assert "new_err__01" in remaining
    # Los running/pending viejos también SÍ (no se tocan)
    assert "old_run__01" in remaining
    assert "old_pend_01" in remaining


def test_cleanup_also_removes_files(app):
    """Cuando cleanup_old_jobs borra un job, debe borrar también log y carpeta."""
    from webui import paths
    from webui.jobs import cleanup_old_jobs

    job_id = _create_job_in_db(app, status="done", days_ago=100, job_id="cleanupfiles")
    log = paths.JOB_LOG_DIR / f"{job_id}.log"
    log.write_text("x")
    out = paths.JOB_OUTPUTS_DIR / job_id
    out.mkdir(exist_ok=True)
    (out / "leads.csv").write_text("a")

    cleanup_old_jobs(retention_days=30)

    assert not log.exists()
    assert not out.exists()


def test_cleanup_with_zero_days_returns_zero(app):
    """Días inválidos no provocan borrados destructivos."""
    from webui.jobs import cleanup_old_jobs
    _create_job_in_db(app, status="done", days_ago=1000, job_id="paranoid_test")
    assert cleanup_old_jobs(retention_days=0) == 0
    assert cleanup_old_jobs(retention_days=-1) == 0
