import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId } from "@/lib/repositories";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

const SEGMENT_RE = /^[A-Za-z0-9_.-]+$/;

function isSafeSegment(segment: string): boolean {
  if (segment === "." || segment === "..") return false;
  return SEGMENT_RE.test(segment);
}

function scrubApiKey(rawBody: string): string {
  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody);
  } catch {
    return rawBody;
  }

  if (parsed && typeof parsed === "object" && !Array.isArray(parsed) && "api_key" in parsed) {
    delete (parsed as Record<string, unknown>).api_key;
  }

  return JSON.stringify(parsed);
}

async function proxy(request: Request, path: string[]) {
  // Registration creates a key rather than needing one — it is not a game
  // action and does not belong behind this key-scoped proxy. 404, not 403,
  // so the route's existence isn't confirmed.
  if (path[0] === "register") {
    return NextResponse.json({ error: "Not found." }, { status: 404 });
  }

  if (path.length === 0 || !path.every(isSafeSegment)) {
    return NextResponse.json({ error: "Not found." }, { status: 404 });
  }

  const session = await auth();
  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in." }, { status: 401 });
  }

  const profile = await getPlayerProfileByUserId(session.user.id);
  const apiKey = profile?.backendApiKey;
  if (!apiKey) {
    return NextResponse.json({ error: "No backend player provisioned." }, { status: 409 });
  }

  const body = request.method === "GET" || request.method === "HEAD"
    ? undefined
    : await request.text();

  const incomingUrl = new URL(request.url);
  const upstreamUrl = `${API_BASE}/api/${path.join("/")}${incomingUrl.search}`;

  const upstream = await fetch(upstreamUrl, {
    method: request.method,
    headers: { "Content-Type": "application/json", "x-api-key": String(apiKey) },
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
