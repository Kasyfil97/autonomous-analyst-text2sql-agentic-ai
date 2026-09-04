"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useRef,
  useState,
  type Dispatch,
  type ReactNode,
  type SetStateAction,
} from "react";
import type { AgentReply, SearchResponse } from "@/lib/api";
import { addAttached, type AttachResult } from "@/lib/attach";
import {
  STORAGE_KEY,
  deserialize,
  newSession as makeSession,
  seedState,
  serializeToString,
  type PersistedAgent,
  type PersistedState,
  type Session,
} from "@/lib/sessions";

// State that must outlive route changes lives here. The provider is mounted once in the root
// layout, which stays mounted across client-side navigation. It is now backed by a keyed
// collection of Sessions persisted to localStorage (see lib/sessions.ts): a search (or agent
// draft) survives a full page refresh, and prior sessions can be resumed from the rail.

export interface SearchState {
  q: string;
  domain: string | null;
  res: SearchResponse | null;
}

export interface ChatTurn {
  id: string;
  role: "user" | "assistant";
  content?: string; // user message text
  attached?: string[]; // tables attached on a user turn
  response?: AgentReply; // assistant structured result (single or multi-draft)
  error?: string; // assistant turn that failed to load
}

export interface AgentState {
  attached: string[]; // pending attachments for the next message
  question: string; // composer draft (transient — never persisted)
  turns: ChatTurn[]; // the conversation
  sending: boolean; // a request is in flight (transient — never persisted)
}

/**
 * The outcome of the most recent attach attempt (button or drag), used by AgentPane's
 * `aria-live` region to announce success / already-attached / cap-reached for BOTH paths.
 * `nonce` bumps on every attempt so re-announcing the same table+result still fires.
 */
export interface AttachAnnouncement {
  table: string;
  result: AttachResult;
  nonce: number;
}

interface AppStateValue {
  // --- existing consumer API (search/agent page) — preserved verbatim -------
  search: SearchState;
  setSearch: Dispatch<SetStateAction<SearchState>>;
  agent: AgentState;
  setAgent: Dispatch<SetStateAction<AgentState>>;

  // --- attach layer (button + drag-and-drop, U7) ----------------------------
  /**
   * Attach a table to the active session's agent context, enforcing dedupe + the cap of 10
   * (mirrors the backend). Records an announcement for the aria-live region and returns the
   * result so callers can drive local visual feedback. Single source of truth for both the
   * "Kirim ke agent" button and the drop target.
   */
  attachTable: (table: string) => AttachResult;
  /** The most recent attach outcome (or null before any attach). */
  lastAttach: AttachAnnouncement | null;

  // --- session layer (consumed by the U4 rail) ------------------------------
  sessions: Session[]; // newest-first, for the rail
  activeId: string;
  newSession: () => void;
  switchSession: (id: string) => void;
  clearHistory: () => void;
  /** Session ids that currently have an in-flight agent request (for the rail indicator). */
  generatingIds: string[];
}

const Ctx = createContext<AppStateValue | null>(null);

// Transient composer draft + sending flag are kept OUT of the persisted Session, so they live in
// a parallel per-session map here in memory only, keyed by session id.
interface Transient {
  question: string;
  sending: boolean;
}
const EMPTY_TRANSIENT: Transient = { question: "", sending: false };

const PERSIST_DEBOUNCE_MS = 400;

export function AppStateProvider({ children }: { children: ReactNode }) {
  // Neutral first render (SSR + first client render) — hydrate from localStorage only AFTER mount
  // to avoid an SSR/CSR hydration mismatch. `hydrated` gates persistence so we never overwrite the
  // stored state with the seed before we've read it.
  const [state, setState] = useState<PersistedState>(() => seedState(0));
  const [transient, setTransient] = useState<Record<string, Transient>>({});
  const [hydrated, setHydrated] = useState(false);
  const [lastAttach, setLastAttach] = useState<AttachAnnouncement | null>(null);
  const attachNonce = useRef(0);

  // Live mirrors of `state` / `transient`, kept in sync on every render so callbacks captured once
  // (stable identity) still read the CURRENT values — not a stale closure snapshot. This is what
  // lets `setAgent` apply a caller's updater against LIVE turns even when the same captured setter
  // is invoked twice across an `await` (append user turn, then append assistant turn).
  const stateRef = useRef(state);
  stateRef.current = state;
  const transientRef = useRef(transient);
  transientRef.current = transient;

  // Hydrate once, client-only, post-mount.
  useEffect(() => {
    const now = Date.now();
    let raw: string | null = null;
    try {
      raw = window.localStorage.getItem(STORAGE_KEY);
    } catch {
      raw = null;
    }
    setState(deserialize(raw, now));
    setHydrated(true);
  }, []);

  // Persist on change, debounced. Serialization strips `sending`/`question` (they are not part of
  // the Session shape) and applies R19 redaction + eviction inside serializeToString.
  const debounceRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  useEffect(() => {
    if (!hydrated) return;
    if (debounceRef.current) clearTimeout(debounceRef.current);
    debounceRef.current = setTimeout(() => {
      try {
        window.localStorage.setItem(STORAGE_KEY, serializeToString(state, Date.now()));
      } catch {
        /* quota / disabled storage — best effort, in-memory state still authoritative */
      }
    }, PERSIST_DEBOUNCE_MS);
    return () => {
      if (debounceRef.current) clearTimeout(debounceRef.current);
    };
  }, [state, hydrated]);

  const active: Session | undefined = state.sessions[state.activeId];

  // --- patch helpers ---------------------------------------------------------

  // Patch a specific session's search slice (used by the switch-mid-generation seam so a completed
  // request lands in its ORIGIN session, not the now-active one).
  const patchSessionSearch = useCallback(
    (id: string, updater: SetStateAction<SearchState>) => {
      setState((prev) => {
        const s = prev.sessions[id];
        if (!s) return prev;
        const next =
          typeof updater === "function"
            ? (updater as (p: SearchState) => SearchState)(s.search)
            : updater;
        return { ...prev, sessions: { ...prev.sessions, [id]: { ...s, search: next } } };
      });
    },
    [],
  );

  const patchSessionAgent = useCallback(
    (id: string, updater: SetStateAction<PersistedAgent>) => {
      setState((prev) => {
        const s = prev.sessions[id];
        if (!s) return prev;
        const next =
          typeof updater === "function"
            ? (updater as (p: PersistedAgent) => PersistedAgent)(s.agent)
            : updater;
        return { ...prev, sessions: { ...prev.sessions, [id]: { ...s, agent: next } } };
      });
    },
    [],
  );

  // --- search: the SearchState facade over the ACTIVE session ---------------

  const search: SearchState = active?.search ?? { q: "", domain: null, res: null };

  const setSearch: Dispatch<SetStateAction<SearchState>> = useCallback(
    (updater) => patchSessionSearch(state.activeId, updater),
    [patchSessionSearch, state.activeId],
  );

  // --- agent: reconstruct the AgentState facade (persisted slice + transient) -

  const activeTransient = transient[state.activeId] ?? EMPTY_TRANSIENT;
  const agent: AgentState = useMemo(
    () => ({
      attached: active?.agent.attached ?? [],
      turns: active?.agent.turns ?? [],
      question: activeTransient.question,
      sending: activeTransient.sending,
    }),
    [active, activeTransient],
  );

  // setAgent accepts the full AgentState (for backward compat with the existing consumer that uses
  // functional updaters). It splits the update: persisted slice (attached/turns) → the ACTIVE
  // session; transient slice (question/sending) → the transient map keyed by the ACTIVE session id.
  //
  // NOTE (switch-mid-generation, U6 seam): the existing agent consumer resolves an in-flight
  // request against `setAgent`, which targets whatever is active NOW. When U6 wires the real
  // request binding, capture `activeId` at submit time and route the completion through
  // `patchSessionAgent(originId, ...)` + `setSending(originId, false)` below, so the draft lands in
  // its origin session even after the user switches away. The state model already supports this —
  // per-session `agent` slices and a per-session `sending` flag are addressable by id.
  const setAgent: Dispatch<SetStateAction<AgentState>> = useCallback(
    (updater) => {
      // Bind this call to the session that is active NOW (captured once, at call time). An in-flight
      // request therefore lands in its ORIGIN session even if the user switches away mid-generation.
      const id = stateRef.current.activeId;
      // Stash the transient half computed inside the patch (which runs against LIVE state).
      let stashed: Transient = transientRef.current[id] ?? EMPTY_TRANSIENT;

      patchSessionAgent(id, (prevSlice) => {
        const prevTransient = transientRef.current[id] ?? EMPTY_TRANSIENT;
        const prevAgent: AgentState = {
          attached: prevSlice.attached,
          turns: prevSlice.turns,
          question: prevTransient.question,
          sending: prevTransient.sending,
        };
        const next =
          typeof updater === "function"
            ? (updater as (p: AgentState) => AgentState)(prevAgent)
            : updater;
        stashed = { question: next.question, sending: next.sending };
        return { attached: next.attached, turns: next.turns };
      });

      setTransient((t) => {
        const nt = { ...t, [id]: stashed };
        transientRef.current = nt;
        return nt;
      });
    },
    [patchSessionAgent],
  );

  // --- attach action (button + drag-and-drop) -------------------------------
  //
  // Single source of truth for attaching a table to the ACTIVE session's agent context. Enforces
  // dedupe + the cap of 10 via the pure `addAttached` helper (mirrors the backend), records an
  // announcement for the aria-live region, and returns the result for local visual feedback.
  const attachTable = useCallback(
    (table: string): AttachResult => {
      const id = state.activeId;
      const current = state.sessions[id]?.agent.attached ?? [];
      const { next, result } = addAttached(current, table);
      if (result === "added") {
        patchSessionAgent(id, (a) => ({ ...a, attached: next }));
      }
      attachNonce.current += 1;
      setLastAttach({ table, result, nonce: attachNonce.current });
      return result;
    },
    [patchSessionAgent, state.activeId, state.sessions],
  );

  // --- session lifecycle actions --------------------------------------------

  const newSession = useCallback(() => {
    const s = makeSession(Date.now());
    setState((prev) => ({
      ...prev,
      sessions: { ...prev.sessions, [s.id]: s },
      activeId: s.id,
    }));
  }, []);

  const switchSession = useCallback((id: string) => {
    setState((prev) => (prev.sessions[id] ? { ...prev, activeId: id } : prev));
  }, []);

  const clearHistory = useCallback(() => {
    const fresh = seedState(Date.now());
    setState(fresh);
    setTransient({});
    try {
      window.localStorage.setItem(STORAGE_KEY, serializeToString(fresh, Date.now()));
    } catch {
      /* best effort */
    }
  }, []);

  const sessions = useMemo(
    () => Object.values(state.sessions).sort((a, b) => b.createdAt - a.createdAt),
    [state.sessions],
  );

  // Only surface a "generating" indicator for sessions that still EXIST. Evicted/cleared sessions
  // can leave a dangling transient entry (transient is a parallel map); filtering by live
  // membership prevents ghost indicators in the rail.
  const generatingIds = useMemo(
    () =>
      Object.entries(transient)
        .filter(([id, t]) => t.sending && state.sessions[id])
        .map(([id]) => id),
    [transient, state.sessions],
  );

  const value: AppStateValue = {
    search,
    setSearch,
    agent,
    setAgent,
    attachTable,
    lastAttach,
    sessions,
    activeId: state.activeId,
    newSession,
    switchSession,
    clearHistory,
    generatingIds,
  };

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppStateValue {
  const v = useContext(Ctx);
  if (!v) throw new Error("useAppState must be used within AppStateProvider");
  return v;
}
