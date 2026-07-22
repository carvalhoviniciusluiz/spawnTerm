#!/usr/bin/env python3
"""Daemon↔broker bridge for it2agent (Tier 2.4, #37) — the glue that wires the
Tier 1 iTerm2 daemon (#26/#28) to the Tier 2 durable broker (#34/#35/#36).

This module is **iTerm2-free**: it never imports ``iterm2`` and performs no
iTerm2 I/O. The only I/O it does is talking to the in-repo broker over the
:class:`client.BrokerClient` (a synchronous unix-socket request/response), which
a unit test trivially replaces with a fake object. All the iTerm2 reads/writes
(``async_send_text`` / ``async_get_screen_contents``) are injected as two async
callables (``send_text`` / ``read_screen``) supplied by the thin
:mod:`adapter`; the bridge only decides *what* to send and *when* to ack.

Why a separate module (design of #37): the bridge is **glue**, not a new
subsystem. It does not reimplement iTerm2 transport (it reuses the Tier 1 daemon)
and it does not put durable state in iTerm2 (it reuses the broker). Its job is
three decisions, each kept pure and unit-tested:

  1. **Mode** — durable vs in-memory vs off, from two feature flags plus whether
     a broker client is present (:func:`select_mode`).
  2. **Envelope → broker op** — map an ingested agent envelope to a durable
     ``send`` op (:func:`build_send_op`), and a live :class:`registry.SessionRecord`
     to a ``register`` / ``touch`` op (:func:`build_register_op` /
     :func:`build_touch_op`).
  3. **Ack-by-observation** — after injecting a queued message into the target,
     decide whether it was observed on the target's screen (:func:`was_observed`)
     and therefore may be acked.

Two feature flags interact (see the daemon README for the full table):

  * ``agent.messaging`` gates routing/mailbox — the *whole* relay. OFF ⇒ the
    daemon still parses/logs envelopes (upstream) but neither routes nor enqueues.
  * ``agent.broker`` gates the *durable* path. The durable mailbox+registry
    is used only when **both** flags are ON *and* a broker client is reachable.
    With messaging ON but the broker OFF/unreachable the daemon degrades to the
    #28 in-memory best-effort router. So: durable needs messaging **and** broker;
    messaging-only falls back to in-memory; messaging-off is a no-op.

Graceful degradation: every broker call is wrapped so that a down/unreachable
broker (``OSError``/``ProtocolError``/anything) never crashes the daemon — the
ingest path falls back to the in-memory router for that message and logs that it
is degraded, and delivery/registry-population simply skip that round.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

# Pure reuse from the #28 router: the routing resolution (id-before-role
# precedence, self-guard, fan-out) and the body→text coercion. Reusing these
# keeps the in-memory fallback byte-for-byte identical to #28 and avoids
# duplicating the body-serialization logic.
from envelope import Envelope
from router import _body_text, messaging_enabled, route

# Feature-flag keys. ``messaging`` is read via router.messaging_enabled (reused,
# not re-implemented); ``broker`` is read here with the same #11 helper.
BROKER_FLAG = "agent.broker"

# Where the in-repo broker package (client.py etc.) lives, relative to us:
# it2agent/broker. Added to sys.path lazily by connect_broker so the pure path
# never needs it.
_BROKER_DIR = Path(__file__).resolve().parent.parent / "broker"
_FLAGS_DIR = Path(__file__).resolve().parent.parent / "flags"

# Delivery modes.
MODE_OFF = "off"
MODE_IN_MEMORY = "in_memory"
MODE_DURABLE = "durable"

# Injected-text marker for a durably-delivered message. It embeds the broker
# message id so the marker is unique per message; the ack-by-observation
# predicate looks for exactly this substring on the target's screen.
def marker_for(msg_id: Any) -> str:
    """Return the unique on-screen marker for broker message ``msg_id``."""
    return f"[it2agent#{msg_id}]"


def broker_enabled() -> bool:
    """Return True iff the ``agent.broker`` flag is ON.

    Mirrors :func:`router.messaging_enabled`: import the #11 helper and ask it.
    Fail-safe — if the helper is unreachable the flag reads OFF (capabilities
    default off). No ``iterm2`` involvement, so this module stays pure.
    """
    try:
        if str(_FLAGS_DIR) not in sys.path:
            sys.path.insert(0, str(_FLAGS_DIR))
        import it2agent_flag  # type: ignore

        return it2agent_flag.is_enabled(BROKER_FLAG)
    except Exception:  # noqa: BLE001 - unreachable helper => flag OFF
        return False


def select_mode(
    *, messaging_enabled: bool, broker_enabled: bool, has_broker_client: bool
) -> str:
    """Pick the delivery mode from the two flags + client presence (pure).

    * messaging OFF                              → :data:`MODE_OFF`
    * messaging ON, broker ON, client present    → :data:`MODE_DURABLE`
    * messaging ON, otherwise                     → :data:`MODE_IN_MEMORY`

    Reachability is *not* decided here — a present client may still fail at call
    time; the caller wraps each durable op and degrades to in-memory on failure.
    """
    if not messaging_enabled:
        return MODE_OFF
    if broker_enabled and has_broker_client:
        return MODE_DURABLE
    return MODE_IN_MEMORY


def build_send_op(envelope) -> tuple[Optional[dict], str]:
    """Map an ingested envelope to a broker ``send`` op, or explain why not.

    Returns ``(op_dict, "")`` when the envelope is enqueueable, else
    ``(None, reason)`` with the same reasons the #28 router would reject on
    (``no destination`` / ``empty body``) so durable and in-memory agree. The
    body is coerced to a string (objects → compact JSON) because the mailbox
    stores a string body.
    """
    to = (getattr(envelope, "to", None) or "").strip()
    if not to:
        return None, "no destination"
    body_text = _body_text(getattr(envelope, "body", None))
    if not body_text.strip():
        return None, "empty body"
    sender = (getattr(envelope, "sender", None) or "").strip() or "unknown"
    return {"op": "send", "to": to, "from": sender, "body": body_text}, ""


def synthetic_envelope(message: dict) -> Envelope:
    """Rebuild an :class:`envelope.Envelope` from a polled broker message dict.

    Lets the durable delivery path reuse the exact #28 :func:`router.route`
    resolution (id-before-role, fan-out, self-guard) rather than re-deriving it.
    """
    return Envelope(
        v=1,
        type="msg",
        to=message.get("to"),
        sender=message.get("from"),
        body=message.get("body"),
        raw="",
    )


def format_durable_delivery(
    msg_id: Any, sender: Optional[str], body_text: str
) -> tuple[str, str]:
    """Build the exact text to inject for a durable message + its screen marker.

    The line carries the unique ``[it2agent#<id>]`` marker (so ack-by-observation
    can find it) plus the sender, mirroring the #28 relay's human-readable shape.
    Returns ``(text, marker)``.
    """
    marker = marker_for(msg_id)
    who = (sender or "").strip() or "unknown"
    return f"{marker} message from {who}: {body_text}\n", marker


def was_observed(screen_text: Optional[str], marker: str) -> bool:
    """Ack-by-observation predicate (pure): did the injected message land?

    Heuristic: after injecting the delivery line we read the target session's
    visible screen contents; the message counts as *observed* iff its unique
    ``marker`` (``[it2agent#<id>]``) appears somewhere in that text — i.e. the
    injected bytes reached the session and were echoed by the running program.
    Only an observed message is acked; an un-observed one is left un-acked in the
    durable mailbox and replayed on the next poll (at-least-once until observed).
    """
    if not marker or not screen_text:
        return False
    return marker in screen_text


def recipient_keys(records) -> list[str]:
    """Distinct mailbox recipient keys addressable by the live sessions (pure).

    A message is stored in the mailbox under the raw ``to`` string the sender
    used — which is either an ``agent_id`` or an ``agent_role`` (#28 precedence).
    So the set of keys worth polling is the union of every live session's
    non-empty ``agent_id`` and ``agent_role``, de-duplicated and order-stable.
    """
    keys: list[str] = []
    seen: set[str] = set()
    for record in records:
        for value in (getattr(record, "agent_id", ""), getattr(record, "agent_role", "")):
            value = (value or "").strip()
            if value and value not in seen:
                seen.add(value)
                keys.append(value)
    return keys


def build_register_op(record) -> dict:
    """Map a live :class:`registry.SessionRecord` to a broker ``register`` op.

    Keyed by ``session_id`` (an upsert on the broker side). Role/task are
    included only when present; ``alive`` reflects the session being live.
    """
    op: dict[str, Any] = {"op": "register", "session_id": record.session_id, "alive": True}
    if getattr(record, "agent_role", ""):
        op["role"] = record.agent_role
    if getattr(record, "agent_task", ""):
        op["task"] = record.agent_task
    return op


def build_touch_op(session_id: str, *, alive: bool = True) -> dict:
    """Map a liveness change to a broker ``touch`` op (refreshes last_seen)."""
    return {"op": "touch", "session_id": session_id, "alive": alive}


# Signatures of the two injected iTerm2 I/O callables the adapter supplies.
SendText = Callable[[str, str], Awaitable[None]]
ReadScreen = Callable[[str], Awaitable[str]]


@dataclass
class IngestOutcome:
    """What :meth:`Bridge.handle_ingest` did with one envelope (for logs/tests)."""

    mode: str
    action: str  # "durable_send" | "delivered" | "dropped"
    reason: str = ""
    msg_id: Any = None
    targets: tuple[str, ...] = ()
    degraded: bool = False


@dataclass
class DeliveryOutcome:
    """Result of one :meth:`Bridge.deliver_once` sweep (for logs/tests)."""

    polled: int = 0
    delivered: int = 0
    acked: int = 0
    degraded: bool = False
    keys: tuple[str, ...] = field(default_factory=tuple)


class Bridge:
    """Orchestrates ingest / delivery / registry-population over the broker.

    iTerm2-free: the broker client is injected (a real
    :class:`client.BrokerClient` in production, a fake in tests) and the two
    iTerm2 reads/writes are injected as async callables. The three feature-flag
    reads are injected as zero-arg callables (defaulting to the module readers)
    so a test can pin the mode without touching config files.
    """

    def __init__(
        self,
        broker_client: Any,
        registry: Any,
        *,
        send_text: SendText,
        read_screen: ReadScreen,
        logger: Any = None,
        messaging_enabled: Callable[[], bool] = messaging_enabled,
        broker_enabled: Callable[[], bool] = broker_enabled,
    ) -> None:
        self.broker = broker_client
        self.registry = registry
        self._send_text = send_text
        self._read_screen = read_screen
        self._messaging_enabled = messaging_enabled
        self._broker_enabled = broker_enabled
        import logging

        self.log = logger or logging.getLogger("agent.daemon")

    # -- mode -------------------------------------------------------------

    def mode(self) -> str:
        """Current delivery mode from the live flags + client presence."""
        return select_mode(
            messaging_enabled=self._messaging_enabled(),
            broker_enabled=self._broker_enabled(),
            has_broker_client=self.broker is not None,
        )

    def _broker_request(self, op: dict) -> Optional[dict]:
        """Do one broker request, returning ``None`` (and logging degraded) on
        any failure so a down broker never crashes the daemon."""
        try:
            return self.broker.request(op)
        except Exception as exc:  # noqa: BLE001 - broker down => degrade, never crash
            self.log.warning("bridge: broker request %r failed (degraded): %s", op.get("op"), exc)
            return None

    # -- ingest (agent → daemon) -----------------------------------------

    async def handle_ingest(self, envelope) -> IngestOutcome:
        """Route one ingested envelope, durable when possible, else in-memory.

        Durable: enqueue via broker ``send`` (delivery happens later by polling).
        In-memory: resolve + inject immediately via the #28 router (best-effort).
        Falls back from durable to in-memory if the broker call fails.
        """
        mode = self.mode()
        if mode == MODE_OFF:
            return IngestOutcome(mode=mode, action="dropped", reason="messaging disabled")

        if mode == MODE_DURABLE:
            op, reason = build_send_op(envelope)
            if op is None:
                return IngestOutcome(mode=mode, action="dropped", reason=reason)
            resp = self._broker_request(op)
            if resp is not None and resp.get("ok"):
                return IngestOutcome(mode=mode, action="durable_send", msg_id=resp.get("id"))
            # Broker unreachable / errored → degrade to the in-memory relay.
            return await self._deliver_in_memory(envelope, degraded=True)

        return await self._deliver_in_memory(envelope, degraded=False)

    async def _deliver_in_memory(self, envelope, *, degraded: bool) -> IngestOutcome:
        """#28 best-effort relay: resolve against the live registry and inject."""
        decision = route(envelope, self.registry)
        if not decision.deliverable:
            return IngestOutcome(
                mode=MODE_IN_MEMORY, action="dropped", reason=decision.reason, degraded=degraded
            )
        for session_id in decision.target_session_ids:
            await self._send_text(session_id, decision.text)
        return IngestOutcome(
            mode=MODE_IN_MEMORY,
            action="delivered",
            targets=decision.target_session_ids,
            degraded=degraded,
        )

    # -- delivery (broker mailbox → live sessions) -----------------------

    async def deliver_once(self) -> DeliveryOutcome:
        """One poll→inject→observe→ack sweep for every live recipient key.

        No-op unless in durable mode. For each recipient key addressable by a
        live session, poll the mailbox, inject each un-acked message into its
        resolved target session(s), read the target screen(s), and ack the
        message iff the marker was observed. Un-observed messages are left in the
        mailbox for replay. Never raises — a broker failure degrades the sweep.
        """
        if self.mode() != MODE_DURABLE:
            return DeliveryOutcome()

        keys = tuple(recipient_keys(self.registry.all()))
        outcome = DeliveryOutcome(keys=keys)
        for key in keys:
            resp = self._broker_request({"op": "poll", "agent": key})
            if resp is None:
                outcome.degraded = True
                return outcome
            for message in resp.get("messages") or []:
                outcome.polled += 1
                await self._deliver_message(key, message, outcome)
        return outcome

    async def _deliver_message(self, key: str, message: dict, outcome: DeliveryOutcome) -> None:
        """Inject one polled message, observe, and ack iff observed."""
        decision = route(synthetic_envelope(message), self.registry)
        if not decision.deliverable:
            # No live target right now (e.g. gone, or self-send) — leave it
            # durable for a later poll rather than acking it away.
            return
        msg_id = message.get("id")
        text, marker = format_durable_delivery(msg_id, message.get("from"), message.get("body"))
        observed = False
        for session_id in decision.target_session_ids:
            await self._send_text(session_id, text)
            outcome.delivered += 1
            screen = await self._read_screen(session_id)
            if was_observed(screen, marker):
                observed = True
        if observed:
            ack = self._broker_request({"op": "ack", "agent": key, "msg_id": msg_id})
            if ack is not None:
                outcome.acked += 1

    # -- registry population (daemon events → broker registry) -----------

    def note_session(self, record) -> bool:
        """Upsert a live session into the durable broker registry (#36).

        Gated on the broker flag + a present client (the durable registry is a
        broker concern, independent of the messaging relay). Returns True iff the
        broker acknowledged; never raises.
        """
        if not (self._broker_enabled() and self.broker is not None):
            return False
        resp = self._broker_request(build_register_op(record))
        return bool(resp and resp.get("ok"))

    def note_terminated(self, session_id: str) -> bool:
        """Mark a terminated session not-alive in the durable broker registry."""
        if not (self._broker_enabled() and self.broker is not None):
            return False
        resp = self._broker_request(build_touch_op(session_id, alive=False))
        return bool(resp and resp.get("ok"))


def connect_broker(logger: Any = None) -> Any:
    """Best-effort connect to the in-repo broker; return a client or ``None``.

    Adds the ``it2agent/broker`` dir to ``sys.path`` (lazily — the pure path
    never needs it), constructs a :class:`client.BrokerClient`, and pings it to
    confirm the server is actually up. Any failure (package missing, socket
    down) returns ``None`` so the daemon runs in-memory-only. Never raises. This
    is the only function here that does socket I/O; unit tests inject a fake
    client into :class:`Bridge` instead of calling it.
    """
    import logging

    log = logger or logging.getLogger("agent.daemon")
    try:
        if str(_BROKER_DIR) not in sys.path:
            sys.path.insert(0, str(_BROKER_DIR))
        from client import BrokerClient  # type: ignore

        client = BrokerClient()
        reply = client.ping()
        if not (isinstance(reply, dict) and reply.get("ok")):
            log.info("bridge: broker ping returned %r; running in-memory only", reply)
            return None
        log.info("bridge: connected to broker at %s (durable path available)", client.sock_path)
        return client
    except Exception as exc:  # noqa: BLE001 - broker absent/down => in-memory only
        log.info("bridge: broker not reachable (%s); running in-memory only", exc)
        return None
