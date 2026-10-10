"""Operator documentation for the distribution: copy ``03_SW/docs/{USER_MANUAL, QUICK_REFERENCE}.md`` + ``img/`` into
``<dist>/docs/`` and render each Markdown file to a self-contained HTML page next to it (stdlib only, KD-11).

Windows 10 has no default program for ``.md`` files, so the Start-menu entries and ``BUILD_INFO.txt`` point at the
``.html`` pages (screenshots shown, table of contents links work); the ``.md`` sources are installed as well.

The converter covers the Markdown subset the manuals use: ATX headings with GitHub-style ids (the manuals' table of
contents links ``#9-load-calibration-with-1-kg--10-kg``), paragraphs, nested ordered / unordered lists (with tables,
images and paragraphs inside items), pipe tables, fenced code, block quotes, rules, and inline code, bold, italic,
links (``*.md`` → ``*.html``; links leaving ``docs/`` become plain text) and images.

Usage: ``.venv\\Scripts\\python 03_SW\\packaging\\docs_html.py --dist <dist folder>`` (``buildinfo.py finalize``
calls :func:`install_docs`).

Implements: SW-PLT-001 (installable application incl. its operator documentation, OI-UM-03)
"""
from __future__ import annotations

import argparse
import html
import re
import shutil
import sys
from pathlib import Path

SW_ROOT = Path(__file__).resolve().parent.parent
DOCS_SRC = SW_ROOT / "docs"
DOC_FILES = ("USER_MANUAL.md", "QUICK_REFERENCE.md")
IMG_DIR = "img"

_FENCE = re.compile(r"^\s*```")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_HR = re.compile(r"^\s*(-{3,}|\*{3,}|_{3,})\s*$")
_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
_TABLE_SEP = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")
_IMAGE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)\)")
_LINK = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
_CODE = re.compile(r"(`+)(.+?)\1")

CSS = """
body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:1.5em auto;padding:0 1em;line-height:1.45;color:#222}
h1,h2,h3,h4{color:#1f4e79;margin-top:1.4em} h1{border-bottom:2px solid #1f4e79}
table{border-collapse:collapse;margin:.6em 0} th,td{border:1px solid #bbb;padding:.25em .5em;vertical-align:top}
th{background:#e8eef5} code{background:#f2f2f2;padding:0 .2em;border-radius:3px;font-family:Consolas,monospace}
pre{background:#f2f2f2;padding:.6em;overflow:auto} pre code{padding:0} img{max-width:100%;border:1px solid #ccc}
blockquote{border-left:4px solid #ccc;margin-left:0;padding-left:1em;color:#444} li{margin:.15em 0}
"""


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def slug(text: str) -> str:
    """GitHub heading id: plain text, lower case, punctuation removed, spaces → '-'."""
    t = _IMAGE.sub(r"\1", text)
    t = _LINK.sub(r"\1", t)
    t = t.replace("`", "").replace("**", "").replace("*", "")
    t = re.sub(r"[^\w\- ]", "", t.strip().lower())
    return t.replace(" ", "-")


class Renderer:
    def __init__(self, link_map: dict[str, str] | None = None) -> None:
        self.link_map = link_map or {}
        self.ids: dict[str, int] = {}

    # ------------------------------------------------------------------------------------------------ inline
    def _href(self, href: str) -> str | None:
        if href.startswith(("#", "http://", "https://", "mailto:")):
            return href
        path, _, frag = href.partition("#")
        if path in self.link_map:
            return self.link_map[path] + (f"#{frag}" if frag else "")
        if path.startswith("../") or path.startswith("/"):
            return None                                   # outside the installed docs folder
        return href

    def inline(self, text: str) -> str:
        codes: list[str] = []

        def keep(m: re.Match[str]) -> str:
            codes.append(f"<code>{html.escape(m.group(2).strip(), quote=False)}</code>")
            return f"\x00{len(codes) - 1}\x00"

        def literal(m: re.Match[str]) -> str:                    # backslash escape: \* \_ \| \` …
            codes.append(html.escape(m.group(1), quote=False))
            return f"\x00{len(codes) - 1}\x00"

        t = _CODE.sub(keep, text)
        t = html.escape(re.sub(r"\\([\\`*_{}\[\]()#+\-.!|>~])", literal, t), quote=False)
        t = _IMAGE.sub(lambda m: f'<img src="{html.escape(m.group(2))}" alt="{m.group(1)}">', t)

        def link(m: re.Match[str]) -> str:
            href = self._href(html.unescape(m.group(2)))
            return m.group(1) if href is None else f'<a href="{html.escape(href)}">{m.group(1)}</a>'

        t = _LINK.sub(link, t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"(?<![\w*])\*(?![\s*])(.+?)(?<![\s*])\*(?![\w*])", r"<em>\1</em>", t)
        t = re.sub(r"(?<![\w])_(?![\s_])(.+?)(?<![\s_])_(?![\w])", r"<em>\1</em>", t)
        return re.sub("\x00(\\d+)\x00", lambda m: codes[int(m.group(1))], t)

    # ------------------------------------------------------------------------------------------------ blocks
    def _starts_block(self, lines: list[str], i: int) -> bool:
        line = lines[i]
        return bool(_FENCE.match(line) or _HEADING.match(line) or _HR.match(line) or _ITEM.match(line)
                    or line.lstrip().startswith(">") or self._is_table(lines, i))

    @staticmethod
    def _is_table(lines: list[str], i: int) -> bool:
        return (lines[i].lstrip().startswith("|") and i + 1 < len(lines)
                and bool(_TABLE_SEP.match(lines[i + 1])))

    @staticmethod
    def _cells(line: str) -> list[str]:
        s = line.strip()
        s = s[1:] if s.startswith("|") else s
        s = s[:-1] if s.endswith("|") and not s.endswith("\\|") else s
        return [c.strip() for c in re.split(r"(?<!\\)\|", s)]

    def blocks(self, lines: list[str]) -> str:
        out: list[str] = []
        i, n = 0, len(lines)
        while i < n:
            line = lines[i]
            if not line.strip():
                i += 1
                continue
            if _FENCE.match(line):
                j = i + 1
                while j < n and not _FENCE.match(lines[j]):
                    j += 1
                body = "\n".join(lines[i + 1:j])
                out.append(f"<pre><code>{html.escape(body, quote=False)}</code></pre>")
                i = j + 1
                continue
            m = _HEADING.match(line)
            if m:
                level, text = len(m.group(1)), m.group(2)
                sid = slug(text)
                k = self.ids.get(sid, 0)
                self.ids[sid] = k + 1
                sid = sid if k == 0 else f"{sid}-{k}"
                out.append(f'<h{level} id="{sid}">{self.inline(text)}</h{level}>')
                i += 1
                continue
            if _HR.match(line):
                out.append("<hr>")
                i += 1
                continue
            if self._is_table(lines, i):
                head = self._cells(line)
                rows = []
                j = i + 2
                while j < n and lines[j].lstrip().startswith("|"):
                    rows.append(self._cells(lines[j]))
                    j += 1
                th = "".join(f"<th>{self.inline(c)}</th>" for c in head)
                trs = "".join("<tr>" + "".join(f"<td>{self.inline(c)}</td>" for c in r) + "</tr>" for r in rows)
                out.append(f"<table><thead><tr>{th}</tr></thead><tbody>{trs}</tbody></table>")
                i = j
                continue
            if line.lstrip().startswith(">"):
                j = i
                quoted = []
                while j < n and lines[j].lstrip().startswith(">"):
                    quoted.append(re.sub(r"^\s*>\s?", "", lines[j]))
                    j += 1
                out.append(f"<blockquote>{self.blocks(quoted)}</blockquote>")
                i = j
                continue
            if _ITEM.match(line):
                html_list, i = self._list(lines, i)
                out.append(html_list)
                continue
            para = [line.strip()]
            i += 1
            while i < n and lines[i].strip() and not self._starts_block(lines, i):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{self.inline(' '.join(para))}</p>")
        return "\n".join(out)

    def _list(self, lines: list[str], i: int) -> tuple[str, int]:
        first = _ITEM.match(lines[i])
        assert first is not None
        base = len(first.group(1))
        ordered = first.group(2)[0].isdigit()
        start = int(first.group(2)[:-1]) if ordered else 1
        items: list[str] = []
        n = len(lines)
        while i < n:
            m = _ITEM.match(lines[i])
            if m is None or len(m.group(1)) != base or m.group(2)[0].isdigit() != ordered:
                break
            content_indent = base + len(m.group(2)) + 1
            body = [m.group(3)]
            i += 1
            while i < n:
                line = lines[i]
                if not line.strip():
                    j = i
                    while j < n and not lines[j].strip():
                        j += 1
                    if j < n and _indent(lines[j]) > base:
                        body.extend([""] * (j - i))
                        i = j
                        continue
                    break
                if _indent(line) > base:
                    body.append(line[min(_indent(line), content_indent):])
                    i += 1
                    continue
                if not self._starts_block(lines, i) and body[-1].strip():
                    body.append(line.strip())                    # lazy continuation of the item's paragraph
                    i += 1
                    continue
                break
            inner = self.blocks(body)
            if inner.startswith("<p>") and inner.count("<p>") == 1 and inner.endswith("</p>"):
                inner = inner[3:-4]                              # tight item
            elif inner.startswith("<p>"):
                k = inner.index("</p>")
                inner = inner[3:k] + inner[k + 4:]               # first paragraph inline with the marker
            items.append(f"<li>{inner}</li>")
            j = i
            while j < n and not lines[j].strip():
                j += 1
            if j < n and (m2 := _ITEM.match(lines[j])) and len(m2.group(1)) == base \
                    and m2.group(2)[0].isdigit() == ordered:
                i = j
                continue
            break
        tag = "ol" if ordered else "ul"
        attr = f' start="{start}"' if ordered and start != 1 else ""
        return f"<{tag}{attr}>" + "".join(items) + f"</{tag}>", i


def render(md: str, link_map: dict[str, str] | None = None, title: str | None = None) -> str:
    lines = md.replace("\r\n", "\n").split("\n")
    if title is None:
        m = next((_HEADING.match(x) for x in lines if _HEADING.match(x)), None)
        title = m.group(2) if m else "Document"
    body = Renderer(link_map).blocks(lines)
    return (f'<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8"><title>{html.escape(title)}</title>'
            f"<style>{CSS}</style></head>\n<body>\n{body}\n</body></html>\n")


def image_refs(md: str) -> list[str]:
    return [m.group(2) for m in _IMAGE.finditer(md) if not m.group(2).startswith(("http://", "https://"))]


def install_docs(dist: Path, src: Path = DOCS_SRC) -> list[Path]:
    """Copy the manuals + screenshots into ``<dist>/docs`` and render the HTML pages; returns the written files.
    Raises ``FileNotFoundError`` if a manual or an image it references is missing."""
    out = dist / "docs"
    if out.exists():
        shutil.rmtree(out)
    (out / IMG_DIR).mkdir(parents=True)
    link_map = {name: name[:-3] + ".html" for name in DOC_FILES}
    written: list[Path] = []
    for p in sorted((src / IMG_DIR).glob("*")):
        if p.is_file():
            written.append(Path(shutil.copy2(p, out / IMG_DIR / p.name)))
    for name in DOC_FILES:
        md = (src / name).read_text(encoding="utf-8")
        missing = [r for r in image_refs(md) if not (src / r).is_file()]
        if missing:
            raise FileNotFoundError(f"{name}: missing images {missing}")
        written.append(Path(shutil.copy2(src / name, out / name)))
        page = out / link_map[name]
        page.write_text(render(md, link_map), encoding="utf-8")
        written.append(page)
    return written


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="docs_html.py")
    ap.add_argument("--dist", required=True)
    args = ap.parse_args(argv)
    files = install_docs(Path(args.dist))
    print(f"docs   {len(files)} files -> {Path(args.dist) / 'docs'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
