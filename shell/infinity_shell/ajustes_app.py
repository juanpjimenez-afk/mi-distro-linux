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
# Ajustes que ya no hacen nada: el motor de Gemini de pAnkI está retirado (ver panki_app.py; nadie lee el modelo ni
# «proponer comandos»). Siguen en el esquema —ajustes.json, Exportar/Importar y el motor antiguo los entienden y
# se conservan al aplicar— pero la ventana no los enseña, para no ofrecer controles que no sirven de nada. Si el
# motor se retoma, basta vaciar esta lista.
OCULTOS = ("panki.modelo", "panki.comandos")
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
label.fondo-aviso.error { color: #ff8a80; }
button.fondo-quitar { padding: 1px 10px; min-height: 0; min-width: 0; }
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


def _se_pierde_al_apagar():
    """True si lo que se cambie no sobrevive al apagado: ISO en vivo y sin partición de guardado (casper persistent)."""
    return _en_vivo() and A.estado_persistencia().modo != "persistente"


def _hex(color):
    return "#%02x%02x%02x" % tuple(round(x * 255) for x in (color.red, color.green, color.blue))


def _motivo(e):
    """Por qué falló una operación con ficheros, en una frase (sin la traza de Python ni la ruta entera)."""
    if isinstance(e, OSError):
        nombre = f" ({os.path.basename(e.filename)})" if getattr(e, "filename", None) else ""
        return f"{e.strerror or 'error de entrada/salida'}{nombre}"
    return str(e) or "el contenido no es válido"


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
        self._proveedor = None  # el CSS de ese color
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
        self.aviso.add_css_class("fondo-aviso")
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
        if not elegido:   # Ctrl + clic en la elegida la desmarca: siempre tiene que haber una
            self._marcar()
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
        etiqueta = Gtk.Label(label=nombre, xalign=0.5, ellipsize=Pango.EllipsizeMode.END,
                             width_chars=18, max_width_chars=18)
        tarjeta.append(etiqueta)
        if fondo is not None and fondo.origen == "usuario":   # «Quitar», sobre la esquina de la miniatura
            quitar = Gtk.Button(label="Quitar", halign=Gtk.Align.START, valign=Gtk.Align.START)
            quitar.add_css_class("osd")
            quitar.add_css_class("fondo-quitar")
            quitar.set_margin_start(6)
            quitar.set_margin_top(6)
            quitar.set_tooltip_text(f"Borra «{fondo.nombre}» de tu carpeta de fondos.")
            quitar.connect("clicked", lambda *_, f=fondo: self.ventana.quitar_fondo(f))
            marco.add_overlay(quitar)
        hijo = Gtk.FlowBoxChild(halign=Gtk.Align.START, valign=Gtk.Align.START)  # sin estirarse
        hijo.set_child(tarjeta)
        hijo.set_tooltip_text(nombre)
        self.caja.append(hijo)
        self._mosaicos[valor] = hijo
        self._marcas[valor] = marca

    def pintar_color(self):
        """Colorea «Color sólido» con el color que hay en el ajuste de color del fondo."""
        if not self._colores:
            return
        proveedor = self._proveedor
        if proveedor is None:
            proveedor = self._proveedor = Gtk.CssProvider()
            display = Gdk.Display.get_default()
            if display is not None:
                Gtk.StyleContext.add_provider_for_display(display, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        proveedor.load_from_string(f".fondo-solido {{ background-color: {self.ventana.color_fondo()}; }}")

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
        self._ocultos = {i: self.valores[i] for i in OCULTOS if i in self.valores}   # sin control: se conservan
        self._pendientes = set()   # acciones de recarga que fallaron y se reintentan al volver a pulsar Aplicar
        self._cargando = True  # hasta cargar los valores, los controles no cuentan como cambios
        self._importando = False
        # Página «Fecha y hora»: se rellena al mostrarla (lee el sistema), no al abrir Ajustes
        self._estado_hora = None        # A.EstadoHora, o None hasta leerlo
        self._zonas = []                # las zonas horarias (A.listar_zonas)
        self._zona_de_fila = {}         # fila de la lista → zona
        self._zona_elegida = None
        self._zonas_visibles = None     # None = sin filtro; si no, el conjunto de zonas que pasan el buscador
        self._cargando_hora = False     # poner un control por código no cuenta como un cambio del usuario
        self._hora_editada = False      # el usuario ya tocó el calendario o la hora: no se le pisa con la actual
        self._ocupado_hora = False      # esperando a que el sistema (o el usuario, en la ventana de permiso) responda
        self._tic_hora_id = 0
        self._ntp_visto = False         # lo que el sistema dice de la hora automática (no lo que se acaba de pulsar)
        self._cerrada = False
        self.connect("close-request", self._al_cerrar)
        self.connect("destroy", self._al_destruir)
        display = Gdk.Display.get_default()
        if display is not None:
            estilo = Gtk.CssProvider()
            estilo.load_from_string(CSS)
            Gtk.StyleContext.add_provider_for_display(display, estilo, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(raiz)
        if _se_pierde_al_apagar():
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
        elif clave == "fecha":
            self._pagina_fecha_hora(caja)
        else:
            if clave == "panki":
                self._nota_panki(caja)
            for a in A.SCHEMA:
                if a.pagina == clave and a.id not in OCULTOS:
                    caja.append(self._galeria(a) if a.id in GALERIAS else self._fila(a))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(caja)
        return scroll

    def _galeria(self, a):
        g = GaleriaFondos(a, self)
        self.controles[a.id] = g
        return g

    def _fila(self, a):
        ayuda = " ".join(p for p in (a.descripcion, AYUDA_APLICA.get(a.aplica, "")) if p)
        return self._fila_de(a.etiqueta, ayuda or None, self._control(a))[0]

    @staticmethod
    def _fila_de(etiqueta, ayuda, control):
        """Una fila: título y ayuda a la izquierda, el control a la derecha. Devuelve (fila, etiqueta_de_ayuda); la
        etiqueta de ayuda es None si `ayuda` es None (con "" se crea vacía, para rellenarla luego)."""
        fila = Gtk.Box(spacing=18)
        fila.set_margin_top(8)
        fila.set_margin_bottom(8)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titulo = Gtk.Label(label=etiqueta, xalign=0, wrap=True)
        titulo.add_css_class("heading")
        textos.append(titulo)
        etiqueta_ayuda = None
        if ayuda is not None:
            etiqueta_ayuda = Gtk.Label(label=ayuda, xalign=0, wrap=True)
            etiqueta_ayuda.add_css_class("dim-label")
            textos.append(etiqueta_ayuda)
        fila.append(textos)
        control.set_valign(Gtk.Align.CENTER)
        fila.append(control)
        return fila, etiqueta_ayuda

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
            w = Gtk.ColorDialogButton.new(Gtk.ColorDialog(with_alpha=False))   # el ajuste es #rrggbb: sin transparencia
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
        """Lo que hay que saber de pAnkI antes de tocar sus ajustes: qué es ahora y qué privacidad tiene."""
        nota = Gtk.Label(label="pAnkI es una app local de tareas, agenda y notas (Super + N); no necesita "
                               "clave ni cuenta. En Modo Rápido no sale nada de tu equipo; en Modo Profundo "
                               "puede consultar algún servicio público gratuito para preguntas generales.",
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

    # ------------------------------------------------------------- fecha y hora
    # Aquí no hay «Aplicar» de la ventana ni ajustes.json: la hora, la fecha y la zona son del sistema y se cambian al
    # momento con timedatectl (ver ajustes.ejecutar_con_permisos). Lo lento (leer el sistema, pedir permiso) va en un
    # hilo, como importar_imagen, para que la ventana no se congele mientras el usuario escribe su contraseña.
    @staticmethod
    def _seccion(texto):
        titulo = Gtk.Label(label=texto, xalign=0)
        titulo.add_css_class("title-4")
        titulo.set_margin_top(26)
        titulo.set_margin_bottom(4)
        return titulo

    @staticmethod
    def _aviso_pagina():
        """Un aviso dentro de la página (rojo si es error): el mismo estilo que los de la galería de fondos."""
        aviso = Gtk.Label(xalign=0, wrap=True)
        aviso.add_css_class("fondo-aviso")
        return aviso

    @staticmethod
    def _poner_aviso(etiqueta, texto, error=False):
        etiqueta.set_text(texto)
        etiqueta.set_visible(bool(texto))   # un aviso vacío no ocupa una línea
        if error:
            etiqueta.add_css_class("error")
        else:
            etiqueta.remove_css_class("error")

    @staticmethod
    def _spin_hora(maximo):
        """Un selector 0..maximo que se vuelve a dar la vuelta (23 → 0) y siempre enseña dos cifras."""
        w = Gtk.SpinButton.new_with_range(0, maximo, 1)
        w.set_wrap(True)
        w.set_numeric(True)
        w.set_width_chars(2)
        w.set_orientation(Gtk.Orientation.VERTICAL)

        def dos_cifras(spin):
            spin.set_text(f"{spin.get_value_as_int():02d}")
            return True
        w.connect("output", dos_cifras)
        return w

    def _pagina_fecha_hora(self, caja):
        # Ahora mismo, con el día de la semana en español (A.fecha_larga: tabla propia, no el idioma del sistema)
        arriba = Gtk.Box(spacing=12)
        ahora = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        self.hora_dia = Gtk.Label(xalign=0)
        self.hora_dia.add_css_class("title-3")
        self.hora_reloj = Gtk.Label(xalign=0)
        self.hora_reloj.add_css_class("title-1")
        self.hora_zona = Gtk.Label(xalign=0, wrap=True)
        self.hora_zona.add_css_class("dim-label")
        for w in (self.hora_dia, self.hora_reloj, self.hora_zona):
            ahora.append(w)
        arriba.append(ahora)
        self.hora_rueda = Gtk.Spinner(valign=Gtk.Align.START)
        arriba.append(self.hora_rueda)
        caja.append(arriba)

        # Hora automática (por internet)
        self.hora_ntp = Gtk.Switch()
        self.hora_ntp.connect("notify::active", self._al_cambiar_ntp)
        fila, self.hora_ntp_ayuda = self._fila_de("Ajustar hora automáticamente (internet)", "", self.hora_ntp)
        caja.append(fila)
        self.hora_aviso_ntp = self._aviso_pagina()
        caja.append(self.hora_aviso_ntp)

        # Fecha y hora a mano
        caja.append(self._seccion("Cambiar la fecha y la hora"))
        self.hora_nota = Gtk.Label(xalign=0, wrap=True)
        self.hora_nota.add_css_class("dim-label")
        caja.append(self.hora_nota)
        cuerpo = Gtk.Box(spacing=28)
        cuerpo.set_margin_top(6)
        self.hora_calendario = Gtk.Calendar(valign=Gtk.Align.START)
        self.hora_calendario.connect("day-selected", self._al_editar_hora)
        cuerpo.append(self.hora_calendario)
        columna = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, valign=Gtk.Align.START, hexpand=True)
        etiqueta_hora = Gtk.Label(label="Hora (24 horas)", xalign=0)
        etiqueta_hora.add_css_class("heading")
        columna.append(etiqueta_hora)
        selector = Gtk.Box(spacing=6)
        self.hora_h, self.hora_m, self.hora_s = self._spin_hora(23), self._spin_hora(59), self._spin_hora(59)
        for i, w in enumerate((self.hora_h, self.hora_m, self.hora_s)):
            if i:
                selector.append(Gtk.Label(label=":"))
            w.connect("value-changed", self._al_editar_hora)
            selector.append(w)
        columna.append(selector)
        self.hora_vista = Gtk.Label(xalign=0, wrap=True)   # lo que se fijaría, con su día de la semana
        columna.append(self.hora_vista)
        self.hora_boton = Gtk.Button(label="Aplicar fecha y hora", halign=Gtk.Align.START)
        self.hora_boton.add_css_class("suggested-action")
        self.hora_boton.set_tooltip_text("Cambia la fecha y la hora del equipo. Puede pedir permiso de administrador.")
        self.hora_boton.connect("clicked", self._aplicar_fecha_hora)
        columna.append(self.hora_boton)
        cuerpo.append(columna)
        caja.append(cuerpo)
        self.hora_aviso = self._aviso_pagina()
        caja.append(self.hora_aviso)

        # Zona horaria: buscador + lista corta con scroll (son unas 400, no un desplegable de 400 filas)
        caja.append(self._seccion("Zona horaria"))
        self.zona_buscar = Gtk.SearchEntry(placeholder_text="Buscar ciudad o región (Santiago, Madrid, Tokio…)")
        self.zona_buscar.connect("search-changed", self._al_buscar_zona)
        caja.append(self.zona_buscar)
        self.zona_lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.zona_lista.set_filter_func(self._zona_visible)
        self.zona_lista.connect("row-selected", self._al_elegir_zona)
        self.zona_scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=self.zona_lista)
        self.zona_scroll.set_min_content_height(190)
        marco = Gtk.Frame(child=self.zona_scroll)
        marco.set_margin_top(6)
        caja.append(marco)
        self.zona_boton = Gtk.Button(label="Aplicar zona horaria", halign=Gtk.Align.START)
        self.zona_boton.add_css_class("suggested-action")
        self.zona_boton.set_margin_top(8)
        self.zona_boton.set_tooltip_text("Cambia la zona horaria del equipo. Puede pedir permiso de administrador.")
        self.zona_boton.connect("clicked", self._aplicar_zona)
        caja.append(self.zona_boton)
        self.zona_aviso = self._aviso_pagina()
        caja.append(self.zona_aviso)

        self._poner_aviso(self.hora_aviso_ntp, "")
        self.hora_ntp_ayuda.set_text("Leyendo el estado del reloj…")
        self._sensibilidad_hora()
        self._pintar_ahora()
        self._pintar_vista()
        caja.connect("map", self._al_mostrar_hora)
        caja.connect("unmap", self._al_ocultar_hora)

    def _al_cerrar(self, *_):
        self._al_destruir()
        return False   # que la ventana se cierre

    def _al_destruir(self, *_):
        self._cerrada = True
        self._al_ocultar_hora()

    def _al_mostrar_hora(self, *_):
        """Al enseñar la página: reloj en marcha y se lee el sistema (estado de la hora automática y zonas)."""
        self._pintar_ahora()
        if not self._tic_hora_id:
            self._tic_hora_id = GLib.timeout_add_seconds(1, self._tic_hora)
        self._leer_estado_hora()

    def _al_ocultar_hora(self, *_):
        if self._tic_hora_id:
            GLib.source_remove(self._tic_hora_id)
            self._tic_hora_id = 0

    def _tic_hora(self):
        if self._cerrada:
            self._tic_hora_id = 0
            return GLib.SOURCE_REMOVE
        self._pintar_ahora()
        return GLib.SOURCE_CONTINUE

    def _pintar_ahora(self):
        t = A.ahora()
        dia = A.fecha_larga(t.year, t.month, t.day)
        self.hora_dia.set_text(dia[:1].upper() + dia[1:])
        self.hora_reloj.set_text(f"{t.hour:02d}:{t.minute:02d}:{t.second:02d}")

    def _lanzar(self, trabajo, listo, ocupado=True, aviso=None):
        """Corre `trabajo()` (devuelve una tupla) en un hilo y llama a `listo(*tupla)` en el hilo de la interfaz.
        Mientras tanto, los controles de la página quedan bloqueados y gira la rueda (si `ocupado`). Si `trabajo` lanza
        una excepción, el error se escribe en `aviso` (por defecto, el de la fecha y la hora): nunca en silencio."""
        if ocupado:
            self._hora_ocupada(True)

        def correr():
            try:
                resultado = trabajo()
            except Exception as e:
                resultado = None
                print(f"infinity-ajustes: fecha y hora: {type(e).__name__}: {e}", file=sys.stderr)
            GLib.idle_add(self._terminado, listo, resultado, ocupado, aviso or self.hora_aviso)

        threading.Thread(target=correr, daemon=True).start()

    def _terminado(self, listo, resultado, ocupado, aviso):
        if not self._cerrada:
            if ocupado:
                self._hora_ocupada(False)
            if resultado is None:
                self._poner_aviso(aviso, "No se pudo completar la operación por un error inesperado "
                                         "(el detalle está en el registro de la sesión).", error=True)
            else:
                listo(*resultado)
        return GLib.SOURCE_REMOVE

    def _hora_ocupada(self, si):
        self._ocupado_hora = si
        self.hora_rueda.set_spinning(si)
        self._sensibilidad_hora()

    # ---- estado del sistema
    def _leer_estado_hora(self):
        zonas = self._zonas
        self._lanzar(lambda: (A.leer_estado_hora(), zonas or A.listar_zonas()), self._estado_hora_listo,
                     ocupado=False)

    def _estado_hora_listo(self, estado, zonas):
        self._estado_hora = estado
        if zonas != self._zonas or not zonas:   # sin lista, se avisa cada vez
            self._llenar_zonas(zonas)
        self._pintar_estado_hora()
        if not self._hora_editada:   # los selectores arrancan en la hora actual; lo que el usuario toque no se pisa
            self._poner_selectores(A.ahora())

    def _pintar_estado_hora(self):
        estado = self._estado_hora
        zona = estado.zona or "—"
        self.hora_zona.set_text(f"Zona horaria: {zona}")
        if getattr(self, "sistema_zona", None) is not None:
            self.sistema_zona.set_text(zona)
        ayuda, _ = A.resumen_hora_automatica(estado)
        self.hora_ntp_ayuda.set_text(ayuda)
        self._ntp_visto = bool(estado.ntp)   # reflejar el estado real en el interruptor no es una orden del usuario
        self._cargando_hora = True
        try:
            self.hora_ntp.set_active(self._ntp_visto)
        finally:
            self._cargando_hora = False
        self._elegir_zona_actual()
        self._sensibilidad_hora()

    def _sensibilidad_hora(self):
        """Qué se puede tocar ahora: nada mientras se espera; el calendario y la hora, solo con la hora automática
        apagada (timedatectl set-time falla con ella activa); la zona, si se eligió una distinta de la actual."""
        libre = not self._ocupado_hora
        estado = self._estado_hora
        auto = bool(estado and estado.ntp)
        puede = A.resumen_hora_automatica(estado)[1] if estado else False
        self.hora_ntp.set_sensitive(libre and puede)
        manual = libre and not auto
        for w in (self.hora_calendario, self.hora_h, self.hora_m, self.hora_s, self.hora_boton):
            w.set_sensitive(manual)
        self.hora_nota.set_text("La hora automática está activada: apaga «Ajustar hora automáticamente» para "
                                "poder cambiar la fecha y la hora a mano." if auto else "")
        self.hora_nota.set_visible(auto)
        actual = estado.zona if estado else None
        self.zona_boton.set_sensitive(libre and bool(self._zona_elegida) and self._zona_elegida != actual)

    # ---- hora automática
    def _al_cambiar_ntp(self, *_):
        activo = self.hora_ntp.get_active()
        # GTK puede avisar tarde de un cambio hecho por código: lo que ya es el estado del sistema no es una orden
        if self._cargando_hora or activo == self._ntp_visto:
            return
        self._poner_aviso(self.hora_aviso_ntp, "Pidiendo permiso y cambiando la hora automática…")
        self._lanzar(lambda: A.fijar_hora_automatica(activo), lambda ok, texto: self._ntp_listo(activo, ok, texto),
                     aviso=self.hora_aviso_ntp)

    def _ntp_listo(self, activo, ok, texto):
        if ok:
            self._poner_aviso(self.hora_aviso_ntp, "Hora automática activada." if activo else
                              "Hora automática desactivada: ya puedes cambiar la fecha y la hora a mano.")
        else:
            self._poner_aviso(self.hora_aviso_ntp, texto, error=True)
        self._leer_estado_hora()   # el interruptor vuelve a lo que hay en el sistema (si falló, a lo de antes)

    # ---- fecha y hora a mano
    def _valores_selectores(self, confirmar=False):
        """(año, mes, día, hora, minutos, segundos) tal como están en pantalla. `confirmar` da por buenos los números
        tecleados y aún sin confirmar (al aplicar; mientras se teclea, no se toca lo que el usuario escribe)."""
        if confirmar:
            for w in (self.hora_h, self.hora_m, self.hora_s):
                w.update()
        f = self.hora_calendario.get_date()
        return (f.get_year(), f.get_month(), f.get_day_of_month(),
                self.hora_h.get_value_as_int(), self.hora_m.get_value_as_int(), self.hora_s.get_value_as_int())

    def _poner_selectores(self, t):
        self._cargando_hora = True
        try:
            self.hora_calendario.select_day(GLib.DateTime.new_local(t.year, t.month, t.day, 0, 0, 0))
            self.hora_h.set_value(t.hour)
            self.hora_m.set_value(t.minute)
            self.hora_s.set_value(t.second)
        finally:
            self._cargando_hora = False
        self._pintar_vista()

    def _al_editar_hora(self, *_):
        if self._cargando_hora:
            return
        self._hora_editada = True
        self._pintar_vista()

    def _pintar_vista(self):
        """«Se fijará: viernes 23 de octubre de 2026, 18:00:00»: así se ve el día de la semana antes de aplicar."""
        a, m, d, h, mi, s = self._valores_selectores()
        try:
            self.hora_vista.set_text(f"Se fijará: {A.fecha_larga(a, m, d)}, {h:02d}:{mi:02d}:{s:02d}")
        except ValueError as e:
            self.hora_vista.set_text(str(e))

    def _aplicar_fecha_hora(self, *_):
        valores = self._valores_selectores(confirmar=True)
        self._poner_aviso(self.hora_aviso, "Pidiendo permiso y cambiando la fecha y la hora…")
        self._lanzar(lambda: A.fijar_fecha_hora(*valores), self._fecha_hora_lista)

    def _fecha_hora_lista(self, ok, texto):
        if ok:
            self._hora_editada = False
            self._pintar_ahora()
            self._poner_aviso(self.hora_aviso, "Fecha y hora cambiadas.")
        else:
            self._poner_aviso(self.hora_aviso, texto, error=True)
        self._leer_estado_hora()

    # ---- zona horaria
    def _llenar_zonas(self, zonas):
        self._zonas = list(zonas)
        while (hijo := self.zona_lista.get_first_child()) is not None:
            self.zona_lista.remove(hijo)
        self._zona_de_fila = {}
        for z in self._zonas:
            fila = Gtk.ListBoxRow()
            etiqueta = Gtk.Label(label=z.replace("_", " "), xalign=0)
            etiqueta.set_margin_start(10)
            etiqueta.set_margin_end(10)
            etiqueta.set_margin_top(5)
            etiqueta.set_margin_bottom(5)
            fila.set_child(etiqueta)
            self.zona_lista.append(fila)
            self._zona_de_fila[fila] = z
        self._poner_aviso(self.zona_aviso, "" if self._zonas else
                          "No se pudo leer la lista de zonas horarias. En una terminal: timedatectl list-timezones",
                          error=not self._zonas)

    def _zona_visible(self, fila):
        return self._zonas_visibles is None or self._zona_de_fila.get(fila) in self._zonas_visibles

    def _al_buscar_zona(self, *_):
        texto = self.zona_buscar.get_text()
        self._zonas_visibles = set(A.filtrar_zonas(self._zonas, texto)) if texto.strip() else None
        self.zona_lista.invalidate_filter()

    def _al_elegir_zona(self, _lista, fila):
        self._zona_elegida = self._zona_de_fila.get(fila) if fila is not None else None
        self._sensibilidad_hora()

    def _elegir_zona_actual(self):
        """Marca en la lista la zona que tiene el sistema y la deja a la vista."""
        actual = self._estado_hora.zona if self._estado_hora else None
        for fila, zona in self._zona_de_fila.items():
            if zona == actual:
                if self._zona_elegida is None:   # solo la primera vez: no se pisa lo que el usuario ya eligió
                    self.zona_lista.select_row(fila)
                    GLib.idle_add(self._mostrar_fila, fila)
                return

    def _mostrar_fila(self, fila):
        """Desplaza la lista hasta `fila` (en el siguiente ciclo, cuando ya tiene tamaño)."""
        try:
            ok, caja = fila.compute_bounds(self.zona_lista)
            if ok:
                ajuste = self.zona_scroll.get_vadjustment()
                ajuste.set_value(max(0, caja.origin.y - (ajuste.get_page_size() - caja.size.height) / 2))
        except (AttributeError, TypeError, ValueError):
            pass   # sin los límites no se desplaza; la zona sigue marcada
        return GLib.SOURCE_REMOVE

    def _aplicar_zona(self, *_):
        zona, zonas = self._zona_elegida, list(self._zonas)
        self._poner_aviso(self.zona_aviso, "Pidiendo permiso y cambiando la zona horaria…")
        self._lanzar(lambda: A.fijar_zona_horaria(zona, zonas), lambda ok, texto: self._zona_lista(zona, ok, texto),
                     aviso=self.zona_aviso)

    def _zona_lista(self, zona, ok, texto):
        if ok:
            self._poner_aviso(self.zona_aviso, f"Zona horaria cambiada a {zona}.")
            self._pintar_ahora()
        else:
            self._poner_aviso(self.zona_aviso, texto, error=True)
        self._leer_estado_hora()

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
            valor = Gtk.Label(label=dato, xalign=0, hexpand=True, selectable=True)
            if etiqueta == "Zona horaria":
                self.sistema_zona = valor   # «Fecha y hora» la actualiza si el usuario cambia la zona
            fila.append(valor)
            caja.append(fila)
        caja.append(self._fila_guardado())
        ayuda = Gtk.Label(
            label="Estos ajustes son del sistema y necesitan permisos de administrador. En una terminal:\n"
                  "Idioma: sudo dpkg-reconfigure locales\n"
                  "Teclado de la consola: sudo dpkg-reconfigure keyboard-configuration\n"
                  "La zona horaria, la fecha y la hora se cambian en la página «Fecha y hora».",
            xalign=0, wrap=True, selectable=True)
        ayuda.set_margin_top(18)
        ayuda.add_css_class("dim-label")
        caja.append(ayuda)
        caja.append(self._bloque_cuenta())

    def _fila_guardado(self):
        """«Guardado de cambios», solo lectura: si lo que se haga sobrevive al apagado (casper persistent) y, si no,
        por qué y qué hacer."""
        p = self.persistencia = A.estado_persistencia()
        fila = Gtk.Box(spacing=18)
        fila.set_margin_top(4)
        titulo = Gtk.Label(label="Guardado de cambios", xalign=0, yalign=0, width_chars=24)
        titulo.add_css_class("heading")
        fila.append(titulo)
        valor = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3, hexpand=True)
        self.persistencia_titular = Gtk.Label(label=p.titular, xalign=0, wrap=True, selectable=True)
        if p.modo == "vivo":
            self.persistencia_titular.add_css_class("warning")
        valor.append(self.persistencia_titular)
        for texto in (p.motivo, p.pista):
            if texto:
                nota = Gtk.Label(label=texto, xalign=0, wrap=True, selectable=True)
                nota.add_css_class("dim-label")
                valor.append(nota)
        fila.append(valor)
        return fila

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
        if _se_pierde_al_apagar():
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
            self._absorber_cambios_externos()   # y pAnkI pudo renombrarse mientras tanto

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
        valores = dict(self._ocultos)
        for a in A.SCHEMA:
            if a.id in OCULTOS:
                continue
            w = self.controles[a.id]
            if a.id in GALERIAS:
                v = w.valor
            elif a.tipo == "bool":
                v = w.get_active()
            elif a.tipo == "choice":
                i = w.get_selected()
                v = a.opciones[i][0] if i < len(a.opciones) else a.defecto
            elif a.tipo in ("int", "float"):
                w.update()   # un número tecleado y aún sin confirmar (Enter o salir del campo) también cuenta
                v = int(round(w.get_value())) if a.tipo == "int" else round(w.get_value(), 3)
            elif a.tipo == "color":
                v = _hex(w.get_rgba())
            else:
                v = w.get_text()
            valores[a.id] = v
        return valores

    def _cargar_controles(self, valores):
        """Pone todos los controles: lo que no está en `valores` vuelve a su valor por defecto."""
        self._poner_controles({a.id: valores.get(a.id, a.defecto) for a in A.SCHEMA})

    def _poner_controles(self, valores):
        """Pone los controles de los ajustes que vienen en `valores` (y solo esos)."""
        self._cargando = True  # cargar valores no cuenta como un cambio del usuario
        try:
            for a in A.SCHEMA:
                if a.id not in valores:
                    continue
                v = valores[a.id]
                if a.id in OCULTOS:
                    self._ocultos[a.id] = v
                    continue
                w = self.controles[a.id]
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

    def _absorber_cambios_externos(self):
        """Otro programa (pAnkI al renombrarse, por ejemplo) pudo cambiar ajustes.json con esta ventana abierta.
        Lo que el usuario no ha tocado aquí se pone al día; lo que sí tocó manda. Así, aplicar no devuelve a su
        valor viejo un ajuste que cambió otro."""
        externos = A.cambios_externos(self.guardado)
        if not externos:
            return
        pantalla = self._leer_controles()
        self._poner_controles({k: v for k, v in externos.items() if pantalla.get(k) == self.guardado.get(k)})
        self.guardado.update(externos)   # lo que hay en disco es lo que se tiene por aplicado
        self._cambio()

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
        self._absorber_cambios_externos()
        nuevos, errores = A.validar(self._leer_controles())
        if errores:
            self._estado("No se ha aplicado nada. " + " ".join(m for _, m in errores), error=True)
            return
        cambiados = A.cambiados(self.guardado, nuevos)
        try:
            A.escribir(nuevos)
        except (OSError, ValueError) as e:
            self._estado(f"No se pudo guardar: {_motivo(e)}. No se ha cambiado nada.", error=True)
            return
        self.guardado = nuevos
        reintento = bool(self._pendientes)
        self._pendientes.update(A.acciones_para(cambiados))
        fallos = A.ejecutar_acciones(A.ordenar_acciones(self._pendientes))
        # Lo que falla se vuelve a intentar con el siguiente Aplicar, aunque ya no haya nada distinto que guardar.
        self._pendientes = set(A.acciones_fallidas(fallos))
        if fallos:
            for argv, salida in fallos:   # el detalle va al registro de la sesión, no a la ventana
                print(f"infinity-ajustes: {' '.join(argv)}: {salida}", file=sys.stderr)
            que = ", ".join(A.DESCRIPCION_ACCION[a] for a in A.ordenar_acciones(self._pendientes))
            self._estado(f"Guardado, pero no se pudo aplicar ahora ({que}). Pulsa Aplicar para reintentarlo; "
                         "si sigue fallando, se aplicará al cerrar sesión y volver a entrar.", error=True)
        elif cambiados:
            self._estado(f"Aplicado: {len(cambiados)} cambio(s).")
        elif reintento:
            self._estado("Aplicado.")
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
        ruta = fichero.get_path()
        if ruta is None:   # una carpeta remota que el sistema no ha montado: no hay ruta local donde escribir
            self._estado("Solo se puede exportar a una carpeta de este equipo.", error=True)
            return
        try:
            A.guardar_json(self.guardado, ruta)
        except (OSError, ValueError) as e:
            self._estado(f"No se pudo exportar: {_motivo(e)}.", error=True)
            return
        self._estado(f"Exportado (ajustes aplicados) a {ruta}")

    def _importar(self, *_):
        dialogo = Gtk.FileDialog(title="Importar ajustes")
        dialogo.open(self, None, self._importado)

    def _importado(self, dialogo, resultado):
        try:
            fichero = dialogo.open_finish(resultado)
        except GLib.Error:
            return  # cancelado
        ruta = fichero.get_path()
        if ruta is None:
            self._estado("Solo se pueden importar ajustes de un archivo de este equipo.", error=True)
            return
        try:
            # Lo que el archivo no menciona (uno de una versión anterior) se queda como está en pantalla.
            valores, errores = A.importar(ruta, base=A.validar(self._leer_controles())[0])
        except (OSError, ValueError) as e:
            self._estado(f"No se pudo importar: {_motivo(e)}.", error=True)
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
