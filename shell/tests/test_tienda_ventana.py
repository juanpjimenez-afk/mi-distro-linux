"""La ventana de la Tienda (tienda_app.py), con GTK 4.

Como test_boveda_ventana.py: se salta sin pantalla (DISPLAY) o sin GTK 4; corre con xvfb-run. Todo es FALSO: ni apt, ni
flatpak, ni red, ni subprocesos de verdad. Las pruebas usan dos dobles del gestor:

  * un tienda_ops.Gestor REAL con un ejecutor manual (no ejecuta nada: guarda el plan y la prueba lo termina cuando
    quiere), estados inventados y planes vacíos. Sirve para ver la ventana reaccionar a lo que el Gestor cuenta
    (en cola, en curso con progreso, fallo, el aviso de fin de trabajo…);
  * un GestorFalso que solo registra lo que la ventana le pide (qué id, qué acción, con qué navegador, si se fuerza) y
    devuelve las Respuestas que la prueba le ponga (falta_navegador, poco_espacio, error…).

Además, cada prueba vigila que GTK no suelte ningún aviso (Gtk-CRITICAL, Gtk-WARNING…): una altura mínima mal medida o una
regla de CSS que no existe se notan aquí.
"""
import ctypes
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")
PANTALLA = bool(os.environ.get("DISPLAY"))


class EjecutorManual:
    """No ejecuta nada: guarda cada plan y deja que la prueba lo termine cuando quiera."""

    def __init__(self, O):
        self.O = O
        self.pendientes = []
        self.historial = []

    def ejecutar(self, plan, al_evento, al_terminar):
        self.pendientes.append((plan, al_evento, al_terminar))
        self.historial.append(plan)

    def evento(self, tipo, valor, texto, indice=0):
        self.pendientes[indice][1](self.O.Evento(tipo, valor, texto))

    def terminar(self, ok=True, mensaje="", indice=0):
        _plan, _ev, fin = self.pendientes.pop(indice)
        fin(ok, mensaje)


class GestorFalso:
    """Lo que la ventana espera de un Gestor, sin hacer nada: registra lo que se le pide."""

    def __init__(self, O, catalogo):
        self.O = O
        self.catalogo = catalogo
        self.estados = {}
        self.trabajos = {}
        self.llamadas = []                # (ident, accion, kwargs) de cada pedir()
        self.respuestas = []              # lo que devolverá cada pedir(), en orden; sin más, «ok»
        self.sacados = []
        self.saca = True
        self.refrescos = 0
        self.ocupado = False
        self.al_cambiar = lambda ident: None
        self.al_terminar_trabajo = lambda trabajo: None

    def estado(self, ident):
        return self.estados.get(ident, self.O.SIN_INSTALAR)

    def trabajo(self, ident):
        t = self.trabajos.get(ident)
        return t if t is not None and t.activo else None

    def pedir(self, ident, accion, **kwargs):
        self.llamadas.append((ident, accion, kwargs))
        return self.respuestas.pop(0) if self.respuestas else self.O.Respuesta("ok", "", ())

    def quitar_de_la_cola(self, ident):
        self.sacados.append(ident)
        return self.saca

    def refrescar(self):
        self.refrescos += 1
        self.al_cambiar(None)


class RatonX:
    """Ratón y teclado REALES para el servidor X (la extensión XTest, con ctypes): no se llama a ningún método de GTK, el
    servidor entrega los eventos como si los hiciera una persona. Es la única forma de comprobar de verdad que un clic llega
    a un botón de la rejilla (el Gtk.FlowBox se los comía). Lanza OSError si no hay libX11/libXtst o el servidor no tiene XTest."""

    KEYSYM_ESC, KEYSYM_CONTROL = 0xFF1B, 0xFFE3

    def __init__(self):
        self.x11 = ctypes.CDLL("libX11.so.6")
        self.xtst = ctypes.CDLL("libXtst.so.6")
        p, i, ul = ctypes.c_void_p, ctypes.c_int, ctypes.c_ulong
        self.x11.XOpenDisplay.restype, self.x11.XOpenDisplay.argtypes = p, [ctypes.c_char_p]
        self.x11.XFlush.argtypes = [p]
        self.x11.XKeysymToKeycode.restype, self.x11.XKeysymToKeycode.argtypes = ctypes.c_ubyte, [p, ul]
        self.xtst.XTestQueryExtension.argtypes = [p] + [ctypes.POINTER(i)] * 4
        self.xtst.XTestFakeMotionEvent.argtypes = [p, i, i, i, ul]
        self.xtst.XTestFakeButtonEvent.argtypes = [p, ctypes.c_uint, i, ul]
        self.xtst.XTestFakeKeyEvent.argtypes = [p, ctypes.c_uint, i, ul]
        self.dpy = self.x11.XOpenDisplay(None)
        if not self.dpy:
            raise OSError("no se pudo abrir el servidor X")
        a, b, c, d = (ctypes.c_int() for _ in range(4))
        if not self.xtst.XTestQueryExtension(self.dpy, a, b, c, d):
            raise OSError("el servidor X no tiene la extensión XTest")

    def mover(self, x, y):
        self.xtst.XTestFakeMotionEvent(self.dpy, -1, int(x), int(y), 0)
        self.x11.XFlush(self.dpy)

    def boton(self, abajo, numero=1):
        self.xtst.XTestFakeButtonEvent(self.dpy, numero, 1 if abajo else 0, 0)
        self.x11.XFlush(self.dpy)

    def tecla(self, keysym, control=False):
        codigo = self.x11.XKeysymToKeycode(self.dpy, keysym)
        ctrl = self.x11.XKeysymToKeycode(self.dpy, self.KEYSYM_CONTROL)
        if control:
            self.xtst.XTestFakeKeyEvent(self.dpy, ctrl, 1, 0)
        self.xtst.XTestFakeKeyEvent(self.dpy, codigo, 1, 0)
        self.xtst.XTestFakeKeyEvent(self.dpy, codigo, 0, 0)
        if control:
            self.xtst.XTestFakeKeyEvent(self.dpy, ctrl, 0, 0)
        self.x11.XFlush(self.dpy)


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class BaseVentana(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, Gio, GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        sys.path.insert(0, SHELL)
        from infinity_shell import ajustes, tienda_app as X, tienda_catalogo as C, tienda_ops as O, tienda_webapps as W
        cls.X, cls.C, cls.O, cls.W, cls.ajustes = X, C, O, W, ajustes
        cls.Gdk, cls.Gio, cls.GLib, cls.Gtk = Gdk, Gio, GLib, Gtk
        cls.PERSISTENTE = ajustes.Persistencia("instalado", "Se guardan tus cambios al apagar", "instalado en el disco", "")
        cls.VIVO = ajustes.Persistencia("vivo", "Modo en vivo", "Sesión limpia", "Reinicia y elige la primera opción del menú.")

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.datos = os.path.join(self.tmp, "datos")     # el XDG_DATA_HOME de la prueba: ahí buscan sus lanzadores las WebApps
        os.makedirs(self.datos)
        entorno = mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.datos})
        entorno.start()
        self.addCleanup(entorno.stop)
        # GTK no debe soltar avisos: se recogen y, al acabar (este cleanup es el último en correr), se exige que no haya
        self.avisos_gtk = []
        niveles = self.GLib.LogLevelFlags.LEVEL_CRITICAL | self.GLib.LogLevelFlags.LEVEL_WARNING
        manejadores = [(d, self.GLib.log_set_handler(d, niveles, lambda dom, n, msg, _u: self.avisos_gtk.append(f"{dom}: {msg}"), None))
                       for d in ("Gtk", "Gdk")]
        self.addCleanup(self._sin_avisos_de_gtk, manejadores)
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.tienda{time.time_ns()}")
        self.app.register(None)
        self.catalogo = self.C.cargar(CATALOGO)
        self.espacio = {"poco": False}
        self.hay_navegador = False
        self.instaladas = {}                  # id → Estado: lo que «ve» el sistema
        self.ej = EjecutorManual(self.O)
        self.abiertos, self.urls = [], []
        # ni apt, ni flatpak, ni pkexec: los planes son vacíos y el espacio es el que diga la prueba
        self.parchear(self.O, "plan_para", lambda e, a, b=None, ayudante=None, preferido=None, datos=None: self.O.Plan(e, a, []))
        self.parchear(self.O, "comprobar_espacio", lambda e, statvfs=None, home=None: (
            self.O.AvisoEspacio(10, 500, "/") if self.espacio["poco"] else None))

    def _sin_avisos_de_gtk(self, manejadores):
        for dominio, ident in manejadores:
            self.GLib.log_remove_handler(dominio, ident)
        self.assertEqual(self.avisos_gtk, [], "GTK ha soltado avisos")

    def parchear(self, objeto, nombre, valor):
        p = mock.patch.object(objeto, nombre, valor)
        p.start()
        self.addCleanup(p.stop)

    # ------------------------------------------------------------------------------------ construcción
    def buscar_ejecutable(self, nombre):
        return "/usr/bin/google-chrome-stable" if self.hay_navegador and nombre == "google-chrome-stable" else None

    def gestor(self):
        """Un Gestor real: ejecutor manual, estados de `self.instaladas` y navegador si `self.hay_navegador`."""
        return self.O.Gestor(self.catalogo, ejecutor=self.ej, estados=lambda: {
            a.id: self.instaladas.get(a.id, self.O.SIN_INSTALAR) for a in self.catalogo.apps},
            buscar=self.buscar_ejecutable, datos=self.datos, home=self.datos)

    def falso(self):
        return GestorFalso(self.O, self.catalogo)

    def ventana(self, gestor=None, **kw):
        opciones = dict(abrir=lambda entrada, como: self.abiertos.append((entrada.id, como)), abrir_url=self.urls.append,
                        persistencia=self.PERSISTENTE)
        opciones.update(kw)
        v = self.X.VentanaTienda(self.app, gestor=gestor or self.gestor(), **opciones)
        self.addCleanup(self._cerrar, v)
        return v

    def _cerrar(self, v):
        v.liberar()
        app = v.get_application()
        if app is not None and v in app.get_windows():
            v.destroy()

    def mostrar(self, v, segundos=0.3):
        v.present()
        self.pump(segundos)
        return v

    # ----------------------------------------------------------------------------------- utilidades
    def pump(self, segundos):
        ctx = self.GLib.MainContext.default()
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.002)

    def pump_hasta(self, condicion, limite=5.0):
        ctx = self.GLib.MainContext.default()
        fin = time.monotonic() + limite
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            if condicion():
                return True
            time.sleep(0.002)
        return condicion()

    def hijos(self, raiz):
        salida, h = [], raiz.get_first_child()
        while h is not None:
            salida.append(h)
            h = h.get_next_sibling()
        return salida

    def todos(self, raiz):
        """Todos los widgets bajo `raiz` (sin contar `raiz`), en orden de documento."""
        salida = []
        for h in self.hijos(raiz):
            salida.append(h)
            salida.extend(self.todos(h))
        return salida

    def textos(self, raiz):
        """TODO el texto que hay en los widgets bajo `raiz`: etiquetas, botones, campos y tooltips."""
        salida = []
        for w in [raiz] + self.todos(raiz):
            if isinstance(w, self.Gtk.Label):
                salida.append(w.get_text())
            if isinstance(w, self.Gtk.Button) and w.get_label():
                salida.append(w.get_label())
            if isinstance(w, self.Gtk.Editable):
                salida.append(w.get_text())
            if w.get_tooltip_text():
                salida.append(w.get_tooltip_text())
        return salida

    def botones(self, raiz):
        """Las etiquetas de los botones de texto a la vista que hay bajo `raiz`."""
        return [w.get_label() for w in self.todos(raiz) if isinstance(w, self.Gtk.Button) and w.get_label() and w.get_visible()]

    def menu_de(self, boton_menu):
        modelo = boton_menu.get_menu_model()
        return [modelo.get_item_attribute_value(i, "label", None).get_string() for i in range(modelo.get_n_items())]

    def tarjeta(self, v, ident):
        return v._tarjetas[ident]

    def filas(self, v):
        """Las filas del lateral (la lista también tiene como hijos las cabeceras de sección)."""
        return [w for w in self.hijos(v.lista_vistas) if isinstance(w, self.Gtk.ListBoxRow)]

    def trabajo_falso(self, ident, accion, estado, mensaje="", progreso=None, texto=""):
        t = self.O.Trabajo(self.catalogo.por_id[ident], accion)
        t.estado, t.mensaje, t.progreso, t.texto = estado, mensaje, progreso, texto
        return t

    def tecla(self, v, tecla, control=False):
        return v._tecla(None, tecla, 0, self.Gdk.ModifierType.CONTROL_MASK if control else 0)

    def menu_accion(self, tarjeta, nombre):
        """Lo que pasa al elegir «Quitar» o «Actualizar» en el menú ⋮ de una tarjeta. La acción corre en cuanto el bucle
        queda libre (no dentro del clic del menú), así que se le da un respiro."""
        tarjeta.activate_action(f"tarjeta.{nombre}", None)
        self.pump(0.03)

    def desktop(self, ident):
        """Deja el lanzador de una WebApp (con /bin/true como Exec: GLib no crea la ficha si el programa no existe)."""
        ruta = self.W.ruta_desktop(ident)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=Falsa\nExec=/bin/true\nX-Infinity-Tienda=webapp\nX-Infinity-Tienda-Id={ident}\n")
        return ruta


# ===================================================================================== funciones puras
class TestFunciones(BaseVentana):
    def test_formatear_tamano(self):
        f = self.X.formatear_tamano
        self.assertEqual([f(0), f(450), f(700), f(999), f(1000), f(1500), f(2000), f(10000)],
                         ["", "≈ 450 MB", "≈ 700 MB", "≈ 999 MB", "≈ 1 GB", "≈ 1,5 GB", "≈ 2 GB", "≈ 10 GB"])

    def ids(self, apps):
        return [a.id for a in apps]

    def test_filtrar_por_vista(self):
        filtrar, nadie = self.X.filtrar, (lambda i: False)
        self.assertEqual(self.ids(filtrar(self.catalogo, "todo", "", nadie)), [a.id for a in self.catalogo.apps])
        self.assertEqual(self.ids(filtrar(self.catalogo, "instaladas", "", lambda i: i in ("chrome", "spotify"))),
                         ["spotify", "chrome"], "en el orden del catálogo")
        self.assertEqual(self.ids(filtrar(self.catalogo, "instaladas", "", nadie)), [])
        self.assertEqual(self.ids(filtrar(self.catalogo, "cat:ia", "", nadie)), ["claude", "gemini", "notebooklm"])
        self.assertEqual(self.ids(filtrar(self.catalogo, "cat:no-existe", "", nadie)), [])

    def test_filtrar_busca_sin_tildes_y_dentro_de_la_vista(self):
        filtrar, nadie = self.X.filtrar, (lambda i: False)
        peliculas = self.ids(filtrar(self.catalogo, "todo", "pelicula", nadie))
        self.assertEqual(peliculas, self.ids(self.catalogo.buscar("pelicula")))
        self.assertIn("netflix", peliculas)
        self.assertIn("hbo-max", peliculas)
        self.assertEqual(self.ids(filtrar(self.catalogo, "cat:ia", "GEMINI", nadie)), ["gemini"])
        self.assertEqual(self.ids(filtrar(self.catalogo, "cat:ia", "netflix", nadie)), [], "la búsqueda respeta la categoría")
        self.assertEqual(self.ids(filtrar(self.catalogo, "instaladas", "netflix", lambda i: i == "claude")), [])

    def test_modo_de_cada_estado(self):
        O = self.O
        sin, instalada, rota = O.SIN_INSTALAR, O.Estado(True, "1", True, ""), O.Estado(True, "", False, "sin navegador")
        cola = self.trabajo_falso("spotify", O.INSTALAR, "en_cola")
        curso = self.trabajo_falso("spotify", O.INSTALAR, "en_curso")
        fallo = self.trabajo_falso("spotify", O.INSTALAR, "fallo", "no")
        listo = self.trabajo_falso("spotify", O.INSTALAR, "listo", "sí")
        casos = [(sin, None, None, "instalar"), (sin, cola, cola, "cola"), (sin, curso, curso, "curso"),
                 (instalada, None, None, "abrir"), (instalada, curso, curso, "curso"), (rota, None, None, "reparar"),
                 (sin, None, fallo, "fallo"), (instalada, None, fallo, "fallo"), (sin, None, listo, "instalar"),
                 (instalada, None, listo, "abrir")]
        for estado, trabajo, ultimo, esperado in casos:
            with self.subTest(esperado=esperado, instalada=estado.instalada, trabajo=getattr(trabajo, "estado", None),
                              ultimo=getattr(ultimo, "estado", None)):
                self.assertEqual(self.X.modo_de(estado, trabajo, ultimo), esperado)

    def parseo_css(self, texto):
        avisos = []
        proveedor = self.Gtk.CssProvider()
        proveedor.connect("parsing-error", lambda _p, _seccion, error: avisos.append(error.message))
        proveedor.load_from_string(texto)
        return avisos

    def test_el_estilo_se_carga_sin_errores(self):
        self.assertEqual(self.parseo_css(self.X.ESTILO), [])

    def test_el_estilo_es_translucido_con_el_acento_violeta_y_el_cian(self):
        e = self.X.ESTILO
        self.assertIn("window.tienda-ventana { background-color: rgba(15, 18, 38, 0.92);", e, "el fondo deja ver el desenfoque")
        self.assertNotIn("rgb(15, 18, 38)", e, "ni rastro del fondo opaco")
        for pieza in ("#b9a8ff", "#7cf0ff", "rgba(255, 255, 255, 0.04)", "border-radius: 18px", "JetBrains Mono", '"Inter"',
                      "window.tienda-ventana", ".tienda-tarjeta:hover"):
            self.assertIn(pieza, e)

    def test_colores_de_las_franjas(self):
        css = self.X.css_colores(self.catalogo.apps)
        self.assertEqual(len(css.splitlines()), 12)
        self.assertIn(".tienda-color-netflix { background-image: linear-gradient(90deg, rgba(229, 9, 20, 0.95)", css)
        self.assertEqual(self.parseo_css(css), [])

    def test_un_color_o_un_id_raros_no_llegan_al_css(self):
        import dataclasses
        netflix = self.catalogo.por_id["netflix"]
        raro = dataclasses.replace(netflix, color="rojo; } window { background: red")
        malo = dataclasses.replace(netflix, id="Malo id {")
        css = self.X.css_colores([raro, malo])
        self.assertIn("rgba(185, 168, 255, 0.95)", css, "un color que no es #rrggbb vuelve al violeta")
        self.assertNotIn("Malo", css)
        self.assertNotIn("red", css)
        self.assertEqual(len(css.splitlines()), 1)

    def png(self, ruta):
        """Un PNG válido de 1×1 px (no depende de ningún cargador de SVG)."""
        import struct
        import zlib

        def trozo(tipo, datos):
            return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos) & 0xFFFFFFFF)

        with open(ruta, "wb") as f:
            f.write(b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 6, 0, 0, 0))
                    + trozo(b"IDAT", zlib.compress(b"\x00\xb9\xa8\xff\xff")) + trozo(b"IEND", b""))
        return ruta

    def test_el_icono_de_una_app_con_imagen(self):
        ruta = self.png(os.path.join(self.tmp, "icono.png"))
        with mock.patch.object(self.C, "ruta_icono", return_value=ruta):
            img = self.X.imagen_de_app(self.catalogo.por_id["spotify"], 64)
        self.assertIsInstance(img, self.Gtk.Image)
        self.assertIsNotNone(img.get_paintable())
        self.assertEqual(img.get_pixel_size(), 64)

    def test_el_icono_que_falta_o_esta_roto_se_sustituye_por_un_simbolo(self):
        roto = os.path.join(self.tmp, "roto.svg")
        with open(roto, "w") as f:
            f.write("esto no es una imagen")
        for ruta in (None, roto, os.path.join(self.tmp, "no-existe.svg")):
            with self.subTest(ruta=ruta), mock.patch.object(self.C, "ruta_icono", return_value=ruta):
                img = self.X.imagen_de_app(self.catalogo.por_id["spotify"], 64)
                self.assertNotEqual(img.get_icon_name(), "image-missing")
                self.assertTrue(img.get_icon_name().endswith("-symbolic"))
                self.assertEqual(img.get_size_request(), (64, 64))

    def test_el_icono_de_la_tienda_esta_en_el_repositorio(self):
        ruta = self.X.ruta_icono_tienda()
        self.assertIsNotNone(ruta)
        self.assertTrue(ruta.endswith("tienda.svg"))


# ============================================================================== abrir apps y enlaces
class TestLanzar(BaseVentana):
    def lanzador(self, exec_):
        ruta = os.path.join(self.tmp, "falsa.desktop")
        with open(ruta, "w") as f:
            f.write(f"[Desktop Entry]\nType=Application\nName=Falsa\nExec={exec_}\n")
        return ruta

    def test_archivo_desktop(self):
        self.X.lanzar_app(("archivo", self.lanzador("/bin/true")), self.Gdk.Display.get_default())

    def test_un_lanzador_cuyo_programa_ya_no_existe_se_dice(self):
        """GLib no crea la ficha si el programa de Exec no existe: la ventana cuenta qué pasó en vez de callar."""
        with self.assertRaises(OSError):
            self.X.lanzar_app(("archivo", self.lanzador("/no/existe/ningun-programa")))
        with self.assertRaises(OSError):
            self.X.lanzar_app(("archivo", os.path.join(self.tmp, "no-existe.desktop")))
        with self.assertRaises(OSError):
            self.X.lanzar_app(("id", "no-existe-de-verdad.desktop"))

    def test_orden_en_su_propia_sesion_y_sin_esperar(self):
        with mock.patch.object(self.X.subprocess, "Popen") as popen, mock.patch.object(self.X.threading, "Thread") as hilo:
            self.X.lanzar_app(("orden", ["flatpak", "run", "com.spotify.Client"]))
        args, kw = popen.call_args
        self.assertEqual(args[0], ["flatpak", "run", "com.spotify.Client"])
        self.assertTrue(kw["start_new_session"])
        self.assertEqual((kw["stdin"], kw["stdout"], kw["stderr"]), (self.X.subprocess.DEVNULL,) * 3)
        hilo.return_value.start.assert_called_once()      # alguien recoge al hijo: no queda zombi

    def test_orden_real(self):
        self.X.lanzar_app(("orden", ["/bin/true"]))
        with self.assertRaises(OSError):
            self.X.lanzar_app(("orden", ["/no/existe/nada"]))

    def test_forma_desconocida(self):
        with self.assertRaises(ValueError):
            self.X.lanzar_app(("telepatia", "x"))

    def test_enlaces_solo_https(self):
        with mock.patch.object(self.X, "_xdg_open") as xdg:
            for url in ("http://x.org", "javascript:alert(1)", "file:///etc/passwd", "", None):
                self.assertFalse(self.X.abrir_enlace(url), url)
            xdg.assert_not_called()
            self.assertTrue(self.X.abrir_enlace("https://claude.ai/"))
        xdg.assert_called_once_with("https://claude.ai/")

    def test_enlace_con_ventana_usa_el_lanzador_de_gtk_y_si_falla_xdg_open(self):
        ventana = self.ventana()
        lanzador = mock.Mock()
        with mock.patch.object(self.X.Gtk.UriLauncher, "new", return_value=lanzador), \
                mock.patch.object(self.X, "_xdg_open", return_value=True) as xdg:
            self.assertTrue(self.X.abrir_enlace("https://www.netflix.com/", ventana))
            args = lanzador.launch.call_args[0]
            self.assertEqual(args[:2], (ventana, None))
            lanzador.launch_finish.side_effect = self.GLib.Error.new_literal(self.GLib.quark_from_string("prueba"), "falló", 1)
            args[2](lanzador, object())
            xdg.assert_called_once_with("https://www.netflix.com/")

    def test_enlace_cancelado_por_el_usuario_no_prueba_otra_via(self):
        ventana = self.ventana()
        lanzador = mock.Mock()
        with mock.patch.object(self.X.Gtk.UriLauncher, "new", return_value=lanzador), \
                mock.patch.object(self.X, "_xdg_open") as xdg:
            self.X.abrir_enlace("https://www.netflix.com/", ventana)
            lanzador.launch_finish.side_effect = self.GLib.Error.new_literal(
                self.Gtk.dialog_error_quark(), "cancelado", int(self.Gtk.DialogError.DISMISSED))
            lanzador.launch.call_args[0][2](lanzador, object())
            xdg.assert_not_called()

    def test_enlace_que_no_se_abre_de_ninguna_forma_avisa(self):
        avisos = []
        ventana = self.ventana()
        lanzador = mock.Mock()
        with mock.patch.object(self.X.Gtk.UriLauncher, "new", return_value=lanzador), \
                mock.patch.object(self.X, "_xdg_open", return_value=False):
            self.X.abrir_enlace("https://www.netflix.com/", ventana, al_fallar=avisos.append)
            lanzador.launch_finish.side_effect = self.GLib.Error.new_literal(self.GLib.quark_from_string("prueba"), "falló", 1)
            lanzador.launch.call_args[0][2](lanzador, object())
        self.assertEqual(avisos, ["No se pudo abrir el enlace."])


# ====================================================================================== la ventana
class TestVentana(BaseVentana):
    def test_ventana_y_aplicacion(self):
        v = self.ventana()
        self.assertEqual(v.get_title(), "Tienda")
        self.assertEqual(v.get_default_size(), (1000, 680))
        self.assertEqual(v.get_size_request(), (820, 520))
        self.assertTrue(v.has_css_class("tienda-ventana"))
        self.assertTrue(v.get_resizable())
        self.assertEqual(self.X.APP_ID, "org.infinity.Tienda")
        self.assertEqual(self.X.AppTienda().get_application_id(), "org.infinity.Tienda")

    def test_cabecera_con_la_marca_el_buscador_y_el_menu(self):
        v = self.ventana()
        cab = v.get_titlebar()
        self.assertIsInstance(cab, self.Gtk.HeaderBar)
        self.assertEqual(cab.get_decoration_layout(), ":minimize,maximize,close")
        self.assertIs(cab.get_title_widget(), v.busqueda)
        self.assertIsInstance(v.busqueda, self.Gtk.SearchEntry)
        self.assertIn("Tienda", self.textos(cab))
        self.assertEqual(self.menu_de(v.menu), ["Actualizar estado", "Acerca de la Tienda"])

    def test_el_foco_empieza_en_el_buscador(self):
        v = self.mostrar(self.ventana())
        foco = v.get_focus()
        self.assertTrue(foco is v.busqueda or (foco is not None and foco.is_ancestor(v.busqueda)))

    def test_doce_tarjetas_con_sus_nombres_en_el_orden_del_catalogo(self):
        v = self.ventana()
        tarjetas = [w for w in self.hijos(v.rejilla) if isinstance(w, self.X.Tarjeta)]
        self.assertEqual(len(tarjetas), 12)
        self.assertEqual([t.nombre.get_text() for t in tarjetas], [a.nombre for a in self.catalogo.apps])
        self.assertEqual(v.visibles(), [a.id for a in self.catalogo.apps])
        for nombre in ("Spotify", "Google Antigravity", "Netflix", "HBO Max", "Disney+", "YouTube", "Claude", "Gemini",
                       "NotebookLM", "Google Classroom", "Google Chrome", "Brave"):
            self.assertIn(nombre, [t.nombre.get_text() for t in tarjetas])

    def test_la_rejilla_es_un_grid_de_tres_columnas_y_no_un_flowbox(self):
        v = self.ventana()
        self.assertIsInstance(v.rejilla, self.Gtk.Grid)
        self.assertFalse([w for w in self.todos(v) if isinstance(w, self.Gtk.FlowBox)], "el FlowBox se come los clics")
        for i, ident in enumerate(v.visibles()):
            self.assertEqual(v.rejilla.query_child(v._tarjetas[ident])[:2], (i % 3, i // 3), ident)
        self.assertTrue(v.rejilla.get_column_homogeneous())
        scroll = v.rejilla.get_ancestor(self.Gtk.ScrolledWindow)
        self.assertIs(scroll, v.scroll)
        self.assertEqual(scroll.get_policy()[0], self.Gtk.PolicyType.NEVER)

    def test_la_ventana_puede_encogerse_hasta_820_por_520(self):
        v = self.mostrar(self.ventana())
        self.assertLessEqual(v.measure(self.Gtk.Orientation.HORIZONTAL, -1)[0], 820)
        self.assertLessEqual(v.measure(self.Gtk.Orientation.VERTICAL, 820)[0], 520)

    def test_lateral_con_todo_instaladas_y_las_categorias_con_su_contador(self):
        v = self.ventana()
        filas = self.filas(v)
        self.assertEqual([f.vista for f in filas], ["todo", "instaladas", "cat:entretenimiento", "cat:ia", "cat:estudio",
                                                    "cat:desarrollo", "cat:navegadores"])
        cuentas = {clave: e.get_text() for clave, e in v._contadores.items()}
        self.assertEqual(cuentas, {"todo": "12", "instaladas": "0", "cat:entretenimiento": "5", "cat:ia": "3",
                                   "cat:estudio": "1", "cat:desarrollo": "1", "cat:navegadores": "2"})
        nombres = [self.textos(f.get_child())[0] for f in filas]
        self.assertEqual(nombres, ["Todo", "Instaladas", "Música y vídeo", "Inteligencia artificial", "Estudio",
                                   "Desarrollo", "Navegadores"])
        self.assertIs(v.lista_vistas.get_selected_row(), v._filas["todo"], "«Todo» empieza resaltada")
        self.assertTrue(v.lista_vistas.has_css_class("tienda-categorias"))

    def test_el_lateral_pone_el_titulo_categorias_sobre_la_primera_categoria(self):
        v = self.mostrar(self.ventana())
        cabeceras = [f.get_header() for f in self.filas(v)]
        self.assertEqual([c.get_text() if c is not None else None for c in cabeceras],
                         [None, None, "CATEGORÍAS", None, None, None, None])

    def test_filtrar_por_categoria(self):
        v = self.ventana()
        v.seleccionar_vista("cat:ia")
        self.assertEqual(v.visibles(), ["claude", "gemini", "notebooklm"])
        self.assertEqual(v.titulo.get_text(), "Inteligencia artificial")
        self.assertEqual(v.subtitulo.get_text(), "3 aplicaciones")
        self.assertIs(v.lista_vistas.get_selected_row(), v._filas["cat:ia"])
        v.seleccionar_vista("cat:estudio")
        self.assertEqual(v.visibles(), ["classroom"])
        self.assertEqual(v.subtitulo.get_text(), "1 aplicación")
        v.seleccionar_vista("todo")
        self.assertEqual(len(v.visibles()), 12)

    def test_filtrar_por_instaladas_y_los_contadores_siguen_al_sistema(self):
        self.instaladas.update({"spotify": self.O.Estado(True, "", True, ""), "chrome": self.O.Estado(True, "141", True, "")})
        v = self.ventana()
        self.assertEqual(v._contadores["instaladas"].get_text(), "2")
        v.seleccionar_vista("instaladas")
        self.assertEqual(v.visibles(), ["spotify", "chrome"])
        self.assertEqual(v.titulo.get_text(), "Instaladas")
        self.instaladas["brave"] = self.O.Estado(True, "1.0", True, "")
        v.gestor.refrescar()
        self.assertEqual(v._contadores["instaladas"].get_text(), "3")
        self.assertEqual(v.visibles(), ["spotify", "chrome", "brave"], "«Instaladas» gana la app nueva sin tocar nada")
        del self.instaladas["spotify"]
        v.gestor.refrescar()
        self.assertEqual(v.visibles(), ["chrome", "brave"])

    def test_instaladas_vacia_lo_explica_y_ofrece_volver(self):
        v = self.ventana()
        v.seleccionar_vista("instaladas")
        self.assertEqual(v.visibles(), [])
        self.assertEqual(v.pila.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_titulo.get_text(), "Aún no hay nada instalado")
        self.assertTrue(v.vacio_boton.get_visible())
        v.vacio_boton.emit("clicked")
        self.assertEqual(v.pila.get_visible_child_name(), "rejilla")
        self.assertEqual(len(v.visibles()), 12)
        self.assertIs(v.lista_vistas.get_selected_row(), v._filas["todo"])

    def test_una_app_que_se_instala_aparece_en_instaladas_sin_pulsar_nada(self):
        v = self.ventana()
        v.seleccionar_vista("instaladas")
        self.assertEqual(v.pila.get_visible_child_name(), "vacio")
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        self.ej.terminar(True)
        self.assertEqual(v.visibles(), ["spotify"])
        self.assertEqual(v.pila.get_visible_child_name(), "rejilla")

    def test_buscar_pelicula_encuentra_netflix(self):
        v = self.ventana()
        v.buscar("pelicula")
        self.assertIn("netflix", v.visibles())
        self.assertIn("hbo-max", v.visibles())
        self.assertNotIn("spotify", v.visibles())
        n = len(v.visibles())
        self.assertEqual(v.subtitulo.get_text(), f"{n} resultados para «pelicula»")
        self.assertEqual(v.busqueda.get_text(), "pelicula")
        v.buscar("")
        self.assertEqual(len(v.visibles()), 12)

    def test_el_buscador_filtra_solo_a_los_pocos_milisegundos(self):
        v = self.ventana()
        v.busqueda.set_text("spotify")
        self.assertEqual(len(v.visibles()), 12, "todavía no: el buscador espera a que dejes de teclear")
        self.assertTrue(self.pump_hasta(lambda: v.visibles() == ["spotify"]))
        self.assertEqual(v.subtitulo.get_text(), "1 resultado para «spotify»")

    def test_la_busqueda_respeta_la_categoria_y_ofrece_buscar_en_todo(self):
        v = self.ventana()
        v.seleccionar_vista("cat:ia")
        v.buscar("gemini")
        self.assertEqual(v.visibles(), ["gemini"])
        v.buscar("netflix")
        self.assertEqual(v.visibles(), [])
        self.assertEqual(v.pila.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_titulo.get_text(), "Nada coincide con «netflix»")
        self.assertIn("Inteligencia artificial", v.vacio_texto.get_text())
        self.assertEqual(v.vacio_boton.get_label(), "Buscar en todo")
        v.vacio_boton.emit("clicked")
        self.assertEqual(v.visibles(), ["netflix", "chrome"], "mismo texto, ahora en todo el catálogo (Chrome también lo nombra)")

    def test_sin_resultados_en_todo_ofrece_borrar_la_busqueda(self):
        v = self.ventana()
        v.buscar("zzzz")
        self.assertEqual(v.pila.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_titulo.get_text(), "Nada coincide con «zzzz»")
        self.assertEqual(v.vacio_boton.get_label(), "Borrar la búsqueda")
        v.vacio_boton.emit("clicked")
        self.assertEqual(v.busqueda.get_text(), "")
        self.assertEqual(len(v.visibles()), 12)

    def test_esc_limpia_la_busqueda_y_ctrl_f_la_enfoca(self):
        v = self.mostrar(self.ventana())
        v.buscar("spotify")
        self.assertEqual(v.visibles(), ["spotify"])
        self.assertTrue(self.tecla(v, self.Gdk.KEY_Escape))
        self.assertEqual(v.busqueda.get_text(), "")
        self.assertEqual(len(v.visibles()), 12)
        self.assertFalse(self.tecla(v, self.Gdk.KEY_Escape), "sin nada que deshacer, Esc no se come la tecla")
        self.tarjeta(v, "spotify").boton_principal.grab_focus()
        self.assertTrue(self.tecla(v, self.Gdk.KEY_f, control=True))
        foco = v.get_focus()
        self.assertTrue(foco is v.busqueda or (foco is not None and foco.is_ancestor(v.busqueda)))

    def test_ctrl_q_cierra_como_el_boton_de_cerrar(self):
        v = self.mostrar(self.ventana())
        self.assertTrue(self.tecla(v, self.Gdk.KEY_q, control=True))
        self.assertTrue(v._cerrada)
        self.assertNotIn(v, self.app.get_windows())

    def test_una_tarjeta_sola_no_se_ensancha(self):
        v = self.mostrar(self.ventana())
        normal = v._tarjetas["spotify"].get_width()
        self.assertGreater(normal, 150)
        v.buscar("spotify")
        self.pump(0.3)
        self.assertAlmostEqual(v._tarjetas["spotify"].get_width(), normal, delta=2)
        self.assertEqual(len(self.hijos(v.rejilla)), 3, "la tarjeta y dos huecos que mantienen las columnas")
        v.buscar("")
        v.seleccionar_vista("instaladas")
        v.seleccionar_vista("cat:entretenimiento")
        self.pump(0.3)
        self.assertEqual(len([w for w in self.hijos(v.rejilla) if isinstance(w, self.X.Tarjeta)]), 5)
        self.assertEqual(len(self.hijos(v.rejilla)), 6)

    def test_cambiar_de_vista_conserva_las_mismas_tarjetas(self):
        v = self.ventana()
        antes = dict(v._tarjetas)
        v.seleccionar_vista("cat:ia")
        v.seleccionar_vista("todo")
        v.buscar("net")
        v.buscar("")
        self.assertEqual(len(v._tarjetas), 12)
        for ident, t in antes.items():
            self.assertIs(v._tarjetas[ident], t, ident)

    def test_menu_de_la_cabecera(self):
        v = self.ventana()
        gestor = v.gestor
        with mock.patch.object(gestor, "refrescar", wraps=gestor.refrescar) as refrescar:
            v.lookup_action("actualizar-estado").activate(None)
        refrescar.assert_called_once()
        self.assertTrue(v.barra.get_visible())
        self.assertEqual(v.barra.etiqueta.get_text(), "Estado actualizado.")
        self.assertTrue(v.barra.has_css_class("ok"))
        with mock.patch.object(self.Gtk.AboutDialog, "present") as presentar:
            v.lookup_action("acerca").activate(None)
        presentar.assert_called_once()

    def test_actualizar_estado_vuelve_a_mirar_el_sistema(self):
        v = self.ventana()
        self.assertEqual(self.botones(self.tarjeta(v, "spotify").zona_accion), ["Instalar"])
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        v.lookup_action("actualizar-estado").activate(None)
        self.assertEqual(self.botones(self.tarjeta(v, "spotify").zona_accion), ["Abrir"])
        self.assertEqual(v._contadores["instaladas"].get_text(), "1")

    def test_si_el_gestor_falla_al_actualizar_el_estado_lo_cuenta(self):
        v = self.ventana()
        with mock.patch.object(v.gestor, "refrescar", side_effect=OSError("disco")):
            v.lookup_action("actualizar-estado").activate(None)
        self.assertTrue(v.barra.has_css_class("error"))
        self.assertIn("disco", v.barra.etiqueta.get_text())


# ================================================================================= el pie de la tarjeta
class TestTarjeta(BaseVentana):
    def test_estado_sin_instalar(self):
        v = self.ventana()
        t = self.tarjeta(v, "spotify")
        self.assertEqual(t.modo, "instalar")
        self.assertEqual(self.botones(t.zona_accion), ["Instalar"])
        self.assertTrue(t.boton_principal.has_css_class("suggested-action"), "el botón violeta")
        self.assertIsNone(t.menu)
        self.assertFalse(t.marca_instalada.get_visible())
        self.assertTrue(t.etiqueta_tamano.get_visible())
        self.assertEqual(t.etiqueta_tamano.get_text(), "≈ 1 GB")
        self.assertFalse(t.zona_mensaje.get_visible())

    def test_el_tamano_solo_se_ve_si_el_catalogo_lo_dice(self):
        v = self.ventana()
        self.assertFalse(self.tarjeta(v, "netflix").etiqueta_tamano.get_visible())
        self.assertEqual(self.tarjeta(v, "antigravity").etiqueta_tamano.get_text(), "≈ 700 MB")
        self.assertTrue(self.tarjeta(v, "antigravity").etiqueta_tamano.get_visible())

    def test_chips_de_tipo_y_drm(self):
        v = self.ventana()
        tipos = {i: t.chip_tipo.get_text() for i, t in v._tarjetas.items()}
        self.assertEqual(tipos["spotify"], "FLATPAK")
        self.assertEqual(tipos["antigravity"], "APT")
        self.assertEqual(tipos["chrome"], "APT")
        self.assertEqual({tipos[i] for i in ("netflix", "claude", "youtube", "classroom")}, {"WEBAPP"})
        con_drm = sorted(i for i, t in v._tarjetas.items() if t.chip_drm is not None)
        self.assertEqual(con_drm, ["disney-plus", "hbo-max", "netflix"])
        self.assertEqual(self.tarjeta(v, "netflix").chip_drm.get_text(), "DRM")
        self.assertTrue(self.tarjeta(v, "netflix").chip_tipo.has_css_class("tienda-chip"))

    def test_cada_tarjeta_es_un_grupo_con_nombre_y_descripcion_para_los_lectores_de_pantalla(self):
        v = self.ventana()
        for ident, t in v._tarjetas.items():
            self.assertEqual(t.get_accessible_role(), self.Gtk.AccessibleRole.GROUP, ident)
            self.assertTrue(self.Gtk.test_accessible_has_property(t, self.Gtk.AccessibleProperty.LABEL), ident)
            self.assertTrue(self.Gtk.test_accessible_has_property(t, self.Gtk.AccessibleProperty.DESCRIPTION), ident)
        self.assertEqual(v.barra.get_accessible_role(), self.Gtk.AccessibleRole.STATUS, "la barra se anuncia como un estado")

    def test_cada_tarjeta_lleva_su_franja_de_color(self):
        v = self.ventana()
        franja = self.hijos(self.tarjeta(v, "netflix"))[0]
        self.assertTrue(franja.has_css_class("tienda-franja"))
        self.assertTrue(franja.has_css_class("tienda-color-netflix"))

    def test_en_cola(self):
        v = self.ventana()
        g = v.gestor
        g.pedir("spotify", self.O.INSTALAR)
        g.pedir("antigravity", self.O.INSTALAR)
        t = self.tarjeta(v, "antigravity")
        self.assertEqual(t.modo, "cola")
        self.assertEqual(self.botones(t.zona_accion), ["En cola…", "Quitar de la cola"])
        self.assertFalse(t.boton_principal.get_sensitive(), "«En cola…» está desactivado")
        self.assertIsNone(t.barra)
        self.assertFalse(t.etiqueta_tamano.get_visible())

    def test_en_curso_con_progreso(self):
        v = self.ventana()
        g = v.gestor
        g.pedir("spotify", self.O.INSTALAR)
        t = self.tarjeta(v, "spotify")
        self.assertEqual(t.modo, "curso")
        self.assertIsNone(t.boton_principal, "durante el trabajo no hay botón")
        self.assertIsInstance(t.barra, self.Gtk.ProgressBar)
        self.assertEqual(t.texto_estado.get_text(), "Instalando Spotify…")
        self.ej.evento("progreso", 0.4, "Descargando org.freedesktop.Platform…")
        self.assertAlmostEqual(t.barra.get_fraction(), 0.4)
        self.assertEqual(t.texto_estado.get_text(), "Descargando org.freedesktop.Platform…")
        self.assertEqual(t.porcentaje.get_text(), "40 %")
        self.assertEqual(self.botones(t.zona_accion), [])

    def test_sin_medida_la_barra_pulsa_y_con_ella_se_para(self):
        v = self.ventana()
        v.gestor.pedir("spotify", self.O.INSTALAR)
        t = self.tarjeta(v, "spotify")
        self.assertTrue(t._id_pulso, "progreso None: barra pulsante")
        self.assertEqual(t.porcentaje.get_text(), "")
        self.pump(0.3)
        self.assertTrue(t._id_pulso)
        self.ej.evento("progreso", 0.7, "")
        self.assertEqual(t._id_pulso, 0)
        self.assertAlmostEqual(t.barra.get_fraction(), 0.7)
        self.ej.evento("progreso", None, "Esperando…")
        self.assertTrue(t._id_pulso, "si vuelve a perder la medida, vuelve a pulsar")
        self.assertEqual(t.texto_estado.get_text(), "Esperando…")

    def test_el_teclado_no_pierde_su_sitio_al_cambiar_el_pie(self):
        v = self.mostrar(self.ventana())
        g = v.gestor
        spotify = self.tarjeta(v, "spotify")
        spotify.boton_principal.grab_focus()
        self.assertIs(v.get_focus(), spotify.boton_principal)
        spotify.boton_principal.emit("clicked")
        self.assertEqual(spotify.modo, "curso")
        self.assertIsNone(spotify.boton_principal)
        self.assertIs(v.get_focus(), spotify.boton_info, "sin botón principal, el foco se queda en la tarjeta")
        antigravity = self.tarjeta(v, "antigravity")
        antigravity.boton_principal.grab_focus()
        antigravity.boton_principal.emit("clicked")
        self.assertEqual(antigravity.modo, "cola")
        self.assertIs(v.get_focus(), antigravity.boton_cola, "«En cola…» no se puede pulsar: el foco, en «Quitar de la cola»")
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        self.ej.terminar(True)
        self.assertEqual(spotify.modo, "abrir")
        self.assertEqual(antigravity.modo, "curso", "le tocó el turno: su «Quitar de la cola» desaparece")
        self.assertIs(v.get_focus(), antigravity.boton_info, "y el foco no se pierde")
        self.tarjeta(v, "gemini").boton_principal.grab_focus()
        g.refrescar()
        self.assertIs(v.get_focus(), self.tarjeta(v, "gemini").boton_principal, "un refresco no toca el foco de otra tarjeta")

    def test_la_barra_deja_de_pulsar_al_acabar_y_al_cerrar_la_ventana(self):
        v = self.ventana()
        v.gestor.pedir("spotify", self.O.INSTALAR)
        t = self.tarjeta(v, "spotify")
        self.assertTrue(t._id_pulso)
        self.ej.terminar(True)
        self.assertEqual(t._id_pulso, 0)
        v.gestor.pedir("spotify", self.O.QUITAR)
        self.assertTrue(t._id_pulso)
        v.liberar()
        self.assertEqual(t._id_pulso, 0, "al cerrar no queda ningún temporizador")

    def test_instalada_con_abrir_y_menu(self):
        self.instaladas.update({"spotify": self.O.Estado(True, "", True, ""), "chrome": self.O.Estado(True, "141.0.7390", True, ""),
                                "claude": self.O.Estado(True, "", True, "")})
        v = self.ventana()
        for ident in ("spotify", "chrome", "claude"):
            t = self.tarjeta(v, ident)
            self.assertEqual(t.modo, "abrir", ident)
            self.assertEqual(t.boton_principal.get_label(), "Abrir")
            self.assertTrue(t.marca_instalada.get_visible(), "la marca cian de «instalada»")
        self.assertEqual(self.menu_de(self.tarjeta(v, "spotify").menu), ["Actualizar", "Quitar"], "flatpak")
        self.assertEqual(self.menu_de(self.tarjeta(v, "chrome").menu), ["Actualizar", "Quitar"], "apt")
        self.assertEqual(self.menu_de(self.tarjeta(v, "claude").menu), ["Quitar"], "una WebApp no se actualiza")
        self.assertIn("141.0.7390", self.tarjeta(v, "chrome").marca_instalada.get_tooltip_text())
        self.assertFalse(self.tarjeta(v, "spotify").etiqueta_tamano.get_visible())

    def test_webapp_instalada_sin_navegador_se_repara(self):
        self.instaladas["claude"] = self.O.Estado(True, "", False, "Su navegador ya no está instalado: instala otro para que vuelva a abrir.")
        v = self.ventana()
        t = self.tarjeta(v, "claude")
        self.assertEqual(t.modo, "reparar")
        self.assertEqual(t.boton_principal.get_label(), "Reparar")
        self.assertIn("Su navegador ya no está instalado", t.etiqueta_detalle.get_text())
        self.assertTrue(t.aviso_reparar.has_css_class("tienda-aviso"), "el detalle, como aviso ámbar")
        self.assertTrue(t.zona_mensaje.get_visible())
        self.assertEqual(self.menu_de(t.menu), ["Quitar"])
        self.assertFalse(t.marca_instalada.get_visible(), "no está bien instalada: sin la marca cian")
        self.assertEqual(v._contadores["instaladas"].get_text(), "1")

    def test_tras_un_fallo_reintentar_y_el_mensaje_en_la_tarjeta(self):
        v = self.ventana()
        g = v.gestor
        g.pedir("brave", self.O.INSTALAR)
        self.ej.terminar(False, "No hay conexión con el servidor.")
        t = self.tarjeta(v, "brave")
        self.assertEqual(t.modo, "fallo")
        self.assertEqual(t.boton_principal.get_label(), "Reintentar")
        self.assertEqual(t.etiqueta_error.get_text(), "No se pudo instalar Brave. No hay conexión con el servidor.")
        self.assertTrue(t.etiqueta_error.get_parent().has_css_class("tienda-error"))
        self.assertEqual(t.etiqueta_error.get_tooltip_text(), t.etiqueta_error.get_text(), "el mensaje entero, en el tooltip")

    def test_descartar_el_error_vuelve_a_instalar(self):
        v = self.ventana()
        v.gestor.pedir("brave", self.O.INSTALAR)
        self.ej.terminar(False, "Sin red.")
        t = self.tarjeta(v, "brave")
        t.boton_descartar.emit("clicked")
        self.assertEqual(t.modo, "instalar")
        self.assertIsNone(t.etiqueta_error)
        self.assertEqual(t.boton_principal.get_label(), "Instalar")
        v.gestor.refrescar()
        self.assertEqual(t.modo, "instalar", "un refresco no resucita el error descartado")
        v.gestor.pedir("brave", self.O.INSTALAR)
        self.ej.terminar(False, "Otra vez sin red.")
        self.assertEqual(t.modo, "fallo", "un fallo nuevo sí se enseña")
        self.assertIn("Otra vez sin red.", t.etiqueta_error.get_text())

    def test_reintentar_vuelve_a_pedir_la_misma_accion(self):
        v = self.ventana()
        v.gestor.pedir("brave", self.O.INSTALAR)
        self.ej.terminar(False, "Sin red.")
        self.tarjeta(v, "brave").boton_principal.emit("clicked")
        self.assertEqual(self.tarjeta(v, "brave").modo, "curso")
        self.assertEqual([p.accion for p in self.ej.historial], [self.O.INSTALAR, self.O.INSTALAR])

    def test_un_exito_no_deja_huella_en_la_tarjeta(self):
        v = self.ventana()
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        self.ej.terminar(True)
        t = self.tarjeta(v, "spotify")
        self.assertEqual(t.modo, "abrir")
        self.assertIsNone(t.etiqueta_error)
        self.assertEqual(v._contadores["instaladas"].get_text(), "1")

    def test_detalles_descripcion_aviso_y_enlace(self):
        v = self.ventana()
        t = self.tarjeta(v, "netflix")
        e = self.catalogo.por_id["netflix"]
        self.assertFalse(t.detalles.get_reveal_child())
        t.boton_info.emit("clicked")
        self.assertTrue(t.detalles.get_reveal_child())
        self.assertTrue(t.expandida)
        self.assertEqual(t.descripcion.get_text(), e.descripcion)
        self.assertIn(e.aviso, self.textos(t.caja_aviso))
        self.assertTrue(t.caja_aviso.has_css_class("tienda-aviso"), "el aviso, en caja ámbar")
        self.assertEqual(t.boton_web.get_label(), "netflix.com ↗")
        self.assertEqual(t.boton_web.get_tooltip_text(), "https://www.netflix.com/")
        t.boton_web.emit("clicked")
        self.assertEqual(self.urls, ["https://www.netflix.com/"])
        t.boton_info.emit("clicked")
        self.assertFalse(t.detalles.get_reveal_child())

    def test_un_clic_en_la_cabecera_tambien_despliega(self):
        v = self.ventana()
        t = self.tarjeta(v, "spotify")
        t.alternar_detalles()
        self.assertTrue(t.expandida)
        self.assertTrue(t.boton_info.get_active())
        t.alternar_detalles()
        self.assertFalse(t.expandida)

    def test_solo_las_apps_con_aviso_llevan_caja_ambar(self):
        v = self.ventana()
        con_aviso = sorted(i for i, t in v._tarjetas.items() if t.caja_aviso is not None)
        self.assertEqual(con_aviso, sorted(a.id for a in self.catalogo.apps if a.aviso))
        self.assertIn("antigravity", con_aviso)
        self.assertNotIn("spotify", con_aviso)

    def test_sin_nada_que_desplegar_no_hay_boton_i(self):
        pelada = self.C.validar_entrada({"id": "pelada", "nombre": "Pelada", "resumen": "Solo lo básico.", "categoria": "ia",
                                         "tipo": "webapp", "origen": {"url": "https://pelada.example.org/"}})
        t = self.X.Tarjeta(pelada, mock.Mock())
        self.assertFalse(t.boton_info.get_visible())
        self.assertFalse(t.tiene_detalles)
        t.alternar_detalles()
        self.assertFalse(t.expandida)

    def test_el_resumen_y_el_nombre_se_acortan_con_puntos_suspensivos(self):
        v = self.ventana()
        t = self.tarjeta(v, "spotify")
        self.assertEqual(t.resumen.get_ellipsize(), self.X.Pango.EllipsizeMode.END)
        self.assertEqual(t.resumen.get_lines(), 3)
        self.assertTrue(t.resumen.get_wrap())
        self.assertEqual(t.nombre.get_lines(), 2)

    def test_la_tarjeta_aislada_se_repinta_con_lo_que_se_le_da(self):
        acciones = mock.Mock()
        t = self.X.Tarjeta(self.catalogo.por_id["spotify"], acciones)
        self.assertEqual(t.modo, "instalar")
        t.actualizar(self.O.Estado(True, "", True, ""))
        self.assertEqual(t.modo, "abrir")
        t.boton_principal.emit("clicked")
        acciones.abrir_app.assert_called_once_with(self.catalogo.por_id["spotify"])
        t.actualizar(self.O.SIN_INSTALAR, self.trabajo_falso("spotify", self.O.INSTALAR, "en_curso", progreso=0.5, texto="Bajando"))
        self.assertEqual(t.modo, "curso")
        self.assertAlmostEqual(t.barra.get_fraction(), 0.5)
        t.detener()


# ================================================================================ las acciones y sus respuestas
class TestAcciones(BaseVentana):
    def setUp(self):
        super().setUp()
        self.g = self.falso()
        self.v = self.ventana(self.g)

    def cambia(self, ident):
        self.g.al_cambiar(ident)

    def instalada(self, ident, **kw):
        self.g.estados[ident] = self.O.Estado(True, kw.get("version", ""), kw.get("funciona", True), kw.get("detalle", ""))
        self.cambia(ident)

    def pulsa_instalar(self, ident):
        self.tarjeta(self.v, ident).boton_principal.emit("clicked")

    # ---- instalar y las demás peticiones
    def test_instalar_pide_la_accion_correcta_sin_nada_mas(self):
        self.pulsa_instalar("spotify")
        self.assertEqual(self.g.llamadas, [("spotify", "instalar", {})])
        self.assertFalse(self.v.panel.get_visible())
        self.assertFalse(self.v.barra.get_visible())

    def test_actualizar_desde_el_menu_de_la_tarjeta(self):
        self.instalada("chrome")
        self.menu_accion(self.tarjeta(self.v, "chrome"), "actualizar")
        self.assertEqual(self.g.llamadas, [("chrome", "actualizar", {})])

    def test_reparar_vuelve_a_instalar(self):
        self.instalada("claude", funciona=False, detalle="Su navegador ya no está instalado.")
        self.tarjeta(self.v, "claude").boton_principal.emit("clicked")
        self.assertEqual(self.g.llamadas, [("claude", "instalar", {})])

    def test_reintentar_repite_la_accion_que_fallo(self):
        for accion in ("instalar", "quitar", "actualizar"):
            with self.subTest(accion=accion):
                self.g.llamadas.clear()
                self.g.trabajos["spotify"] = self.trabajo_falso("spotify", accion, "fallo", "No se pudo.")
                self.cambia("spotify")
                self.tarjeta(self.v, "spotify").boton_principal.emit("clicked")
                self.assertEqual(self.g.llamadas, [("spotify", accion, {})])

    def test_quitar_de_la_cola(self):
        self.g.trabajos["spotify"] = self.trabajo_falso("spotify", "instalar", "en_cola")
        self.cambia("spotify")
        self.tarjeta(self.v, "spotify").boton_cola.emit("clicked")
        self.assertEqual(self.g.sacados, ["spotify"])
        self.assertFalse(self.v.barra.get_visible())
        self.g.saca = False
        self.tarjeta(self.v, "spotify").boton_cola.emit("clicked")
        self.assertTrue(self.v.barra.has_css_class("aviso"), "si ya empezó, se dice")
        self.assertIn("no se puede quitar de la cola", self.v.barra.etiqueta.get_text())

    # ---- quitar pide confirmación en línea
    def test_quitar_pide_confirmacion_y_solo_entonces_actua(self):
        self.instalada("claude")
        t = self.tarjeta(self.v, "claude")
        self.menu_accion(t, "quitar")
        self.assertTrue(t.confirmando)
        self.assertEqual(self.g.llamadas, [], "con solo pulsar «Quitar» no se quita nada")
        self.assertTrue(t.caja_confirmar.has_css_class("tienda-confirmar"), "barra roja DENTRO de la tarjeta")
        self.assertTrue(t.caja_confirmar.is_ancestor(t))
        self.assertTrue(t.texto_confirmar.get_text().startswith("¿Quitar Claude?"))
        self.assertEqual(self.botones(t.caja_confirmar), ["Cancelar", "Sí, quitar"])
        self.assertTrue(t.boton_si.has_css_class("destructive-action"))
        self.assertFalse(t.fila.get_visible(), "mientras pregunta, el pie de siempre se esconde")
        self.assertFalse(self.v.panel.get_visible(), "nunca el panel ni un popover")
        t.boton_si.emit("clicked")
        self.assertEqual(self.g.llamadas, [("claude", "quitar", {})])
        self.assertFalse(t.confirmando)
        self.assertTrue(t.fila.get_visible())

    def test_las_acciones_del_menu_se_aplazan_hasta_que_el_menu_se_cierre(self):
        """Repintar el pie quita el botón ⋮ y su menú: no se hace dentro del clic que lo cierra."""
        self.instalada("chrome")
        t = self.tarjeta(self.v, "chrome")
        boton_menu = t.menu
        t.activate_action("tarjeta.quitar", None)
        self.assertFalse(t.confirmando, "todavía no")
        self.assertIs(t.menu, boton_menu)
        self.pump(0.05)
        self.assertTrue(t.confirmando)
        t.cancelar_confirmacion()
        t.activate_action("tarjeta.actualizar", None)
        self.assertEqual(self.g.llamadas, [])
        self.pump(0.05)
        self.assertEqual(self.g.llamadas, [("chrome", "actualizar", {})])

    def test_al_preguntar_el_foco_va_a_cancelar(self):
        self.mostrar(self.v)
        self.instalada("claude")
        t = self.tarjeta(self.v, "claude")
        self.menu_accion(t, "quitar")
        self.assertIs(self.v.get_focus(), t.boton_no, "la opción segura")

    def test_cancelar_la_confirmacion_no_hace_nada(self):
        self.instalada("claude")
        t = self.tarjeta(self.v, "claude")
        self.menu_accion(t, "quitar")
        t.boton_no.emit("clicked")
        self.assertFalse(t.confirmando)
        self.assertEqual(self.g.llamadas, [])
        self.assertEqual(t.boton_principal.get_label(), "Abrir")

    def test_esc_cancela_la_confirmacion(self):
        self.instalada("claude")
        t = self.tarjeta(self.v, "claude")
        self.menu_accion(t, "quitar")
        self.assertTrue(self.tecla(self.v, self.Gdk.KEY_Escape))
        self.assertFalse(t.confirmando)
        self.assertEqual(self.g.llamadas, [])

    def test_solo_una_confirmacion_a_la_vez(self):
        self.instalada("claude")
        self.instalada("chrome")
        a, b = self.tarjeta(self.v, "claude"), self.tarjeta(self.v, "chrome")
        self.menu_accion(a, "quitar")
        self.menu_accion(b, "quitar")
        self.assertFalse(a.confirmando)
        self.assertTrue(b.confirmando)

    def test_la_confirmacion_dice_lo_que_pasa_segun_el_tipo(self):
        self.instalada("chrome")
        self.instalada("claude")
        self.instalada("spotify")
        textos = {}
        for ident in ("chrome", "claude", "spotify"):
            t = self.tarjeta(self.v, ident)
            self.menu_accion(t, "quitar")
            textos[ident] = t.texto_confirmar.get_text()
            t.cancelar_confirmacion()
        self.assertIn("contraseña de administrador", textos["chrome"])
        self.assertIn("WebApps", textos["chrome"], "quitar un navegador avisa de lo que les pasa a las WebApps")
        self.assertIn("Solo se borra su lanzador", textos["claude"])
        self.assertEqual(textos["spotify"], "¿Quitar Spotify?")

    def test_la_confirmacion_se_va_si_la_app_cambia_de_estado(self):
        self.instalada("claude")
        t = self.tarjeta(self.v, "claude")
        self.menu_accion(t, "quitar")
        self.g.trabajos["claude"] = self.trabajo_falso("claude", "quitar", "en_curso")
        self.cambia("claude")
        self.assertFalse(t.confirmando)
        self.assertEqual(t.modo, "curso")

    def test_quitar_solo_se_pregunta_con_la_app_a_mano(self):
        t = self.tarjeta(self.v, "spotify")
        t.pedir_confirmacion()
        self.assertFalse(t.confirmando, "sin instalar no hay nada que quitar")

    # ---- falta un navegador
    def falta_navegador(self):
        return self.O.Respuesta("falta_navegador", "Las WebApps necesitan un navegador basado en Chromium.",
                                tuple(self.catalogo.navegadores()))

    def test_falta_navegador_abre_el_panel_en_linea(self):
        self.g.respuestas = [self.falta_navegador()]
        self.pulsa_instalar("netflix")
        p = self.v.panel
        self.assertTrue(p.get_visible())
        self.assertEqual(p.titulo.get_text(), "Para usar Netflix necesitas un navegador basado en Chromium")
        self.assertEqual([b.opcion.texto for b in p.botones], ["Instalar Google Chrome y Netflix", "Instalar Brave y Netflix"])
        self.assertEqual(p.botones[0].opcion.marca, "Recomendado", "el primero, marcado")
        self.assertEqual(p.botones[1].opcion.marca, "")
        self.assertIn("RECOMENDADO", self.textos(p.botones[0]))
        self.assertTrue(p.botones[0].has_css_class("suggested-action"))
        self.assertEqual(p.boton_cancelar.get_label(), "Cancelar")
        self.assertIn("Widevine", p.nota.get_text(), "Netflix: una línea sobre que Chrome trae Widevine")
        self.assertIn("≈ 450 MB", self.textos(p.botones[0]))
        self.assertIs(p.get_parent(), self.v.superposicion, "es un Gtk.Overlay de la propia ventana")
        self.assertFalse(self.v.cuerpo.get_sensitive(), "lo de detrás no se pulsa")
        self.assertEqual(self.g.llamadas, [("netflix", "instalar", {})], "todavía no se ha elegido nada")

    def test_el_panel_lleva_el_icono_de_la_app_y_el_de_cada_navegador(self):
        self.g.respuestas = [self.falta_navegador()]
        self.pulsa_instalar("netflix")
        p = self.v.panel
        self.assertTrue(p.hueco_icono.get_visible())
        self.assertEqual(len(self.hijos(p.hueco_icono)), 1)
        self.assertEqual([b.opcion.entrada.id for b in p.botones], ["chrome", "brave"])
        for boton in p.botones:
            self.assertIsInstance(self.todos(boton)[1], (self.Gtk.Image, self.Gtk.Box), "el icono va delante del texto")
        p.cancelar()
        self.g.respuestas = [self.poco_espacio()]
        self.pulsa_instalar("spotify")
        self.assertTrue(p.hueco_icono.get_visible(), "la pregunta del espacio también habla de una app")
        self.assertEqual(len(self.hijos(p.hueco_icono)), 1, "y no se acumulan iconos de preguntas anteriores")
        p.cancelar()
        p.preguntar("Suelta", "sin app", [])
        self.assertFalse(p.hueco_icono.get_visible())
        p.cancelar()

    def test_el_panel_es_un_dialogo_de_alerta_con_el_titulo_como_nombre(self):
        self.g.respuestas = [self.falta_navegador()]
        self.pulsa_instalar("netflix")
        cuadro = self.v.panel.cuadro
        self.assertEqual(cuadro.get_accessible_role(), self.Gtk.AccessibleRole.ALERT_DIALOG)
        self.assertTrue(self.Gtk.test_accessible_has_property(cuadro, self.Gtk.AccessibleProperty.LABEL))

    def test_la_nota_de_widevine_solo_sale_en_las_series_con_drm(self):
        for ident, con_nota in (("netflix", True), ("hbo-max", True), ("disney-plus", True), ("youtube", False), ("claude", False)):
            with self.subTest(ident=ident):
                self.g.respuestas = [self.falta_navegador()]
                self.pulsa_instalar(ident)
                self.assertEqual("Widevine" in self.v.panel.nota.get_text(), con_nota)
                self.assertEqual(self.v.panel.nota.get_visible(), con_nota)
                self.v.panel.cancelar()

    def test_elegir_chrome_vuelve_a_pedir_con_ese_navegador(self):
        self.g.respuestas = [self.falta_navegador()]
        self.pulsa_instalar("netflix")
        self.v.panel.botones[0].emit("clicked")
        self.assertEqual(self.g.llamadas, [("netflix", "instalar", {}), ("netflix", "instalar", {"navegador": "chrome"})])
        self.assertFalse(self.v.panel.get_visible())
        self.assertTrue(self.v.cuerpo.get_sensitive())

    def test_elegir_brave(self):
        self.g.respuestas = [self.falta_navegador()]
        self.pulsa_instalar("claude")
        self.v.panel.botones[1].emit("clicked")
        self.assertEqual(self.g.llamadas[-1], ("claude", "instalar", {"navegador": "brave"}))

    def test_cancelar_el_panel_no_pide_nada(self):
        self.mostrar(self.v)                  # con la ventana repartida de verdad, el clic en el velo tiene su sitio
        for como in ("boton", "esc", "velo"):
            with self.subTest(como=como):
                self.g.llamadas.clear()
                self.g.respuestas = [self.falta_navegador()]
                self.pulsa_instalar("netflix")
                if como == "boton":
                    self.v.panel.boton_cancelar.emit("clicked")
                elif como == "esc":
                    self.assertTrue(self.tecla(self.v, self.Gdk.KEY_Escape))
                else:
                    self.pump(0.2)                # que el cuadro tenga su sitio antes de pulsar fuera de él
                    self.v.panel._clic_en_el_velo(None, 1, 2.0, 2.0)
                self.assertFalse(self.v.panel.get_visible())
                self.assertTrue(self.v.cuerpo.get_sensitive())
                self.assertEqual(self.g.llamadas, [("netflix", "instalar", {})])

    def test_un_clic_dentro_del_cuadro_no_lo_cierra(self):
        self.g.respuestas = [self.falta_navegador()]
        self.mostrar(self.v)
        self.pulsa_instalar("netflix")
        self.pump(0.3)
        ok, caja = self.v.panel.cuadro.compute_bounds(self.v.panel)
        self.assertTrue(ok)
        self.v.panel._clic_en_el_velo(None, 1, caja.get_x() + 20, caja.get_y() + 20)
        self.assertTrue(self.v.panel.get_visible())

    def test_con_el_panel_abierto_el_foco_esta_en_el_y_al_cerrar_vuelve(self):
        self.mostrar(self.v)
        boton = self.tarjeta(self.v, "netflix").boton_principal
        boton.grab_focus()
        self.g.respuestas = [self.falta_navegador()]
        boton.emit("clicked")
        self.assertIs(self.v.get_focus(), self.v.panel.botones[0], "la opción principal, a mano")
        self.tecla(self.v, self.Gdk.KEY_f, control=True)
        self.assertIs(self.v.get_focus(), self.v.panel.botones[0], "Ctrl+F no se cuela por debajo del panel")
        self.v.panel.cancelar()
        self.assertIs(self.v.get_focus(), boton, "al cerrar, el foco vuelve a donde estaba")

    # ---- poco espacio
    def poco_espacio(self, mensaje="Queda poco espacio para Spotify: hay unos 10 MB libres y hacen falta cerca de 1200 MB."):
        return self.O.Respuesta("poco_espacio", mensaje, ())

    def test_poco_espacio_pregunta_y_con_si_fuerza(self):
        self.mostrar(self.v)
        self.g.respuestas = [self.poco_espacio()]
        self.pulsa_instalar("spotify")
        p = self.v.panel
        self.assertTrue(p.get_visible())
        self.assertEqual(p.texto.get_text(), "Queda poco espacio para Spotify: hay unos 10 MB libres y hacen falta cerca de 1200 MB.")
        self.assertEqual([b.opcion.texto for b in p.botones], ["Instalar de todos modos"])
        self.assertIs(self.v.get_focus(), p.boton_cancelar, "sin opción principal, el foco, en la salida segura")
        p.botones[0].emit("clicked")
        self.assertEqual(self.g.llamadas, [("spotify", "instalar", {}), ("spotify", "instalar", {"forzar": True})])
        self.assertFalse(p.get_visible())

    def test_poco_espacio_y_cancelar(self):
        self.g.respuestas = [self.poco_espacio()]
        self.pulsa_instalar("spotify")
        self.v.panel.boton_cancelar.emit("clicked")
        self.assertEqual(self.g.llamadas, [("spotify", "instalar", {})])

    def test_en_modo_en_vivo_el_aviso_de_espacio_habla_de_la_ram(self):
        v = self.ventana(self.falso(), persistencia=self.VIVO)
        v.gestor.respuestas = [self.poco_espacio()]
        v.pedir_instalar(self.catalogo.por_id["spotify"])
        self.assertIn("RAM", v.panel.nota.get_text())
        self.g.respuestas = [self.poco_espacio()]
        self.pulsa_instalar("spotify")
        self.assertEqual(self.v.panel.nota.get_text(), "", "con guardado no hace falta decirlo")

    def test_falta_navegador_y_luego_poco_espacio_conserva_el_navegador(self):
        self.g.respuestas = [self.falta_navegador(), self.poco_espacio("Queda poco espacio para Google Chrome.")]
        self.pulsa_instalar("netflix")
        self.v.panel.botones[0].emit("clicked")
        self.assertTrue(self.v.panel.get_visible(), "el segundo panel, el del espacio")
        self.assertEqual(self.v.panel.texto.get_text(), "Queda poco espacio para Google Chrome.")
        self.v.panel.botones[0].emit("clicked")
        self.assertEqual(self.g.llamadas, [("netflix", "instalar", {}), ("netflix", "instalar", {"navegador": "chrome"}),
                                           ("netflix", "instalar", {"navegador": "chrome", "forzar": True})])
        self.assertFalse(self.v.panel.get_visible())

    def test_el_panel_se_repite_con_otra_app_sin_dejar_restos(self):
        self.g.respuestas = [self.falta_navegador(), self.poco_espacio()]
        self.pulsa_instalar("netflix")
        self.v.panel.cancelar()
        self.pulsa_instalar("spotify")
        self.assertEqual(len(self.v.panel.botones), 1)
        self.assertNotIn("Netflix", self.v.panel.titulo.get_text())

    # ---- error y ocupado
    def test_error_va_a_la_barra_roja_y_no_se_esconde_sola(self):
        self.g.respuestas = [self.O.Respuesta("error", "Falta Flatpak en este sistema (actualiza la ISO de Infinity OS).", ())]
        self.pulsa_instalar("spotify")
        b = self.v.barra
        self.assertTrue(b.get_visible())
        self.assertTrue(b.has_css_class("error"))
        self.assertEqual(b.etiqueta.get_text(), "Falta Flatpak en este sistema (actualiza la ISO de Infinity OS).")
        self.assertEqual(b._id_ocultar, 0)
        self.assertFalse(self.v.panel.get_visible())

    def test_ocupado_va_a_la_barra_de_aviso(self):
        self.g.respuestas = [self.O.Respuesta("ocupado", "Ya hay una operación en marcha para esta app.", ())]
        self.pulsa_instalar("spotify")
        self.assertTrue(self.v.barra.has_css_class("aviso"))
        self.assertEqual(self.v.barra.etiqueta.get_text(), "Ya hay una operación en marcha para esta app.")
        self.assertTrue(self.v.barra._id_ocultar, "un aviso sí se esconde solo")

    def test_un_error_sin_mensaje_dice_algo(self):
        self.g.respuestas = [self.O.Respuesta("error", "", ())]
        self.pulsa_instalar("spotify")
        self.assertEqual(self.v.barra.etiqueta.get_text(), "No se pudo instalar Spotify.")

    def test_si_el_gestor_lanza_una_excepcion_la_ventana_no_se_cae(self):
        self.g.pedir = mock.Mock(side_effect=RuntimeError("fallo raro"))
        self.pulsa_instalar("spotify")
        self.assertTrue(self.v.barra.has_css_class("error"))
        self.assertIn("fallo raro", self.v.barra.etiqueta.get_text())
        self.assertIn("No se pudo instalar Spotify", self.v.barra.etiqueta.get_text())


# ============================================================================================== abrir
class TestAbrir(BaseVentana):
    def setUp(self):
        super().setUp()
        self.g = self.falso()
        self.v = self.ventana(self.g)

    def instalada(self, ident):
        self.g.estados[ident] = self.O.Estado(True, "", True, "")
        self.g.al_cambiar(ident)

    def abrir(self, ident):
        self.tarjeta(self.v, ident).boton_principal.emit("clicked")

    def test_webapp_abre_su_lanzador(self):
        ruta = self.desktop("netflix")
        self.instalada("netflix")
        self.abrir("netflix")
        self.assertEqual(self.abiertos, [("netflix", ("archivo", ruta))])
        self.assertFalse(self.v.barra.get_visible())

    def test_flatpak_con_flatpak_run(self):
        self.instalada("spotify")
        with mock.patch.object(self.O, "como_abrir", return_value=("orden", ["flatpak", "run", "com.spotify.Client"])):
            self.abrir("spotify")
        self.assertEqual(self.abiertos, [("spotify", ("orden", ["flatpak", "run", "com.spotify.Client"]))])

    def test_apt_por_el_id_del_desktop(self):
        self.instalada("chrome")
        with mock.patch.object(self.O, "como_abrir", return_value=("id", "google-chrome.desktop")) as como:
            self.abrir("chrome")
        como.assert_called_once_with(self.catalogo.por_id["chrome"])
        self.assertEqual(self.abiertos, [("chrome", ("id", "google-chrome.desktop"))])

    def test_si_no_sabe_como_abrirla_lo_dice(self):
        self.instalada("netflix")             # instalada, pero sin lanzador: reinicio de sesión, otro usuario…
        self.abrir("netflix")
        self.assertEqual(self.abiertos, [])
        self.assertEqual(self.v.barra.etiqueta.get_text(),
                         "No encuentro cómo abrir Netflix; reinicia la sesión o ábrela desde el lanzador.")
        self.assertTrue(self.v.barra.has_css_class("aviso"))

    def test_si_abrirla_falla_cuenta_por_que(self):
        self.desktop("claude")
        self.instalada("claude")
        v = self.ventana(self.g, abrir=mock.Mock(side_effect=OSError("su lanzador no es válido")))
        v._tarjetas["claude"].boton_principal.emit("clicked")
        self.assertTrue(v.barra.has_css_class("error"))
        self.assertEqual(v.barra.etiqueta.get_text(), "No se pudo abrir Claude: su lanzador no es válido")
        v2 = self.ventana(self.g, abrir=lambda e, c: False)
        v2._tarjetas["claude"].boton_principal.emit("clicked")
        self.assertEqual(v2.barra.etiqueta.get_text(), "No se pudo abrir Claude.")

    def test_por_defecto_abre_con_lanzar_app_y_la_pantalla_de_la_ventana(self):
        ruta = self.desktop("claude")
        self.instalada("claude")
        v = self.X.VentanaTienda(self.app, gestor=self.g, persistencia=self.PERSISTENTE)
        self.addCleanup(self._cerrar, v)
        with mock.patch.object(self.X, "lanzar_app") as lanzar:
            v._tarjetas["claude"].boton_principal.emit("clicked")
        lanzar.assert_called_once_with(("archivo", ruta), v.get_display())

    def test_abrir_de_verdad_una_webapp_con_true_como_exec(self):
        self.desktop("claude")
        self.instalada("claude")
        v = self.X.VentanaTienda(self.app, gestor=self.g, persistencia=self.PERSISTENTE)
        self.addCleanup(self._cerrar, v)
        v._tarjetas["claude"].boton_principal.emit("clicked")
        self.assertFalse(v.barra.get_visible(), "se lanzó sin quejas")


# ========================================================= actualizar una tarjeta no toca las demás
class TestActualizacionSelectiva(BaseVentana):
    def foto(self, v):
        return ({i: t for i, t in v._tarjetas.items()}, {i: t.reconstrucciones for i, t in v._tarjetas.items()},
                {i: t.boton_principal for i, t in v._tarjetas.items()}, self.hijos(v.rejilla))

    def contar_rejillas(self, v):
        llamadas = []
        original = v._reconstruir_rejilla
        v._reconstruir_rejilla = lambda *a, **k: (llamadas.append(a), original(*a, **k))[1]
        return llamadas

    def test_cambiar_una_app_no_rehace_las_demas_ni_la_rejilla(self):
        v = self.ventana()
        tarjetas, cuentas, botones, hijos = self.foto(v)
        rejillas = self.contar_rejillas(v)
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.ej.evento("progreso", 0.3, "Descargando…")
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        self.ej.terminar(True)
        despues = self.foto(v)
        self.assertEqual(rejillas, [], "la rejilla no se reconstruye")
        self.assertEqual(despues[3], hijos, "los mismos widgets, en el mismo orden")
        for ident, t in tarjetas.items():
            self.assertIs(v._tarjetas[ident], t)
            if ident != "spotify":
                self.assertEqual(t.reconstrucciones, cuentas[ident], f"la tarjeta de {ident} no se tocó")
                self.assertIs(t.boton_principal, botones[ident], f"el botón de {ident} sigue siendo el mismo")
        self.assertGreater(v._tarjetas["spotify"].reconstrucciones, cuentas["spotify"])

    def test_los_avances_de_progreso_no_rehacen_ni_el_pie(self):
        v = self.ventana()
        v.gestor.pedir("spotify", self.O.INSTALAR)
        t = self.tarjeta(v, "spotify")
        barra, cuenta = t.barra, t.reconstrucciones
        rejillas = self.contar_rejillas(v)
        for i in range(1, 40):
            self.ej.evento("progreso", i / 40, f"Paso {i}")
            self.ej.evento("log", None, f"línea {i}")
        self.assertIs(t.barra, barra, "la misma barra: solo cambia su fracción")
        self.assertEqual(t.reconstrucciones, cuenta)
        self.assertAlmostEqual(t.barra.get_fraction(), 39 / 40)
        self.assertEqual(t.texto_estado.get_text(), "Paso 39")
        self.assertEqual(rejillas, [])

    def test_con_instaladas_a_la_vista_un_avance_tampoco_rehace_la_rejilla(self):
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        v = self.ventana()
        v.seleccionar_vista("instaladas")
        rejillas = self.contar_rejillas(v)
        v.gestor.pedir("spotify", self.O.ACTUALIZAR)
        for i in range(10):
            self.ej.evento("progreso", i / 10, "x")
        self.assertEqual(rejillas, [])

    def test_refrescar_solo_toca_lo_que_cambio(self):
        v = self.ventana()
        _t, cuentas, _b, hijos = self.foto(v)
        self.instaladas["brave"] = self.O.Estado(True, "1.0", True, "")
        v.gestor.refrescar()
        self.assertEqual(self.tarjeta(v, "brave").modo, "abrir")
        for ident, t in v._tarjetas.items():
            if ident != "brave":
                self.assertEqual(t.reconstrucciones, cuentas[ident], ident)
        self.assertEqual(self.hijos(v.rejilla), hijos)

    def test_un_cambio_de_una_app_que_no_se_ve_no_falla(self):
        v = self.ventana()
        v.seleccionar_vista("cat:ia")
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.assertEqual(self.tarjeta(v, "spotify").modo, "curso", "su tarjeta, aunque oculta, está al día")
        v.seleccionar_vista("todo")
        self.assertEqual(self.tarjeta(v, "spotify").modo, "curso")

    def test_una_tarjeta_que_aun_no_existe_nace_con_su_estado(self):
        """La primera vez que una app aparece en la rejilla (p. ej. al elegir su categoría) ya refleja lo que pasa."""
        self.instaladas["classroom"] = self.O.Estado(True, "", True, "")
        v = self.ventana()
        v._tarjetas.pop("classroom")
        v.seleccionar_vista("cat:estudio")
        self.assertEqual(self.tarjeta(v, "classroom").modo, "abrir")

    def test_el_gestor_real_con_su_ejecutor_en_hilos_llega_siempre_por_el_hilo_de_la_interfaz(self):
        """Con el Ejecutor de verdad (un hilo + GLib.idle_add) y un plan de una función, los avisos llegan al hilo
        principal: ahí se puede tocar GTK."""
        self.hay_navegador = True
        plan = lambda e, a, b=None, ayudante=None, preferido=None, datos=None: self.O.Plan(
            e, a, [self.O.Paso("Haciendo…", None, lambda: time.sleep(0.05))])
        self.parchear(self.O, "plan_para", plan)
        g = self.O.Gestor(self.catalogo, ejecutor=self.O.Ejecutor(despachar=self.GLib.idle_add), estados=lambda: {
            a.id: self.instaladas.get(a.id, self.O.SIN_INSTALAR) for a in self.catalogo.apps},
            buscar=self.buscar_ejecutable, datos=self.datos, home=self.datos)
        v = self.ventana(g)
        hilos = []
        for nombre in ("_al_cambiar", "_al_terminar_trabajo"):
            original = getattr(v, nombre)
            setattr(v, nombre, lambda *a, original=original: (hilos.append(threading.current_thread()), original(*a))[1])
        g.al_cambiar, g.al_terminar_trabajo = v._al_cambiar, v._al_terminar_trabajo
        self.assertEqual(g.pedir("claude", self.O.INSTALAR).estado, "ok")
        self.instaladas["claude"] = self.O.Estado(True, "", True, "")
        self.assertTrue(self.pump_hasta(lambda: not g.ocupado and v.barra.get_visible()))
        self.assertTrue(hilos)
        self.assertTrue(all(h is threading.main_thread() for h in hilos))
        self.assertEqual(v.barra.etiqueta.get_text(), "Claude instalada.")
        self.assertEqual(self.tarjeta(v, "claude").modo, "abrir")


# ====================================================== el flujo completo con el Gestor real (sin herramientas)
class TestFlujoReal(BaseVentana):
    """Lo que pasa de punta a punta cuando la ventana habla con el Gestor de verdad (ejecutor manual, planes vacíos)."""

    def test_netflix_sin_navegador_elegir_chrome_instala_chrome_y_luego_netflix(self):
        v = self.ventana()
        netflix = self.tarjeta(v, "netflix")
        netflix.boton_principal.emit("clicked")
        self.assertTrue(v.panel.get_visible(), "no hay navegador: pregunta cuál")
        self.assertEqual([b.opcion.texto for b in v.panel.botones], ["Instalar Google Chrome y Netflix", "Instalar Brave y Netflix"])
        self.assertEqual(self.ej.historial, [], "todavía no se ha lanzado nada")
        v.panel.botones[0].emit("clicked")
        self.assertFalse(v.panel.get_visible())
        self.assertEqual(self.tarjeta(v, "chrome").modo, "curso", "primero el navegador")
        self.assertEqual(netflix.modo, "cola", "y Netflix espera su turno")
        self.hay_navegador = True                      # el navegador «se instaló»
        self.instaladas["chrome"] = self.O.Estado(True, "141", True, "")
        self.ej.terminar(True)
        self.assertEqual(netflix.modo, "curso")
        self.assertEqual(v.barra.etiqueta.get_text(), "Google Chrome instalada.")
        self.instaladas["netflix"] = self.O.Estado(True, "", True, "")
        self.ej.terminar(True)
        self.assertEqual(netflix.modo, "abrir")
        self.assertEqual(v.barra.etiqueta.get_text(), "Netflix instalada.")
        self.assertEqual(v._contadores["instaladas"].get_text(), "2")
        self.assertEqual([p.entrada.id for p in self.ej.historial], ["chrome", "netflix"])

    def test_con_un_navegador_instalado_no_pregunta(self):
        self.hay_navegador = True
        v = self.ventana()
        self.tarjeta(v, "claude").boton_principal.emit("clicked")
        self.assertFalse(v.panel.get_visible())
        self.assertEqual(self.tarjeta(v, "claude").modo, "curso")

    def test_poco_espacio_de_verdad_pregunta_y_con_si_instala(self):
        self.espacio["poco"] = True
        v = self.ventana()
        self.tarjeta(v, "spotify").boton_principal.emit("clicked")
        self.assertTrue(v.panel.get_visible())
        self.assertIn("Queda poco espacio para Spotify", v.panel.texto.get_text())
        self.assertEqual(self.ej.historial, [])
        v.panel.botones[0].emit("clicked")
        self.assertEqual(self.tarjeta(v, "spotify").modo, "curso")

    def test_poco_espacio_y_cancelar_no_encola_nada(self):
        self.espacio["poco"] = True
        v = self.ventana()
        self.tarjeta(v, "spotify").boton_principal.emit("clicked")
        v.panel.boton_cancelar.emit("clicked")
        self.assertEqual(self.tarjeta(v, "spotify").modo, "instalar")
        self.assertFalse(v.gestor.ocupado)

    def test_quitar_de_verdad_pasa_por_la_confirmacion_y_luego_por_la_cola(self):
        self.instaladas["spotify"] = self.O.Estado(True, "", True, "")
        v = self.ventana()
        t = self.tarjeta(v, "spotify")
        self.menu_accion(t, "quitar")
        self.assertEqual(self.ej.historial, [])
        t.boton_si.emit("clicked")
        self.assertEqual(t.modo, "curso")
        self.assertEqual(self.ej.historial[0].accion, self.O.QUITAR)
        del self.instaladas["spotify"]
        self.ej.terminar(True)
        self.assertEqual(t.modo, "instalar")
        self.assertEqual(v.barra.etiqueta.get_text(), "Spotify quitada.")

    def test_dos_trabajos_en_cola_y_uno_se_quita_de_la_cola(self):
        v = self.ventana()
        self.tarjeta(v, "spotify").boton_principal.emit("clicked")
        self.tarjeta(v, "brave").boton_principal.emit("clicked")
        brave = self.tarjeta(v, "brave")
        self.assertEqual(brave.modo, "cola")
        brave.boton_cola.emit("clicked")
        self.assertEqual(brave.modo, "instalar")
        self.ej.terminar(True)
        self.assertFalse(v.gestor.ocupado)

    def test_el_error_de_pedir_del_gestor_real_sale_en_la_barra(self):
        v = self.ventana()
        with mock.patch.object(self.O, "plan_para", side_effect=self.O.ErrorPlan("Falta Flatpak en este sistema.")):
            self.tarjeta(v, "spotify").boton_principal.emit("clicked")
        self.assertTrue(v.barra.has_css_class("error"))
        self.assertEqual(v.barra.etiqueta.get_text(), "Falta Flatpak en este sistema.")
        self.assertEqual(self.tarjeta(v, "spotify").modo, "instalar")

    def test_pedir_dos_veces_lo_mismo_es_ocupado(self):
        v = self.ventana()
        spotify = self.catalogo.por_id["spotify"]
        v.pedir_instalar(spotify)
        v.pedir_instalar(spotify)
        self.assertTrue(v.barra.has_css_class("aviso"))
        self.assertEqual(v.barra.etiqueta.get_text(), "Ya hay una operación en marcha para esta app.")


# ================================================================ ratón y teclado reales (XTest)
class TestEventosReales(BaseVentana):
    """Eventos de verdad (los entrega el servidor X, no se llama a ningún método de GTK): un clic llega al botón de una
    tarjeta de la rejilla, la cabecera despliega, se escribe en el buscador, Esc y Ctrl+F. Sin XTest, se saltan."""

    def setUp(self):
        super().setUp()
        try:
            self.x = RatonX()
        except (OSError, AttributeError) as e:
            self.skipTest(f"sin ratón real: {e}")
        try:
            import gi
            gi.require_version("Graphene", "1.0")
            from gi.repository import Graphene
        except (ImportError, ValueError) as e:
            self.skipTest(f"sin Graphene: {e}")
        self.Graphene = Graphene

    def real(self, gestor=None, **kw):
        """Una ventana mostrada y calibrada: se averigua dónde cree GTK que está un puntero puesto en (300, 300)."""
        v = self.mostrar(self.ventana(gestor or self.falso(), **kw), 0.8)
        vistos = []
        movimiento = self.Gtk.EventControllerMotion()
        movimiento.connect("motion", lambda _c, x, y: vistos.append((x, y)))
        v.add_controller(movimiento)
        self.x.mover(300, 300)
        self.pump_hasta(lambda: vistos, 3.0)
        v.remove_controller(movimiento)
        if not vistos:
            self.skipTest("la ventana no recibe el ratón (sin un servidor X de verdad)")
        self.dx, self.dy = 300 - vistos[-1][0], 300 - vistos[-1][1]
        self.v = v
        return v

    def centro(self, widget):
        ok, p = widget.compute_point(self.v, self.Graphene.Point().init(widget.get_width() / 2, widget.get_height() / 2))
        self.assertTrue(ok)
        return p.x + self.dx, p.y + self.dy

    def a_la_vista(self, widget):
        """Desplaza la rejilla lo justo para que `widget` se vea entero."""
        adj = self.v.scroll.get_vadjustment()
        ok, p = widget.compute_point(self.v.rejilla, self.Graphene.Point().init(0, 0))
        arriba, abajo = p.y - 8, p.y + widget.get_height() + 24
        if arriba < adj.get_value():
            adj.set_value(max(0, arriba))
        elif abajo > adj.get_value() + adj.get_page_size():
            adj.set_value(abajo - adj.get_page_size())
        self.pump(0.15)

    def clic(self, widget):
        x, y = self.centro(widget)
        self.x.mover(x, y)
        self.pump(0.12)
        self.x.boton(True)
        self.pump(0.06)
        self.x.boton(False)
        self.pump(0.2)

    def teclear(self, texto):
        for c in texto:
            self.x.tecla(ord(c))
            self.pump(0.04)

    def test_un_clic_real_en_instalar_llega_al_gestor(self):
        v = self.real()
        self.clic(self.tarjeta(v, "netflix").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: v.gestor.llamadas, 3.0))
        self.assertEqual(v.gestor.llamadas, [("netflix", "instalar", {})])

    def test_cada_boton_instalar_de_la_rejilla_responde_a_un_clic_real(self):
        v = self.real()
        for ident in v.visibles():
            with self.subTest(ident=ident):
                boton = self.tarjeta(v, ident).boton_principal
                self.a_la_vista(boton)
                antes = len(v.gestor.llamadas)
                self.clic(boton)
                self.assertTrue(self.pump_hasta(lambda: len(v.gestor.llamadas) > antes, 3.0))
                self.assertEqual(v.gestor.llamadas[antes:], [(ident, "instalar", {})], "el clic llegó al botón de esa tarjeta")

    def test_un_clic_real_en_la_cabecera_despliega_y_en_la_i_no_despliega_dos_veces(self):
        v = self.real()
        t = self.tarjeta(v, "spotify")
        self.clic(t.nombre)
        self.assertTrue(self.pump_hasta(lambda: t.expandida, 3.0), "clic en la cabecera: se despliega")
        self.clic(t.boton_info)
        self.assertTrue(self.pump_hasta(lambda: not t.expandida, 3.0), "clic en la «i»: se pliega")
        self.pump(0.3)
        self.assertFalse(t.expandida, "una sola vez: la cabecera no lo repite")
        self.clic(t.boton_info)
        self.assertTrue(self.pump_hasta(lambda: t.expandida, 3.0))
        self.assertEqual(v.gestor.llamadas, [], "desplegar no pide nada")

    def test_un_clic_real_en_el_enlace_de_la_web(self):
        v = self.real()
        t = self.tarjeta(v, "spotify")
        self.clic(t.nombre)
        self.assertTrue(self.pump_hasta(lambda: t.expandida, 3.0))
        self.pump(0.4)                      # la animación del despliegue
        self.clic(t.boton_web)
        self.assertTrue(self.pump_hasta(lambda: self.urls, 3.0))
        self.assertEqual(self.urls, ["https://www.spotify.com/"])

    def test_clics_reales_en_el_lateral(self):
        v = self.real()
        self.clic(v._filas["cat:ia"])
        self.assertTrue(self.pump_hasta(lambda: v.visibles() == ["claude", "gemini", "notebooklm"], 3.0))
        self.clic(v._filas["instaladas"])
        self.assertTrue(self.pump_hasta(lambda: v.visibles() == [], 3.0))
        self.clic(v._filas["todo"])
        self.assertTrue(self.pump_hasta(lambda: len(v.visibles()) == 12, 3.0))

    def test_teclear_en_el_buscador_filtra_y_esc_lo_limpia(self):
        v = self.real()
        self.clic(v.busqueda)
        self.teclear("pelicula")
        self.assertTrue(self.pump_hasta(lambda: v.busqueda.get_text() == "pelicula", 3.0))
        self.assertTrue(self.pump_hasta(lambda: v.visibles() == ["netflix", "hbo-max"], 3.0), "filtra al vuelo")
        self.x.tecla(RatonX.KEYSYM_ESC)
        self.assertTrue(self.pump_hasta(lambda: v.busqueda.get_text() == "" and len(v.visibles()) == 12, 3.0))

    def test_ctrl_f_real_enfoca_el_buscador_desde_cualquier_sitio(self):
        v = self.real()
        self.clic(self.tarjeta(v, "claude").boton_info)
        self.assertFalse(v.get_focus() is v.busqueda)
        self.x.tecla(ord("f"), control=True)
        self.assertTrue(self.pump_hasta(lambda: v.get_focus() is not None and (
            v.get_focus() is v.busqueda or v.get_focus().is_ancestor(v.busqueda)), 3.0))

    def test_el_panel_con_clics_reales(self):
        g = self.falso()
        g.respuestas = [self.O.Respuesta("falta_navegador", "x", tuple(self.catalogo.navegadores()))]
        v = self.real(g)
        self.clic(self.tarjeta(v, "netflix").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: v.panel.get_visible(), 3.0))
        self.pump(0.4)
        self.clic(v.panel.botones[0])
        self.assertTrue(self.pump_hasta(lambda: len(g.llamadas) == 2, 3.0))
        self.assertEqual(g.llamadas, [("netflix", "instalar", {}), ("netflix", "instalar", {"navegador": "chrome"})])
        self.assertFalse(v.panel.get_visible())
        g.respuestas = [self.O.Respuesta("falta_navegador", "x", tuple(self.catalogo.navegadores()))]
        self.clic(self.tarjeta(v, "claude").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: v.panel.get_visible(), 3.0))
        self.pump(0.4)
        # un clic en el velo, fuera del cuadro, equivale a «Cancelar»
        self.x.mover(self.dx + 60, self.dy + 90)
        self.pump(0.1)
        self.x.boton(True)
        self.x.boton(False)
        self.assertTrue(self.pump_hasta(lambda: not v.panel.get_visible(), 3.0))
        self.assertEqual(len(g.llamadas), 3, "cancelar no pide nada")

    def test_con_el_panel_abierto_un_clic_real_no_llega_a_lo_que_hay_debajo(self):
        g = self.falso()
        g.respuestas = [self.O.Respuesta("falta_navegador", "x", tuple(self.catalogo.navegadores()))]
        v = self.real(g)
        boton_de_debajo = self.tarjeta(v, "youtube").boton_principal
        self.clic(self.tarjeta(v, "netflix").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: v.panel.get_visible(), 3.0))
        self.pump(0.4)
        antes = len(g.llamadas)
        self.clic(boton_de_debajo)               # el velo y el cuadro tapan la rejilla
        self.pump(0.3)
        self.assertEqual(len(g.llamadas), antes)

    def test_la_confirmacion_de_quitar_con_clics_reales(self):
        g = self.falso()
        g.estados["claude"] = self.O.Estado(True, "", True, "")
        v = self.real(g)
        t = self.tarjeta(v, "claude")
        t.pedir_confirmacion()
        self.pump(0.2)
        self.a_la_vista(t.boton_no)
        self.clic(t.boton_no)
        self.assertTrue(self.pump_hasta(lambda: not t.confirmando, 3.0))
        self.assertEqual(g.llamadas, [])
        t.pedir_confirmacion()
        self.pump(0.2)
        self.a_la_vista(t.boton_si)
        self.clic(t.boton_si)
        self.assertTrue(self.pump_hasta(lambda: g.llamadas, 3.0))
        self.assertEqual(g.llamadas, [("claude", "quitar", {})])

    def test_quitar_de_la_cola_y_el_resto_de_botones_de_pie_con_clics_reales(self):
        g = self.falso()
        g.trabajos["netflix"] = self.trabajo_falso("netflix", "instalar", "en_cola")
        g.estados["claude"] = self.O.Estado(True, "", False, "Su navegador ya no está instalado.")
        g.trabajos["spotify"] = self.trabajo_falso("spotify", "instalar", "fallo", "No se pudo instalar Spotify.")
        v = self.real(g)
        g.al_cambiar(None)
        self.clic(self.tarjeta(v, "netflix").boton_cola)
        self.assertTrue(self.pump_hasta(lambda: g.sacados == ["netflix"], 3.0))
        self.a_la_vista(self.tarjeta(v, "claude").boton_principal)
        self.clic(self.tarjeta(v, "claude").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: g.llamadas and g.llamadas[-1] == ("claude", "instalar", {}), 3.0), "Reparar")
        self.clic(self.tarjeta(v, "spotify").boton_principal)
        self.assertTrue(self.pump_hasta(lambda: g.llamadas[-1] == ("spotify", "instalar", {}), 3.0), "Reintentar")
        self.clic(self.tarjeta(v, "spotify").boton_descartar)
        self.assertTrue(self.pump_hasta(lambda: self.tarjeta(v, "spotify").modo == "instalar", 3.0))

    def test_cerrar_la_barra_de_resultado_con_un_clic_real(self):
        v = self.real()
        v.barra.mostrar("Spotify instalada.", "ok", ["una línea"])
        self.pump(0.2)
        self.clic(v.barra.boton_registro)
        self.assertTrue(self.pump_hasta(lambda: v.barra.caja_registro.get_visible(), 3.0))
        self.clic(v.barra.boton_cerrar)
        self.assertTrue(self.pump_hasta(lambda: not v.barra.get_visible(), 3.0))

    def test_clic_real_en_cerrar_el_aviso_del_modo_en_vivo(self):
        v = self.real(persistencia=self.VIVO)
        self.assertTrue(v.franja_vivo.get_visible())
        self.clic(v.boton_cerrar_vivo)
        self.assertTrue(self.pump_hasta(lambda: not v.franja_vivo.get_visible(), 3.0))

    def test_instalar_con_el_teclado_enter_en_el_boton_enfocado(self):
        v = self.real()
        boton = self.tarjeta(v, "gemini").boton_principal
        self.a_la_vista(boton)
        boton.grab_focus()
        self.x.mover(*self.centro(self.tarjeta(v, "gemini")))      # el puntero sobre la ventana: ahí van las teclas
        self.pump(0.15)
        self.x.tecla(0xFF0D)                                       # Return
        self.assertTrue(self.pump_hasta(lambda: v.gestor.llamadas, 3.0))
        self.assertEqual(v.gestor.llamadas, [("gemini", "instalar", {})])


# ===================================================================================== barra de resultado
class TestBarraResultado(BaseVentana):
    def terminar(self, v, ident="spotify", ok=True, mensaje="", lineas=()):
        v.gestor.pedir(ident, self.O.INSTALAR)
        for l in lineas:
            self.ej.evento("log", None, l)
        self.ej.terminar(ok, mensaje)

    def test_empieza_escondida(self):
        v = self.ventana()
        self.assertFalse(v.barra.get_visible())

    def test_exito_en_verde_con_el_mensaje_del_trabajo(self):
        v = self.ventana()
        self.terminar(v)
        b = v.barra
        self.assertTrue(b.get_visible())
        self.assertTrue(b.has_css_class("ok"))
        self.assertFalse(b.has_css_class("error"))
        self.assertEqual(b.etiqueta.get_text(), v.gestor.trabajos["spotify"].mensaje)
        self.assertEqual(b.etiqueta.get_text(), "Spotify instalada.")
        self.assertEqual(self.botones(b), ["Cerrar"], "sin registro, no hay nada que ver")

    def test_fallo_en_rojo_con_ver_registro_y_no_se_esconde(self):
        v = self.ventana(segundos_exito=0.05)
        self.terminar(v, "brave", ok=False, mensaje="No hay conexión.", lineas=["Err:1 https://x", "No se pudo resolver"])
        b = v.barra
        self.assertTrue(b.has_css_class("error"))
        self.assertEqual(b.etiqueta.get_text(), "No se pudo instalar Brave. No hay conexión.")
        self.assertEqual(self.botones(b), ["Ver registro", "Cerrar"])
        self.pump(0.3)
        self.assertTrue(b.get_visible(), "un error se queda hasta que lo cierres")

    def test_un_fallo_sin_nada_en_el_registro_tambien_lo_ofrece_y_lo_dice(self):
        v = self.ventana()
        self.terminar(v, "brave", ok=False, mensaje="Sin red.")
        self.assertEqual(self.botones(v.barra), ["Ver registro", "Cerrar"])
        v.barra.alternar_registro()
        buffer = v.barra.vista_registro.get_buffer()
        self.assertIn("no escribió nada", buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))

    def test_un_aviso_que_no_viene_de_un_trabajo_no_ofrece_registro(self):
        v = self.ventana(self.falso())
        v.gestor.respuestas = [self.O.Respuesta("error", "Falta Flatpak.", ())]
        v.pedir_instalar(self.catalogo.por_id["spotify"])
        self.assertTrue(v.barra.has_css_class("error"))
        self.assertEqual(self.botones(v.barra), ["Cerrar"])

    def test_los_exitos_se_esconden_solos(self):
        v = self.ventana(segundos_exito=0.05)
        self.terminar(v)
        self.assertTrue(v.barra.get_visible())
        self.assertTrue(v.barra._id_ocultar)
        self.assertTrue(self.pump_hasta(lambda: not v.barra.get_visible(), 2.0))
        self.assertEqual(v.barra._id_ocultar, 0)

    def test_por_defecto_ocho_segundos(self):
        self.assertEqual(self.X.SEGUNDOS_EXITO, 8)
        v = self.ventana()
        self.terminar(v)
        with mock.patch.object(self.X.GLib, "timeout_add", wraps=self.X.GLib.timeout_add) as t:
            v.barra.mostrar("otro", "ok")
        self.assertEqual(t.call_args[0][0], 8000)

    def test_cerrar_la_esconde_y_cancela_el_temporizador(self):
        v = self.ventana()
        self.terminar(v)
        self.assertTrue(v.barra._id_ocultar)
        v.barra.boton_cerrar.emit("clicked")
        self.assertFalse(v.barra.get_visible())
        self.assertEqual(v.barra._id_ocultar, 0)
        self.assertEqual(v.barra.etiqueta.get_text(), "")

    def test_cerrar_la_ventana_cancela_el_temporizador(self):
        v = self.ventana()
        self.terminar(v)
        self.assertTrue(v.barra._id_ocultar)
        v.liberar()
        self.assertEqual(v.barra._id_ocultar, 0)

    def test_un_resultado_nuevo_reemplaza_al_anterior(self):
        v = self.ventana()
        self.terminar(v, "spotify")
        self.terminar(v, "brave", ok=False, mensaje="Sin red.")
        self.assertTrue(v.barra.has_css_class("error"))
        self.assertFalse(v.barra.has_css_class("ok"))
        self.assertEqual(v.barra._id_ocultar, 0, "el error no hereda el temporizador del éxito")
        self.terminar(v, "antigravity")
        self.assertTrue(v.barra.has_css_class("ok"))
        self.assertFalse(v.barra.has_css_class("error"))

    def test_ver_registro_enseña_las_ultimas_400_lineas(self):
        v = self.ventana()
        self.terminar(v, "brave", ok=False, mensaje="Sin red.", lineas=[f"línea {i}" for i in range(500)])
        b = v.barra
        self.assertFalse(b.caja_registro.get_visible())
        b.boton_registro.emit("clicked")
        self.assertTrue(b.caja_registro.get_visible())
        buffer = b.vista_registro.get_buffer()
        lineas = buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False).split("\n")
        self.assertEqual(len(lineas), 400)
        self.assertEqual((lineas[0], lineas[-1]), ("línea 100", "línea 499"))
        self.assertEqual(b.boton_registro.get_label(), "Ocultar registro")
        self.assertTrue(b.vista_registro.get_monospace(), "una caja monoespaciada")
        self.assertFalse(b.vista_registro.get_editable())
        self.assertIsInstance(b.vista_registro.get_parent(), self.Gtk.ScrolledWindow)
        b.boton_registro.emit("clicked")
        self.assertFalse(b.caja_registro.get_visible())
        self.assertEqual(b.boton_registro.get_label(), "Ver registro")

    def test_el_registro_abierto_no_se_esconde_solo(self):
        v = self.ventana(segundos_exito=0.05)
        self.terminar(v, "spotify", lineas=["Instalando…", "Listo."])
        v.barra.alternar_registro()
        self.pump(0.3)
        self.assertTrue(v.barra.get_visible(), "si lo estás leyendo, no se te quita de delante")

    def test_el_registro_se_vacia_al_cerrar_y_no_pasa_al_siguiente_trabajo(self):
        v = self.ventana()
        self.terminar(v, "brave", ok=False, mensaje="x", lineas=["secreto del primero"])
        v.barra.alternar_registro()
        self.terminar(v, "antigravity")
        self.assertFalse(v.barra.caja_registro.get_visible())
        buffer = v.barra.vista_registro.get_buffer()
        self.assertEqual(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False), "")
        self.assertFalse(v.barra.boton_registro.get_visible())

    def test_la_barra_aislada(self):
        b = self.X.BarraResultado(segundos_exito=0.05)
        b.mostrar("Todo bien.", "ok", ["a", "b"])
        self.assertEqual(self.botones(b), ["Ver registro", "Cerrar"])
        b.mostrar("Cuidado.", "aviso")
        self.assertTrue(b.has_css_class("aviso"))
        self.assertEqual(self.botones(b), ["Cerrar"])
        b.mostrar("Para siempre.", "ok", segundos=0)
        self.assertEqual(b._id_ocultar, 0)
        b.ocultar()


# ===================================================================== cerrar con instalaciones en curso
class TestCerrar(BaseVentana):
    def setUp(self):
        super().setUp()
        # hold y release de mentira, puestos ANTES de crear ninguna ventana: así siguen puestos cuando los cleanups
        # cierran las ventanas (si no, un release de verdad sin su hold dispararía un aviso de GLib)
        self.hold, self.release = mock.Mock(), mock.Mock()
        self.parchear(self.app, "hold", self.hold)
        self.parchear(self.app, "release", self.release)

    def test_con_un_trabajo_en_curso_se_oculta_y_no_sale(self):
        v = self.mostrar(self.ventana())
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.assertTrue(v.gestor.ocupado)
        v.close()
        self.assertFalse(v.get_visible(), "oculta")
        self.assertIn(v, self.app.get_windows(), "no se destruye")
        self.assertFalse(v._cerrada)
        self.hold.assert_called_once_with()
        self.release.assert_not_called()
        self.assertTrue(v._retenida)
        self.assertEqual(len(self.ej.pendientes), 1, "no se mata el trabajo")

    def test_cerrar_varias_veces_solo_retiene_una(self):
        v = self.ventana()
        v.gestor.pedir("spotify", self.O.INSTALAR)
        self.assertTrue(v.emit("close-request"), "el handler frena el cierre")
        self.assertTrue(v.emit("close-request"))
        self.hold.assert_called_once_with()

    def test_al_acabar_el_ultimo_trabajo_se_libera_y_la_ventana_se_despide(self):
        v = self.mostrar(self.ventana())
        g = v.gestor
        g.pedir("spotify", self.O.INSTALAR)
        g.pedir("brave", self.O.INSTALAR)
        v.close()
        self.ej.terminar(True)
        self.release.assert_not_called()                      # queda el de Brave
        self.assertIn(v, self.app.get_windows())
        self.assertFalse(v.get_visible(), "al terminar uno con la ventana oculta no se muestra")
        self.ej.terminar(True)
        self.release.assert_called_once_with()
        self.assertNotIn(v, self.app.get_windows(), "sin trabajos ni ventana visible, la aplicación puede salir")
        self.assertTrue(v._cerrada)
        self.assertFalse(g.ocupado)

    def test_si_se_vuelve_a_mostrar_antes_de_acabar_la_ventana_no_se_va(self):
        v = self.mostrar(self.ventana())
        v.gestor.pedir("spotify", self.O.INSTALAR)
        v.close()
        v.present()
        self.pump(0.2)
        self.assertTrue(v.get_visible())
        self.ej.terminar(True)
        self.release.assert_called_once_with()
        self.assertIn(v, self.app.get_windows())
        self.assertFalse(v._cerrada)
        self.assertTrue(v.barra.get_visible(), "ahora sí se ve el resultado")

    def test_sin_trabajos_cerrar_sale_normal(self):
        v = self.mostrar(self.ventana())
        v.close()
        self.hold.assert_not_called()
        self.assertTrue(v._cerrada)
        self.assertNotIn(v, self.app.get_windows())

    def test_cerrar_suelta_al_gestor_y_los_temporizadores(self):
        v = self.mostrar(self.ventana())
        g = v.gestor
        v.barra.mostrar("algo", "ok")
        self.assertTrue(v.barra._id_ocultar)
        v.close()
        self.assertEqual(v.barra._id_ocultar, 0)
        self.assertNotEqual(g.al_cambiar, v._al_cambiar, "el gestor ya no llama a una ventana muerta")
        g.al_cambiar("spotify")
        g.al_terminar_trabajo(self.trabajo_falso("spotify", "instalar", "listo", "x"))

    def test_cerrar_con_el_panel_abierto_y_trabajo_en_marcha_lo_cierra(self):
        g = self.falso()
        v = self.ventana(g)
        g.ocupado = True
        g.respuestas = [self.O.Respuesta("poco_espacio", "x", ())]
        v.pedir_instalar(self.catalogo.por_id["spotify"])
        self.assertTrue(v.panel.get_visible())
        v.emit("close-request")
        self.assertFalse(v.panel.get_visible())
        self.assertTrue(v.cuerpo.get_sensitive())

    def test_con_el_estado_de_un_trabajo_que_falla_al_final_tambien_se_despide(self):
        v = self.mostrar(self.ventana())
        v.gestor.pedir("brave", self.O.INSTALAR)
        v.close()
        self.ej.terminar(False, "Sin red.")
        self.release.assert_called_once_with()
        self.assertNotIn(v, self.app.get_windows())

    def test_la_aplicacion_se_despide_sola_con_el_bucle_de_verdad(self):
        """Gtk.Application.run() de verdad: cierra con un trabajo en marcha (la app sigue), acaba el trabajo (run vuelve)."""
        with mock.patch.object(self.X, "APP_ID", f"org.infinity.test.tienda{time.time_ns()}"):
            app = self.X.AppTienda(CATALOGO)
        hechos = {}

        def paso1():
            v = app.ventana
            v.gestor.ejecutor = self.ej
            v.gestor.pedir("spotify", self.O.INSTALAR)
            v.close()
            hechos["oculta"] = not v.get_visible()
            hechos["viva"] = v in app.get_windows()
            self.GLib.timeout_add(500, paso2)
            return False

        def paso2():
            hechos["sigue"] = True               # si run() hubiera vuelto, no se llegaría aquí
            self.ej.terminar(True)
            return False

        app.connect("activate", lambda a: self.GLib.timeout_add(200, paso1))
        self.GLib.timeout_add(6000, lambda: (hechos.setdefault("seguridad", True), app.quit(), False)[2])
        with mock.patch.object(self.O, "consultar_estados", return_value={}):
            app.run([])
        self.assertTrue(hechos.get("oculta"))
        self.assertTrue(hechos.get("viva"))
        self.assertTrue(hechos.get("sigue"), "la aplicación seguía viva con la ventana oculta")
        self.assertNotIn("seguridad", hechos, "run() volvió solo, al acabar el último trabajo")


class TestAplicacion(BaseVentana):
    def aplicacion(self, **kw):
        with mock.patch.object(self.X, "APP_ID", f"org.infinity.test.tienda{time.time_ns()}"):
            app = self.X.AppTienda(CATALOGO, **kw)
        app.register(None)
        return app

    def activar(self, app):
        with mock.patch.object(self.O, "consultar_estados", return_value={}):
            app.activate()
        self.addCleanup(lambda v=app.ventana: self._cerrar(v))
        return app.ventana

    def test_una_instancia_unica_y_la_segunda_trae_la_ventana_al_frente(self):
        app = self.aplicacion()
        v = self.activar(app)
        self.assertIsNotNone(v)
        self.assertTrue(v.get_visible())
        self.assertIs(self.activar(app), v, "activar otra vez trae la misma ventana")

    def test_con_la_ventana_oculta_un_segundo_infinity_tienda_la_vuelve_a_mostrar(self):
        app = self.aplicacion()
        v = self.activar(app)
        v.gestor.ejecutor = self.ej
        v.gestor.pedir("spotify", self.O.INSTALAR)
        v.emit("close-request")
        self.assertFalse(v.get_visible())
        with mock.patch.object(self.Gtk.Window, "present") as presentar:
            self.assertIs(self.activar(app), v)
        presentar.assert_called_once()                    # do_activate hace present()
        v.present()
        self.assertTrue(v.get_visible())
        v._soltar_retencion()

    def test_si_la_ventana_se_despidio_se_crea_otra(self):
        app = self.aplicacion()
        v = self.mostrar(self.activar(app))
        v.close()
        self.assertNotIn(v, app.get_windows())
        nueva = self.activar(app)
        self.assertIsNot(nueva, v)
        self.assertIn(nueva, app.get_windows())

    def test_la_aplicacion_pasa_el_catalogo_a_la_ventana(self):
        app = self.aplicacion()
        v = self.activar(app)
        self.assertEqual(len(v.catalogo.apps), 12)
        self.assertEqual(v.error_catalogo, "")

    def test_modo_oscuro(self):
        app = self.aplicacion()
        self.activar(app)
        self.assertTrue(self.Gtk.Settings.get_default().get_property("gtk-application-prefer-dark-theme"))

    def test_main_lee_catalogo_y_no_exige_nada(self):
        class AppFalsa:
            hechos = []

            def __init__(self, ruta=None):
                self.ruta = ruta
                AppFalsa.hechos.append(("nueva", ruta))

            def run(self, argv):
                AppFalsa.hechos.append(("run", argv))
                return 7

        with mock.patch.object(self.X, "AppTienda", AppFalsa):
            self.assertEqual(self.X.main(["infinity-tienda"]), 7)
            self.assertEqual(self.X.main(["infinity-tienda", "--catalogo", "/tmp/otro.json"]), 7)
            self.assertEqual(self.X.main(["infinity-tienda", "--catalogo=/tmp/tercero.json", "--gtk-debug=misc"]), 7)
        self.assertEqual(AppFalsa.hechos, [("nueva", None), ("run", ["infinity-tienda"]),
                                           ("nueva", "/tmp/otro.json"), ("run", ["infinity-tienda"]),
                                           ("nueva", "/tmp/tercero.json"), ("run", ["infinity-tienda", "--gtk-debug=misc"])],
                         "lo que no es del Tienda se le deja a GTK")

    def test_main_ayuda_y_errores_en_espanol_sin_abrir_nada(self):
        import io
        with mock.patch.object(self.X, "AppTienda") as app, mock.patch.object(self.X.sys, "stdout", io.StringIO()) as salida, \
                mock.patch.object(self.X.sys, "stderr", io.StringIO()) as error:
            self.assertEqual(self.X.main(["infinity-tienda", "--help"]), 0)
            self.assertEqual(self.X.main(["infinity-tienda", "--catalogo"]), 2)
            self.assertEqual(self.X.main(["infinity-tienda", "--catalogo="]), 2)
        app.assert_not_called()
        self.assertIn("uso: infinity-tienda", salida.getvalue())
        self.assertIn("--catalogo RUTA", salida.getvalue())
        self.assertIn("necesita la ruta", error.getvalue())


# ============================================================================ aviso del modo en vivo
class TestModoEnVivo(BaseVentana):
    TEXTO = "Estás en modo en vivo sin guardado: lo que instales se perderá al apagar."

    def test_en_vivo_la_franja_es_visible_con_su_texto_y_su_pista(self):
        v = self.ventana(persistencia=self.VIVO)
        self.assertTrue(v.franja_vivo.get_visible())
        self.assertIn(self.TEXTO, self.textos(v.franja_vivo))
        self.assertEqual(v.franja_vivo.get_tooltip_text(), self.VIVO.pista)
        self.assertTrue(v.franja_vivo.has_css_class("tienda-vivo"))

    def test_con_guardado_o_instalado_no_hay_franja(self):
        for modo in ("persistente", "instalado"):
            with self.subTest(modo=modo):
                p = self.ajustes.Persistencia(modo, "x", "y", "")
                v = self.ventana(persistencia=p)
                self.assertFalse(v.franja_vivo.get_visible())

    def test_la_franja_se_descarta(self):
        v = self.ventana(persistencia=self.VIVO)
        v.boton_cerrar_vivo.emit("clicked")
        self.assertFalse(v.franja_vivo.get_visible())

    def test_se_calcula_al_abrir_con_estado_persistencia_del_sistema(self):
        with mock.patch.object(self.ajustes, "estado_persistencia", return_value=self.VIVO) as estado:
            v = self.X.VentanaTienda(self.app, gestor=self.gestor(), abrir=lambda e, c: None)
            self.addCleanup(self._cerrar, v)
            self.pump(0.1)
        estado.assert_called_once_with()
        self.assertTrue(v.franja_vivo.get_visible())

    def test_una_funcion_tambien_vale_y_un_fallo_al_leer_no_pone_la_franja(self):
        v = self.ventana(persistencia=lambda: self.VIVO)
        self.assertTrue(v.franja_vivo.get_visible())
        v2 = self.ventana(persistencia=mock.Mock(side_effect=OSError("sin /proc")))
        self.assertFalse(v2.franja_vivo.get_visible())

    def test_el_estado_real_del_equipo_de_pruebas_no_rompe(self):
        v = self.X.VentanaTienda(self.app, gestor=self.gestor(), abrir=lambda e, c: None)
        self.addCleanup(self._cerrar, v)
        self.assertIsNotNone(v.franja_vivo)


# ================================================================================== robustez
class TestRobustez(BaseVentana):
    def test_catalogo_vacio(self):
        vacio = self.C.Catalogo(self.catalogo.categorias, [])
        g = GestorFalso(self.O, vacio)
        v = self.ventana(g)
        self.assertEqual(v.visibles(), [])
        self.assertEqual(v.pila.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_titulo.get_text(), "No hay aplicaciones")
        self.assertFalse(v.vacio_boton.get_visible())
        self.assertEqual(v._contadores["todo"].get_text(), "0")
        v.buscar("algo")
        v.seleccionar_vista("instaladas")
        v.seleccionar_vista("cat:ia")
        g.refrescar()
        self.tecla(v, self.Gdk.KEY_Escape)
        v.lookup_action("actualizar-estado").activate(None)

    def test_catalogo_sin_categorias(self):
        v = self.ventana(GestorFalso(self.O, self.C.Catalogo([], list(self.catalogo.apps))))
        self.assertEqual([f.vista for f in self.filas(v)], ["todo", "instaladas"])
        self.assertEqual(len(v.visibles()), 12)

    def test_un_catalogo_que_no_se_puede_leer_se_cuenta_en_la_ventana(self):
        v = self.X.VentanaTienda(self.app, ruta_catalogo=os.path.join(self.tmp, "no-existe.json"), abrir=lambda e, c: None,
                                 persistencia=self.PERSISTENTE)
        self.addCleanup(self._cerrar, v)
        self.assertEqual(v.visibles(), [])
        self.assertEqual(v.vacio_titulo.get_text(), "No se pudo abrir el catálogo")
        self.assertIn("no se pudo leer el catálogo", v.vacio_texto.get_text())
        self.assertIn("no-existe.json", v.error_catalogo)

    def test_un_catalogo_pasado_a_mano_se_usa(self):
        un_poco = self.C.Catalogo(self.catalogo.categorias, self.catalogo.apps[:3])
        self.parchear(self.O, "consultar_estados", lambda *a, **k: {})      # ni dpkg-query: el Gestor se crea de verdad
        v = self.X.VentanaTienda(self.app, catalogo=un_poco, abrir=lambda e, c: None, persistencia=self.PERSISTENTE)
        self.addCleanup(self._cerrar, v)
        self.assertEqual(v.visibles(), ["spotify", "antigravity", "netflix"])

    def test_sin_iconos_no_hay_excepciones_y_cada_tarjeta_tiene_su_simbolo(self):
        with mock.patch.object(self.C, "ruta_icono", return_value=None), \
                mock.patch.object(self.X, "ruta_icono_tienda", return_value=None):
            v = self.mostrar(self.ventana())
            self.assertEqual(len(v.visibles()), 12)
            for t in v._tarjetas.values():
                self.assertNotEqual(t.icono.get_icon_name(), "image-missing")
            self.assertIn("Tienda", self.textos(v.get_titlebar()))

    def test_con_iconos_que_son_basura_tampoco(self):
        roto = os.path.join(self.tmp, "roto.svg")
        with open(roto, "w") as f:
            f.write("<svg>")
        with mock.patch.object(self.C, "ruta_icono", return_value=roto), mock.patch.object(self.X, "ruta_icono_tienda", return_value=roto):
            v = self.mostrar(self.ventana())
            acerca = mock.patch.object(self.Gtk.AboutDialog, "present")
            with acerca:
                v.lookup_action("acerca").activate(None)
            self.assertEqual(len(v.visibles()), 12)

    def test_ids_y_colores_nuevos_en_el_catalogo_no_rompen_el_estilo(self):
        import dataclasses
        rara = dataclasses.replace(self.catalogo.por_id["claude"], id="otra-app", color="#123456")
        catalogo = self.C.Catalogo(self.catalogo.categorias, [rara])
        v = self.ventana(GestorFalso(self.O, catalogo))
        franja = self.hijos(v._tarjetas["otra-app"])[0]
        self.assertTrue(franja.has_css_class("tienda-color-otra-app"))

    def test_un_trabajo_sin_mensaje_o_sin_registro_no_rompe_la_barra(self):
        g = self.falso()
        v = self.ventana(g)
        t = self.trabajo_falso("spotify", "instalar", "listo", "")
        g.al_terminar_trabajo(t)
        self.assertEqual(v.barra.etiqueta.get_text(), "Spotify: hecho.")
        t2 = self.trabajo_falso("spotify", "instalar", "fallo", "")
        g.al_terminar_trabajo(t2)
        self.assertEqual(v.barra.etiqueta.get_text(), "Spotify: no se pudo.")

    def test_mucho_trasiego_de_eventos_con_la_ventana_visible(self):
        v = self.mostrar(self.ventana())
        g = v.gestor
        for ident in ("spotify", "antigravity", "brave"):
            g.pedir(ident, self.O.INSTALAR)
        for i in range(100):
            self.ej.evento("progreso", i / 100, f"paso {i}")
            self.ej.evento("log", None, f"línea {i}")
        self.pump(0.2)
        self.ej.terminar(False, "Sin red.")
        self.pump(0.1)
        self.ej.terminar(True)
        self.ej.terminar(True)
        self.pump(0.2)
        self.assertFalse(g.ocupado)
        self.assertEqual(self.tarjeta(v, "spotify").modo, "fallo")

    def test_accesibilidad_nombre_en_los_botones_de_solo_icono(self):
        self.instaladas["chrome"] = self.O.Estado(True, "1", True, "")
        g = self.gestor()
        v = self.ventana(g)
        g.pedir("brave", self.O.INSTALAR)
        self.ej.terminar(False, "x")                 # Brave falla: su tarjeta tiene la «✕»
        g.pedir("spotify", self.O.INSTALAR)          # Spotify en curso: su tarjeta tiene la barra
        v.franja_vivo.set_visible(True)
        etiqueta = self.Gtk.AccessibleProperty.LABEL
        con_nombre = {
            "buscador": v.busqueda, "menú de la cabecera": v.menu, "menú de la tarjeta": self.tarjeta(v, "chrome").menu,
            "«i»": self.tarjeta(v, "netflix").boton_info, "descartar el error": self.tarjeta(v, "brave").boton_descartar,
            "descartar el aviso del modo en vivo": v.boton_cerrar_vivo, "barra de progreso": self.tarjeta(v, "spotify").barra,
            "botón Instalar": self.tarjeta(v, "gemini").boton_principal,
            "enlace": self.tarjeta(v, "netflix").boton_web, "registro": v.barra.vista_registro,
        }
        for nombre, widget in con_nombre.items():
            with self.subTest(nombre):
                self.assertTrue(self.Gtk.test_accessible_has_property(widget, etiqueta), f"«{nombre}» sin nombre accesible")

    def test_accesibilidad_las_acciones_de_cada_tarjeta_dicen_de_que_app_hablan(self):
        v = self.ventana()
        g = v.gestor
        g.pedir("spotify", self.O.INSTALAR)
        self.assertEqual(self.tarjeta(v, "gemini").boton_principal.get_label(), "Instalar")
        self.assertTrue(self.Gtk.test_accessible_has_property(self.tarjeta(v, "gemini").boton_principal,
                                                              self.Gtk.AccessibleProperty.LABEL))
        for t in v._tarjetas.values():
            self.assertTrue(self.Gtk.test_accessible_has_property(t.boton_info, self.Gtk.AccessibleProperty.LABEL), t.entrada.id)


if __name__ == "__main__":
    unittest.main()
