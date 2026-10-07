"""Acciones del sistema: apagar, reiniciar, suspender, bloquear, cerrar sesión."""
import subprocess
from collections import namedtuple

Accion = namedtuple("Accion", "clave nombre icono comando palabras peligrosa")

ACCIONES = [
    Accion("bloquear", "Bloquear", "system-lock-screen-symbolic", ["loginctl", "lock-session"],
           "bloquear lock pantalla", False),
    Accion("suspender", "Suspender", "weather-clear-night-symbolic", ["systemctl", "suspend"],
           "suspender dormir sleep suspend", False),
    Accion("salir", "Cerrar sesión", "system-log-out-symbolic", ["hyprctl", "dispatch", "exit"],
           "cerrar sesion salir logout", True),
    Accion("reiniciar", "Reiniciar", "system-reboot-symbolic", ["systemctl", "reboot"],
           "reiniciar reboot restart", True),
    Accion("apagar", "Apagar", "system-shutdown-symbolic", ["systemctl", "poweroff"],
           "apagar poweroff shutdown", True),
]
POR_CLAVE = {a.clave: a for a in ACCIONES}


def ejecutar(accion):
    try:
        subprocess.Popen(accion.comando, start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError as e:
        print(f"infinity-shell: no se pudo ejecutar {accion.comando}: {e}")
