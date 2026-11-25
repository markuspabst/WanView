"use strict";
// WanView dashboard. Router values are untrusted: always inserted as text, never as HTML.

const STATE_INTERVAL_MS = 5000;   // also keeps live LAN traffic running on the server
const HISTORY_INTERVAL_MS = 30000;
const BITS_PER_MEGABIT = 1e6;
const WHOLE_MEGABIT_THRESHOLD = 10;

let current = null;
let historyRange = "day";
const TABS = ["overview", "dsl", "mobile", "speedtest", "settings"];
const HISTORY_TABS = ["dsl", "mobile", "speedtest"];
// Old bookmarks (#history) lead to the DSL tab.
const TAB_ALIASES = { history: "dsl" };
let activeTab = "overview";

// Formatting

function megabits(bps) {
  if (bps === null || bps === undefined) return "–";
  const value = bps / BITS_PER_MEGABIT;
  return value.toFixed(value < WHOLE_MEGABIT_THRESHOLD ? 1 : 0);
}
const rate = (bps) => `${megabits(bps)} Mbit/s`;
const pair = (down, up, unit = "") => `${down} / ${up}${unit}`;
const decibel = (value, unit) => (value === null || value === undefined ? "–" : `${Math.round(value * 10) / 10} ${unit}`);
const plain = (value) => (value === null || value === undefined || value === "" ? "–" : String(value));
const clock = (iso) => (iso ? new Date(iso).toLocaleTimeString() : "–");

// DOM helpers

function element(tag, attributes = {}, text = null) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  if (text !== null) node.textContent = text;
  return node;
}

function indicator(rating) {
  const cell = element("dd", { class: "indicator" });
  if (!rating) return cell;
  // Poor gets a different shape, not only a different colour.
  const mark = rating === "poor" ? element("span", { class: "triangle", title: rating }, "▲")
                                 : element("span", { class: `dot ${rating}`, title: rating });
  cell.append(mark);
  return cell;
}

function rows(cardId, entries, emptyText = null) {
  const card = document.getElementById(cardId);
  const list = card.querySelector("dl");
  list.replaceChildren();
  for (const [title, value, rating, help] of entries) {
    // The value's tooltip repeats it in full, in case the row had to shorten it.
    list.append(element("dt", help ? { title: help } : {}, title),
                element("dd", { title: help ? `${value}\n${help}` : value }, value), indicator(rating));
  }
  if (entries.length === 0 && emptyText) list.append(element("dt", { class: "muted" }, emptyText));
}

function show(id, visible) {
  document.getElementById(id).hidden = !visible;
}

// Cards

function renderConnection(state) {
  const s = state.status;
  if (!s) return rows("card-connection", [], state.router.model ? "Waiting for status…" : "Identifying router…");
  const entries = [
    ["Router", plain(state.router.model)],
    ["Firmware", plain(s.firmware)],
    ["Internet", plain(s.online), s.onlineRating],
  ];
  if (s.onlineSince) entries.push(["Online since", s.onlineSince, null, "Router local time"]);
  rows("card-connection", entries);
}

function renderDSL(state) {
  const s = state.status, d = state.dsl;
  if (!s) return rows("card-dsl", []);
  const entries = [
    ["Link", plain(s.dslLink), s.dslLinkRating],
    ...(d && d.mode ? [["Mode", d.mode, null, d.state ? `Line state: ${d.state}` : null]] : []),
    ["Sync ↓ / ↑", pair(megabits(s.syncDown), rate(s.syncUp)), s.syncRating,
     s.syncNormalDown ? `Normal (median of 7 days): ${pair(megabits(s.syncNormalDown), rate(s.syncNormalUp))}`
                      : "Rated once about an hour of status polls is logged."],
  ];
  if (d) {
    const perHour = (v) => (v === null ? "–" : `${Math.round(v)}/h`);
    entries.push(
      ["Attainable ↓ / ↑", pair(megabits(d.attainableDown), rate(d.attainableUp))],
      ["SNR margin ↓ / ↑", pair(decibel(d.snrDown, "dB"), decibel(d.snrUp, "dB")), d.snrRating],
      ["Attenuation ↓ / ↑", pair(decibel(d.attenuationDown, "dB"), decibel(d.attenuationUp, "dB"))],
      ["CRC errors ↓ / ↑", pair(plain(d.crcDown), plain(d.crcUp)), d.crcRating,
       d.crcPerHourDown === null && d.crcPerHourUp === null ? "Totals since the last resync; rated from the second poll on."
         : `Totals since the last resync. Increase: ${perHour(d.crcPerHourDown)} ↓, ${perHour(d.crcPerHourUp)} ↑.`],
      ["FEC errors ↓ / ↑", pair(plain(d.fecDown), plain(d.fecUp)), null,
       d.fecPerHourDown === null && d.fecPerHourUp === null ? "Corrected errors since the last resync; normal in operation."
         : `Corrected errors since the last resync (normal in operation). Increase: ${perHour(d.fecPerHourDown)} ↓, ${perHour(d.fecPerHourUp)} ↑.`],
    );
  }
  // With a 5G receiver the internet rate is the DSL overflow limit, shown under bonding.
  if (!state.hasReceiver) entries.push(["Internet ↓ / ↑", pair(megabits(s.internetDown), rate(s.internetUp))]);
  rows("card-dsl", entries);
}

function renderBonding(state) {
  show("card-bonding", state.hasReceiver);
  const b = state.bonding, t = state.traffic;
  if (!b) return;
  const entries = [];
  if (b.dslLegUp !== null) {
    entries.push(["DSL / 5G legs", pair(b.dslLegUp ? "up" : "down", b.mobileLegUp ? "up" : "down"), b.legsRating]);
  }
  entries.push(["DSL limit ↓ / ↑", pair(megabits(b.limitDown), rate(b.limitUp)), null, "Traffic above these rates overflows from DSL to 5G."]);
  if (t && t.mobileDown !== undefined) {
    entries.push(["5G now ↓ / ↑", pair(megabits(t.mobileDown), rate(t.mobileUp)), null, "Live: measured on LAN1 (5G receiver) every 5 s."],
                 ["DSL now ≈", pair(megabits(t.dslEstimateDown), rate(t.dslEstimateUp)), null, "Live estimate: wired client traffic minus 5G; Wi-Fi is not counted."]);
  } else if (b.overflowInterval) {
    entries.push(["5G average ↓ / ↑", pair(megabits(b.overflowDown), rate(b.overflowUp)), null,
                  `Average 5G traffic over the last ${Math.round(b.overflowInterval / 60)} min.`]);
  } else if (state.router.hasPassword) {
    entries.push(["5G overflow ↓ / ↑", "measuring…"]);
  }
  rows("card-bonding", entries);
}

function renderReceiver(state) {
  show("card-receiver", state.hasReceiver);
  const s = state.status, r = state.receiver;
  if (!s || !state.hasReceiver) return;
  const title = (tech, band) => [tech, band].filter(Boolean).join(" ");
  const help = (cell) => ["RSRP / RSRQ", cell.pci && `cell ID (PCI) ${cell.pci}`].filter(Boolean).join(" · ");
  const entries = r
    ? [["Receiver", plain(r.state), r.stateRating],
       [title("5G", r.nr.band), pair(decibel(r.nr.rsrp, "dBm"), decibel(r.nr.rsrq, "dB")), r.nr.rating, help(r.nr)],
       [title("LTE", r.lte.band), pair(decibel(r.lte.rsrp, "dBm"), decibel(r.lte.rsrq, "dB")), r.lte.rating, help(r.lte)]]
    : [[title("5G", s.nrBand), decibel(s.nrSignal, "dBm"), s.signalRating, "RSRP"],
       [title("LTE", s.lteBand), decibel(s.lteSignal, "dBm"), null, "RSRP"]];
  rows("card-receiver", entries);
}

function renderSpeedtest(state) {
  const t = state.speedtest;
  const button = document.getElementById("run-speedtest");
  button.disabled = t.running || state.paused || Boolean(state.routerBackTime);
  button.textContent = t.running ? "Running…" : "Run";
  const entries = [];
  if (t.running) entries.push(["Status", "measuring, about 20 s"]);
  if (t.error) entries.push(["Error", t.error, "poor"]);
  const last = t.last;
  if (last) {
    entries.push(["Down / up", pair(megabits(last.down), rate(last.up))]);
    if (last.mobileDown !== null || last.mobileUp !== null) {
      entries.push(["DSL / 5G ↓", pair(megabits(last.dslDown), rate(last.mobileDown)), null,
                    "5G share from the receiver port during the test; other traffic in that time counts too."],
                   ["DSL / 5G ↑", pair(megabits(last.dslUp), rate(last.mobileUp))]);
    }
    const links = {
      wired: ["wired", "excellent", "The host's network interface is wired: the result measures the line."],
      wifi: ["Wi-Fi: limits the result", "fair", "The host's network interface is Wi-Fi: the result is limited by Wi-Fi, not the line."],
      "router-wifi": ["router Wi-Fi: limits the result", "fair", "The test traffic did not pass a router LAN port: the host is on the router's Wi-Fi, so the result is limited by Wi-Fi."],
      "lan-port": ["LAN port (cable or mesh)", null, "The test traffic entered through a router LAN port: by cable, or by Wi-Fi through a mesh access point on that port. The host's own interface could not be checked."],
    };
    const [linkText, linkRating, linkHelp] = links[last.hostLink]
      || ["unknown", null, "Needs Linux with the host's network visible (Docker: network_mode: host) or the router login."];
    entries.push(["Connection", linkText, linkRating, linkHelp]);
    entries.push(["Latency", `${Math.round(last.latencyMs)} ms`],
                 ["Measured", new Date(last.time).toLocaleString([], { dateStyle: "short", timeStyle: "short" })]);
  }
  rows("card-speedtest", entries, "No test yet.");
}

function renderTraffic(state) {
  show("card-traffic", state.router.hasPassword && !state.paused);
  const t = state.traffic;
  if (!t) return rows("card-traffic", [], "Measuring…");
  if (t.error) return rows("card-traffic", [["Error", t.error, "poor"]]);
  const active = t.ports.filter((p) => (p.down || 0) + (p.up || 0) > 0);
  const total = (key) => t.ports.reduce((sum, p) => sum + (p[key] || 0), 0);
  rows("card-traffic", [
    ...active.map((p) => [`LAN${p.number} ↓ / ↑`, pair(megabits(p.down), rate(p.up))]),
    ["Wired total ↓ / ↑", pair(megabits(total("down")), rate(total("up")))],
  ]);
}

// Header, notices, setup, settings

// Filled bars per rating, as in Linkbar's menu bar.
const SIGNAL_BARS = { poor: 1, fair: 2, good: 3, excellent: 4 };
const BAR_COUNT = 4;

function signalBars(rating, tooltip) {
  const svg = svgElement("svg", { viewBox: "0 0 20 14", class: "bars", role: "img", "aria-label": tooltip });
  const title = svgElement("title", {});
  title.textContent = tooltip;
  svg.append(title);
  const filled = SIGNAL_BARS[rating] || 0;
  for (let i = 0; i < BAR_COUNT; i++) {
    const height = 4 + i * 3;
    svg.append(svgElement("rect", { x: i * 5, y: 14 - height, width: 3.5, height, rx: 1,
                                    class: i < filled ? "bar-on" : "bar-off" }));
  }
  return svg;
}

function renderHeader(state) {
  const s = state.status;
  const summary = document.getElementById("summary");
  let text = "";
  if (state.paused) text = "Paused";
  else if (state.routerBackTime) text = "Router restarting";
  else if (state.router.ignored) text = "Ignored router (not monitored)";
  else if (s) text = `↓${megabits(s.syncDown)} ↑${megabits(s.syncUp)}`;
  summary.replaceChildren(text);
  // Sync below the line's normal rate is flagged next to the rates.
  if (s && !state.paused && !state.routerBackTime && s.syncRating && s.syncRating !== "excellent") {
    const mark = indicator(s.syncRating).firstChild;
    mark.setAttribute("title", `Sync below normal (${pair(megabits(s.syncNormalDown), rate(s.syncNormalUp))})`);
    summary.append(" ", mark);
    text += " (below normal)";
  }

  // 5G if measured, else LTE, labelled "4G" as on phones; nothing without a receiver.
  const signal = s && state.hasReceiver && !state.paused && !state.routerBackTime
    ? (s.nrSignal !== null ? ["5G", s.nrSignal] : s.lteSignal !== null ? ["4G", s.lteSignal] : null)
    : null;
  if (signal && s.signalRating) {
    summary.append(` · ${signal[0]} `, signalBars(s.signalRating, `${signal[0]} ${Math.round(signal[1])} dBm (${s.signalRating})`));
    text += ` · ${signal[0]} ${SIGNAL_BARS[s.signalRating]}/${BAR_COUNT}`;
  }
  document.title = text ? `WanView · ${text}` : "WanView";

  const pause = document.getElementById("pause");
  pause.textContent = state.paused ? "Resume" : "Pause";
  pause.disabled = Boolean(state.routerBackTime);
  document.getElementById("router-link").href = state.router.url;
  document.getElementById("restart-router").disabled = !state.router.canRestart;
}

function renderNotices(state) {
  const notices = document.getElementById("notices");
  notices.replaceChildren();
  const add = (text, cls = "muted") => notices.append(element("p", { class: cls }, text));
  if (state.paused) add("Paused: nothing is polled and the router login is free for its web interface.");
  if (state.routerBackTime) add(`Router restarting. Monitoring resumes at ${clock(state.routerBackTime)}.`);
  if (state.router.model && state.router.supported === false) {
    add(`Unsupported router: ${state.router.model}. Supported: Speedport Smart 4.`, "error");
  }
  for (const error of state.errors) add(error, "error");

  show("access-setup", state.dashboardAccess === "unset");
  show("setup", state.router.new);
  document.getElementById("setup-name").textContent = `${state.router.model} (S/N ${state.router.serial})`;
  if (state.router.ignored) {
    const line = element("p", {}, `Ignored router: ${state.router.model} (S/N ${state.router.serial}). `);
    const button = element("button", { type: "button" }, "Set up");
    button.addEventListener("click", () => act(`/api/routers/${encodeURIComponent(state.router.serial)}`, "PUT", { choice: "setUp" }));
    line.append(button);
    notices.append(line);
  }

  const updated = [state.statusTime && `Status ${clock(state.statusTime)}`,
                   state.detailsTime && `line details ${clock(state.detailsTime)}`].filter(Boolean);
  document.getElementById("updated").textContent = updated.length ? `Updated: ${updated.join(", ")}` : "";
}

function renderSettings(state) {
  const editing = (id) => document.activeElement === document.getElementById(id);
  if (!editing("router-url")) document.getElementById("router-url").value = state.router.url;
  if (!editing("status-interval")) document.getElementById("status-interval").value = state.settings.statusInterval;
  if (!editing("details-interval")) document.getElementById("details-interval").value = state.settings.detailsInterval;
  if (!editing("speedtest-interval")) document.getElementById("speedtest-interval").value = state.settings.speedtestInterval;

  document.getElementById("access-status").textContent =
    state.dashboardAccess === "password" ? "Login required." : "Open: no login.";
  document.getElementById("access-remove").disabled = state.dashboardAccess !== "password";

  const router = state.router;
  document.getElementById("password-router").textContent = router.serial
    ? `${router.model}, S/N ${router.serial}: ${router.hasPassword ? "password stored" : router.new ? "new, saving a password sets it up" : "no password"}`
    : "Router not identified yet.";
  document.getElementById("password").disabled = !router.serial;
  document.getElementById("password-remove").disabled = !router.hasPassword;

  for (const choice of ["setUp", "ignored"]) {
    const list = document.getElementById(`routers-${choice}`);
    list.replaceChildren();
    const routers = state.routers.filter((r) => r.choice === choice);
    if (routers.length === 0) list.append(element("li", { class: "muted" }, "None."));
    for (const r of routers) {
      const item = element("li");
      item.append(element("span", {}, `${r.name}${r.connected ? " (connected)" : ""} · S/N ${r.serial} · added ${r.added.slice(0, 10)}`));
      const path = `/api/routers/${encodeURIComponent(r.serial)}`;
      const toggle = element("button", { type: "button" }, choice === "setUp" ? "Ignore" : "Set up");
      toggle.addEventListener("click", () => act(path, "PUT", { choice: choice === "setUp" ? "ignored" : "setUp" }));
      const remove = element("button", { type: "button", class: "danger" }, "Remove");
      remove.addEventListener("click", () => {
        if (confirm(`Remove ${r.name} (S/N ${r.serial}) and its password?`)) act(path, "DELETE");
      });
      item.append(toggle, remove);
      list.append(item);
    }
  }
}

// Dashboard access

// Sets the dashboard password from a password and repeat field; null keeps the dashboard open.
async function setDashboardAccess(passwordId, repeatId) {
  let password = null;
  if (passwordId) {
    const field = document.getElementById(passwordId);
    const repeat = document.getElementById(repeatId);
    if (field.value !== repeat.value) {
      alert("The passwords differ.");
      return;
    }
    password = field.value;
    field.value = "";
    repeat.value = "";
  }
  const response = await fetch("/api/dashboard-access", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ password }),
  });
  if (!response.ok) {
    alert(`Request failed (${response.status}).`);
    return;
  }
  // The browser asks for the new password on reload.
  if (password !== null) location.reload();
  else await refreshState();
}

// API

async function act(path, method = "POST", body = undefined) {
  const response = await fetch(path, {
    method,
    headers: body ? { "Content-Type": "application/json" } : {},
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}));
    alert(typeof detail.detail === "string" ? detail.detail : `Request failed (${response.status}).`);
  }
  await refreshState();
}

async function refreshState() {
  try {
    const response = await fetch("/api/state");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    current = await response.json();
  } catch (error) {
    document.getElementById("notices").replaceChildren(element("p", { class: "error" }, `WanView not reachable: ${error.message}`));
    return;
  }
  renderHeader(current);
  renderNotices(current);
  renderTabs(current);
  renderConnection(current);
  renderDSL(current);
  renderBonding(current);
  renderReceiver(current);
  renderTraffic(current);
  renderSpeedtest(current);
  renderSettings(current);
}

function wireActions() {
  document.getElementById("pause").addEventListener("click", () => act(current && current.paused ? "/api/resume" : "/api/pause"));
  document.getElementById("router-link").addEventListener("click", () => {
    // The router allows one login; the browser needs it.
    if (current && !current.paused) act("/api/pause");
  });
  document.getElementById("run-speedtest").addEventListener("click", () => act("/api/speedtest"));
  document.getElementById("restart-router").addEventListener("click", () => {
    if (confirm("Restart the router? Internet, Wi-Fi and telephone are interrupted for about 3 minutes. Settings are kept.")) {
      act("/api/restart-router");
    }
  });
  document.getElementById("setup-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const password = document.getElementById("setup-password").value;
    document.getElementById("setup-password").value = "";
    if (password) act("/api/password", "PUT", { password });
    else act(`/api/routers/${encodeURIComponent(current.router.serial)}`, "PUT", { choice: "setUp" });
  });
  document.getElementById("setup-ignore").addEventListener("click", () =>
    act(`/api/routers/${encodeURIComponent(current.router.serial)}`, "PUT", { choice: "ignored" }));
  document.getElementById("access-setup-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setDashboardAccess("access-setup-password", "access-setup-repeat");
  });
  document.getElementById("access-setup-open").addEventListener("click", () => setDashboardAccess(null));
  document.getElementById("access-form").addEventListener("submit", (event) => {
    event.preventDefault();
    setDashboardAccess("access-password", "access-repeat");
  });
  document.getElementById("access-remove").addEventListener("click", () => {
    if (confirm("Remove the dashboard password? Anyone on the network can then open the dashboard.")) setDashboardAccess(null);
  });
  document.getElementById("settings-form").addEventListener("submit", (event) => {
    event.preventDefault();
    act("/api/settings", "PUT", {
      routerUrl: document.getElementById("router-url").value,
      statusInterval: Number(document.getElementById("status-interval").value),
      detailsInterval: Number(document.getElementById("details-interval").value),
      speedtestInterval: Number(document.getElementById("speedtest-interval").value),
    });
  });
  document.getElementById("password-form").addEventListener("submit", (event) => {
    event.preventDefault();
    const field = document.getElementById("password");
    if (field.value) act("/api/password", "PUT", { password: field.value });
    field.value = "";
  });
  document.getElementById("password-remove").addEventListener("click", () => {
    if (confirm("Remove the stored device password for this router?")) act("/api/password", "DELETE");
  });
  for (const button of document.querySelectorAll(".range button")) {
    button.addEventListener("click", () => {
      historyRange = button.dataset.range;
      // Both history tabs have a switch; keep them in step.
      document.querySelectorAll(".range button").forEach((b) => b.classList.toggle("selected", b.dataset.range === historyRange));
      refreshHistory();
    });
  }
}

// History charts (SVG, one measure per chart, shared time axis)

const SVG = "http://www.w3.org/2000/svg";
// Drawing units: width is stretched to the card, height matches the plot's CSS height in px.
const CHART = { width: 1000, height: 170, top: 6, bottom: 6 };
const TIME_LABEL_FRACTIONS = [0, 0.25, 0.5, 0.75, 1];
const TIME_LABEL_DAY_LIMIT_MS = 36 * 3600 * 1000;   // up to this range: times of day only
const TIME_LABEL_WEEK_LIMIT_MS = 8 * 24 * 3600 * 1000;   // above: dates only
const TICK_TARGET = 4;
const GAP_FACTOR = 3;
// Titles of collapsed charts, kept across reloads and history refreshes.
const COLLAPSED_KEY = "wanview.collapsedCharts";

function collapsedCharts() {
  try {
    return new Set(JSON.parse(localStorage.getItem(COLLAPSED_KEY)) || []);
  } catch {
    return new Set();
  }
}

function rememberCollapsed(title, collapsed) {
  const titles = collapsedCharts();
  if (collapsed) titles.add(title); else titles.delete(title);
  localStorage.setItem(COLLAPSED_KEY, JSON.stringify([...titles]));
}

function svgElement(tag, attributes) {
  const node = document.createElementNS(SVG, tag);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, value);
  return node;
}

// Round axis values: steps of 1, 2 or 5 × 10^n, about TICK_TARGET of them inside [low, high].
function niceTicks(low, high) {
  const rough = (high - low) / TICK_TARGET;
  const magnitude = 10 ** Math.floor(Math.log10(rough));
  const step = [1, 2, 5, 10].map((m) => m * magnitude).find((candidate) => candidate >= rough);
  const values = [];
  for (let v = Math.ceil(low / step) * step; v <= high; v += step) values.push(Math.round(v / step) * step);
  return { values, decimals: Math.max(0, -Math.floor(Math.log10(step))) };
}

const SERIES_COLORS = ["var(--series1)", "var(--series2)"];
const WIFI_COLORS = ["var(--wifi1)", "var(--wifi2)"];

// Options: `dots` marks every point (sparse series such as speed tests; points with
// `wifi: true` are marked in the Wi-Fi colour); `bands` shades [startMs, endMs] ranges
// red (problems); `note` is extra header text; `wifiNote` adds a legend entry in the
// Wi-Fi colour; `palette` replaces the series colours; `references` draws a dashed
// line per series at that value (null: none).
function chart(title, unit, names, points, start, end, maxGapMs,
               { dots = false, bands = [], note = null, wifiNote = null, palette = SERIES_COLORS, references = [] } = {}) {
  const box = element("details", { class: "chart" });
  box.open = !collapsedCharts().has(title);
  box.addEventListener("toggle", () => rememberCollapsed(title, !box.open));
  const heading = element("summary", {});
  heading.append(element("span", {}, title), element("span", { class: "muted" }, unit));
  if (note) heading.append(element("span", { class: "muted note" }, note));
  const legend = element("span", { class: "legend" });
  names.forEach((name, i) => legend.append(element("span", { style: `color: ${palette[i]}` }, `■ ${name}  `)));
  if (wifiNote) legend.append(element("span", { style: `color: ${WIFI_COLORS[0]}` }, `● ${wifiNote}`));
  heading.append(legend);
  box.append(heading);

  const values = points.flatMap((p) => p.values).concat(references).filter((v) => v !== null && v !== undefined);
  if (values.length === 0) {
    box.append(element("p", { class: "muted" }, "No values in this range."));
    return box;
  }
  let low = Math.min(...values), high = Math.max(...values);
  // Counts and rates are never negative: their axis starts at 0.
  const nonNegative = low >= 0;
  if (low === high) { low -= 1; high += 1; }
  const pad = (high - low) * 0.08;
  low -= pad; high += pad;
  if (nonNegative) low = Math.max(low, 0);
  const ticks = niceTicks(low, high);

  // The lines are drawn in a viewBox stretched to the card width (vertical scale 1:1);
  // labels are HTML on top of it, so text is never distorted by the stretching.
  const plot = element("div", { class: "plot" });
  const svg = svgElement("svg", { viewBox: `0 0 ${CHART.width} ${CHART.height}`, preserveAspectRatio: "none" });
  const plotHeight = CHART.height - CHART.top - CHART.bottom;
  const x = (t) => ((t - start) / (end - start)) * CHART.width;
  const y = (v) => CHART.top + (1 - (v - low) / (high - low)) * plotHeight;

  for (const [from, to] of bands) {
    const left = Math.max(x(from), 0), right = Math.min(x(to), CHART.width);
    if (right > left) {
      svg.append(svgElement("rect", { x: left, y: CHART.top, width: right - left, height: plotHeight, class: "band" }));
    }
  }
  for (const tick of ticks.values) {
    svg.append(svgElement("line", { x1: 0, x2: CHART.width, y1: y(tick), y2: y(tick), class: "axis",
                                    "vector-effect": "non-scaling-stroke" }));
    plot.append(element("span", { class: "y-label", style: `top: ${y(tick)}px` }, tick.toFixed(ticks.decimals)));
  }
  references.forEach((value, series) => {
    if (value === null || value === undefined) return;
    svg.append(svgElement("line", { x1: 0, x2: CHART.width, y1: y(value), y2: y(value), stroke: palette[series],
                                    "stroke-dasharray": "6 4", "stroke-width": 1, "vector-effect": "non-scaling-stroke" }));
  });
  const shortRange = end - start <= TIME_LABEL_DAY_LIMIT_MS;
  const longRange = end - start > TIME_LABEL_WEEK_LIMIT_MS;
  for (const fraction of TIME_LABEL_FRACTIONS) {
    const t = start + (end - start) * fraction;
    const text = new Date(t).toLocaleString([], shortRange ? { hour: "2-digit", minute: "2-digit" }
      : longRange ? { day: "numeric", month: "short" } : { weekday: "short", hour: "2-digit", minute: "2-digit" });
    const align = fraction === 0 ? "start" : fraction === 1 ? "end" : "middle";
    plot.append(element("span", { class: `x-label ${align}`, style: `left: ${fraction * 100}%` }, text));
  }
  names.forEach((_, series) => {
    let path = "", previous = null;
    for (const point of points) {
      const value = point.values[series];
      if (value === null || value === undefined) { previous = null; continue; }
      const breaks = previous === null || point.time - previous > maxGapMs;
      path += `${breaks ? "M" : "L"}${x(point.time).toFixed(1)},${y(value).toFixed(1)}`;
      previous = point.time;
    }
    svg.append(svgElement("path", { d: path, fill: "none", stroke: palette[series], "stroke-width": 1.5,
                                    "vector-effect": "non-scaling-stroke" }));
    if (!dots) return;
    // Zero-length segments with round caps and a non-scaling stroke stay round although
    // the chart is stretched to the card width.
    const marks = (wifi) => points
      .filter((p) => Boolean(p.wifi) === wifi && p.values[series] !== null && p.values[series] !== undefined)
      .map((p) => `M${x(p.time).toFixed(1)},${y(p.values[series]).toFixed(1)}l0,0`).join("");
    for (const [wifi, color] of [[false, palette[series]], [true, WIFI_COLORS[series] || WIFI_COLORS[0]]]) {
      const d = marks(wifi);
      if (d) {
        svg.append(svgElement("path", { d, stroke: color, "stroke-width": 7, "stroke-linecap": "round",
                                        "vector-effect": "non-scaling-stroke" }));
      }
    }
  });
  plot.prepend(svg);
  box.append(plot);
  return box;
}

// 5G overflow (average per details poll); red: a bonding leg was down. Each overflow row:
// [time, 5G down, 5G up, DSL leg up, 5G leg up, DSL limit down, DSL limit up].
function bondingChart(rows, start, end, maxGapMs) {
  const mbit = (v) => (v === null ? null : v / BITS_PER_MEGABIT);
  const points = rows.map((r) => ({ time: Date.parse(r[0]), values: [mbit(r[1]), mbit(r[2])] }));
  const bands = [];
  rows.forEach((r, i) => {
    if (r[3] && r[4]) return;
    const time = Date.parse(r[0]);
    // A down leg is known from the previous poll on (or one interval back for the first).
    const from = i > 0 ? Date.parse(rows[i - 1][0]) : time - maxGapMs / GAP_FACTOR;
    bands.push([from, time]);
  });
  const last = rows.length ? rows[rows.length - 1] : null;
  const note = last && last[5] ? `DSL limit ${megabits(last[5])} / ${megabits(last[6])} Mbit/s` : null;
  return chart("Hybrid bonding: 5G overflow", "Mbit/s", ["↓ down", "↑ up"], points, start, end, maxGapMs,
               { bands, note: bands.length ? `${note || ""}${note ? " · " : ""}red: bonding leg down` : note });
}

async function refreshHistory() {
  const response = await fetch(`/api/history?range=${historyRange}`);
  if (!response.ok) return;
  const h = await response.json();
  const start = Date.parse(h.since), end = Date.parse(h.now);
  const statusGap = (current ? current.settings.statusInterval : 30) * 1000 * GAP_FACTOR;
  const detailsGap = (current ? current.settings.detailsInterval : 300) * 1000 * GAP_FACTOR;
  const mbit = (v) => (v === null ? null : v / BITS_PER_MEGABIT);
  const series = (rows, map) => rows.map((row) => ({ time: Date.parse(row[0]), values: map(row) }));
  // Outages: from a poll that saw the internet offline to the next poll.
  const outages = [];
  h.status.forEach((r, i) => {
    if (r[5]) return;
    const from = Date.parse(r[0]);
    outages.push([from, i + 1 < h.status.length ? Date.parse(h.status[i + 1][0]) : from + statusGap / GAP_FACTOR]);
  });
  const normal = h.normalSync.map(mbit);
  const syncNote = [normal[0] !== null ? "dashed: normal (7-day median)" : null, outages.length ? "red: offline" : null]
    .filter(Boolean).join(" · ") || null;
  document.getElementById("charts-dsl").replaceChildren(
    chart("DSL sync rate", "Mbit/s", ["↓ down", "↑ up"], series(h.status, (r) => [mbit(r[1]), mbit(r[2])]), start, end, statusGap,
          { references: normal, bands: outages, note: syncNote }),
    chart("SNR margin", "dB", ["↓ down", "↑ up"], series(h.line, (r) => [r[1], r[2]]), start, end, detailsGap),
    chart("Attainable rate", "Mbit/s", ["↓ down", "↑ up"], series(h.line, (r) => [mbit(r[3]), mbit(r[4])]), start, end, detailsGap,
          { note: "line capacity; a slow decline means a degrading line" }),
    chart("CRC errors", "per hour", ["↓ down", "↑ up"], series(h.crc, (r) => [r[1], r[2]]), start, end, detailsGap,
          { note: "uncorrectable; 0 is normal" }),
  );
  // Tests are minutes to hours apart: connect them all, mark each one.
  // Tests from a host on Wi-Fi measure Wi-Fi, not the line: they get their own panel in
  // the Wi-Fi colours (only when there are any) and Wi-Fi-coloured latency points.
  const onWifi = (r) => r[4] === "wifi" || r[4] === "router-wifi";
  const tests = (rows, map) => rows.map((r) => ({ time: Date.parse(r[0]), values: map(r), wifi: onWifi(r) }));
  const rates = (r) => [mbit(r[1]), mbit(r[2])];
  const lineTests = h.speedtest.filter((r) => !onWifi(r)), wifiTests = h.speedtest.filter(onWifi);
  const panels = [chart("Download / upload", "Mbit/s", ["↓ down", "↑ up"], tests(lineTests, rates), start, end, Infinity,
                        { dots: true, note: wifiTests.length ? "without Wi-Fi tests" : null })];
  if (wifiTests.length) {
    // Whole panel in the Wi-Fi palette; the points need no extra marking there.
    panels.push(chart("Download / upload over Wi-Fi", "Mbit/s", ["↓ down", "↑ up"],
                      tests(wifiTests, rates).map((p) => ({ ...p, wifi: false })), start, end,
                      Infinity, { dots: true, note: "limited by Wi-Fi, not the line", palette: WIFI_COLORS }));
  }
  panels.push(chart("Latency", "ms", ["latency"], tests(h.speedtest, (r) => [r[3]]), start, end, Infinity,
                    { dots: true, wifiNote: wifiTests.length ? "Wi-Fi" : null }));
  document.getElementById("charts-speedtest").replaceChildren(...panels);
  document.getElementById("charts-mobile").replaceChildren(
    chart("Receiver signal (RSRP)", "dBm", ["5G", "LTE"], series(h.status, (r) => [r[3], r[4]]), start, end, statusGap),
    chart("Signal quality (RSRQ)", "dB", ["5G", "LTE"], series(h.line, (r) => [r[5], r[6]]), start, end, detailsGap,
          { note: "interference and load; below −20 dB is poor" }),
    bondingChart(h.overflow, start, end, detailsGap),
  );
}

// Tabs: the selected one is kept in the URL hash, so reloads and bookmarks keep it.

// A bookmark to #mobile can only be honoured once the first state shows a receiver.
let pendingTab = null;

function renderTabs(state) {
  document.getElementById("tab-mobile").hidden = !state.hasReceiver;
  if (activeTab === "mobile" && !state.hasReceiver) selectTab("overview");
  if (pendingTab === "mobile" && state.hasReceiver) selectTab("mobile");
  pendingTab = null;
}

function selectTab(name) {
  name = TAB_ALIASES[name] || name;
  // The 5G/LTE tab exists only while a receiver is reported.
  const available = (tab) => tab !== "mobile" || (current !== null && current.hasReceiver);
  activeTab = TABS.includes(name) && available(name) ? name : "overview";
  for (const button of document.querySelectorAll(".tabs button")) {
    button.setAttribute("aria-selected", String(button.dataset.tab === activeTab));
  }
  for (const panel of document.querySelectorAll(".tab-panel")) panel.hidden = panel.dataset.panel !== activeTab;
  if (location.hash.slice(1) !== activeTab) history.replaceState(null, "", `#${activeTab}`);
  if (HISTORY_TABS.includes(activeTab)) refreshHistory();
}

for (const button of document.querySelectorAll(".tabs button")) {
  button.addEventListener("click", () => selectTab(button.dataset.tab));
}
window.addEventListener("hashchange", () => selectTab(location.hash.slice(1)));

wireActions();
pendingTab = location.hash.slice(1);
selectTab(pendingTab);
refreshState();
setInterval(refreshState, STATE_INTERVAL_MS);
// History is only reloaded while its tab is visible.
setInterval(() => { if (HISTORY_TABS.includes(activeTab)) refreshHistory(); }, HISTORY_INTERVAL_MS);
