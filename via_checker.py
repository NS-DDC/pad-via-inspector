# -*- coding: utf-8 -*-
"""via_checker.py - VIA 검사 (단일 파일, 복붙용)

이미지 4장 넣으면 코드와 결과 이미지가 나옵니다. 그게 전부입니다.

    from via_checker import check_via

    code, result, via_bin = check_via(원본, 이진화, PAD설계도, VIA설계도)

    #  code    : "1"  양품
    #            "42" VIA 없음        (둘 다면 42 가 우선)
    #            "99" VIA 쏠림
    #            "-1" 입력 오류 (이때 result 와 via_bin 은 None)
    #  result  : 원본과 같은 해상도의 결과 이미지 (찾아낸 VIA 도 하늘색 원으로 표기)
    #  via_bin : 검출한 VIA 의 이진화 마스크 (원본과 같은 해상도, 0/255 단일채널)

PAD 표면 얼룩 때문에 엉뚱한 것을 VIA 로 잡으면 임계값을 내리세요.

    code, result, via_bin = check_via(원본, 이진화, PAD설계도, VIA설계도,
                                      dark_offset=-8)   # 더 어두운 것만 VIA 로 인정

디버깅할 때는 debug_via 를 쓰세요. PAD 별 수치가 표로 찍히고
결과 이미지에는 PAD 번호가 함께 그려집니다.

    code, result, via_bin, rows = debug_via(원본, 이진화, PAD설계도, VIA설계도)

    for r in rows:
        print(r["pad_id"], r["status"], r["offset_norm"])

--------------------------------------------------------------------------
입력 4장
--------------------------------------------------------------------------
  1) 원본        : 검사할 이미지          (BGR / GRAY ndarray 또는 파일 경로)
  2) 이진화      : 원본을 이진화한 실측 PAD 마스크
  3) PAD 설계도  : PAD 가 있어야 할 자리 (이진)
  4) VIA 설계도  : VIA 가 있어야 할 자리 (이진)

  네 장 모두 같은 해상도·같은 좌표계여야 합니다.
  크기가 다르면 조용히 리사이즈하지 않고 "-1" 로 실패합니다.

--------------------------------------------------------------------------
출력 via_bin - 검출한 VIA 의 이진화 마스크
--------------------------------------------------------------------------
  원본과 같은 해상도의 0/255 단일채널 이미지입니다.
  '검사 대상 PAD 안에서 VIA 로 채택한 덩어리' 만 흰색으로 남습니다.

    - VIA 를 못 찾은 PAD(VIA_MISSING) 와 PAD_ABSENT 는 아무것도 안 찍힙니다.
    - 후보였다가 면적 조건에서 탈락한 덩어리도 안 찍힙니다.
      즉 흰 픽셀 = 이 판정의 근거가 된 VIA 그 자체입니다.

  판정 근거를 눈으로 확인하거나 다음 공정에 넘길 때 쓰세요.

    code, result, via_bin = check_via(...)
    print(cv2.connectedComponents(via_bin)[0] - 1)   # 검출된 VIA 개수

--------------------------------------------------------------------------
검사 대상
--------------------------------------------------------------------------
  VIA 설계도에 점이 찍힌 PAD 만 검사합니다.
  설계상 VIA 가 없는 PAD 는 아예 건드리지 않습니다.

--------------------------------------------------------------------------
판정
--------------------------------------------------------------------------
  OK           VIA 가 있고 정중앙        -> code "1"
  VIA_OFFSET   VIA 가 있는데 쏠림        -> code "99"
  VIA_MISSING  VIA 가 없음               -> code "42"
  PAD_ABSENT   실물 PAD 자체가 없음      -> code 에 반영 안 함 (PAD 누락은 별개 검사 영역)

  편심 = ||VIA중심 - PAD중심|| / PAD등가반지름

--------------------------------------------------------------------------
실물 이미지에서 자주 나던 오판정 네 가지를 어떻게 막는가
--------------------------------------------------------------------------
  1) "쏠림이 아닌데 쏠림"
     설계도와 실물은 완벽히 겹치지 않습니다. 2~3px 만 어긋나도 PAD 반지름이
     6px 수준이면 편심 0.3~0.5 가 그냥 나옵니다. 그래서 PAD 마다 설계 형상을
     실측 마스크 쪽으로 조금 평행이동시켜(=국소 정합) 기준 중심을 잡습니다.
     - 정합량은 반지름의 ALIGN_MAX_RATIO 이내로 묶어 폭주를 막습니다.
     - VIA 가 한가운데면 구멍이 대칭이라 무게중심이 안 밀립니다.
     - VIA 가 쏠려 있으면 무게중심이 반대편으로 밀려 오히려 편심이 커집니다.
       즉 진짜 쏠림은 지워지지 않고 더 잘 드러납니다.

  2) "VIA 구멍 때문에 PAD 가 없다고 나옴"
     실물에서는 VIA 가 이진화 마스크에 구멍으로 뚫립니다. 면적 커버리지만
     보면 이 구멍 때문에 멀쩡한 PAD 가 PAD_ABSENT 로 빠집니다.
     그래서 커버리지를 두 가지로 재고 둘 중 큰 값을 씁니다.
       full : 설계 PAD 전체 대비
       excl : VIA 가 있어야 할 자리를 빼고 계산   <- 구멍에 영향 없음
     구멍 메우기(hole filling) 는 쓰지 않습니다.

  3) "PAD 안 노이즈를 VIA 로 잘못 잡음"
     PAD 표면의 긁힘 자국·얼룩도 어둡기 때문에 VIA 후보로 올라옵니다.
     크기만 보면 진짜 VIA 보다 큰 경우가 많아 그냥 두면 이겨버립니다.
     막는 방법이 두 가지입니다.

     (a) 모양으로 거르기 (자동)
         VIA 는 항상 원형입니다. 원이 아닌 덩어리는 뒤로 밀어둡니다.
           장단비   > VIA_MAX_ASPECT      -> 길쭉함 (긁힘 자국은 1px 폭)
           반경편차 > VIA_MAX_RADIAL_DEV  -> 흩어짐 (ㄴ자·대각선·부스러기)
         실측으로 진짜 VIA 는 장단비 1.00~1.75 / 반경편차 0.211~0.317,
         실물 노이즈는 장단비 4.0~7.0 / 반경편차 0.443~0.690 이라 잘 갈립니다.
         몇 개를 밀어냈는지는 rows 의 shape_rejected 에 찍힙니다.

         중요 : 이 조건은 '누가 VIA 인가' 를 고를 때만 씁니다.
         'VIA 가 없다' 고 단정하는 데는 쓰지 않습니다. 원형 후보가 하나도
         없으면 모양을 무시하고 가장 큰 것을 그냥 씁니다. 긁힘이 진짜 VIA 를
         가로지르면 둘이 한 덩어리로 붙어 길쭉해지는데, 그때 탈락시키면
         멀쩡한 PAD 가 VIA_MISSING 으로 둔갑하기 때문입니다.
         즉 이 필터는 판정을 나쁘게 만들 수 없습니다.

     (b) 어둡기로 거르기 (호출할 때 조절)
         노이즈가 원형이라 (a) 로 안 걸리면 dark_offset 을 음수로 주세요.
         임계 = PAD밝기중앙값 * DARK_RATIO + dark_offset  (1~254 로 묶임)

         단, 이건 만능이 아닙니다. 얼룩이 진짜 VIA 만큼 어두우면 같이
         사라집니다. 대비가 연한 VIA 를 쓰는 라인이라면 조금씩(-3, -5)
         내리면서 debug_via 의 dark_threshold 와 판정을 같이 보세요.

  4) "PAD 테두리를 VIA 로 오인"  <- 넷 중 가장 심각했던 것
     실물에서 PAD 테두리를 한 바퀴 두른 어두운 링이 후보로 올라옵니다.
     이건 앞의 세 가지와 성격이 다릅니다. 멀쩡한 것을 불량이라고 하는 게
     아니라 불량을 양품이라고 하는(놓치는) 오류이기 때문입니다.

     링은 장단비 1.0 / 반경편차 0.13~0.20 이라 모양으로는 '완벽한 원'이고,
     무게중심도 PAD 중심과 겹쳐 편심이 0 이 됩니다. 즉 VIA 가 아예 없는
     PAD 가 OK 로 나옵니다. 위의 (3) 모양 필터로는 절대 못 잡습니다.

     그래서 모양이 아니라 위치를 봅니다. VIA 는 PAD 안에 뚫린 구멍이라
     정의상 경계에서 떨어져 있고, 링은 경계에 붙어 있습니다.

       경계여유 = max(거리변환(설계PAD)[덩어리]) / PAD반지름

     실측 : 진짜 VIA 최소 0.536 / 테두리 링 중앙 0.254 -> 임계 0.30
     몇 개를 걸렀는지는 rows 의 edge_rejected 에 찍힙니다.

     중요 : 이것만 '탈락' 입니다. (3) 과 달리 되살리지 않습니다.
     경계에 붙은 덩어리가 VIA 인 경우는 정의상 없기 때문입니다.

     탐색 영역을 더 깎는(PAD_ERODE 를 키우는) 방식은 실패했습니다.
     연한 VIA 는 화소가 4~5개뿐이라 3px 만 깎아도 통째로 사라집니다
     (실측 : 테스트셋 OK 705 -> 661). 자세한 것은 PAD_ERODE 주석 참고.

의존성 : numpy, opencv-python  (Python 3.9+)
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

__all__ = ["check_via", "debug_via",
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
# 튜닝 값 - 결과가 마음에 안 들면 이 숫자만 고치면 됩니다
# ============================================================================

# 설계 PAD 가 실물보다 몇 px 작게 그려졌는지. 그 만큼 되돌려서 공칭 크기로 씁니다.
# 설계도를 실물과 같은 크기로 그렸으면 0.
DESIGN_PAD_SHRINK = 2

# 편심 판정. 아래 두 조건을 '모두' 넘어야 불량입니다.
#   상대 : 편심거리 / PAD반지름 > OFFSET_TOL
#   절대 : 편심거리(px)        > OFFSET_MIN_PX      <- 작은 PAD 의 반올림 오차 흡수용
# 테스트셋 실측 : 정중앙 최대 0.119 / 쏠림 최소 0.448 -> 그 사이에서 정중앙 쪽에 여유를 둠
OFFSET_TOL = 0.30
OFFSET_MIN_PX = 2.2

# 국소 정합. 설계 PAD 를 실측 마스크 무게중심 쪽으로 이만큼까지 평행이동해서
# 설계도-실물 어긋남이 거짓 쏠림으로 둔갑하는 것을 막습니다. 0 이면 정합 안 함.
ALIGN_MAX_RATIO = 0.40    # 최대 이동량 = PAD반지름 * 이 값
ALIGN_MARGIN = 2          # 무게중심을 잴 때 설계 PAD 를 몇 px 키운 창을 볼지
ALIGN_MIN_COVER = 0.30    # 창 안 실측 픽셀이 이 비율도 안 되면 정합 생략

# VIA 후보 : PAD 밝기 중앙값 * DARK_RATIO 보다 어두운 픽셀
# 최종 임계 = PAD밝기중앙값 * DARK_RATIO + dark_offset
# dark_offset 은 함수 호출할 때 넘기는 인자입니다 (기본 0). 여기 상수는 안 고쳐도 됩니다.
DARK_RATIO = 0.62

# VIA 모양 조건 - VIA 는 항상 원형이므로 원이 아닌 덩어리는 후보에서 뺍니다.
# 둘 다 크기와 무관한 무차원 값이라 이미지 배율이 바뀌어도 그대로 씁니다.
#
#   장단비   = 외접 사각형의 긴변 / 짧은변          (원 = 1.0)
#   반경편차 = 중심까지 거리의 표준편차 / 등가반지름 (꽉 찬 원 = 0.236, 크기 무관)
#
# 실측 근거 (테스트셋 VIA 746개 / 실물 노이즈 이미지)
#   진짜 VIA  : 장단비 1.00~1.75   반경편차 0.211~0.317
#   실물 노이즈: 장단비 4.0~7.0    반경편차 0.443~0.690   <- 1px 폭 긁힘 자국
# 두 분포 사이가 넓게 비어 있어서 그 가운데에 선을 그었습니다.
VIA_MAX_ASPECT = 2.2        # 이보다 길쭉하면 탈락
VIA_MAX_RADIAL_DEV = 0.45   # 이보다 흩어져 있으면 탈락 (ㄴ자·대각선·부스러기)

# Black-hat (주변보다 움푹 들어간 곳만 남겨 PAD 테두리 오검출을 막는 필터)
BLACKHAT_KSIZE_RATIO = 0.85   # 커널 크기 = PAD반지름 * 이 값 (VIA 보다 크고 PAD 보다 작게)
BLACKHAT_MIN = 12.0           # 응답 절대 하한
BLACKHAT_RATIO = 0.18         # 응답 상대 하한 (PAD 밝기 중앙값 * 이 값)

# VIA 로 인정할 덩어리 크기
VIA_MIN_AREA = 4              # 최소 px
VIA_MAX_AREA_RATIO = 0.25     # 최대 = PAD 면적 * 이 값

# 탐색 영역을 PAD 안쪽으로 몇 px 깎을지.
# 이 값을 키워서 PAD 테두리 오검출을 막으려 하면 안 됩니다.
# 연한 VIA 는 화소가 4~5개뿐이라 조금만 깎아도 통째로 사라집니다.
# 테두리 문제는 아래 VIA_MIN_CLEARANCE 가 담당합니다.
PAD_ERODE = 1

# VIA 로 인정할 최소 경계 여유 = PAD반지름 * 이 값.
# 덩어리 안에서 PAD 경계로부터 가장 먼 픽셀이 이 거리보다 가까우면 VIA 가 아닙니다.
# VIA 는 PAD 안쪽에 뚫린 구멍이라 항상 경계에서 떨어져 있는 반면,
# PAD 테두리를 한 바퀴 두른 어두운 링은 전부 경계에 붙어 있습니다.
#
# 이 조건이 꼭 필요한 이유 : 테두리 링은 장단비 1.0 / 반경편차 0.1~0.2 라
# 모양만 보면 '완벽한 원'으로 보이고, 무게중심도 PAD 중심과 겹쳐 편심이 0 이
# 됩니다. 즉 VIA 가 아예 없는 PAD 가 OK 로 둔갑합니다(불량 놓침).
#
# 실측 (PAD 반지름으로 나눈 값, 덩어리 최대 경계거리)
#   진짜 VIA      최소 0.536  중앙 0.965   (테스트셋 746개)
#   테두리 링     중앙 0.254  p1  0.086    (실물 이미지 158개)
VIA_MIN_CLEARANCE = 0.30

# 실물 PAD 존재 판정. 커버리지가 이 값 미만이면 PAD 없음.
# 커버리지는 full 과 excl 중 큰 값입니다 (아래 VIA_EXCLUDE_RATIO 설명 참고).
PAD_PRESENT_MIN = 0.55

# excl 커버리지에서 제외할 원의 반지름 = PAD반지름 * 이 값.
# 실물 VIA 는 이진화 마스크에 구멍으로 뚫리므로, VIA 가 있어야 할 자리를 빼고 재면
# 구멍 크기와 무관하게 PAD 존재 여부를 판정할 수 있습니다 (구멍 메우기 불필요).
VIA_EXCLUDE_RATIO = 0.75

# 설계도 잡티 제거용 최소 px. 검사 대상은 "VIA 설계도에 점이 있는 PAD" 로만 정해지므로
# 여기서는 1~3px 짜리 노이즈만 걸러내면 됩니다. 값이 작아 스케일이 바뀌어도 안전합니다.
MIN_PAD_AREA = 4
MIN_VIA_AREA = 1              # VIA 설계 점의 최소 px

# 결과 이미지 마커 색 (BGR)
COLOR_OK = (0, 220, 0)          # 초록 : 정상
COLOR_OFFSET = (0, 165, 255)    # 주황 : 쏠림
COLOR_MISSING = (0, 0, 255)     # 빨강 : VIA 없음
COLOR_ABSENT = (150, 150, 150)  # 회색 : PAD 없음
COLOR_VIA = (255, 255, 0)       # 하늘 : 찾아낸 VIA 위치 (판정과 무관하게 항상 표기)


# ============================================================================
# 공개 함수
# ============================================================================
def check_via(image: Union[str, np.ndarray],
              bin_mask: Union[str, np.ndarray],
              pad_design: Union[str, np.ndarray],
              via_design: Union[str, np.ndarray],
              dark_offset: float = 0.0
              ) -> Tuple[str, Optional[np.ndarray], Optional[np.ndarray]]:
    """이미지 4장을 받아 (코드, 결과이미지, VIA이진화) 를 돌려준다.

        code, result, via_bin = check_via(원본, 이진화, PAD설계도, VIA설계도)

    code 는 "1"(양품) / "42"(VIA 없음) / "99"(VIA 쏠림) / "-1"(입력 오류) 중 하나입니다.
    한 이미지에 없음과 쏠림이 같이 있으면 "42" 가 우선합니다.
    via_bin 은 검출한 VIA 만 흰색인 0/255 마스크입니다 (원본과 같은 해상도).
    "-1" 이면 result 와 via_bin 은 None 이고, 이유가 표준에러로 출력됩니다.

    dark_offset : VIA 를 찾는 밝기 임계값을 밝기 단위로 올리고 내립니다 (기본 0).

        임계값 = PAD밝기중앙값 * 0.62 + dark_offset

        음수  더 어두운 픽셀만 VIA 로 본다  -> 표면 얼룩에 안 속음 (노이즈 억제)
        양수  덜 어두운 픽셀도 VIA 로 본다  -> 연한 VIA 를 놓치지 않음

    한 번에 -20 씩 움직이지 말고 -5 정도씩 옮기면서 debug_via 의
    dark_threshold 열을 보세요. 실제로 적용된 값이 그대로 찍힙니다.
    """
    code, src, via_bin, rows, err = _run(image, bin_mask, pad_design, via_design,
                                         dark_offset)
    if code == CODE_ERROR:
        sys.stderr.write("[via_checker] %s\n" % err)
        return CODE_ERROR, None, None
    return code, _draw(src, rows, numbering=False), via_bin


def debug_via(image: Union[str, np.ndarray],
              bin_mask: Union[str, np.ndarray],
              pad_design: Union[str, np.ndarray],
              via_design: Union[str, np.ndarray],
              quiet: bool = False,
              dark_offset: float = 0.0
              ) -> Tuple[str, Optional[np.ndarray], Optional[np.ndarray],
                         List[Dict[str, Any]]]:
    """check_via 와 같은 검사를 하되, 디버깅에 필요한 것을 함께 준다.

        code, result, via_bin, rows = debug_via(원본, 이진화, PAD설계도, VIA설계도)

    앞 세 개는 check_via 와 같습니다. rows 만 추가됩니다.
    dark_offset 도 check_via 와 같은 뜻입니다.

    - PAD 별 수치를 표로 출력합니다 (quiet=True 로 끄기)
    - 결과 이미지에 PAD 번호를 함께 그립니다
    - rows 는 PAD 별 dict 목록입니다. 들어있는 키:
        pad_id, status, pad_center, pad_radius, pad_area, pad_coverage,
        align_shift, design_via, via_center, via_area, offset_px, offset_norm,
        pad_median, dark_threshold, via_aspect, via_radial_dev,
        shape_rejected, edge_rejected
      (해당 없는 항목은 None)

    align_shift 가 크게 나오면 설계도와 실물이 그만큼 어긋나 있다는 뜻입니다.
    shape_rejected 가 0 이 아니면 모양 조건에서 뒤로 밀어낸 후보가 그만큼 있었다는 뜻입니다.
    edge_rejected 가 0 이 아니면 PAD 테두리에 붙어 탈락시킨 후보가 그만큼 있었다는 뜻입니다.
    (뒤로 밀어낸 것은 되살아날 수 있고, 탈락시킨 것은 안 되살아납니다.)
    """
    code, src, via_bin, rows, err = _run(image, bin_mask, pad_design, via_design,
                                         dark_offset)
    if code == CODE_ERROR:
        if not quiet:
            print("code=-1  ERROR: %s" % err)
        return CODE_ERROR, None, None, [{"status": "ERROR", "message": err}]
    if not quiet:
        _print_table(code, rows)
    return code, _draw(src, rows, numbering=True), via_bin, rows


# ============================================================================
# 이미지 읽기 (한글 경로 대응)
# ============================================================================
def _imread(path: str, flags: int) -> Optional[np.ndarray]:
    """cv2.imread 는 비ASCII 경로에서 실패하므로 np.fromfile 로 우회한다."""
    try:
        buf = np.fromfile(path, dtype=np.uint8)
    except OSError:
        return None
    if buf.size == 0:
        return None
    return cv2.imdecode(buf, flags)


def _to_bgr(src: Union[str, np.ndarray], name: str) -> np.ndarray:
    """무엇이 들어와도 uint8 3채널 BGR 로 맞춘다."""
    img = src if isinstance(src, np.ndarray) else _imread(str(src), cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError("%s를 읽을 수 없습니다: %s" % (name, src))
    if img.dtype != np.uint8:
        img = cv2.normalize(img, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)
    return img


def _to_mask(src: Union[str, np.ndarray], name: str) -> np.ndarray:
    """무엇이 들어와도 0/255 uint8 단일채널 마스크로 맞춘다."""
    m = src if isinstance(src, np.ndarray) else _imread(str(src), cv2.IMREAD_GRAYSCALE)
    if m is None:
        raise ValueError("%s를 읽을 수 없습니다: %s" % (name, src))
    if m.ndim == 3:
        m = cv2.cvtColor(m, cv2.COLOR_BGR2GRAY)
    return np.where(m > 0, 255, 0).astype(np.uint8)


# ============================================================================
# 설계도 해석
# ============================================================================
def _label_pads(pad_design: np.ndarray) -> Tuple[np.ndarray, Dict[int, Tuple[int, int, int, int]]]:
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
# 검사 본체
# ============================================================================
def _run(image: Union[str, np.ndarray],
         bin_mask: Union[str, np.ndarray],
         pad_design: Union[str, np.ndarray],
         via_design: Union[str, np.ndarray],
         dark_offset: float = 0.0
         ) -> Tuple[str, Optional[np.ndarray], Optional[np.ndarray],
                    List[Dict[str, Any]], str]:
    """반환 (code, 원본BGR, VIA이진화, rows, 오류메시지)"""
    try:
        src = _to_bgr(image, "원본 이미지")
        actual = _to_mask(bin_mask, "이진화 이미지")
        pdes = _to_mask(pad_design, "PAD 설계도")
        vdes = _to_mask(via_design, "VIA 설계도")
    except ValueError as e:
        return CODE_ERROR, None, None, [], str(e)

    H, W = src.shape[:2]
    for nm, m in (("이진화 이미지", actual), ("PAD 설계도", pdes), ("VIA 설계도", vdes)):
        if m.shape[:2] != (H, W):
            return CODE_ERROR, None, None, [], (
                "%s 크기%s가 원본 이미지 크기%s와 다릅니다." % (nm, m.shape[:2], (H, W)))

    gray = cv2.GaussianBlur(cv2.cvtColor(src, cv2.COLOR_BGR2GRAY), (3, 3), 0)

    pad_label, boxes = _label_pads(pdes)
    design_vias = _map_vias(vdes, pad_label)

    dil = None
    if DESIGN_PAD_SHRINK > 0:
        d = DESIGN_PAD_SHRINK
        dil = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * d + 1, 2 * d + 1))
    # 침식은 1px 로 얕게만 한다. 여기서 크게 깎으면 안 된다.
    # 연한 VIA 는 화소가 4~5개뿐이라 3px 만 깎아도 통째로 사라진다
    # (실측: 침식 1->3 으로 바꿨더니 테스트셋 OK 705 -> 661 로 45개가
    #  오검출 VIA_MISSING 이 됐다). 탐색 영역이 PAD 밖으로 조금 나가는 문제는
    # 깎아서가 아니라 아래 VIA_MIN_CLEARANCE 로 '위치' 를 보고 막는다.
    ero = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                    (2 * PAD_ERODE + 1, 2 * PAD_ERODE + 1))
    alk = cv2.getStructuringElement(cv2.MORPH_ELLIPSE,
                                    (2 * ALIGN_MARGIN + 1, 2 * ALIGN_MARGIN + 1))

    # 검출한 VIA 만 흰색으로 남길 마스크. PAD 마다 채택한 덩어리를 여기에 찍는다.
    via_bin = np.zeros((H, W), np.uint8)

    rows: List[Dict[str, Any]] = []
    found_status = set()

    for pid in sorted(design_vias):
        dvx, dvy = design_vias[pid]
        x, y, w, h = boxes[pid]
        mg = DESIGN_PAD_SHRINK + PAD_ERODE + 4
        x0, x1 = max(x - mg, 0), min(x + w + mg, W)
        y0, y1 = max(y - mg, 0), min(y + h + mg, H)

        roi = gray[y0:y1, x0:x1]
        act = actual[y0:y1, x0:x1]

        # 기준 형상은 '설계 PAD' 를 쓴다.
        # VIA 가 가장자리로 심하게 쏠리면 어두운 VIA 가 배경과 이어져
        # 실측 PAD 윤곽에 노치가 파이고 중심이 흔들리기 때문이다.
        # '정중앙'의 정의 자체도 설계 중심이므로 이쪽이 타당하다.
        shape = ((pad_label[y0:y1, x0:x1] == pid).astype(np.uint8)) * 255
        if dil is not None:
            shape = cv2.dilate(shape, dil)   # 설계가 작게 그려진 만큼 공칭 크기로 복원

        area = float(np.count_nonzero(shape))
        if area <= 0:
            continue
        ys, xs = np.nonzero(shape)
        cx, cy = float(xs.mean()), float(ys.mean())
        radius = float(np.sqrt(area / np.pi))

        # ---- 국소 정합 : 설계도-실물 어긋남을 흡수한다 ----
        shape, cx, cy, shift = _align(shape, act, cx, cy, radius, area, alk)

        row: Dict[str, Any] = {
            "pad_id": pid,
            "status": None,
            "pad_center": (round(cx + x0, 2), round(cy + y0, 2)),
            "pad_radius": round(radius, 2),
            "pad_area": int(area),
            "pad_coverage": None,
            "align_shift": (round(shift[0], 2), round(shift[1], 2)),
            "design_via": (round(dvx, 2), round(dvy, 2)),
            "via_center": None,
            "via_area": None,
            "offset_px": None,
            "offset_norm": None,
            "pad_median": None,
            "dark_threshold": None,
            "via_aspect": None,
            "via_radial_dev": None,
            "shape_rejected": 0,
            "edge_rejected": 0,
        }

        # ---- 실물 PAD 가 그 자리에 있는지 ----
        row["pad_coverage"] = round(
            _coverage(shape, act, area, radius, dvx - x0, dvy - y0), 3)
        if row["pad_coverage"] < PAD_PRESENT_MIN:
            # PAD 가 없으면 VIA 가 없는 게 당연하므로 VIA 불량으로 세지 않는다.
            row["status"] = "PAD_ABSENT"
            rows.append(row)
            continue

        # ---- VIA 찾기 ----
        found = _find_via(roi, shape, radius, ero, row, dark_offset)
        if found is None:
            row["status"] = "VIA_MISSING"
            found_status.add(CODE_VIA_MISSING)
            rows.append(row)
            continue

        via_bin[y0:y1, x0:x1][found["mask"]] = 255

        vx, vy = found["cx"] + x0, found["cy"] + y0
        dist = float(np.hypot(found["cx"] - cx, found["cy"] - cy))
        row["via_center"] = (round(vx, 2), round(vy, 2))
        row["via_area"] = int(found["area"])
        row["via_aspect"] = round(found["aspect"], 2)
        row["via_radial_dev"] = round(found["radial_dev"], 3)
        row["offset_px"] = round(dist, 2)
        row["offset_norm"] = round(dist / radius if radius > 1e-6 else 999.0, 4)

        # 상대·절대 두 조건을 모두 넘어야 불량
        if row["offset_norm"] > OFFSET_TOL and dist > OFFSET_MIN_PX:
            row["status"] = "VIA_OFFSET"
            found_status.add(CODE_VIA_OFFSET)
        else:
            row["status"] = "OK"
        rows.append(row)

    code = CODE_OK
    for c in CODE_PRIORITY:
        if c in found_status:
            code = c
            break
    return code, src, via_bin, rows, ""


def _align(shape: np.ndarray,
           act: np.ndarray,
           cx: float,
           cy: float,
           radius: float,
           area: float,
           alk: np.ndarray) -> Tuple[np.ndarray, float, float, Tuple[float, float]]:
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
        return shape, cx, cy, (0.0, 0.0)

    loc = (cv2.dilate(shape, alk) > 0) & (act > 0)
    if np.count_nonzero(loc) < area * ALIGN_MIN_COVER:
        return shape, cx, cy, (0.0, 0.0)

    ly, lx = np.nonzero(loc)
    lim = radius * ALIGN_MAX_RATIO
    sx = float(np.clip(float(lx.mean()) - cx, -lim, lim))
    sy = float(np.clip(float(ly.mean()) - cy, -lim, lim))
    if abs(sx) < 1e-3 and abs(sy) < 1e-3:
        return shape, cx, cy, (0.0, 0.0)

    moved = cv2.warpAffine(shape, np.float32([[1, 0, sx], [0, 1, sy]]),
                           (shape.shape[1], shape.shape[0]), flags=cv2.INTER_NEAREST)
    return moved, cx + sx, cy + sy, (sx, sy)


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

    반환 (장단비, 반경편차). 둘 다 크기와 무관한 무차원 값이라
    이미지 배율이 바뀌어도 같은 임계값을 그대로 쓸 수 있다.

      장단비   = 외접 사각형 긴변 / 짧은변
                 원이면 1.0, 길쭉할수록 커진다. 긁힘·선 자국을 잡는다.

      반경편차 = 중심까지 거리의 표준편차 / 등가반지름
                 꽉 찬 원이면 크기와 상관없이 항상 0.236 이다.
                 ㄴ자, 대각선, 흩어진 부스러기처럼 '외접 사각형은 정사각인데
                 속이 안 찬' 모양을 잡는다. 장단비가 못 잡는 것을 담당한다.

    (링 모양은 반경편차가 오히려 작아서 이 두 값으로는 안 걸러진다.
     실물에서 나오는 노이즈가 아니라 일부러 다루지 않는다.)
    """
    w = float(xs.max() - xs.min() + 1)
    h = float(ys.max() - ys.min() + 1)
    aspect = max(w, h) / min(w, h)

    d = np.hypot(xs - xs.mean(), ys - ys.mean())
    equiv_r = np.sqrt(len(xs) / np.pi)
    dev = float(d.std() / equiv_r) if equiv_r > 1e-6 else 0.0
    return aspect, dev


def _find_via(roi: np.ndarray,
              shape: np.ndarray,
              radius: float,
              ero: np.ndarray,
              row: Dict[str, Any],
              dark_offset: float) -> Optional[Dict[str, Any]]:
    """PAD 한 개 안에서 VIA 를 찾는다. 없으면 None.

    찾으면 {"cx", "cy", "area", "mask", "aspect", "radial_dev"} 를 준다.
    mask 는 채택한 덩어리의 bool 배열(roi 와 같은 크기)로,
    호출부에서 via_bin 에 찍는 데 쓴다.

    두 조건의 AND 로 후보를 만든다.
      (1) 전역 : PAD 밝기 중앙값 * DARK_RATIO + dark_offset 보다 어두움
      (2) 국소 : Black-hat 응답이 큼 = 주변보다 움푹 들어간 고립된 우물
    PAD 테두리는 '단조 경사'라 Black-hat 응답이 거의 0 이므로 자연히 걸러진다.
    덕분에 PAD 를 크게 깎지 않아도 되고 가장자리로 쏠린 VIA 도 놓치지 않는다.

    그 다음 크기와 모양으로 후보를 거르고, 남은 것 중 가장 큰 것을 고른다.
    """
    inner = cv2.erode(shape, ero)
    if np.count_nonzero(inner) < VIA_MIN_AREA * 4:
        return None

    # 각 화소가 PAD 경계에서 얼마나 떨어져 있는지. 아래에서 테두리 링을 거를 때 쓴다.
    dist = cv2.distanceTransform((shape > 0).astype(np.uint8), cv2.DIST_L2, 5)
    min_clr = radius * VIA_MIN_CLEARANCE

    med = float(np.median(roi[inner > 0]))
    # 임계값을 0 이나 255 로 밀어버리면 전부 탈락/전부 통과가 되어 원인 파악이 어렵다.
    thr = float(np.clip(med * DARK_RATIO + dark_offset, 1.0, 254.0))
    row["pad_median"] = round(med, 1)
    row["dark_threshold"] = round(thr, 1)

    dark = (roi < thr) & (inner > 0)

    # PAD 바깥(어두운 배경)을 PAD 중앙값으로 메운 뒤 Black-hat 을 건다.
    # 그대로 두면 가장자리로 쏠린 VIA 주변에서 closing 이 배경 어둠에 끌려
    # 내려가 Black-hat 응답이 사라진다(= 쏠린 VIA 를 놓친다).
    filled = np.where(shape > 0, roi, np.uint8(round(med)))
    ks = int(np.clip(int(round(radius * BLACKHAT_KSIZE_RATIO)) | 1, 5, 31))
    se = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (ks, ks))
    bh = cv2.morphologyEx(filled, cv2.MORPH_BLACKHAT, se).astype(np.float32)
    bh_thr = max(BLACKHAT_MIN, med * BLACKHAT_RATIO)

    cand = (dark & (bh > bh_thr) & (inner > 0)).astype(np.uint8)
    # 여기서 MORPH_OPEN 으로 잡티를 지우면 안 된다.
    # 대비가 약한 VIA(코어 밝기 = PAD 밝기의 0.45배)는 임계 아래로 내려가는 화소가
    # 4~5개뿐이고 모양도 십자/대각이라 2x2 로 열면 통째로 사라진다(= 오검출 VIA_MISSING).
    # 잡티 제거는 아래 연결성분 면적 하한(VIA_MIN_AREA)이 이미 담당한다.
    # dark AND blackhat 두 조건을 동시에 통과한 화소가 우연히 4개나 이어질 확률은 낮다.

    num, lab, stats, cents = cv2.connectedComponentsWithStats(cand, 8)
    max_area = max(int(np.count_nonzero(shape) * VIA_MAX_AREA_RATIO), VIA_MIN_AREA)

    # VIA 는 항상 원형이다. 크기가 맞아도 모양이 원이 아니면 뒤로 밀어둔다.
    # PAD 표면의 긁힘 자국은 크기만 보면 진짜 VIA 보다 커서 그냥 두면 이기지만,
    # 1px 폭이라 장단비에서 걸린다. 그래서 원형인 것 중에서 먼저 고른다.
    #
    # 다만 모양 조건은 '누가 VIA 인가' 를 고르는 데만 쓰고
    # 'VIA 가 없다' 고 단정하는 데는 쓰지 않는다. 원형 후보가 하나도 없으면
    # 모양을 무시하고 가장 큰 것을 그냥 쓴다. 이유 :
    #   긁힘이 진짜 VIA 를 가로지르면 둘이 한 덩어리로 붙어 길쭉해진다.
    #   이때 탈락시키면 멀쩡한 PAD 가 VIA_MISSING(코드 42) 으로 둔갑한다.
    #   실측에서 이 역전이 4건 나왔고, 대안이 있을 때만 거르도록 바꿔 0건이 됐다.
    # 즉 이 필터는 판정을 뒤집지 못하고 후보 선택만 바꾼다.
    #
    # 경계 여유는 이것들과 달리 '탈락' 이다. 되살리지 않는다.
    # PAD 테두리에 딱 붙은 덩어리는 어떤 경우에도 VIA 가 아니기 때문이다.
    # (VIA 는 PAD 안에 뚫린 구멍이라 정의상 경계에서 떨어져 있다.)
    best, best_area, best_shape = -1, 0, (0.0, 0.0)
    alt, alt_area, alt_shape = -1, 0, (0.0, 0.0)
    rejected = 0
    edge_rejected = 0
    for i in range(1, num):
        a = int(stats[i, 4])
        if not (VIA_MIN_AREA <= a <= max_area):
            continue
        ys, xs = np.nonzero(lab == i)

        # PAD 테두리를 두른 어두운 링 걸러내기.
        # 링은 장단비 1.0 / 반경편차 0.1~0.2 라 모양으로는 '완벽한 원' 이고
        # 무게중심도 PAD 중심과 겹쳐서 편심 0 = OK 로 나온다. 위치로만 잡힌다.
        if float(dist[ys, xs].max()) < min_clr:
            edge_rejected += 1
            continue

        aspect, dev = _roundness(ys, xs)
        if a > alt_area:
            alt, alt_area, alt_shape = i, a, (aspect, dev)
        if aspect > VIA_MAX_ASPECT or dev > VIA_MAX_RADIAL_DEV:
            rejected += 1
            continue
        if a > best_area:
            best, best_area, best_shape = i, a, (aspect, dev)

    row["shape_rejected"] = rejected
    row["edge_rejected"] = edge_rejected
    if best < 0:
        # 원형 후보가 없다. 모양을 포기하고 가장 큰 것을 쓴다 (없다고 하지는 않는다).
        # 단 경계에 붙은 것은 여기서도 안 되살아난다 (애초에 alt 에 안 들어갔다).
        best, best_area, best_shape = alt, alt_area, alt_shape
    if best < 0:
        return None

    blob = lab == best
    # 이진 중심은 픽셀 양자화 오차가 커서 작은 VIA 에서 흔들린다.
    # '어두운 정도'를 가중치로 쓴 무게중심이 훨씬 안정적이다(서브픽셀).
    wgt = np.clip(thr - roi.astype(np.float32), 0.0, None) * blob
    total = float(wgt.sum())
    if total > 1e-6:
        gy, gx = np.mgrid[0:roi.shape[0], 0:roi.shape[1]].astype(np.float32)
        cx = float((gx * wgt).sum() / total)
        cy = float((gy * wgt).sum() / total)
    else:
        cx, cy = float(cents[best][0]), float(cents[best][1])

    return {"cx": cx, "cy": cy, "area": best_area, "mask": blob,
            "aspect": best_shape[0], "radial_dev": best_shape[1]}


# ============================================================================
# 결과 이미지
# ============================================================================
def _draw(src: np.ndarray, rows: List[Dict[str, Any]], numbering: bool) -> np.ndarray:
    """원본과 같은 해상도에 판정 마커를 그린다. 원본은 건드리지 않는다.

        PAD 판정 마커
          정상    : 초록 원
          쏠림    : 주황 원 + PAD중심 -> VIA중심 화살표
          VIA없음 : 빨강 X
          PAD없음 : 회색 원

        찾아낸 VIA (판정과 무관하게 항상)
          하늘색 원 + 중심점. 원 크기는 실제로 검출된 덩어리 크기입니다.
          어디를 VIA 로 봤는지 눈으로 바로 확인할 수 있습니다.
    """
    out = src.copy()
    for r in rows:
        px, py = int(round(r["pad_center"][0])), int(round(r["pad_center"][1]))
        rad = max(int(round(r["pad_radius"])), 3)
        st = r["status"]

        if st == "OK":
            cv2.circle(out, (px, py), rad, COLOR_OK, 1, cv2.LINE_AA)
            color = COLOR_OK
        elif st == "VIA_OFFSET":
            cv2.circle(out, (px, py), rad, COLOR_OFFSET, 1, cv2.LINE_AA)
            vx = int(round(r["via_center"][0]))
            vy = int(round(r["via_center"][1]))
            cv2.arrowedLine(out, (px, py), (vx, vy), COLOR_OFFSET, 1,
                            cv2.LINE_AA, tipLength=0.35)
            color = COLOR_OFFSET
        elif st == "VIA_MISSING":
            cv2.drawMarker(out, (px, py), COLOR_MISSING, cv2.MARKER_TILTED_CROSS,
                           max(rad * 2, 7), 1, cv2.LINE_AA)
            color = COLOR_MISSING
        else:   # PAD_ABSENT
            cv2.circle(out, (px, py), rad, COLOR_ABSENT, 1, cv2.LINE_AA)
            color = COLOR_ABSENT

        # 찾아낸 VIA 를 실제 크기로 표기한다. 판정선(초록/주황)과 겹쳐도
        # 색이 달라 구분되고, 오검출이면 엉뚱한 자리에 원이 그려져 바로 보인다.
        if r["via_center"] is not None:
            vx = int(round(r["via_center"][0]))
            vy = int(round(r["via_center"][1]))
            vr = max(int(round(np.sqrt(max(r["via_area"], 1) / np.pi))), 2)
            cv2.circle(out, (vx, vy), vr, COLOR_VIA, 1, cv2.LINE_AA)
            cv2.circle(out, (vx, vy), 0, COLOR_VIA, -1)   # 중심점 1px

        if numbering:
            cv2.putText(out, str(r["pad_id"]), (px + rad + 1, py - rad),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.3, color, 1, cv2.LINE_AA)
    return out


# ============================================================================
# 디버깅용 표 출력
# ============================================================================
def _print_table(code: str, rows: List[Dict[str, Any]]) -> None:
    count: Dict[str, int] = {}
    for r in rows:
        count[r["status"]] = count.get(r["status"], 0) + 1
    tally = "  ".join("%s=%d" % (k, count[k]) for k in sorted(count))

    print("code=%s   검사대상 %d개   %s" % (code, len(rows), tally))
    if not rows:
        return

    head = ("PAD", "판정", "PAD중심", "VIA중심", "편심px", "편심비율",
            "허용", "VIA면적", "장단비", "반경편차", "모양탈락", "테두리탈락",
            "PAD밝기", "임계", "덮임", "정합이동")
    print("%4s %-12s %-16s %-16s %7s %9s %6s %8s %7s %9s %9s %10s %8s %6s %6s %12s" % head)
    print("-" * 162)

    def pt(v):
        return "-" if v is None else "(%.1f,%.1f)" % (v[0], v[1])

    def num(v, f="%.2f"):
        return "-" if v is None else f % v

    for r in rows:
        flag = "NG" if r["status"] in ("VIA_OFFSET", "VIA_MISSING") else "  "
        rej = r["shape_rejected"]
        erej = r["edge_rejected"]
        print("%4d %-12s %-16s %-16s %7s %9s %6s %8s %7s %9s %9s %10s %8s %6s %6s %12s %s" % (
            r["pad_id"], r["status"], pt(r["pad_center"]), pt(r["via_center"]),
            num(r["offset_px"]), num(r["offset_norm"], "%.4f"),
            "%.2f" % OFFSET_TOL, num(r["via_area"], "%d"),
            num(r["via_aspect"]), num(r["via_radial_dev"], "%.3f"),
            "%d" % rej if rej else "-", "%d" % erej if erej else "-",
            num(r["pad_median"], "%.1f"), num(r["dark_threshold"], "%.1f"),
            num(r["pad_coverage"], "%.2f"), pt(r["align_shift"]), flag))
