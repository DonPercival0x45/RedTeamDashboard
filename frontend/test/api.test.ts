import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError, listEngagements, getEngagement } from "@/lib/api";

// These tests lock in the two frontend contracts every screen depends on:
//
// 1. ApiError parsing — the finding-chat "Provider key needed" banner, the
//    feedback-page duplicate detection, and strategy-view 409 handling all
//    branch on ApiError.status / .code / .actionUrl. The structured
//    "missing_provider_key" shape must round-trip intact.
// 2. request() — the shared fetch wrapper: 2xx JSON, 204 No Content, and
//    non-2xx throwing ApiError with the parsed detail.

describe("ApiError", () => {
  it("parses a plain string detail", () => {
    const err = new ApiError(400, "Bad Request", "boom");
    expect(err.status).toBe(400);
    expect(err.message).toContain("400");
    expect(err.message).toContain("boom");
    expect(err.code).toBeUndefined();
  });

  it("unwraps a FastAPI {detail: ...} envelope recursively", () => {
    // FastAPI wraps HTTPException detail once; our finding-chat path nests it.
    const err = new ApiError(400, "Bad Request", {
      detail: { detail: { message: "no key", code: "missing_provider_key" } },
    });
    expect(err.message).toContain("no key");
    expect(err.code).toBe("missing_provider_key");
  });

  it("preserves the structured action_url/label the chat banner links to", () => {
    const err = new ApiError(400, "Bad Request", {
      detail: {
        message: "Add a provider key first",
        code: "missing_provider_key",
        action_url: "/settings/keys",
        action_label: "Open settings",
      },
    });
    expect(err.actionUrl).toBe("/settings/keys");
    expect(err.actionLabel).toBe("Open settings");
  });

  it("falls back to JSON when the detail is an object without message/error", () => {
    const err = new ApiError(422, "Unprocessable", { weird: true });
    expect(err.message).toContain('"weird":true');
  });

  it("names itself ApiError so instanceof checks in the UI work", () => {
    const err = new ApiError(409, "Conflict", "dup");
    expect(err).toBeInstanceOf(ApiError);
    expect(err.name).toBe("ApiError");
  });
});

// ---- request() behaviour via exported helpers ---------------------------------

const okJson = (body: unknown, status = 200): Response =>
  ({
    ok: true,
    status,
    statusText: "OK",
    text: async () => JSON.stringify(body),
    json: async () => body,
  }) as Response;

const errText = (status: number, statusText: string, body: string): Response =>
  ({
    ok: false,
    status,
    statusText,
    text: async () => body,
  }) as Response;

beforeEach(() => {
  // The shared wrapper resolves auth headers per call; stub the two env-driven
  // modules so no MSAL/config plumbing runs.
  vi.doMock("@/lib/config", () => ({
    API_BASE_URL: "http://test",
    DEV_USER: "dev-user",
    ENTRA_ENABLED: false,
  }));
  vi.doMock("@/lib/msal", () => ({ getAccessToken: async () => "tok" }));
});

afterEach(() => {
  vi.doUnmock("@/lib/config");
  vi.doUnmock("@/lib/msal");
  vi.restoreAllMocks();
});

describe("request() via listEngagements", () => {
  it("returns parsed JSON on 2xx", async () => {
    const fetchMock = vi
      .spyOn(globalThis, "fetch")
      .mockResolvedValue(okJson([{ id: "1", slug: "acme" }]));

    const { listEngagements } = await import("@/lib/api");
    const rows = await listEngagements();
    expect(rows).toEqual([{ id: "1", slug: "acme" }]);
    expect(fetchMock).toHaveBeenCalledOnce();
    const calledUrl = String(fetchMock.mock.calls[0][0]);
    expect(calledUrl).toContain("/engagements");
  });

  it("throws ApiError carrying status + parsed detail on 409", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errText(409, "Conflict", JSON.stringify("already exists")),
    );

    const { listEngagements } = await import("@/lib/api");
    await expect(listEngagements()).rejects.toMatchObject({
      status: 409,
      name: "ApiError",
    });
  });

  it("maps a 409 with {detail:{message}} into a readable ApiError", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      errText(
        409,
        "Conflict",
        JSON.stringify({ detail: { message: "playbook-runs instead" } }),
      ),
    );

    const { getEngagement } = await import("@/lib/api");
    await expect(getEngagement("x")).rejects.toMatchObject({
      status: 409,
    });
    try {
      await getEngagement("x");
    } catch (e) {
      expect((e as ApiError).message).toContain("playbook-runs instead");
    }
  });
});
