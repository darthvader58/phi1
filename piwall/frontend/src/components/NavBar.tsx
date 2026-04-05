"use client";

import { useState } from "react";
import Link from "next/link";
import type { Session } from "next-auth";
import AuthModal from "@/components/AuthModal";
import UserMenu from "@/components/UserMenu";

const NAV_LINKS = [
  { href: "/lobby", label: "Lobby" },
  { href: "/strategy", label: "Strategy" },
  { href: "/season", label: "Season" }
];

type NavBarProps = {
  session: Session | null;
  googleEnabled: boolean;
};

export default function NavBar({ session, googleEnabled }: NavBarProps) {
  const [menuOpen, setMenuOpen] = useState(false);

  return (
    <nav className="fixed top-0 left-0 right-0 z-50 bg-pit-black/80 backdrop-blur-xl border-b border-pit-border">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 h-14 flex items-center gap-4 sm:gap-8">
        <Link href="/" className="flex items-center gap-2.5 group flex-shrink-0">
          <img
            src="/f1-car.svg"
            alt=""
            className="w-14 h-5 group-hover:animate-f1-rev transition-transform duration-300
                       drop-shadow-[0_0_6px_rgba(225,6,0,0.3)]
                       group-hover:drop-shadow-[0_0_12px_rgba(225,6,0,0.5)]"
          />
          <span className="text-white font-bold text-sm tracking-tight hidden sm:inline">PIT WALL</span>
        </Link>

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
          {session?.user ? (
            <UserMenu image={session.user.image} name={session.user.name} />
          ) : (
            <div className="hidden sm:block">
              <AuthModal googleEnabled={googleEnabled} triggerLabel="Login / Signup" />
            </div>
          )}

          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="sm:hidden p-2 text-pit-text hover:text-white transition-colors"
            aria-label="Toggle menu"
            type="button"
          >
            <svg width="20" height="20" viewBox="0 0 20 20" fill="none" stroke="currentColor" strokeWidth="1.5">
              {menuOpen ? <path d="M5 5L15 15M15 5L5 15" /> : <path d="M3 5h14M3 10h14M3 15h14" />}
            </svg>
          </button>
        </div>
      </div>

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
              {session?.user ? (
                <Link
                  href="/account"
                  onClick={() => setMenuOpen(false)}
                  className="block px-3 py-2.5 text-sm font-semibold text-f1-red"
                >
                  Signed in as {session.user.name || "Driver"}
                </Link>
              ) : (
                <div className="px-3 py-2.5">
                  <AuthModal googleEnabled={googleEnabled} triggerLabel="Login / Signup" />
                </div>
              )}
            </div>
          </div>
        </div>
      )}
    </nav>
  );
}
