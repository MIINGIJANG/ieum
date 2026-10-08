"""학습된 모델로 한국어 문장 → 수어 글로스(+아바타용 동작 시간) 변환."""
import re

from .oov import Lexicon, norm_gloss
from .tokenizer import Vocab, tokenize_ko

# 한국어 조사·어미 (사전 치환 베이스라인용, 단순 규칙)
_JOSA = re.compile(r"(으로써|으로서|에서는|에게서|으로|에서|에게|까지|부터|보다|처럼|이나|하고|"
                   r"은|는|이|가|을|를|에|와|과|도|만|의|로|나)$")
_ENDING = re.compile(r"(해야\s?합니다|해야\s?한다|합니다|하세요|십시오|습니다|ㅂ니다|한다|하고|하여|해서|해야|할|한|하는|하다|되어|된|될|됩니다)$")


def split_sentences(text):
    parts = re.split(r"(?<=[.!?。])\s+|(?<=다\.)|\n+", text.strip())
    return [p.strip() for p in parts if p and p.strip()]


class Translator:
    def __init__(self, ckpt_path, lexicon_path=None, device=None):
        import torch                                   # 무거운 라이브러리라 실제로 번역할 때만 불러옴 (서버 시작 빠르게)
        from .model import Ko2Gloss
        self.torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        ck = torch.load(ckpt_path, map_location=self.device, weights_only=True)
        self.cfg = ck["cfg"]
        self.src = Vocab(ck["src_itos"])
        self.tgt = Vocab(ck["tgt_itos"])
        self.durations = ck.get("durations", {"__default__": 0.8})
        self.model = Ko2Gloss(len(self.src), len(self.tgt), src_pad=self.src.pad,
                              tgt_pad=self.tgt.pad, **self.cfg).to(self.device)
        self.model.load_state_dict(ck["model"])
        self.model.eval()
        self.lexicon = Lexicon(lexicon_path)
        self._known = frozenset(self.tgt.itos)

    def _raw(self, text, beam=4):
        ids = self.src.encode(tokenize_ko(text))[: self.cfg["max_src_len"]]
        src = self.torch.tensor([ids], device=self.device)
        if beam and beam > 1:
            out = self.model.beam(src, self.tgt.bos, self.tgt.eos, beam, self.cfg["max_tgt_len"])
        else:
            out = self.model.greedy(src, self.tgt.bos, self.tgt.eos, self.cfg["max_tgt_len"])[0].tolist()
        return self.tgt.decode(out)

    def translate(self, text, beam=4, use_lexicon=True):
        masked, s2g, s2t, pending = (self.lexicon.mask(text, self._known) if use_lexicon
                                     else (text, {}, {}, []))
        raw = self._raw(masked, beam)
        glosses, missing = Lexicon.unmask(raw, s2g, s2t)
        glosses = [g for g in glosses if g != "<unk>"] if glosses else glosses
        default = self.durations.get("__default__", 0.8)
        timeline, t = [], 0.0
        for g in glosses:
            d = self.durations.get(g, self.durations.get(norm_gloss(g), default))
            timeline.append({"gloss": g, "start": round(t, 3), "duration": d})
            t += d
        return {
            "korean": text,
            "gloss": glosses,
            "timeline": timeline,                         # 3D 아바타 재생 순서/길이
            "lexicon_terms": [t for t in s2t.values() if t not in pending],  # 사전 글로스로 치환된 용어
            "dropped_terms": missing,                     # 모델이 슬롯을 빠뜨려 앞에 붙인 용어 (품질 점검용)
            "unverified_terms": pending,                  # 수어 표현 미확보 → 지문자(FS:)로 출력, 검토 필요
            "has_unk": "<unk>" in raw,
        }


class DictBaseline:
    """'형태소 단위로 단순 치환' 베이스라인.
    어절에서 조사/어미를 떼고, 학습 글로스 어휘에 있으면 그대로 출력(한국어 어순 유지).
    → 계획서의 '단순 치환보다 글로스 구조 기반 모델이 낫다'는 주장을 수치로 확인하기 위한 비교군.
    """

    def __init__(self, gloss_vocab, lexicon: Lexicon = None):
        self.vocab = {norm_gloss(g): g for g in gloss_vocab}
        self.lexicon = lexicon or Lexicon()

    def translate(self, text):
        out = []
        for w in text.replace(".", " ").split():
            if w in self.lexicon.known:
                out.extend(self.lexicon.known[w].split())
                continue
            stem = _JOSA.sub("", w)
            stem2 = _ENDING.sub("", stem) or stem
            for s in (w, stem, stem2):
                if s in self.vocab:
                    out.append(self.vocab[s])
                    break
        return out
