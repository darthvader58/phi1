import { auth } from "@/lib/auth";
import StrategySubmissionForm from "@/components/StrategySubmissionForm";

export const dynamic = "force-dynamic";

export default async function StrategyPage() {
  const session = await auth();

  return (
    <section className="page-shell">
      <div className="page-header">
        <div>
          <p className="eyebrow">Strategy Workspace</p>
          <h1>Build and store your race submissions.</h1>
          <p className="lede">
            Save tyre windows, stint plans, and risk notes so the account page can track every strategic
            submission you make.
          </p>
        </div>
      </div>

      <StrategySubmissionForm disabled={!session?.user} />
    </section>
  );
}
