#!/usr/bin/env python3
"""Genera los iconos de la Tienda de Infinity OS: uno por aplicación del catálogo y el de la propia Tienda.

    python3 branding/make_tienda_iconos.py                  # escribe en overlay/usr/share/infinity/
    python3 branding/make_tienda_iconos.py --destino DIR    # lo mismo, pero bajo DIR (para probar sin tocar el repo)
    python3 branding/make_tienda_iconos.py --probar [PNG]   # además deja una hoja de contacto (rsvg-convert + ImageMagick)

(con `python3 -I` también vale, como en make_calendario.py). Escribe, con la estructura que tiene /usr/share/infinity en
la ISO:

    tienda/iconos/<id>.svg   spotify, antigravity, netflix, hbo-max, disney-plus, claude, gemini, notebooklm,
                             classroom, youtube, chrome y brave (los ids son los de tienda/catalogo.json)
    tienda.svg               el icono de la propia Tienda (lanzador y dock)

Todos comparten el estilo de Contraseñas (overlay/usr/share/infinity/boveda.svg): una baldosa de 240x240 con 8 px de
margen en un lienzo de 256x256, de esquinas continuas («squircle», 22 %, como los iconos de iOS y macOS), con degradado
vertical del color de la marca, un borde de luz fino arriba y una sombra suave que cabe en el margen. Encima va un
glifo grande y limpio, pensado para leerse a 24 px.

Son dibujos ORIGINALES, inspirados en el color y en la idea de cada app, no copias de ningún logotipo. Ninguno usa
tipografías (la N y la H son trazos y rectángulos de aquí), ni <filter>, ni imágenes, ni scripts, ni referencias
externas: SVG 1.1 sencillo que renderiza igual con librsvg y GdkPixbuf. Todos los ids (degradados) llevan el prefijo del
icono. La salida es determinista: ejecutarlo dos veces da los mismos bytes.

Solo usa la biblioteca estándar. Los SVG se commitean: no hace falta ejecutar esto para compilar la ISO.
"""
import argparse
import math
import os
import subprocess
import sys
import tempfile

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(RAIZ, "overlay", "usr", "share", "infinity")     # lo que la ISO instala en /usr/share/infinity

# ------------------------------------------------------------------------------------------------ colores
BLANCO = "#ffffff"
NEGRO = "#000000"
VIOLETA = "#b9a8ff"            # el acento de Infinity OS
FONDO_OSCURO = "#12162c"       # y el fondo de su interfaz: sobre él se miran los iconos
FONDO_CLARO = "#eef0f6"

# (arriba, abajo) del degradado vertical de la baldosa de cada icono
COLORES = {
    "spotify":     ("#1db954", "#14833b"),
    "antigravity": ("#5b6cff", "#271a8c"),
    "netflix":     ("#e50914", "#8f0710"),
    "hbo-max":     ("#5822b4", "#2d0f66"),
    "disney-plus": ("#113ccf", "#0a1f6e"),
    "claude":      ("#d97757", "#b0553a"),
    "gemini":      ("#262c58", "#0f1226"),
    "notebooklm":  ("#ffffff", "#dde3f4"),
    "classroom":   ("#0f9d58", "#0b6e3e"),
    "youtube":     ("#ff0000", "#b30000"),
    "chrome":      ("#ffffff", "#e1e6f1"),
    "brave":       ("#fb542b", "#c43a17"),
    "tienda":      (VIOLETA, "#6c4bff"),
}

# ------------------------------------------------------------------------------------------------ geometría
LIENZO = 256
MARGEN = 8                     # aire alrededor de la baldosa (donde cabe la sombra)
LADO = LIENZO - 2 * MARGEN     # 240
RADIO = 54                     # 22 % del lado: la esquina «de iOS»
SUAVIDAD = 0.6                 # cuánto se alarga la curva de la esquina por el borde (0 = esquina redonda normal)
# sombra suave sin <filter>: capas cada vez más anchas y tenues, siempre dentro del lienzo (ensanche, bajada, opacidad)
SOMBRA = ((0, 3, 0.16), (2, 4, 0.08), (4, 4, 0.04))


def n(v):
    """Número compacto: dos decimales como mucho y sin ceros de más (y nunca «-0»)."""
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def pt(x, y=None):
    """«x y» para los datos de un trazo (acepta también una tupla)."""
    if y is None:
        x, y = x
    return f"{n(x)} {n(y)}"


def polar(cx, cy, r, grados):
    """Punto a distancia r de (cx, cy); 0° = a la derecha y los ángulos crecen en sentido horario (la y baja)."""
    a = math.radians(grados)
    return cx + r * math.cos(a), cy + r * math.sin(a)


def arco(cx, cy, r, desde, hasta):
    """Datos de un trazo (sin relleno) con un arco de circunferencia, de `desde` a `hasta` grados en sentido horario."""
    grande = 1 if (hasta - desde) % 360 > 180 else 0
    return f"M{pt(polar(cx, cy, r, desde))}A{n(r)} {n(r)} 0 {grande} 1 {pt(polar(cx, cy, r, hasta))}"


def squircle(x, y, w, h, r, suavidad=SUAVIDAD):
    """Contorno de esquinas continuas (como los iconos de iOS/macOS) para un recuadro de x, y, w, h.

    Cada esquina es un arco de circunferencia de radio r entre dos curvas de Bézier que lo enlazan con el borde recto
    sin el «salto» de curvatura de un rectángulo redondeado normal (el esquema de Figma: la curva ocupa (1 + suavidad)·r
    del borde). Con suavidad = 0 sale el rectángulo redondeado de siempre.
    """
    p = (1 + suavidad) * r
    grados = 90 * (1 - suavidad)                                   # lo que queda de arco circular en la esquina
    cuerda = math.sin(math.radians(grados / 2)) * r * math.sqrt(2)  # su cuerda, medida a lo largo de cada eje
    c = r * math.tan(math.radians((90 - grados) / 4)) * math.cos(math.radians(45 * suavidad))
    d = c * math.tan(math.radians(45 * suavidad))
    b = (p - cuerda - c - d) / 3
    a = 2 * b

    def esquina(origen, u):
        """Una esquina a partir de `origen`, avanzando por el borde hacia `u` y girando a la derecha."""
        v = (-u[1], u[0])

        def mueve(desde, s, t):
            return desde[0] + s * u[0] + t * v[0], desde[1] + s * u[1] + t * v[1]

        e1 = mueve(origen, a + b + c, d)
        medio = mueve(e1, cuerda, cuerda)
        e2 = mueve(medio, d, a + b + c)
        return (f"C{pt(mueve(origen, a, 0))} {pt(mueve(origen, a + b, 0))} {pt(e1)}"
                f"A{n(r)} {n(r)} 0 0 1 {pt(medio)}"
                f"C{pt(mueve(medio, d, c))} {pt(mueve(medio, d, b + c))} {pt(e2)}")

    return (f"M{pt(x + w - p, y)}{esquina((x + w - p, y), (1, 0))}V{n(y + h - p)}"
            f"{esquina((x + w, y + h - p), (0, 1))}H{n(x + p)}"
            f"{esquina((x + p, y + h), (-1, 0))}V{n(y + p)}"
            f"{esquina((x, y + p), (0, -1))}Z")


def estrella4(cx, cy, r, pellizco=0.07, hondo=0.45):
    """Estrella de cuatro puntas de lados cóncavos (✦), con las puntas a distancia r sobre los ejes.

    `hondo` hunde los lados hacia el centro (más = más fina) y `pellizco` los afila junto a cada punta.
    """
    c1, c2, fin = (pellizco, -hondo), (hondo, -pellizco), (1, 0)       # el lado de arriba a la derecha, en unidades de r
    pasos = []
    for _ in range(4):
        pasos.append("C" + " ".join(pt(cx + r * x, cy + r * y) for x, y in (c1, c2, fin)))
        c1, c2, fin = [(-y, x) for x, y in (c1, c2, fin)]               # y los otros tres: el mismo, girado 90°
    return f"M{pt(cx, cy - r)}{''.join(pasos)}Z"


def infinito(cx, cy, a, b):
    """Datos de un trazo con el símbolo ∞ (dos lazos que se cruzan en el centro): a es la mitad del ancho y b el alto de control."""
    k = 0.42
    return (f"M{pt(cx, cy)}C{pt(cx + k * a, cy - b)} {pt(cx + a, cy - b)} {pt(cx + a, cy)}"
            f"C{pt(cx + a, cy + b)} {pt(cx + k * a, cy + b)} {pt(cx, cy)}"
            f"C{pt(cx - k * a, cy - b)} {pt(cx - a, cy - b)} {pt(cx - a, cy)}"
            f"C{pt(cx - a, cy + b)} {pt(cx - k * a, cy + b)} {pt(cx, cy)}Z")


def simetrico(mitad, cx=128):
    """Polígono simétrico respecto a x = cx, a partir de su mitad izquierda dada como (distancia al eje, y) de arriba abajo."""
    puntos = [(cx - dx, y) for dx, y in mitad] + [(cx + dx, y) for dx, y in reversed(mitad)]
    limpios = [p for i, p in enumerate(puntos) if p != puntos[i - 1]]      # los puntos sobre el eje no se repiten
    return "M" + "L".join(pt(p) for p in limpios) + "Z"


# ------------------------------------------------------------------------------------------------ piezas SVG
def parada(offset, color, opacidad=None):
    """Un <stop> de degradado."""
    op = "" if opacidad is None or opacidad == 1 else f' stop-opacity="{n(opacidad)}"'
    return f'<stop offset="{n(offset)}" stop-color="{color}"{op}/>'


def lineal(id_, paradas, x1=0, y1=0, x2=0, y2=1, absoluto=False):
    """<linearGradient>; por defecto vertical sobre el recuadro del objeto, con absoluto=True en coordenadas del lienzo."""
    unidades = ' gradientUnits="userSpaceOnUse"' if absoluto else ""
    cuerpo = "\n".join(f"  {parada(*p)}" for p in paradas)
    return (f'<linearGradient id="{id_}"{unidades} x1="{n(x1)}" y1="{n(y1)}" x2="{n(x2)}" y2="{n(y2)}">\n'
            f'{cuerpo}\n</linearGradient>')


def radial(id_, paradas, cx, cy, r):
    """<radialGradient> en coordenadas del lienzo."""
    cuerpo = "\n".join(f"  {parada(*p)}" for p in paradas)
    return (f'<radialGradient id="{id_}" gradientUnits="userSpaceOnUse" cx="{n(cx)}" cy="{n(cy)}" r="{n(r)}">\n'
            f'{cuerpo}\n</radialGradient>')


def blanco(id_, y0=56, y1=200, c1="#e9ecff"):
    """Degradado vertical blanco → c1 (id «<icono>-glifo»), en coordenadas del lienzo, para los glifos blancos."""
    return lineal(f"{id_}-glifo", [(0, BLANCO), (1, c1)], 0, y0, 0, y1, absoluto=True)


def sangria(texto, espacios):
    return "\n".join((" " * espacios + linea) if linea else linea for linea in texto.split("\n"))


def sombra(elementos, dy=4, opacidad=0.22):
    """La sombra de un glifo hecho a mano: los mismos elementos en negro (con sus propios grosores), bajados dy."""
    return ([f'<g transform="translate(0 {n(dy)})" opacity="{n(opacidad)}" fill="{NEGRO}" stroke="{NEGRO}"'
             f' stroke-linecap="round" stroke-linejoin="round">'] + [f"  {e}" for e in elementos] + ["</g>"])


def capa(contenido, relleno=None, trazo=None, ancho=0, con_sombra=True, dy=4, opacidad=0.22):
    """Un glifo de relleno y/o trazo redondeado, precedido de su sombra (la misma silueta en negro, bajada dy).

    `contenido` es una lista de elementos SVG que NO fijan colores (solo «none» si hace falta): el color lo pone el
    grupo, y así la sombra reutiliza exactamente los mismos elementos.
    """
    def grupo(r, t):
        atributos = f'fill="{r or "none"}"'
        if t:
            atributos += f' stroke="{t}" stroke-width="{n(ancho)}" stroke-linecap="round" stroke-linejoin="round"'
        return [f"<g {atributos}>"] + [f"  {e}" for e in contenido] + ["</g>"]

    salida = []
    if con_sombra:
        salida.append(f'<g transform="translate(0 {n(dy)})" opacity="{n(opacidad)}">')
        salida += [f"  {linea}" for linea in grupo(NEGRO if relleno else None, NEGRO if trazo else None)]
        salida.append("</g>")
    return salida + grupo(relleno, trazo)


def icono(id_, titulo, nota, defs=(), cuerpo=(), *, luz=0.12, claro=False):
    """El SVG completo de un icono: sombra, baldosa con degradado, luz y borde finos, y el glifo (`cuerpo`).

    `defs` son degradados extra del glifo (ids con el prefijo del icono), `luz` la opacidad del brillo blanco de la
    mitad de arriba (0 en las baldosas claras) y `claro` marca una baldosa clara, que necesita el borde de abajo más marcado.
    """
    arriba, abajo = COLORES[id_]
    todos = [lineal(f"{id_}-fondo", [(0, arriba), (1, abajo)])]
    if luz:
        todos.append(lineal(f"{id_}-luz", [(0, BLANCO, luz), (0.55, BLANCO, 0)]))
    todos.append(lineal(f"{id_}-borde", [(0, BLANCO, 0.55), (0.4, BLANCO, 0.05), (0.75, NEGRO, 0),
                                         (1, NEGRO, 0.3 if claro else 0.22)]))
    todos += list(defs)

    forma = squircle(MARGEN, MARGEN, LADO, LADO, RADIO)
    lineas = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LIENZO} {LIENZO}" width="{LIENZO}" height="{LIENZO}">',
        f"  <title>{titulo}</title>",
        f"  <!-- {nota} -->",
        "  <defs>",
    ]
    lineas += [sangria(d, 4) for d in todos]
    lineas.append("  </defs>")
    for ensanche, baja, opacidad in SOMBRA:
        g = MARGEN - ensanche
        lineas.append(f'  <path d="{squircle(g, g + baja, LADO + 2 * ensanche, LADO + 2 * ensanche, RADIO + ensanche)}"'
                      f' fill="{NEGRO}" fill-opacity="{n(opacidad)}"/>')
    lineas.append(f'  <path d="{forma}" fill="url(#{id_}-fondo)"/>')
    if luz:
        lineas.append(f'  <path d="{forma}" fill="url(#{id_}-luz)"/>')
    lineas.append(f'  <path d="{squircle(MARGEN + 1, MARGEN + 1, LADO - 2, LADO - 2, RADIO - 1)}" fill="none"'
                  f' stroke="url(#{id_}-borde)" stroke-width="2"/>')
    lineas += [f"  {linea}" for linea in cuerpo]
    lineas.append("</svg>")
    return "\n".join(lineas) + "\n"


# ------------------------------------------------------------------------------------------------ los iconos
def svg_spotify():
    """Tres arcos de sonido que salen de un punto (ondas de audio), blancos sobre verde."""
    cx, cy = 85, 128
    arcos = "".join(arco(cx, cy, r, -52, 52) for r in (34, 62, 90))
    contenido = [f'<path fill="none" d="{arcos}"/>', f'<circle cx="{n(cx)}" cy="{n(cy)}" r="14" stroke="none"/>']
    return icono("spotify", "Spotify",
                 "Tres arcos de sonido que salen de un punto: ondas de audio, no el logotipo de la marca.",
                 defs=[blanco("spotify", 44, 212, "#dcffe8")],
                 cuerpo=capa(contenido, "url(#spotify-glifo)", "url(#spotify-glifo)", 19))


def svg_antigravity():
    """Una «A» (dos trazos que se juntan arriba) atravesada por un anillo orbital en vez de travesaño."""
    atras = "M-92 0A92 28 0 0 1 92 0"          # media elipse de arriba: pasa por detrás de la A
    delante = "M92 0A92 28 0 0 1-92 0"         # media elipse de abajo: pasa por delante
    giro = 'transform="translate(128 156) rotate(-14)"'
    anillo = f'<g {giro} fill="none" stroke="url(#antigravity-{{}})" stroke-width="9" stroke-linecap="round">'
    cuerpo = [anillo.format("atras") + f'<path d="{atras}"/></g>']
    cuerpo += capa(['<path d="M68 196L128 66L188 196"/>'], None, "url(#antigravity-glifo)", 26)
    cuerpo.append(anillo.format("delante") + f'<path d="{delante}"/></g>')
    # los dos tramos del anillo coinciden en color en los extremos (y = 0): atrás se oscurece, delante se aclara
    return icono("antigravity", "Google Antigravity",
                 "Una «A» abstracta atravesada por un anillo orbital: la gravedad se aparta.",
                 defs=[blanco("antigravity", 60, 200, "#cfd6ff"),
                       lineal("antigravity-atras", [(0, "#8fa5ff"), (1, "#d3ddff")]),
                       lineal("antigravity-delante", [(0, "#d3ddff"), (1, BLANCO)])],
                 cuerpo=cuerpo)


def svg_netflix():
    """Una «N» propia: tres trazos redondeados (palo, diagonal, palo) en blanco."""
    return icono("netflix", "Netflix", "Monograma «N» de trazo redondeado y proporción estrecha; no es el logotipo.",
                 defs=[blanco("netflix", 50, 206, "#ffdede")],
                 cuerpo=capa(['<path d="M92 190V66L164 190V66"/>'], None, "url(#netflix-glifo)", 30))


def svg_hbo_max():
    """Una «H» cursiva hecha con tres barras de esquinas redondeadas."""
    barras = ('<g transform="translate(128 128) skewX(-9) translate(-128 -128)">'
              '<rect x="70" y="60" width="38" height="136" rx="14"/><rect x="148" y="60" width="38" height="136" rx="14"/>'
              '<rect x="96" y="112" width="64" height="32" rx="4"/></g>')
    return icono("hbo-max", "HBO Max", "Monograma «H» cursiva de barras redondeadas, propio.",
                 defs=[blanco("hbo-max", 56, 200, "#d9c8ff")],
                 cuerpo=capa([barras], "url(#hbo-max-glifo)"))


def svg_disney_plus():
    """Un «+» grande y, sobre él, un arco que termina en una estrella (una estela)."""
    fin = polar(126, 146, 84, -34)                       # el arco acaba justo donde está la estrella
    cuerpo = capa([f'<path fill="none" d="{arco(126, 146, 84, -168, -34)}"/>'], None, "url(#disney-plus-glifo)", 16)
    cuerpo += capa(['<path d="M124 118V190M88 154H160"/>'], None, "url(#disney-plus-glifo)", 28)
    cuerpo += capa([f'<path d="{estrella4(fin[0], fin[1], 27, 0.1, 0.46)}"/>'], "url(#disney-plus-glifo)")
    return icono("disney-plus", "Disney+", "Un «+» bajo un arco que acaba en estrella; sin tipografía ni silueta de marca.",
                 defs=[blanco("disney-plus", 50, 200, "#cfdcff")], cuerpo=cuerpo)


def svg_claude():
    """Un asterisco de ocho puntas redondeadas, en crema, sobre terracota."""
    rayos = "".join(f"M128 128L{pt(polar(128, 128, 70 if k % 2 == 0 else 56, k * 45 - 90))}" for k in range(8))
    return icono("claude", "Claude", "Asterisco de ocho puntas redondeadas (un destello genérico), en blanco crema.",
                 defs=[lineal("claude-glifo", [(0, "#fff8ee"), (1, "#f6e2cf")], 0, 56, 0, 200, absoluto=True)],
                 cuerpo=capa([f'<path d="{rayos}"/>'], None, "url(#claude-glifo)", 24))


def svg_gemini():
    """Una estrella de cuatro puntas curvas con degradado azul, violeta y rosa, sobre baldosa oscura."""
    cuerpo = ['<circle cx="128" cy="128" r="104" fill="url(#gemini-brillo)"/>']       # un resplandor muy tenue detrás
    cuerpo += capa([f'<path d="{estrella4(128, 128, 94)}"/>'], "url(#gemini-glifo)", con_sombra=False)
    cuerpo += capa([f'<path d="{estrella4(200, 62, 24)}"/>'], "url(#gemini-glifo)", con_sombra=False)
    return icono("gemini", "Gemini", "Estrella de cuatro puntas curvas con degradado azul, violeta y rosa.",
                 defs=[lineal("gemini-glifo", [(0.1, "#4285f4"), (0.5, "#9b72cb"), (0.9, "#d96570")],
                              34, 34, 222, 222, absoluto=True),
                       radial("gemini-brillo", [(0, "#9b72cb", 0.16), (1, "#9b72cb", 0)], 128, 128, 104)],
                 cuerpo=cuerpo)


def svg_notebooklm():
    """Una libreta de tapa índigo con espiral y líneas de texto, y un destello cálido en la esquina."""
    anillas = "".join(f'<rect x="55" y="{72 + 26 * k}" width="38" height="12" rx="6"/>' for k in range(5))
    tapa = '<rect x="73" y="56" width="108" height="150" rx="14"/>'
    cuerpo = sombra([tapa], dy=5, opacidad=0.2)
    cuerpo += [tapa.replace("/>", ' fill="url(#notebooklm-tapa)"/>'),
               f'<g fill="url(#notebooklm-anilla)" stroke="#6b75a8" stroke-width="1.5">{anillas}</g>',
               '<path d="M105 110H157M105 134H157M105 158H137" fill="none" stroke="#ffffff" stroke-opacity="0.92"'
               ' stroke-width="10" stroke-linecap="round"/>',
               f'<path d="{estrella4(173, 78, 44)}" fill="url(#notebooklm-destello)" stroke="#ffffff" stroke-width="7"'
               ' stroke-linejoin="round"/>']
    return icono("notebooklm", "NotebookLM", "Libreta con espiral y un destello (la IA que estudia tus notas).",
                 claro=True, luz=0,
                 defs=[lineal("notebooklm-tapa", [(0, "#4a5bdc"), (1, "#242d8a")], 0, 56, 0, 206, absoluto=True),
                       lineal("notebooklm-anilla", [(0, "#f7f8ff"), (1, "#b4bce0")], 0, 70, 0, 200, absoluto=True),
                       lineal("notebooklm-destello", [(0, "#ffd54a"), (1, "#ff8a1f")], 147, 34, 197, 122, absoluto=True)],
                 cuerpo=cuerpo)


def svg_classroom():
    """Una pizarra verde con marco blanco, líneas de tiza y repisa y, delante, el busto de una persona."""
    persona = ('<circle cx="82" cy="134" r="21"/>'
               '<path d="M38 208C38 176 58 164 82 164C106 164 126 176 126 208Z"/>')
    marco = '<rect x="84" y="52" width="130" height="98" rx="13"/>'
    cuerpo = sombra([marco, '<path d="M96 162H208" fill="none" stroke-width="9"/>'], dy=4, opacidad=0.2)
    cuerpo += [marco.replace("/>", ' fill="url(#classroom-marco)"/>'),
               '<rect x="94" y="62" width="110" height="78" rx="6" fill="url(#classroom-pizarra)"/>',
               '<path d="M96 162H208" fill="none" stroke="#ffffff" stroke-opacity="0.92" stroke-width="9"'
               ' stroke-linecap="round"/>',
               '<path d="M108 87H188M108 107H188M108 127H152" fill="none" stroke="#ffffff" stroke-opacity="0.92"'
               ' stroke-width="8" stroke-linecap="round"/>']
    cuerpo += sombra([f'<g stroke-width="0">{persona}</g>'], dy=4, opacidad=0.2)
    # el contorno de la persona es del color exacto de la baldosa en ese punto: la «recorta» de la pizarra
    cuerpo += [f'<g fill="url(#classroom-fondo-abs)" stroke="url(#classroom-fondo-abs)" stroke-width="12"'
               f' stroke-linejoin="round">{persona}</g>',
               f'<g fill="url(#classroom-persona)">{persona}</g>']
    arriba, abajo = COLORES["classroom"]
    return icono("classroom", "Google Classroom", "Pizarra verde con marco y repisa, y una persona delante.",
                 defs=[lineal("classroom-marco", [(0, BLANCO), (1, "#e3f3ea")], 0, 52, 0, 150, absoluto=True),
                       lineal("classroom-pizarra", [(0, "#0c7a45"), (1, "#0a5f36")], 0, 62, 0, 140, absoluto=True),
                       lineal("classroom-persona", [(0, BLANCO), (1, "#d9efe3")], 0, 112, 0, 208, absoluto=True),
                       lineal("classroom-fondo-abs", [(0, arriba), (1, abajo)], 0, MARGEN, 0, MARGEN + LADO,
                              absoluto=True)],
                 cuerpo=cuerpo)


def svg_youtube():
    """Una pastilla oscura sobre el rojo con un triángulo de reproducción blanco, redondeado."""
    cuerpo = [f'<rect x="42" y="70" width="172" height="118" rx="40" fill="{NEGRO}" fill-opacity="0.28"/>',
              '<rect x="43" y="71" width="170" height="116" rx="39" fill="none" stroke="#ffffff" stroke-opacity="0.18"'
              ' stroke-width="2"/>']
    cuerpo += capa(['<path d="M114 102V156L158 129Z"/>'], "url(#youtube-glifo)", "url(#youtube-glifo)", 12, opacidad=0.28)
    return icono("youtube", "YouTube", "Pastilla con un triángulo de reproducción blanco.",
                 defs=[blanco("youtube", 90, 170, "#ffe3e3")], cuerpo=cuerpo)


def svg_chrome():
    """Una rueda de cuatro sectores (rojo, amarillo, verde y azul) con un centro azul, sobre baldosa clara."""
    cx = cy = 128
    radio = 84
    sectores = "".join(
        f'<path fill="{color}" d="M{pt(cx, cy)}L{pt(polar(cx, cy, radio, -135 + 90 * k))}'
        f'A{radio} {radio} 0 0 1 {pt(polar(cx, cy, radio, -45 + 90 * k))}Z"/>'
        for k, color in enumerate(("#ea4335", "#fbbc04", "#34a853", "#4285f4")))
    cortes = "".join(f"M{pt(cx, cy)}L{pt(polar(cx, cy, radio - 1, -135 + 90 * k))}" for k in range(4))
    cuerpo = sombra([f'<circle cx="{cx}" cy="{cy}" r="{radio}"/>'], dy=5, opacidad=0.2)
    cuerpo += [sectores,
               f'<path d="{cortes}" fill="none" stroke="#ffffff" stroke-width="5"/>',
               f'<circle cx="{cx}" cy="{cy}" r="40" fill="#ffffff"/>',
               f'<circle cx="{cx}" cy="{cy}" r="30" fill="url(#chrome-centro)"/>']
    return icono("chrome", "Google Chrome", "Rueda de cuatro colores con centro azul: un navegador, sin copiar el logotipo.",
                 claro=True, luz=0,
                 defs=[lineal("chrome-centro", [(0, "#5a9bff"), (1, "#1a62d8")], 0, 98, 0, 158, absoluto=True)],
                 cuerpo=cuerpo)


def svg_brave():
    """Un escudo blanco con una cabeza de león naranja (melena en zigzag, ojos y nariz) que asoma por arriba."""
    dy = -5                                              # todo el dibujo, subido para que quede centrado
    escudo = (f"M64 {58 + dy}H192V{120 + dy}C192 {162 + dy} 166 {190 + dy} 128 {212 + dy}"
              f"C90 {190 + dy} 64 {162 + dy} 64 {120 + dy}Z")
    melena = [(0, 90), (18, 90), (34, 72), (40, 98), (50, 114), (40, 124), (47, 141), (33, 145), (33, 160), (16, 164), (0, 180)]
    cara = [(0, 112), (16, 112), (26, 120), (26, 138), (12, 154), (0, 160)]
    k, y0 = 1.25, 130                                    # la cabeza, ampliada alrededor de (128, 130)

    def aumenta(mitad):
        return simetrico([(dx * k, y0 + (y - y0) * k + dy - 2) for dx, y in mitad])

    ojo_y, nariz_y = y0 + dy - 2 - 14 * k, y0 + dy - 2 + 15 * k
    rasgos = (f'<path fill="none" d="M{pt(128 - 33 * k, ojo_y)}L{pt(128 - 12 * k, ojo_y + 8 * k)}'
              f'M{pt(128 + 33 * k, ojo_y)}L{pt(128 + 12 * k, ojo_y + 8 * k)}"/>'
              f'<path d="M{pt(128 - 12 * k, nariz_y)}H{n(128 + 12 * k)}L{pt(128, nariz_y + 13 * k)}Z"/>')
    cuerpo = capa([f'<path d="{escudo}"/>'], "url(#brave-glifo)", "url(#brave-glifo)", 14)
    cuerpo += [f'<path d="{aumenta(melena)}" fill="#e0451d" stroke="#e0451d" stroke-width="8" stroke-linejoin="round"/>',
               f'<path d="{aumenta(cara)}" fill="#f4764a" stroke="#f4764a" stroke-width="6" stroke-linejoin="round"/>',
               f'<g fill="#ffffff" stroke="#ffffff" stroke-width="8" stroke-linecap="round"'
               f' stroke-linejoin="round">{rasgos}</g>']
    return icono("brave", "Brave", "Escudo con una cabeza de león simplificada, de melena en zigzag.",
                 defs=[blanco("brave", 50, 207, "#ffe3d6")], cuerpo=cuerpo)


def svg_tienda():
    """Una bolsa de compras blanca con un infinito (∞) violeta en el frente, sobre el violeta de Infinity."""
    bolsa = "M64 94H192L185 204Q184 216 171 216H85Q72 216 71 204Z"
    asa = "M99 98V74A29 29 0 0 1 157 74V98"
    cuerpo = sombra([f'<path d="{bolsa}"/>', f'<path fill="none" stroke-width="14" d="{asa}"/>'])
    cuerpo += [f'<path d="{asa}" fill="none" stroke="#ffffff" stroke-width="14" stroke-linecap="round"/>',
               f'<path d="{bolsa}" fill="url(#tienda-bolsa)" stroke="url(#tienda-bolsa)" stroke-width="10"'
               ' stroke-linejoin="round"/>',
               f'<path d="{infinito(128, 158, 38, 29)}" fill="none" stroke="url(#tienda-inf)" stroke-width="13"'
               ' stroke-linejoin="round"/>']
    return icono("tienda", "Tienda", "Una bolsa de compras con un infinito en el frente: la tienda de Infinity OS.",
                 defs=[lineal("tienda-bolsa", [(0, BLANCO), (1, "#e6defe")], 0, 90, 0, 216, absoluto=True),
                       lineal("tienda-inf", [(0, "#8f73ff"), (1, "#6c4bff")], 0, 126, 0, 190, absoluto=True)],
                 cuerpo=cuerpo)


# los de las apps del catálogo (tienda/iconos/<id>.svg), en el orden del catálogo; el de la Tienda va aparte (tienda.svg)
ICONOS = {
    "spotify": svg_spotify, "antigravity": svg_antigravity, "netflix": svg_netflix, "hbo-max": svg_hbo_max,
    "disney-plus": svg_disney_plus, "claude": svg_claude, "gemini": svg_gemini, "notebooklm": svg_notebooklm,
    "classroom": svg_classroom, "youtube": svg_youtube, "chrome": svg_chrome, "brave": svg_brave,
}


# ------------------------------------------------------------------------------------------------ salida
def rutas(base=BASE):
    """{id: ruta del SVG} de los 13 iconos, bajo `base` (la raíz que corresponde a /usr/share/infinity)."""
    carpeta = os.path.join(base, "tienda", "iconos")
    salida = {id_: os.path.join(carpeta, f"{id_}.svg") for id_ in ICONOS}
    salida["tienda"] = os.path.join(base, "tienda.svg")
    return salida


def escribir(base=BASE):
    """Escribe los 13 SVG bajo `base` y devuelve {id: ruta}. Idempotente: nada depende de la hora ni del azar."""
    salida = rutas(base)
    for id_, ruta in salida.items():
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        svg = svg_tienda() if id_ == "tienda" else ICONOS[id_]()
        with open(ruta, "w", encoding="utf-8", newline="\n") as f:
            f.write(svg)
    return salida


def hoja_de_prueba(salida="/tmp/tienda-iconos.png", base=BASE):
    """Hoja de contacto (necesita rsvg-convert y ImageMagick): los 13 iconos a 128 y a 48 px, sobre oscuro y sobre claro."""
    with tempfile.TemporaryDirectory() as tmp:
        franjas = []
        for lado, fondo, columnas in ((128, FONDO_OSCURO, 7), (128, FONDO_CLARO, 7),
                                      (48, FONDO_OSCURO, 13), (48, FONDO_CLARO, 13)):
            pngs = []
            for id_, ruta in rutas(base).items():
                png = os.path.join(tmp, f"{id_}-{lado}.png")
                subprocess.run(["rsvg-convert", "-w", str(lado), "-h", str(lado), "-o", png, ruta], check=True)
                pngs.append(png)
            franja = os.path.join(tmp, f"franja-{len(franjas)}.png")
            subprocess.run(["montage", *pngs, "-tile", f"{columnas}x", "-geometry", "+8+8", "-background", fondo, franja],
                           check=True)
            franjas.append(franja)
        subprocess.run(["convert", *franjas, "-background", FONDO_OSCURO, "-gravity", "center", "-append", salida],
                       check=True)
    return salida


def main(argv=None):
    ap = argparse.ArgumentParser(description="Genera los iconos SVG de la Tienda de Infinity OS.")
    ap.add_argument("--destino", default=BASE, metavar="DIR",
                    help="raíz donde escribir tienda/iconos/*.svg y tienda.svg (por defecto overlay/usr/share/infinity)")
    ap.add_argument("--probar", nargs="?", const="/tmp/tienda-iconos.png", metavar="PNG",
                    help="además, deja una hoja de contacto en ese PNG (por defecto /tmp/tienda-iconos.png)")
    args = ap.parse_args(argv)
    escritos = escribir(args.destino)
    print(f"escritos {len(escritos)} iconos bajo", os.path.abspath(args.destino))
    if args.probar:
        print("hoja de prueba:", hoja_de_prueba(args.probar, args.destino))
    return 0


if __name__ == "__main__":
    sys.exit(main())
