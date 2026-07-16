"use client";

import { useEffect, useState } from "react";
import { ApiError, tableDetail, type TableDetail } from "@/lib/api";
import type { AttachResult } from "@/lib/attach";

function DomainBadge({ label }: { label: string }) {
  return (
    <span className="rounded-full bg-[color:var(--color-panel-2)] px-2.5 py-0.5 text-xs font-medium text-[color:var(--color-muted)]">
      {label}
    </span>
  );
}

function PiiBadge({ status }: { status: TableDetail["card"]["pii"] }) {
  if (status === "present") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-2.5 py-0.5 text-xs font-semibold text-[color:var(--color-warn)]">
        <span aria-hidden>⚠</span> PII
      </span>
    );
  }
  return (
    <span
      className="rounded-full border border-dashed border-[color:var(--color-line)] px-2.5 py-0.5 text-xs text-[color:var(--color-muted)]"
      title="Sensitivity not classified — absence of a PII flag does not mean the table is safe."
    >
      sensitivity not classified
    </span>
  );
}

/**
 * Right-side slide-over that overlays the RESULTS region only (its parent must be `relative`), so
 * the agent panel stays reachable. Keyed by physical name via the `id` prop. `onAttach` sends the
 * viewed table to the active session's agent context (no drag — see the plan's slide-over decision).
 */
export function TableDetailPanel({
  id,
  onClose,
  onAttach,
}: {
  id: string;
  onClose: () => void;
  onAttach: (table: string) => AttachResult;
}) {
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

  // Close on Escape.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  function attach() {
    if (!detail) return;
    setAttachResult(onAttach(detail.card.table_name));
  }

  // The aria-live announcement is owned centrally by AgentPane (both paths share it), so the
  // button here only carries a local visual confirmation.
  const attachLabel =
    attachResult === "added"
      ? "Ditambahkan ke agent ✓"
      : attachResult === "duplicate"
        ? "Sudah terlampir ✓"
        : attachResult === "cap"
          ? "Maksimal 10 tabel"
          : "Kirim ke agent ↗";
  const attachAtCap = attachResult === "cap";

  return (
    // Overlays the results region only. The scrim is scoped to this region (absolute inset-0).
    <div className="absolute inset-0 z-20 flex justify-end">
      <button
        type="button"
        aria-label="Tutup detail"
        onClick={onClose}
        className="absolute inset-0 cursor-default bg-[color:var(--color-ink)]/20"
      />
      <div
        role="dialog"
        aria-label="Detail tabel"
        className="relative flex w-[420px] max-w-[92%] flex-col overflow-hidden border-l border-[color:var(--color-line)] bg-[color:var(--color-panel)] shadow-2xl"
      >
        <div className="flex items-center justify-between gap-3 border-b border-[color:var(--color-line)] px-5 py-3">
          <span className="text-[11px] font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
            Detail tabel
          </span>
          <button
            type="button"
            onClick={onClose}
            aria-label="Tutup"
            className="rounded-md px-2 py-1 text-sm text-[color:var(--color-muted)] transition-colors hover:bg-[color:var(--color-panel-2)] hover:text-[color:var(--color-ink)]"
          >
            ✕
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto px-5 py-5">
          {status === "loading" && (
            <div className="grid gap-3">
              <div className="h-8 w-2/3 animate-pulse rounded-lg bg-[color:var(--color-panel-2)]" />
              <div className="h-40 animate-pulse rounded-xl bg-[color:var(--color-panel-2)]/60" />
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
              Gagal memuat detail tabel. Coba tutup dan buka kembali.
            </div>
          )}

          {status === "done" && detail && (
            <>
              <header>
                <h2 className="text-lg font-semibold tracking-tight text-[color:var(--color-ink)]">
                  {detail.card.headline}
                </h2>
                <p className="mt-1 font-mono text-xs text-[color:var(--color-muted)]">
                  {detail.card.physical_name}
                </p>

                <div className="mt-3 flex flex-wrap items-center gap-1.5">
                  {detail.card.domain_tags.map((d) => (
                    <DomainBadge key={d} label={d} />
                  ))}
                  <PiiBadge status={detail.card.pii} />
                </div>

                {detail.card.description && (
                  <p className="mt-4 text-sm leading-relaxed text-[color:var(--color-ink)]">
                    {detail.card.description}
                  </p>
                )}

                <button
                  type="button"
                  onClick={attach}
                  aria-disabled={attachAtCap}
                  className={[
                    "mt-4 w-full rounded-lg border px-4 py-2 text-sm font-semibold transition-colors",
                    attachAtCap
                      ? "border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]"
                      : "border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/5 text-[color:var(--color-accent)] hover:bg-[color:var(--color-accent)]/10",
                  ].join(" ")}
                >
                  {attachLabel}
                </button>
              </header>

              <section className="mt-6">
                <div className="mb-2 flex items-baseline justify-between">
                  <h3 className="text-xs font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
                    Kolom
                  </h3>
                  <span className="text-xs text-[color:var(--color-muted)]">
                    {detail.columns.length} kolom
                  </span>
                </div>

                {detail.columns.length === 0 ? (
                  <p className="rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/40 px-4 py-3 text-sm text-[color:var(--color-muted)]">
                    Tidak ada kamus kolom untuk tabel ini di knowledge base.
                  </p>
                ) : (
                  <div className="overflow-hidden rounded-xl border border-[color:var(--color-line)]">
                    <table className="w-full border-collapse text-sm">
                      <thead>
                        <tr className="bg-[color:var(--color-panel-2)]/70 text-left text-xs uppercase tracking-wide text-[color:var(--color-muted)]">
                          <th className="px-3 py-2 font-semibold">Kolom</th>
                          <th className="px-3 py-2 font-semibold">Tipe</th>
                          <th className="px-3 py-2 font-semibold">Keterangan</th>
                        </tr>
                      </thead>
                      <tbody>
                        {detail.columns.map((c) => (
                          <tr
                            key={c.field_name}
                            className="border-t border-[color:var(--color-line)] align-top"
                          >
                            <td className="px-3 py-2 font-mono text-[color:var(--color-ink)]">
                              {c.field_name}
                              {c.pii && (
                                <span className="ml-1.5 text-[color:var(--color-warn)]" title="PII">
                                  ⚠
                                </span>
                              )}
                            </td>
                            <td className="whitespace-nowrap px-3 py-2 text-[color:var(--color-muted)]">
                              {c.data_type}
                            </td>
                            <td className="px-3 py-2 text-[color:var(--color-muted)]">
                              {c.business_title || c.description || "—"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </div>
    </div>
  );
}
