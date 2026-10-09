"""Guards the `// BEGIN agentek` ... `// END agentek` block of schema.prisma.

Rolling back to an image whose schema lacks part of the block makes the default migration resolver
drop the missing tables, so the block must be identical in all copies and may only grow: nothing is
removed, retyped or physically renamed (`@map`, `@@map`, `@@schema`).
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

DECLARATION_PATTERN = re.compile(
    r"^(model|enum|type|view)\s+(\w+)\s*\{(.*?)^\}", re.MULTILINE | re.DOTALL
)
PHYSICAL_RENAME_PATTERN = re.compile(r"@@?(?:map|schema)\s*\(")
TYPE_MODIFIERS = "?[]"

Declarations = dict[tuple[str, str], dict[str, str]]


def has_markers(schema_text: str) -> bool:
    return BEGIN_MARKER in schema_text or END_MARKER in schema_text


def extract_block(schema_text: str) -> str | None:
    """The text between the markers, or None unless the markers occur once and the block ends the file."""
    if schema_text.count(BEGIN_MARKER) != 1 or schema_text.count(END_MARKER) != 1:
        return None
    before_end, end, tail = schema_text.partition(END_MARKER)
    _, begin, block = before_end.partition(BEGIN_MARKER)
    if not (begin and end) or tail.strip():
        return None
    return block.strip()


def code_lines(text: str) -> tuple[str, ...]:
    """Lines without comments and blanks."""
    stripped = (line.split("//", 1)[0].strip() for line in text.splitlines())
    return tuple(line for line in stripped if line)


def block_declarations(block: str) -> Declarations:
    """(kind, name) -> {member: base type}; enum values map to an empty type."""
    return {
        (kind, name): {
            words[0]: (
                words[1].rstrip(TYPE_MODIFIERS)
                if len(words) > 1 and kind != "enum"
                else ""
            )
            for words in (line.split() for line in code_lines(body))
            if not words[0].startswith("@@")
        }
        for kind, name, body in DECLARATION_PATTERN.findall(block)
    }


def physical_rename_problems(block: str) -> tuple[str, ...]:
    return tuple(
        f"agentek block must not use physical renames: {line}"
        for line in code_lines(block)
        if PHYSICAL_RENAME_PATTERN.search(line)
    )


def removed_definitions(base_block: str, new_block: str) -> tuple[str, ...]:
    """Declarations, members and member types of `base_block` that `new_block` lacks."""
    new_declarations = block_declarations(new_block)
    return tuple(
        f"{kind} {name}: {member} {member_type}".rstrip()
        for (kind, name), members in block_declarations(base_block).items()
        for member, member_type in members.items()
        if new_declarations.get((kind, name), {}).get(member) != member_type
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
    new_block = extract_block(new_schema)
    if new_block is None:
        return ("the current agentek block is missing or malformed",)
    renames = physical_rename_problems(new_block)
    if not has_markers(base_schema):
        return renames
    base_block = extract_block(base_schema)
    if base_block is None:
        return (*renames, "the base agentek block is malformed, cannot compare")
    return (
        *renames,
        *(
            f"agentek block lost a definition: {removed}"
            for removed in removed_definitions(base_block, new_block)
        ),
    )


def read_base_schema(base_ref: str) -> str:
    try:
        return subprocess.run(
            ["git", "show", f"{base_ref}:{SCHEMA_COPIES[0]}"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    except subprocess.CalledProcessError as error:
        raise SystemExit(
            f"cannot read {SCHEMA_COPIES[0]} at {base_ref}: {error.stderr.strip()}\n"
            "Fetch the base branch first (for example `git fetch origin master`)."
        ) from error


def main(argv: Sequence[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-ref", help="git ref whose block the current one must contain"
    )
    args = parser.parse_args(argv)

    copies = {path: Path(path).read_text() for path in SCHEMA_COPIES}
    problems = copy_problems(copies)
    if not problems:
        reference = copies[SCHEMA_COPIES[0]]
        problems = (
            growth_problems(read_base_schema(args.base_ref), reference)
            if args.base_ref
            else physical_rename_problems(extract_block(reference) or "")
        )
    sys.stderr.writelines(f"{problem}\n" for problem in problems)
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
