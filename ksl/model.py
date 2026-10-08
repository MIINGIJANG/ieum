"""한국어 → 수어 글로스 Transformer (encoder-decoder)."""
import math

import torch
import torch.nn as nn


class PositionalEncoding(nn.Module):
    def __init__(self, d_model, max_len=512, dropout=0.1):
        super().__init__()
        pe = torch.zeros(max_len, d_model)
        pos = torch.arange(max_len).unsqueeze(1)
        div = torch.exp(torch.arange(0, d_model, 2) * (-math.log(10000.0) / d_model))
        pe[:, 0::2] = torch.sin(pos * div)
        pe[:, 1::2] = torch.cos(pos * div)
        self.register_buffer("pe", pe.unsqueeze(0))
        self.drop = nn.Dropout(dropout)

    def forward(self, x):
        return self.drop(x + self.pe[:, : x.size(1)])


class Ko2Gloss(nn.Module):
    def __init__(self, src_vocab, tgt_vocab, d_model=256, nhead=4, enc_layers=3,
                 dec_layers=3, ff_dim=1024, dropout=0.1, src_pad=0, tgt_pad=0, **_):
        super().__init__()
        self.d_model, self.src_pad, self.tgt_pad = d_model, src_pad, tgt_pad
        self.src_emb = nn.Embedding(src_vocab, d_model, padding_idx=src_pad)
        self.tgt_emb = nn.Embedding(tgt_vocab, d_model, padding_idx=tgt_pad)
        self.pos = PositionalEncoding(d_model, dropout=dropout)
        self.tf = nn.Transformer(d_model, nhead, enc_layers, dec_layers, ff_dim, dropout,
                                 batch_first=True, norm_first=True)
        self.out = nn.Linear(d_model, tgt_vocab)

    def encode(self, src):
        mask = src.eq(self.src_pad)
        x = self.pos(self.src_emb(src) * math.sqrt(self.d_model))
        return self.tf.encoder(x, src_key_padding_mask=mask), mask

    def decode(self, tgt_in, memory, src_mask):
        L = tgt_in.size(1)
        causal = torch.triu(torch.ones(L, L, dtype=torch.bool, device=tgt_in.device), 1)
        y = self.pos(self.tgt_emb(tgt_in) * math.sqrt(self.d_model))
        h = self.tf.decoder(y, memory, tgt_mask=causal,
                            tgt_key_padding_mask=tgt_in.eq(self.tgt_pad),
                            memory_key_padding_mask=src_mask)
        return self.out(h)

    def forward(self, src, tgt_in):
        memory, src_mask = self.encode(src)
        return self.decode(tgt_in, memory, src_mask)

    @torch.no_grad()
    def greedy(self, src, bos, eos, max_len=60):
        memory, src_mask = self.encode(src)
        ys = torch.full((src.size(0), 1), bos, dtype=torch.long, device=src.device)
        done = torch.zeros(src.size(0), dtype=torch.bool, device=src.device)
        for _ in range(max_len):
            nxt = self.decode(ys, memory, src_mask)[:, -1].argmax(-1)
            nxt = nxt.masked_fill(done, eos)
            ys = torch.cat([ys, nxt.unsqueeze(1)], 1)
            done |= nxt.eq(eos)
            if done.all():
                break
        return ys[:, 1:]

    @torch.no_grad()
    def beam(self, src, bos, eos, beam_size=4, max_len=60, alpha=0.6):
        """단일 문장(src: 1×L) 빔 서치. 길이 패널티 ((5+len)/6)^alpha."""
        memory, src_mask = self.encode(src)
        beams = [([bos], 0.0)]
        finished = []
        for _ in range(max_len):
            cand = []
            for seq, score in beams:
                y = torch.tensor([seq], device=src.device)
                logp = self.decode(y, memory, src_mask)[0, -1].log_softmax(-1)
                top = logp.topk(beam_size)
                for lp, idx in zip(top.values.tolist(), top.indices.tolist()):
                    cand.append((seq + [idx], score + lp))
            cand.sort(key=lambda x: x[1], reverse=True)
            beams = []
            for seq, score in cand:
                if seq[-1] == eos:
                    finished.append((seq, score / (((5 + len(seq)) / 6) ** alpha)))
                else:
                    beams.append((seq, score))
                if len(beams) == beam_size:
                    break
            if len(finished) >= beam_size or not beams:
                break
        if not finished:
            finished = [(s, sc / (((5 + len(s)) / 6) ** alpha)) for s, sc in beams]
        best = max(finished, key=lambda x: x[1])[0]
        return best[1:]
