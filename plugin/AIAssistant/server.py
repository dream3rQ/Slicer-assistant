# Local HTTP server: serves the chat page from web/ and the JSON API the page
# uses. Listens on 127.0.0.1 only. Every /api and /mcp request must carry the
# X-STE-AI header, which a foreign web page cannot add without a CORS
# preflight we never answer, and the Host header must be local (blocks DNS
# rebinding).
import base64
import json
import os
import ssl
import threading
import traceback
import urllib.error
import urllib.request
import uuid
from collections import OrderedDict
from http.server import HTTPServer, SimpleHTTPRequestHandler
from socketserver import ThreadingMixIn
from urllib.parse import urlparse, parse_qs

from UM.Logger import Logger

from .agent import config, llm
from .agent import tools as tool_registry
from .agent.mainthread import run_on_main
from .agent.session import Session

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
MAX_BODY = 200 * 1024 * 1024
REQUIRED_HEADER = "X-STE-AI"

# Voice input settings. Kept here (not in agent/config.py) because only the
# chat page uses them; stored in steslicer.cfg next to the agent's settings,
# since the page's own storage is lost: the port, and so the origin, changes
# on every slicer start.
VOICE_DEFAULTS = OrderedDict([
    ("voice_engine", "auto"),     # auto | server | browser
    ("voice_model", ""),          # empty = pick a *transcribe* / whisper model from /models
    ("voice_autosend", False),    # send the recognized text right away
])
VOICE_ENGINES = ("auto", "server", "browser")
MAX_AUDIO = 20 * 1024 * 1024
# Vocabulary hint for the speech model, so print terms come out spelled right.
TRANSCRIBE_PROMPT = ("Разговор о 3D-печати в STE Slicer. Режимы 3D, 5D, 5D Spiral. Материалы Stereotech: "
                     "Proto PLA, Enduse PETG, Enduse ABS, Enduse PC, Fiberpart PA12, Fiberpart PC G20, "
                     "Sealant TPU, Sealant TPV, Metalcast, ContiFiber. Сопло, экструдер, стол, поддержки, заполнение, высота слоя, "
                     "кайма, юбка, ретракт, обдув, STL, G-code, мм, °C.")


class ChatServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    # On Windows SO_REUSEADDR lets a second process bind a port that is already
    # taken, which would hide the fallback to a free port (see AIAssistant.py).
    allow_reuse_address = os.name != "nt"

    def __init__(self, address):
        HTTPServer.__init__(self, address, Handler)
        self.session = Session()
        self.session_lock = threading.Lock()
        self.transcribe_model = None
        _voice_init()

    def reset_session(self):
        with self.session_lock:
            self.session = Session()
        return self.session


class Handler(SimpleHTTPRequestHandler):
    server_version = "STEAIAssistant/0.1"

    # ---- plumbing
    def translate_path(self, path):
        path = urlparse(path).path
        rel = os.path.normpath(path.lstrip("/")) if path not in ("", "/") else "index.html"
        full = os.path.abspath(os.path.join(WEB_DIR, rel))
        if not full.startswith(os.path.abspath(WEB_DIR)):
            return os.path.join(WEB_DIR, "index.html")
        return full

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        SimpleHTTPRequestHandler.end_headers(self)

    def log_message(self, fmt, *args):
        Logger.log("d", "[AI Assistant HTTP] " + fmt, *args)

    def _host_ok(self):
        host = (self.headers.get("Host") or "").split(":")[0]
        return host in ("127.0.0.1", "localhost")

    def _send_json(self, data, status=200):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self):
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length > MAX_BODY:
            raise ValueError("Слишком большой запрос")
        raw = self.rfile.read(length) if length else b""
        return json.loads(raw.decode("utf-8")) if raw else {}

    def _guard(self):
        if not self._host_ok():
            self._send_json({"error": "forbidden host"}, 403)
            return False
        if self.headers.get(REQUIRED_HEADER) != "1":
            self._send_json({"error": "missing %s header" % REQUIRED_HEADER}, 403)
            return False
        return True

    # ---- routing
    def do_GET(self):
        url = urlparse(self.path)
        if not url.path.startswith("/api/"):
            if not self._host_ok():
                self.send_error(403)
                return
            return SimpleHTTPRequestHandler.do_GET(self)
        if not self._guard():
            return
        query = parse_qs(url.query)
        session = self.server.session
        try:
            if url.path == "/api/events":
                after = int((query.get("after") or ["0"])[0])
                self._send_json({"session": session.id, "busy": session.busy,
                                 "events": session.events_after(after)})
            elif url.path == "/api/config":
                self._send_json(_public_config())
            elif url.path == "/api/tools":
                self._send_json({"tools": session.mcp.list_tools()})
            elif url.path == "/api/transcript":
                self._send_json(session.transcript())
            elif url.path == "/api/debug/settings":
                self._send_json(run_on_main(_dump_settings, _timeout=120))
            else:
                self._send_json({"error": "not found"}, 404)
        except Exception as e:
            Logger.logException("e", "AI Assistant GET %s failed", url.path)
            self._send_json({"error": str(e)}, 500)

    def do_POST(self):
        if not self._guard():
            return
        url = urlparse(self.path)
        try:
            payload = self._read_json()
        except ValueError as e:
            self._send_json({"error": "bad json: %s" % e}, 400)
            return
        session = self.server.session
        try:
            if url.path == "/mcp":
                reply = session.mcp.handle(payload)
                if reply is None:
                    self.send_response(202)
                    self.end_headers()
                else:
                    self._send_json(reply)
            elif url.path == "/api/message":
                text = (payload.get("text") or "").strip()
                attachments = payload.get("attachments") or []
                if not text and not attachments:
                    self._send_json({"error": "Пустое сообщение"}, 400)
                    return
                try:
                    session.post_user_message(text, attachments)
                except RuntimeError as e:
                    self._send_json({"error": str(e)}, 409)
                    return
                self._send_json({"ok": True})
            elif url.path == "/api/upload":
                self._send_json(session.add_upload(payload.get("name"), payload.get("data") or ""))
            elif url.path == "/api/cancel":
                session.cancel()
                self._send_json({"ok": True})
            elif url.path == "/api/reset":
                new = self.server.reset_session()
                self._send_json({"ok": True, "session": new.id})
            elif url.path == "/api/config":
                values = {k: v for k, v in payload.items() if k in config.DEFAULTS}
                if "llm_key" in values and not values["llm_key"]:
                    values.pop("llm_key")  # empty field = keep the stored key
                if "llm_url" in values or "llm_key" in values:
                    self.server.transcribe_model = None  # another gateway may name it differently
                config.set_many(values)
                _voice_set({k: v for k, v in payload.items() if k in VOICE_DEFAULTS})
                self._send_json(_public_config())
            elif url.path == "/api/config/test":
                models = llm.list_models()
                self._send_json({"ok": True, "models": models})
            elif url.path == "/api/transcribe":
                self._send_json(_transcribe(self.server, payload))
            elif url.path == "/api/tool":
                # Direct tool call from the UI (parameter panel etc.)
                name = payload.get("name")
                if name not in tool_registry.TOOLS:
                    self._send_json({"error": "unknown tool"}, 404)
                    return
                from .agent.session import ToolContext
                result, is_error = session.mcp.call_tool(name, payload.get("arguments") or {}, ToolContext(session))
                self._send_json({"ok": not is_error, "result": result})
            elif url.path == "/api/debug/eval":
                if not config.get("dev_mode"):
                    self._send_json({"error": "Включите режим разработчика в настройках"}, 403)
                    return
                self._send_json(run_on_main(_eval, payload.get("code") or "", _timeout=120))
            else:
                self._send_json({"error": "not found"}, 404)
        except llm.LLMError as e:
            self._send_json({"error": str(e)}, 502)
        except Exception as e:
            Logger.logException("e", "AI Assistant POST %s failed", url.path)
            self._send_json({"error": str(e), "traceback": traceback.format_exc()[-2000:]}, 500)


def _voice_prefs():
    from UM.Application import Application
    return Application.getInstance().getPreferences()


def _voice_init():
    prefs = _voice_prefs()
    for key, default in VOICE_DEFAULTS.items():
        prefs.addPreference(config.PREFIX + key, default)


def _voice_get(key):
    value = _voice_prefs().getValue(config.PREFIX + key)
    default = VOICE_DEFAULTS[key]
    if value is None:
        return default
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    return str(value)


def _voice_set(values):
    if not values:
        return
    prefs = _voice_prefs()
    for key, value in values.items():
        if key == "voice_engine" and value not in VOICE_ENGINES:
            continue
        if key == "voice_model":
            value = " ".join(str(value or "").split())  # it goes into a multipart field
        prefs.setValue(config.PREFIX + key, value)
    try:
        from UM.Application import Application
        Application.getInstance().savePreferences()
    except Exception:
        pass


def _public_config():
    data = config.public()
    for key in VOICE_DEFAULTS:
        data[key] = _voice_get(key)
    return data


def _pick_transcribe_model(server):
    """The gateway's speech-to-text model: the one set in the settings, else the
    best *transcribe* / whisper model it lists, else the OpenAI default name."""
    chosen = _voice_get("voice_model").strip()
    if chosen:
        return chosen
    if server.transcribe_model:
        return server.transcribe_model
    try:
        models = llm.list_models()
    except llm.LLMError:
        models = []

    def rank(name):
        low = name.lower()
        if "transcribe" in low:
            return 0 if "mini" in low else 1
        return 2 if "whisper" in low else 3

    found = sorted((m for m in models if rank(m) < 3), key=rank)
    server.transcribe_model = found[0] if found else "gpt-4o-mini-transcribe"
    return server.transcribe_model


def _transcribe(server, payload):
    """POST <llm_url>/audio/transcriptions (OpenAI-compatible) with the recorded
    audio; the key stays on this side, the page only gets the text."""
    try:
        audio = base64.b64decode(payload.get("data") or "")
    except (TypeError, ValueError):
        raise llm.LLMError("Запись повреждена, попробуйте ещё раз")
    if not audio:
        raise llm.LLMError("Пустая запись: микрофон ничего не передал")
    if len(audio) > MAX_AUDIO:
        raise llm.LLMError("Слишком длинная запись, разбейте на части")
    mime = (payload.get("mime") or "audio/webm").split(";")[0].strip()
    ext = {"audio/ogg": "ogg", "audio/mp4": "m4a", "audio/mpeg": "mp3", "audio/wav": "wav"}.get(mime, "webm")
    model = _pick_transcribe_model(server)
    boundary = "steai" + uuid.uuid4().hex
    parts = []
    for name, value in (("model", model), ("language", "ru"), ("prompt", TRANSCRIBE_PROMPT),
                        ("response_format", "json")):
        parts.append(('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n'
                      % (boundary, name, value)).encode("utf-8"))
    parts.append(('--%s\r\nContent-Disposition: form-data; name="file"; filename="speech.%s"\r\n'
                  'Content-Type: %s\r\n\r\n' % (boundary, ext, mime)).encode("utf-8"))
    parts.append(audio)
    parts.append(("\r\n--%s--\r\n" % boundary).encode("utf-8"))
    headers = {"Content-Type": "multipart/form-data; boundary=" + boundary, "Accept": "application/json"}
    key = config.get("llm_key") or ""
    if key:
        headers["Authorization"] = "Bearer " + key
    req = urllib.request.Request(llm._endpoint("/audio/transcriptions"), data=b"".join(parts),
                                 headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60, context=llm._ssl_context()) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        server.transcribe_model = None  # re-pick next time
        if e.code in (401, 403):
            raise llm.LLMError("Шлюз отклонил ключ при распознавании речи (HTTP %s). %s" % (e.code, detail))
        raise llm.LLMError("Шлюз не распознал речь моделью %s (HTTP %s): %s" % (model, e.code, detail))
    except ssl.SSLError as e:
        raise llm.LLMError("Ошибка SSL при распознавании речи: %s" % e)
    except urllib.error.URLError as e:
        raise llm.LLMError("Не удалось подключиться к шлюзу для распознавания речи: %s" % e.reason)
    try:
        text = json.loads(raw).get("text", "")
    except (ValueError, AttributeError):
        text = raw  # response_format=text on some gateways
    return {"text": (text or "").strip(), "model": model}


def _dump_settings():
    from UM.Application import Application
    gs = Application.getInstance().getGlobalContainerStack()
    out = {"printer": gs.getName(), "definition": gs.definition.getId(), "global": {}, "extruders": {}}
    props = ("label", "type", "unit", "value", "options", "enabled", "settable_per_extruder")

    def dump(stack):
        data = {}
        for key in sorted(stack.getAllKeys()):
            if stack.getProperty(key, "type") == "category":
                continue
            data[key] = {p: tool_registry._jsonable(stack.getProperty(key, p)) for p in props}
        return data

    out["global"] = dump(gs)
    for pos, ext in gs.extruders.items():
        out["extruders"][pos] = dump(ext)
    return out


def _eval(code):
    # Developer console: runs Python inside the slicer on the main thread.
    # Only reachable when dev_mode is on, from 127.0.0.1, with the custom header.
    import io
    import contextlib
    from UM.Application import Application
    buf = io.StringIO()
    scope = {"app": Application.getInstance(), "tools": tool_registry}
    try:
        with contextlib.redirect_stdout(buf):
            try:
                result = eval(compile(code, "<console>", "eval"), scope)
            except SyntaxError:
                exec(compile(code, "<console>", "exec"), scope)
                result = scope.get("result")
        return {"ok": True, "stdout": buf.getvalue(), "result": tool_registry._jsonable(result)}
    except Exception:
        return {"ok": False, "stdout": buf.getvalue(), "error": traceback.format_exc()[-3000:]}
