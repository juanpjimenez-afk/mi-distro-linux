#!/usr/bin/env bash
# Infinity OS — construye la ISO "en vivo" (arranca desde USB/VM sin instalar).
#
#   sudo ./build/build.sh          # todo: sistema + ISO
#   sudo ./build/build.sh rootfs   # solo el sistema de archivos (lo lento, 10-15 min)
#   sudo ./build/build.sh iso      # solo empaquetar la ISO (reutiliza el sistema ya hecho)
#   sudo ./build/build.sh clean    # borra work/ y out/
#
# Requiere Ubuntu/Debian con:  debootstrap squashfs-tools xorriso mtools
#                              grub-pc-bin grub-efi-amd64-bin rsync
# (en Windows 10: no necesitas nada de esto; usa GitHub Actions, ver README.md)
set -Eeuo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=build/config.env
source "$ROOT/build/config.env"
MIRROR=${MIRROR_OVERRIDE:-$MIRROR}
VERSION=$(<"$ROOT/VERSION")
WORK=${WORK:-$ROOT/work}
OUT=${OUT:-$ROOT/out}
ROOTFS=$WORK/rootfs
ISO_DIR=$WORK/iso
ISO=$OUT/infinity-os-$VERSION-amd64.iso

log() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\033[1;31merror:\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "ejecuta con sudo:  sudo $0 ${*:-}"

need() {
  local falta=()
  for c in "$@"; do command -v "$c" >/dev/null || falta+=("$c"); done
  ((${#falta[@]} == 0)) || die "faltan herramientas: ${falta[*]}  (sudo apt install debootstrap squashfs-tools xorriso mtools grub-pc-bin grub-efi-amd64-bin rsync)"
}

# ---------------------------------------------------------------- montajes
mount_chroot() {
  mkdir -p "$ROOTFS"/{dev/pts,dev/shm,proc,sys,run}
  mountpoint -q "$ROOTFS/dev" || mount --bind /dev "$ROOTFS/dev"
  mountpoint -q "$ROOTFS/dev/pts" || mount -t devpts devpts "$ROOTFS/dev/pts"
  mountpoint -q "$ROOTFS/dev/shm" || mount -t tmpfs -o mode=1777 tmpfs "$ROOTFS/dev/shm"
  mountpoint -q "$ROOTFS/proc" || mount -t proc proc "$ROOTFS/proc"
  mountpoint -q "$ROOTFS/sys" || mount -t sysfs sysfs "$ROOTFS/sys"
  mountpoint -q "$ROOTFS/run" || mount -t tmpfs tmpfs "$ROOTFS/run"
  # DNS dentro del chroot
  rm -f "$ROOTFS/etc/resolv.conf"
  cp -L /etc/resolv.conf "$ROOTFS/etc/resolv.conf" 2>/dev/null || echo "nameserver 1.1.1.1" >"$ROOTFS/etc/resolv.conf"
}

umount_chroot() {
  # Orden inverso; "-l" por si algún proceso hijo aún lo usa.
  for m in run sys proc dev/shm dev/pts dev; do
    mountpoint -q "$ROOTFS/$m" 2>/dev/null && umount -l "$ROOTFS/$m" || true
  done
}
# Si la instalacion falla a medias, tambien se quita policy-rc.d (si no, queda en el sistema).
trap 'umount_chroot; rm -f "$ROOTFS/usr/sbin/policy-rc.d"' EXIT

in_chroot() {
  chroot "$ROOTFS" /usr/bin/env -i \
    HOME=/root TERM="${TERM:-xterm}" LANG=C.UTF-8 DEBIAN_FRONTEND=noninteractive \
    PATH=/usr/sbin:/usr/bin:/sbin:/bin \
    ${http_proxy:+http_proxy="$http_proxy"} \
    "$@"
}

# ---------------------------------------------------------------- plantillas
# Sustituye @VARIABLES@ de config.env en los archivos del overlay recién copiados.
aplicar_plantillas() {
  local dir=$1 raiz=$2
  local -a archivos
  mapfile -t archivos < <(cd "$dir" && find . -type f | sed 's|^\./||')
  for f in "${archivos[@]}"; do
    local destino="$raiz/$f"
    [[ -f $destino ]] && grep -q '@[A-Z_]\+@' "$destino" 2>/dev/null || continue
    sed -i \
      -e "s|@OS_NAME@|$OS_NAME|g" \
      -e "s|@VERSION@|$VERSION|g" \
      -e "s|@SUITE@|$SUITE|g" \
      -e "s|@HOSTNAME@|$HOSTNAME_LIVE|g" \
      -e "s|@LIVE_USER@|$LIVE_USER|g" \
      -e "s|@LOCALE@|$LOCALE|g" \
      -e "s|@KB_LAYOUT@|$KB_LAYOUT|g" \
      -e "s|@TIMEZONE@|$TIMEZONE|g" \
      "$destino"
  done
}

# ---------------------------------------------------------------- etapa 1
etapa_rootfs() {
  need debootstrap rsync
  local firma
  # La firma cubre todo lo que cambia el resultado de instalar paquetes: la lista, la configuración,
  # el script del chroot y los ficheros de apt de overlay-pre. Si cambia cualquiera, se reinstala.
  firma=$(cat "$ROOT/build/packages.list" "$ROOT/build/config.env" "$ROOT/build/chroot-setup.sh" \
    $(find "$ROOT/overlay-pre" -type f | sort) | sha256sum | cut -c1-16)

  if [[ -f $WORK/.paquetes-$firma ]]; then
    log "Paquetes ya instalados (sin cambios en packages.list/config.env)"
  else
    # Marcador de base completa (os-release no sirve: aparece aunque debootstrap se interrumpa)
    if [[ -f $WORK/.base-ok && -f $ROOTFS/etc/os-release ]]; then
      log "Sistema base existente: solo se actualizan los paquetes"
    else
      log "Creando el sistema base Ubuntu $SUITE (debootstrap)"
      umount_chroot
      rm -rf "$ROOTFS"
      mkdir -p "$WORK"
      [[ -e /usr/share/debootstrap/scripts/$SUITE ]] ||
        ln -s gutsy "/usr/share/debootstrap/scripts/$SUITE"
      debootstrap --variant=minbase --arch=amd64 --components=main,universe,multiverse \
        "$SUITE" "$ROOTFS" "$MIRROR"

      log "Configurando apt y dpkg (instalación mínima)"
      rsync -a "$ROOT/overlay-pre/" "$ROOTFS/"
      rm -f "$ROOTFS/etc/apt/sources.list"
      touch "$WORK/.base-ok"
    fi
    rm -f "$WORK"/.paquetes-*
    # Que no arranquen servicios dentro del chroot mientras se instala
    printf '#!/bin/sh\nexit 101\n' >"$ROOTFS/usr/sbin/policy-rc.d"
    chmod +x "$ROOTFS/usr/sbin/policy-rc.d"

    # Fuentes de apt en CADA instalación de paquetes, no solo al crear la base: así un work/ ya
    # existente también recibe componentes nuevos (multiverse, para Steam).
    cat >"$ROOTFS/etc/apt/sources.list.d/ubuntu.sources" <<EOF
Types: deb
URIs: $MIRROR
Suites: $SUITE $SUITE-updates $SUITE-security
Components: main universe multiverse
Signed-By: /usr/share/keyrings/ubuntu-archive-keyring.gpg
EOF

    mount_chroot
    mkdir -p "$ROOTFS/tmp/infinity"
    grep -v '^\s*#' "$ROOT/build/packages.list" | sed 's/#.*//' | awk 'NF{print $1}' \
      >"$ROOTFS/tmp/infinity/packages"
    install -m755 "$ROOT/build/chroot-setup.sh" "$ROOTFS/tmp/infinity/chroot-setup.sh"

    log "Instalando paquetes (esto tarda unos minutos)"
    in_chroot /tmp/infinity/chroot-setup.sh packages
    umount_chroot
    touch "$WORK/.paquetes-$firma"
  fi

  etapa_configurar
}

# ---------------------------------------------------------------- etapa 1b
etapa_configurar() {
  log "Aplicando configuración, shell Infinity y marca"
  umount_chroot
  rsync -a --chown=root:root "$ROOT/overlay/" "$ROOTFS/"
  aplicar_plantillas "$ROOT/overlay" "$ROOTFS"

  # Clave de Gemini para pAnkI (opcional). Solo si el build recibe GEMINI_API_KEY (en GitHub, el secreto del
  # repositorio del mismo nombre): se escribe en /etc/skel, así cada usuario nuevo la tiene sin pegarla. La
  # clave queda DENTRO de la ISO: esa ISO no debe compartirse. Nunca se imprime ni se guarda en el repositorio.
  if [ -n "${GEMINI_API_KEY:-}" ]; then
    mkdir -p "$ROOTFS/etc/skel/.config/infinity/panki"
    chmod 700 "$ROOTFS/etc/skel/.config/infinity" "$ROOTFS/etc/skel/.config/infinity/panki"
    ( umask 077; printf '%s\n' "$GEMINI_API_KEY" > "$ROOTFS/etc/skel/.config/infinity/panki/clave" )
    log "pAnkI: clave de Gemini incluida en la ISO (no la compartas)"
  fi

  # Shell (dock + orbe + buscador)
  # Solo se borra el código de la shell: el overlay ya trajo los módulos de juegos a usr/lib/infinity
  rm -rf "$ROOTFS/usr/lib/infinity/infinity_shell"
  mkdir -p "$ROOTFS/usr/lib/infinity" "$ROOTFS/usr/share/infinity"
  rsync -a --exclude '__pycache__' --exclude 'tests' "$ROOT/shell/infinity_shell" "$ROOTFS/usr/lib/infinity/"
  install -m755 "$ROOT/shell/infinity-shell" "$ROOTFS/usr/bin/infinity-shell"
  # Página web de pAnkI (el motor: tareas, agenda, notas y chat), que panki_app.py aloja en una WebKit.WebView
  mkdir -p "$ROOTFS/usr/share/infinity/panki"
  install -m644 "$ROOT/shell/infinity_shell/panki_web/app.html" "$ROOTFS/usr/share/infinity/panki/app.html"
  # Marca
  install -m644 "$ROOT/branding/logo-white.svg" "$ROOT/branding/logo.svg" "$ROOT/branding/wallpaper.jpg" \
    "$ROOT/branding/logo-white-256.png" "$ROOT/branding/gear-white.png" \
    "$ROOT/branding/game-optimizer.png" "$ROOT/branding/panki.png" "$ROOT/branding/panki-white.svg" \
    "$ROOTFS/usr/share/infinity/"
  mkdir -p "$ROOTFS/usr/share/infinity/lock/fondos"
  install -m644 "$ROOT"/branding/fondos-bloqueo/*.jpg "$ROOTFS/usr/share/infinity/lock/fondos/"
  # Catálogo de fondos para elegir en Ajustes: /usr/share/infinity/fondos/fondos.json y una carpeta por fondo.
  # Lo de arriba (wallpaper.jpg y fondos/wallpaper-lock-*.jpg) se conserva: es el respaldo si falta el catálogo.
  if [[ -f $ROOT/branding/fondos/fondos.json ]]; then
    mkdir -p "$ROOTFS/usr/share/infinity/fondos"
    rsync -a --delete --prune-empty-dirs --chmod=D755,F644 --chown=root:root \
      --include='*/' --include='fondos.json' --include='*.jpg' --exclude='*' \
      "$ROOT/branding/fondos/" "$ROOTFS/usr/share/infinity/fondos/"
  else
    printf '\033[1;33maviso:\033[0m falta branding/fondos/fondos.json: la ISO solo traerá el fondo antiguo\n' >&2
  fi
  install -m644 "$ROOT/branding/logo-white.svg" "$ROOTFS/usr/share/pixmaps/infinity.svg"

  # Los .desktop que trae la imagen de fábrica (uno por línea). La biblioteca de apps (shell/infinity_shell/biblioteca.py)
  # la usa para distinguir lo del sistema de lo «descargado»: lo que se instale después con apt, gdebi o la Tienda
  # (Chrome, Antigravity, un .deb…) también cae en /usr/share/applications, y sin esta lista parecería del sistema.
  # Debe generarse DESPUÉS de instalar los paquetes y de copiar el overlay: es una foto de cómo queda la imagen.
  ( cd "$ROOTFS/usr/share/applications" && find . -maxdepth 1 -name '*.desktop' -printf '%f\n' | LC_ALL=C sort ) \
    >"$ROOTFS/usr/share/infinity/apps-base.txt"
  chmod 644 "$ROOTFS/usr/share/infinity/apps-base.txt"
  [[ -s $ROOTFS/usr/share/infinity/apps-base.txt ]] || die "la lista de apps de fábrica (apps-base.txt) salió vacía"

  mount_chroot
  mkdir -p "$ROOTFS/tmp/infinity"
  install -m755 "$ROOT/build/chroot-setup.sh" "$ROOTFS/tmp/infinity/chroot-setup.sh"
  cat >"$ROOTFS/tmp/infinity/env" <<EOF
LOCALE='$LOCALE'
TIMEZONE='$TIMEZONE'
HOSTNAME_LIVE='$HOSTNAME_LIVE'
LIVE_USER='$LIVE_USER'
KB_LAYOUT='$KB_LAYOUT'
EOF
  in_chroot /tmp/infinity/chroot-setup.sh finalize
  umount_chroot
  rm -rf "$ROOTFS/tmp/infinity"
}

# ---------------------------------------------------------------- etapa 2
etapa_iso() {
  compgen -G "$WORK/.paquetes-*" >/dev/null ||
    die "el sistema no esta completo (falta la instalacion de paquetes); ejecuta primero: $0 rootfs"
  need mksquashfs grub-mkrescue xorriso mformat python3
  [[ -d $ROOTFS/etc ]] || die "no hay sistema construido; ejecuta primero: $0 rootfs"

  log "Preparando la estructura de la ISO"
  rm -rf "$ISO_DIR"
  mkdir -p "$ISO_DIR"/{casper,boot/grub,.disk} "$OUT"

  local kernel initrd
  kernel=$(ls -1 "$ROOTFS"/boot/vmlinuz-* | sort -V | tail -1)
  initrd=$(ls -1 "$ROOTFS"/boot/initrd.img-* | sort -V | tail -1)
  [[ -f $kernel && -f $initrd ]] || die "no encuentro kernel/initrd en $ROOTFS/boot"
  cp "$kernel" "$ISO_DIR/casper/vmlinuz"
  cp "$initrd" "$ISO_DIR/casper/initrd"

  log "Comprimiendo el sistema (squashfs + zstd)"
  # zstd nivel 19: ~10 % menos en binarios que el 15, sin perder velocidad al descomprimir
  # (build unas 3 veces más lento: unos 3 min más en el sistema completo, estimado).
  mksquashfs "$ROOTFS" "$ISO_DIR/casper/filesystem.squashfs" \
    -noappend -comp zstd -Xcompression-level 19 -b 1M -no-recovery -quiet -wildcards \
    -e 'boot/*' 'tmp/*' 'var/cache/apt/*' 'var/lib/apt/lists/*' 'root/.cache'
  chroot "$ROOTFS" dpkg-query -W --showformat='${Package} ${Version}\n' >"$ISO_DIR/casper/filesystem.manifest"
  du -sx --block-size=1 "$ROOTFS" | cut -f1 >"$ISO_DIR/casper/filesystem.size"

  echo "$OS_NAME $VERSION \"$SUITE\" - amd64 ($(date +%Y%m%d))" >"$ISO_DIR/.disk/info"

  install -m644 "$ROOT/branding/grub-background.png" "$ISO_DIR/boot/grub/background.png"
  sed -e "s|@OS_NAME@|$OS_NAME|g" -e "s|@VERSION@|$VERSION|g" \
    "$ROOT/build/grub.cfg" >"$ISO_DIR/boot/grub/grub.cfg"

  log "Generando la ISO híbrida (BIOS + UEFI)"
  rm -f "$ISO"
  # build/xorriso-particion.py reescribe la orden de xorriso para que el sistema ISO quede DENTRO de una
  # partición (como las ISO de Ubuntu). Sin eso casper monta el disco entero y, con el guardado de cambios
  # (arranque «persistent»), no puede montar la partición «writable» del mismo USB («Device or resource busy»).
  grub-mkrescue --xorriso="$ROOT/build/xorriso-particion.py" -o "$ISO" "$ISO_DIR" \
    --product-name "$OS_NAME" --product-version "$VERSION" \
    -- -volid "INFINITY_OS" >"$WORK/grub-mkrescue.log" 2>&1 || {
    tail -20 "$WORK/grub-mkrescue.log"
    die "falló grub-mkrescue"
  }
  (cd "$OUT" && sha256sum "$(basename "$ISO")" >"$(basename "$ISO").sha256")
  log "Listo: $ISO ($(du -h "$ISO" | cut -f1))"
}

case "${1:-all}" in
  rootfs) etapa_rootfs ;;
  iso) etapa_iso ;;
  all) etapa_rootfs; etapa_iso ;;
  clean) umount_chroot; rm -rf "$WORK" "$OUT"; echo "limpio" ;;
  *) die "uso: $0 [all|rootfs|iso|clean]" ;;
esac
