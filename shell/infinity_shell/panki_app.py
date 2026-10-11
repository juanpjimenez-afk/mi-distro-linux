"""pAnkI, el agente virtual de Infinity OS: la ventana de chat (infinity-panki).

La lógica del agente está en panki_nucleo.py (clave, cliente de Gemini, bucle) y panki_herramientas.py (lo que
puede hacer en el equipo). Esta ventana solo conversa, pide permiso y enseña lo que pasa.

    python3 -m infinity_shell.panki_app [--preguntar TEXTO]   (lo lanza el comando infinity-panki)

Reglas que esta ventana cumple (docs/panki-diseno.md):
  * El agente corre en un hilo aparte. NINGÚN widget se toca desde ese hilo: todo lo visual va por
    GLib.idle_add. Cuando el agente necesita permiso, su hilo se queda esperando en un threading.Event y la
    tarjeta de aprobación se pone en pantalla desde el hilo principal.
  * Cerrar la ventana con trabajo en curso, o pulsar Parar, responde "no" a la aprobación pendiente: el hilo
    nunca se queda colgado esperando a alguien que ya no está.
  * El texto del usuario se muestra SIEMPRE como texto plano. El de pAnkI pasa por panki_texto.a_pango
    (escapado) y además se valida con Pango antes de usarlo como marcado.
  * Nada que escriba el modelo se pinta como si fuera de la interfaz: lo suyo es texto que fluye, sin marcos
    ni reglas, y todo lo que viene de él (nombres de herramienta, argumentos, resúmenes) se limpia antes.
  * La tarjeta de permiso (borde fino de acento y la etiqueta fija "Permiso") es lo único con aspecto de
    tarjeta. Sus botones nacen desactivados y se arman a los 700 ms; Escape deniega; si nadie contesta en
    3 minutos se deniega sola.
  * La clave de Gemini no se muestra ni se escribe en ningún registro, ni se envía por el chat.

Ocultar y cerrar (decisión de esta ventana, el documento no la fija):
  * `alternar` (la burbuja y Super + N) con la ventana visible la OCULTA: se revocan los permisos "en esta
    conversación" y se deniega lo que esté pendiente de aprobar; la conversación sigue. Con la ventana oculta
    la muestra. Si llega una tarjeta de permiso con la ventana oculta, se muestra sola.
  * La X con el agente TRABAJANDO cancela, responde "no" a lo pendiente y cierra de verdad (la aplicación
    termina; el hilo es daemon y no la retiene).
  * La X SIN trabajo en curso oculta la ventana y deja la aplicación viva para que la burbuja la abra al
    instante, pero la conversación y sus permisos se descartan (no se guarda nada al cerrar).
"""
import os
import re
import sys
import threading
import time
import traceback
import unicodedata

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "org.infinity.Panki"
MAX_PREGUNTA = 4000  # caracteres de --preguntar; más es casi seguro un error del llamador
ALTO_MAX_DETALLE = 170  # px del detalle de una aprobación antes de que se desplace
ALTO_MAX_DETALLE_ALTO = 280  # ... cuando el riesgo es alto: se quiere ver más antes de decidir
ANCHO_DETALLE = 368  # px que tiene el detalle en una ventana de 460 de ancho (para medir su alto)
MAX_COLA = 5  # mensajes que esperan su turno (la ventana ocupada, o sin clave todavía)
RIESGOS = ("bajo", "medio", "alto")
ETIQUETA_REGISTRO = "~/.cache/infinity/panki.log"
RETARDO_ARMADO_MS = 700  # los botones de una tarjeta de permiso no responden hasta pasado este tiempo
ANTIGUEDAD_S = 30  # a partir de aquí la tarjeta dice cuánto lleva esperando
CADUCIDAD_S = 180  # sin respuesta en este tiempo, se deniega sola

# Lo que hace cada herramienta, en una palabra para la línea de actividad. Todo nombre que no esté aquí
# (lo escribe el modelo) se muestra como "herramienta desconocida".
VERBOS = {
    "info_sistema": "Mirando el equipo",
    "ver_ajustes": "Mirando los ajustes",
    "buscar_apps": "Buscando apps",
    "listar_directorio": "Mirando",
    "buscar_archivos": "Buscando archivos",
    "leer_archivo": "Leyendo",
    "proponer_organizacion": "Preparando un plan",
    "aplicar_organizacion": "Organizando",
    "deshacer_ultima_organizacion": "Deshaciendo la última organización",
    "crear_carpeta": "Creando",
    "mover_archivo": "Moviendo",
    "abrir_app": "Abriendo",
    "abrir_url": "Abriendo",
    "cambiar_ajuste": "Cambiando",
    "ejecutar_comando": "Ejecutando",
}
DESCONOCIDA = "herramienta desconocida"
# Estas no piden permiso nunca (leen datos del equipo y se los envían a Google): sin tarjeta no es "permiso previo".
SIN_APROBACION = ("info_sistema", "ver_ajustes", "buscar_apps")

USO = """Uso: infinity-panki [--preguntar TEXTO]

  (sin opciones)       abre pAnkI
  --preguntar TEXTO    abre pAnkI, o usa la ventana que ya está abierta, y le envía el texto
  -h, --help           muestra esta ayuda
"""

# Lo que el usuario tiene que saber antes de pegar su clave (docs/panki-diseno.md, apartado Privacidad).
# Frases cortas y exactas: lo que se envía, lo que se hace sin preguntar y lo que se guarda.
AVISO_PRIVACIDAD = (
    "pAnkI usa Gemini, el modelo de Google, con tu propia clave.",
    "Se envía a Google (Gemini): cada mensaje, lo que pAnkI ve con sus herramientas, tu nombre de usuario, "
    "tu carpeta personal y la fecha.",
    "Sin pedirte permiso, pAnkI puede leer y enviar a Google datos del equipo: sistema operativo, memoria, "
    "disco, batería, tus ajustes y las apps instaladas.",
    "Los nombres de los archivos de una carpeta y el contenido de un archivo solo los ve si tú lo permites. "
    "«En esta conversación» amplía ese permiso hasta que lo revoques, ocultes la ventana o empieces otra "
    "conversación.",
    "Abrir una app o una web, o ejecutar un comando, puede lanzar programas. Se te pide permiso antes, salvo "
    "lo que ya hayas permitido en esta conversación.",
    "La conversación no se guarda: al cerrar la ventana se pierde. El registro para deshacer guarda en "
    "disco las rutas de los archivos movidos.",
    "Tu clave se guarda solo en este equipo (~/.config/infinity/panki/clave, con permisos privados) o se toma "
    "de GEMINI_API_KEY. pAnkI nunca te pedirá la clave por el chat.",
    "No hay telemetría. Fuera del equipo solo sale lo anterior.",
    "Consigue una clave en aistudio.google.com/apikey.",
)

# Para cambiar la clave o cuando Gemini la rechaza: lo esencial.
AVISO_CORTO = (AVISO_PRIVACIDAD[1], AVISO_PRIVACIDAD[6])

BIENVENIDA = ("Pregúntame por tu equipo, o pídeme que ordene archivos o cambie un ajuste. Antes de cambiar "
              "algo te pido permiso. Lo que veo con mis herramientas se envía a Google.")
PIE_GOOGLE = "Se envía a Google (Gemini)"
AVISO_CLAVE_EN_ENTRADA = "Eso parece una clave de API: pégala solo en el botón Clave…"


# ------------------------------------------------------------------------------- registro de fallos
def ruta_registro():
    """~/.cache/infinity/panki.log (o $XDG_CACHE_HOME/infinity/panki.log)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "infinity", "panki.log")


_CLAVE_API = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")
_CLAVE_EN_ENTRADA = re.compile(r"AIza[0-9A-Za-z_\-]{30,}")


def parece_clave(texto):
    """¿Contiene el texto algo con forma de clave de Gemini? Entonces no se envía por el chat."""
    return bool(_CLAVE_EN_ENTRADA.search(texto or ""))


def sin_secretos(texto):
    """Quita de un texto lo que parezca una clave de Gemini, o el valor de GEMINI_API_KEY.

    Es una segunda barrera: el núcleo ya no debería poner la clave en ningún mensaje.
    """
    texto = _CLAVE_API.sub("[clave]", str(texto))
    valor = os.environ.get("GEMINI_API_KEY", "")
    if len(valor) >= 8:
        texto = texto.replace(valor, "[clave]")
    return texto


def registrar(texto):
    """Añade el texto, con la hora, al registro (permisos 0600). Nunca lanza: si no se puede, no hay otro sitio."""
    try:
        ruta = ruta_registro()
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        try:
            if os.path.getsize(ruta) > 512 * 1024:  # que no crezca sin límite
                os.replace(ruta, ruta + ".1")
        except OSError:
            pass
        fd = os.open(ruta, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {sin_secretos(texto).rstrip()}\n")
    except OSError:
        pass


# ------------------------------------------------------------------------------- texto
# Lo que no se ve o cambia el orden en que se lee: controles, direcciones de escritura (U+202E y compañía),
# espacios de ancho cero, rellenos que parecen un hueco (braille en blanco, hangul) y selectores de variación.
_INVISIBLES = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f؜ᅟᅠ᠎​‎‏‪-‮"
    "⁠-⁯⠀ㅤ︀-️﻿ﾠ￹-￻\U000e0100-\U000e01ef]")
# Marcas combinantes de diacríticos: tras NFC, las que quedan son las que tapan el carácter de al lado.
_COMBINANTES = re.compile("[̀-ͯ҃-҉᪰-᫿᷀-᷿⃐-⃿︠-︯]")


def texto_visible(texto):
    """El texto con lo invisible, lo que invierte el orden y lo que tapa a otros caracteres sustituido por su código.

    Se normaliza a NFC antes, así las tildes normales (á) no se tocan. Los saltos de línea y los tabuladores
    se respetan.
    """
    texto = unicodedata.normalize("NFC", str(texto))
    return _COMBINANTES.sub(lambda m: f"‹U+{ord(m.group()):04X}›",
                            _INVISIBLES.sub(lambda m: f"‹U+{ord(m.group()):04X}›", texto))


def una_linea(texto, maximo=80):
    """Una línea sin trucos para la zona de actividad del sistema: limpia, sin saltos de línea y recortada por el medio.

    Recorta por el medio para que se vea tanto el principio como el final (la extensión de un archivo, el destino).
    """
    t = re.sub(r"\s+", " ", texto_visible(texto).replace("\n", " ⏎ ")).strip()
    if len(t) > maximo:
        cabeza = (maximo - 1) // 2
        t = t[:cabeza] + "…" + t[len(t) - (maximo - 1 - cabeza):]
    return t


_RESTOS_DE_MARCO = re.compile("[─-▟]")
_REGLA = re.compile(r"[-*_=─-╿ ]{3,}")


def filtrar_texto_modelo(texto):
    """El texto del modelo, sin lo que podría hacerse pasar por la interfaz del sistema.

    Fuera las reglas horizontales, las vallas de código y las marcas de cita (que dibujan marcos y barras), y los
    caracteres de dibujo de cajas. Queda texto que fluye, con negrita, cursiva, listas y código en línea.
    """
    lineas = []
    for linea in texto_visible(texto).split("\n"):
        s = linea.strip()
        if s.startswith("```") or s.startswith("~~~") or _REGLA.fullmatch(s):
            continue
        lineas.append(re.sub(r"^\s{0,3}>+\s?", "", linea))
    t = _RESTOS_DE_MARCO.sub("", "\n".join(lineas))
    return re.sub(r"\n{3,}", "\n\n", t).strip()


def interpretar_argumentos(args):
    """Interpreta la línea de órdenes (sin el nombre del programa).

    Devuelve (pregunta, ayuda, error). `--preguntar` se queda con TODO lo que va detrás, aunque empiece por
    un guion: es texto del usuario, no opciones.
    """
    if not args:
        return None, False, None
    primero = args[0]
    if primero in ("-h", "--help", "--ayuda"):
        return None, True, None
    if primero == "--preguntar":
        texto = " ".join(args[1:])
    elif primero.startswith("--preguntar="):
        texto = primero.split("=", 1)[1]
    else:
        return None, False, f"Opción desconocida: {primero}"
    texto = texto.strip()
    return (texto[:MAX_PREGUNTA] or None), False, None


def juntar_pregunta(argv):
    """['prog', '--preguntar', 'a', 'b'] -> ['prog', '--preguntar=a b'].

    GApplication reconoce sus propias opciones (--gapplication-service...) en cualquier argumento; así el
    texto del usuario viaja en un solo argumento que empieza por --preguntar= y nunca se confunde con ellas.
    """
    if "--preguntar" in argv[1:]:
        i = argv.index("--preguntar", 1)
        return argv[:i] + ["--preguntar=" + " ".join(argv[i + 1:])]
    return argv


# ------------------------------------------------------------------------------- estilo
# Minimalista: una sola tipografía (la del sistema) en dos tamaños (14 y 12 px), un solo color de acento (el
# violeta de la burbuja de pAnkI) y nada de sombras ni avatares. El riesgo es una palabra y un punto de color.
ESTILO = """
window.panki-ventana { background-color: rgba(18, 22, 44, 0.94); color: #e9ebf7; }

headerbar.panki-cabecera {
  min-height: 30px;
  padding: 0 6px 0 16px;
  background: none;
  border: none;
  box-shadow: none;
}
headerbar.panki-cabecera windowcontrols button { opacity: 0.5; }
headerbar.panki-cabecera windowcontrols button:hover { opacity: 1; }
label.panki-marca { color: #b9a8ff; font-size: 14px; font-weight: 600; }
button.panki-icono {
  background: none;
  border: none;
  box-shadow: none;
  min-height: 0;
  min-width: 0;
  padding: 4px 7px;
  opacity: 0.4;
}
button.panki-icono:hover { opacity: 1; }
button.panki-icono:disabled { opacity: 0.2; }

.panki-conversacion { padding: 10px 20px 8px 20px; }
.panki-bienvenida { padding: 48px 4px 0 4px; }
.panki-bienvenida label { color: rgba(233, 235, 247, 0.6); }

.panki-usuario { padding-left: 10px; border-left: 1px solid rgba(233, 235, 247, 0.2); }
.panki-usuario label { color: rgba(233, 235, 247, 0.6); }
label.panki-actividad, label.panki-nota { font-size: 12px; color: rgba(233, 235, 247, 0.45); }
label.panki-actividad-fallo { color: rgba(255, 140, 140, 0.8); }
.panki-error label { color: #ff9b9b; }
label.panki-error-titulo { font-size: 12px; font-weight: 600; }

.panki-tarjeta {
  border: 1px solid rgba(185, 168, 255, 0.55);
  border-radius: 8px;
  padding: 12px 14px;
  background-color: rgba(255, 255, 255, 0.03);
}
label.panki-permiso { font-size: 12px; font-weight: 700; color: #b9a8ff; }
label.panki-riesgo-texto, label.panki-estado-tarjeta, label.panki-resuelta { font-size: 12px; color: rgba(233, 235, 247, 0.7); }
label.panki-punto { font-size: 12px; color: rgba(233, 235, 247, 0.55); }
.panki-riesgo-medio label.panki-punto { color: #f2b84b; }
.panki-riesgo-alto label.panki-punto { color: #ff6b6b; }
label.panki-tarjeta-titulo { font-weight: 600; }
label.panki-tarjeta-texto { font-size: 12px; color: rgba(233, 235, 247, 0.75); }
label.panki-mas { font-size: 12px; color: #b9a8ff; }
label.panki-detalle {
  font-family: monospace;
  font-size: 12px;
  background-color: rgba(0, 0, 0, 0.3);
  border-radius: 4px;
  padding: 8px;
}
scrolledwindow.panki-desplazable scrollbar { opacity: 1; }
scrolledwindow.panki-desplazable scrollbar slider { background-color: rgba(185, 168, 255, 0.6); min-width: 6px; }

button.panki-decision {
  background-image: none;
  background-color: transparent;
  border: 1px solid rgba(233, 235, 247, 0.4);
  border-radius: 6px;
  box-shadow: none;
  padding: 6px 12px;
  min-height: 0;
}
button.panki-decision label { font-size: 14px; }
button.panki-decision:hover { background-color: rgba(233, 235, 247, 0.1); }
button.panki-decision:disabled { opacity: 0.4; }
button.panki-concesion {
  background: none;
  border: none;
  box-shadow: none;
  padding: 2px 0;
  min-height: 0;
}
button.panki-concesion label { font-size: 12px; color: rgba(233, 235, 247, 0.65); }
button.panki-concesion:hover label { color: #e9ebf7; }
button.panki-concesion:disabled { opacity: 0.4; }
button.panki-texto {
  background: none;
  border: none;
  box-shadow: none;
  padding: 0 4px;
  min-height: 0;
}
button.panki-texto label { font-size: 12px; color: rgba(233, 235, 247, 0.7); }
button.panki-texto:hover label { color: #b9a8ff; }

.panki-pie { padding: 4px 20px 10px 20px; }
label.panki-estado, label.panki-pie-google, label.panki-pie-permisos { font-size: 12px; color: rgba(233, 235, 247, 0.45); }
label.panki-aviso { font-size: 12px; color: #b9a8ff; }
label.panki-marcador { color: rgba(233, 235, 247, 0.4); }
textview.panki-entrada, textview.panki-entrada text {
  background: none;
  color: #e9ebf7;
  caret-color: #b9a8ff;
  font-size: 14px;
}
scrolledwindow.panki-caja-entrada {
  border: none;
  border-bottom: 1px solid rgba(233, 235, 247, 0.22);
  border-radius: 0;
  background: none;
}
scrolledwindow.panki-caja-entrada:focus-within { border-bottom-color: #b9a8ff; }
entry.panki-clave, entry.panki-clave text {
  background: none;
  color: #e9ebf7;
  border-radius: 0;
  box-shadow: none;
}
entry.panki-clave { border: none; border-bottom: 1px solid rgba(233, 235, 247, 0.3); }
"""

_estilos_cargados = set()


def _cargar_estilo(pantalla):
    """Carga el CSS de pAnkI una vez por pantalla, con prioridad de aplicación."""
    if pantalla is None or id(pantalla) in _estilos_cargados:
        return
    proveedor = Gtk.CssProvider()
    if hasattr(proveedor, "load_from_string"):  # GTK 4.12 o más; antes, load_from_data
        proveedor.load_from_string(ESTILO)
    else:
        proveedor.load_from_data(ESTILO.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(pantalla, proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _estilos_cargados.add(id(pantalla))


# ------------------------------------------------------------------------------- módulos del agente
def _nucleo_real():
    from . import panki_nucleo
    return panki_nucleo


def _a_pango_real():
    try:
        from . import panki_texto
        return panki_texto.a_pango
    except ImportError:
        return None  # sin conversor, el texto de pAnkI sale como texto plano


def _clases_de_error(nucleo):
    """La clase de error del núcleo (ErrorPanki). Se aceptan también las grafías anteriores."""
    clases = []
    for nombre in ("ErrorPanki", "ErrorpAnkI", "ErrorPankI", "ErrorPAnkI"):
        c = getattr(nucleo, nombre, None)
        if isinstance(c, type) and issubclass(c, Exception) and c not in clases:
            clases.append(c)
    return tuple(clases)


class _Espera:
    """Una aprobación pendiente. El hilo del agente espera aquí hasta que alguien responde.

    La primera respuesta gana: si el usuario pulsa Permitir y a la vez se cierra la ventana, el hilo
    recibe una sola respuesta y el resto se ignora.
    """

    def __init__(self, solicitud):
        self.solicitud = solicitud
        self.respuesta = None
        self.tarjeta = None
        self.armada = False  # los botones solo valen cuando ha pasado RETARDO_ARMADO_MS
        self.creada = time.monotonic()
        self.id_armado = 0
        self.id_reloj = 0
        self._evento = threading.Event()
        self._candado = threading.Lock()

    def responder(self, valor):
        with self._candado:
            if self.respuesta is not None:
                return False
            self.respuesta = valor
        self._evento.set()
        return True

    def esperar(self):
        self._evento.wait()
        return self.respuesta


# ------------------------------------------------------------------------------- ventana
class VentanaPanki(Gtk.ApplicationWindow):
    """La ventana de chat.

    `fabrica_agente(aprobar) -> agente` crea el agente (por defecto, panki_nucleo.crear_agente); el agente
    tiene `enviar(texto, alevento)`, `cancelar()` y `reiniciar()`, y opcionalmente `permisos_activos()` y
    `revocar_permisos()`. Las pruebas pasan un agente falso. `nucleo` aporta existe_clave, guardar_clave,
    borrar_clave y la clase de error; `a_pango` convierte el texto de pAnkI en marcado.
    """

    def __init__(self, app, fabrica_agente=None, nucleo=None, a_pango=None):
        super().__init__(application=app, title="pAnkI", default_width=460, default_height=640)
        self.add_css_class("panki-ventana")
        _cargar_estilo(self.get_display())

        self._candado = threading.Lock()
        self._cerrada = False  # la ventana se destruye: nada más debe tocar widgets
        self._cancelado = False  # Parar pulsado en la ejecución actual
        self._trabajando = False  # hay una ejecución en marcha (desde enviar hasta que su resultado llega a la vista)
        self._en_enviar = False  # el hilo del agente está DENTRO de agente.enviar (solo entonces vale cancelar)
        self._espera = None  # la _Espera de la aprobación pendiente (compartida con el hilo del agente)
        self._agente = None
        self._cola = []
        self._pegado = True  # la vista sigue el final de la conversación
        self._moviendo = False
        self._final_pendiente = False
        self._ultimo_texto = None  # lo último que pAnkI dijo en esta ejecución (para no repetirlo)
        self._ultimo_envio = ""  # lo último que se mandó al agente (vuelve a la entrada si falla)
        self._hay_mensajes = False
        self._actividad = []  # [(nombre, etiqueta)] de herramientas aún sin resultado
        self._tarjeta_en_llamada = False  # ¿salió una tarjeta de permiso desde el último evento "herramienta"?
        self._previo_avisado = False  # el núcleo avisó de que esta llamada va con un permiso previo
        self.tarjeta_aprobacion = None
        self.tarjeta_clave = None

        self._error_carga = None
        if nucleo is None:
            try:
                nucleo = _nucleo_real()
            except ImportError as e:
                registrar(f"No se pudo cargar panki_nucleo: {e}")
                self._error_carga = "No se pudo cargar el núcleo de pAnkI. Reinstala Infinity OS."
        self._nucleo = nucleo
        self._errores = _clases_de_error(nucleo) if nucleo is not None else ()
        self._a_pango = a_pango if a_pango is not None else _a_pango_real()
        self._fabrica = fabrica_agente

        self._construir()
        self.connect("close-request", self._al_cerrar)  # GTK 4 no emite "destroy": la X y apagar la app liberan al hilo

        if self._error_carga:
            self._anadir_burbuja(self._error_carga, "error")
        elif not self._nucleo.existe_clave():
            self._mostrar_tarjeta_clave("primera")
        self._actualizar_controles()

    # --------------------------------------------------------------------- construcción
    @staticmethod
    def _boton_icono(icono, ayuda):
        boton = Gtk.Button.new_from_icon_name(icono)
        boton.set_has_frame(False)
        boton.add_css_class("panki-icono")
        boton.set_tooltip_text(ayuda)
        boton.update_property([Gtk.AccessibleProperty.LABEL], [ayuda])
        return boton

    def _construir(self):
        cabecera = Gtk.HeaderBar()
        cabecera.add_css_class("panki-cabecera")
        cabecera.set_title_widget(Gtk.Box())  # sin título centrado: la marca va a la izquierda
        marca = Gtk.Label(label="pAnkI")
        marca.add_css_class("panki-marca")
        cabecera.pack_start(marca)
        self.boton_clave = self._boton_icono("dialog-password-symbolic", "Clave de Gemini…: cambiarla o borrarla")
        self.boton_clave.connect("clicked", lambda *_: self._mostrar_tarjeta_clave("cambiar"))
        cabecera.pack_end(self.boton_clave)
        self.boton_nueva = self._boton_icono("list-add-symbolic",
                                             "Nueva conversación: pAnkI olvida lo anterior y los permisos")
        self.boton_nueva.connect("clicked", lambda *_: self.nueva_conversacion())
        cabecera.pack_end(self.boton_nueva)
        self.set_titlebar(cabecera)

        teclas_ventana = Gtk.EventControllerKey()
        teclas_ventana.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas_ventana.connect("key-pressed", self._tecla_ventana)
        self.add_controller(teclas_ventana)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(raiz)

        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.conversacion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=22)
        self.conversacion.add_css_class("panki-conversacion")
        self.scroll.set_child(self.conversacion)
        raiz.append(self.scroll)
        ajuste = self.scroll.get_vadjustment()
        ajuste.connect("changed", self._al_crecer)
        ajuste.connect("value-changed", self._al_desplazar)

        self.bienvenida = self._crear_bienvenida()
        self.conversacion.append(self.bienvenida)

        pie = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        pie.add_css_class("panki-pie")
        raiz.append(pie)

        fila_estado = Gtk.Box(spacing=8)
        self.estado = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.estado.add_css_class("panki-estado")
        fila_estado.append(self.estado)
        self.boton_parar = Gtk.Button(label="Parar")
        self.boton_parar.set_has_frame(False)
        self.boton_parar.add_css_class("panki-texto")
        self.boton_parar.connect("clicked", lambda *_: self.parar())
        self.boton_parar.set_visible(False)
        fila_estado.append(self.boton_parar)
        pie.append(fila_estado)

        self.aviso_entrada = Gtk.Label(xalign=0, wrap=True)
        self.aviso_entrada.add_css_class("panki-aviso")
        self.aviso_entrada.set_visible(False)
        pie.append(self.aviso_entrada)

        self.entrada = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                    top_margin=6, bottom_margin=6, left_margin=0, right_margin=0)
        self.entrada.add_css_class("panki-entrada")
        self.entrada.set_tooltip_text("Intro envía · Mayús + Intro, nueva línea")
        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.entrada.add_controller(teclas)
        caja = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True,
                                  min_content_height=30, max_content_height=110,  # de 1 a 5 líneas
                                  propagate_natural_height=True)
        caja.add_css_class("panki-caja-entrada")
        caja.set_child(self.entrada)
        superpuesto = Gtk.Overlay(hexpand=True)
        superpuesto.set_child(caja)
        self.marcador = Gtk.Label(label="Escribe a pAnkI…", xalign=0, halign=Gtk.Align.START,
                                  valign=Gtk.Align.START, margin_top=6)
        self.marcador.add_css_class("panki-marcador")
        self.marcador.set_can_target(False)
        superpuesto.add_overlay(self.marcador)
        self.entrada.get_buffer().connect("changed", self._al_cambiar_entrada)
        pie.append(superpuesto)

        fila_pie = Gtk.Box(spacing=8)
        self.pie_google = Gtk.Label(label=PIE_GOOGLE, xalign=0, hexpand=True)
        self.pie_google.add_css_class("panki-pie-google")
        fila_pie.append(self.pie_google)
        self.pie_permisos = Gtk.Box(spacing=2)
        self.etiqueta_permisos = Gtk.Label()
        self.etiqueta_permisos.add_css_class("panki-pie-permisos")
        self.pie_permisos.append(self.etiqueta_permisos)
        self.boton_revocar = Gtk.Button(label="Revocar")
        self.boton_revocar.set_has_frame(False)
        self.boton_revocar.add_css_class("panki-texto")
        self.boton_revocar.connect("clicked", lambda *_: self.revocar_permisos())
        self.pie_permisos.append(self.boton_revocar)
        self.pie_permisos.set_visible(False)
        fila_pie.append(self.pie_permisos)
        pie.append(fila_pie)

    def _crear_bienvenida(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caja.add_css_class("panki-bienvenida")
        texto = Gtk.Label(label=BIENVENIDA, wrap=True, xalign=0, max_width_chars=48)
        caja.append(texto)
        return caja

    # --------------------------------------------------------------------- vista
    def _al_crecer(self, ajuste):
        """El contenido creció (un mensaje nuevo, una tarjeta): si se estaba viendo el final, se sigue viendo."""
        if self._pegado:
            self._programar_final()

    def _al_desplazar(self, ajuste):
        """El usuario se desplazó: solo se sigue el final si lo dejó a la vista."""
        if not self._moviendo:
            self._pegado = ajuste.get_upper() - ajuste.get_page_size() - ajuste.get_value() < 24

    def _ir_al_final(self):
        self._pegado = True
        self._programar_final()

    def _programar_final(self):
        # Cambiar el desplazamiento mientras GTK reparte el espacio (el aviso "changed" llega en pleno reparto)
        # no se refleja en pantalla: se hace al terminar, en un idle. Varios avisos seguidos se juntan en uno.
        if not self._final_pendiente:
            self._final_pendiente = True
            GLib.idle_add(self._mover_al_final)

    def _mover_al_final(self):
        self._final_pendiente = False
        if self._cerrada or not self._pegado:
            return GLib.SOURCE_REMOVE
        ajuste = self.scroll.get_vadjustment()
        self._moviendo = True  # este desplazamiento es nuestro, no del usuario
        try:
            ajuste.set_value(ajuste.get_upper() - ajuste.get_page_size())
        finally:
            self._moviendo = False
        return GLib.SOURCE_REMOVE

    def _anadir(self, widget):
        self.bienvenida.set_visible(False)
        self.conversacion.append(widget)
        self._hay_mensajes = True

    def _marcado(self, texto):
        """Marcado Pango de la respuesta de pAnkI, o None: entonces se usa el texto tal cual."""
        if self._a_pango is None:
            return None
        try:
            marcado = self._a_pango(texto)
            if not isinstance(marcado, str):
                return None
            marcado = _RESTOS_DE_MARCO.sub("", marcado)  # sin caracteres de dibujo de cajas, vengan de donde vengan
            Pango.parse_markup(marcado, -1, "\0")  # lanza GLib.Error si no está bien formado
            return marcado
        except Exception:
            registrar("a_pango devolvió marcado no válido; se muestra como texto plano\n" + traceback.format_exc())
            return None

    def _anadir_burbuja(self, texto, tipo):
        """Un turno de la conversación. tipo: "usuario" (texto plano, a la derecha), "pa" (pAnkI) o "error".

        Sin burbujas de color: el texto de pAnkI fluye a la izquierda, el del usuario va a la derecha un tono más
        apagado con una línea fina a su izquierda, y el error es una línea propia con la palabra "Error".
        """
        turno = Gtk.Box(halign=Gtk.Align.END if tipo == "usuario" else Gtk.Align.START, spacing=8)
        turno.add_css_class(f"panki-burbuja-{tipo}")  # el nombre de clase solo identifica el tipo de turno
        if tipo == "usuario":
            turno.add_css_class("panki-usuario")
            etiqueta = Gtk.Label(xalign=1, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                                 max_width_chars=38, justify=Gtk.Justification.RIGHT)
            etiqueta.set_text(texto)  # texto plano: nunca se interpreta como marcado
        elif tipo == "error":
            turno.add_css_class("panki-error")
            titulo = Gtk.Label(label="Error", xalign=0, valign=Gtk.Align.START)
            titulo.add_css_class("panki-error-titulo")
            turno.append(titulo)
            etiqueta = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                                 max_width_chars=50)
            etiqueta.set_text(texto_visible(texto))
        else:
            turno.add_css_class("panki-pa")
            etiqueta = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True,
                                 max_width_chars=56)
            limpio = filtrar_texto_modelo(texto)
            marcado = self._marcado(limpio)
            if marcado is not None:
                etiqueta.set_markup(marcado)
            else:
                etiqueta.set_text(limpio)
        turno.append(etiqueta)
        self._anadir(turno)
        return etiqueta

    def _anadir_nota(self, texto):
        etiqueta = Gtk.Label(label=texto, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        etiqueta.add_css_class("panki-nota")
        self._anadir(etiqueta)
        return etiqueta

    @staticmethod
    def _verbo(nombre):
        return VERBOS.get(nombre) if isinstance(nombre, str) else None

    @staticmethod
    def _destino(args):
        """`ruta="~/x"` -> `~/x`; con varios argumentos se deja el resumen entero. Siempre limpio y recortado."""
        m = re.fullmatch(r'\w+="(.*)"', args or "")
        return una_linea(m.group(1) if m else (args or ""), 70)

    def _linea_actividad(self, nombre, args=None, resultado=None, etiquetas=""):
        verbo = self._verbo(nombre)
        if verbo is None:
            return "· " + DESCONOCIDA
        texto = "· " + verbo
        if args:
            texto += " " + self._destino(args)
        if resultado:
            texto += " — " + una_linea(resultado, 110)
        return texto + etiquetas

    def _nueva_etiqueta_actividad(self, texto):
        etiqueta = Gtk.Label(label=texto, xalign=0, ellipsize=Pango.EllipsizeMode.MIDDLE, single_line_mode=True)
        etiqueta.add_css_class("panki-actividad")
        etiqueta.set_tooltip_text(texto)
        self._anadir(etiqueta)
        return etiqueta

    def _anadir_actividad(self, nombre, resumen):
        self._tarjeta_en_llamada = False
        self._previo_avisado = False
        etiqueta = self._nueva_etiqueta_actividad(self._linea_actividad(nombre, resumen))
        self._actividad.append((nombre, etiqueta, resumen))

    def _cerrar_actividad(self, nombre, ok, resumen):
        for i, (n, etiqueta, args) in enumerate(self._actividad):
            if n == nombre:
                del self._actividad[i]
                break
        else:
            etiqueta, args = self._nueva_etiqueta_actividad("·"), None
        # Sin tarjeta entre la llamada y su resultado: lo ha hecho un permiso anterior (o no pedía ninguno).
        # Lo que el modelo lee se envía a Google; se dice en la propia línea.
        previo = bool(ok) and (self._previo_avisado or (not self._tarjeta_en_llamada and nombre in VERBOS
                                                          and nombre not in SIN_APROBACION))
        etiquetas = ""
        if self._verbo(nombre) is not None and ok:
            etiquetas = (" (permiso previo)" if previo else "") + " · se envía a Google"
        texto = self._linea_actividad(nombre, args, (resumen if ok else "no se pudo: " + resumen), etiquetas)
        if self._verbo(nombre) is None:
            texto = "· " + DESCONOCIDA
        etiqueta.set_text(texto)
        etiqueta.set_tooltip_text(texto)
        if not ok:
            etiqueta.add_css_class("panki-actividad-fallo")
        self._actualizar_permisos()

    def _poner_estado(self, texto):
        self.estado.set_text(texto)

    def _actualizar_controles(self):
        """Qué se puede pulsar según el momento: trabajando, esperando permiso, pidiendo la clave..."""
        pendiente = self.tarjeta_aprobacion is not None or self.tarjeta_clave is not None
        libre = not self._trabajando and not pendiente and self._nucleo is not None
        self.entrada.set_sensitive(libre)
        self.boton_parar.set_visible(self._trabajando)
        self.boton_parar.set_sensitive(self._trabajando and not self._cancelado)
        self.boton_nueva.set_sensitive(not self._trabajando)
        self.boton_clave.set_sensitive(not self._trabajando and self.tarjeta_clave is None
                                       and self._nucleo is not None)

    # --------------------------------------------------------------------- permisos "en esta conversación"
    def _permisos_activos(self):
        agente = self._agente
        try:
            lista = list(agente.permisos_activos()) if agente is not None and hasattr(agente, "permisos_activos") else []
        except Exception:  # el hilo del agente puede estar tocando la lista: se vuelve a mirar en el próximo suceso
            return None
        return [str(a) for a in lista]

    def _actualizar_permisos(self):
        """El pie «N permisos activos · Revocar», con el alcance literal de cada uno en la ayuda emergente."""
        lista = self._permisos_activos()
        if lista is None:
            return
        n = len(lista)
        self.pie_permisos.set_visible(n > 0)
        if n:
            self.etiqueta_permisos.set_text(f"{n} permiso activo · " if n == 1 else f"{n} permisos activos · ")
            self.pie_permisos.set_tooltip_text("\n".join(texto_visible(a) for a in lista))

    def revocar_permisos(self):
        """El pie «Revocar»: el agente vuelve a pedir permiso para todo. Devuelve cuántos revocó."""
        agente = self._agente
        n = 0
        revocar = getattr(agente, "revocar_permisos", None)
        if callable(revocar):
            try:
                n = revocar() or 0
            except Exception:
                registrar("agente.revocar_permisos() falló:\n" + traceback.format_exc())
        self._actualizar_permisos()
        return n

    # --------------------------------------------------------------------- entrada de texto
    def _al_cambiar_entrada(self, buffer):
        self.marcador.set_visible(buffer.get_char_count() == 0)
        self.aviso_entrada.set_visible(False)

    def _tecla(self, _ctrl, keyval, _code, estado):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if estado & Gdk.ModifierType.SHIFT_MASK:
                return False  # Mayús + Intro: nueva línea, la inserta el propio TextView
            self._enviar_desde_entrada()
            return True
        return False

    def _tecla_ventana(self, _ctrl, keyval, _code, _estado):
        """Escape deniega la solicitud de permiso pendiente (a cualquier hora: denegar nunca es peligroso)."""
        if keyval == Gdk.KEY_Escape and self.tarjeta_aprobacion is not None:
            espera = getattr(self.tarjeta_aprobacion, "espera", None)
            if espera is not None:
                self._responder(espera, "no", "Denegado", forzar=True)
                return True
        return False

    def texto_de_entrada(self):
        buffer = self.entrada.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _avisar_en_la_entrada(self, texto):
        self.aviso_entrada.set_text(texto)
        self.aviso_entrada.set_visible(True)

    def _enviar_desde_entrada(self):
        if not self.entrada.get_sensitive():
            return
        texto = self.texto_de_entrada().strip()
        if not texto:
            return  # una entrada vacía no envía
        if parece_clave(texto):  # una clave por el chat viajaría a Google y quedaría en el historial
            self._avisar_en_la_entrada(AVISO_CLAVE_EN_ENTRADA)
            return  # el texto se queda en la entrada, sin enviar
        self.entrada.get_buffer().set_text("")
        self.preguntar(texto)

    # --------------------------------------------------------------------- enviar
    def preguntar(self, texto):
        """Envía `texto` como si el usuario lo hubiera escrito (la entrada, o `--preguntar`).

        Si pAnkI está ocupada, o aún no hay clave, el texto espera su turno en una cola corta.
        """
        texto = (texto or "").strip()
        if not texto or self._cerrada:
            return
        if parece_clave(texto):
            self._devolver_a_la_entrada(texto)
            self._avisar_en_la_entrada(AVISO_CLAVE_EN_ENTRADA)
            return
        if self._trabajando or self.tarjeta_aprobacion is not None or self.tarjeta_clave is not None:
            if len(self._cola) < MAX_COLA:
                self._cola.append(texto)
                self._poner_estado_de_cola()
            return
        self._iniciar(texto)

    def _iniciar(self, texto):
        if self._nucleo is None:
            return
        if not self._nucleo.existe_clave():
            self._cola.append(texto)  # se envía en cuanto se guarde la clave
            self._mostrar_tarjeta_clave("primera")
            self._poner_estado_de_cola()
            return
        try:
            agente = self._agente_listo()
        except Exception as e:
            mensaje, es_clave = self._describir_error(e)
            self._anadir_burbuja(mensaje, "error")
            self._devolver_a_la_entrada(texto)
            if es_clave:
                self._mostrar_tarjeta_clave("rechazada")
            return
        self._anadir_burbuja(texto, "usuario")
        self._ultimo_envio = texto
        with self._candado:
            self._cancelado = False
            self._trabajando = True
            self._en_enviar = True
        self._ultimo_texto = None
        self._actividad = []
        self._poner_estado("Pensando…")
        self._actualizar_controles()
        self._ir_al_final()
        threading.Thread(target=self._hilo, args=(agente, texto), name="panki-agente", daemon=True).start()

    def _poner_estado_de_cola(self):
        if self.tarjeta_clave is not None:
            self._poner_estado("Tu pregunta se enviará en cuanto guardes la clave.")
        else:
            self._poner_estado(f"En cola: {len(self._cola)} mensaje(s) esperando su turno.")

    def _agente_listo(self):
        if self._agente is None:
            fabrica = self._fabrica or (lambda aprobar: self._nucleo.crear_agente(aprobar))
            self._agente = fabrica(self.aprobar)
        return self._agente

    def _hilo(self, agente, texto):
        """El hilo del agente. Aquí NO se toca ningún widget: todo va por GLib.idle_add."""
        def alevento(evento):
            GLib.idle_add(self._al_evento, evento)

        try:
            try:
                respuesta = agente.enviar(texto, alevento)
            finally:
                with self._candado:
                    self._en_enviar = False  # desde aquí, cancelar() dejaría la señal puesta para el próximo envío
        except Exception as e:
            mensaje, es_clave = self._describir_error(e)
            GLib.idle_add(self._al_fallo, mensaje, es_clave)
        else:
            GLib.idle_add(self._al_terminar, respuesta)

    def _describir_error(self, e):
        """(mensaje para el usuario, ¿Gemini rechazó la clave?). Se puede llamar desde cualquier hilo.

        La tarjeta de la clave solo se reabre por lo que el núcleo MARCA (`clave_invalida`, o el código HTTP
        real 401/403), nunca por las palabras del mensaje: un texto con la palabra «clave» no puede forzarla.
        """
        if self._errores and isinstance(e, self._errores):
            mensaje = sin_secretos(str(e)) or "pAnkI no pudo completar la petición."
            es_clave = bool(getattr(e, "clave_invalida", False)) or getattr(e, "codigo", None) in (401, 403)
            return mensaje, es_clave
        registrar("Fallo inesperado del agente:\n" + traceback.format_exc())
        return (f"Algo ha fallado dentro de pAnkI. El detalle está en {ETIQUETA_REGISTRO}.", False)

    # --------------------------------------------------------------------- sucesos del hilo (hilo principal)
    def _al_evento(self, evento):
        if self._cerrada:
            return GLib.SOURCE_REMOVE
        try:
            tipo = evento[0]
            if tipo == "estado":
                self._poner_estado(una_linea(evento[1], 100))
            elif tipo == "herramienta":
                self._anadir_actividad(evento[1], str(evento[2]) if len(evento) > 2 else "")
            elif tipo == "permiso_previo":  # si el núcleo lo avisa, no hace falta deducirlo
                self._previo_avisado = True
            elif tipo == "resultado":
                self._cerrar_actividad(evento[1], bool(evento[2]), str(evento[3]) if len(evento) > 3 else "")
            elif tipo == "texto" and not self._cancelado:  # tras Parar, el texto es "Cancelado.": basta la nota
                self._mostrar_respuesta(str(evento[1]))
        except Exception:
            registrar("Evento del agente no válido:\n" + traceback.format_exc())
        return GLib.SOURCE_REMOVE

    def _mostrar_respuesta(self, texto):
        texto = texto.strip()
        if texto and texto != self._ultimo_texto:
            self._anadir_burbuja(texto, "pa")
            self._ultimo_texto = texto

    def _terminar_ejecucion(self):
        with self._candado:
            self._trabajando = False
            cancelado, self._cancelado = self._cancelado, False
        self._poner_estado("")
        self._actualizar_controles()
        self._actualizar_permisos()
        return cancelado

    def _al_terminar(self, respuesta):
        if self._cerrada:
            return GLib.SOURCE_REMOVE
        cancelado = self._terminar_ejecucion()
        if cancelado:
            self._anadir_nota("Detenido.")
            self._cola_a_la_entrada()
            return GLib.SOURCE_REMOVE
        if isinstance(respuesta, str):
            self._mostrar_respuesta(respuesta)
        elif self._ultimo_texto is None:
            self._anadir_nota("pAnkI no ha respondido con texto.")
        self._siguiente_en_cola()
        return GLib.SOURCE_REMOVE

    def _al_fallo(self, mensaje, es_clave):
        if self._cerrada:
            return GLib.SOURCE_REMOVE
        cancelado = self._terminar_ejecucion()
        if cancelado:
            self._anadir_nota("Detenido.")
            self._cola_a_la_entrada()
            return GLib.SOURCE_REMOVE
        self._anadir_burbuja(mensaje, "error")
        self._devolver_a_la_entrada(self._ultimo_envio)  # para reintentarlo con Intro
        if es_clave:
            self._mostrar_tarjeta_clave("rechazada")  # la cola espera a que se guarde una clave nueva
        else:
            self._cola_a_la_entrada()
        return GLib.SOURCE_REMOVE

    def _siguiente_en_cola(self):
        if self._cola and not self._trabajando and self.tarjeta_clave is None and not self._cerrada:
            texto = self._cola.pop(0)
            GLib.idle_add(self._iniciar_de_cola, texto)

    def _iniciar_de_cola(self, texto):
        if not self._cerrada and not self._trabajando:
            self._iniciar(texto)
        return GLib.SOURCE_REMOVE

    def _devolver_a_la_entrada(self, *textos):
        """Los textos vuelven a la caja de entrada (detrás de lo que ya hubiera), para que no se pierdan."""
        textos = [t for t in textos if t]
        if not textos:
            return
        previo = self.texto_de_entrada()
        self.entrada.get_buffer().set_text("\n".join(textos + ([previo] if previo else [])))

    def _cola_a_la_entrada(self):
        """El texto que esperaba su turno vuelve a la caja de entrada."""
        cola, self._cola = self._cola, []
        self._devolver_a_la_entrada(*cola)

    # --------------------------------------------------------------------- aprobación
    def aprobar(self, solicitud):
        """Callback `aprobar` del agente. Se llama DESDE EL HILO DEL AGENTE y bloquea hasta que el usuario responde.

        Devuelve "no", "una_vez" o "conversacion". Si se pulsa Parar, se oculta o se cierra la ventana, o pasan
        3 minutos sin respuesta, responde "no".
        """
        espera = _Espera(solicitud)
        with self._candado:
            if self._cancelado or self._cerrada:
                return "no"
            self._espera = espera
        GLib.idle_add(self._mostrar_aprobacion, espera)
        respuesta = espera.esperar()
        with self._candado:
            if self._espera is espera:
                self._espera = None
        if respuesta == "conversacion" and getattr(solicitud, "recordable", None) is None:
            respuesta = "una_vez"
        return respuesta if respuesta in ("no", "una_vez", "conversacion") else "no"

    def _mostrar_aprobacion(self, espera):
        if self._cerrada or espera.respuesta is not None:
            return GLib.SOURCE_REMOVE  # ya se respondió "no" (Parar o cierre) antes de que llegara aquí
        tarjeta = self._crear_tarjeta_aprobacion(espera)
        espera.tarjeta = tarjeta
        tarjeta.espera = espera
        self.tarjeta_aprobacion = tarjeta
        self._tarjeta_en_llamada = True
        self._anadir(tarjeta)
        self._ajustar_detalle(tarjeta)
        espera.creada = time.monotonic()
        espera.id_armado = GLib.timeout_add(RETARDO_ARMADO_MS, self._armar, espera)
        espera.id_reloj = GLib.timeout_add(1000, self._reloj, espera)
        self._poner_estado("Esperando tu permiso…")
        self._actualizar_controles()
        self._ir_al_final()
        self.present()  # el agente está parado esperando: se trae a la vista siempre (oculta, minimizada o tapada)
        return GLib.SOURCE_REMOVE

    def _crear_tarjeta_aprobacion(self, espera):
        sol = espera.solicitud
        riesgo = getattr(sol, "riesgo", None)
        if riesgo not in RIESGOS:
            riesgo = "alto"  # lo desconocido se trata como lo más peligroso
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tarjeta.add_css_class("panki-tarjeta")
        tarjeta.add_css_class(f"panki-riesgo-{riesgo}")
        tarjeta.riesgo = riesgo

        cabecera = Gtk.Box(spacing=6)
        permiso = Gtk.Label(label="Permiso", xalign=0, hexpand=True)  # etiqueta fija: solo la interfaz la pone
        permiso.add_css_class("panki-permiso")
        cabecera.append(permiso)
        punto = Gtk.Label(label="●")
        punto.add_css_class("panki-punto")
        cabecera.append(punto)
        texto_riesgo = Gtk.Label(label=f"Riesgo {riesgo}")
        texto_riesgo.add_css_class("panki-riesgo-texto")
        cabecera.append(texto_riesgo)
        tarjeta.append(cabecera)

        titulo = Gtk.Label(label=texto_visible(getattr(sol, "titulo", "")), xalign=0, wrap=True,
                           wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
        titulo.add_css_class("panki-tarjeta-titulo")
        tarjeta.append(titulo)

        tarjeta.antiguedad = Gtk.Label(xalign=0)
        tarjeta.antiguedad.add_css_class("panki-estado-tarjeta")
        tarjeta.antiguedad.set_visible(False)
        tarjeta.append(tarjeta.antiguedad)

        lineas = [texto_visible(linea) for linea in (getattr(sol, "detalle", None) or [])]
        tarjeta.detalle = tarjeta.desplazable = None
        tarjeta.mas = Gtk.Label(xalign=0)
        tarjeta.mas.add_css_class("panki-mas")
        tarjeta.mas.set_visible(False)
        if lineas:
            detalle = Gtk.Label(label="\n".join(lineas), xalign=0, yalign=0, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            detalle.add_css_class("panki-detalle")
            # La barra de desplazamiento se ve siempre que haga falta (no es de las que aparecen al mover el ratón).
            desplazable = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=36,
                                             max_content_height=ALTO_MAX_DETALLE, overlay_scrolling=False)
            desplazable.add_css_class("panki-desplazable")
            desplazable.set_child(detalle)
            tarjeta.append(desplazable)
            tarjeta.detalle, tarjeta.desplazable = detalle, desplazable
        tarjeta.append(tarjeta.mas)

        # Permitir y Denegar: mismo tamaño, mismo peso, posición fija. Nacen desactivados y con "…".
        fila = Gtk.Box(spacing=8, homogeneous=True)
        permitir = self._boton("…", "panki-decision")
        permitir.connect("clicked", lambda *_: self._responder(espera, "una_vez", "Permitido"))
        denegar = self._boton("…", "panki-decision")
        denegar.connect("clicked", lambda *_: self._responder(espera, "no", "Denegado"))
        for b in (permitir, denegar):
            b.set_sensitive(False)
            fila.append(b)
        tarjeta.append(fila)
        filas = [fila]
        tarjeta.permitir, tarjeta.denegar, tarjeta.conversacion = permitir, denegar, None
        if getattr(sol, "recordable", None) is not None:
            alcance = getattr(sol, "alcance", None) or getattr(sol, "titulo", "")
            conversacion = Gtk.Button()
            conversacion.set_has_frame(False)
            conversacion.add_css_class("panki-concesion")
            conversacion.etiqueta = Gtk.Label(label="Permitir en esta conversación: " + texto_visible(alcance),
                                              xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            conversacion.set_child(conversacion.etiqueta)  # el alcance entero, en las líneas que haga falta
            conversacion.set_halign(Gtk.Align.START)
            conversacion.set_sensitive(False)
            conversacion.connect("clicked", lambda *_: self._responder(
                espera, "conversacion", "Permitido en esta conversación"))
            tarjeta.conversacion = conversacion
            tarjeta.append(conversacion)
            filas.append(conversacion)
        tarjeta.filas_botones = filas
        return tarjeta

    def _ajustar_detalle(self, tarjeta):
        """Da al detalle su alto real y avisa de cuántas líneas quedan fuera.

        Un Gtk.ScrolledWindow dentro de otro recibe solo su alto mínimo, así que el mínimo se iguala al alto que
        necesita el texto, hasta el máximo (mayor si el riesgo es alto). Si no cabe, «+N líneas más».
        """
        detalle = tarjeta.detalle
        if detalle is None:
            return
        maximo = ALTO_MAX_DETALLE_ALTO if tarjeta.riesgo == "alto" else ALTO_MAX_DETALLE
        capa = detalle.create_pango_layout(detalle.get_text())
        capa.set_wrap(Pango.WrapMode.WORD_CHAR)
        capa.set_width((ANCHO_DETALLE - 16) * Pango.SCALE)
        total = max(1, capa.get_line_count())
        alto_texto = capa.get_pixel_size()[1]
        alto = max(36, min(maximo, alto_texto + 18))
        tarjeta.desplazable.set_max_content_height(alto)  # primero el máximo: GTK exige mínimo <= máximo
        tarjeta.desplazable.set_min_content_height(alto)
        por_linea = alto_texto / total
        visibles = int((alto - 16) / por_linea) if por_linea > 0 else total
        if alto_texto + 18 > alto and total > visibles:
            tarjeta.mas.set_text(f"+{total - visibles} líneas más: desplaza el detalle para verlas")
            tarjeta.mas.set_visible(True)

    @staticmethod
    def _boton(texto, clase):
        boton = Gtk.Button(label=texto)
        boton.add_css_class("panki-decision")
        if clase != "panki-decision":
            boton.add_css_class(clase)
        return boton

    def _armar(self, espera):
        """Pasados RETARDO_ARMADO_MS la tarjeta admite respuesta. Antes, un clic o una tecla no hacen nada."""
        espera.id_armado = 0
        if self._cerrada or espera.respuesta is not None or espera.tarjeta is None:
            return GLib.SOURCE_REMOVE
        espera.armada = True
        t = espera.tarjeta
        t.permitir.set_label("Permitir")
        t.denegar.set_label("Denegar")
        for b in (t.permitir, t.denegar, t.conversacion):
            if b is not None:
                b.set_sensitive(True)
        return GLib.SOURCE_REMOVE

    def _reloj(self, espera):
        """Cada segundo, mientras la tarjeta espera: su antigüedad (pasados 30 s) y su caducidad (a los 3 min)."""
        if self._cerrada or espera.respuesta is not None or espera.tarjeta is None:
            espera.id_reloj = 0
            return GLib.SOURCE_REMOVE
        edad = time.monotonic() - espera.creada
        if edad >= CADUCIDAD_S:
            espera.id_reloj = 0
            if espera.responder("no"):
                self._resolver_tarjeta(espera, "Caducada sin respuesta: denegado")
                self._anadir_nota("Una solicitud de permiso caducó sin respuesta y se denegó.")
            return GLib.SOURCE_REMOVE
        if edad >= ANTIGUEDAD_S:
            t = espera.tarjeta
            t.antiguedad.set_text(f"Esperando desde hace {int(edad)} s · caduca a los {CADUCIDAD_S // 60} min")
            t.antiguedad.set_visible(True)
        return GLib.SOURCE_CONTINUE

    @staticmethod
    def _cancelar_temporizadores(espera):
        for atributo in ("id_armado", "id_reloj"):
            identificador = getattr(espera, atributo)
            if identificador:
                setattr(espera, atributo, 0)
                GLib.source_remove(identificador)

    def _responder(self, espera, valor, etiqueta, forzar=False):
        """El usuario pulsó un botón (o Escape). La primera respuesta gana. Antes del armado no hace nada."""
        if not espera.armada and not forzar:
            return
        if espera.responder(valor):
            self._resolver_tarjeta(espera, etiqueta)

    def _resolver_tarjeta(self, espera, etiqueta):
        self._cancelar_temporizadores(espera)
        tarjeta = espera.tarjeta
        if tarjeta is None:
            return
        for fila in tarjeta.filas_botones:
            tarjeta.remove(fila)
        tarjeta.antiguedad.set_visible(False)
        resuelta = Gtk.Label(label=etiqueta, xalign=0)
        resuelta.add_css_class("panki-resuelta")
        tarjeta.append(resuelta)
        tarjeta.resuelta = resuelta
        espera.tarjeta = None
        if self.tarjeta_aprobacion is tarjeta:
            self.tarjeta_aprobacion = None
        if not self._cerrada:
            if self._trabajando and not self._cancelado:
                self._poner_estado("Pensando…")
            self._actualizar_controles()
            self._actualizar_permisos_tras(espera)

    def _actualizar_permisos_tras(self, espera):
        # El núcleo anota el permiso al volver aquí la respuesta; se mira cuando ya haya pasado.
        if espera.respuesta == "conversacion":
            GLib.timeout_add(150, lambda: self._actualizar_permisos() or GLib.SOURCE_REMOVE)

    def _soltar_aprobacion(self, etiqueta="Cancelado"):
        """Responde "no" a la aprobación pendiente, si la hay, y deja la tarjeta como cancelada."""
        with self._candado:
            espera = self._espera
        if espera is not None and espera.responder("no"):
            self._resolver_tarjeta(espera, etiqueta)

    # --------------------------------------------------------------------- parar, nueva conversación, cerrar
    def parar(self):
        """Pulsar Parar: cancela al agente y responde "no" a lo que espere. El hilo termina solo."""
        if not self._trabajando or self._cancelado:
            return
        self._cancelar_agente(marcar_parada=True)
        self._soltar_aprobacion("Cancelado")
        self._cola_a_la_entrada()
        self._poner_estado("Parando… (espero a que acabe la petición en curso)")
        self._actualizar_controles()

    def _cancelar_agente(self, marcar_parada=False, cerrada=False):
        """agente.cancelar() solo si el hilo sigue dentro de enviar(): con el agente parado, la señal se quedaría
        puesta y el siguiente mensaje saldría "Cancelado." sin hacer nada."""
        with self._candado:
            if marcar_parada:
                self._cancelado = True
            if cerrada:
                self._cerrada = True
            agente = self._agente
            if self._en_enviar and agente is not None:
                try:
                    agente.cancelar()  # solo pone una marca: no bloquea
                except Exception:
                    registrar("agente.cancelar() falló:\n" + traceback.format_exc())

    def nueva_conversacion(self):
        """Vacía la vista y llama a reiniciar() del agente. Con trabajo en curso no hace nada (pulsa Parar antes)."""
        if self._trabajando:
            return
        agente, self._agente = self._agente, None  # el siguiente mensaje crea uno nuevo, con los ajustes de ahora
        if agente is not None:
            try:
                agente.reiniciar()
            except Exception:
                registrar("agente.reiniciar() falló:\n" + traceback.format_exc())
        self._cola = []
        self._actividad = []
        if self.tarjeta_aprobacion is not None and getattr(self.tarjeta_aprobacion, "espera", None) is not None:
            self._cancelar_temporizadores(self.tarjeta_aprobacion.espera)
        self.tarjeta_aprobacion = None
        self.tarjeta_clave = None
        hijo = self.conversacion.get_first_child()
        while hijo is not None:
            siguiente = hijo.get_next_sibling()
            if hijo is not self.bienvenida:
                self.conversacion.remove(hijo)
            hijo = siguiente
        self.bienvenida.set_visible(True)
        self._hay_mensajes = False
        self._poner_estado("")
        self.aviso_entrada.set_visible(False)
        self._actualizar_permisos()
        if self._nucleo is not None and not self._nucleo.existe_clave():
            self._mostrar_tarjeta_clave("primera")
        self._actualizar_controles()

    def mostrar(self):
        self.present()
        if self.entrada.get_sensitive():
            self.entrada.grab_focus()

    def alternar(self):
        """La burbuja y Super + N: muestra la ventana si está oculta; si está visible, la OCULTA.

        Al ocultarla se revocan los permisos "en esta conversación" y se deniega lo que esté pendiente de
        aprobar (nadie lo está mirando); la conversación sigue ahí al volver.
        """
        if not self.get_visible():
            self.mostrar()
            return
        self._soltar_aprobacion("Denegado al ocultar la ventana")
        self.revocar_permisos()
        self.set_visible(False)

    def liberar(self):
        """La ventana se cierra o la aplicación se apaga: nadie va a responder.

        El hilo del agente no se queda esperando una aprobación (recibe "no") ni sigue trabajando (se cancela).
        Se puede llamar más de una vez.
        """
        self._cancelar_agente(cerrada=True)
        with self._candado:
            espera = self._espera
        if espera is not None:
            espera.responder("no")

    def _al_cerrar(self, _ventana):
        if self._trabajando or self._espera is not None:
            self.liberar()
            return False  # cierra de verdad: la aplicación termina
        self.nueva_conversacion()  # lo que se habló y sus permisos se descartan al cerrar
        self.set_visible(False)
        return True  # y se queda oculta, viva, para que la burbuja la abra al instante

    # --------------------------------------------------------------------- la clave de Gemini
    def _mostrar_tarjeta_clave(self, motivo):
        """Tarjeta con el aviso de privacidad y el campo de la clave. motivo: "primera", "rechazada" o "cambiar"."""
        if self.tarjeta_clave is not None or self._nucleo is None:
            return
        titulos = {
            "primera": "Antes de empezar: tu clave de Gemini",
            "rechazada": "Gemini no ha aceptado la clave",
            "cambiar": "Tu clave de Gemini",
        }
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tarjeta.add_css_class("panki-tarjeta")
        etiqueta_fija = Gtk.Label(label="Clave", xalign=0)
        etiqueta_fija.add_css_class("panki-permiso")
        tarjeta.append(etiqueta_fija)
        titulo = Gtk.Label(label=titulos.get(motivo, titulos["cambiar"]), xalign=0, wrap=True)
        titulo.add_css_class("panki-tarjeta-titulo")
        tarjeta.append(titulo)
        for parrafo in (AVISO_PRIVACIDAD if motivo == "primera" else AVISO_CORTO):
            etiqueta = Gtk.Label(label=parrafo, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                                 selectable=True)
            etiqueta.add_css_class("panki-tarjeta-texto")
            tarjeta.append(etiqueta)
        if os.environ.get("GEMINI_API_KEY"):
            nota = Gtk.Label(label="Ahora mismo se usa la clave de la variable GEMINI_API_KEY, que tiene "
                                   "prioridad sobre la que guardes aquí.", xalign=0, wrap=True)
            nota.add_css_class("panki-tarjeta-texto")
            tarjeta.append(nota)

        # Gtk.PasswordEntry oculta lo escrito; sin el icono de "ver", la clave no se muestra nunca.
        campo = Gtk.PasswordEntry(show_peek_icon=False, placeholder_text="Pega aquí tu clave de Gemini")
        campo.add_css_class("panki-clave")
        tarjeta.append(campo)
        mensaje = Gtk.Label(xalign=0, wrap=True)
        mensaje.add_css_class("panki-actividad-fallo")
        mensaje.set_visible(False)
        tarjeta.append(mensaje)

        fila = Gtk.Box(spacing=8, homogeneous=True)
        guardar = self._boton("Guardar", "panki-decision")
        guardar.connect("clicked", lambda *_: self._guardar_clave(tarjeta, campo, mensaje))
        campo.connect("activate", lambda *_: self._guardar_clave(tarjeta, campo, mensaje))
        fila.append(guardar)
        if motivo != "primera" and self._nucleo.existe_clave():
            borrar = self._boton("Borrar clave", "panki-decision")
            borrar.connect("clicked", lambda *_: self._borrar_clave(tarjeta, campo))
            fila.append(borrar)
        cancelar = self._boton("Cancelar", "panki-decision")
        cancelar.connect("clicked", lambda *_: self._cerrar_tarjeta_clave(tarjeta, campo))
        fila.append(cancelar)
        tarjeta.append(fila)

        tarjeta.campo, tarjeta.mensaje, tarjeta.guardar = campo, mensaje, guardar
        self.tarjeta_clave = tarjeta
        self._anadir(tarjeta)
        self._actualizar_controles()
        if motivo == "primera":
            self._pegado = False  # el aviso es largo: se empieza a leer por arriba, no por el campo
        else:
            self._ir_al_final()
        campo.grab_focus()

    def _cerrar_tarjeta_clave(self, tarjeta, campo, volver_a_la_entrada=True):
        campo.set_text("")  # la clave no se queda en el widget
        if tarjeta.get_parent() is not None:
            self.conversacion.remove(tarjeta)
        if self.tarjeta_clave is tarjeta:
            self.tarjeta_clave = None
        if volver_a_la_entrada:
            self._cola_a_la_entrada()
        if not self._trabajando:
            self._poner_estado("")
        self._actualizar_controles()

    def _guardar_clave(self, tarjeta, campo, mensaje):
        clave = campo.get_text().strip()
        if not clave:
            self._aviso_clave(mensaje, "Pega primero tu clave.")
            return
        try:
            self._nucleo.guardar_clave(clave)
        except Exception as e:
            if self._errores and isinstance(e, self._errores):
                texto = str(e)
                if clave in texto:  # el núcleo no debería hacerlo, pero la clave no se muestra jamás
                    texto = "La clave no es válida."
                self._aviso_clave(mensaje, sin_secretos(texto) or "La clave no es válida.")
            else:
                registrar("No se pudo guardar la clave:\n" + traceback.format_exc().replace(clave, "[clave]"))
                self._aviso_clave(mensaje, f"No se pudo guardar la clave. El detalle está en {ETIQUETA_REGISTRO}.")
            return
        finally:
            clave = None
        habia_conversacion = self._hay_mensajes
        self._agente = None  # el cliente de Gemini lleva la clave dentro: hay que crear otro
        self._cerrar_tarjeta_clave(tarjeta, campo, volver_a_la_entrada=False)
        self._actualizar_permisos()
        self._anadir_nota("Clave guardada." + (" pAnkI empieza esta conversación de cero." if habia_conversacion else ""))
        self._siguiente_en_cola()

    def _borrar_clave(self, tarjeta, campo):
        try:
            self._nucleo.borrar_clave()
        except Exception:
            registrar("No se pudo borrar la clave:\n" + traceback.format_exc())
            self._aviso_clave(tarjeta.mensaje, f"No se pudo borrar la clave. El detalle está en {ETIQUETA_REGISTRO}.")
            return
        self._agente = None
        self._cerrar_tarjeta_clave(tarjeta, campo)
        self._actualizar_permisos()
        extra = " Se sigue usando GEMINI_API_KEY." if os.environ.get("GEMINI_API_KEY") else ""
        self._anadir_nota("Clave borrada de este equipo." + extra)

    @staticmethod
    def _aviso_clave(etiqueta, texto):
        etiqueta.set_text(texto)
        etiqueta.set_visible(True)


# ------------------------------------------------------------------------------- aplicación
class Panki(Gtk.Application):
    def __init__(self, fabrica_agente=None, nucleo=None, a_pango=None):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self._inyectado = dict(fabrica_agente=fabrica_agente, nucleo=nucleo, a_pango=a_pango)
        self.ventana = None
        accion = Gio.SimpleAction.new("alternar", None)
        accion.connect("activate", lambda *_: self._alternar())
        self.add_action(accion)  # gapplication action org.infinity.Panki alternar

    def _asegurar_ventana(self):
        if self.ventana is None:
            ajustes_gtk = Gtk.Settings.get_default()  # None si GTK no tiene pantalla
            if ajustes_gtk is not None:
                ajustes_gtk.set_property("gtk-application-prefer-dark-theme", True)
            self.ventana = VentanaPanki(self, **self._inyectado)
        return self.ventana

    def _alternar(self):
        self._asegurar_ventana().alternar()  # una ventana recién creada está oculta: alternar la muestra

    def do_shutdown(self):
        """Al apagar la aplicación (Quit, o la X con trabajo en curso), el hilo del agente no se queda colgado."""
        if self.ventana is not None:
            self.ventana.liberar()
        Gtk.Application.do_shutdown(self)

    def do_activate(self):
        self._asegurar_ventana().mostrar()

    def do_command_line(self, linea):
        pregunta, ayuda, error = interpretar_argumentos(linea.get_arguments()[1:])
        if error:
            linea.printerr_literal(error + "\n" + USO)  # literal: el texto puede llevar un «%»
            return 2
        if ayuda:
            linea.print_literal(USO)
            return 0
        self.activate()
        if pregunta:
            self.ventana.preguntar(pregunta)
        return 0


def main():
    return Panki().run(juntar_pregunta(sys.argv))


if __name__ == "__main__":
    sys.exit(main())
