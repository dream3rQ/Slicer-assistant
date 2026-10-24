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

from . import config, llm, memory
from . import tools as tool_registry
from .mainthread import run_on_main
from .mcp import McpServer
from .prompt import build_system_prompt

MODEL_EXT = (".stl", ".obj", ".3mf")
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".webp")
DOC_EXT = (".docx", ".txt", ".md")
# fn(session, event) called on every event, from whatever thread emitted it (slicer_ui.py).
LISTENERS = []
MAX_TOOL_RESULT_CHARS = 12000      # hard cap, always
COMPACT_TOOL_RESULT_CHARS = 4000   # cap when compact_history is on


class ToolContext:
    def __init__(self, session):
        self._session = session
        self.uploads = session.uploads
        self.last_results_folder = None

    def emit(self, kind, data):
        self._session.emit(kind, data)

    def part_features(self, object_id):
        """{"class", "features"} that recommend_print_mode returned for the object, if any."""
        return self._session.part_features.get(object_id)


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
        self.proposal = None   # apply_plan waiting for the user's decision, see _confirm_plan
        self.part_features = {}  # object id -> {"class", "features"} from the geometry tools, for memory
        self.mcp = McpServer(context_factory=lambda: ToolContext(self))

    # ---- events
    def emit(self, kind, data=None):
        with self.lock:
            event = {"seq": len(self.events) + 1, "type": kind, "time": time.time(), "data": data}
            self.events.append(event)
        for listener in LISTENERS:
            try:
                listener(self, event)
            except Exception:
                Logger.logException("w", "AI Assistant event listener failed")
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
    def post_user_message(self, text, attachment_ids, hint=None):
        """hint: extra words for the LLM only, e.g. the 3D/5D chip from the slicer panel."""
        with self.lock:
            if self.proposal is not None and text:
                # Typing while the plan card is open = "Уточнить" with that text.
                self.emit("user", {"text": text, "attachments": []})
                self._answer(self.proposal, {"action": "revise", "text": text})
                return
            if self.busy:
                raise RuntimeError("Ассистент ещё отвечает на предыдущее сообщение")
            self.busy = True
            self.cancel_requested = False
        attachments = [self.uploads[a] for a in attachment_ids or [] if a in self.uploads]
        self.emit("user", {"text": text, "hint": hint, "attachments": [
            {"id": a["id"], "name": a["name"], "kind": a["kind"]} for a in attachments]})
        self.messages.append({"role": "user", "content": _user_content(text + ("\n" + hint if hint else ""), attachments)})
        thread = threading.Thread(target=self._run, name="ste-ai-agent", daemon=True)
        thread.start()

    def retry(self):
        """Run the agent again after an error, without a new user message: the
        history already ends with the user's request (or tool results). After
        the step limit it ends with the assistant, so ask it to go on."""
        with self.lock:
            if self.busy:
                raise RuntimeError("Ассистент ещё отвечает на предыдущее сообщение")
            if not self.messages:
                raise RuntimeError("Повторять нечего: диалог пуст")
            self.busy = True
            self.cancel_requested = False
        if self.messages[-1].get("role") == "assistant":
            self.emit("user", {"text": "Продолжи", "attachments": []})
            self.messages.append({"role": "user", "content": "Продолжи"})
        else:
            self.emit("status", {"text": "Повторяю запрос…"})
        threading.Thread(target=self._run, name="ste-ai-agent", daemon=True).start()

    def cancel(self):
        self.cancel_requested = True

    def answer_proposal(self, proposal_id, action, overrides=None, skip=None, text=None, remember=None):
        """remember: {"keys": [item ids], "scope": "part|material|mode|global"} from "Запомнить"."""
        if action not in PROPOSAL_ACTIONS:
            raise ValueError("action: одно из %s" % ", ".join(PROPOSAL_ACTIONS))
        with self.lock:
            proposal = self.proposal
            if proposal is None or proposal["id"] != proposal_id:
                raise RuntimeError("Предложение уже закрыто")
            answer = {"action": action, "overrides": dict(overrides or {}),
                      "skip": list(skip or []), "text": (text or "").strip()}
            if remember and remember.get("keys"):
                answer["remember"] = {"keys": list(remember["keys"]), "scope": remember.get("scope") or "part"}
            self._answer(proposal, answer)

    def check_remember(self, proposal_id, item_id, value, scope="part"):
        """Live check for the card before "Применить": would "Запомнить" keep this value?
        Returns {"ok", "reason"}."""
        with self.lock:
            proposal = self.proposal
            if proposal is None or proposal["id"] != proposal_id:
                raise RuntimeError("Предложение уже закрыто")
            context = proposal["memory_context"]
        mem = memory.get()
        key = memory.rule_key(item_id)
        if key == "mode":
            return {"ok": True, "reason": "Режим запомнится как пример для этой детали и похожих"}
        if mem.role() != "expert" and scope != "part":
            return {"ok": False, "reason": "Новичок может запомнить значение только для этой детали"}
        ok, reason = mem.validate(key, value, context)
        return {"ok": ok, "reason": reason or None}

    def _answer(self, proposal, answer):
        if proposal["answer"] is None:
            proposal["answer"] = answer
            proposal["event"].set()

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
            if name == "apply_plan" and config.get("confirm_plan"):
                result, is_error = self._confirm_plan(call.get("id"), args, context)
            elif name == "apply_plan":
                args, applied, _ = self._with_memory(args, context)
                result, is_error = self.mcp.call_tool(name, args, context)
                if applied and not is_error:
                    result["memory_applied"] = applied
            else:
                result, is_error = self.mcp.call_tool(name, args, context)
            if not is_error and isinstance(result, dict):
                self._remember_features(result)
                if name in MEMORY_HINT_TOOLS:
                    self._attach_memory_hint(result, args, context)
        self.emit("tool_result", {"id": call.get("id"), "name": name, "ok": not is_error,
                                  "result": result, "ms": int((time.time() - started) * 1000)})
        text = json.dumps(result, ensure_ascii=False)
        cap = COMPACT_TOOL_RESULT_CHARS if config.get("compact_history") else MAX_TOOL_RESULT_CHARS
        if len(text) > cap:
            text = text[:cap] + "…(обрезано)"
        self.messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": text})

    def _confirm_plan(self, call_id, args, context):
        """Show the plan as a card and wait for the user. Waiting inside the tool call
        keeps it one agent run: "Применить" costs no extra LLM step, and the LLM's
        history stays an unchanged prefix (prompt cache). MCP clients and the
        parameter panel call apply_plan directly, without a card."""
        args, applied, memory_context = self._with_memory(args, context)
        preview, is_error = self.mcp.call_tool("preview_plan", args, context)
        items = [] if is_error else preview.get("items") or []
        if all(i.get("unchanged") and not i.get("error") for i in items):
            result, is_error = self.mcp.call_tool("apply_plan", args, context)  # nothing to confirm; errors are apply_plan's to report
            if applied and not is_error:
                result["memory_applied"] = applied
            return result, is_error
        proposal = {"id": "p_" + uuid.uuid4().hex[:6], "event": threading.Event(), "answer": None,
                    "memory_context": memory_context}
        with self.lock:
            self.proposal = proposal
        by_item = {a["item_id"]: a for a in applied}
        for item in preview["items"]:
            if item["id"] in by_item:
                item["from_memory"] = by_item[item["id"]]
        self.emit("proposal", {"id": proposal["id"], "call_id": call_id, "extruder": preview["extruder"],
                               "items": preview["items"], "applied_rules": applied, "role": memory.get().role()})
        try:
            while not proposal["event"].wait(0.3):
                if self.cancel_requested:
                    break
        finally:
            with self.lock:
                self.proposal = None
        answer = proposal["answer"]
        if answer is None:
            self.emit("proposal_closed", {"id": proposal["id"], "action": "cancel"})
            return {"status": "cancelled", "note": "Пользователь остановил работу, план не применён"}, False
        self.emit("proposal_closed", dict(answer, id=proposal["id"]))
        if answer["action"] == "apply":
            plan, changed, skipped = _edited_plan(args, preview["items"], answer)
            result, is_error = self.mcp.call_tool("apply_plan", plan, context)
            result["user_decision"] = "применено пользователем"
            if changed:
                result["user_changed"] = changed
            if skipped:
                result["user_skipped"] = skipped
            if changed or skipped:
                result["note"] = "Пользователь поправил план: объясняй по фактическим значениям, его правки не отменяй"
            if applied:
                result["memory_applied"] = applied
            if not is_error:
                saved = self._record_edits(memory_context, plan, changed, preview["items"], answer, args)
                if saved:
                    result["user_remembered"] = saved
            return result, is_error
        if answer["action"] == "revise":
            return {"status": "not_applied", "user_feedback": answer["text"],
                    "next": "Ответь на вопрос или учти замечание, затем снова вызови apply_plan с исправленным планом"}, False
        return {"status": "rejected", "user_feedback": answer["text"],
                "next": "Ничего не применяй и не нарезай: коротко ответь и жди указаний пользователя"}, False

    # ---- memory of the user's edits (agent/memory.py)
    def _memory_context(self, args, context, object_id=None):
        try:
            ctx = run_on_main(tool_registry.memory_context, context, object_id, args.get("extruder"), args)
        except Exception:
            Logger.logException("w", "AI Assistant: no memory context")
            return {"part": {}, "material": args.get("material_id"), "mode": args.get("mode")}
        features = self.part_features.get(ctx["part"].get("object_id"))
        if features:
            ctx["part"].update(features)
        return ctx

    def _with_memory(self, args, context):
        """apply_plan arguments with the active rules laid over them, before the card.
        Returns (args, applied, memory context); memory never breaks the plan."""
        memory_context = self._memory_context(args, context)
        try:
            plan, applied = memory.get().apply_rules(args, memory_context)
        except Exception:
            Logger.logException("w", "AI Assistant: memory rules not applied")
            return args, [], memory_context
        if plan.get("material_id"):
            memory_context["material"] = plan["material_id"]
        if plan.get("mode"):
            memory_context["mode"] = plan["mode"]
        return plan, applied, memory_context

    def _record_edits(self, memory_context, plan, changed, items, answer, args):
        mem = memory.get()
        remember = answer.get("remember")
        context = dict(memory_context, material=plan.get("material_id") or memory_context.get("material"),
                       mode=plan.get("mode") or memory_context.get("mode"))
        if remember:
            values = {}
            for item in items:
                key = memory.rule_key(item["id"])
                values[key] = (answer.get("overrides") or {}).get(item["id"], item["proposed"])
            remember = dict(remember, values=values)
        try:
            saved = mem.record_edits(context, changed, remember, reasons=args.get("reasons"))
        except Exception as e:
            Logger.logException("w", "AI Assistant: edits not recorded")
            self.emit("memory_saved", {"error": str(e)})
            return None
        self.emit("memory_saved", dict(saved, note=mem.load_note))
        mem.load_note = None
        return {k: v for k, v in saved["rules"].items()} or None

    def _remember_features(self, result):
        """Geometry tools return {"object_id", "features", "class"?}: keep them for precedents."""
        features = result.get("features")
        object_id = result.get("object_id")
        if isinstance(features, dict) and object_id:
            entry = {"features": features}
            # recommend_print_mode names the class features.part_class
            part_class = result.get("class") or features.get("class") or features.get("part_class")
            if part_class:
                entry["class"] = part_class
            self.part_features[object_id] = entry

    def _attach_memory_hint(self, result, args, context):
        """The «Опыт этого ПК» block rides along with the geometry result: no extra step."""
        try:
            ctx = self._memory_context({"extruder": args.get("extruder")}, context, result.get("object_id"))
            block = memory.get().prompt_block(ctx)
        except Exception:
            Logger.logException("w", "AI Assistant: memory hint failed")
            return
        if block:
            result["memory"] = block

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


PROPOSAL_ACTIONS = ("apply", "revise", "reject")
# Tool results that get the «Опыт этого ПК» block attached (see _attach_memory_hint).
MEMORY_HINT_TOOLS = ("analyze_model", "recommend_print_mode")


def _stack_limits(key, context):
    return run_on_main(tool_registry.setting_limits, key)


memory.limits_provider = _stack_limits


def _same(a, b):
    return a == b or str(a).strip().lower() == str(b).strip().lower()


def _edited_plan(args, items, answer):
    """apply_plan arguments with the user's edits from the card: overrides
    {item id: value} and skip [item id]; item ids are mode, material_id,
    quality_type and settings.<key>."""
    overrides, skip = answer.get("overrides") or {}, set(answer.get("skip") or [])
    plan = {k: v for k, v in args.items() if k not in ("mode", "material_id", "quality_type", "settings", "reasons")}
    settings, changed, skipped = {}, {}, []
    for item in items:
        item_id = item["id"]
        if item_id in skip:
            skipped.append(item_id)
            continue
        value = overrides.get(item_id, item["proposed"])
        if item_id in overrides and not _same(value, item["proposed"]):
            changed[item_id] = {"proposed": item["proposed"], "chosen": value}
        if item["kind"] == "setting":
            settings[item_id[len("settings."):]] = value
        else:
            plan[item_id] = value
    if settings:
        plan["settings"] = settings
    return plan, changed, skipped


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
