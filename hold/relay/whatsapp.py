"""HOLD Relay — WhatsApp Business Platform (Cloud API).

Sortant : la Decision Request part en message interactif (3 boutons au plus), avec repli
sur un modèle approuvé hors de la fenêtre de service de 24 h (erreur Meta 131047).
Entrant : webhook signé (X-Hub-Signature-256), lecture défensive, liaison stricte
mission / question / numéro de l'autorité, idempotence par identifiant de message Meta.
Un message « STOP » de l'autorité révoque ses missions actives.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
from dataclasses import dataclass
from typing import Any

import httpx

from ..profile import Authority
from .base import SendResult

DEFAULT_GRAPH_VERSION = "v26.0"
OUTSIDE_WINDOW = 131047
STOP_WORDS = {"stop", "revoquer", "révoquer", "revoke", "halt"}
BUTTON_RE = re.compile(r"^hold:([A-Za-z0-9_.-]{1,64}):(q_[0-9a-f]{12}):([A-Z_]{2,20})$")


@dataclass
class WhatsAppConfig:
    access_token: str = ""
    phone_number_id: str = ""
    app_secret: str = ""
    verify_token: str = ""
    graph_version: str = DEFAULT_GRAPH_VERSION
    api_base: str = "https://graph.facebook.com"
    template_name: str = ""
    template_lang: str = "fr"

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "WhatsAppConfig":
        e = env if env is not None else os.environ
        return cls(
            access_token=e.get("WHATSAPP_ACCESS_TOKEN", ""),
            phone_number_id=e.get("WHATSAPP_PHONE_NUMBER_ID", ""),
            app_secret=e.get("WHATSAPP_APP_SECRET", ""),
            verify_token=e.get("WHATSAPP_VERIFY_TOKEN", ""),
            graph_version=e.get("WHATSAPP_GRAPH_VERSION") or DEFAULT_GRAPH_VERSION,
            api_base=(e.get("WHATSAPP_API_BASE") or "https://graph.facebook.com").rstrip("/"),
            template_name=e.get("WHATSAPP_TEMPLATE_NAME", ""),
            template_lang=e.get("WHATSAPP_TEMPLATE_LANG") or "fr",
        )

    @property
    def can_send(self) -> bool:
        return bool(self.access_token and self.phone_number_id)

    @property
    def can_receive(self) -> bool:
        return bool(self.app_secret and self.verify_token)


# -- identifiants, masques -------------------------------------------------------------

def button_id(mission_id: str, question_id: str, code: str) -> str:
    return f"hold:{mission_id}:{question_id}:{code}"


def parse_button_id(value: str) -> tuple[str, str, str] | None:
    m = BUTTON_RE.match(value or "")
    return (m.group(1), m.group(2), m.group(3)) if m else None


def mask(digits: str | None) -> str:
    if not digits:
        return "—"
    return f"+{digits[:3]}{'•' * max(0, len(digits) - 7)}{digits[-4:]}"


def _clip(s: str, n: int) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


# -- messages sortants -------------------------------------------------------------------

def question_message(to: str, mission_id: str, mission_title: str, request: dict[str, Any], locale: str = "fr") -> dict[str, Any]:
    footer = "Votre choix repasse par le plancher HOLD." if locale == "fr" else "Your choice goes back through the HOLD floor."
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "header": {"type": "text", "text": _clip(f"HOLD · {mission_title}", 60)},
            "body": {"text": _clip(request["question"], 1024)},
            "footer": {"text": _clip(footer, 60)},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": button_id(mission_id, request["question_id"], o["code"]), "title": _clip(o["title"], 20)}}
                    for o in request["options"][:3]
                ]
            },
        },
    }


def template_message(to: str, cfg: WhatsAppConfig, mission_id: str, request: dict[str, Any]) -> dict[str, Any]:
    """Modèle approuvé : corps avec {{1}} et des réponses rapides dans l'ordre des options."""
    return {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "template",
        "template": {
            "name": cfg.template_name,
            "language": {"code": cfg.template_lang},
            "components": [
                {"type": "body", "parameters": [{"type": "text", "text": _clip(" ".join(request["question"].split()), 900)}]},
                *[
                    {"type": "button", "sub_type": "quick_reply", "index": str(i), "parameters": [{"type": "payload", "payload": button_id(mission_id, request["question_id"], o["code"])}]}
                    for i, o in enumerate(request["options"][:3])
                ],
            ],
        },
    }


def text_message(to: str, body: str) -> dict[str, Any]:
    return {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "text", "text": {"preview_url": False, "body": _clip(body, 4096)}}


class WhatsAppRelay:
    channel = "whatsapp"

    def __init__(self, config: WhatsAppConfig | None = None, *, simulate: bool = False, client: httpx.Client | None = None):
        self.config = config or WhatsAppConfig.from_env()
        self.simulate = simulate or not self.config.can_send
        self.mode = "simulated" if self.simulate else "cloud"
        self.client = client or httpx.Client(timeout=10)
        self.outbox: list[dict[str, Any]] = []  # en mode simulé, ce qui serait parti

    def available(self, authority: Authority) -> bool:
        return authority.channel == "whatsapp" and bool(authority.contact)

    def _post(self, body: dict[str, Any]) -> tuple[bool, str | None, int | None, str | None]:
        if self.simulate:
            mid = f"sim.{secrets.token_hex(8)}"
            self.outbox.append(body | {"_id": mid})
            return True, mid, None, None
        url = f"{self.config.api_base}/{self.config.graph_version}/{self.config.phone_number_id}/messages"
        try:
            r = self.client.post(url, headers={"authorization": f"Bearer {self.config.access_token}"}, json=body)
            data = r.json() if r.content else {}
        except Exception as e:  # noqa: BLE001
            return False, None, None, f"{type(e).__name__}: {e}"
        mid = ((data.get("messages") or [{}])[0] or {}).get("id")
        if r.status_code < 300 and mid:
            return True, mid, None, None
        err = data.get("error") or {}
        detail = " — ".join(x for x in (err.get("message"), (err.get("error_data") or {}).get("details")) if x)
        return False, None, err.get("code"), detail or f"HTTP {r.status_code}"

    def send_request(self, *, mission_id: str, mission_title: str, request: dict[str, Any], authority: Authority) -> SendResult:
        ok, mid, code, err = self._post(question_message(authority.contact, mission_id, mission_title, request, authority.locale))
        if not ok and code == OUTSIDE_WINDOW and self.config.template_name:
            ok, mid, code, err = self._post(template_message(authority.contact, self.config, mission_id, request))
            return SendResult(ok, self.mode, mid, None if ok else f"{code} {err}", via="template")
        if not ok and code == OUTSIDE_WINDOW:
            err = f"{err} (outside the 24 h window: the authority must first message the business number)"
        return SendResult(ok, self.mode, mid, None if ok else f"{code or '—'} {err}")

    def send_text(self, *, mission_id: str, authority: Authority, text: str) -> SendResult:
        ok, mid, code, err = self._post(text_message(authority.contact, text))
        return SendResult(ok, self.mode, mid, None if ok else f"{code or '—'} {err}", via="text")


# -- webhook entrant ----------------------------------------------------------------------

def verify_signature(raw: bytes, header: str | None, app_secret: str) -> bool:
    if not app_secret or not header:
        return False
    m = re.fullmatch(r"sha256=([0-9a-fA-F]{64})", header.strip())
    if not m:
        return False
    expected = hmac.new(app_secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, m.group(1).lower())


def parse_webhook(payload: Any) -> dict[str, list[dict[str, Any]]]:
    """Ne lève jamais : un webhook vide, partiel ou d'un autre produit donne des listes vides."""
    out: dict[str, list[dict[str, Any]]] = {"replies": [], "texts": [], "statuses": [], "others": []}
    if not isinstance(payload, dict) or payload.get("object") != "whatsapp_business_account":
        return out
    for entry in payload.get("entry") or []:
        for change in (entry or {}).get("changes") or [] if isinstance(entry, dict) else []:
            if not isinstance(change, dict) or change.get("field") != "messages":
                continue
            value = change.get("value") or {}
            for m in value.get("messages") or []:
                if not isinstance(m, dict) or not m.get("id") or not m.get("from"):
                    continue
                base = {"message_id": str(m["id"]), "from": str(m["from"]), "timestamp": str(m.get("timestamp", ""))}
                inter = m.get("interactive") or {}
                if m.get("type") == "interactive" and inter.get("type") == "button_reply" and (inter.get("button_reply") or {}).get("id"):
                    out["replies"].append(base | {"button": str(inter["button_reply"]["id"]), "title": str(inter["button_reply"].get("title", "")), "via": "interactive"})
                elif m.get("type") == "button" and (m.get("button") or {}).get("payload"):
                    out["replies"].append(base | {"button": str(m["button"]["payload"]), "title": str(m["button"].get("text", "")), "via": "template"})
                elif m.get("type") == "text" and (m.get("text") or {}).get("body"):
                    out["texts"].append(base | {"body": str(m["text"]["body"])[:500]})
                else:
                    out["others"].append(base | {"type": str(m.get("type", "?"))})
            for s in value.get("statuses") or []:
                if isinstance(s, dict) and s.get("id") and s.get("status"):
                    err = (s.get("errors") or [{}])[0] or {}
                    out["statuses"].append({"message_id": str(s["id"]), "status": str(s["status"]), "error_code": err.get("code"), "error": err.get("title") or err.get("message")})
    return out


def simulated_tap(sender: str, button: str, title: str) -> dict[str, Any]:
    """Le webhook que Meta enverrait pour un tap (mode simulé et tests)."""
    return {
        "object": "whatsapp_business_account",
        "entry": [{"id": "SIMULATED", "changes": [{"field": "messages", "value": {
            "messaging_product": "whatsapp",
            "messages": [{"from": sender, "id": f"sim.in.{secrets.token_hex(8)}", "timestamp": "0", "type": "interactive",
                          "interactive": {"type": "button_reply", "button_reply": {"id": button, "title": title}}}],
        }}]}],
    }


class Inbound:
    """Traite les webhooks pour un magasin donné. `habilitations` : missions → Habilitation qui les porte."""

    def __init__(self, store, resolve):
        self.store = store
        self.resolve = resolve  # mission_id -> Habilitation | None

    def _seen(self, message_id: str, mission_id: str, kind: str, mode: str, peer: str, detail: dict[str, Any]) -> bool:
        from ..store import now_iso

        try:
            with self.store.tx() as c:
                c.execute(
                    "INSERT INTO relay_messages(mission, direction, kind, mode, status, wa_message_id, peer, detail, created_at) VALUES(?,?,?,?,?,?,?,?,?)",
                    (mission_id, "IN", kind, mode, "RECEIVED", message_id, mask(peer), json.dumps(detail, ensure_ascii=False), now_iso()),
                )
            return False
        except Exception as e:  # noqa: BLE001
            if "UNIQUE" in str(e):
                return True
            raise

    def _mark(self, message_id: str, status: str, error: str | None = None) -> None:
        with self.store.tx() as c:
            c.execute("UPDATE relay_messages SET status=?, error=? WHERE wa_message_id=?", (status, error, message_id))

    def handle(self, payload: Any, *, mode: str = "cloud") -> dict[str, Any]:
        parsed = parse_webhook(payload)
        results = []
        for r in parsed["replies"]:
            results.append(self._reply(r, mode))
        for t in parsed["texts"]:
            results.append(self._text(t, mode))
        for s in parsed["statuses"]:
            with self.store.tx() as c:
                c.execute("UPDATE relay_messages SET status=?, error=COALESCE(?, error) WHERE wa_message_id=? AND direction='OUT'", (s["status"].upper(), s["error"], s["message_id"]))
        return {"results": results, "statuses": len(parsed["statuses"]), "ignored": len(parsed["others"])}

    def _reply(self, r: dict[str, Any], mode: str) -> dict[str, Any]:
        parsed = parse_button_id(r["button"])
        if not parsed:
            return {"message_id": r["message_id"], "accepted": False, "reason": "UNKNOWN_BUTTON"}
        mission_id, question_id, code = parsed
        hab = self.resolve(mission_id)
        if not hab:
            return {"message_id": r["message_id"], "accepted": False, "reason": "UNKNOWN_MISSION"}
        if self._seen(r["message_id"], mission_id, "ANSWER", mode, r["from"], {"question_id": question_id, "answer": code, "title": r["title"], "via": r["via"]}):
            return {"message_id": r["message_id"], "accepted": False, "duplicate": True, "reason": "ALREADY_PROCESSED"}
        mission = hab.mission(mission_id)
        authority = hab.profile.authority
        if authority.contact != r["from"]:
            with self.store.tx() as c:
                self.store.append(c, mission_id, "RELAY_REJECTED", "CORE", {"reason": "SENDER_NOT_AUTHORITY", "question_id": question_id, "answer": code, "from": mask(r["from"]), "message_id": r["message_id"], "channel": "whatsapp"})
            self._mark(r["message_id"], "REJECTED", "SENDER_NOT_AUTHORITY")
            return {"message_id": r["message_id"], "accepted": False, "reason": "SENDER_NOT_AUTHORITY"}
        outcome = mission.answer(question_id, code, channel="whatsapp", evidence={"message_id": r["message_id"], "from": mask(r["from"]), "mode": mode})
        self._mark(r["message_id"], "ACCEPTED" if outcome["accepted"] else "REJECTED", None if outcome["accepted"] else outcome["reason"])
        fr = authority.locale == "fr"
        if outcome["accepted"]:
            ack = (f"HOLD · réponse enregistrée : {code}. " + ("Aucune seconde exécution ne sera faite." if fr else "Nothing will be executed twice.")) if fr else f"HOLD · answer recorded: {code}. Nothing will be executed twice."
        else:
            why = {
                "QUESTION_EXPIRED": ("cette question a expiré", "this question has expired"),
                "PROFILE_CHANGED": ("l'habilitation a changé de révision depuis la question", "the habilitation changed revision since the question"),
                "CONDITIONS_CHANGED": ("la situation a changé depuis la question", "the situation changed since the question"),
            }.get(outcome.get("reason"), ("cette question n'est plus ouverte", "this question is no longer open"))
            ack = f"HOLD · {why[0]} ; aucune action n'a été effectuée." if fr else f"HOLD · {why[1]}; nothing was done."
        mission.notify(ack, kind="ACK")
        return {"message_id": r["message_id"], "accepted": outcome["accepted"], "reason": outcome.get("reason"), "mission": mission_id}

    def _text(self, t: dict[str, Any], mode: str) -> dict[str, Any]:
        word = t["body"].strip().lower().strip(".!")
        if word not in STOP_WORDS:
            return {"message_id": t["message_id"], "accepted": False, "reason": "TEXT_IGNORED"}
        revoked = []
        for row in self.store.read("SELECT id FROM missions WHERE status IN ('OPEN','HELD','AWAITING_AUTHORITY')"):
            hab = self.resolve(row["id"])
            if hab and hab.profile.authority.contact == t["from"] and hab.profile.authority.can_revoke:
                if not self._seen(f"{t['message_id']}:{row['id']}", row["id"], "REVOKE", mode, t["from"], {"text": word}):
                    m = hab.mission(row["id"])
                    m.revoke(by=hab.profile.authority.name, channel="whatsapp")
                    m.notify("HOLD · mission révoquée depuis WhatsApp. Plus aucune action ne sera faite." if hab.profile.authority.locale == "fr" else "HOLD · mission revoked from WhatsApp. No further action will be taken.", kind="ACK")
                    revoked.append(row["id"])
        return {"message_id": t["message_id"], "accepted": bool(revoked), "reason": "REVOKED" if revoked else "NO_ACTIVE_MISSION", "missions": revoked}
