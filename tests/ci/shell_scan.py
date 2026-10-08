"""A small POSIX shell lexer for the CI contracts.

It yields the simple commands sh would run from a script: quotes, backslash
escapes, line continuations, comments, operators and unquoted newlines are
handled; here-document bodies are skipped as data; command substitutions,
eval and "sh -c" scripts are analysed recursively. It is not a full parser.
Source it cannot follow (an unclosed quote or substitution) raises
ShellSyntaxError, so a contract that relies on it fails closed.
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


def _parameter_end(source, index, in_double_quotes):
    """Index just past the '}' that closes a parameter expansion starting at index."""
    depth = 1
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "'" and not in_double_quotes:
            index = _single_quoted_end(source, index + 1)
            continue
        if char == '"':
            index, _ = _double_quoted(source, index + 1)
            continue
        if source.startswith("${", index):
            depth += 1
            index += 2
            continue
        if char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ShellSyntaxError("unterminated parameter expansion")


def _substitution_end(source, index):
    """Index just past the ')' that closes a '$(' whose body starts at index."""
    depth = 1
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == "'":
            index = _single_quoted_end(source, index + 1)
            continue
        if char == '"':
            index, _ = _double_quoted(source, index + 1)
            continue
        if char == "`":
            index, _ = _backquoted(source, index + 1)
            continue
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return index + 1
        index += 1
    raise ShellSyntaxError("unterminated command substitution")


def _double_quoted(source, index):
    """(end, substitutions) of a double-quoted string whose body starts at index."""
    substitutions = []
    while index < len(source):
        char = source[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            return index + 1, substitutions
        if source.startswith("$(", index):
            end = _substitution_end(source, index + 2)
            substitutions.append(source[index + 2:end - 1])
            index = end
            continue
        if source.startswith("${", index):
            index = _parameter_end(source, index + 2, True)
            continue
        if char == "`":
            index, inner = _backquoted(source, index + 1)
            substitutions.append(inner)
            continue
        index += 1
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
        if source.startswith("$(", index):
            end = _substitution_end(source, index + 2)
            substitutions.append(source[index + 2:end - 1])
            value.append(source[index:end])
            index = end
            continue
        if source.startswith("${", index):
            end = _parameter_end(source, index + 2, False)
            value.append(source[index:end])
            index = end
            continue
        if char == "`":
            end, inner = _backquoted(source, index + 1)
            substitutions.append(inner)
            value.append(source[index:end])
            index = end
            continue
        value.append(char)
        index += 1
    return "".join(value), substitutions, index


def tokens(source):
    """Words ("word", value, substitutions, raw) and operators ("op", text, [], text).

    A word's value has its quotes removed; raw is its source text.
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
            result.append(("op", "\n", [], "\n"))
            index += 1
            # Here-document bodies are data until their delimiter line.
            for delimiter, strip_tabs in heredocs:
                while True:
                    if index >= len(source):
                        raise ShellSyntaxError(f"unterminated here-document {delimiter!r}")
                    end = source.find("\n", index)
                    end = len(source) if end < 0 else end
                    line = source[index:end]
                    index = end + 1
                    if (line.lstrip("\t") if strip_tabs else line) == delimiter:
                        break
            heredocs = []
            continue
        operator = next((candidate for candidate in OPERATORS if source.startswith(candidate, index)), None)
        if operator is not None:
            index += len(operator)
            if operator in ("<<", "<<-"):
                while index < len(source) and source[index] in " \t":
                    index += 1
                delimiter, _, index = _word(source, index)
                if not delimiter:
                    raise ShellSyntaxError("here-document without a delimiter")
                heredocs.append((delimiter, operator == "<<-"))
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
