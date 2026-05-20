"""Tests del job runner: estimación de coste, autorización entre usuarios."""


def test_cost_estimation_endpoint(admin_client):
    r = admin_client.get("/jobs/estimate?segmento=campamentos_verano&ambito=andalucia&max_paginas=1")
    assert r.status_code == 200
    data = r.get_json()
    assert "cost_eur" in data
    assert "text_searches" in data
    assert data["text_searches"] > 0


def test_cost_estimation_unknown_segment(admin_client):
    r = admin_client.get("/jobs/estimate?segmento=nonexistent&ambito=espana&max_paginas=1")
    data = r.get_json()
    # Devuelve 0 si no conoce el segmento (no crashea)
    assert data["text_searches"] == 0


def test_job_rejected_without_api_key(admin_client):
    r = admin_client.post("/jobs/new", data={
        "segmento": "admin_fincas",
        "ambito": "andalucia",
        "max_paginas": "1",
    }, follow_redirects=True)
    assert b"Google Places" in r.data


def test_job_requires_high_cost_confirmation(admin_client):
    # Configurar API key primero
    admin_client.post("/admin/settings",
                       data={"google_places_api_key": "TESTKEY"})
    # Lanzar algo caro (>20€)
    r = admin_client.post("/jobs/new", data={
        "segmento": "clubes_deportivos",
        "ambito": "espana",
        "max_paginas": "3",
    }, follow_redirects=True)
    # Sin checkbox -> rechazado
    assert b"confirmaci" in r.data or b"Marca la casilla" in r.data


def test_job_with_confirmation_accepted(admin_client):
    admin_client.post("/admin/settings",
                       data={"google_places_api_key": "TESTKEY"})
    r = admin_client.post("/jobs/new", data={
        "segmento": "clubes_deportivos",
        "ambito": "espana",
        "max_paginas": "3",
        "confirm_high_cost": "1",
    }, follow_redirects=False)
    # 302 a /jobs/<id>
    assert r.status_code == 302
    assert "/jobs/" in r.headers["Location"]


def test_user_cannot_view_other_users_job(app, admin_client, user_client, second_user_client):
    """Alice no puede ver el job de Bob (403)."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})

    # Bob lanza una búsqueda barata
    r = second_user_client.post("/jobs/new", data={
        "segmento": "admin_fincas",
        "ambito": "andalucia",
        "max_paginas": "1",
    }, follow_redirects=False)
    # Sacar el job_id del Location
    job_id = r.headers["Location"].rsplit("/", 1)[-1]

    # Alice intenta verlo
    assert user_client.get(f"/jobs/{job_id}").status_code == 403
    assert user_client.get(f"/api/jobs/{job_id}").status_code == 403
    assert user_client.get(f"/download/{job_id}/csv").status_code == 403


def test_admin_can_view_all_jobs(app, admin_client, user_client):
    """Admin SÍ puede ver jobs de cualquier usuario."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})

    # Alice lanza un job
    r = user_client.post("/jobs/new", data={
        "segmento": "admin_fincas",
        "ambito": "andalucia",
        "max_paginas": "1",
    }, follow_redirects=False)
    job_id = r.headers["Location"].rsplit("/", 1)[-1]

    # Admin puede ver el detalle
    assert admin_client.get(f"/jobs/{job_id}").status_code == 200
    # Y la API
    r = admin_client.get(f"/api/jobs/{job_id}")
    assert r.status_code == 200
    data = r.get_json()
    assert data["id"] == job_id


def test_unknown_job_returns_404(admin_client):
    assert admin_client.get("/jobs/nonexistent12").status_code == 404
    assert admin_client.get("/api/jobs/nonexistent12").status_code == 404
    assert admin_client.get("/download/nonexistent12/csv").status_code == 404


def test_unknown_download_type_returns_404(app, admin_client):
    """Tipo de descarga inválido devuelve 404, no abre fichero arbitrario."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})
    r = admin_client.post("/jobs/new", data={
        "segmento": "admin_fincas",
        "ambito": "andalucia",
        "max_paginas": "1",
    }, follow_redirects=False)
    job_id = r.headers["Location"].rsplit("/", 1)[-1]

    # 'evil' no es csv/json/enriched -> 404
    r = admin_client.get(f"/download/{job_id}/evil")
    assert r.status_code == 404


def test_budget_limit_enforced(app, admin_client):
    """Si el user tiene presupuesto, no puede pasarlo."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})
    # Crear user con presupuesto pequeño
    admin_client.post("/admin/users/new", data={
        "username": "broke",
        "password": "brokebroke",
        "role": "user",
        "budget_eur_monthly": "5",  # solo 5€/mes
    })
    c = app.test_client()
    c.post("/login", data={"username": "broke", "password": "brokebroke"})

    # Lanzar algo caro (>5€)
    r = c.post("/jobs/new", data={
        "segmento": "clubes_deportivos",
        "ambito": "espana",
        "max_paginas": "3",
        "confirm_high_cost": "1",
    }, follow_redirects=True)
    assert b"presupuesto" in r.data


def test_orphan_jobs_recovered_on_restart(app):
    """REGRESIÓN: al reiniciar, los jobs 'running' deben marcarse como error."""
    from webui.db import new_connection, recover_orphan_jobs
    conn = new_connection()
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('ORPHAN1', 1, 'admin_fincas', 'andalucia', 'running', '2024-01-01')"
    )
    conn.execute(
        "INSERT INTO jobs (id, user_id, segmento, ambito, status, queued_at) "
        "VALUES ('ORPHAN2', 1, 'admin_fincas', 'andalucia', 'pending', '2024-01-01')"
    )
    conn.close()

    n = recover_orphan_jobs()
    assert n == 2

    conn = new_connection()
    rows = conn.execute(
        "SELECT id, status, message FROM jobs WHERE id IN ('ORPHAN1', 'ORPHAN2')"
    ).fetchall()
    conn.close()
    for r in rows:
        assert r["status"] == "error"
        assert "reinicio" in r["message"].lower() or "interrump" in r["message"].lower()
