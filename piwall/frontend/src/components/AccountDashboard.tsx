type DashboardData = {
  profile: {
    name: string;
    email: string;
    image: string | null;
    createdAt: string | null;
  } | null;
  stats: {
    totalSubmissions: number;
    totalRaces: number;
    wins: number;
    podiums: number;
    averageFinish: number | null;
  };
  submissions: Array<{
    id: string;
    track: string;
    title: string;
    stintPlan: string;
    riskLevel: string;
    notes: string;
    createdAt: string | null;
  }>;
  raceRecords: Array<{
    id: string;
    track: string;
    seasonLabel: string;
    gridPosition: number;
    finishPosition: number;
    lapsCompleted: number;
    fieldSize: number;
    notes: string;
    status: string;
    createdAt: string | null;
  }>;
};

type AccountDashboardProps = {
  data: DashboardData;
};

function formatDate(input: string | null) {
  if (!input) {
    return "Just now";
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(input));
}

export default function AccountDashboard({ data }: AccountDashboardProps) {
  return (
    <section className="page-shell">
      <div className="page-header">
        <div>
          <p className="eyebrow">Driver Account</p>
          <h1>{data.profile?.name || "Pit Wall Driver"}</h1>
          <p className="lede">
            Mongo-backed profile data for submissions, race history, and season performance tracking.
          </p>
        </div>
      </div>

      <div className="stats-grid">
        <article className="stat-card">
          <span>Submissions</span>
          <strong>{data.stats.totalSubmissions}</strong>
        </article>
        <article className="stat-card">
          <span>Races logged</span>
          <strong>{data.stats.totalRaces}</strong>
        </article>
        <article className="stat-card">
          <span>Podiums</span>
          <strong>{data.stats.podiums}</strong>
        </article>
        <article className="stat-card">
          <span>Average finish</span>
          <strong>{data.stats.averageFinish ?? "—"}</strong>
        </article>
      </div>

      <div className="content-grid">
        <section className="panel">
          <div className="panel-heading">
            <h2>Strategy submissions</h2>
            <p>Saved to MongoDB from the strategy workspace.</p>
          </div>

          {data.submissions.length ? (
            <div className="stack">
              {data.submissions.map((submission) => (
                <article className="entry-card" key={submission.id}>
                  <div className="entry-row">
                    <strong>{submission.title}</strong>
                    <span className="tag">{submission.riskLevel}</span>
                  </div>
                  <p>{submission.track}</p>
                  <p>{submission.stintPlan}</p>
                  {submission.notes ? <p className="muted">{submission.notes}</p> : null}
                  <small>{formatDate(submission.createdAt)}</small>
                </article>
              ))}
            </div>
          ) : (
            <p className="empty-state">No submissions yet. Save one from the strategy page.</p>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <h2>Race record</h2>
            <p>Track your finishes, field size, and result status by event.</p>
          </div>

          {data.raceRecords.length ? (
            <div className="table-shell">
              <table>
                <thead>
                  <tr>
                    <th>Race</th>
                    <th>Grid</th>
                    <th>Finish</th>
                    <th>Status</th>
                    <th>Logged</th>
                  </tr>
                </thead>
                <tbody>
                  {data.raceRecords.map((record) => (
                    <tr key={record.id}>
                      <td>
                        <strong>{record.track}</strong>
                        <small>{record.seasonLabel}</small>
                      </td>
                      <td>P{record.gridPosition}</td>
                      <td>P{record.finishPosition}</td>
                      <td>{record.status}</td>
                      <td>{formatDate(record.createdAt)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="empty-state">No races logged yet. Add one from the lobby page.</p>
          )}
        </section>
      </div>
    </section>
  );
}
