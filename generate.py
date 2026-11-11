import yt_dlp
import time
import random
import re
import os
import json
from datetime import datetime, timezone

EXPIRE_RE = re.compile(r'[?&/]expire[=/](\d{10})')
MIN_CARRY_SECONDS = 1800   # carry a previous URL only if it has >=30 min of life left

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

    def parse_playlist(self, path):
        """Previous playlist -> {tvg_name: {'extinf','url','expire'}} (live googlevideo links only)."""
        entries = {}
        if not os.path.exists(path):
            return entries
        try:
            with open(path, 'r', encoding='utf-8') as f:
                lines = [l.rstrip('\n') for l in f]
        except Exception:
            return entries
        for i, line in enumerate(lines):
            if line.startswith('#EXTINF') and 'tvg-name="' in line:
                m = re.search(r'tvg-name="([^"]+)"', line)
                if not m:
                    continue
                for j in range(i + 1, min(i + 3, len(lines))):
                    u = lines[j].strip()
                    if u.startswith('http'):
                        if 'manifest.googlevideo.com' in u:
                            em = EXPIRE_RE.search(u)
                            entries[m.group(1)] = {
                                'extinf': line,
                                'url': u,
                                'expire': int(em.group(1)) if em else 0
                            }
                        break
        return entries

    def get_stream_info(self, url):
        # Use same approach as pk3011 - cookies with android/ios clients
        ydl_opts = {
            'quiet': False,
            'no_warnings': False,
            'socket_timeout': 30,
            'playlistreverse': False,
            'playlist_items': '1',
            'retries': 5,
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'ios'],
                    'live_from_start': True,
                    'skip': ['webpage', 'configs']
                }
            },
            'geo_bypass': True,
            'headers': {
                'X-Forwarded-For': f'{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}.{random.randint(1,255)}',
                'Accept-Language': 'en-US,en;q=0.9',
                'Origin': 'https://www.youtube.com',
                'Referer': 'https://www.youtube.com/',
            },
        }

        # Pass cookies using 'cookies' key like pk3011
        if os.path.exists(self.cookies_file):
            ydl_opts['cookies'] = self.cookies_file
            print(f"  🍪 Using cookies file")

        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if not info:
                    print(f"  ⚫ No info returned")
                    return None

                video_id = info.get('id', '')
                channel_id = info.get('channel_id', video_id)
                channel_name = info.get('channel', info.get('uploader', 'Unknown'))
                clean_name = re.sub(r'[^\w\s-]', '', channel_name).strip()
                title = info.get('title', 'Unknown')
                channel_url = info.get('channel_url', url)

                live_status = info.get('live_status', '')
                is_live = live_status in ['is_live', 'is_upcoming', 'live'] or info.get('is_live') or info.get('was_live')

                formats = info.get('formats', [])
                print(f"  ℹ️ live_status={live_status} formats={len(formats)}")

                if formats and not is_live:
                    is_live = True
                    print(f"  ⚠️ Has formats, treating as live")

                video_formats = [
                    f for f in formats
                    if f.get('height') and f.get('url') and f.get('vcodec') != 'none'
                ]

                if not video_formats:
                    print(f"  ⚫ No video formats found")
                    return {
                        'status': 'offline',
                        'video_id': video_id,
                        'channel_id': channel_id,
                        'name': clean_name,
                        'title': title,
                        'channel_url': channel_url,
                        'is_live': False,
                    }

                video_formats.sort(key=lambda f: (f.get('height', 0), f.get('fps', 0)), reverse=True)
                print(f"  ✅ Found {len(video_formats)} formats, best: {video_formats[0].get('height',0)}p")

                streams = {}
                hd = [f for f in video_formats if f.get('height', 0) >= 720]
                if hd:
                    streams['hd'] = {'url': hd[0]['url'], 'height': hd[0].get('height', 0), 'quality_tag': f"{hd[0].get('height',0)}p"}

                mobile = [f for f in video_formats if 0 < f.get('height', 0) <= 480]
                if mobile:
                    streams['mobile'] = {'url': mobile[0]['url'], 'height': mobile[0].get('height', 0), 'quality_tag': f"{mobile[0].get('height',0)}p"}

                if not streams:
                    streams['main'] = {'url': video_formats[0]['url'], 'height': video_formats[0].get('height', 0), 'quality_tag': f"{video_formats[0].get('height',0)}p"}

                self.cache['channels'][channel_id] = {
                    'name': channel_name,
                    'video_id': video_id,
                    'last_seen': datetime.now().isoformat()
                }

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

    def generate_playlists(self, channels_data, n_sources):
        now_utc = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')
        now_ts = int(time.time())

        files = ['streams.m3u8', 'streams_hd.m3u8', 'streams_mobile.m3u8', 'streams_audio.m3u8']
        # Parse the PREVIOUS playlists before overwriting - this is the carry-forward source
        prev = {fn: self.parse_playlist(fn) for fn in files}

        live_entries = {fn: [] for fn in files}
        live_names = set()
        offline_list = []

        for ch in channels_data:
            name = ch.get('name', 'Unknown')
            channel_id = ch.get('channel_id', '')
            video_id = ch.get('video_id', '')

            if ch.get('status') == 'live':
                streams = ch.get('streams', {})
                main_stream = streams.get('hd') or next(iter(streams.values()), None)
                mobile_stream = streams.get('mobile')

                if not main_stream:
                    offline_list.append((name, channel_id, f"https://www.youtube.com/watch?v={video_id}"))
                    continue

                live_names.add(name)
                qtag = main_stream.get('quality_tag', 'Auto')
                live_entries['streams.m3u8'].append((f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Live",{name} [{qtag}] 🔴', main_stream['url']))
                live_entries['streams_hd.m3u8'].append((f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="HD",{name} [HD]', main_stream['url']))
                live_entries['streams_mobile.m3u8'].append((f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Mobile",{name} [Mobile]', (mobile_stream or main_stream)['url']))
                live_entries['streams_audio.m3u8'].append((f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}" group-title="Audio",{name} [Audio]', main_stream['url']))

                safe = self.safe_filename(name)
                with open(f"{self.channels_dir}/{safe}.m3u8", 'w', encoding='utf-8') as f:
                    f.write(f"#EXTM3U\n# {name}\n# Generated: {now_utc}\n\n")
                    f.write(f'#EXTINF:-1 tvg-id="{channel_id}" tvg-name="{name}",{name} [{qtag}] 🔴 LIVE\n')
                    f.write(main_stream['url'] + "\n")
            else:
                fallback = f"https://www.youtube.com/watch?v={video_id}" if video_id else ch.get('channel_url', '')
                offline_list.append((name, channel_id, fallback))

        carried_main = 0
        carried_list = []

        for fn in files:
            body = []
            for extinf, url in live_entries[fn]:
                body += [extinf, url, ""]

            # CARRY-FORWARD: any previous entry not refreshed this run keeps playing
            # on its old URL as long as that URL still has >=30 min of life left.
            carried_names = set()
            for name, e in prev[fn].items():
                if name in live_names:
                    continue
                if e['expire'] - now_ts >= MIN_CARRY_SECONDS:
                    body += [e['extinf'], e['url'], ""]
                    carried_names.add(name)

            if fn == 'streams.m3u8':
                carried_main = len(carried_names)
                carried_list = sorted(carried_names)

            written_offline = 0
            for name, cid, fb in offline_list:
                if name in carried_names:
                    continue
                body += [f'#EXTINF:-1 tvg-id="{cid}" tvg-name="{name}" group-title="Offline",{name} [Offline]', fb, ""]
                written_offline += 1

            total = len(live_entries[fn]) + len(carried_names) + written_offline
            header = ["#EXTM3U", "#EXT-X-VERSION:3", f"# Generated: {now_utc}", f"# Total channels: {total}", ""]
            with open(fn, 'w', encoding='utf-8') as f:
                f.write('\n'.join(header + body))
            print(f"✅ {fn} ({total} entries)")

        if carried_main:
            print(f"♻️ Carried {carried_main} channel(s) on still-valid previous URLs: {', '.join(carried_list)}")

        live_count = len(live_names)
        offline_count = len(offline_list)
        errors = max(0, n_sources - len(channels_data))

        with open('stats.json', 'w') as f:
            json.dump({
                'generated': now_utc,
                'total_sources': n_sources,
                'live': live_count,
                'carried': carried_main,
                'offline': offline_count,
                'errors': errors
            }, f, indent=2)

        return live_count, carried_main, offline_count, errors


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
        time.sleep(random.uniform(2, 5))
        info = generator.get_stream_info(url)
        if not info:
            wait = random.uniform(8, 15)
            print(f"  🔁 Extraction failed - retrying once in {wait:.0f}s...")
            time.sleep(wait)
            info = generator.get_stream_info(url)
        if info:
            channels_data.append(info)
        else:
            print("  ❌ Giving up this run - previous URL will be carried if still valid")

    print("\n🎬 Generating playlists...")
    live, carried, offline, errors = generator.generate_playlists(channels_data, len(lines))
    generator.save_cache()
    print(f"\n{'='*48}")
    print(f"✅ DONE: {live} fresh / {carried} carried / {offline} offline / {errors} errors / {len(lines)} sources")
    print(f"{'='*48}")

if __name__ == "__main__":
    main()
