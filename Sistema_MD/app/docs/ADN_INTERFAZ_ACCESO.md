# ADN reutilizable — interfaz, acceso y diagnóstico [2026-09-14]

Modelo mental: la interfaz recoge intención; el adaptador ejecuta; la reserva evita duplicación; el recibo registra evidencia. Atrás/Adelante mueve pantallas, nunca revierte ni reejecuta operaciones.

- Una tabla y su contenedor son regiones distintas: cada tabla necesita barras ligadas a xview/yview; el formulario largo necesita Canvas y scrollregion.
- Probar barras con filas y columnas desbordadas en ventana pequeña, no solo por existencia del widget. Tk no entrega eventos de ratón a ventanas retiradas; el control de interacción debe mostrarse.
- La rueda del formulario solo debe afectar sus descendientes; no cambiar por accidente un proveedor ni desplazar otras tablas.
- Identidad ≠ instalación ≠ permiso ≠ cuota ≠ calidad. Detectar el ejecutable o una clave no verifica los otros cuatro.
- API: secreto local en almacén Windows/entorno, transporte directo documentado. CLI: ejecutable oficial y autenticación soportada; no fabricar un endpoint, exportar tokens de sesión ni instalar wrappers por nombre.
- Puede integrarse el lanzamiento del CLI en un botón; la primera autorización humana sigue perteneciendo a la interfaz oficial. El botón no debe mandar prompts ni documentos.
- Error visible: fase + código seguro + acción. No registrar textos remotos arbitrarios o claves. Firma estable sin UUID/ruta del intento; evidencia conserva dónde ocurrió.
- Una respuesta rechazada puede consumir tokens. Guardar consumo antes de validar; ausencia de dato = desconocido, nunca cero.
- Cada prueba de diagnóstico introduce un fallo conocido y exige la clasificación esperada; medir calidad documental requiere otro control contra fuentes.

Fuentes primarias consultadas:

- [Python: ttk, Treeview y Scrollbar](https://docs.python.org/3/library/tkinter.ttk.html).
- [Antigravity: instalación y acceso](https://antigravity.google/docs/cli/install/).
- [Antigravity: headless y credenciales previas](https://antigravity.google/docs/cli/headless/).
- [Gemini CLI: autenticación](https://geminicli.com/docs/get-started/authentication/).
- [DeepSeek: integración API](https://api-docs.deepseek.com/).

Instrumentos del proyecto: tests/test_ui.py (gestos/navegación/acceso simulado), tests/test_providers.py (fallos/recibos/reuso), tests/test_diagnostics.py (agrupación/recurrencia/reparación). No sustituyen un login real ni un contraste visual completo.
