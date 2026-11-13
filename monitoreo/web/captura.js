/* v1.5 · Captura y reservaciones de LABORATORIO (portal de prueba propio, datos ficticios).
 * v1.6 · Modalidad POR DÍA: resumen, calendario mensual por sucursal con la hora real de la última
 *        verificación, recorrido visual del expediente, campos de captura manual y rechazo con motivo.
 * Usa las utilidades de app.js (h, api, act, badge, table, card, kpi, openDrawer...).
 * Todo el contenido dinámico se inserta como texto (nunca innerHTML con datos). */
"use strict";

Object.assign(BADGE, {
  PREPARADA: "b-info", DATOS_ENVIADOS: "b-info", RETENIDA: "b-warn", CONFIRMANDO: "b-warn", FECHA_ELEGIDA: "b-warn",
  EN_VERIFICACION: "b-warn", RESERVANDO: "b-warn", EN_CAPTURA: "b-info", CONFIRMADO: "b-ok", FALLIDO: "b-bad",
  NUEVO: "", INCOMPLETO: "b-warn", EN_VALIDACION: "b-info", LISTO: "b-info", CAPTURA_EN_PROCESO: "b-info",
  RESERVACION_PENDIENTE: "b-warn", RESERVACION_CONFIRMADA: "b-ok", REQUIERE_REVISION: "b-bad", CERRADO: "",
  DESHABILITADO: "", NO_HABILITADO: "", PENDIENTE_ACTUALIZACION: "b-warn", DESCONOCIDO: "b-bad",
  preparacion: "", pendiente: "", en_proceso: "b-info", confirmado: "b-ok", revision: "b-bad", cerrado: "",
});
const GRUPOS = { preparacion: "En preparación", pendiente: "Listos o asignados", en_proceso: "En captura",
  confirmado: "Confirmados", revision: "Requieren revisión", cerrado: "Cerrados" };
const ESTADO_DIA = { DISPONIBLE: "Disponible", AGOTADO: "Agotado", DESHABILITADO: "No habilitado",
  NO_HABILITADO: "No habilitado", PENDIENTE_ACTUALIZACION: "Pendiente de actualización",
  DESCONOCIDO: "Desconocido (sin comunicación)" };
const TRAMITES = { alta: "Alta de registro (ficticio)", renovacion: "Renovación (ficticia)", constancia: "Constancia (ficticia)" };
const TABS = {
  trabajo: "Mi trabajo", resumen: "Resumen", expedientes: "Expedientes", dias: "Calendario por día", preparacion: "Preparación de formularios",
  calendario: "Calendario por horario", reservaciones: "Reservaciones", confirmaciones: "Confirmaciones", historial: "Historial",
};
const RECORRIDO = [["Preparación", ["NUEVO", "INCOMPLETO", "EN_VALIDACION"]], ["Listo", ["LISTO"]], ["Asignado", ["ASIGNADO"]],
  ["Captura", ["CAPTURA_EN_PROCESO"]], ["Fecha elegida", ["RESERVACION_PENDIENTE"]], ["Folio", ["RESERVACION_CONFIRMADA"]]];
const MOTIVOS = { NO_DISPONIBLE: "No puedo atenderlo ahora", CARGA_EXCESIVA: "Tengo demasiados casos",
  CONFLICTO_DE_INTERES: "Conflicto de interés", DATOS_DUDOSOS: "Los datos parecen incorrectos", OTRO: "Otro motivo" };
const hace = (seg) => seg === null || seg === undefined ? "nunca" : seg < 60 ? `hace ${Math.round(seg)} s` :
  seg < 3600 ? `hace ${Math.round(seg / 60)} min` : `hace ${Math.round(seg / 3600)} h`;

async function viewCaptura() {
  // v1.7: el operador entra directo a «Mi trabajo» (antes: Resumen y luego la pestaña Expedientes)
  const f = S.filters.captura || (S.filters.captura = { tab: isAdmin() ? "resumen" : "trabajo", grupo: "", region: "" });
  const tabs = h("div", { class: "tabs", role: "tablist" }, Object.entries(TABS).map(([k, t]) =>
    h("button", { class: "btn small" + (f.tab === k ? " primary" : ""), role: "tab", "aria-selected": f.tab === k ? "true" : "false",
      "data-tab": k, onclick: () => { f.tab = k; refresh(true); } }, t)));
  const body = await ({ trabajo: tabTrabajo, resumen: tabResumen, dias: tabDias, expedientes: tabExpedientes, preparacion: tabPreparacion, calendario: tabCalendario,
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
        { t: "Modo", f: (e) => e.modo === "POR_DIA" ? "Por día" : "Por horario" },
        { t: "Prio.", num: true, f: (e) => e.prioridad },
        { t: "Estado", f: (e) => h("span", {}, badge(e.estado), e.faltan && e.faltan.length ?
          h("div", { class: "muted" }, "Falta: " + e.faltan.join(", ")) : null) },
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
    if (d.sucursales_alternativas) d.sucursales_alternativas = d.sucursales_alternativas.split(/[,;\s]+/).filter(Boolean);
    try {
      const r = await api("/api/expedientes", { method: "POST", body: d });
      toast(r.estado === "LISTO" ? `Expediente ${r.codigo} LISTO: el motor lo asignará cuando haya disponibilidad`
        : `Expediente ${r.codigo} ${r.estado.replace(/_/g, " ").toLowerCase()}` + (r.faltan && r.faltan.length ? ` · falta: ${r.faltan.join(", ")}` : ""), 5000);
      e.target.reset(); S.dirty = false; refresh(true);
    } catch (err) {
      const det = err.detalle && err.detalle.errores;
      errs.replaceChildren(err.message, det ? h("ul", {}, Object.entries(det).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
    }
  } },
    h("p", { class: "muted full" }, "Obligatorios: nombre, región y trámite. Lo demás puede completarse después: mientras falte algo el expediente queda INCOMPLETO y no se asigna."),
    field("Nombre completo (ficticio)", inp("nombre", { required: true, maxlength: 128, placeholder: "Ana Ficticia Ruiz" }), "full"),
    field("Fecha de nacimiento", inp("fecha_nacimiento", { type: "date" })),
    field("Identificador de registro previo", inp("id_registro_previo", { maxlength: 32, placeholder: "REG000001" })),
    field("Teléfono (ficticio, 555…)", inp("telefono", { maxlength: 20, inputmode: "tel", placeholder: "5550000001" })),
    field("Confirma el teléfono", inp("telefono_confirmacion", { maxlength: 20, inputmode: "tel" })),
    field("Correo (ficticio, @ejemplo.test)", inp("correo", { type: "email", maxlength: 128 })),
    field("Confirma el correo", inp("correo_confirmacion", { type: "email", maxlength: 128 })),
    field("Región", regionSelect("region")),
    field("Modalidad", h("select", { name: "modo" }, h("option", { value: "POR_DIA" }, "Por día (sin hora)"), h("option", { value: "POR_HORARIO" }, "Por horario"))),
    field("Sucursal preferida (opcional)", inp("sucursal", { maxlength: 32, placeholder: "NOR-1" })),
    field("Sucursales alternativas (opcional)", inp("sucursales_alternativas", { maxlength: 170, placeholder: "NOR-2, NOR-3" })),
    field("Folio de la cita anterior (al reprogramar)", inp("folio_anterior", { maxlength: 32 })),
    field("Tipo de trámite", h("select", { name: "tipo_tramite" }, Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k }, v)))),
    field("Operación", h("select", { name: "operacion" }, h("option", { value: "nueva" }, "Nueva cita"), h("option", { value: "reprogramar" }, "Reprogramar"))),
    field("Prioridad (1 = máxima)", h("select", { name: "prioridad" }, [1, 2, 3, 4, 5].map((p) => h("option", { value: p, selected: p === 3 }, p)))),
    field("Identificador externo (opcional)", inp("id_externo", { maxlength: 64 })),
    h("button", { class: "btn primary" }, "Crear expediente"), errs);
  return form;
}

// ------------------------------------------------------------------ Ficha y flujo del operador
function recorrido(e) {
  const i = RECORRIDO.findIndex(([, ests]) => ests.includes(e.estado));
  const fuera = i < 0;
  return h("ol", { class: "recorrido", "aria-label": "Recorrido del expediente" }, RECORRIDO.map(([t], k) =>
    h("li", { class: fuera ? "" : k < i ? "hecho" : k === i ? "actual" : "", "aria-current": k === i ? "step" : null }, t)),
    fuera ? h("li", { class: "actual aviso" }, e.estado.replace(/_/g, " ")) : null);
}

async function showExpediente(id, paso) {
  const e = await api(`/api/expedientes/${id}`);
  const viva = (e.reservaciones || []).find((r) => ["PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "FECHA_ELEGIDA",
    "CONFIRMANDO", "EN_VERIFICACION"].includes(r.estado));
  const conf = (e.reservaciones || []).find((r) => r.estado === "CONFIRMADA");
  const flujo = h("div", { class: "card section", id: "flujo" });
  openDrawer(
    h("h1", {}, e.codigo), recorrido(e),
    h("p", {}, badge(e.estado), " ", e.operador ? `Operador: ${e.operador}` : "Sin operador todavía",
      " · ", e.modo === "POR_DIA" ? "reservación por día" : "reservación por horario"),
    h("dl", { class: "kv" },
      h("dt", {}, "Titular"), h("dd", {}, e.nombre), h("dt", {}, "Nacimiento"), h("dd", {}, e.fecha_nacimiento || "— (falta)"),
      h("dt", {}, "Teléfono"), h("dd", {}, e.telefono || "— (falta)"), h("dt", {}, "Correo"), h("dd", {}, e.correo || "— (falta)"),
      h("dt", {}, "Registro previo"), h("dd", {}, e.id_registro_previo || "—"),
      h("dt", {}, "Trámite"), h("dd", {}, `${TRAMITES[e.tipo_tramite] || e.tipo_tramite} · ${e.operacion}`),
      h("dt", {}, "Región"), h("dd", {}, e.region + (e.sucursal ? " · sucursal " + e.sucursal : "")),
      e.sucursales_alternativas && e.sucursales_alternativas.length ? [h("dt", {}, "Alternativas"), h("dd", {}, e.sucursales_alternativas.join(", "))] : null,
      e.folio_anterior ? [h("dt", {}, "Folio anterior"), h("dd", {}, e.folio_anterior)] : null,
      h("dt", {}, "Prioridad"), h("dd", {}, e.prioridad),
      e.faltan && e.faltan.length ? [h("dt", {}, "Falta"), h("dd", { class: "error" }, e.faltan.join(", "))] : null,
      e.ultimo_error ? [h("dt", {}, "Último aviso"), h("dd", { class: "error" }, e.ultimo_error)] : null),
    h("p", { class: "muted" }, "Datos personales visibles solo para el operador asignado y administradores; esta consulta queda registrada."),
    flujo,
    h("h3", {}, "Historial"),
    h("ul", { class: "timeline" }, e.historial.slice().reverse().map((x) => h("li", {},
      h("b", {}, x.tipo === "ESTADO" ? `${x.detalle.antes} → ${x.detalle.despues}`.replace(/_/g, " ") : x.tipo.replace(/_/g, " ")),
      h("span", { class: "muted" }, ` · ${fmtTime(x.ts)} · ${x.actor}`),
      x.asignacion ? h("span", { class: "muted" }, ` · asignación #${x.asignacion}`) : null,
      x.detalle && Object.keys(x.detalle).length && x.tipo !== "ESTADO" ? h("div", { class: "muted mono" }, JSON.stringify(x.detalle)) : null))));
  await pintarFlujo(flujo, e, viva, conf, paso);
}

function paso(n, titulo, ...cuerpo) {
  return h("div", { class: "section" }, h("h2", {}, `${n}. ${titulo}`), ...cuerpo);
}

function rechazoForm(e, reabrir) {
  const sel = h("select", { id: "motivo-rechazo" }, Object.entries(MOTIVOS).map(([k, v]) => h("option", { value: k }, v)));
  const nota = h("input", { id: "nota-rechazo", maxlength: 200, placeholder: "Nota (sin datos personales)", autocomplete: "off" });
  const errs = h("div", { class: "error", role: "alert" });
  const btn = h("button", { class: "btn small", id: "btn-rechazar" }, "Rechazar asignación");
  btn.addEventListener("click", async () => {
    errs.textContent = "";
    try {
      const r = await api(`/api/expedientes/${e.id}/rechazar`, { method: "POST", body: { motivo: sel.value, nota: nota.value } });
      toast(r.estado === "LISTO" ? "Rechazada: vuelve a la cola para otro operador" : `Rechazada: el expediente queda ${r.estado.replace(/_/g, " ")}`, 5000);
      reabrir();
    } catch (err) { errs.textContent = err.message; }
  });
  return h("details", { class: "section" }, h("summary", {}, "No puedo atender este expediente"),
    h("div", { class: "form" }, field("Motivo", sel), field("Nota", nota), btn, errs));
}

async function pintarFlujo(box, e, viva, conf, pasoPrevio) {
  const reabrir = (p) => showExpediente(e.id, p);
  const cancelarExp = () => h("button", { class: "btn small danger", onclick: async () => {
    await act(() => api(`/api/expedientes/${e.id}/cancelar`, { method: "POST" }), "Expediente cancelado"); reabrir(); } }, "Cancelar expediente");
  const revalidar = () => h("button", { class: "btn small", id: "btn-revalidar", onclick: async () => {
    await act(() => api(`/api/expedientes/${e.id}/revalidar`, { method: "POST" }), (r) => `Expediente ${r.estado.replace(/_/g, " ")}`); reabrir(); } }, "Volver a validar");
  if (conf) {
    box.replaceChildren(paso(4, "Reservación confirmada",
      h("p", {}, "Folio: ", h("b", { id: "folio", "data-folio": conf.folio }, conf.folio)),
      h("dl", { class: "kv" }, h("dt", {}, "Sucursal"), h("dd", {}, conf.sucursal), h("dt", {}, "Fecha"), h("dd", { id: "fecha-confirmada" }, conf.fecha),
        conf.hora ? [h("dt", {}, "Horario"), h("dd", {}, conf.hora)] : null, h("dt", {}, "Confirmada por"), h("dd", {}, conf.confirmada_por || "—"),
        h("dt", {}, "Evidencia"), h("dd", { class: "mono" }, `${conf.evidencia.fuente} · ${conf.evidencia.estado_portal} · verificada ${fmtTime(conf.evidencia.verificada_en)}`)),
      h("div", { class: "actions" }, h("button", { class: "btn danger", onclick: async () => {
        if (!confirm(`¿Cancelar la reservación ${conf.folio} en el portal de prueba?`)) return;
        await act(() => api(`/api/reservaciones/${conf.id}/cancelar`, { method: "POST" }), "Reservación cancelada"); reabrir(); } }, "Cancelar reservación"))));
    return;
  }
  if (["NUEVO", "INCOMPLETO", "EN_VALIDACION"].includes(e.estado)) {
    box.replaceChildren(h("p", {}, e.estado === "INCOMPLETO" ? `Incompleto: falta ${(e.faltan || []).join(", ")}. Complétalo (editar) y vuelve a validarlo; mientras tanto NO se asigna.`
      : "En validación."), h("div", { class: "actions" }, e.estado === "INCOMPLETO" ? revalidar() : null, cancelarExp()));
    return;
  }
  if (e.estado === "REQUIERE_REVISION") {
    box.replaceChildren(h("p", { class: "error" }, e.ultimo_error || "Requiere revisión de una persona."),
      viva && viva.estado === "EN_VERIFICACION" ? h("p", { class: "muted" }, "Hay una reservación en verificación: espera su resultado antes de revalidar.") : null,
      h("div", { class: "actions" }, isAdmin() ? revalidar() : null, cancelarExp()));
    return;
  }
  if (e.estado === "LISTO" || e.estado === "PENDIENTE") {
    box.replaceChildren(h("p", {}, "Listo y esperando disponibilidad: el motor lo asignará a un operador cuando el calendario tenga cupo en sus sucursales."),
      h("div", { class: "actions" }, cancelarExp()));
    return;
  }
  if (["CERRADO", "CANCELADO", "FALLIDO"].includes(e.estado)) {
    box.replaceChildren(h("p", { class: "muted" }, `Expediente cerrado${e.motivo_cierre ? " · " + e.motivo_cierre.toLowerCase() : ""}.`)); return;
  }
  // 1) preparar (también retoma un intento abierto)
  if (!viva || viva.estado === "PREPARADA") {
    const btn = h("button", { class: "btn primary", id: "btn-preparar" }, "Preparar formulario");
    box.replaceChildren(paso(1, "Preparación del formulario", h("p", { class: "muted" },
      "El motor lee el expediente y llena los campos del portal según el contrato. Revísalos antes de enviarlos."), btn),
      ["ASIGNADO", "CAPTURA_EN_PROCESO"].includes(e.estado) ? rechazoForm(e, reabrir) : null,
      h("div", { class: "actions section" }, cancelarExp()));
    btn.addEventListener("click", async () => {
      try { const r = await api(`/api/expedientes/${e.id}/preparar`, { method: "POST" }); mostrarRevision(box, e, r); }
      catch (err) { toast("⚠ " + err.message, 5000); }
    });
    if (pasoPrevio === "preparar") btn.click();
    return;
  }
  if (viva.estado === "DATOS_ENVIADOS") {
    box.replaceChildren(paso(2, "Calendario", h("p", { class: "muted" }, "Cargando el calendario del portal…")));
    try {
      const r = await api(`/api/reservaciones/${viva.id}/calendario` + (viva.modo === "POR_DIA" && viva.sucursal ? `?sucursal=${encodeURIComponent(viva.sucursal)}` : ""));
      if (viva.modo === "POR_DIA") mostrarCalendarioDia(box, e, viva, r.calendario, r.permitidas);
      else mostrarCalendario(box, e, viva, r.calendario);
    } catch (err) {
      box.replaceChildren(paso(2, "Calendario", h("p", { class: "error" }, err.message),
        h("button", { class: "btn", onclick: () => reabrir("preparar") }, "Volver a preparar y enviar datos")));
    }
    return;
  }
  if (viva.estado === "RETENIDA" || viva.estado === "FECHA_ELEGIDA") { mostrarConfirmacion(box, e, viva); return; }
  if (viva.estado === "CONFIRMANDO" || viva.estado === "EN_VERIFICACION") {
    box.replaceChildren(paso(3, "Confirmación en verificación",
      h("p", {}, "Se envió la confirmación pero no se pudo comprobar el resultado con el portal. El motor lo verifica solo con la clave de confirmación."),
      viva.error_codigo === "FOLIO_DUPLICADO" ? h("p", { class: "error" }, viva.error) : null,
      h("p", { class: "muted" }, "No repitas la reservación por otro medio. Recarga en unos segundos."),
      h("button", { class: "btn", onclick: () => reabrir() }, "Recargar")));
  }
}

function campoManual(c) {
  const err = h("span", { class: "error", "data-error-de": c.campo });
  let input;
  if (c.tipo === "casilla") {
    input = h("input", { type: "checkbox", name: c.campo, id: "manual-" + c.campo, "data-manual": "1" });
  } else {
    input = h("input", { name: c.campo, id: "manual-" + c.campo, "data-manual": "1", autocomplete: "off", spellcheck: "false",
      maxlength: (c.validacion && c.validacion.max_len) || 64, "aria-describedby": "ayuda-" + c.campo });
    if (c.sin_pegar) {
      // Se respeta la regla del portal: este dato se teclea (no se pega). No es un mecanismo para saltarla.
      input.setAttribute("data-sin-pegar", "1");
      input.addEventListener("paste", (ev) => { ev.preventDefault(); err.textContent = "Este dato debe teclearse, no pegarse."; });
      input.addEventListener("drop", (ev) => ev.preventDefault());
    }
  }
  input.addEventListener("input", () => { err.textContent = ""; });
  return { c, input, err, nodo: h("div", { class: "manual" },
    c.tipo === "casilla" ? h("label", { class: "check-line" }, input, c.etiqueta) : field(c.etiqueta, input),
    h("div", { class: "muted", id: "ayuda-" + c.campo }, c.instrucciones || ""), err) };
}

function mostrarRevision(box, e, r) {
  const rev = r.revision;
  const auto = rev.campos.filter((c) => !c.manual);
  const manuales = rev.campos.filter((c) => c.manual).map(campoManual);
  const porDia = rev.modo === "POR_DIA";
  const sucSel = porDia && rev.sucursales_permitidas && rev.sucursales_permitidas.length ? h("select", { id: "sucursal-envio" },
    rev.sucursales_permitidas.map((s) => h("option", { value: s }, s))) : null;
  const chk = h("input", { type: "checkbox", id: "chk-revisado" });
  const enviar = h("button", { class: "btn primary", id: "btn-enviar", disabled: true }, "Enviar datos al portal");
  chk.addEventListener("change", () => { enviar.disabled = !chk.checked || !rev.listo; });
  const errs = h("div", { class: "error", role: "alert" });
  enviar.addEventListener("click", async () => {
    enviar.disabled = true; errs.textContent = "";
    manuales.forEach((m) => { m.err.textContent = ""; });
    const body = { revisado: true };
    if (manuales.length) {
      body.manuales = {};
      for (const m of manuales) {
        const v = m.c.tipo === "casilla" ? (m.input.checked ? "si" : "") : m.input.value;
        if (v) body.manuales[m.c.campo] = v;
      }
    }
    if (sucSel) body.sucursal = sucSel.value;
    try {
      const out = await api(`/api/reservaciones/${r.reservacion.id}/enviar-datos`, { method: "POST", body });
      if (porDia) mostrarCalendarioDia(box, e, out.reservacion, out.calendario, out.permitidas);
      else mostrarCalendario(box, e, out.reservacion, out.calendario);
    } catch (err) {
      const d = err.detalle && err.detalle.errores;
      // Lo tecleado se conserva: solo se marcan los campos que fallaron.
      const restantes = {};
      for (const [k, v] of Object.entries(d || {})) {
        const m = manuales.find((x) => x.c.campo === k);
        if (m) m.err.textContent = v; else restantes[k] = v;
      }
      errs.replaceChildren(err.message, Object.keys(restantes).length ?
        h("ul", {}, Object.entries(restantes).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
      enviar.disabled = !chk.checked;
    }
  });
  box.replaceChildren(paso(1, "Revisión de campos preparados",
    h("p", { class: "muted" }, `Contrato ${rev.contrato} v${rev.version}. Los datos personales se muestran enmascarados.`),
    table([{ t: "Campo del portal", f: (c) => c.etiqueta }, { t: "Valor", f: (c) => h("span", { class: "mono" }, c.valor ?? "—") },
      { t: "Origen", f: (c) => c.origen }, { t: "", f: (c) => badge(c.listo ? "OK" : "ERROR") }], auto),
    rev.faltan.length ? h("p", { class: "error" }, "Faltan datos obligatorios: " + rev.faltan.join(", ") + ". Corrige el expediente antes de enviar.") : null,
    manuales.length ? h("div", { class: "section", id: "captura-manual" }, h("h3", {}, "Captura manual (la teclea el operador)"),
      h("p", { class: "muted" }, "El motor no escribe ni guarda estos datos: se envían al portal solo en este paso."),
      manuales.map((m) => m.nodo)) : null,
    sucSel ? field("Sucursal del calendario", sucSel) : null,
    h("label", { class: "check-line" }, chk, "He revisado los campos y son correctos"),
    h("div", { class: "actions" }, enviar), errs));
}

// ---- POR DÍA: calendario mensual del portal (lo que muestra el portal en esta sesión)
function mesGrid(cal, onElegir) {
  const [y, m] = (cal.mes || (cal.dias[0] && cal.dias[0].fecha.slice(0, 7)) || "").split("-").map(Number);
  if (!y) return h("div", { class: "empty" }, "El portal no mostró días para este mes.");
  const porFecha = Object.fromEntries(cal.dias.map((d) => [d.fecha, d]));
  const primero = new Date(Date.UTC(y, m - 1, 1)).getUTCDay();      // 0 = domingo
  const total = new Date(Date.UTC(y, m, 0)).getUTCDate();
  const celdas = [];
  for (let i = 0; i < (primero + 6) % 7; i++) celdas.push(h("div", { class: "dia vacio" }));
  for (let d = 1; d <= total; d++) {
    const fe = `${y}-${String(m).padStart(2, "0")}-${String(d).padStart(2, "0")}`;
    const x = porFecha[fe];
    const est = x ? x.estado : "NO_HABILITADO";
    const libre = est === "DISPONIBLE" && !!onElegir;
    celdas.push(h(libre ? "button" : "div", { class: "dia e-" + est.toLowerCase(), "data-fecha": fe, "data-estado": est,
      title: `${fe} · ${ESTADO_DIA[est] || est}` + (x && x.libres ? ` · ${x.libres} libres` : ""),
      onclick: libre ? () => onElegir(x) : null }, h("b", {}, d), x && est === "DISPONIBLE" && x.libres !== null && x.libres !== undefined ? h("small", {}, `${x.libres}`) : null));
  }
  return h("div", { class: "mes" }, ["L", "M", "M", "J", "V", "S", "D"].map((d) => h("div", { class: "dsem" }, d)), celdas);
}

function leyendaDias() {
  return h("p", { class: "leyenda" }, ["DISPONIBLE", "AGOTADO", "NO_HABILITADO", "PENDIENTE_ACTUALIZACION", "DESCONOCIDO"].map((k) =>
    h("span", { class: "chip e-" + k.toLowerCase() }, ESTADO_DIA[k])));
}

function mostrarCalendarioDia(box, e, res, cal, permitidas, aviso) {
  const errs = h("div", { class: "error", role: "alert" }, aviso || "");
  const sucs = (permitidas && permitidas.length ? permitidas : [cal.sucursal]).filter(Boolean);
  const recargar = async (suc, mes) => {
    errs.textContent = "";
    try {
      const q = new URLSearchParams({ sucursal: suc }); if (mes) q.set("mes", mes);
      const r = await api(`/api/reservaciones/${res.id}/calendario?` + q);
      mostrarCalendarioDia(box, e, res, r.calendario, r.permitidas);
    } catch (err) { errs.textContent = err.message; }
  };
  const mesAnt = () => { const [y, m] = cal.mes.split("-").map(Number); const d = new Date(Date.UTC(y, m - 2, 1)); return d.toISOString().slice(0, 7); };
  const mesSig = () => { const [y, m] = cal.mes.split("-").map(Number); const d = new Date(Date.UTC(y, m, 1)); return d.toISOString().slice(0, 7); };
  const elegir = async (dia) => {
    errs.textContent = "";
    try {
      const out = await api(`/api/reservaciones/${res.id}/seleccionar-dia`, { method: "POST", body: { fecha: dia.fecha, sucursal: cal.sucursal } });
      mostrarConfirmacion(box, e, out.reservacion);
    } catch (err) {
      if (err.detalle && err.detalle.calendario) { mostrarCalendarioDia(box, e, res, err.detalle.calendario, permitidas, err.message); return; }
      errs.textContent = err.message;
    }
  };
  const disp = cal.dias.filter((d) => d.estado === "DISPONIBLE").length;
  box.replaceChildren(paso(2, "Calendario por día",
    h("div", { class: "ficha" }, Object.entries(cal.ficha || {}).map(([k, v]) => h("span", { class: "chip" }, `${k.replace("ficha_", "")}: ${v}`))),
    h("div", { class: "filters" },
      h("select", { id: "sucursal-cal", onchange: (ev) => recargar(ev.target.value, cal.mes) }, sucs.map((s) => h("option", { value: s, selected: s === cal.sucursal }, s))),
      h("button", { class: "btn small", onclick: () => recargar(cal.sucursal, mesAnt()) }, "◀ Mes anterior"),
      h("b", { "data-mes": cal.mes }, cal.mes),
      h("button", { class: "btn small", onclick: () => recargar(cal.sucursal, mesSig()) }, "Mes siguiente ▶")),
    h("p", { class: "muted" }, `${disp} días disponibles en ${cal.sucursal}. Pulsa un día disponible para elegirlo (no se aparta: se gana al confirmar).`),
    leyendaDias(), errs, mesGrid(cal, elegir)));
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
      await showExpediente(e.id);
      ofrecerSiguiente(e.id);          // v1.7: paso 12, continuar con el siguiente sin volver a la lista
    } catch (err) {
      if (porDia && err.detalle && err.detalle.calendario) {    // el día se agotó: calendario al día, elige otro
        mostrarCalendarioDia(box, e, err.detalle.reservacion || res, err.detalle.calendario, null, err.message); return;
      }
      toast("⚠ " + err.message, 6000); showExpediente(e.id);
    }
  });
  const porDia = res.modo === "POR_DIA";
  box.replaceChildren(paso(3, "Confirmación",
    h("dl", { class: "kv" }, h("dt", {}, "Sucursal"), h("dd", {}, (resumen && resumen.sucursal) || res.sucursal),
      h("dt", {}, "Fecha"), h("dd", { id: "fecha-elegida" }, res.fecha),
      porDia ? null : [h("dt", {}, "Horario"), h("dd", {}, res.hora), h("dt", {}, "Apartado hasta"), h("dd", {}, fmtTime(res.vence_en))]),
    porDia ? h("p", { class: "muted" }, "El día no queda apartado: si alguien toma la última unidad antes, el portal lo dirá y podrás elegir otro.") : null,
    h("label", { class: "check-line" }, chk, "Confirmo esta reservación con los datos del expediente"),
    h("div", { class: "actions" }, btn,
      h("button", { class: "btn ghost", onclick: async () => {
        try {
          const r = await api(`/api/reservaciones/${res.id}/calendario` + (porDia && res.sucursal ? `?sucursal=${encodeURIComponent(res.sucursal)}` : ""));
          if (porDia) mostrarCalendarioDia(box, e, res, r.calendario, r.permitidas); else mostrarCalendario(box, e, res, r.calendario);
        } catch (err) { errs.textContent = err.message; } } }, porDia ? "Elegir otro día" : "Elegir otro horario"),
      h("button", { class: "btn ghost danger", onclick: async () => {
        await act(() => api(`/api/reservaciones/${res.id}/cancelar`, { method: "POST" }), "Intento descartado"); showExpediente(e.id); } }, "Descartar")),
    errs));
}


// ------------------------------------------------------------------ v1.7: MI TRABAJO (consola del operador)
// Una sola lista con lo que el operador tiene que hacer, ordenada por urgencia, con el SIGUIENTE PASO
// de cada expediente. «Continuar» abre el expediente en ese paso (si toca preparar, lo prepara).
// Todo sale del servidor: si se perdió la conexión o se cerró el navegador, al volver se retoma
// donde quedó (el estado vive en la base de datos, no en la pantalla).
async function tabTrabajo() {
  const d = await api("/api/captura/mi-trabajo");
  const r = d.resumen;
  const item = (x) => h("div", { class: "trabajo-item" + (x.urgente ? " urgente" : "") + (x.interrumpido ? " interrumpido" : ""),
    "data-trabajo": x.id },
    h("div", { class: "prio", title: "Prioridad (1 = más alta)" }, "P" + x.prioridad),
    h("div", {},
      h("div", {}, h("b", {}, x.codigo), " · ", TRAMITES[x.tipo_tramite] || x.tipo_tramite, x.sucursal ? " · " + x.sucursal : "", " ", badge(x.estado)),
      h("div", { class: "paso", "data-paso": x.paso.clave }, `Paso ${x.paso.n} de 12: ${x.paso.texto}`),
      x.faltan.length ? h("div", { class: "error" }, "Faltan datos: " + x.faltan.join(", ")) : null,
      x.interrumpido ? h("div", { class: "muted" }, "Operación a medias: se retoma en el paso guardado.") : null,
      x.avisos.map((a) => h("div", { class: "muted" }, a))),
    h("div", { class: "actions" }, x.paso.accion ? h("button", { class: "btn primary", "data-continuar": x.id,
      onclick: () => showExpediente(x.id, x.paso.clave === "preparar" ? "preparar" : undefined) }, x.interrumpido ? "Retomar" : "Continuar")
      : h("button", { class: "btn small", onclick: () => showExpediente(x.id) }, "Ver")));
  return h("div", {},
    h("div", { class: "grid kpis" },
      kpi("Por empezar", num(r.por_empezar), "asignados a ti"), kpi("A medias", num(r.a_medias), "se retoman donde quedaron"),
      kpi("En verificación", num(r.en_verificacion), "el motor comprueba el folio"), kpi("Confirmados hoy", num(r.confirmados_hoy))),
    d.items.length ? h("div", { class: "trabajo section", id: "mi-trabajo" }, d.items.map(item))
      : h("div", { class: "empty section" }, "No tienes expedientes asignados. Cuando el motor te asigne uno aparecerá aquí."),
    h("p", { class: "muted" }, "Solo ves tus expedientes. Los datos personales se muestran al abrir cada uno y esa consulta queda registrada."));
}

async function ofrecerSiguiente(actual) {
  try {
    const d = await api("/api/captura/mi-trabajo");
    const sig = d.items.find((x) => x.id !== actual && x.paso.accion);
    const box = $("flujo");
    if (!box) return;
    box.append(h("div", { class: "siguiente", id: "siguiente" }, sig ? [
      h("p", {}, `Siguiente: ${sig.codigo} (P${sig.prioridad}) · paso ${sig.paso.n}: ${sig.paso.texto}`),
      h("button", { class: "btn primary", id: "btn-siguiente", onclick: () => showExpediente(sig.id, sig.paso.clave === "preparar" ? "preparar" : undefined) },
        "Siguiente expediente")] : h("p", {}, "No te quedan expedientes pendientes.")));
  } catch (_) { /* sin conexión: la lista de «Mi trabajo» lo mostrará al volver */ }
}

// ------------------------------------------------------------------ Otras pestañas
async function tabPreparacion() {
  const [k, res] = await Promise.all([api("/api/captura/contrato"), api("/api/reservaciones")]);   // contrato de la modalidad vigente
  const abiertas = res.filter((r) => ["PREPARADA", "DATOS_ENVIADOS"].includes(r.estado));
  return h("div", { class: "grid cols-2" },
    card("Formularios en preparación", table([{ t: "Expediente", f: (r) => h("b", {}, r.expediente) }, { t: "Estado", f: (r) => badge(r.estado) },
      { t: "Aviso", f: (r) => r.error || "" }, { t: "Actualizado", f: (r) => ago(r.actualizada) }], abiertas, (r) => showExpediente(r.expediente_id))),
    card(`Contrato de campos · ${k.nombre} v${k.version}`, h("p", { class: "muted" },
      "Lo que el motor llena en la pantalla de datos del portal. Si el portal cambia un campo, el motor se detiene y pide revisión."),
      table([{ t: "Campo", f: (c) => h("span", { class: "mono" }, c.campo) }, { t: "Etiqueta", f: (c) => c.etiqueta },
        { t: "Tipo", f: (c) => c.tipo }, { t: "Del expediente", f: (c) => c.modo === "MANUAL_REQUIRED" ? "lo teclea el operador" : c.origen },
        { t: "Obligatorio", f: (c) => c.requerido_si ? `si ${c.requerido_si.campo} = ${c.requerido_si.igual}` : c.obligatorio ? "Sí" : "No" }], k.campos)));
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
    { t: "Sucursal", f: (r) => r.sucursal || "—" }, { t: "Fecha", f: (r) => r.fecha ? `${r.fecha} ${r.hora || ""}` : "—" },
    { t: "Folio", f: (r) => r.folio ? h("span", { class: "mono" }, r.folio) : "—" }, { t: "Intentos", num: true, f: (r) => r.intentos_confirmacion },
    { t: "Aviso", f: (r) => r.error || "" }, { t: "Actualizada", f: (r) => ago(r.actualizada) }], res, (r) => showExpediente(r.expediente_id)));
}

async function tabConfirmaciones() {
  const res = await api("/api/reservaciones");
  const pend = res.filter((r) => ["RETENIDA", "FECHA_ELEGIDA", "CONFIRMANDO", "EN_VERIFICACION"].includes(r.estado));
  const hechas = res.filter((r) => r.estado === "CONFIRMADA");
  return h("div", { class: "grid cols-2" },
    card("Por confirmar o en verificación", table([{ t: "Expediente", f: (r) => h("b", {}, r.expediente) }, { t: "Estado", f: (r) => badge(r.estado) },
      { t: "Día / horario", f: (r) => `${r.sucursal || "—"} · ${r.fecha || ""} ${r.hora || ""}` },
      { t: "Aviso", f: (r) => r.error_codigo || (r.vence_en ? "apartado hasta " + fmtTime(r.vence_en) : "") }], pend, (r) => showExpediente(r.expediente_id))),
    card("Confirmadas (con evidencia del portal)", table([{ t: "Folio", f: (r) => h("span", { class: "mono" }, r.folio) },
      { t: "Expediente", f: (r) => r.expediente }, { t: "Día / horario", f: (r) => `${r.sucursal} · ${r.fecha} ${r.hora || ""}` },
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

// ------------------------------------------------------------------ v1.6 · Resumen para el día a día
async function tabResumen(f) {
  const r = await api("/api/captura/resumen");
  const pe = r.expedientes.por_estado, pr = r.reservaciones.por_estado;
  const n = (o, ...ks) => ks.reduce((a, k) => a + (o[k] || 0), 0);
  const ir = (tab, grupo) => () => { f.tab = tab; if (grupo !== undefined) f.grupo = grupo; refresh(true); };
  const k = (label, value, sub, onClick, alerta) => {
    const el = kpi(label, num(value), sub); if (onClick) { el.classList.add("clickable"); el.addEventListener("click", onClick); }
    if (alerta && value) el.classList.add("attention"); return el;
  };
  const fuentes = r.calendarios.fuentes || [];
  const peor = fuentes.reduce((a, x) => Math.max(a, x.segundos_desde_verificacion ?? 1e9), 0);
  return h("div", {},
    h("div", { class: "grid kpis" },
      k("Listos para asignar", pe.LISTO || 0, "esperan cupo", ir("expedientes", "pendiente")),
      k("Incompletos", n(pe, "NUEVO", "INCOMPLETO", "EN_VALIDACION"), "no se asignan", ir("expedientes", "preparacion"), true),
      k("Requieren revisión", pe.REQUIERE_REVISION || 0, "decide una persona", ir("expedientes", "revision"), true),
      k("Operadores activos", r.operadores.activos, `${r.operadores.con_trabajo} con casos · ${r.operadores.capacidad_libre} lugares libres`),
      k("Asignaciones por tomar", r.asignaciones.por_tomar, `${r.asignaciones.en_cola} solicitudes en cola`),
      k("En verificación", n(pr, "CONFIRMANDO", "EN_VERIFICACION"), "resultado incierto: lo comprueba el motor", ir("confirmaciones"), true),
      k("Confirmadas", pr.CONFIRMADA || 0, "con folio y evidencia", ir("confirmaciones")),
      k("Errores", r.errores.length, "ver abajo", null, true)),
    card("Recorrido de los expedientes", h("p", { class: "muted" }, "De la preparación al folio. Cada flecha es un paso que da el motor o una persona."),
      h("ol", { class: "recorrido grande" }, RECORRIDO.map(([t, ests]) => h("li", {}, h("b", {}, num(n(pe, ...ests))), " ", t)))),
    h("div", { class: "grid cols-2 section" },
      card("Calendarios por sucursal", fuentes.length ? h("p", { class: peor > 60 ? "error" : "muted" },
        fuentes.map((x) => `Fuente ${x.source_id} (${x.mecanismo === "EVENTOS" ? "eventos" : "consulta periódica"}): verificado ${hace(x.segundos_desde_verificacion)}` +
          (x.sin_verificar ? ` · SIN VERIFICAR (${x.ultimo_error_codigo || "?"})` : "")).join(" · ")) :
        h("p", { class: "muted" }, "Todavía no hay calendarios por día conectados."),
        table([{ t: "Sucursal", f: (s) => h("b", {}, s.sucursal) }, { t: "Región", f: (s) => s.region },
          { t: "Disponibles", num: true, f: (s) => s.dias.DISPONIBLE || 0 }, { t: "Agotados", num: true, f: (s) => s.dias.AGOTADO || 0 },
          { t: "Sin verificar", num: true, f: (s) => (s.dias.DESCONOCIDO || 0) + (s.dias.PENDIENTE_ACTUALIZACION || 0) }],
          r.calendarios.sucursales, (s) => { f.tab = "dias"; f.sucursal = s.sucursal; refresh(true); })),
      card("Errores y avisos", r.errores.length ? h("ul", {}, r.errores.map((x) => h("li", {}, badge(x.tipo), " ", x.detalle))) :
        h("div", { class: "empty" }, "Sin errores ahora."))));
}

// ------------------------------------------------------------------ v1.6 · Calendario por día del MOTOR
async function tabDias(f) {
  const q = new URLSearchParams();
  if (f.sucursal) q.set("sucursal", f.sucursal);
  if (f.tramite) q.set("tramite", f.tramite);
  const [v, cambios] = await Promise.all([api("/api/captura/calendario-dias?" + q), api("/api/captura/calendario-dias/cambios?ultimos=true&limite=15")]);
  const todas = v.sucursales;
  const suc = todas.find((s) => s.sucursal === f.sucursal) || todas[0];
  const filtros = h("div", { class: "filters" },
    h("select", { id: "f-sucursal", onchange: (e) => { f.sucursal = e.target.value; refresh(true); } },
      todas.map((s) => h("option", { value: s.sucursal, selected: suc && s.sucursal === suc.sucursal }, `${s.sucursal} · ${s.nombre}`))),
    h("select", { id: "f-tramite", onchange: (e) => { f.tramite = e.target.value; refresh(true); } },
      h("option", { value: "" }, "Todos los trámites"), Object.entries(TRAMITES).map(([k2, t2]) => h("option", { value: k2, selected: f.tramite === k2 }, t2))));
  const banner = v.fuentes.map((x) => h("p", { class: x.sin_verificar ? "error" : "muted", "data-fuente": x.source_id },
    x.sin_verificar ? `⚠ Sin comunicación con el calendario (${x.ultimo_error_codigo || "error"}). Se muestra lo ALMACENADO; ` +
      `última verificación real: ${x.verificado_en ? fmtTime(x.verificado_en) : "nunca"}. No es falta de disponibilidad.`
      : `Información ACTUAL · verificada ${hace(x.segundos_desde_verificacion)} (${fmtTime(x.verificado_en)}) · ` +
        `${x.mecanismo === "EVENTOS" ? "recibe cambios al momento" : "consulta periódica"} · zona ${v.zona_horaria}`));
  if (!suc) return card("Calendario por día", banner, h("div", { class: "empty" }, "Todavía no hay días recibidos de ningún portal."));
  const meses = {};
  for (const [tr, dias] of Object.entries(suc.tramites)) {
    if (f.tramite && tr !== f.tramite) continue;
    for (const d of dias) (meses[d.fecha.slice(0, 7)] = meses[d.fecha.slice(0, 7)] || {})[tr] = [...((meses[d.fecha.slice(0, 7)] || {})[tr] || []), d];
  }
  const bloques = [];
  for (const mes of Object.keys(meses).sort()) for (const [tr, dias] of Object.entries(meses[mes])) {
    bloques.push(card(`${TRAMITES[tr] || tr} · ${mes}`, mesGrid({ mes, dias }, null)));
  }
  return h("div", {}, card(`Calendario por día · ${suc.nombre} (${suc.sucursal}, ${suc.region})`, filtros, banner, leyendaDias(),
      h("p", { class: "muted" }, "Esta vista se actualiza sola cada pocos segundos sin recargar la página. Muestra lo que el motor sabe; no aparta nada.")),
    h("div", { class: "grid cols-2 section" }, bloques),
    card("Últimos cambios detectados", table([{ t: "Cuándo", f: (c) => fmtTime(c.detectado_en) }, { t: "Cambio", f: (c) => badge(c.tipo) },
      { t: "Día", f: (c) => c.fecha ? `${c.sucursal} · ${c.tramite} · ${c.fecha}` : "—" },
      { t: "Antes → después", f: (c) => c.antes ? `${c.antes} → ${c.despues}` : "" }, { t: "Por", f: (c) => c.mecanismo === "EVENTOS" ? "evento" : "consulta" }],
      cambios.cambios.slice().reverse())));
}
