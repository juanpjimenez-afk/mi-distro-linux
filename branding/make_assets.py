#!/usr/bin/env python3
"""Genera los activos gráficos de Infinity OS a partir de las fotos de branding/fotos/.

Requiere: python3-pil y librsvg2-bin (rsvg-convert).

    python3 -I branding/make_assets.py

Resultado:
- wallpaper.jpg        escritorio: la cascada, a pantalla completa, con menos brillo
- fondos-bloqueo/      pantalla de bloqueo, una imagen por resolución: la foto de paisaje a pantalla
                       completa (recorte centrado, sin deformar)
- grub-background.png  menú de arranque (fondo abstracto + logo)
- logo-white-*.png     iconos del logo
"""
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter

AQUI = Path(__file__).resolve().parent
FOTOS = AQUI / "fotos"

W, H = 1920, 1080
RESOLUCIONES_BLOQUEO = [(1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600), (1366, 768),
                        (1280, 800), (1600, 900), (1440, 900), (1280, 720)]
BRILLO_ESCRITORIO = 0.70    # 70 % del brillo original: se nota la foto y sigue viéndose el cristal
BRILLO_LADOS_BLOQUEO = 0.55

# Paleta del fondo abstracto (solo para el menú de arranque)
BASE = (5, 7, 18)
MANCHAS = [
    # (x, y, radio, color)  — coordenadas relativas (0..1)
    (0.18, 0.28, 0.26, (120, 80, 255)),   # violeta
    (0.88, 0.12, 0.22, (30, 190, 255)),   # cian
    (0.78, 0.82, 0.30, (255, 70, 150)),   # magenta
    (0.08, 0.92, 0.22, (20, 210, 170)),   # turquesa
    (0.52, 0.55, 0.16, (60, 80, 255)),    # azul central
]


def cubrir(img, ancho, alto):
    """Escala la imagen para cubrir ancho x alto y recorta el centro."""
    escala = max(ancho / img.width, alto / img.height)
    grande = img.resize((round(img.width * escala), round(img.height * escala)), Image.LANCZOS)
    x0 = (grande.width - ancho) // 2
    y0 = (grande.height - alto) // 2
    return grande.crop((x0, y0, x0 + ancho, y0 + alto))


def fondo_abstracto(ancho, alto):
    """Degradado orgánico con manchas suaves y grano fino (solo para el menú de arranque)."""
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
    ruido = Image.effect_noise((ancho, alto), 14).convert("RGB")
    ruido = ImageChops.add(ruido, Image.new("RGB", (ancho, alto), (0, 0, 0)))
    return Image.blend(img, ImageChops.overlay(img, ruido), 0.06)


def rasterizar_svg(svg, destino, ancho, alto=None, fondo_color=None):
    cmd = ["rsvg-convert", "-w", str(ancho), str(svg), "-o", str(destino)]
    if alto:
        cmd[3:3] = ["-h", str(alto), "--keep-aspect-ratio"]
    if fondo_color:
        cmd[1:1] = ["-b", fondo_color]
    subprocess.run(cmd, check=True)


def escritorio():
    foto = Image.open(FOTOS / "escritorio-cascada.webp").convert("RGB")
    img = cubrir(foto, W, H).filter(ImageFilter.UnsharpMask(radius=2, percent=60, threshold=2))
    img = ImageEnhance.Brightness(img).enhance(BRILLO_ESCRITORIO)
    img.save(AQUI / "wallpaper.jpg", quality=92, optimize=True, progressive=True)


def bloqueo(ancho, alto):
    """Fondo de la pantalla de bloqueo: la foto de paisaje a pantalla completa."""
    foto = Image.open(FOTOS / "bloqueo-islandia.webp").convert("RGB")
    img = cubrir(foto, ancho, alto)
    carpeta = AQUI / "fondos-bloqueo"
    carpeta.mkdir(exist_ok=True)
    img.save(carpeta / f"wallpaper-lock-{ancho}x{alto}.jpg", quality=92, optimize=True)


def grub():
    bg = fondo_abstracto(1024, 768).filter(ImageFilter.GaussianBlur(2))
    oscuro = Image.new("RGB", bg.size, (0, 0, 0))
    bg = Image.blend(bg, oscuro, 0.35)
    tmp = AQUI / "_logo_tmp.png"
    rasterizar_svg(AQUI / "logo-white.svg", tmp, 96)
    logo = Image.open(tmp).convert("RGBA")
    bg.paste(logo, (30, 8), logo)
    bg.save(AQUI / "grub-background.png", optimize=True)
    tmp.unlink()


def main():
    escritorio()
    for ancho, alto in RESOLUCIONES_BLOQUEO:
        bloqueo(ancho, alto)
    grub()
    for t in (64, 256, 512):
        rasterizar_svg(AQUI / "logo-white.svg", AQUI / f"logo-white-{t}.png", t)
    print("Activos generados en", AQUI)


if __name__ == "__main__":
    main()
