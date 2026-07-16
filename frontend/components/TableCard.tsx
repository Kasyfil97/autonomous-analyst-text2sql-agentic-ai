"use client";

import { useRouter } from "next/navigation";
import { type SearchCard } from "@/lib/api";

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

/** Path to a table's detail page, keyed by its schema-qualified physical name. */
export function tableHref(card: SearchCard): string {
  return `/table/${encodeURIComponent(card.physical_name)}`;
}

export function TableCard({ card, onAsk }: { card: SearchCard; onAsk: (t: string) => void }) {
  const router = useRouter();
  const href = tableHref(card);

  function open() {
    router.push(href);
  }

  return (
    <article
      role="link"
      tabIndex={0}
      aria-label={`Lihat detail tabel ${card.headline}`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      className="group cursor-pointer rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] p-4 shadow-[0_6px_20px_rgba(15,23,42,0.05)] transition-colors hover:border-[color:var(--color-accent)]/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]/40"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="truncate text-[15px] font-semibold text-[color:var(--color-ink)] group-hover:text-[color:var(--color-accent)]">
            {card.headline}
          </h3>
          <p className="mt-0.5 truncate font-mono text-xs text-[color:var(--color-muted)]">
            {card.physical_name}
          </p>
        </div>
        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation();
            onAsk(card.table_name);
          }}
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

      <div className="mt-3 flex items-center justify-between text-xs">
        <span className="text-[color:var(--color-muted)]">
          {card.n_columns != null ? `${card.n_columns} kolom` : "Kolom"}
        </span>
        <span className="font-semibold text-[color:var(--color-accent-2)] group-hover:underline">
          Lihat detail →
        </span>
      </div>
    </article>
  );
}
