"""Internet y Bluetooth en una ventana sencilla (infinity-redes): sin terminales ni órdenes que aprender.

GTK 4. Lo que se puede probar sin pantalla (leer lo que contesta nmcli/bluetoothctl, armar las órdenes) está en
redes_logica.py; aquí solo se dibuja. Cada orden corre en un hilo y la ventana se actualiza al terminar: nunca se congela.

    python3 -m infinity_shell.redes_app [--bluetooth]     (lo lanza el comando infinity-redes)

  * Internet: interruptor de Wi-Fi, lista de redes (la conectada arriba), clic en una → «Conectar» (pide la contraseña
    solo si la red la necesita y no está guardada) o «Desconectar».
  * Bluetooth (solo si el equipo tiene adaptador): interruptor, «Buscar dispositivos» y, por cada uno, «Conectar».
    Para emparejar, pon el dispositivo en modo de emparejamiento (suele ser mantener pulsado su botón).
  * La contraseña de una red va por la entrada estándar de nmcli, nunca en una orden ni en el registro.
"""
import argparse
import os
import subprocess
import sys
import threading

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import bluetooth_logica as BT  # noqa: E402
from . import redes_logica as R  # noqa: E402

APP_ID = "org.infinity.Redes"
SEGUNDOS_BUSCAR = 8

ESTILO = """
window.redes-ventana { background-color: rgb(24, 27, 48); color: #e9ebf7; }
window.redes-ventana label { color: #e9ebf7; }
window.redes-ventana button {
  background-image: none; background-color: rgba(255, 255, 255, 0.09); color: #e9ebf7; text-shadow: none; box-shadow: none;
  border: 1px solid rgba(255, 255, 255, 0.14); border-radius: 10px; padding: 5px 14px;
}
window.redes-ventana button:hover { background-color: rgba(255, 255, 255, 0.17); }
window.redes-ventana button label { color: inherit; }
window.redes-ventana entry, window.redes-ventana text {
  background-color: rgba(255, 255, 255, 0.08); color: #ffffff; caret-color: #b9a8ff; border-radius: 10px;
}
window.redes-ventana separator { background-color: rgba(255, 255, 255, 0.12); min-height: 1px; }
window.redes-ventana label.redes-titulo { font-size: 17px; font-weight: 700; }
window.redes-ventana label.redes-estado { color: rgba(233, 235, 247, 0.75); }
window.redes-ventana label.redes-error { color: #ffb4ab; }
window.redes-ventana label.redes-ok { color: #8be9a8; }
window.redes-ventana list.redes-lista { background: transparent; }
window.redes-ventana list.redes-lista row { border-radius: 10px; padding: 4px 6px; margin: 2px 0; }
window.redes-ventana list.redes-lista row:hover { background-color: rgba(255, 255, 255, 0.07); }
window.redes-ventana list.redes-lista row:selected { background-color: rgba(185, 168, 255, 0.24); }
window.redes-ventana button.suggested-action { background-color: #b9a8ff; color: #1a1636; border-color: #b9a8ff; font-weight: 600; }
window.redes-ventana button.suggested-action label { color: #1a1636; }
window.redes-ventana .redes-panel { background-color: rgba(255, 255, 255, 0.05); border-radius: 12px; padding: 10px; }
"""

_estilo_cargado = False


def _cargar_estilo(pantalla):
    global _estilo_cargado
    if _estilo_cargado or pantalla is None:
        return
    prov = Gtk.CssProvider()
    if hasattr(prov, "load_from_string"):
        prov.load_from_string(ESTILO)
    else:
        prov.load_from_data(ESTILO.encode("utf-8"))
    Gtk.StyleContext.add_provider_for_display(pantalla, prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
    _estilo_cargado = True


def correr_real(argv, entrada=None, timeout=25):
    """Ejecuta una orden (lista, sin shell). Devuelve (código, salida). Se sustituye en las pruebas."""
    try:
        r = subprocess.run(argv, input=entrada, capture_output=True, text=True, errors="replace", timeout=timeout,
                           stdin=None if entrada is not None else subprocess.DEVNULL)
        return r.returncode, ((r.stdout or "") + (r.stderr or "")).strip()
    except (OSError, subprocess.SubprocessError) as e:
        return 127, str(e)


def _etiqueta(texto="", *clases, **kw):
    e = Gtk.Label(label=texto, xalign=0, wrap=True, **kw)
    for c in clases:
        e.add_css_class(c)
    return e


def _barras_texto(senal):
    n = R.barras(senal)
    return "▂▄▆█"[:n].ljust(4, "·") if n else "····"


class VentanaRedes(Gtk.ApplicationWindow):
    def __init__(self, app, correr=correr_real, ver_bluetooth=False):
        super().__init__(application=app, title="Internet y Bluetooth", default_width=460, default_height=640)
        self.add_css_class("redes-ventana")
        _cargar_estilo(self.get_display())
        self._correr = correr
        self._redes = []
        self._red = None            # la red elegida en la lista
        self._aparatos = []
        self._ocupado = set()
        self._ajustando = False
        self._cable = False

        raiz = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        for lado in ("start", "end", "top", "bottom"):
            getattr(raiz, f"set_margin_{lado}")(18)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(raiz)
        self.set_child(scroll)

        # ---- Internet
        cab = Gtk.Box(spacing=10)
        cab.append(_etiqueta("Internet", "redes-titulo", hexpand=True))
        self.interruptor_wifi = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Encender o apagar el Wi-Fi")
        self.interruptor_wifi.connect("state-set", self._wifi_cambio)
        self.etiqueta_wifi = _etiqueta("Wi-Fi", valign=Gtk.Align.CENTER)
        cab.append(self.etiqueta_wifi)
        cab.append(self.interruptor_wifi)
        raiz.append(cab)
        self.estado_red = _etiqueta("Comprobando…", "redes-estado")
        raiz.append(self.estado_red)
        self.lista = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.lista.add_css_class("redes-lista")
        self.lista.connect("row-selected", self._red_elegida)
        raiz.append(self.lista)
        self.panel_red = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.panel_red.add_css_class("redes-panel")
        self.panel_titulo = _etiqueta("", "redes-titulo")
        self.clave = Gtk.PasswordEntry(placeholder_text="Contraseña del Wi-Fi", show_peek_icon=True)
        self.clave.connect("activate", lambda *_: self._conectar())
        botones = Gtk.Box(spacing=8)
        self.boton_conectar = Gtk.Button(label="Conectar")
        self.boton_conectar.add_css_class("suggested-action")
        self.boton_conectar.connect("clicked", lambda *_: self._conectar())
        self.boton_desconectar = Gtk.Button(label="Desconectar")
        self.boton_desconectar.connect("clicked", lambda *_: self._desconectar())
        botones.append(self.boton_conectar)
        botones.append(self.boton_desconectar)
        for w in (self.panel_titulo, self.clave, botones):
            self.panel_red.append(w)
        self.panel_red.set_visible(False)
        raiz.append(self.panel_red)
        self.mensaje_red = _etiqueta("", "redes-estado")
        raiz.append(self.mensaje_red)
        self.boton_actualizar = Gtk.Button(label="Actualizar lista", halign=Gtk.Align.START)
        self.boton_actualizar.connect("clicked", lambda *_: self.actualizar_red())
        raiz.append(self.boton_actualizar)

        raiz.append(Gtk.Separator())

        # ---- Bluetooth
        cab2 = Gtk.Box(spacing=10)
        cab2.append(_etiqueta("Bluetooth", "redes-titulo", hexpand=True))
        self.interruptor_bt = Gtk.Switch(valign=Gtk.Align.CENTER, tooltip_text="Encender o apagar el Bluetooth")
        self.interruptor_bt.connect("state-set", self._bt_cambio)
        cab2.append(self.interruptor_bt)
        self.cab_bt = cab2
        raiz.append(cab2)
        self.estado_bt = _etiqueta("", "redes-estado")
        raiz.append(self.estado_bt)
        self.lista_bt = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self.lista_bt.add_css_class("redes-lista")
        raiz.append(self.lista_bt)
        self.boton_buscar = Gtk.Button(label="Buscar dispositivos", halign=Gtk.Align.START)
        self.boton_buscar.connect("clicked", lambda *_: self.buscar_bluetooth())
        raiz.append(self.boton_buscar)
        self.nota_bt = _etiqueta("Para emparejar, pon el dispositivo en modo de emparejamiento (suele ser mantener "
                                 "pulsado su botón) y pulsa «Buscar dispositivos».", "redes-estado")
        raiz.append(self.nota_bt)

        self.actualizar_red()
        self.actualizar_bluetooth()
        if ver_bluetooth:
            self.buscar_bluetooth()

    # ------------------------------------------------------------------ hilos
    def _en_hilo(self, argv, listo, entrada=None, timeout=25):
        def trabajo():
            codigo, salida = self._correr(argv, entrada, timeout)
            GLib.idle_add(lambda: (listo(codigo, salida), False)[1])
        threading.Thread(target=trabajo, daemon=True).start()

    def _decir(self, etiqueta, texto, clase=None):
        etiqueta.set_text(texto)
        for c in ("redes-ok", "redes-error"):
            etiqueta.remove_css_class(c)
        if clase:
            etiqueta.add_css_class(clase)

    # ------------------------------------------------------------------ Internet
    def actualizar_red(self):
        self._en_hilo(R.orden_dispositivos(), self._dispositivos_listos)

    def _dispositivos_listos(self, _c, salida):
        tiene_wifi = R.hay_wifi(salida)
        self._cable = R.hay_cable(salida)
        self.interruptor_wifi.set_visible(tiene_wifi)
        self.etiqueta_wifi.set_visible(tiene_wifi)
        self.boton_actualizar.set_visible(tiene_wifi)
        if not tiene_wifi:
            self._redes = []
            self._pintar_redes()
            self._decir(self.estado_red, "Conectado por cable." if self._cable else
                        "Este equipo no tiene Wi-Fi. Conecta un cable de red para tener Internet.")
            return
        self._en_hilo(R.orden_radio(), self._radio_listo)

    def _radio_listo(self, _c, salida):
        encendido = R.wifi_encendido(salida)
        self._ajustando = True
        self.interruptor_wifi.set_active(encendido)
        self._ajustando = False
        if not encendido:
            self._redes = []
            self._pintar_redes()
            self._decir(self.estado_red, "El Wi-Fi está apagado. Enciéndelo con el interruptor de arriba.")
            return
        self._decir(self.estado_red, "Buscando redes…")
        self._en_hilo(R.orden_guardadas(), self._guardadas_listas)

    def _guardadas_listas(self, _c, salida):
        guardadas = R.parsear_guardadas(salida)
        self._en_hilo(R.orden_redes(), lambda c, s: self._redes_listas(c, s, guardadas))

    def _redes_listas(self, _codigo, salida, guardadas):
        self._redes = R.parsear_redes(salida, guardadas)
        activa = next((r for r in self._redes if r.conectada), None)
        if activa:
            self._decir(self.estado_red, f"Conectado a «{activa.ssid}».", "redes-ok")
        elif self._cable:
            self._decir(self.estado_red, "Conectado por cable.", "redes-ok")
        elif self._redes:
            self._decir(self.estado_red, "Sin conexión. Elige una red de la lista.")
        else:
            self._decir(self.estado_red, "No veo redes. Acércate al router y pulsa «Actualizar lista».")
        self._pintar_redes()

    def _pintar_redes(self):
        previa = self._red.ssid if self._red else None
        while (fila := self.lista.get_first_child()) is not None:
            self.lista.remove(fila)
        elegir = None
        for r in self._redes:
            fila = Gtk.ListBoxRow()
            fila.red = r
            caja = Gtk.Box(spacing=10)
            caja.append(Gtk.Label(label=_barras_texto(r.senal)))
            caja.append(_etiqueta(r.ssid, hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            extra = "Conectada" if r.conectada else "Guardada" if r.guardada else ""
            if r.segura and not extra:
                extra = "🔒"
            caja.append(_etiqueta(extra, "redes-estado"))
            fila.set_child(caja)
            self.lista.append(fila)
            if r.ssid == previa:
                elegir = fila
        if elegir is not None:
            self.lista.select_row(elegir)
        else:
            self._red = None
            self.panel_red.set_visible(False)

    def _red_elegida(self, _lista, fila):
        if fila is None:
            return
        self._red = fila.red
        r = self._red
        self.panel_red.set_visible(True)
        self.panel_titulo.set_text(r.ssid)
        necesita = r.segura and not r.guardada and not r.conectada
        self.clave.set_visible(necesita)
        self.clave.set_text("")
        self.boton_conectar.set_visible(not r.conectada)
        self.boton_desconectar.set_visible(r.conectada)
        self._decir(self.mensaje_red, "")
        if necesita:
            self.clave.grab_focus()

    def _conectar(self):
        r = self._red
        if r is None or "red" in self._ocupado:
            return
        entrada = None
        if r.guardada:
            orden = R.orden_conectar_guardada(r.ssid)
        else:
            orden = R.orden_conectar_nueva(r.ssid)
            if r.segura:
                clave = self.clave.get_text()
                if len(clave) < 8:
                    self._decir(self.mensaje_red, "La contraseña del Wi-Fi tiene al menos 8 caracteres.", "redes-error")
                    return
                entrada = clave + "\n"
        self._ocupado.add("red")
        self._decir(self.mensaje_red, f"Conectando a «{r.ssid}»…")

        def listo(codigo, salida):
            self._ocupado.discard("red")
            if codigo == 0:
                self._decir(self.mensaje_red, "¡Conectado!", "redes-ok")
                self.clave.set_text("")
                self.actualizar_red()
            else:
                self._decir(self.mensaje_red, R.explicar_error_red(salida), "redes-error")
        self._en_hilo(orden, listo, entrada=entrada, timeout=45)

    def _desconectar(self):
        r = self._red
        if r is None:
            return
        self._decir(self.mensaje_red, "Desconectando…")
        self._en_hilo(R.orden_desconectar(r.ssid), lambda c, s: (self._decir(self.mensaje_red, ""), self.actualizar_red()))

    def _wifi_cambio(self, _sw, estado):
        if self._ajustando:
            return False
        self._en_hilo(R.orden_encender(estado), lambda c, s: self.actualizar_red())
        return False

    # ------------------------------------------------------------------ Bluetooth
    def actualizar_bluetooth(self):
        hay = BT.hay_adaptador()
        for w in (self.cab_bt, self.lista_bt, self.boton_buscar, self.nota_bt):
            w.set_visible(hay)
        if not hay:
            self._decir(self.estado_bt, "Este equipo no tiene Bluetooth.")
            return
        self._decir(self.estado_bt, "")
        self._en_hilo(R.orden_bt("show"), self._bt_estado)

    def _bt_estado(self, _c, salida):
        encendido = R.bt_encendido(salida)
        self._ajustando = True
        self.interruptor_bt.set_active(encendido)
        self._ajustando = False
        self.boton_buscar.set_sensitive(encendido)
        if not encendido:
            self._decir(self.estado_bt, "El Bluetooth está apagado. Enciéndelo con el interruptor.")
            self._aparatos = []
            self._pintar_aparatos()
            return
        self._decir(self.estado_bt, "")
        self._leer_aparatos()

    def _bt_cambio(self, _sw, estado):
        if self._ajustando:
            return False
        self._en_hilo(R.orden_bt("power", "on" if estado else "off"), lambda c, s: self.actualizar_bluetooth())
        return False

    def _leer_aparatos(self):
        def listo(_c, salida):
            nombres = R.parsear_dispositivos(salida)
            self._aparatos = []
            pendientes = [len(nombres)]
            if not nombres:
                self._pintar_aparatos()
                return
            for mac, nombre in nombres:
                def info(c, s, mac=mac, nombre=nombre):
                    self._aparatos.append(R.parsear_info(mac, nombre, s))
                    pendientes[0] -= 1
                    if pendientes[0] == 0:
                        self._aparatos.sort(key=lambda a: (not a.conectado, not a.emparejado, R.nombre_legible(a).casefold()))
                        self._pintar_aparatos()
                self._en_hilo(R.orden_bt("info", mac), info)
        self._en_hilo(R.orden_bt("devices"), listo)

    def buscar_bluetooth(self):
        if "bt" in self._ocupado:
            return
        self._ocupado.add("bt")
        self.boton_buscar.set_sensitive(False)
        self._decir(self.estado_bt, f"Buscando dispositivos ({SEGUNDOS_BUSCAR} s)…")

        def listo(_c, _s):
            self._ocupado.discard("bt")
            self.boton_buscar.set_sensitive(True)
            self._decir(self.estado_bt, "")
            self._leer_aparatos()
        self._en_hilo(R.orden_bt("scan", "on", espera=SEGUNDOS_BUSCAR), listo, timeout=SEGUNDOS_BUSCAR + 10)

    def _pintar_aparatos(self):
        while (fila := self.lista_bt.get_first_child()) is not None:
            self.lista_bt.remove(fila)
        if not self._aparatos and self.interruptor_bt.get_active():
            self._decir(self.estado_bt, "No hay dispositivos a la vista. Pulsa «Buscar dispositivos».")
        for a in self._aparatos:
            fila = Gtk.ListBoxRow(activatable=False)
            caja = Gtk.Box(spacing=10)
            caja.append(_etiqueta(R.nombre_legible(a), hexpand=True, ellipsize=Pango.EllipsizeMode.END))
            if a.conectado:
                caja.append(_etiqueta("Conectado", "redes-ok"))
                b = Gtk.Button(label="Desconectar")
                b.connect("clicked", lambda *_, a=a: self._bt_accion(a, desconectar=True))
            else:
                b = Gtk.Button(label="Conectar")
                b.add_css_class("suggested-action")
                b.connect("clicked", lambda *_, a=a: self._bt_accion(a))
            caja.append(b)
            fila.set_child(caja)
            self.lista_bt.append(fila)

    def _bt_accion(self, a, desconectar=False):
        if not R.mac_valida(a.mac) or "btacc" in self._ocupado:
            return
        self._ocupado.add("btacc")
        nombre = R.nombre_legible(a)
        self._decir(self.estado_bt, f"{'Desconectando' if desconectar else 'Conectando con'} «{nombre}»…")
        pasos = [R.orden_bt("disconnect", a.mac)] if desconectar else [
            R.orden_bt("pair", a.mac), R.orden_bt("trust", a.mac), R.orden_bt("connect", a.mac)]

        def seguir(i=0, ultimo=(0, "")):
            if i < len(pasos):
                # «pair» falla si ya estaba emparejado: no corta la cadena; lo que cuenta es el último paso
                self._en_hilo(pasos[i], lambda c, s: seguir(i + 1, (c, s)), timeout=30)
                return
            self._ocupado.discard("btacc")
            if ultimo[0] == 0 and "failed" not in ultimo[1].lower():
                self._decir(self.estado_bt, "Listo." if desconectar else f"¡Conectado con «{nombre}»!", "redes-ok")
            else:
                self._decir(self.estado_bt, "No se pudo conectar. Pon el dispositivo en modo de emparejamiento y "
                                            "vuelve a intentarlo.", "redes-error")
            self._leer_aparatos()
        seguir()


class AppRedes(Gtk.Application):
    def __init__(self, ver_bluetooth=False):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.ver_bluetooth = ver_bluetooth

    def do_activate(self):
        ventana = self.get_active_window()
        if ventana is None:
            ajustes = Gtk.Settings.get_default()
            if ajustes is not None:
                ajustes.set_property("gtk-application-prefer-dark-theme", True)
            ventana = VentanaRedes(self, ver_bluetooth=self.ver_bluetooth)
        ventana.present()


def main(argv=None):
    argv = list(sys.argv if argv is None else argv)
    p = argparse.ArgumentParser(prog="infinity-redes", description="Internet y Bluetooth, sin complicaciones.")
    p.add_argument("--bluetooth", action="store_true", help="empieza buscando dispositivos Bluetooth")
    args, resto = p.parse_known_args(argv[1:])
    return AppRedes(args.bluetooth).run([argv[0]] + resto)


if __name__ == "__main__":
    sys.exit(main())
