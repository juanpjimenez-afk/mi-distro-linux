"""Contraseñas, el gestor de contraseñas de Infinity OS: la ventana (infinity-boveda).

GTK 4 nativo, sin WebKit: ningún secreto pasa por un motor web. Toda la lógica (formato, cifrado, ficheros,
generador, fortaleza, portapapeles temporal) está en boveda.py y se prueba sin pantalla; aquí solo se
dibuja y se conecta. El modelo de seguridad y sus límites: docs/boveda.md.

    python3 -m infinity_shell.boveda_app      (lo lanza el comando infinity-boveda)

Resumen de lo que hace esta ventana:
  * Siempre arranca BLOQUEADA (proceso nuevo = bóveda cerrada): crea la contraseña maestra si no hay bóveda, o la
    pide. Tras varios fallos seguidos hace esperar (1, 2, 4, 8, 16 y 30 s).
  * El KDF (scrypt, ~0,3 s) corre en un hilo: la ventana no se congela y se ve «Comprobando…».
  * Se bloquea sola a los 3 minutos sin teclas, clics ni movimiento, con el botón «Bloquear» (Ctrl+L) y, si hay
    bus del sistema, al suspender (PrepareForSleep de logind). Al bloquear suelta las entradas y la clave, vacía
    los widgets, limpia el portapapeles si aún tiene lo que copiaste y llama a gc.collect().
  * «Copiar» pone el texto en el portapapeles y lo borra a los 25 s SOLO si sigue siendo lo copiado.
  * No abre URLs ni toca la red.
"""
import gc
import math
import os
import sys
import threading
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from . import boveda as B  # noqa: E402

APP_ID = "org.infinity.Boveda"
INACTIVIDAD_S = 180          # 3 minutos sin actividad → se bloquea
PORTAPAPELES_S = 25          # el portapapeles se limpia a los 25 s
OCULTAR_CLAVE_S = 20         # una contraseña que se dejó a la vista vuelve a ocultarse sola
PUNTOS = "•" * 10       # fijo: así la longitud de la contraseña no se adivina mirando la pantalla
VAR_ICONO_DEV = "INFINITY_BOVEDA_ICONO"
RUTA_ICONO_INSTALADA = "/usr/share/infinity/boveda.svg"

# El acento violeta (#b9a8ff) es el de pAnkI y de todo Infinity OS. El fondo oscuro translúcido deja que Hyprland
# desenfoque lo de detrás (blur de ventanas); el resto del estilo del shell vive en style.css, que aquí NO se carga.
ESTILO = """
window.boveda-ventana { background-color: rgba(18, 22, 44, 0.93); color: #e9ebf7; }

headerbar.boveda-cabecera { min-height: 34px; padding: 0 6px 0 14px; background: none; border: none; box-shadow: none; }
headerbar.boveda-cabecera windowcontrols button { opacity: 0.5; }
headerbar.boveda-cabecera windowcontrols button:hover { opacity: 1; }
label.boveda-marca { color: #b9a8ff; font-size: 14px; font-weight: 600; }
headerbar.boveda-cabecera image.boveda-marca { color: #b9a8ff; }

window.boveda-ventana button {
  background-image: none; background-color: rgba(255, 255, 255, 0.08); color: #e9ebf7;
  border: 1px solid rgba(255, 255, 255, 0.12); border-radius: 12px; box-shadow: none;
  padding: 6px 14px; min-height: 22px; text-shadow: none;
}
window.boveda-ventana button:hover { background-color: rgba(255, 255, 255, 0.16); }
window.boveda-ventana button:disabled { opacity: 0.45; }
window.boveda-ventana button.suggested-action { background-color: #b9a8ff; color: #1a1636; border-color: #b9a8ff; font-weight: 600; }
window.boveda-ventana button.suggested-action label { color: #1a1636; }
window.boveda-ventana button.suggested-action:hover { background-color: #cdbfff; }
window.boveda-ventana button.destructive-action { background-color: rgba(255, 99, 99, 0.28); border-color: rgba(255, 138, 128, 0.55); }
window.boveda-ventana button.destructive-action:hover { background-color: rgba(255, 99, 99, 0.45); }
window.boveda-ventana button.flat, window.boveda-ventana menubutton > button { background-color: transparent; border-color: transparent; }
window.boveda-ventana button.flat:hover, window.boveda-ventana menubutton > button:hover { background-color: rgba(255, 255, 255, 0.12); }
window.boveda-ventana button.boveda-icono { padding: 6px 8px; }
window.boveda-ventana button.boveda-bloquear { background-color: rgba(185, 168, 255, 0.20); border-color: rgba(185, 168, 255, 0.45); color: #e2d9ff; }
window.boveda-ventana button.boveda-bloquear:hover { background-color: rgba(185, 168, 255, 0.34); }

window.boveda-ventana entry, window.boveda-ventana text {
  color: #ffffff; caret-color: #b9a8ff;
}
window.boveda-ventana entry {
  background-color: rgba(255, 255, 255, 0.07); border: 1px solid rgba(255, 255, 255, 0.14);
  border-radius: 12px; min-height: 22px; padding: 6px 12px; box-shadow: none;
}
window.boveda-ventana entry:focus-within { border-color: #b9a8ff; box-shadow: 0 0 0 1px rgba(185, 168, 255, 0.45); }
window.boveda-ventana entry image { color: rgba(233, 235, 247, 0.7); }
window.boveda-ventana textview, window.boveda-ventana textview text { background-color: transparent; color: #ffffff; }
window.boveda-ventana scrolledwindow.boveda-notas {
  background-color: rgba(255, 255, 255, 0.07); border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 12px;
}
window.boveda-ventana dropdown > button { padding: 6px 12px; }
window.boveda-ventana spinbutton button { padding: 2px 8px; min-width: 0; min-height: 0; border-radius: 8px; }
window.boveda-ventana spinbutton { border-radius: 12px; }
window.boveda-ventana popover > contents { background-color: rgba(30, 34, 58, 0.97); border: 1px solid rgba(255, 255, 255, 0.16); border-radius: 14px; }
window.boveda-ventana popover modelbutton { border-radius: 8px; }
window.boveda-ventana popover modelbutton:hover { background-color: rgba(185, 168, 255, 0.22); }
window.boveda-ventana checkbutton { color: #e9ebf7; }
window.boveda-ventana expander title { padding: 4px 0; }

.boveda-titulo { font-size: 26px; font-weight: 700; }
window.boveda-ventana label.boveda-subtitulo { color: rgba(233, 235, 247, 0.66); }
window.boveda-ventana label.boveda-pequeno { font-size: 12px; color: rgba(233, 235, 247, 0.55); }
window.boveda-ventana label.boveda-error { color: #ff8a80; }
window.boveda-ventana label.boveda-ok { color: #8be9a8; }
.boveda-aviso {
  background-color: rgba(255, 196, 107, 0.11); border: 1px solid rgba(255, 196, 107, 0.38);
  border-radius: 12px; padding: 10px 12px;
}
window.boveda-ventana .boveda-aviso label, window.boveda-ventana .boveda-aviso image { color: #ffd9a0; }

.boveda-lateral { background-color: rgba(255, 255, 255, 0.035); border-right: 1px solid rgba(255, 255, 255, 0.07); }
window.boveda-ventana label.boveda-lateral-titulo { font-size: 11px; font-weight: 700; letter-spacing: 1px; color: rgba(233, 235, 247, 0.5); }
list.boveda-categorias, list.boveda-lista { background: transparent; }
.boveda-cat { padding: 8px 10px; }
list.boveda-categorias row { border-radius: 10px; margin: 1px 8px; }
list.boveda-categorias row:selected { background-color: rgba(185, 168, 255, 0.22); }
list.boveda-categorias row:hover:not(:selected) { background-color: rgba(255, 255, 255, 0.07); }
.boveda-cuenta { background-color: rgba(255, 255, 255, 0.09); border-radius: 99px; padding: 0 8px; font-size: 11px; }
list.boveda-categorias row:selected .boveda-cuenta { background-color: rgba(185, 168, 255, 0.35); }

.boveda-medio { border-right: 1px solid rgba(255, 255, 255, 0.07); }
list.boveda-lista row { border-radius: 12px; margin: 2px 8px; }
list.boveda-lista row:selected { background-color: rgba(185, 168, 255, 0.20); }
list.boveda-lista row:hover:not(:selected) { background-color: rgba(255, 255, 255, 0.07); }
.boveda-fila { padding: 8px 10px; }
.boveda-fila-nombre { font-weight: 600; font-size: 14px; }
window.boveda-ventana label.boveda-fila-usuario { font-size: 12px; color: rgba(233, 235, 247, 0.6); }
.boveda-avatar {
  min-width: 36px; min-height: 36px; border-radius: 99px;
  background-color: rgba(185, 168, 255, 0.18); color: #b9a8ff; font-weight: 700;
}
window.boveda-ventana .boveda-avatar label { color: #cfc3ff; }
.boveda-avatar-grande { min-width: 56px; min-height: 56px; }
.boveda-avatar-grande label { font-size: 24px; }

.boveda-d-titulo { font-size: 24px; font-weight: 700; }
.boveda-chip { background-color: rgba(185, 168, 255, 0.18); border-radius: 99px; padding: 1px 10px; font-size: 12px; }
window.boveda-ventana .boveda-chip label { color: #d9cfff; }
.boveda-tarjeta {
  background-color: rgba(255, 255, 255, 0.05); border: 1px solid rgba(255, 255, 255, 0.08);
  border-radius: 14px; padding: 10px 14px;
}
window.boveda-ventana label.boveda-etiqueta { font-size: 12px; font-weight: 600; color: rgba(233, 235, 247, 0.55); }
.boveda-valor { font-size: 15px; }
.boveda-clave { font-family: "JetBrains Mono", "Noto Sans Mono", monospace; font-size: 16px; letter-spacing: 1px; }
.boveda-vacio-titulo { font-size: 17px; font-weight: 600; }
.boveda-barra { background-color: rgba(185, 168, 255, 0.12); border-top: 1px solid rgba(185, 168, 255, 0.28); padding: 8px 18px; }
window.boveda-ventana .boveda-barra label { color: #e2d9ff; }
.boveda-barra.error { background-color: rgba(255, 99, 99, 0.12); border-top-color: rgba(255, 138, 128, 0.4); }
window.boveda-ventana .boveda-barra.error label { color: #ffb4ab; }
.boveda-confirmar { background-color: rgba(255, 99, 99, 0.12); border: 1px solid rgba(255, 138, 128, 0.4); border-radius: 14px; padding: 10px 14px; }

.boveda-seg { min-height: 5px; border-radius: 99px; background-color: rgba(255, 255, 255, 0.12); }
.boveda-seg.on.f0 { background-color: #ff6b6b; }
.boveda-seg.on.f1 { background-color: #ff9f5a; }
.boveda-seg.on.f2 { background-color: #f5d76e; }
.boveda-seg.on.f3 { background-color: #9be58f; }
.boveda-seg.on.f4 { background-color: #4fe3a1; }
"""

_estilos_cargados = set()


def _cargar_estilo(pantalla):
    """Carga el CSS de Contraseñas una vez por pantalla, con prioridad de aplicación."""
    if pantalla is None or id(pantalla) in _estilos_cargados:
        return
    proveedor = Gtk.CssProvider()
    if hasattr(proveedor, "load_from_string"):  # GTK 4.12 o más; antes, load_from_data
        proveedor.load_from_string(ESTILO)
    else:
        proveedor.load_from_data(ESTILO.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(pantalla, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _estilos_cargados.add(id(pantalla))


def ruta_icono():
    """El icono de la app: variable de desarrollo, ruta instalada o el del propio repositorio."""
    aqui = os.path.dirname(os.path.abspath(__file__))
    for ruta in (os.environ.get(VAR_ICONO_DEV, ""), RUTA_ICONO_INSTALADA,
                 os.path.join(aqui, "..", "..", "overlay", "usr", "share", "infinity", "boveda.svg")):
        if ruta and os.path.exists(ruta):
            return os.path.abspath(ruta)
    return None


def _icono_del_tema(*nombres):
    """El primero de estos nombres que exista en el tema de iconos (Papirus/Adwaita)."""
    pantalla = Gdk.Display.get_default()
    tema = Gtk.IconTheme.get_for_display(pantalla) if pantalla is not None else None
    for n in nombres:
        if tema is not None and tema.has_icon(n):
            return n
    return nombres[-1]


def _imagen_icono(tam):
    """El icono de la app a `tam` px. Si la imagen no se puede cargar (p. ej. sin el cargador de SVG),
    un símbolo del tema: nunca queda el icono de «imagen que falta»."""
    ruta = ruta_icono()
    if ruta:
        try:
            img = Gtk.Image.new_from_paintable(Gdk.Texture.new_from_filename(ruta))
            img.set_pixel_size(tam)
            return img
        except GLib.Error:
            pass
    img = Gtk.Image.new_from_icon_name(_icono_del_tema("dialog-password-symbolic", "changes-prevent-symbolic",
                                                      "system-lock-screen-symbolic"))
    img.set_pixel_size(tam)
    return img


def _etiqueta(texto="", *clases, ajustar=False, selec=False, xalign=0.0, **props):
    e = Gtk.Label(label=texto, xalign=xalign, wrap=ajustar, selectable=selec, **props)
    if ajustar:
        e.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    for c in clases:
        e.add_css_class(c)
    return e


def _boton_icono(icono, tooltip, *clases):
    b = Gtk.Button(icon_name=icono, tooltip_text=tooltip)
    b.add_css_class("boveda-icono")
    for c in clases:
        b.add_css_class(c)
    b.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
    return b


def _escribible(campo, si):
    """Deja o no escribir en un campo de texto (y lo atenúa). No se usa set_sensitive: quitarle la sensibilidad a
    un campo que tiene el foco hace que GTK avise de un «focus-out» perdido."""
    campo.set_editable(si)
    campo.set_opacity(1.0 if si else 0.55)


def _vaciar(contenedor):
    hijo = contenedor.get_first_child()
    while hijo is not None:
        siguiente = hijo.get_next_sibling()
        contenedor.remove(hijo)
        hijo = siguiente


# ----------------------------------------------------------------------------------- medidor
class MedidorFortaleza(Gtk.Box):
    """Cinco segmentos que se encienden según boveda.evaluar_fortaleza, y debajo la etiqueta y el primer aviso."""

    def __init__(self):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        barra = Gtk.Box(spacing=4, homogeneous=True)
        self._segmentos = []
        for _ in range(5):
            seg = Gtk.Box(hexpand=True)
            seg.add_css_class("boveda-seg")
            barra.append(seg)
            self._segmentos.append(seg)
        self.etiqueta = _etiqueta("", "boveda-pequeno")
        self.append(barra)
        self.append(self.etiqueta)
        self.puntos = 0
        self.fortaleza = B.evaluar_fortaleza("")

    def actualizar(self, password):
        f = self.fortaleza = B.evaluar_fortaleza(password)
        self.puntos = f.puntos
        for i, seg in enumerate(self._segmentos):
            for c in ("on", "f0", "f1", "f2", "f3", "f4"):
                seg.remove_css_class(c)
            if password and i <= f.puntos:
                seg.add_css_class("on")
                seg.add_css_class(f"f{f.puntos}")
        if not password:
            self.etiqueta.set_text("")
        else:
            aviso = f" · {f.avisos[0]}" if f.avisos else ""
            self.etiqueta.set_text(f"{f.etiqueta}{aviso}")


# ----------------------------------------------------------------------------- portapapeles (GDK)
class PortapapelesGdk:
    """El portapapeles de GDK con la interfaz que espera boveda.PortapapelesTemporal. Al copiar marca el
    contenido como secreto (x-kde-passwordManagerHint) para los gestores de historial que lo respetan."""

    def __init__(self, pantalla):
        self._cb = pantalla.get_clipboard()
        self._nuestro = None

    def poner(self, texto):
        proveedor_texto = Gdk.ContentProvider.new_for_value(GObject.Value(str, texto))
        marca = Gdk.ContentProvider.new_for_bytes("x-kde-passwordManagerHint", GLib.Bytes.new(b"secret"))
        self._nuestro = Gdk.ContentProvider.new_union([proveedor_texto, marca])
        self._cb.set_content(self._nuestro)

    def leer(self, devolver):
        def listo(cb, resultado):
            try:
                texto = cb.read_text_finish(resultado)
            except GLib.Error:
                texto = None
            devolver(texto)
        self._cb.read_text_async(None, listo)

    def borrar(self):
        self._nuestro = None
        self._cb.set_content(None)

    def borrar_si_es_nuestro(self):
        """Borra solo si lo que hay en el portapapeles sigue siendo lo que pusimos (y no lo que copió luego otra cosa)."""
        if self._nuestro is not None and self._cb.get_content() is self._nuestro:
            self.borrar()


# ------------------------------------------------------------------------- formulario de contraseña
class _FormMaestra:
    """Los campos de una contraseña maestra: [actual], nueva, repetición, medidor, error y botón de acción.
    Se usa para CREAR la bóveda y para CAMBIAR la contraseña maestra."""

    def __init__(self, con_actual, texto_boton, texto_ocupado):
        self.caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.actual = None
        if con_actual:
            self.actual = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Contraseña actual")
            self.caja.append(self.actual)
        self.nueva = Gtk.PasswordEntry(show_peek_icon=True,
                                       placeholder_text=f"Contraseña {'nueva' if con_actual else 'maestra'} "
                                                        f"(mínimo {B.MIN_MAESTRA} caracteres)")
        self.medidor = MedidorFortaleza()
        self.repite = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Repite la contraseña")
        self.error = _etiqueta("", "boveda-error", ajustar=True)
        self.error.set_visible(False)
        self.actividad = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.spinner = Gtk.Spinner()
        self.texto_ocupado = _etiqueta(texto_ocupado, "boveda-subtitulo")
        self.actividad.append(self.spinner)
        self.actividad.append(self.texto_ocupado)
        self.actividad.set_visible(False)
        self.boton = Gtk.Button(label=texto_boton)
        self.boton.add_css_class("suggested-action")
        self.caja.append(self.nueva)
        self.caja.append(self.medidor)
        self.caja.append(self.repite)
        self.caja.append(self.error)
        self.caja.append(self.actividad)
        self.nueva.connect("changed", lambda e: self.medidor.actualizar(e.get_text()))

    def campos(self):
        return [c for c in (self.actual, self.nueva, self.repite) if c is not None]

    def ocupado(self, si):
        for c in self.campos():
            _escribible(c, not si)
        self.boton.set_sensitive(not si)
        self.actividad.set_visible(si)
        if si:
            self.spinner.start()
        else:
            self.spinner.stop()

    def mostrar_error(self, texto):
        self.error.set_text(texto)
        self.error.set_visible(bool(texto))

    def vaciar(self):
        for c in self.campos():
            c.set_text("")
        self.medidor.actualizar("")
        self.mostrar_error("")


# --------------------------------------------------------------------------------------- ventana
class VentanaBoveda(Gtk.ApplicationWindow):
    """La ventana de Contraseñas. Los parámetros con nombre existen para las pruebas (ruta de la bóveda, scrypt
    reducido, reloj y duración de inactividad, ejecutor síncrono, portapapeles doble); la aplicación no los pasa."""

    def __init__(self, app, *, ruta=None, kdf=None, limites=None, ahora=None, inactividad_s=INACTIVIDAD_S,
                 reloj=time.monotonic, ejecutor=None, portapapeles=None, segundos_portapapeles=PORTAPAPELES_S,
                 vigilar_suspension=True):
        super().__init__(application=app, title="Contraseñas", default_width=880, default_height=600)
        self.add_css_class("boveda-ventana")
        self.set_size_request(760, 500)
        _cargar_estilo(self.get_display())
        opciones = {k: v for k, v in (("kdf", kdf), ("limites", limites), ("ahora", ahora)) if v is not None}
        self.boveda = B.Boveda(ruta, **opciones)
        self._reloj = reloj
        self._inactividad_s = inactividad_s
        self._ultimo_uso = reloj()
        self._ejecutar = ejecutor or self._ejecutar_en_hilo
        self._ocupado = False
        self._epoca = 0            # sube al bloquear: un resultado de antes del bloqueo se descarta
        self._cerrada = False
        self._fallos = 0           # contraseñas incorrectas seguidas
        self._hasta = 0.0          # hora (del reloj) hasta la que no se deja reintentar
        self._usar_copia = False   # el próximo intento abre la .bak
        self._motivo_bloqueo = ""
        self._categoria = "Todas"
        self._seleccion = None     # id de la entrada que se ve en el detalle
        self._filas = {}           # id → fila de la lista
        self._contadores = {}      # categoría → etiqueta con su número
        self._reconstruyendo = False
        self._editando = None      # id de la entrada que se edita; None = una nueva
        self._borrando = None
        self._ids_tic = {"inactividad": 0, "espera": 0, "ocultar": 0, "barra": 0}
        self._bus = None
        self._suscripcion = 0
        self._portapapeles_real = portapapeles or PortapapelesGdk(self.get_display())
        self._copiado = B.PortapapelesTemporal(self._portapapeles_real, self._programar_gtk, self._cancelar_gtk,
                                               segundos_portapapeles, avisar=self._al_cambiar_portapapeles)

        self._construir()
        self._montar_controladores()
        self.connect("close-request", self._al_cerrar)
        if vigilar_suspension:
            self._vigilar_suspension()
        self._mostrar_acceso()

    # ======================================================================= construcción
    def _construir(self):
        cab = Gtk.HeaderBar()
        cab.set_decoration_layout(":minimize,maximize,close")
        cab.add_css_class("boveda-cabecera")
        cab.set_title_widget(Gtk.Box())
        marca = Gtk.Box(spacing=8)
        icono = Gtk.Image.new_from_icon_name(_icono_del_tema("dialog-password-symbolic", "changes-prevent-symbolic",
                                                             "system-lock-screen-symbolic"))
        icono.add_css_class("boveda-marca")
        marca.append(icono)
        marca.append(_etiqueta("Contraseñas", "boveda-marca"))
        cab.pack_start(marca)
        self.menu = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Menú")
        self.menu.update_property([Gtk.AccessibleProperty.LABEL], ["Menú"])
        self.menu.set_menu_model(self._modelo_menu())
        self.boton_bloquear = Gtk.Button(tooltip_text="Bloquear la bóveda (Ctrl+L)")
        contenido = Gtk.Box(spacing=6)
        contenido.append(Gtk.Image.new_from_icon_name(_icono_del_tema("changes-prevent-symbolic",
                                                                      "system-lock-screen-symbolic",
                                                                      "dialog-password-symbolic")))
        contenido.append(Gtk.Label(label="Bloquear"))
        self.boton_bloquear.set_child(contenido)
        self.boton_bloquear.add_css_class("boveda-bloquear")
        self.boton_bloquear.connect("clicked", lambda *_: self.bloquear())
        cab.pack_end(self.menu)
        cab.pack_end(self.boton_bloquear)
        self.set_titlebar(cab)

        self.pila = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140)
        self.pila.add_named(self._pagina_acceso(), "bloqueada")
        self.pila.add_named(self._pagina_abierta(), "abierta")
        self.pila.add_named(self._pagina_cambiar_maestra(), "maestra")
        self.set_child(self.pila)

    def _modelo_menu(self):
        for nombre, funcion in (("cambiar-maestra", self._abrir_cambiar_maestra), ("exportar", self._exportar),
                                ("acerca", self._acerca_de)):
            accion = Gio.SimpleAction.new(nombre, None)
            accion.connect("activate", lambda _a, _p, f=funcion: f())
            self.add_action(accion)
        menu = Gio.Menu()
        menu.append("Cambiar contraseña maestra…", "win.cambiar-maestra")
        menu.append("Exportar copia cifrada…", "win.exportar")
        menu.append("Acerca de Contraseñas", "win.acerca")
        return menu

    # ----------------------------------------------------------------------- pantalla bloqueada
    def _pagina_acceso(self):
        self.pila_acceso = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, transition_duration=140,
                                     halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vhomogeneous=False)
        self.pila_acceso.add_named(self._acceso_entrar(), "entrar")
        self.pila_acceso.add_named(self._acceso_crear(), "crear")
        return self.pila_acceso

    def _acceso_entrar(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, width_request=380)
        icono = _imagen_icono(96)
        icono.set_margin_bottom(6)
        caja.append(icono)
        caja.append(_etiqueta("Contraseñas", "boveda-titulo", xalign=0.5))
        self.subtitulo_entrar = _etiqueta("Escribe tu contraseña maestra para entrar.", "boveda-subtitulo",
                                          xalign=0.5, ajustar=True)
        caja.append(self.subtitulo_entrar)
        self.entrada_clave = Gtk.PasswordEntry(show_peek_icon=True, placeholder_text="Contraseña maestra")
        self.entrada_clave.set_margin_top(8)
        self.entrada_clave.connect("activate", lambda *_: self._entrar())
        caja.append(self.entrada_clave)
        self.boton_entrar = Gtk.Button(label="Entrar")
        self.boton_entrar.add_css_class("suggested-action")
        self.boton_entrar.connect("clicked", lambda *_: self._entrar())
        caja.append(self.boton_entrar)
        self.aviso_entrar = _etiqueta("", "boveda-error", ajustar=True, xalign=0.5)
        self.aviso_entrar.set_visible(False)
        caja.append(self.aviso_entrar)
        self.actividad_entrar = Gtk.Box(spacing=8, halign=Gtk.Align.CENTER)
        self.spinner_entrar = Gtk.Spinner()
        self.actividad_entrar.append(self.spinner_entrar)
        self.texto_actividad_entrar = _etiqueta("Comprobando…", "boveda-subtitulo")
        self.actividad_entrar.append(self.texto_actividad_entrar)
        self.actividad_entrar.set_visible(False)
        caja.append(self.actividad_entrar)
        self.boton_copia = Gtk.Button(label="Abrir la copia de seguridad")
        self.boton_copia.connect("clicked", lambda *_: self._abrir_copia())
        self.boton_copia.set_visible(False)
        caja.append(self.boton_copia)
        return caja

    def _acceso_crear(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=420)
        icono = _imagen_icono(72)
        icono.set_margin_bottom(4)
        caja.append(icono)
        caja.append(_etiqueta("Crea tu contraseña maestra", "boveda-titulo", xalign=0.5))
        caja.append(_etiqueta("Es la única que tendrás que recordar: protege todas las demás.",
                              "boveda-subtitulo", xalign=0.5, ajustar=True))
        aviso = Gtk.Box(spacing=10)
        aviso.add_css_class("boveda-aviso")
        aviso.set_margin_top(6)
        aviso.append(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        self.aviso_no_recuperable = _etiqueta(
            "Si la olvidas, tus contraseñas no se pueden recuperar. Nadie puede restablecerla: "
            "ni Infinity OS ni nadie más.", ajustar=True)
        self.aviso_no_recuperable.set_hexpand(True)
        aviso.append(self.aviso_no_recuperable)
        caja.append(aviso)
        self.form_crear = _FormMaestra(False, "Crear bóveda", "Creando la bóveda…")
        self.form_crear.caja.set_margin_top(6)
        self.form_crear.nueva.connect("activate", lambda *_: self.form_crear.repite.grab_focus())
        self.form_crear.repite.connect("activate", lambda *_: self._crear())
        self.form_crear.boton.connect("clicked", lambda *_: self._crear())
        caja.append(self.form_crear.caja)
        caja.append(self.form_crear.boton)
        return caja

    # ---------------------------------------------------------------- pantalla desbloqueada
    def _pagina_abierta(self):
        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        cuerpo = Gtk.Box(vexpand=True)
        raiz.append(cuerpo)

        # Barra lateral: categorías con su contador
        lateral = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=184)
        lateral.add_css_class("boveda-lateral")
        titulo = _etiqueta("CATEGORÍAS", "boveda-lateral-titulo")
        titulo.set_margin_start(18)
        titulo.set_margin_top(14)
        titulo.set_margin_bottom(8)
        lateral.append(titulo)
        self.lista_categorias = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista_categorias.add_css_class("boveda-categorias")
        self._filas_categoria = {}
        for nombre in ("Todas",) + B.CATEGORIAS:
            fila = Gtk.ListBoxRow()
            fila.categoria = nombre
            caja = Gtk.Box(spacing=8)
            caja.add_css_class("boveda-cat")
            etiqueta = _etiqueta(nombre)
            etiqueta.set_hexpand(True)
            cuenta = _etiqueta("0", "boveda-cuenta")
            caja.append(etiqueta)
            caja.append(cuenta)
            fila.set_child(caja)
            self.lista_categorias.append(fila)
            self._contadores[nombre] = cuenta
            self._filas_categoria[nombre] = fila
        self.lista_categorias.connect("row-selected", self._categoria_elegida)
        lateral.append(self.lista_categorias)
        lateral.append(Gtk.Box(vexpand=True))
        self.boton_nueva = Gtk.Button(label="+ Nueva entrada", tooltip_text="Nueva entrada (Ctrl+N)")
        self.boton_nueva.add_css_class("suggested-action")
        self.boton_nueva.connect("clicked", lambda *_: self._nueva_entrada())
        for lado in ("start", "end", "bottom"):
            getattr(self.boton_nueva, f"set_margin_{lado}")(12)
        lateral.append(self.boton_nueva)
        cuerpo.append(lateral)

        # Columna central: buscador, «Nueva entrada» y la lista
        medio = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=300)
        medio.add_css_class("boveda-medio")
        arriba = Gtk.Box(spacing=8)
        for lado in ("start", "end", "top", "bottom"):
            getattr(arriba, f"set_margin_{lado}")(12 if lado != "bottom" else 8)
        self.busqueda = Gtk.SearchEntry(placeholder_text="Buscar contraseñas…", hexpand=True)
        self.busqueda.connect("search-changed", lambda *_: self._refrescar())
        self.busqueda.connect("stop-search", lambda *_: self._escape())
        arriba.append(self.busqueda)
        medio.append(arriba)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("boveda-lista")
        self.lista.connect("row-selected", self._fila_elegida)
        scroll = Gtk.ScrolledWindow(vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroll.set_child(self.lista)
        medio.append(scroll)
        self.sin_resultados = _etiqueta("", "boveda-subtitulo", ajustar=True, xalign=0.5)
        self.sin_resultados.set_margin_start(18)
        self.sin_resultados.set_margin_end(18)
        self.sin_resultados.set_margin_bottom(14)
        self.sin_resultados.set_visible(False)
        medio.append(self.sin_resultados)
        cuerpo.append(medio)

        # Panel derecho: vacío / detalle / editor
        self.pila_detalle = Gtk.Stack(hexpand=True, vexpand=True, transition_type=Gtk.StackTransitionType.CROSSFADE,
                                      transition_duration=120)
        self.pila_detalle.add_named(self._pagina_vacia(), "vacio")
        self.pila_detalle.add_named(self._pagina_detalle(), "detalle")
        self.pila_detalle.add_named(self._pagina_editor(), "editor")
        cuerpo.append(self.pila_detalle)

        # Barra de avisos (copiado, guardado, errores)
        self.barra = Gtk.Box()
        self.barra.add_css_class("boveda-barra")
        self.texto_barra = _etiqueta("", xalign=0, ajustar=True)
        self.barra.append(self.texto_barra)
        self.barra.set_visible(False)
        raiz.append(self.barra)
        return raiz

    def _pagina_vacia(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                       valign=Gtk.Align.CENTER, width_request=300)
        caja.set_margin_start(24)
        caja.set_margin_end(24)
        icono = _imagen_icono(64)
        icono.set_opacity(0.85)
        caja.append(icono)
        self.vacio_titulo = _etiqueta("", "boveda-vacio-titulo", xalign=0.5, ajustar=True)
        self.vacio_texto = _etiqueta("", "boveda-subtitulo", xalign=0.5, ajustar=True)
        self.vacio_boton = Gtk.Button(label="Añadir una contraseña", halign=Gtk.Align.CENTER)
        self.vacio_boton.add_css_class("suggested-action")
        self.vacio_boton.connect("clicked", lambda *_: self._nueva_entrada())
        caja.append(self.vacio_titulo)
        caja.append(self.vacio_texto)
        caja.append(self.vacio_boton)
        return caja

    def _pagina_detalle(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        for lado in ("start", "end", "top", "bottom"):
            getattr(caja, f"set_margin_{lado}")(24 if lado in ("start", "end") else 20)

        cabecera = Gtk.Box(spacing=14)
        self.d_avatar = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER, hexpand=False)
        self.d_avatar.add_css_class("boveda-avatar")
        self.d_avatar.add_css_class("boveda-avatar-grande")
        self.d_inicial = _etiqueta("", xalign=0.5, halign=Gtk.Align.CENTER, hexpand=True)
        self.d_avatar.append(self.d_inicial)
        titulos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, valign=Gtk.Align.CENTER, hexpand=True)
        self.d_servicio = _etiqueta("", "boveda-d-titulo", ajustar=True)
        self.d_chip = Gtk.Box(halign=Gtk.Align.START)
        self.d_chip.add_css_class("boveda-chip")
        self.d_categoria = _etiqueta("")
        self.d_chip.append(self.d_categoria)
        titulos.append(self.d_servicio)
        titulos.append(self.d_chip)
        cabecera.append(self.d_avatar)
        cabecera.append(titulos)
        caja.append(cabecera)

        # Usuario
        self.d_usuario = _etiqueta("", "boveda-valor", selec=True, ajustar=True)
        self.d_usuario.set_hexpand(True)
        self.b_copiar_usuario = _boton_icono("edit-copy-symbolic", "Copiar usuario")
        self.b_copiar_usuario.connect("clicked", lambda *_: self._copiar("usuario"))
        caja.append(self._tarjeta("Usuario o correo", self.d_usuario, self.b_copiar_usuario))

        # Contraseña
        self.d_clave = _etiqueta(PUNTOS, "boveda-clave", ajustar=True)
        self.d_clave.set_hexpand(True)
        self.b_ver_clave = _boton_icono("view-reveal-symbolic", "Mostrar la contraseña")
        self.b_ver_clave.connect("clicked", lambda *_: self._alternar_clave())
        self.b_copiar_clave = _boton_icono("edit-copy-symbolic", "Copiar contraseña")
        self.b_copiar_clave.connect("clicked", lambda *_: self._copiar("contrasena"))
        self.tarjeta_clave = self._tarjeta("Contraseña", self.d_clave, self.b_ver_clave, self.b_copiar_clave)
        caja.append(self.tarjeta_clave)

        # URL / IP y notas (solo se ven si hay algo)
        self.d_url = _etiqueta("", "boveda-valor", selec=True, ajustar=True)
        self.tarjeta_url = self._tarjeta("Dirección (URL o IP)", self.d_url)
        caja.append(self.tarjeta_url)
        self.d_notas = _etiqueta("", "boveda-valor", selec=True, ajustar=True)
        self.tarjeta_notas = self._tarjeta("Notas", self.d_notas)
        caja.append(self.tarjeta_notas)

        self.d_fechas = _etiqueta("", "boveda-pequeno", ajustar=True)
        caja.append(self.d_fechas)

        relleno = Gtk.Box(vexpand=True)
        caja.append(relleno)
        self.caja_borrar = Gtk.Box(spacing=10)
        self.caja_borrar.add_css_class("boveda-confirmar")
        self.texto_borrar = _etiqueta("", ajustar=True)
        self.texto_borrar.set_hexpand(True)
        self.b_borrar_no = Gtk.Button(label="Cancelar")
        self.b_borrar_no.connect("clicked", lambda *_: self._cancelar_borrado())
        self.b_borrar_si = Gtk.Button(label="Sí, borrar")
        self.b_borrar_si.add_css_class("destructive-action")
        self.b_borrar_si.connect("clicked", lambda *_: self._borrar_confirmado())
        self.caja_borrar.append(self.texto_borrar)
        self.caja_borrar.append(self.b_borrar_no)
        self.caja_borrar.append(self.b_borrar_si)
        self.caja_borrar.set_visible(False)
        caja.append(self.caja_borrar)

        self.fila_acciones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.b_editar = Gtk.Button(label="Editar")
        self.b_editar.connect("clicked", lambda *_: self._editar_seleccionada())
        self.b_borrar = Gtk.Button(label="Borrar")
        self.b_borrar.connect("clicked", lambda *_: self._pedir_borrado())
        self.fila_acciones.append(self.b_editar)
        self.fila_acciones.append(self.b_borrar)
        caja.append(self.fila_acciones)
        return caja

    @staticmethod
    def _tarjeta(titulo, valor, *botones):
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tarjeta.add_css_class("boveda-tarjeta")
        tarjeta.append(_etiqueta(titulo, "boveda-etiqueta"))
        fila = Gtk.Box(spacing=6)
        fila.append(valor)
        for b in botones:
            b.set_valign(Gtk.Align.CENTER)
            fila.append(b)
        tarjeta.append(fila)
        return tarjeta

    def _pagina_editor(self):
        """El editor: los campos en una zona con desplazamiento y, fijos debajo, Cancelar y Guardar."""
        pagina = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        for lado in ("start", "end", "top", "bottom"):
            getattr(caja, f"set_margin_{lado}")(24 if lado in ("start", "end") else 16)
        self.e_titulo = _etiqueta("Nueva entrada", "boveda-d-titulo")
        caja.append(self.e_titulo)

        def campo(titulo, widget):
            caja.append(_etiqueta(titulo, "boveda-etiqueta"))
            caja.append(widget)

        self.e_servicio = Gtk.Entry(placeholder_text="Gmail, Router de casa, Banco…", max_length=B.LIMITES_CAMPO["servicio"])
        self.e_categoria = Gtk.DropDown.new_from_strings(list(B.CATEGORIAS))
        self.e_usuario = Gtk.Entry(placeholder_text="usuario o correo", max_length=B.LIMITES_CAMPO["usuario"])
        self.e_clave = Gtk.Entry(visibility=False, input_purpose=Gtk.InputPurpose.PASSWORD,
                                 max_length=B.LIMITES_CAMPO["contrasena"], hexpand=True,
                                 placeholder_text="contraseña")
        self.e_clave.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-reveal-symbolic")
        self.e_clave.set_icon_tooltip_text(Gtk.EntryIconPosition.SECONDARY, "Mostrar u ocultar")
        self.e_clave.connect("icon-press", lambda *_: self._alternar_visibilidad_editor())
        self.e_generar = Gtk.Button(label="Generar", tooltip_text="Crear una contraseña aleatoria")
        self.e_generar.connect("clicked", lambda *_: self._generar())
        fila_clave = Gtk.Box(spacing=8)
        fila_clave.append(self.e_clave)
        fila_clave.append(self.e_generar)
        self.e_medidor = MedidorFortaleza()
        self.e_clave.connect("changed", lambda e: self.e_medidor.actualizar(e.get_text()))
        self.e_url = Gtk.Entry(placeholder_text="https://… o 192.168.1.1", max_length=B.LIMITES_CAMPO["url"])
        self.e_notas = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=8, bottom_margin=8,
                                    left_margin=10, right_margin=10)
        notas = Gtk.ScrolledWindow(min_content_height=78, max_content_height=130, propagate_natural_height=True)
        notas.add_css_class("boveda-notas")
        notas.set_child(self.e_notas)

        self.e_servicio.set_hexpand(True)
        fila_servicio = Gtk.Grid(column_spacing=10, row_spacing=4)
        fila_servicio.attach(_etiqueta("Servicio", "boveda-etiqueta"), 0, 0, 1, 1)
        fila_servicio.attach(_etiqueta("Categoría", "boveda-etiqueta"), 1, 0, 1, 1)
        fila_servicio.attach(self.e_servicio, 0, 1, 1, 1)
        fila_servicio.attach(self.e_categoria, 1, 1, 1, 1)
        caja.append(fila_servicio)
        campo("Usuario o correo", self.e_usuario)
        caja.append(_etiqueta("Contraseña", "boveda-etiqueta"))
        caja.append(fila_clave)
        caja.append(self.e_medidor)

        # Generador: las opciones se recuerdan mientras la ventana siga abierta
        self.expansor = Gtk.Expander(label="Generador de contraseñas")
        opciones = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        opciones.set_margin_top(6)
        fila_long = Gtk.Box(spacing=10)
        fila_long.append(_etiqueta("Longitud"))
        self.g_longitud = Gtk.SpinButton.new_with_range(B.LONG_GENERADA[0], B.LONG_GENERADA[1], 1)
        self.g_longitud.set_value(20)
        fila_long.append(self.g_longitud)
        opciones.append(fila_long)
        self.g_mayusculas = Gtk.CheckButton(label="Mayúsculas (A-Z)", active=True)
        self.g_minusculas = Gtk.CheckButton(label="Minúsculas (a-z)", active=True)
        self.g_digitos = Gtk.CheckButton(label="Números (0-9)", active=True)
        self.g_simbolos = Gtk.CheckButton(label="Símbolos (!@#$…)", active=True)
        self.g_ambiguos = Gtk.CheckButton(label="Evitar caracteres ambiguos (0, O, l, 1, I)")
        self.g_ambiguos.set_margin_top(2)
        rejilla = Gtk.Grid(column_spacing=18, row_spacing=4)
        for i, c in enumerate((self.g_mayusculas, self.g_minusculas, self.g_digitos, self.g_simbolos)):
            rejilla.attach(c, i % 2, i // 2, 1, 1)
        rejilla.attach(self.g_ambiguos, 0, 2, 2, 1)
        opciones.append(rejilla)
        self.expansor.set_child(opciones)
        caja.append(self.expansor)

        campo("Dirección (URL o IP)", self.e_url)
        campo("Notas", notas)
        self.e_error = _etiqueta("", "boveda-error", ajustar=True)
        self.e_error.set_visible(False)
        caja.append(self.e_error)
        fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        for lado in ("start", "end", "top", "bottom"):
            getattr(fila, f"set_margin_{lado}")(24 if lado in ("start", "end") else 12)
        self.e_cancelar = Gtk.Button(label="Cancelar")
        self.e_cancelar.connect("clicked", lambda *_: self._cancelar_editor())
        self.e_guardar = Gtk.Button(label="Guardar")
        self.e_guardar.add_css_class("suggested-action")
        self.e_guardar.connect("clicked", lambda *_: self._guardar_editor())
        fila.append(self.e_cancelar)
        fila.append(self.e_guardar)
        for entrada in (self.e_servicio, self.e_usuario, self.e_clave, self.e_url):
            entrada.connect("activate", lambda *_: self._guardar_editor())
        scroll.set_child(caja)
        pagina.append(scroll)
        pagina.append(fila)
        return pagina

    # ------------------------------------------------------------- cambiar contraseña maestra
    def _pagina_cambiar_maestra(self):
        centro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, width_request=420)
        caja.append(_etiqueta("Cambiar contraseña maestra", "boveda-titulo", xalign=0.5))
        caja.append(_etiqueta("Se vuelve a cifrar todo con una clave nueva. La contraseña vieja dejará de valer, "
                              "también en la copia de seguridad.", "boveda-subtitulo", xalign=0.5, ajustar=True))
        aviso = Gtk.Box(spacing=10)
        aviso.add_css_class("boveda-aviso")
        aviso.append(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        texto = _etiqueta("Si la olvidas, tus contraseñas no se pueden recuperar.", ajustar=True)
        texto.set_hexpand(True)
        aviso.append(texto)
        caja.append(aviso)
        self.form_cambiar = _FormMaestra(True, "Cambiar contraseña", "Cambiando la contraseña…")
        self.form_cambiar.caja.set_margin_top(6)
        self.form_cambiar.actual.connect("activate", lambda *_: self.form_cambiar.nueva.grab_focus())
        self.form_cambiar.nueva.connect("activate", lambda *_: self.form_cambiar.repite.grab_focus())
        self.form_cambiar.repite.connect("activate", lambda *_: self._cambiar_maestra())
        self.form_cambiar.boton.connect("clicked", lambda *_: self._cambiar_maestra())
        self.form_cambiar_cancelar = Gtk.Button(label="Cancelar")
        self.form_cambiar_cancelar.connect("clicked", lambda *_: self._cerrar_cambiar_maestra())
        caja.append(self.form_cambiar.caja)
        fila = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        fila.append(self.form_cambiar_cancelar)
        fila.append(self.form_cambiar.boton)
        caja.append(fila)
        centro.append(caja)
        return centro

    # ======================================================================== controladores
    def _montar_controladores(self):
        """Cualquier tecla, clic o movimiento dentro de la ventana cuenta como actividad (y reinicia el
        temporizador de inactividad). Los atajos se resuelven en la misma fase de captura."""
        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)
        clic = Gtk.GestureClick()
        clic.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clic.connect("pressed", lambda *_: self._actividad())
        self.add_controller(clic)
        mov = Gtk.EventControllerMotion()
        mov.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        mov.connect("motion", lambda *_: self._actividad())
        self.add_controller(mov)
        rueda = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        rueda.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        rueda.connect("scroll", lambda *_: self._actividad() or False)
        self.add_controller(rueda)

    def _actividad(self):
        self._ultimo_uso = self._reloj()

    def _tecla(self, _ctrl, keyval, _codigo, estado):
        self._actividad()
        if not self.boveda.abierta:
            return False
        control = bool(estado & Gdk.ModifierType.CONTROL_MASK)
        if control and keyval in (Gdk.KEY_l, Gdk.KEY_L):
            self.bloquear()
            return True
        if self.pila.get_visible_child_name() != "abierta":
            if keyval == Gdk.KEY_Escape and self.pila.get_visible_child_name() == "maestra":
                self._cerrar_cambiar_maestra()
                return True
            return False
        if control and keyval in (Gdk.KEY_f, Gdk.KEY_F):
            self.busqueda.grab_focus()
            return True
        if control and keyval in (Gdk.KEY_n, Gdk.KEY_N):
            self._nueva_entrada()
            return True
        if keyval == Gdk.KEY_Escape:
            self._escape()
            return True
        return False

    def _escape(self):
        """Esc: cierra el editor si está abierto; si no, limpia la búsqueda."""
        if self.pila_detalle.get_visible_child_name() == "editor":
            self._cancelar_editor()
        elif self._borrando is not None:
            self._cancelar_borrado()
        else:
            self.busqueda.set_text("")
            self._refrescar()
            self.lista.grab_focus()

    # ===================================================================== hilos y espera
    def _ejecutar_en_hilo(self, trabajo, fin):
        """Corre `trabajo()` en un hilo y entrega (resultado, error) a `fin` en el hilo principal."""
        def corre():
            try:
                resultado = (trabajo(), None)
            except BaseException as e:  # noqa: BLE001  (se entrega al hilo principal)
                resultado = (None, e)
            GLib.idle_add(self._entregar, fin, resultado)
        threading.Thread(target=corre, daemon=True, name="boveda-kdf").start()

    @staticmethod
    def _entregar(fin, resultado):
        valor, error = resultado
        try:
            fin(valor, error)
        finally:
            if error is not None:
                error.__traceback__ = None  # los marcos de la traza llevan la contraseña en sus variables locales
        return False

    def _correr(self, trabajo, al_terminar, ocupar, desocupar):
        """Una operación larga (con scrypt): deshabilita la interfaz, corre `trabajo` fuera del hilo principal
        y vuelve a habilitarla antes de llamar a `al_terminar(resultado, error)` (si no hubo bloqueo entre medias)."""
        self._ocupado = True
        ocupar()
        epoca = self._epoca

        def fin(valor, error):
            self._ocupado = False
            if self._cerrada or epoca != self._epoca:
                if self.boveda.abierta:
                    self.boveda.bloquear()
                return
            desocupar()
            al_terminar(valor, error)

        self._ejecutar(trabajo, fin)

    # ===================================================================== acceso: entrar
    def _mostrar_acceso(self):
        """La pantalla bloqueada: crear la contraseña maestra si no hay bóveda, o pedirla."""
        self.menu.set_visible(False)
        self.boton_bloquear.set_visible(False)
        mensaje, es_error = "", False
        try:
            estado = self.boveda.estado()
        except (B.BovedaError, OSError) as e:
            estado, mensaje, es_error = "existe", self._texto_error(e), True
        self._usar_copia = estado == "solo_copia"
        if estado == "nueva":
            self.pila_acceso.set_visible_child_name("crear")
            self.pila.set_visible_child_name("bloqueada")
            self._enfocar(self.form_crear.nueva)
            return
        if self._motivo_bloqueo:
            mensaje, es_error = self._motivo_bloqueo, False
            self._motivo_bloqueo = ""
        elif estado == "solo_copia" and not mensaje:
            mensaje = "Falta el fichero principal de la bóveda: se usará la copia de seguridad."
        self.pila_acceso.set_visible_child_name("entrar")
        self.pila.set_visible_child_name("bloqueada")
        self.boton_copia.set_visible(False)
        self._mensaje_entrar(mensaje, es_error)
        self._actualizar_espera()
        self._enfocar(self.entrada_clave)

    def _enfocar(self, widget):
        widget.grab_focus()
        self.set_focus(widget)
        GLib.idle_add(lambda: widget.grab_focus() and False)

    def _mensaje_entrar(self, texto, error):
        self.aviso_entrar.set_text(texto)
        self.aviso_entrar.remove_css_class("boveda-error")
        self.aviso_entrar.remove_css_class("boveda-subtitulo")
        self.aviso_entrar.add_css_class("boveda-error" if error else "boveda-subtitulo")
        self.aviso_entrar.set_visible(bool(texto))

    def _esperando(self):
        return self._reloj() < self._hasta

    def _actualizar_espera(self):
        """Cuenta atrás tras fallos seguidos: mientras dura, no se puede reintentar."""
        if self._ids_tic["espera"]:
            GLib.source_remove(self._ids_tic["espera"])
            self._ids_tic["espera"] = 0
        resta = self._hasta - self._reloj()
        if resta > 0:
            self._mensaje_entrar(f"Contraseña incorrecta. Espera {math.ceil(resta)} s para volver a intentarlo.", error=True)
            _escribible(self.entrada_clave, False)
            self.boton_entrar.set_sensitive(False)
            self._ids_tic["espera"] = GLib.timeout_add(250, self._tic_espera)
        elif not self._ocupado:
            _escribible(self.entrada_clave, True)
            self.boton_entrar.set_sensitive(True)
            if self._fallos:
                self._mensaje_entrar("Contraseña incorrecta.", error=True)
            self._enfocar(self.entrada_clave)

    def _tic_espera(self):
        self._ids_tic["espera"] = 0  # este temporizador termina aquí (devuelve False)
        self._actualizar_espera()
        return False

    def _entrar(self):
        if self._ocupado or self._esperando() or self.boveda.abierta:
            return
        clave = self.entrada_clave.get_text()
        if not clave:
            self._mensaje_entrar("Escribe tu contraseña maestra.", error=True)
            return
        copia = self._usar_copia

        def ocupar():
            _escribible(self.entrada_clave, False)
            self.boton_entrar.set_sensitive(False)
            self.boton_copia.set_sensitive(False)
            self.aviso_entrar.set_visible(False)
            self.texto_actividad_entrar.set_text("Comprobando…")
            self.actividad_entrar.set_visible(True)
            self.spinner_entrar.start()

        def desocupar():
            self.actividad_entrar.set_visible(False)
            self.spinner_entrar.stop()
            _escribible(self.entrada_clave, True)
            self.boton_entrar.set_sensitive(True)
            self.boton_copia.set_sensitive(True)

        self._correr(lambda: self.boveda.abrir(clave, copia=copia), self._fin_entrar, ocupar, desocupar)

    def _abrir_copia(self):
        """El principal está dañado: el próximo intento abre la copia de seguridad (y la restaura)."""
        self._usar_copia = True
        self.boton_copia.set_visible(False)
        self._mensaje_entrar("Escribe tu contraseña maestra para abrir la copia de seguridad.", error=False)
        self._enfocar(self.entrada_clave)

    def _fin_entrar(self, _valor, error):
        if error is None:
            self._fallos = 0
            self._hasta = 0.0
            self._usar_copia = False
            self.entrada_clave.set_text("")
            self._mostrar_abierta()
            return
        self.entrada_clave.select_region(0, -1)
        if isinstance(error, B.ClaveIncorrecta):
            self._fallos += 1
            self._hasta = self._reloj() + B.espera_tras_fallos(self._fallos)
            self._mensaje_entrar("Contraseña incorrecta.", error=True)
            if self._fallos >= 3 and not self._usar_copia and self.boveda.hay_copia():
                # Una cabecera alterada también sale como «contraseña incorrecta»: tras varios fallos se ofrece la copia
                self.boton_copia.set_visible(True)
            self._actualizar_espera()
        elif isinstance(error, B.VersionNoSoportada):
            self._mensaje_entrar(str(error), error=True)
        elif isinstance(error, B.BovedaDanada):
            self._mensaje_entrar("El fichero de la bóveda parece dañado y no se puede leer.", error=True)
            self.boton_copia.set_visible(not self._usar_copia and self.boveda.hay_copia())
        elif isinstance(error, B.BovedaNoExiste):
            self._usar_copia = False
            self._mostrar_acceso()
        elif isinstance(error, (B.BovedaError, OSError)):
            self._mensaje_entrar(self._texto_error(error), error=True)
        else:
            self._mensaje_entrar("No se pudo abrir la bóveda.", error=True)

    @staticmethod
    def _texto_error(error):
        if isinstance(error, OSError):
            return f"No se pudo acceder al fichero de la bóveda: {error.strerror or 'error de entrada/salida'}."
        return str(error)

    # ====================================================================== acceso: crear
    def _crear(self):
        f = self.form_crear
        if self._ocupado:
            return
        nueva, repite = f.nueva.get_text(), f.repite.get_text()
        try:
            B.validar_maestra(nueva)
        except B.DatosInvalidos as e:
            f.mostrar_error(str(e))
            f.nueva.grab_focus()
            return
        if nueva != repite:
            f.mostrar_error("Las dos contraseñas no coinciden.")
            f.repite.grab_focus()
            return
        f.mostrar_error("")
        self._correr(lambda: self.boveda.crear(nueva), self._fin_crear,
                     lambda: f.ocupado(True), lambda: f.ocupado(False))

    def _fin_crear(self, _valor, error):
        f = self.form_crear
        if error is None:
            f.vaciar()
            self._fallos, self._hasta = 0, 0.0
            self._mostrar_abierta()
        elif isinstance(error, B.BovedaYaExiste):
            f.vaciar()
            self._mostrar_acceso()
        else:
            f.mostrar_error(self._texto_error(error) if isinstance(error, (B.BovedaError, OSError))
                            else "No se pudo crear la bóveda.")

    # ================================================================== desbloqueada / lista
    def _mostrar_abierta(self):
        self.menu.set_visible(True)
        self.boton_bloquear.set_visible(True)
        self._actividad()
        self._categoria = "Todas"
        self.lista_categorias.select_row(self._filas_categoria["Todas"])
        self.busqueda.set_text("")
        self._seleccion = None
        self._refrescar()
        self.pila.set_visible_child_name("abierta")
        self._iniciar_inactividad()
        self.busqueda.grab_focus()

    def _categoria_elegida(self, _lista, fila):
        if fila is None:
            return
        self._categoria = fila.categoria
        if self.boveda.abierta:
            self._refrescar()

    def _refrescar(self, seleccionar=None):
        """Reconstruye la lista según la búsqueda y la categoría, y actualiza los contadores."""
        if not self.boveda.abierta:
            return
        texto = self.busqueda.get_text()
        categoria = None if self._categoria == "Todas" else self._categoria
        entradas = self.boveda.buscar(texto, categoria)
        for nombre, n in self.boveda.contar_por_categoria().items():
            self._contadores[nombre].set_text(str(n))
        self._reconstruyendo = True
        try:
            _vaciar(self.lista)
            self._filas = {}
            for e in entradas:
                fila = self._nueva_fila(e)
                self.lista.append(fila)
                self._filas[e.id] = fila
            objetivo = seleccionar or self._seleccion
            if objetivo not in self._filas:
                objetivo = entradas[0].id if entradas else None
            if objetivo is not None:
                self.lista.select_row(self._filas[objetivo])
        finally:
            self._reconstruyendo = False
        hay_total = self.boveda.contar_por_categoria()["Todas"] > 0
        self.sin_resultados.set_visible(bool(not entradas and hay_total))
        if not entradas and hay_total:
            self.sin_resultados.set_text(f"Nada coincide con «{texto.strip()}»." if texto.strip()
                                         else "No hay contraseñas en esta categoría.")
        editor = self.pila_detalle.get_visible_child_name() == "editor"
        if objetivo is None:
            self._seleccion = None
            if not editor:
                self._mostrar_vacio(hay_total)
        elif not editor:
            self._mostrar_detalle(self.boveda.obtener(objetivo))

    def _nueva_fila(self, e):
        fila = Gtk.ListBoxRow()
        fila.entrada_id = e.id
        caja = Gtk.Box(spacing=12)
        caja.add_css_class("boveda-fila")
        avatar = Gtk.Box(halign=Gtk.Align.START, valign=Gtk.Align.CENTER, hexpand=False)
        avatar.add_css_class("boveda-avatar")
        inicial = _etiqueta((e.servicio[:1] or "?").upper(), xalign=0.5, halign=Gtk.Align.CENTER, hexpand=True)
        avatar.append(inicial)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        nombre = _etiqueta(e.servicio, "boveda-fila-nombre")
        nombre.set_ellipsize(Pango.EllipsizeMode.END)
        textos.append(nombre)
        usuario = _etiqueta(e.usuario or e.categoria, "boveda-fila-usuario")
        usuario.set_ellipsize(Pango.EllipsizeMode.END)
        textos.append(usuario)
        caja.append(avatar)
        caja.append(textos)
        fila.set_child(caja)
        return fila

    def _fila_elegida(self, _lista, fila):
        if self._reconstruyendo or fila is None or not self.boveda.abierta:
            return
        try:
            entrada = self.boveda.obtener(fila.entrada_id)
        except B.BovedaError:
            return
        self._cerrar_editor()  # si se estaba editando, se descarta: no deja contraseñas en los campos
        self._mostrar_detalle(entrada)

    def _mostrar_vacio(self, hay_entradas):
        self._ocultar_clave()
        self._cancelar_borrado(mostrar_acciones=False)
        if hay_entradas:
            self.vacio_titulo.set_text("Elige una contraseña")
            self.vacio_texto.set_text("Selecciónala en la lista para ver sus datos.")
            self.vacio_boton.set_visible(False)
        else:
            self.vacio_titulo.set_text("Tu bóveda está vacía")
            self.vacio_texto.set_text("Aún no hay contraseñas. Añade la de tu correo o la de tu router.")
            self.vacio_boton.set_visible(True)
        self.pila_detalle.set_visible_child_name("vacio")

    # ================================================================================ detalle
    def _mostrar_detalle(self, e):
        self._seleccion = e.id
        self._ocultar_clave()
        self._cancelar_borrado(mostrar_acciones=True)
        self.d_inicial.set_text((e.servicio[:1] or "?").upper())
        self.d_servicio.set_text(e.servicio)
        self.d_categoria.set_text(e.categoria)
        self.d_usuario.set_text(e.usuario or "—")
        self.b_copiar_usuario.set_sensitive(bool(e.usuario))
        self.d_clave.set_text(PUNTOS if e.contrasena else "—")
        self.b_copiar_clave.set_sensitive(bool(e.contrasena))
        self.b_ver_clave.set_sensitive(bool(e.contrasena))
        self.d_url.set_text(e.url)
        self.tarjeta_url.set_visible(bool(e.url))
        self.d_notas.set_text(e.notas)
        self.tarjeta_notas.set_visible(bool(e.notas))
        self.d_fechas.set_text(f"Creada: {B.fecha_legible(e.creada)}\nModificada: {B.fecha_legible(e.modificada)}")
        self.pila_detalle.set_visible_child_name("detalle")

    def _alternar_clave(self):
        if self._seleccion is None:
            return
        if self.b_ver_clave.get_icon_name() == "view-conceal-symbolic":
            self._ocultar_clave()
            return
        try:
            clave = self.boveda.obtener(self._seleccion).contrasena
        except B.BovedaError:
            return
        self.d_clave.set_text(clave)
        self.b_ver_clave.set_icon_name("view-conceal-symbolic")
        self.b_ver_clave.set_tooltip_text("Ocultar la contraseña")
        self._programar("ocultar", OCULTAR_CLAVE_S * 1000, self._ocultar_clave_auto)

    def _ocultar_clave_auto(self):
        self._ids_tic["ocultar"] = 0
        self._ocultar_clave()
        return False

    def _ocultar_clave(self):
        self._cancelar_tic("ocultar")
        if self.b_ver_clave.get_icon_name() == "view-conceal-symbolic":
            self.b_ver_clave.set_icon_name("view-reveal-symbolic")
            self.b_ver_clave.set_tooltip_text("Mostrar la contraseña")
        self.d_clave.set_text(PUNTOS if self._seleccion is not None else "")

    def _copiar(self, campo):
        if self._seleccion is None:
            return
        try:
            texto = getattr(self.boveda.obtener(self._seleccion), campo)
        except B.BovedaError:
            return
        if texto:
            self._copiado.copiar(texto)

    def _al_cambiar_portapapeles(self, estado):
        """boveda.PortapapelesTemporal avisa de lo que pasa con lo copiado."""
        if estado == "copiado":
            self._avisar(f"Copiado; se borrará en {self._copiado.segundos:g} s", segundos=0)
        elif estado == "borrado":
            self._avisar("Portapapeles borrado.")
        else:
            self._ocultar_barra()

    # ---- borrar
    def _pedir_borrado(self):
        if self._seleccion is None:
            return
        try:
            e = self.boveda.obtener(self._seleccion)
        except B.BovedaError:
            return
        self._borrando = e.id
        self.texto_borrar.set_text(f"¿Borrar «{e.servicio}»? No se puede deshacer.")
        self.caja_borrar.set_visible(True)
        self.fila_acciones.set_visible(False)
        self.b_borrar_no.grab_focus()  # el foco, en la opción segura

    def _cancelar_borrado(self, mostrar_acciones=True):
        self._borrando = None
        self.caja_borrar.set_visible(False)
        self.fila_acciones.set_visible(mostrar_acciones)

    def _borrar_confirmado(self):
        if self._borrando is None:
            return
        entrada_id, self._borrando = self._borrando, None
        try:
            self.boveda.borrar(entrada_id)
        except (B.BovedaError, OSError) as e:
            self._cancelar_borrado()
            self._avisar(f"No se pudo borrar: {self._texto_error(e)}", error=True)
            return
        self._seleccion = None
        self._refrescar()
        self._avisar("Entrada borrada.")

    # ================================================================================ editor
    def _nueva_entrada(self):
        if not self.boveda.abierta or self.pila.get_visible_child_name() != "abierta":
            return
        self._abrir_editor(None)

    def _editar_seleccionada(self):
        if self._seleccion is None:
            return
        try:
            self._abrir_editor(self.boveda.obtener(self._seleccion))
        except B.BovedaError:
            pass

    def _abrir_editor(self, entrada):
        self._editando = entrada.id if entrada else None
        self._ocultar_clave()
        self.e_titulo.set_text("Editar entrada" if entrada else "Nueva entrada")
        self.e_servicio.set_text(entrada.servicio if entrada else "")
        self.e_categoria.set_selected(B.CATEGORIAS.index(entrada.categoria) if entrada
                                      else B.CATEGORIAS.index(self._categoria) if self._categoria in B.CATEGORIAS else 0)
        self.e_usuario.set_text(entrada.usuario if entrada else "")
        self.e_clave.set_text(entrada.contrasena if entrada else "")
        self.e_clave.set_visibility(False)
        self.e_url.set_text(entrada.url if entrada else "")
        self.e_notas.get_buffer().set_text(entrada.notas if entrada else "")
        self.e_error.set_visible(False)
        self.e_medidor.actualizar(self.e_clave.get_text())
        self.pila_detalle.set_visible_child_name("editor")
        self.e_servicio.grab_focus()

    def _cerrar_editor(self):
        """Vacía los campos del editor (sin dejar contraseñas en ellos) y lo quita del panel: lo que se vea
        después (el detalle o el estado vacío) lo decide _refrescar o _mostrar_detalle."""
        self._editando = None
        if self.pila_detalle.get_visible_child_name() == "editor":
            self.pila_detalle.set_visible_child_name("vacio")
        for entrada in (self.e_servicio, self.e_usuario, self.e_clave, self.e_url):
            entrada.set_text("")
        self.e_notas.get_buffer().set_text("")
        self.e_medidor.actualizar("")
        self.e_error.set_visible(False)

    def _cancelar_editor(self):
        self._cerrar_editor()
        self._refrescar()
        self.lista.grab_focus()

    def _alternar_visibilidad_editor(self):
        visible = not self.e_clave.get_visibility()
        self.e_clave.set_visibility(visible)
        self.e_clave.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY,
                                             "view-conceal-symbolic" if visible else "view-reveal-symbolic")

    def _generar(self):
        try:
            clave = B.generar_contrasena(self.g_longitud.get_value_as_int(), self.g_mayusculas.get_active(),
                                         self.g_minusculas.get_active(), self.g_digitos.get_active(),
                                         self.g_simbolos.get_active(), self.g_ambiguos.get_active())
        except B.DatosInvalidos as e:
            self._error_editor(str(e))
            self.expansor.set_expanded(True)
            return
        self.e_error.set_visible(False)
        self.e_clave.set_text(clave)
        self.e_clave.set_visibility(True)  # que la veas antes de guardarla
        self.e_clave.set_icon_from_icon_name(Gtk.EntryIconPosition.SECONDARY, "view-conceal-symbolic")

    def _error_editor(self, texto):
        self.e_error.set_text(texto)
        self.e_error.set_visible(True)

    def _guardar_editor(self):
        buffer = self.e_notas.get_buffer()
        campos = dict(servicio=self.e_servicio.get_text(), usuario=self.e_usuario.get_text(),
                      contrasena=self.e_clave.get_text(), url=self.e_url.get_text(),
                      notas=buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False),
                      categoria=B.CATEGORIAS[self.e_categoria.get_selected()])
        try:
            if self._editando is None:
                guardada = self.boveda.agregar(**campos)
            else:
                guardada = self.boveda.actualizar(self._editando, **campos)
        except (B.BovedaError, OSError) as e:
            self._error_editor(self._texto_error(e))
            return
        self._cerrar_editor()
        self._seleccion = guardada.id
        self._categoria_visible_para(guardada)
        self._refrescar(seleccionar=guardada.id)
        self._avisar("Guardado.")
        self.lista.grab_focus()

    def _categoria_visible_para(self, entrada):
        """Si el filtro (categoría o búsqueda) escondería la entrada recién guardada, se quita el filtro."""
        if self._categoria not in ("Todas", entrada.categoria):
            self._categoria = "Todas"
            self.lista_categorias.select_row(self._filas_categoria["Todas"])
        if self.busqueda.get_text() and entrada not in self.boveda.buscar(self.busqueda.get_text()):
            self.busqueda.set_text("")

    # ============================================================ contraseña maestra / copia / acerca
    def _abrir_cambiar_maestra(self):
        if not self.boveda.abierta or self._ocupado:
            return
        self.form_cambiar.vaciar()
        self.pila.set_visible_child_name("maestra")
        self.form_cambiar.actual.grab_focus()

    def _cerrar_cambiar_maestra(self):
        self.form_cambiar.vaciar()
        if self.boveda.abierta:
            self.pila.set_visible_child_name("abierta")

    def _cambiar_maestra(self):
        f = self.form_cambiar
        if self._ocupado or not self.boveda.abierta:
            return
        actual, nueva, repite = f.actual.get_text(), f.nueva.get_text(), f.repite.get_text()
        if not actual:
            f.mostrar_error("Escribe tu contraseña actual.")
            f.actual.grab_focus()
            return
        try:
            B.validar_maestra(nueva)
        except B.DatosInvalidos as e:
            f.mostrar_error(str(e))
            f.nueva.grab_focus()
            return
        if nueva != repite:
            f.mostrar_error("Las dos contraseñas nuevas no coinciden.")
            f.repite.grab_focus()
            return
        f.mostrar_error("")
        self._correr(lambda: self.boveda.cambiar_password(actual, nueva), self._fin_cambiar_maestra,
                     lambda: f.ocupado(True), lambda: f.ocupado(False))

    def _fin_cambiar_maestra(self, _valor, error):
        f = self.form_cambiar
        if error is None:
            f.vaciar()
            self.pila.set_visible_child_name("abierta")
            self._avisar("Contraseña maestra cambiada. La anterior ya no vale.")
        elif isinstance(error, B.ClaveIncorrecta):
            f.mostrar_error("La contraseña actual no es correcta.")
            f.actual.grab_focus()
        elif isinstance(error, (B.BovedaError, OSError)):
            f.mostrar_error(self._texto_error(error))
        else:
            f.mostrar_error("No se pudo cambiar la contraseña maestra.")

    def _exportar(self):
        if not self.boveda.abierta:
            return
        dialogo = Gtk.FileDialog(title="Exportar copia cifrada", initial_name="contrasenas-copia.enc", modal=True)

        def listo(d, resultado):
            try:
                archivo = d.save_finish(resultado)
            except GLib.Error:
                return  # cancelado
            ruta = archivo.get_path() if archivo is not None else None
            if ruta:
                self.exportar_a(ruta)
            else:
                self._avisar("Elige una carpeta de este equipo.", error=True)

        dialogo.save(self, None, listo)

    def exportar_a(self, ruta):
        """Copia el fichero cifrado a `ruta` (0600). Sirve para guardar una copia en un pendrive."""
        try:
            self.boveda.exportar(ruta)
        except (B.BovedaError, OSError) as e:
            self._avisar(f"No se pudo exportar: {self._texto_error(e)}", error=True)
            return
        self._avisar(f"Copia cifrada guardada en {ruta}. Solo se abre con tu contraseña maestra.", segundos=10)

    def _acerca_de(self):
        acerca = Gtk.AboutDialog(transient_for=self, modal=True, program_name="Contraseñas",
                                 comments="El gestor de contraseñas de Infinity OS.\n\n"
                                          "Tus contraseñas se guardan cifradas en este equipo (AES-256-GCM, con una "
                                          "clave derivada de tu contraseña maestra con scrypt). Sin red y sin telemetría. "
                                          "Si olvidas la contraseña maestra, no hay forma de recuperarlas.")
        acerca.present()

    # ========================================================================= barra de avisos
    def _avisar(self, texto, error=False, segundos=6):
        """Un aviso abajo. `segundos=0`: se queda hasta que lo cambie otro (p. ej. «copiado», que lo cambia el portapapeles)."""
        self._cancelar_tic("barra")
        self.texto_barra.set_text(texto)
        (self.barra.add_css_class if error else self.barra.remove_css_class)("error")
        self.barra.set_visible(True)
        if segundos:
            self._programar("barra", int(segundos * 1000), self._ocultar_barra_auto)

    def _ocultar_barra_auto(self):
        self._ids_tic["barra"] = 0
        self._ocultar_barra()
        return False

    def _ocultar_barra(self):
        self._cancelar_tic("barra")
        self.texto_barra.set_text("")
        self.barra.set_visible(False)

    # ==================================================================== temporizadores
    @staticmethod
    def _programar_gtk(segundos, funcion):
        """Para boveda.PortapapelesTemporal: llama a `funcion` dentro de `segundos` (pueden ser fracciones)."""
        return GLib.timeout_add(max(1, int(segundos * 1000)), funcion)

    @staticmethod
    def _cancelar_gtk(tid):
        try:
            GLib.source_remove(tid)
        except Exception:  # noqa: BLE001  (ya había saltado)
            pass

    def _programar(self, nombre, ms, funcion):
        self._cancelar_tic(nombre)
        self._ids_tic[nombre] = GLib.timeout_add(ms, funcion)

    def _cancelar_tic(self, nombre):
        if self._ids_tic.get(nombre):
            GLib.source_remove(self._ids_tic[nombre])
            self._ids_tic[nombre] = 0

    def _iniciar_inactividad(self):
        intervalo = int(min(1000, max(100, self._inactividad_s * 1000 / 3)))
        self._programar("inactividad", intervalo, self._revisar_inactividad)

    def _revisar_inactividad(self):
        """Cada segundo: ¿pasó el tiempo sin que nadie tocara nada? Entonces, a bloquear."""
        if not self.boveda.abierta:
            self._ids_tic["inactividad"] = 0
            return False
        if self._reloj() - self._ultimo_uso >= self._inactividad_s:
            self._ids_tic["inactividad"] = 0
            self.bloquear("La bóveda se bloqueó por inactividad.")
            return False
        return True

    # ============================================================================ bloquear
    def bloquear(self, motivo=""):
        """Bloquea: suelta la clave y las entradas, vacía los widgets, limpia el portapapeles si aún tiene lo
        copiado y vuelve a la pantalla de acceso. Python no garantiza borrar la memoria (docs/boveda.md): es todo
        lo que se puede hacer desde aquí."""
        self._epoca += 1
        for nombre in list(self._ids_tic):
            self._cancelar_tic(nombre)
        self._copiado.limpiar_ya()
        self.boveda.bloquear()
        self._limpiar_vista()
        self._motivo_bloqueo = motivo
        if not self._cerrada:
            self._mostrar_acceso()
        gc.collect()

    def _limpiar_vista(self):
        """Ningún dato de la bóveda queda en los widgets."""
        self._reconstruyendo = True
        try:
            _vaciar(self.lista)
        finally:
            self._reconstruyendo = False
        self._filas = {}
        self._seleccion = self._borrando = self._editando = None
        for etiqueta in (self.d_inicial, self.d_servicio, self.d_categoria, self.d_usuario, self.d_notas, self.d_url,
                         self.d_fechas, self.texto_borrar):
            etiqueta.set_text("")
        self.d_clave.set_text("")
        self.b_ver_clave.set_icon_name("view-reveal-symbolic")
        self.caja_borrar.set_visible(False)
        self.fila_acciones.set_visible(True)
        self._cerrar_editor()
        self.busqueda.set_text("")
        self._ocultar_barra()
        self.form_cambiar.vaciar()
        self.entrada_clave.set_text("")
        for nombre, etiqueta in self._contadores.items():
            etiqueta.set_text("0")
        self._categoria = "Todas"
        self.lista_categorias.select_row(self._filas_categoria["Todas"])
        self.pila_detalle.set_visible_child_name("vacio")

    # ================================================================== suspensión (logind)
    def _vigilar_suspension(self):
        """Bloquea al suspender: señal PrepareForSleep de org.freedesktop.login1 en el bus del sistema. Todo
        defensivo: sin bus (un contenedor, una sesión rara) simplemente no se vigila."""
        try:
            Gio.bus_get(Gio.BusType.SYSTEM, None, self._bus_listo)
        except (GLib.Error, TypeError):
            pass

    def _bus_listo(self, _origen, resultado):
        try:
            conexion = Gio.bus_get_finish(resultado)
        except GLib.Error:
            return
        if self._cerrada or conexion is None:
            return
        try:
            self._suscripcion = conexion.signal_subscribe(
                "org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                "/org/freedesktop/login1", None, Gio.DBusSignalFlags.NONE, self._senal_suspension)
            self._bus = conexion
        except GLib.Error:
            self._bus = None

    def _senal_suspension(self, _conexion, _remitente, _ruta, _interfaz, _senal, parametros):
        try:
            durmiendo = bool(parametros.unpack()[0])
        except (AttributeError, IndexError, TypeError, GLib.Error):
            durmiendo = True  # ante la duda, bloquear
        if durmiendo and self.boveda.abierta:
            self.bloquear("La bóveda se bloqueó al suspender el equipo.")

    # ============================================================================= cierre
    def _al_cerrar(self, *_):
        """Cerrar la ventana termina la aplicación: la próxima vez arranca bloqueada. Antes, todo se suelta."""
        if self._cerrada:
            return False
        self._cerrada = True
        self._epoca += 1
        for nombre in list(self._ids_tic):
            self._cancelar_tic(nombre)
        self._copiado.limpiar_ya()
        self.boveda.bloquear()
        self._limpiar_vista()
        if self._bus is not None and self._suscripcion:
            try:
                self._bus.signal_unsubscribe(self._suscripcion)
            except GLib.Error:
                pass
            self._suscripcion = 0
        gc.collect()
        return False


# ------------------------------------------------------------------------------------ aplicación
class AppBoveda(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.ventana = None

    def do_activate(self):
        # Una sola instancia: si ya está abierta, se trae al frente tal como esté (bloqueada o no)
        if self.ventana is None:
            ajustes_gtk = Gtk.Settings.get_default()  # None si GTK no tiene pantalla
            if ajustes_gtk is not None:
                ajustes_gtk.set_property("gtk-application-prefer-dark-theme", True)
            self.ventana = VentanaBoveda(self)
        self.ventana.present()

    def do_shutdown(self):
        if self.ventana is not None:
            self.ventana._al_cerrar()
        Gtk.Application.do_shutdown(self)


def main():
    B.endurecer_proceso()
    if B.AESGCM is None:
        sys.stderr.write("Contraseñas: falta el paquete python3-cryptography; no se puede abrir la bóveda.\n")
        return 1
    return AppBoveda().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
