"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV = [
  { href: "/search", label: "Pencarian", sub: "Search", icon: "⌕" },
  { href: "/agent", label: "AI Data Agent", sub: "Draft SQL", icon: "◆" },
];

export function Sidebar() {
  const pathname = usePathname();
  return (
    <aside className="flex flex-col gap-8 border-r border-[color:var(--color-line)] bg-white/70 px-5 py-7 backdrop-blur">
      <div className="flex items-center gap-3">
        <div className="grid h-10 w-10 place-items-center rounded-xl bg-[color:var(--color-ink)] font-bold tracking-tight text-white">
          B
        </div>
        <div className="leading-tight">
          <p className="text-[15px] font-semibold">BRISA</p>
          <p className="text-xs text-[color:var(--color-muted)]">BRI Search &amp; Agent</p>
        </div>
      </div>

      <nav className="flex flex-col gap-1.5">
        {NAV.map((item) => {
          const active = pathname.startsWith(item.href);
          return (
            <Link
              key={item.href}
              href={item.href}
              className={[
                "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm transition-colors",
                active
                  ? "bg-[color:var(--color-panel-2)] font-semibold text-[color:var(--color-ink)]"
                  : "text-[color:var(--color-muted)] hover:bg-[color:var(--color-panel-2)]/60 hover:text-[color:var(--color-ink)]",
              ].join(" ")}
            >
              <span
                aria-hidden
                className={active ? "text-[color:var(--color-accent)]" : "opacity-70"}
              >
                {item.icon}
              </span>
              <span className="flex flex-col">
                <span>{item.label}</span>
                <span className="text-[11px] font-normal text-[color:var(--color-muted)]">
                  {item.sub}
                </span>
              </span>
            </Link>
          );
        })}
      </nav>

      <div className="mt-auto rounded-lg border border-[color:var(--color-warn-line)] bg-[color:var(--color-warn-bg)] px-3 py-2.5 text-xs leading-relaxed text-[color:var(--color-warn)]">
        Draft-only. SQL is never executed — review tables, joins, and filters before running.
      </div>
    </aside>
  );
}
