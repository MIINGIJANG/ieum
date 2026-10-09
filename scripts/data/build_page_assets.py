"""출처 표시용 자료를 미리 만들어 두기 (PDF 라이브러리 없는 컴퓨터에서도 출처가 원본 모양으로 보이도록).

  python scripts/data/build_page_assets.py

data/kosha/*.pdf 마다
- web/pages/<문서>/<쪽>.png        : 쪽 이미지 (화면에서 출처 부분만 잘라 보여줌)
- data/kosha/<문서>.layout.json   : 쪽마다 단어 위치(검색용)와 표(행·열·위치)
필요: pdfplumber, pypdfium2, pillow (이 스크립트를 돌리는 컴퓨터에만)
"""
import json
import sys
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

ROOT = Path(__file__).resolve().parents[2]
SCALE = 2.5
PAPER = 242          # 원문 PDF 회색 상자(법령 인용)의 회색. 흰 바탕도 이 색으로 맞춤


def build(pdf_path):
    doc = pdf_path.stem
    out_img = ROOT / "web" / "pages" / doc
    out_img.mkdir(parents=True, exist_ok=True)
    layout = {"doc": doc, "pages": {}}
    pdf = pdfium.PdfDocument(str(pdf_path))
    with pdfplumber.open(pdf_path) as pl:
        for i, pg in enumerate(pl.pages, start=1):
            img = pdf[i - 1].render(scale=SCALE).to_pil().convert("L")      # 흑백이면 충분, 파일 작게
            img = img.point(lambda v: min(v, PAPER))                        # 흰 바탕을 원문 회색 상자와 같은 회색으로 (출처 배경 통일)
            img = img.quantize(16)                                          # 16단계 회색 → 글자 선명, 용량 작음
            img.save(out_img / f"{i}.png", optimize=True)
            words = [[w["text"], round(w["x0"], 1), round(w["top"], 1), round(w["x1"], 1), round(w["bottom"], 1)]
                     for w in pg.extract_words()]
            tables = []
            for t in pg.find_tables():
                rows = [[None if c is None else c.replace("\n", " ").strip() for c in r] for r in t.extract()]
                if len(rows) >= 2 and max(len(r) for r in rows) >= 2:      # 머리말 상자(KOSHA GUIDE) 제외
                    tables.append({"rows": rows, "bbox": [round(v, 1) for v in t.bbox]})
            layout["pages"][str(i)] = {"width": round(pg.width, 1), "height": round(pg.height, 1),
                                       "words": words, "tables": tables}
    (ROOT / "data" / "kosha" / f"{doc}.layout.json").write_text(
        json.dumps(layout, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{doc}: {len(layout['pages'])}쪽 → web/pages/{doc}/, data/kosha/{doc}.layout.json")


if __name__ == "__main__":
    pdfs = [Path(p) for p in sys.argv[1:]] or sorted((ROOT / "data" / "kosha").glob("*.pdf"))
    for p in pdfs:
        build(p)
