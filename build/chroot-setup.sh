#!/usr/bin/env bash
# Se ejecuta DENTRO del chroot (lo lanza build.sh). No lo ejecutes en tu equipo.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

paquetes() {
  apt-get update
  apt-get -y -o Dpkg::Options::=--force-confold dist-upgrade
  # shellcheck disable=SC2046
  apt-get -y -o Dpkg::Options::=--force-confold install --no-install-recommends \
    $(</tmp/infinity/packages)
}

limpiar() {
  apt-get -y autoremove --purge || true
  apt-get clean
  rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /var/cache/ldconfig/aux-cache \
    /var/log/*.log /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
    /root/.cache /root/.bash_history /tmp/* 2>/dev/null || true
  rm -f /usr/sbin/policy-rc.d
  # Cada arranque genera su propio machine-id
  : >/etc/machine-id
  rm -f /var/lib/dbus/machine-id
  ln -sf ../run/systemd/resolve/stub-resolv.conf /etc/resolv.conf
}

finalizar() {
  # shellcheck disable=SC1091
  source /tmp/infinity/env

  # Idioma y zona horaria
  sed -i -E "s/^# *(en_US.UTF-8|$LOCALE) /\1 /" /etc/locale.gen
  grep -qE "^$LOCALE " /etc/locale.gen || echo "$LOCALE UTF-8" >>/etc/locale.gen
  locale-gen
  update-locale LANG="$LOCALE" LANGUAGE="${LOCALE%%.*}" LC_MESSAGES="$LOCALE"
  echo "$TIMEZONE" >/etc/timezone
  ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime

  # Nombre del equipo
  echo "$HOSTNAME_LIVE" >/etc/hostname
  printf '127.0.0.1 localhost\n127.0.1.1 %s\n::1 localhost ip6-localhost ip6-loopback\n' \
    "$HOSTNAME_LIVE" >/etc/hosts

  # Carpetas del usuario (casper copia /etc/skel al crear el usuario)
  mkdir -p /etc/skel/{Escritorio,Descargas,Documentos,Música,Imágenes,Vídeos}

  # Cursores: solo el tema que usamos (cada variante pesa ~30 MB)
  rm -rf /usr/share/icons/Bibata-Original-* /usr/share/icons/Bibata-Modern-Amber /usr/share/icons/Bibata-Modern-Ice

  # Esquemas de GTK (tema oscuro, iconos, fuente) y caché de iconos
  glib-compile-schemas /usr/share/glib-2.0/schemas
  gtk-update-icon-cache -q -f /usr/share/icons/Papirus-Dark 2>/dev/null || true
  fc-cache -f >/dev/null 2>&1 || true

  # Servicios: solo lo necesario
  systemctl enable greetd.service NetworkManager.service systemd-resolved.service \
    earlyoom.service fstrim.timer 2>&1 | grep -v '^Created symlink' || true
  systemctl enable open-vm-tools.service qemu-guest-agent.service 2>/dev/null || true
  systemctl enable infinity-live-groups.service 2>&1 | grep -v '^Created symlink' || true
  systemctl set-default graphical.target
  # Lo siguiente solo retrasa el arranque o consume recursos sin aportar a un escritorio
  systemctl mask apt-daily.timer apt-daily-upgrade.timer apt-daily.service apt-daily-upgrade.service \
    NetworkManager-wait-online.service systemd-networkd-wait-online.service \
    man-db.timer e2scrub_all.timer motd-news.timer 2>&1 | grep -v '^Created symlink' || true

  # Initramfs a medida: zstd (descompresión rápida)
  sed -i 's/^COMPRESS=.*/COMPRESS=zstd/' /etc/initramfs-tools/initramfs.conf
  update-initramfs -c -k all

  limpiar
}

case "${1:-}" in
  packages) paquetes ;;
  finalize) finalizar ;;
  *) echo "uso: $0 packages|finalize" >&2; exit 2 ;;
esac
