"""Lector del subconjunto de YAML que usa jarvis/project.yaml (v2.2.1), sin dependencias.

Se admite solo lo necesario, y lo demás se RECHAZA con un error claro (nunca se adivina):
- mapas con sangría de espacios (`clave: valor`, `clave:` seguido de un bloque);
- listas en bloque (`- valor` y `- clave: valor` con más claves debajo);
- cadenas entre comillas dobles o simples, enteros, true/false, null o ~ y cadenas simples;
- comentarios con `#` al inicio o tras un espacio; `[]` y `{}` vacíos.
No se admiten tabuladores, anclas (&, *), etiquetas (!), bloques | o >, ni listas o mapas en línea.

Jarvis puede leer el mismo archivo con cualquier biblioteca YAML: este subconjunto es YAML válido
(una prueba lo compara con PyYAML cuando está instalado).
"""
from __future__ import annotations

import re


class ErrorYaml(ValueError):
    pass


_ENTERO = re.compile(r"^-?(0|[1-9]\d*)$")
_CLAVE = re.compile(r"^([A-Za-z0-9_.-]+):(?:\s+(.*))?$")


def _quitar_comentario(linea: str) -> str:
    fuera, comilla = [], None
    for i, ch in enumerate(linea):
        if comilla:
            if ch == comilla and not (comilla == '"' and i > 0 and linea[i - 1] == "\\"):
                comilla = None
        elif ch in "\"'":
            comilla = ch
        elif ch == "#" and (i == 0 or linea[i - 1] in " \t"):
            break
        fuera.append(ch)
    return "".join(fuera).rstrip()


def _escalar(texto: str, n: int):
    t = texto.strip()
    if t in ("", "~", "null"):
        return None
    if t == "true":
        return True
    if t == "false":
        return False
    if t == "[]":
        return []
    if t == "{}":
        return {}
    if t[0] == '"':
        if len(t) < 2 or t[-1] != '"':
            raise ErrorYaml(f"línea {n}: comilla doble sin cerrar")
        cuerpo = t[1:-1]
        return (cuerpo.replace('\\"', '"').replace("\\\\", "\\").replace("\\n", "\n")
                .replace("\\t", "\t"))
    if t[0] == "'":
        if len(t) < 2 or t[-1] != "'":
            raise ErrorYaml(f"línea {n}: comilla simple sin cerrar")
        return t[1:-1].replace("''", "'")
    if t[0] in "[{&*!|>%@`":
        raise ErrorYaml(f"línea {n}: construcción YAML no admitida: {t[:20]!r}")
    if _ENTERO.match(t):
        return int(t)
    if t in ("yes", "no", "on", "off", "True", "False", "TRUE", "FALSE", "Null", "NULL"):
        raise ErrorYaml(f"línea {n}: valor ambiguo {t!r}: usa true/false/null o ponlo entre comillas")
    if ": " in t or t.endswith(":"):
        raise ErrorYaml(f"línea {n}: dos puntos dentro de un valor: ponlo entre comillas")
    return t


def cargar(texto: str):
    lineas: list[tuple[int, int, str]] = []
    for n, cruda in enumerate(texto.splitlines(), 1):
        if "\t" in cruda[: len(cruda) - len(cruda.lstrip())]:
            raise ErrorYaml(f"línea {n}: tabulador en la sangría")
        limpia = _quitar_comentario(cruda)
        if not limpia.strip():
            continue
        if limpia.strip() in ("---", "..."):
            raise ErrorYaml(f"línea {n}: varios documentos no admitidos")
        lineas.append((len(limpia) - len(limpia.lstrip(" ")), n, limpia.strip()))
    if not lineas:
        return None
    valor, i = _bloque(lineas, 0, lineas[0][0])
    if i != len(lineas):
        raise ErrorYaml(f"línea {lineas[i][1]}: sangría inesperada")
    return valor


def _bloque(lineas, i, sangria):
    if lineas[i][2].startswith("- ") or lineas[i][2] == "-":
        return _lista(lineas, i, sangria)
    return _mapa(lineas, i, sangria)


def _mapa(lineas, i, sangria):
    res: dict = {}
    while i < len(lineas):
        s, n, t = lineas[i]
        if s < sangria:
            break
        if s > sangria:
            raise ErrorYaml(f"línea {n}: sangría inesperada")
        if t.startswith("- "):
            raise ErrorYaml(f"línea {n}: elemento de lista donde se esperaba una clave")
        m = _CLAVE.match(t)
        if not m:
            raise ErrorYaml(f"línea {n}: se esperaba `clave: valor`")
        clave, resto = m.group(1), m.group(2)
        if clave in res:
            raise ErrorYaml(f"línea {n}: clave repetida {clave!r}")
        i += 1
        if resto is None or resto == "":
            if i < len(lineas) and (lineas[i][0] > sangria or
                                    (lineas[i][0] == sangria and lineas[i][2].startswith("- "))):
                res[clave], i = _bloque(lineas, i, lineas[i][0])
            else:
                res[clave] = None
        else:
            res[clave] = _escalar(resto, n)
    return res, i


def _lista(lineas, i, sangria):
    res: list = []
    while i < len(lineas):
        s, n, t = lineas[i]
        if s < sangria or not (t.startswith("- ") or t == "-"):
            if s > sangria:
                raise ErrorYaml(f"línea {n}: sangría inesperada")
            break
        if s > sangria:
            raise ErrorYaml(f"línea {n}: sangría inesperada")
        contenido = t[1:].strip()
        if not contenido:
            raise ErrorYaml(f"línea {n}: elemento de lista vacío")
        if _CLAVE.match(contenido):
            # `- clave: valor` abre un mapa cuya sangría es la de la clave
            sub = [(s + 2, n, contenido)]
            j = i + 1
            while j < len(lineas) and lineas[j][0] > s:
                sub.append(lineas[j])
                j += 1
            valor, k = _mapa(sub, 0, s + 2)
            if k != len(sub):
                raise ErrorYaml(f"línea {sub[k][1]}: sangría inesperada")
            res.append(valor)
            i = j
        else:
            res.append(_escalar(contenido, n))
            i += 1
    return res, i
