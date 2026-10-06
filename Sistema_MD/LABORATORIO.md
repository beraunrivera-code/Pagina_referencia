# Sistema MD · El laboratorio: qué hacemos, cómo está armado y para qué

Estado a 2026-10-06. Documento de orientación: lee este primero; los demás documentos
entran en detalle (mapa al final).

---

## 1. La meta y el objetivo

### La meta (por qué existe todo esto)

> **Convertir todos tus documentos de trabajo —Excel, Word, PowerPoint, PDF, planos CAD e
> imágenes— en Markdown fiel, ordenado y comprobable, en tu propia PC, sin enviarlos a
> ninguna IA ni a internet.**

Markdown es texto plano con estructura (títulos, tablas, listas). Un documento en Markdown:

- se busca, se compara y se versiona como texto;
- lo puede leer cualquier persona o cualquier IA **sin perder tablas ni números**;
- no depende de que tengas Office, AutoCAD o el programa que creó el original.

El destino es una **biblioteca documental confiable**: cada archivo original se convierte en
un *paquete* (Markdown + datos derivados + huellas de verificación) del que te puedes fiar.

### El objetivo concreto (cuándo damos el trabajo por terminado)

No basta con «convierte». El programa tiene que cumplir siete condiciones medibles
(detalladas en [LOOP_EVALUACION.md](LOOP_EVALUACION.md)):

| Id | Objetivo | En palabras simples |
|---|---|---|
| **O1** | Instala y arranca | Doble clic en `SISTEMA_MD.bat` y abre; `--check` da `ok` |
| **O2** | No retrocede | Las 444 pruebas automáticas pasan |
| **O3** | Indestructible | Ningún archivo, por roto que esté, tumba el programa: se rechaza **diciendo por qué** |
| **O4** | Markdown válido | Tablas bien cerradas, UTF-8 limpio, enlaces que existen, sin basura interna |
| **O5** | Nada se pierde en silencio | Notas al pie, hojas ocultas, fórmulas, texto de imágenes… aparecen o se marcan `[PENDIENTE]` |
| **O6** | Fiel al original | Una persona compara por muestra: ningún valor, número o fórmula cambiado |
| **O7** | Cubre tus formatos | Al menos 5 archivos **tuyos** por familia convertidos (DWG incluido) |

**Terminado = O1–O7 en verde durante 2 ciclos seguidos.**

### Las reglas que no se negocian

1. **Local primero.** Abrir o convertir no envía nada a una IA. Las rutas API son opcionales y explícitas.
2. **No inventar.** Si algo no se puede leer, se marca `[PENDIENTE: …]`; nunca se rellena con una suposición.
3. **Rechazo explicado.** Un archivo dañado no produce un Markdown basura ni un cuelgue: produce un mensaje con la causa.
4. **Trazable.** Cada paquete guarda huellas SHA-256 del original y de lo que se generó.
5. **Tus archivos son tuyos.** Los documentos reales van a `evaluacion/`, que **nunca** se sube a GitHub (el repositorio es público).

---

## 2. Cómo está armado el laboratorio (vista general)

El laboratorio tiene **cinco piezas**. Cada una responde una pregunta distinta:

```
            ┌───────────────────────────────────────────────┐
 archivo →  │ 1. EL PROGRAMA  (app/conversion)              │ → paquete Markdown
            │    detecta formato → motor por familia → MD   │
            └───────────────────────────────────────────────┘
                 ▲            ▲              ▲            ▲
   ¿el código    │  ¿aguanta  │   ¿funciona  │  ¿corre    │
   sigue bien?   │  lo peor?  │   con LO TUYO?│  en todas  │
 ┌───────────────┴─┐ ┌────────┴─────────┐ ┌──┴─────────┐ ┌┴──────────────────┐
 │ 2. PRUEBAS      │ │ 3. BANCO DE      │ │ 4. CORPUS  │ │ 5. ENTORNOS       │
 │ app/tests       │ │ TORTURA          │ │ REAL       │ │ Windows nativo,   │
 │ 444 pruebas     │ │ tortura/         │ │ evaluacion/│ │ Linux, Docker     │
 └─────────────────┘ └──────────────────┘ └────────────┘ └───────────────────┘
```

| Pieza | Dónde | Pregunta que responde | Quién la usa |
|---|---|---|---|
| 1. Programa | `app/conversion/`, `SISTEMA_MD.bat`, `iniciar.py` | Convertir | Tú (interfaz) o la CLI |
| 2. Pruebas automáticas | `app/tests/` | ¿Un cambio rompió algo que ya funcionaba? | Cada cambio de código |
| 3. Banco de tortura | `tortura/` | ¿Sobrevive a archivos patológicos y rotos? | Cada cambio de código |
| 4. Corpus real | `evaluacion/` + `PROBAR_MIS_ARCHIVOS.bat` | ¿Funciona con **tus** documentos? | Tú, en tu PC |
| 5. Entornos | `SISTEMA_MD.bat`, `entrypoint.sh`, `Dockerfile` | ¿Funciona igual en Windows, Linux y contenedor? | Instalación |

---

## 3. Pieza 1 · El programa: cómo convierte

### 3.1 El recorrido de un archivo

```
archivo ─► detectar ─► ¿formato conocido? ─no─► rechazo explicado («sin adaptador»)
              │                │
              │               sí
              ▼                ▼
   mira los BYTES,       motor de su familia ──falla──► rechazo explicado (ConversionError)
   no la extensión             │
   (un .pptx que es            ▼
   un Excel se trata      paquete en la biblioteca:
   como Excel)            documento.md + derivados/ + imagenes/ + manifest.json + verificacion.json
```

- **Una sola puerta de entrada:** `convert_any` (`app/conversion/pipeline.py`). La interfaz, la
  CLI y el banco de tortura pasan por ella, así lo probado es lo que usas.
- **Detección por contenido:** firma ZIP/OLE/PDF/PNG…, y dentro del ZIP qué partes trae.
  Un archivo con extensión mentirosa se convierte con el motor correcto.
- **Contrato de error:** todo fallo de una librería se envuelve en `ConversionError` con una causa
  legible. Nada sale como traza cruda.

### 3.2 Qué hay dentro de un paquete

| Archivo | Para qué |
|---|---|
| `documento.md` | El Markdown que lees o das a una IA |
| `derivados/` | Datos exactos en CSV/JSON (Excel: fórmulas, celdas, estructura) |
| `imagenes/` | Imágenes extraídas del original, enlazadas desde el MD |
| `document.json` | Metadatos: tipo detectado, motor usado, advertencias |
| `manifest.json` / `verificacion.json` | Huellas SHA-256 y comprobaciones del paquete |

### 3.3 Motores por familia

| Familia | Extensiones | Motor | Necesita instalar aparte |
|---|---|---|---|
| **Excel** | `.xlsx` `.xlsm` | openpyxl + lectura directa del ZIP (`native.py`, `excel_structure.py`) | — |
| Excel antiguo | `.xls` `.xlsb` | LibreOffice convierte una **copia** a `.xlsx` → mismo motor | LibreOffice |
| **Word** | `.docx` | Recorrido propio del XML (`word_docx.py`) | — |
| Word antiguo | `.doc` | LibreOffice → `.docx` → mismo motor | LibreOffice |
| **PowerPoint** | `.pptx` | python-pptx (`native.py`) | — |
| PowerPoint antiguo | `.ppt` | LibreOffice → `.pptx` → mismo motor | LibreOffice |
| **PDF** | `.pdf` | PyMuPDF por página; OCR en páginas sin texto (`pipeline.py`) | Tesseract (para escaneados) |
| **CAD** | `.dxf` | ezdxf: textos, bloques, capas, cotas, longitudes (`native.py`) | — |
| CAD | `.dwg` | ODA File Converter → DXF → ezdxf | **ODA File Converter** |
| **Imágenes** | `.png` `.jpg` `.tif` `.gif` `.webp` | Tesseract OCR (`ocr.py`), publicado como *candidato sin verificar* | Tesseract |
| Texto | `.md` `.txt` | Normalización | — |

Motores locales opcionales (`app/workers/`): **MarkItDown** listo; Docling con adaptador sin
preparar; Marker y MinerU pendientes.

---

## 4. Detalle por formato: qué se extrae y cómo se ve

### 4.1 Excel — el «ADN en tres capas»

Excel es la familia más trabajada porque un número o una fórmula cambiada es un error grave.
Cada libro produce tres capas que se pueden cruzar entre sí:

| Capa | Archivo | Qué contiene |
|---|---|---|
| **1. Lectura** | `documento.md` | Una sección por hoja con sus tablas, legible por personas |
| **2. Datos exactos** | `derivados/celdas.csv`, `derivados/formulas.csv`, `derivados/estructura.json` | Cada celda, cada fórmula con su valor guardado, combinadas, tablas nativas |
| **3. Visual** | `derivados/rejilla_original.md`, `imagenes/` | La rejilla tal cual y las imágenes del libro |

Convenciones que verás en `documento.md`:

```markdown
## Hoja: Presupuesto

| Código [A] | Partida [B] | Total [C] |
|---|---|---|
| 01.01 | Excavación | 1250.5 |

## Hoja: Auxiliar *(oculta)*

*(hoja sin celdas con datos)*

![Logo](imagenes/image1.png)
[PENDIENTE: descripción visual de imagenes/image1.png]
```

- **`[A]`, `[B]`…** = letra de la columna original: puedes ubicar cualquier dato en Excel.
- **`*(oculta)*`** = la hoja estaba oculta; no se esconde, se avisa.
- **`formulas.csv`** (UTF-8 sin BOM, separador `;`):

  ```
  hoja;celda;formula;valor_cacheado
  Presupuesto;C2;=A2*B2;1250.5
  ```

  El valor es el que Excel guardó; el programa **no recalcula** (no inventa resultados).
- Escapes internos de Office (`_x000D_`) se decodifican; caracteres de control se eliminan.

Aún no se extraen: comentarios de celda, filas/columnas ocultas, gráficos, tablas dinámicas
(ver [PENDIENTES_MEJORA.md §4](PENDIENTES_MEJORA.md)).

### 4.2 Word

- Párrafos, títulos, listas, tablas (también **anidadas**: `[tabla anidada 1.1]`), combinadas.
- **Notas al pie** `[^1]` y **notas finales** `[^fin1]`, con su texto al final.
- **Fórmulas** de ecuaciones: `[fórmula OMML: …]` linealizada.
- Comentarios, encabezados y pies, hipervínculos seguros, bloques de código.
- Pendiente: revisiones (texto insertado/eliminado), cuadros de texto flotantes.

### 4.3 PowerPoint

- `## Diapositiva N` por diapositiva; las ocultas como `## Diapositiva N *(oculta)*`.
- Textos, tablas, grupos; **notas del orador** como cita.
- Diapositiva sin texto: `*(diapositiva sin contenido)*`; SmartArt/gráficos: `[PENDIENTE: … contenido visual]`.

### 4.4 PDF

- Texto por página; páginas escaneadas pasan por OCR (candidato sin verificar).
- PDF dañado o cifrado: rechazo explicado.
- Pendiente: orden de lectura en columnas complejas y tablas sin bordes.

### 4.5 CAD (DXF / DWG)

- Textos y MTEXT completos (sin recortar), cotas con su medida, bloques **expandidos**
  (recursivos, con límite de seguridad), capas con su estado, extensión del dibujo.
- **Metrado:** longitud de líneas y polilíneas, incluidos arcos (bulge) y polilíneas cerradas.
- Entidades ilegibles se **cuentan y se informan**, no se ocultan.
- **DWG:** solo con ODA File Converter instalado. **Nunca se ha probado con un DWG real** —
  es la prioridad nº 1 ([PENDIENTES_MEJORA.md §1](PENDIENTES_MEJORA.md)).

### 4.6 Imágenes

- OCR con Tesseract (español + inglés). El texto sale marcado como candidato: confunde
  símbolos (p. ej. «I» → «|»). Una imagen en blanco se publica sin inventar texto.

---

## 5. Pieza 2 · Pruebas automáticas (`app/tests/`)

- **444 pruebas** con `unittest`: conversión, interfaz, rutas, credenciales, Linux/Windows,
  robustez de formatos y una prueba que recorre el banco de tortura completo.
- Se ejecutan con `py -3.12 -m unittest discover -s app\tests` (Windows) o
  `./entrypoint.sh pruebas` (Linux/Docker, con pantalla virtual).
- **Resultado actual:** 444 OK en Linux y en la imagen Docker. Falta: ejecutarlas
  automáticamente en GitHub (CI) y en Windows.

---

## 6. Pieza 3 · El banco de tortura (`tortura/`)

Archivos **sintéticos fabricados a propósito** con todo lo que suele romper un conversor.
Se regeneran siempre igual, así que sirven para comparar antes/después de cada cambio.

### 6.1 Los cuatro pasos

| Paso | Script | Qué hace |
|---|---|---|
| 1. Generar | `generar_tortura_multiformato.py` | Crea los 22 archivos en `stress_test_suite/` + `esperado.json` |
| 2. Ejecutar | `ejecutar_tortura.py` | Convierte cada archivo **en un proceso aislado** (si uno revienta, los demás siguen); mide tiempo y memoria → `output_stress/` y `conversion_stress.log` |
| 3. Validar | `validar_salidas_md.py` | Audita cada salida con las reglas A–F y X → `validacion.json` |
| 4. Reparar | (código del programa) | Se corrige la causa y se repite hasta 0 errores |

### 6.2 Qué contiene el banco (22 archivos)

| Familia | Archivos | Patologías que lleva dentro |
|---|---|---|
| Excel | `stress_excel.xlsx`, `.xlsm`, `.xls`, `.xlsb`, `mentira_excel_llamado.pptx` | Hojas ocultas, combinadas, fórmulas, saltos de línea y `\|` en celdas, Unicode, escapes `_x000D_`, imágenes, extensión falsa |
| Word | `stress_word.docx`, `.doc` | Notas al pie y finales, tablas anidadas, fórmulas OMML, listas, comentarios, encabezados |
| PowerPoint | `stress_powerpoint.pptx`, `.ppt` | Diapositivas ocultas, notas, tablas, grupos tipo SmartArt, diapositiva vacía |
| PDF | `stress_pdf.pdf` | Columnas, ligaduras, página escaneada (OCR) |
| CAD | `stress_cad.dxf`, `stress_cad_falso.dwg` | Bloques anidados, cotas, polilíneas con arcos, capas; DWG falso |
| Imágenes | `stress_ocr.png`, `.jpg`, `.tiff`, `stress_imagen_en_blanco.png` | Texto para OCR; imagen vacía |
| Rotos | `corrupto_*` (6) | ZIP truncado, XML roto, DOCX vacío, PDF basura, PNG truncado, DXF truncado |

`esperado.json` dice, por archivo, si se espera **convertir** o **rechazo controlado**, y qué
frases (`debe_contener`) deben aparecer en el Markdown (hasta 18 marcadores por libro Excel).

### 6.3 Las reglas del validador

| Regla | Comprueba |
|---|---|
| **A** Tablas | Filas abren y cierran con `\|`, mismo nº de columnas, separador bajo el encabezado, bloques de código cerrados |
| **B** Codificación | UTF-8 estricto sin BOM, sin caracteres de control (también en los CSV) |
| **C** Excel | `formulas.csv` existe, cabecera correcta, celdas A1 válidas y **las mismas fórmulas que el libro** |
| **D** Enlaces | Cada imagen o enlace local existe en disco |
| **E** Ruido | Sin nombres internos (`rId7`, `word/document.xml`…) ni escapes `_x000D_` |
| **F** Canales | Todos los marcadores `debe_contener` aparecen |
| **X** Robustez | Ninguna excepción cruda, caída o timeout; resultado = lo esperado |

### 6.4 Resultado

| | Antes | Después |
|---|---|---|
| Archivos que cumplen | 10 / 22 | **22 / 22** |
| Errores del validador | 50 | **0** |
| Avisos (no bloquean) | — | 24 (jerarquía de títulos) |
| Tiempo total del banco | — | ~13,5 s |

Detalle completo: [tortura/REPORTE_TORTURA.md](tortura/REPORTE_TORTURA.md).

**Límite honesto:** el banco prueba lo que *sabemos* que rompe. Tus documentos reales traerán
cosas que nadie fabricó; para eso está la pieza 4.

---

## 7. Pieza 4 · El corpus real (tus archivos)

Es el mismo banco de tortura, pero apuntado a **tus** documentos.

```
Pagina_referencia\Sistema_MD\
└── evaluacion\              ← NO se sube a GitHub (está en .gitignore)
    ├── corpus\              ← aquí copias tus PDF, DWG, Excel, Word…
    ├── resultado\           ← lo genera el script (paquetes convertidos)
    └── RESUMEN.txt          ← lo que me envías
```

1. Copia tus archivos a `evaluacion\corpus\` (copias, no originales).
2. Doble clic en **`PROBAR_MIS_ARCHIVOS.bat`**. Ejecuta los pasos 2 y 3 (convertir y validar).
3. Abre `evaluacion\RESUMEN.txt` y envíamelo (texto, sin tus documentos).
4. Yo diagnostico, reparo el código (paso 4), añado un caso equivalente al banco sintético para
   que no vuelva a pasar, y tú haces `git pull` y repites.

Sin `esperado.json` en tu corpus, el validador detecta caídas, rechazos y Markdown roto, pero
no puede saber si faltó una frase concreta. Para O5 puedes añadir un `esperado.json` con frases
que **sabes** que están en cada archivo ([LOOP_EVALUACION.md §3](LOOP_EVALUACION.md)).

---

## 8. Pieza 5 · Los entornos

| Entorno | Cómo se abre | Estado |
|---|---|---|
| **Windows nativo** (tu PC) | `SISTEMA_MD.bat` (interfaz) tras `py -3.12 preparar_equipo.py --instalar` | Entorno preparado en tu PC; interfaz aún sin recorrer con esta versión |
| **Linux nativo** | `./entrypoint.sh` | 444 pruebas OK |
| **Docker** | `docker build -t sistema-md .` · `docker run --rm sistema-md --check` | Construida y probada (444 OK, `--check` OK también en tu PC) |

Lo que cambia entre Windows y Linux (abrir archivos, claves, ODA, puente fab1–fab5) está en
[DESPLIEGUE_LINUX.md](DESPLIEGUE_LINUX.md).

---

## 9. El ciclo de trabajo (cómo avanzamos)

```
   ┌─► Nivel 0  ¿Arranca?             (--check)                         → O1
   │   Nivel 1  ¿Pruebas en verde?    (444 pruebas)                     → O2
   │   Nivel 2  ¿Banco de tortura?    (22/22, 0 errores)                → O3 O4
   │   Nivel 3  ¿Tus archivos?        (PROBAR_MIS_ARCHIVOS.bat)         → O3 O4 O5 O7
   │   Nivel 4  ¿Fiel? revisión humana por muestra                      → O6
   │        │
   │        ▼ ¿algún fallo?
   │   clasificar (A–G) → reparar → caso nuevo en el banco
   └────────┘
   Sale del ciclo: O1–O7 en verde 2 veces seguidas
```

Detalle paso a paso, plantilla de registro y categorías de fallo: [LOOP_EVALUACION.md](LOOP_EVALUACION.md).

---

## 10. Dónde estamos hoy

| Objetivo | Estado | Falta |
|---|---|---|
| O1 Arranca | 🟢 Docker `--check` OK en tu PC · 🟡 interfaz Windows sin recorrer | Abrir `SISTEMA_MD.bat` y convertir algo |
| O2 Pruebas | 🟢 444/444 en Linux y Docker | CI en GitHub + Windows |
| O3 Indestructible | 🟢 banco sintético · ⚪ tus archivos | Primer `RESUMEN.txt` |
| O4 Markdown válido | 🟢 banco sintético · ⚪ tus archivos | Primer `RESUMEN.txt` |
| O5 Nada se pierde | 🟢 banco sintético · ⚪ tus archivos | `esperado.json` de tu corpus |
| O6 Fiel | ⚪ sin revisión humana | Revisar una muestra |
| O7 Cubre tus formatos | 🔴 DWG nunca probado | ODA + 5 planos reales |

Lista completa de mejoras con prioridad: [PENDIENTES_MEJORA.md](PENDIENTES_MEJORA.md).

**Siguiente paso:** ejecutar `PROBAR_MIS_ARCHIVOS.bat` con tus PDF y DWG y enviarme el
`RESUMEN.txt`. Para los DWG, antes instala ODA File Converter.

---

## 11. Mapa de documentos y carpetas

| Quiero… | Abro |
|---|---|
| Entender todo (este documento) | `LABORATORIO.md` |
| Saber qué falta y en qué orden | `PENDIENTES_MEJORA.md` |
| Seguir el ciclo de evaluación paso a paso | `LOOP_EVALUACION.md` |
| Ver el resultado del banco de tortura | `tortura/REPORTE_TORTURA.md` |
| Probar mis archivos | `PROBAR_MIS_ARCHIVOS.bat` + `evaluacion/corpus/` |
| Instalar en Windows | `INICIO_RAPIDO.md`, `GUIA_PRUEBA_OTRA_PC.md` |
| Linux o Docker | `DESPLIEGUE_LINUX.md` |
| La teoría de cada formato | `ADN_FORMATOS_EXTRACCION.md` |
| Motores locales y API | `INVESTIGACION_MOTORES_LOCAL_API.md` |
| Plan y estado de tareas | `PLAN_MAESTRO_IMPLEMENTACION.md`, `ESTADO_IMPLEMENTACION.md` |

| Carpeta | Contenido |
|---|---|
| `app/conversion/` | El programa: detección, motores por formato, interfaz |
| `app/tests/` | Las 444 pruebas automáticas |
| `app/workers/` | Motores locales opcionales (MarkItDown, Docling…) |
| `tortura/` | Banco de tortura: generador, ejecutor, validador, reportes |
| `evaluacion/` | Tus archivos reales y sus resultados (solo en tu PC) |
| `vendor/` | Instaladores externos opcionales (p. ej. ODA para Docker) |
