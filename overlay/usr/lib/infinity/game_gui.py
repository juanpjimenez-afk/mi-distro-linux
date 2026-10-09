#!/usr/bin/env python3
"""Juegos: ventana compacta de Infinity OS para el modo juego y GE-Proton.

Tkinter estándar (viene con python3-tk; no hace falta customtkinter ni nada de pip).

Ligera de verdad: no tiene temporizadores en reposo. El estado (el LED) se mira al abrir, al
recuperar el foco y tras cada acción. Solo mientras hay una tarea en curso (una descarga, una
activación) se consulta una cola cada 100 ms, y en cuanto termina esa consulta se detiene. Con
el juego en marcha, la ventana no hace nada en segundo plano. La lista de procesos y su % de CPU
se calculan solo al pulsar «Actualizar procesos».

Las acciones de root se lanzan con `sudo -n` (la sesión en vivo lo permite sin contraseña). Si
sudo pide contraseña, se abre una terminal para pedirla ahí.

Nunca se queda en silencio: si la ventana no puede abrirse, o falla una acción, se avisa con una
notificación (o con una terminal si no hay servicio de notificaciones) y se anota en
~/.cache/infinity/juegos.log. El lanzador descarta la salida de las aplicaciones, así que un
error que solo saliera por stderr no lo vería nadie.
"""
import functools
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
import traceback
import tkinter as tk
from tkinter import messagebox, ttk

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)  # game_environment.py y game_optimizer.py viven junto a este archivo
import game_environment as env  # noqa: E402
import game_optimizer as opt  # noqa: E402

COMANDO = "infinity-juegos"
LOGOS = ("/usr/share/infinity/game-optimizer.png",
         # Desarrollo: overlay/usr/lib/infinity -> repo/branding
         os.path.join(AQUI, "..", "..", "..", "..", "branding", "game-optimizer.png"))
MB = 1024 * 1024
NUM_PROCESOS = 50  # cuántos procesos se listan (los que más CPU usan)
# Prefijo de la carpeta oculta a la que se renombra una GE-Proton antes de borrarla
PREFIJO_ELIMINADA = ".eliminando-"

COLORES = {
    "fondo": "#121419", "panel": "#1b1e26", "borde": "#2a2f3b", "texto": "#e7e9f0",
    "secundario": "#8a92a6", "verde": "#34d399", "rojo": "#f87171", "acento": "#5b7cfa",
    "acento_activo": "#7691ff", "peligro": "#a84848", "peligro_activo": "#c05656",
    "boton": "#262a35", "boton_activo": "#313745", "deshabilitado": "#1d2029",
}
FUENTE = ("Inter", 10)
FUENTE_NEGRITA = ("Inter", 10, "bold")
FUENTE_TITULO = ("Inter", 14, "bold")
FUENTE_PEQUENA = ("Inter", 9)


# --------------------------------------------------------------------------- lógica (sin ventana)
def argv_privilegiado(orden):
    """Orden de root: sudo sin pedir contraseña."""
    return ["sudo", "-n", COMANDO, *orden]


def argv_terminal_sudo(ordenes):
    """Abre una terminal que pide la contraseña de sudo y ejecuta las órdenes (cada una es un texto)."""
    cadena = " && ".join(f"sudo {COMANDO} {o}" for o in ordenes)
    return ["foot", "-T", "Juegos: permiso", "sh", "-c",
            f"{cadena}; echo; printf 'Pulsa Enter para cerrar... '; read -r _"]


def sudo_sin_contrasena():
    """True si la orden real (sudo -n infinity-juegos ...) está permitida sin contraseña."""
    try:
        r = subprocess.run(["sudo", "-n", "-l", COMANDO], capture_output=True, timeout=10)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def hay_cpufreq():
    """True si el equipo deja cambiar el governor de la CPU. No escribe nada."""
    return opt.tiene_control_frecuencia()


def leer_estado():
    """Estado del modo juego. No necesita root (solo lee sysfs y el fichero de estado).

    Nunca lanza: si algo no encaja, devuelve error=True con el detalle para mostrarlo.
    """
    try:
        estado = opt.estado()
        if not isinstance(estado, dict):
            raise TypeError("la respuesta del optimizador no es un diccionario")
        return estado
    except Exception as e:  # la ventana no debe caerse por un estado raro
        detalle = str(e) if isinstance(e, opt.OptimizadorError) else f"error inesperado: {e}"
        return {"activado": False, "governors": {}, "procesos": [], "error": True, "detalle": detalle}


def resumen_cpu(estado):
    """Línea corta bajo el LED: governor de la CPU y cuántos procesos están optimizados."""
    if estado.get("error"):
        return estado.get("detalle") or "No se pudo leer el estado."
    valores = sorted(set(estado.get("governors", {}).values()))
    partes = [f"CPU {', '.join(valores)}" if valores else "CPU sin control de frecuencia"]
    n = len(estado.get("procesos", []))
    if n:
        s = "" if n == 1 else "s"
        partes.append(f"{n} proceso{s} optimizado{s}")
    return " · ".join(partes)


def versiones_proton(directorio=None):
    """GE-Proton instaladas, la más nueva primero. Sin carpeta legible: lista vacía."""
    d = directorio or env.directorio_compatibilidad()
    try:
        nombres = os.listdir(d)
    except OSError:
        return []
    validas = [n for n in nombres if n.startswith("GE-Proton") and os.path.isdir(os.path.join(d, n))]
    return sorted(validas, key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)), reverse=True)


def ruta_proton_segura(nombre, directorio=None):
    """Ruta de una versión instalada, o ErrorEntorno si el nombre intenta salirse del directorio
    o si es un enlace simbólico (aunque apunte dentro)."""
    d = directorio or env.directorio_compatibilidad()
    if not nombre or os.sep in nombre or nombre in (".", "..") or not nombre.startswith("GE-Proton"):
        raise env.ErrorEntorno("nombre de versión no válido.")
    ruta = os.path.join(d, nombre)
    if os.path.islink(ruta):
        raise env.ErrorEntorno("la versión es un enlace, no una carpeta; no se toca.")
    if os.path.dirname(os.path.realpath(ruta)) != os.path.realpath(d):
        raise env.ErrorEntorno("la versión no está en la carpeta de Steam.")
    return ruta


def limpiar_restos_eliminacion(directorio=None):
    """Borra las carpetas a medio eliminar que dejó una eliminación interrumpida. Devuelve cuántas."""
    d = directorio or env.directorio_compatibilidad()
    try:
        nombres = os.listdir(d)
    except OSError:
        return 0
    borradas = 0
    for nombre in nombres:
        ruta = os.path.join(d, nombre)
        if nombre.startswith(PREFIJO_ELIMINADA) and os.path.isdir(ruta) and not os.path.islink(ruta):
            shutil.rmtree(ruta, ignore_errors=True)
            borradas += 1
    return borradas


def _ejecutar_root(orden):
    """Lanza una orden de root con sudo -n. Devuelve su salida; ErrorEntorno si falla."""
    try:
        r = subprocess.run(argv_privilegiado(orden), capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise env.ErrorEntorno(f"no se pudo ejecutar sudo: {e}")
    if r.returncode != 0:
        detalle = (r.stderr or r.stdout).strip().splitlines()
        raise env.ErrorEntorno(f"'{' '.join(orden)}' falló: {detalle[-1] if detalle else 'sin detalle'}")
    # Los avisos del optimizador salen por stderr aunque la orden haya ido bien: se muestran igual
    return "\n".join(p for p in (r.stdout.strip(), r.stderr.strip()) if p)


def _abrir_terminal(ordenes):
    """Sin sudo sin contraseña: una terminal pide la contraseña y ejecuta las órdenes."""
    try:
        subprocess.Popen(argv_terminal_sudo(ordenes), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        cadena = " && ".join(f"sudo {COMANDO} {o}" for o in ordenes)
        raise env.ErrorEntorno(f"no hay terminal disponible. En una terminal, ejecuta: {cadena}")
    return "sudo pide contraseña: termina en la terminal que se ha abierto y vuelve aquí."


def _nombre_proceso(pid):
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() or "?"
    except OSError:
        return "?"


def procesos_de_usuario(muestra=0.5):
    """Procesos tuyos con su % de CPU medido durante `muestra` segundos, el más activo primero.

    Solo se llama desde «Actualizar procesos», en un hilo de trabajo: no hay muestreo en reposo.
    """
    uid = os.getuid()
    propio = os.getpid()
    hz = os.sysconf("SC_CLK_TCK")

    def tiempos():
        mapa = {}
        for entrada in os.listdir("/proc"):
            if not entrada.isdigit() or int(entrada) == propio:
                continue
            ruta = f"/proc/{entrada}"
            try:
                if os.stat(ruta).st_uid != uid:
                    continue
                with open(f"{ruta}/stat", encoding="utf-8") as f:
                    campos = f.read().rsplit(")", 1)[1].split()
                mapa[int(entrada)] = int(campos[11]) + int(campos[12])  # utime y stime (campos 14 y 15)
            except (OSError, IndexError, ValueError):
                continue
        return mapa

    antes = tiempos()
    time.sleep(muestra)
    despues = tiempos()
    lista = []
    for pid, t1 in despues.items():
        if pid in antes:
            pct = 100.0 * (t1 - antes[pid]) / (hz * muestra)
            lista.append((pid, _nombre_proceso(pid), pct))
    lista.sort(key=lambda p: p[2], reverse=True)
    return lista[:NUM_PROCESOS]


def _limpiar(texto):
    for prefijo in ("[..] ", "[ok] ", "[!!] "):
        if texto.startswith(prefijo):
            return texto[len(prefijo):]
    return texto


# --------------------------------------------------------------------------- avisos fuera de la ventana
REGISTRO = "juegos.log"
# Sin ventana no hay Tk que muestre nada. Se prueba una notificación y, si no hay servicio de
# notificaciones (la ISO no trae ninguno por defecto), una terminal con el aviso que espera a Enter.
SCRIPT_AVISO = 'printf "%s\\n\\n" "$1"; printf "Pulsa Enter para cerrar... "; read -r _'


def ruta_registro():
    """Registro de fallos: ~/.cache/infinity/juegos.log (o $XDG_CACHE_HOME/infinity/juegos.log)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "infinity", REGISTRO)


def registrar(texto):
    """Añade el texto con la hora al registro. Nunca lanza: si no se puede escribir, no queda otro sitio."""
    try:
        ruta = ruta_registro()
        os.makedirs(os.path.dirname(ruta), exist_ok=True)
        with open(ruta, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {texto.rstrip()}\n")
    except OSError:
        pass


def avisar_sin_ventana(texto):
    """Muestra el aviso fuera de la ventana. Devuelve False si no hubo ningún medio para mostrarlo."""
    try:
        r = subprocess.run(["notify-send", "-u", "critical", "-a", "Juegos", "Juegos", texto],
                           capture_output=True, timeout=5)
        if r.returncode == 0:
            return True
    except (OSError, subprocess.SubprocessError):
        pass
    try:
        subprocess.Popen(["foot", "-T", "Juegos: aviso", "sh", "-c", SCRIPT_AVISO, "sh", texto],
                         start_new_session=True, stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return True
    except OSError:
        return False


def fallo_al_abrir(texto, detalle=""):
    """Un fallo que impide abrir la ventana: se anota, se avisa y devuelve 1 (código de salida)."""
    registrar(f"{texto}\n{detalle}" if detalle else texto)
    avisar_sin_ventana(texto)
    return 1


class _InformeGui:
    """Recibe mensajes, progreso y datos del hilo de trabajo y los deja en una cola para la ventana.

    Cancelar: si la ventana pide cancelar, el siguiente aviso de progreso lanza ErrorEntorno. La
    descarga guarda lo ya bajado en un .part; se retoma solo si se vuelve a elegir la misma versión.
    """

    def __init__(self, cola, cancelar):
        self.cola = cola
        self.cancelar = cancelar

    def mensaje(self, texto):
        self.cola.put(("mensaje", _limpiar(texto)))

    def progreso(self, etiqueta, hecho, total, velocidad):
        if self.cancelar.is_set():
            raise env.ErrorEntorno("descarga cancelada; se retomará solo si eliges la misma versión.")
        self.cola.put(("progreso", etiqueta, hecho, total, velocidad))

    def cerrar_progreso(self):
        self.cola.put(("cerrar_progreso",))

    def datos(self, valor):
        self.cola.put(("datos", valor))


def _cargar_logo(ventana):
    for ruta in LOGOS:
        if os.path.isfile(ruta):
            try:
                imagen = tk.PhotoImage(master=ventana, file=ruta)
                return imagen.subsample(max(1, imagen.width() // 64))  # 512 px -> 64 px
            except tk.TclError:
                return None
    return None


# --------------------------------------------------------------------------- ventana
class Juegos(tk.Tk):
    def __init__(self, con_logo=True):
        # className: clase de ventana (WM_CLASS) para la regla de ventana flotante de Hyprland
        super().__init__(className="infinity-juegos")
        self.title("Juegos")
        self.configure(bg=COLORES["fondo"])
        self.resizable(False, False)
        self.minsize(372, 0)  # ancho fijo: la ventana no cambia de tamaño según el texto
        # Las pruebas lo desactivan: una imagen de Tk liberada desde otro hilo aborta el proceso.
        self._logo = _cargar_logo(self) if con_logo else None
        if self._logo is not None:
            self.iconphoto(True, self._logo)
        self._tarea = None  # hilo de trabajo en curso; None = la ventana está inactiva
        self._cola = None
        self._cancelar = threading.Event()
        self._cancelable = False  # solo la descarga se puede interrumpir sin dejar cambios a medias
        self._sondeo = None  # id del after activo: solo existe mientras hay una tarea
        self._activo = False
        self._versiones = []
        self._procesos = []  # (pid, nombre, % CPU) de la última lista
        self._estado_opt = {}
        self._construir()
        limpiar_restos_eliminacion()
        self.bind("<FocusIn>", self._al_enfocar)
        self.protocol("WM_DELETE_WINDOW", self._cerrar)
        self._refrescar()

    # ----------------------------------------------------------------- construcción
    def _construir(self):
        estilo = ttk.Style(self)
        estilo.theme_use("clam")
        estilo.configure("Juegos.Horizontal.TProgressbar", troughcolor=COLORES["panel"],
                         background=COLORES["acento"], bordercolor=COLORES["borde"],
                         lightcolor=COLORES["acento"], darkcolor=COLORES["acento"])

        fondo = COLORES["fondo"]
        cabecera = tk.Frame(self, bg=fondo)
        cabecera.pack(fill="x", padx=16, pady=(14, 6))
        if self._logo is not None:
            tk.Label(cabecera, image=self._logo, bg=fondo).pack(side="left", padx=(0, 12))
        textos = tk.Frame(cabecera, bg=fondo)
        textos.pack(side="left", fill="x")
        tk.Label(textos, text="Juegos", font=FUENTE_TITULO, fg=COLORES["texto"], bg=fondo).pack(anchor="w")
        tk.Label(textos, text="Modo juego y GE-Proton", font=FUENTE_PEQUENA,
                 fg=COLORES["secundario"], bg=fondo).pack(anchor="w")

        fila_led = tk.Frame(self, bg=fondo)
        fila_led.pack(fill="x", padx=16, pady=(6, 10))
        self._led = tk.Canvas(fila_led, width=16, height=16, bg=fondo, highlightthickness=0)
        self._led.pack(side="left", anchor="n", pady=(3, 0))
        self._led_id = self._led.create_oval(2, 2, 14, 14, fill=COLORES["rojo"], outline="")
        textos_led = tk.Frame(fila_led, bg=fondo)
        textos_led.pack(side="left", padx=(8, 0))
        self._texto_led = tk.Label(textos_led, font=FUENTE_NEGRITA, fg=COLORES["texto"], bg=fondo)
        self._texto_led.pack(anchor="w")
        self._texto_cpu = tk.Label(textos_led, font=FUENTE_PEQUENA, fg=COLORES["secundario"], bg=fondo,
                                   wraplength=330, justify="left")
        self._texto_cpu.pack(anchor="w")

        self._btn_modo = self._boton(self, "", self._modo_juego, primario=True)
        self._btn_modo.pack(fill="x", padx=16)
        self._var_liberar = tk.BooleanVar(master=self, value=False)
        self._chk_liberar = self._casilla(
            self, "Liberar caché de RAM también (no recomendado: ralentiza las cargas)", self._var_liberar)
        self._chk_liberar.pack(anchor="w", padx=16, pady=(8, 0))
        self._btn_ram = self._boton(self, "Liberar RAM", self._liberar_ram)
        self._btn_ram.pack(fill="x", padx=16, pady=(8, 0))

        tk.Frame(self, bg=COLORES["borde"], height=1).pack(fill="x", padx=16, pady=14)

        tk.Label(self, text="Optimizar un proceso", font=FUENTE_NEGRITA, fg=COLORES["texto"], bg=fondo).pack(
            anchor="w", padx=16)
        self._lista_procesos = tk.Listbox(self, height=4, width=1, bg=COLORES["panel"], fg=COLORES["texto"],
                                          selectbackground=COLORES["acento"], selectforeground="#ffffff",
                                          highlightthickness=0, bd=0, activestyle="none", font=FUENTE)
        self._lista_procesos.pack(fill="x", padx=16, pady=(6, 0))
        self._lista_procesos.insert("end", "  pulsa «Actualizar procesos»")
        self._lista_procesos.itemconfigure(0, fg=COLORES["secundario"])
        fila_proc = tk.Frame(self, bg=fondo)
        fila_proc.pack(fill="x", padx=16, pady=(8, 0))
        self._btn_listar = self._boton(fila_proc, "Actualizar procesos", self._listar_procesos)
        self._btn_listar.pack(side="left", fill="x", expand=True)
        self._btn_optimizar = self._boton(fila_proc, "Optimizar proceso", self._optimizar_proceso, primario=True)
        self._btn_optimizar.pack(side="left", fill="x", expand=True, padx=(8, 0))
        self._var_tiempo_real = tk.BooleanVar(master=self, value=False)
        self._chk_tiempo_real = self._casilla(
            self, "Tiempo real: riesgo. Si el juego se cuelga puede dejar el escritorio sin CPU.",
            self._var_tiempo_real)
        self._chk_tiempo_real.pack(anchor="w", padx=16, pady=(8, 0))

        tk.Frame(self, bg=COLORES["borde"], height=1).pack(fill="x", padx=16, pady=14)

        tk.Label(self, text="GE-Proton", font=FUENTE_NEGRITA, fg=COLORES["texto"], bg=fondo).pack(
            anchor="w", padx=16)
        self._lista = tk.Listbox(self, height=4, width=1, bg=COLORES["panel"], fg=COLORES["texto"],
                                 selectbackground=COLORES["acento"], selectforeground="#ffffff",
                                 highlightthickness=0, bd=0, activestyle="none", font=FUENTE)
        self._lista.pack(fill="x", padx=16, pady=(6, 0))
        fila_proton = tk.Frame(self, bg=fondo)
        fila_proton.pack(fill="x", padx=16, pady=(8, 0))
        self._btn_descargar = self._boton(fila_proton, "Descargar la última", self._descargar_proton,
                                          primario=True)
        self._btn_descargar.pack(side="left", fill="x", expand=True)
        self._btn_eliminar = self._boton(fila_proton, "Eliminar", self._eliminar_proton, peligro=True)
        self._btn_eliminar.pack(side="left", padx=(8, 0))

        # El marco de progreso solo se muestra mientras hay una descarga o una extracción.
        self._marco_progreso = tk.Frame(self, bg=fondo)
        self._barra = ttk.Progressbar(self._marco_progreso, style="Juegos.Horizontal.TProgressbar",
                                      mode="determinate", maximum=100)
        self._barra.pack(fill="x")
        fila_prog = tk.Frame(self._marco_progreso, bg=fondo)
        fila_prog.pack(fill="x", pady=(4, 0))
        self._texto_progreso = tk.Label(fila_prog, font=FUENTE_PEQUENA, fg=COLORES["secundario"], bg=fondo,
                                        anchor="w")
        self._texto_progreso.pack(side="left", fill="x", expand=True)
        self._btn_cancelar = self._boton(fila_prog, "Cancelar", self._pedir_cancelar)
        self._btn_cancelar.pack(side="right")

        self._estado = tk.Label(self, text="Listo.", font=FUENTE_PEQUENA, fg=COLORES["secundario"],
                                bg=fondo, anchor="w", justify="left", wraplength=340)
        self._estado.pack(fill="x", padx=16, pady=(12, 14))

    def _boton(self, padre, texto, comando, primario=False, peligro=False):
        if primario:
            fondo, activo = COLORES["acento"], COLORES["acento_activo"]
        elif peligro:
            fondo, activo = COLORES["peligro"], COLORES["peligro_activo"]
        else:
            fondo, activo = COLORES["boton"], COLORES["boton_activo"]
        boton = tk.Button(padre, text=texto, command=comando, bg=fondo, fg="#ffffff" if (primario or peligro)
                          else COLORES["texto"], activebackground=activo, activeforeground="#ffffff",
                          disabledforeground=COLORES["secundario"], relief="flat", bd=0,
                          highlightthickness=0, padx=12, pady=7, font=FUENTE, cursor="hand2")
        boton._fondo_normal = fondo
        return boton

    def _casilla(self, padre, texto, variable):
        return tk.Checkbutton(padre, text=texto, variable=variable, bg=COLORES["fondo"], fg=COLORES["secundario"],
                              activebackground=COLORES["fondo"], activeforeground=COLORES["texto"],
                              selectcolor=COLORES["panel"], highlightthickness=0, bd=0, font=FUENTE_PEQUENA,
                              wraplength=330, justify="left", anchor="w")

    # ----------------------------------------------------------------- estado visible
    def _refrescar(self):
        """Pinta el LED y los textos con el estado actual. Solo lee sysfs y ficheros; es barato."""
        estado = leer_estado()
        self._estado_opt = estado
        activo = bool(estado.get("activado"))
        if estado.get("error"):
            texto, color = "Estado desconocido", COLORES["rojo"]
        elif activo:
            texto, color = "Modo juego: activo", COLORES["verde"]
        else:
            texto, color = "Modo juego: desactivado", COLORES["rojo"]
        self._led.itemconfigure(self._led_id, fill=color)
        self._texto_led.configure(text=texto)
        self._btn_modo.configure(text="Desactivar modo juego" if activo else "Activar modo juego")
        self._texto_cpu.configure(text=resumen_cpu(estado))
        self._refrescar_proton()

    def _refrescar_proton(self):
        self._versiones = versiones_proton()
        self._lista.delete(0, "end")
        if not self._versiones:
            self._lista.insert("end", "  (ninguna instalada todavía)")
            self._lista.itemconfigure(0, fg=COLORES["secundario"])
            return
        for version in self._versiones:
            self._lista.insert("end", "  " + version)

    def _pintar_procesos(self, lista):
        self._procesos = list(lista)
        self._lista_procesos.delete(0, "end")
        if not self._procesos:
            self._lista_procesos.insert("end", "  (ningún proceso tuyo en marcha)")
            self._lista_procesos.itemconfigure(0, fg=COLORES["secundario"])
            return
        for pid, nombre, pct in self._procesos:
            self._lista_procesos.insert("end", f"  {nombre} · PID {pid} · {pct:.0f} % CPU")

    def _mostrar(self, texto, error=False):
        self._estado.configure(text=texto, fg=COLORES["rojo"] if error else COLORES["secundario"])

    def _fijar_ocupada(self, ocupada):
        self._activo = ocupada
        estado = "disabled" if ocupada else "normal"
        for boton in (self._btn_modo, self._btn_ram, self._btn_descargar, self._btn_eliminar,
                      self._btn_listar, self._btn_optimizar):
            boton.configure(state=estado, bg=COLORES["deshabilitado"] if ocupada else boton._fondo_normal)
        for casilla in (self._chk_liberar, self._chk_tiempo_real):
            casilla.configure(state=estado)

    # ----------------------------------------------------------------- acciones
    def _modo_juego(self):
        if self._tarea is not None:
            return
        if self._estado_opt.get("activado"):
            self._lanzar("Desactivando modo juego…", self._tarea_desactivar)
            return
        if not hay_cpufreq():
            # Comprobado antes de tocar nada: en una máquina virtual no hay nada que cambiar
            self._mostrar("Este equipo no permite cambiar la frecuencia de la CPU, habitual en máquinas "
                          "virtuales. No se ha escrito nada.")
            return
        self._lanzar("Activando modo juego…", functools.partial(self._tarea_activar, self._var_liberar.get()))

    def _liberar_ram(self):
        if self._tarea is None:
            self._lanzar("Liberando RAM…", self._tarea_liberar)

    def _listar_procesos(self):
        if self._tarea is None:
            self._lanzar("Midiendo la CPU de tus procesos (medio segundo)…", self._tarea_listar)

    def _optimizar_proceso(self):
        if self._tarea is not None:
            return
        seleccion = self._lista_procesos.curselection()
        if not seleccion or not self._procesos:
            self._mostrar("Elige un proceso de la lista. Si no aparece, pulsa «Actualizar procesos».")
            return
        pid, nombre, _ = self._procesos[seleccion[0]]
        orden = ["optimizar", str(pid)] + (["--tiempo-real"] if self._var_tiempo_real.get() else [])
        self._lanzar(f"Optimizando {nombre}…", functools.partial(self._tarea_optimizar, orden))

    def _descargar_proton(self):
        if self._tarea is None:
            self._lanzar("Descargando la última GE-Proton…", self._tarea_descargar, cancelable=True)

    def _eliminar_proton(self):
        if self._tarea is not None:
            return
        seleccion = self._lista.curselection()
        if not seleccion or not self._versiones:
            self._mostrar("Elige una versión de la lista para eliminarla.")
            return
        nombre = self._versiones[seleccion[0]]
        if not messagebox.askyesno("Eliminar GE-Proton", f"¿Borrar {nombre}? No se puede deshacer.", parent=self):
            return
        self._lanzar(f"Eliminando {nombre}…", functools.partial(self._tarea_eliminar, nombre))

    def _pedir_cancelar(self):
        self._cancelar.set()
        self._mostrar("Cancelando… la descarga queda guardada; se retomará solo si eliges la misma versión.")

    # ----------------------------------------------------------------- tareas (hilo aparte)
    def _tarea_activar(self, liberar, informe):
        """Activa el governor primero (comprueba antes de cambiar nada) y, si se pidió, libera la caché."""
        if not sudo_sin_contrasena():
            return _abrir_terminal(["activar"] + (["liberar-ram"] if liberar else []))
        informe.mensaje("Activando modo rendimiento…")
        texto = "Modo juego activado. " + _ejecutar_root(["activar"])
        if not liberar:
            return texto
        informe.mensaje("Liberando caché de RAM…")
        return texto + "\n" + _ejecutar_root(["liberar-ram"])

    def _tarea_desactivar(self, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal(["revertir"])
        return _ejecutar_root(["revertir"])

    def _tarea_liberar(self, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal(["liberar-ram"])
        return _ejecutar_root(["liberar-ram"])

    def _tarea_optimizar(self, orden, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal([" ".join(orden)])
        return _ejecutar_root(orden)

    def _tarea_listar(self, informe):
        lista = procesos_de_usuario()
        informe.datos(lista)
        if not lista:
            return "No hay procesos tuyos en marcha."
        return f"{len(lista)} procesos. Elige uno y pulsa «Optimizar proceso»."

    def _tarea_descargar(self, informe):
        ruta = env.instalar_ge_proton(None)
        return f"GE-Proton lista: {os.path.basename(ruta)}. Reinicia Steam para verla."

    def _tarea_eliminar(self, nombre, informe):
        """Renombra primero a una carpeta oculta (la lista deja de verla al instante) y luego la borra.
        Si se corta a mitad, la carpeta oculta se limpia al abrir la ventana otra vez."""
        ruta = ruta_proton_segura(nombre)
        oculta = os.path.join(os.path.dirname(ruta), f"{PREFIJO_ELIMINADA}{nombre}-{time.time_ns()}")
        try:
            os.rename(ruta, oculta)
        except OSError as e:
            raise env.ErrorEntorno(f"no se pudo preparar el borrado de {nombre}: {e.strerror or e}.")
        try:
            shutil.rmtree(oculta)
        except OSError as e:
            raise env.ErrorEntorno(f"no se pudo borrar {nombre} del todo ({e.strerror or e}); "
                                   "se limpiará al abrir la ventana otra vez.")
        return f"Eliminada {nombre}."

    def _lanzar(self, inicio, funcion, cancelable=False):
        """Ejecuta `funcion(informe)` en un hilo y empieza a consultar su cola."""
        if self._tarea is not None:
            return
        self._cancelar.clear()
        cola = queue.Queue()
        self._cola = cola
        self._cancelable = cancelable
        informe = _InformeGui(cola, self._cancelar)
        self._fijar_ocupada(True)
        self._mostrar(inicio)

        def trabajo():
            env.fijar_informe(informe)
            try:
                ok, texto = True, funcion(informe) or "Hecho."
            except env.ErrorEntorno as e:
                ok, texto = False, str(e)
            except Exception as e:  # un fallo inesperado no debe dejar la ventana bloqueada
                ok, texto = False, f"error inesperado: {e}"
            finally:
                # Antes de avisar a la ventana: así una tarea nueva no puede pisar este informe
                env.fijar_informe(None)
            cola.put(("fin", ok, texto))

        hilo = threading.Thread(target=trabajo, daemon=True)
        try:
            hilo.start()
        except RuntimeError as e:  # sin recursos para otro hilo: la ventana vuelve a estar libre
            env.fijar_informe(None)
            self._cola = None
            self._cancelable = False
            self._fijar_ocupada(False)
            self._mostrar(f"No se pudo iniciar la tarea: {e}", error=True)
            return
        self._tarea = hilo
        self._sondeo = self.after(100, self._sondear)

    def _sondear(self):
        """Lee la cola de la tarea. Solo se reprograma mientras la tarea siga viva."""
        self._sondeo = None
        cola = self._cola
        while cola is not None:
            try:
                evento = cola.get_nowait()
            except queue.Empty:
                break
            self._procesar(evento)
            if evento[0] == "fin":
                return
        if self._tarea is not None:
            self._sondeo = self.after(100, self._sondear)

    def _procesar(self, evento):
        tipo = evento[0]
        if tipo == "mensaje":
            self._mostrar(evento[1])
        elif tipo == "progreso":
            self._pintar_progreso(*evento[1:])
        elif tipo == "datos":
            self._pintar_procesos(evento[1])
        elif tipo == "cerrar_progreso":
            self._marco_progreso.pack_forget()
        elif tipo == "fin":
            _, ok, texto = evento
            self._tarea = None
            self._cola = None
            self._cancelable = False
            self._marco_progreso.pack_forget()
            self._fijar_ocupada(False)
            self._mostrar(_limpiar(str(texto)), error=not ok)
            self._refrescar()

    def _pintar_progreso(self, etiqueta, hecho, total, velocidad):
        if not self._marco_progreso.winfo_ismapped():
            self._marco_progreso.pack(fill="x", padx=16, pady=(8, 0), before=self._estado)
        if total:
            self._barra.configure(value=100 * min(hecho / total, 1.0))
            texto = f"{etiqueta} · {hecho / MB:.0f}/{total / MB:.0f} MB · {velocidad / MB:.1f} MB/s"
        else:
            self._barra.configure(value=0)
            texto = f"{etiqueta} · {hecho} archivos"
        self._texto_progreso.configure(text=texto)

    # ----------------------------------------------------------------- errores y foco
    def report_callback_exception(self, exc, val, tb):
        """Tk llama aquí si falla un botón o una consulta. Por defecto solo escribiría en stderr,
        donde nadie lo ve: se anota en el registro y se muestra en la barra de estado."""
        registrar("".join(traceback.format_exception(exc, val, tb)))
        try:
            self._mostrar(f"Error inesperado: {val}. Detalles en {ruta_registro()}", error=True)
        except AttributeError:  # la barra de estado aún no existe (el fallo fue al construir la ventana)
            pass

    def _al_enfocar(self, evento):
        # Solo cuando la ventana gana el foco (no con los widgets hijos), y sin tarea en curso
        if evento.widget is self and self._tarea is None:
            self._refrescar()

    def _cerrar(self):
        if self._tarea is not None and self._tarea.is_alive():
            if not self._cancelable:
                # Una orden de root a medias deja el sistema a medias: no se cierra hasta que acabe
                messagebox.showinfo("Juegos", "Hay una orden en curso que cambia el sistema (activar, revertir, "
                                              "optimizar, liberar RAM o eliminar). Cerrar ahora podría dejarla "
                                              "a medias: espera a que termine.", parent=self)
                return
            if not messagebox.askokcancel("Juegos", "Hay una descarga en curso. Se cancelará y se podrá retomar "
                                                    "eligiendo la misma versión. ¿Cerrar?", parent=self):
                return
            self._cancelar.set()
            self._tarea.join(timeout=3)  # da tiempo a parar en un punto seguro (la extracción no se corta)
            if self._tarea.is_alive():
                messagebox.showinfo("Juegos", "La descarga aún no ha parado (está extrayendo o guardando). "
                                              "Espera unos segundos y vuelve a cerrar.", parent=self)
                return
        self.destroy()


def main(argv=None):
    if not os.environ.get("DISPLAY"):
        # Tk solo funciona con X11: en Hyprland llega por XWayland, que es quien define DISPLAY.
        # WAYLAND_DISPLAY sola no basta: antes se intentaba abrir igual y fallaba sin decir nada.
        return fallo_al_abrir("Juegos no puede abrirse: falta DISPLAY (XWayland), que la ventana necesita. "
                              "Reinicia la sesión y vuelve a probar.")
    try:
        ventana = Juegos()
    except Exception as e:  # p. ej. tk.TclError si el servidor gráfico no responde
        return fallo_al_abrir(f"No se pudo abrir la ventana de Juegos: {e}", traceback.format_exc())
    ventana.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
