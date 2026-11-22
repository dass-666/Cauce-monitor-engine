/* RC1 · MESA DE CONTROL DEL ADMINISTRADOR (laboratorio).
 * Flujo principal: Importar -> validar -> preparar expedientes (datos y consentimiento) -> planificar ->
 * observar disponibilidad autorizada -> revisar operaciones -> registrar resultado y folio.
 * Ventanilla sigue disponible para atender o corregir un expediente concreto.
 * Reutiliza: consentimiento (ventanilla.js: camposConsentimiento), expediente (captura.js: showExpediente),
 * historial (ventanilla.js: historialExpediente), observador (ventanilla.js: tarjetaObservador) y la operación
 * de Ventanilla para cada coincidencia. Todo el contenido dinámico se inserta como texto (nunca innerHTML). */
"use strict";

const SIT_BADGE = { INCOMPLETO: "b-bad", SIN_CONSENTIMIENTO: "b-warn", REVISION: "b-warn", LISTO: "b-info", EN_PLAN: "b-info",
  COINCIDENCIA: "b-acc", EN_OPERACION: "b-warn", VERIFICACION: "b-bad", CONFIRMADA: "b-ok", REVOCADO: "b-bad", CERRADO: "" };
const SIT_CORTO = { INCOMPLETO: "Faltan datos", SIN_CONSENTIMIENTO: "Falta consentimiento", REVISION: "En revisión",
  LISTO: "Listo para planificar", EN_PLAN: "En el plan", COINCIDENCIA: "Coincidencia (no es cita)", EN_OPERACION: "Operación en curso",
  VERIFICACION: "Confirmación incierta", CONFIRMADA: "Cita confirmada", REVOCADO: "Consentimiento revocado", CERRADO: "Cerrado" };
const sitBadge = (s) => h("span", { class: "badge " + (SIT_BADGE[s] || ""), "data-situacion": s }, SIT_CORTO[s] || s);
const CAMPO_NOMBRE = { nombre: "Nombre", fecha_nacimiento: "Fecha de nacimiento", id_registro_previo: "Registro previo",
  telefono: "Teléfono", correo: "Correo", region: "Región", sucursal: "Sucursal", tipo_tramite: "Trámite",
  consentimiento: "Consentimiento", operacion: "Operación", folio_anterior: "Folio anterior" };
const faltanTexto = (xs) => (xs || []).map((x) => CAMPO_NOMBRE[x] || x).join(", ");
const RES_IMPORT = { CREADO: ["Nuevo", "b-ok"], YA_EXISTE: ["Ya existe", "b-info"], RECHAZADO: ["Rechazada", "b-bad"],
  REPETIDA_EN_ARCHIVO: ["Repetida en el archivo", "b-warn"] };
const resImport = (r) => h("span", { class: "badge " + ((RES_IMPORT[r] || [])[1] || ""), "data-resultado": r }, (RES_IMPORT[r] || [r])[0]);
S.adm = S.adm || { filtros: {}, sel: new Set(), archivo: null, preview: null, lote: null };

function irA(vista, filtros) {
  if (filtros) { S.adm.filtros = filtros; S.adm.sel = new Set(); }
  if (location.hash === "#" + vista) refresh(true, true); else location.hash = "#" + vista;
}

async function descargar(ruta, nombre) {
  const r = await fetch(ruta, { headers: { Authorization: "Bearer " + S.token } });
  if (!r.ok) { toast("⚠ No se pudo descargar (" + r.status + ")", 4000); return; }
  const url = URL.createObjectURL(await r.blob());
  const a = h("a", { href: url, download: nombre });
  document.body.append(a); a.click(); a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 5000);
}

// Operar una coincidencia: se prepara (UPDATE condicional del plan) y se continúa en la pantalla de atención de
// Ventanilla, que es el ÚNICO camino para elegir el día y confirmar (folio comprobado en el portal).
async function operarCoincidencia(c, errs) {
  errs.textContent = "";
  try {
    if (c.situacion === "COINCIDENCIA") await api(`/api/planificador/elementos/${c.elemento}/preparar`, { method: "POST" });
    const d = await api("/api/ventanilla");
    let aid = ((d.abiertas || []).find((a) => a.expediente && a.expediente.id === c.expediente_id) || {}).id;
    if (!aid) {
      const a = await api("/api/ventanilla/atenciones", { method: "POST", body: { clave: nuevaClave() } });
      await api(`/api/ventanilla/atenciones/${a.id}/expediente`, { method: "POST", body: { expediente_id: c.expediente_id } });
      aid = a.id;
    }
    S.ventanilla = { aid, clave: null, paso: null };
    irA("ventanilla");
  } catch (err) { errs.textContent = err.message; }
}

// ---------------------------------------------------------------- PANEL (mesa de control)
async function viewPanel() {
  const d = await api("/api/admin/panel");
  const c = d.cifras;
  const k = (id, label, valor, sub, filtros, alerta) => {
    const el = kpi(label, h("span", { id: "kpi-" + id }, num(valor)), sub);
    el.dataset.kpi = id;
    if (filtros) { el.classList.add("clickable"); el.addEventListener("click", () => irA("expedientes", filtros)); }
    if (alerta && valor) el.classList.add("attention");
    return el;
  };
  const errs = h("div", { class: "error", role: "alert", id: "panel-error" });
  const obs = d.observador;
  const obsTexto = obs.activos ? `${obs.activos} en marcha` : "apagado";
  const coincid = card("Coincidencias de fecha (aún NO son citas)", h("div", { id: "panel-coincidencias" },
    h("p", { class: "muted" }, "El observador vio un día compatible y el plan lo asignó a este expediente. Una coincidencia no reserva nada: ",
      "la cita existe solo cuando el portal devuelve un folio."),
    table([
      { t: "Expediente", f: (x) => h("b", { "data-coincidencia": x.codigo }, x.codigo) },
      { t: "Día", f: (x) => `${x.fecha || "—"} · ${x.sucursal || ""}` },
      { t: "Situación", f: (x) => sitBadge(x.situacion) },
      { t: "", f: (x) => h("button", { class: "btn primary", "data-operar": x.codigo, onclick: (ev) => { ev.stopPropagation(); operarCoincidencia(x, errs); } },
        x.situacion === "COINCIDENCIA" ? "Preparar operación" : "Continuar operación") }], d.coincidencias)));
  const confirmadas = card("Citas confirmadas con folio", h("div", { id: "panel-confirmadas" },
    h("p", { class: "muted" }, d.simulado),
    table([
      { t: "Expediente", f: (x) => h("b", {}, x.codigo) },
      { t: "Folio", f: (x) => h("b", { class: "mono", "data-folio": x.folio }, x.folio) },
      { t: "Fecha confirmada", f: (x) => `${x.fecha_confirmada || "—"} · ${x.sucursal_confirmada || ""}` },
      // Piloto asistido: una cita declarada por una persona NUNCA se presenta como comprobada con el portal.
      { t: "Origen", f: (x) => x.origen === "PERSONA"
        ? h("span", { class: "badge b-info", "data-origen": "PERSONA" }, "Declarada por persona (sin verificar)")
        : h("span", { class: "badge b-ok", "data-origen": "PORTAL" }, "Comprobada en el portal") },
      { t: "", f: (x) => h("button", { class: "btn ghost small", onclick: async () => openDrawer(await historialExpediente(x.expediente_id, true)) }, "Historial") }],
    d.confirmadas)));
  const problemas = card("Requiere intervención", h("div", { id: "panel-problemas", "data-n": d.problemas.length },
    d.problemas.length ? h("ul", {}, d.problemas.map((p) => h("li", { "data-problema": p.tipo },
      h("span", { class: "badge b-warn" }, p.tipo.replace(/_/g, " ")), " ", p.codigo ? h("b", {}, p.codigo + " · ") : null, p.texto,
      p.expediente_id ? [" ", h("button", { class: "btn ghost small", onclick: () => showExpediente(p.expediente_id) }, "Ver")] : null,
      p.lote ? [" ", h("button", { class: "btn ghost small", onclick: () => { S.adm.lote = null; verLote(p.lote); } }, "Ver lote")] : null)))
      : h("p", { class: "muted" }, "Nada pendiente de intervención.")));
  const leyenda = h("div", { class: "leyenda", id: "leyenda-situaciones" }, Object.keys(SIT_CORTO).map((s) =>
    h("span", { title: d.situaciones[s] }, sitBadge(s), " ", h("span", { class: "muted" }, num(d.por_situacion[s] || 0)))));
  return h("div", { id: "panel" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Mesa de control"),
      h("div", { class: "muted" }, `Jornada ${d.jornada} · ${labOn() ? "laboratorio con datos ficticios" : "producción"} · actualizado ${fmtTime(d.ahora)}`)),
      h("div", { class: "actions" },
        h("button", { class: "btn primary", id: "btn-ir-importar", onclick: () => irA("importar") }, "Importar lista"),
        h("button", { class: "btn", onclick: () => irA("expedientes", {}) }, "Expedientes"),
        labOn() ? h("button", { class: "btn", onclick: () => irA("laboratorio-calendario") }, "Calendario lab") : null)),
    h("div", { class: "grid kpis", id: "panel-kpis" },
      k("total", "Clientes registrados", c.total, `${c.activos} activos`, {}),
      k("completos", "Expedientes completos", c.completos, "datos completos (activos)", {}),
      k("incompletos", "Expedientes incompletos", c.incompletos, "les faltan datos", { situacion: "INCOMPLETO" }, true),
      k("sin_consentimiento", "Falta consentimiento", c.sin_consentimiento, "bloqueados hasta registrarlo", { situacion: "SIN_CONSENTIMIENTO" }, true),
      k("listos", "Listos para planificar", c.listos, "pueden entrar al plan", { situacion: "LISTO" }),
      k("pendientes", "Pendientes de cita", c.pendientes_de_cita, "activos sin cita confirmada", {}),
      k("revision", "En revisión por duplicados", c.revision_duplicados, `${c.revision} en revisión en total`, { situacion: "REVISION" }, true),
      k("en_plan", "Incorporados al plan", c.en_plan, d.plan_hoy ? `plan de hoy: ${d.plan_hoy.vivos} de ${d.plan_hoy.capacidad}` : "sin plan hoy", { situacion: "EN_PLAN" }),
      k("coincidencias", "Coincidencias de fecha", c.coincidencias, "día visto, sin reservar", { situacion: "COINCIDENCIA" }),
      k("operaciones", "Operaciones por revisar", c.operaciones_pendientes, "en curso o en verificación", { situacion: "EN_OPERACION" }, true),
      k("confirmadas", "Citas confirmadas con folio", c.confirmadas_con_folio, c.declaradas_por_persona
        ? `${c.declaradas_por_persona} declaradas por persona (sin verificar)` : "folio comprobado en el portal", { situacion: "CONFIRMADA" }),
      k("problemas", "Requieren intervención", c.problemas, "ver la lista de abajo", null, true),
      kpi("Observador", h("span", { id: "kpi-observador", "data-activos": obs.activos }, obsTexto),
        obs.ultima_vista ? `última lectura ${fmtTime(obs.ultima_vista)}` : "sin lecturas todavía"),
      await tarjetaRecuperacion()),
    h("div", { class: "section" }, h("h3", {}, "Situación de los expedientes"), leyenda,
      h("p", { class: "muted" }, "Registrado ≠ listo para planificar ≠ coincidencia ≠ cita confirmada. Solo «Cita confirmada» tiene folio (del portal, o declarado por una persona en el piloto asistido).")),
    errs,
    h("div", { class: "grid cols-2 section" }, coincid, problemas),
    h("div", { class: "section" }, confirmadas),
    h("div", { class: "grid cols-2 section" }, await tarjetaObservador() || card("Observador", "No disponible"),
      card("Últimas importaciones", table([
        { t: "Lote", f: (x) => h("b", {}, x.codigo) }, { t: "Nuevos", num: true, f: (x) => x.creadas },
        { t: "Rechazadas", num: true, f: (x) => x.rechazadas }, { t: "Fecha", f: (x) => fmtTime(x.fecha) }],
      d.lotes, (x) => verLote(x.codigo)))));
}

// ---------------------------------------------------------------- IMPORTAR
function leerBase64(file) {
  return new Promise((ok, mal) => {
    const r = new FileReader();
    r.onload = () => ok(String(r.result).split(",", 2)[1] || "");
    r.onerror = () => mal(new Error("No se pudo leer el archivo"));
    r.readAsDataURL(file);
  });
}

async function previsualizar(opciones) {
  const a = S.adm.archivo;
  const body = { archivo: a.nombre, contenido_b64: a.b64, formato_fecha: (opciones && opciones.formato_fecha) || S.adm.formato || "DMA" };
  if (opciones && opciones.hoja) body.hoja = opciones.hoja;
  if (opciones && opciones.mapeo) body.mapeo = opciones.mapeo;
  S.adm.formato = body.formato_fecha;
  S.adm.preview = await api("/api/admin/importaciones/previsualizar", { method: "POST", body });
  S.adm.lote = null;
}

async function viewImportar() {
  // el error se conserva entre repintados (la vista se reconstruye entera)
  const errs = h("div", { class: "error", role: "alert", id: "importar-error" }, S.adm.error || "");
  S.adm.error = "";
  const fallo = (err) => { S.adm.error = err.message; };
  const entrada = h("input", { type: "file", id: "imp-archivo", accept: ".csv,.xlsx", onchange: async (ev) => {
    errs.textContent = "";
    const f = ev.target.files && ev.target.files[0];
    if (!f) return;
    if (f.size > 5 * 1024 * 1024) { errs.textContent = "El archivo pasa de 5 MB"; return; }
    try {
      S.adm.archivo = { nombre: f.name, b64: await leerBase64(f) };
      await previsualizar({});
    } catch (err) { fallo(err); S.adm.preview = null; S.adm.lote = null; }
    refresh(true);
  } });
  const lotes = await api("/api/admin/importaciones");
  const cab = h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Importar clientes"),
    h("div", { class: "muted" }, "Excel (.xlsx) o CSV. Primero ves la vista previa; nada se guarda hasta pulsar «Importar».")),
    h("button", { class: "btn ghost", id: "btn-plantilla", onclick: () => descargar("/api/admin/importaciones/plantilla.xlsx", "plantilla_clientes_cauce.xlsx") },
      "Descargar plantilla .xlsx"));
  const reglas = h("details", { class: "card section" }, h("summary", {}, "Reglas de la importación"), h("ul", {},
    h("li", {}, "Importar no es consentir: cada persona queda bloqueada hasta registrar su consentimiento con la huella del documento firmado."),
    h("li", {}, "Las columnas de contraseñas, claves o NIP se descartan: no se muestran ni se guardan."),
    h("li", {}, "Las fórmulas de Excel no se ejecutan: esa celda se rechaza (copia y pega como valores)."),
    h("li", {}, "Los identificadores se leen como texto (conservan sus ceros). Fechas: DD/MM/AAAA o AAAA-MM-DD."),
    h("li", {}, "Cargar otra vez el mismo archivo (o la misma persona) no crea expedientes duplicados.")));
  const tablaLotes = card("Lotes cargados", table([
    { t: "Lote", f: (x) => h("b", { "data-lote": x.codigo }, x.codigo) }, { t: "Archivo", f: (x) => x.archivo + (x.hoja ? ` · ${x.hoja}` : "") },
    { t: "Filas", num: true, f: (x) => x.filas }, { t: "Nuevos", num: true, f: (x) => x.creadas },
    { t: "Ya existían", num: true, f: (x) => x.existentes }, { t: "Rechazadas", num: true, f: (x) => x.rechazadas },
    { t: "Fecha", f: (x) => fmtTime(x.fecha) }], lotes.lotes, (x) => verLote(x.codigo)));
  const partes = [cab, card("1. Elige el archivo", h("div", { class: "actions" }, entrada,
    S.adm.archivo ? h("span", { class: "muted", id: "imp-nombre" }, S.adm.archivo.nombre) : null), errs), reglas];
  if (S.adm.lote) partes.push(resultadoLote(S.adm.lote));
  else if (S.adm.preview) partes.push(vistaPrevia(S.adm.preview, errs, fallo));
  partes.push(h("div", { class: "section" }, tablaLotes));
  return h("div", { id: "importar" }, partes);
}

function vistaPrevia(p, errs, fallo) {
  const r = p.resumen;
  const hoja = h("select", { id: "imp-hoja", onchange: async (ev) => {
    try { await previsualizar({ hoja: ev.target.value }); } catch (err) { fallo(err); }
    refresh(true);
  } }, p.hojas.map((x) => h("option", { value: x, selected: x === p.hoja }, x)));
  const formato = h("select", { id: "imp-formato-fecha", onchange: async (ev) => {
    try { await previsualizar({ hoja: p.hoja, mapeo: p.mapeo, formato_fecha: ev.target.value }); } catch (err) { fallo(err); }
    refresh(true);
  } }, h("option", { value: "DMA", selected: p.formato_fecha === "DMA" }, "DD/MM/AAAA"),
    h("option", { value: "MDA", selected: p.formato_fecha === "MDA" }, "MM/DD/AAAA"));
  const selects = {};
  const mapeo = h("div", { class: "table-wrap", id: "imp-mapeo" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, "Columna del archivo"), h("th", {}, "Campo de Cauce"))),
    h("tbody", {}, p.encabezados.map((c) => {
      let celda;
      if (c.tipo === "PROHIBIDA") celda = h("span", { class: "badge b-bad" }, "Descartada: parece contraseña o clave");
      else {
        const sel = h("select", { "data-columna": c.indice }, h("option", { value: "" }, c.tipo === "CONSENTIMIENTO" ? "— ignorada (no es consentimiento) —" : "— no importar —"),
          Object.entries(p.campos).map(([k, v]) => h("option", { value: k, selected: p.mapeo[String(c.indice)] === k }, v)));
        selects[c.indice] = sel; celda = sel;
      }
      return h("tr", {}, h("td", {}, h("b", {}, c.texto || `(columna ${c.indice + 1})`)), h("td", {}, celda));
    }))));
  const aplicar = h("button", { class: "btn", id: "btn-aplicar-mapeo", onclick: async () => {
    const m = {};
    for (const [i, s] of Object.entries(selects)) if (s.value) m[i] = s.value;
    try { await previsualizar({ hoja: p.hoja, mapeo: m, formato_fecha: p.formato_fecha }); } catch (err) { fallo(err); }
    refresh(true);
  } }, "Aplicar asignación y volver a revisar");
  const filas = table([
    { t: "Fila", num: true, f: (x) => x.fila },
    { t: "Resultado", f: (x) => resImport(x.resultado) },
    { t: "Nombre", f: (x) => (x.valores || {}).nombre || "—" },
    { t: "Registro", f: (x) => h("span", { class: "mono" }, (x.valores || {}).id_registro_previo || "—") },
    { t: "Nacimiento", f: (x) => (x.valores || {}).fecha_nacimiento || "—" },
    { t: "Sucursal · trámite", f: (x) => `${(x.valores || {}).sucursal || "—"} · ${(x.valores || {}).tipo_tramite || "—"}` },
    { t: "Acepta", f: (x) => (x.valores || {}).fechas_desde ? `${x.valores.fechas_desde} a ${x.valores.fechas_hasta || "…"}` : "—" },
    { t: "Qué pasará", f: (x) => x.resultado === "CREADO" ? h("span", { class: x.faltan.length ? "warn-text" : "" },
      x.faltan.length ? "Quedará bloqueado: falta " + faltanTexto(x.faltan) : "Se creará completo") : h("span", { class: x.resultado === "RECHAZADO" ? "error" : "muted" }, x.mensaje) }],
  p.filas);
  const n = r.nuevos;
  return h("div", { id: "imp-vista-previa", "data-nuevos": n, "data-rechazadas": r.rechazadas, "data-existen": r.ya_existen },
    card("2. Hoja y columnas", h("div", { class: "actions" }, p.hojas.length > 1 || p.formato === "xlsx" ? h("label", {}, "Hoja ", hoja) : null,
      h("label", {}, "Fechas escritas como ", formato)),
      p.cargas_anteriores.length ? h("p", { class: "warn-text", id: "imp-ya-cargado" }, `Este mismo archivo ya se cargó (${p.cargas_anteriores.map((x) => x.codigo).join(", ")}). `,
        "Las personas ya importadas no se duplicarán.") : null,
      p.avisos.length ? h("ul", { id: "imp-avisos" }, p.avisos.map((a) => h("li", { class: "muted" }, a))) : null,
      mapeo, h("div", { class: "actions section" }, aplicar)),
    card("3. Vista previa", h("div", { class: "grid kpis", id: "imp-resumen" },
      kpi("Filas", num(r.filas)), kpi("Nuevos", num(n), `${r.nuevos_completos_sin_consentimiento} solo esperan consentimiento`),
      kpi("Les faltan datos", num(r.nuevos_con_datos_faltantes), "se crean bloqueados"), kpi("Ya existen", num(r.ya_existen), "no se duplican"),
      kpi("Repetidas en el archivo", num(r.repetidas_en_archivo)), kpi("Rechazadas", num(r.rechazadas), "ver motivo por fila")),
      filas, p.filas_omitidas_en_vista ? h("p", { class: "muted" }, `Y ${p.filas_omitidas_en_vista} filas más (cuentan en el resumen).`) : null,
      h("div", { class: "actions section" }, h("button", { class: "btn primary", id: "btn-importar", disabled: r.filas ? null : true, onclick: async (ev) => {
        ev.target.disabled = true; errs.textContent = "";
        try {
          const a = S.adm.archivo;
          const lote = await api("/api/admin/importaciones", { method: "POST", body: { archivo: a.nombre, contenido_b64: a.b64,
            hoja: p.hoja, mapeo: p.mapeo, formato_fecha: p.formato_fecha } });
          S.adm.lote = lote; S.adm.preview = null; toast(`${lote.codigo}: ${lote.creadas} expediente(s) nuevos`, 4000); refresh(true, true);
        } catch (err) { errs.textContent = err.message; ev.target.disabled = false; }
      } }, n ? `Importar ${n} fila(s) nuevas` : "Registrar la carga (no hay filas nuevas: no se creará nada)"),
      h("span", { class: "muted" }, "Las rechazadas no se importan pero quedan en el reporte del lote."))));
}

async function verLote(codigo) {
  const lotes = await api("/api/admin/importaciones");
  const l = lotes.lotes.find((x) => x.codigo === codigo);
  if (!l) return;
  S.adm.lote = await api(`/api/admin/importaciones/${l.id}`);
  S.adm.preview = null;
  irA("importar");
}

function resultadoLote(l) {
  return card(`Resultado del lote ${l.codigo}`, h("div", { id: "imp-resultado", "data-lote": l.codigo, "data-creadas": l.creadas,
    "data-existentes": l.existentes, "data-rechazadas": l.rechazadas },
    h("p", {}, `Archivo ${l.archivo}${l.hoja ? " · hoja " + l.hoja : ""} · ${fmtTime(l.fecha)} · por ${l.creado_por}`),
    h("div", { class: "grid kpis" }, kpi("Nuevos", num(l.creadas)), kpi("Ya existían", num(l.existentes)), kpi("Rechazadas", num(l.rechazadas))),
    l.avisos.length ? h("ul", {}, l.avisos.map((a) => h("li", { class: "muted" }, a))) : null,
    table([
      { t: "Fila", num: true, f: (x) => x.fila }, { t: "Resultado", f: (x) => resImport(x.resultado) },
      { t: "Expediente", f: (x) => x.expediente ? h("b", { "data-exp-lote": x.expediente.codigo }, x.expediente.codigo) : "—" },
      { t: "Estado actual", f: (x) => x.expediente ? badge(x.expediente.estado) : "—" },
      { t: "Bloqueo o motivo", f: (x) => x.faltan.length ? "Falta " + faltanTexto(x.faltan) :
        Object.entries(x.errores || {}).map(([k, v]) => (k === "_" ? "" : (CAMPO_NOMBRE[k] || k) + ": ") + v).join("; ") || "—" }],
    l.filas_detalle, (x) => x.expediente && showExpediente(x.expediente.id)),
    h("div", { class: "actions section" },
      h("button", { class: "btn primary", id: "btn-lote-expedientes", onclick: () => irA("expedientes", { lote: l.codigo }) }, "Preparar estos expedientes"),
      h("button", { class: "btn", onclick: () => descargar(`/api/admin/importaciones/${l.id}/reporte.csv`, `reporte_${l.codigo}.csv`) }, "Descargar reporte (CSV)"),
      h("button", { class: "btn ghost", onclick: () => { S.adm.lote = null; S.adm.preview = null; S.adm.archivo = null; refresh(true); } }, "Cargar otro archivo"))));
}

// ---------------------------------------------------------------- EXPEDIENTES (preparar y planificar)
async function viewExpedientesAdmin() {
  const f = S.adm.filtros || {};
  const q = new URLSearchParams(Object.entries(f).filter(([, v]) => v !== "" && v !== null && v !== undefined && v !== false));
  const d = await api("/api/admin/expedientes?" + q.toString());
  const errs = h("div", { class: "error", role: "alert", id: "exp-error" });
  const sel = S.adm.sel;
  for (const id of [...sel]) if (!d.expedientes.some((e) => e.id === id && e.planificable)) sel.delete(id);
  const filtro = (k, input) => { input.addEventListener("change", () => { S.adm.filtros = { ...S.adm.filtros, [k]: input.type === "checkbox" ? input.checked : input.value }; refresh(true); }); return input; };
  const barra = h("div", { class: "filters", id: "exp-filtros" },
    filtro("situacion", h("select", { id: "f-situacion" }, h("option", { value: "" }, "Todas las situaciones"),
      Object.entries(d.situaciones).map(([k, v]) => h("option", { value: k, selected: f.situacion === k }, `${v} (${d.por_situacion[k] || 0})`)))),
    filtro("sucursal", h("input", { id: "f-sucursal", placeholder: "Sucursal (NOR-1)", value: f.sucursal || "", maxlength: 32 })),
    filtro("tramite", h("select", { id: "f-tramite" }, h("option", { value: "" }, "Todos los trámites"),
      Object.entries(TRAMITES).map(([k, v]) => h("option", { value: k, selected: f.tramite === k }, v)))),
    filtro("consentimiento", h("select", { id: "f-consentimiento" }, h("option", { value: "" }, "Consentimiento: todos"),
      ["VIGENTE", "FALTA", "REVOCADO"].map((x) => h("option", { value: x, selected: f.consentimiento === x }, "Consentimiento: " + x.toLowerCase())))),
    filtro("desde", h("input", { type: "date", id: "f-desde", value: f.desde || "", title: "Acepta fechas desde" })),
    filtro("hasta", h("input", { type: "date", id: "f-hasta", value: f.hasta || "", title: "Acepta fechas hasta" })),
    h("label", { class: "check" }, filtro("bloqueados", h("input", { type: "checkbox", id: "f-bloqueados", checked: f.bloqueados ? true : null })), " Solo bloqueados o incompletos"),
    f.lote ? h("span", { class: "chip" }, "Lote " + f.lote, " ", h("button", { class: "btn ghost small", onclick: () => { S.adm.filtros = { ...f, lote: "" }; refresh(true); } }, "✕")) : null,
    h("button", { class: "btn ghost small", onclick: () => { S.adm.filtros = {}; refresh(true); } }, "Quitar filtros"));
  const planificables = d.expedientes.filter((e) => e.planificable);
  const todos = h("input", { type: "checkbox", id: "sel-todos", title: "Seleccionar todos los listos", checked: planificables.length && planificables.every((e) => sel.has(e.id)) ? true : null,
    onchange: (ev) => { for (const e of planificables) { if (ev.target.checked) sel.add(e.id); else sel.delete(e.id); } refresh(true); } });
  const tabla = h("div", { class: "table-wrap", id: "exp-tabla" }, h("table", {},
    h("thead", {}, h("tr", {}, h("th", {}, todos), ["Expediente", "Persona", "Situación", "Sucursal · trámite", "Acepta", "Consentimiento", "Lote", ""].map((t) => h("th", {}, t)))),
    h("tbody", {}, d.expedientes.map((e) => h("tr", { "data-expediente": e.codigo, "data-situacion": e.situacion },
      h("td", {}, e.planificable ? h("input", { type: "checkbox", "data-sel": e.id, checked: sel.has(e.id) ? true : null,
        onchange: (ev) => { if (ev.target.checked) sel.add(e.id); else sel.delete(e.id); refresh(true); } }) : null),
      h("td", {}, h("b", {}, e.codigo)), h("td", {}, e.nombre),
      h("td", {}, sitBadge(e.situacion), e.detalle && e.detalle.faltan && e.detalle.faltan.length ? h("div", { class: "muted" }, "Falta: " + faltanTexto(e.detalle.faltan)) : null,
        e.situacion === "REVISION" && e.ultimo_error ? h("div", { class: "muted" }, e.ultimo_error.slice(0, 90)) : null,
        e.detalle && e.detalle.folio ? h("div", { class: "mono" }, `Folio ${e.detalle.folio} · ${e.detalle.fecha_confirmada}`) : null,
        e.detalle && e.detalle.fecha && !e.detalle.folio ? h("div", { class: "muted" }, `Día ${e.detalle.fecha} · ${e.detalle.sucursal}`) : null),
      h("td", {}, `${e.sucursal || "—"} · ${TRAMITES[e.tipo_tramite] || e.tipo_tramite}`),
      h("td", {}, e.fechas_desde || e.fechas_hasta ? `${e.fechas_desde || "…"} a ${e.fechas_hasta || "…"}` : h("span", { class: "muted" }, "sin rango"),
        e.fecha_solicitada ? h("div", { class: "muted" }, "pidió " + e.fecha_solicitada) : null),
      h("td", {}, h("span", { class: "badge " + (e.consentimiento === "VIGENTE" ? "b-ok" : e.consentimiento === "REVOCADO" ? "b-bad" : "b-warn") }, e.consentimiento.toLowerCase())),
      h("td", {}, e.lote || h("span", { class: "muted" }, "a mano")),
      h("td", {}, h("div", { class: "actions" },
        e.consentimiento === "FALTA" && !["CERRADO", "REVOCADO", "CONFIRMADA"].includes(e.situacion) ? h("button", { class: "btn small", "data-consentir": e.codigo, onclick: () => formConsentimientoAdmin(e) }, "Registrar consentimiento") : null,
        ["INCOMPLETO", "SIN_CONSENTIMIENTO", "LISTO", "REVISION"].includes(e.situacion) ? h("button", { class: "btn ghost small", "data-fechas": e.codigo, onclick: () => formFechas(e) }, "Fechas") : null,
        ["INCOMPLETO", "SIN_CONSENTIMIENTO", "LISTO", "REVISION"].includes(e.situacion) ? h("button", { class: "btn ghost small", "data-corregir": e.codigo, onclick: () => formCorregir(e) }, "Corregir datos") : null,
        h("button", { class: "btn ghost small", onclick: () => showExpediente(e.id) }, "Ver"),
        h("button", { class: "btn ghost small", onclick: async () => openDrawer(await historialExpediente(e.id, true)) }, "Historial"))))))));
  const desde = h("input", { type: "date", id: "plan-desde", title: "Solo días desde (opcional)" });
  const hasta = h("input", { type: "date", id: "plan-hasta", title: "Solo días hasta (opcional)" });
  const capacidad = h("input", { type: "number", id: "plan-capacidad-lote", min: 1, max: 50, placeholder: "capacidad", class: "small" });
  const resultado = h("div", { id: "plan-resultado" });
  const alPlan = card(`Incorporar al plan de hoy (${d.jornada})`, h("div", { id: "al-plan-lote" },
    h("p", { class: "muted" }, "Solo entran expedientes «Listo para planificar» (datos completos, sin duplicado pendiente y con consentimiento vigente). ",
      "Cada uno usa SU rango de fechas aceptables; el intervalo de abajo solo lo acota. La capacidad del plan se respeta."),
    h("div", { class: "actions" }, h("label", {}, "Desde ", desde), h("label", {}, "Hasta ", hasta), h("label", {}, "Capacidad del plan ", capacidad),
      h("button", { class: "btn primary", id: "btn-al-plan-lote", disabled: sel.size ? null : true, onclick: async (ev) => {
        ev.target.disabled = true; errs.textContent = "";
        try {
          const body = { expediente_ids: [...sel] };
          if (desde.value) body.fechas_desde = desde.value;
          if (hasta.value) body.fechas_hasta = hasta.value;
          if (capacidad.value) body.capacidad = Number(capacidad.value);
          const r = await api("/api/admin/plan/agregar", { method: "POST", body });
          S.adm.sel = new Set();
          S.adm.ultimoPlan = r;
          toast(`${r.aceptados.length} al plan · ${r.rechazados.length} no entraron`, 4000);
          refresh(true);
        } catch (err) { errs.textContent = err.message; ev.target.disabled = false; }
      } }, `Añadir ${sel.size} seleccionado(s) al plan`)), resultado));
  if (S.adm.ultimoPlan) {
    const r = S.adm.ultimoPlan;
    resultado.append(h("div", { class: "section", id: "plan-lote-resultado", "data-aceptados": r.aceptados.length, "data-rechazados": r.rechazados.length },
      h("p", {}, h("b", {}, `${r.aceptados.length} incorporado(s)`), ` al plan ${r.plan.fecha_jornada} (${r.plan.vivos} de ${r.plan.capacidad}).`),
      r.aceptados.length ? h("ul", { id: "plan-lote-fechas" }, r.aceptados.map((x) => {
        const fs = [...(x.fechas_aceptables || [])].sort();
        const txt = !fs.length ? "sin fechas" : fs.length === 1 ? `solo el ${fs[0]}` : `del ${fs[0]} al ${fs[fs.length - 1]} (${fs.length} días)`;
        return h("li", {}, `${x.codigo || x.expediente_id}: acepta ${txt}`);
      })) : null,
      r.rechazados.length ? h("ul", {}, r.rechazados.map((x) => h("li", { class: "warn-text" }, `${x.codigo || x.expediente_id}: ${x.motivo}`))) : null));
  }
  return h("div", { id: "expedientes-admin" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Expedientes"),
      h("div", { class: "muted" }, `${d.expedientes.length} mostrados · nombres enmascarados · los datos completos se ven en «Ver» o «Corregir datos» (queda registrado)`)),
      h("div", { class: "actions" }, h("button", { class: "btn", onclick: () => irA("importar") }, "Importar lista"),
        h("button", { class: "btn", onclick: () => irA("ventanilla") }, "Atender uno en Ventanilla"))),
    barra, alPlan, errs, tabla);
}

function formConsentimientoAdmin(e) {
  const errs = h("div", { class: "error full", role: "alert" });
  const form = h("form", { class: "form", id: "form-consentimiento-admin", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const boton = ev.target.querySelector("button[type=submit]");
    boton.disabled = true;
    try {
      const r = await api(`/api/expedientes/${e.id}/consentimientos`, { method: "POST", body: sacarConsentimiento(formData(ev.target)) });
      $("drawer").classList.add("hidden");
      toast(`${e.codigo}: consentimiento ${r.codigo || "registrado"}`, 3500);
      S.dirty = false; refresh(true);
    } catch (err) {
      const d = err.detalle || {};
      errs.replaceChildren(err.message, d.errores ? h("ul", {}, Object.entries(d.errores).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
      boton.disabled = false;
    }
  } }, ...camposConsentimiento(true), h("button", { class: "btn primary", type: "submit", id: "btn-consentimiento-admin" }, "Registrar consentimiento"), errs);
  openDrawer(h("div", { id: "consentimiento-admin", "data-expediente": e.codigo }, h("h2", {}, `Consentimiento · ${e.codigo}`),
    h("p", { class: "muted" }, "Registra solo un consentimiento que exista: el formato firmado por la persona. Cauce guarda su huella, no el documento. ",
      "No se puede registrar a partir de lo que diga la lista importada."), form));
}

// RC1 · Corrección de los datos del formulario desde la Mesa de control. Usa el mismo PATCH (con control de
// versión) que el resto de la aplicación: mismas validaciones y el expediente se vuelve a validar solo.
// Teléfono y correo se escriben dos veces SOLO si se cambian; la confirmación no se guarda.
async function formCorregir(e) {
  const x = await api(`/api/expedientes/${e.id}`);       // datos completos: la consulta queda auditada
  const errs = h("div", { class: "error full", role: "alert" });
  const inp = (name, attrs) => h("input", { name, autocomplete: "off", ...attrs });
  const opciones = (cat, actual) => Object.entries(cat).map(([k, v]) => h("option", { value: k, selected: k === actual }, v));
  const form = h("form", { class: "form", id: "form-corregir", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    const el = ev.target.elements, body = { version: x.version };
    for (const k of ["operacion", "tipo_tramite", "id_registro_previo", "fecha_nacimiento", "folio_anterior"]) {
      const v = el[k].value.trim();
      if (v && v !== String(x[k] || "")) body[k] = v;
    }
    for (const k of ["telefono", "correo"]) {
      const v = el[k].value.trim();
      if (v) { body[k] = v; body[k + "_confirmacion"] = el[k + "_confirmacion"].value.trim(); }
    }
    if (Object.keys(body).length === 1) { errs.textContent = "No cambiaste nada."; return; }
    try {
      const r = await api(`/api/expedientes/${e.id}`, { method: "PATCH", body });
      $("drawer").classList.add("hidden");
      toast(`${e.codigo}: corregido (${(r.campos_modificados || []).map((c) => CAMPO_NOMBRE[c] || c).join(", ")})`, 5000);
      S.dirty = false; refresh(true);
    } catch (err) {
      const d = err.detalle || {};
      errs.replaceChildren(err.message, d.errores ? h("ul", {}, Object.entries(d.errores).map(([k, v]) => h("li", {}, `${CAMPO_NOMBRE[k.replace("_confirmacion", "")] || k}: ${v}`))) : "");
    }
  } },
    field("Operación", h("select", { name: "operacion" }, opciones({ nueva: "Nueva cita", reprogramar: "Reprogramar" }, x.operacion))),
    field("Tipo de trámite", h("select", { name: "tipo_tramite" }, opciones(TRAMITES, x.tipo_tramite))),
    field("Identificador de registro previo", inp("id_registro_previo", { maxlength: 32, value: x.id_registro_previo || "", placeholder: "se guarda como texto: conserva los ceros" })),
    field("Fecha de nacimiento", inp("fecha_nacimiento", { type: "date", value: x.fecha_nacimiento || "" })),
    field(`Teléfono nuevo (actual: ${x.telefono || "falta"})`, inp("telefono", { maxlength: 20, inputmode: "tel" })),
    field("Confirma el teléfono nuevo", inp("telefono_confirmacion", { maxlength: 20, inputmode: "tel" })),
    field(`Correo nuevo (actual: ${x.correo || "falta"})`, inp("correo", { type: "email", maxlength: 128 })),
    field("Confirma el correo nuevo", inp("correo_confirmacion", { type: "email", maxlength: 128 })),
    field("Folio de la cita anterior (solo al reprogramar)", inp("folio_anterior", { maxlength: 32, value: x.folio_anterior || "" })),
    h("button", { class: "btn primary", type: "submit" }, "Guardar corrección"), errs);
  openDrawer(h("div", {}, h("h2", {}, `Corregir datos · ${e.codigo}`),
    h("p", { class: "muted" }, "Mismas reglas que el alta. Deja teléfono y correo vacíos si no cambian. Si el expediente ya estaba listo y cambias el trámite, se vuelve a validar."),
    form));
}

function formFechas(e) {
  const errs = h("div", { class: "error full", role: "alert" });
  const form = h("form", { class: "form", id: "form-fechas", onsubmit: async (ev) => {
    ev.preventDefault(); errs.textContent = "";
    try {
      const x = await api(`/api/expedientes/${e.id}`);
      const body = { version: x.version };
      for (const k of ["fecha_solicitada", "fechas_desde", "fechas_hasta"]) { const v = ev.target.elements[k].value; if (v) body[k] = v; }
      await api(`/api/expedientes/${e.id}`, { method: "PATCH", body });
      $("drawer").classList.add("hidden"); toast(`${e.codigo}: fechas guardadas`); S.dirty = false; refresh(true);
    } catch (err) {
      const d = err.detalle || {};
      errs.replaceChildren(err.message, d.errores ? h("ul", {}, Object.entries(d.errores).map(([k, v]) => h("li", {}, `${k}: ${v}`))) : "");
    }
  } },
    field("Fecha que pidió la persona", h("input", { type: "date", name: "fecha_solicitada", value: e.fecha_solicitada || "" })),
    field("Acepta desde", h("input", { type: "date", name: "fechas_desde", value: e.fechas_desde || "" })),
    field("Acepta hasta", h("input", { type: "date", name: "fechas_hasta", value: e.fechas_hasta || "" })),
    h("button", { class: "btn primary", type: "submit" }, "Guardar fechas"), errs);
  openDrawer(h("div", {}, h("h2", {}, `Fechas aceptables · ${e.codigo}`),
    h("p", { class: "muted" }, "La fecha que pidió y el rango que acepta. La fecha CONFIRMADA solo la pone el portal al dar el folio."), form));
}
