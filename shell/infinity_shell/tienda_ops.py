"""Lo que hace la Tienda por debajo de la ventana: estado de cada app, planes de instalación, ejecución en segundo plano
con progreso y la cola de trabajos. Sin GTK (solo biblioteca estándar), así que se prueba sin pantalla.

Las tres estrategias del catálogo (ver tienda_catalogo.py) se reducen a un PLAN: una lista de pasos, cada uno una orden
externa o una función de Python.

    flatpak   flatpak remote-add / install --user (sin permisos de administrador)
    apt       pkexec /usr/libexec/infinity/tienda-ayudante instalar <id>      (pide la contraseña en una ventana)
    webapp    una función que escribe el lanzador .desktop y el icono (tienda_webapps)

El Ejecutor corre un plan en un hilo (la ventana nunca se congela) y avisa de cada avance; el Gestor lleva la cola (un
trabajo a la vez: apt y la red no admiten más), el estado de cada app y las dependencias (las WebApps necesitan un
navegador Chromium: si no hay, ofrece instalar uno).
"""
import os
import re
import shutil
import subprocess
import threading
from collections import deque, namedtuple

from . import tienda_webapps as W
from .tienda_catalogo import RE_ID

AYUDANTE = "/usr/libexec/infinity/tienda-ayudante"
INSTALAR, QUITAR, ACTUALIZAR = "instalar", "quitar", "actualizar"
ACCIONES = (INSTALAR, QUITAR, ACTUALIZAR)
VERBOS = {INSTALAR: ("Instalando", "instalada", "instalar"), QUITAR: ("Quitando", "quitada", "quitar"),
          ACTUALIZAR: ("Actualizando", "actualizada", "actualizar")}
MARGEN_ESPACIO = 1.2         # hace falta un 20 % más del tamaño aproximado del catálogo

Estado = namedtuple("Estado", "instalada version funciona detalle")
SIN_INSTALAR = Estado(False, "", True, "")
Paso = namedtuple("Paso", "texto argv funcion")          # o una orden (argv) o una función; la otra es None
Plan = namedtuple("Plan", "entrada accion pasos")
Evento = namedtuple("Evento", "tipo valor texto")        # tipo: progreso (valor 0..1 o None) | log | archivo | error | listo
Respuesta = namedtuple("Respuesta", "estado mensaje opciones")   # estado: ok | falta_navegador | poco_espacio | error | ocupado
AvisoEspacio = namedtuple("AvisoEspacio", "libre_mb necesario_mb sitio")


class ErrorPlan(Exception):
    """No se puede ni empezar (falta una herramienta, no hay navegador…). El mensaje se le enseña al usuario."""


# --------------------------------------------------------------------------------------- estado
def estados_dpkg(paquetes, correr=None):
    """{paquete: versión} de los instalados, con UNA sola llamada a dpkg-query. Los que no están, no aparecen."""
    paquetes = [p for p in paquetes if p]
    if not paquetes:
        return {}
    correr = correr or _correr_corto
    salida = correr(["dpkg-query", "-W", "--showformat=${Package}\t${db:Status-Abbrev}\t${Version}\n", *paquetes])
    instalados = {}
    for linea in salida.splitlines():
        partes = linea.split("\t")
        if len(partes) == 3 and partes[1].startswith("ii"):
            instalados[partes[0]] = partes[2]
    return instalados


def _correr_corto(argv):
    try:
        r = subprocess.run(argv, capture_output=True, text=True, timeout=10, check=False,
                           env={**os.environ, "LC_ALL": "C.UTF-8"})
    except (OSError, subprocess.TimeoutExpired):
        return ""
    return r.stdout        # dpkg-query sale con 1 si algún paquete no existe, pero lista los que sí


def consultar_estados(catalogo, datos=None, dpkg=None, buscar=None, flatpak=None):
    """{id: Estado} de todas las apps del catálogo."""
    instalados = estados_dpkg([a.origen["paquete"] for a in catalogo.apps if a.tipo == "apt"], dpkg)
    estados = {}
    for a in catalogo.apps:
        if a.tipo == "flatpak":
            ok = W.flatpak_instalada(a.origen["ref"], datos)
            estados[a.id] = Estado(ok, "", True, "") if ok else SIN_INSTALAR
        elif a.tipo == "apt":
            version = instalados.get(a.origen["paquete"])
            estados[a.id] = Estado(True, version, True, "") if version is not None else SIN_INSTALAR
        else:
            info = W.webapp_instalada(a.id, datos, flatpak, buscar)
            if info is None:
                estados[a.id] = SIN_INSTALAR
            elif info.funciona:
                estados[a.id] = Estado(True, "", True, "")
            else:
                estados[a.id] = Estado(True, "", False, "Su navegador ya no está instalado: instala otro para que vuelva a abrir.")
    return estados


def comprobar_espacio(entrada, statvfs=os.statvfs, home=None):
    """None si hay sitio de sobra; si no, un AvisoEspacio. Usa el tamaño aproximado del catálogo (0 = no se avisa)."""
    if not entrada.tamano_mb or entrada.tipo == "webapp":
        return None
    sitio = (home or os.path.expanduser("~")) if entrada.tipo == "flatpak" else "/"
    try:
        v = statvfs(sitio)
    except OSError:
        return None
    libre = v.f_bavail * v.f_frsize // (1024 * 1024)
    necesario = int(entrada.tamano_mb * MARGEN_ESPACIO)
    return AvisoEspacio(libre, necesario, sitio) if libre < necesario else None


# --------------------------------------------------------------------------------------- planes
def _herramienta(nombre, buscar=None):
    ruta = (buscar or shutil.which)(nombre)
    if not ruta:
        raise ErrorPlan({"flatpak": "Falta Flatpak en este sistema (actualiza la ISO de Infinity OS).",
                         "pkexec": "Falta pkexec en este sistema (actualiza la ISO de Infinity OS)."}.get(
                             nombre, f"Falta «{nombre}» en este sistema."))
    return ruta


def plan_para(entrada, accion, buscar=None, ayudante=AYUDANTE, preferido=None, datos=None):
    """El Plan de `accion` sobre `entrada`. Lanza ErrorPlan si no se puede (falta una herramienta)."""
    if accion not in ACCIONES or not RE_ID.fullmatch(entrada.id):
        raise ErrorPlan("Acción no válida.")
    gerundio = VERBOS[accion][0]
    if entrada.tipo == "flatpak":
        flatpak = _herramienta("flatpak", buscar)
        o = entrada.origen
        if accion == INSTALAR:
            pasos = [Paso("Añadiendo el repositorio Flathub…", [flatpak, "remote-add", "--user", "--if-not-exists",
                                                                  o["remoto"], o["url_remoto"]], None),
                     Paso(f"{gerundio} {entrada.nombre}…", [flatpak, "install", "--user", "-y", "--noninteractive",
                                                           o["remoto"], o["ref"]], None)]
        else:
            orden = "uninstall" if accion == QUITAR else "update"
            pasos = [Paso(f"{gerundio} {entrada.nombre}…", [flatpak, orden, "--user", "-y", "--noninteractive", o["ref"]], None)]
    elif entrada.tipo == "apt":
        pkexec = _herramienta("pkexec", buscar)
        if not os.path.exists(ayudante):
            raise ErrorPlan("Falta el ayudante de instalación de la tienda (actualiza la ISO de Infinity OS).")
        pasos = [Paso(f"{gerundio} {entrada.nombre}… (te pedirá tu contraseña de administrador)",
                      [pkexec, ayudante, accion, entrada.id], None)]
    else:
        def hacer(entrada=entrada, accion=accion):
            if accion == QUITAR:
                W.quitar_webapp(entrada.id, datos)
            else:
                try:
                    W.instalar_webapp(entrada, preferido=preferido, datos=datos)
                except W.SinNavegador as e:
                    raise ErrorPlan(str(e)) from None
        pasos = [Paso(f"{gerundio} {entrada.nombre}…", None, hacer)]
    return Plan(entrada, accion, pasos)


# --------------------------------------------------------------------------------------- lectura de la salida
_PORCIENTO = re.compile(r"(\d{1,3})\s?%")


def interpretar_ayudante(linea):
    """Línea del ayudante de root → Evento. Las que no empiezan por «@@» son el registro de apt."""
    if linea.startswith("@@PROGRESO "):
        m = re.fullmatch(r"@@PROGRESO (\d{1,3}) ?(.*)", linea)
        if m:
            return Evento("progreso", min(int(m.group(1)), 100) / 100, m.group(2))
    elif linea.startswith("@@ARCHIVO "):
        nombre = linea[len("@@ARCHIVO "):].strip()
        return Evento("archivo", nombre, nombre)
    elif linea.startswith("@@ERROR"):
        return Evento("error", None, linea[len("@@ERROR"):].strip())
    elif linea.startswith("@@LISTO"):
        return Evento("listo", None, "")
    return Evento("log", None, linea[2:] if linea.startswith("  ") else linea)


def interpretar_flatpak(linea):
    m = _PORCIENTO.search(linea)
    if m and int(m.group(1)) <= 100:
        return Evento("progreso", int(m.group(1)) / 100, "")
    return Evento("log", None, linea)


def parser_de(paso):
    return interpretar_ayudante if paso.argv and paso.argv[0].endswith("pkexec") else interpretar_flatpak


def explicar_salida(codigo, ultimas, error_del_ayudante=""):
    """Un mensaje claro cuando una orden termina con error."""
    if error_del_ayudante:
        return error_del_ayudante
    if codigo == 126:
        return "Se canceló la autorización (o esta cuenta no tiene permiso de administrador)."
    if codigo == 127:
        return ("No se pudo pedir la autorización de administrador. Comprueba que el agente de polkit está en marcha "
                "(en Infinity OS arranca solo con la sesión).")
    texto = "\n".join(ultimas)
    if re.search(r"Could not resolve|Temporary failure|Network is unreachable|Unable to connect|Failed to connect|"
                 r"Error al|No address|Connection (refused|timed out)", texto, re.I):
        return "No hay conexión con el servidor. Comprueba tu conexión a internet y vuelve a intentarlo."
    if re.search(r"No space left|not enough (free )?space", texto, re.I):
        return "No queda espacio. En modo en vivo sin guardado el espacio es la RAM: libera memoria o arranca con «Guardar cambios»."
    if re.search(r"already installed", texto, re.I):
        return "Ya estaba instalada."
    resto = [l for l in ultimas if l.strip()][-1:] or [""]
    return f"La orden terminó con error (código {codigo}). {resto[0][:160]}".strip()


# --------------------------------------------------------------------------------------- ejecución
class Ejecutor:
    """Corre un Plan en un hilo y avisa de lo que pasa. `despachar(f, *args)` lleva las llamadas al hilo de la interfaz
    (GLib.idle_add en la ventana; en las pruebas, llamar directamente)."""

    def __init__(self, despachar=None, popen=subprocess.Popen):
        self.despachar = despachar or (lambda f, *a: f(*a))
        self.popen = popen

    def ejecutar(self, plan, al_evento, al_terminar):
        """Empieza el plan. al_evento(Evento) por cada avance; al_terminar(ok, mensaje) UNA vez. Devuelve el hilo."""
        hilo = threading.Thread(target=self._correr, args=(plan, al_evento, al_terminar), daemon=False,
                                name=f"tienda-{plan.entrada.id}")
        hilo.start()
        return hilo

    def _correr(self, plan, al_evento, al_terminar):
        def evento(*a):
            self.despachar(al_evento, Evento(*a))
        ok, mensaje = False, ""
        try:
            n = len(plan.pasos)
            for i, paso in enumerate(plan.pasos):
                evento("progreso", i / n, paso.texto)
                if paso.funcion is not None:
                    paso.funcion()
                else:
                    self._orden(paso, i, n, evento)
            evento("progreso", 1.0, "")
            ok = True
        except ErrorPlan as e:
            mensaje = str(e)
        except _ErrorOrden as e:
            mensaje = e.mensaje
        except OSError as e:
            mensaje = f"No se pudo ejecutar la orden: {e.strerror or e}"
        except Exception as e:   # noqa: BLE001 - un error de programación no debe dejar la ventana esperando para siempre
            mensaje = f"Error inesperado: {type(e).__name__}: {e}"
        self.despachar(al_terminar, ok, mensaje)

    def _orden(self, paso, i, n, evento):
        interpretar = parser_de(paso)
        ultimas, error = [], ""
        proceso = self.popen(paso.argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             bufsize=0, close_fds=True, start_new_session=True,
                             env={**os.environ, "LC_ALL": "C.UTF-8"})
        buf = bytearray()

        def linea(texto):
            nonlocal error
            ev = interpretar(texto)
            if ev.tipo == "error":
                error = ev.texto
                return
            if ev.tipo == "progreso" and ev.valor is not None:
                evento("progreso", (i + ev.valor) / n, ev.texto or paso.texto)
            elif ev.tipo == "archivo":
                evento("archivo", ev.valor, ev.texto)
            elif ev.tipo == "log":
                ultimas.append(ev.texto)
                del ultimas[:-30]
                evento("log", None, ev.texto)
        try:
            while True:
                datos = os.read(proceso.stdout.fileno(), 65536)
                if not datos:
                    break
                buf.extend(datos)
                while (m := re.search(rb"[\r\n]", buf)) is not None:
                    texto = bytes(buf[:m.start()]).decode("utf-8", "replace")
                    del buf[:m.end()]
                    if texto:
                        linea(texto)
                if len(buf) > 65536:
                    linea(bytes(buf).decode("utf-8", "replace"))
                    buf.clear()
            if buf:
                linea(bytes(buf).decode("utf-8", "replace"))
            codigo = proceso.wait()
        finally:
            proceso.stdout.close()
        if codigo != 0:
            raise _ErrorOrden(explicar_salida(codigo, ultimas, error))


class _ErrorOrden(Exception):
    def __init__(self, mensaje):
        super().__init__(mensaje)
        self.mensaje = mensaje


# --------------------------------------------------------------------------------------- cómo abrir una app
def como_abrir(entrada, datos=None, existe=os.path.exists, home=None):
    """Cómo se lanza una app instalada: ("archivo", ruta .desktop) | ("id", id .desktop) | ("orden", argv) | None."""
    if entrada.tipo == "webapp":
        ruta = W.ruta_desktop(entrada.id, datos)
        return ("archivo", ruta) if existe(ruta) else None
    if entrada.tipo == "apt":
        for d in entrada.desktop_ids:
            if existe(os.path.join("/usr/share/applications", d)):
                return ("id", d)
        return None
    base = datos or W.dir_datos()
    for d in entrada.desktop_ids:
        for carpeta in (os.path.join(base, "flatpak", "exports", "share", "applications"),
                        "/var/lib/flatpak/exports/share/applications"):
            if existe(os.path.join(carpeta, d)):
                return ("id", d)
    if W.flatpak_instalada(entrada.origen["ref"], datos):
        return ("orden", ["flatpak", "run", entrada.origen["ref"]])
    return None


# --------------------------------------------------------------------------------------- el gestor
class Trabajo:
    """Un paso de la cola: una acción sobre una app (y, si hace falta, lo que antes debe quedar hecho)."""

    def __init__(self, entrada, accion, requiere=None):
        self.entrada = entrada
        self.accion = accion
        self.requiere = requiere          # otro Trabajo que debe acabar bien antes (el navegador de una WebApp)
        self.estado = "en_cola"          # en_cola | en_curso | listo | fallo
        self.progreso = None              # 0..1 o None (sin medida)
        self.texto = ""
        self.mensaje = ""                 # el resultado final, listo para enseñar
        self.registro = deque(maxlen=400)
        self.plan = None

    @property
    def activo(self):
        return self.estado in ("en_cola", "en_curso")


class Gestor:
    """El cerebro de la ventana. Todo lo que cambia llama a `al_cambiar(id)` (o con None si cambia todo)."""

    def __init__(self, catalogo, ejecutor=None, estados=None, buscar=None, datos=None, home=None):
        self.catalogo = catalogo
        self.ejecutor = ejecutor or Ejecutor()
        self._consultar = estados or (lambda: consultar_estados(catalogo, datos, buscar=buscar))
        self.buscar = buscar
        self.datos = datos
        self.home = home
        self.estados = {}
        self.trabajos = {}                # id → último Trabajo (en curso, en cola o ya terminado)
        self.cola = deque()
        self.actual = None
        self.al_cambiar = lambda ident: None
        self.al_terminar_trabajo = lambda trabajo: None
        self.refrescar()

    # -- estado
    def refrescar(self):
        self.estados = self._consultar()
        self.al_cambiar(None)

    def estado(self, ident):
        return self.estados.get(ident, SIN_INSTALAR)

    def trabajo(self, ident):
        t = self.trabajos.get(ident)
        return t if t is not None and t.activo else None

    @property
    def ocupado(self):
        return self.actual is not None or bool(self.cola)

    # -- peticiones
    def navegadores_instalados(self):
        return W.encontrar_navegadores(self.buscar)

    def necesita_navegador(self, entrada, accion=INSTALAR):
        return entrada.tipo == "webapp" and accion != QUITAR and not self.navegadores_instalados()

    def pedir(self, ident, accion, navegador=None, forzar=False):
        """Pide una acción. Devuelve una Respuesta:
          ok               → ya está en la cola (o en marcha);
          falta_navegador  → una WebApp sin navegador: `opciones` son los navegadores del catálogo; se vuelve a pedir
                             con navegador=<id> para instalarlo antes;
          poco_espacio     → `mensaje` lo explica; se vuelve a pedir con forzar=True si el usuario insiste;
          ocupado          → esa app ya tiene una operación en marcha;
          error            → no se puede (falta una herramienta…)."""
        entrada = self.catalogo.por_id.get(ident)
        if entrada is None or accion not in ACCIONES:
            return Respuesta("error", "Esa aplicación no está en el catálogo.", ())
        if self.trabajo(ident) is not None:
            return Respuesta("ocupado", "Ya hay una operación en marcha para esta app.", ())
        nav = None
        if self.necesita_navegador(entrada, accion):
            if navegador is None:
                return Respuesta("falta_navegador", "Las WebApps necesitan un navegador basado en Chromium.",
                                 tuple(self.catalogo.navegadores()))
            nav = self.catalogo.por_id.get(navegador)
            if nav is None or not nav.navegador:
                return Respuesta("error", "Ese navegador no está en el catálogo.", ())
        if not forzar:           # el espacio de TODO lo que se va a instalar, antes de encolar nada
            for e in ([nav] if nav is not None else []) + ([entrada] if accion == INSTALAR else []):
                aviso = comprobar_espacio(e, home=self.home) if self.trabajo(e.id) is None else None
                if aviso:
                    return Respuesta("poco_espacio", _texto_espacio(e, aviso), ())
        creados, previo = [], None
        if nav is not None:
            previo = self.trabajo(nav.id)         # si ya se está instalando (o espera turno), se reutiliza
            if previo is None:
                previo = self._encolar(nav, INSTALAR)
                if isinstance(previo, Respuesta):
                    return previo
                creados.append(previo)
        r = self._encolar(entrada, accion, previo)
        if isinstance(r, Respuesta):
            for t in creados:
                self._descartar(t)
            return r
        self._arrancar()
        return Respuesta("ok", "", ())

    def _encolar(self, entrada, accion, requiere=None):
        try:
            plan = plan_para(entrada, accion, self.buscar, datos=self.datos)
        except ErrorPlan as e:
            return Respuesta("error", str(e), ())
        t = Trabajo(entrada, accion, requiere)
        t.plan = plan
        t.texto = "En cola…"
        self.trabajos[entrada.id] = t
        self.cola.append(t)
        self.al_cambiar(entrada.id)
        return t

    def _descartar(self, trabajo):
        if trabajo in self.cola:
            self.cola.remove(trabajo)
        self.trabajos.pop(trabajo.entrada.id, None)
        self.al_cambiar(trabajo.entrada.id)

    def quitar_de_la_cola(self, ident):
        t = self.trabajos.get(ident)
        if t is not None and t.estado == "en_cola" and t in self.cola:
            self._descartar(t)
            return True
        return False

    # -- la cola
    def _arrancar(self):
        if self.actual is not None or not self.cola:
            return
        t = self.cola.popleft()
        if t.requiere is not None and t.requiere.estado == "fallo":
            self._terminar(t, False, f"No se pudo instalar el navegador que necesita {t.entrada.nombre}.")
            return
        self.actual = t
        t.estado = "en_curso"
        t.texto = f"{VERBOS[t.accion][0]} {t.entrada.nombre}…"
        t.progreso = None
        self.al_cambiar(t.entrada.id)
        self.ejecutor.ejecutar(t.plan, lambda ev, t=t: self._evento(t, ev), lambda ok, m, t=t: self._terminar(t, ok, m))

    def _evento(self, t, ev):
        if ev.tipo == "progreso":
            t.progreso = ev.valor
            if ev.texto:
                t.texto = ev.texto
        elif ev.tipo == "log":
            t.registro.append(ev.texto)
        elif ev.tipo == "archivo":
            t.registro.append(f"(lanzador: {ev.texto})")
        self.al_cambiar(t.entrada.id)

    def _terminar(self, t, ok, mensaje):
        t.estado = "listo" if ok else "fallo"
        nombre = t.entrada.nombre
        _g, participio, infinitivo = VERBOS[t.accion]
        t.mensaje = (f"{nombre} {participio}." if ok else f"No se pudo {infinitivo} {nombre}. {mensaje}".strip())
        t.progreso = 1.0 if ok else None
        if self.actual is t:
            self.actual = None
        if ok and t.entrada.navegador and t.accion == INSTALAR:
            self._reparar_webapps()
        self.refrescar_estado(t.entrada.id)
        self.al_terminar_trabajo(t)
        self._arrancar()

    def _reparar_webapps(self):
        try:
            W.reparar_webapps(self.catalogo.apps, self.datos, buscar=None)
        except (OSError, ValueError):
            pass

    def refrescar_estado(self, ident):
        """Vuelve a mirar el estado de todas las apps (es barato: un dpkg-query y unas comprobaciones de ficheros)."""
        self.estados = self._consultar()
        self.al_cambiar(ident)


def _texto_espacio(entrada, aviso):
    return (f"Queda poco espacio para {entrada.nombre}: hay unos {aviso.libre_mb} MB libres y hacen falta cerca de "
            f"{aviso.necesario_mb} MB.")
