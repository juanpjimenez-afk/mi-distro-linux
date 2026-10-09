#!/usr/bin/env python3
"""Optimizador de juegos de Infinity OS: prepara el sistema para jugar con el máximo rendimiento.

    activar_modo_rendimiento()  governor 'performance' en todos los núcleos (guarda el original)
    liberar_ram()               libera la caché de páginas (drop_caches), con sync antes
    optimizar_proceso(pid)      nice -10 en TODOS los hilos del proceso; con tiempo_real=True,
                                además SCHED_RR en el hilo principal (riesgo: opt-in)
    revertir_optimizacion()     devuelve governor, nice y política originales de lo que se cambió
    estado()                    solo lectura: qué está cambiado de verdad

Cada paso es una orden del sistema (sync, tee, renice, chrt) lanzada con subprocess, así que se
puede leer y repetir a mano. Las acciones que tocan el sistema necesitan root: sin privilegios el
script se detiene con un mensaje claro antes de cambiar nada.

Uso (como root):
    sudo python3 game_optimizer.py activar
    sudo python3 game_optimizer.py liberar-ram
    sudo python3 game_optimizer.py optimizar PID [--tiempo-real]
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
# Estado guardado antes de cambiar nada, para poder revertir. /run es tmpfs: se borra al reiniciar.
ESTADO_FICHERO = "/run/infinity/game-optimizer.json"

NICE_JUEGO = -10
# Prioridad de SCHED_RR (1 es la más baja). Aun así, un hilo en tiempo real gana a todo lo normal:
# si el juego se queda en un bucle, puede dejar sin CPU al escritorio y a otros procesos que
# compartan núcleo (medido: un proceso normal fijado al mismo núcleo baja del 50 % al 5 % de CPU).
# Por eso solo se pide con --tiempo-real.
PRIORIDAD_TIEMPO_REAL = 1
# Bandera de chrt para devolver cada política (la política original de cada hilo).
FLAG_POLITICA = {os.SCHED_OTHER: "-o", os.SCHED_BATCH: "-b", os.SCHED_IDLE: "-i",
                 os.SCHED_FIFO: "-f", os.SCHED_RR: "-r"}


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


def _detalle(r):
    return r.stderr.strip() or r.stdout.strip() or "motivo desconocido"


def leer(ruta):
    """Contenido de un fichero del kernel. Un error de lectura se convierte en OptimizadorError."""
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read().strip()
    except OSError as e:
        raise OptimizadorError(f"no se puede leer {ruta}: {e.strerror or e}.")


def escribir(ruta, valor):
    """Escribe un valor en un fichero del kernel (sysfs/procfs) con tee, como root."""
    r = ejecutar(["tee", ruta], entrada=f"{valor}\n")
    if r.returncode != 0:
        raise OptimizadorError(f"no se pudo escribir '{valor}' en {ruta}: {_detalle(r)}")


# --------------------------------------------------------------------------- estado guardado
# Forma del fichero (solo lo escribe este módulo):
#   {"governors": {ruta_governor: valor_original},
#    "procesos":  {"PID": {"nombre": str, "inicio": int,
#                          "hilos": {"TID": {"nice": int, "politica": int, "prio": int}}}}}
# "inicio" es el campo 22 de /proc/PID/stat: dice si el PID sigue siendo el mismo proceso.
def _estado_vacio():
    return {"governors": {}, "procesos": {}}


def _error_formato_estado():
    return OptimizadorError(
        f"el fichero de estado {ESTADO_FICHERO} no tiene el formato esperado; no se ha tocado nada. "
        f"Para empezar de cero, bórralo con: sudo rm {ESTADO_FICHERO} "
        "(ojo: los governors y prioridades que se cambiaron no se restauran solos).")


def _validar_estado(datos):
    """Comprueba la forma del estado. Si no encaja, error claro: no se borra ni se cambia nada.

    Una clave ausente cuenta como vacía: así se lee el formato anterior ({"governors": ...}).
    Un tipo incorrecto sí es error."""
    if not isinstance(datos, dict):
        raise _error_formato_estado()
    governors = datos.get("governors", {})
    procesos = datos.get("procesos", {})
    if not isinstance(governors, dict) or not isinstance(procesos, dict):
        raise _error_formato_estado()
    if not all(isinstance(r, str) and isinstance(v, str) for r, v in governors.items()):
        raise _error_formato_estado()
    for pid, proceso in procesos.items():
        if not (isinstance(pid, str) and pid.isdigit() and isinstance(proceso, dict)
                and isinstance(proceso.get("nombre"), str) and isinstance(proceso.get("inicio"), int)
                and isinstance(proceso.get("hilos"), dict)):
            raise _error_formato_estado()
        for tid, hilo in proceso["hilos"].items():
            if not (isinstance(tid, str) and tid.isdigit() and isinstance(hilo, dict)
                    and all(isinstance(hilo.get(k), int) for k in ("nice", "politica", "prio"))):
                raise _error_formato_estado()
    return {"governors": governors, "procesos": procesos}


def _cargar_estado():
    """Estado guardado. Sin fichero, vacío. Dañado o con otra forma: OptimizadorError (no se borra nada)."""
    try:
        with open(ESTADO_FICHERO, encoding="utf-8") as f:
            datos = json.load(f)
    except FileNotFoundError:
        return _estado_vacio()
    except ValueError:  # JSON mal formado o texto que no es UTF-8
        raise _error_formato_estado()
    except OSError as e:
        raise OptimizadorError(f"no se puede leer {ESTADO_FICHERO}: {e.strerror or e}.")
    return _validar_estado(datos)


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


def _persistir(datos):
    """Guarda el estado, o borra el fichero si ya no queda nada que deshacer."""
    if datos["governors"] or datos["procesos"]:
        _guardar_estado(datos)
    else:
        _borrar_estado()


# --------------------------------------------------------------------------- procesos
def _inicio_proceso(pid):
    """Hora de inicio de un proceso (campo 22 de /proc/PID/stat). None si ya no existe."""
    try:
        with open(os.path.join(PROC_DIR, str(pid), "stat"), encoding="utf-8") as f:
            campos = f.read().rsplit(")", 1)[1].split()
        return int(campos[19])  # campos[0] es el campo 3 (estado); el 22 es el índice 19
    except (OSError, IndexError, ValueError):
        return None


def _comprobar_proceso(pid):
    """(nombre, inicio) de un proceso al que sí se puede tocar.

    Rechaza el PID 1, los hilos del kernel (su padre es kthreadd, PID 2), los zombies y los PID
    que no existen. Se hace antes de tocar nada.
    """
    try:
        cabecera = leer(os.path.join(PROC_DIR, str(pid), "status"))
    except OptimizadorError:
        raise OptimizadorError(f"no existe ningún proceso con PID {pid}.")
    campos = {}
    for linea in cabecera.splitlines():
        clave, sep, valor = linea.partition(":")
        if sep:
            campos[clave.strip()] = valor.strip()
    nombre = campos.get("Name", "?")
    if pid == 1:
        raise OptimizadorError("el PID 1 es el sistema (init) y no se toca.")
    if campos.get("PPid", "") in ("0", "2"):
        raise OptimizadorError(f"el PID {pid} ({nombre}) es un hilo del kernel, no un programa; no se toca.")
    if campos.get("State", "")[:1] in ("Z", "X"):
        raise OptimizadorError(f"el proceso {pid} ({nombre}) ya ha terminado (zombie); no hay nada que optimizar.")
    inicio = _inicio_proceso(pid)
    if inicio is None:
        raise OptimizadorError(f"no existe ningún proceso con PID {pid}.")
    return nombre, inicio


def _hilos(pid):
    """TID de cada hilo del proceso (el principal incluido), ordenados. Vacío si no se pueden leer."""
    try:
        return sorted(int(t) for t in os.listdir(os.path.join(PROC_DIR, str(pid), "task")) if t.isdigit())
    except OSError:
        return []


def _planificacion(tid):
    """Nice, política y prioridad de un hilo. None si el hilo ya no existe."""
    try:
        return {"nice": os.getpriority(os.PRIO_PROCESS, tid),
                "politica": os.sched_getscheduler(tid),
                "prio": os.sched_getparam(tid).sched_priority}
    except ProcessLookupError:
        return None
    except OSError as e:
        raise OptimizadorError(f"no se puede leer el hilo {tid}: {e.strerror or e}.")


def _renice(tid, valor):
    return ejecutar(["renice", "-n", str(valor), "-p", str(tid)])


def _podar_procesos(datos):
    """Quita del estado los procesos que ya no existen o cuyo PID se ha reutilizado."""
    for pid in list(datos["procesos"]):
        if _inicio_proceso(int(pid)) != datos["procesos"][pid]["inicio"]:
            del datos["procesos"][pid]


def _restaurar_proceso(hilos):
    """Devuelve cada hilo a su política y a su nice originales. Devuelve la lista de fallos."""
    fallos = []
    for tid_txt, original in hilos.items():
        tid = int(tid_txt)
        try:
            actual = _planificacion(tid)
            if actual is None:
                continue  # el hilo ya terminó
            if actual["politica"] != original["politica"]:
                bandera = FLAG_POLITICA.get(original["politica"])
                if bandera is None:
                    raise OptimizadorError(f"no se sabe devolver la política {original['politica']} al hilo {tid}.")
                r = ejecutar(["chrt", bandera, "-p", str(original["prio"]), str(tid)])
                if r.returncode != 0 and _planificacion(tid) is not None:
                    raise OptimizadorError(f"chrt no pudo devolver la política del hilo {tid}: {_detalle(r)}")
            if actual["nice"] != original["nice"]:
                r = _renice(tid, original["nice"])
                if r.returncode != 0 and _planificacion(tid) is not None:
                    raise OptimizadorError(f"renice no pudo devolver el nice del hilo {tid}: {_detalle(r)}")
        except OptimizadorError as e:
            fallos.append(str(e))
    return fallos


# --------------------------------------------------------------------------- CPU
def _rutas_governor():
    """Ruta de scaling_governor de cada núcleo que tiene control de frecuencia."""
    return sorted(glob.glob(os.path.join(CPU_DIR, "cpu[0-9]*", "cpufreq", "scaling_governor")))


def tiene_control_frecuencia():
    """True si el equipo expone el governor de la CPU (no necesita root, no escribe nada)."""
    return bool(_rutas_governor())


def _nombre_cpu(ruta_governor):
    return os.path.basename(os.path.dirname(os.path.dirname(ruta_governor)))


def _governors_disponibles(ruta_governor):
    ruta = os.path.join(os.path.dirname(ruta_governor), "scaling_available_governors")
    try:
        with open(ruta, encoding="utf-8") as f:
            return f.read().split()
    except OSError:
        raise OptimizadorError(
            f"no se sabe qué governors ofrece {_nombre_cpu(ruta_governor)} (falta scaling_available_governors); "
            "no se ha cambiado nada.")


def _escribible(ruta):
    """Permisos del governor. Como root os.access dice que sí incluso con modo solo lectura,
    así que también se miran los bits de escritura del modo."""
    try:
        modo = os.stat(ruta).st_mode
    except OSError:
        return False
    return bool(modo & 0o222) and os.access(ruta, os.W_OK)


def activar_modo_rendimiento():
    """Pone 'performance' en todos los núcleos. Devuelve cuántos se cambiaron.

    Orden de trabajo: 1) comprobar que todos los núcleos ofrecen 'performance' y son escribibles;
    2) guardar el governor original de cada uno ANTES de escribir; 3) escribir. Si una escritura
    falla a mitad, cada núcleo ya cambiado vuelve a su valor anterior. El estado guardado solo se
    mantiene si esa vuelta atrás también falla, y entonces el mensaje lo dice.
    """
    requiere_root()
    rutas = _rutas_governor()
    if not rutas:
        raise OptimizadorError(
            "este equipo no expone control de frecuencia de la CPU (pasa en muchas máquinas virtuales); "
            "no se ha cambiado nada.")
    for ruta in rutas:
        if "performance" not in _governors_disponibles(ruta):
            raise OptimizadorError(f"{_nombre_cpu(ruta)} no ofrece el governor 'performance'; no se ha cambiado nada.")
        if not _escribible(ruta):
            raise OptimizadorError(f"el governor de {_nombre_cpu(ruta)} no se puede escribir; no se ha cambiado nada.")

    guardado = _cargar_estado()
    _podar_procesos(guardado)
    previos = dict(guardado["governors"])  # lo que había guardado antes de esta llamada
    originales = dict(previos)
    for ruta in rutas:
        if ruta not in originales:  # solo el original de la primera activación: no se pierde si se repite
            originales[ruta] = leer(ruta)
    guardado["governors"] = originales
    _persistir(guardado)  # antes de escribir: si el proceso se corta, el original queda guardado

    anteriores, escritos = {}, []
    try:
        for ruta in rutas:
            anteriores[ruta] = leer(ruta)
            escribir(ruta, "performance")
            escritos.append(ruta)
    except OptimizadorError as e:
        motivo = str(e)
    else:
        return len(rutas)

    # Fallo a mitad: cada núcleo ya cambiado vuelve a como estaba justo antes de esta llamada.
    no_devueltos = []
    for ruta in escritos:
        try:
            escribir(ruta, anteriores[ruta])
        except OptimizadorError as e:
            no_devueltos.append(f"{_nombre_cpu(ruta)} ({e})")
    if no_devueltos:
        raise OptimizadorError(
            f"{motivo} No se pudieron devolver estos núcleos: {'; '.join(no_devueltos)}. Quedan pendientes: "
            "ejecuta 'sudo infinity-juegos revertir' para devolver su governor original.")
    guardado["governors"] = previos
    _persistir(guardado)
    if escritos:
        raise OptimizadorError(f"{motivo} Se han devuelto los {len(escritos)} núcleos ya cambiados; "
                               "no ha quedado ningún cambio.")
    raise OptimizadorError(f"{motivo} No se ha cambiado nada.")


def revertir_optimizacion():
    """Deshace lo guardado: governor de cada núcleo, y nice y política de cada hilo optimizado.

    Sin nada guardado no toca nada. Lo que no se puede deshacer se queda guardado para reintentar.
    Devuelve un resumen: {"nada", "governors", "procesos", "omitidos", "avisos"}.
    """
    requiere_root()
    guardado = _cargar_estado()
    if not guardado["governors"] and not guardado["procesos"]:
        return {"nada": True, "governors": 0, "procesos": 0, "omitidos": 0, "avisos": []}

    avisos, fallos = [], []
    n_gov = n_proc = omitidos = 0
    rutas = _rutas_governor()
    if guardado["governors"] and not rutas:
        avisos.append("este equipo no tiene control de frecuencia: no hay governor que devolver; "
                      "se limpia el estado pendiente.")
    pendiente_gov = {}
    for ruta, original in guardado["governors"].items():
        if ruta not in rutas:
            continue  # núcleo que ya no existe, o equipo sin control de frecuencia
        try:
            escribir(ruta, original)
            n_gov += 1
        except OptimizadorError as e:
            pendiente_gov[ruta] = original
            fallos.append(str(e))

    pendiente_proc = {}
    for pid, datos in guardado["procesos"].items():
        if _inicio_proceso(int(pid)) != datos["inicio"]:
            omitidos += 1  # ya no existe (o su PID se reutilizó): no se toca
            continue
        errores = _restaurar_proceso(datos["hilos"])
        if errores:
            pendiente_proc[pid] = datos
            fallos.extend(errores)
        else:
            n_proc += 1

    guardado["governors"], guardado["procesos"] = pendiente_gov, pendiente_proc
    _persistir(guardado)
    if fallos:
        raise OptimizadorError(
            f"revertido a medias: {n_gov} núcleos y {n_proc} procesos devueltos. No se pudo: "
            f"{'; '.join(fallos)}. Lo pendiente sigue guardado: vuelve a ejecutar 'sudo infinity-juegos revertir'.")
    return {"nada": False, "governors": n_gov, "procesos": n_proc, "omitidos": omitidos, "avisos": avisos}


# --------------------------------------------------------------------------- memoria
def _meminfo():
    """Memoria en MB según /proc/meminfo: disponible (MemAvailable), libre (MemFree) y caché (Cached)."""
    valores = {}
    try:
        with open(MEMINFO, encoding="utf-8") as f:
            for linea in f:
                clave, _, resto = linea.partition(":")
                if clave in ("MemAvailable", "MemFree", "Cached"):
                    valores[clave] = int(resto.split()[0]) // 1024
    except (OSError, ValueError, IndexError):
        raise OptimizadorError("no se pudo leer la memoria de /proc/meminfo.")
    if len(valores) < 3:
        raise OptimizadorError("/proc/meminfo no tiene los campos de memoria esperados.")
    return {"disponible": valores["MemAvailable"], "libre": valores["MemFree"], "cache": valores["Cached"]}


def liberar_ram():
    """Libera la caché de páginas limpias (drop_caches=1), con sync antes para volcar lo pendiente.

    Devuelve la memoria antes y después. La caché ya cuenta como memoria disponible (MemAvailable),
    así que soltarla no la aumenta: solo obliga a releer del disco lo que se necesite después.
    """
    requiere_root()
    antes = _meminfo()
    r = ejecutar(["sync"])
    if r.returncode != 0:
        raise OptimizadorError("sync falló; no se libera la caché para no perder datos pendientes.")
    escribir(DROP_CACHES, "1")
    return {"antes": antes, "despues": _meminfo()}


# --------------------------------------------------------------------------- optimizar
def optimizar_proceso(pid, tiempo_real=False):
    """Pone nice -10 en todos los hilos del proceso. Con tiempo_real=True, además SCHED_RR en el
    hilo principal (chrt). Antes de tocar nada guarda el nice y la política originales de cada hilo.

    Un hilo que desaparece durante la operación se omite y se cuenta. Si el PID cambia de dueño
    mientras se trabaja (se comprueba la hora de inicio antes y después), se deshace lo hecho.
    Queda una ventana de microsegundos entre la última comprobación y renice que no se puede
    cerrar del todo sin pidfd; la comprobación la reduce a eso.
    """
    requiere_root()
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0:
        raise OptimizadorError("el PID tiene que ser un número entero positivo.")
    nombre, inicio = _comprobar_proceso(pid)

    clave = str(pid)
    guardado = _cargar_estado()
    _podar_procesos(guardado)
    previo = guardado["procesos"].get(clave)
    hilos = dict(previo["hilos"]) if previo else {}  # si ya estaba optimizado, se conserva el original
    for tid in _hilos(pid):
        if str(tid) not in hilos:
            actual = _planificacion(tid)
            if actual is not None:
                hilos[str(tid)] = actual
    if clave not in hilos:
        raise OptimizadorError(f"no se pueden leer los hilos del proceso {pid}; no se ha cambiado nada.")
    if _inicio_proceso(pid) != inicio:
        raise OptimizadorError(f"el proceso {pid} cambió mientras se preparaba; no se ha cambiado nada.")

    guardado["procesos"][clave] = {"nombre": nombre, "inicio": inicio, "hilos": hilos}
    _persistir(guardado)  # guardar antes de tocar nada

    orden = sorted(hilos, key=lambda t: (t != clave, int(t)))  # el principal primero
    antes, omitidos = {}, 0
    try:
        for tid_txt in orden:
            tid = int(tid_txt)
            actual = _planificacion(tid)
            if actual is None:
                omitidos += 1
                continue
            r = _renice(tid, NICE_JUEGO)
            if r.returncode != 0:
                if _planificacion(tid) is None:
                    omitidos += 1  # el hilo acabó justo ahora
                    continue
                raise OptimizadorError(f"renice falló en el hilo {tid}: {_detalle(r)}")
            antes[tid] = actual["nice"]
        if _inicio_proceso(pid) != inicio:
            raise OptimizadorError(f"el PID {pid} cambió de dueño mientras se optimizaba (el proceso terminó "
                                   "y otro ocupó su PID); se ha deshecho lo cambiado.")
    except OptimizadorError:
        for tid, nice in antes.items():
            try:
                _renice(tid, nice)
            except OptimizadorError:
                pass
        if previo is None:
            guardado["procesos"].pop(clave, None)
            _persistir(guardado)
        raise

    resultado = {"pid": pid, "proceso": nombre, "nice": NICE_JUEGO, "hilos": len(antes),
                 "omitidos": omitidos, "tiempo_real": False, "avisos": []}
    if tiempo_real:
        try:
            r = ejecutar(["chrt", "-r", "-p", str(PRIORIDAD_TIEMPO_REAL), str(pid)])
            if r.returncode == 0:
                resultado["tiempo_real"] = True
            else:
                resultado["avisos"].append(
                    f"no se pudo dar tiempo real al hilo principal: {_detalle(r)}. El nice sí está aplicado.")
        except OptimizadorError as e:
            resultado["avisos"].append(f"tiempo real no disponible: {e} El nice sí está aplicado.")
    return resultado


# --------------------------------------------------------------------------- estado (solo lectura)
def estado():
    """Qué está cambiado de verdad, sin tocar nada (no necesita root).

    governors: valor actual de cada núcleo; cambiados: cuántos difieren del original guardado.
    procesos: procesos optimizados que siguen vivos. activado: True solo si hay un cambio real
    (un governor distinto del original o algún proceso optimizado). pendiente: hay estado guardado,
    aunque no haya cambios reales; 'revertir' lo limpia.
    """
    governors = {ruta: leer(ruta) for ruta in _rutas_governor()}
    guardado = _cargar_estado()
    cambiados = sum(1 for ruta, valor in governors.items()
                    if ruta in guardado["governors"] and guardado["governors"][ruta] != valor)
    procesos = [{"pid": int(pid), "nombre": datos["nombre"]}
                for pid, datos in guardado["procesos"].items()
                if _inicio_proceso(int(pid)) == datos["inicio"]]
    return {
        "governors": governors,
        "cambiados": cambiados,
        "procesos": procesos,
        "pendiente": bool(guardado["governors"] or guardado["procesos"]),
        "activado": cambiados > 0 or bool(procesos),
    }


# --------------------------------------------------------------------------- línea de órdenes
def main(argv=None):
    parser = argparse.ArgumentParser(description="Optimizador de juegos de Infinity OS.")
    sub = parser.add_subparsers(dest="orden", required=True)
    sub.add_parser("activar", help="governor 'performance' en todos los núcleos")
    sub.add_parser("liberar-ram", help="liberar la caché de páginas (no añade memoria disponible)")
    op = sub.add_parser("optimizar", help="nice -10 en todos los hilos de un proceso")
    op.add_argument("pid", type=int)
    op.add_argument("--tiempo-real", action="store_true",
                    help="además SCHED_RR en el hilo principal (riesgo: puede dejar sin CPU al escritorio)")
    sub.add_parser("revertir", help="deshacer la activación: governor, nice y política originales")
    sub.add_parser("estado", help="ver el estado actual (no necesita root)")
    args = parser.parse_args(argv)

    try:
        if args.orden == "activar":
            print(f"CPU en modo rendimiento en {activar_modo_rendimiento()} núcleos.")
        elif args.orden == "liberar-ram":
            r = liberar_ram()
            a, d = r["antes"], r["despues"]
            print(f"Caché de páginas: {a['cache']} → {d['cache']} MB. Memoria libre: {a['libre']} → {d['libre']} MB. "
                  f"Memoria disponible: {a['disponible']} → {d['disponible']} MB.")
            print("Liberar la caché no aumenta la memoria disponible: la caché ya cuenta como disponible, "
                  "y lo que se suelte se volverá a leer del disco.")
        elif args.orden == "optimizar":
            r = optimizar_proceso(args.pid, tiempo_real=args.tiempo_real)
            texto = f"Proceso {r['pid']} ({r['proceso']}): nice {r['nice']} en {r['hilos']} hilos"
            if r["omitidos"]:
                texto += f" ({r['omitidos']} ya no existían)"
            texto += ", tiempo real en el hilo principal." if r["tiempo_real"] else "."
            print(texto)
            for aviso in r["avisos"]:
                print(f"Aviso: {aviso}", file=sys.stderr)
        elif args.orden == "revertir":
            r = revertir_optimizacion()
            if r["nada"]:
                print("No hay ninguna activación que revertir: no se ha tocado nada.")
            else:
                partes = []
                if r["governors"]:
                    partes.append(f"governor restaurado en {r['governors']} núcleos")
                if r["procesos"]:
                    partes.append(f"{r['procesos']} procesos con su prioridad original")
                if r["omitidos"]:
                    partes.append(f"{r['omitidos']} ya no existían y no se tocaron")
                print("Modo juego desactivado" + (": " + "; ".join(partes) if partes else "") + ".")
                for aviso in r["avisos"]:
                    print(f"Aviso: {aviso}", file=sys.stderr)
        elif args.orden == "estado":
            e = estado()
            for ruta, valor in e["governors"].items():
                print(f"{_nombre_cpu(ruta)}: {valor}")
            if not e["governors"]:
                print("Este equipo no expone control de frecuencia de la CPU.")
            for proceso in e["procesos"]:
                print(f"Proceso {proceso['pid']} ({proceso['nombre']}): optimizado.")
            if e["activado"]:
                print("Modo juego activo.")
            elif e["pendiente"]:
                print("Hay un estado guardado sin cambios reales; 'revertir' lo limpia.")
            else:
                print("Modo juego desactivado.")
    except OptimizadorError as e:
        print(f"game_optimizer: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
