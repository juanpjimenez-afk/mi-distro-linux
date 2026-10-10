"""Pruebas de overlay/usr/bin/infinity-lock: cuándo sale el bloqueo al arrancar.

El script se ejecuta de verdad, con un gtklock y un swaylock falsos al principio del PATH. El falso
gtklock tiene que ser un ejecutable llamado gtklock y no un script: pidof (sysvinit-utils, el que trae
Ubuntu) busca por el ejecutable del proceso, así que no ve un script (solo lo ve con -x, que el script
no usa). Por eso el falso gtklock hace exec de una copia de sleep llamada gtklock: dura 30 s, más que
los 2 s de espera del script.
"""

import ctypes
import os
import shlex
import shutil
import signal
import subprocess
import tempfile
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
LOCK = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-lock")
MARCA = "infinity-bloqueo-arranque"
PR_SET_CHILD_SUBREAPER = 36


def _pidof(nombre):
    r = subprocess.run(["pidof", nombre], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    return [int(pid) for pid in r.stdout.split()]


def _espera(condicion, segundos=3.0):
    limite = time.monotonic() + segundos
    while not condicion() and time.monotonic() < limite:
        time.sleep(0.05)
    return condicion()


def _subreaper(activo):
    """Linux: con esto activo, los huérfanos de este proceso pasan a él y puede recogerlos (wait).
    Sin ello, un falso muerto se quedaría como zombi si el pid 1 no recoge huérfanos."""
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_CHILD_SUBREAPER, int(activo), 0, 0, 0)
    except (OSError, AttributeError):
        pass


def _en_grupos(grupos):
    """PID de los procesos (zombis incluidos) cuyo grupo está en 'grupos', leído de /proc."""
    pids = []
    for nombre in os.listdir("/proc"):
        if not nombre.isdigit():
            continue
        try:
            with open("/proc/%s/stat" % nombre, encoding="utf-8") as f:
                datos = f.read()
            pgrp = int(datos[datos.rindex(")") + 2:].split()[2])
        except (OSError, ValueError, IndexError):
            continue
        if pgrp in grupos:
            pids.append(int(nombre))
    return pids


@unittest.skipUnless(shutil.which("pidof") and shutil.which("sleep"), "hacen falta pidof y sleep")
class TestBloqueoArranque(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Un gtklock o swaylock de verdad confundiría los casos: en ese caso no se ejecuta nada.
        for nombre in ("gtklock", "swaylock"):
            if _pidof(nombre):
                raise unittest.SkipTest("ya hay un %s en marcha: no se pueden aislar los falsos" % nombre)
        _subreaper(True)

    @classmethod
    def tearDownClass(cls):
        _subreaper(False)

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="bloqueo-arranque-")
        self.bin = os.path.join(self.tmp, "bin")          # va al principio del PATH
        self.real = os.path.join(self.tmp, "real")
        self.home = os.path.join(self.tmp, "home")
        self.cache = os.path.join(self.tmp, "cache")
        self.runtime = os.path.join(self.tmp, "runtime")  # hará de XDG_RUNTIME_DIR
        for carpeta in (self.bin, self.real, self.home, self.cache, self.runtime):
            os.makedirs(carpeta)
        os.chmod(self.runtime, 0o700)
        self.marca = os.path.join(self.runtime, MARCA)
        self.registro_swaylock = os.path.join(self.tmp, "swaylock.llamadas")
        # Cada Popen lidera su propio grupo de procesos (start_new_session): así se mata todo lo que
        # cuelga de él, y solo eso.
        self.lanzados = []

        sleeper = os.path.join(self.real, "gtklock")
        shutil.copy2(shutil.which("sleep"), sleeper)
        self._script(os.path.join(self.bin, "gtklock"), "#!/bin/sh\nexec %s 30\n" % shlex.quote(sleeper))
        self._script(os.path.join(self.bin, "swaylock"),
                     "#!/bin/sh\necho \"$*\" >> %s\n" % shlex.quote(self.registro_swaylock))

    def tearDown(self):
        self._mata_grupos()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _mata_grupos(self):
        """Mata lo que quede de los procesos de este test y recoge sus huérfanos."""
        grupos = {p.pid for p in self.lanzados}
        for pid in _en_grupos(grupos):
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                continue
            if pid not in grupos:  # huérfano (el gtklock falso): lo recibimos como subreaper
                try:
                    os.waitpid(pid, 0)
                except ChildProcessError:
                    pass
        for p in self.lanzados:
            p.wait(timeout=10)

    def _script(self, ruta, texto):
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        os.chmod(ruta, 0o755)

    def _entorno(self, variable, runtime):
        entorno = {"PATH": self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
                   "HOME": self.home, "XDG_CACHE_HOME": self.cache}
        if runtime:
            entorno["XDG_RUNTIME_DIR"] = self.runtime
        if variable:
            entorno["INFINITY_BLOQUEO_ARRANQUE"] = "1"
        return entorno

    def ejecuta(self, *args, variable=False, runtime=True):
        """Ejecuta infinity-lock y devuelve su código de salida."""
        p = subprocess.Popen(["sh", LOCK, *args], env=self._entorno(variable, runtime),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.lanzados.append(p)
        return p.wait(timeout=30)

    def gtklock_vivos(self):
        """PID de los gtklock vivos que son de este test (pidof no lista los zombis)."""
        grupos = {p.pid for p in self.lanzados}
        vivos = []
        for pid in _pidof("gtklock"):
            try:
                if os.getpgid(pid) in grupos:
                    vivos.append(pid)
            except ProcessLookupError:
                pass
        return sorted(vivos)

    def lanza_gtklock(self):
        """Un gtklock ya en marcha, como si el bloqueo hubiera salido antes."""
        p = subprocess.Popen([os.path.join(self.bin, "gtklock")], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
        self.lanzados.append(p)
        self.assertTrue(_espera(lambda: self.gtklock_vivos() == [p.pid]), "el falso gtklock no arrancó")
        return p

    def cae_hyprland(self):
        """Hyprland cae y con ella muere el bloqueo: se matan los procesos de este test."""
        self._mata_grupos()
        self.assertTrue(_espera(lambda: self.gtklock_vivos() == []), "gtklock no murió")

    def swaylock_llamado(self):
        return os.path.exists(self.registro_swaylock)

    def test_1_sin_variable_no_bloquea(self):
        # Tuigreet no exporta INFINITY_BLOQUEO_ARRANQUE: --arranque sale sin lanzar nada ni marcar.
        self.assertEqual(self.ejecuta("--arranque"), 0)
        self.assertEqual(self.gtklock_vivos(), [])
        self.assertFalse(self.swaylock_llamado())
        self.assertFalse(os.path.exists(self.marca))

    def test_2_primer_inicio_bloquea_y_crea_la_marca(self):
        self.assertEqual(self.ejecuta("--arranque", variable=True), 0)
        self.assertEqual(len(self.gtklock_vivos()), 1)
        self.assertFalse(self.swaylock_llamado())
        self.assertTrue(os.path.exists(self.marca))

    def test_3_segunda_vez_con_marca_no_lanza_nada(self):
        # Hyprland cae con el bloqueo puesto y vuelve a ejecutar exec-once: no debe salir otra vez.
        self.assertEqual(self.ejecuta("--arranque", variable=True), 0)
        self.assertEqual(len(self.gtklock_vivos()), 1)
        self.cae_hyprland()
        self.assertEqual(self.ejecuta("--arranque", variable=True), 0)
        self.assertEqual(self.gtklock_vivos(), [])
        self.assertFalse(self.swaylock_llamado())
        self.assertTrue(os.path.exists(self.marca))

    def test_4_sin_arranque_bloquea_aunque_haya_marca_y_sin_variable(self):
        open(self.marca, "w").close()
        self.assertEqual(self.ejecuta(), 0)
        self.assertEqual(len(self.gtklock_vivos()), 1)
        self.assertFalse(self.swaylock_llamado())

    def test_5_sin_arranque_con_gtklock_vivo_no_lanza_otro(self):
        vivo = self.lanza_gtklock()
        self.assertEqual(self.ejecuta(), 0)
        self.assertEqual(self.gtklock_vivos(), [vivo.pid])
        self.assertFalse(self.swaylock_llamado())

    def test_6_sin_xdg_runtime_dir_bloquea_en_cada_arranque(self):
        # Sin carpeta de sesión no hay dónde guardar la marca: ante la duda, bloquea.
        self.assertEqual(self.ejecuta("--arranque", variable=True, runtime=False), 0)
        self.assertEqual(len(self.gtklock_vivos()), 1)
        self.cae_hyprland()
        self.assertEqual(self.ejecuta("--arranque", variable=True, runtime=False), 0)
        self.assertEqual(len(self.gtklock_vivos()), 1)
        self.assertFalse(self.swaylock_llamado())


if __name__ == "__main__":
    unittest.main()
