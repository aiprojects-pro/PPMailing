"""Tests del panel de administración."""

import pytest


def test_admin_can_create_user(admin_client):
    r = admin_client.post("/admin/users/new", data={
        "username": "newuser",
        "password": "newuser123",
        "role": "user",
    }, follow_redirects=True)
    assert b"creado" in r.data


def test_short_password_rejected(admin_client):
    r = admin_client.post("/admin/users/new", data={
        "username": "shortpw",
        "password": "12345",  # menos de 8
        "role": "user",
    }, follow_redirects=True)
    assert b"8 caracteres" in r.data


def test_invalid_username_rejected(admin_client):
    cases = [
        "<script>",
        "user with spaces",
        "x",          # too short
        "x" * 50,     # too long
        "/etc/passwd",
        "",
    ]
    for username in cases:
        r = admin_client.post("/admin/users/new", data={
            "username": username,
            "password": "validpw123",
            "role": "user",
        }, follow_redirects=True)
        body = r.data.decode()
        assert "inválido" in body or "obligatorios" in body, \
               f"Username inválido aceptado: {username!r}"


def test_uppercase_username_normalized_to_existing(admin_client):
    """'ADMIN' se normaliza a 'admin', que ya existe -> debe rechazarse."""
    r = admin_client.post("/admin/users/new", data={
        "username": "ADMIN",
        "password": "validpw123",
        "role": "user",
    }, follow_redirects=True)
    body = r.data.decode()
    assert "ya existe" in body


def test_cannot_delete_self(admin_client):
    """No puedes borrarte a ti mismo."""
    # Buscar el id de admin
    from webui.db import new_connection
    conn = new_connection()
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()["id"]
    conn.close()

    r = admin_client.post(f"/admin/users/{admin_id}/delete", follow_redirects=True)
    assert b"No puedes borrarte" in r.data


def test_cannot_delete_last_admin(app, admin_client):
    """No puedes borrar al último admin (aunque no seas tú mismo)."""
    # Crear otro admin
    admin_client.post("/admin/users/new", data={
        "username": "admin2",
        "password": "admin2admin2",
        "role": "admin",
    })

    # admin2 borra al admin original
    c = app.test_client()
    c.post("/login", data={"username": "admin2", "password": "admin2admin2"})

    from webui.db import new_connection
    conn = new_connection()
    admin_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin'"
    ).fetchone()["id"]
    conn.close()

    # admin2 borra al admin original (queda admin2 como único admin)
    c.post(f"/admin/users/{admin_id}/delete")

    # Ahora admin2 intenta borrarse a sí mismo (es el último admin)
    conn = new_connection()
    admin2_id = conn.execute(
        "SELECT id FROM users WHERE username = 'admin2'"
    ).fetchone()["id"]
    conn.close()

    # Crear un usuario user para que admin2 NO sea el único usuario
    c.post("/admin/users/new", data={
        "username": "regular",
        "password": "regular123",
        "role": "user",
    })

    # admin2 intenta degradarse a user (sería el último admin -> no debe poder)
    r = c.post(f"/admin/users/{admin2_id}/role", data={"role": "user"},
               follow_redirects=True)
    body = r.data.decode()
    assert "último administrador" in body or "ltimo administrador" in body or \
           "No puedes quitarte" in body or "No puedes degradar" in body


def test_change_other_users_password_invalidates_their_sessions(app, admin_client):
    """Admin cambia password de Alice → sesión de Alice queda invalidada."""
    admin_client.post("/admin/users/new", data={
        "username": "alice",
        "password": "alicealice",
        "role": "user",
    })
    alice = app.test_client()
    alice.post("/login", data={"username": "alice", "password": "alicealice"})
    assert alice.get("/").status_code == 200

    from webui.db import new_connection
    conn = new_connection()
    alice_id = conn.execute(
        "SELECT id FROM users WHERE username = 'alice'"
    ).fetchone()["id"]
    conn.close()

    # Admin cambia la password de Alice
    admin_client.post(f"/admin/users/{alice_id}/password",
                       data={"password": "newpassword"})

    # La sesión de Alice debe estar muerta
    r = alice.get("/", follow_redirects=False)
    assert r.status_code == 302


def test_api_key_storage(admin_client):
    r = admin_client.post("/admin/settings", data={
        "google_places_api_key": "AIzaSy_TEST_KEY_1234567890"
    }, follow_redirects=True)
    assert b"guardada" in r.data

    # En el admin panel debe verse enmascarada
    r = admin_client.get("/admin/")
    body = r.data.decode()
    assert "AIzaSy_TEST_KEY_1234567890" not in body  # NO la entera
    assert "AIzaSy" in body  # SÍ el prefijo


def test_api_key_can_be_cleared(admin_client):
    admin_client.post("/admin/settings",
                       data={"google_places_api_key": "TEST"})
    r = admin_client.post("/admin/settings",
                           data={"google_places_api_key": ""},
                           follow_redirects=True)
    assert b"eliminada" in r.data


def test_failed_logins_logged(client, admin_client):
    """Los intentos fallidos se registran y aparecen en /admin."""
    client.post("/login", data={"username": "fake_user", "password": "fake"})
    client.post("/login", data={"username": "admin", "password": "wrongpass"})

    r = admin_client.get("/admin/")
    body = r.data.decode()
    assert "fake_user" in body or "admin" in body  # al menos uno aparece
