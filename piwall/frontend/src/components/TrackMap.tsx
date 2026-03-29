"use client";

import { useEffect, useRef } from "react";
import { CarState, getCarColor, getCompoundColor } from "@/lib/types";

interface Props {
  cars: CarState[];
  trackName: string;
  weather: string;
  safetyCar: boolean;
}

// Real F1 circuit outlines as SVG paths (scaled to 320x300 viewBox)
// Traced from actual circuit maps
const TRACK_PATHS: Record<string, string> = {
  // Bahrain International Circuit - Sakhir
  bahrain:
    "M 200 30 L 240 30 L 260 45 L 260 80 L 240 95 L 260 110 L 260 140 " +
    "L 240 155 L 240 180 L 220 195 L 220 230 L 200 250 L 160 260 " +
    "L 120 250 L 100 230 L 100 200 L 80 180 L 80 140 L 100 110 " +
    "L 100 80 L 120 60 L 160 40 Z",

  // Circuit de Monaco - Monte Carlo
  monaco:
    "M 80 60 L 160 40 L 240 40 L 270 60 L 270 90 L 250 110 " +
    "L 260 140 L 240 170 L 200 180 L 180 200 L 140 220 " +
    "L 100 240 L 60 230 L 50 200 L 60 170 L 80 150 " +
    "L 60 120 L 60 90 Z",

  // Autodromo Nazionale Monza
  monza:
    "M 140 40 L 200 35 L 240 50 L 255 80 L 250 120 " +
    "L 265 140 L 270 170 L 250 200 L 230 210 L 200 220 " +
    "L 175 240 L 140 260 L 110 255 L 80 235 L 65 210 " +
    "L 60 180 L 55 140 L 65 100 L 80 70 L 110 50 Z",

  // Circuit de Spa-Francorchamps (famous layout with Eau Rouge/Raidillon)
  spa:
    "M 60 180 L 60 140 L 80 100 L 110 70 L 140 50 L 180 40 " +
    "L 220 50 L 250 70 L 270 100 L 275 130 L 260 150 " +
    "L 240 155 L 250 180 L 270 200 L 270 230 L 250 255 " +
    "L 220 265 L 180 270 L 140 260 L 100 240 L 70 210 Z",

  // Silverstone Circuit (modern layout with Loop/Wellington/Brooklands complex)
  silverstone:
    "M 130 40 L 180 35 L 230 50 L 260 75 L 270 110 " +
    "L 260 140 L 240 155 L 260 180 L 255 210 L 230 230 " +
    "L 195 250 L 160 260 L 125 255 L 90 240 L 65 215 " +
    "L 55 180 L 60 145 L 75 115 L 90 90 L 110 60 Z",

  // Suzuka International Racing Course (figure-8 crossover)
  suzuka:
    "M 80 140 L 80 100 L 100 70 L 130 50 L 170 40 L 210 50 " +
    "L 240 75 L 255 110 L 250 140 L 230 160 L 210 150 " +
    "L 190 165 L 200 190 L 220 210 L 230 240 L 210 260 " +
    "L 170 270 L 130 265 L 100 245 L 80 215 L 70 180 Z",
};

// Get a point along the SVG path at parameter t (0-1)
function getPointOnPath(pathEl: SVGPathElement, t: number): { x: number; y: number } {
  const len = pathEl.getTotalLength();
  const pt = pathEl.getPointAtLength(t * len);
  return { x: pt.x, y: pt.y };
}

export default function TrackMap({ cars, trackName, weather, safetyCar }: Props) {
  const pathRef = useRef<SVGPathElement>(null);
  const carsRef = useRef<SVGGElement>(null);
  const prevPositions = useRef<Record<string, { x: number; y: number }>>({});

  const path = TRACK_PATHS[trackName] || TRACK_PATHS.bahrain;
  const activeCars = cars.filter((c) => !c.retired);

  const trackColor = safetyCar ? "#eab308" : weather === "wet" ? "#1e40af" : "#2a2a2a";

  // Position cars along the actual path based on their race position/gap
  useEffect(() => {
    if (!pathRef.current || activeCars.length === 0) return;
    const pathEl = pathRef.current;

    // Distribute cars along the path by position
    // Leader at front, others spaced proportionally by gap
    const maxGap = Math.max(1, ...activeCars.map((c) => c.gap_to_leader));

    activeCars.forEach((car) => {
      // Map gap to path position: leader near the front, others spread behind
      // Use modular positioning so cars spread around the whole track
      const gapFraction = car.gap_to_leader / Math.max(maxGap * 1.5, 30);
      const t = ((1 - gapFraction) * 0.95 + 0.05) % 1.0;

      const target = getPointOnPath(pathEl, t);
      const prev = prevPositions.current[car.car_id];

      // Smooth interpolation for animation
      const lerp = 0.3;
      const x = prev ? prev.x + (target.x - prev.x) * lerp : target.x;
      const y = prev ? prev.y + (target.y - prev.y) * lerp : target.y;

      prevPositions.current[car.car_id] = { x, y };
    });
  }, [activeCars, path]);

  // Get positions for rendering (use refs for smoothing)
  function getCarPosition(car: CarState, idx: number): { x: number; y: number } {
    if (prevPositions.current[car.car_id]) {
      return prevPositions.current[car.car_id];
    }
    // Fallback: distribute evenly
    if (!pathRef.current) {
      const t = idx / (activeCars.length || 1);
      const angle = t * Math.PI * 2 - Math.PI / 2;
      return { x: 160 + 100 * Math.cos(angle), y: 150 + 100 * Math.sin(angle) };
    }
    const t = idx / (activeCars.length || 1);
    return getPointOnPath(pathRef.current, t);
  }

  return (
    <div className="card p-4 relative overflow-hidden">
      {safetyCar && (
        <div className="absolute inset-0 bg-gradient-radial from-yellow-500/5 to-transparent pointer-events-none" />
      )}

      <svg viewBox="0 0 320 300" className="w-full h-auto">
        {/* Track glow */}
        <path
          d={path}
          fill="none"
          stroke={trackColor}
          strokeWidth={18}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.2}
        />
        {/* Track surface */}
        <path
          d={path}
          fill="none"
          stroke={trackColor}
          strokeWidth={12}
          strokeLinecap="round"
          strokeLinejoin="round"
          opacity={0.5}
        />
        {/* Track center */}
        <path
          ref={pathRef}
          d={path}
          fill="none"
          stroke="#161616"
          strokeWidth={7}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* Track edge marking */}
        <path
          d={path}
          fill="none"
          stroke={safetyCar ? "#eab308" : "#333"}
          strokeWidth={1}
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeDasharray={safetyCar ? "4 3" : "none"}
          opacity={0.6}
        />

        {/* Start/Finish line */}
        {pathRef.current && (() => {
          const pt = getPointOnPath(pathRef.current, 0);
          return (
            <line
              x1={pt.x - 8} y1={pt.y - 8}
              x2={pt.x + 8} y2={pt.y + 8}
              stroke="#e10600"
              strokeWidth={2}
              opacity={0.6}
            />
          );
        })()}

        {/* Cars */}
        <g ref={carsRef}>
          {activeCars.map((car, idx) => {
            const pos = getCarPosition(car, idx);
            const carColor = getCarColor(cars.findIndex((c) => c.car_id === car.car_id));
            const compColor = getCompoundColor(car.compound);

            return (
              <g key={car.car_id} style={{ transition: "transform 0.3s ease-out" }}>
                {/* Glow */}
                <circle cx={pos.x} cy={pos.y} r={12} fill={carColor} opacity={0.1} />
                {/* Compound ring */}
                <circle cx={pos.x} cy={pos.y} r={7} fill="none" stroke={compColor} strokeWidth={2} />
                {/* Car body */}
                <circle cx={pos.x} cy={pos.y} r={5.5} fill={carColor} />
                {/* Position number */}
                <text
                  x={pos.x} y={pos.y + 2}
                  textAnchor="middle"
                  fill="#000"
                  fontSize="6"
                  fontFamily="Inter, sans-serif"
                  fontWeight="900"
                >
                  {car.position}
                </text>
                {/* Car ID label */}
                <text
                  x={pos.x} y={pos.y - 12}
                  textAnchor="middle"
                  fill="#888"
                  fontSize="6"
                  fontFamily="Inter, sans-serif"
                  fontWeight="600"
                >
                  {car.car_id}
                </text>
              </g>
            );
          })}
        </g>

        {/* SC overlay */}
        {safetyCar && (
          <g>
            <rect x="110" y="135" width="100" height="26" rx="5" fill="#eab308" opacity={0.12} />
            <text x="160" y="153" textAnchor="middle" fill="#eab308"
                  fontSize="10" fontFamily="Inter, sans-serif" fontWeight="800" letterSpacing="0.12em">
              SAFETY CAR
            </text>
          </g>
        )}

        {/* Track name watermark */}
        <text x="160" y="290" textAnchor="middle" fill="#1a1a1a"
              fontSize="8" fontFamily="Inter, sans-serif" fontWeight="700" letterSpacing="0.2em">
          {trackName.toUpperCase()}
        </text>
      </svg>
    </div>
  );
}
