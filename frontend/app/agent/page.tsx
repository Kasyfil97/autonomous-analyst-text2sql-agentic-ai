"use client";

import { Suspense, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import {
  agentChat,
  type AgentResponse,
  type GroundingEntry,
  type GroundingStrength,
} from "@/lib/api";
import { SqlBlock } from "@/components/SqlBlock";

const STRENGTH: Record<Exclude<GroundingStrength, null>, { label: string; tone: string }> = {
  precedent_strong: {
    label: "Grounded in precedent (strong)",
    tone: "border-[color:var(--color-accent)]/30 bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]",
  },
  schema_only: {
    label: "Schema-only — verify carefully",
    tone: "border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]",
  },
};

function StrengthBadge({ strength }: { strength: GroundingStrength }) {
  if (!strength) return null;
  const s = STRENGTH[strength];
  return (
    <span className={`inline-flex rounded-full border px-2.5 py-0.5 text-[11px] font-semibold ${s.tone}`}>
      {s.label}
    </span>
  );
}

function GroundingChips({ grounding }: { grounding: GroundingEntry[] }) {
  // R13: no chip for a table absent from the schema KB.
  const shown = grounding.filter((g) => g.in_kb);
  if (shown.length === 0) return null;
  return (
    <div className="flex flex-wrap gap-1.5">
      {shown.map((g) => (
        <span
          key={g.name}
          title={g.retrieved ? "Grounded — retrieved this session" : "Named but not retrieved"}
          className={[
            "inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 font-mono text-[11px]",
            g.retrieved
              ? "bg-[color:var(--color-accent)]/10 text-[color:var(--color-accent)]"
              : "border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]",
          ].join(" ")}
        >
          {g.retrieved ? "✓" : "⚠"} {g.name}
        </span>
      ))}
    </div>
  );
}

function Warning({ text }: { text: string }) {
  const critical = text.includes("[CRITICAL]") || text.includes("[HIGH]");
  return (
    <div
      className={[
        "rounded-lg border px-3 py-2 text-xs leading-relaxed",
        critical
          ? "border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] text-[color:var(--color-warn)]"
          : "border-[color:var(--color-line)] bg-[color:var(--color-panel-2)] text-[color:var(--color-muted)]",
      ].join(" ")}
    >
      {text}
    </div>
  );
}

function Region({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <section className="grid gap-2">
      <p className="text-[11px] font-bold uppercase tracking-wide text-[color:var(--color-muted)]">
        {label}
      </p>
      {children}
    </section>
  );
}

function AgentInner() {
  const params = useSearchParams();
  const [attached, setAttached] = useState<string[]>([]);
  const [question, setQuestion] = useState("");
  const [status, setStatus] = useState<"idle" | "loading" | "done" | "error">("idle");
  const [result, setResult] = useState<AgentResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  // Pre-attach a table arriving from the Search cross-link (R17).
  useEffect(() => {
    const t = params.get("table");
    if (t) setAttached((a) => (a.includes(t) ? a : [...a, t]));
  }, [params]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!question.trim()) return;
    setStatus("loading");
    setErrorMsg(null);
    try {
      const res = await agentChat(question, attached);
      setResult(res);
      setStatus("done");
    } catch (err) {
      setErrorMsg(err instanceof Error ? err.message : "The agent is unavailable.");
      setStatus("error");
    }
  }

  return (
    <div className="mx-auto flex min-h-screen max-w-3xl flex-col px-8 py-7">
      <header>
        <h1 className="text-xl font-semibold tracking-tight">AI Data Agent</h1>
        <p className="mt-1 text-sm text-[color:var(--color-muted)]">
          Ajukan pertanyaan analitik — agent menyusun <strong>draft SQL</strong> (tidak dieksekusi)
          beserta tabel sumber, asumsi, dan peringatan.
        </p>
      </header>

      {/* Composer */}
      <form onSubmit={submit} className="mt-5">
        {attached.length > 0 && (
          <div className="mb-2 flex flex-wrap gap-1.5">
            {attached.map((t) => (
              <span
                key={t}
                className="inline-flex items-center gap-1.5 rounded-full bg-[color:var(--color-panel-2)] px-2.5 py-1 font-mono text-xs"
              >
                {t}
                <button
                  type="button"
                  aria-label={`Remove ${t}`}
                  onClick={() => setAttached((a) => a.filter((x) => x !== t))}
                  className="text-[color:var(--color-muted)] hover:text-[color:var(--color-danger)]"
                >
                  ✕
                </button>
              </span>
            ))}
          </div>
        )}
        <div className="flex gap-2">
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder="mis. Berapa jumlah transaksi kartu kredit per bulan selama 2025?"
            rows={2}
            className="w-full resize-y rounded-lg border border-[color:var(--color-line)] bg-white px-4 py-2.5 text-sm outline-none focus:border-[color:var(--color-accent)] focus:ring-2 focus:ring-[color:var(--color-accent)]/20"
          />
          <button
            type="submit"
            disabled={status === "loading"}
            className="shrink-0 self-end rounded-lg bg-[color:var(--color-accent)] px-5 py-2.5 text-sm font-semibold text-white transition-colors hover:brightness-95 disabled:opacity-60"
          >
            {status === "loading" ? "Menyusun…" : "Kirim"}
          </button>
        </div>
      </form>

      {/* States */}
      <div className="mt-6 min-h-0 flex-1">
        {status === "idle" && (
          <div className="rounded-xl border border-dashed border-[color:var(--color-line)] px-6 py-10 text-center text-sm text-[color:var(--color-muted)]">
            Belum ada draft. Ajukan pertanyaan di atas — atau mulai dari{" "}
            <Link href="/search" className="font-semibold text-[color:var(--color-accent-2)] hover:underline">
              Pencarian
            </Link>{" "}
            untuk melampirkan tabel.
          </div>
        )}

        {status === "loading" && (
          <div className="flex items-center gap-3 text-sm text-[color:var(--color-muted)]">
            <span className="h-2 w-2 animate-ping rounded-full bg-[color:var(--color-accent)]" />
            Mencari skema &amp; precedent, lalu menyusun draft SQL…
          </div>
        )}

        {status === "error" && (
          <div className="grid gap-3 rounded-xl border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-4">
            <p className="text-sm font-semibold text-[color:var(--color-warn)]">
              Agent tidak tersedia
            </p>
            <p className="text-xs text-[color:var(--color-warn)]">{errorMsg}</p>
            <button
              onClick={() => submit(new Event("submit") as unknown as React.FormEvent)}
              className="w-fit rounded-lg border border-[color:var(--color-warn-line)] px-3 py-1.5 text-xs font-semibold text-[color:var(--color-warn)]"
            >
              Coba lagi
            </button>
          </div>
        )}

        {status === "done" && result && (
          <div className="grid gap-5">
            {result.grounding_strength && (
              <div>
                <StrengthBadge strength={result.grounding_strength} />
              </div>
            )}

            {result.declined ? (
              <div className="rounded-xl border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-4 py-4 text-sm text-[color:var(--color-warn)]">
                <p className="font-semibold">Agent menahan draft</p>
                <p className="mt-1 text-xs">{result.missing}</p>
              </div>
            ) : (
              <>
                {result.explanation && (
                  <Region label="Interpretasi">
                    <p className="whitespace-pre-wrap text-sm leading-relaxed">{result.explanation}</p>
                  </Region>
                )}

                {result.assumptions.length > 0 && (
                  <Region label="Asumsi (periksa &amp; sesuaikan)">
                    <ul className="grid gap-1.5">
                      {result.assumptions.map((a, i) => (
                        <li
                          key={i}
                          className="rounded-lg border border-dashed border-[color:var(--color-line)] bg-[color:var(--color-panel-2)]/50 px-3 py-1.5 text-sm"
                        >
                          {a}
                        </li>
                      ))}
                    </ul>
                  </Region>
                )}

                {result.grounding.some((g) => g.in_kb) && (
                  <Region label="Tabel sumber">
                    <GroundingChips grounding={result.grounding} />
                  </Region>
                )}

                {result.warnings.length > 0 && (
                  <Region label="Peringatan">
                    <div className="grid gap-1.5">
                      {result.warnings.map((w, i) => (
                        <Warning key={i} text={w} />
                      ))}
                    </div>
                  </Region>
                )}

                {result.sql && (
                  <Region label="Draft SQL">
                    <SqlBlock sql={result.sql} />
                  </Region>
                )}
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export default function AgentPage() {
  return (
    <Suspense fallback={<div className="p-8 text-sm text-[color:var(--color-muted)]">Loading…</div>}>
      <AgentInner />
    </Suspense>
  );
}
