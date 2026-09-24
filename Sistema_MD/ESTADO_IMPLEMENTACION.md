# Sistema MD: alcance real de esta integración

Actualizado [2026-09-21]. Entrega parcial del plan maestro, no producto completo certificado.

## Lo implementado

- Una interfaz común de cola, biblioteca y revisión. Motores locales y proveedores entregan al mismo flujo, no a herramientas desconectadas.
- MarkItDown 0.1.7 en entorno separado: controles reales HTML, DOCX, XLSX, PPTX, TXT, MD, CSV y EPUB. Conserva original, Markdown y JSON crudos. Fidelidad por canales todavía pendiente.
- Worker Docling y selector/configuración integrados. Protocolo probado con simulaciones; instalación, modelos y conversión real pendientes. Marker/MinerU siguen condicionales según el plan.
- Cinco proveedores API: Gemini, DeepSeek, OpenAI, Anthropic y Qwen; cinco posiciones de clave por proveedor. CLI: Gemini, Antigravity, Codex y Claude. Los nuevos adaptadores y sesiones se verificaron con respuestas simuladas, no con autenticación/consumo reales.
- Selecciones persistentes sin guardar claves en el proyecto. Local es el modo predeterminado al abrir; no se conserva permiso para enviar documentos. Sin rotación automática de cuentas ni reenvío ante resultado incierto.
- Sesiones propias de Codex/Claude; compatibilidad opcional con `fab1`–`fab5` para Antigravity. No se copian credenciales OAuth entre equipos.
- Rutas internas relativas, carpetas de datos/MD elegibles y motores configurables. Preparación explícita en otra PC Windows x64/Python 3.12, con recuperación de una instalación propia incompleta.
- Nuevas exportaciones con enlaces relativos cuando sea posible. Control de traslado de biblioteca y MD a otra raíz; aviso explícito si están en unidades diferentes.
- Matriz de las 62 tareas F1–F7: `estado-implementacion --exigir-completo` rechaza declarar el plan completo mientras falten evidencias de aceptación.

## Lo que estas pruebas no demuestran

- Compatibilidad con cualquier IA desconocida: cada proveedor necesita su contrato/adaptador y verificación de su versión.
- Autenticación perpetua, cuota o modelo vigente de una cuenta, ni coste monetario máximo garantizado. CLI con suscripción no significa offline.
- Aislamiento de red del sistema operativo. El guard de Python bloquea sockets/subprocesos auditados, pero no certifica bibliotecas nativas frente a un firewall.
- Extracción perfecta: el contrato publicado sigue en v1. Faltan contrato v2 completo, controles de fidelidad del corpus y aceptación por formato/canal.
- Portabilidad comercial: falta probar una segunda PC limpia, instalador autónomo, inventario/licencias del conjunto, actualizaciones, recuperación y distribución.
- Migración automática de la biblioteca de Claude o revisión completa de Drive. Los originales de Claude permanecen protegidos y separados.
- Inspección visual final: Computer Use no expuso la ventana sintética. Pasaron controles geométricos, no una revisión visual humana de esta versión.

## Evidencia reproducible

Las pruebas están en `app/tests/`. Los informes JSON y logs de cada corrida se conservan en `_qa/`, incluidas las corridas fallidas. No se eliminan para ocultar regresiones.

Última regresión del bloque: **401 pruebas, 0 fallos, 0 errores, 0 omitidas**, 104,906 segundos. Informe: [_qa/regresion_final_portabilidad.json](_qa/regresion_final_portabilidad.json). No es aceptación completa del plan ni prueba en otra PC.

Se corrigieron dos fallos observados durante el cierre: carrera al inicializar SQLite y bloqueo intermitente Windows al promover un paquete ya escrito. La promoción local tiene reintento acotado; no repite conversiones ni llamadas IA. Se probó que un bloqueo transitorio conserva una sola llamada simulada y su consumo; un fallo permanente conserva el paquete pendiente y no se presenta como éxito. El fallo original de una aserción de presupuesto es compatible con esa causa, pero su recibo no permitió atribuirlo definitivamente; ahora la aserción incluye diagnóstico.

`iniciar.py --check` comprueba el arranque local; `motores-locales` comprueba presencia, no capacidad ni fidelidad. Los controles reales de MarkItDown generan documentos sintéticos sin enviar documentos del usuario ni consumir IA.

Procedimiento de otra PC y comandos con parámetros entre comillas: [INICIO_RAPIDO.md](INICIO_RAPIDO.md).

## Nota de la copia ZIP

El resumen de 401 pruebas es histórico y anonimizado del equipo de origen. Los logs completos permanecen allí, no viajan en este paquete. No prueba esta PC. Sin el worker opcional, sus pruebas reales pueden omitirse. Repite el control de la guía.
