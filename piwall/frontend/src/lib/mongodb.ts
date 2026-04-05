import { MongoClient } from "mongodb";

declare global {
  // eslint-disable-next-line no-var
  var _mongoClientPromise: Promise<MongoClient> | undefined;
}

const options = {};

function getMongoUri() {
  const uri = process.env.MONGODB_URI;

  if (!uri) {
    throw new Error("MONGODB_URI is not configured.");
  }

  return uri;
}

export function getMongoClientPromise() {
  if (!global._mongoClientPromise) {
    const client = new MongoClient(getMongoUri(), options);
    global._mongoClientPromise = client.connect().then((connectedClient) => connectedClient);
  }

  return global._mongoClientPromise;
}

export function getDatabaseName() {
  return process.env.MONGODB_DB || new URL(getMongoUri()).pathname.replace("/", "") || "pitwall";
}

export function isMongoConfigured() {
  return Boolean(process.env.MONGODB_URI);
}
