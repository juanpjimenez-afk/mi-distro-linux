# pAnkI, el agente virtual de Infinity OS

pAnkI es el agente virtual del sistema: responde dudas, organiza archivos y automatiza el escritorio
conversando. Nace de la app "NovaOS AI" (React + Express + `@google/genai`, hecha en Google AI Studio:
"copiloto y organizador para tu sistema operativo"). De esa app solo se conservaban `metadata.json`,
`index.html`, `.env.example` y `bun.lock`; el código (`src/`, el servidor y los prompts) no estaba. Esta
versión es una reconstrucción nativa a partir de su descripción y de sus dependencias, no un port línea a
línea. Si aparece el código original, sus prompts se pueden portar a `SISTEMA` en `panki_nucleo.py`.

Decisión de arquitectura: **nativa en Python + GTK4**, sin Node ni Bun en la ISO. Habla con la API REST de
Gemini con `urllib` (sin dependencias nuevas) y usa "function calling" para actuar sobre el sistema.

## Archivos

| Archivo | Qué contiene | GTK |
|---|---|---|
| `shell/infinity_shell/panki_herramientas.py` | Herramientas del agente, política de rutas, plan de organización y deshacer | no |
| `shell/infinity_shell/panki_nucleo.py` | Clave, cliente de Gemini, bucle del agente, prompt del sistema | no |
| `shell/infinity_shell/panki_texto.py` | Markdown de Gemini a Pango (escapado seguro) | no |
| `shell/infinity_shell/panki_app.py` | Ventana de chat (`org.infinity.Panki`) | sí |
| `shell/infinity_shell/panki_burbuja.py` | La burbuja de cristal de pAnkI en el escritorio (a la derecha) | sí |
| `overlay/usr/bin/infinity-panki` | Lanzador (como `infinity-ajustes`) | — |
| `overlay/usr/share/applications/infinity-panki.desktop` | Entrada del menú | — |
| `branding/panki-white.svg`, `branding/panki.png` y su instalación en `build/build.sh` | Marca de pAnkI (la burbuja usa el SVG blanco; el menú, el PNG) | — |
| `shell/infinity_shell/ajustes.py` / `ajustes_app.py` | Página "pAnkI (IA)" con `panki.modelo`, `panki.comandos` y `panki.burbuja` | parcial |
| `shell/infinity_shell/main.py`, `style.css` | Crea la burbuja con el shell; estilo `button.orb.panki` | sí |
| `shell/infinity_shell/launcher.py` | Prefijo `?` en el buscador: "Preguntar a pAnkI" | sí |
| `overlay/etc/skel/.config/hypr/hyprland.conf` | Atajo Super+N y regla de ventana | — |

## La burbuja

pAnkI vive en una **burbuja de cristal** en el escritorio, hermana de la esfera del logo ∞ (el `Orbe` de
`dock.py`, abajo a la izquierda), pero **a la derecha**.

- Es una ventana de capa (`gtk4-layer-shell`) del propio shell, namespace `infinity-panki`: hereda el
  desenfoque de la regla `layerrule` de los paneles `infinity-*`.
- Misma esfera de 48 px (el logo ∞ también baja de 60 a 48, centrado con el dock, porque las apps ahora son de colores) y mismo estilo `button.orb` que el logo, más una clase `panki` para el matiz propio.
  Dentro lleva la marca de pAnkI en blanco (`panki-white.svg`, una burbuja de diálogo con una chispa dentro, con el mismo trazo
  que el ∞).
- Posición: borde derecho, **encima** de la píldora de estado (volumen, batería, red, hora), que ya ocupa
  la esquina inferior derecha. Nunca debe tapar esa píldora ni el dock.
- Al pulsarla abre o cierra la ventana de chat: `gapplication action org.infinity.Panki alternar ||
  infinity-panki`. La ventana de chat sale cerca de la burbuja (abajo a la derecha) con una regla de
  ventana de Hyprland.
- Se puede ocultar con el ajuste `panki.burbuja` (bool, por defecto `True`, "Mostrar la burbuja de pAnkI en
  el escritorio"), que se aplica con la recarga del shell.
- Consume lo mismo que el logo: ninguna animación ni temporizador propio.

## Privacidad (lo que el usuario tiene que saber, y la app tiene que decirlo)

1. Cada mensaje, y lo que pAnkI vea con sus herramientas, **se envía a Google (Gemini)**. pAnkI no funciona sin
   conexión ni sin la clave del propio usuario.
2. pAnkI ve **nombres de archivos** de una carpeta solo si el usuario lo permite (por carpeta), y el
   **contenido** de un archivo solo si el usuario lo permite (por archivo o por carpeta, nunca global).
3. La clave de Gemini es del usuario. Se guarda en `~/.config/infinity/panki/clave` (carpeta 0700, archivo
   0600) o se toma de la variable de entorno `GEMINI_API_KEY`. **Nunca** va en `ajustes.json` (que se
   exporta), nunca en un registro, nunca en una URL (va en la cabecera `x-goog-api-key`), nunca en un mensaje de
   error. La ISO no lleva ninguna clave.
4. No se guarda el historial de conversaciones. Al cerrar la ventana se pierde.
5. Sin telemetría. Lo único que sale del equipo es la llamada a la API de Gemini y lo que el usuario apruebe
   (`abrir_url`, `ejecutar_comando`).

## Seguridad de las herramientas

Principio: **el modelo propone, el usuario decide**. Un archivo, una web o la propia respuesta del modelo
pueden contener instrucciones maliciosas (inyección de prompt). Por eso:

- Todo lo que cambia algo o expone datos pasa por una **aprobación** que muestra la acción exacta.
- Las acciones que modifican (mover, crear, cambiar ajustes, abrir una URL, comandos) **nunca** se recuerdan:
  se preguntan siempre. Solo las lecturas y las aperturas de apps pueden "permitirse en esta conversación", y
  el permiso es **por objeto exacto**, no por familia: `listar:<ruta>:<ocultos 0|1>`, `buscar:<ruta>`,
  `organizar:<ruta>` (solo la propuesta, no el movimiento), `leer:<archivo>` y `app:<id>:<hash>` (el hash cambia
  si cambia el archivo `.desktop`). Aprobar leer `leeme.txt` no concede leer otro archivo de esa carpeta. La
  tarjeta lleva el texto literal de lo que concede (`Solicitud.alcance`) y la ventana lo repite en el botón.
- Los permisos concedidos "en esta conversación" se ven siempre en el pie de la ventana («N permisos activos ·
  Revocar»), se pierden al ocultar la ventana o al empezar una conversación nueva, y se pueden revocar.
- Los resultados de las herramientas son **datos**, nunca órdenes. El contenido de archivos llega al modelo
  dentro de un campo `contenido_no_confiable` y el prompt del sistema le dice que no obedezca instrucciones
  que aparezcan ahí.
- Nada de borrar. Nada de `sudo`. Nada de apagar ni reiniciar.
- Se valida **antes** de preguntar: una llamada con argumentos inválidos devuelve un error al modelo sin
  molestar al usuario.

### Política de rutas (`panki_herramientas.py`)

- Raíz permitida: el directorio personal (`$HOME`). Nada fuera de él.
- Se resuelve con `os.path.realpath` **antes** de comprobar: un enlace simbólico que sale del directorio
  personal se rechaza. `..` y rutas relativas se rechazan (solo `~/...` o absolutas dentro de `$HOME`).
- Lista negra dentro de `$HOME` (ni listar, ni leer, ni mover desde/hacia): `.ssh`, `.gnupg`,
  `.local/share/keyrings`, `.config/infinity/panki`, `.mozilla`, `.config/epiphany`, `.local/share/epiphany`,
  `.config/chromium`, `.config/google-chrome`, `.password-store`, y cualquier archivo cuyo nombre empiece por
  `.env` o sea `id_rsa`, `id_ed25519`, `*.pem`, `*.key`.
- Lista de **escritura prohibida** (aparte, más amplia): no se crea, mueve, renombra ni se mueve nada hacia/desde
  rutas que sirven para iniciar programas o configurar el sistema (`.bashrc`, `.profile`, `.zshrc` y afines,
  `.config/autostart`, `.config/hypr`, `.config/systemd`, `.config/infinity`, `.config/foot`, `.local/bin`,
  `.local/share/applications`, `.local/share/systemd`, `.ssh`, `.gitconfig`, etc.). Así una conversación
  manipulada no puede plantar un autoarranque.
- Los archivos ocultos no se listan salvo `mostrar_ocultos=true` (que tiene su propia tarjeta de riesgo medio
  y su propio permiso); la lista negra se aplica siempre.
- Los nombres que se muestran al usuario se normalizan (NFC) y los caracteres invisibles o de control se
  escriben como `\uXXXX`; si un nombre mezcla alfabetos (homógrafos) la tarjeta avisa. Los nombres largos se
  recortan por el medio, no por el final.
- `mover_archivo` a un nombre ejecutable (`.desktop`, `.sh`, `.py`, `.service`, `.appimage`… o con bit de
  ejecución) es riesgo alto; el título dice «Renombrar» cuando solo cambia el nombre.
- La comprobación se repite al ejecutar: si el sistema de archivos cambió entre la tarjeta y la acción, no se
  hace nada.
- Límites: lista 200 entradas, búsqueda 100, lectura 20 000 bytes (solo texto UTF-8; los binarios se
  rechazan), salida de comando 8 000 bytes.

## Herramientas

Todas devuelven un objeto JSON-serializable. Los errores van como `{"error": "mensaje en español"}`.

| Nombre | Aprobación | Qué hace |
|---|---|---|
| `info_sistema()` | ninguna | SO, kernel, tiempo encendido, carga, RAM, disco, batería. Solo lectura de `/proc`, `/sys`, `os.statvfs` |
| `ver_ajustes(prefijo)` | ninguna | Ajustes de Infinity (id, etiqueta, valor actual, valores válidos) desde `ajustes.SCHEMA` |
| `buscar_apps(consulta)` | ninguna | Apps instaladas (archivos `.desktop`): id, nombre, descripción |
| `listar_directorio(ruta, mostrar_ocultos)` | por carpeta y por ocultos sí/no, "esta conversación" | Nombres, tipo y tamaño |
| `buscar_archivos(carpeta, patron)` | por carpeta, "esta conversación" | Coincidencias por nombre (glob), sin leer contenido |
| `leer_archivo(ruta)` | por archivo, "esta conversación" solo para ese archivo | Texto UTF-8 hasta 20 000 bytes |
| `proponer_organizacion(carpeta, criterio)` | por carpeta (nombres) | Calcula un plan de movimientos (criterio `tipo` o `fecha`), **no mueve nada**; devuelve `id_plan` y resumen |
| `aplicar_organizacion(id_plan)` | siempre, con el detalle completo | Mueve según el plan guardado. No sobrescribe: si el destino existe, añade ` (1)`, ` (2)`… Guarda un registro para deshacer |
| `deshacer_ultima_organizacion()` | siempre | Revierte los movimientos del último registro, solo los que sigan donde se dejaron |
| `crear_carpeta(ruta)` | siempre | Crea una carpeta dentro de `$HOME` |
| `mover_archivo(origen, destino)` | siempre | Mueve o renombra un archivo; no sobrescribe |
| `abrir_app(app)` | por app, "esta conversación" | Lanza un `.desktop` **del sistema** por id (de `buscar_apps`), con `gio launch` y el entorno limpio; ignora los `.desktop` del directorio del usuario |
| `abrir_url(url)` | siempre | Solo `http` y `https`, sin usuario ni contraseña, hasta 2 000 caracteres. Riesgo alto si la URL lleva consulta larga (puede enviar datos) |
| `cambiar_ajuste(id, valor)` | siempre, muestra valor viejo y nuevo | Valida con el esquema de Ajustes; aplica con las mismas acciones de recarga que la app de Ajustes |
| `ejecutar_comando(argv, carpeta)` | siempre, riesgo alto, **solo si `panki.comandos` está activo** | Lista `argv` sin shell, tiempo máximo 30 s, salida recortada. Rechaza `sudo`, `su`, `pkexec`, `doas`, `chroot`, `dd`, `mkfs*`, `shred`, `wipefs`, `fdisk`, `parted`, `shutdown`, `reboot`, `poweroff`, `halt`, `rm -r`/`rm -rf` y también `apt`, `dpkg`, `pip`, `git`, `chmod`, `ln`, `install` y las órdenes de `systemctl` que cambian algo. Máximo 20 argumentos; la tarjeta separa «Programa», «Destino» (si hay URL o host) y un argumento por línea; entorno limpio |

Categorías de `proponer_organizacion` con criterio `tipo`: Imágenes, Vídeos, Música, Documentos, Hojas de
cálculo, Presentaciones, Comprimidos, Programas, Código, Otros. Solo mueve archivos sueltos de la carpeta
(no entra en subcarpetas, no toca enlaces simbólicos, no toca archivos ocultos).

## Contrato entre módulos

```python
# panki_herramientas.py
class ErrorHerramienta(Exception): ...        # mensaje para el modelo y para el usuario, en español

@dataclass
class Solicitud:
    herramienta: str
    titulo: str            # una frase: "Mover 14 archivos en ~/Descargas"
    detalle: list[str]     # líneas exactas (cada movimiento, el comando, la URL...)
    riesgo: str            # "bajo" | "medio" | "alto"
    recordable: str | None # clave para "permitir en esta conversación" (p. ej. "carpeta:/home/u/Descargas"), None = siempre preguntar

@dataclass
class Preparada:
    nombre: str
    args: dict
    solicitud: Solicitud | None   # None = no necesita aprobación

@dataclass
class Resultado:
    ok: bool
    datos: dict            # lo que ve el modelo (JSON-serializable)
    resumen: str           # una línea para el usuario

class Ejecutor:
    def __init__(self, hogar=None, permitir_comandos=False, ajustes=None, correr=None, abrir=None, estado_dir=None): ...
    def declaraciones(self) -> list[dict]          # functionDeclarations de Gemini
    def preparar(self, nombre: str, args: dict) -> Preparada     # valida; lanza ErrorHerramienta
    def ejecutar(self, preparada: Preparada) -> Resultado
```

```python
# panki_nucleo.py
class ErrorPanki(Exception): ...                   # mensaje para el usuario, en español, sin la clave

def existe_clave() -> bool
def cargar_clave() -> str | None                  # variable GEMINI_API_KEY primero; si no, el archivo
def guardar_clave(clave: str) -> None             # valida formato; escribe 0600 de forma atómica
def borrar_clave() -> None

class ClienteGemini:
    def __init__(self, clave, modelo, base_url="https://generativelanguage.googleapis.com/v1beta",
                 timeout=60, abrir=None): ...
    def generar(self, contenidos, sistema, herramientas) -> dict   # una llamada generateContent

class Agente:
    def __init__(self, cliente, ejecutor, aprobar, max_pasos=8): ...
    def enviar(self, texto, alevento) -> str      # bloqueante: se llama desde un hilo
    def cancelar(self) -> None
    def reiniciar(self) -> None
    conversacion_grants: set                       # claves "recordables" concedidas

def crear_agente(aprobar, valores=None, hogar=None) -> Agente
```

- `aprobar(solicitud) -> "no" | "una_vez" | "conversacion"`. La llamada **bloquea** el hilo del agente hasta
  que el usuario responde. Solo se acepta `"conversacion"` si `solicitud.recordable` no es `None`; si no,
  se trata como `"una_vez"`.
- `alevento(evento)` recibe tuplas: `("estado", texto)`, `("herramienta", nombre, resumen_de_args)`,
  `("resultado", nombre, ok, resumen)`, `("texto", texto_final)`.
- `enviar` guarda en el historial el `content` del candidato **tal cual lo devuelve Gemini** (incluidas las
  firmas de pensamiento, `thoughtSignature`), porque los modelos con razonamiento fallan si no se devuelven.
  Con varias `functionCall` en un turno, responde con **todas** las `functionResponse` en un único mensaje de
  rol `user`, en el mismo orden.
- Errores de la API → `ErrorPanki` con mensaje claro: 400 (petición o modelo no válido), 401/403 (clave no
  válida o sin permiso), 404 (modelo no existe: sugerir cambiarlo en Ajustes), 429 (cuota), 5xx (servicio),
  red caída, tiempo agotado, respuesta no JSON, respuesta bloqueada (`promptFeedback.blockReason`, o
  `finishReason` `SAFETY`). El texto del error **nunca** incluye la clave ni la URL con la clave.
- El historial se recorta: los resultados de herramientas antiguos se sustituyen por `[contenido omitido]`
  cuando el total supera 200 000 caracteres.
- `crear_agente` lee `panki.modelo` y `panki.comandos` con `ajustes.valor` (con valores por defecto si el
  esquema aún no los tiene) y lanza `ErrorPanki("falta la clave de Gemini…")` si no hay clave.

### Ajustes (`ajustes.py`)

- `panki.modelo`: tipo `texto`, por defecto `gemini-flash-latest`, máximo 60 caracteres. Texto libre a propósito:
  los nombres de modelo cambian y el usuario puede poner el que admita su clave.
- `panki.comandos`: tipo `bool`, por defecto `False`. "Permitir que pAnkI proponga comandos de terminal.
  Siempre pide confirmación."
- Página nueva `panki` ("pAnkI (IA)") en `PAGINAS`, antes de `atajos`. Sin acciones de recarga. Un texto
  explica que la clave se guarda desde la ventana de pAnkI y que los mensajes se envían a Google.

## Ventana (`panki_app.py`)

- `Gtk.Application` con id `org.infinity.Panki`, una sola instancia: una segunda llamada
  (`infinity-panki --preguntar "texto"`) activa la ventana y envía el texto.
- Burbujas: usuario a la derecha, pAnkI a la izquierda. El texto de pAnkI pasa por `panki_texto` (Pango, escapado).
- Entrada abajo: Intro envía, Mayús+Intro nueva línea. Botón "Parar" mientras trabaja. Botón "Nueva
  conversación".
- Tarjeta de aprobación **en línea**: título, detalle desplazable (lo único en monoespaciada, para ver los
  nombres exactos), riesgo con color, botones "Permitir" y "Denegar", y debajo, si es recordable, el texto
  literal de lo que se concedería «en esta conversación». Los botones no responden hasta pasados 700 ms
  (un clic o tecla que ya venía en camino no puede aprobar por accidente); `Esc` deniega; sin respuesta en
  3 minutos se deniega sola. El hilo del agente espera con un `threading.Event`; la interfaz se actualiza
  con `GLib.idle_add`.
- Interfaz minimalista: cabecera con solo el nombre y unos iconos discretos (nueva conversación, clave, controles de ventana), la
  conversación sin burbujas pesadas, una línea de estado discreta, entrada de una línea y el pie «Se envía a
  Google (Gemini)». Ventana de 460×640 anclada abajo a la derecha, encima de la burbuja.
- Primera vez sin clave: tarjeta con el aviso de privacidad, un campo para pegar la clave (oculto) y "Guardar".
  Un botón "Clave…" permite cambiarla o borrarla.
- Errores visibles en la propia conversación (nunca solo por consola).
- El hilo de red nunca toca widgets; todo lo visual va por `GLib.idle_add`.
- Cerrar la ventana con el agente trabajando cancela (`Agente.cancelar`) y no deja el hilo bloqueado
  esperando una aprobación (se responde "no").

## Integración con el sistema

- `infinity-panki`: igual que `infinity-ajustes` (`PYTHONPATH=/usr/lib/infinity`, `GSK_RENDERER=cairo` por
  defecto, `exec python3 -m infinity_shell.panki_app "$@"`).
- Atajo `Super+N` (alterna la ventana; si no está abierta, la abre). Regla de ventana flotante y centrada para la clase `org.infinity.Panki`, con la misma sintaxis
  que ya usa la regla de Juegos; comprobada con `Hyprland --verify-config` (el binario está en `work/rootfs`).
- Buscador: `? pregunta` muestra "Preguntar a pAnkI: pregunta"; Enter abre pAnkI con esa pregunta.
- Icono `panki.png` instalado en `/usr/share/infinity/` (hay que añadirlo a la lista de `build/build.sh`).

## Lo que no se puede probar aquí (y se dice en el README)

- Llamadas reales a Gemini: no hay clave ni salida a esa API desde la sandbox. Todo se prueba contra un
  servidor HTTP falso que imita el formato de `generateContent` con `functionCall`.
- La ventana en Hyprland real, el atajo, el desenfoque y la posición exacta de la burbuja y de la ventana: solo en VM.
- Los iconos simbólicos de la cabecera dependen de `adwaita-icon-theme` (está en `packages.list`); el logo SVG de
  la burbuja, de `librsvg2-common` (también). Ninguno de los dos se ha visto en una ISO real.
- Los nombres de modelo vigentes: por eso el modelo es un texto editable.

## Pruebas que debe haber

- `test_panki_herramientas.py`: política de rutas (enlace simbólico que escapa, `..`, lista negra, ocultos),
  límites, plan de organización por tipo y por fecha, aplicar sin sobrescribir, deshacer, `cambiar_ajuste`
  (inválido, válido), `abrir_url` (esquemas, usuario en la URL, longitud), `ejecutar_comando` (lista
  negra, desactivado por defecto, tiempo, recorte), contenido envuelto en `contenido_no_confiable`.
- `test_panki_nucleo.py`: servidor falso con respuestas guionizadas (solo texto; llamada y respuesta;
  llamadas paralelas; firma de pensamiento conservada; aprobación denegada; `max_pasos`; 400/401/403/404/429/
  500; JSON roto; respuesta bloqueada; cancelar), la clave nunca aparece en errores ni en registros, permisos
  0600, la variable de entorno tiene prioridad, el recorte del historial.
- `test_panki_texto.py`: escapado (`<b>`, `&`, comillas), negrita, cursiva, código, listas.
- `test_panki_ventana.py` (GTK, se salta sin pantalla): tarjeta de aprobación, hilo no bloqueado, cerrar
  cancela.
- Ampliar `test_ajustes.py` y `test_launcher.py` para lo nuevo.
