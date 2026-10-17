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
import json
import math
import os
import pwd
import re
import subprocess
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


# Páginas de la ventana, en orden. Atajos y Sistema no tienen ajustes: se muestran a mano.
PAGINAS = [
    ("escritorio", "Escritorio"),
    ("dock", "Dock y estado"),
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
       "choice", "%H\\n%M", "nuevo",
       opciones=[("%H\\n%M", "24 h, en dos líneas"), ("%I\\n%M", "12 h, en dos líneas"),
                 ("%H:%M", "24 h, en una línea"), ("%I:%M %p", "12 h, en una línea")]),
    _a("bloqueo.ocultar_contrasena_s", "bloqueo", "Ocultar la contraseña tras (segundos sin tocar nada)",
       "En la pantalla de bloqueo, el campo de contraseña se esconde tras este tiempo.",
       "int", 10, "nuevo", minimo=3, maximo=60),

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
# "conversacion" tampoco: pAnkI lee el ajuste al empezar una conversación nueva (ver panki_app.py).
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
    if isinstance(valor, (int, float)):
        return f"{valor:g}"
    return valor if isinstance(valor, str) else None


def _coaccionar(a, valor):
    """Devuelve el valor normalizado o lanza ValueError con un mensaje para el usuario."""
    t = a.tipo
    if t == "bool":
        if isinstance(valor, bool):
            return valor
        raise ValueError("debe ser sí o no")
    if t in ("int", "float"):
        if isinstance(valor, bool) or not isinstance(valor, (int, float)) or not math.isfinite(valor):
            raise ValueError("debe ser un número")
        if t == "int":
            if valor != int(valor):
                raise ValueError("debe ser un número entero")
            valor = int(valor)
        else:
            valor = round(float(valor), 3)
        if not a.minimo <= valor <= a.maximo:
            raise ValueError(f"debe estar entre {a.minimo:g} y {a.maximo:g}")
        return valor
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
        texto = valor.strip() if isinstance(valor, str) else ""
        if not texto or len(texto) > a.maximo or not _TEXTO.fullmatch(texto):
            raise ValueError(f"solo letras, números, espacios y - _ . ' (máximo {a.maximo} caracteres)")
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
    if not errores:  # reglas que relacionan varios ajustes
        bloq, apag, susp = normal["bloqueo.minutos"], normal["bloqueo.apagar_pantalla"], normal["bloqueo.suspender"]
        if apag <= bloq:
            errores.append(("bloqueo.apagar_pantalla", "Apagar la pantalla tiene que ser más tarde que bloquear."))
        if susp and susp <= apag:
            errores.append(("bloqueo.suspender", "Suspender tiene que ser más tarde que apagar la pantalla (o 0 = nunca)."))
    return normal, errores


def cargar(ruta=None):
    """Lee los ajustes del usuario. Un fichero dañado no rompe nada: se usan los valores por defecto.

    Devuelve (valores, avisos). Los avisos explican qué se ignoró.
    """
    ruta = ruta or ruta_ajustes()
    try:
        with open(ruta, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        return dict(DEFECTOS), []
    except (OSError, ValueError) as e:
        return dict(DEFECTOS), [f"No se pudo leer {ruta}: {e}"]
    if not isinstance(datos, dict):
        return dict(DEFECTOS), [f"{ruta} no tiene el formato esperado; se usan los valores por defecto."]
    valores, errores = validar(datos)
    return valores, [m for _, m in errores]


def valor(id, ruta=None):
    """Un solo ajuste, para el shell. No lanza excepciones."""
    v, _ = cargar(ruta)
    return v.get(id, DEFECTOS.get(id))


def importar(ruta):
    """Lee un fichero exportado. Devuelve (valores, errores); el llamador decide si lo aplica.

    Lanza OSError o ValueError si el fichero no se puede leer como JSON de ajustes.
    """
    with open(ruta, encoding="utf-8") as f:
        datos = json.load(f)
    if not isinstance(datos, dict):
        raise ValueError("el fichero no contiene ajustes de Infinity")
    # Un fondo que añadió el usuario en otro equipo no existe aquí: no tiene sentido rechazar todo el fichero
    # por eso, y vuelve al fondo por defecto.
    for id_ in ("escritorio.fondo", "bloqueo.fondo"):
        a = _POR_ID[id_]
        if id_ in datos and isinstance(datos[id_], str) and a.opciones.normalizar(datos[id_]) is None:
            datos = dict(datos)
            datos[id_] = a.defecto
    return validar(datos)


def guardar_json(valores, ruta=None):
    """Escribe ajustes.json de forma atómica. Solo guarda los ajustes conocidos."""
    ruta = ruta or ruta_ajustes()
    _escribir(ruta, json.dumps({k: valores[k] for k in DEFECTOS}, ensure_ascii=False, indent=1, sort_keys=True) + "\n")


def cambiados(antes, despues):
    """Ids de los ajustes que cambian entre dos conjuntos de valores."""
    return [k for k in despues if antes.get(k) != despues[k]]


def acciones_para(ids):
    """Acciones de recarga necesarias para aplicar estos ajustes, en el orden correcto."""
    pedidas = set()
    for i in ids:
        pedidas.update(ACCION_POR_APLICA.get(_POR_ID[i].aplica, ()))
    return [a for a in ORDEN_ACCIONES if a in pedidas]


# ---------------------------------------------------------------- ficheros
def _leer(ruta):
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read()
    except FileNotFoundError:
        return ""


def _escribir(ruta, texto):
    """Escribe en un .tmp y lo reemplaza: un corte a mitad no deja el fichero a medias."""
    os.makedirs(os.path.dirname(ruta), exist_ok=True)
    with open(ruta + ".tmp", "w", encoding="utf-8") as f:
        f.write(texto)
    os.replace(ruta + ".tmp", ruta)


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


def escribir(valores, base=None):
    """Guarda ajustes.json y regenera los ficheros de configuración. Devuelve las rutas escritas."""
    base = base or dir_config()
    textos = {rel: _leer(os.path.join(base, rel)) for rel in ("gtklock/config.ini", "foot/foot.ini")}
    ficheros = generar(valores, textos)
    guardar_json(valores, os.path.join(base, "infinity", "ajustes.json"))
    for rel, texto in ficheros.items():
        _escribir(os.path.join(base, rel), texto)
    return [os.path.join(base, rel) for rel in ficheros]


# ---------------------------------------------------------------- recarga
def correr_real(argv, detach=False):
    """Ejecuta una orden. Devuelve (ok, texto_de_error)."""
    try:
        if detach:
            subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
            return True, ""
        r = subprocess.run(argv, capture_output=True, text=True, timeout=15)
        return r.returncode == 0, (r.stderr or r.stdout).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return False, str(e)


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
    """Abre la terminal con passwd. Devuelve (ok, texto_de_error)."""
    return (correr or correr_real)(argv_cambiar_contrasena(), True)
