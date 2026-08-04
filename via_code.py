# -*- coding: utf-8 -*-
"""via_code.py - 이미 이진화된 VIA 를 받아 코드만 돌려주는 모듈 (단일 파일, 복붙용)

VIA 이진화를 직접 하고 계신 경우를 위한 판정 전용 모듈입니다.
이미지를 그리지 않고 표도 안 찍습니다. 문자열 하나만 나옵니다.

    from via_code import check_via_code

    code = check_via_code(VIA이진화, PAD설계도, VIA설계도)

    #  code : "1"  양품
    #         "42" VIA 없음        (둘 다면 42 가 우선)
    #         "99" VIA 쏠림
    #         "-1" 입력 오류 (이유는 표준에러로 나감)

왜 판정이 그렇게 나왔는지 보고 싶으면 verbose 를 켜세요.
반환값은 그대로 코드 문자열 하나입니다.

    code = check_via_code(VIA이진화, PAD설계도, VIA설계도, verbose=True)

--------------------------------------------------------------------------
via_checker.py 와 무엇이 다른가
--------------------------------------------------------------------------
  via_checker : 원본 이미지에서 VIA 를 '찾는' 것까지 합니다 (4장 입력)
  via_code    : VIA 를 이미 찾아 놓은 상태에서 '판정만' 합니다 (3장 입력)

  판정 기준(편심 임계·경계 여유·면적·모양)은 두 모듈이 똑같습니다.
  그래서 via_checker 가 준 via_bin 을 이 모듈에 그대로 넣으면 같은 코드가 나옵니다.
  테스트셋 44장 / PAD 783개로 확인했습니다.

    code, result, via_bin = check_via(원본, 이진화, PAD설계도, VIA설계도)
    assert check_via_code(via_bin, PAD설계도, VIA설계도, bin_mask=이진화) == code

  bin_mask 를 빼면 PAD 누락 구분이 꺼져서 5장이 갈립니다 (아래 설명 참고).

--------------------------------------------------------------------------
입력 3장 (+ 선택 1장)
--------------------------------------------------------------------------
  1) VIA 이진화  : 직접 만드신 VIA 마스크. 흰색 = VIA (ndarray 또는 파일 경로)
  2) PAD 설계도  : PAD 가 있어야 할 자리 (이진)
  3) VIA 설계도  : VIA 가 있어야 할 자리 (이진)

  선택) 이진화     : 실측 PAD 마스크. 주는 편이 낫습니다. 바로 아래 설명 참고.

  모두 같은 해상도·같은 좌표계여야 합니다.
  크기가 다르면 조용히 리사이즈하지 않고 "-1" 로 실패합니다.

--------------------------------------------------------------------------
선택 인자 bin_mask - 되도록 같이 주세요
--------------------------------------------------------------------------
    code = check_via_code(VIA이진화, PAD설계도, VIA설계도, bin_mask=이진화)

  실측 PAD 마스크입니다. VIA 를 찾는 데는 안 씁니다(그건 이미 하셨으니까).
  '기준점' 을 잡는 데만 씁니다. 주면 두 가지가 켜집니다.

  1) 국소 정합
     설계도와 실물은 완벽히 겹치지 않습니다. 2~3px 만 어긋나도 PAD 반지름이
     6px 수준이면 편심 0.3~0.5 가 그냥 나와서 정상 VIA 가 쏠림(99)이 됩니다.
     PAD 마다 설계 형상을 실물 쪽으로 조금 평행이동시켜 그 계통 오차를 걷어냅니다.
     99 가 유독 많이 나오면 이걸 먼저 의심하세요.

  2) PAD 누락 구분
     실물 PAD 자체가 없으면 VIA 도 당연히 없습니다. 이걸 VIA 없음(42)으로 세면
     PAD 누락 검사와 중복으로 잡힙니다. bin_mask 가 있으면 커버리지를 재서
     PAD 가 없는 자리는 판정에서 빼 둡니다.

  안 주면 둘 다 꺼집니다. 그때 42 는 'VIA 가 없다' 가 아니라
  'PAD 가 없거나 VIA 가 없다' 라는 뜻이 되고, 99 도 늘어날 수 있습니다.
  실측으로 테스트셋 44장 중 5장이 이 차이 하나로 1 대신 42 가 나왔습니다.

--------------------------------------------------------------------------
검사 대상
--------------------------------------------------------------------------
  VIA 설계도에 점이 찍힌 PAD 만 검사합니다.
  설계상 VIA 가 없는 PAD 는 아예 건드리지 않습니다.

--------------------------------------------------------------------------
판정
--------------------------------------------------------------------------
  PAD 안에서 VIA 덩어리를 하나 고른 뒤 편심을 봅니다.

    편심 = ||VIA중심 - PAD중심|| / PAD등가반지름

    VIA 를 하나도 못 고름  -> 42
    편심이 임계 초과        -> 99
    그 외                   -> 1
    실물 PAD 가 없음        -> 코드에 반영 안 함 (bin_mask 를 준 경우에만 구분됨)

  덩어리를 고를 때 거르는 조건 세 가지입니다.

    면적      VIA_MIN_AREA ~ PAD면적*VIA_MAX_AREA_RATIO 밖이면 탈락
    경계 여유 PAD 경계에 붙어 있으면 탈락 (되살리지 않음)
    모양      원이 아니면 뒤로 밀어둠 (원형 후보가 없으면 그냥 씀)

  경계 여유만 '탈락' 이고 모양은 '선호' 입니다. 이유는 아래 상수 설명에 있습니다.

의존성 : numpy, opencv-python  (Python 3.9+)
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

__all__ = ["check_via_code",
           "CODE_OK", "CODE_VIA_MISSING", "CODE_VIA_OFFSET", "CODE_ERROR"]


# ============================================================================
# 결과 코드
# ============================================================================
CODE_OK = "1"            # 양품
CODE_VIA_MISSING = "42"  # VIA 없음
CODE_VIA_OFFSET = "99"   # VIA 쏠림
CODE_ERROR = "-1"        # 입력 오류

# 한 이미지에 둘 다 있으면 앞쪽이 이깁니다 (결손이 위치오차보다 중대).
CODE_PRIORITY = [CODE_VIA_MISSING, CODE_VIA_OFFSET]


# ============================================================================
# 튜닝 값 - via_checker.py 와 같은 값입니다. 한쪽만 고치면 두 모듈 판정이 갈립니다.
# ============================================================================

# 설계 PAD 가 실물보다 몇 px 작게 그려졌는지. 그 만큼 되돌려서 공칭 크기로 씁니다.
# 설계도를 실물과 같은 크기로 그렸으면 0.
DESIGN_PAD_SHRINK = 2

# 편심 판정. 아래 두 조건을 '모두' 넘어야 불량입니다.
#   상대 : 편심거리 / PAD반지름 > OFFSET_TOL
#   절대 : 편심거리(px)        > OFFSET_MIN_PX      <- 작은 PAD 의 반올림 오차 흡수용
OFFSET_TOL = 0.30
OFFSET_MIN_PX = 2.2

# 국소 정합 (bin_mask 를 준 경우에만 동작). 0 이면 정합 안 함.
ALIGN_MAX_RATIO = 0.40    # 최대 이동량 = PAD반지름 * 이 값
ALIGN_MARGIN = 2          # 무게중심을 잴 때 설계 PAD 를 몇 px 키운 창을 볼지
ALIGN_MIN_COVER = 0.30    # 창 안 실측 픽셀이 이 비율도 안 되면 정합 생략

# VIA 로 인정할 덩어리 크기
VIA_MIN_AREA = 4              # 최소 px
VIA_MAX_AREA_RATIO = 0.25     # 최대 = PAD 면적 * 이 값

# VIA 로 인정할 최소 경계 여유 = PAD반지름 * 이 값.
# 덩어리 안에서 PAD 경계로부터 가장 먼 픽셀이 이 거리보다 가까우면 VIA 가 아닙니다.
#
# 이 조건이 꼭 필요한 이유 : PAD 테두리를 한 바퀴 두른 어두운 링은 장단비 1.0 /
# 반경편차 0.1~0.2 라 모양만 보면 '완벽한 원'이고, 무게중심도 PAD 중심과 겹쳐
# 편심이 0 이 됩니다. 즉 VIA 가 아예 없는 PAD 가 양품으로 둔갑합니다(불량 놓침).
# 모양으로는 못 잡고 위치로만 잡힙니다.
#
# 실측 (PAD 반지름으로 나눈 값, 덩어리 최대 경계거리)
#   진짜 VIA      최소 0.536  중앙 0.965   (테스트셋 746개)
#   테두리 링     중앙 0.254  p1  0.086    (실물 이미지 158개)
VIA_MIN_CLEARANCE = 0.30

# VIA 모양 조건. 둘 다 크기와 무관한 무차원 값이라 배율이 바뀌어도 그대로 씁니다.
#   장단비   = 외접 사각형의 긴변 / 짧은변          (원 = 1.0)
#   반경편차 = 중심까지 거리의 표준편차 / 등가반지름 (꽉 찬 원 = 0.236, 크기 무관)
#
# 실측 : 진짜 VIA 장단비 1.00~1.75 반경편차 0.211~0.317
#        긁힘 노이즈 장단비 4.0~7.0  반경편차 0.443~0.690
VIA_MAX_ASPECT = 2.2
VIA_MAX_RADIAL_DEV = 0.45

# 실물 PAD 존재 판정 (bin_mask 를 준 경우에만 씁니다).
# 커버리지가 이 값 미만이면 PAD 가 없다고 보고 그 자리는 판정에서 뺍니다.
PAD_PRESENT_MIN = 0.55

# excl 커버리지에서 제외할 원의 반지름 = PAD반지름 * 이 값.
# 실물 VIA 는 이진화 마스크에 구멍으로 뚫리므로, VIA 가 있어야 할 자리를 빼고 재면
# 구멍 크기와 무관하게 PAD 존재 여부를 판정할 수 있습니다 (구멍 메우기 불필요).
VIA_EXCLUDE_RATIO = 0.75

# 설계도 잡티 제거용 최소 px.
MIN_PAD_AREA = 4
MIN_VIA_AREA = 1              # VIA 설계 점의 최소 px


# ============================================================================
# 공개 함수
# ============================================================================
def check_via_code(via_bin: Union[str, np.ndarray],
                   pad_design: Union[str, np.ndarray],
                   via_design: Union[str, np.ndarray],
                   bin_mask: Optional[Union[str, np.ndarray]] = None,
                   verbose: bool = False) -> str:
    """이미 이진화된 VIA 마스크를 받아 코드 문자열 하나만 돌려준다.

        code = check_via_code(VIA이진화, PAD설계도, VIA설계도)

    "1"(양품) / "42"(VIA 없음) / "99"(VIA 쏠림) / "-1"(입력 오류) 중 하나입니다.
    한 이미지에 없음과 쏠림이 같이 있으면 "42" 가 우선합니다.

    bin_mask : 실측 PAD 마스크. 주면 설계도-실물 어긋남을 국소 정합으로 흡수합니다.
               안 주면 어긋난 만큼이 그대로 편심에 더해져 99 가 늘 수 있습니다.
    verbose  : PAD 별 판정 근거를 표준출력에 찍습니다. 반환값은 그대로 코드 하나.
    """
    try:
        vmask = _to_mask(via_bin, "VIA 이진화")
        pdes = _to_mask(pad_design, "PAD 설계도")
        vdes = _to_mask(via_design, "VIA 설계도")
        act = _to_mask(bin_mask, "이진화 이미지") if bin_mask is not None else None
    except ValueError as e:
        sys.stderr.write("[via_code] %s\n" % e)
        return CODE_ERROR

    H, W = vmask.shape[:2]
    pairs = [("PAD 설계도", pdes), ("VIA 설계도", vdes)]
    if act is not None:
        pairs.append(("이진화 이미지", act))
    for nm, m in pairs:
        if m.shape[:2] != (H, W):
            sys.stderr.write("[via_code] %s 크기%s가 VIA 이진화 크기%s와 다릅니다.\n"
                             % (nm, m.shape[:2], (H, W)))
            return CODE_ERROR

    rows = _judge(vmask, pdes, vdes, act)
    if verbose:
        _print_rows(rows)

    found = set(r["status"] for r in rows)
    for c in CODE_PRIORITY:
        if (c == CODE_VIA_MISSING and "VIA_MISSING" in found) or \
           (c == CODE_VIA_OFFSET and "VIA_OFFSET" in found):
            return c
    return CODE_OK


# ============================================================================
# 이미지 읽기 (한글 경로 대응)
# ============================================================================
def _to_mask(src: Union[str, np.ndarray], name: str) -> np.ndarray:
    """무엇이 들어와도 0/255 uint8 단일채널 마스크로 맞춘다."""
    if isinstance(src, np.ndarray):
        m: Optional[np.ndarray] = src
    else:
        try:
            buf = np.fromfile(str(src), dtype=np.uint8)
        except OSError:
            buf = np.empty(0, np.uint8)
        # cv2.imread 는 비ASCII 경로에서 실패하므로 np.fromfile 로 우회한다.
        m = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE) if buf.size else None
    if m is None:
        raise ValueError("%s를 읽을 수 없습니다: %s" % (name, src))
    if m.ndim == 3:
        m = cv2.cvtColor(m, cv2.COLOR_BGR2GRAY)
    return np.where(m > 0, 255, 0).astype(np.uint8)


# ============================================================================
# 설계도 해석
# ============================================================================
def _label_pads(pad_design: np.ndarray
                ) -> Tuple[np.ndarray, Dict[int, Tuple[int, int, int, int]]]:
    """PAD 설계도를 연결요소로 나눈다. 반환 (라벨맵, {pad_id: bbox})

    작은 PAD 도 그대로 남긴다. 실제 검사 대상은 뒤에서 'VIA 설계도에 점이 있는가'
    로만 걸러지므로, 여기서 크기로 자르면 작은 PAD 가 통째로 빠진다.
    """
    num, labels, stats, _ = cv2.connectedComponentsWithStats(
        (pad_design > 0).astype(np.uint8), connectivity=8)

    out = np.zeros(labels.shape, np.int32)
    boxes: Dict[int, Tuple[int, int, int, int]] = {}
    next_id = 1
    for i in range(1, num):
        if int(stats[i, 4]) < MIN_PAD_AREA:
            continue
        out[labels == i] = next_id
        boxes[next_id] = (int(stats[i, 0]), int(stats[i, 1]),
                          int(stats[i, 2]), int(stats[i, 3]))
        next_id += 1
    return out, boxes


def _map_vias(via_design: np.ndarray,
              pad_label: np.ndarray) -> Dict[int, Tuple[float, float]]:
    """VIA 설계도의 점들을 각각 어느 설계 PAD 소속인지 매핑한다.

    반환 {pad_id: (x, y)}. 어느 PAD 에도 안 들어간 점은 버린다.
    """
    H, W = pad_label.shape[:2]
    num, _, stats, cents = cv2.connectedComponentsWithStats(
        (via_design > 0).astype(np.uint8), connectivity=8)

    out: Dict[int, Tuple[float, float]] = {}
    for i in range(1, num):
        if int(stats[i, 4]) < MIN_VIA_AREA:
            continue
        vx, vy = float(cents[i][0]), float(cents[i][1])
        ix = int(np.clip(round(vx), 0, W - 1))
        iy = int(np.clip(round(vy), 0, H - 1))
        pid = int(pad_label[iy, ix])
        if pid > 0:
            out[pid] = (vx, vy)
    return out


# ============================================================================
# 판정 본체
# ============================================================================
def _judge(vmask: np.ndarray,
           pdes: np.ndarray,
           vdes: np.ndarray,
           act: Optional[np.ndarray]) -> List[Dict[str, Any]]:
    """PAD 별 판정 결과 목록을 만든다."""
    H, W = vmask.shape[:2]
    pad_label, boxes = _label_pads(pdes)
    design_vias = _map_vias(vdes, pad_label)

    dil = None
    if DESIGN_PAD_SHRINK > 0:
        d = DESIGN_PAD_SHRINK
        dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))
    alk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                    (2 * ALIGN_MARGIN + 1, 2 * ALIGN_MARGIN + 1))

    rows: List[Dict[str, Any]] = []
    for pid in sorted(design_vias):
        dvx, dvy = design_vias[pid]
        x, y, w, h = boxes[pid]
        mg = DESIGN_PAD_SHRINK + 4
        x0, x1 = max(x - mg, 0), min(x + w + mg, W)
        y0, y1 = max(y - mg, 0), min(y + h + mg, H)

        # 기준 형상은 '설계 PAD' 를 쓴다. '정중앙' 의 정의 자체가 설계 중심이다.
        shape = ((pad_label[y0:y1, x0:x1] == pid).astype(np.uint8)) * 255
        if dil is not None:
            shape = cv2.dilate(shape, dil)   # 설계가 작게 그려진 만큼 공칭 크기로 복원

        area = float(np.count_nonzero(shape))
        if area <= 0:
            continue
        ys, xs = np.nonzero(shape)
        cx, cy = float(xs.mean()), float(ys.mean())
        radius = float(np.sqrt(area / np.pi))

        sub_act = None
        if act is not None:
            sub_act = act[y0:y1, x0:x1]
            shape, cx, cy = _align(shape, sub_act, cx, cy, radius, area, alk)

        row: Dict[str, Any] = {
            "pad_id": pid,
            "status": "VIA_MISSING",
            "pad_center": (round(cx + x0, 2), round(cy + y0, 2)),
            "pad_radius": round(radius, 2),
            "pad_coverage": None,
            "via_center": None,
            "via_area": None,
            "offset_px": None,
            "offset_norm": None,
            "edge_rejected": 0,
            "shape_rejected": 0,
        }

        # ---- 실물 PAD 가 그 자리에 있는지 (bin_mask 를 준 경우에만) ----
        if sub_act is not None:
            row["pad_coverage"] = round(
                _coverage(shape, sub_act, area, radius, dvx - x0, dvy - y0), 3)
            if row["pad_coverage"] < PAD_PRESENT_MIN:
                # PAD 가 없으면 VIA 가 없는 게 당연하므로 VIA 불량으로 세지 않는다.
                row["status"] = "PAD_ABSENT"
                rows.append(row)
                continue

        found = _pick_via(vmask[y0:y1, x0:x1], shape, radius, row)
        if found is None:
            rows.append(row)
            continue

        dist = float(np.hypot(found["cx"] - cx, found["cy"] - cy))
        row["via_center"] = (round(found["cx"] + x0, 2), round(found["cy"] + y0, 2))
        row["via_area"] = int(found["area"])
        row["offset_px"] = round(dist, 2)
        row["offset_norm"] = round(dist / radius if radius > 1e-6 else 999.0, 4)

        # 상대·절대 두 조건을 모두 넘어야 불량
        if row["offset_norm"] > OFFSET_TOL and dist > OFFSET_MIN_PX:
            row["status"] = "VIA_OFFSET"
        else:
            row["status"] = "OK"
        rows.append(row)
    return rows


def _align(shape: np.ndarray,
           act: np.ndarray,
           cx: float,
           cy: float,
           radius: float,
           area: float,
           alk: np.ndarray) -> Tuple[np.ndarray, float, float]:
    """설계 PAD 형상을 실측 마스크 쪽으로 조금 평행이동한다.

    설계도와 실물이 2~3px 만 어긋나도 PAD 반지름이 6px 수준이면 편심이 0.3~0.5 로
    잡혀 멀쩡한 VIA 가 전부 쏠림으로 나온다. 그 계통 오차만 걷어내는 것이 목적이다.

    무게중심을 쓰는 이유 :
      - VIA 가 한가운데면 구멍이 대칭이라 무게중심이 밀리지 않는다.
      - VIA 가 쏠려 있으면 무게중심이 반대편으로 밀려 편심이 오히려 커진다.
        즉 진짜 쏠림을 지우지 않는다.
    이동량은 반지름의 ALIGN_MAX_RATIO 이내로 묶어 폭주를 막는다.
    """
    if ALIGN_MAX_RATIO <= 0:
        return shape, cx, cy

    loc = (cv2.dilate(shape, alk) > 0) & (act > 0)
    if np.count_nonzero(loc) < area * ALIGN_MIN_COVER:
        return shape, cx, cy

    ly, lx = np.nonzero(loc)
    lim = radius * ALIGN_MAX_RATIO
    sx = float(np.clip(float(lx.mean()) - cx, -lim, lim))
    sy = float(np.clip(float(ly.mean()) - cy, -lim, lim))
    if abs(sx) < 1e-3 and abs(sy) < 1e-3:
        return shape, cx, cy

    moved = cv2.warpAffine(shape, np.float32([[1, 0, sx], [0, 1, sy]]),
                           (shape.shape[1], shape.shape[0]), flags=cv2.INTER_NEAREST)
    return moved, cx + sx, cy + sy


def _coverage(shape: np.ndarray,
              act: np.ndarray,
              area: float,
              radius: float,
              vx: float,
              vy: float) -> float:
    """실측 마스크가 설계 PAD 를 얼마나 덮는지. 두 방식 중 큰 값을 쓴다.

      full : 설계 PAD 전체 대비        -> 실물 PAD 가 설계보다 작을 때 강함
      excl : VIA 자리를 뺀 영역 대비   -> VIA 구멍이 뚫려 있을 때 강함

    실물 이미지에서는 VIA 가 이진화 마스크에 구멍으로 남는다. full 만 보면 그 구멍
    때문에 멀쩡한 PAD 가 PAD_ABSENT 로 빠지므로, VIA 가 있어야 할 자리를 제외하고
    한 번 더 잰다. 구멍을 메우지 않고도 구멍의 영향을 없앨 수 있다.
    """
    hit = (shape > 0) & (act > 0)
    full = float(np.count_nonzero(hit)) / area if area > 0 else 0.0

    ex = np.zeros(shape.shape, np.uint8)
    cv2.circle(ex, (int(round(vx)), int(round(vy))),
               max(2, int(round(radius * VIA_EXCLUDE_RATIO))), 255, -1)
    ref = (shape > 0) & (ex == 0)
    n = np.count_nonzero(ref)
    excl = float(np.count_nonzero(ref & hit)) / n if n else 0.0

    return max(full, excl)


def _roundness(ys: np.ndarray, xs: np.ndarray) -> Tuple[float, float]:
    """덩어리가 얼마나 '꽉 찬 원' 에 가까운지 두 숫자로 잰다.

    반환 (장단비, 반경편차). 둘 다 크기와 무관한 무차원 값이다.

      장단비   = 외접 사각형 긴변 / 짧은변. 원이면 1.0. 긁힘·선 자국을 잡는다.
      반경편차 = 중심까지 거리의 표준편차 / 등가반지름. 꽉 찬 원이면 항상 0.236.
                 ㄴ자·대각선·부스러기처럼 '외접 사각형은 정사각인데 속이 안 찬'
                 모양을 잡는다.

    (링 모양은 반경편차가 오히려 작아서 이 두 값으로는 안 걸러진다.
     그래서 링은 VIA_MIN_CLEARANCE 로 위치를 보고 따로 거른다.)
    """
    w = float(xs.max() - xs.min() + 1)
    h = float(ys.max() - ys.min() + 1)
    aspect = max(w, h) / min(w, h)

    d = np.hypot(xs - xs.mean(), ys - ys.mean())
    equiv_r = np.sqrt(len(xs) / np.pi)
    dev = float(d.std() / equiv_r) if equiv_r > 1e-6 else 0.0
    return aspect, dev


def _pick_via(sub: np.ndarray,
              shape: np.ndarray,
              radius: float,
              row: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """PAD 한 개 안에서 사용자가 준 VIA 덩어리 중 하나를 고른다. 없으면 None.

    찾으면 {"cx", "cy", "area"} 를 준다.

    거르는 순서와 성격이 다르다.
      면적      하드 탈락. 너무 작으면 잡티, 너무 크면 PAD 얼룩이다.
      경계 여유 하드 탈락. 되살리지 않는다.
      모양      선호. 원형 후보가 하나도 없으면 모양을 포기하고 가장 큰 것을 쓴다.
    """
    inside = (sub > 0) & (shape > 0)
    if not np.any(inside):
        return None

    # 각 화소가 PAD 경계에서 얼마나 떨어져 있는지. 테두리 링을 거를 때 쓴다.
    dist = cv2.distanceTransform((shape > 0).astype(np.uint8), cv2.DIST_L2, 5)
    min_clr = radius * VIA_MIN_CLEARANCE

    num, lab, stats, cents = cv2.connectedComponentsWithStats(
        inside.astype(np.uint8), 8)
    max_area = max(int(np.count_nonzero(shape) * VIA_MAX_AREA_RATIO), VIA_MIN_AREA)

    # 모양 조건은 '누가 VIA 인가' 를 고르는 데만 쓰고 'VIA 가 없다' 고 단정하는 데는
    # 쓰지 않는다. 긁힘이 진짜 VIA 를 가로지르면 둘이 한 덩어리로 붙어 길쭉해지는데,
    # 그때 탈락시키면 멀쩡한 PAD 가 VIA_MISSING 으로 둔갑하기 때문이다.
    #
    # 경계 여유는 다르다. 되살리지 않는다.
    # PAD 테두리에 딱 붙은 덩어리는 어떤 경우에도 VIA 가 아니다
    # (VIA 는 PAD 안에 뚫린 구멍이라 정의상 경계에서 떨어져 있다).
    best, best_area = -1, 0
    alt, alt_area = -1, 0
    for i in range(1, num):
        a = int(stats[i, 4])
        if not (VIA_MIN_AREA <= a <= max_area):
            continue
        ys, xs = np.nonzero(lab == i)

        if float(dist[ys, xs].max()) < min_clr:
            row["edge_rejected"] += 1
            continue

        if a > alt_area:
            alt, alt_area = i, a
        aspect, dev = _roundness(ys, xs)
        if aspect > VIA_MAX_ASPECT or dev > VIA_MAX_RADIAL_DEV:
            row["shape_rejected"] += 1
            continue
        if a > best_area:
            best, best_area = i, a

    if best < 0:
        # 원형 후보가 없다. 모양을 포기하고 가장 큰 것을 쓴다 (없다고 하지는 않는다).
        # 단 경계에 붙은 것은 여기서도 안 되살아난다 (애초에 alt 에 안 들어갔다).
        best, best_area = alt, alt_area
    if best < 0:
        return None

    return {"cx": float(cents[best][0]), "cy": float(cents[best][1]),
            "area": best_area}


# ============================================================================
# verbose 출력
# ============================================================================
def _print_rows(rows: List[Dict[str, Any]]) -> None:
    count: Dict[str, int] = {}
    for r in rows:
        count[r["status"]] = count.get(r["status"], 0) + 1
    tally = "  ".join("%s=%d" % (k, count[k]) for k in sorted(count))
    print("검사대상 %d개   %s" % (len(rows), tally))
    if not rows:
        return

    head = ("PAD", "판정", "PAD중심", "VIA중심", "편심px", "편심비율",
            "허용", "VIA면적", "모양탈락", "테두리탈락", "덮임")
    print("%4s %-12s %-16s %-16s %7s %9s %6s %8s %9s %11s %6s" % head)
    print("-" * 112)

    def pt(v):
        return "-" if v is None else "(%.1f,%.1f)" % (v[0], v[1])

    def num(v, f="%.2f"):
        return "-" if v is None else f % v

    for r in rows:
        flag = "NG" if r["status"] in ("VIA_OFFSET", "VIA_MISSING") else "  "
        rej, erej = r["shape_rejected"], r["edge_rejected"]
        print("%4d %-12s %-16s %-16s %7s %9s %6s %8s %9s %11s %6s %s" % (
            r["pad_id"], r["status"], pt(r["pad_center"]), pt(r["via_center"]),
            num(r["offset_px"]), num(r["offset_norm"], "%.4f"),
            "%.2f" % OFFSET_TOL, num(r["via_area"], "%d"),
            "%d" % rej if rej else "-", "%d" % erej if erej else "-",
            num(r["pad_coverage"], "%.2f"), flag))
