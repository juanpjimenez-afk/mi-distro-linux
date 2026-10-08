"""Biblioteca de apps: rejilla de apps y carpetas que se pueden organizar arrastrando.

    · arrastrar una app sobre otra  → crea una carpeta con las dos
    · arrastrar una app sobre una carpeta → la mete dentro
    · arrastrar una app dentro de una carpeta abierta sobre «←» → la saca
    · clic derecho en una carpeta → renombrar o eliminar (las apps vuelven a la vista principal)

Se abre desde el botón de rejilla del dock, como una hoja de cristal sobre el dock.
"""
import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from .biblioteca import Carpeta  # noqa: E402
from .dock import ALTO, MARGEN  # noqa: E402

PREFIJO_APP = "app:"  # contenido del arrastre: "app:<id del .desktop>"
TAM_ICONO = 48
TAM_MINI = 20
COLUMNAS = 5  # tarjetas por fila (el panel tiene 720 px de ancho)


def _icono(icono, tam):
    if icono is None:
        img = Gtk.Image.new_from_icon_name("application-x-executable")
    else:
        img = Gtk.Image.new_from_gicon(icono)
    img.set_pixel_size(tam)
    return img


class VentanaBiblioteca(Gtk.Window):
    def __init__(self, app, indice, biblioteca):
        super().__init__(application=app, title="Infinity — Biblioteca")
        self.indice = indice
        self.bib = biblioteca
        self.carpeta_abierta = None  # cid de la carpeta que se está viendo, o None

        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "infinity-biblioteca")
        LayerShell.set_layer(self, LayerShell.Layer.OVERLAY)
        for borde in (LayerShell.Edge.TOP, LayerShell.Edge.BOTTOM, LayerShell.Edge.LEFT, LayerShell.Edge.RIGHT):
            LayerShell.set_anchor(self, borde, True)
        LayerShell.set_exclusive_zone(self, -1)
        LayerShell.set_keyboard_mode(self, LayerShell.KeyboardMode.EXCLUSIVE)
        self.add_css_class("infinity-window")

        # Capa transparente a pantalla completa: un clic fuera del panel lo cierra.
        self.fondo = Gtk.Box()
        self.fondo.set_hexpand(True)
        self.fondo.set_vexpand(True)
        self.set_child(self.fondo)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic_fondo)
        self.fondo.add_controller(clic)

        self.panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.panel.add_css_class("glass")
        self.panel.add_css_class("biblioteca")
        self.panel.set_halign(Gtk.Align.CENTER)
        self.panel.set_valign(Gtk.Align.END)
        self.panel.set_margin_bottom(MARGEN + ALTO + 12)  # encima del dock
        self.panel.set_size_request(720, -1)
        self.fondo.append(self.panel)

        cabecera = Gtk.Box(spacing=8)
        cabecera.add_css_class("biblioteca-cabecera")
        self.atras = Gtk.Button()
        self.atras.set_child(_icono_nombre("go-previous-symbolic", 16))
        self.atras.add_css_class("lib-back")
        self.atras.set_tooltip_text("Volver a la biblioteca")
        self.atras.connect("clicked", lambda *_: self._abrir_carpeta(None))
        self._soltar_sobre(self.atras, lambda origen, _destino: self.bib.sacar(origen))
        cabecera.append(self.atras)
        self.titulo = Gtk.Label(xalign=0)
        self.titulo.add_css_class("lib-title")
        self.titulo.set_hexpand(True)
        cabecera.append(self.titulo)
        self.filtro = Gtk.ToggleButton(label="Solo descargadas")
        self.filtro.add_css_class("lib-toggle")
        self.filtro.set_tooltip_text("Ocultar las apps que vienen con el sistema")
        self.filtro.set_active(self.bib.solo_descargadas)
        self._marcar_filtro(self.bib.solo_descargadas)
        self.filtro.connect("toggled", self._cambiar_filtro)
        cabecera.append(self.filtro)
        self.panel.append(cabecera)

        # Gtk.Grid y no Gtk.FlowBox: el FlowBox se queda con los clics de sus hijos.
        self.rejilla = Gtk.Grid(column_spacing=10, row_spacing=10, halign=Gtk.Align.CENTER)
        self.rejilla.add_css_class("lib-grid")
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_child(self.rejilla)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(460)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.panel.append(self.scroll)

        self.vacio = Gtk.Label(xalign=0.5)
        self.vacio.add_css_class("lib-empty")
        self.panel.append(self.vacio)

        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)

    # ------------------------------------------------------------ abrir/cerrar
    def alternar(self):
        if self.get_visible():
            self.cerrar()
        else:
            self.abrir()

    def abrir(self):
        self.carpeta_abierta = None
        self._redibujar()
        self.present()

    def cerrar(self):
        self.set_visible(False)

    def _clic_fondo(self, _gesto, _n, x, y):
        """Un clic fuera del panel cierra la biblioteca (comparando con la caja del panel)."""
        ok, caja = self.panel.compute_bounds(self.fondo)
        if ok and caja.get_x() <= x <= caja.get_x() + caja.get_width() \
                and caja.get_y() <= y <= caja.get_y() + caja.get_height():
            return
        self.cerrar()

    def _tecla(self, _ctrl, keyval, _code, _estado):
        if keyval == Gdk.KEY_Escape:
            if self.carpeta_abierta is not None:
                self._abrir_carpeta(None)
            else:
                self.cerrar()
            return True
        return False

    def _cambiar_filtro(self, boton):
        self.bib.solo_descargadas = boton.get_active()
        self._marcar_filtro(self.bib.solo_descargadas)
        self._guardar_y_redibujar()

    def _marcar_filtro(self, activo):
        if activo:
            self.filtro.add_css_class("on")
        else:
            self.filtro.remove_css_class("on")

    # ---------------------------------------------------------------- vista
    def _abrir_carpeta(self, cid):
        self.carpeta_abierta = cid
        self._redibujar()

    def _redibujar(self):
        while (hijo := self.rejilla.get_first_child()) is not None:
            self.rejilla.remove(hijo)
        vista = self.bib.vista(self.indice.apps)
        elementos, titulo = vista, "Biblioteca"
        if self.carpeta_abierta is not None:
            carpeta = next((i for i in vista if isinstance(i, Carpeta) and i.cid == self.carpeta_abierta), None)
            if carpeta is None:  # la carpeta ya no existe o no tiene apps visibles
                self.carpeta_abierta = None
            else:
                elementos, titulo = carpeta.apps, carpeta.nombre
        self.titulo.set_text(titulo)
        self.atras.set_visible(self.carpeta_abierta is not None)
        dentro = self.carpeta_abierta is not None
        for i, el in enumerate(elementos):
            if isinstance(el, Carpeta):
                tarjeta = self._tile_carpeta(el)
            else:
                tarjeta = self._tile_app(el, dentro)
            self.rejilla.attach(tarjeta, i % COLUMNAS, i // COLUMNAS, 1, 1)
        self.vacio.set_visible(not elementos)
        self.vacio.set_text(
            "Aún no hay apps para mostrar." if self.bib.solo_descargadas
            else "Aún no hay apps en el sistema.")
        self.scroll.set_visible(bool(elementos))

    def _tile_app(self, app, dentro):
        boton = Gtk.Button()
        boton.add_css_class("lib-tile")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        caja.append(_icono(app.icono, TAM_ICONO))
        nombre = _etiqueta_nombre(app.nombre)
        caja.append(nombre)
        boton.set_child(caja)
        boton.set_tooltip_text(app.nombre)
        boton.connect("clicked", lambda *_: self._lanzar(app))

        derecho = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        derecho.connect("pressed", lambda *_: self._menu_app(boton, app, dentro))
        boton.add_controller(derecho)
        self._arrastrar(boton, app.id)
        self._soltar_sobre(boton, lambda origen, destino: self.bib.soltar(origen, destino), app.id)
        return boton

    def _tile_carpeta(self, carpeta):
        boton = Gtk.Button()
        boton.add_css_class("lib-tile")
        boton.add_css_class("lib-folder")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        mini = Gtk.Grid(row_spacing=3, column_spacing=3)
        mini.set_halign(Gtk.Align.CENTER)
        for i in range(4):
            if i < len(carpeta.apps):
                celda = _icono(carpeta.apps[i].icono, TAM_MINI)
            else:
                celda = Gtk.Box()
                celda.set_size_request(TAM_MINI, TAM_MINI)
            mini.attach(celda, i % 2, i // 2, 1, 1)
        caja.append(mini)
        nombre = _etiqueta_nombre(carpeta.nombre)
        caja.append(nombre)
        boton.set_child(caja)
        boton.set_tooltip_text(carpeta.nombre)
        boton.connect("clicked", lambda *_: self._abrir_carpeta(carpeta.cid))

        derecho = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY)
        derecho.connect("pressed", lambda *_: self._menu_carpeta(boton, carpeta))
        boton.add_controller(derecho)
        self._soltar_sobre(boton, lambda origen, _destino: self.bib.agregar(origen, carpeta.cid))
        return boton

    # ------------------------------------------------------- arrastrar y soltar
    def _arrastrar(self, widget, app_id):
        origen = Gtk.DragSource()
        origen.set_actions(Gdk.DragAction.MOVE)
        origen.connect("prepare", lambda *_: Gdk.ContentProvider.new_for_value(PREFIJO_APP + app_id))
        widget.add_controller(origen)

    def _soltar_sobre(self, widget, accion, destino_app=None):
        """accion(origen_id, destino_id) cuando se suelta una app sobre `widget`.

        `destino_app` es el id de la app sobre la que se suelta (None en «←» y en carpetas).
        """
        destino = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        destino.connect("enter", lambda *_: (widget.add_css_class("drop-target"), Gdk.DragAction.MOVE)[1])
        destino.connect("leave", lambda *_: widget.remove_css_class("drop-target"))

        def al_soltar(_t, valor, _x, _y):
            widget.remove_css_class("drop-target")
            if not isinstance(valor, str) or not valor.startswith(PREFIJO_APP):
                return False
            origen = valor[len(PREFIJO_APP):]
            accion(origen, destino_app)
            self._guardar_y_redibujar()
            return True

        destino.connect("drop", al_soltar)
        widget.add_controller(destino)

    # ------------------------------------------------------------------ menús
    def _menu(self, widget, filas):
        pop = Gtk.Popover()
        pop.set_parent(widget)
        pop.set_position(Gtk.PositionType.TOP)
        pop.add_css_class("dock-menu")
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for texto, fn in filas:
            b = Gtk.Button(label=texto)
            b.add_css_class("menu-item")
            b.set_halign(Gtk.Align.FILL)
            b.connect("clicked", lambda *_, fn=fn: (pop.popdown(), fn()))
            caja.append(b)
        pop.set_child(caja)
        pop.connect("closed", lambda p: GLib.idle_add(_desligar, p))
        pop.popup()

    def _menu_app(self, widget, app, dentro):
        filas = [("Abrir", lambda: self._lanzar(app))]
        if dentro:
            filas.append(("Sacar de la carpeta", lambda: (self.bib.sacar(app.id), self._guardar_y_redibujar())))
        self._menu(widget, filas)

    def _menu_carpeta(self, widget, carpeta):
        filas = [
            ("Renombrar…", lambda: self._renombrar(widget, carpeta)),
            ("Eliminar carpeta", lambda: (self.bib.eliminar(carpeta.cid), self._guardar_y_redibujar())),
        ]
        self._menu(widget, filas)

    def _renombrar(self, widget, carpeta):
        pop = Gtk.Popover()
        pop.set_parent(widget)
        pop.set_position(Gtk.PositionType.TOP)
        pop.add_css_class("dock-menu")
        caja = Gtk.Box(spacing=6)
        caja.set_margin_start(6)
        caja.set_margin_end(6)
        entrada = Gtk.Entry()
        entrada.set_text(carpeta.nombre)
        entrada.set_max_length(40)
        entrada.set_width_chars(16)
        caja.append(entrada)

        def guardar(*_):
            self.bib.renombrar(carpeta.cid, entrada.get_text())
            pop.popdown()
            self._guardar_y_redibujar()

        entrada.connect("activate", guardar)
        boton = Gtk.Button(label="Guardar")
        boton.add_css_class("menu-item")
        boton.connect("clicked", guardar)
        caja.append(boton)
        pop.set_child(caja)
        pop.connect("closed", lambda p: GLib.idle_add(_desligar, p))
        pop.popup()
        entrada.grab_focus()

    # ------------------------------------------------------------ utilidades
    def _lanzar(self, app):
        self.cerrar()
        app.lanzar()

    def _guardar_y_redibujar(self):
        self.bib.guardar()
        # Diferido: redibujar dentro de un arrastre destruiría el origen del arrastre en curso.
        GLib.idle_add(self._redibujar_diferido)

    def _redibujar_diferido(self):
        self._redibujar()
        return False


def _etiqueta_nombre(texto):
    """Nombre de una tarjeta: hasta dos líneas centradas, sin cortar la palabra."""
    etiqueta = Gtk.Label(label=texto)
    etiqueta.add_css_class("lib-name")
    etiqueta.set_wrap(True)
    etiqueta.set_max_width_chars(12)
    etiqueta.set_justify(Gtk.Justification.CENTER)
    return etiqueta


def _desligar(popover):
    """Quita el popover de su padre si sigue colgando (el padre puede haberse redibujado)."""
    if popover.get_parent() is not None:
        popover.unparent()
    return False


def _icono_nombre(nombre, tam):
    img = Gtk.Image.new_from_icon_name(nombre)
    img.set_pixel_size(tam)
    return img
