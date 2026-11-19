# Puesta en producción (v1.2)

Esta guía explica cómo dejar el motor listo para uso real. **No sustituye una revisión de
seguridad** del servidor donde se instale; cubre lo que depende del propio motor.

## 1. Los tres entornos

Se elige con `MONITOREO_ENTORNO` en el archivo `.env`.

| | `desarrollo` (por defecto) | `pruebas` | `produccion` |
|---|---|---|---|
| Laboratorio (`/api/lab/*`) | disponible | disponible | **no existe** (404) |
| Rutas de prueba (`/api-prueba/*`) y emulador de Telegram | disponibles | disponibles | **no existen** (404) |
| Datos de demostración | si `MONITOREO_DATOS_DEMO=1` | si `MONITOREO_DATOS_DEMO=1` | **nunca** (aunque se pida) |
| Documentación interactiva (`/api/docs`) | sí | sí | no |
| Configuración insegura | aviso | aviso | **el servidor no arranca** |
| Errores internos | detalle en el registro | igual | al cliente solo "Error interno" |
| Registro | consola | consola | consola + `data/logs/monitoreo.log` (10 MB × 5 archivos) |
| Detector de sesiones anidadas | opcional | activo | inactivo |

En producción las rutas del laboratorio y de prueba **no se registran**: no es que estén
protegidas, es que no existen. Lo comprueba `tests/test_produccion.py`, que recorre 15 rutas
peligrosas y exige 404/405.

## 2. Qué rechaza el modo producción

Si algo de esto falla, `iniciar.bat` muestra `EL SERVIDOR NO ARRANCA` con la lista exacta
de qué cambiar, y termina con código 2.

| Variable | Requisito en producción |
|---|---|
| `MONITOREO_SECRET_KEY` | Al menos 32 caracteres y no un valor de ejemplo. Generar con `python -c "import secrets; print(secrets.token_urlsafe(48))"` |
| `MONITOREO_ADMIN_PASSWORD` | Al menos 12 caracteres, con letras y números. Obligatoria si la base está vacía (no se genera una contraseña automática en producción) |
| `MONITOREO_DATOS_DEMO` | Debe ser `0` |
| `MONITOREO_TELEGRAM_API` | Vacía (usa Telegram real) o `https://…`. Nunca el emulador |
| `MONITOREO_HOST` | `127.0.0.1`, salvo que haya un proxy inverso declarado en `MONITOREO_PROXY_CONFIABLE` |
| `MONITOREO_ENTORNO` | Uno de los tres valores válidos (un error tipográfico detiene el arranque en cualquier entorno) |

Solo **avisos** (se muestran en el panel → Salud → Autodiagnóstico): SQLite en producción,
vida del token mayor de 24 h, `MONITOREO_RESPALDO_PG=omitir`.

## 3. Publicar con HTTPS mediante un proxy inverso

El motor **no** sirve HTTPS por sí mismo, a propósito: gestionar certificados es mejor
dejarlo a un programa especializado. La forma recomendada es:

```
Internet ──HTTPS──▶ proxy inverso (Caddy o nginx) ──HTTP──▶ motor en 127.0.0.1:8000
```

El motor sigue escuchando solo en la propia máquina; nadie de fuera llega a él sin pasar
por el proxy.

### Opción A: Caddy (la más sencilla: obtiene y renueva el certificado solo)

`Caddyfile`:

```
monitor.ejemplo.org {
    reverse_proxy 127.0.0.1:8000
    header Strict-Transport-Security "max-age=31536000"
}
```

`.env` del motor:

```
MONITOREO_ENTORNO=produccion
MONITOREO_HOST=127.0.0.1
MONITOREO_PROXY_CONFIABLE=127.0.0.1
```

### Opción B: nginx

```nginx
server {
    listen 443 ssl;
    server_name monitor.ejemplo.org;
    ssl_certificate     /ruta/fullchain.pem;
    ssl_certificate_key /ruta/privkey.pem;
    add_header Strict-Transport-Security "max-age=31536000" always;
    client_max_body_size 2m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
server { listen 80; server_name monitor.ejemplo.org; return 301 https://$host$request_uri; }
```

### Por qué importa `MONITOREO_PROXY_CONFIABLE`

Detrás de un proxy, el motor ve todas las peticiones como si vinieran del proxy. La IP real
viaja en la cabecera `X-Forwarded-For`, pero **cualquiera puede escribir esa cabecera**. Por
eso el motor solo la cree si la petición viene de la IP indicada en
`MONITOREO_PROXY_CONFIABLE`. Sin esa variable, el límite de peticiones contaría a todos los
usuarios como uno solo (la IP del proxy).

Si el proxy está en **otra máquina**, pon `MONITOREO_HOST=0.0.0.0` (o la IP interna) y en
`MONITOREO_PROXY_CONFIABLE` la IP del proxy, y bloquea el puerto 8000 en el cortafuegos
para todo lo que no sea el proxy.

## 4. Límite de peticiones a la API

Protege al servidor de un script descontrolado o de intentos de adivinar contraseñas.
Es **independiente** del límite de consulta a las fuentes de datos (que protege a los
proveedores): saturar la API no frena al motor (lo prueba
`test_api_rate_limit_is_independent_from_source_limits`).

| Variable | Por defecto | Significado |
|---|---|---|
| `MONITOREO_API_LIMITE_MIN` | 600 | Peticiones `/api` por minuto y por IP |
| `MONITOREO_API_ESCRITURA_MIN` | 120 | De ellas, las que modifican datos (incluye el inicio de sesión) |

Al superarlo se responde `429` con `Retry-After`. `0` desactiva el límite. La cuenta se lleva
en memoria de cada proceso: con dos instancias, cada una cuenta por separado. Es suficiente
para este tamaño de despliegue; un límite compartido (por ejemplo con Redis) solo se
justificaría con muchas instancias y no se ha visto esa necesidad.

## 5. Registros

- En producción se escribe en `data/logs/monitoreo.log`, con rotación (10 MB × 5 archivos,
  unos 60 MB como máximo).
- Nivel con `MONITOREO_LOG` (`info` por defecto; `warning` para menos detalle).
- Los registros **no** contienen el token de Telegram, contraseñas ni datos de titulares.

## 6. Lista de comprobación antes de poner en marcha

1. `.env` con `MONITOREO_ENTORNO=produccion` y todas las variables del apartado 2.
2. Preferiblemente PostgreSQL (`MONITOREO_DB_URL`) con `pg_dump` instalado (respaldo
   automático antes de migrar).
3. `MONITOREO_WEBHOOK_HOSTS` con **solo** los hosts reales que recibirán webhooks.
4. Proxy inverso con HTTPS funcionando y `MONITOREO_PROXY_CONFIABLE` configurado.
5. Ejecutar `diagnostico.bat` (o `python -m monitoreo.diagnostico`). Debe terminar en
   **OK** o **AVISO** sin críticos.
6. Abrir el panel → Salud → Autodiagnóstico y revisar cada aviso.
7. Hacer una copia de seguridad y comprobar que se puede restaurar (ver `README.md`).

## 7. Lo que esta guía no cubre

- Endurecimiento del sistema operativo, cortafuegos y antivirus.
- Copias de seguridad fuera de la máquina (el motor solo hace copias locales).
- Alta disponibilidad real: dos instancias se coordinan con el arrendamiento, pero
  comparten la misma base de datos; si esta cae, el servicio se detiene.
