"use client";

import { useEffect, useRef, useState } from "react";
import { listDomains, searchTables } from "@/lib/api";
import { MAX_ATTACHED } from "@/lib/attach";
import { TableCard } from "@/components/TableCard";
import { TableDetailPanel } from "@/components/TableDetailPanel";
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

export function SearchPane() {
  const { search, setSearch, agent, attachTable } = useAppState();
  const { q, domain, res } = search;
  const atCap = agent.attached.length >= MAX_ATTACHED;

  const [domains, setDomains] = useState<string[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [detailId, setDetailId] = useState<string | null>(null);

  const setQ = (value: string) => setSearch((s) => ({ ...s, q: value }));

  useEffect(() => {
    listDomains()
      .then((d) => setDomains(d.domains))
      .catch(() => setDomains([]));
  }, []);

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
  const prevDomain = useRef(domain);
  useEffect(() => {
    if (prevDomain.current !== domain) {
      prevDomain.current = domain;
      if (res && q.trim()) run(q, domain);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domain]);

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    run(q, domain);
  }

  // The button + slide-over attach path. Enforces dedupe + the cap of 10 and announces the
  // outcome via the shared aria-live region (in AgentPane), matching the drag-drop path.
  function askAgent(table: string) {
    attachTable(table);
  }

  const cards = res?.results ?? [];

  return (
    <div className="relative flex h-full min-h-0 flex-col">
      <header className="border-b border-[color:var(--color-line)] px-6 pb-4 pt-6">
        <h1 className="text-lg font-semibold tracking-tight">Cari data BRI</h1>
        <p className="mt-1 text-sm text-[color:var(--color-muted)]">
          Ketik kebutuhan Anda dalam bahasa sehari-hari — pencarian memahami maksud, bukan sekadar
          kata kunci.
        </p>
        <form onSubmit={onSubmit} className="mt-3 flex gap-2">
          <input
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder='mis. "transaksi kartu kredit per bulan"'
            className="w-full rounded-lg border border-[color:var(--color-line)] bg-white px-4 py-2.5 text-sm outline-none focus:border-[color:var(--color-accent)] focus:ring-2 focus:ring-[color:var(--color-accent)]/20"
          />
          <button
            type="submit"
            className="shrink-0 rounded-lg bg-[color:var(--color-accent)] px-5 text-sm font-semibold text-white transition-colors hover:brightness-95"
          >
            Cari
          </button>
        </form>
      </header>

      <section className="min-h-0 flex-1 overflow-y-auto px-6 py-5">
        {domain && (
          <div className="mb-4 flex items-center gap-2">
            <span className="inline-flex items-center gap-2 rounded-full bg-[color:var(--color-panel-2)] px-3 py-1 text-xs font-medium">
              Kategori: {domain}
              <button
                onClick={() => setSearch((s) => ({ ...s, domain: null }))}
                aria-label="Hapus filter"
                className="text-[color:var(--color-muted)] hover:text-[color:var(--color-danger)]"
              >
                ✕
              </button>
            </span>
          </div>
        )}

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
            hint="Atau telusuri berdasarkan kategori di rail kiri."
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
              <TableCard
                key={c.id}
                card={c}
                onOpen={setDetailId}
                onAsk={askAgent}
                onDragStartCard={() => setDetailId(null)}
                atCap={atCap}
              />
            ))}
          </div>
        )}

        {!loading && !error && cards.length > 0 && (
          <div className="grid gap-3">
            <p className="text-xs text-[color:var(--color-muted)]">
              {cards.length} tabel · diurutkan berdasarkan relevansi
            </p>
            {cards.map((c) => (
              <TableCard
                key={c.id}
                card={c}
                onOpen={setDetailId}
                onAsk={askAgent}
                onDragStartCard={() => setDetailId(null)}
                atCap={atCap}
              />
            ))}
          </div>
        )}
      </section>

      {detailId && (
        <TableDetailPanel
          id={detailId}
          onClose={() => setDetailId(null)}
          onAttach={attachTable}
        />
      )}
    </div>
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
