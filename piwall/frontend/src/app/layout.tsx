import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "PIT WALL — F1 Strategy Game",
  description: "Algorithmic F1 race strategy game with real telemetry data",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-pit-black text-pit-light min-h-screen">
        {/* Navigation */}
        <nav className="fixed top-0 left-0 right-0 z-50 bg-pit-black/80 backdrop-blur-xl border-b border-pit-border">
          <div className="max-w-7xl mx-auto px-6 h-14 flex items-center gap-8">
            <Link href="/" className="flex items-center gap-2.5 group">
              <div className="w-7 h-7 bg-f1-red rounded-md flex items-center justify-center
                              group-hover:shadow-glow-red transition-shadow duration-300">
                <span className="text-white text-xs font-black">PW</span>
              </div>
              <span className="text-white font-bold text-sm tracking-tight hidden sm:inline">
                PIT WALL
              </span>
            </Link>

            <div className="flex items-center gap-1">
              {[
                { href: "/lobby", label: "Lobby" },
                { href: "/strategy", label: "Strategy" },
                { href: "/season", label: "Season" },
              ].map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="px-3 py-1.5 text-sm text-pit-text hover:text-white
                             rounded-md hover:bg-white/5 transition-all duration-200"
                >
                  {link.label}
                </Link>
              ))}
            </div>

            <div className="ml-auto flex items-center gap-3">
              <span className="text-[11px] text-pit-muted font-mono">v0.1</span>
              <div className="w-px h-4 bg-pit-border" />
              <Link
                href="/lobby"
                className="text-xs font-semibold text-f1-red hover:text-f1-redHover transition-colors"
              >
                Race Now
              </Link>
            </div>
          </div>
        </nav>

        {/* Content with nav offset */}
        <main className="pt-14">{children}</main>
      </body>
    </html>
  );
}
