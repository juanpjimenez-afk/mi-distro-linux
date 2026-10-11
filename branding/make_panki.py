#!/usr/bin/env python3
"""Genera la marca de pAnkI, el agente virtual de Infinity OS.

Requiere: python3-pil y librsvg2-bin (rsvg-convert).

    python3 -I branding/make_panki.py

Resultado:
- panki-white.svg  la marca en blanco sobre fondo transparente: la burbuja del escritorio la muestra a
                   34 px dentro de la esfera de cristal, como el ∞ de logo-white.svg
- panki.png        icono de 256 x 256 para el menú y el lanzador: la misma marca sobre un círculo oscuro

La marca es una chispa de cuatro puntas (una estrella de lados cóncavos) con un punto pequeño. Está
dibujada como línea, con el mismo carácter que el ∞ del logo: trazo grueso y redondeado, sin relleno. El
∞ tiene un trazo de unas 371 u en su viewBox de 4000 x 2000, que a 34 px de ancho son 3,2 px; la chispa
usa un viewBox cuadrado de 2000 u de alto (la misma altura) y un trazo de 190 u, que a 34 px también son
3,2 px.
"""
import io
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

AQUI = Path(__file__).resolve().parent
SALIDA_SVG = AQUI / "panki-white.svg"
SALIDA_PNG = AQUI / "panki.png"

# --- la chispa, en un viewBox cuadrado de LADO x LADO unidades
LADO = 2000
C = LADO // 2
RADIO = 800      # del centro a cada punta (el trazo añade GROSOR / 2 por fuera)
CONCAVO = 0.24   # cuánto se hunden los lados: 0 daría una cruz de puntas finísimas, 0,5 un rombo
GROSOR = 190     # trazo de la marca del escritorio: 3,2 px a 34 px, como el ∞
PUNTO = (1575, 425, 115)  # el punto pequeño: centro x, centro y y radio

# --- el icono
TAM = 256
S = 1024  # lienzo de trabajo (4x), para que los bordes salgan suaves al reducir
# Paleta: la gama del fondo de la marca (#15193a) y del cristal del shell.
BORDE = (10, 13, 34)
MEDIO = (21, 25, 58)     # #15193a
LUZ = (74, 82, 178)
HALO = (140, 150, 255)


def trazado():
    """El contorno de la chispa: cuatro curvas cuadráticas que se hunden hacia el centro."""
    k = round(CONCAVO * RADIO)
    r = RADIO
    return (f"M{C} {C - r} Q{C + k} {C - k} {C + r} {C} Q{C + k} {C + k} {C} {C + r} "
            f"Q{C - k} {C + k} {C - r} {C} Q{C - k} {C - k} {C} {C - r} Z")


def svg(grosor=GROSOR, relleno="none", color="#ffffff", lado=200):
    """La marca como SVG. Con grosor 0 solo queda el relleno (sin línea)."""
    x, y, r = PUNTO
    trazo = f' stroke="{color}" stroke-width="{grosor}" stroke-linejoin="round"' if grosor else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LADO} {LADO}" width="{lado}" height="{lado}">\n'
            f'  <path fill="{relleno}"{trazo} d="{trazado()}"/>\n'
            f'  <circle fill="{color}" cx="{x}" cy="{y}" r="{r}"/>\n'
            f'</svg>\n')


def rasterizar(texto_svg, lado):
    """SVG a imagen RGBA de lado x lado, con rsvg-convert."""
    res = subprocess.run(["rsvg-convert", "-w", str(lado), "-h", str(lado)],
                         input=texto_svg.encode("utf-8"), capture_output=True, check=True)
    return Image.open(io.BytesIO(res.stdout)).convert("RGBA")


def degradado_radial(centro, radio, interior, medio, exterior):
    """Imagen RGB de S x S: `interior` en `centro`, `medio` a medio radio y `exterior` a `radio` o más."""
    g = Image.radial_gradient("L").resize((2 * radio, 2 * radio), Image.BICUBIC)  # 0 en el centro, 255 en el borde
    lienzo = Image.new("L", (S, S), 255)
    lienzo.paste(g, (round(centro[0] - radio), round(centro[1] - radio)))
    return ImageOps.colorize(lienzo, black=interior, white=exterior, mid=medio)


def capa_alfa(forma, relleno, desenfoque=0):
    """Capa RGBA transparente con `forma` (función que dibuja sobre un ImageDraw) del color `relleno`."""
    mascara = Image.new("L", (S, S), 0)
    forma(ImageDraw.Draw(mascara))
    if desenfoque:
        mascara = mascara.filter(ImageFilter.GaussianBlur(desenfoque))
    capa = Image.new("RGBA", (S, S), relleno + (0,))
    capa.putalpha(mascara)
    return capa


def escalar_alfa(capa, factor):
    capa.putalpha(capa.getchannel("A").point(lambda v: round(v * factor)))
    return capa


def colorear(marca, color):
    """La forma de `marca` (su canal alfa) con un color liso."""
    capa = Image.new("RGBA", marca.size, color + (0,))
    capa.putalpha(marca.getchannel("A"))
    return capa


def icono():
    centro = S // 2
    radio = 470
    caja = [centro - radio, centro - radio, centro + radio, centro + radio]
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))

    # sombra suave: separa el círculo de los fondos claros
    sombra = capa_alfa(lambda d: d.ellipse([caja[0], caja[1] + 14, caja[2], caja[3] + 14], fill=255), (0, 0, 0), 20)
    img.alpha_composite(escalar_alfa(sombra, 0.38))

    # círculo con degradado oscuro-azulado y la luz desplazada hacia arriba a la izquierda
    disco = degradado_radial((centro - 150, centro - 190), 800, LUZ, MEDIO, BORDE).convert("RGBA")
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).ellipse(caja, fill=255)
    disco.putalpha(mascara)
    img.alpha_composite(disco)

    # brillo de cristal arriba a la izquierda y borde de luz (como button.orb del shell)
    brillo = capa_alfa(lambda d: d.ellipse([centro - 330, centro - 420, centro + 90, centro - 120], fill=255),
                       (255, 255, 255), 55)
    img.alpha_composite(escalar_alfa(brillo, 0.24))
    anillo = capa_alfa(lambda d: d.ellipse(caja, outline=255, width=9), (255, 255, 255), 1.5)
    img.alpha_composite(escalar_alfa(anillo, 0.32))

    # la marca: halo, un velo de relleno muy tenue y la línea blanca. Con 1,25 veces el grosor de la burbuja,
    # para que siga viéndose a 24 px.
    lado = 600
    pos = (centro - lado // 2, centro - lado // 2)
    linea = rasterizar(svg(grosor=round(GROSOR * 1.25)), lado)
    velo = rasterizar(svg(grosor=0, relleno="#ffffff"), lado)
    marca = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    marca.paste(linea, pos)
    relleno = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    relleno.paste(velo, pos)
    halo = colorear(marca, HALO).filter(ImageFilter.GaussianBlur(34))
    img.alpha_composite(escalar_alfa(halo, 0.75))
    img.alpha_composite(escalar_alfa(relleno, 0.16))
    img.alpha_composite(marca)

    return img.resize((TAM, TAM), Image.LANCZOS)


def main():
    SALIDA_SVG.write_text(svg(), encoding="utf-8")
    icono().save(SALIDA_PNG, optimize=True)
    print("Marca generada:", SALIDA_SVG, SALIDA_PNG)


if __name__ == "__main__":
    main()
