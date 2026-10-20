"""apps.App con las claves propias de la Tienda en el .desktop (X-Infinity-Biblioteca, X-Infinity-Dominio) y cómo el dock
reconoce la ventana de una WebApp (clase «chrome-<host>__…-Default»). Necesita gi (Gio): sin él se salta."""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import gi
    gi.require_version("Gio", "2.0")
    from gi.repository import Gio
    from infinity_shell import apps as A
except Exception as e:  # noqa: BLE001
    A = Gio = None
    _MOTIVO = f"sin gi: {e}"

# GLib solo crea un Gio.DesktopAppInfo si el programa de Exec existe: por eso /bin/true y no un Chrome inventado.
WEBAPP = """[Desktop Entry]
Type=Application
Name={nombre}
Exec=/bin/true --app={url}
Icon=infinity-webapp-{ident}
StartupWMClass={clase}
X-Infinity-Tienda=webapp
X-Infinity-Tienda-Id={ident}
X-Infinity-Dominio={dominio}
"""


@unittest.skipIf(A is None, "sin gi")
class TestClavesDeLaTienda(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def app(self, nombre_archivo, texto):
        ruta = os.path.join(self.dir, nombre_archivo)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        info = Gio.DesktopAppInfo.new_from_filename(ruta)
        self.assertIsNotNone(info, nombre_archivo)
        return A.App(info)

    def test_siempre_se_lee_del_desktop(self):
        a = self.app("infinity-tienda.desktop", "[Desktop Entry]\nType=Application\nName=Tienda\nExec=/bin/true\n"
                                               "X-Infinity-Biblioteca=siempre\n")
        self.assertTrue(a.siempre)
        b = self.app("otra.desktop", "[Desktop Entry]\nType=Application\nName=Otra\nExec=/bin/true\n")
        self.assertFalse(b.siempre)
        c = self.app("rara.desktop", "[Desktop Entry]\nType=Application\nName=Rara\nExec=/bin/true\nX-Infinity-Biblioteca=nunca\n")
        self.assertFalse(c.siempre)

    def test_el_dominio_se_lee_en_minusculas(self):
        a = self.app("infinity-webapp-claude.desktop", WEBAPP.format(
            nombre="Claude", url="https://claude.ai/", ident="claude", clase="chrome-claude.ai__-Default", dominio="Claude.AI"))
        self.assertEqual(a.dominio, "claude.ai")

    def test_una_info_que_no_es_desktop_no_tiene_las_claves(self):
        class Info:
            def get_id(self): return "x.desktop"
            def get_name(self): return "X"
            def get_generic_name(self): return ""
            def get_description(self): return ""
            def get_icon(self): return Gio.ThemedIcon.new("x")
            def get_commandline(self): return "x"
        a = A.App(Info())
        self.assertEqual((a.siempre, a.dominio), (False, ""))


@unittest.skipIf(A is None, "sin gi")
class TestParaClase(unittest.TestCase):
    """El dock asocia la ventana de Hyprland (su clase) con un .desktop."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.indice = A.Indice.__new__(A.Indice)       # sin tocar el sistema: la lista la ponemos a mano
        self.indice.apps = []

    def poner(self, ident, nombre, url, clase, dominio):
        ruta = os.path.join(self.dir, f"infinity-webapp-{ident}.desktop")
        texto = WEBAPP.format(nombre=nombre, url=url, ident=ident, clase=clase, dominio=dominio)
        with open(ruta, "w", encoding="utf-8") as f:
            f.write(texto)
        app = A.App(Gio.DesktopAppInfo.new_from_filename(ruta))
        self.indice.apps.append(app)
        return app

    def test_la_clase_exacta_del_desktop(self):
        claude = self.poner("claude", "Claude", "https://claude.ai/", "chrome-claude.ai__-Default", "claude.ai")
        self.assertIs(self.indice.para_clase("chrome-claude.ai__-Default"), claude)
        self.assertIs(self.indice.para_clase("CHROME-CLAUDE.AI__-DEFAULT"), claude)

    def test_por_dominio_si_la_clase_real_no_es_la_prevista(self):
        # El StartupWMClass de la tienda es una suposición sobre Chromium: si la clase real difiere, el dominio salva.
        claude = self.poner("claude", "Claude", "https://claude.ai/", "chrome-claude.ai__-Default", "claude.ai")
        netflix = self.poner("netflix", "Netflix", "https://www.netflix.com/browse", "chrome-x", "www.netflix.com")
        self.assertIs(self.indice.para_clase("chrome-claude.ai__-Profile_1"), claude)
        self.assertIs(self.indice.para_clase("brave-claude.ai__-Default"), claude)
        self.assertIs(self.indice.para_clase("chromium-www.netflix.com__browse-Default"), netflix)

    def test_no_confunde_dominios_parecidos(self):
        self.poner("claude", "Claude", "https://claude.ai/", "x-claude", "claude.ai")
        self.assertIsNone(self.indice.para_clase("chrome-notclaude.ai__-Default"))
        self.assertIsNone(self.indice.para_clase("chrome-claude.ai.evil.org__-Default"))
        self.assertIsNone(self.indice.para_clase("chrome-gemini.google.com__app-Default"))

    def test_una_clase_que_no_es_de_navegador_no_usa_el_dominio(self):
        self.poner("claude", "Claude", "https://claude.ai/", "x-claude", "claude.ai")
        self.assertIsNone(self.indice.para_clase("foot-claude.ai_"))
        self.assertIsNone(self.indice.para_clase("claude.ai"))
        self.assertIsNone(self.indice.para_clase(""))

    def test_dos_webapps_de_un_mismo_navegador_no_se_cruzan(self):
        a = self.poner("gemini", "Gemini", "https://gemini.google.com/app", "x-1", "gemini.google.com")
        b = self.poner("notebooklm", "NotebookLM", "https://notebooklm.google.com/", "x-2", "notebooklm.google.com")
        self.assertIs(self.indice.para_clase("chrome-gemini.google.com__app-Default"), a)
        self.assertIs(self.indice.para_clase("chrome-notebooklm.google.com__-Default"), b)


if __name__ == "__main__":
    unittest.main()
