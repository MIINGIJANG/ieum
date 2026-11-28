"""입력 문서의 출처와 문맥을 보존하는 후보 추출."""

import io
import re

import pandas as pd
from pypdf import PdfReader

from .llm import complete

SEED_WORDS = [
    "자외선",
    "적외선",
    "방사선",
    "얼굴",
    "안면",
    "목",
    "대전",
    "정전기",
    "감전",
    "절연",
    "접지",
    "보호구",
    "안전모",
    "안전장갑",
    "보안경",
    "유해물질",
    "화학물질",
    "누전",
    "과전류",
    "화상",
]


def input_rows(name, data):
    suffix = name.rsplit(".", 1)[-1].lower()
    if suffix == "pdf":
        reader = PdfReader(io.BytesIO(data))
        return [
            {
                "word": "",
                "sense": "",
                "sentence": page.extract_text() or "",
                "source": f"{name} p.{i + 1}",
            }
            for i, page in enumerate(reader.pages)
        ]
    if suffix in ["csv", "xlsx"]:
        frame = (
            pd.read_csv(io.BytesIO(data), keep_default_na=False)
            if suffix == "csv"
            else pd.read_excel(io.BytesIO(data), keep_default_na=False)
        )
        output = []
        for d in frame.to_dict("records"):
            output.append(
                {
                    "word": str(d.get("word", d.get("단어", ""))),
                    "sense": str(d.get("sense", d.get("의미", ""))),
                    "sentence": str(d.get("sentence", d.get("문장", ""))),
                    "source": str(d.get("source", d.get("출처", name))),
                }
            )
        return output
    if suffix == "txt":
        return [
            {"word": line.strip(), "sense": "", "sentence": "", "source": name}
            for line in data.decode("utf-8-sig").splitlines()
            if line.strip()
        ]
    raise ValueError("CSV, XLSX, TXT, PDF를 선택하세요.")


def original_excerpt(text, quote):
    """공백 차이만 허용하고 반환 문장은 원문에서 직접 잘라낸다."""
    if quote in text:
        return quote
    positions = [m.start() for m in re.finditer(r"\S", text)]
    compact = "".join(text[i] for i in positions)
    target = re.sub(r"\s+", "", quote)
    start = compact.find(target) if target else -1
    if start < 0:
        return None
    return text[positions[start] : positions[start + len(target) - 1] + 1]


def candidates(con, text, source, use_llm=False, diagnostics=None):
    if not text.strip():
        return []
    if len(text) > 30000:
        raise ValueError("한 번에 30,000자 이내의 문서 구간을 입력하세요.")
    if use_llm:
        response = complete(
            con,
            "문서 핵심어 추출",
            {
                "task": "extract_terms",
                "document": text,
                "source": source,
                "output_format": {
                    "terms": [{"word": "핵심어", "sense": "문맥상 의미", "sentence": "원문 그대로"}]
                },
            },
        )
        result = response.get("terms", [])
        if not isinstance(result, list) or len(result) > 100:
            raise ValueError("핵심어 출력 형식 오류")
        valid = []
        rejected = 0
        for r in result:
            if not isinstance(r, dict) or not all(
                isinstance(r.get(k), str) and r[k].strip() for k in ["word", "sense", "sentence"]
            ):
                rejected += 1
                continue
            sentence = original_excerpt(text, r["sentence"])
            if sentence is None or re.sub(r"\s+", "", r["word"]) not in re.sub(
                r"\s+", "", sentence
            ):
                rejected += 1
                continue
            valid.append(
                {
                    **r,
                    "sentence": sentence,
                    "source": source,
                    "method": "AI 추출 후보 — 의미 확인 필요",
                }
            )
        if rejected:
            message = f"{source}: 원문에서 확인되지 않거나 형식이 잘못된 후보 {rejected}개 제외"
            if diagnostics is not None:
                diagnostics.append(message)
            elif not valid:
                raise ValueError(
                    "원문에서 확인되는 핵심어 후보가 없습니다. 문장을 바꾸어 생성한 후보는 제외했습니다."
                )
        return valid
    known = {r[0] for r in con.execute("SELECT word FROM terms")} | set(SEED_WORDS)
    output = []
    for sentence in re.split(r"(?<=[.!?。])\s+|\n+", text):
        for word in sorted(known, key=lambda x: (-len(x), x)):
            if word in sentence:
                output.append(
                    {
                        "word": word,
                        "sense": "",
                        "sentence": sentence,
                        "source": source,
                        "method": "등록어·초기 20단어 문자 대조 — AI 추출 아님",
                    }
                )
    return output
