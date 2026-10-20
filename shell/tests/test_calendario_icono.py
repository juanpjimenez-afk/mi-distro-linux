"""El icono del Calendario con el número de hoy: elección del fichero, cambio a medianoche y su uso en el lanzador."""
import datetime
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import calendario_icono as C  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CARPETA = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "calendario")


def _leer(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


class TestElegirDia(unittest.TestCase):
    def test_dia_hoy_es_el_dia_local(self):
        self.assertEqual(C.dia_hoy(datetime.datetime(2026, 10, 19, 23, 59)), 19)
        self.assertEqual(C.dia_hoy(datetime.datetime(2026, 10, 20, 0, 0, 1)), 20)
        self.assertEqual(C.dia_hoy(datetime.datetime(2028, 2, 29, 12)), 29)

    def test_cada_dia_del_mes_tiene_su_icono(self):
        for dia in range(1, 32):
            ruta = C.ruta_dia(dia, [CARPETA])
            self.assertTrue(ruta and os.path.isfile(ruta), f"falta el icono del día {dia}")
            self.assertTrue(ruta.endswith(f"dia-{dia:02d}.svg"))

    def test_dias_fuera_de_rango_o_de_otro_tipo_no_tienen_icono(self):
        for malo in (0, 32, -1, 100, "19", 19.0, None, True):
            self.assertIsNone(C.ruta_dia(malo, [CARPETA]), malo)

    def test_sin_carpetas_no_hay_icono(self):
        self.assertIsNone(C.ruta_dia(19, ["", "/no/existe"]))

    def test_la_primera_carpeta_que_lo_tenga_gana(self):
        self.assertTrue(C.ruta_dia(5, ["/no/existe", CARPETA]).startswith(CARPETA))


class TestMedianoche(unittest.TestCase):
    def test_a_las_23_59_falta_casi_nada(self):
        ahora = datetime.datetime(2026, 10, 19, 23, 59, 30)
        self.assertLessEqual(C.segundos_hasta_el_cambio_de_dia(ahora), 33)
        self.assertGreaterEqual(C.segundos_hasta_el_cambio_de_dia(ahora), 30)

    def test_se_acota_a_una_hora(self):
        self.assertEqual(C.segundos_hasta_el_cambio_de_dia(datetime.datetime(2026, 10, 19, 8, 0)), 3600)
        self.assertEqual(C.segundos_hasta_el_cambio_de_dia(datetime.datetime(2026, 10, 19, 8, 0), maximo=120), 120)

    def test_nunca_es_menos_de_un_segundo(self):
        self.assertGreaterEqual(C.segundos_hasta_el_cambio_de_dia(datetime.datetime(2026, 10, 19, 23, 59, 59)), 1)

    def test_el_dia_despues_de_la_medianoche_cambia(self):
        antes = datetime.datetime(2026, 10, 31, 23, 59, 50)
        despues = antes + datetime.timedelta(seconds=C.segundos_hasta_el_cambio_de_dia(antes))
        self.assertEqual((C.dia_hoy(antes), C.dia_hoy(despues)), (31, 1))


class TestSvg(unittest.TestCase):
    def test_los_31_svg_son_xml_valido_y_llevan_su_numero(self):
        for dia in range(1, 32):
            ruta = os.path.join(CARPETA, f"dia-{dia:02d}.svg")
            raiz = ET.parse(ruta).getroot()
            titulo = raiz.find("{http://www.w3.org/2000/svg}title")
            self.assertEqual(titulo.text, f"Calendario — {dia:02d}", ruta)
            self.assertEqual(raiz.get("viewBox"), "0 0 512 512")

    def test_no_dependen_de_fuentes_ni_de_recursos_externos(self):
        for dia in (1, 19, 31):
            texto = _leer(CARPETA, f"dia-{dia:02d}.svg")
            self.assertNotIn("<text", texto)          # los números son trazos, no tipografía
            self.assertNotIn("font-family", texto)
            self.assertIsNone(re.search(r'(href|src)\s*=\s*"(?!#)', texto), "referencia externa")

    def test_cada_dia_se_dibuja_distinto(self):
        textos = {_leer(CARPETA, f"dia-{d:02d}.svg") for d in range(1, 32)}
        self.assertEqual(len(textos), 31)


class TestIconoDeLaApp(unittest.TestCase):
    """apps.App.icono: dinámico solo para el Calendario."""

    @classmethod
    def setUpClass(cls):
        try:
            from infinity_shell import apps
            from gi.repository import Gio
        except Exception as e:  # noqa: BLE001
            raise unittest.SkipTest(f"sin gi: {e}")
        cls.apps, cls.Gio = apps, Gio

    def _app(self, id_):
        Gio = self.Gio

        class Info:
            def get_id(self): return id_
            def get_name(self): return "Prueba"
            def get_generic_name(self): return ""
            def get_description(self): return ""
            def get_icon(self): return Gio.ThemedIcon.new("application-x-executable")
            def get_commandline(self): return "prueba"
        return self.apps.App(Info())

    def test_el_calendario_lleva_el_numero_de_hoy(self):
        icono = self._app(C.ID_APP).icono
        self.assertIsInstance(icono, self.Gio.FileIcon)
        self.assertRegex(icono.get_file().get_path(), rf"dia-{C.dia_hoy():02d}\.svg$")

    def test_las_demas_apps_conservan_su_icono(self):
        icono = self._app("foot.desktop").icono
        self.assertIsInstance(icono, self.Gio.ThemedIcon)

    def test_si_faltan_los_ficheros_se_usa_el_del_desktop(self):
        viejas = C.RUTAS[:]
        try:
            C.RUTAS[:] = ["/no/existe"]
            self.assertIsInstance(self._app(C.ID_APP).icono, self.Gio.ThemedIcon)
        finally:
            C.RUTAS[:] = viejas


class TestIntegracion(unittest.TestCase):
    def test_el_calendario_esta_fijado_por_defecto(self):
        fijados = _leer(ROOT, "overlay", "etc", "infinity", "fijados")
        self.assertIn(C.ID_APP, fijados.split())
        dock = _leer(ROOT, "shell", "infinity_shell", "dock.py")
        self.assertIn("calendario_icono.ID_APP", dock.split("DEFECTO_FIJADOS", 1)[1].split("\n", 1)[0])

    def test_el_dock_repinta_al_cambiar_de_dia(self):
        dock = _leer(ROOT, "shell", "infinity_shell", "dock.py")
        self.assertIn("_programar_cambio_de_dia", dock)
        self.assertIn("segundos_hasta_el_cambio_de_dia", dock)
        self.assertIn("dia = calendario_icono.dia_hoy()", dock)   # el día entra en la firma de lo dibujado

    def test_el_generador_y_los_svg_coinciden(self):
        sys.path.insert(0, os.path.join(ROOT, "branding"))
        import make_calendario as M
        for dia in (1, 9, 19, 31):
            self.assertEqual(M.svg_dia(dia), _leer(CARPETA, f"dia-{dia:02d}.svg"))


if __name__ == "__main__":
    unittest.main()
