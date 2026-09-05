import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId } from "@/lib/repositories";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SEGMENT_RE = /^[A-Za-z0-9_.-]+$/;

function isSafeSegment(segment: string): boolean {
  if (segment === "." || segment === "..") return false;
  return SEGMENT_RE.test(segment);
}

// Endpoints the backend serves without an x_api_key, and which the game is
// meant to expose publicly. Matched against the whole resolved path, not just
// its first segment, so `season/active` opens without also opening the admin
// `season` POST. GET only, for the same reason.
const PUBLIC_GET_PATHS: RegExp[] = [
  /^leaderboard$/,
  /^races$/,
  /^race\/[^/]+$/,
  /^tracks$/,
  /^track\/[^/]+$/,
  /^player\/[^/]+$/,
  /^player\/[^/]+\/elo-history$/,
  /^player\/[^/]+\/races$/,
  /^strategy\/template$/,
  /^seasons$/,
  /^season\/active$/,
  /^season\/[^/]+\/standings$/
];

function isPublicPath(method: string, resolvedPath: string): boolean {
  if (method !== "GET" && method !== "HEAD") return false;
  return PUBLIC_GET_PATHS.some((pattern) => pattern.test(resolvedPath));
}

// Recursive: this scrubber exists precisely to survive the primary gate being
// wrong, so it must not be defeated by a key nested one level down.
function stripApiKeys(value: unknown): unknown {
  if (Array.isArray(value)) {
    return value.map(stripApiKeys);
  }

  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, child] of Object.entries(value as Record<string, unknown>)) {
      if (key === "api_key") continue;
      out[key] = stripApiKeys(child);
    }
    return out;
  }

  return value;
}

function scrubApiKey(rawBody: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody);
  } catch {
    return rawBody;
  }

  return JSON.stringify(stripApiKeys(parsed));
}

async function proxy(request: Request, path: string[]) {
  // Registration creates a key rather than needing one — it is not a game
  // action and does not belong behind this key-scoped proxy. 404, not 403,
  // so the route's existence isn't confirmed. Compared lowercased rather than
  // relying on FastAPI's routing also being case-sensitive: that is a fact
  // about a different system, and nothing here pins it.
  if (path[0]?.toLowerCase() === "register") {
    return NextResponse.json({ error: "Not found." }, { status: 404 });
  }

  if (path.length === 0 || !path.every(isSafeSegment)) {
    return NextResponse.json({ error: "Not found." }, { status: 404 });
  }

  const resolvedPath = path.join("/");
  const isPublic = isPublicPath(request.method, resolvedPath);

  // A public read takes no key upstream, so it needs neither a session nor a
  // provisioned player. Gating these was what left the game with nothing
  // public to expose: signed-out visitors got "You must be signed in." on a
  // track page and an empty leaderboard on the lobby.
  let apiKey: string | undefined;
  if (!isPublic) {
    const session = await auth();
    if (!session?.user?.id) {
      return NextResponse.json({ error: "You must be signed in." }, { status: 401 });
    }

    const profile = await getPlayerProfileByUserId(session.user.id);
    const storedKey = profile?.backendApiKey;
    if (!storedKey) {
      return NextResponse.json({ error: "No backend player provisioned." }, { status: 409 });
    }
    apiKey = String(storedKey);
  }

  const body = request.method === "GET" || request.method === "HEAD"
    ? undefined
    : await request.text();

  const incomingUrl = new URL(request.url);
  const upstreamUrl = `${API_BASE}/api/${resolvedPath}${incomingUrl.search}`;

  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (apiKey) {
    headers["x-api-key"] = apiKey;
  }

  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers,
    body,
    cache: "no-store",
  });

  const upstreamText = await upstream.text();

  return new NextResponse(scrubApiKey(upstreamText), {
    status: upstream.status,
    headers: { "Content-Type": "application/json" },
  });
}

export async function GET(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}

export async function POST(request: Request, ctx: { params: Promise<{ path: string[] }> }) {
  return proxy(request, (await ctx.params).path);
}
