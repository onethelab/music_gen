# Suno AI 프롬프트 연구

> Style 키워드가 실제 생성 결과에 미치는 영향을 실험·기록한다.
> 의도와 결과가 다를 때 원인을 분석하고, 향후 프롬프트 작성 시 참조한다.

---

## 실험 기록

### 실험 #1: 극고음 오페라 소프라노 시도 (2026-03-25)

**곡**: 17_Final_Aria (최후의 아리아)
**목적**: Suno가 생성할 수 있는 가장 특수한 한국어 여성 보컬을 끌어내기

#### 입력 Style

```
Darkwave Opera, Dark Electronic, Dramatic coloratura soprano, Extreme high register, Piercing vibrato, 85 BPM, C minor, Sub bass drone, Distorted arpeggio, Orchestral stabs, Industrial percussion, Cavernous reverb
```

#### 의도

| 키워드 | 의도한 효과 |
|--------|------------|
| Dramatic coloratura soprano | 클래식 오페라 콜로라투라 소프라노 발성 |
| Extreme high register | 극고음역으로 일반 팝 보컬과 차별화 |
| Piercing vibrato | 관통하는 비브라토로 성악적 질감 유도 |
| Darkwave Opera | 다크 일렉트로닉 + 오페라 융합 장르 |
| Orchestral stabs | 클래식 관현악 짧은 타격으로 오페라 분위기 |

#### 실제 결과

| 항목 | 의도 | 실제 |
|------|------|------|
| 보컬 성별 | 여성 소프라노 | **남성 그로울링 래퍼** 등장 |
| 보컬 스타일 | 오페라 콜로라투라 | **록/메탈 보컬** (라커 발성) |
| 악기 구성 | 오케스트라 + 일렉트로닉 | **헤비 록/메탈** 기타 중심 |
| 전체 분위기 | 장엄한 네오클래시컬 | **어그레시브 다크 메탈** |

#### 원인 분석

**1. "Dark" 계열 키워드의 장르 끌림**

Suno는 `Dark` 접두어를 **메탈/록/하드코어** 방향으로 해석하는 강한 경향이 있다.

| 키워드 | 의도한 해석 | Suno의 실제 해석 |
|--------|-----------|-----------------|
| Darkwave | 다크웨이브 (포스트펑크 전자음악) | 다크 + 웨이브 → 어두운 록/메탈 |
| Dark Electronic | 어두운 전자음악 | 인더스트리얼 메탈 |

> **교훈**: `Dark`를 장르명에 붙이면 Suno가 메탈 쪽으로 끌려간다. 어두운 분위기를 원하면 `Dark` 대신 `Haunting`, `Somber`, `Melancholic` 등 분위기 키워드로 우회해야 한다.

**2. "Extreme", "Piercing"의 어그레시브 해석**

| 키워드 | 의도한 해석 | Suno의 실제 해석 |
|--------|-----------|-----------------|
| Extreme high register | 소프라노 극고음역 | **스크리밍/그로울링** |
| Piercing vibrato | 관통하는 오페라 비브라토 | **날카로운 록 보컬** |

> **교훈**: `Extreme`, `Piercing`은 Suno에서 **공격성 지표**로 작동한다. 오페라 고음을 원하면 `Soaring soprano`, `High operatic vocal`, `Lyrical high notes` 같은 클래식 어휘를 써야 한다.

**3. "Industrial percussion", "Distorted arpeggio"의 록/메탈 유인**

| 키워드 | 의도한 해석 | Suno의 실제 해석 |
|--------|-----------|-----------------|
| Industrial percussion | 금속성 타격 (팀파니 대용) | **메탈 드럼** |
| Distorted arpeggio | 왜곡된 신스 아르페지오 | **디스토션 기타 아르페지오** |

> **교훈**: `Industrial`과 `Distorted`는 Suno에서 **록/메탈 장르 앵커**다. 전자음 왜곡을 원하면 `Filtered synth arpeggio`, `Processed electronic` 등으로 명시해야 한다.

**4. 보컬 성별 미지정**

Style에 `Female`을 명시하지 않았다. Suno는 `Dark` + `Extreme` + `Industrial` 조합에서 **남성 보컬을 디폴트**로 선택한다.

> **교훈**: 여성 보컬을 원하면 반드시 `Female`을 명시해야 한다. 단, `Korean female vocal`은 트로트/발라드를 유인하므로, `Female operatic soprano` 같이 장르+성별을 결합한 형태를 쓴다.

#### 수정 제안 Style

```
Neoclassical Electronic, Opera, Female operatic soprano, Coloratura, High register vibrato, 85 BPM, C minor, String ensemble, Orchestral stabs, Grand piano, Sub bass drone, Cavernous concert hall reverb, Cinematic layered production
```

| 변경 | 이전 | 이후 | 이유 |
|------|------|------|------|
| 장르 앵커 | Darkwave Opera | **Neoclassical Electronic, Opera** | Dark 제거, 클래식 방향 명시 |
| 보컬 | Dramatic coloratura soprano | **Female operatic soprano, Coloratura** | Female 명시, Dramatic 제거 (어그레시브 유인) |
| 고음 | Extreme high register | **High register vibrato** | Extreme 제거 (스크리밍 유인) |
| 비브라토 | Piercing vibrato | (Coloratura에 포함) | Piercing 제거 (날카로운 록 유인) |
| 타악 | Industrial percussion | (제거) | Industrial → 메탈 드럼 유인 |
| 아르페지오 | Distorted arpeggio | (제거) | Distorted → 기타 디스토션 유인 |
| 악기 추가 | — | **String ensemble, Grand piano** | 클래식 악기로 장르 고정 |
| 프로덕션 | Cavernous reverb | **Cavernous concert hall reverb** | 콘서트홀 명시로 클래식 공간감 유도 |

---

### 실험 #2: 수정 Style — Dark 제거 + Neoclassical 앵커 (2026-03-25)

**곡**: 18_Glass_Cathedral (유리 성당)
**목적**: 실험 #1의 분석을 반영하여 오페라 소프라노를 재시도

#### 입력 Style

```
Neoclassical Electronic, Opera, Female operatic soprano, Coloratura, High register vibrato, 85 BPM, C minor, String ensemble, Orchestral stabs, Grand piano, Sub bass drone, Concert hall reverb
```

#### 실험 #1 대비 변경점

| 변경 | #1 (Final Aria) | #2 (Glass Cathedral) | 변경 이유 |
|------|-----------------|---------------------|-----------|
| 장르 앵커 | `Darkwave Opera, Dark Electronic` | `Neoclassical Electronic, Opera` | Dark 계열 → 메탈 유인 차단 |
| 보컬 | `Dramatic coloratura soprano` | `Female operatic soprano, Coloratura` | Female 명시 + Dramatic 제거 |
| 고음 지시 | `Extreme high register` | `High register vibrato` | Extreme → 스크리밍 유인 차단 |
| 비브라토 | `Piercing vibrato` | (Coloratura에 포함) | Piercing → 록 보컬 유인 차단 |
| 타악 | `Industrial percussion` | (제거) | Industrial → 메탈 드럼 유인 |
| 아르페지오 | `Distorted arpeggio` | (제거) | Distorted → 기타 디스토션 유인 |
| 악기 | — | `String ensemble, Grand piano` | 클래식 악기로 장르 고정 |
| 리버브 | `Cavernous reverb` | `Concert hall reverb` | 콘서트홀 공간감 명시 |

#### 실제 결과

| 항목 | 의도 | 실제 |
|------|------|------|
| 보컬 성별 | 여성 소프라노 | ✅ 여성 보컬 (Female 명시 효과) |
| 보컬 스타일 | 맑은 오페라 콜로라투라 | ❌ **허스키한 여성 보컬** — 소프라노 아님 |
| 악기 구성 | 스트링 + 피아노 + 일렉트로닉 | (확인 필요) |
| 전체 분위기 | 장엄한 네오클래시컬 | (확인 필요) |

#### 분석

**개선된 점**: `Female` 명시로 남성 보컬 문제는 해결됨. `Dark/Industrial/Distorted` 제거로 메탈/록 방향 이탈도 해소.

**미해결**: 보컬 톤이 허스키(husky)하게 나옴. 맑은(clear/crystalline) 소프라노가 아님.

**원인 추정**:

1. **`Coloratura`가 Suno에서 작동하지 않을 가능성**
   - Suno의 학습 데이터에 클래식 오페라 보컬이 충분하지 않을 수 있음
   - `Coloratura`를 장르/스타일 키워드로 인식하지 못하고 무시했을 가능성

2. **`Sub bass drone`이 보컬 톤을 어둡게 끌어내림**
   - 저음 중심 악기 배치가 보컬도 낮고 어두운 쪽으로 유도
   - 밝은 보컬을 원하면 악기도 밝은 쪽(높은 음역)으로 배치해야 할 수 있음

3. **`C minor`(단조)가 허스키 톤을 유도**
   - 단조 키가 전반적으로 어두운 분위기 → 보컬도 어두운 톤 선택
   - 맑은 소프라노를 원하면 장조(Major)나 밝은 키가 필요할 수 있음

4. **보컬 톤 직접 지시 부재**
   - `Female operatic soprano`는 음역을 지시하지만 **톤 질감**은 지시하지 않음
   - 맑은 톤을 원하면 `Clear`, `Crystalline`, `Bright`, `Pure` 같은 톤 형용사가 필요

#### 다음 실험 제안 (실험 #3)

```
Neoclassical, Opera, Female soprano, Clear bright vocal, Pure high register, Crystalline vibrato, 90 BPM, C major, String ensemble, Grand piano, Harp, Glockenspiel, Concert hall reverb, Polished production
```

변경 요약:
| 변경 | #2 | #3 제안 | 이유 |
|------|-----|---------|------|
| 보컬 톤 | `Female operatic soprano` | `Female soprano, Clear bright vocal, Pure high register` | 톤 질감 직접 지시 (Clear/Bright/Pure) |
| 비브라토 | `Coloratura, High register vibrato` | `Crystalline vibrato` | Coloratura 미작동 추정 → 톤 형용사로 교체 |
| 키 | `C minor` | `C major` | 단조 → 장조로 밝은 분위기 유도 |
| 저음 | `Sub bass drone` | (제거) | 저음이 보컬 톤을 어둡게 끌어내림 |
| 악기 추가 | — | `Harp, Glockenspiel` | 밝고 높은 음역 악기로 소프라노 톤 유도 |

---

## 키워드 해석 사전 (누적)

> Suno가 키워드를 어떻게 해석하는지 실험으로 확인된 결과를 기록한다.

### 위험 키워드 (의도와 다르게 해석될 수 있음)

| 키워드 | 의도 가능 범위 | Suno 실제 해석 | 대안 |
|--------|--------------|---------------|------|
| `Dark` (장르 접두) | 어두운 분위기 | 메탈/록/하드코어 방향 유인 | `Somber`, `Haunting`, `Melancholic` |
| `Extreme` | 극단적 (음역/다이나믹) | 공격성 지표 → 스크리밍/그로울 | `Soaring`, `Expansive`, `Wide-range` |
| `Piercing` | 관통하는 (비브라토/톤) | 날카로운 록/메탈 보컬 | `Clear`, `Crystalline`, `Ringing` |
| `Industrial` | 기계적/금속적 질감 | 인더스트리얼 메탈 장르 | `Metallic textures`, `Mechanical rhythm` |
| `Distorted` | 전자음 왜곡 | 기타 디스토션 | `Filtered`, `Processed`, `Overdriven synth` |
| `Aggressive` | 강렬한 에너지 | 메탈/하드코어 | `Intense`, `Driving`, `Powerful` |
| `Korean` (보컬) | 한국어 보컬 | 트로트/K-발라드 유인 | 한국어 가사만 넣고 언어 미명시 |
| `Ethereal` | 공기감 있는 | 뉴에이지/앰비언트 이탈 | `Airy`, `Floating` |
| `Cinematic` | 영화적 웅장함 | 뉴에이지/영화 스코어 이탈 | `Epic`, `Grand`, `Majestic` |
| `Whisper to belting` | 속삭임→파워풀 전환 | 발라드/팝 보컬 유인 | 구간별 메타태그로 분리 지시 |

### 안전 키워드 (의도대로 작동 확인됨)

| 키워드 | 효과 | 확인된 곡 |
|--------|------|-----------|
| `Dark Synthwave, Darkwave` | 다크신스/다크웨이브 장르 앵커 | 03~07, 11~13 |
| `Dark female vocal` | 낮고 단단한 여성 보컬 | 08~16 (인더스트리얼 다크웨이브 시리즈) |
| `Gated reverb snare` | 80년대 스네어 사운드 | 다수 곡에서 일관 작동 |
| `Sawtooth arpeggio` | 톱니파 신스 아르페지오 | 인더스트리얼 시리즈 |
| `Sub bass` | 묵직한 저음 베이스 | 대부분의 다크 계열 곡 |
| `Sidechain pumping` | 킥에 맞춘 펌핑 효과 | 다수 곡 |
| `Filter sweep` | 필터 스윕 전환 효과 | 인트로/아웃트로에서 작동 |
| `E Phrygian` | 프리지안 스케일 (이질적 긴장) | 16_Rusted_Warning_Light |
| `128 BPM` | 템포 지정 | 정확하게 반영됨 |

### 보컬 성별/스타일 키워드

| 키워드 | 결과 | 비고 |
|--------|------|------|
| `Female vocal` | 여성 보컬 배정 | 장르에 따라 스타일 변동 |
| `Dark female vocal` | 낮고 어두운 여성 보컬 | 인더스트리얼 계열에서 안정적 |
| `Male vocal` | 남성 보컬 배정 | — |
| `Korean female vocal` | ⚠ 트로트/발라드 유인 | 사용 금지 |
| 성별 미지정 + Dark 계열 | 남성 보컬 배정 확률 높음 | 반드시 성별 명시 필요 |
| `Operatic soprano` | 미확인 (실험 필요) | Neoclassical 앵커와 함께 테스트 필요 |
| `Coloratura` | 미확인 (실험 필요) | Opera 장르 앵커와 함께 테스트 필요 |

---

## 장르 앵커 조합 규칙 (누적)

> 특정 장르를 안정적으로 얻기 위해 필요한 키워드 조합.

| 목표 장르 | 필수 앵커 키워드 | 보조 키워드 | 금지 키워드 |
|-----------|----------------|------------|------------|
| Gothic Synthwave | `Dark Synthwave, Darkwave, Coldwave, Electronic` | `Analog synth arpeggio, Cathedral reverb` | `Korean`, `Instrumental` (단독) |
| Industrial Darkwave | `Dark Synthwave, Darkwave, Aggressive, Menacing` | `Industrial percussion, Distorted bass` | `Korean`, `Ethereal`, `Breathy`, `Cathedral` |
| Dream Pop | `Dream Pop, Shoegaze, Ambient` | `Reverse guitar, Glockenspiel` | `Korean`, 감정 형용사 |
| City Pop | `City Pop, Japanese Funk` | `Slap bass, Fender Rhodes, Saxophone` | — |
| Neoclassical Electronic | `Neoclassical, Electronic, Opera` (미확인) | `String ensemble, Grand piano` (미확인) | `Dark`, `Industrial`, `Distorted` (추정) |

---

## 실험 대기열

> 향후 테스트할 Style 조합 목록.

| # | 목표 | 테스트할 Style | 상태 |
|---|------|---------------|------|
| 1 | 오페라 소프라노 여성 보컬 | `Neoclassical Electronic, Opera, Female operatic soprano, Coloratura, High register vibrato, 85 BPM, C minor, String ensemble, Orchestral stabs, Grand piano, Sub bass drone, Cavernous concert hall reverb, Cinematic layered production` | 대기 |
| 2 | 판소리 + 전자음악 퓨전 | `Fusion Gugak, Pansori female vocal, Korean pentatonic, Gayageum, Electronic beats` | 대기 |
| 3 | 극저음 여성 보컬 (컨트랄토) | `Dark Electronic, Deep female contralto, Low register, Spoken word, Minimal beat` | 대기 |

---

## 기록 규칙

1. 새 실험마다 `### 실험 #N` 형식으로 추가
2. 반드시 **입력 Style**, **의도**, **실제 결과**, **원인 분석**, **수정 제안**을 포함
3. 확인된 키워드는 **키워드 해석 사전**에 누적
4. 장르 앵커 조합이 확인되면 **장르 앵커 조합 규칙**에 추가
