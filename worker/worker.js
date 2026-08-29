/**
 * BursaMusangKing app Worker.
 *
 * Serves the scan JSON to the PWA and relays "Run scan now" to GitHub Actions.
 * This is a SEPARATE Worker from the /run Telegram relay in your original
 * repo — deploy it on its own route so neither can break the other.
 *
 * Routes
 *   GET  /latest              -> newest scan results
 *   GET  /history             -> 3-month OHLC for every matched symbol
 *   GET  /history?symbol=XYZ  -> just that symbol's series
 *   GET  /weekly              -> weekly review stats
 *   GET  /backtest            -> train/test stats + trade list per strategy
 *   GET  /status              -> generated_at timestamps, for polling
 *   POST /run                 -> workflow_dispatch on the app repo
 *   POST /publish?key=latest  -> store JSON in KV (CI only, token-gated)
 *
 * Bindings (wrangler.toml + secrets)
 *   KV namespace : SCANS
 *   Secrets      : GITHUB_TOKEN, PUBLISH_TOKEN, RUN_TOKEN
 *   Vars         : GITHUB_REPO, WORKFLOW_FILE, ALLOWED_ORIGIN
 */

const JSON_KEYS = new Set(["latest", "history", "weekly", "backtest"]);

function cors(env) {
  return {
    "Access-Control-Allow-Origin": env.ALLOWED_ORIGIN || "*",
    "Access-Control-Allow-Methods": "GET,POST,OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type,X-Publish-Token,X-Run-Token",
  };
}

function json(body, env, status = 200, extra = {}) {
  return new Response(typeof body === "string" ? body : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...cors(env), ...extra },
  });
}

async function readKV(env, key) {
  const v = await env.SCANS.get(key);
  return v || null;
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (request.method === "OPTIONS") {
      return new Response(null, { status: 204, headers: cors(env) });
    }

    if (request.method === "GET" && (path === "/" || path === "/health")) {
      if (!env.SCANS) return json({ ok: false, error: "KV binding SCANS is not configured" }, env, 500);
      const keys = [];
      for (const key of JSON_KEYS) {
        if (await env.SCANS.get(key)) keys.push(key);
      }
      return json({ ok: true, keys: keys.sort(), service: "Bursa MusangKing V5.1" }, env);
    }

    // ---------------------------------------------------------- read routes
    if (request.method === "GET" &&
        (path === "/latest" || path === "/weekly" || path === "/backtest")) {
      const key = path.slice(1);
      const body = await readKV(env, key);
      if (!body) return json({ error: "no data yet", key }, env, 404);
      return json(body, env, 200, { "Cache-Control": "public, max-age=60" });
    }

    if (request.method === "GET" && path === "/history") {
      const body = await readKV(env, "history");
      if (!body) return json({ error: "no data yet" }, env, 404);
      const symbol = url.searchParams.get("symbol");
      if (!symbol) {
        return json(body, env, 200, { "Cache-Control": "public, max-age=300" });
      }
      let parsed;
      try {
        parsed = JSON.parse(body);
      } catch {
        return json({ error: "corrupt history blob" }, env, 500);
      }
      const series = (parsed.series || {})[symbol];
      if (!series) return json({ error: "unknown symbol", symbol }, env, 404);
      return json({ symbol, bars: parsed.bars, series }, env, 200, {
        "Cache-Control": "public, max-age=300",
      });
    }

    if (request.method === "GET" && path === "/status") {
      const out = {};
      for (const key of ["latest", "weekly", "backtest"]) {
        const body = await readKV(env, key);
        try {
          out[key] = body ? JSON.parse(body).generated_at : null;
        } catch {
          out[key] = null;
        }
      }
      return json(out, env, 200, { "Cache-Control": "no-store" });
    }

    // ------------------------------------------------------- trigger a scan
    if (request.method === "POST" && path === "/run") {
      // RUN_TOKEN is optional. Set it if you'd rather not leave the trigger
      // open to anyone who finds the Worker URL.
      if (env.RUN_TOKEN) {
        const given = request.headers.get("X-Run-Token");
        if (given !== env.RUN_TOKEN) {
          return json({ error: "unauthorized" }, env, 401);
        }
      }
      if (!env.GITHUB_TOKEN || !env.GITHUB_REPO) {
        return json({ error: "GITHUB_TOKEN / GITHUB_REPO not configured" }, env, 500);
      }

      const workflow = env.WORKFLOW_FILE || "app-scan.yml";
      const gh = await fetch(
        `https://api.github.com/repos/${env.GITHUB_REPO}/actions/workflows/${workflow}/dispatches`,
        {
          method: "POST",
          headers: {
            Authorization: `Bearer ${env.GITHUB_TOKEN}`,
            Accept: "application/vnd.github+json",
            "User-Agent": "bursamusangking-app-worker",
            "Content-Type": "application/json",
          },
          body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
        }
      );

      if (gh.status === 204) {
        return json({ ok: true, message: "scan queued" }, env);
      }
      const detail = await gh.text();
      return json({ ok: false, status: gh.status, detail: detail.slice(0, 300) }, env, 502);
    }

    // ---------------------------------------------------------- CI publishes
    if (request.method === "POST" && path === "/publish") {
      const given = request.headers.get("X-Publish-Token");
      if (!env.PUBLISH_TOKEN || given !== env.PUBLISH_TOKEN) {
        return json({ error: "unauthorized" }, env, 401);
      }
      const key = url.searchParams.get("key");
      if (!JSON_KEYS.has(key)) {
        return json({ error: "bad key", allowed: [...JSON_KEYS] }, env, 400);
      }
      const body = await request.text();
      try {
        JSON.parse(body);
      } catch {
        return json({ error: "body is not valid JSON" }, env, 400);
      }
      await env.SCANS.put(key, body);
      return json({ ok: true, key, bytes: body.length }, env);
    }

    return json({ error: "not found", path }, env, 404);
  },
};
