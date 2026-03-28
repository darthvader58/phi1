"use client";

import { RaceEvent } from "@/lib/types";
import { useEffect, useRef } from "react";

interface Props {
  events: RaceEvent[];
  maxVisible?: number;
}

const EVENT_ICONS: Record<string, string> = {
  pit: "PIT",
  sc_start: " SC",
  sc_end: " GO",
  dnf: "DNF",
  overtake: "OVT",
  weather: " WX",
  penalty: "PEN",
  undercut: "UCT",
};

const EVENT_COLORS: Record<string, string> = {
  pit: "text-blue-400",
  sc_start: "text-yellow-400",
  sc_end: "text-green-400",
  dnf: "text-red-400",
  overtake: "text-purple-400",
  weather: "text-cyan-400",
  penalty: "text-orange-400",
  undercut: "text-pink-400",
};

export default function EventLog({ events, maxVisible = 50 }: Props) {
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
    }
  }, [events.length]);

  const visible = events.slice(-maxVisible);

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg overflow-hidden flex flex-col">
      <div className="px-3 py-2 bg-gray-800 border-b border-gray-700">
        <h3 className="text-sm font-mono font-bold text-gray-300 uppercase tracking-wider">
          Race Log
        </h3>
      </div>
      <div ref={scrollRef} className="overflow-y-auto max-h-64 p-2 space-y-0.5">
        {visible.map((event, idx) => (
          <div key={idx} className="flex gap-2 text-xs font-mono">
            <span className="text-gray-600 w-8 text-right flex-shrink-0">
              L{event.lap}
            </span>
            <span
              className={`w-8 font-bold flex-shrink-0 ${
                EVENT_COLORS[event.type] || "text-gray-400"
              }`}
            >
              {EVENT_ICONS[event.type] || "---"}
            </span>
            <span className="text-gray-300 break-all">{event.detail}</span>
          </div>
        ))}
        {events.length === 0 && (
          <div className="text-gray-600 text-xs font-mono text-center py-4">
            Waiting for race events...
          </div>
        )}
      </div>
    </div>
  );
}
