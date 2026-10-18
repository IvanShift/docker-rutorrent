"""A strict parser for the YAML subset of this repository's GitHub workflows.

The CI contracts must inspect what GitHub executes, not substrings of the file,
and the runner's system Python has no YAML library. This parser accepts block
mappings and sequences, single-line flow collections, quoted and plain
scalars, comments and literal or folded block scalars. Scalars stay strings.
A quote opens a quoted scalar only where a scalar starts; inside a plain
scalar it is a character. Double-quoted scalars and keys may use only the
escapes in ESCAPES. Anything else (other escapes, anchors, aliases, tags,
multi-line plain, quoted or flow values, ': ' inside a plain value, tabs,
duplicate keys after decoding) is refused, so an unusual edit fails the
contracts closed instead of being read differently from GitHub.
"""

import re


class WorkflowYamlError(ValueError):
    pass


_BLOCK_SCALAR = re.compile(r"[|>][+-]?\Z")
# The double-quoted escapes this subset decodes; YAML has more (\u, \x, \N,
# \_, \0, \e ...), which are refused rather than kept as literal text.
ESCAPES = {'"': '"', "\\": "\\", "/": "/", "n": "\n", "t": "\t"}


def _quoted_end(text, index):
    """Index just past the quoted scalar whose opening quote is at index."""
    quote = text[index]
    index += 1
    while index < len(text):
        char = text[index]
        if char == quote:
            if quote == "'" and text.startswith("''", index):
                index += 2
                continue
            return index + 1
        index += 2 if quote == '"' and char == "\\" else 1
    raise WorkflowYamlError(f"Unterminated quoted scalar: {text!r}")


def _scan(text, flow=False):
    """Yield (index, char, depth) for each character outside quoted scalars.

    A quote opens a quoted scalar only where a scalar starts: at the start of
    text, after a '- ' entry or the ': ' of a key, and after '[', '{' or ','
    in a flow collection (flow: text is the inside of one).
    """
    index, start, depth = 0, True, 1 if flow else 0
    while index < len(text):
        char = text[index]
        if start and char in "'\"":
            index = _quoted_end(text, index)
            start = False
            continue
        yield index, char, depth
        separator = text[index + 1:index + 2] in ("", " ")
        if char in " \t":
            pass
        elif char in "[{" and (start or depth):
            depth += 1
        elif char in "]}" and depth:
            depth -= 1
            start = False
        else:
            start = (char == "," and depth > 0) or (char == ":" and separator) or (char == "-" and separator and start)
        index += 1


def _strip_comment(text):
    """Drop a trailing comment: a '#' outside quoted scalars after whitespace."""
    for index, char, _ in _scan(text):
        if char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
    return text.rstrip()


def _split_flow(text):
    """Split the inside of a single-line flow collection at top-level commas."""
    parts, start = [], 0
    for index, char, depth in _scan(text, flow=True):
        if char == "," and depth == 1:
            parts.append(text[start:index])
            start = index + 1
    tail = text[start:]
    if tail.strip() or parts:
        parts.append(tail)
    parts = [part.strip() for part in parts]
    if "" in parts:
        raise WorkflowYamlError(f"Empty flow collection entry: {text!r}")
    return parts


def _key_split(text):
    """Return (key, rest) when text is a mapping entry, else None."""
    for index, char, depth in _scan(text):
        if char == ":" and depth == 0 and (index + 1 == len(text) or text[index + 1] == " "):
            key = text[:index].strip()
            if not key:
                return None
            if key[0] in "[{":
                raise WorkflowYamlError(f"Unsupported mapping key: {key!r}")
            return _scalar(key), text[index + 1:].strip()
    return None


def _unescape(body):
    """Decode the escapes of a double-quoted scalar's body."""
    decoded, index = [], 0
    while index < len(body):
        char = body[index]
        if char == "\\":
            escape = body[index + 1:index + 2]
            if escape not in ESCAPES:
                raise WorkflowYamlError(f"Unsupported escape \\{escape} in {body!r}")
            decoded.append(ESCAPES[escape])
            index += 2
            continue
        decoded.append(char)
        index += 1
    return "".join(decoded)


def _scalar(text):
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!|>%@`":
        raise WorkflowYamlError(f"Unsupported YAML construct: {text!r}")
    if text[0] in "'\"":
        if _quoted_end(text, 0) != len(text):
            raise WorkflowYamlError(f"Unsupported quoted scalar: {text!r}")
        body = text[1:-1]
        return body.replace("''", "'") if text[0] == "'" else _unescape(body)
    if text.startswith("["):
        if not text.endswith("]"):
            raise WorkflowYamlError(f"Multi-line flow sequences are not supported: {text!r}")
        return [_scalar(part) for part in _split_flow(text[1:-1])]
    if text.startswith("{"):
        if not text.endswith("}"):
            raise WorkflowYamlError(f"Multi-line flow mappings are not supported: {text!r}")
        mapping = {}
        for part in _split_flow(text[1:-1]):
            entry = _key_split(part)
            if entry is None:
                raise WorkflowYamlError(f"Unsupported flow mapping entry: {part!r}")
            key, value = entry
            if key in mapping:
                raise WorkflowYamlError(f"Duplicate key {key!r}")
            mapping[key] = _scalar(value)
        return mapping
    if ": " in text or text.endswith(":"):
        # YAML does not allow a mapping value inside a plain scalar.
        raise WorkflowYamlError(f"Unsupported ': ' in a plain scalar: {text!r}")
    if text in ("~", "null"):
        return None
    return text


class _Lines:
    def __init__(self, text):
        if "\t" in text:
            raise WorkflowYamlError("Tabs are not supported in workflow YAML")
        self.raw = text.split("\n")
        self.index = 0

    def peek(self):
        """Next significant line as (indent, content), skipping blanks and comments."""
        while self.index < len(self.raw):
            line = self.raw[self.index]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                self.index += 1
                continue
            if stripped in ("---", "...") or stripped.startswith("%"):
                raise WorkflowYamlError("Document markers and directives are not supported")
            return len(line) - len(line.lstrip(" ")), _strip_comment(line.strip())
        return None


def _block_scalar(lines, indicator, parent_indent):
    body = []
    content_indent = None
    while lines.index < len(lines.raw):
        line = lines.raw[lines.index]
        if line.strip():
            indent = len(line) - len(line.lstrip(" "))
            if indent <= parent_indent:
                break
            if content_indent is None:
                content_indent = indent
            if indent < content_indent:
                raise WorkflowYamlError("Block scalar line is less indented than its first line")
            body.append(line[content_indent:])
        else:
            body.append("")
        lines.index += 1
    while body and not body[-1]:
        body.pop()
    if indicator.startswith(">"):
        folded, paragraph = [], []
        for line in body:
            if line:
                paragraph.append(line)
            else:
                folded.append(" ".join(paragraph))
                paragraph = []
        folded.append(" ".join(paragraph))
        text = "\n".join(folded)
    else:
        text = "\n".join(body)
    return text if indicator.endswith("-") else text + "\n"


def _value(lines, rest, indent):
    """The value after 'key:' or '- ': inline, block scalar or nested block."""
    if rest and _BLOCK_SCALAR.match(rest):
        return _block_scalar(lines, rest, indent)
    if rest:
        following = lines.peek()
        if following is not None and following[0] > indent and not following[1].startswith("- "):
            raise WorkflowYamlError(f"Multi-line plain scalars are not supported after {rest!r}")
        return _scalar(rest)
    following = lines.peek()
    if following is None or following[0] <= indent:
        return None
    return _block(lines, following[0])


def _block(lines, indent):
    first = lines.peek()
    if first is None or first[0] != indent:
        raise WorkflowYamlError("Unexpected indentation")
    if first[1] == "-" or first[1].startswith("- "):
        return _sequence(lines, indent)
    return _mapping(lines, indent)


def _mapping(lines, indent, first_entry=None):
    mapping = {}
    while True:
        if first_entry is not None:
            content, first_entry = first_entry, None
        else:
            line = lines.peek()
            if line is None or line[0] < indent:
                return mapping
            if line[0] > indent:
                raise WorkflowYamlError(f"Unexpected indentation at {line[1]!r}")
            content = line[1]
            if content == "-" or content.startswith("- "):
                return mapping
            lines.index += 1
        entry = _key_split(content)
        if entry is None:
            raise WorkflowYamlError(f"Expected a mapping entry, got {content!r}")
        key, rest = entry
        if key in mapping:
            raise WorkflowYamlError(f"Duplicate key {key!r}")
        mapping[key] = _value(lines, rest, indent)


def _sequence(lines, indent):
    items = []
    while True:
        line = lines.peek()
        if line is None or line[0] < indent:
            return items
        if line[0] > indent:
            raise WorkflowYamlError(f"Unexpected indentation at {line[1]!r}")
        content = line[1]
        if not (content == "-" or content.startswith("- ")):
            return items
        lines.index += 1
        rest = content[1:].strip()
        if not rest:
            following = lines.peek()
            items.append(None if following is None or following[0] <= indent else _block(lines, following[0]))
            continue
        entry = _key_split(rest) if not rest.startswith(("'", '"', "[", "{")) else None
        if entry is None:
            items.append(_value(lines, rest, indent))
            continue
        # "- key: value" opens a mapping whose further keys align with "key".
        items.append(_mapping(lines, indent + 2, first_entry=rest))


def load(text):
    lines = _Lines(text)
    first = lines.peek()
    if first is None:
        return None
    if first[0] != 0:
        raise WorkflowYamlError("The document must start at column 0")
    document = _block(lines, 0)
    if lines.peek() is not None:
        raise WorkflowYamlError(f"Unparsed content at {lines.peek()[1]!r}")
    return document
