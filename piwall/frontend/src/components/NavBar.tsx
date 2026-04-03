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
          <div className="relative w-8 h-7 flex items-center justify-center overflow-hidden">
            {/* F1 car SVG with animation */}
            <svg
              viewBox="0 0 40 20"
              className="w-8 h-5 group-hover:animate-f1-rev transition-transform duration-300"
              fill="none"
            >
              {/* Speed lines behind the car */}
              <line x1="0" y1="8" x2="8" y2="8" stroke="#e10600" strokeWidth="0.8" opacity="0.3" className="animate-speed-line-1" />
              <line x1="2" y1="11" x2="10" y2="11" stroke="#e10600" strokeWidth="0.6" opacity="0.2" className="animate-speed-line-2" />
              <line x1="1" y1="14" x2="7" y2="14" stroke="#e10600" strokeWidth="0.5" opacity="0.15" className="animate-speed-line-3" />
              {/* Car body */}
              <path d="M12 6 L22 4 L30 4 L35 6 L38 8 L38 13 L36 14 L30 14 L28 12 L26 14 L18 14 L16 12 L14 14 L10 14 L10 10 L12 6Z" fill="#e10600" />
              {/* Cockpit */}
              <path d="M22 5 L26 5 L28 7 L23 7Z" fill="#0a0a0a" />
              {/* Front wing */}
              <path d="M35 7 L40 6 L40 9 L38 8Z" fill="#e10600" />
              {/* Rear wing */}
              <path d="M10 5 L14 5 L14 7 L10 8Z" fill="#cc0500" />
              <path d="M10 3 L14 3 L14 4.5 L10 4.5Z" fill="#e10600" />
              {/* Wheels */}
              <circle cx="16" cy="14" r="2.8" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.5" />
              <circle cx="16" cy="14" r="1.2" fill="#3a3a3a" />
              <circle cx="32" cy="14" r="2.8" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.5" />
              <circle cx="32" cy="14" r="1.2" fill="#3a3a3a" />
              {/* Halo */}
              <path d="M22 5.5 Q24 3.5 27 5.5" stroke="#555" strokeWidth="0.8" fill="none" />
            </svg>
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
