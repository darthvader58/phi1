import type { Metadata } from "next";
import "./globals.css";
import NavBar from "@/components/NavBar";
import AuthProviders from "@/components/AuthProviders";
import BackendPlayerSync from "@/components/BackendPlayerSync";
import { ToastProvider } from "@/components/Toast";
import { auth, googleEnabled } from "@/lib/auth";

export const dynamic = "force-dynamic";

export const metadata: Metadata = {
  title: "PIT WALL — F1 Strategy Game",
  description: "Algorithmic F1 race strategy game with real telemetry data"
};

export default async function RootLayout({
  children
}: Readonly<{ children: React.ReactNode }>) {
  const session = await auth();

  return (
    <html lang="en" className="dark">
      <body className="bg-pit-black text-pit-light min-h-screen">
        <AuthProviders session={session}>
          <ToastProvider>
            <BackendPlayerSync />
            <NavBar googleEnabled={googleEnabled} session={session} />
            <main className="pt-14">{children}</main>
          </ToastProvider>
        </AuthProviders>
      </body>
    </html>
  );
}
