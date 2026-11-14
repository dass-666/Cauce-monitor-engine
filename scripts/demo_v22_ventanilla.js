// v2.2 · Consentimiento en la pantalla de Ventanilla con navegador real (Playwright/Chromium).
// Datos FICTICIOS, solo 127.0.0.1. Lo lanza scripts/demo_v22.py.
// Uso: node scripts/demo_v22_ventanilla.js <motor> <usuario> <clave> <capturas> <personas.json>
//   personas.json: [{nombre, fecha_nacimiento, telefono, correo, registro, tramite}, ...] (2 personas ficticias)
// Recorrido:
//   A) persona nueva CON consentimiento en el mismo formulario (huella calculada en el navegador desde un
//      documento ficticio): la atención pasa directo a «Preparar».
//   B) persona nueva SIN consentimiento: la atención se detiene en «Consentimiento»; se registra ahí y continúa.
//   C) la persona B retira su consentimiento desde la atención: la atención termina sin cita.
// Imprime "RESULTADO {json}" (solo códigos; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const [base, user, pass, outDir, personasPath] = process.argv.slice(2);
const personas = JSON.parse(fs.readFileSync(personasPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
  const page = await ctx.newPage();
  const errores = [], http = [];
  page.on("pageerror", (e) => errores.push(e.message));
  page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) errores.push(m.text()); });
  page.on("response", (r) => { if (r.status() >= 400) http.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  const res = { pasos: [] };
  const shot = (name) => page.screenshot({ path: path.join(outDir, name), fullPage: true });
  const esperar = (sel, t = 30000) => page.waitForSelector(sel, { timeout: t });
  const siguiente = () => page.getAttribute("#siguiente-paso", "data-siguiente");
  const doc = path.join(outDir, "formato_ficticio.pdf");
  fs.writeFileSync(doc, "%PDF-1.4\n% formato de consentimiento FICTICIO para el laboratorio\n%%EOF\n");
  const huellaEsperada = crypto.createHash("sha256").update(fs.readFileSync(doc)).digest("hex");

  const nuevaPersona = async (p, conConsentimiento) => {
    await page.goto(base + "/#ventanilla");
    const volver = await page.$("#atencion button:has-text('Volver a la lista')");
    if (volver) await volver.click();                 // la atención anterior queda abierta (se retoma luego)
    await esperar("#btn-nueva-atencion");
    await page.click("#btn-nueva-atencion");
    await esperar("#form-persona");
    const f = (n, v) => page.fill(`#form-persona [name=${n}]`, v);
    await f("nombre", p.nombre);
    await f("fecha_nacimiento", p.fecha_nacimiento);
    await f("id_registro_previo", p.registro);
    await f("telefono", p.telefono); await f("telefono_confirmacion", p.telefono);
    await f("correo", p.correo); await f("correo_confirmacion", p.correo);
    await page.selectOption("#form-persona [name=tipo_tramite]", p.tramite);
    await page.selectOption("#form-persona [name=region]", "NORTE");
    await f("sucursal", "NOR-1");
    if (conConsentimiento) {
      await page.selectOption("#cons-medio", "FIRMA_PAPEL");
      await page.fill("#cons-aviso", "AP-2026-01");
      await page.setInputFiles("#cons-archivo", doc);
      await page.waitForFunction(() => (document.querySelector("#cons-huella").value || "").length === 64, null, { timeout: 10000 });
      res.huella_navegador = await page.inputValue("#cons-huella");
      await page.fill("#cons-ref", "Carpeta LAB-01");
      await shot("80_persona_con_consentimiento.png");
    }
    await page.click("#form-persona button.primary");
    await esperar("#siguiente-paso");
    await page.waitForFunction(() => document.querySelector("#siguiente-paso").dataset.siguiente !== "expediente", null, { timeout: 30000 });
    return page.getAttribute("#atencion", "data-atencion");
  };

  try {
    await page.goto(base + "/");
    await page.fill("input[name=username]", user);
    await page.fill("input[name=password]", pass);
    await page.click("button[type=submit]");
    await esperar("#nav a");

    // A) con consentimiento en el alta
    const aidA = await nuevaPersona(personas[0], true);
    res.pasos.push({ caso: "A", atencion: aidA, siguiente: await siguiente(),
      barra: await page.getAttribute("#consentimiento-vigente", "data-consentimiento").catch(() => null) });
    res.huella_coincide = res.huella_navegador === huellaEsperada;
    await shot("81_atencion_con_consentimiento.png");

    // B) sin consentimiento: se detiene en el paso «Consentimiento»
    const aidB = await nuevaPersona(personas[1], false);
    const sigB = await siguiente();
    const hayPaso = !!(await page.$("#paso-consentimiento"));
    const hayPreparar = !!(await page.$("#flujo"));
    await shot("82_paso_consentimiento.png");
    await page.selectOption("#cons-medio", "PRESENCIAL_VENTANILLA");
    await page.fill("#cons-aviso", "AP-2026-01");
    await page.fill("#cons-huella", crypto.createHash("sha256").update("formato ficticio B").digest("hex"));
    await page.dblclick("#btn-registrar-consentimiento");          // doble clic: un solo registro
    await page.waitForFunction(() => { const x = document.querySelector("#siguiente-paso"); return x && x.dataset.siguiente === "preparar"; },
      null, { timeout: 30000 });
    const codB = await page.getAttribute("#consentimiento-vigente", "data-consentimiento");
    res.pasos.push({ caso: "B", atencion: aidB, siguiente_antes: sigB, paso_consentimiento: hayPaso, flujo_antes: hayPreparar,
      siguiente_despues: await siguiente(), consentimiento: codB });
    await shot("83_consentimiento_registrado.png");

    // C) la persona B retira su consentimiento
    await page.click("#consentimiento-vigente summary");
    await page.selectOption("#rev-medio", "PRESENCIAL");
    await page.click("#btn-revocar");                               // sin la casilla: no hace nada
    const sinCasilla = await page.textContent("#consentimiento-vigente .error");
    await page.check("#rev-seguro");
    await page.click("#btn-revocar");
    await esperar("#resultado-atencion");
    res.pasos.push({ caso: "C", atencion: aidB, aviso_sin_casilla: !!(sinCasilla || "").trim(),
      resultado: await page.textContent("#resultado-atencion h2"),
      estado_atencion: await page.getAttribute("#atencion", "data-estado") });
    await shot("84_revocado_en_ventanilla.png");
  } catch (e) {
    res.error = String(e && e.message || e).slice(0, 300);
    try { await shot("89_error.png"); } catch (_) { /* nada */ }
  }
  res.errores_js = errores;
  res.http_errores = http;
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})();
