// Pure, testable session-persistence logic for the Sage workspace.
//
// The React context (`components/AppState.tsx`) is a thin wrapper that wires effects to these
// functions. Everything here is deterministic: callers inject `now` (ms since epoch) so eviction
// and TTL are testable without mocking the clock — do NOT call `Date.now()` inside these helpers.
//
// R19 (data-at-rest safety): the sensitive asset is the BRI schema catalog itself — table/column
// *business meanings* and free-text descriptions. `redactForStorage` strips those free-text
// payloads before anything is written to `localStorage`, while keeping the identifiers and query
// text needed to restore a usable session. See `redactSearchResponse` / `redactAgentResponse`.

import type {
  AgentReply,
  AgentResponse,
  SearchCard,
  SearchResponse,
} from "@/lib/api";

// --- Persisted shape ---------------------------------------------------------

export const STORAGE_KEY = "sage.sessions.v1";
export const STORAGE_VERSION = 1;
export const MAX_SESSIONS = 20;
export const RETENTION_DAYS = 30;
const RETENTION_MS = RETENTION_DAYS * 24 * 60 * 60 * 1000;

// The persisted agent slice: the transient `sending` flag and the composer `question` draft are
// intentionally NOT part of a Session (see AppState.tsx — they live in ephemeral component state).
import type { ChatTurn, SearchState } from "@/components/AppState";

export interface PersistedAgent {
  attached: string[];
  turns: ChatTurn[];
}

export interface Session {
  id: string;
  title: string;
  createdAt: number; // ms since epoch, injected — never Date.now() in pure code
  search: SearchState;
  agent: PersistedAgent;
}

export interface PersistedState {
  version: number;
  sessions: Record<string, Session>;
  activeId: string;
}

// --- id / session construction ----------------------------------------------

function makeId(): string {
  // crypto.randomUUID exists in jsdom + all modern browsers; fall back for older envs.
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `s_${Math.random().toString(36).slice(2)}_${Math.random().toString(36).slice(2)}`;
}

export const EMPTY_SEARCH: SearchState = { q: "", domain: null, res: null };
export const EMPTY_AGENT: PersistedAgent = { attached: [], turns: [] };

/** A fresh, untitled active session. `now` is injected for deterministic createdAt. */
export function newSession(now: number): Session {
  return {
    id: makeId(),
    title: "Untitled",
    createdAt: now,
    search: { ...EMPTY_SEARCH },
    agent: { attached: [], turns: [] },
  };
}

/** A clean single-session state — used on first visit and after clearHistory(). */
export function seedState(now: number): PersistedState {
  const s = newSession(now);
  return { version: STORAGE_VERSION, sessions: { [s.id]: s }, activeId: s.id };
}

// --- R19 redaction -----------------------------------------------------------
//
// Field-level exclusion defined concretely against the real types in lib/api.ts.
//
// SearchCard: KEEP identifiers + counts + tags + pii flag (id, table_name, physical_name,
//   n_columns, domain_tags, pii); STRIP the free-text business meaning (headline, description).
// SearchResponse: keep query text + structural flags; redact every card + closest_related card.
// AgentResponse: keep the drafted SQL + table/column identifiers + structural signals; STRIP the
//   free-text `explanation`, `assumptions`, `warnings`, and the decline `missing` note, which can
//   quote literal schema business-meaning or values. `grounding` entries keep only the table
//   name + booleans (name is an identifier, already surfaced in tables_used).
// ChatTurn.content (the user's own question text) is conversation text and is retained per the
//   plan ("persist ... conversation text").

function redactSearchCard(card: SearchCard): SearchCard {
  return {
    ...card,
    headline: "",
    description: "",
  };
}

export function redactSearchResponse(res: SearchResponse): SearchResponse {
  return {
    ...res,
    results: res.results.map(redactSearchCard),
    closest_related: res.closest_related?.map(redactSearchCard),
  };
}

export function redactAgentResponse(res: AgentReply): AgentReply {
  // Multi-draft: strip the reconciliation prose + cross-draft warnings, redact each sub-draft.
  if (res.kind === "sql_multi") {
    return {
      ...res,
      reconciliation: null,
      warnings: [],
      sub_drafts: res.sub_drafts.map((sd) => ({
        sub_need: sd.sub_need,
        result: redactAgentResponse(sd.result) as AgentResponse,
      })),
    };
  }
  return {
    ...res,
    explanation: null,
    assumptions: [],
    warnings: [],
    missing: null,
    grounding: res.grounding.map((g) => ({
      name: g.name,
      in_kb: g.in_kb,
      retrieved: g.retrieved,
    })),
  };
}

function redactTurn(turn: ChatTurn): ChatTurn {
  const out: ChatTurn = { id: turn.id, role: turn.role };
  if (turn.content !== undefined) out.content = turn.content;
  if (turn.attached !== undefined) out.attached = turn.attached;
  if (turn.error !== undefined) out.error = turn.error;
  if (turn.response !== undefined) out.response = redactAgentResponse(turn.response);
  return out;
}

/** Return a copy of a session with all R19-sensitive free-text payloads stripped. */
export function redactForStorage(session: Session): Session {
  return {
    id: session.id,
    title: session.title,
    createdAt: session.createdAt,
    search: {
      q: session.search.q,
      domain: session.search.domain,
      res: session.search.res ? redactSearchResponse(session.search.res) : null,
    },
    agent: {
      attached: [...session.agent.attached],
      turns: session.agent.turns.map(redactTurn),
    },
  };
}

// --- eviction (size cap + TTL) ----------------------------------------------

/**
 * Enforce both retention rules against a session map, returning a pruned copy:
 *  - TTL: drop sessions whose createdAt is older than RETENTION_DAYS relative to `now`.
 *  - Cap: keep at most MAX_SESSIONS, dropping the oldest (smallest createdAt) first.
 * `now` is injected for determinism.
 */
export function evict(
  sessions: Record<string, Session>,
  now: number,
): Record<string, Session> {
  const alive = Object.values(sessions).filter(
    (s) => now - s.createdAt <= RETENTION_MS,
  );
  // newest first
  alive.sort((a, b) => b.createdAt - a.createdAt);
  const kept = alive.slice(0, MAX_SESSIONS);
  const out: Record<string, Session> = {};
  for (const s of kept) out[s.id] = s;
  return out;
}

// --- serialize / deserialize -------------------------------------------------

/**
 * Produce the persisted state to write to localStorage: strip the transient `sending`/`question`
 * (already excluded by the Session shape), apply R19 redaction to every session, then enforce the
 * size cap + TTL. `activeId` is repointed to the newest surviving session if it was evicted.
 */
export function serialize(state: PersistedState, now: number): PersistedState {
  const redacted: Record<string, Session> = {};
  for (const s of Object.values(state.sessions)) {
    redacted[s.id] = redactForStorage(s);
  }
  const kept = evict(redacted, now);
  let activeId = state.activeId;
  if (!kept[activeId]) {
    // active was evicted — repoint to newest survivor, or seed one if the map emptied.
    const newest = Object.values(kept).sort((a, b) => b.createdAt - a.createdAt)[0];
    if (newest) {
      activeId = newest.id;
    } else {
      return seedState(now);
    }
  }
  return { version: STORAGE_VERSION, sessions: kept, activeId };
}

/** Convenience: serialize then JSON.stringify for writing to storage. */
export function serializeToString(state: PersistedState, now: number): string {
  return JSON.stringify(serialize(state, now));
}

function isSession(v: unknown): v is Session {
  if (!v || typeof v !== "object") return false;
  const s = v as Record<string, unknown>;
  return (
    typeof s.id === "string" &&
    typeof s.title === "string" &&
    typeof s.createdAt === "number" &&
    typeof s.search === "object" &&
    s.search !== null &&
    typeof s.agent === "object" &&
    s.agent !== null
  );
}

/**
 * Parse + validate + migrate a raw localStorage string into a clean PersistedState.
 * On empty / corrupt / wrong-version input, seed ONE active untitled session (never zero).
 * Applies TTL + cap eviction on hydrate. Never throws.
 */
export function deserialize(raw: string | null, now: number): PersistedState {
  if (!raw) return seedState(now);

  let parsed: unknown;
  try {
    parsed = JSON.parse(raw);
  } catch {
    return seedState(now);
  }

  if (!parsed || typeof parsed !== "object") return seedState(now);
  const obj = parsed as Record<string, unknown>;

  // Wrong / unknown version → ignore and start clean (migration guard).
  if (obj.version !== STORAGE_VERSION) return seedState(now);

  const rawSessions = obj.sessions;
  if (!rawSessions || typeof rawSessions !== "object") return seedState(now);

  const clean: Record<string, Session> = {};
  for (const [id, val] of Object.entries(rawSessions as Record<string, unknown>)) {
    if (isSession(val) && val.id === id) {
      // normalize the persisted slices so downstream code sees the full shape.
      const s = val as Session;
      clean[id] = {
        id: s.id,
        title: s.title,
        createdAt: s.createdAt,
        search: {
          q: s.search?.q ?? "",
          domain: s.search?.domain ?? null,
          res: s.search?.res ?? null,
        },
        agent: {
          attached: Array.isArray(s.agent?.attached) ? s.agent.attached : [],
          turns: Array.isArray(s.agent?.turns) ? s.agent.turns : [],
        },
      };
    }
  }

  const kept = evict(clean, now);
  if (Object.keys(kept).length === 0) return seedState(now);

  let activeId = typeof obj.activeId === "string" ? obj.activeId : "";
  if (!kept[activeId]) {
    activeId = Object.values(kept).sort((a, b) => b.createdAt - a.createdAt)[0].id;
  }

  return { version: STORAGE_VERSION, sessions: kept, activeId };
}
