// v2.0 · Modo ventanilla con navegador real (Playwright/Chromium), datos ficticios, solo 127.0.0.1.
// Uso: node scripts/recorrido_v20.js <motor> <operador> <clave> <capturas> <fase> <json-personas>
//   fase = seis      : 6 atenciones seguidas (persona nueva -> datos -> día -> confirmar -> cerrar)
//          sin_cita  : una atención que termina sin disponibilidad
//          duplicado : registrar a alguien que ya tiene expediente -> se recupera el existente
//          medias    : deja una atención a medias (datos enviados) y cierra el navegador
//          retomar   : tras reiniciar el motor, retoma la atención a medias hasta el folio
//          movil     : la pantalla de ventanilla en un teléfono (sin desborde)
// Cuenta clics, casillas y campos tecleados por atención. Imprime "RESULTADO {json}".
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [base, user, pass, outDir, fase, personasJson] = process.argv.slice(2);
const personas = JSON.parse(fs.readFileSync(personasJson, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });
const CLAVE = "KLM305918";

(async () => {
  const browser = await chromium.launch();
  const errores = [];
  const res = { fase, atenciones: [] };
  const nueva = async (w, hgt) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: hgt }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errores.push(e.message));
    p.on("console", (m) => { if (m.type() === "error") errores.push(m.text()); });
    return p;
  };
  const page = await nueva(fase === "movil" ? 390 : 1366, fase === "movil" ? 844 : 900);
  let n = { clics: 0, casillas: 0, campos: 0 };
  const clic = async (sel) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.click(sel); n.clics++; };
  const casilla = async (sel) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.check(sel); n.casillas++; };
  const teclear = async (sel, v) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.fill(sel, v); n.campos++; };
  const elegir = async (sel, v) => { await page.waitForSelector(sel, { timeout: 30000 }); await page.selectOption(sel, v); n.campos++; };
  const shot = (name) => page.screenshot({ path: path.join(outDir, name), fullPage: true });

  await page.goto(base + "/");
  await page.fill("input[name=username]", user);
  await page.fill("input[name=password]", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector("#nav a");
  await page.goto(base + "/#ventanilla");
  await page.waitForSelector("#ventanilla");

  const registrar = async (p) => {
    await teclear("#form-persona [name=nombre]", p.nombre);
    await teclear("#form-persona [name=fecha_nacimiento]", p.fecha_nacimiento);
    await teclear("#form-persona [name=id_registro_previo]", p.id_registro_previo);
    await teclear("#form-persona [name=telefono]", p.telefono);
    await teclear("#form-persona [name=telefono_confirmacion]", p.telefono);
    await teclear("#form-persona [name=correo]", p.correo);
    await teclear("#form-persona [name=correo_confirmacion]", p.correo);
    await elegir("#form-persona [name=tipo_tramite]", p.tipo_tramite);
    await elegir("#form-persona [name=region]", p.region);
    await teclear("#form-persona [name=sucursal]", p.sucursal);
    await clic("#form-persona button.primary");
  };
  const datosYFecha = async () => {
    await page.waitForSelector("#captura-manual", { timeout: 30000 });       // la ventanilla ya preparó el formulario
    await page.locator("#manual-clave_identidad").pressSequentially(CLAVE, { delay: 5 }); n.campos++;
    await casilla("#manual-acepto_privacidad");
    await casilla("#chk-revisado");
    await clic("#btn-enviar");
    await page.waitForSelector("#flujo .mes", { timeout: 30000 });
    let libre = await page.$("#flujo .mes button.dia");
    for (let k = 0; !libre && k < 2; k++) {                // sin días este mes: el operador pasa al siguiente
      await clic("#flujo .filters button:nth-of-type(2)");
      await page.waitForTimeout(600);
      libre = await page.$("#flujo .mes button.dia");
    }
    return libre;
  };
  const confirmarYCerrar = async () => {
    const fecha = await page.getAttribute("#flujo .mes button.dia >> nth=0", "data-fecha");
    await clic(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
    await casilla("#chk-confirmo");
    await clic("#btn-confirmar");
    await page.waitForSelector("#btn-cerrar-atencion", { timeout: 30000 });
    const folioPanel = await page.getAttribute("#folio", "data-folio");
    await clic("#btn-cerrar-atencion");
    await page.waitForSelector("#folio-atencion", { timeout: 30000 });
    return { folio: await page.getAttribute("#folio-atencion", "data-folio"), folio_flujo: folioPanel };
  };

  if (fase === "seis" || fase === "movil") {
    const total = fase === "seis" ? 6 : 1;
    for (let i = 0; i < total; i++) {
      n = { clics: 0, casillas: 0, campos: 0 };
      const t0 = Date.now();
      await clic(i === 0 ? "#btn-nueva-atencion" : "#btn-siguiente-persona");
      await page.waitForSelector("#form-persona");
      if (i === 0) { await shot(fase === "movil" ? "60_movil_persona.png" : "50_ventanilla_persona.png"); }
      await registrar(personas[i]);
      if (!(await datosYFecha())) throw new Error("sin días disponibles en el portal de prueba");
      if (i === 0) await shot(fase === "movil" ? "61_movil_calendario.png" : "51_ventanilla_calendario.png");
      const r = await confirmarYCerrar();
      if (i === 0 || i === total - 1) await shot(fase === "movil" ? "62_movil_folio.png" : `52_ventanilla_folio_${i + 1}.png`);
      res.atenciones.push({ ...r, ...n, interacciones: n.clics + n.casillas + n.campos, segundos: (Date.now() - t0) / 1000,
        estado: await page.getAttribute("#atencion", "data-estado") });
    }
    if (fase === "movil") res.desborde = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1);
    res.meta_texto = await page.textContent("#meta-dia");
    await page.goto(base + "/#ventanilla"); await page.waitForSelector("#meta-dia");
    res.citas_hoy = Number(await page.textContent("#citas-hoy"));
    await shot(fase === "movil" ? "63_movil_meta.png" : "53_ventanilla_meta_cumplida.png");
  }

  if (fase === "sin_cita") {
    await clic("#btn-nueva-atencion");
    await registrar(personas[0]);
    await page.waitForSelector("#captura-manual");
    await page.click("#cierre-sin-cita summary");
    await page.selectOption("#resultado-cierre", "SIN_DISPONIBILIDAD");
    await page.fill("#motivo-cierre", "sin dias");
    await page.click("#btn-cerrar-sin-cita");
    await page.waitForSelector("#resultado-atencion.sin-cita");
    res.estado = await page.getAttribute("#atencion", "data-estado");
    res.folio_visible = !!(await page.$("#folio-atencion"));
    await shot("54_ventanilla_sin_cita.png");
  }

  if (fase === "duplicado") {
    await clic("#btn-nueva-atencion");
    await registrar(personas[0]);                       // esta persona ya tiene expediente
    await page.waitForSelector("#aviso-duplicado [data-atender]", { timeout: 30000 });
    res.aviso = await page.textContent("#aviso-duplicado");
    await shot("55_ventanilla_duplicado.png");
    await page.click("#aviso-duplicado [data-atender]");
    await page.waitForSelector("#captura-manual");
    res.recuperado = await page.textContent("#atencion");
  }

  if (fase === "medias") {
    await clic("#btn-nueva-atencion");
    await registrar(personas[0]);
    await datosYFecha();
    res.atencion = await page.getAttribute("#atencion", "data-atencion");
    await shot("56_ventanilla_a_medias.png");
  }

  if (fase === "retomar") {
    await page.waitForSelector("#atenciones-abiertas [data-retomar]", { timeout: 30000 });
    await shot("57_ventanilla_retomar.png");
    await page.click("#atenciones-abiertas [data-retomar]");
    await page.waitForSelector("#siguiente-paso");
    res.siguiente = await page.getAttribute("#siguiente-paso", "data-siguiente");
    await page.waitForSelector("#flujo .mes button.dia", { timeout: 30000 });
    const r = await confirmarYCerrar();
    res.folio = r.folio;
    await shot("58_ventanilla_folio_tras_reinicio.png");
  }

  res.errores_js = errores;
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch((e) => { console.error("FALLO", e); process.exit(1); });
