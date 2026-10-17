"""PRUEBAS DEL MOTOR RETIRADO (Gemini) de pAnkI: panki_app.py ya no lo usa (ver docs/panki-web-diseno.md
y panki_app.py). Este módulo entero se salta (abajo); se conserva por si ese motor se retoma. Las
pruebas del motor actual (la página web alojada en WebKitGTK) están en test_panki_app_web.py; lo
reutilizable de aquí (el análisis de --preguntar y las de integración sin GTK) se copió allí.

Lo que sigue es el docstring y las pruebas ORIGINALES, sin tocar, tal como probaban la ventana de chat
nativa (tarjetas de aprobación, burbujas con Markdown, hilos del agente) que panki_app.py tenía antes:

La ventana de chat de pAnkI (panki_app.py) y lo que la rodea: lanzador, menú, atajo y regla de Hyprland.

Las pruebas de la ventana necesitan GTK 4 y una pantalla (xvfb-run); se saltan si no están, igual que
test_launcher.py y test_contrasena_ventana.py. Usan un agente y un núcleo FALSOS: no hay red ni clave
real. Una prueba al final arranca la ventana con los módulos reales (sin clave: tiene que salir la tarjeta
de la clave) y se salta si el núcleo no se puede cargar.

Las pruebas de integración (lanzador, .desktop, build.sh, hyprland.conf) no necesitan GTK.
Si la variable HYPRLAND_VERIFICAR apunta a un ejecutable que acepta la ruta de un hyprland.conf y ejecuta
`Hyprland --verify-config` con él, también se comprueba la configuración con el Hyprland real (más un
control negativo con una línea inválida).
"""
import os
import re
import stat
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

raise unittest.SkipTest("Motor Gemini retirado de panki_app.py; ver test_panki_app_web.py para el motor web "
                        "actual. Este módulo se conserva por si el motor Gemini se retoma.")

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
PANTALLA = bool(os.environ.get("DISPLAY"))
HYPR_CONF = os.path.join(ROOT, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")

CLAVE_FALSA = "AIzaSyD-clave-de-prueba-0123456789abcdef"


def _leer(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


# ------------------------------------------------------------------------------------------ falsos
class ErrorPanki(Exception):
    """Como la del núcleo: mensaje para el usuario, el código HTTP real y si Gemini rechazó la clave."""

    def __init__(self, mensaje, codigo=None, clave_invalida=False):
        super().__init__(mensaje)
        self.codigo = codigo
        self.clave_invalida = clave_invalida


class NucleoFalso:
    ErrorPanki = ErrorPanki

    def __init__(self, clave=True):
        self.clave = clave
        self.guardadas = []
        self.borrados = 0
        self.rechazar_con = None

    def existe_clave(self):
        return self.clave

    def guardar_clave(self, clave):
        if self.rechazar_con is not None:
            raise self.rechazar_con
        self.guardadas.append(clave)
        self.clave = True

    def borrar_clave(self):
        self.borrados += 1
        self.clave = False


class AgenteFalso:
    """enviar(texto, alevento), cancelar() y reiniciar(), como el Agente real. `guion` decide qué hace enviar."""

    def __init__(self, aprobar, guion=None):
        self.aprobar = aprobar
        self.guion = guion or (lambda agente, texto, alevento: "Respuesta a: " + texto)
        self.enviados = []
        self.hilos = []
        self.cancelado = threading.Event()
        self.reinicios = 0
        self.decisiones = []
        self.permisos = []  # textos de alcance de los permisos "en esta conversación" (como Agente.permisos_activos)
        self.revocaciones = 0

    def enviar(self, texto, alevento):
        self.enviados.append(texto)
        self.hilos.append(threading.current_thread())
        return self.guion(self, texto, alevento)

    def cancelar(self):
        self.cancelado.set()

    def reiniciar(self):
        self.reinicios += 1
        self.permisos = []

    def permisos_activos(self):
        return list(self.permisos)

    def revocar_permisos(self, clave=None):
        self.revocaciones += 1
        n, self.permisos = len(self.permisos), []
        return n


def _gtk():
    """(P, Gtk, Gdk, GLib, Solicitud) o SkipTest si no hay GTK 4."""
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, GLib, Gtk
    except (ImportError, ValueError) as e:
        raise unittest.SkipTest(f"sin GTK 4: {e}")
    sys.path.insert(0, SHELL)
    from infinity_shell import panki_app as P
    return P, Gtk, Gdk, GLib


class Solicitud:
    """Lo mismo que panki_herramientas.Solicitud, sin depender de ese módulo."""

    def __init__(self, titulo="Mover 2 archivos en ~/Descargas", detalle=None, riesgo="medio",
                 recordable="carpeta:/home/u/Descargas", herramienta="aplicar_organizacion",
                 alcance="mover archivos de ~/Descargas a sus subcarpetas"):
        self.herramienta = herramienta
        self.titulo = titulo
        self.detalle = detalle if detalle is not None else ["a.jpg -> Imágenes/a.jpg", "b.pdf -> Documentos/b.pdf"]
        self.riesgo = riesgo
        self.recordable = recordable
        self.alcance = alcance if recordable is not None else None


# ------------------------------------------------------------------------------------------ funciones puras
class TestFuncionesPuras(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.P = _gtk()[0]

    def test_argumentos(self):
        P = self.P
        self.assertEqual(P.interpretar_argumentos([]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "qué", "hora", "es"]), ("qué hora es", False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar=hola mundo"]), ("hola mundo", False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar"]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "  "]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["-h"]), (None, True, None))
        self.assertEqual(P.interpretar_argumentos(["--help"]), (None, True, None))
        self.assertIsNotNone(P.interpretar_argumentos(["--no-existe"])[2])

    def test_lo_que_va_tras_preguntar_es_texto_aunque_parezca_una_opcion(self):
        P = self.P
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "--help"])[:2], ("--help", False))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "-h", "--version"])[0], "-h --version")

    def test_pregunta_muy_larga_se_recorta(self):
        P = self.P
        texto = P.interpretar_argumentos(["--preguntar", "x" * (P.MAX_PREGUNTA + 500)])[0]
        self.assertEqual(len(texto), P.MAX_PREGUNTA)

    def test_juntar_pregunta_evita_que_gapplication_lea_el_texto_como_opcion(self):
        P = self.P
        self.assertEqual(P.juntar_pregunta(["p", "--preguntar", "a", "b"]), ["p", "--preguntar=a b"])
        self.assertEqual(P.juntar_pregunta(["p", "--preguntar", "--gapplication-service"]),
                         ["p", "--preguntar=--gapplication-service"])
        self.assertEqual(P.juntar_pregunta(["p"]), ["p"])
        self.assertEqual(P.juntar_pregunta(["p", "--help"]), ["p", "--help"])

    def test_texto_visible_destapa_los_caracteres_de_direccion(self):
        P = self.P
        self.assertEqual(P.texto_visible("~/Descargas/foto.jpg"), "~/Descargas/foto.jpg")
        engano = "~/Descargas/gpj.‮txt"
        visible = P.texto_visible(engano)
        self.assertNotIn("‮", visible)
        self.assertIn("‹U+202E›", visible)
        self.assertEqual(P.texto_visible("a\x00b"), "a‹U+0000›b")
        self.assertEqual(P.texto_visible("línea\ncon\ttabulación"), "línea\ncon\ttabulación")

    def test_parece_clave(self):
        P = self.P
        self.assertTrue(P.parece_clave("mi clave es " + CLAVE_FALSA + " gracias"))
        self.assertTrue(P.parece_clave("AIza" + "x" * 30))
        self.assertFalse(P.parece_clave("AIza" + "x" * 29))
        self.assertFalse(P.parece_clave("hola, ¿qué tal? AIzaCorta"))
        self.assertFalse(P.parece_clave(""))

    def test_texto_visible_destapa_tambien_huecos_y_combinantes(self):
        P = self.P
        for nombre, texto, codigo in (("braille en blanco", "informe.pdf" + "\u2800" * 3 + ".desktop", "U+2800"),
                                      ("relleno hangul", "a\u3164b", "U+3164"),
                                      ("relleno hangul 2", "a\u115fb\u1160c", "U+115F"),
                                      ("zalgo", "informe.pdf" + "\u0489" * 5 + ".sh", "U+0489"),
                                      ("combinante", "x\u0300\u0300y", "U+0300"),
                                      ("selector de variación", "a\ufe0fb", "U+FE0F")):
            with self.subTest(nombre):
                visible = P.texto_visible(texto)
                self.assertIn("‹" + codigo, visible)
                for c in "\u2800\u3164\u115f\u1160\u0489\u0300\ufe0f":
                    self.assertNotIn(c, visible)

    def test_las_tildes_normales_no_se_tocan_y_se_normaliza_a_nfc(self):
        P = self.P
        self.assertEqual(P.texto_visible("Descripción: canción ñandú"), "Descripción: canción ñandú")
        descompuesto = "e\u0301sta"  # e + acento combinante: tras NFC es «ésta», sin marcas raras
        self.assertEqual(P.texto_visible(descompuesto), "ésta")

    def test_una_linea_limpia_y_recorta_por_el_medio(self):
        P = self.P
        t = P.una_linea("a" * 200 + ".pdf", 40)
        self.assertEqual(len(t), 40)
        self.assertTrue(t.endswith(".pdf"), "se conserva el final (la extensión)")
        self.assertIn("…", t)
        self.assertNotIn("\n", P.una_linea("uno\ndos\r\ntres"))
        self.assertNotIn("\u202e", P.una_linea("a\u202eb"))
        self.assertEqual(P.una_linea("corta"), "corta")

    def test_el_texto_del_modelo_no_dibuja_marcos_ni_reglas(self):
        P = self.P
        entra = "Hola\n\n---\n\n> una cita\n\n```\ncodigo\n```\n\n┌──────┐\n│ hola │\n└──────┘\n\n▎barra\n\n***\ntexto"
        sale = P.filtrar_texto_modelo(entra)
        for raro in ("---", "```", "> ", "┌", "─", "│", "▎", "***"):
            self.assertNotIn(raro, sale, raro)
        for queda in ("Hola", "una cita", "codigo", "hola", "barra", "texto"):
            self.assertIn(queda, sale)
        self.assertNotIn("\n\n\n", sale)

    def test_sin_secretos(self):
        P = self.P
        self.assertNotIn(CLAVE_FALSA, P.sin_secretos(f"fallo con {CLAVE_FALSA} en la petición"))
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "valor-secreto-de-entorno"}):
            self.assertNotIn("valor-secreto-de-entorno", P.sin_secretos("x valor-secreto-de-entorno y"))

    def test_registro_privado_y_sin_clave(self):
        P = self.P
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CACHE_HOME": tmp}):
            P.registrar(f"fallo con la clave {CLAVE_FALSA}")
            ruta = os.path.join(tmp, "infinity", "panki.log")
            self.assertEqual(P.ruta_registro(), ruta)
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o600)
            contenido = _leer(ruta)
            self.assertIn("fallo con la clave", contenido)
            self.assertNotIn(CLAVE_FALSA, contenido)

    def test_registro_nunca_lanza(self):
        P = self.P
        with tempfile.TemporaryDirectory() as tmp:
            bloqueo = os.path.join(tmp, "es-un-archivo")
            open(bloqueo, "w").close()
            with mock.patch.dict(os.environ, {"XDG_CACHE_HOME": bloqueo}):
                P.registrar("no hay dónde escribir")  # no debe lanzar

    def test_aviso_de_privacidad_dice_lo_que_el_documento_manda(self):
        P = self.P
        todo = " ".join(P.AVISO_PRIVACIDAD)
        self.assertIn("Se envía a Google (Gemini)", todo)
        self.assertIn("~/.config/infinity/panki/clave", todo)
        self.assertIn("GEMINI_API_KEY", todo)
        self.assertIn("no se guarda", todo)  # la conversación
        self.assertIn("telemetría", todo)

    def test_aviso_de_privacidad_es_veraz_con_lo_que_se_envia_sin_preguntar(self):
        P = self.P
        todo = " ".join(P.AVISO_PRIVACIDAD)
        self.assertIn("pAnkI nunca te pedirá la clave por el chat", todo)
        self.assertIn("Sin pedirte permiso", todo)  # info_sistema, ver_ajustes y buscar_apps
        for dato in ("sistema operativo", "memoria", "disco", "batería", "tus ajustes", "las apps instaladas"):
            self.assertIn(dato, todo)
        for dato in ("tu nombre de usuario", "tu carpeta personal", "la fecha"):  # van en cada petición
            self.assertIn(dato, todo)
        self.assertIn("rutas de los archivos movidos", todo)  # el registro de deshacer
        self.assertIn("lanzar programas", todo)  # abrir una app también ejecuta cosas
        self.assertIn("«En esta conversación» amplía", todo)
        self.assertNotIn("Antes de tocar nada, te pregunto", P.BIENVENIDA)  # no es cierto con permisos concedidos
        self.assertIn("se envía a Google", P.BIENVENIDA)

    def test_estilo_minimalista(self):
        P = self.P
        tamanos = set(re.findall(r"font-size:\s*(\d+px)", P.ESTILO))
        self.assertEqual(tamanos, {"12px", "14px"}, "una tipografía con dos tamaños")
        self.assertEqual(re.findall(r"box-shadow:\s*([^;]+);", P.ESTILO), ["none"] * len(re.findall("box-shadow", P.ESTILO)))
        self.assertNotIn("gradient", P.ESTILO)
        self.assertNotIn("transition", P.ESTILO)
        self.assertNotIn("animation", P.ESTILO)
        self.assertEqual(P.ESTILO.count("#b9a8ff"), P.ESTILO.count("#b9a8ff"))  # el acento es ese violeta
        self.assertNotIn("font-family: \"Inter", P.ESTILO)

    def test_todo_el_css_propio_lleva_el_prefijo_panki(self):
        P = self.P
        clases = set(re.findall(r"\.([A-Za-z][\w-]*)", P.ESTILO.replace("0.", "0 ")))
        clases = {c for c in clases if not re.fullmatch(r"\d+", c)}
        self.assertTrue(clases)
        for c in clases:
            self.assertTrue(c.startswith("panki-"), c)


# ------------------------------------------------------------------------------------------ la ventana
@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class _BaseVentana(unittest.TestCase):
    """Lo común de las pruebas con ventana: entorno aislado, bucle de GTK y ayudas. No tiene pruebas propias."""

    @classmethod
    def setUpClass(cls):
        cls.P, cls.Gtk, cls.Gdk, cls.GLib = _gtk()

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        entorno = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": os.path.join(tmp.name, "config"),
                                               "XDG_CACHE_HOME": os.path.join(tmp.name, "cache")})
        entorno.start()
        self.addCleanup(entorno.stop)
        os.environ.pop("GEMINI_API_KEY", None)
        # Las tarjetas de permiso se arman a los 700 ms; aquí a los 30 (las pruebas del armado fijan el suyo).
        armado = mock.patch.object(self.P, "RETARDO_ARMADO_MS", 30)
        armado.start()
        self.addCleanup(armado.stop)
        self.registro = os.path.join(tmp.name, "cache", "infinity", "panki.log")
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.panki{time.time_ns()}")
        self.app.register(None)
        self.agentes = []
        self.hilos_ajenos = []

    # -- ayudas
    def pumpa(self, segundos=0.05):
        ctx = self.GLib.MainContext.default()
        fin = time.time() + segundos
        while time.time() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.002)

    def hasta(self, condicion, tiempo=5.0, mensaje="no ocurrió a tiempo"):
        fin = time.time() + tiempo
        while time.time() < fin:
            self.pumpa(0.02)
            if condicion():
                return
        self.fail(mensaje)

    def ventana(self, guion=None, clave=True, a_pango=None, nucleo=None):
        nucleo = nucleo or NucleoFalso(clave)
        self.nucleo = nucleo

        def fabrica(aprobar):
            agente = AgenteFalso(aprobar, guion)
            self.agentes.append(agente)
            return agente

        v = self.P.VentanaPanki(self.app, fabrica_agente=fabrica, nucleo=nucleo, a_pango=a_pango)
        v.present()
        self.addCleanup(self._limpiar, v)
        self.pumpa(0.05)
        return v

    def _limpiar(self, v):
        v.liberar()  # lo que hace la X o apagar la aplicación; así ningún hilo se queda esperando
        if not v._cerrada or v.get_visible():
            v.destroy()
        self.pumpa(0.05)
        for agente in self.agentes:
            for hilo in agente.hilos:
                hilo.join(5)
                self.assertFalse(hilo.is_alive(), "el hilo del agente quedó colgado")

    def widgets(self, raiz, tipo):
        salida, pendientes = [], [raiz]
        while pendientes:
            w = pendientes.pop(0)
            if isinstance(w, tipo):
                salida.append(w)
            h = w.get_first_child()
            while h is not None:
                pendientes.append(h)
                h = h.get_next_sibling()
        return salida

    def textos(self, raiz):
        return [e.get_text() for e in self.widgets(raiz, self.Gtk.Label)]

    def burbujas(self, v, tipo):
        return [b for b in self.widgets(v.conversacion, self.Gtk.Box) if b.has_css_class(f"panki-burbuja-{tipo}")]

    def enviar(self, v, texto):
        v.entrada.get_buffer().set_text(texto)
        v._enviar_desde_entrada()

    def espera_a_que_pida_permiso(self, v, armada=True):
        self.hasta(lambda: v.tarjeta_aprobacion is not None, mensaje="no apareció la tarjeta de aprobación")
        t = v.tarjeta_aprobacion
        if armada:
            self.hasta(lambda: t.espera.armada, mensaje="la tarjeta no llegó a armarse")
        return t

    @staticmethod
    def etiqueta_de(boton):
        """El texto de un botón, tenga etiqueta propia (label) o un hijo con el texto (el de la concesión)."""
        return boton.get_label() if boton.get_label() is not None else boton.etiqueta.get_text()

    @staticmethod
    def pide_permiso(solicitud=None):
        """Guion: el agente pide permiso y guarda lo que le respondieron."""
        def guion(agente, texto, alevento):
            agente.decisiones.append(agente.aprobar(solicitud or Solicitud()))
            return "terminado"
        return guion



@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestVentana(_BaseVentana):
    # -- arranque y conversación
    def test_arranca_con_la_bienvenida_y_la_entrada_lista(self):
        v = self.ventana()
        self.assertEqual(v.get_title(), "pAnkI")
        self.assertTrue(v.bienvenida.get_visible())
        self.assertIn(self.P.BIENVENIDA, self.textos(v.bienvenida))
        self.assertTrue(v.entrada.get_sensitive())
        self.assertFalse(v._trabajando)
        self.assertFalse(v.boton_parar.get_visible())
        self.assertIsNone(v.tarjeta_clave)

    def test_entrada_vacia_no_envia(self):
        v = self.ventana()
        self.enviar(v, "  \n ")
        self.pumpa(0.1)
        self.assertEqual(self.agentes, [])
        self.assertTrue(v.bienvenida.get_visible())

    def test_conversacion_basica_con_estado_y_herramientas(self):
        def guion(agente, texto, alevento):
            alevento(("estado", "Pensando…"))
            alevento(("herramienta", "buscar_apps", 'consulta="gimp"'))
            alevento(("resultado", "buscar_apps", True, "1 aplicación encontrada"))
            alevento(("herramienta", "listar_directorio", 'ruta="~/x"'))
            alevento(("resultado", "listar_directorio", False, "carpeta no permitida"))
            alevento(("texto", "Listo."))
            return "Listo."

        v = self.ventana(guion)
        self.enviar(v, "busca gimp")
        self.hasta(lambda: not v._trabajando and "Listo." in self.textos(v.conversacion))
        textos = self.textos(v.conversacion)
        self.assertIn("busca gimp", textos)
        self.assertIn("· Buscando apps gimp — 1 aplicación encontrada · se envía a Google", textos)
        self.assertIn("· Mirando ~/x — no se pudo: carpeta no permitida", textos)
        self.assertEqual(textos.count("Listo."), 1, "el texto final no debe salir dos veces")
        self.assertFalse(v.bienvenida.get_visible())
        self.assertTrue(v.entrada.get_sensitive())
        self.assertEqual(self.agentes[0].enviados, ["busca gimp"])
        self.assertEqual(v.estado.get_text(), "")

    def test_el_texto_del_usuario_es_siempre_texto_plano(self):
        v = self.ventana()
        peligroso = "<b>negrita</b> & <i>x</i> <span foreground='red'>rojo</span>"
        self.enviar(v, peligroso)
        self.hasta(lambda: not v._trabajando and self.burbujas(v, "usuario"))
        etiqueta = self.widgets(self.burbujas(v, "usuario")[0], self.Gtk.Label)[0]
        self.assertFalse(etiqueta.get_use_markup())
        self.assertEqual(etiqueta.get_text(), peligroso)

    def test_la_respuesta_de_panki_usa_el_marcado_validado(self):
        v = self.ventana(guion=lambda a, t, ev: ev(("texto", "**hola**")) or "**hola**",
                         a_pango=lambda t: "<b>hola</b>")
        self.enviar(v, "di hola")
        self.hasta(lambda: self.burbujas(v, "pa"))
        etiqueta = self.widgets(self.burbujas(v, "pa")[0], self.Gtk.Label)[0]
        self.assertTrue(etiqueta.get_use_markup())
        self.assertEqual(etiqueta.get_text(), "hola")

    def test_marcado_invalido_o_que_falla_cae_a_texto_plano(self):
        for nombre, a_pango in (("mal formado", lambda t: "<b>sin cerrar"),
                                ("lanza", lambda t: 1 / 0),
                                ("no es texto", lambda t: None)):
            with self.subTest(nombre):
                v = self.ventana(guion=lambda a, t, ev: ev(("texto", "a < b")) or "a < b", a_pango=a_pango)
                self.enviar(v, "hola")
                self.hasta(lambda: self.burbujas(v, "pa"))
                etiqueta = self.widgets(self.burbujas(v, "pa")[0], self.Gtk.Label)[0]
                self.assertFalse(etiqueta.get_use_markup())
                self.assertEqual(etiqueta.get_text(), "a < b")

    def test_intro_envia_y_mayus_intro_no(self):
        v = self.ventana()
        K = self.Gdk
        v.entrada.get_buffer().set_text("hola")
        self.assertFalse(v._tecla(None, K.KEY_Return, 0, K.ModifierType.SHIFT_MASK))
        self.pumpa(0.1)
        self.assertEqual(self.agentes, [], "Mayús + Intro no envía: es una nueva línea")
        self.assertTrue(v._tecla(None, K.KEY_Return, 0, K.ModifierType(0)))
        self.hasta(lambda: self.agentes and self.agentes[0].enviados == ["hola"])
        self.assertEqual(v.texto_de_entrada(), "")

    def test_un_error_de_la_api_sale_como_burbuja_de_error_y_el_texto_vuelve_a_la_entrada(self):
        def guion(agente, texto, alevento):
            raise ErrorPanki("Se superó la cuota de Gemini (error 429). Espera un momento.", 429)

        v = self.ventana(guion)
        self.enviar(v, "pregunta larga")
        self.hasta(lambda: self.burbujas(v, "error"))
        self.assertIn("Se superó la cuota de Gemini (error 429). Espera un momento.", self.textos(v.conversacion))
        self.assertIsNone(v.tarjeta_clave, "un error de cuota no es un problema de la clave")
        self.hasta(lambda: v.entrada.get_sensitive())
        self.assertEqual(v.texto_de_entrada(), "pregunta larga")

    def test_una_excepcion_inesperada_da_mensaje_generico_y_va_al_registro_sin_la_clave(self):
        def guion(agente, texto, alevento):
            raise RuntimeError(f"fallo raro con {CLAVE_FALSA}")

        v = self.ventana(guion)
        self.enviar(v, "hola")
        self.hasta(lambda: self.burbujas(v, "error"))
        visible = " ".join(self.textos(v.conversacion))
        self.assertIn("Algo ha fallado dentro de pAnkI", visible)
        self.assertIn("panki.log", visible)
        self.assertNotIn("fallo raro", visible)
        self.assertNotIn(CLAVE_FALSA, visible)
        registro = _leer(self.registro)
        self.assertIn("RuntimeError", registro)
        self.assertNotIn(CLAVE_FALSA, registro)

    def test_todo_lo_visual_ocurre_en_el_hilo_principal(self):
        P = self.P
        ajenos = []
        principal = threading.main_thread()

        def vigilar(nombre):
            original = getattr(P.VentanaPanki, nombre)

            def envoltorio(self_, *a, **k):
                if threading.current_thread() is not principal:
                    ajenos.append(nombre)
                return original(self_, *a, **k)
            return mock.patch.object(P.VentanaPanki, nombre, envoltorio)

        def guion(agente, texto, alevento):
            alevento(("estado", "Pensando…"))
            alevento(("herramienta", "x", ""))
            alevento(("resultado", "x", True, "ok"))
            agente.decisiones.append(agente.aprobar(Solicitud()))
            alevento(("texto", "fin"))
            return "fin"

        with vigilar("_anadir"), vigilar("_poner_estado"), vigilar("_actualizar_controles"), \
                vigilar("_mostrar_aprobacion"), vigilar("_anadir_burbuja"):
            v = self.ventana(guion)
            self.enviar(v, "hola")
            tarjeta = self.espera_a_que_pida_permiso(v)
            tarjeta.permitir.emit("clicked")
            self.hasta(lambda: not v._trabajando and "fin" in self.textos(v.conversacion))
        self.assertEqual(ajenos, [])
        self.assertIsNot(self.agentes[0].hilos[0], principal, "el agente debe correr en su propio hilo")
        self.assertTrue(self.agentes[0].hilos[0].daemon)

    def test_mientras_el_agente_espera_la_ventana_sigue_viva(self):
        v = self.ventana(self.pide_permiso())
        self.enviar(v, "mueve cosas")
        self.espera_a_que_pida_permiso(v)
        marca = []
        self.GLib.timeout_add(20, lambda: marca.append(1) or False)
        self.pumpa(0.2)
        self.assertEqual(marca, [1], "el bucle principal no puede quedarse bloqueado por la aprobación")
        self.assertEqual(self.agentes[0].decisiones, [], "el agente sigue esperando la respuesta")

    # -- aprobación
    def test_tarjeta_de_aprobacion(self):
        v = self.ventana(self.pide_permiso(Solicitud(riesgo="medio")))
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        self.assertTrue(t.has_css_class("panki-riesgo-medio"))
        textos = self.textos(t)
        self.assertIn("Mover 2 archivos en ~/Descargas", textos)
        self.assertIn("Riesgo medio", textos)
        self.assertIn("a.jpg -> Imágenes/a.jpg\nb.pdf -> Documentos/b.pdf", textos)
        self.assertEqual(t.permitir.get_label(), "Permitir")
        self.assertEqual(t.denegar.get_label(), "Denegar")
        self.assertEqual(self.etiqueta_de(t.conversacion),
                         "Permitir en esta conversación: mover archivos de ~/Descargas a sus subcarpetas")
        self.assertTrue(t.detalle.has_css_class("panki-detalle"))
        self.assertIsNotNone(t.detalle.get_ancestor(self.Gtk.ScrolledWindow), "el detalle es desplazable")
        # mientras hay una tarjeta pendiente, no se puede escribir ni enviar
        self.assertFalse(v.entrada.get_sensitive())
        self.assertFalse(v.entrada.get_sensitive())
        self.assertTrue(v.boton_parar.get_visible())

    def test_los_tres_botones_devuelven_su_respuesta(self):
        for boton, esperado, etiqueta in (("permitir", "una_vez", "Permitido"),
                                          ("conversacion", "conversacion", "Permitido en esta conversación"),
                                          ("denegar", "no", "Denegado")):
            with self.subTest(boton):
                v = self.ventana(self.pide_permiso())
                self.enviar(v, "ordena")
                t = self.espera_a_que_pida_permiso(v)
                getattr(t, boton).emit("clicked")
                self.hasta(lambda: not v._trabajando and v.entrada.get_sensitive())
                self.assertEqual(self.agentes[-1].decisiones, [esperado])
                self.assertIn(etiqueta, self.textos(t), "la tarjeta queda como registro de lo decidido")
                self.assertEqual(self.widgets(t, self.Gtk.Button), [], "los botones desaparecen al decidir")
                self.assertIsNone(v.tarjeta_aprobacion)

    def test_sin_recordable_no_hay_boton_de_esta_conversacion(self):
        v = self.ventana(self.pide_permiso(Solicitud(recordable=None, riesgo="alto")))
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        self.assertIsNone(t.conversacion)
        self.assertFalse([b for b in self.widgets(t, self.Gtk.Button) if "en esta conversación" in self.etiqueta_de(b)])
        self.assertTrue(t.has_css_class("panki-riesgo-alto"))

    def test_riesgo_desconocido_se_trata_como_alto(self):
        v = self.ventana(self.pide_permiso(Solicitud(riesgo="inventado")))
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        self.assertTrue(t.has_css_class("panki-riesgo-alto"))
        self.assertIn("Riesgo alto", self.textos(t))

    def test_la_tarjeta_destapa_caracteres_de_direccion_engañosos(self):
        engano = "~/Descargas/factura-‮fdp.exe"
        v = self.ventana(self.pide_permiso(Solicitud(titulo="Mover " + engano, detalle=[engano + " -> ~/x"])))
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        for texto in self.textos(t):
            self.assertNotIn("‮", texto)
        self.assertTrue(any("‹U+202E›" in texto for texto in self.textos(t)))

    def test_conversacion_solo_vale_si_la_solicitud_es_recordable(self):
        # Si llegara "conversacion" para una solicitud sin clave "recordable", el agente recibe "una_vez".
        v = self.ventana()
        resultado = []
        hilo = threading.Thread(target=lambda: resultado.append(v.aprobar(Solicitud(recordable=None))), daemon=True)
        hilo.start()
        self.hasta(lambda: v.tarjeta_aprobacion is not None)
        v._espera.responder("conversacion")
        hilo.join(5)
        self.assertEqual(resultado, ["una_vez"])

    def test_parar_con_una_aprobacion_pendiente_responde_no_y_cancela(self):
        v = self.ventana(self.pide_permiso())
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        v.boton_parar.emit("clicked")
        self.hasta(lambda: self.agentes[0].decisiones == ["no"], mensaje="el hilo no recibió 'no'")
        self.assertTrue(self.agentes[0].cancelado.is_set())
        self.assertIn("Cancelado", self.textos(t))
        self.hasta(lambda: not v._trabajando and v.entrada.get_sensitive())
        self.assertIn("Detenido.", self.textos(v.conversacion))
        self.assertIsNone(v.tarjeta_aprobacion)

    def test_cerrar_con_una_aprobacion_pendiente_no_deja_el_hilo_colgado(self):
        v = self.ventana(self.pide_permiso())
        self.enviar(v, "ordena")
        self.espera_a_que_pida_permiso(v)
        agente = self.agentes[0]
        hilo = agente.hilos[0]
        v.close()  # la X: con trabajo en curso cancela y cierra de verdad
        hilo.join(5)
        self.assertFalse(hilo.is_alive(), "el hilo debe terminar al cerrar la ventana")
        self.assertEqual(agente.decisiones, ["no"])
        self.assertTrue(agente.cancelado.is_set())
        self.assertTrue(v._cerrada)

    def test_apagar_la_aplicacion_con_una_aprobacion_pendiente_libera_el_hilo(self):
        P = self.P
        agentes = []

        def fabrica(aprobar):
            agentes.append(AgenteFalso(aprobar, self.pide_permiso()))
            return agentes[-1]

        app = P.Panki(fabrica_agente=fabrica, nucleo=NucleoFalso(True))
        self.GLib.timeout_add(300, lambda: app.ventana.preguntar("ordena") or False)
        self.GLib.timeout_add(1000, lambda: app.quit() or False)  # con la tarjeta de aprobación sin contestar
        app.run(["panki"])
        self.assertEqual(len(agentes), 1)
        agentes[0].hilos[0].join(5)
        self.assertFalse(agentes[0].hilos[0].is_alive())
        self.assertEqual(agentes[0].decisiones, ["no"])
        self.assertTrue(agentes[0].cancelado.is_set())

    def test_una_aprobacion_que_llega_tras_parar_se_responde_no_sin_mostrarse(self):
        v = self.ventana()
        v._cancelado = True
        self.assertEqual(v.aprobar(Solicitud()), "no")
        self.assertIsNone(v.tarjeta_aprobacion)

    def test_parar_sin_aprobacion_pendiente(self):
        gate = threading.Event()

        def guion(agente, texto, alevento):
            gate.wait(5)
            return "Cancelado." if agente.cancelado.is_set() else "fin"

        v = self.ventana(guion)
        self.enviar(v, "algo largo")
        self.hasta(lambda: v.boton_parar.get_visible())
        v.boton_parar.emit("clicked")
        self.assertIn("Parando", v.estado.get_text())
        self.assertFalse(v.entrada.get_sensitive())
        gate.set()
        self.hasta(lambda: not v._trabajando and v.entrada.get_sensitive())
        self.assertTrue(self.agentes[0].cancelado.is_set())
        textos = self.textos(v.conversacion)
        self.assertIn("Detenido.", textos)
        self.assertNotIn("Cancelado.", textos, "el 'Cancelado.' del agente no se repite como burbuja")

    def test_cancelar_no_se_llama_si_el_agente_ya_termino(self):
        # Con el agente parado, cancelar() dejaría la señal puesta y el siguiente mensaje saldría "Cancelado."
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: self.agentes and self.agentes[0].enviados)
        self.agentes[0].hilos[0].join(5)  # enviar() ya volvió, pero la ventana aún no ha procesado el final
        v._cancelar_agente(marcar_parada=True)
        self.assertFalse(self.agentes[0].cancelado.is_set())

    # -- cola
    def test_un_mensaje_mientras_trabaja_espera_su_turno(self):
        gate = threading.Event()

        def guion(agente, texto, alevento):
            if texto == "primero":
                gate.wait(5)
            return "R:" + texto

        v = self.ventana(guion)
        self.enviar(v, "primero")
        self.hasta(lambda: v._trabajando)
        v.preguntar("segundo")  # p. ej. un `infinity-panki --preguntar` con la ventana ocupada
        self.assertEqual(self.agentes[0].enviados, ["primero"])
        gate.set()
        self.hasta(lambda: "R:segundo" in self.textos(v.conversacion))
        self.assertEqual(self.agentes[0].enviados, ["primero", "segundo"])

    # -- nueva conversación, ocultar, cerrar
    def test_nueva_conversacion_reinicia_y_limpia_la_vista(self):
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: "Respuesta a: hola" in self.textos(v.conversacion) and not v._trabajando)
        v.boton_nueva.emit("clicked")
        self.assertEqual(self.agentes[0].reinicios, 1)
        self.assertTrue(v.bienvenida.get_visible())
        self.assertNotIn("hola", self.textos(v.conversacion))
        self.assertNotIn("Respuesta a: hola", self.textos(v.conversacion))
        # y la siguiente conversación crea un agente nuevo (lee otra vez los ajustes)
        self.enviar(v, "otra")
        self.hasta(lambda: len(self.agentes) == 2 and self.agentes[1].enviados == ["otra"])

    def test_nueva_conversacion_con_trabajo_en_curso_no_hace_nada(self):
        gate = threading.Event()
        v = self.ventana(lambda a, t, ev: gate.wait(5) and "fin")
        self.enviar(v, "hola")
        self.hasta(lambda: v._trabajando)
        self.assertFalse(v.boton_nueva.get_sensitive())
        v.nueva_conversacion()
        self.assertEqual(self.agentes[0].reinicios, 0)
        gate.set()
        self.hasta(lambda: not v._trabajando)

    def test_alternar_oculta_y_muestra_sin_tocar_la_conversacion(self):
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: "Respuesta a: hola" in self.textos(v.conversacion))
        v.alternar()
        self.assertFalse(v.get_visible())
        v.alternar()
        self.assertTrue(v.get_visible())
        self.assertIn("Respuesta a: hola", self.textos(v.conversacion))
        self.assertEqual(self.agentes[0].reinicios, 0)

    def test_alternar_con_una_aprobacion_pendiente_oculta_y_deniega(self):
        v = self.ventana(self.pide_permiso())
        self.enviar(v, "ordena")
        t = self.espera_a_que_pida_permiso(v)
        v.alternar()
        self.assertFalse(v.get_visible(), "alternar con la ventana visible la oculta")
        self.hasta(lambda: self.agentes[0].decisiones == ["no"], mensaje="nadie mira la tarjeta: se deniega")
        self.assertIn("Denegado al ocultar la ventana", self.textos(t))

    def test_una_aprobacion_con_la_ventana_oculta_la_muestra(self):
        gate = threading.Event()

        def guion(agente, texto, alevento):
            gate.wait(5)  # la ventana se oculta mientras el agente trabaja y solo entonces pide permiso
            agente.decisiones.append(agente.aprobar(Solicitud()))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "ordena")
        self.hasta(lambda: v._trabajando)
        v.set_visible(False)
        gate.set()
        self.espera_a_que_pida_permiso(v)
        self.assertTrue(v.get_visible())

    def test_cerrar_sin_trabajo_oculta_y_descarta_la_conversacion(self):
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: "Respuesta a: hola" in self.textos(v.conversacion) and not v._trabajando)
        v.close()
        self.assertFalse(v.get_visible())
        self.assertFalse(v._cerrada, "la ventana sigue viva para que la burbuja la abra al instante")
        self.assertEqual(self.agentes[0].reinicios, 1, "la conversación no se guarda al cerrar")
        self.assertTrue(v.bienvenida.get_visible())
        v.alternar()
        self.assertTrue(v.get_visible())

    # -- la clave
    def test_primera_vez_sin_clave_muestra_el_aviso_y_el_campo(self):
        v = self.ventana(clave=False)
        t = v.tarjeta_clave
        self.assertIsNotNone(t)
        texto = " ".join(self.textos(t))
        self.assertIn("Se envía a Google (Gemini)", texto)
        self.assertIn("~/.config/infinity/panki/clave", texto)
        campos = self.widgets(t, self.Gtk.PasswordEntry)
        self.assertEqual(len(campos), 1, "la clave se escribe en un campo oculto")
        self.assertFalse(campos[0].get_show_peek_icon(), "sin icono para mostrar la clave")
        self.assertEqual([b.get_label() for b in self.widgets(t, self.Gtk.Button)], ["Guardar", "Cancelar"])
        self.assertFalse(v.entrada.get_sensitive())

    def test_sin_clave_el_texto_se_guarda_y_se_envia_al_guardar_la_clave(self):
        v = self.ventana(clave=False)
        v.preguntar("qué hora es")  # p. ej. `--preguntar`
        self.pumpa(0.1)
        self.assertEqual(self.agentes, [], "sin clave no se crea ningún agente")
        self.assertNotIn("qué hora es", self.textos(v.conversacion))
        t = v.tarjeta_clave
        campo = self.widgets(t, self.Gtk.PasswordEntry)[0]
        campo.set_text(CLAVE_FALSA)
        t.guardar.emit("clicked")
        self.assertEqual(self.nucleo.guardadas, [CLAVE_FALSA])
        self.assertIsNone(v.tarjeta_clave)
        self.assertEqual(campo.get_text(), "", "la clave no se queda en el campo")
        self.hasta(lambda: self.agentes and self.agentes[0].enviados == ["qué hora es"])
        self.hasta(lambda: "Respuesta a: qué hora es" in self.textos(v.conversacion))
        for texto in self.textos(v.conversacion):
            self.assertNotIn(CLAVE_FALSA, texto, "la clave no se muestra jamás")

    def test_escribir_sin_clave_pide_la_clave_y_no_pierde_el_texto(self):
        v = self.ventana(clave=False)
        # Cancelar: el texto vuelve a la caja de entrada
        v.preguntar("hola")
        t = v.tarjeta_clave
        self.widgets(t, self.Gtk.Button)[-1].emit("clicked")  # Cancelar
        self.assertIsNone(v.tarjeta_clave)
        self.assertEqual(v.texto_de_entrada(), "hola")
        self.assertTrue(v.entrada.get_sensitive())
        # al enviarlo otra vez sin clave, vuelve a salir la tarjeta
        v._enviar_desde_entrada()
        self.assertIsNotNone(v.tarjeta_clave)

    def test_clave_rechazada_por_el_formato_se_dice_sin_mostrar_la_clave(self):
        nucleo = NucleoFalso(clave=False)
        nucleo.rechazar_con = ErrorPanki(f"La clave {CLAVE_FALSA} no es válida: demasiado corta.")
        v = self.ventana(nucleo=nucleo)
        t = v.tarjeta_clave
        self.widgets(t, self.Gtk.PasswordEntry)[0].set_text(CLAVE_FALSA)
        t.guardar.emit("clicked")
        self.assertIs(v.tarjeta_clave, t, "la tarjeta sigue abierta para corregirla")
        self.assertTrue(t.mensaje.get_visible())
        self.assertNotIn(CLAVE_FALSA, t.mensaje.get_text())
        self.assertIn("no es válida", t.mensaje.get_text())
        for texto in self.textos(v):
            self.assertNotIn(CLAVE_FALSA, texto)

    def test_guardar_sin_pegar_nada_avisa(self):
        v = self.ventana(clave=False)
        v.tarjeta_clave.guardar.emit("clicked")
        self.assertTrue(v.tarjeta_clave.mensaje.get_visible())
        self.assertEqual(self.nucleo.guardadas, [])

    def test_error_de_clave_de_la_api_muestra_el_error_y_vuelve_a_pedirla(self):
        def guion(agente, texto, alevento):
            raise ErrorPanki("Gemini no aceptó la clave (error 403): no es válida, ha caducado o no tiene permiso "
                             "para este modelo. Revísala con el botón «Clave…».", 403)

        v = self.ventana(guion)
        self.enviar(v, "hola")
        self.hasta(lambda: v.tarjeta_clave is not None)
        self.assertTrue(self.burbujas(v, "error"))
        self.assertIn("Gemini no ha aceptado la clave", self.textos(v.tarjeta_clave))
        self.hasta(lambda: not v._trabajando)
        self.assertEqual(v.texto_de_entrada(), "hola", "el texto vuelve para reintentarlo")
        # con la clave nueva, el agente nuevo atiende
        self.widgets(v.tarjeta_clave, self.Gtk.PasswordEntry)[0].set_text(CLAVE_FALSA)
        v.tarjeta_clave.guardar.emit("clicked")
        self.assertEqual(self.nucleo.guardadas, [CLAVE_FALSA])
        self.assertIsNone(v._agente, "el cliente de Gemini lleva la clave dentro: hace falta un agente nuevo")

    def test_boton_clave_cambia_o_borra(self):
        v = self.ventana(clave=True)
        self.assertIsNone(v.tarjeta_clave)
        v.boton_clave.emit("clicked")
        t = v.tarjeta_clave
        self.assertIn("Borrar clave", [b.get_label() for b in self.widgets(t, self.Gtk.Button)])
        self.assertFalse(v.boton_clave.get_sensitive())
        borrar = [b for b in self.widgets(t, self.Gtk.Button) if b.get_label() == "Borrar clave"][0]
        borrar.emit("clicked")
        self.assertEqual(self.nucleo.borrados, 1)
        self.assertIsNone(v.tarjeta_clave)
        self.assertIn("Clave borrada de este equipo.", self.textos(v.conversacion))

    def test_con_la_variable_de_entorno_la_tarjeta_lo_avisa(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "otra-clave-de-prueba-de-entorno"}):
            v = self.ventana(clave=True)
            v.boton_clave.emit("clicked")
            self.assertTrue(any("GEMINI_API_KEY" in t and "prioridad" in t for t in self.textos(v.tarjeta_clave)))
            for texto in self.textos(v):
                self.assertNotIn("otra-clave-de-prueba-de-entorno", texto)

    def test_si_el_nucleo_no_carga_se_dice_en_la_conversacion(self):
        with mock.patch.object(self.P, "_nucleo_real", side_effect=ImportError("no hay panki_nucleo")):
            v = self.P.VentanaPanki(self.app)
        self.addCleanup(v.destroy)
        self.assertTrue(self.burbujas(v, "error"))
        self.assertFalse(v.entrada.get_sensitive())

    # -- la aplicación
    def test_la_aplicacion_tiene_la_accion_alternar_y_el_id_correcto(self):
        P = self.P
        self.assertEqual(P.APP_ID, "org.infinity.Panki")
        app = P.Panki(fabrica_agente=lambda a: AgenteFalso(a), nucleo=NucleoFalso(True))
        self.assertIsNotNone(app.lookup_action("alternar"))
        app.register(None)
        app.activate()
        self.assertTrue(app.ventana.get_visible())
        app.activate_action("alternar", None)
        self.pumpa(0.05)
        self.assertFalse(app.ventana.get_visible())
        self.assertIsNotNone(app.ventana, "ocultar no cierra la aplicación")
        app.activate_action("alternar", None)
        self.pumpa(0.05)
        self.assertTrue(app.ventana.get_visible())
        app.ventana.destroy()

    def test_preguntar_desde_la_linea_de_ordenes_activa_la_ventana_y_envia_el_texto(self):
        P = self.P
        agentes = []

        def fabrica(aprobar):
            agentes.append(AgenteFalso(aprobar))
            return agentes[-1]

        app = P.Panki(fabrica_agente=fabrica, nucleo=NucleoFalso(True))
        self.GLib.timeout_add(700, lambda: app.quit() or False)
        codigo = app.run(P.juntar_pregunta(["panki", "--preguntar", "qué", "hora", "es", "--gapplication-service"]))
        self.assertEqual(codigo, 0)
        self.assertEqual(len(agentes), 1)
        self.assertEqual(agentes[0].enviados, ["qué hora es --gapplication-service"])
        for hilo in agentes[0].hilos:
            hilo.join(5)

    def test_ayuda_y_opciones_desconocidas_no_abren_la_ventana(self):
        P = self.P
        for args, codigo in ((["panki", "--help"], 0), (["panki", "--ayuda"], 0), (["panki", "--no-existe%s"], 2)):
            with self.subTest(args):
                app = P.Panki(fabrica_agente=lambda a: AgenteFalso(a), nucleo=NucleoFalso(True))
                self.assertEqual(app.run(args), codigo)
                self.assertIsNone(app.ventana)

    def test_con_los_modulos_reales_y_sin_clave_sale_la_tarjeta_de_la_clave(self):
        try:
            from infinity_shell import panki_nucleo  # noqa: F401
        except ImportError as e:
            self.skipTest(f"el núcleo no está: {e}")
        v = self.P.VentanaPanki(self.app)  # núcleo y conversor reales, sin agente falso
        v.present()
        self.addCleanup(self._limpiar, v)
        self.pumpa(0.05)
        self.assertIsNotNone(v.tarjeta_clave, "sin clave (ni GEMINI_API_KEY) tiene que pedirla")
        self.assertIn("Se envía a Google (Gemini)", " ".join(self.textos(v.tarjeta_clave)))
        self.assertFalse(v.entrada.get_sensitive())


# ------------------------------------------------------------------------------------------ Ajustes
class _RespuestaHttp:
    status = 200

    def __init__(self, cuerpo):
        self._cuerpo = cuerpo

    def read(self, n=-1):
        cuerpo, self._cuerpo = self._cuerpo, b""
        return cuerpo

    def close(self):
        pass


def _gemini(*partes):
    import json
    return json.dumps({"candidates": [{"content": {"role": "model", "parts": list(partes)},
                                       "finishReason": "STOP"}]}).encode()


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestConAgenteReal(_BaseVentana):
    """La ventana con el Agente y las herramientas REALES; solo la red es falsa (el `abrir` del cliente)."""

    def setUp(self):
        super().setUp()
        try:
            from infinity_shell import panki_herramientas as H
            from infinity_shell import panki_nucleo as N
        except ImportError as e:
            self.skipTest(f"faltan el núcleo o las herramientas: {e}")
        self.H, self.N = H, N
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.hogar = os.path.join(tmp.name, "hogar")
        os.makedirs(self.hogar)
        self.estado = os.path.join(tmp.name, "estado")
        N.guardar_clave(CLAVE_FALSA)  # en el XDG_CONFIG_HOME temporal de setUp
        self.respuestas = []  # lo que "contesta Gemini", en orden: bytes de una respuesta, o una excepción
        self.llamadas = []

    def abrir(self, solicitud, timeout=None):
        self.llamadas.append(solicitud)
        r = self.respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return _RespuestaHttp(r)

    def ventana_real(self):
        def fabrica(aprobar):
            cliente = self.N.ClienteGemini(CLAVE_FALSA, "gemini-flash-latest", abrir=self.abrir)
            ejecutor = self.H.Ejecutor(hogar=self.hogar, estado_dir=self.estado)
            agente = self.N.Agente(cliente, ejecutor, aprobar)
            self.agentes.append(agente)
            return agente

        v = self.P.VentanaPanki(self.app, fabrica_agente=fabrica, nucleo=self.N)
        v.present()
        self.addCleanup(self._limpiar_real, v)
        self.pumpa(0.05)
        return v

    def _limpiar_real(self, v):
        v.liberar()
        if not v._cerrada or v.get_visible():
            v.destroy()
        self.pumpa(0.05)

    def llamada_a_crear_carpeta(self):
        return _gemini({"functionCall": {"name": "crear_carpeta", "args": {"ruta": "~/Proyectos"}}})

    def test_permitir_crea_la_carpeta_y_panki_lo_cuenta(self):
        v = self.ventana_real()
        self.respuestas = [self.llamada_a_crear_carpeta(), _gemini({"text": "Carpeta creada."})]
        self.enviar(v, "crea la carpeta Proyectos")
        t = self.espera_a_que_pida_permiso(v)
        self.assertIn("Proyectos", " ".join(self.textos(t)), "la tarjeta enseña la acción exacta")
        self.assertFalse(os.path.exists(os.path.join(self.hogar, "Proyectos")), "nada se hace antes de aprobar")
        t.permitir.emit("clicked")
        self.hasta(lambda: "Carpeta creada." in self.textos(v.conversacion) and v.entrada.get_sensitive())
        self.assertTrue(os.path.isdir(os.path.join(self.hogar, "Proyectos")))
        self.assertEqual(self.textos(v.conversacion).count("Carpeta creada."), 1)
        self.assertTrue(any(t.startswith("· Creando") and "(permiso previo)" not in t
                            for t in self.textos(v.conversacion)))

    def test_denegar_no_hace_nada(self):
        v = self.ventana_real()
        self.respuestas = [self.llamada_a_crear_carpeta(), _gemini({"text": "Entendido, no la creo."})]
        self.enviar(v, "crea la carpeta Proyectos")
        self.espera_a_que_pida_permiso(v).denegar.emit("clicked")
        self.hasta(lambda: "Entendido, no la creo." in self.textos(v.conversacion))
        self.assertFalse(os.path.exists(os.path.join(self.hogar, "Proyectos")))
        # El texto de la denegación del núcleo real es el que la ventana trata como «Denegado», no como fallo.
        linea = next(e for e in self.widgets(v.conversacion, self.Gtk.Label) if e.get_text().startswith("· Creando"))
        self.assertTrue(linea.get_text().endswith("— Denegado"), linea.get_text())
        self.assertFalse(linea.has_css_class("panki-actividad-fallo"))

    def test_parar_en_la_aprobacion_y_despues_se_puede_seguir_hablando(self):
        v = self.ventana_real()
        self.respuestas = [self.llamada_a_crear_carpeta()]
        self.enviar(v, "crea la carpeta Proyectos")
        self.espera_a_que_pida_permiso(v)
        v.boton_parar.emit("clicked")
        self.hasta(lambda: v.entrada.get_sensitive() and not v._trabajando)
        self.assertIn("Detenido.", self.textos(v.conversacion))
        self.assertFalse(os.path.exists(os.path.join(self.hogar, "Proyectos")))
        # La señal de cancelar no se queda puesta: el siguiente mensaje se atiende con normalidad.
        self.respuestas = [_gemini({"text": "Aquí estoy."})]
        self.enviar(v, "hola otra vez")
        self.hasta(lambda: "Aquí estoy." in self.textos(v.conversacion))

    def test_cerrar_con_la_aprobacion_pendiente_termina_el_hilo_del_agente_real(self):
        v = self.ventana_real()
        self.respuestas = [self.llamada_a_crear_carpeta()]
        self.enviar(v, "crea la carpeta Proyectos")
        self.espera_a_que_pida_permiso(v)
        hilos = [h for h in threading.enumerate() if h.name == "panki-agente"]
        self.assertEqual(len(hilos), 1)
        v.close()
        hilos[0].join(5)
        self.assertFalse(hilos[0].is_alive())
        self.assertFalse(os.path.exists(os.path.join(self.hogar, "Proyectos")))

    def test_la_api_rechaza_la_clave_y_se_vuelve_a_pedir(self):
        import io
        import urllib.error
        cuerpo = io.BytesIO(b'{"error": {"code": 403, "message": "denegado", "status": "PERMISSION_DENIED"}}')
        v = self.ventana_real()
        self.respuestas = [urllib.error.HTTPError("https://x/y", 403, "Forbidden", {}, cuerpo)]
        self.enviar(v, "hola")
        self.hasta(lambda: v.tarjeta_clave is not None)
        errores = " ".join(self.textos(v.conversacion))
        self.assertIn("Gemini no aceptó la clave", errores)
        self.assertNotIn(CLAVE_FALSA, errores)
        for solicitud in self.llamadas:  # la clave viaja por la cabecera, nunca por la URL
            self.assertNotIn(CLAVE_FALSA, solicitud.full_url)

    def test_el_cliente_real_no_deja_la_clave_en_ninguna_etiqueta_ni_en_el_registro(self):
        v = self.ventana_real()
        self.respuestas = [OSError("fallo de red con " + CLAVE_FALSA)]
        self.enviar(v, "hola")
        self.hasta(lambda: self.burbujas(v, "error"))
        for texto in self.textos(v):
            self.assertNotIn(CLAVE_FALSA, texto)
        if os.path.exists(self.registro):
            self.assertNotIn(CLAVE_FALSA, _leer(self.registro))


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestEndurecimiento(_BaseVentana):
    """Lo que pidió la revisión de seguridad de la interfaz (F-01 a F-12) y el diseño minimalista."""

    def dos_permisos(self):
        def guion(agente, texto, alevento):
            for _ in range(2):
                agente.decisiones.append(agente.aprobar(Solicitud()))
            return "fin"
        return guion

    # -- A1 (F-02): armado
    def test_la_tarjeta_nace_desarmada_y_un_clic_antes_no_hace_nada(self):
        with mock.patch.object(self.P, "RETARDO_ARMADO_MS", 400):
            v = self.ventana(self.pide_permiso())
            self.enviar(v, "ordena")
            t = self.espera_a_que_pida_permiso(v, armada=False)
            for b in (t.permitir, t.denegar, t.conversacion):
                self.assertFalse(b.get_sensitive())
            self.assertEqual((t.permitir.get_label(), t.denegar.get_label()), ("…", "…"))
            t.permitir.emit("clicked")  # un clic (o una tecla) antes del armado no hace nada
            t.conversacion.emit("clicked")
            self.pumpa(0.1)
            self.assertEqual(self.agentes[0].decisiones, [])
            self.assertIs(v.tarjeta_aprobacion, t)
            self.hasta(lambda: t.permitir.get_sensitive())
            self.assertEqual((t.permitir.get_label(), t.denegar.get_label()), ("Permitir", "Denegar"))
            self.assertTrue(t.conversacion.get_sensitive())

    def test_doble_clic_no_aprueba_la_tarjeta_siguiente(self):
        with mock.patch.object(self.P, "RETARDO_ARMADO_MS", 300):
            v = self.ventana(self.dos_permisos())
            self.enviar(v, "ordena")
            t1 = self.espera_a_que_pida_permiso(v)
            t1.permitir.emit("clicked")  # primer clic
            self.hasta(lambda: v.tarjeta_aprobacion is not None and v.tarjeta_aprobacion is not t1)
            t2 = v.tarjeta_aprobacion
            t2.permitir.emit("clicked")  # el segundo clic del doble clic cae sobre la tarjeta nueva
            self.pumpa(0.1)
            self.assertEqual(self.agentes[0].decisiones, ["una_vez"], "la segunda tarjeta no puede aprobarse aún")
            self.assertIs(v.tarjeta_aprobacion, t2)
            self.hasta(lambda: t2.espera.armada)
            t2.denegar.emit("clicked")
            self.hasta(lambda: len(self.agentes[0].decisiones) == 2)
            self.assertEqual(self.agentes[0].decisiones, ["una_vez", "no"])

    def test_escape_deniega_incluso_antes_del_armado(self):
        with mock.patch.object(self.P, "RETARDO_ARMADO_MS", 5000):
            v = self.ventana(self.pide_permiso())
            self.enviar(v, "ordena")
            t = self.espera_a_que_pida_permiso(v, armada=False)
            self.assertTrue(v._tecla_ventana(None, self.Gdk.KEY_Escape, 0, self.Gdk.ModifierType(0)))
            self.hasta(lambda: self.agentes[0].decisiones == ["no"])
            self.assertIn("Denegado", self.textos(t))
            self.assertEqual(t.espera.id_armado, 0, "el temporizador de armado se cancela")
            self.assertFalse(v._tecla_ventana(None, self.Gdk.KEY_Escape, 0, self.Gdk.ModifierType(0)))

    # -- A2 (F-01): alcance literal, permiso previo, pie de permisos
    def test_el_boton_de_concesion_dice_el_alcance_literal_sin_recortar(self):
        largo = "leer CUALQUIER archivo de ~/Documentos, ocultos incluidos, hasta que lo revoques " * 4
        v = self.ventana(self.pide_permiso(Solicitud(alcance=largo)))
        self.enviar(v, "x")
        t = self.espera_a_que_pida_permiso(v)
        et = t.conversacion.etiqueta
        self.assertEqual(et.get_text(), "Permitir en esta conversación: " + largo)
        self.assertTrue(et.get_wrap())
        self.assertEqual(et.get_ellipsize(), self.P.Pango.EllipsizeMode.NONE)
        self.assertNotIn("panki-decision", t.conversacion.get_css_classes(), "no se parece a Permitir")

    def test_sin_alcance_el_boton_usa_el_titulo(self):
        s = Solicitud(titulo="Leer el archivo leeme.txt")
        s.alcance = None
        v = self.ventana(self.pide_permiso(s))
        self.enviar(v, "x")
        t = self.espera_a_que_pida_permiso(v)
        self.assertEqual(t.conversacion.etiqueta.get_text(), "Permitir en esta conversación: Leer el archivo leeme.txt")

    def lineas(self, v):
        return self.textos(v.conversacion)

    def test_lo_que_se_ejecuta_con_un_permiso_previo_se_marca(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "leer_archivo", 'ruta="~/a.txt"'))  # sin tarjeta: permiso anterior
            alevento(("resultado", "leer_archivo", True, "Leídos 20 bytes"))
            alevento(("herramienta", "info_sistema", ""))  # no pide permiso nunca
            alevento(("resultado", "info_sistema", True, "Sistema leído"))
            alevento(("herramienta", "mover_archivo", 'origen="a"'))
            agente.aprobar(Solicitud())  # aquí sí hay tarjeta
            alevento(("resultado", "mover_archivo", True, "Movido"))
            alevento(("herramienta", "leer_archivo", 'ruta="~/b.txt"'))
            alevento(("resultado", "leer_archivo", False, "No permitido"))  # un fallo no es "permiso previo"
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.espera_a_que_pida_permiso(v).permitir.emit("clicked")
        self.hasta(lambda: not v._trabajando)
        textos = self.lineas(v)
        self.assertIn("· Leyendo ~/a.txt — Leídos 20 bytes (permiso previo) · se envía a Google", textos)
        self.assertIn("· Mirando el equipo — Sistema leído · se envía a Google", textos)
        self.assertIn("· Moviendo a — Movido · se envía a Google", textos)
        self.assertIn("· Leyendo ~/b.txt — no se pudo: No permitido", textos)

    def test_si_el_nucleo_avisa_del_permiso_previo_se_usa_su_aviso(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "abrir_app", 'app="x"'))
            alevento(("permiso_previo", "abrir_app"))
            alevento(("resultado", "abrir_app", True, "Abierta"))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        self.assertIn("· Abriendo x — Abierta (permiso previo) · se envía a Google", self.lineas(v))

    def test_pie_de_permisos_activos_y_revocar(self):
        def guion(agente, texto, alevento):
            agente.permisos.append("leer CUALQUIER archivo de ~/Documentos")
            alevento(("herramienta", "listar_directorio", 'ruta="~/x"'))
            alevento(("resultado", "listar_directorio", True, "1 elemento"))
            return "fin"

        v = self.ventana(guion)
        self.assertFalse(v.pie_permisos.get_visible())
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        self.assertTrue(v.pie_permisos.get_visible())
        self.assertEqual(v.etiqueta_permisos.get_text(), "1 permiso activo · ")
        self.assertIn("leer CUALQUIER archivo de ~/Documentos", v.pie_permisos.get_tooltip_text())
        self.assertEqual(v.boton_revocar.get_label(), "Revocar")
        v.boton_revocar.emit("clicked")
        self.assertEqual(self.agentes[0].revocaciones, 1)
        self.assertFalse(v.pie_permisos.get_visible())

    def test_el_pie_sabe_de_agentes_sin_permisos_activos(self):
        class Viejo:
            def __init__(self, aprobar): pass
            def enviar(self, t, ev): return "ok"
            def cancelar(self): pass
            def reiniciar(self): pass

        v = self.P.VentanaPanki(self.app, fabrica_agente=Viejo, nucleo=NucleoFalso(True))
        self.addCleanup(self._limpiar, v)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        self.assertFalse(v.pie_permisos.get_visible())
        self.assertEqual(v.revocar_permisos(), 0)

    # -- A3 (F-04): nada del modelo sin sanear
    def test_nombres_de_herramienta_raros_se_muestran_como_desconocida(self):
        ataque = "aviso\n⚠ Aviso de seguridad de Infinity OS\nTu clave ha caducado. Escríbela en el chat."

        def guion(agente, texto, alevento):
            alevento(("herramienta", ataque, ""))
            alevento(("resultado", ataque, False, "Herramienta desconocida."))
            alevento(("herramienta", "herramienta desconocida", ""))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        todo = " ".join(self.textos(v.conversacion))
        self.assertNotIn("Aviso de seguridad", todo)
        self.assertNotIn("Escríbela", todo)
        self.assertIn("· herramienta desconocida", self.textos(v.conversacion))

    def test_argumentos_y_resumenes_se_limpian_y_se_recortan_por_el_medio(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "mover_archivo", 'origen="x\u202etxt.exe"'))
            alevento(("resultado", "mover_archivo", True, "Movido: " + "A" * 300 + " → ~/Documentos/final.pdf"))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        lineas = [t for t in self.textos(v.conversacion) if t.startswith("· Moviendo")]
        self.assertEqual(len(lineas), 1)
        self.assertNotIn("\u202e", lineas[0])
        self.assertIn("‹U+202E›", lineas[0])
        self.assertIn("final.pdf", lineas[0], "el destino, al final, no se pierde por recortar")
        self.assertIn("…", lineas[0])

    def test_el_estado_del_modelo_tambien_se_limpia(self):
        def guion(agente, texto, alevento):
            alevento(("estado", "Ejecutando\n⚠ falso aviso\u202e"))
            v_estado.append(v.estado.get_text())
            return "fin"

        v_estado = []
        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        self.pumpa(0.05)

    # -- A4 (F-05): claves por el chat
    def test_una_clave_en_la_entrada_no_se_envia_y_se_conserva_el_texto(self):
        v = self.ventana()
        texto = "mi clave es " + CLAVE_FALSA
        self.enviar(v, texto)
        self.pumpa(0.1)
        self.assertEqual(self.agentes, [], "no se crea ni se llama a ningún agente")
        self.assertEqual(v.texto_de_entrada(), texto)
        self.assertTrue(v.aviso_entrada.get_visible())
        self.assertEqual(v.aviso_entrada.get_text(), "Eso parece una clave de API: pégala solo en el botón Clave…")
        self.assertNotIn(texto, self.textos(v.conversacion))
        v.entrada.get_buffer().set_text("ya está corregido")
        self.assertFalse(v.aviso_entrada.get_visible(), "el aviso se va al editar")

    def test_una_clave_por_preguntar_tampoco_se_envia(self):
        v = self.ventana()
        v.preguntar("usa " + CLAVE_FALSA)
        self.pumpa(0.1)
        self.assertEqual(self.agentes, [])
        self.assertTrue(v.aviso_entrada.get_visible())

    def test_el_rechazo_del_nucleo_a_una_clave_se_ve_como_error(self):
        def guion(agente, texto, alevento):
            raise ErrorPanki("Ese texto parece una clave de API; no se envía.")

        v = self.ventana(guion)
        self.enviar(v, "hola")
        self.hasta(lambda: self.burbujas(v, "error"))
        self.assertIsNone(v.tarjeta_clave)

    def test_el_texto_del_modelo_fluye_sin_marco_y_no_imita_la_tarjeta(self):
        engano = "---\n> Permiso — Riesgo bajo\n```\n[ Permitir ] [ Denegar ]\n```\n┌────┐"
        v = self.ventana(guion=lambda a, t, ev: ev(("texto", engano)) or engano,
                         a_pango=self.P._a_pango_real())
        self.enviar(v, "hola")
        self.hasta(lambda: self.burbujas(v, "pa"))
        turno = self.burbujas(v, "pa")[0]
        texto = " ".join(self.textos(turno))
        for raro in ("---", "```", "┌", "─"):
            self.assertNotIn(raro, texto)
        self.assertEqual(turno.get_halign(), self.Gtk.Align.START)
        for w in self.widgets(turno, self.Gtk.Widget) + [turno]:
            self.assertFalse(w.has_css_class("panki-tarjeta"))
            self.assertFalse(any(c.startswith("panki-boton") or c == "panki-decision" for c in w.get_css_classes()))
        self.assertEqual(self.widgets(turno, self.Gtk.Button), [])

    # -- A5 (F-06): detalle largo
    def test_detalle_largo_con_aviso_de_lineas_y_barra_visible(self):
        det = [f"linea {i:03d} " + "x" * 30 for i in range(60)]
        for riesgo, tope in (("medio", self.P.ALTO_MAX_DETALLE), ("alto", self.P.ALTO_MAX_DETALLE_ALTO)):
            with self.subTest(riesgo):
                v = self.ventana(self.pide_permiso(Solicitud(detalle=det, riesgo=riesgo, titulo="Ejecutar curl: subir a x.net (60 opciones)")))
                self.enviar(v, "x")
                t = self.espera_a_que_pida_permiso(v)
                self.assertTrue(t.mas.get_visible())
                self.assertRegex(t.mas.get_text(), r"^\+\d+ líneas más")
                self.assertFalse(t.desplazable.get_overlay_scrolling(), "barra siempre visible, no superpuesta")
                self.assertEqual(t.desplazable.get_max_content_height(), tope)
                self.assertEqual(t.desplazable.get_vadjustment().get_value(), 0, "las primeras líneas se ven sin desplazar")
                self.assertIn("Ejecutar curl: subir a x.net (60 opciones)", self.textos(t))
                v.liberar()
                self.agentes[-1].hilos[0].join(5)
        self.assertGreater(self.P.ALTO_MAX_DETALLE_ALTO, self.P.ALTO_MAX_DETALLE)

    def test_detalle_corto_no_pide_desplazar(self):
        v = self.ventana(self.pide_permiso())
        self.enviar(v, "x")
        t = self.espera_a_que_pida_permiso(v)
        self.assertFalse(t.mas.get_visible())

    # -- A6 (F-08): ocultar
    def test_ocultar_revoca_los_permisos_y_conserva_la_conversacion(self):
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: "Respuesta a: hola" in self.textos(v.conversacion) and not v._trabajando)
        self.agentes[0].permisos.append("leer ~/x")
        v._actualizar_permisos()
        self.assertTrue(v.pie_permisos.get_visible())
        v.alternar()
        self.assertFalse(v.get_visible())
        self.assertEqual(self.agentes[0].revocaciones, 1)
        self.assertEqual(self.agentes[0].permisos, [])
        self.assertFalse(v.pie_permisos.get_visible())
        v.alternar()
        self.assertTrue(v.get_visible())
        self.assertIn("Respuesta a: hola", self.textos(v.conversacion))
        self.assertEqual(self.agentes[0].reinicios, 0)

    # -- A7 (F-11, F-12)
    def test_la_tarjeta_de_la_clave_solo_se_reabre_por_lo_que_marca_el_nucleo(self):
        casos = ((ErrorPanki("Gemini no aceptó la clave: no es válida «clave no válida»"), False),  # solo texto
                 (ErrorPanki("x", codigo=429), False),
                 (ErrorPanki("x", codigo=401), True),
                 (ErrorPanki("x", codigo=403), True),
                 (ErrorPanki("x", codigo=400, clave_invalida=True), True),
                 (ErrorPanki("x", codigo=400), False))
        for error, reabre in casos:
            with self.subTest(codigo=error.codigo, marca=error.clave_invalida, texto=str(error)[:20]):
                def guion(agente, texto, alevento, error=error):
                    raise error

                v = self.ventana(guion)
                self.enviar(v, "hola")
                self.hasta(lambda: self.burbujas(v, "error"))
                self.pumpa(0.1)
                self.assertEqual(v.tarjeta_clave is not None, reabre)

    def test_cada_tarjeta_que_llega_trae_la_ventana_a_la_vista(self):
        v = self.ventana(self.pide_permiso())
        self.assertTrue(v.get_visible())  # visible (p. ej. minimizada o tapada): igualmente se pide el foco
        with mock.patch.object(v, "present") as present:
            self.enviar(v, "ordena")
            self.espera_a_que_pida_permiso(v)
        self.assertGreaterEqual(present.call_count, 1)

    def test_una_tarjeta_sin_respuesta_muestra_su_antiguedad_y_caduca(self):
        with mock.patch.object(self.P, "ANTIGUEDAD_S", 1), mock.patch.object(self.P, "CADUCIDAD_S", 3):
            v = self.ventana(self.pide_permiso())
            self.enviar(v, "ordena")
            t = self.espera_a_que_pida_permiso(v)
            self.assertFalse(t.antiguedad.get_visible())
            self.hasta(lambda: t.antiguedad.get_visible(), 4)
            self.assertIn("Esperando desde hace", t.antiguedad.get_text())
            self.hasta(lambda: self.agentes[0].decisiones == ["no"], 6, "no caducó")
            self.assertIn("Caducada sin respuesta: denegado", self.textos(t))
            self.assertIn("Una solicitud de permiso caducó sin respuesta y se denegó.", self.textos(v.conversacion))
            self.assertEqual(t.espera.id_reloj, 0)

    # -- B: aspecto minimalista
    def test_cabecera_y_pie(self):
        v = self.ventana()
        cab = v.get_titlebar()
        marcas = [e for e in self.widgets(cab, self.Gtk.Label) if e.get_text() == "pAnkI"]
        self.assertEqual(len(marcas), 1, "la marca de texto, con esas mayúsculas")
        for b in (v.boton_nueva, v.boton_clave):
            self.assertIsNone(b.get_label())
            self.assertTrue(b.get_icon_name())
            self.assertFalse(b.get_has_frame())
            self.assertTrue(b.get_tooltip_text())
        self.assertEqual(self.etiqueta_de_pie(v), "Se envía a Google (Gemini)")
        self.assertTrue(v.pie_google.get_visible())
        self.assertFalse(self.widgets(v, self.Gtk.Spinner), "sin animaciones")
        self.assertEqual(v.entrada.get_buffer().get_char_count(), 0)

    @staticmethod
    def etiqueta_de_pie(v):
        return v.pie_google.get_text()

    def test_turnos_sin_burbujas_el_usuario_a_la_derecha(self):
        v = self.ventana()
        self.enviar(v, "hola")
        self.hasta(lambda: self.burbujas(v, "pa"))
        u, p = self.burbujas(v, "usuario")[0], self.burbujas(v, "pa")[0]
        self.assertEqual(u.get_halign(), self.Gtk.Align.END)
        self.assertEqual(p.get_halign(), self.Gtk.Align.START)
        self.assertTrue(u.has_css_class("panki-usuario"))
        self.assertNotIn("panki-tarjeta", p.get_css_classes())

    def test_invariantes_de_la_tarjeta(self):
        for riesgo in ("medio", "alto"):
            with self.subTest(riesgo):
                v = self.ventana(self.pide_permiso(Solicitud(riesgo=riesgo)))
                self.enviar(v, "x")
                t = self.espera_a_que_pida_permiso(v)
                self.pumpa(0.1)
                textos = self.textos(t)
                self.assertIn("Permiso", textos)  # etiqueta fija
                self.assertIn(f"Riesgo {riesgo}", textos)  # riesgo en palabra...
                self.assertIn("●", textos)  # ...y punto de color
                self.assertIn("Mover 2 archivos en ~/Descargas", textos)  # título
                self.assertIn("a.jpg -> Imágenes/a.jpg\nb.pdf -> Documentos/b.pdf", textos)  # detalle exacto
                self.assertEqual(t.permitir.get_css_classes()[-1:], t.denegar.get_css_classes()[-1:])
                self.assertEqual(sorted(t.permitir.get_css_classes()), sorted(t.denegar.get_css_classes()))
                self.assertEqual(t.permitir.get_width(), t.denegar.get_width(), "mismo tamaño")
                self.assertEqual(t.permitir.get_height(), t.denegar.get_height())
                self.assertLess(t.permitir.get_allocation().x, t.denegar.get_allocation().x)
                self.assertGreater(t.conversacion.get_allocation().y, t.permitir.get_allocation().y)
                self.assertTrue(t.has_css_class("panki-tarjeta"))
                v.liberar()
                self.agentes[-1].hilos[0].join(5)

    def test_la_tarjeta_de_la_clave_enseña_que_se_envia_a_google(self):
        v = self.ventana(clave=False)
        self.assertIn("Se envía a Google (Gemini): cada mensaje, lo que pAnkI ve con sus herramientas, "
                      "tu nombre de usuario, tu carpeta personal y la fecha.", self.textos(v.tarjeta_clave))
        self.assertTrue(v.pie_google.get_visible())


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestRevisionBanco(_BaseVentana):
    """Hallazgos de la revisión con banco de pruebas (agente real, modelo simulado, ventana real bajo Xvfb)."""

    def setUp(self):
        super().setUp()
        import gi
        gi.require_version("Pango", "1.0")
        from gi.repository import Pango
        self.Pango = Pango

    def lineas(self, v):
        return self.textos(v.conversacion)

    def sin_guiones(self, etiqueta):
        """¿Lleva el atributo de Pango que impide partir palabras con un guion?"""
        atributos = etiqueta.get_attributes()
        if atributos is None:
            return False
        return any(a.klass.type == self.Pango.AttrType.INSERT_HYPHENS and a.as_int().value == 0
                   for a in atributos.get_attributes())

    def etiqueta_con(self, v, trozo):
        return next(e for e in self.widgets(v.conversacion, self.Gtk.Label) if trozo in e.get_text())

    # -- 1: la línea de actividad
    def test_la_linea_de_actividad_se_ajusta_y_no_se_corta_por_el_medio(self):
        # Hallazgo 1: «· Mirando ~/Descargas — 21 entrad…en ~/Descargas. · se envía a Google».
        def guion(agente, texto, alevento):
            alevento(("herramienta", "listar_directorio", 'ruta="~/Descargas"'))
            alevento(("resultado", "listar_directorio", True, "21 entradas en ~/Descargas."))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        linea = next(t for t in self.lineas(v) if t.startswith("· Mirando"))
        self.assertEqual(linea, "· Mirando — 21 entradas en ~/Descargas. (permiso previo) · se envía a Google")
        self.assertEqual(linea.count("~/Descargas"), 1, "el destino no se repite si el resultado ya lo dice")
        etiqueta = self.etiqueta_con(v, "· Mirando")
        self.assertTrue(etiqueta.get_wrap())
        self.assertEqual(etiqueta.get_ellipsize(), self.Pango.EllipsizeMode.NONE)
        self.assertFalse(etiqueta.get_single_line_mode())

    def test_una_linea_de_actividad_larga_ocupa_varias_lineas_enteras(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "mover_archivo", 'origen="a"'))
            alevento(("resultado", "mover_archivo", True, "Movido: " + " ".join(f"palabra{i}" for i in range(14))))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        etiqueta = self.etiqueta_con(v, "· Moviendo")
        self.pumpa(0.1)
        self.assertGreater(etiqueta.get_layout().get_line_count(), 1)
        self.assertLessEqual(etiqueta.get_width(), v.conversacion.get_width())
        self.assertIn("palabra13", etiqueta.get_text())

    # -- 4: una denegación no es un fallo
    def test_una_denegacion_se_ve_como_denegado_y_no_en_rojo(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "abrir_url", 'url="https://x.net/a"'))
            alevento(("resultado", "abrir_url", False, self.P.MENSAJE_DENEGADO))
            alevento(("herramienta", "leer_archivo", 'ruta="~/b.txt"'))
            alevento(("resultado", "leer_archivo", False, "No existe."))
            return "fin"

        v = self.ventana(guion)
        self.enviar(v, "x")
        self.hasta(lambda: not v._trabajando)
        denegada = self.etiqueta_con(v, "· Abriendo")
        self.assertEqual(denegada.get_text(), '· Abriendo url="https://x.net/a" — Denegado'.replace('url="https://x.net/a"', "https://x.net/a"))
        self.assertFalse(denegada.has_css_class("panki-actividad-fallo"))
        self.assertNotIn("no se pudo", denegada.get_text())
        fallo = self.etiqueta_con(v, "· Leyendo")
        self.assertTrue(fallo.has_css_class("panki-actividad-fallo"), "un fallo de verdad sigue en rojo")
        self.assertIn("no se pudo: No existe.", fallo.get_text())

    # -- 2: el aviso «+N líneas más» cuenta lo que de verdad queda fuera
    def test_el_aviso_de_lineas_mas_cuenta_las_lineas_del_detalle_que_no_se_ven_enteras(self):
        # Como el banco: 21 movimientos (uno con un nombre larguísimo que ocupa varias filas) y dos de cabecera.
        # El aviso decía «+20» con unas 14 líneas ocultas: contaba filas tras partir y las medía con un layout propio.
        largo = "Informe trimestral de ventas de la región norte — versión definitiva (copia final) (2).pdf"
        nombres = [f"captura-{i}.png" for i in range(1, 20)] + [largo, "zeta.txt"]
        detalle = ["Total: 21 archivos a mover", "Carpetas de destino (9): Imágenes, Documentos, Comprimidos, "
                   "Hojas de cálculo, Música, Presentaciones, Código, Programas y 1 más"]
        detalle += [f"{n} → Documentos/{n}" for n in nombres]
        # Líneas justo en el borde del ancho (45 a 52 caracteres): si se mide con un ancho que no es el real, se parten
        # distinto y la cuenta cambia.
        detalle[2:2] = [f"{chr(97 + k)}" * (45 + k) for k in range(8)]
        for riesgo in ("medio", "alto"):
            with self.subTest(riesgo):
                v = self.ventana(self.pide_permiso(Solicitud(detalle=detalle, riesgo=riesgo, titulo="Mover 21 archivos")))
                self.enviar(v, "x")
                t = self.espera_a_que_pida_permiso(v)
                self.pumpa(0.2)
                capa = t.detalle.get_layout()  # el layout con el que GTK pinta el detalle
                ajuste = t.desplazable.get_vadjustment()
                visible = ajuste.get_page_size()
                # el texto empieza tras el relleno del label (mismo por arriba que por abajo): se mide, no se supone
                relleno = (ajuste.get_upper() - capa.get_extents()[1].height / self.Pango.SCALE) / 2
                octetos = t.detalle.get_text().encode("utf-8")
                it, fuera, filas_fuera = capa.get_iter(), set(), 0
                while True:
                    ext = it.get_line_extents()[1]
                    if relleno + (ext.y + ext.height) / self.Pango.SCALE > visible + 0.01:
                        filas_fuera += 1
                        fuera.add(octetos[:it.get_line_readonly().start_index].count(b"\n"))
                    if not it.next_line():
                        break
                self.assertGreater(len(fuera), 0)
                self.assertGreater(filas_fuera, len(fuera), "la línea larga ocupa varias filas: no es lo mismo contarlas")
                self.assertLess(len(fuera), len(detalle))
                self.assertTrue(t.mas.get_visible())
                self.assertEqual(t.mas.get_text(), f"+{len(fuera)} líneas más: desplaza el detalle para verlas")
                v.liberar()
                self.agentes[-1].hilos[0].join(5)

    # -- 3: sin guiones que Pango se inventa
    def test_pango_no_parte_las_palabras_con_guiones_en_burbujas_y_detalles(self):
        url = "https://www.tienda-ejemplo.com/buscar?q=zapatillas&categoria=deporte&ref=newsletter"
        v = self.ventana(self.pide_permiso(Solicitud(detalle=[url], riesgo="alto", titulo="Abrir " + url)),
                         a_pango=lambda t: t)
        self.enviar(v, "Abre " + url)
        t = self.espera_a_que_pida_permiso(v)
        self.assertTrue(self.sin_guiones(self.etiqueta_con(v, "Abre ")), "burbuja del usuario")
        self.assertTrue(self.sin_guiones(t.detalle), "detalle de la tarjeta")
        titulo = next(e for e in self.widgets(t, self.Gtk.Label) if e.get_text().startswith("Abrir "))
        self.assertTrue(self.sin_guiones(titulo), "título de la tarjeta")
        v._anadir_burbuja("Mira " + url, "pa")
        v._anadir_burbuja("Falló " + url, "error")
        for trozo in ("Mira ", "Falló "):
            self.assertTrue(self.sin_guiones(self.etiqueta_con(v, trozo)), trozo)
        v.liberar()
        self.agentes[-1].hilos[0].join(5)

    # -- 6: tras un error
    def test_tras_un_error_la_burbuja_fallida_se_quita_y_hay_pista_para_reintentar(self):
        intentos = []

        def guion(agente, texto, alevento):
            intentos.append(texto)
            if len(intentos) < 3:
                raise ErrorPanki("Se superó la cuota de Gemini (error 429).", 429)
            return "Por fin."

        v = self.ventana(guion)
        self.enviar(v, "mi pregunta")
        self.hasta(lambda: self.burbujas(v, "error") and not v._trabajando)
        self.assertEqual(self.burbujas(v, "usuario"), [], "la pregunta vuelve a la entrada: su burbuja sobra")
        self.assertEqual(v.texto_de_entrada(), "mi pregunta")
        self.assertTrue(v.aviso_entrada.get_visible())
        self.assertEqual(v.aviso_entrada.get_text(), self.P.AVISO_REINTENTAR)
        # Reintento con Intro (dos fallos seguidos): ni la pregunta ni el error se acumulan.
        v._enviar_desde_entrada()
        self.hasta(lambda: len(intentos) == 2 and not v._trabajando)
        self.assertEqual(self.burbujas(v, "usuario"), [])
        self.assertEqual(len(self.burbujas(v, "error")), 1)
        v._enviar_desde_entrada()
        self.hasta(lambda: "Por fin." in self.lineas(v))
        self.assertEqual(len(self.burbujas(v, "usuario")), 1)
        self.assertEqual(self.burbujas(v, "error"), [])
        self.assertFalse(v.aviso_entrada.get_visible())

    def test_si_hubo_actividad_antes_del_error_la_burbuja_del_usuario_se_queda(self):
        def guion(agente, texto, alevento):
            alevento(("herramienta", "info_sistema", ""))
            alevento(("resultado", "info_sistema", True, "Sistema leído"))
            raise ErrorPanki("Se superó la cuota de Gemini (error 429).", 429)

        v = self.ventana(guion)
        self.enviar(v, "pregunta")
        self.hasta(lambda: self.burbujas(v, "error"))
        self.assertEqual(len(self.burbujas(v, "usuario")), 1, "lo que ocurrió después de ella es real")

    # -- 7: la tarjeta de la clave de primera vez cabe en la ventana
    def test_en_la_primera_vez_guardar_y_cancelar_se_ven_dentro_de_la_ventana(self):
        v = self.ventana(clave=False)
        self.pumpa(0.4)
        t = v.tarjeta_clave
        alto = v.scroll.get_height()
        self.assertGreater(alto, 200)
        for boton in (t.guardar, t.campo):
            limites = boton.compute_bounds(v.scroll)
            self.assertIsNotNone(limites)
            self.assertGreaterEqual(limites[1].get_y(), 0, "no queda por encima del área visible")
            self.assertLessEqual(limites[1].get_y() + limites[1].get_height(), alto,
                                 "Guardar y el campo no se salen del área visible sin desplazar")
        # Y el aviso de privacidad sigue entero (se desplaza dentro de su zona, con la barra siempre visible).
        self.assertFalse(t.zona_aviso.get_overlay_scrolling())
        for parrafo in self.P.AVISO_PRIVACIDAD:
            self.assertIn(parrafo, self.textos(t.zona_aviso))

    # -- 8: la entrada desactivada se ve desactivada
    def test_la_entrada_desactivada_se_atenua(self):
        puerta = threading.Event()

        def guion(agente, texto, alevento):
            puerta.wait(5)
            return "listo"

        v = self.ventana(guion)
        self.assertFalse(v.caja_entrada.has_css_class("panki-entrada-inactiva"))
        self.enviar(v, "x")
        self.hasta(lambda: v._trabajando)
        self.assertFalse(v.entrada.get_sensitive())
        self.assertTrue(v.caja_entrada.has_css_class("panki-entrada-inactiva"))
        puerta.set()
        self.hasta(lambda: not v._trabajando)
        self.assertTrue(v.entrada.get_sensitive())
        self.assertFalse(v.caja_entrada.has_css_class("panki-entrada-inactiva"))
        self.assertIn("panki-entrada-inactiva", self.P.ESTILO)


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestPaginaPanki(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        sys.path.insert(0, SHELL)
        from infinity_shell import ajustes_app as X
        cls.X, cls.Gtk = X, Gtk

    def test_la_pagina_existe_con_sus_controles_y_el_aviso(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            app = self.Gtk.Application(application_id=f"org.infinity.test.ajustespanki{time.time_ns()}")
            app.register(None)
            with mock.patch.object(self.X.A, "leer_estado_contrasena", return_value="con"):
                v = self.X.VentanaAjustes(app)
            self.addCleanup(v.destroy)
            pagina = v.pila.get_child_by_name("panki")
            self.assertIsNotNone(pagina)
            self.assertEqual(v.pila.get_page(pagina).get_title(), "pAnkI (IA)")
            for i in ("panki.modelo", "panki.comandos", "panki.burbuja"):
                self.assertIn(i, v.controles)
            self.assertIsInstance(v.controles["panki.modelo"], self.Gtk.Entry)
            self.assertIsInstance(v.controles["panki.comandos"], self.Gtk.Switch)
            self.assertFalse(v.controles["panki.comandos"].get_active())
            self.assertTrue(v.controles["panki.burbuja"].get_active())
            self.assertEqual(v.controles["panki.modelo"].get_text(), "gemini-flash-latest")
            textos, pendientes = [], [pagina]
            while pendientes:
                w = pendientes.pop()
                if isinstance(w, self.Gtk.Label):
                    textos.append(w.get_text())
                h = w.get_first_child()
                while h is not None:
                    pendientes.append(h)
                    h = h.get_next_sibling()
            todo = " ".join(textos)
            self.assertIn("«Clave…»", todo)
            self.assertIn("se envían a Google (Gemini)", todo)
            self.assertIn("próxima conversación de pAnkI", todo)


# ------------------------------------------------------------------------------------------ integración
class TestIntegracion(unittest.TestCase):
    def test_lanzador(self):
        ruta = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-panki")
        self.assertTrue(os.access(ruta, os.X_OK), "infinity-panki tiene que ser ejecutable")
        texto = _leer(ruta)
        self.assertTrue(texto.startswith("#!/bin/sh\n"))
        self.assertIn("export PYTHONPATH=/usr/lib/infinity", texto)
        self.assertIn('export GSK_RENDERER="${GSK_RENDERER:-cairo}"', texto)
        self.assertIn('exec python3 -m infinity_shell.panki_app "$@"', texto)
        self.assertEqual(subprocess.run(["sh", "-n", ruta]).returncode, 0)

    def test_entrada_del_menu(self):
        datos = {}
        for linea in _leer(ROOT, "overlay", "usr", "share", "applications", "infinity-panki.desktop").splitlines():
            if "=" in linea:
                clave, _, valor = linea.partition("=")
                datos[clave] = valor
        self.assertEqual(datos["Name"], "pAnkI")
        self.assertEqual(datos["Exec"], "infinity-panki")
        self.assertEqual(datos["Icon"], "/usr/share/infinity/panki.png")
        self.assertEqual(datos["StartupWMClass"], "org.infinity.Panki")
        self.assertEqual(datos["Categories"], "Utility;")
        self.assertEqual(datos["Terminal"], "false")

    def test_build_instala_la_marca_de_panki(self):
        build = _leer(ROOT, "build", "build.sh")
        self.assertIn('"$ROOT/branding/panki.png"', build)
        self.assertIn('"$ROOT/branding/panki-white.svg"', build)
        self.assertIn("/usr/share/infinity/", build.split('"$ROOT/branding/panki-white.svg"', 1)[1].split("\n", 2)[1])

    def test_el_atajo_alterna_la_ventana_y_si_no_esta_la_abre(self):
        conf = _leer(HYPR_CONF)
        self.assertRegex(conf, r"(?m)^bind = \$mod, N,\s+exec, gapplication action org\.infinity\.Panki alternar "
                               r"\|\| infinity-panki\s+#")

    def test_regla_de_ventana_flota_abajo_a_la_derecha(self):
        reglas = {}
        for linea in _leer(HYPR_CONF).splitlines():
            if linea.startswith("windowrule") and "Panki" in linea:
                efecto, _, resto = linea.split("=", 1)[1].strip().partition(", match:class ")
                reglas[efecto.split()[0]] = (efecto, resto.strip())
        self.assertEqual(set(reglas), {"float", "size", "move"})
        patrones = {patron for _, patron in reglas.values()}
        self.assertEqual(len(patrones), 1, "las tres reglas casan con la misma clase")
        patron = patrones.pop()
        self.assertIsNotNone(re.fullmatch(patron, "org.infinity.Panki"))
        for otra in ("org.infinity.Ajustes", "org.infinity.Panki2", "xorg.infinity.Panki", "org.infinityXPanki"):
            self.assertIsNone(re.fullmatch(patron, otra), otra)
        self.assertEqual(reglas["float"][0], "float on")
        ancho, alto = (int(n) for n in reglas["size"][0].split()[1:])
        self.assertEqual(ancho, 460)
        self.assertTrue(600 <= alto <= 720)
        # anclada abajo a la derecha: 20 px del borde derecho y 90 px del inferior (dock y píldora de estado)
        self.assertEqual(reglas["move"][0], "move monitor_w-window_w-20 monitor_h-window_h-90")
        orden = [linea.split("=", 1)[1].split()[0] for linea in _leer(HYPR_CONF).splitlines()
                 if linea.startswith("windowrule") and "Panki" in linea]
        self.assertLess(orden.index("size"), orden.index("move"), "move usa el tamaño: va después de size")

    def test_no_hay_regla_de_centrar_para_panki(self):
        self.assertNotRegex(_leer(HYPR_CONF), r"center on, match:class \^org\\\.infinity\\\.Panki")

    @unittest.skipUnless(os.environ.get("HYPRLAND_VERIFICAR"), "sin HYPRLAND_VERIFICAR (ruta del verificador)")
    def test_hyprland_acepta_la_configuracion_y_rechaza_una_rota(self):
        verificar = os.environ["HYPRLAND_VERIFICAR"]
        with tempfile.TemporaryDirectory() as tmp:
            buena = os.path.join(tmp, "buena.conf")
            texto = _leer(HYPR_CONF).replace("@KB_LAYOUT@", "latam")
            with open(buena, "w", encoding="utf-8") as f:
                f.write(texto)
            r = subprocess.run([verificar, buena], capture_output=True, text=True, timeout=120)
            self.assertIn("config ok", r.stdout + r.stderr)
            mala = os.path.join(tmp, "mala.conf")
            with open(mala, "w", encoding="utf-8") as f:
                f.write(texto.replace("windowrule = size 460", "windowrule = tamano_inventado 460"))
            r = subprocess.run([verificar, mala], capture_output=True, text=True, timeout=120)
            self.assertNotIn("config ok", r.stdout + r.stderr, "el verificador debe detectar una línea inválida")
            self.assertIn("invalid field type", r.stdout + r.stderr)


if __name__ == "__main__":
    unittest.main()
