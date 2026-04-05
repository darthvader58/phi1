import Link from "next/link";
import OpenAuthOnMount from "@/components/OpenAuthOnMount";
import { auth } from "@/lib/auth";

export const dynamic = "force-dynamic";

const TRACKS = [
  { name: "Bahrain", key: "bahrain", laps: 57, note: "High deg, 2-stop viable", flag: "BH" },
  { name: "Monaco", key: "monaco", laps: 78, note: "Track position is everything", flag: "MC" },
  { name: "Monza", key: "monza", laps: 53, note: "Low deg, 1-stop temple", flag: "IT" },
  { name: "Spa", key: "spa", laps: 44, note: "Weather chaos, SC prone", flag: "BE" },
  { name: "Silverstone", key: "silverstone", laps: 52, note: "Tyre sensitive", flag: "GB" },
  { name: "Suzuka", key: "suzuka", laps: 53, note: "Compound choice critical", flag: "JP" }
];

const STATS = [
  { label: "Circuits", value: "6", sub: "Real F1 data" },
  { label: "Built-in Bots", value: "5", sub: "Nash to greedy" },
  { label: "Physics Model", value: "7", sub: "Variables per lap" },
  { label: "From FastF1", value: "2024", sub: "Season telemetry" }
];

function F1AccentCar() {
  return (
    <img
      src="/f1-car.svg"
      alt=""
      className="w-8 h-3 animate-f1-accent drop-shadow-[0_0_4px_rgba(225,6,0,0.3)]"
    />
  );
}

function HeroRacingCar() {
  return (
    <div className="absolute inset-0 overflow-hidden pointer-events-none">
      <div className="animate-f1-hero-race absolute top-1/2 left-1/2">
        <img
          src="/f1-car.svg"
          alt=""
          className="w-[500px] h-auto"
        />
      </div>
    </div>
  );
}

export default async function HomePage() {
  const session = await auth();

  return (
    <div className="relative">
      {!session?.user ? <OpenAuthOnMount /> : null}

      <section className="relative overflow-hidden">
        <div className="absolute inset-0 bg-grid opacity-50" />
        <div
          className="absolute top-0 left-1/2 -translate-x-1/2 w-[600px] h-[400px]
                     bg-gradient-radial from-f1-red/8 to-transparent"
        />

        <div className="relative max-w-7xl mx-auto px-6 pt-24 pb-20">
          <div className="max-w-3xl">
            <div className="flex items-center gap-3 mb-6 animate-fade-in">
              <F1AccentCar />
              <span className="section-label text-f1-red">Algorithmic Race Strategy</span>
            </div>

            <h1 className="text-hero text-white mb-6 animate-slide-up">
              Your Code.
              <br />
              <span className="text-f1-red">Their Race.</span>
            </h1>

            <p
              className="text-lg text-pit-text max-w-xl mb-10 leading-relaxed animate-slide-up"
              style={{ animationDelay: "0.1s" }}
            >
              Write strategy bots that compete in physics-accurate F1 simulations. Build a race plan, test it
              against the field, and refine it across real-world circuits and evolving conditions.
            </p>

            <div className="flex items-center gap-4 animate-slide-up flex-wrap" style={{ animationDelay: "0.2s" }}>
              <Link href="/lobby" className="btn-secondary text-base px-8 py-3">
                Start Racing
              </Link>
              <Link href="/strategy" className="btn-primary text-base px-8 py-3">
                Write a Bot
              </Link>
            </div>
          </div>
        </div>

        <HeroRacingCar />
        <div className="absolute bottom-0 left-0 right-0 h-24 gradient-fade-b" />
      </section>

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
              description:
                "Code a Python strategy function. Decide when to pit, which tyres to use, and how to react to weather and rivals.",
              accent: "text-f1-red"
            },
            {
              step: "02",
              title: "Race",
              description:
                "Your bot competes against others in a simulation built on real 2024 F1 telemetry data from FastF1.",
              accent: "text-f1-blue"
            },
            {
              step: "03",
              title: "Track",
              description:
                "Persist submissions and race results to your account so podiums, finishes, and recent entries survive across sessions.",
              accent: "text-compound-medium"
            }
          ].map((item) => (
            <div key={item.step} className="card-hover p-6 group">
              <div
                className={`text-4xl font-black ${item.accent} opacity-20
                            group-hover:opacity-40 transition-opacity mb-4`}
              >
                {item.step}
              </div>
              <h3 className="text-xl font-bold text-white mb-2">{item.title}</h3>
              <p className="text-sm text-pit-text leading-relaxed">{item.description}</p>
            </div>
          ))}
        </div>
      </section>

      <section className="max-w-7xl mx-auto px-6 pb-20">
        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
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

          <div className="card p-6 relative overflow-hidden">
            <div className="absolute top-0 right-0 w-32 h-32 bg-gradient-radial from-f1-blue/10 to-transparent" />
            <div className="relative">
              <span className="section-label text-f1-blue">Race Intelligence</span>
              <h3 className="text-xl font-bold text-white mt-2 mb-3">Decision Pressure</h3>
              <div className="space-y-2 text-sm">
                {[
                  "Track weather swings, degradation curves, and safety-car timing",
                  "Balance undercuts, stint length, and compound windows lap by lap",
                  "Compare your choices against built-in bots with different risk profiles",
                  "Move from quick races into season play across the full circuit rotation"
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

      <section className="max-w-7xl mx-auto px-6 pb-20">
        <div className="flex items-center gap-3 mb-8">
          <F1AccentCar />
          <span className="section-label">Circuit Rotation</span>
        </div>

        <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
          {TRACKS.map((track, idx) => (
            <Link key={track.key} href={`/track/${track.key}`} className="card-hover p-4 group">
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

      <section className="border-t border-pit-border">
        <div className="max-w-7xl mx-auto px-6 py-16 text-center">
          <h2 className="text-title text-white mb-4">Ready to strategize?</h2>
          <p className="text-pit-text mb-8 max-w-md mx-auto">
            Your first race is one function away. Write it, test it, and see how it survives against the field.
          </p>
          <Link href="/lobby" className="btn-primary text-base px-10 py-3.5">
            Enter the Pit Wall
          </Link>
        </div>
      </section>
    </div>
  );
}
