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
  * La clave de Gemini no se muestra ni se escribe en ningún registro.

Ocultar y cerrar (decisión de esta ventana, el documento no la fija):
  * La acción `alternar` (la burbuja y Super + N) muestra u oculta la ventana SIN tocar la conversación: es
    como minimizarla. Si hay una aprobación pendiente no se oculta, porque el agente estaría esperando una
    respuesta que nadie ve; y si llega una aprobación con la ventana oculta, se muestra sola.
  * La X con el agente TRABAJANDO cancela, responde "no" a lo pendiente y cierra de verdad (la aplicación
    termina; el hilo es daemon y no la retiene).
  * La X SIN trabajo en curso oculta la ventana y deja la aplicación viva para que la burbuja la abra al
    instante, pero la conversación se descarta (el documento promete que no se guarda nada al cerrar).
"""
import os
import re
import sys
import threading
import time
import traceback

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
gi.require_version("Pango", "1.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

APP_ID = "org.infinity.Panki"
MAX_PREGUNTA = 4000  # caracteres de --preguntar; más es casi seguro un error del llamador
ALTO_MAX_DETALLE = 170  # px del detalle de una aprobación antes de que se desplace
ANCHO_DETALLE = 390  # px que tiene el detalle en una ventana de 460 de ancho (para medir su alto)
MAX_COLA = 5  # mensajes que esperan su turno (la ventana ocupada, o sin clave todavía)
RIESGOS = ("bajo", "medio", "alto")
ETIQUETA_REGISTRO = "~/.cache/infinity/panki.log"

USO = """Uso: infinity-panki [--preguntar TEXTO]

  (sin opciones)       abre pAnkI
  --preguntar TEXTO    abre pAnkI, o usa la ventana que ya está abierta, y le envía el texto
  -h, --help           muestra esta ayuda
"""

# Lo que el usuario tiene que saber antes de pegar su clave (docs/panki-diseno.md, apartado Privacidad).
AVISO_PRIVACIDAD = (
    "pAnkI usa Gemini, el modelo de Google, con tu propia clave. Antes de empezar, ten en cuenta:",
    "•  Cada mensaje, y lo que pAnkI vea con sus herramientas, se envía a Google (Gemini). "
    "pAnkI no funciona sin conexión ni sin tu clave.",
    "•  pAnkI ve los nombres de los archivos de una carpeta solo si tú lo permites (por carpeta), y el "
    "contenido de un archivo solo si tú lo permites (por archivo o por carpeta, nunca en general).",
    "•  Tu clave se guarda solo en este equipo (~/.config/infinity/panki/clave, con permisos privados) o se "
    "toma de la variable GEMINI_API_KEY. Nunca va en los ajustes ni en ningún registro.",
    "•  La conversación no se guarda: al cerrar la ventana se pierde.",
    "•  No hay telemetría. Solo salen del equipo la llamada a Gemini y lo que tú apruebes "
    "(abrir una dirección web, ejecutar un comando).",
    "Puedes conseguir una clave en aistudio.google.com/apikey.",
)

# Para cambiar la clave o cuando Gemini la rechaza: el resumen de lo importante.
AVISO_CORTO = (AVISO_PRIVACIDAD[1], AVISO_PRIVACIDAD[3], AVISO_PRIVACIDAD[6])

BIENVENIDA = ("Pregúntame lo que quieras de tu equipo, o pídeme que organice tus archivos o cambie un "
              "ajuste del escritorio. Antes de tocar nada, te pregunto.")


# ------------------------------------------------------------------------------- registro de fallos
def ruta_registro():
    """~/.cache/infinity/panki.log (o $XDG_CACHE_HOME/infinity/panki.log)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "infinity", "panki.log")


_CLAVE_API = re.compile(r"AIza[0-9A-Za-z_\-]{20,}")


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
# Lo que no se puede ver o que cambia el orden en que se lee: en una tarjeta de aprobación, un nombre de
# archivo con un carácter de dirección (U+202E) podría hacer pasar una ruta por otra.
_INVISIBLES = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f؜​‎‏‪-‮"
                         "⁠-⁩﻿]")


def texto_visible(texto):
    """El texto con los caracteres invisibles o de dirección sustituidos por su código, visible."""
    return _INVISIBLES.sub(lambda m: f"‹U+{ord(m.group()):04X}›", str(texto))


def es_error_de_clave(mensaje):
    """¿El mensaje de error habla de la clave (falta, no vale, sin permiso)? Entonces se vuelve a pedir."""
    m = mensaje.lower()
    return "clave" in m and any(p in m for p in ("válid", "valid", "falta", "permiso", "rechaz",
                                                   "incorrect", "caduc", "revoc"))


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
ESTILO = """
window.panki-ventana { background-color: #0f1226; }
.panki-conversacion { padding: 14px 14px 6px 14px; }

.panki-bienvenida { padding: 36px 18px 10px 18px; }
.panki-bienvenida-titulo { font-size: 20px; font-weight: 700; color: #f2f3ff; }
.panki-bienvenida-texto { font-size: 13px; color: rgba(236, 238, 255, 0.72); }

.panki-burbuja { padding: 9px 13px; border-radius: 18px; }
.panki-burbuja label { color: #eef0ff; font-size: 14px; }
.panki-burbuja-usuario {
  background-image: linear-gradient(135deg, #5468ff, #7a4dff);
  border-bottom-right-radius: 5px;
}
.panki-burbuja-pa {
  background-color: #1f2548;
  border: 1px solid rgba(255, 255, 255, 0.09);
  border-bottom-left-radius: 5px;
}
.panki-burbuja-error {
  background-color: #3a1a26;
  border: 1px solid #ff6b81;
  border-bottom-left-radius: 5px;
}
.panki-error-titulo { font-size: 11px; font-weight: 700; color: #ff9aa8; }

.panki-actividad { font-size: 12px; color: rgba(236, 238, 255, 0.62); }
.panki-actividad-ok { color: #7fe0ae; }
.panki-actividad-fallo { color: #ff9aa8; }
.panki-nota { font-size: 12px; font-style: italic; color: rgba(236, 238, 255, 0.58); }

.panki-tarjeta {
  background-color: #1a1f3f;
  border: 1px solid rgba(255, 255, 255, 0.14);
  border-left-width: 6px;
  border-radius: 14px;
  padding: 12px;
}
.panki-riesgo-bajo { border-left-color: #4cc38a; }
.panki-riesgo-medio { border-left-color: #f5b942; }
.panki-riesgo-alto { border-left-color: #ff5c6c; }
.panki-tarjeta-titulo { font-size: 14px; font-weight: 700; color: #f2f3ff; }
.panki-tarjeta-texto { font-size: 12px; color: rgba(236, 238, 255, 0.80); }
.panki-insignia { font-size: 11px; font-weight: 700; padding: 2px 8px; border-radius: 999px; }
.panki-insignia-bajo { background-color: rgba(76, 195, 138, 0.20); color: #7fe0ae; }
.panki-insignia-medio { background-color: rgba(245, 185, 66, 0.20); color: #ffd37a; }
.panki-insignia-alto { background-color: rgba(255, 92, 108, 0.22); color: #ffa3ae; }
.panki-detalle {
  font-family: "JetBrains Mono", "DejaVu Sans Mono", monospace;
  font-size: 12px;
  color: #dfe3ff;
  background-color: rgba(0, 0, 0, 0.35);
  border-radius: 8px;
  padding: 8px;
}
.panki-resuelta { font-size: 12px; font-weight: 700; color: rgba(236, 238, 255, 0.78); }

button.panki-boton {
  background-image: none;
  background-color: rgba(255, 255, 255, 0.10);
  color: #f2f3ff;
  border: 1px solid rgba(255, 255, 255, 0.16);
  border-radius: 999px;
  box-shadow: none;
  padding: 5px 14px;
  min-height: 0;
}
button.panki-boton:hover { background-color: rgba(255, 255, 255, 0.20); }
button.panki-boton:disabled { opacity: 0.5; }
button.panki-permitir { background-color: #2a8f63; border-color: #2a8f63; color: #ffffff; }
button.panki-permitir:hover { background-color: #33a574; }
button.panki-denegar { background-color: transparent; }
button.panki-parar { background-color: #b8374a; border-color: #b8374a; color: #ffffff; }
button.panki-parar:hover { background-color: #cf4559; }
button.panki-enviar { background-color: #5468ff; border-color: #5468ff; color: #ffffff; }
button.panki-enviar:hover { background-color: #6679ff; }

.panki-pie { background-color: #12163a; border-top: 1px solid rgba(255, 255, 255, 0.10); padding: 10px 12px; }
.panki-estado { font-size: 12px; color: rgba(236, 238, 255, 0.70); }
.panki-marcador { color: rgba(236, 238, 255, 0.45); font-size: 14px; }
textview.panki-entrada, textview.panki-entrada text {
  background-color: #1b2046;
  color: #f2f3ff;
  caret-color: #ffffff;
  font-size: 14px;
}
scrolledwindow.panki-caja-entrada {
  border: 1px solid rgba(255, 255, 255, 0.18);
  border-radius: 14px;
}
entry.panki-clave, entry.panki-clave text { background-color: #1b2046; color: #f2f3ff; }
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


def _ruta_icono():
    for base in (os.environ.get("INFINITY_ASSETS", ""), "/usr/share/infinity",
                 os.path.join(os.path.dirname(__file__), "..", "..", "branding")):
        ruta = os.path.join(base, "panki.png") if base else ""
        if ruta and os.path.exists(ruta):
            return os.path.abspath(ruta)
    return None


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
    """La clase de error del núcleo. El documento la llama ErrorpAnkI; se aceptan las grafías razonables."""
    clases = []
    for nombre in ("ErrorpAnkI", "ErrorPanki", "ErrorPankI", "ErrorPAnkI"):
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
    tiene `enviar(texto, alevento)`, `cancelar()` y `reiniciar()`. Las pruebas pasan un agente falso.
    `nucleo` aporta existe_clave, guardar_clave, borrar_clave y la clase de error; `a_pango` convierte el
    texto de pAnkI en marcado.
    """

    def __init__(self, app, fabrica_agente=None, nucleo=None, a_pango=None):
        super().__init__(application=app, title="pAnkI", default_width=460, default_height=640)
        self.add_css_class("panki-ventana")
        _cargar_estilo(self.get_display())

        self._candado = threading.Lock()
        self._cerrada = False  # la ventana se destruye: nada más debe tocar widgets
        self._cancelado = False  # Parar pulsado en la ejecución actual
        self._trabajando = False  # hay una ejecución en marcha (desde Enviar hasta que su resultado llega a la vista)
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
        self.connect("close-request", self._al_cerrar)
        self.connect("destroy", self._al_destruir)

        if self._error_carga:
            self._anadir_burbuja(self._error_carga, "error")
        elif not self._nucleo.existe_clave():
            self._mostrar_tarjeta_clave("primera")
        self._actualizar_controles()

    # --------------------------------------------------------------------- construcción
    def _construir(self):
        cabecera = Gtk.HeaderBar()
        self.boton_nueva = Gtk.Button(label="Nueva conversación")
        self.boton_nueva.set_tooltip_text("Empieza de cero: pAnkI olvida lo anterior")
        self.boton_nueva.connect("clicked", lambda *_: self.nueva_conversacion())
        cabecera.pack_start(self.boton_nueva)
        self.boton_clave = Gtk.Button(label="Clave…")
        self.boton_clave.set_tooltip_text("Cambiar o borrar tu clave de Gemini")
        self.boton_clave.connect("clicked", lambda *_: self._mostrar_tarjeta_clave("cambiar"))
        cabecera.pack_end(self.boton_clave)
        self.set_titlebar(cabecera)

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.set_child(raiz)

        self.scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        self.conversacion = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.conversacion.add_css_class("panki-conversacion")
        self.scroll.set_child(self.conversacion)
        raiz.append(self.scroll)
        ajuste = self.scroll.get_vadjustment()
        ajuste.connect("changed", self._al_crecer)
        ajuste.connect("value-changed", self._al_desplazar)

        self.bienvenida = self._crear_bienvenida()
        self.conversacion.append(self.bienvenida)

        pie = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        pie.add_css_class("panki-pie")
        raiz.append(pie)

        fila_estado = Gtk.Box(spacing=8)
        self.ocupado = Gtk.Spinner()
        self.ocupado.set_visible(False)
        fila_estado.append(self.ocupado)
        self.estado = Gtk.Label(xalign=0, hexpand=True, wrap=True)
        self.estado.add_css_class("panki-estado")
        fila_estado.append(self.estado)
        pie.append(fila_estado)

        fila_entrada = Gtk.Box(spacing=8)
        self.entrada = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False,
                                    top_margin=8, bottom_margin=8, left_margin=10, right_margin=10)
        self.entrada.add_css_class("panki-entrada")
        self.entrada.set_tooltip_text("Intro envía · Mayús + Intro, nueva línea")
        teclas = Gtk.EventControllerKey()
        teclas.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        teclas.connect("key-pressed", self._tecla)
        self.entrada.add_controller(teclas)
        caja = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True,
                                  min_content_height=40, max_content_height=140,
                                  propagate_natural_height=True)
        caja.add_css_class("panki-caja-entrada")
        caja.set_child(self.entrada)
        superpuesto = Gtk.Overlay(hexpand=True)
        superpuesto.set_child(caja)
        self.marcador = Gtk.Label(label="Escribe a pAnkI…", xalign=0, halign=Gtk.Align.START,
                                  valign=Gtk.Align.START, margin_start=12, margin_top=10)
        self.marcador.add_css_class("panki-marcador")
        self.marcador.set_can_target(False)
        superpuesto.add_overlay(self.marcador)
        self.entrada.get_buffer().connect("changed", self._al_cambiar_entrada)
        fila_entrada.append(superpuesto)

        botones = Gtk.Box(valign=Gtk.Align.END)
        self.boton_enviar = Gtk.Button(label="Enviar")
        self.boton_enviar.add_css_class("panki-boton")
        self.boton_enviar.add_css_class("panki-enviar")
        self.boton_enviar.connect("clicked", lambda *_: self._enviar_desde_entrada())
        botones.append(self.boton_enviar)
        self.boton_parar = Gtk.Button(label="Parar")
        self.boton_parar.add_css_class("panki-boton")
        self.boton_parar.add_css_class("panki-parar")
        self.boton_parar.connect("clicked", lambda *_: self.parar())
        self.boton_parar.set_visible(False)
        botones.append(self.boton_parar)
        fila_entrada.append(botones)
        pie.append(fila_entrada)

    def _crear_bienvenida(self):
        caja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, halign=Gtk.Align.CENTER)
        caja.add_css_class("panki-bienvenida")
        ruta = _ruta_icono()
        if ruta:
            imagen = Gtk.Image.new_from_file(ruta)
            imagen.set_pixel_size(84)
            caja.append(imagen)
        titulo = Gtk.Label(label="Hola, soy pAnkI")
        titulo.add_css_class("panki-bienvenida-titulo")
        caja.append(titulo)
        texto = Gtk.Label(label=BIENVENIDA, wrap=True, justify=Gtk.Justification.CENTER, max_width_chars=40)
        texto.add_css_class("panki-bienvenida-texto")
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
            Pango.parse_markup(marcado, -1, "\0")  # lanza GLib.Error si no está bien formado
            return marcado
        except Exception:
            registrar("a_pango devolvió marcado no válido; se muestra como texto plano\n" + traceback.format_exc())
            return None

    def _anadir_burbuja(self, texto, tipo):
        """Una burbuja. tipo: "usuario" (texto plano, a la derecha), "pa" (pAnkI, con marcado) o "error"."""
        fila = Gtk.Box(halign=Gtk.Align.END if tipo == "usuario" else Gtk.Align.START)
        burbuja = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        burbuja.add_css_class("panki-burbuja")
        burbuja.add_css_class(f"panki-burbuja-{tipo}")
        if tipo == "error":
            titulo = Gtk.Label(label="Error", xalign=0)
            titulo.add_css_class("panki-error-titulo")
            burbuja.append(titulo)
        etiqueta = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR,
                             selectable=True, max_width_chars=42)
        marcado = self._marcado(texto) if tipo == "pa" else None
        if marcado is not None:
            etiqueta.set_markup(marcado)
        else:
            etiqueta.set_text(texto)  # texto plano: nunca se interpreta como marcado
        burbuja.append(etiqueta)
        fila.append(burbuja)
        self._anadir(fila)
        return etiqueta

    def _anadir_nota(self, texto):
        etiqueta = Gtk.Label(label=texto, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        etiqueta.add_css_class("panki-nota")
        self._anadir(etiqueta)
        return etiqueta

    def _anadir_actividad(self, nombre, resumen):
        texto = f"▸ {nombre}" + (f" — {resumen}" if resumen else "")
        etiqueta = Gtk.Label(label=texto, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        etiqueta.add_css_class("panki-actividad")
        self._anadir(etiqueta)
        self._actividad.append((nombre, etiqueta))

    def _cerrar_actividad(self, nombre, ok, resumen):
        for i, (n, etiqueta) in enumerate(self._actividad):
            if n == nombre:
                del self._actividad[i]
                break
        else:
            etiqueta = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
            etiqueta.add_css_class("panki-actividad")
            self._anadir(etiqueta)
        etiqueta.set_text(("✓ " if ok else "✕ ") + f"{nombre}" + (f" — {resumen}" if resumen else ""))
        etiqueta.add_css_class("panki-actividad-ok" if ok else "panki-actividad-fallo")

    def _poner_estado(self, texto):
        self.estado.set_text(texto)

    def _actualizar_controles(self):
        """Qué se puede pulsar según el momento: trabajando, esperando permiso, pidiendo la clave..."""
        pendiente = self.tarjeta_aprobacion is not None or self.tarjeta_clave is not None
        libre = not self._trabajando and not pendiente and self._nucleo is not None
        self.entrada.set_sensitive(libre)
        self.boton_enviar.set_sensitive(libre)
        self.boton_enviar.set_visible(not self._trabajando)
        self.boton_parar.set_visible(self._trabajando)
        self.boton_parar.set_sensitive(self._trabajando and not self._cancelado)
        self.boton_nueva.set_sensitive(not self._trabajando)
        self.boton_clave.set_sensitive(not self._trabajando and self.tarjeta_clave is None
                                       and self._nucleo is not None)
        if self._trabajando:
            self.ocupado.set_visible(True)
            self.ocupado.start()
        else:
            self.ocupado.stop()
            self.ocupado.set_visible(False)

    # --------------------------------------------------------------------- entrada de texto
    def _al_cambiar_entrada(self, buffer):
        self.marcador.set_visible(buffer.get_char_count() == 0)

    def _tecla(self, _ctrl, keyval, _code, estado):
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            if estado & Gdk.ModifierType.SHIFT_MASK:
                return False  # Mayús + Intro: nueva línea, la inserta el propio TextView
            self._enviar_desde_entrada()
            return True
        return False

    def texto_de_entrada(self):
        buffer = self.entrada.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _enviar_desde_entrada(self):
        if not self.entrada.get_sensitive():
            return
        texto = self.texto_de_entrada().strip()
        if not texto:
            return  # una entrada vacía no envía
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
        if self._trabajando or self.tarjeta_aprobacion is not None or self.tarjeta_clave is not None:
            if len(self._cola) < MAX_COLA:
                self._cola.append(texto)
                self._poner_estado(f"En cola: {len(self._cola)} mensaje(s) esperando su turno.")
            return
        self._iniciar(texto)

    def _iniciar(self, texto):
        if self._nucleo is None:
            return
        if not self._nucleo.existe_clave():
            self._cola.append(texto)  # se envía en cuanto se guarde la clave
            self._mostrar_tarjeta_clave("primera")
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
        """(mensaje para el usuario, ¿es un problema de la clave?). Se puede llamar desde cualquier hilo."""
        if self._errores and isinstance(e, self._errores):
            mensaje = sin_secretos(str(e)) or "pAnkI no pudo completar la petición."
            return mensaje, getattr(e, "codigo", None) in (401, 403) or es_error_de_clave(mensaje)
        registrar("Fallo inesperado del agente:\n" + traceback.format_exc())
        return (f"Algo ha fallado dentro de pAnkI. El detalle está en {ETIQUETA_REGISTRO}.", False)

    # --------------------------------------------------------------------- sucesos del hilo (hilo principal)
    def _al_evento(self, evento):
        if self._cerrada:
            return GLib.SOURCE_REMOVE
        try:
            tipo = evento[0]
            if tipo == "estado":
                self._poner_estado(str(evento[1]))
            elif tipo == "herramienta":
                self._anadir_actividad(str(evento[1]), str(evento[2]) if len(evento) > 2 else "")
            elif tipo == "resultado":
                self._cerrar_actividad(str(evento[1]), bool(evento[2]), str(evento[3]) if len(evento) > 3 else "")
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

        Devuelve "no", "una_vez" o "conversacion". Si se pulsa Parar o se cierra la ventana, responde "no".
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
        self.tarjeta_aprobacion = tarjeta
        self._anadir(tarjeta)
        self._ajustar_detalle(tarjeta)
        self._poner_estado("Esperando tu aprobación…")
        self._actualizar_controles()
        self._ir_al_final()
        if not self.get_visible():
            self.present()  # el agente está parado esperando: el usuario tiene que verlo
        return GLib.SOURCE_REMOVE

    def _crear_tarjeta_aprobacion(self, espera):
        sol = espera.solicitud
        riesgo = getattr(sol, "riesgo", None)
        if riesgo not in RIESGOS:
            riesgo = "alto"  # lo desconocido se trata como lo más peligroso
        tarjeta = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        tarjeta.add_css_class("panki-tarjeta")
        tarjeta.add_css_class(f"panki-riesgo-{riesgo}")

        cabecera = Gtk.Box(spacing=8)
        titulo = Gtk.Label(label=texto_visible(getattr(sol, "titulo", "")), xalign=0, hexpand=True,
                           wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
        titulo.add_css_class("panki-tarjeta-titulo")
        cabecera.append(titulo)
        insignia = Gtk.Label(label=f"Riesgo {riesgo}", valign=Gtk.Align.START)
        insignia.add_css_class("panki-insignia")
        insignia.add_css_class(f"panki-insignia-{riesgo}")
        cabecera.append(insignia)
        tarjeta.append(cabecera)

        pregunta = Gtk.Label(label="pAnkI quiere hacer esto. ¿La dejas?", xalign=0)
        pregunta.add_css_class("panki-tarjeta-texto")
        tarjeta.append(pregunta)

        lineas = [texto_visible(linea) for linea in (getattr(sol, "detalle", None) or [])]
        if lineas:
            detalle = Gtk.Label(label="\n".join(lineas), xalign=0, yalign=0, wrap=True,
                                wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True)
            detalle.add_css_class("panki-detalle")
            desplazable = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, min_content_height=36,
                                             max_content_height=ALTO_MAX_DETALLE)
            desplazable.set_child(detalle)
            tarjeta.append(desplazable)
            tarjeta.detalle, tarjeta.desplazable = detalle, desplazable

        fila1 = Gtk.Box(spacing=8)
        permitir = self._boton("Permitir", "panki-permitir")
        permitir.connect("clicked", lambda *_: self._responder(espera, "una_vez", "Permitido"))
        denegar = self._boton("Denegar", "panki-denegar")
        denegar.connect("clicked", lambda *_: self._responder(espera, "no", "Denegado"))
        fila1.append(permitir)
        fila1.append(denegar)
        botones = [fila1]
        tarjeta.permitir, tarjeta.denegar, tarjeta.conversacion = permitir, denegar, None
        if getattr(sol, "recordable", None) is not None:
            conversacion = self._boton("Permitir en esta conversación", "panki-permitir-conversacion")
            conversacion.set_halign(Gtk.Align.START)
            conversacion.connect("clicked",
                                 lambda *_: self._responder(espera, "conversacion", "Permitido en esta conversación"))
            tarjeta.conversacion = conversacion
            botones.append(conversacion)
        for fila in botones:
            tarjeta.append(fila)
        tarjeta.filas_botones = botones
        return tarjeta

    @staticmethod
    def _ajustar_detalle(tarjeta):
        """Da al detalle su alto real, hasta ALTO_MAX_DETALLE; más allá se desplaza.

        Un Gtk.ScrolledWindow dentro de otro recibe solo su alto mínimo, así que el mínimo se iguala al alto que
        necesita el texto (medido con el ancho que tiene en una ventana normal).
        """
        detalle = getattr(tarjeta, "detalle", None)
        if detalle is None:
            return
        natural = detalle.measure(Gtk.Orientation.VERTICAL, ANCHO_DETALLE)[1]
        alto = max(36, min(ALTO_MAX_DETALLE, natural + 2))
        tarjeta.desplazable.set_min_content_height(alto)
        tarjeta.desplazable.set_max_content_height(alto)

    @staticmethod
    def _boton(texto, clase):
        boton = Gtk.Button(label=texto)
        boton.add_css_class("panki-boton")
        boton.add_css_class(clase)
        return boton

    def _responder(self, espera, valor, etiqueta):
        """El usuario pulsó un botón de la tarjeta. La primera respuesta gana; las demás se ignoran."""
        if espera.responder(valor):
            self._resolver_tarjeta(espera, etiqueta)

    def _resolver_tarjeta(self, espera, etiqueta):
        tarjeta = espera.tarjeta
        if tarjeta is None:
            return
        for fila in tarjeta.filas_botones:
            tarjeta.remove(fila)
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
        if self._nucleo is not None and not self._nucleo.existe_clave():
            self._mostrar_tarjeta_clave("primera")
        self._actualizar_controles()

    def mostrar(self):
        self.present()
        if self.entrada.get_sensitive():
            self.entrada.grab_focus()

    def alternar(self):
        """La burbuja y Super + N: muestra la ventana si está oculta; si está visible, la oculta (sin tocar la conversación)."""
        if not self.get_visible():
            self.mostrar()
        elif self.tarjeta_aprobacion is not None:
            self.present()  # hay un permiso pendiente: no se oculta hasta que se decida
        else:
            self.set_visible(False)

    def _liberar_hilo(self):
        """La ventana se cierra: nadie va a responder. El hilo del agente no se queda esperando ni sigue trabajando."""
        self._cancelar_agente(cerrada=True)
        with self._candado:
            espera = self._espera
        if espera is not None:
            espera.responder("no")

    def _al_cerrar(self, _ventana):
        if self._trabajando or self._espera is not None:
            self._liberar_hilo()
            return False  # cierra de verdad: la aplicación termina
        self.nueva_conversacion()  # lo que se habló se descarta al cerrar
        self.set_visible(False)
        return True  # y se queda oculta, viva, para que la burbuja la abra al instante

    def _al_destruir(self, _ventana):
        self._liberar_hilo()

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
        tarjeta.add_css_class("panki-riesgo-medio")
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

        fila = Gtk.Box(spacing=8)
        guardar = self._boton("Guardar", "panki-permitir")
        guardar.connect("clicked", lambda *_: self._guardar_clave(tarjeta, campo, mensaje))
        campo.connect("activate", lambda *_: self._guardar_clave(tarjeta, campo, mensaje))
        fila.append(guardar)
        if motivo != "primera" and self._nucleo.existe_clave():
            borrar = self._boton("Borrar clave", "panki-denegar")
            borrar.connect("clicked", lambda *_: self._borrar_clave(tarjeta, campo))
            fila.append(borrar)
        cancelar = self._boton("Cancelar", "panki-denegar")
        cancelar.connect("clicked", lambda *_: self._cerrar_tarjeta_clave(tarjeta, campo))
        fila.append(cancelar)
        tarjeta.append(fila)

        tarjeta.campo, tarjeta.mensaje, tarjeta.guardar = campo, mensaje, guardar
        self.tarjeta_clave = tarjeta
        self._anadir(tarjeta)
        self._actualizar_controles()
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
            self.ventana.connect("destroy", self._al_destruir_ventana)
        return self.ventana

    def _al_destruir_ventana(self, _ventana):
        self.ventana = None

    def _alternar(self):
        if self.ventana is None:
            self._asegurar_ventana().mostrar()
        else:
            self.ventana.alternar()

    def do_activate(self):
        self._asegurar_ventana().mostrar()

    def do_command_line(self, linea):
        pregunta, ayuda, error = interpretar_argumentos(linea.get_arguments()[1:])
        if error:
            linea.printerr(error + "\n" + USO)
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
