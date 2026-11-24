/* Simulador de cartera: cálculo compartido por panel.html y movil.html.
   Recibe montos en US$ por símbolo y calcula, con los precios históricos de data.js,
   las mismas métricas que el motor de Python usa para la cartera real (beta, volatilidad,
   VaR, máxima caída, rendimientos, contribución al riesgo, retorno esperado y sectores). */
(function () {
  const mean = a => a.reduce((x, y) => x + y, 0) / a.length;
  const cov = (a, b) => { const ma = mean(a), mb = mean(b); let s = 0; for (let i = 0; i < a.length; i++) s += (a[i] - ma) * (b[i] - mb); return s / (a.length - 1); };
  // percentil con interpolación lineal (igual que numpy.percentile)
  const pctl = (arr, p) => { const a = [...arr].sort((x, y) => x - y), k = (a.length - 1) * p / 100, f = Math.floor(k), c = Math.ceil(k);
    return f === c ? a[f] : a[f] + (a[c] - a[f]) * (k - f); };

  function compute(D, amounts) {
    const cand = (D.sim && D.sim.cand) || {};
    const syms = Object.keys(amounts).filter(s => amounts[s] > 0 && D.series[s]);
    const total = syms.reduce((a, s) => a + amounts[s], 0);
    if (!syms.length || !total) return null;
    const n = D.dates.length, spx = D.series['^GSPC'];
    // si el último día es la barra "viva" (antes de la apertura), el S&P repite su cierre: se excluye,
    // igual que en el motor de Python, para no contar un día sin movimiento
    const end = spx[n - 1] === spx[n - 2] ? n - 2 : n - 1;
    const price = s => (cand[s] && cand[s].price) || D.series[s][n - 1];
    const shares = Object.fromEntries(syms.map(s => [s, amounts[s] / price(s)]));
    const V = D.dates.map((_, t) => { let v = 0; for (const s of syms) { const p = D.series[s][t]; if (p == null) return null; v += shares[s] * p; } return v; });

    const W = 252, t0 = Math.max(1, end - W + 1);
    const ret = (arr, t) => arr[t] != null && arr[t - 1] != null && arr[t - 1] !== 0 ? arr[t] / arr[t - 1] - 1 : null;
    const idx = []; for (let t = t0; t <= end; t++) if (ret(V, t) != null && ret(spx, t) != null) idx.push(t);
    const pr = idx.map(t => ret(V, t)), sr = idx.map(t => ret(spx, t));
    const beta = pr.length > 30 ? cov(pr, sr) / cov(sr, sr) : null;
    const vol = Math.sqrt(cov(pr, pr) * 252), spxVol = Math.sqrt(cov(sr, sr) * 252);
    const p5 = pctl(pr, 5), var95 = -p5, tail = pr.filter(x => x <= p5), cvar95 = -mean(tail);
    let peak = -Infinity, mdd = 0;
    for (let t = Math.max(0, end - W + 1); t <= end; t++) { if (V[t] == null) continue; peak = Math.max(peak, V[t]); mdd = Math.min(mdd, V[t] / peak - 1); }
    const back = (arr, k) => end - k >= 0 && arr[end - k] ? arr[end] / arr[end - k] - 1 : null;
    const first = V.findIndex(v => v != null);
    const y = D.dates[end].slice(0, 4), yi = D.dates.findIndex(d => d.startsWith(y)) - 1;
    const perf = {
      r1y: back(V, 252), spx1y: back(spx, 252),
      r2y: first >= 0 ? V[end] / V[first] - 1 : null, spx2y: spx[end] / spx[first >= 0 ? first : 0] - 1,
      ytd: yi >= 0 && V[yi] ? V[end] / V[yi] - 1 : null, spxYtd: yi >= 0 ? spx[end] / spx[yi] - 1 : null,
    };

    // contribución al riesgo: w_i (Σw)_i / w'Σw con la matriz de covarianzas de 1 año
    const w = syms.map(s => amounts[s] / total);
    const R = syms.map(s => idx.map(t => ret(D.series[s], t) ?? 0));
    const C = syms.map((_, i) => syms.map((_, j) => cov(R[i], R[j])));
    const Cw = C.map(row => row.reduce((a, c, j) => a + c * w[j], 0));
    const pv = Cw.reduce((a, c, i) => a + c * w[i], 0);
    const rc = Object.fromEntries(syms.map((s, i) => [s, pv ? w[i] * Cw[i] / pv : null]));
    const top3 = Object.values(rc).sort((a, b) => b - a).slice(0, 3).reduce((a, b) => a + b, 0);

    // retorno esperado (mismo método que la proyección) y exposición por sector
    let er = 0, erCov = 0;
    syms.forEach((s, i) => { const e = cand[s] && cand[s].er; if (e != null) { er += w[i] * e; erCov += w[i]; } });
    const sectors = {};
    syms.forEach((s, i) => { const k = (cand[s] && cand[s].sector) || 'Otros'; sectors[k] = (sectors[k] || 0) + w[i]; });

    return { total, n: syms.length, w: Object.fromEntries(syms.map((s, i) => [s, w[i]])), rc, top3,
             beta, vol, spxVol, var95, cvar95, var95Usd: var95 * total, maxDD: mdd, ...perf,
             er: erCov ? er / erCov : null, erCoverage: erCov, sharpeLike: erCov && vol ? (er / erCov) / vol : null,
             sectors: Object.entries(sectors).sort((a, b) => b[1] - a[1]), V, end };
  }

  // reglas rápidas: todas mantienen el monto total
  const scale = (a, total) => { const s = Object.values(a).reduce((x, y) => x + y, 0); return Object.fromEntries(Object.entries(a).map(([k, v]) => [k, s ? v / s * total : 0])); };
  function equalize(a) { const ks = Object.keys(a).filter(k => a[k] > 0), t = ks.reduce((x, k) => x + a[k], 0); return Object.fromEntries(ks.map(k => [k, t / ks.length])); }
  function dropSmall(a, minW) { const t = Object.values(a).reduce((x, y) => x + y, 0); return scale(Object.fromEntries(Object.entries(a).filter(([, v]) => v / t >= minW)), t); }
  function capWeight(a, cap) {
    let b = { ...a }; const t = Object.values(b).reduce((x, y) => x + y, 0);
    for (let it = 0; it < 20; it++) {   // recorta al tope y reparte el exceso entre las que están bajo él
      const over = Object.keys(b).filter(k => b[k] > cap * t + 1e-9); if (!over.length) break;
      let ex = 0; over.forEach(k => { ex += b[k] - cap * t; b[k] = cap * t; });
      const under = Object.keys(b).filter(k => b[k] < cap * t - 1e-9), u = under.reduce((x, k) => x + b[k], 0);
      if (!u) break; under.forEach(k => { b[k] += ex * b[k] / u; });
    }
    return b;
  }
  window.SimCore = { compute, equalize, dropSmall, capWeight };
})();
