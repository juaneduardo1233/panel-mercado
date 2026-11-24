@echo off
REM Panel de Mercado: actualiza data.js cada 120 s, abre el panel en el PC
REM y lo publica en la red WiFi para verlo desde el telefono (movil.html).
cd /d "%~dp0"
title Panel de Mercado - PC + movil (cierra esta ventana para detener)
echo Generando datos iniciales...
python panel_mercado.py
start /b python servidor_movil.py
start "" "%~dp0panel.html"
python panel_mercado.py --loop 120
