"""수어 동작이 없어 회색(또는 지문자)으로 나온 단어 기록 → 다음에 어떤 수어를 추가할지 근거.

    {"missing": {"글로스": {"count": 횟수, "last": "2026-10-27T08:00:00", "examples": ["그 단어가 나온 문장", ...]}},
     "substitutes": {"글로스": {"used": ["대신 보여준 수어", ...], "count", "last", "examples"}}}   ← 대체 표현으로 보여준 단어 (검수용)
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
            obj = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            obj = {}
        if isinstance(obj.get("missing"), dict):
            self.data, self.subs = obj["missing"], obj.get("substitutes", {})
        else:                                     # 예전 형식: 글로스 → 기록
            self.data, self.subs = obj, {}

    @staticmethod
    def _bump(store, g, now, text, extra=None):
        e = store.setdefault(g, {"count": 0, "last": now, "examples": []})
        e["count"] += 1
        e["last"] = now
        if extra:
            e.update(extra)
        if text and text not in e["examples"]:
            e["examples"] = (e["examples"] + [text])[-MAX_EXAMPLES:]

    def record(self, glosses, texts=None):
        """glosses: 동작이 없는 글로스들, texts: 같은 순서로 그 글로스가 나온 문장 (없으면 None)."""
        if not glosses:
            return
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            for i, g in enumerate(glosses):
                self._bump(self.data, g, now, texts[i] if texts and i < len(texts) else None)
            self._save()

    def record_sub(self, gloss, used, text=None):
        """대체 표현으로 보여준 단어: gloss 대신 used(글로스 목록)를 보여줌."""
        now = time.strftime("%Y-%m-%dT%H:%M:%S")
        with self._lock:
            self._bump(self.subs, gloss, now, text, {"used": list(used)})
            self._save()

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"missing": self.data, "substitutes": self.subs}, ensure_ascii=False, indent=1), encoding="utf-8")
            tmp.replace(self.path)
        except OSError:
            pass                                  # 쓸 수 없는 곳(읽기 전용)이면 메모리에만

    def top(self, n=100, subs=False):
        with self._lock:
            items = sorted((self.subs if subs else self.data).items(), key=lambda kv: (-kv[1]["count"], kv[0]))
        return [{"gloss": g, **v} for g, v in items[:n]]
