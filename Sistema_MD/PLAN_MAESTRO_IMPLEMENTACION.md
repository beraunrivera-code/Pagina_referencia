# Plan maestro de implementación — Sistema de Conversión Local/API

Fecha: 2026-09-20. Versión del plan: 1.0. Estado general: **PENDIENTE DE IMPLEMENTACIÓN**.
Este documento organiza el trabajo; no activa configuraciones, instala motores ni acredita funciones terminadas.
Base observada: **15 huecos abiertos, 0 cerrados y 3 defectos reproducidos**. La maqueta es exploratoria.
Los nombres nuevos de archivos y módulos son propuestas; confirmarlos al iniciar cada fase para evitar duplicados.

## Índice

1. [Objetivo y decisiones](#objetivo-y-decisiones)
2. [Estado real y mapa del código](#estado-real-y-mapa-del-codigo)
3. [Arquitectura y dependencias](#arquitectura-y-dependencias)
4. [F1 — Estabilizar](#f1-estabilizar)
5. [F2 — Contrato, catálogo, diagnóstico y experiencia](#f2-contrato)
6. [F3 — Local inicial](#f3-local-inicial)
7. [F4 — Alternativas locales](#f4-alternativas)
8. [F5 — API ampliada](#f5-api)
9. [F6 — Calidad continua e interfaz operativa](#f6-calidad)
10. [F7 — Distribución](#f7-distribucion)
11. [Corpus oro y aceptación](#corpus-oro)
12. [Ocho puertas comerciales](#puertas-comerciales)
13. [Trazabilidad, gestión y siguiente paso](#continuacion)

Anexos al mismo nivel: [investigación y evidencia](INVESTIGACION_MOTORES_LOCAL_API.md), [ADN de formatos](ADN_FORMATOS_EXTRACCION.md), [plan estructurado JSON](PLAN_MOTORES_LOCAL_API.json).
Los anexos conservan fuentes oficiales, versiones, límites y pruebas de reproducción. El JSON del plan **no es configuración de ejecución**.

<a id="objetivo-y-decisiones"></a>
## 1. Objetivo y decisiones

**Objetivo:** evolucionar el programa existente hacia una aplicación navegable con Local estricto y asistencia API, salidas MD/JSON, procedencia y diagnóstico verificable.
Éxito significa conservar los canales declarados, identificar pérdidas e incertidumbre y demostrar resultados sobre controles; no prometer extracción universal.

| Tipo | Decisión y estado |
|---|---|
| Confirmado por Gerardino | Ambas modalidades; MD y JSON estructurado; misma prioridad para obra y libros/artículos; comercialización; interfaz navegable; estudiar el ADN de formatos |
| Recomendación PENDIENTE | Una aplicación con núcleo Python, biblioteca/cola compartidas y workers versionados; Local predeterminado sin salto remoto automático |
| Recomendación PENDIENTE | Docling primero para PDF/imágenes; conservar rutas nativas; MarkItDown selectivo; Marker/MinerU solo si aportan ventaja demostrada |
| Decisión de producto PENDIENTE | Edición abierta/cerrada/mixta, licencia de venta, política de activación offline y alcance del primer instalador |
| Decisión técnica PENDIENTE | Interfaz PySide/Qt frente a TypeScript con contenedor desktop; instalador compatible con COM/runtimes; versiones exactas y bundles |
| Parámetros PENDIENTES | Presupuesto API, modelos/regiones, documentos permitidos del piloto, segunda PC, fecha y coste de lanzamiento |

La creación de este plan no autoriza llamadas pagadas, distribución pública ni instalaciones. Se ejecutarán cuando estén dentro del alcance aprobado.
Un cambio de decisión debe registrar motivo, impacto, evidencia y fase afectada; no convertir una recomendación en requisito confirmado.

<a id="estado-real-y-mapa-del-codigo"></a>
## 2. Estado real y mapa del código

Raíz auditada: `C:/Users/USUARIO_ORIGEN/Documents/Codex/2026-09-10/realtime-voice-chat/sistema_conversion`.
Todas las rutas de código de este plan son relativas a esa raíz; las rutas de anexos son relativas a este Markdown.
**E = existente identificado; P = propuesto y PENDIENTE**. Confirmar firmas y puntos de llamada antes de editar; las líneas del informe son una fotografía.

| Estado | Archivos/módulos | Responsabilidad que se conserva o amplía |
|---|---|---|
| E | `conversion/pipeline.py`, `local_io.py`, `workflow.py` | Detección, hashes, entradas locales, enrutamiento y originales |
| E | `conversion/documents.py`, `storage.py`, `jobs.py` | Contrato actual, paquetes MD/JSON/recursos, trabajos y publicación |
| E | `conversion/native.py`, `excel_structure.py` | Office/Visio/CAD y estructura Excel; cobertura parcial documentada |
| E | `conversion/batches.py`, `ai_batches.py` | Estado durable, lotes, pausa, recuperación y recibos |
| E | `conversion/providers.py`, `provider_errors.py`, `credentials.py`, `access.py` | Gemini/DeepSeek, acceso CLI y credenciales; no confundir CLI con API |
| E | `conversion/app.py`, `ui_widgets.py`, `visor.py`, `retrieval.py` | Interfaz/visor y consulta; conservar navegación, selección y controles existentes |
| E | `conversion/diagnostics.py`, `tests/test_*.py`, `tests/support.py` | Diagnóstico y pruebas; ampliar evidencias sin reemplazar historial |
| P | `conversion/format_registry.py`, `document_v2.py`, `migrations.py` | Catálogo común, modelo tipado y compatibilidad de lectura |
| P | `conversion/engines/`, `workers/`, `conversion/engine_manager.py` | Adaptadores, procesos, salud, activos y compatibilidad |
| P | `conversion/quality/`, `tests/gold/`, `tests/fixtures/` | Validadores, referencias oro y regresiones |
| P | `ui_desktop/` o módulo Qt; `packaging/` | Interfaz elegida y distribución; nombres finales tras decisión técnica |

Python observado: 3.13.14; RTX 3060 de 12 GB. RAM física no medida.
MarkItDown 0.1.6 y Marker 1.10.2 están en el intérprete observado; Docling/MinerU no se localizaron allí. Instalado no significa integrado ni probado.
La suite reportó 135 aprobadas el 15-09; es evidencia histórica, no resultado de esta investigación.
No se ejecutó el corpus oro, no se descargaron nuevos pesos y no se resolvieron derechos de redistribución.

<a id="arquitectura-y-dependencias"></a>
## 3. Arquitectura y dependencias

Ruta PENDIENTE: fuente/alcance → firma e inventario → parser nativo/worker → normalización → controles → paquete MD/JSON → biblioteca/revisión.
Modo API reutiliza extracción local y envía solo el alcance autorizado; Local no convierte un error en permiso para enviar datos.
El JSON canónico conserva elementos, relaciones y procedencia; Markdown es una vista. Siempre se conserva salida cruda del motor.
Protocolo PENDIENTE: `probe`, `convert`, `normalize`, `cancel`, `healthcheck`, con versiones y resultados parciales explícitos.
Separar `present`, `importable`, `models_ready`, `control_passed` y `offline_verified`; ninguna señal sustituye las demás.
Un worker GPU pesado inicialmente; CPU ligera con límites. Medir memoria antes de ampliar concurrencia.
Procesos/entornos separados aíslan fallos y dependencias; no constituyen por sí solos un sandbox de seguridad.
Los documentos, metadatos y respuestas de IA son datos; no pueden autorizar herramientas, macros ni conexiones externas.
Aplicación, motores, pesos, telemetría, credenciales y activación comercial requieren políticas independientes.

**Orden:** F1 → F2 → F3 → F4 cuando aporte valor; F5 depende de F2 y capacidades probadas; F6 consolida desde controles iniciados en F1; F7 depende de evidencias aceptadas.
Licencias C01 empiezan en F1; corpus oro y prototipo UX en F2; pruebas adversas y privacidad acompañan cada fase.
Tras F2 pueden prepararse simulaciones API y anotaciones oro en paralelo al worker local; no comparten archivos sin asignación de responsabilidad.
No hay fecha fija: estimar cada fase al conocer alcance, fixtures, descargas y decisiones. Registrar tiempo real y reajustar sin reducir pruebas de cierre.

<a id="f1-estabilizar"></a>
## 4. F1 — Estabilizar el núcleo | PENDIENTE

**Entrada:** anexos conservados, alcance F1 aprobado y estado del árbol de trabajo comprobado. Dependencia: ninguna instalación nueva.
1. F1.01 — Leer `docs/ESTADO.md`, reglas locales y pruebas actuales; registrar cambios ajenos y snapshot de fuentes antes de editar.
2. F1.02 — Reproducir H01 en prueba aislada: falta `title` y heading fuera de rango no deben aceptarse como contrato válido.
3. F1.03 — Corregir validación/publicación en `conversion/documents.py` y su integración en `jobs.py`; añadir `tests/test_document_contract.py` (P).
4. F1.04 — Reproducir H02 con dos cajas PPT y un conector; ampliar inventario de `native.py` y `tests/test_native.py` sin depender de texto vacío.
5. F1.05 — Preservar relación si puede extraerse; en caso contrario marcarla pendiente con evidencia. Detectarla no equivale a interpretarla.
6. F1.06 — Reproducir H03 en rama Excel grande con celda intermedia vacía; corregir coordenadas seguras y verificar fórmulas/valores.
7. F1.07 — Ejecutar controles sanos equivalentes y pruebas afectadas; después la suite documentada `python -m unittest discover -s tests -v`.
8. F1.08 — Abrir inventario C01: dependencias, PyMuPDF, COM/Office, ODA, pesos y licencias; ninguna inclusión comercial se deduce de estar instalado.
9. F1.09 — Registrar prueba, versión, esperado/obtenido y diff; cerrar cada H01–H03 solo si su criterio pasa y no quedan regresiones atribuibles.
**Salida:** tres correcciones con regresión demostrada; suite actual y fallos previos claramente separados; originales y paquetes existentes intactos.
**Rollback:** revertir únicamente el cambio propio fallido, conservar reproducción y evidencia, y mantener el hueco abierto; no descartar trabajo ajeno.

<a id="f2-contrato"></a>
## 5. F2 — Contrato, catálogo, diagnóstico y experiencia | PENDIENTE

**Entrada:** F1 aceptada. Dependencias: modelo documental acordado y canales mínimos del piloto.
1. F2.01 — Definir `document_v2.py` y esquema versionado (P): documento, unidades, bloques, relaciones, activos, ejecución, cobertura y QA.
2. F2.02 — Definir localizadores por formato; bbox con unidades/origen/transformación, null cuando no exista; distinguir índice físico y página impresa.
3. F2.03 — Modelar tablas con celdas/spans/encabezados; Excel con fórmula/caché/valor/tipo; separar fuente matemática, reconocimiento e interpretación.
4. F2.04 — Implementar `migrations.py` (P) de lectura v1→vista v2; conservar v1 crudo; comprobar enlaces/recursos sin reconversión masiva.
5. F2.05 — Crear `format_registry.py` (P): firmas/subtipos, canales, adaptadores, versiones, límites y estados; conectarlo a detector/router/UI/CLI.
6. F2.06 — Cubrir H15 con fixtures: EPUB dentro de ZIP, extensiones disfrazadas, RTF/HTM/XML Excel y TIFF multipágina; sin adaptador, estado pendiente explícito.
7. F2.07 — Ampliar `diagnostics.py`: Entorno/Ejecución/Contenido independientes; prueba enlazada por hash; recurrencia reabre; no cierre por texto libre.
8. F2.08 — Ajustar `storage.py`/`jobs.py`: publicación coherente MD+JSON+verificación+recursos+crudos, manifiesto y caché por identidad completa.
9. F2.09 — Diseñar prototipo Inicio/Biblioteca/Cola/Revisar/Motores e IA/Diagnóstico; validar tareas con Gerardino y conservar funciones existentes.
10. F2.10 — Comparar Qt/PySide y TypeScript desktop con un flujo mínimo; decidir por usabilidad, integración, licencias, tamaño y mantenimiento.
11. F2.11 — Iniciar `tests/gold/` (P) con etiquetas fuera del alcance de los motores; ampliar pruebas de documentos, workflow, diagnóstico y visor.
**Salida:** paquetes v1 legibles; v2 válido y referencialmente íntegro; catálogo coincide con comportamiento; prototipo y elección técnica documentados.
**Rollback:** lectura v1 original disponible, v2 bajo activación reversible; exportar evidencia antes de desactivar; ninguna migración destructiva.

<a id="f3-local-inicial"></a>
## 6. F3 — Local inicial | PENDIENTE

**Entrada:** F2 aceptada y corpus anotado inicial. Dependencias: bundle/licencia/versiones definidos; instalación y tamaño de descarga dentro de autorización.
1. F3.01 — Fijar Docling y dependencias por release/commit; estimar disco/RAM/VRAM, comprobar Python del worker y hashes/licencias de activos.
2. F3.02 — Crear worker aislado en `workers/docling/` y adaptador en `conversion/engines/docling_adapter.py` (P); no actualizar Python productivo.
3. F3.03 — Implementar protocolo, timeouts/cancelación, salida parcial, normalización v2 y conservación de JSON nativo; contrato simulado antes de modelo real.
4. F3.04 — Preparar activos y política de red del perfil Local; probar en entorno aislado sin egress, incluyendo activos ausentes y recursos remotos incrustados.
5. F3.05 — Integrar PDF por página/región: texto/tags/capas OCR, render/coordenadas, tablas/fórmulas; inventariar todas las unidades antes de publicarlas.
6. F3.06 — Integrar imágenes/TIFF con páginas lógicas y orientación; normalización reversible, límites de píxeles y conservación del original.
7. F3.07 — Añadir MarkItDown selectivo con allowlist; plugins/rutas remotas deshabilitados; audio queda fuera hasta disponer de ASR local validado.
8. F3.08 — Completar controles nativos de Word/PPT/legacy y Visio/CAD por canales; conservar Excel propio. Lo no cubierto continúa parcial, no recibe verde.
9. F3.09 — Extender `batches.py`/`engine_manager.py` con un worker GPU, límites, caché por configuración y recuperación tras caída/VRAM insuficiente.
10. F3.10 — Ejecutar el piloto Local acordado; recoger calidad por familia, límites, tiempos y memoria; publicar matriz de capacidades demostradas.
**Salida:** cero egress en controles Local; faltantes fallan claramente; MD/JSON coherentes; ninguna unidad omitida silenciosamente; alcance y recursos medidos.
**Rollback:** desactivar el nuevo adaptador y volver a rutas previas identificadas; conservar parciales y resultados nuevos; no etiquetar la ruta anterior como equivalente.

<a id="f4-alternativas"></a>
## 7. F4 — Marker y MinerU por necesidad | PENDIENTE Y CONDICIONAL

**Entrada:** F3 identifica clases deficientes. Si no hay necesidad o licencia compatible, registrar exclusión; no instalar por completar una lista.
1. F4.01 — Seleccionar fallos concretos y controles sanos comparables; congelar fuente, alcance, configuración y criterios antes de medir.
2. F4.02 — Resolver derechos del bundle exacto y entorno requerido; no confundir Marker local 1.10.2 con release 2.x ni ramas móviles con versiones fijadas.
3. F4.03 — Crear `workers/marker/`, `workers/mineru/` y adaptadores (P) únicamente para candidatos aprobados; aislar dependencias incompatibles.
4. F4.04 — Mapear raíces/páginas/bloques/activos; diferenciar envoltura de respuesta CLI de JSON semántico y confrontar alcance solicitado/recibido.
5. F4.05 — Repetir pruebas Local y de fallo; comparar calidad/coste local/latencia/memoria en las mismas unidades, sin promediar familias desiguales.
6. F4.06 — Elegir mantener, reservar para ciertos casos o descartar; documentar evidencia y actualizar catálogo, nunca convertir cada archivo cuatro veces por defecto.
**Salida:** decisión por clase con evidencia y licencia; un candidato no mejora el producto solo por ejecutar sin error.
**Rollback:** desregistrar candidato, conservar comparación y salida cruda; restaurar política F3 sin alterar documentos aceptados.

<a id="f5-api"></a>
## 8. F5 — API ampliada y BYOK | PENDIENTE

**Entrada:** F2 aceptada y capacidades necesarias definidas; pruebas simuladas pueden prepararse en paralelo a F3.
1. F5.01 — Definir contrato por proveedor/modelo/endpoint/región; separar texto, visión, documento y JSON; /models no prueba capacidades.
2. F5.02 — Reutilizar `providers.py`/`provider_errors.py`; agregar adaptadores OpenAI, Anthropic y Qwen (P), conservando Gemini/DeepSeek.
3. F5.03 — Integrar flujo proveedor→credencial segura→comprobación→modelo→alcance/presupuesto; mantener acceso CLI separado de clave API.
4. F5.04 — Ampliar credenciales/recibos sin secretos en argumentos, logs, paquetes ni diagnósticos; probar reemplazo/borrado con clave señuelo.
5. F5.05 — Simular 401, 429, timeout, corte, respuesta truncada y formato inválido en `tests/test_providers.py`/`test_ai_batches.py`.
6. F5.06 — Hacer durable la intención/envío/recibo; cuando consumo sea incierto, dejarlo desconocido y pausar según política; no cambiar proveedor silenciosamente.
7. F5.07 — Mostrar qué páginas/regiones salen, destino, presupuesto y retención conocida; mantener Local intacto y sin fallback externo.
8. F5.08 — Tras autorización de proveedor/presupuesto, ejecutar control sintético real mínimo y piloto autorizado; medir coste por unidad aceptada.
**Salida:** capacidades demostradas por ruta, presupuesto respetado, fallos recuperables, claves ausentes de exportación y costes desconocidos visibles.
**Rollback:** deshabilitar el adaptador problemático; conservar recibos y salidas, continuar trabajo local independiente; no reenvío automático de solicitudes inciertas.

<a id="f6-calidad"></a>
## 9. F6 — Calidad continua e interfaz operativa | PENDIENTE

**Entrada:** contrato F2 y evidencias F3/F5; F4 solo si se adopta. Las regresiones y la anotación oro comienzan antes de esta fase.
1. F6.01 — Implementar `conversion/quality/` (P): cobertura, valores críticos, orden, tablas/spans, fórmulas, vínculos de figuras y grafos.
2. F6.02 — Calibrar evaluadores con casos correctos y errores sembrados; medir falsas alarmas/errores no detectados, no solo exactitud de extracción.
3. F6.03 — Crear paquetes de fallo saneados: identidad, versión, fase, esperado/obtenido, causa hipotética/confirmada y reproducción mínima.
4. F6.04 — Integrar nueva interfaz según decisión F2: Biblioteca/Cola/Revisar, búsqueda contextual, capítulos/hojas/layouts y exportación.
5. F6.05 — Vincular original/recorte con Lectura/MD/JSON y dudas; sincronizar solo donde haya correspondencia; virtualizar tablas sin recortar datos.
6. F6.06 — Mostrar fase/unidades/transcurrido y resultado conservado; porcentaje con denominador conocido; pausa/cancelación solo con semántica real.
7. F6.07 — Mantener Atrás/Adelante, scroll, selección y controles del visor; validar teclado, Narrador, contraste y escalados.
8. F6.08 — Comparar versiones contra oro; correcciones humanas quedan identificadas y no sustituyen silenciosamente la salida del motor.
9. F6.09 — Probar corte de worker, reapertura, reanudación y diagnóstico; una repetición del defecto reabre el registro enlazado.
**Salida:** revisión usable sin guía del desarrollador, alertas calibradas, trazabilidad hasta fuente y regresiones explicadas por versión.
**Rollback:** conservar interfaz anterior durante transición y paquetes compatibles; restaurar versión evaluada sin borrar decisiones ni anotaciones.

<a id="f7-distribucion"></a>
## 10. F7 — Producto distribuible | PENDIENTE

**Entrada:** piloto aceptado, interfaz operativa y decisiones comerciales explícitas; ocho puertas C01–C08 con evidencias.
1. F7.01 — Cerrar SBOM/licencias/atribuciones de app, transitivos, binarios, pesos y fuentes; excluir o sustituir componentes no resueltos y volver a probar.
2. F7.02 — Elegir instalador tras probar COM/runtimes; separar núcleo, motores y modelos; dos accesos directos opcionales a la misma aplicación.
3. F7.03 — Construir `packaging/` (P) con manifiestos, hashes, firma del editor, sello de tiempo y versiones recuperables.
4. F7.04 — Implementar activación/actualización conforme a política elegida; descargas explícitas y actualización fuera de trabajos activos.
5. F7.05 — Probar paquete manipulado, corte de actualización y recuperación; migración respaldada; documentos/configuración permanecen disponibles.
6. F7.06 — Instalar en segunda PC sin entorno de desarrollo; canario con/sin GPU y sin red, con activos preparados; doctor explica cada ausencia.
7. F7.07 — Completar controles adversos de archivos, visor, workers y servidor local si existe; venv no se presenta como sandbox.
8. F7.08 — Publicar matriz real de soporte, límites, privacidad, retención, licencia, consumo BYOK, soporte y actualización; no anunciar módulos pendientes.
9. F7.09 — Probar desinstalación con elección explícita sobre datos; empaquetar manual, canario, registro de cambios y evidencias de lanzamiento.
**Salida:** segunda PC y usuarios completan tareas; C01–C08 aceptadas; versión y alcance comercial aprobados, con recuperación demostrada.
**Rollback:** retirar versión defectuosa de distribución, ofrecer versión anterior compatible y restauración documentada; preservar datos/recibos.

<a id="corpus-oro"></a>
## 11. Corpus oro y aceptación | PENDIENTE

**Muestra inicial:** 24 documentos reales permitidos: 12 de obra + 12 de libros/artículos; hasta 96 unidades seleccionadas, además de fixtures pequeños.
Unidad significa página/hoja/slide/layout/fragmento según formato; registrar selección y denominador; no inventar páginas para EPUB refluible.
Obra: Excel con vacíos/fórmulas/caché, Word con notas/tablas, PPT con conectores, PDF mixto, planos rotados, Visio y CAD con unidades.
Biblioteca: digital/escaneo, columnas, tabla multipágina, ecuaciones, notas, rotación, EPUB y capítulos largos; equilibrar clases sin forzar cobertura ficticia.
Anotar antes de ejecutar: campos críticos, secuencia, celdas/spans, fórmulas, relaciones y canales presentes; conservar evidencia humana de cada respuesta.
Separar ejemplos de desarrollo y evaluación; motores/prompts no acceden a respuestas oro. Cambiar una etiqueta exige motivo y revisión.
Por familia: control sano, canal no trivial y fallo/parcial esperado. Familias sin prueba ejecutada permanecen pendientes aunque figuren en el atlas.
Medir cobertura, CER/WER auxiliar, exactitud crítica, tablas, orden, fórmulas, relaciones, alertas, recursos y coste reportado; latencias percentiles solo con muestra suficiente.
**Puerta de fidelidad:** todos los campos críticos anotados correctos; ninguna unidad/canal perdido sin declarar; duda visible; MD/JSON apuntan a la misma evidencia.
**Puerta operativa:** Local sin egress, claves ausentes, pausa/reanudación coherente, error sembrado detectado y control sano no rechazado indebidamente.
Un JSON válido, LaTeX compilable, confianza alta o respuestas de comprensión correctas no sustituyen la revisión de canales.
Publicar resultados por familia y límites; el piloto no certifica todo documento futuro ni los 18 grupos del atlas.

<a id="puertas-comerciales"></a>
## 12. Ocho puertas comerciales | TODAS PENDIENTES

| Puerta | Trabajo desde | Evidencia necesaria para lanzar |
|---|---|---|
| C01 Licencias | F1/F4/F7 | SBOM por versión/peso, obligaciones y redistribución resueltas; alternativa probada para cada componente excluido |
| C02 Navegación | F2/F6 | Usuario agrega, revisa duda, vuelve a fuente y exporta; teclado/Narrador/contraste/escala; estado de navegación conservado |
| C03 Progreso/recuperación | F3/F5/F6 | Fallo/corte controlado conserva trabajo; pendiente localizable; no duplica lo facturable; no inventa porcentaje/coste |
| C04 Instalación/actualización | F7 | Editor verificable, paquete alterado rechazado, actualización recuperable y datos preservados en segunda PC |
| C05 Seguridad documental | F2–F7 | Macros inactivas, rutas/contenedores/XML/recursos externos controlados; límites y permisos de workers probados |
| C06 Claves/privacidad/licencia | F5/F7 | Señuelo ausente de exportación, alcance de envío visible, retención definida y Local cumple política offline contratada |
| C07 Resiliencia/soporte | F3/F6/F7 | Paquete saneado distingue fallos, cola resiste un worker caído, corrección con regresión y versión anterior recuperable |
| C08 Alcance comercial | F7 | Matriz pública real, piloto/usuarios/segunda PC, licencias y políticas aceptadas; BYOK separado del precio de aplicación |

Autodesk y Netflix aportan patrones públicos de experiencia, distribución y resiliencia; no se copian supuestos internos ni infraestructura empresarial completa.
Audio/video, RVT/NWC, colaboración, marketplace, facturación y sincronización cloud son ampliaciones PENDIENTES, fuera del primer compromiso.
Fuentes y alcance de cada puerta: [informe, sección 17](INVESTIGACION_MOTORES_LOCAL_API.md#17-producto-comercial-ocho-criterios-de-lanzamiento).

<a id="continuacion"></a>
## 13. Trazabilidad, gestión y siguiente paso

| Huecos abiertos | Fase responsable de producir evidencia de cierre |
|---|---|
| H01 contrato inválido; H02 conector PPT; H03 EmptyCell | F1 |
| H04 offline; H05 adaptadores; H07 PDF/OCR; H13 operación | F3, con regresión F4/F5/F6 |
| H06 JSON; H11 diagnóstico; H15 catálogo | F2 y validación integrada F6 |
| H08 Office; H09 Visio/CAD | F3 por canal; lo pendiente no se anuncia, validación F6 |
| H10 evaluador | Oro desde F2; calibración y cierre F6 |
| H12 proveedores | F5, solo rutas realmente verificadas |
| H14 distribución | F7 y ocho puertas comerciales |

Cada tarea registra responsable, estado, dependencia, archivos, prueba, resultado y evidencia; estados: PENDIENTE → EN CURSO → EN VALIDACIÓN → ACEPTADA.
Si se bloquea: anotar hecho, hipótesis, siguiente prueba discriminante y dato/autorización necesarios; continuar tareas independientes.
Desarrollo/Codex propone cambios y pruebas; Gerardino valida significado crítico y decisiones; obligaciones comerciales/seguridad requieren revisión competente del producto final.
Al cerrar fase, actualizar estado vivo y anexos sin declarar cerrado un hueco por haber redactado una solución. Mantener historial por tema y referencias a evidencias.
**Ahora:** este plan y los tres anexos documentan el trabajo. Las fases F1–F7 y ampliaciones continúan PENDIENTES; no se ha implementado este plan.
**Siguiente bloque exacto recomendado:** ejecutar F1.01–F1.09 sobre el núcleo existente, sin nuevos motores, descargas ni llamadas API.
**Instrucción de continuación:** «Implementa F1 de PLAN_MAESTRO_IMPLEMENTACION.md: corrige H01–H03, añade sus regresiones, ejecuta los controles y la suite documentada, e informa evidencia y huecos realmente cerrados. No avances a F2 ni instales motores».
La primera acción del ejecutor es leer el estado y cambios actuales; el comando de suite indicado en F1 se ejecuta desde la raíz del programa cuando corresponda.

