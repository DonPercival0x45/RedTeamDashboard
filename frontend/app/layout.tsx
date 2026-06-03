import type { Metadata } from "next";
import Link from "next/link";
import { SourceGate } from "@/components/source-gate";
import { SourceSwitcher } from "@/components/source-switcher";
import { SourceProvider } from "@/lib/source-context";
import "./globals.css";

export const metadata: Metadata = {
  title: "Red Team Dashboard",
  description: "Read-only viewer for self-hosted RTD deployments",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-background font-sans antialiased">
        <SourceProvider>
          <header className="border-b">
            <div className="container flex h-14 items-center justify-between">
              <Link href="/" className="text-sm font-semibold">
                Red Team Dashboard
              </Link>
              <SourceSwitcher />
            </div>
          </header>
          <SourceGate>
            <main className="container py-6">{children}</main>
          </SourceGate>
        </SourceProvider>
      </body>
    </html>
  );
}
