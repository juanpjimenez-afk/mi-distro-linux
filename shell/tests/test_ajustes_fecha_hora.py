"""La página «Fecha y hora» de Ajustes y la fila «Guardado de cambios» de Sistema, con GTK 4.

Como test_contrasena_ventana.py: se salta sin pantalla (DISPLAY) o sin GTK 4; corre con xvfb-run. Nunca ejecuta
timedatectl, sudo ni polkit: lo que lee y lo que cambia el sistema se sustituye, y las funciones reales que lo harían
fallan si la ventana llegara a invocarlas.
"""
import contextlib
import datetime
import io
import os
import sys
import tempfile
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PANTALLA = bool(os.environ.get("DISPLAY"))

ZONAS = ["America/Argentina/Buenos_Aires", "America/Santiago", "America/Sao_Paulo", "Asia/Tokyo", "Europe/Madrid",
         "UTC"]


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
class TestPaginaFechaHora(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        sys.path.insert(0, SHELL)
        from infinity_shell import ajustes as A
        from infinity_shell import ajustes_app as X
        cls.A, cls.X, cls.Gtk, cls.GLib = A, X, Gtk, GLib

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        A = self.A
        sin_sistema = AssertionError("las pruebas no tocan el sistema")
        parches = [
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": tmp.name}),
            # Guardas: construir la ventana no lanza nada; si algo llegara a ejecutar una orden real, la prueba falla.
            mock.patch.object(A, "correr_real", side_effect=sin_sistema),
            mock.patch.object(A, "_correr_lectura", side_effect=sin_sistema),
            mock.patch.object(A, "_correr_passwd_s", side_effect=sin_sistema),
            mock.patch.object(A, "leer_estado_contrasena", return_value="con"),
            mock.patch.object(A, "usuario_actual", return_value="infinity"),
            mock.patch.object(self.X, "_en_vivo", return_value=False),
            # Lo que «lee» la página del sistema (el estado se cambia con self.estado y self.zonas)
            mock.patch.object(A, "leer_estado_hora", side_effect=lambda correr=None: self.estado),
            mock.patch.object(A, "listar_zonas", side_effect=lambda correr=None, leer=None: list(self.zonas)),
        ]
        for parche in parches:
            parche.start()
            self.addCleanup(parche.stop)
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.fecha{time.time_ns()}")
        self.app.register(None)
        self.estado = A.EstadoHora("America/Santiago", False, True, False)
        self.zonas = list(ZONAS)

    # ------------------------------------------------------------------ ayudas
    def ventana(self, sincrono=True):
        """Una ventana de Ajustes con la página de Fecha y hora ya leída (estado y zonas fingidos).

        `sincrono`: el trabajo en segundo plano corre al momento, sin hilo."""
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        if sincrono:
            v._lanzar = lambda trabajo, listo, ocupado=True, aviso=None: listo(*trabajo())
        v._leer_estado_hora()
        if not sincrono:   # la lectura corre en un hilo: se espera a que llegue a la ventana
            self.assertTrue(self.bombear(lambda: v._estado_hora is not None))
        return v

    def textos(self, raiz):
        salida, pendientes = [], [raiz]
        while pendientes:
            w = pendientes.pop()
            if isinstance(w, self.Gtk.Label):
                salida.append(w.get_text())
            h = w.get_first_child()
            while h is not None:
                pendientes.append(h)
                h = h.get_next_sibling()
        return salida

    def bombear(self, hasta=None, segundos=3.0):
        ctx = self.GLib.MainContext.default()
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            if hasta is not None and hasta():
                return True
            time.sleep(0.005)
        return hasta is None

    def elegir_fecha(self, v, a, m, d, h, mi, s):
        v._poner_selectores(datetime.datetime(a, m, d, h, mi, s))
        v._hora_editada = True

    def fila_de_zona(self, v, zona):
        return next(f for f, z in v._zona_de_fila.items() if z == zona)

    # ----------------------------------------------------------------- la página
    def test_la_pagina_existe_y_es_facil_de_encontrar(self):
        v = self.ventana()
        self.assertEqual(dict(self.A.PAGINAS)["fecha"], "Fecha y hora")
        pagina = v.pila.get_child_by_name("fecha")
        self.assertIsNotNone(pagina)
        self.assertEqual(v.pila.get_page(pagina).get_title(), "Fecha y hora")
        claves = [p for p, _ in self.A.PAGINAS]
        self.assertLess(claves.index("fecha"), claves.index("sistema"))
        textos = self.textos(pagina)
        for esperado in ("Ajustar hora automáticamente (internet)", "Cambiar la fecha y la hora", "Zona horaria",
                         "Aplicar fecha y hora", "Aplicar zona horaria"):
            self.assertTrue(any(esperado in t or esperado == t for t in textos), esperado)
        self.assertEqual(v.hora_boton.get_label(), "Aplicar fecha y hora")

    def test_construir_la_ventana_no_ejecuta_nada(self):
        """Las guardas de setUp fallarían: la lectura del sistema espera a que se muestre la página."""
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        self.assertIsNone(v._estado_hora)
        self.assertEqual(v._zonas, [])
        self.assertEqual(v._tic_hora_id, 0)

    def test_no_forma_parte_de_ajustes_json(self):
        """La hora es del sistema: ni interruptor ni calendario entran en los valores de Ajustes."""
        v = self.ventana()
        self.assertEqual(set(v._leer_controles()), set(self.A.DEFECTOS))
        self.assertEqual(v._leer_controles(), dict(self.A.DEFECTOS))

    def test_muestra_la_hora_actual_con_el_dia_en_espanol(self):
        v = self.ventana()
        casos = (((2026, 10, 19), "Lunes 19 de octubre de 2026"), ((2026, 10, 23), "Viernes 23 de octubre de 2026"),
                 ((2027, 1, 1), "Viernes 1 de enero de 2027"), ((2028, 2, 29), "Martes 29 de febrero de 2028"))
        for (a, m, d), texto in casos:
            with self.subTest(fecha=(a, m, d)), mock.patch.object(self.A, "ahora",
                                                                  return_value=datetime.datetime(a, m, d, 14, 35, 7)):
                v._pintar_ahora()
                self.assertEqual(v.hora_dia.get_text(), texto)
                self.assertEqual(v.hora_reloj.get_text(), "14:35:07")

    def test_el_calendario_enseña_el_dia_de_la_semana_de_lo_que_se_fijara(self):
        v = self.ventana()
        casos = (((2026, 10, 19), "lunes"), ((2026, 10, 23), "viernes"), ((2027, 1, 1), "viernes"),
                 ((2028, 2, 29), "martes"))
        for (a, m, d), dia in casos:
            with self.subTest(fecha=(a, m, d)):
                self.elegir_fecha(v, a, m, d, 18, 0, 0)
                self.assertEqual(v.hora_vista.get_text(),
                                 f"Se fijará: {dia} {d} de {self.A.MESES[m - 1]} de {a}, 18:00:00")
                self.assertEqual(v._valores_selectores(), (a, m, d, 18, 0, 0))

    def test_el_dia_en_pantalla_coincide_con_el_oraculo_en_todo_el_rango(self):
        """Cada fecha elegida en el calendario: el día que se enseña es el de date.weekday()."""
        v = self.ventana()
        fecha = datetime.date(2026, 1, 1)
        for _ in range(0, 800, 7):   # una de cada semana durante 15 años: cubre todos los días de la semana y los meses
            self.elegir_fecha(v, fecha.year, fecha.month, fecha.day, 12, 0, 0)
            dia = self.A.DIAS[fecha.weekday()]
            self.assertTrue(v.hora_vista.get_text().startswith(f"Se fijará: {dia} {fecha.day} de "), fecha)
            fecha += datetime.timedelta(days=7)

    def test_los_selectores_arrancan_en_la_hora_actual(self):
        self.estado = self.A.EstadoHora("America/Santiago", False, True, False)
        with mock.patch.object(self.A, "ahora", return_value=datetime.datetime(2026, 10, 19, 9, 5, 3)):
            v = self.ventana()
        self.assertEqual(v._valores_selectores(), (2026, 10, 19, 9, 5, 3))
        self.assertEqual(v.hora_h.get_text(), "09")   # siempre dos cifras
        self.assertEqual(v.hora_m.get_text(), "05")
        self.assertEqual(v.hora_s.get_text(), "03")
        self.assertFalse(v._hora_editada)

    def test_lo_que_el_usuario_toca_no_se_pisa_al_releer_el_estado(self):
        v = self.ventana()
        self.elegir_fecha(v, 2030, 5, 17, 8, 30, 0)
        with mock.patch.object(self.A, "ahora", return_value=datetime.datetime(2026, 10, 19, 9, 5, 3)):
            v._estado_hora_listo(self.estado, self.zonas)
        self.assertEqual(v._valores_selectores(), (2030, 5, 17, 8, 30, 0))

    def test_los_selectores_dan_la_vuelta(self):
        v = self.ventana()
        v.hora_h.set_value(23)
        v.hora_h.spin(self.Gtk.SpinType.STEP_FORWARD, 1)
        self.assertEqual(v.hora_h.get_value_as_int(), 0)
        v.hora_m.set_value(0)
        v.hora_m.spin(self.Gtk.SpinType.STEP_BACKWARD, 1)
        self.assertEqual(v.hora_m.get_value_as_int(), 59)
        self.assertEqual((v.hora_h.get_adjustment().get_upper(), v.hora_m.get_adjustment().get_upper(),
                          v.hora_s.get_adjustment().get_upper()), (23, 59, 59))

    # ---------------------------------------------------------------- hora automática
    def test_con_la_hora_automatica_activa_no_se_puede_fijar_a_mano(self):
        self.estado = self.A.EstadoHora("America/Santiago", True, True, True)
        v = self.ventana()
        self.assertTrue(v.hora_ntp.get_active())
        self.assertTrue(v.hora_ntp.get_sensitive())
        for w in (v.hora_calendario, v.hora_h, v.hora_m, v.hora_s, v.hora_boton):
            self.assertFalse(w.get_sensitive())
        self.assertTrue(v.hora_nota.get_visible())
        self.assertIn("apaga «Ajustar hora automáticamente»", v.hora_nota.get_text())
        self.assertIn("sincronizada", v.hora_ntp_ayuda.get_text())

    def test_con_la_hora_automatica_apagada_se_puede_fijar_a_mano(self):
        v = self.ventana()
        self.assertFalse(v.hora_ntp.get_active())
        for w in (v.hora_calendario, v.hora_h, v.hora_m, v.hora_s, v.hora_boton):
            self.assertTrue(w.get_sensitive())
        self.assertFalse(v.hora_nota.get_visible())

    def test_sin_servicio_ntp_el_interruptor_esta_bloqueado_y_dice_como_instalarlo(self):
        self.estado = self.A.EstadoHora("America/Santiago", False, False, False)
        v = self.ventana()
        self.assertFalse(v.hora_ntp.get_sensitive())
        self.assertIn("sudo apt install systemd-timesyncd", v.hora_ntp_ayuda.get_text())
        self.assertTrue(v.hora_boton.get_sensitive())   # a mano sí se puede

    def test_reflejar_el_estado_en_el_interruptor_no_lanza_ninguna_orden(self):
        self.estado = self.A.EstadoHora("America/Santiago", True, True, True)
        with mock.patch.object(self.A, "fijar_hora_automatica", side_effect=AssertionError("no es una orden")):
            self.ventana()

    def test_apagar_la_hora_automatica_llama_a_timedatectl_y_libera_lo_manual(self):
        self.estado = self.A.EstadoHora("America/Santiago", True, True, True)
        v = self.ventana()
        self.estado = self.A.EstadoHora("America/Santiago", False, True, False)   # lo que leerá después
        with mock.patch.object(self.A, "fijar_hora_automatica", return_value=(True, "")) as fijar:
            v.hora_ntp.set_active(False)
        fijar.assert_called_once_with(False)
        self.assertIn("ya puedes cambiar la fecha y la hora a mano", v.hora_aviso_ntp.get_text())
        self.assertFalse(v.hora_aviso_ntp.has_css_class("error"))
        self.assertTrue(v.hora_boton.get_sensitive())
        self.assertFalse(v.hora_nota.get_visible())

    def test_si_falla_el_interruptor_vuelve_a_lo_que_hay_y_dice_por_que(self):
        v = self.ventana()   # la hora automática está apagada
        error = "No se pudo cambiar la hora automática: no hay ninguna ventana de autorización. Hazlo a mano."
        with mock.patch.object(self.A, "fijar_hora_automatica", return_value=(False, error)) as fijar:
            v.hora_ntp.set_active(True)
        fijar.assert_called_once_with(True)
        self.assertFalse(v.hora_ntp.get_active())   # el sistema sigue con la hora automática apagada
        self.assertEqual(v.hora_aviso_ntp.get_text(), error)
        self.assertTrue(v.hora_aviso_ntp.has_css_class("error"))

    # ------------------------------------------------------------ fijar fecha y hora
    def test_aplicar_fecha_y_hora_manda_lo_elegido(self):
        v = self.ventana()
        self.elegir_fecha(v, 2026, 10, 23, 18, 7, 9)
        with mock.patch.object(self.A, "fijar_fecha_hora", return_value=(True, "")) as fijar:
            v.hora_boton.emit("clicked")
        fijar.assert_called_once_with(2026, 10, 23, 18, 7, 9)
        self.assertEqual(v.hora_aviso.get_text(), "Fecha y hora cambiadas.")
        self.assertFalse(v.hora_aviso.has_css_class("error"))
        self.assertFalse(v._hora_editada)

    def test_el_numero_tecleado_sin_confirmar_tambien_cuenta(self):
        v = self.ventana()
        self.elegir_fecha(v, 2026, 10, 23, 18, 0, 0)
        v.hora_m.set_text("45")   # tecleado, aún sin Enter
        with mock.patch.object(self.A, "fijar_fecha_hora", return_value=(True, "")) as fijar:
            v._aplicar_fecha_hora()
        fijar.assert_called_once_with(2026, 10, 23, 18, 45, 0)

    def test_un_error_al_fijar_se_ve_en_la_pagina(self):
        v = self.ventana()
        error = "No se pudo cambiar la fecha y la hora: no se concedió el permiso. Hazlo a mano en una terminal: x"
        with mock.patch.object(self.A, "fijar_fecha_hora", return_value=(False, error)):
            v.hora_boton.emit("clicked")
        self.assertEqual(v.hora_aviso.get_text(), error)
        self.assertTrue(v.hora_aviso.has_css_class("error"))
        self.assertTrue(v.hora_aviso.get_visible())

    def test_de_verdad_llama_a_timedatectl_con_el_argv_esperado_y_sin_shell(self):
        """Sin sustituir fijar_fecha_hora: la orden que llega al ejecutor es sudo -n timedatectl set-time (lista)."""
        v = self.ventana()
        self.elegir_fecha(v, 2028, 2, 29, 0, 0, 0)
        pedidos = []

        def correr(argv, detach=False, timeout=15):
            pedidos.append(list(argv))
            return True, ""
        with mock.patch.object(self.A, "correr_real", side_effect=correr):
            v.hora_boton.emit("clicked")
        self.assertEqual(pedidos, [["sudo", "-n", "timedatectl", "set-time", "2028-02-29 00:00:00"]])

    # ------------------------------------------------------------ zona horaria
    def test_la_lista_trae_todas_las_zonas_y_marca_la_actual(self):
        v = self.ventana()
        self.assertEqual(sorted(v._zona_de_fila.values()), sorted(ZONAS))
        self.assertEqual(v.hora_zona.get_text(), "Zona horaria: America/Santiago")
        self.assertEqual(v._zona_elegida, "America/Santiago")
        self.assertIs(v.zona_lista.get_selected_row(), self.fila_de_zona(v, "America/Santiago"))
        self.assertFalse(v.zona_boton.get_sensitive())   # es la que ya tiene: nada que aplicar

    def test_la_lista_enseña_los_nombres_legibles(self):
        v = self.ventana()
        etiqueta = self.fila_de_zona(v, "America/Argentina/Buenos_Aires").get_child()
        self.assertEqual(etiqueta.get_text(), "America/Argentina/Buenos Aires")

    def test_el_buscador_filtra_la_lista(self):
        v = self.ventana()
        v.zona_buscar.set_text("buenos aires")
        v._al_buscar_zona()
        visibles = [z for f, z in v._zona_de_fila.items() if v._zona_visible(f)]
        self.assertEqual(visibles, ["America/Argentina/Buenos_Aires"])
        v.zona_buscar.set_text("AMERICA")
        v._al_buscar_zona()
        self.assertEqual(sorted(z for f, z in v._zona_de_fila.items() if v._zona_visible(f)),
                         ["America/Argentina/Buenos_Aires", "America/Santiago", "America/Sao_Paulo"])
        v.zona_buscar.set_text("")
        v._al_buscar_zona()
        self.assertEqual(len([f for f in v._zona_de_fila if v._zona_visible(f)]), len(ZONAS))

    def test_el_filtro_llega_a_las_filas_de_verdad(self):
        v = self.ventana()
        v.zona_buscar.set_text("tokyo")
        v._al_buscar_zona()
        self.assertTrue(self.fila_de_zona(v, "Asia/Tokyo").get_child_visible())
        self.assertFalse(self.fila_de_zona(v, "Europe/Madrid").get_child_visible())

    def test_elegir_otra_zona_activa_el_boton_y_aplicar_la_cambia(self):
        v = self.ventana()
        v.zona_lista.select_row(self.fila_de_zona(v, "Europe/Madrid"))
        self.assertEqual(v._zona_elegida, "Europe/Madrid")
        self.assertTrue(v.zona_boton.get_sensitive())
        self.estado = self.A.EstadoHora("Europe/Madrid", False, True, False)   # lo que leerá después
        with mock.patch.object(self.A, "fijar_zona_horaria", return_value=(True, "")) as fijar:
            v.zona_boton.emit("clicked")
        fijar.assert_called_once_with("Europe/Madrid", ZONAS)
        self.assertEqual(v.zona_aviso.get_text(), "Zona horaria cambiada a Europe/Madrid.")
        self.assertEqual(v.hora_zona.get_text(), "Zona horaria: Europe/Madrid")
        self.assertFalse(v.zona_boton.get_sensitive())   # ya es la actual

    def test_un_error_al_cambiar_la_zona_se_ve_y_no_pierde_la_eleccion(self):
        v = self.ventana()
        v.zona_lista.select_row(self.fila_de_zona(v, "Asia/Tokyo"))
        error = "No se pudo cambiar la zona horaria: no se concedió el permiso de administrador. Hazlo a mano: x"
        with mock.patch.object(self.A, "fijar_zona_horaria", return_value=(False, error)):
            v.zona_boton.emit("clicked")
        self.assertEqual(v.zona_aviso.get_text(), error)
        self.assertTrue(v.zona_aviso.has_css_class("error"))
        self.assertEqual(v._zona_elegida, "Asia/Tokyo")
        self.assertTrue(v.zona_boton.get_sensitive())   # puede reintentarlo

    def test_sin_lista_de_zonas_lo_dice(self):
        self.zonas = []
        v = self.ventana()
        self.assertIn("timedatectl list-timezones", v.zona_aviso.get_text())
        self.assertTrue(v.zona_aviso.has_css_class("error"))
        self.assertFalse(v.zona_boton.get_sensitive())

    def test_la_zona_se_actualiza_tambien_en_la_pagina_sistema(self):
        v = self.ventana()
        self.assertEqual(v.sistema_zona.get_text(), "America/Santiago")
        v.zona_lista.select_row(self.fila_de_zona(v, "Europe/Madrid"))
        self.estado = self.A.EstadoHora("Europe/Madrid", False, True, False)
        v._estado_hora_listo(self.estado, self.zonas)
        self.assertEqual(v.sistema_zona.get_text(), "Europe/Madrid")

    # ------------------------------------------------- con hilo de verdad y página mostrada
    def test_el_cambio_corre_en_un_hilo_y_la_ventana_no_espera(self):
        v = self.ventana(sincrono=False)
        v._lanzar = self.X.VentanaAjustes._lanzar.__get__(v)   # el real
        self.elegir_fecha(v, 2026, 10, 23, 18, 0, 0)
        suelta = []

        def lento(*valores):
            while not suelta:
                time.sleep(0.01)
            return True, ""
        with mock.patch.object(self.A, "fijar_fecha_hora", side_effect=lento) as fijar:
            v.hora_boton.emit("clicked")
            self.bombear(segundos=0.2)
            # esperando el permiso: gira la rueda, los controles se bloquean y el aviso lo dice
            self.assertTrue(v.hora_rueda.get_spinning())
            for w in (v.hora_ntp, v.hora_boton, v.hora_calendario, v.zona_boton):
                self.assertFalse(w.get_sensitive())
            self.assertIn("Pidiendo permiso", v.hora_aviso.get_text())
            suelta.append(True)
            self.assertTrue(self.bombear(lambda: v.hora_aviso.get_text() == "Fecha y hora cambiadas."))
        fijar.assert_called_once_with(2026, 10, 23, 18, 0, 0)
        self.assertFalse(v.hora_rueda.get_spinning())
        self.assertTrue(v.hora_boton.get_sensitive())

    def test_una_excepcion_en_el_hilo_llega_a_la_pagina(self):
        """Nunca en silencio: el error sale en el aviso de la sección que lo lanzó, y los controles se liberan."""
        v = self.ventana(sincrono=False)
        v._lanzar = self.X.VentanaAjustes._lanzar.__get__(v)
        v.zona_lista.select_row(self.fila_de_zona(v, "Europe/Madrid"))
        casos = (("fijar_fecha_hora", lambda: v.hora_boton.emit("clicked"), v.hora_aviso),
                 ("fijar_zona_horaria", lambda: v.zona_boton.emit("clicked"), v.zona_aviso),
                 ("fijar_hora_automatica", lambda: v.hora_ntp.set_active(True), v.hora_aviso_ntp))
        for funcion, accion, aviso in casos:
            with self.subTest(funcion=funcion), mock.patch.object(self.A, funcion, side_effect=RuntimeError("raro")), \
                    contextlib.redirect_stderr(io.StringIO()) as registro:
                accion()
                self.assertTrue(self.bombear(lambda: "error inesperado" in aviso.get_text()), funcion)
                self.assertTrue(aviso.has_css_class("error"))
                self.assertIn("RuntimeError: raro", registro.getvalue())   # el detalle va al registro de la sesión
                self.assertTrue(v.hora_boton.get_sensitive())
                self.assertFalse(v.hora_rueda.get_spinning())

    def test_al_mostrar_la_pagina_se_lee_el_sistema_y_corre_el_reloj(self):
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        v._lanzar = lambda trabajo, listo, ocupado=True, aviso=None: listo(*trabajo())
        v.present()
        v.pila.set_visible_child_name("fecha")
        self.assertTrue(self.bombear(lambda: v._tic_hora_id != 0, 3.0))
        self.assertTrue(self.bombear(lambda: v.hora_zona.get_text() == "Zona horaria: America/Santiago", 3.0))
        self.assertGreaterEqual(self.A.leer_estado_hora.call_count, 1)
        self.assertEqual(len(v._zona_de_fila), len(ZONAS))
        # al irse a otra página, el reloj se para
        v.pila.set_visible_child_name("sistema")
        self.assertTrue(self.bombear(lambda: v._tic_hora_id == 0, 3.0))

    def test_cerrar_la_ventana_para_el_reloj(self):
        v = self.X.VentanaAjustes(self.app)
        self.addCleanup(v.destroy)
        v._lanzar = lambda trabajo, listo, ocupado=True, aviso=None: listo(*trabajo())
        v.present()
        v.pila.set_visible_child_name("fecha")
        self.assertTrue(self.bombear(lambda: v._tic_hora_id != 0, 3.0))
        v.close()
        self.assertTrue(v._cerrada)
        self.assertEqual(v._tic_hora_id, 0)

    # ----------------------------------------------------- guardado de cambios, en Sistema
    def persistencia(self, modo):
        A = self.A
        return {
            "persistente": A.Persistencia("persistente", A.TITULAR_GUARDA,
                                          "La capa de escritura está en una partición de disco (ext4, /dev/sdb2), "
                                          "no en la RAM.", ""),
            "vivo": A.Persistencia("vivo", A.TITULAR_EN_VIVO, "Lo que cambies solo vive en la memoria RAM.",
                                   A.PISTA_GUARDADO),
        }[modo]

    def test_sistema_muestra_el_guardado_en_vivo_con_motivo_y_pista(self):
        with mock.patch.object(self.A, "estado_persistencia", return_value=self.persistencia("vivo")):
            v = self.ventana()
        textos = self.textos(v.pila.get_child_by_name("sistema"))
        self.assertIn("Guardado de cambios", textos)
        self.assertIn("Modo en vivo: lo que cambies se pierde al apagar", textos)
        self.assertIn("Lo que cambies solo vive en la memoria RAM.", textos)
        self.assertIn(self.A.PISTA_GUARDADO, textos)
        self.assertTrue(v.persistencia_titular.has_css_class("warning"))

    def test_sistema_muestra_que_se_guarda(self):
        with mock.patch.object(self.A, "estado_persistencia", return_value=self.persistencia("persistente")):
            v = self.ventana()
        textos = self.textos(v.pila.get_child_by_name("sistema"))
        self.assertIn("Se guardan tus cambios al apagar", textos)
        self.assertFalse(any("mkfs.ext4" in t for t in textos))   # sin pista: ya se guarda
        self.assertFalse(v.persistencia_titular.has_css_class("warning"))

    def test_la_fila_va_junto_a_los_datos_del_sistema(self):
        v = self.ventana()
        textos = self.textos(v.pila.get_child_by_name("sistema"))
        for dato in ("Sistema operativo", "Memoria", "Zona horaria", "Teclado de la consola", "Idioma",
                     "Guardado de cambios"):
            self.assertIn(dato, textos)
        self.assertTrue(any("«Fecha y hora»" in t for t in textos))   # la ayuda ya no manda a dpkg-reconfigure tzdata
        self.assertFalse(any("tzdata" in t for t in textos))

    def test_el_aviso_de_la_iso_en_vivo_desaparece_si_se_guarda(self):
        """Con la partición de guardado, «se pierde al apagar» sería falso: el aviso de arriba y el de Cuenta se van."""
        aviso = "En la ISO en vivo, la contraseña se pierde al apagar el equipo."
        for modo, se_ve in (("vivo", True), ("persistente", False)):
            with self.subTest(modo=modo), mock.patch.object(self.X, "_en_vivo", return_value=True), \
                    mock.patch.object(self.A, "estado_persistencia", return_value=self.persistencia(modo)):
                self.assertIs(self.X._se_pierde_al_apagar(), se_ve)
                v = self.X.VentanaAjustes(self.app)
                self.addCleanup(v.destroy)
                self.assertIs(aviso in self.textos(v.pila.get_child_by_name("sistema")), se_ve)
                raiz = self.textos(v.get_child())
                self.assertIs(any("Estás usando la ISO en vivo" in t for t in raiz), se_ve)

    def test_no_en_vivo_nunca_hay_aviso(self):
        with mock.patch.object(self.X, "_en_vivo", return_value=False), \
                mock.patch.object(self.A, "estado_persistencia", return_value=self.persistencia("vivo")):
            self.assertFalse(self.X._se_pierde_al_apagar())


if __name__ == "__main__":
    unittest.main()
