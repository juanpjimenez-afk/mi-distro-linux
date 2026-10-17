"""pAnkI, el asistente de Infinity OS: la ventana que aloja su página web (infinity-panki).

El motor de pAnkI ya NO es un agente de Gemini: es una app web de un solo archivo (tareas, agenda,
notas, temporizador, cálculo y un chat por reglas en español, con un «Modo Profundo» que puede
consultar un puñado de APIs públicas gratuitas), escrita y mantenida en panki_web/app.html. Este
módulo solo le da una ventana real: aloja esa página en una WebKit.WebView, le da almacenamiento
persistente propio y tiende el puente mínimo que necesita con el sistema (ver docs/panki-web-diseno.md
para el diseño completo y el contrato con la página).

    python3 -m infinity_shell.panki_app [--preguntar TEXTO]   (lo lanza el comando infinity-panki)

El motor anterior (Gemini, con sus tarjetas de aprobación y sus herramientas sobre el sistema) está
descrito en docs/panki-diseno.md y vivía en panki_nucleo.py/panki_herramientas.py/panki_texto.py; este
archivo ya no los usa. Se conservan, con sus pruebas, por si se retoma.

Instancia única y burbuja (sin cambios respecto al motor anterior):
  * `alternar` (la acción de GApplication, la burbuja y Super + N) muestra la ventana si está oculta,
    y la oculta si está visible. La conversación y los datos de la página sobreviven: viven en el
    almacenamiento persistente de la WebView, no en esta ventana.
  * La X oculta la ventana y deja la aplicación viva, para que la burbuja la abra al instante (como la
    X "sin trabajo en curso" del motor anterior; con este motor no hay "trabajo" que distinguir: no hay
    hilos propios que cancelar).

Este módulo importa GTK al nivel de módulo (como el resto del shell: lo exige el paquete de verdad),
pero WebKitGTK se importa de forma perezosa, solo al construir la ventana: así, las funciones puras de
este archivo (el análisis de --preguntar, la ruta de app.html, la validación del mensaje que llega desde
la página, la construcción del JavaScript que se le inyecta) se pueden probar sin GTK ni WebKit
instalados — shell/tests/test_panki_app_web.py lo hace así.
"""
import json
import os
import sys
import time
import traceback

try:
    import gi
    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
except (ImportError, ValueError):
    Gdk = Gio = GLib = Gtk = None  # sin GTK 4: el módulo se puede importar igual (ver el docstring)

APP_ID = "org.infinity.Panki"
MAX_PREGUNTA = 4000  # caracteres de --preguntar; más es casi seguro un error del llamador
ETIQUETA_REGISTRO = "~/.cache/infinity/panki.log"

# La página de pAnkI: variable de entorno de desarrollo, ruta instalada, o la del propio repositorio
# (para poder probarla aquí). Mismo patrón que dock.asset()/RUTAS_ASSETS en dock.py.
VAR_WEB_DEV = "INFINITY_PANKI_HTML"
RUTA_WEB_INSTALADA = "/usr/share/infinity/panki/app.html"

# El mensaje que puede llegar desde la página (window.webkit.messageHandlers.panki.postMessage):
# lista cerrada de acciones reconocidas, y el límite de #in/#form del contrato (sanitizeNombre en
# app.html ya exige 1–24 caracteres; aquí se vuelve a comprobar porque la página es el otro lado de
# un puente, no algo de lo que este módulo se pueda fiar sin mirar).
ACCIONES_JS = ("nombre",)
MAX_VALOR_JS = 24

USO = """Uso: infinity-panki [--preguntar TEXTO]

  (sin opciones)       abre pAnkI
  --preguntar TEXTO    abre pAnkI, o usa la ventana que ya está abierta, y le envía el texto
  -h, --help           muestra esta ayuda
"""


# ------------------------------------------------------------------------------- registro de fallos
def ruta_registro():
    """~/.cache/infinity/panki.log (o $XDG_CACHE_HOME/infinity/panki.log)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "infinity", "panki.log")


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
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {str(texto).rstrip()}\n")
    except OSError:
        pass


# ------------------------------------------------------------------------------- línea de órdenes
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


# ------------------------------------------------------------------------------- la página web
def ruta_app_html():
    """La ruta de panki_web/app.html que hay que cargar, o None si no se encuentra en ningún sitio.

    Mismo orden que dock.asset(): primero la variable de entorno de desarrollo (INFINITY_PANKI_HTML),
    luego la ruta instalada (/usr/share/infinity/panki/app.html), y por último la del propio
    repositorio (junto a este archivo), para poder probarla sin instalar nada.
    """
    candidatas = (
        os.environ.get(VAR_WEB_DEV, ""),
        RUTA_WEB_INSTALADA,
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "panki_web", "app.html"),
    )
    for ruta in candidatas:
        if ruta and os.path.exists(ruta):
            return os.path.abspath(ruta)
    return None


_SIN_PAGINA = (
    "<!doctype html><meta charset=\"utf-8\"><body style=\"background:#0f0f10;color:#ececea;"
    "font:15px/1.5 system-ui,sans-serif;display:flex;align-items:center;justify-content:center;"
    "height:100vh;margin:0;padding:24px;text-align:center;box-sizing:border-box\">"
    "No se encontró la página de pAnkI (app.html). Reinstala Infinity OS.</body>"
)


def _js_aplicar_nombre(nombre):
    """El JavaScript que fija el nombre del asistente desde el ajuste del sistema (ver window.aplicarNombrePanki
    en app.html). SIEMPRE por json.dumps: nunca se concatena el texto crudo en el script."""
    return "window.aplicarNombrePanki(%s)" % json.dumps(nombre)


def _js_preguntar(texto):
    """El JavaScript que envía `--preguntar TEXTO` a la página (ver window.pankiPreguntar, inyectado por
    PUENTE_JS). SIEMPRE por json.dumps: `texto` viene de fuera del proceso."""
    return "window.pankiPreguntar(%s)" % json.dumps(texto)


def _interpretar_mensaje_js(texto):
    """Lo que llega por window.webkit.messageHandlers.panki.postMessage, ya como texto (JSCValue.to_string()).

    Devuelve (accion, valor) si es JSON válido, la acción está en ACCIONES_JS y el valor es un texto de
    1 a MAX_VALOR_JS caracteres tras recortar espacios; en cualquier otro caso (JSON roto, forma rara,
    acción desconocida, valor vacío, demasiado largo, o de un tipo que no sea texto —lista, número,
    booleano, None—) devuelve None: el llamador lo ignora sin fallar, nunca confía en la página a ciegas.
    """
    try:
        datos = json.loads(texto)
    except (TypeError, ValueError):
        return None
    if not isinstance(datos, dict):
        return None
    accion = datos.get("accion")
    if accion not in ACCIONES_JS:
        return None
    valor = datos.get("valor")
    if not isinstance(valor, str):
        return None
    valor = valor.strip()
    if not valor or len(valor) > MAX_VALOR_JS:
        return None
    return accion, valor


def _guardar_nombre(nombre, ajustes_mod=None):
    """Persiste panki.nombre con el mismo mecanismo lectura-modificación-escritura que usa ajustes.py en
    todo el proyecto (igual que panki_herramientas._ejecutar_cambiar_ajuste hacía para otros ajustes):
    cargar los valores actuales, sustituir uno, validar el conjunto y, si no hay errores, escribirlo.

    `ajustes_mod` sustituye al módulo ajustes en las pruebas (como el parámetro `valores` de
    panki_nucleo.py sustituye a ajustes.valor). Devuelve True si se guardó.
    """
    modulo = ajustes_mod
    if modulo is None:
        from . import ajustes as modulo
    try:
        actuales, _ = modulo.cargar()
        nuevo = dict(actuales)
        nuevo["panki.nombre"] = nombre
        normal, errores = modulo.validar(nuevo)
        if errores:
            registrar("panki.nombre no se guardó (no pasó la validación): " + "; ".join(m for _, m in errores))
            return False
        modulo.escribir(normal)
        return True
    except OSError:
        registrar("No se pudo guardar el nombre de pAnkI:\n" + traceback.format_exc())
        return False


# ------------------------------------------------------------------------------- almacenamiento persistente
def _ruta_datos_web():
    """~/.local/share/infinity/panki/webkit (o $XDG_DATA_HOME/infinity/panki/webkit)."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".local", "share")
    return os.path.join(base, "infinity", "panki", "webkit")


def _preparar_directorio(ruta):
    os.makedirs(ruta, exist_ok=True)
    os.chmod(ruta, 0o700)  # localStorage, cookies y caché: privados, igual que la clave que usaba el motor anterior
    return ruta


def _crear_sesion_red(WebKit):
    """Una WebKit.NetworkSession persistente, para que localStorage sobreviva a cerrar y reabrir pAnkI.

    En WebKitGTK 6.0 esto ya no se configura en el WebView (como en 4.1, con WebsiteDataManager pasado al
    WebContext): webkit_network_session_new(data_dir, cache_dir) crea la sesión y su WebsiteDataManager
    persistente, y se la pasa a WebKit.WebView como su propiedad `network-session` (de construcción).
    Si no se pueden preparar los directorios, se degrada a una sesión efímera (pAnkI sigue funcionando
    esa vez, solo que sin guardar nada al cerrar) en vez de no abrir la ventana.
    """
    base = _ruta_datos_web()
    try:
        datos = _preparar_directorio(os.path.join(base, "datos"))
        cache = _preparar_directorio(os.path.join(base, "cache"))
    except OSError:
        registrar("No se pudo preparar el almacenamiento de pAnkI; la sesión será efímera:\n"
                  + traceback.format_exc())
        return WebKit.NetworkSession.new_ephemeral()
    return WebKit.NetworkSession.new(datos, cache)


def _importar_webkit():
    """WebKitGTK 6.0 (namespace GI «WebKit», NO «WebKit2»: ese es el de GTK3/WebKit 4.1), importado solo
    aquí: así el resto de este módulo se puede usar (y probar) sin que WebKit esté instalado."""
    import gi as _gi
    _gi.require_version("WebKit", "6.0")
    from gi.repository import WebKit
    return WebKit


# El puente MÍNIMO entre la página y este proceso. Se inyecta por código (WebKit.UserScript), nunca se
# toca panki_web/app.html. Dos cosas, nada más (el contrato completo está en docs/panki-web-diseno.md):
#   * Traduce el evento 'panki:nombre-cambiado' de la página (cuando el usuario se renombra desde el
#     chat o el panel «Yo») a un mensaje para window.webkit.messageHandlers.panki.
#   * Da a --preguntar un sitio donde llegar: window.pankiPreguntar rellena #in y envía #form.
PUENTE_JS = """
(function () {
  window.addEventListener('panki:nombre-cambiado', function (e) {
    try {
      var nombre = e && e.detail ? e.detail.nombre : null;
      window.webkit.messageHandlers.panki.postMessage(JSON.stringify({accion: 'nombre', valor: nombre}));
    } catch (err) { /* sin puente (p.ej. fuera de pAnkI): la página sigue funcionando igual */ }
  });
  window.pankiPreguntar = function (texto) {
    var entrada = document.getElementById('in');
    if (!entrada || !texto) return;
    entrada.value = texto;
    var formulario = document.getElementById('form');
    if (formulario) formulario.requestSubmit();
  };
})();
"""

# ------------------------------------------------------------------------------- estilo
# Minimalista: solo la cabecera y el fondo de la ventana (el resto del diseño vive en panki_web/app.html,
# que trae el suyo). El violeta es el acento de marca de pAnkI en todo Infinity OS.
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


# ------------------------------------------------------------------------------- ventana
_BaseVentana = Gtk.ApplicationWindow if Gtk is not None else object


class VentanaPanki(_BaseVentana):
    """La ventana de pAnkI: una cabecera propia y, debajo, la página web en una WebKit.WebView.

    460×640 (no lo cambies: una regla de Hyprland en overlay/etc/skel/.config/hypr/hyprland.conf calcula
    su posición a partir de este tamaño). La cabecera es la barra de título de la ventana (−, □, ×);
    la página no dibuja los suyos.
    """

    def __init__(self, app):
        if Gtk is None:
            raise RuntimeError("GTK 4 no está disponible: no se puede crear la ventana de pAnkI.")
        super().__init__(application=app, title="pAnkI", default_width=460, default_height=640)
        self.add_css_class("panki-ventana")
        _cargar_estilo(self.get_display())

        self._cerrada = False  # la aplicación entera se apaga: nada más debe tocar la página
        self._pagina_lista = False  # el primer load-changed == FINISHED ya llegó
        self._pregunta_pendiente = None  # --preguntar que llegó antes de que la página terminara de cargar
        self._webkit_disponible = False
        self._ucm = None
        self._WebKit = None

        self._construir()
        self.connect("close-request", self._al_cerrar)

    # --------------------------------------------------------------------- construcción
    def _construir(self):
        cabecera = Gtk.HeaderBar()
        cabecera.set_decoration_layout(":minimize,maximize,close")  # sus propios −, □, ×
        cabecera.add_css_class("panki-cabecera")
        cabecera.set_title_widget(Gtk.Box())  # sin título centrado: la marca va a la izquierda
        marca = Gtk.Label(label="pAnkI")
        marca.add_css_class("panki-marca")
        cabecera.pack_start(marca)
        self.set_titlebar(cabecera)

        self.webview = self._crear_webview()
        self.set_child(self.webview)

    def _crear_webview(self):
        try:
            WebKit = _importar_webkit()
        except (ImportError, ValueError) as e:
            registrar(f"WebKitGTK 6.0 no está disponible: {e}")
            return Gtk.Label(label="No se pudo cargar el motor de pAnkI: falta WebKitGTK 6.0.",
                             wrap=True, xalign=0.5, vexpand=True, margin_start=24, margin_end=24)
        self._WebKit = WebKit
        sesion = _crear_sesion_red(WebKit)

        self._ucm = WebKit.UserContentManager()
        # Se conecta antes de registrar (evita la carrera que documenta WebKit): un mensaje no puede
        # llegar antes de que el manejador exista.
        self._ucm.connect("script-message-received::panki", self._al_mensaje_js)
        self._ucm.register_script_message_handler("panki")
        guion = WebKit.UserScript.new(PUENTE_JS, WebKit.UserContentInjectedFrames.ALL_FRAMES,
                                      WebKit.UserScriptInjectionTime.END, None, None)
        self._ucm.add_script(guion)

        vista = WebKit.WebView(user_content_manager=self._ucm, network_session=sesion)
        vista.connect("load-changed", self._al_cambiar_carga)
        self._webkit_disponible = True

        ruta = ruta_app_html()
        if ruta is None:
            registrar("No se encontró panki_web/app.html: no se puede mostrar pAnkI.")
            vista.load_html(_SIN_PAGINA, None)
        else:
            vista.load_uri(GLib.filename_to_uri(ruta))
        return vista

    # --------------------------------------------------------------------- la página
    def _al_cambiar_carga(self, webview, evento):
        if self._cerrada or evento != self._WebKit.LoadEvent.FINISHED or self._pagina_lista:
            return  # solo cuenta la primera carga: una recarga no debe reinyectar ni repetir --preguntar
        self._pagina_lista = True
        self._aplicar_nombre_inicial()
        if self._pregunta_pendiente is not None:
            texto, self._pregunta_pendiente = self._pregunta_pendiente, None
            self._evaluar_js(_js_preguntar(texto))

    def _aplicar_nombre_inicial(self):
        try:
            from . import ajustes
            nombre = ajustes.valor("panki.nombre")
        except Exception:
            nombre = None
        if not isinstance(nombre, str) or not nombre.strip():
            nombre = "pAnkI"
        self._evaluar_js(_js_aplicar_nombre(nombre))

    def _evaluar_js(self, script):
        if not self._webkit_disponible or self._cerrada:
            return
        try:
            self.webview.evaluate_javascript(script, -1, None, None, None, self._js_terminado, None)
        except Exception:
            registrar("No se pudo evaluar JavaScript en pAnkI:\n" + traceback.format_exc())

    @staticmethod
    def _js_terminado(webview, resultado, _datos):
        # Solo se llama a finish() para liberar el GAsyncResult; a este puente no le hace falta el valor.
        try:
            webview.evaluate_javascript_finish(resultado)
        except Exception:
            pass

    def _al_mensaje_js(self, _manager, valor):
        if self._cerrada:
            return
        try:
            texto = valor.to_string()
        except Exception:
            registrar("Mensaje de pAnkI (JS) sin forma de texto:\n" + traceback.format_exc())
            return
        resultado = _interpretar_mensaje_js(texto)
        if resultado is None:
            return
        accion, dato = resultado
        if accion == "nombre":
            _guardar_nombre(dato)

    # --------------------------------------------------------------------- --preguntar
    def preguntar(self, texto):
        """Envía `texto` a la página, como si el usuario lo hubiera escrito (lo usa --preguntar)."""
        texto = (texto or "").strip()
        if not texto or self._cerrada or not self._webkit_disponible:
            return
        if self._pagina_lista:
            self._evaluar_js(_js_preguntar(texto))
        else:
            self._pregunta_pendiente = texto  # se dispara en _al_cambiar_carga, al terminar de cargar

    # --------------------------------------------------------------------- mostrar, alternar, cerrar
    def mostrar(self):
        self.present()
        self.webview.grab_focus()

    def alternar(self):
        """La burbuja y Super + N: muestra la ventana si está oculta; si está visible, la OCULTA.

        La página sigue viva (sus datos están en localStorage, persistente): no hay nada que descartar.
        """
        if not self.get_visible():
            self.mostrar()
            return
        self.set_visible(False)

    def liberar(self):
        """La aplicación se apaga del todo (Quit): a partir de aquí, nada más toca la página."""
        self._cerrada = True

    def _al_cerrar(self, _ventana):
        """La X oculta la ventana y deja la aplicación viva, para que la burbuja la abra al instante."""
        self.set_visible(False)
        return True


# ------------------------------------------------------------------------------- aplicación
_BaseApp = Gtk.Application if Gtk is not None else object


class Panki(_BaseApp):
    def __init__(self):
        if Gtk is None:
            raise RuntimeError("GTK 4 no está disponible: no se puede iniciar pAnkI.")
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.ventana = None
        accion = Gio.SimpleAction.new("alternar", None)
        accion.connect("activate", lambda *_: self._alternar())
        self.add_action(accion)  # gapplication action org.infinity.Panki alternar

    def _asegurar_ventana(self):
        if self.ventana is None:
            ajustes_gtk = Gtk.Settings.get_default()  # None si GTK no tiene pantalla
            if ajustes_gtk is not None:
                ajustes_gtk.set_property("gtk-application-prefer-dark-theme", True)
            self.ventana = VentanaPanki(self)
        return self.ventana

    def _alternar(self):
        self._asegurar_ventana().alternar()  # una ventana recién creada está oculta: alternar la muestra

    def do_shutdown(self):
        """Al apagar la aplicación (Quit, o cerrar sesión), nada se queda intentando tocar la página."""
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
