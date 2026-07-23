#!/usr/bin/env python3
"""asyncio unix-domain-socket server for the it2agent broker (#34).

Thin I/O layer over the pure core. It opens the sqlite db (:mod:`schema`),
binds a unix socket, and for each connection reads newline-delimited JSON
requests (:mod:`protocol`), routes them through :func:`dispatch.handle`, and
writes back one response line each. All the real logic is pure and unit-tested
without a socket; this file just moves bytes.

The db connection is shared across connections: asyncio runs single-threaded, so
handler calls (small, synchronous sqlite queries) never overlap. WAL + the busy
timeout in :mod:`schema` cover the *multi-process* case (other clients, the #37
bridge) hitting the same file.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

import dispatch
import mailbox  # noqa: F401 - registers the #35 send/poll/fetch/ack ops on import
import protocol
import schema
import store  # noqa: F401 - registers the #36 registry/handoff ops on import


# The kernel's ``struct sockaddr_un.sun_path`` is a fixed-size buffer; a bind
# whose path (plus its trailing NUL) overflows it fails deep in libc with a
# terse ``OSError: AF_UNIX path too long``. The buffer is 104 bytes on Darwin/
# BSD and 108 on Linux (see <sys/un.h>). Hardcoded rather than probed: there is
# no portable runtime API for it, and these values are ABI-stable.
_SUN_PATH_MAX = 104 if sys.platform == "darwin" else 108


class SockPathTooLongError(Exception):
    """Raised at startup when the unix socket path won't fit in sun_path.

    Carries a ready-to-print, actionable message (the actual length vs. the
    platform limit, plus a shorter-path suggestion) so the entry point can
    surface a clean error instead of the raw ``OSError: AF_UNIX path too long``
    traceback that :func:`asyncio.start_unix_server` would otherwise emit.
    """


def _check_sock_path_length(sock_path: Path) -> None:
    """Fail fast if ``sock_path`` cannot fit in the platform's sun_path buffer.

    The kernel needs room for the path *and* a trailing NUL, so a path whose
    encoded length reaches the buffer size is already too long.
    """
    encoded = os.fsencode(str(sock_path))
    if len(encoded) >= _SUN_PATH_MAX:
        raise SockPathTooLongError(
            "unix socket path is too long: {length} bytes, but this platform's "
            "AF_UNIX limit is {limit} bytes (sun_path, including a trailing "
            "NUL).\n"
            "  path: {path}\n"
            "Use a shorter socket path — e.g. a short dir under /tmp:\n"
            "  --sock /tmp/it2a.$$/broker.sock   "
            "(or set IT2AGENT_BROKER_SOCK=/tmp/it2a.$$.sock)".format(
                length=len(encoded), limit=_SUN_PATH_MAX, path=sock_path
            )
        )


class BrokerServer:
    """Serve the broker protocol over a unix domain socket."""

    def __init__(
        self,
        sock_path: str | Path,
        db_path: str | Path,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        self.sock_path = Path(sock_path)
        self.db_path = Path(db_path)
        self.logger = logger or logging.getLogger("agent.broker")
        self._conn = None
        self._ctx: Optional[dispatch.BrokerContext] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._stop: Optional[asyncio.Event] = None

    # -- lifecycle --------------------------------------------------------- #

    def _prepare(self) -> None:
        """Open the db + build the dispatch context (idempotent schema)."""
        self._conn = schema.init_db(self.db_path)
        self._ctx = dispatch.BrokerContext(
            conn=self._conn,
            db_path=str(self.db_path),
            sock_path=str(self.sock_path),
        )

    def _unlink_socket(self) -> None:
        """Remove a stale socket file so bind() succeeds; ignore if absent."""
        try:
            self.sock_path.unlink()
        except FileNotFoundError:
            pass
        except OSError as exc:
            self.logger.warning("could not remove stale socket %s: %s", self.sock_path, exc)

    async def serve(self, ready: Optional["object"] = None) -> None:
        """Bind, then serve until :meth:`request_stop` (or cancellation).

        ``ready`` (any object with ``.set()``, e.g. a ``threading.Event``) is
        signaled once the socket is accepting connections — lets a caller/test
        proceed without polling or sleeping.
        """
        self._loop = asyncio.get_running_loop()
        self._stop = asyncio.Event()
        # Precheck before touching the filesystem: a too-long path would fail at
        # bind() with an opaque ``OSError: AF_UNIX path too long``; raise a clear,
        # actionable error instead (caught by :func:`run`).
        _check_sock_path_length(self.sock_path)
        self._prepare()
        self.sock_path.parent.mkdir(parents=True, exist_ok=True)
        self._unlink_socket()
        server = await asyncio.start_unix_server(
            self._handle_client, path=str(self.sock_path)
        )
        self.logger.info(
            "broker listening on %s (db %s, schema v%d)",
            self.sock_path,
            self.db_path,
            schema.current_version(self._conn),
        )
        if ready is not None:
            ready.set()
        try:
            async with server:
                await self._stop.wait()
        finally:
            self._cleanup()

    def request_stop(self) -> None:
        """Ask the serve loop to exit. Thread-safe (schedules on its loop)."""
        loop, stop = self._loop, self._stop
        if loop is not None and stop is not None:
            loop.call_soon_threadsafe(stop.set)

    def _cleanup(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            finally:
                self._conn = None
        self._unlink_socket()

    # -- per-connection loop ---------------------------------------------- #

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        peer = "unix"
        try:
            while True:
                raw = await reader.readline()
                if not raw:  # client closed
                    break
                response = self._process(raw)
                writer.write(protocol.encode(response))
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError):
            pass
        except Exception as exc:  # noqa: BLE001 - one bad client must not kill the server
            self.logger.warning("client %s error: %s", peer, exc)
        finally:
            try:
                writer.close()
            except Exception:  # noqa: BLE001
                pass

    def _process(self, raw: bytes) -> dict:
        """Decode one line and dispatch it; malformed input → structured error."""
        try:
            request = protocol.decode(raw)
        except protocol.ProtocolError as exc:
            return dispatch.error("bad_request", str(exc))
        return dispatch.handle(request, self._ctx)


def run(sock_path: str | Path, db_path: str | Path, logger: logging.Logger) -> int:
    """Blocking entry point: serve until interrupted (Ctrl-C).

    Returns a process exit code: 0 on a clean shutdown, nonzero when startup
    fails with a known, user-actionable condition (currently a too-long socket
    path — reported clearly to stderr rather than as a raw traceback).
    """
    server = BrokerServer(sock_path, db_path, logger)
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
    except SockPathTooLongError as exc:
        print(f"it2agent-broker: {exc}", file=sys.stderr)
        return 1
    return 0
