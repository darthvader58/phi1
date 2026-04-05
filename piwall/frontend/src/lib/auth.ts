import NextAuth from "next-auth";
import type { Provider } from "next-auth/providers";
import Credentials from "next-auth/providers/credentials";
import Google from "next-auth/providers/google";
import { MongoDBAdapter } from "@auth/mongodb-adapter";
import { compare } from "bcryptjs";
import { ObjectId } from "mongodb";
import { getDatabaseName, getMongoClientPromise, isMongoConfigured } from "@/lib/mongodb";
import { upsertPlayerProfile } from "@/lib/repositories";

const googleEnabled = Boolean(process.env.AUTH_GOOGLE_ID && process.env.AUTH_GOOGLE_SECRET);

export { googleEnabled };

const mongoEnabled = isMongoConfigured();

const providers: Provider[] = [
  Credentials({
    name: "Credentials",
    credentials: {
      email: { label: "Email", type: "email" },
      password: { label: "Password", type: "password" }
    },
    async authorize(credentials) {
      const email = credentials?.email?.toString().trim().toLowerCase();
      const password = credentials?.password?.toString();

      if (!email || !password) {
        return null;
      }

      const client = await getMongoClientPromise();
      const db = client.db(getDatabaseName());
      const user = await db.collection("users").findOne<{ _id: ObjectId; email: string; name?: string; image?: string; passwordHash?: string }>({
        email
      });

      if (!user?.passwordHash) {
        return null;
      }

      const passwordMatches = await compare(password, user.passwordHash);

      if (!passwordMatches) {
        return null;
      }

      return {
        id: user._id.toString(),
        email: user.email,
        name: user.name ?? email.split("@")[0],
        image: user.image ?? null
      };
    }
  })
];

if (googleEnabled) {
  providers.unshift(
    Google({
      clientId: process.env.AUTH_GOOGLE_ID!,
      clientSecret: process.env.AUTH_GOOGLE_SECRET!
    })
  );
}

export const { handlers, auth, signIn, signOut } = NextAuth({
  adapter: mongoEnabled ? MongoDBAdapter(getMongoClientPromise()) : undefined,
  secret: process.env.AUTH_SECRET,
  session: {
    strategy: mongoEnabled ? "database" : "jwt"
  },
  providers,
  pages: {
    signIn: "/"
  },
  callbacks: {
    async signIn({ user }) {
      if (user.id) {
        await upsertPlayerProfile({
          id: user.id,
          name: user.name,
          email: user.email,
          image: user.image
        });
      }

      return true;
    },
    async session({ session, user }) {
      if (session.user) {
        session.user.id = user.id;
      }

      return session;
    }
  }
});
