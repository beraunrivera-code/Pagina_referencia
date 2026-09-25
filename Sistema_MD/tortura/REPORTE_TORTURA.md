# Auditoría de tortura multiformato · Sistema MD

Fecha: 2026-09-24 · Entorno: Ubuntu 24.04, CPython 3.12, LibreOffice 24.2.7, Tesseract 5.3.4 (spa+eng), sin ODA.

## Resultado

| Métrica | Antes | Después |
|---|---:|---:|
| Archivos con el resultado exigido (22) | **10** | **22** |
| Excepciones crudas de librería (X1) | 5 | 0 |
| Formatos sin ruta de conversión (X2) | 7 | 0 |
| Tablas Markdown rotas (A4 separador, A5 valla sin cerrar) | 5 | 0 |
| Escapes OOXML crudos `_x000D_` en el Markdown (E2) | 18 | 0 |
| Contenido del original perdido en silencio (F1) | 15 | 0 |
| **Errores del validador** | **50** | **0** |
| Avisos no bloqueantes (E3 sección vacía) | 15 | 24 |

Los avisos suben porque ahora se publican 12 archivos más. Los 24 que quedan son estructurales del
renderizador común (`## Página/unidad N` seguido del `# título` del motor; `## Hojas principales`
al mismo nivel que `## Hoja: …`, formato exigido por el ADN). No hay contenido perdido tras ellos.

## Por archivo

| Archivo | Exigido | Antes | Err. | Después | Err. | s | MB pico |
|---|---|---|---:|---|---:|---:|---:|
| `corrupto_basura.pdf` | rechazo controlado | excepcion | 1 | rechazo controlado | 0 | 0.25 | 54 |
| `corrupto_dxf_truncado.dxf` | rechazo controlado | excepcion | 1 | rechazo controlado | 0 | 0.46 | 55 |
| `corrupto_png_truncado.png` | rechazo controlado | sin adaptador | 0 | rechazo controlado | 0 | 0.15 | 25 |
| `corrupto_vacio.docx` | rechazo controlado | sin adaptador | 0 | rechazo controlado | 0 | 0.10 | 20 |
| `corrupto_xml_roto.docx` | rechazo controlado | excepcion | 1 | rechazo controlado | 0 | 0.15 | 31 |
| `corrupto_zip_truncado.xlsx` | rechazo controlado | sin adaptador | 0 | rechazo controlado | 0 | 0.10 | 20 |
| `mentira_excel_llamado.pptx` | convertir | ok | 6 | ok | 0 | 0.35 | 50 |
| `stress_cad.dxf` | convertir | ok | 3 | ok | 0 | 0.51 | 66 |
| `stress_cad_falso.dwg` | rechazo controlado | excepcion | 1 | rechazo controlado | 0 | 0.46 | 55 |
| `stress_excel.xls` | convertir | sin adaptador | 1 | ok | 0 | 1.66 | 217 |
| `stress_excel.xlsb` | convertir | excepcion | 1 | ok | 0 | 1.71 | 217 |
| `stress_excel.xlsm` | convertir | ok | 8 | ok | 0 | 0.35 | 50 |
| `stress_excel.xlsx` | convertir | ok | 8 | ok | 0 | 0.10 | 21 |
| `stress_imagen_en_blanco.png` | convertir | sin adaptador | 1 | ok | 0 | 0.35 | 46 |
| `stress_ocr.jpg` | convertir | sin adaptador | 1 | ok | 0 | 0.46 | 53 |
| `stress_ocr.png` | convertir | sin adaptador | 1 | ok | 0 | 0.45 | 53 |
| `stress_ocr.tiff` | convertir | sin adaptador | 1 | ok | 0 | 0.65 | 52 |
| `stress_pdf.pdf` | convertir | ok | 1 | ok | 0 | 1.61 | 136 |
| `stress_powerpoint.ppt` | convertir | sin adaptador | 1 | ok | 0 | 1.81 | 216 |
| `stress_powerpoint.pptx` | convertir | ok | 3 | ok | 0 | 0.20 | 40 |
| `stress_word.doc` | convertir | sin adaptador | 1 | ok | 0 | 1.46 | 217 |
| `stress_word.docx` | convertir | ok | 9 | ok | 0 | 0.20 | 36 |

Tiempo total 13,5 s. El pico de ~217 MB corresponde a LibreOffice (proceso nieto medido con `wait4`).
«Antes» usa la ruta de la CLI `convertir` de entonces (nativo, `preparar-pdf` con todas las páginas, texto).

## Causas raíz y reparación

| # | Síntoma | Causa raíz | Reparación |
|---|---|---|---|
| 1 | `DXFStructureError`, `XMLSyntaxError`, `FileDataError`, `OSError` crudos | Los motores dejaban escapar la excepción de su librería | `ConversionError(ValueError)` en `local_io`; `_convert_native` envuelve toda excepción no controlada con motor y archivo; PDF/DXF/imagen validan antes de leer |
| 2 | DWG sin ODA → `RuntimeError` | Tipo de error fuera del contrato | Rechazo explicado (`ConversionError`) |
| 3 | `.xls`/`.doc`/`.ppt` sin ruta | OLE detectado solo como «office-legacy» | `office_legacy.ole_subtype` lee el directorio CFB (streams `Workbook`, `WordDocument`, `PowerPoint Document`); copia OOXML con LibreOffice, perfil aislado, aviso FMT14 «no neutra» |
| 4 | `.xlsb` → «no valid workbook part» | `detect` lo tomaba por `.xlsx` | Firma `xl/workbook.bin` → `xlsb` → ruta LibreOffice |
| 5 | PNG/JPG/TIFF sin ruta; página PDF escaneada `[PENDIENTE]` | No existía adaptador de imagen ni OCR | `imagen_ocr` (multipágina, verificación previa, tope 60 MP) + `ocr.py` (Tesseract CLI); OCR publicado como **candidato sin verificar**; sin Tesseract queda `[PENDIENTE]` explícito |
| 6 | `_x000D_`, `_x0007_`, `_x0000_` en Markdown y CSV | openpyxl y python-pptx no decodifican `_xHHHH_` | `decode_ooxml_escapes` (respeta `_x005F_`) + `clean_control` en celdas, CSV y notas |
| 7 | Párrafo `\| a \| b \|` → tabla sin separador; «```» abría un bloque de código hasta el final | Texto libre publicado sin neutralizar | `md_text`: escapa `#`, `>`, `\|`, vallas, reglas y marcadores de lista al inicio de línea (sin alterar el texto) |
| 8 | Notas al pie/finales, tablas anidadas y OMML ausentes | `cell.text` y `paragraph.text` de python-docx omiten esos nodos | `word_docx.py` recorre el XML: `[^n]`, `[tabla anidada 1.1]`, `[fórmula OMML: …]`, listas de 6 niveles, vallas de código de longitud segura, filas `gridBefore` rellenadas |
| 9 | Diapositiva oculta sin marca; notas con una sola línea citada; tabla absorbía el texto siguiente | Atributo `show="0"` ignorado; cita solo en la primera línea; tabla sin línea en blanco | `## Diapositiva N *(oculta)*`; cada línea de notas con `>`; línea en blanco tras la tabla |
| 10 | CAD: textos de bloques anidados y cotas ausentes; extensión «±1e20» como tabla suelta | Solo entidades de primer nivel; `DIMENSION` ignorada; cabecera sin actualizar | Expansión recursiva de INSERT (tope de profundidad y entidades), texto de cota con su medida, extensión calculada con `bbox`, capas congeladas/apagadas/bloqueadas declaradas |
| 11 | Longitudes CAD erróneas | Bulge ignorado, polilínea cerrada sin último tramo, ARC 350°→10° = 340°, y **toda POLYLINE medía 0** (`Vec3[:2]` lanza `TypeError`, tragado por un `except`) | Longitud de arco por bulge, cierre, barrido módulo 360, `tuple(location)`; las entidades ilegibles ya no se silencian: se declaran en el informe |
| 12 | Textos CAD recortados a 300 caracteres | `[:300]` en el limpiador | Sin recorte en CSV; solo la vista se acota |

Cada causa tiene regresión en `app/tests/test_robustez_formatos.py`, incluida una prueba de extremo a
extremo que ejecuta generador, pipeline y validador y exige 0 errores.

## Límites declarados

- **DWG real:** no hay ODA en este entorno. El DWG falso se rechaza con causa; la ruta ODA con
  `xvfb-run` está cubierta por pruebas unitarias, no por un DWG verdadero.
- **XLSB:** no existe un escritor libre; el banco fabrica un BIFF12 mínimo a mano (verificado con
  LibreOffice y pyxlsb). No sustituye a un XLSB guardado por Excel.
- **OCR:** es un candidato. En la página escaneada leyó «tipo |» por «tipo I»: por eso nunca se
  publica como texto verificado.
- **Legado vía LibreOffice:** conversión no neutra (ADN FMT14). En el `.doc` los números de nota
  pasan a `[^2]`, `[^3]` por la renumeración de LibreOffice.
- **Regla F** comprueba marcadores declarados por el banco. Demuestra que esos canales llegan,
  no una fidelidad general.

## Reproducir

```bash
cd Sistema_MD/tortura
python generar_tortura_multiformato.py        # FASE 1 → stress_test_suite/ + esperado.json
python ejecutar_tortura.py                    # FASE 2 → output_stress/ + conversion_stress.log
python validar_salidas_md.py                  # FASE 3 → validacion.json; código 0 = 0 errores
```

Instantáneas de esta auditoría: `conversion_stress.antes.log` / `conversion_stress.despues.log`,
`validacion_antes.json` / `validacion_despues.json`.
