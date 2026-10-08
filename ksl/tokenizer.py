"""토크나이저.

- 한국어(입력): 음절(글자) 단위 + 띄어쓰기 토큰 ▁
  → 입력 쪽 OOV가 사실상 없어지고, 조사·어미 변화도 모델이 직접 학습.
- 수어(출력): '글로스 단위' 토큰 (형태소/서브워드로 쪼개지 않음)
  → SSL 벤치마크(LREC-COLING 2024)에서 글로스 토큰화가 음성언어식/통계식보다 좋았던 결과를 반영.
- 슬롯 토큰 <T0>~<T4> 는 양쪽 모두 한 토큰으로 취급 (OOV 용어 치환용).
"""
import json
import re
from collections import Counter

from .config import BOS, EOS, PAD, SLOTS, SPACE, SPECIALS, UNK

_SLOT_RE = re.compile(r"(<T\d>)")


class Vocab:
    def __init__(self, itos):
        self.itos = list(itos)
        self.stoi = {t: i for i, t in enumerate(self.itos)}
        self.pad, self.bos, self.eos, self.unk = (self.stoi[t] for t in (PAD, BOS, EOS, UNK))

    def __len__(self):
        return len(self.itos)

    def encode(self, toks):
        return [self.stoi.get(t, self.unk) for t in toks]

    def decode(self, ids):
        out = []
        for i in ids:
            if i == self.eos:
                break
            if i in (self.pad, self.bos):
                continue
            out.append(self.itos[i])
        return out

    def save(self, path):
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.itos, f, ensure_ascii=False)

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(json.load(f))

    @classmethod
    def build(cls, seqs, min_freq=1):
        c = Counter(t for s in seqs for t in s)
        toks = sorted([t for t, n in c.items() if n >= min_freq and t not in SPECIALS],
                      key=lambda t: (-c[t], t))
        return cls(SPECIALS + toks)


def tokenize_ko(text):
    """'작업장에서 <T0>를 착용' → ['작','업','장','에','서','▁','<T0>','를','▁','착','용']"""
    toks = []
    for part in _SLOT_RE.split(text.strip()):
        if not part:
            continue
        if part in SLOTS:
            toks.append(part)
            continue
        for ch in part:
            if ch.isspace():
                if toks and toks[-1] != SPACE:
                    toks.append(SPACE)
            else:
                toks.append(ch)
    return toks
