"""Infinity Shell: orbe + dock + estado + buscador, en un único proceso ligero.

    infinity-shell                    inicia el shell
    infinity-shell --toggle-launcher  abre/cierra el buscador (en la instancia ya iniciada)
    infinity-shell --refresh          refresca volumen/batería/red (para atajos de teclado)
"""
import os
import sys

# El lanzador precarga gtk4-layer-shell con LD_PRELOAD; ya cumplió su función, así que se
# quita para que las aplicaciones que abras desde el shell no la hereden.
_pre = [p for p in os.environ.get("LD_PRELOAD", "").split(":") if p and "gtk4-layer-shell" not in p]
if _pre:
    os.environ["LD_PRELOAD"] = ":".join(_pre)
else:
    os.environ.pop("LD_PRELOAD", None)

import gi  # noqa: E402

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell  # noqa: E402,F401

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, Gtk  # noqa: E402

from .apps import Indice  # noqa: E402
from .dock import Dock, Estado, Orbe  # noqa: E402
from .hypr import Hyprland  # noqa: E402
from .launcher import Launcher  # noqa: E402

APP_ID = "org.infinity.Shell"
CSS = os.path.join(os.path.dirname(__file__), "style.css")


class Shell(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.indice = None
        self.hypr = None
        self.dock = self.orbe = self.estado = None
        self.launcher = None  # se crea la primera vez que se usa (ahorra RAM)

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        proveedor = Gtk.CssProvider()
        proveedor.connect("parsing-error", lambda _p, sec, err: print(
            f"infinity-shell: CSS línea {sec.get_start_location().lines + 1}: {err.message}", file=sys.stderr))
        proveedor.load_from_path(CSS)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def do_command_line(self, linea):
        args = linea.get_arguments()[1:]
        if self.dock is None:
            self._crear_interfaz()
        if "--toggle-launcher" in args:
            self._launcher().alternar()
        if "--refresh" in args:
            self.estado.refrescar()
        return 0

    def _crear_interfaz(self):
        self.indice = Indice()
        self.hypr = Hyprland()
        self.dock = Dock(self, self.indice, self.hypr)
        self.orbe = Orbe(self, lambda: self._launcher().alternar())
        self.estado = Estado(self)
        for ventana in (self.dock, self.orbe, self.estado):
            ventana.present()

    def _launcher(self):
        if self.launcher is None:
            self.launcher = Launcher(self, self.indice)
        return self.launcher


def main():
    return Shell().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
