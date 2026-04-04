"use client";

import { FormEvent, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";

type AuthModalProps = {
  defaultOpen?: boolean;
  googleEnabled: boolean;
  triggerLabel?: string;
};

type Mode = "login" | "signup";

export default function AuthModal({ defaultOpen = false, googleEnabled, triggerLabel }: AuthModalProps) {
  const router = useRouter();
  const [open, setOpen] = useState(defaultOpen);
  const [mode, setMode] = useState<Mode>("login");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  const buttonLabel = useMemo(() => {
    if (triggerLabel) {
      return triggerLabel;
    }

    return googleEnabled ? "Continue with Google" : "Login / Signup";
  }, [googleEnabled, triggerLabel]);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setPending(true);
    setError(null);

    const formData = new FormData(event.currentTarget);
    const name = String(formData.get("name") ?? "");
    const email = String(formData.get("email") ?? "");
    const password = String(formData.get("password") ?? "");

    try {
      if (mode === "signup") {
        const response = await fetch("/api/signup", {
          method: "POST",
          headers: {
            "Content-Type": "application/json"
          },
          body: JSON.stringify({ name, email, password })
        });

        const payload = (await response.json()) as { error?: string };

        if (!response.ok) {
          throw new Error(payload.error || "Unable to create account.");
        }
      }

      const result = await signIn("credentials", {
        email,
        password,
        redirect: false
      });

      if (result?.error) {
        throw new Error("Email or password is incorrect.");
      }

      setOpen(false);
      router.refresh();
    } catch (caughtError) {
      setError(caughtError instanceof Error ? caughtError.message : "Authentication failed.");
    } finally {
      setPending(false);
    }
  }

  return (
    <>
      <button className="button button-primary" onClick={() => setOpen(true)} type="button">
        {buttonLabel}
      </button>

      {open ? (
        <div className="modal-shell" role="dialog" aria-modal="true" aria-label="Login or signup">
          <div className="modal-card">
            <div className="modal-header">
              <div>
                <p className="eyebrow">Driver Authentication</p>
                <h2>{mode === "login" ? "Jump back into the pit wall." : "Create your paddock account."}</h2>
              </div>
              <button className="icon-button" onClick={() => setOpen(false)} type="button" aria-label="Close">
                ×
              </button>
            </div>

            <div className="segmented-control" role="tablist" aria-label="Authentication mode">
              <button
                className={mode === "login" ? "active" : ""}
                onClick={() => setMode("login")}
                type="button"
              >
                Login
              </button>
              <button
                className={mode === "signup" ? "active" : ""}
                onClick={() => setMode("signup")}
                type="button"
              >
                Signup
              </button>
            </div>

            {googleEnabled ? (
              <button
                className="button button-secondary auth-google"
                disabled={pending}
                onClick={() => signIn("google", { callbackUrl: "/" })}
                type="button"
              >
                Google OAuth
              </button>
            ) : null}

            <form className="auth-form" onSubmit={handleSubmit}>
              {mode === "signup" ? (
                <label>
                  Display name
                  <input name="name" placeholder="Charles Strategy" required type="text" />
                </label>
              ) : null}

              <label>
                Email
                <input name="email" placeholder="driver@pitwall.dev" required type="email" />
              </label>

              <label>
                Password
                <input name="password" placeholder="Minimum 8 characters" required type="password" />
              </label>

              {error ? <p className="form-error">{error}</p> : null}

              <button className="button button-primary" disabled={pending} type="submit">
                {pending ? "Working..." : mode === "login" ? "Login" : "Create account"}
              </button>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
