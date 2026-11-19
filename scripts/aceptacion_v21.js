// v2.1 · Recorrido de aceptación: la sección «Plan del día» de Ventanilla con navegador real
// (Playwright/Chromium), datos ficticios, solo 127.0.0.1.
// Uso: node scripts/aceptacion_v21.js <motor> <usuario> <clave> <capturas> <fase> [n]
//   fase = plan    : (administrador) crea el plan desde Ventanilla, amplía a 8 y lo completa; detalle de un elemento
//          operar  : (operador) prepara n coincidencias desde el plan y las lleva hasta el folio en la misma pantalla
//                    (campos manuales, día asignado, «Confirmo»: la CONFIRMACIÓN HUMANA la hace este script = SIMULADA)
//          sin_cita: (operador) prepara una coincidencia y cierra la atención SIN cita -> vuelve al plan
//          ver     : captura del plan (sin acciones)
//          movil   : el plan del día en un teléfono (sin desborde horizontal)
// Imprime "RESULTADO {json}". Nunca imprime datos de personas (solo códigos y folios).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, user, pass, outDir, fase, nArg] = process.argv.slice(2);
const N = Number(nArg || 1);
fs.mkdirSync(outDir, { recursive: true });
const CLAVE = "QXZ482915";

(async () => {
  const browser = await chromium.launch();
  const movil = fase === "movil";
  const ctx = await browser.newContext({ viewport: { width: movil ? 390 : 1366, height: movil ? 844 : 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errores = [], http = [];
  page.on("pageerror", (e) => errores.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errores.push(m.text()); });
  page.on("response", (r) => { if (r.status() >= 400) http.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  const res = { fase, operaciones: [] };
  const shot = (name) => page.screenshot({ path: path.join(outDir, name), fullPage: true });
  const esperar = (sel, t = 30000) => page.waitForSelector(sel, { timeout: t });
  const aLaLista = async () => {          // tras cerrar una atención, «Volver a la lista» muestra el plan otra vez
    const b = await page.$("#atencion button:has-text('Volver a la lista')");
    if (b) await b.click();
    await esperar("#plan-dia");
  };
  try {
    await page.goto(base + "/");
    await page.fill("input[name=username]", user);
    await page.fill("input[name=password]", pass);
    await page.click("button[type=submit]");
    await esperar("#nav a");
    await page.goto(base + "/#ventanilla");
    await esperar("#plan-dia");

    if (fase === "plan") {
      res.antes = await page.getAttribute("#plan-dia", "data-plan");
      await shot("70_plan_sin_crear.png");
      await page.click("#btn-crear-plan");
      await page.waitForFunction(() => { const x = document.querySelector("#plan-dia"); return x && x.dataset.plan !== "ninguno"; }, null, { timeout: 30000 });
      res.vivos_inicial = await page.textContent("#plan-vivos");
      res.codigos_inicial = await page.$$eval("[data-plan-codigo]", (xs) => xs.map((x) => x.dataset.planCodigo));
      await shot("71_plan_seis.png");
      await page.fill("#plan-capacidad", "8");
      await page.click("#btn-ampliar-plan");
      await page.waitForFunction(() => /de 8/.test(document.querySelector("#plan-vivos").textContent), null, { timeout: 30000 });
      await page.click("#btn-llenar-plan");
      await page.waitForFunction(() => /^8 de 8/.test(document.querySelector("#plan-vivos").textContent), null, { timeout: 30000 });
      res.vivos_final = await page.textContent("#plan-vivos");
      res.codigos_final = await page.$$eval("[data-plan-codigo]", (xs) => xs.map((x) => x.dataset.planCodigo));
      await shot("72_plan_ampliado_8.png");
      await page.click("#plan-dia table tbody tr >> nth=0");
      await esperar("#detalle-plan");
      res.detalle = { sucursales: await page.textContent("#detalle-sucursales"),
        fechas: await page.$$eval("#detalle-fechas .badge", (xs) => xs.length) };
      await shot("73_plan_detalle_elemento.png");
    }

    if (fase === "ver") {
      res.vivos = await page.textContent("#plan-vivos");
      res.coincidencias = Number(await page.textContent("#plan-coincidencias"));
      res.pendientes = Number(await page.textContent("#plan-pendientes"));
      res.estados = await page.$$eval("[data-estado-plan]", (xs) => xs.map((x) => x.dataset.estadoPlan));
      await shot(`${nArg || "75"}_plan_estado.png`);
    }

    if (fase === "operar" || fase === "sin_cita") {
      for (let i = 0; i < N; i++) {
        const t0 = Date.now();
        await aLaLista();
        await esperar("[data-preparar-plan]");
        if (i === 0) await shot(fase === "operar" ? "74_plan_coincidencias.png" : "79_plan_antes_sin_cita.png");
        const btn = await page.$("[data-preparar-plan]");
        const elemento = await btn.getAttribute("data-preparar-plan");
        const fecha = await btn.getAttribute("data-fecha-asignada");
        await btn.click();
        await esperar("#captura-manual");                 // la misma pantalla de atención de Ventanilla
        if (fase === "sin_cita") {
          await page.click("#cierre-sin-cita summary");
          await page.selectOption("#resultado-cierre", "SIN_DISPONIBILIDAD");
          await page.fill("#motivo-cierre", "persona no pudo");
          await page.click("#btn-cerrar-sin-cita");
          await esperar("#resultado-atencion.sin-cita");
          await shot("80_atencion_sin_cita.png");
          res.operaciones.push({ elemento, fecha, estado: await page.getAttribute("#atencion", "data-estado") });
          break;
        }
        await page.locator("#manual-clave_identidad").pressSequentially(CLAVE, { delay: 5 });
        await page.check("#manual-acepto_privacidad");
        await page.check("#chk-revisado");
        await page.click("#btn-enviar");
        await esperar("#flujo .mes");
        let dia = await page.$(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
        for (let k = 0; !dia && k < 2; k++) {
          await page.click("#flujo .filters button:nth-of-type(2)");
          await page.waitForTimeout(600);
          dia = await page.$(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
        }
        if (!dia) throw new Error(`el día asignado ${fecha} no aparece en el calendario del portal`);
        if (i === 0) await shot("76_operacion_calendario_dia_asignado.png");
        await dia.click();
        await page.check("#chk-confirmo");                // confirmación humana: aquí la SIMULA el script
        await page.click("#btn-confirmar");
        await esperar("#btn-cerrar-atencion");
        const folio = await page.getAttribute("#folio", "data-folio");
        if (i === 0) await shot("77_operacion_folio.png");
        await page.click("#btn-cerrar-atencion");
        await esperar("#folio-atencion");
        res.operaciones.push({ elemento, fecha, folio, folio_atencion: await page.getAttribute("#folio-atencion", "data-folio"),
          segundos: (Date.now() - t0) / 1000 });
      }
      await aLaLista();
      await shot(fase === "operar" ? `78_plan_tras_${user}.png` : "81_plan_tras_sin_cita.png");
    }

    if (fase === "movil") {
      res.desborde = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
      await shot("82_plan_movil.png");
    }
  } catch (e) {
    res.error = String(e && e.message || e).slice(0, 300);
    try { await shot(`error_${fase}_${user}.png`); } catch (_) { /* nada */ }
  }
  res.errores_js = errores;
  res.http_errores = http;
  console.log("RESULTADO " + JSON.stringify(res));
  await browser.close();
})();
