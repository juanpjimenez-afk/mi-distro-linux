#!/usr/bin/env bash
# Se ejecuta DENTRO del chroot (lo lanza build.sh). No lo ejecutes en tu equipo.
set -Eeuo pipefail
export DEBIAN_FRONTEND=noninteractive

paquetes() {
  # Arquitectura de 32 bits: muchos juegos (y Steam) siguen necesitando librerías i386 (Vulkan, GL).
  # Se añade antes del primer "apt-get update" para que los índices incluyan i386.
  dpkg --add-architecture i386
  apt-get update
  # Si una ejecución anterior se cortó a mitad, dpkg queda interrumpido: se repara antes de seguir
  dpkg --configure -a
  apt-get -y -f install
  apt-get -y -o Dpkg::Options::=--force-confold dist-upgrade
  # shellcheck disable=SC2046
  apt-get -y -o Dpkg::Options::=--force-confold install --no-install-recommends \
    $(</tmp/infinity/packages)
}

# Apps de terceros desde los repositorios de sus fabricantes: Google Chrome (lo usan las WebApps y trae Widevine,
# que Netflix/HBO Max/Disney+ necesitan) y Google Antigravity. Si un repositorio no responde, se avisa y se sigue:
# la ISO sale igual, sin esa app.
tercero() {  # nombre  url-de-la-clave  archivo-de-clave  linea-de-fuente  paquete
  local nombre=$1 clave=$2 archivo=$3 fuente=$4 paquete=$5
  if curl -fsSL --retry 3 --max-time 60 "$clave" | gpg --dearmor >"$archivo.tmp" && [ -s "$archivo.tmp" ]; then
    mv "$archivo.tmp" "$archivo"; chmod 644 "$archivo"
    printf '%s\n' "$fuente" >"/etc/apt/sources.list.d/$nombre.sources"
    if apt-get update && apt-get -y --no-install-recommends install "$paquete"; then
      echo "[terceros] $nombre instalado"; return 0
    fi
  fi
  rm -f "$archivo.tmp" "$archivo" "/etc/apt/sources.list.d/$nombre.sources"
  echo "[terceros] AVISO: no se pudo instalar $nombre; la ISO sale sin él" >&2
}

terceros() {
  mkdir -p /etc/apt/keyrings
  tercero google-chrome https://dl.google.com/linux/linux_signing_key.pub /usr/share/keyrings/google-chrome.gpg \
"Types: deb
URIs: https://dl.google.com/linux/chrome/deb/
Suites: stable
Components: main
Architectures: amd64
Signed-By: /usr/share/keyrings/google-chrome.gpg" google-chrome-stable
  tercero antigravity https://us-central1-apt.pkg.dev/doc/repo-signing-key.gpg /etc/apt/keyrings/antigravity-repo-key.gpg \
"Types: deb
URIs: https://us-central1-apt.pkg.dev/projects/antigravity-auto-updater-dev/
Suites: antigravity-debian
Components: main
Signed-By: /etc/apt/keyrings/antigravity-repo-key.gpg" antigravity
}

limpiar() {
  apt-get -y autoremove --purge || true
  apt-get clean
  rm -rf /var/lib/apt/lists/* /var/cache/apt/*.bin /var/cache/ldconfig/aux-cache \
    /var/log/*.log /var/log/apt /var/log/dpkg.log /var/log/alternatives.log \
    /root/.cache /root/.bash_history /tmp/* 2>/dev/null || true
  rm -f /usr/sbin/policy-rc.d
  rm -f /var/cache/debconf/*-old   # copias de seguridad de debconf (~3 MB)
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
  # Teclado de la consola y del login (tty7): la misma distribución que el escritorio
  printf 'XKBMODEL="pc105"\nXKBLAYOUT="%s"\nXKBVARIANT=""\nXKBOPTIONS=""\nBACKSPACE="guess"\n' "$KB_LAYOUT" >/etc/default/keyboard
  ln -sf "/usr/share/zoneinfo/$TIMEZONE" /etc/localtime

  # El debootstrap instala la base ANTES de copiar overlay-pre, asi que su path-exclude no
  # llega a esos paquetes: aqui se quitan los idiomas y manuales que no usamos (es y en).
  find /usr/share/locale -mindepth 1 -maxdepth 1 -type d ! -name "en*" ! -name "es*" -exec rm -rf {} +
  find /usr/share/man -mindepth 1 -maxdepth 1 -type d ! -name "man[0-9]*" ! -name "es" -exec rm -rf {} +
  # /usr/share/i18n (fuentes de locales) NO se borra: la futura instalacion a disco (Calamares,
  # README) necesita generar locales. Pendiente de decision (unos 4,5 MiB comprimidos).

  # Nombre del equipo
  echo "$HOSTNAME_LIVE" >/etc/hostname
  printf '127.0.0.1 localhost\n127.0.1.1 %s\n::1 localhost ip6-localhost ip6-loopback\n' \
    "$HOSTNAME_LIVE" >/etc/hosts

  # Carpetas del usuario (casper copia /etc/skel al crear el usuario)
  mkdir -p /etc/skel/{Escritorio,Descargas,Documentos,Música,Imágenes,Vídeos}

  # Cursores: solo el tema que usamos (cada variante pesa ~30 MB)
  rm -rf /usr/share/icons/Bibata-Original-* /usr/share/icons/Bibata-Modern-Amber /usr/share/icons/Bibata-Modern-Ice
  # Solo se usa Papirus-Dark (GTK y dconf). Papirus-Light sobra; Papirus NO se borra: Papirus-Dark
  # enlaza sus tamaños a ../Papirus (sin ella quedan enlaces rotos).
  rm -rf /usr/share/icons/Papirus-Light
  # La interfaz usa solo la familia "Inter": Inter Display sobra
  rm -f /usr/share/fonts/opentype/inter/InterDisplay-*.otf

  # Esquemas de GTK (tema oscuro, iconos, fuente) y caché de iconos
  glib-compile-schemas /usr/share/glib-2.0/schemas
  gtk-update-icon-cache -q -f /usr/share/icons/Papirus-Dark 2>/dev/null || true
  fc-cache -f >/dev/null 2>&1 || true

  # Sesión de serie hyprland-uwsm: necesita uwsm, que no se instala, así que la entrada no funciona.
  # (hyprland.desktop, que salta infinity-session, queda pendiente: decisión de producto.)
  rm -f /usr/share/wayland-sessions/hyprland-uwsm.desktop
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
  packages) paquetes; terceros ;;
  finalize) finalizar ;;
  *) echo "uso: $0 packages|finalize" >&2; exit 2 ;;
esac
