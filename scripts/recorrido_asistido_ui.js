// Piloto asistido desde la interfaz (Playwright/Chromium). Datos FICTICIOS; Cauce en modo piloto sin portal.
// Lo lanza scripts/piloto_asistido.py --recorrido. Uso: node recorrido_asistido_ui.js <motor> <capturas> <entrada.json>
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
  let ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
  let A = await ctx.newPage();
  const vigilar = (pg) => {
    pg.on("pageerror", (e) => res.errores_js.push(e.message));
    pg.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) res.errores_js.push(m.text()); });
    pg.on("response", (r) => { if (r.status() >= 400) res.http_errores.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  };
  vigilar(A);
  let n = E.fase === "anular" ? 20 : 0;      // la segunda fase no pisa la numeración de la primera
  const shot = async (nombre) => { n += 1; await A.screenshot({ path: path.join(outDir, `${String(n).padStart(2, "0")}_${nombre}.png`), fullPage: true }); };
  const paso = (k, ok, detalle) => { res.pasos.push({ paso: k, ok: !!ok, detalle }); if (!ok) throw new Error(`paso ${k} falló: ${JSON.stringify(detalle)}`); };
  const entrar = async () => {
    await A.goto(base + "/");
    await A.fill("input[name=username]", E.op[0]); await A.fill("input[name=password]", E.op[1]);
    await A.click("button[type=submit]");
    await A.waitForSelector("#nav a");
    await A.goto(base + "/#ventanilla");
    await A.click("button:has-text('Retomar')", { timeout: 20000 });   // la atención abierta se retoma donde quedó
  };
  const texto = async (sel) => ((await A.textContent(sel)) || "").trim();

  try {
    if (E.fase === "anular") {
      // 9. La persona canceló: el ADMINISTRADOR abre la ficha y anula la cita (folio tecleado, motivo, declaración)
      await A.goto(base + "/");
      await A.fill("input[name=username]", E.admin[0]); await A.fill("input[name=password]", E.admin[1]);
      await A.click("button[type=submit]");
      await A.waitForSelector("#nav a");
      await A.waitForSelector("#nota-origen-citas", { timeout: 20000 });
      const nota = await texto("#nota-origen-citas");
      paso("9a_mesa_de_control_no_dice_simulada_en_el_piloto", !/SIMULADA/i.test(nota) && /Declarada por persona/.test(nota), { nota });
      await A.evaluate((id) => showExpediente(id), E.eid);
      await A.waitForSelector("#asis-anular", { timeout: 20000 });
      await A.click("#asis-anular summary");
      await shot("admin_anular_cita");
      await A.focus("#anu-folio"); await A.keyboard.type(E.folio + "X");
      await A.fill("#anu-motivo", "la persona canceló (ficticio)");
      await A.check("#anu-declaro");
      await A.click("#btn-asistida-anular");
      await A.waitForFunction(() => /no coincide/.test(document.querySelector("#anu-error")?.textContent || ""), null, { timeout: 10000 });
      paso("9_anular_con_folio_distinto_se_rechaza", true, { error: await texto("#anu-error") });
      await A.fill("#anu-folio", "");
      await A.focus("#anu-folio"); await A.keyboard.type(E.folio);
      await A.click("#btn-asistida-anular");
      await A.waitForFunction(() => !document.querySelector("#asis-anular"), null, { timeout: 15000 });
      await shot("cita_anulada_expediente_cerrado");
      paso("10_cita_anulada_por_el_administrador", true, {});
      return;
    }
    // 1. El operador abre Ventanilla: la atención en curso muestra la opción del portal oficial (sin laboratorio)
    await entrar();
    await A.waitForSelector("#btn-asistida-abrir", { timeout: 20000 });
    const hayLab = await A.$("#btn-preparar");
    await shot("ventanilla_opcion_portal_oficial");
    paso("1_ventanilla_ofrece_agendar_a_mano_y_no_el_laboratorio", !hayLab, { boton_laboratorio: !!hayLab });

    // 2. Abrir la revisión: se ve quién, desde cuándo y la huella de los datos
    await A.click("#btn-asistida-abrir");
    await A.waitForSelector("#asis-frase");
    const huella = await A.getAttribute("#asis-huella", "data-huella");
    const tel = await texto("#asis-datos [data-dato=telefono]");
    await shot("revision_abierta");
    // el operador ve los datos COMPLETOS que debe teclear (no enmascarados)
    paso("2_revision_abierta_con_huella_y_datos_completos", /^[0-9a-f]{64}$/.test(huella || "") && /^555\d{7}$/.test(tel),
      { huella_ok: !!huella, telefono_completo: /^555\d{7}$/.test(tel) });

    // 3. Confirmar enseguida se rechaza (tiempo mínimo); la frase incorrecta también
    await A.fill("#asis-frase", "REVISÉ Y APRUEBO");
    await A.click("#btn-asistida-revisar");
    await A.waitForFunction(() => /al menos/.test(document.querySelector("#flujo .error")?.textContent || ""), null, { timeout: 10000 });
    const errRapido = await texto("#flujo .error");
    paso("3_confirmar_demasiado_rapido_se_rechaza", /al menos/.test(errRapido), { error: errRapido });

    // 4. «Cerrar el navegador»: se cierra la pestaña sin confirmar, se vuelve a entrar y la revisión sigue ABIERTA
    await ctx.close();
    ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
    A = await ctx.newPage();
    vigilar(A);
    await entrar();
    await A.waitForSelector("#asis-frase", { timeout: 20000 });
    await shot("tras_cerrar_el_navegador_sigue_abierta");
    paso("4_cerrar_el_navegador_no_aprueba_nada", !(await A.$("#form-asistida-registrar")), {});

    await A.waitForTimeout(5500);           // tiempo mínimo de revisión (5 s)
    await A.fill("#asis-frase", "revise y apruebo");
    await A.click("#btn-asistida-revisar");
    await A.waitForFunction(() => /exactamente/.test(document.querySelector("#flujo .error")?.textContent || ""), null, { timeout: 10000 });
    paso("5_frase_incorrecta_se_rechaza", true, { error: await texto("#flujo .error") });
    await A.fill("#asis-frase", "REVISÉ Y APRUEBO");
    await A.click("#btn-asistida-revisar");
    await A.waitForSelector("#form-asistida-registrar", { timeout: 15000 });
    await shot("revision_confirmada_formulario_de_folio");
    paso("6_revision_confirmada", true, {});

    // 7. Folios distintos: rechazado con el motivo a la vista
    await A.fill("#asis-folio", E.folio);
    await A.focus("#asis-folio_confirmacion"); await A.keyboard.type(E.folio + "X");
    await A.fill("#asis-fecha", E.fecha);
    await A.fill("#asis-hora", "10:30");
    await A.fill("#asis-comprobante_ref", "Carpeta PILOTO-LAB");
    await A.setInputFiles("#asis-archivo", { name: "acuse_ficticio.pdf", mimeType: "application/pdf", buffer: Buffer.from("%PDF-1.4 acuse ficticio") });
    await A.waitForFunction(() => /^[0-9a-f]{64}$/.test(document.querySelector("#asis-comprobante_sha256")?.value || ""), null, { timeout: 10000 });
    await A.check("#asis-declaro");
    await A.click("#btn-asistida-registrar");
    await A.waitForFunction(() => /no coinciden/.test(document.querySelector("#asis-error")?.textContent || ""), null, { timeout: 10000 });
    await shot("folios_distintos_rechazados");
    paso("7_folios_distintos_se_rechazan", true, { error: await texto("#asis-error") });

    // 8. Registrar bien: la ficha muestra la cita como declarada por una persona, con su comprobante
    await A.fill("#asis-folio_confirmacion", "");
    await A.focus("#asis-folio_confirmacion"); await A.keyboard.type(E.folio);
    await A.click("#btn-asistida-registrar");
    await A.waitForSelector("#asis-aviso", { timeout: 15000 });
    const folio = await A.getAttribute("#folio", "data-folio");
    const comp = await A.getAttribute("[data-comprobante]", "data-comprobante");
    await shot("cita_registrada_por_persona");
    paso("8_cita_registrada_con_folio_y_comprobante", folio === E.folio && comp === "CON_HUELLA", { folio_ok: folio === E.folio, comp });
  } catch (e) {
    res.error = String(e && e.message || e).slice(0, 400);
    try { await shot("error"); } catch (_) { /* nada */ }
    if (!res.pasos.length || res.pasos[res.pasos.length - 1].ok) res.pasos.push({ paso: "recorrido_completo", ok: false, detalle: res.error });
  } finally {
    await browser.close();
    console.log("RESULTADO " + JSON.stringify(res));
  }
})();
