"""Núcleo de pAnkI: clave, cliente de Gemini, bucle del agente y recorte del historial.

No hay red real ni clave real. El cliente habla con un servidor HTTP falso (http.server en 127.0.0.1,
puerto 0, en un hilo) que imita generateContent con respuestas guionizadas. El Ejecutor es FALSO: cumple
el contrato de docs/panki-diseno.md con clases definidas aquí, para no depender de panki_herramientas.py.

Higiene de la clave: cada prueba con servidor comprueba al terminar (tearDown) que la clave solo viaja
en la cabecera x-goog-api-key y que no aparece en ningún registro.
"""
import copy
import io
import json
import logging
import os
import socket
import stat
import sys
import tempfile
import threading
import time
import traceback
import types
import unittest
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import panki_nucleo as N  # noqa: E402

CLAVE = "AIzaSyD-TEST_clave0123456789ABCDEFghij"
OTRA_CLAVE = "AIzaSyZ-OTRA_clave9876543210ZYXWVUtsrq"


# --------------------------------------------------------------------------- Ejecutor falso (contrato)
class ErrorHerramienta(Exception):
    pass


@dataclass
class Solicitud:
    herramienta: str
    titulo: str
    detalle: list
    riesgo: str
    recordable: object


@dataclass
class Preparada:
    nombre: str
    args: dict
    solicitud: object


@dataclass
class Resultado:
    ok: bool
    datos: dict
    resumen: str


SECRETO_INTERNO = "/home/usuario/.config/secreto/interno.py"


class EjecutorFalso:
    """Herramientas: info_sistema (sin aprobación), leer_archivo (recordable), mover_archivo (no recordable),
    invalida (ErrorHerramienta), rota_preparar / rota_ejecutar (excepción inesperada), enorme / opaca."""

    NOMBRES = ("info_sistema", "leer_archivo", "mover_archivo", "invalida", "rota_preparar", "rota_ejecutar",
               "enorme", "opaca", "muta_args")

    def __init__(self):
        self.preparadas = []
        self.ejecutadas = []
        self.al_ejecutar = None

    def declaraciones(self):
        return [{"name": n, "description": f"Herramienta {n}", "parameters": {"type": "OBJECT", "properties": {}}}
                for n in self.NOMBRES]

    def preparar(self, nombre, args):
        self.preparadas.append((nombre, copy.deepcopy(args)))
        if nombre == "invalida":
            raise ErrorHerramienta("La ruta no está permitida.")
        if nombre == "rota_preparar":
            raise RuntimeError(f"Traceback en {SECRETO_INTERNO} línea 3")
        if nombre == "muta_args":
            args["inyectado"] = True
            args.setdefault("lista", []).append("mutada")
        solicitud = None
        if nombre == "leer_archivo":
            solicitud = Solicitud(nombre, "Leer notas.txt", ["~/Docs/notas.txt"], "bajo", "carpeta:/home/u/Docs")
        elif nombre == "mover_archivo":
            solicitud = Solicitud(nombre, "Mover a.txt", ["a.txt -> b.txt"], "medio", None)
        return Preparada(nombre, args, solicitud)

    def ejecutar(self, preparada):
        self.ejecutadas.append(preparada.nombre)
        if self.al_ejecutar:
            self.al_ejecutar(preparada)
        if preparada.nombre == "rota_ejecutar":
            raise RuntimeError(f"Traceback en {SECRETO_INTERNO} línea 9")
        if preparada.nombre == "enorme":
            return Resultado(True, {"texto": "x" * (N.MAX_RESULTADO + 10)}, "enorme")
        if preparada.nombre == "opaca":
            return Resultado(True, {"objeto": object()}, "opaca")
        return Resultado(True, {"herramienta": preparada.nombre, "args": preparada.args}, f"{preparada.nombre} hecho")


# ------------------------------------------------------------------------------ servidor HTTP falso
class ServidorFalso:
    def __init__(self):
        self.guion = []
        self.peticiones = []
        servidor = self

        class Manejador(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.0"

            def do_POST(self):
                largo = int(self.headers.get("Content-Length") or 0)
                cuerpo = self.rfile.read(largo)
                servidor.peticiones.append({
                    "ruta": self.path, "metodo": self.command, "cuerpo": cuerpo,
                    "cabeceras": {k.lower(): v for k, v in self.headers.items()}})
                paso = servidor.guion.pop(0) if servidor.guion else {
                    "status": 500, "json": {"error": {"message": "guion agotado"}}}
                if paso.get("retraso"):
                    time.sleep(paso["retraso"])
                datos = paso["crudo"] if "crudo" in paso else json.dumps(paso.get("json", {})).encode("utf-8")
                self.send_response(paso.get("status", 200))
                for k, v in paso.get("cabeceras", {}).items():
                    self.send_header(k, v)
                self.send_header("Content-Type", paso.get("tipo", "application/json"))
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                try:
                    self.wfile.write(datos)
                except OSError:
                    pass

            def log_message(self, *args):
                pass

        class Servidor(ThreadingHTTPServer):
            daemon_threads = True

            def handle_error(self, request, client_address):
                pass

        self.httpd = Servidor(("127.0.0.1", 0), Manejador)
        self.base_url = f"http://127.0.0.1:{self.httpd.server_address[1]}/v1beta"
        self.hilo = threading.Thread(target=self.httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        self.hilo.start()

    def cerrar(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def r_texto(t, **extra):
    return {"json": {"candidates": [{"content": {"role": "model", "parts": [{"text": t}]},
                                     "finishReason": extra.pop("fin", "STOP"), "index": 0}],
                     "usageMetadata": {"promptTokenCount": 1}}, **extra}


def r_llamadas(*llamadas, firma=None, texto=None, ids=False):
    partes = []
    if texto:
        partes.append({"text": texto})
    for i, (nombre, args) in enumerate(llamadas):
        parte = {"functionCall": {"name": nombre, "args": args}}
        if ids:
            parte["functionCall"]["id"] = f"llamada-{i}"
        if firma and i == 0:
            parte["thoughtSignature"] = firma
        partes.append(parte)
    return {"json": {"candidates": [{"content": {"role": "model", "parts": partes}, "finishReason": "STOP"}]}}


def validar_historial(caso, h, cerrado=True):
    """Lo que exige la API: roles alternados desde user y cada functionCall con su functionResponse."""
    caso.assertTrue(h, "historial vacío")
    for i, m in enumerate(h):
        caso.assertEqual(m["role"], "user" if i % 2 == 0 else "model", f"rol del mensaje {i}: {h}")
        caso.assertTrue(m["parts"], f"mensaje {i} sin partes")
        llamadas = [p["functionCall"]["name"] for p in m["parts"] if "functionCall" in p]
        respuestas = [p["functionResponse"]["name"] for p in m["parts"] if "functionResponse" in p]
        if m["role"] == "model" and llamadas:
            caso.assertLess(i + 1, len(h), "functionCall sin respuesta")
            siguiente = h[i + 1]
            caso.assertEqual([p["functionResponse"]["name"] for p in siguiente["parts"]], llamadas)
            caso.assertEqual(len(siguiente["parts"]), len(llamadas))
        if respuestas:
            caso.assertEqual(m["role"], "user")
            caso.assertGreater(i, 0)
            previas = [p["functionCall"]["name"] for p in h[i - 1]["parts"] if "functionCall" in p]
            caso.assertEqual(respuestas, previas)
    if cerrado:
        caso.assertEqual(h[-1]["role"], "model")
        caso.assertFalse([p for p in h[-1]["parts"] if "functionCall" in p])


class ClienteFalso:
    """Sustituye al ClienteGemini cuando no hace falta HTTP. Guarda copias de lo que recibe."""

    def __init__(self, respuestas):
        self.respuestas = list(respuestas)
        self.recibidos = []
        self.antes = None

    def generar(self, contenidos, sistema, herramientas):
        self.recibidos.append(copy.deepcopy(contenidos))
        if self.antes:
            self.antes()
        r = self.respuestas.pop(0)
        if isinstance(r, BaseException):
            raise r
        return r


def resp_dict(*llamadas, texto=None, firma=None):
    return r_llamadas(*llamadas, texto=texto, firma=firma)["json"] if llamadas else r_texto(texto or "ok")["json"]


class ConServidor(unittest.TestCase):
    """Servidor falso por prueba + vigilancia de la clave en peticiones y registros."""

    def setUp(self):
        self.srv = ServidorFalso()
        self.registro = io.StringIO()
        self.manejador = logging.StreamHandler(self.registro)
        self.manejador.setLevel(logging.DEBUG)
        raiz = logging.getLogger()
        self._nivel = raiz.level
        raiz.setLevel(logging.DEBUG)
        raiz.addHandler(self.manejador)
        self._env = mock.patch.dict(os.environ, {}, clear=False)
        self._env.start()
        os.environ.pop(N.VARIABLE_CLAVE, None)

    def tearDown(self):
        try:
            self.comprobar_higiene()
        finally:
            raiz = logging.getLogger()
            raiz.removeHandler(self.manejador)
            raiz.setLevel(self._nivel)
            self._env.stop()
            self.srv.cerrar()

    def comprobar_higiene(self):
        for p in self.srv.peticiones:
            self.assertEqual(p["metodo"], "POST")
            self.assertEqual(p["cabeceras"].get("x-goog-api-key"), CLAVE)
            self.assertNotIn(CLAVE, p["ruta"])
            self.assertNotIn(CLAVE.encode(), p["cuerpo"])
            for k, v in p["cabeceras"].items():
                if k != "x-goog-api-key":
                    self.assertNotIn(CLAVE, v, k)
        self.assertNotIn(CLAVE, self.registro.getvalue())

    def cliente(self, modelo="gemini-test-1.5", **kw):
        kw.setdefault("timeout", 5)
        return N.ClienteGemini(CLAVE, modelo, base_url=self.srv.base_url, **kw)

    def cuerpo(self, i):
        return json.loads(self.srv.peticiones[i]["cuerpo"].decode("utf-8"))

    def assertSinClave(self, excepcion):
        """La clave no está en el mensaje, en el repr, en la traza ni en la cadena de excepciones."""
        texto = "".join(traceback.format_exception(excepcion)) + str(excepcion) + repr(excepcion)
        self.assertNotIn(CLAVE, texto)
        self.assertIsNone(excepcion.__cause__)
        self.assertIsNone(excepcion.__context__)


# ================================================================================================ clave
class TestClave(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = os.path.join(self.tmp.name, "config")
        os.makedirs(self.base)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.base, "HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(N.VARIABLE_CLAVE, None)
        self.carpeta = os.path.join(self.base, "infinity", "panki")
        self.archivo = os.path.join(self.carpeta, "clave")

    def modo(self, ruta):
        return stat.S_IMODE(os.stat(ruta).st_mode)

    def test_sin_clave(self):
        self.assertIsNone(N.cargar_clave())
        self.assertFalse(N.existe_clave())

    def test_guardar_y_cargar(self):
        N.guardar_clave(CLAVE)
        self.assertEqual(N.cargar_clave(), CLAVE)
        self.assertTrue(N.existe_clave())
        with open(self.archivo, encoding="ascii") as f:
            self.assertEqual(f.read(), CLAVE + "\n")

    def test_la_ruta_respeta_xdg_config_home(self):
        N.guardar_clave(CLAVE)
        self.assertTrue(os.path.isfile(os.path.join(self.base, "infinity", "panki", "clave")))

    def test_xdg_relativa_se_ignora(self):
        os.environ["XDG_CONFIG_HOME"] = "relativa"
        N.guardar_clave(CLAVE)
        self.assertTrue(os.path.isfile(os.path.join(self.tmp.name, ".config", "infinity", "panki", "clave")))
        self.assertFalse(os.path.exists(os.path.join(os.getcwd(), "relativa")))

    def test_permisos_0600_y_0700(self):
        N.guardar_clave(CLAVE)
        self.assertEqual(self.modo(self.archivo), 0o600)
        self.assertEqual(self.modo(self.carpeta), 0o700)

    def test_permisos_aunque_el_umask_sea_cero(self):
        antes = os.umask(0)
        self.addCleanup(os.umask, antes)
        N.guardar_clave(CLAVE)
        self.assertEqual(self.modo(self.archivo), 0o600)
        self.assertEqual(self.modo(self.carpeta), 0o700)

    def test_corrige_permisos_de_lo_que_ya_existia(self):
        os.makedirs(self.carpeta, mode=0o755)
        os.chmod(self.carpeta, 0o755)
        with open(self.archivo, "w") as f:
            f.write("vieja\n")
        os.chmod(self.archivo, 0o644)
        N.guardar_clave(CLAVE)
        self.assertEqual(self.modo(self.carpeta), 0o700)
        self.assertEqual(self.modo(self.archivo), 0o600)
        self.assertEqual(N.cargar_clave(), CLAVE)

    def test_reemplazo_sin_temporales(self):
        N.guardar_clave(CLAVE)
        N.guardar_clave(OTRA_CLAVE)
        self.assertEqual(N.cargar_clave(), OTRA_CLAVE)
        self.assertEqual(os.listdir(self.carpeta), ["clave"])

    def test_reemplazo_atomico_si_falla_no_se_pierde_la_clave_anterior(self):
        N.guardar_clave(CLAVE)
        with mock.patch.object(N.os, "replace", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(N.ErrorPanki) as cm:
                N.guardar_clave(OTRA_CLAVE)
        self.assertNotIn(OTRA_CLAVE, str(cm.exception))
        self.assertNotIn(CLAVE, str(cm.exception))
        self.assertEqual(N.cargar_clave(), CLAVE)
        self.assertEqual(os.listdir(self.carpeta), ["clave"], "no debe quedar un temporal")

    def test_el_entorno_tiene_prioridad_sobre_el_archivo(self):
        N.guardar_clave(CLAVE)
        os.environ[N.VARIABLE_CLAVE] = OTRA_CLAVE
        self.assertEqual(N.cargar_clave(), OTRA_CLAVE)
        os.environ[N.VARIABLE_CLAVE] = "  " + OTRA_CLAVE + "\n"
        self.assertEqual(N.cargar_clave(), OTRA_CLAVE)

    def test_entorno_vacio_usa_el_archivo(self):
        N.guardar_clave(CLAVE)
        for vacio in ("", "   ", "\n"):
            os.environ[N.VARIABLE_CLAVE] = vacio
            self.assertEqual(N.cargar_clave(), CLAVE)

    def test_solo_entorno(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        self.assertEqual(N.cargar_clave(), CLAVE)
        self.assertTrue(N.existe_clave())
        self.assertFalse(os.path.exists(self.carpeta))

    def test_formato_invalido(self):
        malas = ["", " ", CLAVE + " ", " " + CLAVE, CLAVE + "\n", "AIza con espacio dentro 0123456789",
                 "A" * 19, "A" * 201, "AIza\tcon\ttabulaciones0123456789", "AIza\x00nul0123456789abcdefgh",
                 "AIza\x1bescape0123456789abcdef", "AIzaclave-con-ñ-0123456789abcdef", "AIza​cero0123456789abcdef",
                 "AIza\x7fdel0123456789abcdefghij", None, 123, b"AIza" + b"x" * 30, ["x"]]
        for mala in malas:
            with self.subTest(clave=repr(mala)[:40]):
                with self.assertRaises(N.ErrorPanki) as cm:
                    N.guardar_clave(mala)
                if isinstance(mala, str) and len(mala) >= 8:
                    self.assertNotIn(mala.strip(), str(cm.exception))
        self.assertFalse(os.path.exists(self.carpeta), "una clave inválida no crea nada")

    def test_formato_valido_en_los_limites(self):
        for largo in (20, 21, 100, 199, 200):
            with self.subTest(largo=largo):
                clave = "k" * largo
                N.guardar_clave(clave)
                self.assertEqual(N.cargar_clave(), clave)
        N.guardar_clave("!~" * 10)  # primer y último ASCII visibles
        self.assertEqual(N.cargar_clave(), "!~" * 10)

    def test_enlace_simbolico_en_el_archivo_no_se_sigue(self):
        os.makedirs(self.carpeta, mode=0o700)
        destino = os.path.join(self.tmp.name, "destino")
        with open(destino, "w") as f:
            f.write(OTRA_CLAVE + "\n")
        os.symlink(destino, self.archivo)
        self.assertIsNone(N.cargar_clave(), "no se lee a través de un enlace")
        N.guardar_clave(CLAVE)
        with open(destino) as f:
            self.assertEqual(f.read(), OTRA_CLAVE + "\n", "no se escribe a través de un enlace")
        self.assertFalse(os.path.islink(self.archivo))
        self.assertEqual(N.cargar_clave(), CLAVE)

    def test_enlace_simbolico_en_la_carpeta_no_se_sigue(self):
        ajeno = os.path.join(self.tmp.name, "ajeno")
        os.makedirs(ajeno)
        os.makedirs(os.path.dirname(self.carpeta))
        os.symlink(ajeno, self.carpeta)
        with open(os.path.join(ajeno, "clave"), "w") as f:
            f.write(OTRA_CLAVE + "\n")
        self.assertIsNone(N.cargar_clave())
        with self.assertRaises(N.ErrorPanki) as cm:
            N.guardar_clave(CLAVE)
        self.assertIn("enlace", str(cm.exception))
        self.assertEqual(os.listdir(ajeno), ["clave"])
        with open(os.path.join(ajeno, "clave")) as f:
            self.assertEqual(f.read(), OTRA_CLAVE + "\n")

    def test_archivo_que_no_es_regular_se_ignora(self):
        os.makedirs(self.archivo, mode=0o700)
        self.assertIsNone(N.cargar_clave())

    def test_archivo_con_basura_se_ignora(self):
        os.makedirs(self.carpeta, mode=0o700)
        with open(self.archivo, "wb") as f:
            f.write(b"\xff\xfe\x00 no es ascii")
        self.assertIsNone(N.cargar_clave())
        with open(self.archivo, "w") as f:
            f.write("\n\n")
        self.assertIsNone(N.cargar_clave())

    def test_borrar(self):
        N.guardar_clave(CLAVE)
        N.borrar_clave()
        self.assertFalse(os.path.exists(self.archivo))
        self.assertIsNone(N.cargar_clave())
        N.borrar_clave()  # sin archivo, sin error
        os.rmdir(self.carpeta)
        N.borrar_clave()  # sin carpeta, sin error

    def test_borrar_no_toca_la_variable_de_entorno(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        N.guardar_clave(OTRA_CLAVE)
        N.borrar_clave()
        self.assertEqual(N.cargar_clave(), CLAVE)

    def test_error_de_disco_no_incluye_la_clave_ni_rutas(self):
        os.makedirs(os.path.join(self.base, "infinity", "x"), exist_ok=True)
        with open(os.path.join(self.base, "infinity", "panki"), "w") as f:  # un archivo donde va la carpeta
            f.write("x")
        with self.assertRaises(N.ErrorPanki) as cm:
            N.guardar_clave(CLAVE)
        self.assertNotIn(CLAVE, str(cm.exception))
        self.assertNotIn(self.tmp.name, str(cm.exception))
        self.assertIsNone(cm.exception.__context__)

    def test_la_clave_no_va_a_ajustes_json(self):
        N.guardar_clave(CLAVE)
        for raiz, _, nombres in os.walk(self.tmp.name):
            for n in nombres:
                if os.path.join(raiz, n) != self.archivo:
                    with open(os.path.join(raiz, n), errors="ignore") as f:
                        self.assertNotIn(CLAVE, f.read())


# ====================================================================================== ClienteGemini
class TestClienteGemini(ConServidor):
    def test_peticion(self):
        self.srv.guion = [r_texto("hola")]
        resp = self.cliente().generar([{"role": "user", "parts": [{"text": "¿Qué tal, camión?"}]}],
                                      "Eres pAnkI.", [{"name": "f", "description": "d"}])
        self.assertEqual(resp["candidates"][0]["content"]["parts"][0]["text"], "hola")
        p = self.srv.peticiones[0]
        self.assertEqual(p["metodo"], "POST")
        self.assertEqual(p["ruta"], "/v1beta/models/gemini-test-1.5:generateContent")
        self.assertEqual(p["cabeceras"]["x-goog-api-key"], CLAVE)
        self.assertEqual(p["cabeceras"]["content-type"], "application/json")
        self.assertNotIn("?", p["ruta"])
        self.assertEqual(self.cuerpo(0), {
            "systemInstruction": {"parts": [{"text": "Eres pAnkI."}]},
            "contents": [{"role": "user", "parts": [{"text": "¿Qué tal, camión?"}]}],
            "tools": [{"functionDeclarations": [{"name": "f", "description": "d"}]}],
            "generationConfig": {"temperature": 0.3}})

    def test_sin_declaraciones_no_hay_tools(self):
        self.srv.guion = [r_texto("a"), r_texto("b")]
        self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", None)
        for i in (0, 1):
            self.assertNotIn("tools", self.cuerpo(i))

    def test_modelos_peligrosos_se_rechazan_sin_hacer_peticion(self):
        peligrosos = ["", " ", "a/b", "../x", "a/../b", "a:b", "gemini x", "x?key=1", "x#y", "x%2f", "x%00", "a\r\nHost: evil",
                      "a\nb", "gemini-1.5:generateContent?key=" + CLAVE, "modelo\x00", "ñandú", "a" * 61, ".oculto",
                      "-guion", "models/gemini", "a;b", "a@b", "a\\b", "a b", "<x>", "a'b", None, 5, b"gemini"]
        for modelo in peligrosos:
            with self.subTest(modelo=repr(modelo)):
                with self.assertRaises(N.ErrorPanki):
                    self.cliente(modelo=modelo)
        self.assertEqual(self.srv.peticiones, [])

    def test_modelos_validos(self):
        for modelo in ("gemini-flash-latest", "gemini-2.5-flash", "gemini_pro.1", "A", "a" * 60, "1.5-pro"):
            with self.subTest(modelo=modelo):
                self.assertEqual(self.cliente(modelo=modelo).modelo, modelo)

    def test_el_modelo_se_revalida_al_generar(self):
        c = self.cliente()
        c.modelo = "../../otro/recurso"
        with self.assertRaises(N.ErrorPanki):
            c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertEqual(self.srv.peticiones, [])

    def test_base_url_solo_https_o_loopback(self):
        malas = ["http://example.com/v1beta", "http://127.0.0.1.evil.com/v1beta", "http://localhost.evil.com",
                 "http://localhost@evil.com/", "http://127.0.0.1@evil.com/", "http://127.0.0.1:80@evil.com/",
                 "http://127.0.0.1\\@evil.com/", "http://evil.com#@127.0.0.1/", "http://evil.com/?x=127.0.0.1",
                 "ftp://127.0.0.1/", "file:///etc/passwd", "//generativelanguage.googleapis.com", "",
                 "https://user:pass@generativelanguage.googleapis.com/v1beta",
                 "https://generativelanguage.googleapis.com/v1beta?key=1", "https://host/a b", "https://host/\n",
                 "http://[::1]/v1beta", "http://0.0.0.0/v1beta", "http://127.0.0.2/v1beta", "https://",
                 "http://127.0.0.1:99999/x", None, 5]
        for url in malas:
            with self.subTest(url=repr(url)):
                with self.assertRaises(N.ErrorPanki):
                    N.ClienteGemini(CLAVE, "gemini-x", base_url=url)

    def test_base_url_aceptadas(self):
        for url in ("https://generativelanguage.googleapis.com/v1beta", "https://generativelanguage.googleapis.com/v1beta/",
                    "http://127.0.0.1:8080/v1beta", "http://localhost:9/x", "http://localhost"):
            with self.subTest(url=url):
                c = N.ClienteGemini(CLAVE, "gemini-x", base_url=url)
                self.assertFalse(c.base_url.endswith("/"))
        self.assertTrue(N.BASE_URL.startswith("https://"))
        self.assertEqual(N.ClienteGemini(CLAVE, "gemini-x").base_url, N.BASE_URL)

    def test_clave_con_formato_malo_no_se_repite_en_el_error(self):
        for mala in ("clave con espacios y mas cosas", CLAVE + "\n", "corta", None, ""):
            with self.subTest(clave=repr(mala)):
                with self.assertRaises(N.ErrorPanki) as cm:
                    N.ClienteGemini(mala, "gemini-x", base_url=self.srv.base_url)
                if mala:
                    self.assertNotIn(mala.strip(), str(cm.exception))

    def test_repr_no_lleva_la_clave(self):
        self.assertNotIn(CLAVE, repr(self.cliente()))
        self.assertNotIn(CLAVE, str(self.cliente()))

    def test_no_se_siguen_redirecciones(self):
        otro = ServidorFalso()
        self.addCleanup(otro.cerrar)
        for codigo in (301, 302, 303, 307, 308):
            with self.subTest(codigo=codigo):
                self.srv.guion = [{"status": codigo, "cabeceras": {"Location": otro.base_url + "/models/x:generateContent"},
                                   "json": {}}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn("redirección", str(cm.exception))
                self.assertSinClave(cm.exception)
        self.assertEqual(otro.peticiones, [], "la clave no puede llegar a otro servidor")

    def test_errores_http(self):
        casos = {400: "rechazó la petición", 401: "no aceptó la clave", 403: "no aceptó la clave",
                 404: "no existe", 429: "cuota", 500: "no está disponible", 502: "no está disponible",
                 503: "no está disponible", 418: "error inesperado (418)"}
        for codigo, esperado in casos.items():
            with self.subTest(codigo=codigo):
                self.srv.guion = [{"status": codigo, "json": {"error": {"code": codigo, "message": "detalle de la API"}}}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn(esperado, str(cm.exception))
                self.assertIn("detalle de la API", str(cm.exception))
                self.assertEqual(cm.exception.codigo, codigo)
                self.assertSinClave(cm.exception)

    def test_404_sugiere_cambiar_el_modelo_y_lo_nombra(self):
        self.srv.guion = [{"status": 404, "json": {"error": {"message": "models/x is not found"}}}]
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente(modelo="modelo-que-no-existe").generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("modelo-que-no-existe", str(cm.exception))
        self.assertIn("Ajustes", str(cm.exception))

    def test_400_con_clave_no_valida_se_explica_como_clave(self):
        cuerpo = {"error": {"code": 400, "message": "API key not valid. Please pass a valid API key.",
                            "status": "INVALID_ARGUMENT", "details": [{"@type": "x", "reason": "API_KEY_INVALID"}]}}
        self.srv.guion = [{"status": 400, "json": cuerpo}]
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("no aceptó la clave", str(cm.exception))
        self.assertIn("Clave", str(cm.exception))

    def test_detalle_de_la_api_se_recorta_a_300(self):
        self.srv.guion = [{"status": 400, "json": {"error": {"message": "D" * 5000}}}]
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        mensaje = str(cm.exception)
        self.assertLessEqual(mensaje.count("D"), 300)
        self.assertLess(len(mensaje), 700)
        self.assertIn("…", mensaje)

    def test_detalle_de_la_api_no_lleva_la_clave_ni_saltos_de_linea(self):
        for texto in (f"la clave {CLAVE} no vale", f"empieza {CLAVE[:20]}{CLAVE[20:]} fin", "otra AIzaSyOTROTROTROTROTROTRO1234 ahí",
                      f"x\n\r\ty\x00z {CLAVE}"):
            with self.subTest(texto=texto[:30]):
                self.srv.guion = [{"status": 400, "json": {"error": {"message": texto}}}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertNotIn(CLAVE, str(cm.exception))
                self.assertNotIn("AIzaSyOTROTROTROTROTROTRO1234", str(cm.exception))
                self.assertNotIn("\n", str(cm.exception))
                self.assertNotIn("\x00", str(cm.exception))

    def test_clave_justo_en_el_corte_de_los_300_caracteres(self):
        self.srv.guion = [{"status": 400, "json": {"error": {"message": "R" * 285 + CLAVE + " fin"}}}]
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        mensaje = str(cm.exception)
        self.assertNotIn(CLAVE[:10], mensaje)  # ni siquiera un trozo

    def test_cuerpo_de_error_que_no_es_json(self):
        self.srv.guion = [{"status": 502, "crudo": b"<html><body>Bad gateway</body></html>", "tipo": "text/html"},
                          {"status": 400, "crudo": b"[1, 2, 3]"},
                          {"status": 500, "crudo": b'{"error": "texto"}'},
                          {"status": 500, "crudo": b'{"error": {"message": 5, "details": 7}}'},
                          {"status": 500, "crudo": b"\xff\xfe"}]
        for _ in range(5):
            with self.assertRaises(N.ErrorPanki) as cm:
                self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
            self.assertNotIn("<html>", str(cm.exception))
            self.assertIn("Gemini", str(cm.exception))
            self.assertNotIn("Detalle", str(cm.exception))

    def test_json_roto(self):
        for crudo in (b'{"candidates": [', b"esto no es json", b"", b"\xff\xfe\x00"):
            with self.subTest(crudo=crudo):
                self.srv.guion = [{"crudo": crudo}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn("JSON", str(cm.exception))

    def test_json_que_no_es_un_objeto(self):
        for crudo in (b"[1,2]", b'"texto"', b"null", b"5"):
            with self.subTest(crudo=crudo):
                self.srv.guion = [{"crudo": crudo}]
                with self.assertRaises(N.ErrorPanki):
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])

    def test_respuesta_sin_candidatos(self):
        for cuerpo in ({}, {"candidates": []}, {"candidates": "x"}, {"candidates": [5]}):
            with self.subTest(cuerpo=cuerpo):
                self.srv.guion = [{"json": cuerpo}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn("no devolvió ninguna respuesta", str(cm.exception))

    def test_respuesta_bloqueada_en_la_peticion(self):
        self.srv.guion = [{"json": {"promptFeedback": {"blockReason": "SAFETY"}}}]
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("bloqueó", str(cm.exception))
        self.assertIn("SAFETY", str(cm.exception))

    def test_respuesta_bloqueada_por_seguridad(self):
        for fin in ("SAFETY", "PROHIBITED_CONTENT", "RECITATION", "BLOCKLIST"):
            with self.subTest(fin=fin):
                self.srv.guion = [{"json": {"candidates": [{"finishReason": fin, "content": {"role": "model", "parts": [{"text": "x"}]}}]}}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn("bloqueó", str(cm.exception))

    def test_respuesta_vacia(self):
        for candidato in ({"finishReason": "STOP"}, {"finishReason": "STOP", "content": {"role": "model"}},
                          {"content": {"role": "model", "parts": []}}, {"content": {"parts": ["x"]}},
                          {"finishReason": "MALFORMED_FUNCTION_CALL"}):
            with self.subTest(candidato=candidato):
                self.srv.guion = [{"json": {"candidates": [candidato]}}]
                with self.assertRaises(N.ErrorPanki) as cm:
                    self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn("vacía", str(cm.exception))

    def test_tiempo_agotado(self):
        self.srv.guion = [{"retraso": 1.5, **r_texto("tarde")}]
        inicio = time.monotonic()
        with self.assertRaises(N.ErrorPanki) as cm:
            self.cliente(timeout=0.3).generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertLess(time.monotonic() - inicio, 1.4)
        self.assertIn("tardó demasiado", str(cm.exception))
        self.assertSinClave(cm.exception)

    def test_red_caida(self):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            puerto = s.getsockname()[1]
        c = N.ClienteGemini(CLAVE, "gemini-x", base_url=f"http://127.0.0.1:{puerto}/v1beta", timeout=2)
        with self.assertRaises(N.ErrorPanki) as cm:
            c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("conexión", str(cm.exception))
        self.assertSinClave(cm.exception)

    def test_conexion_cortada_a_mitad(self):
        # El servidor anuncia más bytes de los que envía y cierra.
        class Cortado(BaseHTTPRequestHandler):
            def do_POST(self):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                self.send_response(200)
                self.send_header("Content-Length", "1000")
                self.end_headers()
                self.wfile.write(b'{"candidates"')

            def log_message(self, *a):
                pass

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Cortado)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True).start()
        self.addCleanup(httpd.server_close)
        self.addCleanup(httpd.shutdown)
        c = N.ClienteGemini(CLAVE, "gemini-x", base_url=f"http://127.0.0.1:{httpd.server_address[1]}/v1beta", timeout=2)
        with self.assertRaises(N.ErrorPanki) as cm:
            c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertSinClave(cm.exception)

    def test_respuesta_demasiado_grande(self):
        self.srv.guion = [{"crudo": b'{"candidates": [{"content": {"parts": [{"text": "' + b"x" * 5000 + b'"}]}}]}'}]
        with mock.patch.object(N, "MAX_RESPUESTA", 1000):
            with self.assertRaises(N.ErrorPanki) as cm:
                self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("demasiado grande", str(cm.exception))

    def test_peticion_demasiado_grande_no_se_envia(self):
        with mock.patch.object(N, "MAX_PETICION", 100):
            with self.assertRaises(N.ErrorPanki):
                self.cliente().generar([{"role": "user", "parts": [{"text": "x" * 500}]}], "s", [])
        self.assertEqual(self.srv.peticiones, [])

    def test_contenido_no_serializable(self):
        with self.assertRaises(N.ErrorPanki):
            self.cliente().generar([{"role": "user", "parts": [{"text": object()}]}], "s", [])
        self.assertEqual(self.srv.peticiones, [])

    def test_texto_con_sustitutos_sueltos_no_rompe_el_envio(self):
        self.srv.guion = [r_texto("ok")]
        self.cliente().generar([{"role": "user", "parts": [{"text": "mal \ud800 bien ñ"}]}], "s", [])
        self.assertEqual(self.cuerpo(0)["contents"][0]["parts"][0]["text"], "mal ? bien ñ")

    # -- con `abrir` inyectado
    def test_abrir_inyectado(self):
        vistas = []

        class Respuesta:
            status = 200

            def __init__(self):
                self.cuerpo = json.dumps(r_texto("ok")["json"]).encode()

            def read(self, n=-1):
                datos, self.cuerpo = self.cuerpo[:n if n > 0 else None], self.cuerpo[n if n > 0 else len(self.cuerpo):]
                return datos

            def close(self):
                pass

        def abrir(solicitud, timeout=None):
            vistas.append((solicitud.full_url, solicitud.get_method(), dict(solicitud.header_items()), timeout))
            return Respuesta()

        c = N.ClienteGemini(CLAVE, "gemini-x", abrir=abrir, timeout=7)
        c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        url, metodo, cabeceras, timeout = vistas[0]
        self.assertEqual(url, "https://generativelanguage.googleapis.com/v1beta/models/gemini-x:generateContent")
        self.assertEqual(metodo, "POST")
        self.assertEqual(timeout, 7)
        self.assertEqual({k.lower(): v for k, v in cabeceras.items()}["x-goog-api-key"], CLAVE)
        self.assertNotIn(CLAVE, url)

    def test_abrir_que_falla_con_la_clave_en_el_mensaje(self):
        # http.client lanza ValueError("Invalid header value b'<valor>'"): el valor es la clave.
        def abrir(solicitud, timeout=None):
            raise ValueError(f"Invalid header value {CLAVE!r}")

        c = N.ClienteGemini(CLAVE, "gemini-x", abrir=abrir)
        with self.assertRaises(N.ErrorPanki) as cm:
            c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertSinClave(cm.exception)
        self.assertNotIn(CLAVE, self.registro.getvalue())

    def test_abrir_con_cualquier_excepcion_de_red(self):
        import urllib.error as ue
        import http.client as hc
        casos = [(ue.URLError(OSError("sin ruta")), "conexión"), (ue.URLError(TimeoutError()), "tardó"),
                 (TimeoutError(), "tardó"), (ConnectionResetError(), "conexión"), (hc.RemoteDisconnected("x"), "conexión"),
                 (hc.IncompleteRead(b"x"), "conexión"), (ue.URLError(Exception(f"cert {CLAVE}")), "conexión"),
                 (RuntimeError(CLAVE), "inesperado"), (KeyError(CLAVE), "inesperado")]
        for excepcion, esperado in casos:
            with self.subTest(excepcion=type(excepcion).__name__):
                def abrir(solicitud, timeout=None, _e=excepcion):
                    raise _e

                c = N.ClienteGemini(CLAVE, "gemini-x", abrir=abrir)
                with self.assertRaises(N.ErrorPanki) as cm:
                    c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
                self.assertIn(esperado, str(cm.exception))
                self.assertSinClave(cm.exception)

    def test_abrir_con_httperror_inyectado_se_cierra(self):
        import urllib.error as ue
        cuerpo = io.BytesIO(json.dumps({"error": {"message": f"vaya {CLAVE}"}}).encode())

        def abrir(solicitud, timeout=None):
            raise ue.HTTPError(solicitud.full_url, 403, "Forbidden", {}, cuerpo)

        c = N.ClienteGemini(CLAVE, "gemini-x", abrir=abrir)
        with self.assertRaises(N.ErrorPanki) as cm:
            c.generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
        self.assertIn("no aceptó la clave", str(cm.exception))
        self.assertSinClave(cm.exception)

    def test_la_clave_no_sale_en_ningun_error_ni_registro(self):
        for codigo in (400, 401, 403, 404, 429, 500, 503):
            self.srv.guion.append({"status": codigo, "json": {"error": {"message": f"eco {CLAVE} y x-goog-api-key: {CLAVE}"}}})
        self.srv.guion.append({"crudo": f"basura {CLAVE}".encode()})
        for _ in range(8):
            with self.assertRaises(N.ErrorPanki) as cm:
                self.cliente().generar([{"role": "user", "parts": [{"text": "x"}]}], "s", [])
            self.assertSinClave(cm.exception)


# =================================================================================================== Agente
class TestAgente(ConServidor):
    def setUp(self):
        super().setUp()
        self.ejecutor = EjecutorFalso()
        self.preguntas = []
        self.respuesta_aprobar = "una_vez"
        self.eventos = []

    def aprobar(self, solicitud):
        self.preguntas.append(solicitud)
        if isinstance(self.respuesta_aprobar, BaseException):
            raise self.respuesta_aprobar
        return self.respuesta_aprobar

    def agente(self, **kw):
        return N.Agente(self.cliente(), self.ejecutor, self.aprobar, **kw)

    def enviar(self, agente, texto="hola"):
        return agente.enviar(texto, self.eventos.append)

    def tipos(self):
        return [e[0] for e in self.eventos if e[0] != "estado"]

    def respuestas_de(self, i, j=-1):
        """functionResponse de la petición i, mensaje j."""
        return [p["functionResponse"] for p in self.cuerpo(i)["contents"][j]["parts"]]

    # -- flujo básico
    def test_solo_texto(self):
        self.srv.guion = [r_texto("Hola, soy pAnkI.")]
        agente = self.agente()
        self.assertEqual(self.enviar(agente, "Hola"), "Hola, soy pAnkI.")
        self.assertEqual(self.eventos, [("estado", "Pensando…"), ("texto", "Hola, soy pAnkI.")])
        validar_historial(self, agente.historial)
        peticion = self.cuerpo(0)
        self.assertEqual(peticion["contents"], [{"role": "user", "parts": [{"text": "Hola"}]}])
        self.assertIn("pAnkI", peticion["systemInstruction"]["parts"][0]["text"])
        self.assertEqual([d["name"] for d in peticion["tools"][0]["functionDeclarations"]], list(EjecutorFalso.NOMBRES))

    def test_llamada_de_herramienta_y_respuesta(self):
        self.srv.guion = [r_llamadas(("info_sistema", {"detalle": "corto"})), r_texto("Tienes 8 GB.")]
        agente = self.agente()
        self.assertEqual(self.enviar(agente, "¿Cuánta RAM?"), "Tienes 8 GB.")
        self.assertEqual(self.tipos(), ["herramienta", "resultado", "texto"])
        self.assertEqual(self.eventos[1], ("herramienta", "info_sistema", 'detalle="corto"'))
        self.assertIn(("resultado", "info_sistema", True, "info_sistema hecho"), self.eventos)
        self.assertEqual(self.ejecutor.ejecutadas, ["info_sistema"])
        self.assertEqual(self.preguntas, [], "sin solicitud no se pregunta")
        contenidos = self.cuerpo(1)["contents"]
        self.assertEqual([c["role"] for c in contenidos], ["user", "model", "user"])
        self.assertEqual(contenidos[1]["parts"], [{"functionCall": {"name": "info_sistema", "args": {"detalle": "corto"}}}])
        self.assertEqual(contenidos[2]["parts"], [{"functionResponse": {"name": "info_sistema", "response": {
            "herramienta": "info_sistema", "args": {"detalle": "corto"}}}}])
        validar_historial(self, agente.historial)

    def test_el_ultimo_evento_texto_es_el_valor_devuelto(self):
        self.srv.guion = [r_llamadas(("info_sistema", {}), texto="Voy a mirar."), r_texto("Listo.")]
        resultado = self.enviar(self.agente())
        textos = [e[1] for e in self.eventos if e[0] == "texto"]
        self.assertEqual(textos, ["Voy a mirar.", "Listo."])
        self.assertEqual(textos[-1], resultado)
        nombres = [e[0] for e in self.eventos]
        self.assertLess(self.eventos.index(("texto", "Voy a mirar.")), nombres.index("herramienta"),
                        "la explicación se ve antes de la herramienta")

    def test_llamadas_paralelas_un_solo_mensaje_y_en_orden(self):
        self.srv.guion = [r_llamadas(("info_sistema", {"n": 1}), ("leer_archivo", {"ruta": "a"}), ("info_sistema", {"n": 3}),
                                     firma="FIRMA"), r_texto("Hecho.")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(self.ejecutor.ejecutadas, ["info_sistema", "leer_archivo", "info_sistema"])
        contenidos = self.cuerpo(1)["contents"]
        self.assertEqual([c["role"] for c in contenidos], ["user", "model", "user"])
        respuestas = self.respuestas_de(1)
        self.assertEqual([r["name"] for r in respuestas], ["info_sistema", "leer_archivo", "info_sistema"])
        self.assertEqual([r["response"]["args"] for r in respuestas], [{"n": 1}, {"ruta": "a"}, {"n": 3}])
        self.assertEqual(len(self.preguntas), 1)
        validar_historial(self, agente.historial)

    def test_firma_de_pensamiento_se_conserva_tal_cual(self):
        firma = "CiQBjz1rX/+Zq0=a1B2c3D4e5F6g7H8i9J0kLmNoPqRsTuVwXyZ+/==ñ"
        contenido = {"role": "model", "parts": [
            {"text": "pienso", "thoughtSignature": "otra/firma+1=="},
            {"functionCall": {"name": "info_sistema", "args": {"a": [1, 2, {"b": None}]}}, "thoughtSignature": firma,
             "campoNuevo": {"x": [True, 1.5, "ñ"]}}], "campoRaro": "valor"}
        servida = {"json": {"candidates": [{"content": contenido, "finishReason": "STOP", "index": 0, "avgLogprobs": -0.1}],
                            "usageMetadata": {"thoughtsTokenCount": 12}, "modelVersion": "x"}}
        otra = {"role": "model", "parts": [
            {"functionCall": {"name": "info_sistema", "args": {}}, "thoughtSignature": "segunda==Firma/+"}]}
        self.srv.guion = [servida, {"json": {"candidates": [{"content": otra}]}}, r_texto("fin")]
        agente = self.agente()
        self.enviar(agente)
        enviado = self.cuerpo(1)["contents"][1]
        self.assertEqual(enviado, contenido)
        self.assertIn(json.dumps(firma, ensure_ascii=False).encode("utf-8"), self.srv.peticiones[1]["cuerpo"])
        # Y se sigue enviando en los pasos siguientes.
        contenidos = self.cuerpo(2)["contents"]
        self.assertEqual(contenidos[1], contenido)
        self.assertEqual(contenidos[3], otra)
        self.assertEqual(agente.historial[1], contenido)
        validar_historial(self, agente.historial)

    def test_el_id_de_la_llamada_se_repite_en_la_respuesta(self):
        self.srv.guion = [r_llamadas(("info_sistema", {}), ("leer_archivo", {"ruta": "a"}), ids=True), r_texto("ok")]
        self.enviar(self.agente())
        self.assertEqual([r["id"] for r in self.respuestas_de(1)], ["llamada-0", "llamada-1"])
        self.srv.guion = [r_llamadas(("info_sistema", {})), r_texto("ok")]
        self.enviar(self.agente())
        self.assertNotIn("id", self.respuestas_de(3)[0])

    def test_las_partes_de_pensamiento_no_se_muestran(self):
        self.srv.guion = [{"json": {"candidates": [{"content": {"role": "model", "parts": [
            {"text": "razonando...", "thought": True}, {"text": "La respuesta."}]}}]}}]
        self.assertEqual(self.enviar(self.agente()), "La respuesta.")

    def test_texto_cortado_por_longitud_lo_avisa(self):
        self.srv.guion = [r_texto("Empiezo y", fin="MAX_TOKENS")]
        resultado = self.enviar(self.agente())
        self.assertTrue(resultado.startswith("Empiezo y"))
        self.assertIn("se cortó", resultado)

    def test_conversacion_de_varios_turnos(self):
        self.srv.guion = [r_texto("uno"), r_llamadas(("info_sistema", {})), r_texto("dos"), r_texto("tres")]
        agente = self.agente()
        for t in ("a", "b", "c"):
            self.enviar(agente, t)
        validar_historial(self, agente.historial)
        self.assertEqual(len(self.cuerpo(3)["contents"]), 7)

    # -- aprobación
    def test_aprobacion_denegada(self):
        self.respuesta_aprobar = "no"
        self.srv.guion = [r_llamadas(("mover_archivo", {"origen": "a", "destino": "b"})), r_texto("No lo moví.")]
        agente = self.agente()
        self.assertEqual(self.enviar(agente), "No lo moví.")
        self.assertEqual(self.ejecutor.ejecutadas, [])
        self.assertEqual(self.respuestas_de(1)[0]["response"], {"error": "El usuario no permitió esta acción."})
        self.assertIn(("resultado", "mover_archivo", False, "No permitido por el usuario."), self.eventos)
        self.assertEqual(self.preguntas[0].titulo, "Mover a.txt")
        validar_historial(self, agente.historial)

    def test_una_vez_ejecuta_y_vuelve_a_preguntar(self):
        self.srv.guion = [r_llamadas(("leer_archivo", {"ruta": "a"})), r_llamadas(("leer_archivo", {"ruta": "b"})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(len(self.preguntas), 2)
        self.assertEqual(self.ejecutor.ejecutadas, ["leer_archivo", "leer_archivo"])
        self.assertEqual(agente.conversacion_grants, set())

    def test_conversacion_concede_y_la_segunda_vez_no_pregunta(self):
        self.respuesta_aprobar = "conversacion"
        self.srv.guion = [r_llamadas(("leer_archivo", {"ruta": "a"})), r_llamadas(("leer_archivo", {"ruta": "b"})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(len(self.preguntas), 1)
        self.assertEqual(self.ejecutor.ejecutadas, ["leer_archivo", "leer_archivo"])
        self.assertEqual(agente.conversacion_grants, {"carpeta:/home/u/Docs"})
        # Y en otro envío de la misma conversación tampoco.
        self.srv.guion = [r_llamadas(("leer_archivo", {"ruta": "c"})), r_texto("ok")]
        self.enviar(agente, "otra")
        self.assertEqual(len(self.preguntas), 1)
        self.assertEqual(len(self.ejecutor.ejecutadas), 3)

    def test_reiniciar_olvida_los_permisos_y_el_historial(self):
        self.respuesta_aprobar = "conversacion"
        self.srv.guion = [r_llamadas(("leer_archivo", {"ruta": "a"})), r_texto("ok"), r_llamadas(("leer_archivo", {"ruta": "a"})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        agente.reiniciar()
        self.assertEqual(agente.conversacion_grants, set())
        self.assertEqual(agente.historial, [])
        self.enviar(agente)
        self.assertEqual(len(self.preguntas), 2, "tras reiniciar hay que volver a pedir permiso")

    def test_recordable_none_no_se_recuerda(self):
        self.respuesta_aprobar = "conversacion"
        self.srv.guion = [r_llamadas(("mover_archivo", {"a": 1})), r_llamadas(("mover_archivo", {"a": 2})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(len(self.preguntas), 2, "una acción que modifica se pregunta siempre")
        self.assertEqual(self.ejecutor.ejecutadas, ["mover_archivo", "mover_archivo"])
        self.assertEqual(agente.conversacion_grants, set())

    def test_una_respuesta_rara_de_aprobar_es_un_no(self):
        for rara in ("si", "sí", "", None, True, 1, "UNA_VEZ", ["una_vez"], RuntimeError("falla la interfaz")):
            with self.subTest(rara=repr(rara)):
                self.ejecutor.ejecutadas.clear()
                self.respuesta_aprobar = rara
                self.srv.guion = [r_llamadas(("mover_archivo", {})), r_texto("ok")]
                self.enviar(self.agente())
                self.assertEqual(self.ejecutor.ejecutadas, [])

    # -- fallos de herramientas
    def test_herramienta_desconocida(self):
        self.srv.guion = [r_llamadas(("borrar_todo", {"ruta": "/"})), r_texto("No puedo.")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(self.ejecutor.preparadas, [])
        self.assertEqual(self.ejecutor.ejecutadas, [])
        self.assertEqual(self.preguntas, [])
        respuesta = self.respuestas_de(1)[0]
        self.assertEqual(respuesta["name"], "borrar_todo")
        self.assertIn("no existe", respuesta["response"]["error"])
        validar_historial(self, agente.historial)

    def test_llamada_malformada(self):
        malformadas = [{"functionCall": {"args": {}}}, {"functionCall": {"name": 5, "args": {}}},
                       {"functionCall": {"name": "info_sistema", "args": "texto"}},
                       {"functionCall": {"name": "info_sistema", "args": [1]}}]
        for parte in malformadas:
            with self.subTest(parte=parte):
                self.srv.guion = [{"json": {"candidates": [{"content": {"role": "model", "parts": [parte]}}]}}, r_texto("ok")]
                agente = self.agente()
                self.enviar(agente)
                # Aunque la llamada del modelo esté rota, cada functionCall recibe su functionResponse.
                self.assertEqual([m["role"] for m in agente.historial], ["user", "model", "user", "model"])
                self.assertEqual(len(agente.historial[1]["parts"]), len(agente.historial[2]["parts"]))
                self.assertIn("error", agente.historial[2]["parts"][0]["functionResponse"]["response"])
        self.assertEqual(self.ejecutor.ejecutadas, [])

    def test_args_ausentes_son_un_objeto_vacio(self):
        self.srv.guion = [{"json": {"candidates": [{"content": {"role": "model", "parts": [
            {"functionCall": {"name": "info_sistema"}}]}}]}}, r_texto("ok")]
        self.enviar(self.agente())
        self.assertEqual(self.ejecutor.preparadas, [("info_sistema", {})])

    def test_error_de_herramienta_en_preparar_no_molesta_al_usuario(self):
        self.srv.guion = [r_llamadas(("invalida", {"ruta": "/etc"})), r_texto("Esa ruta no vale.")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(self.preguntas, [])
        self.assertEqual(self.respuestas_de(1)[0]["response"], {"error": "La ruta no está permitida."})
        self.assertIn(("resultado", "invalida", False, "La ruta no está permitida."), self.eventos)
        validar_historial(self, agente.historial)

    def test_excepcion_inesperada_al_preparar_no_tumba_el_bucle(self):
        self.srv.guion = [r_llamadas(("rota_preparar", {})), r_texto("Algo falló.")]
        agente = self.agente()
        self.assertEqual(self.enviar(agente), "Algo falló.")
        error = self.respuestas_de(1)[0]["response"]["error"]
        self.assertNotIn("Traceback", error)
        self.assertNotIn(SECRETO_INTERNO, error)
        self.assertNotIn(SECRETO_INTERNO, self.srv.peticiones[1]["cuerpo"].decode())
        self.assertNotIn(SECRETO_INTERNO, " ".join(str(e) for e in self.eventos))
        validar_historial(self, agente.historial)

    def test_excepcion_inesperada_al_ejecutar_no_tumba_el_bucle(self):
        self.srv.guion = [r_llamadas(("rota_ejecutar", {}), ("info_sistema", {})), r_texto("Una falló, la otra no.")]
        agente = self.agente()
        self.assertEqual(self.enviar(agente), "Una falló, la otra no.")
        respuestas = self.respuestas_de(1)
        self.assertEqual([r["name"] for r in respuestas], ["rota_ejecutar", "info_sistema"])
        self.assertNotIn(SECRETO_INTERNO, json.dumps(respuestas))
        self.assertNotIn("Traceback", json.dumps(respuestas))
        self.assertIn("error", respuestas[0]["response"])
        self.assertEqual(respuestas[1]["response"]["herramienta"], "info_sistema")
        self.assertIn(("resultado", "rota_ejecutar", False, "La herramienta falló de forma inesperada."), self.eventos)
        self.assertNotIn(SECRETO_INTERNO, self.registro.getvalue(), "ni una ruta interna en el registro")
        validar_historial(self, agente.historial)

    def test_los_argumentos_no_se_comparten_con_el_historial(self):
        self.srv.guion = [r_llamadas(("muta_args", {"x": 1})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        self.assertEqual(agente.historial[1]["parts"][0]["functionCall"]["args"], {"x": 1})
        self.assertEqual(self.cuerpo(1)["contents"][1]["parts"][0]["functionCall"]["args"], {"x": 1})

    def test_resultado_enorme_o_no_serializable(self):
        self.srv.guion = [r_llamadas(("enorme", {}), ("opaca", {})), r_texto("ok")]
        agente = self.agente()
        self.enviar(agente)
        respuestas = self.respuestas_de(1)
        self.assertIn("demasiado grande", respuestas[0]["response"]["error"])
        self.assertIn("error", respuestas[1]["response"])
        validar_historial(self, agente.historial)

    def test_alevento_que_falla_no_tumba_el_agente(self):
        self.srv.guion = [r_llamadas(("info_sistema", {})), r_texto("ok")]

        def malo(evento):
            raise RuntimeError("la interfaz falló")

        self.assertEqual(self.agente().enviar("hola", malo), "ok")
        self.srv.guion = [r_texto("ok")]
        self.assertEqual(self.agente().enviar("hola", None), "ok")

    # -- límites
    def test_max_pasos(self):
        self.srv.guion = [r_llamadas(("info_sistema", {"n": i})) for i in range(10)]
        agente = self.agente(max_pasos=3)
        resultado = self.enviar(agente)
        self.assertEqual(len(self.srv.peticiones), 3)
        self.assertEqual(len(self.ejecutor.ejecutadas), 3)
        self.assertIn("límite de 3 pasos", resultado)
        self.assertEqual(self.eventos[-1], ("texto", resultado))
        validar_historial(self, agente.historial)
        # La conversación sigue siendo utilizable.
        self.srv.guion = [r_texto("Sigo aquí.")]
        self.assertEqual(self.enviar(agente, "continúa"), "Sigo aquí.")
        validar_historial(self, self.cuerpo(3)["contents"], cerrado=False)
        validar_historial(self, agente.historial)

    def test_max_pasos_por_defecto_son_8(self):
        self.srv.guion = [r_llamadas(("info_sistema", {})) for _ in range(20)]
        self.enviar(self.agente())
        self.assertEqual(len(self.srv.peticiones), 8)

    def test_mensaje_vacio_o_demasiado_largo(self):
        agente = self.agente()
        for texto in ("", "   \n", None, 5):
            with self.assertRaises(N.ErrorPanki):
                self.enviar(agente, texto)
        with self.assertRaises(N.ErrorPanki):
            self.enviar(agente, "x" * (N.MAX_MENSAJE + 1))
        self.assertEqual(agente.historial, [])
        self.assertEqual(self.srv.peticiones, [])

    def test_respuesta_sin_texto_es_un_error(self):
        self.srv.guion = [{"json": {"candidates": [{"content": {"role": "model", "parts": [{"text": "   "}]}}]}}]
        agente = self.agente()
        with self.assertRaises(N.ErrorPanki):
            self.enviar(agente)
        self.assertEqual(agente.historial, [])

    def test_un_envio_a_la_vez(self):
        dentro, liberar = threading.Event(), threading.Event()
        self.ejecutor.al_ejecutar = lambda p: (dentro.set(), liberar.wait(5))
        self.srv.guion = [r_llamadas(("info_sistema", {})), r_texto("ok")]
        agente = self.agente()
        resultados = []
        hilo = threading.Thread(target=lambda: resultados.append(agente.enviar("a", lambda e: None)))
        hilo.start()
        self.assertTrue(dentro.wait(5))
        with self.assertRaises(N.ErrorPanki) as cm:
            agente.enviar("b", lambda e: None)
        self.assertIn("ya está trabajando", str(cm.exception))
        liberar.set()
        hilo.join(5)
        self.assertEqual(resultados, ["ok"])
        validar_historial(self, agente.historial)

    # -- errores de la API en mitad de la conversación
    def test_error_de_la_api_en_el_primer_paso_elimina_el_turno(self):
        self.srv.guion = [{"status": 429, "json": {"error": {"message": "cuota"}}}, r_texto("ya sí")]
        agente = self.agente()
        with self.assertRaises(N.ErrorPanki) as cm:
            self.enviar(agente, "hola")
        self.assertEqual(cm.exception.codigo, 429)
        self.assertEqual(agente.historial, [], "el mensaje no se envió: se puede reenviar")
        self.assertEqual(self.tipos(), [], "un error no emite el evento de texto")
        self.assertEqual(self.enviar(agente, "hola"), "ya sí")
        validar_historial(self, agente.historial)

    def test_error_de_la_api_despues_de_ejecutar_herramientas_conserva_lo_hecho(self):
        self.srv.guion = [r_llamadas(("info_sistema", {})), {"status": 500, "json": {}}, r_texto("Retomo.")]
        agente = self.agente()
        with self.assertRaises(N.ErrorPanki):
            self.enviar(agente, "haz cosas")
        validar_historial(self, agente.historial)
        self.assertEqual(len(agente.historial), 4, "usuario, llamada, resultado y nota del modelo")
        self.assertEqual(agente.historial[-1]["role"], "model")
        self.assertEqual(self.enviar(agente, "reintenta"), "Retomo.")
        validar_historial(self, self.cuerpo(2)["contents"], cerrado=False)
        validar_historial(self, agente.historial)

    def test_respuesta_bloqueada_a_mitad_deja_el_historial_valido(self):
        self.srv.guion = [r_llamadas(("info_sistema", {})), {"json": {"candidates": [{"finishReason": "SAFETY"}]}}]
        agente = self.agente()
        with self.assertRaises(N.ErrorPanki):
            self.enviar(agente)
        validar_historial(self, agente.historial)

    # -- cancelar
    def test_cancelar_entre_llamadas_paralelas(self):
        agente = self.agente()
        self.ejecutor.al_ejecutar = lambda p: agente.cancelar()
        self.srv.guion = [r_llamadas(("info_sistema", {"n": 1}), ("info_sistema", {"n": 2}), ("info_sistema", {"n": 3})),
                          r_texto("no debería pedirse")]
        self.assertEqual(self.enviar(agente), "Cancelado.")
        self.assertEqual(self.ejecutor.ejecutadas, ["info_sistema"], "solo la que ya estaba en marcha")
        self.assertEqual(len(self.srv.peticiones), 1)
        self.assertEqual(self.eventos[-1], ("texto", "Cancelado."))
        validar_historial(self, agente.historial)
        respuestas = [p["functionResponse"]["response"] for p in agente.historial[2]["parts"]]
        self.assertEqual(respuestas[0]["herramienta"], "info_sistema")
        self.assertEqual(respuestas[1:], [{"error": "Cancelado por el usuario."}] * 2)
        self.assertEqual(agente.historial[-1]["role"], "model")
        # El historial sirve para seguir conversando.
        self.srv.guion = [r_texto("Aquí sigo.")]
        self.assertEqual(self.enviar(agente, "¿y ahora?"), "Aquí sigo.")
        validar_historial(self, self.cuerpo(1)["contents"], cerrado=False)
        validar_historial(self, agente.historial)

    def test_cancelar_mientras_se_decide_la_aprobacion(self):
        agente = self.agente()

        def aprobar_y_cancelar(solicitud):
            agente.cancelar()
            return "una_vez"

        agente.aprobar = aprobar_y_cancelar
        self.srv.guion = [r_llamadas(("mover_archivo", {"a": 1})), r_texto("no")]
        self.assertEqual(self.enviar(agente), "Cancelado.")
        self.assertEqual(self.ejecutor.ejecutadas, [], "no se ejecuta lo que se cancela antes de empezar")
        validar_historial(self, agente.historial)

    def test_cancelar_antes_del_primer_paso_elimina_el_turno(self):
        agente = self.agente()
        cancelado = []

        def alevento(evento):
            if not cancelado:
                cancelado.append(True)
                agente.cancelar()

        self.assertEqual(agente.enviar("hola", alevento), "Cancelado.")
        self.assertEqual(self.srv.peticiones, [])
        self.assertEqual(agente.historial, [])

    def test_cancelar_mientras_espera_a_gemini_descarta_la_respuesta(self):
        cliente = ClienteFalso([resp_dict(("info_sistema", {})), resp_dict(texto="no")])
        agente = N.Agente(cliente, self.ejecutor, self.aprobar)
        cliente.antes = agente.cancelar
        self.assertEqual(agente.enviar("hola", self.eventos.append), "Cancelado.")
        self.assertEqual(self.ejecutor.ejecutadas, [], "la llamada que llegó tarde no se ejecuta")
        self.assertEqual(agente.historial, [])
        self.assertEqual(len(cliente.recibidos), 1)

    def test_cancelar_tras_ejecutar_conserva_lo_hecho(self):
        cliente = ClienteFalso([resp_dict(("info_sistema", {})), resp_dict(texto="no")])
        agente = N.Agente(cliente, self.ejecutor, self.aprobar)
        self.ejecutor.al_ejecutar = lambda p: agente.cancelar()
        self.assertEqual(agente.enviar("hola", self.eventos.append), "Cancelado.")
        validar_historial(self, agente.historial)
        self.assertEqual(len(agente.historial), 4)
        self.assertEqual(len(cliente.recibidos), 1)

    def test_cancelar_con_el_agente_parado_solo_afecta_al_siguiente_envio(self):
        self.srv.guion = [r_texto("normal")]
        agente = self.agente()
        agente.cancelar()
        self.assertEqual(self.enviar(agente), "Cancelado.")
        self.assertEqual(self.srv.peticiones, [])
        self.assertEqual(agente.historial, [])
        self.assertEqual(self.enviar(agente), "normal")

    def test_la_cancelacion_no_se_arrastra_al_envio_siguiente(self):
        agente = self.agente()
        self.ejecutor.al_ejecutar = lambda p: agente.cancelar()
        self.srv.guion = [r_llamadas(("info_sistema", {})), r_texto("limpio")]
        self.assertEqual(self.enviar(agente), "Cancelado.")
        self.ejecutor.al_ejecutar = None
        self.assertEqual(self.enviar(agente, "otra"), "limpio")

    def test_reiniciar_mientras_trabaja_cancela_y_borra_al_terminar(self):
        agente = self.agente()
        self.ejecutor.al_ejecutar = lambda p: agente.reiniciar()
        self.srv.guion = [r_llamadas(("leer_archivo", {"ruta": "a"})), r_texto("no")]
        self.respuesta_aprobar = "conversacion"
        self.assertEqual(self.enviar(agente), "Cancelado.")
        self.assertEqual(agente.historial, [])
        self.assertEqual(agente.conversacion_grants, set())
        self.ejecutor.al_ejecutar = None
        self.srv.guion = [r_texto("de cero")]
        self.assertEqual(self.enviar(agente, "hola"), "de cero")
        self.assertEqual(len(agente.historial), 2)


# ======================================================================================= Recorte del historial
class TestRecorte(unittest.TestCase):
    def preparar(self, pasos, tam, firma=True):
        """Agente con cliente falso: `pasos` llamadas a info_sistema que devuelven `tam` caracteres cada una."""
        ejecutor = EjecutorFalso()
        original = ejecutor.ejecutar
        ejecutor.ejecutar = lambda p: Resultado(True, {"datos": "x" * tam, "n": len(ejecutor.ejecutadas)}, "ok") \
            if original(p) is not None else None
        respuestas = [resp_dict(("info_sistema", {"n": i}), firma=f"firma-{i}" if firma else None) for i in range(pasos)]
        respuestas.append(resp_dict(texto="fin"))
        cliente = ClienteFalso(respuestas)
        return N.Agente(cliente, ejecutor, lambda s: "una_vez", max_pasos=pasos + 1), cliente

    def omitidos(self, contenidos):
        return [p["functionResponse"]["response"] == {"omitido": N.OMITIDO}
                for m in contenidos for p in m["parts"] if "functionResponse" in p]

    def test_los_resultados_antiguos_se_sustituyen_y_el_ultimo_se_conserva(self):
        agente, cliente = self.preparar(pasos=6, tam=60_000)
        agente.enviar("hola", lambda e: None)
        ultima = cliente.recibidos[-1]
        self.assertLessEqual(len(json.dumps(ultima, ensure_ascii=False)), N.LIMITE_HISTORIAL)
        marcas = self.omitidos(ultima)
        self.assertEqual(len(marcas), 6)
        self.assertTrue(marcas[0], "el más antiguo se omite primero")
        self.assertFalse(marcas[-1], "el más reciente se conserva")
        self.assertEqual(marcas, sorted(marcas, reverse=True), "se omite de más antiguo a más reciente, sin saltos")
        self.assertEqual(ultima[-1]["parts"][0]["functionResponse"]["response"]["datos"], "x" * 60_000)
        validar_historial(self, ultima, cerrado=False)

    def test_el_recorte_no_rompe_roles_ni_correspondencias_ni_firmas(self):
        agente, cliente = self.preparar(pasos=6, tam=60_000)
        agente.enviar("hola", lambda e: None)
        for contenidos in cliente.recibidos:
            validar_historial(self, contenidos, cerrado=False)
        ultima = cliente.recibidos[-1]
        llamadas = [p for m in ultima for p in m["parts"] if "functionCall" in p]
        self.assertEqual([p["thoughtSignature"] for p in llamadas], [f"firma-{i}" for i in range(6)])
        self.assertEqual([p["functionCall"]["args"]["n"] for p in llamadas], list(range(6)))
        respuestas = [p["functionResponse"]["name"] for m in ultima for p in m["parts"] if "functionResponse" in p]
        self.assertEqual(respuestas, ["info_sistema"] * 6)
        validar_historial(self, agente.historial)

    def test_el_omitido_conserva_nombre_e_id(self):
        agente, cliente = self.preparar(pasos=5, tam=60_000)
        agente.enviar("hola", lambda e: None)
        primera = cliente.recibidos[-1][2]["parts"][0]["functionResponse"]
        self.assertEqual(primera, {"name": "info_sistema", "response": {"omitido": "[contenido omitido]"}})

    def test_por_debajo_del_limite_no_se_toca_nada(self):
        agente, cliente = self.preparar(pasos=4, tam=10_000)
        agente.enviar("hola", lambda e: None)
        self.assertFalse(any(self.omitidos(cliente.recibidos[-1])))

    def test_recorta_solo_lo_necesario(self):
        agente, cliente = self.preparar(pasos=4, tam=60_000)  # 4 x 60 000 = 240 000 > 200 000
        agente.enviar("hola", lambda e: None)
        marcas = self.omitidos(cliente.recibidos[-1])
        self.assertEqual(marcas, [True, False, False, False])

    def test_lo_que_no_es_resultado_de_herramienta_no_se_toca(self):
        agente = N.Agente(ClienteFalso([resp_dict(texto="a")]), EjecutorFalso(), lambda s: "no")
        agente.historial = [{"role": "user", "parts": [{"text": "T" * 150_000}]},
                            {"role": "model", "parts": [{"text": "M" * 150_000}]}]
        agente.enviar("sigue", lambda e: None)
        self.assertEqual(agente.historial[0]["parts"][0]["text"], "T" * 150_000)
        self.assertEqual(agente.historial[1]["parts"][0]["text"], "M" * 150_000)

    def test_idempotente(self):
        agente, _ = self.preparar(pasos=5, tam=60_000)
        agente.enviar("hola", lambda e: None)
        antes = copy.deepcopy(agente.historial)
        agente._recortar()
        agente._recortar()
        self.assertEqual(agente.historial, antes)


# =========================================================================================== crear_agente
class TestCrearAgente(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.tmp.name, "HOME": self.tmp.name})
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(N.VARIABLE_CLAVE, None)
        self.creados = []
        creados = self.creados

        class EjecutorGuardado:
            def __init__(self, hogar=None, permitir_comandos=False, ajustes=None, correr=None, abrir=None, estado_dir=None):
                self.hogar, self.permitir_comandos = hogar, permitir_comandos
                creados.append(self)

            def declaraciones(self):
                return []

        modulo = types.ModuleType("infinity_shell.panki_herramientas")
        modulo.Ejecutor = EjecutorGuardado
        modulo.ErrorHerramienta = ErrorHerramienta
        parche = mock.patch.dict(sys.modules, {"infinity_shell.panki_herramientas": modulo})
        parche.start()
        self.addCleanup(parche.stop)

    def test_sin_clave(self):
        with self.assertRaises(N.ErrorPanki) as cm:
            N.crear_agente(lambda s: "no", valores={})
        self.assertIn("falta la clave de Gemini", str(cm.exception))
        self.assertEqual(self.creados, [])

    def test_con_clave_del_entorno(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        agente = N.crear_agente(lambda s: "no", valores={"panki.modelo": "gemini-2.5-flash", "panki.comandos": True},
                                hogar="/home/prueba")
        self.assertIsInstance(agente, N.Agente)
        self.assertEqual(agente.cliente.modelo, "gemini-2.5-flash")
        self.assertEqual(agente.cliente.base_url, N.BASE_URL)
        self.assertNotIn(CLAVE, repr(agente.cliente))
        self.assertEqual(self.creados[0].hogar, "/home/prueba")
        self.assertIs(self.creados[0].permitir_comandos, True)
        self.assertIn("/home/prueba", agente.sistema)
        self.assertIn(N._fecha(), agente.sistema)
        self.assertNotIn("{", agente.sistema)

    def test_con_clave_guardada(self):
        N.guardar_clave(CLAVE)
        agente = N.crear_agente(lambda s: "no", valores={})
        self.assertEqual(agente.cliente._clave, CLAVE)

    def test_valores_por_defecto(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        agente = N.crear_agente(lambda s: "no", valores={})
        self.assertEqual(agente.cliente.modelo, "gemini-flash-latest")
        self.assertIs(self.creados[0].permitir_comandos, False)
        self.assertEqual(self.creados[0].hogar, os.path.expanduser("~"))

    def test_comandos_solo_con_true_de_verdad(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        for valor in ("true", 1, "si", None, [True]):
            N.crear_agente(lambda s: "no", valores={"panki.comandos": valor})
            self.assertIs(self.creados[-1].permitir_comandos, False, valor)

    def test_sin_valores_usa_ajustes(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        pedidos = []

        def valor(id, ruta=None):
            pedidos.append(id)
            return {"panki.modelo": "modelo-de-ajustes", "panki.comandos": True}.get(id)

        with mock.patch.object(N._ajustes, "valor", valor):
            agente = N.crear_agente(lambda s: "no")
        self.assertEqual(agente.cliente.modelo, "modelo-de-ajustes")
        self.assertEqual(sorted(pedidos), ["panki.comandos", "panki.modelo"])

    def test_ajustes_que_aun_no_conocen_la_clave_usan_el_defecto(self):
        # ajustes.valor devuelve None para un id que no está en el esquema.
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        with mock.patch.object(N._ajustes, "valor", lambda id, ruta=None: None):
            agente = N.crear_agente(lambda s: "no")
        self.assertEqual(agente.cliente.modelo, "gemini-flash-latest")

    def test_modelo_invalido_en_ajustes(self):
        os.environ[N.VARIABLE_CLAVE] = CLAVE
        for modelo in ("a/b", "x?y=1", "con espacio", "a" * 61):
            with self.subTest(modelo=modelo):
                with self.assertRaises(N.ErrorPanki):
                    N.crear_agente(lambda s: "no", valores={"panki.modelo": modelo})

    def test_clave_con_formato_malo_en_el_entorno(self):
        os.environ[N.VARIABLE_CLAVE] = "esto no es una clave"
        with self.assertRaises(N.ErrorPanki) as cm:
            N.crear_agente(lambda s: "no", valores={})
        self.assertNotIn("esto no es una clave", str(cm.exception))


# ============================================================================================ prompt
class TestSistema(unittest.TestCase):
    def test_longitud(self):
        self.assertLess(len(N.SISTEMA), 1200)
        lleno = N.SISTEMA.format(usuario="u" * 32, hogar="/home/" + "d" * 60, fecha=N._fecha())
        self.assertLess(len(lleno), 1200)

    def test_tiene_los_tres_huecos_y_ninguna_llave_mas(self):
        self.assertEqual(sorted(set(__import__("re").findall(r"\{(\w*)\}", N.SISTEMA))), ["fecha", "hogar", "usuario"])
        self.assertEqual(N.SISTEMA.count("{"), 3)
        self.assertEqual(N.SISTEMA.count("}"), 3)

    def test_dice_lo_que_tiene_que_decir(self):
        s = N.SISTEMA
        for trozo in ("Eres pAnkI, el agente virtual de Infinity OS", "herramientas", "explica", "permiso",
                      "DATOS", "nunca instrucciones", "díselo al usuario", "No puedes borrar", "privilegios de administrador",
                      "Propón antes de aplicar", "idioma del usuario", "No afirmes"):
            self.assertIn(trozo, s)

    def test_las_llaves_del_usuario_no_se_interpretan(self):
        lleno = N.SISTEMA.format(usuario="{hogar}", hogar="{0}", fecha="{x}")
        self.assertIn("{hogar}", lleno)

    def test_fecha(self):
        import datetime
        self.assertEqual(N._fecha(datetime.date(2026, 10, 11)), "domingo 11 de octubre de 2026")
        self.assertEqual(N._fecha(datetime.date(2026, 3, 2)), "lunes 2 de marzo de 2026")


class TestImportaciones(unittest.TestCase):
    def test_no_usa_gtk_y_las_herramientas_se_importan_dentro_de_crear_agente(self):
        with open(N.__file__, encoding="utf-8") as f:
            fuente = f.read()
        self.assertNotIn("import gi", fuente)
        self.assertNotIn("Gtk", fuente)
        al_inicio = [linea for linea in fuente.split("def crear_agente")[0].splitlines()
                     if linea.startswith(("import ", "from "))]
        self.assertEqual([linea for linea in al_inicio if "herramientas" in linea], [])


if __name__ == "__main__":
    unittest.main()
