// ---------------------------------------------------------------- estado de red según Network Engine (solo lectura)
// La única fuente del estado de red que se muestra. Monitor no mide la red: pregunta a Network Engine. Si Network Engine
// está apagado, esta pantalla lo dice y Monitor sigue funcionando igual.
const RED_MOTOR = {
  disponible: ["b-ok", "Disponible"], no_disponible: ["b-bad", "No disponible"], consultando: ["b-info", "Consultando…"],
  credencial_rechazada: ["b-bad", "Credencial rechazada"], no_configurado: ["", "No configurado"], desactivado: ["", "Desactivado"],
  paquete_no_instalado: ["b-warn", "No encontrado"], directorio_invalido: ["b-warn", "Carpeta no válida"],
  token_ilegible: ["b-warn", "Credencial ilegible"], puerto_invalido: ["b-warn", "Puerto no válido"],
  error_al_iniciar: ["b-bad", "Error al iniciar"],
};
const RED_MOTOR_AYUDA = {
  no_configurado: "Para activarlo: MONITOREO_NETWORK_TOKEN_FILE (credencial read de Network Engine) y MONITOREO_NETWORK_DIR (carpeta de Network Engine).",
  no_disponible: "Network Engine no responde (apagado o reiniciándose). Monitor sigue funcionando; el estado de red queda como desconocido.",
  credencial_rechazada: "Network Engine rechazó la credencial (revocada o sin permiso). Monitor dejó de consultarlo hasta que se corrija y se reinicie.",
  paquete_no_instalado: "No se encontró Network Engine. Revisa MONITOREO_NETWORK_DIR.",
};
const RED_INTERNET = { DISPONIBLE: ["b-ok", "Con conexión"], NO_DISPONIBLE: ["b-bad", "Sin conexión utilizable"], DESCONOCIDO: ["", "Sin información"] };
const RED_SALUD = { SANA: "b-ok", DEGRADADA: "b-warn", "CAÍDA": "b-bad", "SIN MEDIR": "" };
const RED_EVENTO = { perdida: "b-bad", recuperada: "b-ok", degradada: "b-warn", info: "b-info" };
const redBadge = (par) => h("span", { class: "badge " + par[0] }, par[1]);

async function viewRed() {
  const d = await api("/api/red");
  const motor = RED_MOTOR[d.network_engine] || ["", d.network_engine];
  const inet = RED_INTERNET[d.internet] || RED_INTERNET.DESCONOCIDO;
  const sanas = d.interfaces.filter((i) => i.salud === "SANA").length;
  const degradadas = d.interfaces.filter((i) => i.salud === "DEGRADADA").length;
  return h("div", { id: "red" },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Red"),
      h("div", { class: "muted" }, "Estado de las conexiones del equipo según Cauce Network Engine (solo lectura). ",
        h("b", {}, "Es lo que mide el motor de red, no una prueba física de los enlaces.")))),
    h("div", { class: "grid kpis" },
      kpi("Network Engine", h("span", { "data-red-motor": d.network_engine }, redBadge(motor)),
        RED_MOTOR_AYUDA[d.network_engine] || (d.ultima_respuesta ? `última respuesta ${fmtTime(d.ultima_respuesta)}` : "")),
      kpi("Conectividad", h("span", { "data-red-internet": d.internet || "" }, redBadge(inet)),
        d.degradada ? "con latencia alta o fallos parciales" : (d.razon && d.internet !== "DISPONIBLE" ? d.razon.replace(/_/g, " ") : "")),
      kpi("Interfaces", num(d.interfaces.length), d.interfaces.length ? `${sanas} sanas · ${degradadas} degradadas` : "ninguna informada")),
    h("div", { class: "section" }, card("Interfaces detectadas", d.interfaces.length ? table([
      { t: "Interfaz", f: (i) => h("b", {}, i.nombre) },
      { t: "Enlace", f: (i) => i.enlace },
      { t: "Salud", f: (i) => h("span", { class: "badge " + (RED_SALUD[i.salud] || "") }, i.salud) },
      { t: "Disponible", f: (i) => i.disponible ? "sí" : "no" },
      { t: "Latencia media", num: true, f: (i) => i.latencia_ms === null ? "—" : `${num(i.latencia_ms, 1)} ms` },
      { t: "Disponibilidad", num: true, f: (i) => i.disponibilidad_pct === null ? "—" : `${num(i.disponibilidad_pct, 1)} %` }],
      d.interfaces) : h("p", { class: "muted" }, d.network_engine === "disponible" ? "Network Engine aún no informa interfaces." : "Sin datos de Network Engine."))),
    h("div", { class: "section" }, card("Pérdidas y recuperaciones de conectividad", d.eventos.length ? table([
      { t: "Cuándo", f: (e) => fmtTime(e.ts) },
      { t: "Evento", f: (e) => h("span", { class: "badge " + (RED_EVENTO[e.tipo] || "") }, e.evento) },
      { t: "Interfaz", f: (e) => e.interfaz || "—" }], d.eventos) : h("p", { class: "muted" }, "Sin eventos desde que Monitor empezó a leer Network Engine."),
      h("p", { class: "muted" }, d.nota))));
}
