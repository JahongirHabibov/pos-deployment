# Copyright (c) 2026 Jahongir Habibov. All Rights Reserved.
# Proprietary and Confidential. Unauthorized use, copying, or distribution is strictly prohibited.

"""Entry point for the KASSIO diagnostics service."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys

from . import VERSION, config as config_module
from .server import DEFAULT_HOST, DEFAULT_PORT, Application, serve


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="kassio-diagnostics",
        description="Local diagnostics and repair service for KASSIO POS terminals.")
    parser.add_argument("--host", default=os.environ.get("KASSIO_DIAG_HOST",
                                                         DEFAULT_HOST))
    parser.add_argument("--port", type=int,
                        default=int(os.environ.get("KASSIO_DIAG_PORT", DEFAULT_PORT)))
    parser.add_argument("--config", default=os.environ.get("KASSIO_DIAG_CONFIG",
                                                           config_module.CONFIG_PATH))
    parser.add_argument("--deployment-dir",
                        default=os.environ.get("KASSIO_DIAG_DEPLOYMENT_DIR", ""))
    parser.add_argument("--helper", default=os.environ.get("KASSIO_DIAG_HELPER", ""))
    parser.add_argument("--log-level", default=os.environ.get("KASSIO_DIAG_LOG_LEVEL",
                                                              "INFO"))
    parser.add_argument("--version", action="version", version=VERSION)
    return parser


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, str(level).upper(), logging.INFO),
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,  # journald captures stderr
    )


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    configure_logging(args.log_level)
    log = logging.getLogger("kassio")

    if args.host not in ("127.0.0.1", "::1", "localhost"):
        # Refusing rather than warning: the entire access model assumes the
        # socket is unreachable from the network.
        log.error("refusing to bind to %s — this service is loopback only", args.host)
        return 2

    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    application = Application(
        web_dir=os.path.join(base, "web"),
        locale_dir=os.path.join(base, "locales"),
        helper_path=args.helper,
        config_path=args.config,
        deployment_dir=args.deployment_dir,
        host=args.host,
        port=args.port,
    )
    if application.failed_check_modules or application.failed_action_modules:
        log.warning("some modules failed to import: %s",
                    {**application.failed_check_modules,
                     **application.failed_action_modules})

    try:
        server = serve(application)
    except OSError as exc:
        log.error("could not bind to %s:%s — %s", args.host, args.port, exc)
        return 1

    def shutdown(signum, _frame):
        log.info("signal %s received, shutting down", signum)
        application.sessions.revoke_all()
        threading_shutdown(server)

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        server.serve_forever(poll_interval=0.5)
    finally:
        application.sessions.revoke_all()
        server.server_close()
    return 0


def threading_shutdown(server) -> None:
    import threading
    threading.Thread(target=server.shutdown, daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
