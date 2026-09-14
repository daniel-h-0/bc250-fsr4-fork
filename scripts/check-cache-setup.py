#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Check the simplified cache setup's source and separately scoped qualification."""

import ast
import hashlib
import json
import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_ast(value):
    # Python 3.12 added type_params and 3.13 changed ast.dump's empty-field
    # formatting. Keep this source comparison stable across supported checkers.
    if isinstance(value, ast.AST):
        return [
            type(value).__name__,
            {
                name: canonical_ast(child)
                for name, child in ast.iter_fields(value)
                if name != "type_params"
            },
        ]
    if isinstance(value, list):
        return [canonical_ast(child) for child in value]
    if isinstance(value, bytes):
        return {"bytes": value.hex()}
    return value


def core_fingerprint(path, names):
    tree = ast.parse(path.read_text())
    nodes = [
        node
        for node in tree.body
        if isinstance(node, (ast.Import, ast.ImportFrom))
        or isinstance(node, ast.FunctionDef)
        and node.name in names
    ]
    assert {node.name for node in nodes if isinstance(node, ast.FunctionDef)} == set(names)
    value = canonical_ast(ast.Module(body=nodes, type_ignores=[]))
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def main():
    helper = runpy.run_path(str(ROOT / "scripts/shared-cache.py"))
    assert helper["TOOL_LICENSE"] == (ROOT / "LICENSE.new-code").read_text()
    record = json.loads((ROOT / "docs/data/cache-setup-game-reuse-20260914.json").read_text())
    userspaces = json.loads((ROOT / "docs/data/cache-setup-userspaces-20260914.json").read_text())
    baseline = ROOT / "legacy/cache-setup-tools"
    assert (
        record["helper_sha256"]
        == userspaces["helper_sha256"]
        == digest(baseline / "shared-cache.py")
    )
    assert (
        record["bootstrap_sha256"]
        == userspaces["bootstrap_sha256"]
        == digest(baseline / "shared-cache.sh")
    )
    assert record["driver_tool_sha256"] == digest(baseline / "driver.py")
    assert record["cache_core_ast_sha256"] == core_fingerprint(
        ROOT / "scripts/shared-cache.py", record["cache_core_functions"]
    )
    assert len(userspaces["rows"]) == 4 and userspaces["tests_per_userspace"] == 21
    assert all(row["tests_pass"] and row["readonly_fallback"] for row in userspaces["rows"])
    # Prior tooling results are tied to their immutable source commit. The GPU
    # cache-core comparison above remains against the retained measured source.
    prior = ROOT / "docs/data/cache-review-20260914.json"
    review = json.loads((ROOT / "docs/data/cache-review2-20260914.json").read_text())
    assert digest(prior) == review["previous_review_record_sha256"]
    assert review["complete"] and review["cache_core_unchanged"]
    for name, expected in review["source_sha256"].items():
        assert digest(ROOT / name) == expected, name
    assert len(review["userspaces"]) == 4
    assert all(
        row["tests_pass"]
        and row["readonly_fallback"]
        and row["cache_tests"] >= 27
        and row["driver_tests"] >= 34
        for row in review["userspaces"]
    )
    games = record["games"]
    assert [len(game["substitutions"]) for game in games] == [14, 13, 0]
    assert len(record["read_entry_sha256"]) == record["control_entries_read_by_system_shock"] == 166
    assert record["control_created_entries"] >= 166
    release = json.loads((ROOT / "docs/data/portable-dll-rc10.json").read_text())
    for game in games:
        assert game["driver_sha256"] == release["driver"]["sha256"]
        assert game["same_process_components_verified"] and game["view_has_expected_shared_store"]
        assert game["private_translation_cache"] and game["cache_core_matches_current"]
        assert (
            game["original_files_and_settings_restored"]
            and game["save_payloads_rechecked_after_cloud_restore"]
        )
        assert game["session_is_not_a_startup_benchmark"]
        assert game["screenshot"]["pixels_unedited"]
        assert digest(ROOT / game["screenshot"]["path"]) == game["screenshot"]["sha256"]
    print(
        "PASS: retained game-to-game evidence, unchanged cache core and reviewed tools in four userspaces"
    )


if __name__ == "__main__":
    main()
