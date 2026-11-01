"""Profil d'habilitation (habilitation/v1).

Le profil est l'objet portable : il dit ce que l'agent peut faire seul, dans quelles
limites, qui est l'autorité responsable et comment elle révoque. Il est produit par
n'importe quel outil (HOLD Studio, éditeur, CI) et lu par la bibliothèque.

Les invariants du plancher ne figurent pas dans le profil : ils ne se configurent pas.
"""

from __future__ import annotations

import hmac
import json
from datetime import date, datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .canonical import canonical_json, sha256_hex

SCHEMA_VERSION = "habilitation/v1"
PathName = Literal["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "PRESERVE"]
IDENT = r"^[a-z][a-z0-9_]{0,63}$"


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Money(_Model):
    amount: float = Field(gt=0)
    currency: str = Field(pattern=r"^[A-Z]{3}$")


class DateWindow(_Model):
    start: date
    end: date

    @model_validator(mode="after")
    def _ordered(self) -> "DateWindow":
        if self.start > self.end:
            raise ValueError("The window starts after it ends")
        return self


class EffectEnvelope(_Model):
    """Bornes sur le contenu d'un effet : ce qui rend le pouvoir délégué fini."""

    max_amount: Money | None = None
    amount_field: str = "price"
    currency_field: str = "currency"
    allowed: dict[str, list[str]] = Field(default_factory=dict)
    windows: dict[str, DateWindow] = Field(default_factory=dict)
    required: list[str] = Field(default_factory=list)


class Reconciliation(_Model):
    """Comment lever l'incertitude d'un effet sans le répéter."""

    status_action: str = Field(pattern=IDENT)
    max_checks: int = Field(default=3, ge=1, le=20)
    sigma: float = Field(default=0.9, ge=0, le=1, description="Chance qu'un contrôle rétablisse la cohérence")
    decay: float = Field(default=0.6, ge=0, le=1, description="σ est multiplié par ce facteur après chaque « introuvable »")


class Power(_Model):
    action: str = Field(pattern=IDENT)
    kind: Literal["read", "effect", "status", "contact"]
    description: str = ""
    budget: int | None = Field(default=None, ge=0, description="Nombre maximal d'exécutions ; vide = illimité")
    envelope: EffectEnvelope | None = None
    identity: list[str] = Field(default_factory=list, description="Attributs qui identifient un effet équivalent")
    reconciliation: Reconciliation | None = None
    depends_on: list[str] = Field(default_factory=list, description="Effets qui doivent être confirmés avant cette action")
    role: Literal["support"] | None = None
    sigma: float | None = Field(default=None, ge=0, le=1)
    provenance: list[str] = Field(default_factory=list, description="Attributes that must come, together, from one record an earlier tool result returned (I6). 'attr' or 'attr=field' when the record names it differently")
    provenance_source: list[str] = Field(default_factory=list, description="Tools whose results may justify these attributes (I6). Empty: any tool result, i.e. coherence between attributes only")
    max_corrections: int = Field(default=2, ge=0, le=10, description="Corrected requests allowed after refusals that produced no effect")

    @field_validator("provenance")
    @classmethod
    def _provenance(cls, v: list[str]) -> list[str]:
        import re as _re

        for item in v:
            if not _re.fullmatch(r"[a-z][a-z0-9_]*(=[a-z][a-z0-9_]*)?", item):
                raise ValueError(f"provenance entry {item!r}: 'attribute' or 'attribute=field'")
        return v

    @field_validator("provenance_source")
    @classmethod
    def _sources(cls, v: list[str]) -> list[str]:
        import re as _re

        for item in v:
            if not _re.fullmatch(IDENT, item):
                raise ValueError(f"provenance source {item!r} is not a tool name")
        return v

    @model_validator(mode="after")
    def _coherent(self) -> "Power":
        if self.kind == "effect":
            if not self.identity:
                raise ValueError(f"{self.action}: an effect must declare its identity attributes")
        else:
            if self.envelope or self.reconciliation or self.identity:
                raise ValueError(f"{self.action}: envelope, identity and reconciliation are reserved to effects")
        if self.role == "support" and self.kind != "contact":
            raise ValueError(f"{self.action}: the support role requires a contact power")
        return self


class Authority(_Model):
    """L'autorité responsable (Accountable Authority) : elle tranche et révoque."""

    name: str = Field(min_length=1, max_length=80)
    channel: Literal["whatsapp", "console"] = "whatsapp"
    contact: str | None = Field(default=None, description="Numéro international, chiffres seuls (format wa_id)")
    sigma: float = Field(default=0.95, ge=0, le=1)
    can_revoke: bool = True
    max_extra_checks: int = Field(default=1, ge=0, le=5, description="Contrôles supplémentaires accordables par Decision Request")
    max_requests: int = Field(default=2, ge=0, le=10, description="Decision Requests au plus par mission : l'autorité n'est pas un recours par défaut")
    question_ttl_minutes: int = Field(default=60, ge=1, le=10080, description="A Decision Request left unanswered expires after this delay; a later tap is refused")
    grant_ttl_minutes: int = Field(default=15, ge=1, le=1440, description="A one-time approval must be used within this delay, under the same profile revision")
    locale: Literal["fr", "en"] = "fr"

    @field_validator("contact")
    @classmethod
    def _digits(cls, v: str | None) -> str | None:
        if v is None:
            return v
        d = v.replace(" ", "").replace("-", "").lstrip("+")
        if d.startswith("00"):
            d = d[2:]
        if not d.isdigit() or not (8 <= len(d) <= 15) or d.startswith("0"):
            raise ValueError("Invalid international number")
        return d

    @model_validator(mode="after")
    def _reachable(self) -> "Authority":
        if self.channel == "whatsapp" and not self.contact:
            raise ValueError("The WhatsApp channel requires the authority's number")
        return self


class Hesitation(_Model):
    """Pass / fail / hold, puis hold → discernement."""

    tau_star: float = Field(default=0.5, gt=0, le=1, description="Seuil de cohérence à rétablir")
    ladder: list[PathName] = Field(default_factory=lambda: ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "PRESERVE"])
    on_refused_proposal: Literal["reference", "preserve"] = "reference"
    autonomous_resume: bool = True
    max_rounds: int = Field(default=8, ge=1, le=50)
    envelope_breach: Literal["refuse", "ask_authority"] = "refuse"
    map_retries: int = Field(default=1, ge=0, le=3, description="Times a refused reading or move is handed back to the model before the reference applies")
    max_wait_seconds: int = Field(default=900, ge=0, le=86400, description="Patience: total waiting allowed per unknown effect (0 disables WAIT)")
    max_waits: int = Field(default=2, ge=0, le=10, description="Waits at most per unknown effect")

    @field_validator("ladder")
    @classmethod
    def _ladder(cls, v: list[str]) -> list[str]:
        if len(set(v)) != len(v):
            raise ValueError("Duplicate path in the ladder")
        if not v or v[-1] != "PRESERVE":
            raise ValueError("PRESERVE must be the last path of the ladder")
        return v


class Signature(_Model):
    method: Literal["hmac-sha256"] = "hmac-sha256"
    by: str
    value: str = Field(pattern=r"^[0-9a-f]{64}$")


class Profile(_Model):
    hold: Literal["habilitation/v1"] = SCHEMA_VERSION
    id: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    title: str = Field(min_length=1, max_length=120)
    description: str = ""
    revision: int = Field(default=1, ge=1)
    supersedes: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    issued_at: datetime
    expires_at: datetime | None = None
    authority: Authority
    powers: list[Power] = Field(min_length=1)
    hesitation: Hesitation = Field(default_factory=Hesitation)
    signature: Signature | None = None

    @model_validator(mode="after")
    def _references(self) -> "Profile":
        names = [p.action for p in self.powers]
        if len(set(names)) != len(names):
            raise ValueError("Two powers share the same action")
        by = {p.action: p for p in self.powers}
        for p in self.powers:
            if p.reconciliation:
                s = by.get(p.reconciliation.status_action)
                if not s or s.kind != "status":
                    raise ValueError(f"{p.action}: reconciliation requires a status power named {p.reconciliation.status_action}")
            for d in p.depends_on:
                if d not in by or by[d].kind != "effect":
                    raise ValueError(f"{p.action}: unknown dependency, or not an effect ({d})")
        if sum(1 for p in self.powers if p.role == "support") > 1:
            raise ValueError("Only one power can hold the support role")
        if self.expires_at and self.expires_at <= self.issued_at:
            raise ValueError("The profile expires before it is issued")
        return self

    # -- lecture -------------------------------------------------------------
    def power(self, action: str) -> Power | None:
        return next((p for p in self.powers if p.action == action), None)

    @property
    def support(self) -> Power | None:
        return next((p for p in self.powers if p.role == "support"), None)

    def body(self) -> dict:
        """Contenu signé : tout le profil sauf la signature."""
        return self.model_dump(mode="json", exclude={"signature"}, exclude_none=True)

    @property
    def fingerprint(self) -> str:
        return sha256_hex(canonical_json(self.body()))

    def expired(self, now: datetime | None = None) -> bool:
        now = now or datetime.now(timezone.utc)
        return bool(self.expires_at and _aware(self.expires_at) <= _aware(now))


def _aware(d: datetime) -> datetime:
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# -- signature et révisions -----------------------------------------------------

def sign(profile: Profile, secret: str, by: str) -> Profile:
    mac = hmac.new(secret.encode(), canonical_json(profile.body()).encode(), sha256).hexdigest()
    return profile.model_copy(update={"signature": Signature(by=by, value=mac)})


def signature_valid(profile: Profile, secret: str) -> bool:
    if not profile.signature:
        return False
    mac = hmac.new(secret.encode(), canonical_json(profile.body()).encode(), sha256).hexdigest()
    return hmac.compare_digest(mac, profile.signature.value)


def revise(profile: Profile, **changes) -> Profile:
    """Nouvelle révision : numéro + 1, liée à l'empreinte précédente, non signée."""
    data = profile.model_dump(mode="python", exclude={"signature"})
    data.update(changes)
    data["revision"] = profile.revision + 1
    data["supersedes"] = profile.fingerprint
    data["issued_at"] = datetime.now(timezone.utc)
    return Profile.model_validate(data)


# -- fichiers ----------------------------------------------------------------------

def load_profile(source: str | Path | dict, *, secret: str | None = None, require_signature: bool = False) -> Profile:
    if isinstance(source, dict):
        data = source
    else:
        text = Path(source).read_text(encoding="utf-8")
        data = json.loads(text) if str(source).endswith(".json") else yaml.safe_load(text)
    profile = Profile.model_validate(data)
    if require_signature or (secret and profile.signature):
        if not secret or not signature_valid(profile, secret):
            raise ValueError("Profile signature missing or invalid")
    return profile


def dump_profile(profile: Profile, path: str | Path) -> None:
    data = profile.model_dump(mode="json", exclude_none=True)
    p = Path(path)
    if p.suffix == ".json":
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    else:
        p.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def json_schema() -> dict:
    return Profile.model_json_schema()
