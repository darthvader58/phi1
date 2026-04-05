import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId, saveBackendPlayerCredentials, upsertPlayerProfile } from "@/lib/repositories";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

function slugifyUsername(value: string) {
  return value
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 24);
}

function candidateUsernames(user: { id: string; name?: string | null; email?: string | null }) {
  const emailBase = user.email?.split("@")[0] ?? "";
  const nameBase = user.name ?? "";
  const bases = [nameBase, emailBase, `driver-${user.id.slice(0, 8)}`]
    .map(slugifyUsername)
    .filter(Boolean);

  const uniqueBases = [...new Set(bases)];
  const suffix = user.id.replace(/[^a-zA-Z0-9]/g, "").slice(-6).toLowerCase() || "pit";

  return [
    ...uniqueBases,
    ...uniqueBases.map((base) => `${base}-${suffix}`),
    `pit-${suffix}`
  ];
}

async function isBackendApiKeyValid(apiKey: string) {
  const response = await fetch(`${API_BASE}/api/matchmaking/suggest`, {
    method: "GET",
    headers: { "x-api-key": apiKey },
    cache: "no-store"
  });

  return response.ok;
}

export async function POST(request: Request) {
  const session = await auth();

  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in." }, { status: 401 });
  }

  await upsertPlayerProfile({
    id: session.user.id,
    name: session.user.name,
    email: session.user.email,
    image: session.user.image
  });

  const profile = await getPlayerProfileByUserId(session.user.id);
  const body = (await request.json().catch(() => ({}))) as { force?: boolean };
  const force = Boolean(body.force);

  if (!force && profile?.backendApiKey && profile?.backendUsername) {
    if (await isBackendApiKeyValid(String(profile.backendApiKey))) {
      return NextResponse.json({
        username: String(profile.backendUsername),
        apiKey: String(profile.backendApiKey)
      });
    }
  }

  if (profile?.backendUsername && profile?.backendApiKey && force) {
    return NextResponse.json({
      error: "Stored backend credentials are invalid. Clear the player mapping or rotate the backend player manually."
    }, { status: 409 });
  }

  const usernames = candidateUsernames({
    id: session.user.id,
    name: session.user.name,
    email: session.user.email
  });

  let lastError = "Unable to provision backend player.";

  for (const username of usernames) {
    const response = await fetch(`${API_BASE}/api/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, team_name: "Pit Wall" }),
      cache: "no-store"
    });

    const payload = (await response.json().catch(() => ({}))) as {
      id?: string;
      username?: string;
      api_key?: string;
      detail?: string;
    };

    if (response.ok && payload.api_key && payload.username) {
      await saveBackendPlayerCredentials(session.user.id, {
        backendUsername: payload.username,
        backendApiKey: payload.api_key,
        backendPlayerId: payload.id ?? null
      });

      return NextResponse.json({
        username: payload.username,
        apiKey: payload.api_key
      });
    }

    if (response.status !== 400 || payload.detail !== "Username already taken") {
      lastError = payload.detail || "Unable to register backend player.";
      break;
    }
  }

  return NextResponse.json({ error: lastError }, { status: 500 });
}
