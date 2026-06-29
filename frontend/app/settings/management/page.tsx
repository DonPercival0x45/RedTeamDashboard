"use client";

// Management — admin-only role-assignment surface. Lists every user in
// the tenant with a role dropdown; saving sends PATCH /admin/users/{id}/role.
// Backend refuses to demote the acting admin themselves (avoid lockout).

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { getMe, listAdminUsers, updateUserRole } from "@/lib/api";
import type { AdminUser, Me, UserRole } from "@/lib/types";

const ROLE_LABEL: Record<UserRole, string> = {
  admin: "Admin — full access",
  user: "User — start/stop engagements, submit feedback",
  guest: "Guest — read-only",
};

const ROLE_CLASS: Record<UserRole, string> = {
  admin: "border-amber-500/40 bg-amber-500/10 text-amber-200",
  user: "border-emerald-500/40 bg-emerald-500/10 text-emerald-200",
  guest: "border-zinc-500/40 bg-zinc-500/10 text-zinc-300",
};

export default function SettingsManagementPage() {
  const [me, setMe] = useState<Me | null>(null);
  const [users, setUsers] = useState<AdminUser[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [savingId, setSavingId] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setError(null);
    try {
      const [meData, list] = await Promise.all([
        getMe(),
        listAdminUsers(),
      ]);
      setMe(meData);
      setUsers(list);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  }, []);

  useEffect(() => {
    void reload();
  }, [reload]);

  const onRoleChange = useCallback(
    async (user: AdminUser, role: UserRole) => {
      if (user.role === role) return;
      setSavingId(user.id);
      setError(null);
      try {
        const updated = await updateUserRole(user.id, role);
        setUsers((prev) =>
          prev
            ? prev.map((u) => (u.id === updated.id ? updated : u))
            : prev,
        );
      } catch (err) {
        setError(err instanceof Error ? err.message : String(err));
      } finally {
        setSavingId(null);
      }
    },
    [],
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
          Management
        </h1>
        <p className="mt-1 text-sm text-muted-foreground">
          Assign roles to every user in the tenant. Admin-only. Demoting
          yourself is blocked — promote someone else first, then ask them
          to demote you.
        </p>
      </div>

      {me && me.role !== "admin" && (
        <Card>
          <CardContent className="py-4 text-sm text-critical">
            You need the <strong>admin</strong> role to manage other users.
          </CardContent>
        </Card>
      )}

      {me?.role === "admin" && (
        <Card>
          <CardHeader>
            <CardTitle className="text-base">Users</CardTitle>
            <CardDescription>
              The role dropdown saves on change. Audit log records every
              role change (event{" "}
              <code className="text-foreground">user.role_changed</code>).
            </CardDescription>
          </CardHeader>
          <CardContent className="space-y-3">
            {error && <p className="text-sm text-critical">{error}</p>}
            {users === null && !error && (
              <p className="text-sm text-muted-foreground">Loading…</p>
            )}
            {users !== null && users.length === 0 && (
              <p className="text-sm text-muted-foreground">
                No users yet. They appear here on first sign-in.
              </p>
            )}
            <ul className="divide-y divide-border">
              {users?.map((u) => {
                const isMe = me?.id === u.id;
                return (
                  <li
                    key={u.id}
                    className="flex flex-wrap items-center gap-3 py-3"
                  >
                    <div className="min-w-0 flex-1">
                      <p className="text-sm font-medium text-foreground">
                        {u.display_name || u.email}
                        {isMe && (
                          <span className="ml-1 text-[10px] text-muted-foreground">
                            (you)
                          </span>
                        )}
                      </p>
                      <p className="text-xs text-muted-foreground">
                        {u.email}
                      </p>
                    </div>

                    <span
                      className={`rounded-full border px-2 py-0.5 text-[10px] uppercase tracking-wide ${
                        ROLE_CLASS[u.role]
                      }`}
                    >
                      {u.role}
                    </span>

                    <select
                      value={u.role}
                      onChange={(e) =>
                        onRoleChange(u, e.target.value as UserRole)
                      }
                      disabled={savingId === u.id}
                      className="rounded-md border border-border bg-background px-2 py-1 text-xs"
                    >
                      {(["admin", "user", "guest"] as UserRole[]).map(
                        (r) => (
                          <option key={r} value={r}>
                            {ROLE_LABEL[r]}
                          </option>
                        ),
                      )}
                    </select>
                  </li>
                );
              })}
            </ul>
          </CardContent>
        </Card>
      )}
    </div>
  );
}
