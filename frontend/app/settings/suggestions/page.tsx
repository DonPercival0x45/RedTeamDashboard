"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Download } from "lucide-react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import {
  createRoadmapSuggestion,
  decideRoadmapSuggestion,
  deleteRoadmapSuggestion,
  downloadRoadmapMarkdown,
  getMe,
  listRoadmapSuggestions,
} from "@/lib/api";
import type {
  Me,
  RoadmapSuggestion,
  RoadmapSuggestionStatus,
} from "@/lib/types";

// Tenant-global suggestion box. Any authenticated analyst drops in a product
// idea; the planner agent emits pros/cons; an admin approves or rejects.
// Approved items export to ROADMAP.md for Claude Code to pick up as PR work.

type FilterChip = "all" | RoadmapSuggestionStatus;

const STATUS_LABEL: Record<RoadmapSuggestionStatus, string> = {
  pending_review: "Pending",
  approved: "Approved",
  rejected: "Rejected",
};

const STATUS_CLASS: Record<RoadmapSuggestionStatus, string> = {
  pending_review:
    "border-amber-500/40 bg-amber-500/10 text-amber-200",
  approved: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  rejected: "border-rose-500/40 bg-rose-500/10 text-rose-200",
};

export default function SettingsSuggestionsPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [rows, setRows] = useState<RoadmapSuggestion[] | null>(null);
  const [filter, setFilter] = useState<FilterChip>("all");
  const [error, setError] = useState<string | null>(null);
  const [body, setBody] = useState("");
  const [submitting, setSubmitting] = useState(false);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const next = await listRoadmapSuggestions();
      setRows(next);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
    void getMe()
      .then(setMe)
      .catch(() => {
        // /me is best-effort — if it fails (older backend, dev with no
        // X-User-Id), we just default to non-admin and let the server gate.
        setMe(null);
      });
  }, [reload]);

  const visible = useMemo(() => {
    if (!rows) return null;
    if (filter === "all") return rows;
    return rows.filter((r) => r.status === filter);
  }, [rows, filter]);

  const onSubmit = useCallback(async () => {
    const text = body.trim();
    if (text.length < 4) {
      setError("Suggestion is too short.");
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await createRoadmapSuggestion({ body: text });
      setBody("");
      await reload();
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setSubmitting(false);
    }
  }, [body, reload]);

  const onDecide = useCallback(
    async (
      row: RoadmapSuggestion,
      decision: "approved" | "rejected",
    ) => {
      const note = window.prompt(
        `Optional note for ${decision === "approved" ? "approving" : "rejecting"}:`,
        row.review_note ?? "",
      );
      if (note === null) return;
      try {
        await decideRoadmapSuggestion(row.id, {
          status: decision,
          note: note || null,
        });
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  const onDelete = useCallback(
    async (row: RoadmapSuggestion) => {
      if (!window.confirm("Delete this suggestion? This can't be undone.")) {
        return;
      }
      try {
        await deleteRoadmapSuggestion(row.id);
        await reload();
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      }
    },
    [reload],
  );

  return (
    <div className="mx-auto max-w-4xl space-y-6 px-4 py-6">
      <div>
        <Link
          href="/"
          className="text-xs text-muted-foreground hover:text-foreground"
        >
          ← engagements
        </Link>
        <h1 className="mt-2 text-2xl font-semibold tracking-tight">
          Suggestion box
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Drop in product ideas, scope adjustments, or anything you want the
          team to consider. A planning agent reads your suggestion against the
          project charter and current handoff, then writes pros and cons.
          Admins approve or reject; approved items export to{" "}
          <code className="text-foreground">ROADMAP.md</code> so Claude Code
          can pick them up as future PR work.
        </p>
      </div>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">New suggestion</CardTitle>
          <CardDescription>
            Be specific — the agent gives a better read when the idea names the
            user-visible behavior, the phase or area it touches, and any
            constraints.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-3">
          <Textarea
            value={body}
            onChange={(e) => setBody(e.target.value)}
            placeholder="e.g. Add a 'starred findings' filter so I can pin a short shortlist while I write the report."
            rows={5}
            disabled={submitting}
          />
          <div className="flex items-center justify-between">
            <p className="text-xs text-muted-foreground">
              {submitting
                ? "Agent is evaluating…"
                : `${body.trim().length} characters`}
            </p>
            <Button
              onClick={onSubmit}
              disabled={submitting || body.trim().length < 4}
            >
              {submitting ? "Submitting…" : "Submit for review"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <CardTitle className="text-base">Suggestions</CardTitle>
              <CardDescription>
                Newest first. Approved items land in the export.
              </CardDescription>
            </div>
            <Button
              variant="outline"
              size="sm"
              onClick={() => {
                void downloadRoadmapMarkdown().catch((err) =>
                  setError(err instanceof Error ? err.message : String(err)),
                );
              }}
            >
              <Download className="mr-1.5 h-3.5 w-3.5" />
              Export ROADMAP.md
            </Button>
          </div>
          <div className="mt-3 flex flex-wrap gap-1.5">
            {(["all", "pending_review", "approved", "rejected"] as const).map(
              (chip) => (
                <button
                  key={chip}
                  onClick={() => setFilter(chip)}
                  className={`rounded-full border px-3 py-0.5 text-xs transition ${
                    filter === chip
                      ? "border-foreground bg-foreground text-background"
                      : "border-border text-muted-foreground hover:text-foreground"
                  }`}
                >
                  {chip === "all" ? "All" : STATUS_LABEL[chip]}
                </button>
              ),
            )}
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          {error && <p className="text-sm text-critical">{error}</p>}
          {visible === null && !error && (
            <p className="text-sm text-muted-foreground">Loading…</p>
          )}
          {visible !== null && visible.length === 0 && (
            <p className="text-sm text-muted-foreground">
              {filter === "all"
                ? "No suggestions yet — submit the first one above."
                : `No ${STATUS_LABEL[filter as RoadmapSuggestionStatus].toLowerCase()} suggestions.`}
            </p>
          )}
          {visible?.map((row) => (
            <SuggestionRow
              key={row.id}
              row={row}
              me={me}
              onDecide={onDecide}
              onDelete={onDelete}
            />
          ))}
        </CardContent>
      </Card>
    </div>
  );
}

function SuggestionRow({
  row,
  me,
  onDecide,
  onDelete,
}: {
  row: RoadmapSuggestion;
  me: Me | null;
  onDecide: (
    row: RoadmapSuggestion,
    decision: "approved" | "rejected",
  ) => void;
  onDelete: (row: RoadmapSuggestion) => void;
}) {
  const isAdmin = me?.is_admin ?? false;
  const isAuthor = me?.id !== undefined && row.author_user_id === me.id;
  const canDelete =
    isAdmin || (isAuthor && row.status === "pending_review");

  const evaluating =
    row.agent_summary === null &&
    row.agent_pros.length === 0 &&
    row.agent_cons.length === 0;

  return (
    <div className="rounded-md border border-border bg-card/40 p-3 text-sm">
      <div className="flex items-start justify-between gap-3">
        <p className="whitespace-pre-wrap text-foreground">{row.body}</p>
        <span
          className={`shrink-0 rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${
            STATUS_CLASS[row.status]
          }`}
        >
          {STATUS_LABEL[row.status]}
        </span>
      </div>

      {row.agent_summary && (
        <p className="mt-2 text-xs italic text-muted-foreground">
          {row.agent_summary}
        </p>
      )}

      {evaluating && (
        <p className="mt-2 text-xs text-muted-foreground">
          Agent evaluation failed or is still in progress. You can still
          approve or reject manually.
        </p>
      )}

      {(row.agent_pros.length > 0 || row.agent_cons.length > 0) && (
        <div className="mt-2 grid grid-cols-1 gap-3 sm:grid-cols-2">
          {row.agent_pros.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-emerald-300">
                Pros
              </p>
              <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                {row.agent_pros.map((p, i) => (
                  <li key={i}>{p}</li>
                ))}
              </ul>
            </div>
          )}
          {row.agent_cons.length > 0 && (
            <div>
              <p className="text-[10px] font-semibold uppercase tracking-wide text-rose-300">
                Cons
              </p>
              <ul className="mt-1 list-disc pl-4 text-xs text-muted-foreground">
                {row.agent_cons.map((c, i) => (
                  <li key={i}>{c}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {row.review_note && (
        <p className="mt-2 text-xs text-muted-foreground">
          <span className="font-medium text-foreground">Admin note:</span>{" "}
          {row.review_note}
        </p>
      )}

      <div className="mt-3 flex items-center justify-between gap-2">
        <p className="text-[10px] text-muted-foreground">
          submitted {new Date(row.created_at).toLocaleString()}
          {row.reviewed_at && (
            <> · reviewed {new Date(row.reviewed_at).toLocaleString()}</>
          )}
        </p>
        <div className="flex gap-2">
          {isAdmin && row.status === "pending_review" && (
            <>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onDecide(row, "approved")}
              >
                Approve
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => onDecide(row, "rejected")}
              >
                Reject
              </Button>
            </>
          )}
          {canDelete && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => onDelete(row)}
              className="text-muted-foreground hover:text-critical"
            >
              Delete
            </Button>
          )}
        </div>
      </div>
    </div>
  );
}
