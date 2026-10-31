"""Markdown -> X Article ``content_state`` (the Draft.js raw shape x.com's Article editor sends).

The write mutations take snake_case keys (``entity_map``, ``inline_style_ranges``,
``entity_ranges``); X answers with the camelCase Draft.js names.

Supported: ``#``/``##``/``###`` headings (X has one heading level in body text, header-two),
paragraphs, ``-``/``*`` and ``1.`` lists, ``>`` quotes, ``**bold**``, ``*italic*``/``_italic_``,
``[label](url)`` links, ``---`` dividers, fenced code (a MARKDOWN entity, as X stores it) and
pipe tables (one list item per row, "Header: value" pairs; Articles have no tables).
Inline images are left to the caller: pass already uploaded media ids through ``media``.
"""
import random
import re
import string
from typing import Dict, List, Optional, Tuple

_INLINE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|(?<![*\w])\*(?!\s)(.+?)(?<!\s)\*(?![*\w])|(?<!\w)_(?!\s)(.+?)(?<!\s)_(?!\w)")


def _u16(text: str) -> int:
    """Draft.js offsets count UTF-16 code units, like JavaScript strings."""
    return len(text.encode("utf-16-le")) // 2


def _key(used: set) -> str:
    while True:
        k = "".join(random.choice(string.ascii_lowercase + string.digits) for _ in range(5))
        if k not in used:
            used.add(k)
            return k


class _Builder:
    def __init__(self):
        self.blocks: List[Dict] = []
        self.entities: List[Dict] = []
        self._keys: set = set()

    def entity(self, type_: str, mutability: str, data: Dict) -> int:
        n = len(self.entities)
        self.entities.append({"key": str(n), "value": {"type": type_, "mutability": mutability, "data": data}})
        return n

    def inline(self, raw: str) -> Tuple[str, List[Dict], List[Dict]]:
        text, styles, ranges, pos = "", [], [], 0
        for m in _INLINE.finditer(raw):
            text += raw[pos:m.start()]
            start = _u16(text)
            link_label, link_url, bold, italic = m.group(1), m.group(2), m.group(3), m.group(4) or m.group(5)
            if link_label is not None:
                inner, inner_styles, _ = self.inline(link_label)
                text += inner
                ranges.append({"key": self.entity("LINK", "Mutable", {"url": link_url}),
                               "offset": start, "length": _u16(inner)})
                styles += [dict(s, offset=s["offset"] + start) for s in inner_styles]
            else:
                inner, inner_styles, inner_ranges = self.inline(bold if bold is not None else italic)
                text += inner
                styles.append({"style": "Bold" if bold is not None else "Italic", "offset": start, "length": _u16(inner)})
                styles += [dict(s, offset=s["offset"] + start) for s in inner_styles]
                ranges += [dict(r, offset=r["offset"] + start) for r in inner_ranges]
            pos = m.end()
        text += raw[pos:]
        return text, styles, ranges

    def block(self, type_: str, raw: str = "", entity: Optional[int] = None) -> None:
        if entity is not None:
            text, styles, ranges = " ", [], [{"key": entity, "offset": 0, "length": 1}]
        else:
            text, styles, ranges = self.inline(raw)
        self.blocks.append({"key": _key(self._keys), "type": type_, "text": text, "data": {},
                            "inline_style_ranges": styles, "entity_ranges": ranges})


def _table_rows(lines: List[str]) -> List[str]:
    cells = [[c.strip() for c in l.strip().strip("|").split("|")] for l in lines]
    if len(cells) < 2 or not all(set(c) <= set("-: ") for c in cells[1]):
        return [" ".join(l.split()) for l in lines]
    head, rows = cells[0], cells[2:]
    return [", ".join(f"{h}: {v}" if h else v for h, v in zip(head, row) if v) for row in rows]


def markdown_to_content_state(md: str, media: Optional[Dict[str, Dict]] = None) -> Dict:
    """``media`` maps an image url used as ``![alt](url)`` on its own line to
    ``{"media_id": ..., "category": "DraftTweetImage"}``; unmapped images are dropped."""
    b = _Builder()
    media = media or {}
    lines = (md or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s:
            i += 1
            continue
        if s.startswith("```"):
            j = i + 1
            while j < len(lines) and not lines[j].strip().startswith("```"):
                j += 1
            code = "\n".join(lines[i:j + 1]) + ("" if j < len(lines) else "\n```")
            b.block("atomic", entity=b.entity("MARKDOWN", "Mutable", {"markdown": code}))
            i = j + 1
            continue
        if re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", s):
            b.block("atomic", entity=b.entity("DIVIDER", "Immutable", {}))
            i += 1
            continue
        img = re.fullmatch(r"!\[[^\]]*\]\(([^)\s]+)\)", s)
        if img:
            m = media.get(img.group(1))
            if m:
                b.block("atomic", entity=b.entity("MEDIA", "Immutable", {"mediaItems": [{
                    "localMediaId": str(len(b.entities)), "mediaCategory": m.get("category", "DraftTweetImage"),
                    "mediaId": str(m["media_id"])}]}))
            i += 1
            continue
        if s.startswith("|"):
            j = i
            while j < len(lines) and lines[j].strip().startswith("|"):
                j += 1
            for row in _table_rows(lines[i:j]):
                b.block("unordered-list-item", row)
            i = j
            continue
        h = re.match(r"^#{1,6}\s+(.+)$", s)
        if h:
            b.block("header-two", h.group(1).strip())
            i += 1
            continue
        ul = re.match(r"^[-*+]\s+(.+)$", s)
        if ul:
            b.block("unordered-list-item", ul.group(1))
            i += 1
            continue
        ol = re.match(r"^\d+[.)]\s+(.+)$", s)
        if ol:
            b.block("ordered-list-item", ol.group(1))
            i += 1
            continue
        if s.startswith(">"):
            quote = []
            while i < len(lines) and lines[i].strip().startswith(">"):
                quote.append(lines[i].strip().lstrip(">").strip())
                i += 1
            b.block("blockquote", " ".join(q for q in quote if q))
            continue
        para = [s]
        i += 1
        while i < len(lines):
            n = lines[i].strip()
            if not n or re.match(r"^(#{1,6}\s|[-*+]\s|\d+[.)]\s|>|\||```|!\[)", n) or re.fullmatch(r"(-{3,}|\*{3,}|_{3,})", n):
                break
            para.append(n)
            i += 1
        b.block("unstyled", " ".join(para))
    return {"blocks": b.blocks, "entity_map": b.entities}
