#!/usr/bin/env python3
"""Juegos: ventana compacta de Infinity OS para el modo juego y GE-Proton.

Tkinter estándar (viene con python3-tk; no hace falta customtkinter ni nada de pip).

Ligera de verdad: no tiene temporizadores en reposo. El estado (el LED) se mira al abrir, al
recuperar el foco y tras cada acción. Solo mientras hay una tarea en curso (una descarga, una
activación) se consulta una cola cada 100 ms, y en cuanto termina esa consulta se detiene. Con
el juego en marcha, la ventana no hace nada en segundo plano.

Las acciones de root se lanzan con `sudo -n` (la sesión en vivo lo permite sin contraseña). Si
sudo pide contraseña, se abre una terminal para pedirla ahí.
"""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox, ttk

AQUI = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, AQUI)  # game_environment.py y game_optimizer.py viven junto a este archivo
import game_environment as env  # noqa: E402

COMANDO = "infinity-juegos"
LOGOS = ("/usr/share/infinity/game-optimizer.png",
         # Desarrollo: overlay/usr/lib/infinity -> repo/branding
         os.path.join(AQUI, "..", "..", "..", "..", "branding", "game-optimizer.png"))
MB = 1024 * 1024

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
    """Abre una terminal que pide la contraseña de sudo y ejecuta las órdenes."""
    cadena = " && ".join(f"sudo {COMANDO} {o}" for o in ordenes)
    return ["foot", "-T", "Juegos: permiso", "sh", "-c",
            f"{cadena}; echo; printf 'Pulsa Enter para cerrar... '; read -r _"]


def sudo_sin_contrasena():
    """True si sudo funciona sin pedir contraseña."""
    try:
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=10).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def leer_estado():
    """Estado del modo juego. No necesita root (solo lee sysfs y el fichero de estado)."""
    import game_optimizer as opt
    try:
        return opt.estado()
    except (OSError, opt.OptimizadorError):
        return {"activado": False, "governors": {}, "error": True}


def versiones_proton(directorio=None):
    """GE-Proton instaladas, la más nueva primero."""
    d = directorio or env.directorio_compatibilidad()
    try:
        nombres = os.listdir(d)
    except FileNotFoundError:
        return []
    validas = [n for n in nombres if n.startswith("GE-Proton") and os.path.isdir(os.path.join(d, n))]
    return sorted(validas, key=lambda n: tuple(int(x) for x in re.findall(r"\d+", n)), reverse=True)


def ruta_proton_segura(nombre, directorio=None):
    """Ruta de una versión instalada, o ErrorEntorno si el nombre intenta salirse del directorio."""
    d = directorio or env.directorio_compatibilidad()
    if not nombre or os.sep in nombre or nombre in (".", "..") or not nombre.startswith("GE-Proton"):
        raise env.ErrorEntorno("nombre de versión no válido.")
    ruta = os.path.join(d, nombre)
    if os.path.dirname(os.path.realpath(ruta)) != os.path.realpath(d):
        raise env.ErrorEntorno("la versión no está en la carpeta de Steam.")
    return ruta


def _ejecutar_root(orden):
    """Lanza una orden de root con sudo -n. Devuelve su salida; ErrorEntorno si falla."""
    try:
        r = subprocess.run(argv_privilegiado(orden), capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        raise env.ErrorEntorno(f"no se pudo ejecutar sudo: {e}")
    if r.returncode != 0:
        detalle = (r.stderr or r.stdout).strip().splitlines()
        raise env.ErrorEntorno(f"'{' '.join(orden)}' falló: {detalle[-1] if detalle else 'sin detalle'}")
    return r.stdout.strip()


def _abrir_terminal(ordenes):
    """Sin sudo sin contraseña: una terminal pide la contraseña y ejecuta las órdenes."""
    try:
        subprocess.Popen(argv_terminal_sudo(ordenes), start_new_session=True,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        raise env.ErrorEntorno(f"no hay terminal disponible. En una terminal: sudo {COMANDO} {ordenes[0]}")
    return "sudo pide contraseña: termina en la terminal que se ha abierto y vuelve aquí."


def _limpiar(texto):
    for prefijo in ("[..] ", "[ok] ", "[!!] "):
        if texto.startswith(prefijo):
            return texto[len(prefijo):]
    return texto


class _InformeGui:
    """Recibe mensajes y progreso del hilo de trabajo y los deja en una cola para la ventana.

    Cancelar: si la ventana pide cancelar, el siguiente aviso de progreso lanza ErrorEntorno. Como
    la descarga guarda lo ya bajado en un .part, al volver a pedirla se reanuda donde quedó.
    """

    def __init__(self, cola, cancelar):
        self.cola = cola
        self.cancelar = cancelar

    def mensaje(self, texto):
        self.cola.put(("mensaje", _limpiar(texto)))

    def progreso(self, etiqueta, hecho, total, velocidad):
        if self.cancelar.is_set():
            raise env.ErrorEntorno("descarga cancelada; se puede retomar más tarde.")
        self.cola.put(("progreso", etiqueta, hecho, total, velocidad))

    def cerrar_progreso(self):
        self.cola.put(("cerrar_progreso",))


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
        self._sondeo = None  # id del after activo: solo existe mientras hay una tarea
        self._activo = False
        self._versiones = []
        self._estado_opt = {}
        self._construir()
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
        self._texto_cpu = tk.Label(textos_led, font=FUENTE_PEQUENA, fg=COLORES["secundario"], bg=fondo)
        self._texto_cpu.pack(anchor="w")

        self._btn_modo = self._boton(self, "", self._modo_juego, primario=True)
        self._btn_modo.pack(fill="x", padx=16)
        self._btn_ram = self._boton(self, "Liberar RAM", self._liberar_ram)
        self._btn_ram.pack(fill="x", padx=16, pady=(8, 0))

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
        valores = set(estado.get("governors", {}).values())
        cpu = f"CPU: {', '.join(sorted(valores))}" if valores else "CPU sin control de frecuencia"
        self._texto_cpu.configure(text=cpu)
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

    def _mostrar(self, texto, error=False):
        self._estado.configure(text=texto, fg=COLORES["rojo"] if error else COLORES["secundario"])

    def _fijar_ocupada(self, ocupada):
        self._activo = ocupada
        estado = "disabled" if ocupada else "normal"
        for boton in (self._btn_modo, self._btn_ram, self._btn_descargar, self._btn_eliminar):
            boton.configure(state=estado, bg=COLORES["deshabilitado"] if ocupada else boton._fondo_normal)

    # ----------------------------------------------------------------- acciones
    def _modo_juego(self):
        if self._tarea is not None:
            return
        if self._estado_opt.get("activado"):
            self._lanzar("Desactivando modo juego…", self._tarea_desactivar)
        else:
            self._lanzar("Activando modo juego…", self._tarea_activar)

    def _liberar_ram(self):
        if self._tarea is None:
            self._lanzar("Liberando RAM…", self._tarea_liberar)

    def _descargar_proton(self):
        if self._tarea is None:
            self._lanzar("Descargando la última GE-Proton…", self._tarea_descargar)

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
        self._lanzar(f"Eliminando {nombre}…", lambda informe: self._tarea_eliminar(nombre))

    def _pedir_cancelar(self):
        self._cancelar.set()
        self._mostrar("Cancelando… la descarga queda guardada para retomarla.")

    # ----------------------------------------------------------------- tareas (hilo aparte)
    def _tarea_activar(self, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal(["liberar-ram", "activar"])
        informe.mensaje("Liberando caché de RAM…")
        informe.mensaje(_ejecutar_root(["liberar-ram"]))
        informe.mensaje("Activando modo rendimiento…")
        return "Modo juego activado. " + _ejecutar_root(["activar"])

    def _tarea_desactivar(self, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal(["revertir"])
        return "Modo juego desactivado. " + _ejecutar_root(["revertir"])

    def _tarea_liberar(self, informe):
        if not sudo_sin_contrasena():
            return _abrir_terminal(["liberar-ram"])
        return _ejecutar_root(["liberar-ram"])

    def _tarea_descargar(self, informe):
        ruta = env.instalar_ge_proton(None)
        return f"GE-Proton lista: {os.path.basename(ruta)}. Reinicia Steam para verla."

    def _tarea_eliminar(self, nombre):
        ruta = ruta_proton_segura(nombre)
        shutil.rmtree(ruta)
        return f"Eliminada {nombre}."

    def _lanzar(self, inicio, funcion):
        """Ejecuta `funcion(informe)` en un hilo y empieza a consultar su cola."""
        if self._tarea is not None:
            return
        self._cancelar.clear()
        self._cola = queue.Queue()
        informe = _InformeGui(self._cola, self._cancelar)
        self._fijar_ocupada(True)
        self._mostrar(inicio)

        def trabajo():
            env.fijar_informe(informe)
            try:
                self._cola.put(("fin", True, funcion(informe) or "Hecho."))
            except env.ErrorEntorno as e:
                self._cola.put(("fin", False, str(e)))
            except Exception as e:  # un fallo inesperado no debe dejar la ventana bloqueada
                self._cola.put(("fin", False, f"error inesperado: {e}"))
            finally:
                env.fijar_informe(None)

        self._tarea = threading.Thread(target=trabajo, daemon=True)
        self._tarea.start()
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
        elif tipo == "cerrar_progreso":
            self._marco_progreso.pack_forget()
        elif tipo == "fin":
            _, ok, texto = evento
            self._tarea = None
            self._cola = None
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

    # ----------------------------------------------------------------- foco y cierre
    def _al_enfocar(self, evento):
        # Solo cuando la ventana gana el foco (no con los widgets hijos), y sin tarea en curso
        if evento.widget is self and self._tarea is None:
            self._refrescar()

    def _cerrar(self):
        if self._tarea is not None and self._tarea.is_alive():
            if not messagebox.askokcancel("Juegos", "Hay una tarea en curso. Si cierras, una descarga "
                                                    "se puede retomar más tarde. ¿Cerrar?", parent=self):
                return
            self._cancelar.set()
        self.destroy()


def main(argv=None):
    if not os.environ.get("DISPLAY") and not os.environ.get("WAYLAND_DISPLAY"):
        print("[error] no hay pantalla: esta ventana necesita una sesión gráfica.", file=sys.stderr)
        return 1
    Juegos().mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
