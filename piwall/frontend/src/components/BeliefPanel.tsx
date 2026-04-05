"use client";

import { CarState, getCompoundColor, getCarColor } from "@/lib/types";

interface RivalBelief {
  age: number;
  compound: string;
  pit_prob: number;
  confidence: number;
  undercut: boolean;
  uc_gain: number;
}

interface Props {
  cars: CarState[];
  selectedCarId?: string;
}

export default function BeliefPanel({ cars, selectedCarId }: Props) {
  // Show beliefs for the selected car (or leader by default)
  const sorted = [...cars]
    .filter((c) => !c.retired)
    .sort((a, b) => a.position - b.position);

  const viewCar = selectedCarId
    ? sorted.find((c) => c.car_id === selectedCarId)
    : sorted[0];

  if (!viewCar || !viewCar.beliefs || Object.keys(viewCar.beliefs).length === 0) {
    return (
      <div className="card overflow-hidden">
        <div className="px-4 py-3 border-b border-pit-border">
          <span className="section-label">Rival Analysis</span>
        </div>
        <p className="text-pit-muted text-xs text-center py-6">No belief data available yet.</p>
      </div>
    );
  }

  const beliefs = viewCar.beliefs as Record<string, RivalBelief>;

  // Sort rivals by pit probability (most likely to pit first)
  const rivals = Object.entries(beliefs).sort(
    ([, a], [, b]) => (b.pit_prob || 0) - (a.pit_prob || 0)
  );

  // Count undercut opportunities
  const undercutCount = rivals.filter(([, b]) => b.undercut).length;

  return (
    <div className="card overflow-hidden">
      <div className="px-4 py-3 border-b border-pit-border flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className="section-label">Rival Analysis</span>
          <span className="text-[10px] text-pit-muted font-mono">{viewCar.car_id}</span>
        </div>
        {undercutCount > 0 && (
          <span className="badge bg-f1-red/10 text-f1-red text-[9px] font-bold">
            {undercutCount} UNDERCUT{undercutCount > 1 ? "S" : ""}
          </span>
        )}
      </div>

      {/* Column headers */}
      <div className="px-4 py-1.5 flex items-center gap-2 text-[9px] text-pit-muted font-medium border-b border-pit-border/50">
        <span className="w-14">RIVAL</span>
        <span className="w-8 text-center">TYRE</span>
        <span className="w-10 text-right">AGE</span>
        <span className="flex-1">PIT PROB</span>
        <span className="w-8 text-right">CONF</span>
        <span className="w-6 text-center">UC</span>
      </div>

      <div className="max-h-[280px] overflow-y-auto">
        {rivals.map(([rivalId, belief], idx) => {
          const rivalCar = cars.find((c) => c.car_id === rivalId);
          const carColor = rivalCar
            ? getCarColor(sorted.findIndex((c) => c.car_id === rivalId))
            : "#666";
          const compoundColor = getCompoundColor(belief.compound || "MEDIUM");
          const pitProb = (belief.pit_prob || 0) * 100;

          return (
            <div
              key={rivalId}
              className={`px-4 py-2 flex items-center gap-2 text-xs border-b border-pit-border/30
                         transition-colors duration-150
                         ${belief.undercut ? "bg-f1-red/5" : "hover:bg-white/[0.02]"}`}
            >
              {/* Rival ID */}
              <div className="w-14 flex items-center gap-1.5">
                <div className="w-0.5 h-3.5 rounded-full flex-shrink-0" style={{ backgroundColor: carColor }} />
                <span className="font-semibold text-white text-[11px] truncate">{rivalId}</span>
              </div>

              {/* Estimated compound */}
              <div className="w-8 flex justify-center">
                <span className="compound-dot" style={{ backgroundColor: compoundColor }} />
              </div>

              {/* Estimated tyre age */}
              <span className={`w-10 text-right tabular-nums font-mono text-[11px] ${
                (belief.age || 0) > 20 ? "text-red-400" :
                (belief.age || 0) > 12 ? "text-yellow-400" : "text-pit-muted"
              }`}>
                ~{Math.round(belief.age || 0)}
              </span>

              {/* Pit probability bar */}
              <div className="flex-1 flex items-center gap-1.5">
                <div className="flex-1 h-1.5 bg-pit-surface rounded-full overflow-hidden">
                  <div
                    className="h-full rounded-full transition-all duration-300"
                    style={{
                      width: `${Math.min(100, pitProb)}%`,
                      backgroundColor: pitProb > 70 ? "#e10600" :
                                       pitProb > 40 ? "#eab308" : "#3a3a3a",
                    }}
                  />
                </div>
                <span className="text-[10px] text-pit-muted tabular-nums font-mono w-8 text-right">
                  {pitProb.toFixed(0)}%
                </span>
              </div>

              {/* Confidence */}
              <span className={`w-8 text-right tabular-nums font-mono text-[10px] ${
                (belief.confidence || 0) > 0.7 ? "text-green-400" :
                (belief.confidence || 0) > 0.4 ? "text-pit-text" : "text-pit-muted"
              }`}>
                {((belief.confidence || 0) * 100).toFixed(0)}%
              </span>

              {/* Undercut indicator */}
              <div className="w-6 flex justify-center">
                {belief.undercut ? (
                  <span className="w-3.5 h-3.5 rounded-full bg-f1-red/20 flex items-center justify-center">
                    <span className="w-1.5 h-1.5 rounded-full bg-f1-red" />
                  </span>
                ) : (
                  <span className="text-pit-muted text-[10px]">-</span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Legend */}
      <div className="px-4 py-2 border-t border-pit-border flex items-center gap-4 text-[9px] text-pit-muted">
        <span>AGE = estimated tyre laps</span>
        <span>PIT PROB = 5-lap window</span>
        <span>UC = undercut opportunity</span>
      </div>
    </div>
  );
}
