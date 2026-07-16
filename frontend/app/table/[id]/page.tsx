"use client";

import { useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import Link from "next/link";
import { ApiError, tableDetail, type TableDetail } from "@/lib/api";

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

export default function TableDetailPage() {
  const params = useParams<{ id: string }>();
  const router = useRouter();
  const id = decodeURIComponent(params.id);

  const [detail, setDetail] = useState<TableDetail | null>(null);
  const [status, setStatus] = useState<"loading" | "done" | "notfound" | "error">("loading");

  useEffect(() => {
    let alive = true;
    setStatus("loading");
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

  return (
    <div className="mx-auto flex min-h-screen max-w-4xl flex-col px-8 py-7">
      <Link
        href="/search"
        className="w-fit text-sm font-medium text-[color:var(--color-accent-2)] hover:underline"
      >
        ← Kembali ke pencarian
      </Link>

      {status === "loading" && (
        <div className="mt-6 grid gap-3">
          <div className="h-8 w-2/3 animate-pulse rounded-lg bg-[color:var(--color-panel-2)]" />
          <div className="h-40 animate-pulse rounded-xl bg-[color:var(--color-panel-2)]/60" />
        </div>
      )}

      {status === "notfound" && (
        <div className="mt-6 rounded-xl border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-6 py-10 text-center">
          <p className="text-base font-semibold">Tabel tidak ditemukan</p>
          <p className="mt-1 text-sm text-[color:var(--color-muted)]">
            <span className="font-mono">{id}</span> tidak ada di knowledge base (atau tidak dapat diakses).
          </p>
        </div>
      )}

      {status === "error" && (
        <div className="mt-6 rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-3 text-sm text-[color:var(--color-warn)]">
          Gagal memuat detail tabel. Coba muat ulang halaman.
        </div>
      )}

      {status === "done" && detail && (
        <>
          <header className="mt-5">
            <div className="flex items-start justify-between gap-4">
              <div className="min-w-0">
                <h1 className="text-2xl font-semibold tracking-tight text-[color:var(--color-ink)]">
                  {detail.card.headline}
                </h1>
                <p className="mt-1 font-mono text-sm text-[color:var(--color-muted)]">
                  {detail.card.physical_name}
                </p>
              </div>
              <button
                type="button"
                onClick={() =>
                  router.push(`/agent?table=${encodeURIComponent(detail.card.table_name)}`)
                }
                className="shrink-0 rounded-lg border border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/5 px-4 py-2 text-sm font-semibold text-[color:var(--color-accent)] transition-colors hover:bg-[color:var(--color-accent)]/10"
              >
                Tanya agent ↗
              </button>
            </div>

            <div className="mt-3 flex flex-wrap items-center gap-1.5">
              {detail.card.domain_tags.map((d) => (
                <DomainBadge key={d} label={d} />
              ))}
              <PiiBadge status={detail.card.pii} />
            </div>

            {detail.card.description && (
              <p className="mt-4 max-w-3xl text-sm leading-relaxed text-[color:var(--color-ink)]">
                {detail.card.description}
              </p>
            )}
          </header>

          <section className="mt-7">
            <div className="mb-2 flex items-baseline justify-between">
              <h2 className="text-sm font-bold uppercase tracking-widest text-[color:var(--color-muted)]">
                Kolom
              </h2>
              <span className="text-xs text-[color:var(--color-muted)]">
                {detail.columns.length} kolom
              </span>
            </div>

            {detail.columns.length === 0 ? (
              <p className="rounded-lg border border-[color:var(--color-line)] bg-[color:var(--color-panel)] px-4 py-3 text-sm text-[color:var(--color-muted)]">
                Tidak ada kamus kolom untuk tabel ini di knowledge base.
              </p>
            ) : (
              <div className="overflow-hidden rounded-xl border border-[color:var(--color-line)]">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="bg-[color:var(--color-panel-2)]/70 text-left text-xs uppercase tracking-wide text-[color:var(--color-muted)]">
                      <th className="px-4 py-2.5 font-semibold">Kolom</th>
                      <th className="px-4 py-2.5 font-semibold">Tipe</th>
                      <th className="px-4 py-2.5 font-semibold">Keterangan</th>
                    </tr>
                  </thead>
                  <tbody>
                    {detail.columns.map((c) => (
                      <tr
                        key={c.field_name}
                        className="border-t border-[color:var(--color-line)] align-top"
                      >
                        <td className="whitespace-nowrap px-4 py-2.5 font-mono text-[color:var(--color-ink)]">
                          {c.field_name}
                          {c.pii && (
                            <span className="ml-1.5 text-[color:var(--color-warn)]" title="PII">
                              ⚠
                            </span>
                          )}
                        </td>
                        <td className="whitespace-nowrap px-4 py-2.5 text-[color:var(--color-muted)]">
                          {c.data_type}
                        </td>
                        <td className="px-4 py-2.5 text-[color:var(--color-muted)]">
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
  );
}
