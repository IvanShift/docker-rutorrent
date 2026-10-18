"""A strict parser for the YAML subset of this repository's GitHub workflows.

The CI contracts must inspect what GitHub executes, not substrings of the file,
and the runner's system Python has no YAML library. This parser accepts block
mappings and sequences, single-line flow collections, quoted and plain
scalars, comments and literal or folded block scalars. Scalars stay strings.
Anything else (anchors, aliases, tags, multi-line plain or flow values, tabs,
duplicate keys) is refused, so an unusual edit fails the contracts closed.
"""

import re


class WorkflowYamlError(ValueError):
    pass


_BLOCK_SCALAR = re.compile(r"[|>][+-]?\Z")


def _strip_comment(text):
    """Drop a trailing comment that starts outside quotes after whitespace."""
    quote = None
    index = 0
    while index < len(text):
        char = text[index]
        if quote:
            if char == quote:
                if quote == "'" and index + 1 < len(text) and text[index + 1] == "'":
                    index += 2
                    continue
                quote = None
            elif quote == '"' and char == "\\":
                index += 1
        elif char in "'\"" and (index == 0 or text[index - 1] in " \t[{,:"):
            quote = char
        elif char == "#" and (index == 0 or text[index - 1] in " \t"):
            return text[:index].rstrip()
        index += 1
    if quote:
        raise WorkflowYamlError(f"Unterminated quoted scalar: {text!r}")
    return text.rstrip()


def _split_flow(text):
    """Split the inside of a single-line flow collection at top-level commas."""
    parts, depth, quote, start = [], 0, None, 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
        elif char in "'\"":
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == "," and depth == 0:
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
    quote = None
    depth = 0
    for index, char in enumerate(text):
        if quote:
            if char == quote:
                quote = None
            continue
        if char in "'\"" and index == 0:
            quote = char
        elif char in "[{":
            depth += 1
        elif char in "]}":
            depth -= 1
        elif char == ":" and depth == 0 and (index + 1 == len(text) or text[index + 1] == " "):
            key = text[:index].strip()
            if not key:
                return None
            return _scalar(key), text[index + 1:].strip()
    return None


def _scalar(text):
    text = text.strip()
    if not text:
        return None
    if text[0] in "&*!|>%@`":
        raise WorkflowYamlError(f"Unsupported YAML construct: {text!r}")
    if text.startswith("'"):
        if not text.endswith("'") or len(text) < 2:
            raise WorkflowYamlError(f"Unsupported quoted scalar: {text!r}")
        return text[1:-1].replace("''", "'")
    if text.startswith('"'):
        if not text.endswith('"') or len(text) < 2:
            raise WorkflowYamlError(f"Unsupported quoted scalar: {text!r}")
        body = text[1:-1]
        return re.sub(r'\\(["\\/nt])', lambda m: {"n": "\n", "t": "\t"}.get(m[1], m[1]), body)
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
