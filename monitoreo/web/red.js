// ---------------------------------------------------------------- estado de red según Network Engine (solo lectura)
// La única fuente del estado de red que se muestra. Monitor no mide la red: pregunta a Network Engine. Si Network Engine
// está apagado, esta pantalla lo dice y Monitor sigue funcionando igual.
const RED_MOTOR = {
  disponible: ["b-ok", "Disponible"], no_disponible: ["b-bad", "No disponible"], consultando: ["b-info", "Consultando…"],
  credencial_rechazada: ["b-bad", "Credencial rechazada"], no_configurado: ["", "No configurado"], desactivado: ["", "Desactivado"],
  paquete_no_instalado: ["b-warn", "No encontrado"], directorio_invalido: ["b-warn", "Carpeta no válida"],
  token_ilegible: ["b-warn", "Credencial ilegible"], puerto_invalido: ["b-warn", "Puerto no válido"],
  error_al_iniciar: ["b-bad", "Error al iniciar"], desactualizado: ["b-warn", "Datos desactualizados"],
};
const RED_MOTOR_AYUDA = {
  no_configurado: "Para activarlo: MONITOREO_NETWORK_TOKEN_FILE (credencial read de Network Engine) y MONITOREO_NETWORK_DIR (carpeta de Network Engine).",
  no_disponible: "Network Engine no responde (apagado o reiniciándose). Monitor sigue funcionando; el estado de red queda como desconocido.",
  credencial_rechazada: "Network Engine rechazó la credencial (revocada o sin permiso). Monitor dejó de consultarlo hasta que se corrija y se reinicie.",
  paquete_no_instalado: "No se encontró Network Engine. Revisa MONITOREO_NETWORK_DIR.",
  desactualizado: "Monitor no recibe datos nuevos de Network Engine desde hace un rato. Ninguna interfaz se da por sana hasta que vuelva a responder.",
};
const RED_INTERNET = { DISPONIBLE: ["b-ok", "Con conexión"], NO_DISPONIBLE: ["b-bad", "Sin conexión utilizable"], DESCONOCIDO: ["", "Sin información"] };
const RED_SALUD = { SANA: "b-ok", DEGRADADA: "b-warn", "CAÍDA": "b-bad", "SIN MEDIR": "", "SIN VERIFICAR": "b-warn", "RECUPERÁNDOSE": "b-info" };
const redSeg = (s) => s === null || s === undefined ? "—" : (s < 90 ? `hace ${num(s, 0)} s` : `hace ${num(s / 60, 0)} min`);
const RED_EVENTO = { perdida: "b-bad", recuperada: "b-ok", degradada: "b-warn", info: "b-info" };
const redBadge = (par) => h("span", { class: "badge " + par[0] }, par[1]);
const RED_VEREDICTO = { DISPONIBLE: "b-ok", DEGRADADA: "b-warn", "CAÍDA": "b-bad", "SIN VERIFICAR": "b-warn" };
const RED_ORIGEN = {
  SIMULADO: ["b-warn", "SIMULADO", "Datos del simulador de Network Engine (laboratorio). No son mediciones de este equipo."],
  MEDIDO: ["b-info", "MEDIDO", "Network Engine mide las interfaces reales de este equipo. No es por sí una prueba física de conmutación."],
  DESCONOCIDO: ["", "Sin indicar", "Network Engine no indica si mide o simula."],
};
const RED_FASE = { activa: "b-ok", recuperada: "b-ok", iniciada: "b-info", perdida_conectividad: "b-bad",
  requiere_intervencion: "b-bad", incierta: "b-warn", interrumpida: "b-bad", cerrada: "" };
const RED_PRUEBA = { PASA: "b-ok", FALLA: "b-bad", PENDIENTE: "b-warn", OMITIDA: "", INFO: "b-info" };
const RED_MATRIZ = { "Aprobado físicamente": "b-ok", "Aprobado en simulación": "b-info", Fallido: "b-bad", Pendiente: "b-warn" };

// E5: códigos de Network Engine en español para la persona (si llega uno nuevo, se muestra el código legible)
const RED_TEXTO = {
  no_active_links: "ninguna interfaz con enlace", all_measured_connections_down: "todas las interfaces medidas están caídas",
  insufficient_measurements: "aún no hay mediciones suficientes", stale_measurements: "las mediciones están desactualizadas",
  connection_down: "su conexión está caída", no_usable_connection: "sin conexión utilizable",
  heartbeat_timeout: "sin latidos recientes", no_heartbeat: "aún no ha latido", awaiting_first_heartbeat: "esperando el primer latido",
  engine_restarted: "Network Engine se reinició", operation_interrupted: "operación interrumpida",
  engine_restart_during_operation: "reinicio con una operación en curso", session_expired: "la sesión del portal caducó",
  page_unreachable: "página inaccesible", navigation_timeout: "la página tardó demasiado", browser_closed: "navegador cerrado",
  network_error: "error de red", other: "otro error", expired: "caducada sin latidos", expired_after_restart: "caducada tras reinicio",
  replaced_after_restart: "sustituida tras reinicio",
  ok: "bien", degraded: "degradada", down: "caída", unknown: "sin medir",
};
function redTexto(c) { return c ? (RED_TEXTO[c] || String(c).replace(/_/g, " ")) : "—"; }

function redSesiones(s) {
  if (!s) return h("p", { class: "muted" }, "Network Engine no informa sesiones de navegador (versión sin sesiones o sin datos).");
  const filas = [...s.activas, ...s.cerradas];
  if (!filas.length) return h("p", { class: "muted" }, "Ninguna sesión de navegador registrada.");
  return h("div", {},
    s.requieren_persona ? h("p", {}, h("span", { class: "badge b-bad" }, `${s.requieren_persona} requieren intervención`),
      " Se interrumpió una operación en curso: revisar a mano antes de repetir nada.") : null,
    table([
      { t: "Sesión", f: (x) => h("b", {}, x.etiqueta) },
      { t: "Fase", f: (x) => h("span", { class: "badge " + (RED_FASE[x.fase_codigo] || ""), "data-red-fase": x.fase_codigo || "" }, x.fase) },
      { t: "Persona", f: (x) => x.requiere_persona ? "sí, revisar" : "no" },
      { t: "Operación", f: (x) => x.operacion_en_curso ? "en curso" : "—" },
      { t: "Red", f: (x) => redTexto(x.red) },
      { t: "Motivo", f: (x) => redTexto(x.motivo) },
      { t: "Último latido", f: (x) => x.cerrada_hace_s !== null && x.cerrada_hace_s !== undefined ? `cerrada ${redSeg(x.cerrada_hace_s)}` : redSeg(x.latido_hace_s) }],
      filas));
}

function redValidacion(v) {
  if (!v) return h("p", { class: "muted" }, "Sin resultados de la validación integral (7_VALIDACION_INTEGRAL.cmd) en este equipo.");
  const p = v.pasos;
  const fis = v.por_evidencia["FÍSICA"] || {};
  const sim = v.por_evidencia.SIMULADA || {};
  return h("div", {},
    h("p", {}, `Última ejecución ${fmtTime(v.cuando)}${v.perfil ? " · perfil " + v.perfil : ""}: `,
      ...["PASA", "FALLA", "PENDIENTE", "OMITIDA"].map((e) => h("span", { class: "badge " + RED_PRUEBA[e] }, `${p[e] || 0} ${e}`)), " "),
    h("p", { class: "muted" }, `Con evidencia física: ${fis.PASA || 0} pasan, ${fis.FALLA || 0} fallan. `
      + `Simuladas: ${sim.PASA || 0} pasan, ${sim.FALLA || 0} fallan. PENDIENTE nunca cuenta como aprobado.`),
    table([{ t: "Punto", f: (r) => h("b", {}, r[0]) },
      { t: "Estado", f: (r) => h("span", { class: "badge " + (RED_MATRIZ[r[1]] || "") }, r[1]) }],
      Array.from({ length: 12 }, (_, i) => [`F${i + 1}`, v.matriz[`F${i + 1}`] || "Pendiente"])));
}

async function viewRed() {
  const d = await api("/api/red");
  const motor = RED_MOTOR[d.network_engine] || ["", d.network_engine];
  const inet = RED_INTERNET[d.internet] || RED_INTERNET.DESCONOCIDO;
  const sanas = d.interfaces.filter((i) => i.salud === "SANA").length;
  const degradadas = d.interfaces.filter((i) => i.salud === "DEGRADADA").length;
  const origen = RED_ORIGEN[d.origen_datos] || null;
  return h("div", { id: "red" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Red"),
      h("div", { class: "muted" }, "Estado de las conexiones del equipo según Cauce Network Engine (solo lectura). ",
        h("b", {}, "Es lo que mide el motor de red, no una prueba física de los enlaces.")))),
    h("div", { class: "grid kpis" },
      kpi("Network Engine", h("span", { "data-red-motor": d.network_engine }, redBadge(motor)),
        RED_MOTOR_AYUDA[d.network_engine] || (d.ultima_respuesta ? `última respuesta ${fmtTime(d.ultima_respuesta)}` : "")
        + (d.antiguedad_s !== null && d.antiguedad_s !== undefined ? ` (${redSeg(d.antiguedad_s)})` : "")),
      kpi("Conectividad", h("span", { "data-red-internet": d.internet || "" }, redBadge(inet)),
        d.degradada ? "con latencia alta o fallos parciales" : (d.razon && d.internet !== "DISPONIBLE" ? redTexto(d.razon) : "")),
      kpi("Interfaces", num(d.interfaces.length), d.interfaces.length ? `${sanas} sanas · ${degradadas} degradadas` : "ninguna informada"),
      kpi("Origen de los datos", h("span", { "data-red-origen": d.origen_datos || "" }, origen ? redBadge(origen) : "—"),
        origen ? origen[2] : "Sin datos de Network Engine.")),
    h("div", { class: "section" }, card("Interfaces detectadas", d.interfaces.length ? table([
      { t: "Interfaz", f: (i) => h("b", {}, i.nombre) },
      { t: "Enlace", f: (i) => i.enlace },
      { t: "Estado", f: (i) => h("span", { class: "badge " + (RED_VEREDICTO[i.veredicto] || ""), "data-red-veredicto": i.veredicto || "" }, i.veredicto || "—") },
      { t: "Salud", f: (i) => h("span", { class: "badge " + (RED_SALUD[i.salud] || "") }, i.salud) },
      { t: "Disponible", f: (i) => i.disponible ? "sí" : "no" },
      { t: "Latencia media", num: true, f: (i) => i.latencia_ms === null ? "—" : `${num(i.latencia_ms, 1)} ms` },
      { t: "Disponibilidad", num: true, f: (i) => i.disponibilidad_pct === null ? "—" : `${num(i.disponibilidad_pct, 1)} %` },
      { t: "Medida", f: (i) => redSeg(i.medida_hace_s) },
      { t: "Próximo sondeo", f: (i) => i.proximo_sondeo_s === null || i.proximo_sondeo_s === undefined ? "—" : `en ${num(i.proximo_sondeo_s, 0)} s` }],
      d.interfaces) : h("p", { class: "muted" }, d.network_engine === "disponible" ? "Network Engine aún no informa interfaces." : "Sin datos de Network Engine."))),
    h("div", { class: "section" }, card("Sesiones de navegador", redSesiones(d.sesiones))),
    h("div", { class: "section" }, card("Últimas pruebas (validación integral)", redValidacion(d.ultima_validacion))),
    h("div", { class: "section" }, card("Pérdidas y recuperaciones de conectividad", d.eventos.length ? table([
      { t: "Cuándo", f: (e) => fmtTime(e.ts) },
      { t: "Evento", f: (e) => h("span", { class: "badge " + (RED_EVENTO[e.tipo] || "") }, e.evento) },
      { t: "Interfaz", f: (e) => e.interfaz || "—" }], d.eventos) : h("p", { class: "muted" }, "Sin eventos desde que Monitor empezó a leer Network Engine."),
      h("p", { class: "muted" }, d.nota))));
}
