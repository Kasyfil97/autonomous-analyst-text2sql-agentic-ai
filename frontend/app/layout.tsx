import type { Metadata } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { AppStateProvider } from "@/components/AppState";

// The Sage brand mark, rendered in the top bar. Formerly lived in the (now-removed) left rail.
function SageMark() {
  return (
    <div className="flex items-center gap-3">
      <div
        aria-hidden
        className="grid h-9 w-9 place-items-center rounded-xl bg-[color:var(--color-accent)] text-white shadow-sm"
      >
        {/* search glyph — the Sage mark */}
        <svg
          width="18"
          height="18"
          viewBox="0 0 24 24"
          fill="none"
          stroke="currentColor"
          strokeWidth="2.4"
          strokeLinecap="round"
        >
          <circle cx="10.5" cy="10.5" r="6.5" />
          <line x1="20" y1="20" x2="16" y2="16" />
        </svg>
      </div>
      <div className="flex items-baseline gap-2 leading-tight">
        <p className="text-[15px] font-semibold tracking-tight text-[color:var(--color-ink)]">Sage</p>
        <p className="text-[11px] text-[color:var(--color-muted)]">BigData search &amp; generation</p>
      </div>
    </div>
  );
}

const sans = Instrument_Sans({
  variable: "--font-instrument-sans",
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
});

const mono = JetBrains_Mono({
  variable: "--font-jetbrains-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "Sage — BigData search & generation",
  description: "Semantic data search and draft-SQL agent over the BRI knowledge base.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="id" className={`${sans.variable} ${mono.variable} h-full`}>
      <body className="min-h-full">
        <AppStateProvider>
          {/* Unified workspace: a full-width top bar over two independently scrolling regions.
              `children` (page.tsx) renders the two panes directly as grid cells so each region
              scrolls on its own within the remaining viewport height. */}
          <div className="app-shell flex h-screen flex-col overflow-hidden">
            <header className="flex items-center border-b border-[color:var(--color-line)] bg-white/60 px-6 py-3 backdrop-blur">
              <SageMark />
            </header>
            <div className="min-h-0 flex-1 overflow-hidden">{children}</div>
          </div>
        </AppStateProvider>
        <div className="below-min">
          <div>
            <p className="font-semibold text-[color:var(--color-ink)]">Sage</p>
            <p className="mt-2 max-w-xs text-sm">
              This internal console is designed for desktop. Please open it on a wider screen
              (≥ 900px).
            </p>
          </div>
        </div>
      </body>
    </html>
  );
}
