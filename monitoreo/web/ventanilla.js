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
    h("div", { class: "muted" }, labOn() ? "Solo cuentan las citas con folio comprobado en el portal."
      : "Cuentan las citas con folio registrado: comprobadas en el portal o declaradas por una persona (portal oficial)."));
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
      h("div", { class: "muted" }, labOn() ? "Atención presencial. Laboratorio: portal de prueba propio y datos ficticios."
        : "Atención presencial. La cita se agenda a mano en el portal oficial y aquí se registra el folio."))),
    h("div", { class: "grid cols-2" }, progresoMeta(d),
      h("div", { class: "grid kpis" },
        kpi("Atenciones hoy", num(m.atenciones), "tuyas"),
        kpi("Con cita", num(m.con_cita), labOn() ? "folio comprobado" : "con folio"),
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
  let plan = null;
  try { plan = await api("/api/planificador/jornada"); } catch (_) { plan = null; }   // fuera del laboratorio no existe
  // v2.3: el administrador ve también el observador y los posibles duplicados por revisar
  const [obs, dups] = isAdmin() ? await Promise.all([tarjetaObservador(), revisionDuplicados()]) : [null, null];
  return h("div", { id: "ventanilla" }, cabecera,
    h("div", { class: "section" }, h("button", { class: "btn primary big", id: "btn-nueva-atencion",
      onclick: () => iniciarAtencion() }, "Nueva atención")),
    abiertas, dups ? h("div", { class: "section" }, dups) : null,
    plan ? h("div", { class: "section" }, planDelDia(plan, d.abiertas)) : null, obs ? h("div", { class: "section" }, obs) : null,
    h("div", { class: "section" }, recientes));
}

// ---------------------------------------------------------------- v2.1 · P1: plan de la jornada dentro de Ventanilla
// Lo que el planificador eligió para hoy, sus coincidencias y lo que sigue en espera. El operador prepara
// la operación de una coincidencia y la continúa en ESTA misma pantalla de atención (mismo flujo de
// siempre: campos manuales, día, confirmación humana, folio comprobado). El administrador crea el plan,
// lo llena y amplía su capacidad. Solo códigos, estados y fechas: ningún dato personal.
Object.assign(BADGE, { EN_ESPERA: "b-info", OPERACION_PREPARADA: "b-warn", CONFIRMADO: "b-ok", RETIRADO: "",
  FIN_JORNADA: "", EN_PLAN: "b-info" });
const PLAN_NOMBRE = { EN_ESPERA: "En espera", ASIGNADO: "Coincidencia", OPERACION_PREPARADA: "Operación preparada",
  CONFIRMADO: "Cita confirmada", RETIRADO: "Retirado", FIN_JORNADA: "Terminó la jornada" };
const planBadge = (e) => h("span", { class: "badge " + (BADGE[e] || ""), "data-estado-plan": e }, PLAN_NOMBRE[e] || e);

function rangoFechas(f) {
  if (!f || !f.n) return "—";
  if (f.n === 1) return f.desde;
  return f.continuas ? `${f.desde} a ${f.hasta} (${f.n} días)` : `${f.n} fechas entre ${f.desde} y ${f.hasta}`;
}

async function abrirEnAtencion(expId, atencionId, abiertas) {
  // Si ya hay una atención abierta para ese expediente se retoma; si no, se abre una y se vincula.
  const ya = atencionId || ((abiertas || []).find((a) => a.expediente && a.expediente.id === expId) || {}).id;
  if (ya) { S.ventanilla.aid = ya; refresh(true, true); return; }
  const a = await api("/api/ventanilla/atenciones", { method: "POST", body: { clave: nuevaClave() } });
  await api(`/api/ventanilla/atenciones/${a.id}/expediente`, { method: "POST", body: { expediente_id: expId } });
  S.ventanilla = { aid: a.id, clave: null, paso: null };
  refresh(true, true);
}

async function prepararDelPlan(el, abiertas, errs) {
  errs.textContent = "";
  try {
    await api(`/api/planificador/elementos/${el.id}/preparar`, { method: "POST" });
    await abrirEnAtencion(el.expediente_id, null, abiertas);
  } catch (err) { errs.textContent = err.message; refresh(true); }
}

async function detallePlan(el) {
  const d = await api(`/api/planificador/elementos/${el.id}`);
  const x = d.explicacion || {};
  const lista = (t, xs) => xs && xs.length ? h("div", {}, h("h3", {}, t), h("ul", {}, xs.map((y) =>
    h("li", {}, `${y.codigo || "—"} · prioridad ${y.prioridad}` + (y.motivo ? ` · ${y.motivo}` : ""))))) : null;
  openDrawer(h("div", { id: "detalle-plan", "data-elemento": d.id },
    h("h2", {}, d.codigo || "Expediente"), planBadge(d.estado),
    h("p", {}, `Trámite: ${TRAMITES[d.tramite] || d.tramite} · Prioridad ${d.prioridad} · alta n.º ${d.orden}`),
    h("p", {}, "Sucursales aceptables: ", h("b", { id: "detalle-sucursales" }, d.sucursales.join(", "))),
    h("div", {}, h("h3", {}, `Fechas aceptables (${d.fechas_aceptables.length})`),
      h("div", { class: "fechas-aceptables", id: "detalle-fechas" }, d.fechas_aceptables.map((f) =>
        h("span", { class: "badge" + (f === d.fecha_asignada ? " b-acc" : "") }, f)))),
    d.fecha_asignada ? h("p", {}, "Día asignado: ", h("b", {}, `${d.fecha_asignada} · ${d.sucursal_asignada}`)) : null,
    x.por_que ? h("div", {}, h("h3", {}, "Por qué"), h("p", { id: "detalle-por-que" }, x.por_que),
      x.unidades ? h("p", { class: "muted" }, `Unidades vistas: ${x.unidades.libres_vistas ?? "sin cifra (solo color)"} · comprometidas: ${x.unidades.comprometidas} · cupo: ${x.unidades.cupo}`) : null,
      h("p", { class: "muted" }, "Regla: " + (x.regla || ""))) : null,
    x.devuelto ? h("p", { class: "warn-text" }, "Volvió a la espera: " + x.devuelto.motivo) : null,
    lista("Siguen en espera", x.compatibles_en_espera), lista("Descartados", x.descartados)));
}

function planDelDia(j, abiertas) {
  const errs = h("div", { class: "error", role: "alert", id: "plan-error" });
  const accion = async (fn) => { errs.textContent = ""; try { await fn(); refresh(true); } catch (err) { errs.textContent = err.message; } };
  if (!j.plan) {
    return card("Plan del día", h("div", { id: "plan-dia", "data-plan": "ninguno" },
      h("p", { class: "muted" }, `No hay plan para la jornada ${j.jornada}.`),
      j.administrar ? h("button", { class: "btn primary", id: "btn-crear-plan", onclick: () => accion(() =>
        api("/api/planes", { method: "POST", body: { fecha_jornada: j.jornada } }).then((p) =>
          api(`/api/planes/${p.id}/seleccionar`, { method: "POST", body: {} }))) }, "Crear el plan y elegir expedientes") :
        h("p", { class: "muted" }, "Lo crea un administrador."), errs));
  }
  const p = j.plan;
  const capacidad = h("input", { type: "number", id: "plan-capacidad", min: p.capacidad, max: 50, value: p.capacidad, class: "small" });
  const admin = j.administrar ? h("div", { class: "actions", id: "plan-admin" },
    h("label", {}, "Capacidad ", capacidad),
    h("button", { class: "btn", id: "btn-ampliar-plan", onclick: () => accion(() =>
      api(`/api/planes/${p.id}/ampliar`, { method: "POST", body: { capacidad: Number(capacidad.value) } })) }, "Ampliar"),
    h("button", { class: "btn", id: "btn-llenar-plan", disabled: p.vivos >= p.capacidad ? true : null, onclick: () => accion(() =>
      api(`/api/planes/${p.id}/seleccionar`, { method: "POST", body: {} })) }, "Completar con expedientes listos")) : null;
  const filas = table([
    { t: "Expediente", f: (e) => h("b", { "data-plan-codigo": e.codigo }, e.codigo || "—") },
    { t: "Prioridad", num: true, f: (e) => e.prioridad },
    { t: "Estado", f: (e) => h("span", {}, planBadge(e.estado), e.folio ? h("span", { class: "mono" }, " " + e.folio) : null) },
    { t: "Sucursales", f: (e) => e.sucursales.join(", ") },
    { t: "Fechas aceptables", f: (e) => rangoFechas(e.fechas) },
    { t: "Día asignado", f: (e) => e.fecha_asignada ? h("span", {}, `${e.fecha_asignada} · ${e.sucursal_asignada}`,
      e.dia && e.dia.libres ? h("div", { class: "muted" }, `${e.dia.libres} unidad(es) libres vistas`) : null) :
      (e.devuelto ? h("span", { class: "muted" }, "volvió a la espera") : "—") },
    { t: "", f: (e) => h("div", { class: "actions" },
      e.puede_preparar ? h("button", { class: "btn primary", "data-preparar-plan": e.id, "data-fecha-asignada": e.fecha_asignada,
        onclick: (ev) => { ev.stopPropagation(); prepararDelPlan(e, abiertas, errs); } }, "Preparar operación") : null,
      e.puede_abrir && e.mio ? h("button", { class: "btn primary", "data-abrir-plan": e.id,
        onclick: (ev) => { ev.stopPropagation(); abrirEnAtencion(e.expediente_id, e.atencion_id, abiertas).catch((err) => { errs.textContent = err.message; }); } }, "Continuar") : null,
      j.administrar ? h("button", { class: "btn ghost small", "data-ver-expediente": e.expediente_id,
        onclick: (ev) => { ev.stopPropagation(); showExpediente(e.expediente_id); } }, "Ver expediente") : null) }],
    p.elementos, (e) => detallePlan(e));
  return card(`Plan del día · ${j.jornada}`, h("div", { id: "plan-dia", "data-plan": p.id },
    h("div", { class: "grid kpis" },
      kpi("En el plan", h("span", { id: "plan-vivos" }, `${p.vivos} de ${p.capacidad}`), "expedientes activos / capacidad"),
      kpi("Coincidencias", h("span", { id: "plan-coincidencias" }, num(p.coincidencias)), "días encontrados, listos para preparar"),
      kpi("Pendientes", h("span", { id: "plan-pendientes" }, num(p.pendientes)), "esperan un día compatible"),
      kpi("Confirmadas", num(p.por_estado.CONFIRMADO || 0), "con folio comprobado")),
    h("p", { class: "muted" }, "Pulsa una fila para ver sus fechas aceptables y por qué se le asignó (o no) un día. ",
      "Los expedientes del plan solo los asigna el plan: la cola general no los toma."),
    admin, filas, errs));
}

async function pantallaAtencion(aid) {
  const a = await api(`/api/ventanilla/atenciones/${aid}`);
  const cab = h("div", { class: "card atencion-cab", id: "atencion", "data-atencion": a.id, "data-estado": a.estado },
    h("div", {}, h("b", {}, a.codigo), " ", badge(a.estado), " ", AT_NOMBRE[a.estado] || "",
      a.expediente ? h("span", {}, ` · ${a.expediente.codigo} · ${a.expediente.titular || ""} · ${TRAMITES[a.expediente.tipo_tramite] || a.expediente.tipo_tramite}`) : null),
    pasosAtencion(a.siguiente.clave),
    h("p", { class: "siguiente-paso", id: "siguiente-paso", "data-siguiente": a.siguiente.clave }, "Siguiente: ", a.siguiente.texto),
    h("div", { class: "actions" }, h("button", { class: "btn small ghost", onclick: () => { S.ventanilla.aid = null; refresh(true, true); } },
      a.abierta ? "Volver a la lista (la atención queda abierta)" : "Volver a la lista")));
  // v2.3: el historial se pide solo cuando este usuario puede verlo (así no se generan 404 que confundan):
  // el administrador siempre; el operador mientras el expediente está a su cargo o cuando ya tiene su cita.
  const aCargo = a.expediente && (isAdmin() || (S.user.operator_id != null && a.expediente.operador_id === S.user.operator_id));
  const enEspera = a.expediente && (ESPERA_EXP.includes(a.expediente.estado) || !aCargo);
  const verHist = aCargo;
  const hist = !a.expediente ? null : verHist ? await historialExpediente(a.expediente.id, !a.abierta) : sinHistorial();
  if (!a.abierta) return h("div", {}, cab, resultadoFinal(a), hist);
  // v2.3: una atención abierta por error (o sin persona) también se puede cerrar desde aquí
  if (!a.expediente) return h("div", {}, cab, h("div", { class: "grid cols-2 section" }, buscarPersona(a), registrarPersona(a)), cierre(a, false, null));
  // v2.2: sin consentimiento vigente no se prepara nada: primero se registra (la persona está presente)
  if (a.siguiente.clave === "consentimiento") return h("div", {}, cab, tarjetaConsentimiento(a), cierre(a, false, null), hist);
  // v2.3: un expediente en revisión (posible duplicado) o incompleto no queda asignado al operador, así que
  // su ficha no se puede abrir; antes la pantalla volvía a la lista SIN decir nada. Ahora se explica qué pasa.
  if (enEspera && !isAdmin()) return h("div", {}, cab, expedienteEnEspera(a), cierre(a, false, null), hist);
  let e;
  try { e = await api(`/api/expedientes/${a.expediente.id}`); }
  catch (err) {
    if (err.status !== 404) throw err;
    return h("div", {}, cab, expedienteEnEspera(a), cierre(a, false, null), hist);
  }
  const viva = (e.reservaciones || []).find((r) => ["PREPARADA", "DATOS_ENVIADOS", "RETENIDA", "FECHA_ELEGIDA",
    "CONFIRMANDO", "EN_VERIFICACION"].includes(r.estado));
  const conf = a.reservacion && a.reservacion.estado === "CONFIRMADA" ? a.reservacion : null;
  const flujo = h("div", { class: "card section", id: "flujo" });
  // v2.1: un intento ya PREPARADO (p. ej. la operación preparada desde «Plan del día») se retoma mostrando
  // directamente la revisión con los campos manuales, sin pedir otra vez «Preparar formulario».
  const paso = S.ventanilla.paso || (["preparar", "enviar"].includes(a.siguiente.clave) && (!viva || viva.estado === "PREPARADA")
    ? "preparar" : undefined);
  S.ventanilla.paso = null;
  await pintarFlujo(flujo, e, viva, conf, paso);
  // v2.3: sin operación con el portal todavía, la persona puede quedarse en el plan del día
  const alPlan = !conf && (!viva || viva.estado === "PREPARADA") && ["preparar", "enviar"].includes(a.siguiente.clave)
    && (!a.consentimiento || !a.consentimiento.exigido || a.consentimiento.vigente) ? tarjetaAlPlan(a) : null;
  const auto = !conf && labOn() ? await tarjetaAutorrelleno(a) : null;
  return h("div", {}, cab, barraConsentimiento(a), auto, flujo, alPlan, cierre(a, !!conf, viva), hist);
}

// ---------------------------------------------------------------- laboratorio · autorrellenado con Forms
// El botón solo aparece si el motor lo tiene habilitado (MONITOREO_AUTORRELLENO_LAB=1 y Forms instalado).
// El servidor decide con sus reglas (consentimiento, región, estado): la pantalla solo muestra el motivo.
async function tarjetaAutorrelleno(a) {
  let st;
  try { st = await api(`/api/autorrelleno/atenciones/${a.id}`); } catch (_) { return null; }
  if (!st || !st.habilitado) return null;
  const caja = h("div", { class: "card section", id: "autorrelleno", "data-disponible": st.disponible ? "si" : "no",
    "data-codigo": st.codigo || "" });
  const pintar = (est, corrida) => {
    const c = corrida || est.corrida;
    const enCurso = !!(c && c.en_curso);
    const errs = h("div", { class: "error", role: "alert", id: "autorrelleno-error" });
    const boton = h("button", { class: "btn primary", id: "btn-autorrellenar", disabled: (!est.disponible || enCurso) || null,
      onclick: async () => {
        boton.disabled = true; errs.textContent = "";
        try { const r = await api(`/api/autorrelleno/atenciones/${a.id}`, { method: "POST" }); pintar(est, r); seguir(); }
        catch (err) { errs.textContent = err.message; caja.dataset.codigo = (err.detalle && err.detalle.codigo) || ""; boton.disabled = false; }
      } }, "Autorrellenar formulario");
    caja.dataset.estado = c ? c.estado : "";
    caja.replaceChildren(...[      // replaceChildren escribiría «null» como texto: se filtran los vacíos
      h("h2", {}, "Autorrellenar formulario (Forms · laboratorio)"),
      est.externo ? h("p", { class: "warn", id: "autorrelleno-externo" }, `Formulario EXTERNO autorizado: ${est.sitio}. `,
        "Solo datos ficticios en este laboratorio. Los códigos se traducen con la tabla aprobada; lo que no tenga fila lo completas tú.") : null,
      h("p", { class: "muted" }, est.externo ? "Abre el formulario del sitio externo en una ventana del navegador y Forms lo rellena con "
        : "Abre el formulario del portal de laboratorio en una ventana del navegador y Forms lo rellena con ",
        "los datos de este expediente. Forms no envía nada: tú revisas, tecleas los campos manuales y decides. ",
        "Solo viajan los campos que pide el formulario; el nombre y los campos manuales no."),
      est.coincidencia ? coincidenciaForms(est.coincidencia) : null,
      !est.disponible && est.motivo ? h("p", { class: "error", id: "autorrelleno-motivo" }, est.motivo) : null,
      h("div", { class: "actions" }, boton), errs,
      c ? resumenAutorrelleno(c) : null,
      est.coincidencia && est.coincidencia.lista_para_confirmacion ? folioVerificado(est.coincidencia, a) : null].filter((x) => x));
  };
  let timer = null;
  const seguir = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      if (!document.body.contains(caja)) return;
      try {
        const est = await api(`/api/autorrelleno/atenciones/${a.id}`);
        pintar(est);
        if (est.corrida && est.corrida.en_curso) seguir();
      } catch (_) { /* se reintenta al refrescar la pantalla */ }
    }, 2000);
  };
  pintar(st);
  if (st.corrida && st.corrida.en_curso) seguir();
  return caja;
}

// Eslabón coincidencia -> Forms -> folio: la coincidencia del plan viaja con el autorrellenado y el folio que la
// persona obtuvo en el portal se COMPRUEBA con el portal antes de marcar la cita (nada se confirma solo).
function coincidenciaForms(k) {
  const estado = k.confirmada ? "Cita CONFIRMADA (folio comprobado con el portal)"
    : k.lista_para_confirmacion ? "Lista para confirmación supervisada: todavía NO es una cita"
      : "Coincidencia preparada: se entregará a Forms al autorrellenar";
  return h("div", { id: "autorrelleno-coincidencia", "data-lista": k.lista_para_confirmacion ? "si" : "no",
    "data-confirmada": k.confirmada ? "si" : "no" },
    h("p", {}, h("b", {}, "Coincidencia del plan: "), `${k.fecha} en ${k.sucursal}. `, h("span", { class: "badge " + (k.confirmada ? "b-ok" : "b-warn") }, estado)),
    k.folio ? h("p", {}, "Folio: ", h("b", { id: "autorrelleno-folio" }, k.folio)) : null);
}

function folioVerificado(k, a) {
  const f1 = h("input", { id: "folio-forms", autocomplete: "off", placeholder: "Folio que muestra el portal" });
  const f2 = h("input", { id: "folio-forms-2", autocomplete: "off", placeholder: "Vuelve a teclearlo" });
  const errs = h("div", { class: "error", role: "alert", id: "folio-forms-error" });
  const boton = h("button", { class: "btn primary", id: "btn-folio-forms", onclick: async () => {
    boton.disabled = true; errs.textContent = "";
    try {
      await api(`/api/autorrelleno/reservaciones/${k.reservacion_id}/folio`, { method: "POST", body: { folio: f1.value, folio_confirmacion: f2.value } });
      toast("Folio comprobado con el portal: cita confirmada", 4000);
      refresh(true, true);
    } catch (err) { errs.textContent = err.message; boton.disabled = false; }
  } }, "Comprobar folio con el portal");
  if (!k.verificable) return h("p", { class: "muted", id: "folio-forms-sin-api" }, "Este portal no permite comprobar folios: registra la cita con la confirmación asistida.");
  return h("div", { class: "section", id: "folio-forms-caja" },
    h("p", { class: "muted" }, `Con la persona, en la ventana del portal: teclea los campos manuales, elige el ${k.fecha} en ${k.sucursal} y confirma. Después escribe aquí el folio dos veces.`),
    h("label", { for: "folio-forms" }, "Folio"), f1, h("label", { for: "folio-forms-2" }, "Confirma el folio"), f2,
    h("div", { class: "actions" }, boton), errs);
}

function resumenAutorrelleno(c) {
  const i = c.informe;
  const filas = i ? [
    ...i.campos.map((x) => h("li", { "data-campo": x.campo, class: x.correcto ? "" : "error" },
      `${x.correcto ? "✓" : "✗"} ${x.etiqueta}: ${x.correcto ? "escrito y verificado" : x.estado}`)),
    ...i.confirmaciones.map((x) => h("li", { "data-confirmacion": x.campo, class: x.coinciden ? "" : "error" },
      `${x.coinciden ? "✓" : "✗"} ${x.etiqueta}: ${x.coinciden ? "coincide" : "NO coincide"}`)),
    ...i.sin_dato.map((x) => h("li", { class: "muted" }, `— ${x.etiqueta}: el expediente no lo trae; complétalo tú`)),
    ...(i.sin_equivalencia || []).map((x) => h("li", { class: "muted", "data-sin-equivalencia": x.campo },
      `— ${x.etiqueta}: el código no está en la tabla aprobada; elígelo tú en el formulario`)),
    ...i.manuales.map((x) => h("li", { class: "muted" }, `— ${x}: captura manual del operador`)),
  ] : [];
  return h("div", { id: "autorrelleno-resultado", "data-estado": c.estado },
    h("p", {}, badge(c.estado), " ", c.mensaje, c.en_curso ? "" : ` (${c.codigo || c.estado})`),
    filas.length ? h("ul", { class: "lista-autorrelleno" }, filas) : null,
    c.diagnostico && c.diagnostico.length ? h("p", { class: "muted", id: "autorrelleno-diagnostico" }, `Diagnóstico de Forms: ${c.diagnostico.join(" · ")}`) : null,
    i ? h("p", { class: "muted" }, `Envío por Forms: ${i.envio_realizado_por_conector ? "SÍ" : "no"} · consentimiento ${c.consentimiento}`) : null);
}

const ESPERA_EXP = ["NUEVO", "INCOMPLETO", "EN_VALIDACION", "REQUIERE_REVISION"];   // no quedan a cargo del operador
const sinHistorial = () => h("div", { class: "card section muted", id: "historial-expediente", "data-disponible": "no" },
  "El historial de este expediente lo consulta un administrador (ya no está a tu cargo).");

function expedienteEnEspera(a) {
  const x = a.expediente;
  const revision = x.estado === "REQUIERE_REVISION";
  return h("div", { class: "card section", id: "expediente-en-espera", "data-estado-expediente": x.estado },
    h("h2", {}, revision ? "Posible duplicado: lo revisa un administrador" : `Expediente ${x.estado.replace(/_/g, " ").toLowerCase()}`),
    revision ? h("p", {}, `El expediente ${x.codigo} se parece mucho a otro (nombre casi igual y la misma fecha de nacimiento). `,
      "No entra a la cola ni al plan hasta que un administrador decida en su Ventanilla («Posibles duplicados por revisar») ",
      "si es la misma persona o son personas distintas.") :
      h("p", {}, x.estado === "CERRADO" ? "El expediente ya está cerrado: no habrá más operaciones con él. Cierra esta atención."
        : x.faltan && x.faltan.length ? `Faltan datos: ${x.faltan.join(", ")}.`
          : ESPERA_EXP.includes(x.estado) ? "Todavía no está listo para operar." : "Ahora no está a tu cargo (lo tiene el plan del día u otro operador)."),
    h("p", { class: "muted" }, "Puedes cerrar esta atención (abajo) y retomarla cuando se resuelva; la persona no pierde su lugar."));
}

// ---------------------------------------------------------------- v2.2 · consentimiento de la persona
const MEDIOS_CONS = { FIRMA_PAPEL: "Formato firmado en papel", FIRMA_DIGITAL: "Firma digital o electrónica",
  PRESENCIAL_VENTANILLA: "Firmado aquí, en ventanilla" };
const MEDIOS_REV = { PRESENCIAL: "En persona", ESCRITO: "Por escrito", TELEFONO: "Por teléfono", CORREO: "Por correo" };
const MOTIVOS_REV = { PERSONA_LO_PIDE: "La persona lo pidió", YA_NO_REQUIERE: "Ya no requiere la cita",
  ERROR_REGISTRO: "Se registró por error", OTRO: "Otro" };

async function huellaArchivo(file) {
  // La huella se calcula EN ESTE EQUIPO: el documento nunca se envía al motor.
  const d = await crypto.subtle.digest("SHA-256", await file.arrayBuffer());
  return [...new Uint8Array(d)].map((b) => b.toString(16).padStart(2, "0")).join("");
}

function camposConsentimiento(requerido) {
  const huella = h("input", { name: "cons_evidencia_sha256", maxlength: 64, autocomplete: "off", required: requerido || null,
    pattern: "[0-9a-fA-F]{64}", placeholder: "64 caracteres hexadecimales", id: "cons-huella" });
  const nota = h("span", { class: "muted", id: "cons-huella-nota" });
  const archivo = h("input", { type: "file", id: "cons-archivo", accept: ".pdf,.jpg,.jpeg,.png", onchange: async (ev) => {
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    if (!(window.crypto && crypto.subtle)) {
      nota.textContent = "Este navegador no puede calcularla: en Windows usa «certutil -hashfile ARCHIVO SHA256».";
      return;
    }
    huella.value = await huellaArchivo(f);
    nota.textContent = "Huella calculada en este equipo; el documento no se envía.";
  } });
  return [
    h("p", { class: "muted full" }, "La persona autoriza que se gestione su cita con sus datos. Registra cómo lo autorizó y la ",
      "huella del formato firmado (escaneado o foto). El documento se queda en tu archivo; aquí solo se guarda su huella."),
    field("Cómo lo autorizó", h("select", { name: "cons_medio", required: requerido || null, id: "cons-medio" },
      h("option", { value: "" }, requerido ? "Elige…" : "Lo registro después"),
      Object.entries(MEDIOS_CONS).map(([k, v]) => h("option", { value: k }, v)))),
    field("Versión del aviso de privacidad mostrado", h("input", { name: "cons_aviso_privacidad", maxlength: 32, placeholder: "AP-2026-01",
      autocomplete: "off", required: requerido || null, pattern: "[A-Za-z0-9._\\-]{1,32}", id: "cons-aviso" })),
    field("Documento firmado (solo para calcular la huella)", archivo, "full"),
    field("Huella SHA-256 del documento", huella, "full"), h("div", { class: "full" }, nota),
    field("Dónde está el original", h("input", { name: "cons_evidencia_ref", maxlength: 64, placeholder: "Carpeta 2026-11 hoja 14",
      autocomplete: "off", id: "cons-ref" }), "full"),
  ];
}

function sacarConsentimiento(d) {
  const c = {};
  for (const k of Object.keys(d)) if (k.startsWith("cons_")) { c[k.slice(5)] = d[k]; delete d[k]; }
  return c.medio ? c : null;
}

function tarjetaConsentimiento(a) {
  const errs = h("div", { class: "error full", role: "alert" });
  const bq = (a.consentimiento && a.consentimiento.bloqueo) || {};
  const form = h("form", { class: "form", id: "form-consentimiento", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const boton = ev.target.querySelector("button[type=submit]");
    boton.disabled = true;                       // contra el doble clic
    try {
      await api(`/api/ventanilla/atenciones/${a.id}/consentimiento`, { method: "POST", body: sacarConsentimiento(formData(ev.target)) });
      S.dirty = false; refresh(true, true);
    } catch (err) {
      const d = err.detalle || {};
      errs.replaceChildren(err.message, d.errores ? h("ul", {}, Object.entries(d.errores).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
    } finally { boton.disabled = false; }
  } }, ...camposConsentimiento(true), h("button", { class: "btn primary", type: "submit", id: "btn-registrar-consentimiento" },
    "Registrar consentimiento y continuar"), errs);
  return h("div", { class: "card section", id: "paso-consentimiento", "data-bloqueo": bq.codigo || "" },
    h("h2", {}, "Consentimiento de la persona"),
    bq.mensaje ? h("p", { class: bq.codigo === "CONSENTIMIENTO_REVOCADO" ? "error" : "muted" }, bq.mensaje) : null, form);
}

function barraConsentimiento(a) {
  const c = a.consentimiento;
  if (!c || !c.exigido) return null;
  if (!c.vigente) {
    return h("div", { class: "card section error", id: "consentimiento-bloqueo" },
      (c.bloqueo && c.bloqueo.mensaje) || "Sin consentimiento vigente: no se permiten nuevas operaciones.");
  }
  const errs = h("div", { class: "error", role: "alert" });
  const medio = h("select", { id: "rev-medio" }, Object.entries(MEDIOS_REV).map(([k, v]) => h("option", { value: k }, v)));
  const motivo = h("select", { id: "rev-motivo" }, Object.entries(MOTIVOS_REV).map(([k, v]) => h("option", { value: k }, v)));
  const seguro = h("input", { type: "checkbox", id: "rev-seguro" });
  const boton = h("button", { class: "btn danger", id: "btn-revocar", onclick: async () => {
    errs.textContent = "";
    if (!seguro.checked) { errs.textContent = "Marca la casilla para confirmar que la persona lo pidió."; return; }
    boton.disabled = true;
    try {
      await api(`/api/consentimientos/${c.id}/revocar`, { method: "POST", body: { medio: medio.value, motivo: motivo.value } });
      refresh(true, true);
    } catch (err) { errs.textContent = err.message; boton.disabled = false; }
  } }, "Registrar la revocación");
  return h("details", { class: "card section", id: "consentimiento-vigente", "data-consentimiento": c.codigo },
    h("summary", {}, `Consentimiento ${c.codigo} vigente (${MEDIOS_CONS[c.medio] || c.medio})`),
    h("p", { class: "muted" }, "Si la persona retira su autorización, regístralo aquí. Es definitivo: se detiene todo lo pendiente ",
      "de su expediente. Una cita ya confirmada NO se cancela sola: acuérdalo con la persona."),
    h("div", { class: "form" }, field("Cómo lo pidió", medio), field("Motivo", motivo),
      h("label", { class: "check-line full" }, seguro, "La persona pidió retirar su consentimiento"), boton), errs);
}

function resultadoFinal(a) {
  const conCita = a.estado === "CONFIRMADA";
  return h("div", { class: "card section resultado " + (conCita ? "con-cita" : "sin-cita"), id: "resultado-atencion" },
    h("h2", {}, conCita ? "Cita confirmada" : a.estado === "PENDIENTE" ? "Confirmación en verificación" : "Atención terminada SIN cita"),
    conCita ? h("p", {}, "Folio: ", h("b", { id: "folio-atencion", "data-folio": a.folio }, a.folio)) : null,
    !conCita && a.estado !== "PENDIENTE" && a.resultado === "CONSENTIMIENTO_REVOCADO" ? h("p", { id: "atencion-revocada" },
      "La persona retiró su consentimiento: su expediente se cerró y no habrá más operaciones con sus datos.") : null,
    a.resultado === "EN_PLAN_DEL_DIA" ? h("p", { id: "atencion-en-plan" }, "La persona quedó en el plan del día. Cuando el observador ",
      "encuentre un día compatible aparecerá como «Coincidencia» en «Plan del día»: desde ahí se prepara la operación.") : null,
    !conCita && a.estado !== "PENDIENTE" && !["CONSENTIMIENTO_REVOCADO", "EN_PLAN_DEL_DIA"].includes(a.resultado) ? h("p", {}, `Resultado: ${AT_NOMBRE[a.estado]} (${(a.resultado || "").replace(/_/g, " ").toLowerCase()}). `,
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
    const c = sacarConsentimiento(d);
    if (c) d.consentimiento = c;
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
    h("h3", { class: "full" }, "Consentimiento"), ...camposConsentimiento(false),
    h("button", { class: "btn primary" }, "Registrar y continuar"), errs, dup);
  return card("Persona nueva", form);
}

// ---------------------------------------------------------------- v2.0 · conexiones de red del motor
const TIPO_CONEXION = { ETHERNET: "Cable (Ethernet)", WIFI: "Wi-Fi", USB: "USB (teléfono o adaptador)", MODEM: "Módem móvil",
  LOOPBACK: "Este equipo", VIRTUAL: "Virtual", DESCONOCIDO: "Desconocida" };
const EVENTO_CONEXION = { CAMBIO: "Cambio de conexión", CAIDA: "Conexión caída", RECUPERADA: "Recuperada",
  RESTRICCION: "Restricción del proveedor (operación detenida)", USO: "Operación con el portal" };

async function viewConexiones() {
  const d = await api("/api/conexiones");
  const res = h("div", { id: "sondeo" });
  const medir = async () => {
    res.textContent = "Midiendo…";
    try {
      const r = await api("/api/conexiones/sondear", { method: "POST" });
      res.textContent = r.nota || r.resultados.map((x) => `${x.conexion}: ${x.ok ? x.latencia_ms + " ms" : "sin respuesta (" + x.error + ")"}`).join(" · ");
      setTimeout(() => refresh(true), 800);
    } catch (e) { res.textContent = e.message; }
  };
  const pol = d.politica;
  const u24 = d.ultimas_24h;
  return h("div", { id: "conexiones" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Conexiones de red"),
      h("div", { class: "muted" }, "Por qué red sale el motor hacia el portal. Solo se cambia de conexión cuando la actual falla (no conecta o se corta). ",
        h("b", {}, "Una restricción del proveedor (límite de solicitudes, acceso negado) detiene la operación y nunca se intenta por otra conexión.")))),
    h("div", { class: "grid kpis" },
      kpi("Conexión en uso", d.destino.local ? "Este equipo" : (d.activa || "—"),
        d.destino.local ? "el portal de prueba está en este mismo equipo" : "elegida por preferencia y estado"),
      kpi("Destino", `${d.destino.host}:${d.destino.puerto}`, d.destino.local ? "laboratorio (127.0.0.1)" : "portal configurado"),
      kpi("Preferencia", pol.conexion_orden_tipos.map((t) => TIPO_CONEXION[t] || t).join(" › "),
        `caída tras ${pol.conexion_umbral_fallos} fallos de red · se reintenta a los ${fmtDur(pol.conexion_reintento_s)}`)),
    h("div", { class: "grid kpis", id: "conexiones-24h" },
      kpi("Peticiones al portal (24 h)", num(u24.peticiones_total),
        u24.peticiones_total ? `${num(u24.peticiones.OK || 0)} bien · ${num(u24.peticiones.RED || 0)} de red · ${num(u24.peticiones.PORTAL || 0)} error del portal` : "sin actividad"),
      kpi("Restricciones del proveedor (24 h)", h("span", { class: u24.eventos.RESTRICCION ? "warn-text" : null, "data-restricciones": u24.eventos.RESTRICCION }, num(u24.eventos.RESTRICCION)),
        u24.restricciones.length ? `última: ${u24.restricciones[0].motivo} · se detuvo, sin reintentos` : "ninguna"),
      kpi("Caídas de conexión (24 h)", num(u24.eventos.CAIDA),
        `${num(u24.eventos.RECUPERADA)} recuperadas · ${num(u24.eventos.CAMBIO)} cambios de conexión`)),
    h("div", { class: "section" }, card("Conexiones del equipo", table([
      { t: "Interfaz", f: (c) => h("div", {}, h("b", {}, c.nombre), c.activa ? h("span", { class: "badge b-ok" }, "EN USO") : null,
        h("div", { class: "muted" }, c.descripcion || c.clasificado_por)) },
      { t: "Tipo", f: (c) => TIPO_CONEXION[c.tipo] || c.tipo },
      { t: "Estado", f: (c) => c.deshabilitada ? badge("DESHABILITADA") : !c.candidata ? h("span", { class: "muted" }, c.activa === false && !c.ipv4.length ? "sin dirección" : "no se usa") : badge(c.estado) },
      { t: "Latencia p50 / p95", num: true, f: (c) => c.latencia_ms.n ? `${num(c.latencia_ms.p50, 1)} / ${num(c.latencia_ms.p95, 1)} ms` : "—" },
      { t: "Éxitos", num: true, f: (c) => c.exitos }, { t: "Fallos de red", num: true, f: (c) => c.fallos },
      { t: "Restricciones", num: true, f: (c) => c.restricciones ? h("span", { class: "badge b-warn" }, c.restricciones) : "0" },
      { t: "Último error", f: (c) => c.ultimo_error || "—" }], d.conexiones),
      h("div", { class: "actions" }, h("button", { class: "btn", id: "btn-medir", onclick: medir }, "Medir ahora"), res))),
    h("div", { class: "section" }, card("Últimos eventos", table([
      { t: "Cuándo", f: (e) => fmtTime(e.ts) }, { t: "Evento", f: (e) => EVENTO_CONEXION[e.evento] || e.evento },
      { t: "Conexión", f: (e) => e.conexion || "—" }, { t: "Operación", f: (e) => (e.operacion || "—").replace(/_/g, " ") },
      { t: "Detalle", f: (e) => e.detalle.motivo || (e.detalle.peticiones ? Object.entries(e.detalle.peticiones).filter(([, n]) => n).map(([k, n]) => `${k} ${n}`).join(", ") : "") }],
      d.eventos))));
}

// ------------------------------------------------------------------ v2.0 · P2: trámites hechos a mano
// El operador completa el trámite en el flujo OFICIAL (fuera del motor) y aquí registra el folio.
// Queda DECLARADO; solo se marca VERIFICADO si el adaptador tiene una vía autorizada que lo confirme.
const ESTADO_CAPTURA = {
  DECLARADA: ["", "Declarada por el operador"], VERIFICADA: ["b-ok", "Verificada con el proveedor"],
  DISCREPANCIA: ["b-bad", "No coincide con el proveedor"], SIN_VERIFICACION: ["b-info", "Sin vía de verificación"],
};
const capturaBadge = (s) => h("span", { class: "badge " + (ESTADO_CAPTURA[s] || ["", s])[0], "data-estado-captura": s },
  (ESTADO_CAPTURA[s] || ["", s])[1]);

async function viewTramitesManuales() {
  const [ads, lista] = await Promise.all([api("/api/adaptadores"), api("/api/capturas-manuales")]);
  const usables = ads.adaptadores.filter((a) => a.captura_manual);
  const res = h("div", { id: "captura-resultado" });
  const form = h("form", { class: "form", id: "form-captura-manual", onsubmit: async (ev) => {
    ev.preventDefault(); res.textContent = "";
    const body = formData(ev.target);
    try {
      const c = await api("/api/capturas-manuales", { method: "POST", body });
      res.replaceChildren(h("div", {}, "Registrado ", h("b", {}, c.folio), " · ", capturaBadge(c.estado),
        c.detalle.verificacion && c.detalle.verificacion.motivo ? h("div", { class: "muted" }, c.detalle.verificacion.motivo) : null));
      ev.target.reset();
      setTimeout(() => refresh(true), 600);
    } catch (err) { res.replaceChildren(h("div", { class: "error" }, err.message)); }
  } },
    field("Proveedor", h("select", { name: "adaptador" }, usables.map((a) => h("option", { value: a.nombre }, a.descripcion))), "full"),
    field("Folio que mostró el sitio oficial", h("input", { name: "folio", required: true, maxlength: 64, autocomplete: "off" })),
    field("Fecha de la cita", h("input", { name: "fecha", type: "date", required: true })),
    field("Sucursal (opcional)", h("input", { name: "sucursal", maxlength: 32, autocomplete: "off" })),
    field("Expediente (opcional, número interno)", h("input", { name: "expediente_id", type: "number", min: 1 })),
    h("label", { class: "full check" }, h("input", { type: "checkbox", name: "folio_visto" }),
      " El sitio oficial mostró este folio al terminar el trámite (sin folio no hay cita)"),
    h("button", { class: "btn primary" }, "Registrar"));
  const reverificar = async (c) => {
    try { await api(`/api/capturas-manuales/${c.id}/verificar`, { method: "POST" }); refresh(true); }
    catch (err) { toast("⚠ " + err.message, 4000); }
  };
  return h("div", { id: "tramites-manuales" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Trámites hechos a mano"),
      h("div", { class: "muted" }, "Para proveedores sin integración autorizada: haz el trámite en el sitio oficial con la persona presente y registra aquí el folio. ",
        h("b", {}, "Un registro manual nunca cuenta como cita confirmada por el motor"), "; solo pasa a «verificada» si el proveedor lo confirma por una vía autorizada."))),
    h("div", { class: "section" }, card("Registrar resultado", form, res)),
    h("div", { class: "section" }, card("Registros", table([
      { t: "Cuándo", f: (c) => fmtTime(c.creada) }, { t: "Proveedor", f: (c) => c.adaptador },
      { t: "Folio", f: (c) => h("b", {}, c.folio) }, { t: "Fecha", f: (c) => c.fecha }, { t: "Sucursal", f: (c) => c.sucursal || "—" },
      { t: "Estado", f: (c) => capturaBadge(c.estado) },
      { t: "Detalle", f: (c) => {
        const v = c.detalle.verificacion || {};
        if (v.motivo) return v.motivo;
        if (v.existe_en_proveedor === false) return "el proveedor no tiene ese folio";
        if (v.fecha_proveedor && v.fecha_proveedor !== c.fecha) return "el proveedor tiene otra fecha: " + v.fecha_proveedor;
        return "";
      } },
      { t: "", f: (c) => c.estado === "DECLARADA" ? h("button", { class: "btn", "data-reverificar": c.id, onclick: (ev) => { ev.stopPropagation(); reverificar(c); } }, "Verificar de nuevo") : null }],
      lista.capturas))));
}

// ================================================================ v2.3 · recorrido completo desde la interfaz
// Huecos que antes obligaban a usar la API o un script (ver docs/INFORME_v2.3_ventanilla.md):
//  1. dejar a la persona en el plan del día desde su atención;
//  2. arrancar y detener el observador del calendario;
//  3. abrir días en el calendario del laboratorio (portal propio);
//  4. revisar posibles duplicados (administrador);
//  5. ver el historial completo de un expediente.
const RESULTADO_NOMBRE = { EN_PLAN_DEL_DIA: "quedó en el plan del día", CITA_CONFIRMADA: "cita confirmada",
  SIN_DISPONIBILIDAD: "sin días disponibles", CANCELADA: "la persona se retiró", ERROR: "error" };
const FUENTE_NOMBRE = { expediente: "Expediente", consentimiento: "Consentimiento", cola: "Cola del motor" };

function tarjetaAlPlan(a) {
  // Solo cuando la persona ya tiene expediente con consentimiento y todavía no hay operación con el portal.
  const errs = h("div", { class: "error", role: "alert", id: "al-plan-error" });
  const desde = h("input", { type: "date", id: "al-plan-desde" });
  const hasta = h("input", { type: "date", id: "al-plan-hasta" });
  const boton = h("button", { class: "btn", id: "btn-al-plan", onclick: async () => {
    errs.textContent = ""; boton.disabled = true;
    try {
      const body = {};
      if (desde.value) body.fechas_desde = desde.value;
      if (hasta.value) body.fechas_hasta = hasta.value;
      await api(`/api/ventanilla/atenciones/${a.id}/plan`, { method: "POST", body });
      toast("La persona quedó en el plan del día", 4000);
      refresh(true, true);
    } catch (err) { errs.textContent = err.message; boton.disabled = false; }
  } }, "Dejarla en el plan del día");
  return h("details", { class: "card section", id: "tarjeta-al-plan" },
    h("summary", {}, "¿Hoy no hay día que le sirva? Dejarla en el plan del día"),
    h("p", { class: "muted" }, "La atención se cierra SIN cita y el expediente entra al plan de hoy. Cuando el observador ",
      "vea un día compatible en el calendario, el plan se lo asigna y aparece como «Coincidencia» para preparar la operación. ",
      "Si no indicas fechas, se aceptan los próximos 30 días."),
    h("div", { class: "form" }, field("Fechas aceptables desde", desde), field("hasta", hasta), boton), errs);
}

async function historialExpediente(expId, abierto) {
  // Trazabilidad completa (solo códigos, estados y fechas; nunca datos personales).
  let t;
  try { t = await api(`/api/expedientes/${expId}/trazabilidad`); }
  catch (err) {
    return h("div", { class: "card section muted", id: "historial-expediente", "data-disponible": "no" },
      err.status === 404 ? "El historial de este expediente lo consulta un administrador (ya no está a tu cargo)." : err.message);
  }
  const r = t.expediente || {};
  const v = t.verificacion_consentimientos || {};
  const det = (x) => {
    const d = x.detalle || {};
    if (x.tipo === "ESTADO") return `${d.antes || "—"} → ${d.despues || "—"}`.replace(/_/g, " ");
    const partes = Object.entries(d).filter(([k, val]) => val !== null && val !== "" && typeof val !== "object" && k !== "sello")
      .map(([k, val]) => `${k}: ${val}`);
    return partes.join(" · ").slice(0, 160);
  };
  return h("details", { class: "card section", id: "historial-expediente", "data-disponible": "si", open: abierto ? true : null },
    h("summary", {}, `Historial completo del expediente ${r.codigo} (${t.linea_de_tiempo.length} eventos)`),
    h("div", { class: "grid kpis" },
      kpi("Estado", (r.estado || "").replace(/_/g, " "), r.motivo_cierre ? "cierre: " + r.motivo_cierre : "actual"),
      kpi("Consentimiento", r.consentimiento_vigente || "ninguno vigente",
        h("span", { id: "historial-cadena", "data-ok": v.ok ? "si" : "no" }, v.ok ? "cadena de sellos íntegra" : "⚠ cadena con problemas")),
      kpi("Folio", h("span", { id: "historial-folio" }, (t.reservaciones.find((x) => x.estado === "CONFIRMADA") || {}).folio || "—"), "cita confirmada"),
      kpi("Atenciones", num(t.atenciones.length), t.planes.length ? `en ${t.planes.length} plan(es) del día` : "sin plan del día")),
    t.atenciones.length ? h("div", {}, h("h3", {}, "Atenciones en ventanilla"), table([
      { t: "Atención", f: (x) => h("b", {}, x.codigo) }, { t: "Resultado", f: (x) => h("span", {}, badge(x.estado), " ",
        RESULTADO_NOMBRE[x.resultado] || (x.resultado || "").replace(/_/g, " ").toLowerCase()) },
      { t: "Folio", f: (x) => x.folio || "—" }, { t: "Operador", f: (x) => x.usuario },
      { t: "Inicio", f: (x) => fmtTime(x.iniciada_en) }, { t: "Cierre", f: (x) => x.cerrada_en ? fmtTime(x.cerrada_en) : "abierta" }], t.atenciones)) : null,
    t.planes.length ? h("div", {}, h("h3", {}, "Plan del día"), table([
      { t: "Jornada", f: (x) => x.jornada }, { t: "Estado", f: (x) => planBadge(x.estado) }, { t: "Prioridad", num: true, f: (x) => x.prioridad },
      { t: "Día asignado", f: (x) => x.fecha_asignada ? `${x.fecha_asignada} · ${x.sucursal_asignada}` : "—" }], t.planes)) : null,
    t.reservaciones.length ? h("div", {}, h("h3", {}, "Operaciones con el portal de laboratorio"), table([
      { t: "Estado", f: (x) => badge(x.estado) }, { t: "Día", f: (x) => x.fecha ? `${x.fecha} · ${x.sucursal || ""}` : "—" },
      { t: "Folio", f: (x) => x.folio ? h("b", {}, x.folio) : "—" }, { t: "Confirmó", f: (x) => x.confirmada_por || "—" },
      { t: "Error", f: (x) => x.error_codigo || "—" }], t.reservaciones)) : null,
    h("h3", {}, "Línea de tiempo"),
    h("ul", { class: "timeline", id: "historial-linea" }, t.linea_de_tiempo.slice().reverse().map((x) => h("li", { "data-tipo": x.tipo },
      h("b", {}, (x.tipo || "").replace(/_/g, " ")), h("span", { class: "muted" }, ` · ${FUENTE_NOMBRE[x.fuente] || x.fuente} · ${fmtTime(x.ts)} · ${x.actor || ""}`),
      det(x) ? h("div", { class: "muted" }, det(x)) : null))));
}

// ---------------------------------------------------------------- posibles duplicados (administrador)
async function revisionDuplicados() {
  let lista;
  try { lista = await api("/api/expedientes?estado=REQUIERE_REVISION&limit=20"); } catch (_) { return null; }
  if (!lista.length) return null;
  const items = await Promise.all(lista.map(async (e) => {
    let p = [];
    try { p = (await api(`/api/expedientes/${e.id}/posibles-duplicados`)).posibles || []; } catch (_) { p = []; }
    return { e, p };
  }));
  return card("Posibles duplicados por revisar", h("div", { id: "revision-duplicados" },
    h("p", { class: "muted" }, "Estos expedientes se parecen a otro (nombre muy parecido y misma fecha de nacimiento). ",
      "No entran a la cola ni al plan hasta que decidas. Si es la misma persona, cancela el nuevo y atiende el anterior."),
    items.map(({ e, p }) => {
      const errs = h("div", { class: "error", role: "alert" });
      const motivo = h("input", { maxlength: 120, placeholder: "Por qué son personas distintas (sin datos personales)", autocomplete: "off",
        class: "motivo-descartar" });
      return h("div", { class: "trabajo-item vt", "data-revision": e.codigo },
        h("div", {}, h("b", {}, e.codigo), " ", badge(e.estado), ` · ${e.nombre || ""} · ${TRAMITES[e.tipo_tramite] || e.tipo_tramite}`,
          h("div", { class: "muted" }, "Se parece a: ", p.length ? p.map((x) => `${x.codigo || x.otro || "?"}${x.estado ? " (" + x.estado.replace(/_/g, " ") + ")" : ""}`).join(", ") : "—")),
        h("div", { class: "actions" }, motivo, p.map((x) => h("button", { class: "btn", "data-descartar": x.codigo || x.otro, onclick: async () => {
          errs.textContent = "";
          try {
            const r = await api(`/api/expedientes/${e.id}/duplicados/descartar`, { method: "POST", body: { otro: x.codigo || x.otro, motivo: motivo.value } });
            toast(`${r.expediente}: ${r.estado.replace(/_/g, " ")}`, 4000); refresh(true);
          } catch (err) { errs.textContent = err.message; }
        } }, `Son personas distintas de ${x.codigo || x.otro}`)),
          h("button", { class: "btn danger", onclick: async () => {
            await act(() => api(`/api/expedientes/${e.id}/cancelar`, { method: "POST" }), "Expediente nuevo cancelado (era la misma persona)"); refresh(true);
          } }, "Es la misma persona: cancelar este")), errs);
    })));
}

// ---------------------------------------------------------------- observador del calendario (administrador)
async function tarjetaObservador() {
  let d;
  try { d = await api("/api/observador"); } catch (_) { return null; }
  const errs = h("div", { class: "error", role: "alert", id: "observador-error" });
  const hoy = new Date();
  const mesSig = new Date(hoy.getFullYear(), hoy.getMonth() + 1, 1);
  const mesVal = `${mesSig.getFullYear()}-${String(mesSig.getMonth() + 1).padStart(2, "0")}`;
  const suc = h("input", { id: "obs-sucursal", value: "NOR-1", maxlength: 32, class: "small" });
  const tram = h("select", { id: "obs-tramite" }, Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k }, v)));
  const mes = h("input", { type: "month", id: "obs-mes", value: mesVal });
  const activos = (d.configurados || []).filter((c) => c.activo);
  const vivo = (c) => (d.observadores || []).find((o) => o.nombre && o.nombre.includes(c.sucursal)) || null;
  const estadoTexto = (c) => {
    const o = (d.observadores || [])[0];
    const vista = o && o.vista ? Object.values(o.vista)[0] : null;
    if (c.estado === "DETENIDO_POR_FALLOS") return "detenido por fallos: " + (c.motivo || "");
    return vista === "ACTIVA" ? "mirando el calendario" : (c.estado || "").toLowerCase().replace(/_/g, " ") + (vista ? ` · vista ${vista}` : " · arrancando");
  };
  return card("Observador del calendario", h("div", { id: "observador", "data-activos": activos.length,
    "data-vista": ((d.observadores || [])[0] && Object.values((d.observadores[0].vista || {}))[0]) || "" },
    h("p", { class: "muted" }, "Mira el calendario del laboratorio y avisa al plan cuando aparece un día. Sin observador, el plan no recibe fechas nuevas."),
    activos.length ? h("ul", {}, activos.map((c) => h("li", { "data-observador": c.clave },
      h("b", {}, `${c.sucursal} · ${TRAMITES[c.tramite] || c.tramite} · ${c.mes || "mes actual"}`), " — ", estadoTexto(c, vivo(c))))) :
      h("p", { class: "warn-text", id: "observador-apagado" }, "No hay ningún observador en marcha."),
    h("div", { class: "actions" },
      activos.length ? h("button", { class: "btn", id: "btn-detener-observador", onclick: async () => {
        errs.textContent = "";
        try { await api("/api/observador", { method: "DELETE" }); refresh(true); } catch (err) { errs.textContent = err.message; }
      } }, "Detener") : [h("label", {}, "Sucursal ", suc), h("label", {}, "Trámite ", tram), h("label", {}, "Mes ", mes),
        h("button", { class: "btn primary", id: "btn-iniciar-observador", onclick: async (ev) => {
          errs.textContent = ""; ev.target.disabled = true;
          try {
            await api("/api/observador", { method: "POST", body: { sucursal: suc.value.trim(), tramite: tram.value, mes: mes.value || null, estrategia: "dom" } });
            toast("Observador iniciado", 3000); refresh(true);
          } catch (err) { errs.textContent = err.message; ev.target.disabled = false; }
        } }, "Iniciar observador")]),
    h("button", { class: "btn ghost small", onclick: () => refresh(true) }, "Actualizar estado"), errs));
}

// ---------------------------------------------------------------- Calendario del laboratorio (portal propio)
S.calLab = S.calLab || { sucursal: "NOR-1", tramite: "alta", mes: null };

async function viewCalendarioLab() {
  const est = await api("/api/laboratorio/portal");
  const cab = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Calendario del laboratorio"),
    h("div", { class: "muted" }, "Portal de reservaciones PROPIO en este equipo, con datos ficticios. Aquí simulas lo que haría el proveedor: ",
      "abrir un día, liberar o agotar lugares. El observador lo ve y el plan lo asigna. No se conecta a ningún portal externo.")));
  if (!est.disponible) return h("div", { id: "calendario-lab", "data-disponible": "no" }, cab, card("No disponible", h("p", {}, est.motivo)));
  const f = S.calLab;
  if (!f.mes) {
    const n = new Date(); const s = new Date(n.getFullYear(), n.getMonth() + 1, 1);
    f.mes = `${s.getFullYear()}-${String(s.getMonth() + 1).padStart(2, "0")}`;
  }
  const q = new URLSearchParams({ sucursal: f.sucursal, tramite: f.tramite, mes: f.mes });
  const [cal, regs] = await Promise.all([api("/api/laboratorio/portal/calendario?" + q),
    api("/api/laboratorio/portal/registros?cuantos=6&tramite=" + encodeURIComponent(f.tramite)).catch(() => ({ registros: [] }))]);
  const errs = h("div", { class: "error", role: "alert", id: "calendario-lab-error" });
  const capacidad = h("input", { type: "number", id: "cal-capacidad", min: 1, max: 20, value: 1, class: "small" });
  const accion = async (acc, fecha) => {
    errs.textContent = "";
    try {
      await api(`/api/laboratorio/portal/calendario/${acc}`, { method: "POST", body: { sucursal: f.sucursal, tramite: f.tramite, fecha, capacidad: Number(capacidad.value) || 1 } });
      toast(`${fecha}: ${acc}`, 2500); refresh(true);
    } catch (err) { errs.textContent = err.message; }
  };
  const [y, m] = cal.mes.split("-").map(Number);
  const primero = new Date(y, m - 1, 1).getDay();
  const celdas = [];
  for (let i = 0; i < (primero + 6) % 7; i++) celdas.push(h("div", { class: "dia vacio" }));
  for (const d of cal.dias) {
    const estado = d.estado.toLowerCase();
    celdas.push(h("div", { class: `dia e-${estado}`, "data-fecha": d.fecha, "data-estado": d.estado },
      h("b", {}, Number(d.fecha.slice(8))),
      h("small", {}, d.estado === "DISPONIBLE" ? `${d.libres} libre(s)` : d.estado === "AGOTADO" ? "agotado" : d.estado === "DESHABILITADO" ? "cerrado" : ""),
      d.pasado ? null : h("div", { class: "actions" },
        d.estado === "DISPONIBLE" ? h("button", { class: "btn small", "data-agotar": d.fecha, onclick: () => accion("agotar", d.fecha) }, "Agotar")
          : d.estado === "AGOTADO" ? h("button", { class: "btn small", "data-liberar": d.fecha, onclick: () => accion("liberar", d.fecha) }, "Liberar")
            : h("button", { class: "btn small primary", "data-abrir": d.fecha, onclick: () => accion("habilitar", d.fecha) }, "Abrir"))));
  }
  const mover = (k) => { const dt = new Date(y, m - 1 + k, 1); f.mes = `${dt.getFullYear()}-${String(dt.getMonth() + 1).padStart(2, "0")}`; refresh(true); };
  return h("div", { id: "calendario-lab", "data-disponible": "si", "data-mes": cal.mes }, cab,
    card(`${f.sucursal} · ${TRAMITES[f.tramite] || f.tramite} · ${cal.mes}`,
      h("div", { class: "filters actions" },
        h("label", {}, "Sucursal ", h("input", { id: "cal-sucursal", value: f.sucursal, maxlength: 32, class: "small",
          onchange: (ev) => { f.sucursal = ev.target.value.trim().toUpperCase(); refresh(true); } })),
        h("label", {}, "Trámite ", h("select", { id: "cal-tramite", onchange: (ev) => { f.tramite = ev.target.value; refresh(true); } },
          Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k, selected: k === f.tramite }, v)))),
        h("button", { class: "btn small", onclick: () => mover(-1) }, "‹ Mes anterior"), h("button", { class: "btn small", onclick: () => mover(1) }, "Mes siguiente ›"),
        h("label", {}, "Lugares al abrir o liberar ", capacidad)),
      h("div", { class: "mes", id: "calendario-lab-mes" }, ["L", "M", "M", "J", "V", "S", "D"].map((d) => h("div", { class: "dsem" }, d)), celdas),
      h("p", { class: "muted" }, `Hoy en el laboratorio: ${cal.hoy}. Los días pasados y el de hoy no se abren. `,
        h("a", { href: cal.vista_observador, target: "_blank", rel: "noopener" }, "Ver la página que mira el observador")), errs),
    h("div", { class: "section" }, card("Personas FICTICIAS que el portal de laboratorio reconoce",
      h("p", { class: "muted" }, "Para dar de alta a una persona de prueba usa uno de estos registros previos con su fecha de nacimiento ",
        "(el nombre, teléfono y correo los inventas; usa 555… y @ejemplo.test). No son personas reales."),
      table([{ t: "Registro previo", f: (r) => h("b", { class: "mono", "data-registro": r.identificador }, r.identificador) },
        { t: "Fecha de nacimiento", f: (r) => h("span", { "data-nacimiento": r.fecha_nacimiento }, r.fecha_nacimiento) },
        { t: "Trámite", f: (r) => TRAMITES[r.tramite] || r.tramite }], regs.registros || []))));
}
