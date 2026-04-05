import { ObjectId } from "mongodb";
import { getDatabaseName, getMongoClientPromise, isMongoConfigured } from "@/lib/mongodb";

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

type DashboardRaceRecord = {
  id: string;
  track: string;
  seasonLabel: string;
  gridPosition: number;
  finishPosition: number;
  lapsCompleted: number;
  fieldSize: number;
  points: number;
  pitCount: number;
  compoundsUsed: string[];
  notes: string;
  status: string;
  createdAt: string | null;
};

type DashboardEloEntry = {
  raceId: string;
  eloBefore: number;
  eloAfter: number;
  delta: number;
  createdAt: string | null;
};

async function getDb() {
  const client = await getMongoClientPromise();
  return client.db(getDatabaseName());
}

function normalizeUserId(id: string) {
  return id.trim();
}

function toIsoString(value: unknown) {
  return value instanceof Date ? value.toISOString() : null;
}

function mapManualRaceRecord(record: any): DashboardRaceRecord {
  return {
    id: record._id.toString(),
    track: String(record.track),
    seasonLabel: String(record.seasonLabel),
    gridPosition: Number(record.gridPosition),
    finishPosition: Number(record.finishPosition),
    lapsCompleted: Number(record.lapsCompleted),
    fieldSize: Number(record.fieldSize),
    points: Number(record.points ?? 0),
    pitCount: Number(record.pitCount ?? 0),
    compoundsUsed: Array.isArray(record.compoundsUsed) ? record.compoundsUsed.map(String) : [],
    notes: String(record.notes ?? ""),
    status: String(record.status),
    createdAt: toIsoString(record.createdAt)
  };
}

async function resolveBackendPlayer(db: Awaited<ReturnType<typeof getDb>>, profile: any) {
  const backendPlayerId = typeof profile?.backendPlayerId === "string" ? profile.backendPlayerId : null;
  const backendUsername = typeof profile?.backendUsername === "string" ? profile.backendUsername : null;
  const backendApiKey = typeof profile?.backendApiKey === "string" ? profile.backendApiKey : null;

  if (backendPlayerId) {
    const backendPlayer = await db.collection("players").findOne({ id: backendPlayerId });
    if (backendPlayer) {
      return backendPlayer;
    }
  }

  if (backendUsername) {
    const backendPlayer = await db.collection("players").findOne({ username: backendUsername });
    if (backendPlayer) {
      return backendPlayer;
    }
  }

  if (backendApiKey) {
    const backendPlayer = await db.collection("players").findOne({ api_key: backendApiKey });
    if (backendPlayer) {
      return backendPlayer;
    }
  }

  return null;
}

async function getLinkedBackendRaceRecords(db: Awaited<ReturnType<typeof getDb>>, profile: any) {
  const backendPlayer = await resolveBackendPlayer(db, profile);
  const resolvedPlayerId = typeof backendPlayer?.id === "string" ? backendPlayer.id : null;

  if (!resolvedPlayerId) {
    return [] as DashboardRaceRecord[];
  }

  const results = await db.collection("race_results").find({ player_id: resolvedPlayerId }).sort({ id: -1 }).limit(20).toArray();
  if (results.length === 0) {
    return [] as DashboardRaceRecord[];
  }

  const raceIds = [...new Set(results.map((result) => String(result.race_id)))];
  const races = await db.collection("races").find({ id: { $in: raceIds } }).toArray();
  const raceLookup = new Map(races.map((race) => [String(race.id), race]));

  return results.map((result) => {
    const race = raceLookup.get(String(result.race_id));
    const pitLaps = Array.isArray(result.pit_laps) ? result.pit_laps : [];
    const finishPosition = Number(result.position);
    const status = result.retired ? "DNF" : "Finished";

    return {
      id: String(result.id ?? result._id?.toString?.() ?? result.race_id),
      track: String(race?.track ?? "Unknown"),
      seasonLabel: race?.race_type === "season" ? "Season Race" : "Quick Race",
      gridPosition: 0,
      finishPosition,
      lapsCompleted: 0,
      fieldSize: 0,
      points: Number(result.points ?? 0),
      pitCount: pitLaps.length,
      compoundsUsed: Array.isArray(result.compounds_used) ? result.compounds_used.map(String) : [],
      notes: pitLaps.length ? `${pitLaps.length} stop${pitLaps.length === 1 ? "" : "s"}` : "",
      status,
      createdAt: toIsoString(race?.finished_at) ?? toIsoString(race?.created_at)
    };
  });
}

async function getLinkedBackendEloHistory(db: Awaited<ReturnType<typeof getDb>>, profile: any) {
  const backendPlayer = await resolveBackendPlayer(db, profile);
  const resolvedPlayerId = typeof backendPlayer?.id === "string" ? backendPlayer.id : null;

  if (!resolvedPlayerId) {
    return [] as DashboardEloEntry[];
  }

  const history = await db.collection("elo_history").find({ player_id: resolvedPlayerId }).sort({ created_at: 1 }).limit(24).toArray();

  return history.map((entry) => ({
    raceId: String(entry.race_id),
    eloBefore: Number(entry.elo_before),
    eloAfter: Number(entry.elo_after),
    delta: Number(entry.delta),
    createdAt: toIsoString(entry.created_at)
  }));
}

export async function upsertPlayerProfile(user: {
  id: string;
  name?: string | null;
  email?: string | null;
  image?: string | null;
}) {
  if (!isMongoConfigured()) {
    return;
  }

  const db = await getDb();
  const normalizedUserId = normalizeUserId(user.id);

  await db.collection("playerProfiles").updateOne(
    { userId: normalizedUserId },
    {
      $set: {
        userId: normalizedUserId,
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

export async function getPlayerProfileByUserId(userId: string) {
  if (!isMongoConfigured()) {
    return null;
  }

  const db = await getDb();
  return db.collection("playerProfiles").findOne({ userId: normalizeUserId(userId) });
}

export async function saveBackendPlayerCredentials(
  userId: string,
  credentials: {
    backendUsername: string;
    backendApiKey: string;
    backendPlayerId?: string | null;
  }
) {
  if (!isMongoConfigured()) {
    return;
  }

  const db = await getDb();
  const normalizedUserId = normalizeUserId(userId);

  await db.collection("playerProfiles").updateOne(
    { userId: normalizedUserId },
    {
      $set: {
        userId: normalizedUserId,
        backendUsername: credentials.backendUsername,
        backendApiKey: credentials.backendApiKey,
        backendPlayerId: credentials.backendPlayerId ?? null,
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
  if (!isMongoConfigured()) {
    throw new Error("MongoDB is not configured.");
  }

  const db = await getDb();
  const now = new Date();
  const result = await db.collection("strategySubmissions").insertOne({
    userId: normalizeUserId(userId),
    ...input,
    createdAt: now,
    updatedAt: now
  });

  return result.insertedId.toString();
}

export async function createRaceRecord(userId: string, input: RaceRecordInput) {
  if (!isMongoConfigured()) {
    throw new Error("MongoDB is not configured.");
  }

  const db = await getDb();
  const now = new Date();
  const result = await db.collection("raceRecords").insertOne({
    userId: normalizeUserId(userId),
    ...input,
    createdAt: now,
    updatedAt: now
  });

  return result.insertedId.toString();
}

export async function getUserDashboard(userId: string) {
  if (!isMongoConfigured()) {
    return {
      profile: null,
      stats: {
        totalSubmissions: 0,
        totalRaces: 0,
        wins: 0,
        podiums: 0,
        dnfs: 0,
        winRate: 0,
        averageFinish: null
      },
      backendProfile: null,
      eloHistory: [],
      submissions: [],
      raceRecords: []
    };
  }

  const db = await getDb();
  const normalizedUserId = normalizeUserId(userId);

  const [profile, totalSubmissions, submissions, manualRaceRecords] = await Promise.all([
    db.collection("playerProfiles").findOne({ userId: normalizedUserId }),
    db.collection("strategySubmissions").countDocuments({ userId: normalizedUserId }),
    db
      .collection("strategySubmissions")
      .find({ userId: normalizedUserId })
      .sort({ createdAt: -1 })
      .limit(8)
      .toArray(),
    db.collection("raceRecords").find({ userId: normalizedUserId }).sort({ createdAt: -1 }).limit(12).toArray()
  ]);

  const [backendPlayer, linkedBackendRaceRecords, eloHistory] = await Promise.all([
    resolveBackendPlayer(db, profile),
    getLinkedBackendRaceRecords(db, profile),
    getLinkedBackendEloHistory(db, profile)
  ]);

  const raceRecords = [
    ...manualRaceRecords.map(mapManualRaceRecord),
    ...linkedBackendRaceRecords
  ]
    .sort((a, b) => new Date(b.createdAt ?? 0).getTime() - new Date(a.createdAt ?? 0).getTime())
    .slice(0, 12);

  const totalRaces = raceRecords.length;

  const wins = raceRecords.filter((record) => record.finishPosition === 1).length;
  const podiums = raceRecords.filter((record) => record.finishPosition <= 3).length;
  const dnfs = raceRecords.filter((record) => record.status === "DNF").length;
  const winRate = totalRaces === 0 ? 0 : Number(((wins / totalRaces) * 100).toFixed(1));
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
          createdAt: toIsoString(profile.createdAt)
        }
      : null,
    stats: {
      totalSubmissions,
      totalRaces,
      wins,
      podiums,
      dnfs,
      winRate,
      averageFinish
    },
    backendProfile: backendPlayer
      ? {
          username: String(backendPlayer.username ?? ""),
          team: String(backendPlayer.team_name ?? "Pit Wall"),
          elo: Number(backendPlayer.elo ?? 1200)
        }
      : null,
    eloHistory,
    submissions: submissions.map((submission) => ({
      id: submission._id.toString(),
      track: String(submission.track),
      title: String(submission.title),
      stintPlan: String(submission.stintPlan),
      riskLevel: String(submission.riskLevel),
      notes: String(submission.notes),
      createdAt: toIsoString(submission.createdAt)
    })),
    raceRecords
  };
}

export async function getRaceRecord(recordId: string) {
  if (!isMongoConfigured()) {
    return null;
  }

  if (!ObjectId.isValid(recordId)) {
    return null;
  }

  const db = await getDb();
  const record = await db.collection("raceRecords").findOne({ _id: new ObjectId(recordId) });

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
  if (!isMongoConfigured()) {
    return {
      totalEntries: 0,
      averageFinish: null,
      recentRaces: []
    };
  }

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
