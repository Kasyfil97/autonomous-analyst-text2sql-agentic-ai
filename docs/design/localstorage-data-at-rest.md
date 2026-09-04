# Data-at-rest risk acknowledgement — Sage session `localStorage`

Sage persists workspace session data to the browser's **unencrypted `localStorage`**, under
the versioned key `sage.sessions.v1`. Persisted content includes prior searches (query text and
category), agent chat turns (the user's questions), the identifiers of tables attached to the
agent context, and the drafted SQL the agent produced. There is no server-side session store —
the FastAPI backend stays stateless — so this browser store is the only place session history
lives across page reloads.

This is the **accepted at-rest store** for Sage as an internal, desktop-only tool: the app runs
on network-restricted analyst machines, the drafted SQL is never executed, and the shared bearer
token (`NEXT_PUBLIC_API_TOKEN`) is already inlined into the shipped bundle rather than being a
real secret — the network-restricted host is the actual access control. Given that posture,
encrypting `localStorage` (which would require a key that must itself live in the browser) buys
little, so plaintext `localStorage` is a deliberate, documented choice rather than an oversight.

To limit what sits at rest, the redaction in `frontend/lib/sessions.ts` (`redactForStorage`)
**excludes the free-text schema business meaning** before anything is written: search-card
`headline`/`description` are dropped, and agent-response `explanation`, `assumptions`,
`warnings`, and the decline `missing` note are stripped. Table/column identifiers, `n_columns`
counts, PII flags, and the drafted SQL are retained so a session can be restored; **PII column
detail (dictionary rows with business meanings) is never persisted** — the column dictionary is
re-fetched on demand from the KB, not stored. Retention is bounded by a **30-day TTL** (sessions
older than that are dropped on hydrate) and a **20-session cap** (oldest sessions pruned beyond
the cap). The rail's **"Clear history"** control purges the persisted store and all in-memory
copies, resetting to a single fresh session.
