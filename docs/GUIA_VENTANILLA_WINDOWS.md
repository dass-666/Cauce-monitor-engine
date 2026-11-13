# Guía de Ventanilla en Windows (v2.0, en desarrollo)

Para atender a personas **presentes** frente al operador, de principio a fin, con datos ficticios y
el portal de reservaciones **de prueba** (http://127.0.0.1:8200) que corre en el mismo equipo.

> **Estado honesto.** Esta guía y sus scripts se comprobaron en las máquinas Windows de GitHub
> Actions (Windows Server, Python 3.11–3.13, navegador Chromium automatizado). **No se han probado
> en el equipo MSI físico** ni con su Wi-Fi o cable reales: eso sigue PENDIENTE y es el primer paso
> de la sección G.

## A. Requisitos

- La instalación normal de la guía rápida (`instalar.ps1`, ver `GUIA_RAPIDA_WINDOWS.md`), en una
  carpeta fuera de OneDrive (por ejemplo `C:\motor-monitoreo-v2.0`).
- Para la **demostración automática** (sección C), además: **Node.js LTS** (https://nodejs.org) e
  internet la primera vez (descarga el navegador automatizado, unos 150 MB). Para atender a mano
  (sección B) no hace falta Node.

## B. Atender en ventanilla (uso diario)

1. Doble clic en **`ventanilla.bat`**. Se abren el motor, el portal de prueba (minimizado) y el
   navegador directamente en **Ventanilla**.
2. Entra con un usuario **operador**. El administrador los crea en *Configuración → Usuarios*
   (rol *operador*, región y sucursales).
3. Por cada persona:
   1. **Nueva atención** → busca su expediente (por código, o por nombre + fecha de nacimiento +
      trámite). Si no existe, regístrala. Si ya tenía uno activo, el motor lo dice y atiendes **ese**
      (no se crean duplicados).
   2. Revisa los datos, **teclea los campos manuales** con la persona presente (el motor nunca los
      escribe por su cuenta) y envía.
   3. Elige un **día disponible** y confirma. La pantalla solo muestra «Cita confirmada» cuando el
      portal devolvió un folio **y** el motor lo comprobó por la API del portal.
   4. Si no hay días: **Sin disponibilidad**. El expediente vuelve a la cola del motor.
4. Si se cierra el navegador o se reinicia el equipo a mitad: vuelve a entrar y pulsa **Retomar**
   en la atención abierta. Continúa en el paso exacto donde se quedó.
5. Para terminar: `Ctrl + C` en la ventana negra del motor y cierra la del portal.

### Mensajes que puedes ver

| Mensaje | Qué significa | Qué hacer |
|---|---|---|
| «Base de datos no disponible; no se guardó ningún cambio» | La base de datos no respondió (503). Nada se dio por hecho. | Espera unos segundos y repite. Si sigue, ejecuta `diagnostico.bat`. |
| «El portal limitó las solicitudes» o «negó el acceso» | Restricción del proveedor (429/403). La operación **se detuvo** y no se reintenta por otra red. | No insistas. Revísalo con el responsable del portal. |
| «Esta reservación ya se está confirmando o verificando» | Hay una confirmación en curso o con respuesta perdida. | No la repitas por otro medio: el motor la verifica sola con el portal en segundos. |

## C. Demostración automática (sin teclear nada)

Doble clic en **`demo_ventanilla.bat`** (unos 3 minutos). Usa carpetas temporales: **no toca tu
`data\`**. Un navegador automatizado atiende 6 personas hasta su folio, una sin cita, un duplicado,
una atención retomada tras «tirar» el motor, la vista de teléfono, la pantalla *Conexiones* y
*Trámites a mano*. Resultado en `resultados\demo_v20\demo_v20.json` y capturas en
`resultados\capturas_v20\`.

Código de salida: `0` todo pasó · `1` alguna comprobación falló (está en el JSON) · `3` falta
Node.js o no se pudo descargar el navegador.

## D. Trámites hechos a mano (proveedor sin integración)

Pantalla **Trámites a mano**. Para un portal **oficial** con el que el motor no tiene integración
autorizada: haz el trámite en el sitio oficial con la persona presente y registra aquí el folio y la
fecha que mostró el sitio. Queda **DECLARADO**; nunca cuenta como cita confirmada por el motor. Con el
portal de prueba, el motor sí puede comprobarlo y lo marca **VERIFICADO** o **NO COINCIDE**.
Detalles: `docs\CONTRATO_ADAPTADORES.md`.

## E. Conexiones de red

Pantalla **Conexiones** (administrador). Con el portal de prueba en el mismo equipo verás «Este
equipo»: no hay redes que medir y es lo correcto. Las cifras de 24 h muestran peticiones, caídas y
restricciones del proveedor. `diagnostico.bat` incluye ahora el área **Conectividad** (interfaces
encontradas, cuáles sirven y restricciones recientes).

## F. Si algo falla

1. `diagnostico.bat` y sigue la indicación de cada problema.
2. Registros: la ventana negra del motor (en producción, `data\logs\monitoreo.log`).
3. Para una demostración fallida: `resultados\demo_v20\recorrido_v20_playwright.log` y la captura
   `resultados\capturas_v20\ZZ_fallo_*.png`.

## G. Pendiente de comprobar en el MSI físico (no se ha hecho)

1. `instalar.ps1` y `demo_ventanilla.bat` en el MSI: anotar el resultado del JSON.
2. `ventanilla.bat` atendiendo 2–3 personas ficticias a mano (teclado y ratón reales).
3. *Conexiones* con el portal de prueba en **otro** equipo de la red (hay que añadir su dirección a
   `MONITOREO_WEBHOOK_HOSTS` y `MONITOREO_PORTAL_URL` en `.env`; por defecto solo se permite 127.0.0.1): comprobar que reconoce el Wi-Fi y el
   cable del MSI y que «Medir ahora» da latencias. Desconectar el cable a mitad y ver el cambio a Wi-Fi.
4. Cerrar la tapa / suspender a mitad de una atención y comprobar **Retomar**.
