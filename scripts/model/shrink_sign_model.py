"""카메라 수어 입력의 낱말 인식 모델(web/sign/models/model.onnx) 용량 줄이기: 26MB → 14MB.

python scripts/model/shrink_sign_model.py [입력.onnx] [출력.onnx]      # 기본: web/sign/models/model.onnx 를 그 자리에서 바꿈

큰 가중치를 16비트 실수로 저장하고, 모델 안에서 바로 32비트로 되돌리는(Cast) 단계를 붙임.
실행은 그대로 32비트라 브라우저(ONNX Runtime Web)에서 속도·호환성이 같고, 내려받는 크기만 절반.
값이 16비트로 반올림되므로 결과가 아주 조금 달라짐 → 끝에 원본과 비교해 1위 낱말이 같은지 확인 (다르면 실패).
이미 줄인 모델이면 아무것도 하지 않음.
"""
import sys
from pathlib import Path

import numpy as np
import onnx
from onnx import TensorProto, helper, numpy_helper

ROOT = Path(__file__).resolve().parents[2]
MIN_SIZE = 1024          # 이보다 작은 가중치는 그대로 (줄여도 이득이 없음)


def shrink(model):
    inits, casts, n = [], [], 0
    for init in model.graph.initializer:
        arr = numpy_helper.to_array(init)
        if init.data_type == TensorProto.FLOAT and arr.size >= MIN_SIZE:
            inits.append(numpy_helper.from_array(arr.astype(np.float16), init.name + "_f16"))
            casts.append(helper.make_node("Cast", [init.name + "_f16"], [init.name], to=TensorProto.FLOAT, name="f16_" + init.name))
            n += 1
        else:
            inits.append(init)
    nodes = list(model.graph.node)
    del model.graph.initializer[:], model.graph.node[:]
    model.graph.initializer.extend(inits)
    model.graph.node.extend(casts + nodes)
    return n


def compare(a_path, b_path, trials=200):
    """무작위 입력으로 원본과 비교: 1위 낱말이 같은 비율, 로짓 최대 차이"""
    import onnxruntime as ort
    a = ort.InferenceSession(str(a_path), providers=["CPUExecutionProvider"])
    b = ort.InferenceSession(str(b_path), providers=["CPUExecutionProvider"])
    name = a.get_inputs()[0].name
    shape = [1 if not isinstance(d, int) else d for d in a.get_inputs()[0].shape]
    rng, same, diff = np.random.default_rng(0), 0, 0.0
    for _ in range(trials):
        x = (rng.standard_normal(shape) * 0.5).astype(np.float32)
        x[..., -2:] = rng.random(x[..., -2:].shape) > 0.3          # 손 있음 표시
        la, lb = a.run(None, {name: x})[0][0], b.run(None, {name: x})[0][0]
        same += int(la.argmax() == lb.argmax())
        diff = max(diff, float(np.abs(la - lb).max()))
    return same / trials, diff


def main():
    src = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "web/sign/models/model.onnx"
    dst = Path(sys.argv[2]) if len(sys.argv) > 2 else src
    model = onnx.load(str(src))
    if any(i.name.endswith("_f16") for i in model.graph.initializer):
        print("이미 줄인 모델입니다:", src)
        return
    n = shrink(model)
    tmp = dst.with_suffix(".f16.tmp.onnx")
    onnx.save(model, str(tmp))
    onnx.checker.check_model(str(tmp))
    same, diff = compare(src, tmp)
    print(f"가중치 {n}개를 16비트로: {src.stat().st_size / 1e6:.1f}MB → {tmp.stat().st_size / 1e6:.1f}MB, 1위 낱말 일치 {same:.1%}, 로짓 최대 차이 {diff:.3f}")
    if same < 0.995:
        tmp.unlink()
        sys.exit("원본과 결과가 달라 바꾸지 않았습니다.")
    tmp.replace(dst)
    print("저장:", dst)


if __name__ == "__main__":
    main()
