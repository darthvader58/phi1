import { NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { createRaceRecord } from "@/lib/repositories";

function parseNumber(input: unknown) {
  return Number.parseInt(String(input), 10);
}

export async function POST(request: Request) {
  const session = await auth();

  if (!session?.user?.id) {
    return NextResponse.json({ error: "You must be signed in to record a race." }, { status: 401 });
  }

  const body = (await request.json()) as {
    track?: string;
    seasonLabel?: string;
    gridPosition?: number | string;
    finishPosition?: number | string;
    lapsCompleted?: number | string;
    fieldSize?: number | string;
    status?: string;
    notes?: string;
  };

  if (!body.track || !body.seasonLabel || !body.status) {
    return NextResponse.json({ error: "Track, season label, and status are required." }, { status: 400 });
  }

  const gridPosition = parseNumber(body.gridPosition);
  const finishPosition = parseNumber(body.finishPosition);
  const lapsCompleted = parseNumber(body.lapsCompleted);
  const fieldSize = parseNumber(body.fieldSize);

  if ([gridPosition, finishPosition, lapsCompleted, fieldSize].some((value) => Number.isNaN(value))) {
    return NextResponse.json({ error: "Grid, finish, laps, and field size must be valid numbers." }, { status: 400 });
  }

  const id = await createRaceRecord(session.user.id, {
    track: body.track.trim(),
    seasonLabel: body.seasonLabel.trim(),
    gridPosition,
    finishPosition,
    lapsCompleted,
    fieldSize,
    status: body.status.trim(),
    notes: body.notes?.trim() ?? ""
  });

  return NextResponse.json({ ok: true, id });
}
