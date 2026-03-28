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
  const [history, setHistory] = useState<any[]>([]);

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
      // For now, just validate
      const result = await api.testBot(code, testTrack, 10);
      setMessage("Strategy validated successfully! Join a race to use it.");
      setTestResult(result);
    } catch (err: any) {
      setMessage(`Error: ${err.message}`);
    }
    setSubmitting(false);
  }

  return (
    <div className="max-w-6xl mx-auto px-4 py-8">
      <h1 className="text-2xl font-bold mb-2 text-gray-200">Strategy Editor</h1>
      <p className="text-sm text-gray-500 mb-6">
        Write a Python function that decides when to pit and which compound to use.
        Test it against the built-in bots before racing.
      </p>

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
            <p className={`text-sm mt-2 ${message.includes("Error") ? "text-red-400" : "text-green-400"}`}>
              {message}
            </p>
          )}
        </div>

        {/* Test controls + results */}
        <div className="space-y-4">
          <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
            <h3 className="text-sm font-bold text-gray-300 mb-2">TEST SETTINGS</h3>
            <div className="space-y-2">
              <div>
                <label className="text-xs text-gray-500 block mb-1">Track</label>
                <select
                  value={testTrack}
                  onChange={(e) => setTestTrack(e.target.value)}
                  className="w-full bg-gray-800 border border-gray-700 rounded px-3 py-1.5
                             text-sm text-gray-200 focus:border-red-500 focus:outline-none"
                >
                  {["bahrain", "monaco", "monza", "spa", "silverstone", "suzuka"].map((t) => (
                    <option key={t} value={t}>{t.charAt(0).toUpperCase() + t.slice(1)}</option>
                  ))}
                </select>
              </div>
            </div>
          </div>

          {testResult && (
            <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
              <h3 className="text-sm font-bold text-gray-300 mb-2">TEST RESULT</h3>
              <div className="space-y-1">
                {testResult.standings?.map((car: any) => (
                  <div key={car.car_id} className="flex items-center gap-2 text-xs">
                    <span className={`w-8 text-right font-bold ${
                      car.car_id === "USER" ? "text-green-400" : "text-gray-400"
                    }`}>
                      P{car.position}
                    </span>
                    <span className={`font-bold ${
                      car.car_id === "USER" ? "text-green-300" : "text-gray-300"
                    }`}>
                      {car.car_id}
                    </span>
                    <span className="text-gray-500">
                      {car.retired ? "DNF" : `+${car.gap_to_leader.toFixed(1)}s`}
                    </span>
                    <span className="text-gray-600">
                      {car.pit_count}s
                    </span>
                  </div>
                ))}
              </div>
              {testResult.events?.length > 0 && (
                <div className="mt-3 border-t border-gray-800 pt-2 max-h-40 overflow-y-auto">
                  {testResult.events.slice(0, 20).map((e: any, i: number) => (
                    <div key={i} className="text-[10px] text-gray-500 font-mono">
                      L{e.lap} {e.type}: {e.detail}
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}

          <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
            <h3 className="text-sm font-bold text-gray-300 mb-2">BUILT-IN BOTS</h3>
            <div className="space-y-2 text-xs text-gray-400">
              <div><span className="text-red-400 font-bold">VEL-01</span> — Greedy threshold (pit when deg &gt; 2.2s)</div>
              <div><span className="text-purple-400 font-bold">NXS-07</span> — Undercut hunter (watches gaps + rival tyres)</div>
              <div><span className="text-cyan-400 font-bold">WXP-23</span> — Weather prophet (holds for SC windows)</div>
              <div><span className="text-yellow-400 font-bold">EQL-44</span> — Nash equilibrium (optimal pit timing)</div>
              <div><span className="text-green-400 font-bold">AGR-33</span> — Aggressive 2-stop (fixed windows)</div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
