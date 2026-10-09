"""Conexión directa con Hyprland por sus sockets (sin lanzar `hyprctl` cada vez).

- socket1: peticiones ("j/clients" -> JSON)
- socket2: eventos en vivo (abrir/cerrar ventana, cambio de foco...). Es la razón por la
  que el shell no necesita "sondear" nada: Hyprland nos avisa cuando algo cambia.
"""
import json
import os
import socket
import sys
import threading

from gi.repository import GLib


def _ruta(nombre):
    firma = os.environ.get("HYPRLAND_INSTANCE_SIGNATURE")
    if not firma:
        return None
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    ruta = f"{runtime}/hypr/{firma}/{nombre}"
    return ruta if os.path.exists(ruta) else None


class Hyprland:
    def __init__(self):
        self._cmd = _ruta(".socket.sock")
        self._ev = _ruta(".socket2.sock")
        self._sock = None
        self._buf = b""
        self._watch = None
        self.on_event = None  # callable(nombre, datos)

    @property
    def disponible(self):
        return self._cmd is not None

    # -------------------------------------------------------------- peticiones
    def pedir(self, orden):
        if not self._cmd:
            return ""
        s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        s.settimeout(5.0)
        try:
            s.connect(self._cmd)
            s.sendall(orden.encode())
            partes = []
            while True:
                trozo = s.recv(65536)
                if not trozo:
                    break
                partes.append(trozo)
            return b"".join(partes).decode(errors="replace")
        except OSError:
            return ""
        finally:
            s.close()

    def json(self, orden, defecto=None):
        txt = self.pedir("j/" + orden)
        try:
            return json.loads(txt) if txt else defecto
        except ValueError:
            return defecto

    def despachar(self, *args):
        return self.pedir("dispatch " + " ".join(str(a) for a in args))

    def ventanas(self):
        """Ventanas visibles, ordenadas de la más a la menos usada recientemente."""
        todas = self.json("clients", []) or []
        vis = [c for c in todas if c.get("mapped") and not c.get("hidden")]
        vis.sort(key=lambda c: c.get("focusHistoryID", 99))
        return vis

    def ventana_activa(self):
        a = self.json("activewindow", {}) or {}
        return a.get("address")

    def estado_async(self, callback):
        """Consulta ventanas y ventana activa en un hilo y llama `callback(ventanas, activa)`
        en el hilo principal. Así la interfaz nunca se congela esperando al compositor."""
        def trabajo():
            try:
                ventanas = self.ventanas()
                activa = self.ventana_activa()
            except Exception:  # si falla a mitad, la barra debe seguir: el callback se llama igual
                ventanas, activa = [], None
            GLib.idle_add(lambda: callback(ventanas, activa) and False)
        threading.Thread(target=trabajo, daemon=True).start()

    # ----------------------------------------------------------------- eventos
    def escuchar(self, callback):
        self.on_event = callback
        if not self._ev:
            return False
        try:
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.connect(self._ev)
            s.setblocking(False)
        except OSError:
            return False
        self._sock = s
        self._watch = GLib.io_add_watch(
            s.fileno(), GLib.PRIORITY_DEFAULT, GLib.IO_IN | GLib.IO_HUP | GLib.IO_ERR, self._leer
        )
        return True

    def _leer(self, _fd, cond):
        if cond & (GLib.IO_HUP | GLib.IO_ERR):
            return False  # Hyprland se cerró
        try:
            datos = self._sock.recv(65536)
        except BlockingIOError:
            return True
        except OSError:
            return False
        if not datos:
            return False
        self._buf += datos
        *lineas, self._buf = self._buf.split(b"\n")
        for linea in lineas:
            nombre, _, resto = linea.decode(errors="replace").partition(">>")
            if self.on_event:
                try:
                    self.on_event(nombre, resto)
                except Exception as e:  # GLib quitaria la fuente si el callback lanza
                    print(f"infinity-shell: error al procesar el evento {nombre}: {e}", file=sys.stderr)
        return True
