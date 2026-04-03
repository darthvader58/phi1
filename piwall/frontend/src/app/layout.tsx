import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import NavBar from "@/components/NavBar";
import { ToastProvider } from "@/components/Toast";

export const metadata: Metadata = {
  title: "PIT WALL — F1 Strategy Game",
  description: "Algorithmic F1 race strategy game with real telemetry data",
};

export default function RootLayout({
  children,
}: Readonly<{ children: React.ReactNode }>) {
  return (
    <html lang="en" className="dark">
      <body className="bg-pit-black text-pit-light min-h-screen">
        <ToastProvider>
          <NavBar />
          <main className="pt-14">{children}</main>
        </ToastProvider>
      </body>
    </html>
  );
}
