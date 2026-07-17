"use client";

import { useEffect, useState } from "react";
import { ApiError, tableDetail, type ColumnInfo, type TableDetail } from "@/lib/api";
import { attachResultLabel, type AttachResult } from "@/lib/attach";
import { DomainBadge, PiiBadge } from "@/components/Badges";
import { useAppState } from "@/components/AppState";

/**
 * Full-pane table detail view. Replaces the SearchPane in the left workspace column when a result
 * card is opened (see page.tsx), keeping the AgentPane on the right and the global top bar in place.
 * Structure mirrors the Sage detail design: a header (back · name · attach), a meta row, a
 * description, and a per-column list.
 */
export function TableDetailView({ id, onBack }: { id: string; onBack: () => void }) {
  const { attachTable } = useAppState();
  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "done" | "notfound" | "error">("loading");
  const [attachResult, setAttachResult] = useState<AttachResult | null>(null);

  useEffect(() => {
    let alive = true;
    setStatus("loading");
    setDetail(null);
    setAttachResult(null);
    tableDetail(id)
      .then((d) => {
        if (!alive) return;
        setDetail(d);
        setStatus("done");
      })
      .catch((e) => {
        if (!alive) return;
        setStatus(e instanceof ApiError && e.status === 404 ? "notfound" : "error");
      });
    return () => {
      alive = false;
    };
  }, [id]);

  // Back on Escape — parity with the old slide-over's close-on-Escape.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onBack();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onBack]);

  function attach() {
    if (!detail) return;
    setAttachResult(attachTable(detail.card.table_name));
  }

  const card = detail?.card;
  // Schema = the qualifier before the first dot in the physical name (e.g. "datalake.LOANMASTER_").
  const schema = card?.physical_name.includes(".")
    ? card.physical_name.split(".")[0]
    : "—";
  const attachLabel = attachResultLabel(attachResult);
  const attachAtCap = attachResult === "cap";

  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-center justify-between gap-4 border-b border-[color:var(--color-line)] px-6 py-4">
        <div className="flex min-w-0 items-center gap-3">
          <button
            type="button"
            onClick={onBack}
            aria-label="Kembali ke pencarian"
            className="grid h-9 w-9 shrink-0 place-items-center rounded-lg border border-[color:var(--color-line)] text-[color:var(--color-muted)] transition-colors hover:border-[color:var(--color-accent)] hover:text-[color:var(--color-accent)]"
          >
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.4" strokeLinecap="round" strokeLinejoin="round">
              <line x1="19" y1="12" x2="5" y2="12" />
              <polyline points="12 19 5 12 12 5" />
            </svg>
          </button>
          <div className="flex min-w-0 items-center gap-2">
            <h1 className="truncate text-lg font-semibold tracking-tight text-[color:var(--color-ink)]">
              {card ? card.headline : id}
            </h1>
            {card?.domain_tags.slice(0, 1).map((d) => (
              <DomainBadge key={d} label={d} size="md" />
            ))}
            {card && <PiiBadge status={card.pii} size="md" />}
          </div>
        </div>
        {status === "done" && card && (
          <button
            type="button"
            onClick={attach}
            aria-disabled={attachAtCap}
            className={[
              "inline-flex shrink-0 items-center gap-1.5 rounded-lg px-4 py-2 text-sm font-semibold transition-colors",
              attachAtCap
                ? "border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]"
                : "bg-[color:var(--color-accent)] text-white hover:brightness-95",
            ].join(" ")}
          >
            <span aria-hidden className="text-base leading-none">＋</span>
            {attachResult ? attachLabel : "Kirim ke agent"}
          </button>
        )}
      </header>

      <div className="min-h-0 flex-1 overflow-y-auto px-6 py-6">
        {status === "loading" && (
          <div className="grid gap-4">
            <div className="h-6 w-1/2 animate-pulse rounded-lg bg-[color:var(--color-panel-2)]" />
            <div className="h-24 animate-pulse rounded-xl bg-[color:var(--color-panel-2)]/60" />
            <div className="h-64 animate-pulse rounded-xl bg-[color:var(--color-panel-2)]/40" />
          </div>
        )}

        {status === "notfound" && (
          <div className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/40 px-5 py-8 text-center">
            <p className="text-base font-semibold">Tabel tidak ditemukan</p>
            <p className="mt-1 text-sm text-[color:var(--color-muted)]">
              <span className="font-mono">{id}</span> tidak ada di knowledge base (atau tidak dapat
              diakses).
            </p>
          </div>
        )}

        {status === "error" && (
          <div className="rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-3 text-sm text-[color:var(--color-warn)]">
            Gagal memuat detail tabel. Coba kembali dan buka lagi.
          </div>
        )}

        {status === "done" && card && (
          <div className="mx-auto max-w-3xl">
            {/* Meta row */}
            <div className="flex flex-wrap gap-x-12 gap-y-4">
              <Meta label="Schema" value={schema} mono />
              <Meta label="Kategori" value={card.domain_tags[0] ?? "—"} />
              <Meta
                label="Kolom"
                value={card.n_columns != null ? String(card.n_columns) : String(detail.columns.length)}
              />
            </div>

            {/* Description */}
            {card.description && (
              <div className="mt-7">
                <p className="mb-1.5 text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
                  Deskripsi
                </p>
                <p className="text-sm leading-relaxed text-[color:var(--color-ink)]">
                  {card.description}
                </p>
              </div>
            )}

            {/* Columns */}
            <div className="mt-8">
              <div className="mb-3 flex items-baseline justify-between">
                <p className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
                  Kolom
                </p>
                <span className="text-xs text-[color:var(--color-muted)]">
                  {detail.columns.length} kolom
                </span>
              </div>

              {detail.columns.length === 0 ? (
                <p className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/40 px-4 py-3 text-sm text-[color:var(--color-muted)]">
                  Tidak ada kamus kolom untuk tabel ini di knowledge base.
                </p>
              ) : (
                <div className="grid gap-2">
                  {detail.columns.map((c) => (
                    <ColumnRow key={c.field_name} col={c} />
                  ))}
                </div>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Meta({ label, value, mono }: { label: string; value: string; mono?: boolean }) {
  return (
    <div>
      <p className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
        {label}
      </p>
      <p
        className={[
          "mt-1 text-sm font-semibold text-[color:var(--color-ink)]",
          mono ? "font-mono" : "",
        ].join(" ")}
      >
        {value}
      </p>
    </div>
  );
}

/** One column: a type glyph, the field name (+ PII flag), its description, and the data-type badge. */
function ColumnRow({ col }: { col: ColumnInfo }) {
  const desc = col.business_title || col.description;
  return (
    <div className="rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-4 py-3">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-2.5">
          <span
            aria-hidden
            className="mt-0.5 grid h-5 w-5 shrink-0 place-items-center rounded-md bg-[color:var(--color-panel-2)] font-mono text-[11px] font-bold text-[color:var(--color-muted)]"
          >
            T
          </span>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <span className="font-mono text-sm font-semibold text-[color:var(--color-ink)]">
                {col.field_name}
              </span>
              {col.pii && <PiiBadge status="present" />}
            </div>
            {desc && (
              <p className="mt-0.5 text-sm leading-snug text-[color:var(--color-muted)]">{desc}</p>
            )}
          </div>
        </div>
        <span className="shrink-0 whitespace-nowrap rounded-md bg-[color:var(--color-panel-2)] px-2 py-1 font-mono text-[11px] text-[color:var(--color-muted)]">
          {col.data_type}
        </span>
      </div>
    </div>
  );
}
