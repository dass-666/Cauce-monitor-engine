"""Interoperabilidad con Jarvis Personal (v2.2.1).

Cauce solo EXPONE contratos y comandos seguros; no es un agente ni decide nada por su cuenta.

- `inventario`  estado real del repositorio (solo lectura: git, migraciones, pruebas, scripts).
- `manifiesto`  lee y valida jarvis/test-manifest.json (la lista blanca de suites).
- `ejecutor`    ejecuta UNA suite de la lista blanca con límites de tiempo y salida, entorno aislado
                y cancelación segura. Nunca acepta un comando: solo el identificador de la suite.
- `informe`     exportador de solo lectura del informe JSON (jarvis/report.schema.json).
- `esquema`     validador mínimo de JSON Schema (sin dependencias).
- `yaml_simple` lector del subconjunto de YAML que usa jarvis/project.yaml (sin dependencias).
- `saneado`     quita rutas privadas, correos, credenciales y tokens antes de escribir nada.

Uso: python -m monitoreo.interop --help
"""
