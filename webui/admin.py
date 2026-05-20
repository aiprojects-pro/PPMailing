"""Panel de administración: gestión de usuarios, API key, presupuestos."""

import os
import sqlite3
from datetime import datetime

from flask import (
    Blueprint, flash, redirect, render_template, request, url_for,
)
from werkzeug.security import generate_password_hash

from .auth import admin_required, current_user, normalize_username
from .db import get_db
from .settings import (
    get_api_key, get_retention, load_settings, mask_api_key,
    set_api_key, set_retention,
)


bp = Blueprint("admin", __name__, url_prefix="/admin")


@bp.route("/")
@admin_required
def index():
    db = get_db()
    users = db.execute(
        "SELECT id, username, role, budget_eur_monthly, created_at "
        "FROM users ORDER BY username"
    ).fetchall()

    # Histórico de gasto mensual por usuario
    spending = {}
    for u in users:
        row = db.execute(
            "SELECT COALESCE(SUM(estimated_cost_eur), 0) AS total "
            "FROM jobs WHERE user_id = ? "
            "AND substr(queued_at, 1, 7) = strftime('%Y-%m', 'now') "
            "AND status IN ('pending','running','done')",
            (u["id"],),
        ).fetchone()
        spending[u["id"]] = float(row["total"])

    # Últimos intentos fallidos de login (auditoría)
    failed_logins = db.execute(
        "SELECT username, ip, ts FROM login_attempts "
        "WHERE success = 0 ORDER BY ts DESC LIMIT 10"
    ).fetchall()

    settings = load_settings()
    api_key = get_api_key()
    masked = mask_api_key(settings.get("google_places_api_key", ""))
    using_env = not settings.get("google_places_api_key") and bool(
        os.environ.get("GOOGLE_PLACES_API_KEY"))

    retention = get_retention()

    from .mailgun import mask_mailgun_config
    mailgun_cfg = mask_mailgun_config()

    return render_template(
        "admin.html",
        users=users,
        spending=spending,
        failed_logins=failed_logins,
        masked_key=masked,
        api_key_set=bool(api_key),
        using_env=using_env,
        retention=retention,
        mailgun_cfg=mailgun_cfg,
    )


@bp.route("/users/new", methods=["POST"])
@admin_required
def user_new():
    raw_username = request.form.get("username") or ""
    password = request.form.get("password") or ""
    role = request.form.get("role", "user")
    try:
        budget = float(request.form.get("budget_eur_monthly", "0") or "0")
    except ValueError:
        budget = 0
    if budget < 0:
        budget = 0

    if role not in ("user", "admin"):
        role = "user"

    username = normalize_username(raw_username)
    if not username:
        flash(
            "Nombre de usuario inválido. Usa 2-32 caracteres: letras "
            "minúsculas, números, '.', '_' o '-'.",
            "error",
        )
        return redirect(url_for("admin.index"))
    if len(password) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "error")
        return redirect(url_for("admin.index"))

    try:
        get_db().execute(
            "INSERT INTO users "
            "(username, password_hash, role, budget_eur_monthly, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (username, generate_password_hash(password), role, budget,
             datetime.now().isoformat(timespec="seconds")),
        )
        flash(f"Usuario '{username}' creado.", "success")
    except sqlite3.IntegrityError:
        flash(f"El usuario '{username}' ya existe.", "error")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:uid>/delete", methods=["POST"])
@admin_required
def user_delete(uid):
    u = current_user()
    if uid == u["id"]:
        flash("No puedes borrarte a ti mismo.", "error")
        return redirect(url_for("admin.index"))

    # No permitir borrar al último admin
    db = get_db()
    target = db.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()
    if target and target["role"] == "admin":
        admin_count = db.execute(
            "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
        ).fetchone()["n"]
        if admin_count <= 1:
            flash("No puedes borrar al último administrador.", "error")
            return redirect(url_for("admin.index"))

    db.execute("DELETE FROM users WHERE id = ?", (uid,))
    flash("Usuario eliminado.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:uid>/password", methods=["POST"])
@admin_required
def user_password(uid):
    password = request.form.get("password") or ""
    if len(password) < 8:
        flash("La contraseña debe tener al menos 8 caracteres.", "error")
        return redirect(url_for("admin.index"))
    # Cambiar password Y incrementar session_version para echar otras sesiones
    get_db().execute(
        "UPDATE users SET password_hash = ?, "
        "session_version = session_version + 1 WHERE id = ?",
        (generate_password_hash(password), uid),
    )
    flash("Contraseña actualizada (sesiones existentes cerradas).", "success")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:uid>/budget", methods=["POST"])
@admin_required
def user_budget(uid):
    try:
        budget = float(request.form.get("budget_eur_monthly", "0") or "0")
    except ValueError:
        flash("Presupuesto inválido.", "error")
        return redirect(url_for("admin.index"))
    if budget < 0:
        budget = 0
    get_db().execute(
        "UPDATE users SET budget_eur_monthly = ? WHERE id = ?",
        (budget, uid),
    )
    flash("Presupuesto actualizado.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/users/<int:uid>/role", methods=["POST"])
@admin_required
def user_role(uid):
    u = current_user()
    role = request.form.get("role", "user")
    if role not in ("user", "admin"):
        flash("Rol inválido.", "error")
        return redirect(url_for("admin.index"))

    db = get_db()
    if uid == u["id"] and role != "admin":
        flash("No puedes quitarte el rol admin a ti mismo.", "error")
        return redirect(url_for("admin.index"))

    # No permitir convertir al último admin en user
    if role == "user":
        target = db.execute("SELECT role FROM users WHERE id = ?", (uid,)).fetchone()
        if target and target["role"] == "admin":
            admin_count = db.execute(
                "SELECT COUNT(*) AS n FROM users WHERE role = 'admin'"
            ).fetchone()["n"]
            if admin_count <= 1:
                flash("No puedes degradar al último administrador.", "error")
                return redirect(url_for("admin.index"))

    db.execute("UPDATE users SET role = ? WHERE id = ?", (role, uid))
    flash("Rol actualizado.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/settings", methods=["POST"])
@admin_required
def settings():
    key = (request.form.get("google_places_api_key") or "").strip()
    set_api_key(key)
    flash(
        "Clave de Google Places guardada." if key
        else "Clave de Google Places eliminada (se usará variable de entorno si existe).",
        "success",
    )
    return redirect(url_for("admin.index"))


@bp.route("/mailgun-settings", methods=["POST"])
@admin_required
def mailgun_settings():
    """Guarda la configuración de Mailgun."""
    from .mailgun import clear_mailgun_config, set_mailgun_config

    action = request.form.get("action", "save")
    if action == "clear":
        clear_mailgun_config()
        flash("Configuración de Mailgun eliminada.", "success")
        return redirect(url_for("admin.index"))

    api_key = (request.form.get("api_key") or "").strip()
    domain = (request.form.get("domain") or "").strip()
    base_url = (request.form.get("base_url") or "https://api.mailgun.net").strip()
    webhook_signing_key = (request.form.get("webhook_signing_key") or "").strip()

    if not api_key and not domain and not webhook_signing_key:
        flash("No has indicado ningún valor. Nada se ha cambiado.", "error")
        return redirect(url_for("admin.index"))

    if base_url and not base_url.startswith(("http://", "https://")):
        flash("La base URL debe empezar por http:// o https://.", "error")
        return redirect(url_for("admin.index"))

    set_mailgun_config(api_key, domain, base_url, webhook_signing_key)
    flash("Configuración de Mailgun guardada.", "success")
    return redirect(url_for("admin.index"))


@bp.route("/retention", methods=["POST"])
@admin_required
def retention():
    """Configura la política de retención automática de jobs."""
    enabled = request.form.get("retention_enabled") == "1"
    try:
        days = int(request.form.get("retention_days", "90"))
    except (ValueError, TypeError):
        days = 90
    if days < 1:
        flash("La retención mínima es de 1 día.", "error")
        return redirect(url_for("admin.index"))
    if days > 3650:
        flash("La retención máxima es de 3650 días (~10 años).", "error")
        return redirect(url_for("admin.index"))

    set_retention(enabled, days)
    if enabled:
        flash(f"Retención activada: jobs de más de {days} días se borrarán "
              "automáticamente en la próxima limpieza (cada hora).", "success")
    else:
        flash("Retención automática desactivada. "
              "Los jobs antiguos se conservarán indefinidamente.", "success")
    return redirect(url_for("admin.index"))
