"""WebApps de la Tienda: elegir navegador, escribir el .desktop (comillas, clase de ventana), iconos, quitar y reparar."""
import configparser
import os
import stat
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import tienda_catalogo as C  # noqa: E402
from infinity_shell import tienda_webapps as W  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")

SVG_PRUEBA = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256"/></svg>'


def _catalogo():
    return C.cargar(CATALOGO)


def _sin_flatpak(_ref):
    return False


def _leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.datos = os.path.join(tmp.name, "datos")
        self.bin = os.path.join(tmp.name, "bin")
        os.makedirs(self.bin)
        self.svg = os.path.join(tmp.name, "icono.svg")
        with open(self.svg, "w") as f:
            f.write(SVG_PRUEBA)
        self.cat = _catalogo()

    def instalar_falso(self, *nombres):
        """Deja ejecutables falsos con esos nombres y devuelve un `buscar` que solo mira esa carpeta."""
        for n in nombres:
            ruta = os.path.join(self.bin, n)
            with open(ruta, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(ruta, 0o755)
        return lambda nombre: (os.path.join(self.bin, nombre) if os.path.exists(os.path.join(self.bin, nombre)) else None)


class TestNavegadores(Base):
    def test_sin_navegadores(self):
        self.assertEqual(W.encontrar_navegadores(lambda n: None, _sin_flatpak), [])

    def test_orden_de_preferencia_chrome_brave_chromium(self):
        buscar = self.instalar_falso("brave-browser", "chromium", "google-chrome-stable")
        self.assertEqual([n.id for n in W.encontrar_navegadores(buscar, _sin_flatpak)], ["chrome", "brave", "chromium"])

    def test_el_preferido_va_primero(self):
        buscar = self.instalar_falso("brave-browser", "google-chrome-stable")
        self.assertEqual(W.encontrar_navegadores(buscar, _sin_flatpak, preferido="brave")[0].id, "brave")
        # un preferido que no está instalado no cambia nada
        self.assertEqual(W.encontrar_navegadores(buscar, _sin_flatpak, preferido="vivaldi")[0].id, "chrome")

    def test_binario_alternativo_de_la_misma_familia(self):
        buscar = self.instalar_falso("google-chrome")
        nav = W.encontrar_navegadores(buscar, _sin_flatpak)[0]
        self.assertEqual(nav.id, "chrome")
        self.assertTrue(nav.base[0].endswith("google-chrome"))

    def test_flatpak_si_no_hay_binario_y_el_del_sistema_gana(self):
        solo_flatpak = W.encontrar_navegadores(lambda n: None, lambda ref: ref == "com.brave.Browser")
        self.assertEqual([n.id for n in solo_flatpak], ["brave"])
        self.assertEqual(solo_flatpak[0].base, ("flatpak", "run", "com.brave.Browser"))
        self.assertFalse(solo_flatpak[0].perfil_propio)
        buscar = self.instalar_falso("brave-browser")
        ambos = W.encontrar_navegadores(buscar, lambda ref: ref == "com.brave.Browser")
        self.assertEqual(len(ambos), 1)
        self.assertFalse(ambos[0].flatpak)

    def test_flatpak_instalada_mira_las_carpetas(self):
        os.makedirs(os.path.join(self.datos, "flatpak", "app", "com.google.Chrome", "current"))
        self.assertTrue(W.flatpak_instalada("com.google.Chrome", self.datos))
        self.assertFalse(W.flatpak_instalada("com.brave.Browser", self.datos))


class TestComillasDeExec(unittest.TestCase):
    def test_ida_y_vuelta_con_argumentos_dificiles(self):
        casos = [
            ["/usr/bin/x", "--app=https://claude.ai/"],
            ["/usr/bin/x", "--app=https://x.org/a?b=1&c=2"],
            ["/usr/bin/x", "--user-data-dir=/home/ana maria/.local/share/perfil"],
            ["/usr/bin/x", "100%", "%u", "a%%b"],
            ["/usr/bin/x", 'dice "hola"', "con $HOME", "con `id`", "a;b", "a|b", "(x)", "a#b", "~", "'"],
            ["/usr/bin/x", ""],
            ["/usr/bin/x", "--flag=ñandú"],
        ]
        for argv in casos:
            with self.subTest(argv=argv):
                self.assertEqual(W.argv_desde_exec(W.linea_exec(argv)), argv)

    def test_un_argumento_con_barra_invertida_o_control_se_rechaza(self):
        for malo in ("a\\b", "a\nb", "a\x00b", "a\tb\x1b"):
            with self.subTest(arg=malo):
                with self.assertRaises(ValueError):
                    W.linea_exec(["/usr/bin/x", malo])

    def test_porciento_nunca_queda_como_codigo_de_campo(self):
        linea = W.linea_exec(["/usr/bin/x", "--app=https://x.org/%20a?b=%u"])
        self.assertNotIn("%u", linea.replace("%%", ""))
        self.assertNotIn("%2", linea.replace("%%", ""))

    def test_lo_sencillo_no_lleva_comillas(self):
        self.assertEqual(W.linea_exec(["/usr/bin/google-chrome-stable", "--app=https://claude.ai/", "--no-first-run"]),
                         "/usr/bin/google-chrome-stable --app=https://claude.ai/ --no-first-run")

    def test_comillas_sin_cerrar_dan_lista_vacia(self):
        self.assertEqual(W.argv_desde_exec('/usr/bin/x "abc'), [])


class TestDesktop(Base):
    def _texto(self, ident="netflix", navegador=None, perfil="/home/u/.local/share/infinity/webapps/perfil"):
        nav = navegador or W.Navegador("chrome", "Google Chrome", ("/usr/bin/google-chrome-stable",), True, "chrome", False)
        return W.texto_desktop(self.cat.por_id[ident], nav, perfil)

    def _claves(self, texto):
        cp = configparser.RawConfigParser(strict=True, interpolation=None)
        cp.optionxform = str
        cp.read_string(texto)
        return cp["Desktop Entry"]

    def test_es_un_desktop_valido_con_las_claves_esperadas(self):
        d = self._claves(self._texto())
        self.assertEqual(d["Type"], "Application")
        self.assertEqual(d["Name"], "Netflix")
        self.assertEqual(d["Icon"], "infinity-webapp-netflix")
        self.assertEqual(d["Terminal"], "false")
        self.assertEqual(d["Categories"], "AudioVideo;Video;Network;")
        self.assertIn("series;", d["Keywords"])
        self.assertEqual(d["X-Infinity-Tienda"], "webapp")
        self.assertEqual(d["X-Infinity-Tienda-Id"], "netflix")
        self.assertEqual(d["X-Infinity-Dominio"], "www.netflix.com")
        self.assertEqual(d["X-Infinity-Navegador"], "chrome")

    def test_exec_abre_la_web_en_modo_aplicacion_con_perfil_propio(self):
        argv = W.argv_desde_exec(self._claves(self._texto())["Exec"])
        self.assertEqual(argv[0], "/usr/bin/google-chrome-stable")
        self.assertIn("--app=https://www.netflix.com/browse", argv)
        self.assertIn("--user-data-dir=/home/u/.local/share/infinity/webapps/perfil", argv)
        for bandera in W.BANDERAS_COMUNES:
            self.assertIn(bandera, argv)

    def test_clase_de_ventana_como_la_pone_chromium(self):
        d = self._claves(self._texto("claude"))
        self.assertEqual(d["StartupWMClass"], "chrome-claude.ai__-Default")
        self.assertEqual(self._claves(self._texto("netflix"))["StartupWMClass"], "chrome-www.netflix.com__browse-Default")
        self.assertEqual(self._claves(self._texto("gemini"))["StartupWMClass"], "chrome-gemini.google.com__app-Default")
        brave = W.Navegador("brave", "Brave", ("/usr/bin/brave-browser",), True, "brave", False)
        self.assertEqual(self._claves(self._texto("claude", brave))["StartupWMClass"], "brave-claude.ai__-Default")

    def test_navegador_flatpak_usa_su_perfil_y_no_declara_clase(self):
        flat = W.Navegador("chrome", "Google Chrome", ("flatpak", "run", "com.google.Chrome"), False, "chrome", True)
        d = self._claves(self._texto("claude", flat))
        argv = W.argv_desde_exec(d["Exec"])
        self.assertEqual(argv[:3], ["flatpak", "run", "com.google.Chrome"])
        self.assertFalse([a for a in argv if a.startswith("--user-data-dir")])
        self.assertNotIn("StartupWMClass", d)

    def test_url_con_parametros_se_cita_bien(self):
        entrada = C.validar_entrada({"id": "x", "nombre": "X", "resumen": "r", "categoria": "ia", "tipo": "webapp",
                                     "origen": {"url": "https://x.org/a?b=1&c=%20"}}, {"ia"})
        nav = W.Navegador("chrome", "Chrome", ("/usr/bin/chrome",), True, "chrome", False)
        texto = W.texto_desktop(entrada, nav, "/p")
        argv = W.argv_desde_exec(self._claves(texto)["Exec"])
        self.assertIn("--app=https://x.org/a?b=1&c=%20", argv)

    def test_todas_las_webapps_del_catalogo_generan_un_desktop_valido(self):
        for e in self.cat.apps:
            if e.tipo != "webapp":
                continue
            with self.subTest(app=e.id):
                d = self._claves(self._texto(e.id))
                argv = W.argv_desde_exec(d["Exec"])
                self.assertIn(f"--app={e.origen['url']}", argv)
                self.assertEqual(d["Icon"], f"infinity-webapp-{e.id}")

    def test_con_gio_el_comando_es_el_esperado(self):
        try:
            import gi
            gi.require_version("Gio", "2.0")
            from gi.repository import Gio
        except (ImportError, ValueError):
            self.skipTest("sin gi")
        ruta = os.path.join(self.datos, "x.desktop")
        os.makedirs(self.datos)
        entrada = C.validar_entrada({"id": "x", "nombre": "X", "resumen": "r", "categoria": "ia", "tipo": "webapp",
                                     "origen": {"url": "https://x.org/a?b=1&c=2"}}, {"ia"})
        # GLib solo crea el AppInfo si el programa existe: /bin/true hace de navegador
        nav = W.Navegador("chrome", "Chrome", ("/bin/true",), True, "chrome", False)
        with open(ruta, "w") as f:
            f.write(W.texto_desktop(entrada, nav, "/home/ana maria/perfil"))
        info = Gio.DesktopAppInfo.new_from_filename(ruta)
        self.assertIsNotNone(info)
        self.assertEqual(info.get_name(), "X")
        # GLib parte la línea Exec igual que lo hacemos nosotros
        import shlex
        exec_linea = [l for l in _leer(ruta).splitlines() if l.startswith("Exec=")][0][5:]
        self.assertEqual(shlex.split(info.get_commandline()), W.argv_desde_exec(exec_linea))


class TestInstalar(Base):
    def test_sin_navegador_lanza_sin_navegador_y_no_escribe_nada(self):
        with self.assertRaises(W.SinNavegador):
            W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=self.svg,
                              buscar=lambda n: None, flatpak=_sin_flatpak)
        self.assertFalse(os.path.exists(W.ruta_desktop("claude", self.datos)))

    def test_instala_lanzador_icono_y_perfil_privado(self):
        buscar = self.instalar_falso("google-chrome-stable")
        ruta = W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=self.svg, buscar=buscar,
                                 flatpak=_sin_flatpak, rasterizar=None)
        self.assertEqual(ruta, os.path.join(self.datos, "applications", "infinity-webapp-claude.desktop"))
        self.assertEqual(stat.S_IMODE(os.stat(ruta).st_mode), 0o644)
        svg, _png = W.rutas_iconos("claude", self.datos)
        self.assertTrue(svg.endswith("icons/hicolor/scalable/apps/infinity-webapp-claude.svg"))
        self.assertEqual(_leer(svg), SVG_PRUEBA)
        perfil = W.dir_perfil(self.datos)
        self.assertEqual(stat.S_IMODE(os.stat(perfil).st_mode), 0o700)
        # ningún temporal queda al lado
        self.assertEqual([f for f in os.listdir(os.path.dirname(ruta)) if f.startswith(".")], [])

    def test_instalar_dos_veces_es_inocuo_y_el_perfil_se_comparte(self):
        buscar = self.instalar_falso("google-chrome-stable")
        for ident in ("claude", "claude", "gemini"):
            W.instalar_webapp(self.cat.por_id[ident], datos=self.datos, svg=self.svg, buscar=buscar,
                              flatpak=_sin_flatpak, rasterizar=None)
        self.assertEqual(sorted(f for f in os.listdir(os.path.join(self.datos, "applications"))),
                         ["infinity-webapp-claude.desktop", "infinity-webapp-gemini.desktop"])
        self.assertEqual(os.listdir(os.path.join(self.datos, "infinity", "webapps")), ["perfil"])

    def test_el_preferido_se_respeta(self):
        buscar = self.instalar_falso("google-chrome-stable", "brave-browser")
        ruta = W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=self.svg, buscar=buscar,
                                 flatpak=_sin_flatpak, preferido="brave", rasterizar=None)
        self.assertEqual(W.webapp_instalada("claude", self.datos, _sin_flatpak, buscar).navegador, "brave")
        self.assertIn("brave-browser", _leer(ruta))

    def test_no_instala_lo_que_no_es_una_webapp_ni_ids_raros(self):
        with self.assertRaises(ValueError):
            W.instalar_webapp(self.cat.por_id["spotify"], datos=self.datos, svg=self.svg)
        for malo in ("../x", "a/b", "", "A", None):
            with self.subTest(id=malo):
                with self.assertRaises(ValueError):
                    W.ruta_desktop(malo, self.datos)
                with self.assertRaises(ValueError):
                    W.quitar_webapp(malo, self.datos)

    def test_el_rasterizador_se_llama_con_el_svg_ya_instalado(self):
        llamadas = []
        buscar = self.instalar_falso("google-chrome-stable")
        W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=self.svg, buscar=buscar, flatpak=_sin_flatpak,
                          rasterizar=lambda svg, png, tam: llamadas.append((svg, png, tam)))
        svg, png = W.rutas_iconos("claude", self.datos)
        self.assertEqual(llamadas, [(svg, png, 256)])

    def test_un_fallo_al_rasterizar_no_impide_instalar(self):
        # rasterizar_png nunca lanza; aquí un rasterizador que devuelve False tampoco rompe nada
        buscar = self.instalar_falso("google-chrome-stable")
        ruta = W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=self.svg, buscar=buscar,
                                 flatpak=_sin_flatpak, rasterizar=lambda *a: False)
        self.assertTrue(os.path.exists(ruta))

    def test_rasterizar_png_no_lanza_con_un_svg_roto_o_inexistente(self):
        self.assertFalse(W.rasterizar_png("/no/existe.svg", os.path.join(self.datos, "x.png")))

    def test_sin_icono_se_instala_igual(self):
        buscar = self.instalar_falso("google-chrome-stable")
        ruta = W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=None, buscar=buscar,
                                 flatpak=_sin_flatpak, rasterizar=None)
        self.assertTrue(os.path.exists(ruta))

    def test_dir_datos_respeta_xdg_si_es_absoluta(self):
        viejo = os.environ.get("XDG_DATA_HOME")
        self.addCleanup(lambda: os.environ.__setitem__("XDG_DATA_HOME", viejo) if viejo is not None
                        else os.environ.pop("XDG_DATA_HOME", None))
        os.environ["XDG_DATA_HOME"] = "/tmp/xdg-prueba"
        self.assertEqual(W.dir_datos(), "/tmp/xdg-prueba")
        os.environ["XDG_DATA_HOME"] = "relativa"
        self.assertTrue(W.dir_datos().endswith(os.path.join(".local", "share")))


class TestEstadoQuitarReparar(Base):
    def _instalar(self, ident="claude", *nombres):
        buscar = self.instalar_falso(*(nombres or ("google-chrome-stable",)))
        W.instalar_webapp(self.cat.por_id[ident], datos=self.datos, svg=self.svg, buscar=buscar, flatpak=_sin_flatpak,
                          rasterizar=None)
        return buscar

    def test_estado_de_una_webapp_instalada(self):
        buscar = self._instalar()
        info = W.webapp_instalada("claude", self.datos, _sin_flatpak, buscar)
        self.assertEqual((info.id, info.navegador, info.funciona), ("claude", "chrome", True))
        self.assertIsNone(W.webapp_instalada("gemini", self.datos, _sin_flatpak, buscar))

    def test_deja_de_funcionar_si_el_navegador_desaparece(self):
        self._instalar()
        info = W.webapp_instalada("claude", self.datos, _sin_flatpak, lambda n: None)
        self.assertIsNotNone(info)
        os.unlink(os.path.join(self.bin, "google-chrome-stable"))
        info = W.webapp_instalada("claude", self.datos, _sin_flatpak, lambda n: None)
        self.assertFalse(info.funciona)

    def test_un_desktop_ajeno_con_el_mismo_nombre_no_cuenta(self):
        ruta = W.ruta_desktop("claude", self.datos)
        os.makedirs(os.path.dirname(ruta))
        with open(ruta, "w") as f:
            f.write("[Desktop Entry]\nType=Application\nName=Otro\nExec=/bin/true\n")
        self.assertIsNone(W.webapp_instalada("claude", self.datos, _sin_flatpak, lambda n: None))

    def test_quitar_borra_lanzador_e_iconos_pero_no_el_perfil(self):
        self._instalar()
        self.assertTrue(W.quitar_webapp("claude", self.datos))
        self.assertFalse(os.path.exists(W.ruta_desktop("claude", self.datos)))
        for ruta in W.rutas_iconos("claude", self.datos):
            self.assertFalse(os.path.exists(ruta))
        self.assertTrue(os.path.isdir(W.dir_perfil(self.datos)))
        self.assertFalse(W.quitar_webapp("claude", self.datos))    # nada que quitar

    def test_reparar_rehace_los_que_perdieron_su_navegador(self):
        self._instalar("claude", "brave-browser")
        os.unlink(os.path.join(self.bin, "brave-browser"))
        buscar = self.instalar_falso("google-chrome-stable")
        hechas = W.reparar_webapps(self.cat.apps, self.datos, buscar=buscar, flatpak=_sin_flatpak,
                                   svg_de=lambda e: self.svg, rasterizar=None)
        self.assertEqual(hechas, ["claude"])
        self.assertEqual(W.webapp_instalada("claude", self.datos, _sin_flatpak, buscar).navegador, "chrome")

    def test_reparar_no_toca_lo_que_funciona_ni_lo_que_no_esta_instalado(self):
        buscar = self._instalar()
        antes = os.stat(W.ruta_desktop("claude", self.datos)).st_mtime_ns
        self.assertEqual(W.reparar_webapps(self.cat.apps, self.datos, buscar=buscar, flatpak=_sin_flatpak,
                                           svg_de=lambda e: self.svg, rasterizar=None), [])
        self.assertEqual(os.stat(W.ruta_desktop("claude", self.datos)).st_mtime_ns, antes)

    def test_reparar_sin_ningun_navegador_no_hace_nada(self):
        self._instalar()
        os.unlink(os.path.join(self.bin, "google-chrome-stable"))
        self.assertEqual(W.reparar_webapps(self.cat.apps, self.datos, buscar=lambda n: None, flatpak=_sin_flatpak,
                                           svg_de=lambda e: self.svg, rasterizar=None), [])

    def test_reparar_devuelve_el_icono_que_faltaba(self):
        buscar = self._instalar()
        os.unlink(W.rutas_iconos("claude", self.datos)[0])
        hechas = W.reparar_webapps(self.cat.apps, self.datos, buscar=buscar, flatpak=_sin_flatpak,
                                   svg_de=lambda e: self.svg, rasterizar=None)
        self.assertEqual(hechas, ["claude"])
        self.assertTrue(os.path.exists(W.rutas_iconos("claude", self.datos)[0]))


if __name__ == "__main__":
    unittest.main()
