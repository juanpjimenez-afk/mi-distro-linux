"""Contraseña de la cuenta desde Ajustes, sin GTK (corre en CI).

Lo que no se puede probar aquí (la terminal foot y passwd de verdad) se comprueba en la VM.
"""
import inspect
import os
import pwd
import subprocess
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import ajustes as A  # noqa: E402

# Salidas reales de `passwd -S` sin argumentos, como usuario normal (shadow 4.13)
CON = "testcontr P 2026-10-10 0 99999 7 -1\n"
SIN = "testcontr NP 2026-10-10 0 99999 7 -1\n"
BLOQUEADA = "testcontr L 2026-10-10 0 99999 7 -1\n"


class TestParserPasswdS(unittest.TestCase):
    def test_con_contrasena(self):
        self.assertEqual(A.parsear_passwd_s(CON), "con")

    def test_sin_contrasena(self):
        self.assertEqual(A.parsear_passwd_s(SIN), "sin")

    def test_bloqueada(self):
        self.assertEqual(A.parsear_passwd_s(BLOQUEADA), "bloqueada")

    def test_otros_formatos_de_fecha(self):
        # shadow antiguo escribe MM/DD/AAAA; con la fecha 0 aparece 1970-01-01
        self.assertEqual(A.parsear_passwd_s("ana P 10/10/2026 0 99999 7 -1"), "con")
        self.assertEqual(A.parsear_passwd_s("ana NP 1970-01-01 0 99999 7 -1"), "sin")

    def test_salida_inesperada_da_none(self):
        muestras = [
            None, "", "\n",
            "passwd: You may not view or modify password information for root.",
            "passwd: user 'nadie' does not exist",
            "passwd: Permission denied",
            "testcontr X 2026-10-10 0 99999 7 -1",        # estado que no existe
            "testcontr p 2026-10-10 0 99999 7 -1",        # en minúsculas no vale
            "testcontr P 2026-10-10 0 99999 7",           # falta un campo
            "testcontr P 2026-10-10 0 99999 7 -1 extra",  # sobra un campo
            "testcontr P 2026-10-10 cero 99999 7 -1",
            "testcontr P 2026-10-10 0 99999 7 -1\ntestcontr NP 2026-10-10 0 99999 7 -1",
        ]
        for muestra in muestras:
            with self.subTest(salida=muestra):
                self.assertIsNone(A.parsear_passwd_s(muestra))


class TestLeerEstado(unittest.TestCase):
    def test_pide_passwd_s_sin_usuario(self):
        visto = []

        def falso(argv):
            visto.append(argv)
            return 0, CON

        self.assertEqual(A.leer_estado_contrasena(falso), "con")
        self.assertEqual(visto, [["passwd", "-S"]])

    def test_por_defecto_usa_subprocess_run_sin_shell(self):
        hecho = subprocess.CompletedProcess(["passwd", "-S"], 0, stdout=SIN, stderr="")
        with mock.patch.object(A.subprocess, "run", return_value=hecho) as run:
            self.assertEqual(A.leer_estado_contrasena(), "sin")
        self.assertEqual(run.call_args.args[0], ["passwd", "-S"])
        self.assertFalse(run.call_args.kwargs.get("shell", False))

    def test_codigo_distinto_de_cero_es_desconocido(self):
        self.assertIsNone(A.leer_estado_contrasena(lambda argv: (1, CON)))

    def test_sin_passwd_o_con_tiempo_agotado_es_desconocido(self):
        def sin_passwd(argv):
            raise FileNotFoundError("passwd")

        def colgado(argv):
            raise subprocess.TimeoutExpired(argv, 5)

        self.assertIsNone(A.leer_estado_contrasena(sin_passwd))
        self.assertIsNone(A.leer_estado_contrasena(colgado))
        with mock.patch.object(A.subprocess, "run", side_effect=OSError("sin permisos")):
            self.assertIsNone(A.leer_estado_contrasena())


class TestResumen(unittest.TestCase):
    def test_textos_de_la_seccion(self):
        self.assertEqual(A.resumen_contrasena("con", "infinity")[0], "Con contraseña")
        titular, _, se_puede = A.resumen_contrasena("sin", "infinity")
        self.assertEqual(titular, "Sin contraseña: el bloqueo acepta Enter")
        self.assertTrue(se_puede)

    def test_bloqueada_no_deja_cambiar_y_dice_quien_puede(self):
        _, ayuda, se_puede = A.resumen_contrasena("bloqueada", "infinity")
        self.assertFalse(se_puede)
        self.assertIn("sudo passwd infinity", ayuda)

    def test_desconocido_lo_dice_y_deja_cambiar(self):
        titular, ayuda, se_puede = A.resumen_contrasena(None, "infinity")
        self.assertEqual(titular, "Estado desconocido")
        self.assertTrue(ayuda)
        self.assertTrue(se_puede)

    def test_usuario_actual(self):
        self.assertEqual(A.usuario_actual(), pwd.getpwuid(os.getuid()).pw_name)


class TestCambiarContrasena(unittest.TestCase):
    ARGV = ["foot", "--hold", "--title", "Cambiar contraseña", "passwd"]

    def test_argv_fijo_sin_shell_y_sin_nada_interpolado(self):
        self.assertEqual(A.argv_cambiar_contrasena(), self.ARGV)

    def test_popen_recibe_una_lista_sin_shell(self):
        with mock.patch.object(A.subprocess, "Popen") as popen:
            ok, error = A.cambiar_contrasena()
        self.assertEqual((ok, error), (True, ""))
        popen.assert_called_once()
        argv = popen.call_args.args[0]
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(x, str) for x in argv))
        self.assertEqual(argv, self.ARGV)
        self.assertFalse(popen.call_args.kwargs.get("shell", False))
        self.assertNotIn(argv[0], ("sh", "bash", "zsh"))
        self.assertNotIn("-c", argv)
        self.assertTrue(popen.call_args.kwargs.get("start_new_session"))

    def test_sin_foot_lo_dice(self):
        with mock.patch.object(A.subprocess, "Popen", side_effect=FileNotFoundError("foot")):
            ok, error = A.cambiar_contrasena()
        self.assertFalse(ok)
        self.assertIn("foot", error)

    def test_ninguna_contrasena_entra_en_la_funcion(self):
        # Solo admite el inyector de pruebas: no hay parámetro para una contraseña.
        self.assertEqual(list(inspect.signature(A.cambiar_contrasena).parameters), ["correr"])


if __name__ == "__main__":
    unittest.main()
