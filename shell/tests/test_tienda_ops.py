"""La lógica de la Tienda sin ventana: estados, planes, lectura de la salida, ejecución real de órdenes y la cola."""
import os
import stat
import sys
import tempfile
import textwrap
import threading
import unittest
from collections import namedtuple

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import tienda_catalogo as C  # noqa: E402
from infinity_shell import tienda_ops as O  # noqa: E402
from infinity_shell import tienda_webapps as W  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")
SVG = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256"><rect width="256" height="256"/></svg>'


def _cat():
    return C.cargar(CATALOGO)


def _sin_flatpak(_ref):
    return False


class Base(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.datos = os.path.join(tmp.name, "datos")
        self.bin = os.path.join(tmp.name, "bin")
        os.makedirs(self.bin)
        self.cat = _cat()

    def falso(self, *nombres, contenido="#!/bin/sh\n"):
        for n in nombres:
            ruta = os.path.join(self.bin, n)
            with open(ruta, "w") as f:
                f.write(contenido)
            os.chmod(ruta, 0o755)

    def buscar(self, nombre):
        ruta = os.path.join(self.bin, nombre)
        return ruta if os.path.exists(ruta) else None


# =============================================================================================== estado y espacio
class TestEstados(Base):
    def test_estados_dpkg_lee_los_instalados_con_una_sola_llamada(self):
        llamadas = []

        def correr(argv):
            llamadas.append(argv)
            return "google-chrome-stable\tii \t141.0.1\nbrave-browser\trc \t1.2\nantigravity\tii \t1.23.2\n"
        r = O.estados_dpkg(["google-chrome-stable", "brave-browser", "antigravity", "otro"], correr)
        self.assertEqual(r, {"google-chrome-stable": "141.0.1", "antigravity": "1.23.2"})
        self.assertEqual(len(llamadas), 1)
        self.assertEqual(llamadas[0][:2], ["dpkg-query", "-W"])
        self.assertEqual(llamadas[0][3:], ["google-chrome-stable", "brave-browser", "antigravity", "otro"])

    def test_sin_paquetes_no_llama_a_nada(self):
        self.assertEqual(O.estados_dpkg([], lambda a: self.fail("no debe llamar")), {})
        self.assertEqual(O.estados_dpkg(["", None], lambda a: self.fail("no debe llamar")), {})

    def test_salida_rara_de_dpkg_se_ignora(self):
        self.assertEqual(O.estados_dpkg(["x"], lambda a: "basura\n\n\t\t\nx\tii \t1\n"), {"x": "1"})

    def test_consultar_estados_de_todo_el_catalogo(self):
        os.makedirs(os.path.join(self.datos, "flatpak", "app", "com.spotify.Client", "current"))
        self.falso("google-chrome-stable")
        W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=None, buscar=self.buscar, flatpak=_sin_flatpak,
                          rasterizar=None)
        est = O.consultar_estados(self.cat, self.datos, dpkg=lambda a: "antigravity\tii \t1.23.2\n",
                                  buscar=self.buscar, flatpak=_sin_flatpak)
        self.assertEqual(set(est), {a.id for a in self.cat.apps})
        self.assertTrue(est["spotify"].instalada)
        self.assertEqual(est["antigravity"], O.Estado(True, "1.23.2", True, ""))
        self.assertTrue(est["claude"].instalada and est["claude"].funciona)
        for ident in ("netflix", "chrome", "brave", "gemini"):
            self.assertEqual(est[ident], O.SIN_INSTALAR, ident)

    def test_una_webapp_sin_su_navegador_sale_instalada_pero_no_funciona(self):
        self.falso("google-chrome-stable")
        W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=None, buscar=self.buscar, flatpak=_sin_flatpak,
                          rasterizar=None)
        os.unlink(os.path.join(self.bin, "google-chrome-stable"))
        e = O.consultar_estados(self.cat, self.datos, dpkg=lambda a: "", buscar=lambda n: None, flatpak=_sin_flatpak)
        self.assertTrue(e["claude"].instalada)
        self.assertFalse(e["claude"].funciona)
        self.assertIn("navegador", e["claude"].detalle)


class TestEspacio(Base):
    class V:
        def __init__(self, mb):
            self.f_bavail, self.f_frsize = mb * 256, 4096

    def test_hay_sitio(self):
        self.assertIsNone(O.comprobar_espacio(self.cat.por_id["spotify"], lambda p: self.V(5000), home="/h"))

    def test_poco_sitio_dice_donde_y_cuanto(self):
        a = O.comprobar_espacio(self.cat.por_id["spotify"], lambda p: self.V(300), home="/home/ana")
        self.assertEqual((a.libre_mb, a.necesario_mb, a.sitio), (300, 1200, "/home/ana"))
        a = O.comprobar_espacio(self.cat.por_id["antigravity"], lambda p: self.V(100))
        self.assertEqual((a.libre_mb, a.necesario_mb, a.sitio), (100, 840, "/"))

    def test_webapps_o_sin_tamano_no_avisan_y_los_errores_tampoco(self):
        self.assertIsNone(O.comprobar_espacio(self.cat.por_id["claude"], lambda p: self.V(0)))
        sin_tamano = self.cat.por_id["claude"]
        self.assertIsNone(O.comprobar_espacio(sin_tamano, lambda p: self.V(0)))

        def falla(_p):
            raise OSError("no")
        self.assertIsNone(O.comprobar_espacio(self.cat.por_id["spotify"], falla))


# =============================================================================================== planes
class TestPlanes(Base):
    def setUp(self):
        super().setUp()
        self.falso("flatpak", "pkexec")
        self.ayudante = os.path.join(self.bin, "tienda-ayudante")
        self.falso("tienda-ayudante")

    def plan(self, ident, accion, **kw):
        return O.plan_para(self.cat.por_id[ident], accion, self.buscar, ayudante=self.ayudante, datos=self.datos, **kw)

    def test_flatpak_instalar_es_por_usuario_y_sin_preguntas(self):
        p = self.plan("spotify", O.INSTALAR)
        flatpak = os.path.join(self.bin, "flatpak")
        self.assertEqual([x.argv for x in p.pasos], [
            [flatpak, "remote-add", "--user", "--if-not-exists", "flathub", "https://dl.flathub.org/repo/flathub.flatpakrepo"],
            [flatpak, "install", "--user", "-y", "--noninteractive", "flathub", "com.spotify.Client"]])
        self.assertTrue(all(x.funcion is None for x in p.pasos))

    def test_flatpak_quitar_y_actualizar(self):
        flatpak = os.path.join(self.bin, "flatpak")
        self.assertEqual(self.plan("spotify", O.QUITAR).pasos[0].argv,
                         [flatpak, "uninstall", "--user", "-y", "--noninteractive", "com.spotify.Client"])
        self.assertEqual(self.plan("spotify", O.ACTUALIZAR).pasos[0].argv,
                         [flatpak, "update", "--user", "-y", "--noninteractive", "com.spotify.Client"])

    def test_apt_va_por_pkexec_y_solo_con_la_accion_y_el_id(self):
        for accion in O.ACCIONES:
            p = self.plan("antigravity", accion)
            self.assertEqual(len(p.pasos), 1)
            self.assertEqual(p.pasos[0].argv, [os.path.join(self.bin, "pkexec"), self.ayudante, accion, "antigravity"])

    def test_los_comandos_no_llevan_nada_del_catalogo_salvo_el_id_o_la_ref(self):
        p = self.plan("chrome", O.INSTALAR)
        self.assertNotIn("google-chrome-stable", " ".join(p.pasos[0].argv))        # ni el paquete ni la URL
        self.assertNotIn("https://", " ".join(p.pasos[0].argv))

    def test_webapp_es_una_funcion_que_crea_y_quita_el_lanzador(self):
        self.falso("google-chrome-stable")
        p = O.plan_para(self.cat.por_id["claude"], O.INSTALAR, self.buscar, datos=self.datos)
        self.assertEqual(len(p.pasos), 1)
        self.assertIsNone(p.pasos[0].argv)
        # la función usa shutil.which real: sin chrome en el PATH real da ErrorPlan con un mensaje claro
        viejo = os.environ["PATH"]
        os.environ["PATH"] = self.bin
        self.addCleanup(lambda: os.environ.__setitem__("PATH", viejo))
        p.pasos[0].funcion()
        self.assertTrue(os.path.exists(W.ruta_desktop("claude", self.datos)))
        q = O.plan_para(self.cat.por_id["claude"], O.QUITAR, self.buscar, datos=self.datos)
        q.pasos[0].funcion()
        self.assertFalse(os.path.exists(W.ruta_desktop("claude", self.datos)))

    def test_webapp_sin_navegador_da_error_claro(self):
        viejo = os.environ["PATH"]
        os.environ["PATH"] = self.bin
        self.addCleanup(lambda: os.environ.__setitem__("PATH", viejo))
        p = O.plan_para(self.cat.por_id["claude"], O.INSTALAR, self.buscar, datos=self.datos)
        with self.assertRaises(O.ErrorPlan) as cm:
            p.pasos[0].funcion()
        self.assertIn("Chromium", str(cm.exception))

    def test_faltan_herramientas(self):
        os.unlink(os.path.join(self.bin, "flatpak"))
        with self.assertRaises(O.ErrorPlan) as cm:
            self.plan("spotify", O.INSTALAR)
        self.assertIn("Flatpak", str(cm.exception))
        os.unlink(os.path.join(self.bin, "pkexec"))
        with self.assertRaises(O.ErrorPlan) as cm:
            self.plan("chrome", O.INSTALAR)
        self.assertIn("pkexec", str(cm.exception))

    def test_falta_el_ayudante(self):
        os.unlink(self.ayudante)
        with self.assertRaises(O.ErrorPlan) as cm:
            self.plan("chrome", O.INSTALAR)
        self.assertIn("ayudante", str(cm.exception))

    def test_accion_invalida(self):
        with self.assertRaises(O.ErrorPlan):
            self.plan("spotify", "formatear")


# =============================================================================================== lectura de la salida
class TestInterpretar(unittest.TestCase):
    def test_lineas_del_ayudante(self):
        i = O.interpretar_ayudante
        self.assertEqual(i("@@PROGRESO 45 Instalando X…"), O.Evento("progreso", 0.45, "Instalando X…"))
        self.assertEqual(i("@@PROGRESO 100"), O.Evento("progreso", 1.0, ""))
        self.assertEqual(i("@@PROGRESO 250 a"), O.Evento("progreso", 1.0, "a"))
        self.assertEqual(i("@@ARCHIVO antigravity.desktop"), O.Evento("archivo", "antigravity.desktop", "antigravity.desktop"))
        self.assertEqual(i("@@ERROR No hay red"), O.Evento("error", None, "No hay red"))
        self.assertEqual(i("@@LISTO").tipo, "listo")
        self.assertEqual(i("  Get:1 http://x"), O.Evento("log", None, "Get:1 http://x"))

    def test_lo_que_no_es_de_control_es_registro(self):
        for linea in ("@@PROGRESO", "@@PROGRESO x y", "@@RARA 1", "texto", ""):
            with self.subTest(linea=linea):
                self.assertEqual(O.interpretar_ayudante(linea).tipo, "log")

    def test_flatpak_saca_el_porcentaje(self):
        i = O.interpretar_flatpak
        self.assertEqual(i("Installing… ██████░░ 45%"), O.Evento("progreso", 0.45, ""))
        self.assertEqual(i("Instalando 3/3… 100 %").valor, 1.0)
        self.assertEqual(i("Looking for matches…").tipo, "log")
        self.assertEqual(i("999%").tipo, "log")

    def test_explicar_salida(self):
        e = O.explicar_salida
        self.assertEqual(e(1, [], "Mensaje del ayudante"), "Mensaje del ayudante")
        self.assertIn("cancel", e(126, []).lower())
        self.assertIn("polkit", e(127, []))
        self.assertIn("conexión", e(1, ["error: Could not resolve host: dl.flathub.org"]))
        self.assertIn("espacio", e(1, ["error: No space left on device"]))
        self.assertIn("código 3", e(3, ["", "algo raro"]))


# =============================================================================================== ejecución real
class TestEjecutor(Base):
    def correr(self, plan, esperar=30):
        eventos, fin = [], []
        listo = threading.Event()

        def al_terminar(ok, mensaje):
            fin.append((ok, mensaje))
            listo.set()
        O.Ejecutor().ejecutar(plan, eventos.append, al_terminar)
        self.assertTrue(listo.wait(esperar), "el plan no terminó")
        return eventos, fin

    def plan_de(self, *pasos):
        return O.Plan(self.cat.por_id["antigravity"], O.INSTALAR, list(pasos))

    def script(self, cuerpo, nombre="orden"):
        ruta = os.path.join(self.bin, nombre)
        with open(ruta, "w") as f:
            f.write("#!/bin/sh\n" + textwrap.dedent(cuerpo))
        os.chmod(ruta, 0o755)
        return ruta

    def test_una_orden_buena_con_avance_registro_y_archivo(self):
        pkexec = self.script("""\
            echo "@@PROGRESO 10 Descargando la clave…"
            echo "  Get:1 https://repo"
            echo "@@PROGRESO 60 Instalando…"
            echo "@@ARCHIVO antigravity.desktop"
            echo "@@PROGRESO 100 Listo."
            echo "@@LISTO"
            """, "pkexec")
        eventos, fin = self.correr(self.plan_de(O.Paso("Instalando…", [pkexec, "x"], None)))
        self.assertEqual(fin, [(True, "")])
        tipos = [e.tipo for e in eventos]
        self.assertIn("log", tipos)
        self.assertIn(O.Evento("archivo", "antigravity.desktop", "antigravity.desktop"), eventos)
        avances = [e.valor for e in eventos if e.tipo == "progreso" and e.valor is not None]
        self.assertEqual(avances, sorted(avances))
        self.assertEqual(avances[-1], 1.0)
        self.assertIn(O.Evento("log", None, "Get:1 https://repo"), eventos)

    def test_el_error_del_ayudante_llega_tal_cual(self):
        pkexec = self.script('echo "@@ERROR No hay conexión con el servidor."\nexit 1', "pkexec")
        _eventos, fin = self.correr(self.plan_de(O.Paso("x", [pkexec], None)))
        self.assertEqual(fin, [(False, "No hay conexión con el servidor.")])

    def test_pkexec_cancelado_y_sin_agente(self):
        for codigo, texto in ((126, "cancel"), (127, "polkit")):
            pkexec = self.script(f"exit {codigo}", "pkexec")
            _e, fin = self.correr(self.plan_de(O.Paso("x", [pkexec], None)))
            self.assertFalse(fin[0][0])
            self.assertIn(texto, fin[0][1].lower())

    def test_una_orden_que_no_existe(self):
        _e, fin = self.correr(self.plan_de(O.Paso("x", ["/no/existe/orden"], None)))
        self.assertFalse(fin[0][0])
        self.assertIn("No se pudo ejecutar", fin[0][1])

    def test_dos_pasos_reparten_el_avance(self):
        a = self.script('echo "10%"\necho "100%"', "a")
        b = self.script('echo "50%"', "b")
        eventos, fin = self.correr(self.plan_de(O.Paso("uno", [a], None), O.Paso("dos", [b], None)))
        self.assertEqual(fin, [(True, "")])
        avances = [e.valor for e in eventos if e.tipo == "progreso"]
        self.assertEqual(avances, sorted(avances))
        self.assertAlmostEqual(avances[0], 0.0)
        self.assertIn(0.75, [round(v, 2) for v in avances])        # el 50 % del segundo paso = 0,5 + 0,25
        self.assertEqual(avances[-1], 1.0)

    def test_si_un_paso_falla_no_se_sigue(self):
        a = self.script('echo "boom"\nexit 3', "a")
        marca = os.path.join(self.tmp, "no-debe-existir")
        b = self.script(f'touch "{marca}"', "b")
        _e, fin = self.correr(self.plan_de(O.Paso("uno", [a], None), O.Paso("dos", [b], None)))
        self.assertFalse(fin[0][0])
        self.assertIn("código 3", fin[0][1])
        self.assertFalse(os.path.exists(marca))

    def test_pasos_de_funcion(self):
        llamadas = []
        _e, fin = self.correr(self.plan_de(O.Paso("hacer", None, lambda: llamadas.append(1))))
        self.assertEqual((fin, llamadas), ([(True, "")], [1]))

        def falla():
            raise O.ErrorPlan("falta un navegador")
        _e, fin = self.correr(self.plan_de(O.Paso("hacer", None, falla)))
        self.assertEqual(fin, [(False, "falta un navegador")])

        def rompe():
            raise KeyError("x")
        _e, fin = self.correr(self.plan_de(O.Paso("hacer", None, rompe)))
        self.assertFalse(fin[0][0])
        self.assertIn("KeyError", fin[0][1])

    def test_mucha_salida_y_lineas_sin_salto(self):
        a = self.script('i=0; while [ $i -lt 20000 ]; do echo "linea $i"; i=$((i+1)); done\nprintf "final sin salto"', "a")
        eventos, fin = self.correr(self.plan_de(O.Paso("x", [a], None)))
        self.assertEqual(fin, [(True, "")])
        logs = [e.texto for e in eventos if e.tipo == "log"]
        self.assertEqual((len(logs), logs[-1]), (20001, "final sin salto"))

    def test_el_aviso_de_fin_llega_una_sola_vez_y_por_el_despachador(self):
        despachados, fines = [], []
        pkexec = self.script("exit 0", "pkexec")
        hecho = threading.Event()

        def despachar(f, *a):
            despachados.append(threading.current_thread().name)
            f(*a)

        def fin(ok, m):
            fines.append((ok, m))
            hecho.set()
        O.Ejecutor(despachar).ejecutar(self.plan_de(O.Paso("x", [pkexec], None)), lambda e: None, fin)
        self.assertTrue(hecho.wait(20))
        self.assertEqual(fines, [(True, "")])
        self.assertTrue(despachados)
        self.assertTrue(all(n.startswith("tienda-") for n in despachados))     # todo se despachó desde el hilo de trabajo


# =============================================================================================== cómo abrir
class TestComoAbrir(Base):
    def test_webapp(self):
        self.assertIsNone(O.como_abrir(self.cat.por_id["claude"], self.datos))
        ruta = W.ruta_desktop("claude", self.datos)
        os.makedirs(os.path.dirname(ruta))
        open(ruta, "w").close()
        self.assertEqual(O.como_abrir(self.cat.por_id["claude"], self.datos), ("archivo", ruta))

    def test_apt_con_el_id_que_exista(self):
        existe = lambda r: r == "/usr/share/applications/antigravity.desktop"      # noqa: E731
        self.assertEqual(O.como_abrir(self.cat.por_id["antigravity"], existe=existe), ("id", "antigravity.desktop"))
        self.assertIsNone(O.como_abrir(self.cat.por_id["antigravity"], existe=lambda r: False))

    def test_flatpak_por_sus_exportaciones_o_flatpak_run(self):
        s = self.cat.por_id["spotify"]
        self.assertIsNone(O.como_abrir(s, self.datos))
        os.makedirs(os.path.join(self.datos, "flatpak", "app", "com.spotify.Client", "current"))
        self.assertEqual(O.como_abrir(s, self.datos), ("orden", ["flatpak", "run", "com.spotify.Client"]))
        exportado = os.path.join(self.datos, "flatpak", "exports", "share", "applications")
        os.makedirs(exportado)
        open(os.path.join(exportado, "com.spotify.Client.desktop"), "w").close()
        self.assertEqual(O.como_abrir(s, self.datos), ("id", "com.spotify.Client.desktop"))


# =============================================================================================== el gestor y su cola
class EjecutorManual:
    """No ejecuta nada: guarda cada plan y deja que la prueba lo termine cuando quiera."""

    def __init__(self):
        self.pendientes = []
        self.historial = []

    def ejecutar(self, plan, al_evento, al_terminar):
        self.pendientes.append((plan, al_evento, al_terminar))
        self.historial.append(plan)

    def terminar(self, ok=True, mensaje="", indice=0):
        plan, _ev, fin = self.pendientes.pop(indice)
        fin(ok, mensaje)
        return plan


class TestGestor(Base):
    def setUp(self):
        super().setUp()
        self.falso("flatpak", "pkexec", "tienda-ayudante")
        self.ej = EjecutorManual()
        self.instaladas = {}                  # id → Estado (lo que "ve" el sistema)
        self.cambios = []
        self.espacio = {"hay": True}
        self._parches = []
        self.parchear(O, "AYUDANTE", os.path.join(self.bin, "tienda-ayudante"))
        self.parchear(O, "comprobar_espacio", lambda e, statvfs=None, home=None: (
            None if self.espacio["hay"] else O.AvisoEspacio(10, 500, "/")))
        self.navegadores = []

    def parchear(self, modulo, nombre, valor):
        original = getattr(modulo, nombre)
        setattr(modulo, nombre, valor)
        self.addCleanup(setattr, modulo, nombre, original)

    def gestor(self):
        g = O.Gestor(self.cat, ejecutor=self.ej, estados=lambda: {a.id: self.instaladas.get(a.id, O.SIN_INSTALAR) for a in self.cat.apps},
                     buscar=self.buscar, datos=self.datos)
        # plan_para real, pero con el ayudante falso (la constante por defecto se evaluó al definir la función)
        original = O.plan_para
        g._plan = lambda *a, **k: original(*a, ayudante=os.path.join(self.bin, "tienda-ayudante"), **k)
        self.parchear(O, "plan_para", lambda e, a, b=None, ayudante=None, preferido=None, datos=None: original(
            e, a, b, ayudante=os.path.join(self.bin, "tienda-ayudante"), preferido=preferido, datos=datos))
        g.al_cambiar = self.cambios.append
        self.terminados = []
        g.al_terminar_trabajo = self.terminados.append
        return g

    def test_instalar_un_flatpak_y_ver_el_resultado(self):
        g = self.gestor()
        r = g.pedir("spotify", O.INSTALAR)
        self.assertEqual(r.estado, "ok")
        self.assertTrue(g.ocupado)
        t = g.trabajo("spotify")
        self.assertEqual((t.estado, t.accion), ("en_curso", O.INSTALAR))
        self.assertEqual(self.ej.pendientes[0][0].entrada.id, "spotify")
        self.instaladas["spotify"] = O.Estado(True, "", True, "")
        self.ej.terminar(True)
        self.assertFalse(g.ocupado)
        self.assertIsNone(g.trabajo("spotify"))
        self.assertEqual(g.trabajos["spotify"].mensaje, "Spotify instalada.")
        self.assertTrue(g.estado("spotify").instalada)
        self.assertEqual(self.terminados[0].entrada.id, "spotify")
        self.assertIn("spotify", self.cambios)

    def test_un_fallo_se_cuenta_en_el_mensaje(self):
        g = self.gestor()
        g.pedir("antigravity", O.INSTALAR)
        self.ej.terminar(False, "No hay conexión.")
        t = g.trabajos["antigravity"]
        self.assertEqual((t.estado, t.mensaje), ("fallo", "No se pudo instalar Google Antigravity. No hay conexión."))
        self.assertFalse(g.estado("antigravity").instalada)
        self.assertEqual(g.pedir("antigravity", O.INSTALAR).estado, "ok")     # y se puede reintentar

    def test_eventos_de_progreso_y_registro(self):
        g = self.gestor()
        g.pedir("spotify", O.INSTALAR)
        _plan, al_evento, _fin = self.ej.pendientes[0]
        al_evento(O.Evento("progreso", 0.4, "Descargando…"))
        al_evento(O.Evento("log", None, "una línea"))
        al_evento(O.Evento("archivo", "x.desktop", "x.desktop"))
        t = g.trabajo("spotify")
        self.assertEqual((t.progreso, t.texto), (0.4, "Descargando…"))
        self.assertEqual(list(t.registro), ["una línea", "(lanzador: x.desktop)"])

    def test_la_cola_va_de_una_en_una_y_en_orden(self):
        g = self.gestor()
        for ident in ("spotify", "antigravity", "brave"):
            self.assertEqual(g.pedir(ident, O.INSTALAR).estado, "ok")
        self.assertEqual(g.trabajo("spotify").estado, "en_curso")
        self.assertEqual(g.trabajo("antigravity").estado, "en_cola")
        self.assertEqual(g.trabajo("brave").estado, "en_cola")
        self.assertEqual(len(self.ej.pendientes), 1)
        self.ej.terminar(True)
        self.assertEqual(g.trabajo("antigravity").estado, "en_curso")
        self.ej.terminar(False, "x")
        self.assertEqual(g.trabajo("brave").estado, "en_curso")
        self.ej.terminar(True)
        self.assertEqual([p.entrada.id for p in self.ej.historial], ["spotify", "antigravity", "brave"])
        self.assertFalse(g.ocupado)

    def test_misma_app_dos_veces_es_ocupado(self):
        g = self.gestor()
        g.pedir("spotify", O.INSTALAR)
        self.assertEqual(g.pedir("spotify", O.QUITAR).estado, "ocupado")

    def test_sacar_de_la_cola(self):
        g = self.gestor()
        g.pedir("spotify", O.INSTALAR)
        g.pedir("brave", O.INSTALAR)
        self.assertFalse(g.quitar_de_la_cola("spotify"))      # en curso: no se puede
        self.assertTrue(g.quitar_de_la_cola("brave"))
        self.assertIsNone(g.trabajo("brave"))
        self.ej.terminar(True)
        self.assertEqual(len(self.ej.historial), 1)

    def test_apps_desconocidas_o_acciones_raras(self):
        g = self.gestor()
        for ident, accion in (("no-existe", O.INSTALAR), ("spotify", "formatear"), ("../x", O.INSTALAR)):
            self.assertEqual(g.pedir(ident, accion).estado, "error", (ident, accion))
        self.assertFalse(g.ocupado)

    def test_falta_una_herramienta_es_error_y_no_deja_nada_en_cola(self):
        g = self.gestor()
        os.unlink(os.path.join(self.bin, "flatpak"))
        r = g.pedir("spotify", O.INSTALAR)
        self.assertEqual(r.estado, "error")
        self.assertIn("Flatpak", r.mensaje)
        self.assertFalse(g.ocupado)
        self.assertIsNone(g.trabajo("spotify"))

    # -- WebApps y navegador
    def test_webapp_sin_navegador_pregunta_cual_instalar(self):
        g = self.gestor()
        r = g.pedir("netflix", O.INSTALAR)
        self.assertEqual(r.estado, "falta_navegador")
        self.assertEqual([n.id for n in r.opciones], ["chrome", "brave"])
        self.assertFalse(g.ocupado)

    def test_elegido_el_navegador_se_instala_primero_y_luego_la_webapp(self):
        g = self.gestor()
        r = g.pedir("netflix", O.INSTALAR, navegador="chrome")
        self.assertEqual(r.estado, "ok")
        self.assertEqual(g.trabajo("chrome").estado, "en_curso")
        self.assertEqual(g.trabajo("netflix").estado, "en_cola")
        self.falso("google-chrome-stable")                 # el navegador "se instaló"
        self.ej.terminar(True)
        self.assertEqual(g.trabajo("netflix").estado, "en_curso")
        self.ej.terminar(True)
        self.assertEqual([p.entrada.id for p in self.ej.historial], ["chrome", "netflix"])
        self.assertEqual(g.trabajos["netflix"].mensaje, "Netflix instalada.")

    def test_si_falla_el_navegador_la_webapp_no_se_intenta(self):
        g = self.gestor()
        g.pedir("netflix", O.INSTALAR, navegador="brave")
        self.ej.terminar(False, "Sin conexión.")
        self.assertEqual(len(self.ej.historial), 1)
        t = g.trabajos["netflix"]
        self.assertEqual(t.estado, "fallo")
        self.assertIn("navegador", t.mensaje)
        self.assertFalse(g.ocupado)

    def test_un_navegador_que_no_es_del_catalogo_se_rechaza(self):
        g = self.gestor()
        for malo in ("spotify", "no-existe", "netflix"):
            self.assertEqual(g.pedir("netflix", O.INSTALAR, navegador=malo).estado, "error", malo)
        self.assertFalse(g.ocupado)

    def test_con_navegador_instalado_no_se_pregunta(self):
        self.falso("google-chrome-stable")
        g = self.gestor()
        self.assertEqual(g.pedir("claude", O.INSTALAR).estado, "ok")
        self.assertEqual(len(g.cola) + (1 if g.actual else 0), 1)
        self.assertEqual(len(self.ej.historial), 1)

    def test_quitar_una_webapp_no_pide_navegador(self):
        g = self.gestor()
        self.assertEqual(g.pedir("claude", O.QUITAR).estado, "ok")

    def test_instalar_un_navegador_arregla_los_lanzadores_rotos(self):
        # una WebApp instalada con Brave, y Brave se va: al instalar Chrome se rehace sola
        self.falso("brave-browser")
        W.instalar_webapp(self.cat.por_id["claude"], datos=self.datos, svg=None, buscar=self.buscar, flatpak=_sin_flatpak,
                          rasterizar=None)
        os.unlink(os.path.join(self.bin, "brave-browser"))
        g = self.gestor()
        g.pedir("chrome", O.INSTALAR)
        self.falso("google-chrome-stable")
        viejo = os.environ["PATH"]
        os.environ["PATH"] = self.bin
        self.addCleanup(lambda: os.environ.__setitem__("PATH", viejo))
        self.ej.terminar(True)
        self.assertEqual(W.webapp_instalada("claude", self.datos, _sin_flatpak, self.buscar).navegador, "chrome")

    # -- espacio
    def test_poco_espacio_avisa_y_con_forzar_sigue(self):
        self.espacio["hay"] = False
        g = self.gestor()
        r = g.pedir("spotify", O.INSTALAR)
        self.assertEqual(r.estado, "poco_espacio")
        self.assertIn("Spotify", r.mensaje)
        self.assertFalse(g.ocupado)
        self.assertEqual(g.pedir("spotify", O.INSTALAR, forzar=True).estado, "ok")

    def test_el_espacio_de_un_navegador_tambien_cuenta_y_no_deja_nada_a_medias(self):
        self.espacio["hay"] = False
        g = self.gestor()
        r = g.pedir("netflix", O.INSTALAR, navegador="chrome")
        self.assertEqual(r.estado, "poco_espacio")
        self.assertIn("Google Chrome", r.mensaje)
        self.assertFalse(g.ocupado)
        self.assertEqual(g.pedir("netflix", O.INSTALAR, navegador="chrome", forzar=True).estado, "ok")

    def test_quitar_o_actualizar_no_miran_el_espacio(self):
        self.espacio["hay"] = False
        g = self.gestor()
        self.assertEqual(g.pedir("spotify", O.QUITAR).estado, "ok")

    # -- estado
    def test_refrescar_vuelve_a_leer_el_sistema(self):
        g = self.gestor()
        self.assertFalse(g.estado("brave").instalada)
        self.instaladas["brave"] = O.Estado(True, "1.0", True, "")
        g.refrescar()
        self.assertTrue(g.estado("brave").instalada)
        self.assertIsNone(self.cambios[-1])
        self.assertEqual(g.estado("no-existe"), O.SIN_INSTALAR)


if __name__ == "__main__":
    unittest.main()
