# HOLD — prêt-à-porter habilitation for autonomous agents

An agent that books, pays or sends things will, sooner or later, face an action whose result is **unknown**: the request left, the answer never came. Most agents then guess — they retry and book twice, or they declare success without proof.

**HOLD is an habilitation the agent wears.** You attach a portable profile to your agent; every tool call goes through it.

- **The profile** (`habilitation/v1`, a YAML/JSON file) says what the agent may do on its own: its powers, the envelope of each effect (amount ceiling, allowed providers, date windows), budgets, the threshold τ\* of coherence to restore, and who the accountable authority is — and how she revokes.
- **The floor** guarantees what can never happen, whatever the model proposes: no equivalent effect twice, no success without correlated evidence, no dependent action while an effect is unknown, no power beyond the profile, no decision on a stale position.
- **Hesitation** turns "unknown" into discernment instead of guessing: *pass / fail / hold, then hold → discernment*. The agent verifies by itself, contacts support, and only asks the authority when coherence cannot be restored within its habilitation. It resumes autonomously as soon as correlated evidence arrives.
- **HOLD Relay** carries the Decision Request to the authority on **WhatsApp** (interactive buttons). The tap comes back through a signed webhook and the same floor. "STOP" revokes the mission from the phone.
- **The journal** is hash-chained with a versioned event envelope (`hold.event/v1`): every mission can be verified and replayed — state, every floor decision, and counterfactual policies — without calling the model or the provider again.

The discerner is **Llama 3.2 (Meta open weights)**, served locally by Ollama. The model proposes; the floor admits. A weak model makes the agent less skilful, never unsafe — and the journal measures it.

## Quick start

```bash
pip install -e ".[server]"
hold demo --baseline                  # without HOLD: 2 bookings for one stay; with HOLD: 1, and the authority decides on WhatsApp (simulated)
hold demo --scenario lost-response    # the agent reconciles alone: verify, verify, receipt, resume
hold demo --scenario support          # the provider's support finds it (correlated reply)
hold demo --scenario never            # never visible: the authority closes it unconfirmed
hold demo --scenario envelope         # only hotel above the ceiling: Decision Request, approve once
```

### With Llama (Meta open weights)

```bash
ollama pull llama3.2:3b
hold demo --agent llama --discerner llama --scenario authority
hold qualify --discerner llama        # Llama's choices vs the reference, decision by decision
```

### With real WhatsApp (Cloud API)

```bash
export WHATSAPP_ACCESS_TOKEN=... WHATSAPP_PHONE_NUMBER_ID=... WHATSAPP_APP_SECRET=... WHATSAPP_VERIFY_TOKEN=...
hold demo --relay whatsapp --contact 2126XXXXXXXX --serve 8787 --scenario authority
# expose http://localhost:8787 over HTTPS (e.g. cloudflared) and set the webhook to /api/whatsapp/webhook (field: messages)
# send any message to the business number first: it opens the 24 h window for interactive messages
```

## Wear it on your own agent

```python
from hold import Habilitation, Outcome, LlamaDiscerner
from hold.relay.whatsapp import WhatsAppRelay

hab = Habilitation("my-profile.hold.yaml", store="hold.db", discerner=LlamaDiscerner(), relay=WhatsAppRelay())

@hab.effect("create_booking")
def create_booking(hotel_id: str, check_in: str, check_out: str, traveler: str, price: float, currency: str, operation_id: str = ""):
    ...  # call the real provider; return Outcome.confirmed(receipt) / Outcome.rejected(reason), or let a timeout raise

@hab.status("check_booking")
def check_booking(operation_id: str):
    ...  # Outcome.confirmed(receipt, operation_id=...) or Outcome.not_found()

mission = hab.open_mission("Trip to Lyon")
tools = mission.tools()            # tool specs for Llama / OpenAI-style tool calling
result = mission.dispatch(name, arguments)   # every call goes through the floor
```

## Verify and replay

```bash
hold profile validate hold/profiles/reservation.hold.yaml
hold journal verify hold.db <mission>      # chain + state replay + decision replay
hold journal policy hold.db <mission> --tau 0.3   # what the reference would have chosen under another policy
hold journal export hold.db <mission> --out mission.json
```

## Tests

```bash
pip install -e ".[server,dev]" && pytest
```

Status: research prototype built for the Meta hackathon. The provider in the demo is **synthetic** (no real booking is made). See `docs/` for the design notes.
