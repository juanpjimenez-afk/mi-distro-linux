"""Estado de «guardado de cambios» (persistencia de la ISO en vivo): ajustes.estado_persistencia, solo lectura.

En el sistema en vivo /cow NO es un punto de montaje (mountinfo siempre dice «overlay /cow … upperdir=/cow/upper»,
con o sin guardado), así que no se mira. Hay guardado si el arranque lo pide (la palabra «persistent» en /proc/cmdline y
no «nopersistent») Y existe un disco con etiqueta «writable» o «casper-rw» (/dev/disk/by-label). Los dos lectores se
inyectan: las pruebas no leen nada real.
"""
import os
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import ajustes as A  # noqa: E402

CMD_PERSISTENT = "BOOT_IMAGE=/casper/vmlinuz boot=casper persistent noprompt quiet loglevel=3 ---"
CMD_NOPERSISTENT = "BOOT_IMAGE=/casper/vmlinuz boot=casper nopersistent noprompt quiet loglevel=3 ---"
CMD_SIN_NADA = "BOOT_IMAGE=/casper/vmlinuz boot=casper noprompt loglevel=7"
CMD_INSTALADO = "BOOT_IMAGE=/boot/vmlinuz-6.8.0 root=UUID=1234 ro quiet splash"

# Lo que hay en /dev/disk/by-label en cada caso
CON_WRITABLE = ["Ubuntu-Live", "writable", "ESP"]
CON_CASPER_RW = ["casper-rw"]
SIN_ETIQUETA = ["Ubuntu-Live", "ESP", "Datos"]


def estado(cmdline, discos=()):
    """estado_persistencia con un /proc/cmdline y una lista de etiquetas de disco fingidos."""
    def leer(ruta):
        if ruta != "/proc/cmdline":
            raise AssertionError(f"lee {ruta}: solo debe mirar /proc/cmdline (/cow no sirve)")
        return cmdline
    return A.estado_persistencia(leer, lambda: list(discos))


class TestPersistencia(unittest.TestCase):
    def test_persistent_y_partition_writable_se_guarda(self):
        p = estado(CMD_PERSISTENT, CON_WRITABLE)
        self.assertEqual(p.modo, "persistente")
        self.assertEqual(p.titular, "Se guardan tus cambios al apagar")
        self.assertIn("«writable»", p.motivo)
        self.assertEqual(p.pista, "")

    def test_casper_rw_tambien_vale(self):
        p = estado(CMD_PERSISTENT, CON_CASPER_RW)
        self.assertEqual((p.modo, p.titular), ("persistente", "Se guardan tus cambios al apagar"))
        self.assertIn("«casper-rw»", p.motivo)

    def test_persistent_sin_particion_es_modo_en_vivo_porque_no_hay_donde_guardar(self):
        p = estado(CMD_PERSISTENT, SIN_ETIQUETA)
        self.assertEqual(p.modo, "vivo")
        self.assertEqual(p.titular, "Modo en vivo: lo que cambies se pierde al apagar")
        self.assertIn("no hay dónde guardar", p.motivo)
        self.assertIn("«writable»", p.motivo)
        self.assertEqual(p.pista, A.PISTA_GUARDADO)
        # solo vale la etiqueta exacta
        for discos in ([], ["WRITABLE", "Writable", "casper_rw", "writable2", "mi-writable"]):
            self.assertEqual(estado(CMD_PERSISTENT, discos).modo, "vivo", discos)

    def test_la_pista_es_la_pedida(self):
        self.assertEqual(
            A.PISTA_GUARDADO,
            "En un USB: grábalo en modo DD/Etcher y deja espacio libre (se crea sola la partición de guardado al "
            "arrancar). En una máquina virtual: añade un segundo disco virtual vacío y ejecuta en una terminal: "
            "sudo mkfs.ext4 -L writable /dev/sdb (comprueba antes que sdb es el disco vacío).")

    def test_nopersistent_es_sesion_limpia_aunque_haya_particion(self):
        for discos in (CON_WRITABLE, CON_CASPER_RW, SIN_ETIQUETA):
            with self.subTest(discos=discos):
                p = estado(CMD_NOPERSISTENT, discos)
                self.assertEqual((p.modo, p.titular), ("vivo", "Modo en vivo: lo que cambies se pierde al apagar"))
                self.assertIn("Sesión limpia", p.motivo)
                self.assertIn("no se guarda nada", p.motivo)
                self.assertIn("menú de arranque", p.pista)

    def test_persistent_y_nopersistent_a_la_vez_no_guarda(self):
        for orden in ("boot=casper persistent nopersistent", "boot=casper nopersistent persistent"):
            self.assertEqual(estado(orden, CON_WRITABLE).modo, "vivo", orden)

    def test_sin_persistent_no_se_guarda_aunque_haya_particion(self):
        p = estado(CMD_SIN_NADA, CON_WRITABLE)
        self.assertEqual(p.modo, "vivo")
        self.assertIn("no pide guardado", p.motivo)
        self.assertIn("«persistent»", p.motivo)
        self.assertIn("menú de arranque", p.pista)

    def test_solo_cuentan_las_palabras_enteras_del_arranque(self):
        # «persistent-path=x» o «mipersistent» no son la palabra «persistent»
        for orden in ("boot=casper persistent-path=/x quiet", "boot=casper mipersistent", "boot=casper persistente"):
            with self.subTest(orden=orden):
                self.assertIn("no pide guardado", estado(orden, CON_WRITABLE).motivo)
        # y «nopersistent» no cuenta como «persistent»
        self.assertEqual(estado("boot=casper nopersistent", CON_WRITABLE).modo, "vivo")

    def test_sistema_instalado_se_guarda_en_el_disco(self):
        for discos in ([], CON_WRITABLE, SIN_ETIQUETA):
            p = estado(CMD_INSTALADO, discos)
            self.assertEqual(p.modo, "instalado")
            self.assertEqual(p.titular, "Se guardan tus cambios al apagar")
            self.assertIn("instalado en el disco", p.motivo)
            self.assertEqual(p.pista, "")

    def test_sin_datos_que_leer_cuenta_como_modo_en_vivo(self):
        """Un /proc/cmdline vacío o ilegible y sin etiquetas: no se promete guardado."""
        p = estado("", [])
        self.assertEqual((p.modo, p.titular), ("vivo", "Modo en vivo: lo que cambies se pierde al apagar"))
        self.assertIn("/proc/cmdline", p.motivo)
        self.assertEqual(p.pista, A.PISTA_GUARDADO)
        self.assertEqual(estado("", CON_WRITABLE).modo, "vivo")   # sin saber si se pidió, no se asegura

        def no_hay(ruta):
            raise FileNotFoundError(ruta)
        self.assertEqual(A.estado_persistencia(no_hay, lambda: []).modo, "vivo")
        for error in (PermissionError(13, "sin permiso"), OSError(5, "error de E/S"), ValueError("raro")):
            def falla(ruta, e=error):
                raise e
            self.assertEqual(A.estado_persistencia(falla, lambda: CON_WRITABLE).modo, "vivo", error)

    def test_sin_directorio_de_etiquetas_no_hay_donde_guardar(self):
        """/dev/disk/by-label no existe (ninguna partición con etiqueta): se lee como lista vacía, sin lanzar."""
        def no_hay():
            raise FileNotFoundError(A.DIR_ETIQUETAS)
        p = A.estado_persistencia(lambda ruta: CMD_PERSISTENT, no_hay)
        self.assertEqual(p.modo, "vivo")
        self.assertIn("no hay dónde guardar", p.motivo)
        self.assertEqual(A.estado_persistencia(lambda ruta: CMD_PERSISTENT, lambda: (_ for _ in ()).throw(
            PermissionError(13, "sin permiso"))).modo, "vivo")

    def test_las_etiquetas_pueden_ser_cualquier_iterable(self):
        leer = lambda ruta: CMD_PERSISTENT  # noqa: E731
        self.assertEqual(A.estado_persistencia(leer, lambda: iter(["writable"])).modo, "persistente")
        self.assertEqual(A.estado_persistencia(leer, lambda: ("a", "casper-rw")).modo, "persistente")

    def test_mountinfo_ya_no_se_lee(self):
        """/cow siempre sale como «overlay»: se pide solo /proc/cmdline (el lector falla con cualquier otra ruta)."""
        pedidas = []

        def leer(ruta):
            pedidas.append(ruta)
            return CMD_PERSISTENT
        A.estado_persistencia(leer, lambda: CON_WRITABLE)
        self.assertEqual(pedidas, ["/proc/cmdline"])

    def test_devuelve_cuatro_campos_con_texto_en_espanol(self):
        for cmdline, discos in ((CMD_PERSISTENT, CON_WRITABLE), (CMD_PERSISTENT, []), (CMD_NOPERSISTENT, []),
                                (CMD_SIN_NADA, []), ("", []), (CMD_INSTALADO, [])):
            p = estado(cmdline, discos)
            self.assertEqual(p._fields, ("modo", "titular", "motivo", "pista"))
            self.assertIn(p.modo, ("persistente", "instalado", "vivo"))
            self.assertTrue(p.titular and p.motivo)
            self.assertEqual(p.titular, A.TITULAR_GUARDA if p.modo != "vivo" else A.TITULAR_EN_VIVO)
            self.assertEqual(bool(p.pista), p.modo == "vivo")   # solo el modo en vivo trae pista

    def test_sin_lectores_lee_el_sistema_de_verdad_y_no_lanza(self):
        p = A.estado_persistencia()   # en el equipo de pruebas da igual cuál salga: no puede fallar
        self.assertIn(p.modo, ("persistente", "instalado", "vivo"))


if __name__ == "__main__":
    unittest.main()
