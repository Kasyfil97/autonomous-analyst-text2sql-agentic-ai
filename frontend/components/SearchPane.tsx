"use client";

import { useEffect, useRef, useState } from "react";
import { searchTables } from "@/lib/api";
import { MAX_ATTACHED } from "@/lib/attach";
import { useDomains } from "@/lib/useDomains";
import { TableCard } from "@/components/TableCard";
import { useAppState } from "@/components/AppState";

function Skeletons() {
  return (
    <div className="grid gap-3">
      {[0, 1, 2, 3].map((i) => (
        <div
          key={i}
          className="h-28 animate-pulse rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/50"
        />
      ))}
    </div>
  );
}

export function SearchPane({ onOpenTable }: { onOpenTable: (physicalName: string) => void }) {
  const { search, setSearch, agent, attachTable, activeId } = useAppState();
  const { q, domain, res } = search;
  const atCap = agent.attached.length >= MAX_ATTACHED;

  const domains = useDomains();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Emptying the query returns the pane to its initial state: clear the stored results (so the
  // cards disappear) and any error. A non-empty edit just updates the text.
  const setQ = (value: string) => {
    setSearch((s) => ({ ...s, q: value, res: value.trim() ? s.res : null }));
    if (!value.trim()) setError(null);
  };

  function clearSearch() {
    setSearch((s) => ({ ...s, q: "", res: null }));
    setError(null);
  }

  // Category facet is now a horizontally-scrollable chip row under the search bar (was the left
  // rail). Selecting a chip updates `domain`; the effect below re-runs the search when results are
  // already present, keeping the chips and the result list in sync.
  const pickDomain = (d: string | null) => setSearch((s) => ({ ...s, domain: d }));

  async function run(query: string, dom: string | null) {
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const result = await searchTables(query, dom);
      setSearch((s) => ({ ...s, res: result }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Pencarian gagal.");
      setSearch((s) => ({ ...s, res: null }));
    } finally {
      setLoading(false);
    }
  }

  // Re-run when the Category facet (rail's `domain`) changes AND we already have results, so the
  // rail facet and the results stay in sync without duplicating the facet UI in this pane.
  //
  // The guard is SESSION-AWARE: a session switch also changes the visible `domain` (each session
  // carries its own facet), but that is NOT a genuine facet change and must NOT re-run a search
  // (which would clobber the switched-to session's existing `res`). We track the last-seen
  // {activeId, domain} and only re-run when `domain` changed while `activeId` stayed the same; on a
  // switch we just sync the ref.
  const prevFacet = useRef({ activeId, domain });
  useEffect(() => {
    const prev = prevFacet.current;
    const sameSession = prev.activeId === activeId;
    const domainChanged = prev.domain !== domain;
    prevFacet.current = { activeId, domain };
    if (sameSession && domainChanged && res && q.trim()) run(q, domain);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain, activeId]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    run(q, domain);
  }

  // The button attach path. Enforces dedupe + the cap of 10 and announces the outcome via the
  // shared aria-live region (in AgentPane), matching the drag-drop path.
  function askAgent(table: string) {
    attachTable(table);
  }

  const cards = res?.results ?? [];

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <header className="border-b border-[color:var(--color-line)] px-6 pb-4 pt-6">
        <div className="flex items-baseline gap-2">
          <h1 className="text-lg font-semibold tracking-tight">Cari data BRI</h1>
          <span className="text-sm font-normal text-[color:var(--color-muted)]">Search</span>
        </div>
        <p className="mt-1 text-sm text-[color:var(--color-muted)]">
          Ketik kebutuhan Anda dalam bahasa sehari-hari — pencarian memahami maksud, bukan sekadar
          kata kunci.
        </p>
        <form onSubmit={onSubmit} className="mt-3 flex gap-2">
          <div className="relative w-full">
            <input
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder='mis. "transaksi kartu kredit per bulan"'
              className="w-full rounded-lg border border-[color:var(--color-line)] bg-white px-4 py-2.5 pr-10 text-sm outline-none focus:border-[color:var(--color-accent)] focus:ring-2 focus:ring-[color:var(--color-accent)]/20"
            />
            {q && (
              <button
                type="button"
                onClick={clearSearch}
                aria-label="Hapus pencarian"
                className="absolute inset-y-0 right-2 my-auto grid h-6 w-6 place-items-center rounded-md text-[color:var(--color-muted)] transition-colors hover:bg-[color:var(--color-panel-2)] hover:text-[color:var(--color-ink)]"
              >
                ✕
              </button>
            )}
          </div>
          <button
            type="submit"
            className="shrink-0 rounded-lg bg-[color:var(--color-accent-strong)] px-5 text-sm font-semibold text-white transition-colors hover:brightness-95"
          >
            Cari
          </button>
        </form>

        {/* Category filter — a quick "Semua" chip plus a Category dropdown (replaces the old rail). */}
        <div className="mt-3 flex items-center gap-2">
          <CategoryChip label="Semua" active={domain === null} onClick={() => pickDomain(null)} />
          <CategoryDropdown domains={domains} value={domain} onChange={pickDomain} />
          <span className="ml-auto shrink-0 whitespace-nowrap text-[11px] text-[color:var(--color-muted)]">
            <span className="font-semibold uppercase tracking-widest">Urutkan</span>
            <span className="ml-1.5">Relevansi</span>
          </span>
        </div>
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {loading && <Skeletons />}

        {!loading && error && (
          <div className="grid gap-3">
            <div className="rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-3 text-sm text-[color:var(--color-warn)]">
              {error}
            </div>
            <button
              type="button"
              onClick={() => run(q, domain)}
              className="w-fit rounded-lg border border-[color:var(--color-line)] px-3 py-1.5 text-xs font-semibold text-[color:var(--color-muted)] transition-colors hover:border-[color:var(--color-accent)] hover:text-[color:var(--color-accent)]"
            >
              Coba lagi
            </button>
          </div>
        )}

        {!loading && !error && !res && (
          <EmptyState
            title="Mulai dari sebuah kebutuhan data"
            hint="Atau telusuri berdasarkan kategori di bawah kolom pencarian."
            domains={domains}
            onPick={(d) => {
              setSearch((s) => ({ ...s, domain: d, q: d }));
              run(d, d);
            }}
          />
        )}

        {!loading && !error && res && cards.length === 0 && (
          <div className="grid gap-4">
            <div className="rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-4 py-3 text-sm text-[color:var(--color-muted)]">
              {res.filter_caused_empty
                ? "Tidak ada hasil untuk kategori ini. Berikut tabel terkait terdekat:"
                : "Tidak ada tabel yang cocok. Coba istilah lain atau bahasa Inggris."}
            </div>
            {res.closest_related?.map((c) => (
              <TableCard key={c.id} card={c} onOpen={onOpenTable} onAsk={askAgent} atCap={atCap} />
            ))}
          </div>
        )}

        {!loading && !error && cards.length > 0 && (
          <div className="grid gap-3">
            <p className="text-xs text-[color:var(--color-muted)]">
              {cards.length} tabel · diurutkan berdasarkan relevansi
            </p>
            {cards.map((c) => (
              <TableCard key={c.id} card={c} onOpen={onOpenTable} onAsk={askAgent} atCap={atCap} />
            ))}
          </div>
        )}
      </section>
    </div>
  );
}

function CategoryChip({
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
      type="button"
      onClick={onClick}
      className={[
        "shrink-0 whitespace-nowrap rounded-lg px-3 py-1.5 text-xs font-semibold capitalize transition-colors",
        active
          ? "bg-[color:var(--color-accent-strong)] text-white"
          : "border border-[color:var(--color-line)] bg-[color:var(--color-panel)] text-[color:var(--color-muted)] hover:border-[color:var(--color-accent)]/50 hover:text-[color:var(--color-accent)]",
      ].join(" ")}
    >
      {label}
    </button>
  );
}

function CategoryDropdown({
  domains,
  value,
  onChange,
}: {
  domains: string[];
  value: string | null;
  onChange: (d: string | null) => void;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  // Close on outside click or Escape while the menu is open.
  useEffect(() => {
    if (!open) return;
    function onDoc(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("mousedown", onDoc);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onDoc);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const pick = (d: string | null) => {
    onChange(d);
    setOpen(false);
  };

  const active = value !== null;

  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-haspopup="listbox"
        aria-expanded={open}
        className={[
          "inline-flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors",
          active
            ? "border-[color:var(--color-accent)] bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
            : "border-[color:var(--color-line)] bg-[color:var(--color-panel)] text-[color:var(--color-muted)] hover:border-[color:var(--color-accent)]/50 hover:text-[color:var(--color-accent)]",
        ].join(" ")}
      >
        {/* filter/sliders glyph */}
        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
          <line x1="4" y1="7" x2="20" y2="7" />
          <line x1="7" y1="12" x2="17" y2="12" />
          <line x1="10" y1="17" x2="14" y2="17" />
        </svg>
        <span className="capitalize">{value ?? "Kategori"}</span>
        <svg
          width="11"
          height="11"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          className={open ? "rotate-180 transition-transform" : "transition-transform"}
        >
          <polyline points="6 9 12 15 18 9" />
        </svg>
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-full z-20 mt-1.5 max-h-72 w-56 overflow-y-auto rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel)] p-1 shadow-lg"
        >
          <DropdownItem label="Semua kategori" active={value === null} onClick={() => pick(null)} />
          {domains.map((d) => (
            <DropdownItem key={d} label={d} active={value === d} onClick={() => pick(d)} />
          ))}
        </div>
      )}
    </div>
  );
}

function DropdownItem({
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
      type="button"
      role="option"
      aria-selected={active}
      onClick={onClick}
      className={[
        "flex w-full items-center justify-between gap-2 rounded-md px-2.5 py-1.5 text-left text-sm capitalize transition-colors",
        active
          ? "bg-[color:var(--color-accent)]/10 font-semibold text-[color:var(--color-accent)]"
          : "text-[color:var(--color-ink)] hover:bg-[color:var(--color-panel-2)]",
      ].join(" ")}
    >
      {label}
      {active && <span className="text-[color:var(--color-accent)]">✓</span>}
    </button>
  );
}

function EmptyState({
  title,
  hint,
  domains,
  onPick,
}: {
  title: string;
  hint: string;
  domains: string[];
  onPick: (d: string) => void;
}) {
  return (
    <div className="rounded-xl border border-dashed border-[color:var(--color-line)] px-6 py-10 text-center">
      {/* data-search glyph above the copy */}
      <div
        aria-hidden
        className="mx-auto mb-3 grid h-11 w-11 place-items-center rounded-full bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
      >
        <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
          <ellipse cx="12" cy="5" rx="7" ry="3" />
          <path d="M5 5v6c0 1.66 3.13 3 7 3s7-1.34 7-3V5" />
          <path d="M5 11v6c0 1.66 3.13 3 7 3" />
          <circle cx="17.5" cy="17.5" r="3" />
          <line x1="22" y1="22" x2="19.6" y2="19.6" />
        </svg>
      </div>
      <p className="text-base font-semibold">{title}</p>
      <p className="mt-1 text-sm text-[color:var(--color-muted)]">{hint}</p>
      <div className="mt-5 flex flex-wrap justify-center gap-2">
        {domains.slice(0, 10).map((d) => (
          <button
            key={d}
            onClick={() => onPick(d)}
            className="rounded-full border border-[color:var(--color-line)] px-3 py-1 text-xs capitalize text-[color:var(--color-muted)] transition-colors hover:border-[color:var(--color-accent)] hover:text-[color:var(--color-accent)]"
          >
            {d}
          </button>
        ))}
      </div>
    </div>
  );
}
