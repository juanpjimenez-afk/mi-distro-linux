"""Controles de ventana: una pastilla de cristal con − (minimizar), □ (maximizar/restaurar) y × (cerrar)
junto a cada ventana normal.

Hyprland no dibuja barras de título ni botones, y GTK4 solo pinta la ×; las apps GTK3, foot y Tk (Juegos, por
XWayland) no pintan nada. Por eso los controles los dibuja el shell, iguales para todas las ventanas:

    ┌────────────────┐
    │  [−] [□] [×]   │   <- pastilla (capa TOP de layer-shell, namespace infinity-controles)
    └────────────────┘
    ┌────────────────────────────────┐
    │  la ventana                    │

Una superficie pequeña por ventana, anclada arriba a la izquierda con los márgenes calculados desde la
geometría de la ventana (controles_logica.py: qué ventanas llevan pastilla y dónde va). Hyprland no avisa
cuando una ventana se arrastra o se redimensiona, así que mientras haya pastillas visibles se consulta la
geometría unas 4 veces por segundo (una petición ligera por el socket), y además se reacciona a los eventos
de Hyprland (abrir, cerrar, cambiar de espacio de trabajo...). Solo se mueve una pastilla si su sitio cambió;
al arrastrar una ventana, la pastilla la sigue con un retraso de hasta ~250 ms.
"""
import threading

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import controles_logica as L  # noqa: E402

NAMESPACE = "infinity-controles"  # lo casa la regla de desenfoque `^infinity-.*$` de hyprland.conf
INTERVALO_MS = 250  # sondeo de la geometría mientras haya pastillas visibles (~4 Hz)
INTERVALO_SIN_EVENTOS_MS = 1000  # si el socket de eventos no está disponible, se mira más despacio, siempre


class Icono(Gtk.Widget):
    """El símbolo de un botón (−, □, ❐, ×), trazado con Gsk (GTK 4.14 o más): no depende del tema de iconos, de
    la tipografía ni de pycairo. Toma el color del CSS (`color`), así que un tema puede cambiarlo."""

    __gtype_name__ = "InfinityControlIcono"
    LADO = 12  # px del cuadro donde se dibuja
    GROSOR = 1.6

    def __init__(self, tipo):
        super().__init__()
        self.tipo = tipo  # "minimizar" | "maximizar" | "restaurar" | "cerrar"
        self.set_size_request(self.LADO, self.LADO)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)

    def poner_tipo(self, tipo):
        if tipo != self.tipo:
            self.tipo = tipo
            self.queue_draw()

    def do_snapshot(self, snapshot):
        c, r = self.get_width() / 2, 4.0  # centro y semilado del símbolo
        pb = Gsk.PathBuilder.new()
        if self.tipo == "minimizar":
            pb.move_to(c - r, c + 1.5)
            pb.line_to(c + r, c + 1.5)
        elif self.tipo == "maximizar":
            _cuadrado(pb, c - r, c - r, 2 * r, 2 * r)
        elif self.tipo == "restaurar":  # dos cuadrados superpuestos: el de delante entero, el de atrás solo su "L"
            _cuadrado(pb, c - r, c - r + 2, 2 * r - 2, 2 * r - 2)
            pb.move_to(c - r + 2, c - r + 2)
            pb.line_to(c - r + 2, c - r)
            pb.line_to(c + r, c - r)
            pb.line_to(c + r, c + r - 2)
            pb.line_to(c + r - 2, c + r - 2)
        else:  # cerrar
            pb.move_to(c - r + 0.5, c - r + 0.5)
            pb.line_to(c + r - 0.5, c + r - 0.5)
            pb.move_to(c + r - 0.5, c - r + 0.5)
            pb.line_to(c - r + 0.5, c + r - 0.5)
        trazo = Gsk.Stroke.new(self.GROSOR)
        trazo.set_line_cap(Gsk.LineCap.ROUND)
        trazo.set_line_join(Gsk.LineJoin.ROUND)
        snapshot.append_stroke(pb.to_path(), trazo, self.get_color())


def _cuadrado(pb, x, y, w, h, radio=1.5):
    """Cuadrado de esquinas suaves (como el resto de la interfaz)."""
    caja = Gsk.RoundedRect()
    caja.init_from_rect(Graphene.Rect().init(x, y, w, h), radio)
    pb.add_rounded_rect(caja)


class Pastilla(Gtk.Window):
    """Los tres botones de UNA ventana de Hyprland (identificada por su dirección)."""

    def __init__(self, app, direccion, al_pulsar):
        super().__init__(application=app, title="Infinity — Controles")
        self.direccion = direccion
        self.sitio = None  # (nombre del monitor, margen superior, margen izquierdo) ya aplicado
        self._monitor_gdk = None
        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, NAMESPACE)
        LayerShell.set_layer(self, LayerShell.Layer.TOP)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.NONE)  # jamás roba el foco del teclado
        LayerShell.set_anchor(self, LayerShell.Edge.TOP, True)
        LayerShell.set_anchor(self, LayerShell.Edge.LEFT, True)
        LayerShell.set_exclusive_zone(self, -1)  # ignora lo que reservan el dock y demás
        self.add_css_class("infinity-window")

        caja = Gtk.Box(spacing=L.ESPACIO)
        caja.add_css_class("glass")
        caja.add_css_class("ctl")
        caja.set_size_request(L.ANCHO, L.ALTO)
        self.botones = {}
        for tipo, ayuda in (("minimizar", "Minimizar"), ("maximizar", "Maximizar"), ("cerrar", "Cerrar")):
            b = Gtk.Button()
            b.add_css_class("ctl-btn")
            b.add_css_class("ctl-cerrar" if tipo == "cerrar" else "ctl-" + tipo)
            b.set_size_request(L.BOTON, L.BOTON)
            b.set_child(Icono(tipo))
            b.set_focusable(False)  # el clic no necesita foco y no queremos el aro de foco
            b.set_tooltip_text(ayuda)
            b.update_property([Gtk.AccessibleProperty.LABEL], [ayuda])
            b.connect("clicked", lambda _b, t=tipo: al_pulsar(t, self.direccion))
            caja.append(b)
            self.botones[tipo] = b
        self.set_child(caja)
        self.caja = caja
        self.maximizada = False

    def poner_maximizada(self, maximizada):
        """El botón del medio es □ (maximizar) o ❐ (restaurar) según el estado de la ventana."""
        if maximizada == self.maximizada:
            return
        self.maximizada = maximizada
        medio = self.botones["maximizar"]
        medio.get_child().poner_tipo("restaurar" if maximizada else "maximizar")
        ayuda = "Restaurar" if maximizada else "Maximizar"
        medio.set_tooltip_text(ayuda)
        medio.update_property([Gtk.AccessibleProperty.LABEL], [ayuda])

    def colocar(self, plan, monitor_gdk=None):
        """Aplica los márgenes del plan. Si no cambió nada desde la última vez, no toca la superficie."""
        sitio = (plan.nombre_monitor, plan.arriba, plan.izquierda)
        if monitor_gdk is not None and monitor_gdk is not self._monitor_gdk:
            self._monitor_gdk = monitor_gdk
            LayerShell.set_monitor(self, monitor_gdk)
        if sitio != self.sitio:
            self.sitio = sitio
            LayerShell.set_margin(self, LayerShell.Edge.TOP, plan.arriba)
            LayerShell.set_margin(self, LayerShell.Edge.LEFT, plan.izquierda)
        self.poner_maximizada(plan.maximizada)
        if not self.get_visible():
            self.present()

    def ocultar(self):
        if self.get_visible():
            self.set_visible(False)


def monitor_gdk(nombre):
    """El Gdk.Monitor cuyo conector se llama `nombre` (el que da Hyprland), o None."""
    display = Gdk.Display.get_default()
    if display is None or not nombre:
        return None
    monitores = display.get_monitors()
    for i in range(monitores.get_n_items()):
        m = monitores.get_item(i)
        if m.get_connector() == nombre:
            return m
    return None


class Controles:
    """Crea, mueve y quita las pastillas siguiendo a las ventanas de Hyprland."""

    def __init__(self, app, hypr, fabrica=Pastilla, buscar_monitor=monitor_gdk):
        self.app = app
        self.hypr = hypr
        self._fabrica = fabrica
        self._buscar_monitor = buscar_monitor
        self.pastillas = {}  # dirección -> Pastilla
        self._temporizador = 0
        self._pendiente = 0
        self._en_curso = False
        self._repetir = False
        self._con_eventos = False
        self._cerrado = False
        if hypr.disponible:
            self._con_eventos = bool(hypr.escuchar(self._evento))
            if not self._con_eventos:
                self._poner_temporizador(INTERVALO_SIN_EVENTOS_MS)
            self.refrescar()

    # ----------------------------------------------------------------- consultas
    def _evento(self, nombre, _datos):
        if nombre in L.EVENTOS and not self._cerrado:
            self.refrescar_pronto()

    def refrescar_pronto(self):
        # Agrupa ráfagas de eventos en una sola consulta (igual que el dock).
        if not self._pendiente:
            self._pendiente = GLib.timeout_add(60, self._refrescar_ahora)

    def _refrescar_ahora(self):
        self._pendiente = 0
        self.refrescar()
        return False

    def refrescar(self):
        """Pide ventanas y monitores a Hyprland en un hilo y aplica el resultado en el principal."""
        if not self.hypr.disponible:
            return
        if self._en_curso:
            self._repetir = True  # llegó otro aviso mientras consultábamos: se repite al terminar
            return
        self._en_curso = True

        def trabajo():
            try:
                clientes = self.hypr.json("clients", []) or []
                monitores = self.hypr.json("monitors", []) or []
            except Exception:  # noqa: BLE001  (la interfaz debe seguir aunque la consulta falle)
                clientes, monitores = [], []
            GLib.idle_add(self._recibido, clientes, monitores)

        threading.Thread(target=trabajo, daemon=True).start()

    def _recibido(self, clientes, monitores):
        self._en_curso = False
        if self._cerrado:
            return False
        try:
            self.aplicar(clientes, monitores)
        finally:
            if self._repetir:
                self._repetir = False
                self.refrescar()
        return False

    # ----------------------------------------------------------------- sondeo
    def _poner_temporizador(self, ms):
        if not self._temporizador:
            self._temporizador = GLib.timeout_add(ms, self._tic)

    def _quitar_temporizador(self):
        if self._temporizador:
            GLib.source_remove(self._temporizador)
            self._temporizador = 0

    def _tic(self):
        self.refrescar()
        return True

    def _ajustar_temporizador(self):
        """El sondeo corre solo si hay pastillas visibles; sin ventanas, solo despiertan los eventos."""
        if self._con_eventos:
            if any(p.get_visible() for p in self.pastillas.values()):
                self._poner_temporizador(INTERVALO_MS)
            else:
                self._quitar_temporizador()

    # ----------------------------------------------------------------- pastillas
    def aplicar(self, clientes, monitores):
        """Sincroniza las pastillas con el estado de Hyprland. Devuelve el plan aplicado."""
        plan = L.planificar(clientes, monitores)
        vivas = {c.get("address") for c in clientes}
        con_plan = set()
        for p in plan:
            con_plan.add(p.direccion)
            pastilla = self.pastillas.get(p.direccion)
            if pastilla is None:
                pastilla = self.pastillas[p.direccion] = self._fabrica(self.app, p.direccion, self.pulsar)
            pastilla.colocar(p, self._buscar_monitor(p.nombre_monitor))
        for direccion in list(self.pastillas):
            if direccion in con_plan:
                continue
            if direccion in vivas:
                self.pastillas[direccion].ocultar()  # sigue existiendo (otro espacio, minimizada...): se esconde
            else:
                self.pastillas.pop(direccion).destroy()  # la ventana se cerró
        self._ajustar_temporizador()
        return plan

    def cerrar(self):
        """Quita las pastillas y para el sondeo (lo que llegue después de esto se ignora)."""
        self._cerrado = True
        self._quitar_temporizador()
        if self._pendiente:
            GLib.source_remove(self._pendiente)
            self._pendiente = 0
        for pastilla in self.pastillas.values():
            pastilla.destroy()
        self.pastillas.clear()

    # ----------------------------------------------------------------- acciones
    def pulsar(self, tipo, direccion):
        """Un botón de la pastilla de `direccion`: la orden va a ESA ventana, no a la que tenga el foco."""
        accion = {"minimizar": L.minimizar, "maximizar": L.alternar_maximizada, "cerrar": L.cerrar}.get(tipo)
        if accion is None:
            return
        accion(self.hypr, direccion)
        self.refrescar_pronto()
