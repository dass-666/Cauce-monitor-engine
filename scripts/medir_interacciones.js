// v1.7 · Mide cuántas interacciones necesita un operador para completar DOS reservaciones ficticias
// seguidas en el panel, con un navegador real. Mismo objetivo, dos recorridos:
//   modo "v1.6": Captura -> pestaña Expedientes -> fila -> Preparar -> ... -> folio -> cerrar -> fila ...
//   modo "v1.7": Captura (entra en «Mi trabajo») -> Continuar -> ... -> folio -> «Siguiente expediente» ...
// Las validaciones esenciales se hacen en los dos (casilla «He revisado», campos manuales tecleados,
// casilla «Confirmo»): no se mide un atajo que se salte la revisión humana.
// Uso: node scripts/medir_interacciones.js <motor> <operador> <clave> <modo> <código1> <código2> <capturas>
// Imprime "RESULTADO {json}".
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, user, pass, modo, c1, c2, outDir] = process.argv.slice(2);
fs.mkdirSync(outDir, { recursive: true });
const CLAVE = "KLM305918";

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1366, height: 900 } })).newPage();
  const errores = [];
  page.on("pageerror", (e) => errores.push(e.message));
  const n = { clics: 0, casillas: 0, campos_tecleados: 0, teclas: 0, esperas_ms: 0 };
  const pasos = [];
  const clic = async (sel, que) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.click(sel); n.clics++; pasos.push(que); };
  const casilla = async (sel, que) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.check(sel); n.casillas++; pasos.push(que); };
  const teclear = async (sel, txt, que) => {
    await page.waitForSelector(sel, { timeout: 30000 });
    await page.locator(sel).pressSequentially(txt, { delay: 5 }); n.campos_tecleados++; n.teclas += txt.length; pasos.push(que);
  };
  const t0 = Date.now();
  // inicio de sesión: igual en las dos versiones (no se cuenta como parte de la operación)
  await page.goto(base + "/");
  await page.fill("input[name=username]", user);
  await page.fill("input[name=password]", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector("#nav a");

  const formularioYConfirmar = async (k) => {
    await page.waitForSelector("#captura-manual", { timeout: 30000 });
    await teclear("#manual-clave_identidad", CLAVE, "teclear clave (campo manual)");
    await casilla("#manual-acepto_privacidad", "aceptar aviso de privacidad (campo manual)");
    await casilla("#chk-revisado", "marcar «He revisado los campos»");
    await clic("#btn-enviar", "enviar datos al portal");
    await page.waitForSelector("#flujo .mes button.dia", { timeout: 30000 });
    const fecha = await page.getAttribute("#flujo .mes button.dia >> nth=0", "data-fecha");
    await clic(`#flujo .mes button.dia[data-fecha="${fecha}"]`, "elegir día disponible");
    await casilla("#chk-confirmo", "marcar «Confirmo esta reservación»");
    await clic("#btn-confirmar", "confirmar reservación");
    await page.waitForSelector("#folio", { timeout: 30000 });
    await page.screenshot({ path: path.join(outDir, `${modo}_folio_${k}.png`), fullPage: true });
    return page.getAttribute("#folio", "data-folio");
  };

  const folios = [];
  await clic('#nav a[href="#captura"]', "abrir Captura");
  if (modo === "v1.6") {
    await clic("[data-tab=expedientes]", "pestaña Expedientes");
    for (const [k, cod] of [c1, c2].entries()) {
      if (k > 0) await clic("#drawer-close", "cerrar el expediente terminado");
      await clic(`#view table tbody tr:has-text("${cod}")`, "abrir el expediente en la lista");
      await clic("#btn-preparar", "preparar formulario");
      folios.push(await formularioYConfirmar(k + 1));
    }
  } else {
    await page.waitForSelector("#mi-trabajo [data-continuar]", { timeout: 30000 });
    await page.screenshot({ path: path.join(outDir, "v1.7_mi_trabajo.png"), fullPage: true });
    await clic("#mi-trabajo [data-continuar] >> nth=0", "«Continuar» con el primero de Mi trabajo (prepara el formulario)");
    folios.push(await formularioYConfirmar(1));
    await page.waitForSelector("#btn-siguiente", { timeout: 30000 });
    await page.screenshot({ path: path.join(outDir, "v1.7_siguiente.png"), fullPage: true });
    await clic("#btn-siguiente", "«Siguiente expediente» (prepara el formulario)");
    folios.push(await formularioYConfirmar(2));
  }
  const res = { modo, folios, ...n, interacciones: n.clics + n.casillas + n.campos_tecleados, pasos,
    duracion_s: Math.round((Date.now() - t0) / 100) / 10, errores_js: errores };
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch((e) => { console.error("FALLO", e); process.exit(1); });
