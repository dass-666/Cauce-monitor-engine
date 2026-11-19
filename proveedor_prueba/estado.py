"""Estado del proveedor de prueba: regiones, calendario, inventario, reservas y ventanas.

Independiente del motor: no importa nada de `monitoreo` y guarda sus datos en su propio
archivo JSON. Todos los nombres y titulares son ficticios.

Modelo
- Recurso: un turno, mesa, entrada o unidad con fecha (`inicio`/`fin`) y `capacidad`.
- `libres`: unidades libres de forma estable.
- Ventanas: liberaciones de corta duración ({unidades, hasta}); cuentan como disponibles
  hasta que vencen y entonces desaparecen (disponibilidad que expira).
- Reservas: consumen primero ventanas y después libres; cancelarlas devuelve las unidades.
- Cada cambio incrementa la versión de su región y queda en el historial (la "verdad" con la
  que se comprueba qué detectó el motor).
"""
from __future__ import annotations

import json
import os
import random
import threading
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

REGIONES = {"norte": "Norte", "sur": "Sur", "este": "Este", "oeste": "Oeste", "centro": "Centro"}
TIPOS = {"norte": "entrada", "sur": "mesa", "este": "unidad", "oeste": "turno", "centro": "entrada"}
NOMBRES = ["Sala Aurora", "Mesa Terraza", "Kit Alfa", "Turno Consulta", "Foro Atlas", "Taller Brisa",
           "Mesa Jardín", "Kit Delta", "Turno Taller", "Expo Prisma"]


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


@dataclass
class Recurso:
    id: str
    region: str
    nombre: str
    tipo: str
    inicio: str
    fin: str
    capacidad: int
    libres: int = 0
    ventanas: list = field(default_factory=list)   # [{"unidades": n, "hasta": iso}]
    estado: str = "ABIERTO"                        # ABIERTO | CANCELADO
    actualizado_en: str = ""

    def disponibles(self, t: datetime) -> int:
        if self.estado != "ABIERTO" or parse(self.fin) <= t:
            return 0
        return self.libres + sum(v["unidades"] for v in self.ventanas if parse(v["hasta"]) > t)

    def estado_publico(self, t: datetime) -> str:
        if self.estado == "CANCELADO":
            return "CANCELADO"
        return "CERRADO" if parse(self.fin) <= t else "ABIERTO"

    def ventana_mas_proxima(self, t: datetime) -> str | None:
        vivas = [v["hasta"] for v in self.ventanas if parse(v["hasta"]) > t]
        return min(vivas) if vivas else None


class Proveedor:
    def __init__(self, archivo: Path | None, semilla: int = 7, recursos_por_region: int = 12):
        self.archivo = archivo
        self.lock = threading.RLock()
        self.rng = random.Random(semilla)
        self.recursos: dict[str, Recurso] = {}
        self.versiones: dict[str, int] = {r: 1 for r in REGIONES}
        self.reservas: dict[str, dict] = {}
        self.programadas: list[dict] = []
        self.historial: list[dict] = []
        self.secuencia = 0
        self.arranques = 0
        if archivo and archivo.exists():
            self._cargar()
        else:
            self._sembrar(recursos_por_region)
        self.arranques += 1
        self.guardar()

    def reiniciar_datos(self, recursos_por_region: int = 12) -> None:
        """Vuelve al inventario inicial (solo laboratorio: aísla una prueba de la anterior)."""
        with self.lock:
            self.rng.seed(7)
            self.recursos, self.reservas, self.programadas, self.historial = {}, {}, [], []
            self.versiones = {r: 1 for r in REGIONES}
            self.secuencia = 0
            self._sembrar(recursos_por_region)
            self.guardar()

    # ------------------------------------------------------------------ persistencia
    def _sembrar(self, n: int) -> None:
        base = ahora().replace(minute=0, second=0, microsecond=0) + timedelta(days=1)
        for region in REGIONES:
            for k in range(n):
                inicio = base + timedelta(days=k // 4, hours=9 + 2 * (k % 4))
                rid = f"{region[:3].upper()}-{k + 1:03d}"
                self.recursos[rid] = Recurso(
                    id=rid, region=region, nombre=f"{NOMBRES[k % len(NOMBRES)]} {REGIONES[region]} · {k + 1}",
                    tipo=TIPOS[region], inicio=iso(inicio), fin=iso(inicio + timedelta(hours=2)),
                    capacidad=self.rng.choice([2, 4, 6, 8]), libres=0, actualizado_en=iso(ahora()))

    def _cargar(self) -> None:
        d = json.loads(self.archivo.read_text(encoding="utf-8"))
        self.recursos = {k: Recurso(**v) for k, v in d["recursos"].items()}
        self.versiones, self.reservas = d["versiones"], d["reservas"]
        self.programadas, self.historial = d.get("programadas", []), d.get("historial", [])
        self.secuencia, self.arranques = d.get("secuencia", 0), d.get("arranques", 0)

    def guardar(self) -> None:
        """Escritura atómica: un corte a mitad nunca deja el archivo a medias."""
        if not self.archivo:
            return
        with self.lock:
            data = {"recursos": {k: asdict(v) for k, v in self.recursos.items()}, "versiones": self.versiones,
                    "reservas": self.reservas, "programadas": self.programadas,
                    "historial": self.historial[-20000:], "secuencia": self.secuencia, "arranques": self.arranques}
            tmp = self.archivo.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.archivo)

    # ------------------------------------------------------------------ cambios
    def _cambio(self, r: Recurso, antes: int, motivo: str, t: datetime) -> None:
        despues = r.disponibles(t)
        r.actualizado_en = iso(t)
        self.versiones[r.region] += 1
        self.secuencia += 1
        self.historial.append({"n": self.secuencia, "t": iso(t), "region": r.region, "recurso": r.id,
                               "antes": antes, "despues": despues, "estado": r.estado_publico(t), "motivo": motivo,
                               "version": self.versiones[r.region]})

    def liberar(self, rid: str, unidades: int, ventana_s: float | None = None, motivo: str = "liberación") -> dict:
        with self.lock:
            t = ahora()
            r = self.recursos[rid]
            if r.estado != "ABIERTO":
                raise ValueError("recurso no abierto")
            antes = r.disponibles(t)
            ocupadas = r.capacidad - antes
            unidades = max(0, min(unidades, ocupadas))
            if not unidades:
                return {"recurso": rid, "liberadas": 0}
            if ventana_s:
                r.ventanas.append({"unidades": unidades, "hasta": iso(t + timedelta(seconds=ventana_s))})
            else:
                r.libres += unidades
            self._cambio(r, antes, motivo, t)
            return {"recurso": rid, "liberadas": unidades, "disponibles": r.disponibles(t), "n": self.secuencia,
                    "t": iso(t)}

    def reservar(self, rid: str, unidades: int, titular: str) -> dict:
        with self.lock:
            t = ahora()
            r = self.recursos.get(rid)
            if r is None:
                raise KeyError(rid)
            antes = r.disponibles(t)
            if antes < unidades:
                raise ValueError("no hay unidades suficientes")
            resto = unidades
            for v in sorted((v for v in r.ventanas if parse(v["hasta"]) > t), key=lambda v: v["hasta"]):
                tomar = min(resto, v["unidades"])
                v["unidades"] -= tomar
                resto -= tomar
            r.ventanas = [v for v in r.ventanas if v["unidades"] > 0]
            r.libres -= resto
            self.secuencia += 1
            res_id = f"RES-{self.secuencia:06d}"
            self.reservas[res_id] = {"id": res_id, "recurso": rid, "unidades": unidades, "titular": titular,
                                     "creada": iso(t), "estado": "ACTIVA"}
            self._cambio(r, antes, f"reserva {res_id}", t)
            return self.reservas[res_id]

    def cancelar_reserva(self, res_id: str) -> dict:
        with self.lock:
            t = ahora()
            res = self.reservas[res_id]
            if res["estado"] != "ACTIVA":
                return res
            r = self.recursos[res["recurso"]]
            antes = r.disponibles(t)
            res["estado"] = "CANCELADA"
            if r.estado == "ABIERTO":
                r.libres += res["unidades"]
                self._cambio(r, antes, f"cancelación de {res_id}", t)
            return res

    def cancelar_recurso(self, rid: str) -> None:
        with self.lock:
            t = ahora()
            r = self.recursos[rid]
            antes = r.disponibles(t)
            r.estado = "CANCELADO"
            self._cambio(r, antes, "recurso cancelado", t)

    def programar(self, rid: str, unidades: int, en_s: float, ventana_s: float | None) -> dict:
        with self.lock:
            p = {"recurso": rid, "unidades": unidades, "cuando": iso(ahora() + timedelta(seconds=en_s)),
                 "ventana_s": ventana_s}
            self.programadas.append(p)
            return p

    def tick(self) -> int:
        """Ejecuta liberaciones programadas y vencimientos de ventanas. Devuelve los cambios."""
        n = 0
        with self.lock:
            t = ahora()
            pendientes = []
            for p in self.programadas:
                if parse(p["cuando"]) <= t:
                    try:
                        n += bool(self.liberar(p["recurso"], p["unidades"], p["ventana_s"], "liberación programada")
                                  ["liberadas"])
                    except (KeyError, ValueError):
                        pass
                else:
                    pendientes.append(p)
            self.programadas = pendientes
            for r in self.recursos.values():
                vencidas = [v for v in r.ventanas if parse(v["hasta"]) <= t]
                if vencidas:
                    # disponibilidad justo antes de vencer: con las ventanas aún vivas
                    antes = r.libres + sum(v["unidades"] for v in r.ventanas) if r.estado == "ABIERTO" else 0
                    r.ventanas = [v for v in r.ventanas if parse(v["hasta"]) > t]
                    self._cambio(r, antes, "ventana vencida", t)
                    n += 1
        return n

    # ------------------------------------------------------------------ simulación aleatoria
    def paso_aleatorio(self, cfg: dict) -> int:
        """Un paso de actividad ficticia: liberaciones cortas, reservas, cancelaciones y ráfagas."""
        n = 0
        with self.lock:
            abiertos = [r for r in self.recursos.values() if r.estado == "ABIERTO"]
            if not abiertos:
                return 0
            x = self.rng.random()
            t = ahora()
            if x < cfg.get("p_rafaga", 0.03):
                # Recursos que aparecen a la vez en varias regiones
                for region in REGIONES:
                    cand = [r for r in abiertos if r.region == region and r.disponibles(t) < r.capacidad]
                    if cand:
                        n += bool(self.liberar(self.rng.choice(cand).id, self.rng.randint(1, 2),
                                               cfg.get("ventana_s"), "ráfaga simultánea")["liberadas"])
            elif x < 0.50:
                cand = [r for r in abiertos if r.disponibles(t) < r.capacidad]
                if cand:
                    n += bool(self.liberar(self.rng.choice(cand).id, self.rng.randint(1, 2),
                                           cfg.get("ventana_s") if self.rng.random() < 0.6 else None)["liberadas"])
            elif x < 0.85:
                cand = [r for r in abiertos if r.disponibles(t) > 0]
                if cand:
                    r = self.rng.choice(cand)
                    self.reservar(r.id, 1, f"Titular Ficticio {self.rng.randint(1, 999):03d}")
                    n += 1
            elif x < 0.97:
                activas = [k for k, v in self.reservas.items() if v["estado"] == "ACTIVA"]
                if activas:
                    self.cancelar_reserva(self.rng.choice(activas))
                    n += 1
            elif self.rng.random() < cfg.get("p_cancelar_recurso", 0.2) and len(abiertos) > 20:
                self.cancelar_recurso(self.rng.choice(abiertos).id)
                n += 1
        return n

    # ------------------------------------------------------------------ lectura
    def vista(self, region: str) -> tuple[int, list[dict]]:
        with self.lock:
            t = ahora()
            items = [{"id": r.id, "nombre": r.nombre, "tipo": r.tipo, "inicio": r.inicio, "fin": r.fin,
                      "capacidad": r.capacidad, "disponibles": r.disponibles(t), "estado": r.estado_publico(t),
                      "disponible_hasta": r.ventana_mas_proxima(t),
                      "ventanas": [{"unidades": v["unidades"], "hasta": v["hasta"]} for v in r.ventanas
                                   if parse(v["hasta"]) > t] if r.estado == "ABIERTO" else [],
                      "actualizado_en": r.actualizado_en}
                     for r in sorted(self.recursos.values(), key=lambda r: r.id) if r.region == region]
            return self.versiones[region], items


def reloj() -> float:
    return time.monotonic()
