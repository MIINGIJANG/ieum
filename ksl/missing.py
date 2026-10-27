"""수어 동작이 없어 회색(또는 지문자)으로 나온 단어 기록 → 다음에 어떤 수어를 추가할지 근거.

    {"글로스": {"count": 횟수, "last": "2026-10-27T08:00:00", "examples": ["그 단어가 나온 문장", ...]}}
- 파일: KSL_MISSING_LOG (기본 data/logs/missing_glosses.json). 서버가 다시 시작해도 이어서 셈
  (Hugging Face Space 는 재시작하면 파일이 지워짐 → 오래 모으려면 /missing 결과를 내려받아 두기)
- 같은 문장은 예시에 한 번만, 예시는 단어마다 최근 3개
"""
import json
import os
import threading
import time
from pathlib import Path

MAX_EXAMPLES = 3


class MissingLog:
    def __init__(self, path=None):
        self.path = Path(path or os.environ.get("KSL_MISSING_LOG", "data/logs/missing_glosses.json"))
        self._lock = threading.Lock()
        try:
            self.data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def record(self, glosses, texts=None):
        """glosses: 동작이 없는 글로스들, texts: 같은 순서로 그 글로스가 나온 문장 (없으면 None)."""
        if not glosses:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            for i, g in enumerate(glosses):
                e = self.data.setdefault(g, {"count": 0, "last": now, "examples": []})
                e["count"] += 1
                e["last"] = now
                t = texts[i] if texts and i < len(texts) else None
                if t and t not in e["examples"]:
                    e["examples"] = (e["examples"] + [t])[-MAX_EXAMPLES:]
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self.path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8")
                tmp.replace(self.path)
            except OSError:
                pass                              # 쓸 수 없는 곳(읽기 전용)이면 메모리에만

    def top(self, n=100):
        with self._lock:
            items = sorted(self.data.items(), key=lambda kv: (-kv[1]["count"], kv[0]))
        return [{"gloss": g, **v} for g, v in items[:n]]
