/* v1.5 · Captura y reservaciones de LABORATORIO (portal de prueba propio, datos ficticios).
 * Usa las utilidades de app.js (h, api, act, badge, table, card, kpi, openDrawer...).
 * Todo el contenido dinámico se inserta como texto (nunca innerHTML con datos). */
"use strict";

Object.assign(BADGE, {
  PREPARADA: "b-info", DATOS_ENVIADOS: "b-info", RETENIDA: "b-warn", CONFIRMANDO: "b-warn",
  RESERVANDO: "b-warn", EN_CAPTURA: "b-info", CONFIRMADO: "b-ok", FALLIDO: "b-bad",
  pendiente: "", en_proceso: "b-info", confirmado: "b-ok", fallido: "b-bad",
});
const GRUPOS = { pendiente: "Pendientes", en_proceso: "En proceso", confirmado: "Confirmados", fallido: "Fallidos o cancelados" };
const TRAMITES = { alta: "Alta de registro (ficticio)", renovacion: "Renovación (ficticia)", constancia: "Constancia (ficticia)" };
const TABS = {
  expedientes: "Expedientes", preparacion: "Preparación de formularios", calendario: "Calendario de laboratorio",
  reservaciones: "Reservaciones", confirmaciones: "Confirmaciones", historial: "Historial",
};

async function viewCaptura() {
  const f = S.filters.captura || (S.filters.captura = { tab: "expedientes", grupo: "", region: "" });
  const tabs = h("div", { class: "tabs", role: "tablist" }, Object.entries(TABS).map(([k, t]) =>
    h("button", { class: "btn small" + (f.tab === k ? " primary" : ""), role: "tab", "aria-selected": f.tab === k ? "true" : "false",
      "data-tab": k, onclick: () => { f.tab = k; refresh(true); } }, t)));
  const body = await ({ expedientes: tabExpedientes, preparacion: tabPreparacion, calendario: tabCalendario,
    reservaciones: tabReservaciones, confirmaciones: tabConfirmaciones, historial: tabHistorial }[f.tab] || tabExpedientes)(f);
  return h("div", {},
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Captura y reservaciones"),
      h("div", { class: "muted" }, "Laboratorio: portal de prueba propio y datos ficticios. Cada reservación la confirma una persona."))),
    tabs, body);
}

// ------------------------------------------------------------------ Expedientes
async function tabExpedientes(f) {
  const [res, list] = await Promise.all([api("/api/captura/resumen"),
    api("/api/expedientes" + (f.grupo ? "?grupo=" + f.grupo : ""))]);
  const g = res.expedientes.por_grupo;
  const kpiG = (k) => {
    const el = kpi(GRUPOS[k], num(g[k] || 0), f.grupo === k ? "Filtrando · pulsa para ver todos" : "Pulsa para filtrar");
    el.classList.add("clickable"); el.setAttribute("data-grupo", k);
    if (f.grupo === k) el.classList.add("attention");
    el.addEventListener("click", () => { f.grupo = f.grupo === k ? "" : k; refresh(true); });
    return el;
  };
  return h("div", {},
    h("div", { class: "grid kpis" }, Object.keys(GRUPOS).map(kpiG)),
    h("div", { class: "grid cols-2 section" },
      card("Expedientes" + (f.grupo ? ` · ${GRUPOS[f.grupo]}` : ""), table([
        { t: "Código", f: (e) => h("b", {}, e.codigo) },
        { t: "Titular", f: (e) => h("span", { class: "mono" }, e.nombre) },
        { t: "Trámite", f: (e) => TRAMITES[e.tipo_tramite] || e.tipo_tramite },
        { t: "Región", f: (e) => e.region + (e.sucursal ? " · " + e.sucursal : "") },
        { t: "Prio.", num: true, f: (e) => e.prioridad },
        { t: "Estado", f: (e) => badge(e.estado) },
        { t: "Operador", f: (e) => e.operador || "—" },
        { t: "Actualizado", f: (e) => ago(e.actualizado) }], list, (e) => showExpediente(e.id))),
      card("Nuevo expediente", formExpediente())));
}

function formExpediente() {
  const errs = h("div", { class: "error full", role: "alert" });
  const inp = (name, attrs) => h("input", { name, autocomplete: "off", ...attrs });
  const form = h("form", { class: "form", id: "form-expediente", onsubmit: async (e) => {
    e.preventDefault(); errs.textContent = "";
    const d = formData(e.target);
    if (d.prioridad) d.prioridad = Number(d.prioridad);
    try {
      const r = await api("/api/expedientes", { method: "POST", body: d });
      toast(`Expediente ${r.codigo} creado: el motor lo asignará cuando haya disponibilidad`);
      e.target.reset(); S.dirty = false; refresh(true);
    } catch (err) {
      const det = err.detalle && err.detalle.errores;
      errs.replaceChildren(err.message, det ? h("ul", {}, Object.entries(det).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
    }
  } },
    field("Nombre completo (ficticio)", inp("nombre", { required: true, maxlength: 128, placeholder: "Ana Ficticia Ruiz" }), "full"),
    field("Fecha de nacimiento", inp("fecha_nacimiento", { type: "date", required: true })),
    field("Identificador de registro previo", inp("id_registro_previo", { maxlength: 32, placeholder: "REG000001" })),
    field("Teléfono (ficticio, 555…)", inp("telefono", { required: true, maxlength: 20, inputmode: "tel", placeholder: "5550000001" })),
    field("Confirma el teléfono", inp("telefono_confirmacion", { required: true, maxlength: 20, inputmode: "tel" })),
    field("Correo (ficticio, @ejemplo.test)", inp("correo", { type: "email", required: true, maxlength: 128 })),
    field("Confirma el correo", inp("correo_confirmacion", { type: "email", required: true, maxlength: 128 })),
    field("Región", regionSelect("region")),
    field("Sucursal (opcional)", inp("sucursal", { maxlength: 32, placeholder: "NOR-1" })),
    field("Tipo de trámite", h("select", { name: "tipo_tramite" }, Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k }, v)))),
    field("Operación", h("select", { name: "operacion" }, h("option", { value: "nueva" }, "Nueva cita"), h("option", { value: "reprogramar" }, "Reprogramar"))),
    field("Prioridad (1 = máxima)", h("select", { name: "prioridad" }, [1, 2, 3, 4, 5].map((p) => h("option", { value: p, selected: p === 3 }, p)))),
    field("Identificador externo (opcional)", inp("id_externo", { maxlength: 64 })),
    h("button", { class: "btn primary" }, "Crear expediente"), errs);
  return form;
}

// ------------------------------------------------------------------ Ficha y flujo del operador
async function showExpediente(id, paso) {
  const e = await api(`/api/expedientes/${id}`);
  const viva = (e.reservaciones || []).find((r) => ["PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "CONFIRMANDO"].includes(r.estado));
  const conf = (e.reservaciones || []).find((r) => r.estado === "CONFIRMADA");
  const flujo = h("div", { class: "card section", id: "flujo" });
  openDrawer(
    h("h1", {}, e.codigo), h("p", {}, badge(e.estado), " ", e.operador ? `Operador: ${e.operador}` : "Sin operador todavía"),
    h("dl", { class: "kv" },
      h("dt", {}, "Titular"), h("dd", {}, e.nombre), h("dt", {}, "Nacimiento"), h("dd", {}, e.fecha_nacimiento),
      h("dt", {}, "Teléfono"), h("dd", {}, e.telefono), h("dt", {}, "Correo"), h("dd", {}, e.correo),
      h("dt", {}, "Registro previo"), h("dd", {}, e.id_registro_previo || "—"),
      h("dt", {}, "Trámite"), h("dd", {}, `${TRAMITES[e.tipo_tramite] || e.tipo_tramite} · ${e.operacion}`),
      h("dt", {}, "Región"), h("dd", {}, e.region + (e.sucursal ? " · sucursal " + e.sucursal : "")),
      h("dt", {}, "Prioridad"), h("dd", {}, e.prioridad),
      e.ultimo_error ? [h("dt", {}, "Último aviso"), h("dd", { class: "error" }, e.ultimo_error)] : null),
    h("p", { class: "muted" }, "Datos personales visibles solo para el operador asignado y administradores; esta consulta queda registrada."),
    flujo,
    h("h3", {}, "Historial"),
    h("ul", { class: "timeline" }, e.historial.slice().reverse().map((x) => h("li", {},
      h("b", {}, x.tipo.replace(/_/g, " ")), h("span", { class: "muted" }, ` · ${fmtTime(x.ts)} · ${x.actor}`),
      x.asignacion ? h("span", { class: "muted" }, ` · asignación #${x.asignacion}`) : null,
      x.detalle && Object.keys(x.detalle).length ? h("div", { class: "muted mono" }, JSON.stringify(x.detalle)) : null))));
  await pintarFlujo(flujo, e, viva, conf, paso);
}

function paso(n, titulo, ...cuerpo) {
  return h("div", { class: "section" }, h("h2", {}, `${n}. ${titulo}`), ...cuerpo);
}

async function pintarFlujo(box, e, viva, conf, pasoPrevio) {
  const reabrir = (p) => showExpediente(e.id, p);
  const cancelarExp = () => h("button", { class: "btn small danger", onclick: async () => {
    await act(() => api(`/api/expedientes/${e.id}/cancelar`, { method: "POST" }), "Expediente cancelado"); reabrir(); } }, "Cancelar expediente");
  if (conf) {
    box.replaceChildren(paso(4, "Reservación confirmada",
      h("p", {}, "Folio: ", h("b", { id: "folio", "data-folio": conf.folio }, conf.folio)),
      h("dl", { class: "kv" }, h("dt", {}, "Sucursal"), h("dd", {}, conf.sucursal), h("dt", {}, "Fecha"), h("dd", {}, conf.fecha),
        h("dt", {}, "Horario"), h("dd", {}, conf.hora), h("dt", {}, "Confirmada por"), h("dd", {}, conf.confirmada_por || "—"),
        h("dt", {}, "Evidencia"), h("dd", { class: "mono" }, `${conf.evidencia.fuente} · ${conf.evidencia.estado_portal} · verificada ${fmtTime(conf.evidencia.verificada_en)}`)),
      h("div", { class: "actions" }, h("button", { class: "btn danger", onclick: async () => {
        if (!confirm(`¿Cancelar la reservación ${conf.folio} en el portal de prueba?`)) return;
        await act(() => api(`/api/reservaciones/${conf.id}/cancelar`, { method: "POST" }), "Reservación cancelada"); reabrir(); } }, "Cancelar reservación"))));
    return;
  }
  if (["PENDIENTE"].includes(e.estado)) {
    box.replaceChildren(h("p", {}, "Esperando disponibilidad: el motor asignará este expediente a un operador cuando el portal publique horarios."),
      h("div", { class: "actions" }, cancelarExp()));
    return;
  }
  if (["CANCELADO", "FALLIDO"].includes(e.estado)) { box.replaceChildren(h("p", { class: "muted" }, `Expediente ${e.estado.toLowerCase()}.`)); return; }
  // 1) preparar (también retoma un intento abierto)
  if (!viva || viva.estado === "PREPARADA") {
    const btn = h("button", { class: "btn primary", id: "btn-preparar" }, "Preparar formulario");
    box.replaceChildren(paso(1, "Preparación del formulario", h("p", { class: "muted" },
      "El motor lee el expediente y llena los campos del portal según el contrato. Revísalos antes de enviarlos."), btn),
      h("div", { class: "actions section" }, cancelarExp()));
    btn.addEventListener("click", async () => {
      try { const r = await api(`/api/expedientes/${e.id}/preparar`, { method: "POST" }); mostrarRevision(box, e, r); }
      catch (err) { toast("⚠ " + err.message, 5000); }
    });
    if (pasoPrevio === "preparar") btn.click();
    return;
  }
  if (viva.estado === "DATOS_ENVIADOS") {
    box.replaceChildren(paso(2, "Calendario", h("p", { class: "muted" }, "Cargando horarios del portal…")));
    try {
      const r = await api(`/api/reservaciones/${viva.id}/calendario`);
      mostrarCalendario(box, e, viva, r.calendario);
    } catch (err) {
      box.replaceChildren(paso(2, "Calendario", h("p", { class: "error" }, err.message),
        h("button", { class: "btn", onclick: () => reabrir("preparar") }, "Volver a preparar y enviar datos")));
    }
    return;
  }
  if (viva.estado === "RETENIDA") { mostrarConfirmacion(box, e, viva); return; }
  if (viva.estado === "CONFIRMANDO") {
    box.replaceChildren(paso(3, "Confirmación en verificación",
      h("p", {}, "Se envió la confirmación pero no se pudo comprobar el resultado con el portal. El motor lo verifica solo con la clave de confirmación."),
      h("p", { class: "muted" }, "No repitas la reservación por otro medio. Recarga en unos segundos."),
      h("button", { class: "btn", onclick: () => reabrir() }, "Recargar")));
  }
}

function mostrarRevision(box, e, r) {
  const rev = r.revision;
  const chk = h("input", { type: "checkbox", id: "chk-revisado" });
  const enviar = h("button", { class: "btn primary", id: "btn-enviar", disabled: true }, "Enviar datos al portal");
  chk.addEventListener("change", () => { enviar.disabled = !chk.checked || !rev.listo; });
  const errs = h("div", { class: "error", role: "alert" });
  enviar.addEventListener("click", async () => {
    enviar.disabled = true; errs.textContent = "";
    try {
      const out = await api(`/api/reservaciones/${r.reservacion.id}/enviar-datos`, { method: "POST", body: { revisado: true } });
      mostrarCalendario(box, e, out.reservacion, out.calendario);
    } catch (err) {
      const d = err.detalle && err.detalle.errores;
      errs.replaceChildren(err.message, d ? h("ul", {}, Object.entries(d).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
      enviar.disabled = false;
    }
  });
  box.replaceChildren(paso(1, "Revisión de campos preparados",
    h("p", { class: "muted" }, `Contrato ${rev.contrato} v${rev.version}. Los datos personales se muestran enmascarados.`),
    table([{ t: "Campo del portal", f: (c) => c.etiqueta }, { t: "Valor", f: (c) => h("span", { class: "mono" }, c.valor ?? "—") },
      { t: "Origen", f: (c) => c.origen }, { t: "", f: (c) => badge(c.listo ? "OK" : "ERROR") }], rev.campos),
    rev.faltan.length ? h("p", { class: "error" }, "Faltan datos obligatorios: " + rev.faltan.join(", ") + ". Corrige el expediente antes de enviar.") : null,
    h("label", { class: "check-line" }, chk, "He revisado los campos y son correctos"),
    h("div", { class: "actions" }, enviar), errs));
}

function mostrarCalendario(box, e, res, cal, aviso) {
  const porFecha = {};
  for (const x of cal.horarios) (porFecha[x.fecha] = porFecha[x.fecha] || []).push(x);
  const errs = h("div", { class: "error", role: "alert" }, aviso || "");
  const elegir = async (hid) => {
    errs.textContent = "";
    try {
      const out = await api(`/api/reservaciones/${res.id}/seleccionar`, { method: "POST", body: { horario: hid } });
      mostrarConfirmacion(box, e, out.reservacion, out.resumen);
    } catch (err) {
      if (err.detalle && err.detalle.calendario) { mostrarCalendario(box, e, res, err.detalle.calendario, err.message); return; }
      errs.textContent = err.message;
    }
  };
  box.replaceChildren(paso(2, "Calendario de horarios",
    h("p", { class: "muted" }, `Horarios libres del portal (${cal.horarios.length}). Elegir uno lo aparta unos minutos.`),
    errs,
    Object.keys(porFecha).length ? h("div", { class: "table-wrap" }, h("table", {}, h("tbody", {}, Object.keys(porFecha).sort().map((fe) =>
      h("tr", { "data-fecha": fe }, h("th", {}, fe), h("td", {}, h("div", { class: "actions" }, porFecha[fe].map((x) =>
        h("button", { class: "btn small", "data-horario": x.id, onclick: () => elegir(x.id) }, `${x.hora} · ${x.sucursal}`))))))))) :
      h("div", { class: "empty" }, "No hay horarios libres para este trámite ahora.")));
}

function mostrarConfirmacion(box, e, res, resumen) {
  const chk = h("input", { type: "checkbox", id: "chk-confirmo" });
  const btn = h("button", { class: "btn primary", id: "btn-confirmar", disabled: true }, "Confirmar reservación");
  chk.addEventListener("change", () => { btn.disabled = !chk.checked; });
  const errs = h("div", { class: "error", role: "alert" });
  btn.addEventListener("click", async () => {
    btn.disabled = true; errs.textContent = "";
    try {
      const out = await api(`/api/reservaciones/${res.id}/confirmar`, { method: "POST", body: { confirmo: true }, withStatus: true });
      toast(out.status === 200 ? `Reservación confirmada: folio ${out.data.reservacion.folio}` : "Confirmación enviada; el motor está verificando el resultado", 5000);
      showExpediente(e.id);
    } catch (err) { errs.textContent = err.message; showExpediente(e.id); }
  });
  box.replaceChildren(paso(3, "Confirmación",
    h("dl", { class: "kv" }, h("dt", {}, "Sucursal"), h("dd", {}, (resumen && resumen.sucursal) || res.sucursal),
      h("dt", {}, "Fecha"), h("dd", {}, res.fecha), h("dt", {}, "Horario"), h("dd", {}, res.hora),
      h("dt", {}, "Apartado hasta"), h("dd", {}, fmtTime(res.vence_en))),
    h("label", { class: "check-line" }, chk, "Confirmo esta reservación con los datos del expediente"),
    h("div", { class: "actions" }, btn,
      h("button", { class: "btn ghost", onclick: async () => {
        try { const r = await api(`/api/reservaciones/${res.id}/calendario`); mostrarCalendario(box, e, res, r.calendario); }
        catch (err) { errs.textContent = err.message; } } }, "Elegir otro horario"),
      h("button", { class: "btn ghost danger", onclick: async () => {
        await act(() => api(`/api/reservaciones/${res.id}/cancelar`, { method: "POST" }), "Intento descartado"); showExpediente(e.id); } }, "Descartar")),
    errs));
}

// ------------------------------------------------------------------ Otras pestañas
async function tabPreparacion() {
  const [k, res] = await Promise.all([api("/api/captura/contrato"), api("/api/reservaciones")]);
  const abiertas = res.filter((r) => ["PREPARADA", "DATOS_ENVIADOS"].includes(r.estado));
  return h("div", { class: "grid cols-2" },
    card("Formularios en preparación", table([{ t: "Expediente", f: (r) => h("b", {}, r.expediente) }, { t: "Estado", f: (r) => badge(r.estado) },
      { t: "Aviso", f: (r) => r.error || "" }, { t: "Actualizado", f: (r) => ago(r.actualizada) }], abiertas, (r) => showExpediente(r.expediente_id))),
    card(`Contrato de campos · ${k.nombre} v${k.version}`, h("p", { class: "muted" },
      "Lo que el motor llena en la pantalla de datos del portal. Si el portal cambia un campo, el motor se detiene y pide revisión."),
      table([{ t: "Campo", f: (c) => h("span", { class: "mono" }, c.campo) }, { t: "Etiqueta", f: (c) => c.etiqueta },
        { t: "Tipo", f: (c) => c.tipo }, { t: "Del expediente", f: (c) => c.origen }, { t: "Obligatorio", f: (c) => c.obligatorio ? "Sí" : "No" }], k.campos)));
}

async function tabCalendario(f) {
  let cal, err = null;
  try { cal = await api("/api/captura/calendario" + (f.region ? "?region=" + f.region : "")); } catch (e) { err = e.message; }
  const filtro = h("div", { class: "filters" }, h("select", { onchange: (e) => { f.region = e.target.value; refresh(true); } },
    h("option", { value: "" }, "Todas las regiones"), S.regions.map((r) => h("option", { value: r.code, selected: f.region === r.code }, r.name))));
  if (err) return card("Calendario de laboratorio", filtro, h("p", { class: "error" }, err),
    h("p", { class: "muted" }, "¿Está en marcha el portal de prueba? (iniciar_portal.bat)"));
  return h("div", {}, card("Calendario de laboratorio", filtro,
    h("p", { class: "muted" }, `${num(cal.total_libres)} horarios libres de ${num(cal.total_horarios)} · solo lectura (no aparta nada)`),
    h("p", {}, badge("DISPONIBLE"), " libre   ", badge("OCUPADO"), " ocupado o apartado")),
    cal.sucursales.map((s) => card(`${s.nombre} (${s.sucursal}) · ${s.libres} libres`, h("div", { class: "table-wrap" }, h("table", {},
      h("tbody", {}, s.dias.map((d) => h("tr", {}, h("th", {}, d.fecha), h("td", {}, h("div", { class: "actions" },
        d.horarios.map((x) => badge(x.libres > 0 ? "DISPONIBLE" : "OCUPADO")).map((b, i) => { b.textContent = d.horarios[i].hora; return b; })))))))))));
}

async function tabReservaciones() {
  const res = await api("/api/reservaciones");
  return card("Reservaciones", table([
    { t: "#", num: true, f: (r) => r.id }, { t: "Expediente", f: (r) => h("b", {}, r.expediente) }, { t: "Estado", f: (r) => badge(r.estado) },
    { t: "Sucursal", f: (r) => r.sucursal || "—" }, { t: "Fecha", f: (r) => r.fecha ? `${r.fecha} ${r.hora}` : "—" },
    { t: "Folio", f: (r) => r.folio ? h("span", { class: "mono" }, r.folio) : "—" }, { t: "Intentos", num: true, f: (r) => r.intentos_confirmacion },
    { t: "Aviso", f: (r) => r.error || "" }, { t: "Actualizada", f: (r) => ago(r.actualizada) }], res, (r) => showExpediente(r.expediente_id)));
}

async function tabConfirmaciones() {
  const res = await api("/api/reservaciones");
  const pend = res.filter((r) => ["RETENIDA", "CONFIRMANDO"].includes(r.estado));
  const hechas = res.filter((r) => r.estado === "CONFIRMADA");
  return h("div", { class: "grid cols-2" },
    card("Por confirmar o en verificación", table([{ t: "Expediente", f: (r) => h("b", {}, r.expediente) }, { t: "Estado", f: (r) => badge(r.estado) },
      { t: "Horario", f: (r) => `${r.sucursal} · ${r.fecha} ${r.hora}` }, { t: "Apartado hasta", f: (r) => fmtTime(r.vence_en) }], pend, (r) => showExpediente(r.expediente_id))),
    card("Confirmadas (con evidencia del portal)", table([{ t: "Folio", f: (r) => h("span", { class: "mono" }, r.folio) },
      { t: "Expediente", f: (r) => r.expediente }, { t: "Horario", f: (r) => `${r.sucursal} · ${r.fecha} ${r.hora}` },
      { t: "Evidencia", f: (r) => r.evidencia.fuente || "—" }, { t: "Por", f: (r) => r.confirmada_por || "—" }], hechas, (r) => showExpediente(r.expediente_id))));
}

async function tabHistorial() {
  const ev = await api("/api/captura/historial?limit=300");
  return card("Historial de expedientes", h("p", { class: "muted" }, "Eventos normalizados del motor y cambios de cada expediente (sin datos personales)."),
    table([{ t: "Cuándo", f: (e) => fmtTime(e.ts) }, { t: "Expediente", f: (e) => h("b", {}, e.expediente) },
      { t: "Evento", f: (e) => e.normalizado ? badge(e.tipo) : h("span", { class: "muted" }, e.tipo.replace(/_/g, " ")) },
      { t: "Actor", f: (e) => e.actor }, { t: "Asignación", f: (e) => e.asignacion ? "#" + e.asignacion : "—" },
      { t: "Detalle", f: (e) => h("span", { class: "mono" }, JSON.stringify(e.detalle)) }], ev, (e) => showExpediente(e.expediente_id)));
}
