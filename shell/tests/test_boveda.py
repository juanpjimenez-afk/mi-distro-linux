"""La bóveda de contraseñas (boveda.py), SIN pantalla: formato, cifrado, ficheros, generador y fortaleza.

Las pruebas que cifran o descifran se saltan si no está el paquete `cryptography` (en la ISO es
python3-cryptography); las del generador, el medidor, el portapapeles y la instalación corren siempre. Todos los
datos son FALSOS. Para que las pruebas vayan rápido usan scrypt con N=2**10 (parámetros que SOLO existen aquí:
la aplicación nunca los pasa); un par de pruebas usan los parámetros reales.
"""
import ast
import configparser
import errno
import gc
import os
import stat
import struct
import subprocess
import sys
import tempfile
import time
import traceback
import unittest
import weakref
import xml.etree.ElementTree as ET
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import boveda as B  # noqa: E402  (no importa GTK; sin cryptography se importa igual)

try:
    import cryptography  # noqa: F401
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
except ImportError:
    cryptography = AESGCM = None

KDF_T = B.ParamsKDF(10, 8, 1)
LIM_T = B.LimitesKDF(log2n=(10, 20))

MAESTRA = "maestra-de-prueba-1"
OTRA = "otra-maestra-de-prueba-2"
MARCA = "MARCA-SECRETA-93af"


def _leer_fichero(ruta):
    with open(ruta, "rb") as f:
        return f.read()


def _base(self):
    tmp = tempfile.TemporaryDirectory()
    self.addCleanup(tmp.cleanup)
    self.tmp = tmp.name
    self.carpeta = os.path.join(self.tmp, "infinity", "boveda")
    self.ruta = os.path.join(self.carpeta, "boveda.enc")


@unittest.skipUnless(cryptography, "necesita python3-cryptography")
class BaseBoveda(unittest.TestCase):
    def setUp(self):
        _base(self)

    def nueva(self, **kw):
        kw.setdefault("kdf", KDF_T)
        kw.setdefault("limites", LIM_T)
        return B.Boveda(self.ruta, **kw)

    def creada(self, password=MAESTRA, n=0):
        b = self.nueva()
        b.crear(password)
        for i in range(n):
            b.agregar(f"Servicio {i}", f"usuario{i}@ejemplo.test", f"clave-falsa-{i}")
        return b

    def abierta(self, password=MAESTRA):
        b = self.nueva()
        b.abrir(password)
        return b

    def bytes_(self, nombre="boveda.enc"):
        with open(os.path.join(self.carpeta, nombre), "rb") as f:
            return f.read()

    def escribir_bytes(self, datos, nombre="boveda.enc"):
        ruta = os.path.join(self.carpeta, nombre)
        with open(ruta, "wb") as f:
            f.write(datos)
        os.chmod(ruta, 0o600)

    def modo(self, ruta):
        return stat.S_IMODE(os.stat(ruta).st_mode)

    def archivos(self):
        return sorted(os.listdir(self.carpeta))


class TestIdaYVuelta(BaseBoveda):
    def test_crear_guardar_abrir(self):
        b = self.creada()
        a = b.agregar("Gmail", "ana@ejemplo.test", "clave-falsa-1", "https://mail.ejemplo.test", "nota falsa", "Correo")
        r = b.agregar("Router de casa", "admin", "clave-falsa-2", "192.168.1.1", "", "Router y red")
        b.bloquear()
        c = self.abierta()
        self.assertEqual([e.servicio for e in c.entradas], ["Gmail", "Router de casa"])
        self.assertEqual(c.obtener(a.id), a)
        self.assertEqual(c.obtener(r.id).url, "192.168.1.1")
        self.assertEqual(c.obtener(a.id).contrasena, "clave-falsa-1")
        self.assertEqual(c.obtener(a.id).categoria, "Correo")

    def test_actualizar_y_borrar_persisten(self):
        b = self.creada(n=3)
        e, otra = b.entradas[1], b.entradas[0]
        nueva = b.actualizar(e.id, "Nombre nuevo", "otro", "otra-clave-falsa", "10.0.0.1", "n", "Web")
        self.assertEqual(nueva.id, e.id)
        self.assertEqual(nueva.creada, e.creada)
        b.borrar(otra.id)
        b.bloquear()
        c = self.abierta()
        self.assertEqual(len(c.entradas), 2)
        self.assertEqual(c.obtener(e.id).servicio, "Nombre nuevo")
        self.assertEqual(c.obtener(e.id).categoria, "Web")

    def test_fechas_y_ids(self):
        t = iter(["2026-10-20T10:00:00Z", "2026-10-21T11:30:00Z", "2026-10-22T12:00:00Z"])
        b = self.nueva(ahora=lambda: next(t))
        b.crear(MAESTRA)
        e = b.agregar("Uno")
        self.assertEqual((e.creada, e.modificada), ("2026-10-20T10:00:00Z", "2026-10-20T10:00:00Z"))
        e2 = b.actualizar(e.id, "Uno bis")
        self.assertEqual((e2.creada, e2.modificada), ("2026-10-20T10:00:00Z", "2026-10-21T11:30:00Z"))
        self.assertRegex(e.id, r"^[0-9a-f]{16}$")
        self.assertNotEqual(e.id, b.agregar("Dos").id)

    def test_bloqueada_no_da_nada(self):
        b = self.creada(n=1)
        b.bloquear()
        for f in (lambda: b.entradas, lambda: b.agregar("x"), lambda: b.buscar("x"), lambda: b.exportar(self.tmp + "/x"),
                  lambda: b.cambiar_password(MAESTRA, OTRA), lambda: b.contar_por_categoria()):
            with self.assertRaises(B.BovedaError):
                f()

    def test_unicode_en_los_campos(self):
        b = self.creada()
        e = b.agregar("Café ☕ — Ñandú", "usuário", "pässwörd-falso-日本語-😀", "http://ejemplo.test/ñ", "línea 1\nlínea 2")
        b.bloquear()
        self.assertEqual(self.abierta().obtener(e.id), e)

    def test_normalizacion_nfkc_de_la_maestra(self):
        compuesta, descompuesta = "contraseña-larga", "contraseña-larga"
        self.assertNotEqual(compuesta, descompuesta)
        b = self.creada(compuesta)
        b.bloquear()
        self.abierta(descompuesta)  # no lanza: es la misma contraseña

    def test_relleno_oculta_cuantas_entradas_hay(self):
        a = self.creada()
        tam0 = len(self.bytes_())
        a.agregar("Uno", "u", "p")
        self.assertEqual(len(self.bytes_()), tam0)
        self.assertEqual((tam0 - B.LONG_CAB - B.LONG_NONCE - B.LONG_TAG) % B.BLOQUE_RELLENO, 0)

    def test_parametros_reales_de_scrypt(self):
        """El único test con los parámetros de verdad: N=2**16, r=8, p=1, y razonablemente rápido."""
        ruta = os.path.join(self.tmp, "real", "boveda.enc")
        b = B.Boveda(ruta)
        t = time.monotonic()
        b.crear(MAESTRA)
        b.agregar("Gmail", "ana@ejemplo.test", "clave-falsa")
        b.bloquear()
        c = B.Boveda(ruta)
        c.abrir(MAESTRA)
        self.assertLess(time.monotonic() - t, 15)
        self.assertEqual([e.servicio for e in c.entradas], ["Gmail"])
        with open(ruta, "rb") as f:
            magia, version, kdf, log2n, r, p, sal, verif = struct.unpack(B._FORMATO_CAB, f.read(B.LONG_CAB))
        self.assertEqual((magia, version, kdf, log2n, r, p, len(sal), len(verif)), (b"INFBOVED", 1, 1, 16, 8, 1, 16, 32))
        self.assertEqual(B.KDF_POR_DEFECTO, B.ParamsKDF(16, 8, 1))
        with self.assertRaises(B.ClaveIncorrecta):
            B.Boveda(ruta).abrir(OTRA)


class TestClaveIncorrecta(BaseBoveda):
    def test_contrasena_incorrecta_no_es_fichero_danado(self):
        self.creada(n=1).bloquear()
        for intento in ("equivocada-1", "", " ", MAESTRA.upper(), MAESTRA + " ", "x" * 5000):
            with self.subTest(intento=intento[:12]):
                b = self.nueva()
                with self.assertRaises(B.ClaveIncorrecta) as cm:
                    b.abrir(intento)
                self.assertNotIsInstance(cm.exception, B.BovedaDanada)
                self.assertFalse(b.abierta)

    def test_ni_dato_que_no_es_texto(self):
        self.creada().bloquear()
        for malo in (None, 12345, b"bytes"):
            with self.assertRaises(B.ClaveIncorrecta):
                self.nueva().abrir(malo)

    def test_danada_no_es_clave_incorrecta(self):
        self.creada().bloquear()
        datos = bytearray(self.bytes_())
        datos[-1] ^= 1  # la etiqueta GCM
        self.escribir_bytes(bytes(datos))
        with self.assertRaises(B.BovedaDanada) as cm:
            self.nueva().abrir(MAESTRA)  # la contraseña es la buena: lo dañado es el fichero
        self.assertNotIsInstance(cm.exception, B.ClaveIncorrecta)

    def test_un_fallo_no_cambia_el_fichero(self):
        self.creada(n=2).bloquear()
        antes = self.bytes_()
        with self.assertRaises(B.ClaveIncorrecta):
            self.nueva().abrir(OTRA)
        self.assertEqual(self.bytes_(), antes)
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"])


class TestManipulacion(BaseBoveda):
    def setUp(self):
        super().setUp()
        self.creada(n=3).bloquear()
        self.original = self.bytes_()

    def abrir_con(self, datos):
        self.escribir_bytes(bytes(datos))
        b = self.nueva()
        try:
            b.abrir(MAESTRA)
        except B.BovedaError as e:
            self.assertFalse(b.abierta)
            return e
        self.fail("abrió un fichero manipulado")

    def test_el_original_abre(self):
        self.escribir_bytes(self.original)
        self.abierta()

    def test_cualquier_byte_de_la_cabecera_alterado_siempre_da_error(self):
        for i in range(B.LONG_CAB):
            for mascara in (0x01, 0x80):
                with self.subTest(byte=i, mascara=mascara):
                    datos = bytearray(self.original)
                    datos[i] ^= mascara
                    self.assertIsInstance(self.abrir_con(datos), B.BovedaError)

    def test_cualquier_byte_del_nonce_alterado_siempre_da_error(self):
        for i in range(B.LONG_CAB, B.LONG_CAB + B.LONG_NONCE):
            with self.subTest(byte=i):
                datos = bytearray(self.original)
                datos[i] ^= 0x01
                self.assertIsInstance(self.abrir_con(datos), B.BovedaDanada)

    def test_el_cifrado_alterado_da_danada_en_cualquier_posicion(self):
        ini = B.LONG_CAB + B.LONG_NONCE
        posiciones = list(range(ini, len(self.original), 97)) + [ini, len(self.original) - 1, len(self.original) - 16]
        for i in posiciones:
            with self.subTest(byte=i):
                datos = bytearray(self.original)
                datos[i] ^= 0x40
                self.assertIsInstance(self.abrir_con(datos), B.BovedaDanada)

    def test_fichero_truncado(self):
        for n in (0, 1, 8, 20, B.LONG_CAB - 1, B.LONG_CAB, B.LONG_CAB + B.LONG_NONCE, B.LONG_CAB + B.LONG_NONCE + 15,
                  B.LONG_CAB + B.LONG_NONCE + 16, len(self.original) - 1, len(self.original) // 2):
            with self.subTest(longitud=n):
                self.assertIsInstance(self.abrir_con(self.original[:n]), B.BovedaDanada)

    def test_fichero_con_basura_al_final(self):
        self.assertIsInstance(self.abrir_con(self.original + b"\x00"), B.BovedaDanada)

    def test_fichero_aleatorio_y_vacio(self):
        for datos in (b"", os.urandom(300), b"texto cualquiera" * 20):
            self.assertIsInstance(self.abrir_con(datos), B.BovedaDanada)

    def test_magia_desconocida(self):
        datos = bytearray(self.original)
        datos[:8] = b"OTRACOSA"
        e = self.abrir_con(datos)
        self.assertIsInstance(e, B.VersionNoSoportada)
        self.assertIn("no es una bóveda", str(e))

    def test_version_desconocida(self):
        for version in (0, 2, 7, 65535):
            datos = bytearray(self.original)
            datos[8:10] = struct.pack(">H", version)
            e = self.abrir_con(datos)
            self.assertIsInstance(e, B.VersionNoSoportada)
            self.assertIn(f"versión {version}", str(e))

    def test_kdf_desconocido(self):
        datos = bytearray(self.original)
        datos[10] = 9
        self.assertIsInstance(self.abrir_con(datos), B.VersionNoSoportada)

    def test_parametros_de_kdf_fuera_de_rango_se_rechazan_sin_derivar(self):
        # Los de la tabla están fuera de LIM_T (N 2**10..2**20, r 8..16, p 1..4) o pasan la memoria máxima
        casos = [("log2n", 9), ("log2n", 21), ("log2n", 255), ("log2n", 0), ("r", 7), ("r", 17), ("r", 255), ("r", 0),
                 ("p", 0), ("p", 5), ("p", 255)]
        posicion = {"log2n": 11, "r": 12, "p": 13}
        for nombre, valor in casos:
            with self.subTest(nombre=nombre, valor=valor), \
                    mock.patch.object(B, "derivar_clave", side_effect=AssertionError("derivó con parámetros no válidos")):
                datos = bytearray(self.original)
                datos[posicion[nombre]] = valor
                self.assertIsInstance(self.abrir_con(datos), B.BovedaDanada)

    def test_limites_reales_de_produccion(self):
        """Con los límites de producción: N entre 2**14 y 2**20, r 8..16, p 1..4 y como mucho 1 GiB."""
        lim = B.LIMITES_POR_DEFECTO
        self.assertEqual((lim.log2n, lim.r, lim.p, lim.memoria_max), ((14, 20), (8, 16), (1, 4), 1 << 30))
        for params in (B.ParamsKDF(13, 8, 1), B.ParamsKDF(10, 8, 1), B.ParamsKDF(21, 8, 1), B.ParamsKDF(16, 7, 1),
                       B.ParamsKDF(16, 8, 0), B.ParamsKDF(16, 8, 5), B.ParamsKDF(20, 16, 1)):
            with self.subTest(params=params), self.assertRaises(B.BovedaDanada):
                lim.comprobar(params)
        for params in (B.ParamsKDF(14, 8, 1), B.ParamsKDF(16, 8, 1), B.ParamsKDF(20, 8, 1), B.ParamsKDF(17, 16, 4)):
            lim.comprobar(params)

    def test_una_boveda_debil_creada_en_pruebas_no_abre_con_los_limites_de_produccion(self):
        self.escribir_bytes(self.original)  # N=2**10: un atacante la usaría para rebajar el coste
        with self.assertRaises(B.BovedaDanada):
            B.Boveda(self.ruta).abrir(MAESTRA)

    def test_fichero_enorme_se_rechaza_sin_leerlo(self):
        with open(os.path.join(self.carpeta, "boveda.enc"), "r+b") as f:
            f.truncate(B.MAX_FICHERO + 4096)
        with mock.patch("os.read", side_effect=AssertionError("leyó un fichero enorme")):
            with self.assertRaises(B.BovedaDanada):
                self.nueva().abrir(MAESTRA)

    def test_json_no_valido_dentro_del_cifrado_da_danada(self):
        sal = self.original[B.LONG_CAB - 48:B.LONG_CAB - 32]
        clave = B.derivar_clave(MAESTRA, sal, KDF_T)
        cab = self.original[:B.LONG_CAB]
        casos = {
            "no es json": b"esto no es json" + MARCA.encode(),
            "version distinta": b'{"v":2,"entradas":[]}',
            "sin entradas": b'{"v":1}',
            "claves extra": b'{"v":1,"entradas":[],"extra":1}',
            "no es lista": b'{"v":1,"entradas":{}}',
            "no es objeto": b'[1,2,3]',
            "utf8 roto": b'{"v":1,"entradas":[]}\xff\xfe',
            "entrada no objeto": b'{"v":1,"entradas":[5]}',
            "entrada con campos de menos": b'{"v":1,"entradas":[{"id":"0123456789abcdef"}]}',
        }
        valida = ('{"id":"%s","servicio":"S","usuario":"","contrasena":"","url":"","notas":"",'
                  '"categoria":"%s","creada":"2026-01-01T00:00:00Z","modificada":"2026-01-01T00:00:00Z"}')
        casos["categoria desconocida"] = ('{"v":1,"entradas":[%s]}' % (valida % ("0123456789abcdef", "Rara"))).encode()
        casos["id duplicado"] = ('{"v":1,"entradas":[%s,%s]}' % ((valida % ("0123456789abcdef", "Web"),) * 2)).encode()
        casos["id no hex"] = ('{"v":1,"entradas":[%s]}' % (valida % ("../../etc/pass", "Web"))).encode()
        casos["campo demasiado largo"] = (('{"v":1,"entradas":[%s]}' % (valida % ("0123456789abcdef", "Web")))
                                          .replace('"servicio":"S"', '"servicio":"%s"' % ("x" * 500))).encode()
        casos["fecha mala"] = (('{"v":1,"entradas":[%s]}' % (valida % ("0123456789abcdef", "Web")))
                               .replace("2026-01-01T00:00:00Z", "ayer", 1)).encode()
        casos["tipo malo"] = (('{"v":1,"entradas":[%s]}' % (valida % ("0123456789abcdef", "Web")))
                              .replace('"usuario":""', '"usuario":5')).encode()
        for nombre, plano in casos.items():
            with self.subTest(nombre):
                nonce = os.urandom(12)
                datos = cab + nonce + AESGCM(clave).encrypt(nonce, plano, cab)
                e = self.abrir_con(datos)
                self.assertIsInstance(e, B.BovedaDanada)
                self.assertNotIn(MARCA, str(e) + repr(e))


class TestFicheros(BaseBoveda):
    def test_permisos(self):
        b = self.creada(n=2)
        b.agregar("otra")
        self.assertEqual(self.modo(self.carpeta), 0o700)
        for nombre in ("boveda.enc", "boveda.enc.bak"):
            self.assertEqual(self.modo(os.path.join(self.carpeta, nombre)), 0o600, nombre)
        for nombre in self.archivos():
            self.assertTrue(stat.S_ISREG(os.stat(os.path.join(self.carpeta, nombre)).st_mode))

    def test_permisos_con_umask_permisiva(self):
        antes = os.umask(0)
        self.addCleanup(os.umask, antes)
        self.creada(n=2)
        self.assertEqual(self.modo(self.carpeta), 0o700)
        self.assertEqual(self.modo(self.ruta), 0o600)
        self.assertEqual(self.modo(self.ruta + ".bak"), 0o600)

    def test_carpeta_y_fichero_con_permisos_de_mas_se_corrigen_al_abrir(self):
        self.creada().bloquear()
        os.chmod(self.carpeta, 0o755)
        os.chmod(self.ruta, 0o644)
        self.abierta()
        self.assertEqual(self.modo(self.carpeta), 0o700)
        self.assertEqual(self.modo(self.ruta), 0o600)

    def test_no_deja_basura_temporal(self):
        b = self.creada(n=4)
        b.borrar(b.entradas[0].id)
        b.cambiar_password(MAESTRA, OTRA)
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"])

    def test_la_copia_es_la_version_anterior(self):
        b = self.creada()
        self.assertEqual(self.archivos(), ["boveda.enc"])  # al crear aún no hay versión anterior
        b.agregar("Uno")
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"])  # la .bak es la bóveda vacía de antes
        b.agregar("Dos")
        c = self.nueva()
        c.abrir(MAESTRA)  # principal: Uno y Dos
        self.assertEqual([e.servicio for e in c.entradas], ["Dos", "Uno"])
        c.bloquear()
        os.replace(self.ruta + ".bak", self.ruta)  # ahora la anterior hace de principal
        self.assertEqual([e.servicio for e in self.abierta().entradas], ["Uno"])

    def test_copia_cuando_el_principal_se_daña(self):
        b = self.creada(n=3)
        b.agregar("Cuarto")
        b.bloquear()
        datos = bytearray(self.bytes_())
        datos[-3] ^= 0xFF
        self.escribir_bytes(bytes(datos))
        c = self.nueva()
        with self.assertRaises(B.BovedaDanada):
            c.abrir(MAESTRA)
        self.assertTrue(c.hay_copia())
        c.abrir(MAESTRA, copia=True)
        self.assertEqual(len(c.entradas), 3)  # la versión anterior
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak", "boveda.enc.corrupta"])
        c.bloquear()
        self.assertEqual(len(self.abierta().entradas), 3)  # el principal ya es válido otra vez

    def test_copia_con_contrasena_incorrecta(self):
        b = self.creada(n=2)
        b.agregar("x")
        b.bloquear()
        with self.assertRaises(B.ClaveIncorrecta):
            self.nueva().abrir(OTRA, copia=True)
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"])

    def test_sin_copia(self):
        self.creada().bloquear()
        with self.assertRaises(B.BovedaNoExiste):
            self.nueva().abrir(MAESTRA, copia=True)
        self.assertFalse(self.nueva().hay_copia())

    def test_un_principal_danado_no_pisa_la_copia_buena_al_guardar(self):
        b = self.creada(n=2)
        b.agregar("tres")
        copia_buena = self.bytes_("boveda.enc.bak")
        datos = bytearray(self.bytes_())
        datos[-3] ^= 0xFF  # el principal se daña MIENTRAS la bóveda está abierta
        self.escribir_bytes(bytes(datos))
        b.agregar("cuatro")  # guardar: no debe copiar el principal dañado sobre la .bak
        self.assertEqual(self.bytes_("boveda.enc.bak"), copia_buena)
        self.assertEqual(len(self.abierta().entradas), 4)

    def test_estado(self):
        b = self.nueva()
        self.assertEqual(b.estado(), "nueva")
        b.crear(MAESTRA)
        self.assertEqual(b.estado(), "existe")
        b.agregar("a")
        b.agregar("b")
        os.unlink(self.ruta)
        self.assertEqual(self.nueva().estado(), "solo_copia")
        os.unlink(self.ruta + ".bak")
        self.assertEqual(self.nueva().estado(), "nueva")

    def test_abrir_sin_boveda(self):
        with self.assertRaises(B.BovedaNoExiste):
            self.nueva().abrir(MAESTRA)
        self.assertFalse(os.path.exists(self.carpeta), "abrir no debe crear la carpeta")

    def test_crear_no_sobrescribe(self):
        self.creada(n=1).bloquear()
        antes = self.bytes_()
        with self.assertRaises(B.BovedaYaExiste):
            self.nueva().crear(OTRA)
        self.assertEqual(self.bytes_(), antes)

    def test_crear_con_maestra_no_valida_no_toca_el_disco(self):
        for malo in ("", "    ", "\t\n    ", "corta", "1234567", "x" * 300, "a\x00bcdefgh"):
            with self.subTest(malo=malo[:10]), self.assertRaises(B.DatosInvalidos):
                self.nueva().crear(malo)
        self.assertFalse(os.path.exists(self.carpeta))

    def test_validar_maestra(self):
        B.validar_maestra("12345678")
        B.validar_maestra("con espacios dentro")
        B.validar_maestra("ñandú-ñandú")
        B.validar_maestra("x" * 256)
        with self.assertRaises(B.DatosInvalidos):
            B.validar_maestra(None)
        with self.assertRaises(B.DatosInvalidos):
            B.validar_maestra("x" * 257)

    def test_ruta_respeta_xdg_data_home(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "/datos/xdg"}):
            self.assertEqual(B.ruta_boveda(), "/datos/xdg/infinity/boveda/boveda.enc")
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": "relativa/no/vale"}):
            self.assertEqual(B.ruta_boveda(), os.path.expanduser("~/.local/share/infinity/boveda/boveda.enc"))
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": ""}):
            self.assertEqual(B.ruta_boveda(), os.path.expanduser("~/.local/share/infinity/boveda/boveda.enc"))

    def test_sin_el_paquete_cryptography_se_dice_claro(self):
        with mock.patch.object(B, "AESGCM", None):
            with self.assertRaises(B.BovedaError) as cm:
                self.nueva().crear(MAESTRA)
        self.assertIn("python3-cryptography", str(cm.exception))


class TestAtomicidad(BaseBoveda):
    def setUp(self):
        super().setUp()
        self.b = self.creada(n=2)
        self.b.agregar("tres")
        self.antes = self.bytes_()
        self.antes_copia = self.bytes_("boveda.enc.bak")
        self.entradas = self.b.entradas

    def comprobar_intacto(self):
        self.assertEqual(self.bytes_(), self.antes)
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"], "quedó un temporal")
        self.assertEqual(self.b.entradas, self.entradas, "la memoria no debe adelantarse al disco")
        self.assertEqual(len(self.abierta().entradas), 3)

    def test_fallo_al_publicar(self):
        with mock.patch("os.replace", side_effect=OSError(errno.EIO, "fallo de prueba")):
            with self.assertRaises(OSError):
                self.b.agregar("cuatro")
        self.comprobar_intacto()

    def test_fallo_solo_en_el_ultimo_reemplazo(self):
        real = os.replace
        llamadas = []

        def replace(origen, destino, **kw):
            llamadas.append(destino)
            if destino == "boveda.enc":
                raise OSError(errno.EIO, "fallo de prueba")
            return real(origen, destino, **kw)

        with mock.patch("os.replace", side_effect=replace):
            with self.assertRaises(OSError):
                self.b.agregar("cuatro")
        self.assertEqual(llamadas, ["boveda.enc.bak", "boveda.enc"])
        self.assertEqual(self.bytes_(), self.antes)
        self.assertEqual(self.archivos(), ["boveda.enc", "boveda.enc.bak"])
        self.assertEqual(self.b.entradas, self.entradas)

    def test_fallo_en_fsync(self):
        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fallo de prueba")):
            with self.assertRaises(OSError):
                self.b.agregar("cuatro")
        self.comprobar_intacto()

    def test_disco_lleno_a_mitad_de_la_escritura(self):
        real = os.write

        def escribir_a_medias(fd, datos):
            real(fd, bytes(datos)[: len(datos) // 2])
            raise OSError(errno.ENOSPC, "disco lleno de prueba")

        with mock.patch("os.write", side_effect=escribir_a_medias):
            with self.assertRaises(OSError):
                self.b.agregar("cuatro")
        self.comprobar_intacto()

    def test_corte_brusco_interrupcion_del_proceso(self):
        with mock.patch("os.replace", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.b.agregar("cuatro")
        self.comprobar_intacto()

    def test_orden_de_la_escritura_fsync_del_fichero_antes_y_de_la_carpeta_despues(self):
        """Fichero: write → fsync → replace; luego fsync de la CARPETA (para que el renombrado también sobreviva a un corte)."""
        eventos = []
        real_fsync, real_replace = os.fsync, os.replace

        def fsync(fd):
            eventos.append("fsync-carpeta" if stat.S_ISDIR(os.fstat(fd).st_mode) else "fsync-fichero")
            return real_fsync(fd)

        def replace(*a, **kw):
            eventos.append("replace")
            return real_replace(*a, **kw)

        with mock.patch("os.fsync", side_effect=fsync), mock.patch("os.replace", side_effect=replace):
            self.b.agregar("cuatro")
        self.assertEqual(eventos, ["fsync-fichero", "fsync-fichero", "replace", "replace", "fsync-carpeta"])

    def test_se_puede_seguir_guardando_despues_de_un_fallo(self):
        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fallo de prueba")):
            with self.assertRaises(OSError):
                self.b.agregar("cuatro")
        self.b.agregar("cuatro")
        self.assertEqual(len(self.abierta().entradas), 4)

    def test_temporales_viejos_de_un_corte_anterior_se_limpian(self):
        viejo = os.path.join(self.carpeta, ".boveda.enc.4242.abcd1234.tmp")
        reciente = os.path.join(self.carpeta, ".boveda.enc.4243.abcd1235.tmp")
        for ruta in (viejo, reciente):
            with open(ruta, "wb") as f:
                f.write(b"cifrado a medias")
        hace_un_dia = time.time() - 86400
        os.utime(viejo, (hace_un_dia, hace_un_dia))
        self.b.agregar("cuatro")
        self.assertFalse(os.path.exists(viejo))
        self.assertTrue(os.path.exists(reciente), "no se toca un temporal reciente: podría estar en uso")


class TestCabeceraComoAad(BaseBoveda):
    def test_la_cabecera_entera_es_el_aad_del_cifrado(self):
        """La etiqueta GCM cubre los 62 bytes de la cabecera: con un AAD distinto (aunque sea de un solo bit) no vale."""
        self.creada(n=2).bloquear()
        datos = self.bytes_()
        cab, nonce, cifrado = datos[:B.LONG_CAB], datos[B.LONG_CAB:B.LONG_CAB + B.LONG_NONCE], datos[B.LONG_CAB + B.LONG_NONCE:]
        clave = B.derivar_clave(MAESTRA, cab[14:30], KDF_T)
        self.assertTrue(AESGCM(clave).decrypt(nonce, cifrado, cab))
        from cryptography.exceptions import InvalidTag
        for aad in (b"", cab[:-1], cab + b"\x00", bytes([cab[0] ^ 1]) + cab[1:], cab[:30] + bytes([cab[30] ^ 1]) + cab[31:]):
            with self.assertRaises(InvalidTag):
                AESGCM(clave).decrypt(nonce, cifrado, aad)

    def test_el_nonce_va_justo_despues_de_la_cabecera_y_el_cifrado_acaba_en_la_etiqueta(self):
        self.creada().bloquear()
        datos = self.bytes_()
        self.assertEqual(len(datos), B.LONG_CAB + B.LONG_NONCE + B.BLOQUE_RELLENO + B.LONG_TAG)
        self.assertEqual(datos[:8], b"INFBOVED")
        self.assertEqual(datos[8:10], b"\x00\x01")


class TestNonceYSal(BaseBoveda):
    def cabecera_y_nonce(self):
        datos = self.bytes_()
        return datos[:B.LONG_CAB], datos[B.LONG_CAB:B.LONG_CAB + B.LONG_NONCE]

    def test_nonce_nuevo_en_cada_guardado(self):
        b = self.creada()
        vistos, cabeceras = set(), set()
        for i in range(25):
            b.agregar(f"s{i}")
            cab, nonce = self.cabecera_y_nonce()
            self.assertEqual(len(nonce), 12)
            vistos.add(nonce)
            cabeceras.add(cab)
        self.assertEqual(len(vistos), 25)
        self.assertEqual(len(cabeceras), 1, "mientras no cambie la maestra, la sal y los parámetros son los mismos")

    def test_mismo_contenido_distinto_texto_cifrado(self):
        b = self.creada(n=1)
        a = self.bytes_()
        b.actualizar(b.entradas[0].id, b.entradas[0].servicio, b.entradas[0].usuario, b.entradas[0].contrasena)
        c = self.bytes_()
        self.assertNotEqual(a[B.LONG_CAB:], c[B.LONG_CAB:])

    def test_nunca_se_repite_el_nonce_aunque_el_azar_lo_diera(self):
        b = self.creada()
        b.agregar("a")
        repetido = self.bytes_()[B.LONG_CAB:B.LONG_CAB + B.LONG_NONCE]
        secuencia = [repetido, repetido, b"\x07" * 12]
        with mock.patch("os.urandom", side_effect=secuencia):
            b.agregar("b")
        self.assertEqual(self.bytes_()[B.LONG_CAB:B.LONG_CAB + B.LONG_NONCE], b"\x07" * 12)

    def test_cambiar_la_maestra_cambia_la_sal(self):
        b = self.creada(n=1)
        sal0, verif0 = self.bytes_()[B.LONG_CAB - 48:B.LONG_CAB - 32], self.bytes_()[B.LONG_CAB - 32:B.LONG_CAB]
        b.cambiar_password(MAESTRA, OTRA)
        sal1, verif1 = self.bytes_()[B.LONG_CAB - 48:B.LONG_CAB - 32], self.bytes_()[B.LONG_CAB - 32:B.LONG_CAB]
        self.assertNotEqual(sal0, sal1)
        self.assertNotEqual(verif0, verif1)

    def test_dos_boveda_con_la_misma_maestra_no_comparten_sal(self):
        self.creada().bloquear()
        sal_a = self.bytes_()[B.LONG_CAB - 48:B.LONG_CAB - 32]
        os.unlink(self.ruta)
        self.nueva().crear(MAESTRA)
        self.assertNotEqual(sal_a, self.bytes_()[B.LONG_CAB - 48:B.LONG_CAB - 32])


class TestCambioDeMaestra(BaseBoveda):
    def test_la_vieja_deja_de_valer_y_la_nueva_abre(self):
        b = self.creada(n=3)
        b.agregar("cuatro")  # que haya .bak
        b.cambiar_password(MAESTRA, OTRA)
        b.bloquear()
        with self.assertRaises(B.ClaveIncorrecta):
            self.nueva().abrir(MAESTRA)
        c = self.abierta(OTRA)
        self.assertEqual(len(c.entradas), 4)
        c.agregar("cinco")  # y se puede seguir guardando con la nueva
        c.bloquear()
        self.assertEqual(len(self.abierta(OTRA).entradas), 5)

    def test_la_copia_tambien_pasa_a_la_nueva(self):
        b = self.creada(n=2)
        b.agregar("x")
        b.cambiar_password(MAESTRA, OTRA)
        b.bloquear()
        with self.assertRaises(B.ClaveIncorrecta):
            self.nueva().abrir(MAESTRA, copia=True)  # la .bak NO conserva la contraseña vieja
        self.nueva().abrir(OTRA, copia=True)

    def test_se_borra_la_corrupta_que_seguia_con_la_vieja(self):
        b = self.creada(n=2)
        b.agregar("x")
        b.bloquear()
        datos = bytearray(self.bytes_())
        datos[-2] ^= 1
        self.escribir_bytes(bytes(datos))
        c = self.nueva()
        c.abrir(MAESTRA, copia=True)
        self.assertIn("boveda.enc.corrupta", self.archivos())
        c.cambiar_password(MAESTRA, OTRA)
        self.assertNotIn("boveda.enc.corrupta", self.archivos())

    def test_actual_incorrecta_no_cambia_nada(self):
        b = self.creada(n=1)
        antes = self.bytes_()
        with self.assertRaises(B.ClaveIncorrecta):
            b.cambiar_password("no-es-la-actual", OTRA)
        with self.assertRaises(B.ClaveIncorrecta):
            b.cambiar_password("", OTRA)
        self.assertEqual(self.bytes_(), antes)
        self.assertTrue(b.abierta)
        b.agregar("sigue funcionando")

    def test_nueva_no_valida(self):
        b = self.creada(n=1)
        antes = self.bytes_()
        for mala in ("", "        ", "corta", MAESTRA, " " * 10):
            with self.subTest(mala=mala), self.assertRaises(B.DatosInvalidos):
                b.cambiar_password(MAESTRA, mala)
        self.assertEqual(self.bytes_(), antes)

    def test_fallo_al_escribir_deja_todo_como_estaba(self):
        b = self.creada(n=2)
        antes = self.bytes_()
        with mock.patch("os.fsync", side_effect=OSError(errno.EIO, "fallo de prueba")):
            with self.assertRaises(OSError):
                b.cambiar_password(MAESTRA, OTRA)
        self.assertEqual(self.bytes_(), antes)
        b.bloquear()
        self.abierta()  # sigue valiendo la vieja
        with self.assertRaises(B.ClaveIncorrecta):
            self.nueva().abrir(OTRA)


class TestExportar(BaseBoveda):
    def test_copia_identica_con_permisos_0600(self):
        b = self.creada(n=2)
        destino = os.path.join(self.tmp, "copia.enc")
        b.exportar(destino)
        self.assertEqual(_leer_fichero(destino), self.bytes_())
        self.assertEqual(self.modo(destino), 0o600)
        self.assertEqual(sorted(os.listdir(self.tmp)), ["copia.enc", "infinity"], "sin temporales")

    def test_la_copia_se_abre_con_la_maestra(self):
        b = self.creada(n=2)
        carpeta = os.path.join(self.tmp, "otra")
        os.makedirs(carpeta)
        b.exportar(os.path.join(carpeta, "boveda.enc"))
        c = B.Boveda(os.path.join(carpeta, "boveda.enc"), kdf=KDF_T, limites=LIM_T)
        c.abrir(MAESTRA)
        self.assertEqual(len(c.entradas), 2)

    def test_sobrescribir_un_fichero_existente_deja_0600(self):
        b = self.creada(n=1)
        destino = os.path.join(self.tmp, "copia.enc")
        with open(destino, "w") as f:
            f.write("antiguo")
        os.chmod(destino, 0o644)
        b.exportar(destino)
        self.assertEqual(self.modo(destino), 0o600)
        self.assertEqual(_leer_fichero(destino), self.bytes_())

    def test_no_sobrescribe_la_propia_boveda(self):
        b = self.creada(n=2)
        b.agregar("x")
        antes = self.bytes_()
        enlace = os.path.join(self.tmp, "enlace.enc")
        os.symlink(self.ruta, enlace)
        for destino in (self.ruta, self.ruta + ".bak", enlace):
            with self.subTest(destino=destino), self.assertRaises(B.DatosInvalidos):
                b.exportar(destino)
        self.assertEqual(self.bytes_(), antes)

    def test_destino_no_valido(self):
        b = self.creada()
        with self.assertRaises(OSError):
            b.exportar(os.path.join(self.tmp, "no-existe", "copia.enc"))
        with self.assertRaises(OSError):
            b.exportar(self.tmp)  # una carpeta
        self.assertEqual(self.archivos(), ["boveda.enc"])

    def test_no_contiene_texto_plano(self):
        b = self.creada()
        b.agregar(MARCA + "-servicio", MARCA + "-usuario", MARCA + "-clave")
        destino = os.path.join(self.tmp, "copia.enc")
        b.exportar(destino)
        self.assertNotIn(MARCA.encode(), _leer_fichero(destino))


class TestEnlacesYDuenos(BaseBoveda):
    def test_carpeta_enlace_simbolico(self):
        real = os.path.join(self.tmp, "real")
        os.makedirs(real, mode=0o700)
        os.makedirs(os.path.dirname(self.carpeta))
        os.symlink(real, self.carpeta)
        with self.assertRaises(B.RutaInsegura):
            self.nueva().crear(MAESTRA)
        with self.assertRaises(B.RutaInsegura):
            self.nueva().abrir(MAESTRA)
        self.assertEqual(os.listdir(real), [])

    def test_fichero_principal_enlace_simbolico(self):
        self.creada(n=1).bloquear()
        copia = os.path.join(self.tmp, "fuera.enc")
        os.replace(self.ruta, copia)
        os.symlink(copia, self.ruta)
        with self.assertRaises(B.RutaInsegura):
            self.nueva().abrir(MAESTRA)

    def test_guardar_no_sigue_un_enlace_puesto_en_la_bak(self):
        b = self.creada(n=1)
        destino = os.path.join(self.tmp, "objetivo.txt")
        with open(destino, "w") as f:
            f.write("no tocar")
        os.unlink(self.ruta + ".bak")
        os.symlink(destino, self.ruta + ".bak")
        b.agregar("otra")  # reemplaza el enlace por un fichero normal; nunca escribe en su destino
        self.assertEqual(_leer_fichero(destino), b"no tocar")
        self.assertTrue(stat.S_ISREG(os.lstat(self.ruta + ".bak").st_mode))

    def test_fichero_no_regular(self):
        self.creada().bloquear()
        os.unlink(self.ruta)
        os.mkfifo(self.ruta)
        with self.assertRaises(B.RutaInsegura):
            self.nueva().abrir(MAESTRA)  # sin quedarse colgado esperando al otro extremo

    def test_carpeta_de_otro_usuario(self):
        self.creada().bloquear()
        with mock.patch("os.geteuid", return_value=os.geteuid() + 1):
            with self.assertRaises(B.RutaInsegura):
                self.nueva().abrir(MAESTRA)
            with self.assertRaises(B.RutaInsegura):
                self.nueva().estado()

    def test_la_carpeta_no_es_una_carpeta(self):
        os.makedirs(os.path.dirname(self.carpeta))
        open(self.carpeta, "w").close()
        with self.assertRaises(B.RutaInsegura):
            self.nueva().abrir(MAESTRA)


class TestValidacionDeCampos(BaseBoveda):
    def setUp(self):
        super().setUp()
        self.b = self.creada()

    def test_limites_exactos(self):
        for campo, maximo in B.LIMITES_CAMPO.items():
            with self.subTest(campo):
                valor = "a" * maximo
                self.b.agregar(**{"servicio": "ok", campo: valor})
                with self.assertRaises(B.DatosInvalidos):
                    self.b.agregar(**{"servicio": "ok", campo: valor + "a"})
        self.assertEqual(B.LIMITES_CAMPO, {"servicio": 120, "usuario": 254, "contrasena": 1024, "url": 500, "notas": 5000})

    def test_servicio_obligatorio(self):
        for malo in ("", "   ", "\t\n"):
            with self.assertRaises(B.DatosInvalidos):
                self.b.agregar(malo)

    def test_tipos_y_categoria(self):
        for kw in ({"servicio": None}, {"servicio": 5}, {"servicio": "x", "usuario": 5}, {"servicio": "x", "notas": b"b"},
                   {"servicio": "x", "contrasena": ["l"]}, {"servicio": "x", "categoria": "Inventada"},
                   {"servicio": "x", "categoria": None}, {"servicio": "x", "categoria": "correo"},
                   {"servicio": "x\x00y"}, {"servicio": "x", "contrasena": "a\x00b"}):
            with self.subTest(kw), self.assertRaises(B.DatosInvalidos):
                self.b.agregar(**kw)
        self.assertEqual(len(self.b.entradas), 0)
        for c in B.CATEGORIAS:
            self.b.agregar("x", categoria=c)

    def test_recorta_espacios_menos_en_la_contrasena_y_las_notas(self):
        e = self.b.agregar("  Gmail ", " ana@ejemplo.test  ", "  con espacios  ", "  192.168.1.1 ", "  nota  ")
        self.assertEqual((e.servicio, e.usuario, e.url), ("Gmail", "ana@ejemplo.test", "192.168.1.1"))
        self.assertEqual((e.contrasena, e.notas), ("  con espacios  ", "  nota  "))

    def test_el_mensaje_no_lleva_el_valor(self):
        with self.assertRaises(B.DatosInvalidos) as cm:
            self.b.agregar("x", contrasena=MARCA * 200)
        self.assertNotIn(MARCA, str(cm.exception))
        self.assertIn("contraseña", str(cm.exception))

    def test_actualizar_y_borrar_algo_que_no_existe(self):
        with self.assertRaises(B.BovedaError):
            self.b.actualizar("0123456789abcdef", "x")
        with self.assertRaises(B.BovedaError):
            self.b.borrar("0123456789abcdef")

    def test_actualizar_con_datos_malos_no_cambia_nada(self):
        e = self.b.agregar("bueno", "u", "p")
        antes = self.bytes_()
        with self.assertRaises(B.DatosInvalidos):
            self.b.actualizar(e.id, "")
        self.assertEqual(self.bytes_(), antes)
        self.assertEqual(self.b.obtener(e.id), e)

    def test_maximo_de_entradas(self):
        with mock.patch.object(B, "MAX_ENTRADAS", 3):
            for i in range(3):
                self.b.agregar(f"s{i}")
            with self.assertRaises(B.DatosInvalidos):
                self.b.agregar("uno más")

    def test_demasiados_datos(self):
        with mock.patch.object(B, "MAX_TEXTO", 6000):
            for i in range(3):
                self.b.agregar(f"s{i}", notas="n" * 1500)
            with self.assertRaises(B.DatosInvalidos):
                self.b.agregar("uno más", notas="n" * 1500)
        self.assertEqual(len(self.b.entradas), 3)


class TestBusqueda(BaseBoveda):
    def setUp(self):
        super().setUp()
        b = self.b = self.creada()
        b.agregar("Gmail", "ana.perez@ejemplo.test", "clave-falsa-aaa", "https://mail.ejemplo.test", "correo personal", "Correo")
        b.agregar("Router de casa", "admin", "clave-falsa-bbb", "192.168.1.1", "caja del salón", "Router y red")
        b.agregar("Netflix", "ana.perez@ejemplo.test", "clave-falsa-ccc", "https://netflix.ejemplo.test", "", "Web")
        b.agregar("Banco Ejemplo", "12345678", "clave-falsa-ddd", "https://banco.ejemplo.test", "clave de cajero", "Banco")
        b.agregar("Cámara del jardín", "admin", "clave-falsa-eee", "192.168.1.50", "", "Router y red")

    def nombres(self, *a, **kw):
        return [e.servicio for e in self.b.buscar(*a, **kw)]

    def test_sin_filtro_ordenado_por_nombre(self):
        self.assertEqual(self.nombres(), ["Banco Ejemplo", "Cámara del jardín", "Gmail", "Netflix", "Router de casa"])

    def test_por_nombre_usuario_url_y_notas(self):
        self.assertEqual(self.nombres("gmail"), ["Gmail"])
        self.assertEqual(self.nombres("ana.perez"), ["Gmail", "Netflix"])
        self.assertEqual(self.nombres("192.168.1.1"), ["Router de casa"])
        self.assertEqual(self.nombres("salon"), ["Router de casa"])
        self.assertEqual(self.nombres("netflix.ejemplo"), ["Netflix"])

    def test_sin_mayusculas_ni_acentos(self):
        self.assertEqual(self.nombres("CAMARA"), ["Cámara del jardín"])
        self.assertEqual(self.nombres("jardin"), ["Cámara del jardín"])
        self.assertEqual(self.nombres("cámara"), ["Cámara del jardín"])

    def test_varias_palabras_en_cualquier_orden(self):
        self.assertEqual(self.nombres("ana netflix"), ["Netflix"])
        self.assertEqual(self.nombres("netflix ana"), ["Netflix"])
        self.assertEqual(self.nombres("ana router"), [])

    def test_la_contrasena_no_se_busca(self):
        self.assertEqual(self.nombres("clave-falsa-aaa"), [])
        self.assertEqual(self.nombres("falsa"), [])
        self.assertEqual(self.nombres("clave de cajero"), ["Banco Ejemplo"])  # esto es una nota, no una contraseña

    def test_por_categoria(self):
        self.assertEqual(self.nombres(categoria="Router y red"), ["Cámara del jardín", "Router de casa"])
        self.assertEqual(self.nombres("admin", categoria="Router y red"), ["Cámara del jardín", "Router de casa"])
        self.assertEqual(self.nombres("gmail", categoria="Banco"), [])
        self.assertEqual(self.nombres(categoria="Otros"), [])

    def test_contadores(self):
        self.assertEqual(self.b.contar_por_categoria(),
                         {"Todas": 5, "Correo": 1, "Router y red": 2, "Web": 1, "Banco": 1, "Otros": 0})

    def test_texto_raro(self):
        for q in ("", "   ", "%", ".*", "(", "\\", "ñ", "\x00"):
            self.b.buscar(q)  # no revienta


class TestSecretosNoSeFiltran(BaseBoveda):
    def test_repr_y_str(self):
        b = self.creada()
        e = b.agregar(MARCA + "-servicio", MARCA + "-usuario", MARCA + "-clave", MARCA + "-url", MARCA + "-notas")
        for objeto in (b, e, b.entradas, [e], {"e": e}, B.Fortaleza(1, 2.0, "x", ()), B.ParamsKDF()):
            for texto in (repr(objeto), str(objeto), "%s %r" % (objeto, objeto), f"{objeto}"):
                self.assertNotIn(MARCA, texto)
        self.assertEqual(repr(e), f"Entrada(id='{e.id}')")
        self.assertEqual(str(b), "<Boveda abierta>")
        b.bloquear()
        self.assertEqual(repr(b), "<Boveda cerrada>")

    def test_excepciones(self):
        b = self.creada()
        b.agregar(MARCA, MARCA, MARCA, MARCA, MARCA)
        b.bloquear()
        errores = []

        def intenta(f):
            try:
                f()
            except Exception as e:  # noqa: BLE001
                errores.append(e)

        c = self.nueva()
        intenta(lambda: c.abrir(MARCA + "-intento"))
        intenta(lambda: self.nueva().crear(MARCA[:5]))
        intenta(lambda: self.nueva().crear(MARCA * 40))
        d = self.abierta()
        intenta(lambda: d.agregar(MARCA * 200))
        intenta(lambda: d.agregar("x", contrasena=MARCA * 300))
        intenta(lambda: d.agregar("x", categoria=MARCA))
        intenta(lambda: d.cambiar_password(MARCA + "-no", OTRA))
        intenta(lambda: d.cambiar_password(MAESTRA, MARCA[:3]))
        intenta(lambda: d.actualizar(MARCA, "x"))
        datos = bytearray(self.bytes_())
        datos[-1] ^= 1
        self.escribir_bytes(bytes(datos))
        intenta(lambda: self.nueva().abrir(MAESTRA))
        self.assertGreaterEqual(len(errores), 9)
        for e in errores:
            cadena = [e]
            while cadena[-1].__cause__ or cadena[-1].__context__:
                cadena.append(cadena[-1].__cause__ or cadena[-1].__context__)
            completo = "".join(traceback.format_exception(type(e), e, e.__traceback__))
            for x in cadena:
                self.assertNotIn(MARCA, repr(x) + str(x) + "".join(map(str, x.args)))
            self.assertNotIn(MARCA, completo)

    def test_el_json_roto_no_se_encadena(self):
        """json.JSONDecodeError guarda el documento entero: no debe colgar de la excepción que sale."""
        b = self.creada()
        b.bloquear()
        sal = self.bytes_()[B.LONG_CAB - 48:B.LONG_CAB - 32]
        cab = self.bytes_()[:B.LONG_CAB]
        nonce = os.urandom(12)
        plano = (MARCA + " no es json").encode()
        self.escribir_bytes(cab + nonce + AESGCM(B.derivar_clave(MAESTRA, sal, KDF_T)).encrypt(nonce, plano, cab))
        with self.assertRaises(B.BovedaDanada) as cm:
            self.nueva().abrir(MAESTRA)
        self.assertIsNone(cm.exception.__cause__)
        self.assertIsNone(cm.exception.__context__)

    def test_nada_en_texto_plano_en_disco(self):
        b = self.creada()
        b.agregar(MARCA + "-servicio", MARCA + "-usuario", MARCA + "-clave", MARCA + "-url", MARCA + "-notas")
        b.agregar("Otro", "u", MARCA)
        b.cambiar_password(MAESTRA, OTRA)
        for ruta, _, nombres in os.walk(self.tmp):
            for nombre in nombres:
                contenido = _leer_fichero(os.path.join(ruta, nombre))
                self.assertNotIn(MARCA.encode(), contenido, nombre)
                self.assertNotIn(MAESTRA.encode(), contenido, nombre)
                self.assertNotIn(OTRA.encode(), contenido, nombre)
                self.assertNotIn(b"servicio", contenido, nombre)


class TestBloqueo(BaseBoveda):
    def test_bloquear_suelta_clave_y_entradas(self):
        b = self.creada(n=2)
        clave = b._clave
        self.assertEqual(len(clave), 32)
        self.assertNotEqual(bytes(clave), bytes(32))
        b.bloquear()
        self.assertFalse(b.abierta)
        self.assertEqual(bytes(clave), bytes(32), "la clave que guardábamos se pone a cero")
        self.assertIsNone(b._clave)
        self.assertIsNone(b._cabecera)
        self.assertEqual(b._entradas, ())
        self.assertIsNone(b._ultimo_nonce)
        with self.assertRaises(B.BovedaError):
            b.entradas

    def test_las_entradas_se_pueden_recolectar_tras_bloquear(self):
        b = self.creada(n=2)
        referencia = weakref.ref(b.entradas[0])
        b.bloquear()
        gc.collect()
        self.assertIsNone(referencia())

    def test_bloquear_llama_a_gc_collect(self):
        b = self.creada()
        with mock.patch.object(B.gc, "collect") as colecta:
            b.bloquear()
        colecta.assert_called_once_with()

    def test_se_puede_bloquear_dos_veces_y_volver_a_abrir(self):
        b = self.creada(n=1)
        b.bloquear()
        b.bloquear()
        b.abrir(MAESTRA)
        self.assertEqual(len(b.entradas), 1)

    def test_abrir_una_ya_abierta(self):
        b = self.creada()
        with self.assertRaises(B.BovedaError):
            b.abrir(MAESTRA)
        with self.assertRaises(B.BovedaError):
            b.crear(MAESTRA)

    def test_clave_incorrecta_no_deja_nada_abierto(self):
        self.creada().bloquear()
        b = self.nueva()
        with self.assertRaises(B.ClaveIncorrecta):
            b.abrir(OTRA)
        self.assertIsNone(b._clave)
        self.assertEqual(b._entradas, ())


class TestGenerador(unittest.TestCase):
    def test_longitud(self):
        for n in (8, 9, 16, 20, 33, 64):
            self.assertEqual(len(B.generar_contrasena(n)), n)

    def test_longitudes_fuera_de_rango(self):
        for n in (0, 7, 65, 1000, -5, 12.5, "20", None, True):
            with self.subTest(n), self.assertRaises(B.DatosInvalidos):
                B.generar_contrasena(n)

    def test_al_menos_una_de_cada_clase_incluso_con_longitud_minima(self):
        for _ in range(2000):
            p = B.generar_contrasena(8)
            self.assertTrue(any(c in B.MINUSCULAS for c in p), p)
            self.assertTrue(any(c in B.MAYUSCULAS for c in p), p)
            self.assertTrue(any(c in B.DIGITOS for c in p), p)
            self.assertTrue(any(c in B.SIMBOLOS for c in p), p)

    def test_solo_los_caracteres_pedidos(self):
        casos = {
            "minusculas": (dict(mayusculas=False, digitos=False, simbolos=False), B.MINUSCULAS),
            "mayusculas": (dict(minusculas=False, digitos=False, simbolos=False), B.MAYUSCULAS),
            "digitos": (dict(minusculas=False, mayusculas=False, simbolos=False), B.DIGITOS),
            "simbolos": (dict(minusculas=False, mayusculas=False, digitos=False), B.SIMBOLOS),
            "letras": (dict(digitos=False, simbolos=False), B.MINUSCULAS + B.MAYUSCULAS),
        }
        for nombre, (kw, permitidos) in casos.items():
            with self.subTest(nombre):
                for _ in range(200):
                    p = B.generar_contrasena(12, **kw)
                    self.assertTrue(set(p) <= set(permitidos), p)
        p = B.generar_contrasena(30, mayusculas=False, simbolos=False)
        self.assertTrue(set(p) <= set(B.MINUSCULAS + B.DIGITOS))
        self.assertTrue(any(c in B.DIGITOS for c in p) and any(c in B.MINUSCULAS for c in p))

    def test_sin_ninguna_clase(self):
        with self.assertRaises(B.DatosInvalidos):
            B.generar_contrasena(16, False, False, False, False)

    def test_sin_ambiguos(self):
        for _ in range(3000):
            p = B.generar_contrasena(8, evitar_ambiguos=True)
            self.assertFalse(set(p) & set("0O1lI"), p)
            self.assertTrue(any(c in B.DIGITOS for c in p), p)  # y siguen estando las cuatro clases
            self.assertTrue(any(c in B.SIMBOLOS for c in p), p)

    def test_con_ambiguos_a_veces_salen(self):
        vistos = set()
        for _ in range(500):
            vistos |= set(B.generar_contrasena(64)) & set("0O1lI")
        self.assertEqual(vistos, set("0O1lI"))

    def test_aleatoriedad_basica(self):
        lote = [B.generar_contrasena(16) for _ in range(500)]
        self.assertEqual(len(set(lote)), 500)
        # el primer carácter no está fijado en una clase ni en un valor
        primeros = [p[0] for p in lote]
        self.assertGreater(len(set(primeros)), 30)
        self.assertGreater(sum(c in B.SIMBOLOS for c in primeros), 10)
        self.assertGreater(sum(c in B.DIGITOS for c in primeros), 10)
        # y todos los caracteres posibles aparecen alguna vez
        todos = set("".join(lote))
        self.assertEqual(todos, set(B.MINUSCULAS + B.MAYUSCULAS + B.DIGITOS + B.SIMBOLOS))

    def test_usa_secrets_systemrandom(self):
        usados = []

        class Espia(B.secrets.SystemRandom):
            def shuffle(self, x):
                usados.append("shuffle")
                super().shuffle(x)

            def choice(self, seq):
                usados.append("choice")
                return super().choice(seq)

        with mock.patch.object(B.secrets, "SystemRandom", Espia):
            B.generar_contrasena(12)
        self.assertIn("shuffle", usados)
        self.assertGreaterEqual(usados.count("choice"), 12)

    def test_azar_inyectado_es_determinista(self):
        import random
        a = B.generar_contrasena(20, azar=random.Random(7))
        b = B.generar_contrasena(20, azar=random.Random(7))
        self.assertEqual(a, b)


class TestFortaleza(unittest.TestCase):
    def test_muy_debiles(self):
        for p in ("password", "Password1!", "12345678", "qwertyuiop", "aaaaaaaaaaaa", "abcdefgh", "abcabcabcabc",
                  "contraseña", "p4ssw0rd", "iloveyou2024", "zzzzzzzz", "1q2w3e4r", "admin1234", "infinity123"):
            with self.subTest(p):
                self.assertEqual(B.evaluar_fortaleza(p).puntos, 0, B.evaluar_fortaleza(p))

    def test_orden_razonable(self):
        puntos = [B.evaluar_fortaleza(p).puntos for p in
                  ("abc", "gato1234", "xkcdqwpzmv", "J7$kq!x2", "Xk9#mP2$vL7@", "correct horse battery staple")]
        self.assertEqual(puntos, sorted(puntos))
        self.assertEqual(puntos[0], 0)
        self.assertGreaterEqual(puntos[-1], 3)

    def test_la_longitud_y_la_variedad_suben_la_puntuacion(self):
        self.assertLess(B.evaluar_fortaleza("Jq7$").bits, B.evaluar_fortaleza("Jq7$Jx2!").bits)
        self.assertLess(B.evaluar_fortaleza("xkqjvzwp").bits, B.evaluar_fortaleza("xkqjvzwp3").bits)
        self.assertLess(B.evaluar_fortaleza("xkqjvzwp3").bits, B.evaluar_fortaleza("xkqjvzwp3#").bits)

    def test_aleatoria_de_las_cuatro_clases_es_excelente(self):
        for _ in range(100):
            self.assertEqual(B.evaluar_fortaleza(B.generar_contrasena(24)).puntos, 4)

    def test_repeticiones_y_secuencias_penalizan(self):
        base = B.evaluar_fortaleza("Jq7$Xv2!").bits
        self.assertLess(B.evaluar_fortaleza("Jq7$Xv2!aaaa").bits, B.evaluar_fortaleza("Jq7$Xv2!Pm4%").bits)
        self.assertLess(B.evaluar_fortaleza("Jq7$Xv2!1234").bits, B.evaluar_fortaleza("Jq7$Xv2!Pm4%").bits)
        self.assertLess(B.evaluar_fortaleza("Jq7$Xv2!qwer").bits, B.evaluar_fortaleza("Jq7$Xv2!Pm4%").bits)
        self.assertGreater(base, 40)
        self.assertIn("Tiene repeticiones o secuencias", B.evaluar_fortaleza("Jq7$Xv2!1234").avisos)

    def test_palabras_comunes_y_leet(self):
        for p in ("MiPassword!", "Contr4señ4", "xxPASSWORDxx", "xxp4ssw0rdxx"):
            self.assertIn("Lleva una palabra muy común", B.evaluar_fortaleza(p).avisos, p)
        self.assertNotIn("Lleva una palabra muy común", B.evaluar_fortaleza("Jq7$Xv2!Pm4%").avisos)

    def test_vacia_y_corta(self):
        f = B.evaluar_fortaleza("")
        self.assertEqual((f.puntos, f.bits, f.etiqueta), (0, 0.0, ""))
        self.assertEqual(B.evaluar_fortaleza(None).puntos, 0)
        self.assertLessEqual(B.evaluar_fortaleza("Jq7$Xv2").puntos, 1)  # 7 caracteres: nunca más de «Débil»

    def test_etiquetas(self):
        self.assertEqual(B.ETIQUETAS_FORTALEZA, ("Muy débil", "Débil", "Aceptable", "Buena", "Excelente"))
        self.assertEqual(B.evaluar_fortaleza("password").etiqueta, "Muy débil")
        self.assertEqual(B.evaluar_fortaleza(B.generar_contrasena(24)).etiqueta, "Excelente")

    def test_no_se_desborda_con_textos_enormes_o_raros(self):
        t = time.monotonic()
        for p in ("a" * 5000, "ab" * 3000, "😀" * 300, "ñ" * 300, "\x00" * 10, "áéíóú" * 100,
                  B.generar_contrasena(64) * 1 + "".join(chr(33 + i % 90) for i in range(2000))):
            B.evaluar_fortaleza(p)
        self.assertLess(time.monotonic() - t, 5)


class TestFechaLegible(unittest.TestCase):
    def test_formato(self):
        with mock.patch.dict(os.environ, {"TZ": "UTC"}):
            time.tzset()
            self.addCleanup(time.tzset)
            self.assertEqual(B.fecha_legible("2026-10-20T14:05:00Z"), "20 oct 2026, 14:05")
            self.assertEqual(B.fecha_legible("2026-01-02T00:00:09Z"), "2 ene 2026, 00:00")

    def test_no_es_una_fecha(self):
        for malo in ("", "ayer", None, 5, "2026-13-45T99:99:99Z"):
            self.assertEqual(B.fecha_legible(malo), "—")


class TestEsperaTrasFallos(unittest.TestCase):
    def test_secuencia(self):
        self.assertEqual([B.espera_tras_fallos(n) for n in range(0, 9)], [0, 1, 2, 4, 8, 16, 30, 30, 30])
        self.assertEqual(B.espera_tras_fallos(1000), 30)
        self.assertEqual(B.espera_tras_fallos(-3), 0)


class _PortapapelesFalso:
    def __init__(self):
        self.contenido = None
        self.borrados = 0
        self.es_nuestro = False
        self.lectura_pendiente = []

    def poner(self, texto):
        self.contenido, self.es_nuestro = texto, True

    def leer(self, devolver):
        self.lectura_pendiente.append(devolver)

    def resolver_lectura(self):
        for f in self.lectura_pendiente:
            f(self.contenido)
        self.lectura_pendiente = []

    def borrar(self):
        self.contenido, self.borrados = None, self.borrados + 1

    def borrar_si_es_nuestro(self):
        if self.es_nuestro:
            self.borrar()

    def alguien_copia(self, texto):
        self.contenido, self.es_nuestro = texto, False


class TestPortapapelesTemporal(unittest.TestCase):
    def setUp(self):
        self.cb = _PortapapelesFalso()
        self.temporizadores = {}
        self.avisos = []
        self._n = 0
        self.pt = B.PortapapelesTemporal(self.cb, self._programar, self.temporizadores.pop, 25, self.avisos.append)

    def _programar(self, segundos, funcion):
        self._n += 1
        self.temporizadores[self._n] = (segundos, funcion)
        return self._n

    def vencer(self):
        self.assertEqual(len(self.temporizadores), 1)
        _, (_, funcion) = self.temporizadores.popitem()
        self.assertFalse(funcion())
        self.cb.resolver_lectura()

    def test_borra_a_los_25_segundos_si_sigue_igual(self):
        self.pt.copiar("clave-falsa-123")
        self.assertEqual(self.cb.contenido, "clave-falsa-123")
        self.assertEqual(list(self.temporizadores.values())[0][0], 25)
        self.assertTrue(self.pt.pendiente)
        self.assertEqual(self.avisos, ["copiado"])
        self.vencer()
        self.assertIsNone(self.cb.contenido)
        self.assertEqual(self.cb.borrados, 1)
        self.assertFalse(self.pt.pendiente)
        self.assertEqual(self.avisos, ["copiado", "borrado"])

    def test_no_borra_si_el_usuario_copio_otra_cosa(self):
        self.pt.copiar("clave-falsa-123")
        self.cb.alguien_copia("otra cosa que copié luego")
        self.vencer()
        self.assertEqual(self.cb.contenido, "otra cosa que copié luego")
        self.assertEqual(self.cb.borrados, 0)
        self.assertEqual(self.avisos[-1], "cambiado")

    def test_no_borra_si_el_portapapeles_ya_esta_vacio_o_no_es_texto(self):
        self.pt.copiar("clave-falsa-123")
        self.cb.contenido = None
        self.vencer()
        self.assertEqual(self.cb.borrados, 0)

    def test_una_segunda_copia_anula_el_temporizador_de_la_primera(self):
        self.pt.copiar("usuario-falso")
        self.pt.copiar("clave-falsa-123")
        self.assertEqual(len(self.temporizadores), 1)
        self.vencer()
        self.assertEqual(self.cb.borrados, 1)

    def test_si_se_bloquea_mientras_se_lee_no_se_borra(self):
        self.pt.copiar("clave-falsa-123")
        _, (_, funcion) = self.temporizadores.popitem()
        funcion()
        self.pt.limpiar_ya()  # bloqueo en medio de la lectura
        borrados = self.cb.borrados
        self.cb.resolver_lectura()
        self.assertEqual(self.cb.borrados, borrados)

    def test_limpiar_ya_borra_solo_si_sigue_siendo_nuestro(self):
        self.pt.copiar("clave-falsa-123")
        self.pt.limpiar_ya()
        self.assertEqual(self.cb.borrados, 1)
        self.assertEqual(self.temporizadores, {})
        self.pt.copiar("otra-falsa")
        self.cb.alguien_copia("ajeno")
        self.pt.limpiar_ya()
        self.assertEqual(self.cb.contenido, "ajeno")
        self.pt.limpiar_ya()  # sin nada pendiente: no hace nada
        self.assertEqual(self.cb.borrados, 1)

    def test_no_guarda_el_texto_copiado(self):
        self.pt.copiar(MARCA + "-clave")
        self.assertNotIn(MARCA, repr(vars(self.pt)))
        self.assertNotIn(MARCA, repr(self.temporizadores))


class TestEndurecimiento(unittest.TestCase):
    def test_sin_volcados_ni_ptrace(self):
        codigo = (
            "import sys, resource, ctypes; sys.path.insert(0, %r)\n"
            "from infinity_shell import boveda\n"
            "r = boveda.endurecer_proceso()\n"
            "print(r['core'], r['dumpable'], resource.getrlimit(resource.RLIMIT_CORE), "
            "ctypes.CDLL(None).prctl(3, 0, 0, 0, 0))\n" % os.path.join(HERE, "..")
        )
        salida = subprocess.run([sys.executable, "-c", codigo], capture_output=True, text=True, timeout=60)
        self.assertEqual(salida.returncode, 0, salida.stderr)
        self.assertEqual(salida.stdout.strip(), "True True (0, 0) 0")


# ===================================================================================== instalación y límites
def _fuente(*ruta):
    with open(os.path.join(ROOT, *ruta), encoding="utf-8") as f:
        return f.read()


class TestInstalacion(unittest.TestCase):
    """Lo que hace falta para que la app llegue a la ISO: paquete, comando, lanzador, icono y copia del código."""

    def test_el_paquete_python3_cryptography_esta_en_packages_list(self):
        lineas = _fuente("build", "packages.list").splitlines()
        paquetes = [l.split("#")[0].strip() for l in lineas if l.strip() and not l.lstrip().startswith("#")]
        self.assertIn("python3-cryptography", paquetes)
        linea = next(l for l in lineas if l.startswith("python3-cryptography"))
        self.assertIn("gestor de contraseñas (AES-GCM)", linea)
        self.assertEqual(paquetes.count("python3-cryptography"), 1)

    def test_el_comando_es_un_script_sh_ejecutable_como_infinity_ajustes(self):
        ruta = os.path.join(ROOT, "overlay", "usr", "bin", "infinity-boveda")
        self.assertTrue(os.access(ruta, os.X_OK))
        texto = _fuente("overlay", "usr", "bin", "infinity-boveda")
        self.assertTrue(texto.startswith("#!/bin/sh\n"))
        self.assertIn("export PYTHONPATH=/usr/lib/infinity\n", texto)
        self.assertIn('export GSK_RENDERER="${GSK_RENDERER:-cairo}"', texto)
        self.assertTrue(texto.rstrip().endswith('exec python3 -m infinity_shell.boveda_app "$@"'))
        ajustes = _fuente("overlay", "usr", "bin", "infinity-ajustes")
        for linea in ("export PYTHONPATH=/usr/lib/infinity", 'export GSK_RENDERER="${GSK_RENDERER:-cairo}"'):
            self.assertIn(linea, ajustes)

    def test_el_comando_no_lleva_secretos_ni_argumentos_de_contrasena(self):
        texto = _fuente("overlay", "usr", "bin", "infinity-boveda")
        for malo in ("password", "--clave", "read ", "curl", "wget", "nc "):
            self.assertNotIn(malo, texto.lower().replace("contraseñas", ""), malo)

    def test_el_lanzador_de_escritorio(self):
        ruta = os.path.join(ROOT, "overlay", "usr", "share", "applications", "infinity-boveda.desktop")
        cfg = configparser.ConfigParser(interpolation=None, delimiters=("=",))
        cfg.optionxform = str
        cfg.read(ruta, encoding="utf-8")
        d = cfg["Desktop Entry"]
        self.assertEqual(d["Type"], "Application")
        self.assertEqual(d["Name"], "Contraseñas")
        self.assertEqual(d["GenericName"], "Gestor de contraseñas")
        self.assertEqual(d["Icon"], "/usr/share/infinity/boveda.svg")
        self.assertEqual(d["Exec"], "infinity-boveda")
        self.assertEqual(d["Categories"], "Utility;Security;")
        self.assertEqual(d["StartupWMClass"], "org.infinity.Boveda")
        self.assertEqual(d["Terminal"], "false")
        for palabra in ("contraseñas", "claves", "passwords", "boveda", "llavero", "correo", "router"):
            self.assertIn(palabra, d["Keywords"].split(";"))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "overlay", "usr", "bin", d["Exec"])))
        self.assertTrue(os.path.isfile(os.path.join(ROOT, "overlay") + d["Icon"]))

    def test_la_clase_de_ventana_es_la_del_id_de_la_aplicacion(self):
        fuente = _fuente("shell", "infinity_shell", "boveda_app.py")
        self.assertIn('APP_ID = "org.infinity.Boveda"', fuente)
        self.assertIn("StartupWMClass=org.infinity.Boveda", _fuente("overlay", "usr", "share", "applications",
                                                                    "infinity-boveda.desktop"))

    def test_el_icono_es_un_svg_vectorial_violeta(self):
        raiz = ET.parse(os.path.join(ROOT, "overlay", "usr", "share", "infinity", "boveda.svg")).getroot()
        self.assertTrue(raiz.tag.endswith("svg"))
        self.assertEqual(raiz.get("viewBox"), "0 0 256 256")
        texto = _fuente("overlay", "usr", "share", "infinity", "boveda.svg")
        self.assertIn("#b9a8ff", texto)
        for malo in ("<script", "href=", "<image", "data:", "onload"):
            self.assertNotIn(malo, texto)

    def test_build_instala_el_overlay_y_el_codigo_de_la_shell_incluidos_los_modulos_nuevos(self):
        build = _fuente("build", "build.sh")
        self.assertIn('rsync -a --chown=root:root "$ROOT/overlay/" "$ROOTFS/"', build)
        self.assertIn("rsync -a --exclude '__pycache__' --exclude 'tests' \"$ROOT/shell/infinity_shell\" "
                      "\"$ROOTFS/usr/lib/infinity/\"", build)
        for modulo in ("boveda.py", "boveda_app.py"):
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "shell", "infinity_shell", modulo)))
            self.assertNotIn(modulo, build.replace("boveda_app", "x"), "no se instala por nombre: entra con el resto")

    def test_la_documentacion_dice_lo_que_hace_el_codigo(self):
        doc = _fuente("docs", "boveda.md")
        for frase in ("AES-256-GCM", "scrypt", "N=2**16", "r=8", "p=1", "INFBOVED", "0600", "0700", "boveda.enc.bak",
                      "no se pueden recuperar", "25 s", "3 minutos", "PrepareForSleep", "gc.collect", "python3-cryptography"):
            self.assertIn(frase, doc, frase)
        self.assertEqual((B.KDF_POR_DEFECTO.log2n, B.KDF_POR_DEFECTO.r, B.KDF_POR_DEFECTO.p), (16, 8, 1))
        self.assertEqual((B.LONG_SAL, B.LONG_NONCE, B.LONG_CLAVE, B.LONG_CAB), (16, 12, 32, 62))


class TestSinRedNiRegistros(unittest.TestCase):
    """Sin telemetría, sin red, sin abrir URLs y sin escribir secretos en ningún sitio (se mira el código)."""

    MODULOS = ("boveda.py", "boveda_app.py")
    IMPORTS_PROHIBIDOS = {"socket", "ssl", "http", "urllib", "requests", "webbrowser", "subprocess", "smtplib", "ftplib",
                          "logging", "pickle", "shelve", "sqlite3", "tempfile", "xml", "asyncio"}

    def arbol(self, modulo):
        return ast.parse(_fuente("shell", "infinity_shell", modulo))

    def test_no_importa_nada_de_red_ni_de_registro(self):
        for modulo in self.MODULOS:
            importados = set()
            for n in ast.walk(self.arbol(modulo)):
                if isinstance(n, ast.Import):
                    importados |= {a.name.split(".")[0] for a in n.names}
                elif isinstance(n, ast.ImportFrom) and n.module:
                    importados.add(n.module.split(".")[0])
            self.assertEqual(importados & self.IMPORTS_PROHIBIDOS, set(), modulo)

    def test_no_imprime_ni_escribe_en_la_salida(self):
        for modulo in self.MODULOS:
            for n in ast.walk(self.arbol(modulo)):
                if isinstance(n, ast.Call):
                    nombre = getattr(n.func, "id", None) or getattr(n.func, "attr", None)
                    self.assertNotIn(nombre, ("print", "pprint"), f"{modulo}:{n.lineno}")
        fuente = _fuente("shell", "infinity_shell", "boveda.py")
        self.assertNotIn("sys.stdout", fuente)
        self.assertNotIn("sys.stderr", fuente)

    def test_la_ventana_solo_escribe_en_stderr_el_aviso_de_que_falta_el_paquete(self):
        fuente = _fuente("shell", "infinity_shell", "boveda_app.py")
        self.assertEqual(fuente.count("sys.stderr.write"), 1)
        self.assertIn("falta el paquete python3-cryptography", fuente)

    def usados(self, modulo):
        """Nombres, atributos y cadenas que el código USA de verdad (sin docstrings ni comentarios)."""
        arbol = self.arbol(modulo)
        docstrings = set()
        for n in ast.walk(arbol):
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and ast.get_docstring(n):
                docstrings.add(id(n.body[0].value))
        usados = set()
        for n in ast.walk(arbol):
            if isinstance(n, ast.Name):
                usados.add(n.id)
            elif isinstance(n, ast.Attribute):
                usados.add(n.attr)
            elif isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings:
                usados.add(n.value)
        return usados

    def test_no_abre_urls_ni_programas(self):
        prohibido = {"xdg-open", "show_uri", "UriLauncher", "launch_default_for_uri", "system", "popen", "Popen",
                     "LinkButton", "WebKit", "wl-copy", "putenv", "unsetenv", "setenv", "AppInfo", "spawn_async",
                     "spawn_sync", "spawn_command_line_async"}
        for modulo in self.MODULOS:
            self.assertEqual(self.usados(modulo) & prohibido, set(), modulo)

    def test_no_escribe_en_el_entorno(self):
        for modulo in self.MODULOS:
            for n in ast.walk(self.arbol(modulo)):
                if isinstance(n, ast.Subscript) and getattr(n.value, "attr", "") == "environ":
                    self.fail(f"{modulo}:{n.lineno}: usa os.environ[...]")

    def test_ni_secretos_en_variables_de_entorno_ni_en_argv(self):
        for modulo in self.MODULOS:
            for n in ast.walk(self.arbol(modulo)):
                if isinstance(n, ast.Attribute) and n.attr == "argv":
                    self.assertEqual(modulo, "boveda_app.py", "solo la ventana pasa sys.argv a GApplication.run")
        fuente = _fuente("shell", "infinity_shell", "boveda_app.py")
        self.assertEqual(fuente.count("sys.argv"), 1)
        self.assertIn("AppBoveda().run(sys.argv)", fuente)


if __name__ == "__main__":
    unittest.main()
