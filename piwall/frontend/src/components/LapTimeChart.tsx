"use client";

import { useEffect, useRef } from "react";
import { LapSnapshot, getCarColor } from "@/lib/types";

interface Props {
  lapHistory: LapSnapshot[];
  width?: number;
  height?: number;
}

export default function LapTimeChart({ lapHistory, width = 600, height = 220 }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || lapHistory.length < 2) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#161616";
    ctx.fillRect(0, 0, width, height);

    const padding = { top: 24, right: 72, bottom: 28, left: 52 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    const carIds = lapHistory[0].cars
      .filter((c) => !c.retired)
      .map((c) => c.car_id);

    const maxLap = lapHistory[lapHistory.length - 1].lap;

    // Collect lap times per car
    const lapTimes: Record<string, { lap: number; time: number }[]> = {};
    for (const id of carIds) lapTimes[id] = [];

    let minTime = Infinity;
    let maxTime = 0;

    for (let i = 1; i < lapHistory.length; i++) {
      for (const car of lapHistory[i].cars) {
        if (car.car_id in lapTimes && !car.retired && car.last_lap_time > 0) {
          lapTimes[car.car_id].push({ lap: lapHistory[i].lap, time: car.last_lap_time });
          if (car.last_lap_time < minTime) minTime = car.last_lap_time;
          if (car.last_lap_time > maxTime) maxTime = car.last_lap_time;
        }
      }
    }

    if (minTime === Infinity) return;

    // Add padding to time range
    const timeRange = maxTime - minTime || 1;
    minTime = minTime - timeRange * 0.05;
    maxTime = maxTime + timeRange * 0.05;

    const xScale = (lap: number) => padding.left + (lap / maxLap) * plotW;
    const yScale = (t: number) => padding.top + ((t - minTime) / (maxTime - minTime)) * plotH;

    // Safety car periods (yellow bands)
    for (const snap of lapHistory) {
      if (snap.safety_car) {
        const x = xScale(snap.lap);
        ctx.fillStyle = "rgba(234, 179, 8, 0.06)";
        ctx.fillRect(x - plotW / maxLap / 2, padding.top, plotW / maxLap, plotH);
      }
    }

    // Grid
    ctx.strokeStyle = "#1f1f1f";
    ctx.lineWidth = 0.5;
    const timeStep = Math.max(0.5, Math.round((maxTime - minTime) / 5 * 2) / 2);
    for (let t = Math.ceil(minTime / timeStep) * timeStep; t <= maxTime; t += timeStep) {
      const y = yScale(t);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();
    }

    // Axis labels
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "10px Inter, sans-serif";
    ctx.textAlign = "right";
    for (let t = Math.ceil(minTime / timeStep) * timeStep; t <= maxTime; t += timeStep) {
      ctx.fillText(`${t.toFixed(1)}s`, padding.left - 6, yScale(t) + 3);
    }

    ctx.textAlign = "center";
    const lapStep = Math.max(1, Math.floor(maxLap / 8));
    for (let l = 0; l <= maxLap; l += lapStep) {
      ctx.fillText(`${l}`, xScale(l), height - padding.bottom + 14);
    }

    // Lines
    carIds.forEach((id, idx) => {
      const points = lapTimes[id];
      if (points.length < 2) return;

      const color = getCarColor(idx);

      ctx.strokeStyle = color;
      ctx.lineWidth = 1.2;
      ctx.lineJoin = "round";
      ctx.globalAlpha = 0.8;
      ctx.beginPath();
      ctx.moveTo(xScale(points[0].lap), yScale(points[0].time));
      for (let i = 1; i < points.length; i++) {
        ctx.lineTo(xScale(points[i].lap), yScale(points[i].time));
      }
      ctx.stroke();
      ctx.globalAlpha = 1;

      // End label
      const last = points[points.length - 1];
      ctx.fillStyle = color;
      ctx.font = "bold 9px Inter, sans-serif";
      ctx.textAlign = "left";
      ctx.fillText(id, xScale(last.lap) + 6, yScale(last.time) + 3);
    });

    // Weather strip at bottom
    const stripH = 3;
    const stripY = padding.top + plotH + 1;
    for (const snap of lapHistory) {
      const x = xScale(snap.lap);
      const w = plotW / maxLap;
      ctx.fillStyle = snap.weather === "wet" ? "#1e40af" :
                      snap.weather === "damp" ? "#155e75" :
                      snap.weather === "drying" ? "#065f46" : "transparent";
      if (snap.weather !== "dry") {
        ctx.fillRect(x - w / 2, stripY, w, stripH);
      }
    }

    // Title
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "bold 10px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("LAP TIMES", padding.left, 14);
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
