"""Tests de búsqueda por radio (validación, estimación, creación de jobs)."""

import json
import pytest


def test_validate_points_empty_list():
    from webui.places_radius import validate_points
    points, errors = validate_points([])
    assert points == []
    assert errors == ["Debes indicar al menos un punto."]


def test_validate_points_too_many():
    from webui.places_radius import validate_points
    pts = [{"latitude": 40, "longitude": -3, "radius": 1000} for _ in range(21)]
    points, errors = validate_points(pts)
    assert points == []
    assert any("Máximo 20" in e for e in errors)


def test_validate_points_invalid_lat():
    from webui.places_radius import validate_points
    points, errors = validate_points([{"latitude": 91, "longitude": -3, "radius": 1000}])
    assert points == []
    assert any("latitud" in e for e in errors)


def test_validate_points_invalid_lng():
    from webui.places_radius import validate_points
    points, errors = validate_points([{"latitude": 40, "longitude": 200, "radius": 1000}])
    assert points == []
    assert any("longitud" in e for e in errors)


def test_validate_points_radius_too_small():
    from webui.places_radius import validate_points
    points, errors = validate_points([{"latitude": 40, "longitude": -3, "radius": 50}])
    assert points == []
    assert any("radio" in e.lower() for e in errors)


def test_validate_points_radius_too_large():
    from webui.places_radius import validate_points
    points, errors = validate_points([{"latitude": 40, "longitude": -3, "radius": 100000}])
    assert points == []
    assert any("radio" in e.lower() for e in errors)


def test_validate_points_valid():
    from webui.places_radius import validate_points
    points, errors = validate_points([
        {"latitude": 40.4168, "longitude": -3.7038, "radius": 5000, "label": "Madrid"},
        {"latitude": 41.3851, "longitude": 2.1734, "radius": 3000},
    ])
    assert errors == []
    assert len(points) == 2
    assert points[0]["label"] == "Madrid"
    assert points[1]["label"] == "punto_2"  # auto-generado


def test_validate_points_non_numeric():
    from webui.places_radius import validate_points
    points, errors = validate_points([
        {"latitude": "no_a_number", "longitude": -3, "radius": 1000}
    ])
    assert points == []
    assert any("numéricos" in e for e in errors)


# -----------------------------------------------------------------------------
# Endpoint: estimar coste
# -----------------------------------------------------------------------------

def test_estimate_radius_endpoint(admin_client):
    r = admin_client.get(
        "/jobs/estimate-radius?segmento=admin_fincas&num_points=3&max_paginas=2"
    )
    assert r.status_code == 200
    data = r.get_json()
    assert "cost_eur" in data
    assert "text_searches" in data
    assert data["text_searches"] > 0


def test_estimate_radius_unknown_segment(admin_client):
    r = admin_client.get(
        "/jobs/estimate-radius?segmento=nonexistent&num_points=1&max_paginas=1"
    )
    data = r.get_json()
    assert data["text_searches"] == 0


# -----------------------------------------------------------------------------
# Endpoint: crear job de radio
# -----------------------------------------------------------------------------

def test_create_radius_job(admin_client):
    """Crear un job de radio con un punto válido."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})

    points = [{"latitude": 40.4168, "longitude": -3.7038, "radius": 5000, "label": "Madrid"}]
    r = admin_client.post("/jobs/new-radius", data={
        "segmento": "admin_fincas",
        "max_paginas": "1",
        "points_json": json.dumps(points),
    }, follow_redirects=False)
    assert r.status_code == 302
    assert "/jobs/" in r.headers["Location"]

    # Verificar que existe en BD con ambito_kind=radius
    from webui.db import new_connection
    conn = new_connection()
    job_id = r.headers["Location"].rsplit("/", 1)[-1]
    job = conn.execute(
        "SELECT ambito_kind, ambito, segmento FROM jobs WHERE id = ?", (job_id,)
    ).fetchone()
    points_rows = conn.execute(
        "SELECT * FROM radius_points WHERE job_id = ?", (job_id,)
    ).fetchall()
    conn.close()
    assert job["ambito_kind"] == "radius"
    assert job["segmento"] == "admin_fincas"
    assert len(points_rows) == 1
    assert points_rows[0]["label"] == "Madrid"
    assert points_rows[0]["radius_meters"] == 5000


def test_radius_job_rejects_bad_points(admin_client):
    """Puntos fuera de rango → error."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})

    points = [{"latitude": 999, "longitude": -3, "radius": 5000}]
    r = admin_client.post("/jobs/new-radius", data={
        "segmento": "admin_fincas",
        "max_paginas": "1",
        "points_json": json.dumps(points),
    }, follow_redirects=True)
    body = r.data.decode()
    assert "latitud" in body or "rango" in body


def test_radius_job_rejects_invalid_json(admin_client):
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})
    r = admin_client.post("/jobs/new-radius", data={
        "segmento": "admin_fincas",
        "max_paginas": "1",
        "points_json": "not-json-at-all",
    }, follow_redirects=True)
    assert b"Formato" in r.data or b"inv" in r.data


def test_radius_job_no_api_key(admin_client):
    """Sin API key configurada, rechazar el job."""
    points = [{"latitude": 40.4168, "longitude": -3.7038, "radius": 5000}]
    r = admin_client.post("/jobs/new-radius", data={
        "segmento": "admin_fincas",
        "max_paginas": "1",
        "points_json": json.dumps(points),
    }, follow_redirects=True)
    assert b"Google Places" in r.data


def test_radius_job_respects_budget(app, admin_client):
    """El presupuesto del usuario se aplica también a búsquedas por radio."""
    admin_client.post("/admin/settings", data={"google_places_api_key": "TESTKEY"})
    # Crear usuario con presupuesto pequeño
    admin_client.post("/admin/users/new", data={
        "username": "broke",
        "password": "brokebroke",
        "role": "user",
        "budget_eur_monthly": "0.50",
    })
    c = app.test_client()
    c.post("/login", data={"username": "broke", "password": "brokebroke"})

    # Crear 10 puntos para que el coste supere los 0.50€
    points = [
        {"latitude": 40.0 + i*0.01, "longitude": -3.0, "radius": 5000}
        for i in range(10)
    ]
    r = c.post("/jobs/new-radius", data={
        "segmento": "clubes_deportivos",
        "max_paginas": "3",
        "points_json": json.dumps(points),
        "confirm_high_cost": "1",
    }, follow_redirects=True)
    assert b"presupuesto" in r.data
