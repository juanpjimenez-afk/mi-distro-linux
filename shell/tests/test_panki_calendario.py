"""La app «Calendario» (una ventana APARTE de la de pAnkI, mismo proceso y mismos datos) y todo lo que la rodea, SIN navegador.

Cubre lo que no necesita ni GTK ni Playwright:
  * los archivos del overlay (infinity-calendario, su .desktop con StartupWMClass=org.infinity.Calendario y los iconos
    del día) y que build.sh los instala (el overlay entero se copia con rsync: no hay líneas nuevas que añadir en build.sh);
  * la línea de órdenes de panki_app.py: --calendario[=AAAA-MM-DD], junto a --preguntar, y el JavaScript que
    se le inyecta a la página (siempre por json.dumps);
  * el mensaje «calendario» que la página de pAnkI envía por el puente (validación estricta), el app_id propio de la
    ventana (con dobles de la superficie Wayland) y la lógica de abrir/esperar a la página de CalendarioVentana;
  * el contrato con la página (window.pankiAbrirCalendario, ?vista=calendario) y una auditoría ESTÁTICA de app.html contra los
    errores clásicos de fechas: new Date('AAAA-MM-DD') (se lee como UTC y en Chile/Argentina da el día
    anterior), toISOString() para sacar «hoy», sumar 24 h para «mañana», Intl/toLocaleDateString para los
    nombres de los días;
  * los días de la semana de referencia (19/10/2026 = lunes…), comprobados contra el calendario de Python.

Las pruebas que ejecutan la página de verdad (zonas horarias, agendar por chat, la vista del calendario,
persistencia, el modo calendario y la sincronización entre dos páginas) están en test_panki_calendario_web.py y se saltan
si no hay Playwright/Chromium. Las que necesitan GTK 4 (la acción `calendario` de la aplicación, las dos ventanas con una
WebKit de pega, el buscador de apps) se saltan si no hay GTK.
"""
import datetime
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.normpath(os.path.join(HERE, ".."))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
APP_HTML = os.path.join(SHELL, "infinity_shell", "panki_web", "app.html")
PANTALLA = bool(os.environ.get("DISPLAY"))

sys.path.insert(0, SHELL)
from infinity_shell import panki_app as P  # noqa: E402

DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]


def _leer(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


def _desktop(ruta):
    datos = {}
    for linea in _leer(ruta).splitlines():
        if "=" in linea and not linea.startswith("#"):
            clave, _, valor = linea.partition("=")
            datos[clave] = valor
    return datos


# ------------------------------------------------------------------------------------------ archivos nuevos
class TestLanzador(unittest.TestCase):
    RUTA = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-calendario")

    def test_existe_y_es_ejecutable_como_infinity_panki(self):
        self.assertTrue(os.access(self.RUTA, os.X_OK), "infinity-calendario tiene que ser ejecutable")
        modo = os.stat(self.RUTA).st_mode
        modo_panki = os.stat(os.path.join(ROOT, "overlay", "usr", "bin", "infinity-panki")).st_mode
        self.assertEqual(stat.S_IMODE(modo), stat.S_IMODE(modo_panki))

    def test_es_un_script_sh_valido_que_abre_la_app_calendario(self):
        texto = _leer(self.RUTA)
        self.assertTrue(texto.startswith("#!/bin/sh\n"))
        self.assertEqual(subprocess.run(["sh", "-n", self.RUTA]).returncode, 0)
        self.assertIn("export PYTHONPATH=/usr/lib/infinity", texto)
        self.assertIn('export GSK_RENDERER="${GSK_RENDERER:-cairo}"', texto)
        self.assertIn("infinity_shell.panki_app", texto)
        self.assertIn("--calendario", texto)
        self.assertNotIn("Gemini", texto)
        # el comentario dice lo que es ahora: una app aparte, con ventana propia y los mismos datos que pAnkI
        comentario = "\n".join(l for l in texto.splitlines() if l.startswith("#"))
        for frase in ("APP APARTE", "ventana propia", "MISMOS datos que pAnkI", "org.infinity.Calendario"):
            self.assertIn(frase, comentario)
        self.assertNotIn("MISMA ventana", comentario)

    def _ejecutar(self, *args):
        """Corre el lanzador con un python3 de pega que solo imprime sus argumentos."""
        with tempfile.TemporaryDirectory() as tmp:
            falso = os.path.join(tmp, "python3")
            with open(falso, "w", encoding="utf-8") as f:
                f.write('#!/bin/sh\nfor a in "$@"; do printf "%s\\n" "$a"; done\n')
            os.chmod(falso, 0o755)
            entorno = dict(os.environ, PATH=tmp + os.pathsep + os.environ.get("PATH", ""))
            r = subprocess.run(["sh", self.RUTA, *args], capture_output=True, text=True, env=entorno, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        return r.stdout.splitlines()

    def test_sin_argumentos_pide_el_calendario(self):
        self.assertEqual(self._ejecutar(), ["-m", "infinity_shell.panki_app", "--calendario"])

    def test_una_fecha_se_pasa_como_calendario_igual_a_la_fecha(self):
        self.assertEqual(self._ejecutar("2026-10-23"), ["-m", "infinity_shell.panki_app", "--calendario=2026-10-23"])

    def test_lo_que_no_es_una_fecha_se_pasa_tal_cual(self):
        self.assertEqual(self._ejecutar("--preguntar", "hola"),
                         ["-m", "infinity_shell.panki_app", "--calendario", "--preguntar", "hola"])


class TestEntradaDelMenuYBuscador(unittest.TestCase):
    RUTA = os.path.join(ROOT, "overlay", "usr", "share", "applications", "infinity-calendario.desktop")

    def test_campos_del_desktop(self):
        d = _desktop(self.RUTA)
        self.assertEqual(d["Name"], "Calendario")
        self.assertEqual(d["Exec"], "infinity-calendario")
        self.assertEqual(d["Type"], "Application")
        self.assertEqual(d["Terminal"], "false")
        self.assertEqual(d["Categories"], "Utility;Office;Calendar;")
        palabras = set(d["Keywords"].rstrip(";").split(";"))
        self.assertTrue({"calendario", "agenda", "reunion", "citas", "eventos"} <= palabras, palabras)

    def test_lleva_su_propia_clase_de_ventana(self):
        # Ahora es una app aparte: su ventana fija el app_id org.infinity.Calendario (panki_app.APP_ID_CALENDARIO) y el dock asocia
        # ventana y app por StartupWMClass. Distinta de la de pAnkI, o el dock rotularía una con el nombre de la otra.
        d = _desktop(self.RUTA)
        self.assertEqual(d["StartupWMClass"], "org.infinity.Calendario")
        self.assertEqual(d["StartupWMClass"], P.APP_ID_CALENDARIO)
        self.assertNotEqual(d["StartupWMClass"], P.APP_ID)
        self.assertEqual(_desktop(os.path.join(os.path.dirname(self.RUTA), "infinity-panki.desktop"))["StartupWMClass"],
                         P.APP_ID)

    def test_el_icono_es_el_del_dia_y_existe_en_el_overlay(self):
        d = _desktop(self.RUTA)
        self.assertEqual(d["Icon"], "/usr/share/infinity/calendario/dia-01.svg")
        svg = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "calendario", "dia-01.svg")
        self.assertTrue(os.path.isfile(svg))
        raiz = ET.parse(svg).getroot()
        self.assertTrue(raiz.tag.endswith("svg"))
        self.assertTrue(raiz.get("viewBox"))
        # el icono antiguo (violeta, fijo) ya no existe ni se menciona
        self.assertFalse(os.path.exists(os.path.join(ROOT, "overlay", "usr", "share", "infinity", "calendario.svg")))
        self.assertNotIn("calendario.svg", _leer(self.RUTA))

    def test_la_ventana_calendario_y_el_desktop_hablan_del_mismo_app_id_que_hyprland(self):
        conf = _leer(ROOT, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")
        self.assertIn(r"match:class ^org\.infinity\.Calendario$", conf)
        self.assertIn(r"size %d %d, match:class ^org\.infinity\.Calendario$" % (P.CAL_ANCHO, P.CAL_ALTO), conf)

    def test_ningun_archivo_nuevo_lleva_marcadores_de_plantilla(self):
        # build.sh sustituye @VARIABLES@ en el overlay: aquí no debe haber ninguna sin querer
        for ruta in (TestLanzador.RUTA, self.RUTA):
            self.assertIsNone(re.search(r"@[A-Z_]+@", _leer(ruta)), ruta)


class TestBuildInstalaLoNuevo(unittest.TestCase):
    """Equivalentes de test_build_instala_* de test_panki_app_web.py para lo nuevo."""

    @classmethod
    def setUpClass(cls):
        cls.build = _leer(ROOT, "build", "build.sh")

    def test_el_overlay_entero_se_copia_al_sistema(self):
        # infinity-calendario, su .desktop y el icono viven en overlay/: este rsync los instala, con modos
        # y permisos tal cual (-a) y sin filtros que se los salten
        self.assertRegex(self.build, r'(?m)^\s*rsync -a --chown=root:root "\$ROOT/overlay/" "\$ROOTFS/"\s*$')

    def test_el_overlay_instala_cada_archivo_en_su_ruta_final(self):
        for rel in ["usr/bin/infinity-calendario", "usr/share/applications/infinity-calendario.desktop"] + [
                f"usr/share/infinity/calendario/dia-{n:02d}.svg" for n in range(1, 32)]:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "overlay", rel)), rel)

    def test_todo_lo_de_usr_bin_del_overlay_es_ejecutable(self):
        carpeta = os.path.join(ROOT, "overlay", "usr", "bin")
        for nombre in os.listdir(carpeta):
            self.assertTrue(os.access(os.path.join(carpeta, nombre), os.X_OK), nombre)

    def test_git_guarda_el_lanzador_como_ejecutable(self):
        r = subprocess.run(["git", "ls-files", "-s", "overlay/usr/bin/infinity-calendario"],
                           cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            self.skipTest("no es un repositorio git")
        if not r.stdout.strip():
            self.skipTest("archivo aún sin añadir al índice de git")
        self.assertTrue(r.stdout.startswith("100755"), r.stdout)

    def test_la_pagina_que_se_instala_sigue_siendo_la_misma_app_html(self):
        # el calendario sigue siendo app.html (con ?vista=calendario): no hay segunda página ni segundo almacenamiento
        self.assertIn("shell/infinity_shell/panki_web/app.html", self.build)
        self.assertIn("/usr/share/infinity/panki/app.html", self.build)


# ------------------------------------------------------------------------------------------ panki_app.py
class TestLineaDeOrdenes(unittest.TestCase):
    def test_calendario_sin_fecha(self):
        self.assertEqual(P.interpretar_opciones(["--calendario"]), (None, False, None, ""))

    def test_calendario_con_fecha(self):
        self.assertEqual(P.interpretar_opciones(["--calendario=2026-10-23"]), (None, False, None, "2026-10-23"))

    def test_calendario_con_igual_vacio_es_sin_fecha(self):
        self.assertEqual(P.interpretar_opciones(["--calendario="]), (None, False, None, ""))

    def test_calendario_con_fecha_rara_es_un_error(self):
        for rara in ("--calendario=mañana", "--calendario=2026-1-3", "--calendario=2026-10-23;ls", "--calendario=../x"):
            pregunta, ayuda, error, cal = P.interpretar_opciones([rara])
            self.assertIsNotNone(error, rara)
            self.assertIsNone(cal)

    def test_una_fecha_que_no_existe_tambien_es_un_error(self):
        for rara in ("--calendario=2026-02-30", "--calendario=2026-13-01", "--calendario=2026-00-10",
                     "--calendario=2027-02-29", "--calendario=0000-01-01"):
            self.assertIsNotNone(P.interpretar_opciones([rara])[2], rara)
        self.assertEqual(P.interpretar_opciones(["--calendario=2028-02-29"]), (None, False, None, "2028-02-29"))

    def test_sin_opciones_no_pide_calendario(self):
        self.assertEqual(P.interpretar_opciones([]), (None, False, None, None))

    def test_calendario_y_preguntar_a_la_vez(self):
        self.assertEqual(P.interpretar_opciones(["--calendario", "--preguntar", "qué", "tengo", "mañana"]),
                         ("qué tengo mañana", False, None, ""))
        self.assertEqual(P.interpretar_opciones(["--calendario=2026-10-23", "--preguntar=hola"]),
                         ("hola", False, None, "2026-10-23"))

    def test_lo_que_va_tras_preguntar_sigue_siendo_texto_aunque_parezca_calendario(self):
        pregunta, _, error, cal = P.interpretar_opciones(["--preguntar", "--calendario", "x"])
        self.assertEqual(pregunta, "--calendario x")
        self.assertIsNone(error)
        self.assertIsNone(cal)

    def test_ayuda_gana(self):
        self.assertEqual(P.interpretar_opciones(["--calendario", "-h"]), (None, True, None, None))
        self.assertIn("--calendario", P.USO)

    def test_opcion_desconocida_sigue_siendo_error(self):
        self.assertIsNotNone(P.interpretar_opciones(["--no-existe"])[2])

    def test_interpretar_argumentos_conserva_su_contrato_de_tres_valores(self):
        self.assertEqual(P.interpretar_argumentos([]), (None, False, None))
        self.assertEqual(P.interpretar_argumentos(["--preguntar", "hola"]), ("hola", False, None))
        self.assertEqual(P.interpretar_argumentos(["--calendario"]), (None, False, None))

    def test_juntar_pregunta_deja_el_calendario_aparte(self):
        self.assertEqual(P.juntar_pregunta(["prog", "--calendario", "--preguntar", "a", "b"]),
                         ["prog", "--calendario", "--preguntar=a b"])
        self.assertEqual(P.juntar_pregunta(["prog", "--calendario=2026-10-23"]), ["prog", "--calendario=2026-10-23"])


class TestJavaScriptDelCalendario(unittest.TestCase):
    def test_sin_fecha(self):
        self.assertEqual(P._js_abrir_calendario(), "window.pankiAbrirCalendario(null)")
        self.assertEqual(P._js_abrir_calendario(None), "window.pankiAbrirCalendario(null)")

    def test_con_fecha_va_por_json_dumps(self):
        self.assertEqual(P._js_abrir_calendario("2026-10-23"), 'window.pankiAbrirCalendario("2026-10-23")')

    def test_algo_que_no_es_una_fecha_nunca_llega_a_la_pagina(self):
        for rara in ('2026-10-23"); alert(1);//', "ayer", "2026-10-2", "<script>", "", "1e3", "2026-02-30", "2026-10-23\n",
                     "٢٠٢٦-١٠-٢٣", 20261023, ["2026-10-23"]):
            script = P._js_abrir_calendario(rara)
            self.assertEqual(script, "window.pankiAbrirCalendario(null)", rara)

    def test_el_contrato_de_la_pagina_existe(self):
        html = _leer(APP_HTML)
        self.assertIn("window.pankiAbrirCalendario=function", html)
        # y lo que el host le pasa se vuelve a validar allí (okKey) antes de tocar el calendario
        self.assertRegex(html, r"window\.pankiAbrirCalendario=function\(fecha\)\{[^}]*okKey\(fecha\)")


class _VentanaMinima:
    """CalendarioVentana sin GTK: solo lo que usa abrir (los atributos de estado)."""

    def __init__(self, lista, webkit=True):
        self._cerrada = False
        self._webkit_disponible = webkit
        self._pagina_lista = lista
        self._calendario_pendiente = None
        self.js = []

    def _evaluar_js(self, script):
        self.js.append(script)


class TestAbrirCalendarioSinGtk(unittest.TestCase):
    """CalendarioVentana.abrir llamado como función suelta sobre un objeto mínimo (la ventana de pAnkI ya no abre nada)."""

    def test_con_la_pagina_lista_evalua_el_js(self):
        v = _VentanaMinima(True)
        P.CalendarioVentana.abrir(v, "2026-10-23")
        self.assertEqual(v.js, ['window.pankiAbrirCalendario("2026-10-23")'])
        self.assertIsNone(v._calendario_pendiente)

    def test_sin_fecha_abre_el_mes_actual(self):
        v = _VentanaMinima(True)
        P.CalendarioVentana.abrir(v)
        self.assertEqual(v.js, ["window.pankiAbrirCalendario(null)"])

    def test_una_fecha_que_no_existe_abre_el_mes_actual(self):
        v = _VentanaMinima(True)
        P.CalendarioVentana.abrir(v, "2026-02-30")
        self.assertEqual(v.js, ["window.pankiAbrirCalendario(null)"])

    def test_antes_de_cargar_se_queda_pendiente(self):
        v = _VentanaMinima(False)
        P.CalendarioVentana.abrir(v, "2026-10-23")
        self.assertEqual(v.js, [])
        self.assertEqual(v._calendario_pendiente, "2026-10-23")
        v2 = _VentanaMinima(False)
        P.CalendarioVentana.abrir(v2)
        self.assertEqual(v2._calendario_pendiente, "")  # "" = «sin fecha», distinto de None = «nada pendiente»

    def test_sin_webkit_o_cerrada_no_hace_nada_ni_falla(self):
        v = _VentanaMinima(True, webkit=False)
        P.CalendarioVentana.abrir(v, "2026-10-23")
        w = _VentanaMinima(True)
        w._cerrada = True
        P.CalendarioVentana.abrir(w)
        self.assertEqual(v.js + w.js, [])

    def test_la_ventana_de_panki_ya_no_abre_el_calendario_embebido(self):
        # El Calendario es otra ventana: la de pAnkI no tiene un abrir_calendario que mande a SU página a esa vista.
        self.assertFalse(hasattr(P.VentanaPanki, "abrir_calendario"))
        self.assertFalse(hasattr(P.VentanaPanki, "abrir"))


class TestFijarAppId(unittest.TestCase):
    """fijar_app_id_wayland con dobles: solo actúa sobre un WaylandToplevel y nunca rompe nada."""

    class _Toplevel:
        def __init__(self, falla=False):
            self.ids, self.falla = [], falla

        def set_application_id(self, app_id):
            if self.falla:
                raise RuntimeError("el compositor dijo que no")
            self.ids.append(app_id)

    class _GdkWayland:
        pass

    def setUp(self):
        self.GdkWayland = self._GdkWayland()
        self.GdkWayland.WaylandToplevel = self._Toplevel

    def test_en_una_superficie_wayland_fija_el_app_id(self):
        sup = self._Toplevel()
        self.assertTrue(P.fijar_app_id_wayland(sup, "org.infinity.Calendario", GdkWayland=self.GdkWayland))
        self.assertEqual(sup.ids, ["org.infinity.Calendario"])

    def test_en_otra_superficie_se_ignora_en_silencio(self):
        class _X11:  # xvfb: no es un WaylandToplevel
            def set_application_id(self, *_):
                raise AssertionError("no debía llamarse")
        self.assertFalse(P.fijar_app_id_wayland(_X11(), "org.infinity.Calendario", GdkWayland=self.GdkWayland))
        self.assertFalse(P.fijar_app_id_wayland(None, "org.infinity.Calendario", GdkWayland=self.GdkWayland))

    def test_un_fallo_al_fijarlo_no_rompe(self):
        sup = self._Toplevel(falla=True)
        self.assertFalse(P.fijar_app_id_wayland(sup, "org.infinity.Calendario", GdkWayland=self.GdkWayland))

    def test_sin_el_typelib_de_gdkwayland_no_rompe(self):
        from types import SimpleNamespace

        def sin_typelib(nombre, version):
            raise ValueError(f"Namespace {nombre} not available")
        with mock.patch.dict(sys.modules, {"gi": SimpleNamespace(require_version=sin_typelib)}):
            self.assertFalse(P.fijar_app_id_wayland(object(), "org.infinity.Calendario"))

    def test_sin_gi_instalado_no_rompe(self):
        with mock.patch.dict(sys.modules, {"gi": None}):  # «import gi» -> ImportError
            self.assertFalse(P.fijar_app_id_wayland(object(), "org.infinity.Calendario"))

    def test_el_app_id_es_el_del_desktop(self):
        self.assertEqual(P.APP_ID_CALENDARIO, "org.infinity.Calendario")


class TestMensajeCalendarioDeLaPagina(unittest.TestCase):
    """El mensaje {accion:'calendario', valor} que la página de pAnkI envía por el puente: validación estricta."""

    def _m(self, valor, accion="calendario"):
        return P._interpretar_mensaje_js(json.dumps({"accion": accion, "valor": valor}))

    def test_vacio_o_fecha_real_se_aceptan(self):
        self.assertEqual(self._m(""), ("calendario", ""))
        self.assertEqual(self._m("2026-10-23"), ("calendario", "2026-10-23"))
        self.assertEqual(self._m("2028-02-29"), ("calendario", "2028-02-29"))

    def test_todo_lo_demas_se_rechaza(self):
        for malo in ("2026-02-30", "2026-13-01", "2026-10-2", "26-10-23", " 2026-10-23", "2026-10-23 ", "2026-10-23\n",
                     "mañana", "2026-10-23; rm -rf /", "٢٠٢٦-١٠-٢٣", "2026/10/23", "   ", "<img src=x>", "a" * 500,
                     None, 20261023, 3.5, True, ["2026-10-23"], {"d": "2026-10-23"}):
            self.assertIsNone(self._m(malo), repr(malo))

    def test_sin_valor_o_con_forma_rara_se_rechaza(self):
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"calendario"}'))
        self.assertIsNone(P._interpretar_mensaje_js('["calendario","2026-10-23"]'))
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"Calendario","valor":""}'))
        self.assertIsNone(P._interpretar_mensaje_js('{"accion":"calendario ","valor":""}'))

    def test_el_vacio_solo_vale_para_calendario_no_para_nombre(self):
        self.assertIsNone(self._m("", accion="nombre"))
        self.assertIsNone(self._m("   ", accion="nombre"))
        self.assertEqual(self._m("Nova", accion="nombre"), ("nombre", "Nova"))
        self.assertIsNone(self._m("Nova", accion="calendario"))  # un nombre no es una fecha

    def test_las_acciones_permitidas(self):
        self.assertEqual(set(P.ACCIONES_JS), {"nombre", "calendario"})

    def test_fecha_real(self):
        for ok in ("2026-10-23", "2028-02-29", "1900-01-01", "2200-12-31"):
            self.assertTrue(P.fecha_real(ok), ok)
        for mal in ("2027-02-29", "2026-04-31", "2026-00-01", "0000-01-01", "2026-1-01", "2026-10-23\n", "", None, 3):
            self.assertFalse(P.fecha_real(mal), repr(mal))


class _VentanaDePanki:
    """VentanaPanki sin GTK: lo que usa _al_mensaje_js."""

    def __init__(self, pedir=None):
        self._cerrada = False
        self.al_pedir_calendario = pedir


class _Valor:
    """El JSCValue que entrega WebKit: solo to_string()."""

    def __init__(self, texto):
        self.texto = texto

    def to_string(self):
        return self.texto


class TestPankiAbreElCalendarioPorElPuente(unittest.TestCase):
    def _enviar(self, ventana, accion, valor):
        P.VentanaPanki._al_mensaje_js(ventana, None, _Valor(json.dumps({"accion": accion, "valor": valor})))

    def test_un_mensaje_calendario_abre_la_app_en_esa_fecha(self):
        pedidos = []
        v = _VentanaDePanki(pedidos.append)
        self._enviar(v, "calendario", "2026-10-23")
        self._enviar(v, "calendario", "")
        self.assertEqual(pedidos, ["2026-10-23", None])  # '' = el mes de hoy

    def test_un_mensaje_malo_no_abre_nada(self):
        pedidos = []
        v = _VentanaDePanki(pedidos.append)
        for malo in ("2026-02-30", "mañana", " 2026-10-23", None, 5, ["2026-10-23"]):
            self._enviar(v, "calendario", malo)
        P.VentanaPanki._al_mensaje_js(v, None, _Valor("{no es json"))
        self.assertEqual(pedidos, [])

    def test_sin_quien_lo_atienda_o_con_la_ventana_cerrada_se_ignora(self):
        self._enviar(_VentanaDePanki(None), "calendario", "2026-10-23")  # no lanza
        pedidos = []
        v = _VentanaDePanki(pedidos.append)
        v._cerrada = True
        self._enviar(v, "calendario", "2026-10-23")
        self.assertEqual(pedidos, [])

    def test_si_abrir_falla_no_rompe_la_ventana(self):
        def mal(_fecha):
            raise RuntimeError("no hay pantalla")
        with mock.patch.object(P, "registrar") as reg:
            self._enviar(_VentanaDePanki(mal), "calendario", "")
        self.assertTrue(reg.called)

    def test_un_mensaje_nombre_sigue_guardando_el_nombre_y_no_abre_el_calendario(self):
        pedidos = []
        v = _VentanaDePanki(pedidos.append)
        with mock.patch.object(P, "_guardar_nombre") as guardar:
            self._enviar(v, "nombre", "Nova")
        guardar.assert_called_once_with("Nova")
        self.assertEqual(pedidos, [])


# ------------------------------------------------------------------------------------------ con GTK
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
class TestAplicacionConGtk(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Gio, cls.Gtk = _cargar_gtk()

    def _app(self):
        app = P.Panki()
        # otro id: así no se confunde con un pAnkI de verdad que esté corriendo en la sesión
        app.set_application_id(f"org.infinity.test.calendario{id(app)}")
        app.register(None)
        self.addCleanup(lambda: [v.destroy() for v in (app.ventana, app.calendario) if v is not None])
        return app

    def test_tiene_la_accion_calendario_junto_a_alternar(self):
        app = self._app()
        self.assertIsNotNone(app.lookup_action("calendario"))
        self.assertIsNotNone(app.lookup_action("alternar"))
        # sin parámetro: se pide con `gapplication action org.infinity.Panki calendario`, igual que `alternar`
        self.assertIsNone(app.lookup_action("calendario").get_parameter_type())

    def test_la_accion_calendario_fecha_lleva_la_fecha_como_texto(self):
        app = self._app()
        accion = app.lookup_action("calendario-fecha")
        self.assertIsNotNone(accion)
        self.assertEqual(accion.get_parameter_type().dup_string(), "s")
        from gi.repository import GLib
        with mock.patch.object(app, "_abrir_calendario") as abrir:
            accion.activate(GLib.Variant("s", "2026-10-23"))
            accion.activate(GLib.Variant("s", "2026-02-30"))  # no existe: se abre el mes de hoy
            accion.activate(GLib.Variant("s", "mañana"))
        self.assertEqual([c.args for c in abrir.call_args_list], [("2026-10-23",), (None,), (None,)])

    def test_abrir_calendario_reutiliza_las_mismas_ventanas(self):
        app = self._app()
        with mock.patch.object(P.CalendarioVentana, "mostrar"):
            app._abrir_calendario()
            panki, cal = app.ventana, app.calendario
            self.assertIsInstance(panki, P.VentanaPanki)
            self.assertIsInstance(cal, P.CalendarioVentana)
            self.assertIsNot(panki, cal)
            app._abrir_calendario("2026-10-23")
            self.assertIs(app.ventana, panki, "no debe abrirse una segunda ventana de pAnkI")
            self.assertIs(app.calendario, cal, "no debe abrirse una segunda ventana del Calendario")

    def test_la_accion_calendario_presenta_la_ventana_del_calendario_y_no_la_de_panki(self):
        app = self._app()
        app.lookup_action("calendario").activate(None)  # gapplication action org.infinity.Panki calendario
        self.assertIsInstance(app.calendario, P.CalendarioVentana)
        self.assertTrue(app.calendario.get_visible())
        self.assertFalse(app.ventana.get_visible(), "la acción no muestra la ventana de pAnkI")
        app.lookup_action("alternar").activate(None)  # alternar sigue siendo de pAnkI y no toca al Calendario
        self.assertTrue(app.ventana.get_visible())
        self.assertTrue(app.calendario.get_visible())

    def test_abrir_el_calendario_no_muestra_la_ventana_de_panki(self):
        app = self._app()
        app._abrir_calendario("2026-10-23")
        self.assertTrue(app.calendario.get_visible())
        self.assertIsNotNone(app.ventana, "la página de pAnkI tiene que seguir creándose (sus recordatorios)")
        self.assertFalse(app.ventana.get_visible(), "pero NO se muestra la ventana de pAnkI")

    def test_la_ventana_calendario_es_820x620_y_se_titula_calendario(self):
        app = self._app()
        cal = app._asegurar_calendario()
        self.assertEqual(cal.get_default_size(), (820, 620))
        self.assertEqual((P.CAL_ANCHO, P.CAL_ALTO), (820, 620))
        self.assertEqual(cal.get_title(), "Calendario")
        self.assertTrue(cal.get_resizable())
        self.assertFalse(cal.get_visible(), "como la de pAnkI, nace oculta hasta que se presenta")
        self.assertEqual(app._asegurar_ventana().get_default_size(), (460, 640))  # la de pAnkI no cambia
        self.assertEqual(app.ventana.get_title(), "pAnkI")

    def test_la_x_del_calendario_lo_oculta_y_la_aplicacion_sigue_viva(self):
        app = self._app()
        cal = app._asegurar_calendario()
        cal.mostrar()
        self.assertTrue(cal.get_visible())
        self.assertTrue(cal._al_cerrar(cal))
        self.assertFalse(cal.get_visible())
        cal.mostrar()  # se puede volver a presentar
        self.assertTrue(cal.get_visible())

    def test_sin_webkitgtk_el_calendario_se_degrada_con_un_aviso(self):
        app = self._app()
        cal = app._asegurar_calendario()
        if cal._webkit_disponible:
            self.skipTest("WebKitGTK 6.0 SÍ está disponible aquí; esta prueba es para cuando falta")
        self.assertIsInstance(cal.get_child(), self.Gtk.Label)
        self.assertIn("Calendario", cal.get_child().get_label())
        cal.abrir("2026-10-23")  # no hay motor web: no debe lanzar
        self.assertIsNone(cal._calendario_pendiente)

    def test_la_ventana_recuerda_el_calendario_pedido_antes_de_cargar(self):
        app = self._app()
        v = app._asegurar_calendario()
        if not v._webkit_disponible:
            self.skipTest("sin WebKitGTK 6.0: abrir no hace nada (se prueba en TestAbrirCalendarioSinGtk)")
        v._pagina_lista = False
        v.abrir("2026-10-23")
        self.assertEqual(v._calendario_pendiente, "2026-10-23")


class _LineaDeOrdenes:
    """El GApplicationCommandLine de pega de do_command_line."""

    def __init__(self, *args):
        self.args = ["infinity"] + list(args)
        self.salida, self.errores = [], []

    def get_arguments(self):
        return self.args

    def print_literal(self, t):
        self.salida.append(t)

    def printerr_literal(self, t):
        self.errores.append(t)


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestDosVentanasUnProceso(unittest.TestCase):
    """Las dos ventanas (pAnkI y Calendario) con GTK real y una WebKit de PEGA: se comprueba cómo se construyen (misma
    NetworkSession, URI, puente, app_id, orden de realize/present) sin necesitar WebKitGTK, que aquí no está."""

    @classmethod
    def setUpClass(cls):
        cls.Gio, cls.Gtk = _cargar_gtk()
        from types import SimpleNamespace
        from gi.repository import GObject
        Gtk = cls.Gtk

        class FalsoWebView(Gtk.Label):
            __gsignals__ = {"load-changed": (GObject.SignalFlags.RUN_FIRST, None, (int,))}

            def __init__(self, user_content_manager=None, network_session=None):
                super().__init__(label="webview de pega")
                self.ucm, self.sesion = user_content_manager, network_session
                self.uris, self.js, self.html = [], [], []

            def load_uri(self, uri):
                self.uris.append(uri)

            def load_html(self, html, base):
                self.html.append(html)

            def evaluate_javascript(self, script, longitud, mundo, fuente, cancelable, callback, datos):
                self.js.append(script)

            def evaluate_javascript_finish(self, resultado):
                return None

        class FalsoUCM:
            def __init__(self):
                self.conexiones, self.manejadores, self.guiones = [], [], []

            def connect(self, nombre, callback):
                self.conexiones.append(nombre)

            def register_script_message_handler(self, nombre):
                self.manejadores.append(nombre)

            def add_script(self, guion):
                self.guiones.append(guion)

        class FalsaSesion:
            creadas = []

            def __init__(self, datos, cache):
                self.datos, self.cache = datos, cache
                FalsaSesion.creadas.append(self)

            @classmethod
            def new(cls, datos, cache):
                return cls(datos, cache)

            @classmethod
            def new_ephemeral(cls):
                return cls(None, None)

        cls.FalsoWebView, cls.FalsaSesion = FalsoWebView, FalsaSesion
        cls.WebKit = SimpleNamespace(
            WebView=FalsoWebView, UserContentManager=FalsoUCM, NetworkSession=FalsaSesion,
            UserScript=SimpleNamespace(new=lambda codigo, *resto: SimpleNamespace(codigo=codigo, momento=resto[1])),
            UserContentInjectedFrames=SimpleNamespace(ALL_FRAMES=0),
            UserScriptInjectionTime=SimpleNamespace(START="START", END="END"),
            LoadEvent=SimpleNamespace(STARTED=0, FINISHED=3))

    def setUp(self):
        self.FalsaSesion.creadas.clear()
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        for parche in (mock.patch.dict(os.environ, {"XDG_DATA_HOME": tmp.name, "XDG_CONFIG_HOME": tmp.name,
                                                    "XDG_CACHE_HOME": tmp.name}),
                       mock.patch.object(P, "_importar_webkit", return_value=self.WebKit)):
            parche.start()
            self.addCleanup(parche.stop)
        self.tmp = tmp.name
        self.app = P.Panki()
        self.app.set_application_id(f"org.infinity.test.dosventanas{id(self.app)}")
        self.app.register(None)
        self.addCleanup(lambda: [v.destroy() for v in (self.app.ventana, self.app.calendario) if v is not None])

    def _cargada(self, ventana):
        """Simula el primer load-changed == FINISHED de la página."""
        ventana.webview.emit("load-changed", self.WebKit.LoadEvent.FINISHED)

    def test_las_dos_ventanas_comparten_una_sola_networksession_y_el_mismo_directorio(self):
        self.app._abrir_calendario()
        panki, cal = self.app.ventana, self.app.calendario
        self.assertTrue(panki._webkit_disponible and cal._webkit_disponible)
        self.assertIs(panki.webview.sesion, cal.webview.sesion)
        self.assertEqual(len(self.FalsaSesion.creadas), 1, "una sola sesión de red para las dos ventanas")
        sesion = self.FalsaSesion.creadas[0]
        base = os.path.join(self.tmp, "infinity", "panki", "webkit")
        self.assertEqual((sesion.datos, sesion.cache), (os.path.join(base, "datos"), os.path.join(base, "cache")))
        self.assertEqual(os.stat(sesion.datos).st_mode & 0o777, 0o700)

    def test_la_sesion_se_crea_aunque_solo_se_abra_el_calendario_y_aunque_se_abra_pankI_despues(self):
        self.app._asegurar_calendario()
        self.app._asegurar_ventana()
        self.assertIs(self.app.ventana.webview.sesion, self.app.calendario.webview.sesion)
        self.assertEqual(len(self.FalsaSesion.creadas), 1)

    def test_una_ventana_suelta_sin_aplicacion_crea_su_propia_sesion(self):
        v = P.VentanaPanki(self.app)  # sin proveedor: lo de siempre (una sesión propia)
        self.addCleanup(v.destroy)
        self.assertEqual(len(self.FalsaSesion.creadas), 1)

    def test_la_uri_del_calendario_lleva_la_vista_y_la_de_panki_no(self):
        self.app._abrir_calendario()
        (uri_panki,) = self.app.ventana.webview.uris
        (uri_cal,) = self.app.calendario.webview.uris
        self.assertTrue(uri_panki.startswith("file://") and uri_panki.endswith("panki_web/app.html"), uri_panki)
        self.assertEqual(uri_cal, uri_panki + "?vista=calendario")

    def test_solo_la_pagina_de_panki_tiene_el_puente_de_mensajes(self):
        self.app._abrir_calendario()
        ucm_panki, ucm_cal = self.app.ventana._ucm, self.app.calendario._ucm
        self.assertEqual(ucm_panki.manejadores, ["panki"])
        self.assertEqual(ucm_panki.conexiones, ["script-message-received::panki"])
        self.assertEqual(ucm_cal.manejadores, [], "la página del Calendario no emite «nombre» ni nada: sin manejador")
        self.assertEqual(ucm_cal.conexiones, [])
        codigos_cal = [g.codigo for g in ucm_cal.guiones]
        self.assertEqual(len(codigos_cal), 1)
        self.assertIn("window.__PANKI_NOMBRE_SISTEMA__", codigos_cal[0])
        self.assertNotIn(P.PUENTE_JS, codigos_cal, "sin window.pankiPreguntar ni traducción de eventos en el Calendario")
        self.assertIn(P.PUENTE_JS, [g.codigo for g in ucm_panki.guiones])

    def test_la_fecha_pedida_antes_de_cargar_llega_solo_al_calendario(self):
        self.app._abrir_calendario("2026-10-23")
        panki, cal = self.app.ventana, self.app.calendario
        self.assertEqual(cal.webview.js, [])
        self._cargada(cal)
        self._cargada(panki)
        self.assertEqual(cal.webview.js, ['window.pankiAbrirCalendario("2026-10-23")'])
        self.assertEqual(panki.webview.js, [P._js_aplicar_nombre("pAnkI")], "a pAnkI solo le llega su nombre")

    def test_con_la_pagina_ya_cargada_se_navega_al_instante(self):
        self.app._abrir_calendario()
        self._cargada(self.app.calendario)
        self.app._abrir_calendario("2027-01-31")
        self.app._abrir_calendario()
        self.assertEqual(self.app.calendario.webview.js,
                         ["window.pankiAbrirCalendario(null)",  # la petición que esperaba a la carga
                          'window.pankiAbrirCalendario("2027-01-31")', "window.pankiAbrirCalendario(null)"])
        # una segunda carga (recarga) no repite lo pendiente
        self._cargada(self.app.calendario)
        self.assertEqual(len(self.app.calendario.webview.js), 3)

    def test_el_mensaje_calendario_de_la_pagina_de_panki_abre_la_ventana_del_calendario(self):
        panki = self.app._asegurar_ventana()
        self._cargada(panki)
        panki._al_mensaje_js(None, _Valor('{"accion":"calendario","valor":"2026-10-23"}'))
        cal = self.app.calendario
        self.assertIsNotNone(cal, "el mensaje crea y presenta la ventana del Calendario")
        self.assertTrue(cal.get_visible())
        self.assertFalse(panki.get_visible(), "y no muestra (ni oculta) la ventana de pAnkI")
        self._cargada(cal)
        self.assertEqual(cal.webview.js, ['window.pankiAbrirCalendario("2026-10-23")'])
        self.assertEqual(panki.webview.js, [P._js_aplicar_nombre("pAnkI")], "la página de pAnkI no se toca")

    def test_un_mensaje_calendario_malo_no_crea_ninguna_ventana(self):
        panki = self.app._asegurar_ventana()
        for valor in ("2026-02-30", "ayer", " 2026-10-23", 7):
            panki._al_mensaje_js(None, _Valor(json.dumps({"accion": "calendario", "valor": valor})))
        self.assertIsNone(self.app.calendario)

    def test_el_mensaje_nombre_solo_lo_atiende_la_ventana_de_panki(self):
        self.app._abrir_calendario()
        with mock.patch.object(P, "_guardar_nombre") as guardar:
            self.app.ventana._al_mensaje_js(None, _Valor('{"accion":"nombre","valor":"Nova"}'))
            # la ventana del Calendario ni siquiera tiene _al_mensaje_js ni manejador registrado
            self.assertFalse(hasattr(P.CalendarioVentana, "_al_mensaje_js"))
        guardar.assert_called_once_with("Nova")
        self.assertEqual(self.app.ventana.get_title(), "pAnkI", "el menú Calendario no cambia el título de pAnkI")

    def test_linea_de_ordenes_calendario_solo_muestra_el_calendario(self):
        linea = _LineaDeOrdenes("--calendario=2026-10-23")
        self.assertEqual(self.app.do_command_line(linea), 0)
        self.assertTrue(self.app.calendario.get_visible())
        self.assertFalse(self.app.ventana.get_visible(), "pAnkI se crea pero no se muestra")
        self._cargada(self.app.calendario)
        self.assertEqual(self.app.calendario.webview.js, ['window.pankiAbrirCalendario("2026-10-23")'])

    def test_linea_de_ordenes_sin_calendario_muestra_solo_panki(self):
        self.assertEqual(self.app.do_command_line(_LineaDeOrdenes()), 0)
        self.assertTrue(self.app.ventana.get_visible())
        self.assertIsNone(self.app.calendario, "sin --calendario no se crea la ventana del Calendario")

    def test_linea_de_ordenes_calendario_y_pregunta_muestran_las_dos(self):
        self.assertEqual(self.app.do_command_line(_LineaDeOrdenes("--calendario", "--preguntar", "qué", "tengo")), 0)
        self.assertTrue(self.app.calendario.get_visible())
        self.assertTrue(self.app.ventana.get_visible())
        self.assertEqual(self.app.ventana._pregunta_pendiente, "qué tengo")

    def test_linea_de_ordenes_con_fecha_mala_no_abre_nada(self):
        linea = _LineaDeOrdenes("--calendario=2026-02-30")
        self.assertEqual(self.app.do_command_line(linea), 2)
        self.assertIn("Fecha no válida", linea.errores[0])
        self.assertIsNone(self.app.ventana)

    # ------------------------------------------------------------------ app_id
    def test_el_app_id_se_fija_tras_realize_y_antes_de_present(self):
        cal = self.app._asegurar_calendario()
        orden = []
        with mock.patch.object(P.CalendarioVentana, "realize", lambda self: orden.append("realize")), \
                mock.patch.object(P.CalendarioVentana, "present", lambda self: orden.append("present")), \
                mock.patch.object(P.CalendarioVentana, "get_surface", lambda self: "la-superficie"), \
                mock.patch.object(P, "fijar_app_id_wayland",
                                  lambda sup, app_id: orden.append(("app_id", sup, app_id))):
            cal.mostrar()
            cal.mostrar()  # solo la primera vez: la superficie ya está mapeada
        self.assertEqual(orden, ["realize", ("app_id", "la-superficie", "org.infinity.Calendario"), "present", "present"])

    def test_un_fallo_al_fijar_el_app_id_no_impide_mostrar_la_ventana(self):
        cal = self.app._asegurar_calendario()
        with mock.patch.object(P.CalendarioVentana, "realize", side_effect=RuntimeError("sin superficie")), \
                mock.patch.object(P, "registrar") as reg:
            cal.mostrar()
        self.assertTrue(cal.get_visible())
        self.assertTrue(reg.called)

    def test_bajo_x11_no_hay_app_id_que_fijar_y_todo_sigue_igual(self):
        # xvfb: la superficie es de X11, no un WaylandToplevel: se ignora en silencio
        cal = self.app._asegurar_calendario()
        cal.mostrar()
        self.assertTrue(cal.get_visible())
        self.assertFalse(P.fijar_app_id_wayland(cal.get_surface(), "org.infinity.Calendario"))

    def test_con_una_superficie_wayland_de_pega_se_llama_a_set_application_id(self):
        # el camino completo: mostrar() -> realize() -> fijar_app_id_wayland(get_surface()) -> set_application_id -> present()
        cal = self.app._asegurar_calendario()
        sup = TestFijarAppId._Toplevel()
        gdk = TestFijarAppId._GdkWayland()
        gdk.WaylandToplevel = TestFijarAppId._Toplevel
        real = P.fijar_app_id_wayland
        with mock.patch.object(P.CalendarioVentana, "realize", lambda self: None), \
                mock.patch.object(P.CalendarioVentana, "present", lambda self: None), \
                mock.patch.object(P.CalendarioVentana, "get_surface", lambda self: sup), \
                mock.patch.object(P, "fijar_app_id_wayland", lambda s, a: real(s, a, GdkWayland=gdk)):
            cal.mostrar()
        self.assertEqual(sup.ids, ["org.infinity.Calendario"])


class TestRejillaDeApps(unittest.TestCase):
    """La biblioteca (rejilla de apps) enumera los .desktop; Calendario se comporta como pAnkI y Ajustes."""

    def test_aparece_en_la_rejilla_con_el_filtro_apagado_como_las_demas_apps_del_sistema(self):
        from types import SimpleNamespace
        from infinity_shell import biblioteca as B
        ruta = "/usr/share/applications/infinity-calendario.desktop"  # donde build.sh instala el overlay
        cal = SimpleNamespace(id="infinity-calendario.desktop", nombre="Calendario", ruta=ruta)
        panki = SimpleNamespace(id="infinity-panki.desktop", nombre="pAnkI",
                                ruta="/usr/share/applications/infinity-panki.desktop")
        self.assertFalse(B.es_descargada(ruta))  # es una app del sistema, igual que pAnkI
        b = B.Biblioteca(ruta=os.devnull)
        b.solo_descargadas = False
        self.assertEqual([a.nombre for a in b.vista([panki, cal])], ["Calendario", "pAnkI"])
        b.solo_descargadas = True  # por defecto la rejilla oculta lo que viene con el sistema (decisión de la biblioteca)
        self.assertEqual(b.vista([panki, cal]), [])


class TestBuscadorDeApps(unittest.TestCase):
    """El lanzador (apps.Indice) encuentra «Calendario» al buscar «calendario» o «agenda»."""

    def test_calendario_aparece_al_buscar_calendario_y_agenda(self):
        try:
            import gi
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gio
            from infinity_shell import apps
            from infinity_shell.search import normalizar
        except (ImportError, ValueError) as e:
            self.skipTest(f"sin GTK/GIO: {e}")
        ruta = os.path.join(ROOT, "overlay", "usr", "share", "applications", "infinity-calendario.desktop")
        panki = os.path.join(ROOT, "overlay", "usr", "share", "applications", "infinity-panki.desktop")
        with tempfile.TemporaryDirectory() as tmp:
            # Gio solo carga un .desktop si su Exec existe en el PATH
            for nombre in ("infinity-calendario", "infinity-panki"):
                p = os.path.join(tmp, nombre)
                with open(p, "w", encoding="utf-8") as f:
                    f.write("#!/bin/sh\n")
                os.chmod(p, 0o755)
            with mock.patch.dict(os.environ, {"PATH": tmp + os.pathsep + os.environ.get("PATH", "")}):
                info = Gio.DesktopAppInfo.new_from_filename(ruta)
                info_panki = Gio.DesktopAppInfo.new_from_filename(panki)
            if info is None or info_panki is None:
                self.skipTest("este Gio no carga .desktop sueltos")
            self.assertTrue(info.should_show(), "tiene que verse en la rejilla de apps")
            cal, pan = apps.App(info), apps.App(info_panki)
            self.assertEqual(cal.nombre, "Calendario")
            self.assertGreater(cal.puntaje(normalizar("calendario")), 1000)  # coincide con el NOMBRE
            self.assertGreater(cal.puntaje(normalizar("agenda")), 0)
            self.assertGreater(cal.puntaje(normalizar("agenda")), pan.puntaje(normalizar("agenda")),
                               "«agenda» tiene que llevar a Calendario antes que a pAnkI")
            self.assertEqual(cal.puntaje(normalizar("zzzz")), 0)


# ------------------------------------------------------------------------------------------ fechas
class TestDiasDeLaSemanaDeReferencia(unittest.TestCase):
    """Las fechas que pidió el usuario, contra el calendario de Python (la fuente independiente)."""

    def test_los_cuatro_casos_obligatorios(self):
        for (y, m, d), esperado in (((2026, 10, 19), "lunes"), ((2026, 10, 23), "viernes"),
                                    ((2027, 1, 1), "viernes"), ((2028, 2, 29), "martes")):
            self.assertEqual(DIAS[datetime.date(y, m, d).weekday()], esperado, (y, m, d))

    def test_el_19_de_octubre_de_2026_no_es_jueves(self):
        self.assertNotEqual(DIAS[datetime.date(2026, 10, 19).weekday()], "jueves")


class TestAuditoriaEstaticaDeFechas(unittest.TestCase):
    """app.html no puede volver a usar los patrones que dan el día equivocado según la zona horaria."""

    @classmethod
    def setUpClass(cls):
        html = _leer(APP_HTML)
        i = html.index("<script>")
        codigo = html[i:html.index("</script>", i)]
        codigo = re.sub(r"/\*.*?\*/", "", codigo, flags=re.S)  # sin comentarios: ahí se explica qué NO hacer
        cls.codigo = re.sub(r"(?m)^\s*//.*$", "", codigo)

    def _no_aparece(self, patron, motivo):
        self.assertIsNone(re.search(patron, self.codigo), motivo)

    def test_no_se_crean_fechas_desde_texto_iso(self):
        # new Date('2026-10-23') se interpreta como UTC: en Chile/Argentina muestra el día ANTERIOR
        self._no_aparece(r"new Date\(\s*['\"`]", "new Date('…') desde texto")
        self._no_aparece(r"new Date\(\s*[\w.\[\]]+\s*\+\s*['\"`]", "new Date(x+'T12:00:00') y parecidos")
        self._no_aparece(r"Date\.parse\(", "Date.parse")

    def test_hoy_no_se_saca_de_toISOString(self):
        self._no_aparece(r"toISOString\s*\(", "toISOString() da el día UTC, no el local")
        self._no_aparece(r"toUTCString|toJSON\s*\(", "formatos UTC")

    def test_los_nombres_de_dias_y_meses_no_dependen_de_intl(self):
        self._no_aparece(r"toLocaleDateString", "toLocaleDateString depende del locale/ICU")
        self._no_aparece(r"weekday\s*:", "Intl con weekday")
        self._no_aparece(r"toLocaleTimeString", "toLocaleTimeString da 12 h o 24 h según el locale")

    def test_manana_no_se_calcula_sumando_24_horas(self):
        self._no_aparece(r"\+\s*(?:24\s*\*\s*)?(?:60\s*\*\s*)*(?:864e5|86400000|864e2)", "sumar un día en milisegundos")
        self._no_aparece(r"getTime\(\)\s*\+\s*(?:24|1440)\s*\*", "sumar 24 h")

    def test_el_dia_de_la_semana_sale_de_la_tabla_y_no_de_getDay_suelto(self):
        # getDay() solo se admite dentro de la propia utilidad (si alguien la vuelve a usar, que sea a conciencia)
        self.assertLessEqual(len(re.findall(r"\.getDay\(\)", self.codigo)), 0)

    def test_las_tablas_de_nombres_estan_bien(self):
        m = re.search(r"const DAYS_ES=\[(.*?)\];", self.codigo)
        self.assertIsNotNone(m)
        dias = [x.strip("'\"") for x in m.group(1).split(",")]
        self.assertEqual(dias, ["domingo", "lunes", "martes", "miércoles", "jueves", "viernes", "sábado"])  # 0 = domingo
        m = re.search(r"const MONTHS=\[(.*?)\];", self.codigo)
        meses = [x.strip("'\"") for x in m.group(1).split(",")]
        self.assertEqual(meses, ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                 "septiembre", "octubre", "noviembre", "diciembre"])


class TestPaginaYSuPuente(unittest.TestCase):
    def test_los_botones_ver_en_el_calendario_sobreviven_a_recargar_la_pagina(self):
        # cleanHTML (historial del chat guardado) solo deja atributos conocidos: data-cal tiene que estar
        html = _leer(APP_HTML)
        m = re.search(r"const OKATTR=new Set\(\[(.*?)\]\)", html)
        self.assertIsNotNone(m)
        self.assertIn("'data-cal'", m.group(1))

    def test_los_datos_no_se_duplican(self):
        # el calendario lee S.tasks: no existe otra clave de almacenamiento ni otro arreglo de eventos
        html = _leer(APP_HTML)
        self.assertIn("localStorage.setItem(KEY,", html)  # S (tareas incluidas) se guarda bajo la clave de siempre
        self.assertNotIn("infinitos.calendario", html)
        self.assertNotIn("S.events", html)
        self.assertNotIn("S.eventos", html)

    def test_el_calendario_empieza_la_semana_en_lunes(self):
        html = _leer(APP_HTML)
        self.assertIn('<span>L</span><span>M</span><span>X</span><span>J</span><span>V</span><span>S</span><span>D</span>', html)


if __name__ == "__main__":
    unittest.main()
