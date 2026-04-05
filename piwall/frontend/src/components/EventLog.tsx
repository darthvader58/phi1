"use client";

import { RaceEvent } from "@/lib/types";
import { useEffect, useRef } from "react";

interface Props {
  events: RaceEvent[];
  maxVisible?: number;
}

const EVENT_CONFIG: Record<string, { label: string; color: string; bg: string }> = {
  pit: { label: "PIT", color: "text-blue-400", bg: "bg-blue-400/10" },
  sc_start: { label: "SC", color: "text-yellow-400", bg: "bg-yellow-400/10" },
  sc_end: { label: "GO", color: "text-green-400", bg: "bg-green-400/10" },
  dnf: { label: "DNF", color: "text-red-400", bg: "bg-red-400/10" },
  overtake: { label: "OVT", color: "text-purple-400", bg: "bg-purple-400/10" },
  weather: { label: "WX", color: "text-cyan-400", bg: "bg-cyan-400/10" },
  penalty: { label: "PEN", color: "text-orange-400", bg: "bg-orange-400/10" },
  undercut: { label: "UCT", color: "text-pink-400", bg: "bg-pink-400/10" },
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
    <div className="card overflow-hidden flex flex-col">
      <div className="px-4 py-3 border-b border-pit-border flex items-center justify-between">
        <span className="section-label">Race Log</span>
        <span className="text-[10px] text-pit-muted font-mono">{events.length} events</span>
      </div>
      <div ref={scrollRef} className="overflow-y-auto max-h-80 p-2 space-y-0.5">
        {visible.map((event, idx) => {
          const config = EVENT_CONFIG[event.type] || { label: "---", color: "text-pit-muted", bg: "bg-pit-surface" };
          return (
            <div
              key={idx}
              className="flex items-start gap-2.5 px-2 py-1.5 rounded-md hover:bg-white/[0.02]
                         transition-colors duration-100 animate-fade-in"
            >
              <span className="text-pit-muted text-[11px] font-mono w-6 text-right flex-shrink-0 mt-px tabular-nums">
                {event.lap}
              </span>
              <span className={`${config.bg} ${config.color} text-[10px] font-bold px-1.5 py-0.5
                              rounded flex-shrink-0 min-w-[30px] text-center`}>
                {config.label}
              </span>
              <span className="text-pit-text text-xs leading-relaxed">{event.detail}</span>
            </div>
          );
        })}
        {events.length === 0 && (
          <div className="text-pit-muted text-xs text-center py-8">
            Waiting for race events...
          </div>
        )}
      </div>
    </div>
  );
}
