"""
Lector de certificados de Fintual Acciones.

- Certificado de transacciones y eventos de capital: compras, ventas, dividendos y splits
  → reconstruye las posiciones actuales y el precio promedio de compra (costo promedio).
- Certificado de inversión (balance): número de acciones por activo → sirve para verificar.

Trabaja sobre el TEXTO del PDF con espacios normalizados, así tolera que una celda venga
partida en varias líneas (como las fechas "2025-\\n07-07" o "US $\\n220,00").
La misma lógica está en gmail_fintual.gs (Apps Script) para la actualización automática.

Uso en el Mac (prueba / importación manual):
    .venv/bin/python fintual.py certificados/fintual_transacciones.pdf [certificados/fintual_balance.pdf]
"""
import re
import sys

NUM = r"\d[\d.]*,\d+"                       # número chileno: 1.234,56
USD = r"US\s?\$\s*(" + NUM + r")"
DATE = r"(\d{4})-\s*(\d{2})-\s*(\d{2})"
SYM = r"([A-Z][A-Z0-9.\-]{0,9})"
CAT = r"(Acciones|ETF|ETFs|Fondos?)"
SECTIONS = ["Compra de activos", "Venta de activos", "Dividendos recibidos", "Splits"]


def num(s):
    return float(s.replace(".", "").replace(",", "."))


def normalize(text):
    return re.sub(r"\s+", " ", text)


def split_sections(text):
    """{nombre de sección: texto}; las cabeceras de columna repetidas en cada página no molestan."""
    pos = sorted((text.find(s), s) for s in SECTIONS if text.find(s) >= 0)
    out = {}
    for i, (start, name) in enumerate(pos):
        end = pos[i + 1][0] if i + 1 < len(pos) else len(text)
        out[name] = text[start + len(name):end]
    return out


def parse_transactions(text):
    t = normalize(text)
    sec = split_sections(t)
    ev = []
    # nombre (no codicioso) seguido de SÍMBOLO y CATEGORÍA: el ancla de categoría evita confundir
    # palabras en mayúsculas del nombre (p. ej. "SAP SE ADR SAP Acciones") con el símbolo
    trade = re.compile(DATE + r" (.+?) " + SYM + " " + CAT + r" (\S+) " + USD + " (" + NUM + ")")
    for kind, name in (("buy", "Compra de activos"), ("sell", "Venta de activos")):
        for m in trade.finditer(sec.get(name, "")):
            y, mo, d, nm, sym, cat, otype, usd, qty = m.groups()
            ev.append({"t": kind, "date": f"{y}-{mo}-{d}", "name": nm.strip(), "sym": sym,
                       "usd": num(usd), "qty": num(qty)})
    div = re.compile(DATE + r" (.+?) " + SYM + " " + CAT + " " + USD + " " + USD + " " + USD)
    for m in div.finditer(sec.get("Dividendos recibidos", "")):
        y, mo, d, nm, sym, cat, gross, tax, net = m.groups()
        ev.append({"t": "div", "date": f"{y}-{mo}-{d}", "name": nm.strip(), "sym": sym,
                   "gross": num(gross), "net": num(net)})
    spl = re.compile(DATE + r" (.+?) " + SYM + " " + CAT + " (" + NUM + ") (" + NUM + ") (" + NUM + ")")
    for m in spl.finditer(sec.get("Splits", "")):
        y, mo, d, nm, sym, cat, a, b, ratio = m.groups()
        ev.append({"t": "split", "date": f"{y}-{mo}-{d}", "name": nm.strip(), "sym": sym,
                   "before": num(a), "after": num(b), "ratio": num(ratio)})
    return ev


def positions(events):
    """Aplica los eventos en orden de fecha con método de costo promedio."""
    order = {"split": 0, "buy": 1, "sell": 2, "div": 3}
    st = {}
    for e in sorted(events, key=lambda e: (e["date"], order[e["t"]])):
        p = st.setdefault(e["sym"], {"sym": e["sym"], "name": e["name"], "units": 0.0, "cost": 0.0,
                                      "realized": 0.0, "divs": 0.0})
        if e["t"] == "buy":
            p["units"] += e["qty"]
            p["cost"] += e["usd"]
        elif e["t"] == "sell":
            avg = p["cost"] / p["units"] if p["units"] else 0
            q = min(e["qty"], p["units"])
            p["realized"] += e["usd"] - avg * q
            p["cost"] -= avg * q
            p["units"] -= q
        elif e["t"] == "split":
            p["units"] *= e["ratio"]
        elif e["t"] == "div":
            p["divs"] += e["net"]
    return st


def current(events, min_units=1e-6):
    """Posiciones abiertas: símbolo, unidades y precio promedio de compra."""
    out = []
    for p in positions(events).values():
        if p["units"] > min_units:
            out.append({"sym": p["sym"].replace(".", "-"), "name": p["name"], "units": round(p["units"], 9),
                        "avg": round(p["cost"] / p["units"], 4), "cost": round(p["cost"], 2)})
    return sorted(out, key=lambda x: -x["cost"])


def parse_balance(text):
    """Certificado de inversión: [(nombre, unidades, balance USD)] + caja pendiente de invertir."""
    t = normalize(text)
    body = t.split("Balance (USD)", 1)[-1].split("Total", 1)[0]
    rows = [(m.group(1).strip(), float(m.group(2)), num(m.group(3)))
            for m in re.finditer(r"(.+?) (\d+\.\d+) " + USD, body)]
    cash = re.search(r"mantenía " + USD + r" en su cuenta", t)
    return rows, (num(cash.group(1)) if cash else None)


def pdf_text(path):
    import pypdf
    return "\n".join(p.extract_text() or "" for p in pypdf.PdfReader(path).pages)


def main():
    if "--guardar" in sys.argv:  # Mac: deja las posiciones para el panel local (reemplaza al Excel)
        sys.argv.remove("--guardar")
        import json
        import os
        from datetime import date
        cur = current(parse_transactions(pdf_text(sys.argv[1])))
        out = {"asof": date.today().isoformat(),
               "positions": [{"sym": p["sym"], "units": p["units"], "avg": p["avg"]} for p in cur]}
        dest = os.path.join(os.path.dirname(os.path.abspath(__file__)), "posiciones_fintual.json")
        with open(dest, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
        print(f"Guardado {dest} ({len(cur)} posiciones). El panel del Mac lo usará en vez del Excel.")
        return
    tx = pdf_text(sys.argv[1])
    ev = parse_transactions(tx)
    cnt = {k: sum(1 for e in ev if e["t"] == k) for k in ("buy", "sell", "div", "split")}
    print(f"Eventos leídos: {cnt['buy']} compras, {cnt['sell']} ventas, {cnt['div']} dividendos, {cnt['split']} splits")
    cur = current(ev)
    print(f"{'Símbolo':8} {'Unidades':>13} {'P. prom':>10} {'Costo':>10}")
    for p in cur:
        print(f"{p['sym']:8} {p['units']:13.9f} {p['avg']:10.2f} {p['cost']:10.2f}")
    print(f"Costo total: US$ {sum(p['cost'] for p in cur):,.2f}")
    if len(sys.argv) > 2:
        rows, cash = parse_balance(pdf_text(sys.argv[2]))
        names = {p["name"].lower(): p for p in cur}
        print(f"\nVerificación contra el certificado de balance ({len(rows)} activos, caja pendiente US$ {cash}):")
        ok = 0
        for nm, units, bal in rows:
            p = names.get(nm.lower())
            diff = (p["units"] - units) if p else None
            flag = "OK" if p and abs(diff) < 1e-6 else "DIFERENCIA"
            ok += flag == "OK"
            print(f"  {flag:10} {nm[:40]:40} certificado {units:.9f}  calculado {p['units'] if p else '—'}")
        extra = [p["sym"] for p in cur if p["name"].lower() not in {n.lower() for n, _, _ in rows}]
        print(f"  {ok}/{len(rows)} coinciden" + (f" · solo en el historial: {extra}" if extra else ""))


if __name__ == "__main__":
    main()
