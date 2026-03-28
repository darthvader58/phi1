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

  // Show lobby state if not racing yet
  if (raceInfo?.status === "lobby") {
    const players = Object.values(raceInfo.players || {}) as any[];
    return (
      <div className="max-w-3xl mx-auto px-4 py-8">
        <h1 className="text-2xl font-bold mb-2 text-gray-200">
          Race Lobby — {raceInfo.track?.charAt(0).toUpperCase() + raceInfo.track?.slice(1)}
        </h1>
        <p className="text-sm text-gray-500 mb-6">Race ID: {raceId}</p>

        <div className="bg-gray-900 border border-gray-700 rounded-lg p-5 mb-4">
          <h2 className="text-sm font-bold text-gray-300 mb-3">
            PLAYERS ({players.length}/8)
          </h2>
          {players.length === 0 ? (
            <p className="text-gray-600 text-sm">No players yet.</p>
          ) : (
            <div className="space-y-1">
              {players.map((p: any) => (
                <div key={p.car_id} className="text-sm text-gray-300">
                  <span className="font-bold">{p.car_id}</span>
                  <span className="text-gray-500 ml-2">{p.username}</span>
                </div>
              ))}
            </div>
          )}
        </div>

        <div className="flex gap-3">
          <button
            onClick={handleJoin}
            disabled={joining}
            className="px-5 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200 rounded
                       text-sm font-bold transition-colors disabled:opacity-50"
          >
            {joining ? "Joining..." : "Join Race"}
          </button>
          <button
            onClick={handleStart}
            disabled={starting || players.length === 0}
            className="px-5 py-2 bg-red-700 hover:bg-red-600 text-white rounded
                       text-sm font-bold transition-colors disabled:opacity-50"
          >
            {starting ? "Starting..." : "Start Race"}
          </button>
        </div>
        {error && <p className="text-red-400 text-xs mt-2">{error}</p>}
      </div>
    );
  }

  // Countdown
  if (countdown !== null) {
    return (
      <div className="flex items-center justify-center min-h-[60vh]">
        <div className="text-center">
          <div className="text-6xl font-bold text-red-500 mb-2">{countdown}</div>
          <div className="text-gray-500 text-sm">Race starting...</div>
        </div>
      </div>
    );
  }

  // Race view (live or finished)
  const cars = lapData?.cars || result?.standings || [];
  const trackName = raceInfo?.track || lapData?.weather || "bahrain";
  const weather = lapData?.weather || "dry";
  const safetyCar = lapData?.safety_car || false;

  return (
    <div className="max-w-7xl mx-auto px-4 py-4">
      {/* Top bar */}
      <div className="flex items-center gap-4 mb-4 bg-gray-900 border border-gray-700 rounded-lg px-4 py-2">
        <div className="text-sm font-bold text-gray-200">
          {raceInfo?.track?.toUpperCase() || "RACE"}
        </div>
        <div className="text-sm text-gray-400">
          Lap {currentLap || result?.total_laps || "?"}/{totalLaps || result?.total_laps || "?"}
        </div>
        <div className={`text-xs font-bold px-2 py-0.5 rounded ${
          weather === "dry" ? "bg-gray-800 text-gray-400" :
          weather === "wet" ? "bg-blue-900 text-blue-300" :
          "bg-cyan-900 text-cyan-300"
        }`}>
          {weather.toUpperCase()}
        </div>
        {safetyCar && (
          <div className="text-xs font-bold px-2 py-0.5 rounded bg-yellow-900 text-yellow-300">
            SAFETY CAR
          </div>
        )}
        <div className="ml-auto flex items-center gap-2 text-xs text-gray-500">
          <span>Speed:</span>
          {[1, 5, 20].map((s) => (
            <button
              key={s}
              onClick={() => setSpeed(s)}
              className="px-2 py-0.5 rounded bg-gray-800 hover:bg-gray-700 text-gray-300 transition-colors"
            >
              {s}x
            </button>
          ))}
        </div>
        <div className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`}
             title={connected ? "Connected" : "Disconnected"} />
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-12 gap-4">
        {/* Left: Leaderboard */}
        <div className="col-span-3">
          {cars.length > 0 && <Leaderboard cars={cars} />}
        </div>

        {/* Center: Track map */}
        <div className="col-span-5">
          {cars.length > 0 && (
            <TrackMap
              cars={cars}
              trackName={raceInfo?.track || "bahrain"}
              weather={weather}
              safetyCar={safetyCar}
            />
          )}
          {/* Gap chart */}
          {allLapData.length > 1 && (
            <div className="mt-4">
              <GapChart lapHistory={allLapData} width={480} height={200} />
            </div>
          )}
        </div>

        {/* Right: Event log */}
        <div className="col-span-4">
          <EventLog events={events} />
        </div>
      </div>

      {/* Finished overlay */}
      {status === "finished" && result && (
        <div className="mt-6 bg-gray-900 border border-gray-700 rounded-lg p-5">
          <h2 className="text-lg font-bold text-gray-200 mb-3">RACE COMPLETE</h2>
          <div className="space-y-1">
            {result.standings.map((car, idx) => (
              <div key={car.car_id} className="flex items-center gap-3 text-sm">
                <span className="w-8 text-right font-bold text-gray-400">
                  P{car.position}
                </span>
                <span className="w-20 font-bold text-gray-200">{car.car_id}</span>
                <span className="text-gray-500">
                  {car.retired ? "DNF" : `+${car.gap_to_leader.toFixed(3)}s`}
                </span>
                <span className="text-gray-600 text-xs">
                  {car.pit_count} stops ({car.compounds_used.join(" → ")})
                </span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
