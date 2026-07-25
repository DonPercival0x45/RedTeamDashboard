import { describe, expect, it } from "vitest";
import {
  normalizeIdentityValue,
  sameEntityIdentity,
} from "@/app/e/entities/entity-workbench-client";
import { scopeTargetForEntity } from "@/lib/entity-scope";

describe("entity workbench identity matching", () => {
  it("preserves email local-part case while normalizing its domain", () => {
    expect(normalizeIdentityValue("email", "Admin@EXAMPLE.COM.")).toBe(
      "Admin@example.com",
    );
    expect(
      sameEntityIdentity(
        "email",
        "Admin@example.com",
        "email",
        "admin@example.com",
      ),
    ).toBe(false);
  });

  it("normalizes URL host, default port, path, and fragment", () => {
    expect(
      normalizeIdentityValue(
        "url",
        "HTTPS://EXAMPLE.COM:443/path?x=1#analyst-note",
      ),
    ).toBe("https://example.com/path?x=1");
    expect(
      sameEntityIdentity(
        "url",
        "https://example.com",
        "website",
        "https://EXAMPLE.com:443/#fragment",
      ),
    ).toBe(true);
  });

  it("compresses IPs and canonicalizes IPv4 CIDR networks", () => {
    expect(normalizeIdentityValue("ip", "2001:0DB8:0:0:0:0:0:1")).toBe(
      "2001:db8::1",
    );
    expect(normalizeIdentityValue("cidr", "192.0.2.5/24")).toBe(
      "192.0.2.0/24",
    );
  });

  it("keeps unknown free-form identities case-sensitive", () => {
    expect(sameEntityIdentity("account", "Admin", "account", "admin")).toBe(
      false,
    );
  });

  it("maps supported discovered entities to authoritative scope kinds", () => {
    expect(scopeTargetForEntity({ type: "subdomain", value: "api.example.com" })).toEqual({
      kind: "domain",
      value: "api.example.com",
    });
    expect(scopeTargetForEntity({ type: "host", value: "203.0.113.4" })).toEqual({
      kind: "ip",
      value: "203.0.113.4",
    });
    expect(scopeTargetForEntity({ type: "host", value: "edge.example.com" })).toEqual({
      kind: "domain",
      value: "edge.example.com",
    });
    expect(scopeTargetForEntity({ type: "email", value: "a@example.com" })).toBeNull();
  });
});
