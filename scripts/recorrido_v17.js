// v1.7 · Panel con navegador real (Playwright/Chromium): centro de operaciones, supervisión,
// notificaciones, explicación de la asignación, Mi trabajo, errores, conexión perdida y retomar
// una operación interrumpida por un reinicio del motor.
// Uso: node scripts/recorrido_v17.js <motor> <admin> <clave_admin> <operador> <clave_op> <capturas> <fase>
//   fase = vistas | interrumpir | retomar | sin_conexion
// Imprime "RESULTADO {json}". Datos ficticios; solo 127.0.0.1.
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, adUser, adPass, opUser, opPass, outDir, fase] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

(async () => {
  const browser = await chromium.launch();
  const errores = [];
  const res = { fase };
  const nuevo = async (w, hgt) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: hgt }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errores.push(e.message));
    p.on("console", (m) => { if (m.type() === "error") errores.push(m.text()); });
    return p;
  };
  const shot = async (page, name) => { await page.screenshot({ path: path.join(outDir, name), fullPage: true }); log("captura", name); };
  const desborda = (page) => page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
  const login = async (page, u, p) => {
    await page.goto(base + "/");
    await page.fill("input[name=username]", u);
    await page.fill("input[name=password]", p);
    await page.click("button[type=submit]");
    await page.waitForSelector("#nav a");
  };
  const ir = async (page, vista, sel) => { await page.goto(base + "/#" + vista); await page.waitForSelector(sel, { timeout: 30000 }); await page.waitForTimeout(400); };

  if (fase === "vistas") {
    const adm = await nuevo(1366, 900);
    await login(adm, adUser, adPass);
    await ir(adm, "inicio", "#centro .kpi");
    res.kpis_centro = await adm.$$eval("#centro .kpi .label", (l) => l.map((x) => x.textContent));
    await shot(adm, "01_centro_escritorio.png");
    await adm.selectOption("#centro [data-filtro=region]", "NORTE");
    await adm.waitForFunction(() => { const s = document.querySelector("#centro [data-filtro=region]"); return s && s.value === "NORTE"; });
    await adm.waitForTimeout(800);
    res.filtro_region_aplicado = await adm.$eval("#centro [data-filtro=region]", (s) => s.value);
    await shot(adm, "02_centro_filtrado_region.png");
    await ir(adm, "supervision", "[data-presencia]");
    res.presencias = await adm.$$eval("[data-presencia]", (l) => l.map((x) => x.getAttribute("data-presencia")));
    await shot(adm, "03_supervision.png");
    const red = await adm.$("[data-redistribuir]");
    if (red) {
      await red.click();
      await adm.waitForSelector("#btn-redistribuir");
      // error visible: enviar sin motivo/confirmación lo bloquea el propio formulario (required)
      await adm.click("#btn-redistribuir");
      res.redistribuir_bloqueado_sin_confirmar = await adm.$eval("input[name=confirmo]", (i) => !i.checkValidity());
      await shot(adm, "04_redistribuir_formulario.png");
      await adm.click("#drawer-close");
    }
    await ir(adm, "alertas", "[data-categoria]");
    res.categorias = await adm.$$eval("[data-categoria]", (l) => l.length);
    await shot(adm, "05_notificaciones.png");
    await adm.click("[data-categoria=EXPEDIENTE_ASIGNADO]");
    await adm.waitForSelector("[data-categoria=EXPEDIENTE_ASIGNADO][aria-pressed=true]");
    await adm.waitForTimeout(500);
    res.filtradas = await adm.$$eval("#lista-notificaciones [data-alerta]", (l) => l.length);
    await shot(adm, "06_notificaciones_filtradas.png");
    await ir(adm, "solicitudes", "[data-explicacion]");
    await adm.click("[data-explicacion] >> nth=0");
    await adm.waitForSelector("#drawer-body h1");
    res.explicacion = await adm.$eval("#drawer-body", (d) => d.textContent.slice(0, 300));
    await shot(adm, "07_explicacion_asignacion.png");
    await adm.click("#drawer-close");
    // formulario largo con errores (alta de expediente sin datos obligatorios)
    await ir(adm, "captura", "[data-tab=expedientes]");
    await adm.click("[data-tab=expedientes]");
    await adm.waitForSelector("[data-tab=expedientes][aria-selected=true]");
    await adm.waitForTimeout(400);
    await shot(adm, "08_expedientes_formulario_largo.png");
    // pantalla pequeña (portátil) y móvil
    const peq = await nuevo(1024, 640);
    await login(peq, adUser, adPass);
    await ir(peq, "inicio", "#centro .kpi");
    res.desborde_pequena_inicio = await desborda(peq);
    await shot(peq, "10_pequena_centro.png");
    await ir(peq, "supervision", "[data-presencia]");
    res.desborde_pequena_supervision = await desborda(peq);
    await shot(peq, "11_pequena_supervision.png");
    for (const [v, sel, n] of [["inicio", "#centro .kpi", "20_movil_centro.png"], ["alertas", "[data-categoria]", "21_movil_notificaciones.png"],
      ["supervision", "[data-presencia]", "22_movil_supervision.png"]]) {
      const m = await nuevo(390, 844);
      await login(m, adUser, adPass);
      await ir(m, v, sel);
      res["desborde_" + n.slice(3, -4)] = await desborda(m);
      await shot(m, n);
    }
    // operador: Mi trabajo y sin acceso a supervisión
    const op = await nuevo(390, 844);
    await login(op, opUser, opPass);
    await ir(op, "captura", "#mi-trabajo, .empty");
    res.mi_trabajo_items = await op.$$eval("[data-trabajo]", (l) => l.length);
    res.desborde_movil_mi_trabajo = await desborda(op);
    await shot(op, "23_movil_mi_trabajo_operador.png");
    await op.goto(base + "/#supervision");
    await op.waitForTimeout(800);
    res.operador_supervision_redirige = await op.evaluate(() => location.hash) !== "#supervision"
      || !(await op.$("[data-presencia]"));
    res.nav_operador = await op.$$eval("#nav a", (l) => l.map((x) => x.textContent));
  }

  if (fase === "interrumpir") {
    const op = await nuevo(1366, 900);
    await login(op, opUser, opPass);
    await ir(op, "captura", "#mi-trabajo [data-continuar]");
    await op.click("#mi-trabajo [data-continuar] >> nth=0");
    await op.waitForSelector("#captura-manual");
    await op.locator("#manual-clave_identidad").pressSequentially("KLM305918", { delay: 10 });
    await op.check("#manual-acepto_privacidad");
    await op.check("#chk-revisado");
    await op.click("#btn-enviar");
    await op.waitForSelector("#flujo .mes button.dia", { timeout: 30000 });
    res.codigo = await op.$eval("#drawer-body h1", (x) => x.textContent);
    await shot(op, "30_operacion_a_medias.png");
    // el navegador se cierra aquí sin terminar (y luego el motor se cae)
  }

  if (fase === "retomar") {
    const op = await nuevo(1366, 900);
    await login(op, opUser, opPass);
    await ir(op, "captura", "#mi-trabajo");
    const it = op.locator("[data-trabajo].interrumpido").first();
    await it.waitFor({ timeout: 30000 });
    res.paso_tras_reinicio = await it.locator("[data-paso]").getAttribute("data-paso");
    res.boton = await it.locator("[data-continuar]").textContent();
    await shot(op, "31_mi_trabajo_tras_reinicio.png");
    await it.locator("[data-continuar]").click();
    await op.waitForSelector("#flujo .mes button.dia", { timeout: 30000 });
    const fecha = await op.getAttribute("#flujo .mes button.dia >> nth=0", "data-fecha");
    await op.click(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
    await op.check("#chk-confirmo");
    await op.click("#btn-confirmar");
    await op.waitForSelector("#folio", { timeout: 30000 });
    res.folio = await op.getAttribute("#folio", "data-folio");
    await shot(op, "32_folio_tras_retomar.png");
  }

  if (fase === "sin_conexion") {
    const adm = await nuevo(1366, 900);
    await login(adm, adUser, adPass);
    await ir(adm, "inicio", "#centro .kpi");
    await adm.route("**/api/**", (r) => r.abort("internetdisconnected"));   // la red del navegador se corta
    await adm.waitForSelector("#offline-banner:not(.hidden)", { timeout: 20000 });
    res.aviso = await adm.textContent("#offline-banner");
    res.datos_siguen_visibles = (await adm.$$("#centro .kpi")).length > 0;
    await shot(adm, "40_conexion_perdida.png");
    await adm.unroute("**/api/**");
    await adm.waitForSelector("#offline-banner.hidden", { state: "attached", timeout: 20000 });
    res.aviso_desaparece_al_volver = true;
    await shot(adm, "41_conexion_recuperada.png");
  }

  // Abortar la red a propósito genera errores de recurso en la consola: se cuentan aparte.
  res.errores_js = errores.filter((e) => !(fase === "sin_conexion" && /Failed to load resource|INTERNET_DISCONNECTED|Failed to fetch/.test(e)));
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch((e) => { console.error("FALLO", e); process.exit(1); });
