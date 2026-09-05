from .constants import DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS memories (
  id TEXT PRIMARY KEY,
  owner TEXT NOT NULL,
  scope TEXT NOT NULL,
  type TEXT NOT NULL,
  content TEXT NOT NULL,
  summary TEXT NOT NULL,
  tags TEXT NOT NULL DEFAULT '[]',
  visibility TEXT NOT NULL DEFAULT '[]',
  source TEXT NOT NULL DEFAULT '{{}}',
  confidence REAL NOT NULL DEFAULT 0.8,
  importance REAL NOT NULL DEFAULT 0.5,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  expires_at TEXT,
  decay_policy TEXT NOT NULL DEFAULT 'exponential',
  decay_half_life_days REAL NOT NULL DEFAULT {DEFAULT_DECAY_HALF_LIFE_FALLBACK_DAYS},
  last_accessed_at TEXT,
  access_count INTEGER NOT NULL DEFAULT 0,
  pinned INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
  id UNINDEXED,
  owner UNINDEXED,
  scope,
  type,
  content,
  summary,
  tags,
  tokenize = 'unicode61'
);
CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
  INSERT INTO memories_fts(id, owner, scope, type, content, summary, tags)
  VALUES (new.id, new.owner, new.scope, new.type, new.content, new.summary, new.tags);
END;
CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
  DELETE FROM memories_fts WHERE id = old.id;
END;
CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
  DELETE FROM memories_fts WHERE id = old.id;
  INSERT INTO memories_fts(id, owner, scope, type, content, summary, tags)
  VALUES (new.id, new.owner, new.scope, new.type, new.content, new.summary, new.tags);
END;
CREATE TABLE IF NOT EXISTS memory_links (
  src_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  dst_id TEXT NOT NULL REFERENCES memories(id) ON DELETE CASCADE,
  relation TEXT NOT NULL DEFAULT 'related_to',
  weight REAL NOT NULL DEFAULT 0.5,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  last_activated_at TEXT,
  activation_count INTEGER NOT NULL DEFAULT 0,
  source TEXT NOT NULL DEFAULT '{{}}',
  PRIMARY KEY (src_id, dst_id, relation)
);
CREATE INDEX IF NOT EXISTS memory_links_src ON memory_links(src_id);
CREATE INDEX IF NOT EXISTS memory_links_dst ON memory_links(dst_id);
CREATE TABLE IF NOT EXISTS recall_profiles (
  agent_id TEXT PRIMARY KEY,
  type_weights TEXT NOT NULL DEFAULT '{{}}',
  scope_weights TEXT NOT NULL DEFAULT '{{}}',
  updated_at TEXT NOT NULL
);
"""
