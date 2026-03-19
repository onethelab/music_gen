# Suno AI 음악 작곡 자동화 프로세스

AI(Claude)에게 요청하면 Suno에 복붙할 수 있는 결과물을 생성해주는 워크플로우.

---

## 1. 프로세스 개요

```
[사용자] 곡 컨셉 설명 (자연어)
    |
[Claude] 분석 & 생성
    |
    +-- Style 프롬프트 생성
    +-- Lyrics (가사 + 구조 태그) 생성
    +-- Title 생성
    |
[사용자] Suno Custom Mode에 복붙
    |
[Suno] 음악 생성
```

---

## 2. Suno 입력 필드 (Custom Mode)

Suno Custom Mode에는 3개의 입력 필드가 있다.

| 필드 | 설명 | 예시 |
|------|------|------|
| **Title** | 곡 제목 | `Midnight Echo` |
| **Style of Music** | 장르, 분위기, 악기, 템포, 보컬 스타일 등 | `Indie folk, 92 BPM, melancholic, fingerstyle acoustic guitar, whispered vocals` |
| **Lyrics** | 구조 태그 + 가사 텍스트 | `[Verse 1]` 아래에 가사 작성 |

---

## 3. Style 프롬프트 작성법

### 3.1 공식 (7요소)

```
[장르] + [템포] + [분위기] + [악기] + [보컬 스타일] + [시대감] + [프로덕션]
```

### 3.2 글자수 제한

| 필드 | 제한 |
|------|------|
| **Title** | 최대 80자 |
| **Style of Music** | **최대 200자** (초과 시 잘림, 핵심 태그를 앞에 배치) |
| **Lyrics** | 최대 약 3000자 (곡 길이에 따라 유동적) |

### 3.3 주요 규칙

- 명령형이 아닌 서술형으로 작성 ("Create a..." X / "Upbeat pop with..." O)
- 4~8개 스타일 태그가 적정
- **Style은 200자 이내로 작성** — 초과 시 뒷부분이 무시되므로 장르·템포를 앞에, 제외 요소를 마지막에 배치
- 제외할 요소는 끝에 부정 프롬프트 추가: `no autotune, no reverb`
- BPM, 키(key)를 지정하면 정밀도 향상

### 3.3 장르 카테고리 참조

| 카테고리 | 대표 장르 |
|----------|-----------|
| Rock/Metal | Rock, Alternative Rock, Grunge, Progressive Rock, Shoegaze, Post-Rock |
| Electronic | EDM, House, Techno, Trance, Dubstep, Synthwave, Ambient, Chillwave |
| Hip Hop | Hip Hop, Trap, Boom Bap, UK Drill, Jazz Rap, Mumble Rap |
| Jazz/Blues | Jazz, Smooth Jazz, Bebop, Blues, Swing, Big Band |
| Folk/World | Folk, Bluegrass, Celtic, Bossa Nova, Afrobeat, Reggae, Flamenco |
| Pop | Pop, Indie Pop, Dream Pop, Synth-pop, K-pop, Disco, R&B |
| Classical | Classical, Orchestral, Symphonic, Opera, Neoclassical, Chamber Music |
| Cinematic | Cinematic, Movie Soundtrack, Video Game Music, Score |

### 3.4 분위기(Mood) 수식어

`Aggressive` `Anthemic` `Atmospheric` `Calming` `Dark` `Emotional` `Epic` `Ethereal` `Festive` `Groovy` `Haunting` `Intimate` `Melancholy` `Nostalgic` `Uplifting` `Vintage`

### 3.5 프로덕션 수식어

`Lo-fi` `Polished` `Gritty` `Crisp` `Warm` `Raw` `Vintage` `Modern` `Atmospheric` `Minimal` `Lush` `Acoustic`

---

## 4. Lyrics 구조 태그

### 4.1 곡 구조 태그

| 태그 | 용도 |
|------|------|
| `[Intro]` | 오프닝 (불안정, `[Instrumental Intro]` 권장) |
| `[Verse]` / `[Verse 1]` | 메인 스토리텔링 |
| `[Pre-Chorus]` | 코러스 전 빌드업 |
| `[Chorus]` | 메인 훅, 반복 구간 |
| `[Post-Chorus]` | 코러스 후 확장 |
| `[Bridge]` | 대비되는 전환 구간 |
| `[Hook]` | 기억에 남는 핵심 멜로디/가사 |
| `[Outro]` | 마무리 |
| `[End]` | 급격한 종료 |
| `[Instrumental]` / `[Interlude]` | 악기 연주 구간 |
| `[Break]` / `[Breakdown]` | 리듬 변화 |
| `[Solo]` / `[Guitar Solo]` | 악기 솔로 |
| `[Drop]` | EDM 스타일 드롭 |
| `[Build]` | 드롭 전 빌드업 |
| `[Fade Out]` | 페이드아웃 |

### 4.2 보컬 전달 태그

| 분류 | 태그 |
|------|------|
| 강도 | `[Whispered]` `[Soft]` `[Gentle]` `[Spoken]` `[Powerful]` `[Belted]` `[Shouted]` `[Screamed]` |
| 스타일 | `[Falsetto]` `[Breathy]` `[Raspy]` `[Smooth]` `[Soulful]` `[Operatic]` `[Airy]` |
| 기법 | `[Harmonies]` `[Ad-libs]` `[Vibrato]` `[Choir]` `[Call and Response]` `[Chant]` |
| 랩 | `[Rapped]` `[Fast Rap]` `[Melodic Rap]` `[Trap Flow]` `[Double Time]` |

태그 조합 가능: `[Chorus] [Belted]`, `[Bridge] [Soft]`

---

## 5. Claude에게 요청하는 프롬프트 템플릿

아래 프롬프트를 Claude에게 보내면 Suno에 바로 복붙할 수 있는 결과물을 생성한다.

### 5.1 기본 요청 템플릿

```
다음 컨셉으로 Suno AI용 음악을 만들어줘.
Suno Custom Mode의 Title, Style, Lyrics 3개 필드를 각각 생성해줘.

[컨셉]
- 주제: (예: 새벽 혼자 걷는 외로움)
- 장르: (예: Indie folk)
- 분위기: (예: 잔잔하고 쓸쓸한)
- 언어: (예: 한국어 / 영어)
- 기타: (예: 여성 보컬, 90 BPM, 어쿠스틱 기타 중심)
```

### 5.2 상세 요청 템플릿

```
Suno AI용 곡을 만들어줘. 아래 형식으로 출력해줘.

[컨셉]
- 주제/스토리:
- 장르:
- 분위기/감정:
- 템포(BPM):
- 악기:
- 보컬 스타일:
- 언어:
- 곡 길이 느낌: (짧은/보통/긴)
- 참고곡: (있으면)

[출력 형식]
## Title
(곡 제목)

## Style of Music
(Suno Style 필드에 복붙할 내용)

## Lyrics
(Suno Lyrics 필드에 복붙할 내용 - 구조 태그 포함)
```

### 5.3 간단 요청 (한 줄)

```
"비 오는 날 카페에서 듣기 좋은 재즈 힙합 곡 만들어줘. Suno용으로."
```

이렇게 간단히 요청해도 Claude가 알아서 Title/Style/Lyrics를 생성한다.

---

## 6. 출력 예시

아래는 Claude가 생성한 결과물 예시이다. 각 섹션을 Suno에 그대로 복붙하면 된다.

### Title
```
Midnight Echo
```

### Style of Music
```
Indie folk, 92 BPM, melancholic, fingerstyle acoustic guitar, whispered vocals, lo-fi warmth, intimate, 2010s indie style
```

### Lyrics
```
[Instrumental Intro]

[Verse 1] [Soft]
Walking down the empty street at dawn
Shadows fading as the light moves on
Every step I take reminds me of you
The silence speaks in shades of blue

[Pre-Chorus]
And I can feel the change in the air tonight

[Chorus] [Powerful]
We were the fire burning bright
We were the stars that owned the night
Now all that's left is midnight echo
Fading out where we let go

[Verse 2] [Breathy]
Memories like rivers running deep
Promises I never meant to keep
Your voice still lingers in the rain
A bittersweet, familiar pain

[Bridge] [Whispered]
Maybe someday we will understand
Why we let go of each other's hand

[Final Chorus] [Belted]
We were the fire burning bright
We were the stars that owned the night
Now all that's left is midnight echo
Fading out where we let go

[Outro] [Fade Out]
```

---

## 7. 팁 & 주의사항

1. **항상 Custom Mode 사용** -- Simple Mode는 제어력이 떨어진다
2. **`[Intro]` 태그는 불안정** -- `[Instrumental Intro]`를 쓰는 것이 안전하다
3. **가사는 단순한 문장 구조**가 보컬 렌더링에 유리하다
4. **Style 필드에는 사운드 관련만, Lyrics 필드에는 가사만** 넣는다 (혼용 금지)
5. **결과가 마음에 안 들면** Suno의 Remix/Extend 기능으로 변형한다
6. **한 번에 하나의 변수만 변경**하며 반복하면 원하는 결과에 빠르게 도달한다
7. **악기 연주곡**은 Lyrics에 `[Instrumental]`만 넣으면 된다
8. **가사에 아라비아 숫자 금지** -- `1`, `2`, `7시` 등은 "일/하나", "이/둘" 등 여러 발음으로 읽힐 수 있으므로 한글로 표기한다 (예: `한 픽셀`, `일곱 시`, `석 달`). Title은 예외
