"""Static single-page console for the AgentMemoryOS Web UI.

The page is served as-is and talks to the JSON API with fetch; all dynamic
values are inserted client-side via textContent, so memory content can never
inject markup. Kept as a plain Python string to preserve the zero-build,
zero-packaging deployment story.
"""

from .constants import (
    RETENTION_MIN_HALF_LIVES,
    WEB_UI_GRAPH_SETTLE_FRAME_THRESHOLD,
    WEB_UI_LOGO_DATA_URI,
    WEB_UI_TOAST_DURATION_MILLISECONDS,
)

PAGE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AgentMemoryOS Web UI</title>
<link rel="icon" type="image/png" href="__AMOS_LOGO_DATA_URI__">
<style>
  :root {
    --bg: #f6f7fb; --panel: #ffffff; --panel-2: #f0f2f8;
    --text: #1c2030; --muted: #6b7390; --border: #e2e6f0;
    --accent: #6d3df0; --accent-soft: #efeafe;
    --good: #178a50; --warn: #b3711d; --bad: #c23a3a;
    --shadow: 0 1px 2px rgba(20, 24, 40, .05), 0 8px 24px rgba(20, 24, 40, .06);
    color-scheme: light;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #0d1020; --panel: #151a30; --panel-2: #1b2140;
      --text: #e8ebf7; --muted: #8f97b8; --border: #262e52;
      --accent: #9a7bff; --accent-soft: #2a2352;
      --good: #4cc98a; --warn: #e3a45a; --bad: #ec7b7b;
      --shadow: 0 1px 2px rgba(0,0,0,.4), 0 10px 30px rgba(0,0,0,.35);
      color-scheme: dark;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font: 15px/1.55 Inter, "SF Pro Text", ui-sans-serif, system-ui, "Segoe UI", sans-serif;
  }
  header {
    position: sticky; top: 0; z-index: 10;
    background: color-mix(in srgb, var(--bg) 88%, transparent);
    backdrop-filter: blur(10px);
    border-bottom: 1px solid var(--border);
  }
  .bar {
    max-width: 1080px; margin: 0 auto; padding: 14px 24px;
    display: flex; align-items: center; gap: 16px; flex-wrap: wrap;
  }
  .brand { display: flex; align-items: center; gap: 10px; font-weight: 700; font-size: 17px; }
  .brand .logo { width: 32px; height: 32px; display: block; }
  .stats { display: flex; gap: 8px; flex-wrap: wrap; }
  .chip {
    padding: 4px 12px; border-radius: 999px; font-size: 12.5px;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--muted);
  }
  .chip b { color: var(--text); font-variant-numeric: tabular-nums; }
  .acting { margin-left: auto; display: flex; align-items: center; gap: 8px; font-size: 13px; color: var(--muted); }
  .acting input {
    width: 150px; padding: 6px 10px; border-radius: 8px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); font-size: 13px;
  }
  nav.tabs {
    max-width: 1080px; margin: 0 auto; padding: 0 24px;
    display: flex; gap: 4px;
  }
  nav.tabs button {
    appearance: none; background: none; border: none; cursor: pointer;
    padding: 10px 14px; font-size: 14px; font-weight: 600; color: var(--muted);
    border-bottom: 2px solid transparent; margin-bottom: -1px;
  }
  nav.tabs button.active { color: var(--accent); border-bottom-color: var(--accent); }
  main { max-width: 1080px; margin: 0 auto; padding: 24px; }
  section.tab { display: none; }
  section.tab.active { display: block; }

  .searchrow { display: flex; gap: 10px; margin-bottom: 18px; }
  .searchrow input[type=search] {
    flex: 1; padding: 12px 16px; font-size: 15px; border-radius: 12px;
    border: 1px solid var(--border); background: var(--panel); color: var(--text);
    box-shadow: var(--shadow);
  }
  .searchrow input[type=search]:focus, input:focus, select:focus, textarea:focus {
    outline: 2px solid color-mix(in srgb, var(--accent) 45%, transparent); outline-offset: 0;
    border-color: var(--accent);
  }
  button.primary {
    padding: 10px 20px; border-radius: 12px; border: none; cursor: pointer;
    background: var(--accent); color: #fff; font-weight: 650; font-size: 14.5px;
  }
  button.primary:hover { filter: brightness(1.08); }
  button.ghost {
    padding: 8px 14px; border-radius: 10px; cursor: pointer; font-size: 13px; font-weight: 600;
    background: var(--panel); color: var(--text); border: 1px solid var(--border);
  }
  button.ghost:hover { border-color: var(--accent); color: var(--accent); }

  .cards { display: flex; flex-direction: column; gap: 14px; }
  .card {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 16px 18px; box-shadow: var(--shadow);
  }
  .card .top { display: flex; align-items: center; gap: 8px; flex-wrap: wrap; margin-bottom: 8px; }
  .badge {
    font-size: 11.5px; font-weight: 700; letter-spacing: .3px; text-transform: uppercase;
    padding: 3px 9px; border-radius: 999px; border: 1px solid transparent;
  }
  .badge.scope-user    { background: #e8f0fe; color: #2456c4; }
  .badge.scope-agent   { background: #e2f6f2; color: #0e7a63; }
  .badge.scope-project { background: #fdf1dc; color: #94660d; }
  .badge.scope-team    { background: #fde8f1; color: #b02a6c; }
  .badge.scope-global  { background: #e6f6e8; color: #1e7d33; }
  @media (prefers-color-scheme: dark) {
    .badge.scope-user    { background: #1d2c52; color: #92b4ff; }
    .badge.scope-agent   { background: #12352f; color: #5ad4b8; }
    .badge.scope-project { background: #3a2d12; color: #eec272; }
    .badge.scope-team    { background: #3c1830; color: #f18ebc; }
    .badge.scope-global  { background: #14311c; color: #6fd487; }
  }
  .badge.type { background: none; border-color: var(--border); color: var(--muted); }
  .badge.kind-claude-code { background: #3a2218; color: #e8977a; }
  .badge.kind-codex       { background: #22282a; color: #c7d3d0; }
  .badge.kind-openclaw    { background: #3a1a12; color: #f08a68; }
  .badge.kind-hermes      { background: #241d4e; color: #a897f0; }
  .badge.kind-agy         { background: #12283a; color: #68b0f0; }
  .badge.kind-custom      { background: var(--panel-2); color: var(--muted); }
  .owner { font-size: 12.5px; color: var(--muted); }
  .owner b { color: var(--text); font-weight: 600; }
  .pin { font-size: 13px; }
  .scorewrap { margin-left: auto; display: flex; align-items: center; gap: 8px; }
  .scorebar { width: 90px; height: 6px; border-radius: 3px; background: var(--panel-2); overflow: hidden; }
  .scorebar i { display: block; height: 100%; background: linear-gradient(90deg, var(--accent), #b96bff); border-radius: 3px; }
  .scoreval { font-size: 12px; color: var(--muted); font-variant-numeric: tabular-nums; }
  .content { white-space: pre-wrap; word-break: break-word; font-size: 14.5px; }
  .meta { display: flex; align-items: center; gap: 14px; flex-wrap: wrap; margin-top: 10px; font-size: 12px; color: var(--muted); }
  .tags { display: flex; gap: 6px; flex-wrap: wrap; }
  .tag { background: var(--accent-soft); color: var(--accent); padding: 2px 9px; border-radius: 999px; font-size: 11.5px; font-weight: 600; }
  .gauge { display: inline-flex; align-items: center; gap: 5px; }
  .gauge .dotbar { width: 44px; height: 4px; border-radius: 2px; background: var(--panel-2); overflow: hidden; }
  .gauge .dotbar i { display: block; height: 100%; background: var(--muted); }
  .card .actions { display: flex; gap: 8px; margin-top: 12px; flex-wrap: wrap; }
  .card .actions button {
    font-size: 12.5px; padding: 5px 12px; border-radius: 8px; cursor: pointer;
    background: var(--panel-2); border: 1px solid var(--border); color: var(--muted); font-weight: 600;
  }
  .card .actions button:hover { color: var(--text); border-color: var(--muted); }
  .card .actions button.danger:hover { color: var(--bad); border-color: var(--bad); }
  .reason { margin-top: 8px; font: 11px/1.5 ui-monospace, SFMono-Regular, Menlo, monospace; color: var(--muted); word-break: break-all; display: none; }
  .linksbox { margin-top: 10px; display: none; border-top: 1px dashed var(--border); padding-top: 10px; font-size: 12.5px; color: var(--muted); }
  .linksbox .linkrow { display: flex; gap: 8px; align-items: center; padding: 3px 0; }
  .linksbox .rel { font-weight: 700; color: var(--accent); }
  .empty { text-align: center; color: var(--muted); padding: 48px 0; }
  .empty .big { font-size: 34px; margin-bottom: 8px; }

  form.addform {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 22px; box-shadow: var(--shadow); display: grid; gap: 16px;
    grid-template-columns: 1fr 1fr;
  }
  form.addform .full { grid-column: 1 / -1; }
  label.field { display: flex; flex-direction: column; gap: 6px; font-size: 12.5px; font-weight: 650; color: var(--muted); }
  label.field input[type=text], label.field select, label.field textarea, label.field input[type=datetime-local] {
    padding: 10px 12px; border-radius: 10px; border: 1px solid var(--border);
    background: var(--panel-2); color: var(--text); font-size: 14px; font-family: inherit;
  }
  label.field textarea { min-height: 110px; resize: vertical; }
  .sliderrow { display: flex; align-items: center; gap: 10px; }
  .sliderrow input[type=range] { flex: 1; accent-color: var(--accent); }
  .sliderrow output { width: 38px; text-align: right; font-variant-numeric: tabular-nums; color: var(--text); }
  .checks { display: flex; gap: 22px; align-items: center; font-size: 13.5px; color: var(--text); }
  .checks label { display: flex; gap: 7px; align-items: center; font-weight: 500; }
  .checks input { accent-color: var(--accent); width: 16px; height: 16px; }

  .toolgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
  .tool { background: var(--panel); border: 1px solid var(--border); border-radius: 16px; padding: 20px; box-shadow: var(--shadow); }
  .tool h3 { margin: 0 0 4px; font-size: 15px; }
  .tool p.hint { margin: 0 0 14px; font-size: 12.5px; color: var(--muted); }
  .tool .row { display: flex; gap: 8px; margin-bottom: 10px; flex-wrap: wrap; }
  .tool input, .tool select {
    padding: 8px 11px; border-radius: 9px; border: 1px solid var(--border);
    background: var(--panel-2); color: var(--text); font-size: 13px; flex: 1; min-width: 120px;
  }
  .packtext {
    white-space: pre-wrap; word-break: break-word; background: var(--panel-2);
    border-radius: 12px; padding: 14px; font: 12.5px/1.6 ui-monospace, Menlo, monospace;
    max-height: 320px; overflow: auto; margin-top: 10px;
  }
  .decisions { margin-top: 10px; font-size: 12px; }
  .decisions .drow { display: flex; gap: 8px; align-items: baseline; padding: 4px 0; border-bottom: 1px dashed var(--border); }
  .decisions .ok { color: var(--good); font-weight: 700; }
  .decisions .no { color: var(--muted); }

  .loadmore { display: flex; justify-content: center; margin-top: 16px; }
  .filterrow { display: flex; gap: 8px; margin-bottom: 16px; flex-wrap: wrap; }
  .filterrow select, .filterrow input {
    padding: 8px 11px; border-radius: 9px; border: 1px solid var(--border);
    background: var(--panel); color: var(--text); font-size: 13px;
  }
  .filterrow input { width: 140px; }
  .graphwrap {
    position: relative; background: var(--panel); border: 1px solid var(--border);
    border-radius: 16px; box-shadow: var(--shadow); overflow: hidden;
  }
  #graph-canvas { display: block; width: 100%; height: 540px; cursor: grab; }
  .graphlegend {
    position: absolute; top: 12px; left: 14px; display: flex; gap: 10px; flex-wrap: wrap;
    font-size: 11.5px; color: var(--muted); pointer-events: none;
  }
  .graphlegend .key { display: flex; align-items: center; gap: 5px; }
  .graphlegend .dot { width: 9px; height: 9px; border-radius: 50%; }
  .graphtip {
    position: absolute; display: none; max-width: 320px; padding: 8px 12px;
    background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
    box-shadow: var(--shadow); font-size: 12.5px; pointer-events: none; z-index: 5;
  }
  .graphhint { font-size: 12.5px; color: var(--muted); margin-top: 10px; }
  .tool.danger { border-color: color-mix(in srgb, var(--bad) 45%, var(--border)); }
  .tool.danger h3 { color: var(--bad); }
  button.dangerbtn { color: var(--bad); border-color: color-mix(in srgb, var(--bad) 55%, var(--border)); flex: 0 0 auto; }
  button.dangerbtn:hover { background: var(--bad); border-color: var(--bad); color: #fff; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 14px; margin-bottom: 16px; }
  .tile {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 16px 18px; box-shadow: var(--shadow); display: flex; flex-direction: column; gap: 2px;
  }
  .tilelabel { font-size: 12px; font-weight: 650; color: var(--muted); letter-spacing: .3px; }
  .tileval { font-size: 30px; font-weight: 750; font-variant-numeric: tabular-nums; }
  .panelgrid { display: grid; grid-template-columns: 1fr 1fr; gap: 14px; margin-bottom: 16px; }
  .panel {
    background: var(--panel); border: 1px solid var(--border); border-radius: 16px;
    padding: 18px; box-shadow: var(--shadow); margin-bottom: 16px;
  }
  .panelgrid .panel { margin-bottom: 0; }
  .panel h3 { margin: 0 0 14px; font-size: 13.5px; color: var(--muted); font-weight: 650; letter-spacing: .3px; }
  .hbars { display: flex; flex-direction: column; gap: 9px; }
  .hbar { display: grid; grid-template-columns: 88px 1fr 34px; align-items: center; gap: 10px; font-size: 12.5px; }
  .hbar .name { color: var(--text); text-align: right; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .hbar .track { height: 12px; border-radius: 0 4px 4px 0; background: var(--panel-2); overflow: hidden; }
  .hbar .track i { display: block; height: 100%; border-radius: 0 4px 4px 0; background: var(--accent); }
  .hbar .val { color: var(--muted); font-variant-numeric: tabular-nums; }
  .cols { display: flex; align-items: flex-end; gap: 4px; height: 120px; padding-top: 6px; }
  .cols .col { flex: 1; display: flex; flex-direction: column; justify-content: flex-end; height: 100%; }
  .cols .col i { display: block; background: var(--accent); border-radius: 4px 4px 0 0; min-height: 2px; }
  .cols .col span { font-size: 9.5px; color: var(--muted); text-align: center; margin-top: 5px; }
  .toplist { display: flex; flex-direction: column; gap: 9px; font-size: 13px; }
  .toplist .toprow { display: flex; gap: 10px; align-items: baseline; }
  .toplist .cnt { font-weight: 750; color: var(--accent); min-width: 30px; font-variant-numeric: tabular-nums; }
  .toplist .sm { color: var(--muted); overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .healthrow { display: flex; gap: 24px; flex-wrap: wrap; font-size: 13px; }
  .healthstat { display: flex; flex-direction: column; gap: 2px; }
  .healthstat b { font-size: 20px; font-variant-numeric: tabular-nums; }
  .healthstat span { color: var(--muted); font-size: 11.5px; }
  .editform { display: grid; gap: 10px; margin-top: 4px; }
  .editform textarea, .editform input[type=text], .editform select {
    padding: 9px 11px; border-radius: 9px; border: 1px solid var(--border);
    background: var(--panel-2); color: var(--text); font-size: 13.5px; font-family: inherit; width: 100%;
  }
  .editform textarea { min-height: 90px; resize: vertical; }
  .editform .erow { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; }
  .editform .erow > * { flex: 1; min-width: 110px; }
  @media (max-width: 720px) { .tiles, .panelgrid { grid-template-columns: 1fr 1fr; } }
  #toasts { position: fixed; right: 20px; bottom: 20px; display: flex; flex-direction: column; gap: 8px; z-index: 99; }
  .toast {
    background: var(--panel); border: 1px solid var(--border); border-left: 3px solid var(--accent);
    padding: 11px 16px; border-radius: 12px; box-shadow: var(--shadow); font-size: 13.5px;
    max-width: 360px; animation: slidein .18s ease-out;
  }
  .toast.err { border-left-color: var(--bad); }
  .toast.ok { border-left-color: var(--good); }
  @keyframes slidein { from { transform: translateY(8px); opacity: 0; } to { transform: none; opacity: 1; } }
  @media (max-width: 720px) {
    form.addform, .toolgrid { grid-template-columns: 1fr; }
    .acting { margin-left: 0; width: 100%; }
  }
  #version-badge { position: fixed; right: 10px; bottom: 8px; z-index: 50;
    font-size: 11px; color: var(--muted); background: var(--panel);
    border: 1px solid var(--border); border-radius: 6px; padding: 2px 8px;
    opacity: .72; user-select: none; pointer-events: none; }
  #ro-banner { display: none; margin: 8px 0; padding: 6px 10px; border-radius: 8px;
    font-size: 13px; background: #7a5b00; color: #fff; }
  .usagecard .sub { font-size: 11px; color: var(--muted); font-weight: 400; }
</style>
</head>
<body>
<header>
  <div class="bar">
    <div class="brand"><img class="logo" src="__AMOS_LOGO_DATA_URI__" alt=""> AgentMemoryOS <span style="font-weight:400;color:var(--muted);font-size:13px">Web UI</span><span id="node-name" style="font-weight:400;color:var(--muted);font-size:12px;margin-left:6px"></span></div>
    <div class="stats">
      <span class="chip">Total memories <b id="stat-total">–</b></span>
      <span class="chip">Links <b id="stat-links">–</b></span>
    </div>
    <div class="acting">
      <span title="Requester identity used for search, context packs and feedback. Empty = unrestricted admin view.">Acting as</span>
      <select id="acting-as"><option value="">admin (all)</option></select>
    </div>
  </div>
  <nav class="tabs">
    <button data-tab="dashboard" class="active">Dashboard</button>
    <button data-tab="search">Search</button>
    <button data-tab="browse">Browse</button>
    <button data-tab="graph">Graph</button>
    <button data-tab="agents">Agents</button>
    <button data-tab="teams">Teams</button>
    <button data-tab="fleet">Fleet</button>
    <button data-tab="add">Add memory</button>
    <button data-tab="tools">Tools</button>
  </nav>
</header>

<main>
  <div id="ro-banner"></div>
  <div id="remote-banner" style="display:none;margin:8px 0;padding:7px 12px;border-radius:8px;background:rgba(210,153,34,.14);border:1px solid var(--warn,#d29922);font-size:13px;align-items:center;gap:10px">
    <span id="remote-banner-text"></span>
    <button class="ghost" id="btn-remote-exit" style="font-size:11px;padding:2px 10px">Back to this node</button>
  </div>
  <section class="tab active" id="tab-dashboard">
    <div class="tiles">
      <div class="tile"><span class="tilelabel">Memories</span><span class="tileval" id="d-total">–</span></div>
      <div class="tile"><span class="tilelabel">Links</span><span class="tileval" id="d-links">–</span></div>
      <div class="tile"><span class="tilelabel">Pinned</span><span class="tileval" id="d-pinned">–</span></div>
      <div class="tile"><span class="tilelabel">Expired</span><span class="tileval" id="d-expired">–</span></div>
      <div class="tile"><span class="tilelabel">Archived</span><span class="tileval" id="d-archived">–</span></div>
    </div>
    <div class="panel"><h3>Token usage</h3>
      <div class="tiles" id="usage-cards">
        <div class="tile usagecard"><span class="tilelabel">Total</span><span class="tileval" id="u-total">–</span><span class="sub" id="u-total-sub"></span></div>
        <div class="tile usagecard"><span class="tilelabel">Top agent</span><span class="tileval" id="u-agent">–</span><span class="sub" id="u-agent-sub"></span></div>
        <div class="tile usagecard"><span class="tilelabel">Top team</span><span class="tileval" id="u-team">–</span><span class="sub" id="u-team-sub"></span></div>
        <div class="tile usagecard"><span class="tilelabel">Top project</span><span class="tileval" id="u-project">–</span><span class="sub" id="u-project-sub"></span></div>
      </div>
    </div>
    <div class="panelgrid">
      <div class="panel"><h3>By scope</h3><div class="hbars" id="d-scope"></div></div>
      <div class="panel"><h3>By type</h3><div class="hbars" id="d-type"></div></div>
    </div>
    <div class="panel"><h3>New memories · last 14 days</h3><div class="cols" id="d-activity"></div></div>
    <div class="panelgrid">
      <div class="panel"><h3>Link relations</h3><div class="hbars" id="d-relations"></div></div>
      <div class="panel"><h3>Most recalled</h3><div class="toplist" id="d-top"></div></div>
    </div>
    <div class="panel"><h3>Resonance health</h3>
      <div class="healthrow" id="d-health"></div>
      <div class="toplist" id="d-hubs" style="margin-top:12px"></div>
    </div>
  </section>

  <section class="tab" id="tab-search">
    <div class="searchrow">
      <input id="q" type="search" placeholder="Search memories… (associative recall included)">
      <button class="primary" id="btn-search">Search</button>
    </div>
    <div class="cards" id="search-results">
      <div class="empty"><div class="big">◈</div>Search your agent's memory.<br>Results resonate through linked memories, gated by the acting identity.</div>
    </div>
  </section>

  <section class="tab" id="tab-browse">
    <div class="filterrow">
      <select id="filter-scope">
        <option value="">all scopes</option>
        <option>user</option><option>agent</option><option>project</option>
        <option>team</option><option>global</option>
      </select>
      <select id="filter-type">
        <option value="">all types</option>
        <option>note</option><option>preference</option><option>fact</option>
        <option>procedure</option><option>environment</option><option>decision</option>
        <option>warning</option>
      </select>
      <input id="filter-owner" type="text" placeholder="owner…">
      <button class="ghost" id="btn-filter">Apply</button>
    </div>
    <div class="cards" id="browse-results"></div>
    <div class="loadmore"><button class="ghost" id="btn-more">Load more</button></div>
  </section>

  <section class="tab" id="tab-graph">
    <div class="row" style="margin-bottom:8px">
      <label style="font-size:13px;color:var(--muted)">Filter</label>
      <select id="graph-filter" style="max-width:260px"><option value="">All</option></select>
    </div>
    <div class="graphwrap">
      <canvas id="graph-canvas"></canvas>
      <div class="graphlegend" id="graph-legend"></div>
      <div class="graphtip" id="graph-tip"></div>
    </div>
    <p class="graphhint">Association graph for the acting identity — an edge is shown only when both memories are visible to it. Drag nodes to untangle; click to copy a memory id.</p>
  </section>

  <section class="tab" id="tab-agents">
    <div class="panel">
      <h3>Register / update an agent</h3>
      <p class="hint" style="font-size:12.5px;color:var(--muted);margin:0 0 12px">One project can mix Claude Code, Codex, OpenClaw, and multiple Hermes profiles against this store. Register each with its teams — team members automatically see <code>team:&lt;id&gt;</code> memories, and MCP servers declare identity via <code>AGENT_MEMORY_AGENT_ID</code>.</p>
      <div class="filterrow">
        <input id="ag-id" type="text" placeholder="agent id (e.g. neo)">
        <input id="ag-name" type="text" placeholder="display name">
        <select id="ag-kind">
          <option>claude-code</option><option>codex</option><option>openclaw</option>
          <option>hermes</option><option>agy</option><option selected>custom</option>
        </select>
        <input id="ag-teams" type="text" placeholder="teams, comma separated (= projects)">
        <button class="ghost" id="btn-agent-save">Save agent</button>
      </div>
    </div>
    <div class="cards" id="agents-list"></div>
  </section>

  <section class="tab" id="tab-teams">
    <div class="panel">
      <h3>Create a team</h3>
      <p class="hint" style="font-size:12.5px;color:var(--muted);margin:0 0 12px">A team is a set of node members; each project under it draws members from the team. Team memory reaches all team members; project memory reaches only that project's members.</p>
      <div class="filterrow">
        <input id="tm-id" type="text" placeholder="team id (e.g. apollo)">
        <input id="tm-name" type="text" placeholder="display name (optional)">
        <button class="ghost" id="btn-team-create">Create team</button>
      </div>
    </div>
    <div id="teams-list" style="margin-top:12px"></div>
  </section>

  <section class="tab" id="tab-fleet">
    <div class="panel">
      <h3>Fleet console</h3>
      <p class="hint" style="font-size:12.5px;color:var(--muted);margin:0 0 12px">Every node this console manages, at a glance: version, health, memory totals, owners. Cross-node actions are signed with this node's fleet key and verified, capability-checked, and audited by each node independently — no shared secret crosses the wire.</p>
      <div class="row">
        <button class="ghost" id="btn-fleet-refresh">Refresh fleet</button>
        <button class="ghost" id="btn-fleet-sync-all">⇆ Sync all</button>
        <button class="ghost" id="btn-fleet-update-all">⬆ Update all</button>
      </div>
      <div id="fleet-console-card" style="margin-top:10px"></div>
      <div id="fleet-drift" style="margin-top:6px;font-size:13px;color:var(--warn,#d29922)"></div>
    </div>
    <div class="toplist" id="fleet-nodes" style="margin-top:12px"></div>
    <div id="fleet-out" style="margin-top:8px;font-size:13px;color:var(--muted)"></div>
    <div class="panel" id="fleet-browse" style="display:none;margin-top:12px">
      <h3 id="fleet-browse-title">Remote memories</h3>
      <p class="hint" style="font-size:11.5px">Read live from the node over a signed request — nothing is copied here. The node checks the read-private capability and records this read in its own audit log.</p>
      <div class="row">
        <input id="fleet-browse-owner" type="text" placeholder="owner…" style="font-size:12px;padding:3px 8px;max-width:160px">
        <button class="ghost" id="btn-fleet-browse-apply">Apply</button>
        <button class="ghost" id="btn-fleet-browse-more">Load more</button>
        <button class="ghost" id="btn-fleet-browse-close">Close</button>
      </div>
      <div class="toplist" id="fleet-browse-list" style="margin-top:8px"></div>
    </div>
  </section>

  <section class="tab" id="tab-add">
    <form class="addform" id="add-form">
      <label class="field full">Content
        <textarea id="f-content" required placeholder="What should be remembered?"></textarea>
      </label>
      <label class="field">Owner
        <input type="text" id="f-owner" value="default">
      </label>
      <label class="field">Scope
        <select id="f-scope">
          <option>user</option><option>agent</option><option>project</option>
          <option>team</option><option>global</option>
        </select>
      </label>
      <label class="field">Type
        <select id="f-type">
          <option>note</option><option>preference</option><option>fact</option>
          <option>procedure</option><option>environment</option><option>decision</option>
          <option>warning</option>
        </select>
      </label>
      <label class="field">Tags <span style="font-weight:400">(comma separated)</span>
        <input type="text" id="f-tags" placeholder="deploy, checklist">
      </label>
      <label class="field full">Visibility <span style="font-weight:400">(comma separated: <code>global</code>, <code>agent:neo</code>, <code>team:core</code> — empty = owner only)</span>
        <input type="text" id="f-visibility" placeholder="owner only">
      </label>
      <label class="field">Importance
        <span class="sliderrow"><input type="range" id="f-importance" min="0" max="1" step="0.05" value="0.5"><output id="o-importance">0.50</output></span>
      </label>
      <label class="field">Confidence
        <span class="sliderrow"><input type="range" id="f-confidence" min="0" max="1" step="0.05" value="0.8"><output id="o-confidence">0.80</output></span>
      </label>
      <label class="field">Expires at <span style="font-weight:400">(optional)</span>
        <input type="datetime-local" id="f-expires">
      </label>
      <div class="field checks" style="justify-content:flex-start; padding-top: 22px;">
        <label><input type="checkbox" id="f-pinned"> Pinned</label>
        <label><input type="checkbox" id="f-autolink" checked> Auto-link similar</label>
      </div>
      <div class="full" style="display:flex;justify-content:flex-end">
        <button class="primary" type="submit">Save memory</button>
      </div>
    </form>
  </section>

  <section class="tab" id="tab-tools">
    <div class="toolgrid">
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Context pack preview</h3>
        <p class="hint">Exactly what would be injected into the prompt for the acting identity, with per-memory decisions.</p>
        <div class="row">
          <input id="pack-q" type="text" placeholder="Query">
          <input id="pack-tokens" type="number" value="1200" min="32" max="32000" style="max-width:110px">
          <label style="display:flex;align-items:center;gap:6px;font-size:13px;color:var(--muted)"><input type="checkbox" id="pack-reinforce" style="accent-color:var(--accent)"> auto-reinforce</label>
          <button class="ghost" id="btn-pack">Build pack</button>
        </div>
        <div id="pack-out"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Orchestrated context <span style="font-weight:400;color:var(--muted);font-size:12px">(budget-aware, v0.4)</span></h3>
        <p class="hint">One call, five buckets: session snapshot pointer, bedrock constants, proactive warnings and procedures, then relevance recall. With a session id, repeated calls skip what was already delivered.</p>
        <div class="row">
          <input id="orch-task" type="text" placeholder="Task description">
          <input id="orch-session" type="text" placeholder="session id (optional)" style="max-width:170px">
          <input id="orch-tokens" type="number" value="2000" min="128" max="32000" style="max-width:100px">
          <button class="ghost" id="btn-orchestrate">Orchestrate</button>
        </div>
        <div id="orch-out"></div>
      </div>
      <div class="tool">
        <h3>Link two memories</h3>
        <p class="hint">Authoritative association edge; resonance recall follows it.</p>
        <div class="row"><input id="link-src" type="text" placeholder="src memory id"></div>
        <div class="row"><input id="link-dst" type="text" placeholder="dst memory id"></div>
        <div class="row">
          <select id="link-rel">
            <option>related_to</option><option>caused_by</option><option>supersedes</option>
            <option>derived_from</option><option>co_recalled</option>
          </select>
          <input id="link-weight" type="number" value="0.5" min="0" max="1" step="0.1" style="max-width:90px">
          <button class="ghost" id="btn-link">Link</button>
        </div>
      </div>
      <div class="tool">
        <h3>Consolidate</h3>
        <p class="hint">Merge exact duplicates and synthesize strongly co-recalled clusters into concept memories. Visibility boundaries are never crossed.</p>
        <button class="ghost" id="btn-consolidate">Run consolidation</button>
        <div id="consolidate-out" style="margin-top:10px;font-size:13px;color:var(--muted)"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Retention &amp; archive</h3>
        <p class="hint">Move expired memories into the cold archive (out of recall, restorable). Optionally also archive unpinned memories idle beyond N decay half-lives.</p>
        <div class="row">
          <button class="ghost" id="btn-retention">Archive expired</button>
          <input id="retention-halflives" type="number" min="1" step="0.5" value="__AMOS_RETENTION_MIN_HALF_LIVES__" style="max-width:90px" title="decay half-lives">
          <button class="ghost" id="btn-retention-decay">Also archive decayed</button>
        </div>
        <div id="retention-out" style="margin:6px 0;font-size:13px;color:var(--muted)"></div>
        <div class="toplist" id="archive-list"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Maintenance</h3>
        <p class="hint">Ops utilities: scan health, clean orphaned memories (scoped to a group with no members — visible to nobody), rebuild the search index, and reclaim disk.</p>
        <div class="row">
          <button class="ghost" id="btn-maint-scan">Scan health</button>
          <button class="ghost" id="btn-maint-orphans">Delete orphan memories</button>
          <button class="ghost" id="btn-maint-reindex">Rebuild index</button>
          <button class="ghost" id="btn-maint-vacuum">Vacuum</button>
          <button class="ghost" id="btn-maint-update">Check for updates</button>
        </div>
        <div class="row" style="margin-top:8px">
          <input id="node-name-input" placeholder="node name" style="font-size:12px;padding:3px 8px;min-width:200px">
          <button class="ghost" id="btn-node-rename">Rename node</button>
        </div>
        <p class="hint" style="font-size:11.5px">The node name is what peers see for this instance; peers refresh it automatically on their next sync. Agent IDs are identities and are not affected.</p>
        <div id="maint-out" style="margin:6px 0;font-size:13px;color:var(--muted)"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Logs</h3>
        <p class="hint">Service log of this node (or of the managed node, in remote mode). Shows the last 100 lines by default; the filter searches the whole recent window and returns the last matching lines.</p>
        <div class="row">
          <select id="log-file" style="max-width:220px;font-size:12px"></select>
          <input id="log-q" type="text" placeholder="filter…" style="font-size:12px;padding:3px 8px;max-width:220px">
          <select id="log-lines" style="max-width:110px;font-size:12px">
            <option value="100" selected>100</option>
            <option value="300">300</option>
            <option value="1000">1000</option>
            <option value="2000">2000</option>
          </select>
          <button class="ghost" id="btn-log-refresh">Refresh</button>
        </div>
        <pre id="log-view" style="margin-top:8px;max-height:420px;overflow:auto;font-size:11.5px;line-height:1.5;background:var(--bg,#0f1117);border:1px solid var(--border,#2a2f45);padding:10px 12px;border-radius:8px;white-space:pre-wrap;word-break:break-all"></pre>
        <div id="log-meta" style="margin-top:4px;font-size:11.5px;color:var(--muted)"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Ownership</h3>
        <p class="hint">Every owner that holds memories on this host, with live and archived counts. Browse is filtered by <b>Acting as</b>; this list is not — so memories owned by an identity you are not browsing as (and not shared with you) show up here even when the Browse tab looks empty. Reassign folds one owner's memories into another (the target may already exist); delete removes them for good.</p>
        <div class="row"><button class="ghost" id="btn-owners-refresh">Refresh owners</button></div>
        <div class="toplist" id="owners-list" style="margin-top:8px"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Membership audit</h3>
        <p class="hint">Recent team/project membership changes (create, delete, add/remove member). Actor "web" = a change made through this console.</p>
        <div class="row"><button class="ghost" id="btn-audit-refresh">Refresh</button></div>
        <div class="toplist" id="audit-list" style="margin-top:8px"></div>
      </div>
      <div class="tool" style="grid-column: 1 / -1;">
        <h3>Federation</h3>
        <p class="hint">Move memories between hosts. Download this host's bundle, or import a bundle exported elsewhere — memories and profiles merge last-writer-wins, links keep their strongest form.</p>
        <div class="row">
          <button class="ghost" id="btn-bundle-export">⬇ Download bundle</button>
          <input type="file" id="bundle-file" accept=".jsonl" style="flex:1;min-width:160px">
          <button class="ghost" id="btn-bundle-import">⬆ Import bundle</button>
        </div>
        <div id="sync-out" style="margin-top:6px;font-size:13px;color:var(--muted)"></div>
        <div class="row" style="margin-top:12px">
          <input id="peer-url" type="text" placeholder="peer url, e.g. http://host:8000">
          <input id="peer-token" type="password" placeholder="peer token (optional)" style="max-width:180px">
          <input id="peer-name" type="text" placeholder="peer name (optional)" style="max-width:150px">
          <select id="peer-policy" title="what to sync to this peer" style="max-width:200px">
            <option value="shared">shared (no private)</option>
            <option value="full">full (all — trusted node)</option>
          </select>
          <button class="ghost" id="btn-peer-add">Add peer</button>
          <button class="ghost" id="btn-sync-now">⇆ Sync mesh now</button>
        </div>
        <div class="toplist" id="peer-list" style="margin-top:8px"></div>
      </div>
      <div class="tool danger" style="grid-column: 1 / -1;">
        <h3>⚠ Danger zone — forget an agent</h3>
        <p class="hint">Permanently deletes EVERY memory owned by the agent id, all links touching them, and its recall profile. This cannot be undone.</p>
        <div class="row">
          <input id="purge-owner" type="text" placeholder="agent / owner id (e.g. mizuki)">
          <button class="ghost dangerbtn" id="btn-purge">Delete all memories</button>
        </div>
        <div id="purge-out" style="margin-top:6px;font-size:13px;color:var(--muted)"></div>
      </div>
    </div>
  </section>
</main>

<div id="toasts"></div>

<div id="login-overlay" style="display:none;position:fixed;inset:0;background:rgba(0,0,0,.65);z-index:9999;align-items:center;justify-content:center">
  <div style="background:var(--card,#1b1e2e);border:1px solid var(--border,#2a2f45);padding:22px 24px;border-radius:14px;width:min(420px,90vw);box-shadow:0 12px 40px rgba(0,0,0,.5)">
    <div style="font-weight:700;font-size:16px;margin-bottom:6px">API token required</div>
    <div style="color:var(--muted,#8b93b0);font-size:13px;margin-bottom:14px">Paste the token shown by <code>agent-memory token show</code>.</div>
    <input id="login-token" type="password" placeholder="amos_\u2026" autocomplete="off" style="width:100%;box-sizing:border-box;margin-bottom:12px">
    <div style="display:flex;align-items:center;gap:10px">
      <button class="ghost" id="login-connect">Connect</button>
      <span id="login-err" style="color:#e0555f;font-size:13px"></span>
    </div>
  </div>
</div>

<script>
"use strict";
const $ = (id) => document.getElementById(id);

/* ---------- i18n ---------- */
const LOCALES = { "en": "English", "zh-TW": "繁體中文", "zh-CN": "简体中文", "ja": "日本語", "ko": "한국어" };
const I18N = {
"zh-TW": {
"Teams":"團隊","Maintenance":"維護","Ops utilities: scan health, clean orphaned memories (scoped to a group with no members — visible to nobody), rebuild the search index, and reclaim disk.":"運維工具:健康掃描、清除孤兒記憶(隸屬無成員的群組、無人可見)、重建搜尋索引、回收磁碟。","memories are now orphaned — clean them in Tools → Maintenance.":"筆記憶已成孤兒——請到 工具 → 維護 清理。","Scan health":"健康掃描","Delete orphan memories":"刪除孤兒記憶","Rebuild index":"重建索引","Vacuum":"壓縮回收","Working…":"處理中…","Delete all orphan memories?":"刪除所有孤兒記憶?","Health":"健康","Orphans":"孤兒","Reindex":"重建索引","Create a team":"建立團隊","Create team":"建立團隊","A team is a set of node members; each project under it draws members from the team. Team memory reaches all team members; project memory reaches only that project's members.":"團隊是一組節點成員;底下的每個專案從團隊成員中選取。團隊記憶所有團隊成員可見,專案記憶僅該專案成員可見。","team id (e.g. apollo)":"團隊 id(例:apollo)","display name (optional)":"顯示名稱(選填)","No teams yet. Create one above.":"尚無團隊,請在上方建立。","select node…":"選擇節點…","+ Add":"+ 加入","delete team":"刪除團隊","rename team":"重新命名","New team id":"新的團隊 id","That team id already exists.":"該團隊 id 已存在。","This moves everything scoped to the id:":"以下隸屬此 id 的項目都會一併移動:","member(s)":"位成員","project(s)":"個專案","memory visibility grant(s)":"筆記憶可見性授權","archived":"筆已封存","Memory text that mentions the old id is history and is left unchanged.":"記憶內文提到舊 id 的部分屬於歷史紀錄,不會被改寫。","A rename is local state: it does not propagate as a deletion, so peers may keep the old team id as an inert orphan.":"改名屬於本機狀態:它不會以刪除的形式傳播,因此對等節點可能保留舊團隊 id 成為無作用的孤兒。","Renamed team":"已重新命名團隊","Delete team?":"刪除團隊?","Members":"成員","no members":"尚無成員","Projects (members chosen from the team)":"專案(成員從團隊中選取)","project id":"專案 id","name (optional)":"名稱(選填)","+ Project":"+ 專案",
"Dashboard":"儀表板","Search":"搜尋","Browse":"瀏覽","Graph":"圖譜","Agents":"代理","Add memory":"新增記憶","Tools":"工具",
"Acting as":"目前身分","admin (all)":"管理者(全部)","Total memories":"記憶總數","Links":"關聯",
"Memories":"記憶","Pinned":"釘選","Expired":"已過期","Archived":"已歸檔",
"By scope":"依範圍","By type":"依類型","New memories · last 14 days":"新增記憶 · 近 14 天","Link relations":"關聯類型","Most recalled":"最常被回想","Resonance health":"共鳴健康度",
"linked memories":"有關聯的記憶","orphans (no links)":"孤立(無關聯)","avg links / memory":"平均關聯數","stale links (90d+)":"陳舊關聯(90天+)","Strongest hubs:":"最強樞紐:",
"No recall activity yet — feedback and auto-reinforce will populate this.":"尚無回想活動——回饋與自動強化會填入此處。",
"Search memories… (associative recall included)":"搜尋記憶……(含聯想回想)",
"Search your agent's memory.":"搜尋你的代理的記憶。","Results resonate through linked memories, gated by the acting identity.":"結果會沿關聯記憶共鳴浮現,並受目前身分的權限管控。",
"Searching…":"搜尋中……","Nothing recalled for that query":"沒有回想起相關記憶",
"all scopes":"全部範圍","all types":"全部類型","owner…":"擁有者……","Apply":"套用","Load more":"載入更多","No memories yet. Add the first one.":"還沒有記憶,新增第一筆吧。",
"Association graph for the acting identity — an edge is shown only when both memories are visible to it. Drag nodes to untangle; click to copy a memory id.":"目前身分的關聯圖——僅當兩端記憶皆可見時才顯示邊。拖曳節點整理佈局;點擊複製記憶 ID。",
"Register / update an agent":"註冊/更新代理","Save agent":"儲存代理","agent id (e.g. neo)":"代理 ID(例:neo)","display name":"顯示名稱","teams, comma separated (= projects)":"團隊,逗號分隔(=專案)",
"No agents registered yet.":"尚未註冊任何代理。","no teams":"無團隊","never seen":"從未活動","👤 Act as":"👤 切換身分","🗑 Remove":"🗑 移除","memories":"筆記憶",
"One project can mix Claude Code, Codex, OpenClaw, and multiple Hermes profiles against this store. Register each with its teams — team members automatically see team:<id> memories, and MCP servers declare identity via AGENT_MEMORY_AGENT_ID.":"一個專案可混用 Claude Code、Codex、OpenClaw 與多個 Hermes profiles。為每個代理註冊所屬團隊——成員自動可見 team:<id> 記憶;MCP 伺服器以 AGENT_MEMORY_AGENT_ID 宣告身分。",
"Content":"內容","Owner":"擁有者","Scope":"範圍","Type":"類型","Tags":"標籤","Visibility":"可見性","Importance":"重要性","Confidence":"信心度","Expires at":"過期時間","Save memory":"儲存記憶",
"What should be remembered?":"要記住什麼?","owner only":"僅擁有者","Pinned":"釘選","Auto-link similar":"自動關聯相似記憶",
"Context pack preview":"Context Pack 預覽","Build pack":"產生 Pack","Query":"查詢","auto-reinforce":"自動強化",
"Orchestrated context":"編排式 Context","Orchestrate":"編排","Task description":"任務描述","session id (optional)":"session ID(選填)",
"Link two memories":"連結兩筆記憶","Link":"建立關聯","src memory id":"來源記憶 ID","dst memory id":"目標記憶 ID",
"Consolidate":"整併","Run consolidation":"執行整併",
"Retention & archive":"保留策略與歸檔","Archive expired":"歸檔過期記憶","Also archive decayed":"連同深度衰減","Archive is empty.":"歸檔是空的。","restore":"還原",
"Federation":"聯邦同步","⬇ Download bundle":"⬇ 下載 Bundle","⬆ Import bundle":"⬆ 匯入 Bundle","Add peer":"加入節點","⇆ Sync mesh now":"⇆ 立即同步網格","peer url, e.g. http://host:8000":"節點網址,例:http://host:8000","peer token (optional)":"節點 token(選填)","peer name (optional)":"節點名稱(選填)","Graph unavailable.":"圖譜暫時無法載入。","No peers registered — this host syncs alone.":"尚未註冊任何節點——本機獨立運作。","remove":"移除","full policy shares private memories — use only for your own trusted nodes":"full 政策會外傳私有記憶——僅用於你自己的信任節點",
"⚠ Danger zone — forget an agent":"⚠ 危險區——遺忘一個代理","Delete all memories":"刪除全部記憶","agent / owner id (e.g. mizuki)":"代理/擁有者 ID(例:mizuki)",
"✎ Edit":"✎ 編輯","👍 Helpful":"👍 有幫助","👎 Misleading":"👎 誤導","🔗 Links":"🔗 關聯","⇢ Share":"⇢ 分享","⧉ Copy id":"⧉ 複製 ID","🗑 Delete":"🗑 刪除","why?":"為什麼?","Save":"儲存","Cancel":"取消","No links yet.":"尚無關聯。","Loading…":"載入中……","Ready.":"就緒。","🔒 private":"🔒 私有"
},
"zh-CN": {
"Teams":"团队","Maintenance":"维护","Ops utilities: scan health, clean orphaned memories (scoped to a group with no members — visible to nobody), rebuild the search index, and reclaim disk.":"运维工具:健康扫描、清除孤儿记忆(隶属无成员的群组、无人可见)、重建搜索索引、回收磁盘。","memories are now orphaned — clean them in Tools → Maintenance.":"条记忆已成孤儿——请到 工具 → 维护 清理。","Scan health":"健康扫描","Delete orphan memories":"删除孤儿记忆","Rebuild index":"重建索引","Vacuum":"压缩回收","Working…":"处理中…","Delete all orphan memories?":"删除所有孤儿记忆?","Health":"健康","Orphans":"孤儿","Reindex":"重建索引","Create a team":"创建团队","Create team":"创建团队","A team is a set of node members; each project under it draws members from the team. Team memory reaches all team members; project memory reaches only that project's members.":"团队是一组节点成员;下面的每个项目从团队成员中选取。团队记忆所有团队成员可见,项目记忆仅该项目成员可见。","team id (e.g. apollo)":"团队 id(如:apollo)","display name (optional)":"显示名称(可选)","No teams yet. Create one above.":"尚无团队,请在上方创建。","select node…":"选择节点…","+ Add":"+ 加入","delete team":"删除团队","rename team":"重命名","New team id":"新的团队 id","That team id already exists.":"该团队 id 已存在。","This moves everything scoped to the id:":"以下隶属此 id 的项目都会一并移动:","member(s)":"位成员","project(s)":"个项目","memory visibility grant(s)":"条记忆可见性授权","archived":"条已归档","Memory text that mentions the old id is history and is left unchanged.":"记忆正文提到旧 id 的部分属于历史记录,不会被改写。","A rename is local state: it does not propagate as a deletion, so peers may keep the old team id as an inert orphan.":"重命名属于本机状态:它不会以删除的形式传播,因此对等节点可能保留旧团队 id 成为无作用的孤儿。","Renamed team":"已重命名团队","Delete team?":"删除团队?","Members":"成员","no members":"尚无成员","Projects (members chosen from the team)":"项目(成员从团队中选取)","project id":"项目 id","name (optional)":"名称(可选)","+ Project":"+ 项目",
"Dashboard":"仪表板","Search":"搜索","Browse":"浏览","Graph":"图谱","Agents":"代理","Add memory":"新增记忆","Tools":"工具",
"Acting as":"当前身份","admin (all)":"管理员(全部)","Total memories":"记忆总数","Links":"关联",
"Memories":"记忆","Pinned":"置顶","Expired":"已过期","Archived":"已归档",
"By scope":"按范围","By type":"按类型","New memories · last 14 days":"新增记忆 · 近 14 天","Link relations":"关联类型","Most recalled":"最常被回想","Resonance health":"共鸣健康度",
"linked memories":"有关联的记忆","orphans (no links)":"孤立(无关联)","avg links / memory":"平均关联数","stale links (90d+)":"陈旧关联(90天+)","Strongest hubs:":"最强枢纽:",
"No recall activity yet — feedback and auto-reinforce will populate this.":"尚无回想活动——反馈与自动强化会填充此处。",
"Search memories… (associative recall included)":"搜索记忆……(含联想回想)",
"Search your agent's memory.":"搜索你的代理的记忆。","Results resonate through linked memories, gated by the acting identity.":"结果会沿关联记忆共鸣浮现,并受当前身份的权限管控。",
"Searching…":"搜索中……","Nothing recalled for that query":"没有回想起相关记忆",
"all scopes":"全部范围","all types":"全部类型","owner…":"所有者……","Apply":"应用","Load more":"加载更多","No memories yet. Add the first one.":"还没有记忆,添加第一条吧。",
"Association graph for the acting identity — an edge is shown only when both memories are visible to it. Drag nodes to untangle; click to copy a memory id.":"当前身份的关联图——仅当两端记忆均可见时才显示边。拖拽节点整理布局;点击复制记忆 ID。",
"Register / update an agent":"注册/更新代理","Save agent":"保存代理","agent id (e.g. neo)":"代理 ID(如:neo)","display name":"显示名称","teams, comma separated (= projects)":"团队,逗号分隔(=项目)",
"No agents registered yet.":"尚未注册任何代理。","no teams":"无团队","never seen":"从未活动","👤 Act as":"👤 切换身份","🗑 Remove":"🗑 移除","memories":"条记忆",
"One project can mix Claude Code, Codex, OpenClaw, and multiple Hermes profiles against this store. Register each with its teams — team members automatically see team:<id> memories, and MCP servers declare identity via AGENT_MEMORY_AGENT_ID.":"一个项目可混用 Claude Code、Codex、OpenClaw 与多个 Hermes profiles。为每个代理注册所属团队——成员自动可见 team:<id> 记忆;MCP 服务器以 AGENT_MEMORY_AGENT_ID 声明身份。",
"Content":"内容","Owner":"所有者","Scope":"范围","Type":"类型","Tags":"标签","Visibility":"可见性","Importance":"重要性","Confidence":"置信度","Expires at":"过期时间","Save memory":"保存记忆",
"What should be remembered?":"要记住什么?","owner only":"仅所有者","Auto-link similar":"自动关联相似记忆",
"Context pack preview":"Context Pack 预览","Build pack":"生成 Pack","Query":"查询","auto-reinforce":"自动强化",
"Orchestrated context":"编排式 Context","Orchestrate":"编排","Task description":"任务描述","session id (optional)":"session ID(可选)",
"Link two memories":"连接两条记忆","Link":"建立关联","src memory id":"源记忆 ID","dst memory id":"目标记忆 ID",
"Consolidate":"整并","Run consolidation":"执行整并",
"Retention & archive":"保留策略与归档","Archive expired":"归档过期记忆","Also archive decayed":"连同深度衰减","Archive is empty.":"归档是空的。","restore":"恢复",
"Federation":"联邦同步","⬇ Download bundle":"⬇ 下载 Bundle","⬆ Import bundle":"⬆ 导入 Bundle","Add peer":"添加节点","⇆ Sync mesh now":"⇆ 立即同步网格","peer url, e.g. http://host:8000":"节点地址,如:http://host:8000","peer token (optional)":"节点 token(可选)","peer name (optional)":"节点名称(可选)","Graph unavailable.":"图谱暂时无法加载。","No peers registered — this host syncs alone.":"尚未注册任何节点——本机独立运行。","remove":"移除","full policy shares private memories — use only for your own trusted nodes":"full 策略会外传私有记忆——仅用于你自己的信任节点",
"⚠ Danger zone — forget an agent":"⚠ 危险区——遗忘一个代理","Delete all memories":"删除全部记忆","agent / owner id (e.g. mizuki)":"代理/所有者 ID(如:mizuki)",
"✎ Edit":"✎ 编辑","👍 Helpful":"👍 有帮助","👎 Misleading":"👎 误导","🔗 Links":"🔗 关联","⇢ Share":"⇢ 分享","⧉ Copy id":"⧉ 复制 ID","🗑 Delete":"🗑 删除","why?":"为什么?","Save":"保存","Cancel":"取消","No links yet.":"暂无关联。","Loading…":"加载中……","Ready.":"就绪。","🔒 private":"🔒 私有"
},
"ja": {
"Teams":"チーム","Maintenance":"メンテナンス","Ops utilities: scan health, clean orphaned memories (scoped to a group with no members — visible to nobody), rebuild the search index, and reclaim disk.":"運用ツール:ヘルス確認、孤立記憶(メンバー不在のグループ所属で誰にも見えない)の削除、検索インデックス再構築、ディスク回収。","memories are now orphaned — clean them in Tools → Maintenance.":"件の記憶が孤立しました。ツール → メンテナンス で整理してください。","Scan health":"ヘルス確認","Delete orphan memories":"孤立記憶を削除","Rebuild index":"インデックス再構築","Vacuum":"最適化","Working…":"処理中…","Delete all orphan memories?":"孤立記憶をすべて削除?","Health":"ヘルス","Orphans":"孤立","Reindex":"再構築","Create a team":"チームを作成","Create team":"チームを作成","A team is a set of node members; each project under it draws members from the team. Team memory reaches all team members; project memory reaches only that project's members.":"チームはノードメンバーの集合です。配下の各プロジェクトはチームメンバーから選びます。チーム記憶は全メンバーに、プロジェクト記憶はそのプロジェクトのメンバーだけに見えます。","team id (e.g. apollo)":"チームID(例:apollo)","display name (optional)":"表示名(任意)","No teams yet. Create one above.":"チームがありません。上で作成してください。","select node…":"ノードを選択…","+ Add":"+ 追加","delete team":"チーム削除","rename team":"名称変更","New team id":"新しいチームID","That team id already exists.":"そのチームIDは既に存在します。","This moves everything scoped to the id:":"このIDに属するもの全てが移動します:","member(s)":"名のメンバー","project(s)":"件のプロジェクト","memory visibility grant(s)":"件の記憶の可視性付与","archived":"件はアーカイブ済み","Memory text that mentions the old id is history and is left unchanged.":"記憶の本文にある旧IDの記述は履歴であり、書き換えません。","A rename is local state: it does not propagate as a deletion, so peers may keep the old team id as an inert orphan.":"名称変更はローカルな状態です。削除として伝播しないため、ピアには旧チームIDが無効な孤児として残る場合があります。","Renamed team":"チーム名を変更しました","Delete team?":"チームを削除?","Members":"メンバー","no members":"メンバーなし","Projects (members chosen from the team)":"プロジェクト(メンバーはチームから選択)","project id":"プロジェクトID","name (optional)":"名前(任意)","+ Project":"+ プロジェクト",
"Dashboard":"ダッシュボード","Search":"検索","Browse":"一覧","Graph":"グラフ","Agents":"エージェント","Add memory":"記憶を追加","Tools":"ツール",
"Acting as":"操作中の身元","admin (all)":"管理者(すべて)","Total memories":"記憶総数","Links":"リンク",
"Memories":"記憶","Pinned":"ピン留め","Expired":"期限切れ","Archived":"アーカイブ済み",
"By scope":"スコープ別","By type":"タイプ別","New memories · last 14 days":"新規記憶 · 過去14日","Link relations":"リンク種別","Most recalled":"最も想起された記憶","Resonance health":"共鳴ヘルス",
"linked memories":"リンク済み記憶","orphans (no links)":"孤立(リンクなし)","avg links / memory":"平均リンク数","stale links (90d+)":"古いリンク(90日+)","Strongest hubs:":"最強ハブ:",
"No recall activity yet — feedback and auto-reinforce will populate this.":"まだ想起履歴がありません——フィードバックと自動強化でここに表示されます。",
"Search memories… (associative recall included)":"記憶を検索……(連想想起を含む)",
"Search your agent's memory.":"エージェントの記憶を検索。","Results resonate through linked memories, gated by the acting identity.":"結果はリンクされた記憶を通じて共鳴し、操作中の身元の権限で制御されます。",
"Searching…":"検索中……","Nothing recalled for that query":"該当する記憶は想起されませんでした",
"all scopes":"すべてのスコープ","all types":"すべてのタイプ","owner…":"所有者……","Apply":"適用","Load more":"さらに読み込む","No memories yet. Add the first one.":"まだ記憶がありません。最初の一件を追加しましょう。",
"Association graph for the acting identity — an edge is shown only when both memories are visible to it. Drag nodes to untangle; click to copy a memory id.":"操作中の身元の関連グラフ——両端の記憶が可視の場合のみエッジを表示。ノードをドラッグで整理、クリックで記憶IDをコピー。",
"Register / update an agent":"エージェントの登録/更新","Save agent":"エージェントを保存","agent id (e.g. neo)":"エージェントID(例:neo)","display name":"表示名","teams, comma separated (= projects)":"チーム、カンマ区切り(=プロジェクト)",
"No agents registered yet.":"登録されたエージェントはまだありません。","no teams":"チームなし","never seen":"活動記録なし","👤 Act as":"👤 この身元で操作","🗑 Remove":"🗑 削除","memories":"件の記憶",
"One project can mix Claude Code, Codex, OpenClaw, and multiple Hermes profiles against this store. Register each with its teams — team members automatically see team:<id> memories, and MCP servers declare identity via AGENT_MEMORY_AGENT_ID.":"一つのプロジェクトで Claude Code・Codex・OpenClaw・複数の Hermes プロファイルを併用できます。各エージェントをチームと共に登録——メンバーは team:<id> の記憶を自動的に閲覧でき、MCP サーバーは AGENT_MEMORY_AGENT_ID で身元を宣言します。",
"Content":"内容","Owner":"所有者","Scope":"スコープ","Type":"タイプ","Tags":"タグ","Visibility":"可視性","Importance":"重要度","Confidence":"確信度","Expires at":"有効期限","Save memory":"記憶を保存",
"What should be remembered?":"何を記憶しますか?","owner only":"所有者のみ","Auto-link similar":"類似記憶を自動リンク",
"Context pack preview":"コンテキストパックのプレビュー","Build pack":"パック生成","Query":"クエリ","auto-reinforce":"自動強化",
"Orchestrated context":"オーケストレーテッド・コンテキスト","Orchestrate":"編成","Task description":"タスクの説明","session id (optional)":"セッションID(任意)",
"Link two memories":"記憶をリンク","Link":"リンク","src memory id":"元の記憶ID","dst memory id":"先の記憶ID",
"Consolidate":"統合","Run consolidation":"統合を実行",
"Retention & archive":"保持とアーカイブ","Archive expired":"期限切れをアーカイブ","Also archive decayed":"減衰分も含める","Archive is empty.":"アーカイブは空です。","restore":"復元",
"Federation":"フェデレーション","⬇ Download bundle":"⬇ バンドルをダウンロード","⬆ Import bundle":"⬆ バンドルをインポート","Add peer":"ピアを追加","⇆ Sync mesh now":"⇆ 今すぐメッシュ同期","peer url, e.g. http://host:8000":"ピアURL(例:http://host:8000)","peer token (optional)":"ピアトークン(任意)","peer name (optional)":"ピア名(任意)","Graph unavailable.":"グラフを読み込めません。","No peers registered — this host syncs alone.":"ピア未登録——このホストは単独で動作します。","remove":"削除","full policy shares private memories — use only for your own trusted nodes":"fullポリシーはプライベート記憶も送信します——自分の信頼できるノードのみに使用してください",
"⚠ Danger zone — forget an agent":"⚠ 危険ゾーン——エージェントを忘却","Delete all memories":"全記憶を削除","agent / owner id (e.g. mizuki)":"エージェント/所有者ID(例:mizuki)",
"✎ Edit":"✎ 編集","👍 Helpful":"👍 役立った","👎 Misleading":"👎 誤解を招く","🔗 Links":"🔗 リンク","⇢ Share":"⇢ 共有","⧉ Copy id":"⧉ IDコピー","🗑 Delete":"🗑 削除","why?":"理由は?","Save":"保存","Cancel":"キャンセル","No links yet.":"リンクはまだありません。","Loading…":"読み込み中……","Ready.":"準備完了。","🔒 private":"🔒 プライベート"
},
"ko": {
"Teams":"팀","Maintenance":"유지보수","Ops utilities: scan health, clean orphaned memories (scoped to a group with no members — visible to nobody), rebuild the search index, and reclaim disk.":"운영 도구: 상태 점검, 고아 기억(멤버 없는 그룹 소속 · 아무도 못 봄) 정리, 검색 색인 재생성, 디스크 회수.","memories are now orphaned — clean them in Tools → Maintenance.":"개의 기억이 고아가 되었습니다 — 도구 → 유지보수 에서 정리하세요.","Scan health":"상태 점검","Delete orphan memories":"고아 기억 삭제","Rebuild index":"색인 재생성","Vacuum":"압축 정리","Working…":"처리 중…","Delete all orphan memories?":"모든 고아 기억을 삭제할까요?","Health":"상태","Orphans":"고아","Reindex":"재색인","Create a team":"팀 만들기","Create team":"팀 만들기","A team is a set of node members; each project under it draws members from the team. Team memory reaches all team members; project memory reaches only that project's members.":"팀은 노드 멤버의 집합입니다. 하위 각 프로젝트는 팀 멤버 중에서 선택합니다. 팀 기억은 모든 팀 멤버에게, 프로젝트 기억은 해당 프로젝트 멤버에게만 보입니다.","team id (e.g. apollo)":"팀 id(예: apollo)","display name (optional)":"표시 이름(선택)","No teams yet. Create one above.":"팀이 없습니다. 위에서 만드세요.","select node…":"노드 선택…","+ Add":"+ 추가","delete team":"팀 삭제","rename team":"이름 변경","New team id":"새 팀 id","That team id already exists.":"해당 팀 id가 이미 있습니다.","This moves everything scoped to the id:":"이 id에 속한 모든 것이 함께 이동합니다:","member(s)":"명의 멤버","project(s)":"개의 프로젝트","memory visibility grant(s)":"건의 기억 가시성 권한","archived":"건은 보관됨","Memory text that mentions the old id is history and is left unchanged.":"기억 본문에 있는 옛 id 언급은 기록이므로 바꾸지 않습니다.","A rename is local state: it does not propagate as a deletion, so peers may keep the old team id as an inert orphan.":"이름 변경은 로컬 상태입니다. 삭제로 전파되지 않으므로 피어에는 옛 팀 id가 무효한 고아로 남을 수 있습니다.","Renamed team":"팀 이름을 변경했습니다","Delete team?":"팀을 삭제할까요?","Members":"멤버","no members":"멤버 없음","Projects (members chosen from the team)":"프로젝트(멤버는 팀에서 선택)","project id":"프로젝트 id","name (optional)":"이름(선택)","+ Project":"+ 프로젝트",
"Dashboard":"대시보드","Search":"검색","Browse":"둘러보기","Graph":"그래프","Agents":"에이전트","Add memory":"기억 추가","Tools":"도구",
"Acting as":"현재 신원","admin (all)":"관리자(전체)","Total memories":"기억 총수","Links":"연결",
"Memories":"기억","Pinned":"고정됨","Expired":"만료됨","Archived":"보관됨",
"By scope":"범위별","By type":"유형별","New memories · last 14 days":"신규 기억 · 최근 14일","Link relations":"연결 유형","Most recalled":"가장 많이 회상됨","Resonance health":"공명 상태",
"linked memories":"연결된 기억","orphans (no links)":"고립(연결 없음)","avg links / memory":"평균 연결 수","stale links (90d+)":"오래된 연결(90일+)","Strongest hubs:":"최강 허브:",
"No recall activity yet — feedback and auto-reinforce will populate this.":"아직 회상 활동이 없습니다 — 피드백과 자동 강화로 채워집니다.",
"Search memories… (associative recall included)":"기억 검색…(연상 회상 포함)",
"Search your agent's memory.":"에이전트의 기억을 검색하세요.","Results resonate through linked memories, gated by the acting identity.":"결과는 연결된 기억을 통해 공명하며, 현재 신원의 권한으로 제어됩니다.",
"Searching…":"검색 중……","Nothing recalled for that query":"해당 쿼리로 회상된 기억이 없습니다",
"all scopes":"모든 범위","all types":"모든 유형","owner…":"소유자……","Apply":"적용","Load more":"더 불러오기","No memories yet. Add the first one.":"아직 기억이 없습니다. 첫 기억을 추가하세요.",
"Association graph for the acting identity — an edge is shown only when both memories are visible to it. Drag nodes to untangle; click to copy a memory id.":"현재 신원의 연관 그래프 — 양쪽 기억이 모두 보일 때만 엣지가 표시됩니다. 노드를 드래그해 정리하고, 클릭하면 기억 ID가 복사됩니다.",
"Register / update an agent":"에이전트 등록/수정","Save agent":"에이전트 저장","agent id (e.g. neo)":"에이전트 ID(예: neo)","display name":"표시 이름","teams, comma separated (= projects)":"팀, 쉼표로 구분(=프로젝트)",
"No agents registered yet.":"등록된 에이전트가 없습니다.","no teams":"팀 없음","never seen":"활동 기록 없음","👤 Act as":"👤 이 신원으로 전환","🗑 Remove":"🗑 제거","memories":"개의 기억",
"One project can mix Claude Code, Codex, OpenClaw, and multiple Hermes profiles against this store. Register each with its teams — team members automatically see team:<id> memories, and MCP servers declare identity via AGENT_MEMORY_AGENT_ID.":"하나의 프로젝트에서 Claude Code, Codex, OpenClaw, 여러 Hermes 프로필을 함께 사용할 수 있습니다. 각 에이전트를 팀과 함께 등록하세요 — 팀원은 team:<id> 기억을 자동으로 볼 수 있고, MCP 서버는 AGENT_MEMORY_AGENT_ID로 신원을 선언합니다.",
"Content":"내용","Owner":"소유자","Scope":"범위","Type":"유형","Tags":"태그","Visibility":"공개 범위","Importance":"중요도","Confidence":"신뢰도","Expires at":"만료 시각","Save memory":"기억 저장",
"What should be remembered?":"무엇을 기억할까요?","owner only":"소유자 전용","Auto-link similar":"유사 기억 자동 연결",
"Context pack preview":"컨텍스트 팩 미리보기","Build pack":"팩 생성","Query":"쿼리","auto-reinforce":"자동 강화",
"Orchestrated context":"오케스트레이션 컨텍스트","Orchestrate":"오케스트레이션","Task description":"작업 설명","session id (optional)":"세션 ID(선택)",
"Link two memories":"기억 두 개 연결","Link":"연결","src memory id":"원본 기억 ID","dst memory id":"대상 기억 ID",
"Consolidate":"통합","Run consolidation":"통합 실행",
"Retention & archive":"보존 및 보관","Archive expired":"만료 기억 보관","Also archive decayed":"감쇠 기억도 포함","Archive is empty.":"보관함이 비어 있습니다.","restore":"복원",
"Federation":"페더레이션","⬇ Download bundle":"⬇ 번들 다운로드","⬆ Import bundle":"⬆ 번들 가져오기","Add peer":"피어 추가","⇆ Sync mesh now":"⇆ 지금 메시 동기화","peer url, e.g. http://host:8000":"피어 URL, 예: http://host:8000","peer token (optional)":"피어 토큰(선택)","peer name (optional)":"피어 이름(선택)","Graph unavailable.":"그래프를 불러올 수 없습니다.","No peers registered — this host syncs alone.":"등록된 피어가 없습니다 — 이 호스트는 단독으로 동작합니다.","remove":"제거","full policy shares private memories — use only for your own trusted nodes":"full 정책은 비공개 기억까지 전송합니다 — 신뢰하는 자체 노드에만 사용하세요",
"⚠ Danger zone — forget an agent":"⚠ 위험 구역 — 에이전트 망각","Delete all memories":"모든 기억 삭제","agent / owner id (e.g. mizuki)":"에이전트/소유자 ID(예: mizuki)",
"✎ Edit":"✎ 편집","👍 Helpful":"👍 도움됨","👎 Misleading":"👎 오해 유발","🔗 Links":"🔗 연결","⇢ Share":"⇢ 공유","⧉ Copy id":"⧉ ID 복사","🗑 Delete":"🗑 삭제","why?":"이유는?","Save":"저장","Cancel":"취소","No links yet.":"아직 연결이 없습니다.","Loading…":"불러오는 중……","Ready.":"준비 완료.","🔒 private":"🔒 비공개"
}
};

Object.assign(I18N["zh-TW"], {"Token usage":"Token 用量","Total":"總計","Top agent":"最高 Agent","Top team":"最高團隊","Top project":"最高專案","none":"無","Check for updates":"檢查更新","Update now":"立即更新","Up to date":"已是最新版本","A new version is available":"有新版本可用","Updating… the console will restart shortly.":"更新中…主控台即將重新啟動。","Pull the new image tag and recreate the container.":"請拉取新映像標籤並重建容器。","Membership audit":"成員稽核","Refresh":"重新整理","No membership changes yet.":"尚無成員異動。","Filter":"篩選","All":"全部","memories":"筆記憶","read-only mode — changes are disabled":"唯讀模式——已停用修改"});
Object.assign(I18N["zh-CN"], {"Token usage":"Token 用量","Total":"总计","Top agent":"最高 Agent","Top team":"最高团队","Top project":"最高项目","none":"无","Check for updates":"检查更新","Update now":"立即更新","Up to date":"已是最新版本","A new version is available":"有新版本可用","Updating… the console will restart shortly.":"更新中…控制台即将重新启动。","Pull the new image tag and recreate the container.":"请拉取新镜像标签并重建容器。","Membership audit":"成员审计","Refresh":"刷新","No membership changes yet.":"暂无成员变动。","Filter":"筛选","All":"全部","memories":"条记忆","read-only mode — changes are disabled":"只读模式——已禁用修改"});
Object.assign(I18N["ja"], {"Token usage":"トークン使用量","Total":"合計","Top agent":"トップエージェント","Top team":"トップチーム","Top project":"トッププロジェクト","none":"なし","Check for updates":"更新を確認","Update now":"今すぐ更新","Up to date":"最新です","A new version is available":"新しいバージョンがあります","Updating… the console will restart shortly.":"更新中…まもなくコンソールが再起動します。","Pull the new image tag and recreate the container.":"新しいイメージタグを取得してコンテナを再作成してください。","Membership audit":"メンバー監査","Refresh":"更新","No membership changes yet.":"メンバー変更はまだありません。","Filter":"フィルター","All":"すべて","memories":"件の記憶","read-only mode — changes are disabled":"読み取り専用モード — 変更は無効です"});
Object.assign(I18N["ko"], {"Token usage":"토큰 사용량","Total":"합계","Top agent":"최상위 에이전트","Top team":"최상위 팀","Top project":"최상위 프로젝트","none":"없음","Check for updates":"업데이트 확인","Update now":"지금 업데이트","Up to date":"최신 상태입니다","A new version is available":"새 버전이 있습니다","Updating… the console will restart shortly.":"업데이트 중… 콘솔이 곧 다시 시작됩니다.","Pull the new image tag and recreate the container.":"새 이미지 태그를 받아 컨테이너를 다시 만드세요.","Membership audit":"멤버 감사","Refresh":"새로고침","No membership changes yet.":"아직 멤버 변경이 없습니다.","Filter":"필터","All":"전체","memories":"개의 기억","read-only mode — changes are disabled":"읽기 전용 모드 — 변경이 비활성화됨"});

Object.assign(I18N["zh-TW"], {"Ownership":"擁有權","Refresh owners":"重新整理擁有者","No owners yet.":"尚無擁有者。","live":"現存","archived":"已歸檔","registered":"已註冊","not shown while acting as":"以此身分瀏覽時不顯示:","Reassign…":"重新指派…","Delete":"刪除","Reassign every memory owned by":"將此擁有者的所有記憶重新指派","to which owner? (the target may already exist — its memories are kept and these are folded in)":"到哪個擁有者?(目標可存在——保留其記憶並把這些併入)","Enter a target owner.":"請輸入目標擁有者。","Source and target are the same.":"來源與目標相同。","moved":"筆已移轉","registered so it's recognized":"已註冊,系統可辨識","This permanently deletes ALL memories, links and the recall profile of":"這將永久刪除以下擁有者的所有記憶、關聯與召回設定檔:","Type the owner id again to confirm:":"再次輸入擁有者 ID 以確認:","Confirmation did not match — nothing was deleted.":"確認不符——未刪除任何項目。","Owner":"擁有者","forgotten":"已遺忘","Working…":"處理中…"});
Object.assign(I18N["zh-CN"], {"Ownership":"归属","Refresh owners":"刷新所有者","No owners yet.":"暂无所有者。","live":"现存","archived":"已归档","registered":"已注册","not shown while acting as":"以此身份浏览时不显示:","Reassign…":"重新指派…","Delete":"删除","Reassign every memory owned by":"将该所有者的所有记忆重新指派","to which owner? (the target may already exist — its memories are kept and these are folded in)":"到哪个所有者?(目标可已存在——保留其记忆并将这些并入)","Enter a target owner.":"请输入目标所有者。","Source and target are the same.":"来源与目标相同。","moved":"条已迁移","registered so it's recognized":"已注册,系统可识别","This permanently deletes ALL memories, links and the recall profile of":"这将永久删除以下所有者的全部记忆、关联与召回配置:","Type the owner id again to confirm:":"再次输入所有者 ID 以确认:","Confirmation did not match — nothing was deleted.":"确认不符——未删除任何内容。","Owner":"所有者","forgotten":"已遗忘","Working…":"处理中…"});
Object.assign(I18N["ja"], {"Ownership":"所有権","Refresh owners":"所有者を更新","No owners yet.":"所有者はまだいません。","live":"現存","archived":"アーカイブ済み","registered":"登録済み","not shown while acting as":"この識別子で閲覧中は非表示:","Reassign…":"再割り当て…","Delete":"削除","Reassign every memory owned by":"次の所有者のすべての記憶を再割り当て","to which owner? (the target may already exist — its memories are kept and these are folded in)":"どの所有者へ?(既存でも可——その記憶は保持され、これらが統合されます)","Enter a target owner.":"対象の所有者を入力してください。","Source and target are the same.":"元と先が同じです。","moved":"件を移動","registered so it's recognized":"登録済み・認識可能に","This permanently deletes ALL memories, links and the recall profile of":"次の所有者のすべての記憶・リンク・想起プロファイルを完全に削除します:","Type the owner id again to confirm:":"確認のため所有者IDを再入力:","Confirmation did not match — nothing was deleted.":"確認が一致しません——何も削除されていません。","Owner":"所有者","forgotten":"忘却しました","Working…":"処理中…"});
Object.assign(I18N["ko"], {"Ownership":"소유권","Refresh owners":"소유자 새로고침","No owners yet.":"아직 소유자가 없습니다.","live":"현존","archived":"보관됨","registered":"등록됨","not shown while acting as":"이 신원으로 탐색 중에는 표시 안 됨:","Reassign…":"재할당…","Delete":"삭제","Reassign every memory owned by":"다음 소유자의 모든 기억을 재할당","to which owner? (the target may already exist — its memories are kept and these are folded in)":"어느 소유자로? (대상이 이미 있어도 됨 — 그 기억은 유지되고 이것들이 병합됩니다)","Enter a target owner.":"대상 소유자를 입력하세요.","Source and target are the same.":"원본과 대상이 같습니다.","moved":"개 이동됨","registered so it's recognized":"등록되어 인식됨","This permanently deletes ALL memories, links and the recall profile of":"다음 소유자의 모든 기억·링크·회상 프로필을 영구 삭제합니다:","Type the owner id again to confirm:":"확인을 위해 소유자 ID를 다시 입력:","Confirmation did not match — nothing was deleted.":"확인이 일치하지 않음 — 아무것도 삭제되지 않았습니다.","Owner":"소유자","forgotten":"잊음","Working…":"처리 중…"});
Object.assign(I18N["zh-TW"], {"connected":"已連線","disconnected":"已斷線","reachable but degraded":"可連線但狀態異常","local (no peer)":"本機(無 peer)"});
Object.assign(I18N["zh-CN"], {"connected":"已连接","disconnected":"已断开","reachable but degraded":"可连接但状态异常","local (no peer)":"本机(无 peer)"});
Object.assign(I18N["ja"], {"connected":"接続済み","disconnected":"切断","reachable but degraded":"到達可能だが劣化","local (no peer)":"ローカル(peer なし)"});
Object.assign(I18N["ko"], {"connected":"연결됨","disconnected":"연결 끊김","reachable but degraded":"도달 가능하나 저하됨","local (no peer)":"로컬(피어 없음)"});
Object.assign(I18N["zh-TW"], {"Fleet":"艦隊","Fleet console":"艦隊主控台","Every node this console manages, at a glance: version, health, memory totals, owners. Cross-node actions are signed with this node's fleet key and verified, capability-checked, and audited by each node independently — no shared secret crosses the wire.":"這個主控台管理的所有節點一覽:版本、健康、記憶總數、擁有者。跨節點操作以本節點的艦隊金鑰簽章,由各節點各自驗證、檢查能力並稽核——不會有共享祕密經過網路。","Refresh fleet":"重新整理艦隊","⇆ Sync all":"⇆ 全部同步","⬆ Update all":"⬆ 全部更新","This node has no fleet key — it is a managed node, not a console. To make it the console, run:":"本節點沒有艦隊金鑰——它是受管節點,不是主控台。要讓它成為主控台,請執行:","key":"金鑰","⚠ version drift across the fleet:":"⚠ 艦隊版本不一致:","No managed nodes — register peers (join / peers add), then run fleet grant on each.":"尚無受管節點——先註冊 peer(join / peers add),再到各節點執行 fleet grant。","reachable but not granted":"可連線但未授權","not granted — run fleet grant on this node":"未授權——請在該節點執行 fleet grant","Sync":"同步","Update":"更新","Update this node? It will upgrade itself and restart.":"更新此節點?它會自行升級並重啟。","Update ALL nodes? Each will upgrade itself and restart.":"更新「全部」節點?每台都會自行升級並重啟。","owners":"位擁有者","no managed nodes":"尚無受管節點"});
Object.assign(I18N["zh-CN"], {"Fleet":"舰队","Fleet console":"舰队控制台","Every node this console manages, at a glance: version, health, memory totals, owners. Cross-node actions are signed with this node's fleet key and verified, capability-checked, and audited by each node independently — no shared secret crosses the wire.":"该控制台管理的所有节点一览:版本、健康、记忆总数、所有者。跨节点操作以本节点的舰队密钥签名,由各节点独立验证、检查能力并审计——没有共享秘密经过网络。","Refresh fleet":"刷新舰队","⇆ Sync all":"⇆ 全部同步","⬆ Update all":"⬆ 全部更新","This node has no fleet key — it is a managed node, not a console. To make it the console, run:":"本节点没有舰队密钥——它是受管节点,不是控制台。要让它成为控制台,请运行:","key":"密钥","⚠ version drift across the fleet:":"⚠ 舰队版本不一致:","No managed nodes — register peers (join / peers add), then run fleet grant on each.":"尚无受管节点——先注册 peer(join / peers add),再到各节点运行 fleet grant。","reachable but not granted":"可连接但未授权","not granted — run fleet grant on this node":"未授权——请在该节点运行 fleet grant","Sync":"同步","Update":"更新","Update this node? It will upgrade itself and restart.":"更新此节点?它会自行升级并重启。","Update ALL nodes? Each will upgrade itself and restart.":"更新「全部」节点?每台都会自行升级并重启。","owners":"位所有者","no managed nodes":"尚无受管节点"});
Object.assign(I18N["ja"], {"Fleet":"フリート","Fleet console":"フリートコンソール","Every node this console manages, at a glance: version, health, memory totals, owners. Cross-node actions are signed with this node's fleet key and verified, capability-checked, and audited by each node independently — no shared secret crosses the wire.":"このコンソールが管理する全ノードを一望:バージョン、ヘルス、記憶総数、所有者。ノード間操作は本ノードのフリート鍵で署名され、各ノードが独立に検証・権限確認・監査します——共有シークレットはネットワークを流れません。","Refresh fleet":"フリートを更新","⇆ Sync all":"⇆ すべて同期","⬆ Update all":"⬆ すべて更新","This node has no fleet key — it is a managed node, not a console. To make it the console, run:":"このノードにはフリート鍵がありません——管理対象ノードであり、コンソールではありません。コンソールにするには実行:","key":"鍵","⚠ version drift across the fleet:":"⚠ フリート内でバージョン不一致:","No managed nodes — register peers (join / peers add), then run fleet grant on each.":"管理対象ノードがありません——peer を登録(join / peers add)し、各ノードで fleet grant を実行してください。","reachable but not granted":"到達可能だが未許可","not granted — run fleet grant on this node":"未許可——このノードで fleet grant を実行","Sync":"同期","Update":"更新","Update this node? It will upgrade itself and restart.":"このノードを更新?自己アップグレードして再起動します。","Update ALL nodes? Each will upgrade itself and restart.":"「全」ノードを更新?各ノードが自己アップグレードして再起動します。","owners":"所有者","no managed nodes":"管理対象ノードなし"});
Object.assign(I18N["ko"], {"Fleet":"플릿","Fleet console":"플릿 콘솔","Every node this console manages, at a glance: version, health, memory totals, owners. Cross-node actions are signed with this node's fleet key and verified, capability-checked, and audited by each node independently — no shared secret crosses the wire.":"이 콘솔이 관리하는 모든 노드 한눈에 보기: 버전, 상태, 기억 총수, 소유자. 노드 간 작업은 이 노드의 플릿 키로 서명되며 각 노드가 독립적으로 검증·권한 확인·감사합니다 — 공유 비밀이 네트워크를 지나지 않습니다.","Refresh fleet":"플릿 새로고침","⇆ Sync all":"⇆ 모두 동기화","⬆ Update all":"⬆ 모두 업데이트","This node has no fleet key — it is a managed node, not a console. To make it the console, run:":"이 노드에는 플릿 키가 없습니다 — 관리 대상 노드이며 콘솔이 아닙니다. 콘솔로 만들려면 실행:","key":"키","⚠ version drift across the fleet:":"⚠ 플릿 버전 불일치:","No managed nodes — register peers (join / peers add), then run fleet grant on each.":"관리 노드 없음 — peer를 등록(join / peers add)한 뒤 각 노드에서 fleet grant를 실행하세요.","reachable but not granted":"도달 가능하나 미승인","not granted — run fleet grant on this node":"미승인 — 이 노드에서 fleet grant 실행","Sync":"동기화","Update":"업데이트","Update this node? It will upgrade itself and restart.":"이 노드를 업데이트할까요? 스스로 업그레이드 후 재시작합니다.","Update ALL nodes? Each will upgrade itself and restart.":"모든 노드를 업데이트할까요? 각 노드가 스스로 업그레이드 후 재시작합니다.","owners":"명의 소유자","no managed nodes":"관리 노드 없음"});
Object.assign(I18N["zh-TW"], {"Browse":"瀏覽","Remote memories":"遠端記憶","Read live from the node over a signed request — nothing is copied here. The node checks the read-private capability and records this read in its own audit log.":"透過簽章請求即時讀取該節點——不會複製到本機。節點會檢查 read-private 能力,並把這次讀取記進它自己的稽核日誌。","This node has not granted read-private to this console — run fleet grant with --caps manage,read-private on it.":"該節點尚未授予本主控台 read-private——請在該節點執行 fleet grant 並加上 --caps manage,read-private。","No memories on this node.":"該節點沒有記憶。","Close":"關閉","admin (all)":"管理者(全部)"});
Object.assign(I18N["zh-CN"], {"Browse":"浏览","Remote memories":"远程记忆","Read live from the node over a signed request — nothing is copied here. The node checks the read-private capability and records this read in its own audit log.":"通过签名请求实时读取该节点——不会复制到本机。节点会检查 read-private 能力,并把这次读取记入它自己的审计日志。","This node has not granted read-private to this console — run fleet grant with --caps manage,read-private on it.":"该节点尚未授予本控制台 read-private——请在该节点运行 fleet grant 并加上 --caps manage,read-private。","No memories on this node.":"该节点没有记忆。","Close":"关闭","admin (all)":"管理员(全部)"});
Object.assign(I18N["ja"], {"Browse":"閲覧","Remote memories":"リモート記憶","Read live from the node over a signed request — nothing is copied here. The node checks the read-private capability and records this read in its own audit log.":"署名リクエストでノードから直接読み取ります——ここへはコピーされません。ノードは read-private 権限を確認し、この読み取りを自身の監査ログに記録します。","This node has not granted read-private to this console — run fleet grant with --caps manage,read-private on it.":"このノードは本コンソールに read-private を許可していません——ノード側で --caps manage,read-private 付きの fleet grant を実行してください。","No memories on this node.":"このノードに記憶はありません。","Close":"閉じる","admin (all)":"管理者(すべて)"});
Object.assign(I18N["ko"], {"Browse":"탐색","Remote memories":"원격 기억","Read live from the node over a signed request — nothing is copied here. The node checks the read-private capability and records this read in its own audit log.":"서명된 요청으로 노드에서 실시간으로 읽습니다 — 여기로 복사되지 않습니다. 노드가 read-private 권한을 확인하고 이 읽기를 자체 감사 로그에 기록합니다.","This node has not granted read-private to this console — run fleet grant with --caps manage,read-private on it.":"이 노드는 콘솔에 read-private를 승인하지 않았습니다 — 해당 노드에서 --caps manage,read-private로 fleet grant를 실행하세요.","No memories on this node.":"이 노드에 기억이 없습니다.","Close":"닫기","admin (all)":"관리자(전체)"});
Object.assign(I18N["zh-TW"], {"Historical context":"歷史情境","delivery records":"筆遞送紀錄","historical context needs owner classification":"歷史情境待歸類擁有者","Classify…":"歸類…","Assign this historical context to which owner?":"要把這批歷史情境歸給哪個擁有者?"});
Object.assign(I18N["zh-CN"], {"Historical context":"历史上下文","delivery records":"条投递记录","historical context needs owner classification":"历史上下文待归类所有者","Classify…":"归类…","Assign this historical context to which owner?":"要把这批历史上下文归给哪个所有者?"});
Object.assign(I18N["ja"], {"Historical context":"過去のコンテキスト","delivery records":"件の配信記録","historical context needs owner classification":"過去のコンテキストは所有者の分類待ち","Classify…":"分類…","Assign this historical context to which owner?":"この過去のコンテキストをどの所有者に割り当てますか?"});
Object.assign(I18N["ko"], {"Historical context":"과거 컨텍스트","delivery records":"건의 전달 기록","historical context needs owner classification":"과거 컨텍스트는 소유자 분류 필요","Classify…":"분류…","Assign this historical context to which owner?":"이 과거 컨텍스트를 어느 소유자에게 지정할까요?"});
Object.assign(I18N["zh-TW"], {"Managing remote node":"管理中的遠端節點","every tab now reads and writes that node, over signed fleet requests audited there.":"所有分頁現在都直接讀寫該節點(經簽章的艦隊請求,並在該節點留下稽核)。","Back to this node":"回到本節點"});
Object.assign(I18N["zh-CN"], {"Managing remote node":"管理中的远程节点","every tab now reads and writes that node, over signed fleet requests audited there.":"所有页签现在都直接读写该节点(经签名的舰队请求,并在该节点留下审计)。","Back to this node":"回到本节点"});
Object.assign(I18N["ja"], {"Managing remote node":"管理中のリモートノード","every tab now reads and writes that node, over signed fleet requests audited there.":"すべてのタブがそのノードを直接読み書きします(署名付きフリートリクエスト、ノード側で監査記録)。","Back to this node":"このノードに戻る"});
Object.assign(I18N["ko"], {"Managing remote node":"관리 중인 원격 노드","every tab now reads and writes that node, over signed fleet requests audited there.":"모든 탭이 이제 해당 노드를 직접 읽고 씁니다(서명된 플릿 요청, 해당 노드에 감사 기록).","Back to this node":"이 노드로 돌아가기"});
Object.assign(I18N["zh-TW"], {"Logs":"日誌","Service log of this node (or of the managed node, in remote mode). Shows the last 100 lines by default; the filter searches the whole recent window and returns the last matching lines.":"本節點的服務日誌(遠端管理模式下為受管節點的日誌)。預設顯示最後 100 行;過濾會搜尋整個近期視窗並回傳最後的相符行。","filter…":"過濾……","No log files found.":"找不到日誌檔。","(no matching lines)":"(沒有相符的行)","lines shown":"行顯示中","matching in the recent window":"行相符(近期視窗內)","older log content beyond the 2 MB window is not searched":"超過 2 MB 視窗的較舊日誌不在搜尋範圍"});
Object.assign(I18N["zh-CN"], {"Logs":"日志","Service log of this node (or of the managed node, in remote mode). Shows the last 100 lines by default; the filter searches the whole recent window and returns the last matching lines.":"本节点的服务日志(远程管理模式下为受管节点的日志)。默认显示最后 100 行;过滤会搜索整个近期窗口并返回最后的匹配行。","filter…":"过滤……","No log files found.":"找不到日志文件。","(no matching lines)":"(没有匹配的行)","lines shown":"行显示中","matching in the recent window":"行匹配(近期窗口内)","older log content beyond the 2 MB window is not searched":"超过 2 MB 窗口的较旧日志不在搜索范围"});
Object.assign(I18N["ja"], {"Logs":"ログ","Service log of this node (or of the managed node, in remote mode). Shows the last 100 lines by default; the filter searches the whole recent window and returns the last matching lines.":"このノードのサービスログ(リモート管理モードでは対象ノードのログ)。既定で最新 100 行を表示。フィルタは直近ウィンドウ全体を検索し、最後に一致した行を返します。","filter…":"フィルタ……","No log files found.":"ログファイルが見つかりません。","(no matching lines)":"(一致する行なし)","lines shown":"行を表示中","matching in the recent window":"行が一致(直近ウィンドウ内)","older log content beyond the 2 MB window is not searched":"2 MB ウィンドウを超える古いログは検索対象外"});
Object.assign(I18N["ko"], {"Logs":"로그","Service log of this node (or of the managed node, in remote mode). Shows the last 100 lines by default; the filter searches the whole recent window and returns the last matching lines.":"이 노드의 서비스 로그(원격 관리 모드에서는 대상 노드의 로그). 기본으로 마지막 100줄을 표시하며, 필터는 최근 창 전체를 검색해 마지막 일치 줄을 반환합니다.","filter…":"필터……","No log files found.":"로그 파일을 찾을 수 없습니다.","(no matching lines)":"(일치하는 줄 없음)","lines shown":"줄 표시 중","matching in the recent window":"줄 일치(최근 창 내)","older log content beyond the 2 MB window is not searched":"2 MB 창을 넘는 오래된 로그는 검색되지 않습니다"});

let locale = localStorage.getItem("amos.locale") || (() => {
  const nav = (navigator.language || "en");
  if (/^zh-(TW|HK|Hant)/i.test(nav)) return "zh-TW";
  if (/^zh/i.test(nav)) return "zh-CN";
  if (/^ja/i.test(nav)) return "ja";
  if (/^ko/i.test(nav)) return "ko";
  return "en";
})();
function t(source) {
  const dictionary = I18N[locale];
  return (dictionary && dictionary[source]) || source;
}
function applyLocale() {
  const selectors = "nav.tabs button, button, h2, .panel h3, .tool h3, .tilelabel, .hint, p.hint, .graphhint, .chip, .acting > span, .empty";
  document.querySelectorAll(selectors).forEach((node) => {
    for (const child of node.childNodes) {
      if (child.nodeType !== Node.TEXT_NODE) continue;
      const original = child.dataset === undefined
        ? (child.__i18nOriginal ?? (child.__i18nOriginal = child.nodeValue.trim()))
        : child.nodeValue.trim();
      if (original) child.nodeValue = child.nodeValue.replace(child.nodeValue.trim(), t(original));
    }
  });
  document.querySelectorAll("label.field, .checks label").forEach((node) => {
    for (const child of node.childNodes) {
      if (child.nodeType !== Node.TEXT_NODE) continue;
      const original = child.__i18nOriginal ?? (child.__i18nOriginal = child.nodeValue.trim());
      if (original) child.nodeValue = child.nodeValue.replace(child.nodeValue.trim(), t(original));
    }
  });
  document.querySelectorAll("[placeholder]").forEach((node) => {
    const original = node.dataset.i18nPh ?? (node.dataset.i18nPh = node.getAttribute("placeholder"));
    node.setAttribute("placeholder", t(original));
  });
  document.querySelectorAll('option[value=""]').forEach((option) => {
    const original = option.dataset.i18n ?? (option.dataset.i18n = option.textContent);
    option.textContent = t(original);
  });
}
(function mountLocalePicker() {
  const acting = document.querySelector(".acting");
  const picker = document.createElement("select");
  picker.id = "locale-pick";
  picker.style.cssText = "padding:6px 8px;border-radius:8px;border:1px solid var(--border);background:var(--panel);color:var(--text);font-size:12.5px";
  for (const [code, name] of Object.entries(LOCALES)) {
    const option = document.createElement("option");
    option.value = code; option.textContent = name;
    if (code === locale) option.selected = true;
    picker.appendChild(option);
  }
  picker.addEventListener("change", () => {
    locale = picker.value;
    localStorage.setItem("amos.locale", locale);
    applyLocale();
  });
  acting.appendChild(picker);
})();

const actingAs = () => $("acting-as").value.trim();
$("acting-as").addEventListener("change", () => {
  localStorage.setItem("amos.actingAs", actingAs());
  syncRemoteTarget();
});
async function syncRemoteTarget() {
  const id = actingAs();
  let target = null;
  if (id) {
    if (!Object.keys(peerUrlByName).length) await fetchPeerStatus();
    const hit = peerUrlByName[id];
    if (hit && hit.ok) target = { url: hit.url, name: id };
  }
  const changed = ((remoteTarget && remoteTarget.url) || "") !== ((target && target.url) || "");
  remoteTarget = target;
  const banner = $("remote-banner");
  if (remoteTarget) {
    $("remote-banner-text").textContent =
      t("Managing remote node") + " “" + remoteTarget.name + "” (" + remoteTarget.url + ") — " +
      t("every tab now reads and writes that node, over signed fleet requests audited there.");
    banner.style.display = "flex";
  } else {
    banner.style.display = "none";
  }
  if (changed) refreshAfterTargetSwitch();
}
function refreshAfterTargetSwitch() {
  browseLoaded = false;
  loadStats(); loadVersionBadge();
  const active = document.querySelector("nav.tabs button.active");
  const tab = active ? active.dataset.tab : "dashboard";
  if (tab === "dashboard") loadDashboard();
  else if (tab === "browse") refreshBrowse();
  else if (tab === "graph") loadGraph();
  else if (tab === "agents") refreshAgents();
  else if (tab === "teams") refreshTeams();
  else if (tab === "tools") { loadOwners(); loadLogs(); }
}
$("btn-remote-exit").addEventListener("click", () => {
  $("acting-as").value = "";
  localStorage.setItem("amos.actingAs", "");
  syncRemoteTarget();
});
function populateActingAs(agents) {
  // A real <select> instead of a datalist: datalist suggestions filter by the
  // field's CURRENT value, so once an identity was chosen it looked like the
  // only option. The select always shows every registered identity.
  const sel = $("acting-as");
  const saved = localStorage.getItem("amos.actingAs") || sel.value || "";
  sel.innerHTML = "";
  sel.appendChild(Object.assign(document.createElement("option"),
    { value: "", textContent: t("admin (all)") }));
  for (const agent of agents)
    sel.appendChild(Object.assign(document.createElement("option"),
      { value: agent.id, textContent: agent.id }));
  if (saved && [...sel.options].some((o) => o.value === saved)) sel.value = saved;
}

function toast(message, kind) {
  const node = document.createElement("div");
  node.className = "toast" + (kind ? " " + kind : "");
  node.textContent = message;
  $("toasts").appendChild(node);
  setTimeout(() => node.remove(), __AMOS_TOAST_DURATION_MILLISECONDS__);
}

/* Remote management mode: when the console operator switches to an identity
   that lives on a managed fleet node, every tab's API call is transparently
   forwarded to that node via the signed fleet proxy — the whole UI becomes
   that node's console. Fleet endpoints and the console's own peer probing
   stay local (they ARE the console's view of the fleet). */
let remoteTarget = null;
function proxied(path) {
  if (!remoteTarget) return path;
  if (!path.startsWith("/api/")) return path;
  if (path.startsWith("/api/fleet") || path.startsWith("/api/peers/status")) return path;
  return "/api/fleet/proxy?url=" + encodeURIComponent(remoteTarget.url) +
         "&path=" + encodeURIComponent(path);
}

async function api(path, options, isRetry) {
  const request = Object.assign({}, options);
  request.headers = Object.assign({}, (options && options.headers) || {});
  const token = localStorage.getItem("amos.token");
  if (token) request.headers["Authorization"] = "Bearer " + token;
  const response = await fetch(proxied(path), request);
  if (response.status === 401) {
    localStorage.removeItem("amos.token");
    showLogin(token ? t("Invalid token \u2014 please re-enter.") : "");
    throw new Error("unauthorized");
  }
  let body = null;
  try { body = await response.json(); } catch (e) { /* empty body */ }
  if (!response.ok) {
    const detail = body && (body.detail || body.error) ? (typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail || body.error)) : ("HTTP " + response.status);
    if (response.status === 403 && /read-only/i.test(detail)) {
      const banner = $("ro-banner");
      if (banner) { banner.textContent = t("read-only mode — changes are disabled"); banner.style.display = "block"; }
    }
    throw new Error(detail);
  }
  return body;
}

function showLogin(err) {
  const o = $("login-overlay");
  if (!o) return;
  o.style.display = "flex";
  $("login-err").textContent = err || "";
  $("login-token").focus();
}
$("login-connect").addEventListener("click", () => {
  const v = $("login-token").value.trim();
  if (!v) { $("login-err").textContent = ""; return; }
  localStorage.setItem("amos.token", v);
  location.reload();
});
$("login-token").addEventListener("keydown", (e) => {
  if (e.key === "Enter") $("login-connect").click();
});

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

/* ---------- peer connection status (color dot) ---------- */
let peerStatusCache = {};
let peerUrlByName = {};
async function fetchPeerStatus() {
  try {
    const data = await api("/api/peers/status");
    const byKey = {};
    for (const s of data.statuses) {
      let state = "down", title = t("disconnected");
      if (s.reachable && s.is_amos && s.status === "ok" && s.integrity !== false) {
        state = "ok"; title = t("connected") + (s.version ? " · v" + s.version : "");
      } else if (s.reachable && s.is_amos) {
        state = "warn"; title = t("reachable but degraded") + (s.detail ? " · " + s.detail : "");
      } else if (s.detail) {
        title = t("disconnected") + " · " + s.detail;
      }
      const entry = { state: state, title: title };
      for (const k of [s.url, s.name, s.node_name]) if (k) byKey[k] = entry;
      for (const k of [s.name, s.node_name]) if (k)
        peerUrlByName[k] = { url: s.url, ok: state !== "down" };
    }
    peerStatusCache = byKey;
  } catch (e) { /* keep last-known cache */ }
  return peerStatusCache;
}
// A small colored dot; entry is undefined for identities with no known peer
// (purely local — rendered as a neutral grey dot so layout stays aligned).
function statusDot(entry) {
  const colors = { ok: "#3fb950", warn: "#d29922", down: "#e0555f" };
  const dot = el("span");
  const color = entry ? (colors[entry.state] || "#8b93b0") : "#8b93b0";
  dot.style.cssText = "display:inline-block;width:9px;height:9px;border-radius:50%;"
    + "margin-right:6px;vertical-align:middle;flex:0 0 auto;background:" + color;
  dot.title = entry ? entry.title : t("local (no peer)");
  return dot;
}
// Match an identity (agent id / display name / member id) to a probed peer.
function statusFor(...keys) {
  for (const k of keys) if (k && peerStatusCache[k]) return peerStatusCache[k];
  return null;
}

async function loadStats() {
  try {
    const stats = await api("/api/stats");
    $("stat-total").textContent = stats.total;
    $("stat-links").textContent = stats.links;
  } catch (e) { /* header stays as dashes */ }
}

/* ---------- tabs ---------- */
document.querySelectorAll("nav.tabs button").forEach((button) => {
  button.addEventListener("click", () => {
    document.querySelectorAll("nav.tabs button").forEach((b) => b.classList.remove("active"));
    document.querySelectorAll("section.tab").forEach((s) => s.classList.remove("active"));
    button.classList.add("active");
    $("tab-" + button.dataset.tab).classList.add("active");
    if (button.dataset.tab === "browse" && !browseLoaded) refreshBrowse();
    if (button.dataset.tab === "graph") loadGraph();
    if (button.dataset.tab === "dashboard") loadDashboard();
    if (button.dataset.tab === "agents") refreshAgents();
    if (button.dataset.tab === "teams") refreshTeams();
    if (button.dataset.tab === "tools") { loadOwners(); loadLogs(); }
    if (button.dataset.tab === "fleet") loadFleet();
  });
});

/* ---------- dashboard ---------- */
function hbarRow(name, value, maxValue, color) {
  const row = el("div", "hbar");
  row.appendChild(el("span", "name", name));
  const track = el("span", "track");
  const fill = el("i");
  fill.style.width = Math.max(2, Math.round((value / maxValue) * 100)) + "%";
  if (color) fill.style.background = color;
  track.appendChild(fill);
  row.appendChild(track);
  row.appendChild(el("span", "val", String(value)));
  return row;
}

function fillBars(containerId, entries, colorFor) {
  const container = $(containerId);
  container.innerHTML = "";
  const items = Object.entries(entries).sort((a, b) => b[1] - a[1]);
  if (!items.length) { container.appendChild(el("span", "sm", "—")); return; }
  const maxValue = Math.max(...items.map(([, v]) => v));
  for (const [name, value] of items) {
    container.appendChild(hbarRow(name, value, maxValue, colorFor ? colorFor(name) : null));
  }
}


async function loadVersionBadge() {
  try {
    const n = await api("/api/node");
    if (n && n.version) $("version-badge").textContent = "v" + n.version;
  } catch (e) { /* pre-auth */ }
}
function fmtTokens(x) {
  if (x >= 1e6) return (x / 1e6).toFixed(1) + "M";
  if (x >= 1e3) return (x / 1e3).toFixed(1) + "k";
  return String(x || 0);
}
async function loadUsage() {
  let u;
  try { u = await api("/api/usage"); } catch (e) { return; }
  const tot = u.total || {};
  $("u-total").textContent = fmtTokens(tot.tokens);
  $("u-total-sub").textContent = (tot.memories || 0) + " " + t("memories");
  const top = (arr) => (arr && arr[0]) ? arr[0] : null;
  const a = top(u.by_agent), tm = top(u.by_team), pr = top(u.by_project);
  $("u-agent").textContent = a ? fmtTokens(a.tokens) : "–";
  $("u-agent-sub").textContent = a ? a.id : t("none");
  $("u-team").textContent = tm ? fmtTokens(tm.tokens) : "–";
  $("u-team-sub").textContent = tm ? tm.id : t("none");
  $("u-project").textContent = pr ? fmtTokens(pr.tokens) : "–";
  $("u-project-sub").textContent = pr ? pr.id : t("none");
}
async function checkForUpdates() {
  const out = $("maint-out");
  out.textContent = t("Working…");
  let r;
  try { r = await api("/api/maintenance/update-check"); }
  catch (e) { out.textContent = String(e.message || e); return; }
  if (r.deployment === "docker") {
    out.textContent = "Docker: " + r.current + " → " + (r.latest || "?") + " · " + t("Pull the new image tag and recreate the container.");
    return;
  }
  if (r.update_available) {
    out.innerHTML = "";
    const msg = document.createElement("span");
    msg.textContent = t("A new version is available") + ": " + r.current + " → " + r.latest + "  ";
    out.appendChild(msg);
    const btn = document.createElement("button");
    btn.className = "primary"; btn.textContent = t("Update now");
    btn.addEventListener("click", async () => {
      if (!confirm(t("Update now") + "?")) return;
      btn.disabled = true;
      try {
        const res = await api("/api/maintenance/update-run?confirm=update", { method: "POST" });
        out.textContent = res.detail || t("Updating… the console will restart shortly.");
      } catch (e) { out.textContent = String(e.message || e); }
    });
    out.appendChild(btn);
  } else {
    out.textContent = t("Up to date") + " (v" + r.current + ")";
  }
}
async function loadAudit() {
  const box = $("audit-list");
  box.textContent = t("Working…");
  let rows;
  try { rows = await api("/api/org/audit"); }
  catch (e) { box.textContent = String(e.message || e); return; }
  const items = (rows && rows.audit) || rows || [];
  box.innerHTML = "";
  if (!items.length) { box.textContent = t("No membership changes yet."); return; }
  for (const it of items) {
    const row = document.createElement("div");
    row.className = "topitem";
    const left = document.createElement("span");
    left.textContent = it.action + " · " + it.detail;
    const right = document.createElement("span");
    right.className = "muted"; right.style.fontSize = "11px";
    right.textContent = (it.actor || "?") + " · " + (it.at || "").replace("T", " ").slice(0, 19);
    row.appendChild(left); row.appendChild(right);
    box.appendChild(row);
  }
}

async function loadOwners() {
  const box = $("owners-list");
  box.textContent = t("Working…");
  let data;
  try { data = await api("/api/owners"); }
  catch (e) { box.textContent = String(e.message || e); return; }
  const owners = (data && data.owners) || [];
  box.innerHTML = "";
  if (!owners.length) { box.textContent = t("No owners yet."); return; }
  const acting = actingAs();
  for (const o of owners) {
    const needsClassification = !!o.classification_required;
    const row = document.createElement("div");
    row.className = "topitem";
    const left = document.createElement("span");
    const name = el("b", null, needsClassification ? t("Historical context") : o.owner);
    left.appendChild(name);
    if (needsClassification) {
      const code = el("span", "muted", o.owner);
      code.style.cssText = "font-size:10px;margin-left:8px";
      left.appendChild(code);
    }
    const meta = el("span", "muted");
    meta.style.fontSize = "11px";
    meta.style.marginLeft = "8px";
    let metaText = o.memories + " " + t("live");
    if (o.archived) metaText += " · " + o.archived + " " + t("archived");
    if (o.context_deliveries) {
      metaText += " · " + o.context_deliveries + " " + t("delivery records");
    }
    if (o.registered_agent) metaText += " · " + t("registered");
    meta.textContent = metaText;
    left.appendChild(meta);
    if (needsClassification) {
      const tag = el("span", "muted");
      tag.style.cssText = "font-size:11px;margin-left:8px;color:var(--warn,#d29922)";
      tag.textContent = t("historical context needs owner classification");
      left.appendChild(tag);
    }
    // Hidden-memory hint: when browsing AS an identity, an owner that is not
    // that identity holds memories the Browse tab may not show.
    if (acting && o.owner !== acting) {
      const tag = el("span", "muted");
      tag.style.cssText = "font-size:11px;margin-left:8px;color:var(--warn,#d29922)";
      tag.textContent = t("not shown while acting as") + " " + acting;
      left.appendChild(tag);
    }
    const right = document.createElement("span");
    right.style.cssText = "display:flex;gap:6px";
    const reBtn = el(
      "button", "ghost", needsClassification ? t("Classify…") : t("Reassign…")
    );
    reBtn.style.fontSize = "11px";
    reBtn.addEventListener("click", () => reassignOwner(o));
    const delBtn = el("button", "danger", t("Delete"));
    delBtn.style.fontSize = "11px";
    delBtn.addEventListener("click", () => deleteOwner(o.owner));
    right.appendChild(reBtn);
    if (!needsClassification) right.appendChild(delBtn);
    row.appendChild(left); row.appendChild(right);
    box.appendChild(row);
  }
}

async function reassignOwner(owner) {
  const oldOwner = owner.owner;
  const n = owner.memories + owner.archived;
  const target = owner.classification_required
    ? prompt(t("Assign this historical context to which owner?"))
    : prompt(
        t("Reassign every memory owned by") + " “" + oldOwner + "” (" + n + ") " +
        t("to which owner? (the target may already exist — its memories are kept and these are folded in)")
      );
  if (target === null) return;
  const newOwner = target.trim();
  if (!newOwner) { toast(t("Enter a target owner."), "err"); return; }
  if (newOwner === oldOwner) { toast(t("Source and target are the same."), "err"); return; }
  try {
    const r = await api("/api/owners/reassign", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ old_owner: oldOwner, new_owner: newOwner }),
    });
    let msg = oldOwner + " → " + newOwner + ": " + r.changed.memories_owner + " " + t("moved");
    if (r.changed.context_deliveries) {
      msg += " · " + r.changed.context_deliveries + " " + t("delivery records");
    }
    if (r.changed.target_registered) msg += " · " + t("registered so it's recognized");
    toast(msg, "ok");
    loadOwners(); loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { toast(e.message, "err"); }
}

async function deleteOwner(owner) {
  const typed = prompt(
    t("This permanently deletes ALL memories, links and the recall profile of") +
    " “" + owner + "”.\n\n" + t("Type the owner id again to confirm:")
  );
  if (typed === null) return;
  if (typed.trim() !== owner) { toast(t("Confirmation did not match — nothing was deleted."), "err"); return; }
  try {
    const r = await api(
      "/api/owners/" + encodeURIComponent(owner) + "/memories?confirm=" + encodeURIComponent(owner),
      { method: "DELETE" }
    );
    toast(t("Owner") + " “" + owner + "” " + t("forgotten") + " (" + r.memories_deleted + ")", "ok");
    loadOwners(); loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { toast(e.message, "err"); }
}

async function loadDashboard() {
  loadUsage();
  let data;
  try { data = await api("/api/dashboard"); }
  catch (e) { toast(e.message, "err"); return; }
  $("d-total").textContent = data.total;
  $("d-links").textContent = data.links;
  $("d-pinned").textContent = data.pinned;
  $("d-expired").textContent = data.expired;
  $("d-archived").textContent = data.archived;
  fillBars("d-scope", data.by_scope, (scope) => SCOPE_COLORS[scope]);
  fillBars("d-type", data.by_type, null);
  fillBars("d-relations", data.by_relation, null);

  const activity = $("d-activity");
  activity.innerHTML = "";
  const maxCount = Math.max(...data.activity.map((d) => d.count), 1);
  for (const dayEntry of data.activity) {
    const col = el("div", "col");
    col.title = dayEntry.day + ": " + dayEntry.count;
    const bar = el("i");
    bar.style.height = Math.round((dayEntry.count / maxCount) * 92) + "%";
    if (dayEntry.count === 0) bar.style.opacity = "0.25";
    col.appendChild(bar);
    col.appendChild(el("span", null, dayEntry.day.slice(5)));
    activity.appendChild(col);
  }

  const top = $("d-top");
  top.innerHTML = "";
  if (!data.top_recalled.length) {
    top.appendChild(el("span", "sm", t("No recall activity yet — feedback and auto-reinforce will populate this.")));
  }
  for (const item of data.top_recalled) {
    const row = el("div", "toprow");
    row.appendChild(el("span", "cnt", "×" + item.access_count));
    row.appendChild(el("span", "sm", item.summary));
    top.appendChild(row);
  }

  const health = data.graph_health;
  const healthRow = $("d-health");
  healthRow.innerHTML = "";
  const stats = [
    [health.linked_memories, t("linked memories")],
    [health.orphan_memories, t("orphans (no links)")],
    [health.avg_degree, t("avg links / memory")],
    [health.stale_links, t("stale links (90d+)")],
  ];
  for (const [value, label] of stats) {
    const stat = el("div", "healthstat");
    stat.appendChild(el("b", null, String(value)));
    stat.appendChild(el("span", null, label));
    healthRow.appendChild(stat);
  }
  const hubs = $("d-hubs");
  hubs.innerHTML = "";
  if (health.top_hubs.length) {
    hubs.appendChild(el("span", "sm", t("Strongest hubs:")));
    for (const hub of health.top_hubs) {
      const row = el("div", "toprow");
      row.appendChild(el("span", "cnt", hub.degree + "⛓"));
      row.appendChild(el("span", "sm", hub.summary));
      hubs.appendChild(row);
    }
  }
}

/* ---------- agents ---------- */
async function refreshTeams() {
  const box = $("teams-list");
  try {
    const [teamsData, agentsData] = await Promise.all([api("/api/teams"), api("/api/agents"), fetchPeerStatus()]);
    const allAgents = agentsData.agents.map((a) => a.id);
    // Agent id is IDENTITY, node/peer name is DISPLAY — they need not match.
    // Correlate member chips to peer status through the registry's
    // display_name too, so a member whose node is named differently from its
    // agent id still gets a connection dot.
    const displayNames = {};
    for (const a of agentsData.agents) displayNames[a.id] = a.display_name || "";
    box.innerHTML = "";
    if (!teamsData.teams.length) {
      const empty = el("div", "empty");
      empty.appendChild(el("div", "big", "\u{1F465}"));
      empty.appendChild(document.createTextNode(t("No teams yet. Create one above.")));
      box.appendChild(empty);
      return;
    }
    for (const team of teamsData.teams) box.appendChild(renderTeam(team, allAgents, displayNames));
  } catch (e) { /* pre-auth */ }
}

function chipRemove(label, onRemove, statusEntry) {
  const chip = el("span", "tag");
  if (statusEntry) chip.appendChild(statusDot(statusEntry));
  chip.appendChild(document.createTextNode(label));
  const x = el("button", null, "×");
  x.style.cssText = "margin-left:6px;border:none;background:none;cursor:pointer;color:var(--muted);font-size:14px";
  x.addEventListener("click", onRemove);
  chip.appendChild(x);
  return chip;
}

function memberPicker(candidates, onAdd) {
  // Free-text input + datalist rather than a closed <select>: known agents
  // are suggested, but any agent id can be typed — on multi-machine meshes
  // a member may not be registered locally yet (it converges over sync).
  const row = el("div", "row"); row.style.cssText = "margin-top:6px;gap:6px";
  const listId = "agents-dl-" + Math.random().toString(36).slice(2, 8);
  const input = el("input");
  input.placeholder = t("select node…");
  input.setAttribute("list", listId);
  input.style.cssText = "font-size:12px;padding:3px 8px;min-width:180px";
  const dl = document.createElement("datalist"); dl.id = listId;
  for (const id of candidates) dl.appendChild(Object.assign(document.createElement("option"), { value: id }));
  const btn = el("button", "ghost", t("+ Add")); btn.style.cssText = "font-size:11px;padding:2px 10px";
  const submit = () => { const v = input.value.trim(); if (v) onAdd(v); };
  btn.addEventListener("click", submit);
  input.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); submit(); } });
  row.appendChild(input); row.appendChild(dl); row.appendChild(btn);
  return row;
}

function renderTeam(team, allAgents, displayNames) {
  const panel = el("div", "panel"); panel.style.marginBottom = "12px";
  const head = el("div", "top");
  const title = el("span", "owner"); title.appendChild(el("b", null, "\u{1F465} " + team.id));
  if (team.name && team.name !== team.id) title.appendChild(document.createTextNode(" · " + team.name));
  head.appendChild(title);
  const ren = el("button", "ghost", t("rename team"));
  ren.style.cssText = "font-size:11px;padding:2px 10px;margin-right:6px";
  ren.addEventListener("click", async () => {
    // A team id is the token inside every team:<id> grant, the parent key of
    // its projects, and the source.team_id the legacy bare grant resolves
    // through. Show what travels with it before renaming, never after.
    const next = (prompt(t("New team id") + ":", team.id) || "").trim();
    if (!next || next === team.id) return;
    try {
      const pre = await api("/api/teams/" + encodeURIComponent(team.id)
        + "/rename-preview?new_id=" + encodeURIComponent(next));
      if (pre.target_exists) { toast(t("That team id already exists.") + " " + next, "err"); return; }
      const grants = pre.explicit_grants + pre.bare_grants;
      let msg = team.id + " \u2192 " + next + "\n\n" + t("This moves everything scoped to the id:") + "\n"
        + "\u2022 " + pre.members + " " + t("member(s)") + "\n"
        + "\u2022 " + pre.projects.length + " " + t("project(s)")
        + (pre.projects.length ? " (" + pre.projects.join(", ") + ")" : "") + "\n"
        + "\u2022 " + grants + " " + t("memory visibility grant(s)")
        + (pre.archived_grants ? " + " + pre.archived_grants + " " + t("archived") : "");
      if (pre.content_mentions) {
        msg += "\n\n" + t("Memory text that mentions the old id is history and is left unchanged.");
      }
      if (pre.sync_peers && pre.sync_peers.length) {
        msg += "\n\n\u26A0 " + t("A rename is local state: it does not propagate as a deletion, so peers may keep the old team id as an inert orphan.");
      }
      if (!confirm(msg)) return;
      await api("/api/teams/" + encodeURIComponent(team.id) + "/rename", {
        method: "POST", headers: { "content-type": "application/json" },
        body: JSON.stringify({ new_id: next }),
      });
      toast(t("Renamed team") + ": " + team.id + " \u2192 " + next, "ok");
      refreshTeams();
    } catch (e) { toast(e.message, "err"); }
  });
  head.appendChild(ren);
  const del = el("button", "ghost", t("delete team")); del.style.cssText = "font-size:11px;padding:2px 10px";
  del.addEventListener("click", async () => {
    if (!confirm(t("Delete team?") + " " + team.id)) return;
    try { await api("/api/teams/" + encodeURIComponent(team.id), { method: "DELETE" }); refreshTeams(); }
    catch (e) { toast(e.message, "err"); }
  });
  head.appendChild(del); panel.appendChild(head);

  panel.appendChild(el("div", "sm", t("Members")));
  const mchips = el("div", "tags");
  for (const m of team.members) mchips.appendChild(chipRemove(m, async () => {
    await api("/api/teams/" + encodeURIComponent(team.id) + "/members?agent_id=" + encodeURIComponent(m), { method: "DELETE" }); refreshTeams(); warnOrphans();
  }, statusFor(m, (displayNames || {})[m])));
  if (!team.members.length) mchips.appendChild(el("span", "sm", t("no members")));
  panel.appendChild(mchips);
  panel.appendChild(memberPicker(allAgents.filter((a) => !team.members.includes(a)), async (id) => {
    try { await api("/api/teams/" + encodeURIComponent(team.id) + "/members", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ agent_id: id }) }); refreshTeams(); }
    catch (e) { toast(e.message, "err"); }
  }));

  const projWrap = el("div"); projWrap.style.cssText = "margin-top:12px;padding-left:12px;border-left:2px solid var(--border,#2a2f45)";
  projWrap.appendChild(el("div", "sm", t("Projects (members chosen from the team)")));
  const projList = el("div"); projWrap.appendChild(projList);
  const cp = el("div", "row"); cp.style.cssText = "margin-top:8px;gap:6px";
  const pid = el("input"); pid.placeholder = t("project id");
  const pname = el("input"); pname.placeholder = t("name (optional)"); pname.style.maxWidth = "150px";
  const cpBtn = el("button", "ghost", t("+ Project")); cpBtn.style.cssText = "font-size:11px;padding:2px 10px";
  cpBtn.addEventListener("click", async () => {
    if (!pid.value.trim()) return;
    try { await api("/api/projects", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: pid.value.trim(), team_id: team.id, name: pname.value.trim() }) }); refreshTeams(); }
    catch (e) { toast(e.message, "err"); }
  });
  cp.appendChild(pid); cp.appendChild(pname); cp.appendChild(cpBtn);
  projWrap.appendChild(cp); panel.appendChild(projWrap);
  loadProjects(team, projList);
  return panel;
}

async function loadProjects(team, projList) {
  let projects = [];
  try { projects = (await api("/api/projects?team=" + encodeURIComponent(team.id))).projects; } catch (e) { return; }
  for (const proj of projects) {
    const pbox = el("div"); pbox.style.margin = "8px 0";
    const ph = el("div", "sm"); ph.appendChild(el("b", null, "\u{1F4C1} " + proj.id));
    if (proj.name && proj.name !== proj.id) ph.appendChild(document.createTextNode(" · " + proj.name));
    const pdel = el("button", null, "×"); pdel.style.cssText = "margin-left:6px;border:none;background:none;cursor:pointer;color:var(--muted);font-size:14px";
    pdel.addEventListener("click", async () => { await api("/api/projects/" + encodeURIComponent(proj.id), { method: "DELETE" }); refreshTeams(); });
    ph.appendChild(pdel); pbox.appendChild(ph);
    const pchips = el("div", "tags");
    for (const m of proj.members) pchips.appendChild(chipRemove(m, async () => {
      await api("/api/projects/" + encodeURIComponent(proj.id) + "/members?agent_id=" + encodeURIComponent(m), { method: "DELETE" }); refreshTeams(); warnOrphans();
    }));
    if (!proj.members.length) pchips.appendChild(el("span", "sm", t("no members")));
    pbox.appendChild(pchips);
    pbox.appendChild(memberPicker(team.members.filter((a) => !proj.members.includes(a)), async (id) => {
      try { await api("/api/projects/" + encodeURIComponent(proj.id) + "/members", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ agent_id: id }) }); refreshTeams(); }
      catch (e) { toast(e.message, "err"); }
    }));
    projList.appendChild(pbox);
  }
}

async function refreshAgents() {
  const list = $("agents-list");
  try {
    const [data] = await Promise.all([api("/api/agents"), fetchPeerStatus()]);
    if (!remoteTarget) populateActingAs(data.agents);
    list.innerHTML = "";
    if (!data.agents.length) {
      const empty = el("div", "empty");
      empty.appendChild(el("div", "big", "🤖"));
      empty.appendChild(document.createTextNode(t("No agents registered yet.")));
      list.appendChild(empty);
      return;
    }
    for (const agent of data.agents) {
      const card = el("article", "card");
      const top = el("div", "top");
      top.appendChild(el("span", "badge kind-" + agent.kind, agent.kind));
      const name = el("span", "owner");
      // Only remote/peer identities carry a connection dot; a purely local
      // agent has no peer to probe, so it stays dotless (no false "offline").
      const agentStatus = statusFor(agent.id, agent.display_name);
      if (agentStatus) name.appendChild(statusDot(agentStatus));
      name.appendChild(el("b", null, agent.id));
      if (agent.display_name) name.appendChild(document.createTextNode(" · " + agent.display_name));
      top.appendChild(name);
      const meta = el("span", "scorewrap");
      meta.appendChild(el("span", "scoreval", agent.memory_count + " " + t("memories")));
      top.appendChild(meta);
      card.appendChild(top);
      const teams = el("div", "meta");
      const chips = el("span", "tags");
      for (const team of agent.teams) chips.appendChild(el("span", "tag", "team:" + team));
      if (!agent.teams.length) chips.appendChild(el("span", "sm", t("no teams")));
      teams.appendChild(chips);
      teams.appendChild(el("span", null, agent.last_seen_at ? new Date(agent.last_seen_at).toLocaleString() : t("never seen")));
      card.appendChild(teams);
      const actions = el("div", "actions");
      const actBtn = el("button", null, t("👤 Act as"));
      actBtn.addEventListener("click", () => { $("acting-as").value = agent.id; localStorage.setItem("amos.actingAs", agent.id); toast("Acting as " + agent.id, "ok"); });
      const editBtn = el("button", null, t("✎ Edit"));
      editBtn.addEventListener("click", () => {
        $("ag-id").value = agent.id; $("ag-name").value = agent.display_name;
        $("ag-kind").value = agent.kind; $("ag-teams").value = agent.teams.join(", ");
        window.scrollTo({ top: 0, behavior: "smooth" });
      });
      const removeBtn = el("button", "danger", t("🗑 Remove"));
      removeBtn.addEventListener("click", async () => {
        if (!confirm("Unregister agent “" + agent.id + "”? Its memories stay; it loses registered team access.")) return;
        try { await api("/api/agents/" + encodeURIComponent(agent.id), { method: "DELETE" }); refreshAgents(); }
        catch (e) { toast(e.message, "err"); }
      });
      actions.append(actBtn, editBtn, removeBtn);
      card.appendChild(actions);
      list.appendChild(card);
    }
  } catch (e) { /* pre-auth */ }
}
async function warnOrphans() {
  try {
    const scan = await api("/api/maintenance/scan");
    if (scan.orphan_memories > 0)
      toast(scan.orphan_memories + " " + t("memories are now orphaned — clean them in Tools \u2192 Maintenance."), "err");
  } catch (e) { /* ignore */ }
}
async function maint(path, method, label) {
  const out = $("maint-out");
  out.textContent = t("Working\u2026");
  try { const r = await api(path, method ? { method: method } : undefined); out.textContent = label + ": " + JSON.stringify(r); }
  catch (e) { out.textContent = e.message; }
}
$("btn-maint-scan").addEventListener("click", () => maint("/api/maintenance/scan", null, t("Health")));
$("btn-maint-orphans").addEventListener("click", () => { if (confirm(t("Delete all orphan memories?"))) maint("/api/maintenance/orphans/delete?confirm=orphans", "POST", t("Orphans")); });
$("btn-maint-reindex").addEventListener("click", () => maint("/api/maintenance/reindex", "POST", t("Reindex")));
$("btn-maint-vacuum").addEventListener("click", () => maint("/api/maintenance/vacuum", "POST", t("Vacuum")));
$("btn-maint-update").addEventListener("click", checkForUpdates);
$("btn-node-rename").addEventListener("click", async () => {
  const v = $("node-name-input").value.trim();
  if (!v) return;
  try {
    const r = await api("/api/node", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ node_name: v }) });
    toast("node → " + r.node_name);
  } catch (e) { toast(e.message, "err"); }
});
$("btn-audit-refresh").addEventListener("click", loadAudit);
$("btn-owners-refresh").addEventListener("click", loadOwners);

/* ---------- log viewer ---------- */
async function loadLogs() {
  const view = $("log-view"), meta = $("log-meta"), select = $("log-file");
  const params = new URLSearchParams({ lines: $("log-lines").value });
  if (select.value) params.set("file", select.value);
  const query = $("log-q").value.trim();
  if (query) params.set("q", query);
  let data;
  try { data = await api("/api/logs?" + params.toString()); }
  catch (e) { view.textContent = String(e.message || e); return; }
  const current = select.value;
  select.innerHTML = "";
  for (const name of data.files)
    select.appendChild(Object.assign(document.createElement("option"),
      { value: name, textContent: name }));
  if (data.files.includes(current)) select.value = current;
  else if (data.file) select.value = data.file;
  if (!data.files.length) {
    view.textContent = t("No log files found.");
    meta.textContent = "";
    return;
  }
  view.textContent = data.lines.length ? data.lines.join("\n") : t("(no matching lines)");
  view.scrollTop = view.scrollHeight;  // newest lines at the bottom
  let info = data.lines.length + " " + t("lines shown");
  if (query) info += " · " + data.matched + " " + t("matching in the recent window");
  if (data.truncated) info += " · " + t("older log content beyond the 2 MB window is not searched");
  meta.textContent = info;
}
$("btn-log-refresh").addEventListener("click", loadLogs);
$("log-file").addEventListener("change", loadLogs);
$("log-lines").addEventListener("change", loadLogs);
$("log-q").addEventListener("keydown", (e) => { if (e.key === "Enter") loadLogs(); });

/* ---------- fleet console ---------- */
async function loadFleet() {
  const card = $("fleet-console-card"), list = $("fleet-nodes"), drift = $("fleet-drift");
  card.textContent = t("Working…"); list.innerHTML = ""; drift.textContent = "";
  let data;
  try { data = await api("/api/fleet/status"); }
  catch (e) { card.textContent = String(e.message || e); return; }
  card.innerHTML = "";
  if (!data.configured) {
    card.appendChild(el("div", "sm", t("This node has no fleet key — it is a managed node, not a console. To make it the console, run:")));
    const code = el("code", null, "agent-memory fleet keygen");
    code.style.cssText = "display:inline-block;margin-top:4px";
    card.appendChild(code);
    return;
  }
  const c = data.console;
  const line = el("div", "sm");
  line.appendChild(el("b", null, c.node_name));
  line.appendChild(document.createTextNode(
    " · v" + c.version + " · " + c.memories + " " + t("memories") +
    " · " + (c.owners ? c.owners.length : 0) + " " + t("owners") +
    " · " + t("key") + " " + c.key_id));
  card.appendChild(line);
  if (data.version_drift)
    drift.textContent = t("⚠ version drift across the fleet:") + " " + data.versions.join(", ");
  if (!data.nodes.length) {
    list.appendChild(el("span", "sm", t("No managed nodes — register peers (join / peers add), then run fleet grant on each.")));
    return;
  }
  for (const n of data.nodes) {
    const row = el("div", "toprow");
    const label = el("span", "sm");
    const entry = !n.reachable
      ? { state: "down", title: t("disconnected") + (n.detail ? " · " + n.detail : "") }
      : (!n.authorized
        ? { state: "warn", title: t("reachable but not granted") + (n.detail ? " · " + n.detail : "") }
        : { state: "ok", title: t("connected") + " · v" + n.version });
    label.appendChild(statusDot(entry));
    label.appendChild(el("b", null, n.name || n.node_name || n.url));
    let extra = " · " + n.url;
    if (n.authorized) {
      extra += " · v" + n.version + " · " + n.memories + " " + t("memories") +
        " · " + (n.owners ? n.owners.length : 0) + " " + t("owners");
    } else if (n.reachable) {
      extra += " · " + t("not granted — run fleet grant on this node");
    }
    label.appendChild(document.createTextNode(extra));
    row.appendChild(label);
    const actions = el("span");
    actions.style.cssText = "display:flex;gap:6px;flex:0 0 auto";
    if (n.authorized) {
      const browseBtn = el("button", "ghost", t("Browse"));
      browseBtn.style.fontSize = "11px";
      browseBtn.addEventListener("click", () => fleetBrowseOpen(n));
      actions.appendChild(browseBtn);
    }
    const syncBtn = el("button", "ghost", t("Sync"));
    syncBtn.style.fontSize = "11px";
    syncBtn.addEventListener("click", () => fleetTrigger("sync", n.url));
    const updBtn = el("button", "ghost", t("Update"));
    updBtn.style.fontSize = "11px";
    updBtn.addEventListener("click", () => {
      if (confirm(t("Update this node? It will upgrade itself and restart."))) fleetTrigger("update", n.url);
    });
    actions.append(syncBtn, updBtn);
    row.appendChild(actions);
    list.appendChild(row);
  }
}

/* ---------- fleet remote browse (read-private) ---------- */
const fleetBrowseState = { url: "", name: "", offset: 0 };
function fleetBrowseOpen(node) {
  fleetBrowseState.url = node.url;
  fleetBrowseState.name = node.name || node.node_name || node.url;
  fleetBrowseState.offset = 0;
  $("fleet-browse-owner").value = "";
  $("fleet-browse-title").textContent = t("Remote memories") + " — " + fleetBrowseState.name;
  $("fleet-browse").style.display = "";
  $("fleet-browse-list").innerHTML = "";
  fleetBrowseLoad();
}
async function fleetBrowseLoad() {
  const list = $("fleet-browse-list");
  const params = new URLSearchParams({
    url: fleetBrowseState.url, limit: "20",
    offset: String(fleetBrowseState.offset),
  });
  const owner = $("fleet-browse-owner").value.trim();
  if (owner) params.set("owner", owner);
  let data;
  try { data = await api("/api/fleet/browse?" + params.toString()); }
  catch (e) {
    list.appendChild(el("div", "sm",
      /read-private/.test(e.message)
        ? t("This node has not granted read-private to this console — run fleet grant with --caps manage,read-private on it.")
        : String(e.message)));
    return;
  }
  const memories = data.memories || [];
  if (!memories.length && fleetBrowseState.offset === 0) {
    list.appendChild(el("div", "sm", t("No memories on this node.")));
    return;
  }
  for (const m of memories) {
    const row = el("div", "topitem");
    const left = el("span");
    const meta = el("div", "sm");
    meta.appendChild(el("b", null, m.owner || "?"));
    const bits = [m.type || "", m.scope || "",
      (m.visibility && m.visibility.length === 0) ? t("🔒 private") : "",
      (m.updated_at || m.created_at || "").slice(0, 16).replace("T", " ")];
    meta.appendChild(document.createTextNode(" · " + bits.filter(Boolean).join(" · ")));
    left.appendChild(meta);
    const body = el("div", "sm", m.content || "");
    body.style.cssText = "white-space:pre-wrap;color:var(--text);margin-top:2px";
    left.appendChild(body);
    row.appendChild(left);
    list.appendChild(row);
  }
  fleetBrowseState.offset += memories.length;
  $("btn-fleet-browse-more").disabled = memories.length < 20;
}
$("btn-fleet-browse-apply").addEventListener("click", () => {
  fleetBrowseState.offset = 0; $("fleet-browse-list").innerHTML = ""; fleetBrowseLoad();
});
$("btn-fleet-browse-more").addEventListener("click", fleetBrowseLoad);
$("btn-fleet-browse-close").addEventListener("click", () => {
  $("fleet-browse").style.display = "none";
});
async function fleetTrigger(action, url) {
  const out = $("fleet-out");
  out.textContent = t("Working…");
  try {
    const r = await api("/api/fleet/trigger", { method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ action: action, url: url || "" }) });
    out.textContent = r.results.map((x) =>
      (x.name || x.url) + ": " + (x.ok ? "ok" : "HTTP " + x.status)).join(" · ")
      || t("no managed nodes");
    loadFleet();
  } catch (e) { out.textContent = e.message; }
}
$("btn-fleet-refresh").addEventListener("click", loadFleet);
$("btn-fleet-sync-all").addEventListener("click", () => fleetTrigger("sync", ""));
$("btn-fleet-update-all").addEventListener("click", () => {
  if (confirm(t("Update ALL nodes? Each will upgrade itself and restart."))) fleetTrigger("update", "");
});
$("graph-filter").addEventListener("change", (e) => { graphFilter = e.target.value; loadGraph(); });
$("btn-team-create").addEventListener("click", async () => {
  const id = $("tm-id").value.trim(); if (!id) return;
  try { await api("/api/teams", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ id: id, name: $("tm-name").value.trim() }) }); $("tm-id").value = ""; $("tm-name").value = ""; refreshTeams(); }
  catch (e) { toast(e.message, "err"); }
});
$("btn-agent-save").addEventListener("click", async () => {
  const id = $("ag-id").value.trim();
  if (!id) { toast("Agent id is required.", "err"); return; }
  try {
    // Only send teams when the field has content; an empty field leaves team
    // membership untouched (it is managed in the Teams tab).
    const teamsRaw = $("ag-teams").value.trim();
    const payload = { id: id, display_name: $("ag-name").value.trim(), kind: $("ag-kind").value };
    if (teamsRaw) payload.teams = teamsRaw.split(",").map(t => t.trim()).filter(Boolean);
    await api("/api/agents", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify(payload) });
    toast("Agent saved.", "ok");
    $("ag-id").value = ""; $("ag-name").value = ""; $("ag-teams").value = "";
    refreshAgents();
  } catch (e) { toast(e.message, "err"); }
});
refreshAgents().then(syncRemoteTarget);

/* ---------- memory cards ---------- */
function gauge(label, value) {
  const wrap = el("span", "gauge");
  wrap.appendChild(el("span", null, label));
  const bar = el("span", "dotbar");
  const fill = el("i");
  fill.style.width = Math.round(value * 100) + "%";
  bar.appendChild(fill);
  wrap.appendChild(bar);
  wrap.appendChild(el("span", null, value.toFixed(2)));
  return wrap;
}

function renderCard(memory, extras) {
  const card = el("article", "card");
  const top = el("div", "top");
  top.appendChild(el("span", "badge scope-" + memory.scope, memory.scope));
  top.appendChild(el("span", "badge type", memory.type));
  const owner = el("span", "owner");
  owner.appendChild(document.createTextNode("by "));
  owner.appendChild(el("b", null, memory.owner));
  top.appendChild(owner);
  if (memory.pinned) top.appendChild(el("span", "pin", "📌"));
  if (!memory.visibility || memory.visibility.length === 0) {
    top.appendChild(el("span", "owner", t("🔒 private")));
  }
  if (extras && typeof extras.score === "number") {
    const wrap = el("span", "scorewrap");
    const bar = el("span", "scorebar");
    const fill = el("i");
    fill.style.width = Math.max(4, Math.round((extras.score / extras.maxScore) * 100)) + "%";
    bar.appendChild(fill);
    wrap.appendChild(bar);
    wrap.appendChild(el("span", "scoreval", extras.score.toFixed(3)));
    top.appendChild(wrap);
  }
  card.appendChild(top);
  card.appendChild(el("div", "content", memory.content));

  const meta = el("div", "meta");
  if (memory.tags && memory.tags.length) {
    const tags = el("span", "tags");
    memory.tags.slice(0, 6).forEach((t) => tags.appendChild(el("span", "tag", t)));
    meta.appendChild(tags);
  }
  meta.appendChild(gauge("imp", memory.importance));
  meta.appendChild(gauge("conf", memory.confidence));
  meta.appendChild(el("span", null, "updated " + new Date(memory.updated_at).toLocaleString()));
  if (memory.expires_at) meta.appendChild(el("span", null, "expires " + new Date(memory.expires_at).toLocaleString()));
  card.appendChild(meta);

  const actions = el("div", "actions");
  const editBtn = el("button", null, t("✎ Edit"));
  editBtn.addEventListener("click", () => enterEditMode(card, memory));
  const helpfulBtn = el("button", null, t("👍 Helpful"));
  helpfulBtn.addEventListener("click", () => feedback(memory.id, true));
  const misleadingBtn = el("button", null, t("👎 Misleading"));
  misleadingBtn.addEventListener("click", () => feedback(memory.id, false));
  const linksBtn = el("button", null, t("🔗 Links"));
  const shareBtn = el("button", null, t("⇢ Share"));
  shareBtn.addEventListener("click", async () => {
    const actor = actingAs() || memory.owner;
    const target = prompt(
      "Share “" + memory.content.slice(0, 60) + "”\n\n" +
      "Grant access to (agent id, or team:<id>; prefix with ~ to share a de-identified copy):\n" +
      "Acting as owner: " + actor
    );
    if (!target) return;
    const deidentify = target.startsWith("~");
    const cleaned = deidentify ? target.slice(1).trim() : target.trim();
    const body = { actor: actor, deidentify: deidentify };
    if (cleaned.startsWith("team:")) body.to_team = cleaned.slice(5); else body.to_agent = cleaned;
    try {
      const result = await api("/api/memories/" + memory.id + "/share", {
        method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body),
      });
      toast(result.deidentified
        ? "De-identified copy shared as " + result.shared_as
        : "Shared with " + result.grant + " (audited).", "ok");
    } catch (e) { toast(e.message, "err"); }
  });
  const copyBtn = el("button", null, t("⧉ Copy id"));
  copyBtn.addEventListener("click", () => { navigator.clipboard.writeText(memory.id); toast("Copied " + memory.id, "ok"); });
  const deleteBtn = el("button", "danger", t("🗑 Delete"));
  deleteBtn.addEventListener("click", async () => {
    if (!confirm("Delete this memory permanently?\n\n" + memory.content.slice(0, 120))) return;
    try {
      await api("/api/memories/" + memory.id, { method: "DELETE" });
      card.remove(); loadStats(); toast("Memory deleted", "ok");
    } catch (e) { toast(e.message, "err"); }
  });
  actions.append(editBtn, helpfulBtn, misleadingBtn, linksBtn, shareBtn, copyBtn, deleteBtn);
  if (extras && extras.reason) {
    const whyBtn = el("button", null, t("why?"));
    const reason = el("div", "reason", extras.reason);
    whyBtn.addEventListener("click", () => { reason.style.display = reason.style.display === "block" ? "none" : "block"; });
    actions.appendChild(whyBtn);
    card.appendChild(actions);
    card.appendChild(reason);
  } else {
    card.appendChild(actions);
  }

  const linksBox = el("div", "linksbox");
  linksBtn.addEventListener("click", async () => {
    if (linksBox.style.display === "block") { linksBox.style.display = "none"; return; }
    linksBox.textContent = "Loading…"; linksBox.style.display = "block";
    try {
      const rq = actingAs() ? "?requester_agent_id=" + encodeURIComponent(actingAs()) : "";
      const data = await api("/api/memories/" + memory.id + "/links" + rq);
      linksBox.textContent = "";
      if (!data.links.length) { linksBox.textContent = "No links yet."; return; }
      for (const link of data.links) {
        const other = link.src_id === memory.id ? link.dst_id : link.src_id;
        const row = el("div", "linkrow");
        row.appendChild(el("span", "rel", link.relation));
        const detail = await api("/api/memories/" + other + rq).catch(() => null);
        row.appendChild(el("span", null, detail ? detail.content.slice(0, 80) : other));
        row.appendChild(el("span", null, "w=" + link.weight.toFixed(2)));
        linksBox.appendChild(row);
      }
    } catch (e) { linksBox.textContent = e.message; }
  });
  card.appendChild(linksBox);
  return card;
}

function enterEditMode(card, memory) {
  const form = el("div", "editform");
  const contentInput = el("textarea");
  contentInput.value = memory.content;
  form.appendChild(contentInput);

  const row1 = el("div", "erow");
  const scopeSelect = el("select");
  for (const scope of ["user", "agent", "project", "team", "global"]) {
    const option = el("option", null, scope);
    if (scope === memory.scope) option.selected = true;
    scopeSelect.appendChild(option);
  }
  const typeSelect = el("select");
  for (const type of ["note", "preference", "fact", "procedure", "environment", "decision", "warning"]) {
    const option = el("option", null, type);
    if (type === memory.type) option.selected = true;
    typeSelect.appendChild(option);
  }
  row1.append(scopeSelect, typeSelect);
  form.appendChild(row1);

  const tagsInput = el("input");
  tagsInput.type = "text"; tagsInput.placeholder = "tags (comma separated)";
  tagsInput.value = (memory.tags || []).join(", ");
  form.appendChild(tagsInput);

  const visibilityInput = el("input");
  visibilityInput.type = "text"; visibilityInput.placeholder = "visibility (empty = owner only)";
  visibilityInput.value = (memory.visibility || []).join(", ");
  form.appendChild(visibilityInput);

  const row2 = el("div", "erow");
  const importanceWrap = el("label", null, "imp ");
  const importanceInput = el("input"); importanceInput.type = "range";
  importanceInput.min = "0"; importanceInput.max = "1"; importanceInput.step = "0.05";
  importanceInput.value = String(memory.importance);
  importanceInput.style.accentColor = "var(--accent)";
  importanceWrap.appendChild(importanceInput);
  const confidenceWrap = el("label", null, "conf ");
  const confidenceInput = el("input"); confidenceInput.type = "range";
  confidenceInput.min = "0"; confidenceInput.max = "1"; confidenceInput.step = "0.05";
  confidenceInput.value = String(memory.confidence);
  confidenceInput.style.accentColor = "var(--accent)";
  confidenceWrap.appendChild(confidenceInput);
  const pinnedWrap = el("label", null, " 📌 pinned ");
  const pinnedInput = el("input"); pinnedInput.type = "checkbox"; pinnedInput.checked = memory.pinned;
  pinnedWrap.appendChild(pinnedInput);
  row2.append(importanceWrap, confidenceWrap, pinnedWrap);
  form.appendChild(row2);

  const row3 = el("div", "erow");
  const saveBtn = el("button", "primary", t("Save"));
  saveBtn.style.padding = "8px 18px";
  const cancelBtn = el("button", "ghost", t("Cancel"));
  row3.append(saveBtn, cancelBtn);
  form.appendChild(row3);

  saveBtn.addEventListener("click", async () => {
    try {
      const updated = await api("/api/memories/" + memory.id, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          content: contentInput.value,
          scope: scopeSelect.value,
          type: typeSelect.value,
          tags: tagsInput.value.split(",").map((t) => t.trim()).filter(Boolean),
          visibility: visibilityInput.value.split(",").map((v) => v.trim()).filter(Boolean),
          importance: Number(importanceInput.value),
          confidence: Number(confidenceInput.value),
          pinned: pinnedInput.checked,
        }),
      });
      card.replaceWith(renderCard(updated, null));
      toast("Memory updated", "ok");
    } catch (e) { toast(e.message, "err"); }
  });
  cancelBtn.addEventListener("click", () => card.replaceWith(renderCard(memory, null)));

  card.innerHTML = "";
  card.appendChild(form);
}

async function feedback(memoryId, helpful) {
  try {
    const body = { memory_ids: [memoryId], helpful: helpful };
    if (actingAs()) body.requester_agent_id = actingAs();
    await api("/api/recall", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(body) });
    toast(helpful ? "Reinforced — will surface more readily." : "Weakened — confidence and links reduced.", "ok");
  } catch (e) { toast(e.message, "err"); }
}

/* ---------- search ---------- */
async function runSearch() {
  const query = $("q").value.trim();
  if (!query) return;
  const container = $("search-results");
  container.innerHTML = ""; container.appendChild(el("div", "empty", t("Searching…")));
  const params = new URLSearchParams({ q: query, limit: "20" });
  if (actingAs()) params.set("requester_agent_id", actingAs());
  try {
    const data = await api("/api/search?" + params);
    container.innerHTML = "";
    if (!data.results.length) {
      const empty = el("div", "empty");
      empty.appendChild(el("div", "big", "∅"));
      empty.appendChild(document.createTextNode(t("Nothing recalled for that query") + (actingAs() ? " — " + actingAs() : "") + "."));
      container.appendChild(empty);
      return;
    }
    const maxScore = Math.max(...data.results.map((r) => r.score), 0.0001);
    for (const result of data.results) {
      container.appendChild(renderCard(result, { score: result.score, maxScore: maxScore, reason: result.reason }));
    }
  } catch (e) { container.innerHTML = ""; toast(e.message, "err"); }
}
$("btn-search").addEventListener("click", runSearch);
$("q").addEventListener("keydown", (event) => { if (event.key === "Enter") runSearch(); });

/* ---------- browse ---------- */
let browseLoaded = false;
let browseOffset = 0;
async function refreshBrowse(more) {
  browseLoaded = true;
  if (!more) { browseOffset = 0; $("browse-results").innerHTML = ""; }
  const params = new URLSearchParams({ limit: "20", offset: String(browseOffset) });
  if (actingAs()) params.set("requester_agent_id", actingAs());
  if ($("filter-scope").value) params.set("scope", $("filter-scope").value);
  if ($("filter-type").value) params.set("type", $("filter-type").value);
  if ($("filter-owner").value.trim()) params.set("owner", $("filter-owner").value.trim());
  try {
    const data = await api("/api/memories?" + params);
    const container = $("browse-results");
    if (!data.memories.length && browseOffset === 0) {
      const empty = el("div", "empty");
      empty.appendChild(el("div", "big", "☁"));
      empty.appendChild(document.createTextNode(t("No memories yet. Add the first one.")));
      container.appendChild(empty);
    }
    for (const memory of data.memories) container.appendChild(renderCard(memory, null));
    browseOffset += data.memories.length;
    $("btn-more").style.display = data.memories.length < 20 ? "none" : "inline-block";
  } catch (e) { toast(e.message, "err"); }
}
$("btn-more").addEventListener("click", () => refreshBrowse(true));
$("btn-filter").addEventListener("click", () => refreshBrowse(false));

/* ---------- association graph ---------- */
const SCOPE_COLORS = {
  user: "#4d7fe8", agent: "#22a58c", project: "#c07f1f", team: "#d9558f", global: "#3aa653",
};
let graphFilter = "";
let graphState = null;

async function loadGraph() {
  const params = new URLSearchParams({ limit: "300" });
  if (actingAs()) params.set("requester_agent_id", actingAs());
  let data;
  try { data = await api("/api/graph?" + params); }
  catch (e) { toast(e.message, "err"); return; }

  const canvas = $("graph-canvas");
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.clientWidth, height = 540;
  canvas.width = width * dpr; canvas.height = height * dpr;
  const context = canvas.getContext("2d");
  context.setTransform(dpr, 0, 0, dpr, 0, 0);

  const legend = $("graph-legend");
  legend.innerHTML = "";
  for (const [scope, color] of Object.entries(SCOPE_COLORS)) {
    const key = el("span", "key");
    const dot = el("span", "dot"); dot.style.background = color;
    key.appendChild(dot); key.appendChild(el("span", null, scope));
    legend.appendChild(key);
  }

  if (data && Array.isArray(data.nodes)) {
    const sel = $("graph-filter");
    const scopes = Array.from(new Set(data.nodes.map(n => n.scope).filter(Boolean))).sort();
    const cur = sel.value;
    sel.innerHTML = '<option value="">' + t("All") + '</option>';
    for (const sc of scopes) { const o = document.createElement("option"); o.value = sc; o.textContent = sc; sel.appendChild(o); }
    sel.value = graphFilter && scopes.includes(graphFilter) ? graphFilter : "";
    if (graphFilter) data = Object.assign({}, data, { nodes: data.nodes.filter(n => n.scope === graphFilter) });
  }
  if (!data || !Array.isArray(data.nodes)) { toast(t("Graph unavailable."), "err"); return; }
  if (!data.nodes.length) {
    context.clearRect(0, 0, width, height);
    context.fillStyle = getComputedStyle(document.body).getPropertyValue("color");
    context.globalAlpha = 0.5; context.font = "14px sans-serif"; context.textAlign = "center";
    context.fillText("No visible links yet — link memories or let co-recall build them.", width / 2, height / 2);
    context.globalAlpha = 1;
    graphState = null;
    return;
  }

  const nodes = data.nodes.map((n, i) => ({
    ...n,
    x: width / 2 + Math.cos(i * 2.399) * (60 + 10 * i % 200),
    y: height / 2 + Math.sin(i * 2.399) * (60 + 7 * i % 160),
    vx: 0, vy: 0, r: 6 + Math.min(10, n.degree * 1.6),
  }));
  const byId = Object.fromEntries(nodes.map((n) => [n.id, n]));
  // Defensive: only keep edges whose endpoints are actually present, so a
  // stray edge can never make stepGraph read .x of undefined and freeze.
  const edges = (data.edges || [])
    .map((e) => ({ ...e, a: byId[e.src], b: byId[e.dst] }))
    .filter((e) => e.a && e.b);
  graphState = { nodes: nodes, edges: edges, ctx: context, w: width, h: height, frame: 0, drag: null, hover: null };
  requestAnimationFrame(stepGraph);
}

function stepGraph() {
  const g = graphState;
  if (!g) return;
  const settled = g.frame > __AMOS_GRAPH_SETTLE_FRAME_THRESHOLD__;
  if (!settled || g.drag) {
    for (let i = 0; i < g.nodes.length; i++) {
      const a = g.nodes[i];
      for (let j = i + 1; j < g.nodes.length; j++) {
        const b = g.nodes[j];
        let dx = a.x - b.x, dy = a.y - b.y;
        let d2 = dx * dx + dy * dy || 1;
        const force = Math.min(1600 / d2, 4);
        const d = Math.sqrt(d2);
        dx /= d; dy /= d;
        a.vx += dx * force; a.vy += dy * force;
        b.vx -= dx * force; b.vy -= dy * force;
      }
      a.vx += (g.w / 2 - a.x) * 0.002;
      a.vy += (g.h / 2 - a.y) * 0.002;
    }
    for (const edge of g.edges) {
      const dx = edge.b.x - edge.a.x, dy = edge.b.y - edge.a.y;
      const d = Math.sqrt(dx * dx + dy * dy) || 1;
      const pull = (d - 110) * 0.004 * (0.4 + edge.weight);
      edge.a.vx += (dx / d) * pull; edge.a.vy += (dy / d) * pull;
      edge.b.vx -= (dx / d) * pull; edge.b.vy -= (dy / d) * pull;
    }
    for (const node of g.nodes) {
      if (g.drag && g.drag.node === node) { node.vx = 0; node.vy = 0; continue; }
      node.vx *= 0.82; node.vy *= 0.82;
      node.x = Math.max(node.r, Math.min(g.w - node.r, node.x + node.vx));
      node.y = Math.max(node.r, Math.min(g.h - node.r, node.y + node.vy));
    }
  }
  drawGraph();
  g.frame += 1;
  requestAnimationFrame(stepGraph);
}

function drawGraph() {
  const g = graphState;
  if (!g) return;
  const context = g.ctx;
  context.clearRect(0, 0, g.w, g.h);
  for (const edge of g.edges) {
    const highlighted = g.hover && (edge.a === g.hover || edge.b === g.hover);
    context.strokeStyle = highlighted ? "#9a7bff" : "rgba(128,136,168,.35)";
    context.lineWidth = 0.6 + edge.weight * 2.4;
    context.setLineDash(edge.relation === "supersedes" ? [5, 4] : []);
    context.beginPath();
    context.moveTo(edge.a.x, edge.a.y);
    context.lineTo(edge.b.x, edge.b.y);
    context.stroke();
  }
  context.setLineDash([]);
  for (const node of g.nodes) {
    context.beginPath();
    context.arc(node.x, node.y, node.r, 0, Math.PI * 2);
    context.fillStyle = SCOPE_COLORS[node.scope] || "#888";
    context.globalAlpha = g.hover && g.hover !== node ? 0.45 : 1;
    context.fill();
    context.globalAlpha = 1;
    if (node.pinned) {
      context.strokeStyle = "#ffffff";
      context.lineWidth = 1.6;
      context.stroke();
    }
  }
}

(function wireGraphPointer() {
  const canvas = $("graph-canvas");
  const tip = $("graph-tip");
  const findNode = (event) => {
    if (!graphState) return null;
    const rect = canvas.getBoundingClientRect();
    const x = event.clientX - rect.left, y = event.clientY - rect.top;
    return graphState.nodes.find((n) => (n.x - x) ** 2 + (n.y - y) ** 2 <= (n.r + 4) ** 2) || null;
  };
  canvas.addEventListener("mousemove", (event) => {
    if (!graphState) return;
    const rect = canvas.getBoundingClientRect();
    if (graphState.drag) {
      graphState.drag.moved = true;
      graphState.drag.node.x = event.clientX - rect.left;
      graphState.drag.node.y = event.clientY - rect.top;
      return;
    }
    const node = findNode(event);
    graphState.hover = node;
    canvas.style.cursor = node ? "pointer" : "grab";
    if (node) {
      tip.style.display = "block";
      tip.style.left = Math.min(node.x + 14, graphState.w - 330) + "px";
      tip.style.top = (node.y + 14) + "px";
      tip.textContent = node.scope + "/" + node.type + " · " + node.degree + " links — " + node.label;
    } else {
      tip.style.display = "none";
    }
  });
  canvas.addEventListener("mousedown", (event) => {
    const node = findNode(event);
    if (node) graphState.drag = { node: node, moved: false };
  });
  canvas.addEventListener("mouseup", (event) => {
    if (!graphState) return;
    if (graphState.drag && !graphState.drag.moved) {
      const node = findNode(event);
      if (node) { navigator.clipboard.writeText(node.id); toast("Copied " + node.id, "ok"); }
    }
    if (graphState.drag) graphState.drag = null;
  });
  canvas.addEventListener("mouseleave", () => {
    if (graphState) { graphState.hover = null; graphState.drag = null; }
    tip.style.display = "none";
  });
})();

/* ---------- add ---------- */
$("f-importance").addEventListener("input", (e) => { $("o-importance").textContent = Number(e.target.value).toFixed(2); });
$("f-confidence").addEventListener("input", (e) => { $("o-confidence").textContent = Number(e.target.value).toFixed(2); });
$("add-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const expiresRaw = $("f-expires").value;
  const payload = {
    content: $("f-content").value,
    owner: $("f-owner").value || "default",
    scope: $("f-scope").value,
    type: $("f-type").value,
    tags: $("f-tags").value.split(",").map((t) => t.trim()).filter(Boolean),
    visibility: $("f-visibility").value.split(",").map((v) => v.trim()).filter(Boolean),
    importance: Number($("f-importance").value),
    confidence: Number($("f-confidence").value),
    pinned: $("f-pinned").checked,
    auto_link: $("f-autolink").checked,
  };
  if (expiresRaw) payload.expires_at = new Date(expiresRaw).toISOString();
  try {
    const saved = await api("/api/memories", { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify(payload) });
    toast("Saved " + saved.id, "ok");
    $("f-content").value = ""; $("f-tags").value = "";
    loadStats(); browseLoaded = false;
  } catch (e) { toast(e.message, "err"); }
});

/* ---------- tools ---------- */
$("btn-pack").addEventListener("click", async () => {
  const query = $("pack-q").value.trim();
  if (!query) return;
  const params = new URLSearchParams({ q: query, max_tokens: $("pack-tokens").value });
  if (actingAs()) params.set("requester_agent_id", actingAs());
  if ($("pack-reinforce").checked) params.set("auto_reinforce", "true");
  const out = $("pack-out");
  out.textContent = "Building…";
  try {
    const data = await api("/api/context-pack?" + params);
    out.innerHTML = "";
    out.appendChild(el("div", null, "")).append(
      Object.assign(el("span", "chip"), { textContent: data.used_tokens + " / " + data.max_tokens + " tokens" })
    );
    out.appendChild(el("pre", "packtext", data.text));
    const decisions = el("div", "decisions");
    for (const decision of data.decisions) {
      const row = el("div", "drow");
      row.appendChild(el("span", decision.selected ? "ok" : "no", decision.selected ? "✓" : "✕"));
      row.appendChild(el("span", null, decision.memory_id));
      row.appendChild(el("span", "no", decision.reason.join(", ")));
      decisions.appendChild(row);
    }
    out.appendChild(decisions);
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
});

$("btn-link").addEventListener("click", async () => {
  try {
    await api("/api/links", {
      method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({
        src_id: $("link-src").value.trim(), dst_id: $("link-dst").value.trim(),
        relation: $("link-rel").value, weight: Number($("link-weight").value),
      }),
    });
    toast("Linked.", "ok"); loadStats();
  } catch (e) { toast(e.message, "err"); }
});

async function refreshArchive() {
  const list = $("archive-list");
  try {
    const data = await api("/api/archive?limit=5");
    list.innerHTML = "";
    if (!data.archived.length) { list.appendChild(el("span", "sm", t("Archive is empty."))); return; }
    for (const item of data.archived) {
      const row = el("div", "toprow");
      row.appendChild(el("span", "cnt", item.archive_reason));
      row.appendChild(el("span", "sm", item.summary));
      const restoreBtn = el("button", "ghost", t("restore"));
      restoreBtn.style.cssText = "font-size:11px;padding:2px 10px;flex:0 0 auto";
      restoreBtn.addEventListener("click", async () => {
        try {
          await api("/api/archive/" + item.id + "/restore", { method: "POST" });
          toast("Restored — expiry cleared, decay clock restarted.", "ok");
          refreshArchive(); loadStats(); loadDashboard(); browseLoaded = false;
        } catch (e) { toast(e.message, "err"); }
      });
      row.appendChild(restoreBtn);
      list.appendChild(row);
    }
  } catch (e) { /* tools tab may load before auth */ }
}

async function runRetention(halfLives) {
  const out = $("retention-out");
  out.textContent = "Running…";
  try {
    const params = halfLives ? "?decayed_half_lives=" + halfLives : "";
    const result = await api("/api/retention" + params, { method: "POST" });
    out.textContent = result.archived_expired + " expired and " + result.archived_decayed + " decayed memories archived.";
    refreshArchive(); loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
}
$("btn-retention").addEventListener("click", () => runRetention(null));
$("btn-retention-decay").addEventListener("click", () => runRetention($("retention-halflives").value));
refreshArchive();

$("btn-bundle-export").addEventListener("click", () => { window.location.href = "/api/sync/export"; });
$("btn-bundle-import").addEventListener("click", async () => {
  const picker = $("bundle-file");
  if (!picker.files.length) { toast("Choose a .jsonl bundle first.", "err"); return; }
  const out = $("sync-out");
  out.textContent = "Importing…";
  try {
    const body = await picker.files[0].text();
    const headers = { "content-type": "application/x-ndjson" };
    const token = localStorage.getItem("amos.token");
    if (token) headers["Authorization"] = "Bearer " + token;
    const response = await fetch("/api/sync/import", { method: "POST", headers: headers, body: body });
    const stats = await response.json();
    if (!response.ok) throw new Error(stats.detail || "import failed");
    out.textContent = "Merged: " + stats.memories_added + " added, " + stats.memories_updated +
      " updated, " + stats.memories_skipped + " skipped · links +" + stats.links_added + "/" + stats.links_merged + " merged";
    loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
});

async function refreshPeers() {
  const list = $("peer-list");
  try {
    const [data] = await Promise.all([api("/api/peers"), fetchPeerStatus()]);
    list.innerHTML = "";
    if (!data.peers.length) { list.appendChild(el("span", "sm", t("No peers registered — this host syncs alone."))); return; }
    for (const peer of data.peers) {
      const row = el("div", "toprow");
      const label = el("span", "sm");
      label.appendChild(statusDot(statusFor(peer.url, peer.name)));
      label.appendChild(document.createTextNode((peer.name ? peer.name + " · " : "") + peer.url + (peer.last_synced_at ? " · last: " + peer.last_result : " · never synced")));
      const badge = el("span", "pill", peer.policy || "shared");
      badge.style.cssText = "margin-left:6px;font-size:10px;padding:1px 7px;border-radius:8px;background:var(--chip);color:var(--muted)";
      if ((peer.policy || "shared") === "full") badge.title = t("full policy shares private memories — use only for your own trusted nodes");
      label.appendChild(badge);
      row.appendChild(label);
      const removeBtn = el("button", "ghost", t("remove"));
      removeBtn.style.cssText = "font-size:11px;padding:2px 10px;flex:0 0 auto";
      removeBtn.addEventListener("click", async () => {
        try { await api("/api/peers?url=" + encodeURIComponent(peer.url), { method: "DELETE" }); refreshPeers(); }
        catch (e) { toast(e.message, "err"); }
      });
      row.appendChild(removeBtn);
      list.appendChild(row);
    }
  } catch (e) { /* pre-auth */ }
}
$("btn-peer-add").addEventListener("click", async () => {
  const url = $("peer-url").value.trim();
  if (!url) return;
  try {
    await api("/api/peers", { method: "POST", headers: { "content-type": "application/json" },
      body: JSON.stringify({ url: url, token: $("peer-token").value || null, policy: $("peer-policy").value, name: $("peer-name").value || "" }) });
    $("peer-url").value = ""; $("peer-token").value = ""; $("peer-name").value = "";
    toast("Peer registered.", "ok"); refreshPeers();
  } catch (e) { toast(e.message, "err"); }
});
$("btn-sync-now").addEventListener("click", async () => {
  const out = $("sync-out");
  out.textContent = "Syncing mesh…";
  try {
    const data = await api("/api/sync/run", { method: "POST" });
    const ok = data.results.filter(r => r.ok).length;
    out.textContent = ok + "/" + data.results.length + " peers converged.";
    refreshPeers(); loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
});
async function loadNode() {
  try { const n = await api("/api/node"); $("node-name").textContent = "· " + n.node_name; }
  catch (e) { /* pre-auth */ }
}
loadNode();
refreshPeers();

$("btn-purge").addEventListener("click", async () => {
  const owner = $("purge-owner").value.trim();
  const out = $("purge-out");
  if (!owner) { toast("Enter an agent / owner id first.", "err"); return; }
  const typed = prompt(
    "This permanently deletes ALL memories, links and the recall profile of “" + owner + "”.\n\n" +
    "Type the agent id again to confirm:"
  );
  if (typed === null) return;
  if (typed.trim() !== owner) { toast("Confirmation did not match — nothing was deleted.", "err"); return; }
  try {
    const result = await api(
      "/api/owners/" + encodeURIComponent(owner) + "/memories?confirm=" + encodeURIComponent(owner),
      { method: "DELETE" }
    );
    out.textContent = result.memories_deleted + " memories and " + result.links_deleted + " links deleted for “" + owner + "”.";
    toast("Agent “" + owner + "” forgotten.", "ok");
    $("purge-owner").value = "";
    loadStats(); loadDashboard(); browseLoaded = false;
  } catch (e) { toast(e.message, "err"); }
});

$("btn-orchestrate").addEventListener("click", async () => {
  const task = $("orch-task").value.trim();
  if (!task) return;
  const params = new URLSearchParams({ task: task, max_tokens: $("orch-tokens").value });
  if ($("orch-session").value.trim()) params.set("session_id", $("orch-session").value.trim());
  if (actingAs()) params.set("requester_agent_id", actingAs());
  const out = $("orch-out");
  out.textContent = "Orchestrating…";
  try {
    const data = await api("/api/orchestrate?" + params);
    out.innerHTML = "";
    const chips = el("div", null, "");
    chips.style.cssText = "display:flex;gap:6px;flex-wrap:wrap;margin:8px 0";
    chips.appendChild(Object.assign(el("span", "chip"), { textContent: data.used_tokens + " / " + data.max_tokens + " tokens" }));
    for (const [name, info] of Object.entries(data.sections)) {
      chips.appendChild(Object.assign(el("span", "chip"),
        { textContent: name + " · " + info.memory_ids.length + " · " + info.used_tokens + "t" }));
    }
    out.appendChild(chips);
    out.appendChild(el("pre", "packtext", data.text || "(empty)"));
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
});

$("btn-consolidate").addEventListener("click", async () => {
  const out = $("consolidate-out");
  out.textContent = "Running…";
  try {
    const result = await api("/api/consolidate", { method: "POST" });
    out.textContent = result.duplicates_merged + " duplicates merged · " + result.concepts_created + " concepts synthesized";
    loadStats();
  } catch (e) { out.textContent = ""; toast(e.message, "err"); }
});

applyLocale();
loadStats();
loadDashboard();
loadVersionBadge();
</script>
<div id="version-badge"></div>
</body>
</html>"""

PAGE = (
    PAGE.replace("__AMOS_LOGO_DATA_URI__", WEB_UI_LOGO_DATA_URI)
    .replace("__AMOS_RETENTION_MIN_HALF_LIVES__", format(RETENTION_MIN_HALF_LIVES, "g"))
    .replace("__AMOS_TOAST_DURATION_MILLISECONDS__", str(WEB_UI_TOAST_DURATION_MILLISECONDS))
    .replace("__AMOS_GRAPH_SETTLE_FRAME_THRESHOLD__", str(WEB_UI_GRAPH_SETTLE_FRAME_THRESHOLD))
)
