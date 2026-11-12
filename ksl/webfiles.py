"""웹 화면 파일(web/) 서빙: 미리 압축해 둔 파일로 보내기 + 오프라인 저장용 파일 목록.

- 압축: 큰 파일(인식 모델·wasm·아바타·라이브러리)을 요청 때마다 압축하면 서버가 느려지므로, 한 번 압축해 임시 폴더에 두고 그대로 보냄
  (Content-Length 가 있어 내려받기 진행률도 나옴). 서버가 켜질 때 뒤에서 미리 만들어 두고, 아직 없으면 요청 때 만듦.
- 목록(/offline/manifest): 파일마다 내용 해시 → 화면(web/sw.js)이 '저장해 둔 것과 같은지'를 요청 한 번으로 확인.
  같으면 내려받지 않고 저장해 둔 것을 쓰고, 다르면 그 파일만 새로 받음.
"""
import gzip
import hashlib
import mimetypes
import os
import tempfile
import threading
from email.utils import formatdate
from pathlib import Path

from starlette.responses import FileResponse, Response
from starlette.staticfiles import NotModifiedResponse, StaticFiles

# 압축해서 보내는 확장자 (png·woff2 등 이미 압축된 것은 뺌). 모델(.onnx·.task)도 13~25% 줄어듦
COMPRESS = {".js", ".mjs", ".css", ".html", ".json", ".wasm", ".glb", ".vrm", ".onnx", ".task", ".svg", ".txt", ".md"}
MIN_SIZE = 1000
SMALL = 300_000                                # 이보다 작은 파일은 요청 때 바로 압축 (몇 ms)
# 오프라인 저장에서 빼는 것: 점검·시험 페이지, 예비 아바타, 쓰지 않는 관절 모델(Holistic), 평가 도구
SKIP_OFFLINE = ("blocks.html", "missing.html", "models/avatar.vrm", "avatar-vrm.js", "sign/index.html", "sign/eval.html", "sign/eval.js",
                "sign/models/holistic_landmarker.task", "LICENSE", ".md")

_lock = threading.Lock()
_locks = {}


def _gz_dir():
    d = Path(os.environ.get("IEUM_GZ_DIR") or Path(tempfile.gettempdir()) / "ieum-gz")
    d.mkdir(parents=True, exist_ok=True)
    return d


def gz_path(full_path, st=None, build=True):
    """압축본 경로 (없으면 만듦, build=False 면 None). 원본이 바뀌면(크기·수정 시각) 새 이름이 되어 다시 만듦."""
    full_path = Path(full_path)
    st = st or full_path.stat()
    key = hashlib.sha1(f"{full_path}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:20]
    out = _gz_dir() / f"{key}{full_path.suffix}.gz"
    if out.exists():
        return out
    if not build:
        return None
    with _lock:
        lk = _locks.setdefault(key, threading.Lock())
    with lk:                                   # 같은 파일을 동시에 두 번 압축하지 않게
        if not out.exists():
            tmp = out.with_suffix(out.suffix + f".{os.getpid()}.{threading.get_ident()}.tmp")
            with open(full_path, "rb") as src, gzip.GzipFile(tmp, "wb", compresslevel=6, mtime=0) as dst:
                while chunk := src.read(1 << 20):
                    dst.write(chunk)
            os.replace(tmp, out)
    return out


def compressible(path, size):
    return Path(path).suffix.lower() in COMPRESS and size >= MIN_SIZE


class WebFiles(StaticFiles):
    """StaticFiles + 미리 압축해 둔 파일 보내기. 모든 파일에 'no-cache'(매번 바뀌었는지만 확인, 안 바뀌었으면 304)."""

    def file_response(self, full_path, stat_result, scope, status_code=200):
        from starlette.datastructures import Headers
        req = Headers(scope=scope)
        if status_code == 200 and "gzip" in req.get("accept-encoding", "") and compressible(full_path, stat_result.st_size) \
                and "range" not in req:
            try:
                # 요청 처리 중에는 압축하지 않음(큰 파일은 몇 초 걸려 다른 요청까지 멈춤) → 아직 없으면 뒤에서 만들고 이번은 원본으로
                gz = gz_path(full_path, stat_result, build=stat_result.st_size < SMALL)
                if gz is None:
                    threading.Thread(target=gz_path, args=(full_path, stat_result), daemon=True).start()
                    raise OSError("압축본 준비 중")
                res = FileResponse(gz, status_code=status_code, stat_result=os.stat(gz),
                                   media_type=mimetypes.guess_type(str(full_path))[0] or "application/octet-stream")
                res.headers["etag"] = f'"{gz.name.split(".")[0]}"'            # 원본(크기·수정 시각) 기준 → 서버가 다시 켜져도 같음
                res.headers["last-modified"] = formatdate(stat_result.st_mtime, usegmt=True)
                res.headers["Content-Encoding"] = "gzip"
                res.headers["Vary"] = "Accept-Encoding"
                res.headers["Cache-Control"] = "no-cache"
                if self.is_not_modified(res.headers, req):
                    return NotModifiedResponse(res.headers)
                return res
            except OSError:
                pass                                   # 압축본을 못 만들면 원본 그대로
        res = super().file_response(full_path, stat_result, scope, status_code)
        if isinstance(res, Response):
            res.headers.setdefault("Cache-Control", "no-cache")
        return res


# ---------------- 오프라인 저장용 목록 ----------------
_manifest = {}


def _hash(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()[:12]


def manifest(root):
    """{version, files: {주소: 해시}, precache: [주소…]} — 한 번 만들어 두고 그대로 씀 (배포하면 서버가 새로 켜짐)."""
    root = Path(root)
    key = str(root)
    if key not in _manifest:
        with _lock:
            lk = _locks.setdefault("manifest:" + key, threading.Lock())
        with lk:
            if key not in _manifest:
                files = {}
                for p in sorted(root.rglob("*")):
                    if p.is_file() and not p.name.startswith("."):
                        files["/" + p.relative_to(root).as_posix()] = _hash(p)
                files["/"] = files.get("/index.html", "")
                pre = [u for u in files if not any(u.endswith(s) or s in u for s in SKIP_OFFLINE) and u != "/index.html"]
                ver = hashlib.sha1("".join(f"{u}{h}" for u, h in sorted(files.items())).encode()).hexdigest()[:12]
                _manifest[key] = {"version": ver, "files": files, "precache": pre}
    return _manifest[key]


def warm(root):
    """서버가 켜질 때 뒤에서: 목록 만들기 + 큰 파일 압축본 만들어 두기 (첫 방문자가 압축을 기다리지 않게)."""
    def job():
        try:
            m = manifest(root)
            for u in m["precache"]:
                p = Path(root) / u.lstrip("/")
                if p.is_file() and compressible(p, p.stat().st_size):
                    gz_path(p)
        except Exception as e:                         # 준비 실패는 서비스에 영향 없음 (요청 때 다시 만듦)
            print("[이음] 웹 파일 미리 압축 실패:", e)
    threading.Thread(target=job, daemon=True).start()
