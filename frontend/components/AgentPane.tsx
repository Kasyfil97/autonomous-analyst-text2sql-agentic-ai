"use client";

import { useEffect, useRef, useState } from "react";
import {
  agentChat,
  type AgentReply,
  type AgentResponse,
  type ChatMessage,
  type GroundingEntry,
  type GroundingStrength,
  type MultiDraftResponse,
} from "@/lib/api";
import { ATTACH_DND_TYPE, MAX_ATTACHED } from "@/lib/attach";
import { SqlBlock } from "@/components/SqlBlock";
import { useAppState, type AttachAnnouncement, type ChatTurn } from "@/components/AppState";

// Build the polite screen-reader announcement string for an attach outcome. Shared by BOTH the
// "Kirim ke agent" button and the drag-drop path (they route through the same `lastAttach` signal),
// so keyboard and pointer users hear identical feedback.
function announceAttach(a: AttachAnnouncement): string {
  switch (a.result) {
    case "added":
      return `Tabel ${a.table} ditambahkan ke konteks agent.`;
    case "duplicate":
      return `Tabel ${a.table} sudah terlampir.`;
    case "cap":
      return `Maksimal ${MAX_ATTACHED} tabel. ${a.table} tidak ditambahkan.`;
  }
}

const UNVERIFIED_PREFIX = "-- UNVERIFIED DRAFT";

const TRY_ASKING = [
  "Berapa jumlah transaksi kartu kredit per bulan selama 2025?",
  "Total nominal transaksi per kantor cabang tahun ini",
  "Daftar nasabah baru per bulan dengan saldo rata-rata",
];

// Parse explanation into discrete bullet points.
function parsePoints(text: string): string[] {
  if (!text.trim()) return [];
  if (/^[-•*▸]\s/m.test(text)) {
    return text
      .split("\n")
      .map((l) => l.replace(/^[-•*▸]\s+/, "").trim())
      .filter(Boolean);
  }
  if (/^\d+[.)]\s/m.test(text)) {
    return text
      .split("\n")
      .map((l) => l.replace(/^\d+[.)]\s+/, "").trim())
      .filter(Boolean);
  }
  const lines = text.split("\n").map((l) => l.trim()).filter(Boolean);
  if (lines.length > 1) return lines;
  return text.split(/(?<=[.!?])\s+/).map((s) => s.trim()).filter(Boolean);
}

const STRENGTH_CONFIG: Record<
  Exclude<GroundingStrength, null>,
  { label: string; dot: string; tone: string }
> = {
  precedent_strong: {
    label: "Grounded in precedent",
    dot: "bg-[color:var(--color-accent)]",
    tone:
      "border-[color:var(--color-accent)]/40 bg-[color:var(--color-accent)]/8 text-[color:var(--color-accent)]",
  },
  schema_only: {
    label: "Schema-only — verify carefully",
    dot: "bg-[color:var(--color-warn)]",
    tone:
      "border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]",
  },
};

function StrengthBadge({ strength }: { strength: GroundingStrength }) {
  if (!strength) return null;
  const s = STRENGTH_CONFIG[strength];
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-[11px] font-semibold ${s.tone}`}
    >
      <span className={`h-1.5 w-1.5 rounded-full ${s.dot}`} />
      {s.label}
    </span>
  );
}

function SectionHeader({ icon, label }: { icon: string; label: string }) {
  return (
    <div className="flex items-center gap-2 pb-2">
      <span className="text-base leading-none">{icon}</span>
      <p className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
        {label}
      </p>
    </div>
  );
}

function ExplanationPoints({ text }: { text: string }) {
  const points = parsePoints(text);
  if (points.length <= 1) {
    return <p className="text-sm leading-relaxed text-[color:var(--color-ink)]">{text}</p>;
  }
  return (
    <ul className="grid gap-2.5">
      {points.map((p, i) => (
        <li key={i} className="flex items-start gap-3">
          <span className="mt-[2px] flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-accent)]/12 text-[10px] font-bold tabular-nums text-[color:var(--color-accent)]">
            {i + 1}
          </span>
          <span className="text-sm leading-relaxed text-[color:var(--color-ink)]">{p}</span>
        </li>
      ))}
    </ul>
  );
}

function AssumptionList({ items }: { items: string[] }) {
  return (
    <ul className="grid gap-1.5">
      {items.map((a, i) => (
        <li
          key={i}
          className="flex items-start gap-2.5 rounded-lg border border-dashed border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)]/50 px-3 py-2.5"
        >
          <span className="mt-px shrink-0 text-[12px] text-[color:var(--color-warn)]">△</span>
          <span className="text-sm leading-snug text-[color:var(--color-ink)]">{a}</span>
        </li>
      ))}
    </ul>
  );
}

function GroundingChips({ grounding }: { grounding: GroundingEntry[] }) {
  const shown = grounding.filter((g) => g.in_kb);
  if (shown.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.map((g) => (
        <span
          key={g.name}
          title={g.retrieved ? "Grounded — retrieved this session" : "Named but not retrieved"}
          className={[
            "inline-flex items-center gap-1.5 rounded-full px-3 py-1 font-mono text-[11px] font-medium",
            g.retrieved
              ? "bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)] ring-1 ring-[color:var(--color-accent)]/20"
              : "border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]",
          ].join(" ")}
        >
          <span className="text-[10px]">{g.retrieved ? "✓" : "⚠"}</span>
          {g.name}
        </span>
      ))}
    </div>
  );
}

function WarningItem({ text }: { text: string }) {
  const critical = text.includes("[CRITICAL]") || text.includes("[HIGH]");
  return (
    <div
      className={[
        "flex items-start gap-2.5 rounded-lg border px-3 py-2.5 text-xs leading-relaxed",
        critical
          ? "border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]"
          : "border-[color:var(--color-line)] bg-[color:var(--color-panel-2)] text-[color:var(--color-muted)]",
      ].join(" ")}
    >
      <span className="shrink-0 text-[13px]">{critical ? "🔴" : "⚡"}</span>
      <span>{text}</span>
    </div>
  );
}

/** The rich assistant answer — interpretation, assumptions, sources, warnings, and the draft SQL. */
function AgentResultCard({ result }: { result: AgentResponse }) {
  return (
    <div className="overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] shadow-sm">
      <div className="flex items-center justify-between gap-3 border-b border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/70 px-5 py-3">
        <span className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
          Hasil Analitik
        </span>
        <StrengthBadge strength={result.grounding_strength} />
      </div>

      <div className="divide-y divide-[color:var(--color-line)]">
        {result.declined ? (
          // Gate-decline: an intentional safety hold, visually distinct from a network error.
          <div className="border-l-4 border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)]/40 px-5 py-5">
            <div className="flex items-center gap-2 text-sm font-semibold text-[color:var(--color-warn)]">
              <span>🚫</span> Agent menahan draft
            </div>
            <p className="mt-1 text-xs text-[color:var(--color-muted)]">
              Ini adalah penahanan keamanan yang disengaja, bukan kegagalan jaringan.
            </p>
            {result.missing && (
              <p className="mt-2 text-xs text-[color:var(--color-warn)]">{result.missing}</p>
            )}
          </div>
        ) : (
          <>
            {result.explanation && (
              <div className="px-5 py-4">
                <SectionHeader icon="💡" label="Interpretasi" />
                <ExplanationPoints text={result.explanation} />
              </div>
            )}

            {result.assumptions.length > 0 && (
              <div className="px-5 py-4">
                <SectionHeader icon="△" label="Asumsi (periksa & sesuaikan)" />
                <AssumptionList items={result.assumptions} />
              </div>
            )}

            {result.grounding.some((g) => g.in_kb) && (
              <div className="px-5 py-4">
                <SectionHeader icon="🗄" label="Tabel Sumber" />
                <GroundingChips grounding={result.grounding} />
              </div>
            )}

            {result.warnings.length > 0 && (
              <div className="px-5 py-4">
                <SectionHeader icon="⚠" label="Peringatan" />
                <div className="grid gap-1.5">
                  {result.warnings.map((w, i) => (
                    <WarningItem key={i} text={w} />
                  ))}
                </div>
              </div>
            )}

            {result.sql && (
              <div className="px-5 py-4">
                <SectionHeader icon="📝" label="Draft SQL" />
                <SqlBlock sql={result.sql} />
              </div>
            )}
          </>
        )}
      </div>
    </div>
  );
}

/** A decomposed multi-part answer: a sub-draft card per sub-need + the reconciliation. */
function MultiDraftCard({ result }: { result: MultiDraftResponse }) {
  return (
    <div className="grid gap-4">
      <div className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/60 px-5 py-3">
        <span className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
          Permintaan multi-bagian — {result.sub_drafts.length} sub-draft
        </span>
      </div>

      {result.sub_drafts.map((sd, i) => (
        <div key={i} className="grid gap-2">
          <div className="flex items-center gap-2 px-1">
            <span className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-[color:var(--color-accent)]/12 text-[10px] font-bold tabular-nums text-[color:var(--color-accent)]">
              {i + 1}
            </span>
            <span className="text-sm font-medium text-[color:var(--color-ink)]">{sd.sub_need}</span>
          </div>
          <AgentResultCard result={sd.result} />
        </div>
      ))}

      {(result.reconciliation || result.combined_sql || result.warnings.length > 0) && (
        <div className="overflow-hidden rounded-2xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] shadow-sm">
          <div className="border-b border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/70 px-5 py-3">
            <span className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
              Penggabungan
            </span>
          </div>
          <div className="divide-y divide-[color:var(--color-line)]">
            {result.reconciliation && (
              <div className="px-5 py-4">
                <SectionHeader icon="🔗" label="Cara menggabung" />
                <ExplanationPoints text={result.reconciliation} />
              </div>
            )}
            {result.warnings.length > 0 && (
              <div className="px-5 py-4">
                <SectionHeader icon="⚠" label="Peringatan lintas-draft" />
                <div className="grid gap-1.5">
                  {result.warnings.map((w, i) => (
                    <WarningItem key={i} text={w} />
                  ))}
                </div>
              </div>
            )}
            {result.combined_sql && (
              <div className="px-5 py-4">
                <SectionHeader icon="📝" label="Draft SQL gabungan (saran — verifikasi)" />
                <SqlBlock sql={result.combined_sql} />
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

/** Flatten an assistant response (single or multi-draft) into plain text for prior context. */
function assistantContext(r: AgentReply): string {
  if (r.kind === "sql_multi") {
    const parts = r.sub_drafts.map(
      (sd, i) => `[${i + 1}] ${sd.sub_need}\n${assistantContext(sd.result)}`,
    );
    if (r.reconciliation) parts.push("Reconciliation: " + r.reconciliation);
    return parts.join("\n\n") || "(no answer)";
  }
  const parts: string[] = [];
  if (r.declined && r.missing) parts.push(r.missing);
  if (r.explanation) parts.push(r.explanation);
  if (r.assumptions.length) parts.push("Assumptions: " + r.assumptions.join("; "));
  if (r.sql) {
    const sql = r.sql
      .split("\n")
      .filter((l) => !l.startsWith(UNVERIFIED_PREFIX))
      .join("\n")
      .trim();
    if (sql) parts.push("SQL:\n" + sql);
  }
  return parts.join("\n\n") || "(no answer)";
}

/** Grounded tables to carry into the next turn — union across sub-drafts for a multi answer. */
function carryTables(r: AgentReply): string[] {
  if (r.kind === "sql_multi") {
    return Array.from(new Set(r.sub_drafts.flatMap((sd) => sd.result.tables_used)));
  }
  return r.tables_used;
}

function UserBubble({ turn }: { turn: ChatTurn }) {
  return (
    <div className="flex flex-col items-end gap-1.5">
      <div className="max-w-[85%] rounded-2xl rounded-br-md bg-[color:var(--color-accent)] px-4 py-2.5 text-sm leading-relaxed text-white">
        {turn.content}
      </div>
      {turn.attached && turn.attached.length > 0 && (
        <div className="flex flex-wrap justify-end gap-1">
          {turn.attached.map((t) => (
            <span
              key={t}
              className="rounded-full bg-[color:var(--color-panel-2)] px-2 py-0.5 font-mono text-[11px] text-[color:var(--color-muted)]"
            >
              {t}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

export function AgentPane() {
  const { agent, setAgent, attachTable, lastAttach, newSession, clearHistory, sessions, search } =
    useAppState();
  const { attached, question, turns, sending } = agent;
  const endRef = useRef<HTMLDivElement | null>(null);
  const atCap = attached.length >= MAX_ATTACHED;

  // Drop-zone highlight: true while a valid table drag is over the drop region. Decorative only
  // (no SR requirement) — cleared on drop/leave. A nested-enter counter would be sturdier, but the
  // single drop region here has no interactive children that steal dragenter, so a boolean is fine.
  const [dragOver, setDragOver] = useState(false);

  // The polite announcement mirrored into an aria-live region for BOTH attach paths. Re-fires on
  // every attempt (the `nonce` in lastAttach forces a new string via the key below).
  const announcement = lastAttach ? announceAttach(lastAttach) : "";

  // Keep the newest turn in view.
  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [turns, sending]);

  const dropHasTable = (e: React.DragEvent) =>
    e.dataTransfer.types.includes(ATTACH_DND_TYPE);

  function onDragOver(e: React.DragEvent) {
    if (!dropHasTable(e)) return;
    e.preventDefault(); // allow the drop
    e.dataTransfer.dropEffect = "copy";
    if (!dragOver) setDragOver(true);
  }

  function onDragLeave(e: React.DragEvent) {
    // Only clear when the pointer actually leaves the drop region (not on child-boundary events).
    if (e.currentTarget.contains(e.relatedTarget as Node | null)) return;
    setDragOver(false);
  }

  function onDrop(e: React.DragEvent) {
    const table = e.dataTransfer.getData(ATTACH_DND_TYPE);
    setDragOver(false);
    if (!table) return;
    e.preventDefault();
    attachTable(table); // dedupe + cap + announcement handled centrally
  }

  async function submit(e?: React.FormEvent) {
    e?.preventDefault();
    const text = question.trim();
    if (!text || sending) return;

    const userTurn: ChatTurn = {
      id: crypto.randomUUID(),
      role: "user",
      content: text,
      attached: attached.length ? [...attached] : undefined,
    };

    // History the model sees: every prior turn flattened to {role, content}.
    const history: ChatMessage[] = turns
      .map((t): ChatMessage | null => {
        if (t.role === "user" && t.content) return { role: "user", content: t.content };
        if (t.role === "assistant" && t.response)
          return { role: "assistant", content: assistantContext(t.response) };
        return null;
      })
      .filter((m): m is ChatMessage => m !== null);

    // Carry the last answer's grounded tables so a follow-up stays grounded without re-searching.
    const lastAnswer = [...turns].reverse().find((t) => t.role === "assistant" && t.response)
      ?.response;
    const carry = lastAnswer ? carryTables(lastAnswer) : [];
    const sendAttached = Array.from(new Set([...attached, ...carry]));

    setAgent((s) => ({
      ...s,
      turns: [...s.turns, userTurn],
      question: "",
      attached: [],
      sending: true,
    }));

    try {
      const res = await agentChat(text, sendAttached, history);
      setAgent((s) => ({
        ...s,
        turns: [...s.turns, { id: crypto.randomUUID(), role: "assistant", response: res }],
        sending: false,
      }));
    } catch (err) {
      setAgent((s) => ({
        ...s,
        turns: [
          ...s.turns,
          {
            id: crypto.randomUUID(),
            role: "assistant",
            error: err instanceof Error ? err.message : "Agent tidak tersedia.",
          },
        ],
        sending: false,
      }));
    }
  }

  function onClear() {
    if (
      typeof window !== "undefined" &&
      !window.confirm("Hapus semua sesi dan riwayat? Tindakan ini tidak dapat dibatalkan.")
    ) {
      return;
    }
    clearHistory();
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-col px-6 py-6">
      <header className="flex items-start justify-between gap-4 pb-4">
        <div className="flex min-w-0 items-start gap-3">
          {/* AI mark — sparkle glyph in an accent square (matches the Sage mark treatment). */}
          <div
            aria-hidden
            className="grid h-9 w-9 shrink-0 place-items-center rounded-xl bg-[color:var(--color-accent)] text-white shadow-sm"
          >
            <svg width="18" height="18" viewBox="0 0 24 24" fill="currentColor">
              <path d="M12 2l1.9 7.1a3 3 0 0 0 2.12 2.12L23 12l-6.98 1.88a3 3 0 0 0-2.12 2.12L12 22l-1.9-7.1a3 3 0 0 0-2.12-2.12L1 12l6.98-1.88A3 3 0 0 0 10.1 8z" />
            </svg>
          </div>
          <div className="min-w-0">
            <div className="flex items-baseline gap-2">
              <h1 className="text-lg font-semibold tracking-tight">AI Data Agent</h1>
              <span className="text-sm font-normal text-[color:var(--color-muted)]">Agent</span>
            </div>
            <p className="mt-1 text-sm text-[color:var(--color-muted)]">
              Percakapan analitik — agent menyusun <strong>draft SQL</strong> (tidak dieksekusi).
            </p>
          </div>
        </div>
        <div className="flex shrink-0 items-center gap-2">
          <button
            type="button"
            onClick={newSession}
            className="inline-flex items-center gap-1.5 rounded-lg bg-[color:var(--color-accent-strong)] px-3 py-1.5 text-white transition-colors hover:brightness-95"
          >
            <span aria-hidden className="text-sm leading-none">＋</span>
            <span className="flex flex-col leading-tight">
              <span className="text-xs font-semibold">Sesi baru</span>
              <span className="text-[9px] font-normal text-white/70">New session</span>
            </span>
          </button>
          <button
            type="button"
            onClick={onClear}
            disabled={!search.res && sessions.length <= 1}
            className="flex flex-col rounded-lg border border-[color:var(--color-accent-strong)] px-3 py-1 text-xs font-semibold text-[color:var(--color-accent-strong)] transition-colors hover:bg-[color:var(--color-accent-strong)]/8 disabled:opacity-50"
          >
            <span>Hapus riwayat</span>
            <span className="text-[9px] font-normal text-[color:var(--color-accent-strong)]/70">
              Clear history
            </span>
          </button>
        </div>
      </header>

      {/* Conversation */}
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto pr-1">
        {turns.length === 0 && !sending && (
          <div className="rounded-xl border border-dashed border-[color:var(--color-muted)]/35 bg-[color:var(--color-panel-2)]/50 px-6 py-8">
            <p className="flex items-baseline gap-2 text-sm font-semibold text-[color:var(--color-ink)]">
              Coba tanyakan
              <span className="text-xs font-normal text-[color:var(--color-muted)]">Try asking</span>
            </p>
            <p className="mt-1 text-xs text-[color:var(--color-muted)]">
              Mulai percakapan, atau kirim tabel dari hasil pencarian untuk melampirkannya.
            </p>
            <div className="mt-4 grid gap-2">
              {TRY_ASKING.map((prompt) => (
                <button
                  key={prompt}
                  type="button"
                  onClick={() => setAgent((s) => ({ ...s, question: prompt }))}
                  className="flex items-center gap-2.5 rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-3 py-2 text-left text-sm text-[color:var(--color-ink)] transition-colors hover:border-[color:var(--color-accent)]/50 hover:text-[color:var(--color-accent)]"
                >
                  {/* idea / lamp glyph */}
                  <svg
                    aria-hidden
                    width="15"
                    height="15"
                    viewBox="0 0 24 24"
                    fill="none"
                    stroke="currentColor"
                    strokeWidth="1.8"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    className="shrink-0 text-[color:var(--color-accent)]"
                  >
                    <path d="M9 18h6" />
                    <path d="M10 21h4" />
                    <path d="M12 2a7 7 0 0 0-4 12.7c.6.5 1 1.3 1 2.1V17h6v-.2c0-.8.4-1.6 1-2.1A7 7 0 0 0 12 2z" />
                  </svg>
                  <span>{prompt}</span>
                </button>
              ))}
            </div>
          </div>
        )}

        {turns.map((t) =>
          t.role === "user" ? (
            <UserBubble key={t.id} turn={t} />
          ) : t.error ? (
            // Network / availability error — distinct from a gate-decline (which renders inside
            // AgentResultCard with a safety-hold treatment).
            <div
              key={t.id}
              className="rounded-xl border border-[color:var(--color-danger)]/40 bg-[color:var(--color-danger)]/5 px-4 py-3 text-xs text-[color:var(--color-danger)]"
            >
              <p className="font-semibold">Agent tidak tersedia</p>
              <p className="mt-1">{t.error}</p>
            </div>
          ) : t.response ? (
            t.response.kind === "sql_multi" ? (
              <MultiDraftCard key={t.id} result={t.response} />
            ) : (
              <AgentResultCard key={t.id} result={t.response} />
            )
          ) : null,
        )}

        {sending && (
          <div className="flex items-center gap-3 text-sm text-[color:var(--color-muted)]">
            <span className="h-2 w-2 animate-ping rounded-full bg-[color:var(--color-accent)]" />
            Mencari skema &amp; precedent, lalu menyusun draft SQL…
          </div>
        )}
        <div ref={endRef} />
      </div>

      {/* aria-live: announces attach outcome (success / already-attached / cap) for BOTH the
          "Kirim ke agent" button and the drag-drop path. Keyed by nonce so identical repeat
          announcements still fire. Visually hidden — the highlight/chip are the visual channel. */}
      <div aria-live="polite" className="sr-only">
        {lastAttach ? <span key={lastAttach.nonce}>{announcement}</span> : null}
      </div>

      {/* Composer + drop target. Dropping a dragged result card here attaches it to the context. */}
      <form
        onSubmit={submit}
        onDragOver={onDragOver}
        onDragLeave={onDragLeave}
        onDrop={onDrop}
        className={[
          "mt-4 rounded-xl border-t pt-4 transition-colors",
          dragOver
            ? "border border-dashed border-[color:var(--color-accent)] bg-[color:var(--color-accent)]/5"
            : "border-[color:var(--color-line)]",
        ].join(" ")}
      >
        {/* Drop-zone hint: guides drag; a distinct cap message once 10 are attached. */}
        <p
          className={[
            "mb-2 text-[11px] transition-colors",
            dragOver
              ? "font-semibold text-[color:var(--color-accent)]"
              : "text-[color:var(--color-muted)]",
          ].join(" ")}
        >
          {atCap
            ? `Maksimal ${MAX_ATTACHED} tabel terlampir.`
            : "Lepas tabel di sini untuk menambah konteks"}
        </p>
        {attached.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attached.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1.5 rounded-full bg-[color:var(--color-panel-2)] px-2.5 py-1 font-mono text-xs"
              >
                {t}
                <button
                  type="button"
                  aria-label={`Remove ${t}`}
                  onClick={() =>
                    setAgent((s) => ({ ...s, attached: s.attached.filter((x) => x !== t) }))
                  }
                  className="text-[color:var(--color-muted)] hover:text-[color:var(--color-danger)]"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            value={question}
            onChange={(e) => setAgent((s) => ({ ...s, question: e.target.value }))}
            onKeyDown={onKeyDown}
            placeholder={
              turns.length === 0
                ? "mis. Berapa jumlah transaksi kartu kredit per bulan selama 2025?"
                : "Ajukan lanjutan — mis. ubah tahun jadi 2024, atau tambah group by bulan"
            }
            rows={2}
            className="w-full resize-y rounded-lg border border-[color:var(--color-line)] bg-white px-4 py-2.5 text-sm outline-none focus:border-[color:var(--color-accent)] focus:ring-2 focus:ring-[color:var(--color-accent)]/20"
          />
          <button
            type="submit"
            disabled={sending || !question.trim()}
            className="shrink-0 self-end rounded-lg bg-[color:var(--color-accent-strong)] px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:brightness-95 disabled:opacity-60"
          >
            {sending ? "Menyusun…" : "Kirim"}
          </button>
        </div>
      </form>
    </div>
  );
}
