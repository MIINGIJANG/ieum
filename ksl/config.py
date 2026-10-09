"""프로젝트 전역 설정.

팀 JSONL의 실제 키 이름이 다르면 FIELDS만 고치면 된다.
(scripts/data/inspect_jsonl.py 를 돌리면 어떤 키가 있는지 바로 보인다.)
"""
from dataclasses import dataclass, field

# ---- 전처리 JSONL 필드 이름 매핑 ----------------------------------------
FIELDS = {
    "korean": "korean",      # 한국어 원문 (str)
    "gloss": "gloss",        # 수어 글로스: "작업장 안전모 착용" (str) 또는 ["작업장", ...] 또는 [{"gloss":..,"start":..,"end":..}, ...]
    "start": "start",        # 문장 수어 동작 시작 시간(초) - 없으면 무시
    "end": "end",            # 문장 수어 동작 종료 시간(초) - 없으면 무시
    "nms": "nms",            # 비수지 표현 - 현재 번역 모델 입력/출력에는 쓰지 않고 통과만 함
    "source": "source",      # 데이터 출처 위치
}

# 글로스 dict 안의 키 이름 (gloss 가 dict 리스트일 때)
GLOSS_ITEM_KEYS = {"gloss": "gloss", "start": "start", "end": "end"}

# ---- 특수 토큰 ----------------------------------------------------------
PAD, BOS, EOS, UNK, SPACE = "<pad>", "<s>", "</s>", "<unk>", "▁"
N_SLOTS = 5                                   # OOV 용어 슬롯 <T0>~<T4>
SLOTS = [f"<T{i}>" for i in range(N_SLOTS)]
SPECIALS = [PAD, BOS, EOS, UNK] + SLOTS


@dataclass
class TrainConfig:
    d_model: int = 256
    nhead: int = 4
    enc_layers: int = 3
    dec_layers: int = 3
    ff_dim: int = 1024
    dropout: float = 0.1
    max_src_len: int = 200      # 한국어 글자 수 기준
    max_tgt_len: int = 60       # 글로스 개수 기준
    batch_size: int = 64
    lr: float = 5e-4
    warmup_steps: int = 1000
    epochs: int = 40
    patience: int = 6           # val BLEU 개선 없으면 조기 종료
    label_smoothing: float = 0.1
    slot_prob: float = 0.3      # 학습 시 용어 슬롯 치환 확률 (OOV 대응 증강)
    min_gloss_freq: int = 1     # 이 빈도 미만 글로스는 <unk>
    seed: int = 42
    extra: dict = field(default_factory=dict)
