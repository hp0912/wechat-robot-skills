"""Tracked replacements for contiguous plain-text runs, retaining each run's style."""

from __future__ import annotations

import re
import zipfile
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from _docx_common import W_NS, parse_xml_bytes, qn


class TrackedReplacement:
    def __init__(self, source: Path, author: str):
        self.author = author.strip()
        if not self.author:
            raise ValueError("修订作者不能为空")
        self.date = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        self.next_id = 0
        self.count = 0
        with zipfile.ZipFile(source) as archive:
            for name in archive.namelist():
                if name.startswith("word/") and name.endswith(".xml"):
                    root = parse_xml_bytes(archive.read(name), label=name)
                    for element in root.iter():
                        value = element.get(qn("id"), "")
                        if value.isdecimal():
                            self.next_id = max(self.next_id, int(value) + 1)

    def _revision(self, name: str) -> Any:
        from docx.oxml import OxmlElement

        element = OxmlElement(f"w:{name}")
        element.set(qn("id"), str(self.next_id))
        element.set(qn("author"), self.author)
        element.set(qn("date"), self.date)
        self.next_id += 1
        return element

    @staticmethod
    def _fragment(run: Any, paragraph: Any, text: str, *, deleted: bool = False) -> Any:
        from docx.text.run import Run

        element = deepcopy(run._r)
        Run(element, paragraph).text = text
        if deleted:
            for node in element.findall(qn("t")):
                node.tag = qn("delText")
        return element

    def replace(self, paragraph: Any, matches: list[re.Match[str]], replacement: str) -> None:
        if any(char in replacement for char in "\n\r\t"):
            raise ValueError("修订替换仅支持段落内文本；换行或制表符需要单独编辑")
        # Edits run backwards. Earlier offsets remain valid in the remaining plain runs.
        for match in reversed(matches):
            start, end = match.span()
            runs = paragraph.runs
            spans = []
            offset = 0
            for run in runs:
                spans.append((offset, offset + len(run.text)))
                offset += len(run.text)
            first = next(i for i, (_, b) in enumerate(spans) if start < b)
            last = next(i for i, (a, b) in enumerate(spans) if a < end <= b)
            selected = runs[first:last + 1]
            children = list(paragraph._p)
            left = children.index(selected[0]._r)
            right = children.index(selected[-1]._r)
            if children[left:right + 1] != [run._r for run in selected]:
                raise ValueError("修订目标跨越书签、批注或其他非文本节点，请缩小替换范围")
            if any(child.tag not in {qn("rPr"), qn("t")} for run in selected for child in run._r):
                raise ValueError("修订目标包含域、图片、换行或其他复杂节点，请使用精确批注或缩小范围")
            prefix = selected[0].text[:start - spans[first][0]]
            suffix = selected[-1].text[end - spans[last][0]:]
            nodes = []
            if prefix:
                nodes.append(self._fragment(selected[0], paragraph, prefix))
            deletion = self._revision("del")
            for i in range(first, last + 1):
                a, b = spans[i]
                text = runs[i].text[max(start - a, 0):min(end, b) - a]
                if text:
                    deletion.append(self._fragment(runs[i], paragraph, text, deleted=True))
            nodes.append(deletion)
            if replacement:
                insertion = self._revision("ins")
                insertion.append(self._fragment(selected[0], paragraph, replacement))
                nodes.append(insertion)
            if suffix:
                nodes.append(self._fragment(selected[-1], paragraph, suffix))
            for run in selected:
                paragraph._p.remove(run._r)
            for index, node in enumerate(nodes, left):
                paragraph._p.insert(index, node)
            self.count += 1
