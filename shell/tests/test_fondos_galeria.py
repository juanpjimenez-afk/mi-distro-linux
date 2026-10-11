"""Galerías de fondos en la ventana de Ajustes (Escritorio y Bloqueo y energía), con GTK 4.

Como test_contrasena_ventana.py: se salta sin pantalla (DISPLAY) o sin GTK 4; corre con xvfb-run.
Los catálogos son de mentira, en carpetas temporales, y nunca se ejecuta hyprctl ni swaybg: las acciones
de recarga se sustituyen. Lo que no se puede probar aquí (el selector de archivos real, arrastrar desde
otra aplicación) se dice en el informe.
"""
import json
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PANTALLA = bool(os.environ.get("DISPLAY"))
JPEG = b"\xff\xd8\xff\xe0" + b"prueba" * 20


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestGaleriaFondos(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, Gio, GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        try:
            gi.require_version("GdkPixbuf", "2.0")
            from gi.repository import GdkPixbuf
        except (ImportError, ValueError):
            GdkPixbuf = None
        sys.path.insert(0, SHELL)
        from infinity_shell import ajustes as A
        from infinity_shell import ajustes_app as X
        from infinity_shell import fondos as F
        cls.A, cls.X, cls.F = A, X, F
        cls.Gtk, cls.Gdk, cls.Gio, cls.GLib, cls.GdkPixbuf = Gtk, Gdk, Gio, GLib, GdkPixbuf

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.config = os.path.join(self.tmp, "config")
        self.usuario = os.path.join(self.tmp, "usuario")
        self.sistema = os.path.join(self.tmp, "sistema")
        os.makedirs(self.config)
        self.catalogo()
        self.rutas = self.F.Rutas(sistema=(self.sistema,), usuario=self.usuario, legado_escritorio=(),
                                  legado_bloqueo=())
        parches = [
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": self.config, "XDG_DATA_HOME": self.tmp}),
            mock.patch.object(self.F, "rutas_por_defecto", return_value=self.rutas),
            mock.patch.object(self.A, "correr_real", side_effect=AssertionError("no se lanza nada en las pruebas")),
            mock.patch.object(self.A, "leer_estado_contrasena", return_value="con"),
            mock.patch.object(self.X, "_en_vivo", return_value=False),
        ]
        for p in parches:
            p.start()
            self.addCleanup(p.stop)
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.galeria{time.time_ns()}")
        self.app.register(None)

    # ---------------------------------------------------------------- ayudas
    def catalogo(self, ids=("cascada", "islandia", "lago")):
        entradas = []
        for i in ids:
            for n in ("escritorio.jpg", "miniatura.jpg", "bloqueo-1920x1080.jpg"):
                ruta = os.path.join(self.sistema, i, n)
                os.makedirs(os.path.dirname(ruta), exist_ok=True)
                with open(ruta, "wb") as f:
                    f.write(JPEG)
            entradas.append({"id": i, "nombre": i.capitalize(), "escritorio": f"{i}/escritorio.jpg",
                             "miniatura": f"{i}/miniatura.jpg", "bloqueo": {"1920x1080": f"{i}/bloqueo-1920x1080.jpg"}})
        with open(os.path.join(self.sistema, "fondos.json"), "w", encoding="utf-8") as f:
            json.dump({"version": 1, "fondos": entradas}, f)

    def guarda(self, **valores):
        os.makedirs(os.path.join(self.config, "infinity"), exist_ok=True)
        with open(os.path.join(self.config, "infinity", "ajustes.json"), "w", encoding="utf-8") as f:
            json.dump({k.replace("__", "."): v for k, v in valores.items()}, f)

    def ventana(self):
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        return v

    def espera(self, condicion, segundos=20):
        ctx = self.GLib.MainContext.default()
        limite = time.monotonic() + segundos
        while not condicion() and time.monotonic() < limite:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.01)
        while ctx.pending():
            ctx.iteration(False)
        self.assertTrue(condicion(), "no se cumplió a tiempo")

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

    def botones(self, raiz, etiqueta):
        return [w for w in self.hijos(raiz) if isinstance(w, self.Gtk.Button) and w.get_label() == etiqueta]

    def imagen(self, nombre="foto.png", ancho=640, alto=360):
        if self.GdkPixbuf is None:
            self.skipTest("sin GdkPixbuf")
        pix = self.GdkPixbuf.Pixbuf.new(self.GdkPixbuf.Colorspace.RGB, False, 8, ancho, alto)
        pix.fill(0x3060A0FF)
        ruta = os.path.join(self.tmp, "origen", nombre)
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        pix.savev(ruta, "png", [], [])
        return ruta

    def elige(self, galeria, valor):
        """Como un clic del usuario sobre la miniatura."""
        galeria.caja.select_child(galeria._mosaicos[valor])

    # ---------------------------------------------------------------- contenido
    def test_el_escritorio_ofrece_el_catalogo_y_el_color_solido(self):
        g = self.ventana().controles["escritorio.fondo"]
        self.assertEqual(list(g._mosaicos), ["imagen", "islandia", "lago", "color"])    # la cascada es «imagen»
        nombres = [h.get_tooltip_text() for h in g._mosaicos.values()]
        self.assertEqual(nombres, ["Cascada", "Islandia", "Lago", "Color sólido"])
        for nombre in nombres:
            self.assertIn(nombre, self.textos(g.caja))

    def test_el_bloqueo_ofrece_el_catalogo_sin_color_solido(self):
        g = self.ventana().controles["bloqueo.fondo"]
        self.assertEqual(list(g._mosaicos), ["cascada", "islandia", "lago"])

    def test_el_valor_antiguo_imagen_marca_la_cascada_y_color_marca_color(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        self.assertEqual(g.valor, "imagen")
        self.assertTrue(g._mosaicos["imagen"].is_selected())
        self.assertTrue(g._marcas["imagen"].get_visible())
        self.assertFalse(g._marcas["lago"].get_visible())
        g.valor = "color"
        self.assertTrue(g._mosaicos["color"].is_selected())
        self.assertEqual(v._leer_controles()["escritorio.fondo"], "color")
        g.valor = "cascada"     # alias
        self.assertEqual(g.valor, "imagen")

    def test_lee_la_configuracion_guardada(self):
        self.guarda(escritorio__fondo="lago", bloqueo__fondo="cascada")
        v = self.ventana()
        self.assertEqual(v.controles["escritorio.fondo"].valor, "lago")
        self.assertEqual(v.controles["bloqueo.fondo"].valor, "cascada")
        self.assertTrue(v.controles["bloqueo.fondo"]._mosaicos["cascada"].is_selected())

    def test_un_fondo_guardado_que_ya_no_existe_se_avisa_y_se_usa_el_defecto(self):
        self.guarda(escritorio__fondo="u-borrado", bloqueo__fondo="u-borrado")
        v = self.ventana()
        self.assertEqual(v.controles["bloqueo.fondo"].valor, "islandia")
        self.assertIn("no existe", v.estado.get_text())
        self.assertTrue(v.estado.has_css_class("error"))

    def test_elegir_una_miniatura_cuenta_como_cambio_sin_aplicar(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        self.elige(g, "lago")
        self.assertEqual(g.valor, "lago")
        self.assertEqual(v._leer_controles()["escritorio.fondo"], "lago")
        self.assertIn("1 cambio", v.estado.get_text())
        self.assertTrue(g._marcas["lago"].get_visible())
        self.assertFalse(g._marcas["imagen"].get_visible())

    def test_cargar_valores_no_cuenta_como_cambio(self):
        v = self.ventana()
        v._cargar_controles(dict(self.A.DEFECTOS, **{"escritorio.fondo": "lago", "bloqueo.fondo": "lago"}))
        self.assertEqual(v.estado.get_text(), "")
        self.assertEqual(v._leer_controles()["bloqueo.fondo"], "lago")

    def test_restaurar_vuelve_a_los_fondos_por_defecto(self):
        v = self.ventana()
        self.elige(v.controles["escritorio.fondo"], "lago")
        self.elige(v.controles["bloqueo.fondo"], "lago")
        v._restaurar()
        self.assertEqual((v.controles["escritorio.fondo"].valor, v.controles["bloqueo.fondo"].valor), ("imagen", "islandia"))

    def test_el_color_solido_toma_el_color_elegido(self):
        v = self.ventana()
        v._cargar_controles(dict(self.A.DEFECTOS, **{"escritorio.color": "#2255aa"}))
        g = v.controles["escritorio.fondo"]
        self.assertIn("rgb(34,85,170)", g._proveedor.to_string())

    def test_el_bloqueo_avisa_de_cuando_se_aplica(self):
        v = self.ventana()
        self.assertTrue(any("próxima vez que se bloquee" in t for t in self.textos(v.controles["bloqueo.fondo"])))

    # ---------------------------------------------------------------- aplicar
    def test_aplicar_escribe_los_ficheros_y_recarga_el_fondo_solo_si_cambia_el_escritorio(self):
        v = self.ventana()
        self.elige(v.controles["bloqueo.fondo"], "lago")
        with mock.patch.object(self.A, "ejecutar_acciones", return_value=[]) as ejecutar:
            v._aplicar()
        self.assertEqual(ejecutar.call_args[0][0], [])        # el bloqueo se aplica en el siguiente bloqueo
        with open(os.path.join(self.config, "infinity", "fondo-bloqueo"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "lago\n")
        self.elige(v.controles["escritorio.fondo"], "islandia")
        with mock.patch.object(self.A, "ejecutar_acciones", return_value=[]) as ejecutar:
            v._aplicar()
        self.assertEqual(ejecutar.call_args[0][0], ["wallpaper", "shell"])
        with open(os.path.join(self.config, "infinity", "fondo"), encoding="utf-8") as f:
            self.assertEqual(f.read(), "islandia\n")

    # ---------------------------------------------------------------- añadir
    def test_anadir_imagen_en_un_hilo_y_queda_elegida_en_ambas_galerias(self):
        v = self.ventana()
        g = v.controles["bloqueo.fondo"]
        v.importar_imagen(self.imagen("Mi playa.png"), g)
        self.assertTrue(v._importando)
        self.assertFalse(g.boton_anadir.get_sensitive())       # mientras trabaja no se puede lanzar otra
        self.espera(lambda: not v._importando)
        self.assertEqual(g.valor, "u-mi-playa")
        self.assertIn("u-mi-playa", v.controles["escritorio.fondo"]._mosaicos)   # la otra galería también la ofrece
        self.assertEqual(v.controles["escritorio.fondo"].valor, "imagen")        # pero la elegida es solo la del bloqueo
        self.assertIn("Mi playa", g.aviso.get_text())
        self.assertIn("Aplicar", g.aviso.get_text())
        self.assertFalse(g.aviso.has_css_class("error"))
        self.assertTrue(g.boton_anadir.get_sensitive())
        self.assertIn("1 cambio", v.estado.get_text())
        # El fondo existe de verdad en la carpeta del usuario
        self.assertTrue(os.path.isfile(os.path.join(self.usuario, "u-mi-playa", "bloqueo-1920x1080.jpg")))

    def test_los_fondos_del_usuario_tienen_quitar_y_los_del_sistema_no(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        self.assertEqual(self.botones(g.caja, "Quitar"), [])
        v.importar_imagen(self.imagen(), g)
        self.espera(lambda: not v._importando)
        self.assertEqual(len(self.botones(g.caja, "Quitar")), 1)
        self.assertEqual(g.valor, "u-foto")

    def test_error_de_importacion_se_ve_en_la_pagina(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        falso = os.path.join(self.tmp, "origen", "falso.jpg")
        os.makedirs(os.path.dirname(falso), exist_ok=True)
        with open(falso, "wb") as f:
            f.write(b"esto no es una imagen\n" * 20)
        v.importar_imagen(falso, g)
        self.espera(lambda: not v._importando)
        self.assertIn("no es una imagen", g.aviso.get_text())
        self.assertTrue(g.aviso.has_css_class("error"))
        self.assertTrue(g.boton_anadir.get_sensitive())
        self.assertEqual(g.valor, "imagen")                    # no cambia lo elegido
        self.assertEqual(self.F.fondos_usuario(self.rutas), [])

    def test_error_inesperado_tambien_se_ve(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        with mock.patch.object(self.F, "importar", side_effect=RuntimeError("fallo raro")):
            v.importar_imagen(self.imagen(), g)
            self.espera(lambda: not v._importando)
        self.assertIn("fallo raro", g.aviso.get_text())
        self.assertTrue(g.aviso.has_css_class("error"))

    def test_no_se_importan_dos_a_la_vez(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        v.importar_imagen(self.imagen("a.png", 4000, 2250), g)
        v.importar_imagen(self.imagen("b.png"), g)
        self.assertIn("Ya se está añadiendo", g.aviso.get_text())
        self.espera(lambda: not v._importando, 60)
        self.assertEqual([f.id for f in self.F.fondos_usuario(self.rutas)], ["u-a"])

    def test_soltar_un_archivo_sobre_la_galeria_lo_importa(self):
        v = self.ventana()
        g = v.controles["bloqueo.fondo"]
        ruta = self.imagen("soltada.png")
        self.assertTrue(g._al_soltar(None, self.Gio.File.new_for_path(ruta), 0, 0))
        self.espera(lambda: not v._importando)
        self.assertEqual(g.valor, "u-soltada")
        self.assertFalse(g._al_soltar(None, "texto", 0, 0))      # lo que no es un archivo se ignora

    # ---------------------------------------------------------------- quitar
    class _Dialogo:
        """Hace de Gtk.AlertDialog: «pulsa» el botón indicado (1 = Quitar, 0 = Cancelar)."""

        def __init__(self, boton):
            self.boton = boton

        def choose_finish(self, _resultado):
            return self.boton

    def importa_y_espera(self, v, g, nombre="x.png"):
        v.importar_imagen(self.imagen(nombre), g)
        self.espera(lambda: not v._importando)
        return self.F.fondos_usuario(self.rutas)[-1]

    def test_quitar_pide_confirmacion_y_cancelar_no_borra(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        f = self.importa_y_espera(v, g)
        v._quitar_confirmado(self._Dialogo(0), None, f)
        self.assertTrue(os.path.isdir(f.carpeta))
        with mock.patch.object(self.Gtk.AlertDialog, "choose") as pregunta:
            self.botones(g.caja, "Quitar")[0].emit("clicked")
        pregunta.assert_called_once()                            # al pulsar «Quitar» se pregunta primero
        self.assertTrue(os.path.isdir(f.carpeta))

    def test_quitar_el_elegido_vuelve_al_defecto_y_lo_dice(self):
        v = self.ventana()
        g, b = v.controles["escritorio.fondo"], v.controles["bloqueo.fondo"]
        f = self.importa_y_espera(v, g)
        self.elige(b, f.id)
        v._quitar_confirmado(self._Dialogo(1), None, f)
        self.assertFalse(os.path.exists(f.carpeta))
        self.assertEqual((g.valor, b.valor), ("imagen", "islandia"))
        self.assertNotIn(f.id, g._mosaicos)
        self.assertNotIn(f.id, b._mosaicos)
        self.assertIn("quitado", g.aviso.get_text())
        self.assertIn("Aplicar", g.aviso.get_text())

    def test_quitar_uno_no_elegido_no_toca_lo_elegido(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        f = self.importa_y_espera(v, g)
        self.elige(g, "lago")
        v._quitar_confirmado(self._Dialogo(1), None, f)
        self.assertEqual(g.valor, "lago")
        self.assertFalse(os.path.exists(f.carpeta))

    def test_quitar_solo_borra_dentro_de_la_carpeta_del_usuario(self):
        v = self.ventana()
        g = v.controles["escritorio.fondo"]
        ajeno = self.F.Fondo("lago", "Lago", "sistema", os.path.join(self.sistema, "lago"), None, None, {})
        v._quitar_confirmado(self._Dialogo(1), None, ajeno)       # un fondo del sistema: se rechaza
        self.assertTrue(os.path.isdir(os.path.join(self.sistema, "lago")))
        self.assertTrue(g.aviso.has_css_class("error"))

    # ---------------------------------------------------------------- sin catálogo
    def test_sin_catalogo_ni_fondo_antiguo_no_falla(self):
        import shutil
        shutil.rmtree(self.sistema)
        v = self.ventana()
        self.assertEqual(list(v.controles["escritorio.fondo"]._mosaicos), ["color"])
        self.assertEqual(list(v.controles["bloqueo.fondo"]._mosaicos), [])
        self.assertEqual(v._leer_controles()["bloqueo.fondo"], "islandia")

    def test_sin_catalogo_pero_con_el_fondo_antiguo_ofrece_el_respaldo(self):
        import shutil
        shutil.rmtree(self.sistema)
        os.makedirs(os.path.join(self.tmp, "viejo", "lock"))
        for ruta in ("wallpaper.jpg", "lock/wallpaper-lock-1920x1080.jpg"):
            with open(os.path.join(self.tmp, "viejo", ruta), "wb") as f:
                f.write(JPEG)
        rutas = self.F.Rutas(sistema=(self.sistema,), usuario=self.usuario,
                             legado_escritorio=(os.path.join(self.tmp, "viejo", "wallpaper.jpg"),),
                             legado_bloqueo=(os.path.join(self.tmp, "viejo", "lock"),))
        with mock.patch.object(self.F, "rutas_por_defecto", return_value=rutas):
            v = self.ventana()
            self.assertEqual(list(v.controles["escritorio.fondo"]._mosaicos), ["imagen", "color"])
            self.assertEqual(list(v.controles["bloqueo.fondo"]._mosaicos), ["islandia"])
            self.assertTrue(v.controles["bloqueo.fondo"]._mosaicos["islandia"].is_selected())

    def test_miniaturas_ilegibles_no_rompen_la_galeria(self):
        # Los JPEG de estas pruebas no son imágenes de verdad: la galería se dibuja igual.
        v = self.ventana()
        v.present()
        for _ in range(20):
            while self.GLib.MainContext.default().pending():
                self.GLib.MainContext.default().iteration(False)
        self.assertEqual(len(v.controles["escritorio.fondo"]._mosaicos), 4)


if __name__ == "__main__":
    unittest.main()
