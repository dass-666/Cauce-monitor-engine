/* RECUPERACIÓN SEGURA · interfaz del administrador (v2.4).
 * Pantalla para la conciliación que antes solo existía por API. Reutiliza SIN cambios:
 *   GET  /api/recuperacion                               estado, motivos, discrepancias, diario y copia
 *   POST /api/recuperacion/discrepancias/{ref}/resolver  una acción explícita con motivo (auditada)
 *   POST /api/recuperacion/levantar                      solo con 0 discrepancias pendientes (auditada)
 *   POST /api/recuperacion/reiniciar-diario              solo si el diario no supera la verificación (auditada)
 *   GET  /api/audit                                      historial (acciones recuperacion_*)
 * Todas exigen rol administrador en el servidor; aquí solo se ofrece lo que el servidor admitiría.
 * Ninguna acción llama al portal. Todo el contenido dinámico se inserta como texto (nunca innerHTML). */
"use strict";

const REC_ACCION = {
  reaplicar_revocacion: ["Reaplicar la revocación", "El consentimiento vuelve a quedar REVOCADO en la base, como dice el diario."],
  reconocer: ["Reconocer (revisado)", "No cambia la base: deja constancia de que lo revisaste."],
  registrar_folio: ["Registrar el folio del portal", "Marca la cita CONFIRMADA con el folio que ves en el portal. Si el diario tiene el folio, debe coincidir."],
  no_existe: ["La cita no existe en el portal", "Comprobaste en el portal que no hay cita: no cambia la base."],
  aplicar_cancelacion: ["Aplicar la cancelación", "La reservación queda CANCELADA en la base y el expediente se cierra."],
  sigue_vigente: ["La cita sigue vigente", "Comprobaste en el portal que la cita sigue: la base no cambia."],
};
const REC_TIPO = {
  REVOCACION_AUSENTE: "Revocación perdida", REVOCACION_SIN_CONSENTIMIENTO: "Revocación sin consentimiento en la base",
  CONFIRMACION_AUSENTE: "Cita confirmada perdida", CONFIRMACION_INCIERTA: "Confirmación incierta",
  CONFIRMACION_SIN_RESERVACION: "Cita sin reservación en la base", CANCELACION_AUSENTE: "Cancelación perdida",
  CANCELACION_INCIERTA: "Cancelación incierta", CANCELACION_SIN_RESERVACION: "Cancelación sin reservación en la base",
};
// Mismos conjuntos que el servidor (monitoreo/recuperacion.py): solo para decidir qué botones mostrar.
const REC_INTEGRIDAD = ["DIARIO_MANIPULADO", "DIARIO_ILEGIBLE", "CLAVE_NO_DISPONIBLE", "DIARIO_TRUNCADO", "DIARIO_AUSENTE", "COPIA_DIVERGENTE"];
const REC_LEVANTABLES = ["RESTAURACION_PENDIENTE", "BLOQUEO_PENDIENTE", "FALLO_ESCRITURA"];

const recCodigos = (e) => (e.motivos || []).map((m) => m.codigo);
const recPuedeLevantar = (e) => e.bloqueado && !(e.pendientes || 0) && recCodigos(e).every((c) => REC_LEVANTABLES.includes(c));
const recPuedeReiniciar = (e) => recCodigos(e).some((c) => REC_INTEGRIDAD.includes(c));

function recErr(errs, err) {
  const d = err.detalle || {};
  const extra = [];
  if (d.motivos && d.motivos.length) extra.push("Motivos: " + d.motivos.join(", "));
  if (d.pendientes) extra.push(`Discrepancias pendientes: ${d.pendientes}`);
  if (d.acciones) extra.push("Acciones válidas: " + d.acciones.join(", "));
  errs.replaceChildren(err.message, extra.length ? h("ul", {}, extra.map((x) => h("li", {}, x))) : "");
}

// Tarjeta para la Mesa de control: visible siempre que el administrador entra (bloqueado o no).
async function tarjetaRecuperacion() {
  let e;
  try { e = await api("/api/recuperacion"); } catch (_) { return null; }
  const el = kpi("Recuperación segura",
    h("span", { id: "kpi-recuperacion", "data-bloqueado": e.bloqueado ? "1" : "0" }, e.bloqueado ? "BLOQUEADA" : "Operable"),
    e.bloqueado ? `${e.pendientes || 0} discrepancia(s) · ${recCodigos(e).join(", ")}` : (e.copia && e.copia.configurada ? "diario íntegro · con copia externa" : "diario íntegro · SIN copia externa"));
  el.dataset.kpi = "recuperacion";
  el.classList.add("clickable");
  if (e.bloqueado) el.classList.add("attention");
  el.addEventListener("click", () => { location.hash = "#recuperacion"; });
  return el;
}

async function viewRecuperacion() {
  const [e, audit] = await Promise.all([api("/api/recuperacion"), api("/api/audit?limit=500").catch(() => [])]);
  const errs = h("div", { class: "error", role: "alert", id: "rec-error" });
  const cods = recCodigos(e);
  const estado = card("Estado", h("div", { id: "rec-estado", "data-bloqueado": e.bloqueado ? "1" : "0" },
    h("p", {}, e.bloqueado
      ? h("span", { class: "badge b-bad" }, "OPERACIONES CON EL PORTAL BLOQUEADAS")
      : h("span", { class: "badge b-ok" }, "Operable: el diario coincide con la base")),
    e.bloqueado ? h("p", { class: "muted" }, "Mientras haya bloqueo no se confirma ni se cancela nada en el portal, y la recuperación automática no actúa. ",
      "Lo demás (consultar, corregir datos, registrar consentimientos) sigue funcionando.") : null,
    cods.length ? h("ul", { id: "rec-motivos" }, (e.motivos || []).map((m) => h("li", { "data-motivo": m.codigo },
      h("b", {}, m.codigo.replace(/_/g, " ")), " · ", m.mensaje))) : null,
    cods.includes("COPIA_NO_DISPONIBLE") ? h("p", { class: "muted" },
      "La copia externa está configurada pero no se puede usar: conecta la unidad (USB o red) y pulsa «Actualizar». ",
      "Si la unidad ya no existe, corrige MONITOREO_DIARIO_COPIA con el motor detenido (docs/RESPALDO_EXTERNO_DIARIO.md).") : null,
    (e.avisos || []).length ? h("div", { id: "rec-avisos" }, e.avisos.map((a) => h("p", { class: "muted" }, "⚠ " + a))) : null));

  const d = e.diario || {}, c = e.copia || {};
  const diario = card("Diario de eventos críticos", h("dl", { class: "kv", id: "rec-diario" },
    h("dt", {}, "Eventos"), h("dd", {}, num(d.eventos || 0)),
    h("dt", {}, "Punto de referencia en la base"), h("dd", {}, d.ancla_en_base ? `línea ${d.ancla_en_base}` : "—"),
    h("dt", {}, "Protección de la clave"), h("dd", {}, d.clave === "dpapi-usuario" ? "DPAPI (ligada a la cuenta de Windows que ejecuta el motor)"
      : d.clave === "archivo-solo-dueno" ? "archivo legible solo por su dueño" : (d.clave || "aún no creada")),
    h("dt", {}, "Clave exportada"), h("dd", { "data-clave-exportada": d.clave_exportada ? "1" : "0" }, d.clave_exportada ? fmtTime(d.clave_exportada) : "NO: expórtala a otro medio"),
    h("dt", {}, "Copia externa"), h("dd", { id: "rec-copia", "data-configurada": c.configurada ? "1" : "0" },
      c.configurada ? `${c.carpeta || ""} · ${c.eventos ?? "?"} evento(s) · ${c.al_dia ? "al día" : "NO al día"}${c.misma_unidad ? " · MISMA unidad (no protege)" : ""}`
        : "sin configurar (no protege contra la pérdida del disco)"),
    d.fragmentos ? [h("dt", {}, "Líneas cortadas"), h("dd", {}, num(d.fragmentos))] : null,
    d.truncado ? [h("dt", {}, "Truncado"), h("dd", {}, d.truncado)] : null,
    d.fallo ? [h("dt", {}, "Fallo de escritura"), h("dd", {}, d.fallo)] : null),
  h("p", { class: "muted" }, "La configuración de la copia y la exportación de la clave se hacen en el equipo, con el motor detenido: ",
    "python -m monitoreo.recuperacion revisar-copia <carpeta> y exportar-clave <archivo>. Ver docs/RESPALDO_EXTERNO_DIARIO.md."));

  const discs = e.discrepancias || [];
  const tabla = card(`Discrepancias (${e.pendientes || 0} pendiente(s))`, h("div", { id: "rec-discrepancias", "data-n": discs.length },
    discs.length ? table([
      { t: "Ref.", f: (x) => h("b", { class: "mono" }, `#${x.ref}`) },
      { t: "Tipo", f: (x) => h("span", { class: "badge " + (x.reconocida ? "" : "b-warn"), "data-tipo": x.tipo }, REC_TIPO[x.tipo] || x.tipo) },
      { t: "Expediente", f: (x) => x.expediente ? h("b", {}, x.expediente) : "—" },
      { t: "En la base", f: (x) => x.estado_en_base || (x.consentimiento_id ? "consentimiento VIGENTE" : "—") },
      { t: "Hora del evento", f: (x) => fmtTime(x.hora_evento) },
      { t: "", f: (x) => x.reconocida ? h("span", { class: "muted" }, "reconocida")
        : h("button", { class: "btn primary small", "data-resolver": `${x.ref}:${x.tipo}`, onclick: (ev) => { ev.stopPropagation(); formResolver(x); } }, "Resolver") }], discs)
      : h("p", { class: "muted" }, "Sin discrepancias: la base refleja todos los eventos críticos del diario.")));

  const acciones = card("Acciones", h("div", { class: "actions", id: "rec-acciones" },
    h("button", { class: "btn", id: "btn-rec-actualizar", onclick: () => refresh(true) }, "Actualizar"),
    h("button", { class: "btn primary", id: "btn-rec-levantar", disabled: !recPuedeLevantar(e), onclick: () => formLevantar(e) }, "Levantar el bloqueo"),
    recPuedeReiniciar(e) ? h("button", { class: "btn danger", id: "btn-rec-reiniciar", onclick: () => formReiniciar(e) }, "Reiniciar el diario") : null),
  h("p", { class: "muted" }, !e.bloqueado ? "No hay bloqueo que levantar."
    : recPuedeLevantar(e) ? "Todas las discrepancias están resueltas: ya puedes levantar el bloqueo (queda auditado)."
      : recPuedeReiniciar(e) ? "El diario no supera la verificación: primero repón la clave o la copia correcta; reinícialo solo si no es posible."
        : "Resuelve cada discrepancia antes de levantar el bloqueo."), errs);

  const hist = (audit || []).filter((a) => String(a.accion || "").startsWith("recuperacion_")).slice(0, 30);
  const historial = card("Historial de conciliación (auditoría)", h("div", { id: "rec-historial", "data-n": hist.length },
    hist.length ? table([
      { t: "Hora", f: (a) => fmtTime(a.ts) }, { t: "Usuario", f: (a) => a.usuario },
      { t: "Acción", f: (a) => a.accion.replace("recuperacion_", "").replace(/_/g, " ") },
      { t: "Detalle", f: (a) => { const x = a.detalle || {}; return [x.discrepancia, x.accion, x.efecto && `→ ${x.efecto}`, x.motivo && `«${x.motivo}»`].filter(Boolean).join(" · "); } }], hist)
      : h("p", { class: "muted" }, "Todavía no hay acciones de recuperación registradas.")));

  return h("div", { id: "recuperacion" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Recuperación segura"),
      h("div", { class: "muted" }, "Conciliación tras restaurar un respaldo o si el diario no cuadra con la base. Cada acción es explícita, con motivo, y queda auditada."))),
    h("div", { class: "grid cols-2 section" }, estado, diario),
    h("div", { class: "section" }, tabla),
    h("div", { class: "grid cols-2 section" }, acciones, historial));
}

function campoMotivo(placeholder) {
  return field("Motivo (obligatorio, queda en la auditoría)", h("textarea", { name: "motivo", required: true, minlength: 5, maxlength: 300, rows: 3, placeholder }));
}
const confirmo = (texto) => h("label", { class: "check full" }, h("input", { type: "checkbox", name: "confirmo", required: true }), " " + texto);

function formResolver(x) {
  const errs = h("div", { class: "error full", role: "alert" });
  const ayuda = h("p", { class: "muted full", id: "rec-ayuda-accion" });
  const folio = field("Folio que muestra el portal", h("input", { name: "folio", maxlength: 32, autocomplete: "off" }));
  const sel = h("select", { name: "accion", required: true, onchange: () => pinta() },
    x.acciones.map((a) => h("option", { value: a }, (REC_ACCION[a] || [a])[0])));
  const pinta = () => {
    ayuda.textContent = (REC_ACCION[sel.value] || ["", ""])[1];
    folio.classList.toggle("hidden", sel.value !== "registrar_folio");
    folio.querySelector("input").required = sel.value === "registrar_folio";
  };
  const form = h("form", { class: "form", id: "form-resolver", "data-ref": x.ref, onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const el = ev.target.elements;
    const body = { tipo: x.tipo, accion: el.accion.value, motivo: el.motivo.value.trim() };
    if (body.accion === "registrar_folio") body.folio = el.folio.value.trim();
    const btn = ev.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      const r = await api(`/api/recuperacion/discrepancias/${x.ref}/resolver`, { method: "POST", body });
      $("drawer").classList.add("hidden");
      toast(`Discrepancia #${x.ref} resuelta. Pendientes: ${r.pendientes || 0}`, 5000);
      S.dirty = false; refresh(true);
    } catch (err) { recErr(errs, err); btn.disabled = false; }
  } },
  field("Acción", sel), ayuda, folio, campoMotivo("Qué comprobaste y dónde (p. ej. «revisé el portal: la cita existe con ese folio»)"),
  confirmo("Comprobé la situación real antes de elegir esta acción. Cauce NO consulta ni repite nada en el portal."),
  h("button", { class: "btn primary", type: "submit" }, "Aplicar la acción"), errs);
  pinta();
  openDrawer(h("div", { id: "rec-resolver", "data-tipo": x.tipo },
    h("h2", {}, `${REC_TIPO[x.tipo] || x.tipo} · #${x.ref}`),
    h("p", {}, x.explicacion),
    h("dl", { class: "kv" },
      h("dt", {}, "Expediente"), h("dd", {}, x.expediente || "—"),
      h("dt", {}, "Estado en la base"), h("dd", {}, x.estado_en_base || (x.consentimiento_id ? "consentimiento VIGENTE" : "no existe en la base")),
      h("dt", {}, "Hora del evento"), h("dd", {}, fmtTime(x.hora_evento)),
      x.folio_en_diario !== undefined ? [h("dt", {}, "Folio en el diario"), h("dd", {}, x.folio_en_diario ? "sí (se comprobará que coincida)" : "no")] : null,
      x.expediente_id && flujoOn() ? [h("dt", {}, ""), h("dd", {}, h("button", { class: "btn ghost small", type: "button", onclick: async () => openDrawer(await historialExpediente(x.expediente_id, true)) }, "Ver historial del expediente"))] : null),
    form));
}

function formLevantar(e) {
  const errs = h("div", { class: "error full", role: "alert" });
  const form = h("form", { class: "form", id: "form-levantar", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const btn = ev.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      const r = await api("/api/recuperacion/levantar", { method: "POST", body: { motivo: ev.target.elements.motivo.value.trim() } });
      $("drawer").classList.add("hidden");
      toast(r.levantado ? "Bloqueo levantado: las operaciones con el portal vuelven a estar disponibles" : (r.aviso || "El bloqueo sigue activo"), 6000);
      S.dirty = false; refresh(true);
    } catch (err) { recErr(errs, err); btn.disabled = false; }
  } }, campoMotivo("p. ej. «restauración del respaldo de las 09:00 revisada; 2 discrepancias conciliadas»"),
  confirmo("Revisé todas las discrepancias y la situación real del portal."),
  h("button", { class: "btn primary", type: "submit" }, "Levantar el bloqueo"), errs);
  openDrawer(h("div", { id: "rec-levantar" }, h("h2", {}, "Levantar el bloqueo de recuperación"),
    h("p", {}, `Motivos actuales: ${recCodigos(e).join(", ")}. El servidor vuelve a comprobar el diario antes de levantarlo y lo rechaza si queda algo pendiente.`), form));
}

function formReiniciar(e) {
  const errs = h("div", { class: "error full", role: "alert" });
  const form = h("form", { class: "form", id: "form-reiniciar", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const btn = ev.target.querySelector("button[type=submit]"); btn.disabled = true;
    try {
      const r = await api("/api/recuperacion/reiniciar-diario", { method: "POST", body: { motivo: ev.target.elements.motivo.value.trim() } });
      $("drawer").classList.add("hidden");
      toast(`Diario reiniciado (${(r.archivados || []).length} archivo(s) archivados). Sigue bloqueado hasta que lo levantes.`, 7000);
      S.dirty = false; refresh(true);
    } catch (err) { recErr(errs, err); btn.disabled = false; }
  } }, campoMotivo("p. ej. «la clave se perdió con el equipo anterior y no hay exportación»"),
  confirmo("Entiendo que lo que solo estaba en el diario anterior ya no se comparará con la base y debo revisarlo a mano."),
  h("button", { class: "btn danger", type: "submit" }, "Archivar y reiniciar el diario"), errs);
  openDrawer(h("div", { id: "rec-reiniciar" }, h("h2", {}, "Reiniciar el diario de eventos críticos"),
    h("p", {}, `El diario no supera la verificación (${recCodigos(e).filter((c) => REC_INTEGRIDAD.includes(c)).join(", ")}).`),
    h("p", { class: "muted" }, "Antes de reiniciar, intenta reponer lo correcto: importar la clave exportada (importar-clave) o reconectar la copia original. ",
      "Reiniciar archiva el diario actual (no lo borra), empieza uno nuevo con un BLOQUEO y lo rellena con lo que tiene la base."), form));
}
