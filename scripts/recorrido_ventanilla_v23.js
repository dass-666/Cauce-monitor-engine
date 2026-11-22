// v2.3 · Recorrido de Ventanilla de principio a fin, SOLO con la interfaz (Playwright/Chromium).
// Datos FICTICIOS; portal de laboratorio propio en 127.0.0.1. Lo lanza scripts/recorrido_ventanilla_v23.py.
// Dos personas usan el programa a la vez, cada una en su navegador: el ADMINISTRADOR (plan, observador,
// calendario del laboratorio, revisión de duplicados) y el OPERADOR (atiende a la persona).
// Ninguna acción usa la API directamente: todo son clics y teclas sobre las pantallas.
// La «confirmación humana» de la cita la hace este script (es una SIMULACIÓN y así consta en la evidencia).
// Uso: node scripts/recorrido_ventanilla_v23.js <motor> <capturas> <entrada.json>
//   entrada.json: {admin:[u,p], operador:[u,p], persona:{nombre,parecido,telefono,correo}, mes:"AAAA-MM", dia:"AAAA-MM-DD"}
// Imprime "RESULTADO {json}" (solo códigos y estados; nunca datos de personas).
const { chromium } = require("playwright");
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { diagnosticarCalendario } = require("./diagnostico_ci.js");
const [base, outDir, entradaPath] = process.argv.slice(2);
const E = JSON.parse(fs.readFileSync(entradaPath, "utf-8"));
fs.mkdirSync(outDir, { recursive: true });
const CLAVE = "QXZ482915";      // campo manual ficticio que el operador teclea

(async () => {
  const browser = await chromium.launch();
  const res = { pasos: [], errores_js: [], http_errores: [] };
  const abrir = async (quien) => {
    const ctx = await browser.newContext({ viewport: { width: 1366, height: 900 }, deviceScaleFactor: 1 });
    const page = await ctx.newPage();
    page.on("pageerror", (e) => res.errores_js.push(`${quien}: ${e.message}`));
    page.on("console", (m) => { if (m.type() === "error" && !m.text().startsWith("Failed to load resource")) res.errores_js.push(`${quien}: ${m.text()}`); });
    page.on("response", (r) => { if (r.status() >= 400) res.http_errores.push(`${quien} ${r.status()} ${r.request().method()} ${new URL(r.url()).pathname}`); });
    return page;
  };
  const A = await abrir("admin"), O = await abrir("operador");
  let n = 0;
  const shot = async (p, nombre) => { n += 1; await p.screenshot({ path: path.join(outDir, `${String(n).padStart(2, "0")}_${nombre}.png`), fullPage: true }); };
  const paso = (k, ok, detalle) => { res.pasos.push({ paso: k, ok: !!ok, detalle }); if (!ok) throw new Error(`paso ${k} falló: ${JSON.stringify(detalle)}`); };
  const entrar = async (p, [u, pw]) => {
    await p.goto(base + "/");
    await p.fill("input[name=username]", u); await p.fill("input[name=password]", pw);
    await p.click("button[type=submit]");
    await p.waitForSelector("#nav a");
  };
  const ir = async (p, vista) => { await p.goto(base + "/#" + vista); await p.waitForTimeout(300); await p.reload(); await p.waitForSelector("#view > *"); };
  const aLaLista = async (p) => {
    await ir(p, "ventanilla");
    const volver = await p.$("#atencion button:has-text('Volver a la lista')");   // abierta o cerrada
    if (volver) await volver.click();
    await p.waitForSelector("#btn-nueva-atencion");
  };
  const hasta = async (fn, ms = 60000, cada = 1000) => {
    const t0 = Date.now();
    while (Date.now() - t0 < ms) { if (await fn()) return true; await new Promise((r) => setTimeout(r, cada)); }
    return false;
  };

  try {
    // ------------------------------------------------------------ 1. abrir Cauce Monitor
    await entrar(O, E.operador); await entrar(A, E.admin);
    await aLaLista(O);
    await shot(O, "operador_ventanilla_inicio");
    paso("1_abrir_cauce", await O.$("#btn-nueva-atencion"), "Ventanilla del operador cargada");

    // ------------------------------------------------------------ 2. plan diario (admin lo crea; observador en marcha)
    await aLaLista(A);
    if (await A.$("#btn-crear-plan")) { await A.click("#btn-crear-plan"); await A.waitForSelector("#plan-dia[data-plan]:not([data-plan=ninguno])"); }
    await A.fill("#obs-mes", E.mes);
    await A.click("#btn-iniciar-observador");
    const obsOk = await hasta(async () => {
      await aLaLista(A);
      return (await A.getAttribute("#observador", "data-vista")) === "ACTIVA";
    }, 90000, 2000);
    await shot(A, "admin_plan_del_dia_y_observador");
    paso("2_plan_diario_visible", obsOk && await A.$("#plan-dia:not([data-plan=ninguno])"),
      { plan: await A.getAttribute("#plan-dia", "data-plan"), observador: await A.getAttribute("#observador", "data-vista") });

    // registros ficticios que reconoce el portal (el admin los ve en «Calendario del laboratorio»)
    await ir(A, "laboratorio-calendario");
    await A.waitForSelector("#calendario-lab[data-disponible=si] [data-registro]");
    const registro = await A.getAttribute("[data-registro] >> nth=0", "data-registro");
    const nacimiento = await A.getAttribute("[data-nacimiento] >> nth=0", "data-nacimiento");
    await shot(A, "admin_calendario_laboratorio_y_registros_ficticios");

    // ------------------------------------------------------------ 3-4. expediente nuevo + consentimiento
    const doc = path.join(outDir, "formato_consentimiento_ficticio.pdf");
    fs.writeFileSync(doc, "%PDF-1.4\n% formato de consentimiento FICTICIO del laboratorio\n%%EOF\n");
    const huella = crypto.createHash("sha256").update(fs.readFileSync(doc)).digest("hex");
    const alta = async (nombre, conConsentimiento) => {
      await aLaLista(O);
      await O.click("#btn-nueva-atencion");
      await O.waitForSelector("#form-persona");
      const f = (k, v) => O.fill(`#form-persona [name=${k}]`, v);
      await f("nombre", nombre); await f("fecha_nacimiento", nacimiento); await f("id_registro_previo", registro);
      await f("telefono", E.persona.telefono); await f("telefono_confirmacion", E.persona.telefono);
      await f("correo", E.persona.correo); await f("correo_confirmacion", E.persona.correo);
      await O.selectOption("#form-persona [name=tipo_tramite]", "alta");
      await O.selectOption("#form-persona [name=region]", "NORTE");
      await f("sucursal", "NOR-1"); await f("sucursales_alternativas", "NOR-2");
      if (conConsentimiento) {
        await O.selectOption("#cons-medio", "PRESENCIAL_VENTANILLA");
        await O.fill("#cons-aviso", "AP-2026-01");
        await O.setInputFiles("#cons-archivo", doc);
        await hasta(async () => (await O.inputValue("#cons-huella")) === huella, 5000, 200);
        await O.fill("#cons-ref", "Carpeta LAB-2026-11 hoja 1");
      }
      await O.click("#form-persona button.btn.primary");
    };
    await alta(E.persona.nombre, true);
    await O.waitForSelector("#atencion[data-estado=EN_PROCESO] #siguiente-paso");
    const sig = await O.getAttribute("#siguiente-paso", "data-siguiente");
    await O.click("#consentimiento-vigente summary");
    const consCodigo = await O.getAttribute("#consentimiento-vigente", "data-consentimiento");
    await shot(O, "operador_expediente_creado_con_consentimiento");
    const atencion1 = await O.getAttribute("#atencion", "data-atencion");
    paso("3_expediente_ficticio_creado", sig === "preparar", { siguiente: sig });
    paso("4_consentimiento_registrado_y_consultado", !!consCodigo, { consentimiento: consCodigo, huella_calculada_en_navegador: true });

    // ------------------------------------------------------------ 5. validación y duplicados
    // 5a. exactamente la misma persona y trámite: el motor lo detiene y ofrece atender el que ya existe
    await alta(E.persona.nombre, false);
    await O.waitForSelector("#aviso-duplicado .error");
    const avisoExacto = (await O.textContent("#aviso-duplicado")).includes("Ya existe");
    await shot(O, "operador_duplicado_exacto_detectado");
    // esta segunda atención no tiene expediente: se cierra como «la persona se retira»
    await O.click("#cierre-sin-cita summary");
    await O.selectOption("#resultado-cierre", "CANCELADA");
    await O.fill("#motivo-cierre", "ya tenia expediente");
    await O.click("#btn-cerrar-sin-cita");
    await O.waitForSelector("#resultado-atencion");
    // 5b. un nombre parecido (letras cambiadas) con la misma fecha: queda EN REVISIÓN, no entra a la cola
    await alta(E.persona.parecido, true);
    await O.waitForSelector("#expediente-en-espera[data-estado-expediente=REQUIERE_REVISION]");
    const atencionParecida = await O.getAttribute("#atencion", "data-atencion");
    await shot(O, "operador_posible_duplicado_en_revision");
    // 5c. el administrador lo ve en su Ventanilla y decide: es la misma persona -> cancela el nuevo
    await aLaLista(A);
    await A.waitForSelector("#revision-duplicados [data-revision]");
    await shot(A, "admin_revision_de_posibles_duplicados");
    await A.click("#revision-duplicados [data-revision] >> nth=0 >> button.btn.danger");
    await hasta(async () => { await aLaLista(A); return !(await A.$("#revision-duplicados [data-revision]")); }, 15000, 1000);
    // 5d. el operador cierra esa atención (la persona era la misma: se retira)
    await aLaLista(O);
    await O.click(`[data-retomar="${atencionParecida}"]`);
    await O.waitForSelector("#cierre-sin-cita");
    res.revision_tras_decision = await O.getAttribute("#atencion", "data-estado");
    await shot(O, "operador_atencion_tras_decision_del_admin");
    await O.click("#cierre-sin-cita summary");
    await O.selectOption("#resultado-cierre", "CANCELADA");
    await O.fill("#motivo-cierre", "duplicado");
    await O.click("#btn-cerrar-sin-cita");
    await O.waitForSelector("#resultado-atencion");
    paso("5_validacion_y_duplicados", avisoExacto && !(await A.$("#revision-duplicados [data-revision]")),
      { exacto: "bloqueado (409) con opción de atender el existente", parecido: "en revisión; el admin lo canceló" });

    // ------------------------------------------------------------ 6. incorporar al plan del día
    await aLaLista(O);
    await O.click(`[data-retomar="${atencion1}"]`);
    await O.waitForSelector("#tarjeta-al-plan");
    await O.click("#tarjeta-al-plan summary");
    await O.fill("#al-plan-desde", `${E.mes}-01`);
    await O.fill("#al-plan-hasta", E.fin_mes);
    await shot(O, "operador_dejar_en_plan_del_dia");
    await O.click("#btn-al-plan");
    await O.waitForSelector("#atencion-en-plan");
    await O.waitForSelector("#historial-expediente");
    await shot(O, "operador_atencion_cerrada_queda_en_plan");
    paso("6_incorporado_al_plan", true, { resultado: "EN_PLAN_DEL_DIA" });

    // ------------------------------------------------------------ 7. estado y pasos pendientes
    await aLaLista(O);
    await O.waitForSelector("#plan-dia [data-plan-codigo]");
    const estadoPlan = await O.getAttribute("#plan-dia [data-estado-plan] >> nth=0", "data-estado-plan");
    await O.click("#plan-dia table tbody tr >> nth=0");
    await O.waitForSelector("#detalle-plan");
    await shot(O, "operador_estado_en_plan_y_detalle");
    await O.click("#drawer-close");
    paso("7_estado_y_pendientes", estadoPlan === "EN_ESPERA", { estado_plan: estadoPlan });

    // ------------------------------------------------------------ 8. calendario del laboratorio -> operación simulada
    await ir(A, "laboratorio-calendario");
    await A.waitForSelector("#calendario-lab[data-disponible=si]");
    for (let k = 0; k < 6 && (await A.getAttribute("#calendario-lab", "data-mes")) !== E.mes; k++) {
      await A.click("#calendario-lab button:has-text('Mes siguiente')");
      await A.waitForTimeout(700);
    }
    await A.click(`[data-abrir="${E.dia}"]`);
    await A.waitForSelector(`#calendario-lab-mes [data-fecha="${E.dia}"][data-estado=DISPONIBLE]`);
    await shot(A, "admin_dia_abierto_en_calendario_laboratorio");
    let boton = null;
    await hasta(async () => {
      await aLaLista(O);
      boton = await O.$("[data-preparar-plan]");
      return !!boton;
    }, 60000, 1500);
    paso("8a_plan_asigna_el_dia", !!boton, { fecha_asignada: boton && await boton.getAttribute("data-fecha-asignada") });
    await shot(O, "operador_coincidencia_en_plan");
    const fecha = await boton.getAttribute("data-fecha-asignada");
    await boton.click();
    await O.waitForSelector("#captura-manual");
    await O.locator("#manual-clave_identidad").pressSequentially(CLAVE, { delay: 5 });
    await O.check("#manual-acepto_privacidad");
    await O.check("#chk-revisado");
    await shot(O, "operador_revision_de_datos_y_campos_manuales");
    await O.click("#btn-enviar");
    await O.waitForSelector("#flujo .mes");
    let dia = await O.$(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
    for (let k = 0; !dia && k < 3; k++) {
      await O.click("#flujo .filters button:nth-of-type(2)");
      await O.waitForTimeout(700);
      dia = await O.$(`#flujo .mes button.dia[data-fecha="${fecha}"]`);
    }
    paso("8b_calendario_del_portal_muestra_el_dia", !!dia, { fecha });
    await shot(O, "operador_calendario_del_portal_de_laboratorio");
    await dia.click();
    await O.check("#chk-confirmo");          // confirmación humana: aquí la SIMULA el script
    await shot(O, "operador_confirmacion_simulada");
    await O.click("#btn-confirmar");
    await O.waitForSelector("#btn-cerrar-atencion", { timeout: 60000 });

    // ------------------------------------------------------------ 9. resultado y folio
    const folio = await O.getAttribute("#folio", "data-folio");
    await shot(O, "operador_cita_confirmada_folio");
    paso("9_resultado_y_folio", /\S/.test(folio || ""), { folio });
    res.folio = folio; res.fecha = fecha;

    // ------------------------------------------------------------ 10. cerrar la atención y consultar su historial
    await O.click("#btn-cerrar-atencion");
    await O.waitForSelector("#folio-atencion");
    await O.waitForSelector("#historial-expediente[data-disponible=si]");
    const folioHist = await O.textContent("#historial-folio");
    const tipos = await O.$$eval("#historial-linea li", (xs) => xs.map((x) => x.dataset.tipo));
    const cadena = await O.getAttribute("#historial-cadena", "data-ok");
    await shot(O, "operador_atencion_cerrada_historial_completo");
    paso("10_cierre_e_historial", folioHist === folio && cadena === "si", { folio_historial: folioHist, eventos: tipos.length, cadena_consentimiento: cadena, tipos: [...new Set(tipos)] });

    // el calendario del laboratorio refleja la cita (el día queda ocupado)
    await ir(A, "laboratorio-calendario");
    await A.waitForSelector("#calendario-lab[data-disponible=si]");
    for (let k = 0; k < 6 && (await A.getAttribute("#calendario-lab", "data-mes")) !== E.mes; k++) {
      await A.click("#calendario-lab button:has-text('Mes siguiente')");
      await A.waitForTimeout(700);
    }
    res.dia_tras_cita = await A.getAttribute(`#calendario-lab-mes [data-fecha="${fecha}"]`, "data-estado");
    await shot(A, "admin_calendario_tras_la_cita");
    // retomar: el operador vuelve a la lista y ve la atención en «Tus atenciones de hoy»
    await aLaLista(O);
    await shot(O, "operador_ventanilla_final");
  } catch (e) {
    res.fallo = String(e.message || e).slice(0, 400);
    res.diagnostico = await diagnosticarCalendario(A, E.dia, res.pasos);     // solo observa (ver docs/CI_RESERVACIONES_DIAGNOSTICO.md)
    await shot(O, "ZZ_fallo_operador").catch(() => {});
    await shot(A, "ZZ_fallo_admin").catch(() => {});
  }
  await browser.close();
  console.log("RESULTADO " + JSON.stringify(res));
  process.exit(res.fallo ? 1 : 0);
})();
