"""El motor WEB de pAnkI: la ventana que lo aloja (panki_app.py) y lo que lo rodea.

panki_app.py importa GTK al nivel de módulo (como el resto del shell) pero WebKitGTK solo de forma
perezosa, así que este módulo se puede importar y probar sin GTK ni WebKit instalados: la mayoría de
las clases de aquí no necesitan ninguno de los dos. Cubren, sin pantalla:
  * el análisis de --preguntar (interpretar_argumentos, juntar_pregunta: igual que antes del motor web);
  * la resolución de la ruta de panki_web/app.html (variable de entorno, instalada, o la del repo);
  * la validación del mensaje que llega desde la página por el puente de script de WebKit;
  * la construcción del JavaScript que se le inyecta (window.aplicarNombrePanki, window.pankiPreguntar),
    siempre con json.dumps, nunca con texto crudo concatenado;
  * cómo se persiste panki.nombre cuando la página avisa de que el usuario se renombró;
  * las pruebas de integración (lanzador, .desktop, build.sh, hyprland.conf) que no necesitan GTK.

Las que SÍ necesitan GTK real (construir una VentanaPanki de verdad) se saltan si no hay GTK 4, igual
que test_launcher.py y test_contrasena_ventana.py. Las que necesitarían además una WebKit.WebView real
se saltan aparte si no hay WebKitGTK 6.0 (gir1.2-webkit-6.0): en el entorno de estas pruebas casi
seguro que no está (ver el informe de quien hizo este cambio), así que esa pieza queda sin verificar
aquí con WebKit de verdad; lo que sí se comprueba con GTK real es que panki_app.py se degrada con
gracia (un aviso en vez de la página) cuando WebKitGTK falta, en vez de no abrir la ventana.
"""
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
PANTALLA = bool(os.environ.get("DISPLAY"))
HYPR_CONF = os.path.join(ROOT, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")

sys.path.insert(0, SHELL)
from infinity_shell import panki_app as P  # noqa: E402


def _leer(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


def _webkit_disponible():
    """¿Se puede importar gi.repository.WebKit 6.0 en este intérprete, tal cual? No mira GI_TYPELIB_PATH
    ni nada especial: es la misma comprobación perezosa que hace panki_app._importar_webkit()."""
    try:
        import gi
        gi.require_version("WebKit", "6.0")
        from gi.repository import WebKit  # noqa: F401
    except (ImportError, ValueError):
        return False
    return True


# ------------------------------------------------------------------------------------------ --preguntar
class TestFuncionesPuras(unittest.TestCase):
    """El análisis de --preguntar: el mismo contrato que tenía el motor anterior (no ha cambiado)."""

    def test_argumentos(self):
        self.assertEqual(P.interpretar_argumentos([]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "qué", "hora", "es"]), ("qué hora es", False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar=hola mundo"]), ("hola mundo", False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar"]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "  "]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["-h"]), (None, True, None))
        self.assertEqual(P.interpretar_argumentos(["--help"]), (None, True, None))
        self.assertIsNotNone(P.interpretar_argumentos(["--no-existe"])[2])

    def test_lo_que_va_tras_preguntar_es_texto_aunque_parezca_una_opcion(self):
        pregunta, ayuda, error = P.interpretar_argumentos(["--preguntar", "--help", "de", "verdad"])
        self.assertEqual(pregunta, "--help de verdad")
        self.assertFalse(ayuda)
        self.assertIsNone(error)

    def test_pregunta_muy_larga_se_recorta(self):
        texto = "a" * (P.MAX_PREGUNTA + 500)
        pregunta, _, _ = P.interpretar_argumentos(["--preguntar=" + texto])
        self.assertEqual(len(pregunta), P.MAX_PREGUNTA)

    def test_juntar_pregunta_evita_que_gapplication_lea_el_texto_como_opcion(self):
        self.assertEqual(P.juntar_pregunta(["prog", "--preguntar", "a", "b"]), ["prog", "--preguntar=a b"])
        self.assertEqual(P.juntar_pregunta(["prog"]), ["prog"])
        self.assertEqual(P.juntar_pregunta(["prog", "--preguntar"]), ["prog", "--preguntar="])


# ------------------------------------------------------------------------------------------ app.html
class TestRutaAppHtml(unittest.TestCase):
    """ruta_app_html(): variable de entorno de desarrollo, instalada, o la del repo (en ese orden)."""

    def test_sin_nada_especial_devuelve_la_del_repo(self):
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop(P.VAR_WEB_DEV, None)
            with mock.patch.object(P, "RUTA_WEB_INSTALADA", "/no/existe/app.html"):
                ruta = P.ruta_app_html()
        self.assertIsNotNone(ruta)
        self.assertTrue(ruta.endswith(os.path.join("panki_web", "app.html")))
        self.assertTrue(os.path.exists(ruta))

    def test_la_variable_de_entorno_gana_a_todo(self):
        with tempfile.TemporaryDirectory() as tmp:
            propia = os.path.join(tmp, "mi-app.html")
            with open(propia, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with mock.patch.dict(os.environ, {P.VAR_WEB_DEV: propia}):
                self.assertEqual(P.ruta_app_html(), os.path.abspath(propia))

    def test_la_instalada_gana_a_la_del_repo(self):
        with tempfile.TemporaryDirectory() as tmp:
            instalada = os.path.join(tmp, "app.html")
            with open(instalada, "w", encoding="utf-8") as f:
                f.write("<html></html>")
            with mock.patch.dict(os.environ, {}, clear=False):
                os.environ.pop(P.VAR_WEB_DEV, None)
                with mock.patch.object(P, "RUTA_WEB_INSTALADA", instalada):
                    self.assertEqual(P.ruta_app_html(), os.path.abspath(instalada))

    def test_variable_de_entorno_que_no_existe_se_ignora_y_sigue_buscando(self):
        with mock.patch.dict(os.environ, {P.VAR_WEB_DEV: "/no/existe/tampoco.html"}):
            with mock.patch.object(P, "RUTA_WEB_INSTALADA", "/no/existe/instalada.html"):
                ruta = P.ruta_app_html()
        self.assertIsNotNone(ruta)  # cae a la del repo, que sí existe

    def test_si_no_hay_ninguna_devuelve_none(self):
        with mock.patch.dict(os.environ, {P.VAR_WEB_DEV: "/no/existe/x.html"}):
            with mock.patch.object(P, "RUTA_WEB_INSTALADA", "/no/existe/y.html"):
                with mock.patch("os.path.exists", return_value=False):
                    self.assertIsNone(P.ruta_app_html())


# ------------------------------------------------------------------------------------------ puente JS
class TestMensajeDesdeLaPagina(unittest.TestCase):
    """_interpretar_mensaje_js: lo único que llega del lado JS, validado sin fiarse de la página."""

    def test_un_nombre_valido_se_acepta(self):
        self.assertEqual(P._interpretar_mensaje_js('{"accion":"nombre","valor":"Nova"}'), ("nombre", "Nova"))

    def test_los_espacios_alrededor_se_recortan(self):
        self.assertEqual(P._interpretar_mensaje_js('{"accion":"nombre","valor":"  Nova  "}'), ("nombre", "Nova"))

    def test_json_roto_se_ignora(self):
        self.assertIsNone(P._interpretar_mensaje_js("{no es json"))
        self.assertIsNone(P._interpretar_mensaje_js(""))

    def test_lo_que_no_es_un_objeto_se_ignora(self):
        for texto in ("42", "null", "[1,2,3]", '"nombre"', "true"):
            self.assertIsNone(P._interpretar_mensaje_js(texto), texto)

    def test_accion_desconocida_se_ignora(self):
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"ejecutar_comando","valor":"rm -rf /"}'))
        self.assertIsNone(P._interpretar_mensaje_js('{"valor":"Nova"}'))  # sin "accion"

    def test_valor_vacio_o_solo_espacios_se_rechaza(self):
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"nombre","valor":""}'))
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"nombre","valor":"   "}'))

    def test_valor_demasiado_largo_se_rechaza(self):
        largo = "a" * (P.MAX_VALOR_JS + 1)
        self.assertIsNone(P._interpretar_mensaje_js(json.dumps({"accion": "nombre", "valor": largo})))
        justo = "a" * P.MAX_VALOR_JS
        self.assertEqual(P._interpretar_mensaje_js(json.dumps({"accion": "nombre", "valor": justo})),
                         ("nombre", justo))

    def test_tipos_raros_de_valor_se_rechazan_sin_fallar(self):
        for valor in (None, 42, 3.5, True, ["Nova"], {"n": "Nova"}):
            texto = json.dumps({"accion": "nombre", "valor": valor})
            self.assertIsNone(P._interpretar_mensaje_js(texto), texto)

    def test_falta_valor_se_rechaza(self):
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"nombre"}'))


class TestConstruccionDeJavaScript(unittest.TestCase):
    """Lo que se le pasa a evaluate_javascript: siempre con json.dumps, nunca texto crudo concatenado."""

    def test_aplicar_nombre_usa_json_dumps(self):
        script = P._js_aplicar_nombre("Nova")
        self.assertEqual(script, 'window.aplicarNombrePanki("Nova")')

    def test_preguntar_usa_json_dumps(self):
        script = P._js_preguntar("qué hora es")
        self.assertEqual(script, 'window.pankiPreguntar("qu\\u00e9 hora es")')

    def test_comillas_y_backslashes_no_rompen_el_script(self):
        texto = 'dile "hola" a C:\\Users\\<script>alert(1)</script>'
        for constructor, nombre_js in ((P._js_aplicar_nombre, "window.aplicarNombrePanki"),
                                       (P._js_preguntar, "window.pankiPreguntar")):
            script = constructor(texto)
            self.assertTrue(script.startswith(nombre_js + "("))
            self.assertTrue(script.endswith(")"))
            # lo que hay entre los paréntesis es exactamente json.dumps(texto); si se recupera igual,
            # no hay inyección posible (ni una comilla ni un backslash escapan del literal JS)
            interior = script[len(nombre_js) + 1:-1]
            self.assertEqual(interior, json.dumps(texto))
            self.assertEqual(json.loads(interior), texto)

    def test_texto_vacio_tambien_pasa_por_json_dumps(self):
        self.assertEqual(P._js_preguntar(""), 'window.pankiPreguntar("")')


# ------------------------------------------------------------------------------------------ guardar el nombre
class _AjustesFalso:
    """Sustituye al módulo ajustes en las pruebas (como el parámetro `valores` de panki_nucleo.py)."""

    def __init__(self, valores=None, rechazar=False, fallar_io=False):
        self.valores = dict(valores or {"panki.nombre": "pAnkI", "panki.modelo": "x"})
        self.rechazar = rechazar
        self.fallar_io = fallar_io
        self.escritos = []

    def cargar(self):
        if self.fallar_io:
            raise OSError("disco de pega roto")
        return dict(self.valores), []

    def validar(self, valores):
        if self.rechazar:
            return valores, [("panki.nombre", "no vale")]
        return dict(valores), []

    def escribir(self, valores):
        if self.fallar_io:
            raise OSError("no se pudo escribir")
        self.escritos.append(dict(valores))
        self.valores = dict(valores)


class TestGuardarNombre(unittest.TestCase):
    def test_guarda_el_nombre_fusionado_con_lo_demas(self):
        falso = _AjustesFalso({"panki.nombre": "pAnkI", "panki.modelo": "gemini-flash-latest"})
        self.assertTrue(P._guardar_nombre("Nova", ajustes_mod=falso))
        self.assertEqual(falso.escritos, [{"panki.nombre": "Nova", "panki.modelo": "gemini-flash-latest"}])

    def test_si_la_validacion_rechaza_no_escribe_nada(self):
        falso = _AjustesFalso(rechazar=True)
        self.assertFalse(P._guardar_nombre("Nova", ajustes_mod=falso))
        self.assertEqual(falso.escritos, [])

    def test_un_fallo_de_disco_no_lanza(self):
        falso = _AjustesFalso(fallar_io=True)
        self.assertFalse(P._guardar_nombre("Nova", ajustes_mod=falso))

    def test_con_el_modulo_ajustes_real_y_una_carpeta_de_pruebas(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            self.assertTrue(P._guardar_nombre("Nova"))
            from infinity_shell import ajustes as A
            valores, _ = A.cargar()
            self.assertEqual(valores["panki.nombre"], "Nova")


# ------------------------------------------------------------------------------------------ integración
class TestIntegracion(unittest.TestCase):
    """No necesitan GTK: leen archivos de texto y los comprueban."""

    def test_lanzador(self):
        ruta = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-panki")
        self.assertTrue(os.access(ruta, os.X_OK), "infinity-panki tiene que ser ejecutable")
        texto = _leer(ruta)
        self.assertTrue(texto.startswith("#!/bin/sh\n"))
        self.assertNotIn("Gemini", texto)
        self.assertIn("WebKitGTK", texto)
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
        self.assertNotIn("gemini", datos["Comment"].lower())
        self.assertNotIn("gemini", datos["Keywords"].lower())

    def test_build_instala_la_marca_de_panki(self):
        build = _leer(ROOT, "build", "build.sh")
        self.assertIn('"$ROOT/branding/panki.png"', build)
        self.assertIn('"$ROOT/branding/panki-white.svg"', build)
        self.assertIn("/usr/share/infinity/", build.split('"$ROOT/branding/panki-white.svg"', 1)[1].split("\n", 2)[1])

    def test_build_instala_la_pagina_web_de_panki(self):
        build = _leer(ROOT, "build", "build.sh")
        self.assertIn("shell/infinity_shell/panki_web/app.html", build)
        self.assertIn("/usr/share/infinity/panki/app.html", build)
        # la instalación va DESPUÉS de copiar infinity_shell y ANTES de machacar nada de marca
        i_shell = build.index("usr/lib/infinity/infinity_shell")
        i_web = build.index("panki_web/app.html")
        self.assertLess(i_shell, i_web)

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
        self.assertEqual(reglas["float"][0], "float on")
        ancho, alto = (int(n) for n in reglas["size"][0].split()[1:])
        self.assertEqual(ancho, 460)
        self.assertTrue(600 <= alto <= 720)
        self.assertEqual(reglas["move"][0], "move monitor_w-window_w-20 monitor_h-window_h-90")

    def test_no_hay_regla_de_centrar_para_panki(self):
        self.assertNotRegex(_leer(HYPR_CONF), r"center on, match:class \^org\\\.infinity\\\.Panki")

    @unittest.skipUnless(os.environ.get("HYPRLAND_VERIFICAR"), "sin HYPRLAND_VERIFICAR (ruta del verificador)")
    def test_hyprland_acepta_la_configuracion(self):
        verificar = os.environ["HYPRLAND_VERIFICAR"]
        with tempfile.TemporaryDirectory() as tmp:
            buena = os.path.join(tmp, "buena.conf")
            with open(buena, "w", encoding="utf-8") as f:
                f.write(_leer(HYPR_CONF).replace("@KB_LAYOUT@", "latam"))
            r = subprocess.run([verificar, buena], capture_output=True, text=True, timeout=120)
            self.assertIn("config ok", r.stdout + r.stderr)


# ======================================================================= con GTK (sin WebKit)
def _cargar_gtk():
    try:
        import gi
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gio, Gtk
    except (ImportError, ValueError) as e:
        raise unittest.SkipTest(f"sin GTK 4: {e}")
    return Gio, Gtk


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestVentana(unittest.TestCase):
    """Construye una VentanaPanki real. Con GTK pero, en este entorno, sin WebKitGTK: ejercita el aviso
    de "falta WebKit" en vez de la página de verdad (ver la clase de más abajo para lo que falta)."""

    @classmethod
    def setUpClass(cls):
        cls.Gio, cls.Gtk = _cargar_gtk()
        cls.app = cls.Gtk.Application(application_id=f"org.infinity.test.pankiweb{id(cls)}")
        cls.app.register(None)

    def _ventana(self):
        v = P.VentanaPanki(self.app)
        self.addCleanup(v.destroy)
        return v

    def test_geometria_460x640(self):
        v = self._ventana()
        self.assertEqual(v.get_default_size(), (460, 640))

    def test_arranca_oculta_y_alternar_la_muestra_y_la_oculta(self):
        v = self._ventana()
        self.assertFalse(v.get_visible())
        v.alternar()
        self.assertTrue(v.get_visible())
        v.alternar()
        self.assertFalse(v.get_visible())

    def test_cerrar_oculta_y_deja_la_aplicacion_viva(self):
        v = self._ventana()
        v.alternar()
        self.assertTrue(v._al_cerrar(v))  # True: cierra la ventana (se oculta), no la aplicación
        self.assertFalse(v.get_visible())

    def test_preguntar_sin_webkit_no_falla(self):
        v = self._ventana()
        v.preguntar("hola")  # no hay motor web en este entorno: no debe lanzar

    def test_sin_webkitgtk_se_degrada_con_un_aviso_en_vez_de_no_abrir(self):
        if _webkit_disponible():
            self.skipTest("WebKitGTK 6.0 SÍ está disponible aquí; esta prueba es para cuando falta")
        v = self._ventana()
        self.assertFalse(v._webkit_disponible)
        self.assertIsInstance(v.get_child(), self.Gtk.Label)


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestPaginaAjustes(unittest.TestCase):
    """La página «pAnkI (IA)» de Ajustes: ahora con panki.nombre, y sin mencionar Gemini/Google."""

    @classmethod
    def setUpClass(cls):
        cls.Gio, cls.Gtk = _cargar_gtk()
        sys.path.insert(0, SHELL)
        from infinity_shell import ajustes_app as X
        cls.X = X

    def test_la_pagina_tiene_el_campo_del_nombre_y_no_menciona_gemini(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp}):
            app = self.Gtk.Application(application_id=f"org.infinity.test.ajustespankiweb{id(self)}")
            app.register(None)
            with mock.patch.object(self.X.A, "leer_estado_contrasena", return_value="con"):
                v = self.X.VentanaAjustes(app)
            self.addCleanup(v.destroy)
            pagina = v.pila.get_child_by_name("panki")
            self.assertIsNotNone(pagina)
            self.assertIn("panki.nombre", v.controles)
            self.assertIsInstance(v.controles["panki.nombre"], self.Gtk.Entry)
            self.assertEqual(v.controles["panki.nombre"].get_text(), "pAnkI")
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
            self.assertIn("próxima conversación de pAnkI", todo)
            # La nota de cabecera de la página (antes decía "se envían a Google (Gemini)") ya no debe
            # mencionarlos; panki.modelo/panki.comandos NO se tocan (lo pide el encargo) y siguen
            # hablando de Gemini, así que no se comprueba toda la página, solo la nota nueva.
            caja = self.Gtk.Box()
            self.X.VentanaAjustes._nota_panki(caja)
            texto_nota = caja.get_first_child().get_text()
            self.assertNotIn("Gemini", texto_nota)
            self.assertNotIn("Google", texto_nota)


# ======================================================================= con WebKit real
@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
@unittest.skipUnless(_webkit_disponible(), "sin WebKitGTK 6.0 (gir1.2-webkit-6.0) en este entorno de pruebas")
class TestVentanaConWebKitReal(unittest.TestCase):
    """La pieza que de verdad importa y que este entorno no tiene cómo comprobar (ver el informe): una
    WebKit.WebView real cargando panki_web/app.html, con su NetworkSession persistente y su puente de
    mensajes. Si algún día corre (con gir1.2-webkit-6.0 instalado), esto ejercita ese camino de verdad."""

    @classmethod
    def setUpClass(cls):
        cls.Gio, cls.Gtk = _cargar_gtk()
        cls.app = cls.Gtk.Application(application_id=f"org.infinity.test.pankiwebkit{id(cls)}")
        cls.app.register(None)

    def test_la_ventana_usa_webkit_de_verdad(self):
        v = P.VentanaPanki(self.app)
        self.addCleanup(v.destroy)
        self.assertTrue(v._webkit_disponible)
        self.assertEqual(type(v.webview).__name__, "WebView")


if __name__ == "__main__":
    unittest.main()
