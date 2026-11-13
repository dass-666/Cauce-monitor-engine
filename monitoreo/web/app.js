/* Panel del Centro de Operaciones.
 * Sin dependencias ni compilación. Todo el contenido dinámico se inserta con
 * textContent (nunca innerHTML con datos) para prevenir XSS. */
"use strict";

const S = { token: null, user: null, view: "inicio", timer: null, regions: [], filters: {} };
try { S.token = sessionStorage.getItem("token"); } catch (_) { /* almacenamiento bloqueado */ }

// ------------------------------------------------------------------ utilidades
function h(tag, attrs, ...children) {
  const el = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs || {})) {
    if (v === null || v === undefined || v === false) continue;
    if (k === "class") el.className = v;
    else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
    else if (k === "value") el.value = v;
    else el.setAttribute(k, v === true ? "" : v);
  }
  for (const c of children.flat(Infinity)) {
    if (c === null || c === undefined || c === false) continue;
    el.append(c instanceof Node ? c : document.createTextNode(String(c)));
  }
  return el;
}
const $ = (id) => document.getElementById(id);
const isAdmin = () => S.user && S.user.role === "admin";
const labOn = () => !S.info || S.info.laboratorio;

// v1.7: aviso visible cuando se pierde la conexión con el servidor (y cuando vuelve). Los datos que se
// ven en pantalla se marcan como "sin actualizar" para que nadie decida con cifras viejas.
function setOffline(off) {
  if (S.offline === off) return;
  S.offline = off;
  let b = $("offline-banner");
  if (!b) {
    b = h("div", { id: "offline-banner", class: "offline-banner hidden", role: "alert" });
    document.body.prepend(b);
  }
  b.textContent = off ? "Sin conexión con el servidor. Lo que ves puede estar desactualizado; se reintenta solo. "
    + "No repitas operaciones: al volver la conexión se comprueba qué se guardó." : "";
  b.classList.toggle("hidden", !off);
  document.body.classList.toggle("is-offline", off);
  if (!off && S.user) refresh(true);
}

async function api(path, opts = {}) {
  let res;
  try {
    res = await fetch(path, {
      method: opts.method || "GET",
      headers: { "Content-Type": "application/json", ...(S.token ? { Authorization: "Bearer " + S.token } : {}), ...(opts.headers || {}) },
      body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
    });
  } catch (e) { setOffline(true); throw e; }        // TypeError: red caída o servidor detenido
  setOffline(false);
  if (res.status === 401 && !path.endsWith("/login")) { logout(true); throw new Error("Sesión expirada"); }
  let data = null;
  try { data = await res.json(); } catch (_) { /* sin cuerpo */ }
  if (!res.ok) {
    let msg = data && data.detail;
    let extra = null;
    if (Array.isArray(msg)) msg = msg.map((d) => `${(d.loc || []).slice(-1)[0]}: ${d.msg}`).join("; ");
    else if (msg && typeof msg === "object") { extra = msg; msg = msg.mensaje || JSON.stringify(msg); }  // v1.5: detalle con campos
    const err = new Error(msg || `Error ${res.status}`);
    err.status = res.status; err.detalle = extra;
    throw err;
  }
  if (opts.withStatus) return { status: res.status, data };
  return data;
}

function toast(msg, ms = 2600) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toast._t); toast._t = setTimeout(() => t.classList.remove("show"), ms);
}
async function act(fn, okMsg) {
  try {
    const r = await fn(); if (okMsg) toast(typeof okMsg === "function" ? okMsg(r) : okMsg);
    S.dirty = false; refresh(true); return r;
  }
  catch (e) { toast("⚠ " + e.message, 4000); }
}

// v1.4: tomar / completar / rechazar con clave de operación. Si se pierde la respuesta (red caída,
// motor reiniciado) se reintenta con la MISMA clave: el servidor no lo hace dos veces y devuelve el
// resultado guardado. Así una asignación no queda "tomada" sin que el operador lo sepa.
function opKey() {
  try { return crypto.randomUUID(); } catch (_) { return "op-" + Date.now() + "-" + Math.random().toString(36).slice(2, 10); }
}
async function assignmentOp(aid, action, okMsg) {
  const key = opKey();
  let last;
  for (let i = 0; i < 4; i++) {
    try {
      const r = await api(`/api/assignments/${aid}/${action}`, { method: "POST", headers: { "Idempotency-Key": key } });
      toast(r && r.repetida ? okMsg + " (confirmado tras reintentar)" : okMsg);
      S.dirty = false; refresh(true); return r;
    } catch (e) {
      if (!(e instanceof TypeError)) { toast("⚠ " + e.message, 4000); refresh(true); return null; }   // respuesta del servidor
      last = e;
      await new Promise((ok) => setTimeout(ok, 1000 * (i + 1)));                    // sin respuesta: reintento
    }
  }
  toast("⚠ Sin conexión con el motor. No sabemos si se guardó: pulsa otra vez cuando vuelva la conexión " +
        "(no se hará dos veces) o revisa la lista.", 8000);
  return null;
}

const fmtTime = (iso) => {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString("es", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
};
const ago = (iso) => {
  if (!iso) return "—";
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 0) return "en " + fmtDur(-s);
  return "hace " + fmtDur(s);
};
const fmtDur = (s) => s < 60 ? `${s}s` : s < 3600 ? `${Math.round(s / 60)}min` : `${(s / 3600).toFixed(1)}h`;
const num = (n, d = 0) => (n === null || n === undefined) ? "—" : Number(n).toLocaleString("es", { maximumFractionDigits: d });

const BADGE = {
  ACTIVA: "b-ok", ACTIVO: "b-ok", CERRADO: "b-ok", OK: "b-ok", ENVIADA: "b-ok", COMPLETADO: "b-ok", COMPLETADA: "b-ok",
  CONFIRMADA: "b-acc", TERMINADA: "b-ok",
  SUSPENDIDA: "", PENDIENTE: "", ELEGIBLE: "b-info", ASIGNADO: "b-acc", EN_PROCESO: "b-warn", REINTENTO: "b-warn",
  SEMIABIERTO: "b-warn", LIMITADO: "b-warn", LIMITE_LOCAL: "b-warn", PARCIAL: "b-warn", EN_CURSO: "b-warn",
  ERROR: "b-bad", ABIERTO: "b-bad", ERROR_TEMPORAL: "b-bad", FALLIDA: "b-bad", EXPIRADO: "b-bad", EXPIRADA: "b-bad",
  CANCELADO: "b-bad", CANCELADA: "b-bad", RECHAZADA: "b-bad", DESAPARECIDO: "b-bad", INTERRUMPIDA: "b-bad",
  NUEVA_DISPONIBILIDAD: "b-ok", REAPARICION: "b-ok", AUMENTO: "b-ok", AGOTADO: "b-bad", REDUCCION: "b-warn",
  CANCELACION: "b-bad", CAMBIO_HORARIO: "b-info", NUEVO_RECURSO: "", ASIGNACION: "b-acc", DISPONIBILIDAD: "b-info",
  CRITICO: "b-bad", AVISO: "b-warn", INFO: "b-info", LIDER: "b-ok", EN_ESPERA: "b-info", DETENIDO: "b-bad",
  SIN_MOTOR: "b-bad", DESACTUALIZADA: "b-warn", AL_DIA: "b-ok", SISTEMA: "b-bad", ENVIANDO: "b-warn",
  OBSOLETA: "b-warn", DISPONIBLE: "b-ok", OCUPADO: "b-warn", INACTIVO: "",
};
const badge = (s) => h("span", { class: "badge " + (BADGE[s] || "") }, String(s || "—").replace(/_/g, " "));

function table(cols, rows, onRow) {
  if (!rows || !rows.length) return h("div", { class: "empty" }, "Sin datos todavía");
  return h("div", { class: "table-wrap" }, h("table", {},
    h("thead", {}, h("tr", {}, cols.map((c) => h("th", { class: c.num ? "num" : null }, c.t)))),
    h("tbody", {}, rows.map((r) => h("tr", { class: onRow ? "clickable" : null, onclick: onRow ? () => onRow(r) : null },
      cols.map((c) => h("td", { class: c.num ? "num" : null }, c.f(r))))))));
}
function bar(frac) {
  const span = h("span"); span.style.width = Math.round(Math.max(0, Math.min(1, frac)) * 100) + "%"; // CSSOM: permitido por la CSP
  return h("div", { class: "bar" }, span);
}
const card = (title, ...body) => h("section", { class: "card" }, title ? h("h2", {}, title) : null, ...body);
const kpi = (label, value, sub) => h("div", { class: "card kpi" }, h("div", { class: "label" }, label),
  h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null);

function formData(form) {
  const o = {};
  for (const el of form.elements) {
    if (!el.name || el.disabled) continue;
    if (el.type === "checkbox") o[el.name] = el.checked;
    else if (el.value === "") continue;
    else if (el.type === "number") o[el.name] = Number(el.value);
    else o[el.name] = el.value;
  }
  return o;
}
const field = (label, input, cls) => h("label", { class: cls }, label, input);
const regionSelect = (name, withAll, value) => h("select", { name },
  withAll ? h("option", { value: "" }, "Todas las regiones") : null,
  S.regions.map((r) => h("option", { value: r.code, selected: r.code === value }, r.name)));

function openDrawer(...content) {
  const b = $("drawer-body"); b.replaceChildren(...content); $("drawer").classList.remove("hidden");
}
$("drawer-close").addEventListener("click", () => $("drawer").classList.add("hidden"));
$("drawer").addEventListener("click", (e) => { if (e.target.id === "drawer") $("drawer").classList.add("hidden"); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape") $("drawer").classList.add("hidden"); });

// ------------------------------------------------------------------ sesión
$("login-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  $("login-error").textContent = "";
  try {
    const r = await api("/api/auth/login", { method: "POST", body: formData(e.target) });
    S.token = r.token;
    try { sessionStorage.setItem("token", r.token); } catch (_) {}
    e.target.reset();
    await start();
  } catch (err) { $("login-error").textContent = err.message; }
});
$("logout").addEventListener("click", async () => {
  try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {}
  logout();
});
function logout(expired) {
  S.token = null; S.user = null;
  try { sessionStorage.removeItem("token"); } catch (_) {}
  clearInterval(S.timer);
  $("app").classList.add("hidden"); $("login-view").classList.remove("hidden");
  if (expired) $("login-error").textContent = "Tu sesión expiró. Vuelve a entrar.";
}

// ------------------------------------------------------------------ navegación
const VIEWS = {
  inicio: { t: "Inicio", f: viewHome },
  monitor: { t: "Monitor", f: viewMonitor },
  salud: { t: "Salud", f: viewHealth },
  solicitudes: { t: "Solicitudes", f: viewRequests },
  captura: { t: "Captura", f: () => viewCaptura(), lab: true },   // v1.5: laboratorio (captura.js); no existe en producción
  alertas: { t: "Notificaciones", f: viewNotificaciones },
  supervision: { t: "Supervisión", f: viewSupervision, admin: true },
  laboratorio: { t: "Laboratorio", f: viewLab, admin: true, lab: true },
  config: { t: "Configuración", f: viewConfig, admin: true },
};
function renderNav() {
  $("nav").replaceChildren(...Object.entries(VIEWS).filter(([, v]) => (!v.admin || isAdmin()) && (!v.lab || labOn())).map(([k, v]) =>
    h("a", { href: "#" + k, class: S.view === k ? "active" : null }, v.t)));
}
window.addEventListener("hashchange", () => { go(location.hash.slice(1)); });
function go(v) {
  if (!VIEWS[v] || (VIEWS[v].admin && !isAdmin()) || (VIEWS[v].lab && !labOn())) v = "inicio";
  S.view = v; S.dirty = false; renderNav(); refresh(true, true);
}

let rendering = false;
// Si el usuario empezó a rellenar un formulario, no se repinta la vista hasta que lo envíe o cambie de sección.
$("view").addEventListener("input", (e) => { if (e.target.closest("form")) S.dirty = true; });
// refresh(): repintado periódico (respeta formularios a medio rellenar).
// refresh(true): repintado inmediato pedido por el usuario (filtros, botones, cambio de sección).
async function refresh(force, scrollTop) {
  if (!S.user) return;
  if (rendering) { if (force) S.pendingForce = true; return; }  // se repite al terminar el repintado en curso
  // No repintar mientras el usuario escribe en un formulario de la vista.
  const a = document.activeElement;
  if (!force && S.dirty) return;
  if (!force && a && $("view").contains(a) && ["INPUT", "SELECT", "TEXTAREA"].includes(a.tagName)) return;
  rendering = true;
  try {
    const node = await VIEWS[S.view].f();
    $("view").replaceChildren(node);
    if (scrollTop) window.scrollTo(0, 0);
  } catch (e) {
    // v1.7: sin conexión se CONSERVA lo que había en pantalla (con el aviso de «sin conexión» arriba);
    // antes se sustituía por un error y el operador perdía de vista lo que estaba haciendo.
    if (!(e instanceof TypeError && S.offline && $("view").childElementCount)) {
      $("view").replaceChildren(card("No se pudo cargar", h("p", { class: "error" }, e.message)));
    }
  } finally {
    rendering = false;
    if (S.pendingForce) { S.pendingForce = false; refresh(true); }
  }
}

async function start() {
  S.user = await api("/api/auth/me");
  S.regions = await api("/api/regions");
  S.info = await api("/api/info");
  $("user-label").textContent = `${S.user.username} · ${S.user.role}` + (S.info.entorno === "desarrollo" ? "" : ` · ${S.info.entorno.toUpperCase()}`);
  $("login-view").classList.add("hidden"); $("app").classList.remove("hidden");
  go(location.hash.slice(1) || "inicio");
  clearInterval(S.timer);
  S.timer = setInterval(() => { if (!document.hidden && $("drawer").classList.contains("hidden")) refresh(); }, 4000);
}

// ------------------------------------------------------------------ INICIO
async function viewHome() {
  const d = await api("/api/dashboard");
  $("engine-dot").className = "dot " + (d.motor.activo ? "on" : "off");
  const fuentesActivas = d.fuentes.ACTIVA || 0;
  const totalFuentes = Object.values(d.fuentes).reduce((a, b) => a + b, 0);
  const cola = (d.solicitudes.PENDIENTE || 0) + (d.solicitudes.ELEGIBLE || 0) + (d.solicitudes.REINTENTO || 0);
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Centro de operaciones"),
      h("div", { class: "muted" }, d.motor.activo ? `Motor en marcha · ${num(d.motor.ciclos)} ciclos` : "Motor detenido"))),
    await centroBlock(),
    h("h3", {}, "Resumen general"),
    h("div", { class: "grid kpis" },
      kpi("Estado del motor", d.motor.activo ? "Activo" : "Detenido", d.motor.ultimo_error ? "Último error: " + d.motor.ultimo_error : "Sin errores del planificador"),
      kpi("Fuentes activas", `${fuentesActivas}/${totalFuentes}`, `${d.fuentes.ERROR || 0} en error · ${d.fuentes.SUSPENDIDA || 0} suspendidas`),
      kpi("Solicitudes en cola", num(cola), `${(d.solicitudes.ASIGNADO || 0) + (d.solicitudes.EN_PROCESO || 0)} asignadas o en proceso`),
      kpi("Alertas sin confirmar", num(d.alertas_sin_confirmar), `${d.ultima_hora.alertas} en la última hora`),
      kpi("Operadores activos", num(d.operadores_activos)),
      // v1.4: dos indicadores de "requiere atención" con un clic hacia donde se resuelve
      attentionKpi("Asignaciones sin actividad", d.asignaciones_sin_actividad,
        d.asignaciones_sin_actividad ? (isAdmin() ? "Pulsa para revisarlas y recuperarlas" : "Pulsa «Sigo con ella» o complétalas") : "Ninguna parada", "solicitudes"),
      attentionKpi("Fuentes en recuperación", d.fuentes_en_recuperacion,
        d.fuentes_en_recuperacion ? "Pausa automática tras fallos; el motor reintenta solo" : "Todas consultan con normalidad", "monitor"),
      kpi("Errores (1 h)", num(d.ultima_hora.errores), `${num(d.ultima_hora.consultas)} consultas · ${num(d.ultima_hora.eventos)} eventos`)),
    h("h3", {}, "Regiones monitoreadas"),
    h("div", { class: "grid regions" }, d.regiones.map((r) => h("div", { class: "card region" },
      h("div", { class: "name" }, r.name),
      h("div", { class: "row" }, h("span", { class: "muted" }, "Fuentes activas"), h("b", {}, `${r.fuentes_activas}/${r.fuentes}`)),
      h("div", { class: "row" }, h("span", { class: "muted" }, "Unidades libres"), h("b", {}, num(r.unidades_libres))),
      h("div", { class: "row" }, h("span", { class: "muted" }, "En cola"), h("b", {}, num(r.solicitudes_en_cola))),
      h("div", { class: "row" }, h("span", { class: "muted" }, "Asignadas"), h("b", {}, num(r.asignadas))),
      r.fuentes_con_error ? h("div", {}, badge("ERROR"), " ", `${r.fuentes_con_error} fuente(s) con problemas`) : null))),
    h("div", { class: "grid cols-2 section" },
      card("Alertas recientes", alertList(d.alertas_recientes)),
      card("Últimas disponibilidades", table([
        { t: "Detectado", f: (e) => ago(e.detected_at) }, { t: "Región", f: (e) => e.region },
        { t: "Recurso", f: (e) => e.resource_name }, { t: "Cambio", f: (e) => badge(e.change_type) },
        { t: "Libres", num: true, f: (e) => `${e.before.available ?? 0} → ${e.after.available}` }], d.ultimas_disponibilidades))),
    h("div", { class: "grid cols-2 section" },
      card("Operadores", table([
        { t: "Nombre", f: (o) => o.name }, { t: "Regiones", f: (o) => o.regions.length ? o.regions.join(", ") : "Todas" },
        { t: "Carga", num: true, f: (o) => `${o.current_load}/${o.max_concurrent}` },
        { t: "Estado", f: (o) => badge(o.active ? "ACTIVO" : "SUSPENDIDA") }], d.operadores)),
      card("Errores detectados", table([
        { t: "Cuándo", f: (p) => ago(p.started_at) }, { t: "Fuente", f: (p) => "#" + p.source_id },
        { t: "Resultado", f: (p) => badge(p.outcome) }, { t: "Detalle", f: (p) => p.error || "" }], d.errores_recientes))));
}

function attentionKpi(label, n, sub, view) {
  const el = kpi(label, num(n || 0), sub);
  if (n) { el.classList.add("attention", "clickable"); el.addEventListener("click", () => go(view)); }
  return el;
}

function alertList(alerts) {
  if (!alerts.length) return h("div", { class: "empty" }, "Sin alertas todavía");
  return h("div", {}, alerts.map((a) => h("div", { class: "alert-item" },
    badge(a.kind),
    h("div", { class: "body" },
      h("div", { class: "title" }, a.title),
      h("div", { class: "meta" }, `${a.region} · ${a.source_code} · ${fmtTime(a.created_at)}`,
        a.priority ? ` · prioridad ${a.priority}` : "", a.operator_name ? ` · ${a.operator_name}` : "")),
    h("div", { class: "actions" }, badge(a.status),
      !a.acknowledged_at ? h("button", { class: "btn small", onclick: () => act(() => api(`/api/alerts/${a.id}/confirmar`, { method: "POST" }), "Alerta confirmada") }, "Confirmar") : null,
      h("button", { class: "btn small ghost", onclick: () => showAlert(a.id) }, "Ver")))));
}

async function showAlert(id) {
  const a = await api(`/api/alerts/${id}`);
  openDrawer(h("h1", {}, a.title), h("p", {}, badge(a.kind), " ", badge(a.status)),
    h("dl", { class: "kv" },
      h("dt", {}, "Fuente"), h("dd", {}, a.source_code), h("dt", {}, "Región"), h("dd", {}, a.region),
      h("dt", {}, "Recurso"), h("dd", {}, a.resource), h("dt", {}, "Fecha y hora"), h("dd", {}, fmtTime(a.created_at)),
      h("dt", {}, "Solicitud"), h("dd", {}, a.payload.solicitud || "—"), h("dt", {}, "Prioridad"), h("dd", {}, a.priority ?? "—"),
      h("dt", {}, "Operador"), h("dd", {}, a.operator_name || "—"),
      h("dt", {}, "Estado de la solicitud"), h("dd", {}, a.payload.estado_solicitud || "—"),
      h("dt", {}, "Confirmada"), h("dd", {}, a.acknowledged_at ? `${fmtTime(a.acknowledged_at)} por ${a.acknowledged_by}` : "No")),
    h("h3", {}, "Entregas por canal"),
    table([{ t: "Canal", f: (d) => d.canal }, { t: "Estado", f: (d) => badge(d.estado) },
      { t: "Intentos", num: true, f: (d) => d.intentos }, { t: "Error", f: (d) => d.ultimo_error || "" }], a.entregas),
    h("h3", {}, "Registro de notificaciones"),
    table([{ t: "Cuándo", f: (l) => fmtTime(l.ts) }, { t: "Canal", f: (l) => l.canal },
      { t: "Resultado", f: (l) => badge(l.resultado === "OK" ? "OK" : "FALLIDA") }, { t: "Detalle", f: (l) => l.detalle }], a.registro),
    h("h3", {}, "Contenido"), h("pre", { class: "json" }, JSON.stringify(a.payload, null, 2)));
}

// ------------------------------------------------------------------ MONITOR
async function viewMonitor() {
  const f = S.filters.monitor || (S.filters.monitor = { region: "", type: "", source: "" });
  const [sources, events, polls] = await Promise.all([
    api("/api/sources"),
    api(`/api/events?limit=100${f.region ? "&region=" + f.region : ""}${f.type ? "&change_type=" + f.type : ""}`),
    api(`/api/polls?limit=100${f.source ? "&source_id=" + f.source : ""}`)]);
  const byId = Object.fromEntries(sources.map((s) => [s.id, s]));
  const sourceActions = (s) => !isAdmin() ? null : h("div", { class: "actions" },
    s.status === "ACTIVA"
      ? h("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); act(() => api(`/api/sources/${s.id}/suspender`, { method: "POST" }), "Fuente suspendida"); } }, "Suspender")
      : h("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); act(() => api(`/api/sources/${s.id}/reanudar`, { method: "POST" }), "Fuente reanudada"); } }, "Reanudar"),
    h("button", { class: "btn small ghost", onclick: (e) => { e.stopPropagation(); act(() => api(`/api/sources/${s.id}/consultar`, { method: "POST" }), (r) => `Consulta: ${r.resultado} · ${r.eventos} eventos · ${r.ms} ms`); } }, "Consultar ya"));
  const sel = (key, options) => h("select", { onchange: (e) => { f[key] = e.target.value; refresh(true); } },
    options.map(([v, t]) => h("option", { value: v, selected: f[key] === v }, t)));
  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "Monitor de fuentes")),
    card("Estado de cada fuente", table([
      { t: "Fuente", f: (s) => h("div", {}, h("b", {}, s.name), h("div", { class: "muted mono" }, `${s.code} · ${s.connector_type}${srcHost(s) ? " · " + srcHost(s) : ""}`),
          httpSummary(s)) },
      { t: "Región", f: (s) => s.region },
      { t: "Estado", f: (s) => h("div", {}, badge(s.status), s.status_reason ? h("div", { class: "muted" }, s.status_reason) : null) },
      { t: "Circuito", f: circuitCell },
      { t: "Última consulta", f: (s) => ago(s.last_polled_at) }, { t: "Próxima", f: (s) => s.status === "ACTIVA" ? ago(s.next_poll_at) : "—" },
      { t: "Consultas", num: true, f: (s) => num(s.metrics.consultas) },
      { t: "Errores", num: true, f: (s) => `${num(s.metrics.errores)} (${num(s.metrics.tasa_error * 100, 1)}%)` },
      { t: "Cambios", num: true, f: (s) => num(s.metrics.cambios) },
      { t: "Latencia", num: true, f: (s) => num(s.metrics.latencia_media_ms, 1) + " ms" },
      { t: "Último error", f: (s) => s.last_error || "" },
      { t: "", f: sourceActions }], sources, (s) => showDiagnosis(s.id))),
    h("div", { class: "grid cols-2 section" },
      card("Eventos detectados",
        h("div", { class: "filters" },
          sel("region", [["", "Todas las regiones"], ...S.regions.map((r) => [r.code, r.name])]),
          sel("type", [["", "Todos los cambios"], ...["NUEVA_DISPONIBILIDAD", "REAPARICION", "AUMENTO", "REDUCCION", "AGOTADO", "CANCELACION", "CAMBIO_HORARIO", "NUEVO_RECURSO"].map((t) => [t, t.replace(/_/g, " ")])])),
        table([
          { t: "Detectado", f: (e) => fmtTime(e.detected_at) }, { t: "Región", f: (e) => e.region },
          { t: "Recurso", f: (e) => e.resource_name }, { t: "Cambio", f: (e) => badge(e.change_type) },
          { t: "Libres", num: true, f: (e) => `${e.before.available ?? "—"} → ${e.after.available}` },
          { t: "Tiempo de detección", num: true, f: (e) => e.detection_latency_ms === null ? "—" : num(e.detection_latency_ms / 1000, 2) + " s" },
          { t: "Alerta", f: (e) => e.relevant ? "Sí" : "No" }], events, (e) => showTrace(e.id)),
        h("p", { class: "muted" }, "Pulsa un evento para ver su recorrido completo: fuente → detección → asignación → aviso.")),
      card("Historial de consultas",
        h("div", { class: "filters" }, sel("source", [["", "Todas las fuentes"], ...sources.map((s) => [String(s.id), s.code])])),
        table([
          { t: "Inicio", f: (p) => fmtTime(p.started_at) }, { t: "Fuente", f: (p) => (byId[p.source_id] || {}).code || p.source_id },
          { t: "Resultado", f: (p) => badge(p.outcome) }, { t: "Intentos", num: true, f: (p) => p.attempts },
          { t: "Recursos", num: true, f: (p) => p.items }, { t: "Cambios", num: true, f: (p) => p.changes },
          { t: "Duplicados", num: true, f: (p) => p.duplicates }, { t: "ms", num: true, f: (p) => num(p.duration_ms, 1) },
          { t: "Error", f: (p) => p.error || "" }], polls))));
}

// v1.4: el circuito explicado sin jerga. CERRADO = normal; ABIERTO = pausa tras varios fallos,
// el motor prueba de nuevo él solo; SEMIABIERTO = esa consulta de prueba está en curso.
function circuitCell(s) {
  if (s.circuit_state === "ABIERTO")
    return h("div", {}, h("span", { class: "badge b-warn" }, "EN RECUPERACIÓN"),
      h("div", { class: "muted" }, `Pausa automática tras fallos${s.circuit_open_count > 1 ? ` (${s.circuit_open_count}.ª vez seguida)` : ""}`),
      h("div", { class: "muted" }, "Próxima prueba " + ago(s.next_poll_at)));
  if (s.circuit_state === "SEMIABIERTO")
    return h("div", {}, h("span", { class: "badge b-warn" }, "PROBANDO"), h("div", { class: "muted" }, "Consulta de prueba en curso"));
  return h("div", {}, h("span", { class: "badge b-ok" }, "NORMAL"),
    s.circuit_open_count > 0 ? h("div", { class: "muted" }, "Recuperándose de fallos recientes") : null);
}

// ------------------------------------------------------------------ v1.3: fuentes HTTP y recorrido de un evento
function srcHost(s) {
  const u = (s.config || {}).url_base || (s.config || {}).url;
  try { return u ? new URL(u).host : ""; } catch (_) { return ""; }
}
function httpSummary(s) {
  const c = (s.last_result || {}).conector;
  if (!c) return null;
  const parts = [`${c.paginas} pág.`, `HTTP ${num(c.http_ms, 0)} ms`];
  if (c.sin_cambios) parts.push("sin cambios (304)");
  if (c.reinicios) parts.push(`${c.reinicios} reinicio(s) por cambios entre páginas`);
  if (c.descartados) parts.push(`${c.descartados} elemento(s) descartado(s)`);
  if (c.incompleta) parts.push("consulta INCOMPLETA");
  return h("div", { class: "muted" }, parts.join(" · "));
}
async function showTrace(id) {
  const t = await api(`/api/eventos/${id}/recorrido`);
  openDrawer(h("h2", {}, `Recorrido del evento ${t.evento}`), h("p", {}, `${t.recurso} · ${t.region}`),
    table([{ t: "Paso", f: (p) => h("b", {}, p.paso) }, { t: "Cuándo", f: (p) => p.en ? fmtTime(p.en) : "—" },
      { t: "Desde el cambio", num: true, f: (p) => p.desde_el_cambio_ms === null ? "—" : num(p.desde_el_cambio_ms / 1000, 3) + " s" },
      { t: "Detalle", f: (p) => h("span", { class: "muted mono" }, Object.entries(p).filter(([k, v]) => !["paso", "en", "desde_el_cambio_ms"].includes(k) && v !== null && v !== undefined)
        .map(([k, v]) => `${k}: ${v}`).join(" · ")) }], t.pasos));
}
const p3 = (o) => o && o.n ? `${num(o.p50 / 1000, 2)} / ${num(o.p95 / 1000, 2)} / ${num(o.p99 / 1000, 2)} s` : "—";
function serviceCard(m) {
  const t = m.tiempos_ms, c = m.consultas;
  const rows = [["Detección (cambio en la fuente → motor)", t.deteccion], ["Procesamiento (detección → asignación y avisos)", t.procesamiento],
    ["Hasta la asignación", t.hasta_asignacion], ["Hasta la alerta", t.hasta_alerta],
    ...Object.entries(t.hasta_notificacion).map(([k, v]) => [`Hasta la entrega por ${k}`, v])];
  return card("Servicio en la última hora (medido)",
    table([{ t: "Tramo", f: (r) => r[0] }, { t: "Casos", num: true, f: (r) => num(r[1].n) }, { t: "p50 / p95 / p99", num: true, f: (r) => p3(r[1]) }], rows),
    h("p", {}, `Consultas correctas: ${c.exito_pct === null ? "—" : num(c.exito_pct, 1) + " %"} de ${num(c.total)} · `,
      Object.entries(c.por_resultado).map(([k, v]) => `${k.replace(/_/g, " ")}: ${num(v)}`).join(" · ")),
    h("p", {}, `Cambios detectados: ${num(m.cambios.detectados)} · ofertas que desaparecieron antes de usarse: ${num(m.cambios.ofertas_desaparecidas_antes_de_usarse)} · asignaciones anuladas por la fuente: ${num(m.cambios.asignaciones_anuladas_por_la_fuente)}`),
    h("p", { class: "muted" }, `Retrasos: ${num(m.retrasos.eventos_sin_procesar)} evento(s) sin procesar (el más antiguo hace ${secs(m.retrasos.evento_mas_antiguo_sin_procesar_s)}) · entrega pendiente más antigua: ${secs(m.retrasos.entrega_pendiente_mas_antigua_s)}`));
}
function operatorsCard(ops) {
  return card("Operadores", table([{ t: "Operador", f: (o) => h("b", {}, o.name) },
    { t: "Regiones", f: (o) => o.regions.length ? o.regions.join(", ") : "todas" },
    { t: "Estado", f: (o) => badge(o.active ? (o.current_load >= o.max_concurrent ? "OCUPADO" : "DISPONIBLE") : "INACTIVO") },
    { t: "Carga", num: true, f: (o) => `${o.current_load} / ${o.max_concurrent}` },
    { t: "Última asignación", f: (o) => o.last_assigned_at ? ago(o.last_assigned_at) : "—" }], ops));
}

// ------------------------------------------------------------------ SALUD (v1.1)
const secs = (s) => s === null || s === undefined ? "—" : fmtDur(Math.round(s));
const lat = (o) => o && o.n ? `${num(o.p50 / 1000, 2)} s / ${num(o.p95 / 1000, 2)} s` : "—";
async function selfCheckCard() {
  if (!isAdmin()) return null;
  // El autodiagnóstico revisa toda la base: se reutiliza durante 60 s salvo que se pida de nuevo.
  if (!S.diag || Date.now() - S.diagAt > 60000) { S.diag = await api("/api/autodiagnostico"); S.diagAt = Date.now(); }
  const d = S.diag;
  const problems = d.comprobaciones.filter((i) => i.estado !== "OK");
  const oks = d.comprobaciones.filter((i) => i.estado === "OK");
  return h("section", { class: "card section" },
    h("div", { class: "page-head" }, h("h2", {}, "Autodiagnóstico"),
      h("div", { class: "actions" }, badge(d.estado), h("span", { class: "muted" }, ` ${d.criticos} crítico(s), ${d.avisos} aviso(s) · ${fmtTime(d.momento)}`),
        h("button", { class: "btn small", onclick: () => { S.diag = null; refresh(true); } }, "Revisar ahora"))),
    problems.length ? h("div", {}, problems.map((i) => h("div", { class: "diag-item" },
      h("div", {}, badge(i.estado), " ", h("b", {}, `${i.area}: ${i.titulo}`)),
      i.detalle ? h("div", { class: "muted" }, i.detalle) : null,
      i.accion ? h("div", {}, "Qué hacer: " + i.accion) : null))) : null,
    h("p", { class: "muted" }, `Correcto: ${oks.map((i) => i.area + " (" + i.titulo + ")").join(" · ") || "—"}`),
    await reparacionesBlock());
}
// v1.7: reparaciones. Las seguras se aplican solas en el autodiagnóstico; aquí se pueden lanzar ya.
// Las que borran datos piden motivo y confirmación explícita.
async function reparacionesBlock() {
  const r = (await api("/api/diagnostico/reparaciones")).reparaciones.filter((x) => x.pendientes);
  if (!r.length) return h("p", { class: "muted", id: "reparaciones" }, "Reparaciones: no hace falta ninguna.");
  return h("div", { id: "reparaciones" }, h("h3", {}, "Reparaciones disponibles"), r.map((x) => h("div", { class: "diag-item" },
    h("div", {}, badge(x.segura ? "INFO" : "AVISO"), " ", h("b", {}, x.titulo), ` · ${x.pendientes} casos`),
    h("div", { class: "muted" }, x.explicacion),
    h("button", { class: "btn small" + (x.segura ? "" : " danger"), "data-reparar": x.clave, onclick: async () => {
      let body = { confirmo: false, motivo: "" };
      if (!x.segura) {
        const motivo = prompt("Esta reparación borra datos. Escribe el motivo (queda en la auditoría):");
        if (!motivo) return;
        body = { confirmo: true, motivo };
      }
      await act(() => api(`/api/diagnostico/reparaciones/${x.clave}`, { method: "POST", body }), (o) => `Reparado: ${o.pendientes} casos`);
      S.diag = null; refresh(true);
    } }, x.segura ? "Reparar ahora" : "Reparar (con confirmación)"))));
}
async function viewHealth() {
  const [d, st, diagCard, svc, ops] = await Promise.all([api("/api/observabilidad"), api("/api/estadisticas?group=region&days=7"), selfCheckCard(),
    api("/api/metricas/servicio?minutos=60"), api("/api/operators")]);
  const m = d.motor, lg = m.lider_global;
  const problems = d.fuentes.filter((f) => f.status !== "ACTIVA" || f.desactualizada || f.circuito !== "CERRADO");
  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "Salud del motor"), h("span", { class: "muted" }, "Pulsa una fuente para ver por qué no se actualiza")),
    h("div", { class: "grid kpis" },
      kpi("Planificador", badge(lg && lg.vigente ? "LIDER" : "SIN_MOTOR"),
        lg ? `Líder: ${lg.titular} · renovado hace ${num(lg.renovado_hace_s, 1)} s` : "Ninguna instancia lo está ejecutando"),
      kpi("Esta instancia", badge(m.rol), m.activo ? `${num(m.ciclos)} ciclos · último hace ${num(m.segundos_desde_ultimo_ciclo, 1)} s` : "Motor no iniciado en este proceso"),
      kpi("Errores de ciclo", num(m.errores_ciclo_seguidos || 0), m.ultimo_error || "Sin errores recientes"),
      kpi("Fuentes con problemas", `${problems.length} / ${d.fuentes.length}`),
      kpi("Solicitudes en espera", num(d.colas.solicitudes_en_espera), `${num(d.colas.solicitudes_en_proceso)} en proceso`),
      kpi("Alertas sin confirmar", num(d.colas.alertas_sin_confirmar), `${num(d.colas.entregas_pendientes)} entregas pendientes · ${num(d.colas.entregas_fallidas_24h)} fallidas 24 h`),
      kpi("Base de datos", `${d.base_de_datos.tipo}`, `latencia ${num(d.base_de_datos.latencia_ms, 2)} ms`),
      kpi("Proceso", `${num(d.proceso.memoria_mb, 1)} MB`, `CPU ${num(d.proceso.cpu_pct, 1)} % · ${d.proceso.hilos} hilos`)),
    diagCard,
    h("div", { class: "grid cols-2 section" }, serviceCard(svc), operatorsCard(ops)),
    card("Estado por región", table([
      { t: "Región", f: (r) => h("b", {}, r.name) },
      { t: "Fuentes OK", num: true, f: (r) => `${r.fuentes_ok}/${r.fuentes}` },
      { t: "Con problemas", num: true, f: (r) => r.fuentes_con_problemas ? badge("AVISO") : "0" },
      { t: "Última consulta correcta", f: (r) => ago(r.ultima_consulta_ok) },
      { t: "Detección p50/p95 (24 h)", num: true, f: (r) => lat(r.latencia_deteccion_ms) },
      { t: "En espera", num: true, f: (r) => num(r.solicitudes_en_espera) },
      { t: "Unidades libres", num: true, f: (r) => num(r.unidades_libres) },
      { t: "Alertas sin confirmar", num: true, f: (r) => num(r.alertas_sin_confirmar) }], d.regiones)),
    h("div", { class: "section" }, card("Estado por fuente", table([
      { t: "Fuente", f: (f) => h("div", {}, h("b", {}, f.code), h("div", { class: "muted" }, f.region)) },
      { t: "Estado", f: (f) => h("div", {}, badge(f.status), f.motivo ? h("div", { class: "muted" }, f.motivo) : null) },
      { t: "Datos", f: (f) => badge(f.desactualizada ? "DESACTUALIZADA" : "AL_DIA") },
      { t: "Circuito", f: (f) => badge(f.circuito) },
      { t: "Último resultado", f: (f) => f.ultimo_resultado ? badge(f.ultimo_resultado) : "—" },
      { t: "Último OK", f: (f) => f.segundos_desde_ok === null ? "nunca" : `hace ${secs(f.segundos_desde_ok)}` },
      { t: "Errores seguidos", num: true, f: (f) => num(f.errores_seguidos) },
      { t: "Consultas 24 h", num: true, f: (f) => `${num(f.consultas_24h)} (${num(f.tasa_error_24h * 100, 1)}% error)` },
      { t: "Detección p50/p95", num: true, f: (f) => lat(f.latencia_deteccion_ms) }], d.fuentes, (f) => showDiagnosis(f.id)))),
    h("div", { class: "grid cols-2 section" },
      card("Estadísticas de 7 días por región", table([
        { t: "Región", f: (x) => x.region }, { t: "Consultas", num: true, f: (x) => num(x.consultas) },
        { t: "Error", num: true, f: (x) => num(x.tasa_error * 100, 2) + " %" }, { t: "Limitadas", num: true, f: (x) => num(x.limitadas) },
        { t: "Cambios", num: true, f: (x) => num(x.cambios) }, { t: "Latencia media", num: true, f: (x) => num(x.latencia_media_ms, 1) + " ms" }], st)),
      card("Historial y recuperación",
        h("p", {}, `Consultas guardadas en detalle: ${num(d.historial.filas_poll_log)} (se conservan ${d.historial.retencion_consultas_dias} días; los resúmenes por hora y día se conservan más).`),
        h("p", {}, `Resumido hasta: ${d.historial.agregado_hasta ? fmtTime(d.historial.agregado_hasta) : "todavía nada"}.`),
        h("p", {}, d.historial.ultimo_mantenimiento ? `Último mantenimiento: ${ago(d.historial.ultimo_mantenimiento.ts)} (${num(d.historial.ultimo_mantenimiento.ms, 0)} ms)` : "Mantenimiento aún no ejecutado."),
        h("p", { class: "muted mono" }, d.ultima_recuperacion ? "Última recuperación tras arranque: " + JSON.stringify(d.ultima_recuperacion) : "Sin recuperación registrada."))));
}
async function showDiagnosis(id) {
  const d = await api(`/api/diagnostico/fuentes/${id}`);
  openDrawer(h("h2", {}, `Diagnóstico: ${d.fuente.code}`),
    h("p", {}, "Veredicto: ", badge(d.veredicto), d.motivo_estado ? ` · ${d.motivo_estado}` : ""),
    ...d.causas.map((c) => h("div", { class: "card" }, h("div", {}, badge(c.gravedad), " ", h("b", {}, c.causa)),
      c.detalle ? h("p", {}, c.detalle) : null, c.accion ? h("p", { class: "muted" }, "Qué hacer: " + c.accion) : null)),
    h("h3", {}, "Resultados de las últimas 50 consultas"),
    h("p", {}, Object.entries(d.ultimas_50_consultas).map(([k, v]) => h("span", {}, badge(k), ` ${v}  `))),
    h("h3", {}, "Últimos errores"),
    table([{ t: "Inicio", f: (p) => fmtTime(p.started_at) }, { t: "Resultado", f: (p) => badge(p.outcome) }, { t: "Error", f: (p) => p.error || "" }], d.ultimos_errores));
}

// ------------------------------------------------------------------ SOLICITUDES
async function viewRequests() {
  const f = S.filters.req || (S.filters.req = { region: "", status: "", q: "" });
  const qs = new URLSearchParams({ limit: "300" });
  if (f.region) qs.set("region", f.region); if (f.status) qs.set("status", f.status); if (f.q) qs.set("q", f.q);
  const [reqs, mine, idle] = await Promise.all([api("/api/requests?" + qs), api("/api/assignments?mine=" + (!isAdmin()) + "&limit=50"),
    api("/api/assignments?sin_actividad=true&limit=200")]);
  const form = h("form", { class: "form", onsubmit: (e) => {
    e.preventDefault();
    const d = formData(e.target);
    const cond = {};
    for (const k of ["desde", "hasta", "vence"]) if (d[k]) { cond[k] = new Date(d[k]).toISOString(); delete d[k]; }
    if (d.nombre_contiene) { cond.nombre_contiene = d.nombre_contiene; delete d.nombre_contiene; }
    d.conditions = cond;
    act(() => api("/api/requests", { method: "POST", body: d }), (r) => `Solicitud ${r.code} registrada (${r.status})`).then((r) => { if (r) e.target.reset(); });
  } },
    field("Titular (ficticio)", h("input", { name: "holder", required: true, maxlength: 128, placeholder: "Expediente Ficticio 001" })),
    field("Región", regionSelect("region")),
    field("Tipo de recurso", h("select", { name: "resource_type" }, ["entrada", "mesa", "unidad", "turno"].map((t) => h("option", { value: t }, t)))),
    field("Prioridad (1 = máxima)", h("select", { name: "priority" }, [1, 2, 3, 4, 5].map((p) => h("option", { value: p, selected: p === 3 }, p)))),
    field("Cantidad", h("input", { name: "quantity", type: "number", min: 1, max: 100, value: 1 })),
    field("Nombre contiene (opcional)", h("input", { name: "nombre_contiene", maxlength: 128 })),
    field("Desde (opcional)", h("input", { name: "desde", type: "datetime-local" })),
    field("Hasta (opcional)", h("input", { name: "hasta", type: "datetime-local" })),
    h("button", { class: "btn primary", type: "submit" }, "Registrar solicitud"));
  const statuses = ["PENDIENTE", "ELEGIBLE", "ASIGNADO", "EN_PROCESO", "COMPLETADO", "REINTENTO", "EXPIRADO", "CANCELADO", "ERROR"];
  const counts = {};
  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "Solicitudes"), h("div", { class: "muted" }, `${reqs.total} expedientes ficticios`)),
    idle.length ? h("div", { class: "section" }, card("⚠ Asignaciones sin actividad",
      h("p", { class: "muted" }, isAdmin()
        ? "Se tomaron y llevan tiempo sin novedades. El motor no las libera solo: pregunta al operador y usa «Recuperar…»."
        : "Llevan tiempo sin novedades. Si sigues con ellas pulsa «Sigo con ella»; si ya terminaste, «Completar»."),
      assignmentsTable(idle))) : null,
    card("Registrar solicitud", form),
    h("div", { class: "section" }, card(isAdmin() ? "Asignaciones recientes" : "Mis asignaciones", assignmentsTable(mine))),
    h("div", { class: "section" }, card("Cola de solicitudes",
      h("div", { class: "filters" },
        h("select", { onchange: (e) => { f.region = e.target.value; refresh(true); } }, h("option", { value: "" }, "Todas las regiones"),
          S.regions.map((r) => h("option", { value: r.code, selected: f.region === r.code }, r.name))),
        h("select", { onchange: (e) => { f.status = e.target.value; refresh(true); } }, h("option", { value: "" }, "Todos los estados"),
          statuses.map((s) => h("option", { value: s, selected: f.status === s }, s.replace(/_/g, " ")))),
        h("input", { type: "search", placeholder: "Buscar código o titular", value: f.q, maxlength: 64,
          onchange: (e) => { f.q = e.target.value; e.target.blur(); refresh(true); } })),
      table([
        { t: "Código", f: (r) => h("span", { class: "mono" }, r.code) }, { t: "Titular", f: (r) => r.holder },
        { t: "Región", f: (r) => r.region }, { t: "Recurso", f: (r) => `${r.resource_type} ×${r.quantity}` },
        { t: "Prioridad", num: true, f: (r) => r.priority }, { t: "Estado", f: (r) => badge(r.status) },
        { t: "Operador", f: (r) => r.operator_name || "—" }, { t: "Intentos", num: true, f: (r) => r.attempts },
        { t: "Registro", f: (r) => fmtTime(r.created_at) }], reqs.items, (r) => showRequest(r.id)))));
}

function assignmentsTable(list) {
  return table([
    { t: "#", f: (a) => a.id }, { t: "Solicitud", f: (a) => h("span", { class: "mono" }, a.request_code) },
    { t: "Recurso", f: (a) => a.resource }, { t: "Región", f: (a) => a.region }, { t: "Operador", f: (a) => a.operator_name },
    { t: "Estado", f: (a) => h("div", {}, badge(a.status),
        a.inactiva ? h("div", {}, h("span", { class: "badge b-warn" }, "SIN ACTIVIDAD"),
          h("div", { class: "muted" }, "Última actividad " + ago(a.last_activity_at || a.taken_at || a.created_at))) : null) },
    { t: "Vence", f: (a) => a.status === "ACTIVA" ? ago(a.expires_at) : a.status === "EN_PROCESO" ? "Tomada " + ago(a.taken_at || a.created_at) : "—" },
    { t: "", f: (a) => h("div", { class: "actions" },
      a.status === "ACTIVA" ? h("button", { class: "btn small primary", onclick: (e) => { e.stopPropagation(); assignmentOp(a.id, "tomar", "Asignación tomada"); } }, "Tomar") : null,
      a.status === "EN_PROCESO" ? h("button", { class: "btn small primary", onclick: (e) => { e.stopPropagation(); assignmentOp(a.id, "completar", "Completada"); } }, "Completar") : null,
      a.status === "EN_PROCESO" && !isAdmin() ? h("button", { class: "btn small", title: "Avisa de que sigues trabajando en ella (quita el aviso de inactividad)", onclick: (e) => { e.stopPropagation(); act(() => api(`/api/assignments/${a.id}/actividad`, { method: "POST" }), "Anotado: sigues con ella"); } }, "Sigo con ella") : null,
      ["ACTIVA", "EN_PROCESO"].includes(a.status) ? h("button", { class: "btn small danger", onclick: (e) => { e.stopPropagation(); assignmentOp(a.id, "rechazar", "Rechazada: vuelve a la cola"); } }, "Rechazar") : null,
      a.status === "EN_PROCESO" && isAdmin() ? h("button", { class: "btn small", onclick: (e) => { e.stopPropagation(); showRecover(a); } }, "Recuperar…") : null,
      h("button", { class: "btn small ghost", "data-explicacion": a.id, title: "Por qué el motor eligió a este operador",
        onclick: (e) => { e.stopPropagation(); showExplicacion(a.id); } }, "¿Por qué?")) }], list);
}

// v1.4: recuperación manual de una asignación EN_PROCESO abandonada (solo administrador).
// Nunca se libera sola: una persona decide qué pasó, con motivo y confirmación, y queda auditado.
function showRecover(a) {
  const form = h("form", { class: "form", onsubmit: async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    const r = await act(() => api(`/api/assignments/${a.id}/recuperar`, { method: "POST",
      body: { resultado: d.resultado, motivo: d.motivo, confirmar: !!d.confirmar } }),
      d.resultado === "completada" ? "Marcada como completada" : "Devuelta a la cola");
    if (r) $("drawer").classList.add("hidden");
  } },
    h("p", { class: "full" }, `Asignación #${a.id} · solicitud ${a.request_code} · ${a.operator_name || "—"} · tomada ${ago(a.taken_at || a.created_at)}.`),
    h("p", { class: "muted full" }, "Antes de decidir, pregunta al operador. Si el trabajo se hizo, márcala como completada; " +
      "si no, devuélvela a la cola para que otro la atienda. El recurso retenido se libera en ambos casos."),
    field("¿Qué pasó?", h("select", { name: "resultado" },
      h("option", { value: "devolver_a_cola" }, "No se terminó: devolver a la cola"),
      h("option", { value: "completada" }, "Se terminó: marcar como completada")), "full"),
    field("Motivo (queda en la auditoría)", h("input", { name: "motivo", required: true, minlength: 3, maxlength: 300,
      placeholder: "Ej.: el operador confirma que no pudo terminarla" }), "full"),
    h("label", { class: "check full" }, h("input", { type: "checkbox", name: "confirmar", required: true }),
      " Confirmo que he comprobado la situación con el operador"),
    h("button", { class: "btn primary", type: "submit" }, "Aplicar"));
  openDrawer(h("h1", {}, "Recuperar asignación"), form);
}

async function showRequest(id) {
  const r = await api(`/api/requests/${id}`);
  const cancellable = !["COMPLETADO", "CANCELADO", "EXPIRADO"].includes(r.status);
  openDrawer(h("h1", {}, r.code), h("p", {}, badge(r.status)),
    h("dl", { class: "kv" },
      h("dt", {}, "Titular"), h("dd", {}, r.holder), h("dt", {}, "Región"), h("dd", {}, r.region),
      h("dt", {}, "Recurso"), h("dd", {}, `${r.resource_type} ×${r.quantity}`), h("dt", {}, "Prioridad"), h("dd", {}, r.priority),
      h("dt", {}, "Operador"), h("dd", {}, r.operator_name || "—"), h("dt", {}, "Intentos"), h("dd", {}, r.attempts),
      h("dt", {}, "Condiciones"), h("dd", { class: "mono" }, JSON.stringify(r.conditions)),
      r.last_error ? [h("dt", {}, "Error"), h("dd", { class: "error" }, r.last_error)] : null),
    h("div", { class: "actions section" },
      cancellable ? h("button", { class: "btn danger", onclick: async () => { await act(() => api(`/api/requests/${id}/cancelar`, { method: "POST" }), "Solicitud cancelada"); showRequest(id); } }, "Cancelar solicitud") : null,
      isAdmin() && r.status === "ERROR" ? h("button", { class: "btn", onclick: async () => { await act(() => api(`/api/requests/${id}/reactivar`, { method: "POST" }), "Reactivada"); showRequest(id); } }, "Reactivar") : null),
    h("h3", {}, "Historial de estados"),
    h("ul", { class: "timeline" }, r.historial.map((e) => h("li", {},
      h("b", {}, e.to.replace(/_/g, " ")), h("span", { class: "muted" }, ` · ${fmtTime(e.ts)} · ${e.actor}`),
      e.note ? h("div", { class: "muted" }, e.note) : null))),
    h("h3", {}, "Historial de asignaciones"), assignmentsTable(r.asignaciones));
}

// ------------------------------------------------------------------ ALERTAS
async function viewAlerts() {
  const f = S.filters.alerts || (S.filters.alerts = { region: "", unacked: false });
  const alerts = await api(`/api/alerts?limit=200${f.region ? "&region=" + f.region : ""}${f.unacked ? "&unacked=true" : ""}`);
  return h("div", {},
    h("div", { class: "page-head" }, h("h1", {}, "Alertas")),
    card(null,
      h("div", { class: "filters" },
        h("select", { onchange: (e) => { f.region = e.target.value; refresh(true); } }, h("option", { value: "" }, "Todas las regiones"),
          S.regions.map((r) => h("option", { value: r.code, selected: f.region === r.code }, r.name))),
        h("select", { onchange: (e) => { f.unacked = e.target.value === "1"; refresh(true); } },
          h("option", { value: "0", selected: !f.unacked }, "Todas"), h("option", { value: "1", selected: f.unacked }, "Sin confirmar"))),
      alertList(alerts)));
}

// ------------------------------------------------------------------ LABORATORIO
async function viewLab() {
  const [items, runs, sources] = await Promise.all([api("/api/lab/items"), api("/api/lab/runs"), api("/api/sources")]);
  const f = S.filters.lab || (S.filters.lab = { region: "" });
  const shown = items.filter((i) => !f.region || i.region === f.region);
  const feeds = [...new Set(sources.filter((s) => s.connector_type === "simulacion").map((s) => s.config.feed))];
  const itemAction = (i, action, body, msg) => (e) => { e.stopPropagation(); act(() => api(`/api/lab/items/${i.id}/${action}`, { method: "POST", body }), msg); };
  const createForm = h("form", { class: "form", onsubmit: (e) => {
    e.preventDefault(); const d = formData(e.target);
    if (d.starts_at) d.starts_at = new Date(d.starts_at).toISOString();
    act(() => api("/api/lab/items", { method: "POST", body: d }), "Evento ficticio creado").then((r) => { if (r) e.target.reset(); });
  } },
    field("Feed (fuente simulada)", h("select", { name: "feed" }, feeds.map((x) => h("option", { value: x }, x)))),
    field("Clave", h("input", { name: "resource_key", required: true, pattern: "[A-Za-z0-9_.\\-]{1,64}", placeholder: "EV-100" })),
    field("Nombre", h("input", { name: "name", required: true, maxlength: 256, placeholder: "Función Ficticia" })),
    field("Región", regionSelect("region")),
    field("Tipo de recurso", h("select", { name: "resource_type" }, ["entrada", "mesa", "unidad", "turno"].map((t) => h("option", { value: t }, t)))),
    field("Capacidad", h("input", { name: "capacity", type: "number", min: 1, value: 5 })),
    field("Disponibles", h("input", { name: "available", type: "number", min: 0, value: 0 })),
    field("Inicio", h("input", { name: "starts_at", type: "datetime-local" })),
    h("button", { class: "btn primary" }, "Crear evento"));
  const compForm = h("form", { class: "form", onsubmit: (e) => {
    e.preventDefault(); const d = formData(e.target); d.item_id = Number(d.item_id);
    act(() => api("/api/lab/competencia", { method: "POST", body: d }), (r) => `${r.solicitudes_creadas} solicitudes compiten (${r.grupo}); se liberaron ${r.unidades_liberadas} unidades`);
  } },
    field("Recurso", h("select", { name: "item_id" }, items.map((i) => h("option", { value: i.id }, `${i.region} · ${i.name}`)))),
    field("Solicitudes compitiendo", h("input", { name: "requests", type: "number", min: 2, max: 500, value: 10 })),
    field("Unidades a liberar", h("input", { name: "units", type: "number", min: 1, max: 100, value: 1 })),
    h("button", { class: "btn primary" }, "Lanzar competencia"));
  const accForm = h("form", { class: "form", onsubmit: (e) => {
    e.preventDefault();
    act(() => api("/api/lab/acelerada", { method: "POST", body: formData(e.target) }), "Simulación acelerada iniciada: los resultados aparecerán abajo");
  } },
    field("Horas virtuales", h("input", { name: "hours", type: "number", min: 1, max: 168, step: "any", value: 12 })),
    field("Paso (s)", h("input", { name: "step_s", type: "number", min: 5, max: 3600, value: 30 })),
    field("Solicitudes por región", h("input", { name: "requests_per_region", type: "number", min: 1, max: 2000, value: 40 })),
    field("Tasa de error de fuentes", h("input", { name: "error_rate", type: "number", min: 0, max: 1, step: "0.01", value: 0.03 })),
    field("Tasa de fallo de notificaciones", h("input", { name: "notify_fail_rate", type: "number", min: 0, max: 1, step: "0.01", value: 0.1 })),
    field("Semilla", h("input", { name: "seed", type: "number", value: 7 })),
    h("button", { class: "btn primary" }, "Ejecutar simulación"));
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Laboratorio de simulación"),
      h("div", { class: "muted" }, "Provoca cambios en el inventario ficticio y observa cómo el motor los detecta, asigna y alerta.")),
      h("div", { class: "actions" },
        h("button", { class: "btn", onclick: () => act(() => api("/api/lab/random", { method: "POST", body: { steps: 1 } }), (r) => `${r.cambios} cambios aleatorios`) }, "Cambios aleatorios"),
        h("button", { class: "btn", onclick: () => act(() => api("/api/lab/random", { method: "POST", body: { steps: 1, burst: true } }), (r) => `Pico de demanda: ${r.cambios} cambios simultáneos`) }, "Pico de cambios simultáneos"))),
    card("Inventario ficticio",
      h("div", { class: "filters" }, h("select", { onchange: (e) => { f.region = e.target.value; refresh(true); } },
        h("option", { value: "" }, "Todas las regiones"), S.regions.map((r) => h("option", { value: r.code, selected: f.region === r.code }, r.name)))),
      table([
        { t: "Región", f: (i) => i.region }, { t: "Recurso", f: (i) => h("div", {}, i.name, h("div", { class: "muted mono" }, `${i.feed} · ${i.resource_key}`)) },
        { t: "Inicio", f: (i) => fmtTime(i.starts_at) },
        { t: "Libres", num: true, f: (i) => h("div", {}, `${i.available}/${i.capacity}`, bar(i.capacity ? i.available / i.capacity : 0)) },
        { t: "Estado", f: (i) => badge(i.cancelled ? "CANCELADO" : i.available ? "ACTIVO" : "AGOTADO") },
        { t: "Acciones", f: (i) => h("div", { class: "actions" },
          h("button", { class: "btn small primary", onclick: itemAction(i, "liberar", { units: 1 }, "Liberada 1 unidad") }, "Liberar +1"),
          h("button", { class: "btn small", onclick: itemAction(i, "consumir", { units: 1 }, "Consumida 1 unidad") }, "−1"),
          h("button", { class: "btn small", onclick: itemAction(i, "agotar", {}, "Inventario agotado") }, "Agotar"),
          i.cancelled ? h("button", { class: "btn small", onclick: itemAction(i, "restaurar", {}, "Restaurado") }, "Restaurar")
            : h("button", { class: "btn small danger", onclick: itemAction(i, "cancelar", {}, "Evento cancelado") }, "Cancelar"),
          h("button", { class: "btn small ghost", onclick: itemAction(i, "reprogramar", { hours: 2 }, "Horario +2 h") }, "+2 h")) }], shown)),
    h("div", { class: "grid cols-2 section" }, card("Crear evento ficticio", createForm), card("Solicitudes compitiendo por un recurso", compForm)),
    h("div", { class: "section" }, card("Simulación acelerada (base de datos aislada)", accForm,
      h("h3", {}, "Resultados"),
      runs.length ? runs.map((r) => h("details", { class: "section" },
        h("summary", {}, `#${r.id} · `, badge(r.status), ` · ${fmtTime(r.started_at)} · ${r.params.hours} h virtuales`,
          r.results && r.results.tiempo_real_s ? ` · ${r.results.tiempo_real_s} s reales · ${r.results.eventos_detectados} eventos · ${r.results.total_violaciones} violaciones de integridad` : ""),
        h("pre", { class: "json" }, JSON.stringify(r.results, null, 2)))) : h("div", { class: "empty" }, "Aún no hay simulaciones"))));
}

// ------------------------------------------------------------------ CONFIGURACIÓN
async function viewConfig() {
  const tab = S.filters.cfgTab || "motor";
  const tabs = [["motor", "Parámetros del motor"], ["fuentes", "Fuentes y frecuencias"], ["operadores", "Operadores"],
    ["canales", "Notificaciones"], ["usuarios", "Usuarios"], ["auditoria", "Auditoría"], ["metricas", "Métricas"]];
  const head = h("div", {}, h("div", { class: "page-head" }, h("h1", {}, "Configuración")),
    h("div", { class: "tabs" }, tabs.map(([k, t]) => h("button", { class: "btn small " + (tab === k ? "primary" : ""), onclick: () => { S.filters.cfgTab = k; refresh(true); } }, t))));
  let body;
  if (tab === "motor") {
    const p = await api("/api/settings/engine");
    const help = {
      tick_segundos: "Cada cuánto revisa el planificador qué fuentes tocan", concurrencia_maxima: "Consultas simultáneas",
      umbral_circuito: "Fallos seguidos para abrir el circuito", enfriamiento_circuito_s: "Segundos con el circuito abierto",
      aperturas_hasta_error: "Aperturas seguidas antes de marcar la fuente en ERROR", backoff_max_s: "Tope del retroceso exponencial",
      ausencias_para_cancelar: "Consultas completas sin ver un recurso para considerarlo cancelado",
      ttl_asignacion_s: "Segundos que tiene el operador para tomar una asignación", max_intentos_solicitud: "Reasignaciones máximas",
      alerta_minimo_aumento: "Unidades mínimas de aumento para alertar", reintentos_notificacion: "Reintentos por canal",
      backoff_notificacion_s: "Base del retroceso entre reintentos de notificación" };
    body = card("Parámetros del motor", h("form", { class: "form", onsubmit: (e) => {
      e.preventDefault(); const d = formData(e.target);
      d.tipos_que_alertan = [...e.target.querySelectorAll("input[data-tipo]:checked")].map((x) => x.value);
      for (const k of Object.keys(d)) if (k.startsWith("tipo_")) delete d[k];
      act(() => api("/api/settings/engine", { method: "PUT", body: d }), "Parámetros guardados");
    } },
      Object.entries(p).filter(([k]) => k !== "tipos_que_alertan").map(([k, v]) =>
        field(help[k] || k, h("input", { name: k, type: "number", min: 0, step: "any", value: v }))),
      h("div", { class: "full" }, h("div", { class: "muted" }, "Cambios que generan alerta"),
        h("div", { class: "actions" }, ["NUEVA_DISPONIBILIDAD", "REAPARICION", "AUMENTO", "REDUCCION", "AGOTADO", "CANCELACION", "CAMBIO_HORARIO"].map((t) =>
          h("label", { class: "badge" }, h("input", { type: "checkbox", value: t, "data-tipo": "1", checked: p.tipos_que_alertan.includes(t) }), " " + t.replace(/_/g, " "))))),
      h("button", { class: "btn primary" }, "Guardar")));
  } else if (tab === "fuentes") {
    const sources = await api("/api/sources");
    body = card("Frecuencias y límites por fuente", h("p", { class: "muted" }, "Intervalo: segundos entre consultas. Variación: ± porcentaje aleatorio. Límite: consultas máximas por minuto."),
      table([
        { t: "Fuente", f: (s) => h("div", {}, h("b", {}, s.code), h("div", { class: "muted" }, `${s.region} · ${s.connector_type}`)) },
        { t: "Estado", f: (s) => badge(s.status) },
        { t: "Configuración", f: (s) => h("form", { class: "form", onsubmit: (e) => {
          e.preventDefault(); act(() => api(`/api/sources/${s.id}`, { method: "PATCH", body: formData(e.target) }), "Fuente actualizada");
        } },
          field("Intervalo (s)", h("input", { name: "interval_s", type: "number", min: 1, step: "any", value: s.interval_s })),
          field("Variación (0–0.9)", h("input", { name: "jitter_pct", type: "number", min: 0, max: 0.9, step: "0.01", value: s.jitter_pct })),
          field("Límite/min", h("input", { name: "rate_limit_per_min", type: "number", min: 1, max: 600, value: s.rate_limit_per_min })),
          field("Reintentos", h("input", { name: "max_retries", type: "number", min: 0, max: 10, value: s.max_retries })),
          h("button", { class: "btn small primary" }, "Guardar")) }], sources));
  } else if (tab === "operadores") {
    const ops = await api("/api/operators");
    body = h("div", { class: "grid" },
      card("Nuevo operador", h("form", { class: "form", onsubmit: (e) => {
        e.preventDefault(); const d = formData(e.target); d.regions = d.regions ? [d.regions] : [];
        act(() => api("/api/operators", { method: "POST", body: d }), "Operador creado");
      } },
        field("Nombre", h("input", { name: "name", required: true, maxlength: 128 })),
        field("Región", h("select", { name: "regions" }, h("option", { value: "" }, "Todas"), S.regions.map((r) => h("option", { value: r.code }, r.name)))),
        field("Máx. simultáneas", h("input", { name: "max_concurrent", type: "number", min: 1, max: 100, value: 3 })),
        h("button", { class: "btn primary" }, "Crear"))),
      card("Operadores", table([
        { t: "Nombre", f: (o) => o.name }, { t: "Regiones", f: (o) => o.regions.length ? o.regions.join(", ") : "Todas" },
        { t: "Carga", num: true, f: (o) => `${o.current_load}/${o.max_concurrent}` }, { t: "Usuario", f: (o) => o.user_id ? "#" + o.user_id : "—" },
        { t: "Estado", f: (o) => badge(o.active ? "ACTIVO" : "SUSPENDIDA") },
        { t: "", f: (o) => h("button", { class: "btn small", onclick: () => act(() => api(`/api/operators/${o.id}`, { method: "PATCH", body: { active: !o.active } }), "Operador actualizado") }, o.active ? "Desactivar" : "Activar") }], ops)));
  } else if (tab === "canales") {
    const [chs, obs] = await Promise.all([api("/api/channels"), api("/api/observabilidad")]);
    const tg = obs.telegram;
    body = card("Canales de notificación",
      h("p", { class: "muted" }, "Los webhooks solo pueden apuntar a hosts permitidos en MONITOREO_WEBHOOK_HOSTS. El correo tiene la interfaz preparada pero no está implementado."),
      h("p", {}, h("b", {}, "Telegram: "), tg.token_configurado
        ? `token configurado · ${tg.chats_autorizados} destinatario(s) autorizado(s) · ${tg.es_emulador ? "EMULADOR LOCAL (no sale a Internet)" : "API real de Telegram"}`
        : "sin configurar (define MONITOREO_TELEGRAM_TOKEN y MONITOREO_TELEGRAM_CHATS en .env; ver docs/TELEGRAM.md)"),
      table([
        { t: "Canal", f: (c) => h("div", {}, h("b", {}, c.name), h("div", { class: "muted" }, c.type)) },
        { t: "Estado", f: (c) => badge(c.enabled ? "ACTIVO" : "SUSPENDIDA") },
        { t: "Configuración", f: (c) => c.type === "webhook" ? h("form", { class: "form", onsubmit: (e) => {
          e.preventDefault(); act(() => api(`/api/channels/${c.id}`, { method: "PATCH", body: { config: formData(e.target) } }), "Canal actualizado");
        } }, field("URL", h("input", { name: "url", value: c.config.url || "", maxlength: 500 })),
          field("Secreto de firma", h("input", { name: "secret", type: "password", value: c.config.secret || "", autocomplete: "new-password" })),
          h("button", { class: "btn small primary" }, "Guardar"))
          : c.type === "simulado" ? h("form", { class: "form", onsubmit: (e) => {
            e.preventDefault(); act(() => api(`/api/channels/${c.id}`, { method: "PATCH", body: { config: formData(e.target) } }), "Canal actualizado");
          } }, field("Tasa de fallo (0–1)", h("input", { name: "fail_rate", type: "number", min: 0, max: 1, step: "0.05", value: c.config.fail_rate ?? 0 })),
            h("button", { class: "btn small primary" }, "Guardar")) : h("span", { class: "mono muted" }, JSON.stringify(c.config)) },
        { t: "", f: (c) => h("div", { class: "actions" },
          h("button", { class: "btn small", onclick: () => act(() => api(`/api/channels/${c.id}`, { method: "PATCH", body: { enabled: !c.enabled } }), "Canal actualizado") }, c.enabled ? "Desactivar" : "Activar"),
          h("button", { class: "btn small ghost", onclick: () => act(() => api(`/api/channels/${c.id}/probar`, { method: "POST" }), (r) => (r.ok ? "Prueba enviada: " : "Prueba fallida: ") + r.detalle) }, "Probar")) }], chs));
  } else if (tab === "usuarios") {
    const users = await api("/api/users");
    body = h("div", { class: "grid" },
      card("Nuevo usuario", h("form", { class: "form", onsubmit: (e) => {
        e.preventDefault(); const d = formData(e.target); d.regions = d.regions ? [d.regions] : [];
        act(() => api("/api/users", { method: "POST", body: d }), "Usuario creado").then((r) => { if (r) e.target.reset(); });
      } },
        field("Usuario", h("input", { name: "username", required: true, pattern: "[a-zA-Z0-9_.\\-]{3,64}", autocomplete: "off" })),
        field("Contraseña (mín. 10)", h("input", { name: "password", type: "password", required: true, minlength: 10, autocomplete: "new-password" })),
        field("Rol", h("select", { name: "role" }, h("option", { value: "operador" }, "Operador"), h("option", { value: "admin" }, "Administrador"))),
        field("Nombre de operador", h("input", { name: "operator_name", maxlength: 128 })),
        field("Región del operador", h("select", { name: "regions" }, h("option", { value: "" }, "Todas"), S.regions.map((r) => h("option", { value: r.code }, r.name)))),
        h("button", { class: "btn primary" }, "Crear usuario"))),
      card("Cambiar mi contraseña", h("form", { class: "form", onsubmit: (e) => {
        e.preventDefault(); act(() => api("/api/auth/password", { method: "POST", body: formData(e.target) }), "Contraseña actualizada").then((r) => { if (r) e.target.reset(); });
      } },
        field("Actual", h("input", { name: "current_password", type: "password", required: true, autocomplete: "current-password" })),
        field("Nueva (mín. 10)", h("input", { name: "new_password", type: "password", required: true, minlength: 10, autocomplete: "new-password" })),
        h("button", { class: "btn primary" }, "Cambiar"))),
      card("Usuarios", table([
        { t: "Usuario", f: (u) => u.username }, { t: "Rol", f: (u) => u.role }, { t: "Estado", f: (u) => badge(u.active ? "ACTIVO" : "SUSPENDIDA") },
        { t: "Creado", f: (u) => fmtTime(u.created_at) },
        { t: "", f: (u) => u.active && u.username !== S.user.username ? h("button", { class: "btn small danger", onclick: () => act(() => api(`/api/users/${u.id}/desactivar`, { method: "POST" }), "Usuario desactivado") }, "Desactivar") : null }], users)));
  } else if (tab === "auditoria") {
    const log = await api("/api/audit?limit=300");
    body = card("Registro de auditoría", table([
      { t: "Cuándo", f: (a) => fmtTime(a.ts) }, { t: "Usuario", f: (a) => a.usuario }, { t: "Acción", f: (a) => a.accion },
      { t: "Objetivo", f: (a) => a.objetivo }, { t: "IP", f: (a) => a.ip }, { t: "Detalle", f: (a) => h("span", { class: "mono" }, JSON.stringify(a.detalle)) }], log));
  } else {
    const m = await api("/api/metrics");
    const pct = (o, unit) => o.n ? `p50 ${num(o.p50, 1)} · p95 ${num(o.p95, 1)} · p99 ${num(o.p99, 1)} · máx ${num(o.max, 1)} ${unit} (n=${o.n})` : "sin datos";
    body = h("div", { class: "grid kpis" },
      kpi("CPU del proceso", num(m.proceso.cpu_pct, 1) + " %", `${m.proceso.hilos} hilos`),
      kpi("Memoria", num(m.proceso.memoria_mb, 1) + " MB"),
      kpi("Consultas", num(m.totales.consultas), pct(m.consulta_ms, "ms")),
      kpi("Eventos", num(m.totales.eventos), "Detección: " + pct(m.deteccion_ms, "ms")),
      kpi("Alertas", num(m.totales.alertas), "Entrega: " + pct(m.entrega_alerta_ms, "ms")),
      kpi("Integridad", m.integridad.length ? `${m.integridad.length} problemas` : "Correcta", m.integridad.slice(0, 3).join(" · ") || "Retenciones, cargas y estados coherentes"));
  }
  return h("div", {}, head, body);
}


// ------------------------------------------------------------------ v1.7: CENTRO (cifras con filtros)
const PERIODOS = { "1h": "Última hora", hoy: "Hoy", "24h": "Últimas 24 h", "7d": "Últimos 7 días" };
const msTxt = (o) => o && o.n ? `p50 ${fmtMs(o.p50)} · p95 ${fmtMs(o.p95)} (n=${o.n})` : "sin datos en el periodo";
const fmtMs = (v) => v === null || v === undefined ? "—" : v < 1000 ? `${Math.round(v)} ms` : v < 60000 ? `${(v / 1000).toFixed(1)} s` : `${(v / 60000).toFixed(1)} min`;
async function centroBlock() {
  const f = S.filters.centro || (S.filters.centro = { region: "", sucursal: "", operador: "", estado: "", periodo: "24h" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v)).toString();
  const [d, ops] = await Promise.all([api("/api/centro" + (qs ? "?" + qs : "")), isAdmin() ? api("/api/operators") : Promise.resolve([])]);
  const g = d.estado_general, k = d.indicadores, c = d.captura;
  const sel = (key, opts, todos) => h("select", { "aria-label": todos, "data-filtro": key, onchange: (e) => { f[key] = e.target.value; refresh(true); } },
    h("option", { value: "" }, todos), opts.map(([v, t]) => h("option", { value: v, selected: String(f[key]) === String(v) }, t)));
  const filtros = h("div", { class: "filters", role: "group", "aria-label": "Filtros del centro" },
    sel("region", S.regions.map((r) => [r.code, r.name]), "Todas las regiones"),
    g.sucursales.length ? sel("sucursal", g.sucursales.map((x) => [x, x]), "Todas las sucursales") : null,
    isAdmin() ? sel("operador", ops.map((o) => [o.id, o.name]), "Todos los operadores") : null,
    c ? sel("estado", Object.keys(c.expedientes.por_estado).concat(["LISTO", "ASIGNADO", "INCOMPLETO"]).filter((v, i, a) => a.indexOf(v) === i)
      .map((x) => [x, x.replace(/_/g, " ").toLowerCase()]), "Todos los estados") : null,
    h("select", { "aria-label": "Periodo", "data-filtro": "periodo", onchange: (e) => { f.periodo = e.target.value; refresh(true); } },
      Object.entries(PERIODOS).map(([v, t]) => h("option", { value: v, selected: f.periodo === v }, t))),
    Object.entries(f).some(([kk, v]) => v && !(kk === "periodo" && v === "24h")) ? h("button", { class: "btn small ghost", onclick: () => {
      S.filters.centro = null; refresh(true); } }, "Quitar filtros") : null);
  const verif = g.ultima_actualizacion_verificada;
  const kpis = [
    kpi("Conectores activos", num(Object.values(g.conectores_activos).reduce((a, b) => a + b, 0)),
      Object.entries(g.conectores_activos).map(([t, n]) => `${t}: ${n}`).join(" · ") || "ninguno"),
    attentionKpi("Fuentes con error de comunicación", g.fuentes.con_error_de_comunicacion,
      `${g.fuentes.activas}/${g.fuentes.total} activas · ${g.fuentes.en_recuperacion} en recuperación`, "monitor"),
    kpi("Última actualización verificada", verif ? ago(verif) : "nunca", verif ? fmtTime(verif) : "ninguna fuente respondió todavía"),
    kpi("Operadores conectados", num(g.operadores_conectados), "con actividad en el panel hace poco"),
    kpi("Solicitudes pendientes", num(g.solicitudes_pendientes), "en cola de asignación"),
    attentionKpi("Alertas importantes", g.alertas_importantes, "críticas o altas sin confirmar", "alertas"),
    kpi("Disponibilidades nuevas", num(k.disponibilidades_nuevas), `${num(k.disponibilidades_agotadas)} agotadas en el periodo`),
    kpi("Asignaciones activas", num(k.asignaciones_activas), "asignadas o en proceso ahora"),
    attentionKpi("Operaciones abandonadas", k.operaciones_abandonadas, "en proceso sin actividad", "solicitudes"),
    kpi("Tasa de errores", k.tasa_de_errores_pct === null ? "—" : num(k.tasa_de_errores_pct, 2) + " %",
      `${num(k.consultas_con_error)} de ${num(k.consultas)} consultas`),
    kpi("Tiempo de detección", fmtMs(k.tiempo_deteccion_ms && k.tiempo_deteccion_ms.p50), msTxt(k.tiempo_deteccion_ms)),
    kpi("Tiempo hasta asignación", fmtMs(k.tiempo_asignacion_ms && k.tiempo_asignacion_ms.p50), msTxt(k.tiempo_asignacion_ms)),
  ];
  if (c) {
    kpis.push(
      kpi("Expedientes preparados", num(c.expedientes.preparados), `${num(c.expedientes.listos)} listos · ${num(c.expedientes.pendientes)} pendientes de validar`),
      attentionKpi("Expedientes incompletos", c.expedientes.incompletos, "faltan datos: no se asignan", "captura"),
      kpi("Reservaciones confirmadas", num(c.reservaciones.confirmadas_periodo),
        Object.entries(c.reservaciones.confirmadas_por_sucursal).map(([s, n]) => `${s}: ${n}`).join(" · ") || "ninguna en el periodo"),
      attentionKpi("En verificación", c.reservaciones.en_verificacion, "resultado incierto: el motor lo comprueba", "captura"),
      kpi("Tiempo hasta confirmación", fmtMs(c.tiempo_hasta_confirmacion_ms.p50), msTxt(c.tiempo_hasta_confirmacion_ms)));
  }
  return h("section", { class: "section", id: "centro", "aria-label": "Centro de operaciones" },
    h("div", { class: "centro-head" }, h("h3", {}, `Indicadores · ${PERIODOS[d.periodo]}`),
      h("span", { class: "muted" }, `Actualizado ${fmtTime(d.generado_en)} · cifras de la base de datos del motor`)),
    filtros, h("div", { class: "grid kpis" }, kpis),
    d.alertas_importantes.length ? card("Requieren atención", alertList(d.alertas_importantes)) : null);
}

// ------------------------------------------------------------------ v1.7: explicación de la asignación
async function showExplicacion(aid) {
  try {
    const r = await api(`/api/asignaciones/${aid}/explicacion`);
    const e = r.explicacion;
    if (!e) { openDrawer(h("h1", {}, `Asignación #${aid}`), h("p", { class: "muted" }, r.nota)); return; }
    openDrawer(h("h1", {}, `¿Por qué este operador? · #${aid}`), h("p", { id: "explicacion-resumen" }, e.resumen),
      e.candidatos ? [h("h3", {}, "Candidatos (en orden)"), table([{ t: "Operador", f: (x) => x.operador }, { t: "Carga", f: (x) => x.carga },
        { t: "Asignaciones 24 h", num: true, f: (x) => x.asignaciones_24h ?? "—" }], e.candidatos)] : null,
      e.descartados && e.descartados.length ? [h("h3", {}, "Descartados"), table([{ t: "Operador", f: (x) => x.operador },
        { t: "Motivo", f: (x) => x.motivo }], e.descartados)] : null,
      h("p", { class: "muted" }, `Criterio: ${e.criterio}`));
  } catch (err) { toast("⚠ " + err.message, 4000); }
}

// ------------------------------------------------------------------ v1.7: SUPERVISIÓN
const PRESENCIA = { CONECTADO: ["b-ok", "Conectado"], CONEXION_PERDIDA: ["b-warn", "Conexión perdida"], INACTIVO: ["", "Inactivo"],
  DESCONECTADO: ["b-bad", "Desconectado"], SIN_USUARIO: ["b-info", "Sin usuario (canal externo)"] };
const presencia = (p) => h("span", { class: "badge " + (PRESENCIA[p] || ["", p])[0], "data-presencia": p }, (PRESENCIA[p] || ["", p])[1]);
async function viewSupervision() {
  const d = await api("/api/supervision");
  const p = d.parametros;
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Supervisión de operadores"),
      h("div", { class: "muted" }, `Conectado: actividad hace menos de ${fmtDur(p.operador_conexion_perdida_s)}. Conexión perdida: hasta ${fmtDur(p.operador_inactivo_s)}. ` +
        `Desconectado: cerró sesión o caducó. Reparto: ${p.reparto_modo === "equitativo_dia" ? "equitativo por día" : "menor carga"}.`))),
    card("Operadores", table([
      { t: "Operador", f: (o) => h("div", {}, h("b", {}, o.nombre), o.activo ? null : h("div", {}, badge("SUSPENDIDA"))) },
      { t: "Presencia", f: (o) => h("div", {}, presencia(o.presencia), h("div", { class: "muted" }, o.visto_hace_s === null ? "nunca en el panel" : "visto " + fmtDurTxt(o.visto_hace_s))) },
      { t: "Carga", num: true, f: (o) => `${o.carga}/${o.max}` },
      { t: "Asignadas", num: true, f: (o) => o.asignadas }, { t: "En proceso", num: true, f: (o) => o.en_proceso },
      { t: "Completadas (hoy)", num: true, f: (o) => `${o.completadas} (${o.completadas_hoy})` },
      { t: "Sin atención", num: true, f: (o) => o.sin_atencion ? h("span", { class: "badge b-warn" }, o.sin_atencion) : "0" },
      { t: "Rechazos", num: true, f: (o) => o.rechazos }, { t: "Reasign.", num: true, f: (o) => o.reasignaciones ?? "—" },
      { t: "Errores", num: true, f: (o) => o.errores ?? "—" },
      { t: "Última actividad", f: (o) => o.ultima_actividad_hace_s === null ? "—" : fmtDurTxt(o.ultima_actividad_hace_s) },
      { t: "", f: (o) => (o.asignadas || o.en_proceso) ? h("button", { class: "btn small", "data-redistribuir": o.id,
          onclick: (e) => { e.stopPropagation(); showRedistribuir(o, p); } }, "Redistribuir…") : null }], d.operadores)),
    h("div", { class: "section" }, card("Historial de acciones", table([
      { t: "Cuándo", f: (a) => fmtTime(a.ts) }, { t: "Quién", f: (a) => a.actor }, { t: "Acción", f: (a) => a.accion.replace(/_/g, " ") },
      { t: "Sobre", f: (a) => a.objetivo }, { t: "Detalle", f: (a) => h("span", { class: "mono" }, JSON.stringify(a.detalle)) }], d.acciones_recientes))));
}
const fmtDurTxt = (s) => "hace " + fmtDur(s);

function showRedistribuir(o, p) {
  const out = h("div", { role: "status" });
  const form = h("form", { class: "form", onsubmit: async (e) => {
    e.preventDefault();
    const d = formData(e.target);
    out.replaceChildren();
    try {
      const r = await api(`/api/supervision/operadores/${o.id}/redistribuir`, { method: "POST",
        body: { confirmo: !!d.confirmo, motivo: d.motivo || "", incluir_en_proceso: !!d.incluir_en_proceso, desactivar: !!d.desactivar } });
      out.replaceChildren(h("p", {}, `Liberadas: ${r.liberadas.length}. No liberadas: ${r.no_liberadas.length}.`),
        r.no_liberadas.length ? h("ul", {}, r.no_liberadas.map((x) => h("li", {}, `#${x.asignacion}: ${x.motivo}`))) : null);
      toast(`Redistribución: ${r.liberadas.length} devueltas a la cola`); refresh(true);
    } catch (err) { out.replaceChildren(h("p", { class: "error" }, err.message)); }
  } },
    h("p", {}, `Devuelve a la cola el trabajo de ${o.nombre}. Las asignaciones sin tomar se liberan. Las tomadas solo si lo marcas, `
      + `si no dio señal en ${fmtDur(p.operador_conexion_perdida_s)} y no hay una confirmación en curso.`),
    field("Motivo (queda en la auditoría)", h("input", { name: "motivo", required: true, minlength: 5, maxlength: 200, placeholder: "No responde desde las 10:00" })),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "incluir_en_proceso" }), " Incluir las tomadas (en proceso)"),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "desactivar" }), " Además, no darle trabajo nuevo hasta reactivarlo"),
    h("label", { class: "check" }, h("input", { type: "checkbox", name: "confirmo", required: true }), " Confirmé con el operador o no responde"),
    h("button", { class: "btn primary", id: "btn-redistribuir" }, "Redistribuir"), out);
  openDrawer(h("h1", {}, `Redistribuir · ${o.nombre}`), presencia(o.presencia), form);
}

// ------------------------------------------------------------------ v1.7: CENTRO DE NOTIFICACIONES
const NIVEL_BADGE = { CRITICA: "b-bad", ALTA: "b-warn", MEDIA: "b-info", BAJA: "" };
async function viewNotificaciones() {
  const f = S.filters.notif || (S.filters.notif = { categoria: "", nivel: "", region: "", sin_confirmar: false, periodo: "7d" });
  const qs = new URLSearchParams(Object.entries(f).filter(([, v]) => v).map(([k, v]) => [k, String(v)])).toString();
  const d = await api("/api/notificaciones?limit=150" + (qs ? "&" + qs : ""));
  const chips = h("div", { class: "chips", role: "group", "aria-label": "Categorías" }, d.categorias.map((c) => {
    const r = d.resumen[c.clave] || { total: 0, sin_confirmar: 0 };
    return h("button", { class: "chip" + (f.categoria === c.clave ? " active" : "") + (c.silenciada ? " muted" : ""), "data-categoria": c.clave,
      "aria-pressed": f.categoria === c.clave ? "true" : "false",
      onclick: () => { f.categoria = f.categoria === c.clave ? "" : c.clave; refresh(true); } },
      h("span", { class: "badge " + (NIVEL_BADGE[c.nivel] || "") }, c.nivel.toLowerCase()), " ", c.nombre, " ",
      h("b", {}, r.total), r.sin_confirmar ? h("span", { class: "muted" }, ` (${r.sin_confirmar} sin confirmar)`) : null,
      c.silenciada ? h("span", { class: "muted" }, " · silenciada") : null);
  }));
  const pendientes = d.items.filter((a) => !a.acknowledged_at).map((a) => a.id);
  const filtros = h("div", { class: "filters" },
    h("select", { "aria-label": "Nivel", onchange: (e) => { f.nivel = e.target.value; refresh(true); } }, h("option", { value: "" }, "Todos los niveles"),
      ["CRITICA", "ALTA", "MEDIA", "BAJA"].map((n) => h("option", { value: n, selected: f.nivel === n }, n.toLowerCase()))),
    h("select", { "aria-label": "Región", onchange: (e) => { f.region = e.target.value; refresh(true); } }, h("option", { value: "" }, "Todas las regiones"),
      S.regions.map((r) => h("option", { value: r.code, selected: f.region === r.code }, r.name))),
    h("select", { "aria-label": "Periodo", onchange: (e) => { f.periodo = e.target.value; refresh(true); } },
      Object.entries(PERIODOS).map(([v, t]) => h("option", { value: v, selected: f.periodo === v }, t))),
    h("label", { class: "check" }, h("input", { type: "checkbox", checked: f.sin_confirmar, onchange: (e) => { f.sin_confirmar = e.target.checked; refresh(true); } }), " Solo sin confirmar"),
    pendientes.length ? h("button", { class: "btn small", id: "confirmar-visibles", onclick: () => act(() => api("/api/notificaciones/confirmar",
      { method: "POST", body: { ids: pendientes.slice(0, 200) } }), (r) => `${r.confirmadas} confirmadas`) }, `Confirmar las ${Math.min(pendientes.length, 200)} visibles`) : null);
  const lista = d.items.length ? h("div", { id: "lista-notificaciones" }, d.items.map((a) => h("div", { class: "alert-item nivel-" + a.nivel.toLowerCase(), "data-alerta": a.id },
    h("span", { class: "badge " + (NIVEL_BADGE[a.nivel] || "") }, a.nivel.toLowerCase()),
    h("div", { class: "body" },
      h("div", { class: "title" }, a.title, a.repeticiones > 1 ? h("span", { class: "badge b-warn", title: "Avisos repetidos agrupados" }, ` ×${a.repeticiones}`) : null),
      h("div", { class: "meta" }, `${a.categoria_nombre} · ${a.region} · ${fmtTime(a.created_at)}`,
        a.repeticiones > 1 ? ` · última ${fmtTime(a.ultima_en)}` : "", a.operator_name ? ` · ${a.operator_name}` : "",
        " · ", entregasTxt(a))),
    h("div", { class: "actions" }, badge(a.status),
      !a.acknowledged_at ? h("button", { class: "btn small", onclick: () => act(() => api(`/api/alerts/${a.id}/confirmar`, { method: "POST" }), "Notificación confirmada") }, "Confirmar") : null,
      h("button", { class: "btn small ghost", onclick: () => showAlert(a.id) }, "Ver"))))) : h("div", { class: "empty" }, "Sin notificaciones con estos filtros");
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Centro de notificaciones"),
      h("div", { class: "muted" }, "Sin datos personales: solo códigos, región y sucursal. Las críticas no se pueden silenciar."))),
    chips, card(null, filtros, lista,
      d.siguiente ? h("p", { class: "muted" }, "Se muestran las 150 más recientes; usa los filtros para acotar.") : null),
    isAdmin() ? h("div", { class: "grid cols-2 section" }, card("Entregas por canal (periodo)", table([
      { t: "Canal", f: (c) => h("div", {}, c.canal, c.habilitado ? null : h("span", { class: "muted" }, " (deshabilitado)")) }, { t: "Tipo", f: (c) => c.tipo },
      { t: "Enviadas", num: true, f: (c) => c.entregas.ENVIADA || 0 }, { t: "Pendientes", num: true, f: (c) => (c.entregas.PENDIENTE || 0) + (c.entregas.ENVIANDO || 0) },
      { t: "Fallidas", num: true, f: (c) => c.entregas.FALLIDA ? h("span", { class: "badge b-bad" }, c.entregas.FALLIDA) : "0" },
      { t: "Latencia", f: (c) => msTxt(c.latencia_ms) }], d.canales)), silencioCard(d)) : null);
}
function entregasTxt(a) {
  if (a.status === "SILENCIADA") return "silenciada: no se envió";
  const e = a.entregas || {};
  const tot = Object.values(e).reduce((x, y) => x + y, 0);
  if (!tot) return "solo en el panel";
  return `entregas ${e.ENVIADA || 0}/${tot}` + (e.FALLIDA ? ` · ${e.FALLIDA} fallidas` : "") + ((e.PENDIENTE || 0) + (e.ENVIANDO || 0) ? " · en curso" : "");
}
function silencioCard(d) {
  const form = h("form", { class: "form", id: "form-silencio", onsubmit: (e) => {
    e.preventDefault();
    const elegidas = [...e.target.querySelectorAll("input[name=cat]:checked")].map((x) => x.value);
    act(() => api("/api/settings/engine", { method: "PUT", body: { alertas_silenciadas: elegidas } }), "Silencio actualizado");
  } },
    h("p", { class: "muted" }, "Las silenciadas se guardan en el historial pero no se envían a Telegram ni webhooks."),
    d.categorias.filter((c) => c.silenciable).map((c) => h("label", { class: "check" },
      h("input", { type: "checkbox", name: "cat", value: c.clave, checked: c.silenciada }), " ", c.nombre)),
    h("p", { class: "muted" }, `Agrupación de avisos repetidos: ${d.agrupar_s ? fmtDur(d.agrupar_s) : "desactivada"} (Configuración › Parámetros).`),
    h("button", { class: "btn primary" }, "Guardar silencio"));
  return card("Silenciar avisos no críticos", form);
}

// ------------------------------------------------------------------ arranque
(async () => {
  if (S.token) { try { await start(); return; } catch (_) { /* token inválido */ } }
  logout();
  $("login-error").textContent = "";
})();
