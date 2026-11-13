// v1.5 · Recorrido completo con un navegador real (Playwright):
//   Expediente -> formulario -> calendario -> horario -> confirmación -> folio   (en el PANEL del motor)
// y, aparte, el portal de prueba llenado directamente A PARTIR DEL CONTRATO JSON (demuestra que el
// contrato de campos es independiente de cómo se navegue).
// Uso: node scripts/recorrido_reservacion.js <motor> <portal> <operador> <clave_op> <admin> <clave_admin>
//        <código_expediente> <carpeta_capturas> <json_registro_portal>
// Imprime al final una línea "RESULTADO {json}". Datos ficticios; solo 127.0.0.1.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, portal, opUser, opPass, adUser, adPass, codigo, outDir, regJson] = process.argv.slice(2);
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

(async () => {
  const browser = await chromium.launch();
  const errores = [];
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errores.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errores.push(m.text()); });
  const res = { expediente: codigo };

  // ---------------------------------------------------------------- operador: del expediente al folio
  await login(page, opUser, opPass);
  await page.goto(base + "/#captura");
  await page.waitForSelector("text=Captura y reservaciones");
  const fila = page.locator("#view table tbody tr", { hasText: codigo });
  await fila.waitFor({ timeout: 30000 });
  await shot(page, "01_expedientes_operador.png");
  await fila.click();
  await page.waitForSelector("#btn-preparar");
  await shot(page, "02_ficha_expediente.png");
  await page.click("#btn-preparar");
  await page.waitForSelector("#chk-revisado");
  res.campos_preparados = await page.$$eval("#flujo table tbody tr", (rs) => rs.length);
  await shot(page, "03_formulario_preparado.png");
  if (!(await page.isDisabled("#btn-enviar"))) throw new Error("el envío no debe permitirse sin revisión humana");
  await page.check("#chk-revisado");
  await page.click("#btn-enviar");
  await page.waitForSelector("#flujo [data-horario]");
  res.horarios_visibles = await page.$$eval("#flujo [data-horario]", (b) => b.length);
  await shot(page, "04_calendario.png");
  const horario = await page.getAttribute("#flujo [data-horario] >> nth=0", "data-horario");
  res.horario = horario;
  await page.click(`#flujo [data-horario="${horario}"]`);
  await page.waitForSelector("#chk-confirmo");
  await shot(page, "05_confirmacion.png");
  if (!(await page.isDisabled("#btn-confirmar"))) throw new Error("confirmar no debe permitirse sin la casilla");
  await page.check("#chk-confirmo");
  await page.click("#btn-confirmar");
  await page.waitForSelector("#folio", { timeout: 30000 });
  res.folio = await page.getAttribute("#folio", "data-folio");
  log("folio en el panel:", res.folio);
  await shot(page, "06_folio.png");

  // ---------------------------------------------------------------- administrador: secciones del panel
  const ctx2 = await browser.newContext({ viewport: { width: 1366, height: 900 } });
  const adm = await ctx2.newPage();
  adm.on("pageerror", (e) => errores.push(e.message));
  await login(adm, adUser, adPass);
  await adm.goto(base + "/#captura");
  await adm.waitForSelector("[data-tab=expedientes]");
  const tabs = ["expedientes", "preparacion", "calendario", "reservaciones", "confirmaciones", "historial"];
  for (const [i, t] of tabs.entries()) {
    await adm.click(`[data-tab=${t}]`);
    await adm.waitForSelector(`[data-tab=${t}][aria-selected=true]`);
    await adm.waitForTimeout(400);
    await shot(adm, `${String(10 + i)}_panel_${t}.png`);
  }
  res.historial_normalizados = await adm.$$eval("#view table tbody tr .badge", (b) => b.map((x) => x.textContent));
  const ancho = await browser.newContext({ viewport: { width: 390, height: 844 } });   // teléfono
  const movil = await ancho.newPage();
  await login(movil, adUser, adPass);
  await movil.goto(base + "/#captura");
  await movil.waitForSelector("[data-tab=expedientes]");
  await movil.waitForTimeout(400);
  res.desborde_horizontal_movil = await movil.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  await shot(movil, "20_panel_movil.png");

  // ---------------------------------------------------------------- portal llenado desde el contrato
  const contrato = JSON.parse(fs.readFileSync(path.join(__dirname, "..", "monitoreo", "captura", "formularios", "contratos",
    "portal_laboratorio.json"), "utf8"));
  const reg = JSON.parse(regJson);
  const valores = { operacion: "nueva", tipo_tramite: reg.tramite, id_registro_previo: reg.identificador,
    fecha_nacimiento: reg.fecha_nacimiento, telefono: "5550004242", correo: "contrato@ejemplo.test" };
  const ctx3 = await browser.newContext({ viewport: { width: 1100, height: 900 } });
  const p = await ctx3.newPage();
  const d = contrato.pantallas.datos;
  await p.goto(portal + d.url);
  await shot(p, "30_portal_datos.png");
  for (const c of d.campos) {
    const sel = `[data-formulario="${d.formulario}"] [name="${c.nombre}"]`;
    if (c.tipo === "opcion") await p.selectOption(sel, valores[c.origen]);
    else await p.fill(sel, valores[c.origen]);
  }
  await p.click(`[data-formulario="${d.formulario}"] button[type=submit]`);
  await p.waitForURL((u) => u.pathname === contrato.pantallas.disponibilidad.url);
  await p.selectOption('[name="region"]', "sur");
  await p.click("#filtrar");
  await p.waitForSelector(`[name="${contrato.pantallas.disponibilidad.opcion_horario}"]`);
  await shot(p, "31_portal_disponibilidad.png");
  await p.check(`[name="${contrato.pantallas.disponibilidad.opcion_horario}"] >> nth=0`);
  await p.click("#seleccionar");
  await p.waitForSelector(`[data-formulario="${contrato.pantallas.confirmacion.formulario}"]`);
  await shot(p, "32_portal_confirmacion.png");
  await p.click("#confirmar");
  await p.waitForSelector(`[${contrato.pantallas.resultado.folio_atributo}]`);
  res.folio_portal_directo = await p.getAttribute(`[${contrato.pantallas.resultado.folio_atributo}]`, contrato.pantallas.resultado.folio_atributo);
  await shot(p, "33_portal_resultado.png");

  res.errores_js = errores;
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch((e) => { console.error("FALLO", e); process.exit(1); });
