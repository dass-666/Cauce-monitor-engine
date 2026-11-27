// E8 · Centro de revisiones pendientes. Archivo aparte a propósito: red.js sigue siendo SOLO LECTURA (sin POST).
// Lo único que escribe esta pantalla es «Marcar como revisada» (POST /api/red/revisiones/{id}/resolver), solo para
// administradores, con nota y confirmación escrita, y solo si Monitor tiene una credencial «review» separada.
// E8 · Centro de revisiones pendientes (marcas «revisar a mano» de Network Engine, también las de sesiones ya cerradas)
function redRevisiones(s, resolubles) {
  if (!s) return h("p", { class: "muted" }, "Sin datos de sesiones de Network Engine.");
  const rv = s.revisiones || [];
  const total = s.revisiones_pendientes ?? (s.requieren_persona || 0);
  const cab = h("p", { "data-red-revisiones": total }, total
    ? h("span", { class: "badge b-bad" }, `${total} revisiones pendientes`)
    : h("span", { class: "badge b-ok" }, "Ninguna revisión pendiente"),
    total ? ` (${s.requieren_persona || 0} en sesiones activas, ${rv.length} sin sesión activa). Nada se repite solo: `
      + "revisa en el portal si la operación llegó a hacerse antes de resolver." : "");
  if (!rv.length) return h("div", {}, cab);
  const form = (r) => {
    if (!r.id || !r.confirmar_con) return "—";
    if (!isAdmin()) return h("span", { class: "muted" }, "solo administración");
    if (!resolubles) return h("code", { class: "muted" }, `cauce revisiones resolver ${r.id} --nota "…" --confirmar "${r.confirmar_con}"`);
    const nota = h("textarea", { rows: 2, maxlength: 500, placeholder: "Qué revisaste y qué hiciste (obligatorio)" });
    const conf = h("input", { placeholder: `Escribe ${r.confirmar_con} para confirmar` });
    const err = h("div", { class: "error", role: "alert" });
    const btn = h("button", { class: "btn", onclick: async () => {
      err.textContent = "";
      if (nota.value.trim().length < 5) { err.textContent = "La nota es obligatoria (mínimo 5 caracteres)."; return; }
      if (conf.value.trim() !== r.confirmar_con) { err.textContent = `Escribe exactamente ${r.confirmar_con}.`; return; }
      btn.disabled = true;
      try {
        await api(`/api/red/revisiones/${r.id}/resolver`, { method: "POST", body: { nota: nota.value, confirmar: conf.value } });
        go("red");
      } catch (e) { err.textContent = String(e.message || e); btn.disabled = false; }
    } }, "Marcar como revisada");
    return h("div", { class: "revision-form" }, nota, conf, btn, err);
  };
  return h("div", {}, cab, table([
    { t: "Sesión", f: (r) => r.tipo === "general" ? h("b", {}, "General (todo el motor)") : h("b", {}, r.etiqueta || "sesión") },
    { t: "Motivo", f: (r) => redTexto(r.motivo) },
    { t: "Desde", f: (r) => redSeg(r.hace_s) },
    { t: "Estado", f: () => h("span", { class: "badge b-bad" }, "pendiente") },
    { t: "Resolver", f: form }], rv));
}

