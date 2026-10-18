"""One reading of the Dockerfile for the build contract tests.

Docker joins a line that ends in a backslash with the next one and drops the
comment and blank lines inside such an instruction. instructions() does the
same, so a test sees each instruction whole, as BuildKit does, instead of
matching raw lines; stages() groups them by build stage. The tests keep their
own assertions; only the reading is shared.
"""

import re
from collections import namedtuple

# name: the AS name; base: the FROM image or stage; header: the whole FROM
# instruction (platform flag included); instructions: the rest, in file order.
Stage = namedtuple("Stage", "name base header instructions")


def instructions(text):
    """Yield every instruction with its continuation lines joined by one space."""
    pending = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        continued = stripped.endswith("\\")
        pending.append(stripped[:-1].rstrip() if continued else stripped)
        if not continued:
            yield " ".join(pending)
            pending = []
    if pending:
        raise ValueError("Unfinished Dockerfile instruction")


def stages(text):
    """Return {stage name: Stage} in file order; every stage must be named once."""
    result = {}
    current = None
    for instruction in instructions(text):
        keyword, _, rest = instruction.partition(" ")
        if keyword.upper() == "FROM":
            words = [word for word in rest.split() if not word.startswith("--")]
            if len(words) != 3 or words[1].upper() != "AS" or words[2] in result:
                raise ValueError(f"Expected one uniquely named stage: {instruction}")
            current = result[words[2]] = Stage(words[2], words[0], instruction, [])
        elif current is not None:
            current.instructions.append(instruction)
    return result


def run_scripts(stage):
    """The shell text of each RUN of STAGE, without its leading --mount/--network flags."""
    return [re.sub(r"\A(?:--\S+\s+)*", "", instruction.partition(" ")[2])
            for instruction in stage.instructions if instruction.partition(" ")[0].upper() == "RUN"]
