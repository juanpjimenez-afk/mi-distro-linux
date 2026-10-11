"""La burbuja de pAnkI (panki_burbuja.py): su posición, su botón, lo que hace al pulsarla y su conexión con
el shell.

Las pruebas de la aritmética de los márgenes, de las constantes y de main.py leen el código con ast (sin
ejecutar GTK). Las de la ventana necesitan GTK 4 y una pantalla (xvfb-run): se saltan si no están. Las
ventanas de capa no funcionan sin un compositor Wayland, así que se prueba con un doble de
gtk4-layer-shell que apunta lo que se le pide (anclas, márgenes, espacio de nombres...). La posición real
en Hyprland, que no tape la píldora de estado ni el dock, solo se puede comprobar en una VM.
"""
import ast
import contextlib
import gc
import os
import re
import shutil
import sys
import time
import types
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PAQUETE = os.path.join(SHELL, "infinity_shell")
RAIZ = os.path.join(SHELL, "..")
DOCK = os.path.join(PAQUETE, "dock.py")
BURBUJA = os.path.join(PAQUETE, "panki_burbuja.py")
MAIN = os.path.join(PAQUETE, "main.py")
PANKI_APP = os.path.join(PAQUETE, "panki_app.py")
CSS = os.path.join(PAQUETE, "style.css")
MARCA_SVG = os.path.join(RAIZ, "branding", "panki-white.svg")
ICONO_PNG = os.path.join(RAIZ, "branding", "panki.png")
PANTALLA = bool(os.environ.get("DISPLAY"))


def _arbol(ruta):
    with open(ruta, encoding="utf-8") as f:
        return ast.parse(f.read(), ruta)


def _es_nombre(nodo, nombre):
    return isinstance(nodo, ast.Name) and nodo.id == nombre


def _asignacion(arbol, nombre):
    """El valor (nodo) de la asignación de primer nivel `nombre = ...`."""
    for n in arbol.body:
        if isinstance(n, ast.Assign) and any(_es_nombre(t, nombre) for t in n.targets):
            return n.value
    raise AssertionError(f"no hay una asignación de {nombre}")


def _funcion(arbol, nombre, clase=None):
    cuerpo = arbol.body
    if clase:
        cuerpo = next(n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == clase).body
    return next(n for n in cuerpo if isinstance(n, ast.FunctionDef) and n.name == nombre)


def _llamadas(nodo):
    return [n for n in ast.walk(nodo) if isinstance(n, ast.Call)]


def _nombre_llamada(llamada):
    f = llamada.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _modulo_puro():
    """Las constantes y la función de margen de panki_burbuja.py, ejecutadas solas (sin importar GTK) y
    con las constantes reales de dock.py. Devuelve (espacio de nombres, constantes de dock)."""
    dock = _arbol(DOCK)
    constantes = {nombre: ast.literal_eval(_asignacion(dock, nombre)) for nombre in ("ALTO", "MARGEN")}
    nodos = [n for n in _arbol(BURBUJA).body
             if (isinstance(n, ast.Assign) and any(_es_nombre(t, nombre) for t in n.targets
                                                   for nombre in ("HUECO", "MARGEN_DERECHO")))
             or (isinstance(n, ast.FunctionDef) and n.name == "margen_inferior")]
    espacio = dict(constantes)
    exec(compile(ast.Module(body=nodos, type_ignores=[]), BURBUJA, "exec"), espacio)
    return espacio, constantes


# ====================================================================== sin GTK
class TestMargenes(unittest.TestCase):
    """La aritmética de la posición: encima de la píldora de estado, sin solaparla."""

    @classmethod
    def setUpClass(cls):
        cls.ns, cls.dock = _modulo_puro()
        cls.alto, cls.margen = cls.dock["ALTO"], cls.dock["MARGEN"]

    def test_margen_inferior_es_margen_mas_alto_mas_hueco(self):
        self.assertEqual(self.ns["margen_inferior"](), self.margen + self.alto + self.ns["HUECO"])
        self.assertEqual(self.ns["margen_inferior"](), 88)  # 14 + 60 + 14

    def test_el_hueco_es_de_unos_14_px(self):
        self.assertTrue(10 <= self.ns["HUECO"] <= 20, self.ns["HUECO"])

    def test_la_burbuja_queda_por_encima_de_la_pildora_sin_solaparla(self):
        # Medidas desde el borde inferior de la pantalla: la píldora (y el dock, que va en la misma fila)
        # ocupa [margen, margen + alto]; la burbuja, [margen_inferior, margen_inferior + alto].
        pildora = (self.margen, self.margen + self.alto)
        burbuja = (self.ns["margen_inferior"](), self.ns["margen_inferior"]() + self.alto)
        self.assertGreater(burbuja[0], pildora[1], "la burbuja tapa la píldora de estado")
        self.assertEqual(burbuja[0] - pildora[1], self.ns["HUECO"])

    def test_margen_inferior_sigue_a_las_constantes(self):
        f = self.ns["margen_inferior"]
        self.assertEqual(f(margen=20, alto=70), 20 + 70 + self.ns["HUECO"])
        self.assertEqual(f(margen=14, alto=60, hueco=0), 74)

    def test_el_margen_derecho_es_el_de_la_pildora_de_estado(self):
        self.assertEqual(self.ns["MARGEN_DERECHO"], self.margen + 4)
        # ...y la píldora de estado lo sigue usando: si dock.py cambia, la burbuja debe cambiar con él.
        llamada = next(c for c in _llamadas(_arbol(DOCK))
                       if _nombre_llamada(c) == "_capa" and len(c.args) > 3
                       and isinstance(c.args[1], ast.Constant) and c.args[1].value == "infinity-status")
        borde, expresion = llamada.args[3].elts
        self.assertEqual(borde.attr, "RIGHT")
        derecho = eval(compile(ast.Expression(expresion), DOCK, "eval"), {"MARGEN": self.margen})
        self.assertEqual(derecho, self.ns["MARGEN_DERECHO"])

    def test_la_aritmetica_parte_de_que_la_pildora_mide_alto_y_sube_margen(self):
        dock = _arbol(DOCK)
        # _capa pone el margen inferior de todas las islas a MARGEN...
        capa = _funcion(dock, "_capa")
        self.assertTrue(any(_nombre_llamada(c) == "set_margin" and len(c.args) == 3
                            and getattr(c.args[1], "attr", None) == "BOTTOM" and _es_nombre(c.args[2], "MARGEN")
                            for c in _llamadas(capa)), "_capa ya no fija el margen inferior a MARGEN")
        # ...y la píldora de estado mide ALTO de alto.
        estado = _funcion(dock, "__init__", clase="Estado")
        self.assertTrue(any(_nombre_llamada(c) == "set_size_request" and len(c.args) == 2
                            and _es_nombre(c.args[1], "ALTO") for c in _llamadas(estado)),
                        "la píldora de estado ya no mide ALTO de alto")


class TestCodigo(unittest.TestCase):
    """Reglas del módulo, leídas del código: nada bloqueante y nada que consuma en reposo."""

    @classmethod
    def setUpClass(cls):
        cls.arbol = _arbol(BURBUJA)
        with open(BURBUJA, encoding="utf-8") as f:
            cls.texto = f.read()

    def test_los_comandos_son_listas_sin_shell(self):
        alternar = ast.literal_eval(_asignacion(self.arbol, "ALTERNAR"))
        abrir = ast.literal_eval(_asignacion(self.arbol, "ABRIR"))
        self.assertEqual(alternar, ["gapplication", "action", "org.infinity.Panki", "alternar"])
        self.assertEqual(abrir, ["infinity-panki"])

    def test_no_bloquea_ni_usa_un_shell(self):
        importados = {a.name.split(".")[0] for n in ast.walk(self.arbol) if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("subprocess", importados)
        for llamada in _llamadas(self.arbol):
            self.assertNotIn(_nombre_llamada(llamada), ("system", "run", "call", "check_call", "check_output",
                                                         "communicate", "Popen"), ast.dump(llamada.func))
        self.assertNotIn("shell=True", self.texto)

    def test_sin_temporizadores_ni_animaciones(self):
        for llamada in _llamadas(self.arbol):
            self.assertNotIn(_nombre_llamada(llamada),
                             ("timeout_add", "timeout_add_seconds", "idle_add", "add_tick_callback"),
                             "la burbuja debe costar lo mismo que el logo: nada de temporizadores")

    def test_reutiliza_capa_y_asset_de_dock(self):
        importados = {a.name for n in self.arbol.body if isinstance(n, ast.ImportFrom) and n.module == "dock"
                      and n.level == 1 for a in n.names}
        self.assertTrue({"_capa", "asset", "ALTO", "MARGEN"} <= importados, importados)
        definidas = {n.name for n in self.arbol.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        self.assertNotIn("_capa", definidas)
        self.assertNotIn("asset", definidas)

    def test_el_espacio_de_nombres_hereda_la_regla_de_desenfoque(self):
        # hyprland.conf desenfoca los espacios de nombres ^infinity-.*$
        self.assertIn('"infinity-panki"', self.texto)
        regla = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")
        with open(regla, encoding="utf-8") as f:
            patrones = re.findall(r"^layerrule\s*=\s*blur on,\s*match:namespace\s+(\S+)", f.read(), re.M)
        self.assertTrue(any(re.search(p, "infinity-panki") for p in patrones), patrones)


class TestConexionConElShell(unittest.TestCase):
    """main.py importa la burbuja, la crea con el resto de la interfaz y reevalúa el ajuste al recargar."""

    @classmethod
    def setUpClass(cls):
        cls.arbol = _arbol(MAIN)

    def test_importa_burbujapanki(self):
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == "panki_burbuja" and n.level == 1
                            and any(a.name == "BurbujaPanki" for a in n.names) for n in self.arbol.body))

    def test_la_crea_junto_al_orbe_el_dock_y_el_estado(self):
        crear = _funcion(self.arbol, "_crear_interfaz", clase="Shell")
        creadas = set()
        for n in ast.walk(crear):
            if (isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                    and _nombre_llamada(n.value) in ("Dock", "Orbe", "Estado", "BurbujaPanki")):
                creadas.update(t.attr for t in n.targets if isinstance(t, ast.Attribute))
        self.assertEqual(creadas, {"dock", "orbe", "estado", "burbuja"})
        self.assertIn("_sincronizar_burbuja", {_nombre_llamada(c) for c in _llamadas(crear)})

    def test_lee_el_ajuste_panki_burbuja_y_la_muestra_u_oculta(self):
        sincronizar = _funcion(self.arbol, "_sincronizar_burbuja", clase="Shell")
        llamadas = _llamadas(sincronizar)
        lecturas = [c for c in llamadas if _nombre_llamada(c) == "ajuste"]
        self.assertEqual([c.args[0].value for c in lecturas], ["panki.burbuja"])
        # El valor leído va tal cual a la burbuja (que trata None como activado)
        mostrar = [c for c in llamadas if _nombre_llamada(c) == "actualizar_visibilidad"]
        self.assertEqual(len(mostrar), 1)
        self.assertIs(mostrar[0].args[0], lecturas[0])

    def test_recargar_reevalua_el_ajuste(self):
        recargar = _funcion(self.arbol, "_accion_recargar", clase="Shell")
        self.assertIn("_sincronizar_burbuja", {_nombre_llamada(c) for c in _llamadas(recargar)})

    def test_la_cadena_de_documentacion_habla_de_la_burbuja(self):
        doc = ast.get_docstring(self.arbol)
        self.assertIn("burbuja de pAnkI", doc)
        self.assertIn("panki.burbuja", doc)


class TestMarcaYEstilo(unittest.TestCase):
    """Los archivos de la marca y la variante de estilo `button.orb.panki`."""

    def test_el_svg_de_la_marca_es_blanco_y_con_viewbox(self):
        import xml.etree.ElementTree as ET
        raiz = ET.parse(MARCA_SVG).getroot()
        self.assertEqual(raiz.tag, "{http://www.w3.org/2000/svg}svg")
        self.assertEqual(raiz.get("viewBox"), "0 0 2000 2000")
        colores = {v.lower() for e in raiz.iter() for k, v in e.attrib.items() if k in ("fill", "stroke")}
        self.assertEqual(colores - {"none"}, {"#ffffff"})

    def test_el_icono_mide_256_con_transparencia(self):
        import struct
        with open(ICONO_PNG, "rb") as f:
            cabecera = f.read(26)
        self.assertEqual(cabecera[:8], b"\x89PNG\r\n\x1a\n")
        ancho, alto, profundidad, tipo = struct.unpack(">IIBB", cabecera[16:26])
        self.assertEqual((ancho, alto), (256, 256))
        self.assertEqual(tipo, 6)  # RGBA

    def test_el_svg_publicado_es_el_que_genera_make_panki(self):
        sys.path.insert(0, os.path.join(RAIZ, "branding"))
        try:
            import make_panki
        except ImportError as e:  # sin Pillow
            self.skipTest(f"sin las dependencias del generador: {e}")
        finally:
            sys.path.pop(0)
        with open(MARCA_SVG, encoding="utf-8") as f:
            self.assertEqual(f.read(), make_panki.svg())

    def test_la_variante_orb_panki_existe_y_conserva_el_cristal(self):
        with open(CSS, encoding="utf-8") as f:
            css = f.read()
        bloques = dict(re.findall(r"^(button\.orb\.panki[^{]*)\{([^}]*)\}", css, re.M))
        bloques = {k.strip(): v for k, v in bloques.items()}
        self.assertEqual(set(bloques), {"button.orb.panki", "button.orb.panki:hover", "button.orb.panki:active"})
        # hyprland.conf solo desenfoca por encima de ignore_alpha 0.3: el relleno no puede ser más transparente
        for selector, cuerpo in bloques.items():
            fondo = re.search(r"background-color:\s*rgba\([^)]*,\s*([\d.]+)\)", cuerpo)
            self.assertIsNotNone(fondo, selector)
            self.assertGreaterEqual(float(fondo.group(1)), 0.30, selector)
        # no redefine el tamaño ni la forma del orbe base
        for propiedad in ("min-width", "min-height", "border-radius", "padding"):
            self.assertNotIn(propiedad, "".join(bloques.values()))

    def test_panki_app_usa_el_mismo_id_y_accion(self):
        if not os.path.exists(PANKI_APP):
            self.skipTest("panki_app.py aún no existe")
        with open(PANKI_APP, encoding="utf-8") as f:
            texto = f.read()
        self.assertIn("org.infinity.Panki", texto)
        self.assertIn('"alternar"', texto)


# ======================================================================= con GTK
def _crear_doble():
    """Un gtk4-layer-shell falso que apunta lo que se le pide a cada ventana (en `registro`)."""
    doble = types.ModuleType("gi.repository.Gtk4LayerShell")
    doble.registro = {}
    doble.Layer = types.SimpleNamespace(BACKGROUND=0, BOTTOM=1, TOP=2, OVERLAY=3)
    doble.Edge = types.SimpleNamespace(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)
    doble.KeyboardMode = types.SimpleNamespace(NONE=0, EXCLUSIVE=1, ON_DEMAND=2)

    def datos(ventana):
        return doble.registro.setdefault(ventana, {"anclas": set(), "margenes": {}})

    def anotar(clave):
        return lambda ventana, valor: datos(ventana).__setitem__(clave, valor)

    doble.init_for_window = lambda v: datos(v)
    doble.set_namespace = anotar("namespace")
    doble.set_layer = anotar("capa")
    doble.set_keyboard_mode = anotar("teclado")
    doble.set_exclusive_zone = anotar("zona")
    doble.auto_exclusive_zone_enable = lambda v: datos(v).__setitem__("zona", "auto")
    doble.set_anchor = lambda v, borde, ancla: (datos(v)["anclas"].add if ancla else datos(v)["anclas"].discard)(borde)
    doble.set_margin = lambda v, borde, valor: datos(v)["margenes"].__setitem__(borde, valor)
    return doble


def cargar_burbuja():
    """Importa infinity_shell.dock y infinity_shell.panki_burbuja. Si el typelib de gtk4-layer-shell no
    está (no está en todas las distros) usa un doble. Devuelve (dock, panki_burbuja)."""
    import gi
    sys.path.insert(0, SHELL)
    original = gi.require_version
    try:
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
        except ValueError:
            if "gi.repository.Gtk4LayerShell" not in sys.modules:
                sys.modules["gi.repository.Gtk4LayerShell"] = _crear_doble()
            gi.require_version = lambda nombre, version: None if nombre == "Gtk4LayerShell" else original(nombre, version)
        from infinity_shell import dock, panki_burbuja
        return dock, panki_burbuja
    finally:
        gi.require_version = original


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestBurbuja(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, Gio, GLib, Gtk
            cls.dock, cls.B = cargar_burbuja()
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        cls.Gdk, cls.Gio, cls.GLib, cls.Gtk = Gdk, Gio, GLib, Gtk
        # Sea cual sea el gtk4-layer-shell importado, aquí manda el doble: en X11 no hay capas
        cls.capa = _crear_doble()
        for modulo in (cls.dock, cls.B):
            parche = mock.patch.object(modulo, "LayerShell", cls.capa)
            parche.start()
            cls.addClassCleanup(parche.stop)

    def setUp(self):
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.burbuja{time.time_ns()}")
        self.app.register(None)
        self.llamadas = []      # argv de cada ejecución pedida, en orden
        self.respuestas = {}    # tuple(argv) -> True/False: con qué acaba (si falta, queda pendiente)
        self.pendientes = []    # (argv, al_terminar) de las ejecuciones que no han acabado
        self.burbuja = self.B.BurbujaPanki(self.app, lanzar=self.lanzar_falso)

    def tearDown(self):
        self.burbuja.destroy()
        del self.burbuja
        gc.collect()

    def lanzar_falso(self, argv, al_terminar=None):
        self.assertIsInstance(argv, list)  # lista de argumentos: nada de cadenas para un shell
        self.llamadas.append(list(argv))
        if al_terminar is None:
            return
        if tuple(argv) in self.respuestas:
            al_terminar(self.respuestas[tuple(argv)])
        else:
            self.pendientes.append((list(argv), al_terminar))

    def pumpa(self, segundos):
        ctx = self.GLib.MainContext.default()
        fin = time.time() + segundos
        while time.time() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.002)

    # ------------------------------------------------------------ ventana
    def test_se_construye(self):
        self.assertIsInstance(self.burbuja, self.Gtk.Window)
        self.assertEqual(self.burbuja.get_title(), "Infinity — pAnkI")
        self.assertIs(self.burbuja.get_child(), self.burbuja.boton)

    def test_es_una_ventana_de_capa_anclada_abajo_a_la_derecha(self):
        d = self.capa.registro[self.burbuja]
        self.assertEqual(d["namespace"], "infinity-panki")
        self.assertEqual(d["capa"], self.capa.Layer.TOP)
        self.assertEqual(d["teclado"], self.capa.KeyboardMode.NONE)  # no roba el foco al escritorio
        self.assertEqual(d["zona"], -1)  # no reserva espacio: lo hace el dock
        self.assertEqual(d["anclas"], {self.capa.Edge.BOTTOM, self.capa.Edge.RIGHT})
        self.assertTrue(self.burbuja.has_css_class("infinity-window"))

    def test_margenes_encima_de_la_pildora_de_estado(self):
        d = self.capa.registro[self.burbuja]
        self.assertEqual(d["margenes"][self.capa.Edge.RIGHT], self.dock.MARGEN + 4)
        self.assertEqual(d["margenes"][self.capa.Edge.BOTTOM], self.B.margen_inferior())
        self.assertEqual(self.B.margen_inferior(), self.dock.MARGEN + self.dock.ALTO + self.B.HUECO)
        self.assertGreater(d["margenes"][self.capa.Edge.BOTTOM], self.dock.MARGEN + self.dock.ALTO)

    def test_los_margenes_coinciden_con_los_del_modulo_leido_sin_gtk(self):
        ns, _ = _modulo_puro()
        self.assertEqual(self.B.margen_inferior(), ns["margen_inferior"]())
        self.assertEqual(self.B.MARGEN_DERECHO, ns["MARGEN_DERECHO"])

    # -------------------------------------------------------------- botón
    def test_boton_con_las_clases_de_la_esfera(self):
        b = self.burbuja.boton
        self.assertIsInstance(b, self.Gtk.Button)
        self.assertTrue(b.has_css_class("orb"))
        self.assertTrue(b.has_css_class("panki"))

    @contextlib.contextmanager
    def css_del_proyecto(self):
        """El style.css real, puesto en la pantalla mientras dura la prueba (y quitado después)."""
        display = self.Gdk.Display.get_default()
        proveedor = self.Gtk.CssProvider()
        proveedor.load_from_path(CSS)
        self.Gtk.StyleContext.add_provider_for_display(display, proveedor, self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            yield
        finally:
            self.Gtk.StyleContext.remove_provider_for_display(display, proveedor)

    def tamano(self, widget):
        """Ancho y alto asignados al widget (con su borde y su relleno)."""
        _ok, caja = widget.compute_bounds(widget)
        return round(caja.get_width()), round(caja.get_height())

    def test_boton_pide_alto_por_alto(self):
        self.assertEqual(self.dock.ALTO, 60)
        self.assertEqual(self.burbuja.boton.get_size_request(), (self.dock.ALTO, self.dock.ALTO))
        self.burbuja.present()
        self.pumpa(0.1)
        self.assertEqual(self.tamano(self.burbuja.boton), (self.dock.ALTO, self.dock.ALTO))

    def test_boton_mide_igual_que_el_logo_con_el_css_del_proyecto(self):
        with self.css_del_proyecto():
            orbe = self.dock.Orbe(self.app, lambda: None)
            try:
                for ventana in (orbe, self.burbuja):
                    ventana.present()
                self.pumpa(0.1)
                logo, burbuja = self.tamano(orbe.boton), self.tamano(self.burbuja.boton)
                self.assertEqual(burbuja, logo)
                # 60 px de la esfera y 1 px de borde a cada lado: lo que mide hoy el logo
                self.assertGreaterEqual(min(burbuja), self.dock.ALTO)
                self.assertLessEqual(max(burbuja), self.dock.ALTO + 2)
            finally:
                orbe.destroy()

    def test_tooltip(self):
        self.assertEqual(self.burbuja.boton.get_tooltip_text(), "pAnkI, tu agente virtual  (Super + N)")

    def test_lleva_la_marca_de_panki_a_34_px(self):
        hijo = self.burbuja.boton.get_child()
        self.assertIsInstance(hijo, self.Gtk.Image)
        self.assertEqual(hijo.get_pixel_size(), 34)

    def test_sin_el_archivo_de_la_marca_lleva_una_chispa_de_texto(self):
        pedidos = []
        with mock.patch.object(self.B, "asset", side_effect=lambda nombre: pedidos.append(nombre)):
            b = self.B.BurbujaPanki(self.app, lanzar=self.lanzar_falso)
        try:
            self.assertEqual(pedidos, ["panki-white.svg"])
            self.assertEqual(b.boton.get_label(), "✦")
        finally:
            b.destroy()

    def test_el_css_del_proyecto_carga_sin_errores(self):
        errores = []
        proveedor = self.Gtk.CssProvider()
        proveedor.connect("parsing-error", lambda _p, seccion, error: errores.append(
            f"línea {seccion.get_start_location().lines + 1}: {error.message}"))
        proveedor.load_from_path(CSS)
        self.assertEqual(errores, [])

    # ------------------------------------------------------------- pulsar
    def test_pulsar_alterna_la_ventana_de_chat(self):
        self.respuestas[tuple(self.B.ALTERNAR)] = True
        self.burbuja.boton.emit("clicked")
        self.assertEqual(self.llamadas, [["gapplication", "action", "org.infinity.Panki", "alternar"]])

    def test_si_alternar_falla_lanza_infinity_panki(self):
        self.respuestas[tuple(self.B.ALTERNAR)] = False
        self.burbuja.boton.emit("clicked")
        self.assertEqual(self.llamadas, [["gapplication", "action", "org.infinity.Panki", "alternar"],
                                         ["infinity-panki"]])

    def test_infinity_panki_se_lanza_sin_esperarlo(self):
        # La app sigue viva mientras el chat esté abierto: esperar su fin dejaría la burbuja muda.
        self.respuestas[tuple(self.B.ALTERNAR)] = False
        self.burbuja.boton.emit("clicked")
        self.assertEqual(self.pendientes, [])
        self.burbuja.boton.emit("clicked")  # y la burbuja sigue respondiendo
        self.assertEqual(len(self.llamadas), 4)

    def test_no_lanza_nada_mientras_espera_la_respuesta_de_alternar(self):
        self.burbuja.boton.emit("clicked")
        self.assertEqual(self.llamadas, [self.B.ALTERNAR])
        self.burbuja.boton.emit("clicked")  # doble clic: no abre y cierra a la vez
        self.assertEqual(self.llamadas, [self.B.ALTERNAR])
        argv, al_terminar = self.pendientes.pop()
        al_terminar(True)
        self.burbuja.boton.emit("clicked")  # ya terminó: vuelve a funcionar
        self.assertEqual(self.llamadas, [self.B.ALTERNAR, self.B.ALTERNAR])

    def test_un_fallo_inmediato_no_deja_la_burbuja_bloqueada(self):
        def revienta(argv, al_terminar=None):
            raise RuntimeError("fallo")
        b = self.B.BurbujaPanki(self.app, lanzar=revienta)
        try:
            for _ in range(2):  # si el ejecutor revienta, la burbuja no se queda "ocupada" para siempre
                with self.assertRaises(RuntimeError):
                    b._pulsar()
        finally:
            b.destroy()

    def test_el_ejecutor_por_defecto_es_el_real(self):
        b = self.B.BurbujaPanki(self.app)
        try:
            self.assertIs(b._lanzar, self.B.lanzar_proceso)
        finally:
            b.destroy()

    # --------------------------------------------------- ejecutor real (Gio)
    def lanzar_real(self, argv, esperar=2.0):
        """Ejecuta `argv` con lanzar_proceso y bombea el bucle hasta que avisa. Devuelve (resultado, tardó)."""
        resultados = []
        t0 = time.time()
        self.B.lanzar_proceso(argv, resultados.append)
        devolvio_en = time.time() - t0
        fin = time.time() + esperar
        while not resultados and time.time() < fin:
            self.pumpa(0.01)
        return (resultados or [None])[0], devolvio_en

    @unittest.skipUnless(shutil.which("true") and shutil.which("false"), "faltan true y false")
    def test_lanzar_proceso_avisa_del_resultado(self):
        self.assertTrue(self.lanzar_real(["true"])[0] is True)
        self.assertTrue(self.lanzar_real(["false"])[0] is False)

    def test_lanzar_proceso_con_un_programa_que_no_existe_avisa_de_que_fallo(self):
        with mock.patch("sys.stderr"):
            self.assertIs(self.lanzar_real(["panki-programa-que-no-existe"])[0], False)

    @unittest.skipUnless(shutil.which("sleep"), "falta sleep")
    def test_lanzar_proceso_no_bloquea_el_bucle(self):
        resultado, tardo = self.lanzar_real(["sleep", "0.4"], esperar=3)
        self.assertLess(tardo, 0.3, "lanzar_proceso esperó al proceso")
        self.assertIs(resultado, True)

    @unittest.skipUnless(shutil.which("sleep"), "falta sleep")
    def test_lanzar_proceso_sin_aviso_no_espera(self):
        t0 = time.time()
        self.B.lanzar_proceso(["sleep", "0.3"])
        self.assertLess(time.time() - t0, 0.2)
        self.pumpa(0.5)  # el hijo se recoge solo, sin zombis ni avisos

    # ----------------------------------------------------------- visibilidad
    def test_el_ajuste_en_false_la_oculta_y_en_true_la_muestra(self):
        b = self.burbuja
        self.assertTrue(b.actualizar_visibilidad(True))
        self.pumpa(0.05)
        self.assertTrue(b.get_visible())
        self.assertFalse(b.actualizar_visibilidad(False))
        self.pumpa(0.05)
        self.assertFalse(b.get_visible())
        self.assertFalse(b.get_mapped())
        self.assertTrue(b.actualizar_visibilidad(True))
        self.pumpa(0.05)
        self.assertTrue(b.get_visible())
        self.assertTrue(b.get_mapped())

    def test_un_ajuste_sin_definir_cuenta_como_activado(self):
        self.assertTrue(self.burbuja.actualizar_visibilidad(None))
        self.assertTrue(self.burbuja.get_visible())

    def test_repetir_el_mismo_ajuste_no_vuelve_a_presentar_la_ventana(self):
        self.burbuja.actualizar_visibilidad(True)
        with mock.patch.object(self.burbuja, "present") as presentar:
            self.burbuja.actualizar_visibilidad(True)
            self.burbuja.actualizar_visibilidad(None)
        presentar.assert_not_called()


if __name__ == "__main__":
    unittest.main()
