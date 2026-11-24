// Observador universal de páginas web (Playwright/Chromium), configurable por URL con un PERFIL (sin código por sitio).
// Lo lanza monitoreo/planificador/observador.py (ObservadorWeb) y habla el MISMO protocolo que observador_calendario.js:
//   INICIAL  días reconocidos al abrir: [{sucursal, fecha, estado, libres, horas}]
//   CELDA    un día cambió (sucursal, fecha, anterior, estado, libres, horas, ts_ms, evidencia {sha256, html, imagen})
//   VISTA    ACTIVA | SESION_CADUCADA | DESCONECTADO | BLOQUEADO_CONTROL (CAPTCHA, inicio de sesión, 401/403/429)
//   LATIDO   cada 2 s, con contadores (mutaciones ignoradas por ruido, elementos no reconocidos, cambios visuales)
//   CAMBIO_VISUAL  la captura de la zona (con el ruido tapado) cambió; dispara una relectura, no crea días
// Órdenes por stdin: {"cmd":"reabrir"} · {"cmd":"salir"}.  Con "una_vez": abre, lee, emite INICIAL y termina.
//
// Reglas que no se configuran: nunca envía formularios (toda navegación que no sea GET se aborta), no rellena campos,
// no resuelve CAPTCHA ni inicia sesión; ante un control de acceso se detiene. Un texto que el perfil no reconoce no
// se interpreta (el elemento se ignora y se cuenta). La página se mantiene ABIERTA: no se recarga para observar.
const emit = (o) => process.stdout.write("OBS " + JSON.stringify(o) + "\n");
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (e) {
  emit({ tipo: "ERROR", estrategia: "arranque", mensaje: "no se encontró el módulo playwright: " + String(e.message || e).split("\n")[0].slice(0, 200), ts_ms: Date.now() });
  process.exit(4);
}
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const P = cfg.perfil;
const estrategia = cfg.estrategia || "dom";
const usaVisual = estrategia !== "dom";
const intervaloVisual = Math.max(250, cfg.intervalo_visual_ms || 1000);
const estabilidad = Math.max(0, P.estabilidad_ms == null ? 800 : P.estabilidad_ms);
const evid = cfg.evidencia_dir || null;
if (evid) fs.mkdirSync(evid, { recursive: true });
const origen = new URL(cfg.url).origin;
const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
const CONTROLES = ["iframe[src*='recaptcha']", "iframe[src*='hcaptcha']", "iframe[src*='turnstile']", ".g-recaptcha",
  ".h-captcha", ".cf-turnstile", "[data-sitekey]", "input[type=password]"];

let browser, ctx, page, salir = false, abriendo = false, bloqueado = false;
let cola = Promise.resolve();
const conPagina = (fn) => { const r = cola.then(fn, fn); cola = r.catch(() => {}); return r; };
let confirmados = new Map();          // sucursal|fecha -> {sucursal, fecha, estado, libres, horas, firma, html}
let estadoVista = null;
const stats = { extracciones: 0, mutaciones_relevantes: 0, mutaciones_ruido: 0, no_reconocidos: 0, cambios_visuales: 0,
                navegaciones_bloqueadas: 0 };
let nEvid = 0;

function vista(estado, detalle) {
  if (estadoVista === estado) return;
  estadoVista = estado;
  emit({ tipo: "VISTA", estrategia: "dom", estado, ts_ms: Date.now(), detalle: (detalle || "").slice(0, 200) });
}

function bloquear(detalle) {
  if (bloqueado) return;
  bloqueado = true;
  vista("BLOQUEADO_CONTROL", detalle);
}

// ------------------------------------------------------------------ extracción (corre DENTRO de la página)
function extraerEnPagina(p) {
  const norm = (s) => (s == null ? "" : String(s)).replace(/\s+/g, " ").trim();
  const clave = (s) => norm(s).toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "");
  const ign = p.ignorar || [];
  const enRuido = (el) => ign.some((s) => el.closest && el.closest(s));
  const leer = (item, r) => {
    if (!r) return null;
    const el = r.selector ? item.querySelector(r.selector) : item;
    if (!el) return null;
    let v = r.atributo ? el.getAttribute(r.atributo) : el.textContent;
    v = norm(v);
    if (r.patron) {
      const m = v.match(new RegExp(r.patron));
      if (!m) return null;
      v = norm(m[1] != null ? m[1] : m[0]);
    }
    return v || null;
  };
  const fechaIso = (v, fmt) => {
    if (!v) return null;
    let y, mo, d, m;
    if (fmt === "ISO" && (m = v.match(/^(\d{4})-(\d{2})-(\d{2})/))) [y, mo, d] = [m[1], m[2], m[3]];
    else if (fmt === "DD/MM/AAAA" && (m = v.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})$/))) [d, mo, y] = [m[1], m[2], m[3]];
    else if (fmt === "DD-MM-AAAA" && (m = v.match(/^(\d{1,2})-(\d{1,2})-(\d{4})$/))) [d, mo, y] = [m[1], m[2], m[3]];
    else if (fmt === "AAAA/MM/DD" && (m = v.match(/^(\d{4})\/(\d{1,2})\/(\d{1,2})$/))) [y, mo, d] = [m[1], m[2], m[3]];
    else return null;
    const iso = `${y}-${String(mo).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const t = new Date(iso + "T00:00:00Z");
    return !isNaN(t) && t.toISOString().slice(0, 10) === iso ? iso : null;
  };
  const zona = document.querySelector(p.zona);
  if (!zona) return { zona: false, items: [], no: [] };
  const disp = (p.estado && p.estado.disponible || []).map(clave), nodisp = (p.estado && p.estado.no_disponible || []).map(clave);
  const ubic = {};
  for (const [k, v] of Object.entries(p.ubicaciones || {})) ubic[clave(k)] = v;
  const items = [], no = [];
  for (const it of zona.querySelectorAll(p.elemento)) {
    if (enRuido(it)) continue;
    const fecha = fechaIso(leer(it, p.fecha), p.fecha.formato || "ISO");
    if (!fecha) { no.push("fecha"); continue; }
    let estado = "DISPONIBLE";
    if (p.estado) {
      const e = clave(leer(it, p.estado));
      estado = disp.includes(e) ? "DISPONIBLE" : nodisp.includes(e) ? "AGOTADO" : null;
      if (!estado) { no.push("estado"); continue; }
    }
    let sucursal = null;
    if (p.ubicacion) {
      sucursal = ubic[clave(leer(it, p.ubicacion))] || null;
      if (!sucursal) { no.push("ubicacion"); continue; }
    }
    let libres = null;
    if (p.libres) { const n = parseInt(leer(it, p.libres), 10); libres = isNaN(n) ? null : n; }
    const hora = p.hora ? leer(it, p.hora) : null;
    items.push({ fecha, estado, sucursal, libres, hora, html: it.outerHTML.slice(0, 300) });
  }
  return { zona: true, items, no };
}

function agrupar(items) {
  const dias = new Map();
  for (const it of items) {
    const suc = it.sucursal || cfg.sucursal || null;
    const k = `${suc}|${it.fecha}`;
    const d = dias.get(k) || { sucursal: suc, fecha: it.fecha, estado: "AGOTADO", libres: null, horas: [], htmls: [] };
    if (it.estado === "DISPONIBLE") {
      d.estado = "DISPONIBLE";
      if (it.libres != null) d.libres = (d.libres || 0) + it.libres;
      if (it.hora) d.horas.push(it.hora);
    }
    d.htmls.push(it.html);
    dias.set(k, d);
  }
  for (const d of dias.values()) {
    d.horas = [...new Set(d.horas)].sort();
    d.firma = `${d.estado}|${d.libres}|${d.horas.join(",")}`;
    d.html = d.htmls.join("").slice(0, 300);
    delete d.htmls;
  }
  return dias;
}

async function leerDias() {
  const r = await page.evaluate(extraerEnPagina, P);
  stats.extracciones++;
  stats.no_reconocidos = r.no.length;
  return { zona: r.zona, dias: agrupar(r.items), no: r.no };
}

async function controlPresente() {
  return page.evaluate((sels) => {
    for (const s of sels) {
      const el = document.querySelector(s);
      if (el && (el.offsetParent !== null || el.tagName === "IFRAME")) return s;
    }
    return null;
  }, CONTROLES).catch(() => null);
}

async function capturaZona(pref) {
  const loc = page.locator(P.zona).first();
  const mask = (P.ignorar || []).map((s) => page.locator(s));
  const buf = await loc.screenshot({ type: "png", animations: "disabled", caret: "hide", mask, timeout: 5000 });
  let ruta = null;
  if (evid && pref) { ruta = path.join(evid, `${pref}-${String(++nEvid).padStart(5, "0")}.png`); fs.writeFileSync(ruta, buf); }
  return { buf, sha: sha(buf), ruta };
}

// ------------------------------------------------------------------ apertura y confirmación de cambios
async function abrir() { return conPagina(abrirSinCandado); }

async function abrirSinCandado() {
  abriendo = true;
  try {
    const r = await page.goto(cfg.url, { waitUntil: "load", timeout: 20000 });
    if (r && [401, 403, 429].includes(r.status())) { bloquear(`la página respondió HTTP ${r.status()}`); return; }
    const c = await controlPresente();
    if (c) { bloquear(`la página pide un control de acceso (${c})`); return; }
    await page.waitForSelector(P.zona, { timeout: 15000 });
    await instalarDom();
    await new Promise((r) => setTimeout(r, Math.max(300, estabilidad)));   // que el contenido cargado por JS se asiente
    const { dias, no } = await leerDias();
    confirmados = dias;
    const t = Date.now();
    emit({ tipo: "INICIAL", estrategia: "dom", ts_ms: t, elementos: [...dias.values()].length, no_reconocidos: no,
           celdas: [...dias.values()].map(({ sucursal, fecha, estado, libres, horas }) => ({ sucursal, fecha, estado, libres, horas })) });
    if (usaVisual) visualPrevio = (await capturaZona(null).catch(() => null) || {}).sha || null;
    estadoVista = null;
    vista("ACTIVA");
  } finally { abriendo = false; }
}

async function instalarDom() {
  await page.evaluate((p) => {
    if (window.__webObsInstalado) return;
    window.__webObsInstalado = true;
    const ign = p.ignorar || [];
    const enRuido = (n) => { const el = n.nodeType === 1 ? n : n.parentElement; return !!(el && ign.some((s) => el.closest(s))); };
    const enZona = (n) => { const el = n.nodeType === 1 ? n : n.parentElement; return !!(el && el.closest && el.closest(p.zona)); };
    const tocaZona = (nodos) => [...nodos].some((n) => n.nodeType === 1 && (n.matches(p.zona) || n.querySelector(p.zona)));
    new MutationObserver((muts) => {
      let relevante = false, ruido = 0;
      for (const m of muts) {
        if (enRuido(m.target)) { ruido++; continue; }
        if (enZona(m.target) || tocaZona(m.addedNodes) || tocaZona(m.removedNodes)) relevante = true;
      }
      window.__webMut({ relevante, ruido, ts: Date.now() });
    }).observe(document.body, { subtree: true, childList: true, attributes: true, characterData: true });
  }, P);
}

let pendiente = null;      // {ts}: primera mutación relevante aún no confirmada
let revisando = false;

async function revisar(origenCambio) {
  if (revisando || abriendo || bloqueado || page.isClosed()) return;
  revisando = true;
  try {
    const ts = pendiente ? pendiente.ts : Date.now();
    await new Promise((r) => setTimeout(r, estabilidad));
    const a = await conPagina(leerDias);
    if (!a.zona) { vista("SESION_CADUCADA", "la zona observada ya no está en la página"); return; }
    if (estabilidad > 0) {             // el cambio tiene que mantenerse: se vuelve a leer tras la ventana
      await new Promise((r) => setTimeout(r, estabilidad));
      const b = await conPagina(leerDias);
      const firma = (m) => [...m.entries()].map(([k, d]) => k + "=" + d.firma).sort().join(";");
      if (firma(a.dias) !== firma(b.dias)) { pendiente = { ts: Date.now() }; setTimeout(() => revisar("inestable"), 0); return; }
    }
    pendiente = null;
    await comparar(a.dias, ts, origenCambio);
  } catch (_) { /* página navegando: se revisará con el siguiente cambio o el sondeo */ }
  finally { revisando = false; }
}

async function comparar(dias, ts, origenCambio) {
  const claves = new Set([...confirmados.keys(), ...dias.keys()]);
  for (const k of [...claves].sort()) {
    const antes = confirmados.get(k), ahora = dias.get(k);
    if (antes && ahora && antes.firma === ahora.firma) continue;
    const d = ahora || { ...antes, estado: "NO_HABILITADO", libres: null, horas: [], html: "" };
    let imagen = null, imagen_sha256 = null;
    if (evid) {
      const c = await capturaZona("cambio").catch((e) => { emit({ tipo: "AVISO", mensaje: "sin captura de evidencia: " + String(e.message || e).split("\n")[0].slice(0, 160), ts_ms: Date.now() }); return null; });
      if (c) { imagen = c.ruta; imagen_sha256 = c.sha; }
    }
    emit({ tipo: "CELDA", estrategia: "dom", sucursal: d.sucursal, fecha: d.fecha, anterior: antes ? antes.estado : null,
           estado: d.estado, libres: d.libres, horas: d.horas, ts_ms: ts, origen: origenCambio,
           evidencia: { html: d.html, sha256: sha(d.html + "|" + d.firma), url: cfg.url, horas: d.horas, imagen, imagen_sha256 } });
  }
  confirmados = dias;
}

// ------------------------------------------------------------------ ciclos
let visualPrevio = null;
async function cicloVisual() {
  while (!salir) {
    await new Promise((r) => setTimeout(r, intervaloVisual));
    if (abriendo || bloqueado || page.isClosed()) continue;
    try {
      const c = await conPagina(() => capturaZona(null));
      if (visualPrevio && c.sha !== visualPrevio) {
        stats.cambios_visuales++;
        emit({ tipo: "CAMBIO_VISUAL", ts_ms: Date.now(), sha256: c.sha });
        if (!pendiente) pendiente = { ts: Date.now() };
        revisar("visual");
      }
      visualPrevio = c.sha;
    } catch (_) { /* */ }
  }
}

async function cicloSondeo() {     // respaldo: relectura periódica por si una mutación se perdió
  while (!salir) {
    await new Promise((r) => setTimeout(r, 3000));
    if (abriendo || bloqueado || page.isClosed()) continue;
    const c = await controlPresente();
    if (c) { bloquear(`la página pide un control de acceso (${c})`); continue; }
    if (P.sesion_perdida) {
      const perdida = await page.$(P.sesion_perdida).catch(() => null);
      if (perdida) { vista("SESION_CADUCADA", "la página indica que la sesión caducó"); continue; }
    }
    if (!page.url().startsWith(origen)) { vista("SESION_CADUCADA", "la página salió del sitio observado"); continue; }
    revisar("sondeo");
  }
}

(async () => {
  browser = await chromium.launch();
  browser.on("disconnected", () => { if (!salir) { emit({ tipo: "ERROR", mensaje: "el navegador se cerró", ts_ms: Date.now() }); process.exit(2); } });
  ctx = await browser.newContext({ viewport: { width: 1000, height: 900 }, deviceScaleFactor: 1 });
  page = await ctx.newPage();
  // Observar no envía nada: toda navegación que no sea GET (envío de formulario) se aborta.
  await page.route("**/*", (route) => {
    const r = route.request();
    if (r.isNavigationRequest() && r.method() !== "GET") { stats.navegaciones_bloqueadas++; return route.abort(); }
    return route.continue();
  });
  page.on("response", (r) => {
    try {
      if (new URL(r.url()).origin === origen && [401, 403, 429].includes(r.status())) bloquear(`el sitio respondió HTTP ${r.status()}`);
    } catch (_) { /* */ }
  });
  page.on("crash", () => { emit({ tipo: "ERROR", mensaje: "la página se bloqueó", ts_ms: Date.now() }); process.exit(2); });
  await ctx.exposeBinding("__webMut", (_src, m) => {
    stats.mutaciones_ruido += m.ruido;
    if (!m.relevante) return;
    stats.mutaciones_relevantes++;
    if (!pendiente) pendiente = { ts: m.ts };
    revisar("dom");
  });
  try {
    await abrir();
  } catch (e) {
    vista("DESCONECTADO", String(e.message || e));
  }
  if (cfg.una_vez) { salir = true; await browser.close().catch(() => {}); emit({ tipo: "FIN", stats, ts_ms: Date.now() }); process.exit(0); }
  emit({ tipo: "LISTO", estrategia, ts_ms: Date.now() });
  const lat = setInterval(() => emit({ tipo: "LATIDO", ts_ms: Date.now(), stats }), 2000);
  const tareas = [cicloSondeo()];
  if (usaVisual) tareas.push(cicloVisual());
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (l) => {
    let o; try { o = JSON.parse(l); } catch (_) { return; }
    if (o.cmd === "salir") rl.close();
    if (o.cmd === "reabrir" && !bloqueado) {
      try { await page.evaluate(() => { window.__webObsInstalado = false; }); } catch (_) { /* */ }
      try { await abrir(); emit({ tipo: "REABIERTA", ts_ms: Date.now() }); }
      catch (e) { vista("DESCONECTADO", String(e.message || e)); }
    }
  });
  rl.on("close", async () => {
    salir = true;
    clearInterval(lat);
    await browser.close().catch(() => {});
    emit({ tipo: "FIN", ts_ms: Date.now() });
    process.exit(0);
  });
})().catch((e) => { emit({ tipo: "ERROR", mensaje: String(e.message || e).slice(0, 300), ts_ms: Date.now() }); process.exit(1); });
