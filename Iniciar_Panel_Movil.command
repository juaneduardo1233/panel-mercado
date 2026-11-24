#!/bin/bash
# Panel de Mercado (macOS): actualiza data.js cada 120 s, abre el panel en el Mac
# y lo publica en la red WiFi para verlo desde el teléfono (movil.html).
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Preparando entorno Python (solo la primera vez)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
echo "Generando datos iniciales..."
.venv/bin/python panel_mercado.py
.venv/bin/python servidor_movil.py &
SRV=$!
trap 'kill $SRV 2>/dev/null' EXIT
open panel.html
.venv/bin/python panel_mercado.py --loop 120
