#!/usr/bin/env python3
"""Genera los fondos de pantalla de Infinity OS (escritorio, bloqueo y miniaturas) a partir de branding/fotos/.

    python3 -I branding/make_fondos.py                 # todos los fondos
    python3 -I branding/make_fondos.py cascada lago-montanas   # solo algunos (no toca fondos.json de los demas)
    python3 -I branding/make_fondos.py --clasico       # fuerza el metodo clasico (sin IA)
    python3 -I branding/make_fondos.py --cache /ruta   # guarda/reutiliza el resultado x4 de la IA (ahorra minutos)

Funciona desde cualquier directorio. Dependencias:
  - Obligatorias: python3-pil (Pillow >= 10) y python3-numpy.
  - Opcional, para el ampliador por IA: Real-ESRGAN (modelo "realesrgan-x4plus") en su version NCNN.
    Corre en CPU, sin GPU ni red (los pesos van dentro de la rueda de PyPI):

        python3.12 -m venv /tmp/venv-ia && /tmp/venv-ia/bin/pip install pillow numpy realesrgan-ncnn-py
        # la rueda enlaza con libomp.so.5 (OpenMP de LLVM): en Debian/Ubuntu `apt install libomp5`
        # (en una maquina sin esa libreria sirve un enlace libomp.so.5 -> libiomp5.so del paquete pip intel-openmp
        # con LD_LIBRARY_PATH apuntando a el).
        /tmp/venv-ia/bin/python -I branding/make_fondos.py

    Hay ruedas para CPython 3.8-3.12 (no 3.13). Si el modulo no se puede importar, el script AVISA y usa el
    metodo clasico para esa imagen; el "metodo" de cada fondo queda escrito en fondos/fondos.json.
    Coste: la red usa un solo hilo y ~3 GB de RAM; todo el catalogo tarda ~45 minutos de CPU (el abstracto,
    ~15). Para repartirlo, se lanzan varios procesos con ids distintos y la misma --cache, y al final uno
    solo sin ids (que ya encuentra el resultado de la IA en la cache y tarda ~1 minuto).
    Los JPEG finales son lo que se commitea: no hace falta repetir la generacion para compilar la ISO.

Que hace con cada fondo (todo en sRGB de 8 bits):
  1. Recorta de la fuente la ventana 16:9 con mejor composicion (clave "ventana" de FONDOS, elegida a mano
     mirando cada imagen). Si la fuente trae un perfil ICC que no es sRGB se convierte. En el recorte quedan
     fuera los bordes del lienzo (noche estrellada) y la marca de agua (abstracto: la ventana acaba en y=974
     y la marca empieza en y=1248; "y_max" lo comprueba).
  2. Amplia a un "maestro" de 2880x1620 (suficiente para recortar 2560x1600 sin reescalar de mas):
       - IA:      Real-ESRGAN x4plus sobre la ventana (quita el ruido/bloques de la compresion y reconstruye
                  bordes), reduccion LANCZOS al maestro, correccion del tono global (la red oscurece hasta un
                  12 %: se devuelve la luz y el color de la foto original, solo en baja frecuencia) y mezcla
                  de un pequeno porcentaje de la ampliacion clasica ("mezcla" de cada fondo, 10-35 %) para que
                  no quede un aspecto "plastico" ni se vean texturas inventadas de mas.
       - clasico: filtro bilateral suave (quita ruido y bloques sin comerse los bordes), ampliacion LANCZOS
                  en pasos de x2 como maximo y mascara de desenfoque (unsharp) con umbral.
  3. Escritorio: maestro -> 2560x1440, OSCURECIMIENTO ADAPTATIVO y grano. Bloqueo: recorte "cubrir" del maestro
     a cada resolucion de RESOLUCIONES_BLOQUEO (sin oscurecer, sin deformar; las resoluciones 16:10 recortan
     un poco los lados de la misma ventana) y grano. Miniatura 320x180 del maestro sin oscurecer.
  4. JPEG 4:4:4 optimizado y progresivo, calidad 92 (miniaturas 88, no progresivas).
     Pesa mucho (unos 55 MB el catalogo) porque 4:4:4 a calidad 92 conserva el detalle fino.

Oscurecimiento adaptativo del escritorio. Antes era un 0,70x fijo: bien para la cascada (clara y saturada) pero
una foto ya oscura, como La noche estrellada o un atardecer, quedaba hundida. Ahora el objetivo es la
luminancia media (Rec. 709 sobre los valores sRGB de 8 bits) que tenia el wallpaper.jpg de la cascada
(LUMA_OBJETIVO, medida una vez). Para cada fondo: factor = LUMA_OBJETIVO / luminancia_media, limitado al intervalo
[BRILLO_ESCRITORIO, 1.0]: nunca aclara y nunca baja de 0,70x. Las fotos claras acaban en 0,70x como antes; las
oscuras se quedan igual o casi. El factor aplicado se escribe en fondos.json ("brillo_escritorio").

El grano (ruido gaussiano de sigma ~1,1 niveles, semilla fija por fondo y tamano) esconde las bandas de los
degradados (cielos, sol) al pasar a 8 bits y disimula la blandura de lo ampliado; es reproducible.
"""
import argparse
import hashlib
import io
import json
import sys
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageFilter

AQUI = Path(__file__).resolve().parent
FOTOS = AQUI / "fotos"
SALIDA = AQUI / "fondos"

# Se mantienen sincronizadas con make_assets.py (que ya no genera fondos y las reexporta de aqui).
RESOLUCIONES_BLOQUEO = [(1920, 1080), (1920, 1200), (2560, 1440), (2560, 1600), (1366, 768),
                        (1280, 800), (1600, 900), (1440, 900), (1280, 720)]
BRILLO_ESCRITORIO = 0.70          # suelo del oscurecimiento (el valor fijo de antes)
ESCRITORIO = (2560, 1440)
MAESTRO = (2880, 1620)
MINIATURA = (320, 180)
CALIDAD = 92                      # JPEG de escritorio y bloqueo (>= 92; 4:4:4)
CALIDAD_MINIATURA = 88
TESELA, MARGEN = 96, 12           # teselas de la IA y margen descartado (px de la fuente)
GRANO = 1.1                       # sigma del grano, en niveles de 8 bits

# Luminancia media (Rec. 709, valores sRGB de 8 bits) del wallpaper.jpg de la cascada de antes de esta
# version (foto de 740x493 ampliada, LANCZOS, brillo 0,70): es el aspecto del escritorio que se queria conservar.
LUMA_OBJETIVO = 71.6

# ---------------------------------------------------------------------------------------------------------
# Catalogo. El orden es el del selector de Ajustes.
#   ventana: (x0, y0, ancho, alto) en pixeles de la fuente; ~16:9. Elegida a mano mirando cada imagen.
#   y_max:   (opcional) la ventana no puede pasar de esta fila de la fuente (marca de agua desde y=1248).
#   ia:      True si con este fondo la IA sale mejor que el clasico (decidido mirando recortes al 100 %).
#   mezcla:  parte de la ampliacion clasica que se mezcla con la de la IA (0 = IA pura).
#   nota:    origen / licencia, tal cual en fondos.json.
FONDOS = [
    dict(id="cascada", nombre="Cascada otoñal", fuente="fotos/escritorio-cascada.webp",
         ventana=(2, 40, 736, 414), ia=True, mezcla=0.25,
         nota="Foto del proyecto (branding/fotos), origen y licencia sin verificar."),
    dict(id="islandia", nombre="Islandia", fuente="fotos/bloqueo-islandia.webp",
         ventana=(6, 55, 928, 522), ia=True, mezcla=0.20,
         nota="Foto del proyecto (branding/fotos), origen y licencia sin verificar."),
    dict(id="atardecer-playa", nombre="Atardecer en la playa", fuente="fotos/fondo-atardecer-playa.png",
         ventana=(2, 0, 622, 350), ia=True, mezcla=0.10,
         nota="Aportada por el usuario; parece generada por IA. Licencia sin verificar."),
    dict(id="lago-montanas", nombre="Lago entre montañas", fuente="fotos/fondo-lago-montanas.webp",
         ventana=(0, 45, 672, 378), ia=True, mezcla=0.10,
         nota="Aportada por el usuario; lámina cromolitográfica antigua, de aspecto de dominio público "
              "pero sin verificar."),
    dict(id="salto-de-agua", nombre="Salto de agua", fuente="fotos/fondo-salto-de-agua.webp",
         ventana=(4, 745, 1264, 711), ia=True, mezcla=0.35,
         nota="Aportada por el usuario (foto vertical recortada a 16:9). Licencia sin verificar."),
    dict(id="abstracto-colores", nombre="Abstracto de colores", fuente="fotos/fondo-abstracto-colores.webp",
         ventana=(0, 130, 1500, 844), y_max=1240, ia=True, mezcla=0.30,
         nota="Aportada por el usuario. La fuente lleva una marca de agua comercial (todo cuadros / tutti Quadri "
              "/ artwork vault / tudo Quadros) en la franja inferior derecha, que queda FUERA del recorte. "
              "Procede de una web de venta de cuadros: licencia dudosa."),
    dict(id="noche-estrellada", nombre="La noche estrellada", fuente="fotos/fondo-noche-estrellada.webp",
         ventana=(26, 12, 1228, 691), ia=True, mezcla=0.20,
         nota="«La noche estrellada» (Van Gogh, 1889): obra de dominio público. La reproducción es la aportada "
              "por el usuario, licencia de la fotografía sin verificar; se recortan los bordes del lienzo."),
]
FONDOS_POR_ID = {f["id"]: f for f in FONDOS}


# ---------------------------------------------------------------------------------------------------------
# Lectura y utilidades de imagen

def abrir_srgb(ruta):
    """Abre la imagen, convierte a sRGB si trae otro perfil ICC y devuelve un RGB de 8 bits."""
    im = Image.open(ruta)
    icc = im.info.get("icc_profile")
    im = im.convert("RGB")
    if icc:
        try:
            origen = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            if "srgb" not in ImageCms.getProfileDescription(origen).lower():
                im = ImageCms.profileToProfile(im, origen, ImageCms.createProfile("sRGB"), outputMode="RGB")
        except (ImageCms.PyCMSError, OSError):
            pass
    return im


def luminancia(arr):
    """Luminancia media Rec. 709 de un array HxWx3 de valores 0..255."""
    a = arr.astype(np.float64)
    return float((0.2126 * a[..., 0] + 0.7152 * a[..., 1] + 0.0722 * a[..., 2]).mean())


def cubrir(img, ancho, alto):
    """Escala la imagen para cubrir ancho x alto y recorta el centro (sin deformar)."""
    escala = max(ancho / img.width, alto / img.height)
    grande = img.resize((round(img.width * escala), round(img.height * escala)), Image.LANCZOS)
    x0 = (grande.width - ancho) // 2
    y0 = (grande.height - alto) // 2
    return grande.crop((x0, y0, x0 + ancho, y0 + alto))


def bilateral(img, radio=2, sigma_s=1.4, sigma_r=9.0):
    """Filtro bilateral en numpy: promedia solo con vecinos de color parecido. Quita el ruido y el bloqueo
    de la compresion en zonas lisas y respeta los bordes (sigma_r = diferencia de color tolerada)."""
    a = np.asarray(img, dtype=np.float32)
    pad = np.pad(a, ((radio, radio), (radio, radio), (0, 0)), mode="reflect")
    h, w = a.shape[:2]
    acum = np.zeros_like(a)
    peso = np.zeros(a.shape[:2] + (1,), dtype=np.float32)
    for dy in range(-radio, radio + 1):
        for dx in range(-radio, radio + 1):
            vec = pad[radio + dy:radio + dy + h, radio + dx:radio + dx + w]
            d2 = ((vec - a) ** 2).sum(axis=2, keepdims=True)
            p = np.exp(-(dx * dx + dy * dy) / (2 * sigma_s ** 2) - d2 / (2 * sigma_r ** 2 * 3))
            acum += vec * p
            peso += p
    return Image.fromarray(np.clip(acum / peso + 0.5, 0, 255).astype(np.uint8))


def ampliar_pasos(img, ancho_final):
    """Amplia con LANCZOS en pasos de x2 como maximo hasta ancho_final (ancho y alto proporcionales)."""
    while img.width < ancho_final:
        nuevo = min(ancho_final, img.width * 2)
        img = img.resize((nuevo, round(img.height * nuevo / img.width)), Image.LANCZOS)
    return img


# ---------------------------------------------------------------------------------------------------------
# Ampliacion

def cargar_ia():
    """Devuelve la clase Realesrgan o None (con el motivo)."""
    try:
        from realesrgan_ncnn_py import Realesrgan
        return Realesrgan, None
    except Exception as e:      # ImportError, o libomp/libvulkan ausentes (OSError al enlazar)
        return None, f"{type(e).__name__}: {e}"


def ia_x4(ventana_img, Realesrgan, cache=None, clave=""):
    """Real-ESRGAN x4plus en CPU (gpuid=-1). Se procesa por teselas de TESELA px con un margen de MARGEN px
    que se descarta (el campo receptivo de la red es local, no se ven costuras) para no gastar gigas de RAM."""
    ruta = None
    if cache:
        h = hashlib.sha1(ventana_img.tobytes() + clave.encode()).hexdigest()[:12]
        ruta = Path(cache) / f"{clave}-{h}-x4.png"
        if ruta.exists():
            return Image.open(ruta).convert("RGB")
    r = Realesrgan(gpuid=-1, model=4, tilesize=0)        # modelo 4 = realesrgan-x4plus
    w, h = ventana_img.size
    out = Image.new("RGB", (w * 4, h * 4))
    for y in range(0, h, TESELA):
        for x in range(0, w, TESELA):
            x1, y1 = min(w, x + TESELA), min(h, y + TESELA)
            a, b, c, d = max(0, x - MARGEN), max(0, y - MARGEN), min(w, x1 + MARGEN), min(h, y1 + MARGEN)
            t = r.process_pil(ventana_img.crop((a, b, c, d)))
            out.paste(t.crop(((x - a) * 4, (y - b) * 4, (x1 - a) * 4, (y1 - b) * 4)), (x * 4, y * 4))
    if ruta:
        Path(cache).mkdir(parents=True, exist_ok=True)
        out.save(ruta)
    return out


def clasico_maestro(ventana_img):
    """Metodo clasico: bilateral suave en la fuente, LANCZOS por pasos y unsharp con umbral."""
    limpia = bilateral(ventana_img)
    grande = ampliar_pasos(limpia, MAESTRO[0]).resize(MAESTRO, Image.LANCZOS)
    return grande.filter(ImageFilter.UnsharpMask(radius=2.0, percent=55, threshold=3))


def igualar_tono(ia, ref, lado=48):
    """La red de IA oscurece y desplaza un poco el color (hasta un 12 % de brillo medio en el salto de agua).
    Se corrige solo la componente de baja frecuencia: se multiplica `ia` por el cociente (borroso) entre la
    ampliacion clasica `ref` y la propia `ia`, limitado a 0,7..1,4, de modo que el detalle es el de la IA
    pero la luz y el color global son los de la foto original."""
    a = np.asarray(ia, dtype=np.float32)
    pequena = lambda im: np.asarray(im.resize((ia.width // lado, ia.height // lado), Image.BOX), dtype=np.float32)
    ratio = (pequena(ref) + 1.0) / (pequena(ia) + 1.0)
    ratio = np.stack([np.asarray(Image.fromarray(ratio[..., c]).resize(ia.size, Image.BICUBIC)) for c in range(3)], -1)
    return Image.fromarray(np.clip(a * np.clip(ratio, 0.7, 1.4) + 0.5, 0, 255).astype(np.uint8))


def maestro(fondo, Realesrgan, cache=None, forzar_clasico=False):
    """Devuelve (imagen RGB 2880x1620, texto del metodo)."""
    x0, y0, w, h = fondo["ventana"]
    asp = (w / h) / (16 / 9)
    assert abs(asp - 1) < 0.005, f"{fondo['id']}: la ventana no es 16:9 ({w}x{h})"
    fuente = abrir_srgb(AQUI / fondo["fuente"])
    assert x0 >= 0 and y0 >= 0 and x0 + w <= fuente.width and y0 + h <= fuente.height, fondo["id"]
    assert y0 + h <= fondo.get("y_max", fuente.height), f"{fondo['id']}: la ventana invade la zona con marca de agua"
    ventana = fuente.crop((x0, y0, x0 + w, y0 + h))
    clasico = clasico_maestro(ventana)
    desc_ventana = f"ventana 16:9 {w}x{h} de {fuente.width}x{fuente.height}"
    if fondo["ia"] and Realesrgan and not forzar_clasico:
        x4 = ia_x4(ventana, Realesrgan, cache, fondo["id"])
        ia = igualar_tono(x4.resize(MAESTRO, Image.LANCZOS), ampliar_pasos(ventana, MAESTRO[0]).resize(MAESTRO, Image.LANCZOS))
        m = fondo["mezcla"]
        res = Image.blend(ia, clasico, m) if m else ia
        texto = (f"{desc_ventana}; Real-ESRGAN x4plus (IA, CPU) y reducción LANCZOS a {MAESTRO[0]}x{MAESTRO[1]}, tono global igualado al original"
                 + (f", mezclado un {round(m * 100)} % con la ampliación clásica" if m else ""))
        return res, texto
    texto = (f"{desc_ventana}; clásico: filtro bilateral, ampliación LANCZOS por pasos de x2 y unsharp con umbral"
             + ("" if not fondo["ia"] else " (sin ampliador IA en esta ejecución)"))
    return clasico, texto


# ---------------------------------------------------------------------------------------------------------
# Salida

def factor_escritorio(arr):
    """Oscurecimiento adaptativo (ver la cabecera): LUMA_OBJETIVO / luminancia, entre BRILLO_ESCRITORIO y 1."""
    return min(1.0, max(BRILLO_ESCRITORIO, LUMA_OBJETIVO / luminancia(arr)))


def con_grano(arr, semilla):
    """arr float32 HxWx3 (0..255) -> imagen 8 bits con grano gaussiano fino y reproducible."""
    rng = np.random.default_rng(int.from_bytes(hashlib.sha1(semilla.encode()).digest()[:8], "little"))
    lum = rng.normal(0.0, GRANO, arr.shape[:2]).astype(np.float32)[..., None]
    col = rng.normal(0.0, GRANO * 0.35, arr.shape).astype(np.float32)
    return Image.fromarray(np.clip(arr + lum + col + 0.5, 0, 255).astype(np.uint8))


def guardar_jpeg(img, ruta, calidad, progresivo):
    ruta.parent.mkdir(parents=True, exist_ok=True)
    img.save(ruta, "JPEG", quality=calidad, subsampling=0, optimize=True, progressive=progresivo)


def generar_fondo(fondo, Realesrgan, cache=None, forzar_clasico=False, salida=SALIDA):
    t0 = time.time()
    m, metodo = maestro(fondo, Realesrgan, cache, forzar_clasico)
    carpeta = salida / fondo["id"]
    # Escritorio
    base = m.resize(ESCRITORIO, Image.LANCZOS)
    arr = np.asarray(base, dtype=np.float32)
    f = factor_escritorio(arr)
    esc = con_grano(arr * f, fondo["id"] + "/escritorio")
    guardar_jpeg(esc, carpeta / "escritorio.jpg", CALIDAD, True)
    # Miniatura: del escritorio ya oscurecido? No: de la imagen sin oscurecer, para que se vea el fondo real.
    guardar_jpeg(m.resize(MINIATURA, Image.LANCZOS), carpeta / "miniatura.jpg", CALIDAD_MINIATURA, False)
    # Bloqueo
    bloqueo = {}
    for ancho, alto in RESOLUCIONES_BLOQUEO:
        img = cubrir(m, ancho, alto)
        img = con_grano(np.asarray(img, dtype=np.float32), f"{fondo['id']}/bloqueo{ancho}x{alto}")
        nombre = f"bloqueo-{ancho}x{alto}.jpg"
        guardar_jpeg(img, carpeta / nombre, CALIDAD, True)
        bloqueo[f"{ancho}x{alto}"] = f"{fondo['id']}/{nombre}"
    print(f"  {fondo['id']}: brillo escritorio x{f:.3f}  ({time.time() - t0:.0f} s)  {metodo}")
    return dict(id=fondo["id"], nombre=fondo["nombre"], escritorio=f"{fondo['id']}/escritorio.jpg",
                miniatura=f"{fondo['id']}/miniatura.jpg", bloqueo=bloqueo, fuente=fondo["fuente"],
                metodo=metodo, nota=fondo["nota"], brillo_escritorio=round(f, 3)), m, f


def compatibilidad(m_cascada, m_islandia, f_cascada, salida=AQUI):
    """branding/wallpaper.jpg (1920x1080, la cascada oscurecida) y branding/fondos-bloqueo/wallpaper-lock-*.jpg
    (la foto de Islandia): los nombres de siempre, que usan build.sh y las pruebas."""
    base = m_cascada.resize((1920, 1080), Image.LANCZOS)
    arr = np.asarray(base, dtype=np.float32)
    guardar_jpeg(con_grano(arr * f_cascada, "wallpaper.jpg"), salida / "wallpaper.jpg", CALIDAD, True)
    carpeta = salida / "fondos-bloqueo"
    for ancho, alto in RESOLUCIONES_BLOQUEO:
        img = cubrir(m_islandia, ancho, alto)
        img = con_grano(np.asarray(img, dtype=np.float32), f"islandia/bloqueo{ancho}x{alto}")
        guardar_jpeg(img, carpeta / f"wallpaper-lock-{ancho}x{alto}.jpg", CALIDAD, True)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("ids", nargs="*", help="ids a regenerar (por defecto, todos)")
    ap.add_argument("--clasico", action="store_true", help="no usar el ampliador por IA")
    ap.add_argument("--cache", help="carpeta donde guardar/reutilizar el resultado x4 de la IA")
    args = ap.parse_args(argv)
    ids = args.ids or [f["id"] for f in FONDOS]
    for i in ids:
        if i not in FONDOS_POR_ID:
            ap.error(f"id desconocido: {i} (hay: {', '.join(FONDOS_POR_ID)})")
    Realesrgan, motivo = (None, "desactivado con --clasico") if args.clasico else cargar_ia()
    if Realesrgan is None:
        print(f"AVISO: sin ampliador por IA ({motivo}); se usa el método clásico.", file=sys.stderr)
    else:
        print("Ampliador por IA: Real-ESRGAN x4plus (NCNN, CPU)")

    SALIDA.mkdir(exist_ok=True)
    catalogo_ruta = SALIDA / "fondos.json"
    previo = {}
    if catalogo_ruta.exists():
        previo = {e["id"]: e for e in json.loads(catalogo_ruta.read_text(encoding="utf-8"))["fondos"]}
    maestros = {}
    for fid in ids:
        entrada, m, f = generar_fondo(FONDOS_POR_ID[fid], Realesrgan, args.cache, args.clasico)
        previo[fid] = entrada
        maestros[fid] = (m, f)
    if "cascada" in maestros and "islandia" in maestros:
        compatibilidad(maestros["cascada"][0], maestros["islandia"][0], maestros["cascada"][1])
        print("  compatibilidad: wallpaper.jpg y fondos-bloqueo/ regenerados")
    elif "cascada" in maestros or "islandia" in maestros:
        print("  (wallpaper.jpg y fondos-bloqueo/ solo se regeneran si se piden cascada e islandia a la vez)")
    catalogo = {"version": 1, "fondos": [previo[f["id"]] for f in FONDOS if f["id"] in previo]}
    catalogo_ruta.write_text(json.dumps(catalogo, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print("Fondos generados en", SALIDA)


if __name__ == "__main__":
    main()
