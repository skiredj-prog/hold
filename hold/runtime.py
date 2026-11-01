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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .canonical import canonical_json, digest
from .discern import Discerner, Proposal, ReferenceDiscerner
from .floor import ActionContext, admit_action, admit_path, effect_key, envelope_violations
from .hesitation import boundary_position, judge
from .journal import verify_chain
from .outcome import Outcome, ToolResult
from .profile import Power, Profile, load_profile
from .relay.base import TITLES, Relay, new_question_id
from .store import Store, now_iso

UNKNOWN_EXCEPTIONS = (TimeoutError, ConnectionError, OSError)
CLOSED = {"CLOSED_COMPLETED", "CLOSED_UNCONFIRMED", "CLOSED_FAILED", "REVOKED"}


@dataclass
class _Tool:
    action: str
    kind: str
    fn: Callable[..., Any]
    description: str
    parameters: dict[str, Any]


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
    ):
        self.profile = profile if isinstance(profile, Profile) else load_profile(profile, secret=secret, require_signature=require_signature)
        self.store = store if isinstance(store, Store) else Store(store)
        self.discerner: Discerner = discerner or ReferenceDiscerner()
        self.relay = relay
        self.background = background
        self.tools: dict[str, _Tool] = {}
        self._loops: dict[str, threading.Lock] = {}
        self._threads: list[threading.Thread] = []

    # -- enregistrement des outils (le profil gouverne) --------------------------------------
    def _register(self, kind: str, action: str, description: str | None, parameters: dict | None):
        def deco(fn: Callable[..., Any]):
            power = self.profile.power(action)
            if not power:
                raise ValueError(f"Aucun pouvoir « {action} » dans le profil {self.profile.id}")
            if power.kind != kind:
                raise ValueError(f"« {action} » est déclaré « {power.kind} » dans le profil, pas « {kind} »")
            self.tools[action] = _Tool(action, kind, fn, description or power.description or (fn.__doc__ or "").strip(), parameters or _schema_from_signature(fn))
            return fn

        return deco

    def read(self, action: str, *, description: str | None = None, parameters: dict | None = None):
        return self._register("read", action, description, parameters)

    def effect(self, action: str, *, description: str | None = None, parameters: dict | None = None):
        return self._register("effect", action, description, parameters)

    def status(self, action: str):
        return self._register("status", action, None, {"type": "object", "properties": {}})

    def contact(self, action: str, *, description: str | None = None):
        return self._register("contact", action, description, {"type": "object", "properties": {"question": {"type": "string"}}, "required": ["question"]})

    def missing_tools(self) -> list[str]:
        return [p.action for p in self.profile.powers if p.action not in self.tools]

    # -- missions -----------------------------------------------------------------------------
    def open_mission(self, title: str, mission_id: str | None = None) -> "Mission":
        mid = mission_id or f"m_{secrets.token_hex(6)}"
        body = canonical_json(self.profile.model_dump(mode="json", exclude_none=True))
        with self.store.tx() as c:
            self.store.create_mission(c, mid, title, self.profile.fingerprint, self.profile.revision, body)
            self.store.append(c, mid, "MISSION_OPENED", "AGENT", {"title": title})
            self.store.append(c, mid, "PROFILE_ATTACHED", "CORE", {
                "fingerprint": self.profile.fingerprint, "revision": self.profile.revision, "profile": self.profile.id,
                "signed": bool(self.profile.signature), "authority": self.profile.authority.name,
            })
        return Mission(self, mid)

    def mission(self, mission_id: str) -> "Mission":
        if not self.store.mission(mission_id):
            raise KeyError(f"Mission inconnue : {mission_id}")
        return Mission(self, mission_id)

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
        return Mission(self, mission_id).answer(question_id, code, channel=channel, evidence=evidence or {})


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
        return [dict(r) | {"attributes": json.loads(r["attributes"])} for r in self.store.effects(self.id)]

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
        if self.status in CLOSED:
            return ToolResult("CLOSED" if self.status != "REVOKED" else "REVOKED", f"Mission {self.status} : aucune action.", rule="I4")
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
            v = admit_action(self.profile, action, attrs, ctx, grant_digest=(grant or {}).get("attrs_digest"))
            if v.admitted and tool is None:
                v = type(v)(False, "NO_IMPLEMENTATION", f"Aucune implémentation enregistrée pour « {action} ».")
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
            return ToolResult("HELD", f"Hors enveloppe : {v.reason}. Decision Request envoyée à {self.profile.authority.name} ; n'essayez pas de contourner.", rule="I4/ENVELOPE", details={"question_id": request["question_id"]})
        if power.kind == "effect":
            return self._effect(tool, power, attrs, grant)
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
            return ToolResult("FAILED", f"{power.action} a échoué : {e}")
        data = out.data if isinstance(out, Outcome) else out
        with self.store.tx() as c:
            self.store.append(c, self.id, "ACTION_RESULT", "PROVIDER", {"action": power.action, "status": "OK", "data_sha": digest(data) if data is not None else None}, cause=admitted.seq)
        return ToolResult("OK", f"{power.action} : terminé.", data=data)

    def _grant(self, grant_id: str) -> dict[str, Any] | None:
        rows = self.store.read("SELECT * FROM grants WHERE id=? AND mission=? AND consumed=0", (grant_id, self.id))
        return json.loads(rows[0]["payload"]) | {"id": grant_id} if rows else None

    def _effect(self, tool: _Tool, power: Power, attrs: dict[str, Any], grant: dict[str, Any] | None) -> ToolResult:
        key = effect_key(self.id, power.action, power.identity, attrs)
        op = f"op_{secrets.token_hex(6)}"
        with self.store.tx() as c:
            existing = c.execute("SELECT * FROM effects WHERE effect_key=?", (key,)).fetchone()
            if existing and existing["status"] in ("CONFIRMED", "UNKNOWN", "DISPATCHING"):
                return self._equivalent(power, existing)  # course perdue face à un appel concurrent
            if existing:  # FAILED_PROVEN : un refus prouvé n'a rien créé, un nouvel essai est admissible
                c.execute("UPDATE effects SET operation_id=?, status='DISPATCHING', receipt=NULL, attempts=attempts+1, not_found=0, updated_at=? WHERE effect_key=?", (op, now_iso(), key))
            else:
                c.execute(
                    "INSERT INTO effects(effect_key, mission, action, operation_id, status, attributes, created_at, updated_at) VALUES(?,?,?,?,?,?,?,?)",
                    (key, self.id, power.action, op, "DISPATCHING", json.dumps(attrs, ensure_ascii=False, default=str), now_iso(), now_iso()),
                )
            if power.budget is not None:
                self.store.bump(c, self.id, f"power:{power.action}")
            if grant:
                c.execute("UPDATE grants SET consumed=1 WHERE id=?", (grant["id"],))
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
                return ToolResult("OK", f"{power.action} confirmé (reçu {out.receipt}).", data={"receipt": out.receipt, "operation_id": op})
            if out.status == "REJECTED":
                c.execute("UPDATE effects SET status='FAILED_PROVEN', updated_at=? WHERE effect_key=?", (now_iso(), key))
                if power.budget is not None:
                    self.store.bump(c, self.id, f"power:{power.action}", -1)  # rien n'a été créé : le budget est rendu
                e = self.store.append(c, self.id, "EFFECT_OUTCOME", "PROVIDER", {"effect_key": key, "operation_id": op, "status": "FAILED_PROVEN", "reason": out.reason}, cause=cause)
                self.store.append(c, self.id, "EVIDENCE", "PROVIDER", {"effect_key": key, "source": "provider", "kind": "PROVEN_REFUSAL", "operation_id": op, "reason": out.reason, "correlated": True}, cause=e.seq)
                return ToolResult("FAILED", f"{power.action} refusé avant création : {out.reason}. Un nouvel essai est admissible.", data={"reason": out.reason})
            c.execute("UPDATE effects SET status='UNKNOWN', updated_at=? WHERE effect_key=?", (now_iso(), key))
            e = self.store.append(c, self.id, "EFFECT_OUTCOME", "PROVIDER", {"effect_key": key, "operation_id": op, "status": "UNKNOWN", "reason": out.reason}, cause=cause)
            scope = self._dependents(power.action)
            self.store.append(c, self.id, "HOLD_ENTERED", "CORE", {"effect_key": key, "operation_id": op, "scope": [power.action, *scope], "reason": out.reason or "résultat inconnu"}, cause=e.seq)
            self.store.set_status(c, self.id, "HELD")
        return self.hesitate(key)

    def _equivalent(self, power: Power, existing) -> ToolResult:
        with self.store.tx() as c:
            if existing["status"] == "CONFIRMED":
                self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": power.action, "rule": "I1", "reason": "Effet équivalent déjà confirmé : rien n'est répété.", "effect_key": existing["effect_key"]})
                return ToolResult("ALREADY_CONFIRMED", f"Déjà fait et confirmé (reçu {existing['receipt']}). Rien n'a été répété.", data={"receipt": existing["receipt"], "operation_id": existing["operation_id"]}, rule="I1")
            self.store.append(c, self.id, "ACTION_REFUSED", "CORE", {"action": power.action, "rule": "I1", "reason": "Effet équivalent au résultat inconnu : HOLD en cours.", "effect_key": existing["effect_key"]})
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
        tail = f" Decision Request ouverte auprès de {self.profile.authority.name}." if req else ""
        return ToolResult(
            "HELD",
            f"HOLD : le résultat de {action} ({op}) est inconnu. Ne réessayez pas et ne créez rien d'équivalent ; "
            f"HOLD conduit la réconciliation. Les actions dépendantes sont suspendues, le travail indépendant peut continuer.{tail}",
            rule=rule, details={"operation_id": op, "question_id": req["question_id"] if req else None},
        )

    # -- hésitation ---------------------------------------------------------------------------------
    def _position(self, key: str) -> dict[str, Any]:
        eff = self.store.effect(key)
        obs = [
            {"seq": e.seq, "at": e.at, "source": e.data.get("source"), "kind": e.data.get("kind"), "detail": e.data.get("detail") or e.data.get("receipt") or e.data.get("reason")}
            for e in self.events() if e.type == "EVIDENCE" and e.data.get("effect_key") == key
        ][-8:]
        for e in self.events():
            if e.type == "EFFECT_OUTCOME" and e.data.get("effect_key") == key and e.data.get("status") == "UNKNOWN":
                obs.insert(0, {"seq": e.seq, "at": e.at, "source": "core", "kind": "NO_RESPONSE", "detail": e.data.get("reason")})
                break
        return boundary_position(
            self.profile, effect_key=key, action=eff["action"], operation_id=eff["operation_id"], effect_state=eff["status"],
            not_found=eff["not_found"], usage=self.store.usage(self.id), authority_available=self._authority_available(),
            open_request=bool(self.open_request()), revoked=bool(self.row["revoked"]), observations=obs,
        ).as_dict()

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
        h = self.profile.hesitation
        ctx = {"mission_title": self.title}
        start_round = sum(1 for e in self.events() if e.type == "DECISION" and e.data.get("position", {}).get("effect_key") == key)
        for rnd in range(start_round + 1, start_round + h.max_rounds + 1):
            if self.status in CLOSED:
                return ToolResult("CLOSED", f"Mission {self.status}.")
            pos = self._position(key)
            eff = self.store.effect(key)
            if not pos["threatened"]:
                return self._resumed(key, eff)
            if self.open_request():
                return self._held_message(eff["action"], eff["operation_id"])
            read_digest = digest(pos)
            proposal: Proposal = self.hab.discerner.propose(pos, ctx)
            now = self._position(key)
            verdict = admit_path(proposal.path, pos, read_digest, digest(now), on_refused=h.on_refused_proposal)
            with self.store.tx() as c:
                p_ev = self.store.append(c, self.id, "PROPOSAL", "DISCERNER", {"round": rnd, "discerner": proposal.discerner, "position": pos, "proposal": proposal.as_dict(), "judgment": judge(proposal.as_dict(), pos)})
                d_ev = self.store.append(c, self.id, "DECISION", "CORE", {
                    "round": rnd, "position": pos, "proposal": {"path": proposal.path}, "read_digest": read_digest, "now_digest": digest(now),
                    "on_refused": h.on_refused_proposal, "verdict": verdict.__dict__,
                }, cause=p_ev.seq)
                if verdict.applied is None:
                    continue
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
                    request = self._open_request(c, "UNKNOWN_EFFECT", effect_key=key, cause=d_ev.seq)
                elif path == "PRESERVE":
                    self.store.append(c, self.id, "PRESERVED", "CORE", {"effect_key": key, "reason": "aucun chemin ouvert n'atteint τ*" if verdict.reference == "PRESERVE" else "conservation choisie"}, cause=d_ev.seq)
                    self.store.set_status(c, self.id, "HELD")
            if path == "ASK_AUTHORITY":
                self._send_request(request)
                return self._held_message(eff["action"], eff["operation_id"])
            if path == "PRESERVE":
                return self._held_message(eff["action"], eff["operation_id"])
            result = self._run_path(path, key, eff, rnd, cause=d_ev.seq)
            if result is not None:
                return result
        with self.store.tx() as c:
            self.store.append(c, self.id, "PRESERVED", "CORE", {"effect_key": key, "reason": f"limite de {h.max_rounds} tours d'hésitation"})
        eff = self.store.effect(key)
        return self._held_message(eff["action"], eff["operation_id"])

    def _run_path(self, path: str, key: str, eff, rnd: int, *, cause: int) -> ToolResult | None:
        power = self.profile.power(eff["action"])
        op = eff["operation_id"]
        if path == "VERIFY":
            status_tool = self.hab.tools.get(power.reconciliation.status_action) if power and power.reconciliation else None
            out = self._safe(status_tool.fn, operation_id=op) if status_tool else Outcome.unknown("aucun outil de statut enregistré")
        else:
            sup = self.profile.support
            sup_tool = self.hab.tools.get(sup.action) if sup else None
            question = f"Pouvez-vous retrouver la réservation liée à l'opération {op} ? Nous n'avons reçu aucune confirmation."
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
            already = c.execute("SELECT COUNT(*) AS n FROM events WHERE mission=? AND type='RESUMED' AND json LIKE ?", (self.id, f'%"{key}"%')).fetchone()["n"]
            if not already:
                self.store.append(c, self.id, "RESUMED", "CORE", {"effect_key": key, "receipt": eff["receipt"], "autonomous": self.profile.hesitation.autonomous_resume})
            if self.status in ("HELD", "AWAITING_AUTHORITY"):
                self.store.set_status(c, self.id, "OPEN")
        return ToolResult("OK", f"{eff['action']} confirmé après réconciliation (reçu {eff['receipt']}). Rien n'a été répété ; la mission reprend.", data={"receipt": eff["receipt"], "operation_id": eff["operation_id"], "reconciled": True})

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
            question = (
                f"L'agent demande {action} hors de son enveloppe : {' ; '.join(violations or [])}. Autoriser cette exécution unique, à l'identique ?"
                if a.locale == "fr" else
                f"The agent requests {action} outside its envelope: {'; '.join(violations or [])}. Approve this single, identical execution?"
            )
            payload = {"question_id": qid, "kind": kind, "action": action, "attrs": attrs, "attrs_digest": digest({"action": action, "attrs": attrs}), "violations": violations, "question": question}
        payload["options"] = [{"code": code, "title": t[code]} for code in codes]
        c.execute("INSERT INTO requests(question_id, mission, kind, status, payload, created_at) VALUES(?,?,?,?,?,?)", (qid, self.id, kind, "OPEN", json.dumps(payload, ensure_ascii=False), now_iso()))
        self.store.bump(c, self.id, "authority_requests")
        self.store.append(c, self.id, "DECISION_REQUEST", "CORE", {k: v for k, v in payload.items() if k != "attrs"} | {"to": self.profile.authority.name}, cause=cause)
        self.store.set_status(c, self.id, "AWAITING_AUTHORITY")
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
        """Réponse de l'autorité. Refusée si la question n'est pas la question ouverte de cette mission."""
        with self.store.tx() as c:
            row = c.execute("SELECT * FROM requests WHERE question_id=? AND mission=?", (question_id, self.id)).fetchone()
            if not row or row["status"] != "OPEN" or self.status in CLOSED:
                self.store.append(c, self.id, "RELAY_REJECTED", "CORE", {"reason": "QUESTION_NOT_OPEN", "question_id": question_id, "answer": code, "channel": channel})
                return {"accepted": False, "reason": "QUESTION_NOT_OPEN"}
            payload = json.loads(row["payload"])
            if code not in {o["code"] for o in payload["options"]}:
                self.store.append(c, self.id, "RELAY_REJECTED", "CORE", {"reason": "OPTION_NOT_OFFERED", "question_id": question_id, "answer": code, "channel": channel})
                return {"accepted": False, "reason": "OPTION_NOT_OFFERED"}
            c.execute("UPDATE requests SET status='ANSWERED', answer=?, answered_at=? WHERE question_id=?", (code, now_iso(), question_id))
            ans = self.store.append(c, self.id, "AUTHORITY_ANSWER", "AUTHORITY", {"question_id": question_id, "answer": code, "channel": channel, "kind": payload["kind"], **{k: v for k, v in evidence.items() if k in ("message_id", "from", "mode")}})
            follow: Callable[[], Any] | None = None
            if code == "REVOKE":
                self._revoke(c, by=self.profile.authority.name, channel=channel, cause=ans.seq)
            elif code == "EXTEND_CHECKS":
                key = payload["effect_key"]
                self.store.bump(c, self.id, f"extra_checks:{key}")
                self.store.bump(c, self.id, f"directed_checks:{key}")
                self.store.append(c, self.id, "AUTHORITY_GRANT", "AUTHORITY", {"grant": {"kind": "EXTRA_CHECK", "effect_key": key, "count": 1, "directed": True}}, cause=ans.seq)
                self.store.set_status(c, self.id, "HELD")
                if self.profile.hesitation.autonomous_resume:
                    follow = lambda: self.hesitate(key)  # noqa: E731
            elif code == "CLOSE_UNCONFIRMED":
                self._close(c, "CLOSED_UNCONFIRMED", cause=ans.seq)
            elif code == "APPROVE_ONCE":
                gid = f"g_{secrets.token_hex(6)}"
                grant = {"kind": "ENVELOPE_ONCE", "action": payload["action"], "attrs_digest": payload["attrs_digest"], "question_id": question_id}
                c.execute("INSERT INTO grants(id, mission, kind, payload, created_at) VALUES(?,?,?,?,?)", (gid, self.id, "ENVELOPE_ONCE", json.dumps(grant), now_iso()))
                self.store.append(c, self.id, "AUTHORITY_GRANT", "AUTHORITY", {"grant": grant | {"id": gid}}, cause=ans.seq)
                self.store.set_status(c, self.id, "OPEN")
                attrs = payload["attrs"]
                follow = lambda: self.call(payload["action"], **attrs, _grant=gid)  # noqa: E731
            elif code == "REFUSE":
                self.store.set_status(c, self.id, "OPEN")
        if follow:
            self.hab._spawn(follow)
        return {"accepted": True, "answer": code}

    # -- révocation et clôture -----------------------------------------------------------------------
    def _revoke(self, c, *, by: str, channel: str, cause: int | None = None) -> None:
        c.execute("UPDATE missions SET revoked=1 WHERE id=?", (self.id,))
        c.execute("UPDATE requests SET status='CANCELLED' WHERE mission=? AND status='OPEN'", (self.id,))
        self.store.append(c, self.id, "REVOKED", "AUTHORITY", {"by": by, "channel": channel}, cause=cause)
        self.store.set_status(c, self.id, "REVOKED")

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

    def _close(self, c, outcome: str, *, cause: int | None = None, report: dict | None = None) -> None:
        c.execute("UPDATE requests SET status='CANCELLED' WHERE mission=? AND status='OPEN'", (self.id,))
        self.store.append(c, self.id, "MISSION_CLOSED", "CORE", {"outcome": outcome, "report": report or self._report()}, cause=cause)
        self.store.set_status(c, self.id, outcome)

    def close(self, outcome: str = "CLOSED_COMPLETED", report: dict | None = None) -> dict[str, Any]:
        with self.store.tx() as c:
            if self.status in CLOSED:
                return {"closed": False, "status": self.status}
            if outcome == "CLOSED_COMPLETED" and any(e["status"] in ("UNKNOWN", "DISPATCHING") for e in self.store.effects(self.id)):
                outcome = "CLOSED_UNCONFIRMED"  # jamais de « terminé » avec un effet sans preuve (I2)
            self._close(c, outcome, report=report)
        return {"closed": True, "status": outcome}

    def wait(self, timeout: float = 60.0, poll: float = 0.25) -> str:
        """Attend que la mission sorte de HOLD / AWAITING_AUTHORITY (ou le délai)."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.status not in ("HELD", "AWAITING_AUTHORITY"):
                break
            time.sleep(poll)
        return self.status


def _mask(digits: str | None) -> str:
    if not digits:
        return "—"
    return f"+{digits[:3]}{'•' * max(0, len(digits) - 7)}{digits[-4:]}"
