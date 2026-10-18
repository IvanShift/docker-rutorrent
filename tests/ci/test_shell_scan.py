"""The CI shell lexer (shell_scan.py) against the shell it models.

Each source runs under the host sh with docker as a shell function that only
records its arguments, so no container starts. The docker commands sh runs
must be the ones commands() reports. Spellings on which dash and bash
disagree must raise ShellSyntaxError, so a contract fails closed on them.
"""

import os
import subprocess
import tempfile
import unittest

from shell_scan import ShellSyntaxError, commands, function_commands

RECORDER = 'unset u w x y z\ndocker() { printf "docker %s\\n" "$*" >> "$CALLS"; }\n'
BODY = "$(docker run -d a) `docker rm b`\n"
# Delimiter spellings: the body is expanded only when no part is quoted.
DELIMITERS = {"EOF": True, "E\\\nOF": True, "'EOF'": False, '"EOF"': False, "\\EOF": False,
              'E"O"F': False, "E'OF'": False, "E\\OF": False}


def run_by_sh(source):
    """The docker commands sh runs from source, as sorted strings."""
    with tempfile.TemporaryDirectory() as work:
        calls = os.path.join(work, "calls")
        open(calls, "w", encoding="utf-8").close()
        subprocess.run(["sh", "-c", RECORDER + source], cwd=work, env={**os.environ, "CALLS": calls},
                       stdout=subprocess.DEVNULL, check=True, timeout=30)
        with open(calls, encoding="utf-8") as handle:
            return sorted(handle.read().splitlines())


def seen_by_scan(source):
    """The docker commands commands() reports for source, as sorted strings."""
    return sorted(" ".join(words) for words in commands(source) if words[:1] == ["docker"])


class ShellScanTestCase(unittest.TestCase):
    def assertScanMatchesSh(self, source, expected):
        with self.subTest(source=source):
            self.assertEqual(run_by_sh(source), sorted(expected), "the case no longer models sh")
            self.assertEqual(seen_by_scan(source), sorted(expected))


class HereDocumentTests(ShellScanTestCase):
    def test_only_an_unquoted_delimiter_expands_the_body(self):
        for operator, indent in (("<<", ""), ("<<-", "\t")):
            for delimiter, expands in DELIMITERS.items():
                source = f"cat {operator}{delimiter}\n{indent}{BODY}{indent}EOF\n"
                self.assertScanMatchesSh(source, ["docker run -d a", "docker rm b"] if expands else [])

    def test_quotes_in_an_unquoted_body_are_plain_characters(self):
        source = ("cat <<EOF\n'$(docker run -d single)' \"$(docker run -d double)\" ${x:-$(docker rm p)}\n"
                  "\\$(docker run -d escaped) \\`docker run -d escaped\\` \\${x:-$(docker rm q)}\nEOF\n")
        self.assertScanMatchesSh(source, ["docker run -d single", "docker run -d double", "docker rm p",
                                          "docker rm q"])

    def test_each_here_document_of_a_line_keeps_its_own_quoting(self):
        self.assertScanMatchesSh("cat <<A <<'B'\n$(docker rm a)\nA\n$(docker rm b)\nB\n", ["docker rm a"])
        self.assertScanMatchesSh("cat <<'A' <<-B\n$(docker rm a)\nA\n\t$(docker rm b)\n\tB\n", ["docker rm b"])

    def test_a_line_continuation_joins_unquoted_body_lines(self):
        # The joined line "xEOF" is not the delimiter, so the body reads on.
        hidden = "cat {}\nx\\\nEOF\necho '\nEOF\ndocker run -d hidden # '\n"
        self.assertScanMatchesSh(hidden.format("<<EOF"), ["docker run -d hidden"])
        self.assertScanMatchesSh(hidden.format("<<'EOF'"), [])
        self.assertScanMatchesSh("cat <<-EOF\n\tx\\\n\tEOF\n\tEOF\n", [])
        self.assertScanMatchesSh("cat <<EOF\nx\\\\\nEOF\ndocker rm after\n", ["docker rm after"])
        self.assertScanMatchesSh("cat <<EOF\n$\\\n(docker run -d joined)\nEOF\n", ["docker run -d joined"])

    def test_a_line_continuation_inside_the_operator(self):
        self.assertScanMatchesSh("cat <\\\n<EOF\necho '\nEOF\ndocker run -d hidden # '\n",
                                 ["docker run -d hidden"])
        self.assertScanMatchesSh("cat <<\\\n-EOF\n\t$(docker run -d stripped)\n\tEOF\n",
                                 ["docker run -d stripped"])
        self.assertScanMatchesSh("true &\\\n& docker rm joined\n", ["docker rm joined"])

    def test_function_bodies_include_their_here_document_substitutions(self):
        source = "cleanup() {\n  cat <<EOF\n$(smoke_remove_container x)\nEOF\n}\ncleanup\n"
        self.assertEqual(list(function_commands(source, "cleanup")), [["smoke_remove_container", "x"], ["cat"]])
        quoted = source.replace("<<EOF", "<<'EOF'")
        self.assertEqual(list(function_commands(quoted, "cleanup")), [["cat"]])

    def test_spellings_the_shells_read_differently_fail_closed(self):
        for source in (
                # bash ends at the joined line "EOF"; dash and busybox read on.
                "cat <<EOF\nEO\\\nF\ndocker rm x\nEOF\n",
                "cat <<-EOF\n\tEOF\\\n\nEOF\n",
                # bash ends at the delimiter; dash parses the substitution across it.
                "cat <<EOF\n$(echo x\nEOF\n)\nEOF\n",
                "cat <<EOF\n`echo x\nEOF\n`\nEOF\n",
                "cat <<EOF\n${x:-\nEOF\n}\nEOF\n",
                # Expansion characters in a delimiter: $'...' is a quote in bash and
                # busybox but a dollar and a quote in dash.
                "cat <<$'EOF'\nEOF\ndocker rm x\n$EOF\n",
                'cat <<$"EOF"\nEOF\n',
                "cat <<$EOF\n$EOF\n",
                "cat <<`EOF`\n`EOF`\n",
                "cat <<EOF\nno delimiter\n",
                "cat <<EOF\n"):
            with self.subTest(source=source):
                with self.assertRaises(ShellSyntaxError):
                    list(commands(source))


class ExpansionTests(ShellScanTestCase):
    def test_substitutions_inside_parameter_expansions(self):
        self.assertScanMatchesSh('echo "${x:-$(docker run -d p)}" ${y:-`docker run -d q`} '
                                 '"${z:-${w:-$(docker rm v)}}" ${u:-"$(docker rm t)"}\n',
                                 ["docker run -d p", "docker run -d q", "docker rm v", "docker rm t"])

    def test_a_line_continuation_after_the_dollar(self):
        self.assertScanMatchesSh('echo "$\\\n(docker run -d r)" $\\\n{x:-$(docker rm s)}\n',
                                 ["docker run -d r", "docker rm s"])

    def test_a_comment_inside_a_substitution_hides_its_parenthesis(self):
        self.assertScanMatchesSh("x=$(true # )'\ndocker run -d hidden >&2\n#'\n)\n", ["docker run -d hidden"])
        self.assertScanMatchesSh("x=$(echo a#b ${#x} $# 'c # )' a\\\n#d; docker rm e)\n", ["docker rm e"])
        self.assertScanMatchesSh("x=$(echo $(echo a)#b ${x:-)}; docker rm f)\n", ["docker rm f"])


if __name__ == "__main__":
    unittest.main()
