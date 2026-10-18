"""Isla dinámica: la lógica pura (sin GTK ni pantalla), para poder probarla sin compositor.

La isla es un WIDGET DE SOFTWARE, no hardware: una píldora negra redondeada, centrada arriba, que se
expande, se encoge y se divide en dos burbujas para mostrar «actividades en vivo». Aquí vive lo que se
puede probar sin pantalla:

- la máquina de estados (COLAPSADA / EXPANDIDA / DIVIDIDA) a partir de las actividades activas;
- el auto-colapso: los OSD (volumen, brillo) caducan solos tras `DURACION_OSD` segundos sin cambios;
- el reparto en dos burbujas cuando hay dos actividades a la vez (p. ej. volumen + brillo, o una
  reproducción en curso + un OSD);
- el parseo robusto de `wpctl` y `brightnessctl`, el formato del porcentaje y la elección de icono.

Para poder simular el tiempo en las pruebas, los métodos aceptan `ahora` (segundos monótonos); si no se
pasa, usan time.monotonic(). La isla de verdad (isla.py) le inyecta el valor real y programa un único
temporizador para el próximo auto-colapso (nada de sondeos continuos).
"""
import time

# --------------------------------------------------------------------------- estados visibles
COLAPSADA = "colapsada"   # reposo: una píldora pequeña con la hora (acento superior, discreto)
EXPANDIDA = "expandida"   # una sola actividad: OSD de volumen o de brillo, o la reproducción
DIVIDIDA = "dividida"     # dos actividades a la vez: dos burbujas, una a cada lado

# --------------------------------------------------------------------------- tipos de actividad
RELOJ = "reloj"       # el contenido de reposo (no cuenta como «actividad»)
VOLUMEN = "volumen"
BRILLO = "brillo"
MEDIA = "media"       # reproducción en curso (now-playing); persiste mientras suene

# Prioridad al repartir las dos burbujas de la isla dividida: la reproducción manda sobre los OSD, y
# el volumen sobre el brillo. Con tres a la vez (poco probable) se muestran las dos primeras.
ORDEN = (MEDIA, VOLUMEN, BRILLO)

DURACION_OSD = 1.5  # segundos que un OSD permanece antes de encogerse solo (sin más cambios)

# --------------------------------------------------------------------------- tamaños de la ventana (px)
# Tamaños nominales de la píldora por estado. La ventana de capa se auto-ajusta a la píldora y el CSS
# anima la transición entre estos tamaños (ver isla.py). Son el «esqueleto» que las pruebas comprueban.
ANCHO_COLAPSADA, ALTO_COLAPSADA = 132, 34
ANCHO_EXPANDIDA, ALTO_EXPANDIDA = 330, 56
# La dividida son dos burbujas de ancho medio con un hueco en medio (aire transparente, «se parte»).
ANCHO_BURBUJA = 176
HUECO_BURBUJAS = 16
ANCHO_DIVIDIDA = 2 * ANCHO_BURBUJA + HUECO_BURBUJAS
ALTO_DIVIDIDA = ALTO_EXPANDIDA

TAMANOS = {
    COLAPSADA: (ANCHO_COLAPSADA, ALTO_COLAPSADA),
    EXPANDIDA: (ANCHO_EXPANDIDA, ALTO_EXPANDIDA),
    DIVIDIDA: (ANCHO_DIVIDIDA, ALTO_DIVIDIDA),
}


def tamano(estado):
    """(ancho, alto) nominal de la ventana para un estado."""
    return TAMANOS[estado]


# ====================================================================== parseo robusto
def _acotar_pct(p):
    return max(0, min(100, int(p)))


def porcentaje(fraccion):
    """Fracción 0..1(+) a porcentaje entero 0..100 (se acota a ese rango)."""
    try:
        return _acotar_pct(round(float(fraccion) * 100))
    except (TypeError, ValueError):
        return 0


def parsear_volumen(salida):
    """De la salida de `wpctl get-volume @DEFAULT_AUDIO_SINK@` saca (fraccion, mudo).

    Admite 'Volume: 0.45', 'Volume: 0.45 [MUTED]', espacios de más y el locale con coma ('Volume: 0,45').
    Devuelve None si no hay ningún número legible. La fracción nunca es negativa (0.0 como mínimo)."""
    if not salida:
        return None
    texto = str(salida).strip()
    mudo = "[MUTED]" in texto
    partes = texto.replace(",", ".").split()
    # El número va justo tras la etiqueta 'Volume:'.
    for i, p in enumerate(partes):
        if p.rstrip(":").lower() == "volume" and i + 1 < len(partes):
            try:
                return max(0.0, float(partes[i + 1])), mudo
            except ValueError:
                break
    # Respaldo: el primer flotante que aparezca en la línea.
    for p in partes:
        try:
            return max(0.0, float(p)), mudo
        except ValueError:
            continue
    return None


def parsear_brillo_m(salida):
    """Porcentaje 0..100 de la salida de `brightnessctl -m` (CSV: nombre,clase,actual,porcentaje%,maximo).

    Ej.: 'intel_backlight,backlight,400,40%,1000' -> 40. Si falta el campo del porcentaje, lo calcula con
    actual/maximo. Devuelve None si no se puede leer."""
    if not salida:
        return None
    lineas = [l for l in str(salida).strip().splitlines() if l.strip()]
    if not lineas:
        return None
    campos = lineas[0].split(",")
    if len(campos) >= 4:
        bruto = campos[3].strip().rstrip("%").replace(",", ".")
        try:
            return _acotar_pct(round(float(bruto)))
        except ValueError:
            pass
    if len(campos) >= 5:  # respaldo: actual (campo 2) sobre maximo (campo 4)
        pct = parsear_brillo(campos[2], campos[4])
        if pct is not None:
            return pct
    return None


def parsear_brillo(actual, maximo):
    """Porcentaje 0..100 a partir de `brightnessctl get` y `brightnessctl max` (dos enteros)."""
    try:
        a = float(str(actual).strip().replace(",", "."))
        m = float(str(maximo).strip().replace(",", "."))
    except (TypeError, ValueError, AttributeError):
        return None
    if m <= 0:
        return None
    return _acotar_pct(round(a / m * 100))


def nivel_volumen(fraccion, mudo):
    """Qué icono de volumen toca: 'mudo' | 'bajo' | 'medio' | 'alto' (mismos cortes que la píldora de estado)."""
    try:
        f = float(fraccion)
    except (TypeError, ValueError):
        f = 0.0
    if mudo or f <= 0:
        return "mudo"
    if f < 0.34:
        return "bajo"
    if f < 0.67:
        return "medio"
    return "alto"


# Nombre del icono (símbolo del tema; existe en adwaita y papirus, los dos iconos del sistema).
_ICONO_VOLUMEN = {"mudo": "audio-volume-muted-symbolic", "bajo": "audio-volume-low-symbolic",
                  "medio": "audio-volume-medium-symbolic", "alto": "audio-volume-high-symbolic"}
ICONO_BRILLO = "display-brightness-symbolic"
ICONO_PLAY = "media-playback-start-symbolic"
ICONO_PAUSA = "media-playback-pause-symbolic"


def icono_volumen(fraccion, mudo):
    """El nombre del icono de volumen según el nivel (mudo/bajo/medio/alto)."""
    return _ICONO_VOLUMEN[nivel_volumen(fraccion, mudo)]


def datos_volumen(fraccion, mudo):
    """Diccionario de contenido del OSD de volumen a partir de los valores leídos."""
    f = max(0.0, float(fraccion))
    return {"nivel": nivel_volumen(f, mudo), "icono": icono_volumen(f, mudo), "fraccion": min(1.0, f),
            "pct": porcentaje(f), "mudo": bool(mudo)}


def datos_brillo(pct):
    """Diccionario de contenido del OSD de brillo a partir del porcentaje leído."""
    p = _acotar_pct(pct)
    return {"fraccion": p / 100.0, "pct": p, "icono": ICONO_BRILLO}


def datos_media(titulo, artista="", reproduciendo=True):
    """Diccionario de contenido de la reproducción en curso."""
    return {"titulo": (titulo or "").strip(), "artista": (artista or "").strip(),
            "reproduciendo": bool(reproduciendo)}


# ====================================================================== máquina de estados
class LogicaIsla:
    """El estado de la isla en función de las actividades activas y del tiempo.

    No toca GTK ni temporizadores: solo guarda qué hay activo y hasta cuándo. La vista le pregunta el
    estado, lo que hay que pintar (`paneles`) y cuándo volver a mirar (`proximo_vencimiento`)."""

    def __init__(self, duracion_osd=DURACION_OSD):
        self.duracion = float(duracion_osd)
        self._act = {}  # tipo -> (datos, vence) ; vence es None para lo que no caduca (reproducción)

    # ---------------------------------------------------------------- utilidades de tiempo
    @staticmethod
    def _ahora(ahora):
        return time.monotonic() if ahora is None else float(ahora)

    def _limpiar(self, ahora):
        self._act = {t: (d, v) for t, (d, v) in self._act.items() if v is None or v > ahora}

    # ---------------------------------------------------------------- entradas
    def osd(self, tipo, datos, ahora=None):
        """Enciende (o refresca) un OSD que caduca solo. Devuelve el estado resultante."""
        if tipo not in (VOLUMEN, BRILLO):
            raise ValueError(f"OSD desconocido: {tipo!r}")
        ahora = self._ahora(ahora)
        self._limpiar(ahora)
        self._act[tipo] = (dict(datos), ahora + self.duracion)
        return self.estado(ahora)

    def media(self, datos, ahora=None):
        """Pone (datos) o quita (datos=None) la reproducción en curso, que no caduca."""
        ahora = self._ahora(ahora)
        self._limpiar(ahora)
        if datos is None:
            self._act.pop(MEDIA, None)
        else:
            self._act[MEDIA] = (dict(datos), None)
        return self.estado(ahora)

    def tic(self, ahora=None):
        """Lo llama el temporizador de la vista: caduca lo vencido y devuelve el estado nuevo."""
        ahora = self._ahora(ahora)
        self._limpiar(ahora)
        return self.estado(ahora)

    def limpiar_todo(self):
        """Vuelve a reposo (para cerrar)."""
        self._act = {}

    # ---------------------------------------------------------------- consultas
    def activas(self, ahora=None):
        """Las actividades vivas, en orden de prioridad."""
        ahora = self._ahora(ahora)
        self._limpiar(ahora)
        return [t for t in ORDEN if t in self._act]

    def estado(self, ahora=None):
        n = len(self.activas(ahora))
        if n == 0:
            return COLAPSADA
        if n == 1:
            return EXPANDIDA
        return DIVIDIDA

    def paneles(self, ahora=None):
        """Lo que hay que pintar: una lista de {'tipo', 'datos'}.

        - COLAPSADA: un solo panel de reloj (la vista rellena la hora).
        - EXPANDIDA: la única actividad.
        - DIVIDIDA: las dos primeras actividades (izquierda, derecha)."""
        activas = self.activas(ahora)
        if not activas:
            return [{"tipo": RELOJ, "datos": {}}]
        return [{"tipo": t, "datos": dict(self._act[t][0])} for t in activas[:2]]

    def proximo_vencimiento(self, ahora=None):
        """Segundos hasta el próximo auto-colapso, o None si no hay nada que caduque."""
        ahora = self._ahora(ahora)
        self._limpiar(ahora)
        futuros = [v - ahora for _d, v in self._act.values() if v is not None and v > ahora]
        return min(futuros) if futuros else None
