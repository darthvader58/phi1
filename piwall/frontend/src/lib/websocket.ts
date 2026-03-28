/** WebSocket hook for live race streaming. */

import { useEffect, useRef, useState, useCallback } from "react";
import type { WsMessage, LapSnapshot, RaceEvent, RaceResult } from "./types";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";

export function useRaceWebSocket(raceId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [currentLap, setCurrentLap] = useState(0);
  const [totalLaps, setTotalLaps] = useState(0);
  const [lapData, setLapData] = useState<LapSnapshot | null>(null);
  const [events, setEvents] = useState<RaceEvent[]>([]);
  const [allLapData, setAllLapData] = useState<LapSnapshot[]>([]);
  const [result, setResult] = useState<RaceResult | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [status, setStatus] = useState<"connecting" | "connected" | "racing" | "finished">("connecting");

  useEffect(() => {
    if (!raceId) return;

    const ws = new WebSocket(`${WS_BASE}/ws/race/${raceId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setStatus("connected");
    };

    ws.onmessage = (event) => {
      const msg: WsMessage = JSON.parse(event.data);

      switch (msg.type) {
        case "countdown":
          setCountdown(msg.seconds ?? null);
          break;

        case "lap":
          setStatus("racing");
          setCountdown(null);
          setCurrentLap(msg.lap ?? 0);
          setTotalLaps(msg.total_laps ?? 0);
          if (msg.data) {
            setLapData(msg.data);
            setAllLapData((prev) => [...prev, msg.data!]);
          }
          if (msg.events) {
            setEvents((prev) => [...prev, ...msg.events!]);
          }
          break;

        case "finished":
          setStatus("finished");
          if (msg.result) setResult(msg.result);
          break;

        case "ping":
          break;
      }
    };

    ws.onclose = () => {
      setConnected(false);
    };

    return () => {
      ws.close();
    };
  }, [raceId]);

  const setSpeed = useCallback((speed: number) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "speed", speed }));
    }
  }, []);

  return {
    connected,
    status,
    currentLap,
    totalLaps,
    lapData,
    allLapData,
    events,
    result,
    countdown,
    setSpeed,
  };
}
