"use client";

import { useEffect, useState } from "react";
import { listDomains } from "@/lib/api";
import { useAppState } from "@/components/AppState";
import type { Session } from "@/lib/sessions";

// Derive a human label for a session: prefer its stored title, else the search query, else the
// first user turn's text, else a neutral "New session" placeholder.
function sessionTitle(s: Session): string {
  if (s.title && s.title !== "Untitled") return s.title;
  const q = s.search.q.trim();
  if (q) return q;
  const firstUser = s.agent.turns.find((t) => t.role === "user" && t.content?.trim());
  if (firstUser?.content) return firstUser.content.trim();
  return "Sesi baru";
}

function SageMark() {
  return (
    <div className="flex items-center gap-3">
      <div
        aria-hidden
        className="grid h-9 w-9 place-items-center rounded-xl bg-[color:var(--color-accent)] text-white shadow-sm"
      >
        {/* search glyph — the Sage mark */}
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round">
          <circle cx="10.5" cy="10.5" r="6.5" />
          <line x1="20" y1="20" x2="16" y2="16" />
        </svg>
      </div>
      <div className="leading-tight">
        <p className="text-[15px] font-semibold tracking-tight text-[color:var(--color-ink)]">Sage</p>
        <p className="text-[11px] text-[color:var(--color-muted)]">Cari &amp; susun draft SQL</p>
      </div>
    </div>
  );
}

export function SessionsRail() {
  const {
    sessions,
    activeId,
    newSession,
    switchSession,
    clearHistory,
    generatingIds,
    search,
    setSearch,
  } = useAppState();

  const { domain, res } = search;
  const [domains, setDomains] = useState<string[]>([]);

  useEffect(() => {
    listDomains()
      .then((d) => setDomains(d.domains))
      .catch(() => setDomains([]));
  }, []);

  function pickDomain(d: string | null) {
    // Category is a UI-only relabel of the `domain` field. Clearing the current result when the
    // facet changes lets SearchPane re-run against the new facet on the next submit; if results are
    // already present we keep them (SearchPane re-runs when it owns the query).
    setSearch((s) => ({ ...s, domain: d }));
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

  return (
    <aside className="flex h-full min-h-0 flex-col gap-5 border-r border-[color:var(--color-line)] bg-white/60 px-4 py-6 backdrop-blur">
      <SageMark />

      <button
        type="button"
        onClick={newSession}
        className="flex items-center justify-center gap-2 rounded-lg bg-[color:var(--color-accent)] px-3 py-2 text-sm font-semibold text-white transition-colors hover:brightness-95"
      >
        <span aria-hidden className="text-base leading-none">＋</span>
        Sesi baru
      </button>

      {/* Session list */}
      <div className="flex min-h-0 flex-col">
        <p className="mb-1.5 px-1 text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
          Sesi
        </p>
        <div className="flex min-h-0 flex-1 flex-col gap-0.5 overflow-y-auto">
          {sessions.map((s) => {
            const active = s.id === activeId;
            const generating = generatingIds.includes(s.id);
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => switchSession(s.id)}
                title={sessionTitle(s)}
                className={[
                  "flex items-center gap-2 rounded-md px-2.5 py-2 text-left text-sm transition-colors",
                  active
                    ? "bg-[color:var(--color-panel-2)] font-semibold text-[color:var(--color-ink)]"
                    : "text-[color:var(--color-muted)] hover:bg-[color:var(--color-panel-2)]/60 hover:text-[color:var(--color-ink)]",
                ].join(" ")}
              >
                {generating ? (
                  <span
                    aria-label="Sedang menyusun"
                    className="h-1.5 w-1.5 shrink-0 animate-ping rounded-full bg-[color:var(--color-accent)]"
                  />
                ) : (
                  <span
                    aria-hidden
                    className={[
                      "h-1.5 w-1.5 shrink-0 rounded-full",
                      active ? "bg-[color:var(--color-accent)]" : "bg-[color:var(--color-line)]",
                    ].join(" ")}
                  />
                )}
                <span className="truncate">{sessionTitle(s)}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Category facet (UI label for the `domain` field) */}
      <div className="flex min-h-0 flex-col">
        <p className="mb-1.5 px-1 text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
          Kategori
        </p>
        <div className="flex max-h-56 flex-col gap-0.5 overflow-y-auto">
          <FacetItem label="Semua kategori" active={domain === null} onClick={() => pickDomain(null)} />
          {domains.map((d) => (
            <FacetItem key={d} label={d} active={domain === d} onClick={() => pickDomain(d)} />
          ))}
        </div>
      </div>

      <div className="mt-auto flex flex-col gap-3">
        {/* Sort control — currently relevance-only (the backend orders by relevance). */}
        <div className="px-1 text-[11px] text-[color:var(--color-muted)]">
          <span className="font-semibold uppercase tracking-widest">Urutkan</span>
          <span className="ml-2">Relevansi</span>
        </div>

        <button
          type="button"
          onClick={onClear}
          disabled={!res && sessions.length <= 1}
          className="rounded-lg border border-[color:var(--color-line)] px-3 py-1.5 text-xs font-semibold text-[color:var(--color-muted)] transition-colors hover:border-[color:var(--color-danger)] hover:text-[color:var(--color-danger)] disabled:opacity-50"
        >
          Hapus riwayat
        </button>

        <div className="rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-3 py-2.5 text-[11px] leading-relaxed text-[color:var(--color-warn)]">
          Draft-only. SQL tidak pernah dieksekusi — periksa tabel, join, dan filter sebelum
          menjalankan.
        </div>
      </div>
    </aside>
  );
}

function FacetItem({
  label,
  active,
  onClick,
}: {
  label: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={[
        "rounded-md px-2.5 py-1.5 text-left text-sm capitalize transition-colors",
        active
          ? "bg-[color:var(--color-accent)]/10 font-semibold text-[color:var(--color-accent)]"
          : "text-[color:var(--color-muted)] hover:bg-[color:var(--color-panel-2)]",
      ].join(" ")}
    >
      {label}
    </button>
  );
}
