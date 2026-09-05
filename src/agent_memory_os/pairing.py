"""Team pairing: one-time invite codes that bootstrap a memory-sharing peer.

The problem this solves: connecting two nodes today means hand-carrying two
secrets (a sync-scoped bearer token and, for encrypted meshes, the shared
sync key) and running `peers add` on both sides. Pairing collapses that into
one explicit consent exchange:

  node A (existing):   agent-memory team invite apollo
                         → prints a one-time code (TTL, single-use)
  node B (joining):    agent-memory join <code> --url http://127.0.0.1:8001
                         → both sides end up with a team-scoped peer entry,
                           B's agent is added to the team, and B receives
                           A's sync key (if any) so encryption engages.

Security model:
- Only the SHA-256 hash of the code is stored; the code itself is shown once.
- Redemption is atomic and single-use (db.consume_pairing_invite); expired,
  used, and unknown codes are indistinguishable to the caller. The code is
  validated and the envelope decrypted BEFORE the invite is consumed, and the
  membership + peer writes happen in one DB transaction, so a malformed
  request neither burns the code nor leaves half-joined state.
- The redeem bodies are Fernet-encrypted UNDER THE CODE (crypto.py), which
  keeps the tokens/sync-key out of URL/header access logs. NOTE: the code
  travels in the same POST body (the server needs it to decrypt), so this is
  NOT confidentiality against a full-body network observer — for a join
  beyond loopback, use TLS. `join_with_code` refuses a non-loopback http://
  target unless `allow_insecure=True`.
- Joining is never automatic: discovery (discovery.py) only *finds* nodes;
  membership always requires a code issued by the other node's operator.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import urllib.error
import urllib.request
from typing import Any

from . import crypto, tokens
from .constants import PAIRING_INVITE_TTL_SECONDS, PAIRING_REDEEM_TIMEOUT_SECONDS

CODE_PREFIX = "amos_join_"
DEFAULT_TTL_SECONDS = PAIRING_INVITE_TTL_SECONDS
REDEEM_PATH = "/api/pairing/redeem"


def _hash_code(code: str) -> str:
    return hashlib.sha256(code.strip().encode("utf-8")).hexdigest()


def issue_invite(client: Any, team_id: str, *, ttl_seconds: int = DEFAULT_TTL_SECONDS) -> dict:
    """Mint a one-time pairing code for `team_id` (store keeps only its hash)."""
    code = CODE_PREFIX + secrets.token_urlsafe(24)
    record = client.store.create_pairing_invite(
        team_id, _hash_code(code), ttl_seconds=ttl_seconds,
    )
    return {"code": code, "team_id": team_id, "expires_at": record["expires_at"]}


# --------------------------------------------------------------------------- #
# Server side (runs inside the inviter's web app)
# --------------------------------------------------------------------------- #

def redeem_invite(
    client: Any,
    envelope: str,
    code: str,
    *,
    home: str | None = None,
    self_node_name: str = "",
    self_agent_id: str = "",
) -> dict:
    """Validate + consume an invite and swap credentials with the joiner.

    `envelope` is the joiner's request payload encrypted under the code:
      {node_name, agent_id, url, sync_token}   (their token, for us)
    Returns the response payload (NOT yet encrypted):
      {team_id, node_name, agent_id, sync_token, sync_key?}  (our creds, for them)

    `agent_id` in the response is OUR identity, so the joiner can register us
    as a team member/agent locally instead of waiting for sync to converge.

    Raises ValueError on any invalid/expired/used code or undecryptable
    envelope — callers map that to a single opaque 403.
    """
    # Decrypt and validate BEFORE consuming, so a malformed request cannot
    # burn a still-valid code. Require a real AMOSENC1 envelope — a plaintext
    # body is rejected so the confidentiality control is not silently optional.
    if not crypto.is_encrypted(envelope):
        raise ValueError("pairing envelope must be encrypted")
    try:
        request = json.loads(crypto.decrypt_bundle(envelope, code))
    except Exception as exc:  # noqa: BLE001 - opaque failure to caller
        raise ValueError(f"undecryptable pairing envelope: {exc}") from exc

    agent_id = str(request.get("agent_id") or "").strip()
    joiner_url = str(request.get("url") or "").strip()
    joiner_name = str(request.get("node_name") or "").strip() or agent_id
    joiner_token = str(request.get("sync_token") or "").strip() or None
    if not agent_id:
        raise ValueError("pairing request missing agent_id")

    # Now consume the invite atomically. Only after a wrong/expired/used code
    # is ruled out (and the envelope proven well-formed) does the code burn.
    invite = client.store.consume_pairing_invite(_hash_code(code), redeemed_by=agent_id)
    if invite is None:
        raise ValueError("invalid, expired, or already-used pairing code")
    team_id = str(invite["team_id"])

    # Membership + peer registration in ONE transaction: either the joiner is
    # fully wired up (team member + peer) or nothing changed.
    client.store.join_team_and_register_peer(
        team_id, agent_id,
        peer_url=joiner_url, peer_token=joiner_token, peer_name=joiner_name,
    )

    # Hand back OUR credentials: a sync-scoped token (mint on first use —
    #    never the admin token) and the mesh key so encryption engages.
    own_sync_token = tokens.load_token(home, tier="sync")
    if not own_sync_token:
        own_sync_token = tokens.create_token(home, tier="sync")
    response: dict[str, Any] = {
        "team_id": team_id,
        "node_name": self_node_name,
        "agent_id": self_agent_id or self_node_name,
        "sync_token": own_sync_token,
    }
    sync_key = crypto.load_sync_secret(home)
    if sync_key:
        response["sync_key"] = sync_key
    return response


def encrypt_payload(payload: dict, code: str) -> str:
    return crypto.encrypt_bundle(json.dumps(payload, ensure_ascii=False), code)


def decrypt_payload(envelope: str, code: str) -> dict:
    return json.loads(crypto.decrypt_bundle(envelope, code))


# --------------------------------------------------------------------------- #
# Client side (the joining node)
# --------------------------------------------------------------------------- #

def _post_redeem(url: str, body: dict, *, timeout: int = PAIRING_REDEEM_TIMEOUT_SECONDS) -> dict:
    """POST the redeem request. Module-level so tests can bridge to a TestClient."""
    request = urllib.request.Request(
        url.rstrip("/") + REDEEM_PATH,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        return json.loads(response.read().decode("utf-8"))


def _is_loopback(url: str) -> bool:
    from urllib.parse import urlparse

    host = (urlparse(url).hostname or "").lower()
    return host in ("localhost", "127.0.0.1", "::1", "0.0.0.0") or host.endswith(".localhost")


def join_with_code(
    client: Any,
    code: str,
    url: str,
    *,
    agent_id: str,
    my_url: str = "",
    node_name: str = "",
    home: str | None = None,
    allow_insecure: bool = False,
) -> dict:
    """Redeem `code` against the inviter at `url` and wire up sharing locally.

    On success both sides hold a team-scoped peer entry for each other and
    this node has the inviter's sync token (and mesh key, when the inviter
    uses one). Returns a report dict; raises ValueError on refusal.

    For a non-loopback target the URL must be https:// (the code and the
    credential-bearing envelope share one HTTP body, so plain HTTP exposes
    them to any on-path observer) unless `allow_insecure=True`.
    """
    code = code.strip()
    if not code.startswith(CODE_PREFIX):
        raise ValueError(f"pairing codes start with {CODE_PREFIX!r}")
    if (url.startswith("http://") and not _is_loopback(url)
            and not allow_insecure):
        raise ValueError(
            "refusing to send a pairing code over plain HTTP to a non-local "
            f"host ({url}) — the code and credentials share one request body. "
            "Use an https:// URL, or pass --insecure to override on a trusted "
            "network.")

    own_sync_token = tokens.load_token(home, tier="sync")
    if not own_sync_token:
        own_sync_token = tokens.create_token(home, tier="sync")

    request_payload = {
        "agent_id": agent_id,
        "node_name": node_name or agent_id,
        "url": my_url,
        "sync_token": own_sync_token,
    }
    # The code identifies the invite server-side (it is single-use and dies
    # with this exchange); the envelope keeps both sides' tokens and the mesh
    # key out of access logs and proxy captures.
    try:
        reply = _post_redeem(
            url, {"code": code, "envelope": encrypt_payload(request_payload, code)},
        )
    except urllib.error.HTTPError as exc:  # type: ignore[attr-defined]
        raise ValueError(
            "pairing refused (invalid, expired, or already-used code)"
            if exc.code in (400, 403) else f"pairing failed: HTTP {exc.code}"
        ) from exc

    payload = decrypt_payload(str(reply.get("envelope") or ""), code)
    team_id = str(payload["team_id"])
    their_token = str(payload.get("sync_token") or "") or None
    their_name = str(payload.get("node_name") or "")
    their_agent_id = str(payload.get("agent_id") or "").strip()

    client.store.touch_agent(agent_id)
    client.store.add_peer(
        url, token=their_token, policy=f"team:{team_id}", name=their_name,
    )
    # Record the joined team locally so it is visible immediately (Teams tab,
    # ACL) instead of only after org structure converges over sync: create the
    # team, add ourselves, and register the inviter as a team member/agent.
    client.store.create_team(team_id)
    client.store.add_team_member(team_id, agent_id, actor="pairing-join")
    if their_agent_id and their_agent_id != agent_id:
        client.store.register_agent(
            their_agent_id, display_name=their_name or their_agent_id, kind="custom")
        client.store.add_team_member(team_id, their_agent_id, actor="pairing-join")

    key_installed = False
    their_key = str(payload.get("sync_key") or "")
    if their_key:
        local_key = crypto.load_sync_secret(home)
        if local_key is None:
            crypto.save_sync_secret(home, their_key)
            key_installed = True
        elif local_key != their_key:
            raise ValueError(
                "the inviter uses a different sync key than this node — "
                "meshes must share ONE key; resolve manually (agent-memory sync genkey docs)"
            )

    return {
        "team_id": team_id,
        "peer_url": url.rstrip("/"),
        "peer_name": their_name,
        "sync_key_installed": key_installed,
    }
