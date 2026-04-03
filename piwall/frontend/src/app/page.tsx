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

/* Small F1 car accent — replaces the old red line before section labels */
function F1AccentCar() {
  return (
    <img
      src="/f1-car.svg"
      alt=""
      className="w-8 h-3 animate-f1-accent drop-shadow-[0_0_4px_rgba(225,6,0,0.3)]"
    />
  );
}

/* Large F1 car racing across the hero banner — left to right loop */
function HeroRacingCar() {
  return (
    <div className="absolute bottom-6 left-0 right-0 overflow-hidden pointer-events-none h-20">
      {/* Track surface line */}
      <div className="absolute bottom-3 left-0 right-0 h-px bg-pit-border/30" />
      {/* The racing car */}
      <div className="animate-f1-race absolute bottom-4 flex items-end">
        {/* Speed trail lines behind the car */}
        <div className="flex flex-col gap-[3px] mr-1 opacity-60">
          <div className="h-[2px] w-8 bg-gradient-to-r from-transparent to-f1-red/60 animate-speed-line-1 rounded-full" />
          <div className="h-[1.5px] w-6 bg-gradient-to-r from-transparent to-f1-red/40 animate-speed-line-2 rounded-full" />
          <div className="h-[1px] w-10 bg-gradient-to-r from-transparent to-f1-red/30 animate-speed-line-3 rounded-full" />
        </div>
        <img
          src="/f1-car.svg"
          alt=""
          className="w-28 h-11 drop-shadow-[0_0_16px_rgba(225,6,0,0.35)]"
        />
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
