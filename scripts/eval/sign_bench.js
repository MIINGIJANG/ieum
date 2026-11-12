// sign_bench.py 가 부름: node sign_bench.js <녹화 폴더> '<설정 JSON>' [서버 주소]
// 변형별 표 (실제 web/sign/core.js 를 브라우저에서 그대로 실행). session: 낱말 몇 개를 한 사람이 이어서 한 것으로 볼지
const { chromium } = require('playwright');
const fs = require('fs');
const path = require('path');
const DIR = process.argv[2], CONFIGS = JSON.parse(process.argv[3] || '{"base":{}}'), BASE = process.argv[4] || 'http://localhost:8000';
(async () => {
  const b = await chromium.launch();
  const pg = await b.newPage();
  pg.on('pageerror', e => console.log('ERR', e.message));
  await pg.route('**/__bench_*.json', r => r.fulfill({ contentType: 'application/json', body: fs.readFileSync(path.join(DIR, decodeURIComponent(r.request().url().split('__bench_')[1]))) }));
  const VARS = fs.readdirSync(DIR).filter((f) => f.endsWith('.json')).map((f) => f.slice(0, -5));
  await pg.goto(BASE + '/sign/eval.html');
  const out = await pg.evaluate(async ([CONFIGS, VARS]) => {
    const C = await import('./core.js');
    const rec = new C.Recognizer(); await rec.load('./');
    const domain = await (await fetch('./domain.json')).json();
    const s = new Set(domain.labels);
    const domainIdx = rec.labels.map((l, i) => (s.has(l) ? i : -1)).filter((i) => i >= 0);
    const word = (l) => domain.words[l] || C.base(l);
    const toFrame = (f) => ({ pose: f.pose.map(([x, y, z, v]) => ({ x, y, z, visibility: v })), leftHand: f.lh ? f.lh.map(([x, y, z]) => ({ x, y, z })) : [], rightHand: f.rh ? f.rh.map(([x, y, z]) => ({ x, y, z })) : [] });
    const res = {};
    for (const name of Object.keys(CONFIGS)) res[name] = { agg: {}, ms: 0 };
    for (const V of VARS) { const recs = await (await fetch('/__bench_' + V + '.json')).json();
    for (const [name, over] of Object.entries(CONFIGS)) {
      const o = { ...C.DEFAULTS, ...over }, agg = res[name].agg;
      const t0 = performance.now();
      // 한 사람이 낱말 4개를 이어서 하는 것으로 보고(같은 변형 안에서 섞어 4개씩) 왼손잡이 판단을 이어 감
      let hand = null, k = 0;
      const order = recs.map((r, i) => [((i * 2654435761) >>> 0) % 9973, r]).sort((x, y) => x[0] - y[0]).map((x) => x[1]);
      for (const r of order) {
        if (o.session && k++ % (o.session) === 0) hand = new C.Handedness(o.hand || {});
        const cleaner = new C.FrameCleaner(o.cleaner || {}), seg = new C.Segmenter(o.seg || {}), segs = [];
        for (const f of r.frames) {
          const raw = toFrame(f), frame = o.smooth ? cleaner.clean(raw, f.t) : raw, feat = C.frameToFeatures(frame, o.useZ);
          const sg = seg.push(frame, feat, f.t); if (sg) segs.push(sg.frames);
        }
        if (seg.cur && seg.cur.frames.length >= 4) segs.push(seg.cur.frames);
        const outs = [];
        for (const fr of segs) outs.push(await C.classify(rec, fr, o, domainIdx, [], o.session ? hand : null));
        const a = (agg[r.variant] ||= { n: 0, found: 0, hit1: 0, hit5: 0, extra: 0, conf: 0, segs: 0, mir: 0 });
        a.n++; a.segs += segs.length; if (outs.some((x) => x.mirrored)) a.mir++; if (segs.length) a.found++;
        const tops = [...new Set(outs.map((x) => word(x.label)))];
        if (tops.includes(r.word)) { a.hit1++; if (outs.some((x) => word(x.label) === r.word && x.confident)) a.conf++; }
        if (outs.some((x) => x.cands.some((c) => word(c.label) === r.word))) a.hit5++;
        a.extra += tops.filter((w) => w !== r.word).length;
      }
      res[name].ms += Math.round(performance.now() - t0);
    } }
    return res;
  }, [CONFIGS, VARS]);
  const variants = Object.keys(Object.values(out)[0].agg);
  const pct = (a, k) => String(Math.round(100 * a[k] / a.n)).padStart(3);
  for (const [name, { agg, ms }] of Object.entries(out)) {
    let tot = { n: 0, hit1: 0, hit5: 0, extra: 0, found: 0, conf: 0 };
    console.log(`\n== ${name} (${ms}ms)`);
    console.log('variant   found hit1 hit5 conf extra/rec segs/rec mir%');
    for (const v of variants) { const a = agg[v]; for (const k in tot) tot[k] += a[k];
      console.log(v.padEnd(9), pct(a, 'found'), ' ', pct(a, 'hit1'), pct(a, 'hit5'), pct(a, 'conf'), '  ', (a.extra / a.n).toFixed(2), '   ', (a.segs / a.n).toFixed(2), '  ', pct(a, 'mir')); }
    console.log('ALL      ', pct(tot, 'found'), ' ', pct(tot, 'hit1'), pct(tot, 'hit5'), pct(tot, 'conf'), '  ', (tot.extra / tot.n).toFixed(2));
  }
  await b.close();
})();
