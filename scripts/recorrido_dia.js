// v1.6 · Recorrido POR DÍA con un navegador real (Playwright), en el PANEL del motor:
//   expediente -> preparación -> validación (campos manuales) -> calendario diario -> día -> confirmación humana -> folio
// Uso: node scripts/recorrido_dia.js <motor> <operador> <clave_op> <admin> <clave_admin> <código> <carpeta_capturas> <fase>
//   fase = "flujo" (hasta el folio) | "tras_reinicio" (folio recuperado + vistas) | "sin_conexion" (calendario caído)
// Imprime "RESULTADO {json}". Datos ficticios; solo 127.0.0.1. Los campos manuales se TECLEAN (no se pegan).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, opUser, opPass, adUser, adPass, codigo, outDir, fase] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);
const shot = async (page, name) => { await page.screenshot({ path: path.join(outDir, name), fullPage: true }); log("captura", name); };

async function login(page, u, p) {
  await page.goto(base + "/");
  await page.fill("input[name=username]", u);
  await page.fill("input[name=password]", p);
  await page.click("button[type=submit]");
  await page.waitForSelector("#nav a");
}
async function abrirExpediente(page) {
  await page.goto(base + "/#captura");
  await page.waitForSelector("[data-tab=expedientes]");
  await page.click("[data-tab=expedientes]");
  const fila = page.locator("#view table tbody tr", { hasText: codigo });
  await fila.waitFor({ timeout: 30000 });
  await fila.click();
  await page.waitForSelector("#flujo");
}

(async () => {
  const browser = await chromium.launch();
  const errores = [];
  const res = { expediente: codigo, fase };
  const nuevo = async (w, hgt) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: hgt } });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errores.push(e.message));
    p.on("console", (m) => { if (m.type() === "error") errores.push(m.text()); });
    return p;
  };

  if (fase === "flujo") {
    const page = await nuevo(1366, 900);
    await login(page, opUser, opPass);
    await page.goto(base + "/#captura");
    await page.waitForSelector("[data-tab=resumen][aria-selected=true]");
    await page.waitForTimeout(500);
    await shot(page, "01_resumen_operador.png");
    await abrirExpediente(page);
    res.recorrido_visible = await page.$$eval(".recorrido li", (l) => l.map((x) => x.textContent));
    await shot(page, "02_ficha_asignada_recorrido.png");
    await page.click("#btn-preparar");
    await page.waitForSelector("#captura-manual");
    // la casilla de revisión es obligatoria antes de enviar
    if (!(await page.isDisabled("#btn-enviar"))) throw new Error("el envío no debe permitirse sin revisión humana");
    // 1) se teclea MAL la clave y se marca el aviso: solo falla la clave; lo demás se conserva
    await page.locator("#manual-clave_identidad").pressSequentially("ab12", { delay: 20 });
    await page.check("#manual-acepto_privacidad");
    await page.check("#chk-revisado");
    await page.click("#btn-enviar");
    await page.waitForSelector("[data-error-de=clave_identidad]:not(:empty)");
    res.error_campo_manual = await page.textContent("[data-error-de=clave_identidad]");
    res.casilla_conservada = await page.isChecked("#manual-acepto_privacidad");
    res.sin_pegar = await page.getAttribute("#manual-clave_identidad", "data-sin-pegar");
    await shot(page, "03_campo_manual_con_error.png");
    // 2) se corrige tecleándola (el portal no permite pegar: se respeta)
    await page.fill("#manual-clave_identidad", "");
    await page.locator("#manual-clave_identidad").pressSequentially("KLM305918", { delay: 20 });
    await shot(page, "04_campos_manuales_listos.png");
    await page.click("#btn-enviar");
    await page.waitForSelector("#flujo .mes button.dia", { timeout: 30000 });
    res.dias_disponibles = await page.$$eval("#flujo .mes button.dia", (b) => b.length);
    res.mes = await page.getAttribute("#flujo [data-mes]", "data-mes");
    await shot(page, "05_calendario_mensual.png");
    const fecha = await page.getAttribute("#flujo .mes button.dia >> nth=0", "data-fecha");
    res.fecha_elegida = fecha;
    await page.click(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
    await page.waitForSelector("#chk-confirmo");
    if (!(await page.isDisabled("#btn-confirmar"))) throw new Error("confirmar no debe permitirse sin la casilla");
    await shot(page, "06_confirmacion_humana.png");
    await page.check("#chk-confirmo");
    await page.click("#btn-confirmar");
    await page.waitForSelector("#folio", { timeout: 30000 });
    res.folio = await page.getAttribute("#folio", "data-folio");
    res.fecha_confirmada = await page.textContent("#fecha-confirmada");
    log("folio en el panel:", res.folio);
    await shot(page, "07_folio.png");
  }

  if (fase === "tras_reinicio") {
    const page = await nuevo(1366, 900);
    await login(page, opUser, opPass);
    await abrirExpediente(page);
    await page.waitForSelector("#folio", { timeout: 30000 });
    res.folio_tras_reinicio = await page.getAttribute("#folio", "data-folio");
    await shot(page, "08_folio_tras_reinicio.png");
    const adm = await nuevo(1366, 900);
    await login(adm, adUser, adPass);
    await adm.goto(base + "/#captura");
    for (const [i, t] of ["resumen", "dias", "expedientes", "confirmaciones", "historial"].entries()) {
      await adm.click(`[data-tab=${t}]`);
      await adm.waitForSelector(`[data-tab=${t}][aria-selected=true]`);
      await adm.waitForTimeout(600);
      await shot(adm, `${String(10 + i)}_panel_${t}.png`);
    }
    res.banner_calendario = await adm.$$eval("[data-fuente]", (p) => p.map((x) => x.textContent));
    for (const [w, hgt, n] of [[390, 844, "20_movil_resumen.png"], [390, 844, "21_movil_calendario.png"]]) {
      const m = await nuevo(w, hgt);
      await login(m, adUser, adPass);
      await m.goto(base + "/#captura");
      await m.waitForSelector("[data-tab=resumen]");
      if (n.includes("calendario")) { await m.click("[data-tab=dias]"); await m.waitForSelector("[data-tab=dias][aria-selected=true]"); }
      await m.waitForTimeout(600);
      res["desborde_" + n.slice(3, -4)] = await m.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      await shot(m, n);
    }
  }

  if (fase === "sin_conexion") {
    const adm = await nuevo(1366, 900);
    await login(adm, adUser, adPass);
    await adm.goto(base + "/#captura");
    await adm.click("[data-tab=dias]");
    await adm.waitForSelector("[data-fuente].error", { timeout: 40000 });
    res.banner_sin_conexion = await adm.$$eval("[data-fuente]", (p) => p.map((x) => x.textContent));
    res.dias_desconocidos = await adm.$$eval(".mes [data-estado=DESCONOCIDO]", (d) => d.length);
    res.dias_disponibles_mostrados = await adm.$$eval(".mes [data-estado=DISPONIBLE]", (d) => d.length);
    await shot(adm, "30_calendario_sin_conexion.png");
  }

  res.errores_js = errores;
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch((e) => { console.error("FALLO", e); process.exit(1); });
