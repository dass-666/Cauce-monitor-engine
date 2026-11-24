/* Páginas observadas (administrador) · observador universal por URL + perfil, SIN programar.
 *
 * Quien configura escribe qué página mirar y cómo leer cada turno (selectores, formato de fecha, textos de
 * disponibilidad, tabla de ubicaciones), pulsa «Probar» (vista previa: abre la página UNA vez y enseña lo que
 * reconocería y lo que no, sin guardar nada) y solo entonces puede «Guardar y observar». Cualquier cambio en el
 * formulario invalida la prueba: lo que se guarda es exactamente lo que se probó.
 * Mismo API que ya existía: GET/POST /api/fuentes-web, POST /api/fuentes-web/probar, DELETE /api/fuentes-web/{nombre}.
 * Las reglas de acceso (sitios locales solo en laboratorio; otros sitios: https + host permitido por quien instala
 * + autorización escrita vigente; CAPTCHA o inicio de sesión = se detiene) las aplica el servidor, no esta pantalla.
 */
"use strict";

S.fuentes = S.fuentes || { borrador: null };

const FW_FORMATOS = ["DD/MM/AAAA", "ISO", "DD-MM-AAAA", "AAAA/MM/DD"];
const FW_MOTIVOS = {
  fecha: "la fecha leída no tiene el formato elegido (revisa el selector, el patrón o el formato)",
  estado: "ese texto no está en «Textos de disponible» ni en «Textos de sin lugares»: no se interpreta",
  ubicacion: "esa ubicación no está en la tabla de ubicaciones: no se adivina la sucursal",
};
const fwLineas = (t) => (t || "").split(/\r?\n/).map((x) => x.trim()).filter(Boolean);

// regla de extracción: selector + (atributo) + (patrón). Vacía = no se usa.
function fwRegla(pref, titulo, ayuda, valores) {
  const v = valores || {};
  const sel = h("input", { name: `${pref}_selector`, value: v.selector || "", placeholder: "selector CSS dentro del elemento" });
  const atr = h("input", { name: `${pref}_atributo`, value: v.atributo || "", placeholder: "(texto)", class: "small" });
  const pat = h("input", { name: `${pref}_patron`, value: v.patron || "", placeholder: "expresión regular (opcional)" });
  return h("fieldset", { class: "fw-regla full", "data-regla": pref }, h("legend", {}, titulo),
    ayuda ? h("p", { class: "muted" }, ayuda) : null,
    h("div", { class: "form" }, h("label", {}, "Selector", sel), h("label", {}, "Atributo", atr), h("label", {}, "Patrón", pat)));
}

function fwLeerRegla(f, pref) {
  const g = (k) => (f.elements[`${pref}_${k}`] ? f.elements[`${pref}_${k}`].value.trim() : "");
  const r = {};
  if (g("selector")) r.selector = g("selector");
  if (g("atributo")) r.atributo = g("atributo");
  if (g("patron")) r.patron = g("patron");
  return r;
}

// lo que se envía al servidor: el MISMO objeto para probar y para guardar
function fwCuerpo(f) {
  const v = (k) => f.elements[k].value.trim();
  const perfil = { zona: v("zona"), elemento: v("elemento"),
    fecha: { ...fwLeerRegla(f, "fecha"), formato: v("fecha_formato") } };
  const estado = fwLeerRegla(f, "estado");
  const disp = fwLineas(v("disponible")), nodisp = fwLineas(v("no_disponible"));
  if (Object.keys(estado).length || disp.length || nodisp.length) perfil.estado = { ...estado, disponible: disp, no_disponible: nodisp };
  for (const k of ["libres", "hora", "ubicacion"]) {
    const r = fwLeerRegla(f, k);
    if (Object.keys(r).length) perfil[k] = r;
  }
  const ub = {};
  for (const l of fwLineas(v("ubicaciones"))) {
    const i = l.lastIndexOf("=");
    if (i > 0) ub[l.slice(0, i).replace(/[=>\s]+$/, "").trim()] = l.slice(i + 1).trim();
  }
  if (Object.keys(ub).length) perfil.ubicaciones = ub;
  const ign = fwLineas(v("ignorar"));
  if (ign.length) perfil.ignorar = ign;
  perfil.estabilidad_ms = parseInt(v("estabilidad_ms") || "800", 10);
  if (v("sesion_perdida")) perfil.sesion_perdida = v("sesion_perdida");
  const cuerpo = { nombre: v("nombre"), url: v("url"), tramite: v("tramite"), sucursal: v("sucursal") || null,
    estrategia: v("estrategia"), perfil };
  if (v("aut_ref") || v("aut_sha") || v("aut_hasta")) {
    cuerpo.autorizacion = { ref: v("aut_ref"), documento_sha256: v("aut_sha").toLowerCase(), vigente_hasta: v("aut_hasta") };
  }
  return cuerpo;
}

function fwResultado(r) {
  const vista = r.vista || {};
  const bloqueada = vista.estado === "BLOQUEADO_CONTROL";
  const ok = !bloqueada && !(r.errores || []).length && vista.estado === "ACTIVA";
  const dias = r.dias || [];
  const muestras = r.muestras_no_reconocidas || [];
  const nNo = (r.no_reconocidos || []).length;
  return h("div", { id: "fw-resultado", "data-ok": ok ? "si" : "no", "data-dias": dias.length, "data-no-reconocidos": nNo },
    bloqueada ? h("p", { class: "error", id: "fw-bloqueada" },
      "La página pide un control de acceso (CAPTCHA, inicio de sesión o límite de peticiones). Cauce no lo resuelve ni lo rodea: ",
      "esta página no se puede observar. ", h("span", { class: "muted" }, vista.detalle || "")) : null,
    (r.errores || []).length ? h("p", { class: "error", id: "fw-errores" }, "No se pudo leer la página: " + r.errores.join(" · ")) : null,
    ok ? h("p", {}, h("span", { class: "badge b-ok" }, "Página leída"), " ",
      `Elementos en la zona: ${r.encontrados == null ? "—" : r.encontrados} · días reconocidos: ${dias.length} · no reconocidos: ${nNo}`) : null,
    ok && r.encontrados === 0 ? h("p", { class: "warn-text", id: "fw-sin-elementos" },
      "La zona existe pero el selector de elemento no encontró nada. Si la página tiene turnos, revisa «Elemento».") : null,
    dias.length ? table([
      { t: "Sucursal", f: (d) => h("b", {}, d.sucursal || "—") },
      { t: "Fecha", f: (d) => d.fecha },
      { t: "Estado", f: (d) => h("span", { class: "badge " + (d.estado === "DISPONIBLE" ? "b-ok" : "b-warn") }, d.estado === "DISPONIBLE" ? "Disponible" : "Sin lugares") },
      { t: "Lugares", f: (d) => d.libres == null ? "—" : String(d.libres), num: true },
      { t: "Horas", f: (d) => (d.horas || []).join(", ") || "—" }], dias) : null,
    muestras.length ? h("div", { id: "fw-no-reconocidos" }, h("h3", {}, "Lo que no se reconoció (y se ignorará)"),
      h("ul", {}, muestras.map((m) => h("li", { "data-motivo": m.motivo },
        h("b", {}, m.leido ? `«${m.leido}»` : "(vacío)"), ` — ${FW_MOTIVOS[m.motivo] || m.motivo}`)))) : null);
}

function fwFormulario(inicial, lista) {
  const p = (inicial && inicial.perfil) || {};
  const st = { probado: null, resultado: null };
  const out = h("div", { id: "fw-prueba" });
  const errs = h("div", { class: "error", role: "alert", id: "fw-error" });
  const tram = h("select", { name: "tramite" }, Object.entries(TRAMITES).map(([k, v]) =>
    h("option", { value: k, selected: inicial && inicial.tramite === k ? true : null }, v)));
  const fmt = h("select", { name: "fecha_formato" }, FW_FORMATOS.map((x) =>
    h("option", { value: x, selected: (p.fecha && p.fecha.formato) === x ? true : null }, x)));
  const estr = h("select", { name: "estrategia" }, [["dom", "Contenido de la página (recomendado)"], ["ambas", "Contenido + comparación visual"],
    ["visual", "Solo comparación visual (dispara relecturas)"]].map(([k, t]) =>
    h("option", { value: k, selected: (inicial && inicial.estrategia) === k ? true : null }, t)));
  const ta = (name, valor, ph) => h("textarea", { name, rows: 3, placeholder: ph }, valor || "");
  const aut = (inicial && inicial.autorizacion) || {};
  const sha = h("input", { name: "aut_sha", value: aut.documento_sha256 || "", placeholder: "64 caracteres hexadecimales", class: "mono" });
  const archivo = h("input", { type: "file", id: "fw-aut-archivo", onchange: async () => {
    const f0 = archivo.files[0];
    if (!f0) return;
    const hx = await sha256Archivo(f0);
    if (hx) { sha.value = hx; invalidar(); } else errs.textContent = "Este navegador no calcula la huella aquí: pégala a mano.";
  } });
  const guardar = h("button", { type: "button", class: "btn primary", id: "btn-fw-guardar", disabled: true, onclick: async () => {
    errs.textContent = ""; guardar.disabled = true;
    try {
      const cuerpo = fwCuerpo(form);
      if (JSON.stringify(cuerpo) !== st.probado) throw new Error("El formulario cambió desde la prueba: vuelve a probar.");
      await api("/api/fuentes-web", { method: "POST", body: cuerpo });
      S.fuentes.borrador = null;
      toast("Página guardada: el observador ya la está mirando", 4000);
      refresh(true, true);
    } catch (err) { errs.textContent = err.message; actualizarGuardar(); }
  } }, "Guardar y observar");
  const actualizarGuardar = () => {
    const vigente = st.probado && JSON.stringify(fwCuerpo(form)) === st.probado;
    guardar.disabled = !(vigente && st.resultado && st.resultado.ok);
    guardar.title = guardar.disabled ? "Primero pulsa «Probar» con estos mismos datos y comprueba el resultado" : "";
  };
  const invalidar = () => {
    try { S.fuentes.borrador = fwCuerpo(form); } catch (_) { /* formulario a medias */ }
    if (st.probado && JSON.stringify(fwCuerpo(form)) !== st.probado) {
      out.setAttribute("data-vigente", "no");
      const aviso = out.querySelector("#fw-cambio");
      if (!aviso) out.prepend(h("p", { class: "warn-text", id: "fw-cambio" }, "Cambiaste el perfil: esta prueba ya no vale. Vuelve a probar."));
    }
    actualizarGuardar();
  };
  const probar = h("button", { type: "button", class: "btn", id: "btn-fw-probar", onclick: async () => {
    errs.textContent = ""; probar.disabled = true; guardar.disabled = true;
    out.replaceChildren(h("p", { class: "muted" }, "Abriendo la página una vez (sin guardar nada)…"));
    try {
      const cuerpo = fwCuerpo(form);
      const r = await api("/api/fuentes-web/probar", { method: "POST", body: cuerpo });
      st.probado = JSON.stringify(cuerpo);
      st.resultado = { ok: !(r.errores || []).length && r.vista && r.vista.estado === "ACTIVA" };
      out.setAttribute("data-vigente", "si");
      out.replaceChildren(fwResultado(r));
    } catch (err) { out.replaceChildren(); errs.textContent = err.message; st.probado = null; }
    probar.disabled = false; actualizarGuardar();
  } }, "Probar (vista previa)");

  const form = h("form", { class: "form", id: "form-fuente-web", oninput: () => invalidar(), onchange: () => invalidar(),
    onsubmit: (ev) => ev.preventDefault() },
    h("label", {}, "Nombre corto", h("input", { name: "nombre", value: (inicial && inicial.nombre) || "", placeholder: "turnos-norte", maxlength: 41 })),
    h("label", { class: "full" }, "Dirección de la página (URL)", h("input", { name: "url", value: (inicial && inicial.url) || "", placeholder: "https://…" })),
    h("label", {}, "Trámite de Cauce", tram),
    h("label", {}, "Sucursal fija (si la página no dice la ubicación)", h("input", { name: "sucursal", value: (inicial && inicial.sucursal && inicial.sucursal !== "*" ? inicial.sucursal : ""), placeholder: "NOR-1", maxlength: 32 })),
    h("label", {}, "Cómo detectar cambios", estr),
    h("h3", { class: "full" }, "Dónde están los turnos"),
    h("label", {}, "Zona (selector CSS)", h("input", { name: "zona", value: p.zona || "", placeholder: "#turnos" })),
    h("label", {}, "Elemento: cada turno o día", h("input", { name: "elemento", value: p.elemento || "", placeholder: ".turno" })),
    fwRegla("fecha", "Fecha (obligatoria)", "Dónde está la fecha dentro de cada elemento y en qué formato la escribe la página.", p.fecha),
    h("label", {}, "Formato de la fecha", fmt),
    fwRegla("estado", "Disponibilidad (opcional)", "Sin regla, todo elemento presente cuenta como disponible. Con regla, solo cuentan los textos de las listas: lo demás se ignora.", p.estado),
    h("label", {}, "Textos de «disponible» (uno por línea)", ta("disponible", ((p.estado || {}).disponible || []).join("\n"), "Disponible")),
    h("label", {}, "Textos de «sin lugares» (uno por línea)", ta("no_disponible", ((p.estado || {}).no_disponible || []).join("\n"), "Sin lugares")),
    fwRegla("libres", "Lugares libres (opcional)", null, p.libres),
    fwRegla("hora", "Hora (opcional; se guarda como evidencia)", null, p.hora),
    fwRegla("ubicacion", "Ubicación (opcional)", "Si la página lista varias oficinas. Cada texto debe estar en la tabla de abajo: lo que no esté, se ignora.", p.ubicacion),
    h("label", { class: "full" }, "Tabla de ubicaciones: «texto de la página = SUCURSAL», una por línea",
      ta("ubicaciones", Object.entries(p.ubicaciones || {}).map(([k, v]) => `${k} = ${v}`).join("\n"), "Oficina Norte Centro = NOR-1")),
    h("h3", { class: "full" }, "Ruido y sesión"),
    h("label", { class: "full" }, "Ignorar (relojes, anuncios, animaciones): un selector por línea", ta("ignorar", (p.ignorar || []).join("\n"), ".reloj")),
    h("label", {}, "Un cambio debe mantenerse (ms)", h("input", { name: "estabilidad_ms", type: "number", min: 0, max: 60000, step: 100, value: p.estabilidad_ms == null ? 800 : p.estabilidad_ms })),
    h("label", {}, "Señal de sesión caducada (selector, opcional)", h("input", { name: "sesion_perdida", value: p.sesion_perdida || "", placeholder: "#sesion-expirada" })),
    h("details", { class: "full", id: "fw-autorizacion", open: aut.ref ? true : null },
      h("summary", {}, "Sitio que no es de este equipo: autorización escrita"),
      h("p", { class: "muted" }, "Para observar un sitio externo hacen falta las tres cosas: https, que quien instaló Cauce haya permitido ese sitio ",
        "y la autorización escrita vigente del responsable del sitio. Sin ellas, Cauce no lo abre."),
      h("div", { class: "form" },
        h("label", {}, "Referencia del documento", h("input", { name: "aut_ref", value: aut.ref || "", maxlength: 64 })),
        h("label", {}, "Documento (calcula la huella)", archivo),
        h("label", {}, "Huella SHA-256", sha),
        h("label", {}, "Vigente hasta", h("input", { name: "aut_hasta", type: "date", value: aut.vigente_hasta || "" })))));
  // al volver a la pantalla (o al copiar una fuente) el formulario conserva el borrador
  setTimeout(actualizarGuardar, 0);
  return card(inicial && inicial.nombre ? `Página: ${inicial.nombre}` : "Nueva página a observar",
    h("p", { class: "muted" }, "Indica qué página mirar y cómo leer cada turno. «Probar» abre la página una vez y te enseña lo que Cauce ",
      "reconocería, sin guardar nada. Solo puedes guardar lo que probaste. Observar nunca envía formularios ni reserva."),
    form, h("div", { class: "actions" }, probar, guardar), errs, out,
    h("details", { class: "section" }, h("summary", {}, "Perfil en JSON (para copiar o revisar)"),
      h("pre", { class: "mono", id: "fw-json" }, ""), h("button", { type: "button", class: "btn ghost small", onclick: (ev) => {
        ev.target.previousSibling.textContent = JSON.stringify(fwCuerpo(form), null, 2);
      } }, "Mostrar")));
}

function fwEstado(f) {
  const o = f.observador;
  const vista = o && o.vista ? Object.values(o.vista)[0] : null;
  if (!f.activo) return h("span", { class: "badge" }, "detenida");
  if (f.estado === "DETENIDO_POR_FALLOS") return h("span", {}, h("span", { class: "badge b-bad" }, "Detenida"), " ", f.motivo || "");
  if (vista === "ACTIVA") return h("span", { class: "badge b-ok" }, "Mirando");
  return h("span", { class: "badge b-warn" }, (vista || f.estado || "arrancando").toLowerCase().replace(/_/g, " "));
}

async function viewFuentesWeb() {
  let d;
  try { d = await api("/api/fuentes-web"); } catch (err) {
    return h("div", { id: "fuentes-web", "data-disponible": "no" }, card("Páginas observadas", h("p", {}, err.message)));
  }
  const fuentes = d.fuentes || [];
  const errs = h("div", { class: "error", role: "alert" });
  const lista = table([
    { t: "Nombre", f: (f) => h("b", { "data-fuente": f.nombre }, f.nombre) },
    { t: "Página", f: (f) => h("span", { class: "mono" }, (() => { try { const u = new URL(f.url); return u.host + u.pathname; } catch (_) { return f.url; } })()) },
    { t: "Trámite", f: (f) => TRAMITES[f.tramite] || f.tramite },
    { t: "Estado", f: (f) => fwEstado(f) },
    { t: "", f: (f) => h("span", {},
      h("button", { class: "btn ghost small", "data-copiar": f.nombre, onclick: (ev) => {
        ev.stopPropagation();
        S.fuentes.borrador = { ...f, nombre: f.nombre + "-2" };
        refresh(true, true);
      } }, "Usar como base"),
      f.activo ? h("button", { class: "btn small", "data-detener": f.nombre, onclick: async (ev) => {
        ev.stopPropagation(); errs.textContent = "";
        try { await api(`/api/fuentes-web/${encodeURIComponent(f.nombre)}`, { method: "DELETE" }); toast("Observación detenida", 3000); refresh(true); }
        catch (err) { errs.textContent = err.message; }
      } }, "Detener") : null) }], fuentes);
  return h("div", { id: "fuentes-web", "data-fuentes": fuentes.length },
    h("div", { class: "page-head" }, h("div", {}, h("h1", {}, "Páginas observadas"),
      h("div", { class: "muted" }, "Cauce mira estas páginas y avisa al plan del día cuando aparece un día disponible. ",
        "Se configuran sin programar; un cambio solo cuenta si se reconoce con el perfil y se mantiene."))),
    card("Configuradas", lista, h("div", { class: "actions" },
      h("button", { class: "btn ghost small", onclick: () => refresh(true) }, "Actualizar estado")), errs),
    h("div", { class: "section" }, fwFormulario(S.fuentes.borrador, fuentes)));
}
