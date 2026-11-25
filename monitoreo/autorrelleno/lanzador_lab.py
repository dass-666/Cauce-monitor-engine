"""Lanzador de LABORATORIO para procesos del conector: controla y termina TODO el árbol de procesos.

Origen: PR #4 (scripts/lanzador_lab.py, sin cambios de lógica). Vive fuera de `monitoreo/conector/` a propósito:
la capa de contrato tiene una prueba que le prohíbe lanzar procesos. Solo lo importa `monitoreo/autorrelleno/`
(el autorrellenado de Ventanilla, que solo se monta en laboratorio); hay una prueba que lo comprueba.

NO es el lanzador operativo: exige `modo_laboratorio=True` de forma explícita y no consulta ni cambia
`aislamiento.puede_lanzar()` (sigue siendo False).
Sirve para probar, con datos ficticios, que Monitor puede detener un conector y a todo lo que haya lanzado.

Cómo se garantiza que no quedan procesos (cada punto tiene su prueba en tests/test_lanzador_lab.py):
- Linux/macOS: el hijo arranca en su propia sesión (grupo de procesos propio). La terminación es en 3 pasos: señal
  ordenada al grupo (SIGTERM), una gracia corta y SIGKILL por rondas hasta que no queda ninguno. Los candidatos salen de
  (a) los descendientes vistos por psutil, (b) un historial de descendientes tomado cada 100 ms mientras vive el proceso
  (alcanza a los que se «desertan» de su padre) y (c) cualquier proceso del mismo usuario que lleve la marca
  CAUCE_LANZADOR_ID en su entorno. Con identidad (pid + hora de creación) para no matar un pid reutilizado.
- Windows: el hijo se asigna a un Job Object con KILL_ON_JOB_CLOSE; terminarlo mata a todos los procesos del trabajo,
  incluidos los nietos. Se mantiene además la parte de psutil.
Si Monitor muere de golpe (cierre de la consola, kill, corte): en Windows el Job Object no es heredable y se cierra
con Monitor, lo que mata todo el árbol; en Linux el hijo directo recibe SIGTERM del núcleo (PR_SET_PDEATHSIG) y Forms
cierra su Chromium al terminar. En macOS no hay equivalente (no se usa en el laboratorio).
Límite conocido (Linux): un proceso que se separa (setsid), limpia su entorno y desaparece del árbol en menos de 100 ms,
antes de ser visto, no se puede encontrar sin cgroups; en Windows el Job Object sí lo cubre. Aislamiento de permisos
(cuenta restringida, ACL) es otro tema: ver docs/AISLAMIENTO_WINDOWS_ESPECIFICACION.md.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import psutil

ES_WINDOWS = sys.platform == "win32"
MARCA = "CAUCE_LANZADOR_ID"
LIMITE_SALIDA = 1_000_000


class LanzadorBloqueado(RuntimeError):
    """Se intentó usar el lanzador de laboratorio sin declarar el modo de laboratorio."""


@dataclass
class ResultadoLanzamiento:
    estado: str                       # TERMINO | PLAZO | CANCELADO
    codigo_salida: int | None
    stdout: str
    stderr: str
    duracion_s: float
    forzado: bool = False             # hubo que usar la terminación forzada
    eliminados: int = 0               # procesos del árbol eliminados por el lanzador
    sobrevivientes: list = field(default_factory=list)   # pids que NO se pudo eliminar (debe quedar vacío)


# ------------------------------------------------------------------------------------------------ Windows: Job Object
class _Trabajo:
    def __init__(self):
        import ctypes
        import ctypes.wintypes as wt
        self.c, self.wt = ctypes, wt
        self.k = ctypes.WinDLL("kernel32", use_last_error=True)
        self.k.CreateJobObjectW.restype = wt.HANDLE
        self.h = self.k.CreateJobObjectW(None, None)
        if not self.h:
            raise OSError("CreateJobObjectW falló")

        class Basic(ctypes.Structure):
            _fields_ = [("PerProcessUserTimeLimit", wt.LARGE_INTEGER), ("PerJobUserTimeLimit", wt.LARGE_INTEGER),
                        ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                        ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD),
                        ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

        class Io(ctypes.Structure):
            _fields_ = [(n, ctypes.c_ulonglong) for n in ("a", "b", "c", "d", "e", "f")]

        class Ext(ctypes.Structure):
            _fields_ = [("Basic", Basic), ("Io", Io), ("ProcessMemoryLimit", ctypes.c_size_t),
                        ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t),
                        ("PeakJobMemoryUsed", ctypes.c_size_t)]
        info = Ext()
        info.Basic.LimitFlags = 0x2000                      # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not self.k.SetInformationJobObject(self.h, 9, ctypes.byref(info), ctypes.sizeof(info)):
            raise OSError("SetInformationJobObject falló")

    def asignar(self, proc: subprocess.Popen) -> None:
        if not self.k.AssignProcessToJobObject(self.h, self.wt.HANDLE(int(proc._handle))):
            raise OSError("AssignProcessToJobObject falló")

    def terminar(self) -> None:
        if self.h:
            self.k.TerminateJobObject(self.h, 1)
            self.k.CloseHandle(self.h)
            self.h = None


# ------------------------------------------------------------------------------------------------ lanzador
def _morir_con_el_padre(padre: int):
    """Linux: el hijo recibe SIGTERM si muere el hilo de Monitor que lo lanzó (también con kill -9 de Monitor)."""
    import ctypes

    libc = ctypes.CDLL(None, use_errno=True)

    def preparar() -> None:          # corre en el hijo entre fork y exec: nada de locks ni logging
        libc.prctl(1, int(signal.SIGTERM), 0, 0, 0)       # 1 = PR_SET_PDEATHSIG
        if os.getppid() != padre:                           # Monitor murió antes de llegar aquí
            os._exit(1)
    return preparar


def _id(p: psutil.Process) -> tuple[int, float]:
    return (p.pid, p.create_time())


class LanzadorLab:
    def __init__(self, *, modo_laboratorio: bool = False, gracia_s: float = 1.5):
        if modo_laboratorio is not True:
            raise LanzadorBloqueado("lanzador de laboratorio: requiere modo_laboratorio=True; no es el lanzador operativo")
        self.gracia_s = gracia_s
        self._cancelar = threading.Event()
        self._proc: subprocess.Popen | None = None

    def _kw_extra(self) -> dict:
        """Argumentos extra de Popen para subclases (p. ej. lanzar con otra cuenta en Linux)."""
        return {}

    def cancelar(self) -> None:
        """Pide terminar (seguro entre hilos). Equivale a que Monitor revoque la autorización a mitad."""
        self._cancelar.set()

    # --- descubrimiento de procesos del árbol
    def _descendientes(self, raiz: psutil.Process | None) -> dict:
        vivos = {}
        if raiz is not None:
            try:
                for p in [raiz, *raiz.children(recursive=True)]:
                    vivos[_id(p)] = p
            except psutil.Error:
                pass
        return vivos

    def _por_marca(self, marca: str) -> dict:
        encontrados = {}
        for p in psutil.process_iter():
            try:
                if p.pid != os.getpid() and p.environ().get(MARCA) == marca:
                    encontrados[_id(p)] = p
            except (psutil.Error, OSError):
                continue
        return encontrados

    @staticmethod
    def _vivo(p: psutil.Process) -> bool:
        try:
            return p.is_running() and p.status() != psutil.STATUS_ZOMBIE
        except psutil.Error:
            return False

    def _terminar_arbol(self, proc: subprocess.Popen, marca: str, historial: dict, trabajo) -> tuple[int, list]:
        try:
            raiz = psutil.Process(proc.pid)
        except psutil.Error:
            raiz = None
        candidatos = {**historial, **self._descendientes(raiz), **self._por_marca(marca)}
        # 1) terminación ordenada del grupo
        try:
            if ES_WINDOWS:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                os.killpg(proc.pid, signal.SIGTERM)
        except (OSError, ProcessLookupError, ValueError):
            pass
        fin = time.monotonic() + self.gracia_s
        while time.monotonic() < fin and any(self._vivo(p) for p in candidatos.values()):
            time.sleep(0.05)
        # 2) forzada: Windows cierra el Job (mata a todos); en todas partes, rondas de kill hasta que no quede ninguno
        eliminados, tope = 0, time.monotonic() + 10
        if ES_WINDOWS and trabajo is not None:
            eliminados += sum(1 for p in candidatos.values() if self._vivo(p) and p.pid != os.getpid())  # los que cierra el Job también cuentan
            trabajo.terminar()
        while time.monotonic() < tope:
            ronda = {**historial, **candidatos, **self._descendientes(raiz), **self._por_marca(marca)}
            vivos = [p for p in ronda.values() if self._vivo(p) and p.pid != os.getpid()]
            if not vivos:
                break
            if not ES_WINDOWS:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (OSError, ProcessLookupError):
                    pass
            for p in vivos:                      # padres y descendientes a la vez: nadie vuelve a lanzar otro
                try:
                    p.kill()
                    eliminados += 1
                except psutil.Error:
                    pass
            psutil.wait_procs(vivos, timeout=0.5)
            candidatos = ronda
        restos = [p.pid for p in {**historial, **candidatos, **self._por_marca(marca)}.values() if self._vivo(p)]
        return eliminados, restos

    def lanzar(self, comando: list[str], *, entrada: str = "", cwd: str | Path | None = None, env: dict | None = None,
               plazo_s: float = 60.0) -> ResultadoLanzamiento:
        """Lanza UN proceso (shell=False), le da `entrada` por stdin y lo espera hasta `plazo_s`."""
        t0, marca = time.monotonic(), uuid.uuid4().hex
        entorno = dict(env or {})
        entorno[MARCA] = marca
        kw: dict = {}
        if ES_WINDOWS:
            kw["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
            if sys.platform.startswith("linux"):
                kw["preexec_fn"] = _morir_con_el_padre(os.getpid())
        kw.update(self._kw_extra())
        trabajo = _Trabajo() if ES_WINDOWS else None
        proc = subprocess.Popen(comando, cwd=cwd, env=entorno, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, shell=False, **kw)
        self._proc = proc
        if trabajo is not None:
            trabajo.asignar(proc)
        salida = {"o": b"", "e": b""}

        def leer(clave, flujo):
            while True:
                b = flujo.read(65536)
                if not b:
                    return
                if len(salida[clave]) < LIMITE_SALIDA:
                    salida[clave] += b[:LIMITE_SALIDA - len(salida[clave])]
        hilos = [threading.Thread(target=leer, args=(k, f), daemon=True) for k, f in (("o", proc.stdout), ("e", proc.stderr))]
        [h.start() for h in hilos]
        historial: dict = {}
        parar = threading.Event()

        def vigilar():                           # historial de descendientes: alcanza a los que luego se desertan
            try:
                raiz = psutil.Process(proc.pid)
            except psutil.Error:
                return
            while not parar.is_set():
                historial.update(self._descendientes(raiz))
                parar.wait(0.1)
        vig = threading.Thread(target=vigilar, daemon=True)
        vig.start()
        try:
            proc.stdin.write(entrada.encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass
        estado = "TERMINO"
        while proc.poll() is None:
            if self._cancelar.is_set():
                estado = "CANCELADO"
                break
            if time.monotonic() - t0 >= plazo_s:
                estado = "PLAZO"
                break
            time.sleep(0.05)
        # incluso si el padre terminó solo, se comprueba que no dejó descendientes ni procesos marcados
        eliminados, restos = self._terminar_arbol(proc, marca, historial, trabajo)
        forzado = estado != "TERMINO" or eliminados > 0
        parar.set()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        [h.join(2) for h in hilos]
        return ResultadoLanzamiento(estado, proc.returncode, salida["o"].decode("utf-8", "replace"),
                                    salida["e"].decode("utf-8", "replace"), round(time.monotonic() - t0, 3),
                                    forzado, eliminados, restos)
