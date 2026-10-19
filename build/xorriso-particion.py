#!/usr/bin/python3
"""Envoltorio de xorriso para grub-mkrescue (--xorriso=...): reescribe la orden para que el sistema de ficheros ISO
quede DENTRO de una partición (receta de las ISO oficiales de Ubuntu: -partition_offset 16 + EFI como partición
añadida). Así casper monta la partición /dev/sdX1 y no el disco entero, y puede crear/montar la partición de
guardado (con el disco entero montado el kernel responde «busy» al montar cualquier otra partición del mismo disco)."""
import os, subprocess, sys

args = sys.argv[1:]
if "-as" not in args or "mkisofs" not in args or "-o" not in args:   # sondeos (-help, -version) y otros usos: tal cual
    os.execvp("xorriso", ["xorriso"] + args)

salida, quitar, i, sal, raiz = [], 0, 0, None, None
SIN_ARG = {"-hfsplus", "--protective-msdos-label", "-efi-boot-part", "--efi-boot-image"}
CON_ARG = {"-apm-block-size": 1, "--efi-boot": 1, "-hfsplus-file-creator-type": 3, "-hfs-bless-by": 2}
while i < len(args):
    a = args[i]
    if a == "--":                                    # lo que sigue son órdenes nativas de xorriso (-volid …)
        salida += args[i:]; break
    if a in SIN_ARG: i += 1; continue
    if a in CON_ARG: i += 1 + CON_ARG[a]; continue
    if a == "-o": sal = args[i + 1]
    if a == "-r" and raiz is None and i + 1 < len(args): raiz = args[i + 1]
    salida.append(a); i += 1

efi = os.path.join(raiz, "efi.img") if raiz else None
if not (efi and os.path.exists(efi)):
    sys.exit("xorriso-particion: no encuentro efi.img en el directorio temporal de grub-mkrescue")
# insertar antes de «--» las opciones de layout
corte = salida.index("--") if "--" in salida else len(salida)
extra = ["-partition_cyl_align", "off", "-partition_offset", "16", "--mbr-force-bootable",
         "-append_partition", "2", "0xef", efi, "-appended_part_as_gpt",
         "-iso_mbr_part_type", "a2a0d0eb-e5b9-3344-87c0-68b6b72699c7",
         "-eltorito-alt-boot", "-e", "--interval:appended_partition_2:all::", "-no-emul-boot"]
salida[corte:corte] = extra
if os.environ.get("XORRISO_PARTICION_DEBUG"):
    print(" ".join(salida), file=sys.stderr)
os.execvp("xorriso", ["xorriso"] + salida)
