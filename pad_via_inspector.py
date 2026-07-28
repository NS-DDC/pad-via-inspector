# -*- coding: utf-8 -*-
"""
pad_via_inspector.py
====================
PCB PAD / VIA 자동 외관검사(ADC) 단일 파일 모듈.

이 파일 하나만 복사해서 다른 프로젝트에 붙여넣으면 바로 동작합니다.
(의존성: numpy, opencv-python 뿐. 프로젝트 내부 import 없음)

기능
----
1) 실제 이미지에서 PAD 영역 분할(segmentation)
2) 설계도(이진 PNG)와 대조하여 "있어야 할 PAD 누락" / "없어야 할 PAD 존재" 검출  -> code "24"
3) PAD 내부 VIA(작은 어두운 점)의 존재 및 정중앙 여부 판정                      -> code "99"
4) [옵션] VIA 설계도(이진 PNG)와 대조하여 "있어야 할 VIA 누락" /
         "없어야 할 VIA 존재" 검출                                             -> code "99"
5) 판정 결과 시각화 이미지 생성 (원본 스케일, 마커만 오버레이)

판정 코드는 문자열입니다.  "1"=양품 / "24"=PAD 결함 / "99"=VIA 결함 / "-1"=오류

사용법
------
[A] 한 번에 호출 (one-shot)
    from pad_via_inspector import inspect
    res = inspect("cur.jpg", "cad.png", "cad.json")
    print(res.code)                     # "1" / "24" / "99"
    cv2.imwrite("out.png", res.result_image)

    # VIA 설계도 대조까지 하려면 (옵션)
    cfg = InspectConfig(via_design_check=True)
    res = inspect("cur.jpg", "cad.png", "cad.json", via_design="cad_via.png", cfg=cfg)

[B] 단계별 호출 (중간 결과 확인용)
    from pad_via_inspector import (InspectConfig, step1_preprocess, step2_segment_pads,
                                   step3_check_pad_presence, step4_check_via, step5_render)
    cfg  = InspectConfig()
    pre  = step1_preprocess(bgr, cfg)                       # pre.binary 확인 가능
    seg  = step2_segment_pads(pre, cfg)                     # seg.pad_mask 확인 가능
    pad  = step3_check_pad_presence(seg, cad_mask, cfg)     # pad.overlay 확인 가능
    via  = step4_check_via(bgr, seg, pad, cad_meta, cfg)    # via.via_mask 확인 가능
    res  = step5_render(bgr, seg, pad, via, cfg)            # 최종 code / result_image

Python 3.9+
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple, Union

import cv2
import numpy as np

__all__ = [
    "CODE_OK", "CODE_PAD_DEFECT", "CODE_VIA_DEFECT", "CODE_ERROR",
    "InspectConfig", "PadInfo", "Defect",
    "PreprocessResult", "SegmentResult", "PadPresenceResult", "ViaResult", "InspectionResult",
    "step1_preprocess", "step2_segment_pads", "step3_check_pad_presence",
    "step4_check_via", "step5_render", "inspect",
    "segment_pad_mask", "build_design_mask", "build_via_design_mask", "is_via_target",
    "imread_unicode", "imwrite_unicode",
]

# ----------------------------------------------------------------------------
# 판정 코드 (문자열)
# ----------------------------------------------------------------------------
CODE_OK = "1"           # 양품
CODE_PAD_DEFECT = "24"  # PAD 누락 / PAD 과잉 (설계도 불일치)
CODE_VIA_DEFECT = "99"  # VIA 없음 / VIA 과잉 / VIA 편심(쏠림)
CODE_ERROR = "-1"       # 입력 오류 등 처리 실패


# ----------------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------------
@dataclass
class InspectConfig:
    """검사 파라미터. 필요한 값만 바꿔서 쓰면 됩니다."""

    # ---- STEP1 : 전처리 -------------------------------------------------
    blur_ksize: int = 3            # 가우시안 블러 커널 (0 이면 미사용). JPEG 노이즈 억제
    manual_thresh: int = -1        # >=0 이면 Otsu 대신 고정 임계값 사용
    otsu_bias: int = 0             # Otsu 임계값 보정 (+면 PAD가 작아짐)

    # ---- STEP2 : PAD 분할 ------------------------------------------------
    open_ksize: int = 3            # 잡티 제거용 열림 연산 커널
    close_ksize: int = 3           # 미세 끊김 메움 커널
    fill_holes: bool = True        # VIA 구멍을 메워 PAD를 온전한 덩어리로 만듦
    min_pad_area: int = 60         # 이보다 작은 덩어리는 노이즈로 간주

    # ---- STEP3 : PAD 유무 검사(code 24) ---------------------------------
    cad_coverage_thresh: float = 0.55   # 설계 PAD가 실측 PAD에 덮인 비율 하한 (미만이면 누락)
    extra_overlap_thresh: float = 0.30  # 실측 PAD가 설계 PAD와 겹친 비율 하한 (미만이면 과잉)
    min_extra_area: int = 120           # 이보다 작은 미매칭 덩어리는 과잉으로 보지 않음
    ignore_border_extra: bool = True    # 이미지 경계에 걸친 잘린 덩어리는 과잉 판정에서 제외

    # ---- VIA 대상 PAD 선별(설계도 JSON이 없을 때 자동 판별) -------------
    via_target_min_area: int = 150      # VIA가 들어갈 만한 PAD 최소 면적
    via_target_max_area: int = 4000     # VIA가 들어갈 만한 PAD 최대 면적
    via_target_min_circularity: float = 0.72   # 원형도 하한 (사각/배선 제외)
    via_target_exclude_border: bool = True     # 경계에 잘린 PAD 제외

    # ---- STEP4 : VIA 검사(code 99) --------------------------------------
    via_pad_erode: int = 1         # PAD 테두리 전이영역을 피하려고 안쪽으로 침식할 픽셀
    via_dark_ratio: float = 0.62   # PAD 내부 밝기 중앙값 * ratio 보다 어두우면 VIA 후보
    via_use_blackhat: bool = True  # Black-hat(국소 함몰) 조건 병행 -> PAD 테두리 오검출 억제
    via_blackhat_ksize_ratio: float = 0.85  # Black-hat 커널 크기 = PAD 등가반지름 * 이 값
    via_blackhat_min: float = 12.0          # Black-hat 응답 절대 하한(gray level)
    via_blackhat_ratio: float = 0.18        # Black-hat 응답 상대 하한 = PAD 중앙값 * 이 값
    via_min_blob: int = 4          # VIA 최소 면적(px)
    via_max_blob_ratio: float = 0.25   # VIA 면적 / PAD 면적 상한 (초과 시 얼룩으로 간주)
    via_offset_tol: float = 0.25   # 편심 허용치. PAD 등가반지름 대비 비율
    via_offset_min_px: float = 2.2  # 절대 하한(px). 작은 PAD에서 픽셀 양자화로 오검출되는 것 방지
    via_weighted_center: bool = True    # 밝기 가중 중심(서브픽셀)으로 VIA 중심 추정

    # ---- STEP4 옵션 : VIA 설계도 대조 -----------------------------------
    # True 로 켜면 VIA 설계 마스크(PNG)를 추가 입력으로 받아
    #   - 설계에 있는데 실물에 없음 -> VIA_MISSING
    #   - 설계에 없는데 실물에 있음 -> VIA_EXTRA
    # 를 판정한다. False(기본)면 기존처럼 "VIA 대상 PAD"에 대해서만 존재/편심을 본다.
    via_design_check: bool = False
    via_design_min_area: int = 2   # 설계 VIA 점으로 인정할 최소 면적(px)

    # ---- STEP5 : 시각화 (원본 스케일에 마커만) ---------------------------
    draw_overlay: bool = True      # False 면 원본 이미지를 그대로 반환

    # ---- 기타 ------------------------------------------------------------
    priority: Tuple[str, ...] = (CODE_PAD_DEFECT, CODE_VIA_DEFECT)  # 앞에 있는 코드가 우선


# ----------------------------------------------------------------------------
# 자료구조
# ----------------------------------------------------------------------------
@dataclass
class PadInfo:
    """검출된 PAD 하나의 정보."""
    pad_id: int
    area: int
    bbox: Tuple[int, int, int, int]        # x, y, w, h
    centroid: Tuple[float, float]          # cx, cy (subpixel)
    perimeter: float = 0.0
    circularity: float = 0.0
    equiv_radius: float = 0.0              # sqrt(area / pi)
    touches_border: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pad_id": self.pad_id,
            "area": int(self.area),
            "bbox": [int(v) for v in self.bbox],
            "centroid": [round(float(self.centroid[0]), 2), round(float(self.centroid[1]), 2)],
            "perimeter": round(float(self.perimeter), 2),
            "circularity": round(float(self.circularity), 4),
            "equiv_radius": round(float(self.equiv_radius), 3),
            "touches_border": bool(self.touches_border),
        }


@dataclass
class Defect:
    """검출된 결함 하나."""
    code: str
    # MISSING_PAD / EXTRA_PAD / VIA_MISSING / VIA_EXTRA / VIA_OFFSET
    kind: str
    pad_id: int
    position: Tuple[float, float]
    detail: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "code": self.code,
            "kind": self.kind,
            "pad_id": self.pad_id,
            "position": [round(float(self.position[0]), 2), round(float(self.position[1]), 2)],
            "detail": self.detail,
        }


@dataclass
class PreprocessResult:
    """STEP1 결과."""
    bgr: np.ndarray
    gray: np.ndarray
    blurred: np.ndarray
    binary: np.ndarray        # PAD=255, 배경=0
    threshold: float


@dataclass
class SegmentResult:
    """STEP2 결과."""
    pad_mask: np.ndarray      # 구멍 메워진 최종 PAD 마스크 (255/0)
    label_map: np.ndarray     # 각 픽셀의 pad_id (0=배경)
    pads: List[PadInfo]
    hole_mask: np.ndarray     # 메워진 구멍(=VIA 후보) 마스크. 참고용


@dataclass
class PadPresenceResult:
    """STEP3 결과."""
    cad_mask: np.ndarray
    cad_pads: List[PadInfo]
    cad_label_map: np.ndarray
    matches: Dict[int, int]           # cad_pad_id -> actual_pad_id (매칭된 것만)
    missing: List[Defect]             # 설계엔 있는데 실물에 없음
    extra: List[Defect]               # 설계엔 없는데 실물에 있음
    coverage: Dict[int, float]        # cad_pad_id -> 덮임 비율
    overlay: np.ndarray               # 중간 확인용 시각화


@dataclass
class ViaResult:
    """STEP4 결과."""
    via_mask: np.ndarray              # 검출된 VIA 픽셀 마스크
    findings: List[Dict[str, Any]]    # PAD별 VIA 판정 상세
    defects: List[Defect]
    target_pad_ids: List[int]         # VIA 검사 대상 PAD


@dataclass
class InspectionResult:
    """최종 결과."""
    code: str                          # "1" / "24" / "99" / "-1"
    codes: List[str]                   # 발생한 모든 코드 (중복 제거, 우선순위 순)
    ok: bool
    defects: List[Defect]
    result_image: np.ndarray
    pads: List[PadInfo] = field(default_factory=list)
    stages: Dict[str, Any] = field(default_factory=dict)   # 중간 단계 객체 보관
    message: str = ""

    # ---- 요약 ------------------------------------------------------------
    def summary(self) -> Dict[str, Any]:
        counts: Dict[str, int] = {}
        for d in self.defects:
            counts[d.kind] = counts.get(d.kind, 0) + 1
        return {
            "code": self.code,
            "codes": self.codes,
            "ok": self.ok,
            "pad_count": len(self.pads),
            "defect_count": len(self.defects),
            "defect_kinds": counts,
            "message": self.message,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(
            {"summary": self.summary(), "defects": [d.to_dict() for d in self.defects]},
            ensure_ascii=False, indent=indent,
        )


# ----------------------------------------------------------------------------
# 한글/유니코드 경로 대응 I/O
# ----------------------------------------------------------------------------
def imread_unicode(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread 가 유니코드 경로에서 실패하는 문제를 우회."""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, flags)


def imwrite_unicode(path: str, img: np.ndarray) -> bool:
    """cv2.imwrite 유니코드 경로 우회."""
    ext = os.path.splitext(path)[1]
    if not ext:
        ext = ".png"
    ok, buf = cv2.imencode(ext, img)
    if not ok:
        return False
    d = os.path.dirname(path)
    if d:
        os.makedirs(d, exist_ok=True)
    buf.tofile(path)
    return True


def _as_bgr(src: Union[str, np.ndarray]) -> np.ndarray:
    if isinstance(src, str):
        img = imread_unicode(src, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError("이미지를 읽을 수 없습니다: %s" % src)
        return img
    img = np.asarray(src)
    if img.ndim == 2:
        return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    if img.ndim == 3 and img.shape[2] == 4:
        return cv2.cvtColor(img, cv2.COLOR_BGRA2BGR)
    return img


def _as_mask(src: Union[str, np.ndarray]) -> np.ndarray:
    if isinstance(src, str):
        img = imread_unicode(src, cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError("설계도를 읽을 수 없습니다: %s" % src)
    else:
        img = np.asarray(src)
        if img.ndim == 3:
            img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    return np.where(img > 127, 255, 0).astype(np.uint8)


def _as_meta(src: Union[None, str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if src is None:
        return None
    if isinstance(src, dict):
        return src
    if not os.path.exists(src):
        return None
    with open(src, "r", encoding="utf-8") as f:
        return json.load(f)


# ----------------------------------------------------------------------------
# 공통 유틸
# ----------------------------------------------------------------------------
def _fill_holes(mask: np.ndarray) -> Tuple[np.ndarray, np.ndarray]:
    """이진 마스크의 내부 구멍을 메운다. (filled_mask, hole_mask) 반환.

    배경(0)에서 flood fill 로 외부 배경만 칠한 뒤 남은 0 픽셀이 내부 구멍.
    """
    h, w = mask.shape[:2]
    inv = np.where(mask > 0, 0, 255).astype(np.uint8)     # 배경=255
    ff = inv.copy()
    ff_mask = np.zeros((h + 2, w + 2), np.uint8)
    # 테두리 픽셀 중 배경인 곳을 모두 시드로 사용 (모서리가 PAD일 수 있으므로)
    for x in range(w):
        if ff[0, x] == 255:
            cv2.floodFill(ff, ff_mask, (x, 0), 128)
        if ff[h - 1, x] == 255:
            cv2.floodFill(ff, ff_mask, (x, h - 1), 128)
    for y in range(h):
        if ff[y, 0] == 255:
            cv2.floodFill(ff, ff_mask, (0, y), 128)
        if ff[y, w - 1] == 255:
            cv2.floodFill(ff, ff_mask, (w - 1, y), 128)
    holes = np.where(ff == 255, 255, 0).astype(np.uint8)  # 외부와 연결 안 된 배경 = 내부 구멍
    filled = np.where((mask > 0) | (holes > 0), 255, 0).astype(np.uint8)
    return filled, holes


def _touches_border(x: int, y: int, w: int, h: int, W: int, H: int, margin: int = 0) -> bool:
    return x <= margin or y <= margin or (x + w) >= (W - margin) or (y + h) >= (H - margin)


def _describe_components(mask: np.ndarray, min_area: int) -> Tuple[np.ndarray, List[PadInfo]]:
    """연결요소 라벨링 + 각 요소의 기하 특징 계산."""
    H, W = mask.shape[:2]
    num, labels, stats, cents = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    out_label = np.zeros_like(labels, dtype=np.int32)
    pads: List[PadInfo] = []
    next_id = 1
    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if area < min_area:
            continue
        comp = (labels == i).astype(np.uint8)
        cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
        perim = cv2.arcLength(cnts[0], True) if cnts else 0.0
        circ = float(4.0 * np.pi * area / (perim * perim)) if perim > 1e-6 else 0.0
        circ = min(circ, 1.0)
        pads.append(PadInfo(
            pad_id=next_id,
            area=int(area),
            bbox=(int(x), int(y), int(w), int(h)),
            centroid=(float(cents[i][0]), float(cents[i][1])),
            perimeter=float(perim),
            circularity=circ,
            equiv_radius=float(np.sqrt(area / np.pi)),
            touches_border=_touches_border(x, y, w, h, W, H),
        ))
        out_label[labels == i] = next_id
        next_id += 1
    return out_label, pads


def is_via_target(pad: PadInfo, cfg: InspectConfig) -> bool:
    """이 PAD가 VIA 검사 대상인지 형상 기준으로 판별."""
    if cfg.via_target_exclude_border and pad.touches_border:
        return False
    if not (cfg.via_target_min_area <= pad.area <= cfg.via_target_max_area):
        return False
    if pad.circularity < cfg.via_target_min_circularity:
        return False
    return True


# ----------------------------------------------------------------------------
# STEP 1 : 전처리 + 이진화
# ----------------------------------------------------------------------------
def step1_preprocess(image: Union[str, np.ndarray],
                     cfg: Optional[InspectConfig] = None) -> PreprocessResult:
    """그레이 변환 -> 블러 -> 이진화(PAD=흰색).

    PAD는 배경보다 밝다는 전제(구리/도금 PAD, 어두운 기판)를 사용합니다.
    """
    cfg = cfg or InspectConfig()
    bgr = _as_bgr(image)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)

    if cfg.blur_ksize and cfg.blur_ksize >= 3:
        k = cfg.blur_ksize | 1                     # 홀수 보정
        blurred = cv2.GaussianBlur(gray, (k, k), 0)
    else:
        blurred = gray.copy()

    if cfg.manual_thresh >= 0:
        thr = float(cfg.manual_thresh)
        _, binary = cv2.threshold(blurred, thr, 255, cv2.THRESH_BINARY)
    else:
        thr, _ = cv2.threshold(blurred, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        thr = float(thr) + cfg.otsu_bias
        _, binary = cv2.threshold(blurred, thr, 255, cv2.THRESH_BINARY)

    return PreprocessResult(bgr=bgr, gray=gray, blurred=blurred,
                            binary=binary.astype(np.uint8), threshold=thr)


# ----------------------------------------------------------------------------
# STEP 2 : PAD 분할
# ----------------------------------------------------------------------------
def step2_segment_pads(pre: PreprocessResult,
                       cfg: Optional[InspectConfig] = None) -> SegmentResult:
    """형태학 정리 -> 구멍 메움 -> 연결요소 라벨링."""
    cfg = cfg or InspectConfig()
    m = pre.binary.copy()

    if cfg.open_ksize >= 3:
        k = np.ones((cfg.open_ksize, cfg.open_ksize), np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_OPEN, k)
    if cfg.close_ksize >= 3:
        k = np.ones((cfg.close_ksize, cfg.close_ksize), np.uint8)
        m = cv2.morphologyEx(m, cv2.MORPH_CLOSE, k)

    if cfg.fill_holes:
        filled, holes = _fill_holes(m)
    else:
        filled, holes = m, np.zeros_like(m)

    label_map, pads = _describe_components(filled, cfg.min_pad_area)
    pad_mask = np.where(label_map > 0, 255, 0).astype(np.uint8)
    return SegmentResult(pad_mask=pad_mask, label_map=label_map, pads=pads, hole_mask=holes)


def segment_pad_mask(image: Union[str, np.ndarray],
                     cfg: Optional[InspectConfig] = None) -> np.ndarray:
    """이미지 -> PAD 마스크 (단축 헬퍼)."""
    cfg = cfg or InspectConfig()
    return step2_segment_pads(step1_preprocess(image, cfg), cfg).pad_mask


def build_design_mask(pad_mask: np.ndarray, erode_px: int = 2) -> np.ndarray:
    """실측 PAD 마스크로부터 설계도 마스크를 만든다.

    설계도는 실제보다 PAD 영역이 '살짝 작게' 정의되므로 침식(erode)합니다.
    침식으로 사라지는 작은 PAD는 보호하기 위해 원본 대비 잔존 면적을 확인합니다.
    """
    if erode_px <= 0:
        return pad_mask.copy()
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * erode_px + 1, 2 * erode_px + 1))
    eroded = cv2.erode(pad_mask, k)

    # 침식으로 완전히 소멸한 PAD는 1px 침식본으로 대체 (설계 정보 손실 방지)
    num, labels, stats, _ = cv2.connectedComponentsWithStats((pad_mask > 0).astype(np.uint8), 8)
    k1 = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
    weak = cv2.erode(pad_mask, k1)
    out = eroded.copy()
    for i in range(1, num):
        comp = labels == i
        if eroded[comp].max() == 0:
            out[comp & (weak > 0)] = 255
    return out


def build_via_design_mask(shape: Tuple[int, int],
                          via_centers: List[Tuple[float, float]],
                          radius: int = 2) -> np.ndarray:
    """VIA 설계 마스크(이진 PNG용 배열)를 만든다.

    shape       : (H, W)
    via_centers : 설계상 VIA 가 있어야 할 좌표 [(cx, cy), ...]
    radius      : 찍을 점의 반지름(px). 위치만 쓰이므로 작아도 된다.
    """
    H, W = int(shape[0]), int(shape[1])
    mask = np.zeros((H, W), np.uint8)
    for cx, cy in via_centers:
        cv2.circle(mask, (int(round(cx)), int(round(cy))), max(1, int(radius)), 255, -1)
    return mask


def _parse_via_design(via_design: Union[str, np.ndarray],
                      cad_label_map: np.ndarray,
                      cfg: InspectConfig) -> Dict[int, Tuple[float, float]]:
    """VIA 설계 마스크 -> {cad_pad_id: (cx, cy)}.

    설계 VIA 점의 무게중심이 어느 설계 PAD 안에 들어가는지로 소속을 정한다.
    PAD 밖에 찍힌 점은 무시한다(설계 오류로 간주).
    """
    mask = _as_mask(via_design)
    if mask.shape[:2] != cad_label_map.shape[:2]:
        mask = cv2.resize(mask, (cad_label_map.shape[1], cad_label_map.shape[0]),
                          interpolation=cv2.INTER_NEAREST)
    num, labels, stats, cents = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    out: Dict[int, Tuple[float, float]] = {}
    H, W = cad_label_map.shape[:2]
    for i in range(1, num):
        if stats[i][4] < cfg.via_design_min_area:
            continue
        cx, cy = float(cents[i][0]), float(cents[i][1])
        ix = int(np.clip(round(cx), 0, W - 1))
        iy = int(np.clip(round(cy), 0, H - 1))
        pid = int(cad_label_map[iy, ix])
        if pid <= 0:
            continue
        out[pid] = (cx, cy)
    return out


# ----------------------------------------------------------------------------
# STEP 3 : PAD 유무 검사 (code 24)
# ----------------------------------------------------------------------------
def step3_check_pad_presence(seg: SegmentResult,
                             cad_mask: Union[str, np.ndarray],
                             cfg: Optional[InspectConfig] = None) -> PadPresenceResult:
    """설계도 대비 PAD 누락/과잉 검출.

    - 누락(MISSING_PAD): 설계 PAD 픽셀이 실측 PAD 마스크에 덮인 비율 < cad_coverage_thresh
    - 과잉(EXTRA_PAD)  : 실측 PAD 픽셀이 설계 PAD 마스크와 겹친 비율 < extra_overlap_thresh
                         (설계도는 침식되어 있으므로 정상 PAD도 100% 겹치지는 않음 -> 낮은 임계값 사용)
    """
    cfg = cfg or InspectConfig()
    cadm = _as_mask(cad_mask)
    if cadm.shape[:2] != seg.pad_mask.shape[:2]:
        raise ValueError("설계도 크기(%s)와 이미지 크기(%s)가 다릅니다."
                         % (cadm.shape[:2], seg.pad_mask.shape[:2]))

    cad_label, cad_pads = _describe_components(cadm, max(8, cfg.min_pad_area // 3))
    actual = seg.pad_mask > 0
    H, W = actual.shape[:2]

    coverage: Dict[int, float] = {}
    matches: Dict[int, int] = {}
    missing: List[Defect] = []

    matched_actual_ids = set()

    # --- 설계 PAD 기준: 누락 검사 ---
    for p in cad_pads:
        region = cad_label == p.pad_id
        inter = np.count_nonzero(region & actual)
        cov = inter / float(p.area) if p.area else 0.0
        coverage[p.pad_id] = cov
        if cov < cfg.cad_coverage_thresh:
            missing.append(Defect(
                code=CODE_PAD_DEFECT, kind="MISSING_PAD", pad_id=p.pad_id,
                position=p.centroid,
                detail={"coverage": round(cov, 4),
                        "threshold": cfg.cad_coverage_thresh,
                        "cad_area": p.area},
            ))
        else:
            # 겹친 픽셀에서 가장 많이 차지하는 실측 PAD와 매칭
            ids, counts = np.unique(seg.label_map[region & actual], return_counts=True)
            keep = ids > 0
            if keep.any():
                best = int(ids[keep][np.argmax(counts[keep])])
                matches[p.pad_id] = best
                matched_actual_ids.add(best)

    # --- 실측 PAD 기준: 과잉 검사 ---
    cad_bool = cadm > 0
    extra: List[Defect] = []
    for p in seg.pads:
        region = seg.label_map == p.pad_id
        inter = np.count_nonzero(region & cad_bool)
        ratio = inter / float(p.area) if p.area else 0.0
        if p.pad_id in matched_actual_ids:
            continue
        if ratio >= cfg.extra_overlap_thresh:
            continue
        if p.area < cfg.min_extra_area:
            continue
        if cfg.ignore_border_extra and p.touches_border:
            continue
        extra.append(Defect(
            code=CODE_PAD_DEFECT, kind="EXTRA_PAD", pad_id=p.pad_id,
            position=p.centroid,
            detail={"overlap_ratio": round(ratio, 4),
                    "threshold": cfg.extra_overlap_thresh,
                    "area": p.area},
        ))

    # --- 중간 확인용 오버레이 ---
    overlay = np.zeros((H, W, 3), np.uint8)
    overlay[..., 1] = np.where(actual, 90, 0)          # 실측 = 초록
    overlay[..., 2] = np.where(cad_bool, 160, 0)       # 설계 = 빨강
    for d in missing:
        cv2.circle(overlay, (int(d.position[0]), int(d.position[1])), 9, (0, 0, 255), 2)
    for d in extra:
        cv2.circle(overlay, (int(d.position[0]), int(d.position[1])), 9, (255, 0, 255), 2)

    return PadPresenceResult(cad_mask=cadm, cad_pads=cad_pads, cad_label_map=cad_label,
                             matches=matches, missing=missing, extra=extra,
                             coverage=coverage, overlay=overlay)


# ----------------------------------------------------------------------------
# STEP 4 : VIA 검사 (code 99)
# ----------------------------------------------------------------------------
def step4_check_via(image: Union[str, np.ndarray],
                    seg: SegmentResult,
                    pres: Optional[PadPresenceResult] = None,
                    cad_meta: Union[None, str, Dict[str, Any]] = None,
                    cfg: Optional[InspectConfig] = None,
                    via_design: Union[None, str, np.ndarray] = None) -> ViaResult:
    """PAD별 VIA 존재/편심 판정.

    대상 PAD 결정
      - [옵션] cfg.via_design_check=True 이고 via_design(VIA 설계 마스크)이 주어지면
        '설계에 VIA 가 있는 PAD' + '형상상 VIA 대상 PAD' 를 모두 검사한다.
        전자는 VIA 누락(VIA_MISSING)을, 후자는 VIA 과잉(VIA_EXTRA)을 잡기 위함이다.
      - cad_meta(JSON)에 via_expected 정보가 있으면 그것을 사용 (설계 기준)
      - 없으면 형상 휴리스틱(is_via_target)으로 자동 선별

    VIA 검출 (두 조건의 AND)
      (1) 전역 조건 : PAD 내부 밝기 중앙값 * via_dark_ratio 보다 어두움
      (2) 국소 조건 : Black-hat 응답이 큼 (= 주변보다 움푹 들어간 작은 어두운 점)
      Black-hat 을 함께 쓰는 이유는 PAD 테두리의 어두운 전이영역을 배제하기 위해서다.
      테두리는 '단조 경사'라 Black-hat 응답이 거의 0 이지만, VIA 는 '고립된 우물'이라
      응답이 크다. 덕분에 PAD 를 크게 침식하지 않아도 되고,
      가장자리로 심하게 쏠린 VIA 도 놓치지 않는다.
      마지막으로 면적 조건을 만족하는 가장 큰 덩어리를 VIA 로 채택.

    편심 판정
      - offset = ||VIA중심 - PAD중심|| / PAD등가반지름
      - offset > via_offset_tol 이면 VIA_OFFSET

    설계 대조 옵션(cfg.via_design_check=True) 판정표
      설계  실물   결과
      ----  ----   ----------------------------------
       O     O     정중앙이면 OK, 쏠리면 VIA_OFFSET
       O     X     VIA_MISSING
       X     O     VIA_EXTRA
       X     X     OK (VIA 없어야 하고 실제로 없음)
    """
    cfg = cfg or InspectConfig()
    bgr = _as_bgr(image)
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray = cv2.GaussianBlur(gray, (3, 3), 0)
    H, W = gray.shape[:2]

    meta = _as_meta(cad_meta)
    pad_by_id = {p.pad_id: p for p in seg.pads}

    # ---- 실측 PAD id -> 설계 PAD id 역매핑 ----
    cad_of_actual: Dict[int, int] = {}
    if pres is not None:
        for cad_id, act_id in pres.matches.items():
            cad_of_actual[act_id] = cad_id
    cad_erode_px = int(meta.get("erode_px", 0)) if meta else 0

    # ---- [옵션] VIA 설계 마스크 파싱 ----
    design_vias: Dict[int, Tuple[float, float]] = {}   # cad_pad_id -> 설계 VIA 좌표
    use_design = bool(cfg.via_design_check and via_design is not None and pres is not None)
    if use_design:
        design_vias = _parse_via_design(via_design, pres.cad_label_map, cfg)

    # ---- 검사 대상 PAD 결정 ----
    target_ids: List[int] = []
    if use_design:
        # 설계에 VIA 가 있는 PAD (누락 검출용)
        for cad_id, act_id in pres.matches.items():
            if cad_id in design_vias and act_id in pad_by_id:
                target_ids.append(act_id)
        # 형상상 VIA 대상인 PAD (과잉 검출용).
        # 사각/테두리 PAD 까지 넣으면 조명 얼룩을 VIA_EXTRA 로 오검출하므로 제외한다.
        for p in seg.pads:
            if is_via_target(p, cfg):
                target_ids.append(p.pad_id)
        target_ids = sorted(set(target_ids))
    elif meta is not None and pres is not None and meta.get("pads"):
        expected_ids = {int(m["pad_id"]) for m in meta["pads"] if m.get("via_expected")}
        for cad_id, act_id in pres.matches.items():
            if cad_id in expected_ids and act_id in pad_by_id:
                target_ids.append(act_id)
        target_ids = sorted(set(target_ids))
    else:
        target_ids = [p.pad_id for p in seg.pads if is_via_target(p, cfg)]

    via_mask = np.zeros((H, W), np.uint8)
    findings: List[Dict[str, Any]] = []
    defects: List[Defect] = []

    ek = cv2.getStructuringElement(
        cv2.MORPH_ELLIPSE, (2 * max(cfg.via_pad_erode, 0) + 1, 2 * max(cfg.via_pad_erode, 0) + 1))

    for pid in target_ids:
        pad = pad_by_id[pid]
        cad_id = cad_of_actual.get(pid)
        # 설계 대조 모드가 아니면 '모든 대상 PAD 에 VIA 가 있어야 한다'가 기본 전제
        expected = (cad_id in design_vias) if use_design else True

        # ------------------------------------------------------------------
        # 기준 형상(reference shape) 결정
        #
        #   설계도가 있으면 '설계 PAD'를 기준으로 삼는다. 이유:
        #     - VIA 가 가장자리로 심하게 쏠리면 어두운 VIA 가 배경과 이어져
        #       실측 PAD 윤곽에 노치가 파이고, 중심/검색영역이 오염된다.
        #     - '정중앙'의 정의 자체가 설계 중심이므로 기준으로 더 타당하다.
        #   설계도가 없으면 실측 PAD(구멍 메움)를 그대로 사용한다.
        # ------------------------------------------------------------------
        use_cad = (pres is not None and cad_id is not None)
        src_mask = (pres.cad_label_map == cad_id) if use_cad else (seg.label_map == pid)

        ys, xs = np.nonzero(src_mask)
        if ys.size == 0:
            continue
        mg = cad_erode_px + cfg.via_pad_erode + 4
        pad0, pad1 = max(int(xs.min()) - mg, 0), min(int(xs.max()) + mg + 1, W)
        pad2, pad3 = max(int(ys.min()) - mg, 0), min(int(ys.max()) + mg + 1, H)

        roi_gray = gray[pad2:pad3, pad0:pad1]
        shape = (src_mask[pad2:pad3, pad0:pad1]).astype(np.uint8) * 255

        # 설계 PAD 는 실제보다 erode_px 만큼 작으므로 공칭 크기로 되돌린다
        if use_cad and cad_erode_px > 0:
            dk = cv2.getStructuringElement(
                cv2.MORPH_ELLIPSE, (2 * cad_erode_px + 1, 2 * cad_erode_px + 1))
            shape = cv2.dilate(shape, dk)

        inner = cv2.erode(shape, ek) if cfg.via_pad_erode > 0 else shape

        m = cv2.moments((shape > 0).astype(np.uint8), binaryImage=True)
        if m["m00"] <= 0:
            continue
        cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        shape_area = float(m["m00"])
        radius = float(np.sqrt(shape_area / np.pi))
        center = (cx + pad0, cy + pad2)

        base = {
            "pad_id": pid,
            "cad_pad_id": cad_id,
            "reference": "cad" if use_cad else "actual",
            "pad_center": [round(float(center[0]), 2), round(float(center[1]), 2)],
            "pad_area": int(shape_area),
            "pad_radius": round(radius, 2),
            "via_expected": bool(expected),
        }

        if np.count_nonzero(inner) < cfg.via_min_blob * 4:
            findings.append(dict(base, status="SKIP", reason="PAD too small after erosion"))
            continue

        vals = roi_gray[inner > 0]
        med = float(np.median(vals))
        thr = med * cfg.via_dark_ratio
        dark = (roi_gray < thr) & (inner > 0)

        if cfg.via_use_blackhat:
            # PAD 바깥(어두운 배경)을 PAD 중앙값으로 메운 뒤 Black-hat 을 건다.
            # 그대로 두면 가장자리로 쏠린 VIA 주변에서 closing 이 배경 어둠에 끌려
            # 내려가 Black-hat 응답이 사라진다(= 쏠린 VIA 를 놓침).
            roi_fill = np.where(shape > 0, roi_gray, np.uint8(round(med)))
            # 커널은 VIA 보다 크고 PAD 보다 작아야 한다 -> PAD 반지름에 비례시킴
            ks = int(round(radius * cfg.via_blackhat_ksize_ratio)) | 1
            ks = int(np.clip(ks, 5, 31))
            se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
            bh = cv2.morphologyEx(roi_fill, cv2.MORPH_BLACKHAT, se).astype(np.float32)
            bh_thr = max(float(cfg.via_blackhat_min), med * cfg.via_blackhat_ratio)
            cand = (dark & (bh > bh_thr) & (inner > 0)).astype(np.uint8)
        else:
            cand = dark.astype(np.uint8)

        # 여기서 MORPH_OPEN 으로 잡티를 지우면 안 된다.
        # 대비가 약한 VIA(코어 밝기 = PAD 밝기의 0.45배)는 임계 아래로 내려가는 화소가
        # 4~5개뿐이고 모양도 십자/대각이라 2x2 로 열면 통째로 사라진다(= 오검출 VIA 없음).
        # 잡티 제거는 아래 연결성분 면적 하한(cfg.via_min_blob)이 이미 담당한다.

        n, lab, st, ct = cv2.connectedComponentsWithStats(cand, 8)
        max_area = max(int(shape_area * cfg.via_max_blob_ratio), cfg.via_min_blob)
        best_i, best_a = -1, 0
        for i in range(1, n):
            a = int(st[i, 4])
            if a < cfg.via_min_blob or a > max_area:
                continue
            if a > best_a:
                best_i, best_a = i, a

        # ---- VIA 없음 ----
        if best_i < 0:
            if not expected:
                # 설계에도 없고 실물에도 없음 -> 정상
                findings.append(dict(base, status="OK", reason="no via by design"))
                continue
            findings.append(dict(base, status="VIA_MISSING",
                                 pad_median=round(med, 1), dark_threshold=round(thr, 1)))
            defects.append(Defect(
                code=CODE_VIA_DEFECT, kind="VIA_MISSING", pad_id=pid, position=center,
                detail={"pad_median": round(med, 1), "dark_threshold": round(thr, 1)},
            ))
            continue

        blob = lab == best_i
        via_mask[pad2:pad3, pad0:pad1][blob] = 255

        if cfg.via_weighted_center:
            # 이진 중심은 픽셀 양자화 오차가 커서 작은 VIA에서 흔들린다.
            # '어두운 정도'를 가중치로 쓴 무게중심이 훨씬 안정적(서브픽셀).
            wgt = np.clip(thr - roi_gray.astype(np.float32), 0.0, None) * blob
            tot = float(wgt.sum())
            if tot > 1e-6:
                gy, gx = np.mgrid[0:roi_gray.shape[0], 0:roi_gray.shape[1]].astype(np.float32)
                vx = float((gx * wgt).sum() / tot)
                vy = float((gy * wgt).sum() / tot)
            else:
                vx, vy = float(ct[best_i][0]), float(ct[best_i][1])
        else:
            vx, vy = float(ct[best_i][0]), float(ct[best_i][1])

        gvx, gvy = vx + pad0, vy + pad2
        dist = float(np.hypot(vx - cx, vy - cy))
        norm = dist / radius if radius > 1e-6 else 999.0

        rec = dict(base,
                   via_center=[round(gvx, 2), round(gvy, 2)],
                   via_area=int(best_a),
                   offset_px=round(dist, 2),
                   offset_norm=round(norm, 4),
                   tolerance=cfg.via_offset_tol,
                   tolerance_px=cfg.via_offset_min_px)

        # ---- VIA 과잉 (설계엔 없는데 실물에 있음) ----
        if not expected:
            rec["status"] = "VIA_EXTRA"
            findings.append(rec)
            defects.append(Defect(
                code=CODE_VIA_DEFECT, kind="VIA_EXTRA", pad_id=pid, position=(gvx, gvy),
                detail={"via_area": int(best_a),
                        "pad_center": [round(float(center[0]), 2), round(float(center[1]), 2)]},
            ))
            continue

        # ---- VIA 편심 ----
        # 상대 허용치와 절대 하한을 모두 넘어야 불량. 작은 PAD의 양자화 오차를 흡수한다.
        if norm > cfg.via_offset_tol and dist > cfg.via_offset_min_px:
            rec["status"] = "VIA_OFFSET"
            findings.append(rec)
            defects.append(Defect(
                code=CODE_VIA_DEFECT, kind="VIA_OFFSET", pad_id=pid, position=(gvx, gvy),
                detail={"offset_px": round(dist, 2), "offset_norm": round(norm, 4),
                        "tolerance": cfg.via_offset_tol,
                        "pad_center": [round(float(center[0]), 2), round(float(center[1]), 2)]},
            ))
        else:
            rec["status"] = "OK"
            findings.append(rec)

    return ViaResult(via_mask=via_mask, findings=findings, defects=defects,
                     target_pad_ids=target_ids)


# ----------------------------------------------------------------------------
# STEP 5 : 판정 종합 + 시각화
# ----------------------------------------------------------------------------
_COLOR_OK = (0, 220, 0)          # 초록  : 정상 PAD
_COLOR_MISSING = (0, 0, 255)     # 빨강  : PAD 누락
_COLOR_EXTRA = (255, 0, 255)     # 자홍  : PAD 과잉
_COLOR_VIA_OFF = (0, 200, 255)   # 주황  : VIA 편심
_COLOR_VIA_MISS = (255, 128, 0)  # 파랑  : VIA 없음
_COLOR_VIA_OK = (0, 255, 255)    # 노랑  : VIA 정상
_COLOR_VIA_EXTRA = (255, 0, 128) # 보라  : VIA 과잉


def step5_render(image: Union[str, np.ndarray],
                 seg: SegmentResult,
                 pres: Optional[PadPresenceResult],
                 via: Optional[ViaResult],
                 cfg: Optional[InspectConfig] = None) -> InspectionResult:
    """모든 결함을 종합해 최종 코드 산출 + 결과 이미지 렌더링.

    결과 이미지는 '원본과 동일한 크기/스케일'이며 텍스트 바 없이 마커만 그린다.
    코드/메시지는 InspectionResult.code / .message 로 받는다.
    """
    cfg = cfg or InspectConfig()
    bgr = _as_bgr(image)
    vis = bgr.copy()

    defects: List[Defect] = []
    if pres is not None:
        defects.extend(pres.missing)
        defects.extend(pres.extra)
    if via is not None:
        defects.extend(via.defects)

    bad_pad_ids = {d.pad_id for d in defects if d.kind == "EXTRA_PAD"}
    via_bad_ids = {d.pad_id for d in defects
                   if d.kind in ("VIA_MISSING", "VIA_OFFSET", "VIA_EXTRA")}

    if cfg.draw_overlay:
        # 정상 PAD 외곽선
        for p in seg.pads:
            comp = (seg.label_map == p.pad_id).astype(np.uint8)
            cnts, _ = cv2.findContours(comp, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            if p.pad_id in bad_pad_ids:
                color = _COLOR_EXTRA
            elif p.pad_id in via_bad_ids:
                color = _COLOR_VIA_OFF
            else:
                color = _COLOR_OK
            cv2.drawContours(vis, cnts, -1, color, 1)

        # 설계도 윤곽 (얇은 회색)
        if pres is not None:
            cnts, _ = cv2.findContours((pres.cad_mask > 0).astype(np.uint8),
                                       cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
            cv2.drawContours(vis, cnts, -1, (150, 150, 150), 1)

        # VIA 표시
        if via is not None:
            for f in via.findings:
                if "via_center" not in f:
                    continue
                vx, vy = int(round(f["via_center"][0])), int(round(f["via_center"][1]))
                st = f.get("status")
                if st == "OK":
                    col = _COLOR_VIA_OK
                elif st == "VIA_EXTRA":
                    col = _COLOR_VIA_EXTRA
                else:
                    col = _COLOR_VIA_OFF
                cv2.drawMarker(vis, (vx, vy), col, cv2.MARKER_CROSS, 7, 1)
                if f.get("status") == "VIA_OFFSET":
                    px, py = int(round(f["pad_center"][0])), int(round(f["pad_center"][1]))
                    cv2.arrowedLine(vis, (px, py), (vx, vy), _COLOR_VIA_OFF, 1, tipLength=0.35)

        # 결함 강조
        for d in defects:
            px, py = int(round(d.position[0])), int(round(d.position[1]))
            if d.kind == "MISSING_PAD":
                cv2.circle(vis, (px, py), 11, _COLOR_MISSING, 2)
                cv2.line(vis, (px - 6, py - 6), (px + 6, py + 6), _COLOR_MISSING, 1)
                cv2.line(vis, (px - 6, py + 6), (px + 6, py - 6), _COLOR_MISSING, 1)
            elif d.kind == "EXTRA_PAD":
                cv2.circle(vis, (px, py), 11, _COLOR_EXTRA, 2)
            elif d.kind == "VIA_MISSING":
                cv2.circle(vis, (px, py), 8, _COLOR_VIA_MISS, 2)
            elif d.kind == "VIA_OFFSET":
                cv2.circle(vis, (px, py), 8, _COLOR_VIA_OFF, 2)
            elif d.kind == "VIA_EXTRA":
                cv2.circle(vis, (px, py), 8, _COLOR_VIA_EXTRA, 2)

    # ---- 코드 산출 ----
    present = {d.code for d in defects}
    codes = [c for c in cfg.priority if c in present]
    code = codes[0] if codes else CODE_OK
    ok = (code == CODE_OK)

    kinds: Dict[str, int] = {}
    for d in defects:
        kinds[d.kind] = kinds.get(d.kind, 0) + 1
    message = "OK" if ok else ", ".join("%s x%d" % (k, v) for k, v in sorted(kinds.items()))

    return InspectionResult(
        code=code, codes=codes, ok=ok, defects=defects, result_image=vis,
        pads=seg.pads,
        stages={"segment": seg, "presence": pres, "via": via},
        message=message,
    )


# ----------------------------------------------------------------------------
# 원샷 API
# ----------------------------------------------------------------------------
def inspect(image: Union[str, np.ndarray],
            cad_mask: Union[str, np.ndarray],
            cad_meta: Union[None, str, Dict[str, Any]] = None,
            cfg: Optional[InspectConfig] = None,
            via_design: Union[None, str, np.ndarray] = None) -> InspectionResult:
    """이미지 1장 검사 (전 단계 일괄 수행).

    Parameters
    ----------
    image      : 검사 대상 이미지 경로 또는 BGR ndarray
    cad_mask   : PAD 설계도 이진 이미지 경로 또는 ndarray (PAD=흰색)
    cad_meta   : 설계도 JSON 경로 / dict / None. via_expected 정보를 담음
    cfg        : InspectConfig
    via_design : [옵션] VIA 설계 마스크 경로 또는 ndarray (VIA 위치=흰색).
                 cfg.via_design_check=True 일 때만 사용된다.

    Returns
    -------
    InspectionResult : .code ("1"/"24"/"99"/"-1"), .result_image, .defects, .summary()
    """
    cfg = cfg or InspectConfig()
    try:
        pre = step1_preprocess(image, cfg)
        seg = step2_segment_pads(pre, cfg)
        pres = step3_check_pad_presence(seg, cad_mask, cfg)
        via = step4_check_via(pre.bgr, seg, pres, cad_meta, cfg, via_design)
        return step5_render(pre.bgr, seg, pres, via, cfg)
    except Exception as exc:                                   # noqa: BLE001
        blank = _as_bgr(image) if not isinstance(image, str) or os.path.exists(image) \
            else np.zeros((64, 64, 3), np.uint8)
        return InspectionResult(code=CODE_ERROR, codes=[CODE_ERROR], ok=False, defects=[],
                                result_image=blank, message="ERROR: %s" % exc)


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def _main() -> int:
    import argparse

    ap = argparse.ArgumentParser(description="PAD/VIA 검사")
    ap.add_argument("image", help="검사 이미지")
    ap.add_argument("cad", help="설계도 이진 이미지(PNG)")
    ap.add_argument("--meta", default=None, help="설계도 JSON")
    ap.add_argument("--via-design", dest="via_design", default=None,
                    help="VIA 설계 마스크 PNG (지정하면 VIA 설계 대조 활성화)")
    ap.add_argument("--out", default=None, help="결과 이미지 저장 경로")
    a = ap.parse_args()

    cfg = InspectConfig(via_design_check=a.via_design is not None)
    res = inspect(a.image, a.cad, a.meta, cfg, a.via_design)
    print(res.to_json())
    if a.out:
        imwrite_unicode(a.out, res.result_image)
        print("saved:", a.out)
    # 프로세스 종료 코드는 int 여야 하므로 0(양품) / 1(불량) 로만 구분한다.
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(_main())
