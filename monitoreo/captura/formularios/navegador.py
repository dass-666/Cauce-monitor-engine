"""Navegador HTTP del motor de formularios (v1.5).

Recorre las pantallas del portal de PRUEBA siguiendo un `Contrato`: lee el HTML, comprueba que
estén los campos que el contrato espera, los llena, envía y reconoce el resultado por los
marcadores del contrato (`data-pantalla`, `data-error`, `data-error-general`, `data-folio`).

Sin dependencias nuevas: httpx + `html.parser` de la biblioteca estándar. No ejecuta JavaScript,
no imita navegadores ni oculta su identidad: se presenta con el User-Agent del motor.

Si el portal no coincide con el contrato (un campo cambió de nombre, falta una opción), el
navegador NO adivina: se detiene con `RevisionRequerida` para que una persona lo revise.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx

from ...connectors.http_api import USER_AGENT, check_url
from .contrato import Contrato

COOKIE_SESION = "portal_sesion"


# ---------------------------------------------------------------- errores (qué puede hacer el motor)
class ErrorFormulario(Exception):
    tipo = "ERROR"

    def __init__(self, codigo: str, mensaje: str, errores: dict | None = None, http: int | None = None):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.errores, self.http = codigo, mensaje, errores or {}, http

    def como_dict(self) -> dict:
        return {"tipo": self.tipo, "codigo": self.codigo, "mensaje": self.mensaje, "errores": self.errores,
                "http": self.http}


class RevisionRequerida(ErrorFormulario):
    """El portal no coincide con el contrato: hace falta una persona (no se adivina)."""
    tipo = "REVISION"


class DatosRechazados(ErrorFormulario):
    """El portal rechazó los datos (campos con error). Se corrigen y se reintenta."""
    tipo = "DATOS"


class SesionCaducada(ErrorFormulario):
    tipo = "SESION"


class Rechazo(ErrorFormulario):
    """Rechazo de negocio definitivo para este intento (horario agotado, duplicada...)."""
    tipo = "RECHAZO"


class ResultadoIncierto(ErrorFormulario):
    """Se perdió la respuesta (corte, tiempo agotado, error 5xx): no se sabe si el portal hizo
    la operación. Nunca se da por buena ni por fallida: se verifica por la API."""
    tipo = "INCIERTO"


# ---------------------------------------------------------------- lectura de HTML
@dataclass
class Formulario:
    nombre: str
    accion: str
    campos: dict[str, dict] = field(default_factory=dict)       # nombre -> {tipo, valor, opciones}
    radios: dict[str, list[dict]] = field(default_factory=dict)  # nombre -> [{valor, data-*}]


@dataclass
class Pagina:
    status: int
    pantalla: str | None = None
    formularios: dict[str, Formulario] = field(default_factory=dict)
    errores: dict[str, str] = field(default_factory=dict)
    error_general: tuple[str, str] | None = None
    folio: str | None = None
    resumen: dict[str, str] = field(default_factory=dict)
    agotados: list[str] = field(default_factory=list)
    ubicacion: str | None = None
    dias_no_disponibles: list[dict] = field(default_factory=list)   # v1.6: [{fecha, estado}]
    mes: str | None = None


class _Lector(HTMLParser):
    def __init__(self, marcadores: dict):
        super().__init__(convert_charrefs=True)
        self.m = marcadores
        self.p = Pagina(status=0)
        self.form: Formulario | None = None
        self.select: str | None = None
        self.option: dict | None = None
        self.captura: tuple[str, str] | None = None     # (clase, clave) del texto que se está leyendo
        self.texto: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self.m["pantalla"] in a and tag == "body":
            self.p.pantalla = a[self.m["pantalla"]]
        if tag == "form":
            self.form = Formulario(a.get("data-formulario", ""), a.get("action", ""))
            self.p.formularios[self.form.nombre] = self.form
        elif tag == "input" and self.form is not None and a.get("name"):
            t = a.get("type", "text")
            if t == "radio":
                self.form.radios.setdefault(a["name"], []).append(
                    {"valor": a.get("value", ""), **{k[5:]: v for k, v in a.items() if k.startswith("data-")}})
            else:
                self.form.campos[a["name"]] = {"tipo": t, "valor": a.get("value", ""),
                                               **{k: v for k, v in a.items() if k.startswith("data-")},
                                               **({"readonly": True} if "readonly" in a else {})}
        elif tag == "select" and self.form is not None and a.get("name"):
            self.select = a["name"]
            self.form.campos[self.select] = {"tipo": "select", "valor": "", "opciones": []}
        elif tag == "option" and self.select:
            self.option = {"valor": a.get("value", ""), "seleccionada": "selected" in a}
            self.texto = []
        if self.m["error_campo"] in a:
            self.captura, self.texto = ("error", a[self.m["error_campo"]]), []
        elif self.m["error_general"] in a:
            self.captura, self.texto = ("general", a[self.m["error_general"]]), []
        elif "data-campo" in a:
            self.captura, self.texto = ("resumen", a["data-campo"]), []
        if "data-folio" in a:
            self.p.folio = a["data-folio"]
        if "data-agotado" in a:
            self.p.agotados.append(a["data-agotado"])
        if "data-dia" in a and "data-estado" in a:
            self.p.dias_no_disponibles.append({"fecha": a["data-dia"], "estado": a["data-estado"]})
        if "data-mes" in a:
            self.p.mes = a["data-mes"]

    def handle_data(self, data):
        if self.captura or self.option is not None:
            self.texto.append(data)

    def handle_endtag(self, tag):
        txt = " ".join("".join(self.texto).split())
        if tag == "option" and self.option is not None and self.select:
            c = self.form.campos[self.select]
            c["opciones"].append(self.option["valor"])
            if self.option["seleccionada"]:
                c["valor"] = self.option["valor"]
            self.option = None
            return
        if tag == "select":
            self.select = None
        elif tag == "form":
            self.form = None
        if self.captura and tag in ("span", "p", "dd", "div"):
            clase, clave = self.captura
            if clase == "error":
                self.p.errores[clave] = txt
            elif clase == "general":
                self.p.error_general = (clave, txt)
            else:
                self.p.resumen[clave] = txt
            self.captura = None


def leer(html: str, status: int, marcadores: dict, ubicacion: str | None = None) -> Pagina:
    lec = _Lector(marcadores)
    lec.feed(html)
    lec.p.status, lec.p.ubicacion = status, ubicacion
    return lec.p


# ---------------------------------------------------------------- navegador
class NavegadorHTTP:
    def __init__(self, base: str, contrato: Contrato, token_api: str | None = None, sesion: str | None = None,
                 timeout: float = 10, allowed_hosts=None, client: httpx.Client | None = None):
        check_url(base, allowed_hosts)
        self.base, self.contrato, self.token_api = base.rstrip("/"), contrato, token_api
        self.m = contrato.marcadores
        host = urlparse(self.base).hostname
        self.http = client or httpx.Client(timeout=timeout, follow_redirects=False, trust_env=False,
                                           headers={"User-Agent": USER_AGENT})
        self._propio = client is None
        if sesion:
            self.http.cookies.set(COOKIE_SESION, sesion, domain=host)
        self._host = host

    def cerrar(self) -> None:
        if self._propio:
            self.http.close()

    @property
    def sesion(self) -> str | None:
        for c in self.http.cookies.jar:
            if c.name == COOKIE_SESION:
                return c.value
        return None

    # -------------------------------------------------------------- transporte
    def _pedir(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        try:
            r = self.http.request(metodo, self.base + ruta, **kw)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise ResultadoIncierto("RED", f"se perdió la comunicación con el portal ({e.__class__.__name__})") from e
        if r.status_code >= 500:
            raise ResultadoIncierto("ERROR_INTERNO", f"el portal respondió HTTP {r.status_code}", http=r.status_code)
        return r

    def _pagina(self, r: httpx.Response) -> Pagina:
        p = leer(r.text, r.status_code, self.m, r.headers.get("location"))
        if r.status_code == 440 or (p.error_general and p.error_general[0] in ("SESION_CADUCADA", "SESION_INEXISTENTE")):
            raise SesionCaducada(p.error_general[0] if p.error_general else "SESION_CADUCADA",
                                 p.error_general[1] if p.error_general else "La sesión del portal caducó", http=r.status_code)
        return p

    def _get(self, ruta: str, params: dict | None = None) -> Pagina:
        r = self._pedir("GET", ruta, params=params)
        if r.status_code in (302, 303) and r.headers.get("location"):
            loc = r.headers["location"]
            if loc.split("?")[0] == self.contrato.pantalla("datos")["url"]:
                raise SesionCaducada("PASO_INVALIDO", "El portal volvió al inicio: hay que empezar de nuevo", http=r.status_code)
            return self._get(loc)
        return self._pagina(r)

    def _esperar_pantalla(self, p: Pagina, pantalla: str) -> None:
        esperado = self.contrato.pantalla(pantalla)["marcador"]
        if p.pantalla != esperado:
            raise RevisionRequerida("PANTALLA_INESPERADA", f"se esperaba la pantalla '{esperado}' y llegó '{p.pantalla}'")

    # -------------------------------------------------------------- pantalla 1
    def abrir_datos(self) -> Formulario:
        cfg = self.contrato.pantalla("datos")
        p = self._get(cfg["url"])
        self._esperar_pantalla(p, "datos")
        f = p.formularios.get(cfg["formulario"])
        if f is None:
            raise RevisionRequerida("FORMULARIO_AUSENTE", "no se encontró el formulario de datos")
        faltan = [c.nombre for c in self.contrato.campos if c.nombre not in f.campos]
        if faltan:
            extra = sorted(set(f.campos) - {c.nombre for c in self.contrato.campos})
            raise RevisionRequerida("CAMPOS_NO_COINCIDEN",
                                    "El formulario del portal no coincide con el contrato: revisa los campos",
                                    errores={**{c: "no está en el portal" for c in faltan},
                                             **{c: "campo nuevo no previsto" for c in extra}})
        return f

    def enviar_datos(self, valores: dict[str, str | None]) -> None:
        cfg = self.contrato.pantalla("datos")
        f = self.abrir_datos()
        from .contrato import requerido
        errores = {}
        for c in self.contrato.campos:
            if c.modo == "SOLO_LECTURA":
                continue
            v = valores.get(c.nombre)
            if v is None and requerido(c, valores):
                errores[c.nombre] = "Captura manual pendiente" if c.manual else "Falta el dato en el expediente"
            elif c.tipo == "opcion" and v is not None and v not in f.campos[c.nombre].get("opciones", []):
                errores[c.nombre] = "El portal no ofrece esa opción"
        if errores:
            raise RevisionRequerida("DATOS_INCOMPLETOS", "Hay campos que no se pueden llenar", errores=errores)
        datos = {}
        for c in self.contrato.campos:
            if c.modo == "SOLO_LECTURA":
                continue
            v = valores.get(c.nombre)
            if c.tipo == "casilla":
                if v:                         # una casilla sin marcar no se envía (como en un navegador)
                    datos[c.nombre] = f.campos[c.nombre].get("valor") or "si"
            else:
                datos[c.nombre] = v or ""
        r = self._pedir("POST", cfg["envio"], data=datos)
        if r.status_code in (302, 303) and r.headers.get("location", "").startswith(cfg["exito"]["redirige_a"]):
            return
        p = self._pagina(r)
        if p.errores:
            raise DatosRechazados("DATOS_INVALIDOS", "El portal rechazó algunos campos", errores=p.errores,
                                  http=r.status_code)
        cod, msg = p.error_general or ("RESPUESTA_INESPERADA", f"HTTP {r.status_code}")
        raise Rechazo(cod, msg, http=r.status_code)

    # -------------------------------------------------------------- pantalla 2
    def calendario(self, filtros: dict[str, str | None]) -> dict:
        cfg = self.contrato.pantalla("disponibilidad")
        params = {}
        for campo, regla in cfg["filtros"].items():
            v = filtros.get(regla["origen"])
            if v:
                params[campo] = v.lower() if regla.get("transformar") == "minusculas" else v
        p = self._get(cfg["url"], params)
        self._esperar_pantalla(p, "disponibilidad")
        f = p.formularios.get(cfg["formulario"])
        horarios = (f.radios.get(cfg["opcion_horario"], []) if f else [])
        return {"horarios": [{"id": h["valor"], "fecha": h.get("fecha"), "hora": h.get("hora"),
                              "sucursal": h.get("sucursal")} for h in horarios],
                "agotados": p.agotados, "filtros": params}

    def seleccionar(self, horario_id: str, filtros: dict[str, str | None]) -> dict:
        cfg = self.contrato.pantalla("disponibilidad")
        datos = {cfg["opcion_horario"]: horario_id}
        for campo, regla in cfg["filtros"].items():
            v = filtros.get(regla["origen"]) or ""
            datos[campo] = v.lower() if regla.get("transformar") == "minusculas" else v
        r = self._pedir("POST", cfg["envio"], data=datos)
        if not (r.status_code in (302, 303) and r.headers.get("location", "").startswith(cfg["exito"]["redirige_a"])):
            p = self._pagina(r)
            cod, msg = p.error_general or ("RESPUESTA_INESPERADA", f"HTTP {r.status_code}")
            raise Rechazo(cod, msg, errores=p.errores, http=r.status_code)
        return self.leer_confirmacion()

    # -------------------------------------------------------------- pantalla 3
    def leer_confirmacion(self) -> dict:
        cfg = self.contrato.pantalla("confirmacion")
        p = self._get(cfg["url"])
        self._esperar_pantalla(p, "confirmacion")
        f = p.formularios.get(cfg["formulario"])
        if f is None or cfg["clave"] not in f.campos:
            raise Rechazo("SIN_RETENCION", "El portal no muestra un horario apartado para confirmar")
        return {"clave": f.campos[cfg["clave"]]["valor"], "resumen": p.resumen}

    def confirmar(self, clave: str) -> str:
        """Envía la confirmación. Devuelve el folio que indica la redirección. El folio de la
        redirección NO es evidencia suficiente: quien llama verifica por la API."""
        cfg = self.contrato.pantalla("confirmacion")
        r = self._pedir("POST", cfg["envio"], data={cfg["clave"]: clave})
        loc = r.headers.get("location", "")
        if r.status_code in (302, 303) and loc.startswith(cfg["exito"]["redirige_prefijo"]):
            return loc[len(cfg["exito"]["redirige_prefijo"]):]
        p = self._pagina(r)
        if p.error_general is None:
            # Ni éxito reconocible ni error reconocible: NO se sabe qué pasó. Se verifica por la API.
            raise ResultadoIncierto("RESPUESTA_INESPERADA", f"respuesta no reconocida al confirmar (HTTP {r.status_code})",
                                    http=r.status_code)
        raise Rechazo(p.error_general[0], p.error_general[1], http=r.status_code)

    # -------------------------------------------------------------- v1.6: pantalla 2 POR DÍA
    def calendario_dia(self, filtros: dict[str, str | None]) -> dict:
        """Lee la pantalla 2 (ficha de solo lectura + calendario mensual). No elige nada."""
        cfg = self.contrato.pantalla("calendario")
        params = {}
        for campo, regla in cfg["filtros"].items():
            v = filtros.get(regla["origen"])
            if v:
                params[campo] = v.lower() if regla.get("transformar") == "minusculas" else v
        p = self._get(cfg["url"], params)
        self._esperar_pantalla(p, "calendario")
        f = p.formularios.get(cfg["formulario"])
        solo = {}
        for regla in cfg.get("solo_lectura", []):
            if regla.get("atributo") and f is not None and regla["campo"] in f.campos:
                solo[regla["campo"]] = f.campos[regla["campo"]].get(regla["atributo"])
            else:
                solo[regla["campo"]] = p.resumen.get(regla["campo"])
        dias = []
        if f is not None:
            for x in f.radios.get(cfg["opcion_dia"], []):
                dias.append({"fecha": x["valor"], "estado": x.get("estado", "DISPONIBLE"),
                             "libres": int(x["libres"]) if str(x.get("libres", "")).isdigit() else None,
                             "version": int(x["version"]) if str(x.get("version", "")).isdigit() else None})
        dias += [{"fecha": d["fecha"], "estado": d["estado"], "libres": 0, "version": None}
                 for d in p.dias_no_disponibles]
        clave = f.campos.get(cfg["clave"], {}).get("valor") if f is not None else None
        return {"dias": sorted(dias, key=lambda d: d["fecha"]), "mes": p.mes, "clave": clave, "solo_lectura": solo,
                "ficha": {k: v for k, v in p.resumen.items() if k.startswith("ficha_")}, "filtros": params,
                "sucursal": params.get("sucursal")}

    def verificar_solo_lectura(self, cal: dict, exp) -> None:
        """Los campos de SOLO LECTURA del portal deben coincidir con el expediente; si no, se para."""
        dif = {}
        for regla in self.contrato.pantalla("calendario").get("solo_lectura", []):
            esperado = getattr(exp, regla["origen"], None)
            visto = cal["solo_lectura"].get(regla["campo"])
            if esperado is not None and str(visto or "").strip().upper() != str(esperado).strip().upper():
                dif[regla["campo"]] = "El portal muestra un dato distinto al del expediente"
        if dif:
            raise RevisionRequerida("SOLO_LECTURA_NO_COINCIDE", "La ficha del portal no coincide con el expediente",
                                    errores=dif)

    def continuar_dia(self, clave: str, sucursal: str, fecha: str, extra: dict | None = None) -> str:
        """Envía el día elegido (botón Continuar). Devuelve la llave del resultado; NO es evidencia."""
        cfg = self.contrato.pantalla("calendario")
        datos = {cfg["clave"]: clave, cfg["opcion_dia"]: fecha, "sucursal": sucursal, **(extra or {})}
        r = self._pedir("POST", cfg["envio"], data=datos)
        loc = r.headers.get("location", "")
        if r.status_code in (302, 303) and loc.startswith(cfg["exito"]["redirige_prefijo"]):
            from urllib.parse import unquote
            return unquote(loc[len(cfg["exito"]["redirige_prefijo"]):])
        p = self._pagina(r)
        if p.error_general is None:
            raise ResultadoIncierto("RESPUESTA_INESPERADA", f"respuesta no reconocida al continuar (HTTP {r.status_code})",
                                    http=r.status_code)
        raise Rechazo(p.error_general[0], p.error_general[1], errores=p.errores, http=r.status_code)

    # -------------------------------------------------------------- API de verificación (evidencia)
    def _api(self, metodo: str, ruta: str, **kw) -> httpx.Response:
        h = {"Authorization": f"Bearer {self.token_api}"} if self.token_api else {}
        return self._pedir(metodo, ruta, headers=h, **kw)

    def verificar_folio(self, folio: str) -> dict | None:
        r = self._api("GET", self.contrato.verificacion["por_folio"].format(folio=folio))
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ResultadoIncierto("VERIFICACION", f"no se pudo verificar el folio (HTTP {r.status_code})")
        return r.json()

    def buscar_por_clave(self, clave: str) -> dict | None:
        r = self._api("GET", self.contrato.verificacion["por_clave"], params={"clave": clave})
        if r.status_code != 200:
            raise ResultadoIncierto("VERIFICACION", f"no se pudo consultar la clave (HTTP {r.status_code})")
        return r.json().get("reservacion")

    def cancelar(self, folio: str) -> dict:
        r = self._api("POST", self.contrato.verificacion["cancelar"].format(folio=folio))
        if r.status_code != 200:
            raise Rechazo("CANCELACION", f"el portal no canceló (HTTP {r.status_code})", http=r.status_code)
        return r.json()
