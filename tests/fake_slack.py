"""a tiny fake slack for tests: the web api over http and the web client's websocket (rtm).

no network beyond localhost. `FakeSlack` records every web api call and every websocket
frame noodle sends, can push frames to it, drop the connection, and be told to answer an api
method with an error (`api_overrides`) or refuse the websocket handshake (`ws_status`).
"""
import json
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from websockets.sync.server import serve


def wait_for(cond, timeout=3.0, step=0.01):
    """poll until cond() is truthy; returns its value (falsy on timeout)."""
    end = time.time() + timeout
    while time.time() < end:
        value = cond()
        if value:
            return value
        time.sleep(step)
    return cond()


class FakeSlack:
    def __init__(self, token="xoxc-fake", cookie="fake-cookie"):
        self.token = token
        self.cookie = cookie
        self.web_calls = []          # (method, params dict, headers dict)
        self.api_overrides = {}      # method -> response dict, or a list consumed one per call
        self.api_status = {}         # method -> http status to answer with
        self.api_responses = {}      # method -> response for calls that aren't overridden
        self.ws_status = None        # int: refuse the handshake with this status
        self.ws_paths = []           # request path (+query) of every websocket connection
        self.ws_headers = []         # headers of every websocket connection
        self.frames = []             # json frames noodle sent
        self._conn = None
        self._lock = threading.Lock()
        self._http = None
        self._ws = None

    # --- lifecycle ---
    def start(self):
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(length).decode() if length else ""
                if "json" in (self.headers.get("Content-Type") or ""):
                    params = json.loads(raw or "{}")
                else:
                    params = {k: v[0] for k, v in parse_qs(raw).items()}
                method = urlparse(self.path).path.strip("/").removeprefix("api/")
                status, body = fake._answer(method, params, dict(self.headers))
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            do_GET = do_POST

        self._http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=lambda: self._http.serve_forever(poll_interval=0.02), daemon=True).start()
        self._ws = serve(self._ws_handler, "127.0.0.1", 0, process_request=self._process_request,
                          close_timeout=0.2)
        threading.Thread(target=self._ws.serve_forever, daemon=True).start()
        return self

    def stop(self):
        self.drop()
        if self._http:
            self._http.shutdown()
            self._http.server_close()
        if self._ws:
            self._ws.shutdown()

    # --- addresses ---
    @property
    def api_url(self):
        return f"http://127.0.0.1:{self._http.server_address[1]}/api/"

    @property
    def ws_url(self):
        return f"ws://127.0.0.1:{self._ws.socket.getsockname()[1]}/rtm"

    # --- web api ---
    def _answer(self, method, params, headers):
        self.web_calls.append((method, params, headers))
        status = self.api_status.get(method, 200)
        override = self.api_overrides.get(method)
        if isinstance(override, list):
            override = override.pop(0) if override else None
        if override is not None:
            return status, override
        if method in self.api_responses:
            return status, self.api_responses[method]
        if method == "client.getWebSocketURL":
            return status, {"ok": True, "primary_websocket_url": self.ws_url}
        if method == "chat.postMessage":
            return status, {"ok": True, "channel": params.get("channel"),
                            "ts": f"1700000000.{len(self.calls('chat.postMessage')):06d}"}
        return status, {"ok": True}

    def calls(self, method):
        return [p for m, p, _ in list(self.web_calls) if m == method]

    def posted(self):
        """(channel, text, thread_ts) of every chat.postMessage."""
        return [(p.get("channel"), p.get("text"), p.get("thread_ts"))
                for p in self.calls("chat.postMessage")]

    # --- websocket ---
    def _process_request(self, connection, request):
        self.ws_paths.append(request.path)
        self.ws_headers.append({"Cookie": request.headers.get("Cookie", "")})
        if self.ws_status:
            return connection.respond(HTTPStatus(self.ws_status), "refused\n")
        query = parse_qs(urlparse(request.path).query)
        cookie = request.headers.get("Cookie", "")
        if query.get("token", [None])[0] != self.token or f"d={self.cookie}" not in cookie:
            return connection.respond(HTTPStatus.UNAUTHORIZED, "bad session\n")
        return None

    def _ws_handler(self, conn):
        with self._lock:
            self._conn = conn
        try:
            conn.send(json.dumps({"type": "hello"}))
            for raw in conn:
                try:
                    frame = json.loads(raw)
                except ValueError:
                    continue
                self.frames.append(frame)
                if frame.get("type") == "ping":
                    conn.send(json.dumps({"type": "pong", "reply_to": frame.get("id")}))
        except Exception:  # noqa: BLE001
            pass
        finally:
            with self._lock:
                if self._conn is conn:
                    self._conn = None

    def connected(self):
        return self._conn is not None

    def push(self, payload):
        """send a frame (dict, or a raw str) to the connected client."""
        data = payload if isinstance(payload, str) else json.dumps(payload)
        with self._lock:
            conn = self._conn
        if conn is None:
            raise RuntimeError("no client connected")
        conn.send(data)

    def drop(self):
        with self._lock:
            conn = self._conn
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass

    def frames_of(self, type_):
        return [f for f in list(self.frames) if f.get("type") == type_]
