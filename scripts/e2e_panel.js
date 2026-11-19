// Prueba de extremo a extremo del panel con un navegador real (Playwright).
// Uso: node scripts/e2e_panel.js http://127.0.0.1:8000 usuario contraseña carpeta_capturas
const { chromium } = require("playwright");
const [base, user, pass, outDir] = process.argv.slice(2);
const fs = require("fs");
fs.mkdirSync(outDir, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

(async () => {
  const browser = await chromium.launch();
  const ctx = await browser.newContext({ viewport: { width: 1400, height: 900 } });
  const page = await ctx.newPage();
  const errors = [];
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto(base + "/");
  await page.fill("input[name=username]", user);
  await page.fill("input[name=password]", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector(".region");
  const regions = await page.$$eval(".region .name", (els) => els.map((e) => e.textContent));
  log("regiones visibles:", regions.length, regions.join(" | "));
  await page.screenshot({ path: `${outDir}/01_inicio.png`, fullPage: true });

  // Registrar una solicitud para la región ESTE
  await page.goto(base + "/#solicitudes");
  await page.waitForSelector("form input[name=holder]");
  const code = "E2E-" + Date.now().toString().slice(-6);
  await page.fill("input[name=holder]", "Expediente Ficticio E2E " + code);
  await page.selectOption("select[name=region]", "ESTE");
  await page.selectOption("select[name=resource_type]", "unidad");
  await page.selectOption("select[name=priority]", "1");
  await page.click("#view form button[type=submit]");
  await page.waitForFunction(() => document.getElementById("toast").textContent.includes("registrada"));
  log("solicitud:", await page.textContent("#toast"));

  // Laboratorio: liberar una unidad en ESTE
  await page.goto(base + "/#laboratorio");
  await page.waitForSelector("text=Inventario ficticio");
  const filter = page.locator("#view .card .filters select").first();
  await filter.selectOption("ESTE");
  await page.waitForFunction(() => [...document.querySelectorAll("#view table tbody tr td:first-child")].every((td) => td.textContent === "ESTE"));
  const t0 = Date.now();
  const regionsShown = await page.$$eval("#view table tbody tr td:first-child", (t) => [...new Set(t.map((x) => x.textContent))]);
  log("filas del inventario filtrado por región:", regionsShown.join(","));
  await page.evaluate(() => { document.getElementById("toast").textContent = ""; });
  await page.locator("button", { hasText: "Liberar +1" }).first().click();
  await page.waitForFunction(() => document.getElementById("toast").textContent.length > 0);
  log("laboratorio:", await page.textContent("#toast"));
  await page.screenshot({ path: `${outDir}/02_laboratorio.png`, fullPage: true });

  // Esperar a que el motor detecte, asigne y alerte (sin intervención)
  let found = null;
  for (let i = 0; i < 60 && !found; i++) {
    await page.waitForTimeout(1000);
    const alerts = await page.evaluate(async () => {
      const r = await fetch("/api/alerts?limit=50", { headers: { Authorization: "Bearer " + sessionStorage.getItem("token") } });
      return r.json();
    });
    found = alerts.find((a) => a.kind === "ASIGNACION" && a.region === "ESTE" && new Date(a.created_at).getTime() >= t0 - 2000);
  }
  log(found ? `alerta recibida en ${((Date.now() - t0) / 1000).toFixed(1)} s: ${found.title}` : "NO llegó alerta de asignación");
  await page.goto(base + "/#inicio");
  await page.waitForSelector(".alert-item");
  await page.screenshot({ path: `${outDir}/03_inicio_con_alerta.png`, fullPage: true });
  await page.goto(base + "/#monitor");
  await page.waitForSelector("text=Estado de cada fuente");
  await page.screenshot({ path: `${outDir}/04_monitor.png`, fullPage: false });
  await page.goto(base + "/#solicitudes");
  await page.waitForSelector("tr.clickable");
  // la tabla se refresca sola: si el clic cae en una fila que se acaba de redibujar, se repite
  // (hasta 3 veces). Se sigue exigiendo que el detalle con su historial aparezca.
  for (let intento = 1; ; intento++) {
    await page.locator("tr.clickable").first().click();
    try { await page.waitForSelector(".timeline", { timeout: 10000 }); break; }
    catch (e) { if (intento >= 3) throw e; log(`detalle no abrió (intento ${intento}); se repite el clic`); }
  }
  await page.screenshot({ path: `${outDir}/05_detalle_solicitud.png` });
  await page.keyboard.press("Escape");
  await page.goto(base + "/#config");
  await page.waitForSelector("#view form");
  await page.screenshot({ path: `${outDir}/06_configuracion.png`, fullPage: true });

  // v1.1: Salud y diagnóstico de una fuente
  await page.goto(base + "/#salud");
  await page.waitForSelector("text=Estado por fuente");
  // v1.2: tarjeta de autodiagnóstico con problemas y "qué hacer"
  await page.waitForSelector("#view h2:has-text('Autodiagnóstico')");
  const diag = await page.locator("#view section", { has: page.locator("h2:has-text('Autodiagnóstico')") }).first().textContent();
  const diagOk = /crítico\(s\)/.test(diag) && /Correcto:/.test(diag);
  log("autodiagnóstico en el panel:", diag.slice(0, 160).replace(/\s+/g, " "));
  await page.screenshot({ path: `${outDir}/09_salud.png`, fullPage: true });
  // Preferir una fuente con problemas (la demo incluye una API que falla a propósito).
  const bad = page.locator("#view tr.clickable", { hasText: "DESACTUALIZADA" });
  await ((await bad.count()) ? bad.first() : page.locator("#view tr.clickable").first()).click();
  await page.waitForSelector("#drawer:not(.hidden) >> text=Diagnóstico");
  const verdict = await page.textContent("#drawer-body");
  log("diagnóstico abierto:", verdict.slice(0, 120).replace(/\s+/g, " "));
  await page.screenshot({ path: `${outDir}/10_diagnostico.png` });
  await page.keyboard.press("Escape");

  // v1.3: métricas de servicio, operadores y recorrido de un evento de punta a punta
  const svcText = await page.locator("#view section", { has: page.locator("h2:has-text('Servicio en la última hora')") }).first().textContent();
  const opsRows = await page.locator("#view section", { has: page.locator("h2:has-text('Operadores')") }).first().locator("tbody tr").count();
  const svcOk = /Detección/.test(svcText) && /p50 \/ p95 \/ p99/.test(svcText) && opsRows > 0;
  log("métricas de servicio:", svcText.slice(0, 160).replace(/\s+/g, " "), "· operadores:", opsRows);
  await page.goto(base + "/#monitor");
  const evCard = page.locator("#view section", { has: page.locator("h2:has-text('Eventos detectados')") }).first();
  await evCard.locator("tr.clickable").first().waitFor();
  // el evento de la alerta de asignación recibida (si está en la lista) o el primero
  const target = found && found.change_event_id ? evCard.locator("tr.clickable", { hasText: found.resource }).first() : evCard.locator("tr.clickable").first();
  await ((await target.count()) ? target : evCard.locator("tr.clickable").first()).click();
  await page.waitForSelector("#drawer:not(.hidden) >> text=Recorrido del evento");
  const trace = await page.textContent("#drawer-body");
  const traceOk = /detectado por el motor/.test(trace);
  log("recorrido:", trace.slice(0, 200).replace(/\s+/g, " "));
  await page.screenshot({ path: `${outDir}/12_recorrido_evento.png` });
  await page.keyboard.press("Escape");

  // v1.4: asignación sin actividad → indicador en Inicio y Solicitudes → recuperación manual
  const call = (path, method = "GET", body, headers = {}) => page.evaluate(async ([path, method, body, headers]) => {
    const r = await fetch(path, { method, headers: { "Content-Type": "application/json", Authorization: "Bearer " + sessionStorage.getItem("token"), ...headers },
      body: body ? JSON.stringify(body) : undefined });
    return { status: r.status, body: await r.json().catch(() => null) };
  }, [path, method, body, headers]);
  let v14 = false;
  const act = (await call("/api/assignments?status=ACTIVA&limit=20")).body || [];
  const prev = (await call("/api/settings/engine")).body;
  if (act.length) {
    const aid = act[0].id;
    const t1 = await call(`/api/assignments/${aid}/tomar`, "POST", null, { "Idempotency-Key": "e2e-tomar-" + aid });
    const t2 = await call(`/api/assignments/${aid}/tomar`, "POST", null, { "Idempotency-Key": "e2e-tomar-" + aid });
    log("tomar dos veces con la misma clave:", t1.status, t2.status, t2.body && t2.body.repetida ? "(repetida, sin efecto)" : "");
    await call("/api/settings/engine", "PUT", { inactividad_en_proceso_min: 0.05 });
    let idle = [];
    for (let i = 0; i < 30 && !idle.length; i++) {
      await page.waitForTimeout(1000);
      idle = (await call("/api/assignments?sin_actividad=true")).body || [];
    }
    await page.goto(base + "/#inicio");
    // v1.7: Inicio es ahora el centro de operaciones y llama «Operaciones abandonadas» a lo que antes
    // era «Asignaciones sin actividad»; se leen todos los indicadores en alerta, no solo el primero.
    await page.waitForSelector(".kpi.attention", { timeout: 15000 }).catch(() => null);
    const kpiTxt = (await page.locator(".kpi.attention").allTextContents().catch(() => [])).join(" | ");
    log("Inicio · indicador:", kpiTxt.replace(/\s+/g, " "));
    await page.screenshot({ path: `${outDir}/13_inicio_sin_actividad.png`, fullPage: true });
    await page.goto(base + "/#solicitudes");
    await page.waitForSelector("h2:has-text('Asignaciones sin actividad')");
    await page.screenshot({ path: `${outDir}/14_solicitudes_sin_actividad.png`, fullPage: true });
    const idleCard = page.locator("#view section", { has: page.locator("h2:has-text('Asignaciones sin actividad')") }).first();
    await idleCard.locator("button:has-text('Recuperar')").first().click();
    await page.waitForSelector("#drawer:not(.hidden) >> text=Recuperar asignación");
    await page.selectOption("#drawer select[name=resultado]", "devolver_a_cola");
    await page.fill("#drawer input[name=motivo]", "Prueba e2e: el operador ficticio no responde");
    await page.screenshot({ path: `${outDir}/15_recuperar.png` });
    await page.check("#drawer input[name=confirmar]");
    await page.click("#drawer button[type=submit]");
    await page.waitForFunction(() => document.getElementById("toast").textContent.includes("Devuelta a la cola"));
    const after = (await call(`/api/requests/${act[0].request_id}`)).body;
    const audit = ((await call("/api/audit?limit=20")).body || []);
    const audited = JSON.stringify(audit).includes("asignacion_recuperar");
    log("tras recuperar: asignación", (after.asignaciones.find((a) => a.id === aid) || {}).status, "· solicitud", after.status, "· auditada:", audited);
    v14 = t1.status === 200 && t2.status === 200 && idle.length > 0 && /Asignaciones sin actividad|Operaciones abandonadas/.test(kpiTxt)
      && (after.asignaciones.find((a) => a.id === aid) || {}).status === "RECHAZADA" && audited;
  } else log("v1.4: no había asignación ACTIVA para la prueba de inactividad");
  if (prev) await call("/api/settings/engine", "PUT", { inactividad_en_proceso_min: prev.inactividad_en_proceso_min });
  await page.goto(base + "/#monitor");
  await page.waitForSelector("text=Estado de cada fuente");
  const circ = await page.locator("#view tbody").first().textContent();
  const circOk = /NORMAL|EN RECUPERACIÓN|PROBANDO/.test(circ);
  log("v1.4 circuito en lenguaje claro:", circOk, "· inactividad y recuperación:", v14);

  // Teléfono
  const phone = await browser.newContext({ viewport: { width: 390, height: 844 }, deviceScaleFactor: 2, isMobile: true, hasTouch: true });
  const p2 = await phone.newPage();
  p2.on("pageerror", (e) => errors.push(e.message));
  await p2.goto(base + "/");
  await p2.fill("input[name=username]", user);
  await p2.fill("input[name=password]", pass);
  await p2.click("button[type=submit]");
  await p2.waitForSelector(".region");
  const overflow = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  log("desbordamiento horizontal en teléfono (px):", overflow);
  await p2.screenshot({ path: `${outDir}/07_telefono_inicio.png`, fullPage: true });
  await p2.goto(base + "/#solicitudes");
  await p2.waitForSelector("tr.clickable");
  await p2.screenshot({ path: `${outDir}/08_telefono_solicitudes.png` });
  const overflowSol = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  log("desbordamiento horizontal en Solicitudes (teléfono, px):", overflowSol);
  await p2.goto(base + "/#salud");
  await p2.waitForSelector("text=Estado por fuente");
  const overflowSalud = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  log("desbordamiento horizontal en Salud (teléfono, px):", overflowSalud);
  await p2.screenshot({ path: `${outDir}/11_telefono_salud.png`, fullPage: true });
  log("errores de consola:", errors.length ? errors : "ninguno");
  await browser.close();
  process.exit(found && diagOk && svcOk && traceOk && v14 && circOk && overflow <= 0 && overflowSalud <= 0 && overflowSol <= 0 && !errors.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
