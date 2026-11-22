"""Cierra «a mano» las ventanas de unos procesos, como si una persona pulsara la X (solo pruebas de laboratorio).

- Windows: envía WM_CLOSE a las ventanas visibles de nivel superior de esos procesos (lo mismo que la X).
- Linux/X11: envía WM_DELETE_WINDOW (protocolo del gestor de ventanas) a las ventanas con _NET_WM_PID de esos
  procesos. Necesita python-xlib y DISPLAY.
Devuelve cuántas ventanas recibieron la orden. No mata procesos: el programa decide qué hacer al cerrarse.
"""
from __future__ import annotations

import os
import sys


def cerrar(pids: set[int]) -> int:
    if sys.platform == "win32":
        return _cerrar_windows(pids)
    return _cerrar_x11(pids)


def _cerrar_windows(pids: set[int]) -> int:
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.WinDLL("user32", use_last_error=True)
    WM_CLOSE = 0x0010
    encontradas: list[int] = []
    PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def cada(hwnd, _):
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value in pids and user32.IsWindowVisible(hwnd) and not user32.GetWindow(hwnd, 4):  # 4 = GW_OWNER
            encontradas.append(hwnd)
        return True

    user32.EnumWindows(PROC(cada), 0)
    for h in encontradas:
        user32.PostMessageW(h, WM_CLOSE, 0, 0)
    return len(encontradas)


def _cerrar_x11(pids: set[int]) -> int:
    from Xlib import X, display
    from Xlib.protocol import event

    d = display.Display(os.environ.get("DISPLAY"))
    try:
        raiz = d.screen().root
        a_pid = d.intern_atom("_NET_WM_PID")
        a_prot = d.intern_atom("WM_PROTOCOLS")
        a_del = d.intern_atom("WM_DELETE_WINDOW")
        n = 0
        pila = [raiz]
        while pila:
            w = pila.pop()
            try:
                pila.extend(w.query_tree().children)
                p = w.get_full_property(a_pid, X.AnyPropertyType)
                prot = w.get_wm_protocols() or []
            except Exception:  # noqa: BLE001 - la ventana pudo desaparecer mientras se recorre
                continue
            if p and p.value and int(p.value[0]) in pids and a_del in prot:
                w.send_event(event.ClientMessage(window=w, client_type=a_prot, data=(32, [a_del, X.CurrentTime, 0, 0, 0])))
                n += 1
        d.flush()
        return n
    finally:
        d.close()
