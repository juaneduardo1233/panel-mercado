"""
Panel de Mercado EE.UU. - motor de datos.

Descarga precios (Yahoo Finance) y series de crédito/tasas (FRED), lee el
portafolio desde Portafolio_de_Inversiones.xlsx, calcula correlaciones,
señales y riesgo, y escribe data.js para que panel.html lo muestre.

Uso:
    python panel_mercado.py            # actualiza una vez
    python panel_mercado.py --loop 120 # actualiza cada 120 s (tiempo real)
"""
import argparse
import json
import math
import re
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote
from zoneinfo import ZoneInfo

import numpy as np
import openpyxl
import pandas as pd
import requests

BASE = os.path.dirname(os.path.abspath(__file__))
EXCEL = os.path.join(BASE, "Portafolio_de_Inversiones.xlsx")
OUT = os.path.join(BASE, "data.js")
CONFIG = os.path.join(BASE, "config.json")
EXTRA = os.path.join(BASE, "carteras.json")  # carteras adicionales para comparar
NY = ZoneInfo("America/New_York")
UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/130.0 Safari/537.36"}

# (símbolo, nombre, grupo)
UNIVERSE = [
    ("^GSPC", "S&P 500", "indice"), ("^IXIC", "Nasdaq Composite", "indice"),
    ("^NDX", "Nasdaq 100", "indice"), ("^DJI", "Dow Jones", "indice"),
    ("^RUT", "Russell 2000", "indice"), ("RSP", "S&P 500 Equal Weight", "indice"),
    ("^VIX", "VIX", "vol"), ("^VIX3M", "VIX 3 meses", "vol"),
    ("^MOVE", "MOVE (vol. bonos)", "vol"), ("^SKEW", "SKEW", "vol"),
    ("^IRX", "Tesoro 3M", "tasa"), ("^FVX", "Tesoro 5Y", "tasa"),
    ("^TNX", "Tesoro 10Y", "tasa"), ("^TYX", "Tesoro 30Y", "tasa"),
    ("HYG", "High Yield (HYG)", "credito"), ("JNK", "High Yield (JNK)", "credito"),
    ("LQD", "Grado inversión (LQD)", "credito"), ("IEF", "Tesoro 7-10Y (IEF)", "credito"),
    ("TLT", "Tesoro 20Y+ (TLT)", "credito"), ("SHY", "Tesoro 1-3Y (SHY)", "credito"),
    ("CL=F", "Petróleo WTI", "commodity"), ("BZ=F", "Petróleo Brent", "commodity"),
    ("NG=F", "Gas natural", "commodity"), ("GC=F", "Oro", "commodity"),
    ("SI=F", "Plata", "commodity"), ("HG=F", "Cobre", "commodity"),
    ("DX-Y.NYB", "Índice Dólar (DXY)", "fx"), ("EURUSD=X", "EUR/USD", "fx"),
    ("JPY=X", "USD/JPY", "fx"), ("CLP=X", "USD/CLP", "fx"), ("BTC-USD", "Bitcoin", "fx"),
    ("SPY", "SPDR S&P 500", "sector"), ("QQQ", "Nasdaq 100 ETF", "sector"),
    ("IWM", "Russell 2000 ETF", "sector"),
    ("XLK", "Tecnología", "sector"), ("XLE", "Energía", "sector"),
    ("XLF", "Financiero", "sector"), ("XLV", "Salud", "sector"),
    ("XLI", "Industrial", "sector"), ("XLY", "Consumo discrecional", "sector"),
    ("XLP", "Consumo básico", "sector"), ("XLU", "Utilities", "sector"),
    ("XLB", "Materiales", "sector"), ("XLRE", "Inmobiliario", "sector"),
    ("XLC", "Comunicaciones", "sector"), ("SMH", "Semiconductores", "sector"),
]
SECTOR_ETFS = ["XLK", "XLE", "XLF", "XLV", "XLI", "XLY", "XLP", "XLU", "XLB", "XLRE", "XLC", "SMH"]
INTRADAY = ["^GSPC", "^IXIC", "^DJI", "^RUT", "^VIX", "^FVX", "^TNX", "^TYX", "^IRX",
            "CL=F", "BZ=F", "GC=F", "DX-Y.NYB", "BTC-USD", "HYG", "CLP=X", "XLE", "XLK",
            "RSP", "^VIX3M", "SMH", "IEF"]  # completan los gráficos en rango 1D

FRED_SERIES = {
    "BAMLH0A0HYM2": "ICE BofA US High Yield OAS",
    "BAMLC0A0CM": "ICE BofA US Corporate (IG) OAS",
    "T10Y2Y": "Curva 10Y - 2Y",
    "T10Y3M": "Curva 10Y - 3M",
    "DGS2": "Tesoro 2Y",
    "DGS5": "Tesoro 5Y",
    "DGS10": "Tesoro 10Y",
    "T10YIE": "Inflación implícita 10Y (breakeven)",
    "DFII10": "Tasa real 10Y (TIPS)",
}

# ---- datos privados: nunca van en el código (el repositorio en GitHub es público)
# En el Mac se leen del Excel, carteras.json e info_posiciones.json (fuera de git).
# En la nube vienen del secreto PANEL_PRIVATE_JSON (ver exportar_secreto.py).
INFO_FILE = os.path.join(BASE, "info_posiciones.json")
_PRIV = json.loads(os.environ["PANEL_PRIVATE_JSON"]) if os.environ.get("PANEL_PRIVATE_JSON") else None


def _load_json(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


# Clasificación manual de posiciones (nombre, sector); lo que no esté aquí se clasifica con Yahoo
HOLDING_INFO = {k: tuple(v) for k, v in ((_PRIV or {}).get("info") or _load_json(INFO_FILE, {})).items()}

# Supuestos de la proyección de largo plazo (retorno = dividendo + crecimiento de utilidades + cambio de múltiplo)
FUND_CACHE = os.path.join(BASE, "fundamentals.json")
LT_GROWTH = 0.045      # crecimiento nominal de utilidades de largo plazo (≈ PIB nominal EE.UU.)
FADE = 0.75            # el crecimiento esperado por analistas converge al de largo plazo un 25% por año
PE_ANCHOR = 18.0       # P/U de referencia; se asume que el múltiplo recorre la mitad del camino (log) en 10 años
GOLD_RET = 0.04        # oro: sin flujos, ≈ inflación + 1,5%
VOL_FLOOR_SPX = 0.16   # volatilidad de largo plazo del S&P 500; si la reciente es menor se escala
HAIRCUT = 0.75         # el consenso suele ser optimista: se usa el 75% del crecimiento sobre el de largo plazo
# ETFs: Yahoo no entrega consenso; S&P 500 y Nasdaq 100 = consenso bottom-up aproximado de 2 años (revisar)
ETF_GROWTH = {"VOO": 0.12, "SPY": 0.12, "QQQ": 0.15, "VNQ": 0.035, "XLU": 0.06, "DVY": 0.04}
NO_RERATE = {"Inmobiliario"}  # REITs: el P/U contable no es representativo (se valoran por FFO)
PROJ_BENCH = {"SPY": "S&P 500 (VOO/SPY)", "QQQ": "Nasdaq 100 (QQQ)"}
BOND_ETFS = {"TLT", "IEF", "SHY", "LQD", "HYG", "JNK"}  # renta fija: retorno esperado ≈ rendimiento actual
# ETFs que el simulador ofrece agregar además de las posiciones de las carteras (sector para el panel)
SIM_ETFS = {"SPY": "Índice S&P 500", "QQQ": "Índice Nasdaq 100", "IWM": "Índice EE.UU.", "RSP": "Índice S&P 500",
            "XLK": "Tecnología", "XLE": "Energía", "XLF": "Financiero", "XLV": "Salud", "XLI": "Industrial",
            "XLY": "Consumo discrecional", "XLP": "Consumo básico", "XLU": "Utilities", "XLB": "Materiales",
            "XLRE": "Inmobiliario", "XLC": "Comunicaciones", "SMH": "Semiconductores",
            "SHY": "Renta fija", "IEF": "Renta fija", "TLT": "Renta fija", "LQD": "Renta fija", "HYG": "Renta fija"}

session = requests.Session()
session.headers.update(UA)
# FRED (Akamai) corta la conexión con User-Agents de navegador o desconocidos;
# acepta el de python-requests por defecto, así que se usa una sesión aparte sin cambiarlo.
fred_session = requests.Session()


def log(msg):
    print(f"[{datetime.now():%H:%M:%S}] {msg}", flush=True)


# ---------------------------------------------------------------- descarga
def yahoo_chart(sym, rng="2y", interval="1d"):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{quote(sym)}"
           f"?range={rng}&interval={interval}&includePrePost=false")
    for attempt in range(3):
        try:
            r = session.get(url, timeout=20)
            if r.status_code == 200:
                res = r.json()["chart"]["result"][0]
                ts = res.get("timestamp") or []
                closes = res["indicators"]["quote"][0].get("close") or []
                return {"meta": res["meta"], "t": ts, "c": closes}
        except Exception:
            pass
        time.sleep(1 + attempt)
    return None


def load_config():
    cfg = {"fred_api_key": "", "refresh_seconds": 120}
    if os.path.exists(CONFIG):
        try:
            with open(CONFIG, encoding="utf-8") as f:
                cfg.update(json.load(f))
        except Exception as e:
            log(f"config.json ilegible: {e}")
    else:
        with open(CONFIG, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
    cfg["fred_api_key"] = os.environ.get("FRED_API_KEY", cfg.get("fred_api_key", "")).strip()
    if os.environ.get("PANEL_REFRESH"):
        cfg["refresh_seconds"] = int(os.environ["PANEL_REFRESH"])
    return cfg


def fred_series(sid, key, start):
    """FRED vía API oficial (con key) o, si no hay key, vía CSV público."""
    try:
        if key:
            r = fred_session.get("https://api.stlouisfed.org/fred/series/observations",
                            params={"series_id": sid, "api_key": key, "file_type": "json",
                                    "observation_start": start}, timeout=25)
            r.raise_for_status()
            obs = [(o["date"], float(o["value"])) for o in r.json()["observations"]
                   if o["value"] not in (".", "")]
        else:
            r = fred_session.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}&cosd={start}",
                                 timeout=20)
            r.raise_for_status()
            obs = []
            for line in r.text.strip().splitlines()[1:]:
                d, v = line.split(",")
                if v not in (".", ""):
                    obs.append((d, float(v)))
        return obs or None
    except Exception:
        return None


_fred_cache = {"t": 0, "data": {}}


def get_fred(cfg, start):
    # 1 h si la última descarga trajo el spread HY; si falló, se reintenta a los 10 min
    ttl = 3600 if "BAMLH0A0HYM2" in _fred_cache["data"] else 600
    if time.time() - _fred_cache["t"] < ttl:
        return _fred_cache["data"]
    with ThreadPoolExecutor(max_workers=5) as ex:
        raw = dict(zip(FRED_SERIES, ex.map(lambda sid: fred_series(sid, cfg["fred_api_key"], start),
                                            FRED_SERIES)))
    fred = {sid: {"name": FRED_SERIES[sid], "dates": [d for d, _ in obs],
                  "values": [round(v, 3) for _, v in obs]}
            for sid, obs in raw.items() if obs}
    log(f"FRED: {len(fred)}/{len(FRED_SERIES)} series" + ("" if "BAMLH0A0HYM2" in fred else
        " (sin acceso: agregue fred_api_key en config.json; se usa proxy HYG/IEF)"))
    _fred_cache.update(t=time.time(), data=fred)
    return fred


_crumb = {"v": None, "t": 0}


def yahoo_crumb():
    """Token que Yahoo exige en quote/quoteSummary. Se reutiliza durante 1 hora."""
    if _crumb["v"] and time.time() - _crumb["t"] < 3600:
        return _crumb["v"]
    session.get("https://fc.yahoo.com", timeout=15)  # entrega la cookie que exige el crumb (responde 404)
    crumb = session.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=15).text.strip()
    if not crumb or crumb.startswith("{") or "<" in crumb:
        raise RuntimeError("Yahoo no entregó crumb")
    _crumb.update(v=crumb, t=time.time())
    return crumb


# ---------------------------------------------------------------- clasificación automática
PROFILE_CACHE = os.path.join(BASE, "profiles.json")
YF_SECTOR = {
    "Technology": "Tecnología", "Communication Services": "Comunicaciones",
    "Consumer Cyclical": "Consumo discrecional", "Consumer Defensive": "Consumo básico",
    "Energy": "Energía", "Financial Services": "Financiero", "Healthcare": "Salud",
    "Industrials": "Industrial", "Basic Materials": "Materiales", "Real Estate": "Inmobiliario",
    "Utilities": "Utilities",
}
# categoría Morningstar de un ETF (fragmento, en minúsculas) -> sector del panel; gana el primero que calce
YF_ETF_CATEGORY = [
    ("real estate", "Inmobiliario"), ("utilities", "Utilities"), ("technology", "Tecnología"),
    ("health", "Salud"), ("financial", "Financiero"), ("energy", "Energía"),
    ("natural resources", "Materiales"), ("communications", "Comunicaciones"),
    ("consumer cyclical", "Consumo discrecional"), ("consumer defensive", "Consumo básico"),
    ("industrials", "Industrial"), ("digital assets", "Cripto"),
    ("bond", "Renta fija"), ("treasury", "Renta fija"), ("government", "Renta fija"),
    ("muni", "Renta fija"), ("ultrashort", "Renta fija"), ("inflation-protected", "Renta fija"),
    ("foreign", "Internacional"), ("emerging", "Internacional"), ("world", "Internacional"),
    ("europe", "Internacional"), ("japan", "Internacional"), ("china", "Internacional"),
    ("large value", "Dividendos"), ("large", "Índice EE.UU."), ("mid-cap", "Índice EE.UU."),
    ("small", "Índice EE.UU."),
]


def classify(sym, x):
    """(nombre, sector) desde la respuesta de quoteSummary (módulos price, assetProfile, fundProfile)."""
    pr, ap, fp = x.get("price") or {}, x.get("assetProfile") or {}, x.get("fundProfile") or {}
    long_, short = pr.get("longName"), pr.get("shortName")
    name = (long_ if long_ and len(long_) <= 32 else short or long_ or sym).strip()
    qt = pr.get("quoteType")
    if qt == "CRYPTOCURRENCY":
        return name, "Cripto"
    if ap.get("sector"):
        ind = (ap.get("industry") or "").lower()
        if "semiconductor" in ind:
            return name, "Semiconductores"
        if "software" in ind:
            return name, "Software"
        return name, YF_SECTOR.get(ap["sector"], ap["sector"])
    cat = (fp.get("categoryName") or "").lower()
    if "gold" in name.lower() and ("commodit" in cat or not cat):
        return name, "Oro"
    for key, sector in YF_ETF_CATEGORY:
        if key in cat:
            return name, sector
    if cat:
        return name, f"ETF: {fp['categoryName']}"
    return name, "Otros"


def profiles(syms):
    """Nombre y sector de los símbolos que no están en HOLDING_INFO. Cache permanente en
    profiles.json (se puede editar a mano para corregir un sector); si Yahoo falla se
    reintenta en la siguiente actualización, como máximo una vez cada 30 min."""
    try:
        with open(PROFILE_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    missing = [s for s in syms if s not in cache]
    if not missing or time.time() - _profile_fail["t"] < 1800:
        return cache
    try:
        crumb = yahoo_crumb()

        def one(s):
            try:
                r = session.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(s)}",
                                params={"modules": "price,assetProfile,fundProfile", "crumb": crumb}, timeout=20)
                return classify(s, r.json()["quoteSummary"]["result"][0])
            except Exception:
                return None

        with ThreadPoolExecutor(max_workers=6) as ex:
            got = {s: v for s, v in zip(missing, ex.map(one, missing)) if v}
    except Exception as e:
        got = {}
        log(f"Clasificación automática no disponible ({e})")
    if got:
        cache.update({s: {"name": n, "sector": sec} for s, (n, sec) in got.items()})
        with open(PROFILE_CACHE, "w", encoding="utf-8") as f:
            json.dump(cache, f, ensure_ascii=False, indent=2)
        log("Clasificados automáticamente: " + ", ".join(f"{s} → {v[1]}" for s, v in got.items()))
    if len(got) < len(missing):
        _profile_fail["t"] = time.time()
    return cache


_profile_fail = {"t": 0}


def holding_info(sym, prof):
    """(nombre, sector): primero la lista manual HOLDING_INFO, luego la clasificación de Yahoo."""
    if sym in HOLDING_INFO:
        return HOLDING_INFO[sym]
    p = prof.get(sym)
    return (p["name"], p["sector"]) if p else (sym, "Otros")


def fundamentals(syms):
    """Utilidades proyectadas (EPS forward, crecimiento de analistas), P/U y dividendos. Cache de 12 h."""
    try:
        with open(FUND_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
        if time.time() - cache.get("t", 0) < 12 * 3600 and set(syms) <= set(cache["data"]):
            return cache["data"]
    except Exception:
        cache = None
    try:
        crumb = yahoo_crumb()
        r = session.get("https://query1.finance.yahoo.com/v7/finance/quote",
                        params={"symbols": ",".join(syms), "crumb": crumb}, timeout=20)
        r.raise_for_status()
        out = {}
        for x in r.json()["quoteResponse"]["result"]:
            dy = x.get("dividendYield")
            out[x["symbol"]] = {
                "type": x.get("quoteType"), "epsTtm": x.get("epsTrailingTwelveMonths"),
                "epsFwd": x.get("epsForward"), "peFwd": x.get("forwardPE"), "peTtm": x.get("trailingPE"),
                "dy": dy / 100 if dy is not None else x.get("trailingAnnualDividendYield"),
            }

        def trend(s):
            try:
                t = session.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(s)}",
                                params={"modules": "earningsTrend", "crumb": crumb}, timeout=20)
                rows = t.json()["quoteSummary"]["result"][0]["earningsTrend"]["trend"]
                return {x["period"]: (x.get("growth") or {}).get("raw") for x in rows}
            except Exception:
                return {}

        eq = [s for s, v in out.items() if v["type"] == "EQUITY"]
        with ThreadPoolExecutor(max_workers=6) as ex:
            for s, g in zip(eq, ex.map(trend, eq)):
                out[s]["growth"] = g
        with open(FUND_CACHE, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "data": out}, f)
        log(f"Fundamentales: {len(out)} instrumentos")
        return out
    except Exception as e:
        log(f"Fundamentales no disponibles ({e}); se usa la última copia")
        return cache["data"] if cache else {}


def expected_return(sym, sector, f):
    """Retorno anual compuesto esperado a 10 años = dividendo + crecimiento de utilidades + cambio de múltiplo."""
    if sym in BOND_ETFS:
        dy = f.get("dy") or 0
        return {"er": clean(dy, 4), "dy": clean(dy, 4), "g": 0, "g10": 0, "rerate": 0, "pe": None,
                "method": "Renta fija: rendimiento actual del fondo"}
    if sym == "IAU":
        return {"er": GOLD_RET, "dy": 0, "g": None, "g10": None, "rerate": 0, "pe": None, "method": "Oro: inflación + 1,5%"}
    dy = f.get("dy") or 0
    g = None
    if f.get("type") == "EQUITY":
        gr = f.get("growth") or {}
        vals = [v for v in (gr.get("0y"), gr.get("+1y")) if v is not None]
        if vals:
            g = sum(vals) / len(vals)
        elif f.get("epsFwd") and f.get("epsTtm") and f["epsTtm"] > 0:
            g = f["epsFwd"] / f["epsTtm"] - 1
        method = "Consenso de analistas (EPS año actual y próximo)" if g is not None else "Sin consenso: crecimiento de largo plazo"
    else:
        g = ETF_GROWTH.get(sym)
        method = ("ETF: consenso aproximado del índice (supuesto manual)" if g is not None
                  else "ETF: dividendo + crecimiento de largo plazo (supuesto)")
    if g is None:
        g = LT_GROWTH
    g = min(max(g, -0.10), 0.35)
    if sector in NO_RERATE:  # la utilidad contable de un REIT es ruidosa; su flujo (AFFO) crece ~4-6%
        g = min(g, 0.06)
    g = LT_GROWTH + (g - LT_GROWTH) * HAIRCUT
    g10 = LT_GROWTH + (g - LT_GROWTH) * (1 - FADE ** 10) / (10 * (1 - FADE))
    pe = f.get("peFwd") or f.get("peTtm")
    rerate = 0.0
    if pe and pe > 0 and sector not in NO_RERATE:
        target = math.sqrt(pe * PE_ANCHOR)
        rerate = min(max((target / pe) ** 0.1 - 1, -0.04), 0.02)
    er = min(max(dy + g10 + rerate, -0.05), 0.20)
    return {"er": clean(er, 4), "dy": clean(dy, 4), "g": clean(g, 4), "g10": clean(g10, 4),
            "rerate": clean(rerate, 4), "pe": clean(pe, 1), "method": method}


def build_projection(portfolio, df, rets):
    rows = portfolio["rows"]
    fund = fundamentals([r["sym"] for r in rows] + list(PROJ_BENCH))
    hold = []
    for r in rows:
        e = expected_return(r["sym"], r["sector"], fund.get(r["sym"], {}))
        hold.append({"sym": r["sym"], "name": r["name"], "sector": r["sector"], "weight": r["weight"], **e})
    mu = sum((h["weight"] or 0) * h["er"] for h in hold)

    def ann(s):
        s = s.dropna()
        return clean((s.iloc[-1] / s.iloc[0]) ** (252 / (len(s) - 1)) - 1, 4) if len(s) > 60 else None

    value = pd.Series(portfolio["value"], index=df.index, dtype="float64")
    spx_vol = rets["^GSPC"].std() * math.sqrt(252)
    scale = max(1.0, VOL_FLOOR_SPX / spx_vol)  # periodo reciente más tranquilo que el promedio histórico
    vol = lambda s: clean(s.pct_change(fill_method=None).std() * math.sqrt(252) * scale, 4)
    bench = {}
    for s, name in PROJ_BENCH.items():
        if s in df:
            e = expected_return(s, "", fund.get(s, {}))
            bench[s] = {"name": name, "mu": e["er"], "sigma": vol(df[s]), "dy": e["dy"], "g10": e["g10"],
                        "rerate": e["rerate"], "pe": e["pe"], "hist": ann(df[s])}
    pr = value.pct_change(fill_method=None)
    for s, b in bench.items():  # correlación cartera-benchmark para simular ambos en conjunto
        a = pd.concat([pr, rets[s]], axis=1).dropna()
        b["rho"] = clean(a.iloc[:, 0].corr(a.iloc[:, 1]), 3)
    return {"holdings": sorted(hold, key=lambda h: -h["er"]), "mu": clean(mu, 4), "sigma": vol(value),
            "hist": ann(value), "bench": bench, "volScale": clean(scale, 3), "fundOk": bool(fund),
            "assump": {"ltGrowth": LT_GROWTH, "fade": FADE, "peAnchor": PE_ANCHOR, "gold": GOLD_RET, "haircut": HAIRCUT}}


# ---------------------------------------------------------------- utilidades
def clean(x, nd=4):
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    if math.isnan(x) or math.isinf(x):
        return None
    return round(x, nd)


def series_list(s, nd=4):
    return [clean(v, nd) for v in s.tolist()]


def rsi(s, n=14):
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def pct_change_back(s, days):
    s = s.dropna()
    if len(s) <= days:
        return None
    return s.iloc[-1] / s.iloc[-1 - days] - 1


def _nth_weekday(y, m, wd, n):
    """n-ésimo día `wd` (0 = lunes) del mes; n = -1 para el último."""
    if n > 0:
        d = date(y, m, 1)
        return d + timedelta(days=(wd - d.weekday()) % 7 + 7 * (n - 1))
    d = date(y, m + 1, 1) - timedelta(days=1) if m < 12 else date(y, 12, 31)
    return d - timedelta(days=(d.weekday() - wd) % 7)


def _easter(y):
    """Domingo de Pascua (algoritmo anónimo gregoriano)."""
    a, b, c = y % 19, y // 100, y % 100
    d, e = b // 4, b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = c // 4, c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    return date(y, month, (h + l - 7 * m + 114) % 31 + 1)


def _observed(d):
    """Feriado en sábado se toma el viernes; en domingo, el lunes."""
    return d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d


def nyse_holidays(y):
    """{fecha: nombre} de los días en que la Bolsa de Nueva York está cerrada (reglas vigentes)."""
    h = {
        _nth_weekday(y, 1, 0, 3): "Martin Luther King",
        _nth_weekday(y, 2, 0, 3): "Presidentes",
        _easter(y) - timedelta(days=2): "Viernes Santo",
        _nth_weekday(y, 5, 0, -1): "Memorial Day",
        _observed(date(y, 6, 19)): "Juneteenth",
        _observed(date(y, 7, 4)): "Independencia",
        _nth_weekday(y, 9, 0, 1): "Labor Day",
        _nth_weekday(y, 11, 3, 4): "Acción de Gracias",
        _observed(date(y, 12, 25)): "Navidad",
    }
    # Año Nuevo en sábado NO se compensa el viernes 31-dic (regla de la NYSE); en domingo pasa al lunes
    ny = date(y, 1, 1)
    if ny.weekday() != 5:
        h[_observed(ny)] = "Año Nuevo"
    return h


def nyse_early_close(d):
    """True si la sesión cierra a las 13:00 (víspera del 4 de julio, viernes tras Acción de Gracias, 24-dic)."""
    if d.weekday() >= 5 or d in nyse_holidays(d.year):
        return False
    return (d == _nth_weekday(d.year, 11, 3, 4) + timedelta(days=1)
            or (d.month, d.day) in ((7, 3), (12, 24)))


def market_status(now=None):
    now = now or datetime.now(NY)
    today = now.date()
    if now.weekday() >= 5:
        return "Cerrado (fin de semana)"
    fer = nyse_holidays(today.year).get(today)
    if fer:
        return f"Feriado: {fer}"
    close = 780 if nyse_early_close(today) else 960  # minutos desde medianoche, hora de Nueva York
    t = now.hour * 60 + now.minute
    if 570 <= t < close:
        return "Abierto (cierra 13:00)" if close == 780 else "Abierto"
    if 240 <= t < 570:
        return "Pre-apertura" + (" · media jornada" if close == 780 else "")
    if close <= t < 1200:
        return "Post-cierre"
    return "Cerrado"


POS_LOCAL = os.path.join(BASE, "posiciones_fintual.json")   # Mac: importadas con fintual.py (fuera de git)


def fintual_positions():
    """Posiciones importadas de los certificados de Fintual. Tienen prioridad sobre el Excel y el
    secreto porque reflejan cada compra (incluidas las fracciones en las bajadas).
    - Nube: se piden al script de Gmail del usuario (FINTUAL_URL + FINTUAL_KEY, secretos de GitHub).
    - Mac: posiciones_fintual.json, creado con  fintual.py <certificado> --guardar"""
    data = None
    url, key = os.environ.get("FINTUAL_URL", "").strip(), os.environ.get("FINTUAL_KEY", "").strip()
    if url and key:
        try:
            r = requests.post(url, data={"key": key}, timeout=30)  # POST: la clave no va en la URL
            r.raise_for_status()
            data = r.json()
            if data.get("error"):
                raise ValueError(data["error"])
        except Exception as e:
            log(f"Posiciones de Fintual no disponibles ({type(e).__name__}); se usan las anteriores")
            data = None
    elif os.path.exists(POS_LOCAL):
        data = _load_json(POS_LOCAL, None)
    if not data or not data.get("positions"):
        return None
    out = []
    for x in data["positions"]:
        try:
            sym, units = str(x["sym"]).strip().upper().replace(".", "-"), float(x["units"])
        except (KeyError, TypeError, ValueError):
            continue
        if re.match(r"^[A-Z][A-Z0-9\-]{0,9}$", sym) and units > 0:
            out.append({"sym": sym, "shares": units, "avg": float(x.get("avg") or 0), "xl_price": None})
    if out:
        log(f"Posiciones desde certificado de Fintual del {data.get('asof', '?')} ({len(out)} activos)")
    return out or None


def read_portfolio():
    fp = fintual_positions()
    if fp:
        return fp
    if _PRIV is not None:
        return [{"sym": str(x["sym"]).strip().upper(), "shares": float(x["units"]), "avg": float(x.get("avg") or 0),
                 "xl_price": None} for x in _PRIV.get("mine", []) if x.get("sym") and x.get("units")]
    wb = openpyxl.load_workbook(EXCEL, data_only=True)
    ws = wb.worksheets[0]
    header_row, cols = None, {}
    for row in ws.iter_rows(min_row=1, max_row=10):
        vals = [str(c.value).strip() if c.value is not None else "" for c in row]
        if "Symbol" in vals:
            header_row = row[0].row
            cols = {v: i for i, v in enumerate(vals)}
            break
    if header_row is None:
        raise RuntimeError("No se encontró la columna 'Symbol' en el Excel")
    out = []
    for row in ws.iter_rows(min_row=header_row + 1, values_only=True):
        sym = row[cols["Symbol"]]
        if not sym or str(sym).strip().upper() == "TOTAL":
            continue
        shares = row[cols.get("Shares Owned")]
        avg = row[cols.get("Avg. Price")]
        if shares is None:
            continue
        out.append({"sym": str(sym).strip().upper(), "shares": float(shares),
                    "avg": float(avg or 0), "xl_price": row[cols.get("Price")]})
    return out


def read_extra_portfolios():
    """Carteras de terceros definidas en carteras.json (se comparan con la propia)."""
    if _PRIV is not None:
        raw = _PRIV.get("carteras", [])
    elif not os.path.exists(EXTRA):
        return []
    else:
        try:
            with open(EXTRA, encoding="utf-8") as f:
                raw = json.load(f).get("carteras", [])
        except Exception as e:
            log(f"carteras.json ilegible: {e}")
            return []
    out = []
    for c in raw:
        hold = [{"sym": str(x["sym"]).strip().upper(), "shares": float(x["units"]),
                 "avg": float(x.get("avg") or 0), "xl_price": None}
                for x in c.get("posiciones", []) if x.get("sym") and x.get("units")]
        if hold:
            out.append({"name": c.get("nombre", "Sin nombre"), "source": c.get("fuente", ""),
                        "note": c.get("nota", ""), "holdings": hold})
    return out


# ---------------------------------------------------------------- análisis
def build(cfg):
    holdings = read_portfolio()
    extras = read_extra_portfolios()
    every = holdings + [h for e in extras for h in e["holdings"]]
    names = {s: n for s, n, _ in UNIVERSE}
    groups = {s: g for s, _, g in UNIVERSE}
    prof = profiles(list(dict.fromkeys(h["sym"] for h in every if h["sym"] not in HOLDING_INFO)))
    for h in every:
        if h["sym"] not in names:
            names[h["sym"]] = holding_info(h["sym"], prof)[0]
            groups[h["sym"]] = "cartera"
    symbols = list(names)

    with ThreadPoolExecutor(max_workers=10) as ex:
        daily = dict(zip(symbols, ex.map(lambda s: yahoo_chart(s, "2y", "1d"), symbols)))
        intra = dict(zip(INTRADAY, ex.map(lambda s: yahoo_chart(s, "1d", "5m"), INTRADAY)))

    # Serie diaria alineada al calendario bursátil de EE.UU. (S&P 500)
    frames = {}
    meta = {}
    for s, d in daily.items():
        if not d or not d["t"]:
            log(f"sin datos: {s}")
            continue
        idx = pd.to_datetime(d["t"], unit="s", utc=True).tz_convert(NY).normalize().tz_localize(None)
        ser = pd.Series(d["c"], index=idx, dtype="float64")
        ser = ser[~ser.index.duplicated(keep="last")]
        px = d["meta"].get("regularMarketPrice")
        if px is not None and len(ser):
            ser.iloc[-1] = px
        frames[s] = ser
        meta[s] = d["meta"]
    if "^GSPC" not in frames:
        raise RuntimeError("No se pudo descargar el S&P 500 (¿sin conexión?)")
    cal = frames["^GSPC"].dropna().index
    df = pd.DataFrame({s: v.reindex(cal.union(v.index)).ffill().reindex(cal) for s, v in frames.items()})

    # Antes de la apertura (o en un feriado de la bolsa) los activos que cotizan casi 24 h
    # (futuros, VIX, divisas, cripto) ya tienen la barra de hoy, pero el calendario del S&P
    # termina en la sesión anterior. Se guarda esa barra "viva" para que gráficos y métricas
    # terminen en el mismo precio que muestra la tarjeta.
    last_bar = {s: f.dropna().index[-1] for s, f in frames.items() if len(f.dropna())}
    live_day = max(last_bar.values())
    live = {}
    if live_day > cal[-1] and live_day.weekday() < 5:
        live = {s: frames[s].dropna().iloc[-1] for s, d in last_bar.items() if d == live_day}
        log(f"{len(live)} instrumentos ya cotizan el {live_day:%d-%m} (antes de la sesión del S&P)")
    last_day = live_day if live else df.index[-1]

    # ---- cotizaciones y métricas por instrumento
    quotes = {}
    in_year = df.index[df.index.year == last_day.year]
    for s in df.columns:
        raw = frames[s].dropna()
        if len(raw) < 2:
            continue
        price = raw.iloc[-1]
        prev = raw.iloc[-2]
        col = df[s].dropna()
        if s in live:
            col = pd.concat([col, pd.Series([live[s]], index=[live_day])])
        last252 = col.iloc[-252:]
        if len(in_year):
            ytd_base = df[s].loc[:in_year[0]].dropna()
            ytd_base = ytd_base.iloc[-2] if len(ytd_base) >= 2 else col.iloc[0]
        else:  # primer día del año y solo existe la barra viva: base = cierre del año anterior
            ytd_base = df[s].dropna().iloc[-1]
        m = meta[s]
        quotes[s] = {
            "name": names[s], "group": groups[s],
            "price": clean(price), "prev": clean(prev),
            "chg": clean(price - prev), "chgPct": clean(price / prev - 1, 6),
            "r1w": clean(pct_change_back(col, 5), 6), "r1m": clean(pct_change_back(col, 21), 6),
            "r3m": clean(pct_change_back(col, 63), 6), "r1y": clean(pct_change_back(col, 252), 6),
            "ytd": clean(price / ytd_base - 1, 6),
            "hi52": clean(last252.max()), "lo52": clean(last252.min()),
            "ma50": clean(col.iloc[-50:].mean()), "ma200": clean(col.iloc[-200:].mean()),
            "rsi": clean(rsi(col).iloc[-1], 1),
            "time": datetime.fromtimestamp(m.get("regularMarketTime", 0), NY).strftime("%d-%m %H:%M"),
        }
        # tasas: el cambio relevante es en puntos básicos
        if groups[s] == "tasa":
            quotes[s]["chgBp"] = clean((price - prev) * 100, 1)

    intraday = {}
    for s, d in intra.items():
        if not d or not d["t"]:
            continue
        pts = [(t, c) for t, c in zip(d["t"], d["c"]) if c is not None]
        if not pts:
            continue
        intraday[s] = {
            "t": [datetime.fromtimestamp(t, NY).strftime("%H:%M") for t, _ in pts],
            "c": [clean(c) for _, c in pts],
            "prev": clean(d["meta"].get("chartPreviousClose") or d["meta"].get("previousClose")),
        }

    # ---- retornos y correlaciones
    rets = df.pct_change(fill_method=None)
    rate_cols = [s for s in df.columns if groups.get(s) == "tasa"]
    for s in rate_cols:  # para tasas se usan cambios absolutos
        rets[s] = df[s].diff()

    def rolling_corr(a, b, w):
        if a not in rets or b not in rets:
            return None
        return series_list(rets[a].rolling(w, min_periods=int(w * .8)).corr(rets[b]), 3)

    corr_pairs = {
        "XLE_XLK": ("XLE", "XLK", "Energía (XLE) vs Tecnología (XLK)"),
        "CL_QQQ": ("CL=F", "QQQ", "Petróleo WTI vs Nasdaq 100"),
        "XLE_SMH": ("XLE", "SMH", "Energía vs Semiconductores"),
        "TNX_QQQ": ("^TNX", "QQQ", "Tasa 10Y vs Nasdaq 100"),
        "SPX_TLT": ("^GSPC", "TLT", "S&P 500 vs Bonos largos (TLT)"),
    }
    rolling = {}
    for k, (a, b, label) in corr_pairs.items():
        rolling[k] = {"label": label, "a": a, "b": b,
                      "c20": rolling_corr(a, b, 20), "c60": rolling_corr(a, b, 60),
                      "c120": rolling_corr(a, b, 120)}

    mat_syms = ["^GSPC", "QQQ", "SMH", "XLK", "XLE", "CL=F", "^TNX", "TLT", "HYG", "GC=F",
                "DX-Y.NYB", "BTC-USD", "XLU", "XLRE"]
    mat_syms = [s for s in mat_syms if s in rets]
    matrices = {}
    for label, w in (("60d", 60), ("1y", 252)):
        c = rets[mat_syms].iloc[-w:].corr()
        matrices[label] = [[clean(c.loc[a, b], 2) for b in mat_syms] for a in mat_syms]

    # ---- FRED (crédito, curva, tasas reales). Publica 1 vez al día: se cachea 1 hora.
    fred = get_fred(cfg, df.index[0].strftime("%Y-%m-%d"))
    fred_ok = "BAMLH0A0HYM2" in fred

    # Proxy de spread HY cuando FRED no está disponible: HYG vs IEF (retorno relativo)
    hy_proxy = None
    if "HYG" in df and "IEF" in df:
        ratio = (df["HYG"] / df["IEF"])
        hy_proxy = series_list(ratio / ratio.dropna().iloc[0] * 100, 2)

    # ---- señales de mercado
    signals = build_signals(df, quotes, fred, rets)

    # ---- portafolio
    portfolio = build_portfolio(holdings, df, quotes, rets, prof)
    others = []
    for e in extras:
        try:
            others.append({"name": e["name"], "source": e["source"], "note": e["note"],
                           **build_portfolio(e["holdings"], df, quotes, rets, prof)})
        except Exception as ex:
            log(f"cartera '{e['name']}' no disponible: {ex}")
    if others:
        log("Carteras comparadas: " + ", ".join(f"{o['name']} ({len(o['rows'])} posiciones)" for o in others))
    try:
        portfolio["proj"] = build_projection(portfolio, df, rets)
    except Exception as e:
        log(f"proyección no disponible: {e}")
        portfolio["proj"] = None

    # ---- simulador: activos disponibles (posiciones de todas las carteras + ETFs amplios, sectoriales y de bonos)
    try:
        sim = build_sim_candidates([portfolio] + others, df, quotes)
    except Exception as e:
        log(f"simulador no disponible: {e}")
        sim = None

    # ---- análisis por acción (resultados, comparables, valor justo) de todas las carteras
    eq_syms = list(dict.fromkeys(r["sym"] for pf in [portfolio] + others for r in pf["rows"]))
    try:
        research = build_research(eq_syms, df, quotes)
    except Exception as e:
        log(f"análisis por acción no disponible: {e}")
        research = None

    # ---- series para gráficos (2 años, alineadas)
    series = {s: series_list(df[s], 4) for s in df.columns}
    dates = [d.strftime("%Y-%m-%d") for d in df.index]
    if live:
        # se agrega el día vivo a todas las series alineadas a `dates`: los activos que ya
        # cotizan usan el precio de hoy y el resto repite el último cierre (igual que ffill)
        dates.append(live_day.strftime("%Y-%m-%d"))
        series = {s: v + [clean(live[s]) if s in live else v[-1]] for s, v in series.items()}
        for r in rolling.values():
            for k in ("c20", "c60", "c120"):
                if r[k]:
                    r[k].append(r[k][-1])
        if hy_proxy:
            hy_proxy.append(hy_proxy[-1])
        for pf in [portfolio] + others:
            pf["value"].append(pf["value"][-1])

    return {
        "generated": datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S"),
        "generatedLocal": datetime.now().strftime("%d-%m-%Y %H:%M:%S"),
        "epoch": int(time.time()),
        "marketStatus": market_status(),
        "refreshSeconds": int(cfg.get("refresh_seconds", 120)),
        "fredOk": fred_ok,
        "dates": dates,
        "series": series,
        "quotes": quotes,
        "intraday": intraday,
        "rolling": rolling,
        "matrix": {"labels": mat_syms, "names": [names.get(s, s) for s in mat_syms], **matrices},
        "fred": fred,
        "hyProxy": hy_proxy,
        "signals": signals,
        "portfolio": portfolio,
        "others": others,
        "sectors": SECTOR_ETFS,
        "research": research,
        "sim": sim,
        "news": build_news(list(dict.fromkeys(h["sym"] for h in every)), names),
    }


# ---------------------------------------------------------------- noticias
NEWS_CACHE = os.path.join(BASE, "news.json")
NEWS_TTL = 10 * 60            # en el Mac el motor corre cada pocos minutos: no repetir consultas
NEWS_PER_SYM = 6
NEWS_DAYS = 7
# mercado general en español (Google Noticias); cada consulta aporta unos pocos titulares recientes
NEWS_ES = [("Wall Street bolsa", "Bolsa"), ("Reserva Federal tasas", "Fed"), ("inflación Estados Unidos", "Economía"),
           ("precio petróleo", "Petróleo"), ("dólar peso chileno", "Dólar")]


def _yahoo_news(sym, crumb, name=""):
    r = session.get("https://query2.finance.yahoo.com/v1/finance/search",
                    params={"q": sym, "newsCount": 12, "quotesCount": 0, "crumb": crumb}, timeout=10)
    r.raise_for_status()
    cut = time.time() - NEWS_DAYS * 86400
    out = []
    for n in r.json().get("news", []):
        rel = [t.upper() for t in n.get("relatedTickers") or []]
        if sym not in rel or not n.get("link") or (n.get("providerPublishTime") or 0) < cut:
            continue
        # "principal" = el titular nombra la empresa o el símbolo (no es una nota general que solo la menciona)
        title = n.get("title", "").strip()
        main = bool(re.search(r"\b" + re.escape(sym.split("-")[0]) + r"\b", title)) or bool(name and name.lower() in title.lower())
        out.append({"id": n.get("uuid") or n["link"], "t": n["providerPublishTime"], "title": title,
                    "src": n.get("publisher") or "", "url": n["link"], "main": main})
        if len(out) >= NEWS_PER_SYM:
            break
    return out


def _google_news(q, tag):
    import xml.etree.ElementTree as ET
    from email.utils import parsedate_to_datetime
    r = requests.get("https://news.google.com/rss/search", headers=UA, timeout=10,
                     params={"q": q + " when:2d", "hl": "es-419", "gl": "CL", "ceid": "CL:es-419"})
    r.raise_for_status()
    out = []
    for it in ET.fromstring(r.content).iter("item"):
        src, title = it.findtext("source") or "", (it.findtext("title") or "").strip()
        if src and title.endswith(" - " + src):
            title = title[: -len(src) - 3]
        try:
            t = int(parsedate_to_datetime(it.findtext("pubDate")).timestamp())
        except Exception:
            continue
        out.append({"id": it.findtext("link"), "t": t, "title": title, "src": src, "url": it.findtext("link"), "tag": tag})
        if len(out) >= 6:
            break
    return out


def build_news(syms, names=None):
    """Titulares por acción (Yahoo, en inglés) + mercado general (Google Noticias, en español)."""
    cache = _load_json(NEWS_CACHE, {})
    if cache.get("t") and time.time() - cache["t"] < NEWS_TTL and set(cache.get("syms", [])) == set(syms):
        return cache["data"]
    stocks, market = {}, []
    try:
        crumb = yahoo_crumb()
        with ThreadPoolExecutor(max_workers=6) as ex:
            for s, items in zip(syms, ex.map(lambda s: _safe(_yahoo_news, s, crumb, _short_name((names or {}).get(s, ""))), syms)):
                for n in items or []:
                    cur = stocks.setdefault(n["id"], {**n, "syms": [], "main": False})
                    cur["syms"].append(s)
                    cur["main"] = cur["main"] or n["main"]
            seen = set()
            for items in ex.map(lambda a: _safe(_google_news, *a), NEWS_ES):
                for n in items or []:
                    k = n["title"].lower()[:60]
                    if k not in seen:
                        seen.add(k)
                        market.append(n)
    except Exception as e:
        log(f"Noticias no disponibles ({e}); se usa la última copia")
        return cache.get("data", {"stocks": [], "market": []})
    data = {"stocks": sorted(stocks.values(), key=lambda n: -n["t"])[:120],
            "market": sorted(market, key=lambda n: -n["t"])[:30]}
    if not data["stocks"] and not data["market"] and cache.get("data"):
        return cache["data"]
    try:
        with open(NEWS_CACHE, "w", encoding="utf-8") as f:
            json.dump({"t": time.time(), "syms": syms, "data": data}, f, ensure_ascii=False)
    except Exception:
        pass
    log(f"Noticias: {len(data['stocks'])} de acciones, {len(data['market'])} de mercado")
    return data


def _short_name(n):
    """Palabra distintiva del nombre de la empresa ("Alphabet Inc." -> "Alphabet") para reconocerla en un titular."""
    skip = {"the", "inc", "corp", "corporation", "co", "company", "group", "holdings", "vanguard", "ishares", "spdr"}
    for w in re.findall(r"[A-Za-z][A-Za-z&'.-]+", n or ""):
        if w.lower().strip(".") not in skip and len(w) >= 3:
            return w.strip(".")
    return ""


def _safe(fn, *a):
    try:
        return fn(*a)
    except Exception:
        return []


def build_signals(df, q, fred, rets):
    sig = []

    def add(name, value, state, text, group):
        sig.append({"name": name, "value": value, "state": state, "text": text, "group": group})

    def last(sid):
        return fred[sid]["values"][-1] if sid in fred else None

    vix = q.get("^VIX", {}).get("price")
    if vix is not None:
        st = "good" if vix < 16 else "warning" if vix < 22 else "serious" if vix < 30 else "critical"
        add("VIX", f"{vix:.1f}", st,
            "Volatilidad implícita baja: mercado tranquilo, coberturas baratas." if vix < 16 else
            "Volatilidad moderada: atención a catalizadores." if vix < 22 else
            "Miedo elevado: mayor dispersión y caídas bruscas posibles." if vix < 30 else
            "Pánico: históricamente zona de capitulación y oportunidades de largo plazo.", "Riesgo")
    v3 = q.get("^VIX3M", {}).get("price")
    if vix and v3:
        ratio = vix / v3
        add("Estructura VIX (VIX/VIX3M)", f"{ratio:.2f}",
            "good" if ratio < 0.9 else "warning" if ratio < 1 else "critical",
            "Contango: el mercado no anticipa estrés inmediato." if ratio < 1 else
            "Backwardation: estrés de corto plazo, señal de cobertura/defensa.", "Riesgo")
    move = q.get("^MOVE", {}).get("price")
    if move:
        add("MOVE (vol. bonos)", f"{move:.0f}",
            "good" if move < 90 else "warning" if move < 120 else "critical",
            "Volatilidad de tasas contenida." if move < 90 else
            "Volatilidad de tasas elevada: presión sobre valuaciones de crecimiento." if move < 120 else
            "Estrés en el mercado de bonos.", "Riesgo")

    hy = last("BAMLH0A0HYM2")
    if hy is not None:
        vals = fred["BAMLH0A0HYM2"]["values"]
        ch = (vals[-1] - vals[-22]) * 100 if len(vals) > 22 else 0
        st = "warning" if hy < 3.0 else "good" if hy < 4.5 else "serious" if hy < 6 else "critical"
        if ch > 50:
            st = "serious"
        add("Spread High Yield (OAS)", f"{hy:.2f}% ({ch:+.0f} pb 1M)", st,
            ("Spreads muy comprimidos: poca compensación por riesgo, complacencia." if hy < 3.0 else
             "Crédito sano: sin señales de estrés." if hy < 4.5 else
             "Crédito tensionándose: suele anticipar debilidad en acciones." if hy < 6 else
             "Estrés crediticio: régimen de riesgo alto / recesivo.")
            + (" Ensanchamiento rápido en el último mes." if ch > 50 else ""), "Crédito")
    elif "HYG" in df and "IEF" in df:
        r = (df["HYG"] / df["IEF"]).dropna()
        ch = r.iloc[-1] / r.iloc[-22] - 1
        add("Crédito (proxy HYG/IEF, 1M)", f"{ch*100:+.2f}%",
            "good" if ch > 0.005 else "warning" if ch > -0.01 else "critical",
            "Proxy del spread HY: si cae, el high yield rinde peor que el Tesoro (spreads se amplían).",
            "Crédito")

    t10, t3m = q.get("^TNX", {}).get("price"), q.get("^IRX", {}).get("price")
    if t10 is not None and t3m is not None:
        sp = (t10 - t3m) * 100
        add("Curva 10Y - 3M", f"{sp:+.0f} pb",
            "critical" if sp < 0 else "warning" if sp < 50 else "good",
            "Curva invertida: señal recesiva clásica (con rezago de 6-18 meses)." if sp < 0 else
            "Curva plana: crecimiento esperado moderado." if sp < 50 else
            "Curva positiva: el mercado de bonos no descuenta recesión.", "Tasas")
    if "T10Y2Y" in fred:
        sp = last("T10Y2Y") * 100
        add("Curva 10Y - 2Y", f"{sp:+.0f} pb", "critical" if sp < 0 else "warning" if sp < 40 else "good",
            "Pendiente entre la tasa política esperada y el largo plazo.", "Tasas")
    if "DFII10" in fred:
        rr = last("DFII10")
        add("Tasa real 10Y", f"{rr:.2f}%", "good" if rr < 1.5 else "warning" if rr < 2.2 else "serious",
            "Tasas reales altas presionan múltiplos de tecnología y el oro.", "Tasas")
    tnx = df["^TNX"].dropna() if "^TNX" in df else None
    if tnx is not None and len(tnx) > 22:
        ch = (tnx.iloc[-1] - tnx.iloc[-22]) * 100
        add("Tesoro 10Y (cambio 1M)", f"{ch:+.0f} pb",
            "good" if ch < 15 else "warning" if ch < 35 else "serious",
            "Alzas rápidas del 10Y suelen golpear acciones de larga duración (tech, REITs, utilities).",
            "Tasas")

    spx = df["^GSPC"].dropna()
    ma50, ma200 = spx.iloc[-50:].mean(), spx.iloc[-200:].mean()
    p = spx.iloc[-1]
    add("S&P 500 vs medias móviles", f"{(p/ma200-1)*100:+.1f}% vs MM200",
        "good" if p > ma50 and p > ma200 else "warning" if p > ma200 else "critical",
        "Tendencia alcista: sobre MM50 y MM200." if p > ma50 and p > ma200 else
        "Corrección dentro de tendencia alcista (bajo MM50, sobre MM200)." if p > ma200 else
        "Tendencia bajista: bajo la MM200.", "Tendencia")
    dd = p / spx.iloc[-252:].max() - 1
    add("S&P 500 desde máximo 52s", f"{dd*100:.1f}%",
        "good" if dd > -0.05 else "warning" if dd > -0.10 else "serious" if dd > -0.20 else "critical",
        "Cerca de máximos." if dd > -0.05 else "Retroceso normal." if dd > -0.10 else
        "Corrección." if dd > -0.20 else "Mercado bajista.", "Tendencia")

    above = [s for s in SECTOR_ETFS if s in df and df[s].iloc[-1] > df[s].iloc[-50:].mean()]
    tot = len([s for s in SECTOR_ETFS if s in df])
    if tot:
        frac = len(above) / tot
        add("Amplitud sectorial", f"{len(above)}/{tot} sobre MM50",
            "good" if frac >= .6 else "warning" if frac >= .35 else "critical",
            "Subida amplia y saludable." if frac >= .6 else
            "Participación mixta: el mercado depende de pocos sectores." if frac >= .35 else
            "Debilidad generalizada.", "Tendencia")
    if "RSP" in df and "SPY" in df:
        rel = (df["RSP"] / df["SPY"]).dropna()
        ch = rel.iloc[-1] / rel.iloc[-63] - 1
        add("Igual ponderado vs S&P (3M)", f"{ch*100:+.1f}%", "good" if ch > 0 else "warning" if ch > -.04 else "serious",
            "La acción promedio acompaña al índice." if ch > 0 else
            "Concentración: el índice sube por las megacaps; amplitud débil.", "Tendencia")

    def ratio_sig(a, b, name, good_txt, bad_txt, group):
        if a in df and b in df:
            r = (df[a] / df[b]).dropna()
            ch = r.iloc[-1] / r.iloc[-21] - 1
            add(name, f"{ch*100:+.1f}% 1M", "good" if ch > 0.01 else "warning" if ch > -0.02 else "serious",
                good_txt if ch > 0 else bad_txt, group)

    ratio_sig("XLY", "XLP", "Apetito por riesgo (XLY/XLP)",
              "Consumo discrecional lidera: perfil pro-ciclo.", "Rotación defensiva hacia consumo básico.", "Ciclo")
    ratio_sig("HG=F", "GC=F", "Cobre / Oro",
              "Cobre supera al oro: expectativas de crecimiento al alza.",
              "Oro supera al cobre: búsqueda de refugio / menor crecimiento.", "Ciclo")
    ratio_sig("SMH", "SPY", "Semiconductores vs S&P",
              "Liderazgo de semis: ciclo de IA intacto.",
              "Semis rezagados: riesgo para carteras sobreexpuestas a IA.", "Ciclo")

    oil = df["CL=F"].dropna() if "CL=F" in df else None
    if oil is not None and len(oil) > 22:
        ch = oil.iloc[-1] / oil.iloc[-22] - 1
        add("Petróleo WTI (1M)", f"{ch*100:+.1f}%",
            "good" if abs(ch) < .08 else "warning" if abs(ch) < .15 else "serious",
            "Alza fuerte del crudo: presión inflacionaria y sobre márgenes; favorece energía." if ch > .08 else
            "Caída fuerte del crudo: señal de demanda débil o exceso de oferta." if ch < -.08 else
            "Crudo estable.", "Macro")
    dxy = df["DX-Y.NYB"].dropna() if "DX-Y.NYB" in df else None
    if dxy is not None and len(dxy) > 22:
        ch = dxy.iloc[-1] / dxy.iloc[-22] - 1
        add("Dólar DXY (1M)", f"{ch*100:+.1f}%", "good" if ch < .01 else "warning" if ch < .03 else "serious",
            "Dólar fuerte endurece condiciones financieras globales y afecta ventas externas de las megacaps."
            if ch > .01 else "Dólar estable o débil: viento a favor para activos de riesgo.", "Macro")

    c60 = rets["XLE"].rolling(60).corr(rets["XLK"]).dropna() if "XLE" in rets and "XLK" in rets else None
    if c60 is not None and len(c60):
        v, avg = c60.iloc[-1], c60.iloc[-252:].mean()
        add("Correlación Energía-Tecnología (60d)", f"{v:+.2f} (prom. 1A {avg:+.2f})",
            "good" if v < 0.3 else "warning" if v < 0.6 else "serious",
            "Correlación baja/negativa: la energía diversifica una cartera tecnológica." if v < 0.3 else
            "Correlación moderada: la diversificación sectorial pierde efecto." if v < 0.6 else
            "Alta correlación: mercado movido por un factor macro común (tasas/liquidez).", "Macro")
    return sig


def build_portfolio(holdings, df, q, rets, prof):
    rows, total_mv, total_cost, total_today = [], 0.0, 0.0, 0.0
    for h in holdings:
        s = h["sym"]
        qq = q.get(s)
        price = qq["price"] if qq else (h["xl_price"] or 0)
        prev = qq["prev"] if qq else price
        mv = h["shares"] * price
        cost = h["shares"] * h["avg"]
        today = h["shares"] * (price - prev)
        total_mv += mv
        total_cost += cost
        total_today += today
        name, sector = holding_info(s, prof)
        rows.append({"sym": s, "name": name, "sector": sector, "shares": h["shares"], "avg": h["avg"],
                     "price": clean(price), "chgPct": qq["chgPct"] if qq else None,
                     "mv": clean(mv, 2), "cost": clean(cost, 2), "pl": clean(mv - cost, 2),
                     "plPct": clean(mv / cost - 1, 5) if cost else None, "today": clean(today, 2),
                     "r1m": qq.get("r1m") if qq else None, "ytd": qq.get("ytd") if qq else None,
                     "rsi": qq.get("rsi") if qq else None,
                     "vsMa200": clean(price / qq["ma200"] - 1, 4) if qq and qq.get("ma200") else None,
                     "fromHigh": clean(price / qq["hi52"] - 1, 4) if qq and qq.get("hi52") else None})
    for r in rows:
        r["weight"] = clean(r["mv"] / total_mv, 5) if total_mv else 0

    syms = [r["sym"] for r in rows if r["sym"] in df]
    shares = {h["sym"]: h["shares"] for h in holdings}
    value = sum(df[s].ffill() * shares[s] for s in syms)
    pret = value.pct_change(fill_method=None)
    win = pret.iloc[-252:].dropna()
    spx = rets["^GSPC"].iloc[-252:]
    al = pd.concat([win, spx], axis=1).dropna()
    beta = al.iloc[:, 0].cov(al.iloc[:, 1]) / al.iloc[:, 1].var() if len(al) > 30 else None
    vol = win.std() * math.sqrt(252)
    spx_vol = spx.std() * math.sqrt(252)
    v1y = value.iloc[-252:]
    mdd = (v1y / v1y.cummax() - 1).min()
    var95 = -np.percentile(win, 5) if len(win) > 30 else None
    cvar95 = -win[win <= np.percentile(win, 5)].mean() if len(win) > 30 else None

    # sensibilidades por posición (1 año)
    R = rets[syms].iloc[-252:]
    w = np.array([next(r["weight"] for r in rows if r["sym"] == s) for s in syms])
    cov = R.cov().values
    port_var = float(w @ cov @ w)
    mrc = cov @ w
    for i, s in enumerate(syms):
        r = next(x for x in rows if x["sym"] == s)
        a = pd.concat([R[s], spx], axis=1).dropna()
        r["beta"] = clean(a.iloc[:, 0].cov(a.iloc[:, 1]) / a.iloc[:, 1].var(), 2)
        r["vol"] = clean(R[s].std() * math.sqrt(252), 3)
        r["riskContrib"] = clean(w[i] * mrc[i] / port_var, 4) if port_var else None
        for k, f in (("corrOil", "CL=F"), ("corrRates", "^TNX"), ("corrTech", "XLK"), ("corrEnergy", "XLE")):
            r[k] = clean(R[s].corr(rets[f].iloc[-252:]), 2) if f in rets else None

    # rendimiento de la cartera (simulada con las posiciones actuales) vs S&P 500 en varios plazos
    spx_px = df["^GSPC"]
    ytd_i = df.index[df.index.year == df.index[-1].year][0]
    ytd_i = df.index.get_loc(ytd_i) - 1
    perf = {}
    for k, n in (("r1m", 21), ("r3m", 63), ("r6m", 126), ("r1y", 252), ("r2y", len(df) - 1)):
        if len(value) > n:
            perf[k] = {"port": clean(value.iloc[-1] / value.iloc[-1 - n] - 1, 4),
                       "spx": clean(spx_px.iloc[-1] / spx_px.iloc[-1 - n] - 1, 4)}
    if ytd_i >= 0:
        perf["ytd"] = {"port": clean(value.iloc[-1] / value.iloc[ytd_i] - 1, 4),
                       "spx": clean(spx_px.iloc[-1] / spx_px.iloc[ytd_i] - 1, 4)}

    sectors = {}
    for r in rows:
        sectors[r["sector"]] = sectors.get(r["sector"], 0) + (r["weight"] or 0)

    # sensibilidad factorial de la cartera completa
    factors = {}
    for f, label in (("CL=F", "Petróleo"), ("^TNX", "Tasa 10Y"), ("XLE", "Energía"), ("XLK", "Tecnología"),
                     ("SMH", "Semiconductores"), ("DX-Y.NYB", "Dólar"), ("HYG", "High Yield")):
        if f in rets:
            a = pd.concat([win, rets[f].iloc[-252:]], axis=1).dropna()
            factors[label] = {"corr": clean(a.iloc[:, 0].corr(a.iloc[:, 1]), 2),
                              "beta": clean(a.iloc[:, 0].cov(a.iloc[:, 1]) / a.iloc[:, 1].var(), 3)}

    return {
        "rows": sorted(rows, key=lambda r: -(r["mv"] or 0)),
        "totals": {"mv": clean(total_mv, 2), "cost": clean(total_cost, 2), "pl": clean(total_mv - total_cost, 2),
                   "plPct": clean(total_mv / total_cost - 1, 5) if total_cost else None,
                   "today": clean(total_today, 2),
                   "todayPct": clean(total_today / (total_mv - total_today), 5) if total_mv else None},
        "stats": {"beta": clean(beta, 2), "vol": clean(vol, 4), "spxVol": clean(spx_vol, 4),
                  "maxDD": clean(mdd, 4), "var95": clean(var95, 4), "cvar95": clean(cvar95, 4),
                  "var95Usd": clean(var95 * total_mv, 2) if var95 else None,
                  "r1y": perf.get("r1y", {}).get("port"),  # 252 sesiones atrás, igual que el S&P (spx1y)
                  "spx1y": q["^GSPC"]["r1y"]},
        "value": series_list(value, 2),
        "sectors": sorted(([k, clean(v, 4)] for k, v in sectors.items()), key=lambda x: -x[1]),
        "factors": factors,
        "perf": perf,
    }


def build_sim_candidates(pfs, df, quotes):
    """Activos que el simulador puede usar, con precio, sector y retorno esperado de largo plazo."""
    cand = {}
    for pf in pfs:
        for r in pf["rows"]:
            cand.setdefault(r["sym"], {"name": r["name"], "sector": r["sector"]})
    for s, sec in SIM_ETFS.items():
        if s in df:
            cand.setdefault(s, {"name": quotes.get(s, {}).get("name", s), "sector": sec})
    cand = {s: c for s, c in cand.items() if s in df and quotes.get(s, {}).get("price")}
    fund = fundamentals(list(cand))
    for s, c in cand.items():
        e = expected_return(s, c["sector"], fund.get(s, {}))
        c.update(price=quotes[s]["price"], er=e["er"], erMethod=e["method"])
    return {"cand": cand}


# ---------------------------------------------------------------- análisis por acción
# Versión automática de tres flujos de análisis: resultados trimestrales (earnings),
# comparables con pares (market research) y valor justo con un modelo descontado (model builder).
RESEARCH_CACHE = os.path.join(BASE, "research.json")
RESEARCH_TTL = 12 * 3600
ERP = 0.05             # prima por riesgo de acciones sobre el Tesoro 10Y
TERM_G = 0.04          # crecimiento de las utilidades a perpetuidad (año 11 en adelante)
HOLD_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile,calendarEvents,earnings,earningsTrend"
PEER_MODULES = "price,summaryDetail,defaultKeyStatistics,financialData,assetProfile"
MAX_PEERS = 5


def _raw(v):
    return v.get("raw") if isinstance(v, dict) else v


def _parse_summary(x):
    pr, sd = x.get("price") or {}, x.get("summaryDetail") or {}
    ks, fd, ap = x.get("defaultKeyStatistics") or {}, x.get("financialData") or {}, x.get("assetProfile") or {}
    out = {
        "name": pr.get("shortName") or pr.get("longName"), "type": pr.get("quoteType"),
        "sector": ap.get("sector"), "industry": ap.get("industry"),
        "price": _raw(fd.get("currentPrice")) or _raw(pr.get("regularMarketPrice")),
        "mcap": _raw(sd.get("marketCap")) or _raw(pr.get("marketCap")),
        "pe": _raw(sd.get("trailingPE")), "fpe": _raw(sd.get("forwardPE")),
        "evEbitda": _raw(ks.get("enterpriseToEbitda")), "peg": _raw(ks.get("pegRatio")),
        "revG": _raw(fd.get("revenueGrowth")), "epsG": _raw(fd.get("earningsGrowth")),
        "gm": _raw(fd.get("grossMargins")), "om": _raw(fd.get("operatingMargins")),
        "pm": _raw(fd.get("profitMargins")), "roe": _raw(fd.get("returnOnEquity")),
        "beta": _raw(sd.get("beta")) or _raw(ks.get("beta")),
        "fwdEps": _raw(ks.get("forwardEps")), "ttmEps": _raw(ks.get("trailingEps")),
        "r1y": _raw(ks.get("52WeekChange")),
        "target": _raw(fd.get("targetMeanPrice")), "targetLo": _raw(fd.get("targetLowPrice")),
        "targetHi": _raw(fd.get("targetHighPrice")), "analysts": _raw(fd.get("numberOfAnalystOpinions")),
        "rec": fd.get("recommendationKey"),
    }
    # resultados: próxima fecha, consenso e historial de sorpresas (con fecha real de publicación)
    ce = (x.get("calendarEvents") or {}).get("earnings") or {}
    if ce:
        dates = [_raw(d) for d in ce.get("earningsDate") or [] if _raw(d)]
        out["next"] = {"date": datetime.fromtimestamp(min(dates), NY).strftime("%Y-%m-%d") if dates else None,
                       "estimate": bool(ce.get("isEarningsDateEstimate")),
                       "eps": _raw(ce.get("earningsAverage")), "epsLo": _raw(ce.get("earningsLow")),
                       "epsHi": _raw(ce.get("earningsHigh")), "rev": _raw(ce.get("revenueAverage")),
                       "cur": (x.get("earnings") or {}).get("financialCurrency") or fd.get("financialCurrency") or "USD"}
    q = ((x.get("earnings") or {}).get("earningsChart") or {}).get("quarterly") or []
    if q:
        out["hist"] = [{"q": e.get("calendarQuarter") or e.get("date"),
                        "rep": datetime.fromtimestamp(_raw(e["reportedDate"]), NY).strftime("%Y-%m-%d") if e.get("reportedDate") else None,
                        "act": _raw(e.get("actual")), "est": _raw(e.get("estimate"))} for e in q]
    tr = (x.get("earningsTrend") or {}).get("trend") or []
    if tr:
        out["growth"] = {t["period"]: _raw(t.get("growth")) for t in tr if t.get("period")}
    return out


def research_data(syms):
    """Datos de Yahoo para las acciones de las carteras y sus pares. Cache de 12 h por símbolo."""
    try:
        with open(RESEARCH_CACHE, encoding="utf-8") as f:
            cache = json.load(f)
    except Exception:
        cache = {}
    now = time.time()
    stale = lambda s, full: s not in cache or now - cache[s].get("t", 0) > RESEARCH_TTL or (full and not cache[s].get("full"))
    need = [s for s in syms if stale(s, True)]
    if need:
        try:
            crumb = yahoo_crumb()

            def one(args):
                s, full = args
                try:
                    r = session.get(f"https://query1.finance.yahoo.com/v10/finance/quoteSummary/{quote(s)}",
                                    params={"modules": HOLD_MODULES if full else PEER_MODULES, "crumb": crumb}, timeout=20)
                    d = _parse_summary(r.json()["quoteSummary"]["result"][0])
                    if full:
                        p = session.get(f"https://query1.finance.yahoo.com/v6/finance/recommendationsbysymbol/{quote(s)}",
                                        timeout=20).json()["finance"]["result"][0]["recommendedSymbols"]
                        d["peers"] = [x["symbol"] for x in p][:MAX_PEERS]
                    return s, {"t": now, "full": full, "d": d}
                except Exception:
                    return s, None

            with ThreadPoolExecutor(max_workers=6) as ex:
                for s, v in ex.map(one, [(s, True) for s in need]):
                    if v:
                        cache[s] = v
                peers = {p for s in syms if s in cache for p in cache[s]["d"].get("peers", [])}
                for s, v in ex.map(one, [(p, False) for p in peers if stale(p, False)]):
                    if v:
                        cache[s] = v
            with open(RESEARCH_CACHE, "w", encoding="utf-8") as f:
                json.dump(cache, f, ensure_ascii=False)
            got = sum(1 for s in need if s in cache and cache[s]["t"] == now)
            log(f"Análisis: {got}/{len(need)} acciones actualizadas (+ pares)")
        except Exception as e:
            log(f"Análisis no disponible ({e}); se usa la última copia")
    return {s: v["d"] for s, v in cache.items()}


def _median(vals):
    v = sorted(x for x in vals if x is not None)
    if not v:
        return None
    n = len(v)
    return v[n // 2] if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2


def fair_value(d, rf):
    """Modelo de utilidades descontadas en 2 etapas (estilo DCF).
    Flujo al accionista = utilidad × (1 − g/ROE): para crecer g, la empresa reinvierte g/ROE de lo que gana.
    Año 1 = UPA forward; años 2-5 crece al consenso del próximo año (recortado), años 6-10 converge
    a TERM_G; valor terminal con TERM_G."""
    if d.get("sector") == "Real Estate":
        return {"na": "REIT: se valora por su flujo operativo (FFO), no por utilidades contables"}
    eps0 = d.get("fwdEps") if (d.get("fwdEps") or 0) > 0 else d.get("ttmEps")
    if not eps0 or eps0 <= 0:
        return {"na": "Utilidades negativas: el modelo no aplica"}
    # la UPA forward ya incluye el crecimiento hacia el próximo año fiscal (Yahoo "0y"/"+1y");
    # para los años 2-5 se extrapola el crecimiento esperado del próximo año ("+1y"), sin contarlo dos veces
    gr = d.get("growth") or {}
    g_src = next((v for v in (gr.get("+1y"), gr.get("0y"), d.get("epsG")) if v is not None), LT_GROWTH)
    g1 = min(max(g_src, -0.05), 0.30)
    g1 = TERM_G + (g1 - TERM_G) * HAIRCUT
    beta = min(max(d.get("beta") or 1.0, 0.6), 2.0)
    r0 = min(max(rf + beta * ERP, 0.07), 0.14)
    roe = min(max(d.get("roe") or 0.15, 0.08), 0.40)
    roe_t = max(min(roe, 0.20), r0)  # a perpetuidad la rentabilidad converge hacia el costo de capital

    def value(r):
        eps, pv, g = eps0, 0.0, g1
        for t in range(1, 11):
            g = g1 if t <= 5 else g1 + (TERM_G - g1) * (t - 5) / 5
            if t > 1:
                eps *= 1 + g
            pv += eps * max(0.0, 1 - g / roe) / (1 + r) ** t
        tv = eps * (1 + TERM_G) * (1 - TERM_G / roe_t) / (r - TERM_G)
        return pv + tv / (1 + r) ** 10

    return {"fv": clean(value(r0), 2), "fvLo": clean(value(r0 + 0.01), 2), "fvHi": clean(value(r0 - 0.01), 2),
            "eps0": clean(eps0, 2), "g1": clean(g1, 4), "gSrc": clean(g_src, 4), "r": clean(r0, 4),
            "beta": clean(beta, 2), "roe": clean(roe, 3)}


def build_research(syms, df, quotes):
    """Resultados, comparables y valor justo para las acciones (no ETFs) de las carteras."""
    data = research_data(syms)
    rf = (quotes.get("^TNX", {}).get("price") or 4.5) / 100
    today = datetime.now(NY).strftime("%Y-%m-%d")
    keys = ("fpe", "pe", "evEbitda", "revG", "om", "roe", "r1y")
    items = {}
    for s in syms:
        d = data.get(s)
        if not d or d.get("type") not in (None, "EQUITY"):
            continue
        price = quotes.get(s, {}).get("price") or d.get("price")
        it = {"name": d.get("name"), "sector": d.get("sector"), "industry": d.get("industry"), "price": price,
              "target": d.get("target"), "targetLo": d.get("targetLo"), "targetHi": d.get("targetHi"),
              "analysts": d.get("analysts"), "rec": d.get("rec"), "mcap": d.get("mcap"),
              **{k: clean(d.get(k), 4) for k in keys}}
        # --- resultados: sorpresas y reacción de la acción (2 días alrededor de la publicación)
        px = df[s].dropna() if s in df else None
        hist = []
        for h in d.get("hist") or []:
            e = {"q": h["q"], "rep": h["rep"], "act": h["act"], "est": h["est"],
                 "surp": clean(h["act"] / h["est"] - 1, 4) if h.get("act") is not None and h.get("est") else None}
            if px is not None and h.get("rep"):
                i = px.index.searchsorted(pd.Timestamp(h["rep"]))
                if 1 <= i < len(px) - 1:
                    e["react"] = clean(px.iloc[i + 1] / px.iloc[i - 1] - 1, 4)
            hist.append(e)
        it["hist"] = hist
        nx = dict(d.get("next") or {})
        if nx.get("date") and nx["date"] < today:  # Yahoo a veces deja el último reporte como "próximo"
            nx = {"date": None, "last": nx["date"]}
        it["next"] = nx or None
        moves = [abs(h["react"]) for h in hist if h.get("react") is not None]
        it["avgMove"] = clean(sum(moves) / len(moves), 4) if moves else None
        # --- comparables
        peers = [p for p in d.get("peers", []) if p in data and data[p].get("type") in (None, "EQUITY")]
        same = [p for p in peers if data[p].get("sector") and data[p].get("sector") == d.get("sector")]
        if len(same) >= 2:  # Yahoo sugiere "acciones que siguen los mismos inversionistas": se prefiere el mismo sector
            peers = same
        it["peers"] = [{"sym": p, "name": data[p].get("name"), **{k: clean(data[p].get(k), 4) for k in keys}}
                       for p in peers]
        it["peerMed"] = {k: clean(_median([data[p].get(k) for p in peers]), 4) for k in keys}
        # --- valor justo
        fv = fair_value(d, rf)
        if "fv" in fv and price:
            fv["up"] = clean(fv["fv"] / price - 1, 4)
        it["model"] = fv
        if it["target"] and price:
            it["targetUp"] = clean(it["target"] / price - 1, 4)
        items[s] = it
    return {"items": items, "assump": {"rf": clean(rf, 4), "erp": ERP, "termG": TERM_G, "haircut": HAIRCUT}}


def write(data):
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write("window.PANEL_DATA = ")
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"), allow_nan=False)
        f.write(";\n")
    os.replace(tmp, OUT)


def run_once(cfg):
    t0 = time.time()
    data = build(cfg)
    write(data)
    log(f"data.js actualizado ({len(data['quotes'])} instrumentos, {time.time()-t0:.1f}s) "
        f"- mercado: {data['marketStatus']}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="segundos entre actualizaciones (0 = una vez)")
    args = ap.parse_args()
    cfg = load_config()
    if args.loop:
        cfg["refresh_seconds"] = args.loop
    while True:
        try:
            run_once(cfg)
        except Exception as e:
            log(f"ERROR: {e}")
            if not args.loop:
                sys.exit(1)
        if not args.loop:
            break
        time.sleep(args.loop)


if __name__ == "__main__":
    main()
