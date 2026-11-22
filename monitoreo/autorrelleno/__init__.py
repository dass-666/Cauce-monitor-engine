"""Autorrellenado del formulario de LABORATORIO con Forms (Conector Universal) desde Ventanilla.

Solo laboratorio: el router vive en `modulos.SOLO_LABORATORIO` (nunca se monta en producción) y cada
petición vuelve a comprobar las condiciones de `adaptador.configuracion()`. `conector.aislamiento.puede_lanzar()`
sigue en False: esto NO es el lanzador operativo. Ver docs/AUTORRELLENO_FORMS_LAB.md.
"""
