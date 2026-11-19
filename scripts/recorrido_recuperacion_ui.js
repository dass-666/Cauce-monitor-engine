// Recuperación segura desde la interfaz (Playwright/Chromium). Datos FICTICIOS, laboratorio propio.
// Lo lanza scripts/recorrido_recuperacion_ui.py. Uso: node recorrido_recuperacion_ui.js <motor> <capturas> <entrada.json>
// Imprime "RESULTADO {json}" (solo códigos y estados; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, outDir, entradaPath] = process.argv.slice(2);
const E = JSON.parse(fs.readFileSync(entradaPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });

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
  const drawerCerrado = () => A.waitForSelector("#drawer.hidden", { state: "attached", timeout: 20000 });
  const resolverBtn = (tipo) => `#rec-discrepancias button[data-resolver$=":${tipo}"]`;
  const rellenar = async (motivo) => { await A.fill("#drawer textarea[name=motivo]", motivo); await A.check("#drawer input[name=confirmo]"); };

  try {
    // 1. La mesa de control avisa del bloqueo
    await A.goto(base + "/");
    await A.fill("input[name=username]", E.admin[0]); await A.fill("input[name=password]", E.admin[1]);
    await A.click("button[type=submit]");
    await A.waitForSelector("#kpi-recuperacion");
    await shot("mesa_de_control_recuperacion_bloqueada");
    paso("1_mesa_de_control_muestra_bloqueo", (await A.getAttribute("#kpi-recuperacion", "data-bloqueado")) === "1",
      { kpi: await A.textContent("#kpi-recuperacion") });

    // 2. Pantalla de recuperación: motivos, discrepancias y desbloqueo desactivado
    await A.click("[data-kpi=recuperacion]");
    await A.waitForSelector("#rec-estado");
    const motivos = await A.$$eval("#rec-motivos li", (xs) => xs.map((x) => x.dataset.motivo));
    const tipos = await A.$$eval("#rec-discrepancias [data-tipo]", (xs) => xs.map((x) => x.dataset.tipo));
    const levantarDesactivado = await A.isDisabled("#btn-rec-levantar");
    const navOk = (await A.getAttribute("#nav a.active", "href")) === "#recuperacion";
    await shot("recuperacion_bloqueada_dos_discrepancias");
    paso("2_pantalla_lista_motivos_y_discrepancias", navOk && motivos.includes("RESTAURACION_PENDIENTE") && motivos.includes("DISCREPANCIAS")
      && tipos.length === 2 && tipos.includes("CONFIRMACION_AUSENTE") && tipos.includes("REVOCACION_AUSENTE") && levantarDesactivado,
      { motivos, tipos, levantar_desactivado: levantarDesactivado });

    // 3. Un folio que no es el del diario se rechaza y se ve el motivo
    await A.click(resolverBtn("CONFIRMACION_AUSENTE"));
    await A.waitForSelector("#form-resolver");
    await A.selectOption("#form-resolver select[name=accion]", "registrar_folio");
    await A.fill("#form-resolver input[name=folio]", E.folio_falso);
    await rellenar("folio leído en el portal de laboratorio");
    await A.click("#form-resolver button[type=submit]");
    await A.waitForFunction(() => /no es el que el portal/.test(document.querySelector("#form-resolver .error").textContent));
    await shot("folio_que_no_coincide_rechazado");
    paso("3_folio_incorrecto_rechazado_en_pantalla", true, { error: (await A.textContent("#form-resolver .error")).slice(0, 120) });

    // 4. Con el folio del portal (SIMULADO: lo leyó el script del control del laboratorio)
    await A.fill("#form-resolver input[name=folio]", E.folio_portal);
    await A.click("#form-resolver button[type=submit]");
    await drawerCerrado();
    await A.waitForFunction(() => document.querySelectorAll("#rec-discrepancias button[data-resolver]").length === 1);
    await shot("confirmacion_conciliada_queda_una");
    paso("4_confirmacion_conciliada_con_folio_del_portal", true, { quedan: 1 });

    // 5. Reaplicar la revocación de G
    await A.click(resolverBtn("REVOCACION_AUSENTE"));
    await A.waitForSelector("#form-resolver");
    const opciones = await A.$$eval("#form-resolver select[name=accion] option", (xs) => xs.map((x) => x.value));
    await shot("revocacion_formulario");
    await rellenar("revocación por escrito registrada en el diario");
    await A.click("#form-resolver button[type=submit]");
    await drawerCerrado();
    await A.waitForFunction(() => document.querySelectorAll("#rec-discrepancias button[data-resolver]").length === 0);
    const sigueBloqueado = (await A.getAttribute("#rec-estado", "data-bloqueado")) === "1";
    const levantarActivo = !(await A.isDisabled("#btn-rec-levantar"));
    await shot("cero_pendientes_sigue_bloqueado");
    paso("5_revocacion_reaplicada_cero_pendientes_y_sigue_bloqueado", opciones.join() === "reaplicar_revocacion" && sigueBloqueado && levantarActivo,
      { opciones, sigue_bloqueado: sigueBloqueado, levantar_activo: levantarActivo });

    // 6. Levantar el bloqueo con motivo
    await A.click("#btn-rec-levantar");
    await A.waitForSelector("#form-levantar");
    await rellenar("restauración revisada; 2 discrepancias conciliadas");
    await shot("levantar_bloqueo_formulario");
    await A.click("#form-levantar button[type=submit]");
    await drawerCerrado();
    await A.waitForSelector("#rec-estado[data-bloqueado='0']");
    const hist = Number(await A.getAttribute("#rec-historial", "data-n"));
    await shot("recuperacion_operable_con_historial");
    paso("6_bloqueo_levantado_e_historial_de_auditoria", hist >= 3, { historial: hist });

    // 7. La mesa de control vuelve a «Operable»
    await A.goto(base + "/#panel"); await A.reload();
    await A.waitForSelector("#kpi-recuperacion");
    await shot("mesa_de_control_operable");
    paso("7_mesa_de_control_operable", (await A.getAttribute("#kpi-recuperacion", "data-bloqueado")) === "0", null);
  } catch (e) {
    res.pasos.push({ paso: "error", ok: false, detalle: String(e.message || e).slice(0, 400) });
    try { await shot("error"); } catch (_) { /* nada */ }
  } finally {
    await browser.close();
    console.log("RESULTADO " + JSON.stringify(res));
  }
})();
