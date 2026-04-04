import { ObjectId } from "mongodb";
import { getDatabaseName, getMongoClientPromise } from "@/lib/mongodb";

export type SubmissionInput = {
  track: string;
  title: string;
  stintPlan: string;
  riskLevel: string;
  notes: string;
};

export type RaceRecordInput = {
  track: string;
  seasonLabel: string;
  gridPosition: number;
  finishPosition: number;
  lapsCompleted: number;
  fieldSize: number;
  notes: string;
  status: string;
};

async function getDb() {
  const client = await getMongoClientPromise();
  return client.db(getDatabaseName());
}

function toObjectId(id: string) {
  return new ObjectId(id);
}

export async function upsertPlayerProfile(user: {
  id: string;
  name?: string | null;
  email?: string | null;
  image?: string | null;
}) {
  const db = await getDb();

  await db.collection("playerProfiles").updateOne(
    { userId: toObjectId(user.id) },
    {
      $set: {
        name: user.name ?? "Pit Wall Driver",
        email: user.email ?? null,
        image: user.image ?? null,
        updatedAt: new Date()
      },
      $setOnInsert: {
        createdAt: new Date()
      }
    },
    { upsert: true }
  );
}

export async function createSubmission(userId: string, input: SubmissionInput) {
  const db = await getDb();
  const now = new Date();
  const result = await db.collection("strategySubmissions").insertOne({
    userId: toObjectId(userId),
    ...input,
    createdAt: now,
    updatedAt: now
  });

  return result.insertedId.toString();
}

export async function createRaceRecord(userId: string, input: RaceRecordInput) {
  const db = await getDb();
  const now = new Date();
  const result = await db.collection("raceRecords").insertOne({
    userId: toObjectId(userId),
    ...input,
    createdAt: now,
    updatedAt: now
  });

  return result.insertedId.toString();
}

export async function getUserDashboard(userId: string) {
  const db = await getDb();
  const objectId = toObjectId(userId);

  const [profile, totalSubmissions, totalRaces, submissions, raceRecords] = await Promise.all([
    db.collection("playerProfiles").findOne({ userId: objectId }),
    db.collection("strategySubmissions").countDocuments({ userId: objectId }),
    db.collection("raceRecords").countDocuments({ userId: objectId }),
    db
      .collection("strategySubmissions")
      .find({ userId: objectId })
      .sort({ createdAt: -1 })
      .limit(8)
      .toArray(),
    db.collection("raceRecords").find({ userId: objectId }).sort({ createdAt: -1 }).limit(12).toArray()
  ]);

  const wins = raceRecords.filter((record) => record.finishPosition === 1).length;
  const podiums = raceRecords.filter((record) => record.finishPosition <= 3).length;
  const averageFinish =
    totalRaces === 0
      ? null
      : Number(
          (
            raceRecords.reduce((sum, record) => sum + Number(record.finishPosition || 0), 0) / totalRaces
          ).toFixed(1)
        );

  return {
    profile: profile
      ? {
          name: String(profile.name ?? ""),
          email: String(profile.email ?? ""),
          image: profile.image ? String(profile.image) : null,
          createdAt: profile.createdAt instanceof Date ? profile.createdAt.toISOString() : null
        }
      : null,
    stats: {
      totalSubmissions,
      totalRaces,
      wins,
      podiums,
      averageFinish
    },
    submissions: submissions.map((submission) => ({
      id: submission._id.toString(),
      track: String(submission.track),
      title: String(submission.title),
      stintPlan: String(submission.stintPlan),
      riskLevel: String(submission.riskLevel),
      notes: String(submission.notes),
      createdAt: submission.createdAt instanceof Date ? submission.createdAt.toISOString() : null
    })),
    raceRecords: raceRecords.map((record) => ({
      id: record._id.toString(),
      track: String(record.track),
      seasonLabel: String(record.seasonLabel),
      gridPosition: Number(record.gridPosition),
      finishPosition: Number(record.finishPosition),
      lapsCompleted: Number(record.lapsCompleted),
      fieldSize: Number(record.fieldSize),
      notes: String(record.notes ?? ""),
      status: String(record.status),
      createdAt: record.createdAt instanceof Date ? record.createdAt.toISOString() : null
    }))
  };
}

export async function getRaceRecord(recordId: string) {
  if (!ObjectId.isValid(recordId)) {
    return null;
  }

  const db = await getDb();
  const record = await db.collection("raceRecords").findOne({ _id: toObjectId(recordId) });

  if (!record) {
    return null;
  }

  return {
    id: record._id.toString(),
    track: String(record.track),
    seasonLabel: String(record.seasonLabel),
    gridPosition: Number(record.gridPosition),
    finishPosition: Number(record.finishPosition),
    lapsCompleted: Number(record.lapsCompleted),
    fieldSize: Number(record.fieldSize),
    notes: String(record.notes ?? ""),
    status: String(record.status),
    createdAt: record.createdAt instanceof Date ? record.createdAt.toISOString() : null
  };
}

export async function getSeasonSnapshot() {
  const db = await getDb();
  const raceRecords = await db.collection("raceRecords").find({}).sort({ createdAt: -1 }).limit(25).toArray();

  const totalEntries = raceRecords.length;
  const averageFinish =
    totalEntries === 0
      ? null
      : Number(
          (
            raceRecords.reduce((sum, record) => sum + Number(record.finishPosition || 0), 0) / totalEntries
          ).toFixed(1)
        );

  return {
    totalEntries,
    averageFinish,
    recentRaces: raceRecords.map((record) => ({
      id: record._id.toString(),
      track: String(record.track),
      seasonLabel: String(record.seasonLabel),
      finishPosition: Number(record.finishPosition),
      status: String(record.status),
      createdAt: record.createdAt instanceof Date ? record.createdAt.toISOString() : null
    }))
  };
}
