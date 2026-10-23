"""Microsoft Rocketbox 캐릭터(MIT) → 웹 아바타 GLB.

  pip install bpy        # Blender 를 파이썬 모듈로 (5.x)
  git clone --depth 1 --filter=blob:none --sparse https://github.com/microsoft/Microsoft-Rocketbox.git rb
  cd rb && git sparse-checkout set Assets/Avatars/Professions/Construction_Male_07 && cd ..
  python scripts/avatar/rocketbox_to_glb.py rb/Assets/Avatars/Professions/Construction_Male_07 build/avatar_raw.glb
  node scripts/deploy/slim_glb.mjs build/avatar_raw.glb web/models/avatar.glb 2048

- 표정이 들어 있는 *_facial.fbx 를 씀
- 셰이프키: ARKit 52 ('AK_25_JawOpen' → 'jawOpen') 와 입 모양 ('AA_VI_10_aa' → 'viseme_aa') 만 남김
  (눈동자 eyeLook* 는 눈 뼈로 움직이므로 뺌, AU·HB·SR 표정 세트도 뺌 → 용량)
- 재질: 색·법선 텍스처, 스페큘러 맵은 거칠기로 (밝을수록 덜 거칠게)
- 뼈 이름(Bip01 ...)은 web/rig.js 가 자동 인식
"""
import re
import sys
from pathlib import Path

import bpy

src, out = Path(sys.argv[-2]), Path(sys.argv[-1])
fbx = next(src.glob("Export/*_facial.fbx"))
tex = src / "Textures"

bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.fbx(filepath=str(fbx))

VIS = {"Sil": "sil", "KK": "kk"}


def arkit(name):
    m = re.match(r"AK_\d+_(\w+)$", name)
    if m:
        n = m.group(1)
        return n[0].lower() + n[1:]
    m = re.match(r"AA_VI_\d+_(\w+)$", name)
    if m:
        return "viseme_" + VIS.get(m.group(1), m.group(1))
    return None


for o in [o for o in bpy.data.objects if o.type == "MESH"]:
    keys = o.data.shape_keys
    if keys:
        for kb in list(keys.key_blocks)[1:]:
            new = arkit(kb.name)
            if new is None or new.startswith("eyeLook"):
                o.shape_key_remove(kb)
            else:
                kb.name = new
        print(o.name, "셰이프키", len(o.data.shape_keys.key_blocks) - 1)
    # 재질: 색 + 법선 + (스페큘러 → 거칠기)
    for mat in o.data.materials:
        part = mat.name.split("_")[-1]                       # body / head / helmet
        mat.use_nodes = True
        nt = mat.node_tree
        bsdf = next(n for n in nt.nodes if n.type == "BSDF_PRINCIPLED")
        def img(kind):
            f = next(tex.glob(f"*_{part}_{kind}.tga"), None)
            if not f:
                return None
            n = nt.nodes.new("ShaderNodeTexImage")
            n.image = bpy.data.images.load(str(f))
            if kind != "color":
                n.image.colorspace_settings.name = "Non-Color"
            return n
        c = img("color")
        if c:
            nt.links.new(c.outputs["Color"], bsdf.inputs["Base Color"])
        nm = img("normal")
        if nm:
            nmap = nt.nodes.new("ShaderNodeNormalMap")
            nt.links.new(nm.outputs["Color"], nmap.inputs["Color"])
            nt.links.new(nmap.outputs["Normal"], bsdf.inputs["Normal"])
        sp = img("specular")
        if sp:
            inv = nt.nodes.new("ShaderNodeInvert")
            nt.links.new(sp.outputs["Color"], inv.inputs["Color"])
            sep = nt.nodes.new("ShaderNodeSeparateColor")
            nt.links.new(inv.outputs["Color"], sep.inputs["Color"])
            nt.links.new(sep.outputs["Green"], bsdf.inputs["Roughness"])
        bsdf.inputs["Metallic"].default_value = 0.0

out.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", export_skins=True, export_morph=True,
                          export_morph_normal=False, export_animations=False, export_yup=True, export_apply=False)
print("완료:", out)
