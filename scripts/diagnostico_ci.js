// Diagnóstico COMPARTIDO por los recorridos de interfaz (v2.3 y RC1) cuando falla la espera de la celda DISPONIBLE del
// calendario del laboratorio. SOLO observa: no cambia aserciones, tiempos ni el comportamiento del calendario.
// Devuelve estados, banderas y códigos; nunca datos de personas (ver docs/CI_RESERVACIONES_DIAGNOSTICO.md).
async function diagnosticarCalendario(page, dia, pasos) {
  const hecho = (pasos || []).filter((p) => p.ok).map((p) => p.paso);
  const base = { ultimo_paso_ok: hecho.length ? hecho[hecho.length - 1] : null };
  const lectura = page.evaluate(async (d) => {
    const r = { vista: S.view, repintando: typeof rendering !== "undefined" ? rendering : null,
      pendiente: S.pendingForce || false, sucio: S.dirty || false, sin_conexion: !!S.offline };
    const c = document.querySelector(`#calendario-lab-mes [data-fecha="${d}"]`);
    r.celda_en_pantalla = c ? c.getAttribute("data-estado") : "NO_HAY_CELDA";
    const er = document.querySelector("#calendario-lab-error");
    r.error_en_pantalla = er ? er.textContent.slice(0, 160) : null;
    r.tarjeta_error = !!document.querySelector("#view .error:not(#calendario-lab-error)");
    try {
      const x = await Promise.race([api("/api/laboratorio/portal/calendario?sucursal=NOR-1&tramite=alta&mes=" + d.slice(0, 7)),
        new Promise((_, no) => setTimeout(() => no(new Error("sin respuesta en 10 s")), 10000))]);
      const z = x.dias.find((y) => y.fecha === d); r.celda_en_servidor = z ? z.estado : "SIN_DATO";
    } catch (err) { r.servidor = String(err.message || err).slice(0, 160); }
    return r;
  }, dia);
  const plazo = new Promise((_, no) => setTimeout(() => no(new Error("la página no respondió al diagnóstico (20 s)")), 20000));
  try { return { ...base, ...(await Promise.race([lectura, plazo])) }; }
  catch (err) { return { ...base, diagnostico_fallido: String(err.message || err).slice(0, 160) }; }
}
module.exports = { diagnosticarCalendario };
