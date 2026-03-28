"use client";

import { useEffect, useRef } from "react";
import { LapSnapshot, getCarColor } from "@/lib/types";

interface Props {
  lapHistory: LapSnapshot[];
  width?: number;
  height?: number;
}

export default function GapChart({ lapHistory, width = 600, height = 250 }: Props) {
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

    // Clear
    ctx.fillStyle = "#111827";
    ctx.fillRect(0, 0, width, height);

    const padding = { top: 20, right: 80, bottom: 30, left: 50 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    // Gather car IDs from first lap
    const carIds = lapHistory[0].cars
      .filter((c) => !c.retired)
      .map((c) => c.car_id);

    // Build gap data per car
    const maxLap = lapHistory[lapHistory.length - 1].lap;
    const gapData: Record<string, { lap: number; gap: number }[]> = {};

    for (const id of carIds) {
      gapData[id] = [];
    }

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

    // Scale functions
    const xScale = (lap: number) => padding.left + (lap / maxLap) * plotW;
    const yScale = (gap: number) => padding.top + (gap / maxGap) * plotH;

    // Grid lines
    ctx.strokeStyle = "#1f2937";
    ctx.lineWidth = 0.5;
    for (let g = 0; g <= maxGap; g += 5) {
      const y = yScale(g);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
    }

    // Axes labels
    ctx.fillStyle = "#6b7280";
    ctx.font = "10px monospace";
    ctx.textAlign = "right";
    for (let g = 0; g <= maxGap; g += 5) {
      ctx.fillText(`${g}s`, padding.left - 5, yScale(g) + 3);
    }
    ctx.textAlign = "center";
    const lapStep = Math.max(1, Math.floor(maxLap / 10));
    for (let l = 0; l <= maxLap; l += lapStep) {
      ctx.fillText(`${l}`, xScale(l), height - padding.bottom + 15);
    }
    ctx.fillText("Lap", width / 2, height - 5);

    // Draw lines
    carIds.forEach((id, idx) => {
      const points = gapData[id];
      if (points.length < 2) return;

      ctx.strokeStyle = getCarColor(idx);
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(xScale(points[0].lap), yScale(points[0].gap));
      for (let i = 1; i < points.length; i++) {
        ctx.lineTo(xScale(points[i].lap), yScale(points[i].gap));
      }
      ctx.stroke();

      // Label at end
      const last = points[points.length - 1];
      ctx.fillStyle = getCarColor(idx);
      ctx.font = "9px monospace";
      ctx.textAlign = "left";
      ctx.fillText(id, xScale(last.lap) + 4, yScale(last.gap) + 3);
    });

    // Title
    ctx.fillStyle = "#9ca3af";
    ctx.font = "11px monospace";
    ctx.textAlign = "left";
    ctx.fillText("Gap to Leader", padding.left, 14);
  }, [lapHistory, width, height]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
      className="rounded-lg border border-gray-700"
    />
  );
}
