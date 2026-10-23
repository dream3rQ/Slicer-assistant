import threading
import webbrowser

from UM.Extension import Extension
from UM.Logger import Logger

from .agent import config
from .server import ChatServer
from .slicer_chat import SlicerChat
from .slicer_ui import SlicerUI

PREFERRED_PORT = 8765


class AIAssistant(Extension):
    def __init__(self):
        super().__init__()
        self._server = None
        self._lock = threading.Lock()
        config.init()
        self._ui = SlicerUI(self)
        self._chat = SlicerChat(self, self._ui)
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        self.addMenuItem("Open chat in browser", self.showChatInBrowser)
        self.addMenuItem("New conversation", self.newConversation)

    def ensureServer(self):
        with self._lock:
            if self._server is None:
                # A fixed port keeps the page's origin, so the browser remembers the
                # microphone permission; if it is taken, the OS picks a free one.
                try:
                    self._server = ChatServer(("127.0.0.1", PREFERRED_PORT))
                except OSError:
                    self._server = ChatServer(("127.0.0.1", 0))
                thread = threading.Thread(target=self._server.serve_forever, name="ste-ai-http", daemon=True)
                thread.start()
                Logger.log("i", "AI Assistant chat server on http://127.0.0.1:%d/", self._server.server_port)
            return self._server

    def server(self):
        return self._server

    def url(self):
        return "http://127.0.0.1:%d/" % self.ensureServer().server_port

    def showChat(self):
        # The chat lives in a slicer window; the browser page is the fallback
        # (and the same conversation, so both can be open at once).
        if not self._chat.show():
            self.showChatInBrowser()

    def showChatInBrowser(self):
        webbrowser.open(self.url())

    def newConversation(self):
        self.ensureServer()
        self._chat.newConversation()
        self.showChat()
