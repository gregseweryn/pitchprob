import type { Metadata } from "next";
// geist npm package, not next/font/google: no build-time network fetch, so
// builds are deterministic offline (ADR 0009 — CI must not depend on a CDN)
import { GeistSans } from "geist/font/sans";
import { GeistMono } from "geist/font/mono";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "pitchprob",
  description:
    "Silnik prawdopodobieństw piłkarskich: skalibrowane oszacowania rynkowe, uczciwe backtesty. Bez obietnic zysku.",
};

// Operator-facing copy is Polish; code, comments and docs stay English.
// The operator reads this surface daily and is the only user.
const NAV = [
  { href: "/jak-to-dziala", label: "Jak to działa" },
  { href: "/scanner", label: "Skaner" },
  { href: "/ledger", label: "Dziennik" },
  { href: "/", label: "Wycena meczu" },
  { href: "/coupons", label: "Kupony" },
  { href: "/backtests", label: "Backtesty" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${GeistSans.variable} ${GeistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="border-b border-line">
          {/* Wraps rather than overflowing: five nav items plus the wordmark
            * exceed a 390px viewport, and a page that scrolls sideways to
            * reach its own navigation is broken, not dense. */}
          <div className="mx-auto flex w-full max-w-6xl flex-wrap items-baseline gap-x-8 gap-y-2 px-6 py-4">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              pitchprob
              <span aria-hidden className="ml-1.5 inline-block h-2.5 w-2.5 rounded-full bg-gold" />
            </Link>
            <nav className="flex flex-wrap gap-x-5 gap-y-1 text-sm">
              {NAV.map((item) => (
                <Link
                  key={item.href}
                  href={item.href}
                  className="text-ink-muted transition-colors duration-150 hover:text-ink"
                >
                  {item.label}
                </Link>
              ))}
            </nav>
            <span className="ml-auto hidden text-xs text-ink-muted sm:block">
              prawdopodobieństwa, nie obietnice
            </span>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
        <footer className="border-t border-line">
          <p className="mx-auto w-full max-w-6xl px-6 py-4 text-xs leading-relaxed text-ink-muted">
            Oszacowania modelu obarczone są niepewnością i regularnie wypadają gorzej
            niż kurs zamknięcia. Nic tutaj nie gwarantuje zysku — każdą liczbę traktuj
            jako oszacowanie z marginesem błędu.
          </p>
        </footer>
      </body>
    </html>
  );
}
