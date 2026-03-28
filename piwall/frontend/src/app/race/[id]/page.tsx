"use client";

import { useParams } from "next/navigation";
import { useState, useEffect, useRef } from "react";
import { useRaceWebSocket } from "@/lib/websocket";
import { api } from "@/lib/api";
import { COMPOUND_COLORS, LapSnapshot } from "@/lib/types";
import Leaderboard from "@/components/Leaderboard";
import GapChart from "@/components/GapChart";
import LapTimeChart from "@/components/LapTimeChart";
import TrackMap from "@/components/TrackMap";
import EventLog from "@/components/EventLog";
import TyreStrategyChart from "@/components/TyreStrategyChart";
import BeliefPanel from "@/components/BeliefPanel";
import { useToast } from "@/components/Toast";

export default function RacePage() {
  const params = useParams();
  const raceId = params.id as string;
  const { toast } = useToast();
  const [raceInfo, setRaceInfo] = useState<any>(null);
  const [joining, setJoining] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const [startingCompound, setStartingCompound] = useState("MEDIUM");
  const [activeChart, setActiveChart] = useState<"gap" | "laptimes">("gap");
  const prevPositions = useRef<Record<string, number>>({});

  const {
    connected, status, currentLap, totalLaps,
    lapData, allLapData, events, result, countdown, setSpeed,
  } = useRaceWebSocket(raceInfo?.status === "lobby" ? null : raceId);

  // Track position changes for delta indicators
  useEffect(() => {
    if (lapData?.cars) {
      const current: Record<string, number> = {};
      for (const c of lapData.cars) current[c.car_id] = c.position;
      prevPositions.current = current;
    }
  }, [lapData]);

  // Get previous positions from the lap *before* current
  const getPrevPositions = (): Record<string, number> => {
    if (allLapData.length < 2) return {};
    const prev = allLapData[allLapData.length - 2];
    const map: Record<string, number> = {};
    for (const c of prev.cars) map[c.car_id] = c.position;
    return map;
  };

  useEffect(() => {
    loadRace();
  }, [raceId]);

  async function loadRace() {
    try {
      const info = await api.getRace(raceId);
      setRaceInfo(info);
    } catch (err: any) {
      setError(err.message);
      toast(err.message, "error");
    }
  }

  async function handleJoin() {
    setJoining(true);
    try {
      await api.joinRace(raceId, startingCompound);
      toast("Joined race!", "success");
      await loadRace();
    } catch (err: any) {
      setError(err.message);
      toast(err.message, "error");
    }
    setJoining(false);
  }

  async function handleStart() {
    setStarting(true);
    try {
      await api.startRace(raceId);
      setRaceInfo((prev: any) => ({ ...prev, status: "countdown" }));
      toast("Race starting...", "info");
    } catch (err: any) {
      setError(err.message);
      toast(err.message, "error");
    }
    setStarting(false);
  }

  // Lobby state
  if (raceInfo?.status === "lobby") {
    const players = Object.values(raceInfo.players || {}) as any[];
    return (
      <div className="max-w-3xl mx-auto px-4 py-10">
        <div className="mb-8">
          <h1 className="text-3xl font-extrabold text-white tracking-tight">
            {raceInfo.track?.charAt(0).toUpperCase() + raceInfo.track?.slice(1)} Grand Prix
          </h1>
          <p className="text-sm text-pit-muted font-mono mt-1">ID: {raceId}</p>
        </div>

        <div className="card overflow-hidden mb-5">
          <div className="px-5 py-3.5 border-b border-pit-border flex items-center justify-between">
            <span className="section-label">Drivers</span>
            <span className="text-[10px] text-pit-muted font-mono">{players.length}/8</span>
          </div>
          <div className="p-4">
            {players.length === 0 ? (
              <p className="text-pit-muted text-sm text-center py-6">Waiting for drivers...</p>
            ) : (
              <div className="space-y-2">
                {players.map((p: any) => (
                  <div key={p.car_id} className="flex items-center gap-3 px-3 py-2.5 rounded-lg bg-pit-surface/50">
                    <div className="w-1 h-6 rounded-full bg-f1-red" />
                    <span className="font-bold text-white text-sm">{p.car_id}</span>
                    <span className="text-pit-muted text-xs">{p.username}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        </div>

        {/* Starting compound selection */}
        <div className="card p-5 mb-5">
          <div className="flex items-center gap-2 mb-3">
            <div className="accent-line" />
            <span className="section-label">Starting Compound</span>
          </div>
          <div className="flex items-center gap-2">
            {["SOFT", "MEDIUM", "HARD"].map((c) => (
              <button
                key={c}
                onClick={() => setStartingCompound(c)}
                className={`flex items-center gap-2 px-4 py-2.5 rounded-lg text-sm font-semibold
                           transition-all duration-150
                           ${startingCompound === c
                             ? "bg-white/10 border border-white/20 text-white"
                             : "bg-pit-surface border border-pit-border text-pit-text hover:bg-pit-border"}`}
              >
                <span className="compound-dot" style={{ backgroundColor: COMPOUND_COLORS[c] }} />
                {c}
              </button>
            ))}
          </div>
        </div>

        <div className="flex gap-3">
          <button onClick={handleJoin} disabled={joining} className="btn-secondary">
            {joining ? "Joining..." : "Join Race"}
          </button>
          <button onClick={handleStart} disabled={starting || players.length === 0} className="btn-primary">
            {starting ? "Starting..." : "Start Race"}
          </button>
        </div>
        {error && <p className="text-f1-red text-xs mt-3">{error}</p>}
      </div>
    );
  }

  // Countdown
  if (countdown !== null) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="text-8xl font-black text-f1-red mb-3 tabular-nums animate-pulse-slow">
            {countdown}
          </div>
          <div className="text-pit-muted text-sm uppercase tracking-widest">Lights Out</div>
        </div>
      </div>
    );
  }

  // Race view
  const cars = lapData?.cars || result?.standings || [];
  const trackName = raceInfo?.track || "bahrain";
  const weather = lapData?.weather || "dry";
  const safetyCar = lapData?.safety_car || false;

  return (
    <div className="max-w-7xl mx-auto px-4 py-4">
      {/* Race info bar */}
      <div className="card px-4 sm:px-5 py-3 mb-4 flex items-center gap-3 sm:gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-1 h-6 rounded-full bg-f1-red" />
          <span className="text-sm font-extrabold text-white tracking-wide">
            {raceInfo?.track?.toUpperCase() || "RACE"}
          </span>
        </div>

        <div className="divider h-4 hidden sm:block" style={{ borderTop: "none", borderLeft: "1px solid #2a2a2a" }} />

        <span className="text-sm text-pit-text font-mono tabular-nums">
          LAP <span className="text-white font-bold">{currentLap || result?.total_laps || "?"}</span>
          <span className="text-pit-muted">/{totalLaps || result?.total_laps || "?"}</span>
        </span>

        {/* Progress bar */}
        <div className="hidden sm:block w-24 h-1 bg-pit-surface rounded-full overflow-hidden">
          <div className="h-full bg-f1-red rounded-full transition-all duration-300"
               style={{ width: `${((currentLap || 0) / (totalLaps || 1)) * 100}%` }} />
        </div>

        <div className={`badge text-[10px] font-bold ${
          weather === "dry" ? "bg-pit-surface text-pit-text" :
          weather === "wet" ? "bg-blue-500/10 text-blue-400" :
          weather === "damp" ? "bg-cyan-500/10 text-cyan-400" :
          "bg-green-500/10 text-green-400"
        }`}>
          {weather.toUpperCase()}
        </div>

        {safetyCar && (
          <div className="badge bg-yellow-500/10 text-yellow-400 text-[10px] font-bold animate-pulse-slow">
            SAFETY CAR
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[10px] text-pit-muted uppercase tracking-wider hidden sm:inline">Speed</span>
          {[1, 5, 20].map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className="px-2.5 py-1 rounded-md bg-pit-surface text-pit-text text-[11px] font-bold
                         hover:bg-pit-border hover:text-white transition-colors duration-150"
            >
              {s}x
            </button>
          ))}
          <div className={`w-2 h-2 rounded-full ml-1 ${
            connected ? "bg-green-500" :
            status === "disconnected" ? "bg-yellow-500 animate-pulse-slow" : "bg-red-500"
          }`} title={connected ? "Connected" : status === "disconnected" ? "Reconnecting..." : "Disconnected"} />
        </div>
      </div>

      {/* Main grid - responsive */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-4">
        {/* Leaderboard */}
        <div className="lg:col-span-3">
          {cars.length > 0 && (
            <Leaderboard
              cars={cars}
              previousPositions={getPrevPositions()}
            />
          )}
        </div>

        {/* Center: Track + Charts */}
        <div className="lg:col-span-5 space-y-4">
          {cars.length > 0 && (
            <TrackMap
              cars={cars}
              trackName={trackName}
              weather={weather}
              safetyCar={safetyCar}
            />
          )}

          {/* Chart tabs */}
          {allLapData.length > 1 && (
            <div>
              <div className="flex items-center gap-1 mb-2">
                <button
                  onClick={() => setActiveChart("gap")}
                  className={`px-3 py-1.5 rounded-md text-[11px] font-bold transition-colors ${
                    activeChart === "gap" ? "bg-f1-red/10 text-f1-red" : "text-pit-muted hover:text-pit-text"
                  }`}
                >
                  Gap to Leader
                </button>
                <button
                  onClick={() => setActiveChart("laptimes")}
                  className={`px-3 py-1.5 rounded-md text-[11px] font-bold transition-colors ${
                    activeChart === "laptimes" ? "bg-f1-red/10 text-f1-red" : "text-pit-muted hover:text-pit-text"
                  }`}
                >
                  Lap Times
                </button>
              </div>
              {activeChart === "gap" ? (
                <GapChart lapHistory={allLapData} width={480} height={200} />
              ) : (
                <LapTimeChart lapHistory={allLapData} width={480} height={200} />
              )}
            </div>
          )}
        </div>

        {/* Right: Event log */}
        <div className="lg:col-span-4 space-y-4">
          <EventLog events={events} />
          {cars.length > 0 && (
            <BeliefPanel cars={cars} />
          )}
        </div>
      </div>

      {/* Finished */}
      {status === "finished" && result && (
        <div className="space-y-4 mt-6">
          {/* Results */}
          <div className="card overflow-hidden">
            <div className="px-5 py-3.5 border-b border-pit-border flex items-center gap-2">
              <div className="accent-line" />
              <span className="section-label">Race Complete</span>
            </div>
            <div className="p-5 space-y-1">
              {result.standings.map((car, idx) => (
                <div key={car.car_id}
                     className={`flex items-center gap-4 text-sm px-3 py-2.5 rounded-lg
                                ${idx === 0 ? "bg-f1-red/5" : "hover:bg-white/[0.02]"} transition-colors`}>
                  <span className={`w-10 text-right font-extrabold tabular-nums ${
                    idx === 0 ? "text-f1-red" : idx < 3 ? "text-white" : "text-pit-muted"
                  }`}>
                    P{car.position}
                  </span>
                  <span className="w-20 font-bold text-white">{car.car_id}</span>
                  <span className="text-pit-text font-mono text-xs tabular-nums">
                    {car.retired ? "DNF" : `+${car.gap_to_leader.toFixed(3)}s`}
                  </span>
                  <div className="flex items-center gap-1 ml-auto">
                    {car.compounds_used.map((c, ci) => (
                      <span key={ci} className="compound-dot" style={{ backgroundColor: COMPOUND_COLORS[c] || "#888" }} />
                    ))}
                    <span className="text-pit-muted text-[11px] ml-2">
                      {car.pit_count} stop{car.pit_count !== 1 ? "s" : ""}
                    </span>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Tyre strategy chart */}
          <TyreStrategyChart cars={result.standings} totalLaps={result.total_laps} />
        </div>
      )}
    </div>
  );
}
