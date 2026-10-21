"""Índice de aplicaciones instaladas (archivos .desktop) y lanzamiento."""
import json
import os
import shlex

from gi.repository import Gdk, Gio, GLib

from . import calendario_icono
from .search import normalizar, puntuar

_ESTADO = os.path.join(GLib.get_user_state_dir(), "infinity")
_USO = os.path.join(_ESTADO, "uso.json")


class App:
    __slots__ = ("id", "info", "nombre", "descripcion", "_icono", "ruta", "_n_nombre", "_n_extra", "wm_class", "binario")

    def __init__(self, info):
        self.info = info
        self.id = info.get_id() or ""
        self.nombre = info.get_name() or self.id
        self.descripcion = info.get_generic_name() or info.get_description() or ""
        self._icono = info.get_icon()
        self._n_nombre = normalizar(self.nombre)
        palabras = []
        if isinstance(info, Gio.DesktopAppInfo):
            palabras = list(info.get_keywords() or [])
            self.wm_class = (info.get_startup_wm_class() or "").casefold()
            self.ruta = info.get_filename() or ""
        else:
            self.wm_class = ""
            self.ruta = ""
        cmd = info.get_commandline() or ""
        try:
            self.binario = os.path.basename(shlex.split(cmd)[0]).casefold() if cmd else ""
        except ValueError:
            self.binario = ""
        self._n_extra = normalizar(" ".join([self.descripcion, self.id.removesuffix(".desktop"), self.binario] + palabras))

    @property
    def icono(self):
        """El icono de la app. El del Calendario es DINÁMICO: lleva el número del día de hoy."""
        if self.id == calendario_icono.ID_APP:
            return calendario_icono.icono_hoy() or self._icono
        return self._icono

    def puntaje(self, consulta):
        """consulta ya normalizada."""
        mejor = puntuar(consulta, self._n_nombre)
        if mejor:
            return mejor + 100  # el nombre pesa más que palabras clave
        return puntuar(consulta, self._n_extra, difuso=False) // 2

    def lanzar(self):
        ctx = None
        display = Gdk.Display.get_default()
        if display:
            ctx = display.get_app_launch_context()
        try:
            self.info.launch([], ctx)
        except GLib.Error as e:
            print(f"infinity-shell: no se pudo abrir {self.nombre}: {e.message}")
            return False
        _sumar_uso(self.id)
        return True


class Indice:
    def __init__(self):
        self.apps = []
        self.por_id = {}
        self.recargar()
        self._monitor = Gio.AppInfoMonitor.get()
        self._monitor.connect("changed", lambda *_: self.recargar())

    def recargar(self):
        apps = []
        for info in Gio.AppInfo.get_all():
            if info.should_show():
                apps.append(App(info))
        apps.sort(key=lambda a: a._n_nombre)
        self.apps = apps
        self.por_id = {a.id: a for a in apps}

    def buscar(self, consulta, limite=8):
        q = normalizar(consulta)
        uso = _leer_uso()
        if not q:
            vistas = sorted(self.apps, key=lambda a: (-uso.get(a.id, 0), a._n_nombre))
            return vistas[:limite]
        puntuadas = []
        for a in self.apps:
            p = a.puntaje(q)
            if p:
                puntuadas.append((p + min(uso.get(a.id, 0), 20), a))
        puntuadas.sort(key=lambda t: (-t[0], t[1]._n_nombre))
        return [a for _, a in puntuadas[:limite]]

    def para_clase(self, clase):
        """App asociada a la clase de una ventana de Hyprland (p. ej. 'org.gnome.Epiphany')."""
        c = (clase or "").casefold()
        if not c:
            return None
        for a in self.apps:
            if c in (a.id.removesuffix(".desktop").casefold(), a.wm_class, a.binario):
                return a
        for a in self.apps:
            # subcadenas demasiado cortas ("e", "sh") coinciden con casi cualquier app: no se usan
            if c and ((len(c) >= 3 and c in a.id.removesuffix(".desktop").casefold())
                      or (len(a.binario) >= 4 and a.binario in c)):
                return a
        return None


def _leer_uso():
    """Contador de lanzamientos por app. Un fichero con otra forma se ignora entero."""
    try:
        with open(_USO, encoding="utf-8") as f:
            datos = json.load(f)
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {k: v for k, v in datos.items()
            if isinstance(k, str) and isinstance(v, int) and not isinstance(v, bool) and v >= 0}


def _sumar_uso(app_id):
    uso = _leer_uso()
    uso[app_id] = uso.get(app_id, 0) + 1
    try:
        os.makedirs(_ESTADO, exist_ok=True)
        with open(_USO + ".tmp", "w", encoding="utf-8") as f:
            json.dump(uso, f)
        os.replace(_USO + ".tmp", _USO)
    except OSError:
        pass
