"""La ventana de Contraseñas (boveda_app.py), con GTK 4.

Como test_launcher.py: se salta sin pantalla (DISPLAY) o sin GTK 4; corre con xvfb-run. También se salta sin el
paquete `cryptography`. Todos los datos son FALSOS, la bóveda vive en una carpeta temporal y scrypt va reducido
(N=2**10: parámetros que SOLO existen en las pruebas). El reloj de la inactividad, el ejecutor (el scrypt de la
ventana corre en un hilo) y el portapapeles son dobles inyectados, salvo en las pruebas que quieren lo real.
"""
import gc
import os
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
SHELL = os.path.join(HERE, "..")
PANTALLA = bool(os.environ.get("DISPLAY"))

try:
    import cryptography  # noqa: F401
except ImportError:
    cryptography = None

MAESTRA = "maestra-de-prueba-1"
OTRA = "otra-maestra-de-prueba-2"
MARCA = "MARCA-SECRETA-93af"


class Reloj:
    """Reloj falso: solo avanza cuando la prueba lo dice."""

    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t

    def avanzar(self, segundos):
        self.t += segundos


def ejecutar_sincrono(trabajo, fin):
    """Como el ejecutor de la ventana, pero sin hilo: entrega el resultado en el acto."""
    try:
        resultado = (trabajo(), None)
    except BaseException as e:  # noqa: BLE001
        resultado = (None, e)
    fin(*resultado)


class PortapapelesFalso:
    """Lo que la ventana espera de un portapapeles: poner, leer, borrar y borrar_si_es_nuestro."""

    def __init__(self):
        self.contenido = None
        self.es_nuestro = False
        self.borrados = 0

    def poner(self, texto):
        self.contenido, self.es_nuestro = texto, True

    def leer(self, devolver):
        devolver(self.contenido)

    def borrar(self):
        self.contenido, self.es_nuestro, self.borrados = None, False, self.borrados + 1

    def borrar_si_es_nuestro(self):
        if self.es_nuestro:
            self.borrar()

    def alguien_copia(self, texto):
        self.contenido, self.es_nuestro = texto, False


@unittest.skipUnless(PANTALLA, "necesita una pantalla (xvfb-run)")
@unittest.skipUnless(cryptography, "necesita python3-cryptography")
class BaseVentana(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            import gi
            gi.require_version("Gtk", "4.0")
            gi.require_version("Gdk", "4.0")
            from gi.repository import Gdk, GLib, Gtk
        except (ImportError, ValueError) as e:
            raise unittest.SkipTest(f"sin GTK 4: {e}")
        sys.path.insert(0, SHELL)
        from infinity_shell import boveda as B
        from infinity_shell import boveda_app as X
        cls.B, cls.X, cls.Gdk, cls.GLib, cls.Gtk = B, X, Gdk, GLib, Gtk
        cls.KDF_T = B.ParamsKDF(10, 8, 1)
        cls.LIM_T = B.LimitesKDF(log2n=(10, 20))

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.carpeta = os.path.join(self.tmp, "infinity", "boveda")
        self.ruta = os.path.join(self.carpeta, "boveda.enc")
        self.reloj = Reloj()
        self.cb = PortapapelesFalso()
        self.app = self.Gtk.Application(application_id=f"org.infinity.test.boveda{time.time_ns()}")
        self.app.register(None)

    # ---------------------------------------------------------------- utilidades
    def nueva_boveda(self):
        return self.B.Boveda(self.ruta, kdf=self.KDF_T, limites=self.LIM_T)

    def con_datos(self, n=3, marca=False):
        """Crea la bóveda en disco con datos FALSOS y la deja cerrada."""
        b = self.nueva_boveda()
        b.crear(MAESTRA)
        if marca:
            b.agregar(MARCA + "-servicio", MARCA + "-usuario", MARCA + "-clave", MARCA + "-url", MARCA + "-notas", "Web")
        for i in range(n):
            b.agregar(f"Servicio {i}", f"usuario{i}@ejemplo.test", f"clave-falsa-{i}", f"10.0.0.{i}", f"nota {i}",
                      self.B.CATEGORIAS[i % len(self.B.CATEGORIAS)])
        b.bloquear()

    def ventana(self, **kw):
        opciones = dict(ruta=self.ruta, kdf=self.KDF_T, limites=self.LIM_T, reloj=self.reloj,
                        ejecutor=ejecutar_sincrono, portapapeles=self.cb, vigilar_suspension=False)
        opciones.update(kw)
        v = self.X.VentanaBoveda(self.app, **opciones)
        self.addCleanup(v.destroy)
        self.addCleanup(v._al_cerrar)  # cancela los temporizadores antes de destruir
        return v

    def abierta(self, **kw):
        v = self.ventana(**kw)
        self.desbloquear(v)
        return v

    def desbloquear(self, v, clave=MAESTRA):
        v.entrada_clave.set_text(clave)
        v._entrar()
        self.pump_hasta(lambda: not v._ocupado)  # con el ejecutor síncrono ya terminó; con el de hilos, espera

    def pump(self, segundos):
        ctx = self.GLib.MainContext.default()
        fin = time.monotonic() + segundos
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            time.sleep(0.002)

    def pump_hasta(self, condicion, limite=5.0):
        ctx = self.GLib.MainContext.default()
        fin = time.monotonic() + limite
        while time.monotonic() < fin:
            while ctx.pending():
                ctx.iteration(False)
            if condicion():
                return True
            time.sleep(0.002)
        return condicion()

    def hijos(self, raiz):
        salida, h = [], raiz.get_first_child()
        while h is not None:
            salida.append(h)
            h = h.get_next_sibling()
        return salida

    def textos(self, raiz):
        """TODO el texto que hay en los widgets bajo `raiz` (en orden de documento): etiquetas, botones, campos,
        cuadros de texto y tooltips."""
        salida = []

        def visitar(w):
            if isinstance(w, self.Gtk.Label):
                salida.append(w.get_text())
            if isinstance(w, self.Gtk.Button) and w.get_label():
                salida.append(w.get_label())
            if isinstance(w, self.Gtk.Editable):
                salida.append(w.get_text())
            if isinstance(w, self.Gtk.TextView):
                buf = w.get_buffer()
                salida.append(buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False))
            if w.get_tooltip_text():
                salida.append(w.get_tooltip_text())
            for h in self.hijos(w):
                visitar(h)

        visitar(raiz)
        return salida

    def en_pantalla(self, raiz):
        return "\n".join(self.textos(raiz))

    def filas(self, v):
        """Los nombres de servicio de la lista, en el orden en que se ven."""
        salida, i = [], 0
        while (fila := v.lista.get_row_at_index(i)) is not None:
            salida.append(v.boveda.obtener(fila.entrada_id).servicio)
            i += 1
        return salida

    def elegir(self, v, servicio):
        entrada = next(e for e in v.boveda.entradas if e.servicio == servicio)
        v.lista.select_row(v._filas[entrada.id])
        return entrada

    def pagina(self, v):
        return v.pila.get_visible_child_name()



# ===================================================================================== pantalla bloqueada
class TestPantallaBloqueada(BaseVentana):
    def test_ventana_y_aplicacion(self):
        v = self.ventana()
        self.assertEqual(v.get_title(), "Contraseñas")
        self.assertEqual(v.get_default_size(), (880, 600))
        self.assertTrue(v.has_css_class("boveda-ventana"))
        self.assertEqual(self.X.APP_ID, "org.infinity.Boveda")
        self.assertEqual(self.X.AppBoveda().get_application_id(), "org.infinity.Boveda")

    def test_sin_boveda_ofrece_crearla(self):
        v = self.ventana()
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "crear")
        todo = self.en_pantalla(v.pila_acceso)
        self.assertIn("Crea tu contraseña maestra", todo)
        self.assertIn("Si la olvidas, tus contraseñas no se pueden recuperar", todo)
        self.assertEqual(v.form_crear.boton.get_label(), "Crear bóveda")
        self.assertIsInstance(v.form_crear.nueva, self.Gtk.PasswordEntry)
        self.assertIsInstance(v.form_crear.repite, self.Gtk.PasswordEntry)
        self.assertFalse(v.boton_bloquear.get_visible())
        self.assertFalse(v.menu.get_visible())
        self.assertFalse(os.path.exists(self.carpeta), "mirar la pantalla no crea nada en disco")

    def test_con_boveda_pide_la_contrasena(self):
        self.con_datos()
        v = self.ventana()
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "entrar")
        self.assertIsInstance(v.entrada_clave, self.Gtk.PasswordEntry)
        self.assertTrue(v.entrada_clave.get_show_peek_icon(), "el ojo para mostrar lo que se escribe")
        self.assertEqual(v.boton_entrar.get_label(), "Entrar")
        self.assertIn("Contraseñas", self.textos(v.pila_acceso))
        self.assertFalse(v.aviso_entrar.get_visible())
        self.assertFalse(v.boton_copia.get_visible())

    def test_los_campos_de_la_maestra_usan_el_buffer_de_memoria_protegida_de_gtk(self):
        v = self.ventana()
        for campo in (v.entrada_clave, v.form_crear.nueva, v.form_crear.repite, v.form_cambiar.actual,
                      v.form_cambiar.nueva, v.form_cambiar.repite):
            self.assertIsInstance(campo, self.Gtk.PasswordEntry)
            self.assertEqual(campo.get_delegate().get_buffer().__gtype__.name, "GtkPasswordEntryBuffer")

    def test_foco_automatico_en_el_campo(self):
        self.con_datos()
        v = self.ventana()
        v.present()
        self.pump(0.2)
        foco = v.get_focus()
        self.assertIsNotNone(foco)
        self.assertTrue(foco is v.entrada_clave or foco.is_ancestor(v.entrada_clave))

    def test_foco_automatico_al_crear(self):
        v = self.ventana()
        v.present()
        self.pump(0.2)
        foco = v.get_focus()
        self.assertTrue(foco is v.form_crear.nueva or foco.is_ancestor(v.form_crear.nueva))

    def test_enter_entra(self):
        self.con_datos()
        v = self.ventana()
        v.entrada_clave.set_text(MAESTRA)
        v.entrada_clave.emit("activate")
        self.assertEqual(self.pagina(v), "abierta")

    def test_sin_escribir_nada_no_cuenta_como_fallo(self):
        self.con_datos()
        v = self.ventana()
        with mock.patch.object(v.boveda, "abrir") as abrir:
            v._entrar()
        abrir.assert_not_called()
        self.assertEqual(v._fallos, 0)
        self.assertIn("Escribe tu contraseña maestra", v.aviso_entrar.get_text())


# ======================================================================================== crear la bóveda
class TestCrear(BaseVentana):
    def rellenar(self, v, nueva, repite):
        v.form_crear.nueva.set_text(nueva)
        v.form_crear.repite.set_text(repite)
        v._crear()

    def test_contrasenas_que_no_coinciden(self):
        v = self.ventana()
        self.rellenar(v, "una-maestra-buena", "una-maestra-buenA")
        self.assertEqual(v.form_crear.error.get_text(), "Las dos contraseñas no coinciden.")
        self.assertTrue(v.form_crear.error.get_visible())
        self.assertFalse(os.path.exists(self.ruta))

    def test_no_se_aceptan_contrasenas_cortas_vacias_ni_de_espacios(self):
        v = self.ventana()
        casos = (("", "", "no puede estar vacía"), ("          ", "          ", "no puede estar vacía"),
                 ("corta", "corta", "al menos 8 caracteres"), ("1234567", "1234567", "al menos 8 caracteres"))
        for nueva, repite, texto in casos:
            with self.subTest(nueva=nueva):
                self.rellenar(v, nueva, repite)
                self.assertIn(texto, v.form_crear.error.get_text())
                self.assertFalse(os.path.exists(self.ruta))
                self.assertEqual(v.pila_acceso.get_visible_child_name(), "crear")

    def test_crea_y_entra_con_la_boveda_vacia(self):
        v = self.ventana()
        self.rellenar(v, "una-maestra-buena", "una-maestra-buena")
        self.assertEqual(self.pagina(v), "abierta")
        self.assertTrue(os.path.isfile(self.ruta))
        self.assertEqual(oct(os.stat(self.ruta).st_mode & 0o777), "0o600")
        self.assertEqual(oct(os.stat(self.carpeta).st_mode & 0o777), "0o700")
        self.assertTrue(v.boveda.abierta)
        self.assertEqual(v.form_crear.nueva.get_text(), "", "los campos de la contraseña se vacían")
        self.assertEqual(v.form_crear.repite.get_text(), "")
        self.assertTrue(v.boton_bloquear.get_visible())
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "vacio")

    def test_la_boveda_creada_se_abre_despues_con_esa_contrasena(self):
        v = self.ventana()
        self.rellenar(v, "una-maestra-buena", "una-maestra-buena")
        v.bloquear()
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "entrar")
        self.desbloquear(v, "una-maestra-buena")
        self.assertEqual(self.pagina(v), "abierta")

    def test_el_medidor_de_fortaleza_sigue_lo_que_se_escribe(self):
        v = self.ventana()
        medidor = v.form_crear.medidor
        v.form_crear.nueva.set_text("password")
        self.assertEqual(medidor.puntos, 0)
        self.assertIn("Muy débil", medidor.etiqueta.get_text())
        v.form_crear.nueva.set_text(self.B.generar_contrasena(24))
        self.assertEqual(medidor.puntos, 4)
        self.assertIn("Excelente", medidor.etiqueta.get_text())
        v.form_crear.nueva.set_text("")
        self.assertEqual(medidor.etiqueta.get_text(), "")

    def test_el_aviso_de_no_recuperable_esta_a_la_vista(self):
        v = self.ventana()
        self.assertTrue(v.aviso_no_recuperable.get_visible())
        self.assertIn("si la olvidas", v.aviso_no_recuperable.get_text().lower())

    def test_mientras_se_crea_los_campos_no_se_pueden_tocar(self):
        v = self.ventana(ejecutor=lambda trabajo, fin: None)  # el trabajo nunca termina: se ve el estado «ocupado»
        v.form_crear.nueva.set_text("una-maestra-buena")
        v.form_crear.repite.set_text("una-maestra-buena")
        v._crear()
        self.assertTrue(v._ocupado)
        self.assertFalse(v.form_crear.nueva.get_editable())
        self.assertFalse(v.form_crear.repite.get_editable())
        self.assertFalse(v.form_crear.boton.get_sensitive())
        self.assertTrue(v.form_crear.actividad.get_visible())
        self.assertIn("Creando", self.en_pantalla(v.form_crear.actividad))
        v._crear()  # no arranca otra operación encima
        self.assertTrue(v._ocupado)


# ================================================================================== contraseña incorrecta
class TestContrasenaIncorrecta(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos()

    def intentar(self, v, clave):
        v.entrada_clave.set_text(clave)
        v._entrar()

    def test_mensaje_y_no_se_abre(self):
        v = self.ventana()
        self.intentar(v, "equivocada-123")
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertFalse(v.boveda.abierta)
        self.assertIn("Contraseña incorrecta", v.aviso_entrar.get_text())
        self.assertTrue(v.aviso_entrar.get_visible())
        self.assertTrue(v.aviso_entrar.has_css_class("boveda-error"))
        self.assertEqual(v._fallos, 1)

    def test_espera_creciente_1_2_4_8_16_30_30(self):
        v = self.ventana()
        for esperado in (1, 2, 4, 8, 16, 30, 30):
            self.intentar(v, "equivocada-123")
            self.assertEqual(v._hasta - self.reloj(), esperado)
            self.assertIn(f"Espera {esperado} s", v.aviso_entrar.get_text())
            self.assertFalse(v.entrada_clave.get_editable(), "mientras se espera no se puede escribir")
            self.assertFalse(v.boton_entrar.get_sensitive())
            self.reloj.avanzar(esperado + 0.1)
            v._actualizar_espera()
            self.assertTrue(v.entrada_clave.get_editable())
            self.assertTrue(v.boton_entrar.get_sensitive())

    def test_la_cuenta_atras_baja_cada_segundo(self):
        v = self.ventana()
        for _ in range(3):  # tercer fallo seguido: 4 s
            self.intentar(v, "equivocada-123")
            self.reloj.avanzar(100)
            v._actualizar_espera()
        self.intentar(v, "equivocada-123")  # cuarto: 8 s
        self.assertIn("Espera 8 s", v.aviso_entrar.get_text())
        self.reloj.avanzar(2.5)
        v._actualizar_espera()
        self.assertIn("Espera 6 s", v.aviso_entrar.get_text())

    def test_mientras_se_espera_no_se_intenta_ni_con_la_buena(self):
        v = self.ventana()
        self.intentar(v, "equivocada-123")
        with mock.patch.object(v.boveda, "abrir") as abrir:
            self.intentar(v, MAESTRA)
            v.entrada_clave.emit("activate")
            v.boton_entrar.emit("clicked")
        abrir.assert_not_called()
        self.assertFalse(v.boveda.abierta)
        self.reloj.avanzar(1.01)
        v._actualizar_espera()
        self.intentar(v, MAESTRA)
        self.assertEqual(self.pagina(v), "abierta")

    def test_acertar_pone_el_contador_a_cero(self):
        v = self.ventana()
        for _ in range(3):
            self.intentar(v, "equivocada-123")
            self.reloj.avanzar(100)
            v._actualizar_espera()
        self.intentar(v, MAESTRA)
        self.assertEqual(v._fallos, 0)
        v.bloquear()
        self.intentar(v, "equivocada-123")
        self.assertEqual(v._hasta - self.reloj(), 1, "tras un acierto, la espera vuelve a empezar en 1 s")

    def test_la_espera_sobrevive_a_un_bloqueo(self):
        v = self.abierta()
        v.bloquear()
        v._fallos, v._hasta = 4, self.reloj() + 8
        v._actualizar_espera()
        self.assertIn("Espera 8 s", v.aviso_entrar.get_text())
        self.assertFalse(v.entrada_clave.get_editable())

    def test_el_campo_queda_seleccionado_para_reescribir(self):
        v = self.ventana()
        self.intentar(v, "equivocada-123")
        seleccion = v.entrada_clave.get_selection_bounds()
        self.assertTrue(seleccion and seleccion[-1] - seleccion[-2] == len("equivocada-123"),
                        "todo el texto queda seleccionado: lo siguiente que se teclee lo sustituye")

    def test_una_clave_incorrecta_no_toca_el_fichero(self):
        with open(self.ruta, "rb") as f:
            antes = f.read()
        v = self.ventana()
        self.intentar(v, "equivocada-123")
        with open(self.ruta, "rb") as f:
            self.assertEqual(f.read(), antes)

    def test_el_mensaje_no_repite_lo_escrito(self):
        v = self.ventana()
        self.intentar(v, MARCA)
        self.assertNotIn(MARCA, v.aviso_entrar.get_text())


class TestFicheroDanado(BaseVentana):
    def danar(self):
        with open(self.ruta, "rb") as f:
            datos = bytearray(f.read())
        datos[-3] ^= 0xFF
        with open(self.ruta, "wb") as f:
            f.write(bytes(datos))

    def test_danado_ofrece_la_copia_y_la_abre(self):
        self.con_datos(n=2)
        b = self.nueva_boveda()
        b.abrir(MAESTRA)
        b.agregar("Tercera")  # ahora hay .bak (con 2 entradas) y principal (3)
        b.bloquear()
        self.danar()
        v = self.ventana()
        self.desbloquear(v)
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertIn("dañado", v.aviso_entrar.get_text())
        self.assertNotIn("incorrecta", v.aviso_entrar.get_text())
        self.assertTrue(v.boton_copia.get_visible())
        self.assertEqual(v._fallos, 0, "un fichero dañado no es una contraseña equivocada: sin espera")
        v.boton_copia.emit("clicked")
        self.assertFalse(v.boton_copia.get_visible())
        self.desbloquear(v)
        self.assertEqual(self.pagina(v), "abierta")
        self.assertEqual(len(v.boveda.entradas), 2)
        self.assertEqual(len(self.filas(v)), 2)

    def test_danado_sin_copia_no_ofrece_nada(self):
        self.con_datos(n=0)
        self.danar()
        v = self.ventana()
        self.desbloquear(v)
        self.assertIn("dañado", v.aviso_entrar.get_text())
        self.assertFalse(v.boton_copia.get_visible())

    def test_tras_varios_fallos_con_copia_se_sugiere_usarla(self):
        self.con_datos(n=2)
        b = self.nueva_boveda()
        b.abrir(MAESTRA)
        b.agregar("x")
        b.bloquear()
        v = self.ventana()
        for i in range(3):
            self.assertFalse(v.boton_copia.get_visible(), f"intento {i}")
            v.entrada_clave.set_text("equivocada-123")
            v._entrar()
            self.reloj.avanzar(100)
            v._actualizar_espera()
        self.assertTrue(v.boton_copia.get_visible())

    def test_version_desconocida_se_dice_claro(self):
        self.con_datos(n=1)
        with open(self.ruta, "rb") as f:
            datos = bytearray(f.read())
        datos[8:10] = b"\x00\x09"
        with open(self.ruta, "wb") as f:
            f.write(bytes(datos))
        v = self.ventana()
        self.desbloquear(v)
        self.assertIn("versión 9", v.aviso_entrar.get_text())

    def test_falta_el_principal_y_hay_copia(self):
        self.con_datos(n=2)
        b = self.nueva_boveda()
        b.abrir(MAESTRA)
        b.agregar("x")
        b.bloquear()
        os.unlink(self.ruta)
        v = self.ventana()
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "entrar")
        self.assertIn("copia de seguridad", v.aviso_entrar.get_text())
        self.desbloquear(v)
        self.assertEqual(self.pagina(v), "abierta")
        self.assertTrue(os.path.isfile(self.ruta), "el principal se restaura")

    def test_carpeta_que_es_un_enlace_simbolico(self):
        real = os.path.join(self.tmp, "real")
        os.makedirs(real)
        os.makedirs(os.path.dirname(self.carpeta))
        os.symlink(real, self.carpeta)
        v = self.ventana()
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "entrar")
        self.assertIn("no es segura", v.aviso_entrar.get_text())


# ====================================================================================== desbloqueo
class TestDesbloqueo(BaseVentana):
    def test_desbloquea_y_muestra_las_entradas(self):
        self.con_datos(n=5)
        v = self.abierta()
        self.assertEqual(self.pagina(v), "abierta")
        self.assertTrue(v.boton_bloquear.get_visible())
        self.assertTrue(v.menu.get_visible())
        self.assertEqual(self.filas(v), [f"Servicio {i}" for i in range(5)])
        self.assertEqual(v.entrada_clave.get_text(), "", "la contraseña escrita se borra del campo")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "detalle", "la primera queda seleccionada")
        self.assertEqual(v.d_servicio.get_text(), "Servicio 0")

    def test_contadores_de_categorias(self):
        self.con_datos(n=7)  # 7 entradas repartidas en 5 categorías: 2,2,1,1,1
        v = self.abierta()
        cuentas = {n: e.get_text() for n, e in v._contadores.items()}
        self.assertEqual(cuentas, {"Todas": "7", "Correo": "2", "Router y red": "2", "Web": "1", "Banco": "1", "Otros": "1"})
        self.assertEqual([r.categoria for r in self.hijos(v.lista_categorias)],
                         ["Todas", "Correo", "Router y red", "Web", "Banco", "Otros"])

    def test_el_scrypt_corre_en_un_hilo_y_la_interfaz_sigue_viva(self):
        self.con_datos(n=2)
        v = self.ventana(ejecutor=None)
        real = v.boveda.abrir
        hilos = []

        def lento(*a, **kw):
            hilos.append(threading.current_thread())
            time.sleep(0.4)  # lo que tardaría scrypt de verdad
            return real(*a, **kw)

        v.boveda.abrir = lento
        v.entrada_clave.set_text(MAESTRA)
        t0 = time.monotonic()
        v._entrar()
        self.assertLess(time.monotonic() - t0, 0.2, "_entrar no se queda esperando al scrypt")
        # Mientras tanto: «Comprobando…», campos y botón desactivados
        self.assertTrue(v._ocupado)
        self.assertTrue(v.actividad_entrar.get_visible())
        self.assertIn("Comprobando", self.en_pantalla(v.actividad_entrar))
        self.assertFalse(v.entrada_clave.get_editable())
        self.assertFalse(v.boton_entrar.get_sensitive())
        # ...y el bucle de eventos sigue girando (se pintaría, se movería el ratón)
        latidos = []
        tic = self.GLib.timeout_add(20, lambda: latidos.append(1) or True)
        self.pump(0.25)
        self.GLib.source_remove(tic)
        self.assertGreaterEqual(len(latidos), 5)
        self.assertTrue(self.pump_hasta(lambda: v.boveda.abierta))
        self.assertTrue(self.pump_hasta(lambda: not v._ocupado))
        self.assertEqual(self.pagina(v), "abierta")
        self.assertEqual(len(hilos), 1)
        self.assertIsNot(hilos[0], threading.main_thread())
        self.assertFalse(v.actividad_entrar.get_visible())

    def test_error_en_el_hilo_se_entrega_en_el_principal(self):
        self.con_datos()
        v = self.ventana(ejecutor=None)
        v.entrada_clave.set_text("equivocada-123")
        v._entrar()
        self.assertTrue(self.pump_hasta(lambda: not v._ocupado))
        self.assertIn("incorrecta", v.aviso_entrar.get_text())
        self.assertTrue(v.entrada_clave.get_editable() or v._esperando())

    def test_si_se_bloquea_mientras_se_comprueba_el_resultado_se_descarta(self):
        self.con_datos()
        v = self.ventana(ejecutor=None)
        real = v.boveda.abrir

        def lento(*a, **kw):
            time.sleep(0.3)
            return real(*a, **kw)

        v.boveda.abrir = lento
        v.entrada_clave.set_text(MAESTRA)
        v._entrar()
        v.bloquear()  # llega el bloqueo (suspensión, p. ej.) antes de que acabe el scrypt
        self.assertTrue(self.pump_hasta(lambda: not v._ocupado))
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertFalse(v.boveda.abierta, "una bóveda abierta tarde se vuelve a cerrar")

    def test_el_error_no_conserva_la_contrasena_en_su_traza(self):
        self.con_datos()
        v = self.ventana(ejecutor=None)
        capturado = []
        original = v._fin_entrar
        v._fin_entrar = lambda valor, error: (capturado.append(error), original(valor, error))
        v.entrada_clave.set_text("equivocada-123")
        v._entrar()
        self.assertTrue(self.pump_hasta(lambda: not v._ocupado))
        self.assertEqual(len(capturado), 1)
        self.assertIsNone(capturado[0].__traceback__, "la traza (y con ella las variables locales) se suelta")


# =================================================================================== lista y buscador
class TestListaYBuscador(BaseVentana):
    def setUp(self):
        super().setUp()
        b = self.nueva_boveda()
        b.crear(MAESTRA)
        for f in (("Gmail", "ana.perez@ejemplo.test", "clave-falsa-1", "https://mail.ejemplo.test", "personal", "Correo"),
                  ("Router de casa", "admin", "clave-falsa-2", "192.168.1.1", "caja del salón", "Router y red"),
                  ("Netflix", "ana.perez@ejemplo.test", "clave-falsa-3", "", "", "Web"),
                  ("Banco Ejemplo", "12345678", "clave-falsa-4", "", "", "Banco"),
                  ("Cámara del jardín", "admin", "clave-falsa-5", "192.168.1.50", "", "Router y red")):
            b.agregar(*f)
        b.bloquear()
        self.v = self.abierta()

    def test_orden_y_texto_de_las_filas(self):
        self.assertEqual(self.filas(self.v), ["Banco Ejemplo", "Cámara del jardín", "Gmail", "Netflix", "Router de casa"])
        primera = self.textos(self.v.lista.get_row_at_index(0))
        self.assertEqual(primera, ["B", "Banco Ejemplo", "12345678"])

    def buscar(self, texto):
        self.v.busqueda.set_text(texto)
        self.v._refrescar()
        return self.filas(self.v)

    def test_buscar(self):
        self.assertEqual(self.buscar("ana"), ["Gmail", "Netflix"])
        self.assertEqual(self.buscar("192.168"), ["Cámara del jardín", "Router de casa"])
        self.assertEqual(self.buscar("camara"), ["Cámara del jardín"])
        self.assertEqual(self.buscar("clave-falsa"), [], "la contraseña no se busca")
        self.assertEqual(self.buscar(""), self.filas(self.v))

    def test_sin_resultados_lo_dice(self):
        self.assertEqual(self.buscar("zzzz"), [])
        self.assertTrue(self.v.sin_resultados.get_visible())
        self.assertIn("Nada coincide con «zzzz»", self.v.sin_resultados.get_text())
        self.assertEqual(self.v.pila_detalle.get_visible_child_name(), "vacio")
        self.buscar("")
        self.assertFalse(self.v.sin_resultados.get_visible())

    def test_filtrar_por_categoria(self):
        self.v.lista_categorias.select_row(self.v._filas_categoria["Router y red"])
        self.assertEqual(self.filas(self.v), ["Cámara del jardín", "Router de casa"])
        self.assertEqual(self.buscar("casa"), ["Router de casa"])
        self.v.lista_categorias.select_row(self.v._filas_categoria["Otros"])
        self.assertEqual(self.buscar(""), [])
        self.assertIn("No hay contraseñas en esta categoría", self.v.sin_resultados.get_text())
        self.v.lista_categorias.select_row(self.v._filas_categoria["Todas"])
        self.assertEqual(len(self.filas(self.v)), 5)

    def test_contadores(self):
        cuentas = {n: e.get_text() for n, e in self.v._contadores.items()}
        self.assertEqual(cuentas, {"Todas": "5", "Correo": "1", "Router y red": "2", "Web": "1", "Banco": "1", "Otros": "0"})

    def test_elegir_una_fila_muestra_su_detalle(self):
        self.elegir(self.v, "Router de casa")
        self.assertEqual(self.v.d_servicio.get_text(), "Router de casa")
        self.assertEqual(self.v.d_usuario.get_text(), "admin")
        self.assertEqual(self.v.d_url.get_text(), "192.168.1.1")
        self.assertEqual(self.v.d_notas.get_text(), "caja del salón")
        self.assertEqual(self.v.d_categoria.get_text(), "Router y red")
        self.assertTrue(self.v.tarjeta_url.get_visible())
        self.assertTrue(self.v.tarjeta_notas.get_visible())
        self.elegir(self.v, "Banco Ejemplo")
        self.assertFalse(self.v.tarjeta_url.get_visible(), "sin URL no se enseña la tarjeta")
        self.assertFalse(self.v.tarjeta_notas.get_visible())

    def test_la_seleccion_sobrevive_a_un_filtro_que_la_incluye(self):
        e = self.elegir(self.v, "Netflix")
        self.buscar("net")
        self.assertEqual(self.v._seleccion, e.id)
        self.assertEqual(self.v.d_servicio.get_text(), "Netflix")


# ===================================================================================== detalle
class TestDetalle(BaseVentana):
    def setUp(self):
        super().setUp()
        b = self.nueva_boveda()
        b.crear(MAESTRA)
        self.entrada = b.agregar("Gmail", "ana@ejemplo.test", "clave-falsa-Zq7#", "https://mail.ejemplo.test", "notas falsas", "Correo")
        b.bloquear()
        self.v = self.abierta()

    def test_la_contrasena_esta_oculta_con_puntos(self):
        v = self.v
        self.assertEqual(v.d_clave.get_text(), self.X.PUNTOS)
        self.assertNotIn("clave-falsa", self.en_pantalla(v))
        self.assertEqual(v.b_ver_clave.get_icon_name(), "view-reveal-symbolic")

    def test_los_puntos_no_dicen_la_longitud(self):
        b = self.v.boveda
        corta = b.agregar("Corta", contrasena="abc12345")
        larga = b.agregar("Larga", contrasena="x" * 200)
        self.v._refrescar(seleccionar=corta.id)
        a = self.v.d_clave.get_text()
        self.v._refrescar(seleccionar=larga.id)
        self.assertEqual(a, self.v.d_clave.get_text())

    def test_mostrar_y_ocultar(self):
        v = self.v
        v.b_ver_clave.emit("clicked")
        self.assertEqual(v.d_clave.get_text(), "clave-falsa-Zq7#")
        self.assertEqual(v.b_ver_clave.get_icon_name(), "view-conceal-symbolic")
        v.b_ver_clave.emit("clicked")
        self.assertEqual(v.d_clave.get_text(), self.X.PUNTOS)
        self.assertEqual(v.b_ver_clave.get_icon_name(), "view-reveal-symbolic")

    def test_cambiar_de_entrada_vuelve_a_ocultarla(self):
        v = self.v
        v.boveda.agregar("Otra", contrasena="otra-clave-falsa")
        v._refrescar()
        v.b_ver_clave.emit("clicked")
        self.assertIn("clave-falsa", v.d_clave.get_text())
        self.elegir(v, "Otra")
        self.assertEqual(v.d_clave.get_text(), self.X.PUNTOS)
        self.assertEqual(v.b_ver_clave.get_icon_name(), "view-reveal-symbolic")

    def test_se_oculta_sola_con_el_tiempo(self):
        v = self.v
        v.b_ver_clave.emit("clicked")
        self.assertTrue(v._ids_tic["ocultar"])
        v._ocultar_clave_auto()
        self.assertEqual(v.d_clave.get_text(), self.X.PUNTOS)
        self.assertFalse(v._ids_tic["ocultar"])

    def test_url_notas_y_usuario_se_pueden_seleccionar_y_la_contrasena_no(self):
        v = self.v
        self.assertTrue(v.d_usuario.get_selectable())
        self.assertTrue(v.d_url.get_selectable())
        self.assertFalse(v.d_clave.get_selectable())

    def test_la_url_no_es_un_enlace(self):
        """Nada de abrir direcciones solo: la URL es un texto, no un GtkLinkButton ni un label con markup."""
        self.assertFalse(self.v.d_url.get_use_markup())
        pendientes, enlaces = [self.v], []
        while pendientes:
            w = pendientes.pop()
            if isinstance(w, self.Gtk.LinkButton):
                enlaces.append(w)
            pendientes.extend(self.hijos(w))
        self.assertEqual(enlaces, [])

    def test_fechas_legibles(self):
        self.assertRegex(self.v.d_fechas.get_text(), r"Creada: \d+ \w{3} \d{4}, \d\d:\d\d\nModificada: \d+ \w{3} \d{4}, \d\d:\d\d")


# ===================================================================================== alta / edición
class TestEditor(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos(n=2)
        self.v = self.abierta()

    def rellenar(self, servicio="", usuario="", clave="", url="", notas="", categoria=None):
        v = self.v
        v.e_servicio.set_text(servicio)
        v.e_usuario.set_text(usuario)
        v.e_clave.set_text(clave)
        v.e_url.set_text(url)
        v.e_notas.get_buffer().set_text(notas)
        if categoria:
            v.e_categoria.set_selected(self.B.CATEGORIAS.index(categoria))

    def test_nueva_entrada_abre_el_editor_vacio(self):
        v = self.v
        v.boton_nueva.emit("clicked")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")
        self.assertEqual(v.e_titulo.get_text(), "Nueva entrada")
        self.assertEqual((v.e_servicio.get_text(), v.e_usuario.get_text(), v.e_clave.get_text(), v.e_url.get_text()),
                         ("", "", "", ""))
        self.assertFalse(v.e_clave.get_visibility(), "la contraseña se escribe oculta")
        self.assertEqual(self.B.CATEGORIAS[v.e_categoria.get_selected()], "Correo")

    def test_alta(self):
        v = self.v
        v._nueva_entrada()
        self.rellenar("Banco del barrio", "12345678", "clave-falsa-#9", "https://banco.ejemplo.test", "nota\nsegunda línea", "Banco")
        v._guardar_editor()
        e = next(x for x in v.boveda.entradas if x.servicio == "Banco del barrio")
        self.assertEqual((e.usuario, e.contrasena, e.url, e.notas, e.categoria),
                         ("12345678", "clave-falsa-#9", "https://banco.ejemplo.test", "nota\nsegunda línea", "Banco"))
        self.assertIn("Banco del barrio", self.filas(v))
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "detalle")
        self.assertEqual(v.d_servicio.get_text(), "Banco del barrio", "la recién guardada queda seleccionada")
        self.assertEqual(v.texto_barra.get_text(), "Guardado.")
        self.assertEqual((v.e_servicio.get_text(), v.e_clave.get_text()), ("", ""), "el editor se vacía")
        v.bloquear()
        self.desbloquear(v)
        self.assertIn("Banco del barrio", self.filas(v), "persiste en disco")

    def test_alta_sin_servicio_no_guarda(self):
        v = self.v
        v._nueva_entrada()
        self.rellenar("   ", "x", "y")
        v._guardar_editor()
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")
        self.assertTrue(v.e_error.get_visible())
        self.assertIn("nombre del servicio es obligatorio", v.e_error.get_text())
        self.assertEqual(len(v.boveda.entradas), 2)

    def test_alta_con_un_campo_demasiado_largo_no_guarda(self):
        v = self.v
        v._nueva_entrada()
        self.rellenar("x", notas="n" * 6000)
        v._guardar_editor()
        self.assertTrue(v.e_error.get_visible())
        self.assertIn("notas", v.e_error.get_text())
        self.assertEqual(len(v.boveda.entradas), 2)

    def test_un_fallo_de_disco_se_muestra_y_no_pierde_lo_escrito(self):
        v = self.v
        v._nueva_entrada()
        self.rellenar("Servicio nuevo", "u", "clave-falsa")
        with mock.patch("os.replace", side_effect=OSError(28, "No space left on device")):
            v._guardar_editor()
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")
        self.assertIn("No space left", v.e_error.get_text())
        self.assertEqual(v.e_servicio.get_text(), "Servicio nuevo")
        self.assertEqual(len(v.boveda.entradas), 2)

    def test_editar(self):
        v = self.v
        e = self.elegir(v, "Servicio 1")
        v.b_editar.emit("clicked")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")
        self.assertEqual(v.e_titulo.get_text(), "Editar entrada")
        self.assertEqual(v.e_servicio.get_text(), "Servicio 1")
        self.assertEqual(v.e_clave.get_text(), "clave-falsa-1")
        self.assertEqual(self.B.CATEGORIAS[v.e_categoria.get_selected()], e.categoria)
        v.e_servicio.set_text("Servicio uno")
        v.e_clave.set_text("clave-falsa-nueva")
        v._guardar_editor()
        nueva = v.boveda.obtener(e.id)
        self.assertEqual((nueva.servicio, nueva.contrasena, nueva.id), ("Servicio uno", "clave-falsa-nueva", e.id))
        self.assertEqual(len([x for x in v.boveda.entradas if x.id == e.id]), 1, "se edita, no se duplica")
        self.assertEqual(v.d_servicio.get_text(), "Servicio uno")

    def test_cancelar_no_cambia_nada_y_vacia_los_campos(self):
        v = self.v
        self.elegir(v, "Servicio 1")
        v._editar_seleccionada()
        v.e_servicio.set_text("cambiado")
        v.e_cancelar.emit("clicked")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "detalle")
        self.assertEqual(v.boveda.obtener(v._seleccion).servicio, "Servicio 1")
        self.assertEqual((v.e_servicio.get_text(), v.e_clave.get_text()), ("", ""))

    def test_elegir_otra_fila_con_el_editor_abierto_descarta_y_no_deja_la_contrasena_en_el_campo(self):
        v = self.v
        self.elegir(v, "Servicio 0")
        v._editar_seleccionada()
        self.assertEqual(v.e_clave.get_text(), "clave-falsa-0")
        self.elegir(v, "Servicio 1")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "detalle")
        self.assertEqual(v.e_clave.get_text(), "")

    def test_la_categoria_se_preselecciona_con_el_filtro(self):
        v = self.v
        v.lista_categorias.select_row(v._filas_categoria["Banco"])
        v._nueva_entrada()
        self.assertEqual(self.B.CATEGORIAS[v.e_categoria.get_selected()], "Banco")

    def test_guardar_en_otra_categoria_quita_el_filtro_para_que_se_vea(self):
        v = self.v
        v.lista_categorias.select_row(v._filas_categoria["Banco"])
        v._nueva_entrada()
        self.rellenar("Web nueva", categoria="Web")
        v._guardar_editor()
        self.assertEqual(v._categoria, "Todas")
        self.assertIn("Web nueva", self.filas(v))

    def test_enter_en_un_campo_guarda(self):
        v = self.v
        v._nueva_entrada()
        self.rellenar("Con Enter")
        v.e_servicio.emit("activate")
        self.assertIn("Con Enter", self.filas(v))

    def test_el_ojo_alterna_la_visibilidad(self):
        v = self.v
        v._nueva_entrada()
        self.assertFalse(v.e_clave.get_visibility())
        v._alternar_visibilidad_editor()
        self.assertTrue(v.e_clave.get_visibility())
        v._alternar_visibilidad_editor()
        self.assertFalse(v.e_clave.get_visibility())

    def test_el_medidor_del_editor(self):
        v = self.v
        v._nueva_entrada()
        v.e_clave.set_text("123456")
        self.assertEqual(v.e_medidor.puntos, 0)
        v.e_clave.set_text(self.B.generar_contrasena(24))
        self.assertEqual(v.e_medidor.puntos, 4)


class TestGeneradorEnElEditor(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos(n=1)
        self.v = self.abierta()
        self.v._nueva_entrada()

    def test_generar_rellena_el_campo_y_la_muestra(self):
        v = self.v
        v.g_longitud.set_value(30)
        v._generar()
        clave = v.e_clave.get_text()
        self.assertEqual(len(clave), 30)
        self.assertTrue(v.e_clave.get_visibility(), "se ve para poder anotarla antes de guardar")
        self.assertEqual(v.e_medidor.puntos, 4)

    def test_boton_generar(self):
        v = self.v
        v.e_generar.emit("clicked")
        self.assertEqual(len(v.e_clave.get_text()), 20)

    def test_opciones(self):
        v = self.v
        v.g_longitud.set_value(8)
        for _ in range(100):
            v._generar()
            c = v.e_clave.get_text()
            self.assertTrue(any(x in self.B.MINUSCULAS for x in c) and any(x in self.B.MAYUSCULAS for x in c)
                            and any(x in self.B.DIGITOS for x in c) and any(x in self.B.SIMBOLOS for x in c), c)
        v.g_simbolos.set_active(False)
        v.g_mayusculas.set_active(False)
        v.g_ambiguos.set_active(True)
        for _ in range(100):
            v._generar()
            c = v.e_clave.get_text()
            self.assertTrue(set(c) <= set(self.B.MINUSCULAS + self.B.DIGITOS) - set("0O1lI"), c)

    def test_sin_ningun_tipo_de_caracter_avisa(self):
        v = self.v
        for c in (v.g_mayusculas, v.g_minusculas, v.g_digitos, v.g_simbolos):
            c.set_active(False)
        v._generar()
        self.assertTrue(v.e_error.get_visible())
        self.assertIn("al menos un tipo", v.e_error.get_text())
        self.assertTrue(v.expansor.get_expanded())
        self.assertEqual(v.e_clave.get_text(), "")

    def test_el_rango_de_longitud(self):
        v = self.v
        adj = v.g_longitud.get_adjustment()
        self.assertEqual((adj.get_lower(), adj.get_upper()), (8.0, 64.0))


# ====================================================================================== borrar
class TestBorrar(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos(n=2)
        self.v = self.abierta()
        self.entrada = self.elegir(self.v, "Servicio 1")

    def test_borrar_pide_confirmacion(self):
        v = self.v
        v.b_borrar.emit("clicked")
        self.assertTrue(v.caja_borrar.get_visible())
        self.assertFalse(v.fila_acciones.get_visible())
        self.assertIn("¿Borrar «Servicio 1»? No se puede deshacer.", v.texto_borrar.get_text())
        self.assertEqual(len(v.boveda.entradas), 2, "no se borra solo con pulsar Borrar")

    def test_cancelar_la_confirmacion(self):
        v = self.v
        v.b_borrar.emit("clicked")
        v.b_borrar_no.emit("clicked")
        self.assertFalse(v.caja_borrar.get_visible())
        self.assertTrue(v.fila_acciones.get_visible())
        self.assertEqual(len(v.boveda.entradas), 2)

    def test_confirmar_borra_y_persiste(self):
        v = self.v
        v.b_borrar.emit("clicked")
        v.b_borrar_si.emit("clicked")
        self.assertEqual([e.servicio for e in v.boveda.entradas], ["Servicio 0"])
        self.assertEqual(self.filas(v), ["Servicio 0"])
        self.assertEqual(v.texto_barra.get_text(), "Entrada borrada.")
        v.bloquear()
        self.desbloquear(v)
        self.assertEqual(self.filas(v), ["Servicio 0"])

    def test_cambiar_de_fila_cancela_la_confirmacion(self):
        v = self.v
        v.b_borrar.emit("clicked")
        self.elegir(v, "Servicio 0")
        self.assertFalse(v.caja_borrar.get_visible())
        self.assertIsNone(v._borrando)

    def test_esc_cancela_la_confirmacion(self):
        v = self.v
        v.b_borrar.emit("clicked")
        self.assertTrue(v._tecla(None, self.Gdk.KEY_Escape, 0, 0))
        self.assertFalse(v.caja_borrar.get_visible())
        self.assertEqual(len(v.boveda.entradas), 2)

    def test_borrar_la_ultima_deja_el_estado_vacio(self):
        v = self.v
        for nombre in ("Servicio 1", "Servicio 0"):
            self.elegir(v, nombre)
            v.b_borrar.emit("clicked")
            v.b_borrar_si.emit("clicked")
        self.assertEqual(self.filas(v), [])
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_texto.get_text(), "Aún no hay contraseñas. Añade la de tu correo o la de tu router.")
        self.assertTrue(v.vacio_boton.get_visible())

    def test_fallo_de_disco_al_borrar(self):
        v = self.v
        v.b_borrar.emit("clicked")
        with mock.patch("os.replace", side_effect=OSError(5, "Input/output error")):
            v.b_borrar_si.emit("clicked")
        self.assertEqual(len(v.boveda.entradas), 2)
        self.assertIn("No se pudo borrar", v.texto_barra.get_text())
        self.assertTrue(v.barra.has_css_class("error"))


class TestEstadoVacio(BaseVentana):
    def test_boveda_nueva_sin_entradas(self):
        b = self.nueva_boveda()
        b.crear(MAESTRA)
        b.bloquear()
        v = self.abierta()
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "vacio")
        self.assertEqual(v.vacio_titulo.get_text(), "Aún no hay contraseñas")
        self.assertEqual(v.vacio_texto.get_text(), "Aún no hay contraseñas. Añade la de tu correo o la de tu router.")
        self.assertTrue(v.vacio_boton.get_visible())
        self.assertEqual(self.filas(v), [])
        v.vacio_boton.emit("clicked")
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")

    def test_con_entradas_pero_ninguna_elegida(self):
        self.con_datos(n=2)
        v = self.abierta()
        v.busqueda.set_text("zzz")
        v._refrescar()
        self.assertFalse(v.vacio_boton.get_visible())
        self.assertEqual(v.vacio_titulo.get_text(), "Elige una contraseña")


# =================================================================================== portapapeles
class TestPortapapeles(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos(n=2)

    def test_copiar_contrasena_y_usuario(self):
        v = self.abierta()
        self.elegir(v, "Servicio 1")
        v.b_copiar_clave.emit("clicked")
        self.assertEqual(self.cb.contenido, "clave-falsa-1")
        v.b_copiar_usuario.emit("clicked")
        self.assertEqual(self.cb.contenido, "usuario1@ejemplo.test")

    def test_aviso_visible(self):
        v = self.abierta()
        v.b_copiar_clave.emit("clicked")
        self.assertTrue(v.barra.get_visible())
        self.assertEqual(v.texto_barra.get_text(), "Copiado; se borrará en 25 s")
        self.assertNotIn("clave-falsa", v.texto_barra.get_text())

    def test_se_borra_a_los_25_s(self):
        v = self.abierta()
        v.b_copiar_clave.emit("clicked")
        self.assertEqual(v._copiado.segundos, 25)
        self.assertEqual(v._copiado.segundos, self.X.PORTAPAPELES_S)
        with mock.patch.object(self.X.GLib, "timeout_add", wraps=self.X.GLib.timeout_add) as t:
            v.b_copiar_clave.emit("clicked")
        self.assertEqual(t.call_args[0][0], 25000, "el temporizador es de 25 s")

    def test_el_borrado_llega_y_solo_si_sigue_siendo_lo_copiado(self):
        v = self.abierta(segundos_portapapeles=0.05)
        self.elegir(v, "Servicio 1")
        v.b_copiar_clave.emit("clicked")
        self.assertEqual(self.cb.contenido, "clave-falsa-1")
        self.pump(0.3)
        self.assertIsNone(self.cb.contenido, "pasado el plazo se borra")
        self.assertEqual(v.texto_barra.get_text(), "Portapapeles borrado.")

    def test_no_se_borra_lo_que_copiaste_despues(self):
        v = self.abierta(segundos_portapapeles=0.05)
        v.b_copiar_clave.emit("clicked")
        self.cb.alguien_copia("otra cosa que copié luego")
        self.pump(0.3)
        self.assertEqual(self.cb.contenido, "otra cosa que copié luego")
        self.assertEqual(self.cb.borrados, 0)
        self.assertFalse(v.barra.get_visible(), "el aviso de «copiado» desaparece")

    def test_copiar_otra_cosa_antes_del_plazo_reinicia_la_cuenta(self):
        v = self.abierta(segundos_portapapeles=0.2)
        v.b_copiar_usuario.emit("clicked")
        self.pump(0.12)
        v.b_copiar_clave.emit("clicked")
        self.pump(0.12)  # ya pasó el plazo del primero, pero no el del segundo
        self.assertEqual(self.cb.contenido, "clave-falsa-0")
        self.pump(0.3)
        self.assertIsNone(self.cb.contenido)

    def test_bloquear_limpia_lo_copiado(self):
        v = self.abierta()
        v.b_copiar_clave.emit("clicked")
        v.bloquear()
        self.assertIsNone(self.cb.contenido)

    def test_bloquear_no_toca_lo_ajeno(self):
        v = self.abierta()
        v.b_copiar_clave.emit("clicked")
        self.cb.alguien_copia("algo ajeno")
        v.bloquear()
        self.assertEqual(self.cb.contenido, "algo ajeno")

    def test_cerrar_la_ventana_limpia_lo_copiado(self):
        v = self.abierta()
        v.b_copiar_clave.emit("clicked")
        v._al_cerrar()
        self.assertIsNone(self.cb.contenido)

    def test_entrada_sin_usuario_no_copia_nada(self):
        b = self.nueva_boveda()
        b.abrir(MAESTRA)
        b.agregar("Sin usuario", contrasena="clave-falsa")
        b.bloquear()
        v = self.abierta()
        self.elegir(v, "Sin usuario")
        self.assertFalse(v.b_copiar_usuario.get_sensitive())
        v._copiar("usuario")
        self.assertIsNone(self.cb.contenido)

    def test_el_portapapeles_real_de_gtk_marca_lo_copiado_como_secreto(self):
        real = self.X.PortapapelesGdk(self.Gdk.Display.get_default())
        real.poner("clave-falsa-gtk")
        resultado = []
        real.leer(resultado.append)
        self.assertTrue(self.pump_hasta(lambda: bool(resultado)))
        self.assertEqual(resultado, ["clave-falsa-gtk"])
        formatos = self.Gdk.Display.get_default().get_clipboard().get_formats().to_string()
        self.assertIn("x-kde-passwordManagerHint", formatos)
        real.borrar_si_es_nuestro()
        resultado.clear()
        real.leer(resultado.append)
        self.assertTrue(self.pump_hasta(lambda: bool(resultado)))
        self.assertEqual(resultado, [None])

    def test_el_portapapeles_real_no_borra_lo_que_puso_otro(self):
        real = self.X.PortapapelesGdk(self.Gdk.Display.get_default())
        real.poner("clave-falsa-gtk")
        from gi.repository import GObject
        self.Gdk.Display.get_default().get_clipboard().set_content(
            self.Gdk.ContentProvider.new_for_value(GObject.Value(str, "ajeno")))
        real.borrar_si_es_nuestro()
        resultado = []
        real.leer(resultado.append)
        self.assertTrue(self.pump_hasta(lambda: bool(resultado)))
        self.assertEqual(resultado, ["ajeno"])


# ==================================================================================== bloqueo
class TestBloqueo(BaseVentana):
    def test_bloqueo_manual_con_el_boton(self):
        self.con_datos(n=3)
        v = self.abierta()
        v.boton_bloquear.emit("clicked")
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertEqual(v.pila_acceso.get_visible_child_name(), "entrar")
        self.assertFalse(v.boveda.abierta)
        self.assertFalse(v.boton_bloquear.get_visible())
        self.assertFalse(v.menu.get_visible())

    def test_bloquear_suelta_todo_lo_de_la_memoria(self):
        self.con_datos(n=3)
        v = self.abierta()
        clave = v.boveda._clave
        v.bloquear()
        self.assertIsNone(v.boveda._clave)
        self.assertEqual(bytes(clave), bytes(32))
        self.assertEqual(v.boveda._entradas, ())
        self.assertEqual(v._filas, {})
        self.assertIsNone(v._seleccion)
        with self.assertRaises(self.B.BovedaError):
            v.boveda.entradas

    def test_bloquear_llama_a_gc_collect(self):
        self.con_datos()
        v = self.abierta()
        with mock.patch.object(self.X.gc, "collect") as colecta:
            v.bloquear()
        self.assertTrue(colecta.called)

    def test_ningun_secreto_queda_en_ningun_widget(self):
        self.con_datos(n=2, marca=True)
        v = self.abierta()
        self.elegir(v, MARCA + "-servicio")
        v.b_ver_clave.emit("clicked")  # la contraseña a la vista
        v._editar_seleccionada()       # y el editor con todo cargado
        v.busqueda.set_text(MARCA[:10])
        antes = self.en_pantalla(v)
        for parte in ("-servicio", "-usuario", "-clave", "-url", "-notas"):
            self.assertIn(MARCA + parte, antes, f"(control: {parte} debía verse antes de bloquear)")
        v.bloquear()
        despues = self.en_pantalla(v)
        self.assertNotIn(MARCA, despues)
        self.assertNotIn("clave-falsa", despues)
        self.assertNotIn("usuario0@ejemplo.test", despues)
        self.assertNotIn(MAESTRA, despues)

    def test_ningun_secreto_tras_cerrar_la_ventana(self):
        self.con_datos(n=1, marca=True)
        v = self.abierta()
        self.elegir(v, MARCA + "-servicio")
        v.b_ver_clave.emit("clicked")
        v._al_cerrar()
        self.assertNotIn(MARCA, self.en_pantalla(v))
        self.assertFalse(v.boveda.abierta)

    def test_al_bloquear_se_vacian_busqueda_editor_y_formularios(self):
        self.con_datos(n=2)
        v = self.abierta()
        v._nueva_entrada()
        v.e_clave.set_text("clave-falsa-en-el-editor")
        v.busqueda.set_text("algo")
        v._abrir_cambiar_maestra()
        v.form_cambiar.actual.set_text(MAESTRA)
        v.bloquear()
        self.assertEqual(v.e_clave.get_text(), "")
        self.assertEqual(v.busqueda.get_text(), "")
        self.assertEqual(v.form_cambiar.actual.get_text(), "")
        self.assertNotIn(MAESTRA, self.en_pantalla(v))

    def test_tras_bloquear_se_puede_volver_a_entrar(self):
        self.con_datos(n=2)
        v = self.abierta()
        v.bloquear()
        self.desbloquear(v)
        self.assertEqual(self.pagina(v), "abierta")
        self.assertEqual(len(self.filas(v)), 2)
        self.assertEqual(v._categoria, "Todas")

    def test_los_temporizadores_se_cancelan_al_bloquear(self):
        self.con_datos()
        v = self.abierta()
        v.b_ver_clave.emit("clicked")
        v._avisar("algo", segundos=5)
        v.bloquear()
        self.assertEqual({k: x for k, x in v._ids_tic.items() if x}, {})

    # ----- inactividad
    def test_inactividad_3_minutos_por_defecto(self):
        self.assertEqual(self.X.INACTIVIDAD_S, 180)
        self.con_datos()
        v = self.abierta()
        self.assertEqual(v._inactividad_s, 180)

    def test_se_bloquea_por_inactividad(self):
        self.con_datos()
        v = self.abierta()
        self.reloj.avanzar(179)
        self.assertTrue(v._revisar_inactividad(), "a los 179 s sigue abierta")
        self.assertTrue(v.boveda.abierta)
        self.reloj.avanzar(1.5)
        self.assertFalse(v._revisar_inactividad())
        self.assertFalse(v.boveda.abierta)
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertIn("inactividad", v.aviso_entrar.get_text())

    def controlador(self, v, nombre):
        lista = v.observe_controllers()
        for i in range(lista.get_n_items()):
            c = lista.get_item(i)
            if type(c).__name__ == nombre:
                return c
        self.fail(f"la ventana no tiene un {nombre}")

    def test_cualquier_actividad_reinicia_el_temporizador(self):
        """Tecla, clic, movimiento y rueda: se emiten las señales de los controladores que la ventana montó."""
        self.con_datos()
        v = self.abierta()
        Gdk = self.Gdk
        acciones = {
            "tecla": lambda: self.controlador(v, "EventControllerKey").emit("key-pressed", Gdk.KEY_a, 38, 0),
            "clic": lambda: self.controlador(v, "GestureClick").emit("pressed", 1, 10.0, 10.0),
            "movimiento": lambda: self.controlador(v, "EventControllerMotion").emit("motion", 20.0, 20.0),
            "rueda": lambda: self.controlador(v, "EventControllerScroll").emit("scroll", 0.0, 1.0),
        }
        for nombre, accion in acciones.items():
            with self.subTest(nombre):
                self.reloj.avanzar(170)
                accion()
                self.reloj.avanzar(170)
                self.assertTrue(v._revisar_inactividad(), "reiniciado: han pasado 170 s, no 340")
                self.assertTrue(v.boveda.abierta)
        self.reloj.avanzar(181)  # y sin actividad, sí se bloquea
        self.assertFalse(v._revisar_inactividad())

    def test_los_controladores_de_actividad_escuchan_en_la_fase_de_captura(self):
        """Así ven los eventos aunque un widget de dentro (un campo, un botón) los consuma."""
        self.con_datos()
        v = self.ventana()
        for nombre in ("EventControllerKey", "GestureClick", "EventControllerMotion", "EventControllerScroll"):
            self.assertEqual(self.controlador(v, nombre).get_propagation_phase(), self.Gtk.PropagationPhase.CAPTURE, nombre)

    def test_inactividad_con_el_temporizador_real(self):
        self.con_datos()
        v = self.abierta(inactividad_s=0.3, reloj=time.monotonic)
        self.assertTrue(v._ids_tic["inactividad"], "el temporizador está en marcha mientras está abierta")
        self.assertTrue(self.pump_hasta(lambda: not v.boveda.abierta, 3))
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertFalse(v._ids_tic["inactividad"], "y se para al bloquear")

    def test_la_actividad_real_mantiene_abierta(self):
        self.con_datos()
        v = self.abierta(inactividad_s=0.5, reloj=time.monotonic)
        fin = time.monotonic() + 1.2
        while time.monotonic() < fin:
            v._actividad()
            self.pump(0.05)
        self.assertTrue(v.boveda.abierta)
        self.assertTrue(self.pump_hasta(lambda: not v.boveda.abierta, 3))

    # ----- atajos
    def test_ctrl_l_bloquea(self):
        self.con_datos()
        v = self.abierta()
        self.assertTrue(v._tecla(None, self.Gdk.KEY_l, 0, self.Gdk.ModifierType.CONTROL_MASK))
        self.assertFalse(v.boveda.abierta)

    def test_ctrl_n_abre_el_editor(self):
        self.con_datos()
        v = self.abierta()
        self.assertTrue(v._tecla(None, self.Gdk.KEY_n, 0, self.Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(v.pila_detalle.get_visible_child_name(), "editor")

    def test_ctrl_f_busca(self):
        self.con_datos()
        v = self.abierta()
        v.lista.grab_focus()
        self.assertTrue(v._tecla(None, self.Gdk.KEY_f, 0, self.Gdk.ModifierType.CONTROL_MASK))
        foco = v.get_focus()
        self.assertTrue(foco is v.busqueda or (foco is not None and foco.is_ancestor(v.busqueda)))

    def test_esc_limpia_la_busqueda(self):
        self.con_datos(n=3)
        v = self.abierta()
        v.busqueda.set_text("servicio 2")
        v._refrescar()
        self.assertEqual(self.filas(v), ["Servicio 2"])
        self.assertTrue(v._tecla(None, self.Gdk.KEY_Escape, 0, 0))
        self.assertEqual(v.busqueda.get_text(), "")
        self.assertEqual(len(self.filas(v)), 3)

    def test_esc_cierra_el_editor(self):
        self.con_datos()
        v = self.abierta()
        v._nueva_entrada()
        v.e_clave.set_text("clave-falsa")
        v._tecla(None, self.Gdk.KEY_Escape, 0, 0)
        self.assertNotEqual(v.pila_detalle.get_visible_child_name(), "editor")
        self.assertEqual(v.e_clave.get_text(), "")

    def test_los_atajos_no_hacen_nada_con_la_boveda_bloqueada(self):
        self.con_datos()
        v = self.ventana()
        for tecla in (self.Gdk.KEY_n, self.Gdk.KEY_f, self.Gdk.KEY_l):
            self.assertFalse(v._tecla(None, tecla, 0, self.Gdk.ModifierType.CONTROL_MASK))
        self.assertEqual(self.pagina(v), "bloqueada")

    # ----- suspensión y cierre
    def test_al_suspender_se_bloquea(self):
        self.con_datos()
        v = self.abierta()
        v._senal_suspension(None, ":1.1", "/org/freedesktop/login1", "org.freedesktop.login1.Manager",
                            "PrepareForSleep", self.GLib.Variant("(b)", (True,)))
        self.assertFalse(v.boveda.abierta)
        self.assertIn("suspender", v.aviso_entrar.get_text())

    def test_al_despertar_no_se_hace_nada(self):
        self.con_datos()
        v = self.abierta()
        v._senal_suspension(None, ":1.1", "/org", "org.freedesktop.login1.Manager", "PrepareForSleep",
                            self.GLib.Variant("(b)", (False,)))
        self.assertTrue(v.boveda.abierta)

    def test_una_senal_rara_ante_la_duda_bloquea(self):
        self.con_datos()
        v = self.abierta()
        v._senal_suspension(None, ":1.1", "/org", "org.freedesktop.login1.Manager", "PrepareForSleep", None)
        self.assertFalse(v.boveda.abierta)

    def test_sin_bus_del_sistema_no_pasa_nada(self):
        self.con_datos()
        with mock.patch.object(self.X.Gio, "bus_get", side_effect=self.GLib.Error("sin bus")):
            v = self.ventana(vigilar_suspension=True)
        self.assertEqual(self.pagina(v), "bloqueada")
        self.assertIsNone(v._bus)

    def test_bus_que_falla_al_conectar_se_ignora(self):
        self.con_datos()
        v = self.ventana()
        with mock.patch.object(self.X.Gio, "bus_get_finish", side_effect=self.GLib.Error("sin bus")):
            v._bus_listo(None, object())
        self.assertIsNone(v._bus)

    def test_se_suscribe_a_prepareforsleep_de_logind(self):
        self.con_datos()
        v = self.ventana()
        conexion = mock.Mock()
        conexion.signal_subscribe.return_value = 42
        with mock.patch.object(self.X.Gio, "bus_get_finish", return_value=conexion):
            v._bus_listo(None, object())
        args = conexion.signal_subscribe.call_args[0]
        self.assertEqual(args[:4], ("org.freedesktop.login1", "org.freedesktop.login1.Manager", "PrepareForSleep",
                                    "/org/freedesktop/login1"))
        v._al_cerrar()
        conexion.signal_unsubscribe.assert_called_once_with(42)

    def test_cerrar_la_ventana_suelta_todo_y_es_idempotente(self):
        self.con_datos(n=2)
        v = self.abierta()
        v._al_cerrar()
        self.assertFalse(v.boveda.abierta)
        self.assertTrue(v._cerrada)
        v._al_cerrar()  # una segunda vez (do_shutdown de la aplicación) no rompe nada

    def test_una_instancia_unica(self):
        with mock.patch.dict(os.environ, {"XDG_DATA_HOME": self.tmp}):
            app = self.X.AppBoveda()
            app.register(None)
            app.activate()
            primera = app.ventana
            app.activate()
            self.assertIs(app.ventana, primera, "activar otra vez trae la misma ventana al frente")
            self.assertEqual(self.pagina(primera), "bloqueada")
            primera._al_cerrar()
            primera.destroy()


# ====================================================================== cambiar contraseña maestra y exportar
class TestMaestraYExportar(BaseVentana):
    def setUp(self):
        super().setUp()
        self.con_datos(n=2)
        self.v = self.abierta()

    def cambiar(self, actual, nueva, repite):
        f = self.v.form_cambiar
        f.actual.set_text(actual)
        f.nueva.set_text(nueva)
        f.repite.set_text(repite)
        self.v._cambiar_maestra()

    def test_menu_con_sus_tres_opciones(self):
        modelo = self.v.menu.get_menu_model()
        etiquetas = [modelo.get_item_attribute_value(i, "label", None).get_string() for i in range(modelo.get_n_items())]
        self.assertEqual(etiquetas, ["Cambiar contraseña maestra…", "Exportar copia cifrada…", "Acerca de Contraseñas"])
        for nombre in ("cambiar-maestra", "exportar", "acerca"):
            self.assertIsNotNone(self.v.lookup_action(nombre), nombre)

    def test_abrir_la_pagina(self):
        self.v.lookup_action("cambiar-maestra").activate(None)
        self.assertEqual(self.pagina(self.v), "maestra")
        self.assertIn("Cambiar contraseña maestra", self.en_pantalla(self.v.pila.get_child_by_name("maestra")))
        self.assertIn("no se pueden recuperar", self.en_pantalla(self.v.pila.get_child_by_name("maestra")))

    def test_cambio_correcto(self):
        self.v._abrir_cambiar_maestra()
        self.cambiar(MAESTRA, OTRA, OTRA)
        self.assertEqual(self.pagina(self.v), "abierta")
        self.assertIn("Contraseña maestra cambiada", self.v.texto_barra.get_text())
        self.assertEqual(self.v.form_cambiar.actual.get_text(), "")
        self.v.bloquear()
        self.desbloquear(self.v, MAESTRA)
        self.assertEqual(self.pagina(self.v), "bloqueada", "la vieja ya no vale")
        self.reloj.avanzar(100)
        self.v._actualizar_espera()
        self.desbloquear(self.v, OTRA)
        self.assertEqual(self.pagina(self.v), "abierta")
        self.assertEqual(len(self.filas(self.v)), 2)

    def test_actual_incorrecta(self):
        self.v._abrir_cambiar_maestra()
        self.cambiar("no-es-la-actual", OTRA, OTRA)
        self.assertEqual(self.pagina(self.v), "maestra")
        self.assertEqual(self.v.form_cambiar.error.get_text(), "La contraseña actual no es correcta.")
        self.v.bloquear()
        self.desbloquear(self.v, MAESTRA)
        self.assertEqual(self.pagina(self.v), "abierta", "no cambió nada")

    def test_validaciones(self):
        self.v._abrir_cambiar_maestra()
        casos = ((MAESTRA, OTRA, OTRA + "x", "no coinciden"), (MAESTRA, "corta", "corta", "al menos 8"),
                 (MAESTRA, "         ", "         ", "vacía"), ("", OTRA, OTRA, "contraseña actual"),
                 (MAESTRA, MAESTRA, MAESTRA, "distinta de la actual"))
        for actual, nueva, repite, texto in casos:
            with self.subTest(texto=texto):
                self.cambiar(actual, nueva, repite)
                self.assertIn(texto, self.v.form_cambiar.error.get_text())
                self.assertEqual(self.pagina(self.v), "maestra")

    def test_cancelar_y_esc_vuelven(self):
        self.v._abrir_cambiar_maestra()
        self.v.form_cambiar.actual.set_text(MAESTRA)
        self.v.form_cambiar_cancelar.emit("clicked")
        self.assertEqual(self.pagina(self.v), "abierta")
        self.assertEqual(self.v.form_cambiar.actual.get_text(), "")
        self.v._abrir_cambiar_maestra()
        self.assertTrue(self.v._tecla(None, self.Gdk.KEY_Escape, 0, 0))
        self.assertEqual(self.pagina(self.v), "abierta")

    def test_mientras_cambia_los_campos_no_se_tocan(self):
        v = self.v
        v._abrir_cambiar_maestra()
        f = v.form_cambiar
        v._ejecutar = lambda trabajo, fin: None  # el scrypt «no termina»: se ve el estado ocupado
        self.cambiar_en(v, MAESTRA, OTRA, OTRA)
        self.assertFalse(f.nueva.get_editable())
        self.assertFalse(f.boton.get_sensitive())
        self.assertTrue(f.actividad.get_visible())

    def cambiar_en(self, v, actual, nueva, repite):
        f = v.form_cambiar
        f.actual.set_text(actual)
        f.nueva.set_text(nueva)
        f.repite.set_text(repite)
        v._cambiar_maestra()

    def test_exportar_copia_cifrada(self):
        destino = os.path.join(self.tmp, "copia.enc")
        self.v.exportar_a(destino)
        with open(destino, "rb") as f, open(self.ruta, "rb") as g:
            self.assertEqual(f.read(), g.read())
        self.assertEqual(oct(os.stat(destino).st_mode & 0o777), "0o600")
        self.assertIn("Copia cifrada guardada en", self.v.texto_barra.get_text())
        self.assertNotIn("clave-falsa", self.en_pantalla(self.v))

    def test_exportar_con_error(self):
        self.v.exportar_a(os.path.join(self.tmp, "no-existe", "copia.enc"))
        self.assertIn("No se pudo exportar", self.v.texto_barra.get_text())
        self.assertTrue(self.v.barra.has_css_class("error"))
        self.v.exportar_a(self.ruta)
        self.assertIn("No se pudo exportar", self.v.texto_barra.get_text())

    def test_acerca_de_dice_lo_esencial(self):
        with mock.patch.object(self.Gtk.AboutDialog, "present") as presentar:
            self.v.lookup_action("acerca").activate(None)
        presentar.assert_called_once()


# ================================================================================ medidor y estilo
class TestMedidorYEstilo(BaseVentana):
    def test_segmentos_del_medidor(self):
        m = self.X.MedidorFortaleza()
        m.actualizar("password")
        encendidos = [s for s in self.hijos(self.hijos(m)[0]) if s.has_css_class("on")]
        self.assertEqual(len(encendidos), 1)
        self.assertTrue(encendidos[0].has_css_class("f0"))
        m.actualizar(self.B.generar_contrasena(30))
        encendidos = [s for s in self.hijos(self.hijos(m)[0]) if s.has_css_class("on")]
        self.assertEqual(len(encendidos), 5)
        self.assertTrue(all(s.has_css_class("f4") for s in encendidos))
        m.actualizar("")
        self.assertEqual([s for s in self.hijos(self.hijos(m)[0]) if s.has_css_class("on")], [])

    def test_el_estilo_va_en_el_modulo_con_el_acento_violeta(self):
        self.assertIn("#b9a8ff", self.X.ESTILO)
        self.assertIn("window.boveda-ventana", self.X.ESTILO)

    def test_el_css_se_carga_sin_errores(self):
        avisos = []
        proveedor = self.Gtk.CssProvider()
        proveedor.connect("parsing-error", lambda _p, seccion, error: avisos.append(error.message))
        proveedor.load_from_string(self.X.ESTILO)
        self.assertEqual(avisos, [])

    def test_icono_de_la_aplicacion_existe(self):
        ruta = self.X.ruta_icono()
        self.assertIsNotNone(ruta)
        self.assertTrue(ruta.endswith("boveda.svg"))

    def test_la_imagen_del_icono_nunca_es_una_imagen_que_falta(self):
        img = self.X._imagen_icono(64)
        self.assertEqual(img.get_pixel_size(), 64)
        self.assertNotEqual(img.get_icon_name(), "image-missing")


if __name__ == "__main__":
    unittest.main()
