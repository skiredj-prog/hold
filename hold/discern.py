"""Discerneurs : qui propose un chemin quand la cohérence est menacée.

Le discerneur propose, le plancher admet. Un discerneur faible ne rend pas le système
dangereux ; il le rend moins habile, et le journal le mesure (concordance, sur-sollicitation).
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any, Protocol

import httpx

from .canonical import canonical_json, sha256_hex

PATHS = ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "PRESERVE"]
EFFECT_STATES = ["DISPATCHING", "UNKNOWN", "CONFIRMED", "FAILED_PROVEN"]


@dataclass
class Proposal:
    discerner: str
    path: str | None
    perceived_state: str | None = None
    paths_reaching_tau: list[str] | None = None
    rationale: str = ""
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
    """Politique de référence (bras de contrôle) : recopie la règle de moindre autorité. Ce n'est pas un modèle."""

    name = "reference"

    def propose(self, position: dict[str, Any], context: dict[str, Any]) -> Proposal:
        reaching = [p["name"] for p in position["paths"] if p["open"] and p["reaches_tau"]]
        return Proposal("reference", position["reference"], position["effect_state"], reaching, "Least-authority rule (authority directive first).")


SYSTEM_PROMPT = """You are the hesitation layer of an autonomous agent. An external action may have happened, but its result is unknown. You never act yourself: you choose one path, and a deterministic floor admits or refuses it.

Definitions:
- effect_state UNKNOWN: a request was sent, the result is not proven. A timeout does not prove failure. "Not found" does not prove failure.
- coherence: 1 when the truth of the effect is known, 0 while it is unknown. tau_star is the threshold to restore.
- Each path has: open (allowed right now, with the reason), sigma (chance to restore coherence), authority (who is mobilized: agent < mandate < authority).
- A path "reaches tau" when it is open and sigma >= tau_star.

Procedure, in order:
0. If an open path has directed=true, the accountable authority explicitly granted it: choose it.
1. Read effect_state from the facts.
2. List the paths that are open AND have sigma >= tau_star.
3. Choose the first of them in this order: VERIFY, CONTACT_SUPPORT, ASK_AUTHORITY. Never choose a closed path. Never ask the authority while VERIFY or CONTACT_SUPPORT can still reach tau.
4. If no path reaches tau, choose PRESERVE.
Never propose to recreate the action, to declare success, or to run dependent actions.

Answer only with the JSON object required by the schema. rationale: one short sentence citing the fields you used (open, sigma, authority)."""

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "perceived_state": {"type": "string", "enum": EFFECT_STATES},
        "paths_reaching_tau": {"type": "array", "items": {"type": "string", "enum": PATHS}},
        "choice": {"type": "string", "enum": PATHS},
        "rationale": {"type": "string"},
    },
    "required": ["perceived_state", "paths_reaching_tau", "choice", "rationale"],
}


def model_view(position: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    """Ce que le modèle voit : les faits et la frontière, pas la référence calculée."""
    return {
        "mission": context.get("mission_title"),
        "impasse": {
            "action": position["action"],
            "operation_id": position["operation_id"],
            "observations": position["observations"],
        },
        "effect_state": position["effect_state"],
        "coherence": position["coherence"],
        "tau_star": position["tau_star"],
        "paths": [{k: p[k] for k in ("name", "open", "reason", "sigma", "authority", "directed")} for p in position["paths"]],
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
        input_sha = sha256_hex(canonical_json(view))
        started = time.monotonic()
        try:
            r = httpx.post(
                f"{self.base_url}/api/chat",
                timeout=self.timeout_s,
                json={
                    "model": self.model,
                    "stream": False,
                    "format": OUTPUT_SCHEMA,
                    "options": self.options,
                    "messages": [
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": "Facts (JSON):\n" + json.dumps(view, ensure_ascii=False, indent=1)},
                    ],
                },
            )
            data = r.json()
            raw = (data.get("message") or {}).get("content", "")
            ms = int((time.monotonic() - started) * 1000)
            if r.status_code != 200 or data.get("error"):
                return Proposal("llama", None, model=self.model, input_sha=input_sha, duration_ms=ms, error=str(data.get("error") or r.status_code))
            out = json.loads(raw)
            choice = out.get("choice")
            if choice not in PATHS:
                return Proposal("llama", None, model=self.model, input_sha=input_sha, output_sha=sha256_hex(raw), duration_ms=ms, error="choice outside the schema")
            return Proposal(
                "llama", choice, out.get("perceived_state"), list(out.get("paths_reaching_tau") or []), str(out.get("rationale", ""))[:500],
                model=self.model, input_sha=input_sha, output_sha=sha256_hex(raw), duration_ms=ms,
            )
        except Exception as e:  # noqa: BLE001
            return Proposal("llama", None, model=self.model, input_sha=input_sha, duration_ms=int((time.monotonic() - started) * 1000), error=f"{type(e).__name__}: {e}")
