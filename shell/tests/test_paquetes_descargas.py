import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))

PACKAGES_LIST = os.path.join(ROOT, "build", "packages.list")
MIMEAPPS = os.path.join(ROOT, "overlay", "etc", "xdg", "mimeapps.list")
HYPRLAND = os.path.join(ROOT, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")

# Comprobados contra el archivo de Ubuntu resolute (los .deb se extrajeron con dpkg-deb).
PAQUETES = ["thunar-archive-plugin", "file-roller", "gdebi", "libfuse2t64", "fuse3", "policykit-1-gnome"]
DEB_MIME = "application/vnd.debian.binary-package"  # MimeType de /usr/share/applications/gdebi.desktop
DESKTOP_GDEBI = "gdebi.desktop"
AGENTE_POLKIT = "/usr/lib/policykit-1-gnome/polkit-gnome-authentication-agent-1"
DBUS = "exec-once = dbus-update-activation-environment"


def _leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


def _paquetes(ruta):
    # Mismo criterio que build/build.sh: el primer campo de cada línea que no es comentario.
    paquetes = []
    for linea in _leer(ruta).splitlines():
        campos = linea.split("#", 1)[0].split()
        if campos:
            paquetes.append(campos[0])
    return paquetes


class TestPaquetesDeDescargas(unittest.TestCase):
    def test_paquetes_elegidos_una_vez_en_packages_list(self):
        lista = _paquetes(PACKAGES_LIST)
        for paquete in PAQUETES:
            self.assertEqual(lista.count(paquete), 1, paquete)


class TestAsociacionDeb(unittest.TestCase):
    def test_mimeapps_solo_asocia_el_deb_a_gdebi(self):
        self.assertTrue(os.path.isfile(MIMEAPPS))
        asociaciones = []
        seccion = None
        for linea in _leer(MIMEAPPS).splitlines():
            linea = linea.strip()
            if not linea or linea.startswith("#"):
                continue
            if linea.startswith("["):
                seccion = linea
            else:
                clave, _, valor = linea.partition("=")
                asociaciones.append((seccion, clave, [d for d in valor.split(";") if d]))
        self.assertEqual(asociaciones, [("[Default Applications]", DEB_MIME, [DESKTOP_GDEBI])])


class TestAgentePolkit(unittest.TestCase):
    def test_arranca_el_agente_justo_despues_de_dbus(self):
        lineas = _leer(HYPRLAND).splitlines()
        dbus = [n for n, linea in enumerate(lineas) if linea.startswith(DBUS)]
        self.assertEqual(len(dbus), 1)
        self.assertEqual(lineas[dbus[0] + 1], "exec-once = " + AGENTE_POLKIT)

    def test_el_agente_aparece_una_sola_vez(self):
        lineas = [linea for linea in _leer(HYPRLAND).splitlines() if "polkit" in linea]
        self.assertEqual(lineas, ["exec-once = " + AGENTE_POLKIT])


if __name__ == "__main__":
    unittest.main()
