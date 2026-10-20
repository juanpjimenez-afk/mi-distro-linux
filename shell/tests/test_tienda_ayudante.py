"""El ayudante de root de la Tienda (overlay/usr/libexec/infinity/tienda-ayudante), probado SIN ser root.

Todo lo que toca el sistema (raíz de ficheros, apt, red, órdenes) se sustituye; la clave GPG se prueba con el gpg real
si está instalado. Lo importante aquí es la frontera de seguridad: qué acepta del usuario y del catálogo, y que nunca
escribe ni ejecuta nada que no salga del catálogo de root.
"""
import contextlib
import copy
import io
import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import textwrap
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import tienda_catalogo as C  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
RUTA = os.path.join(ROOT, "overlay", "usr", "libexec", "infinity", "tienda-ayudante")
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")


def _cargar():
    """Carga el script como módulo SIN compilarlo a .pyc (no debe aparecer un __pycache__ junto al ayudante)."""
    modulo = types.ModuleType("tienda_ayudante")
    modulo.__file__ = RUTA
    with open(RUTA, encoding="utf-8") as f:
        exec(compile(f.read(), RUTA, "exec"), modulo.__dict__)
    return modulo


H = _cargar()
AyudanteReal = H.Ayudante


def _datos():
    with open(CATALOGO, encoding="utf-8") as f:
        return json.load(f)


def _bytes(ruta):
    with open(ruta, "rb") as f:
        return f.read()


def _texto(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def _salida(funcion, *args, **kw):
    """(lo que devolvió, lo que imprimió)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        r = funcion(*args, **kw)
    return r, buf.getvalue()


GPG_FALSO = textwrap.dedent("""\
    pub:-:255:22:AAAAAAAAAAAAAAAA:1700000000:::-:::scESC:::::ed25519:::0:
    fpr:::::::::0123456789ABCDEF0123456789ABCDEF01234567:
    uid:-::::1700000000::AAAA::Fabricante <f@ejemplo.org>::::::::::0:
    sub:-:255:18:BBBBBBBBBBBBBBBB:1700000000::::::e:::::cv25519::
    fpr:::::::::FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF:
    """).encode()
HUELLA_FALSA = "0123456789ABCDEF0123456789ABCDEF01234567"


class Sistema:
    """Un sistema falso: dpkg-query, gpg y apt-get que anotan lo que se les pide."""

    def __init__(self, instalado=False, desktops=("antigravity.desktop",), rc_apt=None, gpg_rc=0, gpg_salida=GPG_FALSO):
        self.instalado = instalado
        self.desktops = list(desktops)
        self.rc_apt = rc_apt or {}
        self.gpg_rc = gpg_rc
        self.gpg_salida = gpg_salida
        self.apt = []
        self.ordenes = []
        self.descargas = []
        self.lineas_apt = {}

    def correr(self, argv, entrada=None, tiempo=60):
        self.ordenes.append(list(argv))
        if argv[0] == H.DPKG_QUERY:
            if "-L" in argv:
                return 0, "".join(f"/usr/share/applications/{d}\n" for d in self.desktops).encode(), b""
            return (0, b"ii ", b"") if self.instalado else (1, b"", b"no packages found")
        if argv[0] == H.GPG:
            if "--dearmor" in argv:
                return self.gpg_rc, b"\x99BINARIO", b""
            return self.gpg_rc, self.gpg_salida, b""
        return 127, b"", b"orden inesperada"

    def correr_apt(self, argumentos, al_estado, al_linea):
        self.apt.append(list(argumentos))
        al_estado("dlstatus:1:0.0000:Retrieving file 1 of 1")
        al_estado("dlstatus:1:100.0000:Retrieving file 1 of 1")
        al_linea("Get:1 https://repo.ejemplo.org stable InRelease")
        for l in self.lineas_apt.get(argumentos[0], ()):
            al_linea(l)
        if argumentos[0] == "install" and "--only-upgrade" not in argumentos:
            al_estado("pmstatus:libc6:i386:50.0000:Preparing libc6:i386")
            if self.rc_apt.get("install", 0) == 0:
                self.instalado = True
        if argumentos[0] == "remove" and self.rc_apt.get("remove", 0) == 0:
            self.instalado = False
        return self.rc_apt.get(argumentos[0], 0)

    def descargar(self, url, *a, **k):
        self.descargas.append(url)
        return b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nxxxx\n-----END PGP PUBLIC KEY BLOCK-----\n"


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.raiz = tmp.name
        self.datos = _datos()
        self.sis = Sistema()

    def ayudante(self):
        return H.Ayudante(self.datos, raiz=self.raiz, correr_=self.sis.correr, correr_apt_=self.sis.correr_apt,
                          descargar_=self.sis.descargar)

    def ruta(self, p):
        return os.path.join(self.raiz, p.lstrip("/"))

    def leer(self, p):
        with open(self.ruta(p), encoding="utf-8") as f:
            return f.read()

    def escribir(self, p, texto):
        os.makedirs(os.path.dirname(self.ruta(p)), exist_ok=True)
        with open(self.ruta(p), "w", encoding="utf-8") as f:
            f.write(texto)


# =============================================================================================== la frontera: argumentos
class TestUso(unittest.TestCase):
    def test_es_un_script_ejecutable_con_python_aislado(self):
        with open(RUTA, encoding="utf-8") as f:
            self.assertEqual(f.readline().strip(), "#!/usr/bin/python3 -I")
        self.assertTrue(os.stat(RUTA).st_mode & stat.S_IXUSR)

    def test_argumentos_malos_se_rechazan_antes_de_hacer_nada(self):
        malos = [[], ["instalar"], ["instalar", "a", "b"], ["borrar", "x"], ["instalar", "../x"], ["instalar", "X"],
                 ["instalar", "a;b"], ["instalar", "a b"], ["instalar", "x" * 41], ["instalar", ""],
                 ["instalar", "-y"], ["instalar", "--help"], ["INSTALAR", "x"], ["instalar", "a\nb"]]
        for argv in malos:
            with self.subTest(argv=argv), mock.patch.object(H, "leer_catalogo") as leer:
                codigo, salida = _salida(H.main, argv)
                self.assertEqual(codigo, 2)
                self.assertIn("@@ERROR", salida)
                leer.assert_not_called()

    def test_sin_ser_root_no_hace_nada(self):
        with mock.patch.object(os, "geteuid", return_value=1000), mock.patch.object(H, "leer_catalogo") as leer:
            codigo, salida = _salida(H.main, ["instalar", "antigravity"])
        self.assertEqual(codigo, 2)
        self.assertIn("root", salida)
        leer.assert_not_called()

    def test_main_completo_con_un_sistema_falso(self):
        sis = Sistema()
        datos = _datos()

        def ayudante_falso(d):
            return AyudanteReal(d, raiz=tempfile.mkdtemp(), correr_=sis.correr, correr_apt_=sis.correr_apt,
                                descargar_=sis.descargar)

        with mock.patch.object(os, "geteuid", return_value=0), mock.patch.object(H, "leer_catalogo", return_value=datos), \
                mock.patch.object(H, "Ayudante", side_effect=ayudante_falso), \
                mock.patch.object(H, "cerrojo", contextlib.nullcontext), \
                mock.patch.object(H.signal, "signal"), mock.patch.object(os, "umask"):
            codigo, salida = _salida(H.main, ["instalar", "antigravity"])
        self.assertEqual(codigo, 0, salida)
        lineas = salida.splitlines()
        self.assertEqual(lineas[-1], "@@LISTO")
        self.assertIn("@@ARCHIVO antigravity.desktop", lineas)
        self.assertEqual([a[0] for a in sis.apt], ["update", "install"])

    def test_un_fallo_se_cuenta_como_error_y_con_su_codigo(self):
        with mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(H, "leer_catalogo", side_effect=H.Fallo("catálogo roto", 4)), \
                mock.patch.object(H, "cerrojo", contextlib.nullcontext), \
                mock.patch.object(H.signal, "signal"), mock.patch.object(os, "umask"):
            codigo, salida = _salida(H.main, ["instalar", "antigravity"])
        self.assertEqual(codigo, 4)
        self.assertEqual(salida.splitlines()[-1], "@@ERROR catálogo roto")

    def test_un_error_inesperado_no_suelta_un_traceback(self):
        with mock.patch.object(os, "geteuid", return_value=0), \
                mock.patch.object(H, "leer_catalogo", side_effect=KeyError("x")), \
                mock.patch.object(H, "cerrojo", contextlib.nullcontext), \
                mock.patch.object(H.signal, "signal"), mock.patch.object(os, "umask"):
            codigo, salida = _salida(H.main, ["instalar", "antigravity"])
        self.assertEqual(codigo, 1)
        self.assertTrue(salida.startswith("@@ERROR Error interno"))
        self.assertNotIn("Traceback", salida)


# =============================================================================================== el catálogo de confianza
class TestConfianza(unittest.TestCase):
    def _ruta(self, texto):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ruta = os.path.join(d.name, "catalogo.json")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        return ruta

    def test_lee_un_catalogo_bueno(self):
        self.assertEqual(H.leer_catalogo(CATALOGO, exigir_root=False)["version"], 1)

    def test_catalogos_inservibles(self):
        for texto in ("{no", "[]", '{"version": 2, "apps": []}', '{"version": 1}', '{"version": 1, "apps": {}}',
                      "[" * 100000):
            with self.subTest(texto=texto[:20]):
                with self.assertRaises(H.Fallo) as cm:
                    H.leer_catalogo(self._ruta(texto), exigir_root=False)
                self.assertEqual(cm.exception.codigo, 4)

    def test_catalogo_enorme_o_inexistente(self):
        with self.assertRaises(H.Fallo):
            H.leer_catalogo(self._ruta(" " * (H.LIMITE_CATALOGO + 5)), exigir_root=False)
        with self.assertRaises(H.Fallo):
            H.leer_catalogo("/no/existe/catalogo.json", exigir_root=False)

    def test_un_enlace_simbolico_no_se_sigue(self):
        real = self._ruta('{"version": 1, "apps": []}')
        enlace = real + ".enlace"
        os.symlink(real, enlace)
        with self.assertRaises(H.Fallo):
            H.leer_catalogo(enlace, exigir_root=False)

    def test_un_directorio_no_es_un_catalogo(self):
        with self.assertRaises(H.Fallo):
            H.leer_catalogo(tempfile.gettempdir(), exigir_root=False)

    def _con_lstat(self, propietarios):
        """Simula que cada componente de la ruta es de ese (uid, modo)."""
        def lstat(ruta):
            uid, modo = propietarios.get(ruta, (0, stat.S_IFDIR | 0o755))
            return os.stat_result((modo, 0, 0, 1, uid, 0, 0, 0, 0, 0))
        return mock.patch.object(os, "lstat", side_effect=lstat), mock.patch.object(os.path, "realpath", side_effect=lambda r: r)

    def test_cadena_de_confianza_de_root_se_acepta(self):
        a, b = self._con_lstat({})
        with a, b:
            self.assertEqual(H.comprobar_cadena_de_confianza("/usr/share/infinity/tienda/catalogo.json"),
                             "/usr/share/infinity/tienda/catalogo.json")

    def test_cadena_con_un_eslabon_ajeno_o_escribible_se_rechaza(self):
        base = "/usr/share/infinity/tienda/catalogo.json"
        for ruta, uid_modo in (("/usr/share/infinity/tienda/catalogo.json", (1000, stat.S_IFREG | 0o644)),
                               ("/usr/share/infinity/tienda", (0, stat.S_IFDIR | 0o775)),
                               ("/usr/share/infinity", (0, stat.S_IFDIR | 0o757)),
                               ("/usr", (1000, stat.S_IFDIR | 0o755)),
                               ("/usr/share/infinity/tienda/catalogo.json", (0, stat.S_IFREG | 0o666)),
                               ("/usr/share/infinity/tienda/catalogo.json", (0, stat.S_IFLNK | 0o777))):
            a, b = self._con_lstat({ruta: uid_modo})
            with self.subTest(eslabon=ruta, uid_modo=uid_modo), a, b:
                with self.assertRaises(H.Fallo) as cm:
                    H.comprobar_cadena_de_confianza(base)
                self.assertEqual(cm.exception.codigo, 4)

    def test_en_tmp_no_se_fia_aunque_el_fichero_sea_de_root(self):
        ruta = self._ruta('{"version": 1, "apps": []}')     # /tmp es escribible por todos
        with self.assertRaises(H.Fallo):
            H.leer_catalogo(ruta, exigir_root=True)


# =============================================================================================== validar la entrada apt
class TestEntradaApt(unittest.TestCase):
    def test_las_entradas_apt_del_catalogo_son_validas_y_coinciden_con_el_validador_de_la_ventana(self):
        datos = _datos()
        cat = C.cargar(CATALOGO)
        n = 0
        for a in cat.apps:
            if a.tipo != "apt":
                continue
            n += 1
            e = H.entrada_apt(datos, a.id)
            for clave in ("paquete", "uri", "suite", "archivo_clave", "archivo_fuente", "clave_url"):
                self.assertEqual(e[clave], a.origen[clave], f"{a.id}.{clave}")
            for clave in ("componentes", "arquitecturas", "huella"):
                self.assertEqual(tuple(e[clave]), tuple(a.origen[clave]), f"{a.id}.{clave}")
        self.assertEqual(n, 3)

    def test_ids_que_no_son_apt_o_no_existen(self):
        datos = _datos()
        for ident, codigo in (("spotify", 2), ("netflix", 2), ("no-existe", 2), ("../x", 2), ("", 2), (None, 2),
                              ("X", 2), (5, 2)):
            with self.subTest(id=ident), self.assertRaises(H.Fallo) as cm:
                H.entrada_apt(datos, ident)
            self.assertEqual(cm.exception.codigo, codigo)

    def test_un_id_repetido_en_el_catalogo_no_vale(self):
        datos = _datos()
        datos["apps"].append(copy.deepcopy(datos["apps"][1]))
        with self.assertRaises(H.Fallo):
            H.entrada_apt(datos, "antigravity")

    # el mismo corpus de trampas que el validador de la ventana: los dos deben rechazarlo todo
    CAMPOS_MALOS = {
        "paquete": ("x; rm -rf /", "a b", "../x", "$(id)", "", "A", "x\ny", 5, None, "-x", "x" * 101),
        "suite": ("a b", "a;b", "../a", "", "x\ny", None, "-a"),
        "archivo_fuente": ("../x.list", "x", "a/b.list", "x\n.list", ".x.list", "x.txt", "/etc/passwd", None,
                           "a..b/c.list"),
        "archivo_clave": ("/etc/passwd", "/etc/apt/keyrings/../x.gpg", "/tmp/x.gpg", "/etc/apt/keyrings/x",
                          "/etc/apt/keyrings/a/b.gpg", "etc/apt/keyrings/x.gpg", "/usr/share/keyrings/X.gpg",
                          "/etc/apt/trusted.gpg.d/x.gpg", None, "/etc/apt/keyrings/x.gpg\n"),
        "uri": ("http://repo.ejemplo.org/", "https://u:p@repo.ejemplo.org/", "https://repo.ejemplo.org/ a",
                "https://repo.ejemplo.org/#x", "https://localhost/", "https://x.org:abc/", "", None,
                "file:///etc/", "https://repo.ejemplo.org/`id`", "https://repo.ejemplo.org/'x"),
        "clave_url": ("http://repo.ejemplo.org/k", "https://repo.ejemplo.org/k k", "ftp://x.org/k", None,
                      "https://-x.org/k"),
        "componentes": ([], ["ma in"], ["main", "main"], "main", [5], None, ["x"] * 9, ["Main"], ["main;x"]),
        "arquitecturas": (["mips"], "amd64", [5], ["amd64", "amd64"], ["amd64 arm64"]),
        "huella": (["abc"], ["A" * 39], ["a" * 40], "A" * 40, ["A" * 40, "A" * 40], [5]),
    }

    def test_cada_campo_malo_se_rechaza_en_los_dos_validadores(self):
        base = _datos()
        for campo, valores in self.CAMPOS_MALOS.items():
            for v in valores:
                with self.subTest(campo=campo, valor=v):
                    d = copy.deepcopy(base)
                    entrada = next(a for a in d["apps"] if a["id"] == "antigravity")
                    entrada["origen"][campo] = v
                    with self.assertRaises(H.Fallo):
                        H.entrada_apt(d, "antigravity")
                    with self.assertRaises(C.CatalogoError):
                        C.validar_entrada(entrada, {c["id"] for c in d["categorias"]})

    def test_nombre_malo(self):
        for v in ("", "a\nb", "x" * 61, None, 5):
            d = _datos()
            next(a for a in d["apps"] if a["id"] == "antigravity")["nombre"] = v
            with self.subTest(nombre=v), self.assertRaises(H.Fallo):
                H.entrada_apt(d, "antigravity")

    def test_falta_el_origen(self):
        d = _datos()
        del next(a for a in d["apps"] if a["id"] == "antigravity")["origen"]
        with self.assertRaises(H.Fallo):
            H.entrada_apt(d, "antigravity")


# =============================================================================================== los ficheros de apt
class TestFuentes(unittest.TestCase):
    def setUp(self):
        self.datos = _datos()

    def test_antigravity_es_la_linea_que_documenta_google(self):
        o = H.entrada_apt(self.datos, "antigravity")
        self.assertEqual(H.texto_fuente(o), H.MARCA + "\n"
                         "deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg] "
                         "https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/ "
                         "antigravity-debian main\n")

    def test_chrome_es_deb822_con_su_clave(self):
        o = H.entrada_apt(self.datos, "chrome")
        self.assertEqual(H.texto_fuente(o), "\n".join([
            H.MARCA, "Types: deb", "URIs: https://dl.google.com/linux/chrome/deb/", "Suites: stable",
            "Components: main", "Architectures: amd64", "Signed-By: /usr/share/keyrings/google-chrome.gpg"]) + "\n")

    def test_list_con_arquitecturas(self):
        o = H.entrada_apt(self.datos, "antigravity")
        o["arquitecturas"] = ["amd64", "arm64"]
        self.assertIn("deb [arch=amd64,arm64 signed-by=", H.texto_fuente(o))

    def test_brave(self):
        o = H.entrada_apt(self.datos, "brave")
        t = H.texto_fuente(o)
        self.assertIn("URIs: https://brave-browser-apt-release.s3.brave.com/", t)
        self.assertIn("Signed-By: /usr/share/keyrings/brave-browser-archive-keyring.gpg", t)

    def test_la_marca_es_un_comentario(self):
        self.assertTrue(H.MARCA.startswith("#"))
        self.assertNotIn("\n", H.MARCA)


class TestEscribirAtomico(unittest.TestCase):
    def test_escribe_con_permisos_y_sin_temporales(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "sub", "x.gpg")
            H.escribir_atomico(ruta, b"hola", 0o644)
            self.assertEqual(_bytes(ruta), b"hola")
            self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o644)
            H.escribir_atomico(ruta, b"adios", 0o644)
            self.assertEqual(_bytes(ruta), b"adios")
            self.assertEqual(os.listdir(os.path.dirname(ruta)), ["x.gpg"])

    def test_un_fallo_no_deja_el_fichero_a_medias_ni_temporales(self):
        with tempfile.TemporaryDirectory() as d:
            ruta = os.path.join(d, "x.gpg")
            H.escribir_atomico(ruta, b"original")
            with mock.patch.object(os, "fsync", side_effect=OSError("disco lleno")):
                with self.assertRaises(OSError):
                    H.escribir_atomico(ruta, b"nuevo")
            self.assertEqual(_bytes(ruta), b"original")
            self.assertEqual(os.listdir(d), ["x.gpg"])

    def test_no_sigue_un_enlace_puesto_en_el_destino(self):
        with tempfile.TemporaryDirectory() as d:
            objetivo = os.path.join(d, "objetivo")
            with open(objetivo, "w") as f:
                f.write("intacto")
            enlace = os.path.join(d, "x.gpg")
            os.symlink(objetivo, enlace)
            H.escribir_atomico(enlace, b"nuevo")
            self.assertEqual(_texto(objetivo), "intacto")     # replace sustituye el enlace, no escribe a través
            self.assertFalse(os.path.islink(enlace))


# =============================================================================================== clave GPG
class TestParsearYExplicar(unittest.TestCase):
    def test_estado_de_apt(self):
        self.assertEqual(H.parsear_estado("dlstatus:3:93.3333:Retrieving file 3 of 3"),
                         ("dlstatus", 93.3333, "Retrieving file 3 of 3"))
        self.assertEqual(H.parsear_estado("pmstatus:dpkg-exec:0.0000:Running dpkg"), ("pmstatus", 0.0, "Running dpkg"))
        self.assertEqual(H.parsear_estado("pmstatus:libc6:i386:50.0000:Preparing libc6:i386"),
                         ("pmstatus", 50.0, "Preparing libc6:i386"))
        self.assertEqual(H.parsear_estado("pmstatus:x:45.5:Unpacking x (1:2.3-1)"), ("pmstatus", 45.5, "Unpacking x (1:2.3-1)"))
        self.assertEqual(H.parsear_estado("pmerror:x:10:falló")[0], "pmerror")

    def test_porcentaje_acotado_y_basura_ignorada(self):
        self.assertEqual(H.parsear_estado("dlstatus:1:250:x")[1], 100.0)
        for malo in ("", "hola", "dlstatus", "dlstatus:1", "dlstatus:1:abc:x", "otra:1:5:x", "pmconffile:'a' 'b'",
                     "@@LISTO", ":::"):
            self.assertIsNone(H.parsear_estado(malo), malo)

    def test_explicar_errores_de_apt(self):
        casos = {
            "E: Could not get lock /var/lib/dpkg/lock-frontend": "usando apt",
            "E: Failed to fetch https://repo.org/dists/x/InRelease  404  Not Found [IP: 1.2.3.4 443]": "404",
            "E: The repository 'https://x stable' does not have a Release file.": "404",
            "Err:1 https://x  Temporary failure resolving 'x'": "conexión",
            "W: Failed to fetch https://x": "conexión",
            "dpkg: error: No space left on device": "espacio",
            "E: dpkg was interrupted, you must manually run 'dpkg --configure -a'": "dpkg --configure -a",
            "E: Unable to locate package foo": "encuentra el paquete",
            "The following packages have unmet dependencies": "dependencias",
            "W: GPG error: NO_PUBKEY ABCDEF": "firma",
            "algo raro": "error",
        }
        for linea, esperado in casos.items():
            self.assertIn(esperado, H.explicar_error_apt([linea]).lower(), linea)


@unittest.skipUnless(shutil.which("gpg"), "sin gpg")
class TestClaveConGpgReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.casa = tempfile.mkdtemp(prefix="gpg-prueba-")
        os.chmod(cls.casa, 0o700)
        base = ["gpg", "--batch", "--no-tty", "--homedir", cls.casa]
        cls.huellas = []
        for i in range(2):
            r = subprocess.run(base + ["--passphrase", "", "--pinentry-mode", "loopback", "--quick-gen-key",
                                       f"Fabricante {i} <f{i}@ejemplo.org>", "ed25519", "sign", "never"],
                               capture_output=True, timeout=120)
            if r.returncode != 0:
                raise unittest.SkipTest("gpg no puede generar claves aquí: " + r.stderr.decode()[:200])
        listado = subprocess.run(base + ["--list-keys", "--with-colons", "--fingerprint"], capture_output=True,
                                 timeout=60).stdout.decode().splitlines()
        tipo = ""
        for l in listado:
            c = l.split(":")
            if c[0] in ("pub", "sub"):
                tipo = c[0]
            elif c[0] == "fpr" and tipo == "pub":
                cls.huellas.append(c[9])
        cls.armadura_dos = subprocess.run(base + ["--armor", "--export"], capture_output=True, timeout=60).stdout
        cls.armadura_una = subprocess.run(base + ["--armor", "--export", cls.huellas[0]], capture_output=True,
                                          timeout=60).stdout
        cls.binaria_una = subprocess.run(base + ["--export", cls.huellas[0]], capture_output=True, timeout=60).stdout
        cls.secreta = subprocess.run(base + ["--armor", "--export-secret-keys", "--pinentry-mode", "loopback",
                                             "--passphrase", "", cls.huellas[0]], capture_output=True,
                                     timeout=60).stdout

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.casa, ignore_errors=True)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = os.path.join(tmp.name, "run")

    def test_armadura_se_convierte_a_binario_y_da_su_huella(self):
        llave, huellas = H.procesar_clave(self.armadura_una, [], dir_run=self.run_dir)
        self.assertEqual(huellas, [self.huellas[0]])
        self.assertFalse(llave.startswith(b"-----BEGIN"))
        self.assertEqual(llave, self.binaria_una)

    def test_una_clave_ya_binaria_se_acepta_tal_cual(self):
        llave, huellas = H.procesar_clave(self.binaria_una, [], dir_run=self.run_dir)
        self.assertEqual(llave, self.binaria_una)
        self.assertEqual(huellas, [self.huellas[0]])

    def test_la_huella_fijada_en_el_catalogo_se_exige(self):
        H.procesar_clave(self.armadura_una, [self.huellas[0]], dir_run=self.run_dir)
        with self.assertRaises(H.Fallo) as cm:
            H.procesar_clave(self.armadura_una, [self.huellas[1]], dir_run=self.run_dir)
        self.assertIn("huella", cm.exception.mensaje)

    def test_con_dos_claves_hay_que_fijar_las_dos(self):
        _llave, huellas = H.procesar_clave(self.armadura_dos, [], dir_run=self.run_dir)
        self.assertEqual(sorted(huellas), sorted(self.huellas))
        with self.assertRaises(H.Fallo):
            H.procesar_clave(self.armadura_dos, [self.huellas[0]], dir_run=self.run_dir)     # una clave sin fijar colada
        H.procesar_clave(self.armadura_dos, list(self.huellas), dir_run=self.run_dir)

    def test_basura_texto_vacio_y_claves_secretas_se_rechazan(self):
        for malo in (b"esto no es una clave", b"", b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nbasura\n-----END PGP PUBLIC KEY BLOCK-----\n",
                     b"\x99\x00\x01", self.secreta):
            with self.subTest(malo=malo[:30]), self.assertRaises(H.Fallo):
                H.procesar_clave(malo, [], dir_run=self.run_dir)

    def test_no_deja_directorios_de_trabajo(self):
        H.procesar_clave(self.armadura_una, [], dir_run=self.run_dir)
        self.assertEqual(os.listdir(self.run_dir), [])
        self.assertEqual(stat.S_IMODE(os.stat(self.run_dir).st_mode), 0o700)


class TestClaveConGpgFalso(unittest.TestCase):
    def test_pone_las_banderas_de_seguridad_y_no_toca_el_gnupg_del_usuario(self):
        sis = Sistema()
        with tempfile.TemporaryDirectory() as d:
            H.procesar_clave(b"-----BEGIN PGP PUBLIC KEY BLOCK-----\nx\n", [], sis.correr, dir_run=os.path.join(d, "r"))
        for orden in sis.ordenes:
            self.assertEqual(orden[0], "/usr/bin/gpg")
            self.assertIn("--batch", orden)
            self.assertIn("--no-tty", orden)
            self.assertIn("--homedir", orden)
            self.assertTrue(orden[orden.index("--homedir") + 1].startswith(d))

    def test_secretas_y_subclaves(self):
        sis = Sistema(gpg_salida=b"sec:u:255:22:AAAA:1700000000:::u:::scESC:::::ed25519:::0:\nfpr:::::::::" + b"A" * 40 + b":\n")
        with tempfile.TemporaryDirectory() as d, self.assertRaises(H.Fallo):
            H.procesar_clave(b"\x99xx", [], sis.correr, dir_run=os.path.join(d, "r"))
        # la huella de una SUBclave no cuenta como principal
        sis = Sistema(gpg_salida=GPG_FALSO)
        with tempfile.TemporaryDirectory() as d:
            _llave, huellas = H.procesar_clave(b"\x99xx", [], sis.correr, dir_run=os.path.join(d, "r"))
        self.assertEqual(huellas, [HUELLA_FALSA])


class TestDescargar(unittest.TestCase):
    class Respuesta:
        def __init__(self, datos):
            self.datos = datos

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self, n):
            return self.datos[:n]

    def _con(self, resultado):
        class Abridor:
            def open(_self, peticion, timeout=None):
                if isinstance(resultado, Exception):
                    raise resultado
                self.peticion, self.tiempo = peticion, timeout
                return self.Respuesta(resultado)
        return mock.patch.object(H.urllib.request, "build_opener", return_value=Abridor())

    def test_descarga_normal(self):
        with self._con(b"clave"):
            self.assertEqual(H.descargar("https://x.org/k"), b"clave")
        self.assertEqual(self.peticion.full_url, "https://x.org/k")
        self.assertEqual(self.tiempo, 30)

    def test_demasiado_grande_o_vacia(self):
        with self._con(b"x" * (H.LIMITE_CLAVE + 1)), self.assertRaises(H.Fallo) as cm:
            H.descargar("https://x.org/k")
        self.assertIn("grande", cm.exception.mensaje)
        with self._con(b""), self.assertRaises(H.Fallo):
            H.descargar("https://x.org/k")

    def test_errores_de_red_son_fallos_claros(self):
        for error in (H.urllib.error.URLError("Name or service not known"), OSError("sin red"), ValueError("url")):
            with self.subTest(error=error), self._con(error), self.assertRaises(H.Fallo) as cm:
                H.descargar("https://x.org/k")
            self.assertIn("internet", cm.exception.mensaje)

    def test_el_abridor_solo_acepta_redirecciones_https_y_verifica_certificados(self):
        manejador = H._SoloHttps()
        with self.assertRaises(H.urllib.error.URLError):
            manejador.redirect_request(mock.Mock(), None, 302, "x", {}, "http://x.org/k")
        with self.assertRaises(H.urllib.error.URLError):
            manejador.redirect_request(mock.Mock(), None, 302, "x", {}, "file:///etc/passwd")
        with mock.patch.object(H.ssl, "create_default_context", wraps=H.ssl.create_default_context) as ctx:
            with mock.patch.object(H.urllib.request, "build_opener") as constructor:
                constructor.return_value.open.side_effect = H.urllib.error.URLError("x")
                with self.assertRaises(H.Fallo):
                    H.descargar("https://x.org/k")
            ctx.assert_called_once_with()      # contexto por defecto: valida cadena y nombre


# =============================================================================================== apt de verdad (un apt falso)
APT_FALSO = textwrap.dedent("""\
    #!/bin/bash
    fd=""
    for a in "$@"; do case "$a" in APT::Status-Fd=*) fd="${a#APT::Status-Fd=}";; esac; done
    [ -n "$fd" ] || exit 7
    %s
    exit %d
    """)


class TestCorrerApt(unittest.TestCase):
    def _apt_falso(self, cuerpo, rc=0):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ruta = os.path.join(d.name, "apt-get")
        with open(ruta, "w") as f:
            f.write(APT_FALSO % (cuerpo, rc))
        os.chmod(ruta, 0o755)
        return mock.patch.object(H, "APT_GET", ruta)

    def _correr(self, argumentos=("install", "x")):
        estados, lineas = [], []
        codigo = H.correr_apt(list(argumentos), estados.append, lineas.append)
        return codigo, estados, lineas

    def test_reenvia_el_registro_y_el_estado_y_devuelve_el_codigo(self):
        cuerpo = textwrap.dedent("""\
            echo "args: $*"
            printf 'dlstatus:1:10.0000:Retrieving file 1 of 2\\n' >&$fd
            echo "Get:1 http://x"
            printf 'pmstatus:libc6:i386:50.0000:Preparing\\n' >&$fd
            echo "salida por stderr" >&2
            printf 'sin salto final' >&$fd
            printf 'sin salto en stdout'""")
        with self._apt_falso(cuerpo, rc=0):
            codigo, estados, lineas = self._correr(("install", "pkg"))
        self.assertEqual(codigo, 0)
        self.assertEqual(estados, ["dlstatus:1:10.0000:Retrieving file 1 of 2", "pmstatus:libc6:i386:50.0000:Preparing",
                                   "sin salto final"])
        self.assertIn("Get:1 http://x", lineas)
        self.assertIn("salida por stderr", lineas)
        self.assertEqual(lineas[-1], "sin salto en stdout")
        arg_linea = next(l for l in lineas if l.startswith("args:"))
        for esperado in ("-y", "Dpkg::Options::=--force-confold", "DPkg::Lock::Timeout=300", "install pkg"):
            self.assertIn(esperado, arg_linea)

    def test_codigo_de_error_se_propaga(self):
        with self._apt_falso("echo E: fallo", rc=100):
            codigo, _estados, lineas = self._correr()
        self.assertEqual(codigo, 100)
        self.assertEqual(lineas, ["E: fallo"])

    def test_mucha_salida_no_se_atasca(self):
        with self._apt_falso('for i in $(seq 1 30000); do echo "linea $i de la salida"; echo "dlstatus:1:1.0:x" >&$fd; done'):
            codigo, estados, lineas = self._correr()
        self.assertEqual((codigo, len(lineas), len(estados)), (0, 30000, 30000))

    def test_retornos_de_carro_cuentan_como_fin_de_linea(self):
        with self._apt_falso(r"printf 'a\rb\rc\n'"):
            _codigo, _e, lineas = self._correr()
        self.assertEqual(lineas, ["a", "b", "c"])

    def test_una_linea_gigante_sin_salto_se_trocea(self):
        with self._apt_falso("head -c 200000 /dev/zero | tr '\\0' 'x'; echo"):
            _codigo, _e, lineas = self._correr()
        self.assertTrue(all(len(l) <= 65536 + 65536 for l in lineas))
        self.assertEqual(sum(len(l) for l in lineas), 200000)

    def test_el_entorno_de_apt_es_el_minimo(self):
        with self._apt_falso('echo "PATH=$PATH"; echo "LANG=$LANG"; echo "FRONT=$DEBIAN_FRONTEND"; echo "EVIL=$EVIL_USUARIO"'):
            with mock.patch.dict(os.environ, {"EVIL_USUARIO": "pwned", "PATH": "/tmp/malo"}):
                _codigo, _e, lineas = self._correr()
        self.assertIn("PATH=/usr/sbin:/usr/bin:/sbin:/bin", lineas)
        self.assertIn("FRONT=noninteractive", lineas)
        self.assertIn("EVIL=", lineas)

    def test_un_apt_que_no_existe_es_un_error_de_os(self):
        with mock.patch.object(H, "APT_GET", "/no/existe/apt-get"):
            with self.assertRaises(OSError):
                self._correr()

    def test_no_se_cuelan_lineas_de_control_desde_la_salida_de_apt(self):
        r, salida = _salida(H.emitir_log, "@@LISTO")
        self.assertEqual(salida, "  @@LISTO\n")
        self.assertFalse(salida.startswith("@@"))


# =============================================================================================== el trabajo, de punta a punta
class TestInstalar(Base):
    def test_instala_desde_cero(self):
        _r, salida = _salida(self.ayudante().instalar, "antigravity")
        s = self.sis
        self.assertEqual(s.descargas, ["https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg"])
        self.assertEqual([a[0] for a in s.apt], ["update", "install"])
        self.assertEqual(s.apt[1], ["install", "antigravity"])
        # clave binaria y fuente exactamente como las genera el ayudante, con permisos de apt
        clave = self.ruta("/etc/apt/keyrings/antigravity-repo-key.gpg")
        fuente = self.ruta("/etc/apt/sources.list.d/antigravity.list")
        self.assertEqual(_bytes(clave), b"\x99BINARIO")
        self.assertEqual(stat.S_IMODE(os.stat(clave).st_mode), 0o644)
        self.assertEqual(stat.S_IMODE(os.stat(fuente).st_mode), 0o644)
        self.assertIn("deb [signed-by=/etc/apt/keyrings/antigravity-repo-key.gpg]", self.leer("/etc/apt/sources.list.d/antigravity.list"))
        self.assertIn("@@ARCHIVO antigravity.desktop", salida.splitlines())
        # el avance sube y nunca pasa de 100
        pcts = [int(l.split()[1]) for l in salida.splitlines() if l.startswith("@@PROGRESO")]
        self.assertEqual(pcts, sorted(pcts))
        self.assertEqual(pcts[-1], 100)
        self.assertTrue(all(0 <= p <= 100 for p in pcts))

    def test_el_registro_de_apt_llega_con_sangria(self):
        _r, salida = _salida(self.ayudante().instalar, "antigravity")
        self.assertIn("  Get:1 https://repo.ejemplo.org stable InRelease", salida.splitlines())

    def test_sin_conexion_para_la_clave_no_se_toca_apt_ni_se_escribe_nada(self):
        def sin_red(url):
            raise H.Fallo("No se pudo descargar la clave (sin internet)")
        a = H.Ayudante(self.datos, raiz=self.raiz, correr_=self.sis.correr, correr_apt_=self.sis.correr_apt,
                       descargar_=sin_red)
        with self.assertRaises(H.Fallo):
            _salida(a.instalar, "antigravity")
        self.assertEqual(self.sis.apt, [])
        self.assertFalse(os.path.exists(self.ruta("/etc/apt")))

    def test_una_clave_invalida_no_registra_el_repositorio(self):
        self.sis.gpg_rc = 2
        with self.assertRaises(H.Fallo):
            _salida(self.ayudante().instalar, "antigravity")
        self.assertEqual(self.sis.apt, [])
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))

    def test_la_huella_del_catalogo_se_hace_cumplir(self):
        e = next(a for a in self.datos["apps"] if a["id"] == "antigravity")
        e["origen"]["huella"] = ["B" * 40]
        with self.assertRaises(H.Fallo) as cm:
            _salida(self.ayudante().instalar, "antigravity")
        self.assertIn("huella", cm.exception.mensaje)
        self.assertEqual(self.sis.apt, [])
        e["origen"]["huella"] = [HUELLA_FALSA]
        _salida(self.ayudante().instalar, "antigravity")
        self.assertEqual([a[0] for a in self.sis.apt], ["update", "install"])

    def test_si_otro_fichero_ya_configura_el_repositorio_no_se_duplica(self):
        self.escribir("/etc/apt/sources.list.d/google-chrome.list",
                      "deb [arch=amd64] https://dl.google.com/linux/chrome/deb/ stable main\n")
        _salida(self.ayudante().instalar, "chrome")
        self.assertEqual(self.sis.descargas, [])
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/google-chrome.sources")))
        self.assertEqual([a[0] for a in self.sis.apt], ["update", "install"])

    def test_una_linea_comentada_no_cuenta_como_configurada(self):
        self.escribir("/etc/apt/sources.list.d/otro.list", "# deb https://dl.google.com/linux/chrome/deb/ stable main\n")
        _salida(self.ayudante().instalar, "chrome")
        self.assertTrue(os.path.exists(self.ruta("/etc/apt/sources.list.d/google-chrome.sources")))

    def test_un_fichero_de_fuente_ajeno_con_el_mismo_nombre_no_se_pisa(self):
        self.escribir("/etc/apt/sources.list.d/antigravity.list", "deb [trusted=yes] https://raro.ejemplo.org/ x main\n")
        _salida(self.ayudante().instalar, "antigravity")
        self.assertEqual(self.sis.descargas, [])
        self.assertEqual(self.leer("/etc/apt/sources.list.d/antigravity.list"),
                         "deb [trusted=yes] https://raro.ejemplo.org/ x main\n")

    def test_un_fichero_propio_de_antes_se_refresca(self):
        _salida(self.ayudante().instalar, "antigravity")
        self.sis.descargas.clear()
        self.sis.instalado = False
        _salida(self.ayudante().instalar, "antigravity")
        self.assertEqual(len(self.sis.descargas), 1)

    def test_fallo_al_actualizar_las_listas_no_instala(self):
        self.sis.rc_apt["update"] = 100
        self.sis.lineas_apt["update"] = ["Err:1 https://x  Temporary failure resolving 'x'"]
        with self.assertRaises(H.Fallo) as cm:
            _salida(self.ayudante().instalar, "antigravity")
        self.assertIn("conexión", cm.exception.mensaje)
        self.assertEqual([a[0] for a in self.sis.apt], ["update"])

    def test_un_repositorio_nuevo_que_rompe_apt_update_se_retira(self):
        self.sis.rc_apt["update"] = 100
        self.sis.lineas_apt["update"] = ["E: The repository 'https://x stable' does not have a Release file."]
        with self.assertRaises(H.Fallo):
            _salida(self.ayudante().instalar, "antigravity")
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/keyrings/antigravity-repo-key.gpg")))

    def test_un_repositorio_que_ya_funcionaba_no_se_retira_por_un_fallo_de_red(self):
        _salida(self.ayudante().instalar, "antigravity")
        self.sis.rc_apt["update"] = 100
        self.sis.lineas_apt["update"] = ["Err:1 https://x  Temporary failure resolving 'x'"]
        with self.assertRaises(H.Fallo):
            _salida(self.ayudante().instalar, "antigravity")
        self.assertTrue(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))

    def test_apt_get_que_no_se_puede_ejecutar_es_un_fallo_claro(self):
        def sin_apt(argumentos, al_estado, al_linea):
            raise FileNotFoundError(2, "No such file or directory")
        a = H.Ayudante(self.datos, raiz=self.raiz, correr_=self.sis.correr, correr_apt_=sin_apt,
                       descargar_=self.sis.descargar)
        with self.assertRaises(H.Fallo) as cm:
            _salida(a.instalar, "antigravity")
        self.assertIn("apt-get", cm.exception.mensaje)

    def test_fallo_al_instalar_explica_el_motivo(self):
        self.sis.rc_apt["install"] = 100
        self.sis.lineas_apt["install"] = ["E: Unable to locate package antigravity"]
        with self.assertRaises(H.Fallo) as cm:
            _salida(self.ayudante().instalar, "antigravity")
        self.assertIn("paquete", cm.exception.mensaje)

    def test_apt_dice_que_fue_bien_pero_el_paquete_no_esta(self):
        original = self.sis.correr_apt

        def apt_que_miente(argumentos, al_estado, al_linea):
            original(argumentos, al_estado, al_linea)
            self.sis.instalado = False
            return 0
        a = H.Ayudante(self.datos, raiz=self.raiz, correr_=self.sis.correr, correr_apt_=apt_que_miente,
                       descargar_=self.sis.descargar)
        with self.assertRaises(H.Fallo) as cm:
            _salida(a.instalar, "antigravity")
        self.assertIn("no quedó instalado", cm.exception.mensaje)

    def test_nada_que_no_sea_de_la_entrada_llega_a_los_comandos(self):
        _salida(self.ayudante().instalar, "antigravity")
        for orden in self.sis.ordenes:
            self.assertIn(orden[0], (H.DPKG_QUERY, H.GPG))
        for apt in self.sis.apt:
            self.assertIn(apt[-1], ("update", "antigravity"))


class TestActualizarYQuitar(Base):
    def test_actualizar_solo_actualiza_ese_paquete(self):
        self.sis.instalado = True
        _salida(self.ayudante().actualizar, "chrome")
        self.assertEqual([a[0] for a in self.sis.apt], ["update", "install"])
        self.assertEqual(self.sis.apt[1], ["install", "--only-upgrade", "google-chrome-stable"])

    def test_quitar_borra_el_paquete_y_lo_que_puso_la_tienda(self):
        self.sis.instalado = False
        _salida(self.ayudante().instalar, "antigravity")
        self.assertTrue(self.sis.instalado)
        self.sis.apt.clear()
        _salida(self.ayudante().quitar, "antigravity")
        self.assertEqual(self.sis.apt, [["remove", "antigravity"]])
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/keyrings/antigravity-repo-key.gpg")))

    def test_quitar_no_borra_la_clave_si_otro_fichero_la_usa(self):
        _salida(self.ayudante().instalar, "chrome")
        self.escribir("/etc/apt/sources.list.d/otro.sources",
                      "Types: deb\nURIs: https://otro.org/\nSuites: s\nComponents: main\n"
                      "Signed-By: /usr/share/keyrings/google-chrome.gpg\n")
        _salida(self.ayudante().quitar, "chrome")
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/google-chrome.sources")))
        self.assertTrue(os.path.exists(self.ruta("/usr/share/keyrings/google-chrome.gpg")))

    def test_quitar_no_toca_un_fichero_de_fuente_que_no_es_de_la_tienda(self):
        self.sis.instalado = True
        self.escribir("/etc/apt/sources.list.d/antigravity.list", "deb https://ajeno.org/ x main\n")
        _salida(self.ayudante().quitar, "antigravity")
        self.assertTrue(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))

    def test_quitar_algo_no_instalado_solo_limpia(self):
        _salida(self.ayudante().instalar, "antigravity")
        self.sis.instalado = False
        self.sis.apt.clear()
        _salida(self.ayudante().quitar, "antigravity")
        self.assertEqual(self.sis.apt, [])
        self.assertFalse(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))

    def test_quitar_que_falla_no_borra_el_repositorio(self):
        _salida(self.ayudante().instalar, "antigravity")
        self.sis.rc_apt["remove"] = 100
        with self.assertRaises(H.Fallo):
            _salida(self.ayudante().quitar, "antigravity")
        self.assertTrue(os.path.exists(self.ruta("/etc/apt/sources.list.d/antigravity.list")))


class TestCerrojo(unittest.TestCase):
    def test_una_sola_instalacion_a_la_vez(self):
        with tempfile.TemporaryDirectory() as d:
            run = os.path.join(d, "run")
            with H.cerrojo(run):
                with self.assertRaises(H.Fallo) as cm:
                    with H.cerrojo(run):
                        pass
                self.assertEqual(cm.exception.codigo, 3)
            with H.cerrojo(run):       # liberado
                pass
            self.assertEqual(stat.S_IMODE(os.stat(run).st_mode), 0o700)


if __name__ == "__main__":
    unittest.main()
