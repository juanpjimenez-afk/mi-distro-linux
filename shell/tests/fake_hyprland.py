"""Hyprland de mentira: imita sus dos sockets para probar el shell sin compositor real.

    socket1 (.socket.sock):  "j/clients", "j/activewindow", "dispatch ..."
    socket2 (.socket2.sock): eventos "nombre>>datos\\n"

Uso desde un test o desde un guion de pruebas:

    fake = FakeHyprland(runtime_dir)
    fake.start()
    os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = fake.firma
    fake.abrir("0xaaa", "foot")          # abre una ventana y emite openwindow/activewindowv2
"""
import json
import os
import socket
import threading


class FakeHyprland:
    def __init__(self, runtime_dir, firma="fakesig"):
        self.firma = firma
        self.dir = os.path.join(runtime_dir, "hypr", firma)
        os.makedirs(self.dir, exist_ok=True)
        self.clientes = []
        self.activa = ""
        self.despachos = []
        self._conexiones = []
        self._socks = []
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- servidor
    def start(self):
        for nombre, destino in ((".socket.sock", self._servir_ordenes), (".socket2.sock", self._servir_eventos)):
            ruta = os.path.join(self.dir, nombre)
            if os.path.exists(ruta):
                os.unlink(ruta)
            s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            s.bind(ruta)
            s.listen(8)
            self._socks.append(s)
            threading.Thread(target=destino, args=(s,), daemon=True).start()
        return self

    def stop(self):
        for s in self._socks:
            try:
                s.close()
            except OSError:
                pass
        for c in self._conexiones:
            try:
                c.close()
            except OSError:
                pass

    def _servir_ordenes(self, srv):
        while True:
            try:
                con, _ = srv.accept()
            except OSError:
                return
            with con:
                orden = con.recv(65536).decode()
                con.sendall(self._responder(orden).encode())

    def _servir_eventos(self, srv):
        while True:
            try:
                con, _ = srv.accept()
            except OSError:
                return
            with self._lock:
                self._conexiones.append(con)

    def _responder(self, orden):
        if orden == "j/clients":
            return json.dumps(self.clientes)
        if orden == "j/activewindow":
            for c in self.clientes:
                if c["address"] == self.activa:
                    return json.dumps(c)
            return "{}"
        if orden.startswith("dispatch "):
            self.despachos.append(orden[len("dispatch "):])
            return "ok"
        return "unknown request"

    # ------------------------------------------------------------------ eventos
    def emitir(self, nombre, datos=""):
        with self._lock:
            for c in list(self._conexiones):
                try:
                    c.sendall(f"{nombre}>>{datos}\n".encode())
                except OSError:
                    self._conexiones.remove(c)

    def abrir(self, direccion, clase, titulo=""):
        self.clientes.append({
            "address": direccion, "mapped": True, "hidden": False,
            "class": clase, "initialClass": clase, "title": titulo or clase,
            "workspace": {"id": 1, "name": "1"}, "focusHistoryID": 0,
        })
        for c in self.clientes:
            if c["address"] != direccion:
                c["focusHistoryID"] += 1
        self.activa = direccion
        self.emitir("openwindow", f"{direccion[2:]},1,{clase},{titulo or clase}")
        self.emitir("activewindowv2", direccion[2:])

    def cerrar(self, direccion):
        self.clientes = [c for c in self.clientes if c["address"] != direccion]
        if self.activa == direccion:
            self.activa = self.clientes[0]["address"] if self.clientes else ""
        self.emitir("closewindow", direccion[2:])
