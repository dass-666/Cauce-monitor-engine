"""Lanzador de LABORATORIO que ejecuta el conector COMO OTRA CUENTA de Windows (cuenta restringida).

Por qué existe: el Job Object de `lanzador_lab` controla y termina el árbol de procesos, pero el proceso sigue siendo
del mismo usuario que Monitor y puede abrir `data\\*.db`, `.env` o los respaldos. Lo único que lo impide es otro
principal de Windows (una cuenta local sin privilegios) + ACL NTFS. Este módulo hace la parte del lanzamiento
(control C6 de docs/AISLAMIENTO_WINDOWS_ESPECIFICACION.md); las ACL y la cuenta las crea quien prepara el equipo.

Cómo funciona (solo Windows; en otro sistema se niega):
1. `CreateProcessWithLogonW` (servicio «Inicio de sesión secundario», el mismo que usa `runas`) con la cuenta y su
   clave, `LOGON_WITH_PROFILE` y `CREATE_SUSPENDED`: el proceso nace detenido, todavía no ha ejecutado nada.
2. Se asigna a un Job Object propio con KILL_ON_JOB_CLOSE (el de `lanzador_lab`). Si la asignación falla, el proceso
   se mata antes de reanudarlo: nunca corre fuera del Job. Después se reanuda.
3. stdin/stdout/stderr van por tuberías anónimas (como en el lanzador de laboratorio): Monitor no escribe archivos
   con la solicitud. El entorno es solo el que se pasa (nada de MONITOREO_*).
4. Cancelación o plazo: `TerminateJobObject` mata a todos los procesos del trabajo (conector, Chromium, nietos).
   Al terminar normalmente también se cierra el Job, para no dejar nada vivo. Si Monitor muere de golpe, Windows
   cierra el Job con él y mata el árbol.

Límites (no afirmar lo contrario):
- No hay terminación «ordenada» (CTRL_BREAK): el hijo tiene otra consola. Se termina con el Job.
- La clave de la cuenta la recibe quien llama; guardarla (DPAPI de la cuenta de Monitor) es otro paso.
- Esto NO desbloquea `aislamiento.puede_lanzar()` (sigue en False). Lanzar como otra cuenta solo sirve si las ACL
  niegan a esa cuenta los datos de Monitor, y eso se comprueba con `scripts/aislamiento_windows_lanzador.py`.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time

from monitoreo.autorrelleno.lanzador_lab import LIMITE_SALIDA, LanzadorBloqueado, LanzadorLab, ResultadoLanzamiento

ES_WINDOWS = sys.platform == "win32"

LOGON_WITH_PROFILE = 0x1
CREATE_SUSPENDED = 0x4
CREATE_NEW_CONSOLE = 0x10
CREATE_NEW_PROCESS_GROUP = 0x200
CREATE_UNICODE_ENVIRONMENT = 0x400
STARTF_USESHOWWINDOW = 0x1
STARTF_USESTDHANDLES = 0x100
SW_HIDE = 0
HANDLE_FLAG_INHERIT = 0x1
WAIT_OBJECT_0 = 0
JOB_BASIC_PROCESS_ID_LIST = 3


def bloque_entorno(env: dict) -> str:
    """Bloque de entorno UNICODE de Windows: «CLAVE=valor\\0» ordenado sin distinguir mayúsculas, y «\\0» final."""
    for k, v in env.items():
        if not k or "=" in k or "\0" in k or "\0" in str(v):
            raise ValueError("variable de entorno no válida")
    partes = [f"{k}={v}" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())]
    return "\0".join(partes) + "\0\0"


def linea_de_comando(comando: list[str]) -> str:
    """Línea de comando con las reglas de comillas de Windows (sin shell)."""
    if not comando or not all(isinstance(x, str) and x for x in comando):
        raise ValueError("comando vacío")
    return subprocess.list2cmdline(comando)


class LanzadorRestringido:
    def __init__(self, *, cuenta: str, clave: str, dominio: str = ".", modo_laboratorio: bool = False):
        if modo_laboratorio is not True:
            raise LanzadorBloqueado("lanzador restringido: requiere modo_laboratorio=True; no es el lanzador operativo")
        if not ES_WINDOWS:
            raise LanzadorBloqueado("lanzador restringido: solo existe en Windows (cuenta local + ACL NTFS)")
        if not cuenta or not clave:
            raise ValueError("faltan la cuenta o la clave")
        self.cuenta, self._clave, self.dominio = cuenta, clave, dominio
        self._cancelar = threading.Event()

    def cancelar(self) -> None:
        self._cancelar.set()

    # ------------------------------------------------------------------ Win32
    def _api(self):
        import ctypes
        import ctypes.wintypes as wt

        class SA(ctypes.Structure):
            _fields_ = [("nLength", wt.DWORD), ("lpSecurityDescriptor", wt.LPVOID), ("bInheritHandle", wt.BOOL)]

        class SI(ctypes.Structure):
            _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR),
                        ("dwX", wt.DWORD), ("dwY", wt.DWORD), ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD),
                        ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                        ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD),
                        ("lpReserved2", wt.LPVOID), ("hStdInput", wt.HANDLE), ("hStdOutput", wt.HANDLE),
                        ("hStdError", wt.HANDLE)]

        class PI(ctypes.Structure):
            _fields_ = [("hProcess", wt.HANDLE), ("hThread", wt.HANDLE), ("dwProcessId", wt.DWORD),
                        ("dwThreadId", wt.DWORD)]
        k = ctypes.WinDLL("kernel32", use_last_error=True)
        a = ctypes.WinDLL("advapi32", use_last_error=True)
        k.CreatePipe.argtypes = [ctypes.POINTER(wt.HANDLE), ctypes.POINTER(wt.HANDLE), ctypes.POINTER(SA), wt.DWORD]
        k.SetHandleInformation.argtypes = [wt.HANDLE, wt.DWORD, wt.DWORD]
        k.CloseHandle.argtypes = [wt.HANDLE]
        k.ResumeThread.argtypes = [wt.HANDLE]
        k.ResumeThread.restype = wt.DWORD
        k.TerminateProcess.argtypes = [wt.HANDLE, wt.UINT]
        k.WaitForSingleObject.argtypes = [wt.HANDLE, wt.DWORD]
        k.WaitForSingleObject.restype = wt.DWORD
        k.GetExitCodeProcess.argtypes = [wt.HANDLE, ctypes.POINTER(wt.DWORD)]
        k.QueryInformationJobObject.argtypes = [wt.HANDLE, ctypes.c_int, wt.LPVOID, wt.DWORD, ctypes.POINTER(wt.DWORD)]
        a.CreateProcessWithLogonW.argtypes = [wt.LPCWSTR, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD, wt.LPCWSTR, wt.LPWSTR,
                                              wt.DWORD, wt.LPVOID, wt.LPCWSTR, ctypes.POINTER(SI), ctypes.POINTER(PI)]
        return ctypes, wt, k, a, SA, SI, PI

    def _pids_del_trabajo(self, trabajo) -> list[int]:
        ctypes, wt = trabajo.c, trabajo.wt
        n = 1024

        class Lista(ctypes.Structure):
            _fields_ = [("asignados", wt.DWORD), ("en_lista", wt.DWORD), ("pids", ctypes.c_size_t * n)]
        lst = Lista()
        if not trabajo.h or not trabajo.k.QueryInformationJobObject(trabajo.h, JOB_BASIC_PROCESS_ID_LIST,
                                                                   ctypes.byref(lst), ctypes.sizeof(lst), None):
            return []
        return [int(lst.pids[i]) for i in range(min(lst.en_lista, n))]

    # ------------------------------------------------------------------ lanzamiento
    def lanzar(self, comando: list[str], *, entrada: str = "", cwd: str | None = None, env: dict | None = None,
               plazo_s: float = 60.0) -> ResultadoLanzamiento:
        import msvcrt

        from monitoreo.autorrelleno.lanzador_lab import _Trabajo
        ctypes, wt, k, a, SA, SI, PI = self._api()
        t0 = time.monotonic()
        linea = ctypes.create_unicode_buffer(linea_de_comando(comando))
        bloque = bloque_entorno(dict(env or {}))
        envbuf = (ctypes.c_wchar * len(bloque))(*bloque)
        sa = SA(ctypes.sizeof(SA), None, True)
        tub = {}
        for nombre in ("in", "out", "err"):
            r, w = wt.HANDLE(), wt.HANDLE()
            if not k.CreatePipe(ctypes.byref(r), ctypes.byref(w), ctypes.byref(sa), 0):
                raise OSError(ctypes.get_last_error(), "CreatePipe falló")
            tub[nombre] = (r, w)
        # el extremo de Monitor no se hereda
        for h in (tub["in"][1], tub["out"][0], tub["err"][0]):
            k.SetHandleInformation(h, HANDLE_FLAG_INHERIT, 0)
        si = SI()
        si.cb = ctypes.sizeof(SI)
        si.dwFlags = STARTF_USESTDHANDLES | STARTF_USESHOWWINDOW
        si.wShowWindow = SW_HIDE                     # oculta solo la consola de Python; Chromium abre su ventana
        si.hStdInput, si.hStdOutput, si.hStdError = tub["in"][0], tub["out"][1], tub["err"][1]
        pi = PI()
        trabajo = _Trabajo()
        ok = a.CreateProcessWithLogonW(self.cuenta, self.dominio, self._clave, LOGON_WITH_PROFILE, None, linea,
                                       CREATE_SUSPENDED | CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP
                                       | CREATE_UNICODE_ENVIRONMENT, envbuf, cwd, ctypes.byref(si), ctypes.byref(pi))
        err = ctypes.get_last_error()
        for h in (tub["in"][0], tub["out"][1], tub["err"][1]):
            k.CloseHandle(h)
        if not ok:
            for h in (tub["in"][1], tub["out"][0], tub["err"][0]):
                k.CloseHandle(h)
            trabajo.terminar()
            # sin el texto de la cuenta ni la clave: solo el código de Windows
            raise OSError(err, f"CreateProcessWithLogonW falló (WinError {err})")
        if not trabajo.k.AssignProcessToJobObject(trabajo.h, pi.hProcess):
            err = ctypes.get_last_error()
            k.TerminateProcess(pi.hProcess, 1)       # nunca se reanuda fuera del Job
            for h in (pi.hThread, pi.hProcess, tub["in"][1], tub["out"][0], tub["err"][0]):
                k.CloseHandle(h)
            trabajo.terminar()
            raise OSError(err, f"AssignProcessToJobObject falló (WinError {err}); el proceso se terminó sin ejecutarse")
        k.ResumeThread(pi.hThread)
        k.CloseHandle(pi.hThread)

        salida = {"o": b"", "e": b""}
        f_in = os.fdopen(msvcrt.open_osfhandle(tub["in"][1].value, os.O_WRONLY), "wb", 0)
        flujos = {"o": os.fdopen(msvcrt.open_osfhandle(tub["out"][0].value, os.O_RDONLY), "rb", 0),
                  "e": os.fdopen(msvcrt.open_osfhandle(tub["err"][0].value, os.O_RDONLY), "rb", 0)}

        def leer(clave):
            f = flujos[clave]
            while True:
                try:
                    b = f.read(65536)
                except OSError:
                    return
                if not b:
                    return
                if len(salida[clave]) < LIMITE_SALIDA:
                    salida[clave] += b[:LIMITE_SALIDA - len(salida[clave])]
        hilos = [threading.Thread(target=leer, args=(c,), daemon=True) for c in ("o", "e")]
        [h.start() for h in hilos]
        try:
            f_in.write(entrada.encode("utf-8"))
        except OSError:
            pass
        finally:
            try:
                f_in.close()
            except OSError:
                pass
        estado = "TERMINO"
        while k.WaitForSingleObject(pi.hProcess, 50) != WAIT_OBJECT_0:
            if self._cancelar.is_set():
                estado = "CANCELADO"
                break
            if time.monotonic() - t0 >= plazo_s:
                estado = "PLAZO"
                break
        vivos = self._pids_del_trabajo(trabajo)
        eliminados = len(vivos)
        trabajo.k.TerminateJobObject(trabajo.h, 1)
        fin = time.monotonic() + 10
        while self._pids_del_trabajo(trabajo) and time.monotonic() < fin:
            time.sleep(0.05)
        restos = self._pids_del_trabajo(trabajo)
        trabajo.terminar()
        k.WaitForSingleObject(pi.hProcess, 5000)
        codigo = wt.DWORD()
        k.GetExitCodeProcess(pi.hProcess, ctypes.byref(codigo))
        k.CloseHandle(pi.hProcess)
        [h.join(2) for h in hilos]
        for f in flujos.values():
            try:
                f.close()
            except OSError:
                pass
        return ResultadoLanzamiento(estado, int(codigo.value), salida["o"].decode("utf-8", "replace"),
                                    salida["e"].decode("utf-8", "replace"), round(time.monotonic() - t0, 3),
                                    estado != "TERMINO" or eliminados > 0, eliminados, restos)


class LanzadorCuentaPosix(LanzadorLab):
    """Equivalente de PRUEBAS en Linux (contenedor/CI) del lanzador restringido: el proceso nace con el uid/gid de otra
    cuenta (sin grupos suplementarios) y se controla igual que en `LanzadorLab` (sesión propia, PDEATHSIG, rondas de
    terminación). Exige que Monitor corra como root, cosa que solo tiene sentido en un equipo de pruebas. Sirve para
    comprobar con permisos REALES del sistema de archivos lo mismo que en Windows hacen la cuenta + ACL NTFS.
    Diferencia con Windows: no hay Job Object; un nieto que se separa y Monitor muere de golpe puede sobrevivir. Por
    eso `aislamiento_lab.limpiar_huerfanos()` termina cualquier proceso de la cuenta antes del siguiente lanzamiento."""

    def __init__(self, *, cuenta: str, modo_laboratorio: bool = False, gracia_s: float = 1.5):
        super().__init__(modo_laboratorio=modo_laboratorio, gracia_s=gracia_s)
        if ES_WINDOWS or not sys.platform.startswith("linux"):
            raise LanzadorBloqueado("lanzador de cuenta POSIX: solo Linux (en Windows se usa LanzadorRestringido)")
        if os.geteuid() != 0:
            raise LanzadorBloqueado("lanzador de cuenta POSIX: Monitor debe correr como root (solo pruebas)")
        import pwd
        pw = pwd.getpwnam(cuenta)
        if pw.pw_uid == 0:
            raise LanzadorBloqueado("lanzador de cuenta POSIX: la cuenta no puede ser root")
        self.cuenta, self._uid, self._gid = cuenta, pw.pw_uid, pw.pw_gid

    def _kw_extra(self) -> dict:
        return {"user": self._uid, "group": self._gid, "extra_groups": [], "umask": 0o077}
