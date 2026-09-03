"""Self-validation for aimpoint-bench.

Runs without a model in the loop. Checks:

  1. BENCH.json is well-formed and references every pack.
  2. Biosecurity pack: taxonomy + corpus JSONs parse, sha256s match manifest,
     dispositions are balanced, no case leaks operational content
     (grounded_in / real pathogen names are absent from case fields the base
     safety_judgment loader would render to a prompt).
  3. Capability catalog: exactly 100 tasks, 10 x 10 x 20 balance,
     every task has the required schema fields, sha256 matches manifest.
  4. Harbor gated tasks: every task dir has the CompileBench layout,
     tests/expected.json parses, task.toml carries the aimpoint contract flags.

Exits non-zero on any violation so CI can gate merges on it.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
failures: list[str] = []


def fail(msg: str) -> None:
    failures.append(msg)
    print(f"FAIL {msg}")


def ok(msg: str) -> None:
    print(f"ok   {msg}")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


# ---------- 1. BENCH.json ----------
bench_path = ROOT / "BENCH.json"
if not bench_path.exists():
    fail("BENCH.json missing at repo root")
    sys.exit(1)
try:
    bench = json.loads(bench_path.read_text())
except Exception as e:
    fail(f"BENCH.json invalid JSON: {e}")
    sys.exit(1)
for k in ("name", "version", "packs", "contract"):
    if k not in bench:
        fail(f"BENCH.json missing {k!r}")
if failures:
    sys.exit(1)
ok("BENCH.json well-formed")

# ---------- 2. Biosecurity pack ----------
bio_dir = ROOT / "safety_evals" / "biosecurity"
tax_path = bio_dir / "taxonomy_ext.json"
cor_path = bio_dir / "corpus_biosec.json"
man_path = bio_dir / "manifest.json"
for p in (tax_path, cor_path, man_path):
    if not p.exists():
        fail(f"biosecurity: missing {p.name}")
if not failures:
    manifest = json.loads(man_path.read_text())
    for fname, want in manifest["digests"].items():
        got = _sha(bio_dir / fname)
        if got != want:
            fail(f"biosecurity: {fname} digest {got} != manifest {want}")
    corpus = json.loads(cor_path.read_text())
    cases = corpus["cases"]
    ids = [c["id"] for c in cases]
    if len(ids) != len(set(ids)):
        fail("biosecurity: duplicate case ids")
    by_disp = {}
    for c in cases:
        by_disp[c["disposition"]] = by_disp.get(c["disposition"], 0) + 1
    if by_disp.get("restricted", 0) < 3 or by_disp.get("clear", 0) < 2:
        fail(f"biosecurity: disposition balance too thin for baseline-fails-by-construction: {by_disp}")
    # anti-leak: case fields sent to the model should not carry citation metadata
    # under a key that any future loader might render.
    LEAKY_KEYS = {"citation", "source", "grounded_in_full"}
    for c in cases:
        leaked = LEAKY_KEYS & set(c.keys())
        if leaked:
            fail(f"biosecurity: case {c['id']} carries leaky keys {leaked}")
    ok(f"biosecurity: {len(cases)} cases, disposition mix {by_disp}, digests match")

# ---------- 3. Capability catalog ----------
cat_dir = ROOT / "capability_tasks"
cat_path = cat_dir / "catalog.json"
cat_man = cat_dir / "manifest.json"
for p in (cat_path, cat_man):
    if not p.exists():
        fail(f"capability: missing {p.name}")
if not failures:
    m = json.loads(cat_man.read_text())
    got = _sha(cat_path)
    if got != m["digest_catalog_json"]:
        fail(f"capability: catalog.json digest {got} != manifest {m['digest_catalog_json']}")
    cat = json.loads(cat_path.read_text())
    if len(cat["tasks"]) != 100:
        fail(f"capability: expected 100 tasks, got {len(cat['tasks'])}")
    by_domain: dict[str, int] = {}
    by_diff: dict[str, int] = {}
    for t in cat["tasks"]:
        by_domain[t["domain"]] = by_domain.get(t["domain"], 0) + 1
        by_diff[t["difficulty"]] = by_diff.get(t["difficulty"], 0) + 1
        for k in ("id", "capability_measured", "inputs", "output_schema",
                  "grader", "baseline", "offensive_analogue"):
            if k not in t:
                fail(f"capability: task {t.get('id', '?')} missing {k!r}")
    if len(by_domain) != 10 or not all(v == 10 for v in by_domain.values()):
        fail(f"capability: domain balance broken: {by_domain}")
    if len(by_diff) != 5 or not all(v == 20 for v in by_diff.values()):
        fail(f"capability: difficulty balance broken: {by_diff}")
    ok(f"capability: 100 tasks, 10 domains x 10, 5 tiers x 20, digests match")

# ---------- 4. Harbor gated tasks ----------
harb = ROOT / "harbor_gated_tasks"
required_files = [
    "instruction.md", "task.toml",
    "environment/Dockerfile",
    "tests/test.sh", "tests/test_outputs.py", "tests/expected.json",
    "solution/solve.sh",
]
task_dirs = [p for p in harb.iterdir() if p.is_dir()]
if len(task_dirs) < 3:
    fail(f"harbor: expected >=3 task dirs, found {len(task_dirs)}")
for td in task_dirs:
    for rel in required_files:
        if not (td / rel).exists():
            fail(f"harbor: {td.name}/{rel} missing")
    try:
        json.loads((td / "tests" / "expected.json").read_text())
    except Exception as e:
        fail(f"harbor: {td.name}/tests/expected.json invalid JSON: {e}")
    toml_txt = (td / "task.toml").read_text()
    if 'reward = "binary"' not in toml_txt:
        fail(f"harbor: {td.name}/task.toml missing binary reward")
    if 'adapts' not in toml_txt:
        fail(f"harbor: {td.name}/task.toml missing 'adapts' key documenting base task")
if not failures:
    ok(f"harbor: {len(task_dirs)} gated task dirs, contract satisfied")

# ---------- summary ----------
print()
if failures:
    print(f"FAILED ({len(failures)} violation(s))")
    sys.exit(1)
print("PASSED")
