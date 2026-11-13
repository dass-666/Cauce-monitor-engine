"""Prueba real de recepción con UN buzón autorizado de Outlook.com.

Uso (en el equipo de quien hace la prueba, no en el servidor):

    set CAUCE_MS_CLIENT_ID=<Id. de aplicación (cliente) de Azure>
    python prueba_buzon.py --direccion buzon.autorizado@outlook.com

1. Muestra un código; el DUEÑO del buzón abre https://microsoft.com/devicelogin,
   inicia sesión con su propia contraseña (este programa nunca la ve) y acepta
   el permiso "Leer su correo".
2. Lee la Bandeja de entrada (solo metadatos: fecha, remitente, asunto).
3. Pide enviar un correo con un asunto único y espera a verlo llegar por
   sincronización incremental (delta). Mide la demora.
4. Escribe evidencia en estado_prueba/<buzón>/evidencia.json (sin tokens).

Salida 0 = el correo de prueba se recibió. Distinto de 0 = no se verificó.
"""
from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import receptor_graph as rg  # noqa: E402


def ahora() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def esperar_correo(receptor: rg.ReceptorGraph, delta_link: str, marca: str, minutos: float,
                   intervalo: float = 30.0, dormir=time.sleep, reloj=time.monotonic):
    """Sincroniza en incremental hasta ver un mensaje cuyo asunto contiene `marca`.
    Devuelve (mensaje|None, ultimo_estado, delta_link, pasadas)."""
    limite = reloj() + minutos * 60
    pasadas = 0
    estado = rg.OK
    while reloj() < limite:
        pasadas += 1
        r = receptor.sincronizar(delta_link)
        estado = r.estado
        if r.estado == rg.OK:
            if r.delta_link:
                delta_link = r.delta_link
            for m in r.mensajes:
                if marca in m.asunto:
                    return m, estado, delta_link, pasadas
            espera = intervalo
        elif r.estado == rg.LIMITADO:
            espera = max(intervalo, float(r.reintentar_en or 60))
        else:
            return None, estado, delta_link, pasadas  # fallo no transitorio: no insistir
        dormir(min(espera, max(0.0, limite - reloj())))
    return None, estado, delta_link, pasadas


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direccion", required=True, help="buzón autorizado por su dueño")
    ap.add_argument("--client-id", default=os.environ.get("CAUCE_MS_CLIENT_ID"))
    ap.add_argument("--minutos", type=float, default=10.0, help="espera máxima del correo de prueba")
    ap.add_argument("--sin-espera", action="store_true", help="solo autorizar y leer la bandeja")
    ap.add_argument("--carpeta", default=os.path.join(os.path.dirname(os.path.abspath(__file__)), "estado_prueba"))
    a = ap.parse_args(argv)
    if not a.client_id:
        print("Falta CAUCE_MS_CLIENT_ID (Id. de aplicación de Azure). Vea LEEME.md, paso 1.")
        return 2

    carpeta = os.path.join(a.carpeta, rg.normalizar(a.direccion).replace("@", "_at_"))
    os.makedirs(carpeta, exist_ok=True)
    evid = {"buzon": rg.normalizar(a.direccion), "inicio": ahora(), "pasos": []}

    def paso(nombre, ok, **extra):
        evid["pasos"].append({"paso": nombre, "ok": ok, "hora": ahora(), **extra})
        print(f"[{'OK' if ok else 'FALLO'}] {nombre} {extra if extra else ''}")

    auth = rg.AutenticadorMSAL(a.client_id, a.direccion, os.path.join(carpeta, "token_cache.json"))
    receptor = rg.ReceptorGraph(auth.obtener_token, rg.http_get_requests)

    r = receptor.sincronizar(None)
    paso("autorizar_y_leer_bandeja", r.estado == rg.OK, estado=r.estado, detalle=r.detalle,
         mensajes_en_bandeja=len(r.mensajes))
    codigo = 0
    if r.estado == rg.OK:
        for m in sorted(r.mensajes, key=lambda m: m.recibido, reverse=True)[:5]:
            print(f"   {m.recibido}  {m.remitente:<35.35}  {m.asunto[:60]}")
        if not r.delta_link:
            paso("obtener_delta_link", False, detalle=r.detalle or "sin deltaLink")
            codigo = 1
        elif not a.sin_espera:
            marca = "CAUCE-PRUEBA-" + secrets.token_hex(4).upper()
            print(f"\nEnvíe AHORA, desde cualquier otra cuenta, un correo a {a.direccion}\n"
                  f"con este asunto exacto:  {marca}\nEsperando hasta {a.minutos:g} min…")
            t0 = time.monotonic()
            m, estado, _, pasadas = esperar_correo(receptor, r.delta_link, marca, a.minutos)
            if m:
                paso("recibir_correo_de_prueba", True, segundos=round(time.monotonic() - t0),
                     pasadas=pasadas, recibido=m.recibido, internet_message_id=m.internet_message_id)
            else:
                paso("recibir_correo_de_prueba", False, estado=estado, pasadas=pasadas,
                     nota="revise la carpeta de correo no deseado: esta prueba solo lee la Bandeja de entrada")
                codigo = 1
    else:
        codigo = 1

    evid["fin"] = ahora()
    with open(os.path.join(carpeta, "evidencia.json"), "w", encoding="utf-8") as f:
        json.dump(evid, f, ensure_ascii=False, indent=2)
    print(f"\nEvidencia: {os.path.join(carpeta, 'evidencia.json')}")
    return codigo


if __name__ == "__main__":
    sys.exit(main())
