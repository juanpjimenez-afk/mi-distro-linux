import json
import os
import sys
import tempfile
import unittest
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from infinity_shell.biblioteca import Biblioteca, Carpeta, es_descargada, leer_manifiesto  # noqa: E402

App = namedtuple("App", "id nombre ruta")
SYS = "/usr/share/applications/"
USR = os.path.expanduser("~/.local/share/applications/")


def apps_de_prueba():
    return [
        App("foot.desktop", "Terminal", SYS + "foot.desktop"),
        App("steam.desktop", "Steam", USR + "steam.desktop"),
        App("lutris.desktop", "Lutris", USR + "lutris.desktop"),
        App("flat.desktop", "Flatpak app", "/var/lib/flatpak/exports/share/applications/flat.desktop"),
        App("zed.desktop", "Zed", "/usr/local/share/applications/zed.desktop"),
    ]


class TestEsDescargada(unittest.TestCase):
    def test_sistema_y_usuario(self):
        self.assertFalse(es_descargada(SYS + "foot.desktop"))
        self.assertFalse(es_descargada("/usr/local/share/applications/zed.desktop"))
        self.assertTrue(es_descargada(USR + "steam.desktop"))
        self.assertTrue(es_descargada("/var/lib/flatpak/exports/share/applications/x.desktop"))

    def test_sin_ruta_cuenta_como_descargada(self):
        self.assertTrue(es_descargada(""))
        self.assertTrue(es_descargada(None))


class TestCarpetas(unittest.TestCase):
    def setUp(self):
        self.b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "biblioteca.json"))

    def test_soltar_crea_carpeta_con_las_dos(self):
        cid = self.b.soltar("steam.desktop", "lutris.desktop")
        self.assertEqual(self.b.carpetas[cid]["apps"], ["lutris.desktop", "steam.desktop"])
        self.assertEqual(self.b.carpeta_de("steam.desktop"), cid)

    def test_soltar_sobre_app_de_carpeta_se_une_a_ella(self):
        cid = self.b.soltar("steam.desktop", "lutris.desktop")
        self.b.soltar("zed.desktop", "steam.desktop")
        self.assertEqual(self.b.carpeta_de("zed.desktop"), cid)
        self.assertEqual(len(self.b.carpetas), 1)

    def test_soltar_app_de_otra_carpeta_la_mueve(self):
        c1 = self.b.soltar("steam.desktop", "lutris.desktop")
        c2 = self.b.soltar("foot.desktop", "zed.desktop")
        self.b.agregar("steam.desktop", c2)
        self.assertEqual(self.b.carpeta_de("steam.desktop"), c2)
        # c1 se queda con una sola app, y sigue existiendo
        self.assertEqual(self.b.carpetas[c1]["apps"], ["lutris.desktop"])

    def test_carpeta_vacia_desaparece(self):
        cid = self.b.soltar("steam.desktop", "lutris.desktop")
        self.b.sacar("steam.desktop")
        self.b.sacar("lutris.desktop")
        self.assertNotIn(cid, self.b.carpetas)

    def test_soltar_una_app_sobre_si_misma_no_hace_nada(self):
        self.assertIsNone(self.b.soltar("steam.desktop", "steam.desktop"))
        self.assertEqual(self.b.carpetas, {})

    def test_agregar_a_carpeta_inexistente_no_hace_nada(self):
        self.b.agregar("steam.desktop", "nada")
        self.assertEqual(self.b.carpetas, {})

    def test_renombrar_y_eliminar(self):
        cid = self.b.soltar("steam.desktop", "lutris.desktop")
        self.b.renombrar(cid, "  Juegos  ")
        self.assertEqual(self.b.carpetas[cid]["nombre"], "Juegos")
        self.b.renombrar(cid, "   ")  # nombre vacío: se ignora
        self.assertEqual(self.b.carpetas[cid]["nombre"], "Juegos")
        self.b.eliminar(cid)
        self.assertEqual(self.b.carpetas, {})
        self.b.eliminar(cid)  # dos veces no falla

    def test_nombre_largo_se_recorta(self):
        cid = self.b.soltar("steam.desktop", "lutris.desktop")
        self.b.renombrar(cid, "x" * 100)
        self.assertEqual(len(self.b.carpetas[cid]["nombre"]), 40)


class TestVista(unittest.TestCase):
    def setUp(self):
        self.b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "biblioteca.json"))

    def test_solo_descargadas_filtra_las_del_sistema(self):
        ids = [a.id for a in self.b.vista(apps_de_prueba()) if not isinstance(a, Carpeta)]
        self.assertEqual(ids, ["flat.desktop", "lutris.desktop", "steam.desktop"])

    def test_sin_filtro_muestra_todo(self):
        self.b.solo_descargadas = False
        self.assertEqual(len(self.b.vista(apps_de_prueba())), 5)

    def test_carpetas_primero_y_apps_sueltas_ordenadas(self):
        self.b.solo_descargadas = False
        self.b.soltar("steam.desktop", "lutris.desktop")
        items = self.b.vista(apps_de_prueba())
        self.assertIsInstance(items[0], Carpeta)
        self.assertEqual(items[0].nombre, "Carpeta nueva")
        self.assertEqual([a.nombre for a in items[0].apps], ["Lutris", "Steam"])
        self.assertEqual([a.nombre for a in items[1:]], ["Flatpak app", "Terminal", "Zed"])

    def test_carpeta_sin_apps_visibles_no_aparece(self):
        # Steam y Lutris están en una carpeta, pero el filtro oculta Terminal, no esas.
        self.b.soltar("foot.desktop", "zed.desktop")  # ambas del sistema
        items = self.b.vista(apps_de_prueba())
        self.assertFalse(any(isinstance(i, Carpeta) for i in items))

    def test_app_en_carpeta_filtrada_no_sale_suelta(self):
        self.b.soltar("foot.desktop", "steam.desktop")
        self.b.solo_descargadas = True
        items = self.b.vista(apps_de_prueba())
        ids = [i.id for i in items if not isinstance(i, Carpeta)]
        self.assertNotIn("foot.desktop", ids)
        carpeta = [i for i in items if isinstance(i, Carpeta)][0]
        self.assertEqual([a.id for a in carpeta.apps], ["steam.desktop"])

    def test_ids_de_apps_desinstaladas_se_ignoran(self):
        self.b.carpetas["c1"] = {"nombre": "Vieja", "apps": ["desinstalada.desktop", "steam.desktop"]}
        items = self.b.vista(apps_de_prueba())
        carpeta = [i for i in items if isinstance(i, Carpeta)][0]
        self.assertEqual([a.id for a in carpeta.apps], ["steam.desktop"])


class TestPersistencia(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ruta = os.path.join(self.dir, "sub", "biblioteca.json")

    def test_ida_y_vuelta(self):
        b = Biblioteca(ruta=self.ruta)
        b.solo_descargadas = False
        cid = b.soltar("steam.desktop", "lutris.desktop")
        b.renombrar(cid, "Juegos")
        self.assertTrue(b.guardar())
        r = Biblioteca.cargar(self.ruta)
        self.assertFalse(r.solo_descargadas)
        self.assertEqual(r.carpetas, {cid: {"nombre": "Juegos", "apps": ["lutris.desktop", "steam.desktop"]}})

    def test_fichero_inexistente_o_corrupto(self):
        self.assertEqual(Biblioteca.cargar(self.ruta).carpetas, {})
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w") as f:
            f.write("{no es json")
        b = Biblioteca.cargar(self.ruta)
        self.assertEqual(b.carpetas, {})
        self.assertTrue(b.solo_descargadas)

    def test_datos_invalidos_se_descartan(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w") as f:
            json.dump({"solo_descargadas": 0, "carpetas": {
                "ok": {"nombre": "Bien", "apps": ["a.desktop", 7, "a.desktop"]},
                "mal": {"nombre": 3, "apps": []},
                "otro": "texto",
            }}, f)
        b = Biblioteca.cargar(self.ruta)
        # Solo "false" de verdad apaga el filtro: 0 no es un booleano y cuenta como el valor por defecto
        self.assertTrue(b.solo_descargadas)
        self.assertEqual(b.carpetas, {"ok": {"nombre": "Bien", "apps": ["a.desktop"]}})

    def test_json_que_no_es_objeto(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w") as f:
            json.dump(["lista"], f)
        self.assertEqual(Biblioteca.cargar(self.ruta).carpetas, {})

    def test_guardar_sin_permisos_devuelve_false(self):
        # Un fichero normal no puede tener hijos: falla tambien como root (que salta los permisos)
        fichero = os.path.join(self.dir, "fichero")
        open(fichero, "w").close()
        b = Biblioteca(ruta=os.path.join(fichero, "biblioteca.json"))
        self.assertFalse(b.guardar())


class TestPersistenciaTiposRaros(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ruta = os.path.join(self.dir, "biblioteca.json")

    def _cargar(self, datos):
        with open(self.ruta, "w") as f:
            json.dump(datos, f)
        return Biblioteca.cargar(self.ruta)

    def test_carpetas_con_tipo_inesperado_no_rompe(self):
        for malo in (5, [1], "texto", None, True):
            with self.subTest(carpetas=malo):
                self.assertEqual(self._cargar({"carpetas": malo}).carpetas, {})

    def test_apps_con_tipo_inesperado_no_rompe(self):
        for malo in (5, "steam.desktop", None, {"a": 1}):
            with self.subTest(apps=malo):
                b = self._cargar({"carpetas": {"c1": {"nombre": "A", "apps": malo}}})
                # Una carpeta sin apps válidas desaparece en vez de quedar vacía
                self.assertNotIn("c1", b.carpetas)

    def test_nombre_vacio_o_largo_en_el_json(self):
        b = self._cargar({"carpetas": {"c1": {"nombre": "   ", "apps": ["a.desktop"]},
                                       "c2": {"nombre": "x" * 100, "apps": ["b.desktop"]}}})
        self.assertEqual(b.carpetas["c1"]["nombre"], "Carpeta")
        self.assertEqual(len(b.carpetas["c2"]["nombre"]), 40)


class TestManifiestoDeLaImagen(unittest.TestCase):
    """Con el manifiesto de la imagen (build.sh), «del sistema» es lo que trae la ISO, no lo que está en /usr/share."""
    BASE = frozenset({"foot.desktop", "thunar.desktop", "infinity-tienda.desktop"})

    def _manifiesto(self, texto):
        d = tempfile.mkdtemp()
        ruta = os.path.join(d, "apps-base.txt")
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        return ruta

    def test_leer_manifiesto(self):
        ruta = self._manifiesto("# apps de fábrica\nfoot.desktop\n\n  thunar.desktop  \n#comentado.desktop\n")
        self.assertEqual(leer_manifiesto(ruta), frozenset({"foot.desktop", "thunar.desktop"}))

    def test_sin_manifiesto_o_ilegible_es_none(self):
        self.assertIsNone(leer_manifiesto("/no/existe/apps-base.txt"))
        self.assertIsNone(leer_manifiesto(tempfile.mkdtemp()))
        ruta = self._manifiesto("")
        with open(ruta, "wb") as f:
            f.write(b"\xff\xfe\x00")
        self.assertIsNone(leer_manifiesto(ruta))

    def test_un_manifiesto_vacio_cuenta_como_manifiesto(self):
        self.assertEqual(leer_manifiesto(self._manifiesto("")), frozenset())

    def test_lo_que_instalan_apt_o_gdebi_en_usr_share_es_descargado(self):
        self.assertFalse(es_descargada(SYS + "foot.desktop", self.BASE))
        self.assertTrue(es_descargada(SYS + "google-chrome.desktop", self.BASE))
        self.assertTrue(es_descargada(SYS + "antigravity.desktop", self.BASE))
        self.assertTrue(es_descargada("/usr/local/share/applications/zed.desktop", self.BASE))
        self.assertFalse(es_descargada("/usr/local/share/applications/foot.desktop", self.BASE))

    def test_lo_de_fuera_de_las_carpetas_del_sistema_sigue_siendo_descargado(self):
        self.assertTrue(es_descargada(USR + "infinity-webapp-netflix.desktop", self.BASE))
        self.assertTrue(es_descargada("/var/lib/flatpak/exports/share/applications/com.spotify.Client.desktop", self.BASE))
        self.assertTrue(es_descargada("", self.BASE))
        self.assertTrue(es_descargada(None, self.BASE))

    def test_sin_manifiesto_se_sigue_usando_la_ruta(self):
        self.assertFalse(es_descargada(SYS + "google-chrome.desktop", None))

    def test_la_biblioteca_usa_el_manifiesto(self):
        b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "b.json"), base=self.BASE)
        apps = apps_de_prueba() + [App("google-chrome.desktop", "Google Chrome", SYS + "google-chrome.desktop")]
        ids = [a.id for a in b.vista(apps)]
        self.assertIn("google-chrome.desktop", ids)
        self.assertNotIn("foot.desktop", ids)

    def test_la_biblioteca_sin_manifiesto_conserva_el_comportamiento_de_siempre(self):
        b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "b.json"), base=None)
        apps = apps_de_prueba() + [App("google-chrome.desktop", "Google Chrome", SYS + "google-chrome.desktop")]
        self.assertNotIn("google-chrome.desktop", [a.id for a in b.vista(apps)])

    def test_cargar_acepta_el_manifiesto(self):
        b = Biblioteca.cargar(os.path.join(tempfile.mkdtemp(), "nada.json"), base=self.BASE)
        self.assertEqual(b.base, self.BASE)

    def test_una_app_que_lo_pide_se_ve_aunque_sea_del_sistema(self):
        AppS = namedtuple("AppS", "id nombre ruta siempre")
        tienda = AppS("infinity-tienda.desktop", "Tienda", SYS + "infinity-tienda.desktop", True)
        otra = AppS("foot.desktop", "Terminal", SYS + "foot.desktop", False)
        b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "b.json"), base=self.BASE)
        self.assertEqual([a.id for a in b.vista([tienda, otra])], ["infinity-tienda.desktop"])
        b.solo_descargadas = False
        self.assertEqual(len(b.vista([tienda, otra])), 2)

    def test_las_apps_sin_el_atributo_siempre_no_rompen(self):
        b = Biblioteca(ruta=os.path.join(tempfile.mkdtemp(), "b.json"), base=self.BASE)
        self.assertEqual(b.vista([App("foot.desktop", "Terminal", SYS + "foot.desktop")]), [])


if __name__ == "__main__":
    unittest.main()


class TestRobustezBiblioteca(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.ruta = os.path.join(self.dir, "biblioteca.json")

    def test_una_app_solo_puede_estar_en_una_carpeta(self):
        with open(self.ruta, "w") as f:
            json.dump({"carpetas": {
                "a": {"nombre": "Juegos", "apps": ["steam.desktop", "lutris.desktop"]},
                "b": {"nombre": "Otra", "apps": ["lutris.desktop"]},
            }}, f)
        b = Biblioteca.cargar(self.ruta)
        ids = [i for c in b.carpetas.values() for i in c["apps"]]
        self.assertEqual(sorted(ids), ["lutris.desktop", "steam.desktop"])
        self.assertEqual(len(ids), len(set(ids)))

    def test_fichero_corrupto_se_copia_antes_de_sobrescribir(self):
        with open(self.ruta, "w") as f:
            f.write('{"carpetas": {"c1": {"nombre": "Juegos", "apps": ["steam.desktop"]}')  # sin cerrar
        b = Biblioteca.cargar(self.ruta)
        self.assertTrue(b.ilegible)
        b.agregar("x.desktop", "nada")  # cualquier cambio dispara la escritura
        self.assertTrue(b.guardar())
        copias = [f for f in os.listdir(self.dir) if ".roto-" in f]
        self.assertEqual(len(copias), 1)
        with open(os.path.join(self.dir, copias[0])) as f:
            self.assertIn("steam.desktop", f.read())
