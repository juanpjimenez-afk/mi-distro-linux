# Controles de ventana (−, □, ×)

Hyprland no dibuja barras de título ni botones, y no tiene "minimizar". GTK4 por sí solo pinta solo la ×, y
GTK3 (Thunar), foot y Tk (Infinity Juegos, por XWayland) no pintan nada. Por eso el shell dibuja los controles,
iguales para todas las ventanas: `shell/infinity_shell/controles.py` (la parte GTK) y `controles_logica.py`
(la geometría y las órdenes a Hyprland, sin GTK, con sus pruebas en `shell/tests/test_controles.py`).

## Qué se ve

Una **pastilla de cristal** de 78 × 26 px con tres botones redondos: **−** minimizar, **□** maximizar o
restaurar (pasa a ❐ cuando la ventana está maximizada) y **×** cerrar (con un tinte rojo al pasar el ratón).
Es una superficie de `layer-shell` por ventana (capa `TOP`, espacio de nombres `infinity-controles`, sin foco de
teclado), así que la regla de desenfoque `^infinity-.*$` de `hyprland.conf` le da el mismo cristal que al dock.
No lleva `xray`: dentro de una ventana, el desenfoque debe ver también lo que hay debajo.

- **Dónde:** encima de la esquina superior derecha de la ventana, en el hueco superior de la pantalla
  (`general:gaps_out = 34, 14, 14, 14`: arriba, derecha, abajo, izquierda). Si encima no cabe (ventana flotante
  pegada al borde de arriba, u otra ventana justo encima, como las filas de abajo de un mosaico), va **dentro**,
  en la esquina superior derecha, a 8 px de los bordes.
- **Quién la lleva:** las ventanas normales del espacio de trabajo visible de cada monitor. No la llevan las
  minimizadas, las de pantalla completa real (un vídeo, `Super + F`), las diminutas (< 120 × 60) ni pAnkI, que
  trae los suyos en su cabecera (lista `EXCLUIDAS` en `controles_logica.py`).
- **Ajustes:** al cambiar la separación entre ventanas, Ajustes conserva el hueco superior (nunca menos de 34 px).

## Qué hace cada botón

Siempre sobre **esa** ventana (por su dirección), no sobre la que tenga el foco:

| Botón | Orden a Hyprland |
|---|---|
| × | `closewindow address:0x…` (cierre amable: la app puede preguntar si guardar) |
| − | `movetoworkspacesilent special:minimizadas,address:0x…` (nunca `togglespecialworkspace`: no aparece el espacio especial) |
| □ | `focuswindow address:0x…` y `fullscreen 1` |

Es el modo 1 (**maximizar**) y no el 0 porque conserva los huecos: el dock, el orbe y la propia pastilla siguen
visibles, y es lo que la gente espera de "expandir". `Super + F` sigue siendo pantalla completa real.

**Restaurar:** el dock cuenta las ventanas minimizadas como ventanas de su app (el icono sigue ahí). Un clic en
una app cuyas ventanas están todas minimizadas trae la última al espacio de trabajo activo
(`movetoworkspace`) y le da el foco. Atajos: `Super + M` minimiza la ventana activa y `Super + Shift + F`
maximiza o restaura.

## Seguimiento de la ventana

Hyprland no avisa de que una ventana se arrastra o se redimensiona. Mientras hay pastillas visibles, el shell
mira la geometría unas 4 veces por segundo (dos peticiones pequeñas por el socket, en un hilo) y además
reacciona a los eventos de Hyprland (abrir, cerrar, cambiar de espacio...). Solo mueve una pastilla si su sitio
cambió. Al arrastrar una ventana la pastilla la sigue con un retraso de hasta ~250 ms. Sin ventanas visibles
no hay sondeo; sin Hyprland, el módulo no hace nada.

## Botones de GTK

Para que ninguna ventana tenga dos juegos de botones, el diseño de botones de GTK está vacío
(`gtk-decoration-layout=:` en `gtk-3.0` y `gtk-4.0` de `settings.ini`, y `button-layout=':'` en el esquema
`org.gnome.desktop.wm.preferences`, `90_infinity.gschema.override`). La cabecera de pAnkI fija su propio
`:minimize,maximize,close`, así que conserva sus botones. La × de pAnkI y su □ van por GTK; Hyprland puede no
atender el "minimizar" de un cliente (no tiene esa función).

## Límites

Probado con pruebas sin pantalla y con GTK en Xvfb (doble de `layer-shell`) y con capturas simuladas. **No** se
ha visto en una sesión real de Hyprland: la sintaxis de los dispatchers solo está validada en nombres
(`--verify-config`), no su comportamiento; la posición real de las superficies, el retraso al arrastrar, las
ventanas Tk por XWayland y el comportamiento de libadwaita (Epiphany) con el diseño vacío están sin comprobar.
