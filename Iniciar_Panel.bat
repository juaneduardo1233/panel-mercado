@echo off
REM Panel de Mercado EE.UU. - actualiza data.js cada 120 s y abre el panel.
cd /d "%~dp0"
title Panel de Mercado - actualizando (cierra esta ventana para detener)
echo Generando datos iniciales...
python panel_mercado.py
start "" "%~dp0panel.html"
python panel_mercado.py --loop 120
