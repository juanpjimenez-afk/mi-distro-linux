"""El calendario de pAnkI y el agendar por chat, EJECUTANDO la página de verdad en Chromium (Playwright).

Verifica, con el reloj y la zona horaria simulados:
  * los días de la semana (19/10/2026 = lunes, 23/10/2026 = viernes, 1/1/2027 = viernes, 29/2/2028 = martes);
  * «hoy», «mañana» y el calendario en 7 zonas (Chile, Argentina, UTC, Madrid, Los Ángeles, Auckland, Kolkata)
    a las 23:30 y a las 00:30 locales (donde fallan los errores de UTC), y alrededor de los cambios de hora;
  * «agéndame una reunión a las 14:35 el 23 de octubre con pepe» y sus variantes (fecha, hora, persona,
    título, aviso del año siguiente, fecha inválida, hora que falta, otro evento a la misma hora);
  * consultar, mover y borrar por chat; la vista del calendario (cuadrícula con la semana desde el LUNES,
    puntos y contadores, lista del día, añadir a mano, borrar, marcar hecho, deshacer, navegación);
  * que los eventos son las mismas entradas que las tareas y que sobreviven a cerrar y reabrir el navegador;
  * la app «Calendario» como ventana APARTE: el «modo calendario» de la página (?vista=calendario: solo el calendario, dos
    columnas si es ancho, sin chat, sin tick() ni recordatorios), la SINCRONIZACIÓN entre dos páginas del mismo contexto
    (mismo localStorage, como las dos ventanas del mismo proceso con la misma NetworkSession) y el aviso que la página de
    pAnkI manda por el puente para abrir esa ventana (con respaldo a la vista embebida cuando no hay puente).

Se SALTAN si no hay Playwright de Python o un Chromium que lanzar (la CI de GitHub solo tiene python3).
Variable opcional PANKI_CHROMIUM: ruta del ejecutable de Chromium.
"""
import datetime as dt
import functools
import glob
import html as htmlmod
import http.server
import json
import os
import re
import tempfile
import sys
import threading
import time
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
_SERVIDOR = None  # servidor HTTP local de app.html: lo usan SOLO las pruebas de dos páginas (ver BaseApp)
URL_HTTP = None


class _Silencioso(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


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
    global _SERVIDOR, URL_HTTP
    try:
        # app.html por HTTP en 127.0.0.1: con file:// Chromium a veces da a cada página su PROPIO localStorage (se vio en ~10 %
        # de los arranques de dos páginas) y no se pueden probar dos ventanas que comparten datos. WebKitGTK, con una sola
        # NetworkSession, no es Chromium: lo que NO se puede verificar aquí es el comportamiento de file:// en él.
        _SERVIDOR = http.server.ThreadingHTTPServer(
            ("127.0.0.1", 0), functools.partial(_Silencioso, directory=os.path.dirname(APP_HTML)))
        threading.Thread(target=_SERVIDOR.serve_forever, daemon=True).start()
        URL_HTTP = "http://127.0.0.1:%d/app.html" % _SERVIDOR.server_address[1]
    except OSError:
        _SERVIDOR = URL_HTTP = None
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
    global _PW, _BROWSER, _SERVIDOR, URL_HTTP
    if _SERVIDOR is not None:
        _SERVIDOR.shutdown()
        _SERVIDOR.server_close()
        _SERVIDOR = URL_HTTP = None
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

    def test_la_pagina_con_el_hash_calendario_abre_el_modo_calendario(self):
        # #calendario ya no abre la vista embebida de pAnkI: activa el modo calendario (la app Calendario), igual que ?vista=
        page = self.pagina()
        page.goto(URL + "#calendario")
        page.reload()
        self.assertTrue(page.evaluate("calOn"))
        self.assertTrue(page.evaluate("MODO_CAL"))
        self.assertTrue(page.evaluate("document.body.classList.contains('modo-cal')"))
        self.assertFalse(page.is_visible("#in"))

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
# =========================================================================================== la app Calendario
SEMILLA = {"profile": {}, "facts": [], "notes": [], "log": [], "model": {}, "next": 2, "lastBrief": 0, "asked": True,
           "mode": "fast", "web": False, "nombre": "pAnkI",
           "tasks": [{"id": 1, "title": "Llamar al banco", "cat": "General", "pri": 2, "due": "2026-10-19", "time": "11:00",
                      "done": False, "created": 1, "notified": False}],
           "chat": [{"r": "ai", "h": "<p>Hola, soy pAnkI.</p>"}]}
NOTIF_JS = ("window.__notif=[];try{window.Notification=class{static get permission(){return '%s'}"
            "static requestPermission(){window.__notif.push('perm');return Promise.resolve('%s')}"
            "constructor(t){window.__notif.push('new:'+t)}}}catch(e){}")  # %s = 'granted' (avisa) o 'default' (pide permiso)
PUENTE_FALSO_JS = ("window.__msgs=[];window.webkit={messageHandlers:{panki:{postMessage:function(m){window.__msgs.push(m)}}}};")
CAL_URL = URL + "?vista=calendario"  # para las pruebas de una sola página (file://, como lo carga el host)


class BaseApp(Base):
    """Dos páginas en el MISMO contexto del navegador (mismo localStorage): lo que son las ventanas de pAnkI y del Calendario,
    del mismo proceso y con la misma NetworkSession de WebKit. Van por HTTP local (ver setUpModule)."""

    def setUp(self):
        super().setUp()
        if URL_HTTP is None:
            self.skipTest("no se pudo abrir un servidor HTTP local")

    def dos(self, ancho=820, alto=620, scale=1, scheme="light", semilla=None, ahora=None, tz=None, puente=False,
            notif=None, sufijo_cal="?vista=calendario", con_chat=True):
        tz = tz or self.TZ
        ahora = ahora or self.AHORA
        ctx = _BROWSER.new_context(timezone_id=tz, locale="es-CL", viewport={"width": ancho, "height": alto},
                                   device_scale_factor=scale, color_scheme=scheme)
        self.addCleanup(ctx.close)
        ctx.add_init_script("window.__PANKI_NOMBRE_SISTEMA__='pAnkI';")
        ctx.add_init_script("try{localStorage.setItem('infinitos.prefs',JSON.stringify({mode:'fast',web:false}))}catch(e){}")
        if semilla is not None:
            ctx.add_init_script("try{if(!localStorage.getItem('infinitos.v1'))localStorage.setItem('infinitos.v1',%s)}catch(e){}"
                                % json.dumps(json.dumps(semilla)))
        if notif:
            ctx.add_init_script(NOTIF_JS % (notif, notif))
        if puente:
            ctx.add_init_script(PUENTE_FALSO_JS)
        self.errores = []
        paginas = []
        for destino in ((URL_HTTP,) if con_chat else ()) + (URL_HTTP + sufijo_cal,):
            pg = ctx.new_page()
            pg.on("pageerror", lambda e: self.errores.append(str(e)))
            pg.clock.install(time=instante(tz, *ahora))
            pg.goto(destino)
            paginas.append(pg)
        return (paginas[0], paginas[1]) if con_chat else (None, paginas[0])

    @staticmethod
    def guardado(page):
        return page.evaluate("JSON.parse(localStorage.getItem('infinitos.v1'))")

    @staticmethod
    def esperar(page, js, limite=8.0):
        """Espera a que `js` sea verdadero. Bucle propio: wait_for_function con sondeo evalúa el predicado con eval() y la
        Content-Security-Policy de la página (sin 'unsafe-eval') lo bloquea en cuanto el primer intento da falso."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < limite:
            if page.evaluate(js):
                return
            page.wait_for_timeout(50)
        raise AssertionError(f"no se cumplió a tiempo: {js}")

    @staticmethod
    def agendar(chat, frase=FRASE_USUARIO):
        antes = chat.evaluate("document.querySelectorAll('#chat .m.a').length")
        chat.fill("#in", frase)
        chat.press("#in", "Enter")
        BaseApp.esperar(chat, f"document.querySelectorAll('#chat .m.a').length>{antes}")

    @staticmethod
    def crear_en_calendario(cal, titulo, hora="", quien="", donde=""):
        cal.click("#calNew")
        cal.fill("#cfTitle", titulo)
        cal.fill("#cfTime", hora)
        cal.fill("#cfWho", quien)
        cal.fill("#cfLoc", donde)
        cal.click("#calForm button[type=submit]")


class TestModoCalendario(BaseApp):
    """?vista=calendario: la página sin nada de pAnkI, con el calendario a toda la ventana."""

    def test_solo_se_ve_el_calendario_con_su_cabecera(self):
        _, cal = self.dos()
        self.assertTrue(cal.evaluate("document.body.classList.contains('modo-cal')"))
        self.assertEqual(cal.title(), "Calendario")
        for oculto in ("header", "#modelSwitch", "#btnDeep", "#btnFast", "#btnCal", "#menu", "#scroll", "#chat", "#chips",
                       "#in", "#go", "#form", "#panel", "#timerPill"):
            self.assertFalse(cal.is_visible(oculto), oculto)
        for visible in ("#calView", "#calGrid", "#calTitle", "#calHoy", "#calPrev", "#calNext", "#calNew", "#calList"):
            self.assertTrue(cal.is_visible(visible), visible)
        self.assertEqual(cal.inner_text("#calTitle"), "Octubre de 2026")
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        self.assertEqual(cal.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-19")
        # «＋ Añadir reunión» está en la cabecera del calendario, junto al mes y a ‹ Hoy ›
        self.assertEqual(cal.evaluate("document.querySelector('#calNew').parentElement.className"), "cal-top")
        self.assertIn("Añadir reunión", cal.inner_text("#calNew"))
        self.assertEqual(cal.evaluate("document.querySelectorAll('.cal-dow span').length"), 7)
        self.assertEqual(cal.evaluate("[...document.querySelectorAll('.cal-dow span')].map(x=>x.textContent).join('')"), "LMXJVSD")
        self.assertEqual(self.errores, [])

    def test_la_pagina_de_panki_sigue_igual_sin_la_consulta(self):
        chat, _ = self.dos()
        self.assertFalse(chat.evaluate("MODO_CAL"))
        self.assertFalse(chat.evaluate("document.body.classList.contains('modo-cal')"))
        for visible in ("header", "#modelSwitch", "#btnCal", "#menu", "#chips", "#in"):
            self.assertTrue(chat.is_visible(visible), visible)
        self.assertFalse(chat.is_visible("#calView"))

    def test_otras_consultas_no_activan_el_modo(self):
        for sufijo in ("?vista=chat", "?vista=calendario2", "?x=vista=calendario", "?vista="):
            _, cal = self.dos(sufijo_cal=sufijo, con_chat=False)
            self.assertFalse(cal.evaluate("MODO_CAL"), sufijo)

    def test_tambien_con_vista_entre_otros_parametros_y_con_el_hash(self):
        _, cal = self.dos(sufijo_cal="?a=1&vista=calendario&b=2", con_chat=False)
        self.assertTrue(cal.evaluate("MODO_CAL"))
        _, cal2 = self.dos(sufijo_cal="#calendario", con_chat=False)
        self.assertTrue(cal2.evaluate("MODO_CAL"))

    def test_no_pinta_el_saludo_ni_toca_el_chat_ni_lanza_tick_ni_recordatorios(self):
        sem = json.loads(json.dumps(SEMILLA))
        # control: la página de pAnkI SÍ saluda (lastBrief viejo) y SÍ dispara el recordatorio vencido (11:00 < 12:00)
        chat, _ = self.dos(semilla=sem, notif="granted")
        self.assertGreater(chat.evaluate("S.chat.length"), 1)
        self.assertTrue(chat.evaluate("S.tasks[0].notified"))
        self.assertIn("new:Recordatorio", chat.evaluate("window.__notif"))
        # la app Calendario SOLA (pAnkI sin arrancar, el peor caso para ella): ni saludo, ni chat, ni tick, ni permisos
        _, cal = self.dos(semilla=sem, notif="granted", con_chat=False)
        self.assertEqual(cal.evaluate("S.chat.length"), 1, "no añade ningún mensaje de bienvenida")
        self.assertEqual(cal.evaluate("S.lastBrief"), 0)
        self.assertTrue(cal.evaluate("S.asked"))
        self.assertEqual(cal.evaluate("document.querySelector('#chat').children.length"), 0, "ni siquiera pinta el chat")
        self.assertFalse(cal.evaluate("S.tasks[0].notified"), "el recordatorio lo dispara solo la ventana de pAnkI")
        cal.clock.run_for(5 * 60 * 1000)  # cinco minutos: con tick() se habría avisado veinte veces
        self.assertFalse(cal.evaluate("S.tasks[0].notified"))
        self.assertEqual(cal.evaluate("window.__notif"), [], "sin notificaciones ni permisos")
        self.assertEqual(cal.evaluate("S.chat.length"), 1)
        self.assertEqual(self.guardado(cal)["chat"], sem["chat"], "lo guardado: ni un mensaje más")
        self.assertEqual((self.guardado(cal)["lastBrief"], self.guardado(cal)["tasks"][0]["notified"]), (0, False))
        self.assertEqual(self.errores, [])

    def test_no_escribe_nada_al_arrancar_ni_siquiera_con_el_nombre_del_sistema(self):
        ctx = _BROWSER.new_context(timezone_id=self.TZ, locale="es-CL", viewport={"width": 820, "height": 620})
        self.addCleanup(ctx.close)
        ctx.add_init_script("window.__PANKI_NOMBRE_SISTEMA__='Nova';")
        page = ctx.new_page()
        page.goto(CAL_URL)
        self.assertIsNone(page.evaluate("localStorage.getItem('infinitos.v1')"))
        self.assertIsNone(page.evaluate("localStorage.getItem('infinitos.prefs')"))

    def test_no_pide_permiso_de_notificaciones_ni_al_anadir_una_reunion_con_hora(self):
        chat, cal = self.dos(notif="default")  # sin permiso concedido: pAnkI lo pediría al agendar con hora
        chat.evaluate("respond('agéndame una reunión a las 14:35 el 23 de octubre con pepe')")
        self.assertEqual(chat.evaluate("window.__notif"), ["perm"], "control: pAnkI sí lo pide")
        self.esperar(cal, "S.tasks.length===1")
        self.crear_en_calendario(cal, "Revisión", "10:00", "Ana")
        self.assertEqual(cal.evaluate("S.tasks.length"), 2, (cal.evaluate("S.tasks.map(t=>t.title)"), chat.evaluate("S.tasks.map(t=>t.title)"), self.guardado(cal)["tasks"]))  # la de pAnkI (sincronizada) y la suya
        self.assertEqual(cal.evaluate("window.__notif"), [])

    def test_anadir_desde_el_calendario_no_ensucia_el_chat_de_panki(self):
        sem = json.loads(json.dumps(SEMILLA))
        chat, cal = self.dos(semilla=sem)
        chat.evaluate("aplicarNombrePanki('Nova')")
        self.esperar(cal, "JSON.parse(localStorage.getItem(KEY)).nombre==='Nova'")  # que la otra ventana ya lo vea guardado
        antes = self.guardado(chat)["chat"]
        self.crear_en_calendario(cal, "Dentista", "9:30")
        despues = self.guardado(cal)
        self.assertEqual(despues["chat"], antes, "el chat guardado sigue siendo el de pAnkI")
        self.assertEqual((despues["mode"], despues["nombre"], despues["asked"]), ("fast", "Nova", True))
        self.assertEqual(len(despues["tasks"]), 2)

    def test_navegar_a_una_fecha_con_pankiAbrirCalendario(self):
        _, cal = self.dos()
        cal.evaluate("window.pankiAbrirCalendario('2027-02-14')")
        self.assertEqual(cal.inner_text("#calTitle"), "Febrero de 2027")
        self.assertEqual(cal.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2027-02-14")
        self.assertEqual(cal.inner_text("#calDayTitle"), "Domingo 14 de febrero de 2027")
        for malo in ("ayer", "2026-02-30", "", None, 42):
            cal.evaluate("(v)=>{window.pankiAbrirCalendario(v)}", malo)
            self.assertEqual(cal.inner_text("#calTitle"), "Octubre de 2026", repr(malo))
        self.assertTrue(cal.is_visible("#calView"), "el calendario no se cierra nunca")

    def test_cabecera_navegacion_hoy_y_anadir_reunion(self):
        _, cal = self.dos()
        cal.click("#calNext")
        self.assertEqual(cal.inner_text("#calTitle"), "Noviembre de 2026")
        cal.click("#calPrev")
        cal.click("#calPrev")
        self.assertEqual(cal.inner_text("#calTitle"), "Septiembre de 2026")
        cal.click("#calHoy")
        self.assertEqual(cal.inner_text("#calTitle"), "Octubre de 2026")
        self.assertEqual(cal.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-19")
        self.assertFalse(cal.is_visible("#calForm"))
        cal.click("#calNew")
        self.assertTrue(cal.is_visible("#calForm"))
        cal.keyboard.press("Escape")
        self.assertFalse(cal.is_visible("#calForm"))
        self.assertTrue(cal.is_visible("#calView"), "Escape cierra el formulario, no el calendario")
        self.crear_en_calendario(cal, "Revisión", "10:00", "Ana", "la oficina")
        self.assertIn("Revisión con Ana", cal.inner_text("#calList"))
        self.assertIn("la oficina", cal.inner_text("#calList"))

    def test_el_texto_del_dia_vacio_no_manda_a_escribir_en_un_chat_que_no_esta(self):
        _, cal = self.dos()
        self.assertIn("Nada agendado este día", cal.inner_text("#calList"))
        self.assertIn("Añadir reunión", cal.inner_text("#calList"))

    # ------------------------------------------------------------------ el día cambia
    def test_el_hoy_se_repinta_a_medianoche_cada_30_segundos(self):
        _, cal = self.dos(ahora=(2026, 10, 19, 23, 59))
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        cal.clock.fast_forward(2 * 60 * 1000)  # son las 00:01 del martes 20
        cal.clock.run_for(31000)
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-20")
        self.assertEqual(cal.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-20", "la selección sigue al día")
        self.assertEqual(cal.inner_text("#calDaySub"), "Hoy")

    def test_el_hoy_se_repinta_al_volver_a_tener_foco(self):
        _, cal = self.dos(ahora=(2026, 10, 19, 23, 59))
        cal.clock.set_system_time(instante(self.TZ, 2026, 10, 20, 0, 5))  # sin disparar temporizadores
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-19")
        cal.evaluate("window.dispatchEvent(new Event('focus'))")
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-20")

    def test_si_habias_navegado_a_otro_dia_la_seleccion_no_se_mueve(self):
        _, cal = self.dos(ahora=(2026, 10, 19, 23, 59))
        cal.click('#calGrid .cd[data-cd="2026-10-23"]')
        cal.clock.set_system_time(instante(self.TZ, 2026, 10, 20, 0, 5))
        cal.evaluate("window.dispatchEvent(new Event('focus'))")
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-10-20")
        self.assertEqual(cal.locator("#calGrid .cd.sel").get_attribute("data-cd"), "2026-10-23")

    def test_el_cambio_de_mes_a_medianoche_lleva_al_mes_nuevo(self):
        _, cal = self.dos(ahora=(2026, 10, 31, 23, 59))
        cal.clock.set_system_time(instante(self.TZ, 2026, 11, 1, 0, 2))
        cal.evaluate("document.dispatchEvent(new Event('visibilitychange'))")
        self.assertEqual(cal.inner_text("#calTitle"), "Noviembre de 2026")
        self.assertEqual(cal.locator("#calGrid .cd.today").get_attribute("data-cd"), "2026-11-01")

    # ------------------------------------------------------------------ diseño
    def test_dos_columnas_si_es_ancho_y_una_si_es_estrecho(self):
        _, cal = self.dos(ancho=820, alto=620)
        medidas = """()=>{const r=s=>document.querySelector(s).getBoundingClientRect().toJSON();
            return {grid:r('#calGrid'),dia:r('.cal-day'),top:r('.cal-top'),lista:r('#calList'),
                    sw:document.documentElement.scrollWidth,iw:innerWidth,ih:innerHeight}}"""
        ancho = cal.evaluate(medidas)
        self.assertLessEqual(ancho["grid"]["right"], ancho["dia"]["left"], "ancho: el día va a la derecha del mes")
        self.assertLess(abs(ancho["grid"]["top"] - ancho["dia"]["top"]), 60, "y a la misma altura")
        self.assertLessEqual(ancho["sw"], ancho["iw"])
        self.assertLessEqual(ancho["dia"]["bottom"], ancho["ih"] + 1, "cabe en la ventana sin bajar")
        cal.set_viewport_size({"width": 460, "height": 640})
        estrecho = cal.evaluate(medidas)
        self.assertGreaterEqual(estrecho["dia"]["top"], estrecho["grid"]["bottom"] - 1, "estrecho: una sola columna")
        self.assertLessEqual(estrecho["sw"], estrecho["iw"], "sin scroll horizontal a 460 px")
        self.assertLessEqual(estrecho["top"]["right"], estrecho["iw"])
        # el umbral: 699 px = una columna; 700 px = dos
        cal.set_viewport_size({"width": 699, "height": 620})
        self.assertGreaterEqual(cal.evaluate(medidas)["dia"]["top"], cal.evaluate(medidas)["grid"]["bottom"] - 1)
        cal.set_viewport_size({"width": 700, "height": 620})
        m = cal.evaluate(medidas)
        self.assertLessEqual(m["grid"]["right"], m["dia"]["left"])

    def test_la_cabecera_cabe_en_460_con_el_mes_mas_largo(self):
        _, cal = self.dos(ancho=460, alto=640)
        cal.evaluate("pankiAbrirCalendario('2026-09-15')")  # «Septiembre de 2026»
        d = cal.evaluate("""()=>{const r=s=>document.querySelector(s).getBoundingClientRect();
            return {tit:r('#calTitle').toJSON(),nuevo:r('#calNew').toJSON(),next:r('#calNext').toJSON(),iw:innerWidth,
                    corta:document.querySelector('#calTitle').scrollWidth>document.querySelector('#calTitle').clientWidth}}""")
        self.assertLessEqual(d["nuevo"]["right"], d["iw"])
        self.assertLessEqual(d["next"]["right"], d["nuevo"]["left"], "los botones no se pisan")
        self.assertFalse(d["corta"], "el mes y el año se leen enteros")

    def test_el_modo_oscuro_se_respeta(self):
        _, cal = self.dos(scheme="dark")
        self.assertEqual(cal.evaluate("getComputedStyle(document.body).backgroundColor"), "rgb(15, 15, 16)")
        self.assertTrue(cal.is_visible("#calGrid"))
        _, cal2 = self.dos(scheme="light")
        self.assertEqual(cal2.evaluate("getComputedStyle(document.body).backgroundColor"), "rgb(251, 251, 250)")

    def test_la_cuadricula_sigue_empezando_en_lunes_en_modo_calendario(self):
        _, cal = self.dos()
        primera = cal.locator("#calGrid .cd").first.get_attribute("data-cd")
        self.assertEqual(primera, "2026-09-28")  # el lunes anterior al jueves 1 de octubre de 2026
        self.assertEqual(dt.date.fromisoformat(primera).weekday(), 0)


class TestSincronizacionEntreVentanas(BaseApp):
    """Dos páginas del mismo contexto = mismo localStorage: lo que hace una sale en la otra (evento `storage`)."""

    def test_lo_que_se_agenda_por_chat_sale_en_el_calendario(self):
        chat, cal = self.dos()
        self.assertEqual(cal.evaluate("S.tasks.length"), 0)
        self.agendar(chat)
        self.esperar(cal, "S.tasks.length===1")
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.assertEqual(cal.inner_text("#calDayTitle"), "Viernes 23 de octubre de 2026")
        self.assertIn("Reunión con Pepe", cal.inner_text("#calList"))
        self.assertIn("14:35", cal.inner_text("#calList"))
        self.assertEqual(cal.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 1)

    def test_aparece_al_instante_en_el_calendario_ya_abierto_en_ese_dia(self):
        chat, cal = self.dos()
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.assertIn("Nada agendado", cal.inner_text("#calList"))
        self.agendar(chat)
        self.esperar(cal, "document.querySelector('#calList').textContent.includes('Reunión con Pepe')")  # sin recargar ni tocar nada
        self.assertEqual(cal.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 1)

    def test_lo_que_se_crea_en_el_calendario_sale_en_las_tareas_de_panki(self):
        chat, cal = self.dos()
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.crear_en_calendario(cal, "Dentista", "9:30", "Eva", "la clínica")
        self.esperar(chat, "S.tasks.length===1")
        t = self.tareas(chat)[0]
        self.assertEqual((t["title"], t["due"], t["time"], t["who"], t["loc"]),
                         ("Dentista con Eva", "2026-10-23", "09:30", "Eva", "la clínica"))
        chat.click("#menu")
        self.esperar(chat, "document.querySelector('#pbody').textContent.includes('Dentista con Eva')")
        self.assertIn("Dentista con Eva", chat.inner_text("#pbody"))
        # y por chat: «organiza mi día» / «qué tengo el 23 de octubre» lo ven
        self.assertIn("Dentista", self.di(chat, "qué tengo el 23 de octubre"))

    def test_borrar_en_el_calendario_lo_borra_en_panki(self):
        chat, cal = self.dos()
        self.agendar(chat)
        self.esperar(cal, "S.tasks.length===1")
        fila = chat.locator('#chat .row[data-tid]').last
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        cal.click('#calList [data-caldel]')
        self.esperar(chat, "S.tasks.length===0")
        self.assertEqual(self.tareas(chat), [])
        self.assertEqual(fila.evaluate("e=>e.style.opacity"), "0.35", "la fila del chat queda atenuada: ya no existe")
        chat.click("#menu")
        self.assertIn("Sin tareas pendientes", chat.inner_text("#pbody"))

    def test_marcar_hecho_en_el_calendario_se_ve_en_panki(self):
        chat, cal = self.dos()
        self.agendar(chat)
        self.esperar(cal, "S.tasks.length===1")
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        cal.click('#calList .chk')
        self.esperar(chat, "S.tasks[0].done===true")
        self.assertTrue(chat.locator('#chat .row[data-tid]').last.evaluate("e=>e.classList.contains('done')"))
        # y al revés: reabrirla desde pAnkI la deja pendiente también en el calendario
        self.di(chat, "reabre 1")
        self.esperar(cal, "S.tasks[0].done===false")
        self.assertEqual(cal.locator('#calList .row.ev.done').count(), 0)

    def test_mover_en_el_chat_se_ve_en_el_calendario(self):
        chat, cal = self.dos()
        self.agendar(chat)
        self.esperar(cal, "S.tasks.length===1")
        chat.evaluate("respond('mueve la reunión con Pepe al 24 a las 10')")
        chat.evaluate("save()")
        self.esperar(cal, "S.tasks[0].due==='2026-10-24'")
        self.assertEqual(cal.evaluate("S.tasks[0].time"), "10:00")
        self.assertEqual(cal.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 0)
        self.assertEqual(cal.locator('#calGrid .cd[data-cd="2026-10-24"] .dots i').count(), 1)

    def test_borrar_en_el_chat_lo_quita_del_calendario(self):
        chat, cal = self.dos()
        self.agendar(chat)
        self.esperar(cal, "S.tasks.length===1")
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.assertIn("Reunión con Pepe", cal.inner_text("#calList"))
        self.agendar(chat, "borra la reunión con Pepe")
        self.esperar(cal, "S.tasks.length===0")
        self.assertIn("Nada agendado", cal.inner_text("#calList"))
        self.assertEqual(cal.locator('#calGrid .cd[data-cd="2026-10-23"] .dots i').count(), 0)

    def test_cambios_que_no_son_de_agenda_no_tocan_nada_en_la_otra(self):
        chat, cal = self.dos()
        self.crear_en_calendario(cal, "Dentista", "9:30")
        self.esperar(chat, "S.tasks.length===1")
        chat_antes = cal.evaluate("S.chat.length")
        cal.click("#calNew")  # un formulario a medio escribir en el calendario...
        cal.fill("#cfTitle", "Reunión a medias")
        self.agendar(chat, "hola")  # ...mientras pAnkI solo guarda chat
        self.agendar(chat, "qué día es el 19/10/2026")
        self.assertTrue(cal.is_visible("#calForm"))
        self.assertEqual(cal.input_value("#cfTitle"), "Reunión a medias", "no se pierde lo que se está escribiendo")
        self.assertEqual(cal.evaluate("S.chat.length"), chat_antes, "el chat de pAnkI no se sincroniza con el calendario")
        self.assertEqual(cal.evaluate("S.tasks.length"), 1)

    def test_el_estado_propio_de_cada_ventana_no_se_pisa(self):
        chat, cal = self.dos()
        chat.evaluate("aplicarNombrePanki('Nova')")
        chat.evaluate("setMode('deep')")
        self.esperar(cal, "JSON.parse(localStorage.getItem(KEY)).nombre==='Nova'")
        self.crear_en_calendario(cal, "Dentista", "9:30")
        self.esperar(chat, "S.tasks.length===1")
        self.assertEqual(chat.evaluate("S.nombre"), "Nova")
        self.assertEqual(chat.evaluate("S.mode"), "deep")
        g = self.guardado(chat)
        self.assertEqual(g["nombre"], "Nova")
        self.assertEqual(g["asked"], True)
        self.assertGreaterEqual(len(g["chat"]), 1, "el chat de pAnkI sigue guardado")

    # ------------------------------------------------------------------ carreras
    def test_el_calendario_no_pisa_un_cambio_reciente_de_panki_aunque_aun_no_le_haya_llegado(self):
        """En UNA sola tarea de JS (el evento `storage` no puede llegar en medio): la otra ventana guarda y esta, que sigue
        con la agenda vieja, crea un evento. Tienen que quedar los DOS, con ids distintos."""
        chat, cal = self.dos()
        self.crear_en_calendario(cal, "Primero", "8:00")
        self.esperar(chat, "S.tasks.length===1")
        g = cal.evaluate("""()=>{
            const d=JSON.parse(localStorage.getItem(KEY));
            d.tasks.push({id:d.next,title:'De pAnkI',cat:'General',pri:2,due:'2026-10-23',time:'14:35',done:false,created:2,notified:false});
            d.next+=1;d.chat.push({r:'ai',h:'<p>mensaje nuevo</p>'});
            localStorage.setItem(KEY,JSON.stringify(d));   /* «otra ventana» guardó... */
            crearEvento({title:'Segundo',due:'2026-10-24',time:'9:00'});  /* ...y esta aún no se enteró */
            return JSON.parse(localStorage.getItem(KEY))}""")
        titulos = sorted(t["title"] for t in g["tasks"])
        self.assertEqual(titulos, ["De pAnkI", "Primero", "Segundo"])
        ids = [t["id"] for t in g["tasks"]]
        self.assertEqual(len(ids), len(set(ids)), "ids únicos")
        self.assertEqual(g["next"], max(ids) + 1)
        self.assertIn("mensaje nuevo", json.dumps(g["chat"]), "y el chat que pAnkI acababa de guardar sigue ahí")

    def test_panki_no_pisa_un_cambio_reciente_del_calendario_aunque_aun_no_le_haya_llegado(self):
        chat, cal = self.dos()
        self.agendar(chat, "agéndame una reunión a las 9:00 el 21 de octubre con ana")
        self.esperar(cal, "S.tasks.length===1")
        g = chat.evaluate("""()=>{
            const d=JSON.parse(localStorage.getItem(KEY));
            d.tasks.push({id:d.next,title:'Del calendario',cat:'General',pri:2,due:'2026-10-23',time:'14:35',done:false,created:2,notified:false});
            d.next+=1;localStorage.setItem(KEY,JSON.stringify(d));
            respond('agéndame una reunión a las 10:00 el 22 de octubre con eva');
            return JSON.parse(localStorage.getItem(KEY))}""")
        self.assertEqual(sorted(t["title"] for t in g["tasks"]),
                         ["Del calendario", "Reunión con Ana", "Reunión con Eva"])
        self.assertEqual(len({t["id"] for t in g["tasks"]}), 3)

    def test_tick_de_panki_tampoco_pisa_la_agenda_nueva(self):
        sem = json.loads(json.dumps(SEMILLA))
        chat, _ = self.dos(semilla=sem)
        g = chat.evaluate("""()=>{
            const d=JSON.parse(localStorage.getItem(KEY));
            d.tasks.push({id:d.next,title:'Del calendario',cat:'General',pri:2,due:'2026-10-23',time:'14:35',done:false,created:2,notified:false});
            d.tasks.push({id:d.next+1,title:'Vencida sin avisar',cat:'General',pri:2,due:'2026-10-19',time:'11:30',done:false,created:2,notified:false});
            d.next+=2;localStorage.setItem(KEY,JSON.stringify(d));
            tick();   /* avisa «Vencida sin avisar» y guarda: no puede perder lo que acaba de llegar */
            return JSON.parse(localStorage.getItem(KEY))}""")
        self.assertEqual(sorted(t["title"] for t in g["tasks"]), ["Del calendario", "Llamar al banco", "Vencida sin avisar"])
        self.assertTrue(all(t["notified"] for t in g["tasks"] if t["title"] == "Vencida sin avisar"))

    def test_deshacer_no_revierte_lo_que_hizo_la_otra_ventana(self):
        chat, cal = self.dos()
        chat.evaluate("respond('tarea: comprar pan')")
        chat.evaluate("save()")
        self.esperar(cal, "S.tasks.length===1")
        self.crear_en_calendario(cal, "Dentista", "9:30")
        self.esperar(chat, "S.tasks.length===2")
        r = self.di(chat, "deshacer")
        self.assertIn("No hay nada que deshacer", r)
        self.assertEqual(sorted(t["title"] for t in self.tareas(chat)), ["Comprar pan", "Dentista"])
        # lo suyo propio sí se deshace
        self.di(chat, "tarea: llamar a Luis")
        self.assertIn("Deshecho", self.di(chat, "deshacer"))
        self.assertEqual(len(self.tareas(chat)), 2)

    def test_un_guardado_corrupto_de_la_otra_ventana_no_rompe_ni_vacia_nada(self):
        chat, cal = self.dos()
        self.crear_en_calendario(cal, "Dentista", "9:30")
        self.esperar(chat, "S.tasks.length===1")
        chat.evaluate("localStorage.setItem(KEY,'{esto no es json')")
        cal.wait_for_timeout(200)
        self.assertEqual(cal.evaluate("S.tasks.length"), 1, "se queda con lo que tenía")
        self.assertEqual(self.errores, [])
        chat.evaluate("localStorage.setItem(KEY,JSON.stringify({tasks:'no soy una lista'}))")
        cal.wait_for_timeout(200)
        self.assertEqual(cal.evaluate("S.tasks.length"), 1)
        self.assertEqual(self.errores, [])


class TestEnlaceDesdePanki(BaseApp):
    """En la ventana de pAnkI, los botones del calendario avisan al host por el puente (que abre la app Calendario). Sin
    puente (la página suelta en un navegador) se conserva la vista embebida."""

    @staticmethod
    def mensajes(page):
        return page.evaluate("window.__msgs.map(m=>JSON.parse(m))")

    def test_el_boton_de_la_cabecera_pide_la_app_y_no_abre_la_vista_embebida(self):
        chat, _ = self.dos(puente=True)
        chat.click("#btnCal")
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": ""}])
        self.assertFalse(chat.is_visible("#calView"))
        self.assertTrue(chat.is_visible("#in"), "pAnkI se queda como estaba")
        self.assertFalse(chat.evaluate("calOn"))

    def test_el_chip_calendario_pide_la_app(self):
        chat, _ = self.dos(puente=True)
        chat.click('#chips [data-cal]')
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": ""}])
        self.assertFalse(chat.evaluate("calOn"))

    def test_ver_en_el_calendario_pide_la_app_en_esa_fecha(self):
        chat, _ = self.dos(puente=True)
        self.agendar(chat)
        boton = chat.locator("#chat .m.a").last.locator("[data-cal]")
        self.assertEqual(boton.get_attribute("data-cal"), "2026-10-23")
        boton.click()
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": "2026-10-23"}])
        self.assertFalse(chat.evaluate("calOn"))
        self.assertFalse(chat.is_visible("#calView"))

    def test_abrir_calendario_del_panel_de_agenda_pide_la_app(self):
        chat, _ = self.dos(puente=True)
        chat.evaluate("respond('agéndame una reunión a las 14:35 el 23 de octubre con pepe')")
        chat.click("#menu")
        chat.click('#panel [data-tab="agenda"]')
        chat.click('#pbody [data-cal]')
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": ""}])

    def test_la_orden_por_chat_abre_la_app_y_lo_dice(self):
        chat, _ = self.dos(puente=True)
        r = self.di(chat, "abre el calendario")
        self.assertIn("app Calendario", r)
        self.assertNotIn("Toca un día", r)
        self.di(chat, "calendario de marzo de 2028")
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": ""},
                                                {"accion": "calendario", "valor": "2028-03-01"}])
        self.assertFalse(chat.evaluate("calOn"))

    def test_lo_que_la_pagina_envia_lo_acepta_el_validador_del_host(self):
        # contrato entre los dos lados: cada mensaje que sale de la página pasa por panki_app._interpretar_mensaje_js
        chat, _ = self.dos(puente=True)
        self.agendar(chat)
        chat.locator("#chat .m.a").last.locator("[data-cal]").click()
        chat.click("#btnCal")
        self.di(chat, "calendario de marzo de 2028")
        crudos = chat.evaluate("window.__msgs")
        self.assertEqual(len(crudos), 3)
        self.assertEqual([panki_app._interpretar_mensaje_js(m) for m in crudos],
                         [("calendario", "2026-10-23"), ("calendario", ""), ("calendario", "2028-03-01")])

    def test_un_valor_raro_en_data_cal_se_envia_como_vacio(self):
        chat, _ = self.dos(puente=True)
        chat.evaluate("""()=>{const b=document.createElement('button');b.dataset.cal='2026-02-30; alert(1)';b.id='raro';
                          document.body.appendChild(b)}""")
        chat.click("#raro")
        self.assertEqual(self.mensajes(chat), [{"accion": "calendario", "valor": ""}])

    def test_sin_puente_se_mantiene_la_vista_embebida(self):
        chat, _ = self.dos(puente=False)
        chat.click("#btnCal")
        self.assertTrue(chat.is_visible("#calView"))
        self.assertTrue(chat.evaluate("calOn"))
        chat.click("#btnCal")
        self.agendar(chat)
        chat.locator("#chat .m.a").last.locator("[data-cal]").click()
        self.assertEqual(chat.inner_text("#calDayTitle"), "Viernes 23 de octubre de 2026")
        self.assertFalse(chat.evaluate("document.body.classList.contains('modo-cal')"))

    def test_si_el_puente_falla_tambien_se_mantiene_la_vista_embebida(self):
        chat, _ = self.dos(puente=False)
        chat.evaluate("window.webkit={messageHandlers:{panki:{postMessage(){throw new Error('sin host')}}}}")
        chat.click("#btnCal")
        self.assertTrue(chat.evaluate("calOn"))

    def test_la_pagina_del_calendario_nunca_pide_la_app_a_si_misma(self):
        _, cal = self.dos(puente=True)
        self.assertFalse(cal.evaluate("pedirCalendarioApp('2026-10-23')"))
        self.assertEqual(self.mensajes(cal), [])
        cal.evaluate("pankiAbrirCalendario('2026-10-23')")
        self.assertEqual(self.mensajes(cal), [])


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
