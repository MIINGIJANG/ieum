"""수어 없는 단어 기록(ksl/missing.py)을 Hugging Face 데이터셋에 보관 → Space 가 다시 켜져도 기록이 이어짐.

Hugging Face Space 는 다시 시작하면 서버 안의 파일이 지워짐. 그래서
- 서버가 켜질 때: 데이터셋(KSL_LOG_REPO, 기본 MIINGI/ieum-logs, 비공개)의 missing_glosses.json 을 받아 합치고
- 기록이 바뀌었으면 10분마다(KSL_LOG_SYNC_SEC) 데이터셋에 올림
KSL_HF_TOKEN(또는 HF_TOKEN, 쓰기 권한)이 있고 Space 위(SPACE_ID)이거나 KSL_LOG_REPO 를 직접 정했을 때만 동작. 실패해도 서버는 그대로.
"""
import json
import os
import threading
import time

FILE = "missing_glosses.json"


class LogSync:
    def __init__(self, log, repo=None, token=None, interval=None):
        self.log = log
        self.repo = repo or os.environ.get("KSL_LOG_REPO") or "MIINGI/ieum-logs"
        self.token = token or os.environ.get("KSL_HF_TOKEN") or os.environ.get("HF_TOKEN")
        self.interval = float(interval or os.environ.get("KSL_LOG_SYNC_SEC", 600))
        self.enabled = bool(log is not None and self.token and (os.environ.get("SPACE_ID") or os.environ.get("KSL_LOG_REPO")))
        self._sent = None
        self._stop = threading.Event()
        self.status = "꺼짐" if not self.enabled else "준비"

    def _api(self):
        from huggingface_hub import HfApi
        return HfApi(token=self.token)

    def pull(self):
        """데이터셋의 기록을 받아 합침."""
        from huggingface_hub import hf_hub_download
        api = self._api()
        api.create_repo(self.repo, repo_type="dataset", private=True, exist_ok=True)
        try:
            path = hf_hub_download(self.repo, FILE, repo_type="dataset", token=self.token)
        except Exception:                          # 아직 올린 적 없음
            self._sent = self.log.version
            return 0
        with open(path, encoding="utf-8") as f:
            obj = json.load(f)
        self.log.merge(obj)
        self._sent = self.log.version              # 받은 그대로는 다시 올리지 않음
        return len(obj.get("missing", {}))

    def push(self, force=False):
        """바뀐 것이 있으면 올림."""
        v = self.log.version
        if not force and v == self._sent:
            return False
        self._api().upload_file(path_or_fileobj=self.log.snapshot().encode("utf-8"), path_in_repo=FILE,
                                repo_id=self.repo, repo_type="dataset",
                                commit_message=f"수어 없는 단어 기록 {time.strftime('%Y-%m-%d %H:%M')}")
        self._sent = v
        return True

    def _loop(self):
        while not self._stop.wait(self.interval):
            try:
                if self.push():
                    self.status = f"올림 {time.strftime('%H:%M')}"
            except Exception as e:
                self.status = f"올리기 실패: {e}"[:200]

    def start(self):
        if not self.enabled:
            return
        try:
            n = self.pull()
            self.status = f"받음 (단어 {n}개)"
        except Exception as e:
            self.status = f"받기 실패: {e}"[:200]
            print("[이음] 기록 받기 실패:", e)
        threading.Thread(target=self._loop, daemon=True).start()

    def stop(self):
        """서버가 꺼질 때: 마지막으로 한 번 올림."""
        self._stop.set()
        if self.enabled:
            try:
                self.push()
            except Exception as e:
                print("[이음] 기록 올리기 실패:", e)
