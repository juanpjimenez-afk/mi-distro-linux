"""La ISLA DINÁMICA: una píldora negra redondeada, centrada arriba, que se expande, se encoge y se divide
en dos burbujas para enseñar «actividades en vivo» (inspirada en la Dynamic Island del iPhone, pero como
widget de software puro: aquí no hay muesca, ni panel OLED, ni cámara; solo GTK4 + CSS + una ventana de capa).

    COLAPSADA            EXPANDIDA (OSD)                 DIVIDIDA (dos actividades)
    ┌────────┐      ┌───────────────────────┐      ┌───────────┐   ┌───────────┐
    │ 12:34  │      │  🔊  ▭▭▭▭▭▭▭▭░░   65%  │      │ ♪ Canción │   │ 🔆 ▭▭▭░ 40 │
    └────────┘      └───────────────────────┘      └───────────┘   └───────────┘

Cómo se logra la sensación «líquida» (ver también el bloque ISLA de style.css):
- La ventana de capa (namespace `infinity-isla`) se ancla solo ARRIBA, así el compositor la centra sola en
  horizontal; no tiene tamaño fijo, se auto-ajusta a la píldora que lleva dentro.
- El tamaño (min-width / min-height) y el radio de borde de la píldora los lleva el CSS por clase de estado
  (`isla-colapsada`, `isla-expandida`, `isla-burbuja`), con una transición `ease`. Al cambiar de clase, GTK
  anima esos valores y re-mide cada fotograma, de modo que la VENTANA crece o encoge de verdad siguiendo la
  animación del CSS (no es solo un efecto dentro de una ventana fija). La segunda burbuja aparece animando su
  min-width y su opacidad desde cero. LÍMITE: la fluidez real y el que no haya un salto de un fotograma al
  pedirle al compositor la nueva superficie solo se pueden juzgar sobre Hyprland; aquí no hay desenfoque.

Robustez: si wpctl o brightnessctl no están, o Hyprland no responde, la isla no falla ni bloquea; el OSD
simplemente no aparece. Nada de sondeos continuos: la isla reacciona a las acciones `osd-volumen` /
`osd-brillo` (y, si hay, a MPRIS por D-Bus) y usa un único temporizador de un disparo para auto-colapsar.
El reloj de reposo se refresca con un temporizador alineado al minuto que SOLO vive mientras está colapsada.
"""
import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Gsk", "4.0")
gi.require_version("Graphene", "1.0")
from gi.repository import Gdk, Gio, GLib, Graphene, Gsk, Gtk  # noqa: E402

from . import isla_logica as L  # noqa: E402
from .dock import _capa  # noqa: E402  (reutiliza el ayudante de ventana de capa, sin tocar dock.py)

NAMESPACE = "infinity-isla"  # lo casa la regla de desenfoque `^infinity-.*$` de hyprland.conf
MARGEN_SUPERIOR = 8  # aire con el borde de arriba: la píldora «flota» pegada al techo, como la muesca

ACENTO = "#b9a8ff"  # el violeta de marca de Infinity, para la barra de progreso
_TROUGH = "rgba(255,255,255,0.16)"  # el canal de la barra (translúcido)


# ====================================================================== lectores (inyectables)
def leer_volumen_wpctl(cb):
    """Lee el volumen con `wpctl` sin bloquear y llama cb(fraccion, mudo); cb(None, None) si falla."""
    try:
        p = Gio.Subprocess.new(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                               Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        cb(None, None)
        return

    def listo(proc, res):
        try:
            _ok, salida, _err = proc.communicate_utf8_finish(res)
        except GLib.Error:
            cb(None, None)
            return
        r = L.parsear_volumen(salida)
        cb(*(r if r is not None else (None, None)))

    p.communicate_utf8_async(None, None, listo)


def leer_brillo_brightnessctl(cb):
    """Lee el brillo con `brightnessctl -m` sin bloquear y llama cb(pct); cb(None) si falla."""
    try:
        p = Gio.Subprocess.new(["brightnessctl", "-m"],
                               Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error:
        cb(None)
        return

    def listo(proc, res):
        try:
            _ok, salida, _err = proc.communicate_utf8_finish(res)
        except GLib.Error:
            cb(None)
            return
        cb(L.parsear_brillo_m(salida))

    p.communicate_utf8_async(None, None, listo)


def reloj_local():
    """La hora local HH:MM (reloj de reposo). Inyectable en las pruebas para fijar la hora."""
    return GLib.DateTime.new_now_local().format("%H:%M")


# ====================================================================== barra de progreso
def _rgba(texto):
    c = Gdk.RGBA()
    c.parse(texto)
    return c


class Barra(Gtk.Widget):
    """La barra de progreso del OSD: un canal redondeado y un relleno violeta. Dibujada con Gsk (no depende
    del tema ni de pycairo); el ancho lo da el contenedor (hexpand), así se adapta a la burbuja."""

    __gtype_name__ = "InfinityIslaBarra"
    ALTO = 6

    def __init__(self):
        super().__init__()
        self._frac = 0.0
        self.set_size_request(-1, self.ALTO)
        self.set_hexpand(True)
        self.set_valign(Gtk.Align.CENTER)

    def poner(self, fraccion):
        self._frac = max(0.0, min(1.0, float(fraccion)))
        self.queue_draw()

    def do_snapshot(self, snapshot):
        w, h = self.get_width(), self.get_height()
        if w <= 0 or h <= 0:
            return
        r = h / 2.0
        canal = Gsk.RoundedRect()
        canal.init_from_rect(Graphene.Rect().init(0, 0, w, h), r)
        snapshot.push_rounded_clip(canal)
        snapshot.append_color(_rgba(_TROUGH), Graphene.Rect().init(0, 0, w, h))
        snapshot.pop()
        ancho = max(h, w * self._frac)  # al menos un redondel, aunque sea 0 %
        relleno = Gsk.RoundedRect()
        relleno.init_from_rect(Graphene.Rect().init(0, 0, ancho, h), r)
        snapshot.push_rounded_clip(relleno)
        snapshot.append_color(_rgba(ACENTO), Graphene.Rect().init(0, 0, ancho, h))
        snapshot.pop()


# ====================================================================== la ventana de la isla
class Isla(Gtk.Window):
    """La ventana de capa de la isla. Vive siempre; su contenido y su tamaño cambian según el estado."""

    def __init__(self, app, *, logica=None, leer_volumen=leer_volumen_wpctl,
                 leer_brillo=leer_brillo_brightnessctl, reloj=reloj_local, reproduccion=None):
        super().__init__(application=app, title="Infinity — Isla")
        self.logica = logica if logica is not None else L.LogicaIsla()
        self._leer_volumen = leer_volumen
        self._leer_brillo = leer_brillo
        self._reloj = reloj
        self._colapso = 0   # id del temporizador de auto-colapso (un disparo)
        self._tic_reloj = 0  # id del temporizador del reloj de reposo (solo mientras colapsada)
        self._firma = None   # tipos pintados ahora mismo, para no reconstruir sin necesidad
        self._slots = []     # refs a los widgets de cada burbuja, para refrescar sin reconstruir

        # Ventana de capa anclada ARRIBA: el compositor la centra sola en horizontal. Sin zona exclusiva
        # (no reserva sitio) y sin foco de teclado: _capa ya lo deja así; aquí solo cambia el borde superior.
        _capa(self, NAMESPACE, [LayerShell.Edge.TOP], (LayerShell.Edge.TOP, MARGEN_SUPERIOR))
        self.add_css_class("isla")

        self.caja = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self.caja.set_halign(Gtk.Align.CENTER)
        self.caja.set_valign(Gtk.Align.START)
        self.izq = self._pildora("isla-izq")
        self.der = self._pildora("isla-der")
        self.caja.append(self.izq)
        self.caja.append(self.der)
        self.set_child(self.caja)

        # Reproducción en curso (now-playing) opcional: fábrica inyectable `reproduccion(al_cambiar)`.
        # Por defecto no se engancha nada (el núcleo —OSD de volumen y brillo— no la necesita); main.py
        # puede pasar ReproduccionMPRIS para leer el reproductor por D-Bus sin añadir dependencias.
        self._repro = reproduccion(self._media_cambio) if reproduccion is not None else None

        self._aplicar()  # arranca en reposo (colapsada)

    # ------------------------------------------------------------------ construcción
    @staticmethod
    def _pildora(clase_lado):
        p = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        p.add_css_class("isla-pildora")
        p.add_css_class(clase_lado)
        p.set_valign(Gtk.Align.START)
        p.set_overflow(Gtk.Overflow.HIDDEN)  # el contenido no se sale de las esquinas redondeadas
        return p

    def _contenido(self, tipo):
        """Crea el contenido de una burbuja para `tipo` y devuelve (widget_raiz, refs)."""
        if tipo == L.RELOJ:
            lbl = Gtk.Label()
            lbl.add_css_class("isla-reloj")
            lbl.set_hexpand(True)
            return lbl, {"label": lbl}
        if tipo == L.MEDIA:
            caja = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            img = Gtk.Image()
            img.add_css_class("isla-icono")
            img.set_pixel_size(18)
            lbl = Gtk.Label(xalign=0)
            lbl.add_css_class("isla-titulo")
            lbl.set_hexpand(True)
            lbl.set_ellipsize(3)  # Pango.EllipsizeMode.END
            lbl.set_single_line_mode(True)
            caja.append(img)
            caja.append(lbl)
            return caja, {"imagen": img, "titulo": lbl}
        # VOLUMEN / BRILLO: icono + barra + porcentaje
        caja = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        img = Gtk.Image()
        img.add_css_class("isla-icono")
        img.set_pixel_size(18)
        barra = Barra()
        pct = Gtk.Label()
        pct.add_css_class("isla-pct")
        pct.set_width_chars(4)  # evita que la píldora «respire» al pasar de 9% a 100%
        caja.append(img)
        caja.append(barra)
        caja.append(pct)
        return caja, {"imagen": img, "barra": barra, "pct": pct}

    def _pintar(self, tipo, refs, datos):
        """Refresca los valores de una burbuja ya construida (sin reconstruir)."""
        if tipo == L.RELOJ:
            try:
                refs["label"].set_text(self._reloj() or "")
            except Exception:  # noqa: BLE001  (un reloj inyectable no debe tumbar la isla)
                refs["label"].set_text("")
        elif tipo == L.MEDIA:
            refs["imagen"].set_from_icon_name(L.ICONO_PAUSA if datos.get("reproduciendo") else L.ICONO_PLAY)
            titulo = datos.get("titulo") or "Reproduciendo"
            if datos.get("artista"):
                titulo = f"{titulo} — {datos['artista']}"
            refs["titulo"].set_text(titulo)
        else:  # VOLUMEN / BRILLO
            refs["imagen"].set_from_icon_name(datos.get("icono") or L.ICONO_BRILLO)
            refs["barra"].poner(datos.get("fraccion", 0.0))
            refs["pct"].set_text(f"{datos.get('pct', 0)}%")

    # ------------------------------------------------------------------ estados / acciones
    def mostrar_volumen(self, *_):
        """Acción `osd-volumen`: lee el volumen actual y enseña (o refresca) el OSD de volumen."""
        self._leer_volumen(self._con_volumen)

    def _con_volumen(self, fraccion, mudo):
        if fraccion is None:
            return  # no se pudo leer: no molestamos con un OSD vacío
        self.logica.osd(L.VOLUMEN, L.datos_volumen(fraccion, mudo))
        self._aplicar()

    def mostrar_brillo(self, *_):
        """Acción `osd-brillo`: lee el brillo actual y enseña (o refresca) el OSD de brillo."""
        self._leer_brillo(self._con_brillo)

    def _con_brillo(self, pct):
        if pct is None:
            return
        self.logica.osd(L.BRILLO, L.datos_brillo(pct))
        self._aplicar()

    def _media_cambio(self, info):
        """Lo llama la reproducción (MPRIS): info = dict de now-playing o None si no suena nada."""
        self.logica.media(info)
        self._aplicar()

    def _vencer(self):
        self._colapso = 0
        self.logica.tic()  # caduca lo vencido
        self._aplicar()
        return False  # un disparo

    # ------------------------------------------------------------------ pintado coordinado
    def _aplicar(self):
        """Única vía de actualización: fija el estado, el contenido, las clases de tamaño y los temporizadores.
        Se puede llamar cuantas veces haga falta; solo reconstruye si cambió el reparto de burbujas."""
        estado = self.logica.estado()
        paneles = self.logica.paneles()
        firma = tuple(p["tipo"] for p in paneles)
        if firma != self._firma:
            self._reconstruir(paneles)
            self._firma = firma
        for slot, panel in zip(self._slots, paneles):
            self._pintar(panel["tipo"], slot["refs"], panel["datos"])
        self._clases_de_tamano(estado)
        self._gestionar_reloj(estado)
        self._reprogramar_colapso()

    def _reconstruir(self, paneles):
        for pild in (self.izq, self.der):
            while (hijo := pild.get_first_child()) is not None:
                pild.remove(hijo)
        self._slots = []
        destinos = (self.izq, self.der)
        for i, panel in enumerate(paneles[:2]):
            widget, refs = self._contenido(panel["tipo"])
            destinos[i].append(widget)
            self._slots.append({"refs": refs})

    def _clases_de_tamano(self, estado):
        """Pone en cada burbuja la clase que el CSS anima. izq siempre se ve; der solo en dividida."""
        if estado == L.DIVIDIDA:
            izq_clase, der_clase = "isla-burbuja", "isla-burbuja"
        elif estado == L.EXPANDIDA:
            izq_clase, der_clase = "isla-expandida", "isla-oculta"
        else:
            izq_clase, der_clase = "isla-colapsada", "isla-oculta"
        for pild, clase in ((self.izq, izq_clase), (self.der, der_clase)):
            for c in ("isla-colapsada", "isla-expandida", "isla-burbuja", "isla-oculta"):
                pild.remove_css_class(c)
            pild.add_css_class(clase)

    # ------------------------------------------------------------------ temporizadores
    def _reprogramar_colapso(self):
        """Un único temporizador de un disparo para el próximo auto-colapso (nada de sondeos)."""
        if self._colapso:
            GLib.source_remove(self._colapso)
            self._colapso = 0
        dt = self.logica.proximo_vencimiento()
        if dt is not None:
            self._colapso = GLib.timeout_add(max(1, int(dt * 1000) + 30), self._vencer)

    def _gestionar_reloj(self, estado):
        """El reloj de reposo solo corre mientras la isla está colapsada; en cuanto se expande, se para."""
        if estado == L.COLAPSADA:
            if not self._tic_reloj:
                self._programar_reloj()
        elif self._tic_reloj:
            GLib.source_remove(self._tic_reloj)
            self._tic_reloj = 0

    def _programar_reloj(self):
        try:
            segundo = GLib.DateTime.new_now_local().get_second()
        except Exception:  # noqa: BLE001
            segundo = 0
        self._tic_reloj = GLib.timeout_add_seconds(max(1, 60 - segundo), self._tic_de_reloj)

    def _tic_de_reloj(self):
        self._tic_reloj = 0
        if self.logica.estado() != L.COLAPSADA:
            return False
        if self._slots:  # refresca la hora en sitio
            self._pintar(L.RELOJ, self._slots[0]["refs"], {})
        self._programar_reloj()  # vuelve a alinear al siguiente minuto
        return False

    # ------------------------------------------------------------------ cierre
    def do_destroy(self):
        for atrib in ("_colapso", "_tic_reloj"):
            ident = getattr(self, atrib, 0)
            if ident:
                GLib.source_remove(ident)
                setattr(self, atrib, 0)
        if getattr(self, "_repro", None) is not None:
            try:
                self._repro.cerrar()
            except Exception:  # noqa: BLE001
                pass
            self._repro = None
        Gtk.Window.do_destroy(self)


# ====================================================================== reproducción (MPRIS, opcional)
class ReproduccionMPRIS:
    """Lee el reproductor activo por MPRIS (D-Bus de sesión) usando solo Gio: NO añade dependencias
    (nada de playerctl). Es totalmente defensiva: si el bus o el reproductor fallan, no enseña nada y
    jamás rompe la isla. Llama `al_cambiar(info)` con el now-playing (dict de isla_logica.datos_media)
    o con None cuando no suena nada.

    NOTA: en este entorno no hay sesión de audio ni reproductor, así que este camino NO se ha podido
    verificar de verdad; se prueba con un doble (ver test_isla.py). Es una ampliación «lista para la VM»."""

    MPRIS_PREFIJO = "org.mpris.MediaPlayer2."
    RUTA = "/org/mpris/MediaPlayer2"
    IFACE = "org.mpris.MediaPlayer2.Player"

    def __init__(self, al_cambiar):
        self._al_cambiar = al_cambiar
        self._bus = None
        self._suscripciones = []
        self._nombre = None  # el nombre de bus del reproductor elegido
        try:
            Gio.bus_get(Gio.BusType.SESSION, None, self._bus_listo)
        except Exception:  # noqa: BLE001
            pass

    def _bus_listo(self, _origen, res):
        try:
            self._bus = Gio.bus_get_finish(res)
        except GLib.Error:
            return
        try:
            self._suscripciones.append(self._bus.signal_subscribe(
                "org.freedesktop.DBus", "org.freedesktop.DBus", "NameOwnerChanged",
                "/org/freedesktop/DBus", None, Gio.DBusSignalFlags.NONE, self._cambio_nombres))
            self._suscripciones.append(self._bus.signal_subscribe(
                None, "org.freedesktop.DBus.Properties", "PropertiesChanged",
                self.RUTA, self.IFACE, Gio.DBusSignalFlags.NONE, self._cambio_propiedades))
        except Exception:  # noqa: BLE001
            pass
        self._buscar()

    def _cambio_nombres(self, *_args):
        self._buscar()

    def _cambio_propiedades(self, *_args):
        # Cualquier cambio de propiedades de un reproductor: re-leemos el estado actual.
        self._buscar()

    def _buscar(self):
        if self._bus is None:
            return
        try:
            self._bus.call(
                "org.freedesktop.DBus", "/org/freedesktop/DBus", "org.freedesktop.DBus", "ListNames",
                None, GLib.VariantType.new("(as)"), Gio.DBusCallFlags.NONE, -1, None, self._con_nombres)
        except Exception:  # noqa: BLE001
            pass

    def _con_nombres(self, _origen, res):
        try:
            (nombres,) = self._bus.call_finish(res)
        except GLib.Error:
            return
        reproductores = [n for n in nombres if n.startswith(self.MPRIS_PREFIJO)]
        self._nombre = reproductores[0] if reproductores else None
        if self._nombre is None:
            self._emitir(None)
            return
        try:
            self._bus.call(
                self._nombre, self.RUTA, "org.freedesktop.DBus.Properties", "GetAll",
                GLib.Variant("(s)", (self.IFACE,)), GLib.VariantType.new("(a{sv})"),
                Gio.DBusCallFlags.NONE, -1, None, self._con_props)
        except Exception:  # noqa: BLE001
            pass

    def _con_props(self, _origen, res):
        try:
            (props,) = self._bus.call_finish(res)
        except GLib.Error:
            return
        estado = props.get("PlaybackStatus", "")
        meta = props.get("Metadata", {}) or {}
        titulo = meta.get("xesam:title", "") or ""
        artistas = meta.get("xesam:artist", []) or []
        artista = artistas[0] if isinstance(artistas, (list, tuple)) and artistas else ""
        if estado in ("Playing", "Paused") and (titulo or artista):
            self._emitir(L.datos_media(titulo, artista, reproduciendo=(estado == "Playing")))
        else:
            self._emitir(None)

    def _emitir(self, info):
        try:
            self._al_cambiar(info)
        except Exception:  # noqa: BLE001
            pass

    def play_pausa(self):
        """Alterna play/pausa del reproductor elegido (al tocar la burbuja de reproducción)."""
        if self._bus is None or self._nombre is None:
            return
        try:
            self._bus.call(self._nombre, self.RUTA, self.IFACE, "PlayPause", None, None,
                           Gio.DBusCallFlags.NONE, -1, None, None)
        except Exception:  # noqa: BLE001
            pass

    def cerrar(self):
        if self._bus is not None:
            for ident in self._suscripciones:
                try:
                    self._bus.signal_unsubscribe(ident)
                except Exception:  # noqa: BLE001
                    pass
        self._suscripciones = []
        self._bus = None
