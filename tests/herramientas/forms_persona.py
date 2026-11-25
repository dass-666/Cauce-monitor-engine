"""Forms REAL con una persona SIMULADA dentro de la MISMA ventana (solo pruebas y recorridos de laboratorio).

Uso: <python de Forms> forms_persona.py <carpeta de Forms> <fecha AAAA-MM-DD> <sucursal> <archivo folio> [clave]
Monitor lo lanza en lugar de `python -m cauce_form_connector preparar` (MONITOREO_FORMS_COMANDO). Corre el modo de
integración de Forms tal cual (lee la solicitud por stdin, rellena y verifica, NUNCA envía) y, durante la revisión,
hace de persona con clics y teclas reales en la ventana que Forms dejó abierta: teclea los campos manuales,
«Continuar», elige la sucursal y el día de la coincidencia, «Continuar», lee el folio y cierra la ventana.
No vuelve a teclear ninguno de los campos que rellenó Forms. El folio (no es un dato personal) se escribe en
<archivo folio> para que el recorrido lo registre en Ventanilla.
"""
import os
import re
import sys
from pathlib import Path

FORMS, FECHA, SUCURSAL, SALIDA = Path(sys.argv[1]), sys.argv[2], sys.argv[3], Path(sys.argv[4])
CLAVE = sys.argv[5] if len(sys.argv) > 5 else "QXZ482915"
if (FORMS / ".pw-browsers").is_dir():
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(FORMS / ".pw-browsers"))
sys.path.insert(0, str(FORMS))
from cauce_form_connector import integracion as I  # noqa: E402

estado = {"paso": 0, "llamadas_tras_envio": 0}


def persona(s):
    p = s.page
    if estado["paso"] == 0:                       # revisa la ventana y teclea SOLO los campos manuales
        p.wait_for_timeout(int((I.GRACIA_ACTIVACION_S + 1) * 1000))
        p.locator("#clave_identidad").click()
        p.keyboard.type(CLAVE, delay=40)
        p.locator("#acepto_privacidad").check()
        p.locator("button[type=submit]").first.click()
        p.wait_for_load_state()
        estado["paso"] = 1
        return
    if estado["paso"] == 1:                       # Forms ya vio el envío; la ventana debe seguir abierta
        estado["llamadas_tras_envio"] += 1
        if 'data-pantalla="calendario"' not in p.content():
            return
        p.select_option("select[name=sucursal]", SUCURSAL)
        p.locator("button[type=submit]").first.click()
        p.wait_for_load_state()
        for _ in range(4):
            if p.locator(f'input[type=radio][value="{FECHA}"]').count():
                break
            p.get_by_text("Mes siguiente").first.click()
            p.wait_for_load_state()
        p.locator(f'input[type=radio][value="{FECHA}"]').check()
        p.locator("button[type=submit]").last.click()
        p.wait_for_load_state()
        m = re.search(r'data-folio="([^"]+)"', p.content())
        SALIDA.write_text(m.group(1) if m else "", "utf-8")
        estado["paso"] = 2
        return
    p.wait_for_timeout(1500)                      # la persona lee el folio y cierra la ventana
    p.close()


if __name__ == "__main__":
    texto = sys.stdin.read()
    resp, salida = I.ejecutar(texto, gancho_revision=persona)
    if resp is not None:
        sys.stdout.write(__import__("json").dumps(resp, ensure_ascii=False))
        sys.stdout.flush()
    sys.exit(salida)
