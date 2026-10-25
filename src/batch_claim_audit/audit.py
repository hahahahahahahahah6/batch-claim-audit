"""Core audit logic: docs claims vs Python source evidence.

Static only: nothing is executed, no network is used, and .env file
contents are never opened.
"""

from __future__ import annotations

import ast
import fnmatch
import json
import re
from pathlib import Path

VERSION = "0.1.0"

BATCHED_PHRASES = (
    "in batches",
    "batched",
    "batch processing",
    "processes in batches",
    "processed in batches",
    "batch uploads",
    "batch requests",
    "batch inserts",
    "bulk insert",
    "bulk update",
    "bulk upload",
    "bulk requests",
)

ONE_BY_ONE_PHRASES = (
    "one by one",
    "one at a time",
    "item by item",
    "no batching",
    "without batching",
    "not batched",
    "does not batch",
    "never batches",
    "processes items individually",
    "process items individually",
    "handles items individually",
    "handle items individually",
)

NEGATION_WORDS = ("no", "not", "never", "without")

GROUPING_NAMES = {"batched", "chunked"}


def _normalise(sentence: str) -> str:
    text = sentence.lower()
    text = text.replace("n't", " not")
    text = text.replace("-", " ")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _phrase_re(phrase: str) -> re.Pattern:
    parts = [re.escape(word) for word in phrase.split()]
    return re.compile(r"\b" + r"\s+".join(parts) + r"\b")


_PHRASE_RES = {p: _phrase_re(p) for p in BATCHED_PHRASES + ONE_BY_ONE_PHRASES}


def _strip_fenced_lines(text: str) -> list[str]:
    lines: list[str] = []
    in_fence = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)
    return lines


def _sentences_from_lines(lines: list[str]) -> list[str]:
    sentences: list[str] = []
    for line in lines:
        # Inline code spans count as prose: drop the backticks only.
        line = line.replace("`", " ")
        for part in re.split(r"(?<=[.!?])\s+|\n", line):
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def extract_claims(docs_text: str) -> dict[str, list[str]]:
    """Return {family: [matched phrases]} for one docs text blob."""
    found: dict[str, list[str]] = {"batched": [], "one-by-one": []}
    for sentence in _sentences_from_lines(_strip_fenced_lines(docs_text)):
        norm = _normalise(sentence)
        if not norm:
            continue
        tokens = set(re.findall(r"[a-z]+", norm))
        negated = bool(tokens & set(NEGATION_WORDS))
        for phrase in BATCHED_PHRASES:
            if _PHRASE_RES[phrase].search(norm):
                if not negated and phrase not in found["batched"]:
                    found["batched"].append(phrase)
        for phrase in ONE_BY_ONE_PHRASES:
            if _PHRASE_RES[phrase].search(norm):
                if phrase not in found["one-by-one"]:
                    found["one-by-one"].append(phrase)
    return {k: v for k, v in found.items() if v}


def collect_docs(root: Path) -> str:
    chunks: list[str] = []
    readme = root / "README.md"
    if readme.is_file() and not readme.is_symlink():
        chunks.append(readme.read_text(encoding="utf-8", errors="replace"))
    docs_dir = root / "docs"
    if docs_dir.is_dir():
        for path in sorted(docs_dir.rglob("*.md")):
            if path.is_file() and not path.is_symlink():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
    return "\n".join(chunks)


def _is_test_path(rel: Path) -> bool:
    parts = rel.parts
    if "tests" in parts or "fixtures" in parts:
        return True
    name = rel.name
    return name.startswith("test_") or name.endswith("_test.py")


def _is_excluded_path(rel: Path) -> bool:
    parts = rel.parts
    return "docs" in parts or "examples" in parts


def _ignored(rel_posix: str, ignore: list[str]) -> bool:
    for pattern in ignore:
        if fnmatch.fnmatch(rel_posix, pattern):
            return True
        if "/" not in pattern and fnmatch.fnmatch(rel_posix.split("/")[-1], pattern):
            return True
    return False


class _EvidenceVisitor(ast.NodeVisitor):
    def __init__(self, shadowed: set[str] | None = None) -> None:
        self.items: list[dict] = []
        self.shadowed: set[str] = set(shadowed or set())

    def _add(self, node: ast.AST, kind: str, detail: str) -> None:
        self.items.append(
            {"line": getattr(node, "lineno", 0), "kind": kind, "detail": detail}
        )

    def visit_For(self, node: ast.For) -> None:
        it = node.iter
        if isinstance(it, ast.Call) and isinstance(it.func, ast.Name):
            if it.func.id == "range" and len(it.args) == 3:
                step = it.args[2]
                if isinstance(step, ast.Constant):
                    if (
                        isinstance(step.value, int)
                        and not isinstance(step.value, bool)
                        and step.value > 1
                    ):
                        self._add(node, "chunked-loop", "range step > 1")
                else:
                    self._add(node, "chunked-loop", "range variable step")
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        if isinstance(func, ast.Attribute):
            attr = func.attr
            lowered = attr.lower()
            if attr in GROUPING_NAMES:
                self._add(node, "call", attr)
            elif attr == "executemany":
                self._add(node, "call", "executemany")
            elif lowered not in ("batch", "bulk") and (
                "batch" in lowered or "bulk" in lowered
            ):
                self._add(node, "call", f"grouped-call:{attr}")
        elif isinstance(func, ast.Name):
            name = func.id
            if name in GROUPING_NAMES and name not in self.shadowed:
                self._add(node, "call", name)
            elif (
                name.lower() not in ("batch", "bulk")
                and ("batch" in name.lower() or "bulk" in name.lower())
                and name not in self.shadowed
            ):
                self._add(node, "call", f"grouped-call:{name}")
        for keyword in node.keywords:
            if keyword.arg in ("batch_size", "bulk_size"):
                value = keyword.value
                if isinstance(value, ast.Constant):
                    if value.value is None:
                        continue
                    if (
                        isinstance(value.value, int)
                        and not isinstance(value.value, bool)
                        and value.value == 1
                    ):
                        continue
                    self._add(node, "call", f"{keyword.arg}={value.value!r}")
                else:
                    self._add(node, "call", f"{keyword.arg}=variable")
        self.generic_visit(node)


def _file_evidence(path: Path) -> list[dict]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return []
    # Pre-collect file-local def/class/parameter names so a def anywhere
    # in the file suppresses Name evidence anywhere in the file.
    shadowed: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            shadowed.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in list(node.args.args) + list(node.args.kwonlyargs):
                shadowed.add(arg.arg)
    visitor = _EvidenceVisitor(shadowed)
    visitor.visit(tree)
    out: list[dict] = []
    seen: set[tuple] = set()
    for item in visitor.items:
        key = (item["line"], item["kind"], item["detail"])
        if key not in seen:
            seen.add(key)
            out.append(item)
    return out


def audit(root: Path, ignore: list[str] | None = None) -> dict:
    ignore = ignore or []
    claims: dict[str, list[str]] = {"batched": [], "one-by-one": []}
    for phrase_family, phrases in extract_claims(collect_docs(root)).items():
        claims[phrase_family] = phrases
    prod_evidence: list[dict] = []
    test_evidence: list[dict] = []
    prod_source_count = 0
    for path in sorted(root.rglob("*.py")):
        if path.is_symlink() or not path.is_file():
            continue
        rel = path.relative_to(root)
        rel_posix = rel.as_posix()
        if _ignored(rel_posix, ignore):
            continue
        if _is_excluded_path(rel):
            continue
        items = _file_evidence(path)
        annotated = [
            {"file": rel_posix, "line": it["line"], "kind": it["kind"], "detail": it["detail"]}
            for it in items
        ]
        if _is_test_path(rel):
            test_evidence.extend(annotated)
        else:
            prod_source_count += 1
            prod_evidence.extend(annotated)

    findings: list[dict] = []
    verified: list[dict] = []

    def _finding(rule: str, severity: str, evidence: list) -> dict:
        return {"rule": rule, "severity": severity, "evidence": evidence}

    claimed = [fam for fam in ("batched", "one-by-one") if claims[fam]]
    for family in claimed:
        if prod_source_count == 0:
            if family == "batched":
                findings.append(_finding("documented-batched-claim-false", "HIGH", ["no-source"]))
            else:
                findings.append(
                    _finding("documented-one-by-one-claim-false", "HIGH", ["no-source"])
                )
            continue
        if family == "batched":
            if not prod_evidence and not test_evidence:
                findings.append(
                    _finding("documented-batched-claim-false", "HIGH", ["no batching evidence"])
                )
            elif not prod_evidence:
                findings.append(
                    _finding("documented-batched-claim-false-test-only", "MEDIUM", test_evidence)
                )
            else:
                verified.append({"family": family, "phrases": claims[family]})
        else:
            if prod_evidence:
                findings.append(
                    _finding("documented-one-by-one-claim-false", "HIGH", prod_evidence)
                )
            elif test_evidence:
                findings.append(
                    _finding(
                        "documented-one-by-one-claim-false-test-only", "MEDIUM", test_evidence
                    )
                )
            else:
                verified.append({"family": family, "phrases": claims[family]})

    status = "FAIL" if findings else "PASS"
    return {
        "status": status,
        "claims": {k: v for k, v in claims.items() if v},
        "findings": findings,
        "verified_claims": verified,
    }
