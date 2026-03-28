"use client";

import { CarState, getCarColor, getCompoundColor } from "@/lib/types";

interface Props {
  cars: CarState[];
  trackName: string;
  weather: string;
  safetyCar: boolean;
}

const TRACK_PATHS: Record<string, string> = {
  bahrain:
    "M 150 40 C 250 40, 280 60, 280 100 L 280 200 C 280 220, 270 240, 250 250 L 200 270 C 180 280, 160 280, 140 270 L 80 230 C 60 220, 50 200, 50 180 L 50 100 C 50 60, 80 40, 150 40 Z",
  monaco:
    "M 100 50 L 250 50 C 270 50, 280 60, 280 80 L 280 140 L 240 180 L 200 180 L 160 220 C 140 240, 100 240, 80 220 L 50 160 C 40 140, 50 100, 60 80 L 100 50 Z",
  monza:
    "M 80 80 L 250 40 C 270 35, 285 50, 280 70 L 260 200 C 255 220, 240 235, 220 230 L 100 260 C 80 265, 60 250, 60 230 L 60 120 C 60 100, 65 85, 80 80 Z",
  spa: "M 50 150 L 100 50 L 180 30 L 260 60 L 280 120 L 250 180 L 200 200 L 180 260 L 120 280 L 60 240 L 50 150 Z",
  silverstone:
    "M 100 40 L 220 40 C 260 40, 280 60, 280 100 L 270 160 L 230 200 L 180 220 L 120 260 C 80 270, 50 250, 40 220 L 40 120 C 40 60, 60 40, 100 40 Z",
  suzuka:
    "M 100 80 C 120 40, 180 30, 220 50 L 270 100 C 285 120, 280 150, 260 170 L 200 200 L 160 170 L 140 200 L 100 250 C 70 270, 40 250, 40 220 L 50 140 C 55 100, 70 90, 100 80 Z",
};

export default function TrackMap({ cars, trackName, weather, safetyCar }: Props) {
  const path = TRACK_PATHS[trackName] || TRACK_PATHS.bahrain;
  const activeCars = cars.filter((c) => !c.retired);
  const totalCars = activeCars.length || 1;

  const trackColor = safetyCar ? "#eab308" : weather === "wet" ? "#1e40af" : "#2a2a2a";

  return (
    <div className="card p-4 relative overflow-hidden">
      {/* Background glow for SC */}
      {safetyCar && (
        <div className="absolute inset-0 bg-gradient-radial from-yellow-500/5 to-transparent pointer-events-none" />
      )}

      <svg viewBox="0 0 320 300" className="w-full h-auto">
        {/* Track outline glow */}
        <path
          d={path}
          fill="none"
          stroke={trackColor}
          strokeWidth={16}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.3}
        />
        {/* Track surface */}
        <path
          d={path}
          fill="none"
          stroke={trackColor}
          strokeWidth={10}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.7}
        />
        {/* Track center line */}
        <path
          d={path}
          fill="none"
          stroke="#161616"
          strokeWidth={6}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Cars */}
        {activeCars.map((car, idx) => {
          const t = idx / totalCars;
          const angle = t * Math.PI * 2 - Math.PI / 2;
          const cx = 160 + 100 * Math.cos(angle);
          const cy = 150 + 100 * Math.sin(angle);
          const carColor = getCarColor(idx);
          const compColor = getCompoundColor(car.compound);

          return (
            <g key={car.car_id}>
              {/* Glow */}
              <circle cx={cx} cy={cy} r={14} fill={carColor} opacity={0.12} />
              {/* Compound ring */}
              <circle cx={cx} cy={cy} r={8} fill="none" stroke={compColor} strokeWidth={2} />
              {/* Car dot */}
              <circle cx={cx} cy={cy} r={6} fill={carColor} />
              {/* Position */}
              <text
                x={cx} y={cy + 2.5}
                textAnchor="middle"
                fill="#000"
                fontSize="7"
                fontFamily="Inter, sans-serif"
                fontWeight="800"
              >
                {car.position}
              </text>
              {/* Label */}
              <text
                x={cx} y={cy - 14}
                textAnchor="middle"
                fill="#a0a0a0"
                fontSize="7"
                fontFamily="Inter, sans-serif"
                fontWeight="600"
              >
                {car.car_id}
              </text>
            </g>
          );
        })}

        {/* SC badge */}
        {safetyCar && (
          <g>
            <rect x="115" y="138" width="90" height="24" rx="4" fill="#eab308" opacity={0.15} />
            <text x="160" y="155" textAnchor="middle" fill="#eab308"
                  fontSize="11" fontFamily="Inter, sans-serif" fontWeight="800" letterSpacing="0.1em">
              SAFETY CAR
            </text>
          </g>
        )}
      </svg>
    </div>
  );
}
