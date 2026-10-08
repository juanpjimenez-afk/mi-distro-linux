#!/usr/bin/env python3
"""Quita la hora y la fecha que vienen pintadas en la foto de referencia de la pantalla de bloqueo.

La foto (branding/fotos/bloqueo-yosemite.webp) es una maqueta con "12 45" y la fecha ya escritos.
La hora de Infinity OS es en directo, así que se borra ese texto y gtklock dibuja la hora real
en el mismo sitio. Se rellena la zona con OpenCV (inpainting) y se guarda bloqueo-sin-texto.png.

Requiere python3-opencv (en Ubuntu: /usr/bin/python3.12 -c "import cv2"):

    python3 -I branding/limpiar_foto_bloqueo.py
"""
from pathlib import Path

import cv2
import numpy as np

AQUI = Path(__file__).resolve().parent
ORIGEN = AQUI / "fotos" / "bloqueo-yosemite.webp"
DESTINO = AQUI / "fotos" / "bloqueo-sin-texto.png"

# Coordenadas en la foto original (474 x 1024): caja de los dígitos y caja de la fecha
CAJA_HORA = (326, 124, 446, 512)
CAJA_FECHA = (252, 530, 446, 580)


def mascara(img):
    mn = img.min(axis=2).astype(int)
    sat = img.max(axis=2).astype(int) - mn
    m = np.zeros(img.shape[:2], np.uint8)

    x0, y0, x1, y1 = CAJA_HORA            # trazos blancos translúcidos: claros y poco saturados
    m[y0:y1, x0:x1] = ((mn[y0:y1, x0:x1] > 125) & (sat[y0:y1, x0:x1] < 100)).astype(np.uint8) * 255
    m = cv2.dilate(m, np.ones((3, 3), np.uint8), iterations=2)

    f = np.zeros_like(m)                  # letras blancas de la fecha
    x0, y0, x1, y1 = CAJA_FECHA
    f[y0:y1, x0:x1] = (mn[y0:y1, x0:x1] > 185).astype(np.uint8) * 255
    f = cv2.dilate(f, np.ones((3, 3), np.uint8), iterations=1)
    return np.maximum(m, f)


def main():
    img = cv2.imread(str(ORIGEN))
    limpia = cv2.inpaint(img, mascara(img), 7, cv2.INPAINT_TELEA)
    cv2.imwrite(str(DESTINO), limpia)
    print("Foto sin texto:", DESTINO)


if __name__ == "__main__":
    main()
