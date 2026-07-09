import { Suspense } from "react";
import { EntityWorkbenchPage } from "./entity-workbench-client";

export default function Page() {
  return (
    <Suspense
      fallback={
        <p className="px-6 py-10 text-sm text-muted-foreground">Loading…</p>
      }
    >
      <EntityWorkbenchPage />
    </Suspense>
  );
}
