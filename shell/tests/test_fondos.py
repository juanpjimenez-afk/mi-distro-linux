"""Pruebas de los fondos de pantalla: shell/infinity_shell/fondos.py y los scripts que eligen la imagen
(overlay/usr/libexec/infinity/fondo-ruta, fondo-modo, overlay/usr/bin/infinity-wallpaper e infinity-lock).

Todo usa carpetas temporales: nada toca /usr/share ni el HOME real. Las pruebas que decodifican imágenes
necesitan GdkPixbuf (se saltan sin él; con el Python del sistema y sin PyGObject no hay importación).
Las imágenes se generan aquí mismo: no dependen del catálogo de branding/fondos.
"""
import json
import os
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import unittest
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import fondos as F  # noqa: E402

LIBEXEC = os.path.join(ROOT, "overlay", "usr", "libexec", "infinity")
FONDO_RUTA = os.path.join(LIBEXEC, "fondo-ruta")
FONDO_MODO = os.path.join(LIBEXEC, "fondo-modo")
WALLPAPER = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-wallpaper")
LOCK = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-lock")

try:
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    TIENE_PIXBUF = True
except (ImportError, ValueError):
    TIENE_PIXBUF = False

JPEG = b"\xff\xd8\xff\xe0" + b"contenido de prueba " * 8   # empieza como un JPEG: para fondo-ruta basta eso


def lineas(ruta):
    with open(ruta, encoding="utf-8") as f:
        return [l.strip() for l in f]


def escribe(ruta, datos=JPEG):
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta, "wb") as f:
        f.write(datos)
    return ruta


class Entorno(unittest.TestCase):
    """Un «sistema» y un «usuario» de mentira en una carpeta temporal."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="fondos-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.share = os.path.join(self.tmp, "share")           # hará de /usr/share/infinity
        self.config = os.path.join(self.tmp, "config")         # XDG_CONFIG_HOME
        self.datos = os.path.join(self.tmp, "datos")           # XDG_DATA_HOME
        self.home = os.path.join(self.tmp, "home")
        for d in (self.share, self.config, self.datos, self.home):
            os.makedirs(d)
        self.fondos = os.path.join(self.share, "fondos")
        self.usuario = os.path.join(self.datos, "infinity", "fondos")
        self.rutas = F.Rutas(sistema=(self.fondos,), usuario=self.usuario,
                             legado_escritorio=(os.path.join(self.share, "wallpaper.jpg"),),
                             legado_bloqueo=(os.path.join(self.share, "lock", "fondos"),))

    # --- construir el catálogo del sistema
    def catalogo(self, ids=("cascada", "islandia", "lago"), resoluciones=("1920x1080", "1280x720"), manifiesto=True):
        entradas = []
        for i in ids:
            escribe(os.path.join(self.fondos, i, "escritorio.jpg"), JPEG + i.encode())
            escribe(os.path.join(self.fondos, i, "miniatura.jpg"))
            bloqueo = {}
            for r in resoluciones:
                escribe(os.path.join(self.fondos, i, f"bloqueo-{r}.jpg"), JPEG + i.encode() + r.encode())
                bloqueo[r] = f"{i}/bloqueo-{r}.jpg"
            entradas.append({"id": i, "nombre": i.capitalize(), "escritorio": f"{i}/escritorio.jpg",
                             "miniatura": f"{i}/miniatura.jpg", "bloqueo": bloqueo})
        if manifiesto:
            with open(os.path.join(self.fondos, "fondos.json"), "w", encoding="utf-8") as f:
                json.dump({"version": 1, "fondos": entradas}, f)

    def legado(self, escritorio=True, resoluciones=("1920x1080", "1280x720")):
        if escritorio:
            escribe(os.path.join(self.share, "wallpaper.jpg"), JPEG + b"legado")
        for r in resoluciones:
            escribe(os.path.join(self.share, "lock", "fondos", f"wallpaper-lock-{r}.jpg"), JPEG + b"legado" + r.encode())

    def fondo_usuario(self, id_="u-mio", nombre="Mío", resoluciones=("1920x1080",), escritorio=True):
        d = os.path.join(self.usuario, id_)
        if escritorio:
            escribe(os.path.join(d, "escritorio.jpg"), JPEG + b"mio")
        escribe(os.path.join(d, "miniatura.jpg"))
        for r in resoluciones:
            escribe(os.path.join(d, f"bloqueo-{r}.jpg"), JPEG + b"mio" + r.encode())
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "nombre": nombre, "creado": 1}, f)
        return d

    def ajuste(self, nombre, texto):
        escribe(os.path.join(self.config, "infinity", nombre), texto.encode() if isinstance(texto, str) else texto)

    def entorno(self, **extra):
        e = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": self.home, "XDG_CONFIG_HOME": self.config,
             "XDG_DATA_HOME": self.datos, "INFINITY_SHARE": self.share, "INFINITY_LIBEXEC": LIBEXEC}
        e.update(extra)
        return e


# ======================================================================== ids y nombres
class TestIds(unittest.TestCase):
    def test_ids_validos(self):
        for i in ("cascada", "atardecer-playa", "u-mi-foto", "a", "7-colores", "u-" + "a" * 20):
            self.assertTrue(F.id_valido(i), i)

    def test_ids_invalidos(self):
        for i in ("", "..", ".", "../x", "a/b", "A", "Cascada", "ñu", "-x", "x-", "a b", "a\n", "a" * 41, None, 3,
                  ["a"], "a\x00", "u-../x", "a.b", "%2e%2e"):
            self.assertFalse(F.id_valido(i), repr(i))

    def test_ids_de_usuario(self):
        self.assertTrue(F.es_de_usuario("u-foto"))
        self.assertFalse(F.es_de_usuario("cascada"))
        self.assertFalse(F.es_de_usuario("u-"))
        self.assertFalse(F.es_de_usuario("u-../etc"))

    def test_limpiar_nombre(self):
        self.assertEqual(F.limpiar_nombre("Mi foto"), "Mi foto")
        self.assertEqual(F.limpiar_nombre("../../etc/passwd"), "etc passwd")
        self.assertEqual(F.limpiar_nombre("a\nb\x00c\x1b[31m"), "a b c 31m")
        self.assertEqual(F.limpiar_nombre("<b>x</b> & y"), "b x b y")
        self.assertEqual(F.limpiar_nombre("   "), "Imagen")
        self.assertEqual(F.limpiar_nombre("///"), "Imagen")
        self.assertEqual(F.limpiar_nombre(None), "Imagen")
        self.assertEqual(F.limpiar_nombre("Ñandú café"), "Ñandú café")
        self.assertEqual(len(F.limpiar_nombre("x" * 500)), 40)

    def test_crear_id_siempre_valido_y_sin_choques(self):
        for nombre in ("Mi foto", "../../x", "Ñandú", "###", "x" * 200, "‮ evil"):
            i = F._crear_id(nombre, set())
            self.assertTrue(F.id_valido(i), (nombre, i))
            self.assertTrue(i.startswith("u-"))
        self.assertEqual(F._crear_id("Foto", {"u-foto"}), "u-foto-2")
        self.assertEqual(F._crear_id("Foto", {"u-foto", "u-foto-2"}), "u-foto-3")
        self.assertTrue(F.id_valido(F._crear_id("x" * 200, {F._crear_id("x" * 200, set())})))


# ======================================================================== catálogo
class TestCatalogo(Entorno):
    def test_lee_el_catalogo_del_sistema(self):
        self.catalogo()
        lista = F.catalogo_sistema(self.rutas)
        self.assertEqual([f.id for f in lista], ["cascada", "islandia", "lago"])
        f = lista[1]
        self.assertEqual((f.nombre, f.origen), ("Islandia", "sistema"))
        self.assertTrue(f.escritorio.endswith("islandia/escritorio.jpg"))
        self.assertEqual(sorted(f.bloqueos), [(1280, 720), (1920, 1080)])
        self.assertTrue(os.path.isfile(f.miniatura))

    def test_sin_catalogo_queda_el_respaldo_antiguo(self):
        self.legado()
        lista = F.catalogo_sistema(self.rutas)
        self.assertEqual([f.id for f in lista], ["cascada", "islandia"])
        self.assertTrue(lista[0].escritorio.endswith("wallpaper.jpg"))
        self.assertEqual(lista[0].bloqueos, {})
        self.assertEqual(sorted(lista[1].bloqueos), [(1280, 720), (1920, 1080)])
        self.assertTrue(lista[1].miniatura)   # sin miniatura propia: la menor imagen de bloqueo

    def test_sin_nada_no_falla(self):
        self.assertEqual(F.listar(self.rutas), [])
        self.assertIsNone(F.buscar("cascada", self.rutas))

    def test_manifiesto_roto_o_enorme_se_ignora(self):
        self.legado()
        os.makedirs(self.fondos)
        for texto in (b"no es json", b"[]", b'{"fondos": 3}', b"\xff\xfe", b""):
            escribe(os.path.join(self.fondos, "fondos.json"), texto)
            self.assertEqual([f.id for f in F.catalogo_sistema(self.rutas)], ["cascada", "islandia"], texto)
        escribe(os.path.join(self.fondos, "fondos.json"), b'{"fondos": [' + b" " * (2 * 1024 * 1024) + b"]}")
        self.assertEqual([f.id for f in F.catalogo_sistema(self.rutas)], ["cascada", "islandia"])

    def test_entradas_peligrosas_se_descartan(self):
        self.catalogo(ids=("bueno",))
        escribe(os.path.join(self.tmp, "fuera.jpg"))
        with open(os.path.join(self.fondos, "fondos.json"), encoding="utf-8") as f:
            man = json.load(f)
        bueno = man["fondos"][0]
        malas = [
            {"id": "../x", "escritorio": "bueno/escritorio.jpg"},
            {"id": "u-colado", "escritorio": "bueno/escritorio.jpg"},      # los ids del sistema no son de usuario
            {"id": "MAYUS", "escritorio": "bueno/escritorio.jpg"},
            {"id": "salida", "escritorio": "../fuera.jpg", "bloqueo": {"1920x1080": "../fuera.jpg"}},
            {"id": "absoluta", "escritorio": os.path.join(self.tmp, "fuera.jpg")},
            {"id": "tipos", "escritorio": 3, "bloqueo": [1, 2]},
            {"id": "inexistente", "escritorio": "inexistente/escritorio.jpg"},
            "texto", 7, None,
            {"id": "bueno", "escritorio": "bueno/escritorio.jpg"},              # repetido
        ]
        man["fondos"] = [bueno] + malas
        with open(os.path.join(self.fondos, "fondos.json"), "w", encoding="utf-8") as f:
            json.dump(man, f)
        self.assertEqual([f.id for f in F.catalogo_sistema(self.rutas)], ["bueno"])

    def test_resolucion_mal_escrita_se_ignora(self):
        self.catalogo(ids=("a",))
        with open(os.path.join(self.fondos, "fondos.json"), encoding="utf-8") as f:
            man = json.load(f)
        man["fondos"][0]["bloqueo"]["../../x"] = "a/bloqueo-1920x1080.jpg"
        man["fondos"][0]["bloqueo"]["99999999x1"] = "a/bloqueo-1920x1080.jpg"
        with open(os.path.join(self.fondos, "fondos.json"), "w", encoding="utf-8") as f:
            json.dump(man, f)
        self.assertEqual(sorted(F.catalogo_sistema(self.rutas)[0].bloqueos), [(1280, 720), (1920, 1080)])

    def test_nombre_del_manifiesto_se_sanea(self):
        self.catalogo(ids=("a",))
        with open(os.path.join(self.fondos, "fondos.json"), encoding="utf-8") as f:
            man = json.load(f)
        man["fondos"][0]["nombre"] = "<big>\x1b[2J</big>\n"
        with open(os.path.join(self.fondos, "fondos.json"), "w", encoding="utf-8") as f:
            json.dump(man, f)
        self.assertEqual(F.catalogo_sistema(self.rutas)[0].nombre, "big 2J big")

    def test_el_catalogo_sin_ficheros_cae_al_respaldo(self):
        # Un manifiesto cuyas imágenes aún no existen (se generan poco a poco) no deja la lista vacía.
        self.catalogo(ids=("a",))
        shutil.rmtree(os.path.join(self.fondos, "a"))
        self.legado()
        self.assertEqual([f.id for f in F.catalogo_sistema(self.rutas)], ["cascada", "islandia"])

    def test_fondos_del_usuario(self):
        self.fondo_usuario("u-uno", "Uno")
        self.fondo_usuario("u-dos", "Dos", resoluciones=("1280x720", "1536x864"), escritorio=False)
        lista = F.fondos_usuario(self.rutas)
        self.assertEqual({f.id for f in lista}, {"u-uno", "u-dos"})
        dos = F.buscar("u-dos", self.rutas)
        self.assertEqual((dos.nombre, dos.origen, dos.escritorio), ("Dos", "usuario", None))
        self.assertEqual(sorted(dos.bloqueos), [(1280, 720), (1536, 864)])

    def test_el_usuario_ignora_lo_que_no_encaja(self):
        self.fondo_usuario("u-bueno")
        os.makedirs(os.path.join(self.usuario, "sin-prefijo"))
        self.fondo_usuario("u-sin-meta")
        os.remove(os.path.join(self.usuario, "u-sin-meta", "meta.json"))
        escribe(os.path.join(self.usuario, "u-sin-ficheros", "meta.json"), b'{"nombre": "x"}')
        escribe(os.path.join(self.usuario, "u-meta-rota", "escritorio.jpg"))
        escribe(os.path.join(self.usuario, "u-meta-rota", "meta.json"), b"{no")
        escribe(os.path.join(self.usuario, "u-archivo"), b"soy un archivo, no una carpeta")
        escribe(os.path.join(self.usuario, ".nuevo-abc", "escritorio.jpg"))
        self.assertEqual([f.id for f in F.fondos_usuario(self.rutas)], ["u-bueno"])

    def test_el_usuario_no_sigue_enlaces_simbolicos(self):
        fuera = os.path.join(self.tmp, "fuera")
        self.fondo_usuario("u-real")
        shutil.copytree(os.path.join(self.usuario, "u-real"), fuera)
        os.symlink(fuera, os.path.join(self.usuario, "u-enlace"))             # carpeta que es un enlace
        self.fondo_usuario("u-con-enlaces")
        os.remove(os.path.join(self.usuario, "u-con-enlaces", "escritorio.jpg"))
        os.symlink(os.path.join(fuera, "escritorio.jpg"), os.path.join(self.usuario, "u-con-enlaces", "escritorio.jpg"))
        os.remove(os.path.join(self.usuario, "u-con-enlaces", "bloqueo-1920x1080.jpg"))
        os.symlink(os.path.join(fuera, "bloqueo-1920x1080.jpg"),
                   os.path.join(self.usuario, "u-con-enlaces", "bloqueo-1920x1080.jpg"))
        self.assertEqual([f.id for f in F.fondos_usuario(self.rutas)], ["u-real"])
        self.assertIsNone(F.buscar("u-enlace", self.rutas))

    def test_la_carpeta_de_usuario_enlazada_no_se_usa(self):
        self.fondo_usuario("u-real")
        otra = os.path.join(self.tmp, "otra")
        os.rename(self.usuario, otra)
        os.symlink(otra, self.usuario)
        self.assertEqual(F.fondos_usuario(self.rutas), [])
        self.assertIsNone(F.buscar("u-real", self.rutas))

    def test_buscar_no_usa_ids_mal_formados(self):
        self.catalogo()
        self.fondo_usuario("u-mio")
        for i in ("../fondos/cascada", "cascada/", "u-../u-mio", "", None, "CASCADA"):
            self.assertIsNone(F.buscar(i, self.rutas), repr(i))
        self.assertIsNotNone(F.buscar("cascada", self.rutas))
        self.assertIsNotNone(F.buscar("u-mio", self.rutas))
        self.assertIsNone(F.buscar("mio", self.rutas))     # sin el prefijo no se busca en la carpeta del usuario
        self.assertIsNone(F.buscar("u-otro", self.rutas))

    def test_un_fondo_de_usuario_no_tapa_a_uno_del_sistema(self):
        self.catalogo()
        self.fondo_usuario("u-cascada")
        self.assertEqual(F.buscar("cascada", self.rutas).origen, "sistema")
        self.assertEqual(F.buscar("u-cascada", self.rutas).origen, "usuario")

    def test_listar_junta_sistema_y_usuario(self):
        self.catalogo(ids=("cascada",))
        self.fondo_usuario("u-mio")
        self.assertEqual([f.id for f in F.listar(self.rutas)], ["cascada", "u-mio"])


# ======================================================================== qué fondo toca
class TestResolver(Entorno):
    def test_mas_cercana(self):
        b = {(1920, 1080): "a", (1280, 720): "b", (1920, 1200): "c", (1280, 800): "d"}
        self.assertEqual(F.mas_cercana(b, 1920, 1080), "a")           # exacta
        self.assertEqual(F.mas_cercana(b, 1536, 864), "b")            # misma proporción: la de área más cercana
        self.assertEqual(F.mas_cercana(b, 1700, 956), "a")
        self.assertEqual(F.mas_cercana(b, 1440, 900), "d")            # 16:10 → 1280x800 (área más cercana) o 1920x1200
        self.assertEqual(F.mas_cercana(b, 2560, 1600), "c")
        self.assertEqual(F.mas_cercana(b, 1024, 768), "d")            # 4:3 se parece más a 16:10 que a 16:9
        self.assertIsNone(F.mas_cercana({}, 1920, 1080))

    def test_escritorio_cadena(self):
        self.catalogo()
        self.legado()
        self.fondo_usuario("u-mio")
        r = lambda v: F.ruta_escritorio(v, self.rutas)  # noqa: E731
        self.assertTrue(r("imagen").endswith("cascada/escritorio.jpg"))
        self.assertTrue(r("lago").endswith("lago/escritorio.jpg"))
        self.assertTrue(r("u-mio").endswith("u-mio/escritorio.jpg"))
        self.assertIsNone(r("color"))
        for raro in ("borrado", "../../etc/passwd", "", None, 7, "u-no-existe", "LAGO"):
            self.assertTrue(r(raro).endswith("cascada/escritorio.jpg"), repr(raro))
        shutil.rmtree(os.path.join(self.fondos, "cascada"))
        self.assertEqual(r("borrado"), os.path.join(self.share, "wallpaper.jpg"))
        os.remove(os.path.join(self.share, "wallpaper.jpg"))
        self.assertIsNone(r("borrado"))

    def test_bloqueo_cadena(self):
        self.catalogo(resoluciones=("1920x1080", "1280x720"))
        self.legado()
        self.fondo_usuario("u-mio", resoluciones=("1536x864",))
        r = lambda i, w=1920, h=1080: F.ruta_bloqueo(i, w, h, self.rutas)  # noqa: E731
        self.assertTrue(r("lago").endswith("lago/bloqueo-1920x1080.jpg"))
        self.assertTrue(r("lago", 1280, 720).endswith("lago/bloqueo-1280x720.jpg"))
        self.assertTrue(r("lago", 1700, 956).endswith("lago/bloqueo-1920x1080.jpg"))   # la más cercana de ese fondo
        self.assertTrue(r("u-mio", 1920, 1080).endswith("u-mio/bloqueo-1536x864.jpg"))  # solo tiene esa
        for raro in ("borrado", "../x", "", None, 3, "u-no-existe"):
            self.assertTrue(r(raro).endswith("islandia/bloqueo-1920x1080.jpg"), repr(raro))
        shutil.rmtree(os.path.join(self.fondos, "islandia"))
        self.assertTrue(r("borrado", 1280, 720).endswith("wallpaper-lock-1280x720.jpg"))
        self.assertTrue(r("borrado", 3000, 2000).endswith("wallpaper-lock-1920x1080.jpg"))
        shutil.rmtree(os.path.join(self.share, "lock"))
        self.assertIsNone(r("borrado"))

    def test_medidas_raras_no_rompen(self):
        self.catalogo()
        for medida in ((None, None), (0, 0), (-5, 10), ("a", "b"), (True, False)):
            self.assertTrue(F.ruta_bloqueo("lago", *medida, self.rutas).endswith("lago/bloqueo-1920x1080.jpg"), medida)


# ======================================================================== borrar
class TestQuitar(Entorno):
    def test_quita_un_fondo_del_usuario(self):
        d = self.fondo_usuario("u-mio")
        self.fondo_usuario("u-otro")
        self.assertTrue(F.quitar("u-mio", self.rutas))
        self.assertFalse(os.path.exists(d))
        self.assertTrue(os.path.isdir(os.path.join(self.usuario, "u-otro")))
        self.assertFalse(F.quitar("u-mio", self.rutas))      # ya no está

    def test_no_quita_ids_del_sistema_ni_mal_formados(self):
        self.catalogo()
        sentinela = escribe(os.path.join(self.tmp, "sentinela.txt"))
        for i in ("cascada", "..", "../../sentinela.txt", "u-../../sentinela", "", None, "u-", "u-a/b", "/etc"):
            with self.assertRaises(F.ErrorFondo, msg=repr(i)):
                F.quitar(i, self.rutas)
        self.assertTrue(os.path.exists(sentinela))
        self.assertTrue(os.path.isdir(os.path.join(self.fondos, "cascada")))

    def test_no_sigue_un_enlace_a_otra_carpeta(self):
        fuera = os.path.join(self.tmp, "valioso")
        escribe(os.path.join(fuera, "tesoro.txt"))
        os.makedirs(self.usuario)
        os.symlink(fuera, os.path.join(self.usuario, "u-trampa"))
        self.assertFalse(F.quitar("u-trampa", self.rutas))
        self.assertTrue(os.path.exists(os.path.join(fuera, "tesoro.txt")))

    def test_un_enlace_dentro_del_fondo_se_borra_sin_seguirlo(self):
        fuera = os.path.join(self.tmp, "valioso")
        escribe(os.path.join(fuera, "tesoro.txt"))
        d = self.fondo_usuario("u-mio")
        os.symlink(fuera, os.path.join(d, "enlace"))
        os.symlink(os.path.join(fuera, "tesoro.txt"), os.path.join(d, "enlace.txt"))
        self.assertTrue(F.quitar("u-mio", self.rutas))
        self.assertFalse(os.path.exists(d))
        self.assertTrue(os.path.exists(os.path.join(fuera, "tesoro.txt")))

    def test_con_la_carpeta_de_usuario_enlazada_no_borra_nada(self):
        d = self.fondo_usuario("u-mio")
        otra = os.path.join(self.tmp, "otra")
        os.rename(self.usuario, otra)
        os.symlink(otra, self.usuario)
        self.assertFalse(F.quitar("u-mio", self.rutas))
        self.assertTrue(os.path.exists(os.path.join(otra, "u-mio")))
        self.assertFalse(os.path.exists(d) and False)


# ======================================================================== importar
def _png_con_cabecera(ancho, alto):
    """Un PNG que solo tiene la cabecera: dice medir ancho×alto pero no trae píxeles (para probar los topes)."""
    def trozo(tipo, datos):
        return struct.pack(">I", len(datos)) + tipo + datos + struct.pack(">I", zlib.crc32(tipo + datos))
    ihdr = struct.pack(">IIBBBBB", ancho, alto, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + trozo(b"IHDR", ihdr) + trozo(b"IDAT", zlib.compress(b"\x00" * 64)) + trozo(b"IEND", b"")


@unittest.skipUnless(TIENE_PIXBUF, "hace falta GdkPixbuf (PyGObject)")
class TestImportar(Entorno):
    def imagen(self, nombre, ancho=640, alto=360, formato="jpeg", izq=(220, 30, 30), der=(30, 30, 220), alfa=False):
        """Guarda una imagen: mitad izquierda de un color y mitad derecha de otro."""
        pix = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, alfa, 8, ancho, alto)
        pix.fill(0xFF)
        mitad = pix.new_subpixbuf(0, 0, ancho // 2, alto)
        mitad.fill(izq[0] << 24 | izq[1] << 16 | izq[2] << 8 | 0xFF)
        otra = pix.new_subpixbuf(ancho // 2, 0, ancho - ancho // 2, alto)
        otra.fill(der[0] << 24 | der[1] << 16 | der[2] << 8 | 0xFF)
        ruta = os.path.join(self.tmp, "origen", nombre)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        pix.savev(ruta, formato, [], [])
        return ruta

    @staticmethod
    def leer(ruta):
        with open(ruta, "rb") as f:
            return f.read()

    def importa(self, origen, **kw):
        return F.importar(origen, self.rutas, **kw)

    def medida(self, ruta):
        formato, ancho, alto = GdkPixbuf.Pixbuf.get_file_info(ruta)
        return ancho, alto

    def pixel(self, ruta, x, y):
        pix = GdkPixbuf.Pixbuf.new_from_file(ruta)
        i = y * pix.get_rowstride() + x * pix.get_n_channels()
        return tuple(pix.get_pixels()[i:i + 3])

    def sin_restos(self):
        """No quedan carpetas temporales (.nuevo-…) ni nada que no sea un fondo."""
        if os.path.isdir(self.usuario):
            self.assertEqual([n for n in os.listdir(self.usuario) if not F.es_de_usuario(n)], [])

    # --- el caso feliz
    def test_importa_y_genera_los_mismos_ficheros_que_el_catalogo(self):
        origen = self.imagen("Mi Foto.png", 1920, 1080, "png")
        avances = []
        f = self.importa(origen, progreso=lambda h, t: avances.append((h, t)))
        self.assertEqual((f.id, f.nombre, f.origen), ("u-mi-foto", "Mi Foto", "usuario"))
        nombres = set(os.listdir(f.carpeta))
        esperados = {"escritorio.jpg", "miniatura.jpg", "meta.json"} | {f"bloqueo-{w}x{h}.jpg" for w, h in F.RESOLUCIONES}
        self.assertEqual(nombres, esperados)
        self.assertEqual(self.medida(f.miniatura), (320, 180))
        for w, h in F.RESOLUCIONES:
            self.assertEqual(self.medida(f.bloqueos[(w, h)]), (w, h))
        self.assertEqual(self.medida(f.escritorio), (1920, 1080))     # no se amplía más allá de la imagen
        self.assertEqual(avances[-1][0], avances[-1][1])
        self.assertEqual(len(avances), len(F.RESOLUCIONES) + 2)
        with open(os.path.join(f.carpeta, "meta.json"), encoding="utf-8") as m:
            meta = json.load(m)
        self.assertEqual(meta["nombre"], "Mi Foto")
        self.assertEqual(F.buscar("u-mi-foto", self.rutas).id, "u-mi-foto")

    def test_ficheros_son_jpeg_sin_metadatos_y_con_permisos_normales(self):
        f = self.importa(self.imagen("a.png", 800, 450, "png"))
        for n in os.listdir(f.carpeta):
            ruta = os.path.join(f.carpeta, n)
            self.assertTrue(stat.S_ISREG(os.lstat(ruta).st_mode), n)
            self.assertEqual(stat.S_IMODE(os.lstat(ruta).st_mode) & 0o022, 0, n)   # sin escritura para grupo/otros
            if n.endswith(".jpg"):
                datos = self.leer(ruta)
                self.assertEqual(datos[:3], b"\xff\xd8\xff", n)
                self.assertNotIn(b"Exif", datos[:2000], n)
        self.assertEqual(stat.S_IMODE(os.lstat(f.carpeta).st_mode) & 0o022, 0)

    def test_cubre_sin_deformar_recortando_del_centro(self):
        # 2:1 (izquierda roja, derecha azul) a 16:9: se recortan los lados y la raya sigue en el centro.
        f = self.importa(self.imagen("dos.png", 800, 400, "png"))
        ruta = f.bloqueos[(1280, 720)]
        izq, der = self.pixel(ruta, 300, 360), self.pixel(ruta, 980, 360)
        self.assertGreater(izq[0], izq[2] + 60)
        self.assertGreater(der[2], der[0] + 60)
        self.assertLess(izq[0], 200)       # oscurecida como las del catálogo (65 %)
        borde = self.pixel(ruta, 639, 100), self.pixel(ruta, 641, 100)
        self.assertGreater(borde[0][0], borde[0][2])
        self.assertGreater(borde[1][2], borde[1][0])

    def test_imagen_vertical_y_pequena(self):
        f = self.importa(self.imagen("alta.png", 300, 600, "png"))
        self.assertEqual(self.medida(f.bloqueos[(1920, 1080)]), (1920, 1080))
        self.assertEqual(self.medida(f.escritorio), (300, 169))       # 16:9 sin ampliar
        pequena = self.importa(self.imagen("peq.png", 100, 100, "png"))
        self.assertEqual(self.medida(pequena.bloqueos[(2560, 1600)]), (2560, 1600))

    def test_escritorio_no_pasa_de_2560x1440(self):
        f = self.importa(self.imagen("grande.png", 4000, 3000, "png"))
        self.assertEqual(self.medida(f.escritorio), (2560, 1440))

    def test_monitores_conectados_se_anaden_si_no_estan(self):
        f = self.importa(self.imagen("a.png"), monitores=[(1536, 864), (1920, 1080), (1080, 1920), (10, 10), (99999, 5),
                                                          ("x", 1), None, (1536, 864)])
        extras = sorted(set(f.bloqueos) - set(F.RESOLUCIONES))
        self.assertEqual(extras, [(1080, 1920), (1536, 864)])
        self.assertEqual(self.medida(f.bloqueos[(1536, 864)]), (1536, 864))

    def test_resoluciones_a_generar_pone_un_tope(self):
        r = F.resoluciones_a_generar([(1000 + i, 800) for i in range(30)])
        self.assertEqual(len(r), len(F.RESOLUCIONES) + F.MAX_MONITORES_EXTRA)

    def test_con_transparencia(self):
        f = self.importa(self.imagen("alfa.png", 400, 225, "png", alfa=True))
        self.assertEqual(self.medida(f.escritorio), (400, 225))

    def test_otros_formatos(self):
        for nombre, formato in (("a.jpg", "jpeg"), ("a.bmp", "bmp"), ("a.tiff", "tiff")):
            f = self.importa(self.imagen(nombre, 320, 180, formato), nombre="otro " + formato)
            self.assertTrue(os.path.isfile(f.escritorio), formato)

    def test_se_mira_el_contenido_no_la_extension(self):
        f = self.importa(self.imagen("foto.txt", 320, 180, "png"))      # una imagen con extensión falsa vale
        self.assertTrue(os.path.isfile(f.escritorio))
        falso = os.path.join(self.tmp, "origen", "falso.jpg")
        escribe(falso, b"esto es texto, no una imagen\n" * 100)
        with self.assertRaisesRegex(F.ErrorFondo, "no es una imagen"):
            self.importa(falso)
        self.sin_restos()

    def test_nombres_repetidos_reciben_numero(self):
        a = self.importa(self.imagen("mi foto.png", 320, 180, "png"))
        b = self.importa(self.imagen("mi foto.png", 320, 180, "png"))
        self.assertEqual((a.id, b.id), ("u-mi-foto", "u-mi-foto-2"))

    # --- nombres peligrosos
    def test_nombre_con_rutas_no_sale_de_la_carpeta(self):
        for nombre in ("../../../etc/passwd", "..", "a/../../b", "/tmp/x", "x\x00y", "‮gpj.exe", "<script>"):
            f = self.importa(self.imagen("base.png", 320, 180, "png"), nombre=nombre)
            self.assertTrue(F.id_valido(f.id), (nombre, f.id))
            self.assertEqual(os.path.dirname(f.carpeta), self.usuario)
        fuera = [n for n in os.listdir(self.tmp) if n not in ("share", "config", "datos", "home", "origen")]
        self.assertEqual(fuera, [])
        self.assertEqual(os.listdir(os.path.join(self.datos, "infinity")), ["fondos"])

    def test_archivo_con_nombre_raro(self):
        for nombre in ("..png", "a;b$(touch x).png", "con\nsalto.png", "  .png", "ñandú café.png"):
            f = self.importa(self.imagen(nombre, 320, 180, "png"))
            self.assertTrue(F.id_valido(f.id), (nombre, f.id))
            self.assertEqual(os.path.dirname(f.carpeta), self.usuario)

    # --- imágenes que se rechazan
    def test_rechaza_lo_que_no_es_una_imagen_legible(self):
        casos = {
            "vacio": (b"", "vacío"),
            "texto": (b"hola" * 100, "no es una imagen"),
            "jpeg-cortado": (self.leer(self.imagen("c.jpg", 640, 360, "jpeg"))[:300], "dañada"),
            "jpeg-a-medias": (self.leer(self.imagen("m.jpg", 640, 360, "jpeg"))[:-300], "dañada"),
            "gif-cortado": (self.leer(self.imagen("c.gif", 640, 360, "gif"))[:-5], "dañada"),
            "bmp-cortado": (self.leer(self.imagen("c.bmp", 64, 64, "bmp"))[:-50], "dañada"),
            "jpeg-roto": (b"\xff\xd8\xff\xe0" + os.urandom(2000), "dañada"),
            "png-cortado": (self.leer(self.imagen("c.png", 640, 360, "png"))[:100], "dañada"),
            "png-sin-el-final": (self.leer(self.imagen("f.png", 640, 360, "png"))[:-12], "dañada"),
            "html": (b"<html><body>no</body></html>", "no es una imagen"),
        }
        for clave, (datos, mensaje) in casos.items():
            ruta = escribe(os.path.join(self.tmp, "origen", clave + ".jpg"), datos)
            with self.assertRaisesRegex(F.ErrorFondo, mensaje, msg=clave):
                self.importa(ruta)
        self.assertEqual(F.fondos_usuario(self.rutas), [])
        self.sin_restos()

    def test_rechaza_svg(self):
        svg = escribe(os.path.join(self.tmp, "origen", "a.svg"),
                      b'<svg xmlns="http://www.w3.org/2000/svg" width="640" height="360"><rect width="640" height="360"/></svg>')
        with self.assertRaises(F.ErrorFondo):
            self.importa(svg)
        self.assertEqual(F.fondos_usuario(self.rutas), [])

    def test_rechaza_dimensiones_enormes_antes_de_decodificar(self):
        for ancho, alto in ((F.MAX_LADO + 1, 100), (100, F.MAX_LADO + 1), (9000, 9000), (60000, 60000)):
            ruta = escribe(os.path.join(self.tmp, "origen", f"enorme-{ancho}x{alto}.png"), _png_con_cabecera(ancho, alto))
            with self.assertRaisesRegex(F.ErrorFondo, "demasiado grande", msg=(ancho, alto)):
                self.importa(ruta)
        self.assertEqual(F.fondos_usuario(self.rutas), [])
        self.sin_restos()

    def test_rechaza_archivos_de_mas_de_40_mb_sin_leerlos(self):
        ruta = os.path.join(self.tmp, "origen", "pesado.jpg")
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "wb") as f:
            f.truncate(F.MAX_BYTES + 1)         # fichero hueco: no ocupa disco
        with self.assertRaisesRegex(F.ErrorFondo, "demasiado grande"):
            self.importa(ruta)

    def test_rechaza_tamano_minimo(self):
        with self.assertRaisesRegex(F.ErrorFondo, "pequeña"):
            self.importa(self.imagen("mini.png", 20, 20, "png"))

    def test_rechaza_lo_que_no_es_un_fichero(self):
        carpeta = os.path.join(self.tmp, "origen", "carpeta")
        os.makedirs(carpeta)
        for ruta in (carpeta, os.path.join(self.tmp, "no-existe.png"), "", None, "/dev/zero", "/dev/null"):
            with self.assertRaises(F.ErrorFondo, msg=repr(ruta)):
                self.importa(ruta)
        fifo = os.path.join(self.tmp, "origen", "tuberia.png")
        os.mkfifo(fifo)
        t0 = time.monotonic()
        with self.assertRaises(F.ErrorFondo):
            self.importa(fifo)
        self.assertLess(time.monotonic() - t0, 5)      # una tubería no deja la interfaz esperando

    def test_acepta_un_enlace_a_una_imagen_pero_no_a_otras_cosas(self):
        real = self.imagen("real.png", 320, 180, "png")
        enlace = os.path.join(self.tmp, "origen", "enlace.png")
        os.symlink(real, enlace)
        self.assertTrue(os.path.isfile(self.importa(enlace).escritorio))     # lo eligió el usuario a propósito
        malo = os.path.join(self.tmp, "origen", "malo.png")
        os.symlink("/dev/zero", malo)
        with self.assertRaises(F.ErrorFondo):
            self.importa(malo)

    @unittest.skipIf(TIENE_PIXBUF and "webp" in [f.get_name() for f in GdkPixbuf.Pixbuf.get_formats()],
                     "este equipo sí lee WebP")
    def test_webp_sin_cargador_da_un_mensaje_claro(self):
        webp = escribe(os.path.join(self.tmp, "origen", "a.webp"), b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 40)
        with self.assertRaisesRegex(F.ErrorFondo, "WebP"):
            self.importa(webp)

    # --- carpeta del usuario
    def test_tope_de_fondos(self):
        for i in range(F.MAX_FONDOS_USUARIO):
            self.fondo_usuario(f"u-fondo-{i}", f"Fondo {i}")
        with self.assertRaisesRegex(F.ErrorFondo, str(F.MAX_FONDOS_USUARIO)):
            self.importa(self.imagen("una-mas.png", 320, 180, "png"))
        F.quitar("u-fondo-0", self.rutas)
        self.assertTrue(self.importa(self.imagen("una-mas.png", 320, 180, "png")).id.startswith("u-una-mas"))

    def test_no_pisa_ni_sigue_un_enlace_que_ya_esta_en_la_carpeta(self):
        fuera = os.path.join(self.tmp, "valioso")
        escribe(os.path.join(fuera, "tesoro.txt"))
        os.makedirs(self.usuario)
        os.symlink(fuera, os.path.join(self.usuario, "u-mi-foto"))
        f = self.importa(self.imagen("mi foto.png", 320, 180, "png"))
        self.assertEqual(f.id, "u-mi-foto-2")
        self.assertEqual(os.listdir(fuera), ["tesoro.txt"])

    def test_la_carpeta_de_fondos_enlazada_no_se_usa(self):
        fuera = os.path.join(self.tmp, "valioso")
        os.makedirs(fuera)
        os.makedirs(os.path.dirname(self.usuario))
        os.symlink(fuera, self.usuario)
        with self.assertRaisesRegex(F.ErrorFondo, "no es una carpeta normal"):
            self.importa(self.imagen("a.png", 320, 180, "png"))
        self.assertEqual(os.listdir(fuera), [])

    def test_un_fallo_a_mitad_no_deja_nada(self):
        origen = self.imagen("a.png", 640, 360, "png")
        llamadas = []
        original = F._jpeg

        def falla_al_quinto(pix):
            llamadas.append(1)
            if len(llamadas) == 5:
                raise F.ErrorFondo("disco lleno de mentira")
            return original(pix)
        F._jpeg = falla_al_quinto
        try:
            with self.assertRaisesRegex(F.ErrorFondo, "disco lleno"):
                self.importa(origen)
        finally:
            F._jpeg = original
        self.assertEqual(os.listdir(self.usuario), [])

    def test_error_de_escritura_se_explica(self):
        origen = self.imagen("a.png", 640, 360, "png")
        original = F._escribir_bytes

        def sin_espacio(ruta, datos):
            raise OSError(28, "No space left on device")
        F._escribir_bytes = sin_espacio
        try:
            with self.assertRaisesRegex(F.ErrorFondo, "No se pudo guardar"):
                self.importa(origen)
        finally:
            F._escribir_bytes = original
        self.assertEqual(os.listdir(self.usuario), [])

    def test_limpia_temporales_viejos_de_importaciones_cortadas(self):
        viejo = os.path.join(self.usuario, ".nuevo-viejo")
        nuevo = os.path.join(self.usuario, ".nuevo-nuevo")
        os.makedirs(viejo)
        os.makedirs(nuevo)
        antes = time.time() - 3 * 86400
        os.utime(viejo, (antes, antes))
        self.importa(self.imagen("a.png", 320, 180, "png"))
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(nuevo))

    def test_lo_importado_se_puede_quitar_y_se_usa_en_la_cadena(self):
        f = self.importa(self.imagen("a.png", 320, 180, "png"))
        self.assertEqual(F.ruta_escritorio(f.id, self.rutas), f.escritorio)
        self.assertEqual(F.ruta_bloqueo(f.id, 1920, 1080, self.rutas), f.bloqueos[(1920, 1080)])
        self.assertTrue(F.quitar(f.id, self.rutas))
        self.assertIsNone(F.ruta_escritorio(f.id, self.rutas))
        self.assertIsNone(F.ruta_bloqueo(f.id, 1920, 1080, self.rutas))


class TestImportarSinGdkPixbuf(Entorno):
    def test_las_comprobaciones_previas_no_necesitan_gdkpixbuf(self):
        # Vacío, carpeta o inexistente se rechazan antes de intentar decodificar nada.
        vacio = escribe(os.path.join(self.tmp, "vacio.png"), b"")
        for ruta in (vacio, self.tmp, os.path.join(self.tmp, "no.png")):
            with self.assertRaises(F.ErrorFondo):
                F.importar(ruta, self.rutas)


# ======================================================================== scripts
def _corre(args, env, **kw):
    return subprocess.run(args, env=env, capture_output=True, text=True, timeout=30, **kw)


@unittest.skipUnless(shutil.which("sh") and shutil.which("awk") and shutil.which("od"), "hacen falta sh, awk y od")
class TestFondoRuta(Entorno):
    def ruta(self, *args):
        r = _corre(["sh", FONDO_RUTA, *args], self.entorno())
        return r.stdout.strip() or None, r.returncode

    def escritorio(self):
        return self.ruta("escritorio")

    def bloqueo(self, ancho=1920, alto=1080):
        return self.ruta("bloqueo", str(ancho), str(alto))

    def rel(self, ruta):
        return os.path.relpath(ruta, self.tmp) if ruta else None

    def test_escritorio_elegido_y_respaldos(self):
        self.catalogo()
        self.legado()
        self.fondo_usuario("u-mio")
        casos = [("imagen", "share/fondos/cascada/escritorio.jpg"), ("lago", "share/fondos/lago/escritorio.jpg"),
                 ("u-mio", "datos/infinity/fondos/u-mio/escritorio.jpg"), ("borrado", "share/fondos/cascada/escritorio.jpg"), ("../../etc/passwd", "share/fondos/cascada/escritorio.jpg"),
                 ("LAGO", "share/fondos/lago/escritorio.jpg"),     # el script pasa a minúsculas, igual que fondo-modo
                 ("u-no-existe", "share/fondos/cascada/escritorio.jpg"), ("  lago \n", "share/fondos/lago/escritorio.jpg"),
                 ("-lago", "share/fondos/cascada/escritorio.jpg"), ("lago/", "share/fondos/cascada/escritorio.jpg"),
                 ("a" * 200, "share/fondos/cascada/escritorio.jpg")]
        for contenido, esperado in casos:
            self.ajuste("fondo", contenido)
            ruta, codigo = self.escritorio()
            self.assertEqual((self.rel(ruta), codigo), (esperado, 0), repr(contenido))

    def test_escritorio_con_color_no_da_imagen(self):
        self.catalogo()
        self.legado()
        for contenido in ("color", "COLOR\n"):
            self.ajuste("fondo", contenido)
            self.assertEqual(self.escritorio(), (None, 3))

    def test_escritorio_sin_fichero_de_ajuste_usa_la_cascada(self):
        self.catalogo()
        self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg")

    def test_escritorio_cadena_hasta_el_fondo_antiguo_y_nada(self):
        self.catalogo()
        self.legado()
        self.ajuste("fondo", "lago")
        shutil.rmtree(os.path.join(self.fondos, "lago"))
        shutil.rmtree(os.path.join(self.fondos, "cascada"))
        self.assertEqual(self.rel(self.escritorio()[0]), "share/wallpaper.jpg")
        os.remove(os.path.join(self.share, "wallpaper.jpg"))
        self.assertEqual(self.escritorio(), (None, 1))

    def test_ficheros_danados_se_saltan(self):
        self.catalogo()
        self.legado()
        self.ajuste("fondo", "lago")
        malos = {"vacio": b"", "texto": b"no soy un jpeg" * 10, "png": b"\x89PNG\r\n\x1a\n" + b"x" * 50, "corto": b"\xff\xd8"}
        for nombre, datos in malos.items():
            escribe(os.path.join(self.fondos, "lago", "escritorio.jpg"), datos)
            self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg", nombre)
        os.remove(os.path.join(self.fondos, "lago", "escritorio.jpg"))
        self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg")
        os.makedirs(os.path.join(self.fondos, "lago", "escritorio.jpg"))     # una carpeta con ese nombre
        self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg")

    def test_el_usuario_no_se_sigue_por_enlaces(self):
        self.catalogo()
        d = self.fondo_usuario("u-mio")
        self.ajuste("fondo", "u-mio")
        self.assertIn("u-mio", self.escritorio()[0])
        os.remove(os.path.join(d, "escritorio.jpg"))
        os.symlink(os.path.join(self.fondos, "lago", "escritorio.jpg"), os.path.join(d, "escritorio.jpg"))
        self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg")   # el fichero es un enlace
        os.remove(os.path.join(d, "escritorio.jpg"))
        shutil.move(d, os.path.join(self.tmp, "movida"))
        os.symlink(os.path.join(self.tmp, "movida"), d)
        self.assertEqual(self.rel(self.escritorio()[0]), "share/fondos/cascada/escritorio.jpg")   # la carpeta es un enlace

    def test_bloqueo_exacto_y_mas_cercano(self):
        self.catalogo(resoluciones=("1920x1080", "1280x720", "1920x1200"))
        self.ajuste("fondo-bloqueo", "lago")
        casos = {(1920, 1080): "1920x1080", (1280, 720): "1280x720", (1536, 864): "1280x720", (1700, 956): "1920x1080", (1440, 900): "1920x1200",
                 (1024, 768): "1920x1200", (800, 450): "1280x720", (3840, 2160): "1920x1080"}
        for (w, h), esperado in casos.items():
            ruta, codigo = self.bloqueo(w, h)
            self.assertEqual((self.rel(ruta), codigo), (f"share/fondos/lago/bloqueo-{esperado}.jpg", 0), (w, h))

    def test_bloqueo_cadena_de_respaldo(self):
        self.catalogo(ids=("islandia", "lago"))
        self.legado()
        self.fondo_usuario("u-mio", resoluciones=("1536x864",))
        casos = [("lago", "share/fondos/lago/bloqueo-1920x1080.jpg"), ("u-mio", "datos/infinity/fondos/u-mio/bloqueo-1536x864.jpg"),
                 ("borrado", "share/fondos/islandia/bloqueo-1920x1080.jpg"), ("", "share/fondos/islandia/bloqueo-1920x1080.jpg"),
                 ("../../x", "share/fondos/islandia/bloqueo-1920x1080.jpg"), ("u-no-existe", "share/fondos/islandia/bloqueo-1920x1080.jpg")]
        for contenido, esperado in casos:
            self.ajuste("fondo-bloqueo", contenido)
            self.assertEqual(self.rel(self.bloqueo()[0]), esperado, repr(contenido))
        self.ajuste("fondo-bloqueo", "lago")
        shutil.rmtree(os.path.join(self.fondos, "lago"))
        shutil.rmtree(os.path.join(self.fondos, "islandia"))
        self.assertEqual(self.rel(self.bloqueo(1280, 720)[0]), "share/lock/fondos/wallpaper-lock-1280x720.jpg")
        self.assertEqual(self.rel(self.bloqueo(3000, 2000)[0]), "share/lock/fondos/wallpaper-lock-1920x1080.jpg")
        shutil.rmtree(os.path.join(self.share, "lock"))
        self.assertEqual(self.bloqueo(), (None, 1))

    def test_bloqueo_con_ficheros_danados(self):
        self.catalogo(ids=("islandia", "lago"))
        self.ajuste("fondo-bloqueo", "lago")
        escribe(os.path.join(self.fondos, "lago", "bloqueo-1920x1080.jpg"), b"")
        # El exacto está roto: se usa la más cercana que sí sirve, del mismo fondo.
        self.assertEqual(self.rel(self.bloqueo()[0]), "share/fondos/lago/bloqueo-1280x720.jpg")
        escribe(os.path.join(self.fondos, "lago", "bloqueo-1280x720.jpg"), b"texto")
        self.assertEqual(self.rel(self.bloqueo()[0]), "share/fondos/islandia/bloqueo-1920x1080.jpg")

    def test_bloqueo_medidas_raras_usan_1920x1080(self):
        self.catalogo(ids=("islandia",))
        for w, h in (("abc", "1080"), ("", ""), ("0", "0"), ("-5", "10"), ("1920", "x"), ("1e3", "2")):
            ruta, codigo = _corre(["sh", FONDO_RUTA, "bloqueo", w, h], self.entorno()).stdout.strip(), 0
            self.assertEqual(self.rel(ruta), "share/fondos/islandia/bloqueo-1920x1080.jpg", (w, h))

    def test_sin_argumentos_da_error(self):
        self.assertEqual(self.ruta()[1], 2)
        self.assertEqual(self.ruta("otra-cosa")[1], 2)

    def test_coincide_con_python(self):
        self.catalogo(ids=("cascada", "islandia", "lago"), resoluciones=("1920x1080", "1280x720", "1920x1200", "2560x1600"))
        self.legado()
        self.fondo_usuario("u-mio", resoluciones=("1536x864", "1080x1920", "1366x768"))
        self.fondo_usuario("u-solo-bloqueo", resoluciones=("1280x800",), escritorio=False)
        valores = ["imagen", "color", "lago", "u-mio", "borrado", "u-solo-bloqueo", "u-no-existe", "islandia", "cascada"]
        for v in valores:
            self.ajuste("fondo", v)
            esperado = F.ruta_escritorio(v, self.rutas)
            if v == "u-solo-bloqueo":     # el shell mira el fichero, Python mira el manifiesto: ambos acaban en la cascada
                esperado = F.ruta_escritorio("imagen", self.rutas)
            self.assertEqual(self.escritorio()[0], esperado, v)
            self.assertEqual(self.escritorio()[1], 3 if v == "color" else 0, v)
        for v in valores:
            for medida in ((1920, 1080), (1536, 864), (1366, 768), (1440, 900), (1024, 768), (800, 600), (3840, 2160),
                           (1080, 1920), (1280, 800), (2560, 1440), (1600, 1200), (1280, 1024), (5120, 1440)):
                self.ajuste("fondo-bloqueo", v)
                self.assertEqual(self.bloqueo(*medida)[0], F.ruta_bloqueo(v, *medida, self.rutas), (v, medida))


@unittest.skipUnless(shutil.which("sh"), "hace falta sh")
class TestFondoModo(Entorno):
    def modo(self):
        return _corre(["sh", FONDO_MODO], self.entorno()).stdout.strip()

    def test_modos(self):
        self.assertEqual(self.modo(), "imagen")          # sin fichero
        for contenido, esperado in (("color", "color"), ("COLOR\n", "color"), (" color ", "color"), ("imagen", "imagen"),
                                    ("lago", "imagen"), ("u-mio", "imagen"), ("colr", "imagen"), ("colorimagen", "imagen"),
                                    ("", "imagen"), ("\n", "imagen"), ("color\nimagen", "imagen")):
            self.ajuste("fondo", contenido)
            self.assertEqual(self.modo(), esperado, repr(contenido))


@unittest.skipUnless(shutil.which("sh") and shutil.which("awk") and shutil.which("od"), "hacen falta sh, awk y od")
class TestWallpaper(Entorno):
    """infinity-wallpaper con un swaybg falso que anota con qué imagen lo llamaron."""

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        os.makedirs(self.bin)
        self.registro = os.path.join(self.tmp, "swaybg.llamadas")
        escribe(os.path.join(self.bin, "swaybg"),
                ("#!/bin/sh\necho \"$*\" >> %s\ncase \"$*\" in *roto*) exit 1 ;; esac\nsleep 3\n" % self.registro).encode())
        os.chmod(os.path.join(self.bin, "swaybg"), 0o755)

    def lanza(self):
        if os.path.exists(self.registro):
            os.remove(self.registro)
        env = self.entorno(PATH=self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"))
        p = subprocess.Popen(["sh", WALLPAPER], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        try:
            p.wait(timeout=20)
        finally:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            with open(self.registro, encoding="utf-8") as f:
                return [l.strip() for l in f]
        except FileNotFoundError:
            return []

    def img(self, ruta):
        return f"-i {os.path.join(self.tmp, ruta)} -m fill"

    def test_usa_la_imagen_elegida(self):
        self.catalogo()
        self.legado()
        self.ajuste("fondo", "lago")
        self.assertEqual(self.lanza(), [self.img("share/fondos/lago/escritorio.jpg")])

    def test_sin_ajuste_o_con_imagen_usa_la_cascada(self):
        self.catalogo()
        self.assertEqual(self.lanza(), [self.img("share/fondos/cascada/escritorio.jpg")])
        self.ajuste("fondo", "imagen")
        self.assertEqual(self.lanza(), [self.img("share/fondos/cascada/escritorio.jpg")])

    def test_color_no_lanza_swaybg(self):
        self.catalogo()
        self.ajuste("fondo", "color")
        self.assertEqual(self.lanza(), [])

    def test_fondo_borrado_vuelve_a_la_cascada_y_luego_al_antiguo(self):
        self.catalogo(ids=("cascada",))
        self.legado()
        self.ajuste("fondo", "u-borrado")
        self.assertEqual(self.lanza(), [self.img("share/fondos/cascada/escritorio.jpg")])
        shutil.rmtree(os.path.join(self.fondos, "cascada"))
        self.assertEqual(self.lanza(), [self.img("share/wallpaper.jpg")])

    def test_sin_ninguna_imagen_no_falla_ni_lanza_nada(self):
        self.ajuste("fondo", "lago")
        self.assertEqual(self.lanza(), [])

    def test_si_swaybg_no_puede_con_la_elegida_prueba_la_antigua(self):
        self.catalogo(ids=("cascada", "roto"))
        self.legado()
        self.ajuste("fondo", "roto")
        self.assertEqual(self.lanza(), [self.img("share/fondos/roto/escritorio.jpg"), self.img("share/wallpaper.jpg")])


@unittest.skipUnless(shutil.which("pidof") and shutil.which("sleep") and shutil.which("awk") and shutil.which("od"),
                     "hacen falta pidof, sleep, awk y od")
class TestBloqueoFondo(Entorno):
    """infinity-lock con gtklock y swaylock falsos: qué imagen recibe gtklock y que SIEMPRE se bloquea.

    Mismo método que test_bloqueo_arranque.py: el falso gtklock es un ejecutable llamado gtklock (copia de
    sleep) para que pidof lo vea, y el pidof que ve el script solo cuenta los procesos de esta carpeta.
    """

    def setUp(self):
        super().setUp()
        self.bin = os.path.join(self.tmp, "bin")
        self.real = os.path.join(self.tmp, "real")
        self.cache = os.path.join(self.tmp, "cache")
        for d in (self.bin, self.real, self.cache):
            os.makedirs(d)
        self.llamadas = os.path.join(self.tmp, "gtklock.llamadas")
        self.swaylock = os.path.join(self.tmp, "swaylock.llamadas")
        shutil.copy2(shutil.which("sleep"), os.path.join(self.real, "gtklock"))
        self.script(os.path.join(self.bin, "gtklock"),
                    "#!/bin/sh\necho \"$*\" >> '%s'\nexec '%s' 30\n" % (self.llamadas, os.path.join(self.real, "gtklock")))
        self.script(os.path.join(self.bin, "swaylock"), "#!/bin/sh\necho \"$*\" >> '%s'\n" % self.swaylock)
        self.script(os.path.join(self.bin, "pidof"),
                    "#!/bin/sh\nfound=\nfor pid in $('%s' \"$@\" 2>/dev/null); do\n"
                    "  case \"$(readlink /proc/$pid/exe 2>/dev/null)\" in %s/*) found=\"$found $pid\" ;; esac\ndone\n"
                    "[ -n \"$found\" ] || exit 1\necho $found\n" % (shutil.which("pidof"), os.path.realpath(self.tmp)))
        self.monitor = {"width": 1920, "height": 1080, "scale": 1}
        self.lanzados = []
        self.addCleanup(self.mata)

    def script(self, ruta, texto):
        escribe(ruta, texto.encode())
        os.chmod(ruta, 0o755)

    def mata(self):
        for p in self.lanzados:
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        carpeta = os.path.realpath(self.tmp)
        for nombre in os.listdir("/proc"):       # el falso gtklock puede haber quedado como huérfano
            if nombre.isdigit():
                try:
                    if os.readlink(f"/proc/{nombre}/exe").startswith(carpeta + os.sep):
                        os.kill(int(nombre), signal.SIGKILL)
                except OSError:
                    pass

    def bloquea(self, libexec=None, monitor=None):
        m = dict(self.monitor, focused=True, **(monitor or {}))
        self.script(os.path.join(self.bin, "hyprctl"), "#!/bin/sh\necho '%s'\n" % json.dumps([m]))
        env = self.entorno(PATH=self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
                           XDG_CACHE_HOME=self.cache, INFINITY_LIBEXEC=libexec or LIBEXEC)
        p = subprocess.Popen(["sh", LOCK], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.lanzados.append(p)
        self.assertEqual(p.wait(timeout=30), 0)
        gtk = lineas(self.llamadas) if os.path.exists(self.llamadas) else []
        return gtk

    def fondo_de(self, llamadas):
        """La ruta que recibió gtklock con -b, relativa a la carpeta de la prueba (None si no recibió -b)."""
        self.assertEqual(len(llamadas), 1, llamadas)
        partes = llamadas[0].split(" ")
        if "-b" not in partes:
            return None
        return os.path.relpath(partes[partes.index("-b") + 1], self.tmp)

    def test_bloquea_con_el_fondo_elegido_para_la_resolucion_del_monitor(self):
        self.catalogo(resoluciones=("1920x1080", "1280x720"))
        self.ajuste("fondo-bloqueo", "lago")
        self.assertEqual(self.fondo_de(self.bloquea()), "share/fondos/lago/bloqueo-1920x1080.jpg")
        self.assertFalse(os.path.exists(self.swaylock))

    def test_la_resolucion_sale_de_hyprland_con_su_escala(self):
        self.catalogo(resoluciones=("1920x1080", "1280x720"))
        self.ajuste("fondo-bloqueo", "lago")
        # 2560x1440 con escala 2 → 1280x720 lógicos
        self.assertEqual(self.fondo_de(self.bloquea(monitor={"width": 2560, "height": 1440, "scale": 2})),
                         "share/fondos/lago/bloqueo-1280x720.jpg")

    def test_usa_la_resolucion_propia_de_un_fondo_del_usuario(self):
        self.catalogo()
        self.fondo_usuario("u-mio", resoluciones=("1920x1080", "1536x864"))
        self.ajuste("fondo-bloqueo", "u-mio")
        self.assertEqual(self.fondo_de(self.bloquea(monitor={"width": 1536, "height": 864})),
                         "datos/infinity/fondos/u-mio/bloqueo-1536x864.jpg")

    def test_fondo_borrado_bloquea_con_islandia(self):
        self.catalogo(ids=("islandia",))
        self.ajuste("fondo-bloqueo", "u-ya-no-existe")
        self.assertEqual(self.fondo_de(self.bloquea()), "share/fondos/islandia/bloqueo-1920x1080.jpg")
        self.assertFalse(os.path.exists(self.swaylock))

    def test_sin_ajuste_bloquea_con_islandia(self):
        self.catalogo(ids=("islandia",))
        self.assertEqual(self.fondo_de(self.bloquea()), "share/fondos/islandia/bloqueo-1920x1080.jpg")

    def test_ajuste_raro_o_malicioso_no_rompe_el_bloqueo(self):
        self.catalogo(ids=("islandia",))
        for contenido in ("../../../../etc/passwd", "'; touch /tmp/pwned; '", "$(touch x)", "\x00\x01", "a" * 5000, "-b /etc/shadow"):
            self.ajuste("fondo-bloqueo", contenido)
            os.path.exists(self.llamadas) and os.remove(self.llamadas)
            self.mata()
            self.assertEqual(self.fondo_de(self.bloquea()), "share/fondos/islandia/bloqueo-1920x1080.jpg", repr(contenido))
        self.assertFalse(os.path.exists("/tmp/pwned"))

    def test_fondo_danado_cae_a_islandia(self):
        self.catalogo(ids=("islandia", "lago"))
        self.ajuste("fondo-bloqueo", "lago")
        for datos in (b"", b"no es un jpeg"):
            for r in ("1920x1080", "1280x720"):
                escribe(os.path.join(self.fondos, "lago", f"bloqueo-{r}.jpg"), datos)
            os.path.exists(self.llamadas) and os.remove(self.llamadas)
            self.mata()
            self.assertEqual(self.fondo_de(self.bloquea()), "share/fondos/islandia/bloqueo-1920x1080.jpg")

    def test_sin_catalogo_usa_el_fondo_antiguo(self):
        self.legado()
        self.ajuste("fondo-bloqueo", "lago")
        self.assertEqual(self.fondo_de(self.bloquea()), "share/lock/fondos/wallpaper-lock-1920x1080.jpg")

    def test_si_falta_el_script_de_eleccion_tambien_usa_el_antiguo(self):
        self.catalogo()
        self.legado()
        self.ajuste("fondo-bloqueo", "lago")
        vacio = os.path.join(self.tmp, "sin-libexec")
        os.makedirs(vacio)
        self.assertEqual(self.fondo_de(self.bloquea(libexec=vacio)), "share/lock/fondos/wallpaper-lock-1920x1080.jpg")

    def test_si_no_hay_ninguna_imagen_bloquea_igual_sin_fondo(self):
        # Lo importante es que la sesión quede bloqueada: sin imagen, gtklock arranca sin -b.
        self.ajuste("fondo-bloqueo", "lago")
        llamadas = self.bloquea()
        self.assertIsNone(self.fondo_de(llamadas))
        self.assertIn("-x", llamadas[0])
        self.assertFalse(os.path.exists(self.swaylock))

    def test_el_bloqueo_de_arranque_tambien_usa_el_fondo_elegido(self):
        self.catalogo()
        self.ajuste("fondo-bloqueo", "lago")
        env = self.entorno(PATH=self.bin + os.pathsep + os.environ.get("PATH", "/usr/bin:/bin"),
                           XDG_CACHE_HOME=self.cache, INFINITY_BLOQUEO_ARRANQUE="1", XDG_RUNTIME_DIR=self.cache)
        self.script(os.path.join(self.bin, "hyprctl"), "#!/bin/sh\necho '[]'\n")
        p = subprocess.Popen(["sh", LOCK, "--arranque"], env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
        self.lanzados.append(p)
        self.assertEqual(p.wait(timeout=30), 0)
        llamadas = lineas(self.llamadas)
        self.assertEqual(self.fondo_de(llamadas), "share/fondos/lago/bloqueo-1920x1080.jpg")


class TestFicherosDelSistema(unittest.TestCase):
    def test_scripts_ejecutables_y_con_sintaxis_valida(self):
        for ruta in (FONDO_RUTA, FONDO_MODO, WALLPAPER, LOCK):
            self.assertTrue(os.access(ruta, os.X_OK), ruta)
            if shutil.which("sh"):
                self.assertEqual(subprocess.run(["sh", "-n", ruta]).returncode, 0, ruta)

    def test_build_instala_el_catalogo_y_conserva_el_respaldo(self):
        with open(os.path.join(ROOT, "build", "build.sh"), encoding="utf-8") as f:
            build = f.read()
        self.assertIn("usr/share/infinity/fondos", build)
        self.assertIn("branding/fondos/", build)
        self.assertIn("branding/wallpaper.jpg", build)
        self.assertIn("branding/fondos-bloqueo", build)

    def test_webp_en_la_lista_de_paquetes(self):
        paquetes = [l.split("#")[0].strip() for l in lineas(os.path.join(ROOT, "build", "packages.list"))]
        self.assertIn("webp-pixbuf-loader", paquetes)
        self.assertIn("librsvg2-common", paquetes)


if __name__ == "__main__":
    unittest.main()
