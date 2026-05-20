"""Tests de autenticación, sesiones, CSRF y autorización."""

import pytest


def test_unauthenticated_redirects_to_login(client):
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_login_page_loads(client):
    r = client.get("/login")
    assert r.status_code == 200
    assert b"Acceder" in r.data


def test_admin_can_login(client):
    r = client.post("/login", data={"username": "admin", "password": "admin"},
                    follow_redirects=False)
    assert r.status_code == 302


def test_wrong_password_rejected(client):
    r = client.post("/login", data={"username": "admin", "password": "wrong"},
                    follow_redirects=True)
    assert b"incorrectos" in r.data


def test_dashboard_accessible_after_login(admin_client):
    r = admin_client.get("/")
    assert r.status_code == 200
    assert b"Nueva b" in r.data  # "Nueva búsqueda"


def test_logout_is_post_only(client):
    """GET /logout debe estar prohibido (CSRF defense)."""
    client.post("/login", data={"username": "admin", "password": "admin"})
    r = client.get("/logout")
    assert r.status_code == 405


def test_logout_clears_session(admin_client):
    admin_client.post("/logout")
    r = admin_client.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_username_normalized_lowercase(client, admin_client):
    """ADMIN, Admin y admin deberían ser el mismo usuario."""
    admin_client.post("/admin/users/new", data={
        "username": "TestUser",  # mayúsculas
        "password": "test1234",
        "role": "user",
    })
    # Login con minúsculas funciona
    c2 = client
    r = c2.post("/login", data={"username": "testuser", "password": "test1234"},
                follow_redirects=False)
    assert r.status_code == 302


def test_password_change_invalidates_other_sessions(app, admin_client):
    """Al cambiar password en una sesión, otras sesiones del mismo usuario
    quedan cerradas."""
    # Sesión A (admin_client)
    # Sesión B: misma cuenta admin
    sessB = app.test_client()
    sessB.post("/login", data={"username": "admin", "password": "admin"})
    assert sessB.get("/").status_code == 200  # antes funciona

    # Cambiar password desde sesión A
    admin_client.post("/account", data={
        "current_password": "admin",
        "new_password": "admin_v2",
        "confirm_password": "admin_v2",
    })

    # Sesión B debería estar muerta ahora
    r = sessB.get("/", follow_redirects=False)
    assert r.status_code == 302
    assert "/login" in r.headers["Location"]


def test_role_check_enforced_in_db():
    """La BD rechaza roles arbitrarios gracias al CHECK constraint."""
    from webui.db import new_connection, init_db
    init_db()
    conn = new_connection()
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO users (username, password_hash, role, created_at) "
            "VALUES ('weird', 'x', 'superadmin', 'now')"
        )
    conn.close()


def test_user_cannot_access_admin(user_client):
    assert user_client.get("/admin/").status_code == 403
    assert user_client.post("/admin/users/new", data={}).status_code == 403
    assert user_client.post("/admin/settings", data={}).status_code == 403


def test_admin_link_hidden_for_non_admin(user_client):
    r = user_client.get("/")
    body = r.data.decode()
    # En la nav debe NO aparecer el link "Admin"
    assert ">Admin<" not in body


def test_open_redirect_protection(client):
    """El parámetro `next` no puede llevar a un sitio externo."""
    r = client.post("/login?next=https://evil.com",
                    data={"username": "admin", "password": "admin"},
                    follow_redirects=False)
    assert r.status_code == 302
    assert "evil.com" not in r.headers["Location"]


def test_session_cookie_has_httponly(admin_client):
    """La cookie de sesión debe llevar HttpOnly (defensa contra XSS)."""
    r = admin_client.get("/")
    # Las cookies se setean al hacer login; las posteriores solo si cambia algo.
    # Comprobamos en cualquier respuesta que el header existe.
    # Más robusto: leer la config de la app.
    from flask import current_app
    with admin_client.application.app_context():
        assert current_app.config["SESSION_COOKIE_HTTPONLY"] is True
        assert current_app.config["SESSION_COOKIE_SAMESITE"] == "Lax"
