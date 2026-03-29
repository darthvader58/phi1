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
// ~16px long, ~8px wide — will be scaled
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

// Front wing
const FRONT_WING: [number, number][] = [
  [6, -4.5], [8, -4.5], [8, 4.5], [6, 4.5],
];

// Rear wing
const REAR_WING: [number, number][] = [
  [-8, -5], [-7, -5], [-7, 5], [-8, 5],
];

export default function TrackMap({ cars, trackName, weather, safetyCar }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const trackPathRef = useRef<TrackPath | null>(null);
  const animFrameRef = useRef<number>(0);
  const prevPositions = useRef<Record<string, { x: number; y: number; angle: number }>>({});
  const carsRef = useRef<CarState[]>(cars);
  const propsRef = useRef({ weather, safetyCar, trackName });

  carsRef.current = cars;
  propsRef.current = { weather, safetyCar, trackName };

  // Build track path when track changes
  useEffect(() => {
    const def = TRACK_DEFS[trackName] || TRACK_DEFS.bahrain;
    trackPathRef.current = buildTrackPath(def.points);
    prevPositions.current = {};
  }, [trackName]);

  // Rendering
  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = canvas?.getContext("2d");
    const trackPath = trackPathRef.current;
    if (!canvas || !ctx || !trackPath) return;

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

    // Clear
    ctx.clearRect(0, 0, W, H);

    // Background
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
    ctx.lineWidth = 22 * scale;
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
    ctx.lineWidth = 14 * scale;
    ctx.stroke();

    // ── Racing line (center stripe) ──
    ctx.beginPath();
    ctx.moveTo(tx(pts[0][0]), ty(pts[0][1]));
    for (let i = 1; i < pts.length; i++) {
      ctx.lineTo(tx(pts[i][0]), ty(pts[i][1]));
    }
    ctx.closePath();
    ctx.strokeStyle = sc ? "#4a4020" : "#1e1e22";
    ctx.lineWidth = 8 * scale;
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
    ctx.lineWidth = 1 * scale;
    ctx.stroke();
    ctx.setLineDash([]);

    // ── Start/Finish line ──
    const sfPoint = getPointAtFraction(trackPath, 0);
    const sfAngle = sfPoint.angle + Math.PI / 2;
    const sfLen = 9 * scale;
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
    const activeCars = carsRef.current.filter((c) => !c.retired);
    const maxGap = Math.max(1, ...activeCars.map((c) => c.gap_to_leader));

    activeCars.forEach((car) => {
      const globalIdx = carsRef.current.findIndex((c) => c.car_id === car.car_id);
      const color = getCarColor(globalIdx);
      const compColor = getCompoundColor(car.compound);

      // Map gap to track fraction
      const gapFrac = car.gap_to_leader / Math.max(maxGap * 1.5, 30);
      const targetT = ((1 - gapFrac) * 0.95 + 0.02) % 1.0;

      const target = getPointAtFraction(trackPath, targetT);
      const prev = prevPositions.current[car.car_id];

      // Smooth interpolation
      const lerp = 0.18;
      let x: number, y: number, angle: number;
      if (prev) {
        x = prev.x + (target.x - prev.x) * lerp;
        y = prev.y + (target.y - prev.y) * lerp;
        // Angle interpolation (handle wrap-around)
        let da = target.angle - prev.angle;
        if (da > Math.PI) da -= 2 * Math.PI;
        if (da < -Math.PI) da += 2 * Math.PI;
        angle = prev.angle + da * lerp;
      } else {
        x = target.x;
        y = target.y;
        angle = target.angle;
      }
      prevPositions.current[car.car_id] = { x, y, angle };

      const cx = tx(x);
      const cy = ty(y);
      const carScale = scale * 1.1;

      ctx.save();
      ctx.translate(cx, cy);
      ctx.rotate(angle);

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

      // Position number label (above car)
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

    const loop = () => {
      if (!running) return;
      draw();
      animFrameRef.current = requestAnimationFrame(loop);
    };

    loop();

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
        style={{ aspectRatio: "800 / 520" }}
      />
    </div>
  );
}
