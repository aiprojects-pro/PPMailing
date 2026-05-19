#!/usr/bin/env python3
"""
CLI — Extrae emails y palabras clave de las webs de un JSON de negocios.

Uso:
    python scripts/extraer_emails.py --input <archivo.json>
    python scripts/extraer_emails.py --input campamentos_verano_espana_20260517.json
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import WEB_SCRAPING_RATE_LIMIT, DATA_DIR, LOG_DIR
from core.modelos import Negocio
from core.extractor_emails import procesar_web


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Extrae emails de webs de negocios.")
    parser.add_argument("--input", required=True, help="JSON de buscar.py (nombre o ruta)")
    return parser.parse_args()


def resolver_input(arg: str) -> Path:
    p = Path(arg)
    if p.is_file():
        return p
    p_data = DATA_DIR / arg
    if p_data.is_file():
        return p_data
    raise FileNotFoundError(f"No encontrado: {arg}")


def main() -> int:
    args = parse_args()

    try:
        ruta_in = resolver_input(args.input)
    except FileNotFoundError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1

    log_file = LOG_DIR / f"emails_{ruta_in.stem}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger("emails")

    log.info("=" * 72)
    log.info("CGD - Extractor de emails")
    log.info("Entrada: %s", ruta_in)
    log.info("=" * 72)

    with open(ruta_in, encoding="utf-8") as f:
        raw = json.load(f)
    negocios = [Negocio(**d) for d in raw]
    con_web = [n for n in negocios if n.web]

    log.info("Total: %d  |  Con web (se procesan): %d", len(negocios), len(con_web))

    for i, n in enumerate(con_web, 1):
        log.info("[%d/%d] %s", i, len(con_web), n.web)
        try:
            procesar_web(n)
        except Exception as e:
            log.warning("   Error: %s", e)
        time.sleep(WEB_SCRAPING_RATE_LIMIT)

    con_email = sum(1 for n in negocios if n.email)
    con_email_propio = sum(1 for n in negocios if n.email_dominio_propio)
    con_keywords = sum(1 for n in negocios if n.palabras_clave_encontradas)

    log.info("")
    log.info("=" * 72)
    log.info("RESUMEN")
    log.info("  Con email:             %4d / %d  (%.1f%%)",
             con_email, len(negocios), 100*con_email/max(len(negocios), 1))
    log.info("  Email dominio propio:  %4d  (%.1f%% del total)",
             con_email_propio, 100*con_email_propio/max(len(negocios), 1))
    log.info("  Con palabras clave:    %4d  (%.1f%% del total)",
             con_keywords, 100*con_keywords/max(len(negocios), 1))
    log.info("=" * 72)

    # Reemplazar "<segmento>_<ambito>" por "enriquecido_<segmento>_<ambito>"
    salida = DATA_DIR / f"enriquecido_{ruta_in.name}"
    with open(salida, "w", encoding="utf-8") as f:
        json.dump([asdict(n) for n in negocios], f, ensure_ascii=False, indent=2)
    log.info("Guardado: %s", salida)
    log.info("Siguiente paso: python scripts/generar_csv.py --input %s", salida.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
