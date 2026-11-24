// Laboratorio · «Páginas observadas» SOLO con la interfaz (Chromium automatizado). Lo lanza
// scripts/recorrido_fuentes_web_ui.py, que pone el sitio de turnos local (laboratorio_web) y cambia su contenido
// entre fases. Datos ficticios; todo en 127.0.0.1.
//   configurar: el administrador escribe el perfil en el formulario (con dos errores a propósito), prueba, ve qué no
//               se reconoce y por qué, corrige, vuelve a probar y guarda. «Guardar» solo se habilita con una prueba
//               vigente de esos mismos datos.
//   detener:    detiene la observación desde la lista.
//   captcha:    con el sitio pidiendo un CAPTCHA, la vista previa lo dice y no deja guardar.
// Uso: node scripts/recorrido_fuentes_web_ui.js <fase> <motor> <capturas> <entrada.json>
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
  const F = (n) => `#form-fuente-web [name=${n}]`;
  const guardarHabilitado = async () => !(await O.$eval("#btn-fw-guardar", (b) => b.disabled));
  const probar = async () => {
    await O.click("#btn-fw-probar");
    await O.waitForSelector("#fw-resultado", { timeout: 60000 });
    return {
      ok: await O.getAttribute("#fw-resultado", "data-ok"),
      dias: Number(await O.getAttribute("#fw-resultado", "data-dias")),
      no: Number(await O.getAttribute("#fw-resultado", "data-no-reconocidos")),
      muestras: await O.$$eval("#fw-no-reconocidos li", (xs) => xs.map((x) => ({ motivo: x.dataset.motivo, texto: x.textContent }))),
    };
  };
  try {
    await O.goto(base + "/");
    await O.fill("input[name=username]", "admin"); await O.fill("input[name=password]", E.admin);
    await O.click("button[type=submit]");
    await O.waitForSelector("#nav a");
    // se llega por el menú, como lo haría una persona
    await O.click("details[data-grupo=operacion] summary");
    await O.click("a[href='#fuentes-web']");
    await O.waitForSelector("#fuentes-web", { timeout: 15000 });

    if (fase === "configurar") {
      paso("1_pantalla_en_el_menu", Number(await O.getAttribute("#fuentes-web", "data-fuentes")) === 0 && !(await guardarHabilitado()), null);
      const P = E.perfil;
      await O.fill(F("nombre"), E.nombre); await O.fill(F("url"), E.url);
      await O.selectOption(F("tramite"), E.tramite);
      await O.fill(F("zona"), P.zona); await O.fill(F("elemento"), P.elemento);
      await O.fill(F("fecha_selector"), P.fecha.selector); await O.fill(F("fecha_patron"), P.fecha.patron);
      await O.selectOption(F("fecha_formato"), P.fecha.formato);
      await O.fill(F("estado_selector"), P.estado.selector);
      await O.fill(F("disponible"), "Disponible");                        // error 1: falta «Sin lugares»
      await O.fill(F("libres_selector"), P.libres.selector); await O.fill(F("libres_patron"), P.libres.patron);
      await O.fill(F("hora_selector"), P.hora.selector); await O.fill(F("hora_patron"), P.hora.patron);
      await O.fill(F("ubicacion_selector"), P.ubicacion.selector);
      await O.fill(F("ubicaciones"), "Oficina Norte Centro = NOR-1");      // error 2: falta la oficina Poniente
      await O.fill(F("ignorar"), P.ignorar.join("\n"));
      await O.fill(F("estabilidad_ms"), String(P.estabilidad_ms));
      await O.fill(F("sesion_perdida"), P.sesion_perdida);
      paso("2_sin_prueba_no_se_puede_guardar", !(await guardarHabilitado()), null);

      const r1 = await probar();
      await shot("1_prueba_con_errores_de_perfil");
      const motivos = r1.muestras.map((m) => m.motivo).sort();
      paso("3_vista_previa_dice_que_no_reconoce_y_por_que", r1.ok === "si" && r1.dias === 1 && r1.no === 2
        && JSON.stringify(motivos) === JSON.stringify(["estado", "ubicacion"])
        && r1.muestras.some((m) => m.texto.includes("Sin lugares")) && r1.muestras.some((m) => m.texto.includes("Oficina Norte Poniente")), r1);

      // corregir: la prueba anterior deja de valer y «Guardar» se apaga
      await O.fill(F("no_disponible"), "Sin lugares\nAgotado");
      await O.fill(F("ubicaciones"), "Oficina Norte Centro = NOR-1\nOficina Norte Poniente = NOR-2");
      await O.waitForSelector("#fw-cambio", { timeout: 5000 });
      paso("4_cambiar_el_perfil_invalida_la_prueba", !(await guardarHabilitado()), null);

      const r2 = await probar();
      const dias = await O.$$eval("#fw-resultado tbody tr", (xs) => xs.map((x) => [...x.children].map((td) => td.textContent.trim())));
      await shot("2_prueba_correcta");
      paso("5_vista_previa_correcta", r2.ok === "si" && r2.dias === 3 && r2.no === 0
        && dias.some((d) => d[0] === "NOR-2" && d[2] === "Disponible") && dias.some((d) => d[2] === "Sin lugares"), { r2, dias });
      paso("6_con_prueba_vigente_se_puede_guardar", await guardarHabilitado(), null);

      await O.click("#btn-fw-guardar");
      await O.waitForSelector(`#fuentes-web [data-fuente="${E.nombre}"]`, { timeout: 30000 });
      let mirando = false;
      for (let i = 0; i < 30 && !mirando; i++) {
        mirando = (await O.textContent("#fuentes-web table")).includes("Mirando");
        if (!mirando) { await O.waitForTimeout(1000); await O.click("button:has-text('Actualizar estado')"); await O.waitForSelector("#fuentes-web"); }
      }
      await shot("3_guardada_y_mirando");
      paso("7_guardada_y_el_observador_la_mira", mirando, null);
    } else if (fase === "detener") {
      await O.click(`[data-detener="${E.nombre}"]`);
      await O.waitForFunction((n) => !document.querySelector(`[data-detener="${n}"]`), E.nombre, { timeout: 15000 });
      await shot("1_detenida");
      paso("1_detener_desde_la_lista", (await O.textContent("#fuentes-web table")).includes("detenida"), null);
    } else if (fase === "captcha") {
      // «Usar como base» copia el perfil de la fuente existente al formulario
      await O.click(`[data-copiar="${E.nombre}"]`);
      await O.waitForFunction(() => (document.querySelector("#form-fuente-web [name=zona]") || {}).value === "#turnos", null, { timeout: 15000 });
      const r = await probar();
      const bloqueada = !!(await O.$("#fw-bloqueada"));
      await shot("1_captcha_no_se_puede_observar");
      paso("1_captcha_detectado_y_no_se_guarda", bloqueada && r.ok === "no" && !(await guardarHabilitado()), { r, bloqueada });
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
