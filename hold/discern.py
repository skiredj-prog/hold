"""Discerners: who proposes a move when coherence is threatened.

The discerner declares a reading and a move; the floor checks the reading, the move, and that one
follows from the other. A weak discerner cannot make the floor unsafe, and for that very reason its
contribution is measured separately: what was admitted on its own merit is credited to it, what the
floor applied after refusing it is credited to the floor.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from .canonical import canonical_json, sha256_hex

MOVES = ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "WAIT", "PRESERVE"]
PENDING = ["NONE", "VISIBILITY_DELAY", "SUPPORT_REPLY"]
EFFECT_STATES = ["DISPATCHING", "UNKNOWN", "CONFIRMED", "FAILED_PROVEN"]


@dataclass
class Proposal:
    """A decision returned by a discerner: a declared map of the facts, a move, and reasons citing
    fact ids. Only the map, the move and the citations are checked; the rationale is kept as text."""

    discerner: str
    move: str | None
    map: dict[str, Any] | None = None
    rationale: str = ""
    cites: list[str] | None = None
    wait_seconds: int | None = None
    model: str | None = None
    model_digest: str | None = None
    input_sha: str | None = None
    output_sha: str | None = None
    duration_ms: int | None = None
    error: str | None = None

    def as_dict(self) -> dict[str, Any]:
        return {k: v for k, v in asdict(self).items() if v is not None}


class Discerner(Protocol):
    name: str

    def propose(self, position: dict[str, Any], context: dict[str, Any]) -> Proposal: ...


class ReferenceDiscerner:
    """The reference cascade (control arm). Not a model: it copies the kernel's own reading, so its map is
    true by construction, it cannot read free text, and it never waits."""

    name = "reference"

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": "rule", "note": "least-authority cascade on the kernel's own reading; not a model"}

    def propose(self, position: dict[str, Any], context: dict[str, Any]) -> Proposal:
        from .hesitation import true_map

        return Proposal("reference", position["reference"], true_map(position), "First open path reaching tau* in the ladder (authority directive first).", ["P:" + position["reference"]])


SYSTEM_PROMPT = """You are the hesitation layer of an autonomous agent. An external action was sent, but its result is unknown. You never act on the world yourself: you read the facts, declare your reading, choose one move, and a deterministic floor checks your reading and your move before anything happens.

The facts:
- effect_state: UNKNOWN means a request was sent and its result is not proven. A timeout does not prove failure. "Not found" does not prove failure.
- tau_star: the coherence to restore before acting again. A path "reaches tau*" when it is open and its sigma >= tau_star.
- paths: each has open (allowed now, with the reason), sigma (chance to restore coherence), authority (who is mobilized: agent < mandate < authority), left (what remains of it), directed (granted explicitly by the accountable authority).
- observations: evidence collected so far, each with an id (E...) and its text.

The moves:
- VERIFY: check the status with the provider. Each check is spent, and each "not found" lowers the sigma of the next one.
- CONTACT_SUPPORT: ask the provider's support. Its contacts are few.
- ASK_AUTHORITY: send a Decision Request to the accountable authority. It disturbs a person and uses one of few requests.
- WAIT: wait without acting, only when an observation states that something is pending (the record may take time to appear, a reply is coming). You must quote that observation.
- PRESERVE: keep the state and do nothing more.

The mission's rules:
1. Never choose a closed path.
2. A path granted by the authority (directed=true) comes first.
3. Do not ask the authority while a path inside the habilitation (VERIFY, CONTACT_SUPPORT) reaches tau*, or while a pending condition stated in the evidence can still resolve within your patience.
4. A path below tau* is not expected to restore coherence.
5. If nothing is justified, PRESERVE.
6. Never propose to recreate the action, to declare success, or to run dependent actions.

Among the moves the facts justify, the choice is yours: weigh what each one spends and what it leaves for later.

Answer only with the JSON object required by the schema:
- map.effect_state, map.open_paths (open paths except PRESERVE), map.reaching_tau: your reading of the facts.
- map.pending: condition NONE, or VISIBILITY_DELAY / SUPPORT_REPLY with the evidence id, an exact quote from that observation, and wait_seconds.
- move, rationale (one or two sentences), cites (the ids of the facts you used: E... for observations, P:<PATH> for paths)."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "map": {
            "type": "object",
            "properties": {
                "effect_state": {"type": "string", "enum": EFFECT_STATES},
                "open_paths": {"type": "array", "items": {"type": "string", "enum": MOVES[:4]}},
                "reaching_tau": {"type": "array", "items": {"type": "string", "enum": MOVES[:3]}},
                "pending": {
                    "type": "object",
                    "properties": {
                        "condition": {"type": "string", "enum": PENDING},
                        "evidence": {"type": "string"},
                        "quote": {"type": "string"},
                        "wait_seconds": {"type": "integer"},
                    },
                    "required": ["condition"],
                },
            },
            "required": ["effect_state", "open_paths", "reaching_tau", "pending"],
        },
        "move": {"type": "string", "enum": MOVES},
        "rationale": {"type": "string"},
        "cites": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["map", "move", "rationale", "cites"],
}


def model_view(position: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """What the model sees: the facts and the boundary, never the computed reference."""
    return {
        "mission": context.get("mission_title"),
        "impasse": {"action": position["action"], "operation_id": position["operation_id"]},
        "effect_state": position["effect_state"],
        "coherence": position["coherence"],
        "tau_star": position["tau_star"],
        "paths": [{k: p.get(k) for k in ("name", "open", "reason", "sigma", "authority", "left", "directed")} for p in position["paths"]],
        "observations": [{k: o.get(k) for k in ("id", "source", "kind", "text")} for o in position["observations"]],
        "forbidden": position["forbidden"],
    }


class LlamaDiscerner:
    """Llama (poids ouverts Meta) servi par Ollama. Sortie contrainte par schéma JSON, température 0."""

    name = "llama"

    def __init__(self, model: str | None = None, base_url: str | None = None, timeout_s: float = 240.0):
        self.model = model or os.environ.get("LLAMA_MODEL", "llama3.2:3b")
        self.base_url = (base_url or os.environ.get("LLAMA_BASE_URL", "http://127.0.0.1:11434")).rstrip("/")
        self.timeout_s = timeout_s
        self.options = {"temperature": 0, "seed": 42, "num_ctx": 4096, "num_predict": 400}

    def describe(self) -> dict[str, Any]:
        return {"name": self.name, "kind": "model", "model": self.model, "options": self.options, "prompt_sha256": sha256_hex(SYSTEM_PROMPT)}

    def available(self) -> tuple[bool, str | None, str | None]:
        try:
            r = httpx.get(f"{self.base_url}/api/tags", timeout=4)
            for m in r.json().get("models", []):
                if m.get("name") in (self.model, f"{self.model}:latest"):
                    return True, m.get("digest"), None
            return False, None, "model not pulled"
        except Exception as e:  # noqa: BLE001 — l'indisponibilité est une information, pas une panne
            return False, None, str(e)

    def propose(self, position: dict[str, Any], context: dict[str, Any]) -> Proposal:
        view = model_view(position, context)
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Facts (JSON):\n" + json.dumps(view, ensure_ascii=False, indent=1)},
        ]
        for past in context.get("feedback") or []:  # the floor's refusals of earlier attempts, handed back
            messages += [{"role": "assistant", "content": past["answer"]}, {"role": "user", "content": "The floor refused this: " + past["reason"] + " Read the facts again and answer."}]
        input_sha = sha256_hex(canonical_json(messages))
        started = time.monotonic()
        try:
            r = httpx.post(
                f"{self.base_url}/api/chat",
                timeout=self.timeout_s,
                json={"model": self.model, "stream": False, "format": OUTPUT_SCHEMA, "options": self.options, "messages": messages},
            )
            data = r.json()
            raw = (data.get("message") or {}).get("content", "")
            ms = int((time.monotonic() - started) * 1000)
            if r.status_code != 200 or data.get("error"):
                return Proposal("llama", None, model=self.model, input_sha=input_sha, duration_ms=ms, error=str(data.get("error") or r.status_code))
            out = json.loads(raw)
            move = out.get("move")
            if move not in MOVES:
                return Proposal("llama", None, model=self.model, input_sha=input_sha, output_sha=sha256_hex(raw), duration_ms=ms, error="move outside the schema")
            m = out.get("map") if isinstance(out.get("map"), dict) else None
            wait = ((m or {}).get("pending") or {}).get("wait_seconds")
            return Proposal(
                "llama", move, m, str(out.get("rationale", ""))[:600], [str(x) for x in (out.get("cites") or [])][:20],
                wait_seconds=int(wait) if isinstance(wait, (int, float)) else None,
                model=self.model, input_sha=input_sha, output_sha=sha256_hex(raw), duration_ms=ms,
            )
        except Exception as e:  # noqa: BLE001
            return Proposal("llama", None, model=self.model, input_sha=input_sha, duration_ms=int((time.monotonic() - started) * 1000), error=f"{type(e).__name__}: {e}")
