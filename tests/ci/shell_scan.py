"""A small POSIX shell lexer for the CI contracts.

It yields the simple commands sh would run from a script, so a contract can
inspect them. It is neither a full parser nor a security sandbox. It follows
the grammar below and raises ShellSyntaxError on source outside it, or on
source that dash and bash read differently, so a contract that relies on it
fails closed.

Supported grammar:

* Words: single and double quotes, backslash escapes, line continuations
  (also inside an operator), comments at the start of a token and an IO
  number before a redirection. bash's $'...' and $"..." are refused.
* Expansions: $name and the special parameters, ${...}, $(...) and `...`.
  A $(...) is lexed with this grammar itself, so it ends at the ')' the
  shell ends it at: here-document bodies are skipped, the parentheses of
  subshells and function definitions pair up, and the ')' after a case
  pattern belongs to the case statement. Arithmetic $((...)) is lexed like a
  command substitution.
* Case statements are followed exactly: the subject, 'in', patterns with an
  optional '(', '|', ')', ';;' and 'esac'. Their patterns are not commands.
  bash's ';&', ';;&' and extended patterns are refused, as is an unpaired
  ')'. Other compound commands are lists of words.
* Here-documents (<< and <<-): the body is data when any part of the
  delimiter is quoted. Otherwise its substitutions run with the command that
  reads it. Delimiters with $ or `, a delimiter line formed by a line
  continuation, an expansion open across the delimiter and a here-document
  left without a body inside $(...) are refused.
* Simple commands (simple_command): the reserved words in PREFIXES, then
  assignments and the WRAPPERS with their options and operands are skipped
  to find the command name. Wrapper options have their exact arity; an
  option a wrapper's entry does not list is refused. command -v/-V query
  their operands and are not unwrapped as execution. A word with a parameter
  expansion ends a wrapper's options and counts as one word: field
  splitting is not modelled.
* Nested code: the substitutions of every word and here-document; the
  arguments of eval, joined; for a SHELLS interpreter, its -c command
  string, or the here-document on its stdin when it reads commands from
  stdin. Code that cannot be seen is refused: eval arguments, a -c string or
  a here-document body that the outer shell expands, and stdin that is not
  a here-document (a pipe, a file or the inherited stdin). A script file
  operand is not followed. Unquoted pathname-pattern characters (*, ? and [)
  in eval or -c arguments are refused; quoted or escaped characters are literal.

Not modelled: aliases, functions named like commands, and commands run by
other programs (find -exec, su -c, ssh, docker exec and the like).
"""

import re
from typing import NamedTuple, Optional


class ShellSyntaxError(ValueError):
    pass


OPERATORS = ("<<-", "&&", "||", ";;", "<<", ">>", ">&", "<&", "<>", ">|", ";", "&", "|", "(", ")", "<", ">")
REDIRECTIONS = {">>", ">&", "<&", "<>", ">|", "<", ">"}
STDIN_REDIRECTIONS = {"<", "<&", "<>"}
WORD_END = set(" \t\n;&|()<>")
SHELLS = {"sh", "bash", "dash", "ash", "ksh", "mksh", "zsh"}
# Shell options with a value, and long options without one.
SHELL_VALUED = {"--init-file", "--rcfile"}
SHELL_LONG = {"--debugger", "--dump-po-strings", "--dump-strings", "--help", "--login", "--noediting",
              "--noprofile", "--norc", "--posix", "--pretty-print", "--restricted", "--verbose", "--version"}
# Reserved words that open a compound command, with the word that closes it.
OPENERS = {"if": "fi", "case": "esac", "while": "done", "until": "done", "for": "done", "select": "done", "{": "}"}
# After these, the next word is again in command position.
PREFIXES = {"then", "do", "else", "elif", "if", "while", "until", "!", "{"}
COMMAND_OPERATORS = {";", "\n", "&", "&&", "||", "|", "(", ")", ";;"}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


class Wrapper(NamedTuple):
    """How a command that runs another command takes its own arguments."""
    flags: str = ""                  # short options without a value
    valued: str = ""                 # short options with a value, attached or the next word
    optional: str = ""               # short options whose value can only be attached
    long_flags: frozenset = frozenset()     # long options without a value
    long_valued: frozenset = frozenset()    # long options with a value, after '=' or the next word
    long_optional: frozenset = frozenset()  # long options whose value can only follow '='
    operands: int = 0                # operands before the command, such as timeout's duration
    assignments: bool = False        # NAME=value words before the command
    numeric: bool = False            # nice's obsolete -N and --N
    hidden: frozenset = frozenset()  # options whose value is itself a command line
    query: str = ""                 # short flags that query operands instead of executing them


WRAPPERS = {
    "builtin": Wrapper(),
    "busybox": Wrapper(),
    "command": Wrapper(flags="pvV", query="vV"),
    "env": Wrapper(flags="0iv", valued="uCS",
                   long_flags=frozenset({"debug", "ignore-environment", "list-signal-handling", "null"}),
                   long_valued=frozenset({"chdir", "split-string", "unset"}),
                   long_optional=frozenset({"block-signal", "default-signal", "ignore-signal"}),
                   assignments=True, hidden=frozenset({"S", "split-string"})),
    "exec": Wrapper(flags="cl", valued="a"),
    "nice": Wrapper(valued="n", long_valued=frozenset({"adjustment"}), numeric=True),
    "nohup": Wrapper(),
    "setsid": Wrapper(flags="cfw", long_flags=frozenset({"ctty", "fork", "wait"})),
    "stdbuf": Wrapper(valued="eio", long_valued=frozenset({"error", "input", "output"})),
    "su-exec": Wrapper(operands=1),
    "sudo": Wrapper(flags="AbEeHiKklnPSsVv", valued="CDghpRrTtUu",
                    long_flags=frozenset({"askpass", "background", "edit", "list", "login", "non-interactive",
                                          "preserve-groups", "remove-timestamp", "reset-timestamp", "set-home",
                                          "shell", "stdin", "validate", "version"}),
                    long_valued=frozenset({"chdir", "close-from", "command-timeout", "group", "host", "other-user",
                                           "prompt", "role", "type", "user"}),
                    long_optional=frozenset({"preserve-env"}), assignments=True),
    "time": Wrapper(flags="apqv", valued="fo", long_flags=frozenset({"append", "portability", "quiet", "verbose"}),
                    long_valued=frozenset({"format", "output"})),
    "timeout": Wrapper(flags="fpv", valued="ks", long_flags=frozenset({"foreground", "preserve-status", "verbose"}),
                       long_valued=frozenset({"kill-after", "signal"}), operands=1),
    "xargs": Wrapper(flags="0oprtx", valued="adEILnPs", optional="eil",
                     long_flags=frozenset({"exit", "interactive", "no-run-if-empty", "null", "open-tty",
                                           "show-limits", "verbose"}),
                     long_valued=frozenset({"arg-file", "delimiter", "max-args", "max-chars", "max-procs",
                                            "process-slot-var"}),
                     long_optional=frozenset({"eof", "max-lines", "replace"})),
}


class HereDocument:
    """A here-document. The lexer fills in its body when it reaches it."""

    def __init__(self, delimiter, strip_tabs, expands):
        self.delimiter, self.strip_tabs, self.expands = delimiter, strip_tabs, expands
        self.body, self.body_expands = None, False

    def script(self, reader):
        """The text a shell gets from this here-document on its stdin."""
        if not self.expands:
            return self.body
        if self.body_expands:
            raise ShellSyntaxError(f"{reader} reads a here-document that the outer shell expands first")
        return re.sub(r"\\([$`\\])", r"\1", self.body)


class Token(NamedTuple):
    """A lexed word, case pattern, operator or here-document redirection."""
    kind: str                 # "word", "pattern", "op" or "heredoc"
    text: str                 # a word without its quotes, or the operator
    substitutions: list       # the source of the substitutions that run with it
    raw: str                  # its source text
    expands: bool = False     # parameter/command expansion or an unquoted pathname pattern
    fd: Optional[int] = None  # the IO number of a redirection
    heredoc: Optional[HereDocument] = None


class SimpleCommand(NamedTuple):
    """A simple command split into its prefix and the command it runs."""
    words: list
    start: int                # index of the command name, len(words) if there is none
    reserved: tuple           # the reserved words before it, such as ('if', '!')
    wrappers: tuple           # the wrappers it runs through, such as ('exec', 'su-exec')

    @property
    def name(self):
        return self.words[self.start] if self.start < len(self.words) else None

    @property
    def arguments(self):
        return self.words[self.start + 1:]


def _single_quoted_end(source, index):
    end = source.find("'", index)
    if end < 0:
        raise ShellSyntaxError("unterminated single quote")
    return end + 1


def _backquoted(source, index):
    """(end, inner text) of a backquoted substitution whose body starts at index."""
    inner = []
    while index < len(source):
        char = source[index]
        if char == "\\" and index + 1 < len(source):
            following = source[index + 1]
            inner.append(following if following in "`\\$" else char + following)
            index += 2
            continue
        if char == "`":
            return index + 1, "".join(inner)
        inner.append(char)
        index += 1
    raise ShellSyntaxError("unterminated backquote substitution")


def _expansion(source, index, in_double_quotes):
    """(end, substitutions, text) of the expansion at index, or None.

    text is what a word keeps of it: the source of $(...), ${...} and `...`,
    and $name for a parameter.
    """
    if source.startswith("`", index):
        end, inner = _backquoted(source, index + 1)
        return end, [inner], source[index:end]
    if not source.startswith("$", index):
        return None
    start = index + 1
    while source.startswith("\\\n", start):  # sh removes a line continuation first
        start += 2
    following = source[start:start + 1]
    if following == "(":
        end = _lex(source, start + 1, nested=True)[1]
        return end, [source[start + 1:end - 1]], source[index:end]
    if following == "{":
        end, inner = _parameter_end(source, start + 1, in_double_quotes)
        return end, inner, source[index:end]
    if following in ("'", '"') and not in_double_quotes:
        raise ShellSyntaxError("bash's $'...' and $\"...\" quoting is not supported")
    name = NAME.match(source, start)
    if name:
        return name.end(), [], "$" + name[0]
    if following and (following.isdigit() or following in "@*#?-$!"):
        return start + 1, [], "$" + following
    return None


def _parameter_end(source, index, in_double_quotes):
    """(end, substitutions) of a parameter expansion whose body starts at index."""
    substitutions = []
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "'" and not in_double_quotes:
            index = _single_quoted_end(source, index + 1)
            continue
        if char == '"':
            index, inner, _ = _double_quoted(source, index + 1)
            substitutions.extend(inner)
            continue
        if source.startswith(("$'", '$"'), index):
            # bash releases differ on these inside a double-quoted ${...}.
            raise ShellSyntaxError("bash's $'...' and $\"...\" quoting is not supported")
        expansion = _expansion(source, index, in_double_quotes)
        if expansion is not None:
            index, inner, _ = expansion
            substitutions.extend(inner)
            continue
        if char == "}":
            return index + 1, substitutions
        index += 1
    raise ShellSyntaxError("unterminated parameter expansion")


def _double_quoted(source, index, closing='"'):
    """(end, substitutions, expands) of a double-quoted string whose body starts at index.

    With closing None it reads to the end of source, as sh expands the body
    of a here-document whose delimiter is unquoted: its quotes are plain text.
    """
    substitutions, expands = [], False
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == closing:
            return index + 1, substitutions, expands
        expansion = _expansion(source, index, True)
        if expansion is not None:
            index, inner, _ = expansion
            substitutions.extend(inner)
            expands = True
            continue
        index += 1
    if closing is None:
        return index, substitutions, expands
    raise ShellSyntaxError("unterminated double quote")


def _unescape_double(text):
    return re.sub(r'\\([$`"\\\n])', lambda match: "" if match[1] == "\n" else match[1], text)


def _word(source, index):
    """(value, substitutions, end, expands) of the word that starts at index.

    The value has its quotes removed; substitutions and expansions keep their
    source text, since the contracts read literal words.
    """
    value, substitutions, expands = [], [], False
    while index < len(source) and source[index] not in WORD_END:
        char = source[index]
        if char == "\\":
            if source.startswith("\\\n", index):
                index += 2
                continue
            value.append(source[index + 1:index + 2])
            index += 2
            continue
        if char == "'":
            end = _single_quoted_end(source, index + 1)
            value.append(source[index + 1:end - 1])
            index = end
            continue
        if char == '"':
            end, inner, quoted_expands = _double_quoted(source, index + 1)
            substitutions.extend(inner)
            expands = expands or quoted_expands
            value.append(_unescape_double(source[index + 1:end - 1]))
            index = end
            continue
        expansion = _expansion(source, index, False)
        if expansion is not None:
            index, inner, text = expansion
            substitutions.extend(inner)
            expands = True
            value.append(text)
            continue
        if char in "*?[":
            expands = True  # Its value can depend on filenames in the outer shell.
        value.append(char)
        index += 1
    return "".join(value), substitutions, index, expands


def _operator(source, index):
    """(operator, end) of the longest operator at index, or None.

    sh removes line continuations first, so one may split an operator.
    """
    found, text = None, ""
    while index < len(source):
        if source.startswith("\\\n", index):
            index += 2
            continue
        text += source[index]
        index += 1
        if not any(candidate.startswith(text) for candidate in OPERATORS):
            break
        if text in OPERATORS:
            found = (text, index)
    return found


def _here_document(source, index, delimiter, strip_tabs, expands):
    """(end, body) of a here-document whose first line starts at index.

    In an expanded body a line ending in an unescaped backslash continues on
    the next line. dash and busybox never compare a continued line with the
    delimiter, bash compares the joined line: that case is refused.
    """
    body, joined = [], None
    while True:
        if index >= len(source):
            raise ShellSyntaxError(f"unterminated here-document {delimiter!r}")
        end = source.find("\n", index)
        end = len(source) if end < 0 else end
        line = source[index:end]
        index = end + 1
        if joined is None:
            line = line.lstrip("\t") if strip_tabs else line
            if line == delimiter:
                return index, "".join(body)
        else:
            line = joined + line
        if expands and (len(line) - len(line.rstrip("\\"))) % 2:
            joined = line[:-1]
            continue
        if joined is not None and line == delimiter:
            raise ShellSyntaxError(f"here-document delimiter {delimiter!r} joined by a line continuation")
        joined = None
        body.append(line + "\n")


def _here_document_operator(source, index, operator):
    """(HereDocument, end) for the delimiter after a << or <<- that ends at index."""
    while index < len(source) and source[index] in " \t":
        index += 1
    start = index
    delimiter, _, index, _ = _word(source, index)
    spelling = source[start:index].replace("\\\n", "")
    if not delimiter:
        raise ShellSyntaxError("here-document without a delimiter")
    if "$" in spelling or "`" in spelling:
        raise ShellSyntaxError(f"unsupported here-document delimiter {spelling!r}")
    # Any quoted part of the delimiter keeps the body literal.
    expands = not any(quote in spelling for quote in "'\"\\")
    return HereDocument(delimiter, operator == "<<-", expands), index


class _Grammar:
    """Pairs '(' with ')' and follows case statements.

    The stack holds '(' for a parenthesis and the state of each case
    statement: subject, in, patterns (a pattern list may start), pattern (a
    pattern must follow), separator ('|' or ')' must follow) and body.
    """

    def __init__(self):
        self.stack, self.at_command = [], True

    def word(self, spelling):
        """The kind of a word: 'pattern' for a case pattern, else 'word'."""
        state = self.stack[-1] if self.stack else None
        if state == "subject":
            self.stack[-1] = "in"
        elif state == "in":
            if spelling != "in":
                raise ShellSyntaxError(f"case statement without 'in' before {spelling!r}")
            self.stack[-1] = "patterns"
        elif state == "patterns" and spelling == "esac":
            self.stack.pop()
            self.at_command = False
        elif state in ("patterns", "pattern"):
            self.stack[-1] = "separator"
            return "pattern"
        elif state == "separator":
            raise ShellSyntaxError(f"unsupported case pattern near {spelling!r}")
        elif self.at_command and spelling == "case":
            self.stack.append("subject")
            self.at_command = False
        elif self.at_command and spelling == "esac":
            if state != "body":
                raise ShellSyntaxError("'esac' outside a case statement")
            self.stack.pop()
            self.at_command = False
        else:
            self.at_command = spelling in PREFIXES
        return "word"

    def operator(self, text):
        state = self.stack[-1] if self.stack else None
        if state in ("subject", "in", "patterns", "pattern", "separator"):
            if text == "\n" and state in ("in", "patterns"):
                return
            if text == "(" and state == "patterns" or text == "|" and state == "separator":
                self.stack[-1] = "pattern"
                return
            if text == ")" and state == "separator":
                self.stack[-1], self.at_command = "body", True
                return
            raise ShellSyntaxError(f"unsupported case statement syntax at {text!r}")
        if text == ";;":
            if state != "body":
                raise ShellSyntaxError("';;' outside a case statement")
            self.stack[-1] = "patterns"
            return
        if text == "(":
            self.stack.append("(")
        elif text == ")":
            if state != "(":
                raise ShellSyntaxError("unpaired ')'")
            self.stack.pop()
        self.at_command = text in COMMAND_OPERATORS

    def end(self):
        if self.stack:
            raise ShellSyntaxError("unclosed parenthesis or case statement")


def _lex(source, index=0, nested=False):
    """(tokens, end) of source from index.

    When nested, source[index:] is the body of a $( and the result ends just
    past the ')' that closes it.
    """
    result, heredocs, grammar, fd = [], [], _Grammar(), None
    while index < len(source):
        char = source[index]
        if source.startswith("\\\n", index):
            index += 2
            continue
        if char in " \t":
            index += 1
            continue
        if char == "#":
            end = source.find("\n", index)
            index = len(source) if end < 0 else end
            continue
        if char == "\n":
            index += 1
            # Here-document bodies follow the line. Only the body of an
            # unquoted delimiter is expanded; its substitutions go with the
            # newline, so they run with the command that reads the body.
            substitutions = []
            for heredoc in heredocs:
                index, heredoc.body = _here_document(source, index, heredoc.delimiter, heredoc.strip_tabs,
                                                     heredoc.expands)
                if heredoc.expands:
                    _, inner, heredoc.body_expands = _double_quoted(heredoc.body, 0, None)
                    substitutions.extend(inner)
            heredocs = []
            grammar.operator("\n")
            result.append(Token("op", "\n", substitutions, "\n"))
            continue
        operator = _operator(source, index)
        if operator is not None:
            text, end = operator
            if text in ("<<", "<<-"):
                heredoc, end = _here_document_operator(source, end, text)
                heredocs.append(heredoc)
                result.append(Token("heredoc", text, [], source[index:end], fd=fd, heredoc=heredoc))
            elif nested and text == ")" and not grammar.stack:
                if heredocs:
                    # bash reads the body after the substitution, dash does not.
                    raise ShellSyntaxError("here-document without a body inside $(...)")
                return result, end
            else:
                grammar.operator(text)
                result.append(Token("op", text, [], text, fd=fd))
            index, fd = end, None
            continue
        start = index
        value, substitutions, index, expands = _word(source, index)
        raw = source[start:index]
        spelling = raw.replace("\\\n", "")
        if spelling.isdigit() and source[index:index + 1] in ("<", ">"):
            fd = int(spelling)  # an IO number such as the 2 of 2>&1, not an argument
            continue
        kind = grammar.word(spelling)
        result.append(Token(kind, value, substitutions, raw, expands))
    if nested:
        raise ShellSyntaxError("unterminated command substitution")
    if heredocs:
        raise ShellSyntaxError("here-document without a body")
    grammar.end()
    return result, index


def tokens(source):
    """The Token list of source.

    A newline carries the substitutions of the here-document bodies that
    follow it.
    """
    return _lex(source)[0]


def _long_option(name, spec, word):
    """(option, has_value) of a long option; getopt accepts an unambiguous prefix."""
    option, has_value, _ = word[2:].partition("=")
    known = spec.long_flags | spec.long_valued | spec.long_optional
    matches = [option] if option in known else [candidate for candidate in known if candidate.startswith(option)]
    if len(matches) != 1:
        raise ShellSyntaxError(f"unknown option {word!r} of {name}")
    return matches[0], bool(has_value)


def _wrapper_end(name, spec, words, index):
    """Index of the command NAME runs, or None for an operand query."""
    query = False
    while index < len(words):
        word = words[index]
        if word == "--":
            index += 1
            break
        if spec.numeric and re.fullmatch(r"--?[0-9]+", word):
            index += 1
            continue
        if word.startswith("--"):
            option, has_value = _long_option(name, spec, word)
            if option in spec.hidden:
                raise ShellSyntaxError(f"{name} --{option} runs a command line the scanner cannot see")
            if option in spec.long_valued:
                index += 1 if has_value else 2
            elif has_value and option not in spec.long_optional:
                raise ShellSyntaxError(f"option --{option} of {name} takes no value")
            else:
                index += 1
            continue
        if len(word) > 1 and word.startswith("-"):
            index += 1
            for position, letter in enumerate(word[1:], 2):
                query = query or letter in spec.query
                if letter in spec.hidden:
                    raise ShellSyntaxError(f"{name} -{letter} runs a command line the scanner cannot see")
                if letter in spec.valued:
                    index += position == len(word)  # no attached value: the next word is the value
                    break
                if letter in spec.optional:
                    break
                if letter not in spec.flags:
                    raise ShellSyntaxError(f"unknown option -{letter} of {name}")
            continue
        if name == "env" and word == "-":  # the same as -i
            index += 1
            continue
        break
    while spec.assignments and index < len(words) and ASSIGNMENT.match(words[index]):
        index += 1
    return None if query else index + spec.operands


def simple_command(words):
    """The SimpleCommand of a word list from commands().

    Leading reserved words, then assignments and wrappers with their options
    and operands are skipped. An option of a wrapper whose arity is unknown
    raises ShellSyntaxError.
    """
    index, reserved, wrappers = 0, [], []
    while index < len(words) and words[index] in PREFIXES:
        reserved.append(words[index])
        index += 1
    while index < len(words):
        word = words[index]
        if ASSIGNMENT.match(word):
            index += 1
            continue
        if word not in WRAPPERS:
            break
        end = _wrapper_end(word, WRAPPERS[word], words, index + 1)
        if end is None:
            break
        wrappers.append(word)
        index = end
    return SimpleCommand(list(words), min(index, len(words)), tuple(reserved), tuple(wrappers))


def command_start(words):
    """Index of the command word: reserved words, assignments and wrappers are skipped."""
    return simple_command(words).start


def commands(source):
    """Every simple command of source as a list of word values.

    Commands inside command substitutions, eval arguments and the code of a
    shell (its -c string or the here-document on its stdin) are included, so
    a command cannot hide in them.
    """
    yield from _commands(tokens(source))


def _commands(token_list):
    words, stdin, skip_target = [], None, False
    for token in token_list:
        for inner in token.substitutions:
            yield from commands(inner)
        if token.kind == "heredoc":
            if token.fd in (None, 0):
                stdin = token
            continue
        if token.kind == "op" and token.text in REDIRECTIONS:
            skip_target = True
            if token.fd in (None, 0) and token.text in STDIN_REDIRECTIONS:
                stdin = token
            continue
        if token.kind in ("op", "pattern"):
            if words:
                yield from _simple(words, stdin)
            words, stdin = [], None
            continue
        if skip_target:
            skip_target = False
            continue
        words.append(token)
    if words:
        yield from _simple(words, stdin)


def _simple(words, stdin):
    values = [token.text for token in words]
    yield values
    command = simple_command(values)
    if command.name is None:
        return
    arguments = words[command.start + 1:]
    if command.name == "eval":
        expanded = [token.raw for token in arguments if token.expands]
        if expanded:
            raise ShellSyntaxError(f"eval runs {' '.join(expanded)}, which the outer shell expands first")
        yield from commands(" ".join(token.text for token in arguments))
    elif command.name.rsplit("/", 1)[-1] in SHELLS:
        yield from _shell_code(command.name, arguments, stdin)


def _shell_code(name, arguments, stdin):
    """The commands of a shell run with ARGUMENTS (tokens) and STDIN (a token or None)."""
    index, command_string, reads_stdin = 0, False, False
    while index < len(arguments):
        word = arguments[index].text
        if word in ("-", "--"):
            index += 1
            break
        if word.startswith("--"):
            if word not in SHELL_VALUED | SHELL_LONG:
                raise ShellSyntaxError(f"unknown option {word!r} of {name}")
            index += 2 if word in SHELL_VALUED else 1
            continue
        if len(word) > 1 and word[0] in "-+" and word[1:].isalpha():
            index += 1
            for letter in word[1:]:
                if letter in "oO":
                    index += 1  # -o and -O name an option in the next word
                elif word[0] == "-":
                    command_string = command_string or letter == "c"
                    reads_stdin = reads_stdin or letter == "s"
            continue
        break
    operands = arguments[index:]
    if command_string:
        if not operands:
            return
        script = operands[0]
        if script.expands:
            raise ShellSyntaxError(f"{name} -c runs {script.raw}, which the outer shell expands first")
        yield from commands(script.text)
    elif reads_stdin or not operands:
        if stdin is None or stdin.kind != "heredoc":
            raise ShellSyntaxError(f"{name} reads commands from a stdin the scanner cannot see")
        yield from commands(stdin.heredoc.script(name))


def function_commands(source, name):
    """The commands in the body of each definition of the function NAME."""
    token_list = tokens(source)
    for index in range(len(token_list) - 2):
        if [token[:2] for token in token_list[index:index + 3]] != [("word", name), ("op", "("), ("op", ")")]:
            continue
        start = index + 3
        while start < len(token_list) and token_list[start][:2] == ("op", "\n"):
            start += 1
        opener = token_list[start][:2] if start < len(token_list) else None
        if opener == ("word", "{"):
            closer = ("word", "}")
        elif opener == ("op", "("):
            closer = ("op", ")")
        else:
            raise ShellSyntaxError(f"unsupported body of function {name}")
        depth, at_command, end = 0, True, None
        for position in range(start, len(token_list)):
            token = token_list[position][:2]
            if token in (opener, closer) and (at_command or token[0] == "op"):
                depth += 1 if token == opener else -1
                if depth == 0:
                    end = position
                    break
            at_command = token[0] == "op" or token[1] in ("{", "then", "do", "else", "elif", "if", "while", "until", "!")
        if end is None:
            raise ShellSyntaxError(f"unterminated body of function {name}")
        yield from _commands(token_list[start + 1:end])


def top_level_lists(source):
    """The and-or lists that SOURCE runs unconditionally at its top level.

    Each is a tuple of raw words and operators, such as ('cmd', '"$a"', '||',
    'true'), ended by a newline or ';'. Lists inside or touching a compound
    command (if, case, a loop, braces or a subshell) and lists run in the
    background are left out.
    """
    lists, current, stack, plain, at_command = [], [], [], True, True
    for token in tokens(source):
        if token.kind == "heredoc":
            continue
        kind, text, raw = token.kind, token.text, token.raw
        if at_command and ((kind == "word" and text in OPENERS) or (kind, text) == ("op", "(")):
            stack.append(OPENERS.get(text, ")") if kind == "word" else ")")
            plain = False
        elif stack and text == stack[-1] and ((kind == "word" and at_command) or (kind, text) == ("op", ")")):
            stack.pop()
            plain = False
        if kind == "op" and text in ("\n", ";", "&"):
            if current and plain and not stack and text != "&":
                lists.append(tuple(current))
            current, plain = [], True
        else:
            current.append(raw)
        at_command = (kind == "op" and text in COMMAND_OPERATORS) or (kind == "word" and text in PREFIXES)
    if current and plain and not stack:
        lists.append(tuple(current))
    if stack:
        raise ShellSyntaxError(f"unclosed compound command: {stack}")
    return lists
