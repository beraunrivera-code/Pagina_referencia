# Regla documental del proyecto

Antes de releer PDF, Excel, Word, DWG u otro nativo, lee `docs/CONTRATO_AGENTES.md` y consulta el índice local. No llames a proveedores externos ni cambies cuentas sin una instrucción explícita del usuario.

## Contrato de entrega y portabilidad [2026-09-21]

- Antes de cerrar una implementación, confrontar TODAS las tareas F1–F7 del plan maestro con código y pruebas; ejecutar `estado-implementacion`. No presentar una mejora parcial como cumplimiento integral ni mocks como conexión real.
- Integrar capacidades de motores en la cola, biblioteca y revisión comunes. Un enlace, ejecutable detectado o adaptador sin control no equivale a capacidad validada.
- Rutas relativas a la instalación o elegidas por el usuario. Ningún nombre de cuenta, letra de unidad, usuario Windows o entorno de esta PC es requisito universal.
- `fab1`–`fab5` es compatibilidad opcional con la instalación histórica. API, sesión CLI y usuario Windows son identidades distintas. No copiar OAuth entre equipos ni rotar cuentas automáticamente.
- Datos portables; runtimes, modelos y autorización se verifican/preparan en cada PC. No afirmar portabilidad comercial sin control independiente en otra PC y revisión de licencias.
