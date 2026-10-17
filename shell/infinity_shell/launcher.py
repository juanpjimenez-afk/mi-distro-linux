"""Buscador rápido + apagado. Se abre sobre el orbe (logo) como una hoja de cristal."""
import subprocess
from urllib.parse import quote_plus

import gi

gi.require_version("Gtk4LayerShell", "1.0")
from gi.repository import Gtk4LayerShell as LayerShell  # noqa: E402  (antes que Gtk)

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from . import power  # noqa: E402
from .ajustes import valor as ajuste  # noqa: E402
from .dock import asset  # noqa: E402
from .search import calcular, normalizar  # noqa: E402

MAX_FILAS = 8
# Tras la última tecla se busca al cabo de este tiempo: al teclear deprisa no se reconstruye la
# lista en cada letra. Enter y las flechas aplican la búsqueda pendiente antes de actuar.
RETARDO_BUSQUEDA_MS = 60


def _segundos_confirmacion():
    """Tiempo para pulsar "otra vez" y confirmar apagar/reiniciar/cerrar sesión (Ajustes)."""
    return int(ajuste("buscador.confirmar_s"))

BUSCADOR_WEB = "https://www.google.com/search?q="


class Resultado:
    def __init__(self, titulo, subtitulo, icono, accion, confirmar=False):
        self.titulo = titulo
        self.subtitulo = subtitulo
        self.icono = icono  # nombre de icono (str) o Gio.Icon
        self.accion = accion  # callable() -> None
        self.confirmar = confirmar
        self.armado = False
        self.tid = 0  # temporizador que desarma la confirmación
        self.lbl_sub = None


def _imagen(icono, tam):
    img = Gtk.Image()
    img.set_pixel_size(tam)
    _poner_icono(img, icono)
    return img


def _poner_icono(img, icono):
    if icono is None:
        img.set_from_icon_name("application-x-executable")
    elif isinstance(icono, str):
        img.set_from_icon_name(icono)
    else:
        img.set_from_gicon(icono)


class Launcher(Gtk.Window):
    def __init__(self, app, indice):
        super().__init__(application=app, title="Infinity — Buscar")
        self.indice = indice
        self.resultados = []
        self._filas = []  # filas de la lista, una por resultado; se reutilizan entre búsquedas
        self._busqueda_pendiente = 0  # id del temporizador de búsqueda diferida (0 = ninguno)
        self._armado = None  # (accion, id_temporizador) del botón de energía armado

        LayerShell.init_for_window(self)
        LayerShell.set_namespace(self, "infinity-launcher")
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
        self.panel.add_css_class("launcher")
        self.panel.set_halign(Gtk.Align.START)
        self.panel.set_valign(Gtk.Align.END)
        self.panel.set_margin_start(18)
        self.panel.set_margin_bottom(14 + 60 + 12)  # sobre el orbe
        self.panel.set_size_request(580, -1)
        self.fondo.append(self.panel)

        self.entrada = Gtk.Entry()
        self.entrada.add_css_class("launcher-entry")
        self.entrada.set_placeholder_text("Buscar apps, calcular, > comando, ? preguntar a pAnkI…")
        self.entrada.set_icon_from_icon_name(Gtk.EntryIconPosition.PRIMARY, "system-search-symbolic")
        self.entrada.connect("changed", lambda *_: self._programar_busqueda())
        self.entrada.connect("activate", lambda *_: self._activar_seleccion())
        self.panel.append(self.entrada)

        self.lista = Gtk.ListBox()
        self.lista.add_css_class("launcher-list")
        self.lista.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.lista.set_activate_on_single_click(True)
        self.lista.connect("row-activated", lambda _l, fila: self._activar(self._resultado_de(fila)))
        self.scroll = Gtk.ScrolledWindow()
        self.scroll.set_child(self.lista)
        self.scroll.set_propagate_natural_height(True)
        self.scroll.set_max_content_height(400)
        self.scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.panel.append(self.scroll)

        # Fila inferior: estado + ajustes + botones de energía
        inferior = Gtk.Box(spacing=6)
        inferior.add_css_class("launcher-bottom")
        self.estado = Gtk.Label(label="", xalign=0)
        self.estado.add_css_class("launcher-status")
        self.estado.set_hexpand(True)
        inferior.append(self.estado)
        inferior.append(self._boton_ajustes())
        self.botones = {}
        for a in power.ACCIONES:
            b = Gtk.Button()
            b.set_child(_imagen(a.icono, 18))
            b.add_css_class("power-btn")
            if a.peligrosa:
                b.add_css_class("danger")
            b.set_tooltip_text(a.nombre)
            b.connect("clicked", self._clic_energia, a)
            inferior.append(b)
            self.botones[a.clave] = b
        self.panel.append(inferior)

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
        self._desarmar()
        self.entrada.set_text("")  # si cambia el texto, se programa una búsqueda que aquí sobra
        self._cancelar_busqueda()
        self._actualizar()
        self.present()
        self.entrada.grab_focus()

    def cerrar(self):
        self._desarmar()
        self._cancelar_busqueda()
        self._cancelar_armados()
        self.set_visible(False)

    # ------------------------------------------------------------ búsqueda diferida
    def _programar_busqueda(self):
        self._cancelar_busqueda()
        self._busqueda_pendiente = GLib.timeout_add(RETARDO_BUSQUEDA_MS, self._busqueda_lista)

    def _cancelar_busqueda(self):
        if self._busqueda_pendiente:
            GLib.source_remove(self._busqueda_pendiente)
            self._busqueda_pendiente = 0

    def _busqueda_lista(self):
        self._busqueda_pendiente = 0
        self._actualizar()
        return False

    def _al_dia(self):
        """Aplica ya la búsqueda pendiente, si la hay: Enter y las flechas actúan sobre lo que se ve."""
        if self._busqueda_pendiente:
            self._cancelar_busqueda()
            self._actualizar()

    def _cancelar_armados(self):
        for r in self.resultados:
            if r.tid:
                GLib.source_remove(r.tid)
                r.tid = 0
            r.armado = False

    def _clic_fondo(self, _gesto, _n, x, y):
        destino = self.fondo.pick(x, y, Gtk.PickFlags.DEFAULT)
        if destino is None or destino is self.fondo:
            self.cerrar()
            return
        if destino is self.panel or destino.is_ancestor(self.panel):
            return
        self.cerrar()

    def _tecla(self, _ctrl, keyval, _code, _estado):
        if keyval == Gdk.KEY_Escape:
            self.cerrar()
            return True
        if keyval in (Gdk.KEY_Down, Gdk.KEY_Tab):
            self._mover(1)
            return True
        if keyval in (Gdk.KEY_Up, Gdk.KEY_ISO_Left_Tab):
            self._mover(-1)
            return True
        return False

    # --------------------------------------------------------------- resultados
    def _consultar(self, texto):
        q = texto.strip()
        res = []
        if q.startswith(">"):
            cmd = q[1:].strip()
            if cmd:
                res.append(Resultado(f"Ejecutar: {cmd}", "En una terminal", "utilities-terminal",
                                     lambda c=cmd: self._lanzar(["foot", "--hold", "sh", "-c", c])))
            return res
        if q.startswith("?"):
            # Como ">": un prefijo manda sobre todo lo demás, así que no se mezcla con la calculadora,
            # las apps ni la búsqueda web. La pregunta va a pAnkI como un argumento más: sin shell.
            pregunta = q[1:].strip()
            if pregunta:
                res.append(Resultado(f"Preguntar a pAnkI: {pregunta}", "pAnkI · internet solo en Modo Profundo",
                                     "dialog-question",
                                     lambda p=pregunta: self._lanzar(["infinity-panki", "--preguntar", p])))
            else:
                res.append(Resultado("Abrir pAnkI", "El agente virtual · escribe ? y tu pregunta",
                                     "dialog-question", lambda: self._lanzar(["infinity-panki"])))
            return res

        valor = calcular(q) if q else None
        if valor is not None:
            res.append(Resultado(f"= {valor}", "Resultado · Enter para copiar", "accessories-calculator",
                                 lambda v=valor: self._lanzar(["wl-copy", v])))

        for a in self.indice.buscar(q, MAX_FILAS if not q else MAX_FILAS - 1):
            res.append(Resultado(a.nombre, a.descripcion, a.icono, lambda a=a: a.lanzar()))

        nq = normalizar(q)
        if len(nq) >= 3:
            for ac in power.ACCIONES:
                if any(p.startswith(nq) for p in normalizar(ac.palabras).split()):
                    res.append(Resultado(ac.nombre, "Sistema", ac.icono,
                                         lambda ac=ac: power.ejecutar(ac), confirmar=ac.peligrosa))

        if q and valor is None:
            res.append(Resultado(f"Buscar en la web: «{q}»", "Google", "web-browser",
                                 lambda q=q: self._lanzar(["xdg-open", BUSCADOR_WEB + quote_plus(q)])))
        return res[: MAX_FILAS + 2]

    def _actualizar(self):
        self._cancelar_armados()
        self.resultados = self._consultar(self.entrada.get_text())
        # Las filas se reutilizan: solo se crean las que faltan y se quitan las que sobran.
        for i, r in enumerate(self.resultados):
            if i < len(self._filas):
                fila = self._filas[i]
            else:
                fila = self._nueva_fila()
                self._filas.append(fila)
                self.lista.append(fila)
            self._pintar_fila(fila, r)
        for fila in self._filas[len(self.resultados):]:
            self.lista.remove(fila)
        del self._filas[len(self.resultados):]
        self.scroll.set_visible(bool(self.resultados))
        primera = self.lista.get_row_at_index(0)
        if primera:
            self.lista.select_row(primera)
        self.scroll.get_vadjustment().set_value(0)

    @staticmethod
    def _nueva_fila():
        """Fila con sus widgets vacíos; _pintar_fila pone el contenido de cada resultado."""
        fila = Gtk.ListBoxRow()
        caja = Gtk.Box(spacing=12)
        caja.add_css_class("result")
        icono = Gtk.Image()
        icono.set_pixel_size(32)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        textos.set_valign(Gtk.Align.CENTER)
        titulo = Gtk.Label(xalign=0)
        titulo.add_css_class("result-title")
        titulo.set_ellipsize(3)  # Pango.EllipsizeMode.END
        sub = Gtk.Label(xalign=0)
        sub.add_css_class("result-sub")
        sub.set_ellipsize(3)
        textos.append(titulo)
        textos.append(sub)
        caja.append(icono)
        caja.append(textos)
        fila.set_child(caja)
        fila.partes = (icono, titulo, sub)
        return fila

    @staticmethod
    def _pintar_fila(fila, r):
        icono, titulo, sub = fila.partes
        fila.resultado = r
        _poner_icono(icono, r.icono)
        titulo.set_text(r.titulo)
        sub.set_text(r.subtitulo or "")
        sub.set_visible(bool(r.subtitulo))
        sub.remove_css_class("warn")  # una fila reutilizada no conserva el aviso de confirmación
        r.lbl_sub = sub if r.subtitulo else None

    def _seleccionada(self):
        return self._resultado_de(self.lista.get_selected_row())

    def _activar_seleccion(self):
        self._al_dia()
        self._activar(self._seleccionada())

    @staticmethod
    def _resultado_de(fila):
        return getattr(fila, "resultado", None) if fila else None

    def _mover(self, delta):
        self._al_dia()
        n = len(self.resultados)
        if not n:
            return
        actual = self.lista.get_selected_row()
        i = actual.get_index() if actual else -1
        fila = self.lista.get_row_at_index((i + delta) % n)
        self.lista.select_row(fila)
        # Mantén la fila visible aunque el foco siga en la caja de texto.
        adj = self.scroll.get_vadjustment()
        alloc = fila.get_allocation()
        if alloc.y < adj.get_value():
            adj.set_value(alloc.y)
        elif alloc.y + alloc.height > adj.get_value() + adj.get_page_size():
            adj.set_value(alloc.y + alloc.height - adj.get_page_size())

    def _activar(self, r):
        if r is None:
            return
        if r.confirmar and not r.armado:
            # Igual que los botones de energía: la confirmación caduca tras unos segundos
            r.armado = True
            r.tid = GLib.timeout_add_seconds(_segundos_confirmacion(), lambda: self._caducar(r))
            if r.lbl_sub:
                r.lbl_sub.set_text("Pulsa Enter otra vez para confirmar")
                r.lbl_sub.add_css_class("warn")
            return
        self.cerrar()
        r.accion()

    def _caducar(self, r):
        r.armado = False
        r.tid = 0
        if r.lbl_sub:
            r.lbl_sub.set_text(r.subtitulo)
            r.lbl_sub.remove_css_class("warn")
        return False

    # ------------------------------------------------------------------ ajustes
    def _boton_ajustes(self):
        """Engranaje junto a los botones de energía: abre la app de Ajustes."""
        b = Gtk.Button()
        ruta = asset("gear-white.png")
        if ruta:
            imagen = Gtk.Image.new_from_file(ruta)
            imagen.set_pixel_size(18)
        else:
            imagen = _imagen("preferences-system-symbolic", 18)
        b.set_child(imagen)
        b.add_css_class("power-btn")
        b.set_tooltip_text("Ajustes")
        b.connect("clicked", self._abrir_ajustes)
        return b

    def _abrir_ajustes(self, _boton):
        self.cerrar()
        subprocess.Popen(["infinity-ajustes"], start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # ------------------------------------------------------------------ energía
    def _clic_energia(self, _boton, accion):
        if accion.peligrosa and (self._armado is None or self._armado[0] is not accion):
            self._desarmar()
            self.botones[accion.clave].add_css_class("armed")
            self.estado.set_text(f"{accion.nombre}: pulsa otra vez para confirmar")
            self._armado = (accion, GLib.timeout_add_seconds(_segundos_confirmacion(), lambda: self._desarmar(desde_temporizador=True)))
            return
        self.cerrar()
        power.ejecutar(accion)

    def _desarmar(self, desde_temporizador=False):
        if self._armado:
            accion, tid = self._armado
            self._armado = None
            self.botones[accion.clave].remove_css_class("armed")
            if tid and not desde_temporizador:
                GLib.source_remove(tid)
        self.estado.set_text("")
        return False

    @staticmethod
    def _lanzar(cmd):
        try:
            subprocess.Popen(cmd, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            print(f"infinity-shell: no se pudo ejecutar {cmd[0]}: {e}")
