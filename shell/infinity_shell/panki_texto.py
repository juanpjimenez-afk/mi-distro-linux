"""Markdown de Gemini a marcado Pango seguro (para Gtk.Label con use_markup).

Este módulo NO usa GTK ni nada fuera de la biblioteca estándar. La ventana de pAnkI (panki_app.py) pasa
cada respuesta por `a_pango`; las pruebas (shell/tests/test_panki_texto.py) comprueban el resultado con
Pango real cuando hay GI.

Un texto de más de MAX_FORMATEADO caracteres sale como texto plano escapado, sin formato (y uno de más de
200 000 se recorta con un aviso): la conversión corre en el hilo principal de la ventana y no puede tardar.

Garantía de seguridad: el texto se ESCAPA entero antes de interpretar nada (& < > " '), así que ningún
carácter del modelo puede abrir una etiqueta. Después solo se insertan etiquetas propias y siempre en
parejas: cada marcador (**, *, _) solo genera etiqueta cuando encuentra su cierre; si queda abierto,
se deja como texto. Como último recurso, si el resultado no estuviera bien formado, se devuelve el
texto escapado sin formato. `a_pango` no lanza nunca una excepción.

Qué entiende: **negrita**, *cursiva*, _cursiva_, `código`, bloques ``` (sin interpretar su contenido),
listas con - * + (viñeta •), listas numeradas, encabezados # (negrita), citas > , [texto](url) (se
muestra "texto (url)", sin enlace pulsable) y líneas ---. No entiende tablas ni HTML: salen como texto.
"""
import re

# Etiquetas que este módulo genera. Cualquier otra cosa en la salida es un error.
_ETIQUETA = re.compile(r"</?(?:b|i|tt)>")
_ENTIDAD_MALA = re.compile(r"&(?!(?:amp|lt|gt|quot|apos);)")

# Caracteres que XML (y por tanto Pango) no admite, o que solo estorban: se eliminan.
_CONTROL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f￾￿]")

_VALLA_ABRE = re.compile(r"^ {0,3}(`{3,})[^`]*$")
_VALLA_CIERRA = re.compile(r"^ {0,3}(`{3,})[ \t]*$")
_TITULO = re.compile(r"^ {0,3}#{1,6}[ \t]+(.*?)[ \t]*$")
_TITULO_CIERRE = re.compile(r"[ \t]+#+$")
_REGLA = re.compile(r"^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$")
_CITA = re.compile(r"^ {0,3}&gt;[ \t]?(.*)$")
_VINETA = re.compile(r"^([ \t]*)[-*+][ \t]+(.*)$")
_NUMERADA = re.compile(r"^([ \t]*)(\d{1,9})([.)])[ \t]+(.*)$")

_URL = re.compile(r"https?://[^\s*`]+")
# [texto](url) y [texto](url "título"); el texto ya está escapado (las comillas son &quot;).
_ENLACE = re.compile(
    r"\[([^\[\]]+)\]\(((?:[^()\s]|\([^()\s]*\))+)(?:[ \t]+&quot;.*?&quot;)?\)")
# Trozo de texto corriente: nada que pueda ser un marcador.
_CORRIENTE = re.compile(r"[^\\`*_\[!h]+")

# Tope de marcadores sin cerrar a la vez: cada marcador nuevo mira la pila entera, y con «*a *a *a…» sin cierre
# el coste crecía con el cuadrado del texto. Pasado el tope, el marcador queda como texto.
_MAX_ABIERTOS = 64
# Más de este texto no se formatea (la ventana convierte en el hilo principal): sale como texto plano escapado.
# Y más de _MAX_MOSTRADO se recorta, con aviso al final.
MAX_FORMATEADO = 20_000
_MAX_MOSTRADO = 200_000

_ESCAPABLES = "\\`*_[]#"
_ETIQUETA_DE = {"**": "b", "*": "i", "_": "i"}
_LINEA = "─" * 16


def _escapar(s):
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;").replace("'", "&apos;"))


class _Abre:
    """Un marcador de énfasis que aún no sabe si tendrá cierre."""
    __slots__ = ("marcador",)

    def __init__(self, marcador):
        self.marcador = marcador


def _literal(x):
    return x.marcador if isinstance(x, _Abre) else x


def _cerrar(salida, pila, marcador):
    """Cierra el marcador abierto más reciente. Los abiertos por encima pasan a ser texto.

    Devuelve False si no hay ninguno abierto o si no hay nada entre la apertura y el cierre.
    """
    for k in range(len(pila) - 1, -1, -1):
        if salida[pila[k]].marcador == marcador:
            break
    else:
        return False
    idx = pila[k]
    interior = "".join(_literal(x) for x in salida[idx + 1:])
    if not interior.strip():
        return False
    etiqueta = _ETIQUETA_DE[marcador]
    salida[idx:] = [f"<{etiqueta}>{interior}</{etiqueta}>"]
    del pila[k:]
    return True


def _inline(s):
    """Énfasis, código, enlaces y URL de UNA línea ya escapada. El resultado está siempre equilibrado."""
    salida = []   # str, o _Abre mientras no se sabe si el marcador tendrá cierre
    pila = []     # posiciones en `salida` de los _Abre vivos, de más antiguo a más reciente
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        m = _CORRIENTE.match(s, i)
        if m:
            salida.append(m.group())
            i = m.end()
        elif c == "\\":
            if i + 1 < n and s[i + 1] in _ESCAPABLES:
                salida.append(s[i + 1])
                i += 2
            else:
                salida.append(c)
                i += 1
        elif c == "`":
            j = i
            while j < n and s[j] == "`":
                j += 1
            largo = j - i
            cierre = re.compile(r"(?<!`)`{%d}(?!`)" % largo).search(s, j)
            if cierre:
                codigo = s[j:cierre.start()]
                if len(codigo) > 2 and codigo[0] == " " and codigo[-1] == " " and codigo.strip():
                    codigo = codigo[1:-1]
                salida.append(f"<tt>{codigo}</tt>" if codigo else "")
                i = cierre.end()
            else:
                salida.append(s[i:j])
                i = j
        elif c in "[!":
            # [texto](url) y ![alt](url); un "[" o un "!" que no abren un enlace son texto
            m = _ENLACE.match(s, i + 1 if c == "!" else i) if (c == "[" or s.startswith("![", i)) else None
            if m:
                texto, url = m.group(1), m.group(2)
                visible = _inline(texto)
                salida.append(url if texto == url else f"{visible} ({url})")
                i = m.end()
            else:
                salida.append(c)
                i += 1
        elif c == "h":
            m = _URL.match(s, i) if (i == 0 or not s[i - 1].isalnum()) else None
            if m:
                salida.append(m.group())
                i = m.end()
            else:
                salida.append(c)
                i += 1
        else:  # * o _
            j = i
            while j < n and s[j] == c:
                j += 1
            largo = j - i
            previo = s[i - 1] if i > 0 else " "
            siguiente = s[j] if j < n else " "
            puede_abrir = not siguiente.isspace()
            puede_cerrar = not previo.isspace()
            if c == "_":  # el guion bajo no cierra ni abre dentro de una palabra (snake_case)
                puede_abrir = puede_abrir and not previo.isalnum()
                puede_cerrar = puede_cerrar and not siguiente.isalnum()
            marcadores = {"*": {1: ["*"], 2: ["**"], 3: ["**", "*"]}, "_": {1: ["_"]}}[c].get(largo)
            abiertos = {salida[p].marcador: p for p in pila}  # posición del más reciente de cada tipo
            if marcadores and puede_cerrar and all(mk in abiertos for mk in marcadores):
                # con *** se cierra primero el marcador abierto más recientemente
                for mk in sorted(marcadores, key=lambda mk: -abiertos[mk]):
                    if not _cerrar(salida, pila, mk):
                        salida.append(mk)  # nada entre la apertura y el cierre: queda como texto
            elif marcadores and puede_abrir and len(pila) + len(marcadores) <= _MAX_ABIERTOS:
                for mk in marcadores:
                    salida.append(_Abre(mk))
                    pila.append(len(salida) - 1)
            else:
                salida.append(s[i:j])
            i = j
    return "".join(_literal(x) for x in salida)


def _sangria(espacios):
    """Sangría de una lista: espacios (la tabulación cuenta 4), con tope."""
    return " " * min(len(espacios.replace("\t", "    ")), 8)


def _convertir(texto):
    lineas = _escapar(texto).split("\n")
    salida = []
    i, n = 0, len(lineas)
    while i < n:
        linea = lineas[i].rstrip()
        valla = _VALLA_ABRE.match(linea)
        if valla:
            # Un bloque solo existe si tiene cierre; si no, la línea de la valla se queda como texto.
            fin = None
            for j in range(i + 1, n):
                cierre = _VALLA_CIERRA.match(lineas[j])
                if cierre and len(cierre.group(1)) >= len(valla.group(1)):
                    fin = j
                    break
            if fin is not None:
                cuerpo = "\n".join(lineas[i + 1:fin])
                if cuerpo.strip():
                    salida.append(f"<tt>{cuerpo}</tt>")
                i = fin + 1
                continue
        m = _TITULO.match(linea)
        if m and m.group(1):
            contenido = _TITULO_CIERRE.sub("", m.group(1)).strip()
            salida.append(f"<b>{_inline(contenido)}</b>" if contenido else "")
        elif _REGLA.match(linea):
            salida.append(_LINEA)
        elif _CITA.match(linea):
            salida.append("▎ " + _inline(_CITA.match(linea).group(1)))
        elif _VINETA.match(linea):
            m = _VINETA.match(linea)
            salida.append(f"{_sangria(m.group(1))}• {_inline(m.group(2))}")
        elif _NUMERADA.match(linea):
            m = _NUMERADA.match(linea)
            salida.append(f"{_sangria(m.group(1))}{m.group(2)}{m.group(3)} {_inline(m.group(4))}")
        else:
            salida.append(_inline(linea))
        i += 1
    resultado = "\n".join(salida)
    return re.sub(r"\n{3,}", "\n\n", resultado).strip("\n")


def _bien_formado(marcado):
    """Comprobación barata de último recurso: solo etiquetas propias, bien anidadas, sin & ni < sueltos."""
    if _ENTIDAD_MALA.search(marcado):
        return False
    pila = []
    pos = 0
    for m in _ETIQUETA.finditer(marcado):
        trozo = marcado[pos:m.start()]
        if "<" in trozo or ">" in trozo:
            return False
        pos = m.end()
        nombre = m.group()
        if nombre[1] != "/":
            pila.append(nombre[1:-1])
        elif not pila or pila.pop() != nombre[2:-1]:
            return False
    resto = marcado[pos:]
    return not pila and "<" not in resto and ">" not in resto


def _texto_plano(texto):
    return _escapar(_CONTROL.sub("", texto))


def a_pango(texto: str) -> str:
    """Convierte el markdown de Gemini en marcado Pango seguro. No lanza nunca una excepción."""
    try:
        if texto is None:
            return ""
        if isinstance(texto, (bytes, bytearray)):
            texto = bytes(texto).decode("utf-8", "replace")
        elif not isinstance(texto, str):
            texto = str(texto)
        # Sin sustitutos sueltos (no se pueden codificar), sin controles y con saltos de línea unificados.
        texto = texto.encode("utf-8", "replace").decode("utf-8")
        texto = texto.replace("\r\n", "\n").replace("\r", "\n")
        texto = _CONTROL.sub("", texto)
        if len(texto) > MAX_FORMATEADO:
            if len(texto) > _MAX_MOSTRADO:
                texto = texto[:_MAX_MOSTRADO] + f"\n[…texto recortado: {len(texto) - _MAX_MOSTRADO} caracteres más]"
            return _texto_plano(texto)
        marcado = _convertir(texto)
        return marcado if _bien_formado(marcado) else _texto_plano(texto)
    except Exception:  # noqa: BLE001 - la ventana no puede romperse por un texto raro
        try:
            return _texto_plano(str(texto))
        except Exception:  # noqa: BLE001
            return ""
