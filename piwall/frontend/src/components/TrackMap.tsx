"use client";

import { useEffect, useRef, useCallback } from "react";
import { CarState, getCarColor, getCompoundColor, CAR_COLORS } from "@/lib/types";
import {
  TRACK_DEFS,
  buildTrackPath,
  getPointAtFraction,
  TrackPath,
} from "@/lib/trackData";

interface Props {
  cars: CarState[];
  trackName: string;
  weather: string;
  safetyCar: boolean;
}

// ─── F1 car polygon (top-down, pointing RIGHT at angle=0) ─────────
const CAR_BODY: [number, number][] = [
  [8, 0],    // nose tip
  [5, -2.2], // front right
  [3, -3],   // cockpit right
  [-2, -3.5],// sidepod right
  [-5, -3.8],// rear right widest
  [-7, -3],  // rear right
  [-8, -4],  // rear wing right
  [-8, 4],   // rear wing left
  [-7, 3],   // rear left
  [-5, 3.8], // rear left widest
  [-2, 3.5], // sidepod left
  [3, 3],    // cockpit left
  [5, 2.2],  // front left
];

const FRONT_WING: [number, number][] = [
  [6, -4.5], [8, -4.5], [8, 4.5], [6, 4.5],
];

const REAR_WING: [number, number][] = [
  [-8, -5], [-7, -5], [-7, 5], [-8, 5],
];

// ─── Per-car animation state ──────────────────────────────────────
interface CarAnimState {
  trackT: number;       // Current position on track [0, 1)
  targetGapFrac: number; // Target gap as track fraction
  renderX: number;
  renderY: number;
  renderAngle: number;
  initialized: boolean;
}

export default function TrackMap({ cars, trackName, weather, safetyCar }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trackPathRef = useRef<TrackPath | null>(null);
  const animFrameRef = useRef<number>(0);
  const lastFrameRef = useRef<number>(0);
  const carsRef = useRef<CarState[]>(cars);
  const propsRef = useRef({ weather, safetyCar, trackName });

  // Animation state per car — persists across renders
  const carAnimRef = useRef<Record<string, CarAnimState>>({});
  // Track the leader's trackT separately
  const leaderTRef = useRef<number>(0);

  carsRef.current = cars;
  propsRef.current = { weather, safetyCar, trackName };

  // Update target gaps when car data changes
  useEffect(() => {
    const activeCars = cars.filter((c) => !c.retired);
    const maxGap = Math.max(1, ...activeCars.map((c) => c.gap_to_leader));

    activeCars.forEach((car) => {
      if (!carAnimRef.current[car.car_id]) {
        // Initialize new car with spread-out starting positions
        const idx = activeCars.indexOf(car);
        carAnimRef.current[car.car_id] = {
          trackT: (1 - idx * 0.04) % 1.0,
          targetGapFrac: 0,
          renderX: 0,
          renderY: 0,
          renderAngle: 0,
          initialized: false,
        };
      }
      // Convert gap_to_leader to a track fraction (how far behind leader)
      // Normalize so max gap = ~0.4 of the track (visual spacing)
      const gapFrac = car.gap_to_leader / Math.max(maxGap * 2, 40);
      carAnimRef.current[car.car_id].targetGapFrac = Math.min(gapFrac, 0.45);
    });
  }, [cars]);

  // Build track path when track changes
  useEffect(() => {
    const def = TRACK_DEFS[trackName] || TRACK_DEFS.bahrain;
    trackPathRef.current = buildTrackPath(def.points);
    carAnimRef.current = {};
    leaderTRef.current = 0;
    lastFrameRef.current = 0;
  }, [trackName]);

  // Rendering
  const draw = useCallback((timestamp: number) => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const trackPath = trackPathRef.current;
    if (!canvas || !ctx || !trackPath) return;

    // Delta time calculation
    if (lastFrameRef.current === 0) lastFrameRef.current = timestamp;
    const dt = Math.min((timestamp - lastFrameRef.current) / 1000, 0.1); // cap at 100ms
    lastFrameRef.current = timestamp;

    const { weather: wx, safetyCar: sc } = propsRef.current;
    const dpr = typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1;
    const W = canvas.clientWidth;
    const H = canvas.clientHeight;

    if (canvas.width !== W * dpr || canvas.height !== H * dpr) {
      canvas.width = W * dpr;
      canvas.height = H * dpr;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // Scale factor: track data is 800×520, fit into canvas
    const scaleX = W / 800;
    const scaleY = H / 520;
    const scale = Math.min(scaleX, scaleY) * 0.92;
    const offsetX = (W - 800 * scale) / 2;
    const offsetY = (H - 520 * scale) / 2;

    const tx = (x: number) => x * scale + offsetX;
    const ty = (y: number) => y * scale + offsetY;

    // ── Advance car positions ──
    // All cars continuously drive forward around the track
    // Base speed: ~1 lap every 3 seconds for smooth visual motion
    const baseSpeed = 0.33; // track fraction per second
    const scSpeed = sc ? 0.20 : baseSpeed; // slower under safety car

    // Advance leader position
    leaderTRef.current = (leaderTRef.current + scSpeed * dt) % 1.0;
    const leaderT = leaderTRef.current;

    // Update each car's position
    const activeCars = carsRef.current.filter((c) => !c.retired);
    activeCars.forEach((car) => {
      const anim = carAnimRef.current[car.car_id];
      if (!anim) return;

      // Target position: leader position minus this car's gap fraction
      const targetT = ((leaderT - anim.targetGapFrac) % 1 + 1) % 1;

      if (!anim.initialized) {
        anim.trackT = targetT;
        anim.initialized = true;
      } else {
        // Smoothly converge toward target position along the track
        // Move forward at base speed, with correction to maintain gap
        let diff = targetT - anim.trackT;
        // Handle wrap-around: always take the shortest path forward
        if (diff < -0.5) diff += 1.0;
        if (diff > 0.5) diff -= 1.0;

        // Advance: base forward speed + correction toward target
        // Correction is gentle to avoid jerking
        const correction = diff * 2.0; // converge over ~0.5s
        const speed = scSpeed + correction;
        anim.trackT = ((anim.trackT + speed * dt) % 1 + 1) % 1;
      }

      // Get position on track — directly from trackT, no XY lerp
      // (XY lerp would cut corners and go off-track)
      const point = getPointAtFraction(trackPath, anim.trackT);
      anim.renderX = point.x;
      anim.renderY = point.y;
      anim.renderAngle = point.angle;
    });

    // ── Clear & Background ──
    ctx.clearRect(0, 0, W, H);
    const bgGrad = ctx.createRadialGradient(W / 2, H / 2, 0, W / 2, H / 2, W * 0.7);
    bgGrad.addColorStop(0, "#141414");
    bgGrad.addColorStop(1, "#0a0a0a");
    ctx.fillStyle = bgGrad;
    ctx.fillRect(0, 0, W, H);

    const pts = trackPath.points;

    // ── Track glow (outer) ──
    ctx.beginPath();
    ctx.moveTo(tx(pts[0][0]), ty(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(tx(pts[i][0]), ty(pts[i][1]));
    }
    ctx.closePath();
    ctx.strokeStyle = sc ? "rgba(234,179,8,0.12)" : wx === "wet" ? "rgba(30,64,175,0.15)" : "rgba(50,50,60,0.2)";
    ctx.lineWidth = 36 * scale;
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.stroke();

    // ── Track surface (asphalt) ──
    ctx.beginPath();
    ctx.moveTo(tx(pts[0][0]), ty(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(tx(pts[i][0]), ty(pts[i][1]));
    }
    ctx.closePath();
    ctx.strokeStyle = sc ? "#3d3520" : wx === "wet" ? "#1a2540" : "#2a2a2e";
    ctx.lineWidth = 24 * scale;
    ctx.stroke();

    // ── Racing line (center stripe) ──
    ctx.beginPath();
    ctx.moveTo(tx(pts[0][0]), ty(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(tx(pts[i][0]), ty(pts[i][1]));
    }
    ctx.closePath();
    ctx.strokeStyle = sc ? "#4a4020" : "#1e1e22";
    ctx.lineWidth = 14 * scale;
    ctx.stroke();

    // ── Track edge markings ──
    ctx.beginPath();
    ctx.moveTo(tx(pts[0][0]), ty(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(tx(pts[i][0]), ty(pts[i][1]));
    }
    ctx.closePath();
    if (sc) {
      ctx.strokeStyle = "rgba(234,179,8,0.4)";
      ctx.setLineDash([6 * scale, 4 * scale]);
    } else {
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.setLineDash([]);
    }
    ctx.lineWidth = 1.5 * scale;
    ctx.stroke();
    ctx.setLineDash([]);

    // ── Start/Finish line ──
    const sfPoint = getPointAtFraction(trackPath, 0);
    const sfAngle = sfPoint.angle + Math.PI / 2;
    const sfLen = 15 * scale;
    ctx.beginPath();
    ctx.moveTo(
      tx(sfPoint.x) - Math.cos(sfAngle) * sfLen,
      ty(sfPoint.y) - Math.sin(sfAngle) * sfLen,
    );
    ctx.lineTo(
      tx(sfPoint.x) + Math.cos(sfAngle) * sfLen,
      ty(sfPoint.y) + Math.sin(sfAngle) * sfLen,
    );
    ctx.strokeStyle = "#e10600";
    ctx.lineWidth = 2.5 * scale;
    ctx.stroke();

    // ── Draw cars ──
    activeCars.forEach((car) => {
      const anim = carAnimRef.current[car.car_id];
      if (!anim) return;

      const globalIdx = carsRef.current.findIndex((c) => c.car_id === car.car_id);
      const color = getCarColor(globalIdx);
      const compColor = getCompoundColor(car.compound);

      const cx = tx(anim.renderX);
      const cy = ty(anim.renderY);
      const carScale = scale * 1.5;

      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(anim.renderAngle);

      // Car shadow
      ctx.beginPath();
      ctx.moveTo(CAR_BODY[0][0] * carScale, CAR_BODY[0][1] * carScale);
      for (let i = 1; i < CAR_BODY.length; i++) {
        ctx.lineTo(CAR_BODY[i][0] * carScale, CAR_BODY[i][1] * carScale);
      }
      ctx.closePath();
      ctx.fillStyle = "rgba(0,0,0,0.5)";
      ctx.fill();

      // Front wing
      ctx.beginPath();
      ctx.moveTo(FRONT_WING[0][0] * carScale, FRONT_WING[0][1] * carScale);
      for (let i = 1; i < FRONT_WING.length; i++) {
        ctx.lineTo(FRONT_WING[i][0] * carScale, FRONT_WING[i][1] * carScale);
      }
      ctx.closePath();
      ctx.fillStyle = "#333";
      ctx.fill();

      // Rear wing
      ctx.beginPath();
      ctx.moveTo(REAR_WING[0][0] * carScale, REAR_WING[0][1] * carScale);
      for (let i = 1; i < REAR_WING.length; i++) {
        ctx.lineTo(REAR_WING[i][0] * carScale, REAR_WING[i][1] * carScale);
      }
      ctx.closePath();
      ctx.fillStyle = "#444";
      ctx.fill();

      // Car body (team color)
      ctx.beginPath();
      ctx.moveTo(CAR_BODY[0][0] * carScale, CAR_BODY[0][1] * carScale);
      for (let i = 1; i < CAR_BODY.length; i++) {
        ctx.lineTo(CAR_BODY[i][0] * carScale, CAR_BODY[i][1] * carScale);
      }
      ctx.closePath();
      ctx.fillStyle = color;
      ctx.fill();

      // Cockpit (dark ellipse)
      ctx.beginPath();
      ctx.ellipse(1 * carScale, 0, 2 * carScale, 1.5 * carScale, 0, 0, Math.PI * 2);
      ctx.fillStyle = "#111";
      ctx.fill();

      // Compound dot (on the nose)
      ctx.beginPath();
      ctx.arc(5 * carScale, 0, 1.5 * carScale, 0, Math.PI * 2);
      ctx.fillStyle = compColor;
      ctx.fill();

      ctx.restore();

      // Position label (above car)
      ctx.fillStyle = "#ccc";
      ctx.font = `${Math.max(8, 9 * scale)}px Inter, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "bottom";
      ctx.fillText(`P${car.position}`, cx, cy - 10 * scale);

      // Car ID label (below car)
      ctx.fillStyle = "#666";
      ctx.font = `${Math.max(6, 7 * scale)}px Inter, system-ui, sans-serif`;
      ctx.textBaseline = "top";
      ctx.fillText(car.car_id, cx, cy + 10 * scale);
    });

    // ── Safety Car overlay ──
    if (sc) {
      ctx.fillStyle = "rgba(234,179,8,0.08)";
      ctx.fillRect(W * 0.3, H * 0.42, W * 0.4, H * 0.12);
      ctx.strokeStyle = "rgba(234,179,8,0.3)";
      ctx.lineWidth = 1;
      ctx.strokeRect(W * 0.3, H * 0.42, W * 0.4, H * 0.12);
      ctx.fillStyle = "#eab308";
      ctx.font = `bold ${14 * scale}px Inter, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.letterSpacing = "2px";
      ctx.fillText("SAFETY CAR", W / 2, H * 0.48);
    }

    // ── Track name watermark ──
    const def = TRACK_DEFS[propsRef.current.trackName] || TRACK_DEFS.bahrain;
    ctx.fillStyle = "rgba(255,255,255,0.04)";
    ctx.font = `bold ${12 * scale}px Inter, system-ui, sans-serif`;
    ctx.textAlign = "center";
    ctx.textBaseline = "bottom";
    ctx.fillText(def.displayName.toUpperCase(), W / 2, H - 8 * scale);
  }, []);

  // Animation loop
  useEffect(() => {
    let running = true;

    const loop = (timestamp: number) => {
      if (!running) return;
      draw(timestamp);
      animFrameRef.current = requestAnimationFrame(loop);
    };

    animFrameRef.current = requestAnimationFrame(loop);

    return () => {
      running = false;
      cancelAnimationFrame(animFrameRef.current);
    };
  }, [draw]);

  return (
    <div className="card overflow-hidden relative">
      <canvas
        ref={canvasRef}
        className="w-full"
        style={{ aspectRatio: "2 / 1" }}
      />
    </div>
  );
}
