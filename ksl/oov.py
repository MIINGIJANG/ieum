"""산업안전 전문용어(OOV) 처리 — 용어 슬롯 방식.

Kim et al.(LREC 2022, Avatar Layering)의 '고유명사 변환' 아이디어를 단순화한 것:
  1) 학습 때: 한국어 문장과 글로스에 동시에 나타나는 단어를 확률적으로 <T0>.. 슬롯으로 바꿔
     "슬롯은 그대로 옮겨 적는다"는 걸 모델에 가르친다.
  2) 추론 때: 용어사전(lexicon.csv)에 있는 단어를 슬롯으로 가린 뒤 번역하고,
     출력의 슬롯을 사전의 수어 글로스로 되돌린다.
→ 학습 데이터에 한 번도 없던 '방진마스크', '고소작업' 같은 용어도 사전에만 있으면 번역 가능.
"""
import csv
import random
import re

from .config import N_SLOTS, SLOTS

_SUFFIX = re.compile(r"[_\-]?\d+#?$")
# 용어 뒤에 붙어도 되는 것: 조사, '하다' 활용, 문장부호 → '보호'가 '보호구' 안에서 잘못 잡히는 것 방지
_TAIL = re.compile(r"(?:은|는|이|가|을|를|에서는|에서|에게|에는|에|와|과|도|만|의|으로|로|이나|나|부터|"
                   r"까지|처럼|보다|이다|입니다|하\S*|할\S*|한|해\S*|합\S*|되\S*|될\S*|된)?[.,!?·]*")


def norm_gloss(g):
    """'안전1', '보호_2' 처럼 동형어 번호가 붙은 글로스를 원형으로."""
    return _SUFFIX.sub("", g)


def replace_term(text, term, slot, count=0):
    """어절 맨 앞에서 시작하고 뒤에 조사/어미만 붙은 경우에만 term → slot. count=0 이면 전부."""
    words, n = text.split(" "), 0
    for i, w in enumerate(words):
        if w.startswith(term) and _TAIL.fullmatch(w[len(term):]):
            words[i] = slot + w[len(term):]
            n += 1
            if count and n >= count:
                break
    return " ".join(words), n


def slot_augment(ko, gloss, rng: random.Random, prob=0.3, max_slots=2):
    if rng.random() >= prob:
        return ko, gloss
    cands, seen = [], set()
    for i, g in enumerate(gloss):
        n = norm_gloss(g)
        if len(n) >= 2 and n not in seen and replace_term(ko, n, "", 1)[1]:
            cands.append((i, n))
            seen.add(n)
    if not cands:
        return ko, gloss
    rng.shuffle(cands)
    cands = sorted(cands[: rng.randint(1, min(max_slots, len(cands)))], key=lambda x: -len(x[1]))
    gloss = list(gloss)
    for j, (i, n) in enumerate(cands):
        ko2, hit = replace_term(ko, n, SLOTS[j], 1)
        if hit:
            ko, gloss[i] = ko2, SLOTS[j]
    return ko, gloss


class Lexicon:
    """lexicon.csv: term,gloss,source,status
    - gloss 가 채워진 행  → 번역에 사용
    - gloss 가 빈 행      → '추가 검토 필요 용어'로 탐지만 하고 리포트
    """

    def __init__(self, path=None):
        self.known, self.pending = {}, set()
        if path:
            with open(path, encoding="utf-8-sig") as f:
                for row in csv.DictReader(f):
                    t, g = (row.get("term") or "").strip(), (row.get("gloss") or "").strip()
                    if not t:
                        continue
                    if g:
                        self.known[t] = g
                    else:
                        self.pending.add(t)
        self._terms = sorted(self.known, key=len, reverse=True)

    def mask(self, text, model_glosses=frozenset()):
        """전문용어 → 슬롯.

        - 사전에 수어가 있고, 그 글로스를 모델이 이미 학습했으면 → 모델에 맡김 (가리지 않음)
        - 사전에 수어가 있지만 모델이 모르는 글로스면     → 슬롯 → 사전 글로스로 복원
        - 사전에 수어가 아직 없는 '추가검토' 용어면       → 슬롯 → 지문자(FS:용어)로 복원
          (모델이 비슷한 다른 단어를 지어내는 것 방지. 아바타는 FS: 를 지문자로 표현)
        반환: (가린 문장, {슬롯: 글로스}, {슬롯: 원래 용어}, 검토필요 용어 목록)
        """
        slot2gloss, slot2term, pending = {}, {}, []
        todo = [(t, self.known[t]) for t in self._terms
                if not all(g in model_glosses for g in self.known[t].split())]
        todo += [(t, f"FS:{t}") for t in sorted(self.pending, key=len, reverse=True)]
        for t, g in todo:
            if len(slot2gloss) >= N_SLOTS:
                break
            s = SLOTS[len(slot2gloss)]
            text, hit = replace_term(text, t, s)
            if hit:
                slot2gloss[s], slot2term[s] = g, t
                if g.startswith("FS:"):
                    pending.append(t)
        return text, slot2gloss, slot2term, pending

    @staticmethod
    def unmask(glosses, slot2gloss, slot2term):
        out, used = [], set()
        for g in glosses:
            if g in SLOTS:
                if g in slot2gloss and g not in used:
                    out.extend(slot2gloss[g].split())
                    used.add(g)
                continue          # 매핑 없는/중복 슬롯은 버림
            out.append(g)
        # 모델이 슬롯을 빠뜨린 용어는 문장 앞에 주제어로 붙임 (수어는 화제를 앞에 두는 경향)
        missing = [s for s in slot2gloss if s not in used]
        lead = [x for s in missing for x in slot2gloss[s].split()]
        return lead + out, [slot2term[s] for s in missing]
