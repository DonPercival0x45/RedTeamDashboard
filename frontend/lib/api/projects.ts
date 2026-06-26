import { request } from "./base";
import type { Project, EngagementStatus } from "@/lib/types/project";

export function listProjects(status?: EngagementStatus): Promise<Project[]> {
  const q = status ? `?status=${status}` : "";
  return request<Project[]>(`/projects${q}`);
}

export function getProject(slug: string): Promise<Project> {
  return request<Project>(`/projects/${slug}`);
}

export function createProject(body: {
  name: string;
  slug?: string;
  description?: string;
}): Promise<Project> {
  return request<Project>("/projects", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export function archiveProject(slug: string): Promise<Project> {
  return request<Project>(`/projects/${slug}`, { method: "DELETE" });
}

export function flushEngagement(slug: string): Promise<void> {
  return request<void>(`/projects/${slug}/flush`, { method: "POST" });
}
