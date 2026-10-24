# The assistant inside the slicer's main window: the Stereo Agent panel on the
# right (qml/AgentPanel.qml, see slicer_chat.py), the plan card over the 3D view
# (qml/PlanCard.qml), the toggle button in the top bar (qml/AgentButton.qml) and
# slicer notifications. The items are put into the main window's own item tree,
# next to the slicer's top bar, the same way the slicer adds plugin components
# to its interface. The browser chat stays as a fallback on the same Session, so
# answering the card in either place closes it in both.
import json
import os
import re

from PyQt5.QtCore import QObject, pyqtProperty, pyqtSignal, pyqtSlot

from UM.Application import Application
from UM.Logger import Logger
from UM.Message import Message

from .agent import config, session as session_module

QML_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "qml")


def find_item(root, class_prefix):
    """First item under root whose QML type name starts with class_prefix
    (a component from Foo.qml has the class name Foo_QMLTYPE_<n>)."""
    queue = [root]
    while queue:
        item = queue.pop(0)
        if item.metaObject().className().startswith(class_prefix):
            return item
        queue.extend(item.childItems())
    return None


def mount(name, context, properties=None, in_topbar=False):
    """Create qml/<name>.qml and put it into the main window: next to the top bar,
    or into it (in_topbar). Returns the item, or None when the window layout is
    not what we expect (then the caller falls back to the browser chat)."""
    try:
        window = Application.getInstance().getMainWindow()
        topbar = find_item(window.contentItem(), "Topbar") if window is not None else None
    except Exception:
        Logger.logException("w", "AI Assistant: looking for the slicer top bar failed")
        topbar = None
    if topbar is None:
        Logger.log("w", "AI Assistant: slicer top bar not found, %s not mounted", name)
        return None
    item = Application.getInstance().createQmlComponent(os.path.join(QML_DIR, name + ".qml"), context)
    if item is None:
        return None
    item.setParentItem(topbar if in_topbar else topbar.parentItem())
    item.setProperty("topbar", topbar)
    # The view orientation buttons: the right-most Row of the top bar; the toggle button sits left of them.
    # Its height is set to 30 in Topbar.qml, unlike the stage tabs Row.
    rows = [c for c in topbar.childItems() if c.metaObject().className() == "QQuickRow"]
    rows.sort(key=lambda r: (abs(r.height() - 30) > 1, -r.x()))
    if rows:
        item.setProperty("orientation", rows[0])
    family = theme_font_family()
    if family and item.property("fontFamily") is not None:
        item.setProperty("fontFamily", family)
    for key, value in (properties or {}).items():
        item.setProperty(key, value)
    return item


def theme_font_family():
    """The family of the slicer theme's default font, so our text matches the
    rest of the window; "" (the Qt default) when the theme cannot be read."""
    try:
        from UM.Qt.Bindings.Theme import Theme
        return Theme.getInstance().getFont("default").family()
    except Exception:
        return ""


class SlicerUI(QObject):
    # Session events arrive on worker threads; this signal hands them to the Qt
    # main thread (queued connection), where items may be touched.
    _event = pyqtSignal(object, object)
    proposalChanged = pyqtSignal()

    def __init__(self, extension, parent=None):
        super().__init__(parent)
        self._extension = extension
        self._card = None
        self._proposal = None
        self._session = None
        self._event.connect(self._onEvent)
        session_module.LISTENERS.append(self._event.emit)

    # ---- QML API (PlanCard.qml)
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

    def reshow(self):
        if self._proposal is not None:
            self._show()

    # ---- events
    def _onEvent(self, session, event):
        server = self._extension.server()
        if server is None or session is not server.session:
            return
        kind, data = event["type"], event.get("data") or {}
        if kind == "proposal":
            self._session, self._proposal = session, data
            self.proposalChanged.emit()
            if config.get("slicer_popups"):
                self._show()
        elif kind == "proposal_closed" and self._proposal and data.get("id") == self._proposal.get("id"):
            self._close()
        elif not config.get("slicer_popups") or self._extension.panelVisible():
            return  # the answer is already in front of the user
        elif kind == "assistant":
            self._notify("Ассистент закончил", _plain(data.get("text")))
        elif kind == "error":
            self._notify("Ошибка ассистента", data.get("text") or "")

    def _show(self):
        if self._card is None:
            self._card = mount("PlanCard", {"manager": self}, {"panel": self._extension.panelItem()})
            if self._card is None:
                self._extension.showChatInBrowser()  # the card is in the browser chat too
                return
        self._card.setProperty("panel", self._extension.panelItem())
        self._card.setProperty("visible", True)

    def _close(self):
        self._proposal = None
        self.proposalChanged.emit()
        if self._card is not None:
            self._card.setProperty("visible", False)

    def _notify(self, title, text):
        message = Message(text[:400] + ("…" if len(text) > 400 else ""), title="AI Assistant: " + title, lifetime=30)
        message.addAction("open_chat", "Открыть чат", "", "")
        message.actionTriggered.connect(lambda m, action: self.openChat())
        message.show()


def _plain(markdown):
    """First lines of the final answer without Markdown, for a notification."""
    text = re.sub(r"\|.*\|", "", markdown or "")
    text = re.sub(r"[*_`#>]+", "", text)
    lines = [l.strip(" -") for l in text.splitlines() if l.strip(" -")]
    return "\n".join(lines[:4])
