#!/usr/bin/env python3
"""Genera los activos gráficos de Infinity OS (fondo de pantalla, imagen de GRUB, iconos).

Requiere: python3-pil y librsvg2-bin (rsvg-convert).
Los resultados ya están en el repo; solo ejecuta esto si quieres cambiar colores o tamaños:

    python3 branding/make_assets.py
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

AQUI = Path(__file__).resolve().parent

# Paleta: base casi negra azulada + manchas de color que el "cristal" desenfocará.
BASE = (5, 7, 18)
MANCHAS = [
    # (x, y, radio, color)  — coordenadas relativas (0..1)
    (0.18, 0.28, 0.26, (120, 80, 255)),   # violeta
    (0.88, 0.12, 0.22, (30, 190, 255)),   # cian
    (0.78, 0.82, 0.30, (255, 70, 150)),   # magenta
    (0.08, 0.92, 0.22, (20, 210, 170)),   # turquesa
    (0.52, 0.55, 0.16, (60, 80, 255)),    # azul central
]


def fondo(ancho, alto):
    """Degradado orgánico con manchas suaves y grano fino (evita bandas al desenfocar)."""
    # Se pinta pequeño y se amplía: mucho más rápido y el desenfoque es el mismo.
    w, h = ancho // 8, alto // 8
    img = Image.new("RGB", (w, h), BASE)
    for x, y, r, color in MANCHAS:
        capa = Image.new("L", (w, h), 0)
        d = ImageDraw.Draw(capa)
        cx, cy, rad = x * w, y * h, r * max(w, h)
        d.ellipse([cx - rad, cy - rad, cx + rad, cy + rad], fill=235)
        capa = capa.filter(ImageFilter.GaussianBlur(rad * 0.42))
        img = Image.composite(Image.new("RGB", (w, h), color), img, capa)
    img = img.resize((ancho, alto), Image.BICUBIC).filter(ImageFilter.GaussianBlur(4))
    # Grano ligero
    ruido = Image.effect_noise((ancho, alto), 14).convert("RGB")
    ruido = ImageChops.add(ruido, Image.new("RGB", (ancho, alto), (0, 0, 0)))
    img = Image.blend(img, ImageChops.overlay(img, ruido), 0.06)
    return img


def rasterizar_svg(svg, destino, ancho, alto=None, fondo_color=None):
    cmd = ["rsvg-convert", "-w", str(ancho), str(svg), "-o", str(destino)]
    if alto:
        cmd[3:3] = ["-h", str(alto), "--keep-aspect-ratio"]
    if fondo_color:
        cmd[1:1] = ["-b", fondo_color]
    subprocess.run(cmd, check=True)


def main():
    # 1) Fondo de pantalla 4K (JPEG: pesa ~400 KB en vez de varios MB)
    fondo(3840, 2160).save(AQUI / "wallpaper.jpg", quality=90, optimize=True, progressive=True)

    # 2) Imagen del menú de arranque (GRUB) 1280x720: fondo + logo centrado
    bg = fondo(1280, 720).filter(ImageFilter.GaussianBlur(2))
    oscuro = Image.new("RGB", bg.size, (0, 0, 0))
    bg = Image.blend(bg, oscuro, 0.35)
    tmp = AQUI / "_logo_tmp.png"
    rasterizar_svg(AQUI / "logo-white.svg", tmp, 300)
    logo = Image.open(tmp).convert("RGBA")
    bg.paste(logo, ((1280 - logo.width) // 2, 70), logo)
    bg.save(AQUI / "grub-background.png", optimize=True)
    tmp.unlink()

    # 3) Iconos PNG del logo (blanco) para usos varios
    for t in (64, 256, 512):
        rasterizar_svg(AQUI / "logo-white.svg", AQUI / f"logo-white-{t}.png", t)
    print("Activos generados en", AQUI)


if __name__ == "__main__":
    main()
