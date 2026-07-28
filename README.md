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
| **`via_checker.py`** | **VIA 검사만 떼어낸 단독 모듈.** 이미 준비된 이미지 4개를 받아 VIA만 판정 |
| `make_testset.py` | 원본 이미지에 VIA·결함을 주입해 테스트셋과 설계도를 생성 |
| `run_inspection.py` | 테스트셋 전체를 검사하고 정답과 대조해 정확도 리포트 |
| `eval_padlevel.py` | PAD 단위 혼동행렬 + 편심 임계값 분리도 분석 |
| `ALGORITHM.md` | **알고리즘·로직 상세 문서** (설계 근거, 실패 이력, 튜닝 가이드 포함) |

의존성은 `opencv-python`, `numpy` 뿐입니다. **Python 3.9+**.

```bash
pip install opencv-python numpy
```

---

## 동작 원리

### 전체 파이프라인

```mermaid
flowchart TD
    IMG["검사 이미지<br/>(BGR)"] --> S1
    CAD["PAD 설계도<br/>(이진 PNG)"] --> S3
    META["설계도 메타<br/>(JSON, 선택)"] --> S4
    VCAD["VIA 설계도<br/>(이진 PNG, 옵션)"] -.-> S4

    S1["STEP 1 · 전처리<br/>Gray → Blur → Otsu 이진화"] --> S2
    S2["STEP 2 · PAD 분할<br/>Open → Close → 구멍 메움 → 연결요소 라벨링"] --> S3
    S3["STEP 3 · PAD 유무 검사<br/>설계→실측 커버리지 = 누락<br/>실측→설계 중첩률 = 과잉"] --> S4
    S4["STEP 4 · VIA 검사<br/>어두움 AND Black-hat → 후보<br/>어둠 가중 무게중심 → VIA 중심<br/>편심 = ‖VIA−PAD‖ / PAD반지름"] --> S5
    S5["STEP 5 · 판정 종합 + 시각화"] --> OUT

    OUT["result_image (원본 해상도)<br/>code : '1' / '24' / '99' / '-1'"]

    S3 -. "MISSING_PAD / EXTRA_PAD" .-> C24["code '24'"]
    S4 -. "VIA_MISSING / VIA_OFFSET / VIA_EXTRA" .-> C99["code '99'"]
    C24 --> S5
    C99 --> S5

    style S3 fill:#ffe0e0,stroke:#c00
    style S4 fill:#e0e8ff,stroke:#00c
    style OUT fill:#e0ffe0,stroke:#0a0
```

`구멍 메움` 이 핵심입니다. VIA 는 PAD 안의 어두운 점이라 이진화하면 **PAD 에 구멍이 뚫린 형태**가 됩니다.
이 구멍을 메워야 PAD 를 온전한 덩어리로 셀 수 있고, 메워낸 구멍 자체가 VIA 후보가 됩니다.

### 판정 우선순위

```mermaid
flowchart LR
    A{"PAD 누락<br/>또는 과잉?"} -->|예| B["code '24'"]
    A -->|아니오| C{"VIA 이상?"}
    C -->|예| D["code '99'"]
    C -->|아니오| E["code '1'"]

    style B fill:#ffd0d0,stroke:#c00
    style D fill:#ffe8c0,stroke:#e80
    style E fill:#d0ffd0,stroke:#0a0
```

PAD 자체가 틀렸다면 그 위의 VIA 판정은 의미가 없으므로 **`"24"` 가 `"99"` 보다 우선**합니다.
둘 다 발생하면 `code = "24"`, `codes = ["24", "99"]` 로 **모든 코드를 함께** 돌려줍니다.

### VIA 판정 로직

```mermaid
flowchart TD
    T{"검사 대상<br/>PAD 인가?"} -->|아니오| SKIP["SKIP<br/>(직사각·경계 PAD 등)"]
    T -->|예| F{"VIA 후보가<br/>검출되었나?"}

    F -->|없음| G{"설계에<br/>VIA 가 있나?"}
    G -->|있음| MISS["VIA_MISSING → '99'"]
    G -->|없음| OK1["양품"]

    F -->|있음| H{"설계에<br/>VIA 가 있나?"}
    H -->|없음| EX["VIA_EXTRA → '99'"]
    H -->|있음| I{"편심 ≤ 허용치?"}
    I -->|예| OK2["양품"]
    I -->|아니오| OFF["VIA_OFFSET → '99'"]

    style MISS fill:#d0e0ff,stroke:#04c
    style EX fill:#f0d0ff,stroke:#80c
    style OFF fill:#ffe0c0,stroke:#e80
    style OK1 fill:#d0ffd0,stroke:#0a0
    style OK2 fill:#d0ffd0,stroke:#0a0
```

`설계에 VIA 가 있나?` 분기는 **`via_design_check=True` 일 때만** 실제로 갈라집니다.
꺼져 있으면 "검사 대상 PAD 에는 VIA 가 있어야 한다"가 기본 전제라 항상 `있음` 으로 처리되고,
따라서 `VIA_EXTRA` 는 발생하지 않습니다.

### 편심(offset) 계산

편심은 **PAD 등가반지름으로 정규화**하므로 PAD 크기가 달라도 같은 기준이 적용됩니다.

```
            ┌─────────────────┐
            │   설계 PAD 형상  │   ← 기준 형상은 설계도에서 가져온다
            │    ╭───────╮    │      (VIA 때문에 생긴 노치에 영향받지 않음)
            │   ╱    +    ╲   │   + = PAD 중심
            │  │     ↘     │  │   ● = VIA 중심 (어둠 가중 무게중심, 서브픽셀)
            │   ╲     ●   ╱   │   ↘ = 편심 벡터
            │    ╰───────╯    │
            └─────────────────┘

    offset_norm = ‖VIA중심 − PAD중심‖ / PAD등가반지름       (등가반지름 = √(면적/π))

    불량 판정 = (offset_norm > 0.25)  AND  (실제거리 > 2.2px)
                 └─ 상대 기준 ─┘         └─ 절대 하한 ─┘
```

**두 조건을 모두** 넘어야 불량입니다. 작은 PAD 에서는 1px 의 픽셀 양자화만으로도
상대 편심이 커 보이므로, 절대 하한이 없으면 오검출이 납니다.

### 실측 편심 분포 — 왜 이 임계값인가

테스트셋 676개 VIA 의 `offset_norm` 실측 분포입니다 (막대는 그룹별로 따로 정규화).

```
 offset_norm │ 정상 (center) n=641          │ 쏠림 (shift) n=35
─────────────┼──────────────────────────────┼────────────────────────────
  0.00–0.05  │ ████████████████████████ 308 │
  0.05–0.10  │ ███████████████████████  294 │
  0.10–0.15  │ ███                      39  │
  0.15–0.20  │                          0   │
  0.20–0.25  │                          0   │   ← 판정 임계값 0.25
  0.25–0.30  │                          0   │
  0.30–0.35  │                          0   │        비어 있는 구간
  0.35–0.40  │                          0   │        (분리 마진)
  0.40–0.45  │                              │ ████████████████       6
  0.45–0.50  │                              │ ████████████████████████ 9
  0.50–0.55  │                              │ ████████████████████████ 9
  0.55–0.60  │                              │ ████████████████       6
  0.60–0.65  │                              │ ████████               3
  0.65–0.70  │                              │ ██                     1
  0.70–0.75  │                              │ ██                     1
─────────────┴──────────────────────────────┴────────────────────────────
  정상 최댓값 0.131 │←── 분리 마진 0.282 ──→│ 0.413 쏠림 최솟값
```

두 분포 사이가 **완전히 비어 있습니다.** 임계값을 0.15 ~ 0.40 중 어디에 두어도 결과가 같습니다.
즉 **파라미터를 데이터에 맞춰 끼워맞춘 것이 아닙니다.**

### 결과 이미지 마커

```
   ┌────────────────────────────────────────────┐
   │   ╭──────╮        ╭──────╮                 │
   │   │  ╶┼╴ │        │  ╶┼╴↘│      ╭┄┄┄┄╮     │
   │   ╰──────╯        ╰──────╯      ┆ ✕  ┆     │
   │    노랑 십자       주황 십자+화살표  빨강 원+X │
   │    VIA 정상        VIA 편심        PAD 누락  │
   │                                            │
   │   ╭──────╮        ╭──────╮      ╭┄┄┄┄╮     │
   │   │  ╶┼╴ │        │      │      ┆    ┆     │
   │   ╰──────╯        ╰──────╯      ╰┄┄┄┄╯     │
   │    보라 십자        파랑 원       자홍 원    │
   │    VIA 과잉         VIA 없음      PAD 과잉  │
   └────────────────────────────────────────────┘
     녹색 외곽선 = 정상 PAD
```

텍스트 바나 여백은 **그리지 않습니다.** `result_image.shape == 입력.shape` 이 항상 보장됩니다.

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

## VIA 검사만 쓰기 — `via_checker.py`

이진화와 설계도가 **이미 준비되어 있고** VIA 판정만 필요할 때 쓰는 단독 모듈입니다.
`pad_via_inspector.py` 없이 **이 파일 하나만** 다른 프로젝트에 붙여 넣으면 됩니다.

### 입력 4개

| # | 인자 | 내용 |
|---|---|---|
| 1 | `image` | 원본 이미지 (BGR/GRAY ndarray 또는 경로) |
| 2 | `bin_mask` | 원본의 이진화 결과 = 실측 PAD 마스크 |
| 3 | `pad_design` | PAD 설계도 (이진) |
| 4 | `via_design` | VIA 설계도 (이진) |

네 이미지는 **같은 해상도·같은 좌표계**여야 합니다. 크기가 다르면 조용히 리사이즈하지 않고 `"-1"` 로 실패합니다.

### 검사 대상

**VIA 설계도에 점이 찍힌 PAD만** 검사합니다.
VIA 설계도의 각 연결요소 무게중심이 어느 설계 PAD 안에 있는지로 대상을 결정하며,
설계상 VIA 가 없는 PAD 는 아예 건드리지 않습니다.

```python
from via_checker import check_via, draw_via_result

res = check_via("board.png", "board_bin.png", "pad_cad.png", "via_cad.png")

print(res.code)          # "1" / "99" / "-1"
print(res.summary())     # code=1  target=13  {OK=13}

for f in res.findings:
    print(f["pad_id"], f["status"], f.get("offset_norm"))

img = draw_via_result(res)    # 결과 이미지
```

### 판정

| 설계 VIA | 실물 | 판정 | code |
|---|---|---|---|
| 있음 | 있음 · 정중앙 | `OK` | `"1"` |
| 있음 | 있음 · 쏠림 | `VIA_OFFSET` | `"99"` |
| 있음 | 없음 | `VIA_MISSING` | `"99"` |
| 있음 | 실물 PAD 자체가 없음 | `PAD_ABSENT` | code 에 반영 안 함 |

`PAD_ABSENT` 는 실측 이진 마스크가 설계 PAD 영역을 `pad_present_coverage`(기본 0.55) 만큼도
덮지 못한 경우입니다. PAD 가 없으면 VIA 가 없는 것이 당연하므로 VIA 불량으로 세지 않습니다.
(PAD 누락은 `"24"` 영역 — `pad_via_inspector.py` 담당)

### 자주 건드리는 설정

```python
from via_checker import ViaCheckConfig

cfg = ViaCheckConfig(
    design_pad_dilate = 2,       # 설계 PAD 가 실물보다 작게 그려진 만큼 되돌림 (0=그대로)
    via_offset_tol    = 0.25,    # 반지름 대비 허용 편심 (스케일 불변)
    via_offset_min_px = 2.2,     # 절대 허용 편심(px)
    center_ref        = "pad",   # "pad"=설계 PAD 중심(정중앙) / "design_via"=VIA 설계 좌표
    check_pad_present = True,
)
res = check_via(img, binm, padm, viam, cfg=cfg)

# PAD 픽셀 크기가 다른 실이미지라면 배율만 넘기면 됩니다 (면적 s², 길이 s 자동 보정)
cfg = ViaCheckConfig().scaled(실제_PAD_등가반지름 / 6.0)
```

### 결과 이미지

**함수 하나**로 받습니다. 원본을 다시 넘기거나 플래그를 켤 필요가 없습니다.

```python
res = check_via(img, binm, padm, viam)
cv2.imwrite("out.png", draw_via_result(res))
```

원본 이미지는 `res.source` 에 들어 있어 `draw_via_result` 가 알아서 씁니다.
원본과 같은 해상도에 **PAD 하나당 마커 하나**만 그립니다 (텍스트·여백 없음, 원본은 변형하지 않음).

| 판정 | 마커 |
|---|---|
| `OK` | 초록 원 |
| `VIA_OFFSET` | 주황 원 + PAD중심 → VIA중심 화살표 |
| `VIA_MISSING` | 빨강 X |
| `PAD_ABSENT` | 회색 원 |

마커가 마음에 안 들면 `draw_via_result` 를 참고해 직접 그리면 됩니다.
판정에 필요한 좌표는 전부 `res.findings` 의 `pad_center` / `via_center` / `pad_radius` 에 들어 있습니다.

### 단독 실행

```bash
python via_checker.py board.png board_bin.png pad_cad.png via_cad.png --out result.png
```

### 검증

동일한 44장 테스트셋 기준 — **PAD 703/703 = 1.0000**, 이미지 코드 **44/44 = 1.0000**.

| 기대 \ 판정 | OK | VIA_OFFSET | VIA_MISSING |
|---|---|---|---|
| OK (정중앙) | **641** | 0 | 0 |
| 쏠림 | 0 | **35** | 0 |
| 없음 | 0 | 0 | **27** |

> 전체 엔진의 714 와 개수가 다른 이유: `via_checker.py` 는 설계 대상 PAD 만 훑으므로
> 설계에 없는 VIA(과잉, 11개)는 검사 범위 밖입니다. 그 항목이 필요하면
> `pad_via_inspector.py` 의 `via_design_check=True` 를 쓰세요.

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

## 다른 크기의 실제 이미지에 적용할 때

**기본 설정 그대로는 동작을 보장하지 않습니다.** 실측 결과와 대응 방법입니다.

### ① 설계도 JSON 을 반드시 함께 넘기세요

크롭 범위나 여백이 달라지면 PAD 가 이미지 경계에 닿는지 여부(`touches_border`)가 바뀝니다.
JSON 이 없으면 VIA 검사 대상을 형상 휴리스틱으로 고르는데, 경계에 걸려 제외됐던 PAD 가
여백이 생기면서 갑자기 검사 대상이 되어 **양품이 `"99"` 로 뒤집힙니다.**

| 캔버스 확장 | x1 | x2 | x4 |
|---|---|---|---|
| JSON 메타 **없음** | 0.955 | **0.568** | **0.568** |
| JSON 메타 **있음** | **1.000** | **1.000** | **1.000** |

JSON 을 주면 검사 대상이 설계의 `via_expected` 로 확정되어 이 문제가 사라집니다.

### ② PAD 의 픽셀 크기가 다르면 파라미터를 보정하세요

문제는 이미지 해상도가 아니라 **PAD 가 몇 픽셀인가** 입니다.
이 저장소 기준은 PAD 등가반지름 ≈ 6px, VIA 코어 ≈ 4~5px 입니다.

| 배율 | 기본 설정 | 보정 후 |
|---|---|---|
| x0.5 | 0.364 | 0.614 |
| x1.5 | 0.977 | **1.000** |
| x2.0 | 0.909 | **1.000** |
| x3.0 | 0.864 | **1.000** |

면적 계열은 `s²`, 길이 계열은 `s` 로 스케일하면 확대는 완전히 복구됩니다.

```python
s = 실제_PAD_등가반지름 / 6.0        # 이 저장소 기준 대비 배율

cfg = InspectConfig(
    # 면적 계열 (s²)
    min_pad_area        = int(60   * s*s),
    min_extra_area      = int(120  * s*s),
    via_target_min_area = int(150  * s*s),
    via_target_max_area = int(4000 * s*s),
    via_min_blob        = max(4, int(4 * s*s)),
    # 길이 계열 (s)
    via_offset_min_px   = 2.2 * s,
    via_pad_erode       = max(1, round(1 * s)),
    blur_ksize          = int(3*s) | 1,   # 커널은 홀수여야 함
    open_ksize          = int(3*s) | 1,
    close_ksize         = int(3*s) | 1,
)
```

`via_offset_tol`(0.25) 과 `via_target_min_circularity`(0.72) 는 **비율 기반이라 보정 불필요**합니다.

### ③ 축소는 하지 마세요

x0.5 는 파라미터를 보정해도 0.614 가 한계입니다. VIA 코어가 4~5px 인데 절반이 되면
2px 로 뭉개져 **정보 자체가 소실**됩니다. 임계값 문제가 아니라 복구 불가능한 손실입니다.

### ④ 크기 불일치는 조용히 틀리지 않습니다

설계도와 검사 이미지의 크기가 다르면 자동 리사이즈하지 않고 `code = "-1"` 로 실패합니다.

```
이미지 220x220 vs 설계도 440x440 -> code='-1'
message: ERROR: 설계도 크기((440, 440))와 이미지 크기((220, 220))가 다릅니다.
```

### 새 데이터 붙일 때 순서

1. `run_inspection.py --one <이름>` 으로 STEP1/STEP2 중간 이미지를 **눈으로** 확인 → 이진화·PAD 분할 확정
2. `eval_padlevel.py` 로 **분리 마진**이 양수인지 확인
3. 마진이 음수면 임계값을 건드릴 게 아니라 전처리를 손봐야 합니다 ([`ALGORITHM.md`](ALGORITHM.md) §4.2, §4.3)

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
