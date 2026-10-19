"""Fecha y hora: la lógica pura de ajustes.py (sin GTK, sin pantalla y sin ejecutar nada de verdad) y el reloj del dock.

Lo que más importa: el día de la semana coincide con la fecha, siempre en español y sin depender del idioma del
sistema. Los ejemplos que el usuario pidió expresamente: 19/10/2026 es lunes, 23/10/2026 viernes, 1/1/2027 viernes
y 29/2/2028 martes. La página de Ajustes con GTK está en test_ajustes_fecha_hora.py.
"""
import ast
import calendar
import datetime
import inspect
import os
import re
import sys
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))

from infinity_shell import ajustes as A  # noqa: E402

DOCK = os.path.join(HERE, "..", "infinity_shell", "dock.py")

# (año, mes, día) → nombre del día, tal como lo pidió el usuario
CASOS_DEL_USUARIO = (
    ((2026, 10, 19), "lunes"),
    ((2026, 10, 23), "viernes"),
    ((2027, 1, 1), "viernes"),
    ((2028, 2, 29), "martes"),
)


class TestTablasEnEspanol(unittest.TestCase):
    def test_los_nombres(self):
        self.assertEqual(A.DIAS, ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"))
        self.assertEqual(A.DIAS_CORTOS, ("lun", "mar", "mié", "jue", "vie", "sáb", "dom"))
        self.assertEqual(A.MESES, ("enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto",
                                   "septiembre", "octubre", "noviembre", "diciembre"))
        self.assertEqual(A.MESES_CORTOS, ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct",
                                          "nov", "dic"))

    def test_los_cortos_son_el_comienzo_de_los_largos(self):
        for largo, corto in zip(A.DIAS + A.MESES, A.DIAS_CORTOS + A.MESES_CORTOS):
            self.assertTrue(largo.startswith(corto[:2]), (largo, corto))
            self.assertEqual(len(corto), 3, corto)


class TestDiaDeLaSemana(unittest.TestCase):
    def test_los_ejemplos_del_usuario(self):
        for (a, m, d), esperado in CASOS_DEL_USUARIO:
            with self.subTest(fecha=f"{d}/{m}/{a}"):
                self.assertEqual(A.nombre_dia(a, m, d), esperado)
                # y el oráculo: la tabla en español indexada por date.weekday()
                self.assertEqual(A.DIAS[datetime.date(a, m, d).weekday()], esperado)

    def test_fecha_larga_de_los_ejemplos(self):
        self.assertEqual(A.fecha_larga(2026, 10, 19), "lunes 19 de octubre de 2026")
        self.assertEqual(A.fecha_larga(2026, 10, 23), "viernes 23 de octubre de 2026")
        self.assertEqual(A.fecha_larga(2027, 1, 1), "viernes 1 de enero de 2027")
        self.assertEqual(A.fecha_larga(2028, 2, 29), "martes 29 de febrero de 2028")

    def test_fecha_corta_de_la_pildora_del_dock(self):
        self.assertEqual(A.fecha_corta(2026, 10, 19), "lun 19 oct")
        self.assertEqual(A.fecha_corta(2026, 10, 23), "vie 23 oct")
        self.assertEqual(A.fecha_corta(2027, 1, 1), "vie 1 ene")
        self.assertEqual(A.fecha_corta(2028, 2, 29), "mar 29 feb")
        self.assertEqual(A.fecha_corta(2026, 10, 18), "dom 18 oct")   # el «Sun 18 Oct» que salía en inglés

    def test_los_siete_dias_y_los_doce_meses_salen_en_espanol(self):
        lunes = datetime.date(2026, 10, 19)
        for i in range(7):
            dia = lunes + datetime.timedelta(days=i)
            self.assertEqual(A.nombre_dia(dia.year, dia.month, dia.day), A.DIAS[i])
            self.assertTrue(A.fecha_corta(dia.year, dia.month, dia.day).startswith(A.DIAS_CORTOS[i] + " "))
        for mes in range(1, 13):
            self.assertIn(f" de {A.MESES[mes - 1]} de ", A.fecha_larga(2026, mes, 1))
            self.assertTrue(A.fecha_corta(2026, mes, 1).endswith(" " + A.MESES_CORTOS[mes - 1]))

    def test_de_1970_a_2099_cada_dia_coincide_con_su_fecha(self):
        """Todos los días del rango del reloj, contra dos oráculos: date.weekday() y un contador de días que parte de
        un lunes conocido (19/10/2026) y no usa datetime para el día de la semana."""
        dia = datetime.date(1970, 1, 1)
        fin = datetime.date(2099, 12, 31)
        lunes = datetime.date(2026, 10, 19)
        while dia <= fin:
            esperado = A.DIAS[dia.weekday()]
            self.assertEqual(A.nombre_dia(dia.year, dia.month, dia.day), esperado, dia)
            self.assertEqual(A.DIAS[(dia - lunes).days % 7], esperado, dia)
            dia += datetime.timedelta(days=1)

    def test_fechas_de_referencia_conocidas(self):
        self.assertEqual(A.nombre_dia(1970, 1, 1), "jueves")      # el origen de la época Unix
        self.assertEqual(A.nombre_dia(2000, 1, 1), "sábado")
        self.assertEqual(A.nombre_dia(2024, 2, 29), "jueves")
        self.assertEqual(A.nombre_dia(2099, 12, 31), "jueves")

    def test_una_fecha_que_no_existe_no_tiene_dia(self):
        malas = ((2027, 2, 29), (2026, 4, 31), (2026, 13, 1), (2026, 0, 1), (2026, 1, 0), (2026, 1, "1"), (0, 1, 1))
        for fecha in malas:
            with self.subTest(fecha=fecha):
                with self.assertRaises(ValueError):
                    A.nombre_dia(*fecha)

    def test_no_depende_del_idioma_del_sistema(self):
        """Ni strftime ni %a/%b: las funciones de nombre solo usan las tablas."""
        for f in (A.dia_de_la_semana, A.nombre_dia, A.fecha_larga, A.fecha_corta):
            fuente = inspect.getsource(f)
            self.assertNotIn("strftime", fuente, f.__name__)
            self.assertNotIn("calendar.day", fuente, f.__name__)
            self.assertIsNone(re.search(r"%[aAbB]", fuente), f.__name__)

    def test_con_otro_idioma_en_el_entorno_sale_lo_mismo(self):
        for idioma in ("C", "en_US.UTF-8", "fr_FR.UTF-8", "ja_JP.UTF-8"):
            with self.subTest(idioma=idioma), mock.patch.dict(os.environ, {"LANG": idioma, "LC_ALL": idioma,
                                                                              "LC_TIME": idioma}):
                self.assertEqual(A.fecha_corta(2026, 10, 19), "lun 19 oct")
                self.assertEqual(A.fecha_larga(2028, 2, 29), "martes 29 de febrero de 2028")


class TestBisiestosYDiasDelMes(unittest.TestCase):
    def test_bisiestos(self):
        for anio, esperado in ((2000, True), (1900, False), (2100, False), (2024, True), (2026, False), (2028, True),
                               (2027, False), (2096, True)):
            with self.subTest(anio=anio):
                self.assertIs(A.es_bisiesto(anio), esperado)

    def test_de_1970_a_2099_coincide_con_calendar(self):
        for anio in range(A.ANIO_MIN, A.ANIO_MAX + 1):
            self.assertIs(A.es_bisiesto(anio), calendar.isleap(anio), anio)
            for mes in range(1, 13):
                self.assertEqual(A.dias_del_mes(anio, mes), calendar.monthrange(anio, mes)[1], (anio, mes))


class TestValidacion(unittest.TestCase):
    def test_fechas_y_horas_validas(self):
        casos = ((2026, 10, 19, 14, 35, 7), (2028, 2, 29, 0, 0, 0), (2026, 12, 31, 23, 59, 59), (1970, 1, 1, 0, 0, 0),
                 (2099, 12, 31, 23, 59, 59), (2026, 4, 30, 12, 0, 0), (2026, 2, 28, 1, 2, 3))
        for caso in casos:
            with self.subTest(caso=caso):
                self.assertEqual(A.validar_fecha_hora(*caso), datetime.datetime(*caso))

    def error(self, *caso):
        with self.assertRaises(ValueError) as cm:
            A.validar_fecha_hora(*caso)
        return str(cm.exception)

    def test_29_de_febrero_solo_en_bisiestos(self):
        A.validar_fecha_hora(2028, 2, 29, 0, 0, 0)
        A.validar_fecha_hora(2096, 2, 29, 0, 0, 0)
        for anio in (2026, 2027, 2029, 2030):
            with self.subTest(anio=anio):
                msg = self.error(anio, 2, 29, 0, 0, 0)
                self.assertIn(f"El 29 de febrero de {anio} no existe", msg)
                self.assertIn("no es bisiesto", msg)
        self.assertIn("no es bisiesto", self.error(2099, 2, 29, 0, 0, 0))

    def test_dias_que_no_existen(self):
        self.assertIn("solo hay 30 días", self.error(2026, 4, 31, 0, 0, 0))
        self.assertIn("solo hay 30 días", self.error(2026, 11, 31, 0, 0, 0))
        self.assertIn("solo hay 28 días", self.error(2027, 2, 30, 0, 0, 0))
        self.assertIn("solo hay 29 días", self.error(2028, 2, 30, 0, 0, 0))
        self.assertIn("solo hay 31 días", self.error(2026, 10, 32, 0, 0, 0))
        self.assertIn("día 0", self.error(2026, 10, 0, 0, 0, 0))
        self.assertIn("día -1", self.error(2026, 10, -1, 0, 0, 0))

    def test_mes_y_anio_fuera_de_rango(self):
        self.assertIn("mes tiene que estar entre 1 y 12", self.error(2026, 0, 1, 0, 0, 0))
        self.assertIn("mes tiene que estar entre 1 y 12", self.error(2026, 13, 1, 0, 0, 0))
        self.assertIn("1970 y 2099", self.error(1969, 12, 31, 0, 0, 0))
        self.assertIn("1970 y 2099", self.error(2100, 1, 1, 0, 0, 0))

    def test_hora_minutos_y_segundos(self):
        self.assertIn("hora tiene que estar entre 0 y 23", self.error(2026, 10, 19, 24, 0, 0))
        self.assertIn("hora tiene que estar entre 0 y 23", self.error(2026, 10, 19, -1, 0, 0))
        self.assertIn("minutos tienen que estar entre 0 y 59", self.error(2026, 10, 19, 0, 60, 0))
        self.assertIn("minutos tienen que estar entre 0 y 59", self.error(2026, 10, 19, 0, -1, 0))
        self.assertIn("segundos tienen que estar entre 0 y 59", self.error(2026, 10, 19, 0, 0, 60))
        self.assertIn("segundos tienen que estar entre 0 y 59", self.error(2026, 10, 19, 0, 0, -1))

    def test_solo_enteros(self):
        for malo in ("5", 5.0, 5.5, None, True, [5], float("nan")):
            for posicion in range(6):
                caso = [2026, 10, 19, 12, 30, 15]
                caso[posicion] = malo
                with self.subTest(malo=malo, posicion=posicion):
                    self.assertIn("número entero", self.error(*caso))

    def test_los_mensajes_estan_en_espanol(self):
        for caso in ((2026, 4, 31, 0, 0, 0), (2027, 2, 29, 0, 0, 0), (2026, 10, 19, 24, 0, 0), (2100, 1, 1, 0, 0, 0)):
            msg = self.error(*caso)
            self.assertTrue(msg[0].isupper() and msg.endswith("."), msg)


class TestOrdenes(unittest.TestCase):
    def test_formato_de_set_time(self):
        self.assertEqual(A.formato_set_time(2026, 10, 19, 9, 5, 3), "2026-10-19 09:05:03")
        self.assertEqual(A.formato_set_time(2028, 2, 29, 0, 0, 0), "2028-02-29 00:00:00")
        self.assertEqual(A.formato_set_time(1970, 1, 1, 23, 59, 59), "1970-01-01 23:59:59")
        with self.assertRaises(ValueError):
            A.formato_set_time(2027, 2, 29, 0, 0, 0)

    def test_argv_de_la_hora_es_una_lista_sin_shell(self):
        argv = A.argv_fijar_fecha_hora(2026, 10, 23, 18, 0, 0)
        self.assertEqual(argv, ["timedatectl", "set-time", "2026-10-23 18:00:00"])
        self.assertIsInstance(argv, list)
        self.assertTrue(all(isinstance(x, str) for x in argv))

    def test_argv_de_la_zona(self):
        self.assertEqual(A.argv_fijar_zona("America/Santiago"), ["timedatectl", "set-timezone", "America/Santiago"])
        self.assertEqual(A.argv_fijar_zona("America/Argentina/Buenos_Aires")[2], "America/Argentina/Buenos_Aires")
        self.assertEqual(A.argv_fijar_zona("Etc/GMT+5")[2], "Etc/GMT+5")
        self.assertEqual(A.argv_fijar_zona("America/Port-au-Prince")[2], "America/Port-au-Prince")
        self.assertEqual(A.argv_fijar_zona("UTC")[2], "UTC")

    def test_zonas_con_texto_peligroso_se_rechazan(self):
        malas = ("-h", "--help", "--no-ask-password", "a b", "America/Santiago; rm -rf ~", "$(reboot)", "`id`",
                 "../../etc/passwd", "America/../x", "", " ", "\n", "America/Santiago\n", "America//Santiago",
                 "/America/Santiago", "America/Santiago/", "a" * 65, "América/Santiago", None, 5, ["UTC"])
        for zona in malas:
            with self.subTest(zona=zona), self.assertRaises(ValueError):
                A.argv_fijar_zona(zona)

    def test_solo_valen_las_zonas_de_la_lista(self):
        zonas = ["America/Santiago", "Europe/Madrid"]
        self.assertEqual(A.argv_fijar_zona("Europe/Madrid", zonas)[2], "Europe/Madrid")
        with self.assertRaises(ValueError) as cm:
            A.argv_fijar_zona("Europe/Narnia", zonas)
        self.assertIn("no existe", str(cm.exception))

    def test_argv_de_la_hora_automatica(self):
        self.assertEqual(A.argv_hora_automatica(True), ["timedatectl", "set-ntp", "true"])
        self.assertEqual(A.argv_hora_automatica(False), ["timedatectl", "set-ntp", "false"])
        for malo in ("true", 1, 0, None, "yes"):
            with self.subTest(malo=malo), self.assertRaises(ValueError):
                A.argv_hora_automatica(malo)


class Ejecutor:
    """Un `correr(argv, detach)` falso: apunta lo que se le pide y contesta según un guion de (ok, salida)."""

    def __init__(self, *respuestas):
        self.respuestas = list(respuestas)
        self.llamadas = []

    def __call__(self, argv, detach=False):
        self.llamadas.append((list(argv), detach))
        return self.respuestas.pop(0) if self.respuestas else (True, "")


class TestPermisos(unittest.TestCase):
    def test_con_sudo_sin_clave_basta_una_llamada(self):
        correr = Ejecutor((True, ""))
        self.assertEqual(A.ejecutar_con_permisos(["timedatectl", "set-ntp", "true"], correr), (True, ""))
        self.assertEqual(correr.llamadas, [(["sudo", "-n", "timedatectl", "set-ntp", "true"], False)])

    def test_si_sudo_pide_clave_se_prueba_por_polkit(self):
        correr = Ejecutor((False, "sudo: a password is required"), (True, ""))
        self.assertEqual(A.ejecutar_con_permisos(["timedatectl", "set-ntp", "true"], correr), (True, ""))
        self.assertEqual(correr.llamadas, [(["sudo", "-n", "timedatectl", "set-ntp", "true"], False),
                                           (["timedatectl", "set-ntp", "true"], False)])

    def test_sudo_no_instalado_o_sin_permiso_tambien_pasa_a_polkit(self):
        for salida in ("no está instalada", "no se pudo ejecutar (sin permiso)",
                       "sudo: a terminal is required to read the password; either use the -S option ...",
                       "sudo: no tty present and no askpass program specified",
                       "Sorry, user infinity is not allowed to execute '/usr/bin/timedatectl' as root on infinity.",
                       "infinity is not in the sudoers file."):
            with self.subTest(salida=salida):
                correr = Ejecutor((False, salida), (True, ""))
                self.assertEqual(A.ejecutar_con_permisos(["timedatectl", "show"], correr), (True, ""))
                self.assertEqual(len(correr.llamadas), 2)

    def test_si_timedatectl_se_niega_no_se_repite(self):
        """sudo sí dejó: el error es de timedatectl, y otro intento solo lo repetiría (o pediría otra contraseña)."""
        correr = Ejecutor((False, "Failed to set time: Automatic time synchronization is enabled"))
        ok, salida = A.ejecutar_con_permisos(["timedatectl", "set-time", "2026-10-19 12:00:00"], correr)
        self.assertFalse(ok)
        self.assertIn("Automatic time synchronization", salida)
        self.assertEqual(len(correr.llamadas), 1)

    def test_un_tiempo_agotado_no_se_reintenta(self):
        correr = Ejecutor((False, "no respondió a tiempo"))
        self.assertEqual(A.ejecutar_con_permisos(["timedatectl", "show"], correr), (False, "no respondió a tiempo"))
        self.assertEqual(len(correr.llamadas), 1)

    def test_si_fallan_las_dos_vias_el_error_es_el_de_polkit(self):
        correr = Ejecutor((False, "sudo: a password is required"),
                          (False, "Failed to set time: Interactive authentication required."))
        ok, salida = A.ejecutar_con_permisos(["timedatectl", "set-time", "2026-10-19 12:00:00"], correr)
        self.assertFalse(ok)
        self.assertIn("Interactive authentication required", salida)

    def test_nunca_se_usa_shell_ni_se_pide_contrasena(self):
        correr = Ejecutor((False, "sudo: a password is required"), (True, ""))
        A.fijar_fecha_hora(2026, 10, 19, 12, 0, 0, correr)
        for argv, _ in correr.llamadas:
            self.assertIsInstance(argv, list)
            self.assertTrue(all(isinstance(x, str) for x in argv))
            self.assertNotIn("sh", argv[:1] + argv[1:2])
            self.assertNotIn("-c", argv)
            self.assertNotIn("-S", argv)   # sudo -S leería la contraseña de la entrada estándar
        self.assertEqual(correr.llamadas[0][0][:2], ["sudo", "-n"])   # -n: sudo nunca pregunta
        # la fecha viaja como UN solo elemento del argv, sin interpolar nada más
        self.assertIn("2026-10-19 12:00:00", correr.llamadas[0][0])

    def test_el_ejecutor_real_espera_mas_que_las_recargas(self):
        with mock.patch.object(A, "correr_real", return_value=(True, "")) as real:
            self.assertEqual(A.ejecutar_con_permisos(["timedatectl", "show"]), (True, ""))
        real.assert_called_once_with(["sudo", "-n", "timedatectl", "show"], False, timeout=A.ESPERA_PERMISO_S)
        self.assertGreaterEqual(A.ESPERA_PERMISO_S, 60)   # da tiempo a escribir la contraseña en la ventana de polkit

    def test_correr_real_acepta_el_tiempo_de_espera(self):
        self.assertEqual(A.correr_real(["true"], timeout=5), (True, ""))
        self.assertFalse(A.correr_real(["false"])[0])
        ok, motivo = A.correr_real(["sleep", "5"], timeout=0.2)
        self.assertEqual((ok, motivo), (False, "no respondió a tiempo"))


class TestFijar(unittest.TestCase):
    def test_fijar_fecha_hora_ok(self):
        correr = Ejecutor((True, ""))
        self.assertEqual(A.fijar_fecha_hora(2026, 10, 23, 18, 0, 0, correr), (True, ""))
        self.assertEqual(correr.llamadas[0][0], ["sudo", "-n", "timedatectl", "set-time", "2026-10-23 18:00:00"])

    def test_una_fecha_invalida_no_ejecuta_nada(self):
        correr = Ejecutor()
        ok, msg = A.fijar_fecha_hora(2027, 2, 29, 12, 0, 0, correr)
        self.assertFalse(ok)
        self.assertIn("no es bisiesto", msg)
        ok, msg = A.fijar_fecha_hora(2026, 10, 19, 25, 0, 0, correr)
        self.assertFalse(ok)
        self.assertIn("entre 0 y 23", msg)
        self.assertEqual(correr.llamadas, [])

    def test_error_sin_permisos_dice_que_paso_y_como_hacerlo_a_mano(self):
        correr = Ejecutor((False, "sudo: a password is required"),
                          (False, "Failed to set time: Interactive authentication required."))
        ok, msg = A.fijar_fecha_hora(2026, 10, 19, 12, 0, 0, correr)
        self.assertFalse(ok)
        self.assertIn("No se pudo cambiar la fecha y la hora", msg)
        self.assertIn("no hay ventana de autorización", msg)
        self.assertIn("Hazlo a mano en una terminal: sudo timedatectl set-time '2026-10-19 12:00:00'", msg)

    def test_con_la_hora_automatica_activa_dice_que_la_apague(self):
        correr = Ejecutor((False, "Failed to set time: Automatic time synchronization is enabled"))
        ok, msg = A.fijar_fecha_hora(2026, 10, 19, 12, 0, 0, correr)
        self.assertFalse(ok)
        self.assertIn("la hora automática está activada", msg)
        self.assertIn("Desactívala primero", msg)
        self.assertNotIn("sudo timedatectl set-time", msg)   # a mano tampoco funcionaría

    def test_cancelar_la_autorizacion(self):
        correr = Ejecutor((False, "no está instalada"), (False, "Failed to set time: Access denied"))
        ok, msg = A.fijar_fecha_hora(2026, 10, 19, 12, 0, 0, correr)
        self.assertIn("no se concedió el permiso de administrador", msg)
        self.assertIn("sudo timedatectl set-time", msg)

    def test_sin_servicio_ntp(self):
        correr = Ejecutor((False, "Failed to set ntp: NTP not supported"))
        ok, msg = A.fijar_hora_automatica(True, correr)
        self.assertEqual(correr.llamadas[0][0], ["sudo", "-n", "timedatectl", "set-ntp", "true"])
        self.assertIn("No se pudo cambiar la hora automática", msg)
        self.assertIn("systemd-timesyncd", msg)
        self.assertIn("sudo apt install systemd-timesyncd", msg)

    def test_error_desconocido_se_cita_sin_traza(self):
        correr = Ejecutor((False, "Failed to set time: algo raro\nsegunda línea"))
        ok, msg = A.fijar_fecha_hora(2026, 10, 19, 12, 0, 0, correr)
        self.assertIn("el sistema respondió «Failed to set time: algo raro»", msg)
        self.assertNotIn("segunda línea", msg)
        self.assertIn("el sistema no dio ninguna explicación", A.fijar_hora_automatica(False, Ejecutor((False, "")))[1])

    def test_tiempo_agotado(self):
        ok, msg = A.fijar_zona_horaria("UTC", correr=Ejecutor((False, "no respondió a tiempo")))
        self.assertIn("se agotó el tiempo de espera", msg)

    def test_fijar_zona(self):
        zonas = ["America/Santiago", "Europe/Madrid"]
        correr = Ejecutor((True, ""))
        self.assertEqual(A.fijar_zona_horaria("Europe/Madrid", zonas, correr), (True, ""))
        self.assertEqual(correr.llamadas[0][0], ["sudo", "-n", "timedatectl", "set-timezone", "Europe/Madrid"])
        sin = Ejecutor()
        ok, msg = A.fijar_zona_horaria("Europe/Narnia", zonas, sin)
        self.assertFalse(ok)
        self.assertEqual(msg, "No se pudo cambiar la zona horaria: la zona «Europe/Narnia» no existe.")
        ok, msg = A.fijar_zona_horaria("--help", zonas, sin)
        self.assertFalse(ok)
        self.assertEqual(sin.llamadas, [])

    def test_zona_inexistente_segun_timedatectl(self):
        correr = Ejecutor((False, "Failed to set time zone: Invalid time zone 'Europe/Narnia'"))
        ok, msg = A.fijar_zona_horaria("Europe/Narnia", correr=correr)
        self.assertIn("esa zona horaria no existe", msg)
        self.assertNotIn("Hazlo a mano", msg)

    def test_fijar_hora_automatica_con_valor_raro(self):
        correr = Ejecutor()
        ok, msg = A.fijar_hora_automatica("true", correr=correr)
        self.assertFalse(ok)
        self.assertEqual(correr.llamadas, [])


SHOW = """Timezone=America/Santiago
LocalRTC=no
CanNTP=yes
NTP=yes
NTPSynchronized=no
TimeUSec=Mon 2026-10-19 12:00:00 -03
RTCTimeUSec=Mon 2026-10-19 15:00:00 UTC
"""


class TestEstadoDelReloj(unittest.TestCase):
    def test_parsear_show(self):
        d = A.parsear_timedatectl_show(SHOW)
        self.assertEqual((d["Timezone"], d["NTP"], d["CanNTP"], d["NTPSynchronized"]),
                         ("America/Santiago", "yes", "yes", "no"))
        self.assertEqual(d["TimeUSec"], "Mon 2026-10-19 12:00:00 -03")   # los «=» y espacios del valor se conservan
        self.assertEqual(A.parsear_timedatectl_show(""), {})
        self.assertEqual(A.parsear_timedatectl_show(None), {})
        self.assertEqual(A.parsear_timedatectl_show("basura\n=x\nA=b=c\n"), {"A": "b=c"})

    def test_leer_estado(self):
        e = A.leer_estado_hora(lambda argv: (0, SHOW))
        self.assertEqual(e, A.EstadoHora("America/Santiago", True, True, False))
        sin = A.leer_estado_hora(lambda argv: (0, SHOW.replace("NTP=yes", "NTP=no").replace("CanNTP=yes", "CanNTP=no")))
        self.assertEqual((sin.ntp, sin.puede_ntp), (False, False))

    def test_pide_show_sin_permisos(self):
        pedidos = []
        A.leer_estado_hora(lambda argv: pedidos.append(argv) or (0, SHOW))
        self.assertEqual(pedidos, [["timedatectl", "show"]])

    def test_si_timedatectl_falla_se_usa_el_respaldo_y_nada_mas_es_desconocido(self):
        with mock.patch.object(A, "zona_del_sistema", return_value="Europe/Madrid"):
            self.assertEqual(A.leer_estado_hora(lambda argv: (1, "")), A.EstadoHora("Europe/Madrid", None, None, None))

            def no_hay(argv):
                raise FileNotFoundError("timedatectl")
            self.assertEqual(A.leer_estado_hora(no_hay), A.EstadoHora("Europe/Madrid", None, None, None))
            import subprocess

            def tarda(argv):
                raise subprocess.TimeoutExpired(argv, 5)
            self.assertEqual(A.leer_estado_hora(tarda), A.EstadoHora("Europe/Madrid", None, None, None))

    def test_zona_del_sistema(self):
        self.assertEqual(A.zona_del_sistema(lambda ruta: "America/Santiago\n"), "America/Santiago")
        with mock.patch.object(A.os.path, "realpath", return_value="/usr/share/zoneinfo/Europe/Madrid"):
            self.assertEqual(A.zona_del_sistema(lambda ruta: ""), "Europe/Madrid")
            self.assertEqual(A.zona_del_sistema(lambda ruta: "no es una zona;\n"), "Europe/Madrid")
        with mock.patch.object(A.os.path, "realpath", return_value="/etc/localtime"):
            self.assertIsNone(A.zona_del_sistema(lambda ruta: ""))

    def test_resumen_de_la_hora_automatica(self):
        E = A.EstadoHora
        ayuda, puede = A.resumen_hora_automatica(E("UTC", True, True, True))
        self.assertTrue(puede)
        self.assertIn("sincronizada", ayuda)
        ayuda, puede = A.resumen_hora_automatica(E("UTC", True, True, False))
        self.assertTrue(puede)
        self.assertIn("todavía no se ha sincronizado", ayuda)
        ayuda, puede = A.resumen_hora_automatica(E("UTC", False, True, False))
        self.assertTrue(puede)
        self.assertIn("a mano", ayuda)
        ayuda, puede = A.resumen_hora_automatica(E("UTC", False, False, False))
        self.assertFalse(puede)   # sin systemd-timesyncd no hay nada que activar
        self.assertIn("sudo apt install systemd-timesyncd", ayuda)
        ayuda, puede = A.resumen_hora_automatica(E(None, None, None, None))
        self.assertTrue(puede)
        self.assertIn("No se pudo leer", ayuda)


class TestZonas(unittest.TestCase):
    ZONAS = ["Africa/Abidjan", "America/Argentina/Buenos_Aires", "America/Santiago", "America/Sao_Paulo",
             "Asia/Tokyo", "Europe/Madrid", "Etc/GMT+5", "UTC"]

    def test_filtrar(self):
        f = A.filtrar_zonas
        self.assertEqual(f(self.ZONAS, "santiago"), ["America/Santiago"])
        self.assertEqual(f(self.ZONAS, "SANTIAGO"), ["America/Santiago"])
        self.assertEqual(f(self.ZONAS, "buenos aires"), ["America/Argentina/Buenos_Aires"])
        self.assertEqual(f(self.ZONAS, "buenos_aires"), ["America/Argentina/Buenos_Aires"])
        self.assertEqual(f(self.ZONAS, "america sant"), ["America/Santiago"])
        self.assertEqual(f(self.ZONAS, "america/sant"), ["America/Santiago"])
        self.assertEqual(f(self.ZONAS, "são paulo"), ["America/Sao_Paulo"])   # sin tildes
        self.assertEqual(f(self.ZONAS, "tokyo"), ["Asia/Tokyo"])
        self.assertEqual(f(self.ZONAS, "gmt+5"), ["Etc/GMT+5"])
        self.assertEqual(f(self.ZONAS, "america"), ["America/Argentina/Buenos_Aires", "America/Santiago",
                                                    "America/Sao_Paulo"])
        self.assertEqual(f(self.ZONAS, "nada de esto"), [])

    def test_sin_texto_salen_todas(self):
        for texto in ("", "   ", "/", " / "):
            self.assertEqual(A.filtrar_zonas(self.ZONAS, texto), self.ZONAS)
        self.assertIsNot(A.filtrar_zonas(self.ZONAS, ""), self.ZONAS)   # una copia

    def test_listar_zonas_desde_timedatectl(self):
        salida = "Europe/Madrid\nAmerica/Santiago\nUTC\n\nlinea rara con espacios\n--opcion\nAmerica/Santiago\n"
        pedidos = []

        def correr(argv):
            pedidos.append(argv)
            return 0, salida
        self.assertEqual(A.listar_zonas(correr), ["America/Santiago", "Europe/Madrid", "UTC"])
        self.assertEqual(pedidos, [["timedatectl", "list-timezones"]])

    def test_respaldo_con_zone_tab(self):
        tabla = ("# comentario\nCL\t-3327-07040\tAmerica/Santiago\tMost of Chile\n"
                 "ES\t+4024-00341\tEurope/Madrid\tSpain (mainland)\nmal\n")
        for correr in (lambda argv: (1, ""), lambda argv: (0, "")):
            self.assertEqual(A.listar_zonas(correr, leer=lambda ruta: tabla),
                             ["America/Santiago", "Europe/Madrid", "UTC"])

        def no_hay(argv):
            raise FileNotFoundError("timedatectl")
        self.assertEqual(A.listar_zonas(no_hay, leer=lambda ruta: tabla), ["America/Santiago", "Europe/Madrid", "UTC"])

    def test_sin_nada_la_lista_esta_vacia(self):
        def sin_tabla(ruta):
            raise FileNotFoundError(ruta)
        self.assertEqual(A.listar_zonas(lambda argv: (1, ""), leer=sin_tabla), [])
        self.assertEqual(A.listar_zonas(lambda argv: (1, ""), leer=lambda ruta: ""), [])

    def test_la_lista_real_del_sistema_tiene_zonas_validas(self):
        zonas = A.listar_zonas()   # timedatectl, o zone.tab; sin systemd ni tzdata puede estar vacía
        for z in zonas:
            self.assertEqual(A.validar_zona(z), z)


# ------------------------------------------ el reloj del dock (dock.py no se importa: pide gtk4-layer-shell)
def _funcion_del_dock(clase, nombre):
    with open(DOCK, encoding="utf-8") as f:
        arbol = ast.parse(f.read(), DOCK)
    for c in arbol.body:
        if isinstance(c, ast.ClassDef) and c.name == clase:
            for f in c.body:
                if isinstance(f, ast.FunctionDef) and f.name == nombre:
                    return f
    raise AssertionError(f"dock.py ya no tiene {clase}.{nombre}")


def _compilar(funcion, **espacio):
    """La función del dock, ejecutada sola con un espacio de nombres propio (los falsos de GLib y de ajustes)."""
    modulo = ast.Module(body=[funcion], type_ignores=[])
    ast.fix_missing_locations(modulo)
    ns = dict(espacio)
    exec(compile(modulo, DOCK, "exec"), ns)
    return ns[funcion.name]


class Etiqueta:
    def __init__(self):
        self.texto = None

    def set_text(self, t):
        self.texto = t


class FechaFalsa:
    """Lo que devuelve GLib.DateTime.new_now_local(): una fecha fija con el mismo formato que strftime."""

    def __init__(self, fecha, formatos):
        self.fecha, self.formatos = fecha, formatos

    def format(self, formato):
        self.formatos.append(formato)
        return self.fecha.strftime(formato.replace("%-I", "%I").replace("%-d", "%d"))

    def get_year(self):
        return self.fecha.year

    def get_month(self):
        return self.fecha.month

    def get_day_of_month(self):
        return self.fecha.day


class TestRelojDelDock(unittest.TestCase):
    def reloj(self, fecha, fmt="%H:%M"):
        formatos = []
        glib = mock.Mock()
        glib.DateTime.new_now_local.return_value = FechaFalsa(fecha, formatos)
        actualizar = _compilar(_funcion_del_dock("Estado", "_actualizar_reloj"), GLib=glib, fecha_corta=A.fecha_corta)
        yo = mock.Mock(hora=Etiqueta(), fecha=Etiqueta(), _fmt_hora=fmt)
        actualizar(yo)
        return yo, formatos

    def test_la_fecha_sale_en_espanol(self):
        for (a, m, d), dia in CASOS_DEL_USUARIO:
            yo, _ = self.reloj(datetime.datetime(a, m, d, 8, 5))
            corto = A.DIAS_CORTOS[datetime.date(a, m, d).weekday()]
            self.assertEqual(yo.fecha.texto, f"{corto} {d} {A.MESES_CORTOS[m - 1]}")
            self.assertTrue(yo.fecha.texto.startswith(dia[:2]), (yo.fecha.texto, dia))
        yo, _ = self.reloj(datetime.datetime(2026, 10, 19, 8, 5))
        self.assertEqual(yo.fecha.texto, "lun 19 oct")
        yo, _ = self.reloj(datetime.datetime(2026, 10, 18, 8, 5))
        self.assertEqual(yo.fecha.texto, "dom 18 oct")   # antes: «Sun 18 Oct»

    def test_la_hora_conserva_su_formato(self):
        yo, formatos = self.reloj(datetime.datetime(2026, 10, 19, 15, 7), "%H:%M")
        self.assertEqual(yo.hora.texto, "15:07")
        self.assertEqual(formatos, ["%H:%M"])   # y la fecha no pide a GLib ningún %a ni %b
        yo, formatos = self.reloj(datetime.datetime(2026, 10, 19, 15, 7), "%-I:%M %p")
        self.assertEqual(formatos, ["%-I:%M %p"])
        self.assertEqual(yo.hora.texto, "03:07 PM")   # (strftime del falso; GLib no pone el cero)

    def test_ningun_formato_de_dia_ni_mes_del_sistema(self):
        funcion = _funcion_del_dock("Estado", "_actualizar_reloj")
        textos = [n.value for n in ast.walk(funcion) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
        self.assertEqual(textos, [], "el reloj ya no escribe formatos de fecha a mano: solo fecha_corta()")
        self.assertIn("fecha_corta", {n.func.id for n in ast.walk(funcion)
                                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)})

    def test_el_formato_24h_y_12h_de_ajustes_sigue_igual(self):
        for valor, esperado in (("24h", "%H:%M"), ("12h", "%-I:%M %p"), ("otro", "%H:%M")):
            recargar = _compilar(_funcion_del_dock("Estado", "recargar_reloj"), ajuste=lambda id, v=valor: v)
            yo = mock.Mock()
            recargar(yo)
            self.assertEqual(yo._fmt_hora, esperado, valor)
            yo._actualizar_reloj.assert_called_once_with()

    def test_dock_importa_la_tabla_de_ajustes(self):
        with open(DOCK, encoding="utf-8") as f:
            self.assertRegex(f.read(), r"from \.ajustes import [^\n]*\bfecha_corta\b")

    def test_con_glib_real_los_nombres_de_los_metodos_existen(self):
        """Si hay PyGObject (no hace falta pantalla): GLib.DateTime tiene get_year/get_month/get_day_of_month."""
        try:
            import gi
            from gi.repository import GLib
        except ImportError:
            self.skipTest("sin PyGObject")
        t = GLib.DateTime.new_local(2026, 10, 19, 14, 35, 7)
        self.assertEqual((t.get_year(), t.get_month(), t.get_day_of_month()), (2026, 10, 19))
        self.assertEqual(A.fecha_corta(t.get_year(), t.get_month(), t.get_day_of_month()), "lun 19 oct")
        self.assertIsNotNone(gi)


if __name__ == "__main__":
    unittest.main()
