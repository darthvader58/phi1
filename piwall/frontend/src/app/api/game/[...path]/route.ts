import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { getPlayerProfileByUserId } from "@/lib/repositories";

const API_BASE = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";

async function proxy(request: Request, path: string[]) {
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

  const upstream = await fetch(`${API_BASE}/api/${path.join("/")}`, {
    method: request.method,
    headers: { "Content-Type": "application/json", "x-api-key": String(apiKey) },
    body,
    cache: "no-store",
  });

  return new NextResponse(await upstream.text(), {
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
