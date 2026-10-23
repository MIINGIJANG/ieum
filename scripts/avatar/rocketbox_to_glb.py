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
- --subdivide N: 몸(옷·손) 부분만 따로 떼어 N 단계 세분화 (원래 꼭짓점 4천 개라 윤곽이 각져 보임).
  얼굴은 표정 셰이프키가 있어 그대로 둠. 목 경계가 벌어지지 않게 경계선은 움직이지 않음
- --drop-islands=N: 몸 재질에서 꼭짓점 N 개 이하의 떨어진 조각 지우기 (조끼 버클처럼 투박한 작은 부품, 떠 있는 점)
- --smooth-armpits=R: 어깨 관절 R m 안 꼭짓점의 뼈 가중치를 이웃과 평균내 매끄럽게 (겨드랑이에서 조끼·소매가 겹치거나 각지는 것)
"""
import re
import sys
from pathlib import Path

import bpy

opt = {a.split("=")[0][2:]: a.split("=")[1] for a in sys.argv if a.startswith("--") and "=" in a}
SUB = int(opt.get("subdivide", 0))
DROP = int(opt.get("drop-islands", 0))
PIT = float(opt.get("smooth-armpits", 0))
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

import bmesh
from mathutils import Vector

mesh0 = next(o for o in bpy.data.objects if o.type == "MESH")
arm = next(o for o in bpy.data.objects if o.type == "ARMATURE")

if DROP:
    bm = bmesh.new(); bm.from_mesh(mesh0.data); bm.verts.ensure_lookup_table()
    seen, kill = set(), []
    for v in bm.verts:
        if v.index in seen:
            continue
        comp, stack = [], [v]
        seen.add(v.index)
        while stack:
            x = stack.pop(); comp.append(x)
            for e in x.link_edges:
                y = e.other_vert(x)
                if y.index not in seen:
                    seen.add(y.index); stack.append(y)
        mats = {mesh0.data.materials[f.material_index].name for x in comp for f in x.link_faces}
        if len(comp) <= DROP and (not mats or all(m.endswith("_body") for m in mats)):
            kill += comp
    bmesh.ops.delete(bm, geom=kill, context="VERTS")
    bm.to_mesh(mesh0.data); bm.free()
    print("작은 조각 지움:", len(kill), "꼭짓점")

if PIT:
    # 어깨 관절 주변: 위팔·쇄골·가슴 뼈 가중치를 이웃 평균으로 여러 번 (메시 연결을 따라 매끄럽게 퍼짐)
    vg = {g.name: g.index for g in mesh0.vertex_groups}
    me = mesh0.data
    bm = bmesh.new(); bm.from_mesh(me); bm.verts.ensure_lookup_table()
    nbr = {v.index: [e.other_vert(v).index for e in v.link_edges] for v in bm.verts}
    bm.free()
    W = [{g.group: g.weight for g in v.groups} for v in me.vertices]
    mw = mesh0.matrix_world
    for side in ("L", "R"):
        names = [f"Bip01 {side} UpperArm", f"Bip01 {side} Clavicle", "Bip01 Spine2", "Bip01 Spine1"]
        groups = [vg[n] for n in names if n in vg]
        S = arm.matrix_world @ arm.data.bones[f"Bip01 {side} UpperArm"].head_local
        sel = [v.index for v in me.vertices if (mw @ v.co - S).length < PIT and any(W[v.index].get(g, 0) > 0 for g in groups)]
        for _ in range(12):
            new = {}
            for i in sel:
                ns = nbr[i]
                if not ns:
                    continue
                fall = 1 - min(1, (mw @ me.vertices[i].co - S).length / PIT)   # 관절 가까울수록 많이
                k = 0.5 * fall
                w = dict(W[i])
                for g in groups:
                    avg = sum(W[j].get(g, 0) for j in ns) / len(ns)
                    w[g] = (1 - k) * w.get(g, 0) + k * avg
                tot = sum(w.values()) or 1
                new[i] = {g: x / tot for g, x in w.items() if x > 1e-4}
            for i, w in new.items():
                W[i] = w
        for i in sel:
            for g in groups:
                if W[i].get(g, 0) > 1e-4:
                    mesh0.vertex_groups[g].add([i], W[i][g], "REPLACE")
                else:
                    mesh0.vertex_groups[g].remove([i])
        print("겨드랑이 가중치 스무딩:", side, len(sel), "꼭짓점")

if SUB:
    # 몸 재질 면만 따로 떼서 세분화 (셰이프키는 얼굴에만 의미 있음 → 몸 쪽은 지움)
    mesh = next(o for o in bpy.data.objects if o.type == "MESH")
    bpy.context.view_layer.objects.active = mesh
    mesh.select_set(True)
    bpy.ops.object.mode_set(mode="EDIT")
    bpy.ops.mesh.select_all(action="DESELECT")
    body_slot = next(i for i, m in enumerate(mesh.data.materials) if m.name.endswith("_body"))
    mesh.active_material_index = body_slot
    bpy.ops.object.material_slot_select()
    bpy.ops.mesh.separate(type="SELECTED")
    bpy.ops.object.mode_set(mode="OBJECT")
    body = next(o for o in bpy.data.objects if o.type == "MESH" and o is not mesh)
    for o in bpy.context.selected_objects:
        o.select_set(False)
    body.select_set(True)
    bpy.context.view_layer.objects.active = body
    if body.data.shape_keys:
        bpy.ops.object.shape_key_remove(all=True)
    mod = body.modifiers.new("sub", "SUBSURF")
    mod.levels = SUB
    mod.render_levels = SUB
    mod.boundary_smooth = "PRESERVE_CORNERS"
    mod.uv_smooth = "PRESERVE_BOUNDARIES"
    # 아마추어 모디파이어보다 앞에 (세분화한 뒤 뼈에 묶이게)
    while body.modifiers.find("sub") > 0:
        bpy.ops.object.modifier_move_up(modifier="sub")
    bpy.ops.object.modifier_apply(modifier="sub")
    print("몸 세분화:", len(body.data.vertices), "꼭짓점")

out.parent.mkdir(parents=True, exist_ok=True)
bpy.ops.export_scene.gltf(filepath=str(out), export_format="GLB", export_skins=True, export_morph=True,
                          export_morph_normal=False, export_animations=False, export_yup=True, export_apply=False)
print("완료:", out)
