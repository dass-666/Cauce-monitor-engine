# GitHub Actions: ejecución de referencia v1.3

Rama fase-1.3, ejecución 43313525646 (commit 393aca9), 12 de noviembre de 2026.
Resultado: **12/12 trabajos correctos**.

| Trabajo | Resultado |
|---|---|
| Linux · Python 3.11 / 3.12 / 3.13 | 258 pruebas pasan (SQLite + PostgreSQL 16) |
| Windows · Python 3.11 / 3.12 / 3.13 | 166 pasan, 1 omitida (requiere PostgreSQL) |
| Windows · PostgreSQL preinstalado | 258 pasan |
| Integración HTTP · Linux | 40 pasan; escenario corto 15/15 (SQLite) y 15/15 (PostgreSQL) |
| Integración HTTP · Windows | 21 pasan; escenario corto 15/15 (SQLite), tres servicios como procesos |
| Scripts PowerShell 5.1 y 7; instalación y actualización | correctos |

Ejecuciones anteriores de la v1.3 y lo que encontraron: 43292836522 y 43307083842 (dos pruebas
sensibles a tiempos de Windows; bloqueo mutuo del banco de pruebas con PostgreSQL),
43311136309 y 43312383084 (clasificación de la auditoría del banco). Todas corregidas.

---

# GitHub Actions: ejecución de referencia v1.2

Repositorio dass-666/Cauce-monitor-engine, rama fase-1.2, ejecución 43278001279
(commit 2d70082), 12 de noviembre de 2026. Resultado: **todos los trabajos correctos**.

| Trabajo | Resultado |
|---|---|
| Linux · Python 3.11 | 218 pruebas pasan (SQLite + PostgreSQL 16) · arranque real 35/35 |
| Linux · Python 3.12 | 218 pasan · arranque real 35/35 |
| Linux · Python 3.13 | 218 pasan · arranque real + panel con Chromium 37/37 |
| Windows · Python 3.11 | 145 pasan, 1 omitida (requiere PostgreSQL) · arranque real 35/35 |
| Windows · Python 3.12 | 145 pasan, 1 omitida · arranque real 35/35 |
| Windows · Python 3.13 | 145 pasan, 1 omitida · arranque real + panel con Chromium 37/37 |
| Windows · PostgreSQL preinstalado | 218 pasan |
| Scripts PowerShell 5.1 y 7 | sintaxis correcta, codificación correcta, PSScriptAnalyzer: 0 errores, 4 avisos de estilo |
| Instalación y actualización · PowerShell 5.1 | instalación limpia, reinstalación, diagnóstico, arranque 35/35, actualización desde v1.0, negativa a sobrescribir: todo correcto |
| Instalación y actualización · PowerShell 7 | igual: todo correcto |

Ejecuciones anteriores (fallidas) y lo que encontraron: 43274389993 (acentos dañados; mensaje
de actualización engañoso), 43276276662 (acentos en preparar.py), 43276860890 (prueba que leía
la salida con la codificación antigua). Todas corregidas.

## v1.4 (rama fase-1.4) — ejecución 43337690936, commit a7db5ce: 15/15 en verde

| Trabajo | Resultado |
|---|---|
| Linux · Python 3.11 / 3.12 / 3.13 | 324 pasan, 4 omitidas (carreras solo de PostgreSQL, que sí corren en PostgreSQL) · arranque real 35/35; con 3.13 panel con Chromium 37/37 |
| Windows · Python 3.11 / 3.12 / 3.13 | 213 pasan, 5 omitidas (requieren PostgreSQL) · arranque real 35/35; con 3.13 panel 37/37 |
| Windows · PostgreSQL preinstalado | 324 pasan, 4 omitidas |
| Scripts PowerShell 5.1 y 7 | correcto |
| Instalación y actualización · PowerShell 5.1 y 7 | instalación limpia, reinstalación, actualización desde v1.0, arranque 35/35 |
| **Actualizar desde v1.1 / v1.2 / v1.3** (nuevo) · PowerShell 5.1 | instalador original de cada versión (v1.2, v1.3), datos creados por su código, actualización lado a lado, mismas cuentas, integridad, arranque real: correcto |
| Integración HTTP · Linux (SQLite y PostgreSQL) y Windows (SQLite) | 15/15 en cada escenario |

Ejecuciones anteriores en rojo y causa: 43330949224 (1 mensaje de Telegram repetido tras el corte
brusco del motor: la comprobación no admitía la entrega «al menos una vez»), 43335471970 (preparación
del nuevo trabajo de actualización: comillas en PowerShell), 43336363249 (siembra concurrente en
SQLite, defecto real corregido; y trabajo de actualización: faltaban los archivos de demostración y
un aviso se trataba como error). Todas corregidas.
