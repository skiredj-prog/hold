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
    if (!isObj(declared)) return { true: false, discrepancies: ["no map declared"], grounded: null };
    const truth = trueMap(position); const out = [];
    if (declared.effect_state !== truth.effect_state) out.push(`effect_state is ${truth.effect_state}, not ${str(declared.effect_state)}`);
    for (const [k, label] of [["open_paths", "open paths"], ["reaching_tau", "paths reaching tau*"]]) {
      const got = names(declared[k]);
      if (got.join(",") !== truth[k].join(",")) out.push(`${label} are ${truth[k].join(", ") || "none"}, not ${got.join(", ") || "none"}`);
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
    }
    return { true: out.length === 0, discrepancies: out, grounded };
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

  function verdict(outcome, proposed, applied, reference, credit, rule, reason, mapTrue, coherent, admissible, justified, supported, discrepancies, wait) {
    return { outcome, proposed, applied, reference, credit, rule, reason, map_true: mapTrue, coherent, admissible, justified, supported, discrepancies, wait_seconds: wait ?? null };
  }
  function admitProposal(proposal, position, readDigest, nowDigest, final, onRefused) {
    const ref = position.reference; const p = proposal || {}; const move = p.move ?? null;
    const admissible = admissibleMoves(position);
    const justified = justifiedMoves(trueMap(position), position);
    if (readDigest !== nowDigest) return verdict("STALE", move, null, ref, null, "I5", "The position changed during deliberation: re-read before acting.", null, null, admissible, justified, [], []);
    const mc = checkMap(p.map, position);
    const supported = justifiedMoves(p.map, position);
    const coherent = move ? supported.includes(move) : null;
    let outcome, rule, reason;
    if (!move) { outcome = "NO_PROPOSAL"; rule = "D1/NONE"; reason = "No usable proposal" + (p.error ? ` (${p.error})` : "") + "."; }
    else if (!mc.true) { outcome = "MAP_REFUSED"; rule = "D1/MAP"; reason = "The declared reading is false: " + mc.discrepancies.join("; ") + "."; }
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
      return verdict("ADMITTED", move, move, ref, "discerner", "ADMIT", `${move} is open and follows from a true reading.`, true, true, admissible, justified, supported, [], wait);
    }
    if (!final) return verdict(outcome, move, null, ref, null, rule, reason, mc.true, coherent, admissible, justified, supported, mc.discrepancies);
    const applied = onRefused === "reference" ? ref : "PRESERVE";
    return verdict("REFERENCE_APPLIED", move, applied, ref, "floor", rule, `${reason} Retries exhausted: the floor applies ${applied} (credited to the floor, not to the model).`, mc.true, coherent, admissible, justified, supported, mc.discrepancies);
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

  const api = { canonical, digest, sha256Hex, fingerprint, profileBody, verifyChain, decisionReplay, admitProposal, checkMap, justifiedMoves, trueMap, referencePath, validateProfile, simulateHesitation, ENVELOPE_VERSION, PHASES };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.HoldCore = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
