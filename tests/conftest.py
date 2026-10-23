"""공통 준비: KOSHA 인덱스는 PDF 로 한 번만 새로 만들고(서버와 같은 max_chars=300), 파이프라인을 공유.
번역 모델(torch)은 쓰지 않음 — 질문 답변·답변 거절·수어 어순·동작만 확인."""
import os
import sys
import warnings
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
warnings.filterwarnings("ignore")


@pytest.fixture(scope="session")
def index_path(tmp_path_factory):
    from ksl.rag import SafetyRAG
    p = tmp_path_factory.mktemp("index") / "kosha_index.pkl"
    SafetyRAG.build([str(ROOT / "data/kosha/A-G-12-2026.pdf")], max_chars=300).save(p)
    return p


@pytest.fixture(scope="session")
def rag(index_path):
    from ksl.rag import SafetyRAG
    return SafetyRAG.load(index_path)


@pytest.fixture(scope="session")
def pipe(index_path):
    from ksl.pipeline import SafetySignPipeline
    os.chdir(ROOT)
    return SafetySignPipeline("no-model.pt", str(index_path), "data/sign/lexicon.csv", "data/sign/motions",
                              "data/sign/demo_sentences.json")
