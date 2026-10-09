"""한국어 → 수어 글로스 Transformer 학습.

python scripts/model/train.py --data data/processed/ksl_safety.jsonl --out checkpoints/run1
"""
import argparse
import json
import random
import sys
import time
from dataclasses import asdict
from pathlib import Path

import torch
import torch.nn as nn
from torch.utils.data import DataLoader, Dataset

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from ksl.config import TrainConfig  # noqa: E402
from ksl.data import gloss_durations, load_jsonl, save_json, split_records  # noqa: E402
from ksl.metrics import all_metrics  # noqa: E402
from ksl.model import Ko2Gloss  # noqa: E402
from ksl.oov import slot_augment  # noqa: E402
from ksl.tokenizer import Vocab, tokenize_ko  # noqa: E402


class PairDS(Dataset):
    def __init__(self, recs, sv, tv, cfg, train):
        self.recs, self.sv, self.tv, self.cfg, self.train = recs, sv, tv, cfg, train
        self.rng = random.Random(cfg.seed)

    def __len__(self):
        return len(self.recs)

    def __getitem__(self, i):
        r = self.recs[i]
        ko, gl = r["korean"], r["gloss"]
        if self.train and self.cfg.slot_prob > 0:
            ko, gl = slot_augment(ko, gl, self.rng, self.cfg.slot_prob)
        src = self.sv.encode(tokenize_ko(ko))[: self.cfg.max_src_len]
        tgt = [self.tv.bos] + self.tv.encode(gl)[: self.cfg.max_tgt_len - 2] + [self.tv.eos]
        return src, tgt


def collate(batch, spad, tpad):
    S = max(len(s) for s, _ in batch)
    T = max(len(t) for _, t in batch)
    src = torch.tensor([s + [spad] * (S - len(s)) for s, _ in batch])
    tgt = torch.tensor([t + [tpad] * (T - len(t)) for _, t in batch])
    return src, tgt


@torch.no_grad()
def evaluate(model, recs, sv, tv, cfg, device, bs=128):
    model.eval()
    hyps, refs = [], []
    for i in range(0, len(recs), bs):
        chunk = recs[i:i + bs]
        srcs = [sv.encode(tokenize_ko(r["korean"]))[: cfg.max_src_len] for r in chunk]
        S = max(len(s) for s in srcs)
        src = torch.tensor([s + [sv.pad] * (S - len(s)) for s in srcs], device=device)
        out = model.greedy(src, tv.bos, tv.eos, cfg.max_tgt_len)
        hyps += [tv.decode(o.tolist()) for o in out]
        refs += [r["gloss"] for r in chunk]
    model.train()
    return all_metrics(hyps, refs)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", required=True, help="전처리된 JSONL")
    ap.add_argument("--out", default="checkpoints/run1")
    ap.add_argument("--epochs", type=int)
    ap.add_argument("--batch_size", type=int)
    ap.add_argument("--d_model", type=int)
    ap.add_argument("--layers", type=int, help="encoder/decoder 층 수 동일 적용")
    ap.add_argument("--slot_prob", type=float)
    ap.add_argument("--device", default="cuda" if torch.cuda.is_available() else "cpu")
    a = ap.parse_args()

    cfg = TrainConfig()
    for k in ("epochs", "batch_size", "d_model", "slot_prob"):
        if getattr(a, k) is not None:
            setattr(cfg, k, getattr(a, k))
    if a.layers:
        cfg.enc_layers = cfg.dec_layers = a.layers
    if cfg.d_model % cfg.nhead:
        raise SystemExit("d_model 은 nhead(4)의 배수여야 함")
    random.seed(cfg.seed)
    torch.manual_seed(cfg.seed)
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)

    recs, report = load_jsonl(a.data)
    print("[data] 정제 결과:", report)
    tr, va, te = split_records(recs, seed=cfg.seed)
    print(f"[data] train {len(tr)} / val {len(va)} / test {len(te)}")
    for name, part in (("train", tr), ("val", va), ("test", te)):
        with open(out / f"{name}.jsonl", "w", encoding="utf-8") as f:
            for r in part:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")

    sv = Vocab.build([tokenize_ko(r["korean"]) for r in tr])
    tv = Vocab.build([r["gloss"] for r in tr], cfg.min_gloss_freq)
    print(f"[vocab] 한국어 글자 {len(sv)} / 글로스 {len(tv)}")
    durations = gloss_durations(tr)

    model = Ko2Gloss(len(sv), len(tv), src_pad=sv.pad, tgt_pad=tv.pad,
                     **{k: getattr(cfg, k) for k in ("d_model", "nhead", "enc_layers",
                                                     "dec_layers", "ff_dim", "dropout")}).to(a.device)
    print(f"[model] 파라미터 {sum(p.numel() for p in model.parameters()) / 1e6:.2f}M, device={a.device}")

    dl = DataLoader(PairDS(tr, sv, tv, cfg, True), batch_size=cfg.batch_size, shuffle=True,
                    collate_fn=lambda b: collate(b, sv.pad, tv.pad))
    opt = torch.optim.AdamW(model.parameters(), lr=cfg.lr, betas=(0.9, 0.98), weight_decay=0.01)
    # 데이터가 작으면 warmup 1000 step 이 전체 학습보다 길어짐 → 전체 step 의 10% 로 상한
    warm = max(1, min(cfg.warmup_steps, len(dl) * cfg.epochs // 10))
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min((s + 1) / warm, (warm / (s + 1)) ** 0.5))
    print(f"[optim] warmup {warm} steps, 총 {len(dl) * cfg.epochs} steps")
    lossf = nn.CrossEntropyLoss(ignore_index=tv.pad, label_smoothing=cfg.label_smoothing)

    best, bad, log = -1.0, 0, []
    for ep in range(1, cfg.epochs + 1):
        t0, tot, n = time.time(), 0.0, 0
        for src, tgt in dl:
            src, tgt = src.to(a.device), tgt.to(a.device)
            logits = model(src, tgt[:, :-1])
            loss = lossf(logits.reshape(-1, logits.size(-1)), tgt[:, 1:].reshape(-1))
            opt.zero_grad()
            loss.backward()
            nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            sched.step()
            tot += loss.item()
            n += 1
        m = evaluate(model, va, sv, tv, cfg, a.device)
        log.append({"epoch": ep, "loss": round(tot / n, 4), **m, "sec": round(time.time() - t0, 1)})
        print(f"ep {ep:3d} loss {tot / n:.4f} | val BLEU {m['bleu']:.2f} EM {m['exact_match']:.3f} "
              f"F1 {m['gloss_f1']:.3f} | {time.time() - t0:.1f}s")
        if m["bleu"] > best:
            best, bad = m["bleu"], 0
            torch.save({"model": model.state_dict(), "cfg": {**asdict(cfg)},
                        "src_itos": sv.itos, "tgt_itos": tv.itos, "durations": durations},
                       out / "best.pt")
        else:
            bad += 1
            if bad >= cfg.patience:
                print(f"[early stop] {cfg.patience} epoch 동안 개선 없음")
                break
    save_json(log, out / "train_log.json")
    print(f"[done] best val BLEU {best:.2f} → {out / 'best.pt'}")


if __name__ == "__main__":
    main()
