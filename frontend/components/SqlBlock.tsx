"use client";

import { useEffect, useState } from "react";

export function SqlBlock({ sql }: { sql: string }) {
  const [copied, setCopied] = useState(false);
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(sql);

  useEffect(() => setValue(sql), [sql]);

  async function copy() {
    await navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1200);
  }

  return (
    <div className="overflow-hidden rounded-xl border border-[color:var(--color-warn-line)]">
      <div className="flex items-center justify-between gap-3 bg-[color:var(--color-warn-bg)] px-3 py-2">
        <span className="inline-flex items-center gap-2 text-[11px] font-bold uppercase tracking-wide text-[color:var(--color-warn)]">
          <span aria-hidden>⚠</span> Unverified draft — not executed
        </span>
        <div className="flex items-center gap-1.5">
          <button
            type="button"
            onClick={() => setEditing((e) => !e)}
            className="rounded-md border border-[color:var(--color-warn-line)] px-2.5 py-1 text-xs font-semibold text-[color:var(--color-warn)] transition-colors hover:bg-[color:var(--color-warn-line)]/20"
          >
            {editing ? "Done" : "Edit"}
          </button>
          <button
            type="button"
            onClick={copy}
            className="rounded-md bg-[color:var(--color-ink)] px-2.5 py-1 text-xs font-semibold text-white transition-colors hover:brightness-110"
          >
            {copied ? "Copied" : "Copy"}
          </button>
        </div>
      </div>
      {editing ? (
        <textarea
          value={value}
          onChange={(e) => setValue(e.target.value)}
          spellCheck={false}
          className="block h-56 w-full resize-y bg-[color:var(--color-code)] p-4 font-mono text-[13px] leading-relaxed text-[#e8f1fb] outline-none"
        />
      ) : (
        <pre className="overflow-x-auto bg-[color:var(--color-code)] p-4">
          <code className="font-mono text-[13px] leading-relaxed text-[#e8f1fb]">{value}</code>
        </pre>
      )}
      <p className="bg-[color:var(--color-code)] px-4 pb-3 text-[11px] text-[#7d93ad]">
        Copy into your SQL editor to run. This assistant drafts SQL only — it cannot access, run,
        or return data.
      </p>
    </div>
  );
}
