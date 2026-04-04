import type { Metadata } from "next";
import "./globals.css";
import NavBar from "@/components/NavBar";
import { auth, googleEnabled } from "@/lib/auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "Pit Wall",
  description: "Pit Wall race strategy experience with Mongo-backed account history."
};

export default async function RootLayout({ children }: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();

  return (
    <html lang="en">
      <body>
        <NavBar googleEnabled={googleEnabled} session={session} />
        <main className="site-main">{children}</main>
      </body>
    </html>
  );
}
