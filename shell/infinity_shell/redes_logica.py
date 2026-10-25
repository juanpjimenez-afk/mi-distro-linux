"""Internet (Wi-Fi) y Bluetooth en una ventana sencilla: la parte sin pantalla (órdenes, lectura de lo que contestan).

Todo pasa por `nmcli` y `bluetoothctl`, que ya vienen en el sistema. Aquí solo se arman las órdenes (siempre listas,
nunca texto con shell) y se interpreta lo que devuelven; la ventana (redes_app.py) las lanza en un hilo para no
congelarse. La contraseña de una red nunca va en una orden: se pasa por la entrada estándar (`nmcli --ask`).
"""
import re
from collections import namedtuple

Red = namedtuple("Red", "ssid senal segura conectada guardada")
Aparato = namedtuple("Aparato", "mac nombre conectado emparejado")

RE_MAC = re.compile(r"[0-9A-Fa-f]{2}(:[0-9A-Fa-f]{2}){5}")


def separar_nmcli(linea):
    """Un campo por «:» en la salida `-t` de nmcli, donde «\\:» es un «:» dentro del campo y «\\\\» una barra."""
    campos, actual, i = [], "", 0
    while i < len(linea):
        c = linea[i]
        if c == "\\" and i + 1 < len(linea):
            actual += linea[i + 1]
            i += 2
            continue
        if c == ":":
            campos.append(actual)
            actual = ""
        else:
            actual += c
        i += 1
    campos.append(actual)
    return campos


def parsear_redes(salida, guardadas=()):
    """De `nmcli -t -f IN-USE,SSID,SIGNAL,SECURITY dev wifi list`: redes sin repetir (gana la de más señal), la
    conectada primero y luego por señal. Las redes sin nombre (ocultas) no se enseñan."""
    mejores = {}
    for linea in (salida or "").splitlines():
        campos = separar_nmcli(linea)
        if len(campos) < 4:
            continue
        en_uso, ssid, senal, seguridad = campos[0], campos[1], campos[2], campos[3]
        if not ssid.strip():
            continue
        try:
            senal = max(0, min(100, int(senal)))
        except ValueError:
            senal = 0
        r = Red(ssid, senal, seguridad.strip() not in ("", "--"), en_uso.strip() == "*", ssid in set(guardadas))
        anterior = mejores.get(ssid)
        if anterior is None or r.conectada or (not anterior.conectada and r.senal > anterior.senal):
            mejores[ssid] = r
    return sorted(mejores.values(), key=lambda r: (not r.conectada, -r.senal, r.ssid.casefold()))


def parsear_guardadas(salida):
    """Nombres de las conexiones Wi-Fi guardadas, de `nmcli -t -f NAME,TYPE connection show`."""
    nombres = []
    for linea in (salida or "").splitlines():
        campos = separar_nmcli(linea)
        if len(campos) >= 2 and campos[1] in ("802-11-wireless", "wifi"):
            nombres.append(campos[0])
    return nombres


def wifi_encendido(salida):
    return (salida or "").strip().lower() == "enabled"


def hay_wifi(salida):
    """¿Hay algún adaptador Wi-Fi? (`nmcli -t -f TYPE,STATE device`: líneas «wifi:connected», «ethernet:unavailable»…)"""
    return any(separar_nmcli(l)[0] == "wifi" for l in (salida or "").splitlines())


def hay_cable(salida):
    """¿Hay un cable conectado? (`nmcli -t -f TYPE,STATE device`)"""
    return any(separar_nmcli(l)[:2] == ["ethernet", "connected"] for l in (salida or "").splitlines())


def barras(senal):
    """De 1 a 4 barras según la señal (para el dibujo)."""
    return 0 if senal <= 0 else 1 if senal < 25 else 2 if senal < 50 else 3 if senal < 75 else 4


# ---- órdenes de red
def orden_redes():
    return ["nmcli", "-t", "-f", "IN-USE,SSID,SIGNAL,SECURITY", "device", "wifi", "list", "--rescan", "auto"]


def orden_guardadas():
    return ["nmcli", "-t", "-f", "NAME,TYPE", "connection", "show"]


def orden_radio():
    return ["nmcli", "radio", "wifi"]


def orden_encender(si):
    return ["nmcli", "radio", "wifi", "on" if si else "off"]


def orden_dispositivos():
    return ["nmcli", "-t", "-f", "TYPE,STATE", "device"]


def orden_conectar_guardada(ssid):
    return ["nmcli", "connection", "up", "id", ssid]


def orden_conectar_nueva(ssid):
    """La contraseña (si la red la pide) se manda por la entrada estándar: así no queda en la lista de procesos."""
    return ["nmcli", "--ask", "device", "wifi", "connect", ssid]


def orden_desconectar(ssid):
    return ["nmcli", "connection", "down", "id", ssid]


def explicar_error_red(texto):
    t = (texto or "").lower()
    if "secrets were required" in t or "password" in t and "invalid" in t or "802.1x" in t:
        return "La contraseña no es correcta (o la red no la aceptó). Prueba otra vez."
    if "no network with ssid" in t or "not found" in t:
        return "No encuentro esa red. Acércate al router y actualiza la lista."
    if "timeout" in t or "timed out" in t:
        return "La red tardó demasiado en responder. Prueba otra vez."
    if "not authorized" in t or "permission" in t:
        return "No tienes permiso para cambiar la red."
    return "No se pudo conectar. Revisa la contraseña y vuelve a intentarlo."


# ---- Bluetooth
def parsear_dispositivos(salida):
    """De `bluetoothctl devices`: líneas «Device AA:BB:CC:DD:EE:FF Nombre». Sin duplicados."""
    vistos, aparatos = set(), []
    for linea in (salida or "").splitlines():
        partes = linea.strip().split(None, 2)
        if len(partes) >= 2 and partes[0] == "Device" and RE_MAC.fullmatch(partes[1]) and partes[1] not in vistos:
            vistos.add(partes[1])
            nombre = partes[2].strip() if len(partes) > 2 else partes[1]
            aparatos.append((partes[1], nombre))
    return aparatos


def _si(salida, clave):
    for linea in (salida or "").splitlines():
        k, _, v = linea.strip().partition(":")
        if k.strip() == clave:
            return v.strip().lower() == "yes"
    return False


def bt_encendido(salida_show):
    return _si(salida_show, "Powered")


def parsear_info(mac, nombre, salida_info):
    return Aparato(mac, nombre, _si(salida_info, "Connected"), _si(salida_info, "Paired"))


def nombre_legible(a):
    """Un nombre que no sea la dirección MAC con guiones («AA-BB-...» es lo que bluez inventa sin nombre)."""
    return "Dispositivo sin nombre" if re.fullmatch(r"([0-9A-Fa-f]{2}-){5}[0-9A-Fa-f]{2}", a.nombre) else a.nombre


def orden_bt(*args, espera=None):
    base = ["bluetoothctl"]
    if espera:
        base += ["--timeout", str(int(espera))]
    return base + list(args)


def mac_valida(mac):
    return bool(RE_MAC.fullmatch(mac or ""))
