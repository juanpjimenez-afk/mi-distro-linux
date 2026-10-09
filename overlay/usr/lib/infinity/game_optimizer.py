#!/usr/bin/env python3
"""Optimizador de juegos de Infinity OS: prepara el sistema para jugar con el máximo rendimiento.

    activar_modo_rendimiento()  CPU en 'performance' en todos los núcleos
    liberar_ram()               libera la caché de páginas (drop_caches), con sync antes
    optimizar_proceso(pid)      renice a -10 y, si se puede, prioridad de tiempo real (chrt)
    revertir_optimizacion()     devuelve el governor de cada núcleo a su valor original

Cada paso es una orden del sistema (sync, tee, renice, chrt) lanzada con subprocess, así que se
puede leer y repetir a mano. Las acciones que tocan el kernel necesitan root: sin privilegios el
script se detiene con un mensaje claro antes de cambiar nada.

Uso (como root):
    sudo python3 game_optimizer.py activar
    sudo python3 game_optimizer.py liberar-ram
    sudo python3 game_optimizer.py optimizar PID [--sin-tiempo-real]
    sudo python3 game_optimizer.py revertir
    python3 game_optimizer.py estado          (solo lectura, no necesita root)

Para ampliarlo: cada acción es una función que devuelve datos o lanza OptimizadorError. La línea
de órdenes solo las llama, así que una interfaz gráfica o un servicio pueden importar este módulo.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

# Rutas del sistema. Están en variables para poder probarlas con un árbol falso.
CPU_DIR = "/sys/devices/system/cpu"
PROC_DIR = "/proc"
MEMINFO = "/proc/meminfo"
DROP_CACHES = "/proc/sys/vm/drop_caches"
# Estado guardado al activar, para poder revertir. /run es tmpfs: se borra al reiniciar.
ESTADO_FICHERO = "/run/infinity/game-optimizer.json"

NICE_JUEGO = -10
# Prioridad de SCHED_RR (1 es la más baja). Aun así, un proceso en tiempo real gana a todo lo
# normal: si el juego se queda en un bucle, puede dejar sin CPU al escritorio. Por eso se puede
# desactivar con --sin-tiempo-real.
PRIORIDAD_TIEMPO_REAL = 1
# Governors que se usan al revertir, en orden de preferencia (según lo que ofrezca el driver).
GOVERNORS_REVERTIR = ("powersave", "ondemand")


class OptimizadorError(Exception):
    """Un paso no se pudo hacer. El mensaje está pensado para la persona que usa el equipo."""


# --------------------------------------------------------------------------- utilidades
def requiere_root():
    """Corta antes de tocar nada si no somos root (las escrituras al kernel lo necesitan)."""
    if os.geteuid() != 0:
        raise OptimizadorError("esta acción necesita permisos de administrador: ejecútala con sudo.")


def ejecutar(argv, entrada=None):
    """Lanza una orden y devuelve el CompletedProcess. Un programa que no existe es un error claro."""
    try:
        return subprocess.run(argv, input=entrada, text=True, capture_output=True, timeout=30, check=False)
    except FileNotFoundError:
        raise OptimizadorError(f"no se encuentra el programa '{argv[0]}'; hay que instalarlo.")
    except subprocess.TimeoutExpired:
        raise OptimizadorError(f"'{argv[0]}' tardó demasiado y se cortó.")


def leer(ruta):
    with open(ruta, encoding="utf-8") as f:
        return f.read().strip()


def escribir(ruta, valor):
    """Escribe un valor en un fichero del kernel (sysfs/procfs) con tee, como root."""
    r = ejecutar(["tee", ruta], entrada=f"{valor}\n")
    if r.returncode != 0:
        detalle = r.stderr.strip() or "motivo desconocido"
        raise OptimizadorError(f"no se pudo escribir '{valor}' en {ruta}: {detalle}")


def _cargar_estado():
    """Estado guardado al activar. Si no existe, es un diccionario vacío."""
    try:
        with open(ESTADO_FICHERO, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise OptimizadorError(
            f"el fichero de estado {ESTADO_FICHERO} está dañado; bórralo y revisa los governors a mano.")


def _guardar_estado(datos):
    os.makedirs(os.path.dirname(ESTADO_FICHERO), exist_ok=True)
    tmp = ESTADO_FICHERO + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(datos, f)
    os.replace(tmp, ESTADO_FICHERO)  # reemplazo atómico: un corte no deja el fichero a medias


def _borrar_estado():
    try:
        os.remove(ESTADO_FICHERO)
    except FileNotFoundError:
        pass


# --------------------------------------------------------------------------- CPU
def _rutas_governor():
    """Ruta de scaling_governor de cada núcleo que tiene control de frecuencia."""
    return sorted(glob.glob(os.path.join(CPU_DIR, "cpu[0-9]*", "cpufreq", "scaling_governor")))


def _governors_disponibles(ruta_governor):
    ruta = os.path.join(os.path.dirname(ruta_governor), "scaling_available_governors")
    return leer(ruta).split()


def activar_modo_rendimiento():
    """Pone 'performance' en todos los núcleos. Devuelve cuántos se cambiaron.

    Antes de escribir comprueba que todos los núcleos ofrecen 'performance', y guarda el governor
    original de cada uno (solo la primera vez, para no perder el valor real si se llama dos veces).
    """
    requiere_root()
    rutas = _rutas_governor()
    if not rutas:
        raise OptimizadorError(
            "este equipo no expone control de frecuencia de la CPU (pasa en muchas máquinas virtuales).")
    sin_rendimiento = [r for r in rutas if "performance" not in _governors_disponibles(r)]
    if sin_rendimiento:
        raise OptimizadorError(
            "el driver de la CPU no ofrece el governor 'performance'; no se ha cambiado nada.")

    estado = _cargar_estado()
    originales = estado.get("governors", {})
    for ruta in rutas:
        originales.setdefault(ruta, leer(ruta))
    _guardar_estado({"governors": originales})

    for ruta in rutas:
        escribir(ruta, "performance")
    return len(rutas)


def revertir_optimizacion():
    """Devuelve cada núcleo a su governor original; si no hay estado guardado, usa powersave u ondemand."""
    requiere_root()
    rutas = _rutas_governor()
    if not rutas:
        raise OptimizadorError("este equipo no expone control de frecuencia de la CPU.")
    originales = _cargar_estado().get("governors", {})

    for ruta in rutas:
        destino = originales.get(ruta)
        if destino is None:
            destino = next((g for g in GOVERNORS_REVERTIR if g in _governors_disponibles(ruta)), None)
            if destino is None:
                raise OptimizadorError(f"no hay governor de ahorro disponible ({', '.join(GOVERNORS_REVERTIR)}).")
        escribir(ruta, destino)
    _borrar_estado()
    return len(rutas)


# --------------------------------------------------------------------------- memoria
def _memoria_disponible_mb():
    with open(MEMINFO, encoding="utf-8") as f:
        for linea in f:
            if linea.startswith("MemAvailable:"):
                return int(linea.split()[1]) // 1024
    raise OptimizadorError("no se pudo leer la memoria disponible de /proc/meminfo.")


def liberar_ram():
    """Libera la caché de páginas limpias (drop_caches=1). No toca datos no guardados:
    primero hace sync para volcar a disco lo pendiente. Devuelve la memoria libre antes y después."""
    requiere_root()
    antes = _memoria_disponible_mb()
    r = ejecutar(["sync"])
    if r.returncode != 0:
        raise OptimizadorError("sync falló; no se libera la caché para no perder datos pendientes.")
    escribir(DROP_CACHES, "1")
    return {"antes_mb": antes, "despues_mb": _memoria_disponible_mb()}


# --------------------------------------------------------------------------- procesos
def optimizar_proceso(pid, tiempo_real=True):
    """Pone el proceso en nice -10 y, si se puede y se pide, en SCHED_RR (chrt).

    Solo se aplica al proceso principal (no a sus hilos). Si el tiempo real no se puede, se avisa
    y el nice sigue aplicado.
    """
    requiere_root()
    if not isinstance(pid, int) or pid <= 0:
        raise OptimizadorError("el PID tiene que ser un número entero positivo.")
    directorio = os.path.join(PROC_DIR, str(pid))
    if not os.path.isdir(directorio):
        raise OptimizadorError(f"no existe ningún proceso con PID {pid}.")
    nombre = leer(os.path.join(directorio, "comm"))

    r = ejecutar(["renice", "-n", str(NICE_JUEGO), "-p", str(pid)])
    if r.returncode != 0:
        raise OptimizadorError(f"renice falló: {r.stderr.strip() or 'motivo desconocido'}")

    resultado = {"pid": pid, "proceso": nombre, "nice": NICE_JUEGO, "tiempo_real": False, "avisos": []}
    if tiempo_real:
        try:
            r = ejecutar(["chrt", "-r", "-p", str(PRIORIDAD_TIEMPO_REAL), str(pid)])
            if r.returncode == 0:
                resultado["tiempo_real"] = True
            else:
                resultado["avisos"].append(
                    f"no se pudo dar tiempo real: {r.stderr.strip() or 'sin detalle'}. El nice sí está aplicado.")
        except OptimizadorError as e:
            resultado["avisos"].append(f"tiempo real no disponible: {e}")
    return resultado


# --------------------------------------------------------------------------- estado (solo lectura)
def estado():
    """Governor actual de cada núcleo y si hay una activación pendiente de revertir."""
    return {
        "governors": {ruta: leer(ruta) for ruta in _rutas_governor()},
        "activado": bool(_cargar_estado().get("governors")),
    }


# --------------------------------------------------------------------------- línea de órdenes
def main(argv=None):
    parser = argparse.ArgumentParser(description="Optimizador de juegos de Infinity OS.")
    sub = parser.add_subparsers(dest="orden", required=True)
    sub.add_parser("activar", help="CPU en modo rendimiento")
    sub.add_parser("liberar-ram", help="liberar la caché de páginas")
    op = sub.add_parser("optimizar", help="nice -10 y tiempo real para un proceso")
    op.add_argument("pid", type=int)
    op.add_argument("--sin-tiempo-real", action="store_true", help="no pedir prioridad de tiempo real")
    sub.add_parser("revertir", help="devolver el governor de la CPU a su valor de ahorro")
    sub.add_parser("estado", help="ver el governor actual (no necesita root)")
    args = parser.parse_args(argv)

    try:
        if args.orden == "activar":
            print(f"Modo rendimiento activado en {activar_modo_rendimiento()} núcleos.")
        elif args.orden == "liberar-ram":
            r = liberar_ram()
            print(f"Memoria disponible: {r['antes_mb']} MB → {r['despues_mb']} MB.")
        elif args.orden == "optimizar":
            r = optimizar_proceso(args.pid, tiempo_real=not args.sin_tiempo_real)
            print(f"Proceso {r['pid']} ({r['proceso']}): nice {r['nice']}"
                  + (", tiempo real activado." if r["tiempo_real"] else "."))
            for aviso in r["avisos"]:
                print(f"Aviso: {aviso}", file=sys.stderr)
        elif args.orden == "revertir":
            print(f"Governor restaurado en {revertir_optimizacion()} núcleos.")
        elif args.orden == "estado":
            e = estado()
            for ruta, valor in e["governors"].items():
                print(f"{os.path.basename(os.path.dirname(os.path.dirname(ruta)))}: {valor}")
            print("Activación pendiente de revertir." if e["activado"] else "Sin activación pendiente.")
    except OptimizadorError as e:
        print(f"game_optimizer: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
