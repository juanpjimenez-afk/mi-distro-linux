"""Pruebas del optimizador de juegos (overlay/usr/lib/infinity/game_optimizer.py).

Están escritas contra la especificación del optimizador, no contra su implementación actual.

Aislamiento:
- CPU_DIR, PROC_DIR, ESTADO_FICHERO, DROP_CACHES y MEMINFO apuntan a un directorio temporal
  (árboles falsos). Nunca se escribe en /sys ni en /proc/sys reales.
- En los árboles falsos, 'renice' y 'chrt' no se ejecutan, y os.setpriority,
  os.sched_setscheduler y subprocess.run (para renice/chrt) están sustituidos: no cambian
  ningún proceso real.
- Las pruebas con procesos reales (hilos, zombie) se saltan si no se es root o si renice falla.
  Solo tocan procesos que crea la propia prueba.
"""
import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "overlay", "usr", "lib", "infinity"))

import game_optimizer as GO  # noqa: E402

ES_ROOT = os.geteuid() == 0  # se mira antes de cualquier parche
# Originales, para lanzar órdenes y procesos de verdad cuando la prueba lo permite.
REAL_EJECUTAR = GO.ejecutar
REAL_RUN = subprocess.run
REAL_OS = {n: getattr(os, n) for n in ("setpriority", "getpriority", "sched_setscheduler",
                                       "sched_getscheduler", "sched_getparam")}

DISPONIBLES = "performance powersave ondemand schedutil"

# Proceso de prueba: tiene nice de partida distinto de 0 (la reversión debe devolver ese valor)
# y dos hilos secundarios además del principal. Solo duerme.
CODIGO_HILOS = """
import os, threading, time
os.nice(3)
def dormir():
    time.sleep(600)
for _ in range(2):
    threading.Thread(target=dormir, daemon=True).start()
print("listo", flush=True)
time.sleep(600)
"""


def texto_meminfo(disponible_kb):
    return ("MemTotal:       15728640 kB\n"
            "MemFree:         1048576 kB\n"
            f"MemAvailable:   {disponible_kb} kB\n"
            "Cached:          4194304 kB\n")


def stat_falso(pid, nombre, estado, hilos, inicio):
    """Línea de /proc/PID/stat. El estado es el campo 3 y la hora de inicio el 22."""
    tras_nombre = [
        estado,                              # 3  state
        "1", "1", "1",                       # 4-6 ppid, pgrp, session
        "0",                                 # 7  tty_nr
        "-1",                                # 8  tpgid
        "0", "0", "0", "0", "0",             # 9-13 flags, minflt, cminflt, majflt, cmajflt
        "0", "0", "0", "0",                  # 14-17 utime, stime, cutime, cstime
        "20",                                # 18 priority
        "0",                                 # 19 nice
        str(hilos),                          # 20 num_threads
        "0",                                 # 21 itrealvalue
        str(inicio),                         # 22 starttime
    ]
    assert len(tras_nombre) == 20
    return f"{pid} ({nombre}) " + " ".join(tras_nombre) + "\n"


class Ordenes:
    """Sustituye a GO.ejecutar: anota cada orden y decide cuáles se ejecutan de verdad."""

    def __init__(self):
        self.llamadas = []
        self.simuladas = {"renice", "chrt"}  # éxito sin ejecutar
        self.fallan = set()                  # 'tee' sobre estas rutas falla (fichero no escribible)
        self.fallan_orden = set()            # órdenes que devuelven error (p. ej. 'sync')
        self.al_escribir = None              # callback(ruta), antes de cada 'tee'
        self.al_orden = None                 # callback(argv), antes de cualquier orden

    def __call__(self, argv, entrada=None):
        self.llamadas.append(list(argv))
        if self.al_orden:
            self.al_orden(list(argv))
        orden = argv[0]
        if orden in self.fallan_orden:
            return subprocess.CompletedProcess(argv, 1, "", "fallo simulado")
        if orden == "tee":
            if self.al_escribir:
                self.al_escribir(argv[1])
            if argv[1] in self.fallan:
                return subprocess.CompletedProcess(argv, 1, "", "Permission denied")
            return REAL_EJECUTAR(argv, entrada=entrada)  # escribe en un temporal
        if orden == "sync" or orden in self.simuladas:
            return subprocess.CompletedProcess(argv, 0, "", "")
        return REAL_EJECUTAR(argv, entrada=entrada)

    def de(self, *ordenes):
        return [a for a in self.llamadas if a[0] in ordenes]


class Falso(unittest.TestCase):
    """Árbol falso de sysfs/procfs y estado en un directorio temporal. Simula root."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="optimizador-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.cpu = os.path.join(self.tmp, "cpu")
        self.proc = os.path.join(self.tmp, "proc")
        self.estado = os.path.join(self.tmp, "run", "game-optimizer.json")
        self.drop = os.path.join(self.tmp, "drop_caches")
        self.meminfo = os.path.join(self.tmp, "meminfo")
        os.makedirs(self.cpu)
        os.makedirs(self.proc)
        self._poner(self.drop, "0\n")
        self._poner(self.meminfo, texto_meminfo(8_000_000))

        self.permitir_reales = False  # las pruebas reales lo ponen a True
        self._parchear(GO, "CPU_DIR", self.cpu)
        self._parchear(GO, "PROC_DIR", self.proc)
        self._parchear(GO, "ESTADO_FICHERO", self.estado)
        self._parchear(GO, "DROP_CACHES", self.drop)
        self._parchear(GO, "MEMINFO", self.meminfo)
        for ruta in (GO.CPU_DIR, GO.PROC_DIR, GO.ESTADO_FICHERO, GO.DROP_CACHES, GO.MEMINFO):
            self.assertTrue(ruta.startswith(self.tmp), ruta)  # nunca rutas reales

        self._parchear(GO.os, "geteuid", return_value=0)  # se simula root
        self.ord = Ordenes()
        self._parchear(GO, "ejecutar", self.ord)
        # Guardia: ni renice ni chrt se lanzan si la prueba no lo permite.
        self._parchear(subprocess, "run", self._guarda_run)
        # Prioridades y planificación de procesos: mocks, salvo en las pruebas reales.
        self.setpriority = self._parchear(os, "setpriority")
        self._parchear(os, "getpriority", return_value=0)
        self.sched_setscheduler = self._parchear(os, "sched_setscheduler")
        self._parchear(os, "sched_getscheduler", return_value=os.SCHED_OTHER)
        self._parchear(os, "sched_getparam", return_value=mock.Mock(sched_priority=0))

    def _parchear(self, objeto, nombre, *args, **kw):
        p = mock.patch.object(objeto, nombre, *args, **kw)
        valor = p.start()
        self.addCleanup(p.stop)
        return valor

    def _guarda_run(self, argv, *args, **kw):
        if not self.permitir_reales and argv and argv[0] in ("renice", "chrt"):
            raise AssertionError(f"orden real no permitida en una prueba con árbol falso: {argv}")
        return REAL_RUN(argv, *args, **kw)

    def sin_root(self):
        return mock.patch.object(GO.os, "geteuid", return_value=1000)

    @staticmethod
    def _poner(ruta, texto):
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)

    @staticmethod
    def _leer(ruta):
        with open(ruta, encoding="utf-8") as f:
            return f.read().strip()

    def crear_nucleos(self, specs):
        """specs = {'cpu0': (governor_actual, governors_disponibles o None)}. Devuelve {nombre: ruta}."""
        rutas = {}
        for nombre, (actual, disponibles) in specs.items():
            d = os.path.join(self.cpu, nombre, "cpufreq")
            rutas[nombre] = os.path.join(d, "scaling_governor")
            self._poner(rutas[nombre], actual + "\n")
            if disponibles is not None:
                self._poner(os.path.join(d, "scaling_available_governors"), disponibles + "\n")
        return rutas

    def valores(self, rutas):
        return {n: self._leer(r) for n, r in rutas.items()}

    def leer_estado(self):
        with open(self.estado, encoding="utf-8") as f:
            return json.load(f)

    def hay_activacion(self):
        if not os.path.exists(self.estado):
            return False
        return bool(self.leer_estado().get("governors"))

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = GO.main(list(args))
        return rc, out.getvalue(), err.getvalue()

    def crear_proceso(self, pid, nombre="juego", tids=None, estado="S", exe=True, inicio=4242):
        """Proceso falso en PROC_DIR. Sin 'exe' se comporta como un proceso del kernel."""
        tids = tids or [pid]
        d = os.path.join(self.proc, str(pid))
        self._poner(os.path.join(d, "comm"), nombre + "\n")
        self._poner(os.path.join(d, "status"), f"Name:\t{nombre}\nState:\t{estado}\nPid:\t{pid}\n")
        self._poner(os.path.join(d, "stat"), stat_falso(pid, nombre, estado, len(tids), inicio))
        for t in tids:
            os.makedirs(os.path.join(d, "task", str(t)))
        if exe:
            os.symlink(sys.executable, os.path.join(d, "exe"))
        return d


class TestSinRoot(Falso):
    """Sin root no se cambia nada: el error llega antes de cualquier escritura."""

    def test_activar_falla_sin_root_y_no_cambia_nada(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES)})
        with self.sin_root(), self.assertRaises(GO.OptimizadorError):
            GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "powersave"})
        self.assertFalse(os.path.exists(self.estado))
        self.assertEqual(self.ord.de("tee"), [])

    def test_liberar_ram_falla_sin_root_y_no_escribe(self):
        with self.sin_root(), self.assertRaises(GO.OptimizadorError):
            GO.liberar_ram()
        self.assertEqual(self._leer(self.drop), "0")
        self.assertEqual(self.ord.de("tee"), [])

    def test_optimizar_falla_sin_root_y_no_cambia_nada(self):
        self.crear_proceso(1234)
        with self.sin_root(), self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(1234)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_revertir_falla_sin_root_y_no_cambia_nada(self):
        rutas = self.crear_nucleos({"cpu0": ("performance", DISPONIBLES)})
        self._poner(self.estado, json.dumps({"governors": {rutas["cpu0"]: "powersave"}}))
        with self.sin_root(), self.assertRaises(GO.OptimizadorError):
            GO.revertir_optimizacion()
        self.assertEqual(self.valores(rutas), {"cpu0": "performance"})
        self.assertTrue(self.hay_activacion())


class TestActivar(Falso):
    def test_sin_cpufreq_da_error_claro_y_no_crea_estado(self):
        with self.assertRaises(GO.OptimizadorError) as cm:
            GO.activar_modo_rendimiento()
        self.assertTrue(str(cm.exception))
        self.assertFalse(os.path.exists(self.estado))

    def test_pone_performance_y_guarda_originales_antes_de_escribir(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES), "cpu1": ("schedutil", DISPONIBLES)})
        estado_previo = []

        def al_escribir(ruta):
            if ruta in rutas.values():
                estado_previo.append(os.path.exists(self.estado))

        self.ord.al_escribir = al_escribir
        GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "performance", "cpu1": "performance"})
        self.assertEqual(estado_previo, [True, True])  # el estado ya existía al escribir
        self.assertEqual(sorted(self.leer_estado()["governors"].values()), ["powersave", "schedutil"])

    def test_segunda_activacion_conserva_los_originales(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES), "cpu1": ("schedutil", DISPONIBLES)})
        GO.activar_modo_rendimiento()
        GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "performance", "cpu1": "performance"})
        self.assertEqual(sorted(self.leer_estado()["governors"].values()), ["powersave", "schedutil"])

    def test_revertir_restaura_cada_nucleo(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES), "cpu1": ("schedutil", DISPONIBLES)})
        GO.activar_modo_rendimiento()
        rc, _, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        self.assertEqual(self.valores(rutas), {"cpu0": "powersave", "cpu1": "schedutil"})
        self.assertFalse(self.hay_activacion())

    def test_nucleo_sin_performance_no_cambia_nada(self):
        rutas = self.crear_nucleos({
            "cpu0": ("powersave", DISPONIBLES),
            "cpu1": ("powersave", "powersave schedutil"),  # no ofrece performance
        })
        with self.assertRaises(GO.OptimizadorError):
            GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "powersave", "cpu1": "powersave"})
        self.assertEqual([a for a in self.ord.de("tee") if a[1] in rutas.values()], [])
        self.assertFalse(os.path.exists(self.estado))

    def test_fallo_a_mitad_restaura_los_ya_escritos(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES), "cpu1": ("ondemand", DISPONIBLES)})
        self.ord.fallan.add(rutas["cpu1"])  # cpu1 no es escribible
        with self.assertRaises(GO.OptimizadorError):
            GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "powersave", "cpu1": "ondemand"})

    def test_sysfs_sin_scaling_available_governors_es_error_claro(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", None)})
        with self.assertRaises(GO.OptimizadorError):  # no FileNotFoundError
            GO.activar_modo_rendimiento()
        self.assertEqual(self.valores(rutas), {"cpu0": "powersave"})


class TestRevertir(Falso):
    def test_sin_estado_no_toca_los_governors(self):
        rutas = self.crear_nucleos({"cpu0": ("performance", DISPONIBLES), "cpu1": ("performance", DISPONIBLES)})
        rc, out, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        self.assertIn("no hay activación que revertir", (out + err).lower())
        self.assertEqual(self.valores(rutas), {"cpu0": "performance", "cpu1": "performance"})
        self.assertEqual(self.ord.de("tee"), [])

    def test_estado_pendiente_sin_cpufreq_limpia_el_estado_y_avisa(self):
        self._poner(self.estado, json.dumps(
            {"governors": {"/sys/devices/system/cpu/cpu0/cpufreq/scaling_governor": "powersave"}}))
        rc, out, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        self.assertFalse(self.hay_activacion())
        self.assertRegex(out + err, r"(?i)frecuencia|cpu|aviso")

    def test_estado_invalido_da_error_claro_y_no_borra_nada(self):
        rutas = self.crear_nucleos({"cpu0": ("powersave", DISPONIBLES)})
        casos = {
            "null": "null",
            "lista": "[1, 2]",
            "governors es lista": '{"governors": ["cpu0"]}',
            "governors es texto": '{"governors": "performance"}',
            "JSON roto": '{"governors": {',
        }
        for nombre, contenido in casos.items():
            for orden in ("activar", "revertir", "estado"):
                with self.subTest(caso=nombre, orden=orden):
                    self._poner(self.estado, contenido)
                    rc, out, err = self.cli(orden)
                    self.assertEqual(rc, 1)
                    self.assertTrue(err.strip())
                    self.assertNotIn("Traceback", err)
                    self.assertEqual(self._leer(self.estado), contenido)
                    self.assertEqual(self.valores(rutas), {"cpu0": "powersave"})


class TestLiberarRam(Falso):
    def test_hace_sync_y_escribe_1_en_drop_caches(self):
        GO.liberar_ram()
        self.assertEqual(self._leer(self.drop), "1")
        ordenes = [a[0] for a in self.ord.llamadas]
        self.assertIn("sync", ordenes)
        self.assertLess(ordenes.index("sync"), ordenes.index("tee"))

    def test_no_afirma_mejora_si_memoria_disponible_no_sube(self):
        # MemAvailable se queda igual antes y después: el mensaje no puede decir que subió.
        rc, out, err = self.cli("liberar-ram")
        self.assertEqual(rc, 0, err)
        texto = out + err
        self.assertTrue(texto.strip())
        # Una frase que afirme una subida de memoria disponible tiene que llevar una negación.
        for linea in texto.splitlines():
            if re.search(r"(?i)\+\s*\d|(aument|sub|crec)\w* (la )?memoria disponible|más (memoria|ram) disponible",
                         linea):
                self.assertRegex(linea, r"(?i)\bno\b", f"afirma una mejora de MemAvailable: {linea!r}")

    def test_sync_fallido_no_toca_la_cache(self):
        self.ord.fallan_orden.add("sync")
        with self.assertRaises(GO.OptimizadorError):
            GO.liberar_ram()
        self.assertEqual(self._leer(self.drop), "0")
        self.assertEqual(self.ord.de("tee"), [])


class TestProcesosFalsos(Falso):
    """Procesos en un /proc falso. Nada se renice ni se planifica de verdad."""

    def tids_con_nice(self, valor):
        tids = set()
        for argv in self.ord.de("renice"):
            if "-n" in argv and argv[argv.index("-n") + 1] == str(valor):
                tids.add(int(argv[-1]))
        for llamada in self.setpriority.call_args_list:
            prio, tid, nice = llamada.args
            if prio == os.PRIO_PROCESS and nice == valor:
                tids.add(tid)
        return tids

    def tids_con_rr(self):
        tids = set()
        for argv in self.ord.de("chrt"):
            if "-r" in argv:
                tids.add(int(argv[-1]))
        for llamada in self.sched_setscheduler.call_args_list:
            pid, politica = llamada.args[0], llamada.args[1]
            if politica == os.SCHED_RR:
                tids.add(pid)
        return tids

    def test_pid_no_valido_se_rechaza(self):
        for pid in (0, -5):
            with self.subTest(pid=pid), self.assertRaises(GO.OptimizadorError):
                GO.optimizar_proceso(pid)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_pid_1_se_rechaza(self):
        self._parchear(GO, "PROC_DIR", "/proc")  # aquí PID 1 sí existe; no se toca
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(1)
        self.assertEqual(self.ord.de("renice", "chrt"), [])
        self.assertEqual(self.setpriority.call_count, 0)

    def test_pid_inexistente_se_rechaza(self):
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(999999)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_proceso_del_kernel_sin_exe_se_rechaza(self):
        self.crear_proceso(2, nombre="kthreadd", exe=False)
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(2)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_zombie_falso_se_rechaza(self):
        self.crear_proceso(3000, nombre="juego", estado="Z")
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(3000)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_nice_en_todos_los_hilos_y_sin_tiempo_real_por_defecto(self):
        self.crear_proceso(1234, tids=[1234, 1235, 1236])
        GO.optimizar_proceso(1234)
        self.assertEqual(self.tids_con_nice(-10), {1234, 1235, 1236})
        self.assertEqual(self.tids_con_rr(), set())
        self.assertEqual(self.ord.de("chrt"), [])

    def test_tiempo_real_solo_en_el_hilo_principal(self):
        self.crear_proceso(1234, tids=[1234, 1235, 1236])
        GO.optimizar_proceso(1234, tiempo_real=True)
        self.assertEqual(self.tids_con_rr(), {1234})
        self.assertEqual(self.tids_con_nice(-10), {1234, 1235, 1236})

    def test_optimizar_guarda_el_estado_antes_del_primer_renice(self):
        self.crear_proceso(1234, tids=[1234, 1235, 1236])
        vistos = []

        def antes_de_renice(argv):
            if argv[0] == "renice" and not vistos:
                vistos.append(os.path.exists(self.estado) and "1234" in self._leer(self.estado))

        self.ord.al_orden = antes_de_renice
        GO.optimizar_proceso(1234)
        self.assertEqual(vistos, [True])

    def test_pid_reutilizado_durante_la_operacion_se_rechaza(self):
        # La hora de inicio (campo 22 de /proc/PID/stat) cambia tras el primer renice: el PID
        # ha pasado a otro proceso y no se puede seguir tocando.
        d = self.crear_proceso(1234, tids=[1234, 1235, 1236], inicio=4242)

        def reutilizar(argv):
            if argv[0] == "renice":
                self._poner(os.path.join(d, "stat"), stat_falso(1234, "juego", "S", 3, 9999))

        self.ord.al_orden = reutilizar
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(1234)


@unittest.skipUnless(ES_ROOT, "necesita root para cambiar prioridades de procesos reales")
class TestProcesosReales(Falso):
    """Procesos de prueba de verdad (solo los que crea la propia prueba)."""

    def setUp(self):
        super().setUp()
        self.permitir_reales = True
        for nombre, fn in REAL_OS.items():
            self._parchear(os, nombre, fn)
        self._parchear(GO, "PROC_DIR", "/proc")
        self.ord.simuladas = set()  # renice y chrt de verdad
        self.crear_nucleos({"cpu0": ("powersave", "performance powersave")})

    @staticmethod
    def _terminar(p):
        if p.poll() is None:
            p.kill()
        p.wait()
        p.stdout.close()

    def _probar_renice(self, pid):
        """Si renice no funciona aquí, la prueba se salta en vez de fallar."""
        try:
            r = subprocess.run(["renice", "-n", str(os.getpriority(os.PRIO_PROCESS, pid)), "-p", str(pid)],
                               capture_output=True, text=True)
        except FileNotFoundError:
            self.skipTest("no hay renice en este sistema")
        if r.returncode != 0:
            self.skipTest(f"renice no funciona aquí: {r.stderr.strip()}")

    def lanzar_hilos(self):
        p = subprocess.Popen([sys.executable, "-c", CODIGO_HILOS], stdout=subprocess.PIPE, text=True)
        self.addCleanup(self._terminar, p)
        self.assertEqual(p.stdout.readline().strip(), "listo")
        tids = sorted(int(t) for t in os.listdir(f"/proc/{p.pid}/task"))
        self.assertEqual(len(tids), 3)
        self._probar_renice(p.pid)
        return p, tids

    @staticmethod
    def _esperar_zombie(pid, limite=10):
        fin = time.time() + limite
        while time.time() < fin:
            with open(f"/proc/{pid}/status", encoding="utf-8") as f:
                if re.search(r"^State:\s+Z", f.read(), re.M):
                    return True
            time.sleep(0.02)
        return False

    def test_multihilo_nice_en_todos_los_tid(self):
        p, tids = self.lanzar_hilos()
        GO.optimizar_proceso(p.pid)
        for t in tids:
            with self.subTest(tid=t):
                self.assertEqual(os.getpriority(os.PRIO_PROCESS, t), -10)

    def test_revertir_devuelve_el_nice_original_de_cada_tid(self):
        p, tids = self.lanzar_hilos()
        originales = {t: os.getpriority(os.PRIO_PROCESS, t) for t in tids}
        GO.optimizar_proceso(p.pid)
        rc, _, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        for t in tids:
            with self.subTest(tid=t):
                self.assertEqual(os.getpriority(os.PRIO_PROCESS, t), originales[t])

    def test_por_defecto_la_politica_es_sched_other(self):
        p, tids = self.lanzar_hilos()
        GO.optimizar_proceso(p.pid)
        for t in tids:
            with self.subTest(tid=t):
                self.assertEqual(os.sched_getscheduler(t), os.SCHED_OTHER)
        self.assertEqual(self.ord.de("chrt"), [])

    def test_tiempo_real_solo_en_el_hilo_principal(self):
        p, tids = self.lanzar_hilos()
        self.addCleanup(subprocess.run, ["chrt", "-o", "-p", "0", str(p.pid)], capture_output=True)
        GO.optimizar_proceso(p.pid, tiempo_real=True)
        self.assertEqual(os.sched_getscheduler(p.pid), os.SCHED_RR)
        for t in tids:
            if t != p.pid:
                with self.subTest(tid=t):
                    self.assertEqual(os.sched_getscheduler(t), os.SCHED_OTHER)

    def test_revertir_quita_el_tiempo_real(self):
        p, _ = self.lanzar_hilos()
        self.addCleanup(subprocess.run, ["chrt", "-o", "-p", "0", str(p.pid)], capture_output=True)
        GO.optimizar_proceso(p.pid, tiempo_real=True)
        rc, _, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        self.assertEqual(os.sched_getscheduler(p.pid), os.SCHED_OTHER)

    def test_zombie_real_se_rechaza(self):
        z = subprocess.Popen([sys.executable, "-c", "pass"])  # termina y no se recoge
        self.addCleanup(z.wait)
        self.assertTrue(self._esperar_zombie(z.pid), "el hijo no llegó a estado zombie")
        with self.assertRaises(GO.OptimizadorError):
            GO.optimizar_proceso(z.pid)
        self.assertEqual(self.ord.de("renice", "chrt"), [])

    def test_proceso_que_termina_se_omite_al_revertir(self):
        p, _ = self.lanzar_hilos()
        GO.optimizar_proceso(p.pid)
        p.kill()
        p.wait()
        rc, _, err = self.cli("revertir")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("Traceback", err)
        self.assertFalse(self.hay_activacion())


if __name__ == "__main__":
    unittest.main()
