// v2.10.1 — Next.js RSC route loading fallback. Rendered by App Router
// during any route transition while the target page's Server Component
// resolves. Centered inside the main scroll region so the layout chrome
// (sidebar, top bar, What's New banner) stays intact — only the content
// swaps to the loader.

import { Loader } from "@/components/loader";

export default function RouteLoading() {
  return (
    <div className="flex min-h-[60vh] items-center justify-center">
      <Loader label="Loading page" />
    </div>
  );
}
