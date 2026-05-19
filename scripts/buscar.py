#!/usr/bin/env python3
"""
CLI — Buscar negocios de un segmento en Google Places.

Uso:
    python scripts/buscar.py --segmento campamentos_verano --ambito espana
    python scripts/buscar.py --segmento clubes_deportivos --ambito andalucia
    python scripts/buscar.py --listar-segmentos

Salida: data/<segmento>_<ambito>_AAAAMMDD.json  (consumido por extraer_emails.py)
"""

import argparse
import json
import logging
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import List

# Permitir importar desde el directorio raíz del proyecto
sys.path.insert(0, str(Path(__file__).parent.parent))

from config.base import GOOGLE_API_RATE_LIMIT, GOOGLE_PLACES_API_KEY, DATA_DIR, LOG_DIR
from config.ciudades_espana import SUBCONJUNTOS
from config.segmentos import obtener_segmento, listar_segmentos
from core.modelos import Negocio
from core.places_client import text_search_completa
from core.parser_y_dedup import place_a_negocio, deduplicar


def configurar_logging(segmento: str, ambito: str) -> None:
    log_file = LOG_DIR / f"buscar_{segmento}_{ambito}_{datetime.now():%Y%m%d_%H%M%S}.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.FileHandler(log_file, encoding="utf-8"),
                  logging.StreamHandler(sys.stdout)],
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Buscar negocios de un segmento en Google Places.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--segmento", help="ID del segmento (ver --listar-segmentos)")
    parser.add_argument("--ambito", default="espana",
                        choices=list(SUBCONJUNTOS.keys()),
                        help="Ámbito geográfico (defecto: espana)")
    parser.add_argument("--listar-segmentos", action="store_true",
                        help="Lista los segmentos disponibles y sale")
    parser.add_argument("--max-paginas", type=int, default=3,
                        help="Máx. páginas por query (1=20 resultados, 3=60). Defecto 3.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()

    if args.listar_segmentos:
        print("\nSegmentos disponibles:\n")
        for s in listar_segmentos():
            print(f"  • {s['id']:<35} → {s['nombre']}")
            print(f"    Producto CGD: {s['producto']}\n")
        return 0

    if not args.segmento:
        print("ERROR: debes indicar --segmento o usar --listar-segmentos", file=sys.stderr)
        return 1

    if not GOOGLE_PLACES_API_KEY or GOOGLE_PLACES_API_KEY == "pegar-aqui-la-clave-real-de-google-places":
        print(
            "ERROR: GOOGLE_PLACES_API_KEY no está configurada con una clave real. "
            "Crea /opt/cgd/cgd_scraper_v04/.env a partir de .env.example "
            "o exporta la variable antes de ejecutar.",
            file=sys.stderr,
        )
        return 1

    configurar_logging(args.segmento, args.ambito)
    log = logging.getLogger("buscar")

    try:
        seg = obtener_segmento(args.segmento)
    except ValueError as e:
        log.error(str(e))
        return 1

    ciudades = SUBCONJUNTOS[args.ambito]
    queries = seg["queries"]

    log.info("=" * 72)
    log.info("CGD - Buscador de negocios (Google Places)")
    log.info("Segmento: %s (%s)", args.segmento, seg["nombre_humano"])
    log.info("Producto: %s", seg["producto_cgd"])
    log.info("Ámbito: %s (%d ciudades)", args.ambito, len(ciudades))
    log.info("Queries por ciudad: %d", len(queries))
    log.info("Total búsquedas estimadas: %d", len(ciudades) * len(queries))
    log.info("=" * 72)

    todos: List[Negocio] = []
    total = len(ciudades) * len(queries)
    contador = 0

    for ciudad in ciudades:
        for query_base in queries:
            contador += 1
            query = f"{query_base} {ciudad['nombre']}"
            log.info("[%d/%d] %s", contador, total, query)
            try:
                places = text_search_completa(query, max_paginas=args.max_paginas)
                for p in places:
                    todos.append(place_a_negocio(
                        place=p,
                        segmento_id=args.segmento,
                        query_origen=query_base,
                        ciudad_origen=ciudad["nombre"],
                    ))
                log.info("     → %d resultados", len(places))
            except Exception as e:
                log.error("     ERROR: %s", e)
            time.sleep(GOOGLE_API_RATE_LIMIT)

    log.info("")
    log.info("Antes de deduplicar: %d resultados", len(todos))
    unicos = deduplicar(todos)
    log.info("Después de deduplicar: %d negocios únicos", len(unicos))

    # Resumen por CCAA
    log.info("")
    log.info("=== Resumen por CCAA ===")
    por_ccaa = {}
    for n in unicos:
        clave = n.ccaa or "(sin)"
        por_ccaa[clave] = por_ccaa.get(clave, 0) + 1
    for ccaa, n in sorted(por_ccaa.items(), key=lambda x: -x[1]):
        log.info("  %-30s %4d", ccaa, n)

    salida = DATA_DIR / f"{args.segmento}_{args.ambito}_{datetime.now():%Y%m%d}.json"
    with open(salida, "w", encoding="utf-8") as f:
        json.dump([asdict(n) for n in unicos], f, ensure_ascii=False, indent=2)
    log.info("")
    log.info("Guardado: %s", salida)
    log.info("Siguiente paso: python scripts/extraer_emails.py --input %s", salida.name)
    return 0


if __name__ == "__main__":
    sys.exit(main())
