/* BursaMusangKing — PWA front end */
(() => {
  const CFG = window.BMK_CONFIG || {};
  const API = (CFG.WORKER_URL || "").replace(/\/+$/, "");
  const $ = (id) => document.getElementById(id);
  const C = window.BMKChart;

  const LABELS = {
    trending: "Trend",
    early_uptrend: "Early",
    reversal: "Reversal",
    gaining_momentum: "Momentum",
    base_breakout: "Breakout",
    meta_leader: "META",
  };
  // Full names for the detail header / weekly blocks where there's room.
  const LABELS_FULL = {
    trending: "Trending",
    early_uptrend: "Early Uptrend",
    reversal: "Confirmed Reversal",
    gaining_momentum: "Gaining Momentum",
    base_breakout: "Base Breakout",
    meta_leader: "M.E.T.A. Leader",
  };
  const STRATEGY_ORDER = Object.keys(LABELS);

  let latest = null, weekly = null, backtest = null;
  let btPolicy = null;
  let btStrat = null, btOpen = null;
  const historyCache = {};
  let filter = null, current = null, view = "list";
  let pollTimer = null, resizeTimer = null;

  // ------------------------------------------------------------------ theme
  const media = window.matchMedia("(prefers-color-scheme: dark)");
  function applyTheme(mode) {
    document.documentElement.setAttribute("data-theme", mode);
    $("theme").textContent = mode === "dark" ? "☀" : "◐";
    document.querySelector('meta[name="theme-color"]')
      .setAttribute("content", mode === "dark" ? "#1c1c1a" : "#ffffff");
    redraw();
  }
  let theme = localStorage.getItem("bmk-theme") || (media.matches ? "dark" : "light");
  applyTheme(theme);
  $("theme").onclick = () => {
    theme = theme === "dark" ? "light" : "dark";
    localStorage.setItem("bmk-theme", theme);
    applyTheme(theme);
  };
  media.addEventListener("change", (e) => {
    if (localStorage.getItem("bmk-theme")) return;
    theme = e.matches ? "dark" : "light";
    applyTheme(theme);
  });

  // Canvas has no CSS reflow, so anything that changes size or colour needs an
  // explicit repaint: theme flips, rotation, window resize.
  window.addEventListener("resize", () => {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(redraw, 150);
  });

  function redraw() {
    if (view === "list" && latest) drawSparks();
    if (view === "detail" && current) drawDetail();
    if (view === "weekly" && weekly) C.line($("w-chart"), weekly.equity_curve || []);
    if (view === "backtest" && backtest) drawBtChart();
  }

  // ----------------------------------------------------------------- banner
  function banner(msg, kind) {
    const el = $("banner");
    if (!msg) { el.className = "banner"; el.textContent = ""; return; }
    el.className = "banner show" + (kind === "err" ? " err" : "");
    el.textContent = msg;
  }

  // ------------------------------------------------------------------ fetch
  const DEMO = !API || API.includes("example.workers.dev");

  async function get(path) {
    if (DEMO) {
      const key = path.startsWith("/history") ? "history" : path.slice(1).split("?")[0];
      if (key === "status") {
        const l = await (await fetch("demo/latest.json")).json();
        return { latest: l.generated_at, weekly: null };
      }
      const r = await fetch(`demo/${key}.json`, { cache: "no-store" });
      if (!r.ok) throw new Error(`demo/${key}.json → ${r.status}`);
      const data = await r.json();
      const sym = new URLSearchParams(path.split("?")[1] || "").get("symbol");
      if (sym) {
        const series = (data.series || {})[sym];
        if (!series) throw new Error("unknown symbol");
        return { symbol: sym, bars: data.bars, series };
      }
      return data;
    }
    const r = await fetch(API + path, { cache: "no-store" });
    if (!r.ok) throw new Error(`${path} → ${r.status}`);
    return r.json();
  }

  async function loadAll() {
    try {
      latest = await get("/latest");
      renderChips();
      renderList();
      $("updated").textContent = "Scan date " + (latest.scan_date || new Date(latest.generated_at).toLocaleDateString()) +
        " · Updated " + fmtTime(latest.generated_at);
    } catch (e) {
      banner("Couldn't load scan results. " + e.message, "err");
      $("count").textContent = "";
    }
    try {
      weekly = await get("/weekly");
      renderWeekly();
    } catch { /* weekly stays empty until the first review runs */ }
    try {
      backtest = await get("/backtest");
      renderBacktest();
    } catch {
      $("bt-meta").textContent = "No backtest yet — run the App Backtest workflow.";
    }
  }

  function fmtTime(iso) {
    if (!iso) return "–";
    return new Date(iso).toLocaleString(undefined, {
      day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit",
    });
  }

  // ------------------------------------------------------------------- list
  function visible() {
    if (!latest) return [];
    const all = latest.stocks || [];
    return filter ? all.filter((s) => (s.strategies || [s.strategy]).includes(filter)) : all;
  }

  function renderChips() {
    const counts = {};
    (latest.stocks || []).forEach((s) => {
      (s.strategies || [s.strategy]).forEach((strategy) => {
        counts[strategy] = (counts[strategy] || 0) + 1;
      });
    });
    const reported = (latest.strategies || Object.keys(counts))
      .map((s) => typeof s === "string" ? s : s.key);
    const strats = [...STRATEGY_ORDER, ...reported]
      .filter((s, i, all) => s && all.indexOf(s) === i);
    if (filter === null) filter = strats.find((s) => counts[s]) || strats[0];

    $("chips").innerHTML = strats.map((s) => `
      <button class="chip${s === filter ? " on" : ""}" data-s="${s}">
        ${LABELS[s] || s}<span class="n">${counts[s] || 0}</span>
      </button>`).join("");

    $("chips").querySelectorAll(".chip").forEach((c) => {
      c.onclick = () => { filter = c.dataset.s; renderChips(); renderList(); };
    });
  }

  function esc(s) {
    return String(s || "").replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  function renderList() {
    const rows = visible();
    const cur = latest.currency || "";
    $("count").textContent =
      `${rows.length} match${rows.length === 1 ? "" : "es"} · ` +
      `${latest.stocks_screened} screened`;

    if (!rows.length) {
      $("list").innerHTML =
        `<p class="empty">No ${LABELS[filter] || filter} matches in this scan.</p>`;
      return;
    }

    $("list").innerHTML = rows.map((s, i) => {
      const dir = s.change_pct >= 0 ? "up" : "down";
      const sign = s.change_pct > 0 ? "+" : "";
      return `
      <div class="row" data-i="${i}">
        <div class="spark"><canvas id="sp${i}"></canvas></div>
        <div class="row-mid">
          <p class="name">${esc(s.symbol)}${s.is_new ? '<span class="badge">NEW</span>' : ""}${s.status === "WATCH" ? '<span class="badge">WATCH</span>' : ""}</p>
          ${s.name ? `<p class="co">${esc(s.name)}</p>` : ""}
          <p class="sub">Score ${s.score ?? "–"} · RSI ${s.rsi ?? "–"} · ADX ${s.adx ?? "–"} · Vol ${s.vol_ratio ?? "–"}x</p>
        </div>
        <div class="row-end">
          <p class="chg ${dir}">${sign}${s.change_pct}%</p>
          <p class="px">${cur} ${s.close ?? s.price}</p>
        </div>
      </div>`;
    }).join("");

    drawSparks();
    $("list").querySelectorAll(".row").forEach((r) => {
      r.onclick = () => openDetail(rows[+r.dataset.i]);
    });
  }

  function drawSparks() {
    visible().forEach((s, i) => {
      const el = $("sp" + i);
      if (el) C.sparkline(el, s.spark);
    });
  }

  // ----------------------------------------------------------------- detail
  async function openDetail(s) {
    current = s;
    show("detail");
    const cur = latest.currency || "";
    const cat = (LABELS[s.strategy] || s.strategy).toUpperCase();

    $("d-title").innerHTML =
      `${esc(s.symbol)}<span class="sep">|</span>` +
      `<span class="cat">${esc(cat)}</span><span class="sep">|</span>` +
      `<span class="stat">${cur}${s.close ?? s.price}</span>` +
      `<span class="stat">Score ${s.score ?? "–"}</span>` +
      `<span class="stat">RSI ${s.rsi ?? "–"}</span>` +
      `<span class="stat">ADX ${s.adx ?? "–"}</span>` +
      `<span class="stat">Vol ${s.vol_ratio ?? "–"}x</span>`;
    $("d-co").textContent = s.name || "";
    $("title").textContent = s.symbol;
    $("d-entry").textContent = s.entry_low != null
      ? `${cur} ${s.entry_low} – ${s.entry_high}`
      : (s.entry != null ? `${cur} ${s.entry}` : "–");
    $("d-stop").textContent = s.stop != null ? `${cur} ${s.stop}` : "–";

    if (!historyCache[s.symbol]) {
      try {
        const r = await get("/history?symbol=" + encodeURIComponent(s.symbol));
        historyCache[s.symbol] = r.series;
      } catch {
        historyCache[s.symbol] = null;
      }
    }
    if (current === s) drawDetail();
  }

  function drawDetail() {
    const s = historyCache[current.symbol];
    if (s) {
      C.detail($("d-chart"), s);
    } else {
      // No 3-month history for this symbol — fall back to the 20-bar thumbnail
      // data so the screen still shows something rather than an empty box.
      C.detail($("d-chart"), Object.assign({ t: [], v: [] }, current.spark));
    }
  }

  $("back").onclick = () => { current = null; show("list"); };

  // ----------------------------------------------------------------- weekly
  function renderWeekly() {
    if (!weekly) return;
    const o = weekly.overall || {};
    const card = (l, v, cls) =>
      `<div class="c"><p>${l}</p><p class="${cls || ""}">${v}</p></div>`;

    $("w-cards").innerHTML = o.trades
      ? card("Win rate", o.win_rate + "%") +
        card("Profit factor", o.profit_factor ?? "–") +
        card("Signals", o.trades) +
        card("Worst", o.worst + "%", "down")
      : card("Win rate", "–") + card("Profit factor", "–") +
        card("Signals", "0") + card("Worst", "–");

    $("w-curve-label").textContent =
      `Cumulative signal performance · last ${weekly.lookback_weeks} weeks`;

    $("w-strats").innerHTML = (weekly.strategies || []).map((s) => {
      const h = s.horizons || {};
      const line = (n) => h[n]
        ? `+${n}d: ${h[n].win_rate}% win · avg ${h[n].avg > 0 ? "+" : ""}${h[n].avg}% (n=${h[n].n})`
        : null;
      const parts = [5, 10, 20].map(line).filter(Boolean);
      return `<div class="strat-block">
        <h3>${LABELS_FULL[s.strategy] || LABELS[s.strategy] || s.strategy} — ${s.signals} new signals</h3>
        ${parts.map((p) => `<p class="l">${p}</p>`).join("")}
        <p class="l">best ${esc(s.best.symbol)} ${s.best.ret > 0 ? "+" : ""}${s.best.ret}% ·
           worst ${esc(s.worst.symbol)} ${s.worst.ret > 0 ? "+" : ""}${s.worst.ret}%</p>
      </div>`;
    }).join("") || `<p class="empty">${esc(weekly.note || "No review data yet.")}</p>`;

    $("w-note").textContent = weekly.note || "";
    if (view === "weekly") C.line($("w-chart"), weekly.equity_curve || []);
  }

  // --------------------------------------------------------------- backtest
  const LEVEL_ICON = { good: "✓", warn: "!", bad: "✕", thin: "·" };

  function btCurrent() {
    if (!backtest) return null;
    const list = backtest.strategies || [];
    if (!btStrat) {
      // default to the strategy with the most test trades, not just the first —
      // a strategy with 3 trades tells you nothing and shouldn't open by default
      const best = list.reduce((a, b) =>
        ((b.test || {}).trades || 0) > ((a.test || {}).trades || 0) ? b : a, list[0]);
      btStrat = best && best.strategy;
    }
    return list.find((x) => x.strategy === btStrat) || list[0] || null;
  }

  function renderBacktest() {
    if (!backtest) return;
    const cur = btCurrent();
    $("bt-meta").textContent =
      `${backtest.universe_size} stocks · ${backtest.date_from || "?"} – ${backtest.date_to || "?"}` +
      (cur ? ` · ${cur.trades_total || 0} trades` : "");

    $("bt-chips").innerHTML = (backtest.strategies || []).map((s) => `
      <button class="chip${s.strategy === btStrat ? " on" : ""}" data-s="${s.strategy}">
        ${LABELS[s.strategy] || s.strategy}<span class="n">${(s.test || {}).trades || 0}</span>
      </button>`).join("");
    $("bt-chips").querySelectorAll(".chip").forEach((c) => {
      c.onclick = () => {
        btStrat = c.dataset.s; btOpen = null; btPolicy = null;
        closeTrades(); renderBacktest();
      };
    });

    if (!cur) return;

    // Exit-policy toggle: cur.policies holds both exits; default to chosen_policy.
    const policies = cur.policies || null;
    if (policies && !btPolicy) btPolicy = cur.chosen_policy || Object.keys(policies)[0];
    const view = policies && btPolicy && policies[btPolicy] ? policies[btPolicy] : cur;

    if (policies) {
      const order = (backtest.exit_policies || []).map((e) => e.key)
        .filter((k) => policies[k]);
      const keys = order.length ? order : Object.keys(policies);
      $("bt-toggle").innerHTML = keys.map((k) => `
        <button class="seg${k === btPolicy ? " on" : ""}" data-p="${k}">
          ${esc(policies[k].label || k)}</button>`).join("");
      $("bt-toggle").style.display = "";
      $("bt-toggle").querySelectorAll(".seg").forEach((b) => {
        b.onclick = () => { btPolicy = b.dataset.p; btOpen = null; closeTrades(); renderBacktest(); };
      });
    } else {
      $("bt-toggle").style.display = "none";
    }

    const v = view.verdict || cur.verdict || {};
    $("bt-verdict").innerHTML = v.text
      ? `<div class="verdict v-${v.level || "thin"}"><span>${LEVEL_ICON[v.level] || "·"}</span><span>${esc(v.text)}</span></div>`
      : "";

    const tr = view.train || {}, te = view.test || {};
    const pct = (x) => (x == null ? "–" : x + "%");
    const num = (x) => (x == null ? "–" : x);
    const rows = [
      { k: "win", l: "Win rate", tr: pct(tr.win_rate), te: pct(te.win_rate), tap: 1 },
      { k: "lose", l: "Loss rate", tr: pct(tr.loss_rate), te: pct(te.loss_rate), tap: 1 },
      { k: null, l: "Profit factor", tr: num(tr.profit_factor), te: num(te.profit_factor) },
      { k: "all", l: "Avg return", tr: pct(tr.avg), te: pct(te.avg), tap: 1 },
      { k: "lose", l: "Worst trade", tr: pct(tr.worst), te: pct(te.worst), tap: 1 },
      { k: "all", l: "Trades", tr: num(tr.trades), te: num(te.trades), tap: 1 },
    ];

    $("bt-rows").innerHTML = rows.map((r, i) => `
      <div class="bt-row">
        <span>${r.l}</span><span class="tr">${r.tr}</span>
        ${r.tap
          ? `<span class="te tap" data-k="${r.k}" data-i="${i}" role="button" tabindex="0">${r.te} ›</span>`
          : `<span class="te">${r.te}</span>`}
      </div>`).join("");

    $("bt-rows").querySelectorAll(".tap").forEach((t) => {
      const go = () => (btOpen === t.dataset.i ? closeTrades() : openTrades(t.dataset.k, t.dataset.i));
      t.onclick = go;
      t.onkeydown = (e) => {
        if (e.key === "Enter" || e.key === " ") { e.preventDefault(); go(); }
      };
    });

    const mdd = view.max_drawdown;
    const card = (l, val) => `<div class="c"><p>${l}</p><p>${val}</p></div>`;
    $("bt-cards").innerHTML =
      card("Max drawdown", mdd == null ? "–" : mdd + "%") +
      card("Avg hold", te.avg_hold == null ? "–" : te.avg_hold + "d") +
      card("Best trade", te.best == null ? "–" : "+" + te.best + "%") +
      card("Median", te.median == null ? "–" : te.median + "%");

    const cfg = (backtest.strategy_config || {})[cur.strategy] || {};
    const p = backtest.params || {};
    $("bt-cfg").textContent =
      Object.entries(cfg).map(([k, x]) => `${k} ${x}`).join(" · ") +
      (Object.keys(p).length
        ? "\n" + Object.entries(p).map(([k, x]) => `${k} ${x}`).join(" · ")
        : "");

    $("bt-updated").textContent = "Last run " + fmtTime(backtest.generated_at);
    const totalTrades = (backtest.strategies || [])
      .reduce((n, s) => n + Number(s.trades_total || 0), 0);
    const scanDate = latest && new Date(latest.scan_date || latest.generated_at);
    const testDate = backtest.date_to && new Date(backtest.date_to);
    const stale = scanDate && testDate &&
      Number.isFinite(scanDate.getTime()) && Number.isFinite(testDate.getTime()) &&
      scanDate - testDate > 7 * 86400000;
    const warning = totalTrades === 0
      ? "This stored backtest contains zero trades and is not a current validation result. A live backtest producer has not been configured."
      : stale
        ? "This backtest is older than the current scan and may be stale."
        : "";
    $("bt-note").textContent = [warning, backtest.note || ""].filter(Boolean).join(" ");
    requestAnimationFrame(drawBtChart);
  }

  function drawBtChart() {
    const cur = btCurrent();
    if (!cur) return;
    const pol = (cur.policies && btPolicy && cur.policies[btPolicy]) ? cur.policies[btPolicy] : cur;
    const eq = pol.equity || {};
    C.split($("bt-chart"), eq.train || [], eq.test || []);
  }

  function openTrades(kind, idx) {
    const cur = btCurrent();
    if (!cur) return;
    // Only test trades are offered — train trades are the in-sample half the
    // strategy was tuned against, so inspecting them tells you nothing useful.
    const pol = (cur.policies && btPolicy && cur.policies[btPolicy]) ? cur.policies[btPolicy] : cur;
    let rows = (pol.trades || []).filter((t) => t.p === "test");
    let title = "All test trades";
    if (kind === "win") { rows = rows.filter((t) => t.r > 0); title = "Winning trades"; }
    if (kind === "lose") {
      rows = rows.filter((t) => t.r <= 0).sort((a, b) => a.r - b.r);
      title = "Losing trades, worst first";
    }

    $("tl-title").textContent = `${title} · ${rows.length}`;
    $("tl-rows").innerHTML = rows.length
      ? rows.map((t) => `
        <div class="tl-row">
          <span class="sym">${esc(t.s)}</span>
          <span class="dt">${t.in.slice(5)} → ${t.out.slice(5)}</span>
          <span class="ret ${t.r > 0 ? "up" : "down"}">${t.r > 0 ? "+" : ""}${t.r}%</span>
        </div>`).join("")
      : `<p class="empty">No trades in this bucket.</p>`;

    const capped = ((pol.test || cur.test) || {}).trades > rows.length && kind === "all";
    $("tl-note").textContent = capped
      ? `Showing ${rows.length} most recent of ${((pol.test || cur.test) || {}).trades}`
      : `${rows.length} trade${rows.length === 1 ? "" : "s"}`;

    const panel = $("bt-panel");
    panel.style.transition = "height .2s ease";
    panel.style.height = $("bt-panel-in").offsetHeight + "px";
    btOpen = idx;
    $("bt-rows").querySelectorAll(".tap").forEach((t) =>
      t.classList.toggle("on", t.dataset.i === idx));
  }

  function closeTrades() {
    const panel = $("bt-panel");
    if (panel) panel.style.height = "0px";
    btOpen = null;
    const r = $("bt-rows");
    if (r) r.querySelectorAll(".tap").forEach((t) => t.classList.remove("on"));
  }
  $("tl-close").onclick = closeTrades;

  // ------------------------------------------------------------------- nav
  function show(v) {
    view = v;
    ["list", "detail", "weekly", "backtest"].forEach((x) => {
      $("view-" + x).hidden = x !== v;
    });
    document.querySelectorAll("nav button").forEach((b) => {
      b.classList.toggle("on", b.dataset.view === v ||
        (v === "detail" && b.dataset.view === "list"));
    });
    if (v !== "detail") {
      $("title").textContent =
        v === "weekly" ? "Weekly review"
        : v === "backtest" ? "Backtest"
        : "BursaMusangKing";
    }
    window.scrollTo(0, 0);
    // Canvases in a hidden section have zero width, so draw after they're shown.
    requestAnimationFrame(redraw);
  }
  document.querySelectorAll("nav button").forEach((b) => {
    b.onclick = () => { current = null; show(b.dataset.view); };
  });

  // ------------------------------------------------------------- run a scan
  $("run").onclick = async () => {
    if (DEMO) {
      banner("Demo mode — set WORKER_URL in config.js to run real scans.", "err");
      setTimeout(() => banner(null), 4000);
      return;
    }
    const btn = $("run");
    btn.disabled = true;
    banner("Queuing scan…");
    const before = latest ? latest.generated_at : null;

    try {
      const headers = { "Content-Type": "application/json" };
      if (CFG.RUN_TOKEN) headers["X-Run-Token"] = CFG.RUN_TOKEN;
      const r = await fetch(API + "/run", { method: "POST", headers, body: "{}" });
      const j = await r.json().catch(() => ({}));
      if (!r.ok || j.ok === false) throw new Error(j.detail || j.error || r.status);
    } catch (e) {
      banner("Couldn't start the scan: " + e.message, "err");
      btn.disabled = false;
      return;
    }

    banner("Scanning the full market — this takes a few minutes. "
         + "Results refresh automatically.");
    pollForNew(before, Date.now() + (CFG.POLL_SECONDS || 420) * 1000);
  };

  function pollForNew(before, deadline) {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const st = await get("/status");
        if (st.latest && st.latest !== before) {
          Object.keys(historyCache).forEach((k) => delete historyCache[k]);
          await loadAll();
          banner("Scan complete — all screeners updated.");
          setTimeout(() => banner(null), 4000);
          $("run").disabled = false;
          return;
        }
      } catch { /* keep polling */ }

      if (Date.now() > deadline) {
        banner("Scan is taking longer than usual. It's still running on "
             + "GitHub — reload in a few minutes.", "err");
        $("run").disabled = false;
        return;
      }
      pollForNew(before, deadline);
    }, 10000);
  }

  // ---------------------------------------------------------------- startup
  if (DEMO) {
    banner("Demo mode — showing sample data. Set WORKER_URL in config.js "
         + "to connect your live scans.");
  }
  loadAll();

  // Register the shell cache, and reload once when a new worker takes over so
  // the installed app picks up deploys instead of serving an old shell forever.
  if ("serviceWorker" in navigator) {
    let reloaded = false;
    navigator.serviceWorker.addEventListener("controllerchange", () => {
      if (reloaded) return;
      reloaded = true;
      location.reload();
    });
    navigator.serviceWorker.register("sw.js")
      .then((reg) => reg.update().catch(() => {}))
      .catch(() => {});
  }
})();
