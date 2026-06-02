import type { Metadata } from "next";
import Link from "next/link";
import { UserIdGate } from "@/components/user-id-gate";
import "./globals.css";

export const metadata: Metadata = {
  title: "Red Team Dashboard",
  description: "OSINT engagement console",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background font-sans antialiased">
        <header className="border-b">
          <div className="container flex h-14 items-center justify-between">
            <Link href="/" className="text-sm font-semibold">
              Red Team Dashboard
            </Link>
            <span className="text-xs text-muted-foreground">phase 0</span>
          </div>
        </header>
        <UserIdGate>
          <main className="container py-6">{children}</main>
        </UserIdGate>
      </body>
    </html>
  );
}
