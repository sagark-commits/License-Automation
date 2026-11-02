"""A tiny WSGI test client with cookies and CSRF helpers (no network, no server)."""
from __future__ import annotations

import io
import json
import re
import time
import urllib.parse
from http.cookies import SimpleCookie
from typing import Any, Dict, Optional


class Resp:
    def __init__(self, status: int, headers: Dict[str, str], raw: bytes, set_cookies):
        self.status, self.headers, self.raw, self.set_cookies = status, headers, raw, set_cookies

    @property
    def text(self) -> str:
        return self.raw.decode("utf-8", "replace")

    def json(self) -> Any:
        return json.loads(self.text)

    @property
    def location(self) -> Optional[str]:
        return self.headers.get("Location")


class Client:
    def __init__(self, app, ip: str = "10.1.2.3"):
        self.app, self.ip = app, ip
        self.cookies: Dict[str, str] = {}

    def request(self, method: str, path: str, data: Optional[Dict[str, Any]] = None, json_body: Any = None,
                headers: Optional[Dict[str, str]] = None, csrf: Optional[str] = None) -> Resp:
        path, _, qs = path.partition("?")
        body = b""
        env: Dict[str, Any] = {
            "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": qs, "SERVER_NAME": "testserver", "SERVER_PORT": "80",
            "HTTP_HOST": "testserver", "REMOTE_ADDR": self.ip, "wsgi.url_scheme": "http", "wsgi.errors": io.StringIO(),
        }
        if json_body is not None:
            body = json.dumps(json_body).encode("utf-8")
            env["CONTENT_TYPE"] = "application/json"
        elif data is not None:
            pairs = []
            for k, v in data.items():
                for item in (v if isinstance(v, (list, tuple)) else [v]):
                    pairs.append((k, item))
            body = urllib.parse.urlencode(pairs).encode("utf-8")
            env["CONTENT_TYPE"] = "application/x-www-form-urlencoded"
        env["CONTENT_LENGTH"] = str(len(body))
        env["wsgi.input"] = io.BytesIO(body)
        if self.cookies:
            env["HTTP_COOKIE"] = "; ".join("%s=%s" % kv for kv in self.cookies.items())
        for k, v in (headers or {}).items():
            env["HTTP_" + k.upper().replace("-", "_")] = v
        if csrf:
            env["HTTP_X_CSRF_TOKEN"] = csrf
        out: Dict[str, Any] = {}

        def start_response(status, hdrs, exc=None):
            out["status"], out["headers"] = int(status.split()[0]), hdrs

        chunks = self.app(env, start_response)
        raw = b"".join(chunks)
        if hasattr(chunks, "close"):
            chunks.close()
        hd: Dict[str, str] = {}
        cookies = []
        for k, v in out["headers"]:
            if k.lower() == "set-cookie":
                cookies.append(v)
                c = SimpleCookie()
                c.load(v)
                for name, morsel in c.items():
                    if morsel.value:
                        self.cookies[name] = morsel.value
                    else:
                        self.cookies.pop(name, None)
            hd[k] = v
        return Resp(out["status"], hd, raw, cookies)

    def get(self, path: str, **kw) -> Resp:
        return self.request("GET", path, **kw)

    def post(self, path: str, data: Optional[Dict[str, Any]] = None, **kw) -> Resp:
        return self.request("POST", path, data=data if data is not None else {}, **kw)

    def follow(self, resp: Resp) -> Resp:
        assert resp.status in (302, 303), resp.status
        return self.get(resp.location)

    def token(self) -> str:
        page = self.get("/generate")
        m = re.search(r'name="csrf" content="([^"]+)"', page.text)
        assert m, "no csrf meta (not logged in?)"
        return m.group(1)

    def login(self, username: str, password: str) -> Resp:
        page = self.get("/login")
        tok = re.search(r'name="_token" value="([^"]+)"', page.text).group(1)
        return self.post("/login", {"username": username, "password": password, "_token": tok, "next": ""})

    def post_csrf(self, path: str, data: Optional[Dict[str, Any]] = None, **kw) -> Resp:
        d = dict(data or {})
        d["_csrf"] = self.token()
        return self.post(path, d, **kw)


def wait_for(cond, timeout: float = 20.0, step: float = 0.05):
    end = time.time() + timeout
    while time.time() < end:
        v = cond()
        if v:
            return v
        time.sleep(step)
    raise AssertionError("condition not met in %.0fs" % timeout)
