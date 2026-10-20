"""Carpetas de la biblioteca de apps y qué apps se ven. Sin GTK, así se prueba aparte.

La biblioteca guarda solo IDs de apps (.desktop) en ~/.config/infinity/biblioteca.json:

    {"solo_descargadas": true,
     "carpetas": {"c1a2b3": {"nombre": "Juegos", "apps": ["steam.desktop", "lutris.desktop"]}}}

Las carpetas no contienen ficheros: son solo agrupaciones de apps. Una app está en una carpeta
como mucho. Si una carpeta se queda vacía, desaparece.
"""
import json
import os
import shutil
import time
import uuid
from collections import namedtuple

# .desktop que vienen con el sistema. Lo que esté en otra ruta (~/.local, flatpak, snap…) se
# considera descargado/instalado por el usuario.
SISTEMA = ("/usr/share/applications/", "/usr/local/share/applications/")

# La ISO guarda aquí (build.sh) los .desktop que trae de fábrica, uno por línea. Con esa lista, «del sistema» es lo que
# trae la imagen, no lo que está en /usr/share/applications: lo que instalan apt o gdebi (Chrome, Antigravity, un .deb
# descargado…) también cae ahí y es «descargado». Sin la lista (sistema de desarrollo o ISO antigua) vale la regla por ruta.
MANIFIESTO_BASE = "/usr/share/infinity/apps-base.txt"
_AUTO = object()

Carpeta = namedtuple("Carpeta", "cid nombre apps")


def leer_manifiesto(ruta=MANIFIESTO_BASE):
    """Los nombres de .desktop que trae la imagen (frozenset), o None si no hay manifiesto o no se puede leer."""
    try:
        with open(ruta, encoding="utf-8") as f:
            lineas = f.read().splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    return frozenset(l.strip() for l in lineas if l.strip() and not l.lstrip().startswith("#"))


def es_descargada(ruta, base=None):
    """True si el .desktop no es del sistema. Sin ruta (desconocida) cuenta como descargada.

    `base` es el manifiesto de la imagen (leer_manifiesto): con él, un .desktop de las carpetas del sistema solo es «del
    sistema» si su nombre está en la lista. Sin él, basta con la ruta.
    """
    if not ruta:
        return True
    if not ruta.startswith(SISTEMA):
        return True
    return base is not None and os.path.basename(ruta) not in base


def ruta_biblioteca():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "infinity", "biblioteca.json")


class Biblioteca:
    def __init__(self, ruta=None, base=_AUTO):
        self.ruta = ruta or ruta_biblioteca()
        self.base = leer_manifiesto() if base is _AUTO else base   # None: sin manifiesto, regla antigua por ruta
        self.solo_descargadas = True
        self.carpetas = {}  # cid -> {"nombre": str, "apps": [app_id, ...]}
        self.ilegible = False  # el fichero existía pero no se pudo leer: no se sobrescribe sin copia

    # ------------------------------------------------------------- persistencia
    @classmethod
    def cargar(cls, ruta=None, base=_AUTO):
        b = cls(ruta, base)
        if not os.path.exists(b.ruta):
            return b
        try:
            with open(b.ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            b.ilegible = True
            return b
        if not isinstance(datos, dict):
            b.ilegible = True
            return b
        b.solo_descargadas = datos.get("solo_descargadas") is not False
        carpetas = datos.get("carpetas")
        if carpetas is None:
            return b
        if not isinstance(carpetas, dict):  # lista, número…: se empieza sin carpetas
            b.ilegible = True
            return b
        ya_usadas = set()  # una app solo puede estar en una carpeta: gana la primera
        for cid, c in carpetas.items():
            if not isinstance(c, dict) or not isinstance(c.get("nombre"), str):
                continue
            apps = c.get("apps")
            apps = [a for a in apps if isinstance(a, str)] if isinstance(apps, list) else []
            apps = [a for a in dict.fromkeys(apps) if a not in ya_usadas]
            ya_usadas.update(apps)
            if not apps:
                continue
            nombre = c["nombre"].strip()[:40] or "Carpeta"
            b.carpetas[str(cid)] = {"nombre": nombre, "apps": apps}
        return b

    def guardar(self):
        datos = {"solo_descargadas": self.solo_descargadas, "carpetas": self.carpetas}
        try:
            os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
            if self.ilegible and os.path.exists(self.ruta):
                # Un fichero que no se pudo leer (corrupto o editado a mano) no se pierde:
                # se guarda una copia antes de escribir el nuevo.
                copia = f"{self.ruta}.roto-{time.strftime('%Y%m%d-%H%M%S')}"
                shutil.copy2(self.ruta, copia)
                print(f"infinity-shell: biblioteca.json no se pudo leer; copia en {copia}")
                self.ilegible = False
            tmp = self.ruta + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(datos, f, ensure_ascii=False, indent=1)
            os.replace(tmp, self.ruta)
            return True
        except OSError as e:
            print(f"infinity-shell: no se pudo guardar {self.ruta}: {e}")
            return False

    # ----------------------------------------------------------------- cambios
    def carpeta_de(self, app_id):
        for cid, c in self.carpetas.items():
            if app_id in c["apps"]:
                return cid
        return None

    def _quitar(self, app_id):
        cid = self.carpeta_de(app_id)
        if cid is None:
            return
        self.carpetas[cid]["apps"].remove(app_id)
        if not self.carpetas[cid]["apps"]:
            del self.carpetas[cid]

    def agregar(self, app_id, cid):
        """Mete la app en la carpeta (la saca de la anterior)."""
        if cid not in self.carpetas or self.carpeta_de(app_id) == cid:
            return
        self._quitar(app_id)
        self.carpetas[cid]["apps"].append(app_id)

    def soltar(self, origen, destino):
        """Arrastrar `origen` sobre la app `destino`: se une a la carpeta de `destino` o crea una."""
        if origen == destino:
            return None
        cid = self.carpeta_de(destino)
        if cid is not None:
            self.agregar(origen, cid)
            return cid
        self._quitar(origen)
        cid = uuid.uuid4().hex[:8]
        while cid in self.carpetas:
            cid = uuid.uuid4().hex[:8]
        self.carpetas[cid] = {"nombre": "Carpeta nueva", "apps": [destino, origen]}
        return cid

    def sacar(self, app_id):
        self._quitar(app_id)

    def renombrar(self, cid, nombre):
        nombre = (nombre or "").strip()
        if cid in self.carpetas and nombre:
            self.carpetas[cid]["nombre"] = nombre[:40]

    def eliminar(self, cid):
        """Borra la carpeta; sus apps vuelven a la vista principal."""
        self.carpetas.pop(cid, None)

    # ------------------------------------------------------------------- vista
    def visibles(self, apps):
        """Apps que se muestran con el filtro actual (las de carpeta cuentan igual). Una app que lo pide en su
        .desktop (X-Infinity-Biblioteca=siempre: la Tienda) se ve aunque sea del sistema."""
        return [a for a in apps
                if not self.solo_descargadas or es_descargada(a.ruta, self.base) or getattr(a, "siempre", False)]

    def vista(self, apps):
        """Lista de primer nivel: primero las carpetas, luego las apps sueltas por nombre.

        `apps` son objetos con .id, .nombre y .ruta (como apps.App). Los IDs de carpeta que ya
        no existen (app desinstalada) se ignoran sin tocar el fichero.
        """
        visibles = self.visibles(apps)
        por_id = {a.id: a for a in visibles}
        en_carpeta = set()
        items = []
        for cid, c in self.carpetas.items():
            miembros = [por_id[i] for i in c["apps"] if i in por_id]
            en_carpeta.update(c["apps"])
            if miembros:
                items.append(Carpeta(cid, c["nombre"], miembros))
        items.sort(key=lambda k: k.nombre.casefold())
        sueltas = sorted((a for a in visibles if a.id not in en_carpeta), key=lambda a: a.nombre.casefold())
        return items + sueltas
