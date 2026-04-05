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
    if (triggerLabel) return triggerLabel;
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
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name, email, password })
        });
        const payload = (await response.json()) as { error?: string };
        if (!response.ok) throw new Error(payload.error || "Unable to create account.");
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
      <button className="btn-primary" onClick={() => setOpen(true)} type="button">
        {buttonLabel}
      </button>

      {open ? (
        <div className="fixed inset-0 z-[70] bg-black/75 backdrop-blur-sm flex items-center justify-center p-4">
          <div className="card w-full max-w-md p-6 relative">
            <button
              className="absolute top-3 right-3 text-pit-muted hover:text-white text-xl"
              onClick={() => setOpen(false)}
              type="button"
              aria-label="Close"
            >
              ×
            </button>

            <div className="mb-5">
              <p className="section-label text-f1-red mb-2">Driver Authentication</p>
              <h2 className="text-2xl font-extrabold text-white">
                {mode === "login" ? "Jump back into the pit wall." : "Create your paddock account."}
              </h2>
            </div>

            <div className="grid grid-cols-2 gap-2 mb-4">
              <button
                className={`rounded-lg px-4 py-2 text-sm font-semibold border transition-colors ${
                  mode === "login"
                    ? "border-f1-red/40 bg-f1-red/10 text-white"
                    : "border-pit-border text-pit-muted hover:text-white"
                }`}
                onClick={() => setMode("login")}
                type="button"
              >
                Login
              </button>
              <button
                className={`rounded-lg px-4 py-2 text-sm font-semibold border transition-colors ${
                  mode === "signup"
                    ? "border-f1-red/40 bg-f1-red/10 text-white"
                    : "border-pit-border text-pit-muted hover:text-white"
                }`}
                onClick={() => setMode("signup")}
                type="button"
              >
                Signup
              </button>
            </div>

            {googleEnabled ? (
              <button
                className="btn-secondary w-full mb-4"
                disabled={pending}
                onClick={() => signIn("google", { callbackUrl: "/" })}
                type="button"
              >
                Google OAuth
              </button>
            ) : null}

            <form className="space-y-4" onSubmit={handleSubmit}>
              {mode === "signup" ? (
                <div>
                  <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">
                    Display name
                  </label>
                  <input className="input" name="name" placeholder="Charles Strategy" required type="text" />
                </div>
              ) : null}

              <div>
                <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Email</label>
                <input className="input" name="email" placeholder="driver@pitwall.dev" required type="email" />
              </div>

              <div>
                <label className="text-[10px] text-pit-muted uppercase tracking-wider block mb-1.5">Password</label>
                <input className="input" name="password" placeholder="Minimum 8 characters" required type="password" />
              </div>

              {error ? <p className="text-f1-red text-xs">{error}</p> : null}

              <button className="btn-primary w-full" disabled={pending} type="submit">
                {pending ? "Working..." : mode === "login" ? "Login" : "Create account"}
              </button>
            </form>
          </div>
        </div>
      ) : null}
    </>
  );
}
