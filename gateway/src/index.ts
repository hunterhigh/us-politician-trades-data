import { createHash, timingSafeEqual } from "node:crypto";

const SHA = /^[a-f0-9]{40}$/;
const DIGEST = /^[a-f0-9]{64}$/;
const HEAD_LIMIT = 16 * 1024;
const INDEX_LIMIT = 8 * 1024;
const SHARD_LIMIT = 8 * 1024 * 1024;
const TIMEOUT_MS = 10_000;
const MAX_PATH = 300;

export type UpstreamFetch = (url: string, init: RequestInit) => Promise<Response>;

class GatewayError extends Error {
  constructor(readonly status: number, readonly code: string) {
    super(code);
  }
}

function fail(status: number, code: string): never {
  throw new GatewayError(status, code);
}

function outputHeaders(): Headers {
  return new Headers({
    "Content-Type": "application/json; charset=utf-8",
    "Cache-Control": "private, no-store",
    "CDN-Cache-Control": "no-store",
    "Vary": "Authorization",
    "X-Content-Type-Options": "nosniff",
  });
}

function errorResponse(status: number, code: string): Response {
  const headers = outputHeaders();
  if (status === 401) headers.set("WWW-Authenticate", "Bearer");
  if (status === 405) headers.set("Allow", "GET");
  return new Response(JSON.stringify({ error: code }), { status, headers });
}

function authenticate(request: Request, token: string): void {
  if (!token || token === "local-placeholder-not-for-deployment") fail(503, "not_configured");
  const authorization = request.headers.get("Authorization") ?? "";
  if (authorization.length > 4096) fail(401, "unauthorized");
  const supplied = /^Bearer ([\x21-\x7e]+)$/.exec(authorization)?.[1] ?? "";
  // Hash both operands to an equal length before the constant-time comparison.
  const actual = createHash("sha256").update(supplied).digest();
  const expected = createHash("sha256").update(token).digest();
  if (!timingSafeEqual(actual, expected)) fail(401, "unauthorized");
}

export function parseRoute(rawUrl: string): { ref: string; path: string; market: boolean } {
  if (rawUrl.length > 2048) fail(400, "invalid_path");
  // The protocol has ASCII, already-normalized paths; encoded spellings are unnecessary.
  const rawPath = rawUrl.replace(/^[a-z]+:\/\/[^/]+/i, "");
  if (/[\\%?#]/.test(rawPath) || /(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) {
    fail(400, "invalid_path");
  }
  const match = /^\/v1\/(main|[a-f0-9]{40})\/(.+)$/.exec(new URL(rawUrl).pathname);
  if (!match?.[1] || !match[2] || match[2].length > MAX_PATH) fail(404, "not_found");
  const ref = match[1];
  const path = match[2];
  const allowed = path === "manifest.json"
    || /^board\/[a-f0-9]{64}\.json$/.test(path)
    || /^market-pages\/[a-f0-9]{64}\.json$/.test(path)
    || /^(people|tickers|market)\/[a-f0-9]{2}\/(index|[a-f0-9]{64})\.json$/.test(path);
  if (!allowed || (ref === "main" && path !== "manifest.json")) fail(404, "not_found");
  return { ref, path, market: path.startsWith("market/") || path.startsWith("market-pages/") };
}

function object(value: unknown): Record<string, unknown> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) fail(502, "invalid_upstream");
  return value as Record<string, unknown>;
}

async function boundedBytes(response: Response, limit: number): Promise<Uint8Array> {
  if (!response.body) fail(502, "invalid_upstream");
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let size = 0;
  try {
    while (true) {
      const part = await reader.read();
      if (part.done) break;
      size += part.value.byteLength;
      if (size > limit) {
        await reader.cancel();
        fail(502, "upstream_too_large");
      }
      chunks.push(part.value);
    }
  } finally {
    reader.releaseLock();
  }
  const result = new Uint8Array(size);
  let offset = 0;
  for (const chunk of chunks) {
    result.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return result;
}

function json(bytes: Uint8Array): Record<string, unknown> {
  try {
    return object(JSON.parse(new TextDecoder("utf-8", { fatal: true, ignoreBOM: false }).decode(bytes)));
  } catch {
    return fail(502, "invalid_upstream");
  }
}

function validTimestamp(value: unknown): boolean {
  if (typeof value !== "string") return false;
  const match = /^(\d{4})-(\d{2})-(\d{2})T([01]\d|2[0-3]):([0-5]\d):([0-5]\d)(?:\.\d{1,9})?(?:Z|[+-](?:[01]\d|2[0-3]):[0-5]\d)$/.exec(value);
  if (!match) return false;
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (month < 1 || month > 12) return false;
  const leap = year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
  const days = [31, leap ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1];
  return days !== undefined && day >= 1 && day <= days && Number.isFinite(Date.parse(value));
}

function validateManifest(bytes: Uint8Array, env: Env): void {
  const manifest = json(bytes);
  if (manifest.schema_version !== "politician-dashboard/v1"
    || manifest.storage_layout !== "hash-sharded-v2"
    || manifest.is_demo !== false
    || typeof manifest.snapshot_id !== "string" || !manifest.snapshot_id
    || typeof manifest.board !== "string" || !DIGEST.test(manifest.board)
    || !validTimestamp(manifest.generated_at) || !validTimestamp(manifest.data_cutoff_at)) {
    fail(502, "invalid_manifest");
  }
  if (env.ALLOW_MARKET !== "true" && manifest.market_commit) fail(503, "market_disabled");
  if (manifest.market_commit !== undefined
    && (typeof manifest.market_commit !== "string" || !SHA.test(manifest.market_commit))) {
    fail(502, "invalid_manifest");
  }
  if (manifest.market_pages !== undefined
    && (!Array.isArray(manifest.market_pages) || manifest.market_pages.length === 0
      || manifest.market_pages.length > 100
      || manifest.market_pages.some((item) => typeof item !== "string" || !DIGEST.test(item)))) {
    fail(502, "invalid_manifest");
  }
  if (Boolean(manifest.market_commit) !== Boolean(manifest.market_pages)) {
    fail(502, "invalid_manifest");
  }
}

/** Injected transport is for local tests; deployed requests always use native fetch. */
export function createGateway(upstream: UpstreamFetch, timeoutMs = TIMEOUT_MS) {
  return async function handle(request: Request, env: Env): Promise<Response> {
    try {
      authenticate(request, env.PROXY_TOKEN);
      if (request.method !== "GET") fail(405, "method_not_allowed");
      const route = parseRoute(request.url);
      if (route.market && env.ALLOW_MARKET !== "true") fail(404, "not_found");
      if (!/^[A-Za-z0-9-]+$/.test(env.GITHUB_OWNER)
        || !/^[A-Za-z0-9_.-]+$/.test(env.GITHUB_REPO)
        || env.GITHUB_OWNER.startsWith("REPLACE_") || env.GITHUB_REPO.startsWith("REPLACE_")
        || !env.GITHUB_READ_TOKEN || env.GITHUB_READ_TOKEN === "local-placeholder-not-for-deployment") {
        fail(503, "not_configured");
      }
      const base = `https://api.github.com/repos/${env.GITHUB_OWNER}/${env.GITHUB_REPO}`;

      async function get(path: string, raw = false, limit = HEAD_LIMIT): Promise<Response> {
        const controller = new AbortController();
        const timer = setTimeout(() => controller.abort(), timeoutMs);
        let response: Response;
        try {
          response = await upstream(`${base}/${path}`, {
            method: "GET",
            redirect: "manual",
            cache: "no-store",
            signal: controller.signal,
            headers: {
              Authorization: `Bearer ${env.GITHUB_READ_TOKEN}`,
              Accept: raw ? "application/vnd.github.raw+json" : "application/vnd.github+json",
              "X-GitHub-Api-Version": "2022-11-28",
              "User-Agent": "unison-readonly-gateway",
            },
          });
        } catch {
          clearTimeout(timer);
          return fail(controller.signal.aborted ? 504 : 502, "upstream_unavailable");
        }
        if (response.status !== 200 || response.redirected || !response.body) {
          clearTimeout(timer);
          await response.body?.cancel().catch(() => undefined);
          if (response.status === 404) fail(404, "not_found");
          if (response.status === 429) fail(503, "upstream_unavailable");
          fail(502, "upstream_unavailable");
        }
        // Keep the deadline alive until the stream ends, not just until headers arrive.
        const reader = response.body.getReader();
        let bytesRead = 0;
        const stream = new ReadableStream<Uint8Array>({
          async pull(target) {
            try {
              const part = await reader.read();
              if (part.done) {
                clearTimeout(timer);
                target.close();
                reader.releaseLock();
              } else {
                bytesRead += part.value.byteLength;
                if (bytesRead > limit) {
                  clearTimeout(timer);
                  target.error(new GatewayError(502, "upstream_too_large"));
                  controller.abort();
                  await reader.cancel().catch(() => undefined);
                  reader.releaseLock();
                  return;
                }
                target.enqueue(part.value);
              }
            } catch {
              clearTimeout(timer);
              target.error(new Error("upstream_unavailable"));
              reader.releaseLock();
            }
          },
          async cancel() {
            clearTimeout(timer);
            controller.abort();
            await reader.cancel().catch(() => undefined);
            reader.releaseLock();
          },
        });
        return new Response(stream);
      }

      async function resolveRef(refPath: string): Promise<string> {
        const result = json(await boundedBytes(await get(`git/ref/${refPath}`), HEAD_LIMIT));
        const reference = object(result.object);
        if (reference.type !== "commit" || typeof reference.sha !== "string" || !SHA.test(reference.sha)) {
          fail(502, "invalid_upstream");
        }
        return reference.sha;
      }

      const commit = route.ref === "main" ? await resolveRef("heads/main") : route.ref;
      const tag = route.market ? "published-market" : "published";
      let published: string;
      try {
        published = await resolveRef(`tags/${tag}/${commit}`);
      } catch (error) {
        if (error instanceof GatewayError && error.status === 404 && route.ref === "main") {
          fail(503, "publication_pending");
        }
        throw error;
      }
      if (published !== commit) fail(404, "not_found");

      const headers = outputHeaders();
      headers.set("X-Snapshot-Commit", commit);
      if (!route.market) {
        // A known SHA/tag is not enough: a demo manifest must not be bypassed by
        // directly requesting a board/index/entity shard at that commit.
        const bytes = await boundedBytes(await get(`contents/manifest.json?ref=${commit}`, true), HEAD_LIMIT);
        validateManifest(bytes, env);
        if (route.path === "manifest.json") return new Response(bytes, { headers });
      }
      const limit = route.path.endsWith("/index.json") ? INDEX_LIMIT : SHARD_LIMIT;
      const result = await get(`contents/${route.path}?ref=${commit}`, true, limit);
      return new Response(result.body, { headers });
    } catch (error) {
      if (error instanceof GatewayError) return errorResponse(error.status, error.code);
      // Never return upstream bodies, exception text, credentials or repository identity.
      return errorResponse(502, "upstream_unavailable");
    }
  };
}

export default {
  fetch(request, env) {
    return createGateway((url, init) => fetch(url, init))(request, env);
  },
} satisfies ExportedHandler<Env>;
