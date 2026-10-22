"""WSGI application: routing, sessions, CSRF, security headers and the core pages."""
from __future__ import annotations

import datetime as dt
import json
import logging
import re
import time
import urllib.parse
from http.cookies import SimpleCookie
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple

from . import __version__, ui
from .auth import LoginThrottle, Sessions, UserStore, can, load_or_create_secret, sign, unsign
from .jobs import JobManager, is_valid_run_id
from .pages_config import ConfigPages
from .store import DataDir

log = logging.getLogger("lic_web")
COOKIE = "lic_sid"
MAX_BODY = 1024 * 1024
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class Response:
    def __init__(self, body: Any = b"", status: int = 200, headers: Optional[List[Tuple[str, str]]] = None, ctype: str = "text/html; charset=utf-8"):
        self.status = status
        self.headers: List[Tuple[str, str]] = [("Content-Type", ctype)] + (headers or [])
        self.body = body.encode("utf-8") if isinstance(body, str) else body


class FileBody:
    """Iterable file body that closes the file when the server is done with it."""

    def __init__(self, path):
        self.fh = open(path, "rb")

    def __iter__(self):
        return self

    def __next__(self) -> bytes:
        chunk = self.fh.read(65536)
        if not chunk:
            raise StopIteration
        return chunk

    def close(self) -> None:
        self.fh.close()


STATUS_TEXT = {200: "OK", 302: "Found", 303: "See Other", 400: "Bad Request", 401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
               405: "Method Not Allowed", 411: "Length Required", 413: "Payload Too Large", 429: "Too Many Requests", 500: "Internal Server Error"}


class Request:
    def __init__(self, environ: Dict[str, Any], app: "App"):
        self.environ = environ
        self.app = app
        self.method = environ.get("REQUEST_METHOD", "GET").upper()
        self.path = urllib.parse.unquote(environ.get("PATH_INFO", "/") or "/")
        self.query = urllib.parse.parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True)
        self.cookies = SimpleCookie()
        try:
            self.cookies.load(environ.get("HTTP_COOKIE", ""))
        except Exception:
            pass
        self._form: Optional[Dict[str, List[str]]] = None
        self._json: Any = None
        self.session: Optional[Dict[str, Any]] = None
        self.user: Optional[Dict[str, Any]] = None
        ip = environ.get("REMOTE_ADDR", "")
        if app.trust_proxy and environ.get("HTTP_X_FORWARDED_FOR"):
            ip = environ["HTTP_X_FORWARDED_FOR"].split(",")[-1].strip()[:45]
        self.ip = ip
        self.https = environ.get("wsgi.url_scheme") == "https" or (app.trust_proxy and environ.get("HTTP_X_FORWARDED_PROTO") == "https")

    def body(self) -> bytes:
        length = self.environ.get("CONTENT_LENGTH")
        if length in (None, ""):
            return b""
        try:
            n = int(length)
        except ValueError:
            raise HttpError(400, "Bad Content-Length")
        if n < 0 or n > MAX_BODY:
            raise HttpError(413, "Request too large")
        return self.environ["wsgi.input"].read(n)

    def form(self) -> Dict[str, List[str]]:
        if self._form is None:
            ctype = self.environ.get("CONTENT_TYPE", "")
            raw = self.body()
            if ctype.startswith("application/x-www-form-urlencoded"):
                try:
                    self._form = urllib.parse.parse_qs(raw.decode("utf-8"), keep_blank_values=True)
                except UnicodeDecodeError:
                    raise HttpError(400, "Bad encoding")
            else:
                self._form = {}
                if ctype.startswith("application/json") and raw:
                    try:
                        self._json = json.loads(raw.decode("utf-8"))
                    except (ValueError, UnicodeDecodeError):
                        raise HttpError(400, "Bad JSON")
        return self._form

    def f(self, name: str, default: str = "") -> str:
        v = self.form().get(name)
        return v[0] if v else default

    def fl(self, name: str) -> List[str]:
        return self.form().get(name, [])

    def json_body(self) -> Dict[str, Any]:
        self.form()
        return self._json if isinstance(self._json, dict) else {}

    def q(self, name: str, default: str = "") -> str:
        v = self.query.get(name)
        return v[0] if v else default

    def flash(self, kind: str, text: str) -> None:
        if self.session is not None:
            self.session["flash"] = {"kind": kind, "text": text}


class HttpError(Exception):
    def __init__(self, status: int, message: str = ""):
        super().__init__(message)
        self.status, self.message = status, message


def redirect(url: str, headers: Optional[List[Tuple[str, str]]] = None) -> Response:
    return Response(b"", 303, [("Location", url)] + (headers or []))


def json_response(obj: Any, status: int = 200) -> Response:
    return Response(json.dumps(obj, default=str), status, ctype="application/json; charset=utf-8")


def safe_next(url: str) -> str:
    """Only allow same-site relative redirects (no open redirect)."""
    if url and url.startswith("/") and not url.startswith("//") and "\\" not in url and "\n" not in url and "\r" not in url:
        return url
    return "/generate"


def month_options(n: int = 13) -> List[str]:
    today = dt.date.today()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        out.append("%04d-%02d" % (y, m))
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return out


def fmt_dt(s: Optional[str]) -> str:
    return s or "-"


def duration(a: Optional[str], b: Optional[str]) -> str:
    try:
        d = dt.datetime.strptime(b, "%Y-%m-%d %H:%M:%S") - dt.datetime.strptime(a, "%Y-%m-%d %H:%M:%S")
    except (TypeError, ValueError):
        return "-"
    s = int(d.total_seconds())
    return "%dm %02ds" % (s // 60, s % 60)


class App(ConfigPages):
    def __init__(self, data: DataDir, jobs: JobManager, users: UserStore, *, trust_proxy: bool = False, secure_cookies: bool = False):
        self.data, self.jobs, self.users = data, jobs, users
        self.sessions = Sessions()
        self.throttle = LoginThrottle()
        self.secret = load_or_create_secret(data.secret_path)
        self.trust_proxy, self.secure_cookies = trust_proxy, secure_cookies
        self.routes = self._build_routes()

    # ------------------------------------------------------------------ routing table
    def _build_routes(self) -> List[Tuple[str, "re.Pattern[str]", Callable[..., Response], Optional[str]]]:
        R = [
            ("GET", r"^/$", self.h_root, None),
            ("GET", r"^/healthz$", self.h_health, None),
            ("GET", r"^/static/(?P<name>app\.css|app\.js)$", self.h_static, None),
            ("GET", r"^/login$", self.h_login_form, None),
            ("POST", r"^/login$", self.h_login, None),
            ("POST", r"^/logout$", self.h_logout, "auth"),
            ("GET", r"^/account$", self.h_account, "auth"),
            ("POST", r"^/account$", self.h_account_post, "auth"),
            ("GET", r"^/generate$", self.h_generate, "view"),
            ("POST", r"^/generate$", self.h_generate_post, "generate"),
            ("GET", r"^/runs$", self.h_runs, "view"),
            ("GET", r"^/runs/(?P<rid>[0-9a-f\-]+)$", self.h_run, "view"),
            ("GET", r"^/runs/(?P<rid>[0-9a-f\-]+)/status\.json$", self.h_run_status, "view"),
            ("POST", r"^/runs/(?P<rid>[0-9a-f\-]+)/cancel$", self.h_run_cancel, "generate"),
            ("POST", r"^/runs/(?P<rid>[0-9a-f\-]+)/delete$", self.h_run_delete, "delete_runs"),
            ("POST", r"^/runs/(?P<rid>[0-9a-f\-]+)/rerun$", self.h_run_rerun, "generate"),
            ("GET", r"^/runs/(?P<rid>[0-9a-f\-]+)/download/(?P<kind>utilization|login|manifest|zip)$", self.h_download, "download"),
            ("GET", r"^/users$", self.h_users, "users"),
            ("POST", r"^/users$", self.h_users_post, "users"),
            ("GET", r"^/audit$", self.h_audit, "audit"),
        ] + self.config_routes()
        return [(m, re.compile(p), h, perm) for m, p, h, perm in R]

    # ------------------------------------------------------------------ WSGI entry
    def __call__(self, environ: Dict[str, Any], start_response: Callable) -> Iterable[bytes]:
        started = time.time()
        req = Request(environ, self)
        try:
            resp = self.dispatch(req)
        except HttpError as e:
            resp = self.error_page(req, e.status, e.message)
        except Exception:
            log.exception("unhandled error for %s %s", req.method, req.path)
            resp = self.error_page(req, 500, "Something went wrong on the server. The details are in the server log.")
        self._secure(resp, req)
        start_response("%d %s" % (resp.status, STATUS_TEXT.get(resp.status, "OK")), resp.headers)
        log.info("%s %s %s %s %dms user=%s", req.ip, req.method, repr(req.path), resp.status, int((time.time() - started) * 1000), (req.user or {}).get("username", "-"))
        if req.method == "HEAD":
            if isinstance(resp.body, FileBody):
                resp.body.close()
            return []
        if isinstance(resp.body, FileBody):
            return resp.body
        return [resp.body]

    def _secure(self, resp: Response, req: Request) -> None:
        h = resp.headers
        names = {k.lower() for k, _ in h}
        add = {
            "X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY", "Referrer-Policy": "same-origin",
            "Content-Security-Policy": "default-src 'self'; style-src 'self'; script-src 'self'; img-src 'self' data:; form-action 'self'; frame-ancestors 'none'; base-uri 'none'",
        }
        if "cache-control" not in names:
            add["Cache-Control"] = "no-store"
        if req.https:
            add["Strict-Transport-Security"] = "max-age=31536000"
        for k, v in add.items():
            if k.lower() not in names:
                h.append((k, v))

    def dispatch(self, req: Request) -> Response:
        path_matched = False
        for method, pattern, handler, perm in self.routes:
            m = pattern.match(req.path)
            if not m:
                continue
            path_matched = True
            if method != ("GET" if req.method == "HEAD" else req.method):     # HEAD behaves like GET (monitoring probes)
                continue
            self.load_session(req)
            if perm is not None:
                if not req.user:
                    if req.path.endswith(".json") or req.environ.get("CONTENT_TYPE", "").startswith("application/json"):
                        return json_response({"error": "Please sign in again."}, 401)
                    return redirect("/login?next=" + urllib.parse.quote(req.path + (("?" + req.environ["QUERY_STRING"]) if req.environ.get("QUERY_STRING") else "")))
                if perm != "auth" and not can(req.user["role"], perm):
                    raise HttpError(403, "Your role (%s) is not allowed to do this." % req.user["role"])
            if req.method == "POST" and handler != self.h_login:
                self.check_csrf(req)
            return handler(req, **m.groupdict())
        if path_matched:
            raise HttpError(405, "Method not allowed")
        raise HttpError(404, "Page not found")

    # ------------------------------------------------------------------ sessions / csrf
    def load_session(self, req: Request) -> None:
        morsel = req.cookies.get(COOKIE)
        sid = unsign(self.secret, morsel.value) if morsel else None
        sess = self.sessions.get(sid)
        if sess:
            user = self.users.get(sess["user"])
            if user:
                req.session, req.user = sess, user
                req.sid = sid  # type: ignore[attr-defined]

    def check_csrf(self, req: Request) -> None:
        origin = req.environ.get("HTTP_ORIGIN")
        if origin and origin != "null":
            host = req.environ.get("HTTP_HOST", "")
            if urllib.parse.urlparse(origin).netloc != host:
                raise HttpError(403, "Cross-site request blocked.")
        token = req.environ.get("HTTP_X_CSRF_TOKEN") or req.f("_csrf")
        if not req.session or not token or not _consteq(token, req.session["csrf"]):
            raise HttpError(403, "Your form expired. Go back, reload the page and try again.")

    def cookie_header(self, sid: str, clear: bool = False, https: bool = False) -> Tuple[str, str]:
        parts = ["%s=%s" % (COOKIE, "" if clear else sign(self.secret, sid)), "Path=/", "HttpOnly", "SameSite=Strict"]
        if clear:
            parts.append("Max-Age=0")
        if self.secure_cookies or https:
            parts.append("Secure")
        return ("Set-Cookie", "; ".join(parts))

    # ------------------------------------------------------------------ rendering helpers
    def page(self, req: Request, title: str, body: str, active: str = "", status: int = 200) -> Response:
        flash = req.session.pop("flash", None) if req.session else None
        html = ui.layout(title, body, user=req.user, csrf=(req.session or {}).get("csrf", ""), active=active, can=can, flash=flash)
        return Response(html, status)

    def error_page(self, req: Request, status: int, message: str) -> Response:
        if req.path.endswith(".json"):
            return json_response({"error": message}, status)
        body = '<div class="card"><h1>%s</h1><p>%s</p><p><a href="/">Back to the start page</a></p></div>' % (
            ui.esc("%d %s" % (status, STATUS_TEXT.get(status, "Error"))), ui.esc(message))
        return self.page(req, "Error", body, status=status)

    def csrf(self, req: Request) -> str:
        return ui.csrf_field(req.session["csrf"]) if req.session else ""

    # ------------------------------------------------------------------ simple handlers
    def h_root(self, req: Request) -> Response:
        return redirect("/generate" if req.user else "/login")

    def h_health(self, req: Request) -> Response:
        worker = self.jobs._worker
        return json_response({"status": "ok", "version": __version__, "worker": bool(worker and worker.is_alive()), "time": time.strftime("%Y-%m-%d %H:%M:%S")})

    def h_static(self, req: Request, name: str) -> Response:
        if name == "app.css":
            return Response(ui.CSS, ctype="text/css; charset=utf-8", headers=[("Cache-Control", "public, max-age=600")])
        return Response(ui.JS, ctype="application/javascript; charset=utf-8", headers=[("Cache-Control", "public, max-age=600")])

    # ------------------------------------------------------------------ login / account
    def _login_token(self) -> str:
        return sign(self.secret, "login:%d" % int(time.time()))

    def _login_token_ok(self, token: str) -> bool:
        v = unsign(self.secret, token or "")
        if not v or not v.startswith("login:"):
            return False
        try:
            return 0 <= time.time() - int(v.split(":")[1]) < 3600
        except ValueError:
            return False

    def h_login_form(self, req: Request, error: str = "", username: str = "") -> Response:
        self.load_session(req)
        if req.user:
            return redirect("/generate")
        no_users = self.users.count() == 0
        note = ""
        if no_users:
            note = ui.alert("warn", "No users exist yet. On the server run <code>python3.9 run_web.py init</code> to create the first administrator.")
        body = ('<div class="login card"><h1>Sign in</h1><p class="sub">TMONE licence utilization reports</p>%s%s'
                '<form method="post" action="/login" autocomplete="off"><input type="hidden" name="next" value="%s"><input type="hidden" name="_token" value="%s">'
                '%s%s<button type="submit">Sign in</button></form></div>') % (
            note, ui.alert("bad", ui.esc(error)) if error else "", ui.esc(safe_next(req.q("next"))), ui.esc(self._login_token()),
            ui.field("Username", "username", username, extra='autofocus autocomplete="username"'),
            ui.field("Password", "password", "", type_="password", extra='autocomplete="current-password"'))
        return Response(ui.layout("Sign in", body))

    def h_login(self, req: Request) -> Response:
        origin = req.environ.get("HTTP_ORIGIN")
        if origin and origin != "null" and urllib.parse.urlparse(origin).netloc != req.environ.get("HTTP_HOST", ""):
            raise HttpError(403, "Cross-site request blocked.")
        username, password = req.f("username").strip(), req.f("password")
        if not self._login_token_ok(req.f("_token")):
            return self.h_login_form(req, "The sign-in form expired. Please try again.", username)
        wait = self.throttle.blocked_for(req.ip, username)
        if wait:
            self.data.audit(username, "login.blocked", "locked for %ds" % wait, req.ip)
            return self.h_login_form(req, "Too many failed attempts. Try again in %d minute(s)." % ((wait + 59) // 60), username)
        user = self.users.authenticate(username, password) if username and password else None
        if not user:
            self.throttle.failure(req.ip, username)
            self.data.audit(username or "?", "login.fail", "", req.ip)
            return self.h_login_form(req, "Invalid username or password.", username)
        self.throttle.success(req.ip, username)
        sid, sess = self.sessions.create(user)
        self.data.audit(username, "login.ok", "", req.ip)
        return redirect(safe_next(req.f("next")), [self.cookie_header(sid, https=req.https)])

    def h_logout(self, req: Request) -> Response:
        self.data.audit(req.user["username"], "logout", "", req.ip)
        self.sessions.destroy(getattr(req, "sid", None))
        return redirect("/login", [self.cookie_header("", clear=True, https=req.https)])

    def h_account(self, req: Request) -> Response:
        body = ('<h1>My account</h1><p class="sub">Signed in as <b>%s</b> (%s)</p><div class="card"><h2>Change password</h2>'
                '<form method="post" action="/account">%s%s%s%s<button type="submit">Change password</button></form></div>') % (
            ui.esc(req.user["username"]), ui.esc(req.user["role"]), self.csrf(req),
            ui.field("Current password", "current", "", type_="password", extra='autocomplete="current-password"'),
            ui.field("New password", "new", "", type_="password", extra='autocomplete="new-password"', hint="At least 10 characters, two kinds of characters."),
            ui.field("Repeat new password", "new2", "", type_="password", extra='autocomplete="new-password"'))
        return self.page(req, "My account", body)

    def h_account_post(self, req: Request) -> Response:
        u = req.user["username"]
        if not self.users.authenticate(u, req.f("current")):
            req.flash("bad", "The current password is wrong.")
            return redirect("/account")
        if req.f("new") != req.f("new2"):
            req.flash("bad", "The two new passwords are different.")
            return redirect("/account")
        try:
            self.users.set_password(u, req.f("new"))
            self.sessions.destroy_user(u)          # other sessions of this user end; this one is re-issued below
            sid, _ = self.sessions.create(self.users.get(u))
            self.data.audit(u, "password.change", "", req.ip)
            req.flash("ok", "Password changed.")
            return redirect("/account", [self.cookie_header(sid, https=req.https)])
        except ValueError as e:
            req.flash("bad", str(e))
            return redirect("/account")
        self.data.audit(u, "password.change", "", req.ip)
        req.flash("ok", "Password changed.")
        return redirect("/account")

    # ------------------------------------------------------------------ generate
    def h_generate(self, req: Request) -> Response:
        reg = self.data.load_registry()
        tenants = [(k, c) for k, c in reg["tenants"].items() if c.get("enabled", True) is not False]
        arcs = sorted({c.get("arc") for _, c in tenants if c.get("arc")})
        months = month_options()
        default_month = months[1]
        active = self.jobs.active()
        banners = ""
        if not self.data.db_configured():
            link = ' <a href="/settings/db">Open Databases</a>' if can(req.user["role"], "db") else " Ask an administrator."
            banners += ui.alert("warn", "<b>Database connections are not set up yet.</b>" + link)
        if active:
            banners += ui.alert("info", 'A report is running right now: <a href="/runs/%s">open it</a>. New requests are queued behind it.' % ui.esc(active))
        last = self.jobs.last_done()
        last_html = ""
        if last:
            res = last.get("result") or {}
            last_html = ('<div class="card"><h2>Latest finished report</h2><p>%s &middot; %s &middot; %s &middot; <a href="/runs/%s">open</a></p></div>') % (
                ui.esc(last["month"]), ui.badge(res.get("health", "?"), res.get("health")), ui.esc(last.get("finished")), ui.esc(last["id"]))
        if not can(req.user["role"], "generate"):
            form = ui.alert("info", "Your role (viewer) can download existing reports but not generate new ones.")
        else:
            boxes = ""
            for arc in arcs:
                boxes += '<p><b>%s</b> <button type="button" class="sm sec" data-sel="all" data-arc="%s">all</button> <button type="button" class="sm sec" data-sel="none" data-arc="%s">none</button></p>' % (
                    ui.esc(arc), ui.esc(arc), ui.esc(arc))
                boxes += "".join('<label class="check"><input type="checkbox" name="tenant" value="%s" data-arc="%s"> %s <span class="mute">(cc %s)</span></label>' % (
                    ui.esc(k), ui.esc(arc), ui.esc(str(c.get("project_name", k)).split("\n")[0]), ui.esc(c.get("contact_center_id")))
                    for k, c in tenants if c.get("arc") == arc)
            form = ('<form method="post" action="/generate">%s<div class="row">%s%s</div>'
                    '<div id="scope-tenants" class="scope">%s</div><p class="hint">Disabled tenants are left out automatically. Generating takes a few minutes; '
                    'you can close this page - the run continues in the background.</p><button type="submit"%s>Generate report</button></form>') % (
                self.csrf(req),
                ui.select("Month", "month", [(m, m + (" (in progress)" if i == 0 else "")) for i, m in enumerate(months)], default_month, cls="s"),
                ui.select("Scope", "scope", [("all", "All enabled tenants (%d)" % len(tenants))] + [("arc:" + a, "Only %s" % a) for a in arcs] + [("selected", "Selected tenants...")], "all", extra='id="scope-mode"'),
                boxes, "" if self.data.db_configured() else " disabled")
        body = '<h1>Generate the monthly report</h1><p class="sub">Produces the utilization workbook and the login-count workbook, then checks them against each other.</p>%s<div class="card">%s</div>%s' % (banners, form, last_html)
        return self.page(req, "Generate", body, "generate")

    def h_generate_post(self, req: Request) -> Response:
        month = req.f("month")
        if not MONTH_RE.match(month) or month not in month_options(25):
            req.flash("bad", "Choose a valid month.")
            return redirect("/generate")
        if not self.data.db_configured():
            req.flash("bad", "Database connections are not configured.")
            return redirect("/generate")
        reg = self.data.load_registry()
        enabled = [k for k, c in reg["tenants"].items() if c.get("enabled", True) is not False]
        scope, arc, tenants = req.f("scope", "all"), None, None
        if scope.startswith("arc:"):
            arc = scope[4:]
            if arc not in self.data.arcs():
                raise HttpError(400, "Unknown ARC")
        elif scope == "selected":
            tenants = [t for t in req.fl("tenant") if t in enabled]
            if not tenants:
                req.flash("bad", "Tick at least one tenant, or choose another scope.")
                return redirect("/generate")
        if not [k for k in enabled if (arc is None or reg["tenants"][k].get("arc") == arc)]:
            req.flash("bad", "No enabled tenants match that scope.")
            return redirect("/generate")
        try:
            rid = self.jobs.submit(month, arc, tenants, req.user["username"])
        except ValueError as e:
            req.flash("bad", str(e))
            return redirect("/generate")
        return redirect("/runs/" + rid)

    # ------------------------------------------------------------------ runs
    def h_runs(self, req: Request) -> Response:
        rows = []
        for r in self.jobs.list(100):
            c = r.get("counts") or {}
            rows.append([
                '<a href="/runs/%s">%s</a>' % (ui.esc(r["id"]), ui.esc(r["month"])), ui.esc(fmt_dt(r["created"])), ui.esc(r["user"]),
                ui.esc(r["arc"] or ("%d tenants" % len(r["tenants"]) if r.get("tenants") else "all")), ui.badge(r["status"]),
                ui.badge(r["health"], r["health"]) if r.get("health") else '<span class="mute">-</span>',
                ui.esc("%s err / %s warn" % (c.get("errors", 0), c.get("warnings", 0))) if c else "", ui.esc(duration(r.get("started"), r.get("finished")))])
        body = '<h1>Reports</h1><p class="sub">Every run is kept (newest %d) so it can be downloaded again.</p><div class="card">%s</div>' % (
            self.jobs.keep_runs, ui.table(["Month", "Requested", "By", "Scope", "Status", "Checks", "Findings", "Took"], rows, empty="No report has been generated yet."))
        return self.page(req, "Reports", body, "runs")

    def _get_run(self, rid: str) -> Dict[str, Any]:
        run = self.jobs.get(rid) if is_valid_run_id(rid) else None
        if not run:
            raise HttpError(404, "That report does not exist (it may have been deleted).")
        return run

    def h_run_status(self, req: Request, rid: str) -> Response:
        run = self._get_run(rid)
        return json_response({"status": run["status"], "progress": run["progress"], "error": run["error"], "events": self.jobs.events(rid, 300)})

    def h_run(self, req: Request, rid: str) -> Response:
        run = self._get_run(rid)
        res = run.get("result") or {}
        finished = run["status"] in ("done", "failed", "cancelled", "interrupted")
        scope = run["arc"] or ("%d selected tenant(s)" % len(run["tenants"]) if run.get("tenants") else "all enabled tenants")
        head = ('<div class="row"><div><h1>Report %s</h1><p class="sub">%s &middot; requested by %s &middot; %s</p></div>'
                '<div class="right">%s</div></div>') % (
            ui.esc(run["month"]), ui.esc(scope), ui.esc(run["user"]), ui.esc(run["created"]),
            "".join(filter(None, [
                ('<form class="inl" method="post" action="/runs/%s/cancel">%s<button class="bad sm">Cancel</button></form>' % (ui.esc(rid), self.csrf(req))) if run["status"] in ("queued", "running") and can(req.user["role"], "generate") else "",
                ('<form class="inl" method="post" action="/runs/%s/rerun">%s<button class="sec sm">Run again</button></form>' % (ui.esc(rid), self.csrf(req))) if finished and can(req.user["role"], "generate") else "",
                ('<form class="inl" method="post" action="/runs/%s/delete" data-confirm="1">%s<button class="bad sm">Delete</button></form>' % (ui.esc(rid), self.csrf(req))) if finished and can(req.user["role"], "delete_runs") else ""])))
        pg = run["progress"]
        live = ('<div class="card" id="run-live" data-run="%s" data-finished="%d"><div class="row center"><b>Status</b> <span id="st" class="badge b-%s">%s</span>'
                '<span class="mute" id="stage">%s</span><span class="right" id="pct">%d%%</span></div><div class="bar m10"><i id="bar" data-pct="%d"></i></div>'
                '<pre class="log" id="log">%s</pre></div>') % (
            ui.esc(rid), 1 if finished else 0, ui.esc(run["status"]), ui.esc(run["status"]), ui.esc(pg.get("message", "")), pg.get("percent", 0), pg.get("percent", 0),
            ui.esc("\n".join(self.jobs.events(rid, 300))))
        parts = [head, live]
        if run.get("error"):
            parts.insert(1, ui.alert("bad", "<b>The report could not be completed.</b><br>%s" % ui.esc(run["error"])))
        if res:
            health = res.get("health")
            c = res.get("counts", {})
            msg = {"pass": ("ok", "All checks passed. The two workbooks agree with each other and with the registry."),
                   "warn": ("warn", "Generated, with %d warning(s). Review them below before sending." % c.get("warnings", 0)),
                   "fail": ("bad", "<b>Do not send this report yet:</b> %d check(s) failed. See the findings below." % c.get("errors", 0))}[health]
            parts.insert(1, ui.alert(msg[0], msg[1]))
            if can(req.user["role"], "download"):
                parts.append('<div class="card"><h2>Downloads</h2><div class="row">%s</div></div>' % "".join(
                    '<a class="btn%s" href="/runs/%s/download/%s">%s</a>' % ("" if kind in ("utilization", "login") else " sec", ui.esc(rid), kind, label)
                    for kind, label in (("utilization", "Utilization workbook (.xlsx)"), ("login", "Login-count workbook (.xlsx)"), ("zip", "Both + manifest (.zip)"), ("manifest", "manifest.json"))))
            order = {"error": 0, "warn": 1, "info": 2}
            fr = [[ui.badge(f["severity"], {"error": "bad", "warn": "warn", "info": "info"}[f["severity"]]), ui.esc(f["code"]), ui.esc(f["message"])]
                  for f in sorted(res.get("findings", []), key=lambda f: order.get(f["severity"], 3))]
            parts.append('<div class="card"><h2>Checks and findings</h2>%s</div>' % ui.table(["", "Code", "What was found"], fr, empty="No findings - nothing to review."))
            tr = []
            for t in res.get("tenants", []):
                def pk(lic: str) -> str:
                    p = t["peaks"].get(lic)
                    return "%d <span class='mute small'>%s h%s</span>" % (p["count"], ui.esc(p["date"][5:] if p.get("date") else ""), p["hour"]) if p else '<span class="mute">0</span>'
                sheets = ", ".join("%s %d/%d" % (lic, v["users"], t["peaks"][lic]["count"]) for lic, v in t["login_sheets"].items() if t["peaks"].get(lic))
                tr.append([ui.esc(t["project"]), ui.esc(t["arc"]), pk("agent"), pk("supervisor"), pk("wallboard"), '<span class="small mute">%s</span>' % ui.esc(sheets),
                           ui.badge({"ok": "ok", "no_data": "no usage", "error": "error"}[t["status"]], {"ok": "ok", "no_data": "warn", "error": "bad"}[t["status"]])])
            parts.append('<div class="card"><h2>Tenants</h2><p class="hint">Peak = highest hourly distinct-user count of the month (date, hour). '
                         'Login sheets show distinct users listed / peak.</p>%s</div>' % ui.table(["Tenant", "ARC", "Agent", "Supervisor", "Wallboard", "Login evidence", "Status"], tr))
        return self.page(req, "Report " + run["month"], "".join(parts), "runs")

    def h_run_cancel(self, req: Request, rid: str) -> Response:
        self._get_run(rid)
        if self.jobs.cancel(rid, req.user["username"]):
            req.flash("info", "Cancel requested - the run stops after the tenant it is working on.")
        else:
            req.flash("warn", "That run already finished.")
        return redirect("/runs/" + rid)

    def h_run_delete(self, req: Request, rid: str) -> Response:
        self._get_run(rid)
        ok = self.jobs.delete(rid, req.user["username"])
        req.flash("ok" if ok else "warn", "Report deleted." if ok else "Only finished reports can be deleted.")
        return redirect("/runs" if ok else "/runs/" + rid)

    def h_run_rerun(self, req: Request, rid: str) -> Response:
        run = self._get_run(rid)
        try:
            new = self.jobs.submit(run["month"], run["arc"], run["tenants"], req.user["username"])
        except ValueError as e:
            req.flash("bad", str(e))
            return redirect("/runs/" + rid)
        return redirect("/runs/" + new)

    def h_download(self, req: Request, rid: str, kind: str) -> Response:
        self._get_run(rid)
        path = self.jobs.make_zip(rid) if kind == "zip" else self.jobs.file_path(rid, kind)
        if not path:
            raise HttpError(404, "That file does not exist for this run.")
        self.data.audit(req.user["username"], "download", "%s %s" % (rid, kind), req.ip)
        ctype = XLSX if path.suffix == ".xlsx" else ("application/zip" if path.suffix == ".zip" else "application/json")
        quoted = urllib.parse.quote(path.name)
        ascii_name = re.sub(r'[^A-Za-z0-9._ -]', "_", path.name)
        size = path.stat().st_size
        return Response(FileBody(path), 200, [("Content-Disposition", "attachment; filename=\"%s\"; filename*=UTF-8''%s" % (ascii_name, quoted)),
                                                ("Content-Length", str(size))], ctype=ctype)

    # ------------------------------------------------------------------ users (admin)
    def h_users(self, req: Request) -> Response:
        rows = []
        for u in self.users.list():
            me = u["username"] == req.user["username"]
            acts = ""
            if not me:
                acts = ('<form class="inl" method="post" action="/users">%s<input type="hidden" name="username" value="%s">'
                        '<button class="sm sec" name="action" value="%s">%s</button> <button class="sm bad" name="action" value="delete" data-confirm="1">Delete</button></form>') % (
                    self.csrf(req), ui.esc(u["username"]), "enable" if u["disabled"] else "disable", "Enable" if u["disabled"] else "Disable")
            rows.append([ui.esc(u["username"]), ui.esc(u["role"]), ui.badge("disabled", "off") if u["disabled"] else ui.badge("active", "ok"), ui.esc(u["last_login"] or "never"), acts])
        add = ('<form method="post" action="/users"><input type="hidden" name="action" value="add">%s<div class="row">%s%s%s</div><button type="submit">Add user</button></form>') % (
            self.csrf(req), ui.field("Username", "username", "", extra='autocomplete="off"'),
            ui.field("Initial password", "password", "", type_="password", extra='autocomplete="new-password"', hint="Min 10 characters; the user can change it under My account."),
            ui.select("Role", "role", [("operator", "operator - generate reports, edit tenants"), ("viewer", "viewer - view and download"), ("admin", "admin - everything")], "operator"))
        reset = ('<form method="post" action="/users"><input type="hidden" name="action" value="reset">%s<div class="row">%s%s%s</div><button class="sec" type="submit">Reset password / change role</button></form>') % (
            self.csrf(req), ui.field("Username", "username", ""), ui.field("New password (blank = keep)", "password", "", type_="password", extra='autocomplete="new-password"'),
            ui.select("Role", "role", [("", "(keep)"), ("operator", "operator"), ("viewer", "viewer"), ("admin", "admin")], ""))
        body = '<h1>Users</h1><p class="sub">Roles: <b>viewer</b> downloads; <b>operator</b> also generates reports and edits tenants/settings; <b>admin</b> also manages users and databases.</p><div class="card">%s</div><div class="grid g2"><div class="card"><h2>Add a user</h2>%s</div><div class="card"><h2>Reset password or role</h2>%s</div></div>' % (
            ui.table(["User", "Role", "State", "Last sign-in", ""], rows), add, reset)
        return self.page(req, "Users", body, "users")

    def h_users_post(self, req: Request) -> Response:
        action, name = req.f("action"), req.f("username").strip()
        actor = req.user["username"]
        try:
            if action == "add":
                self.users.create(name, req.f("password"), req.f("role", "operator"))
                msg = "User %s created." % name
            elif action == "reset":
                if req.f("password"):
                    self.users.set_password(name, req.f("password"))
                    self.sessions.destroy_user(name)
                if req.f("role"):
                    self.users.set_role(name, req.f("role"))
                msg = "User %s updated." % name
            elif action in ("disable", "enable"):
                if name == actor:
                    raise ValueError("You cannot disable your own account.")
                self.users.set_disabled(name, action == "disable")
                if action == "disable":
                    self.sessions.destroy_user(name)
                msg = "User %s %sd." % (name, action)
            elif action == "delete":
                if name == actor:
                    raise ValueError("You cannot delete your own account.")
                self.users.delete(name)
                self.sessions.destroy_user(name)
                msg = "User %s deleted." % name
            else:
                raise ValueError("Unknown action.")
            self.data.audit(actor, "user." + action, name, req.ip)
            req.flash("ok", msg)
        except ValueError as e:
            req.flash("bad", str(e))
        return redirect("/users")

    def h_audit(self, req: Request) -> Response:
        rows = [[ui.esc(e["ts"]), ui.esc(e["actor"]), ui.esc(e["action"]), ui.esc(e["detail"]), ui.esc(e["ip"])] for e in self.data.read_audit(300)]
        body = '<h1>Audit log</h1><p class="sub">Last 300 events: sign-ins, configuration changes, report runs and downloads.</p><div class="card">%s</div>' % ui.table(
            ["Time", "Who", "What", "Details", "From"], rows, empty="Nothing logged yet.")
        return self.page(req, "Audit", body, "audit")


def _consteq(a: str, b: str) -> bool:
    import hmac
    return hmac.compare_digest(a.encode("utf-8"), b.encode("utf-8"))
