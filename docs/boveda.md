# Contraseñas (la bóveda): modelo de seguridad

«Contraseñas» (`infinity-boveda`) es el gestor de contraseñas personal de Infinity OS: la del correo, la del router,
las webs, el banco... Se abre con una **contraseña maestra** y todo lo demás vive cifrado en un solo fichero.
Este documento dice qué protege, cómo, con qué parámetros exactos y, sobre todo, **qué no protege**.

- Lógica (formato, cifrado, ficheros, generador, fortaleza, portapapeles temporal): `shell/infinity_shell/boveda.py`
  (sin GTK; se prueba sin pantalla en `shell/tests/test_boveda.py`).
- Ventana GTK 4: `shell/infinity_shell/boveda_app.py` (`shell/tests/test_boveda_ventana.py`).
- Comando: `overlay/usr/bin/infinity-boveda`; lanzador: `overlay/usr/share/applications/infinity-boveda.desktop`;
  icono: `overlay/usr/share/infinity/boveda.svg`.
- Dependencia nueva: **`python3-cryptography`** (AES-GCM), añadida a `build/packages.list`. Todo lo demás es la
  biblioteca estándar de Python (`hashlib.scrypt`, `secrets`, `hmac`).
- Se abre desde el lanzador (el botón de la llave, junto al engranaje y a los botones de apagado) o con `infinity-boveda`.

## Qué hace y qué no

Hace: guardar entradas (servicio, usuario o correo, contraseña, URL o IP, notas, categoría), buscarlas, copiar la
contraseña al portapapeles durante 25 s, generar contraseñas aleatorias, cambiar la contraseña maestra y exportar
una copia cifrada.

No hace: **no usa la red** (ni sincronización, ni comprobación de filtraciones, ni telemetría), **no abre URLs**
(la dirección es solo un texto), no rellena formularios del navegador, no tiene recuperación de la contraseña
maestra, no escribe nada en registros (`print`, `logging`) y no pasa secretos por variables de entorno ni por `argv`.
Una prueba (`TestSinRedNiRegistros`) comprueba esto mirando el código.

> **Si olvidas la contraseña maestra, tus contraseñas no se pueden recuperar.** No hay clave de rescate ni
> puerta trasera: nadie puede abrir el fichero sin ella, tampoco quien mantiene Infinity OS. Si ya no la tienes y
> quieres empezar de cero, borra `~/.local/share/infinity/boveda/` (se pierde todo lo guardado).

## Dónde están los datos

`~/.local/share/infinity/boveda/` (respeta `XDG_DATA_HOME` si es una ruta absoluta):

| Fichero | Qué es |
|---|---|
| `boveda.enc` | La bóveda, cifrada. Permisos **0600**. |
| `boveda.enc.bak` | Copia de la versión anterior (0600). Se usa si el principal se daña. |
| `boveda.enc.corrupta` | Solo si hubo que restaurar desde la `.bak`: el principal dañado, apartado (0600). |

La carpeta es **0700**. El HOME va en la partición de guardado de la ISO, así que sobrevive al apagado (en una sesión
en vivo *sin* partición de guardado, la bóveda se pierde al apagar: exporta una copia).

## Formato del fichero (versión 1)

```
cabecera (62 bytes, todo en claro pero AUTENTICADO)
    magia "INFBOVED" (8) · versión u16 (=1) · id de KDF u8 (=1, scrypt) · log2(N) u8 · r u8 · p u8
    · sal aleatoria (16) · verificador (32)
nonce aleatorio (12 bytes, NUEVO en cada guardado)
texto cifrado + etiqueta GCM (16 bytes)
```

- **Cifrado**: AES-256-GCM (`cryptography.hazmat.primitives.ciphers.aead.AESGCM`). La **cabecera entera es el AAD**
  (datos autenticados): cambiar un solo byte de la cabecera, del nonce o del cifrado hace fallar la etiqueta y no se
  devuelve ningún dato. Las pruebas recorren cada byte de la cabecera y del nonce y muchas posiciones del cifrado.
- **Derivación de la clave**: `hashlib.scrypt` con **N=2**16, r=8, p=1** (64 MiB de memoria, del orden de 0,3 s),
  clave de 32 bytes, a partir de la contraseña maestra **normalizada NFKC y en UTF-8** (así «é» da la misma clave
  se teclee como se teclee). Sal de 16 bytes aleatorios.
- **Nonce**: 12 bytes de `os.urandom`, nuevo en cada guardado; si por casualidad saliera igual que el anterior,
  se vuelve a sortear. La sal **solo cambia al cambiar la contraseña maestra** (la clave se conserva en memoria
  mientras la bóveda está abierta, así que guardar no necesita volver a ejecutar scrypt).
- **Contenido** (JSON dentro del cifrado): `{"v": 1, "entradas": [...]}`; cada entrada con `id` (`secrets.token_hex`),
  `servicio`, `usuario`, `contrasena`, `url`, `notas`, `categoria` (Correo, Router y red, Web, Banco, Otros),
  `creada` y `modificada`. Se valida tipo y longitud **al leer y al escribir** (servicio 120, usuario 254, contraseña
  1024, URL/IP 500, notas 5000 caracteres; 2000 entradas; 4 MiB de texto). Un campo desconocido, un `id` repetido o
  una categoría inventada hacen que el fichero se trate como dañado.
- **Relleno**: el JSON se rellena con espacios hasta un múltiplo de 1024 bytes, para que el tamaño del fichero no diga
  cuántas entradas hay (solo aproximadamente).
- **Verificador**: `HMAC-SHA256(clave, "infinity-boveda/v1/comprobacion")`. Existe para poder distinguir
  *contraseña equivocada* (`ClaveIncorrecta`) de *fichero dañado* (`BovedaDanada`): con GCM a secas las dos cosas
  fallan igual. No da ventaja a un atacante: comprobar una contraseña ya exigía el mismo scrypt, y HMAC no revela la clave.
  Efecto secundario honesto: una alteración de la sal, de los parámetros o del verificador se ve como «contraseña
  incorrecta» (la clave que sale es otra); por eso, tras 3 fallos seguidos, la ventana ofrece la copia de seguridad.

### Qué se valida al abrir (defensa frente a un fichero manipulado)

Antes de ejecutar scrypt se comprueban magia, versión y la cabecera: **N entre 2**14 y 2**20, r entre 8 y 16, p entre 1
y 4 y memoria (128·N·r) como mucho 1 GiB**. Un fichero que pida más (denegación de servicio) o menos (rebajar la
protección) se rechaza sin derivar nada. Magia o versión desconocidas dan un error claro (`VersionNoSoportada`), no
«contraseña incorrecta». Los ficheros de más de ~4 MiB no se leen.

## Escritura a prueba de cortes

1. Se asegura la carpeta (0700, tuya, **no** un enlace simbólico) y se trabaja con su descriptor (`dir_fd`), así que
   no se puede cambiar por otra entre la comprobación y el uso.
2. Se escribe un temporal en la misma carpeta (`O_CREAT|O_EXCL|O_NOFOLLOW`, modo **0600**) y se hace `fsync`.
3. Si el principal actual es válido con la clave abierta, se escribe su contenido como `boveda.enc.bak` (también
   atómico). *Primero* la `.bak` y *después* el principal: en cualquier instante hay al menos una versión entera.
4. `os.replace` del temporal sobre el principal y `fsync` de la carpeta.
5. Si algo falla (disco lleno, corte, excepción), el temporal se borra y el principal queda como estaba; la memoria
   no se adelanta al disco. Los temporales de un corte anterior (de más de un minuto) se limpian en el siguiente guardado.

Si el principal está dañado, la ventana ofrece **«Abrir la copia de seguridad»**; al abrirla se restaura el principal
(el dañado se aparta como `.corrupta`). Un principal dañado **no** se copia sobre una `.bak` buena al guardar.

Al **cambiar la contraseña maestra** se re-cifra todo con sal y clave nuevas y la `.bak` pasa a ser una copia con la
contraseña nueva (así la vieja deja de valer también ahí); `.corrupta`, si existe, se borra por lo mismo.

**Exportar copia cifrada…** copia el fichero tal cual está en disco (nada se descifra) al sitio que elijas, con
permisos 0600 y de forma atómica; no puede sobrescribir la propia bóveda. Para usarla, copia ese fichero como
`~/.local/share/infinity/boveda/boveda.enc` y ábrela con la contraseña maestra con la que se hizo.

## La contraseña maestra

- Al crearla: **mínimo 8 caracteres**, no vacía ni de espacios, escrita **dos veces**, con aviso visible de que no se
  puede recuperar. Máximo 256 caracteres.
- **Medidor de fortaleza** (`evaluar_fortaleza`): estimación por longitud y variedad de caracteres, con penalización de
  repeticiones (`aaaa`), secuencias (`abcd`, `4321`), filas del teclado (`qwerty`), palabras muy comunes (también con
  «leet»: `p4ssw0rd`), años y dígitos seguidos, y un descuento prudente para tiradas largas de letras (se parecen a
  palabras). **Es orientativa, no una garantía**: no lleva un diccionario grande, así que no reconoce una palabra poco
  común con sustituciones (`Tr0ub4dor&3` sale «Buena» aunque no lo sea). Una frase larga y aleatoria vale más que
  cualquier medidor.
- **Freno a los intentos**: tras fallos seguidos la ventana hace esperar **1, 2, 4, 8, 16 y 30 s** (30 como máximo)
  antes de dejar reintentar; el coste de scrypt ya frena la fuerza bruta contra el fichero. La cuenta es por
  proceso: cerrar y volver a abrir la aplicación la reinicia (no es una defensa contra quien puede ejecutar código;
  es contra el curioso que tiene la pantalla delante).

## Generador

`generar_contrasena` usa `secrets.SystemRandom` (longitud 8–64; mayúsculas, minúsculas, números, símbolos; «evitar
caracteres ambiguos» quita `0 O l 1 I`). Garantiza **al menos un carácter de cada clase elegida** y baraja el
resultado. El «Generar» del editor deja la contraseña visible un momento para que puedas anotarla antes de guardar.

## Bloqueo

- **Siempre arranca bloqueada**: un proceso nuevo no tiene ni clave ni entradas. Cerrar la ventana cierra la aplicación.
- **Por inactividad a los 3 minutos**: cualquier tecla, clic, movimiento del ratón o rueda dentro de la ventana
  reinicia la cuenta (controladores en fase de captura, así los ven aunque un campo los consuma). El botón
  **Bloquear** y **Ctrl+L** bloquean en el acto.
- **Al suspender**: se suscribe a la señal `PrepareForSleep` de `org.freedesktop.login1` en el bus del sistema; si no hay
  bus, simplemente no vigila. Límite: la aplicación no toma un «inhibidor de suspensión», así que en el instante de
  suspender el bloqueo puede ejecutarse justo al despertar (antes de que nadie pueda tocar nada, pero no antes de
  dormir). La pantalla de bloqueo del sistema (`infinity-lock`) **no** bloquea la bóveda por sí sola.
- Al bloquear: se pone a cero el `bytearray` de la clave, se sueltan todas las referencias (clave, entradas, filas de
  la lista), se vacían todos los widgets (lista, detalle, editor, formularios, búsqueda), se limpia el portapapeles si
  aún contiene lo copiado y se llama a `gc.collect()`.

## Portapapeles

«Copiar contraseña» y «Copiar usuario» ponen el texto en el portapapeles y avisan **«Copiado; se borrará en 25 s»**.
A los 25 s se borra **solo si sigue conteniendo lo que copiaste** (se compara; se guarda únicamente la huella SHA-256,
no el texto). Si copiaste otra cosa mientras tanto, no se toca. También se borra al bloquear o cerrar, si sigue siendo
lo nuestro. El contenido se ofrece con el tipo `x-kde-passwordManagerHint: secret`, que respetan varios
gestores de historial del portapapeles.

## Límites y riesgos (lo que NO se puede garantizar)

1. **La memoria de Python.** Las cadenas son inmutables y el recolector de basura puede dejar copias de la contraseña
   maestra, de las contraseñas guardadas y de lo que se escribió (también en GTK, en la caché de Pango, etc.). Poner a
   cero la clave que guardamos y `gc.collect()` reducen la exposición, **no la eliminan**. El proceso se marca como no
   «dumpable» (`PR_SET_DUMPABLE=0`) y sin volcados de memoria (`RLIMIT_CORE=0`), para que otro proceso del mismo usuario
   no lo lea con `ptrace` ni `/proc/PID/mem`; **root** sí puede leer la memoria, y la memoria puede ir a swap/zram
   (Infinity OS usa swap comprimido en RAM) o a un fichero de hibernación. Los campos de contraseña maestra son
   `Gtk.PasswordEntry` (su buffer, `GtkPasswordEntryBuffer`, es de memoria protegida que se pone a cero al liberarla);
   los del editor de entradas son campos normales, y `get_text()` siempre devuelve una copia en una cadena de Python.
2. **El portapapeles.** En Wayland cualquier cliente con foco puede leer el portapapeles, y un gestor de historial
   (`cliphist` y similares) puede guardar lo copiado: el marcador de «secreto» solo lo respetan los que lo implementan.
   En esos 25 s la contraseña está al alcance de otros programas. (Infinity OS solo trae `wl-clipboard`, sin historial.)
3. **Quien tenga tu sesión abierta y la bóveda desbloqueada** ve todo. El bloqueo por inactividad es una red de
   seguridad, no una defensa contra alguien con acceso físico en esos 3 minutos. Un programa que se ejecute con tu
   usuario puede, además, esperar a que teclees la contraseña maestra (un registrador de teclas) o leer el fichero:
   el cifrado protege el fichero **en reposo**, no un equipo ya comprometido.
4. **Capturas de pantalla.** Mientras una contraseña está a la vista (ojo) cualquier captura o compartición de
   pantalla la incluye. Se oculta sola a los 20 s o al cambiar de entrada.
5. **El disco.** El sistema de ficheros (journal, copy-on-write, SSD con nivelación de desgaste) puede conservar
   bloques de versiones antiguas del fichero cifrado, incluido el que usaba una contraseña maestra anterior. Cambiar la
   contraseña maestra protege el fichero actual y la `.bak`, pero **no borra de forma segura** lo que quedó en el disco.
6. **Fuerza bruta contra el fichero.** Quien consiga una copia de `boveda.enc` puede probar contraseñas sin límite en su
   máquina; solo scrypt (≈0,3 s y 64 MiB por intento en un equipo normal, mucho menos con hardware especializado) lo
   frena. **La seguridad depende de que la contraseña maestra sea larga e impredecible.**
7. **Concurrencia.** Hay una sola instancia (`GApplication`), pero no se bloquea el fichero (`flock`): dos procesos que
   escribieran a la vez (p. ej. un `infinity-boveda` lanzado con otro `XDG_DATA_HOME`) es una situación no soportada.
8. **No hay auditoría externa** de este código. Se apoya en primitivas estándar (AES-GCM de `cryptography`, scrypt de
   OpenSSL vía `hashlib`) y no inventa criptografía, pero es software nuevo: conserva también tus contraseñas
   importantes en otro sitio (una copia en papel, por ejemplo).

## Interfaz

Ventana «Contraseñas» de ~880×600, clase de ventana `org.infinity.Boveda` (para las reglas de Hyprland), una sola
instancia (abrirla otra vez trae la ventana al frente).

- **Pantalla bloqueada**: «Crea tu contraseña maestra» (dos campos, medidor, aviso de no-recuperación) o «Contraseñas»
  con un campo de contraseña (con el ojo de mostrar) y Enter para entrar. El scrypt corre en un hilo: se ve
  «Comprobando…» y los campos quedan sin poder editarse mientras tanto.
- **Pantalla desbloqueada**: categorías con contadores (Todas, Correo, Router y red, Web, Banco, Otros), buscador,
  lista y detalle (contraseña oculta con diez puntos fijos —no se adivina su longitud—, mostrar/ocultar, copiar,
  URL/IP, notas), Editar y Borrar (con confirmación en la propia pantalla), «+ Nueva entrada», **Bloquear** y menú
  (Cambiar contraseña maestra, Exportar copia cifrada, Acerca de).
- **Atajos**: Ctrl+F busca, Ctrl+N nueva, Ctrl+L bloquea, Esc limpia la búsqueda (o cierra el editor).
- La búsqueda mira nombre, usuario, URL/IP, notas y categoría, sin mayúsculas ni acentos; **nunca la contraseña**.
