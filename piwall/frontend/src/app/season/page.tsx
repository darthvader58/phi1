import Link from "next/link";
import { getSeasonSnapshot } from "@/lib/repositories";

export const dynamic = "force-dynamic";

function formatDate(input: string | null) {
  if (!input) {
    return "Just now";
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium"
  }).format(new Date(input));
}

export default async function SeasonPage() {
  const snapshot = await getSeasonSnapshot();

  return (
    <section className="page-shell">
      <div className="page-header">
        <div>
          <p className="eyebrow">Season Snapshot</p>
          <h1>Recent account-backed race outcomes.</h1>
          <p className="lede">
            This page reads MongoDB race records and gives the app a season-level pulse instead of isolated race
            forms.
          </p>
        </div>
      </div>

      <div className="stats-grid">
        <article className="stat-card">
          <span>Recent entries</span>
          <strong>{snapshot.totalEntries}</strong>
        </article>
        <article className="stat-card">
          <span>Average finish</span>
          <strong>{snapshot.averageFinish ?? "—"}</strong>
        </article>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <h2>Recent races</h2>
          <p>Open an entry for full event details.</p>
        </div>
        {snapshot.recentRaces.length ? (
          <div className="stack">
            {snapshot.recentRaces.map((race) => (
              <Link className="entry-card entry-link" href={`/race/${race.id}`} key={race.id}>
                <div className="entry-row">
                  <strong>{race.track}</strong>
                  <span className="tag">{race.status}</span>
                </div>
                <p>{race.seasonLabel}</p>
                <small>
                  Finished P{race.finishPosition} • {formatDate(race.createdAt)}
                </small>
              </Link>
            ))}
          </div>
        ) : (
          <p className="empty-state">No race entries yet.</p>
        )}
      </section>
    </section>
  );
}
