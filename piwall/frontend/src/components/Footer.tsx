import Link from "next/link";

const PRODUCT_LINKS = [
  { href: "/", label: "Home" },
  { href: "/lobby", label: "Race Lobby" },
  { href: "/strategy", label: "Strategy Editor" },
  { href: "/season", label: "Season" }
];

const SUPPORT_LINKS = [
  { href: "#", label: "Feedback" }
];

export default function Footer() {
  return (
    <footer className="border-t border-pit-border bg-[linear-gradient(180deg,#181110_0%,#130d0c_100%)] mt-20">
      <div className="max-w-7xl mx-auto px-6 py-6">
        <div className="grid grid-cols-1 md:grid-cols-[1.35fr_0.8fr_0.8fr] gap-5">
          <div className="max-w-md">
            <div className="flex items-center gap-2.5 mb-2.5">
              <img
                src="/f1-car.svg"
                alt=""
                className="w-7 h-3 opacity-80 drop-shadow-[0_0_6px_rgba(184,31,22,0.16)]"
              />
              <span className="text-lg font-extrabold text-white tracking-tight">PIT WALL</span>
            </div>
            <p className="text-[#b9b1ae] text-[13px] leading-relaxed">
              Build race strategy bots, test them against live simulation conditions, and climb the grid with
              telemetry-driven decisions.
            </p>

            <a
              href="https://github.com/darthvader58/phi1"
              target="_blank"
              rel="noreferrer"
              className="mt-3 inline-flex w-9 h-9 rounded-xl border border-white/10 bg-white/5
                         items-center justify-center text-[#b9b1ae] hover:text-white hover:bg-white/10 transition-colors"
              aria-label="Open GitHub repository"
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                <path d="M12 .5C5.65.5.5 5.8.5 12.35c0 5.24 3.3 9.69 7.88 11.26.58.11.79-.26.79-.57 0-.28-.01-1.2-.02-2.17-3.2.71-3.88-1.58-3.88-1.58-.52-1.37-1.28-1.73-1.28-1.73-1.05-.74.08-.72.08-.72 1.16.08 1.77 1.23 1.77 1.23 1.03 1.84 2.7 1.31 3.36 1 .1-.77.4-1.31.73-1.61-2.55-.3-5.23-1.31-5.23-5.83 0-1.29.44-2.34 1.17-3.17-.12-.3-.51-1.5.11-3.13 0 0 .95-.31 3.12 1.21a10.5 10.5 0 0 1 5.68 0c2.16-1.52 3.11-1.21 3.11-1.21.62 1.63.23 2.83.11 3.13.73.83 1.17 1.88 1.17 3.17 0 4.53-2.68 5.52-5.24 5.82.41.36.78 1.06.78 2.14 0 1.55-.01 2.79-.01 3.17 0 .31.21.68.8.57 4.57-1.57 7.87-6.02 7.87-11.26C23.5 5.8 18.35.5 12 .5Z" />
              </svg>
            </a>
          </div>

          <div>
            <h3 className="text-white text-base font-bold mb-2.5">Game</h3>
            <div className="space-y-2">
              {PRODUCT_LINKS.map((link) => (
                <Link
                  key={link.href}
                  href={link.href}
                  className="block text-[#b9b1ae] hover:text-white transition-colors text-[13px]"
                >
                  {link.label}
                </Link>
              ))}
            </div>
          </div>

          <div>
            <h3 className="text-white text-base font-bold mb-2.5">Support</h3>
            <div className="space-y-2">
              {SUPPORT_LINKS.map((link) =>
                link.external ? (
                  <a
                    key={link.label}
                    href={link.href}
                    target="_blank"
                    rel="noreferrer"
                    className="block text-[#b9b1ae] hover:text-white transition-colors text-[13px]"
                  >
                    {link.label}
                  </a>
                ) : (
                  <span key={link.label} className="block text-[#b9b1ae] text-[13px]">
                    {link.label}
                  </span>
                )
              )}
            </div>
          </div>
        </div>

        <div className="mt-6 pt-3 border-t border-pit-border flex items-center justify-between gap-3 flex-wrap">
          <p className="text-[#9f9591] text-xs">© 2026 PIT WALL. All rights reserved.</p>
          <p className="text-[#b9b1ae] text-xs">Built for telemetry-first race strategy. Made with &lt;3 by Shashwat Raj</p>
        </div>
      </div>
    </footer>
  );
}
