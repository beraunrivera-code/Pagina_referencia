"""Contratos HTTP explícitos para UNA página PNG; sin SDK, login, retry ni fallback.

El llamador obtiene la clave del almacén seguro tras consentimiento y proporciona
el transporte acotado. Este módulo no lee archivos, entorno ni credenciales.
Contrato documentado != modelo/cuenta autenticados o calidad probada.

Fuentes oficiales consultadas el 2026-09-21:
https://ai.google.dev/api/generate-content
https://api-docs.deepseek.com/api/create-chat-completion/
https://developers.openai.com/api/docs/guides/images-vision
https://developers.openai.com/api/docs/guides/structured-outputs
https://platform.claude.com/docs/en/api/http/messages
https://platform.claude.com/docs/en/build-with-claude/vision
https://www.alibabacloud.com/help/en/model-studio/base-url
https://www.alibabacloud.com/help/en/model-studio/vision
https://www.alibabacloud.com/help/en/model-studio/qwen-structured-output
"""

import base64
import re
from urllib.error import HTTPError, URLError

from .documents import load_json
from .provider_errors import ProviderFault

API_PROVIDERS = ("gemini-api", "deepseek-api", "openai-api", "anthropic-api", "qwen-api")
ENDPOINTS = {
    "gemini-api": "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
    "deepseek-api": "https://api.deepseek.com/chat/completions",
    "openai-api": "https://api.openai.com/v1/responses",
    "anthropic-api": "https://api.anthropic.com/v1/messages",
    # Dominio compartido Singapore todavía disponible, según base-url. Migración
    # a WorkspaceId y selección de otras regiones requieren configuración explícita.
    "qwen-api": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
}
QWEN_VISION_MODELS = ("qwen-vl-max", "qwen-vl-plus", "qwen3-vl-plus", "qwen3-vl-flash")
MAX_IMAGE_BYTES = 8_000_000
MAX_TEXT_BYTES = 1_000_000


def validate_api_model(provider, model):
    """Validación local: sintaxis y allowlist VL; nunca consulta /models.

    Otros proveedores conservan selección explícita: el usuario debe comprobar
    visión y salida JSON para el modelo concreto. Esto no certifica capacidades.
    """
    if provider not in API_PROVIDERS:
        raise ValueError("Proveedor API no admitido")
    if not isinstance(model, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,119}", model):
        raise ValueError("Identificador de modelo inválido")
    if provider == "qwen-api" and model not in QWEN_VISION_MODELS:
        raise ValueError("Qwen: selecciona un modelo VL admitido: " + ", ".join(QWEN_VISION_MODELS))


def _request_contents(contents):
    try:
        prompt = contents["solicitud.md"]
        schema = contents["respuesta.schema.json"]
        png = contents["pagina.png"]
        if (not all(isinstance(value, bytes) for value in (prompt, schema, png))
                or len(prompt) + len(schema) > MAX_TEXT_BYTES
                or len(png) > MAX_IMAGE_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n")):
            raise ValueError
        if not isinstance(load_json(schema), dict):
            raise ValueError
        instruction = (prompt.decode("utf-8") + "\nDevuelve solo el objeto JSON del encargo.\n"
                       "Esquema JSON:\n" + schema.decode("utf-8"))
    except (KeyError, TypeError, ValueError, RecursionError):
        raise ValueError("Paquete API inválido: solicitud UTF-8, esquema JSON y PNG de hasta 8 MB") from None
    return instruction, base64.b64encode(png).decode("ascii")


def api_request(provider, model, contents, max_tokens, timeout, *, key, http):
    """Hace una única llamada usando el transporte inyectado; devuelve payload crudo.

    max_tokens limita la salida, no es un presupuesto monetario ni de entrada.
    No envía documentos nativos, URLs de imagen, herramientas o sesiones previas.
    """
    validate_api_model(provider, model)
    if (not isinstance(key, str) or not key.strip() or len(key) > 2000
            or any(ord(char) < 33 or ord(char) > 126 for char in key.strip())):
        raise ValueError("Clave API ausente o inválida")
    if (type(max_tokens) is not int or not 256 <= max_tokens <= 16384
            or type(timeout) not in (int, float) or not 10 <= timeout <= 600):
        raise ValueError("Salida API: 256–16384 tokens; tiempo: 10–600 segundos")
    instruction, image = _request_contents(contents)
    # Claude impone límite a la representación base64, no solo al PNG original.
    if provider == "anthropic-api" and len(image) > 10_000_000:
        raise ValueError("La imagen supera el límite de 10 MB codificada para Anthropic")
    headers = {"Authorization": "Bearer " + key.strip()}
    if provider == "gemini-api":
        headers = {"x-goog-api-key": key.strip()}
        body = {
            "contents": [{"role": "user", "parts": [{"text": instruction},
                {"inlineData": {"mimeType": "image/png", "data": image}}]}],
            "generationConfig": {"responseMimeType": "application/json", "maxOutputTokens": max_tokens},
        }
    elif provider == "openai-api":
        body = {
            "model": model, "input": [{"role": "user", "content": [
                {"type": "input_text", "text": instruction},
                {"type": "input_image", "image_url": "data:image/png;base64," + image}]}],
            "max_output_tokens": max_tokens, "text": {"format": {"type": "json_object"}},
            "store": False, "stream": False,
        }
    elif provider == "anthropic-api":
        headers = {"x-api-key": key.strip(), "anthropic-version": "2023-06-01"}
        body = {
            "model": model, "max_tokens": max_tokens, "stream": False,
            "messages": [{"role": "user", "content": [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": image}},
                {"type": "text", "text": instruction}]}],
        }
        # JSON solicitado en texto y validado localmente. No afirmar que un modelo
        # arbitrario admite constrained decoding ni añadir herramientas para simularlo.
    else:
        body = {
            "model": model, "messages": [{"role": "user", "content": [
                {"type": "text", "text": instruction},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64," + image}}]}],
            "max_tokens": max_tokens, "response_format": {"type": "json_object"}, "stream": False,
        }
        if provider == "qwen-api" and model.startswith("qwen3-vl-"):
            body["enable_thinking"] = False  # Contrato JSON documentado en modo no-thinking.
    try:
        return http(ENDPOINTS[provider].format(model=model), headers, body, timeout)
    except ProviderFault:
        raise
    except HTTPError as exc:
        # Nunca propagar reason/body remotos, pues pueden contener datos o claves.
        raise ProviderFault(f"http_{exc.code}" if type(exc.code) is int else "provider_error") from None
    except (URLError, OSError, TimeoutError):
        raise ProviderFault("network_timeout") from None
    except (ValueError, TypeError, RecursionError):
        raise ProviderFault("invalid_response") from None


def usage_for_api(provider, payload):
    """Preserva consumo antes de validar respuesta; nunca interpreta ausencia como 0.

    Anthropic reporta cuatro componentes disjuntos, no un total. Solo se deriva
    total_tokens cuando los cuatro contadores están presentes, enteros y válidos.
    Los detalles de TTL de cache_creation NO se suman por segunda vez.
    """
    if provider not in API_PROVIDERS:
        raise ValueError("Proveedor API no admitido")
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usageMetadata" if provider == "gemini-api" else "usage")
    if not isinstance(usage, dict):
        return None
    result = dict(usage)
    if provider == "anthropic-api":
        counters = [usage.get(name) for name in ("input_tokens", "output_tokens",
                    "cache_creation_input_tokens", "cache_read_input_tokens")]
        # No confiar en un total_tokens ajeno al contrato Anthropic.
        result.pop("total_tokens", None)
        if all(type(value) is int and value >= 0 for value in counters):
            result["total_tokens"] = sum(counters)
    return result


def _json_object(text):
    if not isinstance(text, str) or not text.strip():
        raise ProviderFault("invalid_response")
    value = text.strip()
    match = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", value, re.S)
    try:
        answer = load_json(match[1] if match else value)
    except (ValueError, TypeError, RecursionError):
        raise ProviderFault("invalid_response") from None
    if not isinstance(answer, dict):
        raise ProviderFault("invalid_response")
    return answer


def _list(value):
    if not isinstance(value, list) or not all(isinstance(item, dict) for item in value):
        raise ProviderFault("invalid_response")
    return value


def _text_parts(parts, text_type, ignored_types=()):
    text = []
    for part in _list(parts):
        kind = part.get("type")
        if kind == "refusal":
            raise ProviderFault("refused_response")
        if kind == text_type and isinstance(part.get("text"), str):
            text.append(part["text"])
        elif kind not in ignored_types:
            raise ProviderFault("invalid_response")
    return "".join(text)


def decode_api(provider, payload):
    """Decodifica un JSON completo. El contrato documental se valida al importar.

    Rechaza truncamiento, negativa y tool-calls. No repara JSON con otra llamada.
    """
    if provider not in API_PROVIDERS:
        raise ValueError("Proveedor API no admitido")
    if not isinstance(payload, dict) or payload.get("error"):
        raise ProviderFault("provider_error")
    usage = usage_for_api(provider, payload)
    if provider == "gemini-api":
        candidates = _list(payload.get("candidates", []))
        if len(candidates) != 1 or candidates[0].get("finishReason") != "STOP":
            raise ProviderFault("incomplete_response")
        content = candidates[0].get("content")
        if not isinstance(content, dict):
            raise ProviderFault("invalid_response")
        text = []
        for part in _list(content.get("parts")):
            if part.get("thought") is True:
                continue
            if not isinstance(part.get("text"), str):
                raise ProviderFault("invalid_response")
            text.append(part["text"])
        value = "".join(text)
    elif provider in ("deepseek-api", "qwen-api"):
        choices = _list(payload.get("choices", []))
        if len(choices) != 1 or choices[0].get("finish_reason") != "stop":
            raise ProviderFault("incomplete_response")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise ProviderFault("invalid_response")
        if message.get("refusal"):
            raise ProviderFault("refused_response")
        if message.get("tool_calls") or message.get("function_call"):
            raise ProviderFault("invalid_response")
        value = message.get("content")
        if value in (None, "") or isinstance(value, str) and not value.strip():
            if message.get("reasoning_content"):
                raise ProviderFault("empty_reasoning")
    elif provider == "openai-api":
        if payload.get("status") != "completed" or payload.get("incomplete_details"):
            raise ProviderFault("incomplete_response")
        text = []
        for item in _list(payload.get("output")):
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message":
                raise ProviderFault("invalid_response")
            if item.get("status") != "completed":
                raise ProviderFault("incomplete_response")
            if item.get("role") != "assistant":
                raise ProviderFault("invalid_response")
            text.append(_text_parts(item.get("content"), "output_text"))
        value = "".join(text)
    else:
        if payload.get("stop_reason") == "refusal":
            raise ProviderFault("refused_response")
        if payload.get("stop_reason") != "end_turn":
            raise ProviderFault("incomplete_response")
        value = _text_parts(payload.get("content"), "text", ("thinking", "redacted_thinking"))
    return _json_object(value), usage
