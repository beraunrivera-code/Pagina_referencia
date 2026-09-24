# ADN de formatos: del archivo original a una extracción verificable

Fecha: 2026-09-20. Estado: investigación y diseño; adaptadores y controles propuestos, todavía no implementados. Complementa [el informe de motores](INVESTIGACION_MOTORES_LOCAL_API.md). Autoría: Codex, GPT-6 según entorno, revisión propia y de subagentes; sin comparación ciega.

**Decisión de diseño propuesta:** un programa Local/API, con conocimiento específico por familia y subtipo. Python coordina el núcleo existente; cada parser, OCR o modelo tiene una función comprobable. «Admitir una extensión» no significa conservar toda su información.

## Ruta de lectura

- Básico: [modelo mental](#modelo-mental) y [papel de cada tecnología](#papel-de-cada-tecnologia).
- Medio: [atlas por familias](#atlas-de-formatos), con estructura, estrategia y caso testigo.
- Avanzado: [contrato de fidelidad](#contrato-de-fidelidad), [premortem](#premortem-por-estructura) y [criterios para publicar soporte](#criterios-para-publicar-soporte).

<a id="modelo-mental"></a>
## Modelo mental

Un archivo tiene varias capas: **contenedor y codificación → objetos y relaciones → significado declarado → apariencia → interpretación**. Una biblioteca puede leer una capa y omitir otra. El informe debe distinguir lo que estaba almacenado, lo que se reconoció visualmente y lo que se infirió.

Ejemplo: en Excel, `=SUM(A1:A3)`, el valor almacenado y `S/ 1.250,00` no son lo mismo. En Word, tres párrafos no demuestran que se hayan capturado notas y comentarios. En PDF, todas las palabras pueden estar presentes y las columnas quedar mezcladas.

Ruta propuesta: identificar firma y subtipo → inventariar canales → extraer estructura nativa → renderizar las regiones necesarias → OCR o visión de faltantes → reconciliar candidatos con procedencia → validar → producir JSON y Markdown. La ruta puede terminar en «parcial» o «requiere revisión»; no debe inventar contenido para terminar en verde.

Conocer el formato reduce errores evitables; no resuelve por sí solo mala calidad de escaneo, símbolos ambiguos, archivos dañados o semántica que nunca se guardó.

<a id="papel-de-cada-tecnologia"></a>
## Papel de cada tecnología

| Pieza | Responsabilidad propuesta | Lo que hay que verificar |
|---|---|---|
| Python | Mantener extractores, cola, adaptadores, normalización y pruebas existentes; workers separados para motores incompatibles | Versión compatible por worker, cancelación, límites y recuperación; no elegir el Python más nuevo por reflejo |
| JavaScript/TypeScript | Candidato para una interfaz navegable y visor: selección, comparación, búsqueda, estados y progreso | Contrato tipado con el núcleo; un error visual no debe modificar la extracción. Comparar con PySide antes de fijar el contenedor desktop |
| Parser de formato | Leer objetos, partes, referencias, fórmulas, celdas o entidades directamente | Cobertura real del subtipo y sus canales; versión, límites y salida cruda |
| Renderer | Crear una referencia visual y coordenadas para revisión/OCR | Fuentes, dimensiones, rotación, versión de aplicación y fidelidad. Un render no recupera fórmulas ni relaciones desaparecidas |
| OCR | Reconocer caracteres y posiciones de texto en píxeles | Idioma, orientación, resolución efectiva, números y símbolos; confianza OCR no equivale a exactitud |
| Modelos de layout/tablas/matemáticas | Proponer orden, regiones, rejillas y expresiones | Medir cada problema por separado; tabla correcta exige relaciones entre celdas |
| Visión o VLM local/API | Describir figuras y resolver regiones difíciles con incertidumbre visible | No sustituir texto ilegible por una suposición. Separar transcripción, descripción e interpretación |
| C++/Rust y runtimes GPU | Motores especializados ya existentes debajo de bindings/workers | Compatibilidad, memoria, licencia y empaquetado; no reescribir parsers maduros sin una necesidad medida |
| Java | Candidato opcional para un worker Apache Tika si aporta cobertura legacy útil | Coste de otro runtime y fidelidad probada. Java y JavaScript son tecnologías diferentes; no es requisito agregar ambos |

Fundamentos de estas opciones: [procesos Python](https://docs.python.org/3.13/library/concurrent.futures.html), [TypeScript](https://www.typescriptlang.org/docs/handbook/intro), [capas de PDF.js](https://mozilla.github.io/pdf.js/getting_started/), [formatos de Apache Tika](https://tika.apache.org/docs/4.0.x/formats.html). Son candidatos de arquitectura, no componentes instalados por este estudio.

Docling es el primer candidato para estructura PDF/imágenes; MarkItDown para rutas ligeras seleccionadas; Marker/MinerU como alternativas donde una prueba muestre ventaja y la licencia permita el uso. OCR nativo, lectura de XML y geometría CAD siguen siendo necesarios. No hay un motor universal ni un lenguaje que por sí solo produzca mayor fidelidad.

<a id="atlas-de-formatos"></a>
## Atlas de formatos

Cada ficha contiene estructura documentada y una estrategia propuesta. Los casos testigo son requisitos para el piloto; no se presentan como pruebas ya aprobadas.

### FMT01 — PDF digital, escaneado y mixto

**Fabricación.** Grafo de objetos, catálogo, páginas, recursos y flujos; las referencias pueden almacenarse como tablas o streams y existir actualizaciones incrementales. Además del dibujo de la página puede haber anotaciones, adjuntos, formularios y metadatos. Un parser debe resolver la revisión vigente. [Sintaxis PDF](https://pdfa.org/the-smallest-possible-valid-pdf/), [ISO 32000-2, erratas de sintaxis](https://pdf-issues.pdfa.org/32000-2-2020/clause07.html).

**Texto.** Códigos y fuentes dibujan glifos; `ToUnicode` ayuda a recuperar caracteres. Ver algo correctamente no garantiza que el texto extraído sea correcto. [ISO, texto](https://pdf-issues.pdfa.org/32000-2-2020/clause09.html). Los tags pueden declarar estructura y orden; hay que contrastarlos con la página. Orden de pintado y orden de lectura no son equivalentes. [Tagged PDF](https://pdfa.org/wp-content/uploads/2023/07/Tagged-PDF-Best-Practice-Guide.pdf).

**Ruta.** Diagnóstico por página y región; reutilizar texto nativo fiable; OCR solo donde falta o discrepa. Una página con imagen y texto OCR invisible exige reconciliación para evitar duplicados. «Tiene texto» no autoriza saltarse toda comprobación visual. Formularios, comentarios y adjuntos deben tener canales separados del cuerpo.

**Geometría.** Guardar MediaBox/CropBox, rotación, escala y transformaciones; una simple inversión del eje Y puede situar mal el recorte. [API de página Adobe](https://opensource.adobe.com/dc-acrobat-sdk-docs/acrobatsdk/apireference/PD_Layer/PDPage.html).

**Caso testigo.** Dos columnas + nota + sello escaneado + capa OCR equivocada; otra página rotada y recortada. Cierre: orden esperado, ninguna duplicación, símbolos/cifras críticos exactos, discrepancia alertada y recorte sobre la región correcta. Para un fixture geométrico controlado, tolerancia propuesta de un píxel del render, registrando su resolución. No aplicar esa tolerancia como certificación universal de OCR.

### FMT02 — Imágenes raster: PNG, JPEG, TIFF, BMP, WebP y GIF

**Fabricación.** Píxeles, codificación/compresión y metadatos. Diferenciar canal alfa, color, orientación, resolución y secuencias. PNG puede incluir texto auxiliar y animación: esos metadatos no son una transcripción de los píxeles. [PNG, W3C](https://www.w3.org/TR/2025/REC-png-3-20250624/).

TIFF enlaza directorios IFD y puede tener páginas, miniaturas y resoluciones alternativas; no todo directorio es una página. BigTIFF tiene una estructura de offsets ampliada. [TIFF 6.0](https://www.itu.int/itudoc/itu-t/com16/tiff-fx/docs/tiff6.pdf), [LibTIFF](https://libtiff.gitlab.io/libtiff/multi_page.html), [BigTIFF](https://libtiff.gitlab.io/libtiff/specification/bigtiff.html).

**Ruta.** Decodificar con límites de tamaño → inventariar páginas/fotogramas → normalizar una copia → OCR con cajas y transformaciones → análisis visual si el contenido lo necesita. Guardar la imagen original. Declarar si animación se conserva, se procesa por fotograma o queda pendiente. Cambiar el DPI declarado no crea detalle.

**Caso testigo.** TIFF de tres páginas con miniaturas, imagen transparente, imagen rotada y texto diminuto. Cierre: tres páginas lógicas, orientación correcta, sin páginas perdidas ni miniaturas duplicadas; el texto no legible queda identificado. Un JPG de plano no permite recuperar precisión geométrica CAD por sí solo.

### FMT03 — Word DOCX

**Fabricación.** DOCX, XLSX y PPTX usan OPC: partes XML/binarias, tipos de contenido y relaciones dentro de un paquete habitualmente ZIP. Seguir relaciones/namespaces y contenido alternativo; el nombre del archivo interno no define por sí solo su orden. [ECMA-376](https://ecma-international.org/publications-and-standards/standards/ecma-376/). Un OOXML cifrado puede estar envuelto en OLE/CFB; no asumir «ZIP corrupto». [Paquete cifrado Microsoft](https://learn.microsoft.com/en-us/dotnet/api/system.io.packaging.encryptedpackageenvelope.isencryptedpackageenvelope).

Word tiene cuerpo principal y otras historias: encabezados, pies, notas, comentarios y cuadros de texto. [WordprocessingML](https://learn.microsoft.com/en-us/office/open-xml/word/structure-of-a-wordprocessingml-document). Conservar revisiones y separar código de campo de resultado almacenado; pueden existir fechas/índices desactualizados. [Revisiones](https://learn.microsoft.com/en-us/office/open-xml/word/how-to-accept-all-revisions-in-a-word-processing-document), [campos](https://learn.microsoft.com/en-us/dotnet/api/documentformat.openxml.wordprocessing.fieldcode).

**Ruta.** Parser semántico → historias, estilos/listas, tablas, imágenes ancladas, ecuaciones y estados → render identificado para paginación/objetos flotantes. No aceptar cambios ni actualizar campos/vínculos automáticamente. La posición de un objeto en XML no sustituye su posición visual.

**Caso testigo.** Documento con nota al pie, comentario, revisión pendiente, campo obsoleto, tabla anidada y ecuación. Cierre: todos los marcadores esperados, cada uno en su canal y estado; revisión eliminada no confundida con texto vigente; original intacto. DOCM/plantillas necesitarán controles adicionales aunque compartan vocabulario.

### FMT04 — Excel XLSX y XLSM

**Fabricación.** Libro, hojas y relaciones; celdas tipadas, cadenas compartidas o en línea, estilos, rangos, tablas, gráficos y conexiones. [SpreadsheetML](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/structure-of-a-spreadsheetml-document). `<f>` contiene fórmula y `<v>` puede contener su resultado de la última evaluación: caché no prueba vigencia. [Fórmulas Microsoft](https://learn.microsoft.com/en-us/office/open-xml/spreadsheet/working-with-formulas).

**Ruta.** Conservar dirección, tipo, valor original, fórmula, caché, formato numérico y valor presentado por separado. Inventariar hojas/filas/columnas ocultas, fusiones, nombres, comentarios, gráficos/pivots y vínculos. Área de impresión no delimita todo el libro. Render complementario para gráficos/layout; no recalcular fórmulas, ejecutar macros ni actualizar conexiones durante extracción. Recálculo sería otro derivado identificado.

**Caso testigo.** Hoja oculta, fechas 1900/1904, fórmula sin caché y otra obsoleta, error, celda vacía intermedia, fusión, gráfico y XLSM con macro inactiva. Cierre: coordenadas/tipos/estados exactos; fórmula y resultado separados; todos los canales incluidos o pendientes explícitos. El caso `EmptyCell` ya está reproducido como H03; los demás son pruebas futuras.

### FMT05 — PowerPoint PPTX

**Fabricación.** Escena por diapositiva, formas/grupos y relaciones con layout, master y tema; notas, diagramas, gráficos y medios pueden ocupar partes separadas. [PresentationML](https://learn.microsoft.com/en-us/office/open-xml/presentation/structure-of-a-presentationml-document).

**Ruta.** Semántica de objetos y conectores + render; conservar posiciones, jerarquía, notas, diapositivas ocultas y medios. Orden XML no es lectura certificada. Imagen estática no cubre animación/audio/video; inventariar esos canales sin declararlos interpretados.

**Caso testigo.** Nota exclusiva, texto de master, grupo rotado, SmartArt, gráfico y flecha NO/SÍ. Cierre: notas asociadas, recursos inventariados y relaciones con dirección/etiqueta comprobadas o pendientes. Dos cajas legibles con un conector no equivalen a un flujo extraído: el fallo de detección visual actual está reproducido en H02.

### FMT06 — Visio VSDX

**Fabricación.** OPC con vocabulario propio: páginas, masters, formas, ShapeSheet, geometría y propiedades. [Formato VSDX](https://learn.microsoft.com/en-us/office/client-developer/visio/introduction-to-the-visio-file-formatvsdx). `Connect` identifica extremos con `FromSheet`/`ToSheet` y referencias de celda/parte; el sentido semántico del flujo requiere interpretación adicional. [Conexiones](https://learn.microsoft.com/en-us/office/client-developer/visio/connect-element-connects_type-complextypevisio-xml).

**Ruta.** Resolver XML/masters/estilos → grafo de nodos/conexiones y propiedades → render de referencia. Conservar forma sin texto, capas, fondos y grupos. Una flecha o etiqueta ausente puede cambiar todo el procedimiento.

**Caso testigo.** Master reutilizado, grupo anidado, capa oculta, Shape Data, dos conectores y forma sin texto. Cierre: IDs/extremos correctos, propiedades recuperadas, dirección del flujo cotejada y componentes no resueltos señalados.

### FMT07 — CAD: DWG y DXF

**Fabricación.** DXF almacena entidades/propiedades mediante códigos; bloques, inserciones y referencias externas se relacionan. DWG requiere lector/conversor compatible con su versión. Una definición de bloque puede contener inserciones de otros bloques. [Autodesk: bloques y xrefs](https://help.autodesk.com/cloudhelp/2023/ENU/AutoCAD-DXF/files/GUID-9DDFC343-6C87-4AF8-B3B9-77E0AB5A3031.htm).

**Ruta.** Parser CAD → entidades, atributos, capas, bloques, layouts, referencias y unidades → aplicar transformaciones → render para cotejo. El contexto y las escalas de inserción determinan las unidades, que no se deducen de cómo se ve el número. La documentación de ezdxf advierte que parte de su interpretación de unidades procede de experimentos de compatibilidad: debe probarse contra el CAD de referencia. [Unidades ezdxf](https://ezdxf.readthedocs.io/en/stable/concepts/units.html).

**Caso testigo.** Bloque rotado y escalado, arco, spline, layout y xref ausente. Cierre: texto/atributos y transformaciones correctos; unidad y tolerancia explícitas; xref faltante visible; entidades no soportadas contabilizadas. Longitud aproximada o deducida de imagen no se publica como metrado certificado. Preservar el DWG original y el DXF derivado con trazabilidad.

### FMT08 — EPUB

**Fabricación.** Contenedor ZIP documental: `mimetype`, `META-INF/container.xml`, paquete, manifiesto y `spine`; XHTML/SVG, estilos, imágenes y navegación. La lista de recursos no define su orden de lectura. [EPUB 3.3](https://www.w3.org/TR/epub-33/).

**Ruta.** Detectar EPUB dentro del ZIP → resolver paquete y spine → extraer capítulos, notas, enlaces, figuras, metadatos y matemática → render selectivo si el layout es fijo. Recursos externos se inventarían sin descargarlos por defecto; contenido cifrado se declara pendiente, sin intentar eludir protección.

**Caso testigo.** Capítulos cuyos nombres alfabéticos contradicen el spine, notas con enlace de retorno e imagen. Cierre: secuencia correcta, enlaces resolubles y activos presentes; conservar identificadores de capítulo/fragmento. No inventar números de página para un libro refluible.

### FMT09 — HTML y HTM

**Fabricación.** Bytes/codificación → tokens → árbol DOM; scripts pueden modificar el documento y los estilos cambian su apariencia. [Algoritmo HTML](https://html.spec.whatwg.org/multipage/parsing.html).

**Ruta.** Parser HTML real, selección explícita del alcance, estructura/tables/enlaces/alt text y recursos locales. Separar árbol almacenado de una página generada por scripts. Render aislado opcional con política de red; no ejecutar automáticamente scripts de archivos importados.

**Caso testigo.** HTML con entidades, tabla con spans, imagen relativa y contenido que solo aparece mediante JavaScript. Cierre: información almacenada fiel, scripts inactivos, recursos ausentes visibles; declarar que contenido dinámico no fue ejecutado. Renombrar `.html` a `.htm` no debe cambiarlo a texto bruto.

### FMT10 — TXT y Markdown

**Fabricación.** Bytes y codificación en TXT; Markdown agrega una gramática y dialectos, con bloques de código, referencias y posible HTML incrustado. [CommonMark](https://spec.commonmark.org/0.31.2/).

**Ruta.** Preservar original/codificación, indicar fallos de decodificación y construir árbol cuando sea MD. Distinguir encabezado real de `#` dentro de código. Sanitizar la vista HTML y mantener una política explícita para enlaces/recursos externos.

**Caso testigo.** UTF-8/UTF-16, acentos, bloque de código con encabezados falsos y enlace relativo. Cierre: ningún carácter sustituido sin aviso; texto y código íntegros; enlace local resuelto o pendiente, sin descarga silenciosa.

### FMT11 — CSV

**Fabricación.** Registros y campos con delimitadores/comillas; un campo puede contener saltos de línea. RFC 4180 describe una variante, no todas las exportaciones regionales. [RFC 4180](https://www.rfc-editor.org/info/rfc4180/).

**Ruta.** Parser de dialecto/codificación con opción de confirmación cuando hay ambigüedad. Conservar el texto de cada campo; no convertir automáticamente `0012` en `12`, ni `1,25` en dos números. La detección de cabecera es heurística. [Python CSV](https://docs.python.org/3.13/library/csv.html).

**Caso testigo.** Coma/punto y coma, comillas escapadas, campos multilínea, códigos con cero inicial y fecha ambigua. Cierre: dimensiones/campos exactos y decisiones de tipo explícitas. Una exportación destinada a hojas de cálculo debe impedir que datos se ejecuten como fórmulas, manteniendo el valor original aparte.

### FMT12 — JSON

**Fabricación.** Objetos, arrays y valores; nombres repetidos generan comportamientos inconsistentes entre lectores y los números pueden exceder la precisión de ciertos runtimes. [RFC 8259](https://www.rfc-editor.org/info/rfc8259/).

**Ruta.** Parser estricto con límites de tamaño/profundidad; detectar duplicados y conservar números sin pérdida. JSON importado es dato del usuario, no configuración ejecutable ni respuesta de IA confiable. Mantener su estructura y localizadores; generar MD solo como vista.

**Caso testigo.** Clave duplicada, entero de más de 53 bits, array heterogéneo, `null` y cadena vacía. Cierre: no se pierde precisión ni se confunden null/vacío/ausente; duplicados rechazados o representados con advertencia y original íntegro.

### FMT13 — XML genérico

**Fabricación.** Elementos, atributos, namespaces, texto mixto y entidades; el namespace y vocabulario definen el dominio, no el sufijo `.xml`. [XML, W3C](https://www.w3.org/TR/xml/).

**Ruta.** Parser seguro sin resolver entidades externas; preservar namespaces, atributos, orden y texto mixto. Detectar vocabulario conocido —por ejemplo Excel XML— antes de la ruta genérica. No aplanar todos los XML como párrafos.

**Caso testigo.** Dos namespaces con nombres locales iguales, texto mezclado con etiquetas y referencia externa. Cierre: identidades/orden preservados; referencia externa no leída; no se pierden atributos que contienen datos.

### FMT14 — Office binario heredado: DOC, XLS y PPT

**Fabricación.** OLE/CFB con streams específicos; no son OOXML descomprimibles. DOC reconstruye texto a través de sus estructuras; XLS usa registros binarios; PPT resuelve registros/objetos vigentes. Buscar cadenas sueltas puede mezclar contenido residual. [CFB](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-cfb/53989ce4-7b05-4f8d-829b-d08d6148375b), [DOC](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-doc/01d5d8c4-cf9c-4ef9-80fd-439e763cfe01), [XLS](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-xls/b0bd153a-9fad-456e-ac69-af652e6ef021), [PPT](https://learn.microsoft.com/en-us/openspecs/office_file_formats/ms-ppt/1fc22d56-28f9-4818-bd45-67c2bf721ccf).

**Ruta.** Parser específico o conversión controlada de una copia mediante aplicación compatible, manteniendo originales, versiones, avisos y hashes de derivados. No asumir que pasar a OOXML o PDF es neutro. LibreOffice documenta diferencias de importación/exportación en revisiones, objetos, tablas, masters y multimedia. [Limitaciones](https://help.libreoffice.org/latest/en-US/text/shared/guide/ms_import_export_limitations.html).

**Caso testigo.** DOC con revisiones/campos, XLS con fórmulas/hoja oculta y PPT con notas/objeto incrustado. Cierre: marcadores y canales comparados antes/después; macros, objetos y vínculos permanecen inactivos; cualquier pérdida queda visible. OLE detectado no identifica de forma suficiente todos los subtipos ni un paquete cifrado.

### FMT15 — RTF

**Fabricación.** Gramática de grupos, palabras de control y destinos, con Unicode, páginas de códigos y objetos. Borrar comandos con regex no constituye un parser fiable. [Especificación Microsoft RTF 1.9.1](https://officeprotocoldoc.z19.web.core.windows.net/files/Archive_References/%5BMSFT-RTF%5D.pdf).

**Ruta.** Parser con pila de estados, destinos y escapes → estructura/contenido → render Word/Writer si hace falta. No confundir datos ASCII con texto ya extraído.

**Caso testigo.** Unicode con fallback, carácter escapado, destino ignorable, tabla, campo y objeto. Cierre: fallback no duplicado, estructura correcta y objetos inventariados sin activarlos. El texto de control `\\rtf` no debe publicarse como extracción legible terminada.

### FMT16 — Excel XML 2003

**Fabricación.** XML con vocabulario de hoja de cálculo; distinto de XLS binario y XLSX ZIP. Resolver elementos por URI de namespace y nombre local, sin exigir un prefijo concreto. [Namespaces W3C](https://www.w3.org/TR/REC-xml-names/).

**Ruta.** Adaptador XML específico; interpretar posiciones, tipos, estilos y fórmulas según su esquema. **Pendiente de investigación técnica adicional:** la referencia histórica completa del esquema no se recuperó en esta sesión; validar detalles de índices y fórmulas antes de implementarlos. Cambiar el sufijo a `.xlsx` no convierte el contenido.

**Caso testigo propuesto.** Varias hojas, posiciones discontinuas, fusiones y fórmulas R1C1, comparadas con Excel. Cierre: posiciones/expresiones/valores exactos. Microsoft documenta que guardar como XML 2003 pierde gráficos, objetos, VBA y otras capacidades; no usarlo como puente de conservación integral. [Pérdidas documentadas](https://support.microsoft.com/en-us/excel/what-s-lost-when-i-save-my-workbook-as-an-xml-spreadsheet-2003-file).

### FMT17 — OpenDocument: ODT, ODS, ODP y ODG

**Fabricación.** El paquete habitual reúne contenido, estilos, metadatos, configuración y manifiesto. Detectar versión y variante; leer solo `content.xml` es insuficiente. [LibreOffice: estructura/versiones](https://help.libreoffice.org/latest/en-US/text/shared/00/00000021.html), [esquema ODF 1.4](https://docs.oasis-open.org/office/OpenDocument/os/part3-schema/OpenDocument-v1.4-os-part3-schema.pdf).

**Ruta.** Parser ODF por familia y render validado. Preservar fórmulas/valores, notas, cambios, masters, grupos y conectores. Expandir filas/celdas repetidas con límites controlados; una celda cubierta por fusión no equivale a una celda con valor independiente.

**Caso testigo.** ODT con notas/cambios, ODS con repetición/fusión/fórmula, ODP con notas/master y ODG con grupo/conector. Cierre: estructura lógica y referencias correctas, sin expansión descontrolada ni pérdida silenciosa. Familia adicional propuesta, no soportada por el programa solo por figurar en este atlas.

### FMT18 — ZIP, RAR, 7z, GZ y componentes internos

Un contenedor puede ser un documento estructurado, como DOCX/EPUB, o un archivo de varios documentos. **Reconocer ZIP no es motivo suficiente para excluirlo.** Inspeccionar de forma limitada sus entradas/firmas antes de decidir. RAR/7z/GZ genéricos quedan fuera del primer adaptador documental; una futura importación de archivos comprimidos necesita su política propia.

Los `.rels`, XML y medios internos de Office se leen como piezas del documento; no se procesan por separado como documentos independientes. Controles propuestos: rutas fuera del destino, expansión excesiva, archivos anidados, symlinks y entradas duplicadas. Cierre: contenedor documental legítimo llega a su parser; paquete peligroso o desconocido queda rechazado/pendiente con causa, sin escribir fuera del destino.

<a id="contrato-de-fidelidad"></a>
## Contrato de fidelidad

**Inventario por canal:** presente, extraído, inferido, omitido, no soportado, no determinado. No convertir «no determinado» en «ausente». El inventario mismo necesita controles; ningún extractor puede autodeclarar cobertura total solo contando lo que logró leer.

**Localizadores:** PDF página/región/transformación; imagen página IFD/fotograma y píxeles; Word parte XML y ancla; Excel hoja/celda/rango; PPT slide/shape; Visio page/shape/conector; CAD layout/handle/transformación; EPUB recurso/fragmento; JSON/XML ruta estructural. No aplicar una numeración de páginas ficticia a todos los formatos.

**Tablas:** celdas, valores originales, filas/columnas, spans, encabezados y unidades, además de la vista MD. **Fórmulas:** fuente matemática/OMML/MathML si existe, representación extraída e interpretación separada. PDF puede aportar datos matemáticos explícitos: buscar antes de reconocer píxeles. [Matemática PDF](https://pdfa.org/download-area/publications/BPG-Math-in-PDF.pdf).

Tesseract puede entregar TSV/hOCR con posiciones y confianza; necesita análisis adicional para documentos/tablas complejos. [Salidas](https://tesseract-ocr.github.io/tessdoc/Command-Line-Usage.html), [limitaciones y calidad](https://tesseract-ocr.github.io/tessdoc/ImproveQuality.html). Un LaTeX que compila o un JSON que valida pueden representar contenido equivocado.

**Cobertura visible:** `esperado`, `procesado`, `pendiente`, `omitido_por_limite`, `fallido`; usar null si el total aún es desconocido. Cada tope —imágenes, páginas, tokens, tiempo— debe mostrar su efecto. Conservar salida cruda, hash original, versiones, configuración, recibos y evidencia de revisión.

No descartar una figura únicamente por pesar menos de cierto número de KB: un sello, símbolo o etiqueta pequeña puede ser decisivo. Se puede reutilizar el análisis de una imagen idéntica por hash, conservando cada aparición, contexto y ancla; deduplicar bytes no autoriza borrar su relación con el documento.

**Texto/visión discrepantes:** conservar ambos candidatos y explicar el conflicto. No escoger por mayoría de tres motores que podrían compartir el mismo modelo o el mismo fallo. Cifras, unidades, negaciones y direcciones de flechas requieren validadores propios.

<a id="premortem-por-estructura"></a>
## Premortem por estructura

Estos escenarios refinan H04–H10 y H15 del informe; no se suman como bugs nuevos. Los riesgos sin ejecución local se etiquetan como hipótesis.

| Fallo anticipado | Evidencia / impacto 1–5 | Primaria, alternativa y cierre |
|---|---|---|
| EPUB clasificado como ZIP no documental | Ruta observada por código; 4: pierde un libro entero | Detector de contenedores; B: pendiente explícito. Fixture con spine y sufijo falso llega al parser correcto |
| RTF/HTML/XML Excel publicado como texto bruto válido | Rutas observadas; frecuencia no medida; 4 | Clasificación por estructura; B: mostrar original como no convertido. Control con códigos estructurales no recibe estado de extracción fiel |
| TIFF de varias páginas solo entrega la primera | Ruta externa inspeccionada, sin ejecución; 5 por omisión silenciosa | Inventario IFD; B: aviso y revisión manual. Tres páginas lógicas exactas, miniaturas aparte |
| Revisión rechazada de Word aparece como texto vigente | Hipótesis de integración; 5 por invertir contenido contractual | Política de revisiones explícita; B: ambas versiones identificadas. Fixture con aceptación/rechazo y comentario preserva sus estados |
| Fórmula Excel o signo matemático cambia manteniendo texto plausible | Riesgo documentado del formato; 5 por cifra incorrecta | Fuente nativa + validación crítica; B: incertidumbre y original. Símbolos y expresiones testigo exactos |
| OCR y render usan coordenadas distintas | Hipótesis de integración; 4 por evidencia equivocada | Transformaciones registradas; B: página entera. Marcas de control coinciden tras rotación/recorte |
| Un tope de descripciones se interpreta como documento terminado | Topes 12/20 y validador superficial en motor antecedente; 4 | Manifiesto de cobertura; B: continuar por subconjunto. Recurso 21 queda procesado o pendiente visible |

Responsable propuesto de adaptadores y pruebas: desarrollo/Codex; responsable del significado crítico y referencia oro: revisión conjunta con Gerardino. Ninguno se cierra por agregar una recomendación al informe.

<a id="criterios-para-publicar-soporte"></a>
## Criterios para publicar soporte

Un registro versionado alimentará **detector, router, CLI, interfaz, lotes y documentación**. Campos mínimos: familia/subtipo, extensiones orientativas, firma/estructura, canales, adaptador/versión, dependencias/licencia, límites, posibilidad offline, localizador fuente y controles aprobados. La interfaz distinguirá «detectado», «convertible», «parcial», «validado en piloto» y «pendiente».

Las capturas del usuario aportan pistas útiles, pero suman rutas de herramientas diferentes. La auditoría encontró 19 extensiones declaradas por el extractor externo de libros; no son 19 capacidades integradas ni 19 formatos distintos. El informe de motores documenta H15 y las rutas concretas.

Para cada familia: al menos un caso sano, uno con canal no trivial y uno que deba fallar o quedar parcial; agregarlos según el riesgo. Primero 24 documentos equilibrados de obra/biblioteca y hasta 96 unidades seleccionadas, más fixtures sintéticos pequeños. **Ese piloto no certifica todo el atlas:** una familia sin control ejecutado sigue pendiente para el lanzamiento.

Medir por familia y por canal: campos críticos, cobertura, relaciones, errores no detectados, falsas alarmas, tiempo/recursos y coste si interviene API. Guardar casos de fallo mínimos y saneados; la mejora consiste en corregir, añadir regresión y demostrar que no se rompió lo ya aprobado. No actualizar ni reentrenar automáticamente con documentos del cliente.

Fuera del compromiso inicial: audio/video, BIM nativo RVT/NWC y otros formatos no catalogados. Se pueden estudiar como módulos posteriores, pero el conocimiento de DWG no demuestra soporte de Revit/Navisworks. No se garantiza extracción universal ni que todos los modelos estén disponibles o autorizados para redistribución.
