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
  if (!input) return "Just now";
  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "medium",
    timeStyle: "short"
  }).format(new Date(input));
}

export default function AccountDashboard({ data }: AccountDashboardProps) {
  return (
    <div className="max-w-6xl mx-auto px-4 py-10">
      <div className="mb-8">
        <p className="section-label text-f1-red mb-2">Driver Account</p>
        <h1 className="text-3xl font-extrabold text-white tracking-tight">{data.profile?.name || "Pit Wall Driver"}</h1>
        <p className="text-sm text-pit-text mt-2">
          Mongo-backed profile data for submissions, race history, and season performance tracking.
        </p>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mb-6">
        {[
          { label: "Submissions", value: data.stats.totalSubmissions },
          { label: "Races", value: data.stats.totalRaces },
          { label: "Podiums", value: data.stats.podiums },
          { label: "Average Finish", value: data.stats.averageFinish ?? "—" }
        ].map((stat) => (
          <div key={stat.label} className="card p-4 text-center">
            <div className="stat-value text-xl">{stat.value}</div>
            <div className="text-[10px] text-pit-muted uppercase tracking-wider mt-1">{stat.label}</div>
          </div>
        ))}
      </div>

      <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
        <section className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border">
            <h2 className="text-white font-bold">Strategy Submissions</h2>
            <p className="text-xs text-pit-muted mt-1">Saved from the strategy workspace into MongoDB.</p>
          </div>

          <div className="p-4 space-y-3">
            {data.submissions.length ? (
              data.submissions.map((submission) => (
                <article key={submission.id} className="rounded-lg border border-pit-border p-4 bg-pit-surface/40">
                  <div className="flex items-center justify-between gap-3 mb-2">
                    <strong className="text-white">{submission.title}</strong>
                    <span className="badge bg-f1-red/10 text-f1-red">{submission.riskLevel}</span>
                  </div>
                  <p className="text-xs text-pit-muted mb-2">{submission.track}</p>
                  <pre className="text-xs text-pit-text whitespace-pre-wrap font-mono leading-relaxed">
                    {submission.stintPlan}
                  </pre>
                  {submission.notes ? <p className="text-xs text-pit-muted mt-3">{submission.notes}</p> : null}
                  <p className="text-[10px] text-pit-muted mt-3">{formatDate(submission.createdAt)}</p>
                </article>
              ))
            ) : (
              <p className="text-pit-muted text-sm text-center py-8">No submissions yet. Save one from Strategy.</p>
            )}
          </div>
        </section>

        <section className="card overflow-hidden">
          <div className="px-5 py-3.5 border-b border-pit-border">
            <h2 className="text-white font-bold">Race Record</h2>
            <p className="text-xs text-pit-muted mt-1">Stored finishes, positions, and event notes.</p>
          </div>

          <div className="p-4">
            {data.raceRecords.length ? (
              <div className="space-y-2">
                {data.raceRecords.map((record) => (
                  <article
                    key={record.id}
                    className="rounded-lg border border-pit-border p-4 bg-pit-surface/40 flex items-start justify-between gap-4"
                  >
                    <div>
                      <strong className="text-white">{record.track}</strong>
                      <p className="text-xs text-pit-muted mt-1">{record.seasonLabel}</p>
                      {record.notes ? <p className="text-xs text-pit-text mt-2">{record.notes}</p> : null}
                      <p className="text-[10px] text-pit-muted mt-3">{formatDate(record.createdAt)}</p>
                    </div>
                    <div className="text-right">
                      <div className="text-sm font-bold text-white">P{record.finishPosition}</div>
                      <div className="text-xs text-pit-muted">from P{record.gridPosition}</div>
                      <div className="badge bg-pit-surface text-pit-text mt-2">{record.status}</div>
                    </div>
                  </article>
                ))}
              </div>
            ) : (
              <p className="text-pit-muted text-sm text-center py-8">No races logged yet. Save one from Lobby.</p>
            )}
          </div>
        </section>
      </div>
    </div>
  );
}
