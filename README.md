# PAD / VIA 검사기

PCB PAD 와 VIA(중앙 검은 점)를 설계도와 대조해 검사하는 순수 OpenCV 기반 파이프라인입니다.
딥러닝·학습 데이터 없이 **고전 영상처리만** 사용합니다.

| 코드 | 의미 |
|---|---|
| `"1"` | 양품 |
| `"24"` | PAD 누락 / PAD 과잉 (설계도 불일치) |
| `"99"` | VIA 없음 / VIA 편심(쏠림) / VIA 과잉 |
| `"-1"` | 처리 실패 |

> 판정 코드는 `int` 가 아니라 **`str`** 로 반환됩니다.

---

## 파일 구성

| 파일 | 역할 |
|---|---|
| **`pad_via_inspector.py`** | **검사 엔진 (단일 파일, 복붙용).** 이 파일 하나만 있으면 동작합니다 |
| `make_testset.py` | 원본 이미지에 VIA·결함을 주입해 테스트셋과 설계도를 생성 |
| `run_inspection.py` | 테스트셋 전체를 검사하고 정답과 대조해 정확도 리포트 |
| `eval_padlevel.py` | PAD 단위 혼동행렬 + 편심 임계값 분리도 분석 |
| `ALGORITHM.md` | **알고리즘·로직 상세 문서** (설계 근거, 실패 이력, 튜닝 가이드 포함) |

의존성은 `opencv-python`, `numpy` 뿐입니다. **Python 3.9+**.

```bash
pip install opencv-python numpy
```

---

## 빠른 시작

### 1) 원샷 모드

```python
from pad_via_inspector import inspect
import cv2

res = inspect("board.png", "board_cad.png", "board_cad.json")

print(res.code)      # "1" / "24" / "99" / "-1"
print(res.codes)     # 예: ["24", "99"]
print(res.ok)        # True / False
print(res.message)   # 'MISSING_PAD x1, VIA_OFFSET x2'

cv2.imwrite("out.png", res.result_image)   # 입력과 동일 해상도

for d in res.defects:
    print(d.kind, d.pad_id, d.position, d.detail)
```

예외가 나도 던지지 않고 `code = "-1"` 인 결과 객체를 반환하므로,
배치 처리 중 한 장 때문에 전체가 멈추지 않습니다.

### 2) 단계별 모드 (중간 결과 확인)

```python
from pad_via_inspector import (InspectConfig, step1_preprocess, step2_segment_pads,
                               step3_check_pad_presence, step4_check_via, step5_render)

cfg  = InspectConfig()

pre  = step1_preprocess("board.png", cfg)          # 전처리 · 이진화
seg  = step2_segment_pads(pre, cfg)                # PAD 분할 · 구멍 메움
pres = step3_check_pad_presence(seg, "board_cad.png", cfg)   # PAD 누락/과잉  -> "24"
via  = step4_check_via(pre.bgr, seg, pres, "board_cad.json", cfg)  # VIA 검사 -> "99"
res  = step5_render(pre.bgr, seg, pres, via, cfg)  # 종합 · 시각화

print(pre.threshold, len(seg.pads), len(pres.missing), res.code)
```

각 단계 결과가 독립 객체이므로 **임의 단계만 교체·재실행** 할 수 있습니다.

### 3) CLI

```bash
python pad_via_inspector.py board.png board_cad.png \
       --meta board_cad.json --out result.png
```

판정 결과는 stdout 에 JSON 으로 출력됩니다. 프로세스 종료 코드는 **양품 0 / 불량 1**.

---

## VIA 설계도 대조 (옵션)

VIA 도 설계도가 있는 경우, **설계에 있는데 없는 VIA(`VIA_MISSING`)** 와
**설계에 없는데 있는 VIA(`VIA_EXTRA`)** 를 함께 잡을 수 있습니다.
기본값은 **꺼짐**이며, 껐을 때의 동작은 기존과 완전히 동일합니다.

VIA 설계도는 검사 이미지와 같은 크기의 **이진 PNG** 로, 설계상 VIA 가 있어야 할 위치에만 흰 점을 찍습니다.

```python
from pad_via_inspector import InspectConfig, inspect

cfg = InspectConfig(via_design_check=True)
res = inspect("board.png", "board_cad.png", "board_cad.json", cfg, "board_via.png")
```

```bash
python pad_via_inspector.py board.png board_cad.png --meta board_cad.json \
       --via-design board_via.png --out result.png
```

판정 규칙:

| 설계 VIA | 실물 VIA | 판정 |
|---|---|---|
| 있음 | 있음 · 정중앙 | 양품 |
| 있음 | 있음 · 쏠림 | `VIA_OFFSET` → `"99"` |
| 있음 | 없음 | `VIA_MISSING` → `"99"` |
| 없음 | 있음 | `VIA_EXTRA` → `"99"` |
| 없음 | 없음 | 양품 |

설계 마스크는 헬퍼로 만들 수 있습니다.

```python
from pad_via_inspector import build_via_design_mask
mask = build_via_design_mask((220, 220), [(150.5, 32.1), (180.6, 32.1)], radius=2)
```

---

## 결과 이미지

**원본과 동일한 해상도**에 마커만 그립니다. 텍스트 바나 여백은 추가하지 않습니다.
코드·메시지는 `res.code` / `res.message` 로 받아 호출 측에서 원하는 대로 표시하세요.
`InspectConfig(draw_overlay=False)` 로 두면 원본을 그대로 반환합니다.

| 표식 | 색 | 의미 |
|---|---|---|
| PAD 외곽선 | 녹색 | 정상 PAD |
| 십자 마커 | 노랑 | VIA 정상 |
| 십자 + 화살표 | 주황 | VIA 편심 |
| 십자 마커 | 보라 | VIA 과잉 |
| 원 + X | 빨강 | PAD 누락 |
| 원 | 자홍 | PAD 과잉 |
| 원 | 파랑 | VIA 없음 |

---

## 설계도 규약

| 입력 | 형식 | 필수 |
|---|---|---|
| 검사 이미지 | BGR 또는 그레이 | 필수 |
| PAD 설계도 마스크 | 이진 PNG (PAD=255). 실물보다 약 2px 작게 침식 | 필수 |
| 설계도 메타 | JSON (`pads[].via_expected` 등) | 선택 (있으면 정확도 향상) |
| VIA 설계도 마스크 | 이진 PNG (VIA 위치만 흰 점) | 선택 (`via_design_check=True` 일 때) |

메타 JSON 이 없어도 PNG 만으로 동작합니다. PAD 설계도는 헬퍼로 생성할 수 있습니다.

```python
from pad_via_inspector import segment_pad_mask, build_design_mask

pad_mask = segment_pad_mask("board.png")          # 실측 PAD 이진 마스크
cad_mask = build_design_mask(pad_mask, erode_px=2)  # 실물보다 2px 작은 설계도
```

---

## 검증 결과

원본 47장 중 VIA 대상 PAD 를 가진 **44장**으로 측정했습니다.

| 지표 | 설계 VIA 대조 OFF | 설계 VIA 대조 ON |
|---|---|---|
| 이미지 단위 정확도 | **44 / 44 = 1.000** | **44 / 44 = 1.000** |
| PAD 단위 정확도 | **714 / 714 = 1.0000** | **714 / 714 = 1.0000** |
| 편심 분리 마진 | **0.282** | 0.282 |

편심 분리 마진 0.282 = 정상 PAD 의 최악 오프셋(0.131)과 쏠림 PAD 의 최소 오프셋(0.413) 사이 간격.
임계값을 0.15 ~ 0.40 어디에 두어도 결과가 같습니다 — **파라미터에 과적합되지 않았습니다.**

---

## 테스트셋 재생성 (선택)

저장소에는 이미지가 포함되어 있지 않습니다. 직접 만들려면 원본 이미지를 `Images/` 에 넣고,

```bash
python make_testset.py                  # testset/ 생성
python run_inspection.py                # 전체 검사 + 정확도
python run_inspection.py --via-design   # VIA 설계 대조까지 켜고 검사
python run_inspection.py --one <이름>    # 1장만 단계별 실행, 중간 이미지 저장
python eval_padlevel.py                 # PAD 단위 혼동행렬 + 분리도
```

---

## 문서

알고리즘 상세 — 각 단계의 수식, 설계 판단 근거, 실패한 시도와 그 원인, 파라미터 튜닝 가이드는
[`ALGORITHM.md`](ALGORITHM.md) 를 참고하세요.
