"""La Tienda en la ISO: que todo lo que necesita está en la lista de paquetes, en el overlay y bien enlazado entre sí
(política de polkit ↔ ayudante ↔ lanzador ↔ regla de Hyprland ↔ biblioteca ↔ sesión). Solo lee ficheros del repo."""
import os
import re
import sys
import unittest
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import controles_logica as CL  # noqa: E402
from infinity_shell import tienda_catalogo as C  # noqa: E402
from infinity_shell import tienda_ops as O  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
OVERLAY = os.path.join(ROOT, "overlay")


def _leer(*partes):
    with open(os.path.join(ROOT, *partes), encoding="utf-8") as f:
        return f.read()


def _paquetes():
    """Los paquetes de build/packages.list (sin comentarios), como los lee build.sh."""
    return [l.split("#")[0].split()[0] for l in _leer("build", "packages.list").splitlines() if l.split("#")[0].strip()]


class TestPaquetes(unittest.TestCase):
    def test_la_tienda_tiene_todo_lo_que_necesita_instalado(self):
        paquetes = _paquetes()
        for p in ("flatpak", "pkexec", "polkitd", "policykit-1-gnome", "gpg", "ca-certificates", "python3-gi",
                  "gir1.2-gtk-4.0", "librsvg2-common", "apt"):
            if p == "apt":
                continue    # apt viene con la base de debootstrap
            self.assertIn(p, paquetes, f"falta {p} en build/packages.list")

    def test_sin_duplicados_en_la_lista(self):
        paquetes = _paquetes()
        self.assertEqual(len(paquetes), len(set(paquetes)), [p for p in paquetes if paquetes.count(p) > 1])


class TestPolitica(unittest.TestCase):
    def setUp(self):
        self.ruta = os.path.join(OVERLAY, "usr", "share", "polkit-1", "actions", "org.infinity.tienda.policy")
        self.raiz = ET.parse(self.ruta).getroot()
        self.accion = self.raiz.find("action")

    def test_la_accion_pide_contrasena_de_administrador(self):
        self.assertEqual(self.accion.get("id"), "org.infinity.tienda.apt")
        d = self.accion.find("defaults")
        self.assertEqual(d.findtext("allow_any"), "auth_admin")
        self.assertEqual(d.findtext("allow_inactive"), "auth_admin")
        self.assertEqual(d.findtext("allow_active"), "auth_admin_keep")

    def test_la_politica_apunta_al_ayudante_real_y_al_que_usa_la_ventana(self):
        ruta = next(a.text for a in self.accion.findall("annotate")
                    if a.get("key") == "org.freedesktop.policykit.exec.path")
        self.assertEqual(ruta, "/usr/libexec/infinity/tienda-ayudante")
        self.assertEqual(O.AYUDANTE, ruta)
        self.assertTrue(os.path.isfile(os.path.join(OVERLAY, ruta.lstrip("/"))))

    def test_solo_hay_una_accion_y_con_mensajes_en_espanol(self):
        self.assertEqual(len(self.raiz.findall("action")), 1)
        self.assertIn("administrador", self.accion.findtext("message"))
        self.assertTrue(self.accion.findtext("description"))


class TestAyudanteInstalado(unittest.TestCase):
    RUTA = os.path.join(OVERLAY, "usr", "libexec", "infinity", "tienda-ayudante")

    def test_es_ejecutable_y_con_python_aislado(self):
        self.assertTrue(os.access(self.RUTA, os.X_OK))
        self.assertEqual(_leer("overlay", "usr", "libexec", "infinity", "tienda-ayudante").splitlines()[0],
                         "#!/usr/bin/python3 -I")

    def test_lee_el_catalogo_instalado_por_la_iso(self):
        m = re.search(r'^CATALOGO = "([^"]+)"', _leer("overlay", "usr", "libexec", "infinity", "tienda-ayudante"), re.M)
        self.assertEqual(m.group(1), C.RUTA_SISTEMA)
        self.assertTrue(os.path.isfile(os.path.join(OVERLAY, C.RUTA_SISTEMA.lstrip("/"))))

    def test_solo_usa_ordenes_por_ruta_absoluta(self):
        texto = _leer("overlay", "usr", "libexec", "infinity", "tienda-ayudante")
        for const in ("APT_GET", "DPKG_QUERY", "GPG"):
            m = re.search(rf'^{const} = "([^"]+)"', texto, re.M)
            self.assertTrue(m and m.group(1).startswith("/usr/bin/"), const)
        self.assertNotIn("shell=True", texto)
        self.assertNotIn("os.system", texto)
        self.assertNotIn("os.environ", texto)       # no hereda nada del entorno: usa el suyo


class TestLanzadorYEscritorio(unittest.TestCase):
    def setUp(self):
        self.desktop = _leer("overlay", "usr", "share", "applications", "infinity-tienda.desktop")
        self.claves = dict(l.split("=", 1) for l in self.desktop.splitlines() if "=" in l)

    def test_el_desktop_es_correcto(self):
        self.assertTrue(self.desktop.startswith("[Desktop Entry]"))
        self.assertEqual(self.claves["Type"], "Application")
        self.assertEqual(self.claves["Name"], "Tienda")
        self.assertEqual(self.claves["Terminal"], "false")
        self.assertEqual(self.claves["Exec"], "infinity-tienda")
        self.assertTrue(os.path.isfile(os.path.join(OVERLAY, self.claves["Icon"].lstrip("/"))), "falta el icono")
        self.assertIn("tienda", self.claves["Keywords"])

    def test_pide_ser_visible_en_la_biblioteca(self):
        self.assertEqual(self.claves["X-Infinity-Biblioteca"], "siempre")

    def test_la_clase_de_la_ventana_coincide_en_todas_partes(self):
        clase = self.claves["StartupWMClass"]
        self.assertEqual(clase, "org.infinity.Tienda")
        self.assertIn(f'APP_ID = "{clase}"', _leer("shell", "infinity_shell", "tienda_app.py"))
        hypr = _leer("overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")
        reglas = [l for l in hypr.splitlines() if l.startswith("windowrule") and "Tienda" in l]
        self.assertEqual(len(reglas), 3)
        for r in reglas:
            patron = re.search(r"match:class (\S+)", r).group(1)
            self.assertTrue(re.search(patron, clase), r)
        self.assertTrue(any("float on" in r for r in reglas))
        self.assertTrue(any("center on" in r for r in reglas))
        self.assertTrue(any(re.search(r"size \d+ \d+", r) for r in reglas))

    def test_el_script_lanza_el_modulo_de_la_ventana(self):
        ruta = os.path.join(OVERLAY, "usr", "bin", "infinity-tienda")
        self.assertTrue(os.access(ruta, os.X_OK))
        texto = _leer("overlay", "usr", "bin", "infinity-tienda")
        self.assertIn("PYTHONPATH=/usr/lib/infinity", texto)
        self.assertIn("exec python3 -m infinity_shell.tienda_app", texto)

    def test_la_ventana_dibuja_sus_propios_botones(self):
        self.assertIn("org.infinity.tienda", CL.EXCLUIDAS)


class TestSesionYBiblioteca(unittest.TestCase):
    def test_la_sesion_ve_las_apps_de_flatpak(self):
        sesion = _leer("overlay", "usr", "bin", "infinity-session")
        linea = next(l for l in sesion.splitlines() if l.startswith("export XDG_DATA_DIRS="))
        self.assertIn("$HOME/.local/share/flatpak/exports/share", linea)
        self.assertIn("/var/lib/flatpak/exports/share", linea)
        self.assertIn("/usr/local/share:/usr/share", linea)        # los directorios por defecto se conservan

    def test_build_genera_la_lista_de_apps_de_fabrica(self):
        build = _leer("build", "build.sh")
        self.assertIn('cd "$ROOTFS/usr/share/applications"', build)
        self.assertIn("find . -maxdepth 1 -name '*.desktop'", build)
        self.assertIn('>"$ROOTFS/usr/share/infinity/apps-base.txt"', build)
        from infinity_shell import biblioteca
        self.assertEqual(biblioteca.MANIFIESTO_BASE, "/usr/share/infinity/apps-base.txt")

    def test_la_lista_se_genera_despues_de_copiar_el_overlay_y_la_shell(self):
        build = _leer("build", "build.sh")
        etapa = build.split("etapa_configurar() {", 1)[1].split("# ----", 1)[0]
        self.assertLess(etapa.index('rsync -a --chown=root:root "$ROOT/overlay/"'), etapa.index("apps-base.txt"))
        self.assertLess(etapa.index('"$ROOT/shell/infinity_shell"'), etapa.index("apps-base.txt"))

    def test_los_lanzadores_del_overlay_se_conocen_como_del_sistema_y_la_tienda_se_ve(self):
        from infinity_shell.biblioteca import es_descargada
        base = frozenset(os.listdir(os.path.join(OVERLAY, "usr", "share", "applications")))
        self.assertIn("infinity-tienda.desktop", base)
        self.assertFalse(es_descargada("/usr/share/applications/infinity-tienda.desktop", base))   # es del sistema…
        self.assertTrue(es_descargada("/usr/share/applications/google-chrome.desktop", base))      # …y Chrome no


class TestCatalogoEnLaIso(unittest.TestCase):
    def test_los_iconos_y_el_catalogo_van_al_overlay(self):
        cat = C.cargar(os.path.join(OVERLAY, "usr", "share", "infinity", "tienda", "catalogo.json"))
        for a in cat.apps:
            self.assertTrue(os.path.isfile(os.path.join(OVERLAY, "usr", "share", "infinity", "tienda", "iconos",
                                                        a.nombre_icono + ".svg")), a.id)
        self.assertTrue(os.path.isfile(os.path.join(OVERLAY, "usr", "share", "infinity", "tienda.svg")))

    def test_el_catalogo_no_lo_puede_escribir_nadie_mas_en_el_repo(self):
        modo = os.stat(os.path.join(OVERLAY, "usr", "share", "infinity", "tienda", "catalogo.json")).st_mode
        self.assertFalse(modo & 0o022)


if __name__ == "__main__":
    unittest.main()
