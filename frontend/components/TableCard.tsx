"use client";

import { useRef } from "react";
import { type SearchCard } from "@/lib/api";
import { ATTACH_DND_TYPE, MAX_ATTACHED } from "@/lib/attach";
import { DomainBadge, PiiBadge } from "@/components/Badges";

/**
 * A result card. Opening it (click / Enter / Space) invokes `onOpen(physical_name)`, which the
 * SearchPane uses to open the detail slide-over — detail is now an in-workspace panel, not a route.
 * "Kirim ke agent" attaches the table to the active session's agent context via `onAsk` (the
 * keyboard/accessible attach path).
 *
 * The card is also an HTML5 drag SOURCE (Unit 7): dragging it carries `card.table_name` in
 * `dataTransfer` under `ATTACH_DND_TYPE` for the AgentPane drop target. A drag must NOT trigger the
 * click-to-open, so we suppress the click that browsers may synthesize at drag end (`draggingRef`).
 * `onDragStartCard` lets the parent auto-dismiss the detail slide-over when a background card is
 * dragged (per the slide-over/drag decision — attach-from-detail stays a button, not a drag).
 */
export function TableCard({
  card,
  onOpen,
  onAsk,
  onDragStartCard,
  atCap = false,
}: {
  card: SearchCard;
  onOpen: (physicalName: string) => void;
  onAsk: (t: string) => void;
  onDragStartCard?: () => void;
  atCap?: boolean;
}) {
  // Set while a drag is in progress so the click synthesized at drag-end doesn't open the detail.
  const draggingRef = useRef(false);

  function open() {
    if (draggingRef.current) return;
    onOpen(card.physical_name);
  }

  return (
    <article
      role="link"
      tabIndex={0}
      draggable
      aria-label={`Lihat detail tabel ${card.headline}`}
      onClick={open}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          open();
        }
      }}
      onDragStart={(e) => {
        draggingRef.current = true;
        e.dataTransfer.setData(ATTACH_DND_TYPE, card.table_name);
        // A text/plain fallback keeps the drag image/behaviour sane across browsers.
        e.dataTransfer.setData("text/plain", card.table_name);
        e.dataTransfer.effectAllowed = "copy";
        onDragStartCard?.();
      }}
      onDragEnd={() => {
        // Clear on the next tick so the click that may fire immediately after drop is still gated.
        setTimeout(() => {
          draggingRef.current = false;
        }, 0);
      }}
      className="group cursor-pointer rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] p-4 shadow-[0_6px_20px_rgba(15,23,42,0.05)] transition-colors hover:border-[color:var(--color-accent)]/50 focus:outline-none focus-visible:ring-2 focus-visible:ring-[color:var(--color-accent)]/40 active:cursor-grabbing"
    >
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-2">
          {/* Grab affordance: signals the card can be dragged into the agent context. */}
          <span
            aria-hidden
            title="Seret ke agent untuk melampirkan"
            className="mt-0.5 shrink-0 cursor-grab select-none text-[color:var(--color-muted)]/60 group-hover:text-[color:var(--color-muted)]"
          >
            ⠿
          </span>
          <div className="min-w-0">
            <h3 className="truncate text-[15px] font-semibold text-[color:var(--color-ink)] group-hover:text-[color:var(--color-accent)]">
              {card.headline}
            </h3>
            <p className="mt-0.5 truncate font-mono text-xs text-[color:var(--color-muted)]">
              {card.physical_name}
            </p>
          </div>
        </div>
        <button
          type="button"
          aria-disabled={atCap}
          title={atCap ? `Maksimal ${MAX_ATTACHED} tabel` : undefined}
          onClick={(e) => {
            e.stopPropagation();
            onAsk(card.table_name);
          }}
          className={[
            "shrink-0 rounded-lg border px-3 py-1.5 text-xs font-semibold transition-colors",
            atCap
              ? "cursor-not-allowed border-[color:var(--color-line)] bg-[color:var(--color-panel-2)] text-[color:var(--color-muted)]"
              : "border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/5 text-[color:var(--color-accent)] hover:bg-[color:var(--color-accent)]/10",
          ].join(" ")}
        >
          Kirim ke agent ↗
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
