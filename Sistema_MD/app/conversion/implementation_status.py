"""Puerta de entrega: enumera el plan entero, no confunde mocks con aceptación."""
import re
from pathlib import Path

EXPECTED_COUNTS = {1:9, 2:11, 3:10, 4:6, 5:8, 6:9, 7:9}
IN_PROGRESS = {
    'F1.03': 'Regresiones contrato existentes; aceptación completa de F1 pendiente.',
    'F1.05': 'Conectores PPT corregidos, piloto de fidelidad pendiente.',
    'F1.06': 'Regresiones Excel existentes, alcance por canales pendiente.',
    'F2.09': 'Interfaz operativa mejorada; comparación Qt/TypeScript pendiente.',
    'F3.02': 'Worker Docling integrado y protocolo simulado; entorno, activos y control real pendientes.',
    'F3.07': 'MarkItDown 0.1.7 integrado, controles reales HTML/DOCX/XLSX/PPTX; corpus de fidelidad pendiente.',
    'F5.02': 'Adaptadores API ampliados; transportes simulados no prueban cuentas/modelos.',
    'F5.03': 'Cinco perfiles/API y sesiones CLI; autorización real por cuenta pendiente.',
    'F5.04': 'Credenciales Windows y perfiles aislados probados con señuelos.',
    'F5.05': 'Pruebas de error simuladas; no aceptación de proveedor real.',
    'F5.06': 'Reserva durable y sin reenvío ambiguo; no coste monetario garantizado.',
    'F5.07': 'Modo Local por defecto y consentimiento explícito; no firewall OS.',
    'F7.02': 'Rutas internas relativas, destinos elegibles y preparación explícita por equipo; segunda PC e instalador comercial pendientes.',
}


def implementation_report(plan=None):
    plan = Path(plan) if plan else Path(__file__).resolve().parents[2] / 'PLAN_MAESTRO_IMPLEMENTACION.md'
    text = plan.read_text(encoding='utf-8')
    ids = list(dict.fromkeys(re.findall(r'\b(F[1-7]\.\d{2})\s+—',text)))
    expected = [f'F{phase}.{index:02}' for phase,count in EXPECTED_COUNTS.items() for index in range(1,count+1)]
    if set(ids) != set(expected):
        raise ValueError('El plan cambió: actualizar trazabilidad antes de declarar una entrega completa')
    return {'complete':False,'total':len(ids),'accepted':0,'external_calls':0,
            'tasks':[{'id':key,'state':'en_validacion' if key in IN_PROGRESS else 'pendiente',
                      'evidence':IN_PROGRESS.get(key,'Sin evidencia de aceptación registrada.')} for key in ids],
            'notice':'Ningún conteo de pruebas sustituye aceptación de las 62 tareas. El producto completo sigue abierto.'}
