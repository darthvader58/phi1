"use client";

import { useEffect } from "react";
import { useSession } from "next-auth/react";

const USERNAME_STORAGE = "piwall_username";
const SESSION_USER_STORAGE = "piwall_session_user_id";

export default function BackendPlayerSync() {
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status === "unauthenticated") {
      localStorage.removeItem(USERNAME_STORAGE);
      localStorage.removeItem(SESSION_USER_STORAGE);
      window.dispatchEvent(new Event("piwall-backend-auth-changed"));
      return;
    }

    if (status !== "authenticated" || !session?.user?.id) {
      return;
    }

    const userId = session.user.id;
    const existingUsername = localStorage.getItem(USERNAME_STORAGE);
    const existingSessionUserId = localStorage.getItem(SESSION_USER_STORAGE);

    if (existingSessionUserId && existingSessionUserId !== userId) {
      localStorage.removeItem(USERNAME_STORAGE);
      localStorage.removeItem(SESSION_USER_STORAGE);
      window.dispatchEvent(new Event("piwall-backend-auth-changed"));
    }

    if (existingUsername && existingSessionUserId === userId) {
      return;
    }

    let cancelled = false;

    async function syncBackendPlayer() {
      const response = await fetch("/api/backend-player", {
        method: "POST",
        cache: "no-store"
      });

      const payload = (await response.json().catch(() => ({}))) as {
        username?: string;
      };

      if (!cancelled && response.ok && payload.username) {
        localStorage.setItem(USERNAME_STORAGE, payload.username);
        localStorage.setItem(SESSION_USER_STORAGE, userId);
        window.dispatchEvent(new Event("piwall-backend-auth-changed"));
      }
    }

    syncBackendPlayer().catch(() => {});

    return () => {
      cancelled = true;
    };
  }, [session?.user?.id, status]);

  return null;
}
