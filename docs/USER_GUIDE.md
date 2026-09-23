# Agent Memory OS — User Guide

Complete reference for the v1.x line. For a five-minute start, see the
[README](../README.md); for integration recipes, see
[docs/integrations/](integrations/claude-code.md).

---

## 1. Concepts

| Concept | What it is |
|---|---|
| **Memory** | One durable fact/preference/procedure/decision/warning/note, owned by an agent, with `scope`, `visibility`, `importance`, `confidence`, decay policy, optional expiry. |
| **Owner & visibility (ACL)** | `visibility: []` = owner-only (private). `["global"]` = everyone. `["agent:<id>"]` = one agent. `["team:<id>"]` = a team/project. ACL is a **hard gate** applied before ranking on every read path. |
| **Agent** | A registered identity (`kind`: claude-code / codex / openclaw / hermes / agy / custom) with team memberships. Team members automatically see `team:<id>` memories. |
| **Team = project** | A team id doubles as a project id. One agent may belong to many teams (multi-project). |
| **Links & resonance** | Authoritative association edges (`related_to`, `caused_by`, `supersedes`, `derived_from`, `co_recalled`). Search expands through them: related memories surface even without keyword overlap, ACL-safe. |
| **Reinforcement** | Helpful co-recall strengthens memories and links (Hebbian); `helpful=False` weakens them and speeds forgetting. |
| **Decay & retention** | Soft freshness decay by half-life (tuned by feedback telemetry); hard expiry; retention archives expired/idle memories into a restorable cold archive. |
| **Context pack / orchestration** | Token-budgeted prompt-ready recall. `orchestrate_context` splits the budget into session / bedrock / warnings / procedures / task buckets with proactive recall and session dedup. |
| **Federation** | Deterministic memory merging across hosts: file bundles, peer HTTP endpoints, or an auto-converging multi-peer mesh. |

**Design invariants** (see [SPEC.md](../SPEC.md)): SQLite is the single source
of truth; FTS/vector indexes are disposable; candidate providers return ids
only and every candidate rejoins SQLite behind the ACL/expiry hard gates.

---

## 2. Installation & setup

```bash
pip install 'agent-memory-os[full]'   # core + Web console + MCP + turbovec
agent-memory doctor                   # verify FTS5/extras (--install to fix)
agent-memory token create             # protect the Web API (stored mode 600)
agent-memory service install          # start at login: launchd/systemd/schtasks
```

Everything lives under one home (default `~/.agent-memory`, override with
`--home` or `AGENT_MEMORY_HOME`):

| File | Purpose |
|---|---|
| `memories.db` | The database (memories, links, agents, peers, archive…). Keep on local disk, not NFS/SMB. |
| `web_token` | Web API bearer token (`agent-memory token …`). |
| `agents.toml` | Declarative fleet config (see §5). |
| `logs/web.log` | Service logs. |

---

## 3. CLI reference (`agent-memory`)

Global flag: `--home <dir>`.

| Command | Purpose |
|---|---|
| `add <content> [--owner --scope --type --tag --confidence --importance]` | Store a memory. |
| `search <query> [--owner --scope --limit --json]` | Search (admin view; use the SDK/API for requester-gated recall). |
| `pack <query> [--max-tokens]` | Prompt-ready context pack. |
| `stats` | Database statistics. |
| `check` | SQLite + FTS + link-graph integrity, schema version. |
| `doctor [--install]` | Dependency/FTS5/token/agents.toml health; auto-install extras. |
| `token create\|show\|rotate\|disable [--readonly\|--sync]` | Web API bearer token. `--readonly` = GET-only tier; `--sync` = federation-only tier (authorizes just `/api/node` + `/api/sync/*`), the credential to hand a peer instead of the admin token. |
| `service install\|uninstall\|start\|stop\|restart\|status [--host --port --dry-run]` | Native login service. |
| `backup <dest> [--keep N]` / `restore <src> [--force]` | WAL-safe online backup / restore; `--keep N` rotates older backups (never the live DB). |
| `retention [--half-lives N]` | Archive expired (+ optionally idle ≥N half-lives); rotates session snapshots; retunes decay from feedback. `--half-lives 0` = expired only. |
| `path show\|install` | Diagnose/fix "command not found": add the pip scripts dir to your shell PATH. |
| `agent rename <old> <new> [--yes]` | Migrate an agent identity to a **new** id everywhere (ownership, `agent:` grants, memberships, profiles). Previews the memory count and confirms first; refuses an existing target — use `owner reassign` to merge. Reminds you to repoint any running service/MCP still on the old id. |
| `team rename <old> <new> [--name N] [--dry-run] [--yes]` | Rename a team id, moving everything the id carries: memberships, its projects' parent key, `team:<id>` grants in live and archived memory, and the `source.team_id` behind the legacy bare `team` grant. Previews all of it and confirms first; refuses an existing target (a rename never merges two teams). Local state only — it does not propagate as a deletion, so peers may keep the old id as an inert orphan. |
| `search <q> [--since T] [--until T] [--time-field F]` | Recall, optionally narrowed to a time window *before* ranking. `T` is an ISO-8601 instant or a relative age (`7d`, `36h`, `2w`). Half-open `[since, until)`. `F` selects the clock: `created_at` (when it was learned, default), `updated_at`, `last_accessed_at`. |
| `timeline <memory-id\|time> [--window 1h] [--limit N]` | What else was recorded around that moment, nearest first, with a signed offset. Links record associations someone asserted; a timeline surfaces the ones that simply happened together. |
| `owner list` | Every owner holding memories on this host, with live/archived counts and whether it is a registered agent. Surfaces owners the Browse tab hides (ACL-filtered by "Acting as"). |
| `owner reassign <old> <new> [--yes] [--no-register]` | Fold one owner's memories into another (merge-capable — the target **may already exist**). Previews the count and confirms first. Registers the destination as an agent so the moved memories stay recognized (`--no-register` opts out, with a warning). The fix for "memories landed under `default` instead of my agent id." |
| `owner delete <owner> [--yes]` | Permanently forget every memory owned by `<owner>` (live + archive + links + audit/recall logs + tombstones). Prompts to confirm unless `--yes`. |
| `fleet keygen [--force]` | Make THIS node a fleet console: mint an Ed25519 keypair (`<home>/fleet_admin_key`, mode 600) and print the public key to grant elsewhere. |
| `fleet grant <public key> [--caps manage,read-private]` | On a managed node: accept signed operations from that console key. `manage` = operate (status/owner tools/sync/update); `read-private` additionally allows reading memory content — audited per read. |
| `fleet revoke <key id>` / `fleet list` | Stop accepting a key immediately (per node — grants never propagate, so neither must revocations) / show grants + this node's own key. |
| `fleet status [--json]` | The whole fleet at a glance: console + every peer's version (drift warning), health, memory totals, owner counts. Up-but-not-granted nodes show as `! unauthorized`. |
| `fleet sync\|update [--node URL]` | Trigger a mesh sync or self-update on every managed node (or one). |
| `status [--json] [--no-probe]` | This host + connected nodes: service state, console reachability, store totals, per-peer online/offline + policy/token/last-synced. |
| `neighbors [--ports A-B]` | Discover other AgentMemoryOS nodes on this host (loopback `/healthz` scan; finding is not joining). |
| `team invite <team> [--ttl s]` | Mint a one-time pairing code so another node can join this team. |
| `join <code> --url <peer> [--agent-id] [--my-url]` | Redeem a pairing code: swaps sync-scoped tokens both ways, registers team-scoped peers, installs the mesh key, runs a first sync. |
| `peers add\|remove\|list [url] [--peer-token] [--policy shared\|full\|team:<id>] [--name <n>]` | Federation peer registry; `--policy` scopes what syncs to the peer, `--name` labels it (auto-fetched if omitted). |
| `node [--set-name <n>] [--set-host <h>] [--set-port <p>]` | Show or set this instance's sync identity and Web UI host/port (`<home>/instance.toml`). |
| `team list\|create\|delete\|add-member\|remove-member [id] [agent] [--name]` | Manage teams and their node members. |
| `project list\|create\|delete\|add-member\|remove-member [id] [agent] [--team] [--name]` | Manage projects under a team (members ⊆ team). |
| `maintenance scan\|orphans [--delete]\|reindex\|vacuum` | Ops: health scan, clean orphan memories (scoped to an empty group), rebuild the index, reclaim disk. |
| `update [--check] [--yes] [--no-restart] [--team]` | Detect host/Docker deployment, check PyPI, upgrade (pip), then restart the running console; `--check` reports only. `--team` also triggers self-update on every peer that opted in (console started with `AGENT_MEMORY_ALLOW_TEAM_UPDATE=1`). |
| `sync export <file> [--since --team]` | Write a bundle (optionally one project's memory). |
| `sync import <file>` | Merge a bundle (last-writer-wins / strongest-wins). |
| `sync pull\|push <peer-url> [--peer-token]` | One peer over HTTP(S). |
| `sync auto` | Converge with every registered peer. |
| `sync genkey` | Generate + store a mesh encryption key (`<home>/sync_key`); set the same `AGENT_MEMORY_SYNC_KEY` on every node to encrypt bundle content on the wire. Needs the `secure-sync` extra. |
| `import-hermes --profile --profile-home` | Import Hermes `MEMORY.md`/`USER.md` (idempotent). |
| `hermes install\|uninstall [--hermes-home]` | Register/remove the Hermes Agent memory-provider shim so `hermes memory setup\|status` can see AgentMemoryOS. See [docs/integrations/hermes-agent.md](integrations/hermes-agent.md). |
| `import --from mem0\|zep\|chatgpt <file> [--owner --visibility --type]` | Import an export from another memory system (idempotent; private by default). See [docs/IMPORTERS.md](IMPORTERS.md). |
| `golden-recall --cases <file>` | Recall-quality evaluation gate. |
| `shadow-summary --log <jsonl>` | Shadow-mode evidence summary. |

---

## 4. Web console & HTTP API

```bash
agent-memory-web --host 127.0.0.1 --port 8000 [--token …]
```

Console tabs: **Dashboard** (stats, activity, resonance health, **token-usage
cards** by agent/team/project) · **Search** · **Browse** (filters, in-place
edit) · **Graph** (interactive link graph with a **scope filter**) · **Agents**
(fleet management) · **Teams** (teams, projects, membership) · **Add memory** ·
**Tools** (context pack, orchestrator, links, consolidate, retention & archive,
federation, **maintenance**: orphan scan/cleanup, reindex, vacuum, **check for
updates**; **membership audit** viewer). A small **version badge** sits in the
bottom-right. UI languages: English, 繁體中文, 简体中文, 日本語, 한국어.

Requests without `requester_agent_id` run in **admin view**; bind to
localhost or require the bearer token. All `/api/` routes honor the token
gate when one is configured.

**Token tiers.** `agent-memory token create` mints the full token (any action).
`agent-memory token create --readonly` mints a second, GET-only token: it can
read every view but every mutation (create/delete/share/vacuum/update…) is
rejected, and the console shows a "read-only mode" banner. Use it for dashboards
and auditors.

| Endpoint | Purpose |
|---|---|
| `GET /health`, `GET /healthz`, `GET /metrics` | Liveness, integrity-aware readiness (200/503), Prometheus metrics — all unauthenticated (aggregate counts only). |
| `GET /api/stats`, `GET /api/dashboard`, `GET /api/usage`, `GET /api/integrity` | Stats, dashboard, token-usage footprint (agent/team/project/total). |
| `GET /api/teams[…]`, `GET /api/projects[…]`, `GET /api/org/audit` | Teams, projects, membership, and the membership-audit log. |
| `GET /api/maintenance/{scan,orphans}`, `POST /api/maintenance/{orphans/delete?confirm=orphans,reindex,vacuum}` | Ops maintenance. |
| `GET /api/maintenance/update-check`, `POST /api/maintenance/update-run?confirm=update` | Version check and one-click self-update (host only). |
| `GET/POST /api/memories`, `GET/PATCH/DELETE /api/memories/{id}` | CRUD + recency browse (`?scope=&type=&owner=&requester_agent_id=`). |
| `GET /api/search`, `GET /api/context-pack`, `GET /api/orchestrate` | Recall (requester-gated; orchestrate supports `session_id`, `max_tokens`). |
| `POST /api/links`, `GET /api/memories/{id}/links`, `GET /api/graph` | Associations (graph is requester-gated). |
| `POST /api/recall` | Helpful/misleading feedback (`requester_agent_id` restricts effect to visible memories). |
| `POST /api/memories/{id}/share\|revoke`, `GET /api/memories/{id}/audit` | Owner-only sharing / de-identified copies / audit trail. |
| `POST /api/consolidate`, `POST /api/retention`, `GET /api/archive`, `POST /api/archive/{id}/restore` | Hygiene & lifecycle. |
| `GET/POST/DELETE /api/agents[…]` | Agent registry. |
| `GET/POST/DELETE /api/peers`, `GET /api/peers/status`, `POST /api/sync/run`, `GET /api/sync/export`, `POST /api/sync/import` | Federation. `/peers/status` probes each peer's health for the console's connection dot. |
| `GET /api/logs?file=&lines=&q=` | Tail the node's service logs (whitelisted files, last-N with filter). |
| `GET /api/owners`, `POST /api/owners/reassign` | List owners with counts; re-attribute one owner's memories to another (merge-capable). |
| `GET /api/fleet/status`, `POST /api/fleet/trigger`, `GET /api/fleet/browse`, `/api/fleet/proxy` | Console-node only: aggregate fleet view; run sync/update across managed nodes; read memories live off a node that granted `read-private` (audited on that node); the proxy powers the console's remote-management mode (switch "Acting as" to a fleet identity and every tab operates on that node). Cross-node calls authenticate with per-request Ed25519 signatures (`X-AMOS-Fleet-*` headers), not bearer tokens. |
| `DELETE /api/owners/{owner}/memories?confirm=<owner>` | Forget an agent entirely (double confirmation). |

---

## 5. Multi-agent projects

Register the fleet — console **Agents** tab, `POST /api/agents`, or as code:

```toml
# ~/.agent-memory/agents.toml   (re-applied on every open; file-authoritative)
[agents.cc-main]
kind = "claude-code"
teams = ["apollo", "shared-infra"]

[agents.hermes-neo]
kind = "hermes"
teams = ["apollo", "ops"]
```

Give each MCP server its identity:

```json
"env": { "AGENT_MEMORY_HOME": "~/.agent-memory", "AGENT_MEMORY_AGENT_ID": "cc-main" }
```

Then:
- writes default to that agent as owner;
- recalls carry its identity **and all of its teams** — `team:apollo`
  memories are visible to every apollo member with no extra wiring;
- private (`visibility: []`) memories stay private inside the fleet;
- owners can `share`/`revoke` (optionally de-identified) with full audit.

MCP tools (11): `memory_add`, `memory_search`, `memory_context_pack`,
`memory_orchestrate_context`, `memory_link`, `memory_update`,
`memory_recall_feedback`, `memory_consolidate`, `memory_offload_context`,
`memory_reload_context`, `memory_snapshot_diff`.

---

## 6. Federation & project sync

In **Tools → Federation**, file imports and **Sync mesh now** show a latest-operation report with the bundle or target node, named record counts, and expandable response details. Mesh results list each peer separately, including failures and rejected organization records. The report stays visible until another operation or a page reload; it is not a stored history.

File imports and bundle downloads use the local node. Mesh sync uses the currently selected node. An **Import rolled back** result is shown only when the server confirms that none of that bundle's records were applied. If a response is lost, the result is unconfirmed—check the node before retrying. A failed peer sync may have completed earlier steps, so its failure does not imply that all changes were rolled back.

```bash
# on each host: mint a sync-scoped token to give peers (NOT the admin token)
agent-memory token create --sync                       # prints amos_sync_…

# each host: register the others (the peer's sync token authenticates you)
# --policy controls what leaves for this peer:
agent-memory peers add https://host-b:8000 --peer-token <b-sync-token>          # 'shared' (default)
agent-memory peers add https://my-laptop:8000 --peer-token <t> --policy full    # own trusted node
agent-memory peers add https://team-hub:8000 --peer-token <t> --policy team:apollo
agent-memory sync auto            # pull + push with every peer

# ship one project's memory as a file
agent-memory sync export apollo.jsonl --team apollo
```

**Encrypt bundle content on the wire.** Generate a mesh key once and set the
same value on every node — bundles are then encrypted app-layer (Fernet), so
content is confidential even over plain HTTP or through a TLS-terminating proxy
(the key never crosses the wire). Needs the `secure-sync` extra
(`pip install "agent-memory-os[secure-sync]"`):

```bash
agent-memory sync genkey                               # prints amos_sk_… , stores <home>/sync_key
export AGENT_MEMORY_SYNC_KEY=amos_sk_…                  # or set this env on every node
```

Encryption is opportunistic — it engages only when a key is configured, so set
it on *all* nodes. The bearer token still rides in the header, so prefer
`https://` peer URLs (certificate-verified) for non-localhost peers.

**Peer policy** decides what a peer receives: `shared` (default — everything
except private `visibility=[]` memories), `full` (the whole store *including
private* — use only for your own trusted replica nodes), or `team:<id>` (one
project). Private memories never leave under `shared`/`team`, and the HTTP
`/api/sync/export` a peer pulls is always `shared`-scoped — full private
replication travels only over the push leg between your own `full` nodes.

### Multiple instances on one machine

Each instance is a separate `--home`. Give each a name and let the console pick
a free port:

```bash
agent-memory --home ~/mem-a node --set-name mizuki-laptop
agent-memory --home ~/mem-b node --set-name codex-box
agent-memory-web --home ~/mem-a          # binds 8000
agent-memory-web --home ~/mem-b          # 8000 taken → auto-binds 8001
```

Settings live in `<home>/instance.toml`:

```toml
[instance]
node_name = "mizuki-laptop"   # shown to peers during sync
host = "127.0.0.1"
port = 8000                   # taken? the launcher advances to a free port
```

`--port` overrides the file; `--strict-port` fails instead of advancing. When
you register a peer, its name is auto-fetched from `GET /api/node` (or set it
with `peers add --name`), so peer lists and sync results read
`mizuki-laptop · http://host:8000` instead of a bare URL.

### Teams & projects

A **team** is a set of node members. A team can hold multiple **projects**, and
each project's members are a **subset** of the team's members. Team memory
(`visibility: ["team:<id>"]`) reaches every team member; project memory
(`visibility: ["project:<id>"]`) reaches only that project's members.

```bash
agent-memory team create apollo --name Apollo
agent-memory team add-member apollo alice
agent-memory team add-member apollo bob
agent-memory project create apollo-web --team apollo --name "Apollo Web"
agent-memory project add-member apollo-web alice     # must already be on the team
agent-memory team list ; agent-memory project list --team apollo
```

Or manage it visually in the console's **Teams** tab (create a team → pick node
members; create a project under it → pick members from the team). Invariants are
enforced everywhere: a project member must be a team member; leaving a team
removes the agent from that team's projects; deleting a team removes its
projects.

Write scoped memory and sync it correctly:

```python
client.add("apollo-wide runbook", owner="neo", visibility=["team:apollo"])
client.add("apollo-web deploy key location", owner="neo", visibility=["project:apollo-web"])
# a peer that only replicates one project's shared memory:
agent-memory peers add http://web-box:8000 --peer-token <t> --policy project:apollo-web
```

The `project:apollo-web` peer bundle carries only that project's shared
memories (and its members' profiles), so project memory never reaches a node
whose agents aren't project members.

Merge rules (identical for files, HTTP, and mesh): memories & profiles —
last-writer-wins on normalized `updated_at` with a content tie-break so
same-second edits converge; links — strongest weight / highest activation /
latest activation. **Deletions propagate** via tombstones (a purged/deleted
memory will not resurrect from a peer). Imports from a non-`full` peer record
`source.synced_from` and cannot impersonate your local agents. Unreachable
peers fail individually. Pair `service install` with a cron/timer entry running
`sync auto` for a continuously converging federation.

---

## 7. SDK quick reference

```python
from agent_memory_os import MemoryClient, RecallProfile

client = MemoryClient(home="~/.agent-memory", semantic="auto")

m = client.add("…", owner="neo", type="procedure", visibility=["team:apollo"],
               auto_link=True)
hits = client.search("…", requester_agent_id="neo")
pack = client.context_pack("…", requester_agent_id="neo", auto_reinforce=True)
ctx  = client.orchestrate_context("deploy staging", session_id="s1",
                                  requester_agent_id="neo", max_tokens=2000)

client.link(a.id, b.id, relation="caused_by", weight=0.8)
client.record_recall([a.id, b.id])            # helpful=False to weaken
client.save_profile(RecallProfile(agent_id="neo",
                                  type_weights={"procedure": 1.5}))
client.consolidate(derive_links=True)          # or link_extractor=<LLM fn>
client.run_retention(); client.integrity_check()
client.share_memory(m.id, actor="neo", to_team="apollo", deidentify=True)
client.offload_context({...}, session_id="s1"); client.reload_context("s1")
client.snapshot_diff("s1")
client.export_bundle("out.jsonl", team="apollo"); client.import_bundle("in.jsonl")
```

For LLM-backed link extraction:
`agent_memory_os.extractors.make_llm_link_extractor(complete_fn)`.

---

## 8. Operations checklist

- **Daily**: nothing — the service self-restarts; auto semantic index
  self-syncs; feedback keeps tuning forgetting.
- **Periodic** (cron/timer): `agent-memory retention && agent-memory sync auto`
  and `agent-memory backup ~/backups/memories-$(date +%F).db --keep 14`
  (`--keep N` rotates out older backups in the same name series; it can never
  delete the live database).
- **Monitoring**: point a health check at `GET /healthz` (200 healthy / 503
  degraded) and a Prometheus scraper at `GET /metrics` (memory/orphan/index-drift
  /peer-error gauges). Both are unauthenticated and expose only counts.
  `agent-memory doctor` also flags processes running code older than what's
  installed.
- **When suspicious**: `agent-memory check`; repair FTS drift with the SDK's
  `rebuild_indexes()` (indexes are disposable, the database is the truth), or
  from the console's Tools → Maintenance panel (scan, orphan cleanup, reindex,
  vacuum).
- **Upgrades**: `agent-memory update` (since v0.14.0) — reports current vs
  PyPI latest, detects host-vs-Docker deployment, upgrades via pip after
  confirmation (`--yes` to skip, `--check` to only report), then handles the
  part a pip upgrade can never do: **processes that are already running keep
  the OLD code**. The tool restarts the web console for you (via the installed
  service, or by relaunching the process — the auth token persists, no
  re-login; `--no-restart` opts out) and lists MCP servers that need their
  host app (e.g. Claude Code) restarted — it never kills those itself.
  `update --check` also flags processes that started before the current
  install landed, i.e. "disk is new, memory is stale".
  - **From the web console**: Tools → Maintenance → **Check for updates** shows
    current-vs-latest; if a newer version exists, an **Update now** button runs
    the same self-update (upgrade + console restart) in the background — reload
    the page after ~30 s. The small **version badge** in the bottom-right always
    shows the running version.
  - The database self-migrates forward on next open (schema version shown by
    `check`); take a backup first if you may need to roll back the package.
  - Installs older than v0.14.0 predate this command — the first hop is a
    manual `pip install -U 'agent-memory-os[full]'`.
  - In Docker, `update` prints the image-pull path instead (a container can't
    pip-upgrade itself): `docker pull` the new tag and recreate; `/data`
    persists.
