# Infinity OS ∞

Un sistema operativo **basado en Linux** (Ubuntu 26.04 LTS), minimalista y ligero, con interfaz de
**cristal líquido** ("liquid glass"): paneles translúcidos con el fondo desenfocado, brillo en los bordes
y un **logo ∞ flotante, separado de la barra de tareas**, desde el que buscas cualquier cosa y apagas el equipo.

<p align="center">
  <img src="docs/escritorio.jpg" alt="Escritorio de Infinity OS" width="49%">
  <img src="docs/buscador.jpg" alt="Buscador del orbe sobre una terminal de cristal" width="49%">
</p>
<p align="center"><sub>Capturas reales del sistema arrancado en una máquina virtual (QEMU, sin GPU).</sub></p>

| Lo que pediste | Cómo está resuelto |
|---|---|
| 1. Optimizado | Instalación mínima (sin snap ni servicios inútiles), swap comprimido en RAM, apagado rápido, un shell que no sondea nada. ISO de **1,09 GB**. [Detalles](#optimización) |
| 2. Minimalista + *liquid glass* | Compositor Hyprland (desenfoque real) + un shell propio con estilo de cristal. |
| 3. Logo separado de la barra, con búsqueda y apagado | El **orbe ∞** vive aparte, a la izquierda. Al pulsarlo (o con `Super + Espacio`) se abre un panel con buscador y botones de energía. |
| 4. Logo = tu imagen | Tu imagen, vectorizada (`branding/logo.svg`, error de trazado ≈ 1,5 %). |

---

## 1. Conseguir la ISO (desde Windows 10, sin instalar nada)

La ISO se construye **en la nube de GitHub**; tú solo la descargas.

1. Entra a este repositorio en GitHub → pestaña **Actions**.
   (Si no ves el flujo, asegúrate de que este código esté en la rama `main`.)
2. En la izquierda elige **Construir ISO de Infinity OS** → botón **Run workflow** → **Run workflow**.
3. Espera unos 10–25 minutos (círculo amarillo → check verde).
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

**VMware Workstation Player** (gratis para uso personal)
1. *Create a New Virtual Machine* → *Installer disc image file* → elige la ISO.
2. Sistema: **Linux → Ubuntu 64-bit**. Memoria: **4 GB**. Procesadores: **2 o más**.
3. Antes de iniciar: *Customize Hardware → Display → **Accelerate 3D graphics*** ✔.

**VirtualBox**
1. *Nueva* → Tipo **Linux**, versión **Ubuntu (64-bit)**. Memoria **4096 MB**, CPUs **2+**.
2. *Configuración → Pantalla*: controlador **VMSVGA**, memoria de vídeo **128 MB**, **Habilitar aceleración 3D** ✔.
3. *Almacenamiento*: inserta la ISO en la unidad óptica.

> Estas dos rutas son las recomendadas por cómo funcionan Hyprland y las VM, **pero no pude probarlas
> aquí** (todas mis pruebas fueron con QEMU). Si algo falla, usa "gráficos seguros" (abajo) y cuéntame qué pasó.

Al arrancar verás el menú de Infinity OS:

- **Infinity OS** — normal.
- **gráficos seguros** — úsalo si la pantalla sale negra o el escritorio no aparece (típico en VM sin 3D).
  Desactiva desenfoque y animaciones y renderiza por software.
- **gráficos seguros (nomodeset)** — último recurso.
- **diagnóstico** — muestra los mensajes del arranque.

El sistema **se recupera solo** de fallos: si Hyprland se cae nada más arrancar, reintenta en modo seguro; si se
cae con la sesión iniciada, vuelve al escritorio (hasta 3 veces). El registro queda en
`~/.local/state/infinity/sesion.log`.

En modo en vivo el usuario es `infinity`, **sin contraseña** (en la pantalla de bloqueo basta pulsar Enter) y con
`sudo` sin contraseña.

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
| `Super + L` | Bloquear la pantalla: hora gigante a la derecha (también se bloquea sola a los 10 min) |
| `Impr` / `Shift + Impr` | Captura de región al portapapeles / pantalla completa a `~/Imágenes` |
| `Super` + arrastrar (izq./der.) | Mover / redimensionar ventana |

> `Super` es la tecla con el logo de Windows.

**Buscador del orbe:** escribe para filtrar apps (sin importar tildes ni mayúsculas, acepta letras
sueltas: `ffx` → Firefox). `2+2*10` calcula; `> ls -la` ejecuta en una terminal; cualquier otra cosa se
busca en la web. Apagar, reiniciar y cerrar sesión **piden confirmación** (pulsa dos veces; también puedes
escribir "apagar" y pulsar Enter dos veces) para no apagar por accidente.
**Dock:** clic = abrir o ir a la ventana · clic central = ventana nueva · clic derecho = abrir ventana
nueva, cerrar, fijar/quitar del dock.

<p align="center"><img src="docs/bloqueo.jpg" alt="Pantalla de bloqueo: hora gigante a la derecha con la fecha debajo" width="70%"></p>

**Pantalla de bloqueo:** al bloquear (botón del orbe, `Super + L` o por inactividad) se ve la foto de referencia con la **hora en directo**, en dos líneas, gigante y a la derecha, con la fecha debajo. Toca una tecla o mueve el ratón para que aparezca la contraseña (se vuelve a esconder sola); en el modo en vivo no hay contraseña, basta pulsar Enter. La hora se adapta a la resolución de la pantalla (lee el tamaño con Hyprland). La foto de referencia traía la hora pintada: en `branding/` se quitó ese texto (`limpiar_foto_bloqueo.py`) y la hora se dibuja en directo con la fuente *Infinity Reloj* (`branding/make_fonts.py`), que es Inter comprimida en horizontal. Para cambiar el formato de la hora edita `time-format` en `~/.config/gtklock/config.ini` (por ejemplo `%H\n%M:%S`).

**Si el ratón va con lag (VirtualBox u otra VM):** en una VM sin aceleración 3D el escritorio se dibuja por CPU, y el fondo de pantalla es una superficie a pantalla completa que se redibuja en cada movimiento del ratón. Por eso el fondo se puede quitar con una línea (en mis pruebas, mover el ratón costaba unas 2,6 veces menos sin fondo):

`echo color > ~/.config/infinity/fondo`  (fondo sólido)  ·  `echo imagen > ~/.config/infinity/fondo`  (volver a la imagen)

Ajustes en VirtualBox que ayudan (no pude probar VirtualBox, así que son recomendaciones):
1. **Configuración → Sistema → Placa base → Dispositivo señalador: *USB Tablet*** (puntero absoluto, sin
   "capturar" el ratón). Es el ajuste que más suele notarse.
2. **Pantalla:** controlador *VMSVGA*, memoria de vídeo al máximo (128 MB) y **Habilitar aceleración 3D** ✔.
   Con 3D sin soporte real del host, el escritorio sigue dibujándose por CPU.
3. **Sistema:** 4 CPU y 6–8 GB de RAM (tu equipo tiene 16 GB). Un monitor virtual de 1280×720 o 1366×768
   cuesta menos por fotograma que uno de 1920×1080.
4. Si el equipo tiene Hyper-V o "Seguridad basada en virtualización" activados en Windows, VirtualBox funciona
   en un modo más lento; desactivarlo es una decisión tuya, con sus consecuencias.

Para saber qué renderizador usa el escritorio, en una terminal (`Super + Enter`):
`grep -m1 Renderer /run/user/$(id -u)/hypr/*/hyprland.log`. Si dice `llvmpipe`, es renderizado por CPU.

Viene configurado para **Chile**: idioma `es_CL`, teclado **Latinoamérica** (el teclado chileno estándar) y
zona horaria de **Santiago** (con cambio de horario de verano). Para otro país edita
[`build/config.env`](build/config.env) y vuelve a construir.

## Optimización

Qué se hizo para que sea ligera, y por qué:

- **Base mínima:** `debootstrap --variant=minbase` e instalación **sin "recomendados"**. El sistema es *exactamente*
  lo que dice [`build/packages.list`](build/packages.list), nada más. Sin `snap` (bloqueado a propósito: es lento
  de arrancar y gasta RAM), sin `cloud-init`, sin indexadores ni servicios de GNOME/KDE.
- **Sin pantalla de inicio de sesión pesada:** `greetd` + `tuigreet` en lugar de GDM/SDDM.
- **Compresión de arranque rápida:** el sistema va en `squashfs` con **zstd** (descomprime mucho más rápido
  que xz) y el initramfs también en zstd.
- **Menos peso muerto:** sin documentación ni traducciones que no uses (solo español/inglés), sin el firmware de
  servidores y móviles ARM (Mellanox, Qualcomm…), sin controladores Vulkan (Hyprland no los necesita) y con un
  solo tema de cursor. **Efecto medido: la ISO pasó de 1,38 GB a 1,09 GB (−21 %).**
- **Memoria:** swap **comprimido en RAM (zram + zstd)**, `earlyoom` (evita que el equipo se cuelgue al llenarse
  la RAM, protegiendo el escritorio) y ajustes de `sysctl` pensados para zram.
- **Arranque y apagado:** se enmascaran los temporizadores de apt, `man-db` y las esperas de red; los servicios
  que no responden se cortan a los **10 s** en lugar de 90 s; sin la pregunta de "quita el medio de instalación".
- **Disco:** planificador de E/S según el tipo de disco (NVMe `none`, SSD `mq-deadline`, HDD `bfq`) y `fstrim`
  semanal. Registro del sistema solo en RAM y limitado a 64 MB.
- **Escritorio:** Hyprland con `vfr` (solo redibuja cuando algo cambia). El shell (dock, orbe, buscador) es
  **un solo proceso** que no sondea nada: se entera de las ventanas por el socket de Hyprland, del reloj por un
  temporizador alineado al minuto y de la red por señales del sistema; el dock solo se redibuja si algo visible
  cambió y las consultas a Hyprland van en un hilo aparte para que la interfaz nunca se congele. Los atajos hablan
  con el shell por D-Bus (`gapplication`), sin arrancar Python. El buscador se crea la primera vez que lo abres.

### Mediciones

Sobre el sistema arrancado en QEMU con 4 GB de RAM y **sin GPU** (todo se dibuja por CPU):

| | |
|---|---|
| Tamaño de la ISO | 1,09 GB (`squashfs` de 0,99 GB) |
| Construcción desde cero (4 núcleos) | 5 min 18 s |
| RAM usada con el escritorio abierto y en reposo | **≈ 670 MB** de 3,9 GB (Hyprland ≈ 215 MB por el render de software; shell ≈ 52 MB) |
| Servicios fallidos al arrancar | 0 |
| Swap | zram de 1,9 GB, `swappiness` 133 |

El tiempo de arranque no es significativo aquí: QEMU emula la CPU sin aceleración (≈ 2 min hasta poder iniciar
sesión). En un equipo real será mucho menor, pero **no lo medí**. Tampoco medí consumo de CPU/batería en reposo.

## Cómo funciona (para curiosos)

```
GRUB (BIOS o UEFI) → kernel Linux → casper (monta la ISO y crea el usuario) → systemd
   → greetd (inicia sesión solo) → infinity-session → Hyprland (compositor)
        ├─ swaybg ........... el fondo de pantalla (la cascada, con menos brillo)
        └─ infinity-shell ... orbe ∞ + dock + estado (3 ventanas "capa") + buscador (oculto hasta que lo abres)
```

**El efecto cristal** son dos mitades que se complementan:
1. **Hyprland desenfoca lo que hay detrás** de las piezas del shell. Las reglas `layerrule` de `hyprland.conf` lo
   activan para todo lo que se llame `infinity-*`, y `ignore_alpha` evita desenfocar las esquinas transparentes.
2. **El CSS del shell dibuja el vidrio** (`style.css`): relleno translúcido, un reflejo diagonal, un borde de luz
   y un brillo especular en el canto superior.

<p align="center">
  <img src="docs/prueba-desenfoque.jpg" alt="Prueba del desenfoque: rayas nítidas de fondo, esmeriladas detrás de los paneles" width="70%">
  <br><sub>Prueba del desenfoque: con un fondo de rayas nítidas, detrás de cada panel de cristal las rayas desaparecen.</sub>
</p>

**Por qué el orbe está "separado":** cada pieza (orbe, dock, estado) es su propia ventana de tipo *layer-shell*,
anclada a un lugar de la pantalla. No hay una barra que las contenga: el orbe flota solo, justo como pediste.

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
branding/         logo (SVG), fotos de referencia, fondos, fuente de la hora e imagen del menú de arranque
docs/             capturas
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

**Verificado** (construyendo la ISO desde cero y arrancándola en QEMU, controlada desde fuera):
- Arranque completo por **BIOS**: GRUB → kernel → casper (crea el usuario) → `greetd` → Hyprland → shell.
- Arranque por **UEFI** (firmware OVMF): menú de GRUB con la marca, kernel y sesión gráfica (Hyprland y shell
  iniciados). No llegué a ver el dock dibujado en esa prueba concreta por la lentitud de la emulación.
- **Orbe ∞** (clic y `Super + Espacio`), **dock** (clic, clic central, clic derecho), **estado** (reloj, red) y
  lanzamiento de la terminal y del navegador.
- **Apagado desde la interfaz**: clic en el orbe → botón de apagar → *"Apagar: pulsa otra vez para confirmar"* →
  segundo clic → el sistema se apaga.
- **Desenfoque** del fondo detrás de los paneles (prueba de las rayas, arriba), bloqueo/desbloqueo de pantalla,
  red por cable conectada, recuperación automática tras forzar una caída de Hyprland.
- Pruebas automáticas del shell (búsqueda, calculadora, comunicación con Hyprland) y validación de las
  configuraciones con las herramientas de cada programa (`Hyprland --verify-config`, `foot --check-config`,
  `udevadm verify`).

**No verificado / límites conocidos:**
- **Nunca se ejecutó en una GPU real.** Todo el renderizado fue por software; el aspecto exacto del cristal con
  GPU puede necesitar ajustes (`size`, `passes`, `vibrancy` en `hyprland.conf`).
- **VMware, VirtualBox y hardware real:** sin probar. El flujo de **GitHub Actions** usa el mismo script de
  construcción que sí se probó, pero aún no se ha ejecutado en GitHub.
- Sin probar: audio, teclas de brillo/volumen, capturas de pantalla (`Impr`), WiFi, varios monitores, pantallas HiDPI.
- Durante las pruebas hubo **una caída aislada de Hyprland** (en el renderizador por software `llvmpipe`) mientras
  la VM emulaba una CPU exótica; no se repitió con una CPU estándar en más de 9 minutos de uso intensivo.
- **Sin instalador a disco** todavía. **Sin Secure Boot** (desactívalo en la BIOS/UEFI o usa una VM).
- La red WiFi se elige con `nmtui` (clic en el icono de red del panel de estado): aún no hay un selector gráfico.
- No es *exactamente* el "Liquid Glass" de Apple: un compositor normal no puede refractar la luz, así que se
  aproxima con desenfoque + translucidez + reflejo especular en el borde.

## Hoja de ruta

1. Instalador a disco (Calamares).
2. Panel rápido (WiFi, Bluetooth, brillo, volumen) y centro de notificaciones.
3. Pantalla de arranque (Plymouth) con el logo ∞ animado.
4. Aplicación de ajustes y tema de iconos propio.
5. Soporte de Secure Boot y Flatpak.
