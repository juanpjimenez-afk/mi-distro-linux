# Fondos de pantalla de Infinity OS

Catálogo de fondos (escritorio, pantalla de bloqueo y miniatura) que lee Ajustes. El índice es `fondos.json`
(`version` 1; rutas relativas a esta carpeta). Todo se genera con `branding/make_fondos.py` a partir de las
fuentes de `branding/fotos/`; los JPEG de aquí son lo que se commitea y lo que usa la ISO.

Por cada fondo `<id>/`:

| archivo | tamaño | notas |
|---|---|---|
| `escritorio.jpg` | 2560x1440 | oscurecido de forma adaptativa (ver abajo) |
| `bloqueo-ANCHOxALTO.jpg` | las 9 resoluciones de bloqueo | sin oscurecer, recorte «cubrir» sin deformar |
| `miniatura.jpg` | 320x180 | sin oscurecer |

Además `branding/wallpaper.jpg` (1920x1080, la cascada oscurecida) y `branding/fondos-bloqueo/wallpaper-lock-*.jpg`
(Islandia) se regeneran con los nombres de siempre: los usan `build/build.sh` y las pruebas.

## Origen y licencia

Todas las fuentes están en `branding/fotos/` tal como llegaron (sin recomprimir).

| id | nombre | fuente | origen / licencia |
|---|---|---|---|
| `cascada` | Cascada otoñal | `escritorio-cascada.webp` (740x493) | foto del proyecto; origen y licencia sin verificar |
| `islandia` | Islandia | `bloqueo-islandia.webp` (940x625) | foto del proyecto; origen y licencia sin verificar |
| `atardecer-playa` | Atardecer en la playa | `fondo-atardecer-playa.png` (625x350) | aportada por el usuario; parece generada por IA; licencia sin verificar |
| `lago-montanas` | Lago entre montañas | `fondo-lago-montanas.webp` (673x456) | aportada por el usuario; lámina cromolitográfica antigua, de aspecto de dominio público pero sin verificar |
| `salto-de-agua` | Salto de agua | `fondo-salto-de-agua.webp` (1275x1542, vertical) | aportada por el usuario; licencia sin verificar |
| `abstracto-colores` | Abstracto de colores | `fondo-abstracto-colores.webp` (1500x1500) | aportada por el usuario. **Lleva una marca de agua comercial** («todo cuadros / tutti Quadri / artwork vault / tudo Quadros») de una web de venta de cuadros: **licencia dudosa**, conviene sustituirla por una imagen con licencia clara |
| `noche-estrellada` | La noche estrellada | `fondo-noche-estrellada.webp` (1280x720) | la obra (Van Gogh, 1889) es de dominio público; la reproducción es la aportada por el usuario y la licencia de esa fotografía no está verificada |

La marca de agua del abstracto **no se retoca**: el fondo es una ventana 16:9 de la fuente (filas 130 a 974 de 1500)
que deja fuera la franja donde está (empieza en la fila 1248); `make_fondos.py` comprueba que no la invade
(`y_max`). La fuente completa, con marca, sigue en `branding/fotos/`.

## Cómo se generó cada uno

Todas las imágenes pasaron por el mismo proceso (detalle en la cabecera de `make_fondos.py`):

1. Recorte a mano de la ventana 16:9 con mejor composición (el mismo para escritorio y bloqueo; las resoluciones
   16:10 recortan algo más los lados). En el salto de agua se prioriza la caída de agua con algo de bosque; el
   cielo no cabe en 16:9 sin perder el salto. En La noche estrellada se descartan los bordes del lienzo.
2. Ampliación a un maestro de 2880x1620 con **Real-ESRGAN x4plus** (modelo de IA, versión NCNN en CPU) sobre la
   ventana y reducción LANCZOS; se corrige el tono global (la red oscurece hasta un 12 %) y se mezcla un
   porcentaje de la ampliación clásica (filtro bilateral + LANCZOS por pasos + unsharp con umbral) para que no
   quede «plástico»:

   | id | ventana de la fuente | mezcla clásica | brillo del escritorio |
   |---|---|---|---|
   | cascada | 736x414 | 25 % | x0,703 |
   | islandia | 928x522 | 20 % | x0,772 |
   | atardecer-playa | 622x350 | 10 % | x0,823 |
   | lago-montanas | 672x378 | 10 % | x0,700 |
   | salto-de-agua | 1264x711 | 35 % | x0,742 |
   | abstracto-colores | 1500x844 | 30 % | x0,700 |
   | noche-estrellada | 1228x691 | 20 % | x0,713 |

   En las fuentes pequeñas (625-940 px de ancho) la IA quita los bloques y el ruido de la compresión y rehace los
   bordes, pero **no recupera detalle que no existe**: lo que se ve en texturas finas (follaje, espuma, pinceladas)
   es una reconstrucción plausible, no la realidad de la foto. En las grandes (salto de agua, abstracto, noche
   estrellada) la mejora es menor y la mezcla clásica es mayor para no inventar demasiado.
3. Escritorio: 2560x1440 con **oscurecimiento adaptativo**: el factor es `luminancia objetivo / luminancia del fondo`,
   entre 0,70 y 1,0, donde la luminancia objetivo es la que tenía el `wallpaper.jpg` de la cascada (71,6 sobre 255).
   Las fotos claras quedan a 0,70 como antes; las oscuras (atardecer, noche) se oscurecen menos para no hundirse.
4. Grano gaussiano fino (σ≈1,1 niveles de 8 bits, semilla fija) para ocultar bandas en degradados.
5. JPEG calidad 92, submuestreo 4:4:4, optimizado y progresivo (miniaturas calidad 88).

Peso total de `fondos/`: 55,7 MiB (4:4:4 a calidad 92 es caro: los bloqueos a 2560 pesan 1,5-2 MB cada uno).

## Regenerar

```sh
python3 -I branding/make_fondos.py             # todos (o: ... cascada lago-montanas)
python3 -I branding/make_fondos.py --clasico   # sin IA
```

Sin el paquete `realesrgan-ncnn-py` el script avisa y usa el método clásico (sin IA el resultado es más blando; el
campo `metodo` de `fondos.json` lo refleja). Para la IA: Python 3.8-3.12, `pip install pillow numpy realesrgan-ncnn-py`
(los pesos vienen en la rueda; sin GPU ni red) y `libomp5` instalado; tarda unos 45 minutos de CPU y ~3 GB de RAM
por proceso (se puede repartir por ids con `--cache`). `branding/make_assets.py` ya no toca estos archivos.
