"use client";

import Link from "next/link";

export default function Home() {
  return (
    <div className="max-w-4xl mx-auto px-4 py-16">
      {/* Hero */}
      <div className="text-center mb-16">
        <h1 className="text-6xl font-bold tracking-tighter mb-4">
          <span className="text-red-500">PIT</span>{" "}
          <span className="text-gray-200">WALL</span>
        </h1>
        <p className="text-xl text-gray-400 mb-2">
          Algorithmic F1 Race Strategy Game
        </p>
        <p className="text-sm text-gray-600 max-w-lg mx-auto">
          Write strategy bots that race each other in a physics-accurate F1
          simulation. Real telemetry data. Real tyre degradation. Real game
          theory.
        </p>
      </div>

      {/* Feature cards */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-12">
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="text-red-500 text-sm font-bold mb-2">REAL DATA</div>
          <p className="text-gray-400 text-sm">
            Tyre degradation curves fitted from actual FastF1 telemetry.
            6 real circuits with calibrated physics.
          </p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="text-yellow-500 text-sm font-bold mb-2">GAME THEORY</div>
          <p className="text-gray-400 text-sm">
            Nash pit windows, undercut detection, Bayesian rival modeling.
            Your bot sees what rivals are doing and adapts.
          </p>
        </div>
        <div className="bg-gray-900 border border-gray-800 rounded-lg p-5">
          <div className="text-gray-300 text-sm font-bold mb-2">COMPETE</div>
          <p className="text-gray-400 text-sm">
            ELO-rated seasons across 6 tracks. Climb the leaderboard.
            Study rival strategies. Iterate and improve.
          </p>
        </div>
      </div>

      {/* Tracks preview */}
      <div className="bg-gray-900 border border-gray-800 rounded-lg p-5 mb-8">
        <div className="text-sm font-bold text-gray-300 mb-3">CIRCUIT ROTATION</div>
        <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
          {[
            { name: "Bahrain", key: "bahrain", laps: 57, note: "High deg, 2-stop viable" },
            { name: "Monaco", key: "monaco", laps: 78, note: "No overtaking, strategy is everything" },
            { name: "Monza", key: "monza", laps: 53, note: "Low deg, 1-stop temple" },
            { name: "Spa", key: "spa", laps: 44, note: "Weather chaos, SC prone" },
            { name: "Silverstone", key: "silverstone", laps: 52, note: "Tyre sensitive, medium deg" },
            { name: "Suzuka", key: "suzuka", laps: 53, note: "Technical, compound choice critical" },
          ].map((track) => (
            <Link
              key={track.key}
              href={`/track/${track.key}`}
              className="bg-gray-800/50 rounded p-3 hover:bg-gray-800 transition-colors"
            >
              <div className="font-bold text-gray-200">{track.name}</div>
              <div className="text-gray-500 text-xs">{track.laps} laps</div>
              <div className="text-gray-600 text-xs mt-1">{track.note}</div>
            </Link>
          ))}
        </div>
      </div>

      {/* CTA */}
      <div className="text-center">
        <Link
          href="/lobby"
          className="inline-block bg-red-700 hover:bg-red-600 text-white px-8 py-3
                     rounded font-bold text-sm transition-colors"
        >
          Enter the Pit Wall
        </Link>
      </div>
    </div>
  );
}
