# -*- coding: utf-8 -*-
"""via_checker.py - VIA 단독 검사 모듈 (단일 파일 / 복붙용)

pad_via_inspector.py 에서 VIA 판정 로직만 떼어내, 이미 준비된 4개 입력을
그대로 받아 검사하도록 만든 독립 모듈입니다. 다른 .py 에 그대로 붙여넣거나
`from via_checker import check_via` 로 가져다 쓰면 됩니다.

의존성 : numpy, opencv-python  (Python 3.9+)

--------------------------------------------------------------------------
입력 4개
--------------------------------------------------------------------------
  1) image       : 원본 이미지            (BGR / GRAY ndarray 또는 파일 경로)
  2) bin_mask    : 원본 이미지의 이진화    (실측 PAD 마스크, 0/255)
  3) pad_design  : PAD 설계도             (이진 마스크, 0/255)
  4) via_design  : VIA 설계도             (이진 마스크, 0/255)

  네 이미지는 모두 같은 해상도여야 하며 좌표계가 정렬되어 있어야 합니다.
  크기가 다르면 조용히 리사이즈하지 않고 code "-1" 로 실패합니다.

--------------------------------------------------------------------------
검사 대상
--------------------------------------------------------------------------
  VIA 설계도에 점이 찍힌 PAD 만 검사합니다.
  (VIA 설계도의 각 연결요소 무게중심이 어느 설계 PAD 안에 있는지로 결정)
  설계상 VIA 가 없는 PAD 는 아예 건드리지 않습니다.

--------------------------------------------------------------------------
판정
--------------------------------------------------------------------------
  VIA 있음 + 정중앙  -> OK
  VIA 있음 + 쏠림    -> VIA_OFFSET   -> code "99"
  VIA 없음           -> VIA_MISSING  -> code "99"
  설계 PAD 자리에 실물 PAD 자체가 없음 -> PAD_ABSENT (VIA 판정 보류, code 에 반영 안 함)

  편심 = ||VIA중심 - 기준중심|| / 설계PAD등가반지름
  상대 허용치(via_offset_tol)와 절대 하한(via_offset_min_px)을 모두 넘어야 불량.

--------------------------------------------------------------------------
사용 예
--------------------------------------------------------------------------
    from via_checker import check_via, ViaCheckConfig

    res = check_via("board.png", "board_bin.png", "pad_cad.png", "via_cad.png")
    print(res.code)        # "1" / "99" / "-1"
    for f in res.findings:
        print(f["pad_id"], f["status"], f.get("offset_norm"))

    # 결과 이미지까지 필요하면
    res = check_via(img, binm, padm, viam, draw=True)
    cv2.imwrite("out.png", res.overlay)      # 원본 해상도, 마커만 그려짐

    # 이 저장소(PAD 등가반지름 약 6px)보다 큰 실이미지라면 배율 보정
    cfg = ViaCheckConfig().scaled(실제_PAD_등가반지름 / 6.0)
    res = check_via(img, binm, padm, viam, cfg=cfg)
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

__all__ = [
    "CODE_OK", "CODE_VIA_DEFECT", "CODE_ERROR",
    "ViaCheckConfig", "ViaCheckResult",
    "check_via", "draw_via_result",
    "label_design_pads", "map_design_vias", "detect_via_in_shape",
]

# ----------------------------------------------------------------------------
# 결과 코드 (문자열)
# ----------------------------------------------------------------------------
CODE_OK = "1"           # 양품
CODE_VIA_DEFECT = "99"  # VIA 없음 / 편심
CODE_ERROR = "-1"       # 입력 오류 등 처리 실패


# ----------------------------------------------------------------------------
# 설정
# ----------------------------------------------------------------------------
@dataclass
class ViaCheckConfig:
    """검사 파라미터.

    기본값은 PAD 등가반지름이 약 6px 인 이미지(이 저장소의 220x220 테스트셋)
    기준입니다. PAD 픽셀 크기가 다르면 `scaled()` 로 보정하세요.
    """

    # ---- 전처리 ----
    blur_ksize: int = 3               # 가우시안 블러 커널 (0 이면 미사용, 홀수)

    # ---- 설계도 해석 ----
    design_pad_dilate: int = 2        # 설계 PAD 가 실물보다 작게 그려진 만큼 되돌림 (0=그대로)
    min_design_pad_area: int = 60     # 이보다 작은 설계 PAD 연결요소는 무시
    via_design_min_area: int = 2      # 이보다 작은 VIA 설계 점은 무시

    # ---- 실물 PAD 존재 확인 (bin_mask 사용) ----
    check_pad_present: bool = True
    pad_present_coverage: float = 0.55   # 설계 PAD 중 실측으로 덮인 비율 하한

    # ---- VIA 검출 ----
    via_pad_erode: int = 1               # 탐색영역을 PAD 안쪽으로 이만큼 침식
    via_dark_ratio: float = 0.62         # PAD 밝기 중앙값 * 이 값 보다 어두우면 후보
    via_use_blackhat: bool = True        # Black-hat 병용 (PAD 테두리 오검출 억제)
    via_blackhat_ksize_ratio: float = 0.85   # Black-hat 커널 = PAD반지름 * 이 값
    via_blackhat_min: float = 12.0       # Black-hat 응답 절대 하한
    via_blackhat_ratio: float = 0.18     # Black-hat 응답 상대 하한 (PAD중앙값 * 이 값)
    via_min_blob: int = 4                # VIA 로 인정할 최소 면적(px)
    via_max_blob_ratio: float = 0.25     # VIA 최대 면적 = PAD면적 * 이 값
    via_weighted_center: bool = True     # 어둠 가중 무게중심(서브픽셀) 사용

    # ---- 편심 판정 ----
    center_ref: str = "pad"              # "pad" = 설계 PAD 중심 기준(정중앙)
                                         # "design_via" = VIA 설계 좌표 기준
    via_offset_tol: float = 0.25         # 반지름 대비 허용 편심 (스케일 불변)
    via_offset_min_px: float = 2.2       # 절대 허용 편심(px). 양자화 오차 흡수용

    def scaled(self, s: float) -> "ViaCheckConfig":
        """PAD 픽셀 크기가 s 배인 이미지용 설정을 만든다.

        면적 계열은 s^2, 길이 계열은 s 로 스케일합니다.
        비율 기반(via_offset_tol, via_dark_ratio 등)은 스케일 불변이라 그대로 둡니다.
        """
        if s <= 0:
            raise ValueError("scale 은 0 보다 커야 합니다.")
        odd = lambda v: max(1, int(round(v)) | 1)      # noqa: E731
        return replace(
            self,
            blur_ksize=odd(self.blur_ksize * s) if self.blur_ksize > 0 else 0,
            design_pad_dilate=int(round(self.design_pad_dilate * s)),
            min_design_pad_area=max(1, int(self.min_design_pad_area * s * s)),
            via_design_min_area=max(1, int(self.via_design_min_area * s * s)),
            via_pad_erode=max(1, int(round(self.via_pad_erode * s))),
            via_min_blob=max(4, int(self.via_min_blob * s * s)),
            via_offset_min_px=self.via_offset_min_px * s,
        )


# ----------------------------------------------------------------------------
# 결과
# ----------------------------------------------------------------------------
@dataclass
class ViaCheckResult:
    """검사 결과.

    code       : "1"(양품) / "99"(VIA 불량) / "-1"(오류)
    findings   : PAD 별 상세. status = OK / VIA_OFFSET / VIA_MISSING / PAD_ABSENT / SKIP
    defects    : 불량 항목만 추린 목록
    via_mask   : 검출된 VIA 픽셀 마스크 (원본 해상도, 0/255)
    target_pad_ids : 검사 대상이 된 설계 PAD id 목록
    overlay    : draw=True 일 때만 채워지는 결과 이미지 (원본 해상도)
    message    : 오류 메시지 (code == "-1" 일 때)
    """
    code: str
    findings: List[Dict[str, Any]]
    defects: List[Dict[str, Any]]
    via_mask: Optional[np.ndarray] = None
    target_pad_ids: Optional[List[int]] = None
    overlay: Optional[np.ndarray] = None
    message: str = ""

    @property
    def ok(self) -> bool:
        return self.code == CODE_OK

    def summary(self) -> str:
        if self.code == CODE_ERROR:
            return "code=-1 ERROR: " + self.message
        cnt: Dict[str, int] = {}
        for f in self.findings:
            cnt[f["status"]] = cnt.get(f["status"], 0) + 1
        body = ", ".join("%s=%d" % (k, cnt[k]) for k in sorted(cnt))
        return "code=%s  target=%d  {%s}" % (
            self.code, len(self.target_pad_ids or []), body)


# ----------------------------------------------------------------------------
# 입출력 헬퍼 (한글 경로 대응)
# ----------------------------------------------------------------------------
def _imread_unicode(path: str, flags: int = cv2.IMREAD_COLOR) -> Optional[np.ndarray]:
    """cv2.imread 는 비ASCII 경로에서 실패하므로 np.fromfile 로 우회한다."""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, flags)


def _as_gray(src: Union[str, np.ndarray], name: str) -> np.ndarray:
    if isinstance(src, np.ndarray):
        img = src
    else:
        img = _imread_unicode(str(src), cv2.IMREAD_COLOR)
        if img is None:
            raise ValueError("%s를 읽을 수 없습니다: %s" % (name, src))
    if img.ndim == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return img


def _as_mask(src: Union[str, np.ndarray], name: str) -> np.ndarray:
    """무엇이 들어와도 0/255 uint8 단일채널 마스크로 정규화."""
    if isinstance(src, np.ndarray):
        m = src
    else:
        m = _imread_unicode(str(src), cv2.IMREAD_GRAYSCALE)
        if m is None:
            raise ValueError("%s를 읽을 수 없습니다: %s" % (name, src))
    if m.ndim == 3:
        m = cv2.cvtColor(m, cv2.COLOR_BGR2GRAY)
    return np.where(m > 0, 255, 0).astype(np.uint8)


# ----------------------------------------------------------------------------
# 설계도 해석
# ----------------------------------------------------------------------------
def label_design_pads(pad_design: np.ndarray,
                      cfg: Optional[ViaCheckConfig] = None
                      ) -> Tuple[np.ndarray, Dict[int, Dict[str, Any]]]:
    """PAD 설계도를 연결요소로 라벨링한다.

    반환 : (label_map, {pad_id: {area, bbox, centroid}})
    label_map 의 값은 pad_id (배경 0) 이며, min_design_pad_area 미만은 제거된다.
    """
    cfg = cfg or ViaCheckConfig()
    num, labels, stats, cents = cv2.connectedComponentsWithStats(
        (pad_design > 0).astype(np.uint8), connectivity=8)

    out = np.zeros(labels.shape, np.int32)
    pads: Dict[int, Dict[str, Any]] = {}
    next_id = 1
    for i in range(1, num):
        x, y, w, h, area = stats[i]
        if area < cfg.min_design_pad_area:
            continue
        out[labels == i] = next_id
        pads[next_id] = {
            "area": int(area),
            "bbox": (int(x), int(y), int(w), int(h)),
            "centroid": (float(cents[i][0]), float(cents[i][1])),
        }
        next_id += 1
    return out, pads


def map_design_vias(via_design: np.ndarray,
                    pad_label_map: np.ndarray,
                    cfg: Optional[ViaCheckConfig] = None
                    ) -> Tuple[Dict[int, Tuple[float, float]], List[Tuple[float, float]]]:
    """VIA 설계도의 각 점을 그 점이 속한 설계 PAD 에 연결한다.

    반환 : ({pad_id: (x, y)}, [어느 PAD 에도 속하지 않은 점들])
    한 PAD 에 점이 여러 개면 마지막 것으로 덮어씁니다(설계상 1 PAD = 1 VIA 가정).
    """
    cfg = cfg or ViaCheckConfig()
    H, W = pad_label_map.shape[:2]
    num, _, stats, cents = cv2.connectedComponentsWithStats(
        (via_design > 0).astype(np.uint8), connectivity=8)

    mapping: Dict[int, Tuple[float, float]] = {}
    orphans: List[Tuple[float, float]] = []
    for i in range(1, num):
        if int(stats[i, 4]) < cfg.via_design_min_area:
            continue
        vx, vy = float(cents[i][0]), float(cents[i][1])
        ix = int(np.clip(round(vx), 0, W - 1))
        iy = int(np.clip(round(vy), 0, H - 1))
        pid = int(pad_label_map[iy, ix])
        if pid > 0:
            mapping[pid] = (vx, vy)
        else:
            orphans.append((vx, vy))
    return mapping, orphans


# ----------------------------------------------------------------------------
# VIA 검출 (단일 PAD)
# ----------------------------------------------------------------------------
def detect_via_in_shape(gray_roi: np.ndarray,
                        shape_roi: np.ndarray,
                        radius: float,
                        cfg: Optional[ViaCheckConfig] = None
                        ) -> Optional[Dict[str, Any]]:
    """PAD 하나의 ROI 안에서 VIA 를 찾는다.

    gray_roi  : 그레이 ROI
    shape_roi : 같은 크기의 PAD 형상 마스크(0/255). 공칭 크기로 맞춰서 넣을 것.
    radius    : PAD 등가반지름(px). Black-hat 커널 크기 결정에 사용.

    반환 : {"cx","cy","area","pad_median","dark_threshold","mask"} 또는 None(=VIA 없음)
           cx, cy 는 ROI 로컬 좌표.

    검출 원리 (두 조건의 AND)
      (1) 전역 : PAD 밝기 중앙값 * via_dark_ratio 보다 어두움
      (2) 국소 : Black-hat 응답이 큼 = 주변보다 움푹 들어간 고립된 우물
      PAD 테두리는 '단조 경사'라 Black-hat 응답이 거의 0 이므로 자연스럽게 배제된다.
      덕분에 PAD 를 크게 침식하지 않아도 되고, 가장자리로 쏠린 VIA 도 놓치지 않는다.
    """
    cfg = cfg or ViaCheckConfig()

    e = max(cfg.via_pad_erode, 0)
    if e > 0:
        ek = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * e + 1, 2 * e + 1))
        inner = cv2.erode(shape_roi, ek)
    else:
        inner = shape_roi

    if np.count_nonzero(inner) < cfg.via_min_blob * 4:
        return None

    vals = gray_roi[inner > 0]
    med = float(np.median(vals))
    thr = med * cfg.via_dark_ratio
    dark = (gray_roi < thr) & (inner > 0)

    if cfg.via_use_blackhat:
        # PAD 바깥(어두운 배경)을 PAD 중앙값으로 메운 뒤 Black-hat 을 건다.
        # 그대로 두면 가장자리로 쏠린 VIA 주변에서 closing 이 배경 어둠에 끌려
        # 내려가 Black-hat 응답이 사라진다(= 쏠린 VIA 를 놓침).
        roi_fill = np.where(shape_roi > 0, gray_roi, np.uint8(round(med)))
        # 커널은 VIA 보다 크고 PAD 보다 작아야 하므로 PAD 반지름에 비례시킨다.
        ks = int(round(radius * cfg.via_blackhat_ksize_ratio)) | 1
        ks = int(np.clip(ks, 5, 31))
        se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
        bh = cv2.morphologyEx(roi_fill, cv2.MORPH_BLACKHAT, se).astype(np.float32)
        bh_thr = max(float(cfg.via_blackhat_min), med * cfg.via_blackhat_ratio)
        cand = (dark & (bh > bh_thr) & (inner > 0)).astype(np.uint8)
    else:
        cand = dark.astype(np.uint8)

    cand = cv2.morphologyEx(cand, cv2.MORPH_OPEN, np.ones((2, 2), np.uint8))

    n, lab, st, ct = cv2.connectedComponentsWithStats(cand, 8)
    shape_area = float(np.count_nonzero(shape_roi))
    max_area = max(int(shape_area * cfg.via_max_blob_ratio), cfg.via_min_blob)
    best_i, best_a = -1, 0
    for i in range(1, n):
        a = int(st[i, 4])
        if a < cfg.via_min_blob or a > max_area:
            continue
        if a > best_a:
            best_i, best_a = i, a

    if best_i < 0:
        return None

    blob = lab == best_i
    if cfg.via_weighted_center:
        # 이진 중심은 픽셀 양자화 오차가 커서 작은 VIA 에서 흔들린다.
        # '어두운 정도'를 가중치로 쓴 무게중심이 훨씬 안정적(서브픽셀).
        wgt = np.clip(thr - gray_roi.astype(np.float32), 0.0, None) * blob
        tot = float(wgt.sum())
        if tot > 1e-6:
            gy, gx = np.mgrid[0:gray_roi.shape[0], 0:gray_roi.shape[1]].astype(np.float32)
            cx = float((gx * wgt).sum() / tot)
            cy = float((gy * wgt).sum() / tot)
        else:
            cx, cy = float(ct[best_i][0]), float(ct[best_i][1])
    else:
        cx, cy = float(ct[best_i][0]), float(ct[best_i][1])

    return {"cx": cx, "cy": cy, "area": int(best_a), "mask": blob,
            "pad_median": med, "dark_threshold": thr}


# ----------------------------------------------------------------------------
# 메인 : VIA 검사
# ----------------------------------------------------------------------------
def check_via(image: Union[str, np.ndarray],
              bin_mask: Union[str, np.ndarray],
              pad_design: Union[str, np.ndarray],
              via_design: Union[str, np.ndarray],
              cfg: Optional[ViaCheckConfig] = None,
              draw: bool = False) -> ViaCheckResult:
    """VIA 설계도에 VIA 가 있는 PAD 만 골라 VIA 존재/편심을 검사한다.

    image      : 원본 이미지 (BGR/GRAY ndarray 또는 경로)
    bin_mask   : 원본의 이진화 결과 = 실측 PAD 마스크
    pad_design : PAD 설계도 (이진)
    via_design : VIA 설계도 (이진)
    cfg        : ViaCheckConfig
    draw       : True 면 result.overlay 에 원본 해상도 결과 이미지를 채운다

    반환 : ViaCheckResult (code = "1" / "99" / "-1")
    """
    cfg = cfg or ViaCheckConfig()

    try:
        gray = _as_gray(image, "원본 이미지")
        actual = _as_mask(bin_mask, "이진화 이미지")
        pdes = _as_mask(pad_design, "PAD 설계도")
        vdes = _as_mask(via_design, "VIA 설계도")
    except ValueError as e:
        return ViaCheckResult(code=CODE_ERROR, findings=[], defects=[], message=str(e))

    H, W = gray.shape[:2]
    for nm, m in (("이진화 이미지", actual), ("PAD 설계도", pdes), ("VIA 설계도", vdes)):
        if m.shape[:2] != (H, W):
            return ViaCheckResult(
                code=CODE_ERROR, findings=[], defects=[],
                message="%s 크기(%s)가 원본 이미지 크기(%s)와 다릅니다."
                        % (nm, m.shape[:2], (H, W)))

    if cfg.blur_ksize and cfg.blur_ksize >= 3:
        k = cfg.blur_ksize | 1
        gray = cv2.GaussianBlur(gray, (k, k), 0)

    pad_label, pads = label_design_pads(pdes, cfg)
    design_vias, orphans = map_design_vias(vdes, pad_label, cfg)

    via_mask = np.zeros((H, W), np.uint8)
    findings: List[Dict[str, Any]] = []
    defects: List[Dict[str, Any]] = []

    for ox, oy in orphans:
        findings.append({"pad_id": None, "status": "SKIP",
                         "design_via": [round(ox, 2), round(oy, 2)],
                         "reason": "VIA 설계 점이 어느 설계 PAD 안에도 없음"})

    dk = None
    if cfg.design_pad_dilate > 0:
        d = cfg.design_pad_dilate
        dk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))

    target_ids = sorted(design_vias.keys())
    for pid in target_ids:
        dv = design_vias[pid]
        x, y, w, h = pads[pid]["bbox"]
        mg = cfg.design_pad_dilate + cfg.via_pad_erode + 4
        x0, x1 = max(x - mg, 0), min(x + w + mg, W)
        y0, y1 = max(y - mg, 0), min(y + h + mg, H)

        roi_gray = gray[y0:y1, x0:x1]
        # 설계 PAD 를 기준 형상으로 쓴다.
        #  - VIA 가 가장자리로 심하게 쏠리면 어두운 VIA 가 배경과 이어져
        #    실측 PAD 윤곽에 노치가 파이고 중심/탐색영역이 오염된다.
        #  - '정중앙'의 정의 자체가 설계 중심이므로 기준으로 더 타당하다.
        shape = ((pad_label[y0:y1, x0:x1] == pid).astype(np.uint8)) * 255
        if dk is not None:
            shape = cv2.dilate(shape, dk)

        m = cv2.moments((shape > 0).astype(np.uint8), binaryImage=True)
        if m["m00"] <= 0:
            continue
        lcx, lcy = m["m10"] / m["m00"], m["m01"] / m["m00"]
        shape_area = float(m["m00"])
        radius = float(np.sqrt(shape_area / np.pi))
        pad_center = (lcx + x0, lcy + y0)

        rec: Dict[str, Any] = {
            "pad_id": pid,
            "pad_center": [round(float(pad_center[0]), 2), round(float(pad_center[1]), 2)],
            "pad_area": int(shape_area),
            "pad_radius": round(radius, 2),
            "design_via": [round(dv[0], 2), round(dv[1], 2)],
        }

        # ---- 실물 PAD 가 그 자리에 있는지 확인 ----
        if cfg.check_pad_present:
            des_px = np.count_nonzero(shape)
            hit = np.count_nonzero((shape > 0) & (actual[y0:y1, x0:x1] > 0))
            cover = hit / float(des_px) if des_px else 0.0
            rec["pad_coverage"] = round(cover, 4)
            if cover < cfg.pad_present_coverage:
                # PAD 자체가 없으면 VIA 가 없는 게 당연하므로 VIA 불량으로 세지 않는다.
                # (PAD 누락은 code "24" 영역 - pad_via_inspector.py 참고)
                rec["status"] = "PAD_ABSENT"
                rec["reason"] = "설계 PAD 영역의 실측 커버리지 부족"
                findings.append(rec)
                continue

        found = detect_via_in_shape(roi_gray, shape, radius, cfg)

        # ---- VIA 없음 ----
        if found is None:
            rec["status"] = "VIA_MISSING"
            findings.append(rec)
            defects.append({"kind": "VIA_MISSING", "pad_id": pid,
                            "position": [round(float(pad_center[0]), 2),
                                         round(float(pad_center[1]), 2)]})
            continue

        via_mask[y0:y1, x0:x1][found["mask"]] = 255
        gvx, gvy = found["cx"] + x0, found["cy"] + y0

        ref = pad_center if cfg.center_ref == "pad" else dv
        dist = float(np.hypot(gvx - ref[0], gvy - ref[1]))
        norm = dist / radius if radius > 1e-6 else 999.0

        rec.update({
            "via_center": [round(gvx, 2), round(gvy, 2)],
            "via_area": int(found["area"]),
            "center_ref": cfg.center_ref,
            "offset_px": round(dist, 2),
            "offset_norm": round(norm, 4),
            "offset_from_pad_px": round(
                float(np.hypot(gvx - pad_center[0], gvy - pad_center[1])), 2),
            "offset_from_design_via_px": round(
                float(np.hypot(gvx - dv[0], gvy - dv[1])), 2),
            "tolerance": cfg.via_offset_tol,
            "tolerance_px": cfg.via_offset_min_px,
        })

        # ---- 편심 ----
        # 상대 허용치와 절대 하한을 모두 넘어야 불량. 작은 PAD 의 양자화 오차를 흡수한다.
        if norm > cfg.via_offset_tol and dist > cfg.via_offset_min_px:
            rec["status"] = "VIA_OFFSET"
            findings.append(rec)
            defects.append({"kind": "VIA_OFFSET", "pad_id": pid,
                            "position": [round(gvx, 2), round(gvy, 2)],
                            "offset_px": round(dist, 2),
                            "offset_norm": round(norm, 4)})
        else:
            rec["status"] = "OK"
            findings.append(rec)

    code = CODE_VIA_DEFECT if defects else CODE_OK
    res = ViaCheckResult(code=code, findings=findings, defects=defects,
                         via_mask=via_mask, target_pad_ids=target_ids)
    if draw:
        res.overlay = draw_via_result(image, res)
    return res


# ----------------------------------------------------------------------------
# 시각화 (원본 해상도, 마커만)
# ----------------------------------------------------------------------------
_C_OK = (0, 220, 0)          # 초록   : 정상
_C_OFFSET = (0, 165, 255)    # 주황   : 편심
_C_MISSING = (0, 0, 255)     # 빨강   : VIA 없음
_C_ABSENT = (150, 150, 150)  # 회색   : PAD 자체가 없음


def draw_via_result(image: Union[str, np.ndarray],
                    res: ViaCheckResult) -> np.ndarray:
    """원본 해상도 위에 마커만 그린 결과 이미지를 만든다 (텍스트 바 없음).

    정상    : 초록 원(PAD) + 초록 점(VIA)
    편심    : 주황 원 + PAD중심 -> VIA중심 화살표
    VIA없음 : 빨강 X
    PAD없음 : 회색 원
    """
    if isinstance(image, np.ndarray):
        base = image
    else:
        base = _imread_unicode(str(image), cv2.IMREAD_COLOR)
        if base is None:
            raise ValueError("원본 이미지를 읽을 수 없습니다: %s" % image)
    out = cv2.cvtColor(base, cv2.COLOR_GRAY2BGR) if base.ndim == 2 else base.copy()

    for f in res.findings:
        if f.get("pad_center") is None:
            continue
        px, py = int(round(f["pad_center"][0])), int(round(f["pad_center"][1]))
        r = max(int(round(f.get("pad_radius", 5))), 3)
        st = f["status"]

        if st == "OK":
            cv2.circle(out, (px, py), r, _C_OK, 1, cv2.LINE_AA)
            vx, vy = f["via_center"]
            cv2.circle(out, (int(round(vx)), int(round(vy))), 1, _C_OK, -1, cv2.LINE_AA)
        elif st == "VIA_OFFSET":
            cv2.circle(out, (px, py), r, _C_OFFSET, 1, cv2.LINE_AA)
            vx, vy = int(round(f["via_center"][0])), int(round(f["via_center"][1]))
            cv2.arrowedLine(out, (px, py), (vx, vy), _C_OFFSET, 1, cv2.LINE_AA, tipLength=0.35)
        elif st == "VIA_MISSING":
            cv2.drawMarker(out, (px, py), _C_MISSING, cv2.MARKER_TILTED_CROSS,
                           max(r * 2, 7), 1, cv2.LINE_AA)
        elif st == "PAD_ABSENT":
            cv2.circle(out, (px, py), r, _C_ABSENT, 1, cv2.LINE_AA)

    return out


# ----------------------------------------------------------------------------
# 단독 실행 (동작 확인용)
# ----------------------------------------------------------------------------
def _main() -> int:
    import argparse
    import json

    ap = argparse.ArgumentParser(description="VIA 단독 검사")
    ap.add_argument("image", help="원본 이미지")
    ap.add_argument("bin", help="원본 이진화 이미지")
    ap.add_argument("pad_design", help="PAD 설계도")
    ap.add_argument("via_design", help="VIA 설계도")
    ap.add_argument("--out", default="", help="결과 이미지 저장 경로")
    ap.add_argument("--scale", type=float, default=1.0, help="PAD 크기 배율 보정")
    ap.add_argument("--center-ref", default="pad", choices=["pad", "design_via"])
    args = ap.parse_args()

    cfg = ViaCheckConfig(center_ref=args.center_ref)
    if args.scale != 1.0:
        cfg = cfg.scaled(args.scale)

    res = check_via(args.image, args.bin, args.pad_design, args.via_design,
                    cfg=cfg, draw=bool(args.out))
    print(json.dumps({"code": res.code,
                      "summary": res.summary(),
                      "defects": res.defects}, ensure_ascii=False, indent=2))
    if args.out and res.overlay is not None:
        ok, buf = cv2.imencode(".png", res.overlay)
        if ok:
            buf.tofile(args.out)
            print("saved:", args.out)
    return 0 if res.code == CODE_OK else 1


if __name__ == "__main__":
    raise SystemExit(_main())
