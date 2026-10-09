"""Marca del escritorio: el ∞ grande y casi transparente sobre el fondo de color.

Solo aparece con el fondo de color (ver overlay/usr/libexec/infinity/fondo-modo): sobre la cascada
no hace falta. Es una superficie pequeña y centrada, así que apenas cuesta CPU; la cascada, a
pantalla completa, sí cuesta mucho.
"""
import subprocess

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from .dock import asset  # noqa: E402

FONDO_MODO = "/usr/libexec/infinity/fondo-modo"
TAM = 560       # lado de la marca (px)
OPACIDAD = 0.07  # casi invisible: solo se intuye sobre el color


def fondo_modo():
    """"color" o "imagen". Si no se puede leer, se asume la cascada (sin marca)."""
    try:
        salida = subprocess.run([FONDO_MODO], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return "imagen"
    return salida.strip() or "imagen"


class Marca(Gtk.Window):
    def __init__(self, app):
        super().__init__(application=app, title="Infinity — Marca")
        LayerShell.init_for_window(self)
        # Sin "infinity-" en el nombre: la regla de desenfoque de Hyprland no debe aplicarse aquí.
        LayerShell.set_namespace(self, "marca-fondo")
        LayerShell.set_layer(self, LayerShell.Layer.BACKGROUND)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)
        LayerShell.set_exclusive_zone(self, -1)  # sin anclajes: el compositor la centra
        self.add_css_class("infinity-window")  # ventana transparente, sin el fondo del tema
        ruta = asset("logo-white.svg")
        if ruta:
            imagen = Gtk.Image.new_from_file(ruta)
            imagen.set_pixel_size(TAM)
            imagen.set_opacity(OPACIDAD)
            self.set_child(imagen)
        self.set_default_size(TAM, TAM)
