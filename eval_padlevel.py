# -*- coding: utf-8 -*-
"""
eval_padlevel.py
================
PAD 단위 정확도 평가 + VIA 편심 임계값 분리도(margin) 분석.

이미지 단위 코드가 맞아도 개별 PAD 판정이 틀릴 수 있으므로 별도로 검증한다.
정답(ground_truth.json)의 pad_center 좌표와 검사 결과를 위치로 매칭한다.
(이진화 임계값 변동으로 pad_id 는 재현되지 않을 수 있어 위치를 키로 사용)

  python eval_padlevel.py                # 기본 검사
  python eval_padlevel.py --via-design   # VIA 설계도 대조 옵션 ON
"""

from __future__ import annotations

import argparse
import glob
import json
import os
from typing import Dict, List, Tuple

import numpy as np

from pad_via_inspector import (
    InspectConfig, step1_preprocess, step2_segment_pads,
    step3_check_pad_presence, step4_check_via,
)

MATCH_RADIUS = 6.0   # 정답 pad_center 와 검출 pad_center 를 같은 PAD로 볼 최대 거리(px)


def _nearest(pt, cands: List[Tuple[float, float]]):
    if not cands:
        return -1, 1e9
    d = [float(np.hypot(pt[0] - c[0], pt[1] - c[1])) for c in cands]
    i = int(np.argmin(d))
    return i, d[i]


def main() -> int:
    ap = argparse.ArgumentParser(description="PAD 단위 평가")
    ap.add_argument("--root", default="testset")
    ap.add_argument("--via-design", dest="via_design", action="store_true",
                    help="VIA 설계도 대조 옵션 활성화")
    a = ap.parse_args()

    root = a.root
    cfg = InspectConfig(via_design_check=a.via_design)
    gt = json.load(open(os.path.join(root, "ground_truth.json"), encoding="utf-8"))

    # 혼동행렬: 정답모드(center/shift/none/extra) x 판정(OK/VIA_OFFSET/VIA_MISSING/...)
    conf: Dict[Tuple[str, str], int] = {}
    good_offsets: List[float] = []
    shift_offsets: List[float] = []
    unmatched = 0

    for path in sorted(glob.glob(os.path.join(root, "images", "*.png"))):
        name = os.path.splitext(os.path.basename(path))[0]
        if name not in gt:
            continue
        cad_png = os.path.join(root, "design", name + "_cad.png")
        cad_json = os.path.join(root, "design", name + "_cad.json")
        via_png = os.path.join(root, "design", name + "_via.png")
        vd = via_png if (cfg.via_design_check and os.path.exists(via_png)) else None

        pre = step1_preprocess(path, cfg)
        seg = step2_segment_pads(pre, cfg)
        pres = step3_check_pad_presence(seg, cad_png, cfg)
        via = step4_check_via(pre.bgr, seg, pres, cad_json, cfg, vd)

        centers = [tuple(f["pad_center"]) for f in via.findings]
        for plan in gt[name]["via_plan"]:
            truth = plan["mode"]
            i, d = _nearest(plan["pad_center"], centers)
            if d > MATCH_RADIUS:
                conf[(truth, "UNDETECTED")] = conf.get((truth, "UNDETECTED"), 0) + 1
                unmatched += 1
                continue
            f = via.findings[i]
            pred = f.get("status", "?")
            conf[(truth, pred)] = conf.get((truth, pred), 0) + 1
            if "offset_norm" in f:
                (shift_offsets if truth == "shift" else
                 good_offsets if truth == "center" else []).append(f["offset_norm"])

    # ---------------- 리포트 ----------------
    truths = ["center", "shift", "none", "extra"]
    preds = ["OK", "VIA_OFFSET", "VIA_MISSING", "VIA_EXTRA", "SKIP", "UNDETECTED"]
    print("=" * 82)
    print("PAD 단위 혼동행렬  (행=정답, 열=판정)   설계 VIA 대조=%s"
          % ("ON" if cfg.via_design_check else "OFF"))
    print("%-10s" % "", "".join("%-13s" % p for p in preds))
    total = correct = 0
    # 설계 대조가 꺼져 있으면 "extra" PAD 는 정중앙 VIA 이므로 OK 가 정답이다.
    expect = {"center": "OK", "shift": "VIA_OFFSET", "none": "VIA_MISSING",
              "extra": "VIA_EXTRA" if cfg.via_design_check else "OK"}
    for t in truths:
        row = [conf.get((t, p), 0) for p in preds]
        print("%-10s" % t, "".join("%-13d" % v for v in row))
        total += sum(row)
        correct += conf.get((t, expect[t]), 0)
    print("-" * 82)
    print("PAD 단위 정확도 = %d/%d = %.4f" % (correct, total, correct / total if total else 0))
    if unmatched:
        print("경고: 위치 매칭 실패 %d건 (MATCH_RADIUS=%.1f)" % (unmatched, MATCH_RADIUS))

    # ---------------- 분리도 ----------------
    g = np.array(good_offsets)
    s = np.array(shift_offsets)
    print("-" * 82)
    print("VIA offset_norm 분포 (판정 임계값 tol=%.2f, 절대하한=%.1fpx)"
          % (cfg.via_offset_tol, cfg.via_offset_min_px))
    if g.size:
        print("  정상(center) n=%-4d mean=%.3f p95=%.3f p99=%.3f max=%.3f"
              % (g.size, g.mean(), np.percentile(g, 95), np.percentile(g, 99), g.max()))
    if s.size:
        print("  쏠림(shift)  n=%-4d mean=%.3f p5 =%.3f p1 =%.3f min=%.3f"
              % (s.size, s.mean(), np.percentile(s, 5), np.percentile(s, 1), s.min()))
    if g.size and s.size:
        margin = s.min() - g.max()
        print("  분리 마진 = %.3f  (%s)" % (margin, "양호" if margin > 0 else "겹침 있음"))
    print("=" * 82)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
