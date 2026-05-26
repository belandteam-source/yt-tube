import yt_dlp
import time
import random
import re
import os
import json
from datetime import datetime, timezone

class YouTubePlaylistGenerator:
    def __init__(self, cookies_file='cookies.txt'):
        self.cookies_file = cookies_file
        self.cache_file = '.channel_cache.json'
        self.channels_dir = 'channels'
        self.load_cache()
        if not os.path.exists(self.channels_dir):
            os.makedirs(self.channels_dir)

    def load_cache(self):
        try:
            with open(self.cache_file, 'r') as f:
                self.cache = json.load(f)
        except:
            self.cache = {'channels': {}}

    def save_cache(self):
        with open(self.cache_file, 'w') as f:
            json.dump(self.cache, f)

    def safe_filename(self, name):
        safe = re.sub(r'[^\w\s-]', '', name).strip()
        safe = re.sub(r'[-\s]+', '_', safe)
        return safe.lower()

    def get_stream_info(self, url):
        ydl_opts = {
            'quiet': False,
            'no_warnings': False,
            'socket_timeout': 60,
            'retries': 3,
            'geo_bypass': True,
            'extractor_args': {
                'youtube': {
                    'player_client': ['tv_embedded', 'web_embedded', 'android_vr'],
                    'skip': ['translated_subs'],
                }
            },
            'format': 'best/bestvideo+bestaudio',
        }

        if os.path.exists(self.cookies_file):
            ydl_opts['cookiefile'] = self.cookies_file
            print(f"  🍪 Using cookies file")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if not info:
                    return None

                video_id = info.get('id', '')
                channel_id = info.get('channel_id', video_id)
                title = info.get('title', 'Unknown')
                channel_name = info.get('channel', info.get('uploader', 'Unknown'))
                channel_url = info.get('channel_url', url)
                clean_name = re.sub(r'[^\w\s-]', '', channel_name).strip()

                live_status = info.get('live_status', '')
                is_live = live_status in ['is_live', 'is_upcoming', 'live'] or info.get('is_live')
                print(f"  📡 Live status: {live_status}, is_live: {is_live}")

                formats = info.get('formats', [])
                print(f"  📋 Total formats: {len(formats)}")

                # Get direct URL if available
                direct_url = info.get('url', '')

                video_formats = [
                    f for f in formats
                    if f.get('url') and
                    f.get('vcodec', 'none') != 'none' and
                    'manifest.googlevideo.com' in f.get('url', '')
                ]

                if not video_formats:
                    video_formats = [
                        f for f in formats
                        if f.get('url') and f.get('vcodec', 'none') != 'none'
                    ]

                if not video_formats and direct_url:
                    video_formats = [{'url': direct_url, 'height': 0, 'quality_tag': 'Auto'}]

                if not video_formats:
                    print(f"  ⚫ No usable formats")
                    return {
                        'status': 'offline',
                        'video_id': video_id,
                        'channel_id': channel_id,
                        'name': clean_name,
                        'title': title,
                        'channel_url': channel_url,
                        'is_live': False,
                    }

                video_formats.sort(key=lambda f: f.get('height', 0), reverse=True)
                print(f"  ✅ Found {len(video_formats)} video formats, best: {video_formats[0].get('height',0)}p")

                streams = {}
                hd = [f for f in video_formats if f.get('height', 0) >= 720]
                if hd:
                    streams['hd'] = {'url': hd[0]['url'], 'height': hd[0].get('height', 0), 'quality_tag': f"{hd[0].get('height',0)}p"}

                mobile = [f for f in video_formats if 0 < f.get('height', 0) <= 480]
                if mobile:
                    streams['mobile'] = {'url': mobile[0]['url'], 'height': mobile[0].get('height', 0), 'quality_tag': f"{mobile[0].get('height',0)}p"}

                if not streams:
                    streams['main'] = {'url': video_formats[0]['url'], 'height': video_formats[0].get('height', 0), 'quality_tag': f"{video_formats[0].get('height',0)}p"}

                self.cache['channels'][channel_id] = {'name': channel_name, 'video_id': video_id, 'last_seen': datetime.now().isoformat()}

                return {
                    'status': 'live',
                    'video_id': video_id,
                    'channel_id': channel_id,
                    'name': clean_name,
                    'title': title,
                    'channel_url': channel_url,
                    'streams': streams,
                    'is_live': is_live,
                }

        except Exception as e:
            print(f"  ❌ Error: {str(e)[:200]}")
            return None

    def generate_playlists(self, channels_data):
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        header = ["#EXTM3U", "#EXT-X-VERSION:3", f"# Generated: {now_utc}", f"# Total channels: {len(channels_data)}", ""]

        main_lines = header.copy()
        hd_lines = header.copy()
        mobile_lines = header.copy()
        live_count = 0
        offline_count = 0

        for ch in channels_data:
            name = ch.get('name', 'Unknown')
            channel_id = ch.get('channel_id', '')
            video_id = ch.get('video_id', '')

            if ch.get('status') == 'live':
                live_count += 1
                main_stream = ch.get('streams', {}).get('hd') or next(iter(ch.get('streams', {}).values()), None)
                mobile_stream = ch.get('streams', {}).get('mobile')

                if main_stream:
                    qtag = main_stream.get('quality_tag', 'Auto')
                    main_lines += [f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Live",{name} [{qtag}] 🔴', main_stream['url'], ""]
                    hd_lines += [f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="HD",{name} [HD]', main_stream['url'], ""]
                    mobile_lines += [f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Mobile",{name} [Mobile]', (mobile_stream or main_stream)['url'], ""]

                    safe = self.safe_filename(name)
                    with open(f"{self.channels_dir}/{safe}.m3u8", 'w', encoding='utf-8') as f:
                        f.write(f"#EXTM3U\n# {name}\n# Generated: {now_utc}\n\n")
                        f.write(f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}",{name} [{qtag}] 🔴 LIVE\n')
                        f.write(main_stream['url'] + "\n")
            else:
                offline_count += 1
                fallback = f"https://www.youtube.com/watch?v={video_id}"
                for lines in [main_lines, hd_lines, mobile_lines]:
                    lines += [f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Offline",{name} [Offline]', fallback, ""]

        for filename, lines in [('streams.m3u8', main_lines), ('streams_hd.m3u8', hd_lines), ('streams_mobile.m3u8', mobile_lines)]:
            with open(filename, 'w', encoding='utf-8') as f:
                f.write('\n'.join(lines))
            print(f"✅ {filename}")

        with open('stats.json', 'w') as f:
            json.dump({'generated': now_utc, 'total': len(channels_data), 'live': live_count, 'offline': offline_count}, f, indent=2)

        return live_count, offline_count


def main():
    if not os.path.exists('streams.txt'):
        print("❌ streams.txt not found")
        return

    with open('streams.txt', 'r') as f:
        lines = [l.strip() for l in f if l.strip() and not l.startswith('#')]

    print(f"📡 Processing {len(lines)} channels...")
    generator = YouTubePlaylistGenerator()
    channels_data = []

    for i, url in enumerate(lines, 1):
        print(f"\n[{i}/{len(lines)}] {url}")
        time.sleep(random.uniform(2, 4))
        info = generator.get_stream_info(url)
        if info:
            channels_data.append(info)

    print("\n🎬 Generating playlists...")
    live, offline = generator.generate_playlists(channels_data)
    generator.save_cache()
    print(f"\n{'='*40}")
    print(f"✅ DONE: {live} live / {offline} offline / {len(channels_data)} total")
    print(f"{'='*40}")

if __name__ == "__main__":
    main()
