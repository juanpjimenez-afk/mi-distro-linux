"""Instalador a disco, sin tocar ningún disco: se comprueban las órdenes que lanzaría y las reglas de seguridad.

La instalación de verdad (UEFI, arranque sin USB) se probó en una VM con OVMF; aquí corre en CI.
"""
import json
import os
import subprocess
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import instalador as I  # noqa: E402

LSBLK = {"blockdevices": [
    {"name": "sda", "path": "/dev/sda", "size": 15628000000, "model": "USB DISK", "tran": "usb", "rm": True,
     "ro": False, "type": "disk", "mountpoints": [None],
     "children": [{"name": "sda1", "type": "part", "mountpoints": ["/cdrom"]}]},
    {"name": "nvme0n1", "path": "/dev/nvme0n1", "size": 512110190592, "model": "KBG40ZNS512G", "tran": "nvme",
     "rm": False, "ro": False, "type": "disk", "mountpoints": [None],
     "children": [{"name": "nvme0n1p1", "type": "part", "mountpoints": [None]}]},
    {"name": "loop0", "path": "/dev/loop0", "size": 4000000000, "model": None, "tran": None, "rm": False,
     "ro": True, "type": "loop", "mountpoints": ["/rofs"]},
    {"name": "zram0", "path": "/dev/zram0", "size": 2000000000, "model": None, "tran": None, "rm": False,
     "ro": False, "type": "disk", "mountpoints": ["[SWAP]"]},
    {"name": "sdb", "path": "/dev/sdb", "size": 1000000000000, "model": "HDD", "tran": "sata", "rm": False,
     "ro": True, "type": "disk", "mountpoints": [None]},
]}


class Falso(I.Ejecutor):
    """Ejecutor que no corre nada: contesta lo que se le pida en `respuestas` (por nombre de la orden)."""

    def __init__(self, respuestas=None):
        super().__init__(simular=True)
        self.respuestas = respuestas or {}

    def correr(self, argv, entrada=None, comprobar=True, aviso=None):
        self.registro.append(list(argv))
        salida = self.respuestas.get(argv[0], "")
        return subprocess.CompletedProcess(argv, 0, salida, "")


def cfg(**kw):
    base = dict(disco="/dev/nvme0n1", usuario="juan", nombre="Juan", clave="secreta123", equipo="infinity",
                origen="/rofs", medio="/cdrom", destino="/target")
    base.update(kw)
    return I.Config(**base)


class TestValidaciones(unittest.TestCase):
    def test_usuario(self):
        self.assertEqual(I.validar_usuario("juan"), "")
        self.assertEqual(I.validar_usuario("ana_2"), "")
        for malo in ("", "Juan", "1juan", "ju an", "juán", "a" * 33, "root", "infinity", "-x", "a:b"):
            self.assertNotEqual(I.validar_usuario(malo), "", malo)

    def test_equipo(self):
        self.assertEqual(I.validar_equipo("infinity"), "")
        self.assertEqual(I.validar_equipo("mi-pc2"), "")
        for malo in ("", "-pc", "pc-", "mi pc", "pc_1", "pc;ls", "a" * 64):
            self.assertNotEqual(I.validar_equipo(malo), "", malo)

    def test_clave(self):
        self.assertEqual(I.validar_clave("secreta"), "")
        for mala in ("", "12345", "abc:def1", "abc\ndef12"):
            self.assertNotEqual(I.validar_clave(mala), "", repr(mala))

    def test_nombre_completo(self):
        self.assertEqual(I.validar_nombre_completo(""), "")
        self.assertEqual(I.validar_nombre_completo("Juan Pérez"), "")
        self.assertNotEqual(I.validar_nombre_completo("a:b"), "")
        self.assertNotEqual(I.validar_nombre_completo("a,b"), "")


class TestEntorno(unittest.TestCase):
    def test_solo_en_vivo(self):
        self.assertTrue(I.es_vivo("BOOT_IMAGE=/casper/vmlinuz boot=casper quiet"))
        self.assertFalse(I.es_vivo("root=UUID=abc ro quiet"))
        self.assertFalse(I.es_vivo("boot=casperx"))

    def test_sistema_instalado_se_niega(self):
        with self.assertRaises(I.ErrorInstalacion) as e:
            I.comprobar_entorno(cmdline="root=UUID=abc ro quiet", uefi=True, root=True)
        self.assertIn("ISO en vivo", str(e.exception))

    def test_sin_uefi_se_niega(self):
        with self.assertRaises(I.ErrorInstalacion) as e:
            I.comprobar_entorno(cmdline="boot=casper", uefi=False, root=True)
        self.assertIn("UEFI", str(e.exception))

    def test_sin_root_se_niega(self):
        with self.assertRaises(I.ErrorInstalacion):
            I.comprobar_entorno(cmdline="boot=casper", uefi=True, root=False)

    def test_en_vivo_uefi_root_pasa(self):
        I.comprobar_entorno(cmdline="boot=casper", uefi=True, root=True)

    def test_nombres_de_particion(self):
        self.assertEqual(I.particion("/dev/sda", 1), "/dev/sda1")
        self.assertEqual(I.particion("/dev/nvme0n1", 2), "/dev/nvme0n1p2")
        self.assertEqual(I.particion("/dev/mmcblk0", 1), "/dev/mmcblk0p1")


class TestDiscos(unittest.TestCase):
    def setUp(self):
        self.discos = I.listar_discos(Falso({"lsblk": json.dumps(LSBLK)}))

    def test_solo_discos_de_verdad(self):
        # fuera: el loop, el zram y el de solo lectura
        self.assertEqual([d.ruta for d in self.discos], ["/dev/sda", "/dev/nvme0n1"])

    def test_el_usb_de_la_iso_no_se_ofrece(self):
        usb = next(d for d in self.discos if d.ruta == "/dev/sda")
        self.assertTrue(usb.es_medio)
        self.assertEqual([d.ruta for d in I.discos_elegibles(self.discos)], ["/dev/nvme0n1"])

    def test_texto_tam(self):
        self.assertEqual(I.texto_tam(512110190592), "512 GB")
        self.assertEqual(I.texto_tam(2000000000000), "2.0 TB")


class TestOrdenes(unittest.TestCase):
    def correr(self, **kw):
        ej = Falso({"lsblk": ""})
        I.Instalador(cfg(**kw), ej, escribir=lambda *_: None).instalar()
        return ej.registro

    def indice(self, reg, *prefijo):
        for i, o in enumerate(reg):
            if o[:len(prefijo)] == list(prefijo):
                return i
        self.fail(f"no se lanzó {prefijo}: {reg}")

    def test_orden_de_los_pasos(self):
        reg = self.correr()
        orden = [self.indice(reg, *p) for p in (
            ("wipefs",), ("sfdisk",), ("mkfs.vfat",), ("mkfs.ext4",), ("mount", "/dev/nvme0n1p2", "/target"),
            ("rsync",), ("chroot", "/target"), ("umount",))]
        self.assertEqual(orden, sorted(orden))

    def test_solo_toca_el_disco_elegido(self):
        reg = self.correr(disco="/dev/nvme0n1")
        for o in reg:
            if o[0] in ("wipefs", "sfdisk", "mkfs.vfat", "mkfs.ext4", "partprobe"):
                self.assertTrue(any(a.startswith("/dev/nvme0n1") for a in o), o)
                self.assertFalse(any(a.startswith("/dev/sda") for a in o), o)

    def test_particiones_nvme_y_formatos(self):
        reg = self.correr(disco="/dev/nvme0n1")
        self.assertIn(["mkfs.vfat", "-F", "32", "-n", "INFINITYEFI", "/dev/nvme0n1p1"], reg)
        self.assertIn(["mkfs.ext4", "-F", "-q", "-L", "infinity-os", "/dev/nvme0n1p2"], reg)

    def test_sata_no_lleva_p(self):
        reg = self.correr(disco="/dev/sdc")
        self.assertIn(["mkfs.ext4", "-F", "-q", "-L", "infinity-os", "/dev/sdc2"], reg)

    def test_guion_gpt_con_efi_y_raiz(self):
        g = I.guion_particiones()
        self.assertIn("label: gpt", g)
        self.assertIn("size=512MiB, type=U", g)
        self.assertIn("type=L", g)

    def test_la_clave_no_aparece_en_ninguna_orden(self):
        reg = self.correr(clave="supersecreta99")
        self.assertNotIn("supersecreta99", json.dumps(reg))
        self.assertIn(["chroot", "chpasswd", "(contraseña oculta)"], reg)

    def test_el_usuario_va_a_sudo_y_grupos_de_hardware(self):
        ej = Falso({"lsblk": ""})
        I.Instalador(cfg(), ej, escribir=lambda *_: None).instalar()
        useradd = next(o for o in ej.registro if "useradd" in o)
        self.assertIn("-m", useradd)
        self.assertEqual(useradd[-1], "juan")
        grupos = useradd[useradd.index("-G") + 1].split(",")
        self.assertIn("sudo", grupos)
        self.assertIn("video", grupos)

    def test_grub_uefi_y_nucleo_del_medio(self):
        reg = self.correr(medio="/cdrom")
        self.assertTrue(any(o[0] == "install" and "/cdrom/casper/vmlinuz" in o for o in reg))
        g = next(o for o in reg if "grub-install" in o)
        self.assertIn("--target=x86_64-efi", g)
        self.assertIn("--removable", g)   # arranca aunque la BIOS no guarde la entrada
        self.assertTrue(any(o[-1] == "update-grub" for o in reg))

    def test_los_montajes_del_chroot_son_esclavos_antes_de_desmontar(self):
        # Si no, desmontar /target/dev/pts desmonta el /dev/pts real de la sesión en vivo.
        reg = self.correr()
        for fs in ("dev", "sys", "run"):
            esclavo = self.indice(reg, "mount", "--make-rslave", f"/target/{fs}")
            montaje = next(i for i, o in enumerate(reg) if o[0] == "mount" and o[-1] == f"/target/{fs}"
                           and "--make-rslave" not in o)
            desmontaje = self.indice(reg, "umount", "-R", "-l", f"/target/{fs}")
            self.assertLess(montaje, esclavo)
            self.assertLess(esclavo, desmontaje)

    def test_siempre_desmonta(self):
        reg = self.correr()
        self.assertTrue(sum(1 for o in reg if o[0] == "umount") >= 2)

    def test_disco_con_algo_montado_se_niega(self):
        ej = Falso({"lsblk": "nvme0n1p2 /mnt/datos\n"})
        with self.assertRaises(I.ErrorInstalacion) as e:
            I.Instalador(cfg(), ej, escribir=lambda *_: None).instalar()
        self.assertIn("montado", str(e.exception))
        self.assertFalse(any(o[0] in ("wipefs", "sfdisk", "mkfs.ext4") for o in ej.registro))

    def test_fstab_y_greetd(self):
        self.assertIn("UUID=r / ext4", I.fstab("r", "e").replace("         ", " "))
        self.assertIn("/boot/efi vfat", I.fstab("r", "e"))
        g = I.greetd_instalado("juan")
        self.assertIn('user = "juan"', g)                      # entra a su sesión...
        self.assertIn("INFINITY_BLOQUEO_ARRANQUE=1", g)        # ...directo a la pantalla de bloqueo (pide contraseña)
        self.assertIn('user = "_greetd"', g)                   # y si cierra sesión, queda el login de texto
        with self.assertRaises(I.ErrorInstalacion):
            I.greetd_instalado('juan"\ncommand = "x')           # nada de inyectar líneas en el archivo


class TestModoSinPreguntas(unittest.TestCase):
    def test_sin_confirmar_no_hace_nada(self):
        antes = sys.stdin
        try:
            import io
            sys.stdin = io.StringIO("secreta123\n")
            rc = I.main(["--disco", "/dev/sdz", "--usuario", "juan", "--simular"])
        finally:
            sys.stdin = antes
        self.assertEqual(rc, 1)

    def test_simulado_con_confirmacion_lista_las_ordenes(self):
        antes = sys.stdin
        try:
            import io
            sys.stdin = io.StringIO("secreta123\n")
            rc = I.main(["--disco", "/dev/sdz", "--usuario", "juan", "--confirmo", "BORRAR", "--simular"])
        finally:
            sys.stdin = antes
        self.assertEqual(rc, 0)

    def test_usuario_invalido_se_rechaza(self):
        self.assertEqual(I.main(["--disco", "/dev/sdz", "--usuario", "Root!", "--confirmo", "BORRAR", "--simular"]), 1)


class TestAsistente(unittest.TestCase):
    """El asistente interactivo (el que usa la persona): con entradas simuladas y sin instalar de verdad."""

    def correr(self, respuestas, claves=("secreta123", "secreta123")):
        import io
        from unittest import mock
        discos = [I.Disco("/dev/sda", 15628000000, "USB DISK", "usb", True, True),
                  I.Disco("/dev/nvme0n1", 512110190592, "SSD", "nvme", False, False)]
        instalados = []
        with mock.patch.object(I, "comprobar_entorno"), \
                mock.patch.object(I, "origen_sistema", return_value="/rofs"), \
                mock.patch.object(I, "medio_arranque", return_value="/cdrom"), \
                mock.patch.object(I, "listar_discos", return_value=discos), \
                mock.patch.object(I.Instalador, "instalar", lambda self: instalados.append(self.c)), \
                mock.patch("builtins.input", side_effect=list(respuestas)), \
                mock.patch.object(I.getpass, "getpass", side_effect=list(claves)), \
                mock.patch("sys.stdout", new=io.StringIO()) as salida:
            try:
                I.asistente(None)
                error = ""
            except I.ErrorInstalacion as e:
                error = str(e)
        return instalados, error, salida.getvalue()

    def test_flujo_completo(self):
        inst, error, salida = self.correr(["1", "juan", "Juan P", "", "BORRAR"])
        self.assertEqual(error, "")
        self.assertEqual(len(inst), 1)
        c = inst[0]
        self.assertEqual((c.disco, c.usuario, c.nombre, c.equipo), ("/dev/nvme0n1", "juan", "Juan P", "infinity"))
        self.assertNotIn("/dev/sda", salida)          # el USB de la ISO no se ofrece
        self.assertNotIn("secreta123", salida)

    def test_sin_borrar_no_instala(self):
        inst, error, _ = self.correr(["1", "juan", "", "", "borrar"])   # minúsculas: no vale
        self.assertEqual(inst, [])
        self.assertIn("Cancelado", error)

    def test_cancelar_en_el_disco(self):
        inst, error, _ = self.correr(["0"])
        self.assertEqual(inst, [])
        self.assertIn("Cancelado", error)

    def test_reintenta_usuario_y_clave_invalidos(self):
        inst, error, salida = self.correr(["1", "Root!", "juan", "", "", "BORRAR"], claves=("123", "secreta123", "secreta123"))
        self.assertEqual(error, "")
        self.assertEqual(inst[0].usuario, "juan")
        self.assertIn("minúsculas", salida)
        self.assertIn("al menos", salida)

    def test_claves_distintas_se_repiten(self):
        inst, error, salida = self.correr(["1", "juan", "", "", "BORRAR"], claves=("secreta123", "otra12345", "secreta123", "secreta123"))
        self.assertEqual(error, "")
        self.assertIn("No coinciden", salida)


if __name__ == "__main__":
    unittest.main()
