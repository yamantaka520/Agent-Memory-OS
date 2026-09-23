// Exercise the shipped inline handlers without adding a browser dependency to pytest.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const page = fs.readFileSync(0, "utf8");
function section(name) {
  const start = `/* ---------- ${name} ---------- */`;
  const end = `/* ---------- end ${name} ---------- */`;
  assert.ok(page.includes(start) && page.includes(end), name);
  return page.split(start)[1].split(end)[0];
}
class Element {
  constructor(tag) { this.tag = tag; this.children = []; this.handlers = {}; this.attributes = {}; this.dataset = {}; this._text = ""; }
  appendChild(child) { this.children.push(child); return child; }
  replaceChildren() { this.children = []; this._text = ""; }
  set textContent(value) { this._text = String(value); this.children = []; }
  get textContent() { return this._text + this.children.map(child => child.textContent).join(""); }
  set innerHTML(value) { throw new Error("Federation results must not render input as HTML"); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(event, handler) { this.handlers[event] = handler; }
}
function setup() {
  const nodes = Object.fromEntries(["btn-bundle-export", "btn-bundle-import", "btn-sync-now", "bundle-file", "sync-out"].map(id => [id, new Element("div")]));
  nodes["bundle-file"].files = [];
  const ctx = vm.createContext({
    I18N: { "zh-TW": {}, "zh-CN": {}, ja: {}, ko: {} }, locale: "en",
    $: id => nodes[id],
    el: (tag, cls, text) => { const n = new Element(tag); n.className = cls; if (text != null) n.textContent = text; return n; },
    localStorage: { getItem: () => "FULL" }, window: { location: {} },
    loadStats() {}, loadDashboard() {}, refreshPeers() {}, toast() {},
    browseLoaded: true, remoteTarget: null,
    document: { querySelectorAll() {
      function labels(node) { return [...(node.dataset.syncLabel ? [node] : []), ...node.children.flatMap(labels)]; }
      return labels(nodes["sync-out"]);
    } },
  });
  vm.runInContext(section("federation result translations"), ctx);
  vm.runInContext('function t(key) { return I18N[locale]?.[key] || key; }', ctx);
  vm.runInContext(section("federation operation results"), ctx);
  return { ctx, nodes, text: () => nodes["sync-out"].textContent };
}
function response(body, ok = true, rollback = false) {
  return { ok, json: async () => body, headers: { get: () => rollback ? "rolled-back" : null } };
}
async function main() {
  const { ctx, nodes, text } = setup();
  const filename = '<img src=x onerror="bad()">.jsonl';
  nodes["bundle-file"].files = [{ name: filename, text: async () => "bundle body" }];
  ctx.remoteTarget = { name: "Remote node", url: "http://remote" };
  ctx.fetch = async (url, options) => {
    assert.equal(url, "/api/sync/import");
    assert.equal(options.headers.Authorization, "Bearer FULL");
    assert.equal(options.body, "bundle body");
    assert.equal(nodes["btn-sync-now"].disabled, true);
    return response({ memories_added: 2, memories_updated: 1, memories_skipped: 3, links_added: 0, org_records_rejected: 1 });
  };
  await nodes["btn-bundle-import"].handlers.click();
  assert.ok(text().includes(filename));
  assert.match(text(), /Target: This node/);
  assert.match(text(), /Completed with rejected records/);
  assert.match(text(), /Memories skipped/);
  assert.match(text(), /Organization records rejected/);
  assert.equal(nodes["btn-bundle-import"].disabled, false);

  ctx.fetch = async () => response({ detail: "invalid visibility <script>bad()</script>" }, false, true);
  await nodes["btn-bundle-import"].handlers.click();
  assert.match(text(), /Import rolled back/);
  assert.match(text(), /No records from this bundle were applied/);
  assert.ok(text().includes("<script>bad()</script>"));
  assert.doesNotMatch(text(), /Memories added/);

  ctx.fetch = async () => { throw new Error("Connection lost"); };
  await nodes["btn-bundle-import"].handlers.click();
  assert.match(text(), /Connection lost/);
  assert.match(text(), /outcome could not be confirmed/);
  assert.doesNotMatch(text(), /rolled back|No records from this bundle were applied/);
  assert.equal(nodes["sync-out"].hidden, false);

  ctx.fetch = async () => response({ detail: "unauthorized" }, false);
  await nodes["btn-bundle-import"].handlers.click();
  assert.match(text(), /unauthorized/);
  assert.doesNotMatch(text(), /rolled back/);

  ctx.fetch = async () => response(null, false);
  await nodes["btn-bundle-import"].handlers.click();
  assert.match(text(), /No error detail was returned/);
  assert.doesNotMatch(text(), /TypeError|rolled back/);

  ctx.api = async (path, options) => {
    assert.equal(path, "/api/sync/run");
    assert.equal(options.method, "POST");
    return { results: [
      { peer: "http://healthy", ok: true, pulled: { memories_added: 2 }, pushed: { memories_skipped: 1 } },
      { peer: "http://failed/<b>", ok: false, error: "Peer refused the bundle" },
    ] };
  };
  await nodes["btn-sync-now"].handlers.click();
  assert.match(text(), /Target: Remote node \(http:\/\/remote\)/);
  assert.match(text(), /Completed with failures/);
  assert.match(text(), /Peers completed: 1 · Peers failed: 1/);
  assert.match(text(), /Received from peer/);
  assert.match(text(), /Sent to peer/);
  assert.match(text(), /Peer refused the bundle/);
  assert.match(text(), /Some records may already have been applied/);
  assert.doesNotMatch(text(), /converged|Import rolled back/);
  assert.equal(nodes["btn-sync-now"].disabled, false);

  ctx.api = async () => ({ results: [] });
  await nodes["btn-sync-now"].handlers.click();
  assert.match(text(), /No peers to sync/);

  const keys = Object.keys(ctx.I18N["zh-TW"]);
  for (const language of ["zh-CN", "ja", "ko"]) {
    assert.deepEqual(Object.keys(ctx.I18N[language]).sort(), [...keys].sort());
    assert.ok(Object.values(ctx.I18N[language]).every(value => value.length > 0));
  }
  ctx.locale = "ja";
  ctx.api = async () => { throw new Error("Changing language must not repeat sync"); };
  vm.runInContext("translateSyncReport()", ctx);
  assert.match(text(), /同期するピアがありません/);
  console.log("Federation workflow: outcomes, counts, rollback certainty, text safety, targets and locales passed");
}
main().catch(error => { console.error(error); process.exitCode = 1; });
