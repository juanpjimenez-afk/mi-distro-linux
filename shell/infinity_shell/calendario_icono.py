"""Icono del Calendario con el número del día de hoy (01…31), como las apps de calendario de otros sistemas.

branding/make_calendario.py dibuja un SVG por día (dia-01.svg … dia-31.svg, instalados en
/usr/share/infinity/calendario/). Aquí solo se elige el de hoy; el dock y el lanzador lo piden cada vez que se
dibujan, y el dock se repinta a medianoche (ver Dock._programar_cambio_de_dia).

Todo es lógica pura (sin GTK) salvo `icono_hoy`, que devuelve un Gio.Icon.
"""
import datetime
import os

ID_APP = "infinity-calendario.desktop"  # la entrada del Calendario (overlay/usr/share/applications/)

RUTAS = [
    os.environ.get("INFINITY_CALENDARIO_ICONOS", ""),
    "/usr/share/infinity/calendario",
    os.path.join(os.path.dirname(__file__), "..", "..", "overlay", "usr", "share", "infinity", "calendario"),
]


def dia_hoy(ahora=None):
    """El día del mes (1-31) en hora LOCAL del sistema."""
    return (ahora or datetime.datetime.now()).day


def ruta_dia(dia, rutas=None):
    """Ruta del SVG de ese día, o None si no existe en ninguna de las carpetas."""
    if not isinstance(dia, int) or isinstance(dia, bool) or not 1 <= dia <= 31:
        return None
    for base in (RUTAS if rutas is None else rutas):
        ruta = os.path.join(base, f"dia-{dia:02d}.svg") if base else ""
        if ruta and os.path.isfile(ruta):
            return os.path.abspath(ruta)
    return None


def icono_hoy(ahora=None, rutas=None):
    """El icono de hoy como Gio.Icon, o None si faltan los ficheros (quien llama usa entonces el del .desktop)."""
    ruta = ruta_dia(dia_hoy(ahora), rutas)
    if ruta is None:
        return None
    from gi.repository import Gio  # aquí dentro: el resto del módulo se puede probar sin gi
    return Gio.FileIcon.new(Gio.File.new_for_path(ruta))


def segundos_hasta_el_cambio_de_dia(ahora=None, maximo=3600):
    """Cuánto falta (en segundos, mínimo 1) para la próxima medianoche local, acotado a `maximo`.

    El tope de una hora hace que un cambio de hora (DST) o un reloj ajustado a mano nunca dejen el icono
    desfasado más de una hora: el temporizador se vuelve a calcular cada vez que salta."""
    ahora = ahora or datetime.datetime.now()
    manana = datetime.datetime.combine(ahora.date() + datetime.timedelta(days=1), datetime.time(0, 0, 1))
    falta = int((manana - ahora).total_seconds()) + 1
    return max(1, min(falta, maximo))
