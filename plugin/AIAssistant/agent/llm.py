# Minimal OpenAI-compatible Chat Completions client on the standard library.
# The slicer ships Python 3.5 without `openai` or `requests`, so we use urllib.
import json
import os
import ssl
import time
import urllib.error
import urllib.request

from . import config


class LLMError(Exception):
    pass


def _endpoint(path):
    base = (config.get("llm_url") or "").strip().rstrip("/")
    if not base:
        raise LLMError("Не задан URL LLM. Откройте настройки ассистента и укажите адрес.")
    for suffix in ("/chat/completions", "/models"):
        if base.endswith(suffix):
            base = base[: -len(suffix)]
    return base + path


BUNDLED_CA = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "certs", "cacert.pem")


def _ssl_context():
    ctx = ssl.create_default_context()  # system store (Windows certificate store on Windows)
    if not config.get("ssl_verify"):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    # The slicer's Python ships an old certifi/OpenSSL; add a fresh Mozilla
    # bundle on top of the system store so newer roots are trusted too.
    if os.path.isfile(BUNDLED_CA):
        try:
            ctx.load_verify_locations(cafile=BUNDLED_CA)
        except Exception:
            pass
    # Prefer our trusted roots over expired cross-signed ones sent by servers
    # (old OpenSSL 1.0.2 otherwise fails on e.g. Let's Encrypt chains).
    trusted_first = getattr(ssl, "VERIFY_X509_TRUSTED_FIRST", None)
    if trusted_first is not None:
        try:
            ctx.verify_flags |= trusted_first
        except Exception:
            pass
    return ctx


def _request(method, path, body=None, timeout=180):
    key = config.get("llm_key") or ""
    headers = {"Content-Type": "application/json", "Accept": "application/json"}
    if key:
        headers["Authorization"] = "Bearer " + key
    data = None
    if body is not None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(_endpoint(path), data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_ssl_context()) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8", "replace")[:800]
        except Exception:
            pass
        low = detail.lower()
        if "usage limit" in low or "quota" in low or "insufficient" in low or "balance" in low:
            raise LLMError("Исчерпан лимит использования ключа LLM (HTTP %s). Переключитесь на более дешёвую "
                           "модель в настройках или пополните лимит у провайдера. Ответ: %s" % (e.code, detail[:300]))
        if e.code in (401, 403):
            raise LLMError("LLM отклонила ключ (HTTP %s). Проверьте ключ в настройках. %s" % (e.code, detail))
        raise LLMError("LLM вернула ошибку HTTP %s: %s" % (e.code, detail))
    except ssl.SSLError as e:
        raise LLMError("Ошибка SSL при подключении к LLM: %s. Можно отключить проверку сертификата в настройках." % e)
    except urllib.error.URLError as e:
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(e.reason):
            hint = " Снимите галочку «Проверять SSL-сертификат» в настройках."
        raise LLMError("Не удалось подключиться к LLM: %s.%s" % (e.reason, hint))
    except OSError as e:
        # a read timeout (socket.timeout) or a dropped connection while reading the answer;
        # "подключиться" makes chat() retry it like a refused connection
        raise LLMError("Не удалось подключиться к LLM: нет ответа (%s)." % (e or e.__class__.__name__))
    except ValueError as e:
        raise LLMError("LLM вернула не JSON: %s" % e)


def list_models():
    data = _request("GET", "/models", timeout=30)
    return [m.get("id") for m in data.get("data", []) if m.get("id")]


def _usage(raw, body):
    """Token counts as reported by the provider (None when it reports nothing),
    plus the request size in characters, which is always known."""
    raw = raw if isinstance(raw, dict) else {}
    details = raw.get("prompt_tokens_details") or {}
    return {
        "prompt_tokens": raw.get("prompt_tokens"),
        "completion_tokens": raw.get("completion_tokens"),
        "cached_tokens": details.get("cached_tokens") if isinstance(details, dict) else None,
        "request_chars": len(json.dumps(body, ensure_ascii=False)),
    }


def chat(messages, tools=None):
    """Returns (assistant message, usage dict)."""
    body = {
        "model": config.get("llm_model") or "default",
        "messages": messages,
        "temperature": config.get("temperature"),
    }
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    last_error = None
    for attempt in range(3):
        try:
            data = _request("POST", "/chat/completions", body)
            choices = data.get("choices") or []
            if not choices:
                raise LLMError("LLM вернула пустой ответ: %s" % json.dumps(data, ensure_ascii=False)[:300])
            return choices[0].get("message") or {}, _usage(data.get("usage"), body)
        except LLMError as e:
            last_error = e
            text = str(e)
            retryable = (("HTTP 5" in text) or ("HTTP 429" in text) or ("подключиться" in text)) \
                and "Исчерпан лимит" not in text
            if not retryable or attempt == 2:
                raise
            time.sleep(2 * (attempt + 1))
    raise last_error
