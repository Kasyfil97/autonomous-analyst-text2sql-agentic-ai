"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { listDomains, searchTables, type SearchResponse } from "@/lib/api";
import { TableCard } from "@/components/TableCard";

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

export default function SearchPage() {
  const router = useRouter();
  const [q, setQ] = useState("");
  const [domain, setDomain] = useState<string | null>(null);
  const [domains, setDomains] = useState<string[]>([]);
  const [res, setRes] = useState<SearchResponse | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

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
      setRes(await searchTables(query, dom));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Search failed.");
      setRes(null);
    } finally {
      setLoading(false);
    }
  }

  function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    run(q, domain);
  }

  function pickDomain(d: string | null) {
    setDomain(d);
    if (res) run(q, d); // re-run with the new facet if we already have results
  }

  function askAgent(table: string) {
    router.push(`/agent?table=${encodeURIComponent(table)}`);
  }

  const cards = res?.results ?? [];

  return (
    <div className="grid min-h-screen grid-rows-[auto_minmax(0,1fr)]">
      <header className="border-b border-[color:var(--color-line)] px-8 pb-5 pt-7">
        <h1 className="text-xl font-semibold tracking-tight">Cari data BRI</h1>
        <p className="mt-1 text-sm text-[color:var(--color-muted)]">
          Ketik kebutuhan Anda dalam bahasa sehari-hari — pencarian memahami maksud, bukan sekadar
          kata kunci.
        </p>
        <form onSubmit={onSubmit} className="mt-4 flex gap-2">
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

      <div className="grid min-h-0 grid-cols-[200px_minmax(0,1fr)]">
        {/* Facet rail */}
        <aside className="overflow-y-auto border-r border-[color:var(--color-line)] px-4 py-6">
          <p className="mb-2 text-xs font-semibold uppercase tracking-wide text-[color:var(--color-muted)]">
            Domain
          </p>
          <div className="flex flex-col gap-0.5">
            <FacetItem label="Semua domain" active={domain === null} onClick={() => pickDomain(null)} />
            {domains.map((d) => (
              <FacetItem key={d} label={d} active={domain === d} onClick={() => pickDomain(d)} />
            ))}
          </div>
        </aside>

        {/* Results */}
        <section className="overflow-y-auto px-8 py-6">
          {domain && (
            <div className="mb-4 flex items-center gap-2">
              <span className="inline-flex items-center gap-2 rounded-full bg-[color:var(--color-panel-2)] px-3 py-1 text-xs font-medium">
                Domain: {domain}
                <button onClick={() => pickDomain(null)} aria-label="Clear filter" className="text-[color:var(--color-muted)] hover:text-[color:var(--color-danger)]">
                  ✕
                </button>
              </span>
            </div>
          )}

          {loading && <Skeletons />}

          {!loading && error && (
            <div className="rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-3 text-sm text-[color:var(--color-warn)]">
              {error}
            </div>
          )}

          {!loading && !error && !res && (
            <EmptyState
              title="Mulai dari sebuah kebutuhan data"
              hint="Atau telusuri berdasarkan domain di samping."
              domains={domains}
              onPick={(d) => {
                setDomain(d);
                setQ(d);
                run(d, d);
              }}
            />
          )}

          {!loading && !error && res && cards.length === 0 && (
            <div className="grid gap-4">
              <div className="rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-4 py-3 text-sm text-[color:var(--color-muted)]">
                {res.filter_caused_empty
                  ? "Tidak ada hasil untuk domain ini. Berikut tabel terkait terdekat:"
                  : "Tidak ada tabel yang cocok. Coba istilah lain atau bahasa Inggris."}
              </div>
              {res.closest_related?.map((c) => (
                <TableCard key={c.id} card={c} onAsk={askAgent} />
              ))}
            </div>
          )}

          {!loading && !error && cards.length > 0 && (
            <div className="grid gap-3">
              <p className="text-xs text-[color:var(--color-muted)]">
                {cards.length} tabel · diurutkan berdasarkan relevansi
              </p>
              {cards.map((c) => (
                <TableCard key={c.id} card={c} onAsk={askAgent} />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}

function FacetItem({ label, active, onClick }: { label: string; active: boolean; onClick: () => void }) {
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
