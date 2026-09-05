import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId, saveBackendPlayerCredentials, upsertPlayerProfile } from "@/lib/repositories";

// Server-side only: this module runs in the Next.js process, where
// "localhost" is the frontend container, not the backend. BACKEND_API_URL is
// the in-network address (http://backend:8000 under compose);
// NEXT_PUBLIC_API_URL stays as a fallback so existing single-host setups,
// where both are localhost, keep working unchanged.
const API_BASE =
  process.env.BACKEND_API_URL || process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

// Proves to the backend that this request came from our server rather than
// the open internet, so /api/register can bucket its rate limit on a real
// user id instead of a socket address every signup shares. Server-side only
// — a NEXT_PUBLIC_ prefix here would inline the secret into the browser
// bundle at build time and publish it to every visitor.
const PROVISIONING_SECRET = process.env.PROVISIONING_SECRET || "";

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
        username: String(profile.backendUsername)
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
      headers: {
        "Content-Type": "application/json",
        "x-provision-secret": PROVISIONING_SECRET,
        "x-provision-subject": session.user.id
      },
      body: JSON.stringify({ username, team_name: "Pit Wall" }),
      cache: "no-store"
    });

    // Surface throttling as throttling. The backend's 429 body is keyed
    // "error", not "detail", so the generic handling below would turn it
    // into an opaque 500 and drop the Retry-After it came with. Return
    // rather than break: another candidate would only burn more of the
    // caller's budget.
    if (response.status === 429) {
      return NextResponse.json(
        { error: "Too many registration attempts. Please try again shortly." },
        { status: 429 }
      );
    }

    // Registration is closed unless PROVISIONING_SECRET matches on both
    // sides. A misconfigured deployment should say so once, not retry.
    if (response.status === 404) {
      return NextResponse.json(
        { error: "Player provisioning is not configured on this server." },
        { status: 503 }
      );
    }

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
        username: payload.username
      });
    }

    if (response.status !== 400 || payload.detail !== "Username already taken") {
      lastError = payload.detail || "Unable to register backend player.";
      break;
    }
  }

  return NextResponse.json({ error: lastError }, { status: 500 });
}
