import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.dirname(__file__))

try:
    import gi
    from gi.repository import GLib
except ImportError:  # sin PyGObject no hay nada que probar
    gi = None

from fake_hyprland import FakeHyprland  # noqa: E402


@unittest.skipIf(gi is None, "PyGObject no disponible")
class TestHypr(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeHyprland(self.tmp.name).start()
        os.environ["XDG_RUNTIME_DIR"] = self.tmp.name
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = self.fake.firma
        from infinity_shell.hypr import Hyprland
        self.hypr = Hyprland()

    def tearDown(self):
        self.fake.stop()
        self.tmp.cleanup()

    def test_disponible_y_ventanas_ordenadas(self):
        self.assertTrue(self.hypr.disponible)
        self.assertEqual(self.hypr.ventanas(), [])
        self.fake.abrir("0xaaa", "foot")
        self.fake.abrir("0xbbb", "org.gnome.Epiphany")
        v = self.hypr.ventanas()
        self.assertEqual([w["address"] for w in v], ["0xbbb", "0xaaa"])  # la más reciente primero
        self.assertEqual(self.hypr.ventana_activa(), "0xbbb")

    def test_ventanas_ocultas_se_ignoran(self):
        self.fake.abrir("0xaaa", "foot")
        self.fake.clientes[0]["hidden"] = True
        self.assertEqual(self.hypr.ventanas(), [])

    def test_despachar(self):
        self.assertEqual(self.hypr.despachar("focuswindow", "address:0xaaa"), "ok")
        self.assertEqual(self.fake.despachos, ["focuswindow address:0xaaa"])

    def test_eventos(self):
        recibidos = []
        loop = GLib.MainLoop()
        self.assertTrue(self.hypr.escuchar(lambda n, d: recibidos.append((n, d))))
        GLib.timeout_add(100, lambda: (self.fake.abrir("0xccc", "foot"), False)[1])
        GLib.timeout_add(600, loop.quit)
        loop.run()
        nombres = [n for n, _ in recibidos]
        self.assertIn("openwindow", nombres)
        self.assertIn(("activewindowv2", "ccc"), recibidos)

    def test_estado_async_no_bloquea_y_entrega_el_resultado(self):
        self.fake.abrir("0xaaa", "foot")
        self.fake.abrir("0xbbb", "thunar")
        resultado = []
        loop = GLib.MainLoop()

        def listo(ventanas, activa):
            resultado.append(([v["class"] for v in ventanas], activa))
            loop.quit()

        self.hypr.estado_async(listo)
        GLib.timeout_add(3000, loop.quit)  # red de seguridad
        loop.run()
        self.assertEqual(resultado, [(["thunar", "foot"], "0xbbb")])

    def test_sin_hyprland(self):
        os.environ.pop("HYPRLAND_INSTANCE_SIGNATURE")
        from infinity_shell.hypr import Hyprland
        h = Hyprland()
        self.assertFalse(h.disponible)
        self.assertEqual(h.ventanas(), [])
        self.assertEqual(h.pedir("j/clients"), "")


if __name__ == "__main__":
    unittest.main()
