"""Sección Cuenta de Sistema en la ventana de Ajustes, con GTK 4.

Como test_launcher.py: se salta sin pantalla (DISPLAY) o sin GTK 4; corre con xvfb-run.
Nunca lanza passwd ni foot: el estado de la contraseña y la terminal se sustituyen, y las
funciones reales que harían esas llamadas fallan si la ventana llegara a invocarlas.
"""
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PANTALLA = bool(os.environ.get("DISPLAY"))

AVISO_VIVO = "En la ISO en vivo, la contraseña se pierde al apagar el equipo."


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestSeccionCuenta(unittest.TestCase):
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
        from infinity_shell import ajustes as A
        from infinity_shell import ajustes_app as X
        cls.A, cls.X, cls.Gtk = A, X, Gtk

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        parches = [
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name}),
            # Guardas: si la ventana llegara a lanzar foot o a leer passwd, la prueba falla.
            mock.patch.object(self.A, "correr_real",
                              side_effect=AssertionError("no se lanza foot en las pruebas")),
            mock.patch.object(self.A, "_correr_passwd_s",
                              side_effect=AssertionError("no se lee passwd en las pruebas")),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.cuenta{time.time_ns()}")
        self.app.register(None)

    def ventana(self, estado, en_vivo=False):
        """Ventana de Ajustes con el estado de la contraseña fingido."""
        with mock.patch.object(self.A, "leer_estado_contrasena", return_value=estado), \
                mock.patch.object(self.A, "usuario_actual", return_value="infinity"), \
                mock.patch.object(self.X, "_en_vivo", return_value=en_vivo):
            v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        return v

    def textos(self, raiz):
        """Todas las etiquetas que hay bajo `raiz`."""
        salida, pendientes = [], [raiz]
        while pendientes:
            w = pendientes.pop()
            if isinstance(w, self.Gtk.Label):
                salida.append(w.get_text())
            h = w.get_first_child()
            while h is not None:
                pendientes.append(h)
                h = h.get_next_sibling()
        return salida

    def test_cuatro_estados_muestran_su_texto(self):
        casos = (
            ("con", "Con contraseña",
             "La pantalla de bloqueo y el inicio de sesión la piden."),
            ("sin", "Sin contraseña: la pantalla de bloqueo y el inicio de sesión aceptan cualquier texto.",
             "Mientras no tenga contraseña, quien esté delante del equipo entra sin ella."),
            ("bloqueada", "Cuenta bloqueada: no entra ninguna contraseña",
             "Aquí no se puede cambiar. Un administrador puede fijar una con: sudo passwd infinity"),
            (None, "Estado desconocido",
             "No se pudo leer si la cuenta tiene contraseña. Puedes cambiarla igualmente."),
        )
        for estado, titular, ayuda in casos:
            with self.subTest(estado=estado):
                v = self.ventana(estado)
                self.assertEqual(v.cuenta_titular.get_text(), titular)
                self.assertEqual(v.cuenta_ayuda.get_text(), ayuda)
                self.assertIn("Cuenta", self.textos(v.pila.get_child_by_name("sistema")))

    def test_boton_activo_solo_si_se_puede_cambiar(self):
        for estado, activo in (("con", True), ("sin", True), ("bloqueada", False), (None, True)):
            with self.subTest(estado=estado):
                v = self.ventana(estado)
                self.assertEqual(v.boton_contrasena.get_label(), "Cambiar contraseña…")
                self.assertEqual(v.boton_contrasena.get_sensitive(), activo)

    def test_clic_llama_a_cambiar_contrasena(self):
        v = self.ventana("con")
        with mock.patch.object(self.A, "cambiar_contrasena", return_value=(True, "")) as cambiar:
            v.boton_contrasena.emit("clicked")
        cambiar.assert_called_once_with()
        self.assertIn("terminal con passwd", v.estado.get_text())

    def test_sin_terminal_lo_dice_en_el_pie(self):
        v = self.ventana("con")
        with mock.patch.object(self.A, "cambiar_contrasena",
                               return_value=(False, "No such file: 'foot'")) as cambiar:
            v.boton_contrasena.emit("clicked")
        cambiar.assert_called_once_with()
        self.assertIn("No se pudo abrir la terminal", v.estado.get_text())
        self.assertIn("error", v.estado.get_css_classes())

    def test_al_recuperar_el_foco_relee_el_estado(self):
        v = self.ventana("con")
        with mock.patch.object(self.A, "leer_estado_contrasena", return_value="bloqueada") as leer, \
                mock.patch.object(self.A, "usuario_actual", return_value="infinity"), \
                mock.patch.object(v, "is_active", return_value=False) as activo:
            v.notify("is-active")  # sin foco: no relee
            leer.assert_not_called()
            activo.return_value = True
            v.notify("is-active")  # con foco: relee
        leer.assert_called_once_with()
        self.assertEqual(v.cuenta_titular.get_text(), "Cuenta bloqueada: no entra ninguna contraseña")
        self.assertFalse(v.boton_contrasena.get_sensitive())

    def test_aviso_de_la_iso_en_vivo_solo_en_vivo(self):
        en_vivo = self.ventana("con", en_vivo=True)
        self.assertIn(AVISO_VIVO, self.textos(en_vivo.pila.get_child_by_name("sistema")))
        instalado = self.ventana("con", en_vivo=False)
        self.assertNotIn(AVISO_VIVO, self.textos(instalado.pila.get_child_by_name("sistema")))


if __name__ == "__main__":
    unittest.main()
