"""Instalador de Infinity OS en el disco del equipo. Sin GTK: es un asistente de terminal.

Qué hace (y solo con confirmación escrita): BORRA un disco entero, lo deja con una partición EFI (FAT32, 512 MiB)
y otra ext4 con el resto, copia el sistema en vivo, crea tu usuario y pone GRUB (UEFI). Después se arranca sin USB.

Reglas de seguridad, en este orden:
  - solo corre en la ISO en vivo (palabra «boot=casper» en /proc/cmdline): nunca sobre un sistema ya instalado;
  - solo con arranque UEFI (si no, GRUB-EFI no arrancaría);
  - no ofrece el disco de donde arrancó la ISO ni los discos de solo lectura;
  - se elige el disco por su ruta exacta y hay que escribir BORRAR para empezar;
  - la contraseña nunca va en una orden: se pasa por la entrada estándar de chpasswd y no se imprime.

Los comandos pasan por un `Ejecutor`, así las pruebas (tests/test_instalador.py) comprueban la lista de órdenes
sin tocar ningún disco. La instalación de verdad se probó en una VM con OVMF (UEFI).
"""
import argparse
import getpass
import json
import os
import re
import shutil
import subprocess
import sys
import time
from collections import namedtuple

TAM_EFI_MIB = 512
ETIQUETA_RAIZ = "infinity-os"
ETIQUETA_EFI = "INFINITYEFI"
DESTINO = "/target"
ORIGENES = ("/rofs", "/run/live/rootfs/filesystem.squashfs")     # el sistema en vivo (squashfs ya montado)
MEDIOS = ("/cdrom", "/run/live/medium", "/lib/live/mount/medium")  # de dónde arrancó la ISO
GRUPOS_USUARIO = ("sudo", "video", "audio", "render", "input", "netdev", "plugdev")
PALABRA_BORRAR = "BORRAR"
CLAVE_MIN = 6

Disco = namedtuple("Disco", "ruta tam modelo transporte removible es_medio")


class ErrorInstalacion(Exception):
    """Algo impidió instalar. El texto está en español y se muestra tal cual."""


# ------------------------------------------------------------------ ejecutar órdenes
class Ejecutor:
    """Corre órdenes (siempre una lista, nunca texto con shell). Las pruebas lo sustituyen."""

    def __init__(self, simular=False):
        self.simular = simular
        self.registro = []          # las órdenes ejecutadas, sin las entradas secretas

    def correr(self, argv, entrada=None, comprobar=True, aviso=None):
        self.registro.append(list(argv))
        if self.simular:
            return subprocess.CompletedProcess(argv, 0, "", "")
        r = subprocess.run(argv, input=entrada, capture_output=True, text=True, errors="replace")
        if comprobar and r.returncode != 0:
            salida = (r.stderr or r.stdout).strip().splitlines()[-3:]
            raise ErrorInstalacion(f"{aviso or 'Falló ' + argv[0]}: {' '.join(salida)}".strip())
        return r


# ------------------------------------------------------------------ validaciones
def validar_usuario(nombre):
    if not re.fullmatch(r"[a-z_][a-z0-9_-]{0,31}", nombre or ""):
        return "El usuario va en minúsculas, sin espacios ni tildes (letras, números, «-» y «_»), máximo 32."
    if nombre in ("root", "daemon", "bin", "sys", "nobody", "infinity", "greeter", "_greetd"):
        return f"«{nombre}» está reservado por el sistema: elige otro."
    return ""


def validar_equipo(nombre):
    if not re.fullmatch(r"[a-zA-Z0-9]([a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", nombre or ""):
        return "El nombre del equipo lleva solo letras, números y «-» (sin espacios), y no empieza ni acaba en «-»."
    return ""


def validar_clave(clave):
    if len(clave or "") < CLAVE_MIN:
        return f"La contraseña necesita al menos {CLAVE_MIN} caracteres."
    if "\n" in clave or "\r" in clave or ":" in clave:
        return "La contraseña no puede llevar saltos de línea ni «:»."
    return ""


def validar_nombre_completo(texto):
    if any(c in texto for c in ":\n\r,=") or len(texto) > 60:
        return "El nombre no puede llevar «:» «,» «=» ni saltos de línea (máximo 60 caracteres)."
    return ""


# ------------------------------------------------------------------ comprobaciones del entorno
def es_vivo(cmdline):
    return "boot=casper" in cmdline.split()


def hay_uefi(ruta="/sys/firmware/efi"):
    return os.path.isdir(ruta)


def particion(dispositivo, numero):
    """/dev/sda → /dev/sda1; /dev/nvme0n1 y /dev/mmcblk0 → ...p1."""
    return f"{dispositivo}p{numero}" if dispositivo[-1].isdigit() else f"{dispositivo}{numero}"


def _hijos(nodo):
    yield nodo
    for h in nodo.get("children") or []:
        yield from _hijos(h)


def listar_discos(ejecutor):
    """Los discos donde se puede instalar, con es_medio=True el de donde arrancó la ISO (no se ofrece)."""
    r = ejecutor.correr(["lsblk", "-J", "-b", "-o", "NAME,PATH,SIZE,MODEL,TRAN,RM,RO,TYPE,MOUNTPOINTS"])
    discos = []
    for d in json.loads(r.stdout or "{}").get("blockdevices", []):
        if d.get("type") != "disk" or d.get("ro") or re.match(r"(zram|loop|ram|sr|fd)", d.get("name", "")):
            continue
        montajes = [m for h in _hijos(d) for m in (h.get("mountpoints") or []) if m]
        es_medio = any(m in MEDIOS or m.startswith(("/run/live", "/cdrom", "/isodevice")) for m in montajes)
        discos.append(Disco(d["path"], int(d.get("size") or 0), (d.get("model") or "").strip(),
                            (d.get("tran") or "").strip(), bool(d.get("rm")) or d.get("rm") in ("1", 1), es_medio))
    return discos


def texto_tam(n):
    return f"{n / 1e9:.0f} GB" if n < 1e12 else f"{n / 1e12:.1f} TB"


def discos_elegibles(discos):
    return [d for d in discos if not d.es_medio]


def origen_sistema():
    for ruta in ORIGENES:
        if os.path.isdir(ruta) and os.path.exists(os.path.join(ruta, "usr")):
            return ruta
    raise ErrorInstalacion("No encuentro el sistema en vivo (/rofs). ¿Estás dentro de la ISO de Infinity OS?")


def medio_arranque():
    for ruta in MEDIOS:
        if os.path.exists(os.path.join(ruta, "casper", "vmlinuz")):
            return ruta
    raise ErrorInstalacion("No encuentro el medio de arranque (falta casper/vmlinuz): sin el núcleo no habría "
                           "con qué arrancar el sistema instalado.")


# ------------------------------------------------------------------ la instalación
Config = namedtuple("Config", "disco usuario nombre clave equipo origen medio destino")


def fstab(uuid_raiz, uuid_efi):
    return ("# /etc/fstab: generado por el instalador de Infinity OS\n"
            f"UUID={uuid_raiz} /         ext4 noatime,errors=remount-ro 0 1\n"
            f"UUID={uuid_efi} /boot/efi vfat umask=0077 0 2\n")


def grub_defecto(nombre_os="Infinity OS"):
    return (f'GRUB_DEFAULT=0\nGRUB_TIMEOUT=3\nGRUB_DISTRIBUTOR="{nombre_os}"\n'
            'GRUB_CMDLINE_LINUX_DEFAULT="quiet loglevel=3 nowatchdog"\nGRUB_CMDLINE_LINUX=""\n'
            "GRUB_DISABLE_OS_PROBER=true\n")


def greetd_instalado():
    """Sin inicio de sesión automático: el sistema instalado pide usuario y contraseña."""
    return ('# greetd: gestor de inicio de sesión mínimo (sistema instalado).\n[terminal]\nvt = 7\n\n'
            '[default_session]\ncommand = "tuigreet --time --asterisks --remember --cmd infinity-session"\n'
            'user = "_greetd"\n')


def guion_particiones():
    return (f"label: gpt\n"
            f"size={TAM_EFI_MIB}MiB, type=U, name=\"EFI\"\n"
            f"type=L, name=\"Infinity OS\"\n")


class Instalador:
    def __init__(self, cfg, ejecutor, escribir=print, esperar=time.sleep):
        self.c = cfg
        self.ej = ejecutor
        self.escribir = escribir
        self.esperar = esperar
        self.montado = []

    # ---- utilidades
    def paso(self, texto):
        self.escribir(f"\n==> {texto}")

    def chroot(self, argv, **kw):
        return self.ej.correr(["chroot", self.c.destino, "/usr/bin/env", "-i", "HOME=/root", "TERM=xterm",
                               "LANG=C.UTF-8", "DEBIAN_FRONTEND=noninteractive",
                               "PATH=/usr/sbin:/usr/bin:/sbin:/bin", *argv], **kw)

    def escribir_archivo(self, ruta, texto, modo=0o644):
        destino = self.c.destino + ruta
        if self.ej.simular:
            self.ej.registro.append(["escribir", ruta])
            return
        os.makedirs(os.path.dirname(destino), exist_ok=True)
        with open(destino, "w", encoding="utf-8") as f:
            f.write(texto)
        os.chmod(destino, modo)

    # ---- pasos
    def preparar_disco(self):
        d, ej = self.c.disco, self.ej
        self.paso(f"Preparando el disco {d} (se borra todo lo que tenga)")
        r = ej.correr(["lsblk", "-nr", "-o", "NAME,MOUNTPOINTS", d], comprobar=False)
        for linea in (r.stdout or "").splitlines():
            partes = linea.split(None, 1)
            if len(partes) == 2 and partes[1].strip():
                raise ErrorInstalacion(f"El disco {d} tiene algo montado ({partes[1].strip()}). "
                                       "No se instala sobre un disco en uso.")
        ej.correr(["wipefs", "--all", "--force", d], aviso=f"No se pudo limpiar {d}")
        ej.correr(["sfdisk", "--force", "--wipe", "always", "--wipe-partitions", "always", d],
                  entrada=guion_particiones(), aviso=f"No se pudo particionar {d}")
        ej.correr(["udevadm", "settle"], comprobar=False)
        ej.correr(["partprobe", d], comprobar=False)
        efi, raiz = particion(d, 1), particion(d, 2)
        for _ in range(30):
            if ej.simular or (os.path.exists(efi) and os.path.exists(raiz)):
                break
            self.esperar(0.5)
        else:
            raise ErrorInstalacion(f"El sistema no vio las particiones nuevas de {d}.")
        self.paso("Dando formato")
        ej.correr(["mkfs.vfat", "-F", "32", "-n", ETIQUETA_EFI, efi], aviso="No se pudo formatear la partición EFI")
        ej.correr(["mkfs.ext4", "-F", "-q", "-L", ETIQUETA_RAIZ, raiz], aviso="No se pudo formatear la partición del sistema")
        return efi, raiz

    def montar(self, efi, raiz):
        ej, dest = self.ej, self.c.destino
        os.makedirs(dest, exist_ok=True) if not ej.simular else None
        ej.correr(["mount", raiz, dest], aviso="No se pudo montar la partición del sistema")
        self.montado.append(dest)
        if not ej.simular:
            os.makedirs(dest + "/boot/efi", exist_ok=True)
        ej.correr(["mount", efi, dest + "/boot/efi"], aviso="No se pudo montar la partición EFI")
        self.montado.append(dest + "/boot/efi")

    def copiar_sistema(self):
        self.paso("Copiando el sistema (unos minutos)")
        self.ej.correr(["rsync", "-aHAXx", "--numeric-ids", "--info=progress2", self.c.origen.rstrip("/") + "/",
                        self.c.destino + "/"], aviso="Falló la copia del sistema")

    def montar_chroot(self):
        dest = self.c.destino
        for fs, args in (("dev", ["--rbind"]), ("proc", ["-t", "proc"]), ("sys", ["--rbind"]), ("run", ["--bind"])):
            origen = {"dev": "/dev", "proc": "proc", "sys": "/sys", "run": "/run"}[fs]
            if not self.ej.simular:
                os.makedirs(f"{dest}/{fs}", exist_ok=True)
            self.ej.correr(["mount", *args, origen, f"{dest}/{fs}"], aviso=f"No se pudo montar /{fs} en el destino")
            self.montado.append(f"{dest}/{fs}")
            # «esclavo»: al desmontar la copia no se desmonta también el original. Sin esto, umount del /dev/pts
            # del destino quitaba el /dev/pts de la sesión en vivo (sudo y las terminales dejaban de funcionar).
            self.ej.correr(["mount", "--make-rslave", f"{dest}/{fs}"], aviso=f"No se pudo aislar /{fs} del destino")

    def configurar(self, uuid_efi, uuid_raiz):
        c = self.c
        self.paso("Configurando el sistema")
        self.escribir_archivo("/etc/fstab", fstab(uuid_raiz, uuid_efi))
        self.escribir_archivo("/etc/hostname", c.equipo + "\n")
        self.escribir_archivo("/etc/hosts", f"127.0.0.1 localhost\n127.0.1.1 {c.equipo}\n"
                              "::1 localhost ip6-localhost ip6-loopback\n")
        self.escribir_archivo("/etc/greetd/config.toml", greetd_instalado())
        self.escribir_archivo("/etc/default/grub", grub_defecto())
        # Cada instalación tiene su propio machine-id (systemd lo genera al primer arranque)
        self.escribir_archivo("/etc/machine-id", "")
        self.ej.correr(["rm", "-f", c.destino + "/var/lib/dbus/machine-id", c.destino + "/etc/casper.conf"])
        # Wifi ya conectada en la sesión en vivo: se conserva
        redes = "/etc/NetworkManager/system-connections"
        if self.ej.simular or (os.path.isdir(redes) and os.listdir(redes)):
            self.ej.correr(["cp", "-a", redes + "/.", c.destino + redes + "/"], comprobar=False)
        # Usuario
        grupos = []
        for g in GRUPOS_USUARIO:
            if self.chroot(["getent", "group", g], comprobar=False).returncode == 0:
                grupos.append(g)
        orden = ["useradd", "-m", "-s", "/bin/bash", "-G", ",".join(grupos)]
        if c.nombre:
            orden += ["-c", c.nombre]
        self.chroot(orden + [c.usuario], aviso=f"No se pudo crear el usuario {c.usuario}")
        # La contraseña va por la entrada estándar: nunca en una orden ni en el registro
        self.chroot(["chpasswd"], entrada=f"{c.usuario}:{c.clave}\n", aviso="No se pudo poner la contraseña")
        self.ej.registro[-1] = ["chroot", "chpasswd", "(contraseña oculta)"]

    def arranque(self):
        c, d = self.c, self.c.disco
        self.paso("Instalando el arranque (GRUB, UEFI)")
        # El sistema en vivo no lleva /boot en el squashfs: el núcleo viene del medio de arranque
        if self.ej.simular:
            version = "6.x-simulado"
        else:
            vers = sorted(os.listdir(c.destino + "/lib/modules")) if os.path.isdir(c.destino + "/lib/modules") else []
            if not vers:
                raise ErrorInstalacion("El sistema copiado no trae módulos del núcleo (/lib/modules).")
            version = vers[-1]
        self.ej.correr(["install", "-m644", f"{c.medio}/casper/vmlinuz", f"{c.destino}/boot/vmlinuz-{version}"],
                       aviso="No se pudo copiar el núcleo")
        self.chroot(["update-initramfs", "-c", "-k", version], aviso="Falló update-initramfs")
        self.chroot(["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi", "--removable",
                     "--no-nvram"], aviso="Falló grub-install")
        # Entrada propia en la memoria de la placa (mejor que solo el arranque «removible»); si falla no pasa nada
        self.chroot(["grub-install", "--target=x86_64-efi", "--efi-directory=/boot/efi",
                     "--bootloader-id=InfinityOS"], comprobar=False)
        self.chroot(["update-grub"], aviso="Falló update-grub")

    def desmontar(self):
        for m in reversed(self.montado):
            self.ej.correr(["umount", "-R", "-l", m], comprobar=False)
        self.montado.clear()

    def instalar(self):
        efi, raiz = self.preparar_disco()
        try:
            self.montar(efi, raiz)
            self.copiar_sistema()
            self.montar_chroot()
            if self.ej.simular:
                u_efi, u_raiz = "UUID-EFI", "UUID-RAIZ"
            else:
                u_efi = self.ej.correr(["blkid", "-s", "UUID", "-o", "value", efi]).stdout.strip()
                u_raiz = self.ej.correr(["blkid", "-s", "UUID", "-o", "value", raiz]).stdout.strip()
            self.configurar(u_efi, u_raiz)
            self.arranque()
            self.ej.correr(["sync"])
        finally:
            self.desmontar()
        self.paso("Listo")


# ------------------------------------------------------------------ asistente de terminal
def _preguntar(texto, defecto="", validar=None, leer=None):
    leer = leer or input
    while True:
        pista = f" [{defecto}]" if defecto else ""
        valor = leer(f"{texto}{pista}: ").strip() or defecto
        error = validar(valor) if validar else ""
        if not error:
            return valor
        print(f"  ✗ {error}")


def _preguntar_clave(leer=None):
    leer = leer or getpass.getpass
    while True:
        a = leer("Contraseña (no se ve al escribir): ")
        error = validar_clave(a)
        if error:
            print(f"  ✗ {error}")
            continue
        if leer("Repite la contraseña: ") != a:
            print("  ✗ No coinciden. Prueba otra vez.")
            continue
        return a


def comprobar_entorno(cmdline=None, uefi=None, root=None):
    cmdline = open("/proc/cmdline", encoding="utf-8").read() if cmdline is None else cmdline
    if not es_vivo(cmdline):
        raise ErrorInstalacion("Este instalador solo funciona desde la ISO en vivo de Infinity OS. "
                               "En un sistema ya instalado borraría tu propio disco, y no lo permito.")
    if not (hay_uefi() if uefi is None else uefi):
        raise ErrorInstalacion("Este equipo arrancó en modo BIOS antiguo (no UEFI). El instalador solo prepara "
                               "arranque UEFI: en la BIOS del equipo activa el arranque UEFI y vuelve a empezar.")
    if (os.geteuid() == 0) if root is None else root:
        return
    raise ErrorInstalacion("Hace falta ser administrador: ejecútalo con «sudo infinity-instalar».")


def asistente(args):
    ej = Ejecutor()
    comprobar_entorno()
    origen, medio = origen_sistema(), medio_arranque()
    print("\n  INFINITY OS — Instalar en el disco\n  ===================================")
    print("  Se BORRARÁ POR COMPLETO el disco que elijas (incluido Windows, si está ahí).\n")
    discos = discos_elegibles(listar_discos(ej))
    if not discos:
        raise ErrorInstalacion("No encuentro ningún disco donde instalar. En un Dell, si el disco no aparece, "
                               "pon el modo SATA en «AHCI» (BIOS → Storage → SATA Operation) en vez de «RAID On».")
    for i, d in enumerate(discos, 1):
        etiqueta = " (USB/extraíble)" if d.removible or d.transporte == "usb" else ""
        print(f"  {i}) {d.ruta:<14} {texto_tam(d.tam):>8}  {d.modelo or 'sin nombre'} {d.transporte}{etiqueta}")
    elegido = None
    while elegido is None:
        r = input("\nEscribe el NÚMERO del disco donde instalar (o 0 para cancelar): ").strip()
        if r == "0":
            raise ErrorInstalacion("Cancelado. No se ha tocado nada.")
        if r.isdigit() and 1 <= int(r) <= len(discos):
            elegido = discos[int(r) - 1]
    usuario = _preguntar("Nombre de usuario (minúsculas)", "", validar_usuario)
    nombre = _preguntar("Tu nombre (opcional, Enter para omitir)", "", validar_nombre_completo)
    equipo = _preguntar("Nombre del equipo", "infinity", validar_equipo)
    clave = _preguntar_clave()
    print(f"\n  Se va a BORRAR {elegido.ruta} ({texto_tam(elegido.tam)}, {elegido.modelo or 'sin nombre'}).")
    if input(f"  Para continuar escribe {PALABRA_BORRAR} (cualquier otra cosa cancela): ").strip() != PALABRA_BORRAR:
        raise ErrorInstalacion("Cancelado. No se ha tocado nada.")
    cfg = Config(elegido.ruta, usuario, nombre, clave, equipo, origen, medio, DESTINO)
    Instalador(cfg, ej).instalar()
    print("\n  Instalación terminada. Apaga, QUITA EL USB y enciende: arrancará Infinity OS desde el disco.")
    print("  (Si el PC vuelve a arrancar Windows, entra en la BIOS y pon el disco de Infinity OS primero.)")


def sin_preguntas(args):
    """Modo para pruebas: todo por parámetros y la contraseña por la entrada estándar."""
    ej = Ejecutor(simular=args.simular)
    if not args.simular:
        comprobar_entorno()
    for texto, err in ((args.usuario, validar_usuario(args.usuario)), (args.equipo, validar_equipo(args.equipo))):
        if err:
            raise ErrorInstalacion(err)
    clave = sys.stdin.readline().rstrip("\n")
    if validar_clave(clave):
        raise ErrorInstalacion(validar_clave(clave))
    if args.confirmo != PALABRA_BORRAR:
        raise ErrorInstalacion(f"Falta --confirmo {PALABRA_BORRAR}.")
    if not args.simular and args.disco not in [d.ruta for d in discos_elegibles(listar_discos(ej))]:
        raise ErrorInstalacion(f"{args.disco} no es un disco donde se pueda instalar (¿es el de la ISO?).")
    origen = args.origen or (origen_sistema() if not args.simular else "/rofs")
    medio = medio_arranque() if not args.simular else "/cdrom"
    Instalador(Config(args.disco, args.usuario, "", clave, args.equipo, origen, medio, DESTINO), ej).instalar()


def main(argv=None):
    p = argparse.ArgumentParser(prog="infinity-instalar", description="Instala Infinity OS en el disco del equipo.")
    p.add_argument("--disco")
    p.add_argument("--usuario")
    p.add_argument("--equipo", default="infinity")
    p.add_argument("--origen", help="solo pruebas: carpeta de donde copiar el sistema")
    p.add_argument("--confirmo", help=f"solo modo sin preguntas: la palabra {PALABRA_BORRAR}")
    p.add_argument("--simular", action="store_true", help="no ejecuta nada: solo muestra las órdenes")
    args = p.parse_args(argv)
    try:
        if args.disco:
            if not args.usuario:
                raise ErrorInstalacion("Falta --usuario.")
            sin_preguntas(args)
        else:
            asistente(args)
    except ErrorInstalacion as e:
        print(f"\n  ✗ {e}", file=sys.stderr)
        return 1
    except (KeyboardInterrupt, EOFError):
        print("\n  Cancelado.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
