/* HOLD Studio — logique pure, sans interface.
 * Mêmes règles que la bibliothèque Python : forme canonique, empreinte SHA-256, vérification
 * de la chaîne du journal, rejeu des décisions du plancher, validation du profil, aperçu de
 * l'hésitation. Tout s'exécute dans le navigateur, indépendamment de la bibliothèque.
 */
(function (root) {
  "use strict";

  const ENVELOPE_VERSION = "hold.event/v1";
  const GENESIS = "0".repeat(64);
  const LADDER_AUTHORITY = { VERIFY: "agent", CONTACT_SUPPORT: "mandate", ASK_AUTHORITY: "authority", WAIT: "none", PRESERVE: "none" };
  const PHASES = ["RESSENTIR", "CAPTER", "PERCEVOIR", "DISCERNER", "RELIER", "CHOISIR", "ENGAGER", "REEVALUER"];
  const PENDING_CONDITIONS = ["VISIBILITY_DELAY", "SUPPORT_REPLY"];
  const INSIDE = ["VERIFY", "CONTACT_SUPPORT"];
  const UNDETERMINED_SCOPE = ["VERIFY", "WAIT", "PRESERVE"];
  const MIN_QUOTE = 8;
  const EVENT_TYPES = {
    MISSION_OPENED: ["title"], PROFILE_ATTACHED: ["fingerprint", "revision", "profile"], RUNTIME_BOUND: ["core", "discerner", "tools"],
    ACTION_ADMITTED: ["action", "kind"], ACTION_REFUSED: ["action", "rule", "reason"], ACTION_RESULT: ["action", "status"],
    EFFECT_RESERVED: ["action", "effect_key", "operation_id", "attributes"], EFFECT_OUTCOME: ["effect_key", "operation_id", "status"],
    HOLD_ENTERED: ["effect_key", "operation_id", "scope"], POSITION: ["round", "effect_key", "position"],
    PROPOSAL: ["round", "attempt", "discerner", "position_seq", "proposal"],
    DECISION: ["round", "attempt", "position_seq", "proposal", "read_digest", "now_digest", "final", "outcome", "verdict"],
    WAIT_STARTED: ["effect_key", "seconds", "until", "condition"], WAIT_ENDED: ["effect_key"],
    PATH_EXECUTED: ["round", "path", "result"], EVIDENCE: ["effect_key", "source", "kind"], DECISION_REQUEST: ["question_id", "kind", "options"],
    AUTHORITY_ANSWER: ["question_id", "answer", "channel"], AUTHORITY_GRANT: ["grant"], REQUEST_CLOSED: ["question_id", "reason", "status", "mission_status"],
    RECONCILED: ["effect_key", "receipt"], RESUMED: ["effect_key"], PRESERVED: ["effect_key", "reason"], REVOKED: ["by", "channel"],
    RELAY_SENT: ["question_id", "channel", "mode"], RELAY_FAILED: ["channel", "error"], RELAY_REJECTED: ["reason"], MISSION_CLOSED: ["outcome"],
  };

  // -- forme canonique (identique à hold/canonical.py) -------------------------------------
  function normalize(v) {
    if (v === null || v === undefined) return undefined;
    if (typeof v === "number") { if (!Number.isFinite(v)) throw new Error("Nombre non fini"); return v; }
    if (typeof v === "string" || typeof v === "boolean") return v;
    if (Array.isArray(v)) return v.map((x) => { const n = normalize(x); return n === undefined ? null : n; });
    if (typeof v === "object") {
      const out = {};
      for (const k of Object.keys(v)) { const n = normalize(v[k]); if (n !== undefined) out[k] = n; }
      return out;
    }
    throw new Error("Type non canonicalisable");
  }
  function serialize(v) {
    if (v === null) return "null";
    if (Array.isArray(v)) return "[" + v.map(serialize).join(",") + "]";
    if (typeof v === "object") return "{" + Object.keys(v).sort().map((k) => JSON.stringify(k) + ":" + serialize(v[k])).join(",") + "}";
    return JSON.stringify(v);
  }
  const canonical = (v) => serialize(normalize(v) ?? null);

  async function sha256Hex(text) {
    const subtle = (root.crypto && root.crypto.subtle) || (typeof require === "function" ? require("crypto").webcrypto.subtle : null);
    const buf = await subtle.digest("SHA-256", new TextEncoder().encode(text));
    return Array.from(new Uint8Array(buf)).map((b) => b.toString(16).padStart(2, "0")).join("");
  }
  const digest = (v) => sha256Hex(canonical(v));

  function profileBody(p) { const { signature, ...body } = p; return body; }
  const fingerprint = (p) => digest(profileBody(p));

  // -- journal ------------------------------------------------------------------------------
  async function verifyChain(events) {
    let prev = GENESIS;
    for (let i = 0; i < events.length; i++) {
      const e = events[i];
      const { hash, ...unsigned } = e;
      if (e.v !== ENVELOPE_VERSION) return { valid: false, count: events.length, brokenAt: e.seq, reason: "unknown envelope version" };
      if (e.seq !== i + 1) return { valid: false, count: events.length, brokenAt: e.seq, reason: "non-contiguous sequence" };
      if (e.prev !== prev) return { valid: false, count: events.length, brokenAt: e.seq, reason: "broken chain" };
      if ((await digest(unsigned)) !== hash) return { valid: false, count: events.length, brokenAt: e.seq, reason: "invalid hash" };
      const missing = (EVENT_TYPES[e.type] || null);
      if (!missing) return { valid: false, count: events.length, brokenAt: e.seq, reason: "unknown type " + e.type };
      if (missing.some((k) => !(k in (e.data || {})))) return { valid: false, count: events.length, brokenAt: e.seq, reason: "missing fields" };
      if (e.phase != null && !PHASES.includes(e.phase)) return { valid: false, count: events.length, brokenAt: e.seq, reason: "unknown phase " + e.phase };
      prev = hash;
    }
    return { valid: true, count: events.length, head: prev };
  }

  function referencePath(paths, ladder) {
    const directed = paths.find((p) => p.open && p.directed);
    if (directed) return directed.name;
    for (const name of ladder) { const p = paths.find((x) => x.name === name); if (p && p.open && p.reaches_tau) return name; }
    return "PRESERVE";
  }

  // -- the gate (identical to hold/hesitation.py and hold/floor.py) ------------------------
  const names = (v) => Array.isArray(v) ? [...new Set(v.filter((x) => typeof x === "string"))].sort() : [];
  const isObj = (v) => v !== null && typeof v === "object" && !Array.isArray(v);
  const str = (v) => (typeof v === "string" ? v : "nothing");
  function trueMap(position) {
    const paths = position.paths;
    return {
      effect_state: position.effect_state,
      open_paths: paths.filter((p) => p.open && p.name !== "PRESERVE").map((p) => p.name).sort(),
      reaching_tau: paths.filter((p) => p.open && p.reaches_tau).map((p) => p.name).sort(),
      pending: { condition: "NONE" },
    };
  }
  function pendingOf(m) {
    const p = isObj(m) ? m.pending : null;
    return isObj(p) && ![undefined, null, "", "NONE"].includes(p.condition) ? p : null;
  }
  function norm(s) {
    return (s || "").normalize("NFKC").toLowerCase().replace(/\s+/g, " ").replace(/^[ ."']+|[ ."']+$/g, "");
  }
  function checkMap(declared, position) {
    if (!isObj(declared)) return { reading: null, claims: {}, discrepancies: ["no reading declared"] };
    const truth = trueMap(position); const out = []; const claims = {};
    claims.effect_state = declared.effect_state === truth.effect_state ? "SUPPORTED" : "CONTRADICTED";
    if (declared.effect_state !== truth.effect_state) out.push(`effect_state is ${truth.effect_state}, not ${str(declared.effect_state)}`);
    for (const [k, label] of [["open_paths", "open paths"], ["reaching_tau", "paths reaching tau*"]]) {
      const got = names(declared[k]);
      const same = got.join(",") === truth[k].join(",");
      claims[k] = same ? "SUPPORTED" : "CONTRADICTED";
      if (!same) out.push(`${label} are ${truth[k].join(", ") || "none"}, not ${got.join(", ") || "none"}`);
    }
    let grounded = null;
    const p = pendingOf(declared);
    if (p) {
      const cited = str(p.evidence);
      const o = typeof p.evidence === "string" ? (position.observations || []).find((x) => x.id === p.evidence) : null;
      const quote = norm(typeof p.quote === "string" ? p.quote : "");
      if (!PENDING_CONDITIONS.includes(p.condition)) { out.push(`pending condition ${str(p.condition)} is not a known condition`); grounded = false; }
      else if (!o) { out.push(`the cited evidence ${cited} does not exist`); grounded = false; }
      else if (quote.length < MIN_QUOTE || !norm(o.text || "").includes(quote)) { out.push(`the cited evidence ${cited} does not contain the quoted passage`); grounded = false; }
      else grounded = true;
      claims.pending = grounded ? "UNDETERMINED" : "CONTRADICTED";
    }
    const values = Object.values(claims);
    const reading = values.includes("CONTRADICTED") ? "CONTRADICTED" : values.includes("UNDETERMINED") ? "UNDETERMINED" : "SUPPORTED";
    return { reading, claims, discrepancies: out };
  }
  function justifiedMoves(declared, position) {
    const directed = position.paths.filter((p) => p.open && p.directed).map((p) => p.name);
    if (directed.length) return directed.slice(0, 1);
    const m = isObj(declared) ? declared : {};
    const reach = new Set(names(m.reaching_tau));
    const moves = INSIDE.filter((n) => reach.has(n));
    if (pendingOf(m) && names(m.open_paths).includes("WAIT")) moves.push("WAIT");
    if (!moves.length && reach.has("ASK_AUTHORITY")) moves.push("ASK_AUTHORITY");
    return moves.length ? moves : ["PRESERVE"];
  }
  const admissibleMoves = (position) => position.paths.filter((p) => p.open).map((p) => p.name);
  const seconds = (v) => (typeof v === "number" && Number.isFinite(v) && Math.abs(v) <= 1e9 ? Math.trunc(v) : null);

  function verdict(outcome, proposed, applied, reference, credit, rule, reason, reading, claims, coherent, admissible, justified, supported, discrepancies, wait) {
    return { outcome, proposed, applied, reference, credit, rule, reason, reading, claims, coherent, admissible, justified, supported, discrepancies, wait_seconds: wait ?? null };
  }
  function admitProposal(proposal, position, readDigest, nowDigest, final, onRefused) {
    const ref = position.reference; const p = proposal || {}; const move = p.move ?? null;
    const admissible = admissibleMoves(position);
    const justified = justifiedMoves(trueMap(position), position);
    if (readDigest !== nowDigest) return verdict("STALE", move, null, ref, null, "I5", "The position changed during deliberation: re-read before acting.", null, {}, null, admissible, justified, [], []);
    const mc = checkMap(p.map, position);
    const supported = justifiedMoves(p.map, position);
    const coherent = move ? supported.includes(move) : null;
    let outcome, rule, reason;
    if (!move) { outcome = "NO_PROPOSAL"; rule = "D1/NONE"; reason = "No usable proposal" + (p.error ? ` (${p.error})` : "") + "."; }
    else if (mc.reading === null || mc.reading === "CONTRADICTED") { outcome = "MAP_REFUSED"; rule = "D1/MAP"; reason = "The declared reading is contradicted by the available evidence: " + mc.discrepancies.join("; ") + "."; }
    else if (mc.reading === "UNDETERMINED" && !UNDETERMINED_SCOPE.includes(move)) { outcome = "MAP_REFUSED"; rule = "D1/SCOPE"; reason = `The reading is undetermined, which is enough only for bounded moves (${UNDETERMINED_SCOPE.join(", ")}), not for ${move}.`; }
    else if (!admissible.includes(move)) {
      const path = position.paths.find((x) => x.name === move);
      outcome = "MOVE_REFUSED"; rule = "I4/PATH"; reason = `Path ${move} is closed: ${path ? path.reason : "unknown path"}.`;
    } else if (!coherent) { outcome = "INCOHERENT"; rule = "D1/COHERENCE"; reason = `${move} does not follow from the declared reading, which supports ${supported.join(", ")}.`; }
    else {
      let wait = null;
      if (move === "WAIT") {
        const left = position.paths.find((x) => x.name === "WAIT").left || 0;
        const asked = seconds(p.wait_seconds) || seconds((pendingOf(p.map) || {}).wait_seconds) || left;
        wait = Math.max(1, Math.min(asked, left));
      }
      const how = mc.reading === "SUPPORTED" ? "supported by the evidence" : "that the evidence does not contradict (part of it is undetermined)";
      return verdict("ADMITTED", move, move, ref, "discerner", "ADMIT", `${move} is open and follows from a reading ${how}.`, mc.reading, mc.claims, true, admissible, justified, supported, [], wait);
    }
    if (!final) return verdict(outcome, move, null, ref, null, rule, reason, mc.reading, mc.claims, coherent, admissible, justified, supported, mc.discrepancies);
    const applied = onRefused === "reference" ? ref : "PRESERVE";
    return verdict("REFERENCE_APPLIED", move, applied, ref, "floor", rule, `${reason} Retries exhausted: the floor applies ${applied} (credited to the floor, not to the model).`, mc.reading, mc.claims, coherent, admissible, justified, supported, mc.discrepancies);
  }

  function decisionReplay(events, profiles) {
    let checked = 0; const mismatches = []; const positions = {};
    for (const e of events) {
      if (e.type === "POSITION") { positions[e.seq] = e.data.position; continue; }
      if (e.type !== "DECISION") continue;
      const prof = profiles[e.profile];
      const ladder = prof ? prof.hesitation.ladder : ["VERIFY", "CONTACT_SUPPORT", "ASK_AUTHORITY", "PRESERVE"];
      const d = e.data; const pos = positions[d.position_seq];
      if (!pos) { mismatches.push({ seq: e.seq, round: d.round, error: "position missing" }); continue; }
      const ref = referencePath(pos.paths, ladder);
      const v = admitProposal(d.proposal, pos, d.read_digest, d.now_digest, d.final, d.on_refused || (prof ? prof.hesitation.on_refused_proposal : "reference"));
      checked++;
      if (ref !== pos.reference || canonical(v) !== canonical(d.verdict) || v.outcome !== d.outcome) mismatches.push({ seq: e.seq, round: d.round, recorded: d.verdict, replayed: v });
    }
    return { checked, matches: mismatches.length === 0, mismatches };
  }

  // -- independent recomputation: positions, verdicts and state from the history alone ----
  const FORBIDDEN = ["RECREATE_EQUIVALENT_EFFECT", "DECLARE_SUCCESS_WITHOUT_EVIDENCE", "RUN_DEPENDENT_ACTIONS", "EXCEED_PROFILE"];
  const round4 = (x) => Math.round(x * 1e4) / 1e4;
  const pathState = (name, open, reason, sigma, authority, reaches, directed, left) => ({ name, open, reason, sigma, authority, reaches_tau: reaches, directed: !!directed, left: left ?? null });

  function boundaryPosition(profile, f) {
    const h = profile.hesitation; const tau = h.tau_star; const u = (k) => f.usage[k] || 0;
    const coherence = ["CONFIRMED", "FAILED_PROVEN"].includes(f.effect_state) ? 1 : 0;
    const power = (profile.powers || []).find((p) => p.action === f.action);
    const rec = power && power.reconciliation;
    const support = (profile.powers || []).find((p) => p.role === "support");
    const a = profile.authority; const key = f.effect_key; const paths = [];
    for (const name of h.ladder) {
      if (name === "VERIFY") {
        if (!rec) { paths.push(pathState(name, false, "no reconciliation declared for this effect", 0, "agent", false, false, 0)); continue; }
        const left = rec.max_checks + u(`extra_checks:${key}`) - u(`checks:${key}`);
        const sigma = round4(rec.sigma * Math.pow(rec.decay, f.not_found));
        const ok = left > 0 && !f.revoked;
        const grantedNow = u(`directed_checks:${key}`) > 0, scheduled = u(`scheduled_checks:${key}`) > 0;
        const directed = ok && (grantedNow || scheduled);
        const reason = directed ? (grantedNow ? "check granted by the authority" : "check scheduled at the end of the wait") : ok ? `${left} check(s) left` : (f.revoked ? "mandate revoked" : "checks exhausted");
        paths.push(pathState(name, ok, reason, sigma, "agent", sigma >= tau, directed, Math.max(left, 0)));
      } else if (name === "CONTACT_SUPPORT") {
        if (!support) { paths.push(pathState(name, false, "no support contact power in the profile", 0, "mandate", false, false, 0)); continue; }
        const left = support.budget == null ? null : support.budget - u(`power:${support.action}`);
        const sigma = support.sigma != null ? support.sigma : 0.5;
        const ok = (left === null || left > 0) && !f.revoked;
        const reason = ok ? (left === null ? "support reachable" : `${left} contact(s) left`) : (f.revoked ? "mandate revoked" : "contact budget exhausted");
        paths.push(pathState(name, ok, reason, sigma, "mandate", sigma >= tau, false, left === null ? null : Math.max(left, 0)));
      } else if (name === "ASK_AUTHORITY") {
        const left = a.max_requests - u("authority_requests");
        const ok = f.authority_available && !f.open_request && left > 0 && !f.revoked;
        const reason = f.revoked ? "mandate revoked" : !f.authority_available ? "authority unreachable (no channel configured)" : f.open_request ? "a request is already open" : left <= 0 ? "requests to the authority exhausted" : `${a.name} reachable on ${a.channel}`;
        paths.push(pathState(name, ok, reason, a.sigma, "authority", a.sigma >= tau, false, Math.max(left, 0)));
      } else if (name === "PRESERVE") {
        if (h.max_wait_seconds > 0) {
          const leftS = Math.max(0, h.max_wait_seconds - u(`waited_s:${key}`));
          const ok = leftS > 0 && u(`waits:${key}`) < h.max_waits && !f.revoked;
          const reason = f.revoked ? "mandate revoked" : !ok ? "patience exhausted" : `${leftS} s of patience left; only on a pending condition stated in the evidence`;
          paths.push(pathState("WAIT", ok, reason, 0, "none", false, false, leftS));
        } else paths.push(pathState("WAIT", false, "waiting is not allowed by the profile", 0, "none", false, false, 0));
        paths.push(pathState(name, true, "always possible: keep the state without acting", 0, "none", false, false, null));
      }
    }
    return {
      effect_key: key, action: f.action, operation_id: f.operation_id, effect_state: f.effect_state, coherence, tau_star: tau,
      threatened: coherence < tau, paths, restorable_in_habilitation: paths.some((p) => p.open && p.reaches_tau && ["agent", "mandate"].includes(p.authority)),
      reference: referencePath(paths, h.ladder), observations: f.observations, forbidden: FORBIDDEN.slice(),
    };
  }

  class Fold {
    constructor(profiles) {
      this.profiles = profiles; this.status = "OPEN"; this.revoked = false; this.effects = {}; this.usage = {}; this.requests = {};
      this.grantsConsumed = new Set(); this.positions = {}; this.observations = {}; this.noResponse = {}; this.authorityReachable = false; this.statusMismatches = [];
    }
    bump(k, d = 1) { this.usage[k] = (this.usage[k] || 0) + d; }
    apply(e) {
      const d = e.data, t = e.type, profile = this.profiles[e.profile];
      const budget = (action) => { const p = (profile.powers || []).find((x) => x.action === action); return !!(p && p.budget != null); };
      const obs = (key) => (this.observations[key] = this.observations[key] || []);
      if (t === "RUNTIME_BOUND") this.authorityReachable = !!d.authority_reachable;
      else if (t === "ACTION_REFUSED") { if (d.correctable) this.bump(`corrections:${d.action}`); }
      else if (t === "ACTION_ADMITTED") { if (d.kind !== "effect" && budget(d.action)) this.bump(`power:${d.action}`); if (d.grant) this.grantsConsumed.add(d.grant); }
      else if (t === "EFFECT_RESERVED") {
        const prev = this.effects[d.effect_key];
        this.effects[d.effect_key] = { action: d.action, operation_id: d.operation_id, status: "DISPATCHING", receipt: null, not_found: 0, attempts: prev ? prev.attempts + 1 : 1 };
        if (budget(d.action)) this.bump(`power:${d.action}`);
      } else if (t === "EFFECT_OUTCOME") {
        const eff = this.effects[d.effect_key]; eff.status = d.status;
        if (d.status === "CONFIRMED") eff.receipt = d.receipt ?? null;
        if (d.status === "FAILED_PROVEN" && budget(eff.action)) this.bump(`power:${eff.action}`, -1);
        if (d.status === "UNKNOWN" && !(d.effect_key in this.noResponse)) this.noResponse[d.effect_key] = { id: `E${e.seq}`, seq: e.seq, at: e.at, source: "core", kind: "NO_RESPONSE", text: d.reason ?? null };
      } else if (t === "HOLD_ENTERED") this.status = "HELD";
      else if (t === "EVIDENCE") {
        obs(d.effect_key).push({ id: `E${e.seq}`, seq: e.seq, at: e.at, source: d.source ?? null, kind: d.kind ?? null, text: d.detail || d.receipt || d.reason || null });
        if (["status", "support"].includes(d.source) && (d.kind === "NOT_FOUND" || (["RECEIPT", "SUPPORT_REPLY"].includes(d.kind) && !d.correlated))) this.effects[d.effect_key].not_found += 1;
      } else if (t === "WAIT_ENDED") obs(d.effect_key).push({ id: `E${e.seq}`, seq: e.seq, at: e.at, source: "core", kind: "WAITED", text: `Waited until ${d.until}; the position is read again.` });
      else if (t === "RECONCILED") { const eff = this.effects[d.effect_key]; eff.status = "CONFIRMED"; eff.receipt = d.receipt; }
      else if (t === "POSITION") this.positions[e.seq] = d.position;
      else if (t === "DECISION") {
        const applied = d.verdict.applied; const pos = this.positions[d.position_seq]; const key = pos.effect_key; const u = (k) => this.usage[k] || 0;
        if (applied === "WAIT") {
          this.bump(`waits:${key}`); this.bump(`waited_s:${key}`, d.verdict.wait_seconds);
          const v = pos.paths.find((x) => x.name === "VERIFY");
          if (v && v.open && !u(`scheduled_checks:${key}`)) this.bump(`scheduled_checks:${key}`);
        } else if (applied === "VERIFY") {
          this.bump(`checks:${key}`);
          if (u(`directed_checks:${key}`) > 0) this.bump(`directed_checks:${key}`, -1);
          else if (u(`scheduled_checks:${key}`) > 0) this.bump(`scheduled_checks:${key}`, -1);
        } else if (applied === "CONTACT_SUPPORT") {
          const sup = (profile.powers || []).find((p) => p.role === "support");
          if (sup && sup.budget != null) this.bump(`power:${sup.action}`);
        }
      } else if (t === "PRESERVED" || t === "WAIT_STARTED") this.status = "HELD";
      else if (t === "DECISION_REQUEST") { this.requests[d.question_id] = "OPEN"; this.bump("authority_requests"); this.status = "AWAITING_AUTHORITY"; }
      else if (t === "AUTHORITY_ANSWER") { this.requests[d.question_id] = "ANSWERED"; if (d.answer === "REFUSE") this.status = "OPEN"; }
      else if (t === "REQUEST_CLOSED") { this.requests[d.question_id] = d.status; this.status = d.mission_status; }
      else if (t === "AUTHORITY_GRANT") {
        if (d.grant.kind === "EXTRA_CHECK") { this.bump(`extra_checks:${d.grant.effect_key}`); this.bump(`directed_checks:${d.grant.effect_key}`); this.status = "HELD"; }
        else if (d.grant.kind === "ENVELOPE_ONCE") this.status = "OPEN";
      } else if (t === "RESUMED") { if (["HELD", "AWAITING_AUTHORITY"].includes(this.status)) this.status = "OPEN"; }
      else if (t === "REVOKED") { this.revoked = true; for (const q in this.requests) if (this.requests[q] === "OPEN") this.requests[q] = "CANCELLED"; this.status = "REVOKED"; }
      else if (t === "MISSION_CLOSED") { for (const q in this.requests) if (this.requests[q] === "OPEN") this.requests[q] = "CANCELLED"; this.status = d.outcome; }
      if (e.status != null && e.status !== this.status) this.statusMismatches.push({ seq: e.seq, type: t, recorded: e.status, recomputed: this.status });
    }
    position(key, profile) {
      const eff = this.effects[key];
      const obs = (this.observations[key] || []).slice(-8);
      if (key in this.noResponse) obs.unshift(this.noResponse[key]);
      return boundaryPosition(profile, {
        effect_key: key, action: eff.action, operation_id: eff.operation_id, effect_state: eff.status, not_found: eff.not_found,
        usage: this.usage, authority_available: this.authorityReachable, open_request: Object.values(this.requests).includes("OPEN"), revoked: this.revoked, observations: obs,
      });
    }
    state() {
      const usage = {}; for (const [k, v] of Object.entries(this.usage)) if (v) usage[k] = v;
      return { status: this.status, revoked: this.revoked, effects: this.effects, usage, requests: this.requests, grants_consumed: [...this.grantsConsumed].sort() };
    }
  }

  /** Verification, not reproduction: every position and verdict is recomputed from the history, the
   * frozen profiles and this implementation of the rules; what the journal asserts is only compared. */
  async function verifyJournal(journal) {
    const events = journal.events, profiles = journal.profiles || {};
    const chain = await verifyChain(events);
    const f = new Fold(profiles); const recomputed = {};
    const positions = { checked: 0, mismatches: [] }, decisions = { checked: 0, mismatches: [] };
    for (const e of events) {
      if (!profiles[e.profile]) return { chain, error: `profile ${String(e.profile).slice(0, 12)} missing from the journal` };
      if (e.type === "POSITION") {
        const mine = f.position(e.data.effect_key, profiles[e.profile]); recomputed[e.seq] = mine; positions.checked++;
        if (canonical(mine) !== canonical(e.data.position)) positions.mismatches.push({ seq: e.seq, round: e.data.round, fields: Object.keys(mine).filter((k) => canonical(mine[k]) !== canonical(e.data.position[k])) });
      } else if (e.type === "DECISION") {
        const d = e.data; const pos = recomputed[d.position_seq]; decisions.checked++;
        if (!pos) decisions.mismatches.push({ seq: e.seq, error: "no recomputed position" });
        else {
          const read = await digest(pos);
          const v = admitProposal(d.proposal, pos, read, d.now_digest, d.final, d.on_refused || profiles[e.profile].hesitation.on_refused_proposal);
          const problems = [];
          if (read !== d.read_digest) problems.push("the gate read a position the history does not imply");
          if (canonical(v) !== canonical(d.verdict) || v.outcome !== d.outcome) problems.push("verdict differs");
          if (problems.length) decisions.mismatches.push({ seq: e.seq, round: d.round, problems, recorded: d.verdict, recomputed: v });
        }
      }
      f.apply(e);
    }
    const state = f.state();
    const store = journal.store ? canonical(journal.store) === canonical(state) : null;
    const counts = (type, pred = () => true) => events.filter((e) => e.type === type && pred(e)).length;
    const external = [
      { what: "provider", count: counts("EFFECT_OUTCOME", (e) => e.data.status !== "UNKNOWN") + counts("EVIDENCE", (e) => e.data.source !== "core"), note: "Receipts, refusals and replies are the provider's statements. Correlation with the operation is checked here; the provider's register is not." },
      { what: "authority", count: counts("AUTHORITY_ANSWER"), note: "Taps were authenticated at runtime (Meta signature, enrolled number, open question). The signature is not in the journal; the WhatsApp message ids are, for audit with Meta." },
      { what: "model", count: counts("PROPOSAL", (e) => e.data.proposal && e.data.proposal.model), note: "Model outputs are recorded with their digests. Reproducing them requires re-running the recorded model with the recorded prompt digest." },
      { what: "completeness", count: null, note: "The chain proves the order and integrity of what was recorded, not that nothing was left out. A full rewrite of the store would also rewrite the chain: only an external witness of the head can rule that out." },
      { what: "time", count: null, note: "Timestamps come from the runtime's clock." },
    ];
    return {
      chain, positions: { ...positions, matches: positions.mismatches.length === 0 }, decisions: { ...decisions, matches: decisions.mismatches.length === 0 },
      status: { matches: f.statusMismatches.length === 0, mismatches: f.statusMismatches }, state, store_matches: store, external,
    };
  }

  // -- profil -------------------------------------------------------------------------------
  const IDENT = /^[a-z][a-z0-9_]{0,63}$/;
  function validateProfile(p) {
    const errs = [];
    const add = (loc, msg) => errs.push({ loc, msg });
    if (!p || typeof p !== "object") return [{ loc: "", msg: "Profil vide" }];
    if (p.hold !== "habilitation/v1") add("hold", "Version de profil attendue : habilitation/v1");
    if (!/^[a-z0-9][a-z0-9._-]{1,63}$/.test(p.id || "")) add("id", "Identifiant : minuscules, chiffres, . _ -");
    if (!p.title) add("title", "Titre requis");
    if (!(Number.isInteger(p.revision) && p.revision >= 1)) add("revision", "Révision entière ≥ 1");
    if (p.expires_at && p.issued_at && Date.parse(p.expires_at) <= Date.parse(p.issued_at)) add("expires_at", "Expire avant d'être émis");
    const a = p.authority || {};
    if (!a.name) add("authority.name", "Nom de l'autorité requis");
    if (a.channel === "whatsapp" && !/^[1-9]\d{7,14}$/.test(a.contact || "")) add("authority.contact", "Numéro international, chiffres seuls (ex. 212612345678)");
    const powers = p.powers || [];
    if (!powers.length) add("powers", "Au moins un pouvoir");
    const names = powers.map((x) => x.action);
    powers.forEach((pw, i) => {
      const loc = `powers.${i}`;
      if (!IDENT.test(pw.action || "")) add(loc + ".action", "Nom d'action : minuscules, chiffres, _");
      if (names.indexOf(pw.action) !== i) add(loc + ".action", "Action en double");
      if (pw.kind === "effect") {
        if (!(pw.identity || []).length) add(loc + ".identity", "Un effet doit déclarer ses attributs d'identité");
        const env = pw.envelope;
        if (env && env.max_amount) {
          if (!(env.max_amount.amount > 0)) add(loc + ".envelope.max_amount", "Plafond positif requis");
          if (!/^[A-Z]{3}$/.test(env.max_amount.currency || "")) add(loc + ".envelope.max_amount", "Devise ISO à 3 lettres");
        }
        for (const [k, w] of Object.entries((env && env.windows) || {})) if (w.start > w.end) add(`${loc}.envelope.windows.${k}`, "Fenêtre inversée");
        const rec = pw.reconciliation;
        if (rec) {
          const s = powers.find((x) => x.action === rec.status_action);
          if (!s || s.kind !== "status") add(loc + ".reconciliation", `La réconciliation exige un pouvoir « status » nommé ${rec.status_action}`);
        }
      } else if (pw.envelope || pw.reconciliation || (pw.identity || []).length) add(loc, "Enveloppe, identité et réconciliation sont réservées aux effets");
      for (const d of pw.depends_on || []) { const t = powers.find((x) => x.action === d); if (!t || t.kind !== "effect") add(loc + ".depends_on", `Dépendance inconnue : ${d}`); }
      if (pw.role === "support" && pw.kind !== "contact") add(loc + ".role", "Le rôle support exige un pouvoir de contact");
    });
    if (powers.filter((x) => x.role === "support").length > 1) add("powers", "Un seul pouvoir « support »");
    const h = p.hesitation || {};
    if (!(h.tau_star > 0 && h.tau_star <= 1)) add("hesitation.tau_star", "τ* entre 0 (exclu) et 1");
    if (!h.ladder || h.ladder[h.ladder.length - 1] !== "PRESERVE") add("hesitation.ladder", "PRESERVE doit être le dernier chemin");
    if (h.ladder && new Set(h.ladder).size !== h.ladder.length) add("hesitation.ladder", "Chemin en double");
    return errs;
  }

  /** Ce que fera l'agent si chaque vérification revient « introuvable » et que le support n'aide pas. */
  function simulateHesitation(p, action) {
    const h = p.hesitation; const tau = h.tau_star; const a = p.authority;
    const effect = (p.powers || []).find((x) => x.action === action) || (p.powers || []).find((x) => x.kind === "effect" && x.reconciliation);
    const rec = effect && effect.reconciliation;
    const support = (p.powers || []).find((x) => x.role === "support");
    let notFound = 0, checks = 0, supportUsed = 0; const steps = [];
    for (let i = 0; i < 10; i++) {
      const paths = h.ladder.map((name) => {
        if (name === "VERIFY") { const sigma = rec ? rec.sigma * Math.pow(rec.decay, notFound) : 0; const open = !!rec && checks < rec.max_checks; return { name, open, sigma, reaches_tau: sigma >= tau }; }
        if (name === "CONTACT_SUPPORT") { const sigma = support ? (support.sigma ?? 0.5) : 0; const open = !!support && (support.budget == null || supportUsed < support.budget); return { name, open, sigma, reaches_tau: sigma >= tau }; }
        if (name === "ASK_AUTHORITY") { const open = !!(a && (a.channel === "console" || a.contact)) && (a.max_requests ?? 2) > 0; return { name, open, sigma: a.sigma, reaches_tau: a.sigma >= tau }; }
        return { name, open: true, sigma: 0, reaches_tau: false };
      });
      const ref = referencePath(paths, h.ladder);
      const sigma = (paths.find((x) => x.name === ref) || {}).sigma || 0;
      steps.push({ path: ref, sigma: Math.round(sigma * 100) / 100, authority: LADDER_AUTHORITY[ref] });
      if (ref === "VERIFY") { checks++; notFound++; }
      else if (ref === "CONTACT_SUPPORT") { supportUsed++; notFound++; }
      else break;
    }
    return steps;
  }

  const api = { canonical, digest, sha256Hex, fingerprint, profileBody, verifyChain, verifyJournal, boundaryPosition, Fold, decisionReplay, admitProposal, checkMap, justifiedMoves, trueMap, referencePath, validateProfile, simulateHesitation, ENVELOPE_VERSION, PHASES };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HoldCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
