import threading
import webbrowser

from UM.Extension import Extension
from UM.Logger import Logger

from .agent import config
from .server import ChatServer


class AIAssistant(Extension):
    def __init__(self):
        super().__init__()
        self._server = None
        self._lock = threading.Lock()
        config.init()
        self.setMenuName("AI Assistant")
        self.addMenuItem("Open chat", self.showChat)
        self.addMenuItem("New conversation", self.newConversation)

    def _ensure_server(self):
        with self._lock:
            if self._server is None:
                self._server = ChatServer(("127.0.0.1", 0))  # the OS picks a free port
                thread = threading.Thread(target=self._server.serve_forever, name="ste-ai-http", daemon=True)
                thread.start()
                Logger.log("i", "AI Assistant chat server on http://127.0.0.1:%d/", self._server.server_port)
            return self._server

    def url(self):
        return "http://127.0.0.1:%d/" % self._ensure_server().server_port

    def showChat(self):
        webbrowser.open(self.url())

    def newConversation(self):
        self._ensure_server().reset_session()
        webbrowser.open(self.url())
