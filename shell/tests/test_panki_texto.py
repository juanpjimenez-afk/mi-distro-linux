"""panki_texto.a_pango: del markdown de Gemini a Pango SEGURO.

La validez del resultado se comprueba de dos maneras: con un analizador XML de la biblioteca estándar
(siempre) y con Pango real vía GI (solo si hay GI y Pango: si no, esa prueba se salta). Para ejecutarla:

    GI_TYPELIB_PATH=<...>/girepository-1.0 LD_LIBRARY_PATH=<...> /usr/bin/python3.12 -m unittest ...
"""
import os
import random
import sys
import unittest
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell.panki_texto import a_pango  # noqa: E402

ETIQUETAS_PROPIAS = {"b", "i", "tt"}


def xml_valido(marcado):
    """Pango usa el mismo subconjunto de XML: bien formado y solo con las etiquetas que genera el módulo."""
    raiz = ET.fromstring("<r>" + marcado + "</r>")
    return all(e.tag in ETIQUETAS_PROPIAS for e in raiz.iter() if e is not raiz)


def pango_real():
    try:
        import gi
        gi.require_version("Pango", "1.0")
        from gi.repository import Pango
        Pango.parse_markup("<b>x</b>", -1, "\0")
        return Pango
    except Exception:  # noqa: BLE001 - sin GI o sin Pango
        return None


class TestEscapado(unittest.TestCase):
    def test_etiquetas_del_modelo_no_pasan(self):
        self.assertEqual(a_pango("<b>x</b>"), "&lt;b&gt;x&lt;/b&gt;")
        self.assertEqual(a_pango("<span foreground='red'>x</span>"),
                         "&lt;span foreground=&apos;red&apos;&gt;x&lt;/span&gt;")
        self.assertEqual(a_pango("<script>alert(1)</script>"), "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_ampersand_y_comillas(self):
        self.assertEqual(a_pango("Tom & Jerry"), "Tom &amp; Jerry")
        self.assertEqual(a_pango('dijo "hola" y \'adiós\''), "dijo &quot;hola&quot; y &apos;adiós&apos;")
        self.assertEqual(a_pango("&amp;"), "&amp;amp;")  # una entidad escrita por el modelo se ve tal cual
        self.assertEqual(a_pango("&lt;b&gt;"), "&amp;lt;b&amp;gt;")

    def test_el_escapado_va_antes_que_el_formato(self):
        # La negrita no puede "desescapar" nada ni dejar pasar una etiqueta dentro.
        self.assertEqual(a_pango("**<i>x</i>**"), "<b>&lt;i&gt;x&lt;/i&gt;</b>")
        self.assertEqual(a_pango("*a<b>b*"), "<i>a&lt;b&gt;b</i>")

    def test_texto_sin_formato_queda_igual(self):
        self.assertEqual(a_pango("Hola, ¿qué tal? Todo bien."), "Hola, ¿qué tal? Todo bien.")
        self.assertEqual(a_pango(""), "")
        self.assertEqual(a_pango("   \n\n  "), "")


class TestEnfasis(unittest.TestCase):
    def test_negrita(self):
        self.assertEqual(a_pango("una **negrita** aquí"), "una <b>negrita</b> aquí")

    def test_cursiva_con_asterisco_y_guion_bajo(self):
        self.assertEqual(a_pango("una *cursiva* aquí"), "una <i>cursiva</i> aquí")
        self.assertEqual(a_pango("una _cursiva_ aquí"), "una <i>cursiva</i> aquí")

    def test_combinadas(self):
        self.assertEqual(a_pango("***ambas***"), "<b><i>ambas</i></b>")
        self.assertEqual(a_pango("**negrita con *cursiva* dentro**"), "<b>negrita con <i>cursiva</i> dentro</b>")
        self.assertEqual(a_pango("*cursiva con **negrita** dentro*"), "<i>cursiva con <b>negrita</b> dentro</i>")

    def test_varios_en_una_linea(self):
        self.assertEqual(a_pango("**a** y **b** y *c*"), "<b>a</b> y <b>b</b> y <i>c</i>")

    def test_no_es_enfasis(self):
        for texto in ("2 * 3 * 4", "a * b", "snake_case_name", "__init__.py", "mi_variable_x y otra_y",
                      "5 _ 6", "*", "**", "_", "a * *", "~/.config/infinity/panki/clave"):
            with self.subTest(texto=texto):
                self.assertEqual(a_pango(texto), texto)

    def test_marcador_sin_cerrar_es_texto(self):
        for texto in ("**sin cerrar", "*sin cerrar", "_sin cerrar", "cierra** solo", "abre *y no cierra **b**"):
            with self.subTest(texto=texto):
                salida = a_pango(texto)
                self.assertTrue(xml_valido(salida), salida)
        self.assertEqual(a_pango("**sin cerrar"), "**sin cerrar")
        self.assertEqual(a_pango("abre *y no cierra **b**"), "abre *y no cierra <b>b</b>")

    def test_cruzados_no_rompen_el_anidado(self):
        self.assertEqual(a_pango("**a *b** c*"), "<b>a *b</b> c*")
        self.assertEqual(a_pango("*a **b* c**"), "<i>a **b</i> c**")
        self.assertTrue(xml_valido(a_pango("**a _b** c_ *d **e* f**")))

    def test_barra_invertida_escapa_el_marcador(self):
        self.assertEqual(a_pango(r"\*no cursiva\*"), "*no cursiva*")


class TestCodigo(unittest.TestCase):
    def test_codigo_en_linea(self):
        self.assertEqual(a_pango("usa `ls -la` ahora"), "usa <tt>ls -la</tt> ahora")

    def test_el_codigo_no_interpreta_nada(self):
        self.assertEqual(a_pango("`**no** <b> & _x_`"), "<tt>**no** &lt;b&gt; &amp; _x_</tt>")

    def test_codigo_con_dobles_comillas_invertidas(self):
        self.assertEqual(a_pango("``a ` b``"), "<tt>a ` b</tt>")

    def test_codigo_sin_cerrar_es_texto(self):
        self.assertEqual(a_pango("una ` suelta"), "una ` suelta")
        self.assertEqual(a_pango("``abierto`"), "``abierto`")

    def test_bloque_de_codigo(self):
        entrada = "Mira:\n```python\nx = 1 < 2\nprint('**hola**')\n```\nFin."
        self.assertEqual(a_pango(entrada), "Mira:\n<tt>x = 1 &lt; 2\nprint(&apos;**hola**&apos;)</tt>\nFin.")

    def test_bloque_conserva_sangria_y_lineas_vacias(self):
        salida = a_pango("```\ndef f():\n    return 1\n\n    # fin\n```")
        self.assertEqual(salida, "<tt>def f():\n    return 1\n\n    # fin</tt>")

    def test_bloque_sin_cerrar_es_texto(self):
        salida = a_pango("```\nsin cierre\n**x**")
        self.assertTrue(xml_valido(salida), salida)
        self.assertNotIn("<tt>", salida)
        self.assertIn("```", salida)

    def test_bloque_con_etiquetas_dentro(self):
        salida = a_pango("```html\n<b>hola</b></tt>\n```")
        self.assertEqual(salida, "<tt>&lt;b&gt;hola&lt;/b&gt;&lt;/tt&gt;</tt>")
        self.assertTrue(xml_valido(salida))


class TestBloques(unittest.TestCase):
    def test_lista_con_guion_y_asterisco(self):
        self.assertEqual(a_pango("- uno\n- dos"), "• uno\n• dos")
        self.assertEqual(a_pango("* uno\n* dos"), "• uno\n• dos")
        self.assertEqual(a_pango("+ uno"), "• uno")

    def test_lista_con_formato_dentro(self):
        self.assertEqual(a_pango("* **Nombre:** valor\n- `cmd`"), "• <b>Nombre:</b> valor\n• <tt>cmd</tt>")

    def test_lista_anidada_conserva_sangria(self):
        self.assertEqual(a_pango("- a\n  - b\n    - c"), "• a\n  • b\n    • c")

    def test_lista_numerada(self):
        self.assertEqual(a_pango("1. uno\n2. dos\n10) diez"), "1. uno\n2. dos\n10) diez")
        self.assertEqual(a_pango("1. **uno**"), "1. <b>uno</b>")

    def test_encabezados(self):
        self.assertEqual(a_pango("# Título"), "<b>Título</b>")
        self.assertEqual(a_pango("### Sección **x**"), "<b>Sección <b>x</b></b>")
        self.assertEqual(a_pango("## Con cierre ##"), "<b>Con cierre</b>")
        self.assertEqual(a_pango("## Lenguaje C#"), "<b>Lenguaje C#</b>")
        self.assertEqual(a_pango("#sinespacio"), "#sinespacio")

    def test_encabezado_con_etiquetas_se_escapa(self):
        self.assertEqual(a_pango("# <b>x</b>"), "<b>&lt;b&gt;x&lt;/b&gt;</b>")

    def test_cita_y_regla(self):
        self.assertEqual(a_pango("> dicho **así**"), "▎ dicho <b>así</b>")
        self.assertTrue(a_pango("---").startswith("─"))
        self.assertEqual(a_pango("a\n\n***\n\nb").count("─"), 16)

    def test_lineas_vacias_se_reducen(self):
        self.assertEqual(a_pango("a\n\n\n\n\nb"), "a\n\nb")

    def test_saltos_de_linea_de_windows(self):
        self.assertEqual(a_pango("a\r\nb\rc"), "a\nb\nc")

    def test_tablas_salen_como_texto(self):
        salida = a_pango("| a | b |\n|---|---|\n| 1 | 2 |")
        self.assertTrue(xml_valido(salida))
        self.assertIn("| a | b |", salida)


class TestEnlaces(unittest.TestCase):
    def test_enlace_se_muestra_con_su_url(self):
        self.assertEqual(a_pango("[Gemini](https://ai.google.dev/docs)"), "Gemini (https://ai.google.dev/docs)")

    def test_no_es_pulsable(self):
        salida = a_pango("[x](https://a.b/c) y <a href='https://a.b'>t</a>")
        self.assertNotIn("<a ", salida)
        self.assertNotIn("href=\"", salida)
        self.assertTrue(xml_valido(salida))

    def test_url_con_guiones_bajos_y_asteriscos_no_se_toca(self):
        self.assertEqual(a_pango("[d](https://x.com/_a_/b_c_)"), "d (https://x.com/_a_/b_c_)")
        self.assertEqual(a_pango("ver https://y.com/_x_/ ahora"), "ver https://y.com/_x_/ ahora")

    def test_url_con_ampersand_y_parentesis(self):
        self.assertEqual(a_pango("[w](https://x.org/a?b=1&c=2)"), "w (https://x.org/a?b=1&amp;c=2)")
        self.assertEqual(a_pango("[w](https://es.wikipedia.org/wiki/Python_(lenguaje))"),
                         "w (https://es.wikipedia.org/wiki/Python_(lenguaje))")

    def test_texto_igual_a_la_url_no_se_repite(self):
        self.assertEqual(a_pango("[https://a.b](https://a.b)"), "https://a.b")

    def test_formato_en_el_texto_del_enlace(self):
        self.assertEqual(a_pango("[**doc**](https://a.b)"), "<b>doc</b> (https://a.b)")

    def test_imagen(self):
        self.assertEqual(a_pango("![logo](https://a.b/l.png)"), "logo (https://a.b/l.png)")

    def test_enlace_sin_cerrar_es_texto(self):
        for texto in ("[texto](https://a.b", "[texto]", "[texto] (https://a.b)", "](x)", "[[a]](b)"):
            with self.subTest(texto=texto):
                self.assertTrue(xml_valido(a_pango(texto)))
        self.assertEqual(a_pango("[texto](https://a.b"), "[texto](https://a.b")


class TestEntradasRaras(unittest.TestCase):
    def test_no_lanza_con_cualquier_tipo(self):
        for entrada in (None, b"hola **x**", b"\xff\xfe", 42, 3.5, ["a"], {"a": 1}, object(), True):
            with self.subTest(entrada=repr(entrada)[:30]):
                salida = a_pango(entrada)
                self.assertIsInstance(salida, str)
                self.assertTrue(xml_valido(salida), salida)
        self.assertEqual(a_pango(None), "")
        self.assertEqual(a_pango(b"hola **x**"), "hola <b>x</b>")

    def test_caracteres_de_control_y_sustitutos(self):
        salida = a_pango("a\x00b\x01c\x08d\x0be\x1ff\x7fg\x85h\ud800i￾j")
        self.assertEqual(salida, "abcdefg" + "h?ij")
        self.assertTrue(xml_valido(salida))

    def test_tabulacion_se_conserva(self):
        self.assertEqual(a_pango("a\tb"), "a\tb")

    def test_texto_muy_largo(self):
        salida = a_pango("**a** " * 20000 + "`" * 3001 + "[" * 3001)
        self.assertTrue(xml_valido(salida))


class TestAleatorio(unittest.TestCase):
    """Muchas entradas hechas de marcadores: el resultado es siempre XML/Pango válido."""

    ALFABETO = ["*", "**", "***", "_", "__", "`", "``", "```", "\n", "\n\n", " ", "[", "]", "(", ")", "!", "#", "-",
                "+", ">", "1.", "<", "&", '"', "'", "\\", "a", "b", "snake_case", "http://x_y/_z_", "](", "h",
                "\t", "&amp;", "<b>", "</b>", "•", "ñ"]

    def entradas(self, n=4000, semilla=20260101):
        azar = random.Random(semilla)
        for _ in range(n):
            yield "".join(azar.choice(self.ALFABETO) for _ in range(azar.randint(1, 24)))

    def test_siempre_xml_valido(self):
        for entrada in self.entradas():
            salida = a_pango(entrada)
            try:
                self.assertTrue(xml_valido(salida))
            except (ET.ParseError, AssertionError):
                self.fail(f"Marcado inválido para {entrada!r}: {salida!r}")

    def test_sin_texto_del_modelo_dentro_de_etiquetas_ajenas(self):
        for entrada in self.entradas(1500, 7):
            salida = a_pango(entrada)
            sin_propias = salida
            for t in ETIQUETAS_PROPIAS:
                sin_propias = sin_propias.replace(f"<{t}>", "").replace(f"</{t}>", "")
            self.assertNotIn("<", sin_propias, (entrada, salida))
            self.assertNotIn(">", sin_propias, (entrada, salida))


@unittest.skipUnless(pango_real(), "sin GI/Pango: no se puede comprobar con Pango real")
class TestPangoReal(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.Pango = pango_real()

    def comprobar(self, entrada):
        salida = a_pango(entrada)
        try:
            self.Pango.parse_markup(salida, -1, "\0")
        except Exception as e:  # noqa: BLE001
            self.fail(f"Pango rechaza {salida!r} (de {entrada!r}): {e}")

    def test_ejemplos_tipicos_de_gemini(self):
        self.comprobar("# Hola\n\nAquí tienes **tres** cosas:\n\n1. `ls`\n2. *cursiva*\n3. [doc](https://a.b/c_d)\n\n"
                       "```bash\nfind . -name '*.pdf' | wc -l\n```\n\n- uno & dos\n  - sub <x>\n> cita\n---\nFin.")

    def test_casos_peligrosos(self):
        for entrada in ("<b>", "</b>", "<span>", "&", "&amp", "&#x41;", "&#0;", "<<>>", "\"'", "<tt>`</tt>",
                        "**<b>**", "`<`", "```\n</tt><b>\n```", "<?xml?>", "<!-- c -->", "<![CDATA[x]]>"):
            with self.subTest(entrada=entrada):
                self.comprobar(entrada)

    def test_todos_los_caracteres_bmp(self):
        # Cada punto de código, solo y dentro de negrita y de código.
        for cp in range(0x10000):
            if 0xD800 <= cp <= 0xDFFF:
                continue
            c = chr(cp)
            self.comprobar(c)
            self.comprobar(f"**{c}** `{c}`")

    def test_planos_astrales_y_no_caracteres(self):
        for cp in (0x10000, 0x1F600, 0x1FFFE, 0x1FFFF, 0x10FFFE, 0x10FFFF, 0xFDD0, 0xFDEF, 0xFFFE, 0xFFFF):
            self.comprobar(chr(cp))

    def test_aleatorio(self):
        for entrada in TestAleatorio().entradas(3000, 99):
            self.comprobar(entrada)


if __name__ == "__main__":
    unittest.main()
