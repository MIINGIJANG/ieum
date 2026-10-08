"""출처 표시용: KOSHA PDF 쪽의 표를 그대로(행·열) 꺼내기.

pdfplumber 로 쪽의 표를 찾고, 출처 문장 중 표 안에 있는 줄은 표로 바꿔서 보여줌.
병합된 칸은 None (화면에서 rowspan/colspan 으로 합침).
"""
import re
from pathlib import Path

_ws = re.compile(r"\s")


def _grams(text, n=3):
    t = _ws.sub("", text or "")
    return {t[i:i + n] for i in range(len(t) - n + 1)}


class PageTables:
    def __init__(self, pdf_dir="data/kosha"):
        self.dir = Path(pdf_dir)
        self._cache = {}

    def get(self, doc, page):
        key = (doc, page)
        if key not in self._cache:
            self._cache[key] = self._load(doc, page)
        return self._cache[key]

    def _load(self, doc, page):
        """미리 만든 data/kosha/<문서>.layout.json 의 표 사용 (없으면 pdfplumber 로 직접)."""
        import json
        lay = self.dir / f"{doc}.layout.json"
        if lay.exists():
            pg = json.loads(lay.read_text(encoding="utf-8"))["pages"].get(str(page))
            raw = pg["tables"] if pg else []
        else:
            raw = self._from_pdf(doc, page)
        out = []
        for t in raw:
            rows = t["rows"]
            text = " ".join(c for r in rows for c in r if c)
            flat = _ws.sub("", "".join(c for r in rows for c in r if c))   # 행 순서로 이어붙인 글자
            out.append({"rows": rows, "grams": _grams(text), "flat": flat, "bbox": t["bbox"]})
        return out

    def _from_pdf(self, doc, page):
        f = self.dir / f"{doc}.pdf"
        if not f.exists():
            return []
        try:
            import pdfplumber
            with pdfplumber.open(f) as pdf:
                if not 1 <= page <= len(pdf.pages):
                    return []
                out = []
                for t in pdf.pages[page - 1].find_tables():
                    rows = [[None if c is None else c.replace("\n", " ").strip() for c in r] for r in t.extract()]
                    if len(rows) >= 2 and max(len(r) for r in rows) >= 2:
                        out.append({"rows": rows, "bbox": list(t.bbox)})
                return out
        except Exception:
            return []

    def blocks(self, doc, page, lines):
        """출처 줄 목록 → [{'type':'text','text'} | {'type':'table','rows'}] (표 안의 줄은 표 하나로)."""
        tables = self.get(doc, page)
        out, used = [], set()
        for ln in lines:
            g = _grams(ln)
            hit = next((i for i, t in enumerate(tables) if len(_ws.sub("", ln)) >= 2 and _ws.sub("", ln) in t["flat"]), None)
            if hit is None and len(g) >= 3:
                best = max(range(len(tables)), key=lambda i: len(g & tables[i]["grams"]), default=None)
                if best is not None and len(g & tables[best]["grams"]) / len(g) >= 0.6:
                    hit = best
            if hit is None:
                out.append({"type": "text", "text": ln})
            elif hit not in used:
                used.add(hit)
                out.append({"type": "table", "rows": tables[hit]["rows"], "bbox": tables[hit]["bbox"]})
        return out
