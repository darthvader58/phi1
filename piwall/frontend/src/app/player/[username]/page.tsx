"use client";

import { useParams } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import { api } from "@/lib/api";
import { PlayerInfo, COMPOUND_COLORS } from "@/lib/types";

export default function PlayerPage() {
  const params = useParams();
  const username = params.username as string;
  const [player, setPlayer] = useState<PlayerInfo | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    api.getPlayer(username)
      .then(setPlayer)
      .catch((e) => setError(e.message));
  }, [username]);

  if (error) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-10">
        <p className="text-f1-red text-sm">Error: {error}</p>
      </div>
    );
  }

  if (!player) {
    return (
      <div className="max-w-4xl mx-auto px-4 py-10">
        <div className="text-pit-muted text-sm animate-pulse-slow">Loading profile...</div>
      </div>
    );
  }

  return (
    <div className="max-w-5xl mx-auto px-4 py-10">
      {/* Header */}
      <div className="mb-8 flex items-end justify-between flex-wrap gap-4">
        <div>
          <div className="flex items-center gap-3 mb-1">
            <div className="w-1.5 h-8 rounded-full bg-f1-red" />
            <h1 className="text-3xl font-extrabold text-white tracking-tight">{player.username}</h1>
          </div>
          <p className="text-sm text-pit-text ml-5">{player.team}</p>
        </div>
        <div className="flex items-center gap-6">
          <div className="text-center">
            <div className="stat-value text-2xl">{player.elo}</div>
            <div className="text-[10px] text-pit-muted uppercase tracking-wider">ELO</div>
          </div>
        </div>
      </div>

      {/* Stats row */}
      <div className="grid grid-cols-2 md:grid-cols-5 gap-3 mb-6">
        {[
          { label: "Races", value: player.stats.total_races },
          { label: "Wins", value: player.stats.wins },
          { label: "Podiums", value: player.stats.podiums },
          { label: "DNFs", value: player.stats.dnfs },
          { label: "Win Rate", value: `${player.stats.win_rate}%` },
        ].map((stat) => (
          <div key={stat.label} className="card p-4 text-center">
            <div className="stat-value text-xl">{stat.value}</div>
            <div className="text-[10px] text-pit-muted uppercase tracking-wider mt-1">{stat.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* ELO history chart */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border">
            <span className="section-label">ELO Progression</span>
          </div>
          <div className="p-4">
            {player.elo_history.length === 0 ? (
              <p className="text-pit-muted text-sm text-center py-8">No ELO history yet.</p>
            ) : (
              <EloChart history={player.elo_history} />
            )}
          </div>
        </div>

        {/* Recent races */}
        <div className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
            <span className="section-label">Recent Races</span>
            <span className="text-[10px] text-pit-muted font-mono">{player.recent_races.length} races</span>
          </div>
          <div className="px-3 py-2 max-h-[360px] overflow-y-auto space-y-1">
            {player.recent_races.length === 0 ? (
              <p className="text-pit-muted text-sm text-center py-8">No races yet.</p>
            ) : (
              player.recent_races.map((race, idx) => (
                <div key={idx} className="flex items-center gap-3 px-3 py-2.5 rounded-lg hover:bg-white/[0.02] transition-colors">
                  <span className={`w-10 text-right font-extrabold tabular-nums text-sm ${
                    race.position === 1 ? "text-f1-red" :
                    race.position <= 3 ? "text-white" : "text-pit-muted"
                  }`}>
                    {race.retired ? "DNF" : `P${race.position}`}
                  </span>
                  <div className="flex-1 min-w-0">
                    <span className="text-white text-sm font-semibold">
                      {race.track.charAt(0).toUpperCase() + race.track.slice(1)}
                    </span>
                    <div className="flex items-center gap-1.5 mt-0.5">
                      {race.compounds_used.map((c, ci) => (
                        <span key={ci} className="compound-dot" style={{ backgroundColor: COMPOUND_COLORS[c] || "#888" }} />
                      ))}
                      <span className="text-[10px] text-pit-muted ml-1">{race.pit_count} stop{race.pit_count !== 1 ? "s" : ""}</span>
                    </div>
                  </div>
                  <div className="text-right">
                    <span className={`text-xs font-bold ${race.points > 0 ? "text-white" : "text-pit-muted"}`}>
                      {race.points > 0 ? `+${race.points}` : "0"} pts
                    </span>
                    <div className={`text-[9px] font-bold px-1.5 py-0.5 rounded mt-0.5 inline-block ${
                      race.race_type === "season" ? "bg-f1-red/10 text-f1-red" : "bg-pit-surface text-pit-muted"
                    }`}>
                      {race.race_type.toUpperCase()}
                    </div>
                  </div>
                </div>
              ))
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

function EloChart({ history }: { history: PlayerInfo["elo_history"] }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const width = 400;
  const height = 200;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || history.length === 0) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    // Background
    ctx.fillStyle = "#161616";
    ctx.fillRect(0, 0, width, height);

    const padding = { top: 20, right: 16, bottom: 24, left: 44 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    // Build data points: start with elo_before of first entry, then all elo_after
    const points = [history[0].elo_before, ...history.map(h => h.elo_after)];
    const minElo = Math.floor(Math.min(...points) / 10) * 10 - 10;
    const maxElo = Math.ceil(Math.max(...points) / 10) * 10 + 10;
    const eloRange = maxElo - minElo || 1;

    const xScale = (i: number) => padding.left + (i / (points.length - 1 || 1)) * plotW;
    const yScale = (elo: number) => padding.top + (1 - (elo - minElo) / eloRange) * plotH;

    // Grid lines
    ctx.strokeStyle = "#1f1f1f";
    ctx.lineWidth = 0.5;
    const gridStep = Math.max(10, Math.round(eloRange / 5 / 10) * 10);
    for (let e = Math.ceil(minElo / gridStep) * gridStep; e <= maxElo; e += gridStep) {
      const y = yScale(e);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();

      ctx.fillStyle = "#3a3a3a";
      ctx.font = "10px Inter, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`${e}`, padding.left - 6, y + 3);
    }

    // 1200 baseline
    const baseline = yScale(1200);
    if (baseline > padding.top && baseline < padding.top + plotH) {
      ctx.strokeStyle = "#2a2a2a";
      ctx.lineWidth = 1;
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(padding.left, baseline);
      ctx.lineTo(width - padding.right, baseline);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    // Gradient fill under line
    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
    gradient.addColorStop(0, "rgba(225, 6, 0, 0.15)");
    gradient.addColorStop(1, "rgba(225, 6, 0, 0)");

    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(xScale(0), yScale(points[0]));
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(xScale(i), yScale(points[i]));
    }
    ctx.lineTo(xScale(points.length - 1), padding.top + plotH);
    ctx.lineTo(xScale(0), padding.top + plotH);
    ctx.closePath();
    ctx.fill();

    // Line
    ctx.strokeStyle = "#e10600";
    ctx.lineWidth = 2;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(xScale(0), yScale(points[0]));
    for (let i = 1; i < points.length; i++) {
      ctx.lineTo(xScale(i), yScale(points[i]));
    }
    ctx.stroke();

    // End dot
    const lastX = xScale(points.length - 1);
    const lastY = yScale(points[points.length - 1]);
    ctx.fillStyle = "#e10600";
    ctx.beginPath();
    ctx.arc(lastX, lastY, 4, 0, Math.PI * 2);
    ctx.fill();

    // Current ELO label
    ctx.fillStyle = "#e10600";
    ctx.font = "bold 11px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`${Math.round(points[points.length - 1])}`, lastX + 8, lastY + 4);

    // Bottom labels
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "10px Inter, sans-serif";
    ctx.textAlign = "center";
    ctx.fillText("Race 1", xScale(0), height - 4);
    if (points.length > 1) {
      ctx.fillText(`Race ${points.length - 1}`, xScale(points.length - 1), height - 4);
    }

    // Title
    ctx.fillStyle = "#3a3a3a";
    ctx.font = "bold 10px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("ELO RATING", padding.left, 12);
  }, [history]);

  return (
    <canvas
      ref={canvasRef}
      style={{ width, height }}
      className="w-full"
    />
  );
}
