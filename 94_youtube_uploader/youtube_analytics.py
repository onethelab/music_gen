"""
유튜브 채널 상세 분석
- 채널 전체 통계
- 동영상별 시청지속시간/평균시청시간 분석
- 장르(음악유형)별 성과 비교
- 트래픽 소스 분석
- 전략 제안

사용법:
    cd 94_youtube_uploader
    python youtube_analytics.py
"""

import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta

from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

UPLOADER_DIR = os.path.dirname(__file__)
CLIENT_SECRET_FILE = os.path.join(UPLOADER_DIR, "client_secret.json")
TOKEN_FILE = os.path.join(UPLOADER_DIR, "token.json")

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
    "https://www.googleapis.com/auth/yt-analytics.readonly",
]


def safe_print(text):
    print(text.encode('cp949', errors='ignore').decode('cp949'))


def get_credentials():
    creds = None
    if os.path.exists(TOKEN_FILE):
        creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not os.path.exists(CLIENT_SECRET_FILE):
                safe_print("client_secret.json 파일이 없습니다.")
                sys.exit(1)
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRET_FILE, SCOPES)
            creds = flow.run_local_server(port=0)
        with open(TOKEN_FILE, 'w') as token:
            token.write(creds.to_json())
    return creds


def parse_duration_iso(duration_str):
    """ISO 8601 duration (PT#H#M#S) → 초 변환"""
    match = re.match(r'PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?', duration_str)
    if not match:
        return 0
    h = int(match.group(1) or 0)
    m = int(match.group(2) or 0)
    s = int(match.group(3) or 0)
    return h * 3600 + m * 60 + s


def extract_genre(title):
    """제목에서 장르(음악유형) 추출: '... | Genre 한글 V1' 패턴"""
    m = re.search(r'\|\s*([A-Za-z][A-Za-z &\-]+)', title)
    if m:
        genre = m.group(1).strip()
        # V1, V2 등 제거
        genre = re.sub(r'\s*V\d+$', '', genre).strip()
        return genre
    return "기타"


def get_all_video_ids(youtube):
    """채널의 모든 동영상 ID 가져오기"""
    ch_res = youtube.channels().list(part='contentDetails', mine=True).execute()
    uploads_id = ch_res['items'][0]['contentDetails']['relatedPlaylists']['uploads']

    video_ids = []
    next_token = None
    while True:
        pl_res = youtube.playlistItems().list(
            part='contentDetails', playlistId=uploads_id,
            maxResults=50, pageToken=next_token
        ).execute()
        video_ids.extend(item['contentDetails']['videoId'] for item in pl_res['items'])
        next_token = pl_res.get('nextPageToken')
        if not next_token:
            break
    return video_ids


def get_video_details(youtube, video_ids):
    """동영상 상세 정보 (snippet, statistics, contentDetails)"""
    videos = []
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        res = youtube.videos().list(
            part='snippet,statistics,contentDetails',
            id=','.join(batch)
        ).execute()
        videos.extend(res['items'])
    return videos


def get_per_video_analytics(youtube_analytics, channel_id, video_ids):
    """동영상별 Analytics 데이터 (시청시간, 평균시청시간, 평균시청비율)"""
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = '2020-01-01'

    # 50개씩 배치로 쿼리 (filters에 videoId 지정)
    analytics_data = {}
    for i in range(0, len(video_ids), 50):
        batch = video_ids[i:i+50]
        filters = 'video==' + ','.join(batch)
        try:
            res = youtube_analytics.reports().query(
                ids=f'channel=={channel_id}',
                startDate=start_date,
                endDate=end_date,
                metrics='views,estimatedMinutesWatched,averageViewDuration,averageViewPercentage',
                dimensions='video',
                filters=filters,
                sort='-views'
            ).execute()
            for row in res.get('rows', []):
                vid_id = row[0]
                analytics_data[vid_id] = {
                    'views': int(row[1]),
                    'watch_minutes': round(row[2], 1),
                    'avg_view_duration': round(row[3], 1),  # 초
                    'avg_view_percentage': round(row[4], 1),  # %
                }
        except Exception as e:
            safe_print(f"  (Analytics 조회 오류: {e})")
    return analytics_data


def get_traffic_sources(youtube_analytics, channel_id):
    """트래픽 소스 분석"""
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=90)).strftime('%Y-%m-%d')

    source_names = {
        'EXT_URL': '외부 링크',
        'RELATED_VIDEO': '추천 동영상',
        'YT_SEARCH': 'YouTube 검색',
        'YT_OTHER_PAGE': 'YouTube 기타',
        'NOTIFICATION': '알림',
        'PLAYLIST': '재생목록',
        'YT_CHANNEL': '채널 페이지',
        'SUBSCRIBER': '구독 피드',
        'NO_LINK_EMBEDDED': '임베드',
        'SHORTS': 'Shorts',
        'END_SCREEN': '최종 화면',
        'ANNOTATION': '카드/주석',
    }

    try:
        res = youtube_analytics.reports().query(
            ids=f'channel=={channel_id}',
            startDate=start_date,
            endDate=end_date,
            metrics='views,estimatedMinutesWatched',
            dimensions='insightTrafficSourceType',
            sort='-views'
        ).execute()
        return [(source_names.get(row[0], row[0]), int(row[1]), round(row[2], 1))
                for row in res.get('rows', [])]
    except Exception as e:
        safe_print(f"  (트래픽 소스 조회 오류: {e})")
        return []


def get_daily_views(youtube_analytics, channel_id, days=28):
    """일별 조회수"""
    end_date = datetime.now().strftime('%Y-%m-%d')
    start_date = (datetime.now() - timedelta(days=days)).strftime('%Y-%m-%d')

    res = youtube_analytics.reports().query(
        ids=f'channel=={channel_id}',
        startDate=start_date,
        endDate=end_date,
        metrics='views,estimatedMinutesWatched,subscribersGained',
        dimensions='day',
        sort='day'
    ).execute()
    return res.get('rows', [])


def main():
    safe_print("YouTube 상세 분석 중...")
    creds = get_credentials()

    youtube = build("youtube", "v3", credentials=creds)
    youtube_analytics = build("youtubeAnalytics", "v2", credentials=creds)

    # ── 1. 채널 통계 ──
    ch_res = youtube.channels().list(part='snippet,statistics', mine=True).execute()
    ch = ch_res['items'][0]
    stats = ch['statistics']
    channel_id = ch['id']

    safe_print(f"\n{'='*60}")
    safe_print(f"  채널: {ch['snippet']['title']}")
    safe_print(f"  구독자: {int(stats['subscriberCount']):,}명 | "
               f"총 조회수: {int(stats['viewCount']):,}회 | "
               f"동영상: {int(stats['videoCount']):,}개")
    safe_print(f"{'='*60}")

    # ── 2. 전체 동영상 데이터 수집 ──
    safe_print("\n동영상 데이터 수집 중...")
    video_ids = get_all_video_ids(youtube)
    videos = get_video_details(youtube, video_ids)
    analytics = get_per_video_analytics(youtube_analytics, channel_id, video_ids)

    # ── 3. 동영상별 상세 분석 (조회수 순 정렬) ──
    video_rows = []
    for v in videos:
        vid_id = v['id']
        title = v['snippet']['title']
        published = v['snippet']['publishedAt'][:10]
        duration_sec = parse_duration_iso(v['contentDetails']['duration'])
        duration_str = f"{duration_sec // 60}:{duration_sec % 60:02d}"
        views = int(v['statistics'].get('viewCount', 0))
        likes = int(v['statistics'].get('likeCount', 0))
        genre = extract_genre(title)

        a = analytics.get(vid_id, {})
        avg_dur = a.get('avg_view_duration', 0)
        avg_pct = a.get('avg_view_percentage', 0)
        watch_min = a.get('watch_minutes', 0)

        video_rows.append({
            'title': title, 'published': published, 'genre': genre,
            'duration_sec': duration_sec, 'duration_str': duration_str,
            'views': views, 'likes': likes,
            'watch_min': watch_min, 'avg_dur': avg_dur, 'avg_pct': avg_pct,
        })

    video_rows.sort(key=lambda x: x['views'], reverse=True)

    safe_print(f"\n{'='*60}")
    safe_print(f"  동영상별 성과 (조회수 순)")
    safe_print(f"{'='*60}")
    safe_print(f"  {'순위':>3} {'조회':>5} {'평균시청':>7} {'시청%':>6} {'길이':>6} {'장르':<20} 제목")
    safe_print(f"  {'-'*80}")

    for i, r in enumerate(video_rows, 1):
        avg_dur_str = f"{int(r['avg_dur'])//60}:{int(r['avg_dur'])%60:02d}" if r['avg_dur'] else "-"
        avg_pct_str = f"{r['avg_pct']:.0f}%" if r['avg_pct'] else "-"
        safe_print(f"  {i:>3}. {r['views']:>4} {avg_dur_str:>7} {avg_pct_str:>6} "
                   f"{r['duration_str']:>6} {r['genre']:<20} {r['title'][:40]}")

    # ── 4. 장르별 성과 비교 ──
    genre_stats = defaultdict(lambda: {
        'count': 0, 'views': 0, 'watch_min': 0,
        'avg_dur_sum': 0, 'avg_pct_sum': 0, 'avg_count': 0
    })
    for r in video_rows:
        g = genre_stats[r['genre']]
        g['count'] += 1
        g['views'] += r['views']
        g['watch_min'] += r['watch_min']
        if r['avg_dur'] > 0:
            g['avg_dur_sum'] += r['avg_dur']
            g['avg_pct_sum'] += r['avg_pct']
            g['avg_count'] += 1

    genre_list = sorted(genre_stats.items(), key=lambda x: x[1]['views'], reverse=True)

    safe_print(f"\n{'='*60}")
    safe_print(f"  장르별 성과 비교")
    safe_print(f"{'='*60}")
    safe_print(f"  {'장르':<22} {'영상수':>5} {'총조회':>6} {'영상당조회':>8} {'평균시청':>7} {'시청%':>6}")
    safe_print(f"  {'-'*65}")

    for genre, g in genre_list:
        avg_views = g['views'] / g['count'] if g['count'] else 0
        avg_dur = g['avg_dur_sum'] / g['avg_count'] if g['avg_count'] else 0
        avg_pct = g['avg_pct_sum'] / g['avg_count'] if g['avg_count'] else 0
        avg_dur_str = f"{int(avg_dur)//60}:{int(avg_dur)%60:02d}" if avg_dur else "-"
        avg_pct_str = f"{avg_pct:.0f}%" if avg_pct else "-"
        safe_print(f"  {genre:<22} {g['count']:>5} {g['views']:>6} "
                   f"{avg_views:>8.1f} {avg_dur_str:>7} {avg_pct_str:>6}")

    # ── 5. 트래픽 소스 ──
    traffic = get_traffic_sources(youtube_analytics, channel_id)
    if traffic:
        safe_print(f"\n{'='*60}")
        safe_print(f"  트래픽 소스 (최근 90일)")
        safe_print(f"{'='*60}")
        safe_print(f"  {'소스':<20} {'조회수':>8} {'시청(분)':>10}")
        safe_print(f"  {'-'*40}")
        for name, views, watch in traffic:
            safe_print(f"  {name:<20} {views:>8,} {watch:>10.0f}")

    # ── 6. 일별 추이 ──
    daily = get_daily_views(youtube_analytics, channel_id)
    if daily:
        safe_print(f"\n{'='*60}")
        safe_print(f"  일별 추이 (최근 28일)")
        safe_print(f"{'='*60}")
        safe_print(f"  {'날짜':<12} {'조회':>6} {'시청(분)':>8} {'구독+':>5}  그래프")
        safe_print(f"  {'-'*55}")

        max_views = max(int(r[1]) for r in daily) if daily else 1
        total_v, total_w, total_s = 0, 0, 0
        for row in daily:
            day, v, w, s = row[0], int(row[1]), int(row[2]), int(row[3])
            total_v += v; total_w += w; total_s += s
            bar_len = int(v / max_views * 30) if max_views else 0
            bar = '#' * bar_len
            safe_print(f"  {day:<12} {v:>6} {w:>8} {s:>5}  {bar}")
        safe_print(f"  {'-'*55}")
        safe_print(f"  {'합계':<12} {total_v:>6} {total_w:>8} {total_s:>5}")

    # ── 7. 인사이트 요약 ──
    safe_print(f"\n{'='*60}")
    safe_print(f"  분석 인사이트")
    safe_print(f"{'='*60}")

    if genre_list:
        best_genre = genre_list[0]
        safe_print(f"  - 가장 조회수 높은 장르: {best_genre[0]} ({best_genre[1]['views']}회)")

        # 시청지속율 기준 최고 장르
        best_retention = max(
            [(g, s) for g, s in genre_list if s['avg_count'] > 0],
            key=lambda x: x[1]['avg_pct_sum'] / x[1]['avg_count'],
            default=None
        )
        if best_retention:
            pct = best_retention[1]['avg_pct_sum'] / best_retention[1]['avg_count']
            safe_print(f"  - 시청지속율 최고 장르: {best_retention[0]} ({pct:.0f}%)")

    if video_rows:
        top = video_rows[0]
        safe_print(f"  - 최고 조회수 영상: {top['title'][:40]} ({top['views']}회)")

        top_retention = max(
            [r for r in video_rows if r['avg_pct'] > 0],
            key=lambda x: x['avg_pct'],
            default=None
        )
        if top_retention:
            safe_print(f"  - 시청지속율 최고 영상: {top_retention['title'][:40]} ({top_retention['avg_pct']:.0f}%)")

    safe_print(f"\n{'='*60}")
    safe_print("분석 완료!")


if __name__ == "__main__":
    main()
