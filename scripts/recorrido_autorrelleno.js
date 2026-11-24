// Laboratorio · «Autorrellenar formulario» desde Ventanilla, SOLO con la interfaz (Playwright/Chromium).
// Datos FICTICIOS; portal de laboratorio propio en 127.0.0.1. Lo lanza scripts/recorrido_autorrelleno.py.
// El operador crea el expediente con su consentimiento (huella del documento calculada en el navegador),
// pulsa «Autorrellenar formulario» y espera el resultado de Forms. Forms abre SU PROPIA ventana (otro proceso):
// este script no la toca; solo lee lo que Monitor muestra. Después revoca el consentimiento desde la pantalla.
// Uso: node scripts/recorrido_autorrelleno.js <motor> <capturas> <entrada.json>
// Imprime "RESULTADO {json}" (solo códigos y estados; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const [base, outDir, entradaPath] = process.argv.slice(2);
const E = JSON.parse(fs.readFileSync(entradaPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch(process.env.RECORRIDO_CHROMIUM ? { executablePath: process.env.RECORRIDO_CHROMIUM } : {});
  const res = { pasos: [], errores_js: [], http_errores: [] };
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
  const O = await ctx.newPage();
  O.on("pageerror", (e) => res.errores_js.push(e.message));
  O.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) res.errores_js.push(m.text()); });
  O.on("response", (r) => { if (r.status() >= 400) res.http_errores.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  let n = 0;
  const shot = async (nombre) => { n += 1; await O.screenshot({ path: path.join(outDir, `${String(n).padStart(2, "0")}_${nombre}.png`), fullPage: true }); };
  const paso = (k, ok, detalle) => { res.pasos.push({ paso: k, ok: !!ok, detalle }); if (!ok) throw new Error(`paso ${k} falló: ${JSON.stringify(detalle)}`); };
  try {
    await O.goto(base + "/");
    await O.fill("input[name=username]", E.operador[0]); await O.fill("input[name=password]", E.operador[1]);
    await O.click("button[type=submit]");
    await O.waitForSelector("#nav a");
    await O.goto(base + "/#ventanilla"); await O.reload();
    await O.waitForSelector("#btn-nueva-atencion");
    paso("1_ventanilla", true, "Ventanilla del operador cargada");

    // 2. expediente ficticio con consentimiento registrado (huella SHA-256 del documento, calculada aquí)
    const doc = path.join(outDir, "..", "formato_consentimiento_ficticio.txt");
    fs.writeFileSync(doc, "Formato de consentimiento FICTICIO para el laboratorio de autorrellenado\n");
    const huella = crypto.createHash("sha256").update(fs.readFileSync(doc)).digest("hex");
    const alta = async (p, conConsentimiento) => {
      await O.goto(base + "/#ventanilla"); await O.reload();
      const volver = await O.$("#atencion button:has-text('Volver a la lista')");
      if (volver) await volver.click();
      await O.click("#btn-nueva-atencion");
      await O.waitForSelector("#form-persona");
      const f = (k, v) => O.fill(`#form-persona [name=${k}]`, v);
      await f("nombre", p.nombre); await f("fecha_nacimiento", p.fecha_nacimiento); await f("id_registro_previo", p.identificador);
      await f("telefono", p.telefono); await f("telefono_confirmacion", p.telefono);
      await f("correo", p.correo); await f("correo_confirmacion", p.correo);
      await O.selectOption("#form-persona [name=tipo_tramite]", p.tramite);
      await O.selectOption("#form-persona [name=region]", "NORTE");
      await f("sucursal", "NOR-1"); await f("sucursales_alternativas", "NOR-2");
      if (conConsentimiento) {
        await O.selectOption("#cons-medio", "PRESENCIAL_VENTANILLA");
        await O.fill("#cons-aviso", "AP-2026-01");
        await O.setInputFiles("#cons-archivo", doc);
        const t0 = Date.now();
        while (Date.now() - t0 < 5000 && (await O.inputValue("#cons-huella")) !== huella) await O.waitForTimeout(100);
        await O.fill("#cons-ref", "Carpeta LAB-AUTO hoja 1");
      }
      await O.click("#form-persona button.btn.primary");
    };
    await alta(E.persona, true);
    await O.waitForSelector("#atencion[data-estado=EN_PROCESO]");
    const atencion = await O.getAttribute("#atencion", "data-atencion");
    const consCodigo = await O.getAttribute("#consentimiento-vigente", "data-consentimiento");
    res.atencion = Number(atencion); res.consentimiento = consCodigo;
    paso("2_expediente_con_consentimiento", !!consCodigo, { consentimiento: consCodigo });

    // 3. el botón aparece y está disponible (lo decide el servidor con las reglas de Monitor)
    await O.waitForSelector("#autorrelleno[data-disponible=si] #btn-autorrellenar:not([disabled])", { timeout: 15000 });
    await shot("boton_autorrellenar_disponible");
    paso("3_boton_disponible", true, null);

    // 4. pulsar: Monitor lanza Forms; la tarjeta muestra que la ventana está abierta
    await O.click("#btn-autorrellenar");
    await O.waitForSelector("#autorrelleno-resultado[data-estado=LANZADA]", { timeout: 15000 });
    fs.writeFileSync(path.join(outDir, "..", "lanzada.marca"), String(Date.now()));
    await shot("forms_rellenando_ventana_abierta");
    paso("4_forms_lanzado", true, null);

    // 5. resultado (la persona no cierra la ventana en el recorrido: caduca la revisión y Forms la cierra)
    await O.waitForSelector("#autorrelleno-resultado:not([data-estado=LANZADA])", { timeout: (E.espera_s + 120) * 1000 });
    const estado = await O.getAttribute("#autorrelleno-resultado", "data-estado");
    const campos = await O.$$eval("#autorrelleno-resultado li[data-campo]", (xs) => xs.map((x) => ({ campo: x.dataset.campo, ok: !x.classList.contains("error") })));
    const confs = await O.$$eval("#autorrelleno-resultado li[data-confirmacion]", (xs) => xs.map((x) => ({ campo: x.dataset.confirmacion, ok: !x.classList.contains("error") })));
    const texto = await O.textContent("#autorrelleno");
    res.resultado = { estado, campos, confirmaciones: confs, texto_sin_null: !/\bnull\b|undefined/.test(texto) };
    await shot("resultado_forms_en_ventanilla");
    paso("5_resultado", ["CADUCADA", "PREPARADO"].includes(estado) && campos.length === 8 && campos.every((c) => c.ok)
      && confs.length === 2 && confs.every((c) => c.ok) && res.resultado.texto_sin_null, res.resultado);

    // 6. revocar desde la pantalla: la atención se cierra (y el servidor negará cualquier otro autorrellenado)
    await O.click("#consentimiento-vigente summary");
    await O.check("#rev-seguro");
    await O.click("#btn-revocar");
    await O.waitForSelector("#atencion-revocada", { timeout: 15000 });
    await shot("consentimiento_revocado_atencion_cerrada");
    paso("6_revocado", !(await O.$("#btn-autorrellenar")), "sin botón tras revocar");

    // 7. una persona SIN consentimiento: Ventanilla pide registrarlo y no ofrece el botón
    await alta(E.persona_sin, false);
    await O.waitForSelector("#paso-consentimiento", { timeout: 15000 });
    await shot("sin_consentimiento_no_hay_boton");
    paso("7_sin_consentimiento_sin_boton", !(await O.$("#btn-autorrellenar")), null);
    res.atencion_sin = Number(await O.getAttribute("#atencion", "data-atencion"));
  } catch (e) {
    res.fallo = String(e && e.message || e).slice(0, 400);
    try { await shot("fallo"); } catch (_) { /* nada */ }
  }
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})();
