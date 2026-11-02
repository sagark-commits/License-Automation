"""A small threaded HTTP(S) server for the WSGI app (standard library only).

For an internal tool used by a handful of people this is sufficient; for TLS you can either pass a
certificate (--cert/--key) or put nginx in front (see deploy/nginx-license-ui.conf) and use --trust-proxy.

Safety properties: at most MAX_THREADS requests are served at once (extra connections get a 503 at once),
the TLS handshake runs inside the per-connection thread under a timeout (an idle TCP connection can never
block the accept loop), and no Server banner is sent.
"""
from __future__ import annotations

import logging
import logging.handlers
import signal
import socket
import ssl
import threading
from socketserver import ThreadingMixIn
from typing import Any, Optional
from wsgiref.simple_server import ServerHandler, WSGIRequestHandler, WSGIServer, make_server

log = logging.getLogger("lic_web")
MAX_THREADS = 32
HANDSHAKE_TIMEOUT = 10


class NoBannerHandler(ServerHandler):
    server_software = None                      # wsgiref sends "Server: WSGIServer/0.2 CPython/x" otherwise


class ThreadedWSGIServer(ThreadingMixIn, WSGIServer):
    daemon_threads = True
    request_queue_size = 64
    ssl_context: Optional[ssl.SSLContext] = None

    def __init__(self, *args: Any, max_threads: int = MAX_THREADS, **kwargs: Any):
        self._slots = threading.BoundedSemaphore(max_threads)
        super().__init__(*args, **kwargs)

    def process_request(self, request: socket.socket, client_address: Any) -> None:
        if not self._slots.acquire(blocking=False):
            try:
                request.sendall(b"HTTP/1.0 503 Service Unavailable\r\nConnection: close\r\nContent-Length: 0\r\n\r\n")
            except OSError:
                pass
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self._slots.release()
            raise

    def process_request_thread(self, request: socket.socket, client_address: Any) -> None:
        try:
            if self.ssl_context is not None:
                try:
                    request.settimeout(HANDSHAKE_TIMEOUT)
                    tls = self.ssl_context.wrap_socket(request, server_side=True, do_handshake_on_connect=False)
                    request = tls
                    request.do_handshake()
                    request.settimeout(None)
                except (ssl.SSLError, OSError):
                    self.shutdown_request(request)
                    return
            super().process_request_thread(request, client_address)
        finally:
            self._slots.release()


class QuietHandler(WSGIRequestHandler):
    timeout = 60                      # a stalled client cannot hold a thread forever

    def log_message(self, format: str, *args: Any) -> None:   # the app logs every request itself
        pass

    def handle(self) -> None:
        self.raw_requestline = self.rfile.readline(65537)
        if len(self.raw_requestline) > 65536:
            self.requestline = self.request_version = self.command = ""
            self.send_error(414)
            return
        if not self.parse_request():
            return
        handler = NoBannerHandler(self.rfile, self.wfile, self.get_stderr(), self.get_environ(), multithread=False)
        handler.request_handler = self
        handler.run(self.server.get_app())


def configure_logging(log_file: Optional[str], verbose: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    root.addHandler(console)
    if log_file:
        fh = logging.handlers.RotatingFileHandler(log_file, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)


def make_app_server(app: Any, host: str, port: int, certfile: Optional[str] = None, keyfile: Optional[str] = None,
                    max_threads: int = MAX_THREADS) -> ThreadedWSGIServer:
    server = make_server(host, port, app, server_class=lambda addr, handler: ThreadedWSGIServer(addr, handler, max_threads=max_threads),
                         handler_class=QuietHandler)
    if certfile:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.minimum_version = ssl.TLSVersion.TLSv1_2
        ctx.verify_mode = ssl.CERT_NONE          # no client certificates (some environments patch the default to REQUIRED)
        ctx.load_cert_chain(certfile, keyfile)
        server.ssl_context = ctx             # wrapped per connection, in the worker thread
        server.base_environ["HTTPS"] = "on"
    return server


def serve(app: Any, host: str, port: int, certfile: Optional[str] = None, keyfile: Optional[str] = None) -> None:
    server = make_app_server(app, host, port, certfile, keyfile)

    def _shutdown(signum: int, frame: Any) -> None:
        log.info("signal %s received - shutting down", signum)
        threading.Thread(target=server.shutdown, daemon=True).start()

    for sig in (signal.SIGINT, getattr(signal, "SIGTERM", None)):
        if sig is not None:
            try:
                signal.signal(sig, _shutdown)
            except ValueError:
                pass
    log.info("Serving on %s://%s:%d", "https" if certfile else "http", host, port)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        server.server_close()
        try:
            app.jobs.stop()
        except Exception:
            pass
