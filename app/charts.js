/* charts.js — candlestick / EMA / volume rendering on plain canvas.
 *
 * No charting library. The financial plugin we were using was built for
 * Chart.js v3 and silently fails to register on v4, which is what produced
 * "candlestick is not a registered controller" and blank thumbnails. Candles
 * are rectangles and lines, so drawing them directly removes that whole class
 * of breakage and gives exact control over the look.
 */
(() => {
  const UP = "#26a69a";
  const DOWN = "#ef5350";
  const EMA = { e20: "#4a7fc1", e50: "#f0a02c", e200: "#9467bd" };

  function css(name) {
    return getComputedStyle(document.documentElement)
      .getPropertyValue(name).trim();
  }

  /** Size the backing store to the device pixel ratio so lines stay crisp. */
  function prep(canvas, cssH) {
    const dpr = window.devicePixelRatio || 1;
    const w = canvas.clientWidth || canvas.parentElement.clientWidth || 300;
    const h = cssH || canvas.clientHeight || 200;
    canvas.width = Math.round(w * dpr);
    canvas.height = Math.round(h * dpr);
    canvas.style.height = h + "px";
    const ctx = canvas.getContext("2d");
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, w, h);
    return { ctx, w, h };
  }

  const finite = (a) => a.filter((v) => typeof v === "number" && isFinite(v));

  /** Round tick steps (1/2/5 x 10^n) so the axis reads cleanly. */
  function ticks(min, max, target = 5) {
    if (!(isFinite(min) && isFinite(max)) || min === max) return [min];
    const raw = (max - min) / target;
    const mag = Math.pow(10, Math.floor(Math.log10(raw)));
    const norm = raw / mag;
    const step = (norm >= 5 ? 5 : norm >= 2 ? 2 : 1) * mag;
    const out = [];
    for (let v = Math.ceil(min / step) * step; v <= max + 1e-9; v += step) {
      out.push(+v.toFixed(10));
    }
    return out;
  }

  function fmtPrice(v) {
    const a = Math.abs(v);
    return a >= 100 ? v.toFixed(0) : a >= 1 ? v.toFixed(2) : v.toFixed(3);
  }

  function fmtVol(v) {
    if (v >= 1e9) return (v / 1e9).toFixed(1) + "B";
    if (v >= 1e6) return (v / 1e6).toFixed(0) + "M";
    if (v >= 1e3) return (v / 1e3).toFixed(0) + "K";
    return String(Math.round(v));
  }

  function fmtDate(iso) {
    const [, m, d] = iso.split("-");
    const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                    "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    return d + " " + months[+m - 1];
  }

  /** Draw a series that may contain nulls, breaking the line at each gap. */
  function polyline(ctx, arr, xAt, yAt, color, width, dash) {
    if (!arr || !finite(arr).length) return false;
    ctx.save();
    ctx.strokeStyle = color;
    ctx.lineWidth = width;
    ctx.lineJoin = "round";
    if (dash) ctx.setLineDash(dash);
    let drawing = false;
    ctx.beginPath();
    for (let i = 0; i < arr.length; i++) {
      const v = arr[i];
      if (typeof v !== "number" || !isFinite(v)) { drawing = false; continue; }
      const x = xAt(i), y = yAt(v);
      if (!drawing) { ctx.moveTo(x, y); drawing = true; } else { ctx.lineTo(x, y); }
    }
    ctx.stroke();
    ctx.restore();
    return true;
  }

  // ---------------------------------------------------------------- sparkline
  function sparkline(canvas, s) {
    if (!s || !s.c || !s.c.length) return;
    const { ctx, w, h } = prep(canvas);
    const n = s.c.length;
    const lows = finite(s.l), highs = finite(s.h);
    if (!lows.length) return;
    let lo = Math.min(...lows), hi = Math.max(...highs);
    if (hi === lo) { hi += 0.01; lo -= 0.01; }
    const pad = 1;
    const y = (v) => pad + (hi - v) / (hi - lo) * (h - pad * 2);
    const step = w / n;
    const bw = Math.max(1, step * 0.6);

    for (let i = 0; i < n; i++) {
      const o = s.o[i], c = s.c[i], hh = s.h[i], ll = s.l[i];
      if (![o, c, hh, ll].every((v) => typeof v === "number")) continue;
      const cx = step * (i + 0.5);
      const col = c >= o ? UP : DOWN;
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, y(hh));
      ctx.lineTo(cx, y(ll));
      ctx.stroke();
      const top = y(Math.max(o, c));
      const bh = Math.max(1, Math.abs(y(o) - y(c)));
      ctx.fillRect(cx - bw / 2, top, bw, bh);
    }
  }

  // ------------------------------------------------------------ detail chart
  function detail(canvas, s) {
    if (!s || !s.c || !s.c.length) return;

    const H = 350;
    const { ctx, w, h } = prep(canvas, H);
    const grid = css("--line") || "rgba(128,128,128,.2)";
    const label = css("--faint") || "#888";
    const text = css("--muted") || "#777";

    const padL = 44, padR = 8, padT = 10, xAxisH = 18, gap = 12;
    const volH = 68;
    const priceH = h - padT - xAxisH - gap - volH;
    const plotW = w - padL - padR;

    const n = s.c.length;
    const step = plotW / n;
    const bw = Math.max(1.5, step * 0.62);
    const xAt = (i) => padL + step * (i + 0.5);

    // ---- price scale: candles AND every EMA that has data, so no line clips
    let lo = Math.min(...finite(s.l));
    let hi = Math.max(...finite(s.h));
    ["e20", "e50", "e200"].forEach((k) => {
      const f = finite(s[k] || []);
      if (f.length) { lo = Math.min(lo, ...f); hi = Math.max(hi, ...f); }
    });
    if (!isFinite(lo) || !isFinite(hi)) return;
    const span = hi - lo || Math.abs(hi) * 0.02 || 1;
    lo -= span * 0.06;
    hi += span * 0.06;
    const yP = (v) => padT + (hi - v) / (hi - lo) * priceH;

    // ---- price grid + labels
    ctx.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textBaseline = "middle";
    ticks(lo, hi, 5).forEach((t) => {
      const y = yP(t);
      if (y < padT - 1 || y > padT + priceH + 1) return;
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillStyle = label;
      ctx.textAlign = "right";
      ctx.fillText(fmtPrice(t), padL - 5, y);
    });

    // ---- candles
    for (let i = 0; i < n; i++) {
      const o = s.o[i], c = s.c[i], hh = s.h[i], ll = s.l[i];
      if (![o, c, hh, ll].every((v) => typeof v === "number")) continue;
      const cx = xAt(i);
      const col = c >= o ? UP : DOWN;
      ctx.strokeStyle = col;
      ctx.fillStyle = col;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(cx, yP(hh));
      ctx.lineTo(cx, yP(ll));
      ctx.stroke();
      const top = yP(Math.max(o, c));
      const bh = Math.max(1, Math.abs(yP(o) - yP(c)));
      ctx.fillRect(cx - bw / 2, top, bw, bh);
    }

    // ---- EMA overlays
    const drawn = [];
    [["e20", "EMA 20"], ["e50", "EMA 50"], ["e200", "EMA 200"]].forEach(([k, name]) => {
      if (polyline(ctx, s[k], xAt, yP, EMA[k], 1.6)) drawn.push([EMA[k], name]);
    });

    // ---- legend, only for lines that actually rendered
    if (drawn.length) {
      ctx.textAlign = "left";
      ctx.textBaseline = "middle";
      let ly = padT + 8;
      drawn.forEach(([col, name]) => {
        ctx.strokeStyle = col;
        ctx.lineWidth = 2;
        ctx.beginPath();
        ctx.moveTo(padL + 4, ly);
        ctx.lineTo(padL + 18, ly);
        ctx.stroke();
        ctx.fillStyle = text;
        ctx.fillText(name, padL + 22, ly);
        ly += 13;
      });
    }

    // ---- volume pane
    const vTop = padT + priceH + gap;
    const vols = finite(s.v || []);
    if (vols.length) {
      const vMax = Math.max(...vols, ...finite(s.vavg || [])) || 1;
      const yV = (v) => vTop + volH - (v / vMax) * volH;

      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, vTop + volH);
      ctx.lineTo(w - padR, vTop + volH);
      ctx.stroke();

      ctx.fillStyle = label;
      ctx.textAlign = "right";
      ctx.fillText(fmtVol(vMax), padL - 5, vTop + 5);

      for (let i = 0; i < n; i++) {
        const v = s.v[i];
        if (typeof v !== "number" || !isFinite(v)) continue;
        const o = s.o[i], c = s.c[i];
        ctx.fillStyle = (c >= o ? UP : DOWN) + "b0";
        const y = yV(v);
        ctx.fillRect(xAt(i) - bw / 2, y, bw, vTop + volH - y);
      }
      polyline(ctx, s.vavg, xAt, yV, text, 1.2, [4, 3]);
    }

    // ---- date axis, ~4 evenly spaced labels
    if (s.t && s.t.length === n) {
      ctx.fillStyle = label;
      ctx.textBaseline = "top";
      const every = Math.max(1, Math.floor(n / 4));
      for (let i = 0; i < n; i += every) {
        const x = xAt(i);
        ctx.textAlign = i === 0 ? "left" : "center";
        if (x > w - padR - 24) continue;
        ctx.fillText(fmtDate(s.t[i]), x, vTop + volH + 5);
      }
    }
  }

  // ------------------------------------------------------------- equity line
  function line(canvas, points) {
    if (!points || points.length < 2) return;
    const { ctx, w, h } = prep(canvas);
    const grid = css("--line"), label = css("--faint"), accent = css("--accent");
    const padL = 40, padR = 8, padT = 8, padB = 18;
    const vals = points.map((p) => p.value);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi === lo) { hi += 1; lo -= 1; }
    const sp = hi - lo;
    lo -= sp * 0.1; hi += sp * 0.1;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const xAt = (i) => padL + (points.length === 1 ? plotW / 2
      : (i / (points.length - 1)) * plotW);
    const yAt = (v) => padT + (hi - v) / (hi - lo) * plotH;

    ctx.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textBaseline = "middle";
    ticks(lo, hi, 4).forEach((t) => {
      const y = yAt(t);
      if (y < padT - 1 || y > padT + plotH + 1) return;
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillStyle = label;
      ctx.textAlign = "right";
      ctx.fillText(t.toFixed(0), padL - 5, y);
    });

    polyline(ctx, vals, xAt, yAt, accent, 2);

    ctx.fillStyle = label;
    ctx.textBaseline = "top";
    const every = Math.max(1, Math.floor(points.length / 4));
    for (let i = 0; i < points.length; i += every) {
      const x = xAt(i);
      if (x > w - padR - 20) continue;
      ctx.textAlign = i === 0 ? "left" : "center";
      ctx.fillText(fmtDate(points[i].date), x, padT + plotH + 5);
    }
  }

  // ------------------------------------------------- split train/test line
  /** Two segments on one shared scale: train dashed grey, test solid accent.
   *  Same scale matters — drawing them independently would hide the fact that
   *  the test half is flatter, which is the whole point of looking. */
  function split(canvas, train, test) {
    const pts = [...(train || []), ...(test || [])];
    if (pts.length < 2) { prep(canvas); return; }
    const { ctx, w, h } = prep(canvas);
    const grid = css("--line"), label = css("--faint"), accent = css("--accent");
    const padL = 40, padR = 8, padT = 8, padB = 18;
    const vals = pts.map((p) => p.v);
    let lo = Math.min(...vals), hi = Math.max(...vals);
    if (hi === lo) { hi += 1; lo -= 1; }
    const sp = hi - lo;
    lo -= sp * 0.08; hi += sp * 0.08;
    const plotW = w - padL - padR, plotH = h - padT - padB;
    const xAt = (i) => padL + (i / (pts.length - 1)) * plotW;
    const yAt = (v) => padT + (hi - v) / (hi - lo) * plotH;

    ctx.font = "10px -apple-system, BlinkMacSystemFont, Segoe UI, sans-serif";
    ctx.textBaseline = "middle";
    ticks(lo, hi, 4).forEach((t) => {
      const y = yAt(t);
      if (y < padT - 1 || y > padT + plotH + 1) return;
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(padL, y);
      ctx.lineTo(w - padR, y);
      ctx.stroke();
      ctx.fillStyle = label;
      ctx.textAlign = "right";
      ctx.fillText(t.toFixed(0), padL - 5, y);
    });

    const nTrain = (train || []).length;
    if (nTrain > 1) {
      polyline(ctx, vals.map((v, i) => (i < nTrain ? v : null)),
        xAt, yAt, label, 2, [5, 3]);
    }
    if ((test || []).length) {
      // start one point early so the two segments visibly join
      polyline(ctx, vals.map((v, i) => (i >= nTrain - 1 ? v : null)),
        xAt, yAt, accent, 2);
      const x = xAt(Math.max(0, nTrain - 1));
      ctx.save();
      ctx.strokeStyle = grid;
      ctx.lineWidth = 1;
      ctx.setLineDash([2, 3]);
      ctx.beginPath();
      ctx.moveTo(x, padT);
      ctx.lineTo(x, padT + plotH);
      ctx.stroke();
      ctx.restore();
    }

    ctx.fillStyle = label;
    ctx.textBaseline = "top";
    [0, pts.length - 1].forEach((i, k) => {
      ctx.textAlign = k === 0 ? "left" : "right";
      ctx.fillText(fmtDate(pts[i].d), xAt(i), padT + plotH + 5);
    });
  }

  window.BMKChart = { sparkline, detail, line, split };
})();
