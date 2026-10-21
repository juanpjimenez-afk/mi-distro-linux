"""Bluetooth de la píldora de estado, sin GTK (se prueba sin pantalla): ¿hay adaptador? y qué icono usar."""
import glob
import os

RAIZ = "/sys/class/bluetooth"
ICONOS = ("bluetooth-active-symbolic", "bluetooth-symbolic")


def hay_adaptador(raiz=RAIZ):
    """True si el kernel ve un adaptador Bluetooth (hci0, hci1…). Sin adaptador el botón no se enseña."""
    return any(os.path.basename(r).startswith("hci") and ":" not in os.path.basename(r)
               for r in glob.glob(os.path.join(raiz, "*")))


def icono(tiene=lambda n: True):
    """El primer icono de ICONOS que acepte `tiene(nombre)` (en GTK: IconTheme.has_icon); si ninguno, el último."""
    return next((n for n in ICONOS if tiene(n)), ICONOS[-1])
