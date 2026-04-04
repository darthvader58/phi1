import { NextResponse } from "next/server";
import { hash } from "bcryptjs";
import { getDatabaseName, getMongoClientPromise } from "@/lib/mongodb";
import { upsertPlayerProfile } from "@/lib/repositories";

export async function POST(request: Request) {
  const body = (await request.json()) as {
    name?: string;
    email?: string;
    password?: string;
  };

  const name = body.name?.trim();
  const email = body.email?.trim().toLowerCase();
  const password = body.password?.trim();

  if (!name || !email || !password) {
    return NextResponse.json({ error: "Name, email, and password are required." }, { status: 400 });
  }

  if (password.length < 8) {
    return NextResponse.json({ error: "Password must be at least 8 characters long." }, { status: 400 });
  }

  const client = await getMongoClientPromise();
  const db = client.db(getDatabaseName());
  const existingUser = await db.collection("users").findOne({ email });

  if (existingUser) {
    return NextResponse.json({ error: "An account already exists for that email." }, { status: 409 });
  }

  const passwordHash = await hash(password, 12);
  const now = new Date();
  const result = await db.collection("users").insertOne({
    name,
    email,
    passwordHash,
    emailVerified: null,
    image: null,
    createdAt: now,
    updatedAt: now
  });

  await upsertPlayerProfile({
    id: result.insertedId.toString(),
    name,
    email,
    image: null
  });

  return NextResponse.json({ ok: true });
}
