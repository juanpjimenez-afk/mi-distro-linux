import gc
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "overlay", "usr", "lib", "infinity"))

try:
    import game_gui as GG  # noqa: E402
    import game_environment as ENV  # noqa: E402
    import tkinter  # noqa: F401
    TK_OK = True
except ImportError:  # sin tkinter (p. ej. CI sin python3-tk): se saltan las pruebas de ventana
    GG = ENV = None
    TK_OK = False

PANTALLA = bool(os.environ.get("DISPLAY"))


def esperar(app, limite=15):
    """Procesa eventos de Tk hasta que termine la tarea en curso (o pase el límite)."""
    fin = time.time() + limite
    while app._tarea is not None and time.time() < fin:
        app.update()
        time.sleep(0.01)
    app.update()


@unittest.skipUnless(TK_OK and PANTALLA, "necesita tkinter y una pantalla (xvfb-run)")
class TestVentana(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="juegos-")
        self.casa = os.path.join(self.tmp, "home")
        os.makedirs(self.casa)
        self.parches = [
            mock.patch.dict(os.environ, {"HOME": self.casa}),
            mock.patch.object(GG, "leer_estado", return_value={"activado": False, "governors": {}}),
        ]
        for p in self.parches:
            p.start()
        self.app = GG.Juegos(con_logo=False)
        self.app.update()

    def tearDown(self):
        # Cada Tk() tiene su intérprete de Tcl: debe liberarse en el hilo principal, no en un
        # hilo de trabajo que el recolector de basura use por casualidad (aborta el proceso).
        self.app.destroy()
        del self.app
        for p in self.parches:
            p.stop()
        gc.collect()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def compat(self):
        return os.path.join(self.casa, ".steam", "root", "compatibilitytools.d")

    def temporizadores(self):
        """Tk devuelve una cadena vacía cuando no hay temporizadores pendientes."""
        return self.app.tk.call("after", "info")

    # ------------------------------------------------------------------ reposo
    def test_en_reposo_no_hay_temporizadores(self):
        for _ in range(5):
            self.app.update()
        self.assertFalse(self.temporizadores())

    def test_despues_de_una_tarea_no_quedan_temporizadores(self):
        with mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", return_value=subprocess.CompletedProcess(
                    [], 0, stdout="Modo rendimiento activado en 4 núcleos.", stderr="")):
            self.app._liberar_ram()
            esperar(self.app)
        self.assertIsNone(self.app._tarea)
        self.assertFalse(self.temporizadores())

    # ------------------------------------------------------------------ LED
    def test_led_verde_si_el_modo_juego_esta_activo(self):
        with mock.patch.object(GG, "leer_estado", return_value={"activado": True, "governors": {"cpu0": "performance"}}):
            self.app._refrescar()
        self.assertEqual(self.app._led.itemcget(self.app._led_id, "fill"), GG.COLORES["verde"])
        self.assertIn("activo", self.app._texto_led.cget("text"))
        self.assertEqual(self.app._btn_modo.cget("text"), "Desactivar modo juego")

    def test_led_rojo_si_esta_desactivado(self):
        self.assertEqual(self.app._led.itemcget(self.app._led_id, "fill"), GG.COLORES["rojo"])
        self.assertEqual(self.app._btn_modo.cget("text"), "Activar modo juego")

    # ------------------------------------------------------------------ rendimiento
    def test_activar_no_libera_ram_por_defecto(self):
        # Antes liberaba la caché siempre; ahora solo activa (la liberación es casilla opt-in, apagada)
        llamadas = []

        def falso(argv, **kw):
            llamadas.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        with mock.patch.object(GG, "hay_cpufreq", return_value=True), \
                mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", side_effect=falso):
            self.app._modo_juego()
            esperar(self.app)
        self.assertEqual(llamadas, [["sudo", "-n", "infinity-juegos", "activar"]])
        self.assertIn("Modo juego activado", self.app._estado.cget("text"))

    def test_sin_sudo_sin_contrasena_abre_terminal(self):
        abiertos = []
        with mock.patch.object(GG, "hay_cpufreq", return_value=True), \
                mock.patch.object(GG, "sudo_sin_contrasena", return_value=False), \
                mock.patch.object(GG.subprocess, "Popen", side_effect=lambda argv, **kw: abiertos.append(argv)):
            self.app._modo_juego()
            esperar(self.app)
        self.assertEqual(abiertos[0][0], "foot")
        self.assertIn("activar", abiertos[0][-1])
        self.assertNotIn("liberar-ram", abiertos[0][-1])

    def test_error_de_root_se_muestra(self):
        fallo = subprocess.CompletedProcess([], 1, stdout="", stderr="activar: no hay control de frecuencia")
        with mock.patch.object(GG, "hay_cpufreq", return_value=True), \
                mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", return_value=fallo):
            self.app._modo_juego()
            esperar(self.app)
        self.assertIn("no hay control de frecuencia", self.app._estado.cget("text"))

    # ------------------------------------------------------------------ GE-Proton
    def test_lista_proton_la_mas_nueva_primero(self):
        for n in ("GE-Proton9-27", "GE-Proton10-2", "GE-Proton10-20", "otra-cosa"):
            os.makedirs(os.path.join(self.compat, n))
        self.app._refrescar_proton()
        self.assertEqual(self.app._versiones, ["GE-Proton10-20", "GE-Proton10-2", "GE-Proton9-27"])
        self.assertEqual(self.app._lista.get(0).strip(), "GE-Proton10-20")

    def test_descarga_muestra_progreso_y_termina(self):
        def falso(_tag):
            for hecho in (25, 50, 100):
                ENV._informe.progreso("Descargando X", hecho, 100, 2 * GG.MB)
            ENV._informe.cerrar_progreso()
            ENV._informe.mensaje("[ok] X instalada")
            return "/ruta/GE-ProtonX"

        with mock.patch.object(GG.env, "instalar_ge_proton", side_effect=lambda tag=None: falso(tag)):
            self.app._descargar_proton()
            esperar(self.app)
        self.assertIn("GE-ProtonX", self.app._estado.cget("text"))
        self.assertFalse(self.temporizadores())

    def test_cancelar_descarga_lanza_error_controlado(self):
        def falso(_tag):
            ENV._informe.progreso("Descargando X", 10, 100, 1)
            self.app._cancelar.set()  # lo que hace el botón Cancelar durante la descarga
            ENV._informe.progreso("Descargando X", 20, 100, 1)  # aquí se lanza la cancelación
            return "no debería llegar"

        with mock.patch.object(GG.env, "instalar_ge_proton", side_effect=lambda tag=None: falso(tag)):
            self.app._descargar_proton()
            esperar(self.app)
        self.assertIn("cancelada", self.app._estado.cget("text"))

    def test_eliminar_pide_confirmacion(self):
        ruta = os.path.join(self.compat, "GE-Proton10-2")
        os.makedirs(ruta)
        self.app._refrescar_proton()
        self.app._lista.selection_set(0)
        with mock.patch.object(GG.messagebox, "askyesno", return_value=False):
            self.app._eliminar_proton()
        self.assertTrue(os.path.isdir(ruta))
        with mock.patch.object(GG.messagebox, "askyesno", return_value=True):
            self.app._eliminar_proton()
            esperar(self.app)
        self.assertFalse(os.path.exists(ruta))

    def test_eliminar_sin_seleccion_no_hace_nada(self):
        with mock.patch.object(GG.messagebox, "askyesno") as pregunta:
            self.app._eliminar_proton()
        pregunta.assert_not_called()

    def test_limpia_restos_de_eliminacion_interrumpida(self):
        resto = os.path.join(self.compat, ".eliminando-GE-Proton9-1-123")
        os.makedirs(os.path.join(resto, "dentro"))
        os.makedirs(os.path.join(self.compat, "GE-Proton10-2"))
        self.assertEqual(GG.limpiar_restos_eliminacion(self.compat), 1)
        self.assertFalse(os.path.exists(resto))
        self.assertEqual(GG.versiones_proton(self.compat), ["GE-Proton10-2"])

    def test_cancelar_avisa_de_que_se_retoma_con_la_misma_version(self):
        self.app._pedir_cancelar()
        self.assertIn("misma versión", self.app._estado.cget("text"))

    # ------------------------------------------------------------------ modo juego: CPU y órdenes
    def test_sin_cpufreq_no_escribe_nada(self):
        with mock.patch.object(GG, "hay_cpufreq", return_value=False), \
                mock.patch.object(GG.subprocess, "run") as orden:
            self.app._modo_juego()
        orden.assert_not_called()
        self.assertIsNone(self.app._tarea)
        self.assertIn("no permite cambiar la frecuencia", self.app._estado.cget("text"))

    def test_casilla_liberar_ram_se_hace_despues_de_activar(self):
        llamadas = []

        def falso(argv, **kw):
            llamadas.append(argv[-1])
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        self.app._var_liberar.set(True)
        with mock.patch.object(GG, "hay_cpufreq", return_value=True), \
                mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", side_effect=falso):
            self.app._modo_juego()
            esperar(self.app)
        self.assertEqual(llamadas, ["activar", "liberar-ram"])

    def test_optimizar_proceso_sin_tiempo_real_por_defecto(self):
        self.app._pintar_procesos([(1234, "juego", 50.0)])
        self.app._lista_procesos.selection_set(0)
        llamadas = []

        def falso(argv, **kw):
            llamadas.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="Proceso 1234 (juego): nice -10", stderr="")

        with mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", side_effect=falso):
            self.app._optimizar_proceso()
            esperar(self.app)
        self.assertEqual(llamadas, [["sudo", "-n", "infinity-juegos", "optimizar", "1234"]])

    def test_optimizar_proceso_con_tiempo_real_si_se_marca(self):
        self.app._pintar_procesos([(1234, "juego", 50.0)])
        self.app._lista_procesos.selection_set(0)
        self.app._var_tiempo_real.set(True)
        llamadas = []

        def falso(argv, **kw):
            llamadas.append(argv)
            return subprocess.CompletedProcess(argv, 0, stdout="ok", stderr="")

        with mock.patch.object(GG, "sudo_sin_contrasena", return_value=True), \
                mock.patch.object(GG.subprocess, "run", side_effect=falso):
            self.app._optimizar_proceso()
            esperar(self.app)
        self.assertEqual(llamadas[0][-2:], ["1234", "--tiempo-real"])

    def test_hilo_que_no_arranca_no_deja_la_ventana_bloqueada(self):
        with mock.patch.object(GG.threading.Thread, "start", side_effect=RuntimeError("sin recursos")):
            self.app._liberar_ram()
        self.assertIsNone(self.app._tarea)
        self.assertEqual(self.app._btn_ram.cget("state"), "normal")
        self.assertIn("No se pudo iniciar", self.app._estado.cget("text"))

    # ------------------------------------------------------------------ cierre con tarea en curso
    def test_cerrar_con_orden_root_en_curso_no_cierra(self):
        import threading
        fin = threading.Event()

        def bloqueada(informe):
            fin.wait(10)
            return "hecho"

        try:
            self.app._lanzar("Activando…", bloqueada)
            with mock.patch.object(GG.messagebox, "showinfo") as aviso:
                self.app._cerrar()
            self.assertTrue(aviso.called)
            self.assertEqual(self.app.winfo_exists(), 1)
        finally:
            fin.set()
            esperar(self.app)

    def test_cerrar_descarga_declinada_no_cierra(self):
        import threading
        fin = threading.Event()

        def descarga(_tag):
            fin.wait(10)
            return "/ruta/GE-ProtonX"

        try:
            with mock.patch.object(GG.env, "instalar_ge_proton", side_effect=lambda tag=None: descarga(tag)):
                self.app._descargar_proton()
                with mock.patch.object(GG.messagebox, "askokcancel", return_value=False), \
                        mock.patch.object(self.app, "destroy") as cerrar:
                    self.app._cerrar()
            cerrar.assert_not_called()
            self.assertFalse(self.app._cancelar.is_set())
        finally:
            fin.set()
            esperar(self.app)


@unittest.skipUnless(TK_OK, "necesita tkinter")
class TestLogica(unittest.TestCase):
    def test_argv_privilegiado_usa_sudo_sin_contrasena(self):
        self.assertEqual(GG.argv_privilegiado(["activar"]), ["sudo", "-n", "infinity-juegos", "activar"])

    def test_argv_terminal_encadena_las_ordenes(self):
        argv = GG.argv_terminal_sudo(["liberar-ram", "activar"])
        self.assertEqual(argv[0], "foot")
        self.assertIn("sudo infinity-juegos liberar-ram && sudo infinity-juegos activar", argv[-1])

    def test_ruta_proton_rechaza_salirse_del_directorio(self):
        with tempfile.TemporaryDirectory() as d:
            for malo in ("../x", "a/b", "..", "", "otra"):
                with self.assertRaises(ENV.ErrorEntorno):
                    GG.ruta_proton_segura(malo, directorio=d)
            self.assertTrue(GG.ruta_proton_segura("GE-Proton1", directorio=d).endswith("GE-Proton1"))

    def test_versiones_con_directorio_inexistente(self):
        self.assertEqual(GG.versiones_proton(os.path.join(tempfile.gettempdir(), "no-existe-juegos")), [])

    def test_versiones_con_ruta_que_no_es_carpeta(self):
        with tempfile.TemporaryDirectory() as d:
            fichero = os.path.join(d, "f")
            open(fichero, "w").close()
            self.assertEqual(GG.versiones_proton(fichero), [])

    def test_enlace_simbolico_fuera_de_la_carpeta_se_rechaza(self):
        with tempfile.TemporaryDirectory() as base:
            d = os.path.join(base, "compat")
            fuera = os.path.join(base, "fuera")
            os.makedirs(d)
            os.makedirs(fuera)
            os.symlink(fuera, os.path.join(d, "GE-Proton1"))
            with self.assertRaises(ENV.ErrorEntorno):
                GG.ruta_proton_segura("GE-Proton1", directorio=d)

    def test_enlace_simbolico_dentro_de_la_carpeta_tambien_se_rechaza(self):
        with tempfile.TemporaryDirectory() as d:
            os.makedirs(os.path.join(d, "GE-Proton2"))
            os.symlink(os.path.join(d, "GE-Proton2"), os.path.join(d, "GE-Proton1"))
            with self.assertRaises(ENV.ErrorEntorno):
                GG.ruta_proton_segura("GE-Proton1", directorio=d)

    def test_abrir_terminal_cita_todas_las_ordenes(self):
        with mock.patch.object(GG.subprocess, "Popen", side_effect=OSError("no hay foot")):
            with self.assertRaises(ENV.ErrorEntorno) as caso:
                GG._abrir_terminal(["liberar-ram", "activar"])
        self.assertIn("sudo infinity-juegos liberar-ram && sudo infinity-juegos activar", str(caso.exception))

    def test_sudo_sin_contrasena_comprueba_la_orden_real(self):
        with mock.patch.object(GG.subprocess, "run", return_value=subprocess.CompletedProcess([], 0)) as orden:
            self.assertTrue(GG.sudo_sin_contrasena())
        self.assertEqual(orden.call_args[0][0], ["sudo", "-n", "-l", "infinity-juegos"])

    def test_leer_estado_nunca_lanza(self):
        with mock.patch.object(GG.opt, "estado", return_value=None):
            self.assertTrue(GG.leer_estado()["error"])
        with mock.patch.object(GG.opt, "estado", side_effect=RuntimeError("raro")):
            estado = GG.leer_estado()
        self.assertTrue(estado["error"])
        self.assertIn("raro", estado["detalle"])

    def test_resumen_cpu_con_plurales(self):
        self.assertEqual(GG.resumen_cpu({"governors": {"c0": "performance"}, "procesos": [{}, {}]}),
                         "CPU performance · 2 procesos optimizados")
        self.assertEqual(GG.resumen_cpu({"governors": {}, "procesos": [{}]}),
                         "CPU sin control de frecuencia · 1 proceso optimizado")

    def test_procesos_de_usuario_devuelve_pid_nombre_y_cpu(self):
        lista = GG.procesos_de_usuario(muestra=0.05)
        self.assertIsInstance(lista, list)
        for pid, nombre, pct in lista:
            self.assertIsInstance(pid, int)
            self.assertIsInstance(nombre, str)
            self.assertIsInstance(pct, float)


if __name__ == "__main__":
    unittest.main()
