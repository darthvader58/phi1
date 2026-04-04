import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { createSubmission } from "@/lib/repositories";

export async function POST(request: Request) {
  const session = await auth();

  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in to save a submission." }, { status: 401 });
  }

  const body = (await request.json()) as {
    track?: string;
    title?: string;
    stintPlan?: string;
    riskLevel?: string;
    notes?: string;
  };

  if (!body.track || !body.title || !body.stintPlan || !body.riskLevel) {
    return NextResponse.json({ error: "Track, title, stint plan, and risk level are required." }, { status: 400 });
  }

  const id = await createSubmission(session.user.id, {
    track: body.track.trim(),
    title: body.title.trim(),
    stintPlan: body.stintPlan.trim(),
    riskLevel: body.riskLevel.trim(),
    notes: body.notes?.trim() ?? ""
  });

  return NextResponse.json({ ok: true, id });
}
