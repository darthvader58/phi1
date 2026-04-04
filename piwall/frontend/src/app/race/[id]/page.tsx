import { notFound } from "next/navigation";
import { getRaceRecord } from "@/lib/repositories";

export const dynamic = "force-dynamic";

function formatDate(input: string | null) {
  if (!input) {
    return "Unknown";
  }

  return new Intl.DateTimeFormat("en-US", {
    dateStyle: "full",
    timeStyle: "short"
  }).format(new Date(input));
}

export default async function RaceDetailPage({ params }: { params: { id: string } }) {
  const record = await getRaceRecord(params.id);

  if (!record) {
    notFound();
  }

  return (
    <section className="page-shell">
      <div className="page-header">
        <div>
          <p className="eyebrow">Race Detail</p>
          <h1>{record.track}</h1>
          <p className="lede">{record.seasonLabel}</p>
        </div>
      </div>

      <div className="stats-grid">
        <article className="stat-card">
          <span>Grid</span>
          <strong>P{record.gridPosition}</strong>
        </article>
        <article className="stat-card">
          <span>Finish</span>
          <strong>P{record.finishPosition}</strong>
        </article>
        <article className="stat-card">
          <span>Laps</span>
          <strong>{record.lapsCompleted}</strong>
        </article>
        <article className="stat-card">
          <span>Status</span>
          <strong>{record.status}</strong>
        </article>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <h2>Event notes</h2>
          <p>{formatDate(record.createdAt)}</p>
        </div>
        <p>{record.notes || "No notes were recorded for this result."}</p>
      </section>
    </section>
  );
}
