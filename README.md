# Infinity OS ∞

Un sistema operativo **basado en Linux** (Ubuntu 26.04 LTS), minimalista y ligero, con interfaz de
**cristal líquido** ("liquid glass"): paneles translúcidos con el fondo desenfocado, brillo en los bordes
y un **logo ∞ flotante, separado de la barra de tareas**, desde el que buscas cualquier cosa y apagas el equipo.

<p align="center">
  <img src="docs/escritorio.png" alt="Escritorio de Infinity OS" width="49%">
  <img src="docs/buscador.png" alt="Buscador del orbe" width="49%">
</p>

| Lo que pediste | Cómo está resuelto |
|---|---|
| 1. Optimizado | Instalación mínima (sin snap, sin servicios inútiles), swap comprimido en RAM, apagado rápido, shell escrito para no consumir CPU en reposo. [Detalles](#optimización) |
| 2. Minimalista + *liquid glass* | Compositor Hyprland (desenfoque real) + un shell propio con estilo de cristal. |
| 3. Logo separado de la barra, con búsqueda y apagado | El **orbe ∞** vive aparte, a la izquierda. Al pulsarlo (o con `Super + Espacio`) se abre un panel con buscador y botones de energía. |
| 4. Logo = tu imagen | Tu imagen, vectorizada (`branding/logo.svg`, fidelidad ≈ 98,5 %). |

---

## 1. Conseguir la ISO (desde Windows 10, sin instalar nada)

La ISO se construye **en la nube de GitHub**; tú solo la descargas.

1. Entra a este repositorio en GitHub → pestaña **Actions**.
2. En la izquierda elige **Construir ISO de Infinity OS** → botón **Run workflow** → **Run workflow**.
3. Espera unos 15–25 minutos (círculo amarillo → check verde).
4. Abre la ejecución terminada y baja **infinity-os-iso** (sección *Artifacts*, abajo). Es un `.zip` con la ISO dentro.

> Para una versión "oficial" (la ISO queda en la pestaña **Releases**, sin zip): crea una etiqueta
> con la versión, por ejemplo `v0.1.0`.
>
> Si el repositorio es privado y los *Artifacts* te dan error de cuota, usa la vía de las etiquetas
> (las *Releases* no cuentan para esa cuota).

Comprueba que la descarga no se dañó: el archivo `.sha256` trae la huella. En PowerShell:
`Get-FileHash .\infinity-os-0.1.0-amd64.iso -Algorithm SHA256`.

## 2. Probarla en una máquina virtual (recomendado)

Probarla en una VM es lo más seguro: no toca tu Windows ni tus discos.

**VMware Workstation Player** (gratis para uso personal) — *la opción más probable de funcionar bien*
1. *Create a New Virtual Machine* → *Installer disc image file* → elige la ISO.
2. Sistema: **Linux → Ubuntu 64-bit**. Memoria: **4 GB**. Procesadores: **2 o más**.
3. Antes de iniciar: *Customize Hardware → Display → **Accelerate 3D graphics*** ✔ (y memoria gráfica al máximo).

**VirtualBox**
1. *Nueva* → Tipo **Linux**, versión **Ubuntu (64-bit)**. Memoria **4096 MB**, CPUs **2+**.
2. *Configuración → Pantalla*: controlador **VMSVGA**, memoria de vídeo **128 MB**, **Habilitar aceleración 3D** ✔.
3. *Almacenamiento*: inserta la ISO en la unidad óptica.

Al arrancar verás el menú de Infinity OS:

- **Infinity OS** — normal.
- **gráficos seguros** — úsalo si la pantalla sale negra o el escritorio no aparece (típico en VM sin 3D).
  Desactiva desenfoque y animaciones y renderiza por software.
- **gráficos seguros (nomodeset)** — último recurso.
- **diagnóstico** — muestra los mensajes del arranque.

> El sistema **también reintenta solo** en modo seguro si Hyprland falla en los primeros segundos.
> Si algo falla, el registro de la sesión queda en `~/.local/state/infinity/sesion.log`.

Para llevarla a un equipo real (no VM): grábala en un USB con [Rufus](https://rufus.ie) en modo
**"Imagen DD"** o con [Ventoy](https://www.ventoy.net). Ojo: **aún no tiene instalador** (ver
[Estado](#estado-y-limitaciones)) y no arranca con *Secure Boot* activado.

## 3. Usarla

| Atajo | Acción |
|---|---|
| `Super + Espacio` o clic en el **orbe ∞** | Buscador (apps, calculadora, `> comando`, web) + apagar/reiniciar |
| `Super + Enter` | Terminal (cristal translúcido) |
| `Super + E` / `Super + B` | Archivos / Navegador |
| `Super + Q` | Cerrar ventana |
| `Super + F` / `Super + V` | Pantalla completa / ventana flotante |
| `Super + ←↑↓→` | Mover el foco · con `Shift`, mover la ventana |
| `Super + 1…5` | Escritorios · con `Shift`, enviar ventana |
| `Super + L` | Bloquear |
| `Impr` / `Shift + Impr` | Captura de región al portapapeles / pantalla completa a `~/Imágenes` |
| `Super` + arrastrar (izq./der.) | Mover / redimensionar ventana |

**Buscador del orbe:** escribe para filtrar apps (sin importar tildes ni mayúsculas, acepta letras
sueltas: `ffx` → Firefox). `2+2*10` calcula; `> ls -la` ejecuta en una terminal; cualquier otra cosa se
busca en la web. Apagar, reiniciar y cerrar sesión **piden confirmación** (pulsa dos veces) para no
apagar por accidente. Clic derecho en un icono del dock: abrir ventana nueva, cerrar, fijar/quitar.

El teclado es **Latinoamérica** y la zona horaria **Ciudad de México** por defecto. Cámbialo en
[`build/config.env`](build/config.env) (hay ejemplos para España, Argentina, Colombia…) y vuelve a construir.

## Optimización

Qué se hizo para que sea ligera, y por qué:

- **Base mínima:** `debootstrap --variant=minbase` e instalación **sin "recomendados"**. El sistema es *exactamente*
  lo que dice [`build/packages.list`](build/packages.list), nada más. Sin `snap` (bloqueado a propósito: es lento
  de arrancar y gasta RAM), sin `cloud-init`, sin indexadores ni servicios de GNOME/KDE.
- **Sin pantalla de inicio de sesión pesada:** `greetd` (un demonio de ~1 MB) en lugar de GDM/SDDM.
- **Compresión de arranque rápida:** el sistema va en `squashfs` con **zstd** (descomprime mucho más rápido
  que xz) y el initramfs también en zstd.
- **Memoria:** swap **comprimido en RAM (zram + zstd)**, `earlyoom` (evita que el equipo se cuelgue al llenarse
  la RAM, protegiendo el escritorio) y ajustes de `sysctl` pensados para zram.
- **Arranque y apagado:** se enmascaran los temporizadores de apt, `man-db` y las esperas de red; los servicios
  que no responden se cortan a los **10 s** en lugar de 90 s.
- **Disco:** planificador de E/S según el tipo de disco (NVMe `none`, SSD `mq-deadline`, HDD `bfq`) y `fstrim`
  semanal. Registro del sistema solo en RAM y limitado a 64 MB.
- **Escritorio:** Hyprland con `vfr` (solo redibuja si algo cambió → casi 0 % de CPU/GPU en reposo). El shell
  (dock, orbe, buscador) es **un solo proceso** que no sondea nada: se entera de las ventanas por el socket de
  Hyprland, del reloj por un temporizador alineado al minuto y de la red por señales del sistema. El buscador
  se crea la primera vez que lo abres.
- **Documentación y traducciones** que no usas (todo menos español/inglés) no se instalan.

TODO_MEDICIONES

## Estructura del repositorio

```
build/            construcción de la ISO
  build.sh          script principal (debootstrap → squashfs → ISO híbrida BIOS+UEFI)
  chroot-setup.sh   lo que se ejecuta dentro del sistema en construcción
  packages.list     ← los paquetes del sistema (edítalo)
  config.env        ← idioma, teclado, zona horaria, nombre (edítalo)
  grub.cfg          menú de arranque
overlay/          archivos que se copian tal cual al sistema (/etc, /usr…)
  etc/skel/.config/hypr/hyprland.conf   ← el aspecto "cristal" y los atajos
overlay-pre/      configuración de apt/dpkg que debe existir ANTES de instalar paquetes
shell/            el shell propio: orbe, dock, estado y buscador (Python + GTK4)
  infinity_shell/style.css              ← el estilo de cristal de las piezas
  tests/                                 pruebas automáticas (incluye un Hyprland simulado)
branding/         logo (SVG), fondo de pantalla, imagen del menú de arranque
.github/workflows/build-iso.yml   construye la ISO en GitHub
```

## Personalizar

| Quiero… | Edito… |
|---|---|
| Cambiar idioma, teclado, zona horaria | `build/config.env` |
| Añadir o quitar programas | `build/packages.list` |
| Cambiar colores, desenfoque, animaciones, atajos | `overlay/etc/skel/.config/hypr/hyprland.conf` |
| Cambiar el aspecto de dock/orbe/buscador | `shell/infinity_shell/style.css` |
| Apps fijadas en el dock | `overlay/etc/infinity/fijados` (o clic derecho en el dock) |
| Fondo de pantalla | `branding/make_assets.py` (genera `wallpaper.jpg`) |
| Cambiar el nombre del sistema | `OS_NAME` en `build/config.env` |

Después de cambiar algo, sube los cambios a GitHub y vuelve a ejecutar el workflow
(también se ejecuta solo al subir cambios a `main`).

**Construir en tu propio Linux** (opcional): `sudo apt install debootstrap squashfs-tools xorriso mtools grub-pc-bin grub-efi-amd64-bin rsync`
y luego `sudo ./build/build.sh`. La ISO queda en `out/`.

## Estado y limitaciones

Esto es la **versión 0.1**: una ISO "en vivo" (arranca sin instalar, lo que cambies se pierde al apagar).

**Verificado** (construyendo y arrancando la ISO en QEMU desde cero):
- La ISO se construye de principio a fin y arranca (GRUB → kernel → casper → creación del usuario → `greetd`
  → Hyprland → shell).
- El orbe, el dock, el buscador y el atajo `Super + Espacio` funcionan sobre el Hyprland real.
- Las pruebas automáticas (búsqueda, calculadora, conexión con Hyprland) pasan; las configuraciones se
  validan con las propias herramientas (`Hyprland --verify-config`, `foot --check-config`, `udevadm verify`).

**No verificado / límites conocidos:**
- No se pudo comprobar el **desenfoque real** con una GPU: las pruebas se hicieron con render por software.
  La configuración es válida y los paneles están pensados para él, pero el aspecto exacto del cristal puede
  necesitar ajustes (`size`, `passes`, `vibrancy` en `hyprland.conf`).
- **Sin instalador a disco** todavía.
- **Sin Secure Boot** (desactívalo en la BIOS/UEFI o usa una VM).
- Hyprland en **VirtualBox** puede dar problemas según la versión; **VMware** y **QEMU/virt-manager** suelen ir mejor.
- La red WiFi se elige con `nmtui` (clic en el icono de red del panel de estado): aún no hay un selector gráfico.
- No es *exactamente* el "Liquid Glass" de Apple: un compositor normal no puede refractar la luz, así que se
  aproxima con desenfoque + translucidez + reflejo especular en el borde.

## Hoja de ruta

1. Instalador a disco (Calamares).
2. Panel rápido (WiFi, Bluetooth, brillo, volumen) y centro de notificaciones.
3. Pantalla de arranque (Plymouth) con el logo ∞ animado.
4. Aplicación de ajustes y tema de iconos propio.
5. Soporte de Secure Boot y Flatpak.
