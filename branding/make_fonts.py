#!/usr/bin/env python3
"""Crea la fuente "Infinity Reloj" para la hora de la pantalla de bloqueo.

Parte de Inter Regular (licencia SIL OFL 1.1) y la adapta a la referencia de la pantalla de
bloqueo: los dígitos se comprimen en horizontal (quedan altos y estrechos, como en la imagen)
y el interlineado se reduce, para que la hora en dos líneas quede junta.

Requiere python3-fonttools y Inter instalado (fonts-inter):

    python3 -I branding/make_fonts.py

El resultado va en overlay/usr/share/fonts/opentype/infinity/ y se usa solo para la hora.
La fecha sigue usando Inter normal.
"""
from pathlib import Path

from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.transformPen import TransformPen
from fontTools.ttLib import TTFont

AQUI = Path(__file__).resolve().parent
ORIGEN = Path("/usr/share/fonts/opentype/inter/Inter-Regular.otf")
DESTINO = AQUI.parent / "overlay/usr/share/fonts/opentype/infinity/InfinityReloj-Regular.otf"

SX = 0.40      # compresión horizontal de todos los glifos
ASCENDENTE = 0.745   # em, desde la línea base hacia arriba (incluye la altura de los dígitos)
DESCENDENTE = 0.065  # em, por debajo de la línea base -> interlineado total = 0.81 em
FAMILIA = "Infinity Reloj"


def main():
    f = TTFont(str(ORIGEN))
    upm = f["head"].unitsPerEm
    gs = f.getGlyphSet()
    cff = f["CFF "].cff
    top = cff.topDictIndex[0]
    cs = top.CharStrings

    # 1) Glifos comprimidos (contornos y anchura de avance)
    for nombre in gs.keys():
        pen = T2CharStringPen(round(f["hmtx"][nombre][0] * SX), gs)
        gs[nombre].draw(TransformPen(pen, (SX, 0, 0, 1, 0, 0)))
        cs[nombre] = pen.getCharString(private=top.Private, globalSubrs=cff.GlobalSubrs)
        adv, lsb = f["hmtx"][nombre]
        f["hmtx"][nombre] = (round(adv * SX), round(lsb * SX))

    # 2) Interlineado: mismas métricas en todas las tablas que lee cada sistema
    asc, desc = round(ASCENDENTE * upm), round(DESCENDENTE * upm)
    f["hhea"].ascent, f["hhea"].descent, f["hhea"].lineGap = asc, -desc, 0
    os2 = f["OS/2"]
    os2.sTypoAscender, os2.sTypoDescender, os2.sTypoLineGap = asc, -desc, 0
    os2.usWinAscent, os2.usWinDescent = asc, desc
    os2.fsSelection |= 1 << 7  # USE_TYPO_METRICS

    # 3) Nombre propio (no es Inter: una versión modificada no debe llamarse igual)
    tabla = f["name"]
    tabla.names = [n for n in tabla.names if n.nameID not in (16, 17, 21, 22)]
    for nid, valor in {1: FAMILIA, 2: "Regular", 3: "InfinityReloj-Regular;1.0",
                       4: f"{FAMILIA} Regular", 6: "InfinityReloj-Regular"}.items():
        tabla.setName(valor, nid, 3, 1, 0x409)
        tabla.setName(valor, nid, 1, 0, 0)

    DESTINO.parent.mkdir(parents=True, exist_ok=True)
    f.save(str(DESTINO))
    print("Fuente creada:", DESTINO)


if __name__ == "__main__":
    main()
