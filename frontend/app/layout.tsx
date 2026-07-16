import type { Metadata } from "next";
import { IBM_Plex_Sans, IBM_Plex_Mono } from "next/font/google";
import "./globals.css";
import { Sidebar } from "@/components/Sidebar";

const plexSans = IBM_Plex_Sans({
  variable: "--font-plex-sans",
  weight: ["400", "500", "600", "700"],
  subsets: ["latin"],
});

const plexMono = IBM_Plex_Mono({
  variable: "--font-plex-mono",
  weight: ["400", "500", "600"],
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "BRISA — BRI Search & Agent",
  description: "Semantic data search and draft-SQL agent over the BRI knowledge base.",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="id" className={`${plexSans.variable} ${plexMono.variable} h-full`}>
      <body className="min-h-full">
        <div className="app-shell grid min-h-screen grid-cols-[248px_minmax(0,1fr)]">
          <Sidebar />
          <main className="min-w-0">{children}</main>
        </div>
        <div className="below-min">
          <div>
            <p className="font-semibold text-[color:var(--color-ink)]">BRISA</p>
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
