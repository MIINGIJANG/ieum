"""출처를 원본 PDF 모양 그대로 보여주기: 출처 문장이 있는 쪽에서 그 부분의 위치를 찾음.

미리 만든 자료만 사용 (scripts/build_page_assets.py) → 서버 컴퓨터에 PDF 라이브러리가 없어도 됨
- data/kosha/<문서>.layout.json : 쪽마다 단어 위치·표
- web/pages/<문서>/<쪽>.png      : 쪽 이미지 (화면이 region 부분만 잘라서 표시)
"""
import json
import re
from functools import lru_cache
from pathlib import Path

_ws = re.compile(r"\s")


class PageImages:
    def __init__(self, data_dir="data/kosha"):
        self.dir = Path(data_dir)

    @lru_cache(maxsize=8)
    def layout(self, doc):
        if not re.fullmatch(r"[\w.\-]+", doc or ""):
            return None
        f = self.dir / f"{doc}.layout.json"
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else None

    def page(self, doc, page):
        lay = self.layout(doc)
        return lay["pages"].get(str(page)) if lay else None

    @lru_cache(maxsize=64)
    def _flat(self, doc, page):
        """쪽의 글자를 공백 없이 이어붙인 문자열 + 글자마다 몇 번째 단어인지."""
        pg = self.page(doc, page)
        text, owner = [], []
        for i, w in enumerate(pg["words"]):
            for ch in _ws.sub("", w[0]):
                text.append(ch)
                owner.append(i)
        return "".join(text), owner

    def region(self, doc, page, lines, boxes=()):
        """출처 문장들(+표 bbox)이 차지하는 영역 [x0, y0, x1, y1] (pt). 못 찾으면 None."""
        pg = self.page(doc, page)
        if not pg:
            return None
        words = pg["words"]
        text, owner = self._flat(doc, page)
        top = bottom = None
        for b in boxes:
            top = b[1] if top is None else min(top, b[1])
            bottom = b[3] if bottom is None else max(bottom, b[3])
        for ln in lines:
            core = _ws.sub("", ln)
            if len(core) < 2:
                continue
            s = text.find(core[:14])
            if s < 0:
                continue
            e = text.find(core[-14:], s)
            e = (e + len(core[-14:]) - 1) if e >= 0 else s + min(len(core), 14) - 1
            ws, we = words[owner[s]], words[owner[min(e, len(owner) - 1)]]
            top = ws[2] if top is None else min(top, ws[2])
            bottom = we[4] if bottom is None else max(bottom, we[4])
        if top is None:
            return None
        xs = [(w[1], w[3]) for w in words if w[4] >= top - 2 and w[2] <= bottom + 2]
        xs += [(b[0], b[2]) for b in boxes]
        x0 = min((a for a, _ in xs), default=40)
        x1 = max((b for _, b in xs), default=pg["width"] - 40)
        pad = 6
        return [round(max(0, x0 - pad), 1), round(max(0, top - pad), 1),
                round(min(pg["width"], x1 + pad), 1), round(min(pg["height"], bottom + pad), 1)]
