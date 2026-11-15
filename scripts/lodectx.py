#!/usr/bin/env python3
"""
lodectx.py — Portable Append-Only SQLite Lineage Engine & Lode Context CLI
Database: docs/.context/lode.db
Configuration: docs/lode.json
100% Python 3 Standard Library (Zero external dependencies).

Subcommands:
  init        Initialize SQLite WAL schema (init db) or scaffold a template (init prd|adr|exp|heur)
  ingest      Validate YAML frontmatter + headings, upsert node & edges, and sync docs
  brief       Emit token-efficient progressive-disclosure briefing for <domain>
  trace       Traverse upstream & downstream lineage graph for <id> via recursive CTE
  search      Sub-millisecond FTS5 BM25 search across active knowledge nodes
  add-node    Insert or update a knowledge node directly via CLI
  add-edge    Append a directed lineage edge directly via CLI
  sync-docs   Deterministically render LODE_MAP.md, experiments/INDEX.md, EXP-*.md headers,
              domain Lode footers, and archive ledgers from lode.db
  stats       Print comprehensive node, lineage, and startup token-budget telemetry
"""

import argparse
import datetime
import json
import pathlib
import re
import sqlite3
import sys
import time
from typing import Any, Dict, List, Tuple

try:
    import yaml
except ImportError:
    yaml = None

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
DOCS_DIR = REPO_ROOT / "docs"
CONTEXT_DIR = DOCS_DIR / ".context"
DB_PATH = CONTEXT_DIR / "lode.db"
CONFIG_PATH = DOCS_DIR / "lode.json"

DEFAULT_CONFIG: Dict[str, Any] = {
    "project_name": REPO_ROOT.name,
    "startup_token_budget": 7500,
    "startup_files": [
        ".agents/GEMINI.md",
        "docs/STATE_SUMMARY.md",
        "docs/LODE_MAP.md",
        "docs/INVARIANTS.md",
        "docs/TASKS.md",
    ],
    "global_guardrail_domains": ["security", "infra", "perf"],
    "domains": {
        "general": "Cross-cutting architecture, tooling, and repository conventions",
        "core": "Core domain logic and state machines",
        "db": "Database schemas, migrations, and query performance",
        "api": "External/internal APIs, serialization, and contracts",
        "auth": "Authentication, authorization, and session boundaries",
        "ui": "Frontend components, state management, and UX",
        "infra": "Containers, CI/CD, deployment, and runtime topology",
        "perf": "Benchmarks, latency budgets, concurrency, and resource limits",
        "security": "Zero-trust boundaries, secrets management, and hardening",
    },
}

VALID_NODE_TYPES = ("adr", "heuristic", "experiment", "change", "task", "prd")
VALID_TIERS = ("TIER_1_ISOLATED", "TIER_2_CONTAMINATED", "QUEUED_SPEC", "N/A")
VALID_VERIFIED_STATUSES = ("VERIFIED", "DRIFTED", "HISTORICAL", "PENDING", "SUPERSEDED", "NEEDS_RETEST")
VALID_RELATIONS = (
    "SUPERSEDES",
    "AMENDS",
    "VALIDATED_BY",
    "INVALIDATED_BY",
    "CONTAMINATED_BY",
    "DEPENDS_ON",
)


def load_config() -> Dict[str, Any]:
    if CONFIG_PATH.exists():
        try:
            return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[WARN] Failed to parse {CONFIG_PATH}: {e}. Using defaults.", file=sys.stderr)
    return DEFAULT_CONFIG


SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS nodes (
    id TEXT PRIMARY KEY,
    node_type TEXT NOT NULL CHECK(node_type IN ('adr', 'heuristic', 'experiment', 'change', 'task', 'prd')),
    domain TEXT NOT NULL,
    title TEXT NOT NULL,
    one_liner TEXT NOT NULL,
    body_md TEXT NOT NULL,
    methodology_tier TEXT NOT NULL DEFAULT 'N/A' CHECK(methodology_tier IN (
        'TIER_1_ISOLATED', 'TIER_2_CONTAMINATED', 'QUEUED_SPEC', 'N/A'
    )),
    live_verified_status TEXT NOT NULL DEFAULT 'VERIFIED',
    live_verified_at TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS lineage_edges (
    edge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_id TEXT NOT NULL REFERENCES nodes(id),
    relation TEXT NOT NULL CHECK(relation IN (
        'SUPERSEDES', 'AMENDS', 'VALIDATED_BY', 'INVALIDATED_BY', 'CONTAMINATED_BY', 'DEPENDS_ON'
    )),
    to_id TEXT NOT NULL REFERENCES nodes(id),
    reason TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(from_id, relation, to_id)
);

CREATE INDEX IF NOT EXISTS idx_nodes_domain_type ON nodes(domain, node_type);
CREATE INDEX IF NOT EXISTS idx_edges_from ON lineage_edges(from_id);
CREATE INDEX IF NOT EXISTS idx_edges_to ON lineage_edges(to_id);

DROP VIEW IF EXISTS v_active_knowledge;
CREATE VIEW v_active_knowledge AS
SELECT
    n.id,
    n.node_type,
    n.domain,
    n.title,
    n.one_liner,
    n.body_md,
    n.methodology_tier,
    n.live_verified_status,
    n.live_verified_at,
    n.created_at,
    CASE
        WHEN n.live_verified_status = 'SUPERSEDED' OR EXISTS (
            SELECT 1 FROM lineage_edges e
            WHERE e.to_id = n.id AND e.relation IN ('SUPERSEDES', 'INVALIDATED_BY')
        ) THEN 'SUPERSEDED'
        WHEN n.methodology_tier = 'TIER_2_CONTAMINATED' OR n.live_verified_status = 'NEEDS_RETEST' OR EXISTS (
            SELECT 1 FROM lineage_edges e
            WHERE (e.from_id = n.id OR e.to_id = n.id) AND e.relation = 'CONTAMINATED_BY'
        ) THEN 'NEEDS_RETEST'
        WHEN n.node_type = 'task' AND n.live_verified_status = 'HISTORICAL' THEN 'COMPLETED'
        ELSE 'ACTIVE'
    END AS effective_status,
    (
        SELECT GROUP_CONCAT(e.from_id || ' [' || e.relation || ': ' || e.reason || ']', '; ')
        FROM lineage_edges e
        WHERE e.to_id = n.id AND e.relation IN ('SUPERSEDES', 'INVALIDATED_BY')
    ) AS superseded_by_info,
    (
        SELECT GROUP_CONCAT(
            CASE
                WHEN e.from_id = n.id THEN e.relation || ' -> ' || e.to_id || ' (' || e.reason || ')'
                ELSE e.from_id || ' ' || e.relation || ' this (' || e.reason || ')'
            END,
            '; '
        )
        FROM lineage_edges e
        WHERE e.from_id = n.id OR e.to_id = n.id
    ) AS validation_chain
FROM nodes n;

CREATE VIRTUAL TABLE IF NOT EXISTS nodes_fts USING fts5(
    id,
    domain,
    title,
    one_liner,
    body_md,
    content='nodes',
    content_rowid='rowid',
    tokenize='porter unicode61'
);

CREATE TRIGGER IF NOT EXISTS nodes_ai AFTER INSERT ON nodes BEGIN
    INSERT INTO nodes_fts(rowid, id, domain, title, one_liner, body_md)
    VALUES (new.rowid, new.id, new.domain, new.title, new.one_liner, new.body_md);
END;

CREATE TRIGGER IF NOT EXISTS nodes_ad AFTER DELETE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, domain, title, one_liner, body_md)
    VALUES ('delete', old.rowid, old.id, old.domain, old.title, old.one_liner, old.body_md);
END;

CREATE TRIGGER IF NOT EXISTS nodes_au AFTER UPDATE ON nodes BEGIN
    INSERT INTO nodes_fts(nodes_fts, rowid, id, domain, title, one_liner, body_md)
    VALUES ('delete', old.rowid, old.id, old.domain, old.title, old.one_liner, old.body_md);
    INSERT INTO nodes_fts(rowid, id, domain, title, one_liner, body_md)
    VALUES (new.rowid, new.id, new.domain, new.title, new.one_liner, new.body_md);
END;
"""


def get_conn(db_path: pathlib.Path = DB_PATH) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(db_path: pathlib.Path = DB_PATH) -> None:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
    conn = get_conn(db_path)
    with conn:
        conn.executescript(SCHEMA_SQL)
    conn.close()


def rebuild_fts(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("INSERT INTO nodes_fts(nodes_fts) VALUES('rebuild');")


def natural_sort_key(node_id: str) -> Tuple[Any, ...]:
    parts = re.split(r"(\d+(?:\.\d+)?)", node_id)
    out: List[Any] = []
    for p in parts:
        if re.fullmatch(r"\d+(?:\.\d+)?", p):
            out.append(float(p))
        else:
            out.append(p)
    return tuple(out)


def slugify(text: str) -> str:
    s = text.lower()
    s = re.sub(r"[^\w\s-]", "", s)
    s = re.sub(r"[\s_-]+", "-", s)
    return s.strip("-")


def parse_frontmatter(text: str) -> Tuple[Dict[str, Any], str]:
    if not text.startswith("---"):
        return {}, text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, text
    fm_raw = parts[1].strip()
    body_md = parts[2].lstrip("\n")
    if yaml is not None:
        try:
            data = yaml.safe_load(fm_raw)
            if isinstance(data, dict):
                return data, body_md
        except Exception:
            pass
    data: Dict[str, Any] = {}
    for line in fm_raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            k = k.strip()
            v = v.strip()
            if " #" in v:
                v = v.split(" #", 1)[0].strip()
            if v.startswith("[") and v.endswith("]"):
                items = [x.strip().strip("'\"") for x in v[1:-1].split(",") if x.strip()]
                data[k] = items
            elif v.lower() in ("null", "~", ""):
                data[k] = None
            elif v.lower() in ("true", "yes"):
                data[k] = True
            elif v.lower() in ("false", "no"):
                data[k] = False
            else:
                data[k] = v.strip("'\"")
    return data, body_md


def validate_template_document(fm: Dict[str, Any], body: str, strict: bool = True) -> List[str]:
    cfg = load_config()
    valid_domains = tuple(cfg.get("domains", {}).keys())
    errors: List[str] = []

    if not fm.get("id"):
        errors.append("Missing required frontmatter field: 'id'")

    node_type = str(fm.get("type", "")).lower()
    if node_type in ("exp", "experiment"):
        node_type = "experiment"
    elif node_type in ("heur", "heuristic"):
        node_type = "heuristic"
    if not node_type or node_type not in VALID_NODE_TYPES:
        errors.append(f"Invalid or missing 'type'. Must be one of: {', '.join(VALID_NODE_TYPES)}")

    domain = fm.get("domain")
    if not domain or domain not in valid_domains:
        errors.append(f"Invalid domain '{domain}'. Valid domains in docs/lode.json: {', '.join(valid_domains)}")

    title = fm.get("title")
    if not title or str(title).strip().startswith("<"):
        errors.append("Field 'title' is empty or still contains a template placeholder")

    one_liner = fm.get("one_liner")
    if not one_liner or str(one_liner).strip().startswith("<"):
        errors.append("Field 'one_liner' is required, non-empty, and must not be a placeholder")

    required_map = {
        "prd": [
            "## 1. Overview",
            "## 2. Problem Statement",
            "## 3. Target Goals",
            "## 4. Non-Goals",
            "## 5. System Invariants",
            "## 6. Deterministic Technical Requirements",
            "## 8. Acceptance Gates",
        ],
        "adr": [
            "## 1. Context & Problem",
            "## 2. Considered Alternatives",
            "## 3. Decision & Production Invariant",
            "## 4. Empirical Evidence & Validation Lineage",
            "## 5. Consequences & Trade-Offs",
        ],
        "experiment": [
            "## 1. Objective & Pre-Registered Hypothesis",
            "## 2. Pre-Flight Ceteris-Paribus Hygiene Checklist",
            "## 3. Non-Goals & Safety Bounds",
            "## 4. Control vs. Treatment Matrix",
            "## 5. Telemetry Instruments",
            "## 6. Pre-Registered Go/No-Go Decision Rule",
            "## 7. Empirical Results Table",
            "## 8. Architectural Conclusion",
        ],
        "heuristic": [
            "Symptom / Failure Mode:",
            "Root Physics / Mechanism:",
            "Permanent Operational Rule:",
            "Verification Check:",
        ],
    }
    for req in required_map.get(node_type, []):
        if req not in body:
            errors.append(f"Missing required {node_type.upper()} section/field: '{req}'")

    if strict:
        stripped = re.sub(r"<!--.*?-->", "", body, flags=re.DOTALL)
        placeholders = [p for p in re.findall(r"<[A-Za-z0-9_ |.,-]{3,}>", stripped)]
        if len(placeholders) > 2:
            errors.append(f"Found unreplaced '<...>' placeholders in body (e.g. {placeholders[:3]}). Replace or pass --force.")
    return errors


def cmd_init(args: argparse.Namespace) -> None:
    ttype = getattr(args, "template_type", None)
    if not ttype or ttype == "db":
        init_db()
        print(f"[OK] Initialized SQLite Lineage Engine at {DB_PATH} (WAL mode, FTS5 enabled)")
        return

    ttype = ttype.lower()
    cfg = load_config()
    valid_domains = tuple(cfg.get("domains", {}).keys())
    if not args.id or not args.domain or not args.title:
        print("[ERROR] --id, --domain, and --title are required when scaffolding a template.")
        sys.exit(1)
    if args.domain not in valid_domains:
        print(f"[ERROR] Domain '{args.domain}' not in docs/lode.json ({', '.join(valid_domains)})")
        sys.exit(1)

    template_map = {
        "prd": ("PRD_TEMPLATE.md", DOCS_DIR / "specs", True),
        "adr": ("ADR_TEMPLATE.md", DOCS_DIR / "lode", True),
        "heur": ("HEUR_TEMPLATE.md", DOCS_DIR / "lode", True),
        "exp": ("EXP_TEMPLATE.md", DOCS_DIR / "experiments", False),
    }
    tfile, target_dir, use_slug = template_map[ttype]
    template_path = DOCS_DIR / "templates" / tfile
    if not template_path.exists():
        print(f"[ERROR] Template not found at {template_path}")
        sys.exit(1)

    slug = slugify(args.title)
    fname = f"{args.id}-{slug}.md" if (use_slug and slug) else f"{args.id}.md"
    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / fname
    if target_file.exists():
        print(f"[ERROR] Target file {target_file} already exists.")
        sys.exit(1)

    id_placeholders = {"prd": "PRD-XXX", "adr": "ADR-XXX", "exp": "EXP-XXX", "heur": "HEUR-XXX"}
    content = template_path.read_text(encoding="utf-8")
    content = content.replace(id_placeholders[ttype], args.id)
    content = re.sub(r"domain:\s*<[^>]+>", f"domain: {args.domain}", content)
    content = re.sub(r'title:\s*"<[^>]+>"', f'title: "{args.title}"', content)
    content = content.replace("<Title>", args.title)
    content = content.replace("YYYY-MM-DD", datetime.date.today().isoformat())
    if args.supersedes:
        content = re.sub(r"supersedes:\s*\[\]", f'supersedes: ["{args.supersedes}"]', content)
        content = content.replace("<OLD-ID>", args.supersedes)

    target_file.write_text(content, encoding="utf-8")
    print(f"[OK] Scaffolded {ttype.upper()} at {target_file}")


def ensure_placeholder_node(conn: sqlite3.Connection, node_id: str, domain: str, created_at: str) -> None:
    row = conn.execute("SELECT id FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if not row:
        prefix = node_id.split("-")[0].lower()
        ntype = {"adr": "adr", "heur": "heuristic", "exp": "experiment", "prd": "prd", "chg": "change"}.get(prefix, "task")
        conn.execute(
            """
            INSERT INTO nodes (id, node_type, domain, title, one_liner, body_md, methodology_tier, live_verified_status, created_at)
            VALUES (?, ?, ?, ?, ?, '', 'N/A', 'PENDING', ?)
            """,
            (node_id, ntype, domain, f"{node_id} (Referenced Node)", "Pending ingestion", created_at),
        )


def cmd_ingest(args: argparse.Namespace) -> None:
    doc_path = pathlib.Path(args.path)
    if not doc_path.exists():
        print(f"[ERROR] File not found: {doc_path}")
        sys.exit(1)

    fm, body_md = parse_frontmatter(doc_path.read_text(encoding="utf-8"))
    if not fm:
        print(f"[ERROR] Missing YAML frontmatter in {doc_path}")
        sys.exit(1)

    errors = validate_template_document(fm, body_md, strict=not args.force)
    if errors:
        print(f"[ERROR] Validation failed for {doc_path}:")
        for e in errors:
            print(f"  - {e}")
        if not args.force:
            sys.exit(1)

    node_id = str(fm["id"]).strip()
    raw_type = str(fm.get("type", "")).lower()
    normalized_type = "experiment" if raw_type in ("exp", "experiment") else ("heuristic" if raw_type in ("heur", "heuristic") else raw_type)
    domain = str(fm["domain"]).strip()
    title = str(fm["title"]).strip()
    one_liner = str(fm["one_liner"]).strip()
    created_at = str(fm.get("created_at") or datetime.date.today().isoformat())
    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")

    status_raw = str(fm.get("status", "ACTIVE")).upper()
    status_map = {
        "ACTIVE": "VERIFIED",
        "VERIFIED": "VERIFIED",
        "COMPLETED": "VERIFIED",
        "COMPLETE_ADOPTED": "VERIFIED",
        "COMPLETE_NULL_REJECTED": "VERIFIED",
        "QUEUED": "PENDING",
        "DRAFT": "PENDING",
        "READY": "PENDING",
        "IN_PROGRESS": "PENDING",
        "SUPERSEDED": "SUPERSEDED",
        "DEPRECATED": "SUPERSEDED",
        "NEEDS_RETEST": "NEEDS_RETEST",
    }
    verified_status = status_map.get(status_raw, "VERIFIED")
    methodology_tier = fm.get("methodology_tier", "TIER_1_ISOLATED" if normalized_type == "experiment" else "N/A")

    init_db()
    conn = get_conn()
    with conn:
        conn.execute(
            """
            INSERT INTO nodes (
                id, node_type, domain, title, one_liner, body_md,
                methodology_tier, live_verified_status, live_verified_at, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                node_type = excluded.node_type,
                domain = excluded.domain,
                title = excluded.title,
                one_liner = excluded.one_liner,
                body_md = excluded.body_md,
                methodology_tier = excluded.methodology_tier,
                live_verified_status = excluded.live_verified_status,
                live_verified_at = excluded.live_verified_at
            """,
            (
                node_id,
                normalized_type,
                domain,
                title,
                one_liner,
                body_md,
                methodology_tier,
                verified_status,
                now_iso if verified_status == "VERIFIED" else None,
                created_at,
            ),
        )

        def _as_list(val: Any) -> List[str]:
            if not val:
                return []
            return [val] if isinstance(val, str) else [str(x) for x in val if x]

        for old_id in _as_list(fm.get("supersedes")):
            ensure_placeholder_node(conn, old_id, domain, created_at)
            conn.execute(
                """
                INSERT INTO lineage_edges (from_id, relation, to_id, reason, created_at)
                VALUES (?, 'SUPERSEDES', ?, ?, ?)
                ON CONFLICT(from_id, relation, to_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (node_id, old_id, f"{node_id} supersedes {old_id}", now_iso),
            )

        for val_id in _as_list(fm.get("validated_by")):
            ensure_placeholder_node(conn, val_id, domain, created_at)
            conn.execute(
                """
                INSERT INTO lineage_edges (from_id, relation, to_id, reason, created_at)
                VALUES (?, 'VALIDATED_BY', ?, ?, ?)
                ON CONFLICT(from_id, relation, to_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (node_id, val_id, f"{node_id} validated by {val_id}", now_iso),
            )

        for target_id in _as_list(fm.get("validates")):
            ensure_placeholder_node(conn, target_id, domain, created_at)
            conn.execute(
                """
                INSERT INTO lineage_edges (from_id, relation, to_id, reason, created_at)
                VALUES (?, 'VALIDATED_BY', ?, ?, ?)
                ON CONFLICT(from_id, relation, to_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (target_id, node_id, f"{target_id} validated by {node_id}", now_iso),
            )

        for dep_id in _as_list(fm.get("depends_on")):
            ensure_placeholder_node(conn, dep_id, domain, created_at)
            conn.execute(
                """
                INSERT INTO lineage_edges (from_id, relation, to_id, reason, created_at)
                VALUES (?, 'DEPENDS_ON', ?, ?, ?)
                ON CONFLICT(from_id, relation, to_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
                """,
                (node_id, dep_id, f"{node_id} depends on {dep_id}", now_iso),
            )

    rebuild_fts(conn)
    sync_all_docs(conn)
    conn.close()
    print(f"[OK] Ingested {node_id} ({normalized_type}/{domain}): '{title}' and synchronized docs.")


def cmd_brief(args: argparse.Namespace) -> None:
    init_db()
    cfg = load_config()
    valid_domains = cfg.get("domains", {})
    global_domains = tuple(cfg.get("global_guardrail_domains", []))
    domain = args.domain.lower()

    conn = get_conn()
    rows = conn.execute("SELECT * FROM v_active_knowledge WHERE domain = ? ORDER BY id", (domain,)).fetchall()
    if not rows and domain not in valid_domains:
        print(f"[WARN] Unknown domain '{domain}'. Configured domains: {', '.join(valid_domains.keys())}")
        conn.close()
        sys.exit(1)

    active_adrs = [r for r in rows if r["node_type"] == "adr" and r["effective_status"] == "ACTIVE"]
    active_heurs = [r for r in rows if r["node_type"] == "heuristic" and r["effective_status"] == "ACTIVE"]
    active_exps = [r for r in rows if r["node_type"] == "experiment" and r["effective_status"] == "ACTIVE" and r["live_verified_status"] in ("VERIFIED", "DRIFTED")]
    queued_exps = [r for r in rows if r["node_type"] == "experiment" and r["effective_status"] == "ACTIVE" and r["live_verified_status"] == "PENDING"]
    active_prds = [r for r in rows if r["node_type"] in ("prd", "task") and r["effective_status"] == "ACTIVE"]
    retest_nodes = [r for r in rows if r["effective_status"] == "NEEDS_RETEST"]
    superseded_nodes = [r for r in rows if r["effective_status"] == "SUPERSEDED"]

    out: List[str] = [
        f"# Domain Briefing: `{domain}`",
        f"- **Scope:** {valid_domains.get(domain, 'Custom domain')}",
        f"- **Lineage Filter:** Showing ONLY `ACTIVE` knowledge (`{len(superseded_nodes)}` superseded node(s) suppressed; `{len(retest_nodes)}` flagged `NEEDS_RETEST`).",
    ]

    if domain not in global_domains and global_domains:
        placeholders = ",".join("?" for _ in global_domains)
        inv_rows = conn.execute(
            f"SELECT id, domain, one_liner FROM v_active_knowledge WHERE domain IN ({placeholders}) AND node_type = 'adr' AND effective_status = 'ACTIVE' ORDER BY id",
            global_domains,
        ).fetchall()
        if inv_rows:
            out.append(f"\n## Global Guardrails ({', '.join(f'`{d}`' for d in global_domains)})")
            for ir in inv_rows:
                out.append(f"- **`{ir['id']}` (`{ir['domain']}`):** {ir['one_liner']}")

    if retest_nodes:
        out.append("\n## Methodological Contamination Warnings (`NEEDS_RETEST`)")
        for r in retest_nodes:
            out.append(f"- **`{r['id']}` ({r['title']}):** `{r['methodology_tier']}` — {r['one_liner']}")

    out.append(f"\n## Active Architectural Decisions (`{domain}`)")
    for r in sorted(active_adrs, key=lambda x: natural_sort_key(x["id"])):
        chain = f" *(Lineage: {r['validation_chain']})*" if r["validation_chain"] else ""
        out.append(f"- **`{r['id']}` — {r['title']}:** {r['one_liner']}{chain}")
    if not active_adrs:
        out.append("- *(No domain-specific active ADRs)*")

    out.append(f"\n## Active Operational Heuristics (`{domain}`)")
    for r in sorted(active_heurs, key=lambda x: natural_sort_key(x["id"])):
        out.append(f"- **`{r['id']}` — {r['title']}:** {r['one_liner']}")
    if not active_heurs:
        out.append("- *(No domain-specific active heuristics)*")

    out.append(f"\n## Verified Empirical Experiments (`{domain}`)")
    for r in sorted(active_exps, key=lambda x: natural_sort_key(x["id"])):
        out.append(f"- **`{r['id']}` (`{r['methodology_tier']}`) — {r['title']}:** {r['one_liner']}")
    if not active_exps:
        out.append("- *(No verified completed experiments in this domain)*")

    if queued_exps:
        out.append(f"\n## Queued Experiment Specs (`{domain}`)")
        for r in sorted(queued_exps, key=lambda x: natural_sort_key(x["id"])):
            out.append(f"- **`{r['id']}` (`QUEUED_SPEC`) — {r['title']}:** {r['one_liner']}")

    if active_prds:
        out.append(f"\n## Active PRDs & Engineering Tasks (`{domain}`)")
        for r in sorted(active_prds, key=lambda x: natural_sort_key(x["id"])):
            out.append(f"- **`{r['id']}` — {r['title']}:** {r['one_liner']}")

    if superseded_nodes:
        sup_list = ", ".join(f"`{r['id']}`" for r in sorted(superseded_nodes, key=lambda x: natural_sort_key(x["id"])))
        out.append(f"\n> **Suppressed Superseded Nodes ({len(superseded_nodes)}):** {sup_list} (Run `lodectx.py trace <ID>` to inspect lineage).")

    conn.close()
    print("\n".join(out))


def cmd_trace(args: argparse.Namespace) -> None:
    init_db()
    conn = get_conn()
    node = conn.execute("SELECT * FROM v_active_knowledge WHERE UPPER(id) = UPPER(?)", (args.id,)).fetchone()
    if not node:
        print(f"[ERROR] Node '{args.id}' not found in {DB_PATH}.")
        conn.close()
        sys.exit(1)

    actual_id = node["id"]
    upstream = conn.execute(
        """
        WITH RECURSIVE up_chain(depth, from_id, relation, to_id, reason, created_at, path) AS (
            SELECT 1, from_id, relation, to_id, reason, created_at, from_id || ' -> ' || to_id
            FROM lineage_edges WHERE to_id = ?
            UNION ALL
            SELECT u.depth + 1, e.from_id, e.relation, e.to_id, e.reason, e.created_at, e.from_id || ' -> ' || u.path
            FROM lineage_edges e JOIN up_chain u ON e.to_id = u.from_id
            WHERE u.depth < 6 AND instr(u.path, e.from_id) = 0
        )
        SELECT DISTINCT depth, from_id, relation, to_id, reason, created_at FROM up_chain ORDER BY depth, from_id;
        """,
        (actual_id,),
    ).fetchall()

    downstream = conn.execute(
        """
        WITH RECURSIVE down_chain(depth, from_id, relation, to_id, reason, created_at, path) AS (
            SELECT 1, from_id, relation, to_id, reason, created_at, from_id || ' -> ' || to_id
            FROM lineage_edges WHERE from_id = ?
            UNION ALL
            SELECT d.depth + 1, e.from_id, e.relation, e.to_id, e.reason, e.created_at, d.path || ' -> ' || e.to_id
            FROM lineage_edges e JOIN down_chain d ON e.from_id = d.to_id
            WHERE d.depth < 6 AND instr(d.path, e.to_id) = 0
        )
        SELECT DISTINCT depth, from_id, relation, to_id, reason, created_at FROM down_chain ORDER BY depth, to_id;
        """,
        (actual_id,),
    ).fetchall()

    print(f"# Lineage Trace: `{actual_id}` — {node['title']}")
    print(f"- **Type:** `{node['node_type']}` | **Domain:** `{node['domain']}` | **Effective Status:** **`{node['effective_status']}`**")
    print(f"- **One-Liner:** {node['one_liner']}")
    if node["superseded_by_info"]:
        print(f"- **Superseded By:** {node['superseded_by_info']}")

    print("\n## Incoming Lineage Edges (Upstream Authorities / Supersessions)")
    for e in upstream:
        print(f"{'  ' * (e['depth'] - 1)}- `[depth={e['depth']}]` **`{e['from_id']}`** --[`{e['relation']}`]--> **`{e['to_id']}`**: {e['reason']}")
    if not upstream:
        print("- *(None)*")

    print("\n## Outgoing Lineage Edges (Downstream Targets / Validations)")
    for e in downstream:
        print(f"{'  ' * (e['depth'] - 1)}- `[depth={e['depth']}]` **`{e['from_id']}`** --[`{e['relation']}`]--> **`{e['to_id']}`**: {e['reason']}")
    if not downstream:
        print("- *(None)*")
    conn.close()


def build_fts_query(raw_query: str, mode: str = "AND") -> str:
    tokens = re.findall(r"[A-Za-z0-9_]+", raw_query)
    return f" {mode} ".join(f'"{t}"' for t in tokens) if tokens else '""'


def cmd_search(args: argparse.Namespace) -> None:
    init_db()
    t0 = time.perf_counter()
    conn = get_conn()
    sql = """
        SELECT v.id, v.node_type, v.domain, v.title, v.one_liner, v.effective_status, v.superseded_by_info,
               bm25(nodes_fts, 10.0, 4.0, 8.0, 6.0, 1.0) AS score
        FROM nodes_fts
        JOIN v_active_knowledge v ON nodes_fts.rowid = (SELECT rowid FROM nodes WHERE id = v.id)
        WHERE nodes_fts MATCH ?
    """
    params: List[Any] = [build_fts_query(args.query, "AND")]
    if args.domain:
        sql += " AND v.domain = ?"
        params.append(args.domain.lower())
    if not args.include_superseded:
        sql += " AND v.effective_status != 'SUPERSEDED'"
    sql += " ORDER BY score ASC LIMIT 25"

    rows = conn.execute(sql, params).fetchall()
    if not rows:
        params[0] = build_fts_query(args.query, "OR")
        rows = conn.execute(sql, params).fetchall()

    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    print(f"# FTS5 BM25 Search Results for `{args.query}` ({len(rows)} hits in `{elapsed_ms:.2f} ms`)")
    for r in rows:
        badge = f"[{r['effective_status']}]"
        if r["effective_status"] == "SUPERSEDED":
            badge = f"[SUPERSEDED BY {r['superseded_by_info']}]"
        print(f"- **`{r['id']}`** (`{r['domain']}` / `{r['node_type']}`) {badge} — **{r['title']}**\n  {r['one_liner']} *(BM25={r['score']:.3f})*")
    conn.close()


def cmd_add_node(args: argparse.Namespace) -> None:
    init_db()
    conn = get_conn()
    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    created_at = args.created_at or datetime.date.today().isoformat()
    body_md = pathlib.Path(args.body_file).read_text(encoding="utf-8") if args.body_file else (args.body or "")
    with conn:
        conn.execute(
            """
            INSERT INTO nodes (id, node_type, domain, title, one_liner, body_md, methodology_tier, live_verified_status, live_verified_at, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                node_type=excluded.node_type, domain=excluded.domain, title=excluded.title,
                one_liner=excluded.one_liner, body_md=excluded.body_md, methodology_tier=excluded.methodology_tier,
                live_verified_status=excluded.live_verified_status, live_verified_at=excluded.live_verified_at
            """,
            (args.id, args.type, args.domain, args.title, args.one_liner, body_md, args.tier, args.status, now_iso, created_at),
        )
    rebuild_fts(conn)
    sync_all_docs(conn)
    conn.close()
    print(f"[OK] Upserted node {args.id} and synchronized docs.")


def cmd_add_edge(args: argparse.Namespace) -> None:
    init_db()
    conn = get_conn()
    now_iso = datetime.datetime.now(datetime.timezone.utc).astimezone().isoformat(timespec="seconds")
    with conn:
        conn.execute(
            """
            INSERT INTO lineage_edges (from_id, relation, to_id, reason, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(from_id, relation, to_id) DO UPDATE SET reason=excluded.reason, created_at=excluded.created_at
            """,
            (args.from_id, args.relation, args.to_id, args.reason, now_iso),
        )
    sync_all_docs(conn)
    conn.close()
    print(f"[OK] Added lineage edge: {args.from_id} --[{args.relation}]--> {args.to_id}")


def sync_all_docs(conn: sqlite3.Connection) -> None:
    cfg = load_config()
    proj_name = cfg.get("project_name", REPO_ROOT.name)
    domains_map: Dict[str, str] = cfg.get("domains", {})

    # 1. Render docs/experiments/INDEX.md
    exp_dir = DOCS_DIR / "experiments"
    exp_dir.mkdir(parents=True, exist_ok=True)
    exp_rows = sorted(
        conn.execute("SELECT * FROM v_active_knowledge WHERE node_type = 'experiment'").fetchall(),
        key=lambda r: natural_sort_key(r["id"]),
    )
    idx_lines = [
        f"# {proj_name} — Empirical Experiments Master Index (`docs/experiments/INDEX.md`)",
        "",
        "> *Auto-rendered from `docs/.context/lode.db` via `scripts/lodectx.py sync-docs`.*",
        "",
        "| ID | Domain | Title | Effective Status | Tier | Summary |",
        "| :--- | :--- | :--- | :--- | :--- | :--- |",
    ]
    for r in exp_rows:
        idx_lines.append(
            f"| `{r['id']}` | `{r['domain']}` | {r['title']} | **`{r['effective_status']}`** | `{r['methodology_tier']}` | {r['one_liner']} |"
        )
    (exp_dir / "INDEX.md").write_text("\n".join(idx_lines) + "\n", encoding="utf-8")

    # 2. Render docs/LODE_MAP.md
    adr_rows = sorted(conn.execute("SELECT * FROM v_active_knowledge WHERE node_type = 'adr'").fetchall(), key=lambda r: natural_sort_key(r["id"]))
    heur_rows = sorted(conn.execute("SELECT * FROM v_active_knowledge WHERE node_type = 'heuristic'").fetchall(), key=lambda r: natural_sort_key(r["id"]))
    prd_rows = sorted(conn.execute("SELECT * FROM v_active_knowledge WHERE node_type = 'prd'").fetchall(), key=lambda r: natural_sort_key(r["id"]))

    lode_lines = [
        f"# {proj_name} — Lode Map & Progressive Disclosure Index (`docs/LODE_MAP.md`)",
        "",
        "> **Startup Core (< 7,500 tokens):** `.agents/GEMINI.md` + `docs/STATE_SUMMARY.md` + `docs/LODE_MAP.md` + `docs/INVARIANTS.md` + `docs/TASKS.md`.",
        "> **On-Demand Domain Briefing:** Run `python3 scripts/lodectx.py brief <domain>` before modifying any subsystem.",
        "> **Canonical Templates & Scaffolding:** `python3 scripts/lodectx.py init <prd|adr|exp|heur>` & `python3 scripts/lodectx.py ingest <path>`.",
        "",
        "## 1. Domain Lode Routing Table",
        "| Domain | Lode File | Briefing CLI | Scope |",
        "| :--- | :--- | :--- | :--- |",
    ]
    for dom, desc in domains_map.items():
        lode_lines.append(f"| `{dom}` | `docs/domains/{dom}.md` | `python3 scripts/lodectx.py brief {dom}` | {desc} |")

    lode_lines.extend(["", "## 2. Engineering PRDs (`docs/specs/`)"])
    for p in prd_rows:
        lode_lines.append(f"- **`{p['id']}`** (`{p['domain']}`): {p['title']} — {p['one_liner']}")
    if not prd_rows:
        lode_lines.append("- *(No PRDs registered yet)*")

    lode_lines.extend(["", "## 3. Architectural Decisions Index (`ADR`)"])
    for r in adr_rows:
        tag = f" [SUPERSEDED BY {r['superseded_by_info']}]" if r["effective_status"] == "SUPERSEDED" else ""
        lode_lines.append(f"- **`{r['id']}`** (`{r['domain']}`): {r['title']}{tag}")
    if not adr_rows:
        lode_lines.append("- *(No ADRs registered yet)*")

    lode_lines.extend(["", "## 4. Operational Heuristics Index by Domain (`HEUR`)"])
    by_dom: Dict[str, List[str]] = {}
    for r in heur_rows:
        entry = f"~~`{r['id']}`~~" if r["effective_status"] == "SUPERSEDED" else f"`{r['id']}`"
        by_dom.setdefault(r["domain"], []).append(entry)
    for dom in domains_map:
        if dom in by_dom:
            lode_lines.append(f"- **`{dom}`:** " + ", ".join(by_dom[dom]))
    if not heur_rows:
        lode_lines.append("- *(No Heuristics registered yet)*")

    (DOCS_DIR / "LODE_MAP.md").write_text("\n".join(lode_lines) + "\n", encoding="utf-8")

    # 3. Render Archive Ledger
    arch_dir = DOCS_DIR / "archive"
    arch_dir.mkdir(parents=True, exist_ok=True)
    dh_lines = [
        f"# {proj_name} — Verbatim Architectural Decisions & Heuristics Ledger",
        "",
        "> *Auto-rendered from `docs/.context/lode.db`.*",
        "",
        "## Part 1: Architectural Decisions",
    ]
    for r in adr_rows:
        dh_lines.extend([f"### `{r['id']}` — {r['title']} [`{r['effective_status']}` | `{r['domain']}`]", "", r["body_md"].strip(), ""])
    dh_lines.extend(["---", "", "## Part 2: Operational Heuristics"])
    for r in heur_rows:
        dh_lines.extend([f"### `{r['id']}` — {r['title']} [`{r['effective_status']}` | `{r['domain']}`]", "", r["body_md"].strip(), ""])
    (arch_dir / "DECISIONS_AND_HEURISTICS_LEDGER.md").write_text("\n".join(dh_lines) + "\n", encoding="utf-8")


def cmd_sync_docs(_args: argparse.Namespace) -> None:
    init_db()
    conn = get_conn()
    rebuild_fts(conn)
    sync_all_docs(conn)
    conn.close()
    print("[OK] Synchronized docs/LODE_MAP.md, docs/experiments/INDEX.md, and docs/archive/DECISIONS_AND_HEURISTICS_LEDGER.md")


def cmd_stats(_args: argparse.Namespace) -> None:
    init_db()
    cfg = load_config()
    budget = int(cfg.get("startup_token_budget", 7500))
    startup_rels = cfg.get("startup_files", DEFAULT_CONFIG["startup_files"])

    conn = get_conn()
    print(f"# SQLite Lineage Engine Telemetry (`{DB_PATH.relative_to(REPO_ROOT)}`)")
    print("\n## 1. Node Inventory by `node_type` & `effective_status`")
    print("| Node Type | Total | `ACTIVE` | `SUPERSEDED` | `NEEDS_RETEST` | `COMPLETED` |")
    print("| :--- | :---: | :---: | :---: | :---: | :---: |")
    total_all = 0
    for ntype in VALID_NODE_TYPES:
        rows = conn.execute(
            "SELECT effective_status, COUNT(*) AS cnt FROM v_active_knowledge WHERE node_type = ? GROUP BY effective_status",
            (ntype,),
        ).fetchall()
        counts = {r["effective_status"]: r["cnt"] for r in rows}
        tot = sum(counts.values())
        total_all += tot
        print(f"| `{ntype}` | **{tot}** | {counts.get('ACTIVE', 0)} | {counts.get('SUPERSEDED', 0)} | {counts.get('NEEDS_RETEST', 0)} | {counts.get('COMPLETED', 0)} |")
    print(f"| **TOTAL** | **{total_all}** | — | — | — | — |")

    print(f"\n## 2. Startup Context Token Budget Verification (Target: < {budget:,} Tokens)")
    print("| Startup File | Bytes | Words | Est. Tokens (`chars / 4`) |")
    print("| :--- | :---: | :---: | :---: |")
    total_bytes = 0
    total_tok = 0
    for rel in startup_rels:
        sf = REPO_ROOT / rel
        if sf.exists():
            txt = sf.read_text(encoding="utf-8")
            b = len(txt.encode("utf-8"))
            w = len(txt.split())
            tok = len(txt) // 4
            total_bytes += b
            total_tok += tok
            print(f"| `{rel}` | `{b:,}` | `{w:,}` | **`{tok:,}`** |")
        else:
            print(f"| `{rel}` | *(missing)* | — | — |")
    status = f"PASS (< {budget:,} tokens)" if total_tok < budget else f"FAIL (>= {budget:,} tokens)"
    print(f"| **COMBINED STARTUP CORE** | **`{total_bytes:,}`** | — | **`{total_tok:,}` ({status})** |")
    conn.close()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lodectx.py", description="Append-Only SQLite Lineage Engine & Lode Context CLI")
    sub = p.add_subparsers(dest="subcommand", required=True)

    p_init = sub.add_parser("init", help="Init SQLite schema ('init db') or scaffold template ('init prd|adr|exp|heur')")
    p_init.add_argument("template_type", nargs="?", choices=["prd", "adr", "exp", "heur", "db"], default="db")
    p_init.add_argument("--id", help="Canonical ID (e.g. PRD-001, ADR-001, EXP-001, HEUR-001)")
    p_init.add_argument("--domain", help="Domain name from docs/lode.json")
    p_init.add_argument("--title", help="Document title")
    p_init.add_argument("--supersedes", help="Optional ID of node being superseded")

    p_ingest = sub.add_parser("ingest", help="Validate markdown template, upsert into SQLite, and sync docs")
    p_ingest.add_argument("path", help="Path to markdown file")
    p_ingest.add_argument("--force", action="store_true", help="Bypass strict placeholder validation")

    p_brief = sub.add_parser("brief", help="Emit progressive-disclosure briefing for <domain>")
    p_brief.add_argument("domain", help="Domain name")

    p_trace = sub.add_parser("trace", help="Traverse upstream and downstream lineage for <id>")
    p_trace.add_argument("id", help="Node ID (e.g. ADR-001)")

    p_search = sub.add_parser("search", help="FTS5 BM25 search across knowledge nodes")
    p_search.add_argument("query", help="Search query")
    p_search.add_argument("--domain", help="Filter by domain")
    p_search.add_argument("--include-superseded", action="store_true")

    p_node = sub.add_parser("add-node", help="Upsert a node directly via CLI")
    p_node.add_argument("--id", required=True)
    p_node.add_argument("--type", required=True, choices=VALID_NODE_TYPES)
    p_node.add_argument("--domain", required=True)
    p_node.add_argument("--title", required=True)
    p_node.add_argument("--one-liner", required=True)
    p_node.add_argument("--body", default="")
    p_node.add_argument("--body-file")
    p_node.add_argument("--tier", default="N/A", choices=VALID_TIERS)
    p_node.add_argument("--status", default="VERIFIED", choices=VALID_VERIFIED_STATUSES)
    p_node.add_argument("--created-at")

    p_edge = sub.add_parser("add-edge", help="Add directed lineage edge")
    p_edge.add_argument("--from", dest="from_id", required=True)
    p_edge.add_argument("--relation", required=True, choices=VALID_RELATIONS)
    p_edge.add_argument("--to", dest="to_id", required=True)
    p_edge.add_argument("--reason", required=True)

    sub.add_parser("sync-docs", help="Render LODE_MAP.md, experiments/INDEX.md, and archive ledgers")
    sub.add_parser("stats", help="Print database and token budget telemetry")
    return p


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    dispatch = {
        "init": cmd_init,
        "ingest": cmd_ingest,
        "brief": cmd_brief,
        "trace": cmd_trace,
        "search": cmd_search,
        "add-node": cmd_add_node,
        "add-edge": cmd_add_edge,
        "sync-docs": cmd_sync_docs,
        "stats": cmd_stats,
    }
    dispatch[args.subcommand](args)


if __name__ == "__main__":
    main()
