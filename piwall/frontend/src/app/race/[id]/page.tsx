"use client";

import { useParams } from "next/navigation";
import { useState, useEffect } from "react";
import { useRaceWebSocket } from "@/lib/websocket";
import { api } from "@/lib/api";
import Leaderboard from "@/components/Leaderboard";
import GapChart from "@/components/GapChart";
import TrackMap from "@/components/TrackMap";
import EventLog from "@/components/EventLog";

export default function RacePage() {
  const params = useParams();
  const raceId = params.id as string;
  const [raceInfo, setRaceInfo] = useState<any>(null);
  const [joining, setJoining] = useState(false);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");

  const {
    connected, status, currentLap, totalLaps,
    lapData, allLapData, events, result, countdown, setSpeed,
  } = useRaceWebSocket(raceInfo?.status === "lobby" ? null : raceId);

  useEffect(() => {
    loadRace();
  }, [raceId]);

  async function loadRace() {
    try {
      const info = await api.getRace(raceId);
      setRaceInfo(info);
    } catch (err: any) {
      setError(err.message);
    }
  }

  async function handleJoin() {
    setJoining(true);
    try {
      await api.joinRace(raceId);
      await loadRace();
    } catch (err: any) {
      setError(err.message);
    }
    setJoining(false);
  }

  async function handleStart() {
    setStarting(true);
    try {
      await api.startRace(raceId);
      setRaceInfo((prev: any) => ({ ...prev, status: "countdown" }));
    } catch (err: any) {
      setError(err.message);
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
  const trackName = raceInfo?.track || lapData?.weather || "bahrain";
  const weather = lapData?.weather || "dry";
  const safetyCar = lapData?.safety_car || false;

  return (
    <div className="max-w-7xl mx-auto px-4 py-4">
      {/* Race info bar */}
      <div className="card px-5 py-3 mb-4 flex items-center gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div className="w-1 h-6 rounded-full bg-f1-red" />
          <span className="text-sm font-extrabold text-white tracking-wide">
            {raceInfo?.track?.toUpperCase() || "RACE"}
          </span>
        </div>

        <div className="divider h-4" />

        <span className="text-sm text-pit-text font-mono tabular-nums">
          LAP <span className="text-white font-bold">{currentLap || result?.total_laps || "?"}</span>
          <span className="text-pit-muted">/{totalLaps || result?.total_laps || "?"}</span>
        </span>

        <div className={`badge text-[10px] font-bold ${
          weather === "dry" ? "bg-pit-surface text-pit-text" :
          weather === "wet" ? "bg-blue-500/10 text-blue-400" :
          "bg-cyan-500/10 text-cyan-400"
        }`}>
          {weather.toUpperCase()}
        </div>

        {safetyCar && (
          <div className="badge bg-yellow-500/10 text-yellow-400 text-[10px] font-bold animate-pulse-slow">
            SAFETY CAR
          </div>
        )}

        <div className="ml-auto flex items-center gap-2">
          <span className="text-[10px] text-pit-muted uppercase tracking-wider">Speed</span>
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
          <div className={`w-2 h-2 rounded-full ml-2 ${connected ? "bg-green-500" : "bg-red-500"}`}
               title={connected ? "Connected" : "Disconnected"} />
        </div>
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-3">
          {cars.length > 0 && <Leaderboard cars={cars} />}
        </div>

        <div className="col-span-5">
          {cars.length > 0 && (
            <TrackMap
              cars={cars}
              trackName={raceInfo?.track || "bahrain"}
              weather={weather}
              safetyCar={safetyCar}
            />
          )}
          {allLapData.length > 1 && (
            <div className="mt-4">
              <GapChart lapHistory={allLapData} width={480} height={200} />
            </div>
          )}
        </div>

        <div className="col-span-4">
          <EventLog events={events} />
        </div>
      </div>

      {/* Finished */}
      {status === "finished" && result && (
        <div className="card overflow-hidden mt-6">
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
                <span className="text-pit-muted text-[11px] ml-auto">
                  {car.pit_count} stop{car.pit_count !== 1 ? "s" : ""} ({car.compounds_used.join(" → ")})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
