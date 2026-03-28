"use client";

import { CarState, getCarColor, getCompoundColor } from "@/lib/types";

interface Props {
  cars: CarState[];
  trackName: string;
  weather: string;
  safetyCar: boolean;
}

// Simplified SVG track outlines (oval approximation with characteristic shapes)
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

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      <svg viewBox="0 0 320 300" className="w-full h-auto">
        {/* Track surface */}
        <path
          d={path}
          fill="none"
          stroke={safetyCar ? "#eab308" : weather === "wet" ? "#1e40af" : "#374151"}
          strokeWidth={safetyCar ? 14 : 12}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        <path
          d={path}
          fill="none"
          stroke={weather === "wet" ? "#1e3a5f" : "#1f2937"}
          strokeWidth={8}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {/* Car dots positioned along the track */}
        {activeCars.map((car, idx) => {
          // Distribute cars along the track path proportionally to position
          const t = idx / totalCars;
          // Simple parametric position on an ellipse approximation
          const angle = t * Math.PI * 2 - Math.PI / 2;
          const cx = 160 + 100 * Math.cos(angle);
          const cy = 150 + 100 * Math.sin(angle);

          return (
            <g key={car.car_id}>
              {/* Car dot */}
              <circle
                cx={cx}
                cy={cy}
                r={8}
                fill={getCarColor(idx)}
                stroke={getCompoundColor(car.compound)}
                strokeWidth={2.5}
              />
              {/* Car label */}
              <text
                x={cx}
                y={cy - 12}
                textAnchor="middle"
                fill="#d1d5db"
                fontSize="8"
                fontFamily="monospace"
                fontWeight="bold"
              >
                {car.car_id}
              </text>
              {/* Position label */}
              <text
                x={cx}
                y={cy + 3}
                textAnchor="middle"
                fill="#000"
                fontSize="7"
                fontFamily="monospace"
                fontWeight="bold"
              >
                {car.position}
              </text>
            </g>
          );
        })}

        {/* Safety car indicator */}
        {safetyCar && (
          <text
            x="160"
            y="155"
            textAnchor="middle"
            fill="#eab308"
            fontSize="16"
            fontFamily="monospace"
            fontWeight="bold"
          >
            SAFETY CAR
          </text>
        )}

        {/* Weather badge */}
        <text
          x="160"
          y="290"
          textAnchor="middle"
          fill={weather === "dry" ? "#6b7280" : "#60a5fa"}
          fontSize="10"
          fontFamily="monospace"
        >
          {weather.toUpperCase()}
        </text>
      </svg>
    </div>
  );
}
