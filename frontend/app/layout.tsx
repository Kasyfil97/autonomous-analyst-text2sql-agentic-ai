import type { Metadata } from "next";
import { Instrument_Sans, JetBrains_Mono } from "next/font/google";
import "./globals.css";
import { SessionsRail } from "@/components/SessionsRail";
import { AppStateProvider } from "@/components/AppState";

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
          {/* Unified workspace: a fixed rail + two independently scrolling flex regions.
              `children` (page.tsx) renders the two panes; `contents` lets them become grid cells
              directly so each region scrolls on its own within the viewport height. */}
          <div className="app-shell grid h-screen grid-cols-[240px_minmax(360px,1fr)_minmax(320px,1fr)] overflow-hidden">
            <SessionsRail />
            <div className="contents">{children}</div>
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
