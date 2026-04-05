"use client";

import { useEffect, useRef } from "react";

type DashboardData = {
  profile: {
    name: string;
    email: string;
    image: string | null;
    createdAt: string | null;
  } | null;
  backendProfile: {
    username: string;
    team: string;
    elo: number;
  } | null;
  stats: {
    totalSubmissions: number;
    totalRaces: number;
    wins: number;
    podiums: number;
    dnfs: number;
    winRate: number;
    averageFinish: number | null;
  };
  eloHistory: Array<{
    raceId: string;
    eloBefore: number;
    eloAfter: number;
    delta: number;
    createdAt: string | null;
  }>;
  submissions: Array<{
    id: string;
    track: string;
    title: string;
    stintPlan: string;
    riskLevel: string;
    notes: string;
    createdAt: string | null;
  }>;
  raceRecords: Array<{
    id: string;
    track: string;
    seasonLabel: string;
    gridPosition: number;
    finishPosition: number;
    lapsCompleted: number;
    fieldSize: number;
    points: number;
    pitCount: number;
    compoundsUsed: string[];
    notes: string;
    status: string;
    createdAt: string | null;
  }>;
};

type AccountDashboardProps = {
  data: DashboardData;
};

function formatDate(input: string | null) {
  if (!input) return "Just now";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(input));
}

function IconButton({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      title={`${label} coming later`}
      className="w-10 h-10 rounded-full border border-pit-border bg-pit-surface/70 text-pit-text
                 hover:text-white hover:border-white/20 transition-colors flex items-center justify-center"
    >
      {children}
    </button>
  );
}

function getCompoundColor(compound: string) {
  return (
    {
      SOFT: "#ff4d4f",
      MEDIUM: "#ffd666",
      HARD: "#d9d9d9",
      INTERMEDIATE: "#52c41a",
      WET: "#1677ff"
    }[compound] || "#666"
  );
}

function EloChart({ history, currentElo }: { history: DashboardData["eloHistory"]; currentElo: number }) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const width = 560;
  const height = 260;

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;

    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = width * dpr;
    canvas.height = height * dpr;
    ctx.scale(dpr, dpr);

    ctx.fillStyle = "#161616";
    ctx.fillRect(0, 0, width, height);

    const padding = { top: 28, right: 24, bottom: 28, left: 56 };
    const plotW = width - padding.left - padding.right;
    const plotH = height - padding.top - padding.bottom;

    const points =
      history.length > 0 ? [history[0].eloBefore, ...history.map((entry) => entry.eloAfter)] : [currentElo, currentElo];

    const minElo = Math.floor(Math.min(...points) / 10) * 10 - 10;
    const maxElo = Math.ceil(Math.max(...points) / 10) * 10 + 10;
    const eloRange = maxElo - minElo || 1;

    const xScale = (i: number) => padding.left + (i / Math.max(points.length - 1, 1)) * plotW;
    const yScale = (elo: number) => padding.top + (1 - (elo - minElo) / eloRange) * plotH;

    ctx.strokeStyle = "#1f1f1f";
    ctx.lineWidth = 0.75;
    const gridStep = Math.max(10, Math.round(eloRange / 4 / 10) * 10);
    for (let e = Math.ceil(minElo / gridStep) * gridStep; e <= maxElo; e += gridStep) {
      const y = yScale(e);
      ctx.beginPath();
      ctx.moveTo(padding.left, y);
      ctx.lineTo(width - padding.right, y);
      ctx.stroke();

      ctx.fillStyle = "#3a3a3a";
      ctx.font = "11px Inter, sans-serif";
      ctx.textAlign = "right";
      ctx.fillText(`${e}`, padding.left - 8, y + 3);
    }

    const baseline = yScale(1200);
    if (baseline >= padding.top && baseline <= padding.top + plotH) {
      ctx.strokeStyle = "#2a2a2a";
      ctx.setLineDash([4, 4]);
      ctx.beginPath();
      ctx.moveTo(padding.left, baseline);
      ctx.lineTo(width - padding.right, baseline);
      ctx.stroke();
      ctx.setLineDash([]);
    }

    const gradient = ctx.createLinearGradient(0, padding.top, 0, padding.top + plotH);
    gradient.addColorStop(0, "rgba(225, 6, 0, 0.16)");
    gradient.addColorStop(1, "rgba(225, 6, 0, 0)");
    ctx.fillStyle = gradient;
    ctx.beginPath();
    ctx.moveTo(xScale(0), yScale(points[0]));
    for (let i = 1; i < points.length; i += 1) {
      ctx.lineTo(xScale(i), yScale(points[i]));
    }
    ctx.lineTo(xScale(points.length - 1), padding.top + plotH);
    ctx.lineTo(xScale(0), padding.top + plotH);
    ctx.closePath();
    ctx.fill();

    ctx.strokeStyle = "#e10600";
    ctx.lineWidth = 3;
    ctx.lineJoin = "round";
    ctx.beginPath();
    ctx.moveTo(xScale(0), yScale(points[0]));
    for (let i = 1; i < points.length; i += 1) {
      ctx.lineTo(xScale(i), yScale(points[i]));
    }
    ctx.stroke();

    const lastX = xScale(points.length - 1);
    const lastY = yScale(points[points.length - 1]);
    ctx.fillStyle = "#e10600";
    ctx.beginPath();
    ctx.arc(lastX, lastY, 5, 0, Math.PI * 2);
    ctx.fill();

    ctx.fillStyle = "#e10600";
    ctx.font = "bold 12px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText(`${Math.round(points[points.length - 1])}`, lastX + 10, lastY + 4);

    ctx.fillStyle = "#3a3a3a";
    ctx.font = "bold 11px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("ELO RATING", padding.left, 16);

    ctx.fillStyle = "#3a3a3a";
    ctx.font = "11px Inter, sans-serif";
    ctx.textAlign = "left";
    ctx.fillText("Race 1", xScale(0), height - 8);
    ctx.textAlign = "right";
    ctx.fillText(`Race ${Math.max(points.length - 1, 1)}`, xScale(points.length - 1), height - 8);
  }, [currentElo, history]);

  return <canvas ref={canvasRef} style={{ width, height }} className="w-full max-w-full h-auto" />;
}

export default function AccountDashboard({ data }: AccountDashboardProps) {
  const primaryName = data.backendProfile?.username || data.profile?.name || "pit-wall-driver";
  const secondaryLabel = data.backendProfile?.team || "Pit Wall";
  const elo = data.backendProfile?.elo ?? 1200;

  return (
    <div className="max-w-7xl mx-auto px-4 py-10">
      <div className="mb-8 flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="flex items-center gap-4">
            <div className="w-1.5 h-10 rounded-full bg-f1-red shrink-0" />
            <div className="min-w-0">
              <div className="flex items-center gap-3 flex-wrap">
                <h1 className="text-4xl font-extrabold text-white tracking-tight">{primaryName}</h1>
                <div className="flex items-center gap-2">
                  <IconButton label="Change username">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M12 20h9" />
                      <path d="M16.5 3.5a2.1 2.1 0 1 1 3 3L7 19l-4 1 1-4Z" />
                    </svg>
                  </IconButton>
                  <IconButton label="Settings">
                    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="M12 15.5A3.5 3.5 0 1 0 12 8.5a3.5 3.5 0 0 0 0 7Z" />
                      <path d="M19.4 15a1.7 1.7 0 0 0 .34 1.87l.06.06a2 2 0 1 1-2.83 2.83l-.06-.06a1.7 1.7 0 0 0-1.87-.34 1.7 1.7 0 0 0-1 1.55V21a2 2 0 1 1-4 0v-.09a1.7 1.7 0 0 0-1-1.55 1.7 1.7 0 0 0-1.87.34l-.06.06a2 2 0 1 1-2.83-2.83l.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-1.55-1H3a2 2 0 1 1 0-4h.09a1.7 1.7 0 0 0 1.55-1 1.7 1.7 0 0 0-.34-1.87l-.06-.06a2 2 0 1 1 2.83-2.83l.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-1.55V3a2 2 0 1 1 4 0v.09a1.7 1.7 0 0 0 1 1.55 1.7 1.7 0 0 0 1.87-.34l.06-.06a2 2 0 1 1 2.83 2.83l-.06.06A1.7 1.7 0 0 0 19.4 9c.35.63.94 1 1.55 1H21a2 2 0 1 1 0 4h-.09c-.61 0-1.2.37-1.55 1Z" />
                    </svg>
                  </IconButton>
                </div>
              </div>
              <p className="text-2xl text-pit-text mt-1">{secondaryLabel}</p>
              {data.profile?.email ? <p className="text-sm text-pit-muted mt-2">{data.profile.email}</p> : null}
            </div>
          </div>
        </div>

        <div className="text-right min-w-24">
          <div className="text-5xl font-extrabold text-white tabular-nums">{Math.round(elo)}</div>
          <div className="text-[11px] text-pit-muted uppercase tracking-[0.2em] mt-1">ELO</div>
        </div>
      </div>

      <div className="grid grid-cols-2 lg:grid-cols-5 gap-4 mb-8">
        {[
          { label: "Races", value: data.stats.totalRaces },
          { label: "Wins", value: data.stats.wins },
          { label: "Podiums", value: data.stats.podiums },
          { label: "DNFs", value: data.stats.dnfs },
          { label: "Win Rate", value: `${data.stats.winRate}%` }
        ].map((stat) => (
          <div key={stat.label} className="card p-6 text-center min-h-[120px] flex flex-col justify-center">
            <div className="stat-value text-4xl">{stat.value}</div>
            <div className="text-[11px] text-pit-muted uppercase tracking-wider mt-3">{stat.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-[1.1fr_0.9fr] gap-6 mb-8">
        <section className="card overflow-hidden">
          <div className="px-5 py-4 border-b border-pit-border">
            <span className="section-label">ELO Progression</span>
          </div>
          <div className="p-4">
            <EloChart history={data.eloHistory} currentElo={elo} />
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="px-5 py-4 border-b border-pit-border flex items-center justify-between">
            <span className="section-label">Recent Races</span>
            <span className="text-[10px] text-pit-muted font-mono">{data.raceRecords.length} races</span>
          </div>
          <div className="px-3 py-2 min-h-[340px] space-y-1">
            {data.raceRecords.length ? (
              data.raceRecords.map((record) => (
                <article
                  key={record.id}
                  className="flex items-center gap-4 px-3 py-3 rounded-lg hover:bg-white/[0.02] transition-colors"
                >
                  <span
                    className={`w-10 text-right font-extrabold tabular-nums text-2xl ${
                      record.status === "DNF"
                        ? "text-pit-muted"
                        : record.finishPosition === 1
                          ? "text-f1-red"
                          : record.finishPosition <= 3
                            ? "text-white"
                            : "text-pit-muted"
                    }`}
                  >
                    {record.status === "DNF" ? "DNF" : `P${record.finishPosition}`}
                  </span>

                  <div className="flex-1 min-w-0">
                    <div className="text-white text-2xl font-bold leading-none">{record.track}</div>
                    <div className="flex items-center gap-2 mt-2 text-xs text-pit-muted">
                      {record.compoundsUsed.length ? (
                        <span className="flex items-center gap-1.5">
                          {record.compoundsUsed.map((compound, index) => (
                            <span
                              key={`${record.id}-${compound}-${index}`}
                              className="w-3.5 h-3.5 rounded-[3px]"
                              style={{ backgroundColor: getCompoundColor(compound) }}
                            />
                          ))}
                        </span>
                      ) : null}
                      {record.pitCount > 0 ? <span>{record.pitCount} stop{record.pitCount === 1 ? "" : "s"}</span> : null}
                      {record.gridPosition > 0 ? <span>from P{record.gridPosition}</span> : null}
                    </div>
                  </div>

                  <div className="text-right">
                    <div className="text-xl font-bold text-white">{record.points > 0 ? `+${record.points} pts` : "0 pts"}</div>
                    <div className="badge bg-pit-surface text-pit-text mt-2">{record.seasonLabel.toUpperCase()}</div>
                  </div>
                </article>
              ))
            ) : (
              <p className="text-pit-muted text-sm text-center py-12">No races logged yet. Save one from Lobby.</p>
            )}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="card overflow-hidden">
          <div className="px-5 py-4 border-b border-pit-border">
            <h2 className="text-white font-bold text-xl">Strategy Submissions</h2>
            <p className="text-xs text-pit-muted mt-1">Saved from the strategy workspace into MongoDB.</p>
          </div>

          <div className="p-4 space-y-3">
            {data.submissions.length ? (
              data.submissions.map((submission) => (
                <article key={submission.id} className="rounded-lg border border-pit-border p-4 bg-pit-surface/40">
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <strong className="text-white text-lg">{submission.title}</strong>
                    <span className="badge bg-f1-red/10 text-f1-red">{submission.riskLevel}</span>
                  </div>
                  <p className="text-xs text-pit-muted mb-2">{submission.track}</p>
                  <pre className="text-xs text-pit-text whitespace-pre-wrap font-mono leading-relaxed">
                    {submission.stintPlan}
                  </pre>
                  {submission.notes ? <p className="text-xs text-pit-muted mt-3">{submission.notes}</p> : null}
                  <p className="text-[10px] text-pit-muted mt-3">{formatDate(submission.createdAt)}</p>
                </article>
              ))
            ) : (
              <p className="text-pit-muted text-sm text-center py-8">No submissions yet. Save one from Strategy.</p>
            )}
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="px-5 py-4 border-b border-pit-border">
            <h2 className="text-white font-bold text-xl">Race Record Log</h2>
            <p className="text-xs text-pit-muted mt-1">Stored finishes, positions, and event notes.</p>
          </div>

          <div className="p-4">
            {data.raceRecords.length ? (
              <div className="space-y-2">
                {data.raceRecords.map((record) => (
                  <article
                    key={record.id}
                    className="rounded-lg border border-pit-border p-4 bg-pit-surface/40 flex items-start justify-between gap-4"
                  >
                    <div>
                      <strong className="text-white">{record.track}</strong>
                      <p className="text-xs text-pit-muted mt-1">{record.seasonLabel}</p>
                      {record.notes ? <p className="text-xs text-pit-text mt-2">{record.notes}</p> : null}
                      <p className="text-[10px] text-pit-muted mt-3">{formatDate(record.createdAt)}</p>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-bold text-white">{record.status === "DNF" ? "DNF" : `P${record.finishPosition}`}</div>
                      <div className="text-xs text-pit-muted">
                        {record.gridPosition > 0 ? `from P${record.gridPosition}` : "backend-linked"}
                      </div>
                      <div className="badge bg-pit-surface text-pit-text mt-2">{record.status}</div>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="text-pit-muted text-sm text-center py-8">No races logged yet. Save one from Lobby.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
