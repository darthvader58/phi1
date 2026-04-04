import { auth } from "@/lib/auth";
import RaceRecordForm from "@/components/RaceRecordForm";

export const dynamic = "force-dynamic";

export default async function LobbyPage() {
  const session = await auth();

  return (
    <section className="page-shell">
      <div className="page-header">
        <div>
          <p className="eyebrow">Race Lobby</p>
          <h1>Log completed races and finishing positions.</h1>
          <p className="lede">
            This is the quickest path to build out your account race history while the simulation frontend keeps
            evolving.
          </p>
        </div>
      </div>

      <RaceRecordForm disabled={!session?.user} />
    </section>
  );
}
