"""Ventana de Internet y Bluetooth con órdenes falsas (nunca toca la red). Se salta sin pantalla; corre con xvfb-run."""
import os
import sys
import time
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")


@unittest.skipUnless(os.environ.get("DISPLAY"), "necesita una pantalla (xvfb-run)")
class TestVentanaRedes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            from gi.repository import GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        sys.path.insert(0, SHELL)
        from infinity_shell import bluetooth_logica as BT
        from infinity_shell import redes_app as X
        cls.X, cls.Gtk, cls.GLib, cls.BT = X, Gtk, GLib, BT

    def setUp(self):
        self.llamadas = []
        self.antes = self.BT.hay_adaptador
        self.BT.hay_adaptador = lambda *a, **k: True
        self.addCleanup(setattr, self.BT, "hay_adaptador", self.antes)

    def falso(self, argv, entrada=None, timeout=25):
        self.llamadas.append((list(argv), entrada))
        a = " ".join(argv)
        if "TYPE,STATE" in a:
            return 0, "ethernet:unavailable\nwifi:disconnected"
        if a.startswith("nmcli radio wifi"):
            return 0, "enabled"
        if "NAME,TYPE" in a:
            return 0, ""
        if "wifi list" in a:
            return 0, " :Casa 5G:78:WPA2\n :Libre:55:\n"
        if argv[:2] == ["bluetoothctl", "show"]:
            return 0, "Powered: yes"
        return 0, ""

    def bombear(self, segundos=0.8):
        ctx = self.GLib.MainContext.default()
        fin = time.time() + segundos
        while time.time() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.01)

    def abrir(self):
        app = self.Gtk.Application(application_id="org.infinity.RedesPrueba")
        app.register(None)
        v = self.X.VentanaRedes(app, correr=self.falso)
        self.addCleanup(v.destroy)
        self.bombear()
        return v

    def test_lista_las_redes(self):
        v = self.abrir()
        nombres = [r.ssid for r in v._redes]
        self.assertEqual(nombres, ["Casa 5G", "Libre"])
        self.assertIn("Sin conexión", v.estado_red.get_text())

    def test_red_protegida_pide_contrasena_y_va_por_stdin(self):
        v = self.abrir()
        v.lista.select_row(v.lista.get_row_at_index(0))
        self.assertTrue(v.clave.get_visible())
        v.clave.set_text("corta")
        v._conectar()
        self.assertIn("8 caracteres", v.mensaje_red.get_text())          # no llega a lanzar nada
        v.clave.set_text("secretisima99")
        v._conectar()
        self.bombear()
        orden, entrada = next(l for l in self.llamadas if "--ask" in l[0])
        self.assertNotIn("secretisima99", " ".join(orden))
        self.assertEqual(entrada, "secretisima99\n")

    def test_red_abierta_no_pide_contrasena(self):
        v = self.abrir()
        v.lista.select_row(v.lista.get_row_at_index(1))
        self.assertFalse(v.clave.get_visible())

    def test_sin_bluetooth_se_oculta(self):
        self.BT.hay_adaptador = lambda *a, **k: False
        v = self.abrir()
        self.assertFalse(v.boton_buscar.get_visible())
        self.assertIn("no tiene Bluetooth", v.estado_bt.get_text())


if __name__ == "__main__":
    unittest.main()
