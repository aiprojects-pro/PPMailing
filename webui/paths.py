"""Rutas y constantes de filesystem usadas por toda la UI."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
WEBUI_DIR = Path(__file__).resolve().parent
INSTANCE_DIR = WEBUI_DIR / "instance"
INSTANCE_DIR.mkdir(exist_ok=True)

DB_PATH = INSTANCE_DIR / "ppmailing.db"
SETTINGS_PATH = INSTANCE_DIR / "settings.json"
SECRET_KEY_PATH = INSTANCE_DIR / ".flask_secret"
JOB_LOG_DIR = INSTANCE_DIR / "job_logs"
JOB_OUTPUTS_DIR = INSTANCE_DIR / "job_outputs"
EXTRA_SEGMENTS_DIR = INSTANCE_DIR / "extra_segments"

JOB_LOG_DIR.mkdir(exist_ok=True)
JOB_OUTPUTS_DIR.mkdir(exist_ok=True)
EXTRA_SEGMENTS_DIR.mkdir(exist_ok=True)

DATA_DIR = PROJECT_ROOT / "data"
LOG_DIR = PROJECT_ROOT / "logs"
DATA_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)
