# PAD / VIA 검사 알고리즘 문서

`pad_via_inspector.py` 가 사용하는 영상처리 로직과 판정 알고리즘을 단계별로 설명합니다.

---

## 0. 개요

### 0.1 목적

PCB(또는 유사 기판) 이미지에서 두 가지를 검사합니다.

| 검사 | 내용 | 불량 코드 |
|---|---|---|
| **PAD 유무** | 설계도에는 있는데 실물에 없음(누락) / 설계도에 없는데 실물에 있음(과잉) | **"24"** |
| **VIA 검사** | PAD 중앙의 검은 점(VIA)이 없거나 한쪽으로 쏠림 / [옵션] 설계에 없는 VIA 존재 | **"99"** |
| 양품 | 위 두 결함 모두 없음 | **"1"** |
| 내부 오류 | 예외 발생 (파일 없음, 크기 불일치 등) | **"-1"** |

> **판정 코드는 `int` 가 아니라 `str`** 입니다. `res.code == "24"` 처럼 문자열로 비교하세요.

두 결함이 동시에 발생하면 **"24"가 우선** 반환됩니다.
단, `InspectionResult.codes` 에는 검출된 모든 코드가, `.defects` 에는 개별 결함이 전부 남습니다.

```python
CODE_OK         = "1"
CODE_PAD_DEFECT = "24"
CODE_VIA_DEFECT = "99"
CODE_ERROR      = "-1"

priority: Tuple[str, ...] = (CODE_PAD_DEFECT, CODE_VIA_DEFECT)   # "24" -> "99" 순
```

결함 종류(`Defect.kind`)는 5가지입니다.

| kind | 코드 | 설명 | 검출 조건 |
|---|---|---|---|
| `MISSING_PAD` | "24" | 설계 PAD 가 실물에 없음 | 항상 |
| `EXTRA_PAD` | "24" | 설계에 없는 PAD 가 실물에 있음 | 항상 |
| `VIA_MISSING` | "99" | 있어야 할 VIA 가 없음 | 항상 |
| `VIA_OFFSET` | "99" | VIA 가 PAD 중앙에서 쏠림 | 항상 |
| `VIA_EXTRA` | "99" | 설계에 없는 VIA 가 실물에 있음 | **`via_design_check=True` 일 때만** |

### 0.2 파일 구성

| 파일 | 역할 |
|---|---|
| `pad_via_inspector.py` | **검사 본체.** 외부 프로젝트에 이 파일 하나만 복사하면 동작 |
| `via_checker.py` | **VIA 판정만 떼어낸 단독 모듈.** 원본·이진화·PAD설계도·VIA설계도 4개를 받아 §5 로직만 수행 |
| `make_testset.py` | 테스트셋 생성기 (VIA 그리기 + PAD 결함 주입 + 설계도 생성) |
| `run_inspection.py` | 원샷 / 단계별 두 가지 사용 예시 및 배치 정확도 측정 |
| `eval_padlevel.py` | PAD 단위 혼동행렬 + 편심 임계값 분리도 분석 |

`via_checker.py` 는 이 문서의 **STEP 4(VIA 검사)** 만 담고 있습니다.
전처리·PAD 분할·PAD 유무 검사(code `"24"`)가 이미 끝나 있고 VIA 판정만 필요할 때 쓰며,
검사 대상은 **VIA 설계도에 점이 찍힌 PAD 로 한정**됩니다. 사용법은 README 참고.

단독 모듈은 VIA 불량을 두 코드로 나눕니다 — **`"42"` VIA 없음 / `"99"` VIA 쏠림**
(한 이미지에 둘 다면 `"42"` 우선). 또 실물 이미지 대응으로 두 가지가 더 들어 있습니다.
**PAD 별 국소 정합**(설계도-실물 어긋남을 흡수해 거짓 쏠림 제거)과
**이중 커버리지 `max(전체, VIA자리제외)`**(VIA 때문에 생긴 구멍에 강건, 홀필링 미사용).

`pad_via_inspector.py` 는 **프로젝트 내부 import 가 전혀 없습니다.**
의존성은 `opencv-python`, `numpy`, 표준 라이브러리뿐이며 **Python 3.9** 문법으로 작성되었습니다.
(`from __future__ import annotations` + `typing.List/Dict/Optional/Union` 사용, `list[int]` 같은 3.10 문법 미사용)

### 0.3 입력 규약

| 입력 | 형식 | 비고 |
|---|---|---|
| 검사 이미지 | 경로(str) 또는 BGR `ndarray` | PAD 가 배경보다 **밝다**고 가정 |
| PAD 설계도 마스크 | 이진 PNG 경로 또는 `ndarray` | PAD=255, 배경=0. **실물보다 살짝 작게(erode)** 정의 |
| 설계도 메타 | JSON 경로 / dict / `None` | `erode_px`, PAD별 `via_expected` 정보 |
| **VIA 설계도 마스크** | 이진 PNG 경로 또는 `ndarray` / `None` | **[옵션]** 설계상 VIA 가 있어야 할 위치만 흰 점. `via_design_check=True` 일 때만 사용 |

설계도 JSON 예시:

```json
{
  "format": "pad-via-cad/1.0",
  "image_size": [220, 220],
  "erode_px": 2,
  "pad_count": 29,
  "via_expected_count": 23,
  "pads": [
    {"pad_id": 6, "area": 239, "bbox": [100,27,17,17], "centroid": [107.52,35.43],
     "perimeter": 55.21, "circularity": 0.9852, "equiv_radius": 8.722,
     "touches_border": false, "via_expected": true}
  ]
}
```

> JSON 은 **선택 사항**입니다. 없어도 PNG 만으로 전 단계가 동작하며,
> 이때 VIA 검사 대상은 형상 휴리스틱(`is_via_target`)으로 자동 선별됩니다.
> JSON 이 있으면 "어느 PAD에 VIA가 있어야 하는가"를 설계 기준으로 확정할 수 있어 더 정확합니다.

VIA 설계도 마스크(옵션)는 "설계상 VIA 가 있어야 할 좌표"만 흰 점으로 찍은 같은 크기의 이진 PNG 입니다.
점의 **크기는 의미가 없고 위치(무게중심)만** 사용합니다. 헬퍼로 만들 수 있습니다.

```python
via_cad = build_via_design_mask((220, 220), [(107.5, 35.4), (150.9, 163.0)], radius=2)
```

### 0.4 전체 데이터 흐름

```
   입력 이미지(BGR)          PAD 설계도 PNG   설계도 JSON   [옵션] VIA 설계도 PNG
         │                        │              │                 │
         ▼                        │              │                 │
┌──────────────────┐              │              │                 │
│ STEP1 전처리      │  Gray→Blur→Otsu             │                 │
└────────┬─────────┘              │              │                 │
         │ binary                 │              │                 │
         ▼                        │              │                 │
┌──────────────────┐              │              │                 │
│ STEP2 PAD 분할    │  Open→Close→구멍메움         │                 │
│                  │  →연결요소 라벨링             │                 │
└────────┬─────────┘              │              │                 │
         │ label_map, pads        │              │                 │
         ├────────────────────────┤              │                 │
         ▼                        ▼              │                 │
┌────────────────────────────────────────┐       │                 │
│ STEP3 PAD 유무 검사        ⇒ code "24"  │       │                 │
│   설계→실측 커버리지  = 누락             │       │                 │
│   실측→설계 중첩률    = 과잉             │       │                 │
│   + 설계 PAD ↔ 실측 PAD 매칭표 생성      │       │                 │
└────────┬───────────────────────────────┘       │                 │
         │ matches, cad_label_map                ▼                 ▼
         ▼
┌──────────────────────────────────────────────────────────────────────┐
│ STEP4 VIA 검사                                        ⇒ code "99"     │
│   기준형상 = 설계 PAD(dilate 복원)                                    │
│   후보 = (어두움) AND (Black-hat 응답 큼)                             │
│   중심 = 어둠 가중 무게중심(서브픽셀)                                  │
│   편심 = ‖VIA중심 − PAD중심‖ / PAD등가반지름                          │
│   [옵션] 설계 VIA 점 ↔ 검출 VIA 대조 → VIA_MISSING / VIA_EXTRA         │
└────────┬─────────────────────────────────────────────────────────────┘
         │ findings, defects
         ▼
┌──────────────────────────────────────┐
│ STEP5 판정 종합 + 시각화               │
│   우선순위("24" > "99")로 최종 코드 결정│
│   결과 이미지 = 원본 스케일 + 마커만    │
└────────┬─────────────────────────────┘
         ▼
 InspectionResult(.code: str, .result_image, .defects, .summary())
```

---

## 1. STEP 1 — 전처리 및 이진화

`step1_preprocess(image, cfg) -> PreprocessResult`

### 1.1 알고리즘

```
BGR ──cvtColor──▶ Gray ──GaussianBlur(3×3)──▶ Blurred ──Otsu threshold──▶ Binary(PAD=255)
```

1. **그레이 변환** — `cv2.COLOR_BGR2GRAY`.
   PAD(구리/도금)와 배경(기판)은 명도 차가 크므로 색상 정보 없이도 충분히 분리됩니다.
2. **가우시안 블러** (`blur_ksize=3`) — 센서 노이즈로 인한 이진화 경계의 들쭉날쭉함을 억제합니다.
   커널이 너무 크면 VIA 같은 작은 구조가 뭉개지므로 3×3 으로 제한합니다.
3. **Otsu 이진화** — 히스토그램을 두 클래스로 나눌 때 **클래스 간 분산이 최대**가 되는 임계값 $t^*$ 를 자동 탐색합니다.

$$t^{*}=\arg\max_{t}\; \omega_0(t)\,\omega_1(t)\,\bigl[\mu_0(t)-\mu_1(t)\bigr]^2$$

여기서 $\omega_i$ 는 각 클래스의 화소 비율, $\mu_i$ 는 평균 밝기입니다.

### 1.2 왜 Otsu 인가

- 이미지마다 조명/노출이 달라 **고정 임계값은 일반화되지 않습니다.**
  실제 47장 테스트에서 Otsu 임계값은 **68~104** 범위로 분포했습니다.
- 적응형 이진화(`adaptiveThreshold`)는 국소 창 안에 PAD 밖에 없을 때
  PAD 내부를 배경으로 오인해 조각내는 문제가 있어 채택하지 않았습니다.
- Otsu 는 배경/PAD 두 봉우리(bimodal) 히스토그램에 최적이며, 이 데이터셋이 정확히 그렇습니다.

### 1.3 파라미터

| 파라미터 | 기본값 | 설명 |
|---|---|---|
| `blur_ksize` | 3 | 가우시안 커널. 0 또는 <3 이면 블러 생략 |
| `manual_thresh` | -1 | ≥0 이면 Otsu 대신 이 값을 고정 사용 |
| `otsu_bias` | 0 | Otsu 결과에 더할 오프셋. **+** 는 PAD를 더 작게, **−** 는 더 크게 |

### 1.4 반환

`PreprocessResult(bgr, gray, blurred, binary, threshold)` — 모든 중간 산출물이 그대로 보존되어
단계별 모드에서 즉시 확인할 수 있습니다.

---

## 2. STEP 2 — PAD 분할

`step2_segment_pads(pre, cfg) -> SegmentResult`

### 2.1 알고리즘

```
Binary ─▶ Opening(3×3) ─▶ Closing(3×3) ─▶ 구멍 메움(flood fill) ─▶ 연결요소 라벨링 + 면적 필터
```

#### (1) 형태학적 정리

| 연산 | 목적 |
|---|---|
| **Opening** (침식→팽창) | 배경에 흩어진 밝은 점 노이즈 제거. PAD 크기는 거의 보존 |
| **Closing** (팽창→침식) | PAD 내부의 작은 틈이나 가장자리 요철 봉합 |

3×3 정사각 커널을 쓰는 이유는 이 데이터의 최소 PAD 반지름이 약 6px 이라
그 이상의 커널은 작은 PAD 자체를 변형시키기 때문입니다.

#### (2) 구멍 메움 — `_fill_holes`

**VIA 는 PAD 내부의 어두운 점이므로 이진화하면 "구멍"이 됩니다.**
이 상태로 두면 PAD 면적/원형도/무게중심이 모두 왜곡되므로 반드시 메워야 합니다.

```
1. 마스크 반전 (배경=255)
2. 테두리의 모든 배경 화소를 시드로 flood fill → 128 로 칠함  (외부 배경)
3. 여전히 255 로 남은 화소 = 외부와 연결되지 않은 배경 = 내부 구멍
4. 원본 마스크 ∪ 구멍 = 메워진 마스크
```

> **주의:** 흔히 쓰는 "네 모서리에서만 flood fill" 방식은 모서리에 PAD 가 걸쳐 있으면
> 외부 배경을 못 칠하고 배경 전체를 구멍으로 오인합니다.
> 그래서 이 구현은 **테두리 전체를 순회하며 배경인 화소를 모두 시드로** 사용합니다.

메워낸 구멍은 `SegmentResult.hole_mask` 로 따로 반환되어 VIA 후보 확인에 참고할 수 있습니다.

#### (3) 연결요소 라벨링 — `_describe_components`

`cv2.connectedComponentsWithStats(connectivity=8)` 로 라벨링한 뒤,
`min_pad_area` 미만은 버리고 살아남은 요소에 **1부터 다시 번호를 부여**합니다.
각 PAD 마다 다음 특징을 계산합니다.

| 특징 | 정의 |
|---|---|
| `area` | 화소 수 |
| `bbox` | 외접 사각형 (x, y, w, h) |
| `centroid` | 무게중심 $(\bar x,\bar y)$ |
| `perimeter` | 외곽 컨투어 길이 `cv2.arcLength` |
| `circularity` | $4\pi A / P^2$ — 완전한 원이면 1.0, 길쭉할수록 0에 근접 |
| `equiv_radius` | $\sqrt{A/\pi}$ — 같은 면적을 갖는 원의 반지름 |
| `touches_border` | 외접 사각형이 이미지 경계에 닿는지 |

`equiv_radius` 를 쓰는 이유는 PAD 가 완전한 원이 아니어도(모서리가 눌린 사각형 등)
**면적 기반이라 안정적**이기 때문입니다. 외접원 반지름은 돌기 하나에 크게 흔들립니다.

### 2.2 VIA 검사 대상 선별 — `is_via_target`

이 기판에는 원형 PAD, 직사각 PAD, 배선 트레이스가 섞여 있고 **VIA 는 원형 PAD 에만** 존재합니다.
설계 JSON 이 없을 때 사용되는 형상 휴리스틱입니다.

```python
경계에 닿지 않을 것                    (touches_border == False)
via_target_min_area ≤ area ≤ via_target_max_area
circularity ≥ via_target_min_circularity
```

| 파라미터 | 기본값 | 근거 |
|---|---|---|
| `via_target_min_area` | 150 | 반지름 ≈ 7px 미만이면 VIA(반지름 2~5px)를 담을 수 없음 |
| `via_target_max_area` | 4000 | 대형 그라운드 패드/트레이스 배제 |
| `via_target_min_circularity` | 0.72 | 실측 원형 PAD 는 0.90~1.00, 직사각 PAD 는 0.55~0.75 |
| `via_target_exclude_border` | True | 잘린 PAD 는 중심 추정이 불가능 |

### 2.3 설계도 생성 헬퍼 — `build_design_mask`

실측 PAD 마스크로부터 "실물보다 살짝 작은" 설계도를 만듭니다 (요구사항 2번).

```python
eroded = erode(pad_mask, ellipse(2*erode_px+1))
```

침식으로 **완전히 소멸하는 작은 PAD** 는 설계 정보가 통째로 사라지므로,
연결요소별로 잔존 여부를 확인해 소멸한 PAD 만 1px 침식본으로 되살립니다.

---

## 3. STEP 3 — PAD 유무 검사 (code 24)

`step3_check_pad_presence(seg, cad_mask, cfg) -> PadPresenceResult`

### 3.1 핵심 아이디어

설계도는 **의도적으로 침식**되어 있으므로, 정상 PAD 라도 설계와 실측이 100% 일치하지 않습니다.
따라서 IoU 같은 대칭 지표는 부적합하고, **방향이 다른 두 비율을 각각** 봅니다.

| 검사 | 지표 | 방향 |
|---|---|---|
| **누락** MISSING_PAD | $\text{coverage}=\dfrac{\lvert \text{설계PAD} \cap \text{실측} \rvert}{\lvert \text{설계PAD} \rvert}$ | 설계 → 실측 |
| **과잉** EXTRA_PAD | $\text{overlap}=\dfrac{\lvert \text{실측PAD} \cap \text{설계전체} \rvert}{\lvert \text{실측PAD} \rvert}$ | 실측 → 설계 |

### 3.2 누락 검출

설계 PAD 하나하나에 대해 coverage 를 계산합니다.

- 설계가 실측보다 작으므로 **정상이면 coverage ≈ 1.0** 이어야 합니다.
- PAD 가 지워졌다면 coverage ≈ 0 입니다.
- 임계값 `cad_coverage_thresh = 0.55` — 정상(≈1.0)과 누락(≈0)의 중간에 넉넉히 위치합니다.
  약간의 위치 틀어짐이나 이진화 변동을 흡수할 수 있는 여유가 큽니다.

coverage 가 통과한 설계 PAD 는 **겹친 화소에서 가장 많은 지분을 차지하는 실측 PAD** 와 매칭합니다.

```python
ids, counts = np.unique(seg.label_map[region & actual], return_counts=True)
best = ids[ids>0][argmax(counts[ids>0])]
matches[cad_pad_id] = best
```

이 `matches` 표(설계 id → 실측 id)는 **STEP4 에서 결정적으로 중요**합니다 (§4.2 참조).

### 3.3 과잉 검출

실측 PAD 중 위에서 매칭되지 않은 것들이 후보입니다. 오검을 막기 위해 3중 필터를 겁니다.

| 조건 | 파라미터 | 이유 |
|---|---|---|
| 설계와의 중첩률 < 0.30 | `extra_overlap_thresh` | 정상 PAD 는 침식된 설계와도 약 **0.70~0.80** 겹침. 0.30 이면 충분히 안전한 여유 |
| 면적 ≥ 120 | `min_extra_area` | 이진화 노이즈로 생긴 작은 조각 무시 |
| 경계에 안 닿음 | `ignore_border_extra` | 화면 밖에서 걸쳐 들어온 구조물은 설계 범위 밖 |

> `extra_overlap_thresh` 를 0.30 처럼 **낮게** 잡는 것이 핵심입니다.
> "정상이면 많이 겹친다"가 아니라 "**과잉이면 거의 안 겹친다**"를 판정하기 때문입니다.

### 3.4 중간 확인용 오버레이

`PadPresenceResult.overlay` 에 설계(빨강 채널) / 실측(초록 채널)을 겹친 이미지가 생성됩니다.
- 노란색 = 설계·실측 모두 존재 (정상)
- 빨간색만 = 설계에만 존재 (누락 의심)
- 초록색만 = 실측에만 존재 (과잉 의심)
- 빨간 원 = 누락 확정, 자홍 원 = 과잉 확정

---

## 4. STEP 4 — VIA 검사 (code "99")

`step4_check_via(image, seg, pres, cad_meta, cfg, via_design) -> ViaResult`

가장 복잡한 단계입니다. VIA 는 지름 4~10px 의 작은 구조라 서브픽셀 정밀도가 필요합니다.

### 4.1 검사 대상 PAD 결정

우선순위대로 세 가지 방식이 있습니다.

```
(A) via_design_check=True + VIA 설계 마스크가 주어짐
      대상 = {설계에 VIA 점이 있는 PAD}  ∪  {is_via_target 을 통과한 PAD}
(B) 설계 JSON 에 via_expected 가 있음
      대상 = via_expected == true 인 설계 PAD → matches 로 실측 PAD 로 변환
(C) 그 외
      대상 = is_via_target(형상 휴리스틱)
```

설계 기준을 우선하는 이유는, VIA 가 있어야 하는지 여부는 **설계가 정의하는 사실**이지
영상에서 추론할 문제가 아니기 때문입니다.

(A) 에서 두 집합의 **합집합**을 쓰는 이유:

- 설계에 VIA 점이 있는 PAD만 보면 → "설계엔 없는데 실물엔 있는 VIA"(과잉)를 영영 못 봅니다.
- 그렇다고 전체 PAD 를 보면 → 사각형 랜드나 테두리에 걸친 PAD 의 조명 얼룩을
  VIA_EXTRA 로 오검출합니다. 그래서 과잉 후보는 `is_via_target` 을 통과한
  원형 PAD 로 제한합니다.

### 4.1.1 [옵션] VIA 설계도 대조 — `via_design_check`

`_parse_via_design()` 이 VIA 설계 마스크를 연결요소로 분해하고, 각 점의 무게중심이
**어느 설계 PAD 안에 들어가는지**로 소속을 정합니다 (`pres.cad_label_map` 조회).
PAD 밖에 찍힌 점은 설계 오류로 보고 무시합니다.

그 결과 PAD 마다 "설계상 VIA 가 있어야 하는가(`expected`)"가 정해지고,
검출 결과와 교차하여 판정합니다.

| 설계 | 실물 | 판정 | kind |
|:---:|:---:|---|---|
| O | O (정중앙) | 양품 | — |
| O | O (쏠림) | 불량 | `VIA_OFFSET` |
| O | X | 불량 | `VIA_MISSING` |
| X | O | 불량 | `VIA_EXTRA` |
| X | X | 양품 | — |

`via_design_check=False`(기본)이면 `expected` 는 항상 `True` 로 고정되어
**기존 동작과 완전히 동일**하며, `VIA_EXTRA` 는 절대 발생하지 않습니다.
즉 이 옵션은 순수 가산 기능입니다.

```python
cfg = InspectConfig(via_design_check=True)
res = inspect("cur.png", "cad.png", "cad.json", cfg, via_design="cad_via.png")
```

### 4.2 기준 형상: 왜 실측 PAD 가 아니라 **설계 PAD** 인가

이 프로젝트에서 정확도를 좌우한 가장 중요한 설계 결정입니다.

**문제:** VIA 가 가장자리로 크게 쏠리면, 어두운 VIA 가 **어두운 배경과 이어져 버립니다.**
그러면 이진화 결과에서 PAD 윤곽에 **노치(notch)** 가 파이고,
`_fill_holes` 는 이를 메울 수 없습니다 (외부와 연결되어 있으므로 "구멍"이 아님).
결과적으로:

- VIA 영역이 검색 범위 밖으로 밀려나 → **VIA_MISSING 오판정**
- PAD 무게중심이 반대편으로 끌려가 → **편심량 계산도 오염**

```
   정상 VIA (구멍)              쏠린 VIA (노치)
   ┌─────────────┐             ┌─────────────┐
   │   ███████   │             │  ███████    │
   │  █████████  │             │ ██████████  │
   │  ████○████  │             │ ◐█████████  │  ← 배경과 연결
   │  █████████  │             │ ◐█████████  │     = 구멍이 아님
   │   ███████   │             │  ███████    │     = 메울 수 없음
   └─────────────┘             └─────────────┘
   fill_holes 로 복원 O         fill_holes 로 복원 X
```

**시도했으나 실패한 대안 (기록):**

| 시도 | 결과 |
|---|---|
| PAD 침식량 축소(3→1) + Black-hat 도입 | 쏠림 30건 여전히 미검출 |
| 형태학적 Closing 으로 노치 복원 | **더 악화.** 비원형 PAD 의 오목부로 배경이 끌려 들어와 정상 19건 오검, 분리 마진 −0.708 |

**채택한 해법:** 설계 PAD 를 기준 형상으로 사용합니다.

1. **설계도는 VIA 결함의 영향을 전혀 받지 않습니다.** 노치가 생길 수 없습니다.
2. **"정중앙"의 정의 자체가 설계 중심**입니다. 실측 중심보다 오히려 타당한 기준입니다.

```python
use_cad  = (pres is not None and cad_id is not None)
src_mask = (pres.cad_label_map == cad_id) if use_cad else (seg.label_map == pid)

# 설계 PAD 는 실물보다 erode_px 만큼 작으므로 공칭 크기로 되돌린다
if use_cad and cad_erode_px > 0:
    shape = cv2.dilate(shape, ellipse(2*cad_erode_px+1))

inner  = cv2.erode(shape, ellipse(2*via_pad_erode+1))   # 테두리 여유 1px
```

설계도가 없으면 기존대로 실측 PAD(구멍 메움본)를 사용해 **하위 호환을 유지**합니다.
`finding["reference"]` 에 `"cad"` / `"actual"` 중 무엇을 썼는지 기록됩니다.

여기서 얻은 형상으로 PAD 중심 $(c_x,c_y)$, 면적 $A$, 등가반지름 $r=\sqrt{A/\pi}$ 를 계산합니다.

### 4.3 VIA 후보 검출 — 두 조건의 AND

#### 조건 (1) 전역 어두움

```python
med = median(roi_gray[inner > 0])          # PAD 내부 밝기 중앙값
thr = med * via_dark_ratio                 # 0.62
dark = (roi_gray < thr) & (inner > 0)
```

절대값이 아니라 **PAD 자신의 중앙값 대비 비율**을 쓰므로 조명 변화에 강건합니다.
중앙값은 평균과 달리 VIA 자체(전체의 5~10%)에 거의 영향받지 않습니다.

실측치: PAD 중앙값 ≈ 150~160, VIA 코어 ≈ 30~55 → 비율 약 0.2~0.35 이므로 0.62 는 충분한 여유.

#### 조건 (2) 국소 Black-hat 응답

어두움만으로는 **PAD 테두리의 어두운 전이 영역**이 함께 잡힙니다.
이를 배제하려고 PAD 를 크게 침식하면 이번엔 가장자리로 쏠린 VIA 를 놓칩니다. 딜레마입니다.

**Black-hat 변환**이 이 딜레마를 풉니다.

$$\mathrm{BlackHat}(I) = (I \bullet S) - I \qquad (\bullet = \text{closing})$$

| 구조 | Black-hat 응답 | 이유 |
|---|---|---|
| PAD 테두리 | ≈ 0 | **단조 경사**. closing 해도 원본과 거의 같음 |
| VIA | **큼** | **고립된 우물**. closing 이 주변 밝기로 메워버림 |

```python
ks = clip(round(radius * via_blackhat_ksize_ratio) | 1, 5, 31)   # VIA보다 크고 PAD보다 작게
bh = morphologyEx(roi_fill, MORPH_BLACKHAT, ellipse(ks))
bh_thr = max(via_blackhat_min, med * via_blackhat_ratio)         # 절대·상대 임계 병용
cand = dark & (bh > bh_thr) & (inner > 0)
```

커널 크기를 PAD 반지름에 비례시키는 이유는, PAD 크기가 이미지마다 크게 다르기 때문입니다
(반지름 6~20px). 고정 커널은 큰 PAD 에서 VIA 를 못 메우거나 작은 PAD 를 통째로 삼킵니다.

##### Black-hat 전 PAD 바깥 채우기 (필수)

```python
roi_fill = np.where(shape > 0, roi_gray, np.uint8(round(med)))
```

**이것을 빠뜨리면 가장자리로 쏠린 VIA 를 놓칩니다.**
Closing = dilate → erode 인데, VIA 가 PAD 경계 근처에 있으면
erode 단계에서 **바깥의 어두운 배경(밝기 ~20)이 최소값으로 끌려 들어와**
closing 결과가 내려앉고 Black-hat 응답이 사라집니다.

실제 사례 (`118-...10-173_Ref`, pad#15, 정답 편심 0.516):

| | 채우기 전 | 채우기 후 |
|---|---|---|
| dark 화소 | 23 | 23 |
| Black-hat 통과 | 6 | 충분 |
| 면적 하한(4) 통과 | 없음 → **VIA_MISSING 오판** | 검출 성공 |

PAD 바깥을 중앙값(=평탄한 PAD 밝기)으로 메우면 경계 효과가 사라져 응답이 정상화됩니다.

#### 후보 정리 및 선택

```python
연결요소 중 via_min_blob ≤ area ≤ pad_area * via_max_blob_ratio 인 것 중 최대 면적 선택
```

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `via_min_blob` | 4 | 이보다 작으면 노이즈 |
| `via_max_blob_ratio` | 0.25 | PAD 면적의 25% 초과는 VIA 가 아니라 조명/오염 |

조건을 만족하는 덩어리가 하나도 없으면 → **`VIA_MISSING`** (code `"42"`).

##### 여기서 `MORPH_OPEN 2×2` 를 걸면 안 된다 (제거된 단계)

원래는 잡티 제거용으로 `cand = morphologyEx(cand, MORPH_OPEN, ones(2,2))` 가
들어 있었습니다. VIA 대비를 3단계(코어 = PAD 밝기의 0.12 / 0.25 / 0.45배)로 넓혀
테스트하면서 **연한 VIA 만 통째로 놓치는 것**을 발견했습니다.

단계별 화소 수 실측 (오검출 6건):

```
   dark 통과   →   dark ∧ blackhat   →   MORPH_OPEN 2×2   →   면적하한 4
      4~5 px           4~5 px                0 px  ✗            (도달 못 함)
```

`MORPH_OPEN` 은 "구조요소가 통째로 들어가는 자리"만 남깁니다.
연한 VIA 의 후보는 4~5px 이고 모양이 십자·대각이라 **2×2 정사각이 들어갈 자리가
하나도 없어** 전부 지워집니다. 결과는 `VIA_MISSING` 오검출입니다.

대안을 비교했습니다 (44장 테스트셋).

| 후보 정리 방식 | PAD 정확도 | 이미지 | 연함(0.45) | 분리 여유 |
|---|---|---|---|---|
| `MORPH_OPEN 2×2` (기존) | 766/772 = 0.9922 | 36/37 | 231/237 | 0.218 |
| **면적 하한만 (현재)** | **772/772 = 1.0000** | **37/37** | **237/237** | **0.313** |
| 면적 하한 ≥ 5 | 766/772 = 0.9922 | 35/37 | 231/237 | 0.313 |
| 면적 하한 ≥ 6 | 757/772 = 0.9806 | 31/37 | 222/237 | 0.313 |

**opening 은 불필요할 뿐 아니라 해롭습니다.** 잡티 제거는 이미 연결요소 면적
하한(`via_min_blob = 4`)이 담당하고 있고, 그쪽이 모양에 대해 중립적이라 더 안전합니다.
`dark` 와 `blackhat` 두 조건을 **동시에** 통과한 화소가 우연히 4개나 이어질 확률은
낮습니다 — 촬영 잡음 σ=3/6/10 을 주입해도 오검출(`OK → VIA_OFFSET`)은 0건이었고,
열화는 언제나 `MISSING` 방향으로만 나타났습니다.

> 이 결함은 **VIA 대비를 0.25 한 종류로만 테스트했을 때는 드러나지 않았습니다.**
> 테스트셋의 다양성이 곧 검출력이라는 사례입니다.

### 4.4 VIA 중심 추정 — 어둠 가중 무게중심

이진 무게중심은 화소 양자화 오차가 큽니다. VIA 가 12화소 정도면 오차가 ±0.5px 에 달해
반지름 8px PAD 에서 편심량 0.06 의 잡음이 됩니다.

**어두운 정도를 가중치**로 쓴 무게중심이 훨씬 안정적입니다 (서브픽셀).

$$w(x,y)=\max\bigl(0,\; t_{\text{dark}} - I(x,y)\bigr)\cdot \mathbb{1}_{\text{blob}}(x,y)$$

$$v_x=\frac{\sum x\,w}{\sum w},\qquad v_y=\frac{\sum y\,w}{\sum w}$$

VIA 는 가우시안 형태의 우물이므로 밝기 가중 중심이 물리적 중심에 정확히 대응합니다.

**효과:** 정상 PAD 의 편심량 최대치가 **0.497 → 0.142** 로 떨어졌습니다.

### 4.5 편심 판정 — 상대·절대 이중 임계

$$d=\lVert (v_x,v_y)-(c_x,c_y) \rVert,\qquad \text{offset\_norm}=\frac{d}{r}$$

```python
if offset_norm > via_offset_tol and d > via_offset_min_px:
    → VIA_OFFSET  (code 99)
else:
    → OK
```

**두 조건을 AND 로 묶는 이유:**
반지름이 작은 PAD(r ≈ 8)에서는 2px 의 미세한 오차도 정규화하면 0.26 이 되어 임계값 0.25 를 넘습니다.
실제로 이 때문에 양품 1장이 오검되었습니다 (`d = 2.11px`, `norm = 0.2601`).
**절대 하한 2.2px** 를 함께 요구하면 작은 PAD 의 양자화 오차를 흡수할 수 있습니다.

| 파라미터 | 기본값 | 의미 |
|---|---|---|
| `via_offset_tol` | 0.25 | 반지름 대비 허용 편심 비율 |
| `via_offset_min_px` | 2.2 | 편심으로 인정할 최소 절대 거리(px) |

### 4.6 반환값

`ViaResult(via_mask, findings, defects, target_pad_ids)`

`findings` 는 PAD 하나당 dict 하나로, 판정 근거 수치를 전부 담습니다.

```python
{'pad_id': 15, 'cad_pad_id': 15, 'reference': 'cad', 'via_expected': True,
 'pad_center': [150.96, 163.07], 'pad_area': 213, 'pad_radius': 8.23,
 'via_center': [147.28, 164.72], 'via_area': 18,
 'offset_px': 4.28, 'offset_norm': 0.5196,
 'tolerance': 0.25, 'tolerance_px': 2.2, 'status': 'VIA_OFFSET'}
```

| status | 의미 |
|---|---|
| `OK` | VIA 가 허용 범위 안에 중앙 배치 (또는 설계·실물 모두 VIA 없음) |
| `VIA_OFFSET` | VIA 는 있으나 편심 |
| `VIA_MISSING` | 있어야 할 VIA 를 찾지 못함 |
| `VIA_EXTRA` | 설계에 없는 VIA 가 검출됨 (설계 대조 옵션 전용) |
| `SKIP` | 침식 후 PAD 가 너무 작아 검사 불가 |

`via_expected` 필드는 설계 대조 옵션이 꺼져 있으면 항상 `True` 입니다.

---

## 5. STEP 5 — 판정 종합 및 시각화

`step5_render(image, seg, pres, via, cfg) -> InspectionResult`

### 5.1 최종 코드 산출

```python
present = {d.code for d in defects}                 # 검출된 코드 집합
codes   = [c for c in cfg.priority if c in present] # 우선순위 순 정렬 ("24", "99")
code    = codes[0] if codes else CODE_OK            # 없으면 "1"
```

"24" 와 "99" 가 동시에 나오면 `code == "24"`, `codes == ["24", "99"]` 가 됩니다.
**정보를 잃지 않으면서** 요구사항("24" 우선)을 만족합니다.

### 5.2 결과 이미지 — 원본 스케일, 마커만

결과 이미지는 **입력 이미지와 동일한 해상도**이며, 텍스트 바나 여백을 덧붙이지 않습니다.
판정 코드·메시지는 이미지가 아니라 `InspectionResult.code` / `.message` 로 받습니다.
따라서 결과 이미지를 원본과 픽셀 단위로 겹쳐 보거나 그대로 후단 파이프라인에 넘길 수 있습니다.

`cfg.draw_overlay = False` 로 두면 마커조차 그리지 않고 원본 사본을 그대로 반환합니다.

| 표시 | 색 | 의미 |
|---|---|---|
| PAD 외곽선 | 초록 | 정상 PAD |
| PAD 외곽선 | 자홍 | 과잉 PAD |
| PAD 외곽선 | 주황 | VIA 결함 PAD |
| 얇은 회색 윤곽 | 회색 | 설계도 경계 |
| 십자 마커 | 노랑 | VIA 정상 위치 |
| 십자 + 화살표 | 주황 | VIA 편심 (PAD중심 → VIA중심) |
| 십자 마커 | 보라 | VIA 과잉 위치 |
| 원 + X | 빨강 | PAD 누락 |
| 원 | 자홍 | PAD 과잉 |
| 원 | 파랑 | VIA 없음 |
| 원 | 주황 | VIA 편심 |
| 원 | 보라 | VIA 과잉 |

---

## 6. 두 가지 사용 방식

### 6.1 원샷 모드

```python
from pad_via_inspector import inspect

res = inspect("board.png", "board_cad.png", "board_cad.json")

print(res.code)        # "1" / "24" / "99" / "-1"   (str)
print(res.codes)       # 예: ["24", "99"]
print(res.ok)          # True / False
print(res.message)     # 'MISSING_PAD x1, VIA_OFFSET x2'
cv2.imwrite("out.png", res.result_image)   # 입력과 동일 해상도

for d in res.defects:
    print(d.kind, d.pad_id, d.position, d.detail)
```

**[옵션] VIA 설계도 대조** — 5번째 인자로 VIA 설계 마스크를 넘기고 `via_design_check=True` 로 켭니다.

```python
from pad_via_inspector import InspectConfig, inspect

cfg = InspectConfig(via_design_check=True)
res = inspect("board.png", "board_cad.png", "board_cad.json", cfg, "board_via.png")
# 설계엔 없는데 실물에 VIA 가 있으면 VIA_EXTRA -> code "99"
```

인자를 주지 않거나 `via_design_check=False` 이면 기존 동작과 **완전히 동일**합니다.

내부에서 예외가 나도 던지지 않고 `code = "-1"` 인 결과 객체를 반환하므로
배치 처리 중 한 장 때문에 전체가 멈추지 않습니다.

### 6.2 단계별 모드

```python
from pad_via_inspector import (InspectConfig, step1_preprocess, step2_segment_pads,
                               step3_check_pad_presence, step4_check_via, step5_render)

cfg  = InspectConfig()

pre  = step1_preprocess("board.png", cfg)
print("임계값:", pre.threshold)
cv2.imshow("binary", pre.binary)

seg  = step2_segment_pads(pre, cfg)
print("PAD 개수:", len(seg.pads))
cv2.imshow("holes", seg.hole_mask)          # 메워낸 구멍 = VIA 후보

pres = step3_check_pad_presence(seg, "board_cad.png", cfg)
print("누락:", len(pres.missing), "과잉:", len(pres.extra))
cv2.imshow("overlay", pres.overlay)         # 설계(적) vs 실측(녹)

via  = step4_check_via(pre.bgr, seg, pres, "board_cad.json", cfg)
#     ↑ 설계 대조를 쓰려면 cfg.via_design_check=True 로 두고 6번째 인자를 추가
#     via = step4_check_via(pre.bgr, seg, pres, "board_cad.json", cfg, "board_via.png")
for f in via.findings:
    print(f)                                # 판정 근거 수치 전부

res  = step5_render(pre.bgr, seg, pres, via, cfg)
print("최종 코드:", res.code)               # str
```

각 단계 결과가 독립 객체로 반환되므로 **임의 단계만 교체·재실행** 할 수 있습니다.
예: 임계값만 바꿔 STEP1 만 다시 돌리고 STEP2 이후를 재사용.

### 6.3 CLI

```bash
python pad_via_inspector.py board.png board_cad.png --meta board_cad.json --out result.png

# VIA 설계도 대조까지 켜기
python pad_via_inspector.py board.png board_cad.png --meta board_cad.json \
       --via-design board_via.png --out result.png
```

판정 결과는 **stdout 의 JSON** 으로 나옵니다 (`{"code": "99", "codes": ["99"], ...}`).
프로세스 종료 코드는 OS 제약상 int 여야 하므로 **양품 0 / 불량 1** 로만 구분합니다.
세부 코드가 필요하면 JSON 의 `code` 필드를 파싱하세요.

---

## 7. 테스트셋 생성 알고리즘 (`make_testset.py`)

### 7.1 산출물

```
testset/
├── images/      <name>.png            검사 대상 이미지 (VIA·결함 주입 완료)
├── design/      <name>_cad.png        PAD 설계도 이진 마스크
│                <name>_cad.json       설계도 메타 (via_expected 포함)
│                <name>_via.png        VIA 설계도 (설계상 VIA 위치만 흰 점)
├── preview/     <name>_prev.png       원본|검사이미지|설계도 3분할 미리보기
└── ground_truth.json                  정답 라벨
```

`ground_truth.json` 은 두 개의 정답 코드를 함께 담습니다.

| 키 | 의미 |
|---|---|
| `expected_code` | `via_design_check=False` (기본) 일 때의 정답 |
| `expected_code_design` | `via_design_check=True` 일 때의 정답 (VIA 과잉 반영) |

### 7.2 PAD 형상 균일화 (`PAD_UNIFORM`)

원본 이미지의 PAD 는 형상 편차가 극심합니다(면적 67~3068, 46배 차이).
이 상태에서는 "설계 PAD 무게중심" 자체가 형상 편차 때문에 흔들려서
편심 측정의 기준점이 신뢰할 수 없습니다.

**원본 위치·배경·조명·잡음은 그대로 두고, PAD 모양만** 동일 정사각으로 다시 그립니다.

```
   원본 PAD (제각각)              균일화 후
   ╭───╮  ▄▄▄▄▄▄▄▄               ┌───┐   ┌───┐
   │   │  ▀▀▀▀                   │   │   │   │      전부 16px 정사각
   ╰───╯      ▄▄                 └───┘   └───┘      (모서리 2px 라운드)
```

파이프라인 — **다시 분할하는 것이 핵심**입니다.

```
원본 → 분할 → 배경 통계 측정 → PAD 자리를 배경으로 지움
                                      ↓
                          동일 정사각 레이어를 PAD 텍스처로 재도색
                                      ↓
                          ★ 다시 전처리 + 분할 ★
                                      ↓
                  설계도 · 정답 · 검사 이미지를 전부 이 결과에서 파생
```

재분할하지 않으면 설계도는 원본 형상 기준인데 검사 이미지는 정사각이라
둘이 어긋납니다.

| 지표 (VIA 대상 PAD) | 균일화 전 | 균일화 후 |
|---|---|---|
| bbox | 제각각 | **761/761 = 17×17** |
| 종횡비 | 0.21 ~ 5.43 | **1.000** (std 0.0000) |
| 면적 | 67 ~ 3068 | 228 ~ 278 (std 1.8) |
| 원형도 | — | 0.777 ~ 0.989 |

```python
PAD_UNIFORM = True     # False 면 원본 PAD 형상을 그대로 사용
PAD_SIDE    = 16       # 정사각 한 변(px). 면적 약 253 -> via_target 범위 안
PAD_CORNER  = 2        # 모서리 라운드 반지름(px)
PAD_MIN_GAP = 5        # 이웃 PAD 사이 최소 간격(px)
```

`PAD_CORNER = 2` 가 필요한 이유: **완벽한 정사각의 원형도는 π/4 ≈ 0.785** 로
`via_target_min_circularity = 0.72` 하한에 아슬아슬하게 걸칩니다. 2px 만 둥글려도
0.989 로 올라가 여유가 생깁니다.

설계도 쪽도 788/790 이 정확히 13×13, 88.7% 가 면적 165 로 균일합니다.
남은 2개는 220px 폭 이미지의 오른쪽 경계(x=216)에서 잘린 것으로,
**실제 크롭에서도 생기는 현상**이라 그대로 두었습니다(0.25%).

### 7.3 VIA 렌더링

실제 샘플(`Nosie/`)의 VIA 를 화소 단위로 계측해 파라미터를 맞췄습니다.

측정 결과: PAD 밝기 ≈ 120, VIA 코어 ≈ 30, 코어 지름 4~5px, 소프트 에지 포함 약 8px.
색상은 BGR `[69, 84, 139]` vs PAD `[88, 102, 173]` — **VIA 도 따뜻한 갈색 톤을 유지**합니다.

```python
sigma = rad * 0.55 + VIA_EDGE_SOFT              # 가우시안 감쇠
w     = exp(-(dist² ) / (2σ²))
dark  = 1 - w * (1 - darkness)                  # darkness = 0.12 / 0.25 / 0.45
img[B] *= dark
img[G] *= dark * 1.05                           # 갈색 톤 유지를 위해
img[R] *= dark * 1.22                           # R 을 덜 어둡게
```

`VIA_EDGE_SOFT` 는 1.6 → 0.9, σ 계수는 0.72 → 0.55 로 조정했습니다.
초기값에서는 후광(halo)이 실제 샘플보다 지나치게 컸습니다.

#### VIA 대비 3단계

단일 대비(0.25)만 쓰면 **알고리즘이 얼마나 여유 있는지 알 수 없습니다.**
진함·중간·연함을 균등하게 섞습니다.

```
   dark 0.12          mid 0.25           light 0.45
   ┌───────┐          ┌───────┐          ┌───────┐
   │ ▓▓▓▓▓ │          │ ▓▓▓▓▓ │          │ ▓▓▓▓▓ │
   │ ▓▓█▓▓ │          │ ▓▓▒▓▓ │          │ ▓▓░▓▓ │   ← 코어
   │ ▓▓▓▓▓ │          │ ▓▓▓▓▓ │          │ ▓▓▓▓▓ │
   └───────┘          └───────┘          └───────┘
   실측 0.087~0.168   0.222~0.322        0.422~0.571
                                          ↑ DARK_RATIO 0.62 에 가장 가까움
```

```python
VIA_DARKNESS_LEVELS = (0.12, 0.25, 0.45)
VIA_DARKNESS_NAMES  = ("dark", "mid", "light")
```

배분은 단순 round-robin 이 아니라 **셔플한 주머니(bag)** 에서 뽑습니다.
round-robin 은 이미지마다 항상 `dark` 부터 시작해서 전체 집계가 `dark` 쪽으로
치우칩니다. 결과: dark 266 / mid 252 / light 239 (거의 정확히 1/3씩).

`ground_truth.json` 의 각 VIA 에 `darkness`(실수)와
`darkness_level`(`dark`/`mid`/`light`)이 함께 기록되므로 대비별 정확도를
바로 집계할 수 있습니다.

이 3단계가 [`MORPH_OPEN` 결함](#여기서-morph_open-2x2-를-걸면-안-된다-제거된-단계)을
드러냈습니다.

### 7.4 배치 계획

| 시나리오 | 비율 | VIA 배치 | PAD 결함 |
|---|---|---|---|
| `GOOD` (code `"1"`) | 60% | 전부 정중앙 | 없음 |
| `VIA_DEFECT` (code `"99"`) | 25% | 정중앙 + 쏠림 + 없음 **3종 혼합** (+ 과잉) | 없음 |
| `PAD_DEFECT` (code `"24"`) | 15% | 전부 정중앙 | 누락 / 과잉 / 둘다 |

> 요구사항 "한 이미지에 3개 특징이 다 있거나, 정중앙만 있어야 한다"를 만족합니다.
> 3종 혼합에는 대상 PAD 가 최소 3개 필요하므로(`MIN_VIA_TARGETS_FOR_MIXED`),
> 부족한 이미지는 쏠림 1개만 주입합니다.

- **정중앙**: ±0.4px 지터 (완벽한 정수 좌표는 비현실적)
- **쏠림**: 반지름의 42~60% 만큼 임의 방향. 단 `min(mag, r_pad − r_via − 2.0)` 으로 PAD 밖 이탈 방지
- **과잉(`extra`)**: 이미지에는 **정중앙 VIA 를 정상적으로 그리되**, VIA 설계 마스크에서만 그 점을 뺍니다.
  → 기본 모드에서는 정중앙이므로 **양품**, 설계 대조 모드에서만 `VIA_EXTRA` 로 잡힙니다.
  이 때문에 정답 코드가 모드별로 달라지고, 그래서 `expected_code` / `expected_code_design` 두 개를 저장합니다.

```python
# make_testset.py — 설계 VIA 마스크는 "extra" 를 제외하고 만든다
design_via_pts = [p.centroid for p in targets if plan[p.pad_id] != "extra"]
via_cad = build_via_design_mask(src.shape[:2], design_via_pts, VIA_DESIGN_DOT_RADIUS)
```

### 7.5 PAD 결함 주입

**누락 (`erase_pad`)** — PAD 영역을 5×5 팽창한 뒤 배경 통계로 채우고 국소 블러 적용.
경계에 안 닿고 충분히 큰 PAD 만 대상으로 합니다.

**과잉 (`add_pad`)** — 기존 PAD 의 색 통계를 복사해 빈 공간에 PAD 를 합성합니다.
`PAD_UNIFORM` 이면 **다른 PAD 와 똑같은 정사각**으로 그립니다. 여기만 원형이면
"모양이 다르다"는 단서로 구분되어 형상 균일화의 의미가 사라집니다.

초기 구현은 화소별 독립 가우시안 노이즈를 써서 **소금·후추 노이즈처럼 보였습니다.**
실제 PAD 는 매끄러운 저주파 얼룩을 가지므로 다음과 같이 수정했습니다.

```python
low  = GaussianBlur(N(0,1), sigma=2.5); low /= low.std()     # 저주파 얼룩
high = N(0,1)                                                 # 고주파 그레인
tex  = pad_mean + low*pad_std*0.75 + high*pad_std*0.25
```

### 7.6 정답 라벨 정합성

라벨은 **`pad_id` 가 아니라 `pad_center` 좌표를 키**로 사용합니다.

생성기는 **원본** 이미지를 분할하지만 검사기는 **VIA 가 그려진** 이미지를 분할합니다.
VIA 의 어두운 화소가 Otsu 임계값을 미세하게 이동시켜 `min_pad_area` 통과 여부가 바뀌고,
연번으로 매기는 `pad_id` 가 어긋납니다. 좌표 매칭(반경 6px)은 이 문제에 영향받지 않습니다.

또한 **VIA 를 먼저 그린 뒤 PAD 를 지우므로**, 지워진 PAD 의 VIA 항목이 정답에 남아
오답으로 집계되는 문제가 있었습니다. 지운 직후 해당 항목을 제거하도록 수정했습니다.

```python
erase_pad(img, seg.label_map, victim.pad_id, bg_mean, bg_std)
via_gt = [v for v in via_gt if v["pad_id"] != victim.pad_id]
```

---

## 8. 검증 결과

원본 47장 중 VIA 대상 PAD 를 가진 **44장**으로 테스트했습니다.
**설계 VIA 대조 OFF / ON 두 모드 모두** 측정했습니다.

### 8.1 이미지 단위 (`run_inspection.py`)

```
# OFF: python run_inspection.py          ON: python run_inspection.py --via-design
검사 44장 / 라벨 44장 / 정확도 1.0        (두 모드 동일)
--------------------------------------------------------------
  OK  OK(1)   -> OK(1)       26
  OK  PAD(24) -> PAD(24)      7
  OK  VIA(99) -> VIA(99)     11
--------------------------------------------------------------
오판정 없음
```

검출된 결함 종류 집계 — **옵션이 순수하게 추가적(additive)** 임을 보여줍니다.

| 모드 | MISSING_PAD | EXTRA_PAD | VIA_MISSING | VIA_OFFSET | VIA_EXTRA |
|---|---|---|---|---|---|
| `via_design_check=False` | 5 | 4 | 26 | 41 | — |
| `via_design_check=True` | 5 | 4 | 26 | 41 | **11** |

주입한 VIA 과잉이 정확히 11건, 검출도 11건이며 **다른 항목 수치는 하나도 변하지 않았습니다**
(= 오검출 0건).

### 8.2 PAD 단위 (`eval_padlevel.py`)

```
# 설계 VIA 대조 = OFF                     # 설계 VIA 대조 = ON
           OK   OFFSET MISS  EXTRA                 OK   OFFSET MISS  EXTRA
center     705  0      0     0           center    705  0      0     0
shift      0    41     0     0           shift     0    41     0     0
none       0    0      26    0           none      0    0      26    0
extra      11   0      0     0           extra     0    0      0     11
--------------------------------------   --------------------------------------
PAD 단위 정확도 = 783/783 = 1.0000        PAD 단위 정확도 = 783/783 = 1.0000
```

`extra` 행의 **정답이 모드에 따라 바뀌는 점**에 주목하세요.
설계 대조가 꺼져 있으면 그 PAD 의 VIA 는 정중앙에 잘 찍혀 있으므로 `OK` 가 정답이고,
켜져 있으면 설계에 없는 VIA 이므로 `VIA_EXTRA` 가 정답입니다. 두 경우 모두 100% 일치합니다.

### 8.3 편심 임계값 분리도

| 그룹 | n | 평균 | p95 / p5 | 최악값 |
|---|---|---|---|---|
| 정상 (center) | 705 | 0.036 | p95 = 0.063 | **max = 0.110** |
| 쏠림 (shift) | 41 | 0.510 | p5 = 0.424 | **min = 0.423** |

**분리 마진 = 0.423 − 0.110 = 0.313** (임계값 0.25 는 두 분포 사이 중앙 부근)

임계값을 0.15~0.40 어디에 두어도 결과가 동일합니다. 즉 **파라미터에 과적합되지 않았습니다.**

### 8.4 VIA 대비별 정확도

테스트셋의 VIA 코어 밝기를 PAD 밝기 대비 3단계로 나눠 주입했습니다.

| 코어 밝기 | 이름 | 실측 코어/PAD 비 | 정확도 |
|---|---|---|---|
| 0.12 × PAD | `dark` (진함) | 0.087 ~ 0.168 | **263/263 = 1.0000** |
| 0.25 × PAD | `mid` (중간) | 0.222 ~ 0.322 | **246/246 = 1.0000** |
| 0.45 × PAD | `light` (연함) | 0.422 ~ 0.571 | **237/237 = 1.0000** |

이 3단계를 도입하면서 [`MORPH_OPEN 2×2` 결함](#여기서-morph_open-2x2-를-걸면-안-된다-제거된-단계)을
찾아냈습니다. 수정 전에는 `light` 만 231/237 = 0.9747 로 떨어졌습니다.

### 8.5 실조건 주입 내성 (15장)

정답: OK 244 / OFFSET 14 / MISSING 10 / ABSENT 0

| 주입 | OK | OFFSET | MISSING | ABSENT | 판정 |
|---|---|---|---|---|---|
| 없음 | 244 | 14 | 10 | 0 | ✓ |
| 설계 어긋남 (1,1) | 244 | 14 | 10 | 0 | ✓ |
| 설계 어긋남 (2,2) | 244 | 14 | 10 | 0 | ✓ |
| 설계 어긋남 (3,3) | 241 | 13 | 14 | 0 | ✗ (정합 상한 초과) |
| 설계 어긋남 (−2,3) | 242 | 14 | 12 | 0 | ✗ |
| VIA 구멍 r=2 | 244 | 14 | 10 | 0 | ✓ |
| VIA 구멍 r=3 | 244 | 14 | 10 | 0 | ✓ |
| VIA 구멍 r=4 | 244 | 14 | 10 | 0 | ✓ |
| 실물 PAD 침식 1 | 244 | 14 | 10 | 0 | ✓ |
| 실물 PAD 침식 2 | 244 | 6 | 10 | 8 | ✗ |
| 촬영 잡음 σ=3 | 243 | 14 | 11 | 0 | 거의 ✓ |
| 촬영 잡음 σ=6 | 243 | 14 | 11 | 0 | 거의 ✓ |
| 촬영 잡음 σ=10 | 241 | 13 | 14 | 0 | 열화 |

**모든 열화가 `MISSING` 방향입니다.** 정상 VIA 를 쏠림으로 오판(`OK → OFFSET`)한
사례는 어느 조건에서도 0건이었습니다 — 과검출보다 미검출로 실패하는 안전한 방향입니다.

### 8.6 개선 이력

| 단계 | 이미지 정확도 | PAD 정확도 | 분리 마진 |
|---|---|---|---|
| 초기 | 0.9773 | — | 정상 max 0.497 (겹침) |
| + 가중 무게중심, 절대 하한 | 1.0000 | 0.9513 | 0.194 |
| + Black-hat, 침식 축소 | 1.0000 | 0.9513 | 0.325 |
| + Closing 노치 복원 *(폐기)* | — | 0.9499 | **−0.708** |
| + 설계 PAD 기준 채택 | 1.0000 | 0.9917 | 0.194 |
| + Black-hat 전 바깥 채우기 | 1.0000 | 0.9930 | 0.282 |
| + 정답 라벨 정합성 수정 | 1.0000 | 1.0000 | 0.282 |
| + VIA 설계 대조 옵션 (ON) | 1.0000 | 1.0000 | 0.282 |
| + 국소 정합 · 이중 커버리지 · code 42 분리 | 1.0000 | 1.0000 | 0.282 |
| **PAD 형상 균일화 + VIA 대비 3단계 (재생성)** | 0.9773 ↓ | 0.9922 ↓ | 0.218 ↓ |
| **+ `MORPH_OPEN` 제거** | **1.0000** | **1.0000** | **0.313** |

> 마지막 두 줄이 핵심입니다. 테스트셋을 어렵게 만들자 **기존 100% 가 깨졌고**,
> 그 덕분에 실제 결함을 찾아 고쳤습니다. 정확도가 한 번 떨어진 것은 퇴보가 아니라
> **그동안 측정이 부실했다는 증거**입니다.

---

## 9. 파라미터 튜닝 가이드

새 데이터에 적용할 때 순서대로 확인하세요.

| 증상 | 조정 대상 | 방향 |
|---|---|---|
| PAD 가 배경과 붙어서 뭉침 | `otsu_bias` | **+** (임계값 상승 → PAD 축소) |
| PAD 가 조각남 | `close_ksize` / `otsu_bias` | 5 로 증가 / **−** |
| 작은 노이즈가 PAD 로 잡힘 | `min_pad_area` | 증가 |
| 정상인데 누락으로 오검 | `cad_coverage_thresh` | 감소 (0.55 → 0.40) |
| 정상인데 과잉으로 오검 | `extra_overlap_thresh` / `min_extra_area` | 감소 / 증가 |
| VIA 를 자주 못 찾음 | `via_dark_ratio` / `via_blackhat_min` | 증가(0.62→0.70) / 감소 |
| 테두리를 VIA 로 오검 | `via_pad_erode` / `via_blackhat_ratio` | 증가 / 증가 |
| 정상인데 편심으로 오검 | `via_offset_tol` / `via_offset_min_px` | 증가 / 증가 |
| 편심을 놓침 | `via_offset_tol` | 감소 |
| VIA 설계 대조를 쓰고 싶다 | `via_design_check` | `True` + `via_design` 인자 전달 |
| 설계 VIA 점이 너무 작아 무시됨 | `via_design_min_area` | 감소 (기본 2px) |
| VIA 과잉이 과하게 잡힘 | `via_target_min_circularity` | 증가 (검사 대상 PAD 축소) |

**튜닝 절차 권장안**

1. `step1` 만 돌려 `pre.binary` 를 눈으로 확인 → 이진화 확정
2. `step2` 의 `seg.pad_mask` 확인 → PAD 개수/형태 확정
3. `eval_padlevel.py` 로 **분리 마진**을 본다. 마진이 양수이고 클수록 임계값 선택이 자유롭다
4. 마진이 음수면 임계값을 조정할 게 아니라 **특징 자체를 개선**해야 한다 (§4.2, §4.3 참조)

---

## 10. 알려진 전제 및 한계

| 항목 | 내용 |
|---|---|
| 밝기 극성 | **PAD 가 배경보다 밝다**고 가정. 반대면 `step1` 에서 `THRESH_BINARY_INV` 필요 |
| 정합(alignment) | PAD 단위 국소 정합이 들어 있어 **±2px 어긋남까지는 완전히 복구**. 3px 이상은 `ALIGN_MAX_RATIO` 상한(≈3.7px)에 막혀 실패하므로 상위 단계의 전역 정합 필요 (§8.5) |
| VIA 대비 | 코어 밝기가 PAD 밝기의 **0.45배 이하**여야 함(`via_dark_ratio = 0.62`). 그보다 연하면 `dark` 조건을 통과하지 못함 |
| 후보 잡티 제거 | 형태학적 opening 을 쓰면 안 됨. 연한 VIA 의 4~5px 후보를 통째로 지움 (§4.3). 면적 하한만 사용 |
| 이미지 크기 | 설계도와 검사 이미지 크기가 같아야 함 (다르면 `ValueError`) |
| VIA 개수 | PAD 당 VIA 는 **1개**로 가정. 최대 면적 덩어리 하나만 채택 |
| 검사 대상 | 원형/블롭형 PAD 만. 직사각 PAD·트레이스는 VIA 검사에서 제외 |
| 회전 | PAD 회전은 등가반지름 기반이라 무관. 단 심한 원근 왜곡은 미보정 |
| 노치 대응 | 설계도가 **없으면** 가장자리 쏠림 VIA 검출률이 떨어짐 (§4.2). 설계도 사용 권장 |
| VIA 설계 대조 | `pres.matches` 로 설계↔실측 PAD 를 이은 뒤 판정하므로 **PAD 매칭이 선행 조건**. PAD 자체가 누락되면 그 PAD 의 VIA 는 판정 대상에서 빠짐 |
| VIA 설계 마스크 | 점 하나가 **PAD 하나**에 대응한다고 가정. 한 PAD 에 설계 VIA 가 2개면 마지막 하나만 사용 |

---

## 11. 요구사항 대응표

| # | 요구사항 | 구현 |
|---|---|---|
| 1 | Testset 준비 (정중앙/쏠림/없음, 이미지당 3종 혼합 또는 정중앙만) | `make_testset.py` — §7.4 |
| 1a | **PAD 패턴 형상 균일화 (설계도 포함)** | `PAD_UNIFORM` + 재분할 — §7.2 |
| 1b | **VIA 대비 3단계 (진함·중간·연함)** | `VIA_DARKNESS_LEVELS` — §7.3 |
| 2 | 설계도 파일 (동일 크기, PAD 이진화, 실제보다 살짝 작음) | `build_design_mask` (`erode_px=2`) — §2.3 |
| 3 | PAD 누락/과잉 검출 (**code `"24"`**) | `step3_check_pad_presence` — §3 |
| 4 | VIA 정중앙 검사 (쏠림·없음 → **code `"99"`**) | `step4_check_via` — §4 |
| 5 | 결과 이미지 + 코드 반환, **원샷 / 단계별** 2가지, **Python 3.9** | `inspect()` / `step1`~`step5` — §6 |
| 6 | 로직·알고리즘 설명 문서 | 본 문서 |
| 7 | **VIA 설계도 대조 (없는 것·있는 것 찾기)를 옵션으로 분리** | `InspectConfig.via_design_check` + `via_design` 인자 — §4.1.1 |
| 8 | **결과 이미지는 원본 스케일에 그림만** | 텍스트 바 제거, `result_image.shape == 입력.shape` — §5.2 |
| 9 | **code 를 `str` 로 반환** | `CODE_OK="1"` / `"24"` / `"99"` / `"-1"` — §0.1 |
| + | 다른 곳에 통째로 복사해 사용 | `pad_via_inspector.py` 단일 파일, 외부 의존 없음 — §0.2 |
