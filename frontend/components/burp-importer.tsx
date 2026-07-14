"use client";

import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ScannerImportWizard } from "@/components/scanner-import-wizard";
import type { Finding } from "@/lib/types";

export function BurpImporter({
  slug,
  onImported,
}: {
  slug: string;
  onImported: (created: Finding[]) => void;
}) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="text-base">Import Burp Pro XML</CardTitle>
        <CardDescription>
          Export selected issues from Burp as XML, preview the proposed groups,
          then confirm exactly which findings to add. Re-imported serials and
          out-of-scope targets are identified before any engagement data changes.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <ScannerImportWizard
          slug={slug}
          source="burp"
          onImported={onImported}
        />
      </CardContent>
    </Card>
  );
}
