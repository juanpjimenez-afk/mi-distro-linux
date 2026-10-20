"""El catálogo de la Tienda: el que trae la ISO (las 10 apps pedidas más los navegadores) y el validador."""
import copy
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import tienda_catalogo as C  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")
ICONOS = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "iconos")

PEDIDAS = ("spotify", "antigravity", "netflix", "hbo-max", "disney-plus", "claude", "gemini", "notebooklm",
           "classroom", "youtube")


def _flatpak():
    return {"id": "otra", "nombre": "Otra", "resumen": "Una app.", "categoria": "ia", "tipo": "flatpak",
            "origen": {"remoto": "flathub", "url_remoto": "https://dl.flathub.org/repo/flathub.flatpakrepo",
                       "ref": "org.ejemplo.App"}}


def _apt():
    return {"id": "otra", "nombre": "Otra", "resumen": "Una app.", "categoria": "ia", "tipo": "apt",
            "origen": {"paquete": "otra-app", "uri": "https://repo.ejemplo.org/apt", "suite": "stable",
                       "componentes": ["main"], "clave_url": "https://repo.ejemplo.org/clave.gpg",
                       "archivo_clave": "/etc/apt/keyrings/otra.gpg", "archivo_fuente": "otra.sources"}}


def _web():
    return {"id": "otra", "nombre": "Otra", "resumen": "Una app.", "categoria": "ia", "tipo": "webapp",
            "origen": {"url": "https://otra.ejemplo.org/"}}


CATEGORIAS = {"ia", "estudio"}


class TestCatalogoDeLaIso(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cat = C.cargar(CATALOGO)

    def test_se_carga_sin_descartar_nada(self):
        self.assertEqual(self.cat.problemas, [])

    def test_estan_las_diez_apps_pedidas_y_los_navegadores(self):
        for ident in PEDIDAS + ("chrome", "brave"):
            self.assertIn(ident, self.cat.por_id, ident)
        self.assertEqual(len(self.cat.apps), len(PEDIDAS) + 2)

    def test_ids_unicos_y_en_orden_estable(self):
        ids = [a.id for a in self.cat.apps]
        self.assertEqual(len(ids), len(set(ids)))

    def test_spotify_es_un_flatpak_de_flathub(self):
        s = self.cat.por_id["spotify"]
        self.assertEqual(s.tipo, "flatpak")
        self.assertEqual(s.origen["ref"], "com.spotify.Client")
        self.assertEqual(s.origen["remoto"], "flathub")
        self.assertTrue(s.origen["url_remoto"].startswith("https://dl.flathub.org/"))

    def test_antigravity_es_apt_con_la_clave_y_el_repositorio_de_google(self):
        a = self.cat.por_id["antigravity"]
        self.assertEqual(a.tipo, "apt")
        o = a.origen
        self.assertEqual(o["paquete"], "antigravity")
        self.assertEqual(o["clave_url"], "https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg")
        self.assertEqual(o["uri"], "https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/")
        self.assertEqual(o["suite"], "antigravity-debian")
        self.assertEqual(o["componentes"], ("main",))
        self.assertEqual(o["archivo_clave"], "/etc/apt/keyrings/antigravity-repo-key.gpg")
        self.assertTrue(a.aviso, "Antigravity avisa de que el repositorio APT es el heredado 1.x")

    def test_las_webapps_apuntan_a_sus_servicios(self):
        esperado = {"netflix": "www.netflix.com", "hbo-max": "play.hbomax.com", "disney-plus": "www.disneyplus.com",
                    "claude": "claude.ai", "gemini": "gemini.google.com", "notebooklm": "notebooklm.google.com",
                    "classroom": "classroom.google.com", "youtube": "www.youtube.com"}
        for ident, host in esperado.items():
            e = self.cat.por_id[ident]
            self.assertEqual(e.tipo, "webapp", ident)
            self.assertEqual(e.dominio, host, ident)
            self.assertTrue(e.origen["url"].startswith("https://"), ident)

    def test_las_series_piden_drm_y_el_resto_no(self):
        for ident in ("netflix", "hbo-max", "disney-plus"):
            self.assertTrue(self.cat.por_id[ident].drm, ident)
        for ident in ("claude", "gemini", "notebooklm", "classroom", "youtube"):
            self.assertFalse(self.cat.por_id[ident].drm, ident)

    def test_los_navegadores_van_primero_chrome(self):
        self.assertEqual([n.id for n in self.cat.navegadores()], ["chrome", "brave"])

    def test_cada_app_tiene_icono_svg(self):
        for a in self.cat.apps:
            self.assertTrue(os.path.isfile(os.path.join(ICONOS, a.nombre_icono + ".svg")), f"falta el icono de {a.id}")

    def test_todo_apt_lleva_los_datos_para_registrar_su_repositorio(self):
        for a in self.cat.apps:
            if a.tipo == "apt":
                for clave in ("paquete", "uri", "suite", "componentes", "clave_url", "archivo_clave", "archivo_fuente"):
                    self.assertTrue(a.origen.get(clave), f"{a.id}: falta {clave}")
                self.assertTrue(a.origen["uri"].endswith("/"))
                self.assertTrue(a.desktop_ids, f"{a.id}: sin desktop_ids")

    def test_buscar_no_distingue_tildes_ni_mayusculas(self):
        self.assertIn("netflix", [a.id for a in self.cat.buscar("PELÍCULAS")])
        self.assertIn("spotify", [a.id for a in self.cat.buscar("musica")])
        self.assertEqual(len(self.cat.buscar("")), len(self.cat.apps))
        self.assertEqual(self.cat.buscar("zzzz-no-existe"), [])

    def test_las_categorias_existen_y_no_hay_ninguna_vacia(self):
        usadas = {a.categoria for a in self.cat.apps}
        self.assertEqual(usadas, {c.id for c in self.cat.categorias})


class TestValidacion(unittest.TestCase):
    def test_entradas_minimas_validas(self):
        for hacer in (_flatpak, _apt, _web):
            e = C.validar_entrada(hacer(), CATEGORIAS)
            self.assertEqual(e.id, "otra")

    def test_la_uri_de_apt_se_normaliza_con_barra_final(self):
        self.assertEqual(C.validar_entrada(_apt(), CATEGORIAS).origen["uri"], "https://repo.ejemplo.org/apt/")

    def _rechaza(self, hacer, tocar, texto=None):
        d = hacer()
        tocar(d)
        with self.assertRaises(C.CatalogoError) as cm:
            C.validar_entrada(d, CATEGORIAS)
        if texto:
            self.assertIn(texto, str(cm.exception))

    def test_ids_malos(self):
        for malo in ("", "Mayus", "con espacio", "../x", "a/b", "-a", "a-", "a--b", "x" * 41, "ñu", 5, None, ["a"]):
            with self.subTest(id=malo):
                self._rechaza(_web, lambda d, m=malo: d.update(id=m))

    def test_tipo_categoria_y_color(self):
        self._rechaza(_web, lambda d: d.update(tipo="snap"), "tipo")
        self._rechaza(_web, lambda d: d.update(categoria="otra-cosa"), "categoría")
        for color in ("rojo", "#fff", "#ggggggg", "#12345", "red;", "#1234567"):
            self._rechaza(_web, lambda d, c=color: d.update(color=c), "color")

    def test_urls_https_y_sin_trucos(self):
        malas = ("http://otra.ejemplo.org/", "ftp://x.org/", "javascript:alert(1)", "https://user:pw@x.org/",
                 "https://x.org/ a", "https://x.org/\"", "https://x.org/'a", "https://x.org/a\nb", "https://localhost/",
                 "https://x.org/#frag", "https://-x.org/", "https://x.org:abc/", "", "https://", "https://x.org/`id`",
                 "https://x.org/\\x", "https://x.org/<a>", "https://" + "a" * 300 + ".org/")
        for u in malas:
            with self.subTest(url=u):
                self._rechaza(_web, lambda d, u=u: d["origen"].update(url=u))

    def test_apt_rechaza_inyecciones_en_cada_campo(self):
        casos = {
            "paquete": ("x; rm -rf /", "a b", "../x", "$(id)", "", "A", "x\ny"),
            "suite": ("a b", "a;b", "../a", "", "x\ny"),
            "archivo_fuente": ("../x.list", "x", "a/b.list", "x\n.list", ".x.list", "x.txt", "/etc/passwd"),
            "archivo_clave": ("/etc/passwd", "/etc/apt/keyrings/../x.gpg", "/tmp/x.gpg", "/etc/apt/keyrings/x",
                              "/etc/apt/keyrings/a/b.gpg", "etc/apt/keyrings/x.gpg", "/usr/share/keyrings/X.gpg"),
        }
        for campo, valores in casos.items():
            for v in valores:
                with self.subTest(campo=campo, valor=v):
                    self._rechaza(_apt, lambda d, c=campo, v=v: d["origen"].update({c: v}))

    def test_apt_listas(self):
        self._rechaza(_apt, lambda d: d["origen"].update(componentes=[]))
        self._rechaza(_apt, lambda d: d["origen"].update(componentes=["main", "main"]))
        self._rechaza(_apt, lambda d: d["origen"].update(componentes=["ma in"]))
        self._rechaza(_apt, lambda d: d["origen"].update(componentes="main"))
        self._rechaza(_apt, lambda d: d["origen"].update(arquitecturas=["mips"]))
        self._rechaza(_apt, lambda d: d["origen"].update(huella=["abc"]))
        ok = _apt()
        ok["origen"]["huella"] = ["A" * 40]
        self.assertEqual(C.validar_entrada(ok, CATEGORIAS).origen["huella"], ("A" * 40,))

    def test_flatpak(self):
        self._rechaza(_flatpak, lambda d: d["origen"].update(ref="com.spotify"))
        self._rechaza(_flatpak, lambda d: d["origen"].update(ref="com.spotify.Client; rm"))
        self._rechaza(_flatpak, lambda d: d["origen"].update(remoto="Flat Hub"))
        self._rechaza(_flatpak, lambda d: d["origen"].update(url_remoto="http://dl.flathub.org/x"))

    def test_webapp(self):
        self._rechaza(_web, lambda d: d["origen"].update(drm="si"))
        self._rechaza(_web, lambda d: d["origen"].update(palabras_clave=["a;b"]))
        self._rechaza(_web, lambda d: d["origen"].update(categorias_desktop=["Net work"]))
        self._rechaza(_web, lambda d: d.update(desktop_ids=["x.desktop"]), "desktop_ids")

    def test_otros_campos(self):
        self._rechaza(_web, lambda d: d.update(tamano_mb=True))
        self._rechaza(_web, lambda d: d.update(tamano_mb=-1))
        self._rechaza(_web, lambda d: d.update(tamano_mb="9"))
        self._rechaza(_web, lambda d: d.update(navegador=True), "navegador")
        self._rechaza(_web, lambda d: d.update(nombre="a\x1bb"), "control")
        self._rechaza(_web, lambda d: d.update(nombre="x" * 61), "largo")
        self._rechaza(_web, lambda d: d.update(web="http://x.org/"))
        self._rechaza(_web, lambda d: d.update(icono="../x"))
        self._rechaza(_web, lambda d: d.pop("origen"))
        with self.assertRaises(C.CatalogoError):
            C.validar_entrada("no es un dict", CATEGORIAS)


class TestInterpretar(unittest.TestCase):
    def _doc(self, *apps):
        return {"version": 1, "categorias": [{"id": "ia", "nombre": "IA"}], "apps": list(apps)}

    def test_una_entrada_mala_se_descarta_y_el_resto_se_conserva(self):
        malo = _web()
        malo["id"] = "MALO"
        cat = C.interpretar(self._doc(malo, dict(_web(), id="buena")))
        self.assertEqual([a.id for a in cat.apps], ["buena"])
        self.assertEqual(len(cat.problemas), 1)
        self.assertIn("MALO", cat.problemas[0])

    def test_un_id_repetido_no_entra_dos_veces(self):
        cat = C.interpretar(self._doc(_web(), _web()))
        self.assertEqual(len(cat.apps), 1)
        self.assertEqual(len(cat.problemas), 1)

    def test_categoria_inexistente_descarta_la_entrada(self):
        d = _web()
        d["categoria"] = "no-existe"
        self.assertEqual(C.interpretar(self._doc(d)).apps, [])

    def test_documentos_inservibles(self):
        for malo in ([], "x", None, {"version": 2, "categorias": [], "apps": []},
                     {"version": 1, "apps": []}, {"version": 1, "categorias": [], "apps": []},
                     {"version": 1, "categorias": [{"id": "ia", "nombre": "IA"}], "apps": "no"},
                     {"version": 1, "categorias": [{"id": "ia", "nombre": "IA"}], "apps": [None] * 201}):
            with self.subTest(doc=malo):
                with self.assertRaises(C.CatalogoError):
                    C.interpretar(malo)

    def test_no_lanza_otra_cosa_con_basura_en_las_apps(self):
        cat = C.interpretar(self._doc(None, 5, "x", [], {"id": 3}, {"id": "a", "origen": 7}))
        self.assertEqual(cat.apps, [])
        self.assertEqual(len(cat.problemas), 6)


class TestCargar(unittest.TestCase):
    def _escribir(self, contenido, modo="w"):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        ruta = os.path.join(d.name, "catalogo.json")
        with open(ruta, modo) as f:
            f.write(contenido)
        return ruta

    def test_lee_un_catalogo_bueno(self):
        doc = {"version": 1, "categorias": [{"id": "ia", "nombre": "IA"}], "apps": [_web()]}
        cat = C.cargar(self._escribir(json.dumps(doc)))
        self.assertEqual(cat.por_id["otra"].nombre, "Otra")

    def test_errores_de_lectura_son_catalogo_error(self):
        with self.assertRaises(C.CatalogoError):
            C.cargar("/no/existe/catalogo.json")
        with self.assertRaises(C.CatalogoError):
            C.cargar(self._escribir("{no es json"))
        with self.assertRaises(C.CatalogoError):
            C.cargar(self._escribir(b"\xff\xfe\x00".decode("latin-1")))
        with self.assertRaises(C.CatalogoError):
            C.cargar(self._escribir("[" * 100000))      # recursión
        with self.assertRaises(C.CatalogoError):
            C.cargar(self._escribir(" " * (C.MAX_BYTES + 10)))

    def test_ruta_catalogo_apunta_a_un_fichero_que_existe(self):
        self.assertTrue(os.path.isfile(C.ruta_catalogo()))

    def test_ruta_icono(self):
        self.assertIsNone(C.ruta_icono("../etc/passwd", [ICONOS]))
        self.assertIsNone(C.ruta_icono("no-existe", [ICONOS]))
        self.assertIsNone(C.ruta_icono("", [ICONOS]))
        cat = C.cargar(CATALOGO)
        self.assertTrue(C.ruta_icono(cat.por_id["claude"], [ICONOS]).endswith("claude.svg"))


if __name__ == "__main__":
    unittest.main()
