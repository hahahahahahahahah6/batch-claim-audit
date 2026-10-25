"""Tests for batch-claim-audit."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from batch_claim_audit.audit import audit, extract_claims

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SRC = Path(__file__).resolve().parents[1] / "src"
MARKER = "zzz-test-marker-7f3a"


def claims_of(text):
    return extract_claims(text)


class ClaimExtractionTests(unittest.TestCase):
    def test_fenced_code_excluded(self):
        text = "```\nThis tool processes records in batches.\n```\nPlain text."
        self.assertEqual(claims_of(text), {})

    def test_inline_code_counts(self):
        text = "This tool processes records `in batches`."
        self.assertIn("in batches", claims_of(text).get("batched", []))

    def test_not_batched_is_one_by_one_only(self):
        found = claims_of("The output is not batched.")
        self.assertEqual(found.get("batched", []), [])
        self.assertIn("not batched", found.get("one-by-one", []))

    def test_no_batching_is_one_by_one(self):
        self.assertIn("no batching", claims_of("There is no batching here.").get("one-by-one", []))

    def test_does_not_batch_is_one_by_one(self):
        self.assertIn("does not batch", claims_of("It does not batch.").get("one-by-one", []))

    def test_doesnt_batch_is_one_by_one(self):
        self.assertIn("does not batch", claims_of("It doesn't batch.").get("one-by-one", []))

    def test_never_batches_is_one_by_one(self):
        self.assertIn("never batches", claims_of("It never batches.").get("one-by-one", []))

    def test_hyphen_normalised(self):
        self.assertIn("batch processing", claims_of("Uses batch-processing throughout.").get("batched", []))

    def test_unbatched_alone_no_claim(self):
        self.assertEqual(claims_of("The output is unbatched."), {})

    def test_bare_batch_no_claim(self):
        self.assertEqual(claims_of("Batch notes follow."), {})

    def test_bare_batch_size_no_claim(self):
        self.assertEqual(claims_of("Set the batch size to 100."), {})

    def test_processes_items_in_batches(self):
        self.assertIn("in batches", claims_of("It processes items in batches.").get("batched", []))

    def test_bare_batched_is_claim(self):
        self.assertIn("batched", claims_of("Output is batched.").get("batched", []))

    def test_one_by_one(self):
        self.assertIn("one by one", claims_of("Handles records one by one.").get("one-by-one", []))

    def test_one_at_a_time(self):
        self.assertIn("one at a time", claims_of("Sends one at a time.").get("one-by-one", []))

    def test_item_by_item(self):
        self.assertIn("item by item", claims_of("Walks item by item.").get("one-by-one", []))

    def test_without_batching(self):
        self.assertIn("without batching", claims_of("Runs without batching.").get("one-by-one", []))

    def test_processes_items_individually(self):
        found = claims_of("It processes items individually.")
        self.assertIn("processes items individually", found.get("one-by-one", []))

    def test_bulk_insert(self):
        self.assertIn("bulk insert", claims_of("Uses bulk insert for rows.").get("batched", []))

    def test_negated_batched_suppressed_entirely(self):
        # "does not process in batches": batched phrase suppressed by
        # negation, and no one-by-one phrase matches either.
        self.assertEqual(claims_of("It does not process in batches."), {})

    def test_both_families_independent(self):
        found = claims_of("Part A runs in batches. Part B runs one by one.")
        self.assertIn("in batches", found.get("batched", []))
        self.assertIn("one by one", found.get("one-by-one", []))

    def test_sentence_split(self):
        found = claims_of("It does not batch. The other half runs in batches.")
        self.assertIn("does not batch", found.get("one-by-one", []))
        self.assertIn("in batches", found.get("batched", []))


class AuditTreeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def _tree(self, readme, files):
        (self.root / "README.md").write_text(readme, encoding="utf-8")
        for rel, code in files.items():
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(code, encoding="utf-8")
        return self.root

    def _batched_claim(self, code, readme="Runs in batches."):
        return audit(self._tree(readme, {"main.py": code}))

    def test_batched_call_counts(self):
        result = self._batched_claim(
            "from itertools import batched\nfor g in batched(items, 10):\n    send(g)\n"
        )
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["verified_claims"][0]["family"], "batched")

    def test_chunked_call_counts(self):
        result = self._batched_claim("for g in chunked(items, 10):\n    send(g)\n")
        self.assertEqual(result["status"], "PASS")

    def test_import_alone_does_not_count(self):
        result = self._batched_claim("from itertools import batched\nx = 1\n")
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"][0]["rule"], "documented-batched-claim-false")

    def test_stepped_range_literal_counts(self):
        result = self._batched_claim(
            "for i in range(0, len(items), 50):\n    send(items[i:i + 50])\n"
        )
        self.assertEqual(result["status"], "PASS")

    def test_range_two_args_does_not_count(self):
        result = self._batched_claim("for i in range(len(items)):\n    send(items[i])\n")
        self.assertEqual(result["status"], "FAIL")

    def test_range_step_one_does_not_count(self):
        result = self._batched_claim("for i in range(0, len(items), 1):\n    send(items[i])\n")
        self.assertEqual(result["status"], "FAIL")

    def test_range_variable_step_counts(self):
        result = self._batched_claim(
            "for i in range(0, len(items), step):\n    send(items[i:i + step])\n"
        )
        self.assertEqual(result["status"], "PASS")

    def test_bulk_create_call_counts(self):
        result = self._batched_claim("bulk_create(rows)\n")
        self.assertEqual(result["status"], "PASS")

    def test_local_def_suppresses_name_bulk_create(self):
        result = self._batched_claim(
            "def bulk_create(rows):\n    pass\nbulk_create(rows)\n"
        )
        self.assertEqual(result["status"], "FAIL")

    def test_executemany_counts(self):
        result = self._batched_claim("cur.executemany(sql, rows)\n")
        self.assertEqual(result["status"], "PASS")

    def test_execute_alone_does_not_count(self):
        result = self._batched_claim("cur.execute(sql, row)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_batch_size_literal_gt_one_counts(self):
        result = self._batched_claim("load(items, batch_size=100)\n")
        self.assertEqual(result["status"], "PASS")

    def test_batch_size_one_does_not_count(self):
        result = self._batched_claim("load(items, batch_size=1)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_batch_size_none_does_not_count(self):
        result = self._batched_claim("load(items, batch_size=None)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_batch_size_variable_counts(self):
        result = self._batched_claim("load(items, batch_size=size)\n")
        self.assertEqual(result["status"], "PASS")

    def test_bulk_size_keyword_counts(self):
        result = self._batched_claim("load(items, bulk_size=25)\n")
        self.assertEqual(result["status"], "PASS")

    def test_variable_named_batch_does_not_count(self):
        result = self._batched_claim("batch = []\nfor x in items:\n    batch.append(x)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_string_literal_batch_does_not_count(self):
        result = self._batched_claim('msg = "batch"\nprint(msg)\n')
        self.assertEqual(result["status"], "FAIL")

    def test_attribute_bulk_create_counts(self):
        result = self._batched_claim("store.bulk_create(rows)\n")
        self.assertEqual(result["status"], "PASS")

    def test_attribute_batched_counts(self):
        result = self._batched_claim("import itertools\nfor g in itertools.batched(items, 5):\n    send(g)\n")
        self.assertEqual(result["status"], "PASS")

    def test_plain_loop_does_not_count(self):
        result = self._batched_claim("for x in items:\n    send(x)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_dead_code_counts(self):
        result = self._batched_claim(
            "def never_called(items):\n    for g in batched(items, 5):\n        send(g)\n"
        )
        self.assertEqual(result["status"], "PASS")

    def test_ignore_drops_evidence(self):
        root = self._tree(
            "Runs in batches.",
            {"main.py": "for g in batched(items, 5):\n    send(g)\n"},
        )
        result = audit(root, ignore=["main.py"])
        self.assertEqual(result["status"], "FAIL")
        # Ignoring the only source file triggers the no-source rule.
        self.assertEqual(result["findings"][0]["evidence"], ["no-source"])

    def test_both_families_falsified(self):
        root = self._tree(
            "Runs in batches. Also runs one by one.",
            {"main.py": "x = 1\n"},
        )
        # Batched claim falsified (no evidence); one-by-one verified.
        result = audit(root)
        rules = [f["rule"] for f in result["findings"]]
        self.assertEqual(rules, ["documented-batched-claim-false"])

    def test_both_families_both_falsified(self):
        # One-by-one falsified needs evidence; batched falsified needs
        # none — impossible together with source present, so use the
        # no-source case: both families present, no .py files at all.
        root = self._tree("Runs in batches. Also runs one by one.", {})
        result = audit(root)
        rules = sorted(f["rule"] for f in result["findings"])
        self.assertEqual(
            rules,
            ["documented-batched-claim-false", "documented-one-by-one-claim-false"],
        )

    def test_no_source_single_family(self):
        root = self._tree("Runs in batches.", {})
        result = audit(root)
        self.assertEqual(result["findings"][0]["severity"], "HIGH")
        self.assertEqual(result["findings"][0]["evidence"], ["no-source"])

    def test_no_claim_passes_with_evidence(self):
        result = audit(self._tree("A plain tool.", {"main.py": "for g in batched(items, 5):\n    send(g)\n"}))
        self.assertEqual(result["status"], "PASS")
        self.assertEqual(result["findings"], [])

    def test_one_by_one_falsified_by_evidence(self):
        result = audit(
            self._tree(
                "Handles records one by one.",
                {"main.py": "for g in batched(items, 5):\n    send(g)\n"},
            )
        )
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"][0]["rule"], "documented-one-by-one-claim-false")
        self.assertEqual(result["findings"][0]["severity"], "HIGH")

    def test_test_only_evidence_medium(self):
        root = self._tree(
            "Runs in batches.",
            {
                "main.py": "x = 1\n",
                "tests/test_work.py": "for g in batched(items, 5):\n    send(g)\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["status"], "FAIL")
        self.assertEqual(result["findings"][0]["rule"], "documented-batched-claim-false-test-only")
        self.assertEqual(result["findings"][0]["severity"], "MEDIUM")

    def test_test_only_one_by_one_medium(self):
        root = self._tree(
            "Handles records one by one.",
            {
                "main.py": "x = 1\n",
                "tests/test_work.py": "for g in batched(items, 5):\n    send(g)\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["findings"][0]["rule"], "documented-one-by-one-claim-false-test-only")
        self.assertEqual(result["findings"][0]["severity"], "MEDIUM")

    def test_root_test_file_is_test_only(self):
        root = self._tree(
            "Runs in batches.",
            {
                "main.py": "x = 1\n",
                "test_work.py": "for g in batched(items, 5):\n    send(g)\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["findings"][0]["severity"], "MEDIUM")

    def test_docs_md_claim_counts(self):
        root = self._tree(
            "A plain tool.",
            {
                "main.py": "x = 1\n",
                "docs/guide.md": "This step runs in batches.\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["findings"][0]["rule"], "documented-batched-claim-false")

    def test_docs_py_not_evidence(self):
        root = self._tree(
            "Handles records one by one.",
            {
                "main.py": "x = 1\n",
                "docs/example.py": "for g in batched(items, 5):\n    send(g)\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["status"], "PASS")

    def test_examples_py_not_evidence(self):
        root = self._tree(
            "Handles records one by one.",
            {
                "main.py": "x = 1\n",
                "examples/demo.py": "for g in batched(items, 5):\n    send(g)\n",
            },
        )
        result = audit(root)
        self.assertEqual(result["status"], "PASS")

    def test_symlink_skipped(self):
        target = self.root / "real.py"
        target.write_text("for g in batched(items, 5):\n    send(g)\n", encoding="utf-8")
        link = self.root / "linked.py"
        try:
            link.symlink_to(target)
        except OSError:
            self.skipTest("symlinks unavailable")
        (self.root / "README.md").write_text("Handles records one by one.", encoding="utf-8")
        result = audit(self.root)
        # Only real.py is production source and it HAS evidence, so the
        # one-by-one claim is falsified via the real file, not the link.
        self.assertEqual(result["findings"][0]["evidence"][0]["file"], "real.py")

    def test_exact_name_batch_call_does_not_count(self):
        result = self._batched_claim("batch(items)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_exact_name_bulk_call_does_not_count(self):
        result = self._batched_claim("bulk(items)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_local_def_suppresses_name_chunked(self):
        result = self._batched_claim("def chunked(xs, n):\n    pass\nchunked(items, 5)\n")
        self.assertEqual(result["status"], "FAIL")

    def test_batch_size_on_plain_call_counts(self):
        result = self._batched_claim("send(items, batch_size=50)\n")
        self.assertEqual(result["status"], "PASS")

    def test_syntax_error_file_ignored(self):
        result = self._batched_claim("def broken(:\n")
        self.assertEqual(result["status"], "FAIL")


class FixtureCliTests(unittest.TestCase):
    def _run(self, *args):
        env = dict(os.environ)
        env["PYTHONPATH"] = str(SRC)
        proc = subprocess.run(
            ["python3", "-m", "batch_claim_audit", *args],
            capture_output=True,
            text=True,
            env=env,
        )
        return proc

    def test_good_batched_fixture(self):
        proc = self._run(str(FIXTURES / "good" / "batched"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("PASS", proc.stdout)

    def test_good_onebyone_fixture(self):
        proc = self._run(str(FIXTURES / "good" / "onebyone"))
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_bad_batched_fixture_exact_finding(self):
        proc = self._run("--json", str(FIXTURES / "bad" / "batched"))
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["rule"], "documented-batched-claim-false")
        self.assertEqual(data["findings"][0]["severity"], "HIGH")
        self.assertNotIn(MARKER, proc.stdout + proc.stderr)

    def test_bad_onebyone_fixture_exact_finding(self):
        proc = self._run("--json", str(FIXTURES / "bad" / "onebyone"))
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["rule"], "documented-one-by-one-claim-false")

    def test_testonly_medium_and_fail_on_high(self):
        proc = self._run("--json", str(FIXTURES / "testonly" / "batched"))
        self.assertEqual(proc.returncode, 2)
        data = json.loads(proc.stdout)
        self.assertEqual(len(data["findings"]), 1)
        self.assertEqual(data["findings"][0]["severity"], "MEDIUM")
        proc2 = self._run("--fail-on", "HIGH", str(FIXTURES / "testonly" / "batched"))
        self.assertEqual(proc2.returncode, 0)
        self.assertIn("MEDIUM", proc2.stdout)

    def test_json_shape(self):
        proc = self._run("--json", str(FIXTURES / "good" / "batched"))
        data = json.loads(proc.stdout)
        self.assertEqual(data["status"], "PASS")
        self.assertIn("findings", data)
        self.assertIn("verified_claims", data)
        self.assertEqual(data["verified_claims"][0]["family"], "batched")

    def test_version(self):
        proc = self._run("--version")
        self.assertEqual(proc.returncode, 0)
        self.assertEqual(proc.stdout.strip(), "0.1.0")

    def test_usage_error(self):
        proc = self._run(str(FIXTURES / "does-not-exist"))
        self.assertEqual(proc.returncode, 1)

    def test_verbose_prints_claims(self):
        proc = self._run("--verbose", str(FIXTURES / "good" / "batched"))
        self.assertEqual(proc.returncode, 0)
        self.assertIn("claims", proc.stdout)


if __name__ == "__main__":
    unittest.main()
