import Link from "next/link";
import AuthModal from "@/components/AuthModal";
import { auth, googleEnabled } from "@/lib/auth";

export const dynamic = "force-dynamic";

export default async function HomePage() {
  const session = await auth();

  return (
    <section className="landing-shell">
      {!session?.user ? <AuthModal defaultOpen googleEnabled={googleEnabled} /> : null}

      <div className="hero">
        <div className="hero-copy">
          <p className="eyebrow">Realtime F1 Strategy Sandbox</p>
          <h1>Race the simulation. Save every call. Track every result.</h1>
          <p className="lede">
            The landing page now forces the auth flow when no user is signed in, the navbar exposes Google
            OAuth or your profile avatar on the far right, and your account page pulls MongoDB-backed
            submissions plus race history.
          </p>
          <div className="button-row">
            {session?.user ? (
              <Link className="button button-primary" href="/account">
                Open account
              </Link>
            ) : (
              <AuthModal googleEnabled={googleEnabled} triggerLabel="Login / Signup" />
            )}
            <Link className="button button-secondary" href="/strategy">
              Open strategy desk
            </Link>
          </div>
        </div>

        <div className="hero-panel">
          <div className="hero-panel-row">
            <span>Auth state</span>
            <strong>{session?.user ? "Signed in" : "Modal required"}</strong>
          </div>
          <div className="hero-panel-row">
            <span>Storage</span>
            <strong>MongoDB</strong>
          </div>
          <div className="hero-panel-row">
            <span>Account data</span>
            <strong>Submissions + race finishes</strong>
          </div>
        </div>
      </div>
    </section>
  );
}
