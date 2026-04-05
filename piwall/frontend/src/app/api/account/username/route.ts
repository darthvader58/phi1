import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { updateBackendUsernameForUser } from "@/lib/repositories";

export async function PATCH(request: Request) {
  const session = await auth();

  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in." }, { status: 401 });
  }

  const body = (await request.json().catch(() => ({}))) as { username?: string };

  if (!body.username?.trim()) {
    return NextResponse.json({ error: "Username is required." }, { status: 400 });
  }

  try {
    const result = await updateBackendUsernameForUser(session.user.id, body.username);
    return NextResponse.json({ ok: true, username: result.username });
  } catch (error) {
    const message = error instanceof Error ? error.message : "Unable to update username.";
    const status = message === "Username already taken" ? 409 : 400;
    return NextResponse.json({ error: message }, { status });
  }
}
