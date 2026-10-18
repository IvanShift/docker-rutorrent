"""Block scalar reference cases for the strict workflow YAML subset.

Expected strings were checked against PyYAML 6.0.3; CI needs no YAML package.
"""

import subprocess
import unittest
from pathlib import Path

from workflow_yaml import WorkflowYamlError, load


ROOT = Path(__file__).resolve().parents[2]


class WorkflowBlockScalarTests(unittest.TestCase):
    def test_folded_scalars_are_refused_in_every_block_position(self):
        for indicator in (">", ">-", ">+"):
            for document in (
                f"run: {indicator}\n  echo hello\n",
                f"jobs:\n  publish:\n    if: {indicator}\n      success()\n",
                f"- {indicator}\n  echo hello\n",
                f"steps:\n  - run: {indicator}\n      echo hello\n",
            ):
                with self.subTest(document=document), self.assertRaises(WorkflowYamlError):
                    load(document)

    def test_more_indented_folded_comment_cannot_hide_failure_masking(self):
        body = (
            "  echo before\n"
            "    # More indentation preserves surrounding line breaks in YAML\n"
            "  command set +e\n"
            "\n"
            "  false\n"
            "\n"
            "  echo after\n"
        )
        # This is YAML's decoded folded text. Joining the comment with the
        # following command would hide `set +e` from the workflow contracts.
        reference_script = (
            "echo before\n"
            "  # More indentation preserves surrounding line breaks in YAML\n"
            "command set +e\n"
            "false\n"
            "echo after\n"
        )
        result = subprocess.run(["bash", "-e", "-c", reference_script], capture_output=True, text=True, check=False)
        self.assertEqual((result.returncode, result.stdout), (0, "before\nafter\n"))
        with self.assertRaises(WorkflowYamlError):
            load("run: >\n" + body)
        workflow_text = (ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8")
        original = "        run: python3 -m unittest discover -s tests/ci -p 'test_*.py'\n"
        folded = "        run: >\n" + "".join("        " + line for line in body.splitlines(keepends=True))
        mutated = workflow_text.replace(original, folded)
        self.assertNotEqual(mutated, workflow_text)
        with self.assertRaises(WorkflowYamlError):
            load(mutated)

    def test_literal_chomping_preserves_actual_line_breaks(self):
        cases = (
            ("  echo hello", "echo hello", "echo hello", "echo hello"),
            ("  echo hello\n", "echo hello\n", "echo hello", "echo hello\n"),
            ("  echo hello\n\n\n", "echo hello\n", "echo hello", "echo hello\n\n\n"),
            ("", "", "", ""),
            ("\n\n", "", "", "\n\n"),
            ("  \n  \n", "", "", "\n\n"),
        )
        for body, clip, strip, keep in cases:
            for indicator, expected in (("|", clip), ("|-", strip), ("|+", keep)):
                # An unterminated content line is only possible at EOF.
                suffixes = ("", "next: value\n") if not body or body.endswith("\n") else ("",)
                for suffix in suffixes:
                    with self.subTest(indicator=indicator, body=body, suffix=suffix):
                        document = load(f"run: {indicator}\n{body}{suffix}")
                        self.assertEqual(document["run"], expected)
                        if suffix:
                            self.assertEqual(document["next"], "value")

    def test_literal_indentation_and_blank_content_are_preserved(self):
        document = "run: |+\n\n  echo before\n    # a literal comment\n    \n \n  echo after\n\nnext: value\n"
        self.assertEqual(load(document), {
            "run": "\necho before\n  # a literal comment\n  \n\necho after\n\n",
            "next": "value",
        })
        # YAML rejects leading blank lines deeper than the first content line.
        with self.assertRaises(WorkflowYamlError):
            load("run: |+\n    \n  echo after\n")

    def test_literal_block_keeps_comment_and_shell_command_separate(self):
        document = "run: |\n  # do not run the next command as part of this comment\n  false\n  echo after\n"
        script = load(document)["run"]
        result = subprocess.run(["bash", "-e", "-c", script], capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_explicit_indentation_modifiers_remain_unsupported(self):
        for indicator in ("|2", "|2-", "|-2", ">2", ">+2"):
            with self.subTest(indicator=indicator), self.assertRaises(WorkflowYamlError):
                load(f"run: {indicator}\n  echo hello\n")

    def test_publish_condition_keeps_its_original_expression(self):
        document = load((ROOT / ".github/workflows/build.yml").read_text(encoding="utf-8"))
        self.assertEqual(document["jobs"]["publish"]["if"],
                         "success() && (github.event_name == 'schedule' || "
                         "(github.event_name == 'push' && github.ref == 'refs/heads/master'))")


if __name__ == "__main__":
    unittest.main()
