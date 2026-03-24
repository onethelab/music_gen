# 영상 이펙트 정리

## Bloom (빛 번짐) 효과

밝은 부분이 부드럽게 번지면서 빛나는 효과.

### 원리
```
원본 이미지
  → 밝은 부분만 추출 (threshold)
  → 가우시안 블러 (빛 번짐)
  → Screen 블렌드로 원본에 합성
```

### ffmpeg filter_complex
```
[0:v]split[a][b];
[b]colorlevels=rimin=0.6:gimin=0.6:bimin=0.6,gblur=sigma=25[bloom];
[a][bloom]blend=all_mode=screen:all_opacity=0.5[out]
```

### 파라미터

| 파라미터 | 역할 | 조절 |
|---------|------|------|
| `rimin/gimin/bimin=0.6` | 밝기 임계값 (0.6 이하 어두운 부분 제거) | 낮추면 더 많은 부분이 빛남 |
| `gblur=sigma=25` | 블러 강도 (빛 번짐 크기) | 높이면 더 넓게 번짐 |
| `all_opacity=0.5` | 블룸 강도 | 높이면 더 강하게 빛남 |

### 적용 대상
- 배경 이미지의 레이저, 빛줄기, 네온 등 밝은 부분
- 어두운 부분에는 영향 없음

### 상태
- 미적용 (테스트 필요)
