import hashlib
import io
import json
import os
import re
import shutil
import sys
import tarfile
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "..", "overlay", "usr", "lib", "infinity"))

import game_environment as G  # noqa: E402

NOMBRE = "GE-ProtonTEST-1"


def _crear_tgz(entradas):
    """Tarball gzip con {ruta: bytes}. Sirve para simular un release de GE-Proton."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for ruta, datos in entradas.items():
            info = tarfile.TarInfo(ruta)
            info.size = len(datos)
            tar.addfile(info, io.BytesIO(datos))
    return buf.getvalue()


TGZ = _crear_tgz({f"{NOMBRE}/proton": b"#!/bin/sh\necho proton\n", f"{NOMBRE}/files/lib/x.so": b"\x7fELF"})
SHA = hashlib.sha512(TGZ).hexdigest()


class _Servidor(BaseHTTPRequestHandler):
    """Imita la API de GitHub y la descarga de assets; admite Range para probar la reanudación."""
    archivos = {}
    release = {}
    peticiones = []

    def log_message(self, *args):
        pass

    def _enviar(self, codigo, cuerpo=b""):
        self.send_response(codigo)
        self.send_header("Content-Length", str(len(cuerpo)))
        self.end_headers()
        self.wfile.write(cuerpo)

    def do_GET(self):
        _Servidor.peticiones.append((self.path, self.headers.get("Range")))
        if "/releases/" in self.path:
            if self.path.endswith("/NO-EXISTE"):
                return self._enviar(404, b'{"message":"Not Found"}')
            return self._enviar(200, json.dumps(_Servidor.release).encode())
        if self.path.startswith("/files/"):
            datos = _Servidor.archivos.get(urllib.parse.unquote(self.path[len("/files/"):]))
            if datos is None:
                return self._enviar(404)
            rango = self.headers.get("Range")
            if rango:
                inicio = int(re.match(r"bytes=(\d+)-", rango).group(1))
                if inicio >= len(datos):
                    return self._enviar(416)
                return self._enviar(206, datos[inicio:])
            return self._enviar(200, datos)
        self._enviar(404)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="genv-")
        self.casa = os.path.join(self.tmp, "home")
        os.makedirs(self.casa)
        _Servidor.peticiones = []
        self.servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Servidor)
        threading.Thread(target=self.servidor.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{self.servidor.server_port}"
        _Servidor.archivos = {f"{NOMBRE}.tar.gz": TGZ, f"{NOMBRE}.sha512sum": f"{SHA}  {NOMBRE}.tar.gz\n".encode()}
        _Servidor.release = {
            "tag_name": NOMBRE,
            "assets": [
                {"name": f"{NOMBRE}.tar.gz", "size": len(TGZ),
                 "browser_download_url": f"{self.base}/files/{NOMBRE}.tar.gz"},
                {"name": f"{NOMBRE}.sha512sum", "size": 0,
                 "browser_download_url": f"{self.base}/files/{NOMBRE}.sha512sum"},
            ],
        }
        self.parches = [
            mock.patch.dict(os.environ, {"HOME": self.casa, "XDG_CACHE_HOME": os.path.join(self.tmp, "cache")}),
            mock.patch.object(G, "API_BASE", f"{self.base}/api"),
            mock.patch.object(G, "REINTENTOS", 2),
            mock.patch.object(G, "ESPERA_BASE", 0.01),
            mock.patch.object(G.os, "geteuid", return_value=1000),
        ]
        for p in self.parches:
            p.start()

    def tearDown(self):
        for p in self.parches:
            p.stop()
        self.servidor.shutdown()
        self.servidor.server_close()
        shutil.rmtree(self.tmp, ignore_errors=True)

    @property
    def compat(self):
        return os.path.join(self.casa, ".steam", "root", "compatibilitytools.d")


class TestGeProton(Base):
    def test_instala_crea_directorio_y_no_repite(self):
        self.assertFalse(os.path.exists(self.compat))
        destino = G.instalar_ge_proton()
        self.assertEqual(destino, os.path.join(self.compat, NOMBRE))
        self.assertTrue(os.path.isfile(os.path.join(destino, NOMBRE, "proton")) or
                        os.path.isfile(os.path.join(destino, "proton")))
        self.assertFalse(os.path.exists(os.path.join(os.path.dirname(destino), ".instalando-" + NOMBRE)))

        n = len(_Servidor.peticiones)
        G.instalar_ge_proton()  # segunda vez: ya instalada, no descarga nada
        nuevas = _Servidor.peticiones[n:]
        self.assertFalse(any(p.startswith("/files/") for p, _ in nuevas))

    def test_checksum_incorrecto_no_instala(self):
        _Servidor.archivos[f"{NOMBRE}.sha512sum"] = ("0" * 128 + f"  {NOMBRE}.tar.gz\n").encode()
        with self.assertRaisesRegex(G.ErrorEntorno, "SHA-512 no coincide"):
            G.instalar_ge_proton()
        self.assertFalse(os.path.exists(os.path.join(self.compat, NOMBRE)))
        self.assertFalse(os.path.exists(os.path.join(G._cache_dir(), NOMBRE + ".tar.gz")))

    def test_sin_checksum_se_niega_sin_descargar(self):
        _Servidor.release["assets"] = [a for a in _Servidor.release["assets"] if a["name"].endswith(".tar.gz")]
        with self.assertRaisesRegex(G.ErrorEntorno, "no se puede verificar"):
            G.instalar_ge_proton()
        self.assertFalse(any(p.startswith("/files/") for p, _ in _Servidor.peticiones))

    def test_reanuda_descarga_parcial(self):
        os.makedirs(G._cache_dir())
        with open(os.path.join(G._cache_dir(), NOMBRE + ".tar.gz.part"), "wb") as f:
            f.write(TGZ[:100])
        G.instalar_ge_proton()
        self.assertIn(("/files/" + NOMBRE + ".tar.gz", "bytes=100-"), _Servidor.peticiones)
        self.assertTrue(os.path.isdir(os.path.join(self.compat, NOMBRE)))

    def test_ruta_insegura_no_se_extrae(self):
        malo = _crear_tgz({"../fuera.txt": b"x", f"{NOMBRE}/proton": b"x"})
        _Servidor.archivos[f"{NOMBRE}.tar.gz"] = malo
        _Servidor.release["assets"][0]["size"] = len(malo)
        _Servidor.archivos[f"{NOMBRE}.sha512sum"] = f"{hashlib.sha512(malo).hexdigest()}  x\n".encode()
        with self.assertRaisesRegex(G.ErrorEntorno, "ruta no segura"):
            G.instalar_ge_proton()
        self.assertFalse(os.path.exists(os.path.join(self.casa, "fuera.txt")))
        self.assertFalse(os.path.exists(os.path.join(self.compat, NOMBRE)))
        self.assertFalse(os.path.exists(os.path.join(self.compat, ".instalando-" + NOMBRE)))

    def test_version_inexistente(self):
        with self.assertRaisesRegex(G.ErrorEntorno, "404"):
            G.instalar_ge_proton("NO-EXISTE")

    def test_como_root_se_niega(self):
        with mock.patch.object(G.os, "geteuid", return_value=0):
            with self.assertRaisesRegex(G.ErrorEntorno, "no ejecutes esto con sudo"):
                G.instalar_ge_proton()


class TestComprobacion(Base):
    def test_vulkan_y_dxvk(self):
        icd = os.path.join(self.tmp, "icd")
        os.makedirs(icd)
        with open(os.path.join(icd, "radeon_icd.x86_64.json"), "w") as f:
            f.write("{}")
        estados = {"mesa-vulkan-drivers": True, "libvulkan1": True, "dxvk": False}
        with mock.patch.object(G, "_paquete_instalado", side_effect=lambda p: estados[p]), \
                mock.patch.object(G, "DIRS_ICD", (icd,)), mock.patch.object(G, "DIR_DXVK", os.path.join(self.tmp, "no")):
            vulkan, dxvk = G.comprobar()
        self.assertTrue(vulkan.ok)
        self.assertIn("radeon_icd.x86_64.json", vulkan.detalle)
        self.assertFalse(dxvk.ok)

    def test_paquetes_sin_driver(self):
        with mock.patch.object(G, "_paquete_instalado", return_value=True), \
                mock.patch.object(G, "DIRS_ICD", (os.path.join(self.tmp, "vacio"),)):
            vulkan, _ = G.comprobar()
        self.assertFalse(vulkan.ok)
        self.assertIn("no hay driver", vulkan.detalle)

    def test_instalar_dependencias_simular_usa_sudo(self):
        with mock.patch.object(G, "_paquete_instalado", return_value=False), \
                mock.patch.object(G.subprocess, "run", side_effect=AssertionError("no debe ejecutar")), \
                mock.patch("builtins.print") as imprime:
            faltan = G.instalar_dependencias(simular=True)
        self.assertEqual(faltan, ["mesa-vulkan-drivers", "libvulkan1", "dxvk"])
        salida = " ".join(str(c.args[0]) for c in imprime.call_args_list)
        self.assertIn("sudo apt-get install -y --no-install-recommends", salida)

    def test_instalar_dependencias_ejecuta_update_y_install(self):
        ordenes = []

        def falso(argv, env=None, **kw):
            ordenes.append((argv, env))
            return mock.Mock(returncode=0)

        with mock.patch.object(G, "_paquete_instalado", return_value=False), \
                mock.patch.object(G.subprocess, "run", side_effect=falso):
            G.instalar_dependencias()
        self.assertEqual([o[0][:3] for o in ordenes], [["sudo", "apt-get", "update"],
                                                       ["sudo", "apt-get", "install"]])
        self.assertEqual(ordenes[1][1]["DEBIAN_FRONTEND"], "noninteractive")

    def test_nada_que_instalar(self):
        with mock.patch.object(G, "_paquete_instalado", return_value=True), \
                mock.patch.object(G.subprocess, "run", side_effect=AssertionError("no debe ejecutar")):
            self.assertEqual(G.instalar_dependencias(), [])


class TestCli(Base):
    def test_codigo_de_salida_con_error(self):
        self.assertEqual(G.main(["ge-proton", "--version", "NO-EXISTE"]), 1)

    def test_comprobar_devuelve_cero(self):
        with mock.patch.object(G, "_paquete_instalado", return_value=True):
            self.assertEqual(G.main(["comprobar"]), 0)


if __name__ == "__main__":
    unittest.main()
