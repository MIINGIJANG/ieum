"""작업별 전처리 엔진 구성과 추출 좌표 재사용."""

import ast
import json
import math
import shutil
from pathlib import Path

from .service import sha256


def prepare_engine(pipeline_dir, job, hand_max_gap_sec):
    seconds = float(hand_max_gap_sec)
    if not math.isfinite(seconds) or not 0 <= seconds <= 1:
        raise ValueError("양손 보간 한도는 0~1초여야 합니다.")
    root = Path(pipeline_dir)
    source = root / "src/pipeline.py"
    if not source.is_file():
        raise ValueError("전처리 경로에 src/pipeline.py가 필요합니다.")
    engine = Path(job) / "engine"
    engine.mkdir()
    shutil.copy2(root / "run.py", engine / "run.py")
    shutil.copytree(root / "src", engine / "src", ignore=shutil.ignore_patterns("__pycache__"))
    if (root / "tools").is_dir():
        shutil.copytree(
            root / "tools", engine / "tools", ignore=shutil.ignore_patterns("__pycache__")
        )
    mentor = Path(__file__).resolve().parents[1] / "tools/postprocess_linear.py"
    shutil.copy2(mentor, engine / "src/postprocess_v3_fixed.py")
    path = engine / "src/pipeline.py"
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    controls = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.If) or not isinstance(node.test, ast.UnaryOp):
            continue
        test = node.test
        if not isinstance(test.op, ast.Not) or not isinstance(test.operand, ast.Subscript):
            continue
        access = test.operand
        if (
            isinstance(access.value, ast.Attribute)
            and access.value.attr == "settings"
            and isinstance(access.value.value, ast.Name)
            and access.value.value.id == "self"
            and isinstance(access.slice, ast.Constant)
            and access.slice.value == "make_check_video"
        ):
            controls.append(node)
    if len(controls) != 1:
        raise ValueError("전처리 실행 구조를 확인할 수 없습니다.")
    control = controls[0]
    lines = text.splitlines(keepends=True)
    indent = lines[control.lineno - 1][: control.col_offset]
    lines.insert(
        control.lineno - 1,
        indent + "cmd.extend(['--hand-max-gap-sec', str(self.settings['hand_max_gap_sec'])])\n",
    )
    text = "".join(lines)
    path.write_text(text, encoding="utf-8")
    return engine, sha256(mentor)


def reuse_extracted(old_job, new_job):
    """검출 좌표를 다시 추출하지 않고 새 작업에서 후처리한다."""
    old = Path(old_job).resolve() / "results/collection_job"
    target = Path(new_job) / "results/collection_job"
    data = json.loads((old / "pipeline.json").read_text(encoding="utf-8"))
    items = data.get("items", {})
    if not items:
        raise ValueError("재사용할 추출 결과가 없습니다.")
    for row in items.values():
        for field in ["raw", "normalized_video", "source"]:
            path = (old / row[field]).resolve()
            if not path.is_relative_to(old) or not path.is_file():
                raise ValueError("원본 영상 또는 추출 좌표가 없어 후처리만 재실행할 수 없습니다.")
        if sha256(old / row["source"]) != row["source_sha256"]:
            raise ValueError("원본 영상이 바뀌었습니다. 새 추출 작업이 필요합니다.")
    target.mkdir(parents=True)
    for row in items.values():
        for field in ["raw", "source"]:
            relative = Path(row[field]).parent
            shutil.copytree(old / relative, target / relative, dirs_exist_ok=True)
        row["stage"] = "extracted"
        row["approval"] = None
        row.pop("audit", None)
        row.pop("avatar_sha256", None)
        row.pop("error", None)
        row.setdefault("history", []).append({"action": "reprocess_bilateral_interpolation"})
    data.pop("signature", None)
    (target / "pipeline.json").write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )
