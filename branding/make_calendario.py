#!/usr/bin/env python3
"""Genera los iconos del Calendario de Infinity OS: uno por cada día del mes (dia-01.svg … dia-31.svg).

    python3 -I branding/make_calendario.py            # escribe overlay/usr/share/infinity/calendario/
    python3 -I branding/make_calendario.py --probar   # además deja una hoja de prueba en /tmp

El dibujo es el del icono que eligió el usuario (hoja azul con dos anillas naranjas, doblez, hoja gris detrás y el
número en blanco) rehecho en vectorial: las medidas y los colores salen de la imagen original (512x512). Los dígitos
NO son una tipografía: son trazos redondeados dibujados aquí mismo (0-9), así el SVG se ve igual en cualquier equipo,
sin depender de fuentes instaladas. El shell elige el fichero del día de hoy (shell/infinity_shell/calendario_icono.py)
y lo cambia a medianoche.

Solo usa la biblioteca estándar. Los SVG se commitean: no hace falta ejecutar esto para compilar la ISO.
"""
import os
import subprocess
import sys

RAIZ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESTINO = os.path.join(RAIZ, "overlay", "usr", "share", "infinity", "calendario")

# ------------------------------------------------------------------------------------------------ dígitos
# Cada dígito es un trazo (línea central) en un recuadro de 213 px de alto, con el grosor del icono original
# (30 px) y extremos/uniones redondeados. Las coordenadas son del recuadro EXTERIOR del dígito (origen arriba
# a la izquierda); el trazo se dibuja 15 px por dentro de ese recuadro. Ancho exterior: 130 (todos) y 56 (el 1).
GROSOR = 30
ALTO_DIGITO = 213
ANCHO_DIGITO = 130
ANCHO_UNO = 56
HUECO = 20                 # aire entre los dos dígitos (en el original: 20 px entre el 0 y el 1)
ARRIBA = 149               # y del borde superior de los dígitos en el icono

#   0: estadio (rectángulo con semicírculos arriba y abajo)
#   1: bandera diagonal + palo
#   2..9: curvas y rectas con la misma anchura de caja
DIGITOS = {
    "0": ("M59,15 H71 A44,44 0 0 1 115,59 V154 A44,44 0 0 1 71,198 H59 A44,44 0 0 1 15,154 V59 A44,44 0 0 1 59,15 Z",
          ANCHO_DIGITO),
    "1": ("M15,36 L41.5,15 V198", ANCHO_UNO),
    "2": ("M17,62 C17,32 38,15 65,15 C92,15 113,32 113,60 C113,104 17,130 17,198 H113", ANCHO_DIGITO),
    "3": ("M20,42 C28,25 45,15 65,15 C92,15 108,32 108,54 C108,78 90,92 62,92 "
          "C94,92 115,110 115,143 C115,178 93,198 65,198 C43,198 25,188 15,168", ANCHO_DIGITO),
    "4": ("M92,198 V15 L15,138 H115", ANCHO_DIGITO),
    "5": ("M108,15 H32 L24,92 C36,80 52,76 68,76 C98,76 115,98 115,138 C115,176 93,198 65,198 "
          "C42,198 26,187 17,166", ANCHO_DIGITO),
    "6": ("M104,40 C92,24 78,15 64,15 C35,15 15,42 15,100 V140 C15,177 36,198 65,198 "
          "C94,198 115,177 115,140 C115,103 94,80 65,80 C43,80 25,92 15,112", ANCHO_DIGITO),
    "7": ("M15,15 H115 L52,198", ANCHO_DIGITO),
    "8": ("M65,15 C89,15 103,32 103,53 C103,74 89,92 65,92 C41,92 27,74 27,53 C27,32 41,15 65,15 Z "
          "M65,92 C94,92 115,110 115,145 C115,178 94,198 65,198 C36,198 15,178 15,145 C15,110 36,92 65,92 Z",
          ANCHO_DIGITO),
}
# el 9 es el 6 girado 180° alrededor del centro del recuadro
DIGITOS["9"] = (DIGITOS["6"][0], ANCHO_DIGITO, "rotate(180 65 106.5)")


def _glifo(d, x, y):
    """Un dígito como <path> con trazo (relleno vacío), colocado con su esquina exterior en (x, y)."""
    datos = DIGITOS[d]
    ruta, _ancho = datos[0], datos[1]
    giro = datos[2] if len(datos) > 2 else ""
    transform = f"translate({x:g} {y:g})" + (f" {giro}" if giro else "")
    return f'<path transform="{transform}" d="{ruta}"/>'


def digitos_svg(texto):
    """Los dos dígitos del día, centrados en el icono (x = 256). Devuelve el grupo de trazos (sin atributos)."""
    anchos = [DIGITOS[c][1] for c in texto]
    total = sum(anchos) + HUECO * (len(texto) - 1)
    x = 256 - total / 2
    piezas = []
    for c, a in zip(texto, anchos):
        piezas.append(_glifo(c, x, ARRIBA))
        x += a + HUECO
    return "\n    ".join(piezas)


# ------------------------------------------------------------------------------------------------ icono
PLANTILLA = """<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <title>Calendario — {dia}</title>
  <defs>
    <linearGradient id="pagina" gradientUnits="userSpaceOnUse" x1="38" y1="43" x2="473" y2="259">
      <stop offset="0" stop-color="#00a6f9"/><stop offset="0.22" stop-color="#00a5f8"/>
      <stop offset="0.55" stop-color="#0095f1"/><stop offset="1" stop-color="#0077e5"/>
    </linearGradient>
    <linearGradient id="base" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="512" y2="0">
      <stop offset="0" stop-color="#00a6f9"/><stop offset="1" stop-color="#007ae6"/>
    </linearGradient>
    <radialGradient id="doblez" gradientUnits="userSpaceOnUse" cx="39" cy="259" r="270">
      <stop offset="0" stop-color="#0050d2" stop-opacity="0.52"/>
      <stop offset="0.2" stop-color="#0050d2" stop-opacity="0.34"/>
      <stop offset="0.45" stop-color="#0050d2" stop-opacity="0.15"/>
      <stop offset="1" stop-color="#0050d2" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="gris" gradientUnits="userSpaceOnUse" x1="0" y1="446" x2="0" y2="497">
      <stop offset="0" stop-color="#5b6a6a"/><stop offset="0.45" stop-color="#8d9c9c"/>
      <stop offset="0.75" stop-color="#b0bebe"/><stop offset="1" stop-color="#b1c0c0"/>
    </linearGradient>
    <linearGradient id="gris-sombra" gradientUnits="userSpaceOnUse" x1="38" y1="449" x2="92" y2="503">
      <stop offset="0" stop-color="#2f3d3d" stop-opacity="0.5"/><stop offset="1" stop-color="#2f3d3d" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="anilla" gradientUnits="objectBoundingBox" x1="0" y1="0" x2="1" y2="0">
      <stop offset="0" stop-color="#ffbf00"/><stop offset="0.8" stop-color="#ffbd00"/>
      <stop offset="0.93" stop-color="#ffaa00"/><stop offset="1" stop-color="#ff8f00"/>
    </linearGradient>
    <linearGradient id="sombra-anilla-i" gradientUnits="userSpaceOnUse" x1="196" y1="52" x2="236" y2="92">
      <stop offset="0" stop-color="#0066e6" stop-opacity="0.78"/><stop offset="1" stop-color="#0066e6" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="sombra-anilla-d" gradientUnits="userSpaceOnUse" x1="333" y1="52" x2="373" y2="92">
      <stop offset="0" stop-color="#0066e6" stop-opacity="0.78"/><stop offset="1" stop-color="#0066e6" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="pliegue-numero" gradientUnits="userSpaceOnUse" x1="0" y1="258" x2="0" y2="292">
      <stop offset="0" stop-color="#5a6b6b" stop-opacity="0"/>
      <stop offset="0.03" stop-color="#5a6b6b" stop-opacity="0.13"/>
      <stop offset="1" stop-color="#5a6b6b" stop-opacity="0"/>
    </linearGradient>
    <clipPath id="hoja-gris">
      <path d="M38,440 H473 V475 Q473,497 451,497 H60 Q38,497 38,475 Z"/>
    </clipPath>
    <clipPath id="trapecio">
      <path d="M38,259 L2,425 Q-3,449 21,449 H491 Q515,449 510,425 L473,259 Z"/>
    </clipPath>
    <clipPath id="hoja">
      <path d="M38,259 V61 Q38,43 56,43 H455 Q473,43 473,61 V259 Z"/>
    </clipPath>
  </defs>

  <!-- hoja gris de detrás -->
  <path d="M38,440 H473 V475 Q473,497 451,497 H60 Q38,497 38,475 Z" fill="url(#gris)"/>
  <g clip-path="url(#hoja-gris)"><path d="M38,449 H108 L38,519 Z" fill="url(#gris-sombra)"/></g>

  <!-- parte baja (en perspectiva) con la sombra del doblez -->
  <path d="M38,259 L2,425 Q-3,449 21,449 H491 Q515,449 510,425 L473,259 Z" fill="url(#base)"/>
  <g clip-path="url(#trapecio)">
    <path d="M39,259 H520 V460 H232 Z" fill="url(#doblez)"/>
  </g>

  <!-- hoja de arriba -->
  <path d="M38,259 V61 Q38,43 56,43 H455 Q473,43 473,61 V259 Z" fill="url(#pagina)"/>

  <!-- sombras alargadas de las anillas -->
  <g clip-path="url(#hoja)">
    <path d="M201,40 L272,111 L240,145 L168,76 Z" fill="url(#sombra-anilla-i)"/>
    <path d="M338,40 L409,111 L377,145 L305,76 Z" fill="url(#sombra-anilla-d)"/>
  </g>

  <!-- anillas -->
  <rect x="165" y="14" width="37.5" height="69.5" rx="18.75" fill="url(#anilla)"/>
  <rect x="302" y="14" width="37.5" height="69.5" rx="18.75" fill="url(#anilla)"/>

  <!-- el número del día -->
  <g fill="none" stroke="#ffffff" stroke-width="{grosor}" stroke-linecap="round" stroke-linejoin="round">
    {digitos}
  </g>
  <g fill="none" stroke="url(#pliegue-numero)" stroke-width="{grosor}" stroke-linecap="round" stroke-linejoin="round">
    {digitos}
  </g>
</svg>
"""


def svg_dia(dia):
    """El icono completo para el día `dia` (1-31), con el número a dos cifras («01»… «31»)."""
    if not 1 <= dia <= 31:
        raise ValueError(f"día fuera de 1-31: {dia!r}")
    texto = f"{dia:02d}"
    return PLANTILLA.format(dia=texto, grosor=GROSOR, digitos=digitos_svg(texto))


def escribir(destino=DESTINO):
    os.makedirs(destino, exist_ok=True)
    for dia in range(1, 32):
        with open(os.path.join(destino, f"dia-{dia:02d}.svg"), "w", encoding="utf-8") as f:
            f.write(svg_dia(dia))
    return destino


def hoja_de_prueba(salida="/tmp/calendario-prueba.png"):
    """Una rejilla con los 31 días (necesita rsvg-convert y ImageMagick): solo para mirar el resultado."""
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        pngs = []
        for dia in range(1, 32):
            png = os.path.join(tmp, f"{dia:02d}.png")
            subprocess.run(["rsvg-convert", "-w", "128", "-h", "128", "-o", png,
                            os.path.join(DESTINO, f"dia-{dia:02d}.svg")], check=True)
            pngs.append(png)
        subprocess.run(["montage", *pngs, "-tile", "8x", "-geometry", "+6+6", "-background", "#2b2f3a", salida],
                       check=True)
    return salida


if __name__ == "__main__":
    print("escrito en", escribir())
    if "--probar" in sys.argv:
        print("hoja de prueba:", hoja_de_prueba())
