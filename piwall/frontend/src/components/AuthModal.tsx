"use client";

import { FormEvent, useEffect, useMemo, useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import { createPortal } from "react-dom";

type AuthModalProps = {
  defaultOpen?: boolean;
  googleEnabled: boolean;
  hideTrigger?: boolean;
  buttonClassName?: string;
  triggerLabel?: string;
};

type Mode = "login" | "signup";

export default function AuthModal({
  defaultOpen = false,
  googleEnabled,
  hideTrigger = false,
  buttonClassName,
  triggerLabel
}: AuthModalProps) {
  const router = useRouter();
  const [open, setOpen] = useState(defaultOpen);
  const [mode, setMode] = useState<Mode>("login");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);
  const [mounted, setMounted] = useState(false);

  const buttonLabel = useMemo(() => {
    if (triggerLabel) return triggerLabel;
    return googleEnabled ? "Continue with Google" : "Login / Signup";
  }, [googleEnabled, triggerLabel]);

  useEffect(() => {
    setMounted(true);
  }, []);

  useEffect(() => {
    function handleOpenAuthModal() {
      setOpen(true);
    }

    window.addEventListener("pitwall-open-auth", handleOpenAuthModal);
    return () => window.removeEventListener("pitwall-open-auth", handleOpenAuthModal);
  }, []);

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
      {hideTrigger ? null : (
        <button className={buttonClassName || "btn-primary"} onClick={() => setOpen(true)} type="button">
          {buttonLabel}
        </button>
      )}

      {mounted && open
        ? createPortal(
        <div className="fixed inset-0 z-[70] bg-black/75 backdrop-blur-sm flex items-start sm:items-center justify-center p-4 pt-20 sm:pt-4 overflow-y-auto">
          <div className="card w-full max-w-md p-6 relative max-h-[calc(100vh-2rem)] overflow-y-auto">
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
                className="w-full mb-4 h-11 rounded-md border border-[#dadce0] bg-white text-[#3c4043]
                           hover:bg-[#f8f9fa] transition-colors flex items-center justify-center gap-3
                           text-sm font-medium shadow-none"
                disabled={pending}
                onClick={() => signIn("google", { callbackUrl: "/" })}
                type="button"
              >
                <svg width="18" height="18" viewBox="0 0 18 18" aria-hidden="true">
                  <path
                    fill="#4285F4"
                    d="M17.64 9.2c0-.64-.06-1.25-.16-1.84H9v3.48h4.84a4.14 4.14 0 0 1-1.8 2.72v2.26h2.92c1.7-1.56 2.68-3.86 2.68-6.62Z"
                  />
                  <path
                    fill="#34A853"
                    d="M9 18c2.43 0 4.47-.8 5.96-2.18l-2.92-2.26c-.81.54-1.84.86-3.04.86-2.34 0-4.32-1.58-5.03-3.7H.96v2.33A9 9 0 0 0 9 18Z"
                  />
                  <path
                    fill="#FBBC05"
                    d="M3.97 10.72A5.41 5.41 0 0 1 3.69 9c0-.6.1-1.18.28-1.72V4.95H.96A9 9 0 0 0 0 9c0 1.45.35 2.82.96 4.05l3.01-2.33Z"
                  />
                  <path
                    fill="#EA4335"
                    d="M9 3.58c1.32 0 2.5.45 3.44 1.33l2.58-2.58C13.46.89 11.43 0 9 0A9 9 0 0 0 .96 4.95l3.01 2.33c.71-2.12 2.69-3.7 5.03-3.7Z"
                  />
                </svg>
                <span>Sign in with Google</span>
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
            ,
            document.body
          )
        : null}
    </>
  );
}
