"use client";

// Shared domain + PII badges, used by TableCard (compact "sm") and TableDetailPanel (roomier "md").
// The only difference between the two call sites was padding / text-size, so a `size` variant keeps
// the copy and semantics in ONE place. The PII copy is load-bearing (compliance): the "not
// classified" wording must stay verbatim — absence of a PII flag does not mean the table is safe.

type PiiStatus = "present" | "unclassified" | (string & {});
type Size = "sm" | "md";

const DOMAIN_SIZE: Record<Size, string> = {
  sm: "px-2 py-0.5 text-[11px]",
  md: "px-2.5 py-0.5 text-xs",
};

const PII_SIZE: Record<Size, string> = {
  sm: "px-2 py-0.5 text-[11px]",
  md: "px-2.5 py-0.5 text-xs",
};

export function DomainBadge({ label, size = "sm" }: { label: string; size?: Size }) {
  return (
    <span
      className={`rounded-full bg-[color:var(--color-panel-2)] font-medium text-[color:var(--color-muted)] ${DOMAIN_SIZE[size]}`}
    >
      {label}
    </span>
  );
}

export function PiiBadge({ status, size = "sm" }: { status: PiiStatus; size?: Size }) {
  if (status === "present") {
    return (
      <span
        className={`inline-flex items-center gap-1 rounded-full border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] font-semibold text-[color:var(--color-warn)] ${PII_SIZE[size]}`}
      >
        <span aria-hidden>⚠</span> PII
      </span>
    );
  }
  return (
    <span
      className={`rounded-full border border-dashed border-[color:var(--color-line)] text-[color:var(--color-muted)] ${PII_SIZE[size]}`}
      title="Sensitivity not classified — absence of a PII flag does not mean the table is safe."
    >
      sensitivity not classified
    </span>
  );
}
