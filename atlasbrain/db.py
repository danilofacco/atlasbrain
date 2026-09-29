import sqlite3
from pathlib import Path

import numpy as np

from .config import data_dir

SCHEMA = """
CREATE TABLE IF NOT EXISTS embedding_cache(
    key TEXT PRIMARY KEY, vector BLOB NOT NULL, touched REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE IF NOT EXISTS notes(
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT,
    kind TEXT,
    mtime REAL,
    size INTEGER,
    hash TEXT,
    frontmatter TEXT,
    aliases TEXT,
    preview TEXT,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS chunks(
    id INTEGER PRIMARY KEY,
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    ord INTEGER,
    heading TEXT,
    text TEXT,
    embedding BLOB
);
CREATE INDEX IF NOT EXISTS idx_chunks_note ON chunks(note_id);
CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
    title, heading, text, content='chunks', content_rowid='id', tokenize='unicode61 remove_diacritics 2'
);
CREATE TABLE IF NOT EXISTS links(
    src INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    target TEXT NOT NULL,
    dst INTEGER,
    kind TEXT NOT NULL,
    weight REAL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_links_src ON links(src);
CREATE INDEX IF NOT EXISTS idx_links_dst ON links(dst);
CREATE TABLE IF NOT EXISTS tags(
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tag TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_tags_tag ON tags(tag);
CREATE INDEX IF NOT EXISTS idx_tags_note ON tags(note_id);
CREATE TABLE IF NOT EXISTS simbolos(
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    nome TEXT NOT NULL, tipo TEXT, linha INTEGER, pai TEXT
);
CREATE INDEX IF NOT EXISTS idx_simbolos_nome ON simbolos(nome COLLATE NOCASE);
CREATE INDEX IF NOT EXISTS idx_simbolos_note ON simbolos(note_id);
CREATE TABLE IF NOT EXISTS porques(
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    etiqueta TEXT, texto TEXT, linha INTEGER
);
CREATE INDEX IF NOT EXISTS idx_porques_note ON porques(note_id);
CREATE TABLE IF NOT EXISTS refs(
    note_id INTEGER NOT NULL REFERENCES notes(id) ON DELETE CASCADE,
    tipo TEXT NOT NULL, alvo TEXT NOT NULL, linha INTEGER, quem TEXT
);
CREATE INDEX IF NOT EXISTS idx_refs_note ON refs(note_id);
"""


def connect(vault: Path) -> sqlite3.Connection:
    con = sqlite3.connect(data_dir(vault) / "index.db", timeout=60, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(SCHEMA)
    cols = {r[1] for r in con.execute("PRAGMA table_info(links)")}
    if "conf" not in cols:  # procedência de cada aresta
        con.execute("ALTER TABLE links ADD COLUMN conf TEXT")
        con.execute("ALTER TABLE links ADD COLUMN detalhe TEXT")
    _migrar_armazenamento(con)
    return con


def _migrar_armazenamento(con) -> None:
    """Índices antigos guardavam o texto duas vezes (tabela + FTS) e vetores em float32. Converte no lugar,
    sem reprocessar arquivos nem recalcular embeddings."""
    if "title" not in {r[1] for r in con.execute("PRAGMA table_info(chunks)")}:
        con.execute("ALTER TABLE chunks ADD COLUMN title TEXT")
        con.execute("UPDATE chunks SET title=(SELECT title FROM notes WHERE notes.id=chunks.note_id)")
    sql = con.execute("SELECT sql FROM sqlite_master WHERE name='chunks_fts'").fetchone()
    if sql and "content=" not in sql[0]:
        con.execute("DROP TABLE chunks_fts")
        con.execute("CREATE VIRTUAL TABLE chunks_fts USING fts5(title, heading, text, content='chunks', "
                    "content_rowid='id', tokenize='unicode61 remove_diacritics 2')")
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    if get_meta(con, "vetores") != "f16":
        for tabela in ("chunks", "notes"):
            rows = con.execute(f"SELECT id, embedding FROM {tabela} WHERE embedding IS NOT NULL").fetchall()
            con.executemany(f"UPDATE {tabela} SET embedding=? WHERE id=?",
                            [(np.frombuffer(r[1], dtype=np.float32).astype(np.float16).tobytes(), r[0]) for r in rows])
        set_meta(con, "vetores", "f16")
        con.commit()
        con.execute("VACUUM")


def get_meta(con, key: str, default: str | None = None) -> str | None:
    row = con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return row[0] if row else default


def set_meta(con, key: str, value) -> None:
    con.execute("INSERT OR REPLACE INTO meta(key, value) VALUES(?, ?)", (key, str(value)))


def to_blob(v: np.ndarray) -> bytes:
    return np.asarray(v, dtype=np.float16).tobytes()  # metade do espaço; a perda não muda o ranking


def from_blob(b: bytes) -> np.ndarray:
    return np.frombuffer(b, dtype=np.float16).astype(np.float32)
