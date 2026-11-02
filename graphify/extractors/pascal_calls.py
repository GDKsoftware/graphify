"""Call-site helpers shared by the Pascal/Delphi extractors."""
from __future__ import annotations

import re

_DOT_SPACE_RE = re.compile(r"\s*\.\s*")


def split_callee(text: str) -> tuple[str | None, str]:
    """Split ``a.b.Foo`` into ``("a.b", "Foo")`` at the last top-level dot.

    Dots inside ``()`` or ``[]`` belong to an expression receiver and do not
    split. Whitespace around dots is dropped. Without a dot the qualifier is
    ``None``.
    """
    text = text.strip()
    depth = 0
    split_at = -1
    for i, ch in enumerate(text):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        elif ch == "." and depth == 0:
            split_at = i
    if split_at < 0:
        return None, text
    qualifier = _DOT_SPACE_RE.sub(".", text[:split_at].strip())
    return qualifier, text[split_at + 1:].strip()


def is_expression_receiver(qualifier: str | None) -> bool:
    """True when the receiver is an expression (``a[i]``, ``GetX()``, ``p^``)."""
    return bool(qualifier) and any(c in qualifier for c in "([^")


def count_args(args_text: str | None) -> int | None:
    """Count the top-level arguments of a call.

    ``None`` (no parentheses) and an empty text give 0. Unbalanced brackets
    give ``None``: the count is then not certain.
    """
    if args_text is None or not args_text.strip():
        return 0
    depth = 0
    commas = 0
    in_string = False
    for ch in args_text:
        if in_string:
            in_string = ch != "'"
        elif ch == "'":
            in_string = True
        elif ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
            if depth < 0:
                return None
        elif ch == "," and depth == 0:
            commas += 1
    if depth != 0 or in_string:
        return None
    return commas + 1


def make_raw_call(
    *,
    source_file: str,
    line: int,
    caller_nid: str,
    callee: str,
    qualifier: str | None,
    arg_count: int | None,
    enclosing_routines: list[str],
    local_receiver: bool = False,
) -> dict:
    """Build a Pascal raw call (spec section 4.2, without ``with_stack``)."""
    return {
        "language": "pascal",
        "source_file": source_file,
        "source_location": f"L{line}",
        "caller_nid": caller_nid,
        "callee": callee,
        "qualifier": qualifier,
        # An expression receiver, or a receiver that is a parameter or local of
        # the caller, is a member call on a value of unknown type; whether any
        # other plain qualifier is a unit, variable or type is up to the resolver.
        "is_member_call": local_receiver or is_expression_receiver(qualifier),
        "arg_count": arg_count,
        "enclosing_routines": enclosing_routines,
    }


def is_local_receiver(qualifier: str | None, shadow: set[str]) -> bool:
    """Whether the head of `qualifier` names a parameter or local of the caller."""
    if not qualifier or qualifier.lower() in ("self", "inherited"):
        return False
    return qualifier.split(".", 1)[0].strip().lower() in shadow
