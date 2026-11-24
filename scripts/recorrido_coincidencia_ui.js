// Laboratorio · la tarjeta «Autorrellenar» de Ventanilla con una COINCIDENCIA del plan, SOLO con la interfaz.
// Lo lanza scripts/recorrido_coincidencia_forms.py --interfaz en dos fases (datos FICTICIOS, 127.0.0.1):
//   autorrellenar: el operador retoma la atención, ve la coincidencia («todavía no es una cita»), pulsa
//                  «Autorrellenar formulario» y espera a que la tarjeta diga LISTA PARA CONFIRMACIÓN SUPERVISADA.
//   folio:         (la persona ya confirmó en el portal) el operador teclea un folio inexistente -> error visible y
//                  nada cambia; después el folio correcto dos veces -> la tarjeta muestra la cita CONFIRMADA.
// Uso: node scripts/recorrido_coincidencia_ui.js <fase> <motor> <capturas> <entrada.json>
// Imprime "RESULTADO {json}" (solo estados y códigos; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const [fase, base, outDir, entradaPath] = process.argv.slice(2);
const E = JSON.parse(fs.readFileSync(entradaPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });

(async () => {
  const browser = await chromium.launch(process.env.RECORRIDO_CHROMIUM ? { executablePath: process.env.RECORRIDO_CHROMIUM } : {});
  const res = { fase, pasos: [], errores_js: [], http_errores: [] };
  const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
  const O = await ctx.newPage();
  O.on("pageerror", (e) => res.errores_js.push(e.message));
  O.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) res.errores_js.push(m.text()); });
  O.on("response", (r) => { if (r.status() >= 400) res.http_errores.push(`${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
  const shot = async (nombre) => { await O.screenshot({ path: path.join(outDir, `${fase}_${nombre}.png`), fullPage: true }); };
  const paso = (k, ok, detalle) => { res.pasos.push({ paso: k, ok: !!ok, detalle }); if (!ok) throw new Error(`paso ${k} falló: ${JSON.stringify(detalle)}`); };
  const sinNull = async (sel) => !/\bnull\b|undefined|NaN/.test(await O.textContent(sel));
  try {
    await O.goto(base + "/");
    await O.fill("input[name=username]", E.operador[0]); await O.fill("input[name=password]", E.operador[1]);
    await O.click("button[type=submit]");
    await O.waitForSelector("#nav a");
    await O.goto(base + "/#ventanilla"); await O.reload();
    const retomar = `.trabajo-item[data-atencion="${E.atencion}"] button:has-text('Retomar')`;
    await O.waitForSelector(retomar, { timeout: 15000 });
    await O.click(retomar);
    await O.waitForSelector(`#atencion[data-atencion="${E.atencion}"]`, { timeout: 15000 });
    await O.waitForSelector("#autorrelleno-coincidencia", { timeout: 15000 });
    const txt = await O.textContent("#autorrelleno-coincidencia");
    res.coincidencia_visible = { fecha: txt.includes(E.fecha), sucursal: txt.includes(E.sucursal) };

    if (fase === "autorrellenar") {
      paso("1_coincidencia_visible_no_es_cita",
        res.coincidencia_visible.fecha && res.coincidencia_visible.sucursal
        && (await O.getAttribute("#autorrelleno-coincidencia", "data-lista")) === "no"
        && (await O.getAttribute("#autorrelleno-coincidencia", "data-confirmada")) === "no" && !(await O.$("#folio-forms")),
        res.coincidencia_visible);
      await O.waitForSelector("#autorrelleno[data-disponible=si] #btn-autorrellenar:not([disabled])", { timeout: 15000 });
      await shot("1_coincidencia_antes_de_forms");
      await O.click("#btn-autorrellenar");
      await O.waitForSelector("#autorrelleno-resultado[data-estado=LANZADA]", { timeout: 15000 });
      paso("2_forms_lanzado_desde_la_tarjeta", true, null);
      // la tarjeta se refresca sola; al terminar Forms, la operación queda lista (no confirmada)
      await O.waitForSelector("#autorrelleno-coincidencia[data-lista=si]", { timeout: (E.espera_s + 180) * 1000 });
      const estado = await O.getAttribute("#autorrelleno-resultado", "data-estado");
      const campos = await O.$$eval("#autorrelleno-resultado li[data-campo]", (xs) => xs.map((x) => !x.classList.contains("error")));
      res.resultado = { estado, campos_ok: campos.filter(Boolean).length, campos: campos.length,
        texto_lista: (await O.textContent("#autorrelleno-coincidencia")).includes("todavía NO es una cita"),
        caja_folio: !!(await O.$("#folio-forms-caja")), sin_null: await sinNull("#autorrelleno") };
      await shot("2_lista_para_confirmacion");
      paso("3_lista_para_confirmacion_en_pantalla", ["CADUCADA", "PREPARADO"].includes(estado) && res.resultado.campos_ok === 8
        && res.resultado.texto_lista && res.resultado.caja_folio && res.resultado.sin_null
        && (await O.getAttribute("#autorrelleno-coincidencia", "data-confirmada")) === "no", res.resultado);
    } else if (fase === "folio") {
      await O.waitForSelector("#folio-forms-caja", { timeout: 15000 });
      await O.fill("#folio-forms", E.folio_falso); await O.fill("#folio-forms-2", E.folio_falso);
      await O.click("#btn-folio-forms");
      await O.waitForFunction(() => (document.querySelector("#folio-forms-error") || {}).textContent, null, { timeout: 15000 });
      res.error_folio_falso = (await O.textContent("#folio-forms-error")).length > 0;
      await shot("1_folio_inexistente_rechazado");
      paso("1_folio_inexistente_muestra_error_y_no_confirma", res.error_folio_falso
        && (await O.getAttribute("#autorrelleno-coincidencia", "data-confirmada")) === "no"
        && !(await O.$eval("#btn-folio-forms", (b) => b.disabled)), { error: res.error_folio_falso });
      await O.fill("#folio-forms", E.folio); await O.fill("#folio-forms-2", E.folio);
      await O.click("#btn-folio-forms");
      // tras confirmar, la pantalla se refresca: o la tarjeta queda CONFIRMADA o la atención pasa a su paso de cierre
      // con la sección «Reservación confirmada» y el folio; se acepta cualquiera, pero el folio debe verse
      await O.waitForFunction((f) => {
        const k = document.querySelector("#autorrelleno-coincidencia[data-confirmada=si]");
        const t = document.body.textContent;
        return !document.querySelector("#folio-forms-caja") && ((k && k.textContent.includes(f))
          || (t.includes("Reservación confirmada") && t.includes(f)));
      }, E.folio, { timeout: 20000 });
      const t = await O.textContent("body");
      res.confirmada = { tarjeta: !!(await O.$("#autorrelleno-coincidencia[data-confirmada=si]")),
        seccion_confirmada: t.includes("Reservación confirmada"), folio_visible: t.includes(E.folio),
        verificada_con_portal: t.includes("PORTAL_API_FOLIO"), caja_folio_retirada: !(await O.$("#folio-forms-caja")) };
      await shot("2_cita_confirmada");
      paso("2_folio_correcto_confirma_en_pantalla", res.confirmada.folio_visible && res.confirmada.caja_folio_retirada
        && (res.confirmada.tarjeta || (res.confirmada.seccion_confirmada && res.confirmada.verificada_con_portal)), res.confirmada);
    } else {
      throw new Error("fase desconocida: " + fase);
    }
    paso("sin_errores_js", res.errores_js.length === 0, res.errores_js);
  } catch (e) {
    res.fallo = String(e && e.message || e).slice(0, 400);
    try { await shot("fallo"); } catch (_) { /* nada */ }
  }
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
})();
