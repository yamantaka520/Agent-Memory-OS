# Changelog — Agent Memory OS

All notable changes, newest first. Releases are published to
[PyPI](https://pypi.org/project/agent-memory-os/) via Trusted Publishing and
tagged on GitHub/GitLab.

## [Unreleased]

- **Fix: malformed fleet-proxy bodies return HTTP 400.** Invalid UTF-8 or JSON is rejected locally before signing or forwarding the request. Valid JSON and empty request bodies keep their existing forwarding behavior.

## [1.9.0] — 2026-08-19

- **Team rename, in the CLI and the console.** A team id is not just a row key:
  it is the token inside every `team:<id>` visibility grant, the parent key of
  every project under it, and the `source.team_id` that the legacy bare `team`
  grant resolves through. Renaming one by hand meant touching four places and
  hoping. `agent-memory team rename <old> <new>` (with `--dry-run` and a
  confirmation prompt) and the Teams tab's rename button now move all of it in
  one transaction — memberships, projects, live and archived grants, and the
  bare-grant key — bumping `acl_updated_at` (the clock sync converges
  visibility on) without touching the content clock, and writing an
  `org_audit` entry. Both surfaces preview what travels with the id first.
  A rename deliberately emits no org tombstone: applying one cascade-deletes
  that team's projects and strips their `project:<id>` grants on the receiving
  node, which the renamed records cannot restore — so a rename is local state,
  and the result warns when peers exist. Memory text that mentions the old id
  is history and is left unchanged.
- **`agy` is a first-class agent kind.** Antigravity (CLI and IDE) had to
  register as `custom`, which left it unlabelled in the console. It now has its
  own kind, badge, and dropdown entry alongside claude-code / codex / openclaw /
  hermes.
- **Fix: `agents.teams` no longer drifts from actual membership.** That column
  mirrors `team_members`, and a stale mirror is a trap rather than a cosmetic
  wart: `register_agent` reconciles membership to the list it is handed and
  drops any team absent from it, and the console's agent editor round-trips the
  column — so a mirror left behind by `add_team_member`, `remove_team_member`,
  `delete_team`, or a team rename could move an agent back to a team that no
  longer exists, taking its project memberships with it. The mirror is now
  rebuilt from the authoritative join table on every membership change, in one
  statement, so no future mutation path can forget to maintain it.

## [1.8.2] — 2026-08-07

- **Hermes and MCP worker-thread safety.** Hermes creates providers on the gateway thread and performs hooks on worker threads; MCP Python SDK v2 likewise runs synchronous tools in AnyIO worker threads. Both integrations now enable cross-thread SQLite handoff and serialize all access to their shared connection, with regression coverage for handoff and concurrent-call safety.
- **MCP Python SDK v2 support.** The high-level server uses `MCPServer`, packaged MCP installs require `mcp>=2.0.0,<3`, stdio round trips accept v2 snake-case result fields, and source checkouts retain a v1 import fallback.
- **Fix: the self-update relaunch log is now visible in the console log
  viewer.** The pidfile relaunch (`agent-memory update` on a non-service
  install) wrote `<home>/web.log`, a root-level name the Tools → Logs
  whitelist never included — so the one log that explains a failed restart
  couldn't be read from the console. The relaunch now writes to
  `logs/web.log` (the same location the installed service uses, already
  whitelisted), and the viewer additionally serves a legacy root-level
  `web.log` so logs written by earlier versions stay reachable.

## [1.8.1] — 2026-07-28

- **Fix: self-update now works on systemd-managed nodes.** The detached
  updater spawned by `update-run` lives in the unit's cgroup, and systemd's
  default `KillMode=control-group` reaped it the moment the main process
  stopped — `fleet update` reported ok while every node silently stayed on
  the old version. Under systemd (detected via `INVOCATION_ID`) the console
  now upgrades in an in-process thread and then exits itself, letting
  `Restart=always` bring it back on the new code; a failed pip leaves the
  node running on the current version. Bare/pidfile deployments keep the
  existing detached-updater path.

## [1.8.0] — 2026-07-25

- **Remote management mode: identity switching now switches the whole
  console.** On a fleet console, "Acting as" was only a local ACL filter —
  but a member's memories live on the member's NODE, so switching identity
  showed an empty view, defeating the point of central management. Now, when
  the operator switches to an identity that lives on a managed node, every
  tab (dashboard, browse, search, graph, agents, teams, tools) transparently
  reads and writes THAT node through the new signed fleet proxy
  (`/api/fleet/proxy`) — full remote administration from one UI, with a
  prominent "Managing remote node" banner and one-click return. The target
  node remains the authority: it verifies each request's Ed25519 signature,
  enforces its manage/read-private grants, and audits every accepted call.
  Guard rails: only registered peers, only non-fleet `/api/` paths, no proxy
  recursion. Local-only identities keep the existing ACL-filter behavior.

- **Log viewer in the console** (Tools → Logs): tails this node's service
  log — last 100 lines by default (100/300/1000/2000), scrollable, with a
  case-insensitive filter that searches the recent window and returns the
  last matching lines. Strictly whitelisted to the home's known log files
  (`webui.log`, `logs/*.log`) with a bounded 2 MB read. In remote-management
  mode it shows the managed node's logs, like every other tab.

## [1.7.0] — 2026-07-25

Requester-scoped memory state — the MCP identity model, completed (community
contribution by @warrentc3, #5, with #4).

- **Security: four holes closed.** On earlier releases, with an MCP identity
  configured: `memory_add`'s caller-supplied `owner` could spoof any identity
  (the argument took precedence over the environment); `update`/`link`/
  `consolidate` had no ownership checks; `memory_reload_context(snapshot_id=…)`
  could read ANY memory by id, bypassing the visibility hard gate; and
  cross-process revocations could be served stale from the recall cache
  indefinitely. All four are fixed — identity comes only from
  `AGENT_MEMORY_AGENT_ID`, mutations require ownership (sharing grants recall,
  not mutation authority), and a `PRAGMA data_version` check (~2 µs/read)
  invalidates process-local caches when another connection commits.
- **Snapshots and delivery history are per identity** (migrations 18–19).
  Context snapshots and the iterative-delivery log are scoped by owner and
  session; pre-upgrade context is marked as *Historical context* and stays
  readable so existing sessions keep their continuity — the console's
  Ownership panel offers a **Classify** action to assign it (delivery history
  moves along). An unset identity retains the legacy administrative behavior.
- **Validation is write-strict, read-lenient.** New writes validate
  scope/type/tags/visibility/expiry; existing rows with application-defined
  values keep hydrating and searching fine. Expiry comparisons are now
  instant-based (`julianday`), with legacy spellings canonicalized
  (migration 20) and normalized on sync import.
- **Fix: `service install` no longer reports success when the native service
  manager failed** (@warrentc3, #4) — launchctl/systemctl/schtasks failures
  surface with a failing exit code.
- Console strings for the new legacy-context flow translated in all five
  locales.

## [1.6.2] — 2026-07-21

- **Fix: fleet update trigger echoes `?confirm=update`.** The update-run
  endpoint refuses without the explicit confirmation echo (so a stray POST
  can never restart a node) — but the fleet trigger didn't supply it, so
  `fleet update` and the console's Update buttons failed fleet-wide with
  HTTP 400 on 1.6.1.
- **Migration anti-collision** (first community contribution — thanks
  @warrentc3, #3): duplicate or out-of-order migration versions are rejected
  before touching a database, and an applied version whose recorded
  description no longer matches the declared migration fails closed instead
  of silently skipping — two independently developed branches can no longer
  ship databases with an incomplete schema.
- **Test hygiene** (@warrentc3, #2): Windows-safe path assertions, tests no
  longer open persistent homes (one touched the operator's real
  `~/.agent-memory`), updater process creation mocked in the authorization
  test, tidier `.gitignore`.

## [1.6.1] — 2026-07-21

Console usability fixes from first real fleet use.

- **Fleet remote browse.** The console can now READ memories live off a
  managed node (Fleet tab → per-node **Browse**; `GET /api/fleet/browse`).
  Private memories deliberately never sync to the console, so its local
  Browse cannot show them — this reads them from the owning node over a
  signed request instead. The node enforces its `read-private` grant and
  records every such read in its own audit log; a node that granted only
  `manage` gets a clear "not granted" message, not an empty list.
- **"Acting as" is a real dropdown now.** The identity switcher was a
  datalist, and datalist suggestions filter by the field's current value —
  once an identity was picked it looked like the only option. Replaced with
  a `<select>` listing every registered agent plus "admin (all)".
- **Teams member dots correlate via display name.** A member chip is an
  agent ID; peer status is keyed by node name. When they differed (the
  default before an operator renames), the chip showed no connection dot.
  The lookup now also goes through the agents registry's display_name.

## [1.6.0] — 2026-07-19

Fleet console: manage every node from one place — one WebUI for a same-host
multi-account fleet or a multi-host mesh — without giving up local-first data
sovereignty.

- **Coordinator, not controller.** Any node that runs `agent-memory fleet
  keygen` can act as the console for every node that ran `agent-memory fleet
  grant <public key>`. Each managed node keeps final say: it verifies every
  request's Ed25519 signature against its own locally-stored grant, enforces
  its own capability split, audits what it accepted, and can revoke at any
  moment. Losing the console loses nothing but the aggregate view.
- **Signed cross-node auth (no shared secret on the wire).** Requests carry
  `X-AMOS-Fleet-*` headers signing method + path + query + body digest +
  timestamp + nonce: tampering with any of them, replaying (durable nonce
  table), or drifting past ±120 s invalidates the signature. Grants live in a
  new `fleet_admins` table (migration 17) written ONLY via the local CLI —
  the sync bundle has no channel for them, so an untrusted peer can never
  mint itself admin access.
- **Two capabilities, granted per node.** `manage` operates the node (status,
  owner tooling, sync, self-update); `read-private` additionally allows
  reading memory content across nodes — deliberately separate, off by
  default, and every such read lands in the owning node's org audit log
  attributed to the signing key.
- **Fleet tab (WebUI) + `fleet status|sync|update` (CLI).** Version matrix
  with drift warning, per-node health dot / memory totals / owner counts,
  per-node and fleet-wide Sync and Update actions. Nodes that are up but not
  granted show as such instead of vanishing. New API: `GET /api/fleet/status`,
  `POST /api/fleet/trigger`.
- Revocation is per node by design (grants never propagate, so neither must
  revocations): `agent-memory fleet revoke <key id>` on the node(s) involved
  takes effect immediately.

## [1.5.1] — 2026-07-19

Fixes to the team-join flow that left the console out of sync on both nodes,
plus a connection-status indicator.

- **Fix: a joining node never appeared in the inviter's Agents tab.**
  `join_team_and_register_peer` used `UPDATE agents … WHERE id = ?`, a no-op for
  an id that doesn't exist yet — so a paired remote agent landed in
  `team_members` and `sync_peers` but never in the agents registry. It is now
  registered (kind `custom`, named after its node), so it shows up as a proper
  identity instead of a bare member id. Re-joins only bump `last_seen_at`,
  never clobbering an operator-set display name.
- **Fix: a joining node's own Teams tab stayed empty.** `join_with_code` wired
  up the inviter as a peer but never recorded the team locally. It now creates
  the team and adds itself as a member on redemption, and — using the inviter's
  identity, newly returned in the redeem response — registers the inviter as a
  team member/agent too. Both sides converge immediately instead of waiting on
  a sync pass.
- **Connection status in the console.** New `GET /api/peers/status` probes every
  peer's `/healthz` concurrently; the Federation panel, Agents tab, and Teams
  member chips now show a color dot next to the display name — green (connected),
  orange (reachable but degraded/integrity failure), red (unreachable). Local
  identities with no peer stay dotless. Translated across all five locales.

## [1.5.0] — 2026-07-19

Ownership tooling: find, migrate, and delete memories by owner — the operator
answer to "the WebUI browse tab is empty but I know memories exist here."

- **Owner tools (CLI + Web console + API).** Memories are keyed by an *owner*
  identity, and the Browse tab is ACL-filtered by the "Acting as" identity — so
  memories written under an identity you are not browsing as (a fallback owner
  like `default`, another account's agent) are simply invisible there. The new
  **Ownership** panel in Tools lists every owner on the host with live/archived
  counts, flags the ones hidden from your current "Acting as" identity, and
  offers per-owner **Reassign** and **Delete**. Same surface on the CLI:
  `agent-memory owner list | reassign <old> <new> | delete <owner>`.
- **`reassign_owner` — merge-capable re-attribution.** Unlike `agent rename`
  (which refuses an existing target so two identities never silently merge),
  reassign folds one owner's memories, `agent:<id>` ACL grants (ACL clock
  bumped so peers converge), team/project memberships, recall profile, and
  registry row into a target that *may already exist*. This is the operation
  that fixes the common "memories landed under `default` instead of my agent
  id" case. Deletion reuses the existing right-to-forget purge (live + archive
  + links + audit/recall logs + tombstones).
- New API: `GET /api/owners`, `POST /api/owners/reassign` (destructive delete
  stays on the existing `DELETE /api/owners/{owner}/memories?confirm=<owner>`).
  Console strings translated across all five locales.
- **Move safety — preview, confirm, and stay recognized.** `agent rename` and
  `owner reassign` now show what will move (N live + M archived memories, plus
  grants/memberships/profile) and ask before acting (`--yes` skips). Reassign
  **registers the destination as an agent** when it isn't one already, so moved
  memories land under an identity the console, member pickers, and "acting as"
  suggestions recognize — instead of silently recreating the hidden-owner state
  (`--no-register` opts out, with a warning). `agent rename` also reminds you to
  repoint any running service/MCP client still using the old id. Verified by a
  regression test that a private memory follows its owner and stays readable by
  the new identity (and not the old).

## [1.4.1] — 2026-07-18

- **Fix: Web console dashboard was blank on 1.4.0.** The node-rename control's
  JavaScript handler shipped without its HTML button (a botched edit landed the
  `$("btn-node-rename")` listener but not the element), so a `TypeError` at
  script load aborted the entire dashboard init — version badge, memory/link
  counts, stat cards, charts, and browse all went dark. The button markup is
  restored, and a new test asserts every top-level `$("id").addEventListener`
  targets an element that exists in the page (this class of bug is invisible to
  the API-level tests). No API or data change.

## [1.4.0] — 2026-07-18

Field-reported gaps from real multi-account use: identity vs display cleanup,
first-run loop closure, PATH ergonomics, and fleet-wide updates.

- **`agent-memory path show|install`.** pip's per-user script directory is
  often not on PATH ("command not found" right after install); `path install`
  appends the export to the right shell profile (zsh/bash/fish; prints the
  `setx` command on Windows) and works via `python -m agent_memory_os.cli`
  when the script itself is unreachable. `doctor` now warns about it.
- **Default node names include the account.** Every account using the default
  home used to advertise the SAME node name (host + "agent-memory"); the
  default is now host + username (+ home basename when non-default). Nodes
  with a persisted name are unaffected.
- **Node rename propagates.** New `POST /api/node` + a WebUI control (Tools →
  Rename node); peers refresh a node's display name automatically on every
  sync, so renames finally reach the peers that registered the old name.
- **`agent-memory agent rename <old> <new>`** (+ `POST /api/agents/rename`):
  migrates an agent identity atomically across memory ownership, `agent:<id>`
  ACL grants (ACL clock bumped so peers converge), team/project memberships,
  the registry, and recall profiles — with per-table change counts.
- **First-run loop closed.** The agents registry is seeded with the node's own
  default agent at console startup, and the Teams member picker is now a
  free-text input with suggestions — so the very first install can add itself
  to a team, and unregistered remote agents can be added by id.
- **Fleet updates: `agent-memory update --team`.** Reports each peer's version
  (now exposed in `/healthz`) and triggers self-update on peers that opted in
  (console started with `AGENT_MEMORY_ALLOW_TEAM_UPDATE=1`); a sync token
  gains no other authority from the opt-in. `status` shows version drift.
- **`docs/DEPLOYMENT.md`**: the four topologies (single/multi account ×
  single/multi machine) with a recipe each.
- **Post-review hardening (code + security review of the multi-account work):**
  pairing redemption now decrypts and validates the envelope BEFORE consuming
  the one-time code and does the team-member + peer writes in one transaction
  (a malformed request no longer burns the code or leaves a ghost member); a
  plaintext (non-`AMOSENC1`) envelope is rejected; `agent-memory join` refuses
  a non-loopback `http://` target unless `--insecure` (the code shares the
  request body with the credentials it protects, so plain HTTP exposes them —
  SECURITY.md corrected accordingly); the redeem audit records the real joiner
  id. Also: `status` probes peers concurrently (no more ~2s-per-dead-peer
  hang), `sync_with_peer` reads the peer's name from the bundle header it
  already pulled (no extra `/api/node` round-trip), default node names include
  the username unless it equals the host exactly, `path install` replaces a
  stale managed line instead of accumulating dead PATH entries, and `status`
  reports the resolved home.

## [1.3.0] — 2026-07-18

Multi-account hosts: several OS accounts on one machine, each with its own
store and console, discovering each other and sharing team memory with
explicit consent.

- **`agent-memory status`.** One view of this host and every connected node:
  login-service state (launchd/systemd/Task Scheduler), console reachability
  (with a warning when the port is answered by a DIFFERENT node), pid, store
  totals, and per-peer live state (online/offline via `/healthz`, policy,
  token presence, last_synced/last_result). `--json` for scripts.
- **`agent-memory neighbors`.** Same-host discovery: scans loopback ports for
  other AgentMemoryOS consoles via the unauthenticated `/healthz` (node name
  only — never memory data). `doctor` prints a hint when neighbors exist.
- **`agent-memory team invite <team>` / `agent-memory join <code> --url …`.**
  One-time pairing codes replace hand-carrying tokens and the mesh key: the
  invite (hash-only at rest, TTL, atomically single-use) is redeemed at the
  new `POST /api/pairing/redeem` endpoint — the only route exempt from bearer
  auth, because the code IS the credential and both payload directions are
  encrypted under it (same authenticated Fernet as sync bundles). The
  exchange swaps sync-scoped tokens both ways, registers team-scoped peers on
  both nodes, adds the joiner to the team, installs the inviter's mesh sync
  key (refusing mismatched meshes), and runs a first sync. Discovery never
  grants access — joining always requires a code from the other operator.
- **Windows service names are per-account.** Task Scheduler names are
  machine-global (unlike per-user launchd/systemd units), so two accounts
  installing the service overwrote each other; tasks are now
  `agent-memory-web-<username>` (uninstall also removes the legacy bare name).
- **`service install` picks and persists a free port.** If another instance
  already holds the configured port, install chooses the next free one and
  writes it to `instance.toml`, so login services from several accounts stop
  racing for port 8000 and peer URLs stay stable.
- `sync_with_peer` now records `last_synced/last_result` on the peer for every
  sync path (previously only the mesh loop did), so `status` is accurate.
- Migration 16: `pairing_invites` table.

## [1.2.0] — 2026-07-15

- **Native Hermes Agent memory-provider plugin.** `pip install agent-memory-os`
  inside a [NousResearch Hermes Agent](https://github.com/NousResearch/hermes-agent)
  (v0.18+) environment now exposes an `agent-memory-os` plugin (discovered via
  the `hermes_agent.plugins` entry point). Enable it and set
  `memory.provider: agent-memory-os` and Hermes gets: automatic ACL-filtered
  recall injected every turn (`prefetch`), `amos_search` / `amos_add` /
  `amos_share` tools (with team/project/global sharing), idempotent mirroring
  of built-in MEMORY.md/USER.md writes, subagent-delegation capture, and
  `hermes backup` coverage of the store. Local-first: no API key, no LLM, no
  network — `is_available()` is true the moment the package is installed.
  Profiles map to ACL identities automatically (`hermes-<profile>`), so
  multiple Hermes profiles and MCP agents (Claude Code, Codex) share one
  store with private/team/project boundaries intact. Cron/subagent contexts
  are read-only; provider failures degrade to "no memory this turn".
  See `docs/integrations/hermes-agent.md`.
- **`agent-memory hermes install`.** Hermes's `hermes memory setup|status`
  picker only discovers providers from plugin *directories*, so a pip install
  alone is invisible to it. The new command writes a two-file shim into
  `$HERMES_HOME/plugins/agent-memory-os/` (idempotent; `uninstall` removes
  it); the shim re-exports the provider from the installed package, so pip
  upgrades keep applying. After install: `hermes memory setup agent-memory-os`.

## [1.1.0] — 2026-07-14

- **Encrypted federation transport (app-layer).** When a shared mesh key is
  configured — `AGENT_MEMORY_SYNC_KEY` (env) or `<home>/sync_key`, minted with
  `agent-memory sync genkey` — sync bundles are wrapped in an authenticated
  `AMOSENC1:` envelope (Fernet: AES-128-CBC + HMAC-SHA256) on both the pull and
  push legs. The key is a **separate secret from the bearer token and never
  crosses the wire**, so memory content stays confidential even over plain HTTP
  or through a TLS-terminating proxy. A bundle encrypted under an unknown key is
  rejected, not merged. Encryption is opportunistic (engages only when a key is
  set) for smooth mesh upgrades. Ships in the new `secure-sync` extra (folded
  into `full`); `agent-memory-web` and the CLI both resolve the key.
- **Sync-scoped token tier.** `agent-memory token create --sync` mints a
  federation-only bearer token (`web_sync_token`, prefix `amos_sync_`) that
  authorizes **only** `GET /api/node`, `GET /api/sync/export`, and
  `POST /api/sync/import`. Hand this to a peer instead of the full admin token,
  so a peer credential can neither read nor mutate memory through the API. The
  Web API auth gate now installs whenever any token tier (full / read-only /
  sync) is configured.
- **Explicit TLS verification for HTTPS peers.** `https://` peer URLs now use an
  explicit certificate-verifying `ssl` context (system trust store + hostname
  check). The plain-HTTP warning now points at both the mesh key and a
  sync-scoped token.
- **`agent-memory-mcp` console entry point.** The MCP stdio server can also be
  launched as `agent-memory-mcp` (besides `python -m
  agent_memory_os.mcp_server`), enabling zero-install runs via
  `uvx --from "agent-memory-os[mcp]" agent-memory-mcp` and cleaner MCP-directory
  listings (e.g. Smithery).

## [1.0.5] — 2026-07-13

- **MCP can now create SHARED memories.** `memory_add` gains a `share` argument
  — `private` (default), `global`, `team`/`team:<id>`, `project`/`project:<id>`,
  or `agent:<id>` — so an agent can store team/project memory straight from the
  MCP tool instead of only private notes. Bare `team`/`project` resolve to the
  caller's own membership when unambiguous. A new **`memory_share`** tool changes
  an existing memory's visibility (owner-only; the change propagates over sync).
  This closes a real gap: previously everything added via MCP was private to its
  owner, so the "team memory" value prop was unreachable through the primary
  interface. Two agents pointed at the same home now share `team:`/`project:`
  memory the moment it's written.

## [1.0.4] — 2026-07-12

- **Richer MCP tool descriptions.** All 11 `memory_*` tools now carry a full
  docstring (purpose, when-to-use, behaviour/side-effects, ACL note, return
  shape) and a description on every parameter (via `pydantic.Field`), so MCP
  clients — and catalogs like Glama that score tool-definition quality — get
  clear, self-describing tools. No behaviour change; the tool set and signatures
  are unchanged.

## [1.0.3] — 2026-07-12

Docker packaging release — the published image is now the complete AgentMemoryOS.
No engine/SDK changes from 1.0.2.

- **Docker image is now complete and multi-mode.** The image installs the `full`
  extra (Web console + MCP server + turbovec + CLI), and the entrypoint dispatches
  on the first argument: `web` (default), `mcp` (the stdio MCP server), or any
  other args run the `agent-memory` CLI. One image, every surface. Verified end
  to end, including a real MCP introspection handshake against
  `docker run -i … mcp`. (`--build-arg EXTRAS=api` still gives a lean web-only build.)
- **docker-compose** (single + mesh) build the complete image too — dropped the
  `EXTRAS=api` override that was forcing web-only containers.

## [1.0.2] — 2026-07-12

Maturity pass for the 1.x line — docs, one opt-in feature, guards, and one fix.
Also the first release listed on the MCP Registry (the PyPI description now
carries the ownership marker, so the registry workflow can publish).

- **Fix (revocation staleness)**: the client's per-query recall cache was not
  invalidated by team/project membership changes, so a removed member could keep
  seeing a revoked team/project memory for a previously-run query until the cache
  evicted it. `add/remove_team_member`, `add/remove_project_member`, and
  `delete_team/delete_project` now clear the cache immediately (like
  `register_agent` already did). Found while writing the runnable example; covered
  by `tests/test_revocation_cache.py`.
- **`examples/`**: a runnable `team_memory.py` — three agents share one store under
  a hard ACL (private/team/project/global), a budgeted context pack, and instant
  re-scoping on member removal. Self-asserting, so it doubles as a smoke test.
- **MCP Registry manifest verified**: `server.json` is validated against the live
  registry schema and a real stdio MCP handshake confirms
  `python -m agent_memory_os.mcp_server` starts and lists the tools
  (`tests/test_server_json_and_mcp.py`). Fixed an over-length `description` that
  would have failed the registry publish.
- **`CONTRIBUTING.md`** + issue/PR templates (security routed to private reporting).

- **Importers** (`agent-memory import --from mem0|zep|chatgpt <export.json>`, and
  `agent_memory_os.importers.import_export`): best-effort migration from other
  memory systems. Deterministic ids (idempotent, no duplicates), private by
  default (`--visibility` to widen), source provenance. See `docs/IMPORTERS.md`.
- **Unencrypted-peer guard**: `add_peer` now warns when a peer URL is plain HTTP
  over the network (token + content would cross the wire in the clear); use
  https:// (TLS proxy/tunnel) for any non-localhost peer.
- **Security & governance docs**: `SECURITY.md` (private disclosure policy +
  honest known limitations) and `docs/THREAT_MODEL.md` (trust boundaries, the
  precise eventual/cooperative revocation guarantee, hardening checklist).
- **`COMPATIBILITY.md`**: the 1.x semver promise across the SDK API, CLI, HTTP
  API, forward-only schema migration, and the sync bundle format (v3, reads 1–3).
- **`docs/EMBEDDINGS.md`**: plug in fastembed/sentence-transformers via
  `TurbovecSemanticCandidateProvider.from_vectors()`, plus 10k→1M scale guidance.
- **README**: a one-line `claude mcp add` snippet and an animated console demo
  GIF; `docs/PROMOTION.md` holds ready-to-submit listing copy for MCP directories.

## [1.0.1] — 2026-07-12

Docs-only release to refresh the PyPI project page (the description is frozen
per version). No code changes.

- README: status badges (PyPI / Python / CI / Docker pulls / License), a top
  navigation bar, and a fact-based "How it compares" positioning table vs Mem0
  and Zep/Graphiti (architecture, not a benchmark).
- New 繁體中文 README (`README.zh-Hant.md`) with an English⇄中文 language switch.
- Web console dashboard screenshot in the README and the Docker Hub overview.

## [1.0.0] — 2026-07-12

First stable release. Everything below lands on top of v0.14.0's federated org
structure, and closes the trust-model, observability, and operability gaps that
a "memory system for AI-agent teams" needs to be run in production.

### Trust model — revocation & escalation (migration 15)
- **Revocation now propagates.** An independent ACL clock (`acl_updated_at`)
  carries share/revoke changes over sync WITHOUT restarting the decay/freshness
  clock. A post-hoc revoke retracts already-synced access on peers; a re-share
  converges back; an older incoming ACL never clobbers a newer local one.
- **Untrusted peers cannot escalate visibility.** Org-structure and ACL merges
  are authorized against the pushing peer's policy scope: a peer may only assert
  team/project membership within its own scope, may only *shrink* a memory's
  visibility (a revoke), never widen it, and cannot delete org structure it
  doesn't own. Anonymous HTTP pushes get no org mutations at all. Future-dated
  org/ACL timestamps are rejected so a forged clock can't pin state.
- Deterministic tie-break on equal-timestamp membership so nodes converge;
  member-removal cascades correctly to projects; deleting a team strips both
  team-grant schemes so a reused id can't resurrect access.
- `suggested_peer_policy(agent)` derives the tightest policy from local
  membership (advisory; the manual policy stays the enforced upper bound).

### Observability
- `GET /healthz` — integrity-aware readiness (200 ok / 503 degraded); the Docker
  HEALTHCHECK uses it.
- `GET /metrics` — Prometheus text format (aggregate counts only: memories,
  orphans, index drift, teams, projects, peers, peer errors, integrity).
- `agent-memory doctor` reports processes that predate the installed version.

### Updating & operations
- **`agent-memory update`** finishes the job: after a pip upgrade it restarts the
  running web console it owns via a self-written pidfile (never a `ps`-derived
  command — closes a local code-exec vector), reports MCP servers to restart in
  their host app, and (`update --check`) flags stale "disk-new/memory-old"
  processes. Docker deployments get image-pull guidance instead.
- `agent-memory service restart`; `agent-memory backup --keep N` (safe rotation
  that can never delete the live database); `agent-memory token create --readonly`
  (a GET-only web token tier).
- Ops/maintenance: orphan detection (owner- and existence-aware, so cleanup never
  deletes recoverable data), one-click orphan cleanup, manual reindex, vacuum.

### Web console
- Version badge (bottom-right), token-usage dashboard cards (total / top agent /
  team / project), a self-update button with version check, a membership-audit
  viewer, a graph scope filter, and a read-only-mode banner — all across 5 locales.

### Hardening & resilience
- Bundle import is fuzz-hardened (malformed lines roll back atomically; garbage
  fields are coerced, not executed). A CI `upgrade-path` job proves a DB written
  by the last published release migrates forward with data + integrity intact.
- Full code + security review (fan-out, two rounds) with reports under
  `docs/reviews/`; performance verified at 10k memories (add 0.17 ms, search
  <1 ms, context-pack 7.8 ms).

## [0.14.0] — 2026-07-11

**Federated org structure** (migration 14). Teams, projects, and their
membership now converge across nodes, so cross-node team/project ACL is
consistent — the missing piece that makes "team operation" correct in a mesh.

- **Convergent org sync**: bundles (v3) carry each team/project with an
  `updated_at` and its full member set. Import applies last-writer-wins on
  `updated_at` and REPLACES the member set, so additions AND removals converge.
  Deletions propagate via `org_tombstones` (a reused id can't resurrect a
  deleted team/project). Org export is scoped like memory (`full`/`shared` →
  all; `team:<id>` → that team + its projects; `project:<id>` → that project +
  its team). The subset invariant is preserved on import.
- **Membership audit** (`org_audit`): create/delete team & project and every
  member add/remove is recorded with actor + timestamp; readable via
  `client.org_audit_log()` and `GET /api/org/audit`. API member routes accept
  an `actor`.

**Ops & maintenance tooling.**

- **Orphan memories**: a memory scoped to a team/project with no members is
  visible to no one. `find_orphan_memories()` / `delete_orphan_memories()`,
  the `agent-memory maintenance orphans [--delete]` command, and a console
  **Maintenance** panel surface and clean them. Removing a team/project member
  warns (CLI + console) when it just orphaned memory.
- **`agent-memory maintenance`**: `scan` (health), `orphans`, `reindex`
  (rebuild FTS/semantic from the truth store), `vacuum` (reclaim disk +
  refresh planner stats) — plus `/api/maintenance/*` and console buttons.
- **`agent-memory update`**: detects the deployment (host `pip` vs Docker
  container) and OS, checks PyPI for a newer version, and either upgrades via
  pip or prints the `docker pull`/recreate steps. `--check` reports only.

**Docs.** README repositioned around **AI-agent team operation** (shared team &
project memory, federation) rather than single-agent recall.
- Review fixes from `docs/reviews/20260711-v0.13.0-review.md` (shipped in this
  release): `create_project` can't re-point a project to a different team;
  `register_agent(teams=None)` leaves membership untouched (metadata edits no
  longer wipe it); `to_project` reachable in the share API; the CLI reports
  domain errors cleanly; deleting a team/project revokes its orphaned
  visibility grant (no id-reuse resurrection).

## [0.13.0] — 2026-07-11

**First-class Teams & Projects** (migration 13). Teams and projects are now
real, manageable entities with explicit membership, so team-shared vs
project-shared memory is scoped and synced correctly.

- **Membership model**: `teams`, `team_members`, `projects` (belongs to a team),
  `project_members` — the join tables are authoritative for ACL. A project's
  members must be a subset of its team's; leaving a team cascades out of its
  projects; deleting a team removes its projects; removing an agent clears all
  its memberships. Existing flat `agent.teams` are backfilled into `team_members`.
- **`project:<id>` ACL**: `visibility: ["team:apollo"]` reaches every team
  member; `visibility: ["project:apollo-web"]` reaches only that project's
  members. Resolved through membership, cached with the same 30s TTL as teams.
- **Scoped sync**: `export_bundle(project=…)` and a `project:<id>` peer policy
  bundle only a project's shared memory (to project members' nodes), so project
  memory never reaches non-members. `share_memory`/`revoke_share` gain `to_project`.
- **Management everywhere**: WebUI **Teams** tab (create a team and pick node
  members; create projects under it and pick members from the team), CLI
  `agent-memory team|project …`, and `/api/teams`, `/api/projects` (+members).

## [0.12.1] — 2026-07-11

- **Web console login fix**: the console now shows a proper in-page token
  login form instead of relying on a `prompt()` dialog (which browsers could
  suppress and which stacked up under the page's parallel API calls, leaving
  users unable to log in). A 401 clears the stored token and reveals the login
  form; entering the token stores it and reloads.

## [0.12.0] — 2026-07-11

**Multiple instances on one machine.** Run several Agent Memory OS instances
side by side (each with its own `--home`) without port clashes, and identify
each other by name during sync.

- **Instance settings** `<home>/instance.toml` (`[instance]` → `node_name`,
  `host`, `port`) — all optional, sensible defaults; `node_name` defaults to a
  host+home label so co-located instances don't collide.
- **Auto port selection**: `agent-memory-web` resolves the port as CLI `--port`
  > `instance.toml` > 8000, and if it's taken, advances to the next free port
  (`--strict-port` to fail instead). It prints the bound URL and node name.
- **Node identity for sync (migration 12)**: `GET /api/node` advertises this
  instance's `node_name`; a registered peer stores a friendly `name` (auto-
  fetched from the peer on add, or `peers add --name` / the console field), so
  the peer list and sync results show names instead of bare URLs. Bundles carry
  the origin `node_name` in their header.
- **`agent-memory node`** shows/sets `node_name`/`host`/`port`; `service install`
  and the console honour the configured port; the console header shows this
  instance's node name.
- **Fix**: port availability is probed by connection, not bind — a restarted
  server re-binds its usual port instead of drifting (TIME_WAIT on POSIX,
  SO_REUSEADDR on Windows), which had surfaced as "failed to fetch" in the
  console. The graph view also degrades gracefully on unexpected data.

**Docker.** `Dockerfile` + `docker-compose.yml` run the Web console with memories
persisted in a `/data` volume; a two-node `docker-compose.mesh.yml` shows
instances syncing by name. The image binds `0.0.0.0` with `--strict-port`,
auto-generates a token on first run (secure by default), and configures entirely
through env (`AGENT_MEMORY_WEB_TOKEN`, `AGENT_MEMORY_NODE_NAME`,
`AGENT_MEMORY_WEB_HOST/PORT`) — env now overrides `instance.toml`. Semantic +
MCP are opt-in via `--build-arg EXTRAS=full`. See `docs/DOCKER.md`.

## [0.11.1] — 2026-07-11

Correctness, ranking, and privacy fixes — review batches 2 and 3, closing every
remaining finding (D5–D15) of `docs/reviews/20260711-v0.10.0-review.md`.

- **Ranking (D5)**: the authority (bedrock) track no longer double-applies
  importance/confidence/freshness — it fuses raw lexical relevance, so a
  matching authority memory is scored once.
- **Lossless archive/restore (D7, migration 10)**: archived memories keep their
  association edges in a link archive; `restore_archived` re-attaches every edge
  whose other endpoint is live again (was: restored at degree 0).
- **De-identified share privacy (D10)**: the recipient-visible copy no longer
  carries the owner's id in its audit row, and owner-identifying tags are dropped.
- **CJK token estimate (D12)**: `approx_tokens` counts CJK codepoints at ~1
  token each, so an orchestrated pack of Japanese/Chinese text no longer blows
  the caller's real token budget 4–6×.
- **Token file (D13)**: written 0600 atomically (no world-readable window, safe
  concurrent rotate).
- **Contradiction guard (D14)**: `record_recall(create_colinks=True)` never lays
  a `co_recalled` edge over a pair joined only by `supersedes`.
- **Team-ACL cache TTL (D8)**: cross-process membership changes are picked up
  within 30s instead of persisting until restart.
- **Configured decay base (D6, migration 11)**: feedback tuning scales the
  memory's configured half-life (default-for-type, or a value you set), so an
  explicit `decay_half_life_days` is no longer clobbered on every retention pass.
- **Sync no longer freezes the server (D9)**: `/api/sync/run` passes the shared
  lock instead of holding it — DB access stays serialized, but a slow/unreachable
  peer's HTTP round-trip never blocks other requests.
- **Orchestrator fallthrough (D11)**: a top hit claimed by the warnings/procedures
  section but dropped by that section's token cap now falls through to the task
  section instead of vanishing from the pack.
- **agents.toml partial entries (D15)**: re-applying a `[agents.<id>]` table that
  sets only some fields keeps the console-set values for the rest, instead of
  resetting them to defaults on every open.

## [0.11.0] — 2026-07-11

**Federation trust model** (migration 9) — resolves review findings D1–D4.

- **Per-peer sync policy**: each peer declares what leaves for it — `shared`
  (default: everything except private `visibility=[]` memories), `full` (whole
  store, own trusted replica nodes only), or `team:<id>` (one project). Private
  memories never leave the machine under `shared`/`team`. Existing peers migrate
  to `full` (no behaviour change); new peers default to `shared`.
- **HTTP export is always `shared`-scoped**: `GET /api/sync/export` never serves
  private memories (it cannot authenticate the puller); full private replication
  flows only over the authenticated push leg between own nodes.
- **Tombstones**: deletions and owner purges record a tombstone that propagates
  over sync, so a deleted memory no longer resurrects from a peer that still
  holds it.
- **Provenance + anti-impersonation**: imports from a semi-trusted peer record
  `source.synced_from` and may not create a memory authored by one of your local
  registered agents.
- **Convergent LWW**: conflict timestamps are normalized (`Z` vs `+00:00`) and a
  same-second edit is resolved by a deterministic content tie-break, so two
  nodes converge instead of diverging silently.
- CLI `peers add --policy`, `PeerRequest.policy`, and a console policy selector
  (with a warning that `full` shares private memory) keep parity across surfaces.

## [0.10.1] — 2026-07-11

Security & correctness fixes from the full v0.10.0 review
(`docs/reviews/20260711-v0.10.0-review.md`).

- **MCP identity escape (security)**: `memory_orchestrate_context` and
  `memory_recall_feedback` no longer accept a caller-supplied
  `requester_agent_id` that overrode the env-pinned agent identity.
- **Web ACL gate**: `GET /api/memories/{id}` and `/links` now enforce the
  same visibility gate as `/api/search` (new `get_visible()`).
- **Right-to-forget**: `purge_owner` now also destroys cold-archive rows and
  the recall/audit logs, so purged content is not restorable.
- `resonance_search` no longer raises on a found seed; LLM link extractor
  survives a non-string reply; `agents.toml` rejects a string `teams` and
  validates the whole file before applying (no half-registered fleet).
- share/revoke no longer reset the freshness/decay clock; the auto semantic
  index rebuilds after an in-place same-second edit; `rotate_snapshots` never
  archives pinned snapshots; `import_bundle` rolls back on a corrupt line;
  bundle temp files are written UTF-8 (Windows non-ASCII).

## [0.10.0] — 2026-07-11

- **Validation milestone**: `docs/VALIDATION_PLAN.md` (G1 security / G2
  functional / G3 performance / G4 deployment gate matrix) supersedes the
  v0.2.x-era Hermes shadow gates; reproducible harness
  `scripts/validation_run.py` generates professional reports into
  `docs/reports/` and runs in CI (`--quick`).
- First full run: **PASS** — 12/12 security, 10/10 functional, 13/13
  performance on a 5k-memory fleet corpus (search p95 11.9 ms,
  orchestrate p95 20.4 ms, writes 8.3k/s).
- **Ranking fix found by validation**: the query-independent authority
  (bedrock) track could crowd resonance results out of the result window
  at scale; its share is now capped at limit/4.
- Documentation sweep: new `docs/USER_GUIDE.md` (full CLI/API/MCP
  reference), CHANGELOG as release history, SPEC current through v0.9,
  INSTALLATION rewritten, status docs refreshed.

## [0.9.0] — 2026-07-11

- **Fleet as code**: `<home>/agents.toml` declares the whole multi-agent,
  multi-project fleet; re-applied on every store open (file-authoritative
  for listed agents, manual registrations untouched).
- **Console i18n**: English, 繁體中文, 简体中文, 日本語, 한국어 — auto-detected,
  live-switchable, persisted.

## [0.8.0] — 2026-07-11

- **Agent registry** (migration 8): agents are first-class entities (id,
  kind, teams, last-seen) with a console Agents tab and `/api/agents`.
- **Team auto-resolution ACL**: registered team memberships resolve inside
  the ACL hard gate — `team:<project>` memory visible to every member with
  zero per-call wiring.
- **Per-agent MCP identity**: `AGENT_MEMORY_AGENT_ID` gives each connected
  agent its own owner/requester identity.
- **Project-scoped sync**: `sync export --team <project>`.

## [0.7.0] — 2026-07-10

- **Mesh federation** (migration 7): peer registry, `agent-memory peers`,
  `sync auto` bidirectional convergence with per-peer failure isolation,
  console peer management.
- **LLM extraction plumbing**: `make_llm_link_extractor(fn)` wraps any
  completion callable into a consolidation link extractor with defensive
  parsing.

## [0.6.0] — 2026-07-10

- **Memory negotiation** (migration 5): owner-only `share_memory` /
  `revoke_share`, de-identified copies, per-memory audit trail.
- **Federated sync**: portable JSONL bundles + peer HTTP transport
  (`/api/sync/export|import`, `sync pull/push`) with deterministic merges.
- **Adaptive forgetting** (migration 6): helpful/unhelpful feedback tunes
  decay half-lives (`base × clamp(√((1+h)/(1+u)), 0.5, 4)`).

## [0.5.0] — 2026-07-10

- **Cross-OS login service**: `agent-memory service install` — launchd /
  systemd user unit / Task Scheduler; per-user, auto-restart, `--dry-run`.
- **Three-OS CI**: Ubuntu, macOS, Windows × Python 3.11–3.13.

## [0.4.0] — 2026-07-10

- **Dynamic context orchestration**: `orchestrate_context()` splits the
  token budget across session / bedrock / warnings / procedures / task
  buckets; proactive recall; task-type emphasis; session iterative
  deepening (migration 4); snapshot rotation and `snapshot_diff()`.

## [0.3.0] — 2026-07-10

- **Schema migrations** (versioned, forward-only) and
  `agent-memory check` integrity verification.
- **Cold-archive retention** (migration 3): expired/decayed memories become
  restorable archives; pinned/authority never archived by decay.
- **Auto semantic recall**: `MemoryClient(semantic="auto")` — self-syncing
  turbovec index over a dependency-free hashing embedder.
- `agent-memory backup/restore` (WAL-safe online backups).

## [0.2.4] — 2026-07-10

- Console dashboard, in-place memory editing (`PATCH`), agent purge danger
  zone, converging-evidence resonance, token/doctor CLI, official logo.

## [0.2.3] — 2026-07-10

- First public release: memory association layer (`memory_links`,
  ACL-safe resonance recall, link decay, hub damping, supersedes),
  Hebbian reinforcement loop with negative feedback, persisted recall
  profiles, write-side consolidation, requester-aware Web console,
  MCP server, Apache-2.0.

## Pre-release lineage

- **2026-06-12 → 06-13** — Dynamic Context Orchestration prototypes
  (`ContextSnapshot` schema, offload/reload, precision test suite) on the
  Hermes workstream, later merged and hardened in 0.4.0.
- **2026-06-05 → 06-09** — v0.2.x internal baselines: SQLite+FTS5 source of
  truth, requester-aware ACL, truth arbitration, retrieval foundation,
  turbovec sidecar validation (`v0.1.0-stable` tag).
