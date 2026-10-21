#!/usr/bin/env python3
"""Genera los lanzadores de las WebApps (Netflix, HBO Max, Disney+, YouTube, Claude, Gemini, NotebookLM, Classroom, Spotify):
overlay/usr/share/applications/infinity-web-<id>.desktop. Cada una abre su web en una ventana propia de Google Chrome
(--app=URL, sin barra de direcciones). Chrome se instala en la ISO (build/chroot-setup.sh); además trae Widevine, que
Netflix, HBO Max y Disney+ necesitan. Los iconos viven en overlay/usr/share/infinity/webapps/<id>.svg.
Uso:  python3 branding/make_webapps.py"""
import os
import re

RAIZ = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
SALIDA = os.path.join(RAIZ, "overlay", "usr", "share", "applications")
# Wayland (la ISO no trae X11) y sin los avisos de primera vez de Chrome (condiciones, bienvenida)
CHROME = "google-chrome-stable --ozone-platform=wayland --no-first-run --no-default-browser-check"
VIDEO = "AudioVideo;Video;Network;"
# id, nombre, url, categorías, palabras clave
APPS = [
    ("spotify", "Spotify", "https://open.spotify.com/", "AudioVideo;Audio;Music;Network;", "musica;podcasts;streaming;"),
    ("netflix", "Netflix", "https://www.netflix.com/browse", VIDEO, "series;peliculas;cine;streaming;"),
    ("hbo-max", "HBO Max", "https://play.hbomax.com/", VIDEO, "hbo;max;series;peliculas;streaming;"),
    ("disney-plus", "Disney+", "https://www.disneyplus.com/", VIDEO, "disney;pixar;marvel;star wars;streaming;"),
    ("youtube", "YouTube", "https://www.youtube.com/", VIDEO, "videos;musica;streaming;"),
    ("claude", "Claude", "https://claude.ai/", "Utility;Network;", "ia;asistente;chat;anthropic;"),
    ("gemini", "Gemini", "https://gemini.google.com/app", "Utility;Network;", "ia;asistente;chat;google;"),
    ("notebooklm", "NotebookLM", "https://notebooklm.google.com/", "Education;Utility;Network;", "ia;notas;estudio;google;"),
    ("classroom", "Google Classroom", "https://classroom.google.com/", "Education;Network;", "clases;tareas;colegio;google;"),
]


def clase_ventana(url):
    """El app_id que Chrome da a una ventana --app=URL: chrome-<host>_<ruta con / → _>-Default."""
    m = re.match(r"^https://([^/?#]+)(/[^?#]*)?", url)
    return f"chrome-{m.group(1)}_{m.group(2) or '/'}".replace("/", "_") + "-Default"


def texto(ident, nombre, url, categorias, claves):
    return (f"[Desktop Entry]\nType=Application\nName={nombre}\nComment=Abre {nombre} en su propia ventana\n"
            f"Exec={CHROME} --app={url}\nIcon=/usr/share/infinity/webapps/{ident}.svg\nCategories={categorias}\n"
            f"Keywords={claves}\nStartupWMClass={clase_ventana(url)}\nStartupNotify=true\nTerminal=false\n")


if __name__ == "__main__":
    for a in APPS:
        with open(os.path.join(SALIDA, f"infinity-web-{a[0]}.desktop"), "w", encoding="utf-8") as f:
            f.write(texto(*a))
    print(f"{len(APPS)} lanzadores en {SALIDA}")
