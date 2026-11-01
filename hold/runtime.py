"""Le runtime d'habilitation : ce que l'agent porte.

    hab = Habilitation("profiles/reservation.hold.yaml", store="hold.db", relay=..., discerner=...)

    @hab.effect("create_booking")
    def create_booking(hotel_id, ..., operation_id): ...

    mission = hab.open_mission("Déplacement à Lyon")
    result = mission.call("create_booking", hotel_id="...", ...)   # ou mission.tools() pour un LLM

Chaque appel passe par le plancher. Un effet au résultat inconnu ouvre une hésitation :
le discerneur propose un chemin, le plancher l'admet ou applique la référence, et la mission
reprend seule dès qu'une preuve corrélée arrive. L'autorité n'est sollicitée que si la
cohérence ne peut pas être rétablie dans l'habilitation.
"""

from __future__ import annotations

import inspect
import json
import secrets
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from .canonical import canonical_json, digest
from .discern import Discerner, Proposal, ReferenceDiscerner
from .floor import ActionContext, admit_action, admit_proposal, effect_key, envelope_violations, grant_problem, parse_time
from .hesitation import boundary_position
from .i18n import t
from .journal import verify_chain
from .outcome import Outcome, ToolResult
from .profile import Power, Profile, load_profile
from .relay.base import TITLES, Relay, new_question_id
from .store import Store, now_iso

UNKNOWN_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)
CLOSED = {"CLOSED_COMPLETED", "CLOSED_UNCONFIRMED", "CLOSED_FAILED", "REVOKED"}
REQUEST_END = {"QUESTION_EXPIRED": "EXPIRED", "PROFILE_CHANGED": "SUPERSEDED", "CONDITIONS_CHANGED": "MOOT"}


class ProfileMismatch(RuntimeError):
    """The mission was opened under another profile revision; adopt it explicitly or use that profile."""


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


@dataclass
class _Tool:
    action: str
    kind: str
    fn: Callable[..., Any]
    description: str
    parameters: dict[str, Any]
    idempotency: str = "none"  # "operation_id": the provider honors the operation_id as an idempotency key (declared, not verified)
    verified_by: str | None = None


# What HOLD can and cannot claim about an external effect. Three separate guarantees, never "exactly once".
GUARANTEES = {
    "dispatch": "At-most-once dispatch inside the HOLD boundary: every runtime sharing one HOLD store (threads, processes on one host). Not across separate stores or hosts.",
    "provider": "Provider-side duplicate prevention only where the provider honors the operation_id as an idempotency key: declared by the runtime binding, not verified by HOLD unless a verification is cited.",
    "outcome": "An effect is CONFIRMED only with evidence correlated to its operation_id, FAILED_PROVEN only on an explicit refusal before creation, and otherwise explicitly UNKNOWN.",
}


def effect_guarantees(effect: dict[str, Any], tool: "_Tool | None") -> dict[str, Any]:
    outcome = {"CONFIRMED": "CONFIRMED_CORRELATED", "FAILED_PROVEN": "PROVEN_NOT_EXECUTED"}.get(effect["status"], "UNKNOWN")
    provider = "NOT_DECLARED"
    if tool and tool.idempotency == "operation_id":
        provider = "VERIFIED" if tool.verified_by else "DECLARED"
    return {"dispatch": "AT_MOST_ONCE_IN_BOUNDARY", "dispatches": effect["attempts"], "provider": provider, "outcome": outcome}


def _schema_from_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    required: list[str] = []
    for name, p in inspect.signature(fn).parameters.items():
        if name in ("operation_id", "self") or p.kind in (p.VAR_KEYWORD, p.VAR_POSITIONAL):
            continue
        t = {int: "integer", float: "number", bool: "boolean"}.get(p.annotation, "string")
        props[name] = {"type": t}
        if p.default is inspect.Parameter.empty:
            required.append(name)
    return {"type": "object", "properties": props, "required": required}


class Habilitation:
    def __init__(
        self,
        profile: Profile | str | Path | dict,
        *,
        store: Store | str = "hold.db",
        discerner: Discerner | None = None,
        relay: Relay | None = None,
        secret: str | None = None,
        require_signature: bool = False,
        background: bool = True,
        clock: Callable[[], datetime] | None = None,
    ):
        self.profile = profile if isinstance(profile, Profile) else load_profile(profile, secret=secret, require_signature=require_signature)
        self.store = store if isinstance(store, Store) else Store(store)
        self.discerner: Discerner = discerner or ReferenceDiscerner()
        self.relay = relay
        self.background = background
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(timezone.utc))
        self.tools: dict[str, _Tool] = {}
        self._loops: dict[str, threading.Lock] = {}
        self._threads: list[threading.Thread] = []

    # -- enregistrement des outils (le profil gouverne) --------------------------------------
    def _register(self, kind: str, action: str, description: str | None, parameters: dict | None, idempotency: str = "none", verified_by: str | None = None):
        if idempotency not in ("none", "operation_id"):
            raise ValueError("idempotency must be \"none\" or \"operation_id\"")

        def deco(fn: Callable[..., Any]):
            power = self.profile.power(action)
            if not power:
                raise ValueError(f"No power \"{action}\" in profile {self.profile.id}")
            if power.kind != kind:
                raise ValueError(f"\"{action}\" is declared \"{power.kind}\" in the profile, not \"{kind}\"")
            self.tools[action] = _Tool(action, kind, fn, description or power.description or (fn.__doc__ or "").strip(), parameters or _schema_from_signature(fn), idempotency, verified_by)
            return fn

        return deco

    def read(self, action: str, *, description: str | None = None, parameters: dict | None = None):
        return self._register("read", action, description, parameters)

    def effect(self, action: str, *, description: str | None = None, parameters: dict | None = None, idempotency: str = "none", verified_by: str | None = None):
        """`idempotency="operation_id"`: you declare that your provider deduplicates on the operation_id
        HOLD passes. HOLD records the declaration; `verified_by` cites how it was verified (a test, a contract)."""
        return self._register("effect", action, description, parameters, idempotency, verified_by)

    def status(self, action: str):
        return self._register("status", action, None, {"type": "object", "properties": {}})

    def contact(self, action: str, *, description: str | None = None):
        return self._register("contact", action, description, {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]})

    def missing_tools(self) -> list[str]:
        return [p.action for p in self.profile.powers if p.action not in self.tools]

    # -- missions -----------------------------------------------------------------------------
    def binding(self, agent: dict[str, Any] | None = None) -> dict[str, Any]:
        """The runtime binding: what this process attaches to the profile. Journaled, never part of the profile."""
        from .journal import CORE_VERSION

        describe = getattr(self.discerner, "describe", None)
        return {
            "core": CORE_VERSION,
            "agent": agent,
            "discerner": describe() if describe else {"name": getattr(self.discerner, "name", type(self.discerner).__name__)},
            "relay": {"channel": self.relay.channel, "mode": getattr(self.relay, "mode", None)} if self.relay else None,
            "authority_reachable": bool(self.relay and self.relay.available(self.profile.authority)),
            "tools": [{"action": t.action, "kind": t.kind, "idempotency": t.idempotency, "verified_by": t.verified_by} for t in self.tools.values()],
            "missing": self.missing_tools(),
        }

    def open_mission(self, title: str, mission_id: str | None = None, *, agent: dict[str, Any] | None = None) -> "Mission":
        mid = mission_id or f"m_{secrets.token_hex(6)}"
        body = canonical_json(self.profile.model_dump(mode="json", exclude_none=True))
        with self.store.tx() as c:
            self.store.create_mission(c, mid, title, self.profile.fingerprint, self.profile.revision, body)
            self.store.append(c, mid, "MISSION_OPENED", "AGENT", {"title": title})
            self.store.append(c, mid, "PROFILE_ATTACHED", "CORE", {
                "fingerprint": self.profile.fingerprint, "revision": self.profile.revision, "profile": self.profile.id,
                "signed": bool(self.profile.signature), "authority": self.profile.authority.name,
            })
            self.store.append(c, mid, "RUNTIME_BOUND", "CORE", self.binding(agent))
        return Mission(self, mid)

    def mission(self, mission_id: str, *, adopt: bool = False) -> "Mission":
        """A mission keeps the profile revision it was opened with. `adopt=True` attaches this
        habilitation's revision instead: the change is journaled, and every open question or unused
        approval given under the previous revision stops being valid."""
        row = self.store.mission(mission_id)
        if not row:
            raise KeyError(f"Unknown mission: {mission_id}")
        if row["profile_fp"] != self.profile.fingerprint:
            if not adopt:
                raise ProfileMismatch(f"Mission {mission_id} wears profile {row['profile_fp'][:12]} (revision {row['profile_rev']}), not {self.profile.fingerprint[:12]}")
            body = canonical_json(self.profile.model_dump(mode="json", exclude_none=True))
            with self.store.tx() as c:
                self.store.attach_profile(c, mission_id, self.profile.fingerprint, self.profile.revision, body)
                self.store.append(c, mission_id, "PROFILE_ATTACHED", "CORE", {
                    "fingerprint": self.profile.fingerprint, "revision": self.profile.revision, "profile": self.profile.id,
                    "signed": bool(self.profile.signature), "authority": self.profile.authority.name, "supersedes": row["profile_fp"],
                })
                self.store.append(c, mission_id, "RUNTIME_BOUND", "CORE", self.binding())
        return Mission(self, mission_id)

    def serve(self, *, host: str = "127.0.0.1", port: int = 8787, background: bool = True):
        """Webhook WhatsApp + HOLD Studio dans le processus de l'agent (nécessite l'extra « server »)."""
        from .server import serve

        return serve(self.store, [self], host=host, port=port, background=background)

    def join(self, timeout: float = 30.0) -> None:
        """Attend la fin des continuations lancées en arrière-plan (tests, démonstrations)."""
        deadline = time.monotonic() + timeout
        for t in list(self._threads):
            t.join(max(0.0, deadline - time.monotonic()))

    def _spawn(self, fn: Callable[[], Any]) -> None:
        if not self.background:
            fn()
            return
        t = threading.Thread(target=fn, daemon=True)
        self._threads.append(t)
        t.start()

    # -- réponse de l'autorité : même porte pour WhatsApp et la console ------------------------
    def answer(self, mission_id: str, question_id: str, code: str, *, channel: str, evidence: dict[str, Any] | None = None) -> dict[str, Any]:
        return self.mission(mission_id).answer(question_id, code, channel=channel, evidence=evidence or {})


class Mission:
    def __init__(self, hab: Habilitation, mission_id: str):
        self.hab = hab
        self.id = mission_id
        self.store = hab.store
        self.profile = hab.profile

    # -- état -----------------------------------------------------------------------------------
    @property
    def row(self):
        return self.store.mission(self.id)

    @property
    def status(self) -> str:
        return self.row["status"]

    @property
    def title(self) -> str:
        return self.row["title"]

    def effects(self) -> list[dict[str, Any]]:
        out = []
        for r in self.store.effects(self.id):
            e = dict(r) | {"attributes": json.loads(r["attributes"])}
            out.append(e | {"guarantees": effect_guarantees(e, self.hab.tools.get(e["action"]))})
        return out

    def open_request(self) -> dict[str, Any] | None:
        rows = self.store.read("SELECT * FROM requests WHERE mission=? AND status='OPEN' ORDER BY created_at DESC LIMIT 1", (self.id,))
        return json.loads(rows[0]["payload"]) | {"status": "OPEN"} if rows else None

    def events(self):
        return self.store.events(self.id)

    def verify(self):
        return verify_chain(self.events())

    def _ctx(self) -> ActionContext:
        row = self.row
        effs = self.store.effects(self.id)
        return ActionContext(
            revoked=bool(row["revoked"]),
            expired=self.profile.expired(),
            usage=self.store.usage(self.id),
            confirmed_actions={e["action"] for e in effs if e["status"] == "CONFIRMED"},
            uncertain_actions={e["action"] for e in effs if e["status"] in ("UNKNOWN", "DISPATCHING")},
            now=self.hab.clock(),
        )

    def _authority_available(self) -> bool:
        return bool(self.hab.relay and self.hab.relay.available(self.profile.authority))

    # -- outils pour un modèle (appels d'outils Llama / format OpenAI) ---------------------------
    def tools(self) -> list[dict[str, Any]]:
        return [
            {"type": "function", "function": {"name": t.action, "description": t.description, "parameters": t.parameters}}
            for t in self.hab.tools.values()
            if t.kind in ("read", "effect")
        ]

    def dispatch(self, name: str, arguments: dict[str, Any] | str | None) -> ToolResult:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
        return self.call(name, **args)

    # -- appel d'une action ------------------------------------------------------------------------
    def call(self, action: str, **attrs: Any) -> ToolResult:
        grant_id = attrs.pop("_grant", None)
        self.sweep()
        if self.status in CLOSED:
            return ToolResult("CLOSED" if self.status != "REVOKED" else "REVOKED", t("closed", status=self.status), rule="I4")
        tool = self.hab.tools.get(action)
        power = self.profile.power(action)
        grant = self._grant(grant_id) if grant_id else None
        ctx = self._ctx()
        if power and power.kind == "effect" and not ctx.revoked and not ctx.expired:
            # I1 avant le budget : un effet équivalent déjà confirmé ou en cours se signale comme tel.
            existing = self.store.effect(effect_key(self.id, action, power.identity, attrs))
            if existing and existing["status"] in ("CONFIRMED", "UNKNOWN", "DISPATCHING"):
                return self._equivalent(power, existing)
        with self.store.tx() as c:
            v = admit_action(self.profile, action, attrs, ctx, grant=grant)
            if v.admitted and tool is None:
                v = type(v)(False, "NO_IMPLEMENTATION", f"No implementation registered for \"{action}\".")
            if not v.admitted:
                breach = v.rule == "I4/ENVELOPE" and self.profile.hesitation.envelope_breach == "ask_authority" and self._authority_available() and not self.open_request()
                self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": action, "rule": v.rule, "reason": v.reason, "violations": v.violations})
                if not breach:
                    return ToolResult("REFUSED", v.reason, rule=v.rule)
                request = self._open_request(c, "ENVELOPE_BREACH", action=action, attrs=attrs, violations=v.violations)
            else:
                request = None
        if request:
            self._send_request(request)
            return ToolResult("HELD", t("envelope_held", reason=v.reason.rstrip("."), authority=self.profile.authority.name), rule="I4/ENVELOPE", details={"question_id": request["question_id"]})
        if power.kind == "effect":
            return self._effect(tool, power, attrs, grant if v.rule == "ADMIT/APPROVED" else None)
        return self._simple(tool, power, attrs)

    def _simple(self, tool: _Tool, power: Power, attrs: dict[str, Any]) -> ToolResult:
        with self.store.tx() as c:
            if power.budget is not None:
                self.store.bump(c, self.id, f"power:{power.action}")
            admitted = self.store.append(c, self.id, "ACTION_ADMITTED", "CORE", {"action": power.action, "kind": power.kind})
        try:
            out = tool.fn(**attrs)
        except Exception as e:  # noqa: BLE001
            with self.store.tx() as c:
                self.store.append(c, self.id, "ACTION_RESULT", "PROVIDER", {"action": power.action, "status": "ERROR", "error": f"{type(e).__name__}: {e}"}, cause=admitted.seq)
            return ToolResult("FAILED", t("failed", action=power.action, error=e))
        data = out.data if isinstance(out, Outcome) else out
        with self.store.tx() as c:
            self.store.append(c, self.id, "ACTION_RESULT", "PROVIDER", {"action": power.action, "status": "OK", "data_sha": digest(data) if data is not None else None}, cause=admitted.seq)
        return ToolResult("OK", t("done", action=power.action), data=data)

    def _grant(self, grant_id: str) -> dict[str, Any] | None:
        rows = self.store.read("SELECT * FROM grants WHERE id=? AND mission=?", (grant_id, self.id))
        return json.loads(rows[0]["payload"]) | {"id": grant_id, "consumed": bool(rows[0]["consumed"])} if rows else None

    def _effect(self, tool: _Tool, power: Power, attrs: dict[str, Any], grant: dict[str, Any] | None) -> ToolResult:
        key = effect_key(self.id, power.action, power.identity, attrs)
        op = f"op_{secrets.token_hex(6)}"
        with self.store.tx() as c:
            existing = c.execute("SELECT * FROM effects WHERE effect_key=?", (key,)).fetchone()
            if existing and existing["status"] in ("CONFIRMED", "UNKNOWN", "DISPATCHING"):
                return self._equivalent(power, existing)  # course perdue face à un appel concurrent
            if grant and c.execute("UPDATE grants SET consumed=1 WHERE id=? AND consumed=0", (grant["id"],)).rowcount != 1:
                self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": power.action, "rule": "I4/ENVELOPE", "reason": "Outside the profile's envelope (the authority's approval does not apply: approval already used)"})
                return ToolResult("REFUSED", t("grant_used", action=power.action), rule="I4/ENVELOPE")
            if existing:  # FAILED_PROVEN : un refus prouvé n'a rien créé, un nouvel essai est admissible
                c.execute("UPDATE effects SET operation_id=?, status='DISPATCHING', receipt=NULL, attempts=attempts+1, not_found=0, updated_at=? WHERE effect_key=?", (op, now_iso(), key))
            else:
                c.execute(
                    "INSERT INTO effects(effect_key, mission, action, operation_id, status, attributes, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (key, self.id, power.action, op, "DISPATCHING", json.dumps(attrs, ensure_ascii=False, default=str), now_iso(), now_iso()),
                )
            if power.budget is not None:
                self.store.bump(c, self.id, f"power:{power.action}")
            adm = self.store.append(c, self.id, "ACTION_ADMITTED", "CORE", {"action": power.action, "kind": "effect", "grant": grant["id"] if grant else None})
            res = self.store.append(c, self.id, "EFFECT_RESERVED", "CORE", {"action": power.action, "effect_key": key, "operation_id": op, "attributes": attrs}, cause=adm.seq)
        try:
            out = tool.fn(**attrs, operation_id=op)
            if not isinstance(out, Outcome):
                out = Outcome.unknown("réponse non typée de l'outil")
        except UNKNOWN_EXCEPTIONS as e:
            out = Outcome.unknown(f"{type(e).__name__}: {e}" if str(e) else type(e).__name__)
        except Exception as e:  # noqa: BLE001 — une erreur après envoi ne prouve rien
            out = Outcome.unknown(f"{type(e).__name__}: {e}")
        return self._record_effect(power, key, op, out, cause=res.seq)

    def _record_effect(self, power: Power, key: str, op: str, out: Outcome, *, cause: int) -> ToolResult:
        with self.store.tx() as c:
            if out.status == "CONFIRMED":
                c.execute("UPDATE effects SET status='CONFIRMED', receipt=?, updated_at=? WHERE effect_key=?", (out.receipt, now_iso(), key))
                e = self.store.append(c, self.id, "EFFECT_OUTCOME", "PROVIDER", {"effect_key": key, "operation_id": op, "status": "CONFIRMED", "receipt": out.receipt}, cause=cause)
                self.store.append(c, self.id, "EVIDENCE", "PROVIDER", {"effect_key": key, "source": "provider", "kind": "RECEIPT", "operation_id": op, "receipt": out.receipt, "correlated": True}, cause=e.seq)
                return ToolResult("OK", t("confirmed", action=power.action, receipt=out.receipt), data={"receipt": out.receipt, "operation_id": op})
            if out.status == "REJECTED":
                c.execute("UPDATE effects SET status='FAILED_PROVEN', updated_at=? WHERE effect_key=?", (now_iso(), key))
                if power.budget is not None:
                    self.store.bump(c, self.id, f"power:{power.action}", -1)  # nothing was created: the budget is given back
                e = self.store.append(c, self.id, "EFFECT_OUTCOME", "PROVIDER", {"effect_key": key, "operation_id": op, "status": "FAILED_PROVEN", "reason": out.reason}, cause=cause)
                self.store.append(c, self.id, "EVIDENCE", "PROVIDER", {"effect_key": key, "source": "provider", "kind": "PROVEN_REFUSAL", "operation_id": op, "reason": out.reason, "correlated": True}, cause=e.seq)
                return ToolResult("FAILED", t("rejected", action=power.action, reason=out.reason), data={"reason": out.reason})
            c.execute("UPDATE effects SET status='UNKNOWN', updated_at=? WHERE effect_key=?", (now_iso(), key))
            e = self.store.append(c, self.id, "EFFECT_OUTCOME", "PROVIDER", {"effect_key": key, "operation_id": op, "status": "UNKNOWN", "reason": out.reason}, cause=cause)
            scope = self._dependents(power.action)
            self.store.append(c, self.id, "HOLD_ENTERED", "CORE", {"effect_key": key, "operation_id": op, "scope": [power.action, *scope], "reason": out.reason or "outcome unknown"}, cause=e.seq, status="HELD")
        return self.hesitate(key)

    def _equivalent(self, power: Power, existing) -> ToolResult:
        with self.store.tx() as c:
            if existing["status"] == "CONFIRMED":
                self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": power.action, "rule": "I1", "reason": "Equivalent effect already confirmed: nothing is repeated.", "effect_key": existing["effect_key"]})
                return ToolResult("ALREADY_CONFIRMED", t("already", receipt=existing["receipt"]), data={"receipt": existing["receipt"], "operation_id": existing["operation_id"]}, rule="I1")
            why = "Equivalent effect being dispatched by another call." if existing["status"] == "DISPATCHING" else "Equivalent effect with an unknown outcome: HOLD in progress."
            self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": power.action, "rule": "I1", "reason": why, "effect_key": existing["effect_key"]})
        return self._held_message(power.action, existing["operation_id"], rule="I1")

    def _dependents(self, action: str) -> list[str]:
        out: list[str] = []
        frontier = [action]
        while frontier:
            a = frontier.pop()
            for p in self.profile.powers:
                if a in p.depends_on and p.action not in out:
                    out.append(p.action)
                    frontier.append(p.action)
        return out

    def _held_message(self, action: str, op: str, *, rule: str | None = None) -> ToolResult:
        req = self.open_request()
        tail = t("held_request", authority=self.profile.authority.name) if req else ""
        return ToolResult(
            "HELD",
            t("held", action=action, op=op) + tail,
            rule=rule, details={"operation_id": op, "question_id": req["question_id"] if req else None},
        )

    # -- hésitation ---------------------------------------------------------------------------------
    def _position(self, key: str) -> dict[str, Any]:
        eff = self.store.effect(key)
        events = self.events()
        obs = [
            {"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": e.data.get("source"), "kind": e.data.get("kind"), "text": e.data.get("detail") or e.data.get("receipt") or e.data.get("reason")}
            if e.type == "EVIDENCE" else
            {"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": "core", "kind": "WAITED", "text": f"Waited until {e.data.get('until')}; the position is read again."}
            for e in events if e.type in ("EVIDENCE", "WAIT_ENDED") and e.data.get("effect_key") == key
        ][-8:]
        for e in events:
            if e.type == "EFFECT_OUTCOME" and e.data.get("effect_key") == key and e.data.get("status") == "UNKNOWN":
                obs.insert(0, {"id": f"E{e.seq}", "seq": e.seq, "at": e.at, "source": "core", "kind": "NO_RESPONSE", "text": e.data.get("reason")})
                break
        return boundary_position(
            self.profile, effect_key=key, action=eff["action"], operation_id=eff["operation_id"], effect_state=eff["status"],
            not_found=eff["not_found"], usage=self.store.usage(self.id), authority_available=self._authority_available(),
            open_request=bool(self.open_request()), revoked=bool(self.row["revoked"]), observations=obs,
        ).as_dict()

    def _waiting(self, key: str) -> str | None:
        rows = self.store.read("SELECT until FROM waits WHERE effect_key=?", (key,))
        return rows[0]["until"] if rows else None

    def hesitate(self, key: str) -> ToolResult:
        lock = self.hab._loops.setdefault(self.id, threading.Lock())
        if not lock.acquire(blocking=False):
            eff = self.store.effect(key)
            return self._held_message(eff["action"], eff["operation_id"])
        try:
            return self._hesitate(key)
        finally:
            lock.release()

    def _hesitate(self, key: str) -> ToolResult:
        """One round: the kernel states the position (DISCERNER), the discerner declares a reading and a
        move (PERCEVOIR), the floor decides (CHOISIR). A refused reading or move is handed back to the
        discerner up to `map_retries` times; then the reference applies, credited to the floor."""
        h = self.profile.hesitation
        start_round = sum(1 for e in self.events() if e.type == "POSITION" and e.data.get("effect_key") == key)
        for rnd in range(start_round + 1, start_round + h.max_rounds + 1):
            if self.status in CLOSED:
                return ToolResult("CLOSED", t("closed", status=self.status))
            eff = self.store.effect(key)
            if self._waiting(key):
                return self._held_message(eff["action"], eff["operation_id"])
            pos = self._position(key)
            if not pos["threatened"]:
                return self._resumed(key, eff)
            if self.open_request():
                return self._held_message(eff["action"], eff["operation_id"])
            read_digest = digest(pos)
            with self.store.tx() as c:
                pos_ev = self.store.append(c, self.id, "POSITION", "CORE", {"round": rnd, "effect_key": key, "position": pos})
            feedback: list[dict[str, str]] = []
            verdict = None
            for attempt in range(h.map_retries + 1):
                proposal: Proposal = self.hab.discerner.propose(pos, {"mission_title": self.title, "feedback": feedback})
                now = self._position(key)
                decided = {k: v for k, v in {"move": proposal.move, "map": proposal.map, "wait_seconds": proposal.wait_seconds, "error": proposal.error}.items() if v is not None}
                final = attempt == h.map_retries
                verdict = admit_proposal(decided, pos, read_digest, digest(now), final=final, on_refused=h.on_refused_proposal)
                with self.store.tx() as c:
                    p_ev = self.store.append(c, self.id, "PROPOSAL", "DISCERNER", {"round": rnd, "attempt": attempt, "discerner": proposal.discerner, "position_seq": pos_ev.seq, "proposal": proposal.as_dict()}, cause=pos_ev.seq)
                    d_ev = self.store.append(c, self.id, "DECISION", "CORE", {
                        "round": rnd, "attempt": attempt, "position_seq": pos_ev.seq, "proposal": decided, "read_digest": read_digest, "now_digest": digest(now),
                        "on_refused": h.on_refused_proposal, "final": final, "outcome": verdict.outcome, "verdict": verdict.__dict__,
                    }, cause=p_ev.seq)
                    if verdict.applied is not None:
                        request = self._book(c, verdict, key, pos, decided, cause=d_ev.seq)
                if verdict.outcome == "STALE" or verdict.applied is not None:
                    break
                feedback.append({"answer": json.dumps({"map": proposal.map, "move": proposal.move}, ensure_ascii=False), "reason": verdict.reason})
            if verdict is None or verdict.applied is None:
                continue  # stale position: read it again in a new round
            path = verdict.applied
            if path == "ASK_AUTHORITY":
                self._send_request(request)
                return self._held_message(eff["action"], eff["operation_id"])
            if path in ("PRESERVE", "WAIT"):
                return self._held_message(eff["action"], eff["operation_id"])
            result = self._run_path(path, key, eff, rnd, cause=d_ev.seq)
            if result is not None:
                return result
        with self.store.tx() as c:
            self.store.append(c, self.id, "PRESERVED", "CORE", {"effect_key": key, "reason": f"limit of {h.max_rounds} hesitation rounds"}, status="HELD", phase="REEVALUER")
        eff = self.store.effect(key)
        return self._held_message(eff["action"], eff["operation_id"])

    def _book(self, c, verdict, key: str, pos: dict[str, Any], decided: dict[str, Any], *, cause: int) -> dict[str, Any] | None:
        """What applying a move spends, recorded in the same transaction as the decision."""
        path = verdict.applied
        if path == "VERIFY":
            self.store.bump(c, self.id, f"checks:{key}")
            if self.store.usage(self.id).get(f"directed_checks:{key}", 0) > 0:
                self.store.bump(c, self.id, f"directed_checks:{key}", -1)
        elif path == "CONTACT_SUPPORT":
            sup = self.profile.support
            if sup and sup.budget is not None:
                self.store.bump(c, self.id, f"power:{sup.action}")
        elif path == "ASK_AUTHORITY":
            return self._open_request(c, "UNKNOWN_EFFECT", effect_key=key, cause=cause)
        elif path == "WAIT":
            seconds = verdict.wait_seconds
            until = _iso(self.hab.clock() + timedelta(seconds=seconds))
            pending = (decided.get("map") or {}).get("pending") or {}
            self.store.bump(c, self.id, f"waits:{key}")
            self.store.bump(c, self.id, f"waited_s:{key}", seconds)
            c.execute("INSERT OR REPLACE INTO waits(effect_key, mission, until, created_at) VALUES(?,?,?,?)", (key, self.id, until, now_iso()))
            self.store.append(c, self.id, "WAIT_STARTED", "CORE", {"effect_key": key, "seconds": seconds, "until": until, "condition": pending.get("condition"), "evidence": pending.get("evidence")}, cause=cause, status="HELD")
        elif path == "PRESERVE":
            reason = "no open path reaches tau*" if verdict.reference == "PRESERVE" else "preservation chosen"
            self.store.append(c, self.id, "PRESERVED", "CORE", {"effect_key": key, "reason": reason}, cause=cause, status="HELD")
        return None

    def _run_path(self, path: str, key: str, eff, rnd: int, *, cause: int) -> ToolResult | None:
        power = self.profile.power(eff["action"])
        op = eff["operation_id"]
        if path == "VERIFY":
            status_tool = self.hab.tools.get(power.reconciliation.status_action) if power and power.reconciliation else None
            out = self._safe(status_tool.fn, operation_id=op) if status_tool else Outcome.unknown("aucun outil de statut enregistré")
        else:
            sup = self.profile.support
            sup_tool = self.hab.tools.get(sup.action) if sup else None
            question = f"Can you find the record for operation {op}? We received no confirmation."
            out = self._safe(sup_tool.fn, question=question, operation_id=op) if sup_tool else Outcome.unknown("aucun outil de support enregistré")
        correlated = out.status in ("CONFIRMED", "REPLY") and bool(out.receipt) and out.operation_id == op
        with self.store.tx() as c:
            ex = self.store.append(c, self.id, "PATH_EXECUTED", "PROVIDER", {"round": rnd, "path": path, "result": out.status, "operation_id": op, "correlated": correlated}, cause=cause)
            kind = {"CONFIRMED": "RECEIPT", "NOT_FOUND": "NOT_FOUND", "REPLY": "SUPPORT_REPLY", "UNKNOWN": "NO_ANSWER", "REJECTED": "PROVEN_REFUSAL"}.get(out.status, out.status)
            self.store.append(c, self.id, "EVIDENCE", "PROVIDER", {
                "effect_key": key, "source": "status" if path == "VERIFY" else "support", "kind": kind, "operation_id": out.operation_id,
                "receipt": out.receipt, "correlated": correlated, "detail": out.text or out.reason,
            }, cause=ex.seq)
            if out.status == "NOT_FOUND" or (out.status in ("CONFIRMED", "REPLY") and not correlated):
                c.execute("UPDATE effects SET not_found=not_found+1 WHERE effect_key=?", (key,))
            if correlated:
                c.execute("UPDATE effects SET status='CONFIRMED', receipt=?, updated_at=? WHERE effect_key=?", (out.receipt, now_iso(), key))
                self.store.append(c, self.id, "RECONCILED", "CORE", {"effect_key": key, "receipt": out.receipt, "operation_id": op, "source": path}, cause=ex.seq)
        if correlated:
            return self._resumed(key, self.store.effect(key))
        return None

    def _safe(self, fn: Callable[..., Any], **kw: Any) -> Outcome:
        try:
            out = fn(**{k: v for k, v in kw.items() if k in inspect.signature(fn).parameters or any(p.kind == p.VAR_KEYWORD for p in inspect.signature(fn).parameters.values())})
            return out if isinstance(out, Outcome) else Outcome.unknown("réponse non typée")
        except Exception as e:  # noqa: BLE001
            return Outcome.unknown(f"{type(e).__name__}: {e}")

    def _resumed(self, key: str, eff) -> ToolResult:
        with self.store.tx() as c:
            for r in c.execute("SELECT payload FROM requests WHERE mission=? AND status='OPEN' AND kind='UNKNOWN_EFFECT'", (self.id,)).fetchall():
                payload = json.loads(r["payload"])
                if payload.get("effect_key") == key:
                    self._close_request(c, payload, "CONDITIONS_CHANGED")
            already = c.execute("SELECT COUNT(*) AS n FROM events WHERE mission=? AND type='RESUMED' AND json LIKE ?", (self.id, f'%"{key}"%')).fetchone()["n"]
            if not already:
                resume_to = "OPEN" if self.status in ("HELD", "AWAITING_AUTHORITY") else None
                self.store.append(c, self.id, "RESUMED", "CORE", {"effect_key": key, "receipt": eff["receipt"], "autonomous": self.profile.hesitation.autonomous_resume}, status=resume_to)
            elif self.status in ("HELD", "AWAITING_AUTHORITY"):
                self.store.set_status(c, self.id, "OPEN")
        return ToolResult("OK", t("reconciled", action=eff["action"], receipt=eff["receipt"]), data={"receipt": eff["receipt"], "operation_id": eff["operation_id"], "reconciled": True})

    # -- Decision Requests -------------------------------------------------------------------------
    def _open_request(self, c, kind: str, *, effect_key: str | None = None, action: str | None = None, attrs: dict | None = None, violations: list[str] | None = None, cause: int | None = None) -> dict[str, Any]:
        a = self.profile.authority
        t = TITLES[a.locale]
        qid = new_question_id()
        if kind == "UNKNOWN_EFFECT":
            eff = c.execute("SELECT * FROM effects WHERE effect_key=?", (effect_key,)).fetchone()
            usage = self.store.usage(self.id)
            checks = usage.get(f"checks:{effect_key}", 0)
            codes = []
            if usage.get(f"extra_checks:{effect_key}", 0) < a.max_extra_checks:
                codes.append("EXTEND_CHECKS")
            codes.append("CLOSE_UNCONFIRMED")
            if a.can_revoke:
                codes.append("REVOKE")
            question = (
                f"L'action {eff['action']} (opération {eff['operation_id']}) reste sans preuve après {checks} contrôle(s). "
                "Aucune seconde exécution ne sera faite. Que décidez-vous ?"
                if a.locale == "fr" else
                f"Action {eff['action']} (operation {eff['operation_id']}) is still unproven after {checks} check(s). "
                "It will not be executed twice. What do you decide?"
            )
            payload = {"question_id": qid, "kind": kind, "effect_key": effect_key, "action": eff["action"], "operation_id": eff["operation_id"], "question": question}
        else:
            codes = ["APPROVE_ONCE", "REFUSE"] + (["REVOKE"] if a.can_revoke else [])
            power = self.profile.power(action)
            violations = envelope_violations(power, attrs or {}, a.locale) if power else violations
            question = (
                f"L'agent demande {action} hors de son enveloppe : {' ; '.join(violations or [])}. Autoriser cette exécution unique, à l'identique ?"
                if a.locale == "fr" else
                f"The agent requests {action} outside its envelope: {'; '.join(violations or [])}. Approve this single, identical execution?"
            )
            payload = {"question_id": qid, "kind": kind, "action": action, "attrs": attrs, "attrs_digest": digest({"action": action, "attrs": attrs}), "violations": violations, "question": question}
        payload["options"] = [{"code": code, "title": t[code]} for code in codes]
        payload |= {"profile": self.profile.fingerprint, "revision": self.profile.revision, "expires_at": _iso(self.hab.clock() + timedelta(minutes=a.question_ttl_minutes))}
        c.execute("INSERT INTO requests(question_id, mission, kind, status, payload, created_at) VALUES(?,?,?,?,?,?)", (qid, self.id, kind, "OPEN", json.dumps(payload, ensure_ascii=False), now_iso()))
        self.store.bump(c, self.id, "authority_requests")
        self.store.append(c, self.id, "DECISION_REQUEST", "CORE", {k: v for k, v in payload.items() if k != "attrs"} | {"to": self.profile.authority.name}, cause=cause, status="AWAITING_AUTHORITY")
        return payload

    def _send_request(self, request: dict[str, Any]) -> None:
        relay = self.hab.relay
        if not relay:
            return
        r = relay.send_request(mission_id=self.id, mission_title=self.title, request=request, authority=self.profile.authority)
        with self.store.tx() as c:
            c.execute(
                "INSERT INTO relay_messages(mission, direction, kind, mode, status, question_id, wa_message_id, peer, detail, error, created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (self.id, "OUT", "DECISION_REQUEST", r.mode, "SENT" if r.ok else "FAILED", request["question_id"], r.message_id, _mask(self.profile.authority.contact), json.dumps({"via": r.via}), r.error, now_iso()),
            )
            if r.ok:
                self.store.append(c, self.id, "RELAY_SENT", "RELAY", {"question_id": request["question_id"], "channel": relay.channel, "mode": r.mode, "via": r.via, "message_id": r.message_id, "to": _mask(self.profile.authority.contact)})
            else:
                self.store.append(c, self.id, "RELAY_FAILED", "RELAY", {"question_id": request["question_id"], "channel": relay.channel, "error": r.error})

    def notify(self, text: str, kind: str = "NOTICE") -> None:
        relay = self.hab.relay
        if not relay or not relay.available(self.profile.authority):
            return
        r = relay.send_text(mission_id=self.id, authority=self.profile.authority, text=text)
        with self.store.tx() as c:
            c.execute(
                "INSERT INTO relay_messages(mission, direction, kind, mode, status, wa_message_id, peer, detail, error, created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (self.id, "OUT", kind, r.mode, "SENT" if r.ok else "FAILED", r.message_id, _mask(self.profile.authority.contact), json.dumps({"text": text}, ensure_ascii=False), r.error, now_iso()),
            )
            if not r.ok:
                self.store.append(c, self.id, "RELAY_FAILED", "RELAY", {"channel": relay.channel, "error": r.error, "kind": kind})

    def answer(self, question_id: str, code: str, *, channel: str, evidence: dict[str, Any]) -> dict[str, Any]:
        """Réponse de l'autorité. Refusée si la question n'est pas la question ouverte de cette mission,
        si elle a expiré, si le profil a changé de révision ou si la situation qui la motivait a changé."""
        self.sweep()
        with self.store.tx() as c:
            row = c.execute("SELECT * FROM requests WHERE question_id=? AND mission=?", (question_id, self.id)).fetchone()
            if not row or row["status"] != "OPEN" or self.status in CLOSED:
                reason = {v: k for k, v in REQUEST_END.items()}.get(row["status"], "QUESTION_NOT_OPEN") if row else "QUESTION_NOT_OPEN"
                self.store.append(c, self.id, "RELAY_REJECTED", "CORE", {"reason": reason, "question_id": question_id, "answer": code, "channel": channel})
                return {"accepted": False, "reason": reason}
            payload = json.loads(row["payload"])
            problem = self._request_problem(c, payload, self.hab.clock())
            if problem:
                self._close_request(c, payload, problem)
                self.store.append(c, self.id, "RELAY_REJECTED", "CORE", {"reason": problem, "question_id": question_id, "answer": code, "channel": channel})
                return {"accepted": False, "reason": problem}
            if code not in {o["code"] for o in payload["options"]}:
                self.store.append(c, self.id, "RELAY_REJECTED", "CORE", {"reason": "OPTION_NOT_OFFERED", "question_id": question_id, "answer": code, "channel": channel})
                return {"accepted": False, "reason": "OPTION_NOT_OFFERED"}
            c.execute("UPDATE requests SET status='ANSWERED', answer=?, answered_at=? WHERE question_id=?", (code, now_iso(), question_id))
            ans = self.store.append(c, self.id, "AUTHORITY_ANSWER", "AUTHORITY", {"question_id": question_id, "answer": code, "channel": channel, "kind": payload["kind"], **{k: v for k, v in evidence.items() if k in ("message_id", "from", "mode")}}, status="OPEN" if code == "REFUSE" else None)
            follow: Callable[[], Any] | None = None
            if code == "REVOKE":
                self._revoke(c, by=self.profile.authority.name, channel=channel, cause=ans.seq)
            elif code == "EXTEND_CHECKS":
                key = payload["effect_key"]
                self.store.bump(c, self.id, f"extra_checks:{key}")
                self.store.bump(c, self.id, f"directed_checks:{key}")
                self.store.append(c, self.id, "AUTHORITY_GRANT", "AUTHORITY", {"grant": {"kind": "EXTRA_CHECK", "effect_key": key, "count": 1, "directed": True}}, cause=ans.seq, status="HELD")
                if self.profile.hesitation.autonomous_resume:
                    follow = lambda: self.hesitate(key)  # noqa: E731
            elif code == "CLOSE_UNCONFIRMED":
                self._close(c, "CLOSED_UNCONFIRMED", cause=ans.seq)
            elif code == "APPROVE_ONCE":
                gid = f"g_{secrets.token_hex(6)}"
                grant = {
                    "kind": "ENVELOPE_ONCE", "action": payload["action"], "attrs_digest": payload["attrs_digest"], "question_id": question_id,
                    "profile": self.profile.fingerprint, "revision": self.profile.revision,
                    "expires_at": _iso(self.hab.clock() + timedelta(minutes=self.profile.authority.grant_ttl_minutes)),
                }
                c.execute("INSERT INTO grants(id, mission, kind, payload, created_at) VALUES(?,?,?,?,?)", (gid, self.id, "ENVELOPE_ONCE", json.dumps(grant), now_iso()))
                self.store.append(c, self.id, "AUTHORITY_GRANT", "AUTHORITY", {"grant": grant | {"id": gid}}, cause=ans.seq, status="OPEN")
                attrs = payload["attrs"]
                follow = lambda: self.call(payload["action"], **attrs, _grant=gid)  # noqa: E731
            elif code == "REFUSE":
                pass  # already recorded with the answer: the mission continues without this effect
        if follow and self._can_continue(payload):
            self.hab._spawn(follow)
        return {"accepted": True, "answer": code}

    def _can_continue(self, payload: dict[str, Any]) -> bool:
        """Seul le processus qui porte les outils de l'agent peut poursuivre (un serveur seul enregistre)."""
        action = payload.get("action")
        power = self.profile.power(action) if action else None
        needed = [action] if action else []
        if power and power.reconciliation:
            needed.append(power.reconciliation.status_action)
        return all(n in self.hab.tools for n in needed)

    def resume_pending(self) -> ToolResult | None:
        """Reprend ce qu'une réponse reçue par un autre processus a rendu possible."""
        if self.status in CLOSED:
            return None
        usage = self.store.usage(self.id)
        for e in self.store.effects(self.id):
            if e["status"] == "UNKNOWN" and usage.get(f"directed_checks:{e['effect_key']}", 0) > 0:
                return self.hesitate(e["effect_key"])
        for g in self.store.read("SELECT * FROM grants WHERE mission=? AND consumed=0", (self.id,)):
            grant = json.loads(g["payload"])
            req = self.store.read("SELECT payload FROM requests WHERE question_id=?", (grant["question_id"],))
            if req:
                attrs = json.loads(req[0]["payload"]).get("attrs") or {}
                if grant_problem(grant, action=grant["action"], attrs=attrs, profile=self.profile, now=self.hab.clock()):
                    continue
                return self.call(grant["action"], **attrs, _grant=g["id"])
        return None

    # -- validité des questions ----------------------------------------------------------------------
    def _request_problem(self, c, payload: dict[str, Any], now: datetime) -> str | None:
        """Why an open Decision Request can no longer be answered, or None."""
        if payload.get("expires_at") and now >= parse_time(payload["expires_at"]):
            return "QUESTION_EXPIRED"
        if payload.get("profile") != self.profile.fingerprint:
            return "PROFILE_CHANGED"
        if payload["kind"] == "UNKNOWN_EFFECT":
            eff = c.execute("SELECT status FROM effects WHERE effect_key=?", (payload["effect_key"],)).fetchone()
            if not eff or eff["status"] != "UNKNOWN":
                return "CONDITIONS_CHANGED"
        else:
            power = self.profile.power(payload["action"])
            if power and power.kind == "effect":
                existing = c.execute("SELECT status FROM effects WHERE effect_key=?", (effect_key(self.id, payload["action"], power.identity, payload.get("attrs") or {}),)).fetchone()
                if existing and existing["status"] in ("CONFIRMED", "UNKNOWN", "DISPATCHING"):
                    return "CONDITIONS_CHANGED"
        return None

    def _close_request(self, c, payload: dict[str, Any], reason: str, *, cause: int | None = None) -> None:
        c.execute("UPDATE requests SET status=? WHERE question_id=? AND status='OPEN'", (REQUEST_END[reason], payload["question_id"]))
        status = self.store.mission(self.id)["status"]
        if status not in CLOSED:
            unknown = c.execute("SELECT COUNT(*) AS n FROM effects WHERE mission=? AND status='UNKNOWN'", (self.id,)).fetchone()["n"]
            status = "HELD" if unknown else "OPEN"
        self.store.append(c, self.id, "REQUEST_CLOSED", "CORE", {"question_id": payload["question_id"], "kind": payload["kind"], "reason": reason, "status": REQUEST_END[reason], "mission_status": status}, cause=cause, status=status)

    def sweep(self) -> list[str]:
        """Closes the Decision Requests whose delay has run out. If the hesitation can continue in this
        process, it resumes: a silent authority is not an answer."""
        now = self.hab.clock()
        expired: list[dict[str, Any]] = []
        for r in self.store.read("SELECT payload FROM requests WHERE mission=? AND status='OPEN'", (self.id,)):
            payload = json.loads(r["payload"])
            if payload.get("expires_at") and now >= parse_time(payload["expires_at"]):
                with self.store.tx() as c:
                    if c.execute("SELECT status FROM requests WHERE question_id=?", (payload["question_id"],)).fetchone()["status"] == "OPEN":
                        self._close_request(c, payload, "QUESTION_EXPIRED")
                        expired.append(payload)
        woken: list[str] = []
        for r in self.store.read("SELECT effect_key, until FROM waits WHERE mission=?", (self.id,)):
            if now >= parse_time(r["until"]):
                with self.store.tx() as c:
                    if c.execute("DELETE FROM waits WHERE effect_key=? AND until=?", (r["effect_key"], r["until"])).rowcount == 1:
                        self.store.append(c, self.id, "WAIT_ENDED", "CORE", {"effect_key": r["effect_key"], "until": r["until"]})
                        woken.append(r["effect_key"])
        for p in expired:
            if p["kind"] == "UNKNOWN_EFFECT" and self.status == "HELD" and self._can_continue(p):
                self.hab._spawn(lambda key=p["effect_key"]: self.hesitate(key))
        for key in woken:
            eff = self.store.effect(key)
            if eff and self.status == "HELD" and self._can_continue({"action": eff["action"]}):
                self.hab._spawn(lambda key=key: self.hesitate(key))
        return [p["question_id"] for p in expired]

    # -- révocation et clôture -----------------------------------------------------------------------
    def _revoke(self, c, *, by: str, channel: str, cause: int | None = None) -> None:
        c.execute("UPDATE missions SET revoked=1 WHERE id=?", (self.id,))
        c.execute("UPDATE requests SET status='CANCELLED' WHERE mission=? AND status='OPEN'", (self.id,))
        c.execute("DELETE FROM waits WHERE mission=?", (self.id,))
        self.store.append(c, self.id, "REVOKED", "AUTHORITY", {"by": by, "channel": channel}, cause=cause, status="REVOKED")

    def revoke(self, *, by: str, channel: str = "console") -> None:
        with self.store.tx() as c:
            if self.status not in CLOSED:
                self._revoke(c, by=by, channel=channel)

    def _report(self) -> dict[str, Any]:
        effs = self.store.effects(self.id)
        return {
            "confirmed": [{"action": e["action"], "receipt": e["receipt"], "operation_id": e["operation_id"]} for e in effs if e["status"] == "CONFIRMED"],
            "unconfirmed": [{"action": e["action"], "operation_id": e["operation_id"], "status": e["status"]} for e in effs if e["status"] in ("UNKNOWN", "DISPATCHING")],
            "failed": [{"action": e["action"], "operation_id": e["operation_id"]} for e in effs if e["status"] == "FAILED_PROVEN"],
        }

    def _close(self, c, outcome: str, *, cause: int | None = None, report: dict | None = None, phase: str | None = None) -> None:
        c.execute("UPDATE requests SET status='CANCELLED' WHERE mission=? AND status='OPEN'", (self.id,))
        c.execute("DELETE FROM waits WHERE mission=?", (self.id,))
        self.store.append(c, self.id, "MISSION_CLOSED", "CORE", {"outcome": outcome, "report": report or self._report()}, cause=cause, status=outcome, phase=phase)

    def close(self, outcome: str = "CLOSED_COMPLETED", report: dict | None = None) -> dict[str, Any]:
        with self.store.tx() as c:
            if self.status in CLOSED:
                return {"closed": False, "status": self.status}
            if outcome == "CLOSED_COMPLETED" and any(e["status"] in ("UNKNOWN", "DISPATCHING") for e in self.store.effects(self.id)):
                outcome = "CLOSED_UNCONFIRMED"  # jamais de « terminé » avec un effet sans preuve (I2)
            self._close(c, outcome, report=report, phase="REEVALUER")  # the agent closes: it confronts results with the mission
        return {"closed": True, "status": outcome}

    def wait(self, timeout: float = 60.0, poll: float = 0.25) -> str:
        """Attend que la mission sorte de HOLD / AWAITING_AUTHORITY (ou le délai)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            self.sweep()
            if self.status not in ("HELD", "AWAITING_AUTHORITY"):
                break
            lock = self.hab._loops.get(self.id)
            if self.status == "HELD" and not (lock and lock.locked()):
                self.resume_pending()
            time.sleep(poll)
        return self.status


def _mask(digits: str | None) -> str:
    if not digits:
        return "—"
    return f"+{digits[:3]}{'•' * max(0, len(digits) - 7)}{digits[-4:]}"
