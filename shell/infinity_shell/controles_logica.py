"""Controles de ventana (−, □, ×): la parte que no necesita GTK ni pantalla.

Hyprland no dibuja barras de título, así que el shell pone junto a cada ventana normal una pastilla de
cristal con tres botones (controles.py). Aquí vive lo que se puede probar sin pantalla:

- qué ventanas llevan pastilla (visibles, ni minimizadas, ni en pantalla completa real, ni diminutas...);
- dónde va la pastilla: ENCIMA de la esquina superior derecha de la ventana, en el hueco superior que
  reserva hyprland.conf, o DENTRO de la esquina si encima no cabe;
- las órdenes que se mandan a Hyprland para cerrar, minimizar, maximizar y restaurar UNA ventana concreta
  (por su dirección, nunca "la que tenga el foco").

Los objetos `hypr` son los de hypr.py (o un doble): solo se usan `json(orden, defecto)` y `despachar(*args)`.
"""
import collections

# --------------------------------------------------------------------------- medidas (px lógicos)
ALTO = 26  # alto de la pastilla
BOTON = 20  # lado de cada botón redondo
ESPACIO = 3  # aire entre botones
RELLENO = 5  # aire entre los botones de los extremos y el borde redondeado de la pastilla
ANCHO = 3 * BOTON + 2 * ESPACIO + 2 * RELLENO + 2  # tres botones, su aire, el relleno y el borde de 1 px (78)
SEPARACION = 4  # aire entre la pastilla y el borde superior de la ventana (modo "encima")
TOPE = 4  # lo mínimo que debe quedar entre la pastilla y el borde de la pantalla
MARGEN_DENTRO = 8  # distancia a los bordes superior y derecho en el modo "dentro"
# Hueco superior que hyprland.conf debe reservar (general:gaps_out, primer valor) para que la pastilla quepa
# encima de una ventana pegada al borde de arriba. Lo usan hyprland.conf y ajustes.py (hay una prueba).
HUECO_SUPERIOR_MIN = ALTO + SEPARACION + TOPE

MIN_ANCHO = 120  # las ventanas más pequeñas (ayudas, menús de Tk...) no llevan pastilla
MIN_ALTO = 60

ESPACIO_MINIMIZADAS = "special:minimizadas"  # donde "viven" las ventanas minimizadas

# Ventanas que traen sus propios botones (la HeaderBar de pAnkI, del Calendario y de Contraseñas tiene −, □ y ×).
# Se compara sin mayúsculas. El Calendario comparte proceso con pAnkI pero fija su propio app_id.
EXCLUIDAS = ("org.infinity.panki", "org.infinity.calendario", "org.infinity.boveda")

# Eventos de Hyprland tras los que conviene mirar de nuevo (no existe uno para "se redimensionó").
EVENTOS = frozenset({
    "openwindow", "closewindow", "movewindow", "movewindowv2", "workspace", "workspacev2", "focusedmon",
    "focusedmonv2", "activespecial", "activespecialv2", "fullscreen", "changefloatingmode", "monitoradded",
    "monitoraddedv2", "monitorremoved", "monitorremovedv2", "createworkspace", "destroyworkspace",
    "activewindowv2", "configreloaded",
})

Plan = collections.namedtuple(
    "Plan", "direccion clase titulo x y dentro monitor nombre_monitor arriba izquierda maximizada")


# --------------------------------------------------------------------------- lectura de los datos
def modo_pantalla_completa(c):
    """0 = normal, 1 = maximizada (conserva los huecos), 2 = pantalla completa real.

    Hyprland 0.53 da `fullscreen` como entero; versiones antiguas daban un booleano (= real)."""
    f = c.get("fullscreen", 0)
    if isinstance(f, bool):
        return 2 if f else 0
    try:
        return int(f)
    except (TypeError, ValueError):
        return 0


def esta_maximizada(c):
    return modo_pantalla_completa(c) == 1


def es_minimizada(c):
    return ((c.get("workspace") or {}).get("name") or "") == ESPACIO_MINIMIZADAS


def _es_especial(c):
    return ((c.get("workspace") or {}).get("name") or "").startswith("special:")


def rect_ventana(c):
    """(x, y, ancho, alto) de la ventana en coordenadas globales del escritorio."""
    x, y = c.get("at") or (0, 0)
    w, h = c.get("size") or (0, 0)
    return int(x), int(y), int(w), int(h)


def rect_monitor(m):
    """(x, y, ancho, alto) lógicos del monitor (los píxeles reales entre la escala; girado, se intercambian)."""
    escala = float(m.get("scale") or 1.0) or 1.0
    w, h = float(m.get("width") or 0), float(m.get("height") or 0)
    if int(m.get("transform") or 0) % 2:
        w, h = h, w
    return int(m.get("x") or 0), int(m.get("y") or 0), int(round(w / escala)), int(round(h / escala))


def _intersecta(a, b):
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    return ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah


def _entre(valor, minimo, maximo):
    return max(minimo, min(valor, maximo))


# --------------------------------------------------------------------------- qué ventanas, y dónde
def lleva_pastilla(c, monitor, excluidas=EXCLUIDAS):
    """¿Esta ventana lleva controles? `monitor` es el monitor (de j/monitors) donde está."""
    if not c.get("mapped") or c.get("hidden") or _es_especial(c):
        return False  # sin mapear, en una pestaña oculta de un grupo, o minimizada / en otro espacio especial
    if monitor is None or (c.get("workspace") or {}).get("id") != (monitor.get("activeWorkspace") or {}).get("id"):
        return False  # solo el espacio de trabajo visible de cada monitor
    clases = {(c.get("class") or "").casefold(), (c.get("initialClass") or "").casefold()}
    if not (clases - {""}):
        return False  # sin clase: ventanas auxiliares (menús de XWayland...)
    if clases & {e.casefold() for e in excluidas}:
        return False
    if modo_pantalla_completa(c) >= 2:
        return False  # pantalla completa real (un vídeo...): nada encima
    _x, _y, w, h = rect_ventana(c)
    return w >= MIN_ANCHO and h >= MIN_ALTO


def colocar(c, monitor_rect, otras):
    """Esquina superior izquierda (x, y) de la pastilla de la ventana `c` y si va dentro de ella.

    Encima si cabe: alineada con el borde derecho de la ventana, `SEPARACION` por encima, siempre que quede
    a `TOPE` del borde de la pantalla y no pise a ninguna de las `otras` ventanas (rectángulos).
    Si no cabe (ventana pegada arriba, otra ventana justo encima), dentro: esquina superior derecha, a
    `MARGEN_DENTRO`. Siempre dentro de la pantalla."""
    x, y, w, h = rect_ventana(c)
    mx, my, mw, mh = monitor_rect
    izq = _entre(x + w - ANCHO, mx + TOPE, max(mx + TOPE, mx + mw - ANCHO - TOPE))
    arriba = y - ALTO - SEPARACION
    if arriba >= my + TOPE and not any(_intersecta((izq, arriba, ANCHO, ALTO), o) for o in otras):
        return izq, arriba, False
    izq = _entre(x + w - ANCHO - MARGEN_DENTRO, mx + TOPE, max(mx + TOPE, mx + mw - ANCHO - TOPE))
    arriba = _entre(y + MARGEN_DENTRO, my + TOPE, max(my + TOPE, my + mh - ALTO - TOPE))
    return izq, arriba, True


def planificar(clientes, monitores, excluidas=EXCLUIDAS):
    """Plan de todas las pastillas: un `Plan` por ventana que lleva controles, con sus márgenes
    (arriba, izquierda) relativos a la esquina de SU monitor, que es lo que pide layer-shell."""
    por_id = {m.get("id"): m for m in monitores or ()}
    con_pastilla = [(c, por_id.get(c.get("monitor"))) for c in clientes or ()]
    con_pastilla = [(c, m) for c, m in con_pastilla if lleva_pastilla(c, m, excluidas)]
    plan = []
    for c, m in con_pastilla:
        otras = [rect_ventana(o) for o, om in con_pastilla if o is not c and om is m]
        mrect = rect_monitor(m)
        x, y, dentro = colocar(c, mrect, otras)
        plan.append(Plan(c.get("address"), c.get("class") or c.get("initialClass") or "", c.get("title") or "",
                         x, y, dentro, m.get("id"), m.get("name") or "", y - mrect[1], x - mrect[0],
                         esta_maximizada(c)))
    return plan


# --------------------------------------------------------------------------- acciones sobre UNA ventana
def _despachar(hypr, *args):
    """Manda una orden a Hyprland. Si la ventana ya se cerró o Hyprland no responde, no es un error del shell."""
    try:
        return hypr.despachar(*args)
    except Exception as e:  # noqa: BLE001  (el socket puede fallar de mil formas; el shell debe seguir)
        print(f"infinity-shell: no se pudo enviar {args[0]} a Hyprland: {e}")
        return ""


def cerrar(hypr, direccion):
    """Cierre amable (la app puede preguntar si guardar), de ESA ventana aunque otra tenga el foco."""
    return _despachar(hypr, "closewindow", f"address:{direccion}")


def minimizar(hypr, direccion):
    """Manda la ventana al espacio especial `minimizadas` SIN mostrarlo (por eso no `togglespecialworkspace`)."""
    return _despachar(hypr, "movetoworkspacesilent", f"{ESPACIO_MINIMIZADAS},address:{direccion}")


def alternar_maximizada(hypr, direccion):
    """Maximiza o restaura ESA ventana. `fullscreen` actúa sobre la ventana con el foco, así que primero se
    le da el foco; el modo 1 (maximizar) respeta los huecos y deja ver los controles, el dock y el estado."""
    _despachar(hypr, "focuswindow", f"address:{direccion}")
    return _despachar(hypr, "fullscreen", "1")


def _espacio_activo(hypr):
    a = hypr.json("activeworkspace", {}) or {}
    ident, nombre = a.get("id"), a.get("name")
    if isinstance(ident, int) and ident > 0:
        return str(ident)
    if nombre:
        return f"name:{nombre}"
    return "+0"  # relativo a donde estás: el mismo espacio de trabajo


def restaurar(hypr, direccion):
    """Saca una ventana minimizada: la trae al espacio de trabajo activo y le da el foco."""
    _despachar(hypr, "movetoworkspace", f"{_espacio_activo(hypr)},address:{direccion}")
    return _despachar(hypr, "focuswindow", f"address:{direccion}")


def elegir_destino_clic(ventanas, activa):
    """Qué hace un clic en una app del dock: ("enfocar" | "restaurar", ventana) o (None, None) sin ventanas.

    `ventanas` va de la más a la menos reciente. Se salta a la más reciente de las visibles que no esté ya en
    primer plano; si la única visible es la activa y hay minimizadas, se trae la más reciente de estas; si todas
    están minimizadas, se restaura la última."""
    visibles = [v for v in ventanas if not es_minimizada(v)]
    minimizadas = [v for v in ventanas if es_minimizada(v)]
    otras = [v for v in visibles if v.get("address") != activa]
    if otras:
        return "enfocar", otras[0]
    if minimizadas:
        return "restaurar", minimizadas[0]
    if visibles:
        return "enfocar", visibles[0]
    return None, None
