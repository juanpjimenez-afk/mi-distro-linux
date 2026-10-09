"""Infinity Shell: orbe + dock + estado + buscador, en un único proceso ligero.

    infinity-shell                    inicia el shell
    infinity-shell --toggle-launcher  abre/cierra el buscador (en la instancia ya iniciada)
    infinity-shell --refresh          refresca volumen/batería/red (para atajos de teclado)
    infinity-shell --toggle-biblioteca  abre/cierra la biblioteca de apps

Para atajos de teclado es MUCHO más rápido hablar con la instancia ya abierta por D-Bus
(no hay que arrancar Python ni cargar GTK):

    gapplication action org.infinity.Shell toggle-launcher
    gapplication action org.infinity.Shell refresh
    gapplication action org.infinity.Shell recargar   (tras cambiar algo en Ajustes)
"""
import os
import sys

# earlyoom (/etc/default/earlyoom) compara el nombre del proceso (comm), no la ruta. El lanzador
# ejecuta "python3", asi que sin esto el shell no se llamaria "infinity-shell" y no tendria la
# proteccion frente al OOM. PR_SET_NAME = 15; el nombre no puede pasar de 15 bytes.
try:
    import ctypes
    _libc = ctypes.CDLL(None)
    _libc.prctl.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong]
    _libc.prctl(15, b"infinity-shell", 0, 0, 0)
except (OSError, AttributeError):
    pass

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

from .ajustes import dir_config, valor as ajuste  # noqa: E402
from .apps import Indice  # noqa: E402
from .biblioteca import Biblioteca  # noqa: E402
from .biblioteca_ventana import VentanaBiblioteca  # noqa: E402
from .dock import Dock, Estado, Orbe  # noqa: E402
from .hypr import Hyprland  # noqa: E402
from .launcher import Launcher  # noqa: E402
from .marca import Marca, fondo_modo  # noqa: E402

APP_ID = "org.infinity.Shell"
CSS = os.path.join(os.path.dirname(__file__), "style.css")


class Shell(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.indice = None
        self.hypr = None
        self.dock = self.orbe = self.estado = None
        self.launcher = None  # se crea la primera vez que se usa (ahorra RAM)
        self.biblioteca_ventana = None  # idem
        self.marca = None  # solo existe con el fondo de color y la marca activada
        self._proveedor_usuario = None

    def do_startup(self):
        Gtk.Application.do_startup(self)
        Gtk.Settings.get_default().set_property("gtk-application-prefer-dark-theme", True)
        display = Gdk.Display.get_default()
        proveedor = Gtk.CssProvider()
        proveedor.connect("parsing-error", self._error_css)
        proveedor.load_from_path(CSS)
        Gtk.StyleContext.add_provider_for_display(display, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        # Estilo del usuario (lo escribe la app de Ajustes): va por encima del estilo del sistema
        self._proveedor_usuario = Gtk.CssProvider()
        self._proveedor_usuario.connect("parsing-error", self._error_css)
        Gtk.StyleContext.add_provider_for_display(display, self._proveedor_usuario,
                                                  Gtk.STYLE_PROVIDER_PRIORITY_USER)
        self._recargar_estilo_usuario()

        # Acciones expuestas por D-Bus (las usan los atajos de teclado y la app de Ajustes)
        acciones = (("toggle-launcher", self._accion_buscador), ("refresh", self._accion_refrescar),
                    ("toggle-biblioteca", self._accion_biblioteca), ("recargar", self._accion_recargar))
        for nombre, funcion in acciones:
            accion = Gio.SimpleAction.new(nombre, None)
            accion.connect("activate", funcion)
            self.add_action(accion)

    @staticmethod
    def _error_css(_proveedor, seccion, error):
        print(f"infinity-shell: CSS línea {seccion.get_start_location().lines + 1}: {error.message}",
              file=sys.stderr)

    def _recargar_estilo_usuario(self):
        ruta = os.path.join(dir_config(), "infinity", "estilo.css")
        if os.path.exists(ruta):
            self._proveedor_usuario.load_from_path(ruta)
        else:
            self._proveedor_usuario.load_from_string("")

    def _sincronizar_marca(self):
        """La marca ∞ se ve solo con el fondo de color y si está activada en Ajustes."""
        quiere = fondo_modo() == "color" and ajuste("escritorio.marca")
        if quiere and self.marca is None:
            self.marca = Marca(self)
            self.marca.present()
        elif not quiere and self.marca is not None:
            self.marca.destroy()
            self.marca = None

    def _accion_recargar(self, *_):
        """Relee lo que la app de Ajustes cambia en el shell: estilo, marca y formato de la hora."""
        if self.dock is None:
            return
        self._recargar_estilo_usuario()
        self._sincronizar_marca()
        self.estado.recargar_reloj()

    def _accion_buscador(self, *_):
        if self.dock is not None:
            self._launcher().alternar()

    def _accion_biblioteca(self, *_):
        if self.dock is not None:
            self._biblioteca().alternar()

    def _accion_refrescar(self, *_):
        if self.estado is not None:
            self.estado.refrescar()

    def do_command_line(self, linea):
        args = linea.get_arguments()[1:]
        if self.dock is None:
            self._crear_interfaz()
        if "--toggle-launcher" in args:
            self._launcher().alternar()
        if "--toggle-biblioteca" in args:
            self._biblioteca().alternar()
        if "--refresh" in args:
            self.estado.refrescar()
        return 0

    def _crear_interfaz(self):
        self.indice = Indice()
        self.hypr = Hyprland()
        self.dock = Dock(self, self.indice, self.hypr, lambda: self._biblioteca().alternar())
        self.orbe = Orbe(self, lambda: self._launcher().alternar())
        self.estado = Estado(self)
        for ventana in (self.dock, self.orbe, self.estado):
            ventana.present()
        self._sincronizar_marca()  # con el fondo de color, la marca ∞ se ve en el centro (marca.py)

    def _launcher(self):
        if self.launcher is None:
            self.launcher = Launcher(self, self.indice)
        return self.launcher

    def _biblioteca(self):
        if self.biblioteca_ventana is None:
            self.biblioteca_ventana = VentanaBiblioteca(self, self.indice, Biblioteca.cargar())
        return self.biblioteca_ventana


def main():
    return Shell().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
