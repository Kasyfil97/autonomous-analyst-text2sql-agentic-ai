"use client";

import { useState } from "react";
import { tableColumns, type ColumnInfo, type SearchCard } from "@/lib/api";

function DomainBadge({ label }: { label: string }) {
  return (
    <span className="rounded-full bg-[color:var(--color-panel-2)] px-2 py-0.5 text-[11px] font-medium text-[color:var(--color-muted)]">
      {label}
    </span>
  );
}

function PiiBadge({ status }: { status: SearchCard["pii"] }) {
  if (status === "present") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-2 py-0.5 text-[11px] font-semibold text-[color:var(--color-warn)]">
        <span aria-hidden>⚠</span> PII
      </span>
    );
  }
  return (
    <span
      className="rounded-full border border-dashed border-[color:var(--color-line)] px-2 py-0.5 text-[11px] text-[color:var(--color-muted)]"
      title="Sensitivity not classified — absence of a PII flag does not mean the table is safe."
    >
      sensitivity not classified
    </span>
  );
}

export function TableCard({ card, onAsk }: { card: SearchCard; onAsk: (t: string) => void }) {
  const [open, setOpen] = useState(false);
  const [cols, setCols] = useState<ColumnInfo[] | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function toggle() {
    const next = !open;
    setOpen(next);
    if (next && cols === null && !loading) {
      setLoading(true);
      setError(null);
      try {
        const res = await tableColumns(card.table_name);
        setCols(res.columns);
      } catch {
        setError("Couldn't load columns.");
      } finally {
        setLoading(false);
      }
    }
  }

  return (
    <article className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] p-4 shadow-[0_6px_20px_rgba(15,23,42,0.05)] transition-colors hover:border-[color:var(--color-accent)]/40">
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold text-[color:var(--color-ink)]">
            {card.headline}
          </h3>
          <p className="mt-0.5 truncate font-mono text-xs text-[color:var(--color-muted)]">
            {card.physical_name}
          </p>
        </div>
        <button
          type="button"
          onClick={() => onAsk(card.table_name)}
          className="shrink-0 rounded-lg border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/5 px-3 py-1.5 text-xs font-semibold text-[color:var(--color-accent)] transition-colors hover:bg-[color:var(--color-accent)]/10"
        >
          Tanya agent ↗
        </button>
      </div>

      <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
        {card.domain_tags.slice(0, 4).map((d) => (
          <DomainBadge key={d} label={d} />
        ))}
        <PiiBadge status={card.pii} />
      </div>

      {card.description && (
        <p className="mt-2.5 line-clamp-2 text-sm leading-relaxed text-[color:var(--color-muted)]">
          {card.description}
        </p>
      )}

      <button
        type="button"
        onClick={toggle}
        aria-expanded={open}
        className="mt-3 text-xs font-semibold text-[color:var(--color-accent-2)] hover:underline"
      >
        {open ? "▾" : "▸"} Kolom{card.n_columns != null ? ` (${card.n_columns})` : ""}
      </button>

      {open && (
        <div className="mt-2 overflow-hidden rounded-lg border border-[color:var(--color-line)]">
          {loading && <p className="px-3 py-2 text-xs text-[color:var(--color-muted)]">Loading columns…</p>}
          {error && <p className="px-3 py-2 text-xs text-[color:var(--color-danger)]">{error}</p>}
          {cols && cols.length > 0 && (
            <table className="w-full border-collapse text-xs">
              <tbody>
                {cols.slice(0, 60).map((c) => (
                  <tr key={c.field_name} className="border-b border-[color:var(--color-line)] last:border-0">
                    <td className="whitespace-nowrap px-3 py-1.5 font-mono text-[color:var(--color-ink)]">
                      {c.field_name}
                      {c.pii && <span className="ml-1 text-[color:var(--color-warn)]" title="PII">⚠</span>}
                    </td>
                    <td className="px-3 py-1.5 text-[color:var(--color-muted)]">{c.data_type}</td>
                    <td className="px-3 py-1.5 text-[color:var(--color-muted)]">
                      {c.business_title || c.description}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {cols && cols.length === 0 && (
            <p className="px-3 py-2 text-xs text-[color:var(--color-muted)]">
              No column dictionary in the knowledge base for this table.
            </p>
          )}
        </div>
      )}
    </article>
  );
}
