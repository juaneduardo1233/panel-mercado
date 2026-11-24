#!/bin/bash
# Panel de Mercado EE.UU. (macOS) - actualiza data.js cada 120 s y abre el panel.
# Doble clic en Finder para ejecutar. Cierra esta ventana para detener.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
  echo "Preparando entorno Python (solo la primera vez)..."
  python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt || exit 1
fi
echo "Generando datos iniciales..."
.venv/bin/python panel_mercado.py
open panel.html
exec .venv/bin/python panel_mercado.py --loop 120
