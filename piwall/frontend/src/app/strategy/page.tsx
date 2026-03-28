"use client";

import { useState, useEffect } from "react";
import { api } from "@/lib/api";
import StrategyEditor from "@/components/StrategyEditor";

export default function StrategyPage() {
  const [template, setTemplate] = useState("");
  const [testResult, setTestResult] = useState<any>(null);
  const [testTrack, setTestTrack] = useState("bahrain");
  const [testing, setTesting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState("");

  useEffect(() => {
    api.getTemplate().then((res) => setTemplate(res.template)).catch(() => {});
  }, []);

  async function handleTest(code: string) {
    setTesting(true);
    setTestResult(null);
    setMessage("");
    try {
      const result = await api.testBot(code, testTrack, 20);
      setTestResult(result);
    } catch (err: any) {
      setMessage(`Test error: ${err.message}`);
    }
    setTesting(false);
  }

  async function handleSubmit(code: string) {
    setSubmitting(true);
    setMessage("");
    try {
      const result = await api.testBot(code, testTrack, 10);
      setMessage("Strategy validated successfully! Join a race to use it.");
      setTestResult(result);
    } catch (err: any) {
      setMessage(`Error: ${err.message}`);
    }
    setSubmitting(false);
  }

  const bots = [
    { id: "VEL-01", color: "text-f1-red", desc: "Greedy threshold — pits when deg > 2.2s" },
    { id: "NXS-07", color: "text-purple-400", desc: "Undercut hunter — watches gaps + rival tyres" },
    { id: "WXP-23", color: "text-cyan-400", desc: "Weather prophet — holds for SC windows" },
    { id: "EQL-44", color: "text-yellow-400", desc: "Nash equilibrium — optimal pit timing" },
    { id: "AGR-33", color: "text-green-400", desc: "Aggressive 2-stop — fixed pit windows" },
  ];

  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="mb-8">
        <h1 className="text-3xl font-extrabold text-white tracking-tight">Strategy Editor</h1>
        <p className="text-sm text-pit-text mt-1">
          Write a Python function that decides when to pit and which compound to use.
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* Editor */}
        <div className="lg:col-span-2">
          {template && (
            <StrategyEditor
              initialCode={template}
              onSubmit={handleSubmit}
              onTest={handleTest}
              submitting={submitting}
              testing={testing}
            />
          )}
          {message && (
            <p className={`text-sm mt-3 ${message.includes("Error") ? "text-f1-red" : "text-green-400"}`}>
              {message}
            </p>
          )}
        </div>

        {/* Sidebar */}
        <div className="space-y-5">
          {/* Test settings */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4">
              <div className="accent-line" />
              <span className="section-label">Test Settings</span>
            </div>
            <div>
              <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Track</label>
              <select
                value={testTrack}
                onChange={(e) => setTestTrack(e.target.value)}
                className="input w-full"
              >
                {["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"].map((t) => (
                  <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                ))}
              </select>
            </div>
          </div>

          {/* Test results */}
          {testResult && (
            <div className="card overflow-hidden">
              <div className="px-5 py-3.5 border-b border-pit-border">
                <span className="section-label">Test Result</span>
              </div>
              <div className="p-4 space-y-1">
                {testResult.standings?.map((car: any) => (
                  <div key={car.car_id} className="flex items-center gap-2 text-xs px-2 py-1.5 rounded-md hover:bg-white/[0.02]">
                    <span className={`w-8 text-right font-extrabold tabular-nums ${
                      car.car_id === "USER" ? "text-f1-red" : "text-pit-muted"
                    }`}>
                      P{car.position}
                    </span>
                    <span className={`font-bold ${
                      car.car_id === "USER" ? "text-white" : "text-pit-text"
                    }`}>
                      {car.car_id}
                    </span>
                    <span className="text-pit-muted font-mono text-[11px]">
                      {car.retired ? "DNF" : `+${car.gap_to_leader.toFixed(1)}s`}
                    </span>
                    <span className="text-pit-muted text-[10px] ml-auto">
                      {car.pit_count}s
                    </span>
                  </div>
                ))}
              </div>
              {testResult.events?.length > 0 && (
                <div className="border-t border-pit-border p-3 max-h-40 overflow-y-auto">
                  {testResult.events.slice(0, 20).map((e: any, i: number) => (
                    <div key={i} className="text-[10px] text-pit-muted font-mono leading-relaxed">
                      L{e.lap} {e.type}: {e.detail}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          {/* Bots reference */}
          <div className="card p-5">
            <div className="flex items-center gap-2 mb-4">
              <div className="accent-line" />
              <span className="section-label">Built-in Bots</span>
            </div>
            <div className="space-y-3">
              {bots.map((bot) => (
                <div key={bot.id} className="text-xs">
                  <span className={`${bot.color} font-bold`}>{bot.id}</span>
                  <span className="text-pit-text ml-2">{bot.desc}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
