"""a tiny uptime kuma look-alike: a real socket.io server that, like kuma, pushes `monitorList` and one
`heartbeatList` per monitor right after connect (auth disabled) or right after a good `login` (auth on)."""
import threading
import time
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

import socketio


class _Threading(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


class FakeKuma:
    def __init__(self, monitors: dict, beats: dict, login: tuple[str, str] | None = None, silent: bool = False):
        self.monitors, self.beats, self.login = monitors, beats, login
        self.silent = silent  # connects fine, never sends anything (what kuma with auth on does before login)
        self.sio = socketio.Server(async_mode="threading")
        self.sio.on("connect", self._connect)
        self.sio.on("login", self._login)
        self.server = make_server("127.0.0.1", 0, socketio.WSGIApp(self.sio), server_class=_Threading,
                                  handler_class=_Quiet)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def _push(self, sid):
        def run():
            time.sleep(0.1)
            self.sio.emit("monitorList", {str(i): m for i, m in self.monitors.items()}, to=sid)
            for i, beats in self.beats.items():
                self.sio.emit("heartbeatList", (i, beats, True), to=sid)
        self.sio.start_background_task(run)

    def _connect(self, sid, environ, auth=None):
        if self.login is None and not self.silent:
            self._push(sid)

    def _login(self, sid, data):
        if self.login and (data["username"], data["password"]) == self.login:
            self._push(sid)
            return {"ok": True, "token": "jwt"}
        return {"ok": False, "msg": "Incorrect username or password."}
