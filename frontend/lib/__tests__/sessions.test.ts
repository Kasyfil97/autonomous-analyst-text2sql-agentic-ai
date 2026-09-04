import { describe, expect, it } from "vitest";
import type { AgentResponse, SearchCard, SearchResponse } from "@/lib/api";
import type { ChatTurn, SearchState } from "@/components/AppState";
import {
  MAX_SESSIONS,
  RETENTION_DAYS,
  STORAGE_VERSION,
  type PersistedState,
  type Session,
  deserialize,
  evict,
  newSession,
  redactAgentResponse,
  redactForStorage,
  redactSearchResponse,
  seedState,
  serialize,
} from "@/lib/sessions";

const DAY = 24 * 60 * 60 * 1000;
const NOW = 1_700_000_000_000;

// --- fixtures ----------------------------------------------------------------

function makeCard(over: Partial<SearchCard> = {}): SearchCard {
  return {
    id: "tbl_1",
    table_name: "credit_card_txn",
    physical_name: "dw.credit_card_txn",
    headline: "Sensitive business headline",
    description: "Free-text description of business meaning that must not persist.",
    domain_tags: ["cards"],
    n_columns: 42,
    pii: "present",
    ...over,
  };
}

function makeSearchResponse(): SearchResponse {
  return {
    query: "transaksi kartu kredit",
    domain: "cards",
    results: [makeCard(), makeCard({ id: "tbl_2", physical_name: "dw.txn_2" })],
    filter_caused_empty: false,
    closest_related: [makeCard({ id: "tbl_3", physical_name: "dw.txn_3" })],
  };
}

function makeAgentResponse(): AgentResponse {
  return {
    kind: "sql",
    sql: "-- UNVERIFIED DRAFT\nSELECT * FROM dw.credit_card_txn",
    explanation: "Sensitive interpretation prose.",
    assumptions: ["Assumed 2025 only"],
    tables_used: ["dw.credit_card_txn"],
    columns_used: ["amount"],
    precedent_ids: ["era_1"],
    dialect: "postgres",
    declined: false,
    missing: "some sensitive missing note",
    warnings: ["[HIGH] quotes a literal business value"],
    grounding: [{ name: "dw.credit_card_txn", in_kb: true, retrieved: true }],
    grounding_strength: "schema_only",
    era_top_cosine: 0.3,
    schema_top_cosine: 0.6,
  };
}

function makeSession(over: Partial<Session> = {}): Session {
  const s = newSession(NOW);
  const search: SearchState = { q: "transaksi", domain: "cards", res: makeSearchResponse() };
  const turns: ChatTurn[] = [
    { id: "u1", role: "user", content: "berapa transaksi?", attached: ["dw.credit_card_txn"] },
    { id: "a1", role: "assistant", response: makeAgentResponse() },
    { id: "a2", role: "assistant", error: "network error" },
  ];
  return {
    ...s,
    search,
    agent: { attached: ["dw.credit_card_txn"], turns },
    ...over,
  };
}

function stateWith(sessions: Session[], activeId?: string): PersistedState {
  const map: Record<string, Session> = {};
  for (const s of sessions) map[s.id] = s;
  return {
    version: STORAGE_VERSION,
    sessions: map,
    activeId: activeId ?? sessions[0]?.id ?? "",
  };
}

// --- round-trip --------------------------------------------------------------

describe("serialize / deserialize round-trip", () => {
  it("round-trips a redacted session (equality minus excluded fields)", () => {
    const session = makeSession();
    const state = stateWith([session]);

    const serialized = serialize(state, NOW);
    const raw = JSON.stringify(serialized);
    const restored = deserialize(raw, NOW);

    // The restored state equals the R19-redacted form of the original — proving both that the
    // round-trip is lossless for retained fields AND that excluded fields never come back.
    const expected = redactForStorage(session);
    expect(restored.activeId).toBe(session.id);
    expect(restored.sessions[session.id]).toEqual(expected);
  });
});

// --- transient fields never persist -----------------------------------------

describe("transient fields", () => {
  it("never writes `sending` or composer `question` to storage", () => {
    // These fields do not exist on the Session type, but assert they are absent in the JSON.
    const state = stateWith([makeSession()]);
    const raw = JSON.stringify(serialize(state, NOW));
    expect(raw).not.toContain("\"sending\"");
    expect(raw).not.toContain("\"question\"");
  });
});

// --- R19 field-level strip ---------------------------------------------------

describe("R19 field-level redaction", () => {
  it("strips SearchCard business-meaning free text but keeps identifiers", () => {
    const red = redactSearchResponse(makeSearchResponse());
    for (const card of red.results) {
      expect(card.headline).toBe("");
      expect(card.description).toBe("");
      // identifiers + structural fields retained
      expect(card.physical_name).toBeTruthy();
      expect(card.table_name).toBeTruthy();
      expect(typeof card.n_columns).toBe("number");
      expect(card.pii).toBeTruthy();
    }
    expect(red.closest_related?.[0].description).toBe("");
    // query text retained
    expect(red.query).toBe("transaksi kartu kredit");
  });

  it("strips AgentResponse free-text but keeps SQL + table identifiers", () => {
    const red = redactAgentResponse(makeAgentResponse());
    if (red.kind !== "sql") throw new Error("expected a single-draft result");
    expect(red.explanation).toBeNull();
    expect(red.assumptions).toEqual([]);
    expect(red.warnings).toEqual([]);
    expect(red.missing).toBeNull();
    // retained: drafted SQL + table/column identifiers + structural signals
    expect(red.sql).toContain("SELECT");
    expect(red.tables_used).toEqual(["dw.credit_card_txn"]);
    expect(red.grounding[0].name).toBe("dw.credit_card_txn");
    expect(red.grounding_strength).toBe("schema_only");
  });

  it("redacts turn responses inside a serialized session", () => {
    const raw = JSON.stringify(serialize(stateWith([makeSession()]), NOW));
    expect(raw).not.toContain("Sensitive interpretation prose");
    expect(raw).not.toContain("Sensitive business headline");
    expect(raw).not.toContain("some sensitive missing note");
    // retained content
    expect(raw).toContain("credit_card_txn");
    expect(raw).toContain("berapa transaksi");
  });
});

// --- eviction: size cap ------------------------------------------------------

describe("session cap eviction", () => {
  it("prunes the oldest beyond MAX_SESSIONS, retaining newest", () => {
    const sessions: Session[] = [];
    for (let i = 0; i < MAX_SESSIONS + 5; i++) {
      sessions.push(newSession(NOW - i * 1000)); // i=0 newest, larger i = older
    }
    const kept = evict(
      Object.fromEntries(sessions.map((s) => [s.id, s])),
      NOW,
    );
    expect(Object.keys(kept)).toHaveLength(MAX_SESSIONS);
    // newest (i=0) kept, oldest (last) dropped
    expect(kept[sessions[0].id]).toBeDefined();
    expect(kept[sessions[MAX_SESSIONS + 4].id]).toBeUndefined();
  });
});

// --- eviction: TTL -----------------------------------------------------------

describe("TTL eviction on hydrate", () => {
  it("drops sessions older than RETENTION_DAYS", () => {
    const fresh = newSession(NOW - 1 * DAY);
    const stale = newSession(NOW - (RETENTION_DAYS + 1) * DAY);
    const raw = JSON.stringify(stateWith([fresh, stale], fresh.id));
    const restored = deserialize(raw, NOW);
    expect(restored.sessions[fresh.id]).toBeDefined();
    expect(restored.sessions[stale.id]).toBeUndefined();
  });

  it("re-seeds one active session if TTL empties the store", () => {
    const stale = newSession(NOW - (RETENTION_DAYS + 10) * DAY);
    const raw = JSON.stringify(stateWith([stale], stale.id));
    const restored = deserialize(raw, NOW);
    expect(Object.keys(restored.sessions)).toHaveLength(1);
    expect(restored.sessions[restored.activeId]).toBeDefined();
  });
});

// --- corrupt / empty / wrong-version hydrate --------------------------------

describe("degraded hydrate → one clean active session", () => {
  it.each([
    ["empty string", ""],
    ["null", null as unknown as string],
    ["corrupt json", "{not valid json"],
    ["wrong shape", JSON.stringify({ hello: "world" })],
    ["wrong version", JSON.stringify({ version: 999, sessions: {}, activeId: "x" })],
  ])("%s seeds exactly one active untitled session, no throw", (_label, raw) => {
    expect(() => deserialize(raw, NOW)).not.toThrow();
    const restored = deserialize(raw, NOW);
    expect(restored.version).toBe(STORAGE_VERSION);
    expect(Object.keys(restored.sessions)).toHaveLength(1);
    expect(restored.sessions[restored.activeId]).toBeDefined();
    expect(restored.sessions[restored.activeId].title).toBe("Untitled");
  });
});

// --- switchSession semantics (state-model level) -----------------------------

describe("switch surfaces the target session", () => {
  it("changing activeId points at that session's search + agent", () => {
    const a = makeSession({ search: { q: "alpha", domain: null, res: null } });
    const b = makeSession({
      id: "sess_b",
      search: { q: "beta", domain: "cards", res: null },
    });
    const state = stateWith([a, b], a.id);

    // seed/serialize the collection, then "switch" by repointing activeId.
    const serialized = serialize(state, NOW);
    const switched: PersistedState = { ...serialized, activeId: "sess_b" };
    expect(switched.sessions[switched.activeId].search.q).toBe("beta");
    expect(switched.sessions[switched.activeId].search.domain).toBe("cards");
  });
});

// --- clearHistory / seed -----------------------------------------------------

describe("seedState", () => {
  it("produces exactly one active untitled session", () => {
    const seeded = seedState(NOW);
    expect(Object.keys(seeded.sessions)).toHaveLength(1);
    expect(seeded.sessions[seeded.activeId].title).toBe("Untitled");
    expect(seeded.sessions[seeded.activeId].createdAt).toBe(NOW);
  });
});
