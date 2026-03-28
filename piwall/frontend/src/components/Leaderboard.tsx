"use client";

import { CarState, getCompoundColor, getCarColor } from "@/lib/types";

interface Props {
  cars: CarState[];
  highlightCarId?: string;
}

export default function Leaderboard({ cars, highlightCarId }: Props) {
  const sorted = [...cars].sort((a, b) => {
    if (a.retired && !b.retired) return 1;
    if (!a.retired && b.retired) return -1;
    return a.position - b.position;
  });

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden">
      <div className="px-3 py-2 bg-gray-800 border-b border-gray-700">
        <h3 className="text-sm font-mono font-bold text-gray-300 uppercase tracking-wider">
          Standings
        </h3>
      </div>
      <div className="divide-y divide-gray-800">
        {sorted.map((car, idx) => {
          const isHighlight = car.car_id === highlightCarId;
          const compoundColor = getCompoundColor(car.compound);
          const carColor = getCarColor(idx);

          return (
            <div
              key={car.car_id}
              className={`px-3 py-1.5 flex items-center gap-2 text-xs font-mono ${
                isHighlight ? "bg-gray-800/80" : ""
              } ${car.retired ? "opacity-40" : ""}`}
            >
              {/* Position */}
              <span className="w-6 text-right text-gray-500 font-bold">
                {car.retired ? "RET" : `P${car.position}`}
              </span>

              {/* Car color dot */}
              <span
                className="w-2 h-2 rounded-full flex-shrink-0"
                style={{ backgroundColor: carColor }}
              />

              {/* Car ID */}
              <span className="w-14 font-bold text-gray-200 truncate">
                {car.car_id}
              </span>

              {/* Gap */}
              <span className="w-16 text-right text-gray-400">
                {car.position === 1 && !car.retired
                  ? "LEADER"
                  : car.retired
                  ? "DNF"
                  : `+${car.gap_to_leader.toFixed(1)}s`}
              </span>

              {/* Compound badge */}
              <span
                className="w-4 h-4 rounded-sm flex items-center justify-center text-[10px] font-bold"
                style={{ backgroundColor: compoundColor, color: "#000" }}
                title={car.compound}
              >
                {car.compound[0]}
              </span>

              {/* Tyre age */}
              <span className="w-8 text-right text-gray-500">
                L{car.tyre_age}
              </span>

              {/* Pit count */}
              <span className="w-6 text-right text-gray-600">
                {car.pit_count > 0 ? `${car.pit_count}s` : "-"}
              </span>

              {/* DRS indicator */}
              {car.drs_available && (
                <span className="text-green-400 text-[10px] font-bold">DRS</span>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
