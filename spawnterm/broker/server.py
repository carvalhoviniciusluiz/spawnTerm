#!/usr/bin/env python3
"""asyncio unix-domain-socket server for the spawnTerm broker (#34).

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
from pathlib import Path
from typing import Optional

import dispatch
import protocol
import schema
import store  # noqa: F401 - registers the #36 registry/handoff ops on import


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
        self.logger = logger or logging.getLogger("spawnterm.broker")
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


def run(sock_path: str | Path, db_path: str | Path, logger: logging.Logger) -> None:
    """Blocking entry point: serve until interrupted (Ctrl-C)."""
    server = BrokerServer(sock_path, db_path, logger)
    try:
        asyncio.run(server.serve())
    except KeyboardInterrupt:
        logger.info("interrupted; shutting down")
