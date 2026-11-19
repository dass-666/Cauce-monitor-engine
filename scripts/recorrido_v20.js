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

let paginaActual = null;
(async () => {
  const browser = await chromium.launch();
  const errores = [];
  const http = [];
  const detalle = [];      // solo el mensaje del servidor (nunca datos de la persona)
  const res = { fase, atenciones: [] };
  const nueva = async (w, hgt) => {
    const ctx = await browser.newContext({ viewport: { width: w, height: hgt }, deviceScaleFactor: 1 });
    const p = await ctx.newPage();
    p.on("pageerror", (e) => errores.push(e.message));
    // el navegador anota como error de consola cada respuesta HTTP >= 400; esas se registran aparte
    // (con su ruta) para comprobar que solo aparecen las esperadas, p. ej. el 409 de un duplicado
    p.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errores.push(m.text()); });
    p.on("response", async (r) => {
      if (r.status() < 400) return;
      const linea = `${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`;
      http.push(linea);
      try { const d = (await r.json()).detail; detalle.push(`${linea} · ${(d && d.mensaje) || ""}`); } catch (_) { /* sin cuerpo */ }
    });
    return p;
  };
  const page = await nueva(fase === "movil" ? 390 : 1366, fase === "movil" ? 844 : 900);
  paginaActual = page;
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
    return buscarDia();
  };
  const buscarDia = async () => {
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
      if (i === 0) await page.route("**/api/**", async (r) => { await new Promise((ok) => setTimeout(ok, 400)); await r.continue(); });
      await clic(i === 0 ? "#btn-nueva-atencion" : "#btn-siguiente-persona");
      await page.waitForSelector("#form-persona");
      if (i === 0) {
        // v2.0 defecto intermitente (visto en PostgreSQL): la pantalla se pintaba DOS veces al empezar
        // y la segunda borraba lo ya tecleado. Con el servidor lento (respuestas retrasadas a propósito)
        // se comprueba que lo escrito sigue ahí un momento después.
        await page.fill("#form-persona [name=nombre]", "prueba de repintado");
        await page.waitForTimeout(2500);
        res.repintado_borra = (await page.inputValue("#form-persona [name=nombre]")) !== "prueba de repintado";
        await page.unroute("**/api/**");
      }
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
    if (!(await datosYFecha())) throw new Error("sin días disponibles para el expediente recuperado");
    res.folio = (await confirmarYCerrar()).folio;          // el expediente existente llega a su cita
  }

  if (fase === "medias") {
    await clic("#btn-nueva-atencion");
    await registrar(personas[0]);
    await datosYFecha();
    res.atencion = await page.getAttribute("#atencion", "data-atencion");
    await shot("56_ventanilla_a_medias.png");
  }

  if (fase === "retomar") {
    const sel = `#atenciones-abiertas [data-retomar="${personas[0].atencion}"]`;   // la que quedó a medias
    await page.waitForSelector(sel, { timeout: 30000 });
    await shot("57_ventanilla_retomar.png");
    await page.click(sel);
    await page.waitForSelector("#siguiente-paso");
    res.siguiente = await page.getAttribute("#siguiente-paso", "data-siguiente");
    await page.waitForSelector("#flujo .mes", { timeout: 30000 });
    if (!(await buscarDia())) throw new Error("sin días disponibles al retomar");
    const r = await confirmarYCerrar();
    res.folio = r.folio;
    await shot("58_ventanilla_folio_tras_reinicio.png");
  }

  if (fase === "conexiones") {                          // administrador: conexiones de red del motor
    await page.goto(base + "/#conexiones");
    await page.waitForSelector("#conexiones");
    const txt = await page.textContent("#conexiones");
    res.local = /Este equipo/.test(txt);
    res.usos = (txt.match(/Operación con el portal/g) || []).length;
    res.restricciones_24h = Number(await page.getAttribute("#conexiones-24h [data-restricciones]", "data-restricciones"));
    await page.click("#btn-medir");
    await page.waitForFunction(() => !/Midiendo/.test(document.getElementById("sondeo").textContent));
    res.sondeo = await page.textContent("#sondeo");
    await shot("64_conexiones.png");
  }

  if (fase === "manual") {                              // v2.0 · P2: trámites hechos a mano (captura manual)
    await page.goto(base + "/#tramites");
    await page.waitForSelector("#form-captura-manual");
    res.capturas = [];
    for (const x of personas) {                          // aquí cada elemento es {adaptador, folio, fecha}
      await page.selectOption("#form-captura-manual select[name=adaptador]", x.adaptador);
      await page.fill("#form-captura-manual input[name=folio]", x.folio);
      await page.fill("#form-captura-manual input[name=fecha]", x.fecha);
      if (x.marcar !== false) await page.check("#form-captura-manual input[name=folio_visto]");
      await page.click("#form-captura-manual button.btn.primary");
      await page.waitForFunction(() => document.getElementById("captura-resultado").textContent.trim() !== "");
      const est = await page.$eval("#captura-resultado", (el) => {
        const b = el.querySelector("[data-estado-captura]");
        return b ? b.getAttribute("data-estado-captura") : "ERROR: " + el.textContent.trim();
      });
      res.capturas.push(est);
      await page.waitForTimeout(900);                     // la pantalla se refresca sola tras registrar
      await page.waitForSelector("#form-captura-manual");
    }
    res.filas = await page.$$eval("#tramites-manuales [data-estado-captura]", (els) => els.map((e) => e.getAttribute("data-estado-captura")));
    await shot("65_tramites_a_mano.png");
  }

  res.errores_js = errores;
  res.http_errores = http;
  res.http_mensajes = detalle;
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})().catch(async (e) => {
  console.error("FALLO", e);
  if (paginaActual) {                                     // evidencia del fallo: captura y texto visible
    try {
      await paginaActual.screenshot({ path: path.join(outDir, `ZZ_fallo_${fase}.png`), fullPage: true });
      console.error("INVALIDOS", JSON.stringify(await paginaActual.evaluate(() =>
        [...document.querySelectorAll("form :invalid")].map((x) => `${x.name || x.id}: ${x.validationMessage}`))));
      console.error("PANTALLA", (await paginaActual.textContent("main")).replace(/\s+/g, " ").slice(0, 1500));
    } catch (_) { /* el navegador ya no responde */ }
  }
  process.exit(1);
});
