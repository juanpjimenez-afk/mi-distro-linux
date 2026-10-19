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

(Hay un tercero, el del calendario, descrito en la sección «Calendario» más abajo.) Es el contrato original; `panki_app.py` no toca `app.html`, solo lo aloja e inyecta un puente
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

## Calendario (vista de pAnkI + app «Calendario»)

pAnkI trae un calendario: una vista de la propia página (`app.html`) y una app del menú, «Calendario», que
abre pAnkI directamente en ella. **No hay un segundo almacenamiento**: un evento es una tarea de `S.tasks`
con `due` (`'AAAA-MM-DD'`) y `time` (`'HH:MM'`), más `who` (con quién) y `loc` (dónde) si los tiene. Lo
que se agenda por chat sale en el calendario; lo creado en el calendario sale en «mis tareas», «organiza mi
día» y la pestaña Agenda; y todo viaja en el mismo respaldo JSON y en el mismo `.ics`.

### Agendar por chat

«Agéndame una reunión a las 14:35 el 23 de octubre con pepe» crea el evento y responde con el **día de la
semana calculado**: `Agendada: Reunión con Pepe · viernes 23 de octubre de 2026 a las 14:35`, con los
botones «Deshacer» y «Ver en el calendario». Se entienden `agéndame / agenda / programa / anota / ponme /
crea / apunta / reserva…` (y «¿puedes agendarme…?», «quiero agendar…»), fechas como `23 de octubre`,
`23/10`, `2026-10-23`, `el viernes 23`, `mañana`, `pasado mañana`, `el próximo lunes`, `dentro de 3 días`,
`en 2 meses`, y horas como `14:35`, `14.35`, `14h35`, `2 y media de la tarde`, `3 menos cuarto`, `2:35 pm`.
La persona se guarda con mayúscula (`con pepe` → `Pepe`; `con el doctor lópez` → `el doctor López`; `con
el equipo de ventas` se queda igual) y el lugar aparte (`en la oficina` → `loc`).

Qué hace en los casos difíciles (todo con pruebas):

- **Sin hora** → la guarda y pregunta «¿A qué hora es?»; la respuesta («a las 2 y media de la tarde») la
  completa. **Sin día ni hora** → pregunta antes de guardar nada. Si repites la misma reunión ya con hora,
  completa la existente en vez de duplicarla.
- **Fecha inexistente** («31 de abril», «29/2/2027», «30 de febrero», hora «25:70») → lo dice
  («El 31 de abril no existe: abril tiene 30 días») y **no guarda nada** (antes se desbordaba al mes siguiente).
- **Sin año y ya pasó** → el año siguiente, y lo dice. El 29 de febrero busca el próximo año bisiesto.
- **Día de la semana que no cuadra** («el lunes 23 de octubre» cuando cae viernes) → manda la fecha y avisa.
- **Otro evento a la misma hora** → «ya tienes «X» a esa misma hora»; si solo se pisan, «se superpone con».
- **Consultar, mover, borrar**: «¿qué tengo el 23 de octubre / mañana / el sábado?», «mueve la reunión con
  Pepe al 24 a las 10» (solo la hora o solo el día conservan lo otro), «borra / cancela la reunión con
  Pepe» (si hay varias, la más próxima, y lo dice). «Abre el calendario», «calendario de noviembre».

### La vista

Botón de calendario en la cabecera (o la pestaña Agenda, o un chip del chat, o «Ver en el calendario»):
cuadrícula del mes con la **semana empezando en lunes** (L M X J V S D), mes y año en español con ‹ ›,
botón «Hoy», el día de hoy resaltado, un punto por evento (rojo si venció sin hacerse, apagado si ya está
hecho) y un contador si hay más de 3. Tocar un día muestra sus eventos (hora, título, con quién/dónde),
con marcar hecho, borrar (con «Deshacer») y «＋ Añadir reunión» (título, hora, con quién, dónde). Flechas
del teclado = día/semana, RePág/AvPág = mes. Cabe en 460×640 sin scroll horizontal. A medianoche el «hoy»
se actualiza solo.

### Fechas: cómo se calculan (y qué se corrigió)

Regla: todo va con fechas de **calendario puro** (`dnum`/`wdayN`: aritmética entera sobre el calendario
gregoriano, sin `Date` ni zonas horarias) y con **tablas propias** de días y meses en español. Nunca
`new Date('AAAA-MM-DD')` (se lee como UTC y en Chile/Argentina da el día anterior), nunca `toISOString()`
para «hoy», nunca sumar 24 h para «mañana», nada de `Intl`/`toLocaleDateString` para nombres. Hay una
prueba estática (`TestAuditoriaEstaticaDeFechas`) que impide que esos patrones vuelvan.

Auditoría de la versión anterior: «hoy», «mañana», «en N días», `dueLabel` y los días de la semana de fechas
válidas **ya eran correctos** en 7 zonas y alrededor de los cambios de hora (usaba getters locales). Lo que
fallaba y se corrigió: fechas inexistentes que saltaban al mes siguiente (el 31 de abril → 1 de mayo de 2027;
29/2/2027 → 1 de marzo; `1/13` → enero del año siguiente), `en 1 mes` desde el 31 de enero (3 de marzo),
`el 30` en febrero, «viernes 23» (dejaba «23» en el título y no usaba el número), «dentro de 3 días»
(ignorado: quedaba para hoy), «2 y media de la tarde» sin «a las», «al 24» al mover una reunión (movía
a mañana), el año siguiente sin avisar, el «qué hora es» y los nombres de día con el formato del locale
(`es-AR`: «p. m.», comas) y **ninguna fecha lejana mostraba el día de la semana** (ahora «vie 23 oct»).

### La app «Calendario»

- `overlay/usr/bin/infinity-calendario` (sh, como `infinity-panki`): `exec python3 -m
  infinity_shell.panki_app --calendario` (con una fecha `AAAA-MM-DD` como argumento, `--calendario=FECHA`).
- `overlay/usr/share/applications/infinity-calendario.desktop`: `Name=Calendario`,
  `Categories=Utility;Office;Calendar;`, palabras clave `calendario;agenda;reunion;…` (el buscador del
  lanzador la encuentra al escribir «calendario» o «agenda», y sale en la rejilla de apps). **No** lleva
  `StartupWMClass`: es la misma ventana de pAnkI (`org.infinity.Panki`) y el dock asocia ventana y app por
  esa clase; si la reclamara, rotularía la ventana de pAnkI como «Calendario».
- `overlay/usr/share/infinity/calendario.svg`: el icono (violeta `#b9a8ff` sobre el azul noche de pAnkI).
  Todo vive en `overlay/`, que `build.sh` copia entero con rsync: no hizo falta tocar `build.sh`.
- Contrato con la página, el tercero: **`window.pankiAbrirCalendario(fecha?)`** (global de `app.html`):
  abre la vista, opcionalmente en un día `'AAAA-MM-DD'`; un valor que no sea una fecha válida abre el mes
  actual. `panki_app.py` la llama (siempre con `json.dumps`, y tras validar la fecha) por
  `--calendario[=AAAA-MM-DD]` (se puede combinar con `--preguntar`; la pregunta devuelve la vista al chat) y por la acción de
  GApplication `calendario` (`gapplication action org.infinity.Panki calendario`), igual que `alternar`.
  Si pAnkI ya está abierta se **reutiliza** (una sola ventana, un solo almacenamiento); si la página aún
  carga, la petición espera al primer `load-changed == FINISHED`. `app.html#calendario` también la abre.

### Persistencia

Los eventos viven en el `localStorage` de la `NetworkSession` persistente de WebKit, en
`~/.local/share/infinity/panki/webkit/{datos,cache}` (`$XDG_DATA_HOME` si existe; `panki_app._ruta_datos_web`).
La ruta no depende de nada que cambie entre arranques (solo de `HOME`), y la ISO guarda el HOME en la
partición «writable» de casper (modo `persistent`), así que sobrevive al apagado. Probado con Chromium
(contexto persistente: agendar, cerrar, reabrir, siguen ahí).

### Pruebas

`shell/tests/test_panki_calendario.py` (sin pantalla ni navegador: archivos, lanzador, `.desktop`, línea de
órdenes, JS inyectado, auditoría estática de fechas, búsqueda del lanzador y acción GIO con GTK) y
`shell/tests/test_panki_calendario_web.py` (Playwright + Chromium: 7 zonas a las 23:30 y 00:30, cambios de
hora, la frase del usuario y variantes en modos Rápido y Profundo, la vista, formulario, persistencia). Las
de Playwright **se saltan** si no hay Playwright de Python o Chromium (`PANKI_CHROMIUM` indica la ruta).

### Lo que no se pudo verificar aquí

- **WebKitGTK 6.0 real** (como arriba): la página se probó en Chromium. Que `localStorage` de una página
  `file://` persista en la `NetworkSession` de WebKitGTK entre arranques, y la entrega de
  `--calendario`/`calendario` a una ventana ya abierta (D-Bus de GApplication), no se ejecutaron de verdad;
  sí sus piezas puras y, con GTK real, que la acción existe y reutiliza la ventana.
- **Escritura inmediata de `localStorage`**: Chromium la retrasa unos segundos; WebKit tiene su propio
  retraso (no medido aquí). Un apagado brusco de la máquina justo después de agendar podría perder
  lo último; cerrar sesión o apagar con normalidad lo guarda.
