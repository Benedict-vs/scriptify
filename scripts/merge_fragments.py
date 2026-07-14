#!/usr/bin/env python3
"""
merge_fragments.py — concatenate a split lecture's parts into work/L##/L##.tex.

A long lecture is processed by several subagents (L03_a.tex, L03_b.tex, ...). Their
page ranges overlap so that neither has to guess at what happens across the seam —
which means the seam is exactly where they can BOTH write the same material. That
shows up as:
  * a duplicate \\label  -> LaTeX error, and a sign the same content was written twice
  * a repeated heading   -> the same topic opened twice
Neither is safe to merge blindly, so this refuses to write a merged file that has
duplicate labels, and warns on repeated headings. Fix the fragments, then re-run.

  python scripts/merge_fragments.py work/L03        # -> work/L03/L03.tex
  python scripts/merge_fragments.py                 # every split lecture in work/
"""
from __future__ import annotations
import re, sys
from collections import Counter
from pathlib import Path

FORCE = False
LABEL = re.compile(r"\\label\{([^}]*)\}")
HEADING = re.compile(r"\\(?:sub)*section\*?\{([^}]*)\}")
# A fragment's header comments legitimately quote the previous part's headings
# ("% Fortsetzung von \subsubsection{Activation functions}"), so scanning the raw
# text reports duplicates that do not exist in the typeset document.
COMMENT = re.compile(r"(?<!\\)%.*$", re.M)


def strip_comments(tex: str) -> str:
    return COMMENT.sub("", tex)


def merge(dir_: Path) -> bool:
    parts = sorted(dir_.glob(f"{dir_.name}_[a-z].tex"))
    if not parts:
        return True                       # not a split lecture; nothing to do

    texts = [p.read_text(encoding="utf-8") for p in parts]
    joined = "\n\n".join(texts)
    code = strip_comments(joined)          # only what LaTeX actually typesets

    dup_labels = [l for l, c in Counter(LABEL.findall(code)).items() if c > 1]
    dup_heads = [h for h, c in Counter(HEADING.findall(code)).items() if c > 1]

    if dup_labels:
        print(f"[!] {dir_.name}: duplicate \\label across fragments — the seam wrote the "
              f"same content twice. NOT merged.")
        for l in dup_labels:
            where = [p.name for p, t in zip(parts, texts) if f"\\label{{{l}}}" in strip_comments(t)]
            print(f"      {l}  in {', '.join(where)}")
        return False

    if dup_heads:
        print(f"[!] {dir_.name}: heading appears in more than one fragment — check the seam:")
        for h in dup_heads:
            print(f"      {h!r}")
        return False

    out = dir_ / f"{dir_.name}.tex"

    # The merged file is the SOURCE OF TRUTH once anyone has edited it — main.tex inputs
    # L##.tex, not the fragments. Stage-5 consistency runs, the slide-patch pass and the
    # vector pass all edit L##.tex directly and do NOT touch L##_a/_b/_c, so the fragments
    # go stale the moment that happens. Re-merging then silently reverts that work, and
    # because the result still compiles nobody notices — the same silent-loss failure mode
    # the extractor guards exist for, just aimed at the .tex instead of the frames.
    if out.exists() and not FORCE:
        newest_part = max(p.stat().st_mtime for p in parts)
        if out.stat().st_mtime > newest_part:
            print(f"[!] {dir_.name}: {out.name} is NEWER than its fragments — it has been "
                  f"edited since the merge (Stage 5 / slide-patch / vector pass all write "
                  f"L##.tex directly). Merging would silently revert those edits. NOT merged.\n"
                  f"      Re-run with --force only if you really mean to discard them.")
            return False

    header = (f"% ===== {dir_.name} — zusammengefuegt aus "
              f"{', '.join(p.name for p in parts)} =====\n")
    out.write_text(header + joined + "\n", encoding="utf-8")
    print(f"[✓] {dir_.name}: {len(parts)} fragments -> {out.name} "
          f"({len(joined.splitlines())} lines, {len(set(LABEL.findall(joined)))} labels)")
    return True


def main() -> None:
    global FORCE
    args = [a for a in sys.argv[1:] if a != "--force"]
    FORCE = "--force" in sys.argv[1:]
    dirs = [Path(a) for a in args] or sorted(
        p for p in Path("work").glob("L*") if p.is_dir())
    if not all([merge(d) for d in dirs]):
        sys.exit(1)


if __name__ == "__main__":
    main()
