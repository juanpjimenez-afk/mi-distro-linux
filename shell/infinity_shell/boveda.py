"""Bóveda de contraseñas de Infinity OS: la parte SIN pantalla (formato, cifrado, ficheros, generador).

La ventana está en boveda_app.py; el modelo de seguridad completo, con sus límites, en docs/boveda.md.
Este módulo no importa GTK: se prueba sin pantalla (shell/tests/test_boveda.py).

Fichero (~/.local/share/infinity/boveda/boveda.enc, carpeta 0700, fichero 0600):

    cabecera (62 B, autenticada como AAD)         magia "INFBOVED" (8) · versión u16 · id de KDF u8 (1 = scrypt)
                                                  · log2(N) u8 · r u8 · p u8 · sal (16) · verificador (32)
    nonce (12 B, nuevo en CADA guardado)
    texto cifrado + etiqueta GCM (16 B)           AES-256-GCM del JSON {"v": 1, "entradas": [...]}

  * La clave (32 B) sale de la contraseña maestra (NFKC, UTF-8) con hashlib.scrypt (N=2**16, r=8, p=1).
  * La cabecera entera va como datos autenticados: cambiar un solo byte de ella (o del nonce o del
    cifrado) hace fallar la etiqueta, y nunca se devuelven datos.
  * El «verificador» es HMAC-SHA256(clave, constante): solo sirve para distinguir una contraseña
    equivocada (ClaveIncorrecta) de un fichero dañado (BovedaDanada), porque con GCM a secas las dos
    cosas fallan igual. No da ventaja a un atacante: comprobar una contraseña ya exigía el mismo
    scrypt, y HMAC no revela la clave.
  * Se escribe a un temporal 0600 en la misma carpeta, fsync, copia «.bak» de la versión anterior y
    os.replace; luego fsync de la carpeta. Todo con descriptores de carpeta (dir_fd) y O_NOFOLLOW.

LÍMITES (están en docs/boveda.md): Python no puede garantizar borrar secretos de la memoria (las cadenas son
inmutables y el recolector puede dejar copias); aquí solo se hace lo posible (puesta a cero de la clave que
guardamos, soltar referencias, gc.collect()). Y el sistema de ficheros puede conservar bloques antiguos.
"""
import calendar
import contextlib
import dataclasses
import errno
import gc
import hashlib
import hmac
import json
import math
import os
import re
import secrets
import stat
import struct
import time
import unicodedata
from dataclasses import dataclass

try:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:  # sin python3-cryptography: el módulo se importa, pero no abre ni crea bóvedas
    AESGCM = InvalidTag = None

# ----------------------------------------------------------------------------------------- formato
MAGIA = b"INFBOVED"
VERSION = 1
KDF_SCRYPT = 1
LONG_SAL = 16
LONG_NONCE = 12
LONG_CLAVE = 32
LONG_VERIF = 32
LONG_TAG = 16
_FORMATO_CAB = ">8sHBBBB16s32s"  # magia, versión, kdf, log2N, r, p, sal, verificador
LONG_CAB = struct.calcsize(_FORMATO_CAB)
_VERIF_TEXTO = b"infinity-boveda/v1/comprobacion"

NOMBRE = "boveda.enc"
NOMBRE_COPIA = NOMBRE + ".bak"
NOMBRE_CORRUPTA = NOMBRE + ".corrupta"

# Tamaños: nada se lee ni se escribe por encima de esto (un fichero manipulado no agota la memoria)
MAX_TEXTO = 4 * 1024 * 1024
MAX_FICHERO = LONG_CAB + LONG_NONCE + MAX_TEXTO + LONG_TAG + 1024
BLOQUE_RELLENO = 1024  # el JSON se rellena con espacios hasta un múltiplo: el tamaño no dice cuántas entradas hay
MAX_ENTRADAS = 2000

CATEGORIAS = ("Correo", "Router y red", "Web", "Banco", "Otros")
LIMITES_CAMPO = {"servicio": 120, "usuario": 254, "contrasena": 1024, "url": 500, "notas": 5000}
_NOMBRES_CAMPO = {"servicio": "nombre del servicio", "usuario": "usuario", "contrasena": "contraseña",
                  "url": "dirección (URL o IP)", "notas": "notas"}
_CLAVES_ENTRADA = frozenset(("id", "servicio", "usuario", "contrasena", "url", "notas", "categoria",
                             "creada", "modificada"))
_ID = re.compile(r"^[0-9a-f]{8,64}$")
_FECHA = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")

MIN_MAESTRA = 8
MAX_MAESTRA = 256
MAX_MAESTRA_ENTRADA = 1024  # al ABRIR se admite más (no se obliga a nada), pero no se deriva algo absurdo


# ------------------------------------------------------------------------------------------ errores
class BovedaError(Exception):
    """Base de los errores de la bóveda. Los mensajes son texto fijo: nunca llevan datos de las entradas."""


class ClaveIncorrecta(BovedaError):
    """La contraseña maestra no es la de esta bóveda."""


class BovedaDanada(BovedaError):
    """El fichero no es una bóveda válida o ha sido alterado (la contraseña, si se dio, era la correcta)."""


class VersionNoSoportada(BovedaDanada):
    """Magia o versión que este programa no conoce (otra versión de Infinity OS, u otro fichero)."""


class BovedaNoExiste(BovedaError):
    """No hay bóveda (o falta la copia que se pidió)."""


class BovedaYaExiste(BovedaError):
    """Crear no sobrescribe nunca una bóveda que ya está."""


class RutaInsegura(BovedaError):
    """La carpeta o el fichero son un enlace simbólico, de otro usuario o de un tipo inesperado."""


class DatosInvalidos(BovedaError, ValueError):
    """Lo que se intentó guardar no es válido (campo demasiado largo, vacío, categoría desconocida...)."""


# ---------------------------------------------------------------------------------------------- KDF
@dataclass(frozen=True)
class ParamsKDF:
    """Parámetros de scrypt. n = 2**log2n."""
    log2n: int = 16
    r: int = 8
    p: int = 1

    @property
    def n(self):
        return 1 << self.log2n


@dataclass(frozen=True)
class LimitesKDF:
    """Qué parámetros acepta AL ABRIR una cabecera: ni tan bajos que debiliten la clave, ni tan altos
    que un fichero manipulado agote la memoria o el tiempo. Solo las pruebas pasan otros límites."""
    log2n: tuple = (14, 20)
    r: tuple = (8, 16)
    p: tuple = (1, 4)
    memoria_max: int = 1 << 30  # 128 · N · r

    def comprobar(self, params):
        for valor, (minimo, maximo) in ((params.log2n, self.log2n), (params.r, self.r), (params.p, self.p)):
            if not minimo <= valor <= maximo:
                raise BovedaDanada("La cabecera de la bóveda trae parámetros de cifrado fuera de lo permitido.")
        if 128 * params.n * params.r > self.memoria_max:
            raise BovedaDanada("La cabecera de la bóveda pide demasiada memoria.")


KDF_POR_DEFECTO = ParamsKDF()
LIMITES_POR_DEFECTO = LimitesKDF()


def _exigir_cripto():
    if AESGCM is None:
        raise BovedaError("Falta el paquete python3-cryptography: la bóveda no puede cifrar ni descifrar.")


def _normalizar(password):
    """La contraseña maestra como bytes: NFKC y UTF-8 (así «é» da la misma clave se teclee como se teclee)."""
    return unicodedata.normalize("NFKC", password).encode("utf-8")


def derivar_clave(password, sal, params):
    """scrypt → 32 bytes. Tarda del orden de 0,3 s con los parámetros reales (hazlo en un hilo)."""
    return hashlib.scrypt(_normalizar(password), salt=sal, n=params.n, r=params.r, p=params.p,
                          maxmem=128 * params.r * (params.n + params.p + 2) + (1 << 20), dklen=LONG_CLAVE)


def _verificador(clave):
    return hmac.new(bytes(clave), _VERIF_TEXTO, hashlib.sha256).digest()


def validar_maestra(password):
    """Reglas de una contraseña maestra NUEVA (crear o cambiar). Lanza DatosInvalidos."""
    if not isinstance(password, str):
        raise DatosInvalidos("La contraseña maestra no es válida.")
    normal = unicodedata.normalize("NFKC", password)
    if not normal.strip():
        raise DatosInvalidos("La contraseña maestra no puede estar vacía ni ser solo espacios.")
    if len(normal) < MIN_MAESTRA:
        raise DatosInvalidos(f"La contraseña maestra debe tener al menos {MIN_MAESTRA} caracteres.")
    if len(normal) > MAX_MAESTRA:
        raise DatosInvalidos(f"La contraseña maestra puede tener como máximo {MAX_MAESTRA} caracteres.")
    if "\x00" in normal:
        raise DatosInvalidos("La contraseña maestra no puede llevar caracteres de control.")
    try:
        normal.encode("utf-8")
    except UnicodeEncodeError:
        raise DatosInvalidos("La contraseña maestra lleva caracteres que no se pueden guardar.") from None


def espera_tras_fallos(fallos, maximo=30):
    """Segundos de espera antes de dejar reintentar tras `fallos` contraseñas seguidas incorrectas:
    1, 2, 4, 8, 16, 30, 30... (el coste de scrypt ya frena la fuerza bruta; esto frena al curioso)."""
    if fallos <= 0:
        return 0
    return min(maximo, 2 ** min(fallos - 1, 10))


# ------------------------------------------------------------------------------------- cabecera/fichero
@dataclass(frozen=True)
class _Cabecera:
    params: ParamsKDF
    sal: bytes
    verif: bytes

    def empaquetar(self):
        return struct.pack(_FORMATO_CAB, MAGIA, VERSION, KDF_SCRYPT, self.params.log2n, self.params.r,
                           self.params.p, self.sal, self.verif)


def _separar(datos, limites):
    """Parte un fichero en (cabecera, bytes de la cabecera, nonce, cifrado). BovedaDanada si no cuadra."""
    if len(datos) < LONG_CAB + LONG_NONCE + LONG_TAG:
        raise BovedaDanada("El fichero de la bóveda está incompleto.")
    magia, version, kdf, log2n, r, p, sal, verif = struct.unpack(_FORMATO_CAB, datos[:LONG_CAB])
    if magia != MAGIA:
        raise VersionNoSoportada("Este fichero no es una bóveda de Infinity OS.")
    if version != VERSION:
        raise VersionNoSoportada(f"Esta bóveda es de la versión {version} y este programa solo conoce la {VERSION}.")
    if kdf != KDF_SCRYPT:
        raise VersionNoSoportada("La bóveda usa un método de protección que este programa no conoce.")
    params = ParamsKDF(log2n, r, p)
    limites.comprobar(params)
    resto = datos[LONG_CAB:]
    return _Cabecera(params, sal, verif), datos[:LONG_CAB], resto[:LONG_NONCE], resto[LONG_NONCE:]


def _hacer_cripto(clave):
    _exigir_cripto()
    return AESGCM(bytes(clave))


def _abrir_cifrado(clave, cab_bytes, nonce, cifrado):
    """Descifra y comprueba la etiqueta. BovedaDanada si no cuadra (la clave ya se comprobó con el verificador)."""
    try:
        return _hacer_cripto(clave).decrypt(nonce, cifrado, cab_bytes)
    except InvalidTag:
        pass
    raise BovedaDanada("La bóveda está dañada o ha sido modificada: no se puede leer.")


# ------------------------------------------------------------------------------------------ entradas
@dataclass(frozen=True, repr=False)
class Entrada:
    """Una contraseña guardada. Inmutable: para cambiarla se crea otra (dataclasses.replace).

    repr()/str() NO muestran ningún dato (ni siquiera el servicio): solo el id."""
    id: str
    servicio: str
    usuario: str = ""
    contrasena: str = ""
    url: str = ""
    notas: str = ""
    categoria: str = "Otros"
    creada: str = ""
    modificada: str = ""

    def __repr__(self):
        return f"Entrada(id={self.id!r})"

    __str__ = __repr__

    def a_dict(self):
        return dataclasses.asdict(self)


def normalizar_campos(servicio, usuario="", contrasena="", url="", notas="", categoria="Otros", *, error=DatosInvalidos):
    """Valida y normaliza los campos de una entrada. Devuelve un dict; `error` es la excepción a lanzar
    (DatosInvalidos al escribir, BovedaDanada al leer un fichero). Los mensajes no llevan los valores."""
    valores = {"servicio": servicio, "usuario": usuario, "contrasena": contrasena, "url": url, "notas": notas}
    for nombre, valor in valores.items():
        if not isinstance(valor, str):
            raise error(f"El campo «{_NOMBRES_CAMPO[nombre]}» no es texto.")
        if "\x00" in valor:
            raise error(f"El campo «{_NOMBRES_CAMPO[nombre]}» lleva caracteres no válidos.")
        if len(valor) > LIMITES_CAMPO[nombre]:
            raise error(f"El campo «{_NOMBRES_CAMPO[nombre]}» es demasiado largo "
                        f"(máximo {LIMITES_CAMPO[nombre]} caracteres).")
    for nombre in ("servicio", "usuario", "url"):  # la contraseña y las notas se guardan tal cual
        valores[nombre] = valores[nombre].strip()
    if not valores["servicio"]:
        raise error("El nombre del servicio es obligatorio.")
    if not isinstance(categoria, str) or categoria not in CATEGORIAS:
        raise error("La categoría no es válida.")
    valores["categoria"] = categoria
    return valores


def _entrada_desde_dict(d):
    if not isinstance(d, dict) or set(d) != _CLAVES_ENTRADA:
        raise BovedaDanada("El contenido de la bóveda no tiene el formato esperado.")
    if not isinstance(d["id"], str) or not _ID.match(d["id"]):
        raise BovedaDanada("El contenido de la bóveda no tiene el formato esperado.")
    for fecha in (d["creada"], d["modificada"]):
        if not isinstance(fecha, str) or not _FECHA.match(fecha):
            raise BovedaDanada("El contenido de la bóveda no tiene el formato esperado.")
    valores = normalizar_campos(d["servicio"], d["usuario"], d["contrasena"], d["url"], d["notas"],
                                d["categoria"], error=BovedaDanada)
    return Entrada(id=d["id"], creada=d["creada"], modificada=d["modificada"], **valores)


def _serializar(entradas):
    """El JSON que se cifra, relleno con espacios hasta un múltiplo de BLOQUE_RELLENO."""
    texto = json.dumps({"v": VERSION, "entradas": [e.a_dict() for e in entradas]},
                       ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    if len(texto) > MAX_TEXTO:
        raise DatosInvalidos("Hay demasiados datos guardados: la bóveda no puede crecer más.")
    return texto + b" " * (-len(texto) % BLOQUE_RELLENO)


def _deserializar(texto):
    try:
        contenido = json.loads(texto.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        contenido = None  # (sin encadenar la excepción: JSONDecodeError guarda el documento entero)
    if not isinstance(contenido, dict) or set(contenido) != {"v", "entradas"} or contenido["v"] != VERSION \
            or not isinstance(contenido["entradas"], list) or len(contenido["entradas"]) > MAX_ENTRADAS:
        raise BovedaDanada("El contenido de la bóveda no tiene el formato esperado.")
    entradas = tuple(_entrada_desde_dict(d) for d in contenido["entradas"])
    if len({e.id for e in entradas}) != len(entradas):
        raise BovedaDanada("El contenido de la bóveda no tiene el formato esperado.")
    return entradas


_MESES = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


def fecha_legible(iso):
    """«2026-10-20T14:05:00Z» → «20 oct 2026, 11:05» (hora local). «—» si no es una fecha."""
    try:
        local = time.localtime(calendar.timegm(time.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")))
    except (ValueError, OverflowError, TypeError):
        return "—"
    return f"{local.tm_mday} {_MESES[local.tm_mon - 1]} {local.tm_year}, {local.tm_hour:02d}:{local.tm_min:02d}"


def _plegar(texto):
    """Minúsculas y sin acentos, para buscar («Cámara» encuentra «camara»)."""
    descompuesto = unicodedata.normalize("NFKD", texto.casefold())
    return "".join(c for c in descompuesto if not unicodedata.combining(c))


# ---------------------------------------------------------------------------------------------- rutas
def ruta_datos():
    """~/.local/share/infinity/boveda (o $XDG_DATA_HOME/infinity/boveda si es una ruta absoluta)."""
    base = os.environ.get("XDG_DATA_HOME", "")
    if not (base and os.path.isabs(base)):
        base = os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "infinity", "boveda")


def ruta_boveda():
    return os.path.join(ruta_datos(), NOMBRE)


@contextlib.contextmanager
def _abrir_carpeta(carpeta, crear):
    """Abre la carpeta de la bóveda y da su descriptor. Si no existe y `crear`, la crea 0700. Comprueba
    que no es un enlace simbólico y que es tuya; si tiene permisos de más, los quita."""
    try:
        if crear:
            os.makedirs(carpeta, mode=0o700, exist_ok=True)
        fd = os.open(carpeta, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except FileNotFoundError:
        raise BovedaNoExiste("No hay ninguna bóveda.") from None
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENOTDIR):
            raise RutaInsegura("La carpeta de la bóveda no es segura (es un enlace o no es una carpeta).") from None
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
            raise RutaInsegura("La carpeta de la bóveda no es tuya.")
        if info.st_mode & 0o077:
            os.fchmod(fd, 0o700)
        yield fd
    finally:
        os.close(fd)


def _existe(dfd, nombre):
    try:
        os.lstat(nombre, dir_fd=dfd)
        return True
    except FileNotFoundError:
        return False


def _leer(dfd, nombre):
    """Lee un fichero de la carpeta: regular, tuyo, sin seguir enlaces y de tamaño razonable."""
    try:
        fd = os.open(nombre, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=dfd)
    except FileNotFoundError:
        raise BovedaNoExiste("No se encuentra el fichero de la bóveda.") from None
    except OSError as e:
        if e.errno in (errno.ELOOP, errno.ENXIO):
            raise RutaInsegura("El fichero de la bóveda no es seguro (es un enlace).") from None
        raise
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid():
            raise RutaInsegura("El fichero de la bóveda no es un fichero normal o no es tuyo.")
        if info.st_size > MAX_FICHERO:
            raise BovedaDanada("El fichero de la bóveda es demasiado grande para ser una bóveda.")
        if info.st_mode & 0o077:
            os.fchmod(fd, 0o600)
        trozos, total = [], 0
        while True:
            trozo = os.read(fd, 1 << 16)
            if not trozo:
                break
            total += len(trozo)
            if total > MAX_FICHERO:
                raise BovedaDanada("El fichero de la bóveda es demasiado grande para ser una bóveda.")
            trozos.append(trozo)
        return b"".join(trozos)
    finally:
        os.close(fd)


def _escribir_temporal(dfd, datos, base):
    """Crea `.base.PID.ALEATORIO.tmp` (O_EXCL, 0600), escribe y hace fsync. Devuelve su nombre.
    Si algo falla, no deja el temporal."""
    nombre = f".{base}.{os.getpid()}.{secrets.token_hex(4)}.tmp"
    fd = os.open(nombre, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600, dir_fd=dfd)
    try:
        os.fchmod(fd, 0o600)
        vista = memoryview(datos)
        while vista:
            vista = vista[os.write(fd, vista):]
        os.fsync(fd)
    except BaseException:
        os.close(fd)
        _borrar_si_hay(dfd, nombre)
        raise
    os.close(fd)
    return nombre


def _borrar_si_hay(dfd, nombre):
    with contextlib.suppress(OSError):
        os.unlink(nombre, dir_fd=dfd)


def _publicar(dfd, temporal, destino):
    """os.replace atómico del temporal sobre `destino`; si falla, no deja el temporal."""
    try:
        os.replace(temporal, destino, src_dir_fd=dfd, dst_dir_fd=dfd)
    except BaseException:
        _borrar_si_hay(dfd, temporal)
        raise


def _limpiar_temporales(dfd, base):
    """Quita temporales de una escritura anterior que se cortó (más de un minuto de antigüedad)."""
    try:
        nombres = os.listdir(dfd)
    except OSError:
        return
    for nombre in nombres:
        if nombre.startswith(f".{base}.") and nombre.endswith(".tmp"):
            with contextlib.suppress(OSError):
                info = os.lstat(nombre, dir_fd=dfd)
                if stat.S_ISREG(info.st_mode) and time.time() - info.st_mtime > 60:
                    os.unlink(nombre, dir_fd=dfd)


# ------------------------------------------------------------------------------------------- bóveda
class Boveda:
    """La bóveda: crea, abre, guarda y cierra. Mientras está abierta guarda en memoria la clave derivada y
    las entradas; `bloquear()` las suelta.

    `kdf` y `limites` existen SOLO para las pruebas (parámetros de scrypt reducidos); la aplicación no los
    toca. No es segura entre hilos: la ventana la usa de una operación en una."""

    def __init__(self, ruta=None, *, kdf=KDF_POR_DEFECTO, limites=LIMITES_POR_DEFECTO, ahora=None):
        ruta = ruta or ruta_boveda()
        self._carpeta = os.path.dirname(os.path.abspath(ruta))
        self._nombre = os.path.basename(ruta)
        self._kdf = kdf
        self._limites = limites
        self._ahora = ahora or (lambda: time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        self._clave = None       # bytearray, para poder ponerla a cero al bloquear
        self._cabecera = None    # _Cabecera
        self._entradas = ()
        self._ultimo_nonce = None

    def __repr__(self):
        return f"<Boveda {'abierta' if self.abierta else 'cerrada'}>"

    __str__ = __repr__

    @property
    def abierta(self):
        return self._clave is not None

    @property
    def entradas(self):
        """Las entradas, ordenadas por nombre. Tupla de Entrada (inmutables)."""
        self._exigir_abierta()
        return tuple(sorted(self._entradas, key=lambda e: (_plegar(e.servicio), e.id)))

    def _exigir_abierta(self):
        if not self.abierta:
            raise BovedaError("La bóveda está bloqueada.")

    @property
    def _copia(self):
        return self._nombre + ".bak"

    # ------------------------------------------------------------------------------- estado en disco
    def estado(self):
        """«nueva» (no hay nada), «existe» (hay bóveda) o «solo_copia» (falta el principal pero hay .bak)."""
        try:
            with _abrir_carpeta(self._carpeta, crear=False) as dfd:
                if _existe(dfd, self._nombre):
                    return "existe"
                return "solo_copia" if _existe(dfd, self._copia) else "nueva"
        except BovedaNoExiste:
            return "nueva"

    def hay_copia(self):
        try:
            with _abrir_carpeta(self._carpeta, crear=False) as dfd:
                return _existe(dfd, self._copia)
        except BovedaNoExiste:
            return False

    # ------------------------------------------------------------------------------- crear / abrir
    def crear(self, password):
        """Bóveda nueva y vacía con esa contraseña maestra (mín. 8 caracteres). No sobrescribe nada."""
        _exigir_cripto()
        if self.abierta:
            raise BovedaError("La bóveda ya está abierta.")
        validar_maestra(password)
        with _abrir_carpeta(self._carpeta, crear=True) as dfd:
            if _existe(dfd, self._nombre) or _existe(dfd, self._copia):
                raise BovedaYaExiste("Ya hay una bóveda: no se sobrescribe.")
            clave, cabecera = self._clave_nueva(password)
            self._poner(clave, cabecera, ())
            try:
                self._escribir(dfd, (), copia=None)
            except BaseException:
                self.bloquear()
                raise

    def abrir(self, password, *, copia=False):
        """Abre la bóveda (o su copia .bak si copia=True; entonces restaura el principal desde ella).
        ClaveIncorrecta si la contraseña no es la buena; BovedaDanada si el fichero no es válido."""
        _exigir_cripto()
        if self.abierta:
            raise BovedaError("La bóveda ya está abierta.")
        with _abrir_carpeta(self._carpeta, crear=False) as dfd:
            datos = _leer(dfd, self._copia if copia else self._nombre)
        cabecera, cab_bytes, nonce, cifrado = _separar(datos, self._limites)
        if not isinstance(password, str) or not password or len(password) > MAX_MAESTRA_ENTRADA:
            raise ClaveIncorrecta("Contraseña incorrecta.")
        try:
            clave = bytearray(derivar_clave(password, cabecera.sal, cabecera.params))
        except UnicodeEncodeError:
            raise ClaveIncorrecta("Contraseña incorrecta.") from None
        try:
            if not hmac.compare_digest(_verificador(clave), cabecera.verif):
                raise ClaveIncorrecta("Contraseña incorrecta.")
            entradas = _deserializar(_abrir_cifrado(clave, cab_bytes, nonce, cifrado))
        except BaseException:
            _poner_a_cero(clave)
            raise
        self._poner(clave, cabecera, entradas)
        if copia:
            self._restaurar_principal()

    def _restaurar_principal(self):
        """Tras abrir la .bak: el principal dañado se aparta como «.corrupta» y se escribe uno nuevo."""
        try:
            with _abrir_carpeta(self._carpeta, crear=False) as dfd:
                with contextlib.suppress(FileNotFoundError):
                    os.replace(self._nombre, self._nombre + ".corrupta", src_dir_fd=dfd, dst_dir_fd=dfd)
                self._escribir(dfd, self._entradas, copia=None)
        except BaseException:
            self.bloquear()
            raise

    def _clave_nueva(self, password):
        sal = os.urandom(LONG_SAL)
        clave = bytearray(derivar_clave(password, sal, self._kdf))
        return clave, _Cabecera(self._kdf, sal, _verificador(clave))

    def _poner(self, clave, cabecera, entradas):
        self._clave, self._cabecera, self._entradas = clave, cabecera, tuple(entradas)

    # ------------------------------------------------------------------------------------ guardar
    def _empaquetar(self, clave, cabecera, entradas):
        """Fichero completo: cabecera + nonce NUEVO + cifrado. Nunca se reutiliza un nonce."""
        cab_bytes = cabecera.empaquetar()
        nonce = os.urandom(LONG_NONCE)
        while nonce == self._ultimo_nonce:
            nonce = os.urandom(LONG_NONCE)
        cifrado = _hacer_cripto(clave).encrypt(nonce, _serializar(entradas), cab_bytes)
        self._ultimo_nonce = nonce
        return cab_bytes + nonce + cifrado

    def _version_anterior_valida(self, dfd):
        """Los bytes del principal actual si SON de esta misma bóveda y se descifran con nuestra clave
        (si está dañado, o es de otra contraseña, no se copia sobre la .bak: la buena se conserva)."""
        try:
            actual = _leer(dfd, self._nombre)
            _, cab_bytes, nonce, cifrado = _separar(actual, self._limites)
            if cab_bytes != self._cabecera.empaquetar():
                return None
            _abrir_cifrado(self._clave, cab_bytes, nonce, cifrado)
            return actual
        except (BovedaError, OSError):
            return None

    def _escribir(self, dfd, entradas, *, copia="anterior", clave=None, cabecera=None):
        """Guarda de forma atómica. `copia`: «anterior» = la versión previa pasa a .bak; None = no se toca la
        .bak; o unos bytes = esos bytes pasan a .bak (cambio de contraseña: la .bak nunca conserva la vieja)."""
        clave = self._clave if clave is None else clave
        cabecera = self._cabecera if cabecera is None else cabecera
        _limpiar_temporales(dfd, self._nombre)
        datos = self._empaquetar(clave, cabecera, entradas)
        temporal = _escribir_temporal(dfd, datos, self._nombre)
        try:
            if copia == "anterior":
                copia = self._version_anterior_valida(dfd)
            elif copia == "nuevos":
                copia = datos
            if copia is not None:
                tmp_copia = _escribir_temporal(dfd, copia, self._copia)
                _publicar(dfd, tmp_copia, self._copia)  # primero la .bak: siempre queda una versión válida
            _publicar(dfd, temporal, self._nombre)
        except BaseException:
            _borrar_si_hay(dfd, temporal)
            raise
        os.fsync(dfd)

    def _guardar(self, entradas):
        """Escribe `entradas` y, solo si salió bien, pasan a ser las de la memoria."""
        with _abrir_carpeta(self._carpeta, crear=False) as dfd:
            self._escribir(dfd, entradas)
        self._entradas = tuple(entradas)

    # ------------------------------------------------------------------------------------ entradas
    def agregar(self, servicio, usuario="", contrasena="", url="", notas="", categoria="Otros"):
        self._exigir_abierta()
        valores = normalizar_campos(servicio, usuario, contrasena, url, notas, categoria)
        if len(self._entradas) >= MAX_ENTRADAS:
            raise DatosInvalidos(f"Se alcanzó el máximo de {MAX_ENTRADAS} entradas.")
        ahora = self._ahora()
        existentes = {e.id for e in self._entradas}
        nuevo_id = secrets.token_hex(8)
        while nuevo_id in existentes:
            nuevo_id = secrets.token_hex(8)
        entrada = Entrada(id=nuevo_id, creada=ahora, modificada=ahora, **valores)
        self._guardar(self._entradas + (entrada,))
        return entrada

    def actualizar(self, entrada_id, servicio, usuario="", contrasena="", url="", notas="", categoria="Otros"):
        self._exigir_abierta()
        valores = normalizar_campos(servicio, usuario, contrasena, url, notas, categoria)
        previa = self.obtener(entrada_id)
        nueva = dataclasses.replace(previa, modificada=self._ahora(), **valores)
        self._guardar(tuple(nueva if e.id == entrada_id else e for e in self._entradas))
        return nueva

    def borrar(self, entrada_id):
        self._exigir_abierta()
        self.obtener(entrada_id)
        self._guardar(tuple(e for e in self._entradas if e.id != entrada_id))

    def obtener(self, entrada_id):
        self._exigir_abierta()
        for e in self._entradas:
            if e.id == entrada_id:
                return e
        raise BovedaError("Esa entrada ya no existe.")

    def buscar(self, texto="", categoria=None):
        """Entradas que contienen TODAS las palabras de `texto` (sin mayúsculas ni acentos) en el nombre,
        usuario, URL/IP, notas o categoría —nunca en la contraseña—, dentro de `categoria` si se da."""
        palabras = _plegar(texto).split()
        salida = []
        for e in self.entradas:
            if categoria and e.categoria != categoria:
                continue
            pajar = _plegar(" ".join((e.servicio, e.usuario, e.url, e.notas, e.categoria)))
            if all(p in pajar for p in palabras):
                salida.append(e)
        return salida

    def contar_por_categoria(self):
        """{"Todas": n, "Correo": n, ...} (todas las categorías aparecen, aunque sea con 0)."""
        self._exigir_abierta()
        cuentas = {c: 0 for c in CATEGORIAS}
        for e in self._entradas:
            cuentas[e.categoria] += 1
        return {"Todas": len(self._entradas), **cuentas}

    # ------------------------------------------------------------------- contraseña maestra / copias
    def cambiar_password(self, actual, nueva):
        """Re-cifra todo con una contraseña maestra nueva y una SAL NUEVA. Pide la actual (ClaveIncorrecta
        si no lo es). La .bak pasa a ser una copia con la contraseña nueva: la vieja deja de valer en todas partes."""
        self._exigir_abierta()
        if not isinstance(actual, str) or not actual or len(actual) > MAX_MAESTRA_ENTRADA:
            raise ClaveIncorrecta("Contraseña incorrecta.")
        try:
            prueba = bytearray(derivar_clave(actual, self._cabecera.sal, self._cabecera.params))
        except UnicodeEncodeError:
            raise ClaveIncorrecta("Contraseña incorrecta.") from None
        try:
            if not hmac.compare_digest(_verificador(prueba), self._cabecera.verif):
                raise ClaveIncorrecta("Contraseña incorrecta.")
        finally:
            _poner_a_cero(prueba)
        validar_maestra(nueva)
        if hmac.compare_digest(_normalizar(actual), _normalizar(nueva)):
            raise DatosInvalidos("La contraseña nueva tiene que ser distinta de la actual.")
        clave, cabecera = self._clave_nueva(nueva)
        try:
            with _abrir_carpeta(self._carpeta, crear=False) as dfd:
                self._escribir(dfd, self._entradas, copia="nuevos", clave=clave, cabecera=cabecera)
                _borrar_si_hay(dfd, self._nombre + ".corrupta")  # también está cifrada con la vieja
        except BaseException:
            _poner_a_cero(clave)
            raise
        _poner_a_cero(self._clave)
        self._clave, self._cabecera = clave, cabecera

    def exportar(self, destino):
        """Copia el fichero cifrado a `destino` (permisos 0600), de forma atómica. No descifra nada: lo que
        sale es exactamente lo que hay en disco, y solo se abre con la contraseña maestra."""
        self._exigir_abierta()
        with _abrir_carpeta(self._carpeta, crear=False) as dfd:
            datos = _leer(dfd, self._nombre)
        _separar(datos, self._limites)  # que al menos sea una bóveda con cabecera válida
        destino = os.path.abspath(destino)
        carpeta, nombre = os.path.dirname(destino), os.path.basename(destino)
        if not nombre:
            raise DatosInvalidos("Elige un nombre de fichero para la copia.")
        propia = os.path.realpath(os.path.join(self._carpeta, self._nombre))
        if os.path.realpath(destino) in (propia, propia + ".bak"):
            raise DatosInvalidos("La copia no puede sobrescribir la propia bóveda.")
        dfd = os.open(carpeta, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
        try:
            temporal = _escribir_temporal(dfd, datos, nombre)
            _publicar(dfd, temporal, nombre)
            with contextlib.suppress(OSError):
                os.fsync(dfd)
        finally:
            os.close(dfd)

    def bloquear(self):
        """Suelta la clave y las entradas. Python no garantiza que la memoria se borre de verdad (ver el
        docstring del módulo): se pone a cero la clave que guardamos, se sueltan TODAS las referencias y se
        recoge basura."""
        if self._clave is not None:
            _poner_a_cero(self._clave)
        self._clave = None
        self._cabecera = None
        self._entradas = ()
        self._ultimo_nonce = None
        gc.collect()


def _poner_a_cero(clave):
    if isinstance(clave, bytearray):
        clave[:] = bytes(len(clave))


# ------------------------------------------------------------------------------------------ generador
MINUSCULAS = "abcdefghijklmnopqrstuvwxyz"
MAYUSCULAS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
DIGITOS = "0123456789"
SIMBOLOS = "!@#$%^&*()-_=+[]{};:,.?/~"
AMBIGUOS = "0O1lI"
LONG_GENERADA = (8, 64)


def generar_contrasena(longitud=20, mayusculas=True, minusculas=True, digitos=True, simbolos=True,
                       evitar_ambiguos=False, azar=None):
    """Contraseña aleatoria con `secrets`. Garantiza al menos un carácter de cada clase pedida y baraja
    con secrets.SystemRandom. `azar` solo existe para las pruebas."""
    if isinstance(longitud, bool) or not isinstance(longitud, int) or not LONG_GENERADA[0] <= longitud <= LONG_GENERADA[1]:
        raise DatosInvalidos(f"La longitud tiene que estar entre {LONG_GENERADA[0]} y {LONG_GENERADA[1]}.")
    clases = [alfabeto for usar, alfabeto in ((minusculas, MINUSCULAS), (mayusculas, MAYUSCULAS),
                                              (digitos, DIGITOS), (simbolos, SIMBOLOS)) if usar]
    if evitar_ambiguos:
        clases = ["".join(c for c in a if c not in AMBIGUOS) for a in clases]
    if not clases:
        raise DatosInvalidos("Elige al menos un tipo de carácter.")
    azar = azar or secrets.SystemRandom()
    todos = "".join(clases)
    caracteres = [azar.choice(a) for a in clases]                      # uno de cada clase
    caracteres += [azar.choice(todos) for _ in range(longitud - len(caracteres))]
    azar.shuffle(caracteres)
    return "".join(caracteres)


# ------------------------------------------------------------------------------------- fortaleza
@dataclass(frozen=True)
class Fortaleza:
    puntos: int        # 0 (muy débil) … 4 (excelente)
    bits: float        # estimación de entropía tras penalizar patrones
    etiqueta: str
    avisos: tuple


ETIQUETAS_FORTALEZA = ("Muy débil", "Débil", "Aceptable", "Buena", "Excelente")
_UMBRALES = (28, 40, 60, 80)  # bits: por debajo de cada uno, un punto menos
_FILAS_TECLADO = ("qwertyuiop", "asdfghjklñ", "zxcvbnm", "1234567890", "qazwsxedcrfv", "1q2w3e4r5t")
_LEET = str.maketrans("013457@$!", "oleastasi")
_COSTE_PALABRA = 11.0  # bits que se cuentan por una palabra de las más comunes (≈ 1 entre 2000)
_BITS_LETRA_EN_TIRADA = 3.2  # una tirada de 5+ letras se parece a una palabra: se cuenta con prudencia
_TIRADA = 5
_BITS_DIGITO_EN_TIRADA = math.log2(10)  # unos dígitos seguidos (1990, 2024, 00) no son el alfabeto entero
_ANIO = re.compile(r"(?:19|20)\d\d")
_COMUNES = tuple(sorted(set("""
password pass passwd contrasena contraseña clave claves admin administrador administrator root user usuario guest
invitado qwerty qwertz azerty asdf asdfgh zxcvbn letmein welcome bienvenido bienvenida iloveyou tequiero teamo
monkey dragon master login secret secreto football futbol soccer baseball basketball hockey sunshine princess
shadow superman batman spiderman pokemon starwars matrix hello hola chao adios test testing prueba demo abc abcd
infinity ubuntu linux hyprland debian windows android iphone apple google gmail hotmail outlook yahoo facebook
instagram netflix amazon router wifi wlan internet casa home house familia family amor love dios jesus
juan jose maria carlos pedro luis miguel ana sofia daniel david javier pablo laura lucia santiago chile mexico
argentina colombia peru espana madrid barcelona messi ronaldo boca river verano invierno primavera otono
enero febrero marzo abril mayo junio julio agosto septiembre octubre noviembre diciembre lunes martes
miercoles jueves viernes sabado domingo cumple cumpleanos
123456 1234567 12345678 123456789 1234567890 654321 111111 000000 123123 121212 112233 1q2w3e 1q2w3e4r
""".split()), key=len, reverse=True))
_COMUNES = tuple(w for w in _COMUNES if len(w) >= 3)


def _tamano_alfabeto(p):
    tamano = 0
    if any(c.islower() and c.isascii() for c in p):
        tamano += 26
    if any(c.isupper() and c.isascii() for c in p):
        tamano += 26
    if any(c.isdigit() and c.isascii() for c in p):
        tamano += 10
    if any(c.isascii() and not c.isalnum() for c in p):
        tamano += 33
    if any(not c.isascii() for c in p):
        tamano += 64
    return max(tamano, 2)


def _marcas_de_patron(p):
    """Posiciones que forman parte de una repetición (aaa), una secuencia (abc, 321) o una fila del teclado (qwe)."""
    marcas = [False] * len(p)
    bajo = p.casefold()
    for i in range(len(p) - 2):
        d1, d2 = ord(bajo[i + 1]) - ord(bajo[i]), ord(bajo[i + 2]) - ord(bajo[i + 1])
        trio = bajo[i:i + 3]
        if (d1 == d2 and d1 in (-1, 0, 1)) or any(trio in fila or trio[::-1] in fila for fila in _FILAS_TECLADO):
            marcas[i] = marcas[i + 1] = marcas[i + 2] = True
    return marcas


def _tiradas_de_letras(p):
    """Posiciones que están dentro de una tirada de 5 o más letras ASCII seguidas (se parecen a palabras:
    un humano rara vez teclea 12 letras al azar). Es una heurística prudente, no un diccionario."""
    marcas = [False] * len(p)
    i = 0
    while i < len(p):
        j = i
        while j < len(p) and p[j].isascii() and p[j].isalpha():
            j += 1
        if j - i >= _TIRADA:
            marcas[i:j] = [True] * (j - i)
        i = max(j, i + 1)
    return marcas


def _tiradas_de_digitos(p):
    """Para cada posición: 0 si no está en una tirada de 2+ dígitos; 1 si lo está; 2 si la tirada es un año."""
    marcas = [0] * len(p)
    for m in re.finditer(r"[0-9]{2,}", p):
        valor = 2 if _ANIO.fullmatch(m.group()) else 1
        marcas[m.start():m.end()] = [valor] * (m.end() - m.start())
    return marcas


def _bits_estimados(p):
    if not p:
        return 0.0, ()
    avisos = []
    # Una contraseña que es un trozo repetido («abcabcabc») vale lo que el trozo
    for k in range(1, len(p) // 2 + 1):
        if len(p) % k == 0 and p == p[:k] * (len(p) // k):
            avisos.append("Repite el mismo trozo")
            bits, extra = _bits_estimados(p[:k])
            return bits + math.log2(len(p) // k) + 1, tuple(dict.fromkeys(extra + tuple(avisos)))
    bajo = p.casefold()
    claro = bajo.translate(_LEET)
    por_caracter = math.log2(_tamano_alfabeto(p))
    marcas = _marcas_de_patron(p)
    tiradas = _tiradas_de_letras(p)
    digitos = _tiradas_de_digitos(p)
    bits, i = 0.0, 0
    while i < len(p):
        palabra = next((w for w in _COMUNES if bajo.startswith(w, i) or claro.startswith(w, i)), None)
        if palabra:
            bits += _COSTE_PALABRA
            i += len(palabra)
            if "Lleva una palabra muy común" not in avisos:
                avisos.append("Lleva una palabra muy común")
            continue
        if marcas[i] and i > 0 and marcas[i - 1]:
            bits += 1.0
            if "Tiene repeticiones o secuencias" not in avisos:
                avisos.append("Tiene repeticiones o secuencias")
        elif digitos[i]:
            bits += _BITS_DIGITO_EN_TIRADA / (2 if digitos[i] == 2 else 1)  # un año ≈ 6,6 bits en total
        elif tiradas[i]:
            bits += min(por_caracter, _BITS_LETRA_EN_TIRADA)
        else:
            bits += por_caracter
        i += 1
    return bits, tuple(avisos)


def evaluar_fortaleza(password):
    """Estimación HONESTA de lo difícil que es adivinar `password` por fuerza bruta: longitud y variedad de
    caracteres, con penalización de repeticiones, secuencias, filas del teclado, palabras muy comunes
    (con «leet»: p4ssw0rd) y tiradas largas de letras. Sin dependencias ni diccionarios enormes: es una
    orientación, no una garantía (no reconoce una palabra poco común con sustituciones, p. ej.)."""
    normal = unicodedata.normalize("NFKC", password or "")
    bits, avisos = _bits_estimados(normal)
    puntos = sum(bits >= u for u in _UMBRALES)
    avisos = list(avisos)
    if normal and len(normal) < 12 and puntos >= 2 and not avisos:
        avisos.append("Mejor si tiene 12 caracteres o más")
    if normal and len(normal) < MIN_MAESTRA:
        puntos = min(puntos, 1)
    return Fortaleza(puntos, round(bits, 1), ETIQUETAS_FORTALEZA[puntos] if normal else "", tuple(avisos))


# --------------------------------------------------------------------------------- portapapeles temporal
class PortapapelesTemporal:
    """Copia texto al portapapeles y lo borra pasados `segundos`, pero SOLO si sigue conteniendo lo copiado
    (si el usuario copió otra cosa mientras tanto, no se toca). No guarda el texto: solo su huella SHA-256.

    `portapapeles` tiene poner(texto), leer(devolver) —llama a devolver(texto o None)—, borrar() y
    borrar_si_es_nuestro() (borra solo si el contenido sigue siendo el que pusimos nosotros);
    `programar(segundos, funcion)` devuelve un identificador y `cancelar(id)` lo anula. Todo viene de fuera
    para que esto se pruebe sin pantalla; la ventana pone las versiones de GTK."""

    def __init__(self, portapapeles, programar, cancelar, segundos=25, avisar=None):
        self._portapapeles = portapapeles
        self._programar, self._cancelar = programar, cancelar
        self.segundos = segundos
        self._avisar = avisar or (lambda estado: None)
        self._huella = None
        self._tid = None

    @staticmethod
    def _huella_de(texto):
        return hashlib.sha256(texto.encode("utf-8", "surrogatepass")).digest()

    @property
    def pendiente(self):
        return self._huella is not None

    def copiar(self, texto):
        self._anular()
        self._portapapeles.poner(texto)
        self._huella = self._huella_de(texto)
        self._tid = self._programar(self.segundos, self._vencio)
        self._avisar("copiado")

    def _anular(self):
        if self._tid is not None:
            self._cancelar(self._tid)
        self._tid, self._huella = None, None

    def _vencio(self):
        self._tid = None
        huella = self._huella
        if huella is None:
            return False
        self._portapapeles.leer(lambda actual: self._comparar(huella, actual))
        return False  # el temporizador de GLib no se repite

    def _comparar(self, huella, actual):
        if self._huella != huella:  # se copió otra cosa (o se bloqueó) mientras se leía
            return
        self._huella = None
        if actual is not None and hmac.compare_digest(self._huella_de(actual), huella):
            self._portapapeles.borrar()
            self._avisar("borrado")
        else:
            self._avisar("cambiado")

    def limpiar_ya(self):
        """Al bloquear o cerrar: si lo copiado sigue en el portapapeles, fuera."""
        huella = self._huella
        self._anular()
        if huella is not None:
            self._portapapeles.borrar_si_es_nuestro()


# ----------------------------------------------------------------------------------- endurecimiento
def endurecer_proceso():
    """Pequeñas defensas del proceso de la aplicación: sin volcados de memoria (core) y no «dumpable», para que
    otro proceso del mismo usuario no pueda leer su memoria con ptrace ni /proc/PID/mem. Devuelve qué se logró."""
    logrado = {"core": False, "dumpable": False}
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
        logrado["core"] = True
    except (ImportError, ValueError, OSError):
        pass
    try:
        import ctypes
        libc = ctypes.CDLL(None, use_errno=True)
        logrado["dumpable"] = libc.prctl(4, 0, 0, 0, 0) == 0  # PR_SET_DUMPABLE = 4
    except (ImportError, OSError, AttributeError):
        pass
    return logrado
