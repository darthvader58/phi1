/** WebSocket hook for live race streaming with auto-reconnect. */

import { useEffect, useRef, useState, useCallback } from "react";
import type { WsMessage, LapSnapshot, RaceEvent, RaceResult } from "./types";

const WS_BASE = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000";
const MAX_RECONNECT_ATTEMPTS = 5;
const RECONNECT_DELAY_MS = 2000;

export function useRaceWebSocket(raceId: string | null) {
  const wsRef = useRef<WebSocket | null>(null);
  const reconnectAttempts = useRef(0);
  const reconnectTimer = useRef<NodeJS.Timeout | null>(null);

  const [connected, setConnected] = useState(false);
  const [currentLap, setCurrentLap] = useState(0);
  const [totalLaps, setTotalLaps] = useState(0);
  const [lapData, setLapData] = useState<LapSnapshot | null>(null);
  const [events, setEvents] = useState<RaceEvent[]>([]);
  const [allLapData, setAllLapData] = useState<LapSnapshot[]>([]);
  const [result, setResult] = useState<RaceResult | null>(null);
  const [countdown, setCountdown] = useState<number | null>(null);
  const [lightsOut, setLightsOut] = useState(false);
  const [status, setStatus] = useState<"connecting" | "connected" | "racing" | "finished" | "disconnected">("connecting");

  const connect = useCallback(() => {
    if (!raceId) return;

    const ws = new WebSocket(`${WS_BASE}/ws/race/${raceId}`);
    wsRef.current = ws;

    ws.onopen = () => {
      setConnected(true);
      setStatus("connected");
      reconnectAttempts.current = 0;
    };

    ws.onmessage = (event) => {
      const msg: WsMessage = JSON.parse(event.data);

      switch (msg.type) {
        case "countdown":
          setCountdown(msg.seconds ?? null);
          setLightsOut(false);
          break;

        case "lights_out":
          setCountdown(null);
          setLightsOut(true);
          break;

        case "lap":
          setStatus("racing");
          setCountdown(null);
          setLightsOut(false);
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
      wsRef.current = null;

      // Don't reconnect if race is finished
      if (status === "finished") return;

      // Auto-reconnect with backoff
      if (reconnectAttempts.current < MAX_RECONNECT_ATTEMPTS) {
        setStatus("disconnected");
        const delay = RECONNECT_DELAY_MS * Math.pow(1.5, reconnectAttempts.current);
        reconnectTimer.current = setTimeout(() => {
          reconnectAttempts.current++;
          connect();
        }, delay);
      }
    };

    ws.onerror = () => {
      ws.close();
    };
  }, [raceId, status]);

  useEffect(() => {
    connect();
    return () => {
      if (reconnectTimer.current) clearTimeout(reconnectTimer.current);
      if (wsRef.current) wsRef.current.close();
    };
  }, [raceId]); // eslint-disable-line react-hooks/exhaustive-deps

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
    lightsOut,
    setSpeed,
  };
}
