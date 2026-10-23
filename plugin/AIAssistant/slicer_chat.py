# The chat as a window inside the slicer (qml/ChatWindow.qml). It is a second
# face of the same Session the browser page talks to: messages, attachments,
# settings and the event log are shared, so either UI can be used at any time.
import base64
import html
import json
import os
import re
import threading

from PyQt5.QtCore import QObject, QUrl, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Logger import Logger

from .agent import config, llm, session as session_module

QML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml", "ChatWindow.qml")


class SlicerChat(QObject):
    _event = pyqtSignal(object, object)   # worker thread -> main thread
    _done = pyqtSignal(str, str)          # (kind, json) results of background calls
    eventAdded = pyqtSignal(str, arguments=["json"])  # one prepared event, JSON
    cleared = pyqtSignal()
    busyChanged = pyqtSignal()
    attachmentsChanged = pyqtSignal()
    configTested = pyqtSignal(bool, str, arguments=["ok", "text"])

    def __init__(self, extension, card, parent=None):
        super().__init__(parent)
        self._extension = extension
        self._card = card
        self._window = None
        self._session = None
        self._busy = False
        self._attachments = []
        self._event.connect(self._onEvent)
        self._done.connect(self._onDone)
        session_module.LISTENERS.append(self._event.emit)

    def show(self):
        server = self._extension.ensureServer()
        if self._window is None:
            self._window = Application.getInstance().createQmlComponent(QML_PATH, {"manager": self})
            if self._window is None:
                Logger.log("e", "AI Assistant: could not create %s", QML_PATH)
                return False
        if self._session is not server.session:
            self._replay(server.session)
        self._window.show()
        self._window.raise_()
        self._window.requestActivate()
        return True

    # ---- state for QML
    @pyqtProperty(bool, notify=busyChanged)
    def busy(self):
        return self._busy

    @pyqtProperty(str, notify=attachmentsChanged)
    def attachmentsJson(self):
        return json.dumps(self._attachments, ensure_ascii=False)

    # ---- actions from QML
    @pyqtSlot(str)
    def send(self, text):
        session = self._extension.ensureServer().session
        text = (text or "").strip()
        if not text and not self._attachments:
            return
        try:
            session.post_user_message(text, [a["id"] for a in self._attachments])
        except RuntimeError as e:
            self._local("error", str(e))
            return
        self._attachments = []
        self.attachmentsChanged.emit()

    @pyqtSlot("QVariantList")
    def addFiles(self, urls):
        session = self._extension.ensureServer().session
        for url in urls:
            path = QUrl(url).toLocalFile() if "://" in str(url) else str(url)
            try:
                with open(path, "rb") as f:
                    data = base64.b64encode(f.read()).decode("ascii")
                self._attachments.append(session.add_upload(os.path.basename(path), data))
            except Exception as e:
                self._local("error", "Не удалось приложить %s: %s" % (os.path.basename(path), e))
        self.attachmentsChanged.emit()

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
        return json.dumps(config.public(), ensure_ascii=False)

    @pyqtSlot(str, result=str)
    def saveConfig(self, payload):
        values = {k: v for k, v in json.loads(payload).items() if k in config.DEFAULTS}
        if "llm_key" in values and not values["llm_key"]:
            values.pop("llm_key")  # empty field = keep the stored key
        config.set_many(values)
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
        if kind == "test":
            data = json.loads(payload)
            self.configTested.emit(data["ok"], data["text"])

    def _forward(self, event):
        data = dict(event.get("data") or {})
        kind = event["type"]
        if kind in ("assistant",):
            data["html"] = markdown_html(data.get("text"))
        elif kind == "tool_result":
            data["summary"] = summarize(data.get("name"), data.get("result"))
            data["details"] = json.dumps(data.get("result"), ensure_ascii=False, indent=1)[:4000]
            data.pop("result", None)
        elif kind == "tool_call":
            data["args"] = json.dumps(data.get("arguments"), ensure_ascii=False)[:160]
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
