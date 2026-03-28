import type { Metadata } from "next";
import localFont from "next/font/local";
import Link from "next/link";
import "./globals.css";

const geistMono = localFont({
  src: "./fonts/GeistMonoVF.woff",
  variable: "--font-geist-mono",
  weight: "100 900",
});

export const metadata: Metadata = {
  title: "PIT WALL — F1 Strategy Game",
  description: "Algorithmic F1 race strategy game with real telemetry data",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className={`${geistMono.variable} font-mono bg-gray-950 text-gray-100 min-h-screen`}>
        {/* Navigation */}
        <nav className="border-b border-gray-800 bg-gray-950/95 backdrop-blur sticky top-0 z-50">
          <div className="max-w-7xl mx-auto px-4 py-3 flex items-center gap-6">
            <Link href="/" className="flex items-center gap-2 group">
              <span className="text-red-500 font-bold text-lg tracking-tighter group-hover:text-red-400 transition-colors">
                PIT WALL
              </span>
            </Link>
            <div className="flex gap-4 text-sm text-gray-400">
              <Link href="/lobby" className="hover:text-gray-200 transition-colors">
                Lobby
              </Link>
              <Link href="/strategy" className="hover:text-gray-200 transition-colors">
                Strategy
              </Link>
              <Link href="/season" className="hover:text-gray-200 transition-colors">
                Season
              </Link>
            </div>
            <div className="ml-auto text-xs text-gray-600">
              v0.1.0
            </div>
          </div>
        </nav>
        <main>{children}</main>
      </body>
    </html>
  );
}
