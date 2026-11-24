/**
 * Panel de Mercado · actualización automática desde los certificados de Fintual.
 *
 * Corre en TU cuenta de Google (Apps Script). Cada 5 minutos revisa tu Gmail buscando los
 * certificados de Fintual Acciones (PDF), los lee y guarda tus posiciones dentro de tu cuenta.
 * El panel en GitHub las pide a este script con una clave secreta en cada actualización.
 *
 * Lo que sale de tu cuenta de Google: solo símbolo, nombre de la empresa, número de acciones y
 * precio promedio de compra. Tu nombre, RUT y el resto del PDF nunca salen de Google.
 *
 * Además hace de "despertador" del panel: le pide a GitHub que se actualice cada 15 minutos en
 * horario de mercado y cada 2 horas fuera de él (las tareas programadas de GitHub no son confiables),
 * y de inmediato cuando llega un certificado nuevo. Requiere un token de GitHub con permiso
 * "Actions: write" guardado en Propiedades del script como GITHUB_TOKEN (opcional).
 *
 * Instalación: ver GUIA_FINTUAL.md (pegar este archivo, activar "Drive API", ejecutar configurar()
 * e implementar como aplicación web).
 */

const ETIQUETA = 'panel-fintual';   // se pone a los correos ya procesados
const BUSQUEDA = 'from:fintual has:attachment filename:pdf newer_than:30d -label:' + ETIQUETA;
const AVISAR_POR_CORREO = true;     // te manda un resumen a ti mismo cada vez que actualiza
const REPO = 'juaneduardo1233/panel-mercado';
const WORKFLOW = 'panel.yml';
const CADA_MERCADO = 15;            // minutos entre actualizaciones con el mercado de EE.UU. abierto
const CADA_FUERA = 120;             // minutos entre actualizaciones el resto del tiempo

/* ===================================================================== instalación */

/** Ejecútalo UNA vez (y de nuevo si cambias el script): crea la clave y programa la revisión cada 5 minutos. */
function configurar() {
  const props = PropertiesService.getScriptProperties();
  if (!props.getProperty('CLAVE')) props.setProperty('CLAVE', Utilities.getUuid().replace(/-/g, '') + Utilities.getUuid().replace(/-/g, ''));
  ScriptApp.getProjectTriggers().forEach(t => { if (t.getHandlerFunction() === 'revisarCorreo') ScriptApp.deleteTrigger(t); });
  ScriptApp.newTrigger('revisarCorreo').timeBased().everyMinutes(5).create();
  GmailApp.getUserLabelByName(ETIQUETA) || GmailApp.createLabel(ETIQUETA);
  Logger.log('Listo. Revisión programada cada 5 minutos.');
  Logger.log('Copia esta clave en el secreto FINTUAL_KEY de GitHub:\n' + props.getProperty('CLAVE'));
  Logger.log(props.getProperty('GITHUB_TOKEN') ? 'Despertador del panel: activo.' :
    'Despertador del panel: inactivo (agrega GITHUB_TOKEN en Configuración del proyecto › Propiedades del script).');
  revisarCorreo();
}

/** Muestra la clave de nuevo si la necesitas. */
function verClave() {
  Logger.log(PropertiesService.getScriptProperties().getProperty('CLAVE') || 'Aún no existe: ejecuta configurar()');
}

/* ===================================================================== servicio para GitHub */

/** GitHub pide las posiciones con POST key=<clave>. Sin la clave correcta no entrega nada. */
function doPost(e) {
  const props = PropertiesService.getScriptProperties();
  const clave = props.getProperty('CLAVE');
  const ok = clave && e && e.parameter && e.parameter.key === clave;
  const body = ok ? (props.getProperty('POSICIONES') || '{"error":"sin posiciones todavía"}') : '{"error":"no autorizado"}';
  return ContentService.createTextOutput(body).setMimeType(ContentService.MimeType.JSON);
}
function doGet() {
  return ContentService.createTextOutput('{"error":"no autorizado"}').setMimeType(ContentService.MimeType.JSON);
}

/* ===================================================================== revisión del correo */

function revisarCorreo() {
  const etiqueta = GmailApp.getUserLabelByName(ETIQUETA) || GmailApp.createLabel(ETIQUETA);
  const hilos = GmailApp.search(BUSQUEDA, 0, 10);
  // del más antiguo al más nuevo, para que el último certificado sea el que queda
  const mensajes = [];
  let nuevas = false;
  hilos.forEach(h => h.getMessages().forEach(m => mensajes.push(m)));
  mensajes.sort((a, b) => a.getDate() - b.getDate());
  mensajes.forEach(m => {
    m.getAttachments().filter(a => /pdf/i.test(a.getContentType()) || /\.pdf$/i.test(a.getName())).forEach(pdf => {
      try { nuevas = procesar(pdfATexto(pdf), m.getDate()) || nuevas; }
      catch (err) { avisar('No pude leer un certificado de Fintual', 'Error: ' + err.message + '\nEl panel sigue con las posiciones anteriores.'); }
    });
  });
  hilos.forEach(h => h.addLabel(etiqueta));
  despertarPanel(nuevas);
}

/** Convierte el PDF a texto con Google Drive (requiere el servicio avanzado "Drive API"). */
function pdfATexto(blob) {
  const doc = Drive.Files.create({ name: 'tmp-certificado-fintual', mimeType: 'application/vnd.google-apps.document' }, blob);
  try { return DocumentApp.openById(doc.id).getBody().getText(); }
  finally { Drive.Files.remove(doc.id); }
}

function procesar(texto, fecha) {
  const asof = Utilities.formatDate(fecha, 'America/Santiago', 'yyyy-MM-dd');
  const props = PropertiesService.getScriptProperties();
  const previo = JSON.parse(props.getProperty('POSICIONES') || '{"positions":[]}');

  if (/CERTIFICADO DE TRANSACCIONES/i.test(texto)) {
    const ev = leerTransacciones(texto);
    const n = c => ev.filter(e => e.t === c).length;
    if (!n('buy')) throw new Error('el certificado de transacciones no tiene compras legibles');
    const pos = posicionesActuales(ev);
    guardar({ asof, source: 'transacciones', positions: pos, cash: previo.cash });
    avisar('Panel actualizado desde Fintual',
      `Leí ${n('buy')} compras, ${n('sell')} ventas, ${n('div')} dividendos y ${n('split')} splits.\n` +
      `Posiciones actuales: ${pos.length} · costo total US$ ${pos.reduce((a, p) => a + p.cost, 0).toFixed(2)}.\n` +
      (PropertiesService.getScriptProperties().getProperty('GITHUB_TOKEN') ? 'El panel se está actualizando ahora (tarda 2–3 minutos).' : 'El panel las usará en su próxima actualización.'));
    return true;
  } else if (/CERTIFICADO DE INVERSI[ÓO]N/i.test(texto)) {
    const { filas, caja } = leerBalance(texto);
    if (!filas.length) throw new Error('el certificado de inversión no tiene filas legibles');
    const porNombre = {};
    previo.positions.forEach(p => { porNombre[clave(p.name)] = p; });
    const nuevas = [], pos = [];
    filas.forEach(f => {
      const p = porNombre[clave(f.name)];
      if (p) pos.push({ sym: p.sym, name: p.name, units: f.units, avg: p.avg, cost: +(p.avg * f.units).toFixed(2) });
      else nuevas.push(f.name);
    });
    if (nuevas.length) {
      avisar('Fintual: falta el historial para actualizar el panel',
        'El certificado de inversión trae activos que el panel aún no conoce: ' + nuevas.join(', ') + '.\n' +
        'Para saber su símbolo y precio de compra, pide en Fintual el "Certificado de transacciones y eventos de capital en Acciones" (Enviar a mi mail). Llegará solo al panel.');
      return false;
    }
    guardar({ asof, source: 'balance', positions: pos, cash: caja });
    avisar('Panel actualizado desde Fintual',
      `Actualicé las cantidades de ${pos.length} posiciones (precios promedio sin cambio).` +
      (caja != null ? `\nEfectivo pendiente de invertir: US$ ${caja.toFixed(2)}.` : '') +
      '\nPara recalcular los precios promedio con tus últimas compras, pide el certificado de transacciones.');
    return true;
  }
  return false;
}

/* ===================================================================== despertador del panel */

/** ¿Toca actualizar? dia: 1=lunes…7=domingo y minutos desde medianoche, ambos en hora de Nueva York. */
function tocaDespertar(ahoraMs, ultimoMs, dia, minutos) {
  const mercado = dia <= 5 && minutos >= 9 * 60 + 15 && minutos <= 16 * 60 + 30;
  const cada = mercado ? CADA_MERCADO : CADA_FUERA;
  return ahoraMs - ultimoMs >= (cada - 2) * 60000;   // 2 min de margen: el reloj de Google no es exacto
}

/** Pide a GitHub que actualice el panel (si hay token). forzar = sin esperar el intervalo. */
function despertarPanel(forzar) {
  const props = PropertiesService.getScriptProperties();
  const token = props.getProperty('GITHUB_TOKEN');
  if (!token) return null;
  const ahora = new Date(), ny = f => Number(Utilities.formatDate(ahora, 'America/New_York', f));
  if (!forzar && !tocaDespertar(ahora.getTime(), Number(props.getProperty('ULTIMO_DESPERTAR') || 0), ny('u'), ny('H') * 60 + ny('m'))) return null;
  const r = UrlFetchApp.fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW + '/dispatches', {
    method: 'post', contentType: 'application/json', muteHttpExceptions: true, payload: JSON.stringify({ ref: 'main' }),
    headers: { Authorization: 'Bearer ' + token, Accept: 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28' },
  });
  const code = r.getResponseCode();
  if (code === 204) { props.setProperty('ULTIMO_DESPERTAR', String(ahora.getTime())); props.deleteProperty('ERROR_DESPERTAR'); }
  else if (props.getProperty('ERROR_DESPERTAR') !== String(code)) {   // avisa una sola vez por tipo de error
    props.setProperty('ERROR_DESPERTAR', String(code));
    avisar('GitHub no aceptó la actualización del panel',
      'Código ' + code + (code === 401 || code === 403 || code === 404 ? ': el token GITHUB_TOKEN no es válido, venció o no tiene el permiso "Actions: Read and write" sobre panel-mercado.' : '.') +
      '\nEl panel seguirá con las actualizaciones automáticas de GitHub (cada 2 horas).');
  }
  return code;
}

/** Prueba manual: pide una actualización ahora y muestra la respuesta de GitHub (204 = aceptada). */
function probarDespertador() {
  const code = despertarPanel(true);
  Logger.log(code === null ? 'Falta GITHUB_TOKEN en Propiedades del script.' : code === 204 ? 'OK: GitHub está actualizando el panel.' : 'GitHub respondió ' + code);
}

function guardar(data) {
  const json = JSON.stringify(data);
  if (json.length > 8500) throw new Error('demasiadas posiciones para guardar');
  PropertiesService.getScriptProperties().setProperty('POSICIONES', json);
}

function avisar(asunto, texto) {
  if (AVISAR_POR_CORREO) MailApp.sendEmail(Session.getActiveUser().getEmail(), '[Panel de Mercado] ' + asunto, texto);
}

const clave = s => String(s || '').toLowerCase().replace(/[^a-z0-9]/g, '');

/* ===================================================================== lector de certificados
   Misma lógica que fintual.py (probada contra los certificados reales). */

const NUM = '\\d[\\d.]*,\\d+';
const USD = 'US\\s?\\$\\s*(' + NUM + ')';
const DATE = '(\\d{4})-\\s*(\\d{2})-\\s*(\\d{2})';
const SYM = '([A-Z][A-Z0-9.\\-]{0,9})';
const CAT = '(Acciones|ETF|ETFs|Fondos?)';
const SECCIONES = ['Compra de activos', 'Venta de activos', 'Dividendos recibidos', 'Splits'];

const num = s => parseFloat(s.replace(/\./g, '').replace(',', '.'));
const normalizar = t => t.replace(/\s+/g, ' ');

function secciones(t) {
  const pos = SECCIONES.map(s => [t.indexOf(s), s]).filter(x => x[0] >= 0).sort((a, b) => a[0] - b[0]);
  const out = {};
  pos.forEach(([ini, nom], i) => { out[nom] = t.slice(ini + nom.length, i + 1 < pos.length ? pos[i + 1][0] : t.length); });
  return out;
}

function todos(re, texto, fn) { let m; const r = new RegExp(re, 'g'); while ((m = r.exec(texto))) fn(m); }

function leerTransacciones(texto) {
  const sec = secciones(normalizar(texto)), ev = [];
  const trade = DATE + ' (.+?) ' + SYM + ' ' + CAT + ' (\\S+) ' + USD + ' (' + NUM + ')';
  [['buy', 'Compra de activos'], ['sell', 'Venta de activos']].forEach(([t, s]) =>
    todos(trade, sec[s] || '', m => ev.push({ t, date: `${m[1]}-${m[2]}-${m[3]}`, name: m[4].trim(), sym: m[5], usd: num(m[8]), qty: num(m[9]) })));
  todos(DATE + ' (.+?) ' + SYM + ' ' + CAT + ' ' + USD + ' ' + USD + ' ' + USD, sec['Dividendos recibidos'] || '',
    m => ev.push({ t: 'div', date: `${m[1]}-${m[2]}-${m[3]}`, name: m[4].trim(), sym: m[5], net: num(m[9]) }));
  todos(DATE + ' (.+?) ' + SYM + ' ' + CAT + ' (' + NUM + ') (' + NUM + ') (' + NUM + ')', sec['Splits'] || '',
    m => ev.push({ t: 'split', date: `${m[1]}-${m[2]}-${m[3]}`, name: m[4].trim(), sym: m[5], ratio: num(m[9]) }));
  return ev;
}

/** Costo promedio: compras suman, ventas restan al costo promedio, splits multiplican unidades. */
function posicionesActuales(ev) {
  const orden = { split: 0, buy: 1, sell: 2, div: 3 }, st = {};
  ev.slice().sort((a, b) => a.date < b.date ? -1 : a.date > b.date ? 1 : orden[a.t] - orden[b.t]).forEach(e => {
    const p = st[e.sym] || (st[e.sym] = { sym: e.sym, name: e.name, units: 0, cost: 0 });
    if (e.t === 'buy') { p.units += e.qty; p.cost += e.usd; }
    else if (e.t === 'sell') { const avg = p.units ? p.cost / p.units : 0, q = Math.min(e.qty, p.units); p.cost -= avg * q; p.units -= q; }
    else if (e.t === 'split') p.units *= e.ratio;
  });
  return Object.values(st).filter(p => p.units > 1e-6).map(p => ({
    sym: p.sym.replace('.', '-'), name: p.name, units: +p.units.toFixed(9), avg: +(p.cost / p.units).toFixed(4), cost: +p.cost.toFixed(2),
  })).sort((a, b) => b.cost - a.cost);
}

function leerBalance(texto) {
  const t = normalizar(texto);
  const cuerpo = t.split('Balance (USD)').slice(-1)[0].split('Total')[0];
  const filas = [];
  todos('(.+?) (\\d+\\.\\d+) ' + USD, cuerpo, m => filas.push({ name: m[1].trim(), units: parseFloat(m[2]), usd: num(m[3]) }));
  const c = t.match(new RegExp('mantenía ' + USD + ' en su cuenta'));
  return { filas, caja: c ? num(c[1]) : null };
}

/** Prueba manual: procesa el último certificado de Fintual aunque ya esté etiquetado. */
function probarUltimoCertificado() {
  const h = GmailApp.search('from:fintual has:attachment filename:pdf', 0, 1)[0];
  if (!h) return Logger.log('No encontré correos de Fintual con PDF.');
  const m = h.getMessages().slice(-1)[0];
  const pdf = m.getAttachments().find(a => /pdf/i.test(a.getContentType()) || /\.pdf$/i.test(a.getName()));
  const texto = pdfATexto(pdf);
  if (/CERTIFICADO DE TRANSACCIONES/i.test(texto)) {
    const ev = leerTransacciones(texto);
    Logger.log('Transacciones: ' + JSON.stringify({ compras: ev.filter(e => e.t === 'buy').length, ventas: ev.filter(e => e.t === 'sell').length, dividendos: ev.filter(e => e.t === 'div').length, splits: ev.filter(e => e.t === 'split').length }));
    posicionesActuales(ev).forEach(p => Logger.log(`${p.sym}  ${p.units}  prom ${p.avg}`));
  } else {
    const b = leerBalance(texto);
    Logger.log('Balance: ' + b.filas.length + ' filas, caja ' + b.caja);
    b.filas.forEach(f => Logger.log(`${f.name}  ${f.units}`));
  }
}
