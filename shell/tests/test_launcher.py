"""El buscador web del lanzador usa Google, y la lista se actualiza sin rehacerse en cada tecla.

Las pruebas de URL y de texto leen launcher.py con ast (sin ejecutarlo). Las de la lista necesitan
GTK 4 y una pantalla (xvfb-run): se saltan si no están.
"""
import ast
import gc
import os
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
RUTA = os.path.join(HERE, "..", "infinity_shell", "launcher.py")
PANTALLA = bool(os.environ.get("DISPLAY"))


class TestBuscadorWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RUTA, encoding="utf-8") as f:
            cls.arbol = ast.parse(f.read(), RUTA)

    def test_url_base_es_google(self):
        valores = [
            n.value.value
            for n in self.arbol.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "BUSCADOR_WEB" for t in n.targets)
        ]
        self.assertEqual(valores, ["https://www.google.com/search?q="])

    def test_resultado_de_busqueda_muestra_google(self):
        subtitulos = []
        for n in ast.walk(self.arbol):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Resultado"
                    and len(n.args) >= 2 and isinstance(n.args[0], ast.JoinedStr)
                    and isinstance(n.args[0].values[0], ast.Constant)
                    and str(n.args[0].values[0].value).startswith("Buscar en la web")):
                subtitulos.append(n.args[1].value)
        self.assertEqual(subtitulos, ["Google"])

    def test_no_queda_duckduckgo(self):
        with open(RUTA, encoding="utf-8") as f:
            self.assertFalse("duckduckgo" in f.read().lower(), "queda una referencia a DuckDuckGo en launcher.py")


class TestPrefijoPreguntar(unittest.TestCase):
    """El prefijo «?» del buscador (código leído sin ejecutarlo; las pruebas con la lista están más abajo)."""

    @classmethod
    def setUpClass(cls):
        with open(RUTA, encoding="utf-8") as f:
            cls.fuente = f.read()

    def test_el_prefijo_devuelve_antes_de_la_calculadora_y_de_la_busqueda_web(self):
        i_pregunta = self.fuente.index('q.startswith("?")')
        self.assertLess(i_pregunta, self.fuente.index("calcular(q)"))
        self.assertLess(i_pregunta, self.fuente.index("Buscar en la web"))
        self.assertGreater(i_pregunta, self.fuente.index('q.startswith(">")'))

    def test_la_pregunta_se_pasa_como_argumento_sin_shell(self):
        self.assertIn('["infinity-panki", "--preguntar", p]', self.fuente)
        self.assertIn('["infinity-panki"]', self.fuente)
        self.assertNotIn("infinity-panki --preguntar", self.fuente)  # nunca una cadena para un shell

    def test_textos_de_los_resultados(self):
        self.assertIn("Preguntar a pAnkI: {pregunta}", self.fuente)
        self.assertIn("pAnkI · internet solo en Modo Profundo", self.fuente)
        self.assertIn('"Abrir pAnkI"', self.fuente)
        self.assertEqual(self.fuente.count('"dialog-question"'), 2)


class _AppFalsa:
    def __init__(self, nombre):
        self.nombre = nombre
        self.descripcion = "descripción de " + nombre
        self.icono = "applications-other"
        self.id = nombre
        self.lanzadas = []

    def lanzar(self):
        self.lanzadas.append(self.nombre)


class _IndiceFalso:
    """Lo único que usa el lanzador del índice real: buscar() y apps con nombre, descripción e icono."""

    def __init__(self, n=40):
        self.apps = [_AppFalsa(f"Aplicación {i} demo") for i in range(1, n + 1)]

    def buscar(self, q, limite=8):
        from infinity_shell.search import normalizar
        return [a for a in self.apps if normalizar(q) in normalizar(a.nombre)][:limite]


def _cargar_launcher():
    """Importa infinity_shell.launcher. Sin el typelib de gtk4-layer-shell (no está en todas las
    distros) usa un doble: estas pruebas miran la lista, no la capa de Wayland."""
    import gi
    sys.path.insert(0, SHELL)
    original = gi.require_version
    try:
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
        except ValueError:
            doble = types.ModuleType("gi.repository.Gtk4LayerShell")
            for nombre in ("init_for_window", "set_namespace", "set_layer", "set_anchor",
                           "set_exclusive_zone", "set_keyboard_mode"):
                setattr(doble, nombre, lambda *a, **k: None)
            doble.Layer = types.SimpleNamespace(OVERLAY=0)
            doble.Edge = types.SimpleNamespace(TOP=0, BOTTOM=1, LEFT=2, RIGHT=3)
            doble.KeyboardMode = types.SimpleNamespace(EXCLUSIVE=0)
            sys.modules["gi.repository.Gtk4LayerShell"] = doble
            gi.require_version = lambda nombre, version: None if nombre == "Gtk4LayerShell" else original(nombre, version)
        from infinity_shell import launcher
        return launcher
    finally:
        gi.require_version = original


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestBusquedaDiferida(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import GLib, Gtk
            cls.L = _cargar_launcher()
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        cls.GLib, cls.Gtk = GLib, Gtk

    def setUp(self):
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.lanzador{time.time_ns()}")
        self.app.register(None)
        self.indice = _IndiceFalso()
        self.lz = self.L.Launcher(self.app, self.indice)
        self.lz.abrir()
        self.pumpa(0.05)

    def tearDown(self):
        self.lz.destroy()
        del self.lz
        gc.collect()

    def pumpa(self, segundos):
        ctx = self.GLib.MainContext.default()
        fin = time.time() + segundos
        while time.time() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.002)

    def filas_en_lista(self):
        filas, i = [], 0
        while (fila := self.lz.lista.get_row_at_index(i)) is not None:
            filas.append(fila)
            i += 1
        return filas

    def test_teclear_deprisa_reconstruye_la_lista_una_vez(self):
        original = self.L.Launcher._actualizar
        with mock.patch.object(self.L.Launcher, "_actualizar", autospec=True, side_effect=original) as act:
            for ch in "aplic":
                buf = self.lz.entrada.get_buffer()
                buf.insert_text(buf.get_length(), ch, -1)
            self.pumpa(0.2)
        self.assertEqual(act.call_count, 1)

    def test_enter_actua_sobre_lo_que_se_escribe_aunque_la_busqueda_no_haya_llegado(self):
        self.lz.entrada.set_text("aplicacion 7")
        self.lz.entrada.emit("activate")  # sin pausa: la búsqueda aún está pendiente
        self.assertEqual(self.indice.apps[6].lanzadas, ["Aplicación 7 demo"])

    def test_filas_se_reutilizan_entre_busquedas(self):
        self.lz.entrada.set_text("aplicacion")
        self.lz._al_dia()
        antes = list(self.lz._filas)
        self.assertGreater(len(antes), 1)
        self.lz.entrada.set_text("aplicacion 1")
        self.lz._al_dia()
        for a, b in zip(antes, self.lz._filas):
            self.assertIs(a, b)
        self.assertEqual(self.lz._filas[0].partes[1].get_text(), self.lz.resultados[0].titulo)

    def test_sobran_filas_se_quitan_de_la_lista(self):
        self.lz.entrada.set_text("aplicacion")
        self.lz._al_dia()
        self.assertGreater(len(self.filas_en_lista()), 2)
        self.lz.entrada.set_text("aplicacion 7")
        self.lz._al_dia()
        self.assertEqual(len(self.filas_en_lista()), len(self.lz.resultados))
        self.assertEqual(len(self.lz.resultados), 2)  # la app y «Buscar en la web»

    def test_fila_reutilizada_no_conserva_el_aviso_de_confirmacion(self):
        self.lz.entrada.set_text("aplicacion 1")
        self.lz._al_dia()
        sub = self.lz._filas[0].partes[2]
        sub.add_css_class("warn")
        self.lz.entrada.set_text("aplicacion 2")
        self.lz._al_dia()
        self.assertFalse(sub.has_css_class("warn"))

    def test_mover_con_flechas_aplica_la_busqueda_pendiente(self):
        self.lz.entrada.set_text("aplicacion 7")
        self.lz._mover(1)  # sin pausa: debe ver la búsqueda nueva (2 resultados), no la anterior
        self.assertEqual(len(self.lz.resultados), 2)

    def test_cerrar_cancela_la_busqueda_pendiente(self):
        original = self.L.Launcher._actualizar
        with mock.patch.object(self.L.Launcher, "_actualizar", autospec=True, side_effect=original) as act:
            self.lz.entrada.set_text("aplicacion")
            self.lz.cerrar()
            self.pumpa(0.2)
        self.assertEqual(act.call_count, 0)


    # ------------------------------------------------------------------ prefijo «?» (pAnkI)
    def buscar(self, texto):
        self.lz.entrada.set_text(texto)
        self.lz._al_dia()
        return self.lz.resultados

    def test_pregunta_a_panki(self):
        res = self.buscar("? qué hora es en Tokio")
        self.assertEqual(len(res), 1, "el prefijo manda: ni apps, ni calculadora, ni búsqueda web")
        self.assertEqual(res[0].titulo, "Preguntar a pAnkI: qué hora es en Tokio")
        self.assertEqual(res[0].subtitulo, "pAnkI · internet solo en Modo Profundo")
        self.assertEqual(res[0].icono, "dialog-question")
        self.assertFalse(res[0].confirmar)

    def test_enter_lanza_infinity_panki_con_la_pregunta_como_argumento(self):
        self.buscar("? qué hora es")
        with mock.patch.object(self.L.Launcher, "_lanzar") as lanzar:
            self.lz.entrada.emit("activate")
        lanzar.assert_called_once_with(["infinity-panki", "--preguntar", "qué hora es"])

    def test_la_pregunta_nunca_pasa_por_un_shell(self):
        peligrosa = "$(touch /tmp/x); rm -rf ~ | cat > /dev/null && `id` 'comilla\" doble"
        self.buscar("?" + peligrosa)
        with mock.patch.object(self.L.Launcher, "_lanzar") as lanzar:
            self.lz.entrada.emit("activate")
        lanzar.assert_called_once_with(["infinity-panki", "--preguntar", peligrosa])
        argv = lanzar.call_args[0][0]
        self.assertIsInstance(argv, list)
        self.assertEqual(argv[:2], ["infinity-panki", "--preguntar"])

    def test_interrogacion_a_secas_abre_panki(self):
        for texto in ("?", "?   ", "  ?  "):
            with self.subTest(texto):
                res = self.buscar(texto)
                self.assertEqual(len(res), 1)
                self.assertEqual(res[0].titulo, "Abrir pAnkI")
                self.assertEqual(res[0].icono, "dialog-question")
                with mock.patch.object(self.L.Launcher, "_lanzar") as lanzar:
                    self.lz.entrada.emit("activate")
                lanzar.assert_called_once_with(["infinity-panki"])
                self.lz.abrir()

    def test_el_prefijo_no_se_mezcla_con_la_calculadora_ni_con_las_apps(self):
        res = self.buscar("? 2+2")
        self.assertEqual([r.titulo for r in res], ["Preguntar a pAnkI: 2+2"])
        res = self.buscar("?aplicacion 7")  # sin espacio tras el «?» también vale
        self.assertEqual([r.titulo for r in res], ["Preguntar a pAnkI: aplicacion 7"])

    def test_una_interrogacion_dentro_del_texto_no_es_el_prefijo(self):
        res = self.buscar("aplicacion 7 ?")
        self.assertNotIn("Preguntar a pAnkI", " ".join(r.titulo for r in res))
        self.assertTrue(any(r.titulo.startswith("Buscar en la web") for r in res))

    def test_el_prefijo_de_comando_sigue_funcionando(self):
        res = self.buscar("> echo hola")
        self.assertEqual([r.titulo for r in res], ["Ejecutar: echo hola"])
        res = self.buscar("2+2")
        self.assertEqual(res[0].titulo, "= 4")


if __name__ == "__main__":
    unittest.main()
