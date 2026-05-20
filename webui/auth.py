"""
Autenticación: blueprint de /login, /logout, /account y los decoradores
login_required / admin_required.

Mejoras frente a la primera versión:
  - session_version: invalida sesiones existentes al cambiar la password.
  - Rate-limit en /login (Flask-Limiter): defiende contra fuerza bruta.
  - Log persistente de intentos fallidos (tabla login_attempts).
  - Username normalizado a minúsculas + validado por regex.
"""

import re
import sqlite3
from datetime import datetime
from functools import wraps

from flask import (
    Blueprint, abort, flash, redirect, render_template, request,
    session, url_for,
)
from werkzeug.security import check_password_hash, generate_password_hash

from .db import get_db
from .security import csrf, limiter


bp = Blueprint("auth", __name__)


USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")


def normalize_username(raw: str) -> str:
    """Normaliza el username a minúsculas y sin espacios alrededor.
    Devuelve cadena vacía si no pasa la validación."""
    if not raw:
        return ""
    candidate = raw.strip().lower()
    return candidate if USERNAME_RE.fullmatch(candidate) else ""


def current_user() -> sqlite3.Row | None:
    """
    Devuelve el usuario actual o None.

    Verifica session_version: si la versión almacenada en la cookie no
    coincide con la de la BD, la sesión se invalida silenciosamente.
    Esto pasa cuando el usuario cambia su contraseña: cookies viejas
    (potencialmente robadas) dejan de ser válidas.
    """
    uid = session.get("user_id")
    if uid is None:
        return None
    row = get_db().execute(
        "SELECT * FROM users WHERE id = ?", (uid,)
    ).fetchone()
    if row is None:
        return None
    if session.get("session_version") != row["session_version"]:
        session.clear()
        return None
    return row


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if current_user() is None:
            return redirect(url_for("auth.login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


def admin_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        u = current_user()
        if u is None:
            return redirect(url_for("auth.login"))
        if u["role"] != "admin":
            abort(403)
        return view(*args, **kwargs)
    return wrapped


def _log_attempt(username: str, success: bool) -> None:
    """Registra el intento en la BD para mostrar histórico y permitir auditoría."""
    try:
        get_db().execute(
            "INSERT INTO login_attempts (username, ip, success, ts) "
            "VALUES (?, ?, ?, ?)",
            (username[:64], request.remote_addr or "", 1 if success else 0,
             datetime.now().isoformat(timespec="seconds")),
        )
    except Exception:
        # No bloquear el login por un fallo de auditoría
        pass


# -----------------------------------------------------------------------------
# Rutas
# -----------------------------------------------------------------------------

@bp.route("/login", methods=["GET", "POST"])
@limiter.limit("10/minute; 30/hour", methods=["POST"])
def login():
    if request.method == "POST":
        username = (request.form.get("username") or "").strip().lower()
        password = request.form.get("password") or ""

        if not username or not password:
            flash("Usuario y contraseña son obligatorios.", "error")
            return render_template("login.html")

        # Buscamos exact match (la columna está case-sensitive en SQLite)
        row = get_db().execute(
            "SELECT * FROM users WHERE username = ?", (username,)
        ).fetchone()

        if row and check_password_hash(row["password_hash"], password):
            session.clear()
            session.permanent = True   # respeta PERMANENT_SESSION_LIFETIME
            session["user_id"] = row["id"]
            session["session_version"] = row["session_version"]
            _log_attempt(username, True)

            # Validar el next para evitar open-redirect
            nxt = request.args.get("next") or url_for("dashboard.index")
            if not nxt.startswith("/") or nxt.startswith("//"):
                nxt = url_for("dashboard.index")
            return redirect(nxt)

        _log_attempt(username, False)
        flash("Usuario o contraseña incorrectos.", "error")

    return render_template("login.html")


@bp.route("/logout", methods=["POST"])
def logout():
    """POST-only para evitar CSRF de logout (un <img src='/logout'> ya no sirve)."""
    session.clear()
    return redirect(url_for("auth.login"))


@bp.route("/account", methods=["GET", "POST"])
@login_required
def account():
    u = current_user()
    if request.method == "POST":
        current = request.form.get("current_password") or ""
        new = request.form.get("new_password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not check_password_hash(u["password_hash"], current):
            flash("Contraseña actual incorrecta.", "error")
        elif len(new) < 8:
            flash("La nueva contraseña debe tener al menos 8 caracteres.", "error")
        elif new != confirm:
            flash("Las contraseñas no coinciden.", "error")
        elif check_password_hash(u["password_hash"], new):
            flash("La nueva contraseña debe ser distinta de la actual.", "error")
        else:
            # Cambiar password + invalidar sesiones existentes
            db = get_db()
            db.execute(
                "UPDATE users SET password_hash = ?, "
                "session_version = session_version + 1 WHERE id = ?",
                (generate_password_hash(new), u["id"]),
            )
            # Re-cargar para refrescar la cookie de la sesión actual
            new_row = db.execute("SELECT session_version FROM users WHERE id = ?",
                                 (u["id"],)).fetchone()
            session["session_version"] = new_row["session_version"]
            flash("Contraseña cambiada. Otras sesiones se han cerrado.", "success")
            return redirect(url_for("auth.account"))

    return render_template("account.html")
