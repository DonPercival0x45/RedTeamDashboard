// v2.0.0: `/` is a server-side redirect into the new nav shell's
// default route. The engagement chooser now lives at /engagements
// (see app/engagements/page.tsx).
//
// This is a server component (no "use client") because Next.js
// `redirect` is a server API — the browser receives a 307 and
// bounces before rendering.

import { redirect } from "next/navigation";

export default function RootPage(): never {
  redirect("/engagements");
}
