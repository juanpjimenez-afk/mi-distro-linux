"""Controles de ventana (−, □, ×): controles_logica.py, controles.py y su conexión con el resto del shell.

Tres capas de pruebas:
  1. Sin GTK ni pantalla: la geometría (qué ventanas llevan pastilla, dónde va), las órdenes exactas que se
     mandan a Hyprland para cada botón y para restaurar desde el dock, y los ficheros del sistema (hyprland.conf,
     ajustes de GTK, esquema de GSettings, CSS).
  2. Con PyGObject pero sin pantalla: hypr.py contra el Hyprland de mentira (fake_hyprland.py).
  3. Con GTK y pantalla (xvfb-run): las pastillas de verdad, con un doble de gtk4-layer-shell (que apunta lo que
     se le pide). Sin compositor Wayland no se puede comprobar la posición REAL de las superficies: eso solo se
     ve en una VM con Hyprland.
"""
import ast
import json
import os
import re
import sys
import tempfile
import time
import types
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
RAIZ = os.path.join(SHELL, "..")
PAQUETE = os.path.join(SHELL, "infinity_shell")
sys.path.insert(0, SHELL)
sys.path.insert(0, HERE)

from infinity_shell import controles_logica as L  # noqa: E402  (no necesita GTK)

try:
    import gi
    from gi.repository import GLib
except ImportError:  # sin PyGObject solo corren las pruebas puras
    gi = None

PANTALLA = bool(os.environ.get("DISPLAY"))
HYPR_CONF = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "hypr", "hyprland.conf")
AJUSTES_CONF = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "hypr", "infinity-ajustes.conf")
GTK3_INI = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "gtk-3.0", "settings.ini")
GTK4_INI = os.path.join(RAIZ, "overlay", "etc", "skel", ".config", "gtk-4.0", "settings.ini")
OVERRIDE = os.path.join(RAIZ, "overlay", "usr", "share", "glib-2.0", "schemas", "90_infinity.gschema.override")
CSS = os.path.join(PAQUETE, "style.css")


def leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read()


# ----------------------------------------------------------------------------- datos de ejemplo
def cliente(direccion, x, y, w, h, *, ws=1, monitor=0, clase="foot", **extra):
    c = {"address": direccion, "mapped": True, "hidden": False, "at": [x, y], "size": [w, h],
         "workspace": {"id": ws, "name": str(ws)}, "monitor": monitor, "class": clase, "initialClass": clase,
         "title": clase, "floating": False, "fullscreen": 0, "fullscreenClient": 0, "xwayland": False,
         "focusHistoryID": 0}
    c.update(extra)
    return c


def monitor(ident=0, x=0, y=0, w=1920, h=1080, *, activo=1, nombre="HDMI-A-1", escala=1.0, transform=0):
    return {"id": ident, "name": nombre, "x": x, "y": y, "width": w, "height": h, "scale": escala,
            "transform": transform, "activeWorkspace": {"id": activo, "name": str(activo)},
            "specialWorkspace": {"id": 0, "name": ""}, "reserved": [0, 0, 0, 74], "focused": ident == 0}


MON = monitor()
# Mosaico real con gaps_in = 6 y gaps_out = 34, 14, 14, 14 en 1920x1080 (el dock reserva abajo)
A = cliente("0xaaa", 14, 34, 934, 960)
B = cliente("0xbbb", 954, 34, 952, 470, clase="thunar")
C = cliente("0xccc", 954, 510, 952, 484, clase="org.gnome.Epiphany")


class HyprFalso:
    """Lo mínimo de hypr.Hyprland que usan los controles, apuntando las órdenes que se despachan."""

    def __init__(self, clientes=(), monitores=(MON,), activo=None, eventos=True):
        self.disponible = True
        self.eventos = eventos
        self.ordenes = []
        self.on_event = None
        self.datos = {"clients": list(clientes), "monitors": list(monitores),
                      "activeworkspace": activo if activo is not None else {"id": 1, "name": "1"}}

    def json(self, orden, defecto=None):
        return json.loads(json.dumps(self.datos.get(orden, defecto)))

    def despachar(self, *args):
        self.ordenes.append(" ".join(str(a) for a in args))
        return "ok"

    def escuchar(self, callback):
        self.on_event = callback
        return self.eventos


# ======================================================================= 1. sin GTK
class TestLectura(unittest.TestCase):
    def test_modo_pantalla_completa(self):
        f = L.modo_pantalla_completa
        self.assertEqual(f({"fullscreen": 0}), 0)
        self.assertEqual(f({"fullscreen": 1}), 1)
        self.assertEqual(f({"fullscreen": 2}), 2)
        self.assertEqual(f({"fullscreen": True}), 2)  # versiones antiguas: booleano = pantalla completa real
        self.assertEqual(f({"fullscreen": False}), 0)
        self.assertEqual(f({}), 0)
        self.assertEqual(f({"fullscreen": None}), 0)
        self.assertEqual(f({"fullscreen": "x"}), 0)

    def test_esta_maximizada_solo_con_el_modo_1(self):
        self.assertTrue(L.esta_maximizada({"fullscreen": 1}))
        self.assertFalse(L.esta_maximizada({"fullscreen": 0}))
        self.assertFalse(L.esta_maximizada({"fullscreen": 2}))

    def test_es_minimizada_por_el_nombre_del_espacio(self):
        def en(nombre, ident=-98):
            return {"workspace": {"id": ident, "name": nombre}}
        self.assertTrue(L.es_minimizada(en("special:minimizadas")))
        self.assertFalse(L.es_minimizada(en("special:otro")))
        self.assertFalse(L.es_minimizada(en("1", 1)))
        self.assertFalse(L.es_minimizada({}))
        self.assertFalse(L.es_minimizada({"workspace": None}))

    def test_rect_monitor_aplica_escala_y_giro(self):
        self.assertEqual(L.rect_monitor(monitor(x=1920, y=0, w=3840, h=2160, escala=2.0)), (1920, 0, 1920, 1080))
        self.assertEqual(L.rect_monitor(monitor(w=1080, h=1920, transform=1)), (0, 0, 1920, 1080))  # girado 90°
        self.assertEqual(L.rect_monitor(monitor(w=1366, h=768, escala=1.0)), (0, 0, 1366, 768))
        self.assertEqual(L.rect_monitor({}), (0, 0, 0, 0))  # datos incompletos: no revienta


class TestQueVentanasLlevanPastilla(unittest.TestCase):
    def llevan(self, c, m=MON, **kw):
        return L.lleva_pastilla(c, m, **kw)

    def test_una_ventana_normal_si(self):
        self.assertTrue(self.llevan(A))

    def test_panki_no_porque_trae_sus_botones(self):
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 460, 640, clase="org.infinity.Panki")))
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 460, 640, clase="ORG.INFINITY.PANKI")))
        # también por la clase inicial, si la actual cambió
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 460, 640, clase="otra", initialClass="org.infinity.Panki")))

    def test_minimizada_o_en_espacio_especial_no(self):
        c = cliente("0x1", 100, 100, 600, 400, ws=-98)
        c["workspace"]["name"] = "special:minimizadas"
        self.assertFalse(self.llevan(c))
        c["workspace"]["name"] = "special:otro"
        self.assertFalse(self.llevan(c))

    def test_minimizada_se_detecta_por_el_nombre_aunque_el_id_coincida(self):
        c = cliente("0x1", 100, 100, 600, 400, ws=1)  # mismo id que el espacio visible del monitor
        c["workspace"]["name"] = "special:minimizadas"
        self.assertFalse(self.llevan(c))

    def test_solo_el_espacio_de_trabajo_activo_de_cada_monitor(self):
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 600, 400, ws=2)))  # el monitor muestra el 1
        self.assertTrue(self.llevan(cliente("0x1", 100, 100, 600, 400, ws=2), monitor(activo=2)))

    def test_sin_monitor_conocido_no(self):
        self.assertFalse(self.llevan(A, None))

    def test_pantalla_completa_real_no_pero_maximizada_si(self):
        self.assertFalse(self.llevan(cliente("0x1", 0, 0, 1920, 1080, fullscreen=2)))
        self.assertFalse(self.llevan(cliente("0x1", 0, 0, 1920, 1080, fullscreen=True)))
        self.assertTrue(self.llevan(cliente("0x1", 14, 34, 1892, 970, fullscreen=1)))

    def test_diminutas_no(self):
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, L.MIN_ANCHO - 1, 400)))
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 600, L.MIN_ALTO - 1)))
        self.assertTrue(self.llevan(cliente("0x1", 100, 100, L.MIN_ANCHO, L.MIN_ALTO)))

    def test_sin_mapear_oculta_o_sin_clase_no(self):
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 600, 400, mapped=False)))
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 600, 400, hidden=True)))
        self.assertFalse(self.llevan(cliente("0x1", 100, 100, 600, 400, clase="", initialClass="")))

    def test_la_lista_de_exclusion_es_ajustable(self):
        self.assertFalse(self.llevan(A, excluidas=("foot",)))
        self.assertTrue(self.llevan(A, excluidas=()))

    def test_ventana_xwayland_como_juegos_si(self):
        juegos = cliente("0x1", 500, 300, 800, 600, clase="Infinity-juegos", xwayland=True, floating=True)
        self.assertTrue(self.llevan(juegos))


class TestDondeVa(unittest.TestCase):
    def test_constantes_coherentes(self):
        self.assertEqual(L.ANCHO, 3 * L.BOTON + 2 * L.ESPACIO + 2 * L.RELLENO + 2)
        self.assertEqual((L.ANCHO, L.ALTO), (78, 26))
        self.assertEqual(L.ALTO, L.BOTON + 2 * 2 + 2)  # botón + 2 px de relleno arriba y abajo + borde
        self.assertEqual(L.HUECO_SUPERIOR_MIN, 34)
        self.assertGreaterEqual(L.ALTO, 24)
        self.assertLessEqual(L.ALTO, 28)

    def test_encima_en_el_hueco_superior_alineada_con_la_derecha(self):
        # ventana pegada al hueco superior del mosaico: la pastilla cabe encima
        x, y, dentro = L.colocar(A, (0, 0, 1920, 1080), [])
        self.assertEqual((x, y, dentro), (14 + 934 - L.ANCHO, 34 - L.ALTO - L.SEPARACION, False))
        self.assertGreaterEqual(y, L.TOPE)
        self.assertLessEqual(y + L.ALTO, 34 - 1)  # no pisa la ventana

    def test_flotante_pegada_arriba_va_dentro_con_8_px(self):
        c = cliente("0x1", 500, 10, 600, 400, floating=True)
        x, y, dentro = L.colocar(c, (0, 0, 1920, 1080), [])
        self.assertTrue(dentro)
        self.assertEqual((x, y), (500 + 600 - L.ANCHO - L.MARGEN_DENTRO, 10 + L.MARGEN_DENTRO))

    def test_otra_ventana_justo_encima_manda_a_dentro(self):
        otras = [L.rect_ventana(B)]
        x, y, dentro = L.colocar(C, (0, 0, 1920, 1080), otras)
        self.assertTrue(dentro)
        self.assertEqual((x, y), (954 + 952 - L.ANCHO - 8, 510 + 8))

    def test_otra_ventana_lejos_no_molesta(self):
        otras = [L.rect_ventana(C)]  # está debajo de B, no encima
        _x, _y, dentro = L.colocar(B, (0, 0, 1920, 1080), otras)
        self.assertFalse(dentro)

    def test_pastilla_que_apenas_roza_la_otra_ventana_cuenta_como_choque(self):
        # la de encima termina 1 px dentro del hueco que ocuparía la pastilla
        c = cliente("0x1", 100, 200, 600, 300)
        y_pastilla = 200 - L.ALTO - L.SEPARACION
        encima = (100, 0, 600, y_pastilla + 1)
        self.assertTrue(L.colocar(c, (0, 0, 1920, 1080), [encima])[2])
        justo = (100, 0, 600, y_pastilla)  # termina justo donde empieza la pastilla: no se tocan
        self.assertFalse(L.colocar(c, (0, 0, 1920, 1080), [justo])[2])

    def test_si_no_deja_el_tope_con_la_pantalla_va_dentro(self):
        c = cliente("0x1", 300, L.ALTO + L.SEPARACION + L.TOPE - 1, 600, 300)
        self.assertTrue(L.colocar(c, (0, 0, 1920, 1080), [])[2])
        c = cliente("0x1", 300, L.ALTO + L.SEPARACION + L.TOPE, 600, 300)
        self.assertFalse(L.colocar(c, (0, 0, 1920, 1080), [])[2])

    def test_el_hueco_minimo_del_conf_basta_para_ir_encima(self):
        c = cliente("0x1", 14, L.HUECO_SUPERIOR_MIN, 800, 600)
        self.assertFalse(L.colocar(c, (0, 0, 1920, 1080), [])[2])

    def test_la_pastilla_no_se_sale_de_la_pantalla(self):
        # ventana que se sale por la derecha y por la izquierda
        c = cliente("0x1", 1500, 100, 900, 400)
        x, _y, _d = L.colocar(c, (0, 0, 1920, 1080), [])
        self.assertEqual(x, 1920 - L.ANCHO - L.TOPE)
        c = cliente("0x1", -900, 100, 800, 400)
        x, _y, _d = L.colocar(c, (0, 0, 1920, 1080), [])
        self.assertEqual(x, L.TOPE)

    def test_dentro_tambien_respeta_la_pantalla(self):
        c = cliente("0x1", 100, -50, 600, 400, floating=True)  # medio fuera por arriba
        _x, y, dentro = L.colocar(c, (0, 0, 1920, 1080), [])
        self.assertTrue(dentro)
        self.assertGreaterEqual(y, L.TOPE)

    def test_monitor_con_desplazamiento(self):
        # segundo monitor a la derecha y más abajo: las coordenadas son globales
        m2 = (1920, 200, 1920, 1080)
        c = cliente("0x1", 1934, 234, 800, 600, monitor=1)
        x, y, dentro = L.colocar(c, m2, [])
        self.assertEqual((x, y, dentro), (1934 + 800 - L.ANCHO, 234 - L.ALTO - L.SEPARACION, False))
        # el tope se mide desde la esquina de SU monitor, no desde 0
        c = cliente("0x1", 1934, 200 + 20, 800, 600, monitor=1)
        self.assertTrue(L.colocar(c, m2, [])[2])


class TestPlanificar(unittest.TestCase):
    def test_mosaico_de_tres_ventanas(self):
        plan = {p.direccion: p for p in L.planificar([A, B, C], [MON])}
        self.assertEqual(set(plan), {"0xaaa", "0xbbb", "0xccc"})
        self.assertFalse(plan["0xaaa"].dentro)
        self.assertFalse(plan["0xbbb"].dentro)
        self.assertTrue(plan["0xccc"].dentro)  # B está justo encima
        a = plan["0xaaa"]
        self.assertEqual((a.arriba, a.izquierda), (4, 14 + 934 - L.ANCHO))
        self.assertEqual((a.clase, a.nombre_monitor, a.maximizada), ("foot", "HDMI-A-1", False))

    def test_dos_monitores_dan_margenes_relativos_a_cada_uno(self):
        m1 = monitor(1, x=1920, y=0, nombre="DP-1", activo=3)
        d = cliente("0xddd", 1934, 34, 934, 960, ws=3, monitor=1)
        plan = {p.direccion: p for p in L.planificar([A, d], [MON, m1])}
        self.assertEqual((plan["0xddd"].arriba, plan["0xddd"].izquierda), (4, 14 + 934 - L.ANCHO))  # relativo a DP-1
        self.assertEqual(plan["0xddd"].nombre_monitor, "DP-1")
        self.assertEqual(plan["0xaaa"].nombre_monitor, "HDMI-A-1")

    def test_una_ventana_de_otro_monitor_no_cuenta_como_encima(self):
        m1 = monitor(1, x=1920, y=0, nombre="DP-1")
        d = cliente("0xddd", 1934, 0, 934, 30, monitor=1, ws=1)  # tapa el sitio, pero en el otro monitor
        d["size"] = [934, 30]
        plan = {p.direccion: p for p in L.planificar([A, d], [MON, m1])}
        self.assertNotIn("0xddd", plan)  # 30 px de alto: diminuta
        otra = cliente("0xeee", 1934, 0, 934, 200, monitor=1, ws=1)
        plan = {p.direccion: p for p in L.planificar([A, otra], [MON, m1])}
        self.assertFalse(plan["0xaaa"].dentro)

    def test_maximizada_conserva_la_pastilla_encima(self):
        m = cliente("0xmmm", 14, 34, 1892, 970, fullscreen=1)
        p = L.planificar([m], [MON])[0]
        self.assertTrue(p.maximizada)
        self.assertFalse(p.dentro)

    def test_pantalla_completa_real_oculta_la_pastilla(self):
        self.assertEqual(L.planificar([cliente("0xfff", 0, 0, 1920, 1080, fullscreen=2)], [MON]), [])

    def test_ventanas_de_otro_espacio_o_minimizadas_no_salen(self):
        mini = cliente("0x111", 0, 0, 800, 600, ws=-98)
        mini["workspace"]["name"] = "special:minimizadas"
        otra = cliente("0x222", 14, 34, 800, 600, ws=2)
        self.assertEqual(L.planificar([mini, otra], [MON]), [])

    def test_entradas_vacias_o_raras_no_revientan(self):
        self.assertEqual(L.planificar([], []), [])
        self.assertEqual(L.planificar(None, None), [])
        self.assertEqual(L.planificar([A], []), [])


class TestAcciones(unittest.TestCase):
    """Las órdenes exactas a Hyprland. Todas llevan la dirección de LA ventana de la pastilla."""

    def setUp(self):
        self.h = HyprFalso()

    def test_cerrar_es_closewindow_de_esa_direccion(self):
        L.cerrar(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes, ["closewindow address:0xbbb"])

    def test_minimizar_manda_al_especial_sin_mostrarlo(self):
        L.minimizar(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes, ["movetoworkspacesilent special:minimizadas,address:0xbbb"])
        self.assertFalse(any("togglespecialworkspace" in o for o in self.h.ordenes))

    def test_maximizar_enfoca_esa_ventana_y_alterna_el_modo_1(self):
        L.alternar_maximizada(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes, ["focuswindow address:0xbbb", "fullscreen 1"])

    def test_restaurar_trae_al_espacio_activo_y_da_el_foco(self):
        self.h.datos["activeworkspace"] = {"id": 3, "name": "3"}
        L.restaurar(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes, ["movetoworkspace 3,address:0xbbb", "focuswindow address:0xbbb"])

    def test_restaurar_en_un_espacio_con_nombre(self):
        self.h.datos["activeworkspace"] = {"id": -1337, "name": "trabajo"}
        L.restaurar(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes[0], "movetoworkspace name:trabajo,address:0xbbb")

    def test_restaurar_sin_saber_el_espacio_usa_el_actual(self):
        self.h.datos["activeworkspace"] = {}
        L.restaurar(self.h, "0xbbb")
        self.assertEqual(self.h.ordenes, ["movetoworkspace +0,address:0xbbb", "focuswindow address:0xbbb"])

    def test_si_hyprland_falla_no_se_propaga(self):
        class Roto(HyprFalso):
            def despachar(self, *a):
                raise OSError("socket roto")
        for f in (L.cerrar, L.minimizar, L.alternar_maximizada, L.restaurar):
            f(Roto(), "0xbbb")  # no lanza


class TestClicEnElDock(unittest.TestCase):
    def v(self, direccion, minimizada=False):
        c = cliente(direccion, 0, 0, 600, 400)
        if minimizada:
            c["workspace"] = {"id": -98, "name": "special:minimizadas"}
        return c

    def test_sin_ventanas_no_hay_destino(self):
        self.assertEqual(L.elegir_destino_clic([], None), (None, None))

    def test_salta_a_la_mas_reciente_que_no_este_en_primer_plano(self):
        vs = [self.v("0x1"), self.v("0x2"), self.v("0x3")]
        que, v = L.elegir_destino_clic(vs, "0x1")
        self.assertEqual((que, v["address"]), ("enfocar", "0x2"))
        que, v = L.elegir_destino_clic(vs, "0x9")
        self.assertEqual((que, v["address"]), ("enfocar", "0x1"))

    def test_una_sola_visible_y_activa_se_queda(self):
        que, v = L.elegir_destino_clic([self.v("0x1")], "0x1")
        self.assertEqual((que, v["address"]), ("enfocar", "0x1"))

    def test_todas_minimizadas_restaura_la_mas_reciente(self):
        vs = [self.v("0x1", True), self.v("0x2", True)]
        que, v = L.elegir_destino_clic(vs, None)
        self.assertEqual((que, v["address"]), ("restaurar", "0x1"))

    def test_si_la_unica_visible_es_la_activa_y_hay_minimizadas_se_trae_una(self):
        vs = [self.v("0x1"), self.v("0x2", True)]
        que, v = L.elegir_destino_clic(vs, "0x1")
        self.assertEqual((que, v["address"]), ("restaurar", "0x2"))

    def test_hay_otra_visible_y_minimizadas_se_prefiere_la_visible(self):
        vs = [self.v("0x1"), self.v("0x2"), self.v("0x3", True)]
        que, v = L.elegir_destino_clic(vs, "0x1")
        self.assertEqual((que, v["address"]), ("enfocar", "0x2"))


# ----------------------------------------------------------------------------- ficheros del sistema
class TestFicheros(unittest.TestCase):
    def test_hyprland_conf_reserva_el_hueco_superior(self):
        m = re.search(r"(?m)^\s*gaps_out\s*=\s*(.+?)\s*$", leer(HYPR_CONF))
        self.assertIsNotNone(m)
        valores = [int(x) for x in re.split(r"[,\s]+", m.group(1))]
        self.assertEqual(len(valores), 4)  # arriba, derecha, abajo, izquierda
        self.assertGreaterEqual(valores[0], L.HUECO_SUPERIOR_MIN)
        self.assertEqual(valores[1:], [14, 14, 14])  # los otros lados no cambian

    def test_el_skel_de_ajustes_tiene_el_mismo_hueco(self):
        m = re.search(r"(?m)^\s*gaps_out\s*=\s*(.+?)\s*$", leer(AJUSTES_CONF))
        self.assertEqual([int(x) for x in re.split(r"[,\s]+", m.group(1))], [34, 14, 14, 14])

    def test_ajustes_conserva_el_hueco_superior_con_cualquier_separacion(self):
        from infinity_shell import ajustes as A_
        for hueco in (0, 3, 6, 12, 30):
            v = dict(A_.DEFECTOS)
            v["ventanas.hueco"] = hueco
            m = re.search(r"(?m)^\s*gaps_out\s*=\s*(.+?)\s*$", A_.hypr_conf(v))
            valores = [int(x) for x in re.split(r"[,\s]+", m.group(1))]
            self.assertEqual(len(valores), 4, hueco)
            self.assertGreaterEqual(valores[0], L.HUECO_SUPERIOR_MIN, hueco)
            self.assertEqual(valores[1:], [hueco + 8] * 3, hueco)
            self.assertGreaterEqual(valores[0], valores[1], hueco)

    def test_los_atajos_nuevos_no_chocan_con_los_existentes(self):
        combos = []
        for linea in leer(HYPR_CONF).splitlines():
            m = re.match(r"^\s*(bind[a-z]*)\s*=\s*([^,]*),\s*([^,]*),", linea)
            if m:
                mods = " ".join(sorted(m.group(2).replace("$mod", "SUPER").split()))
                combos.append((mods, m.group(3).strip().upper()))
        self.assertEqual(len(combos), len(set(combos)), "hay dos atajos con la misma combinación")
        self.assertIn(("SUPER", "M"), combos)
        self.assertIn(("SHIFT SUPER", "F"), combos)

    def test_super_m_minimiza_sin_mostrar_el_especial(self):
        conf = leer(HYPR_CONF)
        self.assertRegex(conf, r"(?m)^bind = \$mod, M,\s+movetoworkspacesilent, special:minimizadas\b")
        self.assertRegex(conf, r"(?m)^bind = \$mod SHIFT, F,\s+fullscreen, 1\b")
        self.assertRegex(conf, r"(?m)^bind = \$mod, F,\s+fullscreen\s*$")  # Super + F sigue siendo pantalla completa real
        self.assertNotIn("togglespecialworkspace", conf)

    def test_la_regla_de_desenfoque_alcanza_a_los_controles(self):
        conf = leer(HYPR_CONF)
        regla = re.search(r"(?m)^layerrule = blur on, match:namespace (\S+)", conf).group(1)
        self.assertTrue(re.match(regla, "infinity-controles"))
        # xray calcula el desenfoque solo con el fondo: dentro de una ventana se vería mal, así que no aplica
        xray = re.search(r"(?m)^layerrule = xray on, match:namespace (\S+)", conf).group(1)
        self.assertFalse(re.match(xray, "infinity-controles"))

    def test_gtk_sin_botones_propios(self):
        for ruta in (GTK3_INI, GTK4_INI):
            self.assertRegex(leer(ruta), r"(?m)^gtk-decoration-layout=:\s*$", ruta)

    def test_esquema_de_gsettings_sin_botones(self):
        texto = leer(OVERRIDE)
        m = re.search(r"\[org\.gnome\.desktop\.wm\.preferences\]\s*\nbutton-layout='([^']*)'", texto)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1), ":")

    def test_panki_fija_su_propio_layout(self):
        arbol = ast.parse(leer(os.path.join(PAQUETE, "panki_app.py")))
        llamadas = [n for n in ast.walk(arbol) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Attribute) and n.func.attr == "set_decoration_layout"]
        self.assertEqual(len(llamadas), 1)
        capa = llamadas[0].args[0].value
        izq, _, der = capa.partition(":")
        self.assertEqual(izq, "")
        self.assertEqual(der.split(","), ["minimize", "maximize", "close"])

    def test_css_con_su_bloque_al_final(self):
        css = leer(CSS)
        for selector in (".glass.ctl", "button.ctl-btn", "button.ctl-btn:hover", "button.ctl-btn.ctl-cerrar:hover"):
            self.assertIn(selector, css)
        self.assertGreater(css.index(".glass.ctl"), css.index("button.dock-item"))
        cierre = re.search(r"button\.ctl-btn\.ctl-cerrar:hover\s*\{([^}]*)\}", css).group(1)
        r, g, b = (int(x) for x in re.search(r"rgba\((\d+),\s*(\d+),\s*(\d+)", cierre).groups())
        self.assertGreater(r, g + 100)  # tinte rojo
        self.assertGreater(r, b + 100)

    def test_main_arranca_los_controles_con_su_propia_conexion(self):
        arbol = ast.parse(leer(os.path.join(PAQUETE, "main.py")))
        texto = ast.unparse(arbol)
        self.assertIn("from .controles import Controles", texto)
        self.assertIn("Controles(self, Hyprland())", texto)

    def test_el_dock_usa_la_logica_de_restaurar(self):
        texto = leer(os.path.join(PAQUETE, "dock.py"))
        self.assertIn("elegir_destino_clic", texto)
        self.assertIn("restaurar(self.hypr", texto)
        self.assertIn("es_minimizada(v)", texto)  # la firma del dock cambia al minimizar / restaurar

    def test_controles_no_usa_togglespecialworkspace(self):
        for nombre in ("controles.py", "controles_logica.py"):
            codigo = ast.parse(leer(os.path.join(PAQUETE, nombre)))
            cadenas = [n.value for n in ast.walk(codigo) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
            self.assertNotIn("togglespecialworkspace", cadenas, nombre)

    def test_nombre_de_capa_y_eventos(self):
        texto = leer(os.path.join(PAQUETE, "controles.py"))
        self.assertIn('NAMESPACE = "infinity-controles"', texto)
        self.assertTrue({"openwindow", "closewindow", "workspacev2", "movewindowv2", "fullscreen",
                         "activespecialv2"} <= L.EVENTOS)
        self.assertNotIn("windowtitlev2", L.EVENTOS)  # llegan sin parar con un navegador y no cambian nada


# ======================================================================= 2. hypr.py real contra el doble
class FakeConMonitores:
    """Un FakeHyprland que además responde a j/monitors y j/activeworkspace (el original no los conoce)."""

    @staticmethod
    def crear(runtime, monitores, activo):
        from fake_hyprland import FakeHyprland

        class Doble(FakeHyprland):
            def _responder(self, orden):
                if orden == "j/monitors":
                    return json.dumps(monitores)
                if orden == "j/activeworkspace":
                    return json.dumps(activo)
                return super()._responder(orden)
        return Doble(runtime).start()


@unittest.skipIf(gi is None, "PyGObject no disponible")
class TestHyprReal(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.fake = FakeConMonitores.crear(self.tmp.name, [MON], {"id": 2, "name": "2"})
        self._env = {k: os.environ.get(k) for k in ("XDG_RUNTIME_DIR", "HYPRLAND_INSTANCE_SIGNATURE")}
        os.environ["XDG_RUNTIME_DIR"] = self.tmp.name
        os.environ["HYPRLAND_INSTANCE_SIGNATURE"] = self.fake.firma
        from infinity_shell.hypr import Hyprland
        self.hypr = Hyprland()

    def tearDown(self):
        self.fake.stop()
        self.tmp.cleanup()
        for k, v in self._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_las_ordenes_llegan_con_el_formato_del_socket(self):
        L.cerrar(self.hypr, "0xbbb")
        L.minimizar(self.hypr, "0xbbb")
        L.alternar_maximizada(self.hypr, "0xbbb")
        L.restaurar(self.hypr, "0xbbb")
        self.assertEqual(self.fake.despachos, [
            "closewindow address:0xbbb",
            "movetoworkspacesilent special:minimizadas,address:0xbbb",
            "focuswindow address:0xbbb", "fullscreen 1",
            "movetoworkspace 2,address:0xbbb", "focuswindow address:0xbbb",
        ])

    def test_el_dock_ve_las_minimizadas_aunque_hyprland_las_marque_ocultas(self):
        self.fake.abrir("0xaaa", "foot")
        self.fake.abrir("0xbbb", "thunar")
        mini = self.fake.clientes[0]  # 0xaaa (la más antigua)
        self.assertEqual(mini["address"], "0xaaa")
        mini["workspace"] = {"id": -98, "name": "special:minimizadas"}
        mini["hidden"] = True
        self.assertEqual({v["address"] for v in self.hypr.ventanas()}, {"0xaaa", "0xbbb"})
        otra = self.fake.clientes[1]
        otra["hidden"] = True  # oculta de verdad (no minimizada): sigue sin contar
        self.assertEqual({v["address"] for v in self.hypr.ventanas()}, {"0xaaa"})

    def test_la_consulta_de_monitores_y_ventanas_alimenta_el_plan(self):
        self.fake.clientes = [dict(A), dict(B), dict(C)]
        plan = L.planificar(self.hypr.json("clients", []), self.hypr.json("monitors", []))
        self.assertEqual({p.direccion for p in plan}, {"0xaaa", "0xbbb", "0xccc"})


# ======================================================================= 3. con GTK y pantalla
def _doble_capa():
    """gtk4-layer-shell falso: apunta lo que se le pide a cada ventana (mismo formato que en test_panki_burbuja)."""
    doble = types.ModuleType("gi.repository.Gtk4LayerShell")
    doble.registro = {}
    doble.Layer = types.SimpleNamespace(BACKGROUND=0, BOTTOM=1, TOP=2, OVERLAY=3)
    doble.Edge = types.SimpleNamespace(LEFT=0, RIGHT=1, TOP=2, BOTTOM=3)
    doble.KeyboardMode = types.SimpleNamespace(NONE=0, EXCLUSIVE=1, ON_DEMAND=2)

    def datos(ventana):
        return doble.registro.setdefault(ventana, {"anclas": set(), "margenes": {}, "cambios": 0})

    def anotar(clave):
        return lambda ventana, valor: datos(ventana).__setitem__(clave, valor)

    def margen(ventana, borde, valor):
        datos(ventana)["margenes"][borde] = valor
        datos(ventana)["cambios"] += 1

    doble.init_for_window = lambda v: datos(v)
    doble.set_namespace = anotar("namespace")
    doble.set_layer = anotar("capa")
    doble.set_keyboard_mode = anotar("teclado")
    doble.set_exclusive_zone = anotar("zona")
    doble.set_monitor = anotar("monitor")
    doble.auto_exclusive_zone_enable = lambda v: datos(v).__setitem__("zona", "auto")
    doble.set_anchor = lambda v, borde, ancla: (datos(v)["anclas"].add if ancla else datos(v)["anclas"].discard)(borde)
    doble.set_margin = margen
    return doble


def _importar(*nombres):
    """Importa módulos de infinity_shell que usan gtk4-layer-shell. Si falta el typelib, con un doble. Si otro
    módulo de pruebas ya instaló el suyo, se reutiliza (añadiéndole set_monitor, que solo usan los controles)."""
    original = gi.require_version
    try:
        try:
            gi.require_version("Gtk4LayerShell", "1.0")
        except ValueError:
            if "gi.repository.Gtk4LayerShell" not in sys.modules:
                sys.modules["gi.repository.Gtk4LayerShell"] = _doble_capa()
            doble = sys.modules["gi.repository.Gtk4LayerShell"]
            if not hasattr(doble, "set_monitor"):
                doble.set_monitor = lambda v, m: doble.registro.setdefault(v, {}).__setitem__("monitor", m)
            gi.require_version = lambda nombre, version: None if nombre == "Gtk4LayerShell" else original(nombre, version)
        import importlib
        modulos = [importlib.import_module("infinity_shell." + n) for n in nombres]
        return modulos[0] if len(modulos) == 1 else modulos
    finally:
        gi.require_version = original


def bombear(ms=300):
    ctx = GLib.MainContext.default()
    fin = time.time() + ms / 1000
    while time.time() < fin:
        while ctx.pending():
            ctx.iteration(False)
        time.sleep(0.005)


@unittest.skipUnless(PANTALLA and gi is not None, "necesita GTK y una pantalla (xvfb-run)")
class TestPastillas(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        gi.require_version("Gtk", "4.0")
        gi.require_version("Gdk", "4.0")
        from gi.repository import Gdk, Gtk
        cls.Gtk = Gtk
        cls.C = _importar("controles")
        cls.capa = _doble_capa()
        cls.C.LayerShell = cls.capa
        cls.app = Gtk.Application(application_id=f"org.infinity.test.controles{time.time_ns()}")
        cls.app.register(None)
        cls.proveedor = Gtk.CssProvider()
        cls.proveedor.load_from_path(CSS)
        cls.display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(cls.display, cls.proveedor, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    @classmethod
    def tearDownClass(cls):
        # El CSS del shell es global: si se queda puesto, cambia lo que miden los widgets de las pruebas que vienen después
        cls.Gtk.StyleContext.remove_provider_for_display(cls.display, cls.proveedor)

    def setUp(self):
        self.pulsados = []
        self.vivas = []

    def tearDown(self):
        for p in self.vivas:
            p.destroy()

    def pastilla(self, direccion="0xbbb"):
        p = self.C.Pastilla(self.app, direccion, lambda t, d: self.pulsados.append((t, d)))
        self.vivas.append(p)
        return p

    # ----------------------------------------------------------------- la superficie
    def test_es_una_capa_top_sin_teclado_anclada_arriba_a_la_izquierda(self):
        p = self.pastilla()
        d = self.capa.registro[p]
        self.assertEqual(d["namespace"], "infinity-controles")
        self.assertEqual(d["capa"], self.capa.Layer.TOP)
        self.assertEqual(d["teclado"], self.capa.KeyboardMode.NONE)
        self.assertEqual(d["zona"], -1)
        self.assertEqual(d["anclas"], {self.capa.Edge.TOP, self.capa.Edge.LEFT})
        self.assertTrue(p.has_css_class("infinity-window"))
        self.assertTrue(p.caja.has_css_class("glass"))

    def test_mide_exactamente_lo_que_dice_controles_logica(self):
        p = self.pastilla()
        Gtk = self.Gtk
        ancho = p.caja.measure(Gtk.Orientation.HORIZONTAL, -1)
        alto = p.caja.measure(Gtk.Orientation.VERTICAL, -1)
        self.assertEqual((ancho[0], alto[0]), (L.ANCHO, L.ALTO))  # el mínimo: nada la hace más grande
        self.assertEqual((ancho[1], alto[1]), (L.ANCHO, L.ALTO))  # y el natural, igual
        for b in p.botones.values():
            self.assertEqual(b.measure(Gtk.Orientation.HORIZONTAL, -1)[0], L.BOTON)
            self.assertEqual(b.measure(Gtk.Orientation.VERTICAL, -1)[0], L.BOTON)

    def test_tres_botones_con_ayuda_y_nombre_accesible(self):
        p = self.pastilla()
        self.assertEqual(list(p.botones), ["minimizar", "maximizar", "cerrar"])
        self.assertEqual([b.get_tooltip_text() for b in p.botones.values()], ["Minimizar", "Maximizar", "Cerrar"])
        self.assertTrue(p.botones["cerrar"].has_css_class("ctl-cerrar"))
        self.assertFalse(any(b.get_focusable() for b in p.botones.values()))

    def test_cada_boton_avisa_con_su_tipo_y_la_direccion_de_su_ventana(self):
        p = self.pastilla("0xabc")
        for tipo in ("minimizar", "maximizar", "cerrar"):
            p.botones[tipo].emit("clicked")
        self.assertEqual(self.pulsados, [("minimizar", "0xabc"), ("maximizar", "0xabc"), ("cerrar", "0xabc")])

    def test_el_boton_del_medio_cambia_entre_maximizar_y_restaurar(self):
        p = self.pastilla()
        medio = p.botones["maximizar"]
        p.poner_maximizada(True)
        self.assertEqual((medio.get_tooltip_text(), medio.get_child().tipo), ("Restaurar", "restaurar"))
        p.poner_maximizada(False)
        self.assertEqual((medio.get_tooltip_text(), medio.get_child().tipo), ("Maximizar", "maximizar"))

    def test_colocar_pone_los_margenes_y_solo_los_toca_si_cambian(self):
        p = self.pastilla()
        plan = L.planificar([A], [MON])[0]
        p.colocar(plan)
        d = self.capa.registro[p]
        self.assertEqual(d["margenes"][self.capa.Edge.TOP], plan.arriba)
        self.assertEqual(d["margenes"][self.capa.Edge.LEFT], plan.izquierda)
        cambios = d["cambios"]
        p.colocar(plan)
        p.colocar(plan)
        self.assertEqual(d["cambios"], cambios)  # misma geometría: la superficie no se toca
        p.colocar(plan._replace(arriba=plan.arriba + 5))
        self.assertEqual(d["cambios"], cambios + 2)
        self.assertEqual(d["margenes"][self.capa.Edge.TOP], plan.arriba + 5)

    def test_colocar_con_monitor_lo_asigna_una_sola_vez(self):
        p = self.pastilla()
        plan = L.planificar([A], [MON])[0]
        falso = object()
        p.colocar(plan, falso)
        p.colocar(plan, falso)
        self.assertIs(self.capa.registro[p]["monitor"], falso)

    # ----------------------------------------------------------------- el gestor
    def gestor(self, clientes, **kw):
        h = HyprFalso(clientes, **kw)
        g = self.C.Controles(self.app, h, buscar_monitor=lambda nombre: None)
        bombear(100)
        self.addCleanup(g.cerrar)
        return g, h

    def test_crea_una_pastilla_por_ventana_que_la_lleva(self):
        g, _h = self.gestor([A, B, C, cliente("0xppp", 100, 100, 460, 640, clase="org.infinity.Panki")])
        self.assertEqual(set(g.pastillas), {"0xaaa", "0xbbb", "0xccc"})
        self.assertTrue(all(p.get_visible() for p in g.pastillas.values()))

    def test_los_botones_actuan_sobre_su_ventana_aunque_otra_tenga_el_foco(self):
        a = dict(A, focusHistoryID=0)  # A es la activa; se pulsa en la pastilla de B
        g, h = self.gestor([a, B])
        g.pastillas["0xbbb"].botones["cerrar"].emit("clicked")
        g.pastillas["0xbbb"].botones["minimizar"].emit("clicked")
        g.pastillas["0xbbb"].botones["maximizar"].emit("clicked")
        self.assertEqual(h.ordenes, [
            "closewindow address:0xbbb",
            "movetoworkspacesilent special:minimizadas,address:0xbbb",
            "focuswindow address:0xbbb", "fullscreen 1",
        ])
        self.assertFalse([o for o in h.ordenes if "0xaaa" in o])

    def test_un_tipo_desconocido_no_hace_nada(self):
        g, h = self.gestor([A])
        g.pulsar("explotar", "0xaaa")
        self.assertEqual(h.ordenes, [])

    def test_una_ventana_que_deja_el_espacio_activo_se_oculta_y_si_se_cierra_se_destruye(self):
        g, h = self.gestor([A, B])
        pb = g.pastillas["0xbbb"]
        # B pasa a otro espacio de trabajo (sigue existiendo)
        h.datos["clients"] = [A, dict(B, workspace={"id": 2, "name": "2"})]
        g.aplicar(h.json("clients"), h.json("monitors"))
        self.assertIn("0xbbb", g.pastillas)
        self.assertFalse(pb.get_visible())
        # vuelve
        h.datos["clients"] = [A, B]
        g.aplicar(h.json("clients"), h.json("monitors"))
        self.assertTrue(pb.get_visible())
        self.assertIs(g.pastillas["0xbbb"], pb)  # la misma pastilla, no una nueva
        # se cierra
        h.datos["clients"] = [A]
        g.aplicar(h.json("clients"), h.json("monitors"))
        self.assertNotIn("0xbbb", g.pastillas)

    def test_al_cerrarse_la_ventana_se_destruye_la_superficie_de_su_pastilla(self):
        destruidas = []

        class Falsa:
            def __init__(self, app, direccion, al_pulsar):
                self.visible = False

            def colocar(self, plan, monitor=None):
                self.visible = True

            def ocultar(self):
                self.visible = False

            def get_visible(self):
                return self.visible

            def destroy(self):
                destruidas.append(self)

        h = HyprFalso([A, B])
        g = self.C.Controles(self.app, h, fabrica=Falsa, buscar_monitor=lambda n: None)
        self.addCleanup(g.cerrar)
        bombear(100)
        self.assertEqual(len(g.pastillas), 2)
        g.aplicar([A], [MON])
        self.assertEqual(len(destruidas), 1)
        g.aplicar([dict(A, workspace={"id": 2, "name": "2"})], [MON])  # A solo cambia de espacio: se oculta, no se destruye
        self.assertEqual(len(destruidas), 1)

    def test_minimizada_oculta_la_pastilla_y_restaurada_vuelve(self):
        g, h = self.gestor([A])
        mini = dict(A, workspace={"id": -98, "name": "special:minimizadas"})
        g.aplicar([mini], h.json("monitors"))
        self.assertFalse(g.pastillas["0xaaa"].get_visible())
        g.aplicar([A], h.json("monitors"))
        self.assertTrue(g.pastillas["0xaaa"].get_visible())

    def test_pantalla_completa_real_oculta_y_maximizada_cambia_el_icono(self):
        g, h = self.gestor([A])
        g.aplicar([dict(A, fullscreen=2)], h.json("monitors"))
        self.assertFalse(g.pastillas["0xaaa"].get_visible())
        g.aplicar([dict(A, fullscreen=1)], h.json("monitors"))
        self.assertTrue(g.pastillas["0xaaa"].get_visible())
        self.assertTrue(g.pastillas["0xaaa"].maximizada)
        g.aplicar([A], h.json("monitors"))
        self.assertFalse(g.pastillas["0xaaa"].maximizada)

    def test_seguir_a_la_ventana_cuando_se_mueve(self):
        g, h = self.gestor([A])
        p = g.pastillas["0xaaa"]
        d = self.capa.registro[p]
        antes = d["margenes"][self.capa.Edge.LEFT]
        movida = dict(A, at=[214, 134])
        g.aplicar([movida], h.json("monitors"))
        self.assertEqual(d["margenes"][self.capa.Edge.LEFT], antes + 200)
        self.assertEqual(d["margenes"][self.capa.Edge.TOP], 134 - L.ALTO - L.SEPARACION)

    def test_el_sondeo_solo_corre_mientras_haya_pastillas_visibles(self):
        g, h = self.gestor([A])
        self.assertTrue(g._temporizador)
        g.aplicar([], h.json("monitors"))
        self.assertFalse(g._temporizador)
        g.aplicar([A], h.json("monitors"))
        self.assertTrue(g._temporizador)
        g.aplicar([dict(A, fullscreen=2)], h.json("monitors"))  # todas ocultas: sin sondeo
        self.assertFalse(g._temporizador)

    def test_sin_socket_de_eventos_sondea_siempre_despacio(self):
        g, _h = self.gestor([], eventos=False)
        self.assertTrue(g._temporizador)
        g.aplicar([], [MON])
        self.assertTrue(g._temporizador)  # sin eventos no hay otra forma de enterarse de una ventana nueva

    def test_los_eventos_de_hyprland_provocan_una_consulta(self):
        g, h = self.gestor([])
        self.assertEqual(g.pastillas, {})
        h.datos["clients"] = [A]
        h.on_event("openwindow", "aaa,1,foot,foot")
        bombear(400)
        self.assertEqual(set(g.pastillas), {"0xaaa"})

    def test_eventos_que_no_cambian_nada_visible_se_ignoran(self):
        g, h = self.gestor([])
        h.datos["clients"] = [A]
        h.on_event("windowtitlev2", "aaa,hola")
        bombear(300)
        self.assertEqual(g.pastillas, {})

    def test_cerrar_quita_todo_y_lo_que_llegue_despues_se_ignora(self):
        g, h = self.gestor([A, B])
        self.assertEqual(len(g.pastillas), 2)
        g.cerrar()
        self.assertEqual(g.pastillas, {})
        self.assertFalse(g._temporizador)
        g._recibido([A], [MON])  # una consulta que estaba en vuelo
        self.assertEqual(g.pastillas, {})

    def test_sin_hyprland_no_hace_nada_y_no_falla(self):
        class Sin(HyprFalso):
            def __init__(self):
                super().__init__()
                self.disponible = False
        g = self.C.Controles(self.app, Sin())
        self.addCleanup(g.cerrar)
        bombear(100)
        self.assertEqual(g.pastillas, {})
        self.assertFalse(g._temporizador)
        g.refrescar()
        g.pulsar("cerrar", "0xaaa")  # tampoco lanza (la orden va al vacío)

    def test_una_consulta_que_falla_deja_el_estado_vacio_sin_romper(self):
        class Roto(HyprFalso):
            def json(self, orden, defecto=None):
                raise OSError("sin socket")
        h = Roto([A])
        g = self.C.Controles(self.app, h, buscar_monitor=lambda n: None)
        self.addCleanup(g.cerrar)
        bombear(150)
        self.assertEqual(g.pastillas, {})

    def test_dos_avisos_seguidos_hacen_una_sola_consulta_a_la_vez(self):
        g, h = self.gestor([A])
        g._quitar_temporizador()  # solo cuentan las consultas de los avisos, no las del sondeo
        consultas = []
        original = h.json
        h.json = lambda orden, defecto=None: (consultas.append(orden), original(orden, defecto))[1]
        for _ in range(5):
            g.refrescar_pronto()
        bombear(180)
        self.assertEqual(consultas.count("clients"), 1)

    def test_con_dos_monitores_pide_el_monitor_de_cada_ventana(self):
        m1 = monitor(1, x=1920, nombre="DP-1")
        d = cliente("0xddd", 1934, 34, 934, 960, monitor=1)
        pedidos = []
        h = HyprFalso([A, d], monitores=[MON, m1])
        g = self.C.Controles(self.app, h, buscar_monitor=lambda n: pedidos.append(n))
        self.addCleanup(g.cerrar)
        bombear(150)
        self.assertEqual(sorted(set(pedidos)), ["DP-1", "HDMI-A-1"])
        self.assertEqual(self.capa.registro[g.pastillas["0xddd"]]["margenes"][self.capa.Edge.LEFT], 14 + 934 - L.ANCHO)

    # ----------------------------------------------------------------- GTK y el layout vacío
    def _botones_de_cabecera(self, Gtk, layout_global, layout_propio=None):
        gtk_settings = Gtk.Settings.get_default()
        anterior = gtk_settings.get_property("gtk-decoration-layout")
        gtk_settings.set_property("gtk-decoration-layout", layout_global)
        try:
            ventana = Gtk.Window(application=self.app)
            cabecera = Gtk.HeaderBar()
            if layout_propio is not None:
                cabecera.set_decoration_layout(layout_propio)
            ventana.set_titlebar(cabecera)
            ventana.set_child(Gtk.Label(label="x"))
            ventana.present()
            bombear(200)
            nombres = []

            def recorrer(w):
                if w.get_css_name() == "windowcontrols":
                    c = w.get_first_child()
                    while c:
                        nombres.append([k for k in c.get_css_classes() if k in ("close", "minimize", "maximize")][0])
                        c = c.get_next_sibling()
                c = w.get_first_child()
                while c:
                    recorrer(c)
                    c = c.get_next_sibling()
            recorrer(ventana)
            ventana.destroy()
            return nombres
        finally:
            gtk_settings.set_property("gtk-decoration-layout", anterior)

    def test_con_el_layout_global_vacio_gtk_no_pinta_botones(self):
        self.assertEqual(self._botones_de_cabecera(self.Gtk, ":"), [])

    def test_pero_la_cabecera_de_panki_conserva_los_suyos(self):
        self.assertEqual(self._botones_de_cabecera(self.Gtk, ":", ":minimize,maximize,close"),
                         ["minimize", "maximize", "close"])

    def test_el_layout_de_panki_no_depende_del_global(self):
        self.assertEqual(self._botones_de_cabecera(self.Gtk, "menu:close", ":minimize,maximize,close"),
                         ["minimize", "maximize", "close"])


@unittest.skipUnless(PANTALLA and gi is not None, "necesita GTK y una pantalla (xvfb-run)")
class TestDockRestaura(unittest.TestCase):
    """_pulsar del dock real (sin construir la ventana: solo necesita .hypr y .activa)."""

    @classmethod
    def setUpClass(cls):
        cls.dock = _importar("controles", "dock")[1]

    def clic(self, ventanas, activa, app=True):
        h = HyprFalso(activo={"id": 4, "name": "4"})
        lanzadas = []
        yo = types.SimpleNamespace(hypr=h, activa=activa)
        el = types.SimpleNamespace(ventanas=ventanas, app=types.SimpleNamespace(lanzar=lambda: lanzadas.append(1)) if app else None)
        self.dock.Dock._pulsar(yo, el)
        return h.ordenes, lanzadas

    def v(self, direccion, minimizada=False):
        c = cliente(direccion, 0, 0, 600, 400)
        if minimizada:
            c["workspace"] = {"id": -98, "name": "special:minimizadas"}
        return c

    def test_clic_con_todas_minimizadas_restaura_la_ultima_en_el_espacio_activo(self):
        ordenes, lanzadas = self.clic([self.v("0x1", True), self.v("0x2", True)], None)
        self.assertEqual(ordenes, ["movetoworkspace 4,address:0x1", "focuswindow address:0x1"])
        self.assertEqual(lanzadas, [])

    def test_clic_normal_sigue_enfocando(self):
        ordenes, _ = self.clic([self.v("0x1"), self.v("0x2")], "0x1")
        self.assertEqual(ordenes, ["focuswindow address:0x2"])

    def test_clic_sin_ventanas_lanza_la_app(self):
        ordenes, lanzadas = self.clic([], None)
        self.assertEqual((ordenes, lanzadas), ([], [1]))

    def test_clic_sin_ventanas_ni_app_no_hace_nada(self):
        self.assertEqual(self.clic([], None, app=False), ([], []))


if __name__ == "__main__":
    unittest.main()
