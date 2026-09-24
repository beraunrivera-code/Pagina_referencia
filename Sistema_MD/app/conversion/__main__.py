from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

from .documents import parse_pages
from .diagnostics import export_failure_report, record_failure, repair_safe, run_diagnostics, resolve_failure
from .jobs import import_answer, prepare_job
from .pipeline import NATIVE_KINDS, convert_native, convert_text, detect, doctor, import_pages, inventory, prepare_pdf
from .selector import choose_and_process, use_path
from .storage import export_index, report
from .workflow import WorkQueue
from .retrieval import consult
from .providers import CLI_PROFILES, PROVIDERS, execute_job, provider_status
from .credentials import ENV_NAMES, save_key
from .batches import plan_queue, run_batch, batch_status
from .ai_batches import plan_ai_batch, run_ai_batch, ai_batch_status
from .runtime_paths import data_root


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="Conversión MD local. Ningún comando llama a IA automáticamente.")
    parser.add_argument("--salida", type=Path, help="Carpeta de datos; por defecto la configurada en esta PC")
    sub = parser.add_subparsers(dest="command", required=True)
    completeness = sub.add_parser("estado-implementacion", help="Comprueba cobertura de las 62 tareas del plan")
    completeness.add_argument("--exigir-completo", action="store_true")
    sub.add_parser("motores-locales", help="Inspecciona motores integrados; no instala ni descarga modelos")
    engine_config = sub.add_parser("configurar-motor-local", help="Selecciona el Python local de un motor")
    engine_config.add_argument("motor", choices=("docling", "markitdown"))
    engine_config.add_argument("--python", type=Path, required=True)
    engine_config.add_argument("--modelos", type=Path)
    engine_run = sub.add_parser("convertir-motor-local", help="Integra el resultado local en la biblioteca común; sin API")
    engine_run.add_argument("ruta", type=Path)
    engine_run.add_argument("--motor", choices=("docling", "markitdown"), required=True)
    engine_run.add_argument("--paginas", help="Rango PDF explícito para Docling, por ejemplo 1-3")
    engine_run.add_argument("--timeout", type=int, default=180)
    sub.add_parser("planificar-lote", help="Planifica la cola: local, duplicado o pendiente; no convierte")
    batch = sub.add_parser("procesar-lote", help="Ejecuta/reanuda un plan local; nunca llama a IA")
    batch.add_argument("--lote", help="ID del plan; por defecto el último")
    batch.add_argument("--limite", type=int, default=20)
    batch.add_argument("--reintentar-errores", action="store_true")
    state = sub.add_parser("estado-lote", help="Lee el progreso persistido del lote local")
    state.add_argument("--lote")
    ai_plan = sub.add_parser("planificar-lote-ia", help="Crea plan de páginas; no llama a IA")
    ai_plan.add_argument("documento", type=Path, help="Paquete preparado dentro de datos/documentos")
    ai_plan.add_argument("--proveedor", choices=PROVIDERS, required=True)
    ai_plan.add_argument("--modelo", required=True)
    ai_plan.add_argument("--paginas", help="Opcional: 1-3 o 1,4; por defecto todas con imagen")
    ai_plan.add_argument("--max-llamadas", type=int, default=20)
    ai_plan.add_argument("--presupuesto-tokens-reportados", type=int, default=200000)
    ai_plan.add_argument("--max-tokens", type=int, default=8192)
    ai_plan.add_argument("--timeout", type=int, default=120)
    ai_plan.add_argument("--perfil-clave", type=int, choices=range(1,6), default=1)
    ai_plan.add_argument("--perfil-cli", choices=CLI_PROFILES, default="principal")
    ai_run = sub.add_parser("procesar-lote-ia", help="Previsualiza; --enviar autoriza consumo página a página")
    ai_run.add_argument("--lote", help="ID; por defecto el último")
    ai_run.add_argument("--enviar", action="store_true")
    ai_state = sub.add_parser("estado-lote-ia", help="Lee progreso, gasto comunicado y resultado unido")
    ai_state.add_argument("--lote")
    sub.add_parser("proveedores", help="Detecta CLI y claves sin revelar secretos ni llamar a IA")
    key = sub.add_parser("configurar-clave", help="Pide una clave oculta y la guarda en Windows")
    key.add_argument("proveedor", choices=tuple(ENV_NAMES))
    key.add_argument("--perfil-clave", type=int, choices=range(1,6), default=1)
    login = sub.add_parser("conectar", help="Abre el acceso oficial; no envía documentos ni prompts")
    login.add_argument("proveedor", choices=tuple(p for p in PROVIDERS if p.endswith('-cli')))
    login.add_argument("--perfil-cli", choices=CLI_PROFILES, default="principal")
    run = sub.add_parser("ejecutar-ia", help="Previsualiza un encargo; --enviar consume cuota")
    run.add_argument("encargo", type=Path)
    # Por defecto Gemini 3.8 Flash: para imagen es el más barato (2.500-4.300 tokens por página
    # contra ~5.600 de DeepSeek y ~50.700 de Antigravity), el más rápido (12 s) y su bolsa son
    # claves gratuitas — lo gratis se gasta antes que lo pagado. DeepSeek queda de relevo cuando
    # la cuota se agota, que es lo que le pasó a Gemini el 13-sep (19 de 23 páginas).
    # Elegir proveedor sigue siendo explícito con --proveedor; el default NO envía por sí solo.
    run.add_argument("--proveedor", choices=PROVIDERS, default="gemini-api")
    run.add_argument("--modelo", default="gemini-3.8-flash")
    # 8192: con 4096 un modelo de razonamiento puede gastar el presupuesto pensando y devolver
    # contenido vacío (medido el 2026-09-13: 3.783 tokens de razonamiento en una página simple).
    run.add_argument("--max-tokens", type=int, default=8192)
    run.add_argument("--timeout", type=int, default=120)
    run.add_argument("--enviar", action="store_true")
    run.add_argument("--perfil-clave", type=int, choices=range(1,6), default=1)
    run.add_argument("--perfil-cli", choices=CLI_PROFILES, default="principal")
    query = sub.add_parser("consultar", help="Devuelve fragmentos y procedencia sin releer nativos")
    query.add_argument("texto")
    query.add_argument("--limite", type=int, default=5)
    query.add_argument("--max-caracteres", type=int, default=4000)
    ranking = sub.add_parser("reordenar-typesafe", help="Vista previa de fragmentos; enviar exige ID confirmado")
    ranking.add_argument("texto")
    ranking.add_argument("--limite", type=int, choices=range(1, 11), default=5)
    ranking.add_argument("--max-caracteres", type=int, default=6000)
    ranking.add_argument("--modelo", default="jev-1.13.0")
    ranking.add_argument("--enviar", action="store_true")
    ranking.add_argument("--confirmar-id", help="ID exacto devuelto por la vista previa")
    enqueue = sub.add_parser("encolar", help="Añade un archivo o carpeta a la cola persistente")
    enqueue.add_argument("ruta", type=Path)
    enqueue.add_argument("--limite", type=int, default=200)
    sub.add_parser("seleccionar", help="Abre el selector visual de carpeta o archivo")
    route = sub.add_parser("usar-ruta", help="Guarda y procesa una carpeta o archivo indicado")
    route.add_argument("ruta", type=Path)
    route.add_argument("--paginas", help="Solo para PDF: 1-3 o 1,4,7")
    route.add_argument("--limite", type=int, default=200, help="Máximo de archivos al inventariar una carpeta")
    sub.add_parser("doctor", help="Disponibilidad local, sin leer claves ni gastar cuota")
    scan = sub.add_parser("inventariar", help="Identifica por contenido y calcula SHA-256")
    scan.add_argument("ruta", type=Path)
    scan.add_argument("--limite", type=int, default=200)
    convert = sub.add_parser("convertir", help="Convierte TXT/MD y Word/Excel/PowerPoint/Visio/DWG/DXF a paquete trazable, sin IA")
    convert.add_argument("ruta", type=Path)
    pages = sub.add_parser("importar-paginas", help="Integra páginas MD ya producidas por Claude/Gemini/DeepSeek")
    pages.add_argument("carpeta", type=Path)
    pages.add_argument("--prefijo", required=True)
    pages.add_argument("--paginas", required=True)
    pages.add_argument("--motor", required=True)
    pages.add_argument("--titulo", required=True)
    pages.add_argument("--imagenes", type=Path)
    pages.add_argument("--contrato-referencia", type=Path)
    pdf = sub.add_parser("preparar-pdf", help="Texto y referencias PNG; no certifica OCR ni semántica")
    pdf.add_argument("ruta", type=Path)
    pdf.add_argument("--paginas", required=True)
    pdf.add_argument("--dpi", type=int, default=150)
    job = sub.add_parser("preparar-encargo", help="Crea paquete aislado, sin enviar a IA")
    job.add_argument("documento", type=Path, help="Carpeta de un documento ya importado/preparado")
    job.add_argument("--pagina", type=int, required=True)
    answer = sub.add_parser("importar-respuesta", help="Valida JSON de un motor contra su encargo")
    answer.add_argument("encargo", type=Path)
    answer.add_argument("respuesta", type=Path)
    answer.add_argument("--motor", required=True)
    sub.add_parser("informe", help="Estados e integridad real de los archivos")
    sub.add_parser("indice", help="Genera un índice Markdown con enlaces a los documentos")
    sub.add_parser("diagnostico", help="Comprueba el sistema sin llamar a IA")
    sub.add_parser("reparar", help="Repara solo índice e infraestructura derivada")
    sub.add_parser("fallos", help="Exporta el historial de errores diagnosticados")
    resolve = sub.add_parser("resolver-fallo", help="Registra corrección y evidencia de un fallo")
    resolve.add_argument("firma")
    resolve.add_argument("--causa", required=True)
    resolve.add_argument("--evidencia", required=True)
    search = sub.add_parser("buscar", help="Encuentra las páginas pertinentes en derivados locales")
    search.add_argument("texto")
    args = parser.parse_args(argv)
    try:
        if args.command == "motores-locales":
            from .local_engines import probe_engines
            print(json.dumps(probe_engines(), ensure_ascii=False, indent=2))
            return 0
        if args.command == "configurar-motor-local":
            from .local_engines import configure_engine
            print(json.dumps(configure_engine(args.motor, args.python, args.modelos), ensure_ascii=False, indent=2))
            return 0
        if args.command == "estado-implementacion":
            from .implementation_status import implementation_report
            result = implementation_report()
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 2 if args.exigir_completo and not result['complete'] else 0
        args.salida = args.salida or data_root(Path(__file__).resolve().parents[1])
        if args.command == "convertir-motor-local":
            from .local_engines import convert_with_engine
            result = convert_with_engine(args.salida, args.ruta, args.motor,
                        pages=parse_pages(args.paginas) if args.paginas else None, timeout=args.timeout)
        elif args.command == "reordenar-typesafe":
            from .semantic_judgments import rerank
            local = consult(args.salida, args.texto, args.limite, args.max_caracteres)
            result = rerank(args.salida, local, send=args.enviar,
                            expected_id=args.confirmar_id, model=args.modelo)
        elif args.command == "planificar-lote":
            result = plan_queue(args.salida)
        elif args.command == "procesar-lote":
            result = run_batch(args.salida, args.lote, limit=args.limite, retry_errors=args.reintentar_errores)
        elif args.command == "estado-lote":
            result = batch_status(args.salida, args.lote)
        elif args.command == "planificar-lote-ia":
            result = plan_ai_batch(args.salida, args.documento, args.proveedor, args.modelo,
                                   pages=parse_pages(args.paginas) if args.paginas else None,
                                   call_budget=args.max_llamadas,
                                   reported_token_budget=args.presupuesto_tokens_reportados,
                                   max_tokens=args.max_tokens, timeout=args.timeout,
                                   key_slot=args.perfil_clave, cli_profile=args.perfil_cli)
        elif args.command == "procesar-lote-ia":
            result = run_ai_batch(args.salida, args.lote, send=args.enviar)
        elif args.command == "estado-lote-ia":
            result = ai_batch_status(args.salida, args.lote)
        elif args.command == "proveedores":
            result = provider_status()
        elif args.command == "conectar":
            from .access import launch_login
            result = launch_login(args.salida, args.proveedor, args.perfil_cli)
        elif args.command == "configurar-clave":
            import getpass
            if not sys.stdin.isatty():
                raise ValueError("Abre una terminal interactiva para introducir la clave de forma oculta")
            save_key(args.proveedor, getpass.getpass("Clave API (oculta): "), args.perfil_clave)
            result = {"status": "guardada_en_windows", "provider": args.proveedor, "external_calls": 0}
        elif args.command == "ejecutar-ia":
            result = execute_job(args.salida, args.encargo, args.proveedor, args.modelo,
                                 send=args.enviar, max_tokens=args.max_tokens, timeout=args.timeout,
                                 key_slot=args.perfil_clave, cli_profile=args.perfil_cli)
        elif args.command == "consultar":
            result = consult(args.salida, args.texto, args.limite, args.max_caracteres)
        elif args.command == "encolar":
            result = WorkQueue(args.salida).add(args.ruta, args.limite)
        elif args.command == "seleccionar":
            result = choose_and_process(args.salida)
        elif args.command == "usar-ruta":
            result = use_path(args.salida, args.ruta, args.paginas, args.limite)
        elif args.command == "doctor":
            result = doctor()
        elif args.command == "inventariar":
            result = inventory(args.ruta, args.limite, args.salida)
        elif args.command == "convertir":
            if args.ruta.is_file() and detect(args.ruta) in NATIVE_KINDS:
                result = convert_native(args.salida, args.ruta)
            else:
                result = convert_text(args.salida, args.ruta)
        elif args.command == "importar-paginas":
            result = import_pages(args.salida, args.carpeta, args.prefijo, parse_pages(args.paginas),
                                  args.motor, args.titulo, args.imagenes, args.contrato_referencia)
        elif args.command == "preparar-pdf":
            result = prepare_pdf(args.salida, args.ruta, parse_pages(args.paginas), args.dpi)
        elif args.command == "preparar-encargo":
            result = prepare_job(args.salida, args.documento, args.pagina)
        elif args.command == "importar-respuesta":
            result = import_answer(args.salida, args.encargo, args.respuesta, args.motor)
        elif args.command == "indice":
            result = export_index(args.salida)
        elif args.command == "diagnostico":
            result = run_diagnostics(args.salida)
        elif args.command == "reparar":
            result = repair_safe(args.salida)
        elif args.command == "fallos":
            result = export_failure_report(args.salida)
        elif args.command == "resolver-fallo":
            result = resolve_failure(args.salida, args.firma, args.causa, args.evidencia)
        else:
            result = report(args.salida, args.texto if args.command == "buscar" else None)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        if isinstance(result, dict) and result.get("ranking", {}).get("status") in {"fallo", "bloqueado"}:
            return 2
        if isinstance(result, dict) and (result.get("counts", {}).get("error") or result.get("status") in {
                "requiere_revision", "bloqueado", "resultado_dañado"}):
            return 2
        if isinstance(result, dict) and (result.get("status") in {"parcial", "ATENCION"} or result.get("errors")):
            return 2
        if isinstance(result, list) and any(not row["integrity_ok"] for row in result):
            return 2
        return 0
    except Exception as exc:
        try:
            failure = record_failure(args.salida, args.command or "cli", exc,
                                     {"arguments": [str(item) for item in (argv or sys.argv[1:])]})
        except Exception:
            failure = {"message": str(exc), "signature": "no-registrado"}
        print(json.dumps({"error": failure["message"], "signature": failure["signature"],
                          "status": "fallo_mapeado"}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
