"""출처를 원본 PDF 모양 그대로 보여주기: 출처 문장이 있는 쪽의 해당 부분만 잘라 이미지로.

- region(): 출처 문장들의 PDF 위치(첫 문장 시작 ~ 마지막 문장 끝)를 pdfplumber 로 찾아 [x0, y0, x1, y1] (pt)
- render(): pypdfium2 로 그 부분만 PNG 로 (서버 /source_image)
글꼴·번호·들여쓰기·표가 원본과 같으므로 원문과 비교하기 쉬움.
"""
import io
import re
from functools import lru_cache
from pathlib import Path

_ws = re.compile(r"\s")


def _pattern(chars):
    return r"\s*".join(map(re.escape, chars))


class PageImages:
    def __init__(self, pdf_dir="data/kosha", scale=2.5):
        self.dir = Path(pdf_dir)
        self.scale = scale

    def path(self, doc):
        """문서 이름 → PDF 경로 (data/kosha 안의 실제 파일만 허용)."""
        if not re.fullmatch(r"[\w.\-]+", doc or ""):
            return None
        f = self.dir / f"{doc}.pdf"
        return f if f.exists() else None

    @lru_cache(maxsize=64)
    def _plumber_page(self, doc, page):
        import pdfplumber
        f = self.path(doc)
        if not f:
            return None
        pdf = pdfplumber.open(f)
        return pdf.pages[page - 1] if 1 <= page <= len(pdf.pages) else None

    def _find(self, pg, chars, after=0.0):
        if len(chars) < 2:
            return None
        try:
            hits = [m for m in pg.search(_pattern(chars), regex=True) if m["top"] >= after - 1]
        except Exception:
            return None
        return hits[0] if hits else None

    def region(self, doc, page, lines, boxes=()):
        """boxes: 출처에 포함된 표의 bbox들 (표는 글 줄 검색 대신 표 테두리 전체를 포함)."""
        try:
            pg = self._plumber_page(doc, page)
        except Exception:
            return None
        if pg is None:
            return None
        top = bottom = None
        for b in boxes:
            top = b[1] if top is None else min(top, b[1])
            bottom = b[3] if bottom is None else max(bottom, b[3])
        for ln in lines:
            core = _ws.sub("", ln)
            a = self._find(pg, core[:14])
            if not a:
                continue
            b = self._find(pg, core[-14:], after=a["top"]) or a
            top = a["top"] if top is None else min(top, a["top"])
            bottom = b["bottom"] if bottom is None else max(bottom, b["bottom"])
        if top is None:
            return None
        # 가로: 그 높이 범위에 있는 글자·선의 좌우 끝 (표 테두리 포함)
        objs = [w for w in pg.extract_words() if w["bottom"] >= top - 2 and w["top"] <= bottom + 2]
        objs += [e for e in (pg.lines + pg.rects) if e["bottom"] >= top - 2 and e["top"] <= bottom + 2]
        x0 = min((o["x0"] for o in objs), default=40)
        x1 = max((o["x1"] for o in objs), default=pg.width - 40)
        pad = 6
        return [round(max(0, x0 - pad), 1), round(max(0, top - pad), 1),
                round(min(pg.width, x1 + pad), 1), round(min(pg.height, bottom + pad), 1)]

    @lru_cache(maxsize=16)
    def _page_bitmap(self, doc, page):
        import pypdfium2 as pdfium
        pdf = pdfium.PdfDocument(str(self.path(doc)))
        return pdf[page - 1].render(scale=self.scale).to_pil()

    def render(self, doc, page, box):
        if not self.path(doc):
            return None
        img = self._page_bitmap(doc, page)
        s = self.scale
        x0, y0, x1, y1 = box
        crop = img.crop((int(x0 * s), int(y0 * s), int(x1 * s), int(y1 * s)))
        buf = io.BytesIO()
        crop.save(buf, format="PNG", optimize=True)
        return buf.getvalue()
