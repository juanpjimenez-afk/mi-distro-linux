# Tienda: diseño, seguridad y límites

La **Tienda** (`infinity-tienda`) instala aplicaciones con un clic. Sale en la **biblioteca de apps** (el botón de
rejilla del dock) aunque sea una app del sistema, y también en el buscador (`Super + Espacio`, «tienda»).

| App | Cómo se instala | ¿Pide contraseña? |
|---|---|---|
| **Spotify** | Flatpak de Flathub (`com.spotify.Client`), por usuario | no |
| **Google Antigravity** | repositorio APT de Google (clave GPG + `apt install antigravity`) | sí (polkit) |
| **Google Chrome**, **Brave** | repositorio APT de cada fabricante | sí (polkit) |
| **Netflix, HBO Max, Disney+, YouTube, Claude, Gemini, NotebookLM, Google Classroom** | **WebApp**: un lanzador `.desktop` que abre la web en una ventana propia de Chrome/Brave (`--app=URL`) | no |

Chrome y Brave no estaban en la lista pedida: están porque **las WebApps necesitan un navegador Chromium** y la ISO no
trae ninguno (solo Epiphany, que no sirve para esto). Si instalas una WebApp sin tener navegador, la tienda te ofrece
instalar uno antes y lo encadena todo.

## Stack: Python + GTK 4, no Tauri

Se eligió **Python + GTK 4 (sin libadwaita) con CSS propio**, el mismo stack que el dock, Ajustes, Contraseñas y
el resto del shell. Por qué, en orden de peso:

1. **Una sola tecnología en el sistema.** Tauri añadiría Rust, Node/npm y una cadena de compilación a una ISO que se
   construye con `debootstrap` en GitHub Actions; hoy el shell son ficheros `.py` que se copian tal cual.
2. **Wayland nativo, sin XWayland.** GTK 4 usa su backend Wayland; una ventana con fondo translúcido recibe el
   desenfoque «cristal» de Hyprland sin hacer nada. Tauri (WebKitGTK por GTK 3) también corre en Wayland, pero obliga a
   pelear con el fondo transparente del `WebView` para lograr lo mismo.
3. **Procesos en segundo plano sin congelar la ventana**: `subprocess` en un hilo y los avisos de vuelta por
   `GLib.idle_add` (ver `tienda_ops.Ejecutor`); no hace falta un puente IPC web↔nativo.
4. **Mismo tema y mismos widgets** que el resto (violeta `#b9a8ff` sobre azul noche). libadwaita no está en la ISO y
   añadirla solo para esto no compensa: el aspecto *cyberpunk/minimalista* sale del CSS.

(No se midió el consumo de RAM de ninguna de las dos opciones: el argumento es de coherencia y de mantenimiento.)

## Estructura

```
shell/infinity_shell/
  tienda_catalogo.py   catálogo: lee y VALIDA catalogo.json (sin GTK)
  tienda_webapps.py    WebApps: navegador, lanzador .desktop, iconos, quitar/reparar (sin GTK)
  tienda_ops.py        estados, planes, Ejecutor (hilo + progreso), Gestor (cola, dependencias, espacio) (sin GTK)
  tienda_app.py        la ventana GTK 4 (tarjetas, búsqueda, categorías, progreso)
overlay/
  usr/bin/infinity-tienda                              lanzador del comando
  usr/libexec/infinity/tienda-ayudante                 la parte de ROOT (la lanza pkexec); un solo fichero, sin imports propios
  usr/share/polkit-1/actions/org.infinity.tienda.policy  la acción de polkit (contraseña de administrador)
  usr/share/applications/infinity-tienda.desktop       lanzador (X-Infinity-Biblioteca=siempre)
  usr/share/infinity/tienda/catalogo.json              LAS APPS: qué hay y cómo se instala cada una
  usr/share/infinity/tienda/iconos/*.svg, tienda.svg   iconos (los genera branding/make_tienda_iconos.py)
build/packages.list                                    + flatpak, pkexec, gpg
shell/tests/test_tienda_*.py, test_apps_tienda.py, test_paquetes_tienda.py
```

### Dependencias para desarrollar (Ubuntu)

```bash
# Lo mínimo para ejecutar las pruebas sin pantalla y leer/editar el código:
sudo apt install python3 python3-gi gir1.2-gtk-4.0 gir1.2-gdkpixbuf-2.0 librsvg2-common gpg
# Para las pruebas con ventana (pantalla virtual) y para probar los instaladores:
sudo apt install xvfb flatpak
sudo apt install pkexec policykit-1-gnome         # Ubuntu 25.04 o más; en 24.04 el paquete es «policykit-1»
# Ejecutar las pruebas (las de GTK se saltan solas si no hay pantalla):
python3 -m unittest discover -s shell/tests
xvfb-run -a python3 -m unittest discover -s shell/tests
```

## Cómo funciona cada estrategia

Todo parte de `catalogo.json`. Una entrada dice `tipo` (`flatpak`, `apt` o `webapp`) y el `origen` de ese tipo; el
validador (`tienda_catalogo.py`) descarta una entrada mal formada sin tumbar el resto.

### Flatpak (Spotify)
`flatpak remote-add --user --if-not-exists flathub <url>` y `flatpak install --user -y --noninteractive flathub
com.spotify.Client`. **Por usuario**: no necesita contraseña de administrador. La sesión añade
`~/.local/share/flatpak/exports/share` a `XDG_DATA_DIRS` (`infinity-session`), así el lanzador, el dock y la biblioteca
ven la app. La primera vez descarga el entorno de Flatpak (cerca de 1 GB).

### APT con permisos (Antigravity, Chrome, Brave)
La ventana lanza `pkexec /usr/libexec/infinity/tienda-ayudante instalar <id>`. **polkit** pide la contraseña en una
ventana gráfica (acción `org.infinity.tienda.apt`, `auth_admin_keep`: la recuerda unos minutos) y el ayudante, como
root:

1. lee el catálogo (comprobando que es de root y que nadie más puede escribir él ni sus carpetas) y valida la entrada;
2. descarga la clave GPG del fabricante por HTTPS verificado (tope de 256 KiB; redirecciones solo a https) y la
   comprueba con `gpg` en un directorio de trabajo propio; si el catálogo trae una huella (`huella`), **todas** las
   claves principales del fichero deben estar en esa lista;
3. escribe la clave y el fichero de fuente de apt con el nombre y el formato que documenta cada fabricante (así el
   propio paquete no añade un repositorio duplicado con otra clave, que haría fallar `apt update`); si el repositorio
   ya lo configura otro fichero, no toca nada;
4. `apt-get update` (la imagen no trae listas de apt) y `apt-get install <paquete>`, con el avance de `APT::Status-Fd`
   reenviado a la barra de progreso;
5. comprueba que el paquete quedó instalado y avisa de los `.desktop` que dejó.

`quitar` quita el paquete y borra el repositorio y la clave **solo si los puso la tienda** (llevan su marca) y ningún otro
fichero usa esa clave. `actualizar` hace `apt-get update` y `install --only-upgrade`.

### WebApps
Un lanzador `~/.local/share/applications/infinity-webapp-<id>.desktop` que ejecuta el navegador en modo aplicación:

```
Exec=/usr/bin/google-chrome-stable --app=https://claude.ai/ --user-data-dir=~/.local/share/infinity/webapps/perfil
     --no-first-run --no-default-browser-check --ozone-platform-hint=auto
```

- **Un perfil compartido** para todas las WebApps, aparte del de tu navegador de siempre: un inicio de sesión de Google
  vale para Gemini, NotebookLM, Classroom y YouTube, y todas corren en un solo proceso del navegador. Quitar una WebApp
  no borra ese perfil (tus sesiones).
- **Navegador**: el primero que haya de Google Chrome, Brave, Chromium, Edge o Vivaldi (apt o Flatpak). Si se
  desinstala y hay otro, la tienda rehace los lanzadores sola.
- **Icono**: un SVG propio de cada servicio se copia a `~/.local/share/icons/hicolor/scalable/apps/` (y un PNG de 256 px si
  se puede rasterizar), con el nombre `infinity-webapp-<id>`, para que **Rofi, Wofi, el dock y el lanzador** lo encuentren
  por nombre. Los iconos son dibujos originales (monogramas y formas geométricas), no los logotipos oficiales.
- **Comillas**: los argumentos de `Exec` se escapan según la especificación (`%` → `%%`, comillas dobles para `?`, `&`,
  espacios…); hay pruebas con argumentos difíciles y una con GLib de verdad.
- **El dock y la ventana**: Chromium no deja fijar el `app_id` de Wayland de una ventana `--app`; el lanzador declara el que
  *debería* tener (`chrome-<host>_<ruta>-Default`) y, además, anota el dominio (`X-Infinity-Dominio`) para que el dock
  reconozca la ventana aunque el `app_id` real sea otro (`apps.Indice.para_clase`).

## La biblioteca de apps

- **La Tienda sale siempre**, aunque la biblioteca esté en «Solo descargadas» (su `.desktop` lleva
  `X-Infinity-Biblioteca=siempre`).
- **Lo que instalas aparece**: antes la biblioteca llamaba «del sistema» a todo lo que estaba en `/usr/share/applications`,
  y lo que instalan apt, gdebi o la tienda (Chrome, Antigravity…) cae ahí. Ahora `build/build.sh` guarda en
  `/usr/share/infinity/apps-base.txt` los `.desktop` que trae la imagen de fábrica y «del sistema» es lo que está en esa
  lista. Sin la lista (un sistema de desarrollo) se usa la regla antigua por ruta.

## Seguridad

La parte delicada es el ayudante de root. Lo que garantiza (y qué pruebas lo comprueban, en
`shell/tests/test_tienda_ayudante.py`):

| Garantía | Cómo |
|---|---|
| El usuario no elige qué se ejecuta | solo recibe `instalar\|quitar\|actualizar <id>`; id con `[a-z0-9-]`; repositorio, clave, paquete y rutas salen del catálogo de root |
| El catálogo es fiable | se abre con `O_NOFOLLOW`; fichero y todas sus carpetas de root y sin escritura de grupo/otros; se revalida con reglas estrictas (`fullmatch`, URLs https sin usuario ni fragmento, rutas solo en `/etc/apt/keyrings` o `/usr/share/keyrings`) |
| Las dos validaciones coinciden | una prueba pasa el mismo corpus de trampas (inyecciones, saltos de línea, `..`, `file://`…) por el validador de la ventana y por el del ayudante |
| No hereda el entorno | `python3 -I` y un entorno propio mínimo para `apt`/`gpg`; órdenes por ruta absoluta; sin `shell=True` |
| Sin escrituras a medias ni a través de enlaces | temporal en la misma carpeta + `rename`, permisos 0644 |
| Una instalación a la vez | cerrojo `flock` en `/run/infinity-tienda` (0700) |
| Cerrar la ventana no rompe apt | `SIGPIPE` y `SIGHUP` ignorados; apt va en su propia sesión |
| La salida de apt no puede fingir órdenes | cada línea del registro lleva sangría, y las de control empiezan por `@@` |

## Límites y cosas por saber (honestas)

- **Antigravity**: lo que pediste (clave GPG, repositorio APT, `apt install antigravity`) funciona, pero **según Google ese
  repositorio es el de la serie 1.x y ya no se actualiza** (la última es la 1.23.2); las versiones 2.x se distribuyen
  como descarga aparte (antigravity.google). La tarjeta lo dice. Si quieres la 2.x, hay que añadir una estrategia nueva
  (descarga del `.tar.gz`) al ayudante; no se hizo porque no se pudo comprobar su formato desde aquí.
- **DRM (Netflix, Disney+, HBO Max)** necesita Widevine: Google Chrome lo trae. En Brave se activa desde sus ajustes la
  primera vez. Epiphany (WebKitGTK) no reproduce DRM, por eso las WebApps no lo usan.
- **No se comprobó contra los servidores reales** (la sandbox de desarrollo no llega a los hosts de los fabricantes): las
  URLs y los nombres de paquete y de fichero salen de la documentación de cada fabricante y de varias guías, no de una
  instalación real. Lo verificable sin red está probado (ver abajo); la instalación real de Spotify, Antigravity, Chrome y
  Brave y la clase de ventana real de Chrome/Brave hay que verlas en un PC con internet.
- **Espacio**: en modo en vivo sin guardado, lo que instales vive en RAM y se pierde al apagar. La tienda avisa si hay
  poco sitio y la ventana avisa del modo en vivo (arranca con «Guardar cambios» para conservar lo instalado).
- Las claves GPG se aceptan por confianza en el primer uso (HTTPS verificado), como en las instrucciones de cada
  fabricante. Para **fijarlas**, pon su huella en `huella` en `catalogo.json`: el ayudante rechazará cualquier otra.
- Un repositorio nuevo del catálogo no se debe añadir sin comprobar su clave y su formato.

## Añadir una app

Una entrada en `overlay/usr/share/infinity/tienda/catalogo.json` (campos y reglas en la cabecera de
`tienda_catalogo.py`) y su icono `iconos/<id>.svg` (añádelo a `branding/make_tienda_iconos.py`). Las pruebas
(`test_tienda_catalogo.py`) fallan si falta el icono o si la entrada no valida. Para una WebApp basta con la URL.

## Pruebas

`test_tienda_catalogo.py` (catálogo y validación), `test_tienda_webapps.py` (lanzadores, comillas, navegadores),
`test_tienda_ops.py` (planes, ejecución real de órdenes, cola), `test_tienda_ayudante.py` (el ayudante de root, con
`gpg` real y un `apt` falso), `test_apps_tienda.py` y `test_biblioteca.py` (biblioteca y dock),
`test_paquetes_tienda.py` (que todo está enlazado en la ISO) y `test_tienda_ventana.py` (la ventana, con pantalla virtual).
