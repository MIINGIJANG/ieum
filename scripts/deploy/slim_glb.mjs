// 아바타 GLB 줄이기: 모프 법선 제거, 눈동자 모프(eyeLook*) 제거(눈은 뼈로 움직임), 텍스처 2048 WebP
import { NodeIO } from "@gltf-transform/core";
import { ALL_EXTENSIONS } from "@gltf-transform/extensions";
import { prune, dedup, textureCompress } from "@gltf-transform/functions";
import sharp from "sharp";
const [,, src, dst, size = "2048"] = process.argv;
const io = new NodeIO().registerExtensions(ALL_EXTENSIONS);
const doc = await io.read(src);
for (const mesh of doc.getRoot().listMeshes()) {
  const names = mesh.getExtras()?.targetNames || [];
  for (const prim of mesh.listPrimitives()) {
    const targets = prim.listTargets();
    const keep = [];
    targets.forEach((t, i) => {
      if (/^eyeLook/.test(names[i] || "")) { prim.removeTarget(t); t.dispose(); return; }
      const n = t.getAttribute("NORMAL"); if (n) { t.setAttribute("NORMAL", null); }
      keep.push(names[i]);
    });
    mesh.setExtras({ ...mesh.getExtras(), targetNames: keep });
    mesh.setWeights(keep.map(() => 0));
  }
}
await doc.transform(dedup(), textureCompress({ encoder: sharp, targetFormat: "webp", quality: 88, resize: [+size, +size] }), prune());
await io.write(dst, doc);
