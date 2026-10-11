"""Ajustes de Infinity OS: la ventana de configuración (infinity-ajustes).

Los controles se crean solos a partir de ajustes.SCHEMA: para añadir un ajuste basta una entrada
allí. Esta ventana solo muestra, valida y aplica; la lógica está en ajustes.py.

    python3 -m infinity_shell.ajustes_app      (lo lanza el comando infinity-ajustes)
"""
import os
import re
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import ajustes as A  # noqa: E402
from . import fondos as F  # noqa: E402

APP_ID = "org.infinity.Ajustes"
# Frase que se añade a la descripción de los ajustes que no recargan nada al aplicar.
AYUDA_APLICA = {
    "nuevo": "Se aplica a las ventanas que abras después.",
    "conversacion": "Se aplica en la próxima conversación de pAnkI.",
    "bloqueo": "Se aplica la próxima vez que se bloquee la pantalla.",
}
# Los ajustes que se eligen con una galería de miniaturas en vez de un desplegable: id → qué fondo es.
GALERIAS = {"escritorio.fondo": "escritorio", "bloqueo.fondo": "bloqueo"}
MOSAICO_ANCHO, MOSAICO_ALTO = 176, 99   # la miniatura (16:9)
CSS = """
.fondo-mosaico { padding: 6px; border-radius: 12px; border: 2px solid transparent; }
flowboxchild { border-radius: 14px; padding: 2px; }
flowboxchild:selected { background-color: alpha(@theme_selected_bg_color, 0.22); }
flowboxchild:selected, flowboxchild:selected label { color: @theme_fg_color; }
flowboxchild:selected .fondo-mosaico { border-color: @theme_selected_bg_color; }
.fondo-miniatura { border-radius: 8px; background-color: rgba(128, 128, 128, 0.25); }
.fondo-marca { background-color: @theme_selected_bg_color; color: @theme_selected_fg_color; border-radius: 99px;
               padding: 0 7px; margin: 6px; font-weight: bold; }
.fondo-infinito { font-size: 44px; color: rgba(255, 255, 255, 0.18); }
"""

_BIND = re.compile(r"^\s*(bind[a-z]*)\s*=\s*(.*)$")


def _en_vivo():
    """True si el sistema arrancó desde la ISO en vivo: lo que se cambie se pierde al apagar."""
    try:
        with open("/proc/cmdline", encoding="utf-8") as f:
            return "boot=casper" in f.read()
    except OSError:
        return False


def _hex(color):
    return "#%02x%02x%02x" % tuple(round(x * 255) for x in (color.red, color.green, color.blue))


def _rgba(texto):
    color = Gdk.RGBA()
    color.parse(texto)
    return color


def _leer_dato(ruta, clave=None, sep="="):
    """Un dato de un fichero del sistema: la línea "clave=valor", o la primera línea si no hay clave."""
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            for linea in f:
                if clave is None:
                    return linea.strip() or "—"
                k, s, v = linea.partition(sep)
                if s and k.strip() == clave:
                    return v.strip().strip('"') or "—"
    except OSError:
        pass
    return "—"


def _atajos(ruta):
    """(combinación, descripción) de cada bind de hyprland.conf. Solo lectura."""
    filas = []
    try:
        with open(ruta, encoding="utf-8", errors="replace") as f:
            lineas = f.readlines()
    except OSError:
        return filas
    for linea in lineas:
        m = _BIND.match(linea)
        if not m:
            continue
        partes = [p.strip() for p in m.group(2).split(",", 3)]
        if len(partes) < 3:
            continue
        mods, tecla, accion = partes[0].replace("$mod", "Super"), partes[1], partes[2]
        resto = partes[3] if len(partes) > 3 else ""
        resto, _, comentario = resto.partition("#")
        combo = " + ".join([p for p in mods.split() if p] + [tecla])
        descripcion = comentario.strip() or f"{accion} {resto.strip()}".strip()
        filas.append((combo, descripcion[:90]))
    return filas


class GaleriaFondos(Gtk.Box):
    """Galería de miniaturas para elegir un fondo: el catálogo del sistema, los del usuario y, en el
    escritorio, «Color sólido». Reemplaza al desplegable de los ajustes escritorio.fondo y bloqueo.fondo.

    `valor` es el mismo texto que guarda el ajuste (un id; en el escritorio también «imagen» y «color»).
    La ventana le da las funciones para añadir y quitar fondos; aquí solo se dibuja y se elige.
    """

    def __init__(self, ajuste, ventana):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.ajuste = ajuste
        self.destino = GALERIAS[ajuste.id]
        self.ventana = ventana
        self._valor = ajuste.defecto
        self._ajustando = False
        self._mosaicos = {}     # valor → FlowBoxChild
        self._marcas = {}       # valor → etiqueta ✓
        self._colores = []      # cajas de «Color sólido», para pintarles el color
        self.set_margin_top(8)
        self.set_margin_bottom(14)

        titulo = Gtk.Label(label=ajuste.etiqueta, xalign=0, wrap=True)
        titulo.add_css_class("heading")
        self.append(titulo)
        ayuda = " ".join(p for p in (ajuste.descripcion, AYUDA_APLICA.get(ajuste.aplica, "")) if p)
        if ayuda:
            etiqueta = Gtk.Label(label=ayuda, xalign=0, wrap=True)
            etiqueta.add_css_class("dim-label")
            self.append(etiqueta)

        self.caja = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.SINGLE, homogeneous=True,
                                min_children_per_line=2, max_children_per_line=6, row_spacing=8,
                                column_spacing=8, valign=Gtk.Align.START, activate_on_single_click=True)
        self.caja.connect("selected-children-changed", self._al_elegir)
        self.append(self.caja)

        pie = Gtk.Box(spacing=10)
        self.boton_anadir = Gtk.Button(label="Añadir imagen…")
        self.boton_anadir.set_tooltip_text("Elige una imagen de tu equipo (por ejemplo, una que hayas guardado "
                                           "desde el navegador, en Descargas).")
        self.boton_anadir.connect("clicked", lambda *_: ventana.elegir_imagen(self))
        pie.append(self.boton_anadir)
        self.rueda = Gtk.Spinner()
        pie.append(self.rueda)
        self.aviso = Gtk.Label(xalign=0, wrap=True, hexpand=True)
        pie.append(self.aviso)
        self.append(pie)
        nota = Gtk.Label(label="Desde el navegador: clic derecho sobre la imagen → «Guardar imagen como…» y "
                               "elígela aquí (se abre en Descargas). También puedes arrastrar un archivo "
                               "de imagen a esta ventana.", xalign=0, wrap=True)
        nota.add_css_class("dim-label")
        self.append(nota)

        # Arrastrar y soltar un archivo de imagen sobre la galería
        destino = Gtk.DropTarget.new(Gio.File, Gdk.DragAction.COPY)
        destino.set_gtypes([Gdk.FileList, Gio.File])
        destino.connect("drop", self._al_soltar)
        self.add_controller(destino)
        self.recargar()

    # ---------------------------------------------------------------- valor
    @property
    def valor(self):
        return self._valor

    @valor.setter
    def valor(self, v):
        if v == F.ID_CASCADA and self.destino == "escritorio":
            v = F.VALOR_CASCADA
        self._valor = v
        self._marcar()

    def _marcar(self):
        """Pone la selección y las ✓ según el valor, sin avisar a la ventana."""
        self._ajustando = True
        try:
            hijo = self._mosaicos.get(self._valor)
            if hijo is None:
                self.caja.unselect_all()
            else:
                self.caja.select_child(hijo)
            for v, marca in self._marcas.items():
                marca.set_visible(v == self._valor)
        finally:
            self._ajustando = False

    def _al_elegir(self, caja):
        if self._ajustando:
            return
        elegido = caja.get_selected_children()
        if not elegido:
            return
        for v, hijo in self._mosaicos.items():
            if hijo is elegido[0]:
                self._valor = v
                break
        self._marcar()
        self.ventana._cambio()

    # ---------------------------------------------------------------- contenido
    def recargar(self):
        """Vuelve a leer los fondos del disco y rehace las miniaturas."""
        self._ajustando = True
        try:
            while (hijo := self.caja.get_first_child()) is not None:
                self.caja.remove(hijo)
            self._mosaicos.clear()
            self._marcas.clear()
            self._colores.clear()
            fondos = [f for f in F.listar() if (f.escritorio if self.destino == "escritorio" else f.bloqueos)]
            for f in (x for x in fondos if x.origen != "usuario"):
                self._anadir(F.VALOR_CASCADA if (f.id == F.ID_CASCADA and self.destino == "escritorio") else f.id, f)
            if self.destino == "escritorio":
                self._anadir(F.VALOR_COLOR, None)
            for f in (x for x in fondos if x.origen == "usuario"):
                self._anadir(f.id, f)
        finally:
            self._ajustando = False
        self.pintar_color()
        self._marcar()

    def _anadir(self, valor, fondo):
        """Una miniatura: la imagen, el nombre y, si es del usuario, «Quitar»."""
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        tarjeta.add_css_class("fondo-mosaico")
        marco = Gtk.Overlay()
        if fondo is None:   # «Color sólido»: el color elegido y la marca ∞
            imagen = Gtk.Box(halign=Gtk.Align.FILL, valign=Gtk.Align.FILL)
            imagen.add_css_class("fondo-solido")
            infinito = Gtk.Label(label="∞", halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
            infinito.add_css_class("fondo-infinito")
            imagen.append(infinito)
            infinito.set_hexpand(True)
            self._colores.append(imagen)
            nombre = "Color sólido"
        else:
            foto = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True)
            if fondo.miniatura:
                foto.set_filename(fondo.miniatura)
            # Una Picture pide el tamaño de su imagen. Dentro de una ventana con scroll (sin barras) el
            # mosaico mide siempre lo mismo y la foto se recorta para llenarlo.
            imagen = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER,
                                        vscrollbar_policy=Gtk.PolicyType.NEVER, child=foto)
            nombre = fondo.nombre
        imagen.set_size_request(MOSAICO_ANCHO, MOSAICO_ALTO)
        imagen.add_css_class("fondo-miniatura")
        imagen.set_overflow(Gtk.Overflow.HIDDEN)
        marco.set_child(imagen)
        marca = Gtk.Label(label="✓", halign=Gtk.Align.END, valign=Gtk.Align.START)
        marca.add_css_class("fondo-marca")
        marca.set_visible(False)
        marco.add_overlay(marca)
        tarjeta.append(marco)
        etiqueta = Gtk.Label(label=nombre, xalign=0.5, ellipsize=Pango.EllipsizeMode.END, max_width_chars=22)
        tarjeta.append(etiqueta)
        if fondo is not None and fondo.origen == "usuario":
            quitar = Gtk.Button(label="Quitar", halign=Gtk.Align.CENTER)
            quitar.add_css_class("flat")
            quitar.set_tooltip_text("Borra este fondo de tu carpeta de fondos.")
            quitar.connect("clicked", lambda *_: self.ventana.quitar_fondo(fondo))
            tarjeta.append(quitar)
        hijo = Gtk.FlowBoxChild(halign=Gtk.Align.START, valign=Gtk.Align.START)  # sin estirarse
        hijo.set_child(tarjeta)
        hijo.set_tooltip_text(nombre)
        self.caja.append(hijo)
        self._mosaicos[valor] = hijo
        self._marcas[valor] = marca

    def pintar_color(self):
        """Colorea «Color sólido» con el color que hay en el ajuste de color del fondo."""
        color = self.ventana.color_fondo()
        proveedor = getattr(self, "_proveedor", None)
        if proveedor is None:
            proveedor = self._proveedor = Gtk.CssProvider()
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(display, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        proveedor.load_from_string(f".fondo-solido {{ background-color: {color}; }}")

    # ---------------------------------------------------------------- avisos
    def mostrar(self, texto, error=False):
        """Un mensaje dentro de la propia página (rojo si es un error)."""
        self.aviso.set_text(texto)
        if error:
            self.aviso.add_css_class("error")
        else:
            self.aviso.remove_css_class("error")

    def ocupado(self, si, texto=""):
        self.boton_anadir.set_sensitive(not si)
        self.rueda.set_spinning(si)
        if si:
            self.mostrar(texto)

    def _al_soltar(self, _destino, valor, _x, _y):
        if isinstance(valor, Gdk.FileList):
            ficheros = valor.get_files()
        elif isinstance(valor, Gio.File):
            ficheros = [valor]
        else:
            return False
        if not ficheros:
            return False
        ruta = ficheros[0].get_path()
        if ruta is None:
            self.mostrar("Solo se pueden añadir archivos de este equipo.", error=True)
            return True
        if len(ficheros) > 1:
            self.mostrar("Se añade una imagen cada vez: se usa la primera.")
        self.ventana.importar_imagen(ruta, self)
        return True


class VentanaAjustes(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ajustes", default_width=920, default_height=660)
        self.valores, avisos = A.cargar()
        self.guardado = dict(self.valores)
        self.controles = {}
        self._cargando = True  # hasta cargar los valores, los controles no cuentan como cambios
        self._importando = False
        display = Gdk.Display.get_default()
        if display is not None:
            estilo = Gtk.CssProvider()
            estilo.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(display, estilo, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(raiz)
        if _en_vivo():
            aviso = Gtk.Label(label="Estás usando la ISO en vivo: lo que cambies se guarda en esta sesión "
                                    "y se pierde al apagar. Usa Exportar para conservarlo.",
                              xalign=0, wrap=True)
            aviso.set_margin_start(16)
            aviso.set_margin_end(16)
            aviso.set_margin_top(10)
            aviso.add_css_class("warning")
            raiz.append(aviso)

        cuerpo = Gtk.Box(spacing=0, vexpand=True)
        raiz.append(cuerpo)
        self.pila = Gtk.Stack(hexpand=True, vexpand=True)
        lateral = Gtk.StackSidebar()
        lateral.set_stack(self.pila)
        cuerpo.append(lateral)
        cuerpo.append(self.pila)
        for clave, titulo in A.PAGINAS:
            self.pila.add_titled(self._pagina(clave), clave, titulo)

        pie = Gtk.Box(spacing=8)
        pie.set_margin_start(16)
        pie.set_margin_end(16)
        pie.set_margin_top(8)
        pie.set_margin_bottom(12)
        self.estado = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        pie.append(self.estado)
        for etiqueta, accion in (("Restaurar", self._restaurar), ("Exportar…", self._exportar),
                                 ("Importar…", self._importar)):
            boton = Gtk.Button(label=etiqueta)
            boton.connect("clicked", accion)
            pie.append(boton)
        aplicar = Gtk.Button(label="Aplicar")
        aplicar.add_css_class("suggested-action")
        aplicar.connect("clicked", self._aplicar)
        pie.append(aplicar)
        raiz.append(pie)

        self._cargar_controles(self.valores)
        self.connect("notify::is-active", self._al_cambiar_foco)  # relee la contraseña al volver de passwd
        if avisos:
            self._estado(" ".join(avisos), error=True)

    # ------------------------------------------------------------------ páginas
    def _pagina(self, clave):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        caja.set_margin_start(24)
        caja.set_margin_end(24)
        caja.set_margin_top(16)
        caja.set_margin_bottom(16)
        if clave == "atajos":
            self._pagina_atajos(caja)
        elif clave == "sistema":
            self._pagina_sistema(caja)
        else:
            if clave == "panki":
                self._nota_panki(caja)
            for a in A.SCHEMA:
                if a.pagina == clave:
                    caja.append(self._galeria(a) if a.id in GALERIAS else self._fila(a))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(caja)
        return scroll

    def _galeria(self, a):
        g = GaleriaFondos(a, self)
        self.controles[a.id] = g
        return g

    def _fila(self, a):
        fila = Gtk.Box(spacing=18)
        fila.set_margin_top(8)
        fila.set_margin_bottom(8)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titulo = Gtk.Label(label=a.etiqueta, xalign=0, wrap=True)
        titulo.add_css_class("heading")
        textos.append(titulo)
        ayuda = " ".join(p for p in (a.descripcion, AYUDA_APLICA.get(a.aplica, "")) if p)
        if ayuda:
            etiqueta_ayuda = Gtk.Label(label=ayuda, xalign=0, wrap=True)
            etiqueta_ayuda.add_css_class("dim-label")
            textos.append(etiqueta_ayuda)
        fila.append(textos)
        control = self._control(a)
        control.set_valign(Gtk.Align.CENTER)
        fila.append(control)
        return fila

    def _control(self, a):
        if a.tipo == "bool":
            w = Gtk.Switch()
            w.connect("notify::active", self._cambio)
        elif a.tipo == "choice":
            w = Gtk.DropDown.new_from_strings([etiqueta for _, etiqueta in a.opciones])
            w.connect("notify::selected", self._cambio)
        elif a.tipo == "int":
            w = Gtk.SpinButton.new_with_range(a.minimo, a.maximo, 1)
            w.set_digits(0)
            w.connect("value-changed", self._cambio)
        elif a.tipo == "float":
            w = Gtk.SpinButton.new_with_range(a.minimo, a.maximo, 0.05)
            w.set_digits(3)
            w.connect("value-changed", self._cambio)
        elif a.tipo == "color":
            w = Gtk.ColorDialogButton.new(Gtk.ColorDialog())
            w.connect("notify::rgba", self._cambio)
            if a.id == "escritorio.color":
                w.connect("notify::rgba", lambda *_: self.controles["escritorio.fondo"].pintar_color())
        else:  # texto
            w = Gtk.Entry(width_chars=22)
            w.connect("changed", self._cambio)
        self.controles[a.id] = w
        return w

    @staticmethod
    def _nota_panki(caja):
        """Lo que hay que saber de pAnkI antes de tocar sus ajustes: dónde va la clave y a dónde se envía todo."""
        nota = Gtk.Label(label="La clave de Gemini no se guarda aquí: se pega en la ventana de pAnkI "
                               "(Super + N), con el botón «Clave…». Los mensajes que escribas a pAnkI y lo que "
                               "vea con sus herramientas se envían a Google (Gemini).",
                         xalign=0, wrap=True)
        nota.add_css_class("dim-label")
        nota.set_margin_bottom(10)
        caja.append(nota)

    def _pagina_atajos(self, caja):
        ruta = os.path.join(A.dir_config(), "hypr", "hyprland.conf")
        nota = Gtk.Label(label="Para cambiar un atajo, edita ~/.config/hypr/hyprland.conf y recarga Hyprland "
                               "(hyprctl reload). Esta pantalla solo los muestra.",
                         xalign=0, wrap=True)
        nota.add_css_class("dim-label")
        nota.set_margin_bottom(10)
        caja.append(nota)
        filas = _atajos(ruta)
        if not filas:
            caja.append(Gtk.Label(label="No se encontraron atajos en hyprland.conf.", xalign=0))
        for combo, descripcion in filas:
            fila = Gtk.Box(spacing=18)
            etiqueta = Gtk.Label(label=combo, xalign=0, width_chars=28)
            etiqueta.add_css_class("heading")
            fila.append(etiqueta)
            fila.append(Gtk.Label(label=descripcion, xalign=0, hexpand=True, wrap=True))
            caja.append(fila)

    def _pagina_sistema(self, caja):
        memoria_kb = _leer_dato("/proc/meminfo", "MemTotal", ":").split()[0]
        datos = [
            ("Sistema operativo", _leer_dato("/etc/os-release", "PRETTY_NAME")),
            ("Memoria", f"{int(memoria_kb) / 1048576:.1f} GB" if memoria_kb.isdigit() else "—"),
            ("Zona horaria", _leer_dato("/etc/timezone")),
            ("Teclado de la consola", _leer_dato("/etc/default/keyboard", "XKBLAYOUT")),
            ("Idioma", os.environ.get("LANG", "—")),
        ]
        for etiqueta, dato in datos:
            fila = Gtk.Box(spacing=18)
            fila.set_margin_top(4)
            titulo = Gtk.Label(label=etiqueta, xalign=0, width_chars=24)
            titulo.add_css_class("heading")
            fila.append(titulo)
            fila.append(Gtk.Label(label=dato, xalign=0, hexpand=True, selectable=True))
            caja.append(fila)
        ayuda = Gtk.Label(
            label="Estos ajustes son del sistema y necesitan permisos de administrador. En una terminal:\n"
                  "Zona horaria: sudo dpkg-reconfigure tzdata\n"
                  "Idioma: sudo dpkg-reconfigure locales\n"
                  "Teclado de la consola: sudo dpkg-reconfigure keyboard-configuration",
            xalign=0, wrap=True, selectable=True)
        ayuda.set_margin_top(18)
        ayuda.add_css_class("dim-label")
        caja.append(ayuda)
        caja.append(self._bloque_cuenta())

    # ---------------------------------------------------------------- cuenta
    def _bloque_cuenta(self):
        """Estado de la contraseña y el botón para cambiarla. passwd la pide en una terminal, no aquí."""
        bloque = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        bloque.set_margin_top(28)
        titulo = Gtk.Label(label="Cuenta", xalign=0)
        titulo.add_css_class("title-4")
        bloque.append(titulo)
        self.cuenta_titular = Gtk.Label(xalign=0, wrap=True)
        self.cuenta_titular.add_css_class("heading")
        bloque.append(self.cuenta_titular)
        self.cuenta_ayuda = Gtk.Label(xalign=0, wrap=True)
        self.cuenta_ayuda.add_css_class("dim-label")
        bloque.append(self.cuenta_ayuda)
        self.boton_contrasena = Gtk.Button(label="Cambiar contraseña…", halign=Gtk.Align.START)
        self.boton_contrasena.set_tooltip_text("Se abre una terminal con passwd: la contraseña se escribe allí.")
        self.boton_contrasena.connect("clicked", self._cambiar_contrasena)
        bloque.append(self.boton_contrasena)
        if _en_vivo():
            aviso = Gtk.Label(label="En la ISO en vivo, la contraseña se pierde al apagar el equipo.",
                              xalign=0, wrap=True)
            aviso.add_css_class("dim-label")
            bloque.append(aviso)
        self._refrescar_cuenta()
        return bloque

    def _refrescar_cuenta(self):
        titular, ayuda, se_puede = A.resumen_contrasena(A.leer_estado_contrasena(), A.usuario_actual())
        self.cuenta_titular.set_text(titular)
        self.cuenta_ayuda.set_text(ayuda)
        self.boton_contrasena.set_sensitive(se_puede)

    def _al_cambiar_foco(self, *_):
        if self.is_active():  # al volver de la terminal de passwd, el estado puede haber cambiado
            self._refrescar_cuenta()

    def _cambiar_contrasena(self, *_):
        ok, error = A.cambiar_contrasena()
        if ok:
            self._estado("Se ha abierto una terminal con passwd. Escribe la contraseña allí, no aquí.")
        else:
            self._estado(f"No se pudo abrir la terminal ({error}). "
                         "Puedes ejecutar «passwd» en cualquier terminal.", error=True)

    # ------------------------------------------------------------- estado y valores
    def _estado(self, texto, error=False):
        self.estado.set_text(texto)
        if error:
            self.estado.add_css_class("error")
        else:
            self.estado.remove_css_class("error")

    def _leer_controles(self):
        valores = {}
        for a in A.SCHEMA:
            w = self.controles[a.id]
            if a.id in GALERIAS:
                v = w.valor
            elif a.tipo == "bool":
                v = w.get_active()
            elif a.tipo == "choice":
                i = w.get_selected()
                v = a.opciones[i][0] if i < len(a.opciones) else a.defecto
            elif a.tipo == "int":
                v = int(round(w.get_value()))
            elif a.tipo == "float":
                v = round(w.get_value(), 3)
            elif a.tipo == "color":
                v = _hex(w.get_rgba())
            else:
                v = w.get_text()
            valores[a.id] = v
        return valores

    def _cargar_controles(self, valores):
        self._cargando = True  # cargar valores no cuenta como un cambio del usuario
        try:
            for a in A.SCHEMA:
                w = self.controles[a.id]
                v = valores.get(a.id, a.defecto)
                if a.id in GALERIAS:
                    w.valor = v
                elif a.tipo == "bool":
                    w.set_active(bool(v))
                elif a.tipo == "choice":
                    claves = [o for o, _ in a.opciones]
                    w.set_selected(claves.index(v) if v in claves else 0)
                elif a.tipo in ("int", "float"):
                    w.set_value(float(v))
                elif a.tipo == "color":
                    w.set_rgba(_rgba(v))
                else:
                    w.set_text(str(v))
        finally:
            self._cargando = False

    def _cambio(self, *_):
        if self._cargando:
            return
        nuevos, _ = A.validar(self._leer_controles())
        n = len(A.cambiados(self.guardado, nuevos))
        self._estado(f"Hay {n} cambio(s) sin aplicar. Pulsa Aplicar para usarlos." if n else "")

    # ---------------------------------------------------------------- fondos
    def color_fondo(self):
        """El color sólido del escritorio tal como está ahora en pantalla (o el guardado, al construir)."""
        w = self.controles.get("escritorio.color")
        try:
            return _hex(w.get_rgba()) if w is not None else self.valores["escritorio.color"]
        except (AttributeError, KeyError):
            return A.DEFECTOS["escritorio.color"]

    def _galerias(self):
        return [self.controles[i] for i in GALERIAS if i in self.controles]

    @staticmethod
    def _monitores():
        """Tamaño lógico de cada monitor conectado: infinity-lock busca el fondo por esa resolución."""
        medidas = []
        display = Gdk.Display.get_default()
        lista = display.get_monitors() if display is not None else None
        for i in range(lista.get_n_items() if lista is not None else 0):
            g = lista.get_item(i).get_geometry()
            medidas.append((g.width, g.height))
        return medidas

    def elegir_imagen(self, galeria):
        """«Añadir imagen…»: el selector de archivos, en Descargas y solo con imágenes."""
        if self._importando:
            galeria.mostrar("Ya se está añadiendo una imagen. Espera a que termine.", error=True)
            return
        filtro = Gtk.FileFilter()
        filtro.set_name("Imágenes")
        filtro.add_mime_type("image/*")
        filtro.add_pixbuf_formats()
        for sufijo in ("jpg", "jpeg", "png", "webp", "bmp", "gif", "tif", "tiff"):
            filtro.add_suffix(sufijo)
        filtros = Gio.ListStore.new(Gtk.FileFilter)
        filtros.append(filtro)
        dialogo = Gtk.FileDialog(title="Añadir imagen como fondo", filters=filtros, default_filter=filtro)
        dialogo.set_initial_folder(Gio.File.new_for_path(F.descarga_dir()))
        dialogo.open(self, None, self._imagen_elegida, galeria)

    def _imagen_elegida(self, dialogo, resultado, galeria):
        try:
            fichero = dialogo.open_finish(resultado)
        except GLib.Error:
            return  # cancelado
        ruta = fichero.get_path()
        if ruta is None:
            galeria.mostrar("Solo se pueden añadir archivos de este equipo.", error=True)
            return
        self.importar_imagen(ruta, galeria)

    def importar_imagen(self, ruta, galeria):
        """Convierte la imagen en un fondo, en un hilo aparte para que la ventana no se congele."""
        if self._importando:
            galeria.mostrar("Ya se está añadiendo una imagen. Espera a que termine.", error=True)
            return
        self._importando = True
        for g in self._galerias():
            g.ocupado(True, "Preparando la imagen…" if g is galeria else "")
        monitores = self._monitores()

        def avance(hecho, total):
            GLib.idle_add(galeria.mostrar, f"Preparando la imagen… ({hecho}/{total})")

        def trabajo():
            try:
                fondo, error = F.importar(ruta, monitores=monitores, progreso=avance), ""
            except F.ErrorFondo as e:
                fondo, error = None, str(e)
            except Exception as e:  # nunca en silencio: el error se enseña en la página
                fondo, error = None, f"No se pudo añadir la imagen ({type(e).__name__}: {e})."
            GLib.idle_add(self._importacion_lista, fondo, error, galeria)

        threading.Thread(target=trabajo, daemon=True).start()

    def _importacion_lista(self, fondo, error, galeria):
        self._importando = False
        for g in self._galerias():
            g.ocupado(False)
            g.recargar()
        if fondo is None:
            galeria.mostrar(error, error=True)
            return GLib.SOURCE_REMOVE
        galeria.valor = fondo.id   # queda elegida, pendiente de Aplicar
        otra = "Se verá la próxima vez que se bloquee la pantalla." if galeria.destino == "bloqueo" else ""
        galeria.mostrar(f"«{fondo.nombre}» añadida y elegida. Pulsa Aplicar para usarla. {otra}".strip())
        self._cambio()
        return GLib.SOURCE_REMOVE

    def quitar_fondo(self, fondo):
        """«Quitar»: pide confirmación y borra el fondo del usuario."""
        dialogo = Gtk.AlertDialog()
        dialogo.set_message(f"¿Quitar el fondo «{fondo.nombre}»?")
        dialogo.set_detail("Se borra de tu carpeta de fondos y no se puede deshacer. "
                           "La imagen original no se toca.")
        dialogo.set_buttons(["Cancelar", "Quitar"])
        dialogo.set_cancel_button(0)
        dialogo.set_default_button(0)
        dialogo.choose(self, None, self._quitar_confirmado, fondo)

    def _quitar_confirmado(self, dialogo, resultado, fondo):
        try:
            if dialogo.choose_finish(resultado) != 1:
                return
        except GLib.Error:
            return  # cerrado sin elegir
        try:
            F.quitar(fondo.id)
        except F.ErrorFondo as e:
            for g in self._galerias():
                g.mostrar(str(e), error=True)
            return
        for g in self._galerias():
            g.recargar()
            if g.valor == fondo.id:  # era el elegido: vuelve al de siempre
                g.valor = g.ajuste.defecto
                g.mostrar(f"«{fondo.nombre}» quitado. Vuelve a estar elegido el fondo por defecto; "
                          "pulsa Aplicar para usarlo.")
            else:
                g.mostrar(f"«{fondo.nombre}» quitado.")
        self._cambio()

    # ---------------------------------------------------------------- acciones
    def _aplicar(self, *_):
        nuevos, errores = A.validar(self._leer_controles())
        if errores:
            self._estado("No se ha aplicado nada. " + " ".join(m for _, m in errores), error=True)
            return
        cambiados = A.cambiados(self.guardado, nuevos)
        try:
            A.escribir(nuevos)
        except OSError as e:
            self._estado(f"No se pudo guardar: {e}", error=True)
            return
        self.guardado = nuevos
        fallos = A.ejecutar_acciones(A.acciones_para(cambiados))
        if fallos:
            detalle = " · ".join(f"{argv[0]}: {(salida.splitlines() or ['falló'])[0]}" for argv, salida in fallos)
            self._estado(f"Guardado, pero no se pudo aplicar: {detalle}", error=True)
        elif cambiados:
            self._estado(f"Aplicado: {len(cambiados)} cambio(s).")
        else:
            self._estado("No había cambios.")

    def _restaurar(self, *_):
        self._cargar_controles(A.DEFECTOS)
        self._estado("Valores por defecto cargados. Pulsa Aplicar para usarlos.")

    def _exportar(self, *_):
        dialogo = Gtk.FileDialog(title="Exportar ajustes", initial_name="infinity-ajustes.json")
        dialogo.save(self, None, self._exportado)

    def _exportado(self, dialogo, resultado):
        try:
            fichero = dialogo.save_finish(resultado)
        except GLib.Error:
            return  # cancelado
        try:
            A.guardar_json(self.guardado, fichero.get_path())
        except OSError as e:
            self._estado(f"No se pudo exportar: {e}", error=True)
            return
        self._estado(f"Exportado (ajustes aplicados) a {fichero.get_path()}")

    def _importar(self, *_):
        dialogo = Gtk.FileDialog(title="Importar ajustes")
        dialogo.open(self, None, self._importado)

    def _importado(self, dialogo, resultado):
        try:
            fichero = dialogo.open_finish(resultado)
        except GLib.Error:
            return  # cancelado
        try:
            valores, errores = A.importar(fichero.get_path())
        except (OSError, ValueError) as e:
            self._estado(f"No se pudo importar: {e}", error=True)
            return
        if errores:
            self._estado("No se ha importado nada. " + " ".join(m for _, m in errores), error=True)
            return
        self._cargar_controles(valores)
        self._estado("Importado. Pulsa Aplicar para usarlo.")


class Ajustes(Gtk.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.connect("activate", self._activar)

    def _activar(self, _app):
        ventana = self.get_active_window()
        if ventana is None:
            ajustes_gtk = Gtk.Settings.get_default()  # None si GTK no tiene pantalla
            if ajustes_gtk is not None:
                ajustes_gtk.set_property("gtk-application-prefer-dark-theme", True)
            ventana = VentanaAjustes(self)
        ventana.present()


def main():
    return Ajustes().run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
