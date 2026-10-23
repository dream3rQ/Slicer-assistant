# Pop-ups inside the slicer window: the plan card as a native QML window and
# slicer notifications when the assistant finishes or fails. They mirror the
# browser chat, which stays the main UI (the case's skeleton is a web page):
# answering the card in either place closes it in both, because both talk to
# the same Session (see Session.answer_proposal).
import json
import os
import re

from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Logger import Logger
from UM.Message import Message

from .agent import config, session as session_module

QML_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml", "PlanCard.qml")


class SlicerUI(QObject):
    # Session events arrive on worker threads; this signal hands them to the Qt
    # main thread (queued connection), where windows may be touched.
    _event = pyqtSignal(object, object)
    proposalChanged = pyqtSignal()

    def __init__(self, extension, parent=None):
        super().__init__(parent)
        self._extension = extension
        self._window = None
        self._proposal = None
        self._session = None
        self._event.connect(self._onEvent)
        session_module.LISTENERS.append(self._event.emit)

    # ---- QML API
    @pyqtProperty(str, notify=proposalChanged)
    def proposalJson(self):
        return json.dumps(self._proposal or {"items": []}, ensure_ascii=False)

    @pyqtSlot(str, str, str, str)
    def answer(self, action, overrides_json, skip_json, text):
        if self._session is None or self._proposal is None:
            return
        try:
            self._session.answer_proposal(self._proposal["id"], action, json.loads(overrides_json or "{}"),
                                          json.loads(skip_json or "[]"), text)
        except (RuntimeError, ValueError) as e:
            Message(str(e), title="AI Assistant", lifetime=10).show()
            self._close()

    @pyqtSlot()
    def openChat(self):
        self._extension.showChat()

    # ---- events
    def _onEvent(self, session, event):
        server = self._extension.server()
        if server is None or session is not server.session or not config.get("slicer_popups"):
            return
        kind, data = event["type"], event.get("data") or {}
        if kind == "proposal":
            self._session, self._proposal = session, data
            self.proposalChanged.emit()
            self._show()
        elif kind == "proposal_closed" and self._proposal and data.get("id") == self._proposal.get("id"):
            self._close()
        elif kind == "assistant":
            self._notify("Ассистент закончил", _plain(data.get("text")), with_chat=True)
        elif kind == "error":
            self._notify("Ошибка ассистента", data.get("text") or "", with_chat=True)

    def _show(self):
        if self._window is None:
            self._window = Application.getInstance().createQmlComponent(QML_PATH, {"manager": self})
            if self._window is None:
                Logger.log("e", "AI Assistant: could not create %s", QML_PATH)
                return
        self._window.show()
        self._window.raise_()
        self._window.requestActivate()

    def _close(self):
        self._proposal = None
        if self._window is not None:
            self._window.hide()

    def _notify(self, title, text, with_chat=False):
        message = Message(text[:400] + ("…" if len(text) > 400 else ""), title="AI Assistant: " + title, lifetime=30)
        if with_chat:
            message.addAction("open_chat", "Открыть чат", "", "")
            message.actionTriggered.connect(lambda m, action: self.openChat())
        message.show()


def _plain(markdown):
    """First lines of the final answer without Markdown, for a notification."""
    text = re.sub(r"\|.*\|", "", markdown or "")
    text = re.sub(r"[*_`#>]+", "", text)
    lines = [l.strip(" -") for l in text.splitlines() if l.strip(" -")]
    return "\n".join(lines[:4])
