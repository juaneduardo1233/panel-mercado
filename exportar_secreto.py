"""
Arma el contenido del secreto PANEL_PRIVATE_JSON de GitHub con tus datos privados:
posiciones del Excel, carteras de carteras.json y la clasificación de info_posiciones.json
(+ correcciones de profiles.json). Así el Excel nunca se sube al repositorio.

Uso (en el Mac, cada vez que cambies tu cartera):
    .venv/bin/python exportar_secreto.py
Luego copia el contenido de secreto_panel.json en GitHub:
    Settings › Secrets and variables › Actions › PANEL_PRIVATE_JSON › Update
"""
import json
import os

import panel_mercado as pm

OUT = os.path.join(pm.BASE, "secreto_panel.json")


def main():
    mine = [{"sym": h["sym"], "units": h["shares"], "avg": h["avg"]} for h in pm.read_portfolio()]
    extra = pm._load_json(pm.EXTRA, {}).get("carteras", [])
    info = {k: list(v) for k, v in pm.HOLDING_INFO.items()}
    for s, p in pm._load_json(pm.PROFILE_CACHE, {}).items():  # nombres/sectores ya revisados
        info.setdefault(s, [p["name"], p["sector"]])
    data = {"mine": mine, "carteras": extra, "info": info}
    text = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(text)
    print(f"Listo: {OUT}")
    print(f"  {len(mine)} posiciones propias, {len(extra)} cartera(s) adicional(es), {len(text):,} caracteres")
    print("  Copia TODO el contenido del archivo en el secreto PANEL_PRIVATE_JSON de GitHub.")
    print("  El archivo está en .gitignore: no se sube al repositorio.")


if __name__ == "__main__":
    main()
