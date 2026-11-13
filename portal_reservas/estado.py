"""Estado del PORTAL DE RESERVACIONES DE PRUEBA (v1.5).

Independiente del motor: no importa nada de `monitoreo` y guarda sus datos en su propio archivo
JSON. Todo es ficticio: registros previos sintéticos, sucursales inventadas, horarios y folios de
laboratorio. No representa a ningún organismo ni servicio real.

Modelo
- registros: identificadores de "registro previo" sintéticos con su fecha de nacimiento y trámite.
- sucursales: por región.
- horarios: (sucursal, fecha, hora, capacidad, ocupados).
- retenciones: reserva temporal de un horario por una sesión; vence a los `retencion_s` segundos.
- reservaciones: confirmadas (folio único) o canceladas. Cada confirmación lleva una clave de
  idempotencia: repetirla devuelve la misma reservación, nunca otra.
- Un identificador no puede tener dos reservaciones CONFIRMADAS del mismo trámite (duplicada).
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import secrets
import threading
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REGIONES = {"norte": "Norte", "sur": "Sur", "este": "Este", "oeste": "Oeste", "centro": "Centro"}
TRAMITES = {"alta": "Alta de registro (ficticio)", "renovacion": "Renovación (ficticia)",
            "constancia": "Constancia (ficticia)"}
OPERACIONES = {"nueva": "Nueva cita", "reprogramar": "Reprogramar"}
NOMBRES_SUC = ["Plaza Aurora", "Centro Brisa", "Módulo Atlas", "Oficina Prisma", "Módulo Ceiba"]
HORAS = ["09:00", "09:30", "10:00", "10:30", "11:00", "11:30", "12:00", "12:30", "13:00"]


class ErrorPortal(Exception):
    """Error de negocio con código estable (lo usa la API y el formulario)."""

    def __init__(self, codigo: str, mensaje: str, campo: str | None = None, http: int = 409):
        super().__init__(mensaje)
        self.codigo, self.mensaje, self.campo, self.http = codigo, mensaje, campo, http


def ahora() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime | None) -> str | None:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z") if dt else None


def parse(s: str | None) -> datetime | None:
    return datetime.fromisoformat(s.replace("Z", "+00:00")) if s else None


def _huella(*partes: str) -> str:
    return hashlib.sha256("|".join(partes).encode()).hexdigest()[:16]


class Portal:
    def __init__(self, ruta: Path, semilla: int = 5, dias: int = 10, retencion_s: float = 300,
                 sesion_s: float = 900, zona: str | None = None, horizonte_dias: int = 30,
                 dias_habilitados: int = 15):
        self.ruta = Path(ruta)
        self.lock = threading.RLock()
        self.retencion_s, self.sesion_s = retencion_s, sesion_s
        if self.ruta.exists():
            self.d = json.loads(self.ruta.read_text(encoding="utf-8"))
        else:
            self.d = self._sembrar(random.Random(semilla), dias)
        # v1.6: inventario POR DÍA (se añade también a un archivo de la v1.5)
        from .dias import InventarioDias
        self.dias = InventarioDias(self, zona=zona, horizonte=horizonte_dias, habilitados=dias_habilitados,
                                   semilla=semilla)
        self.repetir_folio = False          # fallo de laboratorio: el portal repite un folio ya emitido
        self._guardar()

    # ------------------------------------------------------------------ persistencia
    def _guardar(self) -> None:
        tmp = self.ruta.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.d, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, self.ruta)   # escritura atómica: un corte no deja el archivo a medias

    def _sembrar(self, rng: random.Random, dias: int) -> dict:
        hoy = date.today()
        sucursales, horarios = {}, {}
        n = 0
        for reg in REGIONES:
            for k in range(2):
                sid = f"{reg[:3].upper()}-{k + 1}"
                sucursales[sid] = {"id": sid, "region": reg, "nombre": f"{NOMBRES_SUC[(n + k) % 5]} {REGIONES[reg]}"}
                for dd in range(1, dias + 1):
                    dia = hoy + timedelta(days=dd)
                    if dia.weekday() >= 5:
                        continue
                    for h in HORAS:
                        hid = f"{sid}-{dia.isoformat()}-{h.replace(':', '')}"
                        horarios[hid] = {"id": hid, "sucursal": sid, "fecha": dia.isoformat(), "hora": h,
                                         "capacidad": 1, "ocupados": 0 if rng.random() < 0.6 else 1,
                                         "tramites": sorted(TRAMITES)}
            n += 1
        registros = {}
        for i in range(1, 301):
            ident = f"REG{i:06d}"
            nac = date(1960, 1, 1) + timedelta(days=rng.randint(0, 15000))
            registros[ident] = {"fecha_nacimiento": nac.isoformat(), "tramite": rng.choice(sorted(TRAMITES))}
        return {"version": 1, "sucursales": sucursales, "horarios": horarios, "registros": registros,
                "retenciones": {}, "reservaciones": {}, "claves": {}, "sesiones": {}, "historial": [],
                "secuencia": 0}

    def _evento(self, tipo: str, **datos) -> None:
        self.d["historial"].append({"ts": iso(ahora()), "tipo": tipo, **datos})
        if len(self.d["historial"]) > 5000:
            del self.d["historial"][:1000]

    # ------------------------------------------------------------------ sesiones
    def nueva_sesion(self) -> str:
        with self.lock:
            tok = secrets.token_urlsafe(16)
            self.d["sesiones"][tok] = {"creada": iso(ahora()), "vence": iso(ahora() + timedelta(seconds=self.sesion_s)),
                                       "paso": 1, "datos": None, "retencion": None, "clave": secrets.token_hex(12)}
            self._limpiar_sesiones()
            self._guardar()
            return tok

    def sesion(self, tok: str | None) -> dict:
        with self.lock:
            s = self.d["sesiones"].get(tok or "")
            if not s:
                raise ErrorPortal("SESION_INEXISTENTE", "La sesión no existe. Empieza de nuevo.", http=440)
            if parse(s["vence"]) <= ahora():
                raise ErrorPortal("SESION_CADUCADA", "La sesión caducó. Empieza de nuevo.", http=440)
            return s

    def caducar_sesiones(self) -> int:
        with self.lock:
            for s in self.d["sesiones"].values():
                s["vence"] = iso(ahora() - timedelta(seconds=1))
            self._guardar()
            return len(self.d["sesiones"])

    def _limpiar_sesiones(self) -> None:
        limite = ahora() - timedelta(hours=1)
        for k in [k for k, s in self.d["sesiones"].items() if parse(s["vence"]) < limite]:
            del self.d["sesiones"][k]

    # ------------------------------------------------------------------ pantalla 1: datos
    def validar_datos(self, tok: str, f: dict) -> dict:
        """Devuelve {campo: mensaje} con TODOS los errores (vacío si es correcto) y guarda los datos."""
        return self._validar(tok, f, modo="horario")

    def validar_datos_dia(self, tok: str, f: dict) -> dict:
        """v1.6 (modo por día): además de los campos comunes, dos campos de CAPTURA MANUAL
        (clave de identidad y aceptación del aviso) y una validación dependiente: reprogramar exige
        el folio anterior, que debe ser una cita confirmada del mismo identificador y trámite.
        La clave de identidad solo se valida: el portal no la guarda (minimización)."""
        import re
        extra: dict[str, str] = {}
        ci = str(f.get("clave_identidad") or "").strip().upper()
        if not ci:
            extra["clave_identidad"] = "Campo obligatorio (captura manual)"
        elif not re.fullmatch(r"[A-Z]{3}[0-9]{6}", ci):
            extra["clave_identidad"] = "Formato: tres letras y seis números (AAA000000)"
        if str(f.get("acepto_privacidad") or "") != "si":
            extra["acepto_privacidad"] = "Debes aceptar el aviso de privacidad"
        fa = str(f.get("folio_anterior") or "").strip().upper()
        if f.get("operacion") == "reprogramar":
            if not fa:
                extra["folio_anterior"] = "Para reprogramar indica el folio de la cita anterior"
            else:
                with self.lock:
                    r = self._reservacion_por_folio(fa)
                ident = str(f.get("identificador") or "").strip().upper()
                if r is None or r["estado"] != "CONFIRMADA":
                    extra["folio_anterior"] = "No existe una cita confirmada con ese folio"
                elif r["identificador"] != ident or r["tramite"] != f.get("tramite"):
                    extra["folio_anterior"] = "Ese folio no corresponde a este registro y trámite"
        elif fa:
            extra["folio_anterior"] = "Solo se indica al reprogramar"
        return self._validar(tok, f, modo="dia", extra=extra, folio_anterior=fa or None)

    def _validar(self, tok: str, f: dict, modo: str, extra: dict | None = None,
                 folio_anterior: str | None = None) -> dict:
        errores: dict[str, str] = dict(extra or {})
        req = ["operacion", "tramite", "identificador", "fecha_nacimiento", "telefono", "telefono_confirmacion",
               "correo", "correo_confirmacion"]
        for c in req:
            if not str(f.get(c) or "").strip():
                errores[c] = "Campo obligatorio"
        if f.get("operacion") and f["operacion"] not in OPERACIONES:
            errores["operacion"] = "Operación no válida"
        if f.get("tramite") and f["tramite"] not in TRAMITES:
            errores["tramite"] = "Trámite no válido"
        tel = "".join(ch for ch in str(f.get("telefono") or "") if ch.isdigit())
        if f.get("telefono") and len(tel) != 10:
            errores["telefono"] = "El teléfono debe tener 10 dígitos"
        if f.get("telefono_confirmacion") and "".join(ch for ch in f["telefono_confirmacion"] if ch.isdigit()) != tel:
            errores["telefono_confirmacion"] = "Los teléfonos no coinciden"
        correo = str(f.get("correo") or "").strip().lower()
        if correo and ("@" not in correo or "." not in correo.split("@")[-1]):
            errores["correo"] = "Correo no válido"
        if f.get("correo_confirmacion") and f["correo_confirmacion"].strip().lower() != correo:
            errores["correo_confirmacion"] = "Los correos no coinciden"
        try:
            if f.get("fecha_nacimiento"):
                date.fromisoformat(f["fecha_nacimiento"])
        except ValueError:
            errores["fecha_nacimiento"] = "Fecha no válida (AAAA-MM-DD)"
        with self.lock:
            s = self.sesion(tok)
            if not errores:
                reg = self.d["registros"].get(str(f["identificador"]).strip().upper())
                if reg is None:
                    errores["identificador"] = "Identificador inexistente"
                elif reg["fecha_nacimiento"] != f["fecha_nacimiento"]:
                    errores["fecha_nacimiento"] = "La fecha no coincide con el registro"
            if not errores:
                s["datos"] = {"operacion": f["operacion"], "tramite": f["tramite"],
                              "identificador": str(f["identificador"]).strip().upper(),
                              # minimización: el portal guarda solo una huella del contacto
                              "contacto": _huella(tel, correo), "telefono_final": tel[-4:],
                              "folio_anterior": folio_anterior}
                s["paso"], s["modo"] = 2, modo
                self._guardar()
            return errores

    def _reservacion_por_folio(self, folio: str) -> dict | None:
        r = self.d["reservaciones"].get(folio)
        if r is None:                         # folio repetido (fallo simulado): se guarda con sufijo
            r = next((x for x in self.d["reservaciones"].values() if x["folio"] == folio), None)
        return r

    # ------------------------------------------------------------------ v1.6: confirmación POR DÍA
    def confirmar_dia(self, tok: str, clave: str, sucursal: str, fecha: str) -> tuple[dict, bool]:
        """Confirma la cita del DÍA elegido (sin hora). Idempotente por clave, como por horario.
        Si es una reprogramación, la cita anterior se cancela en la MISMA operación."""
        from .dias import clave_dia
        with self.lock:
            if clave in self.d["claves"]:
                return self.d["reservaciones"][self.d["claves"][clave]], True
            s = self.sesion(tok)
            if clave != s["clave"]:
                raise ErrorPortal("CLAVE_INVALIDA", "La confirmación no corresponde a esta sesión. Recarga la página.")
            if s.get("modo") != "dia" or s["paso"] < 2 or not s["datos"]:
                raise ErrorPortal("PASO_INVALIDO", "Primero completa tus datos.", http=409)
            datos = s["datos"]
            if not sucursal or sucursal not in self.d["sucursales"]:
                raise ErrorPortal("SUCURSAL_REQUERIDA", "Elige una sucursal.", campo="sucursal", http=422)
            try:
                date.fromisoformat(fecha or "")
            except ValueError:
                raise ErrorPortal("DIA_REQUERIDO", "Elige un día del calendario.", campo="dia", http=422) from None
            if fecha <= self.dias.hoy().isoformat():
                raise ErrorPortal("DIA_NO_HABILITADO", "Esa fecha ya no se puede reservar.", campo="dia")
            anterior = None
            if datos["operacion"] == "reprogramar":
                anterior = self._reservacion_por_folio(datos.get("folio_anterior") or "")
                if anterior is None or anterior["estado"] != "CONFIRMADA":
                    raise ErrorPortal("FOLIO_ANTERIOR_INVALIDO", "La cita anterior ya no está confirmada.")
            else:
                for r in self.d["reservaciones"].values():
                    if (r["identificador"] == datos["identificador"] and r["tramite"] == datos["tramite"]
                            and r["estado"] == "CONFIRMADA"):
                        raise ErrorPortal("RESERVACION_DUPLICADA",
                                          f"Ya existe una cita confirmada para este trámite (folio {r['folio']}).")
            k = clave_dia(sucursal, datos["tramite"], fecha)
            self.dias.ocupar(k)                       # DIA_AGOTADO / DIA_NO_HABILITADO / DIA_DESHABILITADO
            self.d["secuencia"] += 1
            folio = f"DIA-{fecha.replace('-', '')}-{self.d['secuencia']:05d}"
            folio += "ABCDEFGHJKLMNPQRSTUVWXYZ"[int(_huella(folio), 16) % 24]
            llave = folio
            if self.repetir_folio:
                previas = [x["folio"] for x in self.d["reservaciones"].values() if x.get("modo") == "dia"]
                if previas:
                    folio = previas[-1]
                    llave = f"{folio}#{self.d['secuencia']}"
                self.repetir_folio = False
            suc = self.d["sucursales"][sucursal]
            ahora_iso = iso(ahora())
            r = {"folio": folio, "estado": "CONFIRMADA", "clave": clave, "identificador": datos["identificador"],
                 "tramite": datos["tramite"], "operacion": datos["operacion"], "modo": "dia", "dia": k,
                 "sucursal": sucursal, "sucursal_nombre": suc["nombre"], "region": suc["region"], "fecha": fecha,
                 "hora": None, "horario": None, "confirmada_en": ahora_iso, "reprograma": datos.get("folio_anterior"),
                 "historial": [{"ts": ahora_iso, "estado": "CONFIRMADA"}]}
            self.d["reservaciones"][llave] = r
            self.d["claves"][clave] = llave
            if anterior is not None:
                self._cancelar_registro(anterior, "REPROGRAMADA")
            s["paso"], s["folio"] = 4, llave
            s["clave"] = secrets.token_hex(12)
            self._evento("CONFIRMADA", folio=folio, dia=k)
            self._guardar()
            return r, False

    def _cancelar_registro(self, r: dict, estado: str = "CANCELADA") -> None:
        r["estado"] = estado
        r["historial"].append({"ts": iso(ahora()), "estado": estado})
        if r.get("modo") == "dia":
            self.dias.desocupar(r["dia"])
        elif r.get("horario"):
            self.d["horarios"][r["horario"]]["ocupados"] -= 1
        self._evento(estado, folio=r["folio"], horario=r.get("horario"), dia=r.get("dia"))

    # ------------------------------------------------------------------ pantalla 2: disponibilidad
    def disponibilidad(self, region: str | None = None, sucursal: str | None = None, tramite: str | None = None) -> list[dict]:
        with self.lock:
            self._vencer_retenciones()
            out = []
            for h in self.d["horarios"].values():
                suc = self.d["sucursales"][h["sucursal"]]
                if region and suc["region"] != region or sucursal and h["sucursal"] != sucursal:
                    continue
                if tramite and tramite not in h["tramites"]:
                    continue
                out.append({**h, "region": suc["region"], "sucursal_nombre": suc["nombre"],
                            "libres": self._libres(h["id"])})
            return sorted(out, key=lambda x: (x["sucursal"], x["fecha"], x["hora"]))

    def _libres(self, hid: str) -> int:
        h = self.d["horarios"][hid]
        ret = sum(1 for r in self.d["retenciones"].values() if r["horario"] == hid)
        return max(0, h["capacidad"] - h["ocupados"] - ret)

    def _vencer_retenciones(self) -> None:
        t = ahora()
        for rid in [k for k, r in self.d["retenciones"].items() if parse(r["vence"]) <= t]:
            r = self.d["retenciones"].pop(rid)
            self._evento("RETENCION_VENCIDA", horario=r["horario"])
            for s in self.d["sesiones"].values():
                if s.get("retencion") == rid:
                    s["retencion"], s["paso"] = None, 2

    def retener(self, tok: str, hid: str) -> dict:
        with self.lock:
            s = self.sesion(tok)
            if s["paso"] < 2 or not s["datos"]:
                raise ErrorPortal("PASO_INVALIDO", "Primero completa tus datos.", http=409)
            self._vencer_retenciones()
            h = self.d["horarios"].get(hid)
            if h is None:
                raise ErrorPortal("HORARIO_INEXISTENTE", "El horario no existe.", campo="horario", http=404)
            if s["datos"]["tramite"] not in h["tramites"]:
                raise ErrorPortal("TRAMITE_NO_DISPONIBLE", "Ese horario no atiende este trámite.", campo="horario")
            if s.get("retencion"):                     # cambiar de horario libera el anterior
                self.d["retenciones"].pop(s["retencion"], None)
                s["retencion"] = None
            if self._libres(hid) <= 0:
                self._guardar()
                raise ErrorPortal("HORARIO_AGOTADO", "Ese horario ya no está disponible. Elige otro.", campo="horario")
            rid = secrets.token_hex(8)
            vence = ahora() + timedelta(seconds=self.retencion_s)
            self.d["retenciones"][rid] = {"horario": hid, "vence": iso(vence)}
            s["retencion"], s["paso"] = rid, 3
            self._evento("RETENCION", horario=hid)
            self._guardar()
            return {"retencion": rid, "vence": iso(vence), "horario": self._horario_publico(hid)}

    def _horario_publico(self, hid: str) -> dict:
        h = self.d["horarios"][hid]
        suc = self.d["sucursales"][h["sucursal"]]
        return {"id": hid, "sucursal": h["sucursal"], "sucursal_nombre": suc["nombre"], "region": suc["region"],
                "fecha": h["fecha"], "hora": h["hora"]}

    # ------------------------------------------------------------------ pantalla 3: confirmación
    def confirmar(self, tok: str, clave: str) -> tuple[dict, bool]:
        """Confirma la reservación de la sesión. Devuelve (reservación, repetida).
        Idempotente: la misma clave devuelve la misma reservación aunque la sesión ya no exista."""
        with self.lock:
            if clave in self.d["claves"]:
                return self.d["reservaciones"][self.d["claves"][clave]], True
            s = self.sesion(tok)
            if clave != s["clave"]:
                raise ErrorPortal("CLAVE_INVALIDA", "La confirmación no corresponde a esta sesión. Recarga la página.")
            self._vencer_retenciones()
            rid = s.get("retencion")
            if s["paso"] < 3 or not rid or rid not in self.d["retenciones"]:
                raise ErrorPortal("RETENCION_VENCIDA", "Se agotó el tiempo para confirmar ese horario. Elige otro.",
                                  campo="horario")
            datos = s["datos"]
            for r in self.d["reservaciones"].values():
                if (r["identificador"] == datos["identificador"] and r["tramite"] == datos["tramite"]
                        and r["estado"] == "CONFIRMADA"):
                    raise ErrorPortal("RESERVACION_DUPLICADA",
                                      f"Ya existe una cita confirmada para este trámite (folio {r['folio']}).")
            ret = self.d["retenciones"].pop(rid)
            h = self.d["horarios"][ret["horario"]]
            h["ocupados"] += 1
            self.d["secuencia"] += 1
            folio = f"LAB-{h['fecha'].replace('-', '')}-{self.d['secuencia']:05d}"
            folio += "ABCDEFGHJKLMNPQRSTUVWXYZ"[int(_huella(folio), 16) % 24]   # dígito de control
            r = {"folio": folio, "estado": "CONFIRMADA", "clave": clave, "identificador": datos["identificador"],
                 "tramite": datos["tramite"], "operacion": datos["operacion"], **self._horario_publico(ret["horario"]),
                 "horario": ret["horario"], "confirmada_en": iso(ahora()),
                 "historial": [{"ts": iso(ahora()), "estado": "CONFIRMADA"}]}
            self.d["reservaciones"][folio] = r
            self.d["claves"][clave] = folio
            s["paso"], s["retencion"], s["folio"] = 4, None, folio
            s["clave"] = secrets.token_hex(12)          # una clave nueva por cada confirmación posible
            self._evento("CONFIRMADA", folio=folio, horario=ret["horario"])
            self._guardar()
            return r, False

    def reservacion(self, folio: str) -> dict | None:
        with self.lock:
            return self._reservacion_por_folio(folio)

    def reservacion_llave(self, llave: str) -> dict | None:
        with self.lock:
            return self.d["reservaciones"].get(llave)

    def por_clave(self, clave: str) -> dict | None:
        with self.lock:
            f = self.d["claves"].get(clave)
            return self.d["reservaciones"].get(f) if f else None

    def cancelar(self, folio: str) -> dict:
        with self.lock:
            r = self._reservacion_por_folio(folio)
            if r is None:
                raise ErrorPortal("FOLIO_INEXISTENTE", "Folio inexistente.", http=404)
            if r["estado"] in ("CANCELADA", "REPROGRAMADA"):
                return r
            self._cancelar_registro(r, "CANCELADA")
            self._guardar()
            return r

    # ------------------------------------------------------------------ laboratorio
    def agotar(self, hid: str) -> None:
        with self.lock:
            h = self.d["horarios"][hid]
            h["ocupados"] = h["capacidad"]
            self._evento("AGOTADO_LAB", horario=hid)
            self._guardar()

    def liberar(self, hid: str, unidades: int = 1) -> None:
        with self.lock:
            h = self.d["horarios"][hid]
            h["capacidad"] += max(0, unidades - max(0, h["capacidad"] - h["ocupados"]))
            h["ocupados"] = max(0, min(h["ocupados"], h["capacidad"] - unidades))
            self._evento("LIBERADO_LAB", horario=hid)
            self._guardar()

    def resumen(self) -> dict:
        with self.lock:
            self._vencer_retenciones()
            conf = [r for r in self.d["reservaciones"].values() if r["estado"] == "CONFIRMADA"]
            return {"sucursales": len(self.d["sucursales"]), "horarios": len(self.d["horarios"]),
                    "libres": sum(self._libres(h) for h in self.d["horarios"]),
                    "retenciones": len(self.d["retenciones"]), "confirmadas": len(conf),
                    "canceladas": len(self.d["reservaciones"]) - len(conf), "sesiones": len(self.d["sesiones"]),
                    "eventos": len(self.d["historial"]),
                    "dias": len(self.d["dias"]),
                    "dias_disponibles": sum(1 for k in self.d["dias"] if self.dias.publico(k)["estado"] == "DISPONIBLE"),
                    "seq_dias": self.d["seq_dias"], "reloj": self.dias.reloj.estado()}

    def registro_ejemplo(self, n: int = 0) -> tuple[str, dict]:
        k = sorted(self.d["registros"])[n % len(self.d["registros"])]
        return k, self.d["registros"][k]
