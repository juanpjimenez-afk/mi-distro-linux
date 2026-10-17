# pAnkI, el motor web (diseño actual)

> El motor anterior (un agente de Gemini con herramientas sobre el sistema) está retirado; su diseño
> sigue en [`docs/panki-diseno.md`](panki-diseno.md), con el mismo aviso al revés, por si se retoma.

## Qué es

pAnkI ya no es un agente que llama a un modelo de lenguaje de Google. Ahora es una app web de un solo
archivo (`shell/infinity_shell/panki_web/app.html`) que corre entera en el navegador del usuario (en
este caso, dentro de una `WebKit.WebView`): tareas, agenda, notas, un temporizador, una calculadora y
un chat **por reglas** en español (sin modelo de lenguaje alguno) que entiende fechas, horas y
categorías escritas en lenguaje natural. En su «Modo Profundo» puede además consultar un puñado de
APIs públicas gratuitas (sin clave) para preguntas generales de la vida diaria. El nombre con el que se
presenta es configurable (por defecto «pAnkI»).

Esa página la escribió y la mantiene otro cambio de este mismo repositorio; este documento describe
**cómo se aloja**, no su funcionamiento interno (para eso, lee directamente
`shell/infinity_shell/panki_web/app.html`).

## Cómo se aloja

`shell/infinity_shell/panki_app.py` abre una ventana GTK 4 (460×640, con su propia cabecera y sus
botones de minimizar/maximizar/cerrar) cuyo cuerpo es una `WebKit.WebView` (WebKitGTK 6.0, namespace GI
`WebKit`; **no** `WebKit2`, que es el de GTK3 o el de la rama 4.1) a pantalla completa bajo la cabecera.

- **Página:** se resuelve con el mismo patrón que `dock.asset()` en `dock.py` (`ruta_app_html()` en
  `panki_app.py`): primero la variable de entorno de desarrollo `INFINITY_PANKI_HTML`, luego la ruta
  instalada (`/usr/share/infinity/panki/app.html`), y por último la del propio repositorio (para poder
  probarla sin instalar nada). Si no se encuentra ninguna, se muestra un aviso en vez de abrir una
  ventana en blanco.
- **Persistencia:** `panki_app.py` crea (si no existe, con permisos `0700`) `~/.local/share/infinity/panki/webkit/`,
  con dos subcarpetas (`datos/` y `cache/`, también `0700`) y construye ahí una
  `WebKit.NetworkSession` persistente (`WebKit.NetworkSession.new(datos, cache)`): en WebKitGTK 6.0 el
  almacenamiento ya no se configura en el `WebView` (como en 4.1, con un `WebsiteDataManager` pasado al
  `WebContext`), sino en esa sesión de red, que se le pasa al `WebView` como su propiedad de
  construcción `network-session`. Así, `localStorage` (tareas, notas, agenda, nombre del asistente)
  sobrevive a cerrar y reabrir pAnkI. Si por lo que sea no se pueden preparar esos directorios, la
  sesión cae a una efímera (pAnkI sigue abriendo esa vez, solo que sin guardar nada al cerrar) en vez
  de no abrir la ventana.
- **Instancia única, burbuja y Super + N:** sin cambios de comportamiento respecto al motor anterior.
  `alternar` (la acción de GApplication que usan la burbuja y el atajo) muestra la ventana si está
  oculta y la oculta si está visible; la X también la oculta y deja la aplicación viva, para que la
  burbuja la reabra al instante. Como ya no hay hilos propios que cancelar (no hay agente, no hay
  llamadas bloqueantes esperando una aprobación), no hace falta distinguir "con trabajo en curso".

## El contrato con la página (2 puntos)

Es el único contrato que hay; `panki_app.py` no toca `app.html`, solo lo aloja e inyecta un puente
mínimo por código (`WebKit.UserScript`, al final del documento):

- **`window.aplicarNombrePanki(nombre)`**: función global que la página expone. `panki_app.py` la
  llama por JavaScript (siempre con `json.dumps`, nunca concatenando texto crudo) para fijar el nombre
  del asistente desde el ajuste del sistema (`panki.nombre`), que es la fuente de verdad. Es segura de
  llamar en cualquier momento (si la página aún no ha terminado de cargar, encola la llamada y la
  aplica al final) y, si se llama varias veces, gana la última. No dispara el evento de abajo.
- **Evento `window.dispatchEvent(new CustomEvent('panki:nombre-cambiado', {detail: {nombre}}))`**: lo
  dispara la propia página cuando el USUARIO renombra al asistente desde dentro de la app (por el chat,
  escribiendo «llámate X», o desde el panel «Yo»). El puente inyectado traduce ese evento a un mensaje
  (`window.webkit.messageHandlers.panki.postMessage(JSON.stringify({accion:'nombre', valor:nombre}))`);
  `panki_app.py` lo recibe, lo valida (JSON válido, `accion` dentro de una lista cerrada —de momento
  solo `"nombre"`—, `valor` un texto de 1 a 24 caracteres tras recortar espacios; cualquier otra cosa se
  ignora sin fallar) y, si es válido, lo persiste en el ajuste `panki.nombre` con el mismo mecanismo de
  lectura-modificación-escritura que usa el resto de `ajustes.py`.

`--preguntar TEXTO` (el mismo que ya usaban la burbuja, Super + N y `? pregunta` en el buscador) se
inyecta igual: el puente define `window.pankiPreguntar(texto)`, que rellena `#in` (el campo de entrada
del chat) y envía `#form`; `panki_app.py` la llama (otra vez con `json.dumps`) en cuanto la página
termina de cargar, o de inmediato si ya había terminado.

## Qué puede hacer pAnkI ahora — y qué ya NO puede hacer

Esto es lo más importante para quien venga de conocer el motor anterior: el cambio no es solo de
proveedor, es de alcance. pAnkI ahora:

**Puede:**
- Llevar tareas, agenda y notas (crearlas, completarlas, borrarlas, exportarlas e importarlas como
  respaldo), con un clasificador que aprende categorías de lo que escribes.
- Entender fechas, horas y duraciones en español natural ("mañana a las 10", "en media hora").
- Un temporizador y una calculadora simples, dentro del propio chat.
- Conversar por reglas (sin modelo de lenguaje: patrones de texto en español), con un resumen del día
  ("Organiza mi día") y observaciones sobre tus propios hábitos (en qué sueles organizarte, tu
  categoría más frecuente...).
- En **Modo Profundo**, consultar un puñado de APIs públicas gratuitas para preguntas generales (ver
  "Privacidad" más abajo).
- Cambiar el nombre con el que se presenta (desde Ajustes, o desde dentro de la propia app).

**Ya NO puede** (todo esto era del motor anterior y no tiene equivalente aquí):
- No lee, lista, mueve ni organiza archivos ni carpetas del equipo.
- No abre aplicaciones ni páginas web.
- No cambia ningún ajuste del sistema (salvo, indirectamente y solo ese, su propio nombre).
- No ejecuta comandos de terminal.
- No necesita ni acepta ninguna clave de API, ni lee información del equipo (sistema operativo,
  batería, apps instaladas...).
- No hay tarjetas de permiso: no hay nada que pedir permiso para hacer, porque no toca el sistema.

## Privacidad real

- **Modo Rápido:** nada sale del equipo. Toda la lógica (tareas, agenda, notas, el chat por reglas, el
  temporizador, la calculadora) corre en JavaScript dentro de la página, y los datos viven solo en
  `localStorage` (con la persistencia que le da la `WebKit.NetworkSession` descrita arriba).
- **Modo Profundo:** puede consultar internet para preguntas generales, enviando **solo el tema de la
  pregunta** (nunca tus tareas, notas o agenda) a una lista cerrada de servicios públicos gratuitos sin
  clave, la misma que la propia página declara en su `Content-Security-Policy` (no hay otra lista: si
  un dominio no está aquí, el navegador bloquea la conexión):
  - `es.wikipedia.org`, `es.wikibooks.org` — conocimiento general.
  - `geocoding-api.open-meteo.com`, `api.open-meteo.com` — ubicación y clima.
  - `open.er-api.com`, `api.frankfurter.app` — tipos de cambio y divisas.
  - `restcountries.com` — datos de países.
  - `api.mymemory.translated.net` — traducción.
  - `api.dictionaryapi.dev` — definiciones de diccionario.
  - `api.coingecko.com` — precios de criptomonedas.
- No hay telemetría. No hay clave que proteger (no existe ninguna). El registro de fallos de la
  ventana (`~/.cache/infinity/panki.log`) solo anota errores de carga o de JavaScript, nunca el
  contenido de tareas, notas o conversaciones.

## Lo que no se pudo verificar aquí

- **WebKitGTK 6.0 real:** el entorno donde se escribió y probó este cambio no tiene el typelib de
  introspección (`gir1.2-webkit-6.0`); solo se pudo confirmar, con GTK 4 real, que `panki_app.py` se
  degrada con un aviso (en vez de no abrir la ventana) cuando falta. La `WebKit.WebView` real, la
  `NetworkSession`, el puente de mensajes y la inyección del `UserScript` están escritos a partir de la
  documentación oficial (varadoc/PyGObject/webkitgtk.org) pero no se han ejecutado de verdad.
- **Hyprland real:** la posición y el tamaño de la ventana (460×640, anclada abajo a la derecha) solo
  se comprobó contra la configuración de `hyprland.conf` como texto (y, si hay un verificador de
  Hyprland disponible, contra `Hyprland --verify-config`), no en una sesión real con un compositor.
- **Persistencia real tras reiniciar:** se comprobó que `WebKit.NetworkSession.new()` recibe rutas
  creadas con los permisos correctos, pero no que WebKitGTK efectivamente escriba y recupere
  `localStorage` de ahí entre dos arranques distintos del proceso, al no haber WebKitGTK real en este
  entorno.
