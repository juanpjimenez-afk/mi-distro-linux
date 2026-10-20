"""La Tienda desde la terminal (sin ventana ni GTK): lo mismo que hace la ventana, para scripts y para probar.

    infinity-tienda listar
    infinity-tienda instalar <id> [--navegador chrome|brave] [--si]
    infinity-tienda quitar <id>
    infinity-tienda actualizar <id>

Usa el mismo Gestor que la ventana (cola, dependencias, espacio, pkexec para APT). Códigos de salida: 0 bien,
1 la operación falló, 2 uso incorrecto o hace falta una decisión tuya (--navegador / --si).
"""
import sys
import threading
from collections import namedtuple

from . import tienda_catalogo as C
from . import tienda_ops as O

USO = "infinity-tienda {listar|instalar|quitar|actualizar} [id] [--navegador ID] [--si]"
AYUDA = USO + """

  listar               las apps del catálogo y si están instaladas
  instalar <id>        instala una app (una WebApp sin navegador Chromium pide elegir uno)
  quitar <id>          la quita
  actualizar <id>      la actualiza (Flatpak y APT)

  --navegador ID       navegador que instalar antes si la WebApp lo necesita (chrome, brave)
  --si                 aceptar lo que pregunta la tienda: instalar con poco espacio y el navegador recomendado
  --help               esta ayuda
"""
COMANDOS = ("listar", O.INSTALAR, O.QUITAR, O.ACTUALIZAR)
Argumentos = namedtuple("Argumentos", "comando id navegador si catalogo")


class _Ayuda(Exception):
    pass


def interpretar(argv):
    """Los argumentos de la línea de órdenes. Lanza ValueError (mensaje en español) si no valen, o _Ayuda."""
    comando = ident = navegador = catalogo = None
    si = False
    it = iter(argv)
    for a in it:
        if a in ("-h", "--help"):
            raise _Ayuda()
        if a == "--si":
            si = True
        elif a in ("--navegador", "--catalogo"):
            try:
                valor = next(it)
            except StopIteration:
                raise ValueError(f"{a} necesita un valor") from None
            if a == "--navegador":
                navegador = valor
            else:
                catalogo = valor
        elif a.startswith("-"):
            raise ValueError(f"opción desconocida: {a}")
        elif comando is None:
            comando = a
        elif ident is None:
            ident = a
        else:
            raise ValueError(f"sobra un argumento: {a}")
    if comando is None:
        raise ValueError("falta el comando")
    if comando not in COMANDOS:
        raise ValueError(f"comando desconocido: {comando}")
    return Argumentos(comando, ident, navegador, si, catalogo)


def _estado_texto(e):
    if not e.instalada:
        return "sin instalar"
    if not e.funciona:
        return "instalada (sin navegador)"
    return "instalada" + (f" {e.version}" if e.version else "")


def listar(gestor, salida):
    for a in gestor.catalogo.apps:
        salida.write(f"{a.id:14} {a.etiqueta_tipo:8} {_estado_texto(gestor.estado(a.id)):26} {a.nombre}\n")
    return 0


def main(argv=None, gestor=None, salida=None, error=None):
    salida, error = salida or sys.stdout, error or sys.stderr
    try:
        args = interpretar(sys.argv[1:] if argv is None else argv)
    except _Ayuda:
        salida.write(AYUDA)
        return 0
    except ValueError as e:
        error.write(f"infinity-tienda: {e}.\nUso: {USO}\n")
        return 2
    if gestor is None:
        try:
            catalogo = C.cargar(args.catalogo or C.ruta_catalogo())
        except C.CatalogoError as e:
            error.write(f"infinity-tienda: {e}\n")
            return 1
        for p in catalogo.problemas:
            error.write(f"infinity-tienda: aviso: {p}\n")
        gestor = O.Gestor(catalogo)
    if args.comando == "listar":
        return listar(gestor, salida)
    if not args.id:
        error.write(f"infinity-tienda: falta el id de la app.\nUso: {USO}\n")
        return 2
    return _pedir(gestor, args, salida, error)


def _pedir(gestor, args, salida, error):
    entrada = gestor.catalogo.por_id.get(args.id)
    if entrada is None:
        error.write(f"infinity-tienda: «{args.id}» no está en el catálogo (mira «infinity-tienda listar»).\n")
        return 2
    terminado = threading.Event()
    ultimo = {"p": -1, "texto": ""}

    def al_cambiar(ident):
        t = gestor.trabajos.get(ident) if ident else None
        if t is None or not t.activo:
            return
        p = -1 if t.progreso is None else int(t.progreso * 100)
        if (p, t.texto) != (ultimo["p"], ultimo["texto"]):
            ultimo["p"], ultimo["texto"] = p, t.texto
            error.write(f"  {t.entrada.nombre}: {t.texto}" + (f" [{p}%]" if p >= 0 else "") + "\n")

    def al_terminar(trabajo):
        salida.write(trabajo.mensaje + "\n")
        resultados.append(trabajo)
        if not gestor.ocupado:
            terminado.set()

    resultados = []
    gestor.al_cambiar = al_cambiar
    gestor.al_terminar_trabajo = al_terminar
    r = gestor.pedir(args.id, args.comando, navegador=args.navegador, forzar=args.si)
    if r.estado == "falta_navegador":
        if not args.si:
            nombres = ", ".join(f"{n.id} ({n.nombre})" for n in r.opciones)
            error.write(f"infinity-tienda: {r.mensaje}\nInstala uno con --navegador ID (opciones: {nombres}) o acepta el "
                        f"recomendado con --si.\n")
            return 2
        r = gestor.pedir(args.id, args.comando, navegador=r.opciones[0].id, forzar=True)
    elif r.estado == "poco_espacio" and args.si:
        r = gestor.pedir(args.id, args.comando, navegador=args.navegador, forzar=True)
    if r.estado == "poco_espacio":
        error.write(f"infinity-tienda: {r.mensaje}\nSi quieres intentarlo igualmente, añade --si.\n")
        return 2
    if r.estado != "ok":
        error.write(f"infinity-tienda: {r.mensaje}\n")
        return 1 if r.estado == "error" else 2
    terminado.wait()
    return 0 if all(t.estado == "listo" for t in resultados) else 1


if __name__ == "__main__":
    sys.exit(main())
