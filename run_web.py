#!/usr/bin/env python3
"""Start and administer the License Utilization web UI.

    python3.9 run_web.py init                 # first time: create the data directory and the first admin
    python3.9 run_web.py serve                # start the web server (default http://127.0.0.1:8088)
    python3.9 run_web.py serve --host 0.0.0.0 --port 8088
    python3.9 run_web.py add-user alice --role operator
    python3.9 run_web.py set-password alice
    python3.9 run_web.py check                # sanity-check the registry, databases and environment

No third-party packages are needed beyond the ones the report engine already uses.
"""
from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

if sys.version_info < (3, 8):
    sys.exit("ERROR: Python 3.8+ required (use python3.9 on the server)")

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))


def data_dir_from(args) -> Path:
    return Path(args.data_dir or os.environ.get("LIC_DATA_DIR") or REPO / "data").resolve()


def ask_password(prompt: str = "Password") -> str:
    env = os.environ.get("LIC_ADMIN_PASSWORD")
    if env:
        return env
    while True:
        a = getpass.getpass("%s: " % prompt)
        b = getpass.getpass("Repeat: ")
        if a == b:
            return a
        print("The two entries differ, try again.")


def cmd_init(args) -> int:
    from lic_web.auth import UserStore
    from lic_web.store import DataDir

    data = DataDir(data_dir_from(args), REPO)
    data.init()
    users = UserStore(data.users_path)
    print("Data directory: %s" % data.root)
    if users.count():
        print("Users already exist (%d). Nothing more to do." % users.count())
    else:
        name = args.admin or input("Administrator user name: ").strip()
        while True:
            try:
                users.create(name, ask_password("Password for %s" % name), "admin")
                break
            except ValueError as exc:
                print("  %s" % exc)
                if os.environ.get("LIC_ADMIN_PASSWORD"):
                    return 1
        data.audit("cli", "user.add", "%s (admin)" % name)
        print("Administrator '%s' created." % name)
    print("\nNext: python3.9 run_web.py serve --host 0.0.0.0 --port 8088\nThen open the page and set the database connections under Databases.")
    return 0


def cmd_add_user(args) -> int:
    from lic_web.auth import UserStore
    from lic_web.store import DataDir

    data = DataDir(data_dir_from(args), REPO)
    data.init()
    try:
        UserStore(data.users_path).create(args.username, ask_password("Password for %s" % args.username), args.role)
    except ValueError as exc:
        print("ERROR: %s" % exc)
        return 1
    data.audit("cli", "user.add", "%s (%s)" % (args.username, args.role))
    print("User %s created." % args.username)
    return 0


def cmd_set_password(args) -> int:
    from lic_web.auth import UserStore
    from lic_web.store import DataDir

    data = DataDir(data_dir_from(args), REPO)
    try:
        UserStore(data.users_path).set_password(args.username, ask_password("New password for %s" % args.username))
    except ValueError as exc:
        print("ERROR: %s" % exc)
        return 1
    data.audit("cli", "password.reset", args.username)
    print("Password changed.")
    return 0


def cmd_check(args) -> int:
    from lic_web.store import DataDir
    from lic_web.validation import validate_defaults, validate_tenant

    problems = 0
    print("Python %s" % sys.version.split()[0])
    for mod in ("pandas", "openpyxl", "yaml", "psycopg2"):
        try:
            __import__(mod)
            print("  ok   %s" % mod)
        except ImportError:
            print("  MISSING  %s  (run check_env.py / install the offline wheels)" % mod)
            problems += 1
    data = DataDir(data_dir_from(args), REPO)
    data.init()
    reg = data.load_registry()
    arcs = data.arcs()
    print("Data directory: %s" % data.root)
    errs = validate_defaults(reg["defaults"])
    for k, c in reg["tenants"].items():
        e, _w = validate_tenant(k, c, reg, is_new=False, arcs=arcs)
        errs += ["%s: %s" % (k, x) for x in e]
    print("Tenants: %d  | registry problems: %d" % (len(reg["tenants"]), len(errs)))
    for e in errs[:40]:
        print("  - %s" % e)
    print("Database settings: %s" % ("complete" if data.db_configured() else "INCOMPLETE - set them in the web UI (Databases)"))
    problems += len(errs) + (0 if data.db_configured() else 1)
    return 1 if problems else 0


def cmd_serve(args) -> int:
    from lic_web.server import configure_logging, serve
    from lic_web.wiring import create_app

    data_dir = data_dir_from(args)
    data_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(str(data_dir / "server.log"), args.verbose)
    if args.cert and not args.key:
        print("ERROR: --cert needs --key")
        return 1
    app = create_app(data_dir, REPO, trust_proxy=args.trust_proxy, secure_cookies=bool(args.cert) or args.secure_cookies)
    if app.users.count() == 0:
        print("WARNING: no users exist - run:  python3.9 run_web.py init")
    if args.host not in ("127.0.0.1", "localhost", "::1") and not args.cert and not args.trust_proxy:
        print("NOTE: listening on %s without TLS. Passwords travel in clear text on this network; "
              "use --cert/--key or an nginx reverse proxy (see deploy/)." % args.host)
    serve(app, args.host, args.port, args.cert, args.key)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="License Utilization web UI")
    ap.add_argument("--data-dir", help="where config, users and reports are stored (default ./data or $LIC_DATA_DIR)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("init", help="create the data directory and the first administrator")
    p.add_argument("--admin", help="administrator user name")
    p.set_defaults(fn=cmd_init)
    p = sub.add_parser("serve", help="start the web server")
    p.add_argument("--host", default=os.environ.get("LIC_HOST", "127.0.0.1"))
    p.add_argument("--port", type=int, default=int(os.environ.get("LIC_PORT", "8088")))
    p.add_argument("--cert", help="TLS certificate (PEM) - enables https")
    p.add_argument("--key", help="TLS private key (PEM)")
    p.add_argument("--trust-proxy", action="store_true", help="trust X-Forwarded-For/-Proto from a reverse proxy")
    p.add_argument("--secure-cookies", action="store_true", help="mark cookies Secure (use when TLS is terminated by a proxy)")
    p.add_argument("-v", "--verbose", action="store_true")
    p.set_defaults(fn=cmd_serve)
    p = sub.add_parser("add-user", help="create a user")
    p.add_argument("username")
    p.add_argument("--role", choices=("admin", "operator", "viewer"), default="operator")
    p.set_defaults(fn=cmd_add_user)
    p = sub.add_parser("set-password", help="reset a user's password")
    p.add_argument("username")
    p.set_defaults(fn=cmd_set_password)
    p = sub.add_parser("check", help="sanity-check environment, registry and database settings")
    p.set_defaults(fn=cmd_check)
    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
