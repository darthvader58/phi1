"use client";

import Link from "next/link";

const TRACKS = [
  { name: "Bahrain", key: "bahrain", laps: 57, note: "High deg, 2-stop viable", flag: "BH" },
  { name: "Monaco", key: "monaco", laps: 78, note: "Track position is everything", flag: "MC" },
  { name: "Monza", key: "monza", laps: 53, note: "Low deg, 1-stop temple", flag: "IT" },
  { name: "Spa", key: "spa", laps: 44, note: "Weather chaos, SC prone", flag: "BE" },
  { name: "Silverstone", key: "silverstone", laps: 52, note: "Tyre sensitive", flag: "GB" },
  { name: "Suzuka", key: "suzuka", laps: 53, note: "Compound choice critical", flag: "JP" },
];

const STATS = [
  { label: "Circuits", value: "6", sub: "Real F1 data" },
  { label: "Built-in Bots", value: "5", sub: "Nash to greedy" },
  { label: "Physics Model", value: "7", sub: "Variables per lap" },
  { label: "From FastF1", value: "2024", sub: "Season telemetry" },
];

/* Inline F1 car SVG for section headers — races left to right */
function F1AccentCar({ className = "" }: { className?: string }) {
  return (
    <div className={`relative flex items-center ${className}`}>
      <svg viewBox="0 0 56 16" className="w-10 h-3.5 animate-f1-accent" fill="none">
        {/* Speed lines */}
        <line x1="0" y1="6" x2="6" y2="6" stroke="#e10600" strokeWidth="1" opacity="0.4" className="animate-speed-line-1" />
        <line x1="1" y1="9" x2="8" y2="9" stroke="#e10600" strokeWidth="0.7" opacity="0.25" className="animate-speed-line-2" />
        <line x1="0" y1="12" x2="5" y2="12" stroke="#e10600" strokeWidth="0.5" opacity="0.15" className="animate-speed-line-3" />
        {/* Car body */}
        <path d="M14 4 L24 2 L34 2 L40 4 L44 6 L44 10 L42 11 L36 11 L34 9.5 L32 11 L22 11 L20 9.5 L18 11 L12 11 L12 7 L14 4Z" fill="#e10600" />
        {/* Cockpit */}
        <path d="M25 3 L29 3 L31 5 L26 5Z" fill="#0a0a0a" />
        {/* Front wing */}
        <path d="M41 5 L46 4 L46 7 L44 6Z" fill="#e10600" />
        {/* Rear wing */}
        <path d="M12 3 L16 3 L16 4.5 L12 5Z" fill="#cc0500" />
        <path d="M12 1 L16 1 L16 2.5 L12 2.5Z" fill="#e10600" />
        {/* Wheels */}
        <circle cx="20" cy="11.5" r="2.2" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.4" />
        <circle cx="20" cy="11.5" r="0.9" fill="#3a3a3a" />
        <circle cx="37" cy="11.5" r="2.2" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.4" />
        <circle cx="37" cy="11.5" r="0.9" fill="#3a3a3a" />
        {/* Trail gradient */}
        <rect x="0" y="5" width="12" height="0.5" fill="url(#trailGrad)" rx="0.25" />
        <defs>
          <linearGradient id="trailGrad" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="#e10600" stopOpacity="0" />
            <stop offset="100%" stopColor="#e10600" stopOpacity="0.6" />
          </linearGradient>
        </defs>
      </svg>
    </div>
  );
}

/* Large F1 car for the hero banner — races across the screen */
function HeroRacingCar() {
  return (
    <div className="absolute bottom-8 left-0 right-0 overflow-hidden pointer-events-none h-16 opacity-60">
      {/* Track line */}
      <div className="absolute bottom-2 left-0 right-0 h-px bg-pit-border/40" />
      {/* Racing car — continuous left-to-right animation */}
      <div className="animate-f1-race absolute bottom-3">
        <svg viewBox="0 0 120 32" className="w-24 h-10" fill="none">
          {/* Speed lines */}
          <line x1="0" y1="12" x2="16" y2="12" stroke="#e10600" strokeWidth="1.2" opacity="0.5">
            <animate attributeName="x2" values="16;12;16" dur="0.3s" repeatCount="indefinite" />
          </line>
          <line x1="2" y1="16" x2="18" y2="16" stroke="#e10600" strokeWidth="0.8" opacity="0.3">
            <animate attributeName="x2" values="18;14;18" dur="0.4s" repeatCount="indefinite" />
          </line>
          <line x1="4" y1="20" x2="14" y2="20" stroke="#e10600" strokeWidth="0.6" opacity="0.2">
            <animate attributeName="x2" values="14;10;14" dur="0.35s" repeatCount="indefinite" />
          </line>
          {/* Main body */}
          <path d="M24 8 L44 4 L68 4 L80 8 L88 12 L88 22 L84 24 L70 24 L67 20 L64 24 L42 24 L39 20 L36 24 L22 24 L22 16 L24 8Z" fill="#e10600" />
          {/* Body highlight */}
          <path d="M30 9 L50 6 L70 6 L78 9Z" fill="#ff1801" opacity="0.4" />
          {/* Cockpit */}
          <path d="M48 6 L56 6 L60 10 L50 10Z" fill="#0a0a0a" />
          {/* Halo */}
          <path d="M49 7 Q53 3 58 7" stroke="#555" strokeWidth="1.2" fill="none" />
          {/* Driver helmet */}
          <ellipse cx="53" cy="7.5" rx="2" ry="1.8" fill="#e10600" />
          <ellipse cx="53.5" cy="7" rx="1" ry="0.8" fill="#222" />
          {/* Front wing */}
          <path d="M82 9 L92 7 L92 14 L88 12Z" fill="#e10600" />
          <path d="M92 6 L96 5 L96 8 L92 8Z" fill="#cc0500" />
          {/* Endplates */}
          <rect x="95" y="4" width="1.5" height="10" rx="0.5" fill="#cc0500" />
          {/* Rear wing */}
          <path d="M22 6 L30 6 L30 8 L22 9Z" fill="#cc0500" />
          <path d="M20 2 L30 2 L30 4 L20 5Z" fill="#e10600" />
          {/* Rear wing endplates */}
          <rect x="19" y="1" width="1.5" height="9" rx="0.5" fill="#cc0500" />
          {/* Wheels with spin animation */}
          <circle cx="38" cy="24" r="5" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.8" />
          <circle cx="38" cy="24" r="2" fill="#3a3a3a" />
          <line x1="35" y1="24" x2="41" y2="24" stroke="#2a2a2a" strokeWidth="0.5">
            <animateTransform attributeName="transform" type="rotate" from="0 38 24" to="360 38 24" dur="0.15s" repeatCount="indefinite" />
          </line>
          <circle cx="72" cy="24" r="5" fill="#1a1a1a" stroke="#3a3a3a" strokeWidth="0.8" />
          <circle cx="72" cy="24" r="2" fill="#3a3a3a" />
          <line x1="69" y1="24" x2="75" y2="24" stroke="#2a2a2a" strokeWidth="0.5">
            <animateTransform attributeName="transform" type="rotate" from="0 72 24" to="360 72 24" dur="0.15s" repeatCount="indefinite" />
          </line>
          {/* Exhaust glow */}
          <ellipse cx="20" cy="14" rx="4" ry="2" fill="#e10600" opacity="0.15">
            <animate attributeName="rx" values="4;6;4" dur="0.2s" repeatCount="indefinite" />
            <animate attributeName="opacity" values="0.15;0.25;0.15" dur="0.2s" repeatCount="indefinite" />
          </ellipse>
        </svg>
      </div>
    </div>
  );
}

export default function Home() {
  return (
    <div className="relative">
      {/* Hero */}
      <section className="relative overflow-hidden">
        {/* Background grid */}
        <div className="absolute inset-0 bg-grid opacity-50" />
        {/* Red glow */}
        <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px]
                        bg-gradient-radial from-f1-red/8 to-transparent" />

        <div className="relative max-w-7xl mx-auto px-6 pt-24 pb-20">
          <div className="max-w-3xl">
            {/* Eyebrow */}
            <div className="flex items-center gap-3 mb-6 animate-fade-in">
              <F1AccentCar />
              <span className="section-label text-f1-red">Algorithmic Race Strategy</span>
            </div>

            {/* Title */}
            <h1 className="text-hero text-white mb-6 animate-slide-up">
              Your Code.
              <br />
              <span className="text-f1-red">Their Race.</span>
            </h1>

            {/* Description */}
            <p className="text-lg text-pit-text max-w-xl mb-10 leading-relaxed animate-slide-up"
               style={{ animationDelay: "0.1s" }}>
              Write strategy bots that compete in physics-accurate F1 simulations.
              Real tyre degradation from FastF1 telemetry. Real game theory.
              No driving — pure strategy.
            </p>

            {/* CTAs */}
            <div className="flex items-center gap-4 animate-slide-up" style={{ animationDelay: "0.2s" }}>
              <Link href="/lobby" className="btn-primary text-base px-8 py-3">
                Start Racing
              </Link>
              <Link href="/strategy" className="btn-secondary text-base px-8 py-3">
                Write a Bot
              </Link>
            </div>
          </div>
        </div>

        {/* Racing car animation across the hero */}
        <HeroRacingCar />

        {/* Bottom fade */}
        <div className="absolute bottom-0 left-0 right-0 h-24 gradient-fade-b" />
      </section>

      {/* Stats bar */}
      <section className="border-y border-pit-border bg-pit-dark">
        <div className="max-w-7xl mx-auto px-6 py-6">
          <div className="grid grid-cols-2 md:grid-cols-4 gap-6">
            {STATS.map((stat) => (
              <div key={stat.label} className="text-center md:text-left">
                <div className="stat-value">{stat.value}</div>
                <div className="text-sm font-medium text-pit-text mt-0.5">{stat.label}</div>
                <div className="text-xs text-pit-muted">{stat.sub}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* How it works */}
      <section className="max-w-7xl mx-auto px-6 py-20">
        <div className="flex items-center gap-3 mb-10">
          <F1AccentCar />
          <span className="section-label">How It Works</span>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
          {[
            {
              step: "01",
              title: "Write",
              description: "Code a Python strategy function. Decide when to pit, which tyres to use, how to react to weather and rivals.",
              accent: "text-f1-red",
            },
            {
              step: "02",
              title: "Race",
              description: "Your bot competes against others in a simulation built on real 2024 F1 telemetry data from FastF1.",
              accent: "text-f1-blue",
            },
            {
              step: "03",
              title: "Climb",
              description: "ELO ratings, season championships, and a hall of fame. Study rival strategies and iterate.",
              accent: "text-compound-medium",
            },
          ].map((item) => (
            <div key={item.step} className="card-hover p-6 group">
              <div className={`text-4xl font-black ${item.accent} opacity-20
                              group-hover:opacity-40 transition-opacity mb-4`}>
                {item.step}
              </div>
              <h3 className="text-xl font-bold text-white mb-2">{item.title}</h3>
              <p className="text-sm text-pit-text leading-relaxed">{item.description}</p>
            </div>
          ))}
        </div>
      </section>

      {/* Feature cards */}
      <section className="max-w-7xl mx-auto px-6 pb-20">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
          {/* Real Data card */}
          <div className="card p-6 relative overflow-hidden group">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-radial from-f1-red/10 to-transparent" />
            <div className="relative">
              <span className="section-label text-f1-red">Real Data</span>
              <h3 className="text-xl font-bold text-white mt-2 mb-3">Calibrated Physics</h3>
              <p className="text-sm text-pit-text leading-relaxed mb-4">
                Tyre degradation follows a power law fitted from actual race stints:
              </p>
              <div className="bg-pit-dark rounded-lg p-4 font-mono text-sm text-pit-text border border-pit-border">
                <span className="text-pit-muted">T(lap) = </span>
                <span className="text-white">T_base</span>
                <span className="text-pit-muted"> + </span>
                <span className="text-f1-red">α + k·age<sup>e</sup></span>
                <span className="text-pit-muted"> + </span>
                <span className="text-compound-medium">0.032·fuel</span>
                <span className="text-pit-muted"> + ε</span>
              </div>
            </div>
          </div>

          {/* Game Theory card */}
          <div className="card p-6 relative overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-radial from-f1-blue/10 to-transparent" />
            <div className="relative">
              <span className="section-label text-f1-blue">Game Theory</span>
              <h3 className="text-xl font-bold text-white mt-2 mb-3">Strategic Depth</h3>
              <div className="space-y-2 text-sm">
                {[
                  "Nash pit windows — integral-based optimal timing",
                  "Undercut detection — exploit rival tyre age",
                  "Bayesian beliefs — estimate what rivals will do",
                  "Weather Markov chains — adapt to changing conditions",
                ].map((item, i) => (
                  <div key={i} className="flex items-start gap-2 text-pit-text">
                    <span className="text-f1-blue mt-0.5 text-xs">▸</span>
                    <span>{item}</span>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Circuit Rotation */}
      <section className="max-w-7xl mx-auto px-6 pb-20">
        <div className="flex items-center gap-3 mb-8">
          <F1AccentCar />
          <span className="section-label">Circuit Rotation</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {TRACKS.map((track, idx) => (
            <Link
              key={track.key}
              href={`/track/${track.key}`}
              className="card-hover p-4 group"
            >
              <div className="flex items-center justify-between mb-3">
                <span className="text-[10px] font-bold text-pit-muted">R{idx + 1}</span>
                <span className="text-[10px] font-bold text-pit-muted bg-pit-surface px-1.5 py-0.5 rounded">
                  {track.flag}
                </span>
              </div>
              <h4 className="font-bold text-white text-sm mb-0.5 group-hover:text-f1-red transition-colors">
                {track.name}
              </h4>
              <div className="text-xs text-pit-muted mb-2">{track.laps} laps</div>
              <div className="text-[11px] text-pit-text leading-snug">{track.note}</div>
            </Link>
          ))}
        </div>
      </section>

      {/* Bottom CTA */}
      <section className="border-t border-pit-border">
        <div className="max-w-7xl mx-auto px-6 py-16 text-center">
          <h2 className="text-title text-white mb-4">Ready to strategize?</h2>
          <p className="text-pit-text mb-8 max-w-md mx-auto">
            Your first race is one function away. Write it, test it, race it.
          </p>
          <Link href="/lobby" className="btn-primary text-base px-10 py-3.5">
            Enter the Pit Wall
          </Link>
        </div>
      </section>
    </div>
  );
}
