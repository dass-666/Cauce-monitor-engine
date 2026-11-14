// v2.1 · Observador de calendario con navegador real (Playwright/Chromium). SOLO para el portal
// de laboratorio propio (127.0.0.1): no se usa contra servicios externos ni sortea controles.
//
// Uso: node scripts/observador_calendario.js <config.json>
//   config: { url, estrategia: "dom"|"visual"|"ambas", intervalo_visual_ms, congelado_s,
//             evidencia_dir, perfil: {...selectores...} }
// Mantiene la página ABIERTA (no la recarga) y emite una línea "OBS {json}" por cada hallazgo:
//   INICIAL  estado de todas las celdas al abrir (por estrategia)
//   CELDA    una celda cambió de estado o de unidades libres (fecha, anterior, estado, libres, ts_ms, evidencia);
//            la estrategia visual solo ve colores: sus CELDA no traen unidades
//   VISTA    estado de la página: ACTIVA | SESION_CADUCADA | DESCONECTADO | CONGELADO
//   LATIDO   cada 2 s (el supervisor detecta un observador colgado)
// Órdenes por stdin (una por línea, JSON): {"cmd":"reabrir"} · {"cmd":"salir"}
//
// Estrategia DOM: MutationObserver sobre el calendario; el instante es el de la mutación en la página.
// Estrategia VISUAL: captura de imagen del calendario cada intervalo; los colores de referencia se
// toman de la LEYENDA de la propia página (calibración visual) y la posición de las celdas una sola
// vez al abrir (calibración geométrica). Si la página cambia de diseño hay que recalibrar (reabrir).
const emit = (o) => process.stdout.write("OBS " + JSON.stringify(o) + "\n");
let chromium;
try {
  ({ chromium } = require("playwright"));
} catch (e) {   // cierre v2.1: que el motor sepa POR QUÉ no arranca (antes solo salía por stderr, descartado)
  emit({ tipo: "ERROR", estrategia: "arranque", mensaje: "no se encontró el módulo playwright: " + String(e.message || e).split("\n")[0].slice(0, 200), ts_ms: Date.now() });
  process.exit(4);
}
const crypto = require("crypto");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const cfg = JSON.parse(fs.readFileSync(process.argv[2], "utf-8"));
const P = Object.assign({
  calendario: "[data-calendario=mensual]", celda: "[data-dia]", atr_fecha: "data-dia", atr_estado: "data-estado",
  atr_libres: "data-libres", vista: "#estado-vista", atr_vista: "data-estado-vista", actualizado: "#actualizado",
  atr_actualizado: "data-ts", leyenda: "[data-leyenda]", atr_leyenda: "data-leyenda", ruta_datos: "/observar/celdas",
}, cfg.perfil || {});
const estrategia = cfg.estrategia || "ambas";
const usaDom = estrategia !== "visual", usaVisual = estrategia !== "dom";
const intervaloVisual = Math.max(50, cfg.intervalo_visual_ms || 250);
const congeladoMs = Math.max(1000, (cfg.congelado_s || 5) * 1000);
const evid = cfg.evidencia_dir || null;
if (evid) fs.mkdirSync(evid, { recursive: true });

const sha = (b) => crypto.createHash("sha256").update(b).digest("hex");
let nEvid = 0;
const guardar = (buf, pref) => {
  if (!evid) return null;
  const f = path.join(evid, `${pref}-${String(++nEvid).padStart(5, "0")}.png`);
  fs.writeFileSync(f, buf);
  return f;
};

let browser, ctx, page, aux, salir = false, abriendo = false;
// Candado de página: una captura en curso y una navegación (reabrir) no deben solaparse; en CI se vio
// el navegador sin responder a una reapertura que coincidió con una captura (v2.1, corrida 43544642083).
let cola = Promise.resolve();
const conPagina = (fn) => { const r = cola.then(fn, fn); cola = r.catch(() => {}); return r; };
const estadoVista = { dom: null, visual: null };
const ultimoTs = { dom: { v: null, t: Date.now() }, visual: { v: null, t: Date.now() } };
const visual = { boxes: null, tabla: null, refs: null, previo: null, actBox: null, celdas: {} };
const red = { fallidas: 0, r401: 0 };

function vista(estr, estado, detalle) {
  if (estadoVista[estr] === estado) return;
  estadoVista[estr] = estado;
  emit({ tipo: "VISTA", estrategia: estr, estado, ts_ms: Date.now(), detalle: detalle || "" });
}

async function abrir() {
  return conPagina(abrirSinCandado);
}

async function abrirSinCandado() {
  abriendo = true;
  try {
    await page.goto(cfg.url, { waitUntil: "load", timeout: 15000 });
    await page.waitForSelector(P.calendario, { timeout: 10000 });
    if (usaDom) await instalarDom();
    if (usaVisual) await calibrarVisual();
    const t = Date.now();
    ultimoTs.dom = { v: null, t }; ultimoTs.visual = { v: null, t };
    if (usaDom) {
      const celdas = await page.$$eval(P.celda, (els, p) => els.map((el) => ({
        fecha: el.getAttribute(p.atr_fecha), estado: el.getAttribute(p.atr_estado),
        libres: Number(el.getAttribute(p.atr_libres) || 0) })), P);
      emit({ tipo: "INICIAL", estrategia: "dom", ts_ms: t, celdas });
    }
    if (usaVisual) {
      const est = await analizarVisual(await capturaTabla());
      emit({ tipo: "INICIAL", estrategia: "visual", ts_ms: Date.now(),
             celdas: Object.entries(est).map(([fecha, estado]) => ({ fecha, estado })) });
    }
    for (const e of ["dom", "visual"]) estadoVista[e] = null;
    if (usaDom) vista("dom", "ACTIVA");
    if (usaVisual) vista("visual", "ACTIVA");
  } finally { abriendo = false; }
}

// ------------------------------------------------------------------ estrategia DOM
async function instalarDom() {
  await page.evaluate((p) => {
    if (window.__obsInstalado) return;
    window.__obsInstalado = true;
    const tabla = document.querySelector(p.calendario);
    const previo = new Map();
    // v2.1 (unidades): el estado de una celda es estado + unidades libres (3 -> 1 también es un cambio)
    const firma = (el) => el.getAttribute(p.atr_estado) + "|" + (el.getAttribute(p.atr_libres) || "");
    for (const el of tabla.querySelectorAll(p.celda)) previo.set(el.getAttribute(p.atr_fecha), firma(el));
    new MutationObserver((muts) => {
      const ts = Date.now();
      const vistos = new Set();
      for (const m of muts) {
        const el = m.target.closest ? m.target.closest(p.celda) : null;
        if (!el || vistos.has(el)) continue;
        vistos.add(el);
        const fecha = el.getAttribute(p.atr_fecha), estado = el.getAttribute(p.atr_estado);
        const antes = previo.get(fecha) || "", ahora = firma(el);
        if (antes === ahora) continue;
        previo.set(fecha, ahora);
        const anterior = antes.split("|")[0] || null;
        window.__obsCambio({ fecha, anterior, estado, libres: Number(el.getAttribute(p.atr_libres) || 0), ts,
                             html: el.outerHTML.slice(0, 300) });
      }
    }).observe(tabla, { subtree: true, attributes: true, attributeFilter: [p.atr_estado, p.atr_libres, "class"] });
    const v = document.querySelector(p.vista);
    if (v) new MutationObserver(() => window.__obsVista(v.getAttribute(p.atr_vista)))
      .observe(v, { attributes: true, attributeFilter: [p.atr_vista] });
  }, P);
}

// ------------------------------------------------------------------ estrategia VISUAL
async function capturaTabla() {
  return page.screenshot({ clip: visual.tabla, type: "png", animations: "disabled", caret: "hide" });
}

async function calibrarVisual() {
  // Geometría (una vez): caja de la tabla y de cada celda, relativas a la tabla. Colores: leyenda.
  const g = await page.evaluate((p) => {
    const r = (el) => { const b = el.getBoundingClientRect(); return { x: b.left + scrollX, y: b.top + scrollY, w: b.width, h: b.height }; };
    const t = document.querySelector(p.calendario);
    const a = document.querySelector(p.actualizado);
    return { tabla: r(t), act: a ? r(a) : null,
             celdas: [...t.querySelectorAll(p.celda)].map((el) => ({ fecha: el.getAttribute(p.atr_fecha), ...r(el) })),
             leyenda: [...document.querySelectorAll(p.leyenda)].map((el) => ({ estado: el.getAttribute(p.atr_leyenda), ...r(el) })) };
  }, P);
  visual.tabla = { x: Math.floor(g.tabla.x), y: Math.floor(g.tabla.y), width: Math.ceil(g.tabla.w), height: Math.ceil(g.tabla.h) };
  visual.boxes = g.celdas.map((c) => ({ fecha: c.fecha, x: c.x - visual.tabla.x, y: c.y - visual.tabla.y, w: c.w, h: c.h }));
  visual.actBox = g.act ? { x: Math.floor(g.act.x), y: Math.floor(g.act.y), width: Math.ceil(Math.max(g.act.w, 10)), height: Math.ceil(g.act.h) } : null;
  visual.refs = {};
  for (const l of g.leyenda) {
    const clip = { x: Math.floor(l.x), y: Math.floor(l.y), width: Math.ceil(l.w), height: Math.ceil(l.h) };
    const buf = await page.screenshot({ clip, type: "png" });
    const [m] = await muestras(buf, [{ x: 0, y: 0, w: clip.width, h: clip.height }]);
    visual.refs[l.estado] = m;
  }
  visual.previo = null;
  visual.celdas = {};
}

// Decodifica la imagen en una página auxiliar (about:blank) y devuelve el color medio de una franja
// del borde izquierdo de cada caja (lejos del número del día).
async function muestras(buf, cajas) {
  return aux.evaluate(async ({ b64, cajas }) => {
    const img = new Image();
    img.src = "data:image/png;base64," + b64;
    await img.decode();
    const cv = document.createElement("canvas");
    cv.width = img.width; cv.height = img.height;
    const c2 = cv.getContext("2d", { willReadFrequently: true });
    c2.drawImage(img, 0, 0);
    return cajas.map((b) => {
      const x0 = Math.max(0, Math.round(b.x + 2)), w = Math.max(1, Math.min(6, Math.round(b.w / 6)));
      const y0 = Math.max(0, Math.round(b.y + b.h / 2 - 3)), h = Math.max(1, Math.min(6, Math.round(b.h - 2)));
      const d = c2.getImageData(x0, y0, w, h).data;
      let r = 0, g = 0, bl = 0, n = 0;
      for (let i = 0; i < d.length; i += 4) { r += d[i]; g += d[i + 1]; bl += d[i + 2]; n++; }
      return [r / n, g / n, bl / n];
    });
  }, { b64: buf.toString("base64"), cajas });
}

function clasificar(rgb) {
  let mejor = "DESCONOCIDO", dist = 1e9;
  for (const [estado, ref] of Object.entries(visual.refs)) {
    const d = Math.hypot(rgb[0] - ref[0], rgb[1] - ref[1], rgb[2] - ref[2]);
    if (d < dist) { dist = d; mejor = estado; }
  }
  return dist <= 18 ? mejor : "DESCONOCIDO";
}

async function analizarVisual(buf) {
  const ms = await muestras(buf, visual.boxes);
  const est = {};
  visual.boxes.forEach((b, i) => { est[b.fecha] = clasificar(ms[i]); });
  return est;
}

async function cicloVisual() {
  while (!salir) {
    const t0 = Date.now();
    try {
      if (!abriendo && visual.tabla && !page.isClosed()) await conPagina(async () => {
        if (abriendo) return;
        const buf = await capturaTabla();
        const h = sha(buf);
        const ts = Date.now();
        if (h !== visual.previo) {
          const est = await analizarVisual(buf);
          const cambios = [];
          for (const [fecha, estado] of Object.entries(est)) {
            const ant = visual.celdas[fecha];
            if (ant !== undefined && ant !== estado && estado !== "DESCONOCIDO") cambios.push({ fecha, anterior: ant, estado });
            if (estado !== "DESCONOCIDO") visual.celdas[fecha] = estado;
          }
          if (visual.previo === null) cambios.length = 0;      // primera imagen tras abrir: es la inicial
          visual.previo = h;
          if (cambios.length) {
            const f = guardar(buf, "visual");
            for (const c of cambios)
              emit({ tipo: "CELDA", estrategia: "visual", ...c, ts_ms: ts,
                     evidencia: { imagen: f, sha256: h, caja: visual.boxes.find((b) => b.fecha === c.fecha) } });
          }
        }
        if (visual.actBox) {           // ¿se sigue actualizando la página? (comparación de imagen del sello)
          const a = sha(await page.screenshot({ clip: visual.actBox, type: "png" }));
          if (a !== ultimoTs.visual.v) { ultimoTs.visual = { v: a, t: Date.now() }; if (estadoVista.visual === "CONGELADO") vista("visual", "ACTIVA", "la página volvió a actualizarse"); }
          else if (Date.now() - ultimoTs.visual.t > congeladoMs && estadoVista.visual === "ACTIVA") vista("visual", "CONGELADO", `sin cambios en el sello de actualización en ${congeladoMs} ms`);
        }
      });
    } catch (e) {
      if (!salir) emit({ tipo: "ERROR", estrategia: "visual", mensaje: String(e.message || e).slice(0, 200), ts_ms: Date.now() });
    }
    await new Promise((r) => setTimeout(r, Math.max(10, intervaloVisual - (Date.now() - t0))));
  }
}

async function cicloDomSello() {
  while (!salir) {
    try {
      if (usaDom && !abriendo && !page.isClosed()) {
        const v = await page.$eval(P.actualizado, (el, a) => el.getAttribute(a), P.atr_actualizado).catch(() => null);
        if (v !== ultimoTs.dom.v) { ultimoTs.dom = { v, t: Date.now() }; if (estadoVista.dom === "CONGELADO") vista("dom", "ACTIVA", "la página volvió a actualizarse"); }
        else if (Date.now() - ultimoTs.dom.t > congeladoMs && estadoVista.dom === "ACTIVA") vista("dom", "CONGELADO", `el sello de actualización no cambió en ${congeladoMs} ms`);
      }
    } catch (_) { /* página navegando */ }
    await new Promise((r) => setTimeout(r, 250));
  }
}

(async () => {
  browser = await chromium.launch();
  browser.on("disconnected", () => { if (!salir) { emit({ tipo: "ERROR", mensaje: "el navegador se cerró", ts_ms: Date.now() }); process.exit(2); } });
  ctx = await browser.newContext({ viewport: { width: 900, height: 900 }, deviceScaleFactor: 1 });
  page = await ctx.newPage();
  aux = await ctx.newPage();
  await ctx.exposeBinding("__obsCambio", async (_src, c) => {
    let evidencia = { html: c.html, sha256: sha(c.html) };
    emit({ tipo: "CELDA", estrategia: "dom", fecha: c.fecha, anterior: c.anterior, estado: c.estado, libres: c.libres,
           ts_ms: c.ts, evidencia });
  });
  await ctx.exposeBinding("__obsVista", (_src, est) => {
    if (est === "SESION_CADUCADA" || est === "DESCONECTADO") vista("dom", est, "lo indica la página");
    else if (est === "ACTIVA" && estadoVista.dom !== "CONGELADO") vista("dom", "ACTIVA", "lo indica la página");
    if (usaVisual && (est === "SESION_CADUCADA" || est === "DESCONECTADO" || est === "ACTIVA")) {
      if (est !== "ACTIVA" || estadoVista.visual !== "CONGELADO") vista("visual", est, "lo indica la página");
    }
  });
  page.on("requestfailed", (r) => { if (r.url().includes(P.ruta_datos)) red.fallidas++; });
  page.on("response", (r) => { if (r.url().includes(P.ruta_datos) && r.status() === 401) red.r401++; });
  page.on("crash", () => { emit({ tipo: "ERROR", mensaje: "la página se bloqueó", ts_ms: Date.now() }); process.exit(2); });
  try {
    await abrir();
  } catch (e) {
    emit({ tipo: "VISTA", estrategia: "apertura", estado: "DESCONECTADO", ts_ms: Date.now(), detalle: String(e.message || e).slice(0, 200) });
  }
  emit({ tipo: "LISTO", estrategia, pid_navegador: browser.process ? null : null, ts_ms: Date.now() });
  const lat = setInterval(() => emit({ tipo: "LATIDO", ts_ms: Date.now(), red }), 2000);
  const tareas = [];
  if (usaVisual) tareas.push(cicloVisual());
  if (usaDom) tareas.push(cicloDomSello());
  const rl = readline.createInterface({ input: process.stdin });
  rl.on("line", async (l) => {
    let o; try { o = JSON.parse(l); } catch (_) { return; }
    if (o.cmd === "salir") rl.close();
    if (o.cmd === "reabrir") {
      try { await page.evaluate(() => { window.__obsInstalado = false; }); } catch (_) { /* */ }
      try { await abrir(); emit({ tipo: "REABIERTA", ts_ms: Date.now() }); }
      catch (e) { emit({ tipo: "VISTA", estrategia: "apertura", estado: "DESCONECTADO", ts_ms: Date.now(), detalle: String(e.message || e).slice(0, 200) }); }
    }
  });
  rl.on("close", async () => {
    salir = true;
    clearInterval(lat);
    await Promise.allSettled(tareas);
    await browser.close().catch(() => {});
    emit({ tipo: "FIN", ts_ms: Date.now() });
    process.exit(0);
  });
})().catch((e) => { emit({ tipo: "ERROR", mensaje: String(e.message || e).slice(0, 300), ts_ms: Date.now() }); process.exit(1); });
