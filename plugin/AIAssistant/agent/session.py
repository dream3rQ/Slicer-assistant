# One chat session: message history, attachments, the agent loop and an event
# log the browser polls (GET /api/events?after=N).
import base64
import json
import os
import re
import tempfile
import threading
import time
import uuid
import zipfile

from UM.Logger import Logger

from . import config, llm
from .mcp import McpServer
from .prompt import build_system_prompt

MODEL_EXT = (".stl", ".obj", ".3mf")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
DOC_EXT = (".docx", ".txt", ".md")
MAX_TOOL_RESULT_CHARS = 12000      # hard cap, always
COMPACT_TOOL_RESULT_CHARS = 4000   # cap when compact_history is on


class ToolContext:
    def __init__(self, session):
        self._session = session
        self.uploads = session.uploads
        self.last_results_folder = None

    def emit(self, kind, data):
        self._session.emit(kind, data)


class Session:
    def __init__(self):
        self.id = uuid.uuid4().hex[:8]
        self.lock = threading.RLock()
        self.events = []
        self.uploads = {}
        self.upload_dir = tempfile.mkdtemp(prefix="ste_ai_")
        self.messages = [{"role": "system", "content": build_system_prompt()}]
        self.busy = False
        self.cancel_requested = False
        self.session_tokens = 0
        self.mcp = McpServer(context_factory=lambda: ToolContext(self))

    # ---- events
    def emit(self, kind, data=None):
        with self.lock:
            event = {"seq": len(self.events) + 1, "type": kind, "time": time.time(), "data": data}
            self.events.append(event)
        return event

    def events_after(self, seq):
        with self.lock:
            return [e for e in self.events if e["seq"] > seq]

    # ---- attachments
    def add_upload(self, name, data_b64):
        name = os.path.basename(name or "file")
        ext = os.path.splitext(name)[1].lower()
        raw = base64.b64decode(data_b64)
        file_id = "f_" + uuid.uuid4().hex[:6]
        # One folder per upload keeps the original file name, so the object in the
        # scene (and the results folder named after it) is "Шестерня", not "f_1a2b3c_Шестерня".
        folder = os.path.join(self.upload_dir, file_id)
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, re.sub(r'[<>:"/\\|?*]+', "_", name))
        with open(path, "wb") as f:
            f.write(raw)
        if ext in MODEL_EXT:
            kind = "model"
        elif ext in IMAGE_EXT:
            kind = "image"
        elif ext in DOC_EXT:
            kind = "document"
        else:
            kind = "other"
        item = {"id": file_id, "name": name, "kind": kind, "path": path, "bytes": len(raw)}
        if kind == "image":
            mime = "image/png" if ext == ".png" else ("image/webp" if ext == ".webp" else "image/jpeg")
            item["data_url"] = "data:%s;base64,%s" % (mime, data_b64)
        if kind == "document":
            item["text"] = _document_text(path, ext)
        self.uploads[file_id] = item
        return {k: v for k, v in item.items() if k not in ("data_url", "path", "text")}

    # ---- conversation
    def post_user_message(self, text, attachment_ids):
        with self.lock:
            if self.busy:
                raise RuntimeError("Ассистент ещё отвечает на предыдущее сообщение")
            self.busy = True
            self.cancel_requested = False
        attachments = [self.uploads[a] for a in attachment_ids or [] if a in self.uploads]
        self.emit("user", {"text": text, "attachments": [
            {"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in attachments]})
        self.messages.append({"role": "user", "content": _user_content(text, attachments)})
        thread = threading.Thread(target=self._run, name="ste-ai-agent", daemon=True)
        thread.start()

    def cancel(self):
        self.cancel_requested = True

    def _run(self):
        context = ToolContext(self)
        tools = self.mcp.openai_tools()
        max_steps = max(1, int(config.get("max_steps") or 30))
        steps = []
        try:
            for step in range(max_steps):
                if self.cancel_requested:
                    self.emit("status", {"text": "Остановлено пользователем"})
                    break
                so_far = [s.get("prompt_tokens") for s in steps] + [s.get("completion_tokens") for s in steps]
                self.emit("thinking", {"step": step + 1,
                                       "tokens": sum(so_far) if so_far and None not in so_far else None})
                reply, usage = llm.chat(self._request_messages(with_images=(step == 0)), tools)
                tool_calls = reply.get("tool_calls") or []
                usage["tools"] = [(c.get("function") or {}).get("name") for c in tool_calls]
                steps.append(usage)
                content = reply.get("content")
                assistant_msg = {"role": "assistant", "content": content or ""}
                if tool_calls:
                    assistant_msg["tool_calls"] = tool_calls
                self.messages.append(assistant_msg)
                if not tool_calls:
                    self.emit("assistant", {"text": content or ""})
                    break
                if content:
                    self.emit("note", {"text": content})
                for call in tool_calls:
                    self._run_tool_call(call, context)
            else:
                self.emit("error", {"text": "Достигнут лимит шагов (%s). Напишите «продолжи», чтобы продолжить." % max_steps})
        except llm.LLMError as e:
            self.emit("error", {"text": str(e)})
        except Exception as e:
            Logger.logException("e", "AI Assistant agent loop failed")
            self.emit("error", {"text": "Внутренняя ошибка ассистента: %s" % e})
        finally:
            if steps:
                self.emit("usage", self._usage_summary(steps))
            with self.lock:
                self.busy = False
            self.emit("idle", None)

    def _usage_summary(self, steps):
        def total(key):
            values = [s.get(key) for s in steps]
            return sum(values) if values and all(v is not None for v in values) else None
        prompt, completion = total("prompt_tokens"), total("completion_tokens")
        run_total = prompt + completion if prompt is not None and completion is not None else None
        if run_total is not None:
            self.session_tokens += run_total
        return {
            "steps": steps,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "cached_tokens": total("cached_tokens"),
            "total_tokens": run_total,
            "session_tokens": self.session_tokens,
            "request_chars": total("request_chars"),
            "model": config.get("llm_model"),
            "compact_history": bool(config.get("compact_history")),
        }

    def _run_tool_call(self, call, context):
        fn = call.get("function") or {}
        name = fn.get("name") or ""
        raw_args = fn.get("arguments") or "{}"
        try:
            args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            if not isinstance(args, dict):
                raise ValueError("arguments must be an object")
        except ValueError as e:
            args = None
            result, is_error = {"error": "Некорректные аргументы JSON: %s" % e}, True
        self.emit("tool_call", {"id": call.get("id"), "name": name, "arguments": args})
        started = time.time()
        if args is not None:
            result, is_error = self.mcp.call_tool(name, args, context)
        self.emit("tool_result", {"id": call.get("id"), "name": name, "ok": not is_error,
                                  "result": result, "ms": int((time.time() - started) * 1000)})
        text = json.dumps(result, ensure_ascii=False)
        cap = COMPACT_TOOL_RESULT_CHARS if config.get("compact_history") else MAX_TOOL_RESULT_CHARS
        if len(text) > cap:
            text = text[:cap] + "…(обрезано)"
        self.messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": text})

    def _request_messages(self, with_images):
        """History as sent to the LLM. The provider caches the longest unchanged
        prefix of the request, so history is never rewritten except once: images
        are sent on the first step after the user attached them and replaced by a
        note from then on (a single cache miss instead of resending ~1k+ tokens
        on every step). Tool results are capped when they are stored."""
        if not config.get("compact_history"):
            return list(self.messages)
        out = []
        for i, m in enumerate(self.messages):
            if m["role"] == "user" and isinstance(m["content"], list) and not (with_images and i == len(self.messages) - 1):
                text = " ".join(p.get("text", "") for p in m["content"] if p.get("type") == "text")
                m = dict(m, content=text + "\n[Изображения уже были показаны выше]")
            out.append(m)
        return out

    def transcript(self):
        return {"session": self.id, "events": self.events}


def _user_content(text, attachments):
    lines = [text or ""]
    for a in attachments:
        if a["kind"] == "model":
            lines.append("[Вложена 3D-модель: %s, file_id=%s]" % (a["name"], a["id"]))
        elif a["kind"] == "document":
            lines.append("[Вложен документ %s]\n%s" % (a["name"], a.get("text") or "(не удалось прочитать)"))
        elif a["kind"] == "image":
            lines.append("[Вложено изображение %s]" % a["name"])
        else:
            lines.append("[Вложен файл %s, file_id=%s]" % (a["name"], a["id"]))
    images = [a for a in attachments if a["kind"] == "image"]
    joined = "\n".join(l for l in lines if l)
    if not images:
        return joined
    parts = [{"type": "text", "text": joined}]
    for a in images:
        parts.append({"type": "image_url", "image_url": {"url": a["data_url"]}})
    return parts


def _document_text(path, ext):
    try:
        if ext == ".docx":
            with zipfile.ZipFile(path) as z:
                xml = z.read("word/document.xml").decode("utf-8", "replace")
            paragraphs = re.findall(r"<w:p[ >].*?</w:p>", xml, re.S)
            lines = ["".join(re.findall(r"<w:t[^>]*>([^<]*)</w:t>", p)) for p in paragraphs]
            text = "\n".join(l for l in lines if l.strip())
        else:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        for entity, char in (("&lt;", "<"), ("&gt;", ">"), ("&quot;", '"'), ("&apos;", "'"), ("&amp;", "&")):
            text = text.replace(entity, char)
        return text[:20000]
    except Exception as e:
        return "(ошибка чтения: %s)" % e
