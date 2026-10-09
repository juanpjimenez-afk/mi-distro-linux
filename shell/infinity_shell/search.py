"""Lógica pura de búsqueda (sin GTK, para poder probarla con tests).

- normalizar(): minúsculas y sin tildes ("Cámara" -> "camara")
- puntuar():    coincidencia difusa de una consulta contra un texto
- calcular():   evalúa expresiones matemáticas de forma segura (sin eval)
"""
import ast
import math
import operator
import re
import unicodedata


def normalizar(texto):
    texto = unicodedata.normalize("NFKD", texto or "")
    return "".join(c for c in texto if not unicodedata.combining(c)).casefold().strip()


def puntuar(consulta, texto, difuso=True):
    """Devuelve 0 si no coincide; más alto = mejor coincidencia.

    Ambos argumentos deben venir ya normalizados. `difuso=False` desactiva la coincidencia
    por letras sueltas (útil en textos largos, donde da falsos positivos).
    """
    if not consulta or not texto:
        return 0
    if texto == consulta:
        return 1000
    if texto.startswith(consulta):
        return 800 - min(len(texto) - len(consulta), 100)
    palabras = re.split(r"[\s\-_./]+", texto)
    for i, palabra in enumerate(palabras):
        if palabra.startswith(consulta):
            return 600 - i * 10
    pos = texto.find(consulta)
    if pos >= 0:
        return 400 - min(pos, 100)
    # Subsecuencia: "ffx" encuentra "firefox". Penaliza los saltos.
    if not difuso or len(consulta) < 2 or len(texto) > 40:
        return 0
    i = 0
    saltos = 0
    ultimo = -1
    for c in consulta:
        j = texto.find(c, i)
        if j < 0:
            return 0
        if ultimo >= 0 and j != ultimo + 1:
            saltos += 1
        ultimo = j
        i = j + 1
    return max(50, 250 - saltos * 25 - len(texto))


# ---------------------------------------------------------------- calculadora

_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARIOS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_PERMITIDO = re.compile(r"^[0-9+\-*/().,%^ x×÷]+$")


def _eval(nodo):
    if isinstance(nodo, ast.Expression):
        return _eval(nodo.body)
    if isinstance(nodo, ast.Constant) and isinstance(nodo.value, (int, float)):
        return nodo.value
    if isinstance(nodo, ast.BinOp) and type(nodo.op) in _OPS:
        izq, der = _eval(nodo.left), _eval(nodo.right)
        if isinstance(nodo.op, ast.Pow):
            if abs(der) > 1000:
                raise ValueError("exponente demasiado grande")
            # Cifras aproximadas del resultado: (9^1000)^1000 no debe calcularse
            if abs(izq) > 1 and abs(der) * math.log10(abs(izq)) > 4000:
                raise ValueError("resultado demasiado grande")
        return _OPS[type(nodo.op)](izq, der)
    if isinstance(nodo, ast.UnaryOp) and type(nodo.op) in _UNARIOS:
        return _UNARIOS[type(nodo.op)](_eval(nodo.operand))
    raise ValueError("expresión no permitida")


def calcular(consulta):
    """Devuelve el resultado como texto, o None si no es una operación válida."""
    q = consulta.strip()
    if not q or not _PERMITIDO.match(q) or not re.search(r"[0-9]", q):
        return None
    if not re.search(r"[+\-*/%^x×÷]", q.lstrip("+-")):
        return None  # un número suelto no es una operación
    q = q.replace("×", "*").replace("x", "*").replace("÷", "/").replace("^", "**")
    q = q.replace(",", ".")
    try:
        valor = _eval(ast.parse(q, mode="eval"))
        if isinstance(valor, float):
            if valor != valor or valor in (float("inf"), float("-inf")):
                return None
            if valor == int(valor) and abs(valor) < 1e15:
                return str(int(valor))
            return f"{valor:.10g}"
        return str(valor)  # ValueError si el entero tiene mas de 4300 cifras
    except (ValueError, SyntaxError, ZeroDivisionError, OverflowError, RecursionError):
        return None
