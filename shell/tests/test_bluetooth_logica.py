"""Bluetooth de la píldora de estado: detección del adaptador e icono."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import bluetooth_logica as B  # noqa: E402

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


class TestBluetooth(unittest.TestCase):
    def test_sin_carpeta_o_vacia_no_hay_adaptador(self):
        with tempfile.TemporaryDirectory() as d:
            self.assertFalse(B.hay_adaptador(os.path.join(d, "no-existe")))
            self.assertFalse(B.hay_adaptador(d))

    def test_con_hci0_si_y_las_subentradas_no_cuentan(self):
        with tempfile.TemporaryDirectory() as d:
            os.mkdir(os.path.join(d, "hci0:256"))      # conexión, no adaptador
            self.assertFalse(B.hay_adaptador(d))
            os.mkdir(os.path.join(d, "hci0"))
            self.assertTrue(B.hay_adaptador(d))

    def test_icono(self):
        self.assertEqual(B.icono(), "bluetooth-active-symbolic")
        self.assertEqual(B.icono(lambda n: n == "bluetooth-symbolic"), "bluetooth-symbolic")
        self.assertEqual(B.icono(lambda n: False), "bluetooth-symbolic")

    def test_la_iso_trae_bluez_y_lo_activa(self):
        with open(os.path.join(RAIZ, "build", "packages.list")) as f:
            paquetes = [l.split("#")[0].strip() for l in f]
        self.assertIn("bluez", paquetes)
        with open(os.path.join(RAIZ, "build", "chroot-setup.sh")) as f:
            self.assertIn("systemctl enable bluetooth.service", f.read())


if __name__ == "__main__":
    unittest.main()
