#!/usr/bin/env python3
"""Genera el icono de Nova (branding/nova.png): un orbe con una estrella de cuatro puntas.

Requiere: python3-pil.

    python3 -I branding/make_nova.py

El orbe es el del botón de Infinity (cristal oscuro con brillo arriba a la izquierda). La estrella, la
"nova", se cruza en el centro con un trazo en forma de lemniscata (∞): la marca de Infinity, de
fondo y muy tenue. Se dibuja a 1024 px y se reduce a 256 px para que los bordes salgan suaves.
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

AQUI = Path(__file__).resolve().parent
SALIDA = AQUI / "nova.png"
TAM = 256
S = 1024  # lienzo de trabajo (4x)
C = S / 2

# Paleta: la misma gama azul-violeta del fondo del escritorio y del cristal del shell.
BORDE = (10, 13, 34)
MEDIO = (36, 42, 120)
LUZ = (104, 96, 255)
CIAN = (120, 225, 255)


def degradado_radial(centro, radio, interior, medio, exterior):
    """Imagen RGB de S x S: `interior` en `centro`, `medio` a medio radio y `exterior` a `radio` o más."""
    g = Image.radial_gradient("L").resize((2 * radio, 2 * radio), Image.BICUBIC)  # 0 en el centro, 255 en el borde
    lienzo = Image.new("L", (S, S), 255)
    lienzo.paste(g, (round(centro[0] - radio), round(centro[1] - radio)))
    return ImageOps.colorize(lienzo, black=interior, white=exterior, mid=medio)


def astroide(cx, cy, r, vueltas=240, estirar=1.0):
    """Estrella de cuatro puntas con los lados cóncavos (x = r cos³t, y = r sin³t)."""
    puntos = []
    for i in range(vueltas):
        t = 2 * math.pi * i / vueltas
        puntos.append((cx + r * estirar * math.cos(t) ** 3, cy + r * math.sin(t) ** 3))
    return puntos


def lemniscata(cx, cy, a, pasos=400):
    """Lemniscata de Bernoulli: el ∞."""
    puntos = []
    for i in range(pasos + 1):
        t = 2 * math.pi * i / pasos
        d = 1 + math.sin(t) ** 2
        puntos.append((cx + a * math.cos(t) / d, cy + a * math.sin(t) * math.cos(t) / d))
    return puntos


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
    a = capa.getchannel("A").point(lambda v: round(v * factor))
    capa.putalpha(a)
    return capa


def generar():
    # --- orbe: degradado radial con la luz desplazada hacia arriba a la izquierda
    radio = 470
    orbe = degradado_radial((C - 150, C - 190), 780, LUZ, MEDIO, BORDE).convert("RGBA")
    mascara = Image.new("L", (S, S), 0)
    ImageDraw.Draw(mascara).ellipse([C - radio, C - radio, C + radio, C + radio], fill=255)
    orbe.putalpha(mascara)
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    img.alpha_composite(orbe)

    # --- brillo de cristal arriba a la izquierda (como button.orb del shell)
    brillo = capa_alfa(lambda d: d.ellipse([C - 330, C - 420, C + 90, C - 120], fill=255), (255, 255, 255), 55)
    img.alpha_composite(escalar_alfa(brillo, 0.30))

    # --- el ∞ de fondo: un trazo tenue que cruza la estrella en el centro
    infinito = capa_alfa(lambda d: d.line(lemniscata(C, C + 6, 330), fill=255, width=22, joint="curve"),
                         (150, 175, 255), 2)
    img.alpha_composite(escalar_alfa(infinito, 0.50))

    # --- la nova: resplandor cian, estrella blanca y un núcleo muy claro
    resplandor = capa_alfa(lambda d: d.polygon(astroide(C, C, 300), fill=255), CIAN, 46)
    img.alpha_composite(escalar_alfa(resplandor, 0.95))
    estrella = capa_alfa(lambda d: d.polygon(astroide(C, C, 285), fill=255), (255, 255, 255), 1.5)
    img.alpha_composite(estrella)
    nucleo = capa_alfa(lambda d: d.ellipse([C - 46, C - 46, C + 46, C + 46], fill=255), (190, 240, 255), 18)
    img.alpha_composite(nucleo)

    # --- una estrella pequeña, para que parezca un destello y no un símbolo
    chica = capa_alfa(lambda d: d.polygon(astroide(C + 262, C - 238, 84), fill=255), (255, 255, 255), 1.2)
    luz_chica = capa_alfa(lambda d: d.polygon(astroide(C + 262, C - 238, 96), fill=255), CIAN, 14)
    img.alpha_composite(escalar_alfa(luz_chica, 0.8))
    img.alpha_composite(chica)

    # --- borde de luz, como el de las ventanas de cristal
    anillo = capa_alfa(lambda d: d.ellipse([C - radio, C - radio, C + radio, C + radio], outline=255, width=8),
                       (255, 255, 255), 1.5)
    img.alpha_composite(escalar_alfa(anillo, 0.28))

    # Todo queda dentro del orbe
    alfa = ImageChops.multiply(img.getchannel("A"), mascara)
    img.putalpha(alfa)
    return img.resize((TAM, TAM), Image.LANCZOS)


def main():
    generar().save(SALIDA, optimize=True)
    print("Icono generado:", SALIDA)


if __name__ == "__main__":
    main()
