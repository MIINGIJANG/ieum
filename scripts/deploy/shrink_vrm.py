"""아바타(VRM) 용량 줄이기: 텍스처를 화면에 필요한 크기로 줄이고, 화면에서 숨기는 부분(머리카락·바지·썸네일)은 아주 작게.
  python scripts/deploy/shrink_vrm.py 원본.vrm web/models/avatar.vrm
휴대폰 첫 로딩이 이 파일 내려받기에 가장 오래 걸림 (13MB → 약 4MB)
"""
import io
import json
import struct
import sys

from PIL import Image

# 이름에 들어간 말 → 최대 변 길이(px)
RULES = [("Thumbnail", 64), ("Bottoms", 16), ("Hair", 16), ("Shoes", 64),   # 숨김·안 보임
         ("_nml", 512),                                                     # 노멀맵
         ("Body_00", 1024), ("Tops", 1024)]


def target(name):
    for key, size in RULES:
        if key in name:
            return size
    return None


def shrink(src, dst):
    b = open(src, "rb").read()
    jl = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jl])
    bin_off = 20 + jl + 8
    binary = b[bin_off:bin_off + struct.unpack("<I", b[20 + jl:24 + jl])[0]]
    views = j["bufferViews"]
    img_views = {im["bufferView"]: im for im in j.get("images", [])}
    out, new_views = bytearray(), []
    for i, v in enumerate(views):
        s = v.get("byteOffset", 0)
        data = bytes(binary[s:s + v["byteLength"]])
        im = img_views.get(i)
        if im is not None and target(im.get("name", "")):
            img = Image.open(io.BytesIO(data))
            m = target(im["name"])
            if max(img.size) > m:
                r = m / max(img.size)
                img = img.resize((max(1, round(img.width * r)), max(1, round(img.height * r))), Image.LANCZOS)
            buf = io.BytesIO(); img.save(buf, "PNG", optimize=True); data = buf.getvalue()
            im["mimeType"] = "image/png"
        while len(out) % 4:
            out.append(0)
        nv = dict(v); nv["byteOffset"] = len(out); nv["byteLength"] = len(data)
        out += data; new_views.append(nv)
    while len(out) % 4:
        out.append(0)
    j["bufferViews"] = new_views
    j["buffers"][0]["byteLength"] = len(out)
    js = json.dumps(j, ensure_ascii=False, separators=(",", ":")).encode()
    js += b" " * (-len(js) % 4)
    total = 12 + 8 + len(js) + 8 + len(out)
    with open(dst, "wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total))
        f.write(struct.pack("<I4s", len(js), b"JSON")); f.write(js)
        f.write(struct.pack("<I4s", len(out), b"BIN\0")); f.write(out)
    print(f"{src}: {len(b)/1e6:.1f}MB → {dst}: {total/1e6:.1f}MB")


if __name__ == "__main__":
    shrink(sys.argv[1], sys.argv[2])
