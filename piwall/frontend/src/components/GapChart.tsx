"use client";

import { useEffect, useRef } from "react";
import { LapSnapshot, getCarColor } from "@/lib/types";

interface Props {
  lapHistory: LapSnapshot[];
  width?: number;
  height?: number;
}

export default function GapChart({ lapHistory, width = 600, height = 220 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || lapHistory.length === 0) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    // Background
    ctx.fillStyle = "#161616";
    ctx.fillRect(0, 0, width, height);

    const padding = { top: 24, right: 72, bottom: 28, left: 44 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    const carIds = lapHistory[0].cars
      .filter((c) => !c.retired)
      .map((c) => c.car_id);

    const maxLap = lapHistory[lapHistory.length - 1].lap;
    const gapData: Record<string, { lap: number; gap: number }[]> = {};
    for (const id of carIds) gapData[id] = [];

    let maxGap = 5;
    for (const snap of lapHistory) {
      for (const car of snap.cars) {
        if (car.car_id in gapData && !car.retired) {
          gapData[car.car_id].push({ lap: snap.lap, gap: car.gap_to_leader });
          if (car.gap_to_leader > maxGap) maxGap = car.gap_to_leader;
        }
      }
    }
    maxGap = Math.ceil(maxGap / 5) * 5;

    const xScale = (lap: number) => padding.left + (lap / maxLap) * plotW;
    const yScale = (gap: number) => padding.top + (gap / maxGap) * plotH;

    // Grid
    ctx.strokeStyle = "#1f1f1f";
    ctx.lineWidth = 0.5;
    for (let g = 0; g <= maxGap; g += 5) {
      const y = yScale(g);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
    }

    // Axis labels
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "10px Inter, sans-serif";
    ctx.textAlign = "right";
    for (let g = 0; g <= maxGap; g += 5) {
      ctx.fillText(`${g}s`, padding.left - 6, yScale(g) + 3);
    }
    ctx.textAlign = "center";
    const lapStep = Math.max(1, Math.floor(maxLap / 8));
    for (let l = 0; l <= maxLap; l += lapStep) {
      ctx.fillText(`${l}`, xScale(l), height - padding.bottom + 14);
    }

    // Lines
    carIds.forEach((id, idx) => {
      const points = gapData[id];
      if (points.length < 2) return;

      const color = getCarColor(idx);

      // Line
      ctx.strokeStyle = color;
      ctx.lineWidth = 1.5;
      ctx.lineJoin = "round";
      ctx.beginPath();
      ctx.moveTo(xScale(points[0].lap), yScale(points[0].gap));
      for (let i = 1; i < points.length; i++) {
        ctx.lineTo(xScale(points[i].lap), yScale(points[i].gap));
      }
      ctx.stroke();

      // End dot
      const last = points[points.length - 1];
      ctx.fillStyle = color;
      ctx.beginPath();
      ctx.arc(xScale(last.lap), yScale(last.gap), 3, 0, Math.PI * 2);
      ctx.fill();

      // Label
      ctx.fillStyle = color;
      ctx.font = "bold 9px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(id, xScale(last.lap) + 6, yScale(last.gap) + 3);
    });

    // Title
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "bold 10px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("GAP TO LEADER", padding.left, 14);
  }, [lapHistory, width, height]);

  return (
    <div className="card overflow-hidden">
      <canvas
        ref={canvasRef}
        style={{ width, height }}
        className="w-full"
      />
    </div>
  );
}
