"use client";

import { FormEvent, useState } from "react";

type RaceRecordFormProps = {
  disabled: boolean;
};

export default function RaceRecordForm({ disabled }: RaceRecordFormProps) {
  const [message, setMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setMessage(null);
    setSubmitting(true);

    const formData = new FormData(event.currentTarget);
    const payload = Object.fromEntries(formData.entries());

    const response = await fetch("/api/race-records", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });

    const body = (await response.json()) as { error?: string };

    if (!response.ok) {
      setMessage(body.error || "Unable to save race record.");
      setSubmitting(false);
      return;
    }

    event.currentTarget.reset();
    setMessage("Race result saved to MongoDB and available on your account page.");
    setSubmitting(false);
  }

  return (
    <form className="panel form-panel" onSubmit={handleSubmit}>
      <div className="panel-heading">
        <h2>Log a completed race</h2>
        <p>Store positions, laps completed, and status for the account history view.</p>
      </div>

      <label>
        Track
        <input disabled={disabled || submitting} name="track" placeholder="Suzuka" required type="text" />
      </label>
      <label>
        Season / event
        <input
          defaultValue="2026 Simulation Series"
          disabled={disabled || submitting}
          name="seasonLabel"
          required
          type="text"
        />
      </label>
      <div className="two-column">
        <label>
          Grid position
          <input disabled={disabled || submitting} min="1" name="gridPosition" required type="number" />
        </label>
        <label>
          Finish position
          <input disabled={disabled || submitting} min="1" name="finishPosition" required type="number" />
        </label>
      </div>
      <div className="two-column">
        <label>
          Laps completed
          <input disabled={disabled || submitting} min="1" name="lapsCompleted" required type="number" />
        </label>
        <label>
          Field size
          <input disabled={disabled || submitting} min="2" name="fieldSize" required type="number" />
        </label>
      </div>
      <label>
        Status
        <select defaultValue="Finished" disabled={disabled || submitting} name="status" required>
          <option>Finished</option>
          <option>DNF</option>
          <option>Penalty applied</option>
          <option>Safety car chaos</option>
        </select>
      </label>
      <label>
        Notes
        <textarea
          disabled={disabled || submitting}
          name="notes"
          placeholder="Held P3 despite late safety car and tyre drop-off."
          rows={3}
        />
      </label>

      <button className="button button-primary" disabled={disabled || submitting} type="submit">
        {disabled ? "Sign in to log a race" : submitting ? "Saving..." : "Save race record"}
      </button>

      {message ? <p className="form-note">{message}</p> : null}
    </form>
  );
}
