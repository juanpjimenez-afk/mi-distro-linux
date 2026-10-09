"""Ajustes de Infinity OS: la ventana de configuración (infinity-ajustes).

Los controles se crean solos a partir de ajustes.SCHEMA: para añadir un ajuste basta una entrada
allí. Esta ventana solo muestra, valida y aplica; la lógica está en ajustes.py.

    python3 -m infinity_shell.ajustes_app      (lo lanza el comando infinity-ajustes)
"""
import os
import re
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from . import ajustes as A  # noqa: E402

APP_ID = "org.infinity.Ajustes"
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


class VentanaAjustes(Gtk.ApplicationWindow):
    def __init__(self, app):
        super().__init__(application=app, title="Ajustes", default_width=920, default_height=660)
        self.valores, avisos = A.cargar()
        self.guardado = dict(self.valores)
        self.controles = {}
        self._cargando = True  # hasta cargar los valores, los controles no cuentan como cambios

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
            for a in A.SCHEMA:
                if a.pagina == clave:
                    caja.append(self._fila(a))
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(caja)
        return scroll

    def _fila(self, a):
        fila = Gtk.Box(spacing=18)
        fila.set_margin_top(8)
        fila.set_margin_bottom(8)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        titulo = Gtk.Label(label=a.etiqueta, xalign=0, wrap=True)
        titulo.add_css_class("heading")
        textos.append(titulo)
        ayuda = " ".join(p for p in (a.descripcion,
                                     "Se aplica a las ventanas que abras después." if a.aplica == "nuevo" else "")
                         if p)
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
        else:  # texto
            w = Gtk.Entry(width_chars=22)
            w.connect("changed", self._cambio)
        self.controles[a.id] = w
        return w

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
            if a.tipo == "bool":
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
                if a.tipo == "bool":
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
