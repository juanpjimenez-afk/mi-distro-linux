"""Ajustes de Infinity OS: qué se puede configurar, cómo se valida y cómo se escribe en el sistema.

Este módulo NO usa GTK. La ventana (ajustes_app.py) y el shell lo usan; las pruebas
(shell/tests/test_ajustes.py) lo comprueban sin pantalla.

Para añadir un ajuste:
  1. Una entrada en SCHEMA. La ventana crea el control sola, según su tipo.
  2. Si afecta a un fichero de configuración, una línea en generar().
  3. Si hace falta recargar algo al aplicar, su "aplica" (ver ACCION_POR_APLICA).

Los valores del usuario viven en ~/.config/infinity/ajustes.json. Hyprland y hypridle leen
ficheros que se GENERAN a partir de esos valores; gtklock y foot solo cambian las claves que
gestiona la app y conservan el resto del fichero.
"""
import datetime
import json
import math
import os
import pwd
import re
import secrets
import shlex
import stat
import subprocess
import time
import unicodedata
from collections import namedtuple

from . import fondos as F
from .controles_logica import HUECO_SUPERIOR_MIN

Ajuste = namedtuple("Ajuste", "id pagina etiqueta descripcion tipo defecto aplica minimo maximo opciones")


class OpcionesFondo:
    """Opciones de un ajuste de fondo. No son una lista fija: salen del catálogo del sistema y de los
    fondos que el usuario ha añadido, así que se leen del disco cada vez que se recorren.

    `destino` es "escritorio" (admite además "imagen" = la cascada, y "color") o "bloqueo".
    Valen para todo lo que usa `opciones`: recorrerlas da pares (valor, etiqueta).
    """

    def __init__(self, destino):
        self.destino = destino

    def __iter__(self):
        if self.destino == "escritorio":
            yield F.VALOR_CASCADA, "Cascada"
            yield F.VALOR_COLOR, "Color sólido"
        hay_defecto = False
        for f in F.listar():
            if self.destino == "escritorio":
                if f.escritorio and f.id != F.ID_CASCADA:  # la cascada ya es «imagen»
                    yield f.id, f.nombre
            elif f.bloqueos:
                hay_defecto = hay_defecto or f.id == F.ID_BLOQUEO_DEFECTO
                yield f.id, f.nombre
        if self.destino == "bloqueo" and not hay_defecto:   # sin catálogo ni fondo antiguo, sigue siendo válido
            yield F.ID_BLOQUEO_DEFECTO, "Islandia"

    def normalizar(self, texto):
        """El valor canónico de `texto`, o None si no es una opción. Un id solo se mira si tiene buen formato."""
        if self.destino == "escritorio":
            if texto in (F.VALOR_CASCADA, F.VALOR_COLOR):
                return texto
            if texto == F.ID_CASCADA:  # alias: «imagen» es la cascada
                return F.VALOR_CASCADA
            f = F.buscar(texto)
            return texto if f and f.escritorio else None
        if texto == F.ID_BLOQUEO_DEFECTO:  # siempre vale: hay respaldo aunque falte el catálogo
            return texto
        f = F.buscar(texto)
        return texto if f and f.bloqueos else None


def _a(id, pagina, etiqueta, descripcion, tipo, defecto, aplica, minimo=None, maximo=None, opciones=None):
    return Ajuste(id, pagina, etiqueta, descripcion, tipo, defecto, aplica, minimo, maximo, opciones)


# Páginas de la ventana, en orden. Fecha y hora, Atajos y Sistema no tienen ajustes en el esquema: se muestran a mano
# (la hora y la zona son del sistema, no de ajustes.json: se cambian al momento con timedatectl).
PAGINAS = [
    ("escritorio", "Escritorio"),
    ("dock", "Dock y estado"),
    ("fecha", "Fecha y hora"),
    ("bloqueo", "Bloqueo y energía"),
    ("teclado", "Teclado y ratón"),
    ("terminal", "Terminal"),
    ("panki", "pAnkI (IA)"),
    ("atajos", "Atajos"),
    ("sistema", "Sistema"),
]

# "aplica" indica qué hay que recargar al pulsar Aplicar (ver ACCION_POR_APLICA).
SCHEMA = [
    # ---------------------------------------------------------------- escritorio
    _a("escritorio.fondo", "escritorio", "Fondo del escritorio",
       "Elige una imagen, añade la tuya o usa un color sólido. En un equipo lento, el color gasta mucho menos CPU.",
       "choice", "imagen", "fondo", opciones=OpcionesFondo("escritorio")),
    _a("escritorio.color", "escritorio", "Color del fondo sólido",
       "Se ve cuando el fondo es de color.", "color", "#15193a", "hypr"),
    _a("escritorio.marca", "escritorio", "Marca ∞ sobre el fondo de color",
       "Un ∞ muy tenue en el centro. Solo se ve con el fondo de color.", "bool", True, "shell"),
    _a("escritorio.escala", "escritorio", "Escala de pantalla",
       "Tamaño de todo en pantalla.", "choice", "1", "hypr",
       opciones=[("1", "100 %"), ("1.25", "125 %"), ("1.5", "150 %"), ("2", "200 %")]),
    _a("cristal.desenfoque", "escritorio", "Efecto cristal (desenfoque)",
       "Los paneles y las ventanas translúcidas desenfocan lo que hay detrás. Es lo más caro en una VM.",
       "bool", True, "hypr"),
    _a("cristal.pasadas", "escritorio", "Calidad del desenfoque",
       "Más pasadas: desenfoque más suave y más CPU.", "int", 3, "hypr", minimo=1, maximo=4),
    _a("cristal.opacidad", "escritorio", "Opacidad de los paneles",
       "Cuánto tapan los paneles de cristal lo que hay detrás. Por debajo de 0,30 el desenfoque deja de verse.",
       "float", 0.42, "shell", minimo=0.3, maximo=0.9),
    _a("ventanas.hueco", "escritorio", "Separación entre ventanas (px)", "",
       "int", 6, "hypr", minimo=0, maximo=30),
    _a("ventanas.redondeo", "escritorio", "Esquinas redondeadas (px)", "",
       "int", 22, "hypr", minimo=0, maximo=40),
    _a("ventanas.sombras", "escritorio", "Sombras de las ventanas", "",
       "bool", True, "hypr"),
    _a("ventanas.animaciones", "escritorio", "Animaciones", "Transiciones al abrir y cerrar.",
       "bool", True, "hypr"),

    # ----------------------------------------------------------------- dock y estado
    _a("dock.reloj", "dock", "Formato de la hora", "Reloj de la esquina inferior derecha.",
       "choice", "24h", "shell", opciones=[("24h", "24 horas"), ("12h", "12 horas (AM/PM)")]),
    _a("buscador.confirmar_s", "dock", "Segundos para confirmar apagar, reiniciar o cerrar sesión",
       "Tras pulsar Enter en el buscador, hay que volver a pulsarlo antes de este tiempo.",
       "int", 3, "shell", minimo=1, maximo=10),

    # --------------------------------------------------------- bloqueo y energía
    _a("bloqueo.fondo", "bloqueo", "Fondo de la pantalla de bloqueo",
       "Elige una imagen o añade la tuya.", "choice", F.ID_BLOQUEO_DEFECTO, "bloqueo",
       opciones=OpcionesFondo("bloqueo")),
    _a("bloqueo.minutos", "bloqueo", "Bloquear tras (minutos sin actividad)",
       "Muestra la pantalla de bloqueo.", "int", 10, "hypridle", minimo=1, maximo=120),
    _a("bloqueo.apagar_pantalla", "bloqueo", "Apagar la pantalla tras (minutos sin actividad)",
       "Tiene que ser más tarde que el bloqueo.", "int", 11, "hypridle", minimo=2, maximo=180),
    _a("bloqueo.suspender", "bloqueo", "Suspender tras (minutos; 0 = nunca)",
       "Si lo activas, tiene que ser más tarde que apagar la pantalla.",
       "int", 0, "hypridle", minimo=0, maximo=480),
    _a("bloqueo.antes_de_dormir", "bloqueo", "Bloquear antes de dormir",
       "Al suspender o cerrar la tapa, la pantalla queda bloqueada.", "bool", True, "hypridle"),
    _a("bloqueo.hora", "bloqueo", "Formato de la hora en la pantalla de bloqueo", "",
       "choice", "%H\\n%M", "bloqueo",
       opciones=[("%H\\n%M", "24 h, en dos líneas"), ("%I\\n%M", "12 h, en dos líneas"),
                 ("%H:%M", "24 h, en una línea"), ("%I:%M %p", "12 h, en una línea")]),
    _a("bloqueo.ocultar_contrasena_s", "bloqueo", "Ocultar la contraseña tras (segundos sin tocar nada)",
       "En la pantalla de bloqueo, el campo de contraseña se esconde tras este tiempo.",
       "int", 10, "bloqueo", minimo=3, maximo=60),

    # --------------------------------------------------------- teclado y ratón
    _a("teclado.distribucion", "teclado", "Distribución del teclado",
       "Del escritorio. La consola se fija al construir la ISO.", "choice", "latam", "hypr",
       opciones=[("latam", "Latinoamérica"), ("es", "España"), ("us", "Estados Unidos"),
                 ("gb", "Reino Unido"), ("de", "Alemania"), ("fr", "Francia"), ("br", "Brasil")]),
    _a("raton.sensibilidad", "teclado", "Velocidad del ratón", "De -1 (lento) a 1 (rápido).",
       "float", 0.0, "hypr", minimo=-1.0, maximo=1.0),
    _a("tactil.scroll_natural", "teclado", "Desplazamiento natural (táctil)",
       "El contenido sigue al dedo, como en un móvil.", "bool", True, "hypr"),
    _a("tactil.toque_para_clic", "teclado", "Tocar para hacer clic (táctil)", "",
       "bool", True, "hypr"),

    # ------------------------------------------------------------- terminal
    _a("terminal.fuente", "terminal", "Tipografía de la terminal",
       "Nombre de una fuente instalada en el sistema.", "texto", "JetBrains Mono", "nuevo", maximo=40),
    _a("terminal.tamano", "terminal", "Tamaño de letra", "", "int", 11, "nuevo", minimo=6, maximo=24),
    _a("terminal.opacidad", "terminal", "Transparencia de la terminal",
       "1 = opaca. Con el efecto cristal se ve lo que hay detrás.", "float", 0.55, "nuevo",
       minimo=0.2, maximo=1.0),

    # ----------------------------------------------------------------- pAnkI
    # La clave de Gemini NO es un ajuste: ajustes.json se exporta. Se guarda desde la ventana de pAnkI.
    _a("panki.modelo", "panki", "Modelo de Gemini",
       "Nombre del modelo que usa pAnkI. Es texto libre porque los nombres cambian: pon el que admita tu clave.",
       "texto", "gemini-flash-latest", "conversacion", maximo=60),
    _a("panki.comandos", "panki", "Permitir que pAnkI proponga comandos de terminal",
       "Siempre pide confirmación, y la acción se muestra completa antes de ejecutarse.",
       "bool", False, "conversacion"),
    _a("panki.burbuja", "panki", "Mostrar la burbuja de pAnkI en el escritorio",
       "La esfera de cristal de la derecha: al pulsarla se abre o se cierra la ventana de pAnkI.",
       "bool", True, "shell"),
    _a("panki.nombre", "panki", "Nombre de tu asistente",
       "Cómo se llama y se presenta en su ventana. Puedes cambiarlo también desde dentro de la propia app.",
       "texto", "pAnkI", "conversacion", maximo=24),
]

DEFECTOS = {a.id: a.defecto for a in SCHEMA}
_POR_ID = {a.id: a for a in SCHEMA}

# Qué recargar según "aplica". "nuevo" no recarga nada: afecta a lo que se abra después.
# "bloqueo" tampoco: gtklock e infinity-lock leen sus ficheros cada vez que se bloquea la pantalla.
# "conversacion" tampoco: pAnkI lee el ajuste al empezar (ver panki_app.py).
ACCION_POR_APLICA = {
    "hypr": ("hypr",),
    "hypridle": ("hypridle",),
    "fondo": ("wallpaper", "shell"),
    "shell": ("shell",),
    "nuevo": (),
    "bloqueo": (),  # el fondo del bloqueo se lee la próxima vez que se bloquea (infinity-lock)
    "conversacion": (),
}
ORDEN_ACCIONES = ("hypr", "hypridle", "wallpaper", "shell")

# (argv, en_segundo_plano, obligatorio). Si una orden obligatoria falla, la app lo dice.
COMANDOS = {
    "hypr": [(["hyprctl", "reload"], False, True)],
    "hypridle": [(["pkill", "-x", "hypridle"], False, False), (["hypridle"], True, True)],
    "wallpaper": [(["pkill", "-x", "swaybg"], False, False), (["infinity-wallpaper"], True, True)],
    "shell": [(["gapplication", "action", "org.infinity.Shell", "recargar"], False, False)],
}

_COLOR = re.compile(r"#[0-9a-fA-F]{6}")
_TEXTO = re.compile(r"[A-Za-z0-9 .'_-]+")
# Un ajustes.json de verdad mide unos 2 KB. Más de esto no es un fichero de ajustes: ni se lee entero.
MAX_BYTES_AJUSTES = 1 << 20


# ------------------------------------------------------------------ rutas
def dir_config():
    return os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")


def ruta_ajustes():
    return os.path.join(dir_config(), "infinity", "ajustes.json")


# --------------------------------------------------------------- validación
def _texto_de(valor):
    """Las opciones son texto: "1" aunque el JSON traiga 1 o 1.0."""
    if isinstance(valor, bool):
        return None
    if isinstance(valor, int):   # sin pasar por float: un entero de 400 cifras no cabe en uno
        return str(valor)
    if isinstance(valor, float):
        return f"{valor:g}"
    return valor if isinstance(valor, str) else None


def _nombre_visible(texto, maximo):
    """El nombre del asistente, como lo acepta la página de pAnkI (sanitizeNombre en panki_web/app.html):
    espacios juntados, entre 1 y `maximo` caracteres (JavaScript cuenta unidades UTF-16), sin < ni > y sin
    caracteres de control. Así lo que el usuario se pone dentro de pAnkI también vale aquí."""
    n = " ".join(texto.split())   # tabuladores y saltos de línea cuentan como espacios, igual que en la página
    if not n.isprintable():
        raise ValueError("no puede llevar caracteres de control")
    if not n or len(n.encode("utf-16-le")) // 2 > maximo or "<" in n or ">" in n:
        raise ValueError(f"entre 1 y {maximo} caracteres, sin < ni >")
    return n


# Ajustes de texto que aceptan más que el patrón general (ASCII): id → función(texto, maximo) → texto.
_TEXTO_ESPECIAL = {"panki.nombre": _nombre_visible}


def _coaccionar(a, valor):
    """Devuelve el valor normalizado o lanza ValueError con un mensaje para el usuario."""
    t = a.tipo
    if t == "bool":
        if isinstance(valor, bool):
            return valor
        raise ValueError("debe ser sí o no")
    if t in ("int", "float"):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)):
            raise ValueError("debe ser un número")
        if isinstance(valor, float):
            if not math.isfinite(valor):
                raise ValueError("debe ser un número")
            if t == "int":
                if valor != int(valor):
                    raise ValueError("debe ser un número entero")
                valor = int(valor)
            else:
                valor = round(valor, 3)
        # Un entero enorme (el JSON los admite de cientos de cifras) se compara tal cual: no se convierte a float.
        if not a.minimo <= valor <= a.maximo:
            raise ValueError(f"debe estar entre {a.minimo:g} y {a.maximo:g}")
        return float(valor) if t == "float" else valor
    if t == "choice":
        texto = _texto_de(valor)
        if isinstance(a.opciones, OpcionesFondo):
            canonico = a.opciones.normalizar(texto)
            if canonico is None:
                raise ValueError("ese fondo no existe")
            return canonico
        if texto not in [o for o, _ in a.opciones]:
            raise ValueError("opción no válida")
        return texto
    if t == "color":
        if not isinstance(valor, str) or not _COLOR.fullmatch(valor):
            raise ValueError("debe ser un color como #15193a")
        return valor.lower()
    if t == "texto":
        if a.id in _TEXTO_ESPECIAL:
            if not isinstance(valor, str):
                raise ValueError(f"entre 1 y {a.maximo} caracteres, sin < ni >")
            return _TEXTO_ESPECIAL[a.id](valor, a.maximo)
        texto = valor.strip() if isinstance(valor, str) else ""
        if not texto or len(texto) > a.maximo or not _TEXTO.fullmatch(texto):
            raise ValueError(f"solo letras sin tilde, números, espacios y - _ . ' (máximo {a.maximo} caracteres)")
        return texto
    raise ValueError("tipo desconocido")


def validar(valores):
    """Valida un diccionario. Devuelve (valores_normalizados, errores).

    `errores` es una lista de (id, mensaje). Un valor con error se sustituye por el defecto en el
    resultado, pero la app no debe aplicar nada mientras haya errores.
    """
    normal, errores = {}, []
    for a in SCHEMA:
        if a.id not in valores:
            normal[a.id] = a.defecto
            continue
        try:
            normal[a.id] = _coaccionar(a, valores[a.id])
        except ValueError as e:
            errores.append((a.id, f"{a.etiqueta}: {e}"))
            normal[a.id] = a.defecto
        except ArithmeticError:   # cinturón y tirantes: un número que no se puede operar es un valor no válido
            errores.append((a.id, f"{a.etiqueta}: valor no válido"))
            normal[a.id] = a.defecto
    if not errores:  # reglas que relacionan varios ajustes
        bloq, apag, susp = normal["bloqueo.minutos"], normal["bloqueo.apagar_pantalla"], normal["bloqueo.suspender"]
        if apag <= bloq:
            errores.append(("bloqueo.apagar_pantalla", "Apagar la pantalla tiene que ser más tarde que bloquear."))
        if susp and susp <= apag:
            errores.append(("bloqueo.suspender", "Suspender tiene que ser más tarde que apagar la pantalla (o 0 = nunca)."))
    return normal, errores


def _leer_json(ruta):
    """El JSON de `ruta`. Lanza OSError o ValueError, y nada más: ni RecursionError con miles de niveles anidados
    ni MemoryError con un fichero enorme (un ajustes.json ajeno o corrupto no puede tumbar la app ni el shell)."""
    with open(ruta, "rb") as f:
        crudo = f.read(MAX_BYTES_AJUSTES + 1)
    if len(crudo) > MAX_BYTES_AJUSTES:
        raise ValueError("es demasiado grande para ser un fichero de ajustes")
    try:
        texto = crudo.decode("utf-8-sig")   # utf-8-sig: el Bloc de notas de Windows guarda con BOM
    except UnicodeDecodeError:
        raise ValueError("no es un archivo de texto") from None
    try:
        return json.loads(texto)
    except RecursionError:
        raise ValueError("tiene demasiados niveles anidados") from None
    except json.JSONDecodeError as e:
        raise ValueError(f"no es un JSON válido (línea {e.lineno})") from None


def cargar(ruta=None):
    """Lee los ajustes del usuario. Un fichero dañado no rompe nada: se usan los valores por defecto.

    Devuelve (valores, avisos). Los avisos explican qué se ignoró.
    """
    ruta = ruta or ruta_ajustes()
    try:
        datos = _leer_json(ruta)
    except FileNotFoundError:
        return dict(DEFECTOS), []
    except OSError as e:
        return dict(DEFECTOS), [f"No se pudo leer {ruta} ({e.strerror or 'error de lectura'}); "
                                "se usan los valores por defecto."]
    except ValueError as e:
        return dict(DEFECTOS), [f"{ruta} está dañado o no es un fichero de ajustes ({e}); "
                                "se usan los valores por defecto."]
    if not isinstance(datos, dict):
        return dict(DEFECTOS), [f"{ruta} no tiene el formato esperado; se usan los valores por defecto."]
    valores, errores = validar(datos)
    return valores, [m for _, m in errores]


def valor(id, ruta=None):
    """Un solo ajuste, para el shell. No lanza excepciones."""
    v, _ = cargar(ruta)
    return v.get(id, DEFECTOS.get(id))


def importar(ruta, base=None):
    """Lee un fichero exportado. Devuelve (valores, errores); el llamador decide si lo aplica.

    Lo que el fichero no menciona (uno exportado por una versión anterior no conoce los ajustes nuevos) vuelve
    al valor por defecto, o conserva el de `base` si se da.
    Lanza OSError o ValueError si el fichero no se puede leer como JSON de ajustes.
    """
    datos = _leer_json(ruta)
    if not isinstance(datos, dict):
        raise ValueError("el fichero no contiene ajustes de Infinity")
    # Un fondo que añadió el usuario en otro equipo no existe aquí: no tiene sentido rechazar todo el fichero
    # por eso, y vuelve al fondo por defecto.
    for id_ in ("escritorio.fondo", "bloqueo.fondo"):
        a = _POR_ID[id_]
        if id_ in datos and isinstance(datos[id_], str) and a.opciones.normalizar(datos[id_]) is None:
            datos = dict(datos)
            datos[id_] = a.defecto
    if base:
        datos = {**{k: v for k, v in base.items() if k in DEFECTOS and k not in datos}, **datos}
    return validar(datos)


def cambios_externos(base, ruta=None):
    """Ajustes que otro programa ha cambiado en ajustes.json respecto a `base` (lo último que esta ventana leyó o
    guardó): {id: valor}. Solo cuentan los que el fichero menciona y valen; si no se puede leer, no hay cambios.

    pAnkI guarda su nombre y la herramienta cambiar_ajuste escribe ajustes.json mientras Ajustes puede estar abierta.
    """
    try:
        datos = _leer_json(ruta or ruta_ajustes())
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    valores, errores = validar(datos)
    malos = {i for i, _ in errores}
    return {k: valores[k] for k in DEFECTOS
            if k in datos and k not in malos and valores[k] != base.get(k)}


def _json_texto(valores, extra=None):
    contenido = dict(extra or {})
    contenido.update({k: valores[k] for k in DEFECTOS})
    return json.dumps(contenido, ensure_ascii=False, indent=1, sort_keys=True) + "\n"


def guardar_json(valores, ruta=None):
    """Escribe un fichero de ajustes de forma atómica (exportar). Solo guarda los ajustes conocidos."""
    ruta = ruta or ruta_ajustes()
    _confirmar([_preparar(ruta, _json_texto(valores))])


def cambiados(antes, despues):
    """Ids de los ajustes que cambian entre dos conjuntos de valores."""
    return [k for k in despues if antes.get(k) != despues[k]]


def acciones_para(ids):
    """Acciones de recarga necesarias para aplicar estos ajustes, en el orden correcto."""
    pedidas = set()
    for i in ids:
        pedidas.update(ACCION_POR_APLICA.get(_POR_ID[i].aplica, ()))
    return ordenar_acciones(pedidas)


# ---------------------------------------------------------------- ficheros
def _leer(ruta):
    """El texto de un fichero que se edita en su sitio ("" si no existe). Los bytes que no son UTF-8 se conservan
    (surrogateescape) para devolverlos tal cual al escribir."""
    try:
        with open(ruta, encoding="utf-8", errors="surrogateescape") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _preparar(ruta, texto):
    """Primera mitad de una escritura atómica: deja `texto` en un temporal junto a `ruta` (mismo directorio, así el
    reemplazo es atómico) y devuelve (temporal, destino). Si `ruta` es un enlace simbólico (dotfiles con stow), el
    destino es el fichero al que apunta: el enlace se respeta. Conserva el modo del fichero que sustituye."""
    destino = os.path.realpath(ruta)
    if os.path.isdir(destino):
        raise IsADirectoryError(21, os.strerror(21), destino)
    carpeta = os.path.dirname(destino)
    os.makedirs(carpeta, exist_ok=True)
    try:
        modo = stat.S_IMODE(os.stat(destino).st_mode)
    except OSError:
        modo = None
    temporal = f"{destino}.{secrets.token_hex(4)}.tmp"
    fd = os.open(temporal, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)   # 0o666 menos la umask, como open()
    try:
        f = os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape")
    except BaseException:
        os.close(fd)
        _descartar([(temporal, destino)])
        raise
    try:
        with f:
            if modo is not None:
                os.fchmod(f.fileno(), modo)
            f.write(texto)
            f.flush()
            os.fsync(f.fileno())
    except BaseException:
        _descartar([(temporal, destino)])
        raise
    return temporal, destino


def _descartar(preparados):
    for temporal, _ in preparados:
        try:
            os.unlink(temporal)
        except OSError:
            pass


def _confirmar(preparados):
    """Segunda mitad: sustituye cada destino por su temporal. Si algo falla, borra los temporales que quedan."""
    hechos = 0
    try:
        for temporal, destino in preparados:
            os.replace(temporal, destino)
            hechos += 1
    finally:
        _descartar(preparados[hechos:])


def _escribir(ruta, texto):
    """Escribe en un temporal y lo reemplaza: un corte a mitad no deja el fichero a medias."""
    _confirmar([_preparar(ruta, texto)])


def _bool(b):
    return "true" if b else "false"


def hypr_conf(v):
    """infinity-ajustes.conf: Hyprland lo lee al final de hyprland.conf, así que sus valores mandan."""
    lateral = v["ventanas.hueco"] + 8
    # El hueco de arriba es 20 px mayor (y nunca menor que HUECO_SUPERIOR_MIN): ahí van los controles de ventana.
    superior = max(HUECO_SUPERIOR_MIN, lateral + 20)
    return f"""# Generado por Ajustes de Infinity OS: no lo edites a mano, usa la app de Ajustes.
# Se lee al final de hyprland.conf: lo que hay aquí tiene prioridad sobre lo de allí.

monitor = , preferred, auto, {v["escritorio.escala"]}

input {{
    kb_layout    = {v["teclado.distribucion"]}
    follow_mouse = 1
    sensitivity  = {v["raton.sensibilidad"]:g}
    touchpad {{
        natural_scroll = {_bool(v["tactil.scroll_natural"])}
        tap-to-click   = {_bool(v["tactil.toque_para_clic"])}
    }}
}}

general {{
    gaps_in  = {v["ventanas.hueco"]}
    gaps_out = {superior}, {lateral}, {lateral}, {lateral}
}}

decoration {{
    rounding = {v["ventanas.redondeo"]}
    shadow {{
        enabled = {_bool(v["ventanas.sombras"])}
    }}
    blur {{
        enabled = {_bool(v["cristal.desenfoque"])}
        passes  = {v["cristal.pasadas"]}
    }}
}}

animations {{
    enabled = {_bool(v["ventanas.animaciones"])}
}}

misc {{
    background_color = rgb({v["escritorio.color"].lstrip("#")})
}}
"""


def hypridle_conf(v):
    """hypridle.conf completo. Los tiempos son segundos; hypridle los quiere en orden creciente."""
    antes = "    before_sleep_cmd = loginctl lock-session\n" if v["bloqueo.antes_de_dormir"] else ""
    suspender = ""
    if v["bloqueo.suspender"]:
        suspender = f"""
# Tras {v["bloqueo.suspender"]} min: suspender
listener {{
    timeout    = {v["bloqueo.suspender"] * 60}
    on-timeout = systemctl suspend
}}
"""
    return f"""# Generado por Ajustes de Infinity OS (los tiempos son segundos). Cámbialos desde la app de Ajustes.
general {{
    lock_cmd         = infinity-lock
{antes}    after_sleep_cmd  = hyprctl dispatch dpms on
}}

# Tras {v["bloqueo.minutos"]} min sin actividad: bloquear
listener {{
    timeout    = {v["bloqueo.minutos"] * 60}
    on-timeout = loginctl lock-session
}}

# Tras {v["bloqueo.apagar_pantalla"]} min: apagar la pantalla
listener {{
    timeout    = {v["bloqueo.apagar_pantalla"] * 60}
    on-timeout = hyprctl dispatch dpms off
    on-resume  = hyprctl dispatch dpms on
}}
{suspender}"""


def ini_set(texto, seccion, clave, valor):
    """Cambia una clave de un INI conservando el resto (comentarios incluidos).

    Si la clave no existe en la sección, se añade al final de esa sección; si no existe la sección,
    se crea al final del fichero.
    """
    lineas = texto.splitlines()
    en_seccion = False
    ultima = None
    for i, linea in enumerate(lineas):
        s = linea.strip()
        if s.startswith("[") and s.endswith("]"):
            if en_seccion:
                break
            en_seccion = s[1:-1].strip() == seccion
            if en_seccion:
                ultima = i
            continue
        if en_seccion:
            if s and not s.startswith("#") and s.split("=", 1)[0].strip() == clave:
                lineas[i] = f"{clave}={valor}"
                return "\n".join(lineas) + "\n"
            if s:  # una clave nueva va tras la última línea con contenido, no tras la línea vacía
                ultima = i
    if ultima is not None:
        lineas.insert(ultima + 1, f"{clave}={valor}")
    else:
        lineas += ([""] if lineas else []) + [f"[{seccion}]", f"{clave}={valor}"]
    return "\n".join(lineas) + "\n"


def gtklock_config(texto, v):
    """~/.config/gtklock/config.ini: solo cambia las claves que gestiona la app."""
    texto = ini_set(texto, "main", "time-format", v["bloqueo.hora"])
    return ini_set(texto, "main", "idle-timeout", v["bloqueo.ocultar_contrasena_s"])


def foot_ini(texto, v):
    """~/.config/foot/foot.ini: fuente, tamaño y transparencia. El resto de la paleta se conserva."""
    texto = ini_set(texto, "main", "font", f'{v["terminal.fuente"]}:size={v["terminal.tamano"]}')
    return ini_set(texto, "colors", "alpha", f'{v["terminal.opacidad"]:g}')


def estilo_css(v):
    """Override del estilo del shell: la opacidad de los paneles de cristal y del orbe.

    Se carga después de style.css (prioridad de usuario), así que sus valores mandan.
    """
    return f"""/* Generado por Ajustes de Infinity OS. Se carga después de style.css. */
.glass,
button.orb {{
  background-color: rgba(18, 22, 44, {v["cristal.opacidad"]:g});
}}
"""


def generar(v, textos=None):
    """Todos los ficheros que la app escribe, a partir de los valores.

    `textos` es {ruta_relativa: contenido_actual} para los INI que se editan en su sitio.
    Devuelve {ruta_relativa: contenido_nuevo}, relativo a ~/.config.
    """
    textos = textos or {}
    return {
        "hypr/infinity-ajustes.conf": hypr_conf(v),
        "hypr/hypridle.conf": hypridle_conf(v),
        "gtklock/config.ini": gtklock_config(textos.get("gtklock/config.ini", ""), v),
        "foot/foot.ini": foot_ini(textos.get("foot/foot.ini", ""), v),
        "infinity/estilo.css": estilo_css(v),
        "infinity/fondo": v["escritorio.fondo"] + "\n",
        "infinity/fondo-bloqueo": v["bloqueo.fondo"] + "\n",
    }


def _desconocidos(ruta):
    """Lo que hay en un ajustes.json y no es de este esquema (ajustes de otra versión): se conserva al guardar."""
    try:
        datos = _leer_json(ruta)
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in datos.items() if k not in DEFECTOS} if isinstance(datos, dict) else {}


def escribir(valores, base=None):
    """Guarda ajustes.json y regenera los ficheros de configuración. Devuelve las rutas escritas.

    Todo o nada en lo que importa: primero se preparan todos los temporales (aquí fallan el disco lleno, una carpeta
    sin permiso, un archivo donde tendría que haber una carpeta) y solo si todos están listos se reemplazan. ajustes.json
    va el último: si algo se corta en medio, sigue diciendo lo de antes y volver a pulsar Aplicar lo deja todo coherente.
    """
    base = base or dir_config()
    textos = {rel: _leer(os.path.join(base, rel)) for rel in ("gtklock/config.ini", "foot/foot.ini")}
    ficheros = generar(valores, textos)
    ruta_json = os.path.join(base, "infinity", "ajustes.json")
    preparados = []
    try:
        for rel, texto in ficheros.items():
            preparados.append(_preparar(os.path.join(base, rel), texto))
        preparados.append(_preparar(ruta_json, _json_texto(valores, _desconocidos(ruta_json))))
    except BaseException:
        _descartar(preparados)
        raise
    _confirmar(preparados)
    return [os.path.join(base, rel) for rel in ficheros]


# ---------------------------------------------------------------- recarga
def _motivo_error(e):
    """Por qué no se pudo lanzar una orden, en una frase que se puede enseñar (sin la traza de Python ni el argv)."""
    if isinstance(e, subprocess.TimeoutExpired):
        return "no respondió a tiempo"
    if isinstance(e, FileNotFoundError):
        return "no está instalada"
    if isinstance(e, PermissionError):
        return "no se pudo ejecutar (sin permiso)"
    if isinstance(e, OSError) and e.strerror:
        return e.strerror
    return "no se pudo ejecutar"


def correr_real(argv, detach=False, timeout=15):
    """Ejecuta una orden (sin shell: argv es una lista). Devuelve (ok, texto_de_error).

    `timeout` son los segundos que se espera a que termine (una orden que pide permiso al usuario necesita más)."""
    try:
        if detach:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True, ""
        r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True, errors="replace",
                           timeout=timeout)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, _motivo_error(e)


def ejecutar_acciones(acciones, correr=None):
    """Ejecuta las acciones de recarga. Devuelve [(argv, salida)] de las que fallaron.

    `correr` se puede sustituir: las pruebas no ejecutan nada de verdad.
    """
    correr = correr or correr_real
    fallos = []
    for accion in acciones:
        for argv, detach, obligatorio in COMANDOS[accion]:
            ok, salida = correr(argv, detach)
            if not ok and obligatorio:
                fallos.append((argv, salida))
    return fallos


def ordenar_acciones(acciones):
    """Las acciones sin repetir y en el orden en que hay que ejecutarlas."""
    pedidas = set(acciones)
    return [a for a in ORDEN_ACCIONES if a in pedidas]


def acciones_fallidas(fallos):
    """A qué acciones pertenecen los fallos de `ejecutar_acciones`: lo que hay que reintentar."""
    argvs = {tuple(argv) for argv, _ in fallos}
    return [a for a in ORDEN_ACCIONES if any(tuple(argv) in argvs for argv, _, _ in COMANDOS[a])]


# Qué es cada acción, para decírselo al usuario sin enseñarle nombres de programas ni salidas del sistema.
DESCRIPCION_ACCION = {
    "hypr": "la configuración de Hyprland",
    "hypridle": "el bloqueo y apagado automáticos",
    "wallpaper": "el fondo de pantalla",
    "shell": "el panel del escritorio",
}


# ----------------------------------------------------- contraseña de la cuenta
# Sección "Cuenta" de Sistema. Esta app NUNCA recibe, guarda ni registra una contraseña: passwd la
# pide dentro de una terminal, y aquí solo se abre esa terminal con una lista fija de argumentos.
#
# `passwd -S` sin argumentos muestra el estado de la propia cuenta sin root (passwd es setuid y no
# deja ver otras cuentas). Salida: "usuario P|NP|L fecha mín máx aviso inactividad".
_PASSWD_S = re.compile(r"\S+ (P|NP|L) \S+ (?:-?\d+ ){3}-?\d+")
_ESTADO_PASSWD = {"P": "con", "NP": "sin", "L": "bloqueada"}


def parsear_passwd_s(salida):
    """Interpreta `passwd -S`: "con", "sin" o "bloqueada". None si la salida no es la esperada."""
    m = _PASSWD_S.fullmatch((salida or "").strip())
    return _ESTADO_PASSWD[m.group(1)] if m else None


def _correr_passwd_s(argv):
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       errors="replace", timeout=5)
    return r.returncode, r.stdout


def leer_estado_contrasena(correr=None):
    """Estado de la contraseña de esta cuenta, o None si no se pudo leer.

    `correr(argv) -> (código, salida)` se puede sustituir en las pruebas.
    """
    try:
        codigo, salida = (correr or _correr_passwd_s)(["passwd", "-S"])
    except (OSError, subprocess.SubprocessError):
        return None
    return parsear_passwd_s(salida) if codigo == 0 else None


def usuario_actual():
    try:
        return pwd.getpwuid(os.getuid()).pw_name
    except KeyError:
        return os.environ.get("USER") or "USUARIO"


def resumen_contrasena(estado, usuario):
    """Lo que muestra la sección Cuenta: (titular, ayuda, se_puede_cambiar)."""
    if estado == "con":
        return ("Con contraseña",
                "La pantalla de bloqueo y el inicio de sesión la piden.", True)
    if estado == "sin":
        return ("Sin contraseña: la pantalla de bloqueo y el inicio de sesión aceptan cualquier texto.",
                "Mientras no tenga contraseña, quien esté delante del equipo entra sin ella.", True)
    if estado == "bloqueada":
        # Con la cuenta bloqueada, passwd de un usuario normal falla: solo puede hacerlo root.
        return ("Cuenta bloqueada: no entra ninguna contraseña",
                f"Aquí no se puede cambiar. Un administrador puede fijar una con: sudo passwd {usuario}",
                False)
    return ("Estado desconocido",
            "No se pudo leer si la cuenta tiene contraseña. Puedes cambiarla igualmente.", True)


def argv_cambiar_contrasena():
    """La terminal que ejecuta passwd. Lista fija: sin shell y sin nada interpolado.

    --hold deja la ventana abierta al terminar, para leer el resultado de passwd.
    """
    return ["foot", "--hold", "--title", "Cambiar contraseña", "passwd"]


def cambiar_contrasena(correr=None):
    """Abre la terminal con passwd. Devuelve (ok, texto_de_error).

    El nombre del programa lo pone quien llama (correr_real devuelve solo el motivo), así que si falla
    se antepone la terminal: «La terminal «foot» no está instalada.»."""
    argv = argv_cambiar_contrasena()
    ok, error = (correr or correr_real)(argv, True)
    if not ok and error:
        error = f"La terminal «{argv[0]}» {error}."
    return ok, error


# ------------------------------------------------------------------ fecha y hora
# Página «Fecha y hora». Cambiar la hora, la fecha o la zona exige permisos de administrador. Se lanza `timedatectl`
# (siempre como lista de argumentos: sin shell y sin texto crudo interpolado) con este orden:
#   1. `sudo -n timedatectl …`: en la ISO en vivo casper deja al usuario en sudoers sin contraseña
#      (/etc/sudoers.d/casper). `-n` = nunca pregunta: si hiciera falta contraseña, falla al instante.
#   2. `timedatectl …` a secas: habla con systemd-timedated y polkit pide permiso al agente de la sesión
#      (policykit-1-gnome, que arranca hyprland.conf). No hace falta pkexec, que la ISO no instala.
# Esta app NUNCA recibe, guarda ni registra una contraseña: la teclea el usuario en la ventana del agente.

# Nombres propios en español: ni strftime ni el idioma del sistema (el reloj sale igual con cualquier LANG).
DIAS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")   # índice = date.weekday()
DIAS_CORTOS = ("lun", "mar", "mié", "jue", "vie", "sáb", "dom")
MESES = ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre")
MESES_CORTOS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")
ANIO_MIN, ANIO_MAX = 1970, 2099   # lo que admite el reloj de la placa (RTC)
ESPERA_PERMISO_S = 120            # el usuario puede tardar en escribir su contraseña en la ventana de polkit


def es_bisiesto(anio):
    return anio % 4 == 0 and (anio % 100 != 0 or anio % 400 == 0)


def dias_del_mes(anio, mes):
    if mes == 2:
        return 29 if es_bisiesto(anio) else 28
    return 30 if mes in (4, 6, 9, 11) else 31


def _entero(valor, nombre):
    """Un entero de verdad: ni bool, ni decimal, ni texto («3» no es una hora)."""
    if isinstance(valor, bool) or not isinstance(valor, int):
        raise ValueError(f"{nombre} tiene que ser un número entero.")
    return valor


def validar_fecha_hora(anio, mes, dia, hora, minuto, segundo):
    """La fecha y hora como datetime, o ValueError con un mensaje para el usuario (existe el día, 29 de febrero solo
    en bisiestos, hora 0–23, minutos y segundos 0–59)."""
    anio, mes, dia = _entero(anio, "El año"), _entero(mes, "El mes"), _entero(dia, "El día")
    hora, minuto, segundo = _entero(hora, "La hora"), _entero(minuto, "Los minutos"), _entero(segundo, "Los segundos")
    if not ANIO_MIN <= anio <= ANIO_MAX:
        raise ValueError(f"El año tiene que estar entre {ANIO_MIN} y {ANIO_MAX}.")
    if not 1 <= mes <= 12:
        raise ValueError("El mes tiene que estar entre 1 y 12.")
    maximo = dias_del_mes(anio, mes)
    if not 1 <= dia <= maximo:
        if (mes, dia) == (2, 29):
            raise ValueError(f"El 29 de febrero de {anio} no existe: {anio} no es bisiesto "
                             f"(febrero tiene 28 días).")
        raise ValueError(f"En {MESES[mes - 1]} de {anio} solo hay {maximo} días: el día {dia} no existe.")
    if not 0 <= hora <= 23:
        raise ValueError("La hora tiene que estar entre 0 y 23.")
    if not 0 <= minuto <= 59:
        raise ValueError("Los minutos tienen que estar entre 0 y 59.")
    if not 0 <= segundo <= 59:
        raise ValueError("Los segundos tienen que estar entre 0 y 59.")
    return datetime.datetime(anio, mes, dia, hora, minuto, segundo)


def dia_de_la_semana(anio, mes, dia):
    """0 = lunes … 6 = domingo. ValueError si la fecha no existe (no mira el rango del reloj: sirve para mostrar)."""
    try:
        return datetime.date(anio, mes, dia).weekday()
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Esa fecha no existe.") from None


def nombre_dia(anio, mes, dia):
    return DIAS[dia_de_la_semana(anio, mes, dia)]


def fecha_larga(anio, mes, dia):
    """«lunes 19 de octubre de 2026»."""
    return f"{nombre_dia(anio, mes, dia)} {dia} de {MESES[mes - 1]} de {anio}"


def fecha_corta(anio, mes, dia):
    """«lun 19 oct»: la fecha de la píldora de estado del dock."""
    return f"{DIAS_CORTOS[dia_de_la_semana(anio, mes, dia)]} {dia} {MESES_CORTOS[mes - 1]}"


def formato_set_time(anio, mes, dia, hora, minuto, segundo):
    """El texto que espera `timedatectl set-time`: «YYYY-MM-DD HH:MM:SS» (valida antes de formatear)."""
    f = validar_fecha_hora(anio, mes, dia, hora, minuto, segundo)
    return f"{f.year:04d}-{f.month:02d}-{f.day:02d} {f.hour:02d}:{f.minute:02d}:{f.second:02d}"


def ahora():
    """La fecha y hora locales del sistema (datetime). tzset() hace que se note una zona cambiada con la app abierta."""
    time.tzset()
    return datetime.datetime.now()


# ---- zonas horarias
_ZONA = re.compile(r"[A-Za-z0-9][A-Za-z0-9_+-]*(?:/[A-Za-z0-9][A-Za-z0-9_+-]*)*")   # «America/Argentina/Buenos_Aires»


def validar_zona(zona, zonas=None):
    """La zona si tiene forma de zona (y está en `zonas`, si se da), o ValueError. Nunca empieza por «-»: no puede
    pasar por una opción de timedatectl."""
    if not isinstance(zona, str) or len(zona) > 64 or not _ZONA.fullmatch(zona):
        raise ValueError("esa zona horaria no es válida")
    if zonas is not None and zona not in zonas:
        raise ValueError(f"la zona «{zona}» no existe")
    return zona


def _plano(texto):
    """Minúsculas, sin tildes y con espacios en vez de «_»: para buscar «malaga» y encontrar Europe/Malaga."""
    sin_tildes = "".join(c for c in unicodedata.normalize("NFKD", texto) if not unicodedata.combining(c))
    return sin_tildes.lower().replace("_", " ")


def filtrar_zonas(zonas, texto):
    """Las zonas que contienen todas las palabras de `texto` («buenos aires», «america santiago»). Vacío = todas."""
    palabras = _plano(texto).replace("/", " ").split()
    if not palabras:
        return list(zonas)
    return [z for z in zonas if all(p in _plano(z) for p in palabras)]


def _correr_lectura(argv):
    r = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                       errors="replace", timeout=5)
    return r.returncode, r.stdout


def listar_zonas(correr=None, leer=None):
    """Las zonas horarias, ordenadas. `timedatectl list-timezones`; si no responde, zone.tab de tzdata (más UTC).

    `correr(argv) -> (código, salida)` y `leer(ruta) -> texto` se pueden sustituir en las pruebas."""
    try:
        codigo, salida = (correr or _correr_lectura)(["timedatectl", "list-timezones"])
    except (OSError, subprocess.SubprocessError):
        codigo, salida = 1, ""
    zonas = {l.strip() for l in salida.splitlines() if _ZONA.fullmatch(l.strip())} if codigo == 0 else set()
    if not zonas:
        try:
            tabla = (leer or _leer)("/usr/share/zoneinfo/zone.tab")
        except OSError:
            tabla = ""
        for linea in tabla.splitlines():
            campos = linea.split("\t")
            if not linea.startswith("#") and len(campos) >= 3 and _ZONA.fullmatch(campos[2].strip()):
                zonas.add(campos[2].strip())
        if zonas:
            zonas.add("UTC")
    return sorted(zonas)


# ---- estado del reloj
EstadoHora = namedtuple("EstadoHora", "zona ntp puede_ntp sincronizada")   # ntp, puede_ntp, sincronizada: bool o None


def parsear_timedatectl_show(texto):
    """`timedatectl show` (líneas «Clave=valor») como diccionario."""
    datos = {}
    for linea in (texto or "").splitlines():
        clave, igual, valor = linea.partition("=")
        if igual and clave.strip():
            datos[clave.strip()] = valor.strip()
    return datos


def _si_no(valor):
    return {"yes": True, "no": False}.get(valor)


def zona_del_sistema(leer=None):
    """La zona según /etc/timezone o el enlace /etc/localtime; None si no se sabe. Respaldo de timedatectl show."""
    try:
        zona = (leer or _leer)("/etc/timezone").strip()
    except OSError:
        zona = ""
    if _ZONA.fullmatch(zona):
        return zona
    try:
        destino = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    zona = destino.partition("/zoneinfo/")[2]
    return zona if _ZONA.fullmatch(zona) else None


def leer_estado_hora(correr=None):
    """Zona horaria y estado de la hora automática, según `timedatectl show` (sin permisos). Nunca lanza: lo que no se
    pueda leer queda en None. `correr(argv) -> (código, salida)` se puede sustituir en las pruebas."""
    try:
        codigo, salida = (correr or _correr_lectura)(["timedatectl", "show"])
    except (OSError, subprocess.SubprocessError):
        codigo, salida = 1, ""
    datos = parsear_timedatectl_show(salida) if codigo == 0 else {}
    return EstadoHora(datos.get("Timezone") or zona_del_sistema(), _si_no(datos.get("NTP")),
                      _si_no(datos.get("CanNTP")), _si_no(datos.get("NTPSynchronized")))


def resumen_hora_automatica(estado):
    """Lo que dice el interruptor de hora automática: (ayuda, se_puede_cambiar)."""
    if estado.puede_ntp is False:
        return ("Este sistema no tiene el servicio de hora por internet (systemd-timesyncd). Para instalarlo, "
                "en una terminal: sudo apt install systemd-timesyncd", False)
    if estado.ntp:
        if estado.sincronizada:
            return "Activada y sincronizada: la hora se ajusta sola con internet.", True
        return "Activada, pero todavía no se ha sincronizado (hace falta conexión a internet).", True
    if estado.ntp is False:
        return "Desactivada: la fecha y la hora se cambian a mano, más abajo.", True
    return "No se pudo leer si la hora automática está activada.", True


# ---- cambios con permisos
def argv_fijar_fecha_hora(anio, mes, dia, hora, minuto, segundo):
    return ["timedatectl", "set-time", formato_set_time(anio, mes, dia, hora, minuto, segundo)]


def argv_fijar_zona(zona, zonas=None):
    return ["timedatectl", "set-timezone", validar_zona(zona, zonas)]


def argv_hora_automatica(activo):
    if not isinstance(activo, bool):
        raise ValueError("tiene que ser sí o no")
    return ["timedatectl", "set-ntp", "true" if activo else "false"]


def _correr_con_permisos(argv, detach=False):
    return correr_real(argv, detach, timeout=ESPERA_PERMISO_S)


# Lo que dice sudo cuando no deja (no es un fallo de timedatectl): entonces se prueba por polkit.
_SUDO_NO_DEJA = ("a password is required", "a terminal is required", "no tty present", "not in the sudoers",
                 "may not run sudo", "is not allowed to", "must have a tty")


def _sudo_no_dejo(salida):
    """True si la orden no llegó a ejecutarse porque sudo no está o no autoriza (no si timedatectl se negó)."""
    s = (salida or "").lower()
    return s in ("no está instalada", "no se pudo ejecutar (sin permiso)") or any(f in s for f in _SUDO_NO_DEJA)


def ejecutar_con_permisos(argv, correr=None):
    """Lanza `argv` (una lista) con permisos de administrador. Devuelve (ok, salida_de_error).

    `correr(argv, detach) -> (ok, salida)` se puede sustituir: las pruebas no ejecutan nada de verdad."""
    correr = correr or _correr_con_permisos
    ok, salida = correr(["sudo", "-n", *argv], False)
    if ok:
        return True, ""
    if not _sudo_no_dejo(salida):   # sudo sí dejó y timedatectl se negó: otro intento solo repetiría el mismo error
        return False, salida
    ok, salida_polkit = correr(list(argv), False)
    return (True, "") if ok else (False, salida_polkit or salida)


# (trozo del error en minúsculas, explicación, ayuda). ayuda: None = el comando a mano; "" = nada; otro texto = ese.
_EXPLICACIONES_HORA = (
    ("automatic time synchronization is enabled", "la hora automática está activada",
     "Desactívala primero con el interruptor «Ajustar hora automáticamente»."),
    ("ntp not supported", "este sistema no tiene el servicio de hora por internet (systemd-timesyncd)",
     "Para instalarlo, en una terminal: sudo apt install systemd-timesyncd"),
    ("invalid time zone", "esa zona horaria no existe", ""),
    ("unknown time zone", "esa zona horaria no existe", ""),
    ("interactive authentication required",
     "no se pudo pedir el permiso de administrador (no hay ventana de autorización en la sesión)", None),
    ("a password is required", "hace falta la contraseña de administrador y aquí no se puede escribir", None),
    ("access denied", "no se concedió el permiso de administrador", None),
    ("not authorized", "no se concedió el permiso de administrador", None),
    ("authentication", "no se concedió el permiso de administrador (contraseña incorrecta o cancelado)", None),
    ("failed to connect to system bus", "no hay conexión con el servicio de hora del sistema", None),
    ("no respondió a tiempo", "se agotó el tiempo de espera del permiso", None),
    ("no está instalada", "falta el programa «timedatectl» o «sudo»", None),
)


def _explicar_error_hora(salida):
    """(explicación, ayuda) de un error de timedatectl o de sudo, en español. ayuda None = el comando a mano."""
    bajo = (salida or "").lower()
    for trozo, explicacion, ayuda in _EXPLICACIONES_HORA:
        if trozo in bajo:
            return explicacion, ayuda
    primera = next((l.strip() for l in (salida or "").splitlines() if l.strip()), "")
    return (f"el sistema respondió «{primera[:120]}»" if primera else "el sistema no dio ninguna explicación"), None


def _cambiar_con_permisos(argv, que, correr):
    """Ejecuta un cambio y, si falla, devuelve un mensaje que dice qué pasó y cómo hacerlo a mano."""
    ok, salida = ejecutar_con_permisos(argv, correr)
    if ok:
        return True, ""
    explicacion, ayuda = _explicar_error_hora(salida)
    if ayuda is None:
        ayuda = f"Hazlo a mano en una terminal: {shlex.join(['sudo', *argv])}"
    return False, f"No se pudo cambiar {que}: {explicacion}. {ayuda}".strip()


def fijar_fecha_hora(anio, mes, dia, hora, minuto, segundo, correr=None):
    """Valida y fija la fecha y hora del sistema. Devuelve (ok, mensaje_de_error)."""
    try:
        argv = argv_fijar_fecha_hora(anio, mes, dia, hora, minuto, segundo)
    except ValueError as e:
        return False, str(e)
    return _cambiar_con_permisos(argv, "la fecha y la hora", correr)


def fijar_zona_horaria(zona, zonas=None, correr=None):
    """Fija la zona horaria del sistema. `zonas` (lista de listar_zonas) hace que solo valgan zonas que existen."""
    try:
        argv = argv_fijar_zona(zona, zonas)
    except ValueError as e:
        return False, f"No se pudo cambiar la zona horaria: {e}."
    return _cambiar_con_permisos(argv, "la zona horaria", correr)


def fijar_hora_automatica(activo, correr=None):
    """Activa o desactiva la hora por internet (systemd-timesyncd)."""
    try:
        argv = argv_hora_automatica(activo)
    except ValueError as e:
        return False, f"No se pudo cambiar la hora automática: {e}."
    return _cambiar_con_permisos(argv, "la hora automática", correr)


# ------------------------------------------------------- guardado de cambios
# Fila informativa de Sistema (solo lectura). La ISO arranca con casper «persistent»: casper usa una partición de disco
# etiquetada «writable» (o «casper-rw») como capa de escritura. En el sistema en vivo /cow NO es un punto de montaje
# (mountinfo siempre dice «overlay /cow … upperdir=/cow/upper», con o sin guardado), así que no sirve para saberlo.
# Hay guardado si el arranque lo pide (palabra «persistent» en /proc/cmdline y no «nopersistent») Y existe un disco
# con esa etiqueta (/dev/disk/by-label/writable o casper-rw, que crea udev; casper crea la partición sola en el USB).
Persistencia = namedtuple("Persistencia", "modo titular motivo pista")   # modo: "persistente", "instalado" o "vivo"
ETIQUETAS_GUARDADO = ("writable", "casper-rw")
DIR_ETIQUETAS = "/dev/disk/by-label"
TITULAR_GUARDA = "Se guardan tus cambios al apagar"
TITULAR_EN_VIVO = "Modo en vivo: lo que cambies se pierde al apagar"
PISTA_GUARDADO = ("En un USB: grábalo en modo DD/Etcher y deja espacio libre (se crea sola la partición de guardado "
                  "al arrancar). En una máquina virtual: añade un segundo disco virtual vacío y ejecuta en una "
                  "terminal: sudo mkfs.ext4 -L writable /dev/sdb (comprueba antes que sdb es el disco vacío).")
PISTA_OPCION_CON_GUARDADO = ("Para que se guarde, reinicia y elige la primera opción del menú de arranque "
                             "(«Infinity OS»).")


def _leer_proc(ruta):
    with open(ruta, encoding="utf-8", errors="replace") as f:
        return f.read()


def _listar_etiquetas():
    return os.listdir(DIR_ETIQUETAS)


def estado_persistencia(leer=None, etiquetas=None):
    """¿Sobrevive al apagado lo que se cambia? Mira /proc/cmdline (¿pide guardado el arranque?) y las etiquetas de disco
    (¿hay una partición «writable» o «casper-rw» donde guardar?).

    Devuelve Persistencia(modo, titular, motivo, pista). `leer(ruta) -> texto` (solo se le pide /proc/cmdline) y
    `etiquetas() -> nombres` (lo que hay en /dev/disk/by-label) se pueden sustituir en las pruebas; lo que no se pueda
    leer cuenta como vacío."""
    leer = leer or _leer_proc
    etiquetas = etiquetas or _listar_etiquetas
    try:
        palabras = leer("/proc/cmdline").split()
    except (OSError, ValueError):
        palabras = []
    try:
        discos = set(etiquetas())
    except OSError:
        discos = set()
    if palabras and "boot=casper" not in palabras:   # no arrancó desde la ISO en vivo: es un sistema instalado
        return Persistencia("instalado", TITULAR_GUARDA,
                            "Este equipo está instalado en el disco, no arrancó desde la ISO en vivo.", "")
    if "nopersistent" in palabras:
        return Persistencia("vivo", TITULAR_EN_VIVO,
                            "Sesión limpia: arrancaste con la opción sin guardado (nopersistent), no se guarda nada.",
                            PISTA_OPCION_CON_GUARDADO)
    if "persistent" not in palabras:
        motivo = ("Este arranque no pide guardado (falta «persistent» en las opciones de arranque)." if palabras else
                  "No se pudo comprobar cómo arrancó el sistema (/proc/cmdline).")
        return Persistencia("vivo", TITULAR_EN_VIVO, motivo,
                            PISTA_OPCION_CON_GUARDADO if palabras else PISTA_GUARDADO)
    for etiqueta in ETIQUETAS_GUARDADO:
        if etiqueta in discos:
            return Persistencia("persistente", TITULAR_GUARDA,
                                f"El arranque pide guardado y hay una partición «{etiqueta}» donde guardarlo.", "")
    return Persistencia("vivo", TITULAR_EN_VIVO,
                        "El arranque pide guardado, pero no hay dónde guardar: no se encontró ninguna partición "
                        "«writable» (ni «casper-rw»).", PISTA_GUARDADO)
