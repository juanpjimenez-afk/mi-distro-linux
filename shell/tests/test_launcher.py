"""El buscador web del lanzador usa Google.

launcher.py importa GTK4 + layer-shell al cargarse, así que aquí se lee el código con ast
(sin ejecutarlo): basta para cazar una vuelta atrás a DuckDuckGo en la URL o en el nombre.
"""
import ast
import os
import unittest

RUTA = os.path.join(os.path.dirname(__file__), "..", "infinity_shell", "launcher.py")


class TestBuscadorWeb(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(RUTA, encoding="utf-8") as f:
            cls.arbol = ast.parse(f.read(), RUTA)

    def test_url_base_es_google(self):
        valores = [
            n.value.value
            for n in self.arbol.body
            if isinstance(n, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "BUSCADOR_WEB" for t in n.targets)
        ]
        self.assertEqual(valores, ["https://www.google.com/search?q="])

    def test_resultado_de_busqueda_muestra_google(self):
        subtitulos = []
        for n in ast.walk(self.arbol):
            if (isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "Resultado"
                    and len(n.args) >= 2 and isinstance(n.args[0], ast.JoinedStr)
                    and isinstance(n.args[0].values[0], ast.Constant)
                    and str(n.args[0].values[0].value).startswith("Buscar en la web")):
                subtitulos.append(n.args[1].value)
        self.assertEqual(subtitulos, ["Google"])

    def test_no_queda_duckduckgo(self):
        with open(RUTA, encoding="utf-8") as f:
            self.assertFalse("duckduckgo" in f.read().lower(), "queda una referencia a DuckDuckGo en launcher.py")


if __name__ == "__main__":
    unittest.main()
