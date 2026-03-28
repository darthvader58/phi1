"use client";

import { useState } from "react";
import Link from "next/link";

const NAV_LINKS = [
  { href: "/lobby", label: "Lobby" },
  { href: "/strategy", label: "Strategy" },
  { href: "/season", label: "Season" },
];

export default function NavBar() {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-pit-black/80 backdrop-blur-xl border-b border-pit-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-4 sm:gap-8">
        {/* Logo */}
        <Link href="/" className="flex items-center gap-2.5 group flex-shrink-0">
          <div className="w-7 h-7 bg-f1-red rounded-md flex items-center justify-center
                          group-hover:shadow-glow-red transition-shadow duration-300">
            <span className="text-white text-xs font-black">PW</span>
          </div>
          <span className="text-white font-bold text-sm tracking-tight hidden sm:inline">
            PIT WALL
          </span>
        </Link>

        {/* Desktop nav */}
        <div className="hidden sm:flex items-center gap-1">
          {NAV_LINKS.map((link) => (
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
          <span className="text-[11px] text-pit-muted font-mono hidden sm:inline">v0.1</span>
          <div className="w-px h-4 bg-pit-border hidden sm:block" />
          <Link
            href="/lobby"
            className="text-xs font-semibold text-f1-red hover:text-f1-redHover transition-colors hidden sm:inline"
          >
            Race Now
          </Link>

          {/* Mobile hamburger */}
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="sm:hidden p-2 text-pit-text hover:text-white transition-colors"
            aria-label="Toggle menu"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
              {menuOpen ? (
                <path d="M5 5L15 15M15 5L5 15" />
              ) : (
                <path d="M3 5h14M3 10h14M3 15h14" />
              )}
            </svg>
          </button>
        </div>
      </div>

      {/* Mobile dropdown */}
      {menuOpen && (
        <div className="sm:hidden border-t border-pit-border bg-pit-black/95 backdrop-blur-xl animate-fade-in">
          <div className="px-4 py-3 space-y-1">
            {NAV_LINKS.map((link) => (
              <Link
                key={link.href}
                href={link.href}
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-2.5 text-sm text-pit-text hover:text-white
                           rounded-lg hover:bg-white/5 transition-all duration-200"
              >
                {link.label}
              </Link>
            ))}
            <div className="pt-2 border-t border-pit-border mt-2">
              <Link
                href="/lobby"
                onClick={() => setMenuOpen(false)}
                className="block px-3 py-2.5 text-sm font-semibold text-f1-red"
              >
                Race Now
              </Link>
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
