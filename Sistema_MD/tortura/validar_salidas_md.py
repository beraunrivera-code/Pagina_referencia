"""FASE 3 · Auditoría sintáctica estricta de las salidas de output_stress/.

Reglas de oro (cada incumplimiento es un ERROR; las advertencias no bloquean):

  A  Tablas: fila empieza y termina en «|»; mismo número de columnas en todas las filas;
     separador |---| inmediatamente bajo el encabezado; sin saltos crudos dentro de celdas
     (una fila partida aparece como fila sin cierre). A5: bloque de código sin cerrar,
     que se tragaría el resto del documento.
  B  Codificación: UTF-8 estricto sin BOM; sin NUL ni caracteres de control invisibles
     (C0 salvo \\t \\n \\r, DEL, C1, U+FFFE/U+FFFF). También en los CSV derivados.
  C  Excel: derivados/formulas.csv existe, cabecera hoja;celda;formula;valor_cacheado,
     referencias A1 válidas y, si el original es OOXML legible, exactamente las mismas
     fórmulas (hoja, celda, texto) que el libro.
  D  Enlaces e imágenes locales: todo ![..](ruta) y [..](ruta) relativo existe en disco.
  E  Ruido: ningún título con nombre interno de sistema (OEBPS, Part1, x_template, rIdN,
     rutas xl/ word/ ppt/, *.xml) ni escapes OOXML crudos (_x000D_).
  F  Canales: los marcadores que el banco declara en esperado.json («debe_contener»:
     notas al pie, tablas anidadas, hojas ocultas, texto OCR…) aparecen en documento.md.
  X  Robustez: ninguna excepción cruda, caída o timeout; y el resultado (convertir /
     rechazo controlado) coincide con esperado.json.

Uso:  python validar_salidas_md.py [--salida output_stress] [--json validacion.json]
Código de salida 0 solo con 0 errores.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
from pathlib import Path
import re
import sys
import zipfile

HERE = Path(__file__).resolve().parent
BOM = b"\xef\xbb\xbf"
SEPARADOR = re.compile(r"^\|(?:\s*:?-{3,}:?\s*\|)+$")
FENCE = re.compile(r"^\s{0,3}(`{3,}|~{3,})")
TITULO = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
IMAGEN_O_ENLACE = re.compile(r"(!?)\[([^\]]*)\]\(\s*(<[^>]*>|[^)\s]+)(?:\s+\"[^\"]*\")?\s*\)")
INTERNO = re.compile(r"(?:^|[\s/])(?:OEBPS|OPS|Part\d+|x_template|_template|rId\d+|(?:xl|word|ppt|visio)/\S*|\S+\.xml)(?:$|\s)",
                     re.I)
ESCAPE_OOXML = re.compile(r"_x[0-9A-Fa-f]{4}_")
REF_A1 = re.compile(r"^\$?[A-Z]{1,3}\$?[1-9]\d{0,6}$")
PROHIBIDOS = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f￾￿]")


class Informe:
    def __init__(self):
        self.hallazgos: list[dict] = []

    def error(self, regla, archivo, detalle, linea=None):
        self.hallazgos.append({"nivel": "ERROR", "regla": regla, "archivo": archivo, "linea": linea, "detalle": detalle})

    def aviso(self, regla, archivo, detalle, linea=None):
        self.hallazgos.append({"nivel": "AVISO", "regla": regla, "archivo": archivo, "linea": linea, "detalle": detalle})

    def errores(self):
        return [h for h in self.hallazgos if h["nivel"] == "ERROR"]


def columnas(fila: str) -> int:
    """Celdas de una fila GFM: separadores «|» no escapados con barra invertida."""
    return len(re.findall(r"(?<!\\)(?:\\\\)*\|", fila)) - 1


# ------------------------------------------------------------------ B
def validar_codificacion(ruta: Path, relativo: str, informe: Informe) -> str | None:
    datos = ruta.read_bytes()
    if datos.startswith(BOM):
        informe.error("B1", relativo, "empieza con BOM UTF-8 (EF BB BF)")
    try:
        texto = datos.decode("utf-8")
    except UnicodeDecodeError as error:
        informe.error("B2", relativo, f"no es UTF-8 válido: byte {error.start}")
        return None
    for numero, linea in enumerate(texto.split("\n"), 1):
        for match in PROHIBIDOS.finditer(linea):
            informe.error("B3", relativo, f"carácter de control U+{ord(match.group()):04X} en columna {match.start() + 1}", numero)
    return texto


# ------------------------------------------------------------------ A, D, E
def validar_markdown(ruta: Path, relativo: str, texto: str, informe: Informe) -> dict:
    lineas = texto.replace("\r\n", "\n").split("\n")
    fence_abierto: tuple[str, int] | None = None
    bloque: list[tuple[int, str]] = []
    tablas = 0

    def cerrar_tabla():
        nonlocal tablas
        if not bloque:
            return
        tablas += 1
        inicio = bloque[0][0]
        for numero, fila in bloque:
            if not fila.rstrip().endswith("|") or fila.rstrip() == "|":
                informe.error("A1", relativo, f"fila de tabla sin cierre «|» (¿salto de línea crudo en una celda?): {fila[:80]!r}", numero)
        if len(bloque) < 2 or not SEPARADOR.match(bloque[1][1].strip()):
            informe.error("A4", relativo, f"tabla sin fila separador |---| bajo el encabezado: {bloque[0][1][:80]!r}", inicio)
        esperado = columnas(bloque[0][1].strip())
        for numero, fila in bloque[1:]:
            actual = columnas(fila.strip())
            if actual != esperado:
                informe.error("A2", relativo, f"fila con {actual} columnas; el encabezado tiene {esperado}: {fila[:80]!r}", numero)
        bloque.clear()

    titulos: list[tuple[int, int, str]] = []
    contenido_desde: dict[int, bool] = {}
    for numero, linea in enumerate(lineas, 1):
        fence = FENCE.match(linea)
        if fence_abierto:
            if fence and fence.group(1)[0] == fence_abierto[0][0] and len(fence.group(1)) >= len(fence_abierto[0]) \
                    and not linea.strip()[len(fence.group(1)):].strip():
                fence_abierto = None
            continue
        if fence:
            cerrar_tabla()
            fence_abierto = (fence.group(1), numero)
            continue
        for match in ESCAPE_OOXML.finditer(linea):
            informe.error("E2", relativo, f"escape OOXML crudo {match.group()} (debió decodificarse)", numero)
        for match in IMAGEN_O_ENLACE.finditer(linea):
            destino = match.group(3).strip("<>").split("#")[0]
            if not destino or re.match(r"^[a-z][a-z0-9+.-]*:", destino, re.I):
                continue                          # anclas internas, http:, mailto:, data:
            if not (ruta.parent / destino).exists():
                regla = "D1" if match.group(1) else "D2"
                informe.error(regla, relativo, f"{'imagen' if match.group(1) else 'enlace'} a recurso inexistente: {destino}", numero)
        if linea.lstrip().startswith("|"):
            if linea.startswith("    ") or linea.startswith("\t"):
                pass                              # bloque de código indentado
            else:
                bloque.append((numero, linea))
                if titulos:
                    contenido_desde[titulos[-1][0]] = True
                continue
        cerrar_tabla()
        titulo = TITULO.match(linea)
        if titulo:
            titulos.append((numero, len(titulo.group(1)), titulo.group(2)))
            contenido_desde.setdefault(numero, False)
            if INTERNO.search(titulo.group(2)):
                informe.error("E1", relativo, f"título con nombre interno de sistema: {titulo.group(2)[:80]!r}", numero)
        elif linea.strip() and titulos:
            contenido_desde[titulos[-1][0]] = True
    cerrar_tabla()
    if fence_abierto:
        informe.error("A5", relativo, f"bloque de código {fence_abierto[0]} abierto y nunca cerrado", fence_abierto[1])
    # Secciones vacías: título seguido de otro de igual o mayor rango sin contenido.
    for indice, (numero, nivel, nombre) in enumerate(titulos):
        siguiente = next((t for t in titulos[indice + 1:] if t[1] <= nivel), None)
        hijos = [t for t in titulos[indice + 1:] if siguiente is None or t[0] < siguiente[0]]
        if not contenido_desde.get(numero) and not hijos:
            informe.aviso("E3", relativo, f"sección vacía: {nombre[:80]!r}", numero)
    return {"tablas": tablas}


# ------------------------------------------------------------------ C
def formulas_origen(fuente: Path) -> set[tuple[str, str, str]] | None:
    try:
        with zipfile.ZipFile(fuente) as archivo:
            if "xl/workbook.xml" not in archivo.namelist():
                return None
        import openpyxl
        libro = openpyxl.load_workbook(fuente, data_only=False, read_only=False)
    except Exception:
        return None
    resultado = set()
    for hoja in libro.worksheets:
        for fila in hoja.iter_rows():
            for celda in fila:
                if celda.data_type == "f":
                    valor = celda.value
                    texto = valor if isinstance(valor, str) else getattr(valor, "text", str(valor))
                    resultado.add((hoja.title, celda.coordinate, texto))
    return resultado


def validar_formulas(paquete: Path, nombre: str, fuente: Path, informe: Informe):
    ruta = paquete / "derivados" / "formulas.csv"
    relativo = f"{nombre}/derivados/formulas.csv"
    if not ruta.is_file():
        informe.error("C1", relativo, "no se generó formulas.csv para un libro Excel")
        return
    texto = validar_codificacion(ruta, relativo, informe)
    if texto is None:
        return
    filas = list(csv.reader(io.StringIO(texto, newline=""), delimiter=";"))
    if not filas or filas[0] != ["hoja", "celda", "formula", "valor_cacheado"]:
        informe.error("C2", relativo, f"cabecera inválida: {filas[0] if filas else 'vacío'}")
        return
    extraidas = set()
    for numero, fila in enumerate(filas[1:], 2):
        if len(fila) != 4:
            informe.error("C2", relativo, f"fila con {len(fila)} campos (se esperan 4 separados por ;)", numero)
            continue
        hoja, celda, formula, _cache = fila
        if not REF_A1.match(celda):
            informe.error("C3", relativo, f"referencia de celda inválida {celda!r}", numero)
        if not formula.startswith("="):
            informe.error("C3", relativo, f"fórmula sin «=» inicial en {hoja}!{celda}: {formula[:60]!r}", numero)
        extraidas.add((hoja, celda, formula))
    origen = formulas_origen(fuente)
    if origen is None:
        if not extraidas:
            informe.aviso("C4", relativo, "origen no OOXML: solo se verifica la estructura; no hay fórmulas extraídas")
        return
    for hoja, celda, formula in sorted(origen - extraidas):
        informe.error("C4", relativo, f"fórmula del libro ausente o alterada: {hoja}!{celda} {formula[:60]!r}")
    for hoja, celda, formula in sorted(extraidas - origen):
        informe.error("C4", relativo, f"fórmula que no existe en el libro: {hoja}!{celda} {formula[:60]!r}")


# ------------------------------------------------------------------ F
def _normalizar(texto: str) -> str:
    """Texto legible: sin escapes Markdown/HTML ni saltos <br>, espacios colapsados."""
    import html
    texto = re.sub(r"<br\s*/?>", " ", texto, flags=re.I)
    texto = html.unescape(html.unescape(texto))
    texto = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", texto)
    return re.sub(r"\s+", " ", texto)


def validar_canales(paquete: Path, nombre: str, marcadores: list[str], informe: Informe):
    """Ningún canal declarado por el banco (nota al pie, tabla anidada, hoja oculta, OCR…)
    puede desaparecer en silencio del Markdown publicado."""
    if not marcadores:
        return
    ruta = paquete / "documento.md"
    texto = _normalizar(ruta.read_text(encoding="utf-8")) if ruta.is_file() else ""
    for marcador in marcadores:
        if _normalizar(marcador) not in texto:
            informe.error("F1", f"{nombre}/documento.md", f"contenido del original ausente: {marcador!r}")


# ------------------------------------------------------------------ principal
def validar(salida: Path) -> tuple[Informe, list[dict]]:
    informe = Informe()
    resumen = []
    for paquete in sorted(p for p in salida.iterdir() if p.is_dir() and not p.name.startswith("_")):
        resultado = json.loads((paquete / "_resultado.json").read_text(encoding="utf-8"))
        nombre = paquete.name
        antes = len(informe.errores())
        if resultado["estado"] in {"excepcion", "caida", "timeout"}:
            informe.error("X1", nombre, f"{resultado['estado']}: {resultado.get('tipo_error', '')} "
                                        f"{str(resultado.get('error', resultado.get('stderr', '')))[:140]}")
        elif not resultado["cumple"]:
            informe.error("X2", nombre, f"resultado «{resultado['estado']}» pero se esperaba «{resultado['esperado']}»: "
                                        f"{str(resultado.get('error', ''))[:140]}")
        tablas = 0
        markdowns = sorted(paquete.rglob("*.md"))
        for ruta in markdowns:
            relativo = f"{nombre}/{ruta.relative_to(paquete).as_posix()}"
            texto = validar_codificacion(ruta, relativo, informe)
            if texto is not None:
                tablas += validar_markdown(ruta, relativo, texto, informe)["tablas"]
        for ruta in sorted((paquete / "derivados").glob("*.csv")) if (paquete / "derivados").is_dir() else []:
            if ruta.name != "formulas.csv":
                validar_codificacion(ruta, f"{nombre}/derivados/{ruta.name}", informe)
        if resultado["estado"] == "ok" and resultado.get("tipo_entrada") == "xlsx":
            validar_formulas(paquete, nombre, Path(resultado["fuente"]), informe)
        if resultado["estado"] == "ok":
            validar_canales(paquete, nombre, resultado.get("debe_contener", []), informe)
        resumen.append({"paquete": nombre, "estado": resultado["estado"], "esperado": resultado["esperado"],
                        "markdown": len(markdowns), "tablas": tablas,
                        "errores": len(informe.errores()) - antes})
    return informe, resumen


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--salida", type=Path, default=HERE / "output_stress")
    parser.add_argument("--json", type=Path, default=HERE / "validacion.json")
    args = parser.parse_args(argv)
    informe, resumen = validar(args.salida.resolve())
    por_regla: dict[str, int] = {}
    for hallazgo in informe.errores():
        por_regla[hallazgo["regla"]] = por_regla.get(hallazgo["regla"], 0) + 1
    salida = {"errores": len(informe.errores()), "avisos": len(informe.hallazgos) - len(informe.errores()),
              "errores_por_regla": dict(sorted(por_regla.items())), "paquetes": resumen, "hallazgos": informe.hallazgos}
    args.json.write_text(json.dumps(salida, ensure_ascii=False, indent=2), encoding="utf-8")
    for fila in resumen:
        print(f"{'✔' if not fila['errores'] else '✘'} {fila['paquete']:34} {fila['estado']:20} "
              f"md={fila['markdown']} tablas={fila['tablas']:3} errores={fila['errores']}")
    for hallazgo in informe.errores()[:400]:
        print(f"  [{hallazgo['regla']}] {hallazgo['archivo']}:{hallazgo['linea'] or '-'}  {hallazgo['detalle']}")
    print(json.dumps({k: salida[k] for k in ("errores", "avisos", "errores_por_regla")}, ensure_ascii=False))
    return 1 if informe.errores() else 0


if __name__ == "__main__":
    raise SystemExit(main())
