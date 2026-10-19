"""La app «Calendario» (la vista de calendario de pAnkI) y todo lo que la rodea, SIN navegador.

Cubre lo que no necesita ni GTK ni Playwright:
  * los archivos nuevos del overlay (infinity-calendario, su .desktop y su icono) y que build.sh los instala
    (el overlay entero se copia con rsync, así que no hay líneas nuevas que añadir en build.sh);
  * la línea de órdenes de panki_app.py: --calendario[=AAAA-MM-DD], junto a --preguntar, y el JavaScript que
    se le inyecta a la página (siempre por json.dumps);
  * el contrato con la página (window.pankiAbrirCalendario) y una auditoría ESTÁTICA de app.html contra los
    errores clásicos de fechas: new Date('AAAA-MM-DD') (se lee como UTC y en Chile/Argentina da el día
    anterior), toISOString() para sacar «hoy», sumar 24 h para «mañana», Intl/toLocaleDateString para los
    nombres de los días;
  * los días de la semana de referencia (19/10/2026 = lunes…), comprobados contra el calendario de Python.

Las pruebas que ejecutan la página de verdad (zonas horarias, agendar por chat, la vista del calendario,
persistencia) están en test_panki_calendario_web.py y se saltan si no hay Playwright/Chromium. Las que
necesitan GTK 4 (la acción `calendario` de la aplicación, el buscador de apps) se saltan si no hay GTK.
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

    def test_es_un_script_sh_valido_que_abre_panki_en_el_calendario(self):
        texto = _leer(self.RUTA)
        self.assertTrue(texto.startswith("#!/bin/sh\n"))
        self.assertEqual(subprocess.run(["sh", "-n", self.RUTA]).returncode, 0)
        self.assertIn("export PYTHONPATH=/usr/lib/infinity", texto)
        self.assertIn('export GSK_RENDERER="${GSK_RENDERER:-cairo}"', texto)
        self.assertIn("infinity_shell.panki_app", texto)
        self.assertIn("--calendario", texto)
        self.assertNotIn("Gemini", texto)

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

    def test_no_comparte_la_clase_de_ventana_de_panki(self):
        # Es la MISMA ventana (org.infinity.Panki). Si este .desktop también la reclamara, el dock (que asocia
        # ventana y app por StartupWMClass y elige por orden alfabético) rotularía la ventana de pAnkI como
        # «Calendario».
        self.assertNotIn("StartupWMClass", _desktop(self.RUTA))

    def test_el_icono_existe_en_el_overlay_y_es_un_svg_violeta(self):
        d = _desktop(self.RUTA)
        self.assertEqual(d["Icon"], "/usr/share/infinity/calendario.svg")
        svg = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "calendario.svg")
        self.assertTrue(os.path.isfile(svg))
        raiz = ET.parse(svg).getroot()
        self.assertTrue(raiz.tag.endswith("svg"))
        self.assertIn("#b9a8ff", _leer(svg).lower())
        self.assertEqual(raiz.get("viewBox"), "0 0 256 256")

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
        for rel in ("usr/bin/infinity-calendario",
                    "usr/share/applications/infinity-calendario.desktop",
                    "usr/share/infinity/calendario.svg"):
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
        # el calendario va DENTRO de app.html: no hay segunda página ni segundo almacenamiento
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
        for rara in ('2026-10-23"); alert(1);//', "ayer", "2026-10-2", "<script>", "", "1e3"):
            script = P._js_abrir_calendario(rara)
            self.assertEqual(script, "window.pankiAbrirCalendario(null)", rara)

    def test_el_contrato_de_la_pagina_existe(self):
        html = _leer(APP_HTML)
        self.assertIn("window.pankiAbrirCalendario=function", html)
        # y lo que el host le pasa se vuelve a validar allí (okKey) antes de tocar el calendario
        self.assertRegex(html, r"window\.pankiAbrirCalendario=function\(fecha\)\{[^}]*okKey\(fecha\)")


class _VentanaMinima:
    """VentanaPanki sin GTK: solo lo que usa abrir_calendario (los atributos de estado)."""

    def __init__(self, lista, webkit=True):
        self._cerrada = False
        self._webkit_disponible = webkit
        self._pagina_lista = lista
        self._calendario_pendiente = None
        self.js = []

    def _evaluar_js(self, script):
        self.js.append(script)


class TestAbrirCalendarioSinGtk(unittest.TestCase):
    """VentanaPanki.abrir_calendario llamado como función suelta sobre un objeto mínimo."""

    def test_con_la_pagina_lista_evalua_el_js(self):
        v = _VentanaMinima(True)
        P.VentanaPanki.abrir_calendario(v, "2026-10-23")
        self.assertEqual(v.js, ['window.pankiAbrirCalendario("2026-10-23")'])
        self.assertIsNone(v._calendario_pendiente)

    def test_sin_fecha_abre_el_mes_actual(self):
        v = _VentanaMinima(True)
        P.VentanaPanki.abrir_calendario(v)
        self.assertEqual(v.js, ["window.pankiAbrirCalendario(null)"])

    def test_antes_de_cargar_se_queda_pendiente(self):
        v = _VentanaMinima(False)
        P.VentanaPanki.abrir_calendario(v, "2026-10-23")
        self.assertEqual(v.js, [])
        self.assertEqual(v._calendario_pendiente, "2026-10-23")
        v2 = _VentanaMinima(False)
        P.VentanaPanki.abrir_calendario(v2)
        self.assertEqual(v2._calendario_pendiente, "")  # "" = «sin fecha», distinto de None = «nada pendiente»

    def test_sin_webkit_o_cerrada_no_hace_nada_ni_falla(self):
        v = _VentanaMinima(True, webkit=False)
        P.VentanaPanki.abrir_calendario(v, "2026-10-23")
        w = _VentanaMinima(True)
        w._cerrada = True
        P.VentanaPanki.abrir_calendario(w)
        self.assertEqual(v.js + w.js, [])


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
        self.addCleanup(lambda: app.ventana is not None and app.ventana.destroy())
        return app

    def test_tiene_la_accion_calendario_junto_a_alternar(self):
        app = self._app()
        self.assertIsNotNone(app.lookup_action("calendario"))
        self.assertIsNotNone(app.lookup_action("alternar"))
        # sin parámetro: se pide con `gapplication action org.infinity.Panki calendario`, igual que `alternar`
        self.assertIsNone(app.lookup_action("calendario").get_parameter_type())

    def test_abrir_calendario_reutiliza_la_misma_ventana(self):
        app = self._app()
        with mock.patch.object(P.VentanaPanki, "mostrar"):
            app._abrir_calendario()
            primera = app.ventana
            self.assertIsNotNone(primera)
            app._abrir_calendario("2026-10-23")
            self.assertIs(app.ventana, primera, "no debe abrirse una segunda ventana")

    def test_la_ventana_recuerda_el_calendario_pedido_antes_de_cargar(self):
        app = self._app()
        v = app._asegurar_ventana()
        if not v._webkit_disponible:
            self.skipTest("sin WebKitGTK 6.0: abrir_calendario no hace nada (se prueba en TestAbrirCalendarioSinGtk)")
        v._pagina_lista = False
        v.abrir_calendario("2026-10-23")
        self.assertEqual(v._calendario_pendiente, "2026-10-23")


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
