/* v2.0 · MODO VENTANILLA: atender a una persona presente de principio a fin en una sola pantalla.
 * Persona (buscar o registrar) -> datos -> fecha -> confirmación -> cierre con resultado.
 * Reutiliza el flujo de captura de captura.js (pintarFlujo): no hay un segundo camino para confirmar.
 * Todo el estado vive en el servidor: si se cierra el navegador o se corta la red, «Retomar» vuelve
 * al paso exacto. Todo el contenido dinámico se inserta como texto (nunca innerHTML con datos). */
"use strict";

Object.assign(BADGE, { SIN_DISPONIBILIDAD: "", PENDIENTE: "b-warn", EN_PROCESO: "b-info", CONFIRMADA: "b-ok" });
const AT_NOMBRE = { PENDIENTE: "Pendiente", EN_PROCESO: "En proceso", CONFIRMADA: "Cita confirmada",
  SIN_DISPONIBILIDAD: "Sin disponibilidad", ERROR: "Error", CANCELADA: "Cancelada" };
const AT_PASOS = [["expediente", "Persona"], ["preparar", "Datos"], ["enviar", "Datos"], ["fecha", "Fecha"],
  ["confirmar", "Confirmación"], ["verificacion", "Confirmación"], ["cerrar", "Cierre"], ["terminada", "Cierre"]];
S.ventanilla = S.ventanilla || { aid: null, clave: null, paso: null };

function nuevaClave() {
  if (window.crypto && crypto.randomUUID) return crypto.randomUUID().replace(/-/g, "");
  return Array.from({ length: 32 }, () => Math.floor(Math.random() * 16).toString(16)).join("");
}

async function iniciarAtencion() {
  // La clave se conserva hasta que el servidor responde: si la red falla y se pulsa otra vez,
  // el servidor devuelve LA MISMA atención (no se crean dos).
  S.ventanilla.clave = S.ventanilla.clave || nuevaClave();
  let a;
  try { a = await api("/api/ventanilla/atenciones", { method: "POST", body: { clave: S.ventanilla.clave } }); }
  catch (e) { toast("⚠ " + e.message, 4000); return; }
  S.ventanilla = { aid: a.id, clave: null, paso: null };
  S.dirty = false;
  // UN solo repintado. Antes se llamaba con act(), que repintaba otra vez al terminar: con el servidor
  // lento ese segundo repintado llegaba cuando el operador ya escribía y borraba lo tecleado.
  refresh(true, true);
}

function progresoMeta(d) {
  const pct = d.meta_citas_dia ? d.citas_confirmadas_hoy / d.meta_citas_dia : 0;
  return h("div", { class: "card meta-dia", id: "meta-dia" },
    h("div", { class: "meta-cifras" }, h("b", { id: "citas-hoy" }, num(d.citas_confirmadas_hoy)), ` de ${d.meta_citas_dia} citas confirmadas hoy`,
      d.citas_confirmadas_hoy >= d.meta_citas_dia ? h("span", { class: "badge b-ok" }, "Meta cumplida") : null),
    bar(pct),
    h("div", { class: "muted" }, "Solo cuentan las citas con folio comprobado en el portal."));
}

function pasosAtencion(clave) {
  const etiquetas = ["Persona", "Datos", "Fecha", "Confirmación", "Cierre"];
  const actual = etiquetas.indexOf((AT_PASOS.find(([k]) => k === clave) || [, "Persona"])[1]);
  return h("ol", { class: "recorrido", "aria-label": "Pasos de la atención" }, etiquetas.map((t, k) =>
    h("li", { class: k < actual ? "hecho" : k === actual ? "actual" : "", "aria-current": k === actual ? "step" : null }, t)));
}

async function viewVentanilla() {
  S.flujoHook = (id, p) => { S.ventanilla.paso = p || null; refresh(true); };
  const d = await api("/api/ventanilla");
  const m = d.mias;
  const cabecera = [
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Ventanilla"),
      h("div", { class: "muted" }, "Atención presencial. Laboratorio: portal de prueba propio y datos ficticios."))),
    h("div", { class: "grid cols-2" }, progresoMeta(d),
      h("div", { class: "grid kpis" },
        kpi("Atenciones hoy", num(m.atenciones), "tuyas"),
        kpi("Con cita", num(m.con_cita), "folio comprobado"),
        kpi("Sin cita", num(m.sin_cita), "sin días, error o retiro"),
        kpi("Tiempo medio", m.tiempo_medio_s ? fmtDur(Math.round(m.tiempo_medio_s)) : "—", "por atención cerrada"))),
  ];
  if (S.ventanilla.aid) {
    try {
      return h("div", { id: "ventanilla" }, cabecera, await pantallaAtencion(S.ventanilla.aid));
    } catch (err) {
      if (err.status === 404) S.ventanilla.aid = null; else throw err;
    }
  }
  const abiertas = d.abiertas.length ? card("Atenciones abiertas (se retoman donde quedaron)", h("div", { id: "atenciones-abiertas" },
    d.abiertas.map((a) => h("div", { class: "trabajo-item vt interrumpido", "data-atencion": a.id },
      h("div", {}, h("b", {}, a.codigo), " ", badge(a.estado), a.expediente ? ` · ${a.expediente.codigo} · ${a.expediente.titular || ""}` : " · sin expediente todavía",
        h("div", { class: "paso" }, a.siguiente.texto)),
      h("div", { class: "actions" }, h("button", { class: "btn primary", "data-retomar": a.id,
        onclick: () => { S.ventanilla.aid = a.id; refresh(true, true); } }, "Retomar")))))) : null;
  const recientes = card("Tus atenciones de hoy", table([
    { t: "Atención", f: (a) => h("b", {}, a.codigo) },
    { t: "Expediente", f: (a) => a.expediente ? a.expediente.codigo : "—" },
    { t: "Resultado", f: (a) => h("span", {}, badge(a.estado), a.folio ? h("span", { class: "mono" }, " " + a.folio) : null) },
    { t: "Duración", f: (a) => fmtDur(Math.round(a.duracion_s)) }], d.recientes, (a) => { S.ventanilla.aid = a.id; refresh(true, true); }));
  return h("div", { id: "ventanilla" }, cabecera,
    h("div", { class: "section" }, h("button", { class: "btn primary big", id: "btn-nueva-atencion",
      onclick: () => iniciarAtencion() }, "Nueva atención")),
    abiertas, h("div", { class: "section" }, recientes));
}

async function pantallaAtencion(aid) {
  const a = await api(`/api/ventanilla/atenciones/${aid}`);
  const cab = h("div", { class: "card atencion-cab", id: "atencion", "data-atencion": a.id, "data-estado": a.estado },
    h("div", {}, h("b", {}, a.codigo), " ", badge(a.estado), " ", AT_NOMBRE[a.estado] || "",
      a.expediente ? h("span", {}, ` · ${a.expediente.codigo} · ${a.expediente.titular || ""} · ${TRAMITES[a.expediente.tipo_tramite] || a.expediente.tipo_tramite}`) : null),
    pasosAtencion(a.siguiente.clave),
    h("p", { class: "siguiente-paso", id: "siguiente-paso", "data-siguiente": a.siguiente.clave }, "Siguiente: ", a.siguiente.texto),
    h("div", { class: "actions" }, h("button", { class: "btn small ghost", onclick: () => { S.ventanilla.aid = null; refresh(true, true); } },
      "Volver a la lista (la atención queda abierta)")));
  if (!a.abierta) return h("div", {}, cab, resultadoFinal(a));
  if (!a.expediente) return h("div", {}, cab, h("div", { class: "grid cols-2 section" }, buscarPersona(a), registrarPersona(a)));
  const e = await api(`/api/expedientes/${a.expediente.id}`);
  const viva = (e.reservaciones || []).find((r) => ["PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "FECHA_ELEGIDA",
    "CONFIRMANDO", "EN_VERIFICACION"].includes(r.estado));
  const conf = a.reservacion && a.reservacion.estado === "CONFIRMADA" ? a.reservacion : null;
  const flujo = h("div", { class: "card section", id: "flujo" });
  const paso = S.ventanilla.paso || (a.siguiente.clave === "preparar" && !viva ? "preparar" : undefined);
  S.ventanilla.paso = null;
  await pintarFlujo(flujo, e, viva, conf, paso);
  return h("div", {}, cab, flujo, cierre(a, !!conf, viva));
}

function resultadoFinal(a) {
  const conCita = a.estado === "CONFIRMADA";
  return h("div", { class: "card section resultado " + (conCita ? "con-cita" : "sin-cita"), id: "resultado-atencion" },
    h("h2", {}, conCita ? "Cita confirmada" : a.estado === "PENDIENTE" ? "Confirmación en verificación" : "Atención terminada SIN cita"),
    conCita ? h("p", {}, "Folio: ", h("b", { id: "folio-atencion", "data-folio": a.folio }, a.folio)) : null,
    !conCita && a.estado !== "PENDIENTE" ? h("p", {}, `Resultado: ${AT_NOMBRE[a.estado]} (${(a.resultado || "").replace(/_/g, " ").toLowerCase()}). `,
      "Si la persona sigue esperando una cita, el motor seguirá buscando por ella.") : null,
    a.estado === "PENDIENTE" ? h("p", {}, "El motor comprueba con el portal si la cita quedó registrada. No repitas la confirmación.") : null,
    h("p", { class: "muted" }, `Duración: ${fmtDur(Math.round(a.duracion_s))}`),
    h("div", { class: "actions" }, h("button", { class: "btn primary big", id: "btn-siguiente-persona",
      onclick: () => iniciarAtencion() }, "Atender a la siguiente persona")));
}

function cierre(a, conCita, viva) {
  const errs = h("div", { class: "error", role: "alert" });
  const cerrar = async (body) => {
    errs.textContent = "";
    try {
      await api(`/api/ventanilla/atenciones/${a.id}/cerrar`, { method: "POST", body });
      refresh(true, true);
    } catch (err) { errs.textContent = err.message; }
  };
  if (conCita) {
    return h("div", { class: "card section" }, h("div", { class: "actions" },
      h("button", { class: "btn primary big", id: "btn-cerrar-atencion", onclick: () => cerrar({}) }, "Guardar y cerrar la atención")), errs);
  }
  const verif = viva && ["CONFIRMANDO", "EN_VERIFICACION"].includes(viva.estado);
  const sel = h("select", { id: "resultado-cierre" }, [["SIN_DISPONIBILIDAD", "No hay días disponibles"],
    ["ERROR", "Hubo un error"], ["CANCELADA", "La persona se retira"]].map(([k, v]) => h("option", { value: k }, v)));
  const motivo = h("input", { id: "motivo-cierre", maxlength: 32, placeholder: "Motivo breve (sin datos personales)", autocomplete: "off" });
  const cerrarExp = h("input", { type: "checkbox", id: "cerrar-expediente" });
  return h("details", { class: "card section", id: "cierre-sin-cita", open: verif ? true : null },
    h("summary", {}, verif ? "Cerrar la atención (la confirmación queda en verificación)" : "Terminar sin cita"),
    verif ? h("p", { class: "muted" }, "La atención quedará PENDIENTE y se actualizará sola cuando el motor obtenga la respuesta del portal.") :
      h("p", { class: "muted" }, "Sin cita, el expediente vuelve a la cola y el motor sigue buscando disponibilidad para la persona."),
    h("div", { class: "form" }, verif ? null : field("Resultado", sel), verif ? null : field("Motivo", motivo),
      verif ? null : h("label", { class: "check-line" }, cerrarExp, "La persona ya no quiere la cita: cerrar también su expediente"),
      h("button", { class: "btn", id: "btn-cerrar-sin-cita", onclick: () => cerrar(verif ? { resultado: "ERROR" } :
        { resultado: sel.value, motivo: motivo.value, cerrar_expediente: sel.value === "CANCELADA" && cerrarExp.checked }) }, "Cerrar la atención")),
    errs);
}

function buscarPersona(a) {
  const res = h("div", { id: "resultados-busqueda" });
  const errs = h("div", { class: "error", role: "alert" });
  const pintar = (lista) => res.replaceChildren(lista.length ? h("div", {}, lista.map((x) => h("div", { class: "trabajo-item vt", "data-encontrado": x.id },
    h("div", {}, h("b", {}, x.codigo), " ", badge(x.estado), ` · ${x.titular || ""} · ${TRAMITES[x.tipo_tramite] || x.tipo_tramite} · ${x.region}${x.sucursal ? " · " + x.sucursal : ""}`,
      x.motivo ? h("div", { class: x.puede_atenderse ? "muted" : "error" }, x.motivo) : null),
    h("div", { class: "actions" }, x.puede_atenderse ? h("button", { class: "btn primary", "data-atender": x.id,
      onclick: () => vincular(a, { expediente_id: x.id }, errs) }, "Atender") : null))))
    : h("div", { class: "empty" }, "No hay ningún expediente con esos datos. Regístralo como persona nueva."));
  const porCodigo = h("form", { class: "form", id: "form-buscar-codigo", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    try { pintar((await api(`/api/ventanilla/atenciones/${a.id}/buscar`, { method: "POST", body: formData(ev.target) })).expedientes); }
    catch (err) { errs.textContent = err.message; }
  } }, field("Código del expediente", h("input", { name: "codigo", required: true, maxlength: 32, placeholder: "EXP-000123", autocomplete: "off" }), "full"),
    h("button", { class: "btn" }, "Buscar"));
  const porTitular = h("form", { class: "form", id: "form-buscar-titular", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    try { pintar((await api(`/api/ventanilla/atenciones/${a.id}/buscar`, { method: "POST", body: formData(ev.target) })).expedientes); }
    catch (err) { errs.textContent = err.message; }
  } }, field("Nombre completo", h("input", { name: "nombre", required: true, maxlength: 128, autocomplete: "off" }), "full"),
    field("Fecha de nacimiento", h("input", { name: "fecha_nacimiento", type: "date", required: true })),
    field("Trámite", h("select", { name: "tipo_tramite" }, Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k }, v)))),
    h("button", { class: "btn" }, "Buscar"));
  return card("¿Ya tiene expediente?", h("p", { class: "muted" }, "Busca por el código o por nombre, fecha de nacimiento y trámite (coincidencia exacta)."),
    porCodigo, porTitular, errs, res);
}

async function vincular(a, body, errs, onDuplicado) {
  errs.textContent = "";
  try {
    await api(`/api/ventanilla/atenciones/${a.id}/expediente`, { method: "POST", body });
    S.dirty = false;
    refresh(true, true);
  } catch (err) {
    const d = err.detalle || {};
    if (d.duplicado && onDuplicado) { onDuplicado(d.duplicado, err.message); return; }
    errs.replaceChildren(err.message, d.errores ? h("ul", {}, Object.entries(d.errores).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
  }
}

function registrarPersona(a) {
  const errs = h("div", { class: "error full", role: "alert" });
  const dup = h("div", { id: "aviso-duplicado", class: "full" });
  const inp = (name, attrs) => h("input", { name, autocomplete: "off", ...attrs });
  const form = h("form", { class: "form", id: "form-persona", onsubmit: (ev) => {
    ev.preventDefault();
    const d = formData(ev.target);
    if (d.sucursales_alternativas) d.sucursales_alternativas = d.sucursales_alternativas.split(/[,;\s]+/).filter(Boolean);
    vincular(a, { datos: d }, errs, (lista, msg) => dup.replaceChildren(h("p", { class: "error" }, msg),
      ...lista.map((x) => h("div", { class: "trabajo-item vt" }, h("div", {}, h("b", {}, x.codigo), " ", badge(x.estado), ` · ${x.titular || ""}`,
        x.motivo ? h("div", { class: "muted" }, x.motivo) : null),
        x.puede_atenderse ? h("button", { type: "button", class: "btn primary", "data-atender": x.id,       // dentro del formulario: no debe enviarlo
          onclick: () => vincular(a, { expediente_id: x.id }, errs) }, "Atender este") : null))));
  } },
    h("p", { class: "muted full" }, "Solo lo necesario para la cita. Datos ficticios en el laboratorio: teléfono 555…, correo @ejemplo.test."),
    field("Nombre completo", inp("nombre", { required: true, maxlength: 128 }), "full"),
    field("Fecha de nacimiento", inp("fecha_nacimiento", { type: "date", required: true })),
    field("Registro previo", inp("id_registro_previo", { maxlength: 32, placeholder: "REG000001" })),
    field("Teléfono", inp("telefono", { maxlength: 20, inputmode: "tel", required: true })),
    field("Confirma el teléfono", inp("telefono_confirmacion", { maxlength: 20, inputmode: "tel", required: true })),
    field("Correo", inp("correo", { type: "email", maxlength: 128, required: true })),
    field("Confirma el correo", inp("correo_confirmacion", { type: "email", maxlength: 128, required: true })),
    field("Trámite", h("select", { name: "tipo_tramite" }, Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k }, v)))),
    field("Región", regionSelect("region")),
    field("Sucursal preferida", inp("sucursal", { maxlength: 32, placeholder: "NOR-1" })),
    field("Otras sucursales que le sirven", inp("sucursales_alternativas", { maxlength: 170, placeholder: "NOR-2" })),
    h("button", { class: "btn primary" }, "Registrar y continuar"), errs, dup);
  return card("Persona nueva", form);
}
