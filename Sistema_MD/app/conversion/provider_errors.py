"""Causas seguras y estables: no introducir mensajes remotos ni secretos en logs."""

REMEDIES = {
    'invalid_response': 'Respuesta inválida. Revisa el recibo sin repetir el envío automáticamente.',
    'refused_response': 'El proveedor rechazó la solicitud; no cambiar de cuenta ni reenviar automáticamente.',
    'http_401': 'Clave rechazada. Revisa la clave del proveedor localmente.',
    'http_403': 'Acceso denegado por el proveedor. Revisa permisos y acceso al modelo.',
    'http_429': 'Límite de cuota o frecuencia. Revisa la cuenta; no repetir automáticamente.',
    'network_timeout': 'Red o tiempo agotado; consumo incierto. Revisa el intento antes de otro envío.',
    'cli_timeout': 'CLI agotó tiempo; consumo incierto. Revisa stdout/stderr y no relances a ciegas.',
    'cli_exit': 'CLI terminó con error. Revisa stderr.txt localmente y su acceso/permisos.',
    'incomplete_response': 'Respuesta truncada o bloqueada: no se acepta como completa. Revisa presupuesto y respuesta guardada.',
    'empty_reasoning': 'Respuesta vacía con razonamiento presente. Revisa consumo; puede requerir --max-tokens mayor, con nueva autorización.',
    'provider_error': 'El proveedor informó un fallo. Revisa su respuesta guardada localmente.',
    'response_size': 'La salida excedió el límite local. Revisa el intento; no se reenvía automáticamente.',
}


class ProviderFault(ValueError):
    def __init__(self, code):
        self.code = code
        self.remedy = REMEDIES.get(code, 'Revisa la fase y evidencia del intento. No se reintenta automáticamente.')
        super().__init__(self.remedy)


class ProviderAttemptError(ProviderFault):
    def __init__(self, provider, phase, code, folder):
        super().__init__(code)
        self.diagnostic_key = f'{provider}:{phase}:{code}'
        self.phase, self.folder = phase, str(folder)
        self.args = (f'Intento requiere revisión en {folder}. Fase: {phase}. Código: {code}. {self.remedy} Sin reintento automático.',)


class PriorAttemptError(ValueError):
    """Existe una reserva sin respuesta válida: relanzar podría cobrar dos veces."""
    def __init__(self, state, folder):
        self.state, self.folder = state, str(folder)
        super().__init__(f"Intento previo {state}. Revisar {folder}; no se duplica una llamada incierta.")
