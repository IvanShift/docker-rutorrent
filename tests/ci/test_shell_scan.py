"""The CI shell lexer (shell_scan.py) against the shell it models.

Each source runs under the host sh (and bash where it says so) with docker as
a stub program first in PATH that only records its arguments, so no
container starts, also not from a nested shell. The docker commands the
shell runs must be the ones commands() reports. Spellings outside the
supported grammar, or that dash and bash read differently, must raise
ShellSyntaxError, so a contract fails closed on them; where a shell runs
them, the test also shows what it runs.
"""

import os
import subprocess
import tempfile
import unittest

from shell_scan import ShellSyntaxError, command_start, commands, function_commands, simple_command, top_level_lists

RECORDER = "unset u w x y z\n"
STUB = '#!/bin/sh\nprintf "docker %s\\n" "$*" >> "$CALLS"\n'
BODY = "$(docker run -d a) `docker rm b`\n"
# Delimiter spellings: the body is expanded only when no part is quoted.
DELIMITERS = {"EOF": True, "E\\\nOF": True, "'EOF'": False, '"EOF"': False, "\\EOF": False,
              'E"O"F': False, "E'OF'": False, "E\\OF": False}
BOTH = ("sh", "bash")


def run_by_sh(source, shell="sh"):
    """The docker commands shell runs from source, as sorted strings."""
    with tempfile.TemporaryDirectory() as work:
        calls, stubs = os.path.join(work, "calls"), os.path.join(work, "bin")
        open(calls, "w", encoding="utf-8").close()
        os.mkdir(stubs)
        with open(os.path.join(stubs, "docker"), "w", encoding="utf-8") as stub:
            stub.write(STUB)
        os.chmod(os.path.join(stubs, "docker"), 0o755)
        environment = {**os.environ, "CALLS": calls, "PATH": stubs + os.pathsep + os.environ.get("PATH", os.defpath),
                       "DOCKER_HOST": "unix:///nonexistent/docker.sock"}
        result = subprocess.run([shell, "-c", RECORDER + source], cwd=work, env=environment, stdin=subprocess.DEVNULL,
                                stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True, timeout=30)
        if result.returncode:
            raise AssertionError(f"{shell} failed ({result.returncode}): {result.stderr}")
        with open(calls, encoding="utf-8") as handle:
            return sorted(handle.read().splitlines())


def seen_by_scan(source):
    """The docker commands commands() reports for source, as sorted strings.

    Reserved words, assignments and wrappers before docker are dropped.
    """
    found = []
    for words in commands(source):
        command = simple_command(words)
        if command.name == "docker":
            found.append(" ".join(words[command.start:]))
    return sorted(found)


class ShellScanTestCase(unittest.TestCase):
    def assertScanMatchesSh(self, source, expected, shells=("sh",)):
        for shell in shells:
            with self.subTest(source=source, shell=shell):
                self.assertEqual(run_by_sh(source, shell), sorted(expected), "the case no longer models the shell")
                self.assertEqual(seen_by_scan(source), sorted(expected))

    def assertRefused(self, source, ran=None, shells=("sh",)):
        """commands() raises; where ran is given, each shell runs those docker commands."""
        with self.subTest(source=source):
            if ran is not None:
                for shell in shells:
                    self.assertEqual(run_by_sh(source, shell), sorted(ran), f"the case no longer models {shell}")
            with self.assertRaises(ShellSyntaxError):
                list(commands(source))


class CommandTests(ShellScanTestCase):
    """Generic lexing: what a command is, and what is data."""

    def test_commands_hidden_by_syntax_are_found(self):
        cases = {
            'docker \\\n  rm -f "$c"\n': ["docker", "rm", "-f", "$c"],
            'cid=$(docker run -d alpine)\n': ["docker", "run", "-d", "alpine"],
            'x="$(docker create a)"\n': ["docker", "create", "a"],
            'y=`docker rm z`\n': ["docker", "rm", "z"],
            "sh -c 'docker rm x'\n": ["docker", "rm", "x"],
            'eval "docker run y"\n': ["docker", "run", "y"],
        }
        for source, expected in cases.items():
            with self.subTest(source=source):
                self.assertIn(expected, list(commands(source)))

    def test_comments_quotes_and_here_documents_are_data(self):
        source = ("# docker rm a\necho 'docker rm b' \"docker run c\" # docker rm d\n"
                  "cat <<EOF\ndocker rm e\nEOF\ndocker exec x sh -ec 'docker rm f'\n")
        self.assertEqual(list(commands(source)), [["echo", "docker rm b", "docker run c"], ["cat"],
                                                  ["docker", "exec", "x", "sh", "-ec", "docker rm f"]])
        self.assertEqual(list(commands("cmd a >/dev/null 2>&1 3< in\n")), [["cmd", "a"]])
        with self.assertRaises(ShellSyntaxError):
            list(commands("echo 'unterminated\n"))

    def test_function_bodies_are_found_without_comments(self):
        source = "cleanup() {\n  # smoke_remove_container\n  rm -rf x\n}\nsmoke_remove_container y\n"
        self.assertEqual(list(function_commands(source, "cleanup")), [["rm", "-rf", "x"]])
        self.assertEqual(command_start(["X=1", "timeout", "--signal=KILL", "5", "sh", "-c", "x"]), 4)

    def test_only_unconditional_top_level_lists_are_executed(self):
        script = ("a \\\n  \"b\"  # comment\n# c\nif false; then\n  d\nfi\nf() {\n  e\n}\n"
                  "for x in y; do\n  g\ndone\ncase $x in z) h ;; esac\necho 'i # j'\nk &\n(m)\nn; o\n")
        self.assertEqual(top_level_lists(script), [("a", '"b"'), ("echo", "'i # j'"), ("n",), ("o",)])
        with self.assertRaises(ShellSyntaxError):
            top_level_lists('echo "\ncmd\n')

    def test_case_patterns_are_not_commands(self):
        source = "case $x in sh|bash) docker rm a ;; (docker) docker rm b ;; if) : ;; esac\n"
        self.assertEqual(list(commands(source)), [["case", "$x", "in"], ["docker", "rm", "a"], ["docker", "rm", "b"],
                                                  [":"], ["esac"]])
        self.assertEqual(top_level_lists(source + "c\n"), [("c",)])


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
                "cat <<EOF\n",
                # bash reads this body after the substitution; dash runs it as code.
                "x=$(cat <<EOF)\ndocker rm body\nEOF\n"):
            self.assertRefused(source)


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

    def test_bash_quoting_with_a_dollar_is_refused(self):
        # bash runs docker here, dash runs a command named "$\x64ocker".
        self.assertRefused("$'\\x64ocker' rm q\n", ["docker rm q"], shells=("bash",))
        self.assertRefused('$"docker" rm l\n', ["docker rm l"], shells=("bash",))
        self.assertRefused("echo ${x:-$'a'}\n")
        self.assertRefused("echo \"${x:-$'a'}\"\n")


class SubstitutionGrammarTests(ShellScanTestCase):
    """A $(...) ends at the ')' the shell grammar ends it at."""

    def test_a_case_pattern_does_not_end_the_substitution(self):
        self.assertScanMatchesSh("x=$(case a in a)docker run -d hidden;; esac)\n", ["docker run -d hidden"], BOTH)
        self.assertScanMatchesSh("for v in a b; do x=$(case $v in (a) docker rm a;; b|c) docker rm bc;; esac); done\n",
                                 ["docker rm a", "docker rm bc"], BOTH)
        self.assertScanMatchesSh("x=$(case a in a) y=$(case b in b) docker rm inner;; esac); docker rm outer;; esac)\n",
                                 ["docker rm inner", "docker rm outer"], BOTH)
        self.assertScanMatchesSh('echo "${u:-$(case a in a) docker rm p;; esac)}" "$(case a in a) docker rm q\nesac)"\n',
                                 ["docker rm p", "docker rm q"], BOTH)
        self.assertScanMatchesSh("x=$(case a in a) docker rm c # )\n;; esac)\n", ["docker rm c"], BOTH)

    def test_here_documents_parentheses_and_arithmetic_inside_a_substitution(self):
        self.assertScanMatchesSh("x=$(cat <<EOF\n)\nEOF\ndocker rm h)\n", ["docker rm h"], BOTH)
        self.assertScanMatchesSh("x=$(cat <<'EOF'\n(\nEOF\ndocker rm g)\n", ["docker rm g"], BOTH)
        self.assertScanMatchesSh("x=$( (docker rm s) ; f() { docker rm f; }; f )\n", ["docker rm s", "docker rm f"], BOTH)
        self.assertScanMatchesSh("echo $(( 1 + $(docker rm a; echo 1) ))\n", ["docker rm a"], BOTH)

    def test_case_syntax_outside_the_grammar_is_refused(self):
        # bash falls through with ;& and runs both branches; dash refuses it.
        self.assertRefused("x=$(case a in a) docker rm x;& b) docker rm y;; esac)\n", ["docker rm x", "docker rm y"],
                           shells=("bash",))
        for source in ("x=$(case a in a) docker rm x;;& esac)\n", "x=$(case a in @(a|b)) docker rm x;; esac)\n",
                       "case a b in a) ;; esac\n", "case a in a b) ;; esac\n", "case a in ) ;; esac\n",
                       "case a in a) echo;;\n", "esac\n", ";;\n", "echo a )\n", "x=$(echo a))\n", "(echo a\n"):
            self.assertRefused(source)


class WrapperTests(ShellScanTestCase):
    """Wrappers are skipped with the exact arity of their options."""

    def test_wrapper_options_keep_their_values(self):
        self.assertScanMatchesSh("timeout --signal KILL 5 sh -c 'docker run -d hidden'\n", ["docker run -d hidden"])
        self.assertScanMatchesSh("timeout -s KILL 5 sh -c 'docker rm s'; timeout -sKILL -k 1 5 sh -c 'docker rm k'\n"
                                 "timeout --sig=KILL --kill-after 1 --foreground 5 sh -c 'docker rm l'\n",
                                 ["docker rm s", "docker rm k", "docker rm l"], BOTH)
        self.assertScanMatchesSh("env -u HOME X=1 sh -c 'docker rm e'\n"
                                 "env -i PATH=\"$PATH\" CALLS=\"$CALLS\" sh -c 'docker rm i'\n"
                                 "env - PATH=\"$PATH\" CALLS=\"$CALLS\" sh -c 'docker rm dash'\n",
                                 ["docker rm e", "docker rm i", "docker rm dash"], BOTH)
        self.assertScanMatchesSh("nice -n 5 sh -c 'docker rm n'; nice -5 sh -c 'docker rm o'\n"
                                 "nice --adjustment=5 sh -c 'docker rm a'\nnohup sh -c 'docker rm h' >/dev/null 2>&1\n"
                                 "setsid -w sh -c 'docker rm w'; stdbuf -o L sh -c 'docker rm b'\n"
                                 "stdbuf -oL -e0 sh -c 'docker rm c'\n",
                                 ["docker rm n", "docker rm o", "docker rm a", "docker rm h", "docker rm w",
                                  "docker rm b", "docker rm c"], BOTH)
        self.assertScanMatchesSh("command sh -c 'docker rm c'; command -p sh -c 'docker rm p'\n"
                                 "echo x | xargs -r -n 1 sh -c 'docker rm x' sh\n"
                                 "X=1 timeout 5 env Y=2 nice -n 1 sh -c 'docker rm chain'\n",
                                 ["docker rm c", "docker rm p", "docker rm x", "docker rm chain"], BOTH)
        self.assertScanMatchesSh("exec -a name sh -c 'docker rm exec'\n", ["docker rm exec"], ("bash",))
        self.assertScanMatchesSh("time -p sh -c 'docker rm t' 2>/dev/null\nbuiltin eval 'docker rm builtin'\n",
                                 ["docker rm t", "docker rm builtin"], ("bash",))

    def test_command_queries_do_not_execute_their_operands(self):
        for source in ("command -v sh\n", "command -V sh -c 'docker run -d hidden'\n",
                       "command -pv docker\n", "command -p -V docker\n",
                       "command command -v sh\n"):
            self.assertScanMatchesSh(source, [], BOTH)
        self.assertScanMatchesSh("builtin command -V sh\n", [], ("bash",))
        # Without the query flags, command still executes the selected program.
        self.assertScanMatchesSh("command -p sh -c 'docker rm executed'\n", ["docker rm executed"], BOTH)

    def test_the_prefix_of_a_simple_command(self):
        command = simple_command(["if", "!", "X=1", "timeout", "-s", "KILL", "5", "env", "-u", "A", "B=2",
                                  "su-exec", "$UID:$GID", "sh", "-c", "x"])
        self.assertEqual((command.start, command.name, command.reserved, command.wrappers),
                         (13, "sh", ("if", "!"), ("timeout", "env", "su-exec")))
        self.assertEqual(command.arguments, ["-c", "x"])
        for words, start in ((["exec"], 1), (["timeout", "$t", "cmd"], 2), (["exec", "$@"], 1),
                             (["xargs", "-I{}", "-0", "cmd"], 3), (["sudo", "-u", "me", "A=1", "cmd"], 4),
                             (["command", "-v", "timeout"], 0), (["nice", "--", "-cmd"], 2)):
            with self.subTest(words=words):
                self.assertEqual(simple_command(words).start, start)

    def test_unknown_wrapper_options_and_hidden_command_lines_are_refused(self):
        # env -S splits its value into the command line it runs.
        self.assertRefused("env -S 'docker rm split'\nenv --split-string='docker rm s2'\n",
                           ["docker rm split", "docker rm s2"], BOTH)
        for source in ("env --sp='docker rm x'\n", "timeout --bogus 5 sh -c 'docker rm x'\n",
                       "timeout -z 5 sh -c 'docker rm x'\n", "xargs --max 1 sh -c 'docker rm x'\n",
                       "timeout --foreground=1 5 sh -c 'docker rm x'\n", "nice -q sh -c 'docker rm x'\n"):
            self.assertRefused(source)


class ShellCodeTests(ShellScanTestCase):
    """The code a shell interpreter runs is scanned, or refused when it cannot be seen."""

    def test_command_strings_after_any_shell_options(self):
        self.assertScanMatchesSh("sh -o errexit -c 'docker rm o'; sh -c -e 'docker rm ce'; sh -euc 'docker rm euc'\n",
                                 ["docker rm o", "docker rm ce", "docker rm euc"], BOTH)
        self.assertScanMatchesSh("bash --norc --noprofile -c 'docker rm norc'; bash -O extglob -c 'docker rm O'\n",
                                 ["docker rm norc", "docker rm O"], BOTH)

    def test_a_here_document_on_the_stdin_of_a_shell_is_its_code(self):
        self.assertScanMatchesSh("sh <<'EOF'\ndocker run -d hidden\nEOF\n", ["docker run -d hidden"], BOTH)
        self.assertScanMatchesSh("sh -s a b <<'EOF'\ndocker rm s\nEOF\nsh -- <<'EOF' | cat\ndocker rm piped\nEOF\n",
                                 ["docker rm s", "docker rm piped"], BOTH)
        self.assertScanMatchesSh("bash 0<<-'EOF'\n\tdocker rm t\n\tEOF\n", ["docker rm t"], BOTH)
        # An unquoted body without expansions: the outer shell only removes escapes.
        self.assertScanMatchesSh("sh <<EOF\ndocker rm plain a\\\\b\nEOF\n", ["docker rm plain ab"], BOTH)
        # A here-document for a -c script is data for that script.
        self.assertScanMatchesSh("sh -c cat <<EOF\n$(docker rm sub)\nEOF\n", ["docker rm sub"], BOTH)

    def test_continued_io_numbers_keep_here_documents_on_stdin(self):
        for spelling in ("0\\\n", "0\\\n\\\n"):
            source = f"sh {spelling}<<'EOF'\ndocker run -d hidden\nEOF\n"
            self.assertScanMatchesSh(source, ["docker run -d hidden"], BOTH)

    def test_quoted_or_escaped_digits_are_script_arguments_not_io_numbers(self):
        # Script 0 does nothing; its heredoc is input data, not another script.
        for spelling in ("'0'", '"0"', r"\0", "0''", '"0\\\n"'):
            source = f"printf ':\\n' > 0\nsh {spelling}<<'EOF'\ndocker run -d hidden\nEOF\n"
            self.assertScanMatchesSh(source, [], BOTH)

    def test_eval_code_is_scanned_or_refused_like_a_shell_string(self):
        # Literal arguments are the code eval runs; an expansion makes the code
        # a value the scanner cannot see, as for sh -c.
        self.assertScanMatchesSh("eval 'docker rm e1'; eval \"docker rm e2\"; eval docker rm e3\n"
                                 "command eval 'docker rm e4'\n",
                                 ["docker rm e1", "docker rm e2", "docker rm e3", "docker rm e4"], BOTH)
        self.assertRefused("x='docker rm v'; eval \"$x\"\n", ["docker rm v"], BOTH)
        self.assertRefused("x=v; eval \"docker rm $x\"\n", ["docker rm v"], BOTH)
        self.assertRefused("eval \"$(echo docker rm sub)\"\n", ["docker rm sub"], BOTH)
        self.assertRefused("eval `echo docker rm bq`\n", ["docker rm bq"], BOTH)
        self.assertRefused("set -- 'docker rm arg'; eval \"$1\"\n", ["docker rm arg"], BOTH)

    def test_pathname_expansion_cannot_supply_eval_or_shell_code(self):
        for reader in ("eval", "sh -c"):
            for pattern in ("d*", r"d?cker\ run\ -d\ hidden", r"[d]ocker\ run\ -d\ hidden"):
                source = f"touch 'docker run -d hidden'\n{reader} {pattern}\n"
                self.assertRefused(source, ["docker run -d hidden"], BOTH)

    def test_quoted_or_escaped_patterns_remain_literal_outer_code(self):
        for reader in ("eval", "sh -c"):
            for script, expected in (("'docker rm unmatched-*'", "docker rm unmatched-*"),
                                     ('"docker rm unmatched-?"', "docker rm unmatched-?"),
                                     (r"docker\ rm\ unmatched-\[x\]", "docker rm unmatched-[x]"),
                                     (r"docker\ rm\ unmatched-\*", "docker rm unmatched-*")):
                self.assertScanMatchesSh(f"{reader} {script}\n", [expected], BOTH)

    def test_a_script_file_operand_is_not_followed(self):
        for source in ('sh "$file" a\n', 'sh -n "$file"\n', 'exec su-exec torrent /bin/sh -eu "$0" "$x"\n'):
            with self.subTest(source=source):
                self.assertEqual([words for words in commands(source) if words[:1] == ["docker"]], [])

    def test_code_the_scanner_cannot_see_is_refused(self):
        self.assertRefused("echo 'docker rm piped' | sh\n", ["docker rm piped"], BOTH)
        self.assertRefused("printf 'docker rm file\\n' > script; sh < script\n", ["docker rm file"], BOTH)
        self.assertRefused("x='docker rm v'; sh -c \"$x\"\n", ["docker rm v"], BOTH)
        self.assertRefused("x=v; sh -c \"docker rm $x\"\n", ["docker rm v"], BOTH)
        self.assertRefused("sh -c \"$(echo docker rm sub)\"\n", ["docker rm sub"], BOTH)
        self.assertRefused("x='docker rm h'; sh <<EOF\n$x\nEOF\n", ["docker rm h"], BOTH)
        self.assertRefused("sh -s <<EOF\n`echo docker rm bq`\nEOF\n", ["docker rm bq"], BOTH)
        for source in ("sh\n", "sh -s a\n", "sh 3<<'EOF'\ndocker rm fd3\nEOF\n", "bash --bogus -c 'docker rm x'\n",
                       "sh <<<'docker rm x'\n"):
            self.assertRefused(source)


if __name__ == "__main__":
    unittest.main()
