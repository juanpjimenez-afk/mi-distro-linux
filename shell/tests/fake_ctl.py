"""Arranca un Hyprland de mentira y lo controla por una tubería (FIFO).

    python3 fake_ctl.py DIR_RUNTIME RUTA_FIFO &
    echo "open 0xaaa foot"  > RUTA_FIFO
    echo "close 0xaaa"      > RUTA_FIFO
"""
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from fake_hyprland import FakeHyprland  # noqa: E402

runtime, fifo = sys.argv[1], sys.argv[2]
fake = FakeHyprland(runtime).start()
if not os.path.exists(fifo):
    os.mkfifo(fifo)
print("fake hyprland listo, firma:", fake.firma, flush=True)
while True:
    with open(fifo) as f:
        for linea in f:
            partes = linea.split()
            if not partes:
                continue
            if partes[0] == "open":
                fake.abrir(partes[1], partes[2])
            elif partes[0] == "close":
                fake.cerrar(partes[1])
            elif partes[0] == "dump":
                print("despachos:", fake.despachos, flush=True)
            elif partes[0] == "quit":
                sys.exit(0)
    time.sleep(0.05)
