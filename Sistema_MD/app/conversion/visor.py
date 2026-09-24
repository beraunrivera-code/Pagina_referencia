# -*- coding: utf-8 -*-
"""Visor legible de un paquete: documento.md -> vista.html, dentro del propio paquete.

POR QUE EXISTE (medido 2026-09-14): «Abrir resultado seleccionado» hacia os.startfile() sobre
documento.md y, como .md no tiene asociacion elegida, Windows lo abria en Antigravity IDE, que
muestra el CODIGO FUENTE del Markdown. Gerardino: «no se ve bien... QUIERO VER COMO ESTA
ORDENADO, SOBRE TODO LA ESTRUCTURA».

REGLAS QUE LO GOBIERNAN
  - Cero dependencias nuevas y CERO llamadas a IA: esto es un renderizador. «El motor resuelve
    una vez; el script repite»: un conversor sirve para los 520 paquetes.
  - vista.html vive DENTRO del paquete, NUNCA en %TEMP% (Windows lo borra: fallo ya fichado).
  - No se anade al manifest.json: cambiaria los hashes y la identidad del paquete.
  - Se escapa TODO; solo <br> y <u> vuelven a habilitarse. El corpus trae pseudo-etiquetas
    (<nombre>, <disciplina>) que deben verse como TEXTO.
  - La pagina nunca hace scroll horizontal: solo la caja de cada tabla.

MEDIDO EN LOS 520 PAQUETES (lo que decide el diseno):
  - 32.284 de 32.392 tablas (99,7 %) vienen SIN fila separadora |---|
  - 11.204 filas cambian de ancho dentro de su propia tabla -> se rellenan
  - 713 hojas de Excel, 316 OCULTAS (44 %) -> plegadas por defecto: es la mejora mas grande
  - 78 paquetes tienen filas de >15 columnas (maximo 396); 73,5 % de sus celdas estan vacias
  - una celda llega a 41.437 caracteres
"""
import html as _html
import json
import os
import re
import subprocess
import uuid
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import quote

try:
    import winreg
except ImportError:                       # pruebas fuera de Windows
    winreg = None

from .documents import digest, load_json, read_stable
from .storage import verify_artifacts

VISOR_VERSION = 3
VIEW_NAME = "vista.html"
ANCHA = 12                 # medido: la mayoria de tablas normales estan en 6-10 columnas
TOPE_MARCAS = 200


@dataclass
class Rendered:
    html: str = ""
    toc: list = field(default_factory=list)
    marks: Counter = field(default_factory=Counter)
    mark_items: list = field(default_factory=list)
    tablas: int = 0
    tablas_anchas: int = 0
    hojas: int = 0
    hojas_ocultas: int = 0
    imagenes_faltan: list = field(default_factory=list)


# ─────────────────────────────────────────────────────────────── utilidades
def _slug(texto, usados):
    base = re.sub(r"[^a-z0-9]+", "-", _quitar_tildes(texto).lower()).strip("-") or "s"
    s = base
    n = 2
    while s in usados:
        s = "%s-%d" % (base, n); n += 1
    usados.add(s)
    return s


def _quitar_tildes(s):
    tabla = str.maketrans("áéíóúüñÁÉÍÓÚÜÑ", "aeiouunAEIOUUN")
    return s.translate(tabla)


def _letra(n):
    """1 -> A, 27 -> AA (las columnas de Excel)."""
    s = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


# ─────────────────────────────────────────────────────────────── inline
_MARCA = re.compile(r"\[(verificar|ilegible|pendiente|sin cach[eé])\b([^\]\n]*)\]", re.I)


class _Inline:
    """Convierte una linea de texto. Escapa TODO primero; solo se re-habilita lo de la lista
    blanca. El codigo entre acentos graves se aparta antes para que nada lo toque dentro."""

    def __init__(self, ctx):
        self.ctx = ctx

    def __call__(self, texto):
        guardados = []

        def apartar(m):
            guardados.append(m.group(1))
            return "\x00%d\x00" % (len(guardados) - 1)

        texto = re.sub(r"`([^`]+)`", apartar, texto)
        s = _html.escape(texto, quote=True)
        s = re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", r"\1", s)
        s = re.sub(r"!\[([^\]]*)\]\(([^)\s]+)\)", self._img, s)
        s = re.sub(r"\[([^\]]+)\]\(&lt;([^&]+)&gt;\)", lambda m: self._enlace(m.group(1), m.group(2)), s)
        s = re.sub(r"\[([^\]]+)\]\(([^)\s]+)\)", lambda m: self._enlace(m.group(1), m.group(2)), s)
        s = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", s)
        s = re.sub(r"__(.+?)__", r"<strong>\1</strong>", s)
        s = re.sub(r"(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])", r"<em>\1</em>", s)
        s = _MARCA.sub(self._marca, s)
        s = re.sub(r"&lt;br\s*/?&gt;", "<br>", s)
        s = re.sub(r"&lt;(/?)u&gt;", r"<\1u>", s)
        s = re.sub(r"&amp;(#\d{1,6}|#x[0-9a-fA-F]{1,6}|lt|gt|amp|quot|nbsp);", r"&\1;", s)
        for i, g in enumerate(guardados):
            s = s.replace("\x00%d\x00" % i, "<code>%s</code>" % _html.escape(g, quote=True))
        return s

    # -- politica de rutas: nada sale del paquete, nada remoto se carga --------------
    def _destino(self, crudo):
        crudo = _html.unescape(crudo).strip().strip("<>")
        if crudo.startswith("#"):
            return ("ancla", crudo)
        if re.match(r"^[a-z][a-z0-9+.\-]*:", crudo, re.I):
            return ("externo", crudo)
        carpeta = self.ctx.get("folder")
        if carpeta is None:
            return ("ok", crudo)
        try:
            p = (carpeta / crudo).resolve()
            if not p.is_relative_to(carpeta.resolve()):
                return ("fuera", crudo)
            if not p.exists():
                return ("ausente", crudo)
        except (OSError, ValueError):
            return ("ausente", crudo)
        return ("ok", crudo)

    def _img(self, m):
        alt, crudo = m.group(1), m.group(2)
        clase, destino = self._destino(crudo)
        if clase == "ok":
            return '<img src="%s" alt="%s" loading="lazy">' % (quote(destino, safe="/"), alt)
        self.ctx["faltan"].append(destino)
        etiqueta = {"externo": "imagen remota no cargada",
                    "fuera": "imagen fuera del paquete",
                    "ausente": "imagen ausente",
                    "ancla": "imagen ausente"}[clase]
        return '<span class="falta">%s: %s</span>' % (etiqueta, _html.escape(destino))

    def _enlace(self, texto, crudo):
        clase, destino = self._destino(crudo)
        if clase == "externo" and not re.match(r"^https?:", destino, re.I):
            return _html.escape(destino)          # javascript:, data: -> texto plano
        rel = ' rel="noopener noreferrer" target="_blank"' if clase == "externo" else ""
        return '<a href="%s"%s>%s</a>' % (_html.escape(destino, quote=True), rel, texto)

    def _marca(self, m):
        tipo = m.group(1).lower().replace("é", "e").replace(" ", "")
        tipo = {"sincache": "pendiente"}.get(tipo, tipo)
        self.ctx["marcas"][tipo] += 1
        n = len(self.ctx["items"]) + 1
        ident = "m%d" % n
        crudo = m.group(0)
        self.ctx["items"].append({"id": ident, "tipo": tipo,
                                  "texto": crudo[:70], "seccion": self.ctx.get("seccion", "")})
        return '<mark class="%s" id="%s">%s</mark>' % (tipo, ident, crudo)


# ─────────────────────────────────────────────────────────────── bloques
_H = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$")
_HOJA = re.compile(r"^Hoja:\s*(.+?)(\s*\*\(oculta\)\*)?\s*$")
_ANCLA = re.compile(r'^<a id="(unidad-\d+)"></a>\s*$')
_HR = re.compile(r"^[ \t]{0,3}(-{3,}|\*{3,}|_{3,})[ \t]*$")
_LISTA = re.compile(r"^([ \t]*)([-*+]|\d{1,9}[.)])[ \t]+(.*)$")
_META = re.compile(r"^filas \d+ · columnas (con dato|usadas) \d+")


def _tabla(lineas, ctx, inline):
    filas = []
    for l in lineas:
        t = l.strip()
        if t.startswith("|"):
            t = t[1:]
        if t.endswith("|"):
            t = t[:-1]
        filas.append([c.strip() for c in t.split("|")])
    cabecera = None
    if len(filas) >= 2 and all(re.fullmatch(r":?-{2,}:?", c or "") for c in filas[1] if c is not None) and filas[1]:
        cabecera = filas[0]
        filas = filas[2:]
    if not filas and cabecera is None:
        return ""
    ancho = max([len(f) for f in filas] + [len(cabecera) if cabecera else 0])
    filas = [f + [""] * (ancho - len(f)) for f in filas]
    if cabecera:
        cabecera = cabecera + [""] * (ancho - len(cabecera))
    ancha = ancho > ANCHA
    ctx["tablas"] += 1
    if ancha:
        ctx["anchas"] += 1
    vacias = [all(not (f[i] or "").strip() for f in filas) for i in range(ancho)]
    celdas_vacias = sum(1 for f in filas for c in f if not c.strip())
    total = max(1, ancho * len(filas))
    partes = ['<div class="tabla%s">' % (" ancha" if ancha else "")]
    if ancha:
        partes.append('<div class="tabla-meta">%d filas × %d columnas · %d %% vacías</div>'
                      % (len(filas), ancho, round(100 * celdas_vacias / total)))
    partes.append("<table>")
    if cabecera:
        partes.append("<thead><tr>" + "".join(
            '<th scope="col"%s>%s</th>' % (' class="cv"' if vacias[i] else "", inline(c))
            for i, c in enumerate(cabecera)) + "</tr></thead>")
    elif ancha:
        partes.append("<thead><tr>" + "".join(
            '<th scope="col" class="letra%s">%s</th>' % (" cv" if vacias[i] else "", _letra(i + 1))
            for i in range(ancho)) + "</tr></thead>")
    partes.append("<tbody>")
    for f in filas:
        tds = []
        for i, c in enumerate(f):
            clases = []
            if not c.strip():
                clases.append("v")
            if vacias[i]:
                clases.append("cv")
            attr = ' class="%s"' % " ".join(clases) if clases else ""
            titulo = ' title="%s"' % _html.escape(c[:300], quote=True) if (ancha and c.strip()) else ""
            tds.append("<td%s%s>%s</td>" % (attr, titulo, inline(c)))
        partes.append("<tr>" + "".join(tds) + "</tr>")
    partes.append("</tbody></table></div>")
    return "\n".join(partes)


def _bloques(lineas, ctx, inline, nivel_base=0):
    salida = []
    i = 0
    n = len(lineas)
    while i < n:
        l = lineas[i]
        if not l.strip():
            i += 1
            continue
        # cerco de codigo
        m = re.match(r"^[ \t]{0,3}(`{3,}|~{3,})(.*)$", l)
        if m:
            cierre, i = m.group(1)[0], i + 1
            cuerpo = []
            while i < n and not re.match(r"^[ \t]{0,3}%s{3,}[ \t]*$" % re.escape(cierre), lineas[i]):
                cuerpo.append(lineas[i]); i += 1
            i += 1
            salida.append("<pre><code>%s</code></pre>" % _html.escape("\n".join(cuerpo)))
            continue
        # ancla de unidad
        m = _ANCLA.match(l.strip())
        if m:
            ctx["unidad"] = m.group(1)
            i += 1
            continue
        # titulo (y seccion de hoja)
        m = _H.match(l)
        if m:
            nivel, texto = len(m.group(1)), m.group(2)
            hoja = _HOJA.match(texto)
            if hoja:
                salida.append(_seccion_hoja(hoja, lineas, i, ctx, inline))
                i = ctx["_salto"]
                continue
            ident = _slug(texto, ctx["ids"])
            ctx["seccion"] = texto
            extra = ' data-unidad="%s"' % ctx.pop("unidad") if ctx.get("unidad") else ""
            ctx["toc"].append({"nivel": min(nivel, 4), "id": ident, "texto": texto,
                               "oculta": False, "stats": ""})
            salida.append("<h%d id=\"%s\"%s>%s</h%d>" % (min(nivel, 6), ident, extra, inline(texto), min(nivel, 6)))
            i += 1
            continue
        if _HR.match(l):
            salida.append("<hr>"); i += 1; continue
        if _META.match(l.strip()):
            salida.append('<p class="meta">%s</p>' % inline(l.strip())); i += 1; continue
        # tabla
        if l.strip().startswith("|"):
            j = i
            while j < n and lineas[j].strip().startswith("|"):
                j += 1
            salida.append(_tabla(lineas[i:j], ctx, inline)); i = j; continue
        # cita
        if re.match(r"^[ \t]{0,3}>", l):
            j = i
            dentro = []
            while j < n and re.match(r"^[ \t]{0,3}>", lineas[j]):
                dentro.append(re.sub(r"^[ \t]{0,3}> ?", "", lineas[j])); j += 1
            figura = dentro and dentro[0].lstrip().startswith("🖼️")
            salida.append('<blockquote%s>%s</blockquote>'
                          % (' class="figura"' if figura else "", _bloques(dentro, ctx, inline)))
            i = j
            continue
        # lista
        m = _LISTA.match(l)
        if m:
            ordenada = not m.group(2)[0] in "-*+"
            sangria = len(m.group(1).expandtabs(4))
            items = []
            while i < n:
                mm = _LISTA.match(lineas[i])
                if not mm:
                    actual = len(lineas[i]) - len(lineas[i].lstrip(" \t"))
                    if lineas[i].strip() and items and actual > sangria:
                        items[-1] += " " + lineas[i].strip(); i += 1; continue
                    break
                items.append(inline(mm.group(3))); i += 1
            etq = "ol" if ordenada else "ul"
            salida.append("<%s>%s</%s>" % (etq, "".join("<li>%s</li>" % x for x in items), etq))
            continue
        # parrafo
        j = i
        trozo = []
        while j < n and lineas[j].strip() and not (
                lineas[j].strip().startswith("|") or _H.match(lineas[j]) or _HR.match(lineas[j])
                or re.match(r"^[ \t]{0,3}>", lineas[j]) or _LISTA.match(lineas[j])):
            trozo.append(lineas[j].strip()); j += 1
        salida.append("<p>%s</p>" % inline(" ".join(trozo)))
        i = j if j > i else i + 1
    return "\n".join(salida)


def _seccion_hoja(hoja, lineas, i, ctx, inline):
    """Una hoja de Excel es una seccion PLEGABLE con su ficha. Las ocultas van cerradas:
    son 316 de 713 en el corpus y es la mejora mas grande y mas barata de la vista."""
    nombre, oculta = hoja.group(1), bool(hoja.group(2))
    j = i + 1
    n = len(lineas)
    while j < n:
        m = _H.match(lineas[j])
        if m and len(m.group(1)) <= 2:
            break
        if _ANCLA.match(lineas[j].strip()):
            break
        j += 1
    dentro = lineas[i + 1:j]
    ctx["_salto"] = j
    ctx["hojas"] += 1
    if oculta:
        ctx["hojas_ocultas"] += 1
    ident = _slug("hoja-" + nombre, ctx["ids"])
    ctx["seccion"] = "Hoja: " + nombre
    stats = ""
    for l in dentro:
        if _META.match(l.strip()):
            stats = l.strip(); break
    if not stats:
        filas = [l for l in dentro if l.strip().startswith("|")]
        stats = "%d filas" % max(0, len(filas) - 2) if filas else "sin filas"
    ctx["toc"].append({"nivel": 2, "id": ident, "texto": "Hoja: " + nombre,
                       "oculta": oculta, "stats": stats})
    cuerpo = _bloques(dentro, ctx, inline)
    abierta = not oculta and not ctx.get("hoja_abierta")
    if abierta:
        ctx["hoja_abierta"] = True
    return ('<details class="hoja%s"%s><summary><h2 id="%s">Hoja: %s</h2>%s'
            '<span class="stats">%s</span></summary><div class="contenido">%s</div></details>'
            % (" oculta" if oculta else "", " open" if abierta else "", ident,
               _html.escape(nombre), '<span class="badge">oculta</span>' if oculta else "",
               _html.escape(stats), cuerpo))


def render_markdown(texto, folder=None, unidades=1, reader=False):
    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    # El preambulo que fabrica documents.render() (titulo, estado, indice) no se pinta:
    # esa informacion va en la FICHA, sacada del JSON, que es el nativo de los metadatos.
    corte = texto.find('<a id="unidad-')
    cuerpo = texto[corte:] if corte > 0 else texto
    if reader and unidades == 1 and corte > 0:
        wrapper = re.match(r'<a id="unidad-[^"]+"></a>\n+## Página/unidad 1\n+', cuerpo)
        if wrapper:
            cuerpo = cuerpo[wrapper.end():]
        # La ficha superior ya presenta el título. Evita repetirlo como una página técnica.
        cuerpo = re.sub(r"^# [^\n]+\n+", "", cuerpo, count=1)
    ctx = {"ids": set(), "toc": [], "marcas": Counter(), "items": [], "faltan": [],
           "tablas": 0, "anchas": 0, "hojas": 0, "hojas_ocultas": 0, "folder": folder,
           "seccion": "", "_salto": 0, "hoja_abierta": False}
    inline = _Inline(ctx)
    html_cuerpo = _bloques(cuerpo.split("\n"), ctx, inline)
    return Rendered(html=html_cuerpo, toc=ctx["toc"], marks=ctx["marcas"],
                    mark_items=ctx["items"], tablas=ctx["tablas"], tablas_anchas=ctx["anchas"],
                    hojas=ctx["hojas"], hojas_ocultas=ctx["hojas_ocultas"],
                    imagenes_faltan=ctx["faltan"])


# ─────────────────────────────────────────────────────────────── pagina
def _leer(folder, nombre):
    try:
        value = load_json(read_stable(folder / nombre))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def build_view(folder):
    folder = Path(folder)
    md = (folder / "documento.md").read_text(encoding="utf-8")
    doc, man, ver = _leer(folder, "document.json"), _leer(folder, "manifest.json"), _leer(folder, "verificacion.json")
    unidades = len(doc.get("expected_units") or []) or 1
    r = render_markdown(md, folder=folder, unidades=unidades, reader=True)
    try:
        integro = verify_artifacts(folder)
    except Exception:
        integro = None
    titulo = doc.get("title") or man.get("title") or folder.name
    esc = lambda s: _html.escape(str(s), quote=True)

    meta = []
    ident = man.get("id") or folder.name
    meta.append('<span>id <b title="%s">%s</b></span>' % (esc(ident), esc(str(ident)[:12])))
    if integro is not None:
        meta.append('<span class="badge %s">%s</span>' % ("ok" if integro else "mal",
                                                          "ÍNTEGRO" if integro else "DAÑADO"))
    if ver.get("status"):
        meta.append('<span class="badge">%s</span>' % esc(ver["status"]))
    if man.get("created"):
        meta.append("<span>creado <b>%s</b></span>" % esc(str(man["created"])[:16].replace("T", " ")))
    if ver:
        faltan = len(ver.get("missing") or [])
        meta.append("<span>unidades <b>%s/%s</b>%s</span>"
                    % (esc(ver.get("received", "?")), esc(ver.get("expected", "?")),
                       " · faltan %d" % faltan if faltan else ""))
    if doc.get("input_kind"):
        meta.append("<span>origen <b>%s</b>%s</span>"
                    % (esc(doc["input_kind"]),
                       " · " + esc(doc["provider"]) if doc.get("provider") else ""))
    if doc.get("source_path"):
        meta.append('<span>nativo <b title="%s">%s</b></span>'
                    % (esc(doc["source_path"]), esc(Path(str(doc["source_path"])).name)))
    if not (doc or man or ver):
        meta.append("<span>metadatos no disponibles</span>")

    avisos = []
    for clave, etq in (("errors", "error"), ("warnings", "aviso"), ("missing", "unidad ausente")):
        for x in (ver.get(clave) or [])[:40]:
            avisos.append("<li>%s: %s</li>" % (etq, esc(x)))
    for x in (doc.get("producer_warnings") or [])[:40]:
        avisos.append("<li>productor: %s</li>" % esc(x))
    if r.imagenes_faltan:
        avisos.append("<li>imágenes no cargadas: %d (%s)</li>"
                      % (len(r.imagenes_faltan), esc(", ".join(r.imagenes_faltan[:4]))))

    nav = ['<div class="marca-producto">SISTEMA MD</div>',
           '<div class="bloque"><b>Mapa de lectura</b><span>%d secciones · %d hojas · %d tablas</span></div>'
           % (len(r.toc), r.hojas, r.tablas)]
    nav_sheet = False
    first_sheet = True
    for t in r.toc:
        grupo = t["texto"].lower() in {"mapa del libro", "hojas principales", "hojas auxiliares",
                                         "auditoría y fidelidad"}
        is_sheet = t["nivel"] == 2 and t["texto"].startswith("Hoja: ")
        if is_sheet:
            if nav_sheet:
                nav.append("</details>")
            opened = first_sheet and not t["oculta"]
            first_sheet = False
            nav.append('<details class="nav-seccion%s"%s><summary><a href="#%s">%s%s</a></summary>'
                       % (" oculta" if t["oculta"] else "", " open" if opened else "", t["id"],
                          esc(t["texto"].removeprefix("Hoja: ")),
                          '<span class="st">%s</span>' % esc(t["stats"]) if t["stats"] else ""))
            nav_sheet = True
        elif t["nivel"] > 2 and nav_sheet:
            nav.append('<a class="n%d" href="#%s">%s</a>'
                       % (t["nivel"], t["id"], esc(t["texto"])))
        else:
            if nav_sheet:
                nav.append("</details>")
                nav_sheet = False
            nav.append('<a class="n%d%s%s" href="#%s">%s%s</a>'
                       % (t["nivel"], " oculta" if t["oculta"] else "", " grupo" if grupo else "",
                          t["id"], esc(t["texto"]),
                          '<span class="st">%s</span>' % esc(t["stats"]) if t["stats"] else ""))
    if nav_sheet:
        nav.append("</details>")
    total_marcas = sum(r.marks.values())
    if total_marcas:
        detalle = " · ".join("%d %s" % (v, k) for k, v in r.marks.most_common())
        nav.append('<details class="dudas" open><summary>Dudas del motor: %d (%s)</summary>'
                   % (total_marcas, esc(detalle)))
        for it in r.mark_items[:TOPE_MARCAS]:
            nav.append('<a href="#%s" class="duda %s">%s<span class="st">%s</span></a>'
                       % (it["id"], it["tipo"], esc(it["texto"]), esc(it["seccion"][:34])))
        if total_marcas > TOPE_MARCAS:
            nav.append('<a class="st">y %d más</a>' % (total_marcas - TOPE_MARCAS))
        nav.append("</details>")

    audit_link = ('<a class="modo" href="%s" download>↓ Rejilla original</a>'
                  % esc(doc["audit_asset"])) if doc.get("audit_asset") else ""
    acciones = ('<div class="acciones"><span class="modo activo">Lectura</span>%s'
                '<a class="descarga" href="documento.md" download>↓ Descargar MD</a>'
                '<a href="document.json">control</a> · <a href="verificacion.json">verificación</a>'
                '<label><input type="checkbox" id="sinvacias"> Ocultar columnas vacías</label></div></header>'
                % audit_link)

    sello = "<!-- sistema-md visor v%d md=%s -->" % (VISOR_VERSION, digest(md.encode("utf-8")))
    return "\n".join([
        sello, "<!doctype html>", '<html lang="es"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width,initial-scale=1">',
        '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src \'self\' data:; style-src \'unsafe-inline\'; script-src \'unsafe-inline\'">',
        "<title>%s · Sistema MD</title>" % esc(titulo), "<style>%s</style></head><body>" % _CSS,
        '<header class="cab"><div><h1>%s</h1><div class="meta">%s</div>%s</div>' % (
            esc(titulo), "".join(meta),
            ('<details class="avisos"><summary>%d avisos</summary><ul>%s</ul></details>'
             % (len(avisos), "".join(avisos))) if avisos else ""),
        acciones,
        '<div class="cuerpo"><nav class="indice">%s</nav><main class="doc">%s</main></div>'
        % ("".join(nav), r.html),
        "<script>document.getElementById('sinvacias').addEventListener('change',function(e){"
        "document.body.classList.toggle('sin-vacias',e.target.checked);});"
        "document.querySelectorAll('.indice a[href^=\"#\"]').forEach(function(a){a.addEventListener('click',function(){"
        "var x=document.getElementById(decodeURIComponent(a.hash.slice(1)));if(x){var d=x.closest('details');if(d)d.open=true;"
        "document.querySelectorAll('.indice a').forEach(function(n){n.classList.remove('actual')});a.classList.add('actual');}})});"
        "</script>",
        "</body></html>"])


def write_view(folder):
    """Escribe vista.html DENTRO del paquete. Si el sello coincide, no reescribe nada."""
    folder = Path(folder).resolve()      # con ruta relativa, as_uri() del resultado revienta
    destino = folder / VIEW_NAME
    # El manifiesto se lee CRUDO, no con load_json: si estuviera dañado, load_json lo rechaza
    # y la guarda se quedaría muda — justo cuando más falta hace. Un nombre reclamado por el
    # manifiesto no se pisa nunca: cambiaría los hashes y la identidad del paquete.
    try:
        manifiesto = json.loads((folder / "manifest.json").read_text(encoding="utf-8"))
    except Exception:
        manifiesto = {}
    if VIEW_NAME in (manifiesto.get("files") or {}):
        raise ValueError("el paquete ya declara %s en su manifiesto" % VIEW_NAME)
    md = (folder / "documento.md").read_text(encoding="utf-8")
    sello = "<!-- sistema-md visor v%d md=%s -->" % (VISOR_VERSION, digest(md.encode("utf-8")))
    if destino.exists():
        try:
            with destino.open(encoding="utf-8") as f:
                if f.read(200).startswith(sello):
                    return destino
        except OSError:
            pass
    tmp = folder / (".vista-%d-%s.tmp" % (os.getpid(), uuid.uuid4().hex))
    try:
        tmp.write_text(build_view(folder), encoding="utf-8")
        try:
            os.replace(tmp, destino)
        except PermissionError:
            # Windows puede negar dos ReplaceFile simultáneos. Si el otro trabajador ya
            # publicó exactamente el mismo sello, el objetivo está cumplido y no se repite.
            try:
                with destino.open(encoding="utf-8") as stream:
                    if stream.read(200).startswith(sello):
                        return destino
            except OSError:
                pass
            raise
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
    return destino


def open_in_browser(path, anchor=None):
    """Abre con el NAVEGADOR predeterminado, no con la app asociada a .html.

    Medido 2026-09-14 en su PC: `.md` no tiene asociacion elegida y Antigravity la reclama;
    por eso el documento se abria como codigo fuente. Se resuelve el manejador de `https`
    (ChromeHTML) y se lanza el ejecutable directamente.
    """
    destino = Path(path).resolve(strict=True)
    uri = destino.as_uri() + (("#" + quote(str(anchor), safe="")) if anchor else "")
    try:
        if winreg is None:
            raise OSError("sin registro de Windows")
        clave = r"Software\Microsoft\Windows\Shell\Associations\UrlAssociations\https\UserChoice"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, clave) as k:
            progid = winreg.QueryValueEx(k, "ProgId")[0]
        with winreg.OpenKey(winreg.HKEY_CLASSES_ROOT, progid + r"\shell\open\command") as k:
            comando = winreg.QueryValue(k, None)
        m = re.match(r'\s*"([^"]+)"|\s*(\S+)', comando)
        exe = m.group(1) or m.group(2)
        if not Path(exe).is_file():
            raise FileNotFoundError(exe)
        subprocess.Popen([exe, uri], close_fds=True)
        return "navegador"
    except Exception:
        os.startfile(uri)
        return "asociacion"


_CSS = """
:root{--bg:#151a23;--panel:#1b2230;--panel2:#202938;--borde:#2c3648;--texto:#e6e9ef;--tenue:#9aa4b2;
--acento:#6ea8ff;--titulo:#fff;--verificar:#f5b942;--ilegible:#ff7b7b;--pendiente:#c79bff;--vacia:#171c26}
*{box-sizing:border-box}html{scroll-behavior:smooth}html,body{margin:0;background:var(--bg);color:var(--texto);font:15px/1.55 Inter,"Segoe UI",system-ui,sans-serif}
.cab{position:sticky;top:0;z-index:5;background:var(--panel);border-bottom:1px solid var(--borde);
padding:12px 22px;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px 24px}
.cab h1{margin:0 0 4px;font-size:20px;color:var(--titulo);overflow-wrap:anywhere}
.cab .meta{display:flex;flex-wrap:wrap;gap:4px 18px;color:var(--tenue);font-size:13px}
.cab .meta b{color:var(--texto);font-weight:600}
.acciones{max-width:520px;display:flex;justify-content:flex-end;align-items:center;flex-wrap:wrap;gap:6px 10px;font-size:13px;color:var(--tenue);text-align:right}
.acciones a{color:var(--acento)}.acciones label{display:block;width:100%;margin-top:2px;cursor:pointer}.modo,.descarga{border:1px solid var(--borde);border-radius:5px;padding:4px 9px;text-decoration:none}.modo.activo{background:var(--panel2);color:#fff}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;font-size:12px;border:1px solid var(--borde);background:var(--panel2)}
.badge.ok{border-color:#2f8f5b;color:#8fe3b0}.badge.mal{border-color:#a33;color:#ff9b9b}
.avisos{margin-top:6px;font-size:13px;color:var(--tenue)}.avisos ul{margin:6px 0 0 18px}
.cuerpo{display:grid;grid-template-columns:286px minmax(0,1fr)}
.indice{position:sticky;top:104px;align-self:start;max-height:calc(100vh - 104px);overflow:auto;
padding:0 0 40px;border-right:1px solid var(--borde);font-size:13px;background:#171d27}
.marca-producto{padding:17px 20px 8px;color:#fff;font-weight:750;letter-spacing:.03em}.indice .bloque{color:var(--tenue);padding:0 20px 13px;border-bottom:1px solid var(--borde)}.indice .bloque b,.indice .bloque span{display:block}.indice .bloque span{font-size:11px}
.indice a{color:#c8d1de;text-decoration:none;display:block;padding:9px 18px;border-bottom:1px solid var(--borde);overflow-wrap:anywhere}
.indice a:hover,.indice a.actual{background:var(--panel2);color:var(--titulo)}.indice a.grupo{margin-top:12px;padding:8px 20px 5px;border:0;color:#7f9cc0;background:transparent;font-size:11px;font-weight:750;letter-spacing:.08em;text-transform:uppercase}
.nav-seccion{border-bottom:1px solid var(--borde)}.nav-seccion>summary{list-style:none;display:flex;align-items:stretch}.nav-seccion>summary::-webkit-details-marker{display:none}.nav-seccion>summary::before{content:"\25B8";padding:10px 0 0 14px;color:var(--tenue)}.nav-seccion[open]>summary::before{content:"\25BE"}.nav-seccion>summary>a{flex:1;border:0;padding-left:9px;font-weight:650}.nav-seccion>a{background:#161c26;border:0;padding-top:6px;padding-bottom:6px}.nav-seccion.oculta{opacity:.68}
.indice .n2{padding-left:14px}.indice .n3{padding-left:28px}.indice .n4{padding-left:42px}
.indice a.oculta{opacity:.6;font-style:italic}
.indice .st{display:block;font-size:11px;color:#6f7a89}
.dudas{margin-top:14px;border-top:1px solid var(--borde);padding-top:8px}
.dudas summary{cursor:pointer;color:var(--texto)}
.duda.verificar{border-left:3px solid var(--verificar)}.duda.ilegible{border-left:3px solid var(--ilegible)}
.duda.pendiente{border-left:3px solid var(--pendiente)}
.doc{min-width:0;width:100%;max-width:1760px;margin:0 auto;padding:20px clamp(18px,3vw,48px) 90px}
.doc h1,.doc h2,.doc h3,.doc h4{color:var(--titulo);scroll-margin-top:118px;line-height:1.25}
.doc h1{font-size:24px;margin:28px 0 10px}
.doc h2{font-size:20px;margin:26px 0 8px;border-bottom:1px solid var(--borde);padding-bottom:4px}
.doc h3{font-size:17px}.doc h4{font-size:15px;color:#cfd6e2}
.doc p{margin:8px 0}.doc a{color:var(--acento)}
.doc code{background:var(--panel2);padding:1px 5px;border-radius:3px;font:13px Consolas,monospace}
.doc pre{background:var(--panel2);border:1px solid var(--borde);padding:10px 12px;overflow:auto;border-radius:6px}
blockquote{margin:10px 0;padding:8px 14px;border-left:3px solid var(--acento);background:var(--panel);color:#d6dce6}
blockquote.figura{border-left-color:#7fd1a8}
blockquote.figura::before{content:"Imagen descrita por el motor";display:block;font-size:12px;color:var(--tenue);margin-bottom:4px}
.tabla{overflow:auto;max-height:72vh;margin:10px 0;border:1px solid var(--borde);border-radius:6px;background:var(--panel)}
table{border-collapse:separate;border-spacing:0;font-size:13px;min-width:100%}
th,td{border-right:1px solid var(--borde);border-bottom:1px solid var(--borde);padding:6px 9px;
vertical-align:top;text-align:left;overflow-wrap:anywhere;max-width:60ch}
th{position:sticky;top:0;background:#273246;color:var(--titulo);font-weight:600;z-index:2}tbody tr:nth-child(even) td{background:#1d2532}
th.letra{color:var(--tenue);font:12px Consolas,monospace;text-align:center}
.ancha td{white-space:normal;min-width:12ch;max-width:34ch}
.ancha td:first-child,.ancha th:first-child{position:sticky;left:0;background:var(--panel);z-index:1}
.ancha th:first-child{z-index:3}
td.v{background:var(--vacia)}
body.sin-vacias .cv{display:none}
.tabla-meta{font-size:12px;color:var(--tenue);padding:6px 10px;border-bottom:1px solid var(--borde);position:sticky;left:0}
details.hoja{border:1px solid var(--borde);border-radius:8px;margin:14px 0;background:var(--panel)}
details.hoja>summary{cursor:pointer;padding:10px 14px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;list-style:none}
details.hoja>summary::before{content:"\\25B8";color:var(--tenue)}
details.hoja[open]>summary::before{content:"\\25BE"}
details.hoja>summary h2{margin:0;border:0;padding:0;font-size:18px}
details.hoja>summary .stats{color:var(--tenue);font-size:12px}
details.hoja.oculta>summary{opacity:.75}
details.hoja>.contenido{padding:0 14px 12px}
mark{padding:0 4px;border-radius:3px;color:#111;font-weight:600;scroll-margin-top:118px}
mark.verificar{background:var(--verificar)}mark.ilegible{background:var(--ilegible)}
mark.pendiente{background:var(--pendiente)}
img{max-width:100%;height:auto;border:1px solid var(--borde);border-radius:4px;background:#fff}
.falta{display:inline-block;border:1px dashed #a33;color:#ff9b9b;padding:2px 8px;border-radius:4px;font-size:13px}
p.meta{color:var(--tenue);font-size:13px;margin:2px 0 8px}
hr{border:0;border-top:1px solid var(--borde);margin:18px 0}
@media (max-width:900px){.cuerpo{grid-template-columns:1fr}
.indice{position:static;max-height:none;border-right:0;border-bottom:1px solid var(--borde)}}
"""
