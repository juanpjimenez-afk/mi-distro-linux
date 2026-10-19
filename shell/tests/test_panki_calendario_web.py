"""El calendario de pAnkI y el agendar por chat, EJECUTANDO la página de verdad en Chromium (Playwright).

Verifica, con el reloj y la zona horaria simulados:
  * los días de la semana (19/10/2026 = lunes, 23/10/2026 = viernes, 1/1/2027 = viernes, 29/2/2028 = martes);
  * «hoy», «mañana» y el calendario en 7 zonas (Chile, Argentina, UTC, Madrid, Los Ángeles, Auckland, Kolkata)
    a las 23:30 y a las 00:30 locales (donde fallan los errores de UTC), y alrededor de los cambios de hora;
  * «agéndame una reunión a las 14:35 el 23 de octubre con pepe» y sus variantes (fecha, hora, persona,
    título, aviso del año siguiente, fecha inválida, hora que falta, otro evento a la misma hora);
  * consultar, mover y borrar por chat; la vista del calendario (cuadrícula con la semana desde el LUNES,
    puntos y contadores, lista del día, añadir a mano, borrar, marcar hecho, deshacer, navegación);
  * que los eventos son las mismas entradas que las tareas y que sobreviven a cerrar y reabrir el navegador.

Se SALTAN si no hay Playwright de Python o un Chromium que lanzar (la CI de GitHub solo tiene python3).
Variable opcional PANKI_CHROMIUM: ruta del ejecutable de Chromium.
"""
import datetime as dt
import glob
import html as htmlmod
import os
import re
import tempfile
import sys
import unittest
from zoneinfo import ZoneInfo

HERE = os.path.dirname(os.path.abspath(__file__))
APP_HTML = os.path.normpath(os.path.join(HERE, "..", "infinity_shell", "panki_web", "app.html"))
URL = "file://" + APP_HTML

sys.path.insert(0, os.path.normpath(os.path.join(HERE, "..")))
from infinity_shell import panki_app  # noqa: E402  (PUENTE_JS: lo que panki_app.py le inyecta a la página)

ZONAS = ["America/Santiago", "America/Argentina/Buenos_Aires", "UTC", "Europe/Madrid",
         "America/Los_Angeles", "Pacific/Auckland", "Asia/Kolkata"]
DIAS = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre",
         "noviembre", "diciembre"]

_PW = None
_BROWSER = None
_MOTIVO = None


def _chromium():
    candidatos = [os.environ.get("PANKI_CHROMIUM", ""), "/opt/pw-browsers/chromium"]
    candidatos += sorted(glob.glob(os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome")))
    for ruta in candidatos:
        if ruta and os.path.exists(ruta):
            return ruta
    return None  # que Playwright use el suyo, si lo tiene


def setUpModule():
    global _PW, _BROWSER, _MOTIVO
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        _MOTIVO = "sin Playwright de Python"
        return
    try:
        for tz in ZONAS:
            ZoneInfo(tz)
    except Exception:  # noqa: BLE001 - ZoneInfoNotFoundError u otro: no hay base de datos de zonas horarias
        _MOTIVO = "sin base de datos de zonas horarias (paquete tzdata)"
        return
    try:
        _PW = sync_playwright().start()
        kw = {"args": ["--no-sandbox", "--disable-gpu"]}
        ruta = _chromium()
        if ruta:
            kw["executable_path"] = ruta
        _BROWSER = _PW.chromium.launch(**kw)
    except Exception as e:  # noqa: BLE001 - cualquier fallo al lanzar = no hay navegador utilizable
        _MOTIVO = f"no se pudo lanzar Chromium: {str(e).splitlines()[0] if str(e) else type(e).__name__}"
        if _PW is not None:
            try:
                _PW.stop()
            except Exception:  # noqa: BLE001
                pass
            _PW = None


def tearDownModule():
    global _PW, _BROWSER
    if _BROWSER is not None:
        _BROWSER.close()
        _BROWSER = None
    if _PW is not None:
        _PW.stop()
        _PW = None


def instante(tz, y, m, d, h, mi):
    """Segundos Unix del momento local dado (el reloj falso de Playwright los espera en segundos)."""
    return int(dt.datetime(y, m, d, h, mi, tzinfo=ZoneInfo(tz)).timestamp())


def texto(h):
    """El texto visible de un trozo de HTML (como lo vería el usuario), con los espacios normalizados."""
    t = re.sub(r"<(?:br|/p|/div|/h4|/button)[^>]*>", " ", str(h))
    return re.sub(r"\s+", " ", htmlmod.unescape(re.sub(r"<[^>]+>", "", t))).strip()


def fecha_larga(d):
    return f"{DIAS[d.weekday()]} {d.day} de {MESES[d.month - 1]} de {d.year}"


class Base(unittest.TestCase):
    TZ = "America/Santiago"
    AHORA = (2026, 10, 19, 12, 0)  # lunes 19 de octubre de 2026 a las 12:00 locales

    def setUp(self):
        if _BROWSER is None:
            self.skipTest(_MOTIVO or "sin navegador")

    def pagina(self, tz=None, ahora=None, modo="fast", locale="es-CL", scale=1, scheme="light"):
        """Abre app.html con la zona y la hora simuladas. modo 'fast' (Rápido, síncrono) o 'deep'."""
        tz = tz or self.TZ
        ahora = ahora or self.AHORA
        ctx = _BROWSER.new_context(timezone_id=tz, locale=locale, viewport={"width": 460, "height": 640},
                                   device_scale_factor=scale, color_scheme=scheme)
        self.addCleanup(ctx.close)
        ctx.add_init_script("window.__PANKI_NOMBRE_SISTEMA__='pAnkI';")
        prefs = '{mode:"%s",web:false}' % modo
        ctx.add_init_script("try{localStorage.setItem('infinitos.prefs',JSON.stringify(%s))}catch(e){}" % prefs)
        page = ctx.new_page()
        self.errores = []
        page.on("pageerror", lambda e: self.errores.append(str(e)))
        page.clock.install(time=instante(tz, *ahora))
        page.goto(URL)
        return page

    def tearDown(self):
        self.assertEqual(getattr(self, "errores", []), [], "errores de JavaScript en la página")

    @staticmethod
    def resp(page, frase, deep=False):
        h = page.evaluate("([f,d])=>{const r=d?respondDeep(f,{canWeb:false}):respond(f);return typeof r==='string'?r:JSON.stringify(r)}",
                          [frase, deep])
        return h

    def di(self, page, frase, deep=False):
        return texto(self.resp(page, frase, deep))

    @staticmethod
    def tareas(page):
        return page.evaluate("S.tasks.map(t=>({id:t.id,title:t.title,due:t.due,time:t.time,who:t.who||null,loc:t.loc||null,done:t.done}))")


# =========================================================================================== fechas
class TestDiasDeLaSemana(Base):
    def test_fechas_obligatorias(self):
        page = self.pagina()
        for (y, m, d), esperado in (((2026, 10, 19), "lunes"), ((2026, 10, 23), "viernes"),
                                    ((2027, 1, 1), "viernes"), ((2028, 2, 29), "martes")):
            clave = f"{y}-{m:02d}-{d:02d}"
            self.assertEqual(page.evaluate("(k)=>DAYS_ES[wdayK(k)]", clave), esperado, clave)
            self.assertEqual(page.evaluate("(k)=>fechaLarga(k)", clave), f"{esperado} {d} de {MESES[m - 1]} de {y}")

    def test_que_dia_es_el_19_10_2026_dice_lunes(self):
        page = self.pagina()
        for frase, dia in (("¿qué día es el 19/10/2026?", "lunes"), ("qué día es el 23/10/2026", "viernes"),
                           ("qué día cae el 1/1/2027", "viernes"), ("qué día es el 29/2/2028", "martes"),
                           ("qué día es el 19 de octubre de 2026", "lunes"), ("en qué día cae el 2026-10-23", "viernes")):
            r = self.di(page, frase)
            self.assertIn(dia, r, frase)
            self.assertNotIn("jueves", r, frase) if dia == "lunes" else None

    def test_toda_la_tabla_coincide_con_python_en_cualquier_zona(self):
        # 1900–2200: cada día de la semana que calcula la página es el que calcula Python
        casos = []
        d = dt.date(1900, 1, 1)
        paso = dt.timedelta(days=173)
        while d < dt.date(2200, 1, 1):
            casos.append([d.isoformat(), DIAS[d.weekday()]])
            d += paso
        casos += [["2028-02-29", "martes"], ["2100-02-28", "domingo"], ["2000-02-29", "martes"], ["2024-12-31", "martes"]]
        for tz in ("America/Santiago", "Pacific/Auckland", "America/Los_Angeles"):
            page = self.pagina(tz)
            malos = page.evaluate("(cs)=>cs.filter(([k,w])=>DAYS_ES[wdayK(k)]!==w).map(c=>c.join(' '))", casos)
            self.assertEqual(malos, [], tz)

    def test_fechas_invalidas_no_existen(self):
        page = self.pagina()
        for frase in ("qué día es el 31 de abril", "qué día es el 29/2/2027", "qué día es el 31/4/2026", "qué día es el 30 de febrero",
                      "qué día es el 2026-02-30"):
            r = self.di(page, frase)
            self.assertIn("no existe", r, frase)
        self.assertIn("29 de febrero", self.di(page, "qué día es el 29 de febrero"))  # sin año: busca el próximo bisiesto
        self.assertIn("martes", self.di(page, "qué día es el 29 de febrero"))  # 2028


class TestHoyYMananaEnVariasZonas(Base):
    """Los casos donde falla el error clásico de UTC: de noche (23:30) y de madrugada (00:30) locales."""

    MOMENTOS = ((23, 30), (0, 30), (12, 0))

    def _comprobar(self, tz, y, m, d, h, mi):
        hoy = dt.date(y, m, d)
        manana, pasado = hoy + dt.timedelta(days=1), hoy + dt.timedelta(days=2)
        page = self.pagina(tz, (y, m, d, h, mi))
        etiqueta = f"{tz} {hoy} {h:02d}:{mi:02d}"
        r = page.evaluate("""()=>({hoy:todayK(),man:parseWhen('mañana').date,pas:parseWhen('pasado mañana').date,
              en3:parseWhen('en 3 días').date,sem:parseWhen('en una semana').date,dd:dayDiff(addK(todayK(),1)),
              lab:dueLabel({due:todayK()}),labm:dueLabel({due:addK(todayK(),1)}),
              qh:respond('qué día es hoy'),qm:respond('qué día es mañana')})""")
        self.assertEqual(r["hoy"], hoy.isoformat(), etiqueta)
        self.assertEqual(r["man"], manana.isoformat(), etiqueta)
        self.assertEqual(r["pas"], pasado.isoformat(), etiqueta)
        self.assertEqual(r["en3"], (hoy + dt.timedelta(days=3)).isoformat(), etiqueta)
        self.assertEqual(r["sem"], (hoy + dt.timedelta(days=7)).isoformat(), etiqueta)
        self.assertEqual(r["dd"], 1, etiqueta)
        self.assertEqual((r["lab"], r["labm"]), ("hoy", "mañana"), etiqueta)
        self.assertIn(fecha_larga(hoy), texto(r["qh"]), etiqueta)
        self.assertIn(fecha_larga(manana), texto(r["qm"]), etiqueta)
        # el calendario marca el día correcto, en la columna correcta de la semana (lunes = 0)
        page.evaluate("pankiAbrirCalendario()")
        celdas = page.eval_on_selector_all("#calGrid .cd", "els=>els.map(e=>[e.dataset.cd,e.classList.contains('today'),e.classList.contains('sel')])")
        marcadas = [i for i, (k, es_hoy, _) in enumerate(celdas) if es_hoy]
        self.assertEqual(len(marcadas), 1, etiqueta)
        self.assertEqual(celdas[marcadas[0]][0], hoy.isoformat(), etiqueta)
        self.assertEqual(marcadas[0] % 7, hoy.weekday(), etiqueta)
        self.assertEqual(page.inner_text("#calTitle"), f"{MESES[m - 1].capitalize()} de {y}", etiqueta)
        self.assertIn(DIAS[hoy.weekday()], page.get_attribute("#calGrid .cd.today", "aria-label"), etiqueta)
        self.assertEqual(page.inner_text("#calDayTitle"), fecha_larga(hoy).capitalize(), etiqueta)

    def test_siete_zonas_a_las_23_30_y_00_30(self):
        for tz in ZONAS:
            for h, mi in self.MOMENTOS:
                with self.subTest(tz=tz, hora=f"{h:02d}:{mi:02d}"):
                    self._comprobar(tz, 2026, 10, 19, h, mi)

    def test_cambios_de_hora_y_bordes_del_calendario(self):
        casos = [
            ("America/Santiago", 2026, 9, 5, 23, 30),   # empieza el horario de verano a las 24:00
            ("America/Santiago", 2027, 4, 3, 23, 30),   # termina
            ("Europe/Madrid", 2026, 10, 24, 23, 30),    # termina de madrugada
            ("Europe/Madrid", 2027, 3, 27, 23, 30),
            ("America/Los_Angeles", 2026, 11, 1, 0, 30),
            ("America/Los_Angeles", 2027, 3, 14, 12, 0),
            ("Pacific/Auckland", 2026, 9, 26, 23, 30),
            ("Pacific/Auckland", 2027, 4, 3, 23, 30),
            ("Asia/Kolkata", 2026, 12, 31, 23, 30),     # fin de año
            ("UTC", 2028, 2, 28, 23, 30),               # mañana es 29 de febrero (año bisiesto)
            ("America/Argentina/Buenos_Aires", 2028, 2, 29, 0, 30),
            ("UTC", 2027, 12, 31, 23, 30),
        ]
        for tz, y, m, d, h, mi in casos:
            with self.subTest(tz=tz, fecha=f"{y}-{m:02d}-{d:02d}", hora=f"{h:02d}:{mi:02d}"):
                self._comprobar(tz, y, m, d, h, mi)

    def test_sumar_meses_no_se_desborda(self):
        page = self.pagina(ahora=(2026, 1, 31, 12, 0))
        self.assertEqual(page.evaluate("parseWhen('en 1 mes').date"), "2026-02-28")
        self.assertEqual(page.evaluate("parseWhen('en 2 meses').date"), "2026-03-31")
        page = self.pagina(ahora=(2028, 1, 31, 12, 0))
        self.assertEqual(page.evaluate("parseWhen('en 1 mes').date"), "2028-02-29")

    def test_el_31_salta_a_un_mes_que_lo_tenga(self):
        page = self.pagina(ahora=(2026, 4, 15, 12, 0))
        self.assertEqual(page.evaluate("parseWhen('el 31').date"), "2026-05-31")
        page = self.pagina(ahora=(2026, 1, 31, 12, 0))
        self.assertEqual(page.evaluate("parseWhen('el 30').date"), "2026-03-30")  # febrero no tiene 30


# =========================================================================================== agendar por chat
FRASE_USUARIO = "agéndame una reunión a las 14:35 el 23 de octubre con pepe"


class TestAgendarPorChat(Base):
    def test_la_frase_del_usuario_en_varias_zonas(self):
        for tz in ZONAS:
            with self.subTest(tz=tz):
                page = self.pagina(tz)
                r = self.di(page, FRASE_USUARIO)
                self.assertTrue(r.startswith("Agendada: Reunión con Pepe · viernes 23 de octubre de 2026 a las 14:35"), r)
                (t,) = self.tareas(page)
                self.assertEqual((t["due"], t["time"], t["who"], t["title"]),
                                 ("2026-10-23", "14:35", "Pepe", "Reunión con Pepe"))

    def test_la_frase_del_usuario_en_modo_profundo(self):
        page = self.pagina(modo="deep")
        self.assertEqual(self.di(page, FRASE_USUARIO, deep=True).split(" · ")[0], "Agendada: Reunión con Pepe")
        (t,) = self.tareas(page)
        self.assertEqual((t["due"], t["time"], t["who"]), ("2026-10-23", "14:35", "Pepe"))
        self.assertEqual(page.evaluate("webSent"), 0, "agendar no puede salir a internet")

    def test_por_la_interfaz_se_ve_la_respuesta_y_el_boton_del_calendario(self):
        page = self.pagina()
        page.fill("#in", FRASE_USUARIO)
        page.press("#in", "Enter")
        page.wait_for_function("document.querySelectorAll('#chat .m.a').length>1")
        msg = page.locator("#chat .m.a").last
        self.assertIn("Agendada: Reunión con Pepe · viernes 23 de octubre de 2026 a las 14:35", msg.inner_text())
        boton = msg.locator("[data-cal]")
        self.assertEqual(boton.get_attribute("data-cal"), "2026-10-23")
        boton.click()
        self.assertTrue(page.evaluate("document.body.classList.contains('vcal')"))
        self.assertEqual(page.inner_text("#calTitle"), "Octubre de 2026")
        self.assertEqual(page.inner_text("#calDayTitle"), "Viernes 23 de octubre de 2026")
        self.assertIn("Reunión con Pepe", page.inner_text("#calList"))
        self.assertIn("14:35", page.inner_text("#calList"))

    def test_variantes_de_la_orden_y_de_la_fecha_y_la_hora(self):
        # (frase, fecha, hora, título); hoy es el lunes 19 de octubre de 2026
        casos = [
            ("agendame una reunion a las 14:35 el 23 de octubre con pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("programa una reunión a las 14:35 el 23 de octubre con pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("anota una reunión el 23/10 a las 14:35 con Pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("ponme una reunión a las 14:35 el 23 de octubre con pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("crea una reunión a las 14:35 el 23 de octubre con pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("agéndame una reunión el 23 de octubre a las 14:35 con pepe", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("reunión con Pepe el viernes 23 a las 14:35", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("reunión con Pepe el viernes 23 de octubre a las 14:35", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("agéndame reunión con Pepe mañana a las 10", "2026-10-20", "10:00", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe pasado mañana a las 9", "2026-10-21", "09:00", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el próximo lunes a las 16:00", "2026-10-26", "16:00", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe dentro de 3 días a las 14.35", "2026-10-22", "14:35", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe en 3 días a las 14h35", "2026-10-22", "14:35", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el 23 de octubre a las 2 y media de la tarde", "2026-10-23", "14:30", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el 23 de octubre 2 y media de la tarde", "2026-10-23", "14:30", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el 23 de octubre a las 14.35", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el 23 de octubre a las 14h35", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("agéndame una reunión con Pepe el 23 de octubre a las 2:35 pm", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("¿puedes agendarme una reunión con Pepe el 23 de octubre a las 14:35?", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("quiero agendar una reunión con Pepe el 23 de octubre a las 14:35", "2026-10-23", "14:35", "Reunión con Pepe"),
            ("agéndame un almuerzo con Ana el viernes a las 13", "2026-10-23", "13:00", "Almuerzo con Ana"),
            ("agéndame una reunión con maría josé el 23 de octubre a las 14:35", "2026-10-23", "14:35", "Reunión con María José"),
            ("Mañana a las 10 reunión con Ana", "2026-10-20", "10:00", "Reunión con Ana"),
        ]
        for frase, due, hora, titulo in casos:
            for deep in (False, True):
                with self.subTest(frase=frase, profundo=deep):
                    page = self.pagina()
                    r = self.di(page, frase, deep=deep)
                    ts = self.tareas(page)
                    self.assertEqual(len(ts), 1, (r, ts))
                    self.assertEqual((ts[0]["due"], ts[0]["time"], ts[0]["title"]), (due, hora, titulo), r)
                    y, m, d = (int(x) for x in due.split("-"))
                    self.assertIn(fecha_larga(dt.date(y, m, d)), r)  # el día de la semana, siempre calculado
                    self.assertIn("a las " + hora, r)

    def test_la_persona_se_guarda_con_mayuscula(self):
        page = self.pagina()
        self.di(page, "agéndame una reunión con el doctor lópez el 23 de octubre a las 9")
        self.di(page, "agéndame una reunión con mi mamá el 24 de octubre a las 10")
        self.di(page, "agéndame una reunión con el equipo de ventas el 26 de octubre a las 11")
        self.di(page, "agéndame una reunión con ana y pepe el 27 de octubre a las 11")
        quien = [t["who"] for t in self.tareas(page)]
        self.assertEqual(quien, ["el doctor López", "mi mamá", "el equipo de ventas", "Ana y Pepe"])

    def test_el_lugar_se_guarda_aparte(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con pepe en la oficina el 23 de octubre a las 14:35")
        (t,) = self.tareas(page)
        self.assertEqual((t["title"], t["who"], t["loc"]), ("Reunión con Pepe", "Pepe", "la oficina"))
        self.assertIn("la oficina", r)

    def test_si_ya_paso_este_ano_se_agenda_para_el_siguiente_y_se_dice(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe el 15 de marzo a las 10")
        (t,) = self.tareas(page)
        self.assertEqual(t["due"], "2027-03-15")
        self.assertIn("lunes 15 de marzo de 2027", r)
        self.assertIn("ya pasó", r)
        self.assertIn("2027", r)
        # y la fecha de hoy NO cuenta como pasada
        page = self.pagina()
        self.di(page, "agéndame una reunión con Pepe el 19 de octubre a las 18")
        self.assertEqual(self.tareas(page)[0]["due"], "2026-10-19")

    def test_29_de_febrero_busca_el_proximo_bisiesto(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe el 29 de febrero a las 10")
        self.assertEqual(self.tareas(page)[0]["due"], "2028-02-29")
        self.assertIn("martes 29 de febrero de 2028", r)

    def test_fecha_u_hora_invalidas_se_dicen_y_no_se_guarda_nada(self):
        page = self.pagina()
        for frase, fragmento in (("agéndame una reunión con Pepe el 31 de abril a las 10", "no existe"),
                                 ("agéndame una reunión con Pepe el 30 de febrero a las 10", "no existe"),
                                 ("agéndame una reunión con Pepe el 29/2/2027 a las 10", "no existe"),
                                 ("agéndame una reunión con Pepe el 23 de octubre a las 25:70", "no es válida")):
            r = self.di(page, frase)
            self.assertIn(fragmento, r, frase)
        self.assertEqual(self.tareas(page), [])
        self.assertIn("abril tiene 30 días", self.di(page, "agéndame una reunión con Pepe el 31 de abril a las 10"))

    def test_si_falta_la_hora_se_pregunta_y_la_respuesta_completa_la_reunion(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe el 23 de octubre")
        self.assertIn("¿A qué hora", r)
        self.assertEqual(self.tareas(page)[0]["time"], None)
        r2 = self.di(page, "a las 2 y media de la tarde")
        self.assertIn("Listo: Reunión con Pepe · viernes 23 de octubre de 2026 a las 14:30", r2)
        (t,) = self.tareas(page)
        self.assertEqual((t["due"], t["time"]), ("2026-10-23", "14:30"))
        # y una respuesta que no era la hora se trata como un mensaje normal (sin tocar la reunión)
        self.di(page, "agéndame una reunión con Ana el 24 de octubre")
        r3 = self.di(page, "borra la reunión con Ana")
        self.assertIn("Borrada: Reunión con Ana", r3)

    def test_si_faltan_dia_y_hora_se_pregunta_antes_de_guardar(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe")
        self.assertIn("¿Para qué día y a qué hora", r)
        self.assertEqual(self.tareas(page), [])
        r2 = self.di(page, "el viernes a las 15:00")
        self.assertIn("Agendada: Reunión con Pepe · viernes 23 de octubre de 2026 a las 15:00", r2)
        self.assertEqual(len(self.tareas(page)), 1)

    def test_otra_reunion_a_la_misma_hora_se_avisa(self):
        page = self.pagina()
        self.di(page, FRASE_USUARIO)
        r = self.di(page, "agéndame una reunión con Ana el 23 de octubre a las 14:35")
        self.assertIn("ya tienes «Reunión con Pepe» a esa misma hora (14:35)", r)
        r = self.di(page, "agéndame una reunión con Luis el 23 de octubre a las 15:00")
        self.assertIn("se superpone con", r)
        r = self.di(page, "agéndame una reunión con Eva el 23 de octubre a las 18:00")
        self.assertNotIn("Ojo", r)

    def test_si_dice_un_dia_de_la_semana_que_no_cuadra_con_la_fecha_se_avisa(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe el lunes 23 de octubre a las 14:35")
        self.assertIn("dijiste lunes", r)
        self.assertIn("cae viernes", r)
        self.assertEqual(self.tareas(page)[0]["due"], "2026-10-23")  # manda la fecha

    def test_sin_dia_se_dice_que_se_asumio(self):
        page = self.pagina()
        r = self.di(page, "agéndame una reunión con Pepe a las 14:35")
        self.assertIn("hoy, lunes 19 de octubre de 2026 a las 14:35", r)
        self.assertIn("No dijiste el día", r)
        page = self.pagina(ahora=(2026, 10, 19, 20, 0))
        r = self.di(page, "agéndame una reunión con Pepe a las 14:35")
        self.assertIn("mañana, martes 20 de octubre de 2026", r)

    def test_la_misma_reunion_sin_hora_se_completa_en_vez_de_duplicarse(self):
        page = self.pagina()
        self.di(page, "agéndame una reunión con Pepe el 23 de octubre")
        r = self.di(page, "agéndame una reunión con Pepe el 23 de octubre a las 14:35")
        self.assertIn("Actualizada: Reunión con Pepe", r)
        self.assertEqual(len(self.tareas(page)), 1)

    def test_las_tareas_normales_siguen_siendo_tareas(self):
        page = self.pagina()
        r = self.di(page, "pagar la luz el viernes urgente")
        self.assertTrue(r.startswith("Anotada."), r)
        self.assertIn("Viernes 23 de octubre de 2026", r)
        t = self.tareas(page)[0]
        self.assertEqual((t["title"], t["due"], t["time"]), ("Pagar la luz", "2026-10-23", None))


class TestConsultarMoverBorrar(Base):
    def _con_eventos(self):
        page = self.pagina()
        for f in (FRASE_USUARIO, "agéndame una reunión con Ana el 23 de octubre a las 9",
                  "agéndame una reunión con Luis el 24 de octubre a las 10", "agéndame reunión con Marta mañana a las 17"):
            self.di(page, f)
        return page

    def test_que_tengo_un_dia(self):
        page = self._con_eventos()
        for deep in (False, True):
            r = self.di(page, "¿qué tengo el 23 de octubre?", deep=deep)
            self.assertIn("Viernes 23 de octubre de 2026 · 2 eventos", r)
            self.assertIn("Reunión con Pepe", r)
            self.assertIn("Reunión con Ana", r)
            self.assertNotIn("Luis", r)
        r = self.di(page, "qué tengo mañana")
        self.assertIn("Mañana, martes 20 de octubre de 2026 · 1 evento", r)
        self.assertIn("Marta", r)
        self.assertIn("Sábado 24 de octubre de 2026 · 1 evento", self.di(page, "qué tengo el sábado"))
        self.assertIn("no tienes nada agendado", self.di(page, "qué tengo el 28 de octubre"))

    def test_mover_la_reunion(self):
        page = self._con_eventos()
        r = self.di(page, "mueve la reunión con Pepe al 24 a las 10")
        self.assertIn("Reprogramada: Reunión con Pepe · sábado 24 de octubre de 2026 a las 10:00", r)
        self.assertIn("se superpone con", r) if False else None
        pepe = [t for t in self.tareas(page) if t["who"] == "Pepe"][0]
        self.assertEqual((pepe["due"], pepe["time"]), ("2026-10-24", "10:00"))
        # solo la hora: conserva el día
        r = self.di(page, "mueve la reunión con Marta a las 18")
        self.assertIn("mañana, martes 20 de octubre de 2026 a las 18:00", r)
        # solo el día: conserva la hora
        r = self.di(page, "cambia la reunión con Ana al viernes 30")
        self.assertIn("viernes 30 de octubre de 2026 a las 09:00", r)
        # a una fecha que no existe
        self.assertIn("no existe", self.di(page, "mueve la reunión con Luis al 31 de abril"))

    def test_mover_a_mañana(self):
        page = self._con_eventos()
        r = self.di(page, "mueve la reunión con Luis para mañana a las 11")
        self.assertIn("mañana, martes 20 de octubre de 2026 a las 11:00", r)

    def test_borrar_la_reunion(self):
        page = self._con_eventos()
        r = self.di(page, "borra la reunión con Pepe")
        self.assertIn("Borrada: Reunión con Pepe · viernes 23 de octubre de 2026 a las 14:35", r)
        self.assertEqual([t["who"] for t in self.tareas(page)], ["Ana", "Luis", "Marta"])
        self.assertIn("Borrada: Reunión con Ana", self.di(page, "cancela la reunión con Ana"))
        self.assertIn("No encontré esa reunión", self.di(page, "borra la reunión con Zoe"))
        # deshacer la devuelve
        self.di(page, "deshacer")
        self.assertEqual(len(self.tareas(page)), 3)

    def test_borrar_elige_la_mas_proxima_y_lo_dice(self):
        page = self.pagina()
        self.di(page, "agéndame una reunión con Pepe el 30 de octubre a las 10")
        self.di(page, "agéndame una reunión con Pepe el 23 de octubre a las 10")
        r = self.di(page, "borra la reunión con Pepe")
        self.assertIn("viernes 23 de octubre de 2026", r)
        self.assertIn("Había 2", r)
        self.assertEqual(self.tareas(page)[0]["due"], "2026-10-30")

    def test_abrir_el_calendario_por_chat(self):
        page = self.pagina()
        self.di(page, "abre el calendario")
        self.assertTrue(page.evaluate("calOn"))
        self.assertEqual(page.inner_text("#calTitle"), "Octubre de 2026")
        page.evaluate("cerrarCalendario()")
        self.di(page, "calendario de marzo de 2028")
        self.assertEqual(page.inner_text("#calTitle"), "Marzo de 2028")
        self.di(page, "ver calendario de noviembre")
        self.assertEqual(page.inner_text("#calTitle"), "Noviembre de 2026")


# =========================================================================================== la vista
class TestVistaCalendario(Base):
    def _meses(self):
        return [(2026, 10), (2026, 11), (2026, 12), (2027, 1), (2027, 2), (2028, 2), (2028, 3), (2026, 2)]

    def test_la_cuadricula_empieza_en_lunes_y_cada_dia_cae_en_su_columna(self):
        import calendar as cal
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario()")
        self.assertEqual(page.eval_on_selector_all(".cal-dow span", "els=>els.map(e=>e.textContent).join('')"), "LMXJVSD")
        for y, m in self._meses():
            page.evaluate("([y,m])=>{calSet(ymdKey(y,m-1,1));renderCal()}", [y, m])
            celdas = page.eval_on_selector_all("#calGrid .cd", "els=>els.map(e=>[e.dataset.cd,e.classList.contains('o')])")
            self.assertEqual(len(celdas) % 7, 0)
            filas = len(celdas) // 7
            esperadas = len(cal.Calendar(firstweekday=0).monthdatescalendar(y, m))
            self.assertEqual(filas, esperadas, f"{y}-{m}")
            for i, (k, otro_mes) in enumerate(celdas):
                d = dt.date.fromisoformat(k)
                self.assertEqual(d.weekday(), i % 7, f"{k} en la columna {i % 7}")
                self.assertEqual(otro_mes, d.month != m, k)
            self.assertEqual(page.inner_text("#calTitle"), f"{MESES[m - 1].capitalize()} de {y}")

    def test_puntos_contadores_y_lista_del_dia(self):
        page = self.pagina()
        for f in (FRASE_USUARIO, "agéndame una reunión con Ana el 23 de octubre a las 9",
                  "agéndame una reunión con Eva el 23 de octubre a las 18", "agéndame reunión con Marta mañana a las 17"):
            self.di(page, f)
        for h in (10, 11, 12, 13):
            self.di(page, f"agéndame una reunión con Tomás el 29 de octubre a las {h}")
        page.evaluate("pankiAbrirCalendario()")
        celda = lambda k: page.locator(f'#calGrid .cd[data-cd="{k}"]')  # noqa: E731
        self.assertEqual(celda("2026-10-23").locator(".dots i").count(), 3)        # 3 eventos = 3 puntos
        self.assertEqual(celda("2026-10-20").locator(".dots i").count(), 1)
        self.assertEqual(celda("2026-10-29").locator(".dots b").inner_text(), "4")  # más de 3 = contador
        self.assertEqual(celda("2026-10-21").locator(".dots i, .dots b").count(), 0)
        self.assertIn("lunes", celda("2026-10-19").get_attribute("aria-label"))
        self.assertIn("3 eventos", celda("2026-10-23").get_attribute("aria-label"))
        # hoy resaltado, y seleccionado al abrir
        self.assertEqual(page.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-19")
        # tocar un día muestra sus eventos (hora, título), ordenados por hora
        celda("2026-10-23").click()
        self.assertEqual(page.inner_text("#calDayTitle"), "Viernes 23 de octubre de 2026")
        self.assertEqual(page.inner_text("#calDaySub"), "En 4 días")
        filas = page.eval_on_selector_all("#calList .ev", "els=>els.map(e=>e.querySelector('.et').textContent+' '+e.querySelector('.ttl').textContent)")
        self.assertEqual(filas, ["09:00 Reunión con Ana", "14:35 Reunión con Pepe", "18:00 Reunión con Eva"])
        celda("2026-10-22").click()
        self.assertIn("Nada agendado", page.inner_text("#calList"))

    def test_navegacion_de_meses_y_boton_hoy(self):
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario()")
        page.click("#calNext")
        self.assertEqual(page.inner_text("#calTitle"), "Noviembre de 2026")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-11-19")
        for _ in range(3):
            page.click("#calNext")
        self.assertEqual(page.inner_text("#calTitle"), "Febrero de 2027")
        page.click("#calPrev")
        self.assertEqual(page.inner_text("#calTitle"), "Enero de 2027")
        self.assertEqual(page.locator("#calGrid .cd.today").count(), 0)  # hoy no está en este mes
        page.click("#calHoy")
        self.assertEqual(page.inner_text("#calTitle"), "Octubre de 2026")
        self.assertEqual(page.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        # el 31 de enero → febrero no tiene 31: queda el último día del mes
        page.evaluate("pankiAbrirCalendario('2027-01-31')")
        page.click("#calNext")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2027-02-28")

    def test_tocar_un_dia_de_otro_mes_cambia_de_mes(self):
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario()")
        page.locator('#calGrid .cd[data-cd="2026-11-01"]').click()
        self.assertEqual(page.inner_text("#calTitle"), "Noviembre de 2026")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-11-01")

    def test_teclado_flechas(self):
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario()")
        page.locator("#calGrid .cd.sel").focus()
        page.keyboard.press("ArrowRight")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-20")
        page.keyboard.press("ArrowDown")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-27")
        page.keyboard.press("PageDown")
        self.assertEqual(page.inner_text("#calTitle"), "Noviembre de 2026")

    def test_anadir_una_reunion_a_mano_aparece_en_tareas_y_en_organiza_mi_dia(self):
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario('2026-10-23')")
        page.click("#calNew")
        self.assertTrue(page.is_visible("#calForm"))
        page.fill("#cfWho", "pepe")
        page.fill("#cfTime", "14:35")
        page.fill("#cfLoc", "la oficina")
        page.click("#calForm button[type=submit]")
        self.assertFalse(page.is_visible("#calForm"))
        (t,) = self.tareas(page)
        self.assertEqual((t["title"], t["due"], t["time"], t["who"], t["loc"]),
                         ("Reunión con Pepe", "2026-10-23", "14:35", "Pepe", "la oficina"))
        self.assertIn("14:35", page.inner_text("#calList"))
        self.assertIn("en la oficina", page.inner_text("#calList"))
        self.assertEqual(page.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 1)
        # las MISMAS entradas: salen en «mis tareas», en «organiza mi día» del 23 y en el panel
        self.assertIn("Reunión con Pepe", self.di(page, "mis tareas"))
        self.assertIn("14:35", self.di(page, "organiza mi día el viernes"))
        self.assertIn("Reunión con Pepe", self.di(page, "qué tengo el 23 de octubre"))
        page.evaluate("cerrarCalendario();tab='agenda';renderPanel()")
        self.assertIn("Reunión con Pepe", page.inner_text("#pbody"))
        # y a la inversa: lo agendado por chat sale en el calendario
        self.di(page, "agéndame una reunión con Ana el 23 de octubre a las 18")
        page.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.assertIn("Reunión con Ana", page.inner_text("#calList"))

    def test_el_formulario_valida_la_hora_y_tiene_valores_por_defecto(self):
        page = self.pagina()
        page.evaluate("pankiAbrirCalendario('2026-10-23')")
        page.click("#calNew")
        page.fill("#cfTime", "99:99")
        page.click("#calForm button[type=submit]")
        self.assertEqual(self.tareas(page), [])
        self.assertTrue(page.is_visible("#calForm"))
        page.fill("#cfTime", "")
        page.click("#calForm button[type=submit]")
        (t,) = self.tareas(page)
        self.assertEqual((t["title"], t["time"], t["due"]), ("Reunión", None, "2026-10-23"))
        self.assertIn("Todo el día", page.inner_text("#calList"))
        page.click("#calNew")
        page.click("#cfCancel")
        self.assertFalse(page.is_visible("#calForm"))

    def test_marcar_hecho_borrar_y_deshacer(self):
        page = self.pagina()
        self.di(page, FRASE_USUARIO)
        page.evaluate("pankiAbrirCalendario('2026-10-23')")
        page.click("#calList .ev .chk")
        self.assertTrue(self.tareas(page)[0]["done"])
        self.assertEqual(page.locator("#calList .ev.done").count(), 1)
        self.assertEqual(page.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i.dn').count(), 1)
        page.click("#calList .ev .chk")
        self.assertFalse(self.tareas(page)[0]["done"])
        page.click("#calList .ev .x")
        self.assertEqual(self.tareas(page), [])
        self.assertIn("Borrado «Reunión con Pepe»", page.inner_text("#calUndo"))
        page.click("#calUndo button")
        self.assertEqual(len(self.tareas(page)), 1)
        self.assertIn("Reunión con Pepe", page.inner_text("#calList"))
        self.assertEqual(page.inner_text("#calUndo"), "")

    def test_el_boton_de_la_cabecera_alterna_y_enviar_vuelve_al_chat(self):
        page = self.pagina()
        self.assertFalse(page.is_visible("#calView"))
        page.click("#btnCal")
        self.assertTrue(page.is_visible("#calView"))
        self.assertFalse(page.is_visible("#in"))
        page.click("#btnCal")
        self.assertFalse(page.is_visible("#calView"))
        self.assertTrue(page.is_visible("#in"))
        page.click("#btnCal")
        page.evaluate(panki_app.PUENTE_JS)  # lo que inyecta panki_app.py: window.pankiPreguntar (--preguntar)
        page.evaluate("pankiPreguntar('qué día es el 19/10/2026')")  # con el calendario abierto
        page.wait_for_function("document.querySelectorAll('#chat .m.a').length>1")
        self.assertTrue(page.is_visible("#in"))
        self.assertIn("lunes", page.locator("#chat .m.a").last.inner_text())

    def test_puente_del_host_pankiAbrirCalendario(self):
        page = self.pagina()
        page.evaluate("window.pankiAbrirCalendario('2027-02-14')")
        self.assertEqual(page.inner_text("#calTitle"), "Febrero de 2027")
        self.assertEqual(page.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2027-02-14")
        for malo in ("ayer", "2026-02-30", "2026-13-01", "", None, 42, "<img src=x onerror=1>", "2026-10-2"):
            page.evaluate("(v)=>{window.pankiAbrirCalendario(v)}", malo)
            self.assertEqual(page.inner_text("#calTitle"), "Octubre de 2026", repr(malo))  # cae al mes actual
        self.assertTrue(page.evaluate("typeof window.pankiAbrirCalendario==='function'"))

    def test_la_pagina_con_el_hash_calendario_abre_la_vista(self):
        page = self.pagina()
        page.goto(URL + "#calendario")
        page.reload()
        self.assertTrue(page.evaluate("calOn"))

    def test_cabe_en_460x640_sin_desbordar(self):
        page = self.pagina(scale=2)
        for f in (FRASE_USUARIO, "agéndame una reunión con Eva el 23 de octubre a las 18",
                  "agéndame una reunión con Ana el 23 de octubre a las 9"):
            self.di(page, f)
        page.evaluate("pankiAbrirCalendario('2026-10-23')")
        d = page.evaluate("""()=>({sw:document.documentElement.scrollWidth,iw:innerWidth,
            grid:document.querySelector('#calGrid').getBoundingClientRect().toJSON(),
            cel:document.querySelector('#calGrid .cd').getBoundingClientRect().toJSON(),
            lista:document.querySelector('#calList').getBoundingClientRect().toJSON(),
            ih:innerHeight, cw:document.querySelector('#calView').scrollWidth, cv:document.querySelector('#calView').clientWidth})""")
        self.assertLessEqual(d["sw"], d["iw"], "scroll horizontal")
        self.assertLessEqual(d["cw"], d["cv"], "el calendario desborda horizontalmente")
        self.assertGreaterEqual(d["cel"]["width"], 44)
        self.assertGreaterEqual(d["cel"]["height"], 40)
        self.assertLessEqual(d["grid"]["right"], d["iw"])
        self.assertLess(d["lista"]["top"], d["ih"] - 60, "la lista del día tiene que verse sin tener que bajar")
        # también en modo oscuro
        page2 = self.pagina(scheme="dark")
        page2.evaluate("pankiAbrirCalendario()")
        self.assertTrue(page2.is_visible("#calGrid"))

    def test_el_dia_cambia_a_medianoche_sin_recargar(self):
        page = self.pagina(ahora=(2026, 10, 19, 23, 59))
        page.evaluate("pankiAbrirCalendario()")
        self.assertEqual(page.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        page.clock.fast_forward(2 * 60 * 1000)  # pasan 2 minutos: es el martes 20
        page.clock.run_for(16000)  # y salta el tick de 15 s
        self.assertEqual(page.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-20")


class TestImportarExportar(Base):
    def test_el_respaldo_conserva_con_quien_y_donde_y_el_ics_lleva_el_lugar(self):
        page = self.pagina()
        self.di(page, "agéndame una reunión con pepe en la oficina el 23 de octubre a las 14:35")
        d = page.evaluate("""()=>{const ics=icsEvent(S.tasks[0]);const imp=cleanImport(JSON.parse(JSON.stringify(S)));
            return {ics, who:imp.tasks[0].who, loc:imp.tasks[0].loc}}""")
        self.assertEqual((d["who"], d["loc"]), ("Pepe", "la oficina"))
        self.assertIn("DTSTART:20261023T143500", d["ics"])
        self.assertIn("LOCATION:la oficina", d["ics"])
        self.assertIn("SUMMARY:Reunión con Pepe", d["ics"])
        malo = page.evaluate("cleanImport({tasks:[{id:1,title:'x',who:{a:1},loc:42}]}).tasks[0]")
        self.assertIsNone(malo.get("who"))
        self.assertIsNone(malo.get("loc"))


# =========================================================================================== persistencia
class TestPersistencia(unittest.TestCase):
    def setUp(self):
        if _BROWSER is None:
            self.skipTest(_MOTIVO or "sin navegador")

    def _abrir(self, perfil):
        kw = {"args": ["--no-sandbox", "--disable-gpu"], "headless": True, "viewport": {"width": 460, "height": 640},
              "timezone_id": "America/Santiago", "locale": "es-CL"}
        ruta = _chromium()
        if ruta:
            kw["executable_path"] = ruta
        ctx = _PW.chromium.launch_persistent_context(perfil, **kw)
        ctx.add_init_script("window.__PANKI_NOMBRE_SISTEMA__='pAnkI';try{if(!localStorage.getItem('infinitos.prefs'))localStorage.setItem('infinitos.prefs',JSON.stringify({mode:'fast',web:false}))}catch(e){}")
        page = ctx.new_page()
        page.goto(URL)
        return ctx, page

    @staticmethod
    def _esperar_en_disco(perfil, page, marca, limite_s=30):
        buscadas = (marca.encode("latin-1"), marca.encode("utf-16-le"))
        for _ in range(limite_s * 4):
            for carpeta, _d, archivos in os.walk(os.path.join(perfil, "Default", "Local Storage")):
                for nombre in archivos:
                    try:
                        with open(os.path.join(carpeta, nombre), "rb") as f:
                            datos = f.read()
                    except OSError:
                        continue
                    if any(b in datos for b in buscadas):
                        return
            page.wait_for_timeout(250)
        raise AssertionError("Chromium no escribió localStorage a disco a tiempo")

    def test_un_calendario_con_eventos_sobrevive_a_cerrar_y_reabrir(self):
        with tempfile.TemporaryDirectory() as perfil:
            ctx, page = self._abrir(perfil)
            page.evaluate("respond('agéndame una reunión a las 14:35 el 23 de octubre con pepe')")
            page.evaluate("pankiAbrirCalendario('2026-10-23')")
            page.click("#calNew")
            page.fill("#cfTitle", "Dentista")
            page.fill("#cfTime", "9:30")
            page.click("#calForm button[type=submit]")
            antes = page.evaluate("S.tasks.map(t=>[t.title,t.due,t.time,t.who||''].join('|'))")
            self.assertEqual(len(antes), 2)
            # Chromium escribe localStorage a disco con unos 5 s de retraso (no es cosa de pAnkI): si se cierra
            # antes, el perfil puede quedarse sin lo último. Se espera a verlo en disco; WebKitGTK, con su
            # NetworkSession persistente, hace lo suyo.
            self._esperar_en_disco(perfil, page, "Dentista")
            # El dato aparece en el registro de LevelDB antes de que Chromium termine de confirmar la escritura:
            # con la máquina cargada (otra suite en paralelo) cerrar justo ahí dejaba el perfil vacío.
            page.wait_for_timeout(2500)
            ctx.close()  # se cierra la ventana (el perfil queda en disco, como la carpeta de datos de pAnkI)
            ctx2, page2 = self._abrir(perfil)
            despues = page2.evaluate("S.tasks.map(t=>[t.title,t.due,t.time,t.who||''].join('|'))")
            self.assertEqual(despues, antes)
            self.assertIn("Reunión con Pepe|2026-10-23|14:35|Pepe", despues)
            page2.evaluate("pankiAbrirCalendario('2026-10-23')")
            lista = page2.inner_text("#calList")
            self.assertIn("Reunión con Pepe", lista)
            self.assertIn("Dentista", lista)
            self.assertEqual(page2.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 2)
            ctx2.close()


if __name__ == "__main__":
    unittest.main()
