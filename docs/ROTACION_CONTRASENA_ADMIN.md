# Rotación de la contraseña del administrador (F3)

**Herramienta:** `scripts/rotar_contrasena_admin.py`. No imprime, no registra y no recibe por argumentos ninguna contraseña.

## Qué hace la aplicación (3cda555)

- **Cambio oficial:**
  - Se hace en el panel: Usuarios → «Cambiar mi contraseña».
  - Llama a `POST /api/auth/password`, que exige la contraseña actual.
  - Guarda la nueva con scrypt, cierra las demás sesiones y deja el evento `cambio_contrasena` en `audit_log`.
- **`MONITOREO_ADMIN_PASSWORD` del `.env`:**
  - Solo se usa si la base no tiene ningún administrador, para crearlo.
  - Con un administrador ya existente no se usa para nada.
  - Quitarla del `.env` no impide arrancar.
  - Si está presente y es débil, solo genera un aviso (fuera de producción).
- **Vía de recuperación:**
  - La aplicación no tiene ninguna.
  - Este script la añade con la orden `emergencia`, que funciona con Monitor detenido:
    - pide la nueva contraseña con getpass;
    - hace antes una copia de la base;
    - cierra las sesiones;
    - deja el evento en la auditoría.
  - Antes de tocar la credencial real, se ensaya en una copia con `prueba-emergencia`.

## Procedimiento

1. `inventario`: lista los usuarios (sin hashes), comprueba si la contraseña del `.env` es la vigente y localiza los archivos que la contienen (UTF-8 y UTF-16).
2. `instantanea antes` y `prueba-emergencia`, ambas con Monitor detenido.
3. Arrancar Monitor y ejecutar `login 200`. Comprueba la contraseña antigua y cierra la sesión que abre.
4. El administrador cambia la contraseña en el panel. Después cierra sesión y vuelve a entrar con la nueva.
5. `login 401` (rechazo de la antigua) y `auditoria DESDE`, que debe mostrar `cambio_contrasena` seguido de un `login`.
6. Detener Monitor. Ejecutar `instantanea despues` y `comparar antes despues`: si cambia alguna tabla de expedientes, reservaciones o consentimientos, la comparación falla.
7. `retirar --confirmo`:
   - Se niega a actuar si la contraseña del `.env` sigue siendo la vigente.
   - Solo actúa si el `.env` y el `.txt` vivos tienen copias idénticas en todos los respaldos indicados.
   - Quita la línea del `.env` en el mismo archivo, así conserva sus permisos NTFS.
   - Borra el `.txt` vivo.
   - Vuelve a escanear.
8. Arrancar Monitor y comprobar `/api/health`.

**Reversión:**
- Copiar el `.env` y el `.txt` desde el respaldo F3-0.
- La contraseña nueva solo se revierte con `emergencia`, o restaurando la base del respaldo, que perdería los datos posteriores.

## Prueba en Linux

La prueba se hizo sobre 3cda555, con la API real y contraseñas ficticias:
- inventario correcto;
- ensayo de emergencia correcto;
- login 200 antes del cambio y 401 después;
- en la comparación cambiaron `users`, `api_tokens`, `audit_log` y tablas del motor, y 0 tablas protegidas;
- retirada correcta, con los finales de línea CRLF conservados y 0 archivos con la contraseña tras retirar;
- `/api/health` ok sin la línea en el `.env`;
- `emergencia` probada con éxito;
- 0 contraseñas en las salidas.

**Hallazgo menor, no corregido:** en 3cda555, un login fallido no queda en `audit_log`, porque el evento se deshace junto con la petición que falla.
