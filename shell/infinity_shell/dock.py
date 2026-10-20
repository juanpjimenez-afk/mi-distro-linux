"""Las tres "islas" de cristal de la parte inferior:

    [ orbe ∞ ]          [   dock: apps fijadas y abiertas   ]          [ estado ]

Son ventanas independientes (la forma de Wayland de tener piezas separadas), así que el
logo queda realmente separado de la barra de tareas.
"""
import glob
import math
import os

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import calendario_icono, isla_logica  # noqa: E402  (parseo de volumen compartido con la isla: tolera la coma decimal)
from .ajustes import fecha_corta, valor as ajuste  # noqa: E402
from .controles_logica import elegir_destino_clic, es_minimizada, restaurar  # noqa: E402

ALTO = 60  # alto común de las tres islas (px)
ORBE = 48  # diámetro de las esferas de cristal (el logo y la burbuja de pAnkI), más pequeñas que el dock
LOGO_ORBE = 26  # lado nominal del símbolo dentro de la esfera (entero: es el pixel_size de Gtk.Image)
ESCALA_SIMBOLO = 0.95  # reducción fina del símbolo: la aplica style.css (button.orb image); 26 -> ~24,7 px
MARGEN = 14  # separación con el borde de la pantalla
HUECO_ESFERAS = 14  # aire entre la píldora de estado y las esferas que van encima de la fila del dock


def margen_esferas(margen=MARGEN, alto=ALTO, hueco=HUECO_ESFERAS):
    """Margen entre el borde inferior de la pantalla y el de las esferas (el ∞ y la burbuja de pAnkI):
    deja libres el margen, la fila de las islas (dock y píldora de estado, que ocupan `alto`) y un hueco."""
    return margen + alto + hueco


# ÚNICA fuente de verdad de la altura de las dos esferas: el ∞ (Orbe) y la burbuja de pAnkI (panki_burbuja)
# usan este mismo margen inferior, así que sus centros quedan siempre a la misma altura. Se mide desde el
# borde inferior de la pantalla (no depende del alto del monitor). Para la lectura alternativa «solo 9 px
# más arriba que ahora» (∞ a MARGEN + (ALTO - ORBE) // 2 + 9 = 39 px, la burbuja sin moverse a 88 px), el
# Orbe tendría que dejar de usar esta constante y usar 39.
ALTURA_ESFERAS = margen_esferas()

RUTAS_ASSETS = [
    os.environ.get("INFINITY_ASSETS", ""),
    "/usr/share/infinity",
    os.path.join(os.path.dirname(__file__), "..", "..", "branding"),
]


def asset(nombre):
    for base in RUTAS_ASSETS:
        ruta = os.path.join(base, nombre) if base else ""
        if ruta and os.path.exists(ruta):
            return os.path.abspath(ruta)
    return None


def _capa(ventana, namespace, bordes, margen_borde, *, exclusiva=False):
    LayerShell.init_for_window(ventana)
    LayerShell.set_namespace(ventana, namespace)
    LayerShell.set_layer(ventana, LayerShell.Layer.TOP)
    LayerShell.set_keyboard_mode(ventana, LayerShell.KeyboardMode.NONE)
    for b in bordes:
        LayerShell.set_anchor(ventana, b, True)
    LayerShell.set_margin(ventana, LayerShell.Edge.BOTTOM, MARGEN)
    if margen_borde is not None:
        edge, valor = margen_borde
        LayerShell.set_margin(ventana, edge, valor)
    if exclusiva:
        LayerShell.auto_exclusive_zone_enable(ventana)
    else:
        # -1: ignora la zona reservada por el dock y se pega al borde real de la pantalla,
        # así orbe, dock y estado quedan alineados en la misma fila.
        LayerShell.set_exclusive_zone(ventana, -1)
    ventana.add_css_class("infinity-window")


def icono_cuadricula(tam=22):
    """Botón de la biblioteca: nueve puntos en rejilla 3x3 (dibujado, no depende del tema)."""
    area = Gtk.DrawingArea()
    area.set_content_width(tam)
    area.set_content_height(tam)

    def dibujar(_area, cr, ancho, alto, _datos=None):
        paso = min(ancho, alto) / 3
        radio = paso * 0.34
        cr.set_source_rgba(1, 1, 1, 0.92)
        for fila in range(3):
            for col in range(3):
                cr.arc(paso * (col + 0.5), paso * (fila + 0.5), radio, 0, 2 * math.pi)
                cr.fill()

    area.set_draw_func(dibujar)
    return area


def _ejecutar(cmd):
    try:
        Gio.Subprocess.new(cmd, Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
    except GLib.Error as e:
        print(f"infinity-shell: no se pudo ejecutar {cmd[0]}: {e.message}")


# =================================================================== ORBE
def simbolo_de_esfera(ruta):
    """El símbolo blanco (SVG) de una esfera, con el mismo tamaño en el ∞ y en la burbuja de pAnkI.

    Mide LOGO_ORBE px (pixel_size solo admite enteros) y se centra en la esfera en vez de rellenarla; el
    «achique» fino lo hace style.css con -gtk-icon-transform: scale(ESCALA_SIMBOLO)."""
    simbolo = Gtk.Image.new_from_file(ruta)
    simbolo.set_pixel_size(LOGO_ORBE)
    simbolo.set_halign(Gtk.Align.CENTER)
    simbolo.set_valign(Gtk.Align.CENTER)
    return simbolo


class Orbe(Gtk.Window):
    """El logo (∞) en una esfera de cristal. Al pulsarlo se abre el buscador."""

    def __init__(self, app, al_pulsar):
        super().__init__(application=app, title="Infinity — Orbe")
        _capa(self, "infinity-orb", [LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT],
              (LayerShell.Edge.LEFT, MARGEN + 4))
        # El ∞ va a la misma altura que la burbuja de pAnkI, encima de la fila del dock y del estado
        LayerShell.set_margin(self, LayerShell.Edge.BOTTOM, ALTURA_ESFERAS)
        boton = Gtk.Button()
        boton.add_css_class("orb")
        boton.set_size_request(ORBE, ORBE)
        boton.set_tooltip_text("Buscar y apagar  (Super + Espacio)")
        ruta = asset("logo-white.svg")
        if ruta:
            boton.set_child(simbolo_de_esfera(ruta))
        else:
            boton.set_label("∞")
        boton.connect("clicked", lambda *_: al_pulsar())
        self.set_child(boton)
        self.boton = boton


# =================================================================== DOCK
DEFECTO_FIJADOS = ["foot.desktop", "org.gnome.Epiphany.desktop", "thunar.desktop", calendario_icono.ID_APP]


def _rutas_fijados():
    usuario = os.path.join(GLib.get_user_config_dir(), "infinity", "fijados")
    return usuario, "/etc/infinity/fijados"


def leer_fijados():
    # El fichero del usuario manda aunque este vacio: asi se pueden quitar todas las apps.
    for ruta in _rutas_fijados():
        try:
            with open(ruta, encoding="utf-8", errors="replace") as f:
                return [l.strip() for l in f if l.strip() and not l.strip().startswith("#")]
        except OSError:
            continue
    return list(DEFECTO_FIJADOS)


def guardar_fijados(ids):
    ruta = _rutas_fijados()[0]
    try:
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta + ".tmp", "w", encoding="utf-8") as f:
            f.write("# Apps fijadas en el dock de Infinity (una por línea)\n")
            f.write("\n".join(ids) + "\n")
        os.replace(ruta + ".tmp", ruta)
        return True
    except OSError as e:
        print(f"infinity-shell: no se pudo guardar {ruta}: {e}")
        return False


class Elemento:
    """Una app en el dock: puede estar fijada, abierta, o ambas."""

    def __init__(self, app, clase, fijada):
        self.app = app
        self.clase = clase
        self.fijada = fijada
        self.ventanas = []


class Dock(Gtk.Window):
    def __init__(self, app, indice, hypr, al_biblioteca=None):
        super().__init__(application=app, title="Infinity — Dock")
        self.indice = indice
        self.hypr = hypr
        self.fijados = leer_fijados()
        self.activa = None
        self._pendiente = 0
        self._en_curso = False
        self._repetir = False
        self._firma = None
        self._cambio_dia = 0  # temporizador que repinta el icono del Calendario al cambiar de día
        _capa(self, "infinity-dock", [LayerShell.Edge.BOTTOM], None, exclusiva=True)

        self.caja = Gtk.Box(spacing=4)
        self.caja.add_css_class("glass")
        self.caja.add_css_class("dock")
        self.caja.set_size_request(-1, ALTO)
        self.caja.set_halign(Gtk.Align.CENTER)  # la píldora se ajusta a su contenido
        self.set_child(self.caja)

        # Biblioteca de apps a la izquierda; las apps van en su propia caja, que es la que se
        # redibuja (así el botón no desaparece en cada refresco).
        if al_biblioteca is not None:
            biblioteca = Gtk.Button()
            biblioteca.add_css_class("dock-item")
            biblioteca.add_css_class("library-btn")
            biblioteca.set_child(icono_cuadricula())
            biblioteca.set_tooltip_text("Biblioteca de apps")
            biblioteca.connect("clicked", lambda *_: al_biblioteca())
            self.caja.append(biblioteca)
            separador = Gtk.Separator(orientation=Gtk.Orientation.VERTICAL)
            separador.add_css_class("dock-sep")
            self.caja.append(separador)
        self.apps = Gtk.Box(spacing=4)
        self.caja.append(self.apps)

        if hypr.disponible:
            hypr.escuchar(self._evento)
        self.refrescar()
        self._programar_cambio_de_dia()

    # --------------------------------------------------------- el icono del Calendario cambia cada día
    def _programar_cambio_de_dia(self):
        self._cambio_dia = GLib.timeout_add_seconds(calendario_icono.segundos_hasta_el_cambio_de_dia(),
                                                    self._al_cambiar_de_dia)

    def _al_cambiar_de_dia(self):
        self._cambio_dia = 0
        self.refrescar()  # la firma incluye el día: si cambió, se redibujan los botones con el icono nuevo
        self._programar_cambio_de_dia()
        return False

    # ----------------------------------------------------------------- eventos
    def _evento(self, nombre, datos):
        # Los cambios de título (windowtitlev2) llegan sin parar con un navegador y no cambian
        # nada visible en el dock, así que no se escuchan.
        if nombre in ("openwindow", "closewindow", "activewindowv2", "movewindowv2"):
            self.refrescar_pronto()

    def refrescar_pronto(self):
        # Agrupa ráfagas de eventos en una sola actualización.
        if not self._pendiente:
            self._pendiente = GLib.timeout_add(60, self._refrescar_ahora)

    def _refrescar_ahora(self):
        self._pendiente = 0
        self.refrescar()
        return False

    # ------------------------------------------------------------------ modelo
    def _modelo(self, ventanas):
        elementos = []
        por_clave = {}
        for app_id in self.fijados:
            app = self.indice.por_id.get(app_id)
            if app:
                el = Elemento(app, None, True)
                elementos.append(el)
                por_clave[app.id] = el
        extras = {}
        for v in ventanas:
            clase = v.get("class") or v.get("initialClass") or ""
            if not clase:
                continue
            app = self.indice.para_clase(clase)
            if app and app.id in por_clave:
                por_clave[app.id].ventanas.append(v)
            else:
                clave = app.id if app else "clase:" + clase.casefold()
                if clave not in extras:
                    extras[clave] = Elemento(app, clase, False)
                    elementos.append(extras[clave])
                extras[clave].ventanas.append(v)
        return elementos

    def refrescar(self):
        """Pide el estado a Hyprland en segundo plano y redibuja al recibirlo."""
        if not self.hypr.disponible:
            self._dibujar([], None)
            return
        if self._en_curso:
            self._repetir = True  # llegó otro evento mientras consultábamos: repite al terminar
            return
        self._en_curso = True
        self.hypr.estado_async(self._recibido)

    def _recibido(self, ventanas, activa):
        self._en_curso = False
        self._dibujar(ventanas, activa)
        if self._repetir:
            self._repetir = False
            self.refrescar()

    def _dibujar(self, ventanas, activa):
        self.activa = activa
        elementos = self._modelo(ventanas)
        # Solo se reconstruyen los botones si algo visible cambió. Reconstruir destruye los
        # menús abiertos, los tooltips y el estado de "hover", y gasta CPU para nada.
        dia = calendario_icono.dia_hoy()  # el icono del Calendario lleva el número de hoy
        firma = (dia,) + tuple(
            (el.app.id if el.app else "clase:" + (el.clase or "").casefold(), el.fijada,
             tuple((v["address"], es_minimizada(v)) for v in el.ventanas),
             any(v["address"] == activa for v in el.ventanas))
            for el in elementos
        )
        if firma == self._firma:
            return
        self._firma = firma
        while (hijo := self.apps.get_first_child()) is not None:
            self.apps.remove(hijo)
        for el in elementos:
            self.apps.append(self._boton(el))
        self.set_default_size(1, 1)  # permite que la ventana se encoja si hay menos iconos

    # ------------------------------------------------------------------ vistas
    def _boton(self, el):
        icono = el.app.icono if el.app else None
        if icono is None:
            img = Gtk.Image.new_from_icon_name((el.clase or "application-x-executable").casefold())
        else:
            img = Gtk.Image.new_from_gicon(icono)
        img.set_pixel_size(36)

        punto = Gtk.Box()
        punto.add_css_class("dot")
        punto.set_halign(Gtk.Align.CENTER)
        punto.set_valign(Gtk.Align.END)
        punto.set_opacity(1.0 if el.ventanas else 0.0)

        overlay = Gtk.Overlay()
        overlay.set_child(img)
        overlay.add_overlay(punto)

        boton = Gtk.Button()
        boton.add_css_class("dock-item")
        if any(v.get("address") == self.activa for v in el.ventanas):
            boton.add_css_class("active")
        boton.set_child(overlay)
        nombre = el.app.nombre if el.app else (el.clase or "?")
        boton.set_tooltip_text(nombre)
        boton.connect("clicked", lambda *_: self._pulsar(el))

        derecho = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        derecho.connect("pressed", lambda g, n, x, y: self._menu(boton, el))
        boton.add_controller(derecho)
        medio = Gtk.GestureClick(button=Gdk.BUTTON_MIDDLE)
        medio.connect("pressed", lambda *_: el.app and el.app.lanzar())
        boton.add_controller(medio)
        return boton

    def _pulsar(self, el):
        # Salta a la ventana más reciente que no esté ya en primer plano. Las minimizadas (espacio especial
        # `minimizadas`) siguen siendo ventanas de la app: si no hay otra a la vista, se trae la última al
        # espacio de trabajo activo y recibe el foco (controles_logica.restaurar).
        que, destino = elegir_destino_clic(el.ventanas, self.activa)
        if que == "restaurar":
            restaurar(self.hypr, destino["address"])
        elif que == "enfocar":
            self.hypr.despachar("focuswindow", f"address:{destino['address']}")
        elif el.app:
            el.app.lanzar()

    def _menu(self, boton, el):
        pop = Gtk.Popover()
        pop.set_parent(boton)
        pop.set_position(Gtk.PositionType.TOP)
        pop.add_css_class("dock-menu")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        def item(texto, fn):
            b = Gtk.Button(label=texto)
            b.add_css_class("menu-item")
            b.set_halign(Gtk.Align.FILL)
            b.connect("clicked", lambda *_: (pop.popdown(), fn()))
            caja.append(b)

        if el.app:
            item("Abrir ventana nueva" if el.ventanas else "Abrir", el.app.lanzar)
        for v in el.ventanas[:1]:
            item("Cerrar", lambda v=v: self.hypr.despachar("closewindow", f"address:{v['address']}"))
        if el.app:
            if el.fijada:
                item("Quitar del dock", lambda: self._fijar(el.app.id, False))
            else:
                item("Fijar en el dock", lambda: self._fijar(el.app.id, True))
        pop.set_child(caja)
        pop.connect("closed", lambda p: GLib.idle_add(p.unparent))
        pop.popup()

    def _fijar(self, app_id, fijar):
        if fijar and app_id not in self.fijados:
            self.fijados.append(app_id)
        elif not fijar and app_id in self.fijados:
            self.fijados.remove(app_id)
        guardar_fijados(self.fijados)
        self.refrescar()


# =================================================================== ESTADO
class Estado(Gtk.Window):
    """Volumen, red, batería y reloj."""

    def __init__(self, app):
        super().__init__(application=app, title="Infinity — Estado")
        _capa(self, "infinity-status", [LayerShell.Edge.BOTTOM, LayerShell.Edge.RIGHT],
              (LayerShell.Edge.RIGHT, MARGEN + 4))
        caja = Gtk.Box(spacing=4)
        caja.add_css_class("glass")
        caja.add_css_class("status")
        caja.set_size_request(-1, ALTO)
        self.set_child(caja)

        # Volumen: clic = silenciar, rueda = subir/bajar, clic derecho = mezclador
        self.vol_img = Gtk.Image.new_from_icon_name("audio-volume-high-symbolic")
        self.vol = Gtk.Button()
        self.vol.add_css_class("status-btn")
        self.vol.set_child(self.vol_img)
        self.vol.connect("clicked", lambda *_: self._wpctl(["set-mute", "@DEFAULT_AUDIO_SINK@", "toggle"]))
        rueda = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        rueda.connect("scroll", self._rueda)
        self.vol.add_controller(rueda)
        der = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        der.connect("pressed", lambda *_: _ejecutar(["pavucontrol"]))
        self.vol.add_controller(der)
        caja.append(self.vol)

        # Red: clic = elegir/conectar (nmtui en una terminal)
        self.red_img = Gtk.Image.new_from_icon_name("network-offline-symbolic")
        self.red = Gtk.Button()
        self.red.add_css_class("status-btn")
        self.red.set_child(self.red_img)
        self.red.set_tooltip_text("Red · clic para conectar")
        self.red.connect("clicked", lambda *_: _ejecutar(["foot", "nmtui"]))
        caja.append(self.red)

        # Batería (solo si existe)
        self.bat_caja = Gtk.Box(spacing=4)
        self.bat_caja.add_css_class("status-btn")
        self.bat_img = Gtk.Image.new_from_icon_name("battery-full-symbolic")
        self.bat_lbl = Gtk.Label()
        self.bat_caja.append(self.bat_img)
        self.bat_caja.append(self.bat_lbl)
        caja.append(self.bat_caja)

        # Reloj
        reloj = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        reloj.add_css_class("clock")
        reloj.set_valign(Gtk.Align.CENTER)
        self.hora = Gtk.Label(xalign=1)
        self.hora.add_css_class("clock-time")
        self.fecha = Gtk.Label(xalign=1)
        self.fecha.add_css_class("clock-date")
        reloj.append(self.hora)
        reloj.append(self.fecha)
        caja.append(reloj)

        self.recargar_reloj()
        self.refrescar()
        self._iniciar_reloj()
        monitor = Gio.NetworkMonitor.get_default()
        monitor.connect("network-changed", lambda *_: self._actualizar_red())
        GLib.timeout_add_seconds(30, self._tic_lento)

    # ------------------------------------------------------------------ reloj
    def _iniciar_reloj(self):
        self._actualizar_reloj()
        ahora = GLib.DateTime.new_now_local()
        GLib.timeout_add_seconds(60 - ahora.get_second() + 1, self._alinear_reloj)

    def _alinear_reloj(self):
        self._actualizar_reloj()
        GLib.timeout_add_seconds(60, lambda: (self._actualizar_reloj(), True)[1])
        return False

    def recargar_reloj(self):
        """Hora de 24 o 12 horas, según Ajustes. El shell lo llama al aplicar un cambio."""
        self._fmt_hora = "%-I:%M %p" if ajuste("dock.reloj") == "12h" else "%H:%M"
        self._actualizar_reloj()

    def _actualizar_reloj(self):
        ahora = GLib.DateTime.new_now_local()
        self.hora.set_text(ahora.format(self._fmt_hora))
        # «lun 19 oct» en español con tabla propia (ajustes.fecha_corta): %a y %b siguen el idioma del sistema
        self.fecha.set_text(fecha_corta(ahora.get_year(), ahora.get_month(), ahora.get_day_of_month()))

    # ----------------------------------------------------------------- estado
    def refrescar(self):
        self._actualizar_red()
        self._actualizar_bateria()
        self._actualizar_volumen()

    def _tic_lento(self):
        self._actualizar_bateria()
        self._actualizar_volumen()
        self._actualizar_red()
        return True

    def _actualizar_red(self):
        icono = "network-offline-symbolic"
        for ruta in sorted(glob.glob("/sys/class/net/*")):
            nombre = os.path.basename(ruta)
            if nombre == "lo":
                continue
            # Saltar interfaces virtuales (docker0, virbr0, veth*, tun/tap, wg*, tailscale*): suelen estar «up»
            # y por orden alfabético le ganarían al enlace real (p.ej. docker0 < enp..., tun0 < wlp...),
            # mostrando un icono de red engañoso. Todas cuelgan de /sys/devices/virtual/.
            if "/devices/virtual/" in os.path.realpath(ruta):
                continue
            try:
                with open(os.path.join(ruta, "operstate")) as f:
                    if f.read().strip() != "up":
                        continue
            except OSError:
                continue
            icono = ("network-wireless-symbolic" if os.path.isdir(os.path.join(ruta, "wireless"))
                     else "network-wired-symbolic")
            break
        self.red_img.set_from_icon_name(icono)

    def _actualizar_bateria(self):
        bats = glob.glob("/sys/class/power_supply/BAT*")
        self.bat_caja.set_visible(bool(bats))
        if not bats:
            return
        try:
            with open(os.path.join(bats[0], "capacity")) as f:
                pct = int(f.read())
            with open(os.path.join(bats[0], "status")) as f:
                carga = f.read().strip() in ("Charging", "Full")
        except (OSError, ValueError):
            return
        nivel = min(100, (pct // 10) * 10)
        self.bat_img.set_from_icon_name(f"battery-level-{nivel}{'-charging' if carga else ''}-symbolic")
        self.bat_lbl.set_text(f"{pct}%")

    def _actualizar_volumen(self):
        try:
            p = Gio.Subprocess.new(["wpctl", "get-volume", "@DEFAULT_AUDIO_SINK@"],
                                   Gio.SubprocessFlags.STDOUT_PIPE | Gio.SubprocessFlags.STDERR_SILENCE)
        except GLib.Error:
            return
        p.communicate_utf8_async(None, None, self._volumen_listo)

    def _volumen_listo(self, proc, res):
        try:
            _ok, salida, _err = proc.communicate_utf8_finish(res)
        except GLib.Error:
            return
        # Mismo parseo que la isla: tolera el locale con coma ('Volume: 0,45'), que en un sistema en español
        # haría fallar un float() con punto y dejaría el icono sin actualizar en silencio.
        datos = isla_logica.parsear_volumen(salida)
        if datos is None:
            return
        valor, mudo = datos
        self.vol_img.set_from_icon_name(isla_logica.icono_volumen(valor, mudo))
        self.vol.set_tooltip_text(f"Volumen {round(valor * 100)}%" + (" (silenciado)" if mudo else ""))

    def _wpctl(self, args):
        try:
            p = Gio.Subprocess.new(["wpctl"] + args,
                                   Gio.SubprocessFlags.STDOUT_SILENCE | Gio.SubprocessFlags.STDERR_SILENCE)
            p.wait_async(None, lambda *_: self._actualizar_volumen())
        except GLib.Error:
            pass

    def _rueda(self, _ctrl, _dx, dy):
        self._wpctl(["set-volume", "-l", "1.0", "@DEFAULT_AUDIO_SINK@", "5%-" if dy > 0 else "5%+"])
        return True
