"""WebApps de la Tienda: lanzadores .desktop que abren una web en una ventana propia de un navegador Chromium.

Sin GTK (solo biblioteca estándar; la rasterización a PNG usa GdkPixbuf si está, y si no se omite), así que se prueba sin
pantalla. Una WebApp «instalada» es:

  · ~/.local/share/applications/infinity-webapp-<id>.desktop  — el lanzador (lo ven el lanzador, el dock, Rofi, Wofi…)
  · ~/.local/share/icons/hicolor/scalable/apps/infinity-webapp-<id>.svg (+ un PNG de 256 px si se pudo rasterizar)
  · ~/.local/share/infinity/webapps/perfil — el perfil del navegador, COMPARTIDO por todas las WebApps y aparte del de
    tu navegador de siempre: así un solo inicio de sesión de Google vale para Gemini, NotebookLM, Classroom y YouTube,
    y todas corren en un solo proceso del navegador (menos RAM que un perfil por app).

El lanzador ejecuta el navegador en «modo aplicación» (`chrome --app=https://claude.ai/ …`): una ventana sin barra de
direcciones ni pestañas. Sirve cualquier navegador Chromium: Google Chrome, Brave, Chromium, Edge o Vivaldi, instalados
con apt o como Flatpak. El Chrome de la tienda trae Widevine, que necesitan Netflix, Disney+ y HBO Max.
"""
import os
import re
import shutil
import tempfile
from collections import namedtuple

from .tienda_catalogo import RE_ID, ruta_icono

PREFIJO_ID = "infinity-webapp-"
CLAVE_TIPO = "X-Infinity-Tienda"          # valor: webapp
CLAVE_ID = "X-Infinity-Tienda-Id"
CLAVE_DOMINIO = "X-Infinity-Dominio"      # el host de la web: el dock lo usa para reconocer la ventana (ver apps.py)
CLAVE_NAVEGADOR = "X-Infinity-Navegador"
BANDERAS_COMUNES = ("--no-first-run", "--no-default-browser-check", "--ozone-platform-hint=auto")
TAM_PNG = 256

Navegador = namedtuple("Navegador", "id nombre base perfil_propio prefijo_clase flatpak")
# base: lo que va delante de «--app=…» (["/usr/bin/google-chrome-stable"] o ["flatpak", "run", "com.google.Chrome"]).
# perfil_propio: se le puede dar --user-data-dir (un Flatpak no ve fuera de su sandbox, así que usa su perfil de siempre).
# prefijo_clase: el principio del app_id de Wayland de sus ventanas (chrome-…, brave-…).
InfoWebapp = namedtuple("InfoWebapp", "id ruta argv navegador funciona")

# Orden de preferencia: Chrome primero (trae Widevine, el DRM de las series), luego Brave y el resto.
FAMILIAS = (
    ("chrome", "Google Chrome", ("google-chrome-stable", "google-chrome"), "chrome", "com.google.Chrome"),
    ("brave", "Brave", ("brave-browser", "brave-browser-stable", "brave"), "brave", "com.brave.Browser"),
    ("chromium", "Chromium", ("chromium", "chromium-browser"), "chromium", "org.chromium.Chromium"),
    ("edge", "Microsoft Edge", ("microsoft-edge-stable", "microsoft-edge"), "msedge", "com.microsoft.Edge"),
    ("vivaldi", "Vivaldi", ("vivaldi-stable", "vivaldi"), "vivaldi", "com.vivaldi.Vivaldi"),
)
FAMILIAS_POR_ID = {f[0]: f for f in FAMILIAS}


class SinNavegador(Exception):
    """No hay ningún navegador Chromium instalado: hay que instalar uno antes (lo ofrece la tienda)."""


# --------------------------------------------------------------------------------------- rutas
def dir_datos():
    """$XDG_DATA_HOME (si es una ruta absoluta) o ~/.local/share."""
    base = os.environ.get("XDG_DATA_HOME", "")
    return base if os.path.isabs(base) else os.path.join(os.path.expanduser("~"), ".local", "share")


def _id_valido(ident):
    if not isinstance(ident, str) or not RE_ID.fullmatch(ident) or len(ident) > 40:
        raise ValueError(f"id de WebApp no válido: {ident!r}")
    return ident


def nombre_desktop(ident):
    return f"{PREFIJO_ID}{_id_valido(ident)}.desktop"


def nombre_icono(ident):
    return f"{PREFIJO_ID}{_id_valido(ident)}"


def ruta_desktop(ident, datos=None):
    return os.path.join(datos or dir_datos(), "applications", nombre_desktop(ident))


def dir_perfil(datos=None):
    return os.path.join(datos or dir_datos(), "infinity", "webapps", "perfil")


def rutas_iconos(ident, datos=None):
    """Los ficheros de icono que deja una WebApp (el SVG y el PNG), para instalarlos y para quitarlos."""
    base = os.path.join(datos or dir_datos(), "icons", "hicolor")
    nombre = nombre_icono(ident)
    return (os.path.join(base, "scalable", "apps", nombre + ".svg"),
            os.path.join(base, f"{TAM_PNG}x{TAM_PNG}", "apps", nombre + ".png"))


# --------------------------------------------------------------------------------------- navegadores
def flatpak_instalada(ref, datos=None):
    """¿Está instalado este Flatpak, para el usuario o en todo el sistema? (comprueba las carpetas, sin lanzar flatpak)"""
    for base in (os.path.join(datos or dir_datos(), "flatpak"), "/var/lib/flatpak"):
        if os.path.exists(os.path.join(base, "app", ref, "current")):
            return True
    return False


def encontrar_navegadores(buscar=None, flatpak=None, preferido=None):
    """Los navegadores Chromium instalados, el mejor primero.

    `buscar(nombre) -> ruta o None` (por defecto shutil.which) y `flatpak(ref) -> bool` se pueden sustituir en las
    pruebas. `preferido` es el id de una familia («brave»): si está instalada, va la primera. De cada familia solo
    cuenta una variante: la del sistema (apt) gana a la de Flatpak.
    """
    buscar = buscar or shutil.which
    flatpak = flatpak or flatpak_instalada
    hallados = []
    for ident, nombre, binarios, prefijo, ref in FAMILIAS:
        ruta = next((r for r in (buscar(b) for b in binarios) if r), None)
        if ruta:
            hallados.append(Navegador(ident, nombre, (os.path.abspath(ruta),), True, prefijo, False))
        elif flatpak(ref):
            hallados.append(Navegador(ident, nombre, ("flatpak", "run", ref), False, prefijo, True))
    if preferido:
        hallados.sort(key=lambda n: n.id != preferido)   # sort es estable: el resto conserva su orden
    return hallados


def argv_webapp(navegador, url, perfil):
    """La orden que abre `url` en modo aplicación. Un navegador de Flatpak usa su perfil de siempre."""
    argv = list(navegador.base) + [f"--app={url}"]
    if navegador.perfil_propio:
        argv.append(f"--user-data-dir={perfil}")
    return argv + list(BANDERAS_COMUNES)


def clase_ventana(navegador, url):
    """El app_id de Wayland que Chromium da a una ventana `--app=URL`: «<prefijo>-<host>_<ruta, con / → _>-Default»
    (la regla de Chromium para el nombre de una app: host + «_» + ruta, y el perfil «Default»). Es lo mejor que se sabe
    sin tener el navegador delante: el dock, además, reconoce la ventana por el dominio (X-Infinity-Dominio).
    Un navegador de Flatpak pone otro id, así que ahí no se declara."""
    if navegador.flatpak:
        return ""
    partes = re.match(r"^https://([^/?#]+)(/[^?#]*)?", url)
    if not partes:
        return ""
    host, ruta = partes.group(1), partes.group(2) or "/"
    return f"{navegador.prefijo_clase}-{host}_{ruta}".replace("/", "_") + "-Default"


# --------------------------------------------------------------------------------------- el .desktop
_SEGURO = re.compile(r"^[A-Za-z0-9_@+=:,./-]+$")


def _arg_exec(arg):
    """Un argumento de la clave Exec según la especificación de .desktop: «%» se escribe «%%» y lo que lleve caracteres
    reservados (espacio, ? & ; $ ( ) * # ~ …) va entre comillas dobles, escapando " $ y ` con una barra invertida."""
    if "\\" in arg or re.search(r"[\x00-\x1f\x7f]", arg):
        raise ValueError(f"argumento no permitido en un lanzador: {arg!r}")
    arg = arg.replace("%", "%%")
    if arg and _SEGURO.fullmatch(arg.replace("%%", "")):
        return arg
    return '"' + re.sub(r'(["`$])', r"\\\1", arg) + '"'


def linea_exec(argv):
    """El valor de Exec tal como se escribe en el fichero. Las barras invertidas del escapado de las comillas se
    duplican (la regla general de cadenas de .desktop se aplica ANTES que la de las comillas de Exec)."""
    return " ".join(_arg_exec(a) for a in argv).replace("\\", "\\\\")


def argv_desde_exec(valor):
    """Lo contrario de linea_exec (para leer un lanzador): los argumentos, con «%%» vuelto a «%». Lista vacía si las
    comillas no cierran."""
    texto = valor.replace("\\\\", "\\")
    args, actual, entre, hay = [], [], False, False
    i = 0
    while i < len(texto):
        c = texto[i]
        if entre:
            if c == "\\" and i + 1 < len(texto) and texto[i + 1] in '"`$\\':
                actual.append(texto[i + 1])
                i += 2
                continue
            if c == '"':
                entre = False
            else:
                actual.append(c)
        elif c == '"':
            entre = hay = True
        elif c in " \t":
            if actual or hay:
                args.append("".join(actual))
                actual, hay = [], False
        else:
            actual.append(c)
        i += 1
    if entre:
        return []
    if actual or hay:
        args.append("".join(actual))
    return [a.replace("%%", "%") for a in args]


def _texto_clave(valor):
    """Valor de una clave de texto de .desktop: sin saltos de línea y con la barra invertida escapada."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", valor).replace("\\", "\\\\")


def texto_desktop(entrada, navegador, perfil=None):
    """El contenido del .desktop de una WebApp (cadena)."""
    url = entrada.origen["url"]
    argv = argv_webapp(navegador, url, perfil or dir_perfil())
    categorias = ";".join(entrada.origen.get("categorias_desktop") or ("Network",)) + ";"
    claves = ";".join(entrada.origen.get("palabras_clave") or ()) + ";" if entrada.origen.get("palabras_clave") else ""
    clase = clase_ventana(navegador, url)
    lineas = [
        "[Desktop Entry]",
        "Version=1.0",
        "Type=Application",
        f"Name={_texto_clave(entrada.nombre)}",
        "GenericName=WebApp",
        f"Comment={_texto_clave(entrada.resumen)} (WebApp de la Tienda de Infinity)",
        f"Exec={linea_exec(argv)}",
        f"Icon={nombre_icono(entrada.id)}",
        "Terminal=false",
        f"Categories={categorias}",
    ]
    if claves:
        lineas.append(f"Keywords={claves}")
    if clase:
        lineas.append(f"StartupWMClass={clase}")
    lineas += [
        f"{CLAVE_TIPO}=webapp",
        f"{CLAVE_ID}={entrada.id}",
        f"{CLAVE_DOMINIO}={entrada.dominio}",
        f"{CLAVE_NAVEGADOR}={navegador.id}",
    ]
    return "\n".join(lineas) + "\n"


def escribir_atomico(ruta, texto, modo=0o644):
    """Escribe `texto` en `ruta` sin dejar nunca un fichero a medias (temporal en la misma carpeta + rename)."""
    carpeta = os.path.dirname(ruta)
    os.makedirs(carpeta, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tienda-", suffix=".tmp", dir=carpeta)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, modo)
        os.replace(tmp, ruta)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# --------------------------------------------------------------------------------------- iconos
def rasterizar_png(svg, png, tam=TAM_PNG):
    """SVG → PNG con GdkPixbuf (si hay GTK/gi y el cargador de SVG). Devuelve True si lo logró; nunca lanza."""
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf
        pix = GdkPixbuf.Pixbuf.new_from_file_at_size(svg, tam, tam)
        os.makedirs(os.path.dirname(png), exist_ok=True)
        pix.savev(png, "png", [], [])
        return True
    except Exception:   # noqa: BLE001 - sin gi, sin cargador de SVG, disco lleno…: el SVG solo basta
        return False


def instalar_iconos(entrada, svg_origen, datos=None, rasterizar=rasterizar_png):
    """Copia el SVG de la app al tema de iconos del usuario (para que Rofi, Wofi, el dock y el lanzador lo encuentren
    por su nombre) y, si se puede, deja también un PNG de 256 px. Devuelve el nombre del icono."""
    destino_svg, destino_png = rutas_iconos(entrada.id, datos)
    os.makedirs(os.path.dirname(destino_svg), exist_ok=True)
    with open(svg_origen, "rb") as f:
        contenido = f.read()
    tmp = destino_svg + ".tmp"
    with open(tmp, "wb") as f:
        f.write(contenido)
    os.replace(tmp, destino_svg)
    if rasterizar is not None:
        rasterizar(destino_svg, destino_png, TAM_PNG)
    return nombre_icono(entrada.id)


# --------------------------------------------------------------------------------------- instalar y quitar
def instalar_webapp(entrada, *, navegador=None, preferido=None, datos=None, svg=None, buscar=None, flatpak=None,
                    rasterizar=rasterizar_png):
    """Crea el lanzador y el icono de una WebApp. Devuelve la ruta del .desktop.

    Sin `navegador`, elige el mejor instalado (`preferido` manda); si no hay ninguno lanza SinNavegador. Todo se
    escribe de forma atómica; el perfil compartido del navegador se crea con permisos 0700.
    """
    if entrada.tipo != "webapp":
        raise ValueError(f"{entrada.id} no es una WebApp")
    if navegador is None:
        hallados = encontrar_navegadores(buscar, flatpak, preferido)
        if not hallados:
            raise SinNavegador("Las WebApps necesitan un navegador basado en Chromium (Google Chrome, Brave…).")
        navegador = hallados[0]
    perfil = dir_perfil(datos)
    if navegador.perfil_propio:
        os.makedirs(perfil, mode=0o700, exist_ok=True)
        os.chmod(perfil, 0o700)
    svg = svg or ruta_icono(entrada)
    if svg:
        instalar_iconos(entrada, svg, datos, rasterizar)
    ruta = ruta_desktop(entrada.id, datos)
    escribir_atomico(ruta, texto_desktop(entrada, navegador, perfil))
    return ruta


def _leer_claves(ruta):
    """Las claves del grupo [Desktop Entry] de un .desktop (solo lo que hace falta; sin traducciones)."""
    claves, dentro = {}, False
    try:
        with open(ruta, encoding="utf-8") as f:
            for linea in f:
                linea = linea.rstrip("\n")
                if linea.startswith("["):
                    dentro = linea == "[Desktop Entry]"
                elif dentro and "=" in linea and not linea.startswith("#"):
                    k, _, v = linea.partition("=")
                    claves.setdefault(k.strip(), v)
    except (OSError, UnicodeDecodeError):
        return None
    return claves


def _ejecutable_existe(argv, flatpak=None, buscar=None):
    if not argv:
        return False
    buscar = buscar or shutil.which
    flatpak = flatpak or flatpak_instalada
    if argv[0] == "flatpak":
        return bool(buscar("flatpak")) and len(argv) > 2 and flatpak(argv[2])
    if os.path.isabs(argv[0]):
        return os.path.isfile(argv[0]) and os.access(argv[0], os.X_OK)
    return bool(buscar(argv[0]))


def webapp_instalada(ident, datos=None, flatpak=None, buscar=None):
    """InfoWebapp de una WebApp instalada por la tienda, o None. `funciona` dice si su navegador sigue existiendo."""
    ruta = ruta_desktop(ident, datos)
    claves = _leer_claves(ruta)
    if not claves or claves.get(CLAVE_TIPO) != "webapp" or claves.get(CLAVE_ID) != ident:
        return None
    argv = argv_desde_exec(claves.get("Exec", ""))
    return InfoWebapp(ident, ruta, argv, claves.get(CLAVE_NAVEGADOR, ""), _ejecutable_existe(argv, flatpak, buscar))


def quitar_webapp(ident, datos=None):
    """Borra el lanzador y los iconos de una WebApp. El perfil del navegador (tus sesiones) no se toca.
    Devuelve True si había algo que quitar."""
    habia = False
    for ruta in (ruta_desktop(ident, datos),) + rutas_iconos(ident, datos):
        try:
            os.unlink(ruta)
            habia = True
        except FileNotFoundError:
            pass
    return habia


def reparar_webapps(entradas, datos=None, preferido=None, buscar=None, flatpak=None, svg_de=None,
                    rasterizar=rasterizar_png):
    """Rehace los lanzadores cuyo navegador ya no existe (p. ej. se desinstaló Brave y hay Chrome) o que se
    quedaron sin icono. Devuelve los ids reparados. Si no queda ningún navegador, no toca nada."""
    hallados = encontrar_navegadores(buscar, flatpak, preferido)
    reparadas = []
    if not hallados:
        return reparadas
    for e in entradas:
        if e.tipo != "webapp":
            continue
        info = webapp_instalada(e.id, datos, flatpak, buscar)
        if info is None:
            continue
        sin_icono = not os.path.exists(rutas_iconos(e.id, datos)[0])
        if info.funciona and not sin_icono:
            continue
        instalar_webapp(e, navegador=hallados[0], datos=datos, svg=(svg_de(e) if svg_de else None), rasterizar=rasterizar)
        reparadas.append(e.id)
    return reparadas
