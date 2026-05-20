"""Acceso a SQLite, esquema y migraciones simples."""

import sqlite3
from datetime import datetime

from flask import g
from werkzeug.security import generate_password_hash

from . import paths

# -----------------------------------------------------------------------------
# Esquema
# -----------------------------------------------------------------------------
#
# Notas de diseño:
#   - role tiene CHECK para evitar inconsistencias si alguien edita la BD a mano.
#   - users.session_version: se incrementa al cambiar password -> invalida cookies
#     de sesión robadas. Cada cookie guarda la versión bajo la que se emitió.
#   - users.budget_eur_monthly: presupuesto opcional por usuario. 0 = sin límite.
#   - segments: ahora viven en SQLite, NO en JSON, para evitar races concurrentes.
#   - jobs: añadidos job_dir (donde se almacenan los outputs) y estimated_cost_eur.

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL DEFAULT 'user'
        CHECK (role IN ('user', 'admin')),
    session_version INTEGER NOT NULL DEFAULT 1,
    budget_eur_monthly REAL NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS segments (
    id TEXT PRIMARY KEY,
    nombre_humano TEXT NOT NULL,
    producto_cgd TEXT DEFAULT '',
    queries_json TEXT NOT NULL,           -- JSON array
    palabras_clave_json TEXT NOT NULL,    -- JSON array
    palabras_descarte_json TEXT NOT NULL, -- JSON array
    created_by INTEGER,
    created_at TEXT NOT NULL,
    FOREIGN KEY (created_by) REFERENCES users (id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    segmento TEXT NOT NULL,
    ambito_kind TEXT NOT NULL DEFAULT 'subset'
        CHECK (ambito_kind IN ('subset', 'radius')),
    ambito TEXT NOT NULL,
    max_paginas INTEGER NOT NULL DEFAULT 3,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK (status IN ('pending', 'running', 'done', 'error', 'cancelled')),
    step TEXT NOT NULL DEFAULT 'queued',
    progress INTEGER NOT NULL DEFAULT 0,
    message TEXT DEFAULT '',
    json_filename TEXT DEFAULT '',
    enriched_filename TEXT DEFAULT '',
    csv_filename TEXT DEFAULT '',
    total_leads INTEGER DEFAULT 0,
    aptos_leads INTEGER DEFAULT 0,
    estimated_cost_eur REAL DEFAULT 0,
    queued_at TEXT NOT NULL,
    started_at TEXT DEFAULT '',
    finished_at TEXT DEFAULT '',
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_jobs_user_started
    ON jobs (user_id, queued_at DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_status
    ON jobs (status);

-- =============================================================================
-- LEADS MASTER: un registro único por place_id
-- =============================================================================
--
-- Aquí se acumulan los leads de TODOS los jobs deduplicados por place_id.
-- Esto evita contactar varias veces al mismo negocio si aparece en búsquedas
-- distintas (típico al lanzar segmentos solapados).
--
-- Estados:
--   nuevo:        recién extraído, sin acción
--   contactado:   se le ha enviado outreach
--   respondio:    ha respondido al outreach (positivo o negativo)
--   descartado:   no apto para campaña (decisión manual)
--
-- email_status:
--   ''           sin verificar (default)
--   mx_ok        el dominio tiene registros MX -> el email puede existir
--   mx_fail      el dominio no tiene MX -> email seguro inválido
--   invalid      validación profunda dijo inválido (SMTP/proveedor externo)
--   verified     validación profunda confirmó que existe

CREATE TABLE IF NOT EXISTS leads_master (
    place_id TEXT PRIMARY KEY,
    segmento TEXT NOT NULL,
    nombre TEXT NOT NULL DEFAULT '',
    email TEXT DEFAULT '',
    email_status TEXT NOT NULL DEFAULT ''
        CHECK (email_status IN ('', 'mx_ok', 'mx_fail', 'invalid', 'verified',
                                 'catch_all', 'unknown', 'do_not_send')),
    email_checked_at TEXT DEFAULT '',
    email_check_method TEXT DEFAULT '',   -- 'mx' / 'mailgun' / 'smtp'
    telefono TEXT DEFAULT '',
    web TEXT DEFAULT '',
    direccion TEXT DEFAULT '',
    localidad TEXT DEFAULT '',
    provincia TEXT DEFAULT '',
    ccaa TEXT DEFAULT '',
    rating REAL,
    num_resenas INTEGER,
    score INTEGER DEFAULT 0,
    apto_campanya INTEGER DEFAULT 0,    -- bool: 0/1
    estado TEXT NOT NULL DEFAULT 'nuevo'
        CHECK (estado IN ('nuevo', 'contactado', 'respondio', 'descartado')),
    notas TEXT DEFAULT '',
    fecha_ultimo_contacto TEXT DEFAULT '',
    -- Redes sociales (rellenadas opcionalmente en post-procesado)
    linkedin_url TEXT DEFAULT '',
    instagram_url TEXT DEFAULT '',
    facebook_url TEXT DEFAULT '',
    twitter_url TEXT DEFAULT '',
    youtube_url TEXT DEFAULT '',
    tiktok_url TEXT DEFAULT '',
    social_extracted_at TEXT DEFAULT '',
    -- Trazabilidad
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    last_seen_job_id TEXT,
    times_seen INTEGER NOT NULL DEFAULT 1,
    FOREIGN KEY (last_seen_job_id) REFERENCES jobs (id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_leads_segmento ON leads_master (segmento);
CREATE INDEX IF NOT EXISTS idx_leads_estado ON leads_master (estado);
CREATE INDEX IF NOT EXISTS idx_leads_last_seen ON leads_master (last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_leads_score ON leads_master (score DESC);

-- Relación N:N entre leads y jobs: un lead puede haber salido de varios jobs.
-- Cuando se borra un job, se borra solo el enlace, no el lead.

CREATE TABLE IF NOT EXISTS lead_jobs (
    place_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY (place_id, job_id),
    FOREIGN KEY (place_id) REFERENCES leads_master (place_id) ON DELETE CASCADE,
    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_lead_jobs_job ON lead_jobs (job_id);

-- =============================================================================
-- BÚSQUEDAS POR RADIO: puntos asociados a jobs de tipo radio
-- =============================================================================
--
-- Cuando un job tiene ambito_kind='radius', en lugar de leer SUBCONJUNTOS
-- usamos esta tabla para saber qué puntos buscar. Cada job puede tener
-- 1..N puntos con su propio radio.

CREATE TABLE IF NOT EXISTS radius_points (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id TEXT NOT NULL,
    latitude REAL NOT NULL,
    longitude REAL NOT NULL,
    radius_meters INTEGER NOT NULL,
    label TEXT DEFAULT '',
    FOREIGN KEY (job_id) REFERENCES jobs (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_radius_points_job ON radius_points (job_id);

-- =============================================================================
-- BÚSQUEDAS PROGRAMADAS
-- =============================================================================
--
-- schedule_kind:
--   simple: usa interval_minutes. Ejecuta cada X minutos desde created_at.
--   cron:   usa cron_expr (formato Unix estándar: '0 6 * * 1' = lunes 6am).
--
-- ambito_kind: 'subset' (uno de SUBCONJUNTOS) o 'radius' (puntos en JSON).
-- points_json: solo si ambito_kind='radius'.
--
-- next_run_at se calcula al crear/ejecutar; el worker lo lee para decidir.
-- failure_count: si llega a 5, se desactiva automáticamente para no quemar API.

CREATE TABLE IF NOT EXISTS scheduled_searches (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    segmento TEXT NOT NULL,
    ambito_kind TEXT NOT NULL DEFAULT 'subset'
        CHECK (ambito_kind IN ('subset', 'radius')),
    ambito TEXT DEFAULT '',           -- si ambito_kind='subset'
    points_json TEXT DEFAULT '',      -- si ambito_kind='radius'
    max_paginas INTEGER NOT NULL DEFAULT 3,
    schedule_kind TEXT NOT NULL DEFAULT 'simple'
        CHECK (schedule_kind IN ('simple', 'cron')),
    interval_minutes INTEGER DEFAULT 0,
    cron_expr TEXT DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT DEFAULT '',
    last_job_id TEXT DEFAULT '',
    failure_count INTEGER NOT NULL DEFAULT 0,
    last_error TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users (id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_scheduled_next_run
    ON scheduled_searches (enabled, next_run_at);

CREATE TABLE IF NOT EXISTS login_attempts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    ip TEXT DEFAULT '',
    success INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_login_attempts_user_ts
    ON login_attempts (username, ts DESC);
"""


def get_db() -> sqlite3.Connection:
    """Conexión por-request con foreign keys habilitadas."""
    if "db" not in g:
        g.db = sqlite3.connect(
            paths.DB_PATH,
            timeout=10,
            detect_types=sqlite3.PARSE_DECLTYPES,
            isolation_level=None,  # autocommit; usamos transacciones explícitas
        )
        g.db.row_factory = sqlite3.Row
        g.db.execute("PRAGMA foreign_keys = ON")
        g.db.execute("PRAGMA journal_mode = WAL")
        g.db.execute("PRAGMA synchronous = NORMAL")
    return g.db


def close_db(_=None):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def new_connection() -> sqlite3.Connection:
    """Conexión nueva (para usar fuera del contexto de request, e.g. workers)."""
    conn = sqlite3.connect(paths.DB_PATH, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db(default_admin_password: str = "admin") -> None:
    """
    Crea las tablas si no existen y registra el admin inicial.

    Race condition fix: usamos INSERT OR IGNORE y comprobamos después, en
    lugar del patrón SELECT-then-INSERT que tenía la versión anterior.
    """
    conn = new_connection()
    try:
        conn.executescript(SCHEMA)
        # Migraciones aditivas para BDs creadas con versiones previas
        _migrate_schema(conn)
        # Crear admin sólo si NO existe (idempotente y race-safe)
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "INSERT OR IGNORE INTO users "
            "(username, password_hash, role, created_at) VALUES (?, ?, ?, ?)",
            ("admin", generate_password_hash(default_admin_password), "admin", now),
        )
        if cur.rowcount > 0:
            print(f"[init] Admin creado: usuario=admin  contraseña={default_admin_password}")
            print("[init] Cámbiala desde 'Mi cuenta' lo antes posible.")
    finally:
        conn.close()


def _migrate_schema(conn) -> None:
    """
    Migraciones aditivas. SQLite no soporta ALTER TABLE ADD COLUMN IF NOT EXISTS,
    así que comprobamos las columnas con PRAGMA antes de añadirlas.

    Esto permite actualizar de v2 → v3 sin perder datos: solo se añaden
    las columnas nuevas con su default.
    """
    def existing_cols(table: str) -> set[str]:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}

    # leads_master: columnas de redes sociales y método de validación de email
    leads_cols = existing_cols("leads_master")
    to_add = [
        ("linkedin_url",         "TEXT DEFAULT ''"),
        ("instagram_url",        "TEXT DEFAULT ''"),
        ("facebook_url",         "TEXT DEFAULT ''"),
        ("twitter_url",          "TEXT DEFAULT ''"),
        ("youtube_url",          "TEXT DEFAULT ''"),
        ("tiktok_url",           "TEXT DEFAULT ''"),
        ("social_extracted_at",  "TEXT DEFAULT ''"),
        ("email_check_method",   "TEXT DEFAULT ''"),
    ]
    for colname, coldef in to_add:
        if colname not in leads_cols:
            conn.execute(f"ALTER TABLE leads_master ADD COLUMN {colname} {coldef}")


def recover_orphan_jobs() -> int:
    """
    Marca como error los jobs que quedaron 'pending' o 'running' tras un
    reinicio del servidor: no hay forma de retomar un subproceso muerto.
    Devuelve cuántos jobs se marcaron.
    """
    conn = new_connection()
    try:
        now = datetime.now().isoformat(timespec="seconds")
        cur = conn.execute(
            "UPDATE jobs SET status = 'error', "
            "message = 'Interrumpido por reinicio del servidor', "
            "finished_at = ? "
            "WHERE status IN ('pending', 'running')",
            (now,),
        )
        return cur.rowcount
    finally:
        conn.close()
