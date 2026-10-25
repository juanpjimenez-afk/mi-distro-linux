"""Internet y Bluetooth simples: lectura de nmcli y bluetoothctl y órdenes, sin pantalla ni red."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import redes_logica as R  # noqa: E402

REDES = (" :Casa 5G:78:WPA2\n"
         "*:Mi Wifi:92:WPA2 WPA3\n"
         " :Mi Wifi:40:WPA2 WPA3\n"
         " :Cafe\\:Libre:55:\n"
         " ::70:WPA2\n"
         " :Vecino:31:WPA2\n"
         "basura\n")


class TestRedes(unittest.TestCase):
    def test_separar_con_dos_puntos_escapados(self):
        self.assertEqual(R.separar_nmcli("a:b\\:c:d"), ["a", "b:c", "d"])
        self.assertEqual(R.separar_nmcli("x\\\\y:z"), ["x\\y", "z"])
        self.assertEqual(R.separar_nmcli(""), [""])

    def test_lista_ordenada_sin_repetidas_ni_ocultas(self):
        redes = R.parsear_redes(REDES, guardadas=["Casa 5G"])
        self.assertEqual([r.ssid for r in redes], ["Mi Wifi", "Casa 5G", "Cafe:Libre", "Vecino"])
        self.assertTrue(redes[0].conectada)
        self.assertEqual(redes[0].senal, 92)
        self.assertTrue(redes[1].guardada and redes[1].segura)
        self.assertFalse(redes[2].segura)          # «Cafe:Libre» no tiene seguridad: abierta

    def test_senal_rara(self):
        self.assertEqual(R.parsear_redes(" :X:abc:WPA2\n")[0].senal, 0)
        self.assertEqual(R.parsear_redes(" :X:999:WPA2\n")[0].senal, 100)
        self.assertEqual(R.parsear_redes(None), [])

    def test_guardadas_solo_wifi(self):
        self.assertEqual(R.parsear_guardadas("Casa:802-11-wireless\nCable:802-3-ethernet\nVPN:vpn\n"), ["Casa"])

    def test_radio_y_adaptadores(self):
        self.assertTrue(R.wifi_encendido("enabled\n"))
        self.assertFalse(R.wifi_encendido("disabled"))
        self.assertTrue(R.hay_wifi("ethernet:unavailable\nwifi:disconnected\nloopback:unmanaged\n"))
        self.assertFalse(R.hay_wifi("ethernet:connected\nloopback:unmanaged\n"))
        self.assertFalse(R.hay_wifi(""))
        self.assertTrue(R.hay_cable("ethernet:connected\nwifi:unavailable\n"))
        self.assertFalse(R.hay_cable("ethernet:unavailable\n"))

    def test_barras(self):
        self.assertEqual([R.barras(n) for n in (0, 1, 24, 25, 50, 75, 100)], [0, 1, 1, 2, 3, 4, 4])

    def test_la_contrasena_nunca_va_en_la_orden(self):
        orden = R.orden_conectar_nueva("Mi Wifi")
        self.assertIn("--ask", orden)
        self.assertEqual(orden[-1], "Mi Wifi")
        self.assertNotIn("password", orden)
        for o in (R.orden_redes(), R.orden_guardadas(), R.orden_encender(True), R.orden_desconectar("x")):
            self.assertIsInstance(o, list)
            self.assertTrue(all(isinstance(a, str) for a in o))
        self.assertEqual(R.orden_encender(False)[-1], "off")

    def test_ssid_raro_es_un_solo_argumento(self):
        malo = "red; rm -rf /"
        self.assertEqual(R.orden_conectar_guardada(malo)[-1], malo)
        self.assertEqual(len(R.orden_conectar_guardada(malo)), 5)

    def test_errores_en_cristiano(self):
        self.assertIn("contraseña", R.explicar_error_red("Error: Connection activation failed: Secrets were required"))
        self.assertIn("No encuentro", R.explicar_error_red("Error: No network with SSID 'x' found."))
        self.assertIn("No se pudo", R.explicar_error_red("algo raro"))


class TestBluetooth(unittest.TestCase):
    def test_dispositivos(self):
        salida = ("Device 00:11:22:33:44:55 Auriculares JBL\n"
                  "Device 00:11:22:33:44:55 Auriculares JBL\n"
                  "Device AA-BB-CC-DD-EE-FF mal\n"
                  "Device AA:BB:CC:DD:EE:FF\n"
                  "otra cosa\n")
        self.assertEqual(R.parsear_dispositivos(salida),
                         [("00:11:22:33:44:55", "Auriculares JBL"), ("AA:BB:CC:DD:EE:FF", "AA:BB:CC:DD:EE:FF")])

    def test_estado(self):
        self.assertTrue(R.bt_encendido("Controller X\n\tPowered: yes\n\tPairable: yes\n"))
        self.assertFalse(R.bt_encendido("Powered: no"))
        a = R.parsear_info("00:11:22:33:44:55", "JBL", "\tPaired: yes\n\tConnected: no\n")
        self.assertTrue(a.emparejado)
        self.assertFalse(a.conectado)

    def test_nombre_sin_nombre(self):
        self.assertEqual(R.nombre_legible(R.Aparato("A", "AA-BB-CC-DD-EE-FF", False, False)), "Dispositivo sin nombre")
        self.assertEqual(R.nombre_legible(R.Aparato("A", "JBL", False, False)), "JBL")

    def test_ordenes_y_mac(self):
        self.assertEqual(R.orden_bt("devices"), ["bluetoothctl", "devices"])
        self.assertEqual(R.orden_bt("scan", "on", espera=8), ["bluetoothctl", "--timeout", "8", "scan", "on"])
        self.assertTrue(R.mac_valida("00:11:22:33:44:55"))
        for mala in ("", "00:11", "00:11:22:33:44:5G", "x; ls", None):
            self.assertFalse(R.mac_valida(mala))


if __name__ == "__main__":
    unittest.main()
