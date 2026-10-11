#!/usr/bin/env python3
"""Genera la marca de pAnkI, el agente virtual de Infinity OS.

Requiere: python3-pil y librsvg2-bin (rsvg-convert).

    python3 -I branding/make_panki.py

Resultado:
- panki-white.svg  la marca en blanco sobre fondo transparente: la burbuja del escritorio la muestra a
                   26 px dentro de la esfera de cristal, como el ∞ de logo-white.svg
- panki.png        icono de 256 x 256 para el menú y el lanzador: la misma marca sobre un círculo oscuro

La marca es una burbuja de diálogo (el agente con el que se habla) con una chispa de cuatro puntas dentro.
La burbuja está dibujada como línea, con el mismo carácter que el ∞ del logo: trazo grueso y redondeado,
sin relleno; la chispa va rellena porque es pequeña. El viewBox es cuadrado de 2000 u; el trazo es de 190 u,
que a 26 px son 2,5 px (el ∞ de 4000 u de ancho con 371 u de trazo da 2,4 px a 26 px de ancho).
"""
import io
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageOps

AQUI = Path(__file__).resolve().parent
SALIDA_SVG = AQUI / "panki-white.svg"
SALIDA_PNG = AQUI / "panki.png"

# --- la marca, en un viewBox cuadrado de LADO x LADO unidades
LADO = 2000
GROSOR = 190     # trazo de la burbuja: 2,5 px a 26 px, como el ∞
# La burbuja: rectángulo de esquinas muy redondeadas con una cola abajo a la izquierda.
BURBUJA = ("M670 330H1330A420 420 0 0 1 1750 750V910A420 420 0 0 1 1330 1330H930"
           "L560 1680L700 1330H670A420 420 0 0 1 250 910V750A420 420 0 0 1 670 330Z")
# La chispa de dentro: estrella de cuatro puntas de lados cóncavos.
CENTRO_CHISPA = (1000, 840)
RADIO = 330      # del centro a cada punta
CONCAVO = 0.24   # cuánto se hunden los lados: 0 daría una cruz de puntas finísimas, 0,5 un rombo

# --- el icono
TAM = 256
S = 1024  # lienzo de trabajo (4x), para que los bordes salgan suaves al reducir
# Paleta: la gama del fondo de la marca (#15193a) y del cristal del shell.
BORDE = (10, 13, 34)
MEDIO = (21, 25, 58)     # #15193a
LUZ = (74, 82, 178)
HALO = (140, 150, 255)


def chispa():
    """El contorno de la chispa: cuatro curvas cuadráticas que se hunden hacia el centro."""
    cx, cy = CENTRO_CHISPA
    k = round(CONCAVO * RADIO)
    r = RADIO
    return (f"M{cx} {cy - r} Q{cx + k} {cy - k} {cx + r} {cy} Q{cx + k} {cy + k} {cx} {cy + r} "
            f"Q{cx - k} {cy + k} {cx - r} {cy} Q{cx - k} {cy - k} {cx} {cy - r} Z")


def svg(grosor=GROSOR, relleno="none", color="#ffffff", lado=200):
    """La marca como SVG. Con grosor 0 solo queda el relleno de la burbuja (sin línea)."""
    trazo = f' stroke="{color}" stroke-width="{grosor}" stroke-linejoin="round"' if grosor else ""
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {LADO} {LADO}" width="{lado}" height="{lado}">\n'
            f'  <path fill="{relleno}"{trazo} d="{BURBUJA}"/>\n'
            f'  <path fill="{color}" d="{chispa()}"/>\n'
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
