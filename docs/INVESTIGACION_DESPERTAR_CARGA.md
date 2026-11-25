# Intermitente «Despertar con CPU saturada» (2026-11-25)

**Qué falla:** `test_changes_wake_the_cycle` con un límite de 0,4 s. Se ejecuta 30 veces en el job «Regresión v2.2.2», mientras corren 2 procesos que ocupan la CPU por cada núcleo.

**Fallos registrados:**

| Fecha | Ejecutor | Latencia medida |
|---|---|---|
| Antes del aislamiento de v2.2.2 | Linux · 3.13 | 0,51 y 0,53 s |
| 2026-11-20 | Windows · 3.13 | 0,479 s |
| 2026-11-25, run 45581699336 | Windows · 3.11 | 0,453 s |

## Hechos [V]

- **Frecuencia.** En las 32 corridas completas y no canceladas de `ci.yml` desde el 22-nov, este paso falló una sola vez. Cada corrida lo repite 30 veces en 4 trabajos. Son unas 3800 repeticiones y el fallo aparece 1 vez.
- **El commit no tiene relación.** En la corrida donde falló (1576cc3) solo cambiaban documentación y un script `.ps1`. La otra corrida del mismo commit pasó.
- **Ese mismo trabajo, por lo demás, salió bien:**
  - supervisor: 5 de 5;
  - despertar normal: 30 de 30;
  - script informativo `investigar_despertar.py`, con carga: 40 de 40, máximo 0,125 s.
- **En Linux, con 4 núcleos y 8 procesos ocupando la CPU, no se reproduce.** Se hicieron 150 repeticiones con las tareas de fondo aisladas. Resultados:
  - latencia: mediana 0,086 s, p95 0,095 s, máximo 0,104 s;
  - espera del bucle: 0,031 s;
  - arrendamiento en SQLite: p95 0,0075 s.

## Inferencia [I], no demostrada

Entre «la consulta trae cambios» y «se procesan los eventos» hay unos 4 saltos entre hilos:
1. hilo de consulta → bucle;
2. bucle → hilo del arrendamiento;
3. hilo del arrendamiento → bucle;
4. bucle → hilo de proceso.

En un ejecutor virtual de Windows con el doble de procesos que núcleos, cada salto puede esperar un turno completo del planificador del sistema, que en Windows Server dura más que en Linux. Unos pocos turnos largos bastan para pasar de 0,1 a 0,45 s.

La cola es muy rara, del orden de 1 en varios miles. No hay indicios de un fallo del motor: no se pierde ningún evento, solo llega tarde.

## Qué se hizo, sin cambiar el umbral

Si la prueba vuelve a fallar, el mensaje ahora indica en qué tramo se fue el tiempo, medido desde el inicio de la consulta:
- salida de la espera;
- inicio y fin del arrendamiento;
- latencia total.

Así se puede distinguir entre la espera del bucle, la escritura en SQLite y el salto al hilo de proceso. El umbral sigue en 0,4 s.

## Opciones, sin aplicar

1. **Dejarlo así y esperar al siguiente fallo con tramos (recomendado).** Coste cero. Confirma o descarta la inferencia.
2. **Mejorar el producto: no renovar el arrendamiento al despertar si aún le queda más de la mitad del plazo.** Quita 2 saltos y una escritura en SQLite del camino real. Mejora la latencia de detección a asignación también en producción. Pero toca el planificador, así que conviene hacerlo solo con la evidencia del punto 1 y con pruebas del arrendamiento.
3. **Lo que no se recomienda:** subir el umbral o tolerar un fallo de cada 30 sin una causa demostrada.
