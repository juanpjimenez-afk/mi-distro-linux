"""Los iconos de la Tienda (13 SVG): existen, son sencillos y robustos, y el generador los reproduce al byte.

Sin pantalla. La parte de píxeles (a 48 px no salen vacíos ni iguales entre sí, la sombra cabe en el lienzo y la
baldosa mide lo mismo que la de Contraseñas) se salta si no hay con qué rasterizar: GdkPixbuf con el cargador de SVG
(python3-gi + librsvg2-common, lo que usa el sistema) o, si no, rsvg-convert (librsvg2-bin).
"""
import functools
import importlib.util
import itertools
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
import zlib

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
BASE = os.path.join(ROOT, "overlay", "usr", "share", "infinity")
CARPETA = os.path.join(BASE, "tienda", "iconos")
GENERADOR = os.path.join(ROOT, "branding", "make_tienda_iconos.py")
SVG = "{http://www.w3.org/2000/svg}"

# los ids de tienda/catalogo.json y, aparte, el icono de la propia Tienda (overlay/usr/share/infinity/tienda.svg)
IDS = ("spotify", "antigravity", "netflix", "hbo-max", "disney-plus", "claude", "gemini", "notebooklm", "classroom",
       "youtube", "chrome", "brave")
TODOS = IDS + ("tienda",)
PROHIBIDOS = {"script", "image", "text", "tspan", "foreignObject", "filter", "iframe"}
FONDO = (0x12, 0x16, 0x2C)          # el fondo oscuro de Infinity, sobre el que se miran los iconos


def _ruta(id_):
    return os.path.join(BASE, "tienda.svg") if id_ == "tienda" else os.path.join(CARPETA, id_ + ".svg")


def _bytes(ruta):
    with open(ruta, "rb") as f:
        return f.read()


def _leer(id_):
    return _bytes(_ruta(id_)).decode("utf-8")


def _importar_generador():
    spec = importlib.util.spec_from_file_location("make_tienda_iconos", GENERADOR)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


# ------------------------------------------------------------------------------------------------ rasterizado
def _leer_png(datos):
    """(ancho, alto, bytes RGBA) de un PNG de 8 bits RGBA sin entrelazar (lo que escribe cairo), o None."""
    if datos[:8] != b"\x89PNG\r\n\x1a\n":
        return None
    pos, idat, cabecera = 8, b"", None
    while pos < len(datos):
        largo, tipo = struct.unpack(">I4s", datos[pos:pos + 8])
        cuerpo = datos[pos + 8:pos + 8 + largo]
        pos += 12 + largo
        if tipo == b"IHDR":
            cabecera = struct.unpack(">IIBBBBB", cuerpo)
        elif tipo == b"IDAT":
            idat += cuerpo
    ancho, alto, profundidad, color, _, _, entrelazado = cabecera
    if (profundidad, color, entrelazado) != (8, 6, 0):
        return None
    crudo, fila, salida, previa = zlib.decompress(idat), ancho * 4, bytearray(), bytearray(ancho * 4)
    for y in range(alto):
        filtro = crudo[y * (fila + 1)]
        linea = bytearray(crudo[y * (fila + 1) + 1:(y + 1) * (fila + 1)])
        for i in range(fila):
            izq = linea[i - 4] if i >= 4 else 0
            arriba = previa[i]
            if filtro == 1:
                linea[i] = (linea[i] + izq) & 255
            elif filtro == 2:
                linea[i] = (linea[i] + arriba) & 255
            elif filtro == 3:
                linea[i] = (linea[i] + ((izq + arriba) >> 1)) & 255
            elif filtro == 4:
                diag = previa[i - 4] if i >= 4 else 0
                p = izq + arriba - diag
                pa, pb, pc = abs(p - izq), abs(p - arriba), abs(p - diag)
                linea[i] = (linea[i] + (izq if pa <= pb and pa <= pc else arriba if pb <= pc else diag)) & 255
        salida += linea
        previa = linea
    return ancho, alto, bytes(salida)


def _con_gdkpixbuf(ruta, px):
    import gi
    gi.require_version("GdkPixbuf", "2.0")
    from gi.repository import GdkPixbuf
    pb = GdkPixbuf.Pixbuf.new_from_file_at_size(ruta, px, px)
    ancho, alto, canales = pb.get_width(), pb.get_height(), pb.get_n_channels()
    paso, datos = pb.get_rowstride(), pb.get_pixels()
    rgba = bytearray()
    for y in range(alto):
        fila = datos[y * paso:y * paso + ancho * canales]
        if canales == 4:
            rgba += fila
        else:
            for x in range(ancho):
                rgba += fila[x * 3:x * 3 + 3] + b"\xff"
    return ancho, alto, bytes(rgba)


def _con_rsvg_convert(ruta, px):
    exe = shutil.which("rsvg-convert")
    if not exe:
        return None
    png = subprocess.run([exe, "-w", str(px), "-h", str(px), "-f", "png", ruta], capture_output=True, check=True,
                         timeout=60).stdout
    return _leer_png(png)


@functools.lru_cache(maxsize=None)
def _rasterizar(ruta, px):
    """(ancho, alto, bytes RGBA) del SVG a px x px con GdkPixbuf o, si no, con rsvg-convert; None si no hay ninguno."""
    for intento in (_con_gdkpixbuf, _con_rsvg_convert):
        try:
            resultado = intento(ruta, px)
        except Exception:   # noqa: BLE001 - sin gi, sin cargador de SVG, sin rsvg-convert…: se prueba el siguiente
            continue
        if resultado:
            return resultado
    return None


def _alfa(imagen, x, y):
    ancho, _alto, rgba = imagen
    return rgba[(y * ancho + x) * 4 + 3]


def _sobre_fondo(imagen):
    """Los canales R, G, B (0-255) de la imagen compuesta sobre el fondo oscuro de Infinity."""
    _ancho, _alto, rgba = imagen
    salida = []
    for i in range(0, len(rgba), 4):
        a = rgba[i + 3] / 255
        salida += [round(rgba[i + c] * a + FONDO[c] * (1 - a)) for c in range(3)]
    return salida


def _extension(imagen):
    """(x0, x1, y0, y1) de lo opaco (alfa > 128) por la fila y la columna centrales: lo que mide la baldosa."""
    ancho, alto, _ = imagen
    fila = [x for x in range(ancho) if _alfa(imagen, x, alto // 2) > 128]
    columna = [y for y in range(alto) if _alfa(imagen, ancho // 2, y) > 128]
    return fila[0], fila[-1], columna[0], columna[-1]


# ------------------------------------------------------------------------------------------------ pruebas
class TestFicheros(unittest.TestCase):
    def test_estan_los_13_iconos(self):
        self.assertEqual(len(TODOS), 13)
        for id_ in TODOS:
            with self.subTest(icono=id_):
                self.assertTrue(os.path.isfile(_ruta(id_)), f"falta {_ruta(id_)}")

    def test_cada_uno_pesa_menos_de_12_kb(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                self.assertLess(os.path.getsize(_ruta(id_)), 12 * 1024)

    def test_cada_icono_se_dibuja_distinto(self):
        self.assertEqual(len({_leer(i) for i in TODOS}), len(TODOS))


class TestSvgSencillo(unittest.TestCase):
    def test_son_xml_valido_de_256_con_su_titulo(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                raiz = ET.parse(_ruta(id_)).getroot()
                self.assertEqual(raiz.tag, SVG + "svg")
                self.assertEqual(raiz.get("viewBox"), "0 0 256 256")
                self.assertEqual((raiz.get("width"), raiz.get("height")), ("256", "256"))
                self.assertTrue((raiz.findtext(SVG + "title") or "").strip(), "sin <title>")

    def test_sin_scripts_imagenes_texto_ni_filtros(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                texto = _leer(id_)
                for etiqueta in sorted(PROHIBIDOS):
                    self.assertNotIn("<" + etiqueta, texto)
                self.assertNotIn("font-family", texto)      # las letras son trazos, no tipografía
                for e in ET.fromstring(texto).iter():
                    self.assertNotIn(e.tag.replace(SVG, ""), PROHIBIDOS)
                    for atributo in e.attrib:
                        self.assertFalse(atributo.lower().startswith("on"), f"manejador de evento: {atributo}")

    def test_sin_referencias_externas(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                texto = _leer(id_)
                self.assertIsNone(re.search(r'(href|src)\s*=\s*"(?!#)', texto), "href o src externo")
                self.assertIsNone(re.search(r"""url\(\s*['"]?(?!#)""", texto), "url() que no apunta a un id interno")
                propio = texto.replace('xmlns="http://www.w3.org/2000/svg"', "")
                for esquema in ("http:", "https:", "file:", "ftp:", "data:"):
                    self.assertNotIn(esquema, propio)

    def test_ids_unicos_con_prefijo_y_todas_las_referencias_resuelven(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                texto = _leer(id_)
                ids = [e.get("id") for e in ET.fromstring(texto).iter() if e.get("id")]
                self.assertTrue(ids, "ni siquiera el degradado de la baldosa")
                self.assertEqual(len(ids), len(set(ids)), f"ids repetidos: {sorted(ids)}")
                usados = set(re.findall(r"url\(#([^)]+)\)", texto))
                self.assertEqual(usados - set(ids), set(), "url(#…) que no existe")
                self.assertEqual(set(ids) - usados, set(), "degradados definidos y sin usar")
                for i in ids:
                    self.assertTrue(i.startswith(id_ + "-"), f"el id «{i}» debe llevar el prefijo «{id_}-»")

    def test_pocos_decimales(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                self.assertIsNone(re.search(r"\d\.\d{3,}", _leer(id_)), "números con más de dos decimales")


class TestGenerador(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.M = _importar_generador()

    def test_genera_exactamente_los_13_ids(self):
        self.assertEqual(set(self.M.rutas("/x")), set(TODOS))
        self.assertEqual(set(self.M.rutas("/x").values()), {os.path.join("/x", "tienda.svg")} |
                         {os.path.join("/x", "tienda", "iconos", i + ".svg") for i in IDS})

    def test_los_ficheros_del_repo_son_lo_que_escribe_el_generador(self):
        with tempfile.TemporaryDirectory() as tmp:
            escritos = self.M.escribir(tmp)
            for id_ in TODOS:
                with self.subTest(icono=id_):
                    self.assertEqual(_bytes(escritos[id_]), _bytes(_ruta(id_)),
                                     "SVG desactualizado: python3 branding/make_tienda_iconos.py")

    def test_es_determinista_e_idempotente(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            self.M.escribir(a)
            primera = {i: _bytes(r) for i, r in self.M.rutas(a).items()}
            self.M.escribir(a)                      # otra vez sobre lo ya escrito
            self.M.escribir(b)                      # y en otra carpeta
            for id_ in TODOS:
                with self.subTest(icono=id_):
                    self.assertEqual(_bytes(self.M.rutas(a)[id_]), primera[id_])
                    self.assertEqual(_bytes(self.M.rutas(b)[id_]), primera[id_])
                    self.assertTrue(primera[id_].endswith(b"</svg>\n"))
                    self.assertNotIn(b"\r", primera[id_])

    def test_la_linea_de_comandos_da_los_mismos_bytes_con_cualquier_semilla_de_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            for semilla in ("0", "12345"):
                destino = os.path.join(tmp, semilla)
                env = dict(os.environ, PYTHONHASHSEED=semilla, PYTHONDONTWRITEBYTECODE="1")
                r = subprocess.run([sys.executable, GENERADOR, "--destino", destino], env=env, capture_output=True,
                                   text=True, timeout=120)
                self.assertEqual(r.returncode, 0, r.stderr)
                for id_ in TODOS:
                    with self.subTest(icono=id_, semilla=semilla):
                        self.assertEqual(_bytes(self.M.rutas(destino)[id_]), _bytes(_ruta(id_)))


class TestPixeles(unittest.TestCase):
    """Lo que se ve de verdad. Se salta si no hay GdkPixbuf con cargador de SVG ni rsvg-convert."""

    @classmethod
    def setUpClass(cls):
        if _rasterizar(_ruta(TODOS[0]), 48) is None:
            raise unittest.SkipTest("sin rasterizador de SVG (GdkPixbuf con librsvg, o rsvg-convert)")

    def _imagen(self, ruta, px):
        """La imagen del SVG; si hay rasterizador (ya se comprobó) y este SVG no se deja, es que está mal."""
        imagen = _rasterizar(ruta, px)
        self.assertIsNotNone(imagen, f"no se puede rasterizar {os.path.basename(ruta)}")
        return imagen

    def test_a_48_px_no_salen_vacios_ni_uniformes(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                imagen = self._imagen(_ruta(id_), 48)
                ancho, alto, rgba = imagen
                self.assertEqual((ancho, alto), (48, 48))
                opacos = [rgba[i:i + 3] for i in range(0, len(rgba), 4) if rgba[i + 3] > 200]
                # la baldosa ocupa ~81 % del lienzo y deja las esquinas transparentes
                self.assertGreater(len(opacos), 0.6 * 48 * 48, "casi vacío")
                self.assertLess(len(opacos), 0.95 * 48 * 48, "sin esquinas redondeadas")
                self.assertLess(_alfa(imagen, 0, 0), 8)
                self.assertEqual(_alfa(imagen, 24, 24), 255)
                colores = {(r >> 4, g >> 4, b >> 4) for r, g, b in opacos}
                self.assertGreaterEqual(len(colores), 12, f"demasiado uniforme: {len(colores)} colores")
                luma = [0.299 * r + 0.587 * g + 0.114 * b for r, g, b in opacos]
                media = sum(luma) / len(luma)
                desviacion = (sum((v - media) ** 2 for v in luma) / len(luma)) ** 0.5
                self.assertGreater(desviacion, 15, f"sin contraste: desviación de luminosidad {desviacion:.1f}")

    def test_se_distinguen_entre_si_a_48_px(self):
        compuestos = {i: _sobre_fondo(self._imagen(_ruta(i), 48)) for i in TODOS}
        for a, b in itertools.combinations(TODOS, 2):
            with self.subTest(a=a, b=b):
                diferencia = sum(abs(x - y) for x, y in zip(compuestos[a], compuestos[b])) / len(compuestos[a])
                self.assertGreater(diferencia, 12, f"se parecen demasiado ({diferencia:.1f} sobre 255)")

    def test_la_sombra_cabe_dentro_del_lienzo(self):
        for id_ in TODOS:
            with self.subTest(icono=id_):
                imagen = self._imagen(_ruta(id_), 256)
                ancho, alto, _ = imagen
                perimetro = ([_alfa(imagen, x, y) for x in range(ancho) for y in (0, alto - 1)] +
                             [_alfa(imagen, x, y) for y in range(alto) for x in (0, ancho - 1)])
                self.assertLessEqual(max(perimetro), 2, "algo toca el borde del lienzo: la sombra se sale")

    def test_la_baldosa_hace_pareja_con_la_de_contrasenas(self):
        boveda = os.path.join(BASE, "boveda.svg")
        if not os.path.isfile(boveda):
            self.skipTest("no está boveda.svg")
        referencia = _extension(self._imagen(boveda, 256))
        for id_ in TODOS:
            with self.subTest(icono=id_):
                for medida, esperada in zip(_extension(self._imagen(_ruta(id_), 256)), referencia):
                    self.assertAlmostEqual(medida, esperada, delta=1)


if __name__ == "__main__":
    unittest.main()
