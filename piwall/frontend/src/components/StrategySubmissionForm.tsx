"use client";

import { FormEvent, useState } from "react";

type StrategySubmissionFormProps = {
  disabled: boolean;
};

export default function StrategySubmissionForm({ disabled }: StrategySubmissionFormProps) {
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setSubmitting(true);

    const formData = new FormData(event.currentTarget);
    const payload = Object.fromEntries(formData.entries());

    const response = await fetch("/api/submissions", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const body = (await response.json()) as { error?: string };

    if (!response.ok) {
      setMessage(body.error || "Unable to save submission.");
      setSubmitting(false);
      return;
    }

    event.currentTarget.reset();
    setMessage("Submission saved to MongoDB and ready for review on your account page.");
    setSubmitting(false);
  }

  return (
    <form className="panel form-panel" onSubmit={handleSubmit}>
      <div className="panel-heading">
        <h2>Save a strategy submission</h2>
        <p>Each submission is written to MongoDB and surfaced on the account dashboard.</p>
      </div>

      <label>
        Track
        <input disabled={disabled || submitting} name="track" placeholder="Monza" required type="text" />
      </label>
      <label>
        Submission title
        <input
          disabled={disabled || submitting}
          name="title"
          placeholder="Lap 17 undercut attack"
          required
          type="text"
        />
      </label>
      <label>
        Stint plan
        <textarea
          disabled={disabled || submitting}
          name="stintPlan"
          placeholder="Medium to Hard, pit on lap 17 if degradation exceeds 0.07."
          required
          rows={4}
        />
      </label>
      <label>
        Risk level
        <select defaultValue="Balanced" disabled={disabled || submitting} name="riskLevel" required>
          <option>Aggressive</option>
          <option>Balanced</option>
          <option>Conservative</option>
        </select>
      </label>
      <label>
        Notes
        <textarea
          disabled={disabled || submitting}
          name="notes"
          placeholder="Protect against safety car windows and traffic at pit exit."
          rows={3}
        />
      </label>

      <button className="button button-primary" disabled={disabled || submitting} type="submit">
        {disabled ? "Sign in to submit" : submitting ? "Saving..." : "Save submission"}
      </button>

      {message ? <p className="form-note">{message}</p> : null}
    </form>
  );
}
