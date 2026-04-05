"use client";

import { useEffect } from "react";
import { useSession } from "next-auth/react";

const API_KEY_STORAGE = "piwall_api_key";
const USERNAME_STORAGE = "piwall_username";
const SESSION_USER_STORAGE = "piwall_session_user_id";

export default function BackendPlayerSync() {
  const { data: session, status } = useSession();

  useEffect(() => {
    if (status === "unauthenticated") {
      localStorage.removeItem(API_KEY_STORAGE);
      localStorage.removeItem(USERNAME_STORAGE);
      localStorage.removeItem(SESSION_USER_STORAGE);
      window.dispatchEvent(new Event("piwall-backend-auth-changed"));
      return;
    }

    if (status !== "authenticated" || !session?.user?.id) {
      return;
    }

    const existingKey = localStorage.getItem(API_KEY_STORAGE);
    const existingUsername = localStorage.getItem(USERNAME_STORAGE);
    const existingSessionUserId = localStorage.getItem(SESSION_USER_STORAGE);

    if (existingSessionUserId && existingSessionUserId !== session.user.id) {
      localStorage.removeItem(API_KEY_STORAGE);
      localStorage.removeItem(USERNAME_STORAGE);
      localStorage.removeItem(SESSION_USER_STORAGE);
      window.dispatchEvent(new Event("piwall-backend-auth-changed"));
    }

    if (existingKey && existingUsername && existingSessionUserId === session.user.id) {
      return;
    }

    let cancelled = false;

    async function syncBackendPlayer() {
      const response = await fetch("/api/backend-player", {
        method: "POST",
        cache: "no-store"
      });

      const payload = (await response.json().catch(() => ({}))) as {
        apiKey?: string;
        username?: string;
      };

      if (!cancelled && response.ok && payload.apiKey && payload.username) {
        localStorage.setItem(API_KEY_STORAGE, payload.apiKey);
        localStorage.setItem(USERNAME_STORAGE, payload.username);
        localStorage.setItem(SESSION_USER_STORAGE, session.user.id);
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
