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
  await page.locator("tr.clickable").first().click();
  await page.waitForSelector(".timeline");
  await page.screenshot({ path: `${outDir}/05_detalle_solicitud.png` });
  await page.keyboard.press("Escape");
  await page.goto(base + "/#config");
  await page.waitForSelector("#view form");
  await page.screenshot({ path: `${outDir}/06_configuracion.png`, fullPage: true });

  // v1.1: Salud y diagnóstico de una fuente
  await page.goto(base + "/#salud");
  await page.waitForSelector("text=Estado por fuente");
  await page.screenshot({ path: `${outDir}/09_salud.png`, fullPage: true });
  // Preferir una fuente con problemas (la demo incluye una API que falla a propósito).
  const bad = page.locator("#view tr.clickable", { hasText: "DESACTUALIZADA" });
  await ((await bad.count()) ? bad.first() : page.locator("#view tr.clickable").first()).click();
  await page.waitForSelector("#drawer:not(.hidden) >> text=Diagnóstico");
  const verdict = await page.textContent("#drawer-body");
  log("diagnóstico abierto:", verdict.slice(0, 120).replace(/\s+/g, " "));
  await page.screenshot({ path: `${outDir}/10_diagnostico.png` });
  await page.keyboard.press("Escape");

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
  await p2.goto(base + "/#salud");
  await p2.waitForSelector("text=Estado por fuente");
  const overflowSalud = await p2.evaluate(() => document.documentElement.scrollWidth - window.innerWidth);
  log("desbordamiento horizontal en Salud (teléfono, px):", overflowSalud);
  await p2.screenshot({ path: `${outDir}/11_telefono_salud.png`, fullPage: true });
  log("errores de consola:", errors.length ? errors : "ninguno");
  await browser.close();
  process.exit(found && overflow <= 0 && overflowSalud <= 0 && !errors.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
