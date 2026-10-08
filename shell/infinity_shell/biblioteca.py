"""Carpetas de la biblioteca de apps y qué apps se ven. Sin GTK, así se prueba aparte.

La biblioteca guarda solo IDs de apps (.desktop) en ~/.config/infinity/biblioteca.json:

    {"solo_descargadas": true,
     "carpetas": {"c1a2b3": {"nombre": "Juegos", "apps": ["steam.desktop", "lutris.desktop"]}}}

Las carpetas no contienen ficheros: son solo agrupaciones de apps. Una app está en una carpeta
como mucho. Si una carpeta se queda vacía, desaparece.
"""
import json
import os
import uuid
from collections import namedtuple

# .desktop que vienen con el sistema. Lo que esté en otra ruta (~/.local, flatpak, snap…) se
# considera descargado/instalado por el usuario.
SISTEMA = ("/usr/share/applications/", "/usr/local/share/applications/")

Carpeta = namedtuple("Carpeta", "cid nombre apps")


def es_descargada(ruta):
    """True si el .desktop no es del sistema. Sin ruta (desconocida) cuenta como descargada."""
    if not ruta:
        return True
    return not ruta.startswith(SISTEMA)


def ruta_biblioteca():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return os.path.join(base, "infinity", "biblioteca.json")


class Biblioteca:
    def __init__(self, ruta=None):
        self.ruta = ruta or ruta_biblioteca()
        self.solo_descargadas = True
        self.carpetas = {}  # cid -> {"nombre": str, "apps": [app_id, ...]}

    # ------------------------------------------------------------- persistencia
    @classmethod
    def cargar(cls, ruta=None):
        b = cls(ruta)
        try:
            with open(b.ruta, encoding="utf-8") as f:
                datos = json.load(f)
        except (OSError, ValueError):
            return b
        if not isinstance(datos, dict):
            return b
        b.solo_descargadas = bool(datos.get("solo_descargadas", True))
        for cid, c in (datos.get("carpetas") or {}).items():
            if isinstance(c, dict) and isinstance(c.get("nombre"), str):
                apps = [a for a in c.get("apps") or [] if isinstance(a, str)]
                b.carpetas[str(cid)] = {"nombre": c["nombre"], "apps": list(dict.fromkeys(apps))}
        return b

    def guardar(self):
        datos = {"solo_descargadas": self.solo_descargadas, "carpetas": self.carpetas}
        try:
            os.makedirs(os.path.dirname(self.ruta), exist_ok=True)
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
        """Apps que se muestran con el filtro actual (las de carpeta cuentan igual)."""
        return [a for a in apps if not self.solo_descargadas or es_descargada(a.ruta)]

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
