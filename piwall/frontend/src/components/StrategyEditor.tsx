"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="h-[500px] bg-pit-dark rounded-card border border-pit-border
                    flex items-center justify-center text-pit-muted text-sm">
      Loading editor...
    </div>
  ),
});

interface Props {
  initialCode: string;
  onSubmit: (code: string) => void;
  onTest?: (code: string) => void;
  submitting?: boolean;
  testing?: boolean;
}

const TYPE_HINTS = `# ── PIT WALL Strategy Function ──────────────────────────
#
# Available on 'state':
#   state.lap            (int)    Current lap number
#   state.total_laps     (int)    Total laps in race
#   state.track          (str)    Track name
#   state.weather        (str)    'dry' | 'damp' | 'wet' | 'drying'
#   state.safety_car     (bool)   Safety car deployed
#   state.safety_car_laps_left (int)
#   state.track_temp     (float)  Track temperature
#   state.cars           (list)   All cars in race
#
# Available on 'my_car':
#   my_car.car_id        (str)    Your car ID
#   my_car.position      (int)    Current position
#   my_car.gap_to_leader (float)  Gap to leader in seconds
#   my_car.compound      (str)    Current tyre compound
#   my_car.tyre_age      (int)    Laps on current tyres
#   my_car.fuel_kg       (float)  Remaining fuel
#   my_car.pit_count     (int)    Number of pit stops made
#   my_car.pit_laps      (list)   Laps where you pitted
#   my_car.last_lap_time (float)  Last lap time in seconds
#   my_car.drs_available (bool)   DRS available
#   my_car.compounds_used (list)  Compounds used so far
#   my_car.beliefs       (dict)   Bayesian beliefs about rivals
#     my_car.beliefs[rival_id].estimated_tyre_age
#     my_car.beliefs[rival_id].pit_probability_next_5_laps
#
# Return: {"pit": bool, "compound": str}
#   compound: "SOFT" | "MEDIUM" | "HARD" | "INTERMEDIATE" | "WET"
# ───────────────────────────────────────────────────────────

`;

export default function StrategyEditor({
  initialCode,
  onSubmit,
  onTest,
  submitting,
  testing,
}: Props) {
  const [code, setCode] = useState(TYPE_HINTS + initialCode);

  return (
    <div className="flex flex-col gap-4">
      <div className="card overflow-hidden">
        <div className="px-4 py-2.5 border-b border-pit-border flex items-center justify-between">
          <span className="section-label">Strategy Code</span>
          <span className="text-[10px] text-pit-muted font-mono">Python</span>
        </div>
        <MonacoEditor
          height="500px"
          language="python"
          theme="vs-dark"
          value={code}
          onChange={(val) => setCode(val || "")}
          options={{
            minimap: { enabled: false },
            fontSize: 13,
            fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
            lineNumbers: "on",
            scrollBeyondLastLine: false,
            padding: { top: 12, bottom: 12 },
            wordWrap: "on",
            renderLineHighlight: "gutter",
            cursorBlinking: "smooth",
            smoothScrolling: true,
            bracketPairColorization: { enabled: true },
          }}
        />
      </div>

      <div className="flex items-center gap-3">
        {onTest && (
          <button onClick={() => onTest(code)} disabled={testing} className="btn-secondary">
            {testing ? "Running..." : "Test vs Bots"}
          </button>
        )}
        <button onClick={() => onSubmit(code)} disabled={submitting} className="btn-primary">
          {submitting ? "Submitting..." : "Submit Strategy"}
        </button>
      </div>
    </div>
  );
}
