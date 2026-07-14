import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "pitchprob",
  description:
    "Football probability engine: calibrated market estimates, honest backtests. No profit promises.",
};

const NAV = [
  { href: "/", label: "Price a fixture" },
  { href: "/coupons", label: "Coupons" },
  { href: "/backtests", label: "Backtests" },
];

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col">
        <header className="border-b border-line">
          <div className="mx-auto flex w-full max-w-6xl items-baseline gap-8 px-6 py-4">
            <Link href="/" className="text-lg font-semibold tracking-tight">
              pitchprob
              <span aria-hidden className="ml-1.5 inline-block h-2.5 w-2.5 rounded-full bg-gold" />
            </Link>
            <nav className="flex gap-5 text-sm">
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
              probabilities, not promises
            </span>
          </div>
        </header>
        <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-8">{children}</main>
        <footer className="border-t border-line">
          <p className="mx-auto w-full max-w-6xl px-6 py-4 text-xs leading-relaxed text-ink-muted">
            Model estimates carry uncertainty and are routinely worse than closing-line
            odds. Nothing here guarantees profit; treat every number as an estimate with
            error bars.
          </p>
        </footer>
      </body>
    </html>
  );
}
