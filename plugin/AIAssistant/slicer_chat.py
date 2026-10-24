# The Stereo Agent panel inside the slicer window (qml/AgentPanel.qml) and its
# toggle button in the top bar (qml/AgentButton.qml). It is a second face of the
# same Session the browser page talks to: messages, attachments, settings and
# the event log are shared, so either UI can be used at any time.
import base64
import html
import json
import os
import re
import threading
import time

from PyQt5.QtCore import QEvent, QObject, QPointF, QTimer, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from . import voice
from .agent import config, llm, session as session_module
from .memory_ui import saved_text
from .slicer_ui import mount

# The 3D / 5D chips under the input: an explicit choice the agent must follow (see prompt.py).
# The hint goes to the LLM only; the chat shows the chip.
SILENCE_PAUSE_SEC = 2.5   # dictation ends by itself after this long without speech

MODE_HINTS = {"3D": "Режим печати: 3D (classic), выбран пользователем.",
              "5D": "Режим печати: 5D, выбран пользователем; Spiral или полный 5D выбери по детали."}


def saved_line(result):
    """The "file saved" line under a tool call that wrote files (mock-up frame 6):
    {"saved": text, "saved_dir": folder to open on click}, or {} for other tools."""
    if not isinstance(result, dict):
        return {}
    folder = result.get("folder")
    if folder:
        names = [n for n, key in (("project.3mf", "project_3mf_bytes"), ("result.gcode", "gcode_bytes")) if result.get(key)]
        what = " и ".join(names) or "Результаты"
        return {"saved": "%s %s в %s" % (what, "сохранены" if len(names) != 1 else "сохранён", folder), "saved_dir": folder}
    path = result.get("path")
    if isinstance(path, str) and os.path.splitext(path)[1].lower() in (".3mf", ".gcode"):
        return {"saved": "%s сохранён в %s" % (os.path.basename(path), os.path.dirname(path)),
                "saved_dir": os.path.dirname(path)}
    return {}


class SlicerChat(QObject):
    _event = pyqtSignal(object, object)   # worker thread -> main thread
    _done = pyqtSignal(str, str)          # (kind, json) results of background calls
    eventAdded = pyqtSignal(str, arguments=["json"])  # one prepared event, JSON
    cleared = pyqtSignal()
    busyChanged = pyqtSignal()
    attachmentsChanged = pyqtSignal()
    settingsRequested = pyqtSignal()
    voiceLevelChanged = pyqtSignal()
    configTested = pyqtSignal(bool, str, arguments=["ok", "text"])
    voiceChanged = pyqtSignal()
    voiceText = pyqtSignal(str, arguments=["text"])

    def __init__(self, extension, card, memory, knowledge=None, parent=None):
        super().__init__(parent)
        self._extension = extension
        self._card = card
        self._memory = memory
        self._knowledge = knowledge
        self._panel = None
        self._button = None
        self._session = None
        self._busy = False
        self._attachments = []
        self._voice = ""   # "" | "recording" | "transcribing"
        self._silence = None
        self._listenTimer = QTimer(self)
        self._listenTimer.setInterval(100)
        self._level = 0.0
        self._listenTimer.timeout.connect(self._listen)
        self._event.connect(self._onEvent)
        self._done.connect(self._onDone)
        session_module.LISTENERS.append(self._event.emit)

    def mountUi(self):
        """Called once the slicer's main window exists: the top bar button, and
        the panel itself if it was open when the slicer was closed."""
        if self._button is None:
            self._button = mount("AgentButton", {"manager": self})
        if config.get("panel_open"):
            self.show()

    def show(self):
        server = self._extension.ensureServer()
        if self._panel is None:
            self._panel = mount("AgentPanel", {"manager": self})
            if self._panel is None:
                return False
            if self._button is not None:
                self._button.setProperty("panel", self._panel)
            window = self._panel.window()
            if window is not None:
                window.installEventFilter(self)
        if self._session is not server.session:
            self._replay(server.session)
        self._panel.setProperty("visible", True)
        config.set_many({"panel_open": True})
        return True

    def eventFilter(self, obj, event):
        """Files dragged onto the panel are the assistant's. The slicer's window has
        a drop area of its own that opens files on the build plate; filtering the
        window's drag events here catches them before any QML item sees them."""
        kind = event.type()
        panel = self._panel
        if kind in (QEvent.MouseMove, QEvent.MouseButtonRelease, QEvent.Leave, QEvent.FocusIn):
            # an ordinary mouse event means no drag is going on any more
            if panel is not None and panel.property("fileDrag"):
                panel.setProperty("fileDrag", False)
            return False
        if kind not in (QEvent.DragEnter, QEvent.DragMove, QEvent.DragLeave, QEvent.Drop):
            return False
        over = False
        if panel is not None and panel.property("visible") and kind != QEvent.DragLeave and event.mimeData().hasUrls():
            top_left = panel.mapToScene(QPointF(0, 0))
            pos = event.pos()
            over = (top_left.x() <= pos.x() < top_left.x() + panel.width()
                    and top_left.y() <= pos.y() < top_left.y() + panel.height())
        dragging = over and kind != QEvent.Drop   # the zone goes away as soon as the files are dropped
        if panel is not None and bool(panel.property("fileDrag")) != dragging:
            panel.setProperty("fileDrag", dragging)
        if not over:
            return False
        if kind == QEvent.Drop:
            self.addFiles([url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()])
        event.setDropAction(1)  # Qt.CopyAction
        event.accept()
        return True

    def isVisible(self):
        return self._panel is not None and bool(self._panel.property("visible"))

    def item(self):
        return self._panel

    @pyqtSlot()
    def hide(self):
        if self._panel is not None:
            self._panel.setProperty("visible", False)
        config.set_many({"panel_open": False})

    @pyqtSlot()
    def toggle(self):
        if self.isVisible():
            self.hide()
        else:
            self.show()

    # ---- state for QML
    @pyqtProperty(QObject, constant=True)
    def memory(self):
        """memory_ui.MemoryBridge: the "Память агента" window in the panel."""
        return self._memory

    @pyqtProperty(QObject, constant=True)
    def knowledge(self):
        """knowledge_ui.KnowledgeBridge: the "База знаний" window in the panel."""
        return self._knowledge

    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty(str, notify=attachmentsChanged)
    def attachmentsJson(self):
        return json.dumps(self._attachments, ensure_ascii=False)

    @pyqtSlot()
    def retry(self):
        server = self._extension.ensureServer()
        try:
            server.session.retry()
        except Exception as e:
            self._local("error", str(e))

    @pyqtSlot()
    def openSettings(self):
        """Extensions → AI Assistant → Settings: the settings window lives in the panel's QML."""
        if self.show():
            self.settingsRequested.emit()

    @pyqtProperty(int, constant=True)
    def panelWidth(self):
        return config.get("panel_width")

    @pyqtSlot(int)
    def savePanelWidth(self, width):
        config.set_many({"panel_width": int(width)})

    @pyqtProperty(bool, constant=True)
    def voiceAvailable(self):
        return voice.is_available()

    @pyqtProperty(str, notify=voiceChanged)
    def voiceState(self):
        return self._voice

    # ---- actions from QML
    @pyqtSlot()
    def toggleVoice(self):
        """First press starts recording in the plugin (voice.py), second press stops it
        and transcribes on a worker thread; the text arrives as voiceText."""
        if self._voice == "transcribing":
            return
        try:
            if self._voice == "":
                voice.start()
                self._setVoice("recording")
                self._silence = voice.SilenceDetector(SILENCE_PAUSE_SEC)
                self._listenTimer.start()
                return
            self._listenTimer.stop()
            audio = voice.stop()
        except Exception as e:
            self._setVoice("")
            self._local("error", str(e))
            return
        self._setVoice("transcribing")

        def work():
            try:
                self._done.emit("voice", json.dumps({"text": voice.transcribe(audio, "audio/wav").get("text", "")}))
            except Exception as e:
                self._done.emit("voice", json.dumps({"error": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    def _listen(self):
        """While recording: stop by itself after a pause in speech."""
        if self._voice != "recording":
            self._listenTimer.stop()
            return
        try:
            value = voice.level()
        except Exception:
            value = None
        self._setLevel(value or 0.0)
        if self._silence.feed(value, time.time()):
            self.toggleVoice()

    def _setLevel(self, value):
        if abs(value - self._level) > 0.005:
            self._level = value
            self.voiceLevelChanged.emit()

    @pyqtProperty(float, notify=voiceLevelChanged)
    def voiceLevel(self):
        """The microphone's input peak while recording, 0..1: the waves around the button."""
        return self._level

    @pyqtSlot()
    def cancelVoice(self):
        self._listenTimer.stop()
        if self._voice == "recording":
            try:
                voice.cancel()
            except Exception:
                pass
            self._setVoice("")

    def _setVoice(self, state):
        if state != "recording":
            self._setLevel(0.0)
        self._voice = state
        self.voiceChanged.emit()

    @pyqtSlot(str, str)
    def send(self, text, mode):
        session = self._extension.ensureServer().session
        text = (text or "").strip()

        if not text and not self._attachments:
            return
        try:
            session.post_user_message(text, [a["id"] for a in self._attachments], MODE_HINTS.get(mode))
        except RuntimeError as e:
            self._local("error", str(e))
            return
        self._attachments = []
        self.attachmentsChanged.emit()

    @pyqtSlot("QVariantList")
    def addFiles(self, urls):
        session = self._extension.ensureServer().session
        for url in urls:
            path = url.toLocalFile() if isinstance(url, QUrl) else (QUrl(url).toLocalFile() if "://" in url else url)
            if any(a["name"] == os.path.basename(path) for a in self._attachments):
                continue  # already attached
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                self._attachments.append(session.add_upload(os.path.basename(path), data))
            except Exception as e:
                self._local("error", "Не удалось приложить %s: %s" % (os.path.basename(path), e))
        self.attachmentsChanged.emit()

    @pyqtSlot()
    def pickFiles(self):
        """The "+" button. A Qt widgets dialog from Python, not QtQuick.Dialogs:
        with a FileDialog in our QML the slicer also opened the picked files
        itself (Convert Image for photos, "Invalid File" for docx)."""
        from PyQt5.QtWidgets import QFileDialog
        paths, _ = QFileDialog.getOpenFileNames(
            None, "Приложить файлы для ассистента", config.get("attach_dir") or os.path.expanduser("~"),
            "Модели, фото и ТЗ (*.stl *.obj *.3mf *.png *.jpg *.jpeg *.webp *.docx *.txt *.md);;Все файлы (*)")
        if paths:
            config.set_many({"attach_dir": os.path.dirname(paths[0])})
            self.addFiles(paths)

    @pyqtSlot(str)
    def removeAttachment(self, file_id):
        self._attachments = [a for a in self._attachments if a["id"] != file_id]
        self.attachmentsChanged.emit()

    @pyqtSlot()
    def cancel(self):
        self._extension.ensureServer().session.cancel()

    @pyqtSlot()
    def newConversation(self):
        self._attachments = []
        self.attachmentsChanged.emit()
        self._replay(self._extension.ensureServer().reset_session())

    @pyqtSlot()
    def showCard(self):
        self._card.reshow()

    @pyqtSlot()
    def openInBrowser(self):
        self._extension.showChatInBrowser()

    @pyqtSlot(result=str)
    def configJson(self):
        return json.dumps(dict(config.public(), voice_autosend=voice.get("voice_autosend")), ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def saveConfig(self, payload):
        payload = json.loads(payload)
        values = {k: v for k, v in payload.items() if k in config.DEFAULTS}
        if "llm_key" in values and not values["llm_key"]:
            values.pop("llm_key")  # empty field = keep the stored key
        if values.get("llm_url", config.get("llm_url")) != config.get("llm_url") or "llm_key" in values:
            voice.forget_model()  # another gateway may name the speech model differently
        config.set_many(values)
        voice.set_many({k: v for k, v in payload.items() if k in voice.DEFAULTS})
        return self.configJson()

    @pyqtSlot()
    def testConnection(self):
        def work():
            try:
                models = llm.list_models()
                self._done.emit("test", json.dumps({"ok": True, "text": "Подключение работает, моделей: %d" % len(models)}))
            except Exception as e:
                self._done.emit("test", json.dumps({"ok": False, "text": str(e)}))
        threading.Thread(target=work, daemon=True).start()

    # ---- events
    def _replay(self, session):
        self._session = session
        self.cleared.emit()
        for event in list(session.events):
            self._forward(event)
        self._setBusy(session.busy)

    def _onEvent(self, session, event):
        server = self._extension.server()
        if server is None or session is not server.session:
            return
        if session is not self._session:
            self._replay(session)  # reset from the browser
            return
        self._forward(event)
        self._setBusy(session.busy and event["type"] != "idle")

    def _onDone(self, kind, payload):
        if kind == "voice":
            data = json.loads(payload)
            self._setVoice("")
            if data.get("error"):
                self._local("error", data["error"])
            elif data.get("text"):
                self.voiceText.emit(data["text"])
            else:
                self._local("error", "Речь не распознана. Попробуйте ещё раз, ближе к микрофону.")
            return
        if kind == "test":
            data = json.loads(payload)
            self.configTested.emit(data["ok"], data["text"])

    def _forward(self, event):
        data = dict(event.get("data") or {})
        kind = event["type"]
        if kind in ("assistant", "note"):
            data["html"] = markdown_html(data.get("text"))
        elif kind == "tool_result":
            data["summary"] = summarize(data.get("name"), data.get("result"))
            if data.get("ok"):
                data.update(saved_line(data.get("result")))
            data["details"] = json.dumps(data.get("result"), ensure_ascii=False, indent=1)[:4000]
            data.pop("result", None)
        elif kind == "tool_call":
            data["args"] = json.dumps(data.get("arguments"), ensure_ascii=False)[:160]
        elif kind == "memory_saved":
            # Rules from "Запомнить": a line in the chat, and the memory window reloads.
            self._memory.changed.emit()
            text = saved_text(data)
            if not text:
                return
            kind, data = "note", {"text": text, "html": markdown_html(text)}
        elif kind == "usage":
            data["line"] = usage_line(data) + " · шагов %d" % len(data.pop("steps", None) or [])
        self.eventAdded.emit(json.dumps({"seq": event.get("seq"), "type": kind, "data": data}, ensure_ascii=False))

    def _local(self, kind, text):
        self.eventAdded.emit(json.dumps({"seq": None, "type": kind, "data": {"text": text}}, ensure_ascii=False))

    def _setBusy(self, busy):
        if busy != self._busy:
            self._busy = busy
            self.busyChanged.emit()


# ---- formatting (Qt rich text: a subset of HTML 4 with tables)

def _inline(text):
    text = html.escape(text, quote=False)
    text = re.sub(r"`([^`]+)`", r"<code>\1</code>", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    return re.sub(r"(^|[^*])\*([^*\n]+)\*", r"\1<i>\2</i>", text)


def markdown_html(src):
    lines, out, i = (src or "").replace("\r", "").split("\n"), [], 0
    cells = lambda row: [c.strip() for c in row.strip().strip("|").split("|")]
    while i < len(lines):
        line = lines[i]
        if line.strip().startswith("|") and i + 1 < len(lines) and re.match(r"^\s*\|?\s*:?-{2,}", lines[i + 1]):
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append(lines[i])
                i += 1
            head = "".join("<th>%s</th>" % _inline(c) for c in cells(rows[0]))
            body = "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % _inline(c) for c in cells(r)) for r in rows[2:])
            out.append('<table border="1" cellspacing="0" cellpadding="4"><tr>%s</tr>%s</table>' % (head, body))
            continue
        item = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", line)
        if item:
            ordered, items = item.group(1)[0].isdigit(), []
            while i < len(lines):
                m = re.match(r"^\s*([-*]|\d+[.)])\s+(.*)$", lines[i])
                if not m:
                    break
                items.append("<li>%s</li>" % _inline(m.group(2)))
                i += 1
            tag = "ol" if ordered else "ul"
            out.append("<%s>%s</%s>" % (tag, "".join(items), tag))
            continue
        heading = re.match(r"^#{1,4}\s+(.*)$", line)
        if heading:
            out.append("<h4>%s</h4>" % _inline(heading.group(1)))
        elif line.strip():
            out.append("<p>%s</p>" % _inline(line))
        i += 1
    return "".join(out)


def summarize(name, result):
    if not isinstance(result, dict):
        return ""
    if result.get("error"):
        return "ошибка: %s" % result["error"]
    if result.get("status") in ("not_applied", "rejected", "cancelled"):
        return {"not_applied": "отправлено на уточнение", "rejected": "отклонено", "cancelled": "остановлено"}[result["status"]]
    if result.get("print_time"):
        return "%s · %s г" % (result["print_time"], result.get("total_weight_g"))
    if result.get("folder"):
        return result["folder"]
    if result.get("user_decision"):
        return result["user_decision"] + (" (с правками)" if result.get("user_changed") or result.get("user_skipped") else "")
    if result.get("problems"):
        return "есть замечания: %d" % len(result["problems"])
    size = result.get("size_mm")
    if isinstance(size, dict):
        return "%s × %s × %s мм" % (size.get("x"), size.get("y"), size.get("z"))
    if isinstance(result.get("candidates"), list):
        return ", ".join(c.get("name", "") for c in result["candidates"][:3])
    return "ok"


def usage_line(d):
    def num(n):
        return "?" if n is None else "{:,}".format(n).replace(",", " ")
    if d.get("total_tokens") is None:
        return "Провайдер не сообщил токены · отправлено %s символов" % num(d.get("request_chars"))
    return "Токены за ответ: %s (вход %s%s, выход %s) · за сессию %s" % (
        num(d["total_tokens"]), num(d.get("prompt_tokens")),
        (", из кэша " + num(d["cached_tokens"])) if d.get("cached_tokens") else "",
        num(d.get("completion_tokens")), num(d.get("session_tokens")))
