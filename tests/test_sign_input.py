"""카메라 수어 입력 시험판 (web/sign/): 파일이 다 있고, 모델 규격·산업안전 단어 목록이 맞는지."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGN = ROOT / "web/sign"


def test_sign_input_files():
    for f in ["index.html", "app.js", "core.js", "engines.js", "landmark-worker.js", "eval.html", "eval.js", "custom_words.json",
              "domain.json", "models/model.onnx", "models/meta.json", "models/holistic_landmarker.task",
              "models/pose_landmarker_lite.task", "models/hand_landmarker.task",
              "lib/mediapipe/vision_bundle.mjs", "lib/mediapipe/vision_wasm_internal.wasm", "lib/ort/ort.wasm.min.mjs",
              "lib/ort/ort-wasm-simd-threaded.wasm"]:
        assert (SIGN / f).exists(), f
    meta = json.loads((SIGN / "models/meta.json").read_text(encoding="utf-8"))
    assert meta["feature_dim"] == 155 and meta["seq_len"] == 32 and len(meta["labels"]) == meta["num_classes"]
    app = (SIGN / "core.js").read_text(encoding="utf-8")
    assert "const SEQ_LEN = 32" in app and "POSE_KEYS = [0, 11, 12, 13, 14, 15, 16, 23, 24]" in app   # 팀원 landmarks.ts 와 같은 특징
    d = json.loads((SIGN / "domain.json").read_text(encoding="utf-8"))
    assert set(d["labels"]) <= set(meta["labels"]) and len(d["labels"]) > 100
    assert {"as": "안전모", "words": ["머리", "보호"]} in d["combos"] and d["to_doc"].get("입다") == "착용"
    assert len(d["questions"]) >= 10
