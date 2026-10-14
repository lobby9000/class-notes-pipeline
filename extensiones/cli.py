"""Subcomandos nuevos de `pizza` (y de `apuntes`): pregunta, cuenta, cotiza y despensa."""

from __future__ import annotations

import argparse

from .comun import ErrorExtension

__all__ = ["ErrorExtension", "registrar_comandos", "ejecutar"]


def _subcomandos(parser: argparse.ArgumentParser):
    for accion in parser._actions:  # el parser de apuntes.py ya tiene sus subcomandos; se agregan ahí
        if isinstance(accion, argparse._SubParsersAction):
            return accion
    return None


def registrar_comandos(parser: argparse.ArgumentParser) -> None:
    sub = _subcomandos(parser)
    if sub is None:
        return
    sp = sub.add_parser("pregunta", aliases=["preguntar"],
                        help="pregúntale a tus clases: responde citando clase y minuto")
    sp.add_argument("pregunta", nargs="+", help='entre comillas, ej. "¿cómo se calcula el ISR?"')
    sp.add_argument("--clase", help='busca solo en una nota, ej. "24 septiembre"')
    sp.add_argument("--modelo", help="opus, sonnet, haiku o un id de la API (default: [claude.modelos] pregunta)")
    sp.add_argument("--fragmentos", type=int, help="cuántos fragmentos recibe Claude de entrada (default: 8)")
    sp.add_argument("--ver-fragmentos", action="store_true", help="muestra los fragmentos que recibió Claude")
    sp.set_defaults(accion="pregunta", accion_extension="pregunta")

    sc = sub.add_parser("cuenta", aliases=["costos"], help="tokens, costo y latencia de cada llamada a la API")
    sc.add_argument("--ultimas", type=int, default=15, help="cuántas llamadas mostrar (default: 15)")
    sc.set_defaults(accion="cuenta", accion_extension="cuenta")

    se = sub.add_parser("cotiza", aliases=["estimar"],
                        help="cuánto costaría una nota con Opus, Sonnet o Haiku antes de hacerla")
    se.add_argument("sesion", help='parte del nombre o la fecha, ej. "24 septiembre" o 2026-09-24')
    se.set_defaults(accion="cotiza", accion_extension="cotiza")

    sd = sub.add_parser("despensa", aliases=["indexar"], help="actualiza el índice de notas y transcripciones")
    sd.add_argument("--reconstruir", action="store_true", help="borra el índice y lo arma desde cero")
    sd.set_defaults(accion="despensa", accion_extension="despensa")


def cmd_despensa(cfg: dict, reconstruir: bool = False, despensa=None) -> int:
    from .indice import Despensa
    despensa = despensa or Despensa(cfg)
    print("Revisando la despensa...", flush=True)
    r = despensa.actualizar(reconstruir=reconstruir)
    print(f"Listo: {r['notas']} notas y {r['fragmentos']:,} fragmentos ({r['nuevas']} nuevas, "
          f"{r['actualizadas']} actualizadas, {r['borradas']} borradas). Embeddings: {despensa.modelo}")
    return 0


def ejecutar(args, cfg: dict) -> int:
    accion = args.accion_extension
    if accion == "pregunta":
        from .preguntas import cmd_pregunta
        return cmd_pregunta(cfg, " ".join(args.pregunta), clase=args.clase, modelo=args.modelo,
                            k=args.fragmentos, ver_fragmentos=args.ver_fragmentos)
    if accion == "cuenta":
        from .cuenta import cmd_cuenta
        return cmd_cuenta(cfg, args.ultimas)
    if accion == "cotiza":
        from .cuenta import cmd_cotiza
        return cmd_cotiza(cfg, args.sesion)
    if accion == "despensa":
        return cmd_despensa(cfg, args.reconstruir)
    raise ErrorExtension(f"Comando desconocido: {accion}")
