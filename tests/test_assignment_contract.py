"""Guard the promise that grading depends only on the public CRC harness contract.

For P3 the graded bundle tests drive scenarios exclusively through
``crc_support.CRCTestManager``. They never import the student's server or poke
its private state, so the whole grading surface stays behavioral. These guards
keep that true and keep the bundle markers consistent with each file's role.
"""

from __future__ import annotations

import ast
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Each graded bundle file maps to the single bundle number every one of its
# graded test functions must carry.
GRADED_BUNDLES = {
    "test_bundle1_core.py": 1,
    "test_bundle2_routing.py": 2,
    "test_bundle3_multihop.py": 3,
}


def _imported_modules(tree: ast.AST) -> set[str]:
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
        elif isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
    return modules


def test_graded_bundle_tests_use_only_the_harness_contract():
    violations = []

    for filename in GRADED_BUNDLES:
        path = PROJECT_ROOT / "tests" / filename
        assert path.exists(), f"missing graded bundle file: {filename}"
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)
        modules = _imported_modules(tree)

        if "crc_support.CRCTestManager" not in modules:
            violations.append(f"{filename}: must import crc_support.CRCTestManager")

        # The graded surface is behavioral; importing the student server (or any
        # of its internals) would couple grading to a private design.
        server_imports = sorted(m for m in modules if m == "src.ChatServer" or m.startswith("src."))
        if server_imports:
            violations.append(f"{filename}: must not import the student server {server_imports}")

    assert violations == []


def test_every_graded_test_carries_its_files_bundle_marker():
    violations = []

    for filename, expected_bundle in GRADED_BUNDLES.items():
        path = PROJECT_ROOT / "tests" / filename
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=filename)

        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef) or not node.name.startswith("test_"):
                continue
            bundles = []
            for decorator in node.decorator_list:
                if (
                    isinstance(decorator, ast.Call)
                    and isinstance(decorator.func, ast.Attribute)
                    and decorator.func.attr == "bundle"
                    and decorator.args
                ):
                    bundles.append(ast.literal_eval(decorator.args[0]))
            if bundles != [expected_bundle]:
                violations.append(
                    f"{filename}:{node.name} has bundle markers {bundles}, "
                    f"expected [{expected_bundle}]"
                )

    assert violations == []


def test_protocol_doc_documents_the_message_contract():
    protocol = (PROJECT_ROOT / "PROTOCOL.md").read_text(encoding="utf-8")

    required_terms = {
        "ServerRegistrationMessage",
        "ClientRegistrationMessage",
        "StatusUpdateMessage",
        "ClientChatMessage",
        "ClientQuitMessage",
        "last_hop_id",
        "first_link_id",
        "selectors",
        "spanning tree",
    }

    missing = sorted(term for term in required_terms if term not in protocol)
    assert missing == []
