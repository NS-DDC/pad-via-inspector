# -*- coding: utf-8 -*-
"""
run_inspection.py
=================
테스트셋 전체를 검사하고 ground_truth.json 과 대조해 정확도를 보고한다.

사용
----
  python run_inspection.py                       # testset 전체 검사 + 정확도
  python run_inspection.py --via-design          # VIA 설계도 대조 옵션까지 켜고 검사
  python run_inspection.py --one <이미지이름>     # 1장만 단계별로 실행하며 중간 결과 저장
  python run_inspection.py --no-save             # 결과 이미지 저장 생략

출력
----
  testset/result/<name>_result.png   판정 시각화
  testset/result/report.json         전체 결과 + 정확도
  testset/steps/<name>/step*.png     (--one 사용 시) 단계별 중간 이미지

Python 3.9+
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Any, Dict, List

import cv2
import numpy as np

from pad_via_inspector import (
    CODE_ERROR, CODE_OK, CODE_PAD_DEFECT, CODE_VIA_DEFECT,
    InspectConfig, imread_unicode, imwrite_unicode, inspect,
    step1_preprocess, step2_segment_pads, step3_check_pad_presence,
    step4_check_via, step5_render,
)

CODE_NAME = {CODE_OK: "OK(1)", CODE_PAD_DEFECT: "PAD(24)",
             CODE_VIA_DEFECT: "VIA(99)", CODE_ERROR: "ERROR"}


# ----------------------------------------------------------------------------
# 방식 1) 한 번에 호출
# ----------------------------------------------------------------------------
def run_batch(root: str, save: bool = True, cfg: InspectConfig = None) -> Dict[str, Any]:
    cfg = cfg or InspectConfig()
    img_dir = os.path.join(root, "images")
    cad_dir = os.path.join(root, "design")
    out_dir = os.path.join(root, "result")
    if save:
        os.makedirs(out_dir, exist_ok=True)

    gt_path = os.path.join(root, "ground_truth.json")
    gt = json.load(open(gt_path, encoding="utf-8")) if os.path.exists(gt_path) else {}

    results: Dict[str, Any] = {}
    confusion: Dict[str, int] = {}
    n_ok = 0
    n_total = 0

    for path in sorted(glob.glob(os.path.join(img_dir, "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0]
        cad_png = os.path.join(cad_dir, name + "_cad.png")
        cad_json = os.path.join(cad_dir, name + "_cad.json")
        via_png = os.path.join(cad_dir, name + "_via.png")
        if not os.path.exists(cad_png):
            continue

        vd = via_png if (cfg.via_design_check and os.path.exists(via_png)) else None
        res = inspect(path, cad_png, cad_json if os.path.exists(cad_json) else None, cfg, vd)

        if save:
            imwrite_unicode(os.path.join(out_dir, name + "_result.png"), res.result_image)

        item = res.summary()
        item["defects"] = [d.to_dict() for d in res.defects]

        if name in gt:
            gt_key = "expected_code_design" if cfg.via_design_check else "expected_code"
            exp = str(gt[name].get(gt_key, gt[name]["expected_code"]))
            item["expected_code"] = exp
            item["match"] = (res.code == exp)
            n_total += 1
            n_ok += int(item["match"])
            key = "%s -> %s" % (CODE_NAME.get(exp, exp), CODE_NAME.get(res.code, res.code))
            confusion[key] = confusion.get(key, 0) + 1

        results[name] = item

    report = {
        "config": {k: getattr(cfg, k) for k in
                   ("cad_coverage_thresh", "extra_overlap_thresh", "via_dark_ratio",
                    "via_offset_tol", "via_target_min_circularity", "via_design_check")},
        "total": len(results),
        "labeled": n_total,
        "correct": n_ok,
        "accuracy": round(n_ok / n_total, 4) if n_total else None,
        "confusion": confusion,
        "results": results,
    }
    if save:
        with open(os.path.join(root, "result", "report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=1)
    return report


# ----------------------------------------------------------------------------
# 방식 2) 단계별 호출 (중간 결과 확인)
# ----------------------------------------------------------------------------
def run_stepwise(root: str, name: str, cfg: InspectConfig = None,
                 save: bool = True) -> Dict[str, Any]:
    """1장을 단계별로 실행하면서 각 단계 결과를 이미지로 남긴다."""
    cfg = cfg or InspectConfig()
    img_path = os.path.join(root, "images", name + ".png")
    cad_png = os.path.join(root, "design", name + "_cad.png")
    cad_json = os.path.join(root, "design", name + "_cad.json")
    via_png = os.path.join(root, "design", name + "_via.png")
    out_dir = os.path.join(root, "steps", name)
    if save:
        os.makedirs(out_dir, exist_ok=True)

    bgr = imread_unicode(img_path, cv2.IMREAD_COLOR)
    if bgr is None:
        raise FileNotFoundError(img_path)

    def dump(fn: str, img: np.ndarray) -> None:
        if save:
            imwrite_unicode(os.path.join(out_dir, fn), img)

    # --- STEP 1 : 전처리 ---
    pre = step1_preprocess(bgr, cfg)
    print("[STEP1] 이진화 임계값 = %.1f" % pre.threshold)
    dump("step1_gray.png", pre.gray)
    dump("step1_binary.png", pre.binary)

    # --- STEP 2 : PAD 분할 ---
    seg = step2_segment_pads(pre, cfg)
    print("[STEP2] 검출 PAD = %d개 (구멍 메움 %d px)"
          % (len(seg.pads), int(np.count_nonzero(seg.hole_mask))))
    dump("step2_pad_mask.png", seg.pad_mask)
    lab_vis = cv2.applyColorMap(
        np.uint8(seg.label_map * (255 // max(seg.label_map.max(), 1))), cv2.COLORMAP_JET)
    lab_vis[seg.label_map == 0] = 0
    dump("step2_labels.png", lab_vis)

    # --- STEP 3 : PAD 유무 (code 24) ---
    pres = step3_check_pad_presence(seg, cad_png, cfg)
    print("[STEP3] 설계 PAD = %d개 / 누락 %d / 과잉 %d"
          % (len(pres.cad_pads), len(pres.missing), len(pres.extra)))
    for d in pres.missing + pres.extra:
        print("        - %s pad#%d @%s %s" % (d.kind, d.pad_id,
                                              [round(v, 1) for v in d.position], d.detail))
    dump("step3_cad_mask.png", pres.cad_mask)
    dump("step3_overlay.png", pres.overlay)

    # --- STEP 4 : VIA (code 99) ---
    vd = via_png if (cfg.via_design_check and os.path.exists(via_png)) else None
    via = step4_check_via(bgr, seg, pres, cad_json if os.path.exists(cad_json) else None, cfg, vd)
    n_off = sum(1 for f in via.findings if f.get("status") == "VIA_OFFSET")
    n_mis = sum(1 for f in via.findings if f.get("status") == "VIA_MISSING")
    n_ext = sum(1 for f in via.findings if f.get("status") == "VIA_EXTRA")
    n_okv = sum(1 for f in via.findings if f.get("status") == "OK")
    print("[STEP4] VIA 대상 %d개 -> 정상 %d / 편심 %d / 없음 %d / 과잉 %d%s"
          % (len(via.target_pad_ids), n_okv, n_off, n_mis, n_ext,
             " (설계 대조 ON)" if vd else ""))
    for f in via.findings:
        if f.get("status") in ("VIA_OFFSET", "VIA_MISSING", "VIA_EXTRA"):
            print("        - %s pad#%d offset_norm=%s (tol=%s)"
                  % (f["status"], f["pad_id"], f.get("offset_norm"), cfg.via_offset_tol))
    dump("step4_via_mask.png", via.via_mask)

    # --- STEP 5 : 종합 + 시각화 ---
    res = step5_render(bgr, seg, pres, via, cfg)
    print("[STEP5] 최종 CODE = %s (%s)" % (res.code, res.message))
    dump("step5_result.png", res.result_image)

    return {"result": res, "pre": pre, "seg": seg, "presence": pres, "via": via,
            "findings": via.findings}


# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="PAD/VIA 검사 실행")
    ap.add_argument("--root", default="testset")
    ap.add_argument("--one", default=None, help="1장만 단계별 실행 (확장자 없는 이름)")
    ap.add_argument("--no-save", action="store_true")
    ap.add_argument("--verbose", action="store_true", help="틀린 항목 상세 출력")
    ap.add_argument("--via-design", dest="via_design", action="store_true",
                    help="VIA 설계도 대조 옵션 활성화 (VIA 과잉까지 검출)")
    a = ap.parse_args()

    cfg = InspectConfig(via_design_check=a.via_design)

    if a.one:
        run_stepwise(a.root, a.one, cfg=cfg, save=not a.no_save)
        return 0

    rep = run_batch(a.root, save=not a.no_save, cfg=cfg)
    print("=" * 62)
    print("검사 %d장 / 라벨 %d장 / 정확도 %s"
          % (rep["total"], rep["labeled"], rep["accuracy"]))
    print("-" * 62)
    print("혼동표 (정답 -> 판정)")
    for k in sorted(rep["confusion"]):
        mark = "OK " if k.split(" -> ")[0] == k.split(" -> ")[1] else "NG "
        print("  %s %-28s %d" % (mark, k, rep["confusion"][k]))
    print("=" * 62)

    wrong = [(n, v) for n, v in rep["results"].items() if v.get("match") is False]
    if wrong:
        print("오판정 %d건:" % len(wrong))
        for n, v in wrong:
            print("  %-46s exp=%-3s got=%-3s %s"
                  % (n[:46], v.get("expected_code"), v["code"], v["defect_kinds"]))
            if a.verbose:
                for d in v["defects"]:
                    print("      %s pad#%s %s %s" % (d["kind"], d["pad_id"],
                                                     d["position"], d["detail"]))
    else:
        print("오판정 없음")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
