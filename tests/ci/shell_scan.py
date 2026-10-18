"""A small POSIX shell lexer for the CI contracts.

It yields the simple commands sh would run from a script: quotes, backslash
escapes, line continuations, comments, operators and unquoted newlines are
handled; command substitutions (also inside parameter expansions and the
bodies of here-documents whose delimiter is unquoted), eval and "sh -c"
scripts are analysed recursively. A here-document with a quoted delimiter is
data. It is not a full parser. Source it cannot follow (an unclosed quote or
substitution) or that dash and bash read differently raises ShellSyntaxError,
so a contract that relies on it fails closed.
"""

import re


class ShellSyntaxError(ValueError):
    pass


OPERATORS = ("<<-", "&&", "||", ";;", "<<", ">>", ">&", "<&", "<>", ">|", ";", "&", "|", "(", ")", "<", ">")
REDIRECTIONS = {">>", ">&", "<&", "<>", ">|", "<", ">"}
WORD_END = set(" \t\n;&|()<>")
SHELLS = {"sh", "bash", "dash", "ash"}
# Reserved words that open a compound command, with the word that closes it.
OPENERS = {"if": "fi", "case": "esac", "while": "done", "until": "done", "for": "done", "select": "done", "{": "}"}
# After these, the next word is again in command position.
PREFIXES = {"then", "do", "else", "elif", "if", "while", "until", "!", "{"}
COMMAND_OPERATORS = {";", "\n", "&", "&&", "||", "|", "(", ")", ";;"}
# Wrappers that run their remaining arguments as a command; options are skipped.
WRAPPERS = {"timeout": 1, "env": 0, "nice": 0, "nohup": 0, "exec": 0, "command": 0, "time": 0,
            "setsid": 0, "stdbuf": 0, "sudo": 0, "xargs": 0, "!": 0}
ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=")


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
    """(end, substitutions) of the $(...), ${...} or `...` at index, or None."""
    if source.startswith("`", index):
        end, inner = _backquoted(source, index + 1)
        return end, [inner]
    if not source.startswith("$", index):
        return None
    start = index + 1
    while source.startswith("\\\n", start):  # sh removes a line continuation first
        start += 2
    if source.startswith("(", start):
        end = _substitution_end(source, start + 1)
        return end, [source[start + 1:end - 1]]
    if source.startswith("{", start):
        return _parameter_end(source, start + 1, in_double_quotes)
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
            index, inner = _double_quoted(source, index + 1)
            substitutions.extend(inner)
            continue
        expansion = _expansion(source, index, in_double_quotes)
        if expansion is not None:
            index, inner = expansion
            substitutions.extend(inner)
            continue
        if char == "}":
            return index + 1, substitutions
        index += 1
    raise ShellSyntaxError("unterminated parameter expansion")


def _substitution_end(source, index):
    """Index just past the ')' that closes a '$(' whose body starts at index."""
    depth, word_start = 1, True
    while index < len(source):
        char = source[index]
        if source.startswith("\\\n", index):
            index += 2
            continue
        if char == "#" and word_start:
            # A comment hides quotes and parentheses up to the end of its line.
            index = source.find("\n", index)
            if index < 0:
                break
            continue
        word_start = char in WORD_END
        if char == "\\":
            index += 2
            continue
        if char == "'":
            index = _single_quoted_end(source, index + 1)
            continue
        if char == '"':
            index, _ = _double_quoted(source, index + 1)
            continue
        expansion = _expansion(source, index, False)
        if expansion is not None:
            index = expansion[0]
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ShellSyntaxError("unterminated command substitution")


def _double_quoted(source, index, closing='"'):
    """(end, substitutions) of a double-quoted string whose body starts at index.

    With closing None it reads to the end of source, as sh expands the body
    of a here-document whose delimiter is unquoted: its quotes are plain text.
    """
    substitutions = []
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == closing:
            return index + 1, substitutions
        expansion = _expansion(source, index, True)
        if expansion is not None:
            index, inner = expansion
            substitutions.extend(inner)
            continue
        index += 1
    if closing is None:
        return index, substitutions
    raise ShellSyntaxError("unterminated double quote")


def _unescape_double(text):
    return re.sub(r'\\([$`"\\\n])', lambda match: "" if match[1] == "\n" else match[1], text)


def _word(source, index):
    """(value, substitutions, end) of the word that starts at index.

    The value has its quotes removed; substitutions and expansions keep their
    source text, since the contracts read literal words.
    """
    value, substitutions = [], []
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
            end, inner = _double_quoted(source, index + 1)
            substitutions.extend(inner)
            value.append(_unescape_double(source[index + 1:end - 1]))
            index = end
            continue
        expansion = _expansion(source, index, False)
        if expansion is not None:
            end, inner = expansion
            substitutions.extend(inner)
            value.append(source[index:end])
            index = end
            continue
        value.append(char)
        index += 1
    return "".join(value), substitutions, index


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


def tokens(source):
    """Words ("word", value, substitutions, raw) and operators ("op", text, substitutions, text).

    A word's value has its quotes removed; raw is its source text. A newline
    carries the substitutions of the here-document bodies that follow it.
    """
    result = []
    heredocs = []
    index = 0
    while index < len(source):
        char = source[index]
        if source.startswith("\\\n", index):
            index += 2
            continue
        if char in " \t":
            index += 1
            continue
        if char == "#":
            while index < len(source) and source[index] != "\n":
                index += 1
            continue
        if char == "\n":
            index += 1
            # Here-document bodies follow the line. Only the body of an
            # unquoted delimiter is expanded; its substitutions go with the
            # newline, so they run with the command that reads the body.
            substitutions = []
            for delimiter, strip_tabs, expands in heredocs:
                index, body = _here_document(source, index, delimiter, strip_tabs, expands)
                if expands:
                    substitutions.extend(_double_quoted(body, 0, None)[1])
            result.append(("op", "\n", substitutions, "\n"))
            heredocs = []
            continue
        operator = _operator(source, index)
        if operator is not None:
            operator, index = operator
            if operator in ("<<", "<<-"):
                while index < len(source) and source[index] in " \t":
                    index += 1
                start = index
                delimiter, _, index = _word(source, index)
                spelling = source[start:index].replace("\\\n", "")
                if not delimiter:
                    raise ShellSyntaxError("here-document without a delimiter")
                if "$" in spelling or "`" in spelling:
                    raise ShellSyntaxError(f"unsupported here-document delimiter {spelling!r}")
                # Any quoted part of the delimiter keeps the body literal.
                heredocs.append((delimiter, operator == "<<-", not any(quote in spelling for quote in "'\"\\")))
                continue
            result.append(("op", operator, [], operator))
            continue
        start = index
        value, substitutions, index = _word(source, index)
        if source[start:index].isdigit() and source[index:index + 1] in ("<", ">"):
            continue  # an IO number such as the 2 of 2>&1, not an argument
        result.append(("word", value, substitutions, source[start:index]))
    if heredocs:
        raise ShellSyntaxError("here-document without a body")
    return result


def command_start(words):
    """Index of the command word: leading assignments and wrappers are skipped."""
    index = 0
    while index < len(words):
        word = words[index]
        if ASSIGNMENT.match(word):
            index += 1
            continue
        if word in WRAPPERS:
            positional = WRAPPERS[word]
            index += 1
            while index < len(words) and (words[index].startswith("-") or ASSIGNMENT.match(words[index])):
                index += 1
            index += positional
            continue
        return index
    return index


def commands(source):
    """Every simple command of source as a list of word values.

    Commands inside command substitutions, eval arguments and the script of
    "sh -c" are included, so a command cannot hide in them.
    """
    yield from _commands(tokens(source))


def _commands(token_list):
    current = []
    skip_target = False
    for kind, text, substitutions, _ in token_list:
        for inner in substitutions:
            yield from commands(inner)
        if kind == "op":
            if text in REDIRECTIONS:
                skip_target = True
                continue
            if current:
                yield current
                yield from _nested(current)
            current = []
            continue
        if skip_target:
            skip_target = False
            continue
        current.append(text)
    if current:
        yield current
        yield from _nested(current)


def _nested(words):
    start = command_start(words)
    if start >= len(words):
        return
    if words[start] == "eval":
        yield from commands(" ".join(words[start + 1:]))
    elif words[start].rsplit("/", 1)[-1] in SHELLS:
        for index in range(start + 1, len(words) - 1):
            if re.fullmatch(r"-[A-Za-z]*c[A-Za-z]*", words[index]):
                yield from commands(words[index + 1])
                break


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
    for kind, text, _, raw in tokens(source):
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
