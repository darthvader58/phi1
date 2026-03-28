"use client";

import { useState } from "react";
import dynamic from "next/dynamic";

const MonacoEditor = dynamic(() => import("@monaco-editor/react"), {
  ssr: false,
  loading: () => (
    <div className="h-96 bg-gray-900 flex items-center justify-center text-gray-500 font-mono">
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
    <div className="flex flex-col gap-3">
      <div className="border border-gray-700 rounded-lg overflow-hidden">
        <MonacoEditor
          height="480px"
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
            padding: { top: 10 },
            wordWrap: "on",
          }}
        />
      </div>
      <div className="flex gap-2">
        {onTest && (
          <button
            onClick={() => onTest(code)}
            disabled={testing}
            className="px-4 py-2 bg-gray-700 hover:bg-gray-600 text-gray-200
                       rounded font-mono text-sm disabled:opacity-50 transition-colors"
          >
            {testing ? "Testing..." : "Test vs Bots"}
          </button>
        )}
        <button
          onClick={() => onSubmit(code)}
          disabled={submitting}
          className="px-4 py-2 bg-red-700 hover:bg-red-600 text-white
                     rounded font-mono text-sm font-bold disabled:opacity-50 transition-colors"
        >
          {submitting ? "Submitting..." : "Submit Strategy"}
        </button>
      </div>
    </div>
  );
}
