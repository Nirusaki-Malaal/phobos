#!/usr/bin/env python3
"""Drop top-level patch segments containing a nested `- id: <target>` row.

Used by the DSH entrypoint to retire the dsh-default-workspace plugin insert
without touching any other entry. Segment splitting mirrors split_entries()
in scripts/push-dsh-models.py (column-0 comments/blank lines attach forward).

Usage: dsh-drop-patch-entry <patch-file> <nested-id>
"""

import re
import sys


def split_segments(text):
    segments, cur, pending, in_entry = [], [], [], False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        col0 = (not stripped) or (line.startswith("#") and line == line.lstrip())
        if not in_entry and col0:
            pending.append(line)
            continue
        if col0 and "<<<" not in line:
            pending.append(line)
            continue
        if line.startswith("- ") and in_entry:
            segments.append(cur)
            cur = pending + [line]
            pending = []
        else:
            if not in_entry:
                cur = pending + [line]
                pending = []
                in_entry = True
            else:
                cur.append(line)
    if pending and not in_entry:
        cur = pending
    elif pending:
        cur.extend(pending)
    if cur:
        segments.append(cur)
    return segments


def main(patch, target):
    with open(patch, encoding="utf-8") as f:
        original = f.read()
    nested = re.compile(r"^\s+-\s+id:\s+" + re.escape(target) + r"(\s|$)")
    out, dropped = [], 0
    for seg in split_segments(original):
        if any(nested.match(ln) for ln in seg):
            dropped += 1
            continue
        out.append("".join(seg))
    merged = "".join(out)
    if merged != original:
        with open(patch, "w", encoding="utf-8") as f:
            f.write(merged)
    print(f"dropped {dropped} segment(s) containing nested id '{target}'")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])
