"""FASE 2 · Ejecuta el pipeline de Sistema MD sobre cada archivo de stress_test_suite/.

Cada archivo se convierte en un proceso hijo aislado: un fallo nativo (segfault, bucle,
memoria) no detiene la batería y se mide por separado. Por archivo se registran tiempo,
pico de memoria (RSS máximo del hijo), advertencias del productor y de Python, y el
resultado frente a lo exigido en ``esperado.json``:

  ok                  publicado como paquete (y se esperaba convertir)
  rechazo_controlado  error explicado por el sistema (ValueError del contrato)
  sin_adaptador       el sistema no tiene ruta para ese formato
  excepcion           excepción cruda de una librería: NO es un rechazo controlado
  caida / timeout     el proceso murió o superó el tiempo

Salidas: output_stress/<archivo>/ (documento.md, derivados/, imagenes/, _resultado.json)
y conversion_stress.log (una línea JSON por archivo).

Uso:  python ejecutar_tortura.py [--suite stress_test_suite] [--salida output_stress]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import warnings

HERE = Path(__file__).resolve().parent
APP = HERE.parent / "app"
TIMEOUT = 300


def _convertir(biblioteca: Path, fuente: Path) -> dict:
    """Ruta pública del proyecto: ``convert_any`` si existe; si no, la de la CLI ``convertir``."""
    sys.path.insert(0, str(APP))
    from conversion import pipeline
    convert_any = getattr(pipeline, "convert_any", None)
    if convert_any is not None:
        return convert_any(biblioteca, fuente)
    tipo = pipeline.detect(fuente)
    if tipo in pipeline.NATIVE_KINDS:
        return pipeline.convert_native(biblioteca, fuente)
    if tipo == "pdf":
        import fitz
        with fitz.open(fuente) as documento:
            paginas = list(range(1, len(documento) + 1))
        return pipeline.prepare_pdf(biblioteca, fuente, paginas)
    # Igual que la CLI «convertir»: lo que no es nativo cae en convert_text, que rechaza
    # con ValueError. Para formatos sin ruta propia se informa como «sin_adaptador».
    if tipo not in {"md", "txt", "text"}:
        try:
            return pipeline.convert_text(biblioteca, fuente)
        except ValueError as error:
            raise LookupError(f"sin adaptador para «{tipo}»: {error}") from None
    return pipeline.convert_text(biblioteca, fuente)


def hijo(fuente: Path, biblioteca: Path, resultado: Path) -> int:
    """Proceso aislado: convierte un archivo y deja su resultado en JSON."""
    registro = {"archivo": fuente.name}
    with warnings.catch_warnings(record=True) as capturadas:
        warnings.simplefilter("always")
        try:
            publicado = _convertir(biblioteca, fuente)
            registro.update(estado="ok", salida=publicado.get("output"), estado_paquete=publicado.get("status"))
        except LookupError as error:
            # Sin ruta de conversión: para un archivo dañado equivale a un rechazo explicado.
            registro.update(estado="sin_adaptador", error=str(error), tipo_error=type(error).__name__)
        except ValueError as error:           # contrato del proyecto: rechazo explicado
            registro.update(estado="rechazo_controlado", error=str(error), tipo_error=type(error).__name__)
        except Exception as error:            # cualquier otra cosa es un fallo no controlado
            registro.update(estado="excepcion", error=str(error)[:500], tipo_error=type(error).__name__,
                            traza=traceback.format_exc(limit=6)[-2000:])
    registro["advertencias_python"] = sorted({f"{w.category.__name__}: {str(w.message)[:160]}"
                                              for w in capturadas
                                              if not issubclass(w.category, (DeprecationWarning, PendingDeprecationWarning))})
    resultado.write_text(json.dumps(registro, ensure_ascii=False), encoding="utf-8")
    return 0


def _copiar_paquete(origen: Path, destino: Path) -> dict:
    documento = json.loads((origen / "document.json").read_text(encoding="utf-8"))
    shutil.copytree(origen, destino, dirs_exist_ok=True)
    return {"advertencias_productor": documento.get("producer_warnings", []),
            "proveedor": documento.get("provider"), "tipo_entrada": documento.get("input_kind")}


def ejecutar(suite: Path, salida: Path) -> list[dict]:
    esperado = json.loads((suite / "esperado.json").read_text(encoding="utf-8"))
    if salida.exists():
        shutil.rmtree(salida)
    salida.mkdir(parents=True)
    biblioteca = salida / "_biblioteca"
    registros = []
    log = salida.parent / "conversion_stress.log"
    with log.open("w", encoding="utf-8") as bitacora:
        for nombre in sorted(esperado):
            fuente = suite / nombre
            destino = salida / nombre.replace(".", "_")
            destino.mkdir()
            with tempfile.TemporaryDirectory(prefix="tortura_") as temporal:
                resultado = Path(temporal) / "resultado.json"
                errores = (Path(temporal) / "stderr.txt").open("w+b")
                inicio = time.perf_counter()
                # stderr a archivo: una tubería llena bloquearía al hijo mientras se sondea wait4.
                proceso = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--hijo", str(fuente),
                                            "--biblioteca", str(biblioteca), "--resultado", str(resultado)],
                                           stdout=subprocess.DEVNULL, stderr=errores)
                limite = time.monotonic() + TIMEOUT
                estado_os, uso = None, None
                while time.monotonic() < limite:
                    pid, estado_os, uso = os.wait4(proceso.pid, os.WNOHANG)
                    if pid:
                        break
                    time.sleep(0.05)
                else:
                    proceso.kill()
                    _pid, estado_os, uso = os.wait4(proceso.pid, 0)
                    estado_os = "timeout"
                segundos = round(time.perf_counter() - inicio, 3)
                errores.seek(0)
                stderr = errores.read().decode("utf-8", "replace")[-1500:]
                errores.close()
                proceso.returncode = 0          # ya recogido con wait4
                if estado_os == "timeout":
                    registro = {"archivo": nombre, "estado": "timeout"}
                elif resultado.is_file():
                    registro = json.loads(resultado.read_text(encoding="utf-8"))
                else:
                    registro = {"archivo": nombre, "estado": "caida", "stderr": stderr,
                                "codigo_salida": os.waitstatus_to_exitcode(estado_os)}
            registro.update(familia=esperado[nombre]["familia"], esperado=esperado[nombre]["esperado"],
                            debe_contener=esperado[nombre].get("debe_contener", []),
                            segundos=segundos, pico_memoria_mb=round(uso.ru_maxrss / 1024, 1) if uso else None)
            if registro["estado"] == "ok" and registro.get("salida"):
                registro.update(_copiar_paquete(Path(registro["salida"]), destino))
            if registro["esperado"] == "convertir":
                registro["cumple"] = registro["estado"] == "ok"
            else:
                registro["cumple"] = registro["estado"] in {"rechazo_controlado", "sin_adaptador"}
            registro["fuente"] = str(fuente)
            (destino / "_resultado.json").write_text(json.dumps(registro, ensure_ascii=False, indent=2), encoding="utf-8")
            bitacora.write(json.dumps({"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), **{k: v for k, v in registro.items()
                                       if k != "traza"}}, ensure_ascii=False) + "\n")
            registros.append(registro)
            marca = "✔" if registro["cumple"] else "✘"
            print(f"{marca} {nombre:32} {registro['estado']:20} {segundos:7.2f}s "
                  f"{registro['pico_memoria_mb'] or 0:7.1f} MB  {registro.get('error', '')[:70]}")
    return registros


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--suite", type=Path, default=HERE / "stress_test_suite")
    parser.add_argument("--salida", type=Path, default=HERE / "output_stress")
    parser.add_argument("--hijo", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--biblioteca", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--resultado", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.hijo:
        return hijo(args.hijo.resolve(), args.biblioteca.resolve(), args.resultado)
    registros = ejecutar(args.suite.resolve(), args.salida.resolve())
    incumplen = [r["archivo"] for r in registros if not r["cumple"]]
    print(json.dumps({"archivos": len(registros), "cumplen": len(registros) - len(incumplen),
                      "incumplen": incumplen}, ensure_ascii=False))
    return 1 if incumplen else 0


if __name__ == "__main__":
    raise SystemExit(main())
