"""La Tienda desde la terminal: lo mismo que la ventana, con un gestor de pruebas (sin instalar nada de verdad)."""
import io
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from infinity_shell import tienda_catalogo as C  # noqa: E402
from infinity_shell import tienda_cli as CLI  # noqa: E402
from infinity_shell import tienda_ops as O  # noqa: E402

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CATALOGO = os.path.join(ROOT, "overlay", "usr", "share", "infinity", "tienda", "catalogo.json")


class EjecutorInmediato:
    """Termina cada plan al momento, bien o mal según `resultados[id]`."""

    def __init__(self, resultados=None):
        self.resultados = resultados or {}
        self.historial = []

    def ejecutar(self, plan, al_evento, al_terminar):
        self.historial.append((plan.entrada.id, plan.accion))
        al_evento(O.Evento("progreso", 0.5, "Trabajando…"))
        ok, mensaje = self.resultados.get(plan.entrada.id, (True, ""))
        al_terminar(ok, mensaje)


class Base(unittest.TestCase):
    def setUp(self):
        self.cat = C.cargar(CATALOGO)
        self.espacio = True
        self.navegador = False
        for modulo, nombre, valor in (
                (O, "plan_para", lambda e, a, *r, **k: O.Plan(e, a, [O.Paso("x", ["/bin/true"], None)])),
                (O, "comprobar_espacio", lambda e, statvfs=None, home=None: (
                    None if self.espacio else O.AvisoEspacio(10, 500, "/")))):
            original = getattr(modulo, nombre)
            setattr(modulo, nombre, valor)
            self.addCleanup(setattr, modulo, nombre, original)

    def gestor(self, resultados=None, instaladas=None):
        self.ej = EjecutorInmediato(resultados)
        g = O.Gestor(self.cat, ejecutor=self.ej,
                     estados=lambda: {a.id: (instaladas or {}).get(a.id, O.SIN_INSTALAR) for a in self.cat.apps})
        g.navegadores_instalados = lambda: [object()] if self.navegador else []
        return g

    def correr(self, *argv, gestor=None, **kw):
        salida, error = io.StringIO(), io.StringIO()
        codigo = CLI.main(list(argv), gestor=gestor or self.gestor(**kw), salida=salida, error=error)
        return codigo, salida.getvalue(), error.getvalue()


class TestListar(Base):
    def test_lista_todas_con_su_estado(self):
        g = self.gestor(instaladas={"spotify": O.Estado(True, "", True, ""), "antigravity": O.Estado(True, "1.23.2", True, ""),
                                    "claude": O.Estado(True, "", False, "x")})
        codigo, salida, _ = self.correr("listar", gestor=g)
        self.assertEqual(codigo, 0)
        lineas = salida.splitlines()
        self.assertEqual(len(lineas), len(self.cat.apps))
        por_id = {l.split()[0]: l for l in lineas}
        self.assertIn("instalada", por_id["spotify"])
        self.assertIn("instalada 1.23.2", por_id["antigravity"])
        self.assertIn("sin navegador", por_id["claude"])
        self.assertIn("sin instalar", por_id["netflix"])
        self.assertIn("WebApp", por_id["netflix"])
        self.assertIn("Flatpak", por_id["spotify"])


class TestInstalar(Base):
    def test_instalar_bien(self):
        codigo, salida, error = self.correr("instalar", "spotify")
        self.assertEqual(codigo, 0)
        self.assertEqual(salida.strip(), "Spotify instalada.")
        self.assertIn("Trabajando", error)
        self.assertEqual(self.ej.historial, [("spotify", "instalar")])

    def test_quitar_y_actualizar(self):
        self.assertEqual(self.correr("quitar", "spotify")[1].strip(), "Spotify quitada.")
        self.assertEqual(self.correr("actualizar", "antigravity")[1].strip(), "Google Antigravity actualizada.")

    def test_fallo_da_codigo_1_y_el_motivo(self):
        codigo, salida, _ = self.correr("instalar", "spotify", resultados={"spotify": (False, "Sin conexión.")})
        self.assertEqual(codigo, 1)
        self.assertEqual(salida.strip(), "No se pudo instalar Spotify. Sin conexión.")

    def test_id_desconocido_o_ausente(self):
        codigo, _, error = self.correr("instalar", "no-existe")
        self.assertEqual(codigo, 2)
        self.assertIn("catálogo", error)
        codigo, _, error = self.correr("instalar")
        self.assertEqual(codigo, 2)
        self.assertIn("falta el id", error)

    def test_argumentos_invalidos(self):
        for argv, texto in ((["formatear", "x"], "comando desconocido"), (["--nada"], "opción desconocida"),
                            ([], "falta el comando"), (["instalar", "a", "b"], "sobra un argumento"),
                            (["instalar", "x", "--navegador"], "necesita un valor"), (["--si"], "falta el comando")):
            with self.subTest(argv=argv):
                codigo, salida, error = self.correr(*argv)
                self.assertEqual(codigo, 2)
                self.assertIn(texto, error)
                self.assertIn("Uso:", error)
                self.assertEqual(salida, "")

    def test_la_ayuda_no_es_un_error(self):
        for pedir in (["--help"], ["-h"], ["instalar", "--help"]):
            codigo, salida, error = self.correr(*pedir)
            self.assertEqual((codigo, error), (0, ""))
            self.assertIn("listar", salida)
            self.assertIn("--navegador", salida)

    def test_sin_navegador_pide_decidir(self):
        codigo, _, error = self.correr("instalar", "netflix")
        self.assertEqual(codigo, 2)
        self.assertIn("--navegador", error)
        self.assertIn("chrome", error)
        self.assertEqual(self.ej.historial, [])

    def test_con_navegador_elegido_instala_primero_el_navegador(self):
        codigo, salida, _ = self.correr("instalar", "netflix", "--navegador", "brave")
        self.assertEqual(codigo, 0)
        self.assertEqual(self.ej.historial, [("brave", "instalar"), ("netflix", "instalar")])
        self.assertEqual(salida.splitlines(), ["Brave instalada.", "Netflix instalada."])

    def test_si_acepta_el_recomendado(self):
        codigo, _, _ = self.correr("instalar", "netflix", "--si")
        self.assertEqual(codigo, 0)
        self.assertEqual(self.ej.historial[0], ("chrome", "instalar"))

    def test_si_falla_el_navegador_no_se_sigue_y_el_codigo_es_1(self):
        codigo, salida, _ = self.correr("instalar", "netflix", "--si", resultados={"chrome": (False, "Cancelaste.")})
        self.assertEqual(codigo, 1)
        self.assertEqual(self.ej.historial, [("chrome", "instalar")])
        self.assertIn("No se pudo instalar Google Chrome", salida)
        self.assertIn("navegador", salida)

    def test_poco_espacio_pide_confirmacion(self):
        self.espacio = False
        codigo, _, error = self.correr("instalar", "spotify")
        self.assertEqual(codigo, 2)
        self.assertIn("--si", error)
        self.assertEqual(self.ej.historial, [])
        codigo, salida, _ = self.correr("instalar", "spotify", "--si")
        self.assertEqual(codigo, 0)
        self.assertEqual(salida.strip(), "Spotify instalada.")

    def test_webapp_con_navegador_instalado_va_directa(self):
        self.navegador = True
        codigo, _, _ = self.correr("instalar", "claude")
        self.assertEqual(codigo, 0)
        self.assertEqual(self.ej.historial, [("claude", "instalar")])

    def test_un_error_de_plan_es_codigo_1(self):
        def sin_herramienta(e, a, *r, **k):
            raise O.ErrorPlan("Falta Flatpak en este sistema.")
        original = O.plan_para
        O.plan_para = sin_herramienta
        self.addCleanup(setattr, O, "plan_para", original)
        codigo, _, error = self.correr("instalar", "spotify")
        self.assertEqual(codigo, 1)
        self.assertIn("Flatpak", error)


class TestCatalogoRoto(unittest.TestCase):
    def test_un_catalogo_que_no_se_puede_leer(self):
        salida, error = io.StringIO(), io.StringIO()
        codigo = CLI.main(["listar", "--catalogo", "/no/existe.json"], salida=salida, error=error)
        self.assertEqual(codigo, 1)
        self.assertIn("no se pudo leer", error.getvalue())


if __name__ == "__main__":
    unittest.main()
