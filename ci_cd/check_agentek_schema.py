"""Guards the `// BEGIN agentek` ... `// END agentek` block of schema.prisma.

Rolling back to an image whose schema lacks part of the block makes the default migration resolver
drop the missing tables, so the block must be identical in all copies and may only grow.
"""

import argparse
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path

BEGIN_MARKER = "// BEGIN agentek"
END_MARKER = "// END agentek"
SCHEMA_COPIES = (
    "schema.prisma",
    "litellm/proxy/schema.prisma",
    "litellm-proxy-extras/litellm_proxy_extras/schema.prisma",
)

MODEL_PATTERN = re.compile(r"^model\s+(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL)


def extract_block(schema_text: str) -> str | None:
    """The text between the markers, or None unless the markers occur once and the block ends the file."""
    if schema_text.count(BEGIN_MARKER) != 1 or schema_text.count(END_MARKER) != 1:
        return None
    before_end, end, tail = schema_text.partition(END_MARKER)
    _, begin, block = before_end.partition(BEGIN_MARKER)
    if not (begin and end) or tail.strip():
        return None
    return block.strip()


def block_models(block: str) -> dict[str, frozenset[str]]:
    """Model name -> its normalized definition lines (comments and blanks dropped)."""
    return {
        name: frozenset(
            " ".join(line.split())
            for line in body.splitlines()
            if line.strip() and not line.strip().startswith("//")
        )
        for name, body in MODEL_PATTERN.findall(block)
    }


def removed_definitions(base_block: str, new_block: str) -> tuple[str, ...]:
    """Definition lines (a removed model loses all of its lines) of `base_block` absent from `new_block`."""
    new_models = block_models(new_block)
    return tuple(
        f"{name}: {definition}"
        for name, definitions in block_models(base_block).items()
        for definition in sorted(definitions - new_models.get(name, frozenset()))
    )


def copy_problems(copies: Mapping[str, str]) -> tuple[str, ...]:
    blocks = {path: extract_block(text) for path, text in copies.items()}
    malformed = tuple(
        f"{path}: agentek block missing, duplicated or not at the end of the file"
        for path, block in blocks.items()
        if block is None
    )
    if malformed:
        return malformed
    reference_path, reference = next(iter(blocks.items()))
    return tuple(
        f"{path}: agentek block differs from {reference_path}"
        for path, block in blocks.items()
        if block != reference
    )


def growth_problems(base_schema: str, new_schema: str) -> tuple[str, ...]:
    base_block = extract_block(base_schema)
    new_block = extract_block(new_schema)
    if base_block is None:
        return ()
    if new_block is None:
        return ("agentek block was present in the base and is gone",)
    return tuple(
        f"agentek block lost a definition: {removed}"
        for removed in removed_definitions(base_block, new_block)
    )


def read_base_schema(base_ref: str) -> str:
    return subprocess.run(
        ["git", "show", f"{base_ref}:{SCHEMA_COPIES[0]}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref", help="git ref whose block the current one must contain"
    )
    args = parser.parse_args(argv)

    copies = {path: Path(path).read_text() for path in SCHEMA_COPIES}
    problems = copy_problems(copies)
    if args.base_ref and not problems:
        problems = growth_problems(
            read_base_schema(args.base_ref), copies[SCHEMA_COPIES[0]]
        )
    sys.stderr.writelines(f"{problem}\n" for problem in problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
