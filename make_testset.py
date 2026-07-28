# -*- coding: utf-8 -*-
"""
make_testset.py
===============
Images/ 폴더의 원본 이미지를 재료로 PAD/VIA 검사용 테스트셋을 생성한다.

생성물
------
testset/
  images/<name>.png        검사 대상 이미지 (VIA가 그려지고, 일부는 PAD 결함이 주입됨)
  design/<name>_cad.png    PAD 설계도 (PAD 영역만 이진화, 실제보다 살짝 작음)
  design/<name>_cad.json   설계도 메타 (PAD 목록, via_expected 등)
  design/<name>_via.png    VIA 설계도 (설계상 VIA 가 있어야 할 위치만 흰 점)
  preview/<name>_prev.png  원본 | 테스트이미지 | 설계도 비교 미리보기
  ground_truth.json        정답 라벨 (expected_code, 주입 결함 목록)

결함 시나리오
-------------
  GOOD        : 대상 PAD 전부 정중앙 VIA            -> expected_code "1"
  VIA_DEFECT  : 일부 VIA 쏠림 / 누락 / 과잉         -> expected_code "99"
  PAD_DEFECT  : PAD 지우기(누락) 또는 PAD 추가(과잉) -> expected_code "24"

VIA 과잉(VIA_EXTRA)은 "설계 VIA 마스크에는 점이 없는데 실물엔 VIA 가 찍힌" PAD 로 만든다.
설계 대조 옵션(InspectConfig.via_design_check=True)에서만 검출 가능한 결함이다.

주의: 한 이미지에 "정중앙 / 쏠림 / 없음" 세 가지가 모두 나타나거나,
      전부 정중앙만 있는 두 가지 형태로만 만든다 (요구사항).

Python 3.9+
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import random
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np

from pad_via_inspector import (
    InspectConfig, build_design_mask, build_via_design_mask,
    imwrite_unicode, imread_unicode,
    is_via_target, step1_preprocess, step2_segment_pads,
)

# ----------------------------------------------------------------------------
# 생성 파라미터
# ----------------------------------------------------------------------------
CAD_ERODE_PX = 2                  # 설계도 축소량(px). "실제보다 살짝 작음"

# --- PAD 형상 균일화 -------------------------------------------------------
# 실측 PAD 는 원본 이미지에서 따오므로 모양·크기가 제각각이다(면적 67~3068,
# 종횡비 0.21~5.43). 검사 성능을 형상 변수 없이 재려면 전부 같게 만들어야 한다.
# 원본 PAD 자리는 그대로 두고 모양만 동일 크기 정사각으로 다시 그린다.
PAD_UNIFORM = True                # False 면 원본 PAD 형상을 그대로 사용
PAD_SIDE = 16                     # 정사각 한 변(px). 면적 약 253 -> via_target 범위 안
PAD_CORNER = 2                    # 모서리 라운드 반지름(px). 원형도 0.72 하한 여유 확보
PAD_MIN_GAP = 5                   # 이웃 PAD 사이 최소 간격(px). 붙어서 한 덩어리 되는 것 방지

VIA_RADIUS_RATIO = 0.13           # PAD 등가반지름 대비 VIA 코어 반지름
VIA_RADIUS_MIN = 2.0
VIA_RADIUS_MAX = 5.0

# VIA 코어 밝기 = PAD 밝기 * 이 값. 작을수록 진하다.
# 대비가 약한 VIA 까지 검출되는지 보려고 3단계를 골고루 섞는다.
VIA_DARKNESS_LEVELS = (0.12, 0.25, 0.45)
VIA_DARKNESS_NAMES = ("dark", "mid", "light")

VIA_EDGE_SOFT = 0.9               # 가장자리 번짐(px 단위 시그마 가산)
VIA_CENTER_JITTER = 0.4           # 양품 VIA의 중심 흔들림(px). 실사 느낌용

VIA_SHIFT_MIN = 0.42              # 불량(쏠림) 시 이동량 (PAD 반지름 대비)
VIA_SHIFT_MAX = 0.60

VIA_DESIGN_DOT_RADIUS = 2         # VIA 설계 마스크에 찍는 점의 반지름(px). 위치만 의미 있음

RATIO_GOOD = 0.60
RATIO_VIA = 0.25
RATIO_PAD = 0.15

MIN_VIA_TARGETS_FOR_MIXED = 3     # 3종 혼합(정중앙/쏠림/없음)을 넣으려면 최소 이만큼 필요


# ----------------------------------------------------------------------------
# 배경 통계
# ----------------------------------------------------------------------------
def _background_stats(bgr: np.ndarray, pad_mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """PAD가 아닌 영역의 채널별 평균/표준편차."""
    bg = pad_mask == 0
    if np.count_nonzero(bg) < 50:
        return np.array([20.0, 20.0, 20.0]), np.array([6.0, 6.0, 6.0])
    px = bgr[bg].astype(np.float32)
    return px.mean(axis=0), px.std(axis=0) + 1e-3


def _pad_color_stats(bgr: np.ndarray, label_map: np.ndarray,
                     pad_id: int) -> Tuple[np.ndarray, np.ndarray]:
    px = bgr[label_map == pad_id].astype(np.float32)
    return px.mean(axis=0), px.std(axis=0) + 1e-3


# ----------------------------------------------------------------------------
# VIA 그리기
# ----------------------------------------------------------------------------
def draw_via(bgr: np.ndarray, center: Tuple[float, float], radius: float,
             rng: random.Random, darkness: float = 0.25) -> None:
    """(cx, cy)에 실사에 가까운 어두운 VIA 점을 그린다 (in-place).

    가우시안 프로파일로 중심이 가장 어둡고 가장자리로 갈수록 원래 PAD 색에 수렴.
    darkness 가 작을수록 진하다 (0.12 진함 / 0.25 중간 / 0.45 연함).
    """
    H, W = bgr.shape[:2]
    cx, cy = float(center[0]), float(center[1])
    rad = float(max(radius, VIA_RADIUS_MIN))
    reach = int(np.ceil(rad * 2.6 + VIA_EDGE_SOFT * 2)) + 1

    x0, x1 = max(int(cx) - reach, 0), min(int(cx) + reach + 1, W)
    y0, y1 = max(int(cy) - reach, 0), min(int(cy) + reach + 1, H)
    if x1 <= x0 or y1 <= y0:
        return

    ys, xs = np.mgrid[y0:y1, x0:x1].astype(np.float32)
    dist = np.hypot(xs - cx, ys - cy)

    # 코어 반지름 rad 안쪽은 거의 1, 밖으로 부드럽게 감쇠
    sigma = rad * 0.55 + VIA_EDGE_SOFT
    weight = np.exp(-0.5 * (np.clip(dist - rad * 0.45, 0, None) / sigma) ** 2)
    weight = np.clip(weight, 0.0, 1.0).astype(np.float32)

    roi = bgr[y0:y1, x0:x1].astype(np.float32)
    dark = roi * float(darkness)
    # VIA는 완전 무채색 검정이 아니라 약간 갈색빛을 띔 -> R채널을 조금 덜 낮춤
    dark[..., 2] *= 1.22
    dark[..., 1] *= 1.05

    out = roi * (1.0 - weight[..., None]) + dark * weight[..., None]
    noise = np.random.normal(0.0, 3.0, out.shape).astype(np.float32) * weight[..., None]
    out = np.clip(out + noise, 0, 255)
    bgr[y0:y1, x0:x1] = out.astype(np.uint8)


# ----------------------------------------------------------------------------
# PAD 형상 균일화
# ----------------------------------------------------------------------------
def _square_layer(shape: Tuple[int, int], center: Tuple[int, int],
                  side: int, corner: int) -> np.ndarray:
    """모서리가 살짝 둥근 정사각 알파맵(0~255)."""
    H, W = shape
    layer = np.zeros((H, W), np.uint8)
    cx, cy = center
    h = side // 2
    r = int(np.clip(corner, 0, h - 1))
    # 십자 두 개로 라운드 사각을 만든 뒤 네 귀퉁이에 원을 채운다
    cv2.rectangle(layer, (cx - h + r, cy - h), (cx + h - r, cy + h), 255, -1)
    cv2.rectangle(layer, (cx - h, cy - h + r), (cx + h, cy + h - r), 255, -1)
    if r > 0:
        for sx in (-1, 1):
            for sy in (-1, 1):
                cv2.circle(layer, (cx + sx * (h - r), cy + sy * (h - r)), r, 255, -1)
    return layer


def _paint_texture(bgr: np.ndarray, alpha: np.ndarray,
                   mean: np.ndarray, std: np.ndarray) -> None:
    """알파맵 자리에 PAD 질감을 합성한다 (in-place).

    백색잡음만 쓰면 소금후추처럼 보이므로 저주파 얼룩 + 약한 고주파로 섞는다.
    """
    H, W = bgr.shape[:2]
    low = np.random.normal(0.0, 1.0, (H, W, 3)).astype(np.float32)
    low = cv2.GaussianBlur(low, (0, 0), 2.5)
    low /= (low.std() + 1e-6)
    high = np.random.normal(0.0, 1.0, (H, W, 3)).astype(np.float32)
    scale = np.asarray(std, np.float32)
    tex = np.asarray(mean, np.float32) + low * scale * 0.75 + high * scale * 0.25
    a = (cv2.GaussianBlur(alpha, (3, 3), 0).astype(np.float32) / 255.0)[..., None]
    out = bgr.astype(np.float32) * (1 - a) + np.clip(tex, 0, 255) * a
    bgr[:] = np.clip(out, 0, 255).astype(np.uint8)


def uniformize_pads(bgr: np.ndarray, seg, bg_mean: np.ndarray, bg_std: np.ndarray
                    ) -> np.ndarray:
    """실측 PAD 를 전부 지우고 같은 자리에 동일한 정사각 PAD 를 다시 그린다.

    - 위치 : 원본 PAD 무게중심 그대로 (배경·조명·노이즈는 실사 유지)
    - 모양 : 한 변 PAD_SIDE 인 라운드 정사각, 전부 동일
    - 색   : 원본의 전체 PAD 화소 통계 하나로 통일

    서로 너무 가까운 PAD 는 정사각으로 바꾸면 한 덩어리로 붙어버리므로
    큰 것부터 자리를 잡고 간격이 모자라면 버린다.
    """
    H, W = bgr.shape[:2]
    pad_px = bgr[seg.pad_mask > 0].astype(np.float32)
    if pad_px.size == 0:
        return bgr
    pmean, pstd = pad_px.mean(axis=0), pad_px.std(axis=0) + 1e-3

    out = bgr.copy()
    # 1) 기존 PAD 를 배경으로 밀어버린다
    grown = cv2.dilate((seg.pad_mask > 0).astype(np.uint8), np.ones((5, 5), np.uint8))
    idx = grown > 0
    n = int(np.count_nonzero(idx))
    if n:
        out[idx] = np.clip(np.random.normal(bg_mean, bg_std, (n, 3)), 0, 255).astype(np.uint8)
        out[idx] = cv2.GaussianBlur(out, (3, 3), 0)[idx]

    # 2) 같은 자리에 동일한 정사각을 놓는다 (면적 큰 PAD 우선)
    h = PAD_SIDE // 2
    margin = h + 2
    taken = np.zeros((H, W), np.uint8)
    alpha = np.zeros((H, W), np.uint8)
    for p in sorted(seg.pads, key=lambda q: -q.area):
        cx, cy = int(round(p.centroid[0])), int(round(p.centroid[1]))
        if not (margin <= cx < W - margin and margin <= cy < H - margin):
            continue
        if taken[cy, cx]:
            continue
        sq = _square_layer((H, W), (cx, cy), PAD_SIDE, PAD_CORNER)
        alpha = np.maximum(alpha, sq)
        g = PAD_SIDE + PAD_MIN_GAP
        cv2.rectangle(taken, (cx - g, cy - g), (cx + g, cy + g), 255, -1)

    _paint_texture(out, alpha, pmean, pstd)
    return out


# ----------------------------------------------------------------------------
# PAD 결함 주입
# ----------------------------------------------------------------------------
def erase_pad(bgr: np.ndarray, label_map: np.ndarray, pad_id: int,
              bg_mean: np.ndarray, bg_std: np.ndarray) -> None:
    """PAD를 배경으로 덮어 지운다 -> 설계엔 있는데 실물에 없음 (MISSING_PAD)."""
    region = (label_map == pad_id).astype(np.uint8)
    grown = cv2.dilate(region, np.ones((5, 5), np.uint8))     # 경계 잔상 제거
    idx = grown > 0
    n = int(np.count_nonzero(idx))
    if n == 0:
        return
    fill = np.random.normal(bg_mean, bg_std, (n, 3))
    bgr[idx] = np.clip(fill, 0, 255).astype(np.uint8)
    # 주변과 자연스럽게 섞이도록 해당 영역만 약하게 블러
    blur = cv2.GaussianBlur(bgr, (3, 3), 0)
    bgr[idx] = blur[idx]


def find_free_spot(pad_mask: np.ndarray, radius: int,
                   rng: random.Random, tries: int = 400) -> Optional[Tuple[int, int]]:
    """기존 PAD와 겹치지 않는 배경 위치를 찾는다."""
    H, W = pad_mask.shape[:2]
    margin = radius + 4
    if W - margin <= margin or H - margin <= margin:
        return None
    occupied = cv2.dilate(pad_mask, np.ones((2 * (radius + 3) + 1,) * 2, np.uint8))
    for _ in range(tries):
        x = rng.randint(margin, W - margin - 1)
        y = rng.randint(margin, H - margin - 1)
        if occupied[y, x] == 0:
            return x, y
    return None


def add_pad(bgr: np.ndarray, center: Tuple[int, int], radius: int,
            pad_mean: np.ndarray, pad_std: np.ndarray) -> None:
    """배경에 없어야 할 PAD를 새로 그린다 -> EXTRA_PAD.

    PAD_UNIFORM 이면 다른 PAD 와 똑같은 정사각으로 그린다. 여기만 원형이면
    "모양이 다르다"는 단서로 구분될 수 있어 형상 균일화의 의미가 없어진다.
    """
    H, W = bgr.shape[:2]
    if PAD_UNIFORM:
        layer = _square_layer((H, W), center, PAD_SIDE, PAD_CORNER)
    else:
        layer = np.zeros((H, W), np.uint8)
        cv2.circle(layer, center, radius, 255, -1)
    _paint_texture(bgr, layer, pad_mean, pad_std)


# ----------------------------------------------------------------------------
# 설계도 생성
# ----------------------------------------------------------------------------
def make_design(pad_mask: np.ndarray, pads, via_target_ids, erode_px: int
                ) -> Tuple[np.ndarray, Dict[str, Any]]:
    """PAD 마스크 -> (설계 이진 이미지, 메타 dict).

    메타의 pad_id 는 '설계도 이진 이미지에서 재라벨링했을 때의 id'와 일치해야 한다.
    검사기(step3)가 설계 마스크를 다시 라벨링하기 때문.
    """
    cad = build_design_mask(pad_mask, erode_px)

    # 설계 마스크를 검사기와 동일한 방식으로 라벨링해서 id 체계를 맞춘다
    from pad_via_inspector import _describe_components  # 동일 규칙 재사용
    cfg = InspectConfig()
    cad_label, cad_pads = _describe_components(cad, max(8, cfg.min_pad_area // 3))

    # 실측 PAD와 설계 PAD를 겹침으로 매칭해서 via_expected 전달
    actual_label = np.zeros_like(cad_label)
    # pads 는 실측 라벨맵 기준이므로 여기서는 중심거리로 매칭
    meta_pads: List[Dict[str, Any]] = []
    for cp in cad_pads:
        best, bestd = None, 1e9
        for ap in pads:
            d = float(np.hypot(cp.centroid[0] - ap.centroid[0], cp.centroid[1] - ap.centroid[1]))
            if d < bestd:
                best, bestd = ap, d
        via_exp = bool(best is not None and bestd < 6.0 and best.pad_id in via_target_ids)
        item = cp.to_dict()
        item["via_expected"] = via_exp
        item["src_pad_id"] = int(best.pad_id) if best is not None else -1
        meta_pads.append(item)

    meta = {
        "format": "pad-via-cad/1.0",
        "image_size": [int(pad_mask.shape[1]), int(pad_mask.shape[0])],
        "erode_px": int(erode_px),
        "note": "PAD=255 binary design. Design pads are eroded (slightly smaller than real).",
        "pad_count": len(meta_pads),
        "via_expected_count": sum(1 for p in meta_pads if p["via_expected"]),
        "pads": meta_pads,
    }
    return cad, meta


# ----------------------------------------------------------------------------
# 이미지 1장 처리
# ----------------------------------------------------------------------------
def build_one(src_path: str, scenario: str, rng: random.Random,
              cfg: InspectConfig) -> Optional[Dict[str, Any]]:
    """원본 1장 -> (테스트 이미지, 설계도, 메타, 정답라벨)."""
    src = imread_unicode(src_path, cv2.IMREAD_COLOR)
    if src is None:
        return None

    pre = step1_preprocess(src, cfg)
    seg = step2_segment_pads(pre, cfg)
    if not seg.pads:
        return None

    # --- PAD 형상 균일화 ---
    # 원본을 갈아끼운 뒤 다시 분할한다. 이렇게 해야 설계도·정답·검사 입력이
    # 전부 같은(균일한) PAD 를 기준으로 만들어진다.
    if PAD_UNIFORM:
        bgm, bgs = _background_stats(src, seg.pad_mask)
        src = uniformize_pads(src, seg, bgm, bgs)
        pre = step1_preprocess(src, cfg)
        seg = step2_segment_pads(pre, cfg)
        if not seg.pads:
            return None

    # --- VIA 대상 PAD 선별 ---
    targets = [p for p in seg.pads if is_via_target(p, cfg)]
    if not targets:
        return None
    target_ids = {p.pad_id for p in targets}

    # --- 설계도는 '결함 주입 전' 원본 PAD 기준으로 생성 ---
    cad_mask, meta = make_design(seg.pad_mask, seg.pads, target_ids, CAD_ERODE_PX)

    img = src.copy()
    bg_mean, bg_std = _background_stats(src, seg.pad_mask)
    injected: List[Dict[str, Any]] = []

    # ------------------------------------------------------------------
    # VIA 배치 계획
    #   GOOD        -> 전부 정중앙
    #   VIA_DEFECT  -> 정중앙 + 쏠림 + 없음 (3종 혼합), 여유가 있으면 과잉 1개 추가
    #   PAD_DEFECT  -> VIA는 전부 정중앙 (PAD 결함만 평가)
    # ------------------------------------------------------------------
    plan: Dict[int, str] = {p.pad_id: "center" for p in targets}

    if scenario == "VIA_DEFECT":
        if len(targets) >= MIN_VIA_TARGETS_FOR_MIXED:
            pool = list(targets)
            rng.shuffle(pool)
            n_shift = max(1, int(round(len(pool) * rng.uniform(0.15, 0.30))))
            n_none = max(1, int(round(len(pool) * rng.uniform(0.08, 0.20))))
            n_shift = min(n_shift, len(pool) - 2)
            n_none = min(n_none, len(pool) - n_shift - 1)
            for p in pool[:n_shift]:
                plan[p.pad_id] = "shift"
            for p in pool[n_shift:n_shift + n_none]:
                plan[p.pad_id] = "none"
            # 설계 VIA 마스크에는 없는데 실물엔 정중앙 VIA 가 찍힌 PAD (= VIA 과잉)
            rest = pool[n_shift + n_none:]
            if len(rest) >= 2:
                plan[rest[0].pad_id] = "extra"
        else:
            # 대상 PAD가 적으면 최소 1개만 쏠림 처리
            plan[targets[rng.randrange(len(targets))].pad_id] = "shift"

    # 설계 VIA 마스크에 점을 찍을 PAD = "extra" 를 제외한 모든 대상 PAD
    # ("none" 은 설계엔 있어야 하는데 실물에 없는 것이므로 설계 점은 찍는다)
    design_via_pts = [p.centroid for p in targets if plan[p.pad_id] != "extra"]
    via_cad = build_via_design_mask(src.shape[:2], design_via_pts, VIA_DESIGN_DOT_RADIUS)

    # --- VIA 밝기 배분 ---
    # 진함/중간/연함을 1/3 씩 균등하게 섞는다. 단순 round-robin 은 이미지마다
    # 항상 "진함"부터 시작해 전체 집계에서 진함이 많아지므로 섞어서 뽑는다.
    n_draw = sum(1 for p in targets if plan[p.pad_id] != "none")
    n_lv = len(VIA_DARKNESS_LEVELS)
    bag = [i % n_lv for i in range(n_draw)]
    rng.shuffle(bag)
    bag_it = iter(bag)

    # --- VIA 그리기 ---
    via_gt: List[Dict[str, Any]] = []
    for p in targets:
        mode = plan[p.pad_id]
        rad = float(np.clip(p.equiv_radius * VIA_RADIUS_RATIO, VIA_RADIUS_MIN, VIA_RADIUS_MAX))
        cx, cy = p.centroid
        # pad_id 는 이진화 임계값 변화로 재현이 어긋날 수 있으므로 pad_center 를 기준키로 함께 남긴다
        pad_center = [round(cx, 2), round(cy, 2)]
        if mode == "none":
            via_gt.append({"pad_id": p.pad_id, "pad_center": pad_center, "mode": "none"})
            continue
        if mode in ("center", "extra"):
            jx = rng.uniform(-VIA_CENTER_JITTER, VIA_CENTER_JITTER)
            jy = rng.uniform(-VIA_CENTER_JITTER, VIA_CENTER_JITTER)
            pos = (cx + jx, cy + jy)
        else:  # shift
            ang = rng.uniform(0, 2 * np.pi)
            mag = p.equiv_radius * rng.uniform(VIA_SHIFT_MIN, VIA_SHIFT_MAX)
            # PAD 밖으로 나가지 않게 제한
            mag = min(mag, max(p.equiv_radius - rad - 2.0, 1.0))
            pos = (cx + mag * np.cos(ang), cy + mag * np.sin(ang))
        lv = next(bag_it)
        draw_via(img, pos, rad, rng, VIA_DARKNESS_LEVELS[lv])
        via_gt.append({
            "pad_id": p.pad_id, "pad_center": pad_center, "mode": mode,
            "via_center": [round(pos[0], 2), round(pos[1], 2)],
            "radius": round(rad, 2),
            "offset_norm": round(float(np.hypot(pos[0] - cx, pos[1] - cy)) / p.equiv_radius, 4),
            "darkness": VIA_DARKNESS_LEVELS[lv],
            "darkness_level": VIA_DARKNESS_NAMES[lv],
        })
        if mode == "shift":
            injected.append({"kind": "VIA_OFFSET", "pad_id": p.pad_id,
                             "position": [round(pos[0], 2), round(pos[1], 2)]})
        elif mode == "extra":
            injected.append({"kind": "VIA_EXTRA", "pad_id": p.pad_id,
                             "position": [round(pos[0], 2), round(pos[1], 2)]})
    for p in targets:
        if plan[p.pad_id] == "none":
            injected.append({"kind": "VIA_MISSING", "pad_id": p.pad_id,
                             "position": [round(p.centroid[0], 2), round(p.centroid[1], 2)]})

    # ------------------------------------------------------------------
    # PAD 결함 주입
    # ------------------------------------------------------------------
    if scenario == "PAD_DEFECT":
        modes = ["erase", "add", "both"]
        weights = [0.45, 0.35, 0.20]
        mode = rng.choices(modes, weights=weights, k=1)[0]

        if mode in ("erase", "both"):
            # 지울 PAD: 경계에 안 걸리고 충분히 큰 것
            cands = [p for p in seg.pads
                     if not p.touches_border and p.area >= max(cfg.min_pad_area * 3, 200)]
            if cands:
                victim = cands[rng.randrange(len(cands))]
                erase_pad(img, seg.label_map, victim.pad_id, bg_mean, bg_std)
                injected.append({"kind": "MISSING_PAD", "pad_id": victim.pad_id,
                                 "position": [round(victim.centroid[0], 2),
                                              round(victim.centroid[1], 2)]})
                # 지워진 PAD 는 더 이상 존재하지 않으므로 VIA 정답에서도 제거한다.
                # (VIA 를 먼저 그린 뒤 PAD 를 지우므로 항목이 남아 있으면 오답으로 집계됨)
                via_gt = [v for v in via_gt if v["pad_id"] != victim.pad_id]
            elif mode == "erase":
                mode = "add"

        if mode in ("add", "both"):
            ref = targets[rng.randrange(len(targets))]
            pmean, pstd = _pad_color_stats(src, seg.label_map, ref.pad_id)
            rad = int(round(max(ref.equiv_radius * rng.uniform(0.75, 1.0), 6)))
            spot = find_free_spot(seg.pad_mask, rad, rng)
            if spot is not None:
                add_pad(img, spot, rad, pmean, pstd)
                injected.append({"kind": "EXTRA_PAD", "pad_id": -1,
                                 "position": [spot[0], spot[1]], "radius": rad})

    # ------------------------------------------------------------------
    # 정답 코드 결정 (code "24" 우선)
    #   expected_code        : 기본 검사(설계 VIA 대조 OFF) 기준 정답
    #   expected_code_design : 설계 VIA 대조 ON 기준 정답 (VIA_EXTRA 포함)
    # ------------------------------------------------------------------
    kinds = {d["kind"] for d in injected}

    def _code(with_design: bool) -> str:
        via_kinds = {"VIA_MISSING", "VIA_OFFSET"}
        if with_design:
            via_kinds = via_kinds | {"VIA_EXTRA"}
        if kinds & {"MISSING_PAD", "EXTRA_PAD"}:
            return "24"
        if kinds & via_kinds:
            return "99"
        return "1"

    return {
        "image": img,
        "cad": cad_mask,
        "via_cad": via_cad,
        "meta": meta,
        "gt": {
            "scenario": scenario,
            "expected_code": _code(False),
            "expected_code_design": _code(True),
            "via_target_count": len(targets),
            "via_plan": via_gt,
            "injected": injected,
        },
    }


# ----------------------------------------------------------------------------
# 미리보기
# ----------------------------------------------------------------------------
def make_preview(src: np.ndarray, test: np.ndarray, cad: np.ndarray) -> np.ndarray:
    cad3 = cv2.cvtColor(cad, cv2.COLOR_GRAY2BGR)
    gap = np.full((src.shape[0], 4, 3), 60, np.uint8)
    strip = np.hstack([src, gap, test, gap, cad3])
    header = np.zeros((18, strip.shape[1], 3), np.uint8)
    w = src.shape[1]
    for i, t in enumerate(["ORIGINAL", "TESTSET", "CAD"]):
        cv2.putText(header, t, (i * (w + 4) + 4, 13), cv2.FONT_HERSHEY_SIMPLEX, 0.4,
                    (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([header, strip])


# ----------------------------------------------------------------------------
# 메인
# ----------------------------------------------------------------------------
def main() -> int:
    ap = argparse.ArgumentParser(description="PAD/VIA 테스트셋 생성")
    ap.add_argument("--src", default="Images", help="원본 이미지 폴더")
    ap.add_argument("--out", default="testset", help="출력 폴더")
    ap.add_argument("--seed", type=int, default=20250728)
    ap.add_argument("--no-preview", action="store_true")
    a = ap.parse_args()

    rng = random.Random(a.seed)
    np.random.seed(a.seed)
    cfg = InspectConfig()

    files = sorted(glob.glob(os.path.join(a.src, "*.jpg")) +
                   glob.glob(os.path.join(a.src, "*.png")) +
                   glob.glob(os.path.join(a.src, "*.bmp")))
    if not files:
        print("원본 이미지가 없습니다:", a.src)
        return 1

    # --- 사용 가능한 이미지 선별 (VIA 대상 PAD가 있는 것만) ---
    usable: List[str] = []
    for f in files:
        img = imread_unicode(f, cv2.IMREAD_COLOR)
        if img is None:
            continue
        seg = step2_segment_pads(step1_preprocess(img, cfg), cfg)
        if any(is_via_target(p, cfg) for p in seg.pads):
            usable.append(f)
    print("총 %d장 중 VIA 대상 PAD 보유: %d장" % (len(files), len(usable)))
    if not usable:
        return 1

    # --- 시나리오 배분 ---
    n = len(usable)
    n_pad = max(1, int(round(n * RATIO_PAD)))
    n_via = max(1, int(round(n * RATIO_VIA)))
    n_good = n - n_pad - n_via
    scen = ["GOOD"] * n_good + ["VIA_DEFECT"] * n_via + ["PAD_DEFECT"] * n_pad
    rng.shuffle(scen)

    dirs = {k: os.path.join(a.out, k) for k in ("images", "design", "preview")}
    for d in dirs.values():
        os.makedirs(d, exist_ok=True)

    gt_all: Dict[str, Any] = {}
    counts: Dict[str, int] = {}
    for path, sc in zip(usable, scen):
        name = os.path.splitext(os.path.basename(path))[0]
        built = build_one(path, sc, rng, cfg)
        if built is None:
            print("  skip:", name)
            continue

        imwrite_unicode(os.path.join(dirs["images"], name + ".png"), built["image"])
        imwrite_unicode(os.path.join(dirs["design"], name + "_cad.png"), built["cad"])
        imwrite_unicode(os.path.join(dirs["design"], name + "_via.png"), built["via_cad"])
        with open(os.path.join(dirs["design"], name + "_cad.json"), "w", encoding="utf-8") as f:
            json.dump(built["meta"], f, ensure_ascii=False, indent=1)

        if not a.no_preview:
            src = imread_unicode(path, cv2.IMREAD_COLOR)
            imwrite_unicode(os.path.join(dirs["preview"], name + "_prev.png"),
                            make_preview(src, built["image"], built["cad"]))

        gt_all[name] = built["gt"]
        key = "%s(code %s)" % (built["gt"]["scenario"], built["gt"]["expected_code"])
        counts[key] = counts.get(key, 0) + 1

    with open(os.path.join(a.out, "ground_truth.json"), "w", encoding="utf-8") as f:
        json.dump(gt_all, f, ensure_ascii=False, indent=1)

    print("\n생성 완료: %s (%d장)" % (a.out, len(gt_all)))
    for k in sorted(counts):
        print("  %-22s %d" % (k, counts[k]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
