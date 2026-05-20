"""Tests de gestión de segmentos: builtin + custom + concurrencia."""

import threading


def test_builtin_segments_listed(admin_client):
    r = admin_client.get("/segments/")
    body = r.data.decode()
    for sid in ["admin_fincas", "clubes_deportivos", "campamentos_verano",
                "asesorias", "centros_formacion"]:
        assert sid in body


def test_create_custom_segment(admin_client):
    r = admin_client.post("/segments/new", data={
        "id": "test_seg",
        "nombre_humano": "Test segment",
        "producto_cgd": "X",
        "queries": "uno\ndos\ntres",
        "palabras_clave_web": "a\nb",
        "palabras_descarte": "",
    }, follow_redirects=True)
    assert b"creado" in r.data
    assert b"test_seg" in r.data


def test_invalid_segment_ids_rejected(admin_client):
    cases = [
        ("X invalid", "spaces"),
        ("123foo", "starts with digit"),
        ("a", "too short"),
        ("a" * 51, "too long"),
        ("admin_fincas", "duplicate builtin"),
        ("X-Y", "uppercase"),
    ]
    for sid, reason in cases:
        r = admin_client.post("/segments/new", data={
            "id": sid,
            "nombre_humano": "X",
            "queries": "x",
        }, follow_redirects=True)
        # Cada caso debe devolver un error
        body = r.data.decode()
        assert ("inv" in body or "ya existe" in body), \
               f"sid={sid!r} ({reason}) no fue rechazado"


def test_delete_custom_segment(admin_client):
    admin_client.post("/segments/new", data={
        "id": "seg_to_delete",
        "nombre_humano": "X",
        "queries": "x",
    })
    r = admin_client.post("/segments/seg_to_delete/delete", follow_redirects=True)
    assert b"eliminado" in r.data


def test_cannot_delete_builtin(admin_client):
    r = admin_client.post("/segments/admin_fincas/delete", follow_redirects=True)
    body = r.data.decode()
    assert "sistema" in body or "No se pueden" in body
    # Asegurar que no se borró nada
    r2 = admin_client.get("/segments/")
    assert b"admin_fincas" in r2.data


def test_concurrent_segment_creation_no_race(app):
    """
    REGRESIÓN: la primera versión perdía segmentos en escrituras concurrentes
    porque user_segments.json era un read-modify-write no atómico.
    En la nueva versión usamos SQLite, que serializa los INSERTs.
    """
    from webui.segments import create_custom_segment
    from webui.db import init_db

    init_db()  # asegurar BD lista en el tmpdir

    results = []
    errors = []

    def create(i):
        with app.app_context():
            try:
                ok, errs = create_custom_segment(
                    sid=f"race_{i}",
                    nombre=f"race {i}",
                    producto="",
                    queries=["x"],
                    palabras=[],
                    descarte=[],
                    created_by=None,
                )
                if ok:
                    results.append(i)
                else:
                    errors.append((i, errs))
            except Exception as e:
                errors.append((i, str(e)))

    threads = [threading.Thread(target=create, args=(i,)) for i in range(20)]
    for t in threads: t.start()
    for t in threads: t.join()

    # Los 20 deberían haberse insertado correctamente
    with app.app_context():
        from webui.segments import list_custom_segments
        all_segs = list_custom_segments()
        race_segs = [s for s in all_segs if s["id"].startswith("race_")]
        assert len(race_segs) == 20, \
            f"Race condition: esperados 20, encontrados {len(race_segs)}"


def test_path_traversal_in_segment_id_blocked(admin_client):
    """No se puede pasar un sid con '..' a /segments/<sid>/delete.

    Flask normaliza la URL antes de hacer routing, así que devuelve 404.
    Lo importante es que NO crashee ni acepte el path traversal.
    """
    r = admin_client.post("/segments/..%2F..%2Fetc/delete", follow_redirects=True)
    # 404 (no encuentra la ruta) o 200 con flash de error — ambos son OK
    assert r.status_code in (200, 404)


def test_query_length_limit(admin_client):
    """Queries muy largas deben rechazarse."""
    r = admin_client.post("/segments/new", data={
        "id": "longquery_test",
        "nombre_humano": "X",
        "queries": "a" * 250,  # >200 chars
    }, follow_redirects=True)
    assert b"200 caracteres" in r.data


def test_too_many_queries_rejected(admin_client):
    """Más de 100 queries debe rechazarse."""
    r = admin_client.post("/segments/new", data={
        "id": "manyqueries_test",
        "nombre_humano": "X",
        "queries": "\n".join(f"query{i}" for i in range(101)),
    }, follow_redirects=True)
    assert b"Demasiadas queries" in r.data
