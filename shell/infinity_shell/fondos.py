"""Fondos de pantalla de Infinity OS: el catálogo del sistema, los del usuario y cómo se añaden.

Este módulo NO usa GTK. Ajustes (ajustes_app.py) lo usa para la galería; las pruebas
(shell/tests/test_fondos.py) lo comprueban sin pantalla. Para decodificar imágenes necesita
GdkPixbuf, que se importa solo al importar una imagen (listar y resolver no lo necesitan).

Dos sitios de fondos:
  - Catálogo del sistema: /usr/share/infinity/fondos/fondos.json y una carpeta por fondo
    (<id>/escritorio.jpg, <id>/bloqueo-ANCHOxALTO.jpg, <id>/miniatura.jpg). Se genera en
    branding/fondos y build.sh lo instala.
  - Fondos del usuario: ~/.local/share/infinity/fondos/u-<nombre>/ con los mismos ficheros y un
    meta.json. Se crean al importar una imagen (por ejemplo guardada desde el navegador).

Un fondo se identifica por su id: solo [a-z0-9-]. Un id NUNCA se usa como ruta sin validarlo antes
(sin "..", sin "/"). Los ids de usuario empiezan siempre por "u-" y los del sistema nunca: así un
fondo del usuario no puede tapar uno del sistema, y cada id apunta a un único sitio.

La elección del escritorio y del bloqueo la leen los scripts del sistema (overlay/usr/libexec/infinity/
fondo-ruta); este módulo hace lo mismo en Python (ruta_escritorio, ruta_bloqueo) y una prueba comprueba
que ambos eligen igual. Todo termina siempre en algo que funciona: el fondo antiguo de /usr/share/infinity
o, si tampoco está, ninguna imagen (el escritorio queda en color sólido y el bloqueo, sin imagen).
"""
import json
import math
import os
import re
import secrets
import shutil
import stat
import struct
import time
import unicodedata
from collections import namedtuple

# Las nueve resoluciones que tiene el catálogo del sistema (las mismas de branding/make_assets.py).
RESOLUCIONES = ((1280, 720), (1280, 800), (1366, 768), (1440, 900), (1600, 900),
                (1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600))
ESCRITORIO_TAM = (2560, 1440)
MINIATURA_TAM = (320, 180)
CALIDAD_JPEG = 92

# Una imagen importada se oscurece como las del catálogo (branding/make_assets.py: escritorio al 70 %,
# bloqueo al 65 %), para que el cristal y la hora blanca sigan viéndose. 1.0 = sin oscurecer.
BRILLO_ESCRITORIO = 0.70
BRILLO_BLOQUEO = 0.65

# Límites al importar. El tope de megapíxeles evita agotar la memoria con una imagen enorme.
MAX_BYTES = 40 * 1024 * 1024
MAX_LADO = 12000
MAX_PIXELES = 50_000_000
MAX_FONDOS_USUARIO = 50
MAX_MONITORES_EXTRA = 6
MIN_LADO_MONITOR, MAX_LADO_MONITOR = 320, 8192
FORMATOS_ACEPTADOS = {"jpeg", "png", "webp", "gif", "bmp"}

ID_VALIDO = re.compile(r"[a-z0-9][a-z0-9-]{0,39}")
PREFIJO_USUARIO = "u-"
_BLOQUEO = re.compile(r"bloqueo-(\d{3,5})x(\d{3,5})\.jpg")
_META_MAX = 64 * 1024
_MANIFIESTO_MAX = 1024 * 1024

# Valores del ajuste escritorio.fondo que no son ids de fondo (ver ajustes.py).
VALOR_CASCADA = "imagen"
VALOR_COLOR = "color"
ID_CASCADA = "cascada"
ID_BLOQUEO_DEFECTO = "islandia"

Fondo = namedtuple("Fondo", "id nombre origen carpeta escritorio miniatura bloqueos")
# origen: "sistema" | "usuario" | "respaldo". escritorio y miniatura: ruta o None.
# bloqueos: {(ancho, alto): ruta}, solo los ficheros que existen.

Rutas = namedtuple("Rutas", "sistema usuario legado_escritorio legado_bloqueo")
# sistema: carpetas de catálogo a probar, en orden. usuario: carpeta de los fondos del usuario.
# legado_escritorio: rutas del fondo antiguo del escritorio. legado_bloqueo: carpetas con wallpaper-lock-*.jpg.

_RAIZ_REPO = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))


class ErrorFondo(Exception):
    """Un problema que se le explica al usuario tal cual (el mensaje va en español)."""


def dir_usuario():
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "infinity", "fondos")


def rutas_por_defecto():
    """Dónde buscar. En el árbol de desarrollo (sin /usr/share/infinity) se usa branding/ del repositorio."""
    branding = os.path.join(_RAIZ_REPO, "branding")
    return Rutas(
        sistema=("/usr/share/infinity/fondos", os.path.join(branding, "fondos")),
        usuario=dir_usuario(),
        legado_escritorio=("/usr/share/infinity/wallpaper.jpg", os.path.join(branding, "wallpaper.jpg")),
        legado_bloqueo=("/usr/share/infinity/lock/fondos", os.path.join(branding, "fondos-bloqueo")))


def descarga_dir():
    """Carpeta de descargas XDG (~/Descargas en español), o la carpeta personal si no hay."""
    try:
        from gi.repository import GLib
        ruta = GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
    except (ImportError, ValueError):
        ruta = None
    if ruta and os.path.isdir(ruta):
        return ruta
    return os.path.expanduser("~")


# ------------------------------------------------------------------ ids y nombres
def id_valido(valor):
    return isinstance(valor, str) and ID_VALIDO.fullmatch(valor) is not None and not valor.endswith("-")


def es_de_usuario(id_):
    return id_valido(id_) and id_.startswith(PREFIJO_USUARIO)


def limpiar_nombre(texto, maximo=40):
    """Nombre para mostrar: sin caracteres de control ni raros, sin espacios de más. Nunca vacío."""
    texto = unicodedata.normalize("NFC", texto if isinstance(texto, str) else "")
    texto = "".join(c if (c.isalnum() or c in " .'()+-_") else " " for c in texto)
    texto = re.sub(r"\s+", " ", texto).strip(" ._-")
    return texto[:maximo].strip() or "Imagen"


def _slug(texto, maximo=24):
    base = unicodedata.normalize("NFKD", texto if isinstance(texto, str) else "")
    base = base.encode("ascii", "ignore").decode("ascii").lower()
    base = re.sub(r"[^a-z0-9]+", "-", base).strip("-")[:maximo].strip("-")
    return base or "imagen"


def _crear_id(nombre, existentes):
    base = PREFIJO_USUARIO + _slug(nombre)
    candidato, n = base, 1
    while candidato in existentes:
        n += 1
        candidato = f"{base}-{n}"
    return candidato


# ------------------------------------------------------------------ lectura
def _archivo(ruta):
    """La ruta si es un fichero normal (no un enlace ni otra cosa); si no, None."""
    try:
        return ruta if stat.S_ISREG(os.lstat(ruta).st_mode) else None
    except OSError:
        return None


def _archivo_sistema(ruta):
    """Igual, pero un enlace simbólico a un fichero normal vale (carpetas del sistema, de confianza)."""
    try:
        return ruta if stat.S_ISREG(os.stat(ruta).st_mode) else None
    except OSError:
        return None


def _es_carpeta_real(ruta):
    try:
        return stat.S_ISDIR(os.lstat(ruta).st_mode)
    except OSError:
        return False


def _leer_json(ruta, maximo):
    """El JSON de un fichero normal de hasta `maximo` bytes, o None si no existe, no sirve o no es un fichero normal.

    Una tubería (FIFO) con ese nombre haría esperar a open() para siempre: Ajustes lista los fondos en su hilo
    principal y se congelaría. Y un JSON con miles de corchetes anidados lanza RecursionError, no ValueError.
    """
    try:
        # O_NONBLOCK: abrir una tubería no se queda esperando; después se comprueba que es un fichero normal.
        fd = os.open(ruta, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return None
        with os.fdopen(fd, "rb", closefd=False) as f:
            datos = f.read(maximo + 1)
    except OSError:
        return None
    finally:
        os.close(fd)
    if len(datos) > maximo:
        return None
    try:
        return json.loads(datos.decode("utf-8"))
    except (ValueError, RecursionError):
        return None


def _hija(base, rel, buscar):
    """Ruta de un fichero relativo del manifiesto, sin salirse de `base`. None si no es válida o no existe."""
    if not isinstance(rel, str) or not rel or "\0" in rel or os.path.isabs(rel):
        return None
    if ".." in rel.split("/"):
        return None
    base = os.path.normpath(base)
    ruta = os.path.normpath(os.path.join(base, rel))
    if not ruta.startswith(base.rstrip(os.sep) + os.sep):
        return None
    return buscar(ruta)


def _resolucion(texto):
    m = re.fullmatch(r"(\d{3,5})x(\d{3,5})", texto) if isinstance(texto, str) else None
    return (int(m.group(1)), int(m.group(2))) if m else None


def _menor(bloqueos):
    """La imagen de bloqueo más pequeña (sirve de miniatura si no hay otra), o None."""
    return bloqueos[min(bloqueos, key=lambda r: r[0] * r[1])] if bloqueos else None


def _fondo_del_manifiesto(base, e):
    if not isinstance(e, dict) or not id_valido(e.get("id")) or es_de_usuario(e["id"]):
        return None
    nombre = limpiar_nombre(e.get("nombre") if isinstance(e.get("nombre"), str) else e["id"])
    carpeta = os.path.join(base, e["id"])
    escritorio = _hija(base, e.get("escritorio"), _archivo_sistema)
    miniatura = _hija(base, e.get("miniatura"), _archivo_sistema)
    bloqueos = {}
    if isinstance(e.get("bloqueo"), dict):
        for res, rel in e["bloqueo"].items():
            r = _resolucion(res)
            ruta = _hija(base, rel, _archivo_sistema) if r else None
            if ruta:
                bloqueos[r] = ruta
    if not escritorio and not bloqueos:
        return None
    return Fondo(e["id"], nombre, "sistema", carpeta, escritorio, miniatura or escritorio or _menor(bloqueos),
                 bloqueos)


def _catalogo_de(base):
    datos = _leer_json(os.path.join(base, "fondos.json"), _MANIFIESTO_MAX)
    if not isinstance(datos, dict) or not isinstance(datos.get("fondos"), list):
        return []
    vistos, lista = set(), []
    for e in datos["fondos"][:200]:
        f = _fondo_del_manifiesto(base, e)
        if f and f.id not in vistos:
            vistos.add(f.id)
            lista.append(f)
    return lista


def respaldo(rutas=None):
    """Lo mínimo si no hay catálogo: el fondo antiguo del escritorio (cascada) y el del bloqueo (islandia)."""
    rutas = rutas or rutas_por_defecto()
    lista = []
    escritorio = next((r for r in rutas.legado_escritorio if _archivo_sistema(r)), None)
    if escritorio:
        lista.append(Fondo(ID_CASCADA, "Cascada", "respaldo", os.path.dirname(escritorio), escritorio,
                           escritorio, {}))
    for carpeta in rutas.legado_bloqueo:
        bloqueos = {}
        for nombre in sorted(_nombres(carpeta)):
            m = re.fullmatch(r"wallpaper-lock-(\d{3,5})x(\d{3,5})\.jpg", nombre)
            if m and _archivo_sistema(os.path.join(carpeta, nombre)):
                bloqueos[(int(m.group(1)), int(m.group(2)))] = os.path.join(carpeta, nombre)
        if bloqueos:
            lista.append(Fondo(ID_BLOQUEO_DEFECTO, "Islandia", "respaldo", carpeta, None, _menor(bloqueos),
                               bloqueos))
            break
    return lista


def _nombres(carpeta):
    try:
        return os.listdir(carpeta)
    except OSError:
        return []


def catalogo_sistema(rutas=None):
    """Los fondos del catálogo del sistema. Sin catálogo (o sin ficheros), el respaldo antiguo."""
    rutas = rutas or rutas_por_defecto()
    for base in rutas.sistema:
        lista = _catalogo_de(base)
        if lista:
            return lista
    return respaldo(rutas)


def fondos_usuario(rutas=None):
    """Los fondos que importó el usuario, del más antiguo al más nuevo. Ignora lo que no encaje."""
    rutas = rutas or rutas_por_defecto()
    base = rutas.usuario
    if not _es_carpeta_real(base):
        return []
    lista = []
    for nombre in sorted(_nombres(base))[:500]:
        f = _leer_usuario(base, nombre)
        if f:
            lista.append(f)
    lista.sort(key=lambda f: (_creado(f.carpeta), f.id))
    return lista


def _creado(carpeta):
    meta = _leer_json(os.path.join(carpeta, "meta.json"), _META_MAX)
    c = meta.get("creado") if isinstance(meta, dict) else None
    return c if isinstance(c, (int, float)) and not isinstance(c, bool) else 0


def _leer_usuario(base, nombre):
    if not es_de_usuario(nombre):
        return None
    carpeta = os.path.join(base, nombre)
    if not _es_carpeta_real(carpeta):
        return None
    meta = _leer_json(os.path.join(carpeta, "meta.json"), _META_MAX)
    if not isinstance(meta, dict):
        return None
    escritorio = _archivo(os.path.join(carpeta, "escritorio.jpg"))
    miniatura = _archivo(os.path.join(carpeta, "miniatura.jpg"))
    bloqueos = {}
    for n in _nombres(carpeta):
        m = _BLOQUEO.fullmatch(n)
        if m and _archivo(os.path.join(carpeta, n)):
            bloqueos[(int(m.group(1)), int(m.group(2)))] = os.path.join(carpeta, n)
    if not escritorio and not bloqueos:
        return None
    return Fondo(nombre, limpiar_nombre(meta.get("nombre")), "usuario", carpeta, escritorio,
                 miniatura or escritorio or _menor(bloqueos), bloqueos)


def listar(rutas=None):
    """Todos los fondos para elegir: primero los del sistema y después los del usuario."""
    rutas = rutas or rutas_por_defecto()
    return catalogo_sistema(rutas) + fondos_usuario(rutas)


def buscar(id_, rutas=None):
    """El fondo con ese id, o None. Un id mal formado no se busca en ninguna parte."""
    if not id_valido(id_):
        return None
    rutas = rutas or rutas_por_defecto()
    if es_de_usuario(id_):
        return _leer_usuario(rutas.usuario, id_) if _es_carpeta_real(rutas.usuario) else None
    return next((f for f in catalogo_sistema(rutas) if f.id == id_), None)


# ------------------------------------------------------- qué fondo toca (igual que fondo-ruta)
def mas_cercana(bloqueos, ancho, alto):
    """De {(ancho, alto): ruta}, la ruta de la resolución más parecida: primero la proporción, luego el área.

    Con empate de área gana la mayor. fondo-ruta (shell) hace la misma cuenta con awk.
    """
    if not bloqueos:
        return None
    if (ancho, alto) in bloqueos:
        return bloqueos[(ancho, alto)]
    objetivo = ancho / alto

    def clave(r):
        w, h = r
        return (float(f"{abs(math.log((w / h) / objetivo)):.3f}"), abs(w * h - ancho * alto), -w * h)
    return bloqueos[min(bloqueos, key=clave)]


def ruta_escritorio(valor, rutas=None):
    """Imagen del escritorio para el ajuste escritorio.fondo. None = no hay imagen (color sólido).

    "color" → None. "imagen", un id que ya no existe o un valor raro → la cascada; si falta también, el
    fondo antiguo de /usr/share/infinity; si falta, None.
    """
    rutas = rutas or rutas_por_defecto()
    if isinstance(valor, str) and valor.strip().lower() == VALOR_COLOR:
        return None
    candidatos = []
    if id_valido(valor) and valor not in (VALOR_CASCADA, VALOR_COLOR):
        candidatos.append(valor)
    candidatos.append(ID_CASCADA)
    for id_ in candidatos:
        f = buscar(id_, rutas)
        if f and f.escritorio:
            return f.escritorio
    return next((r for r in rutas.legado_escritorio if _archivo_sistema(r)), None)


def ruta_bloqueo(id_, ancho, alto, rutas=None):
    """Imagen del bloqueo para este fondo y esta resolución. Cadena de respaldo:

    el fondo elegido (esa resolución o la más cercana de ese fondo) → "islandia" → el fondo antiguo del
    bloqueo (esa resolución o 1920x1080) → None (gtklock arranca sin imagen, pero bloquea).
    """
    rutas = rutas or rutas_por_defecto()
    if not (isinstance(ancho, int) and isinstance(alto, int) and ancho > 0 and alto > 0):
        ancho, alto = 1920, 1080
    candidatos = [i for i in (id_, ID_BLOQUEO_DEFECTO) if id_valido(i)]
    for i in candidatos:
        f = buscar(i, rutas)
        ruta = mas_cercana(f.bloqueos, ancho, alto) if f else None
        if ruta:
            return ruta
    for carpeta in rutas.legado_bloqueo:
        for w, h in ((ancho, alto), (1920, 1080)):
            ruta = _archivo_sistema(os.path.join(carpeta, f"wallpaper-lock-{w}x{h}.jpg"))
            if ruta:
                return ruta
    return None


# ------------------------------------------------------------------ borrar
def quitar(id_, rutas=None):
    """Borra un fondo del usuario. Solo dentro de la carpeta de fondos del usuario y sin seguir enlaces.

    Devuelve True si lo borró. Lanza ErrorFondo si el id no es de un fondo del usuario.
    """
    rutas = rutas or rutas_por_defecto()
    if not es_de_usuario(id_):
        raise ErrorFondo("Solo se pueden quitar los fondos que has añadido tú.")
    base = rutas.usuario
    if not _es_carpeta_real(base):
        return False
    carpeta = os.path.join(base, id_)
    if not _es_carpeta_real(carpeta):  # no existe, o es un enlace simbólico (no se sigue)
        return False
    try:
        shutil.rmtree(carpeta)
    except OSError as e:
        raise ErrorFondo(f"No se pudo quitar el fondo: {e.strerror or e}") from e
    return True


# ------------------------------------------------------------------ importar
def _gdkpixbuf():
    try:
        import gi
        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib
    except (ImportError, ValueError) as e:
        raise ErrorFondo("Este equipo no tiene GdkPixbuf: no se pueden leer imágenes.") from e
    return GdkPixbuf, GLib


def _leer_origen(origen):
    """Los bytes del fichero, tras comprobar que es un fichero normal y que no pesa demasiado."""
    if not isinstance(origen, (str, os.PathLike)) or not os.fspath(origen):
        raise ErrorFondo("No se ha elegido ninguna imagen.")
    ruta = os.fspath(origen)
    try:
        # O_NONBLOCK: abrir una tubería (FIFO) no se queda esperando.
        fd = os.open(ruta, os.O_RDONLY | os.O_NONBLOCK | getattr(os, "O_CLOEXEC", 0))
    except OSError as e:
        raise ErrorFondo(f"No se puede abrir el archivo: {e.strerror or e}.") from e
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ErrorFondo("Eso no es un archivo de imagen (es una carpeta u otro tipo de elemento).")
        if st.st_size == 0:
            raise ErrorFondo("El archivo está vacío.")
        if st.st_size > MAX_BYTES:
            raise ErrorFondo(f"El archivo es demasiado grande ({st.st_size / 1048576:.0f} MB). "
                             f"El máximo son {MAX_BYTES // 1048576} MB.")
        with os.fdopen(fd, "rb", closefd=False) as f:
            datos = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise ErrorFondo(f"No se pudo leer el archivo: {e.strerror or e}.") from e
    finally:
        os.close(fd)
    if len(datos) > MAX_BYTES:
        raise ErrorFondo(f"El archivo es demasiado grande. El máximo son {MAX_BYTES // 1048576} MB.")
    return datos


def _error_de_lectura(datos, error):
    """El mensaje adecuado cuando GdkPixbuf no pudo leer los datos."""
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return ErrorFondo("Este equipo todavía no puede leer imágenes WebP. Guarda la imagen como JPG o PNG "
                          "(o instala el paquete webp-pixbuf-loader).")
    if _cabecera_conocida(datos):
        return ErrorFondo("La imagen está dañada o es ilegible.")
    return ErrorFondo("El archivo no es una imagen que se pueda leer: usa JPG, PNG o WebP (se mira el "
                      "contenido, no la extensión).")


def _cabecera_conocida(datos):
    return (datos[:3] == b"\xff\xd8\xff" or datos[:8] == b"\x89PNG\r\n\x1a\n" or datos[:3] == b"GIF"
            or datos[:2] == b"BM")


def _cabecera(datos):
    """(formato, ancho, alto) leído de la cabecera del propio archivo, sin decodificar nada.

    Así una imagen enorme (o una «bomba de descompresión») se rechaza antes de que ningún decodificador
    reserve memoria. Solo se admiten los formatos que se saben leer aquí: JPEG, PNG, GIF, BMP y WebP.
    Devuelve None si el archivo no empieza como ninguno de ellos y lanza ErrorFondo si la cabecera está rota.
    """
    corto = ErrorFondo("La imagen está incompleta o dañada (la cabecera no se puede leer).")
    try:
        if datos[:8] == b"\x89PNG\r\n\x1a\n":
            if datos[12:16] != b"IHDR":
                raise corto
            ancho, alto = struct.unpack(">II", datos[16:24])
            return "png", ancho, alto
        if datos[:3] == b"GIF":
            ancho, alto = struct.unpack("<HH", datos[6:10])
            return "gif", ancho, alto
        if datos[:2] == b"BM":
            tam_cabecera = struct.unpack("<I", datos[14:18])[0]
            if tam_cabecera == 12:   # formato antiguo de OS/2: medidas de 16 bits
                ancho, alto = struct.unpack("<HH", datos[18:22])
            else:
                ancho, alto = struct.unpack("<ii", datos[18:26])
            return "bmp", abs(ancho), abs(alto)
        if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
            trozo = datos[12:16]
            if trozo == b"VP8 ":
                if datos[23:26] != b"\x9d\x01\x2a":
                    raise corto
                ancho, alto = struct.unpack("<HH", datos[26:30])
                return "webp", ancho & 0x3FFF, alto & 0x3FFF
            if trozo == b"VP8L":
                if datos[20:21] != b"\x2f":
                    raise corto
                bits = struct.unpack("<I", datos[21:25])[0]
                return "webp", (bits & 0x3FFF) + 1, ((bits >> 14) & 0x3FFF) + 1
            if trozo == b"VP8X":
                ancho = int.from_bytes(datos[24:27], "little") + 1
                alto = int.from_bytes(datos[27:30], "little") + 1
                return "webp", ancho, alto
            raise corto
        if datos[:3] == b"\xff\xd8\xff":
            i = 2
            while i + 4 <= len(datos):
                if datos[i] != 0xFF:
                    i += 1
                    continue
                marcador = datos[i + 1]
                if marcador == 0xFF or marcador == 0x01 or 0xD0 <= marcador <= 0xD8:
                    i += 1 if marcador == 0xFF else 2
                    continue
                if marcador in (0xD9, 0xDA):   # fin o datos de la imagen, y no ha aparecido el SOF
                    break
                largo = struct.unpack(">H", datos[i + 2:i + 4])[0]
                if 0xC0 <= marcador <= 0xCF and marcador not in (0xC4, 0xC8, 0xCC):
                    alto, ancho = struct.unpack(">HH", datos[i + 5:i + 9])
                    return "jpeg", ancho, alto
                i += 2 + largo
            raise corto
    except struct.error as e:
        raise corto from e
    return None


_MARCADOR_JPEG = re.compile(rb"\xff[^\x00\xd0-\xd7\xff]")   # FF 00 (relleno), FF D0-D7 (reinicio) y FF FF no son marcadores


def _jpeg_termina(datos):
    """Recorre los segmentos de un JPEG: True si llega a su marcador final (FF D9), False si el archivo se acaba antes
    y None si la estructura no se entiende (una longitud imposible).

    Mirar solo los últimos bytes fallaba en los dos sentidos: rechazaba JPEG completos con datos detrás (las fotos
    «en movimiento» de los móviles llevan un vídeo pegado tras el FF D9; algunos archivos, relleno de ceros) y daba
    por buena una foto cortada si el FF D9 de la miniatura EXIF quedaba a la vista. Los segmentos se saltan
    por su longitud y entre ellos solo se busca el siguiente marcador (el FF D9 de una miniatura no cuenta).
    """
    n, i = len(datos), 2
    while True:
        m = _MARCADOR_JPEG.search(datos, i)
        if m is None:
            return False
        marca, pos = datos[m.start() + 1], m.start() + 2
        if marca == 0xD9:
            return True
        if marca in (0xD8, 0x01):    # sin longitud
            i = pos
            continue
        if pos + 2 > n:
            return False
        largo = (datos[pos] << 8) | datos[pos + 1]
        if largo < 2 or pos + largo > n:
            return None
        i = pos + largo


def _esta_completa(datos):
    """False si el archivo parece cortado (una descarga interrumpida, por ejemplo).

    GdkPixbuf no se queja de un PNG, BMP o JPEG cortado: devuelve media imagen. Aquí se comprueba el final
    de cada formato común. Un formato que no se reconoce aquí se deja pasar (el cargador dirá si no sirve).
    """
    if datos[:8] == b"\x89PNG\r\n\x1a\n":
        i = 8
        while i + 12 <= len(datos):
            largo = struct.unpack(">I", datos[i:i + 4])[0]
            if datos[i + 4:i + 8] == b"IEND":
                return i + 12 + 0 <= len(datos)
            i += 12 + largo
        return False
    if datos[:3] == b"\xff\xd8\xff":
        completo = _jpeg_termina(datos)
        # Si la estructura no se entiende (una longitud rota), vale la regla sencilla: un FF D9 al final.
        return completo if completo is not None else b"\xff\xd9" in datos[-2048:]
    if datos[:3] == b"GIF":
        return datos.rstrip(b"\x00")[-1:] == b";"
    if datos[:2] == b"BM" and len(datos) >= 6:
        tam = struct.unpack("<I", datos[2:6])[0]
        return tam == 0 or len(datos) >= tam
    if datos[:4] == b"RIFF" and datos[8:12] == b"WEBP":
        return len(datos) >= struct.unpack("<I", datos[4:8])[0] + 8
    return True


def _decodificar(datos, mayor_objetivo):
    """Pixbuf RGB (sin transparencia, derecho) a partir de los bytes de una imagen.

    Se rechaza por el tamaño en cuanto se lee la cabecera, antes de decodificar los píxeles: así una imagen
    enorme o una «bomba de descompresión» no gasta memoria. Si la imagen es mucho mayor de lo necesario,
    se reduce al decodificar.
    """
    cabecera = _cabecera(datos)
    if cabecera is None:
        raise _error_de_lectura(datos, None)
    _, ancho, alto = cabecera
    if not (0 < ancho <= MAX_LADO and 0 < alto <= MAX_LADO and ancho * alto <= MAX_PIXELES):
        raise ErrorFondo(f"La imagen es demasiado grande ({ancho}×{alto} px). El máximo son {MAX_LADO} px por "
                         f"lado y {MAX_PIXELES // 1_000_000} megapíxeles.")
    if not _esta_completa(datos):
        raise ErrorFondo("La imagen está incompleta o dañada (¿se cortó la descarga?). Vuelve a guardarla.")
    GdkPixbuf, GLib = _gdkpixbuf()
    cargador = GdkPixbuf.PixbufLoader()
    estado = {}

    def al_conocer_tamano(c, ancho, alto):
        estado["tam"] = (ancho, alto)
        if not (0 < ancho <= MAX_LADO and 0 < alto <= MAX_LADO and ancho * alto <= MAX_PIXELES):
            estado["rechazo"] = True
            return
        escala = mayor_objetivo / min(ancho, alto)  # cubrir el mayor objetivo, tenga la orientación que tenga
        if escala < 1:
            c.set_size(max(1, round(ancho * escala)), max(1, round(alto * escala)))

    cargador.connect("size-prepared", al_conocer_tamano)
    error = None
    try:
        for i in range(0, len(datos), 65536):
            cargador.write(datos[i:i + 65536])
            if estado.get("rechazo"):
                break
    except GLib.Error as e:
        error = e
    try:
        cargador.close()
    except GLib.Error as e:
        error = error or e
    if estado.get("rechazo"):
        ancho, alto = estado["tam"]
        raise ErrorFondo(f"La imagen es demasiado grande ({ancho}×{alto} px). El máximo son {MAX_LADO} px por "
                         f"lado y {MAX_PIXELES // 1_000_000} megapíxeles.")
    pix = cargador.get_pixbuf() if error is None else None
    if pix is None:
        raise _error_de_lectura(datos, error)
    formato = cargador.get_format()
    if formato is None or formato.get_name() not in FORMATOS_ACEPTADOS:
        raise ErrorFondo("Ese tipo de imagen no sirve como fondo. Usa JPG, PNG o WebP.")
    giro = pix.apply_embedded_orientation()  # fotos del móvil: respeta el giro de la cámara
    pix = giro or pix
    if pix.get_width() < 64 or pix.get_height() < 64:
        raise ErrorFondo("La imagen es demasiado pequeña para un fondo (mínimo 64×64 px).")
    if pix.get_has_alpha():  # la transparencia se pone sobre negro
        plano = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, pix.get_width(), pix.get_height())
        plano.fill(0x000000FF)
        pix.composite(plano, 0, 0, pix.get_width(), pix.get_height(), 0, 0, 1, 1,
                      GdkPixbuf.InterpType.NEAREST, 255)
        pix = plano
    return pix


def _cubrir(pix, ancho, alto):
    """Cubre ancho×alto sin deformar: se queda con la parte central que tiene esa proporción y la escala.

    Se recorta ANTES de escalar: escalar toda la imagen y recortar después reservaba memoria de más. Con una
    imagen muy estrecha (64×8000, que es legal) cubrir 2560×1600 pedía un intermedio de 2560×320000 px (2,4 GB).
    Así lo máximo que se reserva es el tamaño de destino.
    """
    GdkPixbuf, _ = _gdkpixbuf()
    w, h = pix.get_width(), pix.get_height()
    if (w, h) == (ancho, alto):
        return pix
    if w * alto >= h * ancho:    # más ancha que el destino: sobran los lados
        cw, ch = min(w, max(1, round(h * ancho / alto))), h
    else:                        # más alta: sobran arriba y abajo
        cw, ch = w, min(h, max(1, round(w * alto / ancho)))
    parte = pix.new_subpixbuf((w - cw) // 2, (h - ch) // 2, cw, ch)
    cubierta = parte.scale_simple(ancho, alto, GdkPixbuf.InterpType.BILINEAR)
    if cubierta is None:
        raise ErrorFondo("No hay memoria suficiente para preparar esta imagen.")
    return cubierta


def _recorte_escritorio(pix):
    """2560×1440 como mucho: cubre ese tamaño con recorte; si la imagen es menor, recorta a 16:9 sin ampliar."""
    w, h = pix.get_width(), pix.get_height()
    ancho, alto = ESCRITORIO_TAM
    if max(ancho / w, alto / h) <= 1:
        return _cubrir(pix, ancho, alto)
    if w * 9 >= h * 16:
        cw, ch = min(w, round(h * 16 / 9)), h
    else:
        cw, ch = w, min(h, round(w * 9 / 16))
    return pix.new_subpixbuf((w - cw) // 2, (h - ch) // 2, cw, ch).copy()


def _oscurecer(pix, factor):
    if factor >= 0.999:
        return pix
    GdkPixbuf, GLib = _gdkpixbuf()
    tabla = bytes(round(i * factor) for i in range(256))
    datos = pix.get_pixels().translate(tabla)
    return GdkPixbuf.Pixbuf.new_from_bytes(GLib.Bytes.new(datos), GdkPixbuf.Colorspace.RGB, False, 8,
                                           pix.get_width(), pix.get_height(), pix.get_rowstride())


def _jpeg(pix):
    ok, datos = pix.save_to_bufferv("jpeg", ["quality"], [str(CALIDAD_JPEG)])
    if not ok:
        raise ErrorFondo("No se pudo codificar la imagen.")
    return bytes(datos)


def resoluciones_a_generar(monitores=()):
    """Las nueve de siempre y la de cada monitor conectado (si no está entre ellas). Entradas raras se ignoran."""
    lista = list(RESOLUCIONES)
    extra = []
    for m in monitores or ():
        try:
            w, h = int(m[0]), int(m[1])
        except (TypeError, ValueError, IndexError, OverflowError):   # int(float('inf')) lanza OverflowError
            continue
        ok = MIN_LADO_MONITOR <= w <= MAX_LADO_MONITOR and MIN_LADO_MONITOR <= h <= MAX_LADO_MONITOR
        if ok and (w, h) not in lista and (w, h) not in extra and len(extra) < MAX_MONITORES_EXTRA:
            extra.append((w, h))
    return lista + extra


def _limpiar_restos(base):
    """Borra carpetas temporales de importaciones que se cortaron (más de un día)."""
    limite = time.time() - 86400
    for n in _nombres(base):
        ruta = os.path.join(base, n)
        if n.startswith(".nuevo-") and _es_carpeta_real(ruta):
            try:
                if os.lstat(ruta).st_mtime < limite:
                    shutil.rmtree(ruta, ignore_errors=True)
            except OSError:
                pass


def _preparar_base(base):
    if os.path.lexists(base) and not _es_carpeta_real(base):
        raise ErrorFondo("La carpeta de fondos del usuario no es una carpeta normal (¿un enlace?): no se usa.")
    try:
        os.makedirs(base, exist_ok=True)
    except OSError as e:
        raise ErrorFondo(f"No se pudo crear la carpeta de fondos: {e.strerror or e}.") from e


def importar(origen, rutas=None, monitores=(), nombre=None, progreso=None):
    """Convierte una imagen en un fondo del usuario y devuelve el Fondo creado.

    Genera, en ~/.local/share/infinity/fondos/<id>/, lo mismo que el catálogo: escritorio.jpg, miniatura.jpg
    y bloqueo-ANCHOxALTO.jpg. Es lento (decodifica y reescala): la interfaz lo llama desde un hilo.
    `progreso(hecho, total)` se llama tras cada fichero. Lanza ErrorFondo con un mensaje claro si algo falla.
    Todo se escribe en una carpeta temporal y se renombra al final: nunca queda un fondo a medias.
    """
    rutas = rutas or rutas_por_defecto()
    base = rutas.usuario
    datos = _leer_origen(origen)
    if len(fondos_usuario(rutas)) >= MAX_FONDOS_USUARIO:
        raise ErrorFondo(f"Ya tienes {MAX_FONDOS_USUARIO} fondos añadidos. Quita alguno antes de añadir más.")
    resoluciones = resoluciones_a_generar(monitores)
    mayor = max(max(w, h) for w, h in resoluciones + [ESCRITORIO_TAM])
    pix = _decodificar(datos, mayor)
    del datos

    bruto = nombre if nombre is not None else os.path.splitext(os.path.basename(os.fspath(origen)))[0]
    nombre = limpiar_nombre(bruto)
    _preparar_base(base)
    _limpiar_restos(base)
    total = len(resoluciones) + 2
    hecho = 0

    def avanza():
        nonlocal hecho
        hecho += 1
        if progreso:
            progreso(hecho, total)

    temporal = None
    try:
        temporal = _crear_temporal(base)
        escritorio = _oscurecer(_recorte_escritorio(pix), BRILLO_ESCRITORIO)
        _escribir_bytes(os.path.join(temporal, "escritorio.jpg"), _jpeg(escritorio))
        avanza()
        _escribir_bytes(os.path.join(temporal, "miniatura.jpg"), _jpeg(_cubrir(escritorio, *MINIATURA_TAM)))
        avanza()
        del escritorio
        for w, h in resoluciones:
            _escribir_bytes(os.path.join(temporal, f"bloqueo-{w}x{h}.jpg"),
                            _jpeg(_oscurecer(_cubrir(pix, w, h), BRILLO_BLOQUEO)))
            avanza()
        origen_limpio = limpiar_nombre(os.path.splitext(os.path.basename(os.fspath(origen)))[0], 60)
        meta = {"version": 1, "nombre": nombre, "origen": origen_limpio, "creado": int(time.time()),
                "bloqueo": [f"{w}x{h}" for w, h in resoluciones]}
        _escribir_bytes(os.path.join(temporal, "meta.json"),
                        (json.dumps(meta, ensure_ascii=False, indent=1) + "\n").encode("utf-8"))
        id_ = _colocar(temporal, base, nombre)
    except ErrorFondo:
        _borrar_temporal(temporal)
        raise
    except OSError as e:
        _borrar_temporal(temporal)
        raise ErrorFondo(f"No se pudo guardar el fondo: {e.strerror or e.__class__.__name__}.") from e
    except Exception:
        _borrar_temporal(temporal)
        raise
    f = _leer_usuario(base, id_)
    if f is None:
        raise ErrorFondo("El fondo se guardó pero no se puede leer. Inténtalo otra vez.")
    return f


def _crear_temporal(base):
    """Una carpeta nueva `.nuevo-…` dentro de `base`, con los permisos de siempre (0755 menos la umask).

    mkdir aplica la umask él mismo: no hace falta leerla. Leerla con os.umask() la cambia un instante para TODO
    el proceso, y importar() corre en un hilo mientras la ventana (otro hilo) puede estar creando ficheros.
    """
    for _ in range(100):
        ruta = os.path.join(base, ".nuevo-" + secrets.token_hex(6))
        try:
            os.mkdir(ruta, 0o755)
            return ruta
        except FileExistsError:
            continue
    raise ErrorFondo("No se pudo crear una carpeta temporal para el fondo.")


def _borrar_temporal(ruta):
    if ruta:
        shutil.rmtree(ruta, ignore_errors=True)


def _escribir_bytes(ruta, datos):
    # O_EXCL | O_NOFOLLOW: la carpeta es nueva y propia; si hubiera algo dentro, no se pisa ni se sigue.
    fd = os.open(ruta, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o644)
    with os.fdopen(fd, "wb") as f:
        f.write(datos)


def _colocar(temporal, base, nombre):
    """Renombra la carpeta temporal a su id definitivo (<u-nombre>, con -2, -3… si ya existe)."""
    for _ in range(100):
        existentes = {n for n in _nombres(base)}
        id_ = _crear_id(nombre, existentes)
        destino = os.path.join(base, id_)
        try:
            # rename no pisa una carpeta con contenido; si alguien la creó entre medias, se elige otro id.
            if os.path.lexists(destino):
                continue
            os.rename(temporal, destino)
            return id_
        except OSError:
            continue
    raise ErrorFondo("No se encontró un nombre libre para el fondo.")
