#!/usr/bin/env python3
"""
push-dsh-models.py — Push the regenerated DSH seed into the live volume.

Source of truth: dsh/settings.yaml (rebuilt by `make sync` from the same
liveness-probed alias list as llm-gateway/config.yaml).

Merge strategy (no YAML dependency, stdlib only):
  1. Copy profiles/web/cordis.patch.yml out of the dsh_data volume.
  2. Split it into top-level `- id: ...` entries (comments/blank lines attach
     to the FOLLOWING entry, so the managed auth block is never touched).
  3. Replace every live entry whose id appears in the seed (models block +
     gateway-only overlays) with the fresh seed, or append it if absent
     (same as first-boot path).
  4. Byte-compare: identical → IN_SYNC (no restart); different → copy back,
     chown to runtime uid, report CHANGED (Makefile restarts DSH — HMR is off).

Prints human progress lines plus a final `RESULT:<TOKEN>` line for Make:
  CHANGED | IN_SYNC | NOPATCH (profile never booted) | NOSEED (seed missing)
"""

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path


def run(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def split_entries(text: str):
    """Split an entry-list patch file into segments.

    Attachment rules (validated against the live file shape):
    - Column-0 comments/blank lines attach FORWARD to the following entry
      (seed headers travel with their `- id:` block, so replacing the block
      drops its stale header too).
    - Indented comments stay put (they are internal to an entry).
    - Exception: a column-0 comment containing `<<<` attaches BACKWARD — it
      is the managed-block close marker (`# <<< dsh-docker-server ...`),
      and the entrypoint rewrites that block delimited by its markers.
    """
    segments: list[list[str]] = []
    cur: list[str] = []
    pending: list[str] = []
    in_entry = False
    for line in text.splitlines(keepends=True):
        stripped = line.strip()
        col0_comment_or_blank = (not stripped) or (line.startswith("#") and line == line.lstrip())
        if not in_entry and col0_comment_or_blank:
            pending.append(line)  # file preamble: belongs to the first entry
            continue
        if col0_comment_or_blank and "<<<" not in line:
            pending.append(line)  # boundary: belongs to the next entry
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


def is_empty_doc(lines: list[str]) -> bool:
    """True if the segment is just the empty flow-list document (`[]`).

    DSH writes fresh patch files as comments + a bare `[]` line. A block
    entry appended AFTER that line is a YAML parse error (`end of the stream
    ... expected`), which crash-loops `dsh web`. So whenever the seed
    provides entries, `[]`-only segments are dropped and the seed takes
    their place. Segments carrying real entries or genuine user content
    are never affected.
    """
    body = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return len(body) == 1 and body[0].strip() == "[]"


def entry_key(lines: list[str]) -> str | None:
    for ln in lines:
        m = re.match(r"^- id: (\S+)\s*$", ln)
        if m:
            return m.group(1)
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Push DSH seed into live cordis.patch.yml.")
    parser.add_argument("--seed", type=Path, default=Path("dsh/settings.yaml"))
    parser.add_argument("--volume", default="phobos_dsh_data")
    parser.add_argument("--patch", default="profiles/web/cordis.patch.yml")
    parser.add_argument("--uid", type=int, default=1000, help="Runtime UID for chown")
    parser.add_argument("--gid", type=int, default=1000, help="Runtime GID for chown")
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parent.parent
    seed_file = args.seed if args.seed.is_absolute() else (repo_root / args.seed)
    if not seed_file.exists():
        print(f"seed not found: {seed_file}", flush=True)
        print("RESULT:NOSEED", flush=True)
        return 0

    seed = seed_file.read_text(encoding="utf-8")
    if not seed.endswith("\n"):
        seed += "\n"

    tmp = Path(tempfile.mkdtemp(prefix="dsh-push-"))
    try:
        cp_out = run([
            "docker", "run", "--rm",
            "-v", f"{args.volume}:/data:ro",
            "-v", f"{tmp}:/work",
            "alpine", "cp", f"/data/{args.patch}", "/work/patch.yml",
        ])
        if cp_out.returncode != 0:
            print("live patch file not present (profile never booted?) — nothing to refresh.", flush=True)
            print("RESULT:NOPATCH", flush=True)
            return 0

        original = (tmp / "patch.yml").read_text(encoding="utf-8")
        # Managed set = every entry id present in the seed (llm-pi-ai models
        # block + gateway-only overlays). Live segments with those keys are
        # dropped and the fresh seed is inserted at the first drop position;
        # every other entry (managed auth block, UI edits) is preserved.
        seed_keys = [k for k in (entry_key(s) for s in split_entries(seed)) if k]
        segments = split_entries(original)
        out_parts: list[str] = []
        inserted = False
        for seg in segments:
            if entry_key(seg) in seed_keys and not inserted:
                out_parts.append(seed)
                inserted = True
            elif entry_key(seg) in seed_keys:
                continue  # stale duplicate managed segment
            elif seed_keys and is_empty_doc(seg):
                continue  # bare `[]`: seed takes its place (see is_empty_doc)
            else:
                out_parts.append("".join(seg))
        if not inserted:
            out_parts.append(seed)

        merged = "".join(out_parts)
        if not merged.endswith("\n"):
            merged += "\n"

        norm = lambda s: s.rstrip("\n") + "\n"
        if norm(merged) == norm(original):
            print("live patch already matches seed — no restart needed.", flush=True)
            print("RESULT:IN_SYNC", flush=True)
            return 0

        (tmp / "patch.yml").write_text(norm(merged), encoding="utf-8")
        cp_back = run([
            "docker", "run", "--rm",
            "-v", f"{args.volume}:/data",
            "-v", f"{tmp}:/work",
            "alpine", "sh", "-c",
            f"cp /work/patch.yml /data/{args.patch} && chown {args.uid}:{args.gid} /data/{args.patch}",
        ])
        if cp_back.returncode != 0:
            print(f"ERROR writing back to volume: {cp_back.stderr.strip()}", flush=True)
            return 1
        action = "replaced" if inserted else "appended"
        print(f"managed entries ({', '.join(seed_keys)}) {action} in live patch ({len(seed.splitlines())} seed lines).", flush=True)
        print("RESULT:CHANGED", flush=True)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())