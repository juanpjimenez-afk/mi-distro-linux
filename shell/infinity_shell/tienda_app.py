"""La Tienda de Infinity OS: la ventana (infinity-tienda).

GTK 4 nativo, sin libadwaita. Toda la lógica (catálogo, estado de cada app, planes de instalación, cola de trabajos,
dependencias entre apps) está en tienda_catalogo.py, tienda_ops.py y tienda_webapps.py y se prueba sin pantalla; aquí solo
se dibuja y se conecta. El diseño y el modelo de seguridad: docs/tienda.md.

    python3 -m infinity_shell.tienda_app [--catalogo RUTA]      (lo lanza el comando infinity-tienda)

Resumen de lo que hace esta ventana:
  * Una rejilla de tarjetas (Gtk.Grid de 3 columnas, dentro de un ScrolledWindow; NO un Gtk.FlowBox, que se queda con los
    clics de sus botones hijos) con lateral de «Todo», «Instaladas» y las categorías del catálogo, cada una con su
    contador, y un buscador en la cabecera que filtra al vuelo (Ctrl+F lo enfoca, Esc lo limpia).
  * Cada tarjeta repinta SOLO su pie según el estado de su app (sin instalar, en cola, en curso, instalada, WebApp que
    hay que reparar, fallo). Los avances de una instalación llegan a menudo: actualizan la barra de esa tarjeta y nada
    más; la rejilla solo se reconstruye al cambiar de categoría o de búsqueda.
  * Nada de diálogos ni de popovers para decidir: las preguntas («falta un navegador», «poco espacio») salen en un
    panel EN LÍNEA (un velo oscuro con un cuadro centrado, dentro de la propia ventana) y «Quitar» pide confirmación
    dentro de la tarjeta, como la bóveda de Contraseñas.
  * Al terminar cada trabajo, una barra abajo cuenta cómo fue (y deja ver el registro). Los éxitos se esconden solos.
  * Cerrar con instalaciones en marcha NO las mata: la ventana se oculta, la aplicación sigue viva (Gtk.Application.hold)
    hasta que acaba el último trabajo y entonces se despide sola. Un segundo `infinity-tienda` la vuelve a mostrar.
  * En modo en vivo sin guardado avisa (franja ámbar descartable) de que lo instalado se perderá al apagar.

El estilo (azul noche translúcido para que el desenfoque de Hyprland se note, tarjetas de cristal sutil, acento violeta
#b9a8ff y, con mucha moderación, cian neón #7cf0ff) va en ESTILO; el resto del shell vive en style.css, que aquí NO se carga.
"""
import os
import subprocess
import sys
import threading
from collections import namedtuple
from urllib.parse import urlsplit

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import ajustes  # noqa: E402
from . import tienda_catalogo as C  # noqa: E402
from . import tienda_ops as O  # noqa: E402

APP_ID = "org.infinity.Tienda"
COLUMNAS = 3                 # tarjetas por fila
TAM_ICONO = 64               # px del icono de cada tarjeta
SEGUNDOS_EXITO = 8           # los avisos que no son errores se esconden solos a los 8 s
LINEAS_REGISTRO = 400        # lo máximo del registro de un trabajo que enseña «Ver registro»
RUTA_BASE_SISTEMA = "/usr/share/infinity"     # ahí vive tienda.svg (el icono de la propia Tienda)
VISTA_TODO, VISTA_INSTALADAS, PREFIJO_CATEGORIA = "todo", "instaladas", "cat:"

# Una opción del panel en línea: texto del botón, qué hacer, si es la principal (violeta), una marca pequeña
# («Recomendado»), un detalle en monoespaciada («≈ 450 MB») y la entrada del catálogo cuyo icono la acompaña (o None).
OpcionPanel = namedtuple("OpcionPanel", "texto accion principal marca detalle entrada", defaults=(False, "", "", None))

# El fondo es translúcido (92 %): Hyprland desenfoca lo que hay detrás y se nota «cristal». Las tarjetas y el lateral
# llevan encima una capa blanca muy sutil (3–6 %) que no le quita contraste al texto, ni siquiera sobre un fondo claro.
ESTILO = """
window.tienda-ventana { background-color: rgba(15, 18, 38, 0.92); color: #e9ebf7; }
window.tienda-ventana * { font-family: "Inter", "Noto Sans", sans-serif; }
window.tienda-ventana .tienda-mono, window.tienda-ventana .tienda-chip, window.tienda-ventana label.tienda-tamano,
window.tienda-ventana label.tienda-porcentaje, window.tienda-ventana label.tienda-seccion-sub,
window.tienda-ventana label.tienda-lateral-titulo, window.tienda-ventana textview.tienda-mono,
window.tienda-ventana textview.tienda-mono text {
  font-family: "JetBrains Mono", "Noto Sans Mono", monospace;
}

headerbar.tienda-cabecera { min-height: 46px; padding: 0 8px 0 16px; background: none; border: none; box-shadow: none; }
headerbar.tienda-cabecera windowcontrols button {
  opacity: 0.5; padding: 0; min-width: 24px; min-height: 24px;
  background-color: transparent; border-color: transparent; border-radius: 99px;
}
headerbar.tienda-cabecera windowcontrols button:hover { opacity: 1; background-color: rgba(255, 255, 255, 0.12); }
label.tienda-marca { color: #b9a8ff; font-size: 14px; font-weight: 600; letter-spacing: 0.3px; }
headerbar.tienda-cabecera image.tienda-marca { color: #b9a8ff; }

window.tienda-ventana button {
  background-image: none; background-color: rgba(255, 255, 255, 0.07); color: #e9ebf7;
  border: 1px solid rgba(255, 255, 255, 0.11); border-radius: 10px; box-shadow: none;
  padding: 4px 14px; min-height: 22px; text-shadow: none; font-size: 12.5px;
}
window.tienda-ventana button:hover { background-color: rgba(255, 255, 255, 0.15); }
window.tienda-ventana button:disabled { opacity: 0.5; }
window.tienda-ventana button:focus-visible { outline: 2px solid rgba(185, 168, 255, 0.55); outline-offset: 1px; }
window.tienda-ventana button.suggested-action {
  background-color: #b9a8ff; color: #1a1636; border-color: #b9a8ff; font-weight: 600;
}
window.tienda-ventana button.suggested-action label { color: #1a1636; }
window.tienda-ventana button.suggested-action:hover { background-color: #cdbfff; border-color: #cdbfff; }
window.tienda-ventana button.destructive-action { background-color: rgba(255, 99, 99, 0.26); border-color: rgba(255, 138, 128, 0.55); }
window.tienda-ventana button.destructive-action:hover { background-color: rgba(255, 99, 99, 0.42); }
window.tienda-ventana button.flat, window.tienda-ventana menubutton > button {
  background-color: transparent; border-color: transparent;
}
window.tienda-ventana button.flat:hover, window.tienda-ventana menubutton > button:hover { background-color: rgba(255, 255, 255, 0.12); }
window.tienda-ventana button.tienda-mini { padding: 2px 4px; min-height: 18px; font-size: 11px; color: rgba(233, 235, 247, 0.72); }
window.tienda-ventana button.tienda-icono { padding: 4px 6px; min-width: 22px; }
window.tienda-ventana button.tienda-espera { padding: 4px 10px; color: rgba(233, 235, 247, 0.7); }
window.tienda-ventana button.tienda-espera:disabled { opacity: 0.85; }      /* desactivado, pero «En cola…» se lee */

window.tienda-ventana entry {
  background-color: rgba(255, 255, 255, 0.07); border: 1px solid rgba(255, 255, 255, 0.13);
  border-radius: 12px; min-height: 20px; padding: 5px 12px; box-shadow: none; color: #ffffff; caret-color: #b9a8ff;
}
window.tienda-ventana entry:focus-within { border-color: #b9a8ff; outline: 2px solid rgba(185, 168, 255, 0.30); outline-offset: 0; }
window.tienda-ventana entry text, window.tienda-ventana textview text { color: #ffffff; caret-color: #b9a8ff; }
window.tienda-ventana entry image { color: rgba(233, 235, 247, 0.62); }
window.tienda-ventana popover > contents {
  background-color: rgba(30, 34, 58, 0.97); border: 1px solid rgba(255, 255, 255, 0.16); border-radius: 14px;
}
window.tienda-ventana popover modelbutton { border-radius: 8px; }
window.tienda-ventana popover modelbutton:hover { background-color: rgba(185, 168, 255, 0.22); }
window.tienda-ventana scrollbar slider { background-color: rgba(255, 255, 255, 0.22); }
window.tienda-ventana separator { background-color: rgba(255, 255, 255, 0.08); min-height: 1px; }

/* ---- lateral */
.tienda-lateral { background-color: rgba(255, 255, 255, 0.035); border-right: 1px solid rgba(255, 255, 255, 0.07); }
window.tienda-ventana label.tienda-lateral-titulo { font-size: 10px; font-weight: 700; letter-spacing: 1.4px; color: rgba(233, 235, 247, 0.58); }
list.tienda-categorias { background: transparent; }
list.tienda-categorias row { border-radius: 10px; margin: 1px 10px; }
list.tienda-categorias row:selected { background-color: rgba(185, 168, 255, 0.22); }
list.tienda-categorias row:hover:not(:selected) { background-color: rgba(255, 255, 255, 0.07); }
.tienda-cat { padding: 8px 10px; }
.tienda-cuenta { background-color: rgba(255, 255, 255, 0.09); border-radius: 99px; padding: 0 8px; font-size: 11px; }
list.tienda-categorias row:selected .tienda-cuenta { background-color: rgba(185, 168, 255, 0.35); }

/* ---- cabecera de la sección y estado vacío */
window.tienda-ventana label.tienda-seccion { font-size: 22px; font-weight: 700; }
window.tienda-ventana label.tienda-seccion-sub { font-size: 11px; letter-spacing: 0.4px; color: rgba(233, 235, 247, 0.6); }
window.tienda-ventana label.tienda-vacio-titulo { font-size: 17px; font-weight: 600; }
window.tienda-ventana label.tienda-subtitulo { color: rgba(233, 235, 247, 0.66); }

/* ---- tarjeta */
.tienda-tarjeta {
  background-color: rgba(255, 255, 255, 0.04); border: 1px solid rgba(255, 255, 255, 0.08); border-radius: 18px;
  transition: border-color 160ms ease, box-shadow 160ms ease, background-color 160ms ease;
}
.tienda-tarjeta.tienda-expandida { border-color: rgba(185, 168, 255, 0.30); }
.tienda-tarjeta:hover {
  border-color: #b9a8ff; background-color: rgba(185, 168, 255, 0.055);
  box-shadow: 0 0 0 1px rgba(185, 168, 255, 0.30), 0 6px 20px rgba(185, 168, 255, 0.22);
}
.tienda-franja { min-height: 3px; opacity: 0.8; }
window.tienda-ventana label.tienda-nombre { font-size: 15px; font-weight: 600; }
window.tienda-ventana label.tienda-resumen { font-size: 12.5px; color: rgba(233, 235, 247, 0.68); }
window.tienda-ventana label.tienda-descripcion { font-size: 12px; color: rgba(233, 235, 247, 0.8); }
.tienda-chip { font-size: 9px; font-weight: 600; letter-spacing: 0.7px; border-radius: 5px; padding: 1px 5px; }
.tienda-chip-tipo { color: #cfc3ff; background-color: rgba(185, 168, 255, 0.10); border: 1px solid rgba(185, 168, 255, 0.28); }
.tienda-chip-drm { color: rgba(233, 235, 247, 0.78); background-color: rgba(255, 255, 255, 0.06); border: 1px solid rgba(255, 255, 255, 0.20); }
window.tienda-ventana image.tienda-icono-respaldo { background-color: rgba(255, 255, 255, 0.06); border-radius: 15px; color: rgba(233, 235, 247, 0.55); }
.tienda-marca-ok {
  min-width: 18px; min-height: 18px; border-radius: 99px; background-color: #7cf0ff;
  box-shadow: 0 0 10px rgba(124, 240, 255, 0.55);
}
window.tienda-ventana .tienda-marca-ok image, window.tienda-ventana .tienda-marca-ok label { color: #05222b; font-size: 11px; font-weight: 700; }
window.tienda-ventana label.tienda-tamano { font-size: 10.5px; color: rgba(233, 235, 247, 0.6); }
window.tienda-ventana button.tienda-info {
  min-width: 22px; min-height: 22px; padding: 0; border-radius: 99px; font-size: 12px; font-weight: 700; font-style: italic;
  background-color: transparent; border-color: rgba(255, 255, 255, 0.18); color: rgba(233, 235, 247, 0.7);
}
window.tienda-ventana button.tienda-info:hover { background-color: rgba(255, 255, 255, 0.12); }
window.tienda-ventana button.tienda-info:checked { background-color: rgba(185, 168, 255, 0.28); border-color: rgba(185, 168, 255, 0.6); color: #e2d9ff; }
window.tienda-ventana button.tienda-enlace { padding: 0; min-height: 0; color: #cfc3ff; font-size: 12px; }
window.tienda-ventana button.tienda-enlace:hover { background-color: transparent; text-decoration: underline; }

progressbar.tienda-progreso trough { min-width: 0; min-height: 5px; border-radius: 99px; background-color: rgba(255, 255, 255, 0.10); border: none; }
progressbar.tienda-progreso progress {
  min-height: 5px; margin: 0; border-radius: 99px; background-color: #7cf0ff; background-image: none; border: none;
  box-shadow: 0 0 10px rgba(124, 240, 255, 0.5);
}
window.tienda-ventana label.tienda-progreso-texto { font-size: 11.5px; color: rgba(233, 235, 247, 0.75); }
window.tienda-ventana label.tienda-porcentaje { font-size: 10.5px; color: #7cf0ff; }

/* ---- avisos dentro de la tarjeta y del panel */
.tienda-aviso {
  background-color: rgba(255, 196, 107, 0.11); border: 1px solid rgba(255, 196, 107, 0.38);
  border-radius: 12px; padding: 8px 10px;
}
window.tienda-ventana .tienda-aviso label, window.tienda-ventana .tienda-aviso image { color: #ffd9a0; font-size: 12px; }
.tienda-error { background-color: rgba(255, 99, 99, 0.12); border: 1px solid rgba(255, 138, 128, 0.40); border-radius: 12px; padding: 8px 10px; }
window.tienda-ventana .tienda-error label { color: #ffb4ab; font-size: 12px; }
.tienda-confirmar { background-color: rgba(255, 99, 99, 0.12); border: 1px solid rgba(255, 138, 128, 0.40); border-radius: 14px; padding: 10px 12px; }
window.tienda-ventana .tienda-confirmar label { font-size: 12.5px; }

/* ---- franja de modo en vivo y barra de resultado */
.tienda-vivo { background-color: rgba(255, 196, 107, 0.12); border-bottom: 1px solid rgba(255, 196, 107, 0.34); padding: 6px 12px 6px 20px; }
window.tienda-ventana .tienda-vivo label, window.tienda-ventana .tienda-vivo image { color: #ffd9a0; font-size: 12.5px; }
.tienda-barra { background-color: rgba(185, 168, 255, 0.12); border-top: 1px solid rgba(185, 168, 255, 0.28); padding: 8px 18px; }
window.tienda-ventana .tienda-barra label { color: #e2d9ff; }
.tienda-barra.ok { background-color: rgba(79, 227, 161, 0.10); border-top-color: rgba(79, 227, 161, 0.38); }
window.tienda-ventana .tienda-barra.ok label { color: #8be9a8; }
.tienda-barra.error { background-color: rgba(255, 99, 99, 0.12); border-top-color: rgba(255, 138, 128, 0.42); }
window.tienda-ventana .tienda-barra.error label { color: #ffb4ab; }
.tienda-barra.aviso { background-color: rgba(255, 196, 107, 0.11); border-top-color: rgba(255, 196, 107, 0.38); }
window.tienda-ventana .tienda-barra.aviso label { color: #ffd9a0; }
.tienda-registro { background-color: rgba(0, 0, 0, 0.30); border-radius: 10px; margin-top: 8px; }
window.tienda-ventana .tienda-registro textview, window.tienda-ventana .tienda-registro textview text { background-color: transparent; font-size: 11px; color: rgba(233, 235, 247, 0.85); }

/* ---- panel en línea (velo + cuadro) */
.tienda-velo { background-color: rgba(8, 10, 24, 0.74); }
.tienda-panel {
  background-color: rgb(24, 28, 54); border: 1px solid rgba(185, 168, 255, 0.32); border-radius: 18px; padding: 22px 24px;
  box-shadow: 0 18px 50px rgba(0, 0, 0, 0.55);
}
window.tienda-ventana label.tienda-panel-titulo { font-size: 17px; font-weight: 700; }
window.tienda-ventana label.tienda-panel-texto { color: rgba(233, 235, 247, 0.78); }
window.tienda-ventana label.tienda-panel-nota { font-size: 12px; color: #7cf0ff; }
window.tienda-ventana button.tienda-opcion { padding: 9px 14px; }
.tienda-chip-marca { color: #cfc3ff; background-color: rgba(185, 168, 255, 0.16); border: 1px solid rgba(185, 168, 255, 0.38); }
window.tienda-ventana button.suggested-action .tienda-chip-marca { background-color: rgba(26, 22, 54, 0.14); border-color: rgba(26, 22, 54, 0.40); }
window.tienda-ventana button.suggested-action .tienda-chip-marca, window.tienda-ventana button.suggested-action label.tienda-tamano { color: #1a1636; }
"""

_estilos_cargados = set()
_colores_cargados = {}


def _cargar_css(pantalla, texto, prioridad=Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION):
    proveedor = Gtk.CssProvider()
    if hasattr(proveedor, "load_from_string"):  # GTK 4.12 o más; antes, load_from_data
        proveedor.load_from_string(texto)
    else:
        proveedor.load_from_data(texto.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(pantalla, proveedor, prioridad)
    return proveedor


def _cargar_estilo(pantalla):
    """Carga el CSS de la Tienda una vez por pantalla, con prioridad de aplicación."""
    if pantalla is None or id(pantalla) in _estilos_cargados:
        return
    _cargar_css(pantalla, ESTILO)
    _estilos_cargados.add(id(pantalla))


def clase_color(ident):
    """Nombre de la clase CSS de la franja de color de una app."""
    return f"tienda-color-{ident}"


def css_colores(apps):
    """El CSS de las franjas de color de las tarjetas: una regla por app, con el `color` de su entrada del catálogo (el
    catálogo ya lo validó como #rrggbb; aquí se vuelve a comprobar porque acaba dentro de un CSS)."""
    reglas = []
    for a in apps:
        if not C.RE_ID.fullmatch(a.id or ""):
            continue
        color = a.color if C.RE_COLOR.fullmatch(a.color or "") else "#b9a8ff"
        r, g, b = (int(color[i:i + 2], 16) for i in (1, 3, 5))
        reglas.append(f".{clase_color(a.id)} {{ background-image: linear-gradient(90deg, rgba({r}, {g}, {b}, 0.95), "
                      f"rgba({r}, {g}, {b}, 0.18)); }}")
    return "\n".join(reglas)


def _cargar_colores(pantalla, apps):
    """Carga (una vez por pantalla y por catálogo) las franjas de color de las tarjetas."""
    texto = css_colores(apps)
    if pantalla is None or not texto or (id(pantalla), texto) in _colores_cargados:
        return
    _colores_cargados[(id(pantalla), texto)] = _cargar_css(pantalla, texto)


# ------------------------------------------------------------------------------------ utilidades puras
def formatear_tamano(mb):
    """«≈ 700 MB», «≈ 1 GB», «≈ 1,5 GB»; vacío si el catálogo no dice el tamaño."""
    if not mb:
        return ""
    if mb >= 1000:
        gb = f"{mb / 1000:.1f}".rstrip("0").rstrip(".").replace(".", ",")
        return f"≈ {gb} GB"
    return f"≈ {mb} MB"


def filtrar(catalogo, vista, texto, instalada):
    """Las apps que se ven: las que casan con `texto` (Catalogo.buscar) dentro de la vista elegida. `vista` es «todo»,
    «instaladas» o «cat:<id de categoría>»; `instalada(id) -> bool` dice qué hay instalado."""
    apps = catalogo.buscar(texto)
    if vista == VISTA_INSTALADAS:
        return [a for a in apps if instalada(a.id)]
    if vista.startswith(PREFIJO_CATEGORIA):
        categoria = vista[len(PREFIJO_CATEGORIA):]
        return [a for a in apps if a.categoria == categoria]
    return apps


def modo_de(estado, trabajo, ultimo):
    """Qué enseña el pie de una tarjeta: instalar | cola | curso | abrir | reparar | fallo.

    `estado` es el tienda_ops.Estado de la app, `trabajo` su trabajo activo (o None) y `ultimo` el último que hubo
    (Gestor.trabajos[id]; solo importa si fue un fallo)."""
    if trabajo is not None:
        return "cola" if trabajo.estado == "en_cola" else "curso"
    if ultimo is not None and ultimo.estado == "fallo":
        return "fallo"
    if estado.instalada:
        return "abrir" if estado.funciona else "reparar"
    return "instalar"


def _plural(n, uno, varios):
    return f"{n} {uno if n == 1 else varios}"


# ------------------------------------------------------------------------------------ iconos y widgets
def _icono_del_tema(*nombres):
    """El primero de estos nombres que exista en el tema de iconos (Papirus/Adwaita); si ninguno, el último."""
    pantalla = Gdk.Display.get_default()
    tema = Gtk.IconTheme.get_for_display(pantalla) if pantalla is not None else None
    for n in nombres:
        if tema is not None and tema.has_icon(n):
            return n
    return nombres[-1]


def _simbolo(nombres, respaldo, tam=14):
    """Un icono simbólico del tema; si el tema no tiene ninguno de `nombres`, un texto (`respaldo`) en su lugar."""
    pantalla = Gdk.Display.get_default()
    tema = Gtk.IconTheme.get_for_display(pantalla) if pantalla is not None else None
    for n in nombres:
        if tema is not None and tema.has_icon(n):
            img = Gtk.Image.new_from_icon_name(n)
            img.set_pixel_size(tam)
            return img
    return Gtk.Label(label=respaldo)


def ruta_icono_tienda():
    """El SVG de la propia Tienda: el instalado o el del repositorio (al desarrollar). None si no está."""
    aqui = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(aqui, "..", "..", "overlay", "usr", "share", "infinity"))
    for carpeta in (RUTA_BASE_SISTEMA, repo):      # primero la imagen elegida (tienda.png); el SVG queda de respaldo
        ruta = os.path.join(carpeta, "tienda.png")
        if os.path.isfile(ruta):
            return ruta
    return C.ruta_icono("tienda", carpetas=(RUTA_BASE_SISTEMA, repo))


def _textura(ruta):
    """El icono de `ruta` como textura, o None si no existe o no se puede leer (p. ej. sin el cargador de SVG)."""
    if not ruta:
        return None
    try:
        return Gdk.Texture.new_from_filename(ruta)
    except GLib.Error:
        return None


def imagen_de_app(entrada, tam=TAM_ICONO):
    """El icono de una app a `tam` px. Si su SVG falta o no se puede leer, un símbolo del tema sobre una baldosa:
    nunca queda el icono de «imagen que falta»."""
    textura = _textura(C.ruta_icono(entrada))
    if textura is not None:
        img = Gtk.Image.new_from_paintable(textura)
        img.set_pixel_size(tam)
        return img
    img = Gtk.Image.new_from_icon_name(_icono_del_tema("application-x-executable-symbolic", "package-x-generic-symbolic"))
    img.set_pixel_size(tam // 2)
    img.set_size_request(tam, tam)
    img.add_css_class("tienda-icono-respaldo")
    return img


def imagen_de_la_tienda(tam):
    """El icono de la marca «Tienda» de la cabecera."""
    textura = _textura(ruta_icono_tienda())
    if textura is not None:
        img = Gtk.Image.new_from_paintable(textura)
    else:
        img = Gtk.Image.new_from_icon_name(_icono_del_tema("system-software-install-symbolic", "folder-download-symbolic",
                                                           "package-x-generic-symbolic"))
        img.add_css_class("tienda-marca")      # el símbolo del tema se tiñe de violeta como el nombre
    img.set_pixel_size(tam)
    return img


def _etiqueta(texto="", *clases, ajustar=False, lineas=0, ancho=0, xalign=0.0, **props):
    e = Gtk.Label(label=texto, xalign=xalign, wrap=ajustar, **props)
    if ajustar:
        e.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lineas:
        e.set_lines(lineas)
        e.set_ellipsize(Pango.EllipsizeMode.END)
    if ancho:
        e.set_max_width_chars(ancho)
    for c in clases:
        e.add_css_class(c)
    return e


def _boton(texto, *clases, al_pulsar=None, accesible=None, tooltip=None, acortar=False):
    """Un botón de texto. Con `acortar`, su etiqueta se corta con «…» antes que ensanchar la tarjeta."""
    b = Gtk.Button(label=texto)
    for c in clases:
        b.add_css_class(c)
    etiqueta = b.get_child()
    if acortar and isinstance(etiqueta, Gtk.Label):
        etiqueta.set_ellipsize(Pango.EllipsizeMode.END)
    if al_pulsar is not None:
        b.connect("clicked", lambda *_: al_pulsar())
    if accesible:
        b.update_property([Gtk.AccessibleProperty.LABEL], [accesible])
    if tooltip:
        b.set_tooltip_text(tooltip)
    return b


def _boton_icono(iconos, accesible, al_pulsar, *clases):
    """Un botón pequeño de solo un icono simbólico del tema (el primero de `iconos` que exista): lleva su nombre para
    los lectores de pantalla y como tooltip."""
    b = Gtk.Button(icon_name=_icono_del_tema(*iconos), tooltip_text=accesible)
    b.add_css_class("flat")
    b.add_css_class("tienda-icono")
    for c in clases:
        b.add_css_class(c)
    b.update_property([Gtk.AccessibleProperty.LABEL], [accesible])
    b.connect("clicked", lambda *_: al_pulsar())
    return b


def _vaciar(contenedor):
    hijo = contenedor.get_first_child()
    while hijo is not None:
        siguiente = hijo.get_next_sibling()
        contenedor.remove(hijo)
        hijo = siguiente


def _margenes(widget, lados, px):
    for lado in lados:
        getattr(widget, f"set_margin_{lado}")(px)


# ------------------------------------------------------------------------------- abrir apps y enlaces
def _lanzar_orden(argv):
    """Lanza una orden suelta en su propia sesión (no muere con la Tienda) sin esperarla. Lanza OSError si no arranca."""
    proceso = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               close_fds=True, start_new_session=True)
    threading.Thread(target=proceso.wait, name="tienda-abrir", daemon=True).start()   # recoge al hijo cuando acabe


def lanzar_app(como, pantalla=None):
    """Abre una app instalada. `como` es lo que devuelve tienda_ops.como_abrir: ("archivo", ruta .desktop),
    ("id", id .desktop) u ("orden", argv). No espera a la app. Lanza OSError / GLib.Error / ValueError si no se puede."""
    metodo, valor = como
    if metodo in ("archivo", "id"):
        try:
            info = Gio.DesktopAppInfo.new_from_filename(valor) if metodo == "archivo" else Gio.DesktopAppInfo.new(valor)
        except TypeError:     # GLib no crea la ficha si el programa de Exec ya no existe (PyGObject: «constructor returned NULL»)
            info = None
        if info is None:
            raise OSError("su lanzador no es válido o su programa ya no está instalado")
        contexto = pantalla.get_app_launch_context() if pantalla is not None else None
        info.launch([], contexto)
    elif metodo == "orden":
        _lanzar_orden(list(valor))
    else:
        raise ValueError(f"forma de abrir desconocida: {metodo!r}")


def _xdg_open(url):
    try:
        _lanzar_orden(["xdg-open", url])
    except OSError:
        return False
    return True


def abrir_enlace(url, ventana=None, al_fallar=None):
    """Abre una web (solo https://) con el navegador de siempre, sin bloquear la ventana: Gtk.UriLauncher y, si no
    puede, `xdg-open`. Devuelve False si no se intentó. `al_fallar(texto)` se llama si ninguna de las dos funciona."""
    if not isinstance(url, str) or not url.startswith("https://"):
        return False
    if ventana is None or not hasattr(Gtk, "UriLauncher"):
        return _xdg_open(url)

    def listo(lanzador, resultado):
        try:
            lanzador.launch_finish(resultado)
        except GLib.Error as e:
            if e.matches(Gtk.dialog_error_quark(), Gtk.DialogError.DISMISSED):
                return        # lo canceló el usuario
            if not _xdg_open(url) and al_fallar is not None:
                al_fallar("No se pudo abrir el enlace.")

    Gtk.UriLauncher.new(url).launch(ventana, None, listo)
    return True


# ------------------------------------------------------------------------------------------ tarjeta
class Tarjeta(Gtk.Box):
    """Una app del catálogo: franja de color, icono, nombre, chips de tipo, resumen, detalles desplegables y un pie
    que cambia según el estado (ver modo_de). Sabe repintarse a partir de (estado, trabajo, ultimo).

    `acciones` es quien hace de verdad lo que se pulsa (la ventana): pedir_instalar, pedir_actualizar, pedir_quitar
    (se llama DESPUÉS de la confirmación en línea), pedir_reparar, reintentar, sacar_de_cola, abrir_app y abrir_web."""

    def __init__(self, entrada, acciones, estado=O.SIN_INSTALAR, trabajo=None, ultimo=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, accessible_role=Gtk.AccessibleRole.GROUP)
        self.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.DESCRIPTION], [entrada.nombre, entrada.resumen])
        self.entrada = entrada
        self._acciones = acciones
        self.modo = None
        self.confirmando = False       # «¿Quitar X?» a la vista dentro de la tarjeta
        self.expandida = False
        self.reconstrucciones = 0      # cuántas veces se ha rehecho el pie (las pruebas miran que no sean de más)
        self.boton_principal = self.boton_cola = self.menu = self.barra = None
        self.texto_estado = self.porcentaje = self.boton_si = self.boton_no = self.boton_descartar = None
        self.etiqueta_error = self.aviso_reparar = self.etiqueta_detalle = None
        self.caja_confirmar = self.texto_confirmar = None
        self._datos = (O.SIN_INSTALAR, None, None)
        self._clave_pie = None
        self._descartado = None        # el fallo que el usuario cerró con la «✕»
        self._id_pulso = 0
        self.add_css_class("tienda-tarjeta")
        self.set_overflow(Gtk.Overflow.HIDDEN)     # la franja de color sigue la curva de las esquinas
        self.tiene_detalles = bool(entrada.descripcion or entrada.aviso or entrada.web)
        self._construir()
        self._crear_acciones()
        self.actualizar(estado, trabajo, ultimo)

    # ---------------------------------------------------------------------------- construcción
    def _construir(self):
        e = self.entrada
        franja = Gtk.Box()
        franja.add_css_class("tienda-franja")
        franja.add_css_class(clase_color(e.id))
        self.append(franja)
        cuerpo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)    # sin spacing: los huecos van en cada pieza
        _margenes(cuerpo, ("start", "end"), 14)
        cuerpo.set_margin_top(14)
        cuerpo.set_margin_bottom(14)
        self.append(cuerpo)

        # Cabecera: icono (con la marca de «instalada») y, al lado, nombre y chips. Un clic en ella despliega los detalles.
        self.cabecera = Gtk.Box(spacing=11)
        clic = Gtk.GestureClick()
        clic.connect("pressed", lambda *_: self.alternar_detalles())
        self.cabecera.add_controller(clic)
        if self.tiene_detalles:
            self.cabecera.set_cursor_from_name("pointer")
        self.icono = imagen_de_app(e)
        superposicion = Gtk.Overlay(valign=Gtk.Align.CENTER)
        superposicion.set_child(self.icono)
        self.marca_instalada = Gtk.Box(halign=Gtk.Align.END, valign=Gtk.Align.END)
        self.marca_instalada.add_css_class("tienda-marca-ok")
        self.marca_instalada.append(_simbolo(("object-select-symbolic", "emblem-ok-symbolic"), "✓", 11))
        superposicion.add_overlay(self.marca_instalada)
        textos = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, valign=Gtk.Align.CENTER, hexpand=True)
        self.nombre = _etiqueta(e.nombre, "tienda-nombre", ajustar=True, lineas=2, ancho=14)
        self.chips = Gtk.Box(spacing=4)
        self.chip_tipo = _etiqueta(e.etiqueta_tipo.upper(), "tienda-chip", "tienda-chip-tipo")
        self.chips.append(self.chip_tipo)
        self.chip_drm = None
        if e.drm:
            self.chip_drm = _etiqueta("DRM", "tienda-chip", "tienda-chip-drm")
            self.chip_drm.set_tooltip_text("Usa DRM (Widevine): necesita Google Chrome o un navegador con Widevine")
            self.chips.append(self.chip_drm)
        textos.append(self.nombre)
        textos.append(self.chips)
        self.cabecera.append(superposicion)
        self.cabecera.append(textos)
        self.cabecera.set_margin_bottom(10)
        cuerpo.append(self.cabecera)

        self.resumen = _etiqueta(e.resumen, "tienda-resumen", ajustar=True, lineas=3, ancho=30)
        cuerpo.append(self.resumen)

        # Detalles: descripción completa, aviso (caja ámbar) y enlace a la web
        self.detalles = Gtk.Revealer(transition_type=Gtk.RevealerTransitionType.SLIDE_DOWN, transition_duration=160)
        interior = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        interior.set_margin_top(12)
        interior.append(Gtk.Separator())
        self.descripcion = self.caja_aviso = self.boton_web = None
        if e.descripcion:
            self.descripcion = _etiqueta(e.descripcion, "tienda-descripcion", ajustar=True, ancho=30)
            interior.append(self.descripcion)
        if e.aviso:
            self.caja_aviso = Gtk.Box(spacing=8)
            self.caja_aviso.add_css_class("tienda-aviso")
            icono = _simbolo(("dialog-warning-symbolic", "emblem-important-symbolic"), "!", 14)
            icono.set_valign(Gtk.Align.START)
            self.caja_aviso.append(icono)
            self.caja_aviso.append(_etiqueta(e.aviso, ajustar=True, ancho=30, hexpand=True))
            interior.append(self.caja_aviso)
        if e.web:
            dominio = (urlsplit(e.web).hostname or e.web).removeprefix("www.")
            self.boton_web = _boton(f"{dominio} ↗", "flat", "tienda-enlace", al_pulsar=lambda: self._acciones.abrir_web(e.web),
                                    accesible=f"Abrir la web de {e.nombre}", tooltip=e.web)
            self.boton_web.set_halign(Gtk.Align.START)
            interior.append(self.boton_web)
        self.detalles.set_child(interior)
        cuerpo.append(self.detalles)

        cuerpo.append(Gtk.Box(vexpand=True))      # empuja el pie hacia abajo: los pies de una fila quedan alineados
        self.zona_mensaje = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.zona_mensaje.set_margin_top(10)
        cuerpo.append(self.zona_mensaje)
        self.fila = Gtk.Box(spacing=8)
        self.fila.set_margin_top(10)
        self.zona_accion = Gtk.Box(spacing=4, hexpand=True, valign=Gtk.Align.CENTER)
        self.etiqueta_tamano = _etiqueta(formatear_tamano(e.tamano_mb), "tienda-tamano", valign=Gtk.Align.CENTER,
                                         ellipsize=Pango.EllipsizeMode.END)
        self.boton_info = Gtk.ToggleButton(label="i", valign=Gtk.Align.CENTER, tooltip_text="Más información")
        self.boton_info.add_css_class("tienda-info")
        self.boton_info.update_property([Gtk.AccessibleProperty.LABEL], [f"Más información sobre {e.nombre}"])
        self.boton_info.connect("toggled", lambda b: self._poner_detalles(b.get_active()))
        self.boton_info.set_visible(self.tiene_detalles)
        self.fila.append(self.zona_accion)
        self.fila.append(self.etiqueta_tamano)
        self.fila.append(self.boton_info)
        cuerpo.append(self.fila)

    def _crear_acciones(self):
        """Las acciones del menú ⋮ de la tarjeta (prefijo «tarjeta.»): así el menú no depende de la ventana. Se ejecutan
        en cuanto el bucle queda libre (no dentro del clic del menú): lo que hacen repinta el pie y quita el propio botón
        ⋮ y su menú, y eso no se hace mientras el menú aún se está cerrando."""
        grupo = Gio.SimpleActionGroup()
        for nombre, funcion in (("actualizar", lambda: self._acciones.pedir_actualizar(self.entrada)),
                                ("quitar", self.pedir_confirmacion)):
            accion = Gio.SimpleAction.new(nombre, None)
            accion.connect("activate", lambda _a, _p, f=funcion: GLib.idle_add(lambda: (f(), False)[1]))
            grupo.add_action(accion)
        self.insert_action_group("tarjeta", grupo)

    # ------------------------------------------------------------------------------- detalles
    def alternar_detalles(self):
        if self.tiene_detalles:
            self.boton_info.set_active(not self.boton_info.get_active())

    def _poner_detalles(self, abrir):
        self.expandida = bool(abrir)
        self.detalles.set_reveal_child(self.expandida)
        (self.add_css_class if self.expandida else self.remove_css_class)("tienda-expandida")

    # ---------------------------------------------------------------------------------- estado
    def actualizar(self, estado, trabajo=None, ultimo=None):
        """Repinta la tarjeta. El pie solo se rehace si cambia lo que enseña (el botón, el mensaje…); un avance de
        progreso cambia la barra y el texto, nada más."""
        if ultimo is not None and ultimo is self._descartado:
            ultimo = None
        self._datos = (estado, trabajo, ultimo)
        modo = modo_de(estado, trabajo, ultimo)
        if modo not in ("abrir", "reparar"):
            self.confirmando = False       # la confirmación de «Quitar» solo vale con la app a mano
        self.modo = modo
        self.marca_instalada.set_visible(estado.instalada and estado.funciona)
        self.marca_instalada.set_tooltip_text("Instalada" + (f" · versión {estado.version}" if estado.version else ""))
        self.etiqueta_tamano.set_visible(bool(self.entrada.tamano_mb) and modo in ("instalar", "fallo") and not estado.instalada)
        clave = (modo, self.confirmando, estado.funciona, estado.detalle if modo == "reparar" else "",
                 ultimo if modo == "fallo" else None)
        if clave != self._clave_pie:
            self._clave_pie = clave
            self._reconstruir_pie(modo, estado, ultimo)
        if modo == "curso":
            self._actualizar_progreso(trabajo)

    def _repintar(self):
        self.actualizar(*self._datos)

    def _foco_en_el_pie(self):
        """¿Tiene el foco del teclado alguno de los botones del pie? (se pierde si ese botón se quita)"""
        raiz = self.get_root()
        foco = raiz.get_focus() if raiz is not None else None
        return foco is not None and (foco is self.zona_accion or foco.is_ancestor(self.zona_accion))

    def _devolver_el_foco(self):
        """Tras rehacer el pie, el foco vuelve a su botón principal (o al siguiente que valga): sin esto, quien usa el
        teclado perdería su sitio al pulsar «Instalar»."""
        for candidato in (self.boton_principal, self.boton_cola, self.menu, self.boton_info):
            if candidato is not None and candidato.get_visible() and candidato.get_sensitive():
                candidato.grab_focus()
                return

    def _reconstruir_pie(self, modo, estado, ultimo):
        self.reconstrucciones += 1
        self._parar_pulso()
        tenia_foco = self._foco_en_el_pie()
        if self.menu is not None:
            self.menu.popdown()
        _vaciar(self.zona_mensaje)
        _vaciar(self.zona_accion)
        self.boton_principal = self.boton_cola = self.menu = self.barra = None
        self.texto_estado = self.porcentaje = self.boton_si = self.boton_no = self.boton_descartar = None
        self.etiqueta_error = self.aviso_reparar = self.etiqueta_detalle = None
        self.caja_confirmar = self.texto_confirmar = None
        self.fila.set_visible(not self.confirmando)
        if self.confirmando:
            self._pie_confirmar()
        else:
            getattr(self, f"_pie_{modo}")(estado, ultimo)
        self.zona_mensaje.set_visible(self.zona_mensaje.get_first_child() is not None)
        if tenia_foco and not self.confirmando:
            self._devolver_el_foco()

    def _principal(self, texto, al_pulsar, *clases, accesible=None):
        self.boton_principal = _boton(texto, *clases, al_pulsar=al_pulsar,
                                      accesible=accesible or f"{texto} {self.entrada.nombre}")
        self.zona_accion.append(self.boton_principal)

    def _pie_instalar(self, _estado, _ultimo):
        self._principal("Instalar", lambda: self._acciones.pedir_instalar(self.entrada), "suggested-action")

    def _pie_cola(self, _estado, _ultimo):
        self._principal("En cola…", None, "tienda-espera", accesible=f"{self.entrada.nombre}: en cola")
        self.boton_principal.set_sensitive(False)
        self.boton_cola = _boton("Quitar de la cola", "flat", "tienda-mini", accesible=f"Quitar {self.entrada.nombre} de la cola",
                                 al_pulsar=lambda: self._acciones.sacar_de_cola(self.entrada), acortar=True)
        self.zona_accion.append(self.boton_cola)

    def _pie_curso(self, _estado, _ultimo):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        self.barra = Gtk.ProgressBar(hexpand=True, valign=Gtk.Align.CENTER)
        self.barra.add_css_class("tienda-progreso")
        self.barra.update_property([Gtk.AccessibleProperty.LABEL], [f"Progreso de {self.entrada.nombre}"])
        self.porcentaje = _etiqueta("", "tienda-porcentaje", xalign=1.0, width_chars=5)
        fila = Gtk.Box(spacing=8)
        fila.append(self.barra)
        fila.append(self.porcentaje)
        self.texto_estado = _etiqueta("", "tienda-progreso-texto", ajustar=True, lineas=2, ancho=28)
        caja.append(fila)
        caja.append(self.texto_estado)
        self.zona_accion.append(caja)

    def _actualizar_progreso(self, trabajo):
        texto = trabajo.texto or "Trabajando…"
        self.texto_estado.set_text(texto)
        self.texto_estado.set_tooltip_text(texto)
        if trabajo.progreso is None:
            self.porcentaje.set_text("")
            self._empezar_pulso()
        else:
            self._parar_pulso()
            fraccion = max(0.0, min(1.0, trabajo.progreso))
            self.barra.set_fraction(fraccion)
            self.porcentaje.set_text(f"{round(fraccion * 100)} %")

    def _pie_abrir(self, _estado, _ultimo):
        self._principal("Abrir", lambda: self._acciones.abrir_app(self.entrada), "suggested-action")
        self._poner_menu(con_actualizar=self.entrada.tipo in ("apt", "flatpak"))

    def _pie_reparar(self, estado, _ultimo):
        texto = estado.detalle or "Su navegador ya no está instalado: instala otro para que vuelva a abrir."
        self.aviso_reparar = Gtk.Box(spacing=8)
        self.aviso_reparar.add_css_class("tienda-aviso")
        icono = _simbolo(("dialog-warning-symbolic", "emblem-important-symbolic"), "!", 14)
        icono.set_valign(Gtk.Align.START)
        self.aviso_reparar.append(icono)
        self.etiqueta_detalle = _etiqueta(texto, ajustar=True, lineas=4, ancho=28, hexpand=True)
        self.etiqueta_detalle.set_tooltip_text(texto)
        self.aviso_reparar.append(self.etiqueta_detalle)
        self.zona_mensaje.append(self.aviso_reparar)
        self._principal("Reparar", lambda: self._acciones.pedir_reparar(self.entrada), "suggested-action")
        self._poner_menu(con_actualizar=False)

    def _pie_fallo(self, _estado, ultimo):
        caja = Gtk.Box(spacing=6)
        caja.add_css_class("tienda-error")
        self.etiqueta_error = _etiqueta(ultimo.mensaje, ajustar=True, lineas=5, ancho=28, hexpand=True)
        self.etiqueta_error.set_tooltip_text(ultimo.mensaje)
        self.boton_descartar = _boton_icono(("window-close-symbolic", "edit-delete-symbolic"),
                                            f"Descartar el aviso de {self.entrada.nombre}", self.descartar_fallo)
        self.boton_descartar.set_valign(Gtk.Align.START)
        caja.append(self.etiqueta_error)
        caja.append(self.boton_descartar)
        self.zona_mensaje.append(caja)
        self._principal("Reintentar", lambda u=ultimo: self._acciones.reintentar(self.entrada, u), "suggested-action")

    def descartar_fallo(self):
        """Cierra el mensaje de error de la tarjeta (el Gestor guarda el último trabajo para siempre; esto solo lo oculta)."""
        self._descartado = self._datos[2]
        self._repintar()

    def _poner_menu(self, con_actualizar):
        modelo = Gio.Menu()
        if con_actualizar:
            modelo.append("Actualizar", "tarjeta.actualizar")
        modelo.append("Quitar", "tarjeta.quitar")
        nombre = f"Más acciones de {self.entrada.nombre}"
        self.menu = Gtk.MenuButton(icon_name=_icono_del_tema("view-more-symbolic", "open-menu-symbolic"), tooltip_text=nombre,
                                   valign=Gtk.Align.CENTER)
        self.menu.add_css_class("flat")
        self.menu.add_css_class("tienda-icono")
        self.menu.update_property([Gtk.AccessibleProperty.LABEL], [nombre])
        self.menu.set_menu_model(modelo)
        self.zona_accion.append(self.menu)

    # ------------------------------------------------------------------- quitar (confirmación en línea)
    def pedir_confirmacion(self):
        """«Quitar» no actúa de inmediato: la tarjeta pregunta ahí mismo (barra roja) y solo «Sí, quitar» sigue adelante."""
        if self.modo not in ("abrir", "reparar") or self.confirmando:
            return
        self._acciones.confirmacion_abierta(self)       # la ventana cierra las demás: solo una pregunta a la vez
        self.confirmando = True
        self._repintar()
        if self.boton_no is not None:
            self.boton_no.grab_focus()       # el foco, en la opción segura

    def cancelar_confirmacion(self):
        if self.confirmando:
            self.confirmando = False
            self._repintar()

    def _confirmado(self):
        self.confirmando = False
        self._repintar()
        self._acciones.pedir_quitar(self.entrada)

    def _pie_confirmar(self):
        e = self.entrada
        texto = f"¿Quitar {e.nombre}?"
        if e.tipo == "webapp":
            texto += " Solo se borra su lanzador."
        elif e.tipo == "apt":
            texto += " Te pedirá tu contraseña de administrador."
            if e.navegador:
                texto += " Las WebApps que lo usen dejarán de abrir hasta que las repares con otro navegador."
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        caja.add_css_class("tienda-confirmar")
        self.texto_confirmar = _etiqueta(texto, ajustar=True, ancho=28)
        botones = Gtk.Box(spacing=8, halign=Gtk.Align.END)
        self.boton_no = _boton("Cancelar", al_pulsar=self.cancelar_confirmacion, accesible=f"No quitar {e.nombre}")
        self.boton_si = _boton("Sí, quitar", "destructive-action", al_pulsar=self._confirmado, accesible=f"Sí, quitar {e.nombre}")
        botones.append(self.boton_no)
        botones.append(self.boton_si)
        caja.append(self.texto_confirmar)
        caja.append(botones)
        self.caja_confirmar = caja
        self.zona_mensaje.append(caja)

    # ----------------------------------------------------------------------------- pulso de la barra
    def _empezar_pulso(self):
        if not self._id_pulso:
            self._id_pulso = GLib.timeout_add(110, self._pulsar)

    def _pulsar(self):
        if self.barra is None or self.modo != "curso":
            self._id_pulso = 0
            return False
        self.barra.pulse()
        return True

    def _parar_pulso(self):
        if self._id_pulso:
            GLib.source_remove(self._id_pulso)
            self._id_pulso = 0

    def detener(self):
        """Para los temporizadores de la tarjeta (al cerrar la ventana)."""
        self._parar_pulso()


# ------------------------------------------------------------------------------------ panel en línea
class PanelPregunta(Gtk.Box):
    """El velo oscuro con un cuadro centrado para las preguntas («falta un navegador», «poco espacio»). Va DENTRO de la
    ventana, en un Gtk.Overlay: no es un diálogo ni un popover. Un clic fuera del cuadro o Esc equivalen a «Cancelar»."""

    def __init__(self):
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("tienda-velo")
        self.al_cerrar = None            # la ventana se entera de que se cerró (para devolver el foco)
        self._al_cancelar = None
        self.botones = []
        self.cuadro = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER,
                              valign=Gtk.Align.CENTER, hexpand=True, vexpand=True, width_request=470,
                              accessible_role=Gtk.AccessibleRole.ALERT_DIALOG)
        self.cuadro.add_css_class("tienda-panel")
        self.cabecera = Gtk.Box(spacing=14)
        self.hueco_icono = Gtk.Box(valign=Gtk.Align.START)        # el icono de la app sobre la que se pregunta
        self.titulo = _etiqueta("", "tienda-panel-titulo", ajustar=True, ancho=40, hexpand=True, valign=Gtk.Align.CENTER)
        self.cabecera.append(self.hueco_icono)
        self.cabecera.append(self.titulo)
        self.texto = _etiqueta("", "tienda-panel-texto", ajustar=True, ancho=40)
        self.nota = _etiqueta("", "tienda-panel-nota", ajustar=True, ancho=40)
        self.caja_opciones = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.caja_opciones.set_margin_top(6)
        self.boton_cancelar = _boton("Cancelar", al_pulsar=self.cancelar)
        self.boton_cancelar.set_halign(Gtk.Align.END)
        for w in (self.cabecera, self.texto, self.nota, self.caja_opciones, self.boton_cancelar):
            self.cuadro.append(w)
        self.append(self.cuadro)
        clic = Gtk.GestureClick()
        clic.connect("pressed", self._clic_en_el_velo)
        self.add_controller(clic)
        self.set_visible(False)

    def _clic_en_el_velo(self, _gesto, _n, x, y):
        ok, caja = self.cuadro.compute_bounds(self)
        if ok and caja.get_x() <= x <= caja.get_x() + caja.get_width() and caja.get_y() <= y <= caja.get_y() + caja.get_height():
            return
        self.cancelar()

    def preguntar(self, titulo, texto, opciones, nota="", al_cancelar=None, cancelar="Cancelar", entrada=None):
        """Enseña la pregunta. `opciones` son OpcionPanel; al elegir una se cierra el panel y se llama a su acción.
        `entrada`: la app de la que se habla (su icono va junto al título)."""
        self._al_cancelar = al_cancelar
        _vaciar(self.hueco_icono)
        if entrada is not None:
            self.hueco_icono.append(imagen_de_app(entrada, 44))
        self.hueco_icono.set_visible(entrada is not None)
        self.cuadro.update_property([Gtk.AccessibleProperty.LABEL], [titulo])
        self.titulo.set_text(titulo)
        self.texto.set_text(texto)
        self.nota.set_text(nota)
        self.nota.set_visible(bool(nota))
        _vaciar(self.caja_opciones)
        self.botones = []
        for op in opciones:
            boton = self._boton_opcion(op)
            self.caja_opciones.append(boton)
            self.botones.append(boton)
        self.caja_opciones.set_visible(bool(self.botones))
        self.boton_cancelar.set_label(cancelar)
        self.set_visible(True)
        principal = next((b for b in self.botones if b.has_css_class("suggested-action")), None)
        (principal or self.boton_cancelar).grab_focus()     # sin una opción principal, el foco, en la salida segura

    def _boton_opcion(self, op):
        caja = Gtk.Box(spacing=10)
        if op.entrada is not None:
            caja.append(imagen_de_app(op.entrada, 28))
        caja.append(_etiqueta(op.texto, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
        if op.marca:
            caja.append(_etiqueta(op.marca.upper(), "tienda-chip", "tienda-chip-marca", valign=Gtk.Align.CENTER))
        if op.detalle:
            caja.append(_etiqueta(op.detalle, "tienda-tamano", valign=Gtk.Align.CENTER))
        boton = Gtk.Button()
        boton.set_child(caja)
        boton.add_css_class("tienda-opcion")
        if op.principal:
            boton.add_css_class("suggested-action")
        boton.update_property([Gtk.AccessibleProperty.LABEL], [op.texto + (f" ({op.marca})" if op.marca else "")])
        boton.connect("clicked", lambda *_: self._elegida(op))
        boton.opcion = op
        return boton

    def _elegida(self, op):
        self.cerrar()
        op.accion()

    def cancelar(self):
        al_cancelar = self._al_cancelar
        self.cerrar()
        if al_cancelar is not None:
            al_cancelar()

    def cerrar(self):
        visible = self.get_visible()
        self._al_cancelar = None
        self.set_visible(False)
        if visible and self.al_cerrar is not None:
            self.al_cerrar()


# ---------------------------------------------------------------------------------- barra de resultado
class BarraResultado(Gtk.Box):
    """La barra de abajo: cuenta cómo acabó un trabajo (verde si fue bien, roja si falló, ámbar si es un aviso), con
    «Ver registro» (las últimas líneas de lo que dijo la orden) y «Cerrar». Lo que no es un error se esconde solo."""

    def __init__(self, segundos_exito=SEGUNDOS_EXITO):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, accessible_role=Gtk.AccessibleRole.STATUS)
        self.add_css_class("tienda-barra")
        self.segundos_exito = segundos_exito
        self.tipo = "ok"
        self._id_ocultar = 0
        fila = Gtk.Box(spacing=10)
        self.etiqueta = _etiqueta("", ajustar=True, hexpand=True, valign=Gtk.Align.CENTER)
        self.boton_registro = _boton("Ver registro", "flat", "tienda-mini", al_pulsar=self.alternar_registro)
        self.boton_cerrar = _boton("Cerrar", "flat", "tienda-mini", al_pulsar=self.ocultar)
        fila.append(self.etiqueta)
        fila.append(self.boton_registro)
        fila.append(self.boton_cerrar)
        self.append(fila)
        self.vista_registro = Gtk.TextView(editable=False, cursor_visible=False, monospace=True,
                                           wrap_mode=Gtk.WrapMode.WORD_CHAR, top_margin=8, bottom_margin=8,
                                           left_margin=10, right_margin=10)
        self.vista_registro.add_css_class("tienda-mono")
        self.vista_registro.update_property([Gtk.AccessibleProperty.LABEL], ["Registro del trabajo"])
        self.caja_registro = Gtk.ScrolledWindow(min_content_height=110, max_content_height=190, propagate_natural_height=True,
                                                hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.caja_registro.add_css_class("tienda-registro")
        self.caja_registro.set_child(self.vista_registro)
        self.caja_registro.set_visible(False)
        self.append(self.caja_registro)
        buffer = self.vista_registro.get_buffer()
        self._marca_fin = buffer.create_mark("fin", buffer.get_end_iter(), False)
        self.set_visible(False)

    def mostrar(self, texto, tipo="ok", registro=None, segundos=None):
        """`registro`: las líneas del registro de un trabajo (se enseñan las últimas LINEAS_REGISTRO); None si el aviso no
        viene de un trabajo. Un trabajo que falla siempre deja «Ver registro» (aunque la orden no escribiera nada). `segundos`:
        0 = no se esconde sola; None = la regla de siempre (los errores se quedan; lo demás se esconde a los SEGUNDOS_EXITO)."""
        self.cancelar_temporizador()
        self.tipo = tipo
        for c in ("ok", "error", "aviso"):
            self.remove_css_class(c)
        self.add_css_class(tipo)
        self.etiqueta.set_text(texto)
        lineas = [str(x) for x in (registro or ())][-LINEAS_REGISTRO:]
        hay_registro = registro is not None and (bool(lineas) or tipo == "error")
        if hay_registro and not lineas:
            lineas = ["(La orden no escribió nada en el registro.)"]
        self.vista_registro.get_buffer().set_text("\n".join(lineas))
        self.boton_registro.set_visible(hay_registro)
        self._poner_registro(False)
        self.set_visible(True)
        if segundos is None:
            segundos = self.segundos_exito if tipo in ("ok", "aviso") else 0
        if segundos:
            self._id_ocultar = GLib.timeout_add(max(1, int(segundos * 1000)), self._ocultar_auto)

    def _ocultar_auto(self):
        self._id_ocultar = 0
        if not self.caja_registro.get_visible():     # si estás leyendo el registro, no se te quita de delante
            self.ocultar()
        return False

    def ocultar(self):
        self.cancelar_temporizador()
        self.etiqueta.set_text("")
        self.vista_registro.get_buffer().set_text("")
        self._poner_registro(False)
        self.set_visible(False)

    def cancelar_temporizador(self):
        if self._id_ocultar:
            GLib.source_remove(self._id_ocultar)
            self._id_ocultar = 0

    def alternar_registro(self):
        self._poner_registro(not self.caja_registro.get_visible())

    def _poner_registro(self, ver):
        self.caja_registro.set_visible(ver)
        self.boton_registro.set_label("Ocultar registro" if ver else "Ver registro")
        if ver:
            buffer = self.vista_registro.get_buffer()
            buffer.move_mark(self._marca_fin, buffer.get_end_iter())
            self.vista_registro.scroll_to_mark(self._marca_fin, 0.0, True, 0.0, 1.0)     # a las últimas líneas


# --------------------------------------------------------------------------------------- ventana
class VentanaTienda(Gtk.ApplicationWindow):
    """La ventana de la Tienda. Los parámetros existen para las pruebas: `gestor` (por defecto, un tienda_ops.Gestor
    real con su Ejecutor en hilos), `catalogo` (por defecto el del gestor o el del sistema), `abrir(entrada, como)`
    (lo que abre una app instalada; `como` es lo que devuelve tienda_ops.como_abrir), `abrir_url(url)` y `persistencia`
    (un ajustes.Persistencia, o una función que lo da; por defecto se calcula al abrir). La aplicación solo pasa
    `ruta_catalogo` (--catalogo)."""

    def __init__(self, app, gestor=None, catalogo=None, abrir=None, persistencia=None, *, abrir_url=None,
                 ruta_catalogo=None, segundos_exito=SEGUNDOS_EXITO):
        super().__init__(application=app, title="Tienda", default_width=1000, default_height=680)
        self.add_css_class("tienda-ventana")
        self.set_size_request(820, 520)
        self.error_catalogo = ""
        if gestor is None:
            if catalogo is None:
                catalogo, self.error_catalogo = self._leer_catalogo(ruta_catalogo)
            gestor = O.Gestor(catalogo, ejecutor=O.Ejecutor(despachar=GLib.idle_add))
        elif catalogo is None:
            catalogo = gestor.catalogo
        self.gestor, self.catalogo = gestor, catalogo
        pantalla = self.get_display()
        _cargar_estilo(pantalla)
        _cargar_colores(pantalla, catalogo.apps)
        self._abrir = abrir or (lambda entrada, como: lanzar_app(como, self.get_display()))
        self._abrir_url = abrir_url or (lambda url: abrir_enlace(url, self, al_fallar=lambda t: self._avisar(t, "error")))
        self._persistencia = self._leer_persistencia(persistencia)
        self._segundos_exito = segundos_exito
        self._vista = VISTA_TODO
        self._tarjetas = {}          # id → Tarjeta (se crean al hacer falta y se reutilizan: no se rehacen al filtrar)
        self._ids_visibles = []      # los ids de la rejilla, en orden
        self._relleno = []           # cajas vacías que completan la última fila (así las columnas no se ensanchan)
        self._filas = {}             # vista → fila del lateral
        self._contadores = {}        # vista → etiqueta con su número
        self._instaladas = frozenset()   # los ids instalados la última vez que se miró
        self._cerrada = False
        self._retenida = False       # ¿la aplicación está «retenida» (hold) para acabar los trabajos con la ventana oculta?
        self._foco_previo = None

        self._construir()
        self._montar_controladores()
        gestor.al_cambiar = self._al_cambiar
        gestor.al_terminar_trabajo = self._al_terminar_trabajo
        self.connect("close-request", self._al_pedir_cierre)
        self._actualizar_contadores()
        self._aplicar_filtro(forzar=True)
        self.busqueda.grab_focus()

    # ----------------------------------------------------------------------------- datos de entrada
    @staticmethod
    def _leer_catalogo(ruta):
        try:
            return C.cargar(ruta or C.ruta_catalogo()), ""
        except C.CatalogoError as e:
            return C.Catalogo(), str(e)

    @staticmethod
    def _leer_persistencia(persistencia):
        """El estado de guardado del arranque (se mira una vez, al abrir). Si no se puede saber, None (sin franja)."""
        try:
            if callable(persistencia):
                return persistencia()
            return persistencia if persistencia is not None else ajustes.estado_persistencia()
        except (OSError, ValueError):
            return None

    # ======================================================================================= construcción
    def _construir(self):
        self._construir_cabecera()
        self.cuerpo = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.franja_vivo = self._construir_franja_vivo()
        self.cuerpo.append(self.franja_vivo)
        centro = Gtk.Box(vexpand=True)
        centro.append(self._construir_lateral())
        centro.append(self._construir_contenido())
        self.cuerpo.append(centro)
        self.barra = BarraResultado(self._segundos_exito)
        self.cuerpo.append(self.barra)
        self.superposicion = Gtk.Overlay()
        self.superposicion.set_child(self.cuerpo)
        self.panel = PanelPregunta()
        self.panel.al_cerrar = self._panel_cerrado
        self.superposicion.add_overlay(self.panel)
        self.set_child(self.superposicion)

    def _construir_cabecera(self):
        cab = Gtk.HeaderBar()
        cab.set_decoration_layout(":minimize,maximize,close")      # sus propios −, □, ×
        cab.add_css_class("tienda-cabecera")
        marca = Gtk.Box(spacing=8)
        marca.append(imagen_de_la_tienda(22))
        marca.append(_etiqueta("Tienda", "tienda-marca"))
        cab.pack_start(marca)
        self.busqueda = Gtk.SearchEntry(placeholder_text="Buscar aplicaciones…", width_request=380)
        self.busqueda.update_property([Gtk.AccessibleProperty.LABEL], ["Buscar aplicaciones"])
        self.busqueda.connect("search-changed", lambda *_: self._aplicar_filtro(arriba=True))
        self.busqueda.connect("stop-search", lambda *_: self._escape())
        cab.set_title_widget(self.busqueda)
        self.menu = Gtk.MenuButton(icon_name=_icono_del_tema("view-more-symbolic", "open-menu-symbolic"), tooltip_text="Menú")
        self.menu.update_property([Gtk.AccessibleProperty.LABEL], ["Menú de la Tienda"])
        self.menu.set_menu_model(self._modelo_menu())
        cab.pack_end(self.menu)
        self.set_titlebar(cab)

    def _modelo_menu(self):
        for nombre, funcion in (("actualizar-estado", self._actualizar_estado), ("acerca", self._acerca_de)):
            accion = Gio.SimpleAction.new(nombre, None)
            accion.connect("activate", lambda _a, _p, f=funcion: f())
            self.add_action(accion)
        menu = Gio.Menu()
        menu.append("Actualizar estado", "win.actualizar-estado")
        menu.append("Acerca de la Tienda", "win.acerca")
        return menu

    def _construir_franja_vivo(self):
        """Aviso del modo en vivo: ámbar, descartable, con la pista de cómo guardar en el tooltip."""
        franja = Gtk.Box(spacing=10)
        franja.add_css_class("tienda-vivo")
        icono = _simbolo(("dialog-warning-symbolic", "emblem-important-symbolic"), "!", 16)
        franja.append(icono)
        franja.append(_etiqueta("Estás en modo en vivo sin guardado: lo que instales se perderá al apagar.", ajustar=True, hexpand=True))
        self.boton_cerrar_vivo = _boton_icono(("window-close-symbolic", "edit-delete-symbolic"),
                                              "Descartar el aviso del modo en vivo", lambda: franja.set_visible(False))
        franja.append(self.boton_cerrar_vivo)
        p = self._persistencia
        vivo = p is not None and getattr(p, "modo", "") == "vivo"
        if vivo and getattr(p, "pista", ""):
            franja.set_tooltip_text(p.pista)
        franja.set_visible(vivo)
        return franja

    def _construir_lateral(self):
        lateral = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, width_request=178)
        lateral.add_css_class("tienda-lateral")
        self.lista_vistas = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista_vistas.add_css_class("tienda-categorias")
        self.lista_vistas.set_margin_top(14)
        self.lista_vistas.set_header_func(self._cabecera_de_fila)
        vistas = [(VISTA_TODO, "Todo"), (VISTA_INSTALADAS, "Instaladas")]
        vistas += [(PREFIJO_CATEGORIA + c.id, c.nombre) for c in self.catalogo.categorias]
        for clave, nombre in vistas:
            fila = Gtk.ListBoxRow()
            fila.vista = clave
            caja = Gtk.Box(spacing=8)
            caja.add_css_class("tienda-cat")
            etiqueta = _etiqueta(nombre, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
            cuenta = _etiqueta("0", "tienda-cuenta")
            caja.append(etiqueta)
            caja.append(cuenta)
            fila.set_child(caja)
            self.lista_vistas.append(fila)
            self._filas[clave] = fila
            self._contadores[clave] = cuenta
        self.lista_vistas.select_row(self._filas[VISTA_TODO])
        self.lista_vistas.connect("row-selected", self._vista_elegida)
        lateral.append(self.lista_vistas)
        return lateral

    @staticmethod
    def _cabecera_de_fila(fila, anterior):
        """El título «CATEGORÍAS» sobre la primera categoría (Todo e Instaladas van sin título)."""
        es_categoria = fila.vista.startswith(PREFIJO_CATEGORIA)
        if es_categoria and (anterior is None or not anterior.vista.startswith(PREFIJO_CATEGORIA)):
            titulo = _etiqueta("CATEGORÍAS", "tienda-lateral-titulo")
            titulo.set_margin_start(20)
            titulo.set_margin_top(16)
            titulo.set_margin_bottom(6)
            fila.set_header(titulo)
        else:
            fila.set_header(None)

    def _construir_contenido(self):
        contenido = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True)
        cab = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        _margenes(cab, ("start", "end"), 22)
        cab.set_margin_top(16)
        cab.set_margin_bottom(12)
        self.titulo = _etiqueta("Todo", "tienda-seccion")
        self.subtitulo = _etiqueta("", "tienda-seccion-sub")
        cab.append(self.titulo)
        cab.append(self.subtitulo)
        contenido.append(cab)
        self.pila = Gtk.Stack(vexpand=True, hexpand=True)
        # Gtk.Grid y no Gtk.FlowBox: el FlowBox se queda con los clics de sus hijos (ya pasó en biblioteca_ventana.py)
        self.rejilla = Gtk.Grid(column_spacing=12, row_spacing=12, column_homogeneous=True, hexpand=True,
                                valign=Gtk.Align.START)
        _margenes(self.rejilla, ("start", "end"), 20)
        self.rejilla.set_margin_top(4)
        self.rejilla.set_margin_bottom(24)
        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        self.scroll.set_child(self.rejilla)
        self.pila.add_named(self.scroll, "rejilla")
        self.pila.add_named(self._pagina_vacia(), "vacio")
        contenido.append(self.pila)
        return contenido

    def _pagina_vacia(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER,
                       width_request=320)
        self.vacio_titulo = _etiqueta("", "tienda-vacio-titulo", xalign=0.5, ajustar=True, justify=Gtk.Justification.CENTER)
        self.vacio_texto = _etiqueta("", "tienda-subtitulo", xalign=0.5, ajustar=True, justify=Gtk.Justification.CENTER)
        self.vacio_boton = _boton("Ver todo", al_pulsar=self._ver_todo)
        self.vacio_boton.set_halign(Gtk.Align.CENTER)
        for w in (self.vacio_titulo, self.vacio_texto, self.vacio_boton):
            caja.append(w)
        return caja

    # ======================================================================================= controladores
    def _montar_controladores(self):
        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.add_controller(teclas)

    def _tecla(self, _ctrl, keyval, _codigo, estado):
        """Ctrl+F busca, Ctrl+Q cierra (como el botón ×: con trabajos en marcha, oculta) y Esc va deshaciendo lo que haya."""
        control = bool(estado & Gdk.ModifierType.CONTROL_MASK)
        if control and keyval in (Gdk.KEY_f, Gdk.KEY_F):
            if not self.panel.get_visible():
                self.busqueda.grab_focus()
            return True
        if control and keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            self.close()
            return True
        if keyval == Gdk.KEY_Escape:
            return self._escape()
        return False

    def _escape(self):
        """Esc: cierra la pregunta en línea; si no, una confirmación de «Quitar»; si no, limpia la búsqueda."""
        if self.panel.get_visible():
            self.panel.cancelar()
            return True
        for tarjeta in self._tarjetas.values():
            if tarjeta.confirmando:
                tarjeta.cancelar_confirmacion()
                return True
        if self.busqueda.get_text():
            self.buscar("")
            return True
        return False

    # ============================================================================== vistas y filtro
    def visibles(self):
        """Los ids de las apps que se ven en la rejilla, en orden."""
        return list(self._ids_visibles)

    def seleccionar_vista(self, vista):
        self.lista_vistas.select_row(self._filas[vista])

    def buscar(self, texto):
        """Pone `texto` en el buscador y filtra ya (el buscador solo avisa a los ~150 ms de dejar de teclear)."""
        self.busqueda.set_text(texto)
        self._aplicar_filtro(arriba=True)

    def _vista_elegida(self, _lista, fila):
        if fila is None or fila.vista == self._vista:
            return
        self._vista = fila.vista
        self._aplicar_filtro(forzar=True, arriba=True)

    def _ver_todo(self):
        """El botón del estado vacío: quita la búsqueda si no hay nada en la vista, o sale de «Instaladas»/categoría."""
        if self._vista != VISTA_TODO:
            self.seleccionar_vista(VISTA_TODO)
        else:
            self.buscar("")

    def _instalada(self, ident):
        return self.gestor.estado(ident).instalada

    def _aplicar_filtro(self, forzar=False, arriba=False):
        """Recalcula qué tarjetas se ven. La rejilla solo se rehace si cambia el conjunto (o se fuerza); `arriba` la
        lleva al principio (al cambiar de vista o de búsqueda, no cuando cambia el estado de una app)."""
        texto = self.busqueda.get_text()
        apps = filtrar(self.catalogo, self._vista, texto, self._instalada)
        self._actualizar_titulos(len(apps), texto)
        if forzar or [a.id for a in apps] != self._ids_visibles:
            self._reconstruir_rejilla(apps, texto)
            if arriba:
                self.scroll.get_vadjustment().set_value(0)

    def _nombre_de_vista(self):
        if self._vista == VISTA_TODO:
            return "Todo"
        if self._vista == VISTA_INSTALADAS:
            return "Instaladas"
        categoria = self._vista[len(PREFIJO_CATEGORIA):]
        return next((c.nombre for c in self.catalogo.categorias if c.id == categoria), categoria)

    def _actualizar_titulos(self, n, texto):
        self.titulo.set_text(self._nombre_de_vista())
        texto = texto.strip()
        self.subtitulo.set_text(f"{_plural(n, 'resultado', 'resultados')} para «{texto}»" if texto
                                else _plural(n, "aplicación", "aplicaciones"))

    def _tarjeta(self, entrada):
        """La tarjeta de una app: se crea la primera vez que hace falta y desde entonces se reutiliza."""
        t = self._tarjetas.get(entrada.id)
        if t is None:
            t = self._tarjetas[entrada.id] = Tarjeta(entrada, self, self.gestor.estado(entrada.id),
                                                      self.gestor.trabajo(entrada.id), self.gestor.trabajos.get(entrada.id))
        return t

    def _reconstruir_rejilla(self, apps, texto=""):
        _vaciar(self.rejilla)                 # las tarjetas no se destruyen: _tarjetas las guarda
        self._relleno = []
        for i, a in enumerate(apps):
            self.rejilla.attach(self._tarjeta(a), i % COLUMNAS, i // COLUMNAS, 1, 1)
        if apps:
            for k in range((-len(apps)) % COLUMNAS):
                hueco = Gtk.Box(hexpand=True)
                self._relleno.append(hueco)
                self.rejilla.attach(hueco, COLUMNAS - 1 - k, (len(apps) - 1) // COLUMNAS, 1, 1)
        self._ids_visibles = [a.id for a in apps]
        if apps:
            self.pila.set_visible_child_name("rejilla")
        else:
            self._mostrar_vacio(texto.strip())

    def _mostrar_vacio(self, texto):
        if not self.catalogo.apps:
            titulo = "No se pudo abrir el catálogo" if self.error_catalogo else "No hay aplicaciones"
            detalle = self.error_catalogo or "El catálogo de la Tienda está vacío."
            boton = ""
        elif texto:
            titulo = f"Nada coincide con «{texto}»"
            if self._vista != VISTA_TODO:
                detalle, boton = f"No hay resultados en «{self._nombre_de_vista()}».", "Buscar en todo"
            else:
                detalle, boton = "Prueba con otra palabra.", "Borrar la búsqueda"
        elif self._vista == VISTA_INSTALADAS:
            titulo, detalle, boton = ("Aún no hay nada instalado", "Lo que instales desde la Tienda aparecerá aquí.",
                                      "Ver todo")
        else:
            titulo, detalle, boton = "No hay aplicaciones en esta categoría", "", "Ver todo"
        self.vacio_titulo.set_text(titulo)
        self.vacio_texto.set_text(detalle)
        self.vacio_texto.set_visible(bool(detalle))
        self.vacio_boton.set_label(boton)
        self.vacio_boton.set_visible(bool(boton))
        self.pila.set_visible_child_name("vacio")

    def _actualizar_contadores(self):
        """Pone los números del lateral. Devuelve True si cambió el conjunto de apps instaladas."""
        instaladas = frozenset(a.id for a in self.catalogo.apps if self._instalada(a.id))
        cambio = instaladas != self._instaladas
        self._instaladas = instaladas
        self._contadores[VISTA_TODO].set_text(str(len(self.catalogo.apps)))
        self._contadores[VISTA_INSTALADAS].set_text(str(len(instaladas)))
        for c in self.catalogo.categorias:
            self._contadores[PREFIJO_CATEGORIA + c.id].set_text(str(len(self.catalogo.de_categoria(c.id))))
        return cambio

    # ================================================================================= lo que cambia
    def _pintar(self, ident):
        """Repinta la tarjeta de `ident` (si existe) con el estado y los trabajos que dice el gestor."""
        t = self._tarjetas.get(ident)
        if t is not None:
            t.actualizar(self.gestor.estado(ident), self.gestor.trabajo(ident), self.gestor.trabajos.get(ident))

    def _al_cambiar(self, ident):
        """Gestor.al_cambiar: algo cambió en `ident` (o en todo, si es None). Solo se toca la tarjeta de ese id."""
        if self._cerrada:
            return
        if ident is None:
            for i in list(self._tarjetas):
                self._pintar(i)
        else:
            self._pintar(ident)
        cambio = self._actualizar_contadores()
        if ident is None or (cambio and self._vista == VISTA_INSTALADAS):
            self._aplicar_filtro()     # «Instaladas» puede haber ganado o perdido una app

    def _al_terminar_trabajo(self, trabajo):
        """Gestor.al_terminar_trabajo: cuenta cómo acabó y, si la ventana estaba oculta esperando al último, se despide."""
        if self._cerrada:
            return
        ok = trabajo.estado == "listo"
        texto = trabajo.mensaje or f"{trabajo.entrada.nombre}: {'hecho' if ok else 'no se pudo'}."
        self.barra.mostrar(texto, "ok" if ok else "error", list(trabajo.registro))
        if not self.gestor.ocupado:
            oculta = self._retenida and not self.get_visible()    # se cerró con trabajos en marcha y nadie la ha vuelto a abrir
            self._soltar_retencion()
            if oculta:            # ya no queda ningún trabajo y no hace falta mostrarla: la ventana se despide y la aplicación sale
                self.liberar()
                self.destroy()

    # ======================================================================== acciones de las tarjetas
    def pedir_instalar(self, entrada):
        self._pedir(entrada, O.INSTALAR)

    def pedir_actualizar(self, entrada):
        self._pedir(entrada, O.ACTUALIZAR)

    def pedir_quitar(self, entrada):
        self._pedir(entrada, O.QUITAR)

    def pedir_reparar(self, entrada):
        self._pedir(entrada, O.INSTALAR)

    def reintentar(self, entrada, ultimo):
        self._pedir(entrada, ultimo.accion if ultimo is not None else O.INSTALAR)

    def confirmacion_abierta(self, tarjeta):
        """Una tarjeta va a preguntar «¿Quitar X?»: las demás dejan de preguntar."""
        for otra in self._tarjetas.values():
            if otra is not tarjeta:
                otra.cancelar_confirmacion()

    def sacar_de_cola(self, entrada):
        if not self.gestor.quitar_de_la_cola(entrada.id):
            self._avisar(f"{entrada.nombre} ya ha empezado y no se puede quitar de la cola.", "aviso")

    def abrir_web(self, url):
        self._abrir_url(url)

    def abrir_app(self, entrada):
        como = O.como_abrir(entrada)
        if como is None:
            self._avisar(f"No encuentro cómo abrir {entrada.nombre}; reinicia la sesión o ábrela desde el lanzador.", "aviso")
            return
        try:
            resultado = self._abrir(entrada, como)
        except (OSError, ValueError, GLib.Error) as e:
            self._avisar(f"No se pudo abrir {entrada.nombre}: {getattr(e, 'message', None) or e}", "error")
            return
        if resultado is False:
            self._avisar(f"No se pudo abrir {entrada.nombre}.", "error")

    # --------------------------------------------------------------------------------- pedir al gestor
    def _pedir(self, entrada, accion, navegador=None, forzar=False):
        """Pide una acción al gestor y reacciona a su Respuesta. `navegador` y `forzar` solo se pasan si hacen falta."""
        extra = {}
        if navegador is not None:
            extra["navegador"] = navegador
        if forzar:
            extra["forzar"] = True
        try:
            respuesta = self.gestor.pedir(entrada.id, accion, **extra)
        except Exception as e:     # noqa: BLE001  (un fallo inesperado de la lógica no debe dejar la ventana muda)
            self._avisar(f"No se pudo {O.VERBOS[accion][2]} {entrada.nombre}: {e}", "error")
            return
        estado = respuesta.estado
        if estado == "ok":
            self.panel.cerrar()
        elif estado == "falta_navegador":
            self._preguntar_navegador(entrada, accion, respuesta)
        elif estado == "poco_espacio":
            self._preguntar_espacio(entrada, accion, navegador, respuesta)
        elif estado == "ocupado":
            self._avisar(respuesta.mensaje or "Ya hay una operación en marcha para esta app.", "aviso")
        else:
            self._avisar(respuesta.mensaje or f"No se pudo {O.VERBOS[accion][2]} {entrada.nombre}.", "error")

    def _preguntar_navegador(self, entrada, accion, respuesta):
        opciones = []
        for i, nav in enumerate(respuesta.opciones):
            opciones.append(OpcionPanel(f"Instalar {nav.nombre} y {entrada.nombre}",
                                        lambda n=nav: self._pedir(entrada, accion, navegador=n.id),
                                        principal=(i == 0), marca="Recomendado" if i == 0 else "",
                                        detalle=formatear_tamano(nav.tamano_mb), entrada=nav))
        nota = f"Google Chrome trae Widevine, que {entrada.nombre} necesita para reproducir vídeo." if entrada.drm else ""
        self._mostrar_panel(f"Para usar {entrada.nombre} necesitas un navegador basado en Chromium",
                            f"{entrada.nombre} se abre en su propia ventana, como una aplicación, con un navegador Chromium. "
                            f"Elige el que se instalará ahora:", opciones, nota, entrada)

    def _preguntar_espacio(self, entrada, accion, navegador, respuesta):
        nota = ""
        if getattr(self._persistencia, "modo", "") == "vivo":
            nota = "En modo en vivo sin guardado el espacio que hay es la memoria (RAM)."
        opciones = [OpcionPanel("Instalar de todos modos", lambda: self._pedir(entrada, accion, navegador=navegador, forzar=True))]
        self._mostrar_panel("¿Instalar con poco espacio?", respuesta.mensaje, opciones, nota, entrada)

    def _mostrar_panel(self, titulo, texto, opciones, nota="", entrada=None):
        self._foco_previo = self.get_focus()
        self.panel.preguntar(titulo, texto, opciones, nota, entrada=entrada)
        self.cuerpo.set_sensitive(False)      # con la pregunta a la vista, lo de detrás ni se pulsa ni recibe el tabulador

    def _panel_cerrado(self):
        self.cuerpo.set_sensitive(True)
        previo, self._foco_previo = self._foco_previo, None
        if previo is not None and previo.get_root() is self and previo.get_mapped():
            previo.grab_focus()
        else:
            self.busqueda.grab_focus()

    def _avisar(self, texto, tipo="aviso", segundos=None):
        self.barra.mostrar(texto, tipo, segundos=segundos)

    # ================================================================================= menú de la cabecera
    def _actualizar_estado(self):
        try:
            self.gestor.refrescar()
        except Exception as e:     # noqa: BLE001
            self._avisar(f"No se pudo actualizar el estado: {e}", "error")
            return
        self._avisar("Estado actualizado.", "ok", segundos=3)

    def _acerca_de(self):
        acerca = Gtk.AboutDialog(transient_for=self, modal=True, program_name="Tienda",
                                 comments="La tienda de aplicaciones de Infinity OS.\n\n"
                                          "Instala con un clic Spotify (Flatpak, sin contraseña de administrador), "
                                          "Google Antigravity, Google Chrome y Brave (desde los repositorios APT oficiales de "
                                          "sus fabricantes, con tu autorización) y WebApps (Netflix, YouTube, Claude, Gemini…: "
                                          "un lanzador que abre la web en una ventana propia de Chrome o Brave). "
                                          "Nada se instala sin que lo pidas.")
        textura = _textura(ruta_icono_tienda())
        if textura is not None:
            acerca.set_logo(textura)
        acerca.present()

    # ================================================================================ ocultar y cerrar
    def _al_pedir_cierre(self, *_):
        """Cerrar con instalaciones en marcha NO las mata: la ventana se oculta y la aplicación sigue viva hasta que acaben."""
        if self._cerrada:
            return False
        if self.gestor.ocupado:
            self.panel.cerrar()
            self.set_visible(False)
            app = self.get_application()
            if app is not None and not self._retenida:
                app.hold()
                self._retenida = True
            return True               # no se destruye
        self.liberar()
        return False

    def _soltar_retencion(self):
        if self._retenida:
            self._retenida = False
            app = self.get_application()
            if app is not None:
                app.release()

    def liberar(self):
        """Para temporizadores, suelta al gestor y la retención de la aplicación. Se puede llamar más de una vez."""
        if self._cerrada:
            return
        self._cerrada = True
        self.barra.cancelar_temporizador()
        for t in self._tarjetas.values():
            t.detener()
        if self.gestor.al_cambiar == self._al_cambiar:
            self.gestor.al_cambiar = lambda ident: None
        if self.gestor.al_terminar_trabajo == self._al_terminar_trabajo:
            self.gestor.al_terminar_trabajo = lambda trabajo: None
        self._soltar_retencion()


# ------------------------------------------------------------------------------------ aplicación
class AppTienda(Gtk.Application):
    def __init__(self, ruta_catalogo=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.ruta_catalogo = ruta_catalogo
        self.ventana = None

    def do_activate(self):
        # Una sola instancia: si ya está abierta (o oculta, esperando a que acaben las instalaciones), se trae al frente
        if self.ventana is not None and self.ventana not in self.get_windows():
            self.ventana = None            # se despidió sola al acabar el último trabajo
        if self.ventana is None:
            ajustes_gtk = Gtk.Settings.get_default()      # None si GTK no tiene pantalla
            if ajustes_gtk is not None:
                ajustes_gtk.set_property("gtk-application-prefer-dark-theme", True)
            self.ventana = VentanaTienda(self, ruta_catalogo=self.ruta_catalogo)
        self.ventana.present()

    def do_shutdown(self):
        if self.ventana is not None:
            self.ventana.liberar()
        Gtk.Application.do_shutdown(self)


AYUDA = """uso: infinity-tienda [--catalogo RUTA]

La Tienda de aplicaciones de Infinity OS (la ventana). Desde la terminal y sin ventana, el mismo comando sabe «listar»,
«instalar», «quitar» y «actualizar» (se explica con «infinity-tienda --help»).

  --catalogo RUTA   catálogo JSON que usar en vez del del sistema (para desarrollar)
  -h, --help        esta ayuda
"""


def leer_argumentos(argv):
    """Los argumentos de la línea de órdenes: (ruta del catálogo o None, el resto, que es para GTK). Lanza ValueError (en
    español) si `--catalogo` no lleva ruta, y _PideAyuda con -h/--help."""
    ruta, resto = None, []
    it = iter(argv)
    for a in it:
        if a in ("-h", "--help"):
            raise _PideAyuda()
        if a == "--catalogo":
            ruta = next(it, None)
            if not ruta:
                raise ValueError("--catalogo necesita la ruta de un catálogo")
        elif a.startswith("--catalogo="):
            ruta = a.partition("=")[2]
            if not ruta:
                raise ValueError("--catalogo necesita la ruta de un catálogo")
        else:
            resto.append(a)
    return ruta, resto


class _PideAyuda(Exception):
    pass


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    try:
        ruta, resto = leer_argumentos(argv[1:])
    except _PideAyuda:
        sys.stdout.write(AYUDA)
        return 0
    except ValueError as e:
        sys.stderr.write(f"infinity-tienda: {e}\n")
        return 2
    return AppTienda(ruta).run([argv[0]] + resto)


if __name__ == "__main__":
    sys.exit(main())
