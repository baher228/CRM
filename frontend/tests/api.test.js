import { afterEach, describe, expect, it, vi } from "vitest";

import { api, routeForResult, unwrapList, unwrapPage } from "../src/api";

afterEach(() => vi.unstubAllGlobals());

describe("API response helpers", () => {
  it("accepts both cursor resources and legacy arrays", () => {
    expect(unwrapList({ items: [{ id: 1 }], next_cursor: "two" })).toEqual([{ id: 1 }]);
    expect(unwrapList([{ id: 2 }])).toEqual([{ id: 2 }]);
    expect(unwrapList(null)).toEqual([]);
    expect(unwrapPage({ items: [{ id: 1 }], next_cursor: "two" })).toEqual({
      items: [{ id: 1 }],
      nextCursor: "two",
    });
    expect(unwrapPage([{ id: 2 }])).toEqual({ items: [{ id: 2 }], nextCursor: null });
  });

  it("builds deep links for search results", () => {
    expect(routeForResult({ type: "account", id: 42 })).toBe("/accounts/42");
    expect(routeForResult({ resource_type: "deal", id: "abc" })).toBe("/opportunities/abc");
    expect(routeForResult({ entity_type: "contact", entity_id: 9 })).toBe("/contacts/9");
    expect(routeForResult({ type: "unknown", id: 2 })).toBe("/");
  });

  it("uses the versioned same-origin API and idempotency keys", async () => {
    const fetchMock = vi.fn(async (url) => new Response(
      JSON.stringify(url.endsWith("/session") ? { authenticated: true, csrf_token: "csrf-test" } : { id: 7 }),
      { status: 200, headers: { "content-type": "application/json" } },
    ));
    vi.stubGlobal("fetch", fetchMock);
    await api.post("accounts", { name: "North Star" });
    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/session");
    const [url, options] = fetchMock.mock.calls[1];
    expect(url).toBe("/api/v1/accounts");
    expect(options.method).toBe("POST");
    expect(options.headers["Idempotency-Key"]).toBeTruthy();
    expect(options.headers["X-CSRF-Token"]).toBe("csrf-test");
    expect(options.credentials).toBe("same-origin");

    await api.put("contact/7/tags", [3]);
    const [putUrl, putOptions] = fetchMock.mock.calls.at(-1);
    expect(putUrl).toBe("/api/v1/contact/7/tags");
    expect(putOptions.method).toBe("PUT");
    expect(putOptions.headers["Idempotency-Key"]).toBeTruthy();
    expect(putOptions.headers["X-CSRF-Token"]).toBe("csrf-test");
  });
});
