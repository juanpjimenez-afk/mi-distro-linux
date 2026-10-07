import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infinity_shell.search import calcular, normalizar, puntuar  # noqa: E402


class TestNormalizar(unittest.TestCase):
    def test_tildes_y_mayusculas(self):
        self.assertEqual(normalizar("Cámara  "), "camara")
        self.assertEqual(normalizar("ÁÉÍÓÚ ñ"), "aeiou n")

    def test_none(self):
        self.assertEqual(normalizar(None), "")


class TestPuntuar(unittest.TestCase):
    def test_orden(self):
        exacto = puntuar("foot", "foot")
        prefijo = puntuar("fo", "foot")
        palabra = puntuar("fox", "mozilla fox")
        contiene = puntuar("oot", "foot")
        difuso = puntuar("ffx", "firefox")
        self.assertGreater(exacto, prefijo)
        self.assertGreater(prefijo, palabra)
        self.assertGreater(palabra, contiene)
        self.assertGreater(contiene, difuso)
        self.assertGreater(difuso, 0)

    def test_sin_coincidencia(self):
        self.assertEqual(puntuar("zzz", "firefox"), 0)
        self.assertEqual(puntuar("", "firefox"), 0)

    def test_una_letra_solo_prefijo_o_palabra(self):
        self.assertGreater(puntuar("f", "firefox"), 0)
        self.assertEqual(puntuar("x", "firefox") > 0, True)  # contiene
        self.assertEqual(puntuar("q", "firefox"), 0)


class TestDifuso(unittest.TestCase):
    def test_no_hay_falsos_positivos_en_textos_largos(self):
        largo = "shell prompt command commandline cmd terminal emulator wayland"
        self.assertEqual(puntuar("alc", largo, difuso=False), 0)
        self.assertEqual(puntuar("alc", largo), 0)  # más de 40 caracteres: tampoco

    def test_difuso_solo_con_dos_o_mas_letras(self):
        self.assertGreater(puntuar("ffx", "firefox"), 0)
        self.assertEqual(puntuar("fz", "firefox"), 0)


class TestCalcular(unittest.TestCase):
    def test_basico(self):
        self.assertEqual(calcular("2+2"), "4")
        self.assertEqual(calcular("2 + 3 * 4"), "14")
        self.assertEqual(calcular("(2+3)*4"), "20")
        self.assertEqual(calcular("2^10"), "1024")
        self.assertEqual(calcular("10/4"), "2.5")
        self.assertEqual(calcular("7 % 3"), "1")

    def test_coma_decimal_y_signos(self):
        self.assertEqual(calcular("1,5*2"), "3")
        self.assertEqual(calcular("3×4"), "12")
        self.assertEqual(calcular("9÷3"), "3")
        self.assertEqual(calcular("-5+2"), "-3")

    def test_no_es_operacion(self):
        self.assertIsNone(calcular("42"))
        self.assertIsNone(calcular("firefox"))
        self.assertIsNone(calcular(""))
        self.assertIsNone(calcular("-5"))

    def test_seguridad(self):
        self.assertIsNone(calcular("__import__('os')"))
        self.assertIsNone(calcular("1/0"))
        self.assertIsNone(calcular("9**9**9"))
        self.assertIsNone(calcular("2^100000"))
        self.assertIsNone(calcular("(" * 500 + "1+1" + ")" * 500))


if __name__ == "__main__":
    unittest.main()
