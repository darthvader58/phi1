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
  // Auth.js refuses to derive its own origin from the Host header unless it
  // is told to, because a forged Host would otherwise redirect OAuth
  // callbacks to an attacker's domain. It auto-detects only on Vercel, so
  // every other deployment — this container included — must opt in, or
  // /api/auth/session returns UntrustedHost and no request can be signed in.
  //
  // Trusting the header is safe here only because AUTH_URL pins the
  // canonical origin that callbacks are built from; keep them set together.
  trustHost: true,
  session: {
    // Must be "jwt" whenever the Credentials provider is enabled: a
    // credentials sign-in has no linked account for an adapter to hang a
    // session off, so Auth.js rejects the database strategy outright. It
    // throws that during config assertion, which runs on every auth
    // request, so the mismatch 500s even a plain session read rather than
    // failing only at sign-in. The adapter stays on — it still persists
    // users and OAuth account links; only the session lives in the cookie.
    strategy: "jwt"
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
    // Under the JWT strategy the session callback receives no `user` — that
    // argument is database-strategy only. The id has to be carried on the
    // token instead, or every consumer of session.user.id (player
    // provisioning included) silently receives undefined.
    async jwt({ token, user }) {
      if (user?.id) {
        token.id = user.id;
      }

      return token;
    },
    async session({ session, token }) {
      if (session.user) {
        session.user.id = (token.id as string) ?? (token.sub as string);
      }

      return session;
    }
  }
});
