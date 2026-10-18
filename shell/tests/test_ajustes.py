import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import time
import tomllib
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import ajustes as A  # noqa: E402
from infinity_shell import fondos as F  # noqa: E402

SKEL = os.path.join(ROOT, "overlay", "etc", "skel", ".config")
STYLE = os.path.join(ROOT, "shell", "infinity_shell", "style.css")


def _leer(*partes):
    with open(os.path.join(*partes), encoding="utf-8") as f:
        return f.read()


class TestEsquema(unittest.TestCase):
    def test_ids_unicos_y_paginas_conocidas(self):
        ids = [a.id for a in A.SCHEMA]
        self.assertEqual(len(ids), len(set(ids)))
        paginas = {p for p, _ in A.PAGINAS}
        for a in A.SCHEMA:
            self.assertIn(a.pagina, paginas, a.id)

    def test_defectos_son_validos(self):
        normal, errores = A.validar(A.DEFECTOS)
        self.assertEqual(errores, [])
        self.assertEqual(normal, A.DEFECTOS)

    def test_choice_tiene_su_defecto_entre_opciones(self):
        for a in A.SCHEMA:
            if a.tipo == "choice":
                self.assertIn(a.defecto, [o for o, _ in a.opciones], a.id)

    def test_aplica_conocidos(self):
        for a in A.SCHEMA:
            self.assertIn(a.aplica, A.ACCION_POR_APLICA, a.id)

    def test_opacidad_minima_respeta_el_desenfoque(self):
        # style.css: el relleno debe tener opacidad >= 0.30, o Hyprland no desenfoca.
        a = next(x for x in A.SCHEMA if x.id == "cristal.opacidad")
        self.assertGreaterEqual(a.minimo, 0.30)


class TestValidar(unittest.TestCase):
    def valida(self, **cambios):
        valores = dict(A.DEFECTOS)
        valores.update({k.replace("__", "."): v for k, v in cambios.items()})
        return A.validar(valores)

    def test_bool_estricto(self):
        _, errores = self.valida(ventanas__sombras="false")
        self.assertEqual([i for i, _ in errores], ["ventanas.sombras"])
        normal, errores = self.valida(ventanas__sombras=False)
        self.assertEqual(errores, [])
        self.assertFalse(normal["ventanas.sombras"])

    def test_entero_en_rango(self):
        self.assertEqual(self.valida(cristal__pasadas=0)[1][0][0], "cristal.pasadas")
        self.assertEqual(self.valida(cristal__pasadas=4.0)[0]["cristal.pasadas"], 4)
        self.assertNotEqual(self.valida(cristal__pasadas=2.5)[1], [])
        self.assertNotEqual(self.valida(cristal__pasadas=True)[1], [])
        self.assertNotEqual(self.valida(cristal__pasadas="3")[1], [])

    def test_flotante_redondea_y_rechaza_no_finitos(self):
        self.assertEqual(self.valida(raton__sensibilidad=0.12345)[0]["raton.sensibilidad"], 0.123)
        self.assertNotEqual(self.valida(raton__sensibilidad=float("inf"))[1], [])
        self.assertNotEqual(self.valida(raton__sensibilidad=float("nan"))[1], [])

    def test_opciones_aceptan_numeros(self):
        self.assertEqual(self.valida(escritorio__escala=1.0)[0]["escritorio.escala"], "1")
        self.assertEqual(self.valida(escritorio__escala=1.25)[0]["escritorio.escala"], "1.25")
        self.assertNotEqual(self.valida(escritorio__escala=3)[1], [])
        self.assertNotEqual(self.valida(escritorio__fondo="sol")[1], [])

    def test_color(self):
        self.assertEqual(self.valida(escritorio__color="#15193A")[0]["escritorio.color"], "#15193a")
        self.assertNotEqual(self.valida(escritorio__color="15193a")[1], [])
        self.assertNotEqual(self.valida(escritorio__color="#15193a\n")[1], [])

    def test_texto_de_fuente(self):
        self.assertEqual(self.valida(terminal__fuente="  Fira Code ")[0]["terminal.fuente"], "Fira Code")
        self.assertNotEqual(self.valida(terminal__fuente="")[1], [])
        self.assertNotEqual(self.valida(terminal__fuente="a\nb")[1], [])
        self.assertNotEqual(self.valida(terminal__fuente="x" * 41)[1], [])

    def test_reglas_entre_ajustes_de_energia(self):
        self.assertEqual(self.valida(bloqueo__minutos=10, bloqueo__apagar_pantalla=10)[1][0][0],
                         "bloqueo.apagar_pantalla")
        self.assertEqual(self.valida(bloqueo__apagar_pantalla=20, bloqueo__suspender=15)[1][0][0],
                         "bloqueo.suspender")
        self.assertEqual(self.valida(bloqueo__suspender=0)[1], [])

    def test_un_error_no_tira_los_demas(self):
        normal, errores = self.valida(raton__sensibilidad=9)
        self.assertEqual(len(errores), 1)
        self.assertEqual(normal["escritorio.color"], A.DEFECTOS["escritorio.color"])


class TestFichero(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ajustes-test-")
        self.ruta = os.path.join(self.tmp, "infinity", "ajustes.json")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_sin_fichero_son_los_defectos(self):
        self.assertEqual(A.cargar(self.ruta), (A.DEFECTOS, []))

    def test_json_roto_avisa_sin_tocar_el_fichero(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w", encoding="utf-8") as f:
            f.write("{no es json")
        valores, avisos = A.cargar(self.ruta)
        self.assertEqual(valores, A.DEFECTOS)
        self.assertEqual(len(avisos), 1)
        with open(self.ruta, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{no es json")

    def test_no_es_un_objeto(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump([1, 2], f)
        self.assertEqual(A.cargar(self.ruta)[0], A.DEFECTOS)

    def test_guardar_y_cargar_ida_y_vuelta(self):
        valores = dict(A.DEFECTOS)
        valores["bloqueo.minutos"] = 25
        valores["bloqueo.apagar_pantalla"] = 30
        A.guardar_json(valores, self.ruta)
        self.assertEqual(A.cargar(self.ruta), (valores, []))
        self.assertFalse(os.path.exists(self.ruta + ".tmp"))

    def test_guardar_solo_claves_conocidas(self):
        A.guardar_json(dict(A.DEFECTOS, basura=1), self.ruta)
        with open(self.ruta, encoding="utf-8") as f:
            self.assertNotIn("basura", json.load(f))

    def test_valor_nunca_lanza(self):
        self.assertEqual(A.valor("bloqueo.minutos", os.path.join(self.tmp, "no-existe.json")), 10)
        self.assertIsNone(A.valor("no.existe", self.ruta))

    def test_importar_rechaza_lo_que_no_es_ajustes(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(["x"], f)
        with self.assertRaises(ValueError):
            A.importar(self.ruta)

    def test_importar_devuelve_errores_sin_aplicar(self):
        os.makedirs(os.path.dirname(self.ruta))
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump({"raton.sensibilidad": 5}, f)
        valores, errores = A.importar(self.ruta)
        self.assertEqual(len(errores), 1)


class TestSkelYGenerador(unittest.TestCase):
    """Lo que se instala en el skel debe ser exactamente lo que genera la app con los defectos."""

    def test_infinity_ajustes_conf_del_skel(self):
        self.assertEqual(_leer(SKEL, "hypr", "infinity-ajustes.conf"), A.hypr_conf(A.DEFECTOS))

    def test_hypridle_del_skel(self):
        self.assertEqual(_leer(SKEL, "hypr", "hypridle.conf"), A.hypridle_conf(A.DEFECTOS))

    def test_gtklock_y_foot_del_skel_no_cambian_con_los_defectos(self):
        self.assertEqual(A.gtklock_config(_leer(SKEL, "gtklock", "config.ini"), A.DEFECTOS),
                         _leer(SKEL, "gtklock", "config.ini"))
        self.assertEqual(A.foot_ini(_leer(SKEL, "foot", "foot.ini"), A.DEFECTOS),
                         _leer(SKEL, "foot", "foot.ini"))

    def test_teclado_por_defecto_es_el_de_la_iso(self):
        config = _leer(ROOT, "build", "config.env")
        self.assertIsNotNone(re.search(r"^KB_LAYOUT=%s$" % A.DEFECTOS["teclado.distribucion"], config, re.M))

    def test_hyprland_carga_los_ajustes_al_final(self):
        conf = _leer(SKEL, "hypr", "hyprland.conf")
        linea = "source = ~/.config/hypr/infinity-ajustes.conf"
        self.assertIn(linea, conf)
        self.assertGreater(conf.index(linea), conf.rindex("bindm ="))

    def test_bloqueo_al_encender_y_no_al_entrar_con_tuigreet(self):
        # Solo la configuración: greetd marca el primer inicio (initial_session) y tuigreet no; hyprland.conf
        # lanza infinity-lock --arranque y deja Super + L como bindl (activo con la pantalla bloqueada).
        # Que infinity-lock bloquee o no según esas marcas lo comprueba test_bloqueo_arranque.py.
        with open(os.path.join(ROOT, "overlay", "etc", "greetd", "config.toml"), "rb") as f:
            greetd = tomllib.load(f)
        self.assertIn("INFINITY_BLOQUEO_ARRANQUE=1", greetd["initial_session"]["command"])
        self.assertNotIn("INFINITY_BLOQUEO_ARRANQUE", greetd["default_session"]["command"])
        conf = _leer(SKEL, "hypr", "hyprland.conf")
        self.assertIn("exec-once = infinity-lock --arranque", conf)
        self.assertRegex(conf, r"(?m)^bindl = \$mod, L,")

    def test_opacidad_por_defecto_es_la_del_css(self):
        style = _leer(STYLE)
        self.assertIn("rgba(18, 22, 44, 0.42)", style)
        self.assertIn(".glass", style)
        self.assertIn("button.orb", style)
        self.assertIn("rgba(18, 22, 44, 0.42)", A.estilo_css(A.DEFECTOS))


class TestPanki(unittest.TestCase):
    """La página «pAnkI (IA)»: modelo, comandos y burbuja. La clave de Gemini NO es un ajuste."""

    def ajuste(self, id):
        return next(a for a in A.SCHEMA if a.id == id)

    def valida(self, **cambios):
        valores = dict(A.DEFECTOS)
        valores.update({k.replace("__", "."): v for k, v in cambios.items()})
        return A.validar(valores)

    def test_la_pagina_va_antes_de_atajos(self):
        claves = [p for p, _ in A.PAGINAS]
        self.assertIn("panki", claves)
        self.assertEqual(claves.index("panki") + 1, claves.index("atajos"))
        self.assertEqual(dict(A.PAGINAS)["panki"], "pAnkI (IA)")

    def test_los_tres_ajustes_y_sus_defectos(self):
        self.assertEqual(A.DEFECTOS["panki.modelo"], "gemini-flash-latest")
        self.assertIs(A.DEFECTOS["panki.comandos"], False)
        self.assertIs(A.DEFECTOS["panki.burbuja"], True)
        for id in ("panki.modelo", "panki.comandos", "panki.burbuja"):
            self.assertEqual(self.ajuste(id).pagina, "panki", id)
        self.assertEqual(self.ajuste("panki.modelo").tipo, "texto")
        self.assertEqual(self.ajuste("panki.modelo").maximo, 60)
        self.assertEqual(self.ajuste("panki.comandos").tipo, "bool")
        self.assertEqual(self.ajuste("panki.burbuja").tipo, "bool")
        self.assertEqual(self.ajuste("panki.burbuja").etiqueta, "Mostrar la burbuja de pAnkI en el escritorio")

    def test_modelo_y_comandos_no_recargan_nada_y_la_burbuja_recarga_el_shell(self):
        self.assertEqual(self.ajuste("panki.modelo").aplica, "conversacion")
        self.assertEqual(self.ajuste("panki.comandos").aplica, "conversacion")
        self.assertEqual(self.ajuste("panki.burbuja").aplica, "shell")
        self.assertEqual(A.ACCION_POR_APLICA["conversacion"], ())
        self.assertEqual(A.acciones_para(["panki.modelo", "panki.comandos"]), [])
        self.assertEqual(A.acciones_para(["panki.burbuja"]), ["shell"])

    def test_modelo_acepta_nombres_de_modelo_y_rechaza_lo_raro(self):
        for bueno in ("gemini-flash-latest", "gemini-2.5-flash", "gemini-3-pro-preview", "gemini_x.1"):
            self.assertEqual(self.valida(panki__modelo=bueno)[1], [], bueno)
        self.assertEqual(self.valida(panki__modelo="  gemini-2.5-flash ")[0]["panki.modelo"], "gemini-2.5-flash")
        for malo in ("", "   ", "x" * 61, "gemini\nflash", "models/gemini", "a;b", "gemini?key=1", 7, None):
            self.assertEqual([i for i, _ in self.valida(panki__modelo=malo)[1]], ["panki.modelo"], repr(malo))
        self.assertEqual(self.valida(panki__modelo="x" * 60)[1], [])

    def test_comandos_y_burbuja_son_booleanos_estrictos(self):
        for id in ("panki__comandos", "panki__burbuja"):
            for malo in ("true", 1, 0, None, "si"):
                self.assertEqual(len(self.valida(**{id: malo})[1]), 1, f"{id}={malo!r}")
        self.assertIs(self.valida(panki__comandos=True)[0]["panki.comandos"], True)
        self.assertIs(self.valida(panki__burbuja=False)[0]["panki.burbuja"], False)

    def test_ida_y_vuelta_por_el_fichero(self):
        with tempfile.TemporaryDirectory() as tmp:
            ruta = os.path.join(tmp, "infinity", "ajustes.json")
            valores = dict(A.DEFECTOS, **{"panki.modelo": "gemini-2.5-flash", "panki.comandos": True,
                                          "panki.burbuja": False})
            A.guardar_json(valores, ruta)
            self.assertEqual(A.cargar(ruta), (valores, []))
            self.assertEqual(A.valor("panki.modelo", ruta), "gemini-2.5-flash")
            self.assertIs(A.valor("panki.comandos", ruta), True)
            self.assertEqual(A.valor("panki.comandos", os.path.join(tmp, "no-existe.json")), False)

    def test_la_clave_de_gemini_no_es_un_ajuste(self):
        # ajustes.json se exporta: ahí no puede haber nada con aspecto de clave.
        for a in A.SCHEMA:
            self.assertNotRegex(a.id.lower(), r"clave|key|token|secret", a.id)

    def test_los_ajustes_de_panki_no_tocan_los_ficheros_generados(self):
        base = A.generar(A.DEFECTOS)
        cambiado = A.generar(dict(A.DEFECTOS, **{"panki.modelo": "otro-modelo", "panki.comandos": True,
                                                 "panki.burbuja": False}))
        self.assertEqual(base, cambiado)


class TestFicherosGenerados(unittest.TestCase):
    def cambia(self, **cambios):
        v = dict(A.DEFECTOS)
        v.update({k.replace("__", "."): x for k, x in cambios.items()})
        return v

    def test_hypr_refleja_los_valores(self):
        conf = A.hypr_conf(self.cambia(escritorio__escala=1.5, cristal__desenfoque=False,
                                       raton__sensibilidad=-0.5, teclado__distribucion="es"))
        self.assertIn("monitor = , preferred, auto, 1.5", conf)
        self.assertIn("kb_layout    = es", conf)
        self.assertIn("sensitivity  = -0.5", conf)
        self.assertRegex(conf, r"blur \{\n\s+enabled = false")

    def test_hypr_fondo_color(self):
        self.assertIn("background_color = rgb(20ff10)",
                      A.hypr_conf(self.cambia(escritorio__color="#20ff10")))

    def test_hypridle_tiempos_crecientes_siempre(self):
        casos = [dict(), dict(bloqueo__suspender=30), dict(bloqueo__minutos=1, bloqueo__apagar_pantalla=2),
                 dict(bloqueo__minutos=120, bloqueo__apagar_pantalla=180, bloqueo__suspender=480)]
        for c in casos:
            tiempos = [int(t) for t in re.findall(r"timeout\s*=\s*(\d+)", A.hypridle_conf(self.cambia(**c)))]
            self.assertEqual(tiempos, sorted(tiempos), c)
            self.assertEqual(len(tiempos), len(set(tiempos)), c)

    def test_hypridle_suspender_solo_si_se_activa(self):
        self.assertNotIn("systemctl suspend", A.hypridle_conf(A.DEFECTOS))
        texto = A.hypridle_conf(self.cambia(bloqueo__suspender=30))
        self.assertIn("timeout    = 1800", texto)
        self.assertIn("systemctl suspend", texto)

    def test_hypridle_sin_bloquear_antes_de_dormir(self):
        self.assertNotIn("before_sleep_cmd", A.hypridle_conf(self.cambia(bloqueo__antes_de_dormir=False)))

    def test_fondo_y_css(self):
        generados = A.generar(self.cambia(escritorio__fondo="color", cristal__opacidad=0.6))
        self.assertEqual(generados["infinity/fondo"], "color\n")
        self.assertIn("rgba(18, 22, 44, 0.6)", generados["infinity/estilo.css"])


class TestIni(unittest.TestCase):
    def test_reemplaza_clave_y_conserva_comentarios(self):
        texto = "# mi terminal\n[main]\n# fuente\nfont=Mono:size=9\npad=18x14\n"
        nuevo = A.ini_set(texto, "main", "font", "Fira:size=12")
        self.assertEqual(nuevo, "# mi terminal\n[main]\n# fuente\nfont=Fira:size=12\npad=18x14\n")

    def test_anade_clave_a_seccion_existente(self):
        nuevo = A.ini_set("[main]\nfont=x\n\n[colors]\nalpha=1\n", "main", "pad", "2x2")
        self.assertEqual(nuevo, "[main]\nfont=x\npad=2x2\n\n[colors]\nalpha=1\n")

    def test_crea_seccion_si_falta(self):
        self.assertEqual(A.ini_set("[main]\nfont=x\n", "colors", "alpha", "0.5"),
                         "[main]\nfont=x\n\n[colors]\nalpha=0.5\n")
        self.assertEqual(A.ini_set("", "main", "k", "v"), "[main]\nk=v\n")

    def test_no_toca_la_misma_clave_de_otra_seccion(self):
        nuevo = A.ini_set("[colors]\nalpha=1\n[main]\nfont=x\n", "main", "alpha", "0.3")
        self.assertIn("[colors]\nalpha=1\n", nuevo)
        self.assertIn("alpha=0.3", nuevo)

    def test_foot_ini_cambia_fuente_y_transparencia(self):
        v = dict(A.DEFECTOS, **{"terminal.fuente": "Fira Code", "terminal.tamano": 14,
                                "terminal.opacidad": 0.8})
        texto = A.foot_ini("[main]\nfont=JetBrains Mono:size=11\npad=18x14\n[colors]\nalpha=0.55\n"
                           "background=0b0f1e\n", v)
        self.assertIn("font=Fira Code:size=14", texto)
        self.assertIn("alpha=0.8", texto)
        self.assertIn("background=0b0f1e", texto)
        self.assertIn("pad=18x14", texto)


class TestEscribir(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ajustes-base-")

    def tearDown(self):
        shutil.rmtree(self.base, ignore_errors=True)

    def test_escribe_todos_los_ficheros(self):
        A.escribir(A.DEFECTOS, self.base)
        for rel in ("infinity/ajustes.json", "hypr/infinity-ajustes.conf", "hypr/hypridle.conf",
                    "gtklock/config.ini", "foot/foot.ini", "infinity/estilo.css", "infinity/fondo",
                    "infinity/fondo-bloqueo"):
            self.assertTrue(os.path.exists(os.path.join(self.base, rel)), rel)
        self.assertEqual(A.cargar(os.path.join(self.base, "infinity", "ajustes.json")), (A.DEFECTOS, []))

    def test_conserva_lo_que_el_usuario_tenia_en_foot(self):
        os.makedirs(os.path.join(self.base, "foot"))
        with open(os.path.join(self.base, "foot", "foot.ini"), "w", encoding="utf-8") as f:
            f.write("[main]\nfont=Mono:size=11\n[colors]\nbackground=000000\nalpha=0.55\n")
        v = dict(A.DEFECTOS, **{"terminal.opacidad": 0.9})
        A.escribir(v, self.base)
        texto = _leer(self.base, "foot", "foot.ini")
        self.assertIn("background=000000", texto)
        self.assertIn("alpha=0.9", texto)

    def test_no_deja_ficheros_temporales(self):
        A.escribir(A.DEFECTOS, self.base)
        for raiz, _, nombres in os.walk(self.base):
            for n in nombres:
                self.assertFalse(n.endswith(".tmp"), n)


class TestRecarga(unittest.TestCase):
    def test_fondo_recarga_el_fondo_y_el_shell(self):
        self.assertEqual(A.acciones_para(["escritorio.fondo"]), ["wallpaper", "shell"])

    def test_orden_fijo_y_sin_repetir(self):
        ids = ["bloqueo.minutos", "escritorio.marca", "escritorio.color", "cristal.opacidad", "raton.sensibilidad"]
        self.assertEqual(A.acciones_para(ids), ["hypr", "hypridle", "shell"])

    def test_nuevo_no_recarga_nada(self):
        self.assertEqual(A.acciones_para(["terminal.tamano", "bloqueo.hora"]), [])

    def test_cambiados(self):
        antes = dict(A.DEFECTOS)
        despues = dict(antes, **{"dock.reloj": "12h"})
        self.assertEqual(A.cambiados(antes, despues), ["dock.reloj"])
        self.assertEqual(A.cambiados(antes, antes), [])

    def test_ejecutar_en_orden_y_solo_fallan_las_obligatorias(self):
        llamadas = []

        def falso(argv, detach):
            llamadas.append((argv[0], detach))
            if argv[0] == "pkill":
                return False, ""  # pkill devuelve 1 si no había nada que matar: no es un fallo
            if argv[0] == "hyprctl":
                return False, "config inválida: línea 3"
            return True, ""

        fallos = A.ejecutar_acciones(["hypr", "hypridle", "wallpaper", "shell"], correr=falso)
        self.assertEqual(fallos, [(["hyprctl", "reload"], "config inválida: línea 3")])
        self.assertEqual([c for c, _ in llamadas],
                         ["hyprctl", "pkill", "hypridle", "pkill", "infinity-wallpaper", "gapplication"])
        self.assertIn(("hypridle", True), llamadas)


class TestFondosEnAjustes(unittest.TestCase):
    """Los ajustes escritorio.fondo y bloqueo.fondo: compatibilidad con «imagen»/«color» y los ids de fondo."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ajustes-fondos-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fondos = os.path.join(self.tmp, "sistema")
        self.usuario = os.path.join(self.tmp, "usuario")
        os.makedirs(self.fondos)
        entradas = []
        for i in ("cascada", "islandia", "lago"):
            os.makedirs(os.path.join(self.fondos, i))
            for n in ("escritorio.jpg", "bloqueo-1920x1080.jpg"):
                with open(os.path.join(self.fondos, i, n), "wb") as f:
                    f.write(b"\xff\xd8\xff")
            entradas.append({"id": i, "nombre": i.capitalize(), "escritorio": f"{i}/escritorio.jpg",
                             "bloqueo": {"1920x1080": f"{i}/bloqueo-1920x1080.jpg"}})
        with open(os.path.join(self.fondos, "fondos.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "fondos": entradas}, f)
        self.rutas = F.Rutas(sistema=(self.fondos,), usuario=self.usuario, legado_escritorio=(), legado_bloqueo=())
        parche = mock.patch.object(F, "rutas_por_defecto", return_value=self.rutas)
        parche.start()
        self.addCleanup(parche.stop)

    def valida(self, **cambios):
        valores = dict(A.DEFECTOS)
        valores.update({k.replace("__", "."): v for k, v in cambios.items()})
        return A.validar(valores)

    def fondo_usuario(self, id_="u-mio", escritorio=True, bloqueo=True):
        d = os.path.join(self.usuario, id_)
        os.makedirs(d)
        if escritorio:
            with open(os.path.join(d, "escritorio.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
        if bloqueo:
            with open(os.path.join(d, "bloqueo-1920x1080.jpg"), "wb") as f:
                f.write(b"\xff\xd8\xff")
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump({"nombre": "Mío"}, f)

    # --- compatibilidad con lo que ya había
    def test_valores_antiguos_siguen_valiendo(self):
        for v in ("imagen", "color"):
            normal, errores = self.valida(escritorio__fondo=v)
            self.assertEqual((errores, normal["escritorio.fondo"]), ([], v))

    def test_los_defectos_no_cambian(self):
        self.assertEqual(A.DEFECTOS["escritorio.fondo"], "imagen")
        self.assertEqual(A.DEFECTOS["bloqueo.fondo"], "islandia")

    def test_configuracion_antigua_sin_el_ajuste_nuevo_carga_sin_migrar(self):
        ruta = os.path.join(self.tmp, "ajustes.json")
        antigua = {k: v for k, v in A.DEFECTOS.items() if k != "bloqueo.fondo"}
        antigua["escritorio.fondo"] = "color"
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump(antigua, f)
        valores, avisos = A.cargar(ruta)
        self.assertEqual(avisos, [])
        self.assertEqual((valores["escritorio.fondo"], valores["bloqueo.fondo"]), ("color", "islandia"))

    def test_cascada_es_otro_nombre_de_imagen(self):
        self.assertEqual(self.valida(escritorio__fondo="cascada")[0]["escritorio.fondo"], "imagen")

    # --- ids de fondo
    def test_acepta_ids_del_catalogo_y_del_usuario(self):
        self.fondo_usuario()
        normal, errores = self.valida(escritorio__fondo="lago", bloqueo__fondo="u-mio")
        self.assertEqual((errores, normal["escritorio.fondo"], normal["bloqueo.fondo"]), ([], "lago", "u-mio"))
        self.assertEqual(self.valida(escritorio__fondo="u-mio")[1], [])

    def test_rechaza_ids_que_no_existen_o_son_peligrosos(self):
        for malo in ("azul", "", "u-no-existe", "../../etc/passwd", "lago/..", "LAGO", " lago", "lago\n", "a" * 300,
                     None, 5, True, ["lago"], "u-../lago"):
            for ajuste in ("escritorio__fondo", "bloqueo__fondo"):
                self.assertNotEqual(self.valida(**{ajuste: malo})[1], [], (ajuste, malo))

    def test_un_fondo_solo_de_bloqueo_no_vale_para_el_escritorio(self):
        self.fondo_usuario("u-solo-bloqueo", escritorio=False)
        self.assertNotEqual(self.valida(escritorio__fondo="u-solo-bloqueo")[1], [])
        self.assertEqual(self.valida(bloqueo__fondo="u-solo-bloqueo")[1], [])

    def test_el_defecto_del_bloqueo_vale_aunque_falte_el_catalogo(self):
        with mock.patch.object(F, "rutas_por_defecto",
                               return_value=F.Rutas(sistema=(), usuario=self.usuario, legado_escritorio=(), legado_bloqueo=())):
            self.assertEqual(self.valida(bloqueo__fondo="islandia")[1], [])
            self.assertEqual(self.valida(escritorio__fondo="cascada")[0]["escritorio.fondo"], "imagen")
            self.assertEqual(self.valida(escritorio__fondo="imagen")[1], [])

    def test_sin_catalogo_el_defecto_del_bloqueo_sigue_entre_las_opciones(self):
        with mock.patch.object(F, "rutas_por_defecto",
                               return_value=F.Rutas(sistema=(), usuario=self.usuario, legado_escritorio=(), legado_bloqueo=())):
            a = next(x for x in A.SCHEMA if x.id == "bloqueo.fondo")
            self.assertEqual(list(a.opciones), [("islandia", "Islandia")])

    def test_un_fondo_borrado_en_el_json_vuelve_al_defecto_con_aviso(self):
        ruta = os.path.join(self.tmp, "ajustes.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"escritorio.fondo": "u-ya-no-esta", "bloqueo.fondo": "u-tampoco", "dock.reloj": "12h"}, f)
        valores, avisos = A.cargar(ruta)
        self.assertEqual((valores["escritorio.fondo"], valores["bloqueo.fondo"]), ("imagen", "islandia"))
        self.assertEqual(valores["dock.reloj"], "12h")      # lo demás se conserva
        self.assertEqual(len(avisos), 2)
        self.assertTrue(all("ya no existe" in a or "no existe" in a for a in avisos), avisos)

    def test_opciones_son_pares_valor_etiqueta_con_lo_que_hay(self):
        self.fondo_usuario()
        por = {a.id: a for a in A.SCHEMA}
        escritorio = list(por["escritorio.fondo"].opciones)
        self.assertEqual(escritorio[:2], [("imagen", "Cascada"), ("color", "Color sólido")])
        self.assertEqual([v for v, _ in escritorio[2:]], ["islandia", "lago", "u-mio"])    # la cascada ya es «imagen»
        self.assertEqual([v for v, _ in por["bloqueo.fondo"].opciones], ["cascada", "islandia", "lago", "u-mio"])
        for valor, etiqueta in escritorio:
            self.assertIsInstance(valor, str)
            self.assertIsInstance(etiqueta, str)

    def test_importar_ajustes_de_otro_equipo_no_se_rechaza_por_un_fondo_ajeno(self):
        ruta = os.path.join(self.tmp, "exportado.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"escritorio.fondo": "u-de-otro-pc", "bloqueo.fondo": "u-otro", "dock.reloj": "12h"}, f)
        valores, errores = A.importar(ruta)
        self.assertEqual(errores, [])
        self.assertEqual((valores["escritorio.fondo"], valores["bloqueo.fondo"], valores["dock.reloj"]),
                         ("imagen", "islandia", "12h"))

    def test_importar_ajustes_sigue_rechazando_lo_demas(self):
        ruta = os.path.join(self.tmp, "exportado.json")
        with open(ruta, "w", encoding="utf-8") as f:
            json.dump({"escritorio.fondo": 5, "dock.reloj": "99h"}, f)
        _, errores = A.importar(ruta)
        self.assertEqual({i for i, _ in errores}, {"escritorio.fondo", "dock.reloj"})

    # --- acciones y ficheros
    def test_acciones(self):
        self.assertEqual(A.acciones_para(["escritorio.fondo"]), ["wallpaper", "shell"])    # al momento
        self.assertEqual(A.acciones_para(["bloqueo.fondo"]), [])                          # en el siguiente bloqueo
        self.assertEqual(A.acciones_para(["bloqueo.fondo", "escritorio.fondo"]), ["wallpaper", "shell"])

    def test_el_ajuste_de_bloqueo_esta_en_la_pagina_de_bloqueo(self):
        a = next(x for x in A.SCHEMA if x.id == "bloqueo.fondo")
        self.assertEqual(a.pagina, "bloqueo")
        self.assertEqual(a.aplica, "bloqueo")
        self.assertEqual(a.tipo, "choice")

    def test_generar_escribe_los_dos_ficheros_que_leen_los_scripts(self):
        v = dict(A.DEFECTOS, **{"escritorio.fondo": "lago", "bloqueo.fondo": "u-mio"})
        g = A.generar(v)
        self.assertEqual(g["infinity/fondo"], "lago\n")
        self.assertEqual(g["infinity/fondo-bloqueo"], "u-mio\n")
        g = A.generar(A.DEFECTOS)
        self.assertEqual((g["infinity/fondo"], g["infinity/fondo-bloqueo"]), ("imagen\n", "islandia\n"))

    def test_escribir_deja_los_ficheros_en_la_carpeta_de_configuracion(self):
        self.fondo_usuario()
        base = os.path.join(self.tmp, "config")
        A.escribir(dict(A.DEFECTOS, **{"bloqueo.fondo": "u-mio"}), base)
        self.assertEqual(_leer(base, "infinity", "fondo-bloqueo"), "u-mio\n")
        self.assertEqual(_leer(base, "infinity", "fondo"), "imagen\n")
        valores, avisos = A.cargar(os.path.join(base, "infinity", "ajustes.json"))
        self.assertEqual((valores["bloqueo.fondo"], avisos), ("u-mio", []))


class TestRobustezDeLaValidacion(unittest.TestCase):
    """Un ajustes.json ajeno, corrupto o hecho a mano no tumba la app ni el shell (`valor` no lanza nunca)."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ajustes-robustez-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ruta = os.path.join(self.tmp, "ajustes.json")

    def escribe(self, contenido, modo="w"):
        with open(self.ruta, modo, **({} if "b" in modo else {"encoding": "utf-8"})) as f:
            f.write(contenido)

    def test_un_entero_de_cientos_de_cifras_es_un_error_y_no_una_excepcion(self):
        enorme = 10 ** 400
        for a in A.SCHEMA:
            if a.tipo in ("int", "float", "choice", "color", "texto", "bool"):
                for v in (enorme, -enorme):
                    normal, errores = A.validar({a.id: v})   # antes: OverflowError
                    self.assertEqual([i for i, _ in errores], [a.id], (a.id, v > 0))
                    self.assertEqual(normal[a.id], a.defecto)

    def test_cargar_importar_y_valor_con_un_entero_enorme(self):
        self.escribe('{"bloqueo.minutos": %s, "dock.reloj": "12h"}' % ("9" * 400))
        valores, avisos = A.cargar(self.ruta)
        self.assertEqual((valores["bloqueo.minutos"], valores["dock.reloj"]), (10, "12h"))
        self.assertEqual(len(avisos), 1)
        self.assertEqual(A.valor("dock.reloj", self.ruta), "12h")
        _, errores = A.importar(self.ruta)
        self.assertEqual([i for i, _ in errores], ["bloqueo.minutos"])

    def test_json_con_miles_de_niveles_no_tumba_nada(self):
        self.escribe("[" * 100000)
        valores, avisos = A.cargar(self.ruta)           # antes: RecursionError
        self.assertEqual((valores, len(avisos)), (A.DEFECTOS, 1))
        self.assertEqual(A.valor("dock.reloj", self.ruta), A.DEFECTOS["dock.reloj"])
        with self.assertRaises(ValueError):
            A.importar(self.ruta)

    def test_un_fichero_enorme_se_rechaza_sin_leerlo_entero(self):
        self.escribe('{"x": "' + "a" * (2 * 1024 * 1024) + '"}')
        valores, avisos = A.cargar(self.ruta)
        self.assertEqual((valores, len(avisos)), (A.DEFECTOS, 1))
        with self.assertRaises(ValueError):
            A.importar(self.ruta)

    def test_binario_o_con_marca_de_orden_de_bytes(self):
        self.escribe(b"\xff\xfe\x00\x01 no es texto", "wb")
        self.assertEqual(A.cargar(self.ruta)[0], A.DEFECTOS)
        with self.assertRaises(ValueError):
            A.importar(self.ruta)
        # El Bloc de notas de Windows guarda con BOM: es un JSON válido.
        self.escribe(b'\xef\xbb\xbf{"dock.reloj": "12h"}', "wb")
        self.assertEqual(A.cargar(self.ruta), (dict(A.DEFECTOS, **{"dock.reloj": "12h"}), []))

    def test_importar_un_directorio_o_un_fichero_que_no_existe(self):
        for ruta in (self.tmp, os.path.join(self.tmp, "no-existe.json")):
            with self.assertRaises(OSError):
                A.importar(ruta)

    def test_valores_de_cualquier_forma_nunca_lanzan(self):
        raros = [None, True, 0, -1, 2 ** 63, 10 ** 400, 1e308, float("nan"), "", " ", "\x00", "\ud800", "a" * 5000,
                 [], {}, [1], {"a": 1}, 1.5]
        for a in A.SCHEMA:
            for v in raros:
                normal, errores = A.validar({a.id: v})
                self.assertIn(a.id, normal)
                if errores:
                    self.assertEqual(normal[a.id], a.defecto)


class TestNombreDelAsistente(unittest.TestCase):
    """panki.nombre acepta lo mismo que la página de pAnkI (sanitizeNombre en app.html): si no, el nombre que
    el usuario se pone dentro de pAnkI se ve hasta que se cierra y luego «vuelve» al de antes."""

    def valida(self, nombre):
        return A.validar(dict(A.DEFECTOS, **{"panki.nombre": nombre}))

    def test_acepta_tildes_eñes_otros_alfabetos_y_emojis(self):
        for bueno in ("Lúa", "Ñandú", "Álex", "Nova 2", "Pánki!", "Ana-María", "Дружок", "小助手", "🤖 Nova"):
            normal, errores = self.valida(bueno)
            self.assertEqual((errores, normal["panki.nombre"]), ([], bueno), bueno)

    def test_recorta_y_junta_espacios_como_hace_la_pagina(self):
        self.assertEqual(self.valida("  Mi   asistente \t")[0]["panki.nombre"], "Mi asistente")

    def test_rechaza_vacio_html_control_y_demasiado_largo(self):
        for malo in ("", "   ", "<b>", "a>b", "Hola\x00", "a‍b", "\ud800", "x" * 25, "🤖" * 13, 5, None, True, ["a"]):
            self.assertEqual([i for i, _ in self.valida(malo)[1]], ["panki.nombre"], repr(malo))
        self.assertEqual(self.valida("x" * 24)[1], [])
        self.assertEqual(self.valida("🤖" * 12)[1], [])     # 24 unidades UTF-16, como cuenta JavaScript

    def test_las_demas_cadenas_siguen_siendo_ascii(self):
        self.assertNotEqual(A.validar(dict(A.DEFECTOS, **{"terminal.fuente": "Fuente Ñ"}))[1], [])


class TestEscrituraSegura(unittest.TestCase):
    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="ajustes-escritura-")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.json = os.path.join(self.base, "infinity", "ajustes.json")

    def arbol(self):
        return sorted(os.path.relpath(os.path.join(r, n), self.base) for r, _, ns in os.walk(self.base) for n in ns)

    def test_si_falla_un_fichero_no_se_cambia_ninguno(self):
        A.escribir(A.DEFECTOS, self.base)
        antes = {rel: _leer(self.base, rel) for rel in self.arbol()}
        # «hypr» es un archivo donde tendría que haber una carpeta: no se puede escribir hypr/hypridle.conf
        shutil.rmtree(os.path.join(self.base, "hypr"))
        with open(os.path.join(self.base, "hypr"), "w") as f:
            f.write("estorbo")
        antes.pop("hypr/hypridle.conf"), antes.pop("hypr/infinity-ajustes.conf")
        antes["hypr"] = "estorbo"
        with self.assertRaises(OSError):
            A.escribir(dict(A.DEFECTOS, **{"dock.reloj": "12h", "terminal.tamano": 16}), self.base)
        # antes: ajustes.json ya decía «12h» y foot.ini «size=16» con la configuración de Hyprland sin tocar
        despues = {rel: _leer(self.base, rel) for rel in self.arbol()}
        self.assertEqual(despues, antes)
        self.assertEqual(A.cargar(self.json), (A.DEFECTOS, []))

    def test_no_deja_temporales_si_falla(self):
        with open(os.path.join(self.base, "hypr"), "w") as f:
            f.write("estorbo")
        with self.assertRaises(OSError):
            A.escribir(A.DEFECTOS, self.base)
        self.assertEqual([n for n in self.arbol() if ".tmp" in n], [])

    def test_no_deja_temporales_si_falla_al_escribir_el_disco(self):
        with mock.patch.object(A.os, "fsync", side_effect=OSError(28, "No space left on device")):
            with self.assertRaises(OSError):
                A.escribir(A.DEFECTOS, self.base)
        self.assertEqual(self.arbol(), [])

    def test_un_enlace_simbolico_de_foot_ini_se_respeta(self):
        # Quien guarda sus dotfiles con stow/git tiene ~/.config/foot/foot.ini -> ~/dotfiles/foot.ini.
        dotfiles = os.path.join(self.base, "dotfiles")
        os.makedirs(dotfiles)
        os.makedirs(os.path.join(self.base, "foot"))
        with open(os.path.join(dotfiles, "foot.ini"), "w") as f:
            f.write("[main]\nfont=Mono:size=9\n")
        os.symlink(os.path.join(dotfiles, "foot.ini"), os.path.join(self.base, "foot", "foot.ini"))
        A.escribir(dict(A.DEFECTOS, **{"terminal.tamano": 15}), self.base)
        self.assertTrue(os.path.islink(os.path.join(self.base, "foot", "foot.ini")))   # antes: ya no era un enlace
        self.assertIn("size=15", _leer(dotfiles, "foot.ini"))

    def test_conserva_el_modo_del_fichero_que_sustituye(self):
        os.makedirs(os.path.join(self.base, "hypr"))
        ruta = os.path.join(self.base, "hypr", "hypridle.conf")
        with open(ruta, "w") as f:
            f.write("viejo")
        os.chmod(ruta, 0o600)
        A.escribir(A.DEFECTOS, self.base)
        self.assertEqual(os.stat(ruta).st_mode & 0o777, 0o600)
        self.assertNotEqual(_leer(ruta), "viejo")

    def test_ajustes_de_otra_version_no_se_pierden_al_guardar(self):
        os.makedirs(os.path.dirname(self.json))
        with open(self.json, "w", encoding="utf-8") as f:
            json.dump({"dock.reloj": "12h", "futuro.ajuste": {"a": [1, 2]}, "otro": "x"}, f)
        valores, _ = A.cargar(self.json)
        A.escribir(dict(valores, **{"terminal.tamano": 13}), self.base)
        with open(self.json, encoding="utf-8") as f:
            guardado = json.load(f)
        self.assertEqual(guardado["futuro.ajuste"], {"a": [1, 2]})     # antes: desaparecía
        self.assertEqual((guardado["otro"], guardado["terminal.tamano"], guardado["dock.reloj"]), ("x", 13, "12h"))

    def test_exportar_no_arrastra_lo_que_no_es_del_esquema(self):
        destino = os.path.join(self.base, "exportado.json")
        with open(destino, "w", encoding="utf-8") as f:
            json.dump({"futuro.ajuste": 1}, f)
        A.guardar_json(A.DEFECTOS, destino)
        with open(destino, encoding="utf-8") as f:
            self.assertEqual(set(json.load(f)), set(A.DEFECTOS))

    def test_ini_con_bytes_que_no_son_utf8_no_hace_fallar_el_guardado(self):
        os.makedirs(os.path.join(self.base, "foot"))
        ruta = os.path.join(self.base, "foot", "foot.ini")
        with open(ruta, "wb") as f:
            f.write(b"# fuente \xe9 en latin-1\n[main]\nfont=Mono:size=9\n")
        A.escribir(dict(A.DEFECTOS, **{"terminal.tamano": 15}), self.base)     # antes: UnicodeDecodeError
        with open(ruta, "rb") as f:
            crudo = f.read()
        self.assertIn(b"# fuente \xe9 en latin-1\n", crudo)       # el resto, byte a byte
        self.assertIn(b"size=15", crudo)

    def test_el_json_es_lo_ultimo_que_se_escribe(self):
        # Si algo falla en medio, ajustes.json sigue diciendo lo de antes y lo que ya estaba aplicado.
        ordenes = []
        real = A.os.replace
        with mock.patch.object(A.os, "replace", side_effect=lambda a, b: (ordenes.append(os.path.basename(b)), real(a, b))[1]):
            A.escribir(A.DEFECTOS, self.base)
        self.assertEqual(ordenes[-1], "ajustes.json")
        self.assertEqual(len(ordenes), 8)


class TestSalidaDeLasOrdenes(unittest.TestCase):
    def test_salida_que_no_es_utf8_no_lanza(self):
        ok, texto = A.correr_real(["sh", "-c", "printf '\\377\\376 mal' >&2; exit 1"])    # antes: UnicodeDecodeError
        self.assertFalse(ok)
        self.assertIn("mal", texto)

    def test_una_orden_que_no_existe_se_explica_sin_la_traza_de_python(self):
        ok, texto = A.correr_real(["orden-que-no-existe-xyz", "--opcion"])
        self.assertFalse(ok)
        self.assertNotIn("Errno", texto)
        self.assertNotIn("orden-que-no-existe-xyz", texto)     # el nombre ya lo pone quien la llama
        self.assertTrue(texto)
        ok, texto = A.correr_real(["orden-que-no-existe-xyz"], detach=True)
        self.assertEqual((ok, "Errno" in texto), (False, False))

    def test_una_orden_que_tarda_demasiado_no_enseña_el_argv(self):
        with mock.patch.object(A.subprocess, "run", side_effect=A.subprocess.TimeoutExpired(["hyprctl", "reload"], 15)):
            ok, texto = A.correr_real(["hyprctl", "reload"])
        self.assertFalse(ok)
        self.assertNotIn("hyprctl", texto)
        self.assertNotIn("[", texto)

    def test_quien_ha_fallado_para_reintentarlo(self):
        falso = lambda argv, detach: (argv[0] != "hyprctl", "x")      # noqa: E731
        fallos = A.ejecutar_acciones(["hypr", "hypridle", "shell"], correr=falso)
        self.assertEqual(A.acciones_fallidas(fallos), ["hypr"])
        self.assertEqual(A.acciones_fallidas([(["hypridle"], "x"), (["gapplication", "action"], "x")]),
                         ["hypridle"])
        self.assertEqual(A.ordenar_acciones({"shell", "hypr", "wallpaper"}), ["hypr", "wallpaper", "shell"])
        self.assertEqual(A.ordenar_acciones([]), [])

    def test_los_ajustes_del_bloqueo_dicen_que_se_aplican_al_bloquear(self):
        por = {a.id: a for a in A.SCHEMA}
        for id_ in ("bloqueo.hora", "bloqueo.ocultar_contrasena_s", "bloqueo.fondo"):
            self.assertEqual(por[id_].aplica, "bloqueo", id_)
        self.assertEqual(A.acciones_para(["bloqueo.hora", "bloqueo.ocultar_contrasena_s"]), [])


class TestCambiosExternos(unittest.TestCase):
    """Otro programa (pAnkI al renombrarse, la herramienta cambiar_ajuste) escribe ajustes.json mientras la
    ventana de Ajustes está abierta."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="ajustes-externos-")
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.ruta = os.path.join(self.tmp, "ajustes.json")

    def escribe(self, datos):
        with open(self.ruta, "w", encoding="utf-8") as f:
            json.dump(datos, f)

    def test_solo_lo_que_otro_cambio_y_es_valido(self):
        self.escribe({"panki.nombre": "Luna", "dock.reloj": "99h", "ventanas.hueco": 6, "terminal.tamano": 14})
        externos = A.cambios_externos(dict(A.DEFECTOS), self.ruta)
        # «99h» no vale, «hueco» es igual que antes y lo que falta en el fichero no cuenta como cambio
        self.assertEqual(externos, {"panki.nombre": "Luna", "terminal.tamano": 14})

    def test_fichero_ausente_o_roto_no_cuenta_como_cambio(self):
        self.assertEqual(A.cambios_externos(dict(A.DEFECTOS), self.ruta), {})
        with open(self.ruta, "w") as f:
            f.write("{roto")
        self.assertEqual(A.cambios_externos(dict(A.DEFECTOS), self.ruta), {})
        self.escribe([1, 2])
        self.assertEqual(A.cambios_externos(dict(A.DEFECTOS), self.ruta), {})

    def test_importar_conserva_lo_que_el_fichero_no_menciona(self):
        self.escribe({"dock.reloj": "12h"})
        base = dict(A.DEFECTOS, **{"panki.nombre": "Luna", "terminal.tamano": 14})
        valores, errores = A.importar(self.ruta, base=base)
        self.assertEqual(errores, [])
        self.assertEqual((valores["dock.reloj"], valores["panki.nombre"], valores["terminal.tamano"]),
                         ("12h", "Luna", 14))
        valores, _ = A.importar(self.ruta)          # sin base, como siempre: lo que falta vuelve al defecto
        self.assertEqual((valores["panki.nombre"], valores["terminal.tamano"]),
                         (A.DEFECTOS["panki.nombre"], A.DEFECTOS["terminal.tamano"]))


class TestCoherenciaDelEsquema(unittest.TestCase):
    """Ningún ajuste queda huérfano: o cambia un fichero generado, o lo lee el programa que lo usa."""

    # El motor Gemini de pAnkI está retirado (panki_app.py ya no lo usa): leer un ajuste ahí no es usarlo.
    MOTOR_RETIRADO = ("panki_nucleo.py", "panki_herramientas.py", "panki_texto.py")
    SOLO_ESQUEMA = ("ajustes.py", "ajustes_app.py")

    def fuentes_activas(self):
        carpeta = os.path.join(ROOT, "shell", "infinity_shell")
        texto = ""
        for n in sorted(os.listdir(carpeta)):
            if n.endswith(".py") and n not in self.MOTOR_RETIRADO + self.SOLO_ESQUEMA:
                texto += _leer(carpeta, n)
        return texto

    @staticmethod
    def otro_valor(a):
        if a.tipo == "bool":
            return not a.defecto
        if a.tipo in ("int", "float"):
            return a.maximo
        if a.tipo == "color":
            return "#102030"
        if a.tipo == "choice":
            return [o for o, _ in a.opciones if o != a.defecto][0]
        return "Otro"

    def test_cada_ajuste_se_refleja_en_un_fichero_generado_o_lo_lee_el_shell(self):
        base = A.generar(A.DEFECTOS)
        fuentes = self.fuentes_activas()
        huerfanos = []
        for a in A.SCHEMA:
            if a.tipo == "choice" and isinstance(a.opciones, A.OpcionesFondo):
                continue          # los fondos tienen sus propias pruebas (TestFondosEnAjustes)
            valores = dict(A.DEFECTOS, **{a.id: self.otro_valor(a)})
            if a.id.startswith("bloqueo."):
                valores.update({"bloqueo.apagar_pantalla": 180, "bloqueo.suspender": 480})
            if A.generar(valores, {"gtklock/config.ini": "", "foot/foot.ini": ""}) != \
                    A.generar(A.DEFECTOS, {"gtklock/config.ini": "", "foot/foot.ini": ""}):
                continue
            if re.search(r"""["']%s["']""" % re.escape(a.id), fuentes):
                continue
            huerfanos.append(a.id)
        # panki.modelo y panki.comandos: solo los lee el motor retirado. La ventana no los enseña (ajustes_app.OCULTOS).
        self.assertEqual(sorted(huerfanos), ["panki.comandos", "panki.modelo"])

    def test_los_ajustes_sin_efecto_son_justo_los_que_la_ventana_oculta(self):
        # Si alguien retoma el motor Gemini y vuelve a leer estos ajustes, esta prueba avisa de que ya no hay que
        # esconderlos; si se añade un ajuste que nadie lee, avisa de que hay que esconderlo (o conectarlo).
        try:
            import gi  # noqa: F401
            from infinity_shell import ajustes_app as X
        except (ImportError, ValueError) as e:
            self.skipTest(f"sin GTK 4: {e}")
        self.assertEqual(sorted(X.OCULTOS), ["panki.comandos", "panki.modelo"])

    def test_cada_orden_de_recarga_es_una_lista_de_argumentos_sin_shell(self):
        for accion, ordenes in A.COMANDOS.items():
            for argv, detach, obligatorio in ordenes:
                self.assertIsInstance(argv, list, accion)
                self.assertTrue(all(isinstance(x, str) and x for x in argv), accion)
                self.assertNotIn(argv[0], ("sh", "bash", "env"), accion)
                self.assertIsInstance(detach, bool)
                self.assertIsInstance(obligatorio, bool)
        self.assertEqual(set(A.ORDEN_ACCIONES), set(A.COMANDOS))
        self.assertEqual({x for v in A.ACCION_POR_APLICA.values() for x in v}, set(A.COMANDOS))


PANTALLA = bool(os.environ.get("DISPLAY"))


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestVentanaAjustes(unittest.TestCase):
    """La ventana (ajustes_app.py) con GTK 4, como test_fondos_galeria.py y test_contrasena_ventana.py: se salta sin
    pantalla y corre con xvfb-run. Nada se ejecuta de verdad: las órdenes de recarga se sustituyen por un registro."""

    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, Gio, GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        from infinity_shell import ajustes_app as X
        cls.X, cls.Gdk, cls.Gio, cls.GLib, cls.Gtk = X, Gdk, Gio, GLib, Gtk

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.config = os.path.join(self.tmp, "config")
        os.makedirs(self.config)
        self.llamadas = []      # argv de cada orden de recarga que la ventana intentó lanzar
        self.fallan = set()     # programas cuya orden falla

        def correr(argv, detach=False):
            self.llamadas.append(argv[0])
            if argv[0] in self.fallan:
                return False, "HYPRLAND_INSTANCE_SIGNATURE not set! (is hyprland running?)"
            return True, ""

        vacio = F.Rutas(sistema=(), usuario=os.path.join(self.tmp, "usuario"), legado_escritorio=(), legado_bloqueo=())
        for parche in (mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config, "XDG_DATA_HOME": self.tmp}),
                       mock.patch.object(F, "rutas_por_defecto", return_value=vacio),
                       mock.patch.object(A, "correr_real", side_effect=correr),
                       mock.patch.object(A, "leer_estado_contrasena", return_value="con"),
                       mock.patch.object(self.X, "_en_vivo", return_value=False)):
            parche.start()
            self.addCleanup(parche.stop)
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.ajustesventana{time.time_ns()}")
        self.app.register(None)

    # ---------------------------------------------------------------- ayudas
    def ventana(self):
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        return v

    def guarda(self, **valores):
        os.makedirs(os.path.join(self.config, "infinity"), exist_ok=True)
        with open(os.path.join(self.config, "infinity", "ajustes.json"), "w", encoding="utf-8") as f:
            json.dump({k.replace("__", "."): v for k, v in valores.items()}, f)

    def en_disco(self):
        with open(os.path.join(self.config, "infinity", "ajustes.json"), encoding="utf-8") as f:
            return json.load(f)

    def hijos(self, raiz):
        salida, pendientes = [], [raiz]
        while pendientes:
            w = pendientes.pop()
            salida.append(w)
            h = w.get_first_child()
            while h is not None:
                pendientes.append(h)
                h = h.get_next_sibling()
        return salida

    def textos(self, raiz):
        return [w.get_text() for w in self.hijos(raiz) if isinstance(w, self.Gtk.Label)]

    def teclea(self, spin, texto):
        """Escribe en el campo de un SpinButton sin confirmar (ni Enter ni salir del campo)."""
        campo = next(w for w in self.hijos(spin) if isinstance(w, self.Gtk.Text))
        campo.set_text(texto)

    def dialogo(self, ruta=None, uri=None):
        fichero = self.Gio.File.new_for_uri(uri) if uri else self.Gio.File.new_for_path(ruta)

        class Falso:
            def save_finish(self, _r):
                return fichero

            open_finish = save_finish
        return Falso()

    def escribe_fichero(self, nombre, contenido, modo="w"):
        ruta = os.path.join(self.tmp, nombre)
        with open(ruta, modo, **({} if "b" in modo else {"encoding": "utf-8"})) as f:
            f.write(contenido)
        return ruta

    # ---------------------------------------------------------------- los controles
    def test_cada_tipo_de_control_lleva_y_trae_su_valor(self):
        v = self.ventana()
        cambiados = {}
        for a in A.SCHEMA:
            if a.id in self.X.GALERIAS or a.id in self.X.OCULTOS:
                continue
            if a.tipo == "bool":
                cambiados[a.id] = not a.defecto
            elif a.tipo == "choice":
                cambiados[a.id] = a.opciones[-1][0]
            elif a.tipo == "int":
                cambiados[a.id] = a.maximo
            elif a.tipo == "float":
                cambiados[a.id] = a.maximo
            elif a.tipo == "color":
                cambiados[a.id] = "#102030"
            else:
                cambiados[a.id] = "Nova 2" if a.id == "panki.nombre" else "Fira Code"
        cambiados["bloqueo.apagar_pantalla"] = 180
        cambiados["bloqueo.suspender"] = 480
        v._poner_controles(cambiados)
        leidos = v._leer_controles()
        for id_, esperado in cambiados.items():
            self.assertEqual(leidos[id_], esperado, id_)
        v._aplicar()
        self.assertEqual(v.estado.get_text().split(":")[0], "Aplicado", v.estado.get_text())
        guardado = self.en_disco()
        for id_, esperado in cambiados.items():
            self.assertEqual(guardado[id_], esperado, id_)
        self.assertEqual(A.cargar(os.path.join(self.config, "infinity", "ajustes.json")), (v.guardado, []))

    def test_un_numero_tecleado_sin_confirmar_tambien_cuenta(self):
        v = self.ventana()
        self.teclea(v.controles["terminal.tamano"], "20")
        v._aplicar()                       # antes: se guardaba el 11 de siempre
        self.assertEqual(self.en_disco()["terminal.tamano"], 20)

    def test_el_selector_de_color_no_ofrece_transparencia(self):
        v = self.ventana()
        self.assertFalse(v.controles["escritorio.color"].get_dialog().get_with_alpha())

    def test_el_color_va_y_viene_en_hexadecimal(self):
        v = self.ventana()
        for color in ("#000000", "#ffffff", "#15193a", "#fe00a1"):
            v._poner_controles({"escritorio.color": color})
            self.assertEqual(v._leer_controles()["escritorio.color"], color)

    # ---------------------------------------------------------------- errores a la vista
    def test_un_valor_invalido_se_ve_en_la_ventana_y_no_se_escribe_nada(self):
        v = self.ventana()
        v._poner_controles({"bloqueo.minutos": 30})        # apagar la pantalla sigue en 11: no cuadra
        v._aplicar()
        self.assertIn("No se ha aplicado nada", v.estado.get_text())
        self.assertIn("Apagar la pantalla tiene que ser más tarde", v.estado.get_text())
        self.assertIn("error", v.estado.get_css_classes())
        self.assertFalse(os.path.exists(os.path.join(self.config, "infinity", "ajustes.json")))
        self.assertEqual(self.llamadas, [])

    def test_un_texto_con_caracteres_raros_se_explica(self):
        v = self.ventana()
        v._poner_controles({"terminal.fuente": "Mono; rm -rf ~"})
        v._aplicar()
        self.assertIn("Tipografía de la terminal", v.estado.get_text())
        self.assertIn("error", v.estado.get_css_classes())
        self.assertEqual(self.llamadas, [])

    def test_si_no_se_puede_guardar_se_dice_y_no_se_cambia_nada(self):
        with open(os.path.join(self.config, "hypr"), "w") as f:     # un archivo donde tendría que haber una carpeta
            f.write("estorbo")
        v = self.ventana()
        v._poner_controles({"dock.reloj": "12h"})
        v._aplicar()
        self.assertIn("No se pudo guardar", v.estado.get_text())
        self.assertNotIn("Traceback", v.estado.get_text())
        self.assertIn("error", v.estado.get_css_classes())
        self.assertFalse(os.path.exists(os.path.join(self.config, "infinity", "ajustes.json")))
        self.assertEqual(self.llamadas, [])
        self.assertEqual(v.guardado["dock.reloj"], "24h")       # sigue siendo un cambio pendiente

    # ---------------------------------------------------------------- recarga
    def test_lo_que_falla_al_recargar_se_reintenta_y_no_se_enseña_la_salida_del_sistema(self):
        self.fallan = {"hyprctl"}
        v = self.ventana()
        v._poner_controles({"ventanas.hueco": 10})
        registro = io.StringIO()
        with contextlib.redirect_stderr(registro):
            v._aplicar()
        texto = v.estado.get_text()
        self.assertIn("Guardado, pero no se pudo aplicar", texto)
        self.assertIn("Hyprland", texto)
        self.assertNotIn("hyprctl", texto)
        self.assertNotIn("INSTANCE_SIGNATURE", texto)           # el detalle del sistema no sale en la ventana
        self.assertIn("INSTANCE_SIGNATURE", registro.getvalue())    # sino en el registro de la sesión
        self.assertEqual(self.en_disco()["ventanas.hueco"], 10)
        self.llamadas.clear()
        self.fallan = set()
        v._aplicar()                                            # antes: «No había cambios.» y no recargaba nada
        self.assertEqual(self.llamadas, ["hyprctl"])
        self.assertEqual(v.estado.get_text(), "Aplicado.")
        self.assertNotIn("error", v.estado.get_css_classes())
        self.llamadas.clear()
        v._aplicar()
        self.assertEqual((self.llamadas, v.estado.get_text()), ([], "No había cambios."))

    def test_las_acciones_van_en_orden_y_solo_las_de_lo_que_cambia(self):
        v = self.ventana()
        v._poner_controles({"bloqueo.minutos": 20, "bloqueo.apagar_pantalla": 21, "escritorio.escala": "1.5",
                            "dock.reloj": "12h"})
        v._aplicar()
        self.assertEqual(self.llamadas, ["hyprctl", "pkill", "hypridle", "gapplication"])
        self.llamadas.clear()
        v._poner_controles({"terminal.tamano": 14, "bloqueo.hora": "%H:%M"})     # no recargan nada
        v._aplicar()
        self.assertEqual(self.llamadas, [])
        self.assertEqual(v.estado.get_text(), "Aplicado: 2 cambio(s).")

    # ---------------------------------------------------------------- otro programa cambia ajustes.json
    def test_aplicar_no_devuelve_a_su_valor_viejo_lo_que_cambio_otro_programa(self):
        v = self.ventana()
        valores, _ = A.cargar()
        A.escribir(dict(valores, **{"panki.nombre": "Luna"}))          # pAnkI se renombra con Ajustes abierta
        v._poner_controles({"dock.reloj": "12h"})
        v._aplicar()
        self.assertEqual(self.en_disco()["panki.nombre"], "Luna")      # antes: volvía a «pAnkI»
        self.assertEqual(v.controles["panki.nombre"].get_text(), "Luna")
        self.assertEqual(self.en_disco()["dock.reloj"], "12h")
        self.assertEqual(self.llamadas, ["gapplication"])              # solo lo que cambió aquí

    def test_lo_que_el_usuario_toco_manda_sobre_lo_de_fuera(self):
        v = self.ventana()
        valores, _ = A.cargar()
        A.escribir(dict(valores, **{"panki.nombre": "Luna", "terminal.tamano": 14}))
        v._poner_controles({"panki.nombre": "Sol"})
        v._aplicar()
        guardado = self.en_disco()
        self.assertEqual((guardado["panki.nombre"], guardado["terminal.tamano"]), ("Sol", 14))

    def test_al_volver_a_la_ventana_se_ponen_al_dia_los_cambios_de_otro(self):
        v = self.ventana()
        valores, _ = A.cargar()
        A.escribir(dict(valores, **{"panki.nombre": "Luna"}))
        with mock.patch.object(v, "is_active", return_value=True):
            v.notify("is-active")
        self.assertEqual(v.controles["panki.nombre"].get_text(), "Luna")
        self.assertEqual(v.guardado["panki.nombre"], "Luna")
        self.assertEqual(self.llamadas, [])                      # leer no recarga nada

    # ---------------------------------------------------------------- restaurar, exportar, importar
    def test_restaurar_carga_los_defectos_y_no_escribe_hasta_aplicar(self):
        self.guarda(dock__reloj="12h", terminal__tamano=16, panki__nombre="Luna")
        v = self.ventana()
        self.assertEqual(v.controles["terminal.tamano"].get_value(), 16)
        v._restaurar()
        self.assertEqual(v._leer_controles(), dict(A.DEFECTOS))
        self.assertEqual(self.en_disco()["terminal.tamano"], 16)       # todavía no
        self.assertIn("Pulsa Aplicar", v.estado.get_text())
        v._aplicar()
        self.assertEqual(self.en_disco()["terminal.tamano"], 11)

    def test_exportar_escribe_lo_aplicado_y_importar_lo_recupera(self):
        self.guarda(dock__reloj="12h", terminal__tamano=16)
        v = self.ventana()
        v._poner_controles({"terminal.tamano": 20})                    # sin aplicar: no se exporta
        destino = os.path.join(self.tmp, "mis-ajustes.json")
        v._exportado(self.dialogo(destino), None)
        self.assertIn("Exportado", v.estado.get_text())
        with open(destino, encoding="utf-8") as f:
            exportado = json.load(f)
        self.assertEqual((exportado["terminal.tamano"], exportado["dock.reloj"]), (16, "12h"))
        self.assertEqual(set(exportado), set(A.DEFECTOS))
        v._restaurar()
        v._importado(self.dialogo(destino), None)
        self.assertEqual(v.estado.get_text(), "Importado. Pulsa Aplicar para usarlo.")
        self.assertEqual(v._leer_controles()["terminal.tamano"], 16)
        self.assertEqual(v._leer_controles()["dock.reloj"], "12h")

    def test_exportar_o_importar_un_archivo_que_no_es_de_este_equipo(self):
        v = self.ventana()
        remoto = self.dialogo(uri="http://example.com/ajustes.json")
        with mock.patch.object(A, "guardar_json") as guardar, mock.patch.object(A, "importar") as importar:
            v._exportado(remoto, None)         # antes: «Exportado a None» y escribía en ajustes.json
            self.assertIn("de este equipo", v.estado.get_text())
            self.assertIn("error", v.estado.get_css_classes())
            v._importado(remoto, None)         # antes: TypeError sin decir nada
            self.assertIn("de este equipo", v.estado.get_text())
        guardar.assert_not_called()
        importar.assert_not_called()

    def test_importar_un_archivo_ajeno_o_corrupto_no_tumba_la_ventana(self):
        v = self.ventana()
        antes = v._leer_controles()
        casos = {"hondo.json": "[" * 100000, "enorme.json": '{"bloqueo.minutos": %s}' % ("9" * 400),
                 "lista.json": "[1, 2, 3]", "texto.json": "esto no es json", "vacio.json": "",
                 "grande.json": '{"x": "' + "a" * (2 * 1024 * 1024) + '"}',
                 "malo.json": '{"bloqueo.minutos": 500, "dock.reloj": "99h"}'}
        for nombre, contenido in casos.items():
            v._importado(self.dialogo(self.escribe_fichero(nombre, contenido)), None)
            self.assertIn("error", v.estado.get_css_classes(), nombre)
            self.assertTrue(v.estado.get_text().startswith(("No se pudo importar", "No se ha importado nada")), nombre)
            self.assertEqual(v._leer_controles(), antes, nombre)
        v._importado(self.dialogo(self.escribe_fichero("binario.json", b"\xff\xfe\x00\x01", "wb")), None)
        self.assertTrue(v.estado.get_text().startswith("No se pudo importar"))
        v._importado(self.dialogo(os.path.join(self.tmp, "no-existe.json")), None)
        self.assertTrue(v.estado.get_text().startswith("No se pudo importar"))
        self.assertNotIn("Errno", v.estado.get_text())

    def test_importar_conserva_lo_que_el_archivo_no_menciona(self):
        self.guarda(panki__nombre="Luna", terminal__tamano=16)
        v = self.ventana()
        v._importado(self.dialogo(self.escribe_fichero("antiguo.json", '{"dock.reloj": "12h"}')), None)
        leidos = v._leer_controles()
        self.assertEqual((leidos["dock.reloj"], leidos["panki.nombre"], leidos["terminal.tamano"]), ("12h", "Luna", 16))

    def test_un_ajustes_json_corrupto_al_abrir_avisa_y_la_ventana_funciona(self):
        os.makedirs(os.path.join(self.config, "infinity"))
        with open(os.path.join(self.config, "infinity", "ajustes.json"), "w") as f:
            f.write("[" * 100000)
        v = self.ventana()
        self.assertIn("error", v.estado.get_css_classes())
        self.assertIn("se usan los valores por defecto", v.estado.get_text())
        self.assertEqual(v._leer_controles(), dict(A.DEFECTOS))

    # ---------------------------------------------------------------- coherencia
    def test_no_se_enseñan_ajustes_que_no_hacen_nada_pero_se_conservan(self):
        self.guarda(panki__modelo="gemini-2.5-flash", panki__comandos=True)
        v = self.ventana()
        for id_ in ("panki.modelo", "panki.comandos"):
            self.assertNotIn(id_, v.controles)
        todo = " ".join(self.textos(v.pila.get_child_by_name("panki")))
        self.assertNotIn("Gemini", todo)
        self.assertNotIn("comandos de terminal", todo)
        self.assertIn("Nombre de tu asistente", todo)
        v._poner_controles({"panki.nombre": "Luna"})
        v._aplicar()
        guardado = self.en_disco()
        self.assertEqual((guardado["panki.modelo"], guardado["panki.comandos"]), ("gemini-2.5-flash", True))
        v._restaurar()
        self.assertEqual((v._leer_controles()["panki.modelo"], v._leer_controles()["panki.comandos"]),
                         (A.DEFECTOS["panki.modelo"], A.DEFECTOS["panki.comandos"]))

    def test_todo_ajuste_visible_tiene_control_y_su_ayuda_dice_cuando_se_aplica(self):
        v = self.ventana()
        for a in A.SCHEMA:
            if a.id in self.X.OCULTOS:
                continue
            self.assertIn(a.id, v.controles, a.id)
            pagina = " ".join(self.textos(v.pila.get_child_by_name(a.pagina)))
            self.assertIn(a.etiqueta, pagina, a.id)
            if a.aplica in self.X.AYUDA_APLICA:
                self.assertIn(self.X.AYUDA_APLICA[a.aplica], pagina, a.id)


if __name__ == "__main__":
    unittest.main()
