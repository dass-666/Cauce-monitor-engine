// Panel en modo producción (v1.2): sin Laboratorio, entorno visible, sin errores de consola.
// Uso: node scripts/e2e_produccion.js http://127.0.0.1:8000 usuario contraseña carpeta_capturas
const { chromium } = require("playwright");
const [base, user, pass, outDir] = process.argv.slice(2);
require("fs").mkdirSync(outDir, { recursive: true });
const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

(async () => {
  const browser = await chromium.launch();
  const page = await (await browser.newContext({ viewport: { width: 1400, height: 900 } })).newPage();
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  page.on("console", (m) => { if (m.type() === "error") errors.push(m.text()); });
  await page.goto(base + "/");
  await page.fill("input[name=username]", user);
  await page.fill("input[name=password]", pass);
  await page.click("button[type=submit]");
  await page.waitForSelector("#nav a");
  const nav = await page.$$eval("#nav a", (els) => els.map((e) => e.textContent.trim()));
  const label = await page.textContent("#user-label");
  log("menú:", nav.join(" | "));
  log("usuario:", label);
  await page.goto(base + "/#laboratorio");          // intento directo: debe volver a Inicio
  await page.waitForTimeout(1500);
  const labShown = await page.locator("text=Inventario ficticio").count();
  await page.goto(base + "/#salud");
  await page.waitForSelector("#view h2:has-text('Autodiagnóstico')");
  await page.screenshot({ path: `${outDir}/20_produccion_salud.png`, fullPage: true });
  const ok = !nav.includes("Laboratorio") && /PRODUCCION/.test(label) && labShown === 0;
  log("sin Laboratorio:", !nav.includes("Laboratorio"), "· entorno visible:", /PRODUCCION/.test(label),
      "· #laboratorio bloqueado:", labShown === 0);
  // En producción el servidor no expone /api/lab: el navegador puede registrar ese 404 si algo lo pidiera.
  log("errores de consola:", errors.length ? errors : "ninguno");
  await browser.close();
  process.exit(ok && !errors.length ? 0 : 1);
})().catch((e) => { console.error(e); process.exit(2); });
