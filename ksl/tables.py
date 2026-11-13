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
        caps = []
        if lay.exists():
            pg = json.loads(lay.read_text(encoding="utf-8"))["pages"].get(str(page))
            raw = pg["tables"] if pg else []
            caps = self._captions(doc, page, pg) if pg else []
        else:
            raw = self._from_pdf(doc, page)
        out = []
        for i, t in enumerate(raw):
            rows = t["rows"]
            text = " ".join(c for r in rows for c in r if c)
            flat = _ws.sub("", "".join(c for r in rows for c in r if c))   # 행 순서로 이어붙인 글자
            out.append({"rows": rows, "grams": _grams(text), "flat": flat, "bbox": t["bbox"], "caption": caps[i] if i < len(caps) else None})
        return out

    def _headings(self, doc, page, pg=None):
        """그 쪽의 번호 붙은 제목 줄: [(y, '7.10.1', '사용 장소에 따른 종류')] (표 안의 줄은 뺌)."""
        key = ("head", doc, page)
        if key in self._cache:
            return self._cache[key]
        if pg is None:
            import json
            lay = self.dir / f"{doc}.layout.json"
            pg = json.loads(lay.read_text(encoding="utf-8"))["pages"].get(str(page)) if lay.exists() else None
        out = []
        if pg:
            lines = []
            for w in sorted(pg.get("words") or [], key=lambda w: (round(w[2]), w[1])):
                if lines and abs(lines[-1][0] - w[2]) < 4:
                    lines[-1][1].append(w)
                else:
                    lines.append([w[2], [w]])
            boxes = [t["bbox"] for t in pg["tables"]]
            for y, ws in lines:
                if any(b[1] - 2 <= y <= b[3] for b in boxes):
                    continue
                m = re.match(r"^(\d+(?:\.\d+)+)\s+(\D.+)$", " ".join(x[0] for x in sorted(ws, key=lambda w: w[1])))
                if m:
                    out.append((y, m.group(1), m.group(2).strip()))
        self._cache[key] = out
        return out

    def _captions(self, doc, page, pg):
        """표마다 제목: 표 바로 위의 번호 붙은 제목 줄('7.4 방진마스크의 종류(등급)'). 없으면 앞 표의 제목을 이어받음
        (같은 절의 다음 표·앞 쪽에서 이어지는 표). 작은 제목('7.10.1 사용 장소에 따른 종류')은 큰 제목('7.10 눈 보호구의 종류')을 앞에 붙임."""
        heads = self._headings(doc, page, pg)

        def full(y, num, text):
            if num.count(".") < 2:
                return text
            parent = num.rsplit(".", 1)[0]
            for pno in range(page, max(page - 6, 0), -1):               # 큰 제목은 앞 쪽에 있을 수 있음
                for hy, hn, ht in reversed(self._headings(doc, pno, pg if pno == page else None)):
                    if hn == parent and (pno < page or hy < y):
                        return f"{ht}: {text}"
            return text

        out, prev = [], None
        for b in (t["bbox"] for t in pg["tables"]):
            near = [h for h in heads if h[0] < b[1] and b[1] - h[0] < 70]
            if near:
                prev = full(*near[-1])
            elif prev is None and page > 1:                       # 이 쪽 첫 표에 제목이 없으면 앞 쪽 마지막 표에서 이어지는 것
                before = self.get(doc, page - 1)
                prev = before[-1]["caption"] if before else None
            out.append(prev)
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
