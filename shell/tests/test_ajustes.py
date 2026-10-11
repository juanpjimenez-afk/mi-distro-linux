import json
import os
import re
import shutil
import sys
import tempfile
import tomllib
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import ajustes as A  # noqa: E402

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
                    "gtklock/config.ini", "foot/foot.ini", "infinity/estilo.css", "infinity/fondo"):
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


if __name__ == "__main__":
    unittest.main()
