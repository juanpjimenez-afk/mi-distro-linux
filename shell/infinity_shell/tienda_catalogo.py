"""Catálogo de la Tienda de Infinity: qué aplicaciones ofrece y cómo se instala cada una.

Sin GTK y sin red: solo lee un JSON y lo valida, así se prueba sin pantalla. El catálogo del sistema vive en
/usr/share/infinity/tienda/catalogo.json (lo instala la ISO, de root). Es la ÚNICA fuente de la que el ayudante de root
(tienda-ayudante, el que lanza pkexec) toma repositorios, claves y nombres de paquete: nada de lo que pueda escribir
un usuario llega a un comando de root. La ventana usa el mismo fichero para dibujar las tarjetas.

Formato (versión 1):

    {"version": 1,
     "categorias": [{"id": "ia", "nombre": "Inteligencia artificial"}, ...],
     "apps": [ {entrada}, ... ]}

Cada entrada lleva: id, nombre, resumen, descripcion, categoria, tipo y origen; y opcionalmente color (#rrggbb),
icono (nombre del SVG de tienda/iconos, por defecto el id), web, aviso, tamano_mb, desktop_ids y navegador.
`tipo` decide qué lleva `origen`:

    flatpak  {"remoto": "flathub", "url_remoto": "https://…/flathub.flatpakrepo", "ref": "com.spotify.Client"}
             Se instala SIN permisos de administrador (flatpak --user).
    apt      {"paquete": "antigravity", "uri": "https://…/", "suite": "…", "componentes": ["main"],
              "arquitecturas": ["amd64"], "clave_url": "https://…", "archivo_clave": "/etc/apt/keyrings/x.gpg",
              "archivo_fuente": "x.list" (o x.sources), "huella": ["…"]}
             Registra el repositorio y su clave GPG, y instala con apt, todo por el ayudante de root.
    webapp   {"url": "https://…", "drm": false, "categorias_desktop": ["Network"], "palabras_clave": ["…"]}
             Crea un lanzador .desktop que abre la web en una ventana propia de un navegador Chromium.

Una entrada mal formada se descarta (y se anota en Catalogo.problemas): un catálogo roto a medias no debe dejar la
tienda sin abrir. El validador del ayudante de root es aparte y más estricto (está dentro de su propio fichero);
una prueba comprueba que los dos están de acuerdo.
"""
import json
import os
import re
from dataclasses import dataclass, field
from urllib.parse import urlsplit

from .search import normalizar

RUTA_SISTEMA = "/usr/share/infinity/tienda/catalogo.json"
DIR_ICONOS_SISTEMA = "/usr/share/infinity/tienda/iconos"
VERSION_FORMATO = 1
TIPOS = ("flatpak", "apt", "webapp")
ETIQUETAS_TIPO = {"flatpak": "Flatpak", "apt": "APT", "webapp": "WebApp"}

MAX_BYTES = 512 * 1024          # un catálogo más grande que esto no es un catálogo
MAX_APPS = 200
RE_ID = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
RE_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
RE_PAQUETE = re.compile(r"^[a-z0-9][a-z0-9+.-]{1,99}$")
RE_SUITE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
RE_COMPONENTE = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RE_ARCH = re.compile(r"^(?:amd64|arm64|i386|armhf)$")
RE_FUENTE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}\.(?:list|sources)$")
RE_CLAVE = re.compile(r"^/(?:etc/apt/keyrings|usr/share/keyrings)/[a-z0-9][a-z0-9._-]{0,63}\.gpg$")
RE_HUELLA = re.compile(r"^[0-9A-F]{40}$")
RE_REMOTO = re.compile(r"^[a-z0-9][a-z0-9-]{0,30}$")
RE_REF_FLATPAK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_-]*){2,}$")
RE_DESKTOP_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,100}\.desktop$")
RE_ETIQUETA = re.compile(r"^[A-Za-z][A-Za-z0-9-]{0,30}$")
RE_DOMINIO = re.compile(r"^(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,24}$")
CONTROL = re.compile(r"[\x00-\x1f\x7f]")


class CatalogoError(ValueError):
    """Una entrada (o el fichero entero) no cumple el formato."""


@dataclass(frozen=True)
class Categoria:
    id: str
    nombre: str


@dataclass(frozen=True)
class Entrada:
    id: str
    nombre: str
    resumen: str
    descripcion: str
    categoria: str
    tipo: str
    origen: dict
    color: str = "#b9a8ff"
    icono: str = ""
    web: str = ""
    aviso: str = ""
    tamano_mb: int = 0
    desktop_ids: tuple = ()
    navegador: bool = False

    @property
    def etiqueta_tipo(self):
        return ETIQUETAS_TIPO[self.tipo]

    @property
    def drm(self):
        """Una WebApp que necesita DRM (Widevine): solo funciona en Chrome o en un navegador con Widevine."""
        return self.tipo == "webapp" and bool(self.origen.get("drm"))

    @property
    def nombre_icono(self):
        return self.icono or self.id

    @property
    def dominio(self):
        """Host de la web de una WebApp (claude.ai, www.netflix.com…), o ''."""
        if self.tipo != "webapp":
            return ""
        return urlsplit(self.origen.get("url", "")).hostname or ""


@dataclass
class Catalogo:
    categorias: list = field(default_factory=list)
    apps: list = field(default_factory=list)
    problemas: list = field(default_factory=list)   # lo que se descartó, en texto legible

    def __post_init__(self):
        self.por_id = {a.id: a for a in self.apps}

    def de_categoria(self, categoria):
        return [a for a in self.apps if a.categoria == categoria]

    def navegadores(self):
        """Los navegadores que sirven para las WebApps, en el orden recomendado del catálogo."""
        return [a for a in self.apps if a.navegador]

    def buscar(self, texto):
        """Las apps cuyo nombre, resumen o descripción contienen `texto` (sin tildes ni mayúsculas); sin texto, todas."""
        q = normalizar(texto)
        if not q:
            return list(self.apps)
        return [a for a in self.apps
                if q in normalizar(" ".join((a.nombre, a.resumen, a.descripcion, a.id)))]


# --------------------------------------------------------------------------------------- validación
def _texto(d, clave, maximo, obligatorio=True, defecto="", recortar=True):
    """Un campo de texto. Los de texto para mostrar se recortan; los que son identificadores o rutas (`recortar=False`)
    se toman TAL CUAL: un espacio o un salto de línea de más los hace inválidos, igual que para el ayudante de root."""
    valor = d.get(clave, defecto)
    if valor is None:
        valor = defecto
    if not isinstance(valor, str):
        raise CatalogoError(f"«{clave}» debe ser un texto")
    if recortar:
        valor = valor.strip()
    if obligatorio and not valor:
        raise CatalogoError(f"falta «{clave}»")
    if len(valor) > maximo:
        raise CatalogoError(f"«{clave}» es demasiado largo ({len(valor)} > {maximo})")
    if CONTROL.search(valor):
        raise CatalogoError(f"«{clave}» lleva caracteres de control")
    return valor


def url_https(valor, campo="url", barra_final=False):
    """Una URL https:// sin usuario ni espacios, con un nombre de host normal. Devuelve la URL normalizada."""
    if not isinstance(valor, str) or not valor or len(valor) > 300 or re.search(r"[\s\x00-\x1f\x7f\\\"'<>`]", valor):
        raise CatalogoError(f"«{campo}» no es una URL válida")
    partes = urlsplit(valor)
    if partes.scheme != "https" or not partes.hostname or partes.username or partes.password or partes.fragment:
        raise CatalogoError(f"«{campo}» debe ser una URL https:// sin usuario ni fragmento")
    if not RE_DOMINIO.fullmatch(partes.hostname):
        raise CatalogoError(f"«{campo}» tiene un nombre de host raro: {partes.hostname!r}")
    try:
        partes.port
    except ValueError:
        raise CatalogoError(f"«{campo}» tiene un puerto no válido") from None
    if barra_final and not valor.endswith("/"):
        valor += "/"
    return valor


def _lista(d, clave, patron, obligatorio=False, maximo=8):
    valor = d.get(clave)
    if valor is None:
        if obligatorio:
            raise CatalogoError(f"falta «{clave}»")
        return ()
    if not isinstance(valor, list) or len(valor) > maximo or (obligatorio and not valor):
        raise CatalogoError(f"«{clave}» debe ser una lista de {1 if obligatorio else 0} a {maximo} elementos")
    for v in valor:
        if not isinstance(v, str) or not patron.fullmatch(v):
            raise CatalogoError(f"«{clave}» tiene un elemento no válido: {v!r}")
    if len(set(valor)) != len(valor):
        raise CatalogoError(f"«{clave}» tiene elementos repetidos")
    return tuple(valor)


def _origen_flatpak(o):
    remoto = _texto(o, "remoto", 31, recortar=False)
    ref = _texto(o, "ref", 120, recortar=False)
    if not RE_REMOTO.fullmatch(remoto):
        raise CatalogoError(f"«remoto» no es un nombre válido: {remoto!r}")
    if not RE_REF_FLATPAK.fullmatch(ref):
        raise CatalogoError(f"«ref» no es un id de Flatpak: {ref!r}")
    return {"remoto": remoto, "url_remoto": url_https(o.get("url_remoto"), "url_remoto"), "ref": ref}


def _origen_apt(o):
    paquete = _texto(o, "paquete", 100, recortar=False)
    suite = _texto(o, "suite", 64, recortar=False)
    fuente = _texto(o, "archivo_fuente", 70, recortar=False)
    clave = _texto(o, "archivo_clave", 120, recortar=False)
    if not RE_PAQUETE.fullmatch(paquete):
        raise CatalogoError(f"«paquete» no es un nombre de paquete: {paquete!r}")
    if not RE_SUITE.fullmatch(suite):
        raise CatalogoError(f"«suite» no es válida: {suite!r}")
    if not RE_FUENTE.fullmatch(fuente):
        raise CatalogoError(f"«archivo_fuente» debe ser un nombre simple acabado en .list o .sources: {fuente!r}")
    if not RE_CLAVE.fullmatch(clave):
        raise CatalogoError("«archivo_clave» debe estar en /etc/apt/keyrings o /usr/share/keyrings y acabar en .gpg")
    return {
        "paquete": paquete,
        "uri": url_https(o.get("uri"), "uri", barra_final=True),
        "suite": suite,
        "componentes": _lista(o, "componentes", RE_COMPONENTE, obligatorio=True),
        "arquitecturas": _lista(o, "arquitecturas", RE_ARCH),
        "clave_url": url_https(o.get("clave_url"), "clave_url"),
        "archivo_clave": clave,
        "archivo_fuente": fuente,
        "huella": _lista(o, "huella", RE_HUELLA),
    }


def _origen_webapp(o):
    drm = o.get("drm", False)
    if not isinstance(drm, bool):
        raise CatalogoError("«drm» debe ser true o false")
    claves = o.get("palabras_clave", [])
    if not isinstance(claves, list) or len(claves) > 12 or not all(
            isinstance(c, str) and 0 < len(c) <= 30 and not CONTROL.search(c) and ";" not in c for c in claves):
        raise CatalogoError("«palabras_clave» debe ser una lista corta de textos sin «;»")
    return {
        "url": url_https(o.get("url"), "url"),
        "drm": drm,
        "categorias_desktop": _lista(o, "categorias_desktop", RE_ETIQUETA),
        "palabras_clave": tuple(claves),
    }


_ORIGENES = {"flatpak": _origen_flatpak, "apt": _origen_apt, "webapp": _origen_webapp}


def validar_entrada(d, categorias=None):
    """Convierte un dict del JSON en una Entrada, o lanza CatalogoError diciendo qué falla."""
    if not isinstance(d, dict):
        raise CatalogoError("la entrada no es un objeto")
    ident = _texto(d, "id", 40, recortar=False)
    if not RE_ID.fullmatch(ident):
        raise CatalogoError(f"id no válido: {ident!r} (minúsculas, cifras y guiones)")
    tipo = _texto(d, "tipo", 10, recortar=False)
    if tipo not in TIPOS:
        raise CatalogoError(f"tipo desconocido: {tipo!r}")
    categoria = _texto(d, "categoria", 40, recortar=False)
    if categorias is not None and categoria not in categorias:
        raise CatalogoError(f"categoría desconocida: {categoria!r}")
    origen = d.get("origen")
    if not isinstance(origen, dict):
        raise CatalogoError("falta «origen»")
    color = _texto(d, "color", 7, obligatorio=False, defecto="#b9a8ff", recortar=False)
    if not RE_COLOR.fullmatch(color):
        raise CatalogoError(f"color no válido: {color!r}")
    icono = _texto(d, "icono", 40, obligatorio=False, recortar=False)
    if icono and not RE_ID.fullmatch(icono):
        raise CatalogoError(f"icono no válido: {icono!r}")
    tamano = d.get("tamano_mb", 0)
    if isinstance(tamano, bool) or not isinstance(tamano, int) or not 0 <= tamano <= 50000:
        raise CatalogoError("«tamano_mb» debe ser un entero entre 0 y 50000")
    navegador = d.get("navegador", False)
    if not isinstance(navegador, bool):
        raise CatalogoError("«navegador» debe ser true o false")
    web = _texto(d, "web", 300, obligatorio=False)
    if web:
        web = url_https(web, "web")
    desktop_ids = _lista(d, "desktop_ids", RE_DESKTOP_ID)
    if tipo == "webapp" and desktop_ids:
        raise CatalogoError("una WebApp no declara «desktop_ids»: su lanzador lo crea la tienda")
    if navegador and tipo != "apt":
        raise CatalogoError("solo una entrada apt puede ser «navegador»")
    return Entrada(
        id=ident,
        nombre=_texto(d, "nombre", 60),
        resumen=_texto(d, "resumen", 120),
        descripcion=_texto(d, "descripcion", 700, obligatorio=False),
        categoria=categoria,
        tipo=tipo,
        origen=_ORIGENES[tipo](origen),
        color=color,
        icono=icono,
        web=web,
        aviso=_texto(d, "aviso", 400, obligatorio=False),
        tamano_mb=tamano,
        desktop_ids=desktop_ids,
        navegador=navegador,
    )


def interpretar(datos):
    """Lo que hay dentro del JSON ya leído → Catalogo. Lanza CatalogoError si el fichero entero no sirve."""
    if not isinstance(datos, dict):
        raise CatalogoError("el catálogo debe ser un objeto JSON")
    if datos.get("version") != VERSION_FORMATO:
        raise CatalogoError(f"versión de catálogo no soportada: {datos.get('version')!r}")
    crudas = datos.get("categorias")
    if not isinstance(crudas, list) or not crudas:
        raise CatalogoError("faltan las categorías")
    categorias, problemas = [], []
    for c in crudas:
        if (isinstance(c, dict) and isinstance(c.get("id"), str) and RE_ID.fullmatch(c["id"])
                and isinstance(c.get("nombre"), str) and c["nombre"].strip() and len(c["nombre"]) <= 40
                and not CONTROL.search(c["nombre"]) and all(c["id"] != x.id for x in categorias)):
            categorias.append(Categoria(c["id"], c["nombre"].strip()))
        else:
            problemas.append(f"categoría descartada: {c!r}")
    ids_categoria = {c.id for c in categorias}
    crudas_apps = datos.get("apps")
    if not isinstance(crudas_apps, list) or len(crudas_apps) > MAX_APPS:
        raise CatalogoError("«apps» debe ser una lista de hasta %d entradas" % MAX_APPS)
    apps, vistos = [], set()
    for d in crudas_apps:
        nombre = d.get("id", "?") if isinstance(d, dict) else "?"
        try:
            entrada = validar_entrada(d, ids_categoria)
            if entrada.id in vistos:
                raise CatalogoError("id repetido")
        except CatalogoError as e:
            problemas.append(f"entrada «{nombre}» descartada: {e}")
            continue
        vistos.add(entrada.id)
        apps.append(entrada)
    catalogo = Catalogo(categorias, apps, problemas)
    return catalogo


def cargar(ruta=None):
    """Lee y valida el catálogo (por defecto el del sistema). Lanza CatalogoError si no se puede leer o no sirve."""
    ruta = ruta or RUTA_SISTEMA
    try:
        with open(ruta, "rb") as f:
            crudo = f.read(MAX_BYTES + 1)
    except OSError as e:
        raise CatalogoError(f"no se pudo leer el catálogo ({ruta}): {e.strerror or e}") from None
    if len(crudo) > MAX_BYTES:
        raise CatalogoError("el catálogo es demasiado grande")
    try:
        datos = json.loads(crudo.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, RecursionError) as e:
        raise CatalogoError(f"el catálogo no es un JSON válido: {e}") from None
    return interpretar(datos)


def ruta_catalogo():
    """El catálogo instalado, o el del propio repositorio al desarrollar."""
    if os.path.exists(RUTA_SISTEMA):
        return RUTA_SISTEMA
    aqui = os.path.dirname(os.path.abspath(__file__))
    return os.path.abspath(os.path.join(aqui, "..", "..", "overlay", "usr", "share", "infinity", "tienda", "catalogo.json"))


def ruta_icono(entrada_o_nombre, carpetas=None):
    """Ruta del SVG de una entrada (o de un nombre de icono), o None si no está en ninguna carpeta."""
    nombre = entrada_o_nombre if isinstance(entrada_o_nombre, str) else entrada_o_nombre.nombre_icono
    if not RE_ID.fullmatch(nombre or ""):
        return None
    aqui = os.path.dirname(os.path.abspath(__file__))
    repo = os.path.abspath(os.path.join(aqui, "..", "..", "overlay", "usr", "share", "infinity", "tienda", "iconos"))
    for carpeta in carpetas if carpetas is not None else (DIR_ICONOS_SISTEMA, repo):
        ruta = os.path.join(carpeta, nombre + ".svg")
        if os.path.isfile(ruta):
            return ruta
    return None
