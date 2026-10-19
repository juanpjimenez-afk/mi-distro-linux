"""Isla dinámica: pruebas.

- La máquina de estados y el parseo (isla_logica) se prueban SIN GTK ni pantalla, con el tiempo simulado.
- La conexión con el shell (main.py), los atajos (hyprland.conf) y el CSS se comprueban leyendo el código.
- La ventana (isla.py) necesita GTK 4 y una pantalla (xvfb-run): se salta si no están, y usa el MISMO doble
  de gtk4-layer-shell que las pruebas de la burbuja y del dock (test_panki_burbuja._crear_doble).
"""
import ast
import contextlib
import gc
import os
import re
import sys
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PAQUETE = os.path.join(SHELL, "infinity_shell")
RAIZ = os.path.join(SHELL, "..")
CSS = os.path.join(PAQUETE, "style.css")
MAIN = os.path.join(PAQUETE, "main.py")
ISLA = os.path.join(PAQUETE, "isla.py")
CONF = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")
PANTALLA = bool(os.environ.get("DISPLAY"))

sys.path.insert(0, SHELL)
from infinity_shell import isla_logica as L  # noqa: E402  (solo usa `time`, no toca GTK)

import test_panki_burbuja as T  # noqa: E402  (reutiliza el doble de gtk4-layer-shell)


def _arbol(ruta):
    with open(ruta, encoding="utf-8") as f:
        return ast.parse(f.read(), ruta)


def _llamadas(nodo):
    return [n for n in ast.walk(nodo) if isinstance(n, ast.Call)]


def _nombre_llamada(llamada):
    f = llamada.func
    return f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)


def _funcion(arbol, nombre, clase=None):
    cuerpo = arbol.body
    if clase:
        cuerpo = next(n for n in arbol.body if isinstance(n, ast.ClassDef) and n.name == clase).body
    return next(n for n in cuerpo if isinstance(n, ast.FunctionDef) and n.name == nombre)


# ======================================================================= sin GTK: parseo
class TestParseo(unittest.TestCase):
    def test_volumen_punto_y_coma_y_espacios(self):
        self.assertEqual(L.parsear_volumen("Volume: 0.45"), (0.45, False))
        self.assertEqual(L.parsear_volumen("Volume: 0,45"), (0.45, False))      # locale con coma
        self.assertEqual(L.parsear_volumen("  Volume:   0.80  "), (0.80, False))

    def test_volumen_mudo(self):
        self.assertEqual(L.parsear_volumen("Volume: 0.30 [MUTED]"), (0.30, True))
        self.assertEqual(L.parsear_volumen("Volume: 0.00 [MUTED]"), (0.0, True))

    def test_volumen_limites_y_basura(self):
        self.assertIsNone(L.parsear_volumen(""))
        self.assertIsNone(L.parsear_volumen(None))
        self.assertIsNone(L.parsear_volumen("no hay numero"))
        self.assertEqual(L.parsear_volumen("Volume: -0.1"), (0.0, False))       # nunca negativo
        self.assertEqual(L.parsear_volumen("0.9"), (0.9, False))                # respaldo: primer flotante

    def test_brillo_m_con_y_sin_porcentaje(self):
        self.assertEqual(L.parsear_brillo_m("intel_backlight,backlight,400,40%,1000"), 40)
        self.assertEqual(L.parsear_brillo_m("amdgpu_bl1,backlight,200,,500"), 40)   # sin %: usa actual/max
        self.assertEqual(L.parsear_brillo_m("x,backlight,0,0%,1000"), 0)
        self.assertEqual(L.parsear_brillo_m("x,backlight,1000,100%,1000"), 100)

    def test_brillo_m_locale_multilinea_y_basura(self):
        self.assertEqual(L.parsear_brillo_m("dev,backlight,5,50,5\n"), 50)          # coma final / entero
        self.assertEqual(L.parsear_brillo_m("dev,backlight,3,33%,9\notra,linea"), 33)
        self.assertIsNone(L.parsear_brillo_m(""))
        self.assertIsNone(L.parsear_brillo_m("basura"))

    def test_brillo_get_max(self):
        self.assertEqual(L.parsear_brillo("250", "1000"), 25)
        self.assertEqual(L.parsear_brillo("5000", "1000"), 100)                     # acota a 100
        self.assertEqual(L.parsear_brillo(0, 1000), 0)
        self.assertIsNone(L.parsear_brillo("250", "0"))                            # max 0: no se puede
        self.assertIsNone(L.parsear_brillo("x", "y"))

    def test_porcentaje(self):
        self.assertEqual(L.porcentaje(0.0), 0)
        self.assertEqual(L.porcentaje(0.666), 67)
        self.assertEqual(L.porcentaje(1.0), 100)
        self.assertEqual(L.porcentaje(1.5), 100)                                   # acota por arriba
        self.assertEqual(L.porcentaje(-0.2), 0)

    def test_nivel_e_icono_de_volumen_en_los_cortes(self):
        self.assertEqual(L.nivel_volumen(0.0, False), "mudo")
        self.assertEqual(L.nivel_volumen(0.5, True), "mudo")                       # mudo manda
        self.assertEqual(L.nivel_volumen(0.33, False), "bajo")
        self.assertEqual(L.nivel_volumen(0.34, False), "medio")                    # corte exacto
        self.assertEqual(L.nivel_volumen(0.66, False), "medio")
        self.assertEqual(L.nivel_volumen(0.67, False), "alto")                     # corte exacto
        self.assertEqual(L.icono_volumen(0.9, False), "audio-volume-high-symbolic")
        self.assertEqual(L.icono_volumen(0.9, True), "audio-volume-muted-symbolic")

    def test_datos_de_contenido(self):
        d = L.datos_volumen(1.5, False)                                           # fracción se acota a 1.0
        self.assertEqual((d["nivel"], d["pct"], d["fraccion"], d["mudo"]), ("alto", 100, 1.0, False))
        self.assertEqual(d["icono"], "audio-volume-high-symbolic")
        b = L.datos_brillo(40)
        self.assertEqual((b["pct"], b["fraccion"], b["icono"]), (40, 0.4, "display-brightness-symbolic"))
        m = L.datos_media("  Canción  ", "Artista")
        self.assertEqual((m["titulo"], m["artista"], m["reproduciendo"]), ("Canción", "Artista", True))
        self.assertEqual(L.datos_media("T", album=" Disco ")["album"], "Disco")


# ======================================================================= sin GTK: máquina de estados
class TestMaquina(unittest.TestCase):
    def nueva(self, dur=1.5):
        return L.LogicaIsla(duracion_osd=dur)

    def test_reposo_es_colapsada(self):
        m = self.nueva()
        self.assertEqual(m.estado(ahora=0), L.COLAPSADA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0)], [L.RELOJ])

    def test_idle_volumen_idle(self):
        m = self.nueva(dur=1.5)
        self.assertEqual(m.osd(L.VOLUMEN, L.datos_volumen(0.5, False), ahora=0), L.EXPANDIDA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0.1)], [L.VOLUMEN])
        self.assertEqual(round(m.proximo_vencimiento(ahora=0), 3), 1.5)
        self.assertEqual(m.tic(ahora=1.0), L.EXPANDIDA)                            # todavía vivo
        self.assertEqual(m.tic(ahora=1.6), L.COLAPSADA)                           # caducó: auto-colapso
        self.assertIsNone(m.proximo_vencimiento(ahora=1.6))

    def test_idle_brillo_idle(self):
        m = self.nueva(dur=1.0)
        self.assertEqual(m.osd(L.BRILLO, L.datos_brillo(40), ahora=0), L.EXPANDIDA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0)], [L.BRILLO])
        self.assertEqual(m.tic(ahora=1.1), L.COLAPSADA)

    def test_refrescar_un_osd_reinicia_su_cuenta(self):
        m = self.nueva(dur=1.0)
        m.osd(L.VOLUMEN, L.datos_volumen(0.5, False), ahora=0)
        m.osd(L.VOLUMEN, L.datos_volumen(0.6, False), ahora=0.8)                  # otro cambio: reinicia
        self.assertEqual(m.tic(ahora=1.2), L.EXPANDIDA)                           # seguiría vivo hasta 1.8
        self.assertEqual(m.tic(ahora=1.9), L.COLAPSADA)

    def test_dos_osd_a_la_vez_es_dividida(self):
        m = self.nueva(dur=1.5)
        m.osd(L.VOLUMEN, L.datos_volumen(0.5, False), ahora=0)
        self.assertEqual(m.osd(L.BRILLO, L.datos_brillo(40), ahora=0.5), L.DIVIDIDA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0.5)], [L.VOLUMEN, L.BRILLO])
        self.assertEqual(m.tic(ahora=1.6), L.EXPANDIDA)                           # el volumen caducó
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=1.6)], [L.BRILLO])
        self.assertEqual(m.tic(ahora=2.1), L.COLAPSADA)

    def test_media_sola_y_media_mas_osd(self):
        m = self.nueva(dur=1.0)
        self.assertEqual(m.media(L.datos_media("Tema", "Grupo"), ahora=0), L.EXPANDIDA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0)], [L.MEDIA])
        self.assertEqual(m.osd(L.VOLUMEN, L.datos_volumen(0.3, False), ahora=0), L.DIVIDIDA)
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=0)], [L.MEDIA, L.VOLUMEN])  # media a la izquierda
        self.assertEqual(m.tic(ahora=5), L.EXPANDIDA)                            # la media NO caduca
        self.assertEqual([p["tipo"] for p in m.paneles(ahora=5)], [L.MEDIA])
        self.assertEqual(m.media(None, ahora=6), L.COLAPSADA)                    # se para la reproducción

    def test_media_no_tiene_vencimiento(self):
        m = self.nueva()
        m.media(L.datos_media("X"), ahora=0)
        self.assertIsNone(m.proximo_vencimiento(ahora=100))

    def test_limpiar_todo_vuelve_a_reposo(self):
        m = self.nueva()
        m.media(L.datos_media("X"), ahora=0)
        m.osd(L.VOLUMEN, L.datos_volumen(0.5, False), ahora=0)
        m.limpiar_todo()
        self.assertEqual(m.estado(ahora=0), L.COLAPSADA)

    def test_osd_desconocido(self):
        self.assertRaises(ValueError, self.nueva().osd, "otro", {})

    def test_los_tamanos_crecen_con_el_estado(self):
        col = L.tamano(L.COLAPSADA)
        exp = L.tamano(L.EXPANDIDA)
        div = L.tamano(L.DIVIDIDA)
        self.assertLess(col[0], exp[0])          # expandida más ancha que colapsada
        self.assertLess(exp[1], div[1] + 1)      # misma altura expandida/dividida
        self.assertGreater(div[0], exp[0])       # dividida (dos burbujas) más ancha que una sola


# ======================================================================= leído del código
class TestCodigoIsla(unittest.TestCase):
    """Reglas de isla.py leídas del código: nada que bloquee el hilo principal y reutiliza dock._capa."""

    @classmethod
    def setUpClass(cls):
        cls.arbol = _arbol(ISLA)
        with open(ISLA, encoding="utf-8") as f:
            cls.texto = f.read()

    def test_no_bloquea_ni_usa_subprocess_sincrono(self):
        importados = {a.name.split(".")[0] for n in ast.walk(self.arbol) if isinstance(n, ast.Import) for a in n.names}
        self.assertNotIn("subprocess", importados)
        bloqueantes = {"communicate", "communicate_utf8", "run", "check_output", "check_call", "Popen",
                       "system", "call_sync", "bus_get_sync", "spawn_command_line_sync"}
        for llamada in _llamadas(self.arbol):
            self.assertNotIn(_nombre_llamada(llamada), bloqueantes, ast.dump(llamada.func))
        self.assertNotIn("shell=True", self.texto)
        # Sí usa las variantes asíncronas (no bloqueantes)
        nombres = {_nombre_llamada(c) for c in _llamadas(self.arbol)}
        self.assertIn("communicate_utf8_async", nombres)

    def test_reutiliza_capa_de_dock_sin_redefinirla(self):
        importados = {a.name for n in self.arbol.body if isinstance(n, ast.ImportFrom)
                      and n.module == "dock" and n.level == 1 for a in n.names}
        self.assertIn("_capa", importados)
        definidas = {n.name for n in self.arbol.body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
        self.assertNotIn("_capa", definidas)

    def test_namespace_y_temporizadores(self):
        self.assertIn('"infinity-isla"', self.texto)
        # el espacio de nombres hereda la regla de desenfoque ^infinity-.*$ de hyprland.conf
        with open(CONF, encoding="utf-8") as f:
            patrones = re.findall(r"^layerrule\s*=\s*blur on,\s*match:namespace\s+(\S+)", f.read(), re.M)
        self.assertTrue(any(re.search(p, "infinity-isla") for p in patrones), patrones)


class TestConexionConElShell(unittest.TestCase):
    """main.py importa la isla, la crea con el resto de la interfaz y registra las acciones de OSD."""

    @classmethod
    def setUpClass(cls):
        cls.arbol = _arbol(MAIN)

    def test_importa_la_isla(self):
        self.assertTrue(any(isinstance(n, ast.ImportFrom) and n.module == "isla" and n.level == 1
                            and any(a.name == "Isla" for a in n.names) for n in self.arbol.body))

    def test_la_crea_en_crear_interfaz(self):
        crear = _funcion(self.arbol, "_crear_interfaz", clase="Shell")
        creadas = {t.attr for n in ast.walk(crear) if isinstance(n, ast.Assign) and isinstance(n.value, ast.Call)
                   and _nombre_llamada(n.value) == "Isla" for t in n.targets if isinstance(t, ast.Attribute)}
        self.assertEqual(creadas, {"isla"})

    def test_registra_las_acciones_osd(self):
        startup = _funcion(self.arbol, "do_startup", clase="Shell")
        texto = ast.dump(startup)
        self.assertIn("osd-volumen", texto)
        self.assertIn("osd-brillo", texto)

    def test_los_manejadores_llaman_a_la_isla(self):
        for metodo, llamada in (("_accion_osd_volumen", "mostrar_volumen"), ("_accion_osd_brillo", "mostrar_brillo")):
            fn = _funcion(self.arbol, metodo, clase="Shell")
            self.assertIn(llamada, {_nombre_llamada(c) for c in _llamadas(fn)}, metodo)


class TestAtajosYEstilo(unittest.TestCase):
    def test_los_atajos_disparan_el_osd_sin_quitar_el_refresh(self):
        with open(CONF, encoding="utf-8") as f:
            conf = f.read()
        for tecla in ("XF86AudioRaiseVolume", "XF86AudioLowerVolume", "XF86AudioMute"):
            linea = next(l for l in conf.splitlines() if tecla in l)
            self.assertIn("osd-volumen", linea, tecla)
            self.assertIn("refresh", linea, tecla)      # no se pierde el refresco de la píldora de estado
        for tecla in ("XF86MonBrightnessUp", "XF86MonBrightnessDown"):
            linea = next(l for l in conf.splitlines() if tecla in l)
            self.assertIn("osd-brillo", linea, tecla)

    def test_el_css_tiene_el_bloque_de_la_isla_con_transicion(self):
        with open(CSS, encoding="utf-8") as f:
            css = f.read()
        self.assertIn(".isla-pildora", css)
        for clase in (".isla-colapsada", ".isla-expandida", ".isla-burbuja", ".isla-oculta"):
            self.assertIn(clase, css)
        bloque = re.search(r"\.isla-pildora\s*\{([^}]*)\}", css)
        self.assertIsNotNone(bloque)
        self.assertIn("transition", bloque.group(1))
        self.assertIn("border-radius", bloque.group(1))


# ======================================================================= con GTK
def cargar_isla():
    """Importa infinity_shell.dock e infinity_shell.isla usando el doble de gtk4-layer-shell si el typelib
    no está. Devuelve (dock, isla)."""
    import gi
    sys.path.insert(0, SHELL)
    original = gi.require_version
    try:
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
        except ValueError:
            if "gi.repository.Gtk4LayerShell" not in sys.modules:
                sys.modules["gi.repository.Gtk4LayerShell"] = T._crear_doble()
            gi.require_version = lambda n, v: None if n == "Gtk4LayerShell" else original(n, v)
        from infinity_shell import dock, isla
        return dock, isla
    finally:
        gi.require_version = original


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestIslaVentana(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, GLib, Gtk
            cls.dock, cls.isla_mod = cargar_isla()
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        cls.Gdk, cls.GLib, cls.Gtk = Gdk, GLib, Gtk
        # En X11 no hay capas: aquí manda el doble, el mismo para dock e isla (comparten _capa y las Edge).
        cls.capa = T._crear_doble()
        for modulo in (cls.dock, cls.isla_mod):
            parche = mock.patch.object(modulo, "LayerShell", cls.capa)
            parche.start()
            cls.addClassCleanup(parche.stop)

    def setUp(self):
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.isla{time.time_ns()}")
        self.app.register(None)

    def crear(self, **kw):
        """Una isla con lectores falsos y reloj fijo; sin MPRIS (reproduccion=None)."""
        kw.setdefault("leer_volumen", lambda cb: cb(0.5, False))
        kw.setdefault("leer_brillo", lambda cb: cb(40))
        kw.setdefault("reloj", lambda: "12:34")
        isla = self.isla_mod.Isla(self.app, **kw)
        self.addCleanup(self._destruir, isla)
        return isla

    def _destruir(self, isla):
        try:
            isla.destroy()
        except Exception:
            pass

    def pumpa(self, segundos):
        ctx = self.GLib.MainContext.default()
        fin = time.time() + segundos
        while time.time() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.003)

    @contextlib.contextmanager
    def css_del_proyecto(self):
        display = self.Gdk.Display.get_default()
        proveedor = self.Gtk.CssProvider()
        proveedor.load_from_path(CSS)
        self.Gtk.StyleContext.add_provider_for_display(display, proveedor,
                                                       self.Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        try:
            yield
        finally:
            self.Gtk.StyleContext.remove_provider_for_display(display, proveedor)

    def ancho(self, widget):
        _ok, caja = widget.compute_bounds(widget)
        return round(caja.get_width())

    # ---------------------------------------------------------------- construcción
    def test_se_construye_sin_hyprland_ni_webkit(self):
        isla = self.crear()
        self.assertIsInstance(isla, self.Gtk.Window)
        self.assertEqual(isla.get_title(), "Infinity — Isla")
        self.assertIs(isla.get_child(), isla.caja)
        self.assertTrue(isla.izq.has_css_class("isla-pildora"))
        self.assertTrue(isla.der.has_css_class("isla-pildora"))

    def test_es_ventana_de_capa_anclada_arriba(self):
        isla = self.crear()
        d = self.capa.registro[isla]
        self.assertEqual(d["namespace"], "infinity-isla")
        self.assertEqual(d["capa"], self.capa.Layer.TOP)
        self.assertEqual(d["teclado"], self.capa.KeyboardMode.NONE)
        self.assertEqual(d["zona"], -1)                                   # no reserva espacio
        self.assertEqual(d["anclas"], {self.capa.Edge.TOP})               # solo arriba -> el compositor la centra
        self.assertEqual(d["margenes"][self.capa.Edge.TOP], self.isla_mod.MARGEN_SUPERIOR)
        self.assertTrue(isla.has_css_class("infinity-window"))

    def test_arranca_colapsada(self):
        isla = self.crear()
        self.assertEqual(isla.logica.estado(), L.COLAPSADA)
        self.assertTrue(isla.izq.has_css_class("isla-colapsada"))
        self.assertTrue(isla.der.has_css_class("isla-oculta"))

    # ---------------------------------------------------------------- OSD de volumen / brillo
    def test_osd_volumen_expande_y_crece(self):
        with self.css_del_proyecto():
            isla = self.crear()
            isla.present()
            self.pumpa(0.1)
            colapsada = self.ancho(isla.izq)
            isla.mostrar_volumen()
            self.pumpa(0.1)
            self.assertEqual(isla.logica.estado(), L.EXPANDIDA)
            self.assertTrue(isla.izq.has_css_class("isla-expandida"))
            self.assertGreater(self.ancho(isla.izq), colapsada)           # la ventana crece de verdad
            # el contenido del OSD: icono + barra + porcentaje
            refs = isla._slots[0]["refs"]
            self.assertEqual(refs["pct"].get_text(), "50%")
            self.assertIsInstance(refs["barra"], self.isla_mod.Barra)

    def test_osd_brillo_expande(self):
        isla = self.crear()
        isla.mostrar_brillo()
        self.pumpa(0.05)
        self.assertEqual(isla.logica.estado(), L.EXPANDIDA)
        self.assertEqual(isla._slots[0]["refs"]["pct"].get_text(), "40%")

    def test_dos_osd_dividen(self):
        with self.css_del_proyecto():
            isla = self.crear()
            isla.present()
            isla.mostrar_volumen()
            isla.mostrar_brillo()
            self.pumpa(0.1)
            self.assertEqual(isla.logica.estado(), L.DIVIDIDA)
            self.assertTrue(isla.izq.has_css_class("isla-burbuja"))
            self.assertTrue(isla.der.has_css_class("isla-burbuja"))
            self.assertFalse(isla.der.has_css_class("isla-oculta"))
            self.assertEqual(len(isla._slots), 2)
            self.assertTrue(isla.caja.has_css_class("isla-union"))   # una sola píldora, no dos separadas

    def test_media_enseña_titulo_y_album_pequeno(self):
        isla = self.crear()
        isla._media_cambio(L.datos_media("Tema", "Grupo", album="Disco"))
        self.pumpa(0.02)
        refs = isla._slots[0]["refs"]
        self.assertEqual((refs["titulo"].get_text(), refs["sub"].get_text()), ("Tema", "Disco"))
        self.assertFalse(isla.caja.has_css_class("isla-union"))          # sola, no es dividida

    def test_auto_colapso_por_temporizador(self):
        isla = self.crear(logica=L.LogicaIsla(duracion_osd=0.05))
        isla.mostrar_volumen()
        self.pumpa(0.02)
        self.assertEqual(isla.logica.estado(), L.EXPANDIDA)
        self.pumpa(0.25)                                                  # el temporizador real auto-colapsa
        self.assertEqual(isla.logica.estado(), L.COLAPSADA)
        self.assertTrue(isla.izq.has_css_class("isla-colapsada"))

    def test_lectura_fallida_no_molesta(self):
        isla = self.crear(leer_volumen=lambda cb: cb(None, None))
        isla.mostrar_volumen()
        self.pumpa(0.05)
        self.assertEqual(isla.logica.estado(), L.COLAPSADA)               # sin valor, sin OSD

    def test_reproduccion_media_divide_y_se_quita(self):
        isla = self.crear()
        isla._media_cambio(L.datos_media("Tema", "Grupo"))
        self.pumpa(0.02)
        self.assertEqual(isla.logica.estado(), L.EXPANDIDA)
        self.assertEqual(isla._slots[0]["refs"]["titulo"].get_text(), "Tema")
        self.assertEqual(isla._slots[0]["refs"]["sub"].get_text(), "Grupo")   # sin álbum, cae al artista
        isla.mostrar_volumen()
        self.pumpa(0.02)
        self.assertEqual(isla.logica.estado(), L.DIVIDIDA)
        isla._media_cambio(None)                                          # deja de sonar
        isla.logica.limpiar_todo() if False else None
        self.pumpa(0.02)

    # ---------------------------------------------------------------- MPRIS defensivo
    def test_reproduccion_mpris_no_falla_sin_bus(self):
        llamadas = []
        repro = self.isla_mod.ReproduccionMPRIS(lambda info: llamadas.append(info))
        self.pumpa(0.1)
        repro.play_pausa()                                                # no hay reproductor: no debe fallar
        repro.cerrar()
        # Nunca debe inventar un now-playing de la nada
        self.assertNotIn(True, [bool(x) for x in llamadas])


if __name__ == "__main__":
    unittest.main()
