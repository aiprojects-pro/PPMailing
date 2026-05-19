#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")"

./run.sh scripts/buscar.py --segmento campamentos_verano --ambito andalucia

json_in="$(ls -t data/campamentos_verano_andalucia_*.json | head -1)"
./run.sh scripts/extraer_emails.py --input "$(basename "$json_in")"

json_enriquecido="$(ls -t data/enriquecido_campamentos_verano_andalucia_*.json | head -1)"
./run.sh scripts/generar_csv.py --input "$(basename "$json_enriquecido")"

csv_final="$(ls -t data/leads_campamentos_verano_andalucia_*.csv | head -1)"
./run.sh - "$csv_final" <<'PY'
import csv
import sys

ruta = sys.argv[1]
with open(ruta, encoding="utf-8", newline="") as f:
    rows = list(csv.DictReader(f))

total = len(rows)
aptos = sum(1 for r in rows if r.get("apto_campanya") == "SI")
con_email = sum(1 for r in rows if r.get("email"))
email_propio = sum(1 for r in rows if r.get("email_dominio_propio") in ("True", "true", "1", "SI"))
con_web = sum(1 for r in rows if r.get("web"))
con_telefono = sum(1 for r in rows if r.get("telefono"))

print("")
print("METRICAS")
print(f"csv={ruta}")
print(f"total_leads={total}")
print(f"aptos_campanya={aptos}")
print(f"con_email={con_email}")
print(f"email_dominio_propio={email_propio}")
print(f"con_web={con_web}")
print(f"con_telefono={con_telefono}")
PY
