import assert from "node:assert/strict";
import { test } from "node:test";
import { createGateway, parseRoute, type UpstreamFetch } from "../src/index.ts";

const COMMIT = "a".repeat(40);
const NEXT = "b".repeat(40);
const HASH = "c".repeat(64);
const env: Env = {
  GITHUB_OWNER: "test-owner",
  GITHUB_REPO: "test-private-repo",
  ALLOW_MARKET: "false",
  PROXY_TOKEN: "synthetic-proxy-test-token",
  GITHUB_READ_TOKEN: "synthetic-github-test-token",
};
const manifest = {
  schema_version: "politician-dashboard/v1",
  storage_layout: "hash-sharded-v2",
  snapshot_id: HASH,
  board: HASH,
  is_demo: false,
  generated_at: "2026-09-18T08:00:00Z",
  data_cutoff_at: "2026-09-17T16:00:00-04:00",
};
function request(path = "main/manifest.json", headers: Record<string, string> = {}) {
  return new Request(`https://gateway.test/v1/${path}`, {
    headers: { Authorization: `Bearer ${env.PROXY_TOKEN}`, ...headers },
  });
}
function reference(sha: string) {
  return Response.json({ object: { type: "commit", sha } });
}
function upstreamWith(body: unknown = manifest, metadata: unknown = body): { fetch: UpstreamFetch; calls: string[] } {
  const calls: string[] = [];
  return {
    calls,
    fetch: async (url, init) => {
      calls.push(url);
      assert.ok(url.startsWith("https://api.github.com/repos/test-owner/test-private-repo/"));
      assert.equal(init.redirect, "manual");
      assert.equal(init.cache, "no-store");
      assert.equal(new Headers(init.headers).get("Authorization"), `Bearer ${env.GITHUB_READ_TOKEN}`);
      assert.ok(init.signal);
      if (url.endsWith("git/ref/heads/main") || url.endsWith(`git/ref/tags/published/${COMMIT}`)
        || url.endsWith(`git/ref/tags/published-market/${COMMIT}`)) return reference(COMMIT);
      if (url.includes("/contents/")) {
        assert.ok(url.endsWith(`?ref=${COMMIT}`), "content must be pinned to the resolved commit");
        return Response.json(url.includes("/contents/manifest.json?") ? metadata : body);
      }
      return new Response("upstream private diagnostic", { status: 404 });
    },
  };
}

test("main resolves exactly once then fetches manifest at an authorized immutable commit", async () => {
  const mock = upstreamWith();
  const response = await createGateway(mock.fetch)(request(), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), manifest);
  assert.equal(response.headers.get("X-Snapshot-Commit"), COMMIT);
  assert.match(response.headers.get("Cache-Control") ?? "", /no-store/);
  assert.equal(response.headers.get("Vary"), "Authorization");
  assert.equal(mock.calls.length, 3);
  assert.ok(!mock.calls.at(-1)?.includes("ref=main"));
});

test("fixed SHA never re-resolves the moving main branch", async () => {
  const mock = upstreamWith({ people: [] }, manifest);
  const response = await createGateway(mock.fetch)(request(`${COMMIT}/board/${HASH}.json`), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), { people: [] });
  assert.equal(mock.calls.length, 3);
  assert.ok(mock.calls.every((url) => !url.endsWith("heads/main")));
});

test("missing, wrong and oversized credentials never contact upstream", async () => {
  const mock = upstreamWith();
  for (const auth of ["", "Bearer wrong", "Basic abc", `Bearer ${"x".repeat(5000)}`]) {
    const response = await createGateway(mock.fetch)(request(undefined, { Authorization: auth }), env);
    assert.equal(response.status, 401);
    assert.deepEqual(await response.json(), { error: "unauthorized" });
  }
  assert.equal(mock.calls.length, 0);
});

test("each request authenticates independently; a previous success grants no cache access", async () => {
  const mock = upstreamWith();
  const gateway = createGateway(mock.fetch);
  await (await gateway(request(), env)).arrayBuffer();
  const response = await gateway(request(undefined, { Authorization: "Bearer wrong" }), env);
  assert.equal(response.status, 401);
  assert.equal(mock.calls.length, 3);
});

test("unsafe paths, mutable indexes, unknown refs and internal files are rejected", async () => {
  const mock = upstreamWith();
  for (const path of [
    "main/people/aa/index.json", "demo/manifest.json", "market/manifest.json",
    `${COMMIT}/status/last-run.json`, `${COMMIT}/raw/filing.pdf`, `${COMMIT}/.github/workflows/publish.yml`,
    `${COMMIT}/manifest.json?repo=other`, `${COMMIT}/people/%61a/index.json`,
    `${COMMIT}/people/aa/%252e%252e/index.json`, `${COMMIT}/people/aa/not-a-hash.json`,
    `${COMMIT}/https://attacker.test/manifest.json`,
  ]) {
    const response = await createGateway(mock.fetch)(request(path), env);
    assert.ok([400, 404].includes(response.status), path);
  }
  assert.equal(mock.calls.length, 0);
});

test("raw traversal and backslash routes are rejected before URL normalization", () => {
  for (const path of ["../manifest.json", "people/../manifest.json", "people\\aa\\index.json", "people/%2e%2e/index.json"]) {
    assert.throws(() => parseRoute(`https://gateway.test/v1/${COMMIT}/${path}`));
  }
});

test("only GET is accepted", async () => {
  const mock = upstreamWith();
  const post = new Request(request(), { method: "POST" });
  const response = await createGateway(mock.fetch)(post, env);
  assert.equal(response.status, 405);
  assert.equal(response.headers.get("Allow"), "GET");
  assert.equal(mock.calls.length, 0);
});

test("unknown SHA is denied even when it is a syntactically valid hash", async () => {
  const mock = upstreamWith();
  const response = await createGateway(mock.fetch)(request(`${NEXT}/board/${HASH}.json`), env);
  assert.equal(response.status, 404);
  assert.equal(mock.calls.length, 1);
});

test("a retained tag must point to the requested SHA", async () => {
  const response = await createGateway(async () => reference(NEXT))(request(`${COMMIT}/board/${HASH}.json`), env);
  assert.equal(response.status, 404);
});

test("main waits with 503 if its publication tag is not visible yet", async () => {
  const response = await createGateway(async (url) => url.endsWith("heads/main")
    ? reference(COMMIT) : new Response("private", { status: 404 }))(request(), env);
  assert.equal(response.status, 503);
  assert.deepEqual(await response.json(), { error: "publication_pending" });
});

test("annotated or malformed tags are not treated as commit authorization", async () => {
  for (const object of [{ type: "tag", sha: COMMIT }, { type: "commit", sha: "main" }, {}]) {
    const response = await createGateway(async () => Response.json({ object }))(
      request(`${COMMIT}/board/${HASH}.json`), env,
    );
    assert.equal(response.status, 502);
  }
});

test("redirects, upstream credential errors and diagnostics never escape", async () => {
  for (const status of [302, 401, 403, 500]) {
    const response = await createGateway(async () => new Response(
      `private repository ${env.GITHUB_READ_TOKEN}`, { status, headers: { Location: "https://attacker.test" } },
    ))(request(), env);
    assert.equal(response.status, 502);
    assert.equal(await response.text(), '{"error":"upstream_unavailable"}');
    assert.equal(response.headers.get("Location"), null);
  }
});

test("thrown upstream exceptions are sanitized", async () => {
  const response = await createGateway(async () => { throw new Error(env.GITHUB_READ_TOKEN); })(request(), env);
  assert.equal(await response.text(), '{"error":"upstream_unavailable"}');
});

test("a stalled upstream fetch observes a bounded abort deadline", async () => {
  const fetch: UpstreamFetch = (_url, init) => new Promise((_resolve, reject) => {
    init.signal?.addEventListener("abort", () => reject(new Error("aborted")), { once: true });
  });
  const response = await createGateway(fetch, 10)(request(), env);
  assert.equal(response.status, 504);
});

test("manifest and metadata responses are bounded even without Content-Length", async () => {
  const oversized = upstreamWith({ ...manifest, padding: "x".repeat(17 * 1024) });
  const response = await createGateway(oversized.fetch)(request(), env);
  assert.equal(response.status, 502);
  assert.deepEqual(await response.json(), { error: "upstream_too_large" });
  const metadata = await createGateway(async () => new Response("x".repeat(17 * 1024)))(request(), env);
  assert.equal(metadata.status, 502);
});

test("demo, absent demo flag, wrong schema, missing id and invalid JSON fail closed", async () => {
  for (const body of [
    { ...manifest, is_demo: true }, { ...manifest, is_demo: undefined },
    { ...manifest, schema_version: "v99" }, { ...manifest, snapshot_id: "" },
    { ...manifest, board: "../escape" }, [manifest],
  ]) {
    const response = await createGateway(upstreamWith(body).fetch)(request(), env);
    assert.equal(response.status, 502);
  }
});

test("market paths and enabled-market manifests are closed by default", async () => {
  const mock = upstreamWith({ ...manifest, market_commit: COMMIT });
  const gateway = createGateway(mock.fetch);
  assert.equal((await gateway(request(`${COMMIT}/market/aa/index.json`), env)).status, 404);
  assert.equal(mock.calls.length, 0);
  assert.equal((await gateway(request(), env)).status, 503);
});

test("market opt-in uses its own retained tag namespace", async () => {
  for (const path of [`market/aa/${HASH}.json`, `market-pages/${HASH}.json`]) {
    const mock = upstreamWith({ security_market_data: [] });
    const response = await createGateway(mock.fetch)(request(`${COMMIT}/${path}`), {
      ...env, ALLOW_MARKET: "true",
    });
    assert.equal(response.status, 200);
    await response.arrayBuffer();
    assert.ok(mock.calls[0]?.includes("tags/published-market/"));
  }
});

test("large shards stream without imposing the small manifest limit", async () => {
  const body = { transactions: [], padding: "x".repeat(200_000) };
  const response = await createGateway(upstreamWith(body, manifest).fetch)(request(`${COMMIT}/people/aa/${HASH}.json`), env);
  assert.equal(response.status, 200);
  assert.deepEqual(await response.json(), body);
});

test("a shard response is returned before its unbounded body completes", async () => {
  let source: ReadableStreamDefaultController<Uint8Array> | undefined;
  const upstream: UpstreamFetch = async (url) => {
    if (url.includes("/git/ref/")) return reference(COMMIT);
    if (url.includes("/contents/manifest.json?")) return Response.json(manifest);
    return new Response(new ReadableStream<Uint8Array>({ start(controller) { source = controller; } }));
  };
  const response = await createGateway(upstream)(request(`${COMMIT}/board/${HASH}.json`), env);
  assert.equal(response.status, 200);
  assert.ok(source);
  source.enqueue(new TextEncoder().encode('{"people":[]}'));
  source.close();
  assert.equal(await response.text(), '{"people":[]}');
});

test("a truncated upstream body does not return a valid complete shard", async () => {
  const upstream: UpstreamFetch = async (url) => {
    if (url.includes("/git/ref/")) return reference(COMMIT);
    if (url.includes("/contents/manifest.json?")) return Response.json(manifest);
    return new Response(new ReadableStream<Uint8Array>({ start(controller) {
      controller.error(new Error(`private ${env.GITHUB_READ_TOKEN}`));
    } }));
  };
  const response = await createGateway(upstream)(request(`${COMMIT}/board/${HASH}.json`), env);
  await assert.rejects(response.text(), /upstream_unavailable/);
});

test("invalid JSON and invalid UTF-8 in manifest are rejected", async () => {
  for (const bytes of [new TextEncoder().encode("not json"), new Uint8Array([0xff, 0xfe])]) {
    const upstream: UpstreamFetch = async (url) => url.includes("/git/ref/")
      ? reference(COMMIT) : new Response(bytes);
    const response = await createGateway(upstream)(request(), env);
    assert.equal(response.status, 502);
    assert.equal(await response.text(), '{"error":"invalid_upstream"}');
  }
});

test("placeholder configuration refuses all upstream access", async () => {
  const mock = upstreamWith();
  const response = await createGateway(mock.fetch)(request(), { ...env, GITHUB_OWNER: "REPLACE_OWNER" });
  assert.equal(response.status, 503);
  assert.equal(mock.calls.length, 0);
});

test("a published tag cannot bypass a demo manifest through direct SHA paths", async () => {
  for (const path of [`board/${HASH}.json`, "people/aa/index.json", `tickers/aa/${HASH}.json`]) {
    const mock = upstreamWith({ people: [] }, { ...manifest, is_demo: true });
    const response = await createGateway(mock.fetch)(request(`${COMMIT}/${path}`), env);
    assert.equal(response.status, 502);
    assert.deepEqual(await response.json(), { error: "invalid_manifest" });
    assert.equal(mock.calls.length, 2, "rejected before the requested shard is fetched");
    assert.ok(mock.calls.at(-1)?.includes("/contents/manifest.json?"));
  }
});

test("both mandatory timestamps require valid calendar dates and explicit timezones", async () => {
  for (const field of ["generated_at", "data_cutoff_at"]) {
    for (const value of [undefined, null, "", "2026-09-18", "2026-09-18T08:00:00",
      "2026-02-30T08:00:00Z", "2026-02-29T08:00:00Z", "2026-13-01T08:00:00Z",
      "2026-09-18T24:00:00Z", "2026-09-18T08:00:00+25:00", "tomorrow"]) {
      const response = await createGateway(upstreamWith({ ...manifest, [field]: value }).fetch)(request(), env);
      assert.equal(response.status, 502, `${field}: ${String(value)}`);
    }
  }
  const response = await createGateway(upstreamWith({
    ...manifest, generated_at: "2024-02-29T08:00:00.123456+08:00",
  }).fetch)(request(), env);
  assert.equal(response.status, 200);
  await response.arrayBuffer();
});

function rawFileUpstream(bytes: string): UpstreamFetch {
  return async (url) => {
    if (url.includes("/git/ref/")) return reference(COMMIT);
    if (url.includes("/contents/manifest.json?")) return Response.json(manifest);
    return new Response(bytes);
  };
}

test("every index class enforces an 8 KiB streaming boundary", async () => {
  for (const cls of ["people", "tickers", "market"]) {
    for (const excess of [0, 1]) {
      const bytes = `"${"x".repeat(8 * 1024 - 2 + excess)}"`;
      const response = await createGateway(rawFileUpstream(bytes))(
        request(`${COMMIT}/${cls}/aa/index.json`), { ...env, ALLOW_MARKET: "true" },
      );
      assert.equal(response.status, 200);
      if (excess) await assert.rejects(response.json(), /upstream_too_large/);
      else assert.equal(await response.text(), bytes);
    }
  }
});

test("board, person, ticker and market shards enforce a 16 MiB streaming boundary", async () => {
  for (const cls of ["board", "people", "tickers", "market"]) {
    const path = cls === "board" ? `${cls}/${HASH}.json` : `${cls}/aa/${HASH}.json`;
    const bytes = `"${"x".repeat(16 * 1024 * 1024 - 1)}"`;
    const response = await createGateway(rawFileUpstream(bytes))(
      request(`${COMMIT}/${path}`), { ...env, ALLOW_MARKET: "true" },
    );
    assert.equal(response.status, 200);
    await assert.rejects(response.json(), /upstream_too_large/);
  }
});
