"""Inventario POR DÍA del portal de PRUEBA (v1.6).

Convive con el inventario por horario de la v1.5 (mismo archivo JSON, mismo candado). Aquí la
unidad de disponibilidad es (sucursal, trámite, fecha): no existe selección de hora.

Estado público de un día (lo que ven la API, los eventos y la pantalla):
  DISPONIBLE     habilitado, con unidades libres y sin vencer
  AGOTADO        habilitado pero sin unidades (o su disponibilidad venció: causa EXPIRACION)
  DESHABILITADO  cerrado administrativamente (sigue en la lista: no es lo mismo que agotado)
  (ausente)      la fecha no está habilitada todavía: el motor la llama NO_HABILITADO

Cada cambio de un día incrementa su `version` y publica un evento con número de secuencia
global (`seq`). Así quien escucha puede: reanudar tras un corte (desde una secuencia), detectar
un hueco (pedir resincronización) y descartar actualizaciones que lleguen fuera de orden
(versión menor o igual a la ya conocida).

Reloj de laboratorio: el portal puede adelantar o acelerar su reloj (`/control/reloj`) para
simular, por ejemplo, una ventana nocturna de liberaciones (22:00-07:30 por defecto) en pocos
minutos. Esa ventana es un PARÁMETRO DE SIMULACIÓN, no describe a ningún proveedor real.
"""
from __future__ import annotations

import random
import secrets
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

DISPONIBLE, AGOTADO, DESHABILITADO = "DISPONIBLE", "AGOTADO", "DESHABILITADO"
MAX_EVENTOS = 5000     # en memoria: tras reiniciar el portal, quien pida algo anterior recibe "resincronizar"


def zona_local(nombre: str | None):
    """Zona IANA indicada o, si no hay, la del sistema (desplazamiento actual)."""
    if nombre:
        return ZoneInfo(nombre)
    return datetime.now().astimezone().tzinfo


def clave_dia(sucursal: str, tramite: str, fecha: str) -> str:
    return f"{sucursal}|{tramite}|{fecha}"


def _hhmm(s: str) -> dtime:
    h, m = s.split(":")
    return dtime(int(h), int(m))


class Reloj:
    """Reloj del laboratorio: real por defecto; se puede fijar, desplazar o acelerar."""

    def __init__(self, d: dict):
        self.d = d.setdefault("reloj", {"base_real": None, "base_lab": None, "factor": 1.0})

    def ahora(self) -> datetime:
        real = datetime.now(timezone.utc)
        if not self.d.get("base_real"):
            return real
        br = datetime.fromisoformat(self.d["base_real"])
        bl = datetime.fromisoformat(self.d["base_lab"])
        return bl + (real - br) * float(self.d.get("factor") or 1.0)

    def configurar(self, fijar: str | None = None, desplazar_s: float | None = None,
                   factor: float | None = None, real: bool = False) -> dict:
        actual = self.ahora()
        if real:
            self.d.update(base_real=None, base_lab=None, factor=1.0)
            return self.estado()
        if fijar:
            actual = datetime.fromisoformat(fijar.replace("Z", "+00:00"))
            if actual.tzinfo is None:
                actual = actual.replace(tzinfo=timezone.utc)
        if desplazar_s:
            actual = actual + timedelta(seconds=float(desplazar_s))
        if factor is not None:
            if not 0 < float(factor) <= 3600:
                raise ValueError("factor fuera de rango (0-3600)")
            self.d["factor"] = float(factor)
        self.d["base_real"] = datetime.now(timezone.utc).isoformat()
        self.d["base_lab"] = actual.isoformat()
        return self.estado()

    def estado(self) -> dict:
        return {"ahora": self.ahora().isoformat().replace("+00:00", "Z"), "factor": float(self.d.get("factor") or 1),
                "simulado": bool(self.d.get("base_real"))}


class InventarioDias:
    """Opera sobre `portal.d` con `portal.lock` (lo llama siempre el Portal)."""

    def __init__(self, portal, zona: str | None = None, horizonte: int = 30, habilitados: int = 15,
                 semilla: int = 5):
        self.p = portal
        self.d = portal.d
        self.tz = zona_local(zona)
        self.zona_nombre = zona or "local"
        self.reloj = Reloj(self.d)
        if "dias" not in self.d:                      # archivo de la v1.5: se añade el inventario diario
            self._sembrar(random.Random(semilla + 1000), horizonte, habilitados)
        self.d.pop("eventos_dias", None)
        self.eventos: list[dict] = []          # no se guarda en disco (sí la secuencia)
        self.d.setdefault("seq_dias", 0)
        self.d.setdefault("ventana", {"inicio": "22:00", "fin": "07:30", "activa": False, "prob": 0.15,
                                      "max_unidades": 2, "vigencia_min": 0})
        self.d.setdefault("eventos_sesiones", {})

    # ------------------------------------------------------------------ tiempo
    def ahora(self) -> datetime:
        return self.reloj.ahora()

    def hoy(self) -> date:
        return self.ahora().astimezone(self.tz).date()

    def en_ventana(self, momento: datetime | None = None) -> bool:
        v = self.d["ventana"]
        t = (momento or self.ahora()).astimezone(self.tz).time()
        ini, fin = _hhmm(v["inicio"]), _hhmm(v["fin"])
        return (ini <= t or t < fin) if ini > fin else (ini <= t < fin)

    # ------------------------------------------------------------------ siembra
    def _sembrar(self, rng: random.Random, horizonte: int, habilitados: int) -> None:
        from .estado import TRAMITES
        hoy = datetime.now(timezone.utc).astimezone(self.tz).date()
        dias = {}
        for sid in sorted(self.d["sucursales"]):
            for tr in sorted(TRAMITES):
                n = 0
                for dd in range(1, horizonte + 1):
                    f = hoy + timedelta(days=dd)
                    if f.weekday() >= 5:
                        continue
                    n += 1
                    if n > habilitados:
                        break
                    cap = rng.choice([1, 2, 2, 3, 4])
                    ocup = cap if rng.random() < 0.35 else rng.randint(0, cap - 1)
                    est = DESHABILITADO if rng.random() < 0.05 else "HABILITADO"
                    dias[clave_dia(sid, tr, f.isoformat())] = {
                        "sucursal": sid, "tramite": tr, "fecha": f.isoformat(), "capacidad": cap, "ocupados": ocup,
                        "estado": est, "version": 1, "disponible_hasta": None, "expirado": False}
        self.d["dias"] = dias

    # ------------------------------------------------------------------ lectura
    def publico(self, k: str) -> dict:
        x = self.d["dias"][k]
        suc = self.d["sucursales"][x["sucursal"]]
        if x["estado"] == DESHABILITADO:
            est, libres = DESHABILITADO, 0
        else:
            libres = 0 if x.get("expirado") else max(0, x["capacidad"] - x["ocupados"])
            est = DISPONIBLE if libres > 0 else AGOTADO
        return {"clave": k, "sucursal": x["sucursal"], "sucursal_nombre": suc["nombre"], "region": suc["region"],
                "tramite": x["tramite"], "fecha": x["fecha"], "estado": est, "libres": libres,
                "capacidad": x["capacidad"], "version": x["version"], "disponible_hasta": x.get("disponible_hasta")}

    def listar(self, region: str | None = None, sucursal: str | None = None, tramite: str | None = None,
               desde: str | None = None, hasta: str | None = None) -> list[dict]:
        hoy = self.hoy().isoformat()
        desde = max(desde or hoy, hoy)            # el pasado nunca se ofrece
        out = []
        for k, x in self.d["dias"].items():
            if x["fecha"] < desde or (hasta and x["fecha"] > hasta):
                continue
            if sucursal and x["sucursal"] != sucursal or tramite and x["tramite"] != tramite:
                continue
            if region and self.d["sucursales"][x["sucursal"]]["region"] != region:
                continue
            out.append(self.publico(k))
        return sorted(out, key=lambda z: (z["sucursal"], z["tramite"], z["fecha"]))

    # ------------------------------------------------------------------ eventos
    def _cambio(self, k: str, causa: str) -> dict:
        x = self.d["dias"][k]
        x["version"] += 1
        self.d["seq_dias"] += 1
        ev = {"seq": self.d["seq_dias"], "ts": self.ahora().isoformat().replace("+00:00", "Z"), "causa": causa,
              "ts_real": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), **self.publico(k)}
        evs = self.eventos
        evs.append(ev)
        if len(evs) > MAX_EVENTOS:
            del evs[: len(evs) - MAX_EVENTOS]
        return ev

    def eventos_desde(self, seq: int) -> tuple[list[dict], bool]:
        """(eventos con seq > `seq`, hay_hueco). Hay hueco si se pide algo ya descartado."""
        evs = self.eventos
        if not evs:
            return [], seq < self.d["seq_dias"]
        hueco = seq < evs[0]["seq"] - 1
        return [e for e in evs if e["seq"] > seq], hueco

    def reemitir_antiguo(self, k: str) -> dict:
        """Laboratorio: publica de nuevo una versión ANTERIOR de un día (llega fuera de orden)."""
        viejo = next((e for e in reversed(self.eventos) if e["clave"] == k
                      and e["version"] < self.d["dias"][k]["version"]), None)
        if viejo is None:
            pub = self.publico(k)
            viejo = {**pub, "version": max(0, pub["version"] - 1), "estado": DISPONIBLE if pub["estado"] != DISPONIBLE
                     else AGOTADO, "causa": "ANTIGUO"}
        self.d["seq_dias"] += 1
        ev = {**viejo, "seq": self.d["seq_dias"], "ts": self.ahora().isoformat().replace("+00:00", "Z"),
              "reenviado": True}
        self.eventos.append(ev)
        return ev

    # ------------------------------------------------------------------ sesiones de eventos
    def abrir_sesion_eventos(self, duracion_s: float) -> dict:
        tok = secrets.token_urlsafe(16)
        vence = datetime.now(timezone.utc) + timedelta(seconds=duracion_s)
        ses = self.d["eventos_sesiones"]
        for t in [t for t, v in ses.items() if datetime.fromisoformat(v) < datetime.now(timezone.utc) - timedelta(hours=1)]:
            del ses[t]
        ses[tok] = vence.isoformat()
        return {"sesion": tok, "vence": vence.isoformat().replace("+00:00", "Z")}

    def sesion_eventos_valida(self, tok: str | None) -> bool:
        v = self.d["eventos_sesiones"].get(tok or "")
        return bool(v) and datetime.fromisoformat(v) > datetime.now(timezone.utc)

    def caducar_sesiones_eventos(self) -> int:
        n = len(self.d["eventos_sesiones"])
        for t in self.d["eventos_sesiones"]:
            self.d["eventos_sesiones"][t] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        return n

    # ------------------------------------------------------------------ cambios (laboratorio y reservas)
    def _existe(self, k: str) -> dict:
        from .estado import ErrorPortal
        x = self.d["dias"].get(k)
        if x is None:
            raise ErrorPortal("DIA_NO_HABILITADO", "Esa fecha no está habilitada.", campo="dia", http=409)
        return x

    def liberar(self, k: str, unidades: int = 1, vigencia_min: float | None = None, causa: str = "LIBERACION") -> dict:
        x = self._existe(k)
        libres = 0 if x.get("expirado") else max(0, x["capacidad"] - x["ocupados"])
        if x.get("expirado"):                      # una liberación nueva reemplaza la vencida
            x["capacidad"], x["expirado"] = x["ocupados"], False
        x["capacidad"] = x["ocupados"] + libres + max(1, int(unidades))
        x["disponible_hasta"] = ((self.ahora() + timedelta(minutes=vigencia_min)).isoformat()
                                 if vigencia_min else None)
        return self._cambio(k, causa)

    def agotar(self, k: str) -> dict:
        x = self._existe(k)
        x["capacidad"] = x["ocupados"]
        return self._cambio(k, "AGOTADO")

    def deshabilitar(self, k: str) -> dict:
        x = self._existe(k)
        x["estado"] = DESHABILITADO
        return self._cambio(k, "DESHABILITADO")

    def rehabilitar(self, k: str) -> dict:
        x = self._existe(k)
        x["estado"] = "HABILITADO"
        return self._cambio(k, "REHABILITADO")

    def habilitar(self, sucursal: str, tramite: str, fecha: str, capacidad: int = 2) -> dict:
        from .estado import ErrorPortal, TRAMITES
        if sucursal not in self.d["sucursales"] or tramite not in TRAMITES:
            raise ErrorPortal("DATOS_INVALIDOS", "Sucursal o trámite inexistente.", http=422)
        date.fromisoformat(fecha)
        k = clave_dia(sucursal, tramite, fecha)
        if k in self.d["dias"]:
            x = self.d["dias"][k]
            x["estado"], x["capacidad"] = "HABILITADO", max(x["capacidad"], x["ocupados"] + int(capacidad))
        else:
            self.d["dias"][k] = {"sucursal": sucursal, "tramite": tramite, "fecha": fecha, "capacidad": int(capacidad),
                                 "ocupados": 0, "estado": "HABILITADO", "version": 0, "disponible_hasta": None,
                                 "expirado": False}
        return self._cambio(k, "NUEVA_FECHA")

    def ocupar(self, k: str) -> dict:
        """Reserva una unidad del día (lo llama la confirmación). Lanza DIA_AGOTADO si no hay."""
        from .estado import ErrorPortal
        x = self._existe(k)
        pub = self.publico(k)
        if pub["estado"] == DESHABILITADO:
            raise ErrorPortal("DIA_DESHABILITADO", "Esa fecha ya no se ofrece.", campo="dia")
        if pub["libres"] <= 0:
            raise ErrorPortal("DIA_AGOTADO", "Ya no quedan lugares para esa fecha. Elige otra.", campo="dia")
        x["ocupados"] += 1
        return self._cambio(k, "RESERVA")

    def desocupar(self, k: str) -> dict | None:
        x = self.d["dias"].get(k)
        if x is None:
            return None
        x["ocupados"] = max(0, x["ocupados"] - 1)
        return self._cambio(k, "CANCELACION")

    # ------------------------------------------------------------------ paso del tiempo
    def tic(self, rng: random.Random) -> list[dict]:
        """Vencimientos y liberaciones de la ventana nocturna simulada. Lo llama una tarea del
        servidor cada medio segundo (y las pruebas, a mano)."""
        out = []
        ahora = self.ahora()
        for k, x in self.d["dias"].items():
            hasta = x.get("disponible_hasta")
            if hasta and not x.get("expirado") and datetime.fromisoformat(hasta) <= ahora:
                x["expirado"] = True
                out.append(self._cambio(k, "EXPIRACION"))
        v = self.d["ventana"]
        if v.get("activa") and self.en_ventana(ahora) and rng.random() < float(v.get("prob", 0.1)):
            hoy = self.hoy().isoformat()
            candidatos = [k for k, x in self.d["dias"].items() if x["fecha"] > hoy and x["estado"] != DESHABILITADO
                          and self.publico(k)["estado"] == AGOTADO]
            if candidatos:
                k = rng.choice(sorted(candidatos))
                out.append(self.liberar(k, rng.randint(1, int(v.get("max_unidades", 2))),
                                        float(v.get("vigencia_min") or 0) or None, causa="LIBERACION_NOCTURNA"))
        return out
