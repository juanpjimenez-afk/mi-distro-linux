#!/usr/bin/env python3
"""Entorno de juegos de Infinity OS: comprueba e instala lo necesario para jugar.

    comprobar()                    estado de Vulkan (paquetes y driver ICD) y de DXVK
    instalar_dependencias()        instala con apt lo que falta (pide sudo si hace falta)
    instalar_ge_proton(tag=None)   descarga GE-Proton desde GitHub, verifica su SHA-512 y lo
                                   extrae en ~/.steam/root/compatibilitytools.d/

Solo usa la biblioteca estándar. Las descargas se reanudan si se cortan, cada archivo se verifica
antes de usarlo y nada se instala a medias: la extracción va a una carpeta temporal que solo se
renombra cuando todo ha salido bien.

Los mensajes y el progreso van por un "informe": en la terminal (por defecto) o en la ventana
de Juegos (game_gui.py la sustituye con fijar_informe). Así el mismo código sirve para los dos.

Uso:
    python3 game_environment.py comprobar
    python3 game_environment.py instalar-dependencias [--simular]   (pide sudo para apt)
    python3 game_environment.py ge-proton [--version GE-Proton10-20] (como tu usuario, no root)

Nota: en Ubuntu no existe el paquete 'vulkan-driver'; el driver es 'mesa-vulkan-drivers'.
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import namedtuple

# Constantes. API_BASE se puede cambiar para probar con un servidor local.
API_BASE = "https://api.github.com"
GE_REPO = "GloriousEggroll/proton-ge-custom"
USER_AGENT = "infinity-os-game-environment"
PAQUETES_VULKAN = ("mesa-vulkan-drivers", "libvulkan1")
PAQUETE_DXVK = "dxvk"
DIRS_ICD = ("/usr/share/vulkan/icd.d", "/etc/vulkan/icd.d")
DIR_DXVK = "/usr/share/dxvk"
REINTENTOS = 4
ESPERA_BASE = 2  # segundos; se duplica en cada reintento
TIMEOUT = 30  # segundos sin datos antes de considerar la conexión caída
TAM_BLOQUE = 256 * 1024
MB = 1024 * 1024

Release = namedtuple("Release", "tag nombre url tamano url_sha512")
Estado = namedtuple("Estado", "nombre ok detalle")


class ErrorEntorno(Exception):
    """Un paso no se pudo completar. El mensaje está pensado para la persona usuaria."""


# --------------------------------------------------------------------------- informe
def _tiempo(segundos):
    if segundos is None or segundos != segundos or segundos == float("inf"):
        return "--:--"
    m, s = divmod(int(segundos), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


class Informe:
    """Salida por defecto: mensajes y barra de progreso en la terminal (stderr).

    Otro informe puede sustituirlo (ver fijar_informe): debe tener mensaje(texto),
    progreso(etiqueta, hecho, total, velocidad) y cerrar_progreso(). Si `total` es None, el
    progreso cuenta elementos (archivos) en vez de bytes.
    """

    def __init__(self):
        self._decil = {}

    @property
    def tty(self):
        """Se consulta al usar, no al importar el módulo (stderr puede no existir aún)."""
        try:
            return sys.stderr.isatty()
        except (AttributeError, ValueError):
            return False

    def mensaje(self, texto):
        # Resultados ([ok] y los listados de órdenes) a stdout, para poder encadenarlos en un script;
        # avisos y progreso a stderr.
        flujo = sys.stdout if texto.startswith(("[ok]", "  ")) else sys.stderr
        print(texto, file=flujo, flush=True)

    def progreso(self, etiqueta, hecho, total, velocidad):
        if total is None:
            if self.tty:
                sys.stderr.write(f"\r{etiqueta}: {hecho} archivos".ljust(100))
                sys.stderr.flush()
            return
        frac = min(hecho / total, 1.0)
        pct = int(frac * 100)
        if not self.tty:  # sin terminal: una línea cada 10 %, para que el registro sea legible
            decil = pct // 10
            if self._decil.get(etiqueta) != decil or frac >= 1.0:
                self._decil[etiqueta] = decil
                print(f"{etiqueta}: {pct} % ({hecho / MB:.1f}/{total / MB:.1f} MB)", file=sys.stderr, flush=True)
            return
        ancho = 30
        llenos = int(ancho * frac)
        barra = "#" * llenos + "-" * (ancho - llenos)
        eta = (total - hecho) / velocidad if velocidad > 0 else None
        sys.stderr.write(f"\r{etiqueta} [{barra}] {pct:3d} %  "
                         f"{hecho / MB:.1f}/{total / MB:.1f} MB  "
                         f"{velocidad / MB:.1f} MB/s  ETA {_tiempo(eta)}".ljust(110))
        sys.stderr.flush()

    def cerrar_progreso(self):
        if self.tty:
            sys.stderr.write("\r" + " " * 110 + "\r")
            sys.stderr.flush()


_informe = Informe()


def fijar_informe(informe):
    """Cambia dónde van mensajes y progreso. Con None vuelve a la terminal."""
    global _informe
    _informe = informe or Informe()


def _info(texto):
    _informe.mensaje(f"[..] {texto}")


class Barra:
    """Progreso de una operación larga. Reenvía al informe actual, con el ritmo de descarga."""

    def __init__(self, etiqueta, total=None, inicial=0):
        self.etiqueta = etiqueta
        self.total = total or None
        self.inicial = inicial
        self.hecho = inicial
        self.t0 = self.ultimo = time.monotonic()

    def avanza(self, n):
        self.hecho += n
        ahora = time.monotonic()
        completo = self.total is not None and self.hecho >= self.total
        if not completo and ahora - self.ultimo < 0.1:  # no avisar más de 10 veces por segundo
            return
        self.ultimo = ahora
        transcurrido = max(ahora - self.t0, 1e-6)
        velocidad = (self.hecho - self.inicial) / transcurrido  # bytes por segundo
        _informe.progreso(self.etiqueta, self.hecho, self.total, velocidad)

    def fin(self, texto):
        _informe.cerrar_progreso()
        _informe.mensaje(f"[ok] {texto}")


# --------------------------------------------------------------------------- utilidades
def _requiere_usuario():
    """GE-Proton es del usuario: como root se instalaría en /root y Steam no lo vería."""
    if os.geteuid() == 0:
        raise ErrorEntorno("no ejecutes esto con sudo: GE-Proton se instalaría en /root, no en tu usuario.")


def _como_root(argv):
    return argv if os.geteuid() == 0 else ["sudo", *argv]


def _leer_texto(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def _cache_dir():
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "infinity", "game-environment")


def directorio_compatibilidad():
    """Donde Steam busca las versiones de Proton de terceros (GE-Proton)."""
    return os.path.join(os.path.expanduser("~"), ".steam", "root", "compatibilitytools.d")


def _steam_presente():
    casa = os.path.expanduser("~")
    return any(os.path.exists(os.path.join(casa, r))
               for r in (".steam/steam.pipe", ".steam/root", ".local/share/Steam"))


# --------------------------------------------------------------------------- comprobación
def _paquete_instalado(nombre):
    """True/False según dpkg. None si no se puede saber (no hay dpkg-query)."""
    try:
        r = subprocess.run(["dpkg-query", "-W", "-f=${Status}", nombre],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.returncode == 0 and "install ok installed" in r.stdout


def _icd_instalados():
    """Nombres de los drivers Vulkan (ICD) que hay en el sistema."""
    rutas = []
    for d in DIRS_ICD:
        rutas += glob.glob(os.path.join(d, "*.json"))
    return sorted(os.path.basename(r) for r in rutas)


def comprobar():
    """Estado de Vulkan y DXVK. Lo que falta no es un error: se devuelve para informar."""
    faltan = [p for p in PAQUETES_VULKAN if not _paquete_instalado(p)]
    icd = _icd_instalados()
    if faltan:
        vulkan = Estado("Vulkan", False, f"faltan paquetes: {', '.join(faltan)}")
    elif not icd:
        vulkan = Estado("Vulkan", False, "paquetes instalados, pero no hay driver (ICD) en /usr/share/vulkan/icd.d")
    else:
        vulkan = Estado("Vulkan", True, f"driver: {', '.join(icd)}")

    dxvk_pkg = _paquete_instalado(PAQUETE_DXVK)
    if dxvk_pkg or os.path.isdir(DIR_DXVK):
        dxvk = Estado("DXVK", True, "instalado")
    elif dxvk_pkg is None:
        dxvk = Estado("DXVK", False, "no se puede comprobar (falta dpkg-query)")
    else:
        dxvk = Estado("DXVK", False, "no instalado. Proton ya trae su propio DXVK; esto solo hace falta "
                                     "para prefijos de Wine sueltos")
    return [vulkan, dxvk]


def instalar_dependencias(simular=False):
    """Instala con apt los paquetes que falten (Vulkan y DXVK). Devuelve la lista instalada."""
    estado = {p: _paquete_instalado(p) for p in (*PAQUETES_VULKAN, PAQUETE_DXVK)}
    faltan = [p for p, ok in estado.items() if not ok]
    if not faltan:
        _info("Vulkan y DXVK ya están instalados: nada que hacer.")
        return []

    actualizar = _como_root(["apt-get", "update"])
    instalar = _como_root(["apt-get", "install", "-y", "--no-install-recommends", *faltan])
    if simular:
        _info("Modo simulación. Se ejecutaría:")
        _informe.mensaje("  " + " ".join(actualizar))
        _informe.mensaje("  " + " ".join(instalar))
        return faltan

    _info(f"Paquetes a instalar: {', '.join(faltan)}")
    entorno = {**os.environ, "DEBIAN_FRONTEND": "noninteractive"}
    for orden in (actualizar, instalar):
        _info(f"Ejecutando: {' '.join(orden)}")
        try:
            r = subprocess.run(orden, env=entorno)  # sudo pide contraseña en esta misma terminal
        except OSError as e:
            raise ErrorEntorno(f"no se pudo ejecutar '{orden[0]}': {e}")
        if r.returncode != 0:
            raise ErrorEntorno(f"'{' '.join(orden)}' terminó con código {r.returncode}.")
    _informe.mensaje(f"[ok] Instalado: {', '.join(faltan)}")
    return faltan


# --------------------------------------------------------------------------- red
def _peticion_json(url):
    """GET a la API de GitHub con reintentos. Un 404 o un límite de peticiones se informa sin reintentar."""
    cabeceras = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")  # opcional: sube el límite de 60 peticiones por hora
    if token:
        cabeceras["Authorization"] = f"Bearer {token}"
    motivo = "sin detalle"
    for intento in range(1, REINTENTOS + 1):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=cabeceras), timeout=TIMEOUT) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise ErrorEntorno("GitHub no tiene esa versión de GE-Proton (404). Revisa el nombre.")
            if e.code in (403, 429):
                raise ErrorEntorno(f"GitHub rechaza la petición (HTTP {e.code}): límite de uso o acceso denegado. "
                                   "Espera un rato o define GITHUB_TOKEN.")
            motivo = f"HTTP {e.code}"
        except (OSError, ValueError) as e:  # URLError, timeouts, cortes y JSON roto
            motivo = str(getattr(e, "reason", e))
        if intento < REINTENTOS:
            espera = ESPERA_BASE * 2 ** (intento - 1)
            _info(f"GitHub no respondió ({motivo}). Reintento {intento + 1}/{REINTENTOS} en {espera} s...")
            time.sleep(espera)
    raise ErrorEntorno(f"no se pudo hablar con GitHub tras {REINTENTOS} intentos ({motivo}).")


def _descargar(url, destino, tamano, etiqueta):
    """Descarga `url` a `destino`. Si se corta, reanuda desde lo ya bajado (fichero .part)."""
    parcial = destino + ".part"
    motivo = "sin detalle"
    completo = False
    for intento in range(1, REINTENTOS + 1):
        hecho = os.path.getsize(parcial) if os.path.exists(parcial) else 0
        if tamano and hecho == tamano:
            completo = True
            break
        cabeceras = {"User-Agent": USER_AGENT}
        if hecho:
            cabeceras["Range"] = f"bytes={hecho}-"
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=cabeceras), timeout=TIMEOUT) as r:
                if hecho and r.status != 206:
                    hecho = 0  # el servidor no admite reanudar: se empieza de cero
                total = tamano or ((hecho + int(r.headers.get("Content-Length") or 0)) or None)
                barra = Barra(etiqueta, total, inicial=hecho)
                with open(parcial, "ab" if hecho else "wb") as f:
                    while bloque := r.read(TAM_BLOQUE):
                        f.write(bloque)
                        barra.avanza(len(bloque))
            if tamano and os.path.getsize(parcial) != tamano:
                motivo = "la descarga llegó incompleta"
            else:
                barra.fin(f"{etiqueta}: descargado")
                completo = True
                break
        except urllib.error.HTTPError as e:
            if e.code == 416:  # rango no válido: el parcial no sirve, se reinicia
                if os.path.exists(parcial):
                    os.remove(parcial)
                motivo = "el parcial no era válido; se reinicia"
            elif e.code == 404:
                raise ErrorEntorno(f"el archivo ya no existe en GitHub (404): {url}")
            else:
                motivo = f"HTTP {e.code}"
        except OSError as e:  # URLError, timeouts y cortes de conexión
            motivo = str(getattr(e, "reason", e))
        if intento < REINTENTOS:
            espera = ESPERA_BASE * 2 ** (intento - 1)
            _info(f"{etiqueta}: fallo ({motivo}). Reintento {intento + 1}/{REINTENTOS} en {espera} s...")
            time.sleep(espera)
    if not completo:
        raise ErrorEntorno(f"no se pudo descargar tras {REINTENTOS} intentos ({motivo}). "
                           "Vuelve a ejecutarlo: retoma desde donde se quedó.")
    os.replace(parcial, destino)


# --------------------------------------------------------------------------- verificación
def _sha512(ruta, etiqueta):
    h = hashlib.sha512()
    barra = Barra(etiqueta, os.path.getsize(ruta))
    with open(ruta, "rb") as f:
        while bloque := f.read(TAM_BLOQUE):
            h.update(bloque)
            barra.avanza(len(bloque))
    barra.fin(f"{etiqueta}: correcto")
    return h.hexdigest()


def _leer_sha512(texto):
    """Extrae el SHA-512 de un .sha512sum ('<hash>  <archivo>')."""
    for linea in texto.splitlines():
        partes = linea.split()
        if partes and re.fullmatch(r"[0-9a-fA-F]{128}", partes[0]):
            return partes[0].lower()
    raise ErrorEntorno("el fichero de checksum no contiene un SHA-512 válido.")


# --------------------------------------------------------------------------- GE-Proton
def obtener_release(tag=None):
    """Datos de la última versión de GE-Proton (o de `tag`), con su .tar.gz y su .sha512sum."""
    ruta = f"releases/tags/{urllib.parse.quote(tag, safe='')}" if tag else "releases/latest"
    datos = _peticion_json(f"{API_BASE}/repos/{GE_REPO}/{ruta}")
    assets = datos.get("assets")
    if not isinstance(assets, list):
        raise ErrorEntorno("respuesta inesperada de GitHub (sin lista de archivos).")
    por_nombre = {a["name"]: a for a in assets}
    tarballs = [n for n in por_nombre if n.startswith("GE-Proton") and n.endswith(".tar.gz")]
    if len(tarballs) != 1:
        raise ErrorEntorno(f"la versión {datos.get('tag_name')} no tiene un único GE-Proton .tar.gz.")
    nombre = tarballs[0]
    sha = nombre[: -len(".tar.gz")] + ".sha512sum"
    if sha not in por_nombre:
        # Sin checksum no podemos saber si el archivo es el publicado: no se instala.
        raise ErrorEntorno(f"la versión {datos.get('tag_name')} no trae su {sha}; no se puede verificar.")
    return Release(
        tag=datos.get("tag_name", tag or "?"),
        nombre=nombre,
        url=por_nombre[nombre]["browser_download_url"],
        tamano=por_nombre[nombre].get("size"),
        url_sha512=por_nombre[sha]["browser_download_url"],
    )


def _extraer(tarball, destino, etiqueta):
    """Extrae el .tar.gz con el filtro 'data' de tarfile: rechaza rutas absolutas y enlaces fuera."""
    os.makedirs(destino, exist_ok=True)
    barra = Barra(etiqueta)  # sin total: cuenta archivos
    try:
        with tarfile.open(tarball, "r:gz") as tar:
            def miembros():
                for m in tar:
                    barra.avanza(1)
                    yield m
            tar.extractall(destino, members=miembros(), filter="data")
    except tarfile.FilterError as e:
        raise ErrorEntorno(f"el archivo contiene una ruta no segura ({e}); no se extrae.")
    except (tarfile.TarError, EOFError, OSError) as e:
        raise ErrorEntorno(f"no se pudo extraer el archivo: {e}")
    barra.fin(f"{etiqueta}: {barra.hecho} archivo{'s' if barra.hecho != 1 else ''}")


def instalar_ge_proton(tag=None):
    """Descarga, verifica y extrae GE-Proton en la carpeta de Steam. Devuelve la ruta instalada.

    Si ya está instalada, no vuelve a descargar. La descarga queda en la caché y se reutiliza
    si vuelves a ejecutar tras un fallo de extracción.
    """
    _requiere_usuario()
    rel = obtener_release(tag)
    carpeta = rel.nombre[: -len(".tar.gz")]
    compat = directorio_compatibilidad()
    destino = os.path.join(compat, carpeta)
    if os.path.isdir(destino):
        _informe.mensaje(f"[ok] {carpeta} ya está instalada en {destino}")
        return destino

    if not _steam_presente():
        _info("Steam no parece instalado; la carpeta se crea igualmente.")
    os.makedirs(compat, exist_ok=True)
    cache = _cache_dir()
    os.makedirs(cache, exist_ok=True)
    _info(f"Versión {rel.tag} ({(rel.tamano or 0) / MB:.0f} MB)")

    sha_ruta = os.path.join(cache, carpeta + ".sha512sum")
    _descargar(rel.url_sha512, sha_ruta, None, f"Checksum {carpeta}")
    esperado = _leer_sha512(_leer_texto(sha_ruta))

    tarball = os.path.join(cache, rel.nombre)
    if os.path.exists(tarball) and _sha512(tarball, "Comprobando descarga anterior") == esperado:
        _info("La descarga anterior está completa y es válida; se reutiliza.")
    else:
        _descargar(rel.url, tarball, rel.tamano, f"Descargando {rel.nombre}")
        if _sha512(tarball, "Verificando integridad") != esperado:
            os.remove(tarball)
            raise ErrorEntorno("el SHA-512 no coincide con el publicado: archivo corrupto o alterado. "
                               "Se ha borrado; vuelve a ejecutarlo.")

    tmp = os.path.join(compat, f".instalando-{carpeta}")
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        _extraer(tarball, tmp, f"Extrayendo {carpeta}")
        contenido = os.listdir(tmp)
        if len(contenido) != 1 or not os.path.isdir(os.path.join(tmp, contenido[0])):
            raise ErrorEntorno("el archivo no tiene la estructura esperada de GE-Proton.")
        os.replace(os.path.join(tmp, contenido[0]), destino)  # renombrar: todo o nada
    except OSError as e:
        raise ErrorEntorno(f"no se pudo instalar en {destino}: {e}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    os.remove(tarball)
    os.remove(sha_ruta)
    _informe.mensaje(f"[ok] {carpeta} instalada en {destino}")
    _info("Reinicia Steam para que aparezca en Propiedades → Compatibilidad.")
    return destino


# --------------------------------------------------------------------------- línea de órdenes
def main(argv=None):
    parser = argparse.ArgumentParser(description="Entorno de juegos de Infinity OS.")
    sub = parser.add_subparsers(dest="orden", required=True)
    sub.add_parser("comprobar", help="estado de Vulkan y DXVK")
    dep = sub.add_parser("instalar-dependencias", help="instalar con apt lo que falte")
    dep.add_argument("--simular", action="store_true", help="mostrar los comandos sin ejecutarlos")
    ge = sub.add_parser("ge-proton", help="descargar e instalar GE-Proton")
    ge.add_argument("--version", metavar="TAG", help="p. ej. GE-Proton10-20 (por defecto, la última)")
    args = parser.parse_args(argv)

    try:
        if args.orden == "comprobar":
            for e in comprobar():
                print(f"[{'ok' if e.ok else '!!'}] {e.nombre}: {e.detalle}")
        elif args.orden == "instalar-dependencias":
            instalar_dependencias(simular=args.simular)
        elif args.orden == "ge-proton":
            instalar_ge_proton(args.version)
    except ErrorEntorno as e:
        print(f"[error] {e}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\n[..] Cancelado. La descarga se puede retomar volviendo a ejecutar el comando.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
