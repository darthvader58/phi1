"use client";

import type { ReactNode } from "react";
import type { Session } from "next-auth";
import { SessionProvider } from "next-auth/react";

type AuthProvidersProps = {
  children: ReactNode;
  session: Session | null;
};

export default function AuthProviders({ children, session }: AuthProvidersProps) {
  return <SessionProvider session={session}>{children}</SessionProvider>;
}
