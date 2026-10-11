"""La burbuja de pAnkI: una esfera de cristal en el borde derecho del escritorio.

Es la hermana del logo (dock.Orbe, abajo a la izquierda): la misma esfera de 60 px y el mismo estilo
`button.orb`, con la clase `panki` para su matiz propio. Vive en el borde derecho, justo encima de la
píldora de estado (volumen, batería, red y hora), sin tapar esa píldora ni el dock:

    pantalla (de abajo arriba, en el borde derecho)
        MARGEN            aire con el borde de la pantalla
        ALTO              la píldora de estado
        HUECO             aire entre la píldora y la burbuja
        ALTO              la burbuja

Al pulsarla abre o cierra la ventana de chat (panki_app.py). Sin animaciones ni temporizadores propios:
cuesta lo mismo que el logo.
"""
import sys

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from .dock import ALTO, LOGO_ORBE, MARGEN, ORBE, _capa, asset  # noqa: E402

HUECO = 14  # aire entre la burbuja y la píldora de estado (px)
MARGEN_DERECHO = MARGEN + 4  # el mismo que el de la píldora de estado (dock.Estado)

# Abrir/cerrar la ventana de chat si la app ya está en marcha; si no, lanzarla. Listas de argumentos,
# nunca por un shell.
ALTERNAR = ["gapplication", "action", "org.infinity.Panki", "alternar"]
ABRIR = ["infinity-panki"]


def margen_inferior(margen=MARGEN, alto=ALTO, hueco=HUECO):
    """Margen entre el borde inferior de la pantalla y el de la burbuja: deja libres el margen, la
    píldora de estado (que ocupa `alto`) y un hueco de aire."""
    return margen + alto + hueco


def lanzar_proceso(argv, al_terminar=None):
    """Ejecuta `argv` sin bloquear el bucle de GTK ni usar un shell.

    Con `al_terminar`, lo llama con True si el proceso acabó bien (código 0) y con False si falló o ni
    siquiera pudo arrancar. Sin `al_terminar` no espera al proceso (para lanzar una app que sigue viva)."""
    banderas = Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE
    try:
        proceso = Gio.Subprocess.new(argv, banderas)
    except GLib.Error as e:
        print(f"infinity-shell: no se pudo ejecutar {argv[0]}: {e.message}", file=sys.stderr)
        if al_terminar is not None:
            al_terminar(False)
        return
    if al_terminar is None:
        return

    def terminado(proc, resultado):
        try:
            proc.wait_finish(resultado)
            ok = proc.get_successful()
        except GLib.Error:
            ok = False
        al_terminar(ok)

    proceso.wait_async(None, terminado)


class BurbujaPanki(Gtk.Window):
    """La marca de pAnkI en una esfera de cristal. Al pulsarla se abre o se cierra el chat."""

    def __init__(self, app, lanzar=lanzar_proceso):
        super().__init__(application=app, title="Infinity — pAnkI")
        self._lanzar = lanzar  # inyectable: las pruebas no ejecutan nada de verdad
        self._ocupada = False
        _capa(self, "infinity-panki", [LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT],
              (LayerShell.Edge.RIGHT, MARGEN_DERECHO))
        LayerShell.set_margin(self, LayerShell.Edge.BOTTOM, margen_inferior())  # _capa pone MARGEN
        boton = Gtk.Button()
        boton.add_css_class("orb")
        boton.add_css_class("panki")
        boton.set_size_request(ORBE, ORBE)
        boton.set_tooltip_text("pAnkI, tu agente virtual  (Super + N)")
        ruta = asset("panki-white.svg")
        if ruta:
            marca = Gtk.Image.new_from_file(ruta)
            marca.set_pixel_size(LOGO_ORBE)  # como el ∞ del logo dentro de la esfera
            boton.set_child(marca)
        else:
            boton.set_label("✦")
        boton.connect("clicked", self._pulsar)
        self.set_child(boton)
        self.boton = boton

    def _pulsar(self, *_):
        """Abre o cierra el chat; si la app aún no está en marcha, la lanza."""
        if self._ocupada:  # un doble clic no debe abrir y cerrar a la vez
            return
        self._ocupada = True
        try:
            self._lanzar(ALTERNAR, self._alternado)
        except Exception:
            self._ocupada = False
            raise

    def _alternado(self, ok):
        self._ocupada = False
        if not ok:  # nadie respondió en D-Bus: pAnkI no está en marcha
            self._lanzar(ABRIR)

    def actualizar_visibilidad(self, valor):
        """Muestra u oculta la burbuja según el ajuste `panki.burbuja`. None (el ajuste aún no existe)
        cuenta como activado. Devuelve si queda visible."""
        visible = True if valor is None else bool(valor)
        if not visible:
            self.set_visible(False)
        elif not self.get_visible():
            self.present()
        return visible
