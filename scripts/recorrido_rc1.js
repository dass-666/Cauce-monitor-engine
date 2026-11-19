// RC1 · Recorrido administrativo completo SOLO con la interfaz (Playwright/Chromium). Datos FICTICIOS.
// Una persona administrativa: abre Cauce -> importa 6 clientes -> ve la clasificación y los bloqueos ->
// registra el consentimiento de quien lo tiene firmado -> añade al plan los elegibles -> enciende el observador ->
// abre un día en el calendario del laboratorio -> ve la coincidencia -> opera (confirmación SIMULADA) -> folio ->
// historial -> reimporta el mismo archivo sin duplicar.
// Lo lanza scripts/recorrido_rc1.py. Uso: node scripts/recorrido_rc1.js <motor> <capturas> <entrada.json>
// Imprime "RESULTADO {json}" (solo códigos y estados; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const [base, outDir, entradaPath] = process.argv.slice(2);
const E = JSON.parse(fs.readFileSync(entradaPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });
const CLAVE = "QXZ482915";      // campo manual ficticio que se teclea en el formulario del portal de laboratorio

(async () => {
  const browser = await chromium.launch();
  const res = { pasos: [], errores_js: [], http_errores: [] };
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
  const A = await ctx.newPage();
  A.on("pageerror", (e) => res.errores_js.push(e.message));
  A.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) res.errores_js.push(m.text()); });
  A.on("response", (r) => { if (r.status() >= 400) res.http_errores.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  let n = 0;
  const shot = async (nombre) => { n += 1; await A.screenshot({ path: path.join(outDir, `${String(n).padStart(2, "0")}_${nombre}.png`), fullPage: true }); };
  const paso = (k, ok, detalle) => { res.pasos.push({ paso: k, ok: !!ok, detalle }); if (!ok) throw new Error(`paso ${k} falló: ${JSON.stringify(detalle)}`); };
  const ir = async (vista, sel) => { await A.goto(base + "/#" + vista); await A.waitForTimeout(300); await A.reload(); await A.waitForSelector(sel || "#view > *"); };
  const hasta = async (fn, ms = 60000, cada = 1000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await fn()) return true; await new Promise((r) => setTimeout(r, cada)); }
    return false;
  };
  const kpi = async (id) => Number((await A.textContent("#kpi-" + id)).replace(/\D/g, "") || 0);
  const filasExp = async () => A.$$eval("#exp-tabla tbody tr", (xs) => xs.map((x) => ({ codigo: x.dataset.expediente, situacion: x.dataset.situacion })));

  try {
    // ------------------------------------------------------------ 1. abrir Cauce: la mesa de control
    await A.goto(base + "/");
    await A.fill("input[name=username]", E.admin[0]); await A.fill("input[name=password]", E.admin[1]);
    await A.click("button[type=submit]");
    await A.waitForSelector("#panel-kpis");
    await shot("mesa_de_control_vacia");
    paso("1_abrir_cauce", (await A.getAttribute("#nav a.active", "href")) === "#panel", { inicio: "Mesa de control" });

    // ------------------------------------------------------------ 2. importar los seis registros
    await ir("importar", "#imp-archivo");
    await A.setInputFiles("#imp-archivo", E.archivo);
    await A.waitForSelector("#imp-vista-previa");
    const prev = { nuevos: await A.getAttribute("#imp-vista-previa", "data-nuevos"), hoja: await A.inputValue("#imp-hoja") };
    const avisos = await A.textContent("#imp-avisos");
    await shot("importar_vista_previa");
    await A.click("#btn-importar");
    await A.waitForSelector("#imp-resultado");
    const lote = await A.getAttribute("#imp-resultado", "data-lote");
    const creadas = await A.getAttribute("#imp-resultado", "data-creadas");
    await shot("importar_resultado_lote");
    paso("2_importar_seis", prev.nuevos === "6" && creadas === "6" && prev.hoja === "Clientes"
      && /Contraseña del portal.*descartada/.test(avisos) && /no equivale a tener consentimiento/.test(avisos),
      { lote, vista_previa_nuevos: prev.nuevos, creadas, hoja: prev.hoja, contrasena_descartada: true, columna_consentimiento_ignorada: true });

    // ------------------------------------------------------------ 3-4. clasificación y bloqueos
    await A.click("#btn-lote-expedientes");
    await A.waitForSelector("#exp-tabla tbody tr");
    const clasif = await filasExp();
    await shot("expedientes_del_lote_clasificados");
    const por = (s) => clasif.filter((x) => x.situacion === s).length;
    paso("3_clasificacion_y_faltantes", clasif.length === 6 && por("INCOMPLETO") === 2 && por("SIN_CONSENTIMIENTO") === 4,
      { situaciones: clasif.map((x) => `${x.codigo}:${x.situacion}`) });
    const seleccionables0 = await A.$$("#exp-tabla [data-sel]");
    paso("4_incompletos_bloqueados", seleccionables0.length === 0, { seleccionables_antes_de_consentimiento: seleccionables0.length });

    // ------------------------------------------------------------ 5. el administrador prepara: consentimientos con evidencia
    const doc = path.join(outDir, "formato_consentimiento_ficticio.pdf");
    fs.writeFileSync(doc, "%PDF-1.4\n% formato de consentimiento FICTICIO del laboratorio (RC1)\n%%EOF\n");
    const huella = crypto.createHash("sha256").update(fs.readFileSync(doc)).digest("hex");
    const consentidos = [];
    for (const k of E.con_consentimiento) {          // posiciones (0-5) de las filas con formato firmado
      const codigo = clasif[k].codigo;
      await A.click(`[data-consentir="${codigo}"]`);
      await A.waitForSelector("#form-consentimiento-admin");
      await A.selectOption("#cons-medio", "FIRMA_PAPEL");
      await A.fill("#cons-aviso", "AP-2026-01");
      await A.setInputFiles("#cons-archivo", doc);
      await hasta(async () => (await A.inputValue("#cons-huella")) === huella, 5000, 200);
      await A.fill("#cons-ref", `Carpeta LAB-RC1 hoja ${k + 1}`);
      if (!consentidos.length) await shot("registrar_consentimiento_con_huella");
      await A.click("#btn-consentimiento-admin");
      await A.waitForSelector("#drawer.hidden", { state: "attached" });
      await A.waitForTimeout(400);
      consentidos.push(codigo);
    }
    await ir("expedientes", "#exp-tabla tbody tr");
    const tras = await filasExp();
    await shot("expedientes_tras_consentimiento");
    const listos = tras.filter((x) => x.situacion === "LISTO").map((x) => x.codigo);
    paso("5_revisar_los_que_pueden_entrar", listos.length === 3 && consentidos.every((c) => listos.includes(c)),
      { listos, siguen_bloqueados: tras.filter((x) => x.situacion !== "LISTO").map((x) => `${x.codigo}:${x.situacion}`) });

    // ------------------------------------------------------------ 6. plan: solo los elegibles
    await A.click("#sel-todos");
    await A.waitForSelector("#btn-al-plan-lote:not([disabled])");
    await shot("seleccion_para_el_plan");
    await A.click("#btn-al-plan-lote");
    await A.waitForSelector("#plan-lote-resultado");
    const aceptados = await A.getAttribute("#plan-lote-resultado", "data-aceptados");
    await A.waitForSelector("#exp-tabla tbody tr");
    const enPlan = (await filasExp()).filter((x) => x.situacion === "EN_PLAN").map((x) => x.codigo);
    await shot("plan_solo_elegibles");
    paso("6_plan_solo_elegibles", aceptados === "3" && enPlan.length === 3 && listos.every((c) => enPlan.includes(c)), { en_plan: enPlan });

    // ------------------------------------------------------------ 7a. observador (laboratorio) en marcha
    await ir("panel", "#panel-kpis");
    if (await A.$("#btn-iniciar-observador")) {
      await A.fill("#obs-sucursal", "NOR-1");
      await A.selectOption("#obs-tramite", "alta");
      await A.fill("#obs-mes", E.mes);
      await A.click("#btn-iniciar-observador");
    }
    const obsOk = await hasta(async () => { await ir("panel", "#panel-kpis"); return (await A.getAttribute("#observador", "data-vista")) === "ACTIVA"; }, 90000, 2000);
    await shot("mesa_de_control_plan_y_observador");
    paso("7a_observador_activo", obsOk, { vista: await A.getAttribute("#observador", "data-vista") });

    // ------------------------------------------------------------ 7b. el calendario del laboratorio publica una fecha
    await ir("laboratorio-calendario", "#calendario-lab[data-disponible=si]");
    for (let k = 0; k < 6 && (await A.getAttribute("#calendario-lab", "data-mes")) !== E.mes; k++) {
      await A.click("#calendario-lab button:has-text('Mes siguiente')");
      await A.waitForTimeout(700);
    }
    await A.click(`[data-abrir="${E.dia}"]`);
    await A.waitForSelector(`#calendario-lab-mes [data-fecha="${E.dia}"][data-estado=DISPONIBLE]`);
    await shot("calendario_laboratorio_publica_fecha");
    paso("7b_fecha_publicada", true, { dia: E.dia, sucursal: "NOR-1", tramite: "alta" });

    // ------------------------------------------------------------ 8. el sistema detecta la coincidencia
    let coincide = null;
    await hasta(async () => { await ir("panel", "#panel-kpis"); coincide = await A.$("[data-coincidencia]"); return !!coincide; }, 90000, 1500);
    const codCoincide = coincide && await coincide.getAttribute("data-coincidencia");
    const kpis8 = { coincidencias: await kpi("coincidencias"), confirmadas: await kpi("confirmadas") };
    await shot("coincidencia_detectada_no_es_cita");
    paso("8_coincidencia_detectada", codCoincide === E.esperado_coincidencia && kpis8.coincidencias === 1 && kpis8.confirmadas === 0,
      { expediente: codCoincide, ...kpis8 });

    // ------------------------------------------------------------ 9. operación simulada y folio
    await A.click(`[data-operar="${codCoincide}"]`);
    await A.waitForSelector("#captura-manual", { timeout: 30000 });
    await A.locator("#manual-clave_identidad").pressSequentially(CLAVE, { delay: 5 });
    await A.check("#manual-acepto_privacidad");
    await A.check("#chk-revisado");
    await shot("operacion_revision_de_datos");
    await A.click("#btn-enviar");
    await A.waitForSelector("#flujo .mes");
    let dia = await A.$(`#flujo .mes button.dia[data-fecha="${E.dia}"]`);
    for (let k = 0; !dia && k < 3; k++) {
      await A.click("#flujo .filters button:nth-of-type(2)");
      await A.waitForTimeout(700);
      dia = await A.$(`#flujo .mes button.dia[data-fecha="${E.dia}"]`);
    }
    if (!dia) throw new Error("el día abierto no aparece en el calendario del portal");
    await dia.click();
    await A.check("#chk-confirmo");          // confirmación humana: la SIMULA este script
    await shot("operacion_confirmacion_simulada");
    await A.click("#btn-confirmar");
    await A.waitForSelector("#btn-cerrar-atencion", { timeout: 60000 });
    const folio = await A.getAttribute("#folio", "data-folio");
    await shot("cita_confirmada_con_folio");
    paso("9_operacion_y_folio", /\S/.test(folio || ""), { folio, confirmacion: "SIMULADA" });
    res.folio = folio;

    // ------------------------------------------------------------ 10. el historial conserva el resultado
    await A.click("#btn-cerrar-atencion");
    await A.waitForSelector("#historial-expediente[data-disponible=si]");
    const folioHist = await A.textContent("#historial-folio");
    const tipos = await A.$$eval("#historial-linea li", (xs) => [...new Set(xs.map((x) => x.dataset.tipo))]);
    await shot("historial_con_folio");
    let confirmadaPanel = false;
    await hasta(async () => { await ir("panel", "#panel-kpis"); confirmadaPanel = !!(await A.$(`#panel-confirmadas [data-folio="${folio}"]`)); return confirmadaPanel; }, 30000, 1500);
    await shot("mesa_de_control_cita_confirmada");
    paso("10_historial_y_panel", folioHist === folio && tipos.includes("IMPORTADO") && confirmadaPanel,
      { folio_historial: folioHist, eventos: tipos, en_panel: confirmadaPanel });

    // ------------------------------------------------------------ 11. segunda importación del mismo archivo
    await ir("importar", "#imp-archivo");
    if (await A.$("#imp-resultado")) { await A.click("#imp-resultado button:has-text('Cargar otro archivo')"); await A.waitForSelector("#imp-archivo"); }
    await A.setInputFiles("#imp-archivo", E.archivo);
    await A.waitForSelector("#imp-vista-previa");
    const prev2 = { nuevos: await A.getAttribute("#imp-vista-previa", "data-nuevos"), existen: await A.getAttribute("#imp-vista-previa", "data-existen"),
      aviso: !!(await A.$("#imp-ya-cargado")) };
    await shot("reimportar_mismo_archivo_vista_previa");
    await A.click("#btn-importar");
    await A.waitForSelector("#imp-resultado");
    const lote2 = { lote: await A.getAttribute("#imp-resultado", "data-lote"), creadas: await A.getAttribute("#imp-resultado", "data-creadas"),
      existentes: await A.getAttribute("#imp-resultado", "data-existentes") };
    await shot("reimportar_mismo_archivo_resultado");
    await ir("expedientes", "#exp-tabla tbody tr");
    const total = (await filasExp()).length;
    paso("11_reimportar_no_duplica", prev2.nuevos === "0" && prev2.existen === "6" && prev2.aviso && lote2.creadas === "0"
      && lote2.existentes === "6" && total === 6, { ...prev2, segundo_lote: lote2, expedientes_totales: total });
    await ir("panel", "#panel-kpis");
    res.kpis_finales = {};
    for (const k of ["total", "incompletos", "sin_consentimiento", "en_plan", "coincidencias", "confirmadas"]) res.kpis_finales[k] = await kpi(k);
    await shot("mesa_de_control_final");
  } catch (e) {
    res.fallo = String(e.message || e).slice(0, 400);
    await shot("ZZ_fallo").catch(() => {});
  }
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
  process.exit(res.fallo ? 1 : 0);
})();
