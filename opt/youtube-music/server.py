#!/usr/bin/env python3
"""YouTube Music -> MP3 cho Xiaozhi.

Xiaozhi goi GET /stream_pcm?song=&artist= va can JSON {"audio_url": "..."}.
audio_url phai la file MP3. Server nay tim bai tren YouTube Music, doi sang MP3,
roi tra link noi bo. Kho MP3 cu (cong 5006) khong nam o day.
"""

import hashlib
import json
import os
import re
import shutil
import subprocess
import threading
import unicodedata
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", "5007"))
CACHE_DIR = os.environ.get("CACHE_DIR", "/data")
# 1/true/yes/on: cho phep /stream_video. Mac dinh tat. Nhac /stream_pcm van chay.
ENABLE_VIDEO = os.environ.get("ENABLE_VIDEO", "0").strip().lower() in ("1", "true", "yes", "on")
READY_BYTES = 8192
SEARCH_TIMEOUT = 20

os.makedirs(CACHE_DIR, exist_ok=True)
jobs = {}
jobs_lock = threading.Lock()


class Job:
    def __init__(self, key, query):
        self.key = key
        self.query = query
        self.title = query
        self.thumb = ""
        self.path = os.path.join(CACHE_DIR, key + ".mp3")
        self.part = self.path + ".part"
        self.cover = os.path.join(CACHE_DIR, key + ".wide.png")
        self.meta = os.path.join(CACHE_DIR, key + ".json")
        self.error = None
        self.ready = False
        self.done = False
        self.cond = threading.Condition()
        self.procs = []
        self.video_url = ""
        self.found = False
        self.vframes = deque()
        self.vcond = threading.Condition()
        self.vready = False
        self.vdone = False
        self.vstop = False


def normalize(song, artist):
    text = " ".join((song or "").split())
    extra = " ".join((artist or "").split())
    if extra:
        text = (text + " " + extra).strip()
    return text


def job_key(query):
    return hashlib.sha256(query.encode("utf-8")).hexdigest()[:20]


STYLE_WORDS = {
    "remix", "cover", "lyrics", "lyric", "karaoke", "official", "mv", "audio",
    "video", "beat", "version", "ver", "full", "hd", "live", "piano",
}
# Loi noi thuong dinh o dau/cuoi cau, khong phai ten bai.
LEAD_WORDS = {
    "tim", "bai", "hat", "nhac", "mo", "phat", "bat", "nghe", "youtube",
    "music", "giup", "toi", "minh", "ho", "choi", "cai", "mot",
}
TAIL_WORDS = {"di", "nhe", "voi", "a", "oi", "nha", "dum"}


def fold(text):
    text = unicodedata.normalize("NFD", text or "")
    text = "".join(ch for ch in text if unicodedata.category(ch) != "Mn")
    return text.replace("đ", "d").replace("Đ", "d").lower()


def tokens(text):
    return re.findall(r"[a-z0-9]+", fold(text))


def cleaned_query(query):
    words = (query or "").split()
    while len(words) > 1 and fold(words[0]) in LEAD_WORDS:
        words.pop(0)
    while len(words) > 1 and fold(words[-1]) in TAIL_WORDS:
        words.pop()
    return " ".join(words)


def search_plans(query, video=False):
    # Tim cau da noi, roi ban khong dau. Video khong dung YouTube Music.
    text = cleaned_query(query)
    plain = " ".join(tokens(text))
    plans = [("ytsearch8:", text)]
    if plain and plain != text.lower():
        plans.append(("ytsearch8:", plain))
    if not video:
        plans.append(("ytmsearch5:", text))
    words = text.split()
    if len(words) >= 4:
        plans.append(("ytsearch5:", " ".join(words[1:])))
    return plans


def edit_distance(a, b):
    if abs(len(a) - len(b)) > 2:
        return 3
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[-1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def close_word(word, other):
    # "hay"/"hãy" da trung sau khi bo dau. "trao" van khop "trau", "doremon" khop "doraemon".
    if word == other:
        return True
    if min(len(word), len(other)) < 3:
        return False
    if (word in other or other in word) and abs(len(word) - len(other)) <= 3:
        return True
    limit = 1 if min(len(word), len(other)) < 6 else 2
    return edit_distance(word, other) <= limit


def word_hit(word, found):
    if word in found:
        return True
    return any(close_word(word, other) for other in found)


def score_title(query, title):
    wanted = tokens(query)
    found = set(tokens(title))
    if not wanted or not found:
        return 0
    content = [word for word in wanted if word not in STYLE_WORDS] or wanted
    hit = sum(1 for word in content if word_hit(word, found))
    value = hit / len(content)
    styles = [word for word in wanted if word in STYLE_WORDS]
    if styles and any(word_hit(word, found) for word in styles):
        value += 0.2
    return value


def ytdlp_base():
    # YouTube chan link video neu thieu bo giai ma JavaScript. Deno/Node giai ma do.
    cmd = ["yt-dlp", "--no-warnings", "--no-check-certificates"]
    if shutil.which("deno"):
        cmd += ["--js-runtimes", "deno", "--remote-components", "ejs:github"]
    elif shutil.which("node"):
        cmd += ["--js-runtimes", "node"]
    return cmd


def list_flat(prefix, query):
    proc = subprocess.run(
        ytdlp_base()
        + [
            "--flat-playlist",
            "-J",
            prefix + query,
        ],
        capture_output=True,
        text=True,
        timeout=8,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        return []
    try:
        info = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return []
    found = []
    for item in info.get("entries") or []:
        if not item:
            continue
        title = item.get("title") or ""
        video_id = item.get("id") or ""
        page = item.get("url") or item.get("webpage_url") or ""
        if page and not page.startswith("http"):
            page = "https://www.youtube.com/watch?v=" + page
        if not page and video_id:
            page = "https://www.youtube.com/watch?v=" + video_id
        if title and page:
            found.append((title, page))
    return found


def media_of(page):
    proc = subprocess.run(
        ytdlp_base()
        + [
            "--no-playlist",
            "-f",
            "bestaudio/best",
            "-j",
            page,
        ],
        capture_output=True,
        text=True,
        timeout=SEARCH_TIMEOUT,
    )
    if proc.returncode != 0 or not proc.stdout.strip():
        raise RuntimeError("Khong lay duoc audio")
    info = json.loads(proc.stdout.strip().splitlines()[-1])
    url = info.get("url")
    if not url:
        for item in info.get("requested_formats") or info.get("formats") or []:
            if item.get("url") and item.get("acodec") not in (None, "none"):
                url = item["url"]
                break
    if not url:
        raise RuntimeError("Khong lay duoc audio")
    return info.get("title") or "", url, info.get("thumbnail") or ""


def pick_page(query, video=False):
    needle = cleaned_query(query)
    best = None
    first = None
    seen = set()
    print("[youtube-music] tim gan dung: %s" % needle)
    for prefix, text in search_plans(query, video):
        for title, page in list_flat(prefix, text):
            if page in seen:
                continue
            seen.add(page)
            if first is None:
                first = (title, page)
            value = score_title(needle, title)
            if best is None or value > best[0]:
                best = (value, title, page)
        if best and best[0] >= 0.6:
            break
    # Khop mot phan la du. Neu diem thap, van lay bai YouTube xep dau.
    if best and best[0] >= 0.25:
        print("[youtube-music] chon %s (%.2f)" % (best[1], best[0]))
        return best[1], best[2]
    if first is not None:
        print("[youtube-music] chon tuong doi %s" % first[0])
        return first
    raise RuntimeError("Khong tim thay bai tren YouTube Music")


def run_search(query):
    title, page = pick_page(query)
    got_title, url, thumb = media_of(page)
    return got_title or title, url, thumb


def media_pair(page):
    proc = subprocess.run(
        ytdlp_base()
        + [
            "--no-playlist",
            "-f",
            "bv*[height<=480][vcodec^=avc1]+ba[acodec^=mp4a]/bv*[height<=480]+ba",
            "-g",
            page,
        ],
        capture_output=True,
        text=True,
        timeout=SEARCH_TIMEOUT,
    )
    lines = [line.strip() for line in proc.stdout.splitlines() if line.strip().startswith("http")]
    if not lines:
        reason = (proc.stderr or "").strip().splitlines()
        detail = reason[-1] if reason else "Khong lay duoc video"
        raise RuntimeError(detail[-180:])
    if len(lines) == 1:
        return lines[0], lines[0]
    return lines[0], lines[1]


def pump_video(job):
    cmd = [
        "ffmpeg",
        "-nostdin",
        "-loglevel",
        "error",
        "-reconnect",
        "1",
        "-reconnect_streamed",
        "1",
        "-reconnect_delay_max",
        "5",
        "-i",
        job.video_url,
        "-map",
        "0:v:0",
        "-an",
        "-vf",
        "scale=160:120:force_original_aspect_ratio=increase,crop=160:120,fps=2",
        "-q:v",
        "31",
        "-f",
        "mjpeg",
        "pipe:1",
    ]
    ff = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    job.procs.append(ff)
    buf = b""
    try:
        while not job.vstop:
            chunk = ff.stdout.read(16384)
            if not chunk:
                break
            buf += chunk
            while not job.vstop:
                start = buf.find(b"\xff\xd8")
                if start < 0:
                    buf = buf[-1:]
                    break
                end = buf.find(b"\xff\xd9", start + 2)
                if end < 0:
                    buf = buf[start:]
                    break
                frame = buf[start : end + 2]
                buf = buf[end + 2 :]
                with job.vcond:
                    while len(job.vframes) >= 4 and not job.vstop:
                        job.vcond.wait(0.05)
                    if job.vstop:
                        break
                    job.vframes.append(frame)
                    job.vready = True
                    job.vcond.notify_all()
    finally:
        job.vstop = True
        ff.kill()
        with job.vcond:
            job.vdone = True
            job.vcond.notify_all()


def video_worker(job):
    try:
        ranked = []
        seen = set()
        needle = cleaned_query(job.query)
        for prefix, text in search_plans(job.query, video=True):
            for title, page in list_flat(prefix, text):
                if page in seen:
                    continue
                seen.add(page)
                ranked.append((score_title(needle, title), title, page))
        ranked.sort(key=lambda item: item[0], reverse=True)
        if not ranked:
            raise RuntimeError("Khong tim thay video")
        last = "Khong lay duoc video"
        chosen = None
        for score, title, page in ranked[:4]:
            try:
                video_url, audio_url = media_pair(page)
            except RuntimeError as exc:
                last = str(exc)
                print("[youtube-music] bo qua %s: %s" % (title, last))
                continue
            chosen = (title, video_url, audio_url, score)
            break
        if chosen is None:
            raise RuntimeError(last)
        job.title = chosen[0]
        print("[youtube-music] video %s (%.2f)" % (chosen[0], chosen[3]))
        audio_url = chosen[2]
        if os.path.exists(job.path) and os.path.getsize(job.path) > READY_BYTES:
            with job.cond:
                job.ready = True
                job.done = True
                job.cond.notify_all()
        else:
            threading.Thread(target=transcode, args=(job, audio_url, 24000, "32k"), daemon=True).start()
            ready, err = wait_ready(job, 35)
            if not ready:
                raise RuntimeError(err or "Khong co tieng video")
        with job.cond:
            job.video_url = chosen[1]
            job.found = True
            job.cond.notify_all()
        pump_video(job)
    except Exception as exc:
        job.vstop = True
        with job.cond:
            job.error = str(exc)
            job.done = True
            job.cond.notify_all()
        with job.vcond:
            job.vdone = True
            job.vcond.notify_all()


def start_video_job(query):
    key = job_key("video5\n" + query)
    with jobs_lock:
        current = jobs.get(key)
        # Job cu da xem xong hoac bi ngat: tao moi, khong phat lai hang hinh da het.
        if current is not None and not current.error and not current.vstop and not current.vdone:
            return current
        if current is not None:
            current.vstop = True
            with current.vcond:
                current.vcond.notify_all()
        job = Job(key, query)
        jobs[key] = job
        threading.Thread(target=video_worker, args=(job,), daemon=True).start()
        return job


def write_meta(job):
    with open(job.meta, "w", encoding="utf-8") as fh:
        json.dump({"title": job.title, "thumbnail": job.thumb}, fh, ensure_ascii=False)


def load_meta(job):
    if not os.path.exists(job.meta):
        return
    try:
        with open(job.meta, encoding="utf-8") as fh:
            meta = json.load(fh)
        job.title = meta.get("title") or job.title
        job.thumb = meta.get("thumbnail") or ""
    except (OSError, json.JSONDecodeError):
        return


def save_cover(thumb, dest):
    if not thumb or os.path.exists(dest):
        return os.path.exists(dest)
    # ffmpeg chon dinh dang theo duoi file. ".png.part" bi tu choi, nen giu duoi .png.
    tmp = dest + ".tmp.png"
    try:
        proc = subprocess.run(
            [
                "ffmpeg",
                "-y",
                "-nostdin",
                "-loglevel",
                "error",
                "-i",
                thumb,
                "-vf",
                "scale=300:160:force_original_aspect_ratio=decrease",
                "-frames:v",
                "1",
                tmp,
            ],
            capture_output=True,
            text=True,
            timeout=25,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        print("[youtube-music] anh bia loi:", exc)
        proc = None
    if proc and proc.returncode == 0 and os.path.exists(tmp) and os.path.getsize(tmp) > 100:
        os.replace(tmp, dest)
        return True
    if proc and proc.stderr:
        print("[youtube-music] anh bia loi:", proc.stderr.strip()[-300:])
    if os.path.exists(tmp):
        try:
            os.remove(tmp)
        except OSError:
            pass
    return False


def transcode(job, media_url, sample_rate=44100, bitrate="96k"):
    ff = subprocess.Popen(
        [
            "ffmpeg",
            "-nostdin",
            "-loglevel",
            "error",
            "-reconnect",
            "1",
            "-reconnect_streamed",
            "1",
            "-reconnect_delay_max",
            "5",
            "-i",
            media_url,
            "-vn",
            "-ac",
            "1",
            "-ar",
            str(sample_rate),
            "-c:a",
            "libmp3lame",
            "-b:a",
            bitrate,
            "-write_xing",
            "0",
            "-f",
            "mp3",
            job.part,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    job.procs.append(ff)
    while True:
        code = ff.poll()
        size = os.path.getsize(job.part) if os.path.exists(job.part) else 0
        with job.cond:
            if size >= READY_BYTES and not job.ready:
                job.ready = True
                job.cond.notify_all()
            if code is not None:
                if code == 0 and size > 0:
                    os.replace(job.part, job.path)
                    job.done = True
                    job.ready = True
                else:
                    err = ff.stderr.read().decode("utf-8", "replace")[-400:]
                    job.error = err or "ffmpeg loi"
                    job.done = True
                job.cond.notify_all()
                break
        threading.Event().wait(0.2)


def start_job(query):
    key = job_key(query)
    with jobs_lock:
        job = jobs.get(key)
        if job is None:
            job = Job(key, query)
            jobs[key] = job
            threading.Thread(target=worker, args=(job,), daemon=True).start()
        return job


def worker(job):
    try:
        if os.path.exists(job.path) and os.path.getsize(job.path) > READY_BYTES:
            load_meta(job)
            if not job.thumb:
                try:
                    title, _url, thumb = run_search(job.query)
                    job.title = title
                    job.thumb = thumb
                    write_meta(job)
                except Exception:
                    pass
            save_cover(job.thumb, job.cover)
            with job.cond:
                job.ready = True
                job.done = True
                job.cond.notify_all()
            return
        title, url, thumb = run_search(job.query)
        job.title = title
        job.thumb = thumb
        write_meta(job)
        threading.Thread(target=save_cover, args=(job.thumb, job.cover), daemon=True).start()
        transcode(job, url)
    except Exception as exc:
        with job.cond:
            job.error = str(exc)
            job.done = True
            job.cond.notify_all()


def wait_ready(job, seconds):
    with job.cond:
        job.cond.wait_for(lambda: job.ready or job.error or job.done, seconds)
        return job.ready, job.error


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print("[youtube-music]", self.address_string(), fmt % args)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/health"):
            body = json.dumps({"ok": True, "service": "youtube-music", "video": ENABLE_VIDEO}).encode("utf-8")
            self._send(200, "application/json", body)
            return
        if parsed.path == "/stream_pcm":
            self.handle_search(parsed)
            return
        if parsed.path == "/stream_video" or parsed.path.startswith("/video/"):
            if not ENABLE_VIDEO:
                self._send(503, "application/json", b'{"error":"video youtube dang tat"}')
                return
            if parsed.path == "/stream_video":
                self.handle_video_search(parsed)
            else:
                self.handle_video(parsed.path[len("/video/") :])
            return
        if parsed.path.startswith("/audio/"):
            self.handle_audio(parsed.path[len("/audio/") :])
            return
        if parsed.path.startswith("/cover/"):
            self.handle_cover(parsed.path[len("/cover/") :])
            return
        self._send(404, "application/json", b'{"error":"not found"}')

    def handle_search(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        query = normalize((qs.get("song") or [""])[0], (qs.get("artist") or [""])[0])
        if not query:
            self._send(400, "application/json", b'{"error":"thieu ten bai"}')
            return
        job = start_job(query)
        ready, err = wait_ready(job, 18)
        if not ready:
            message = err or "Dang xu ly, thu lai"
            self._send(503, "application/json", json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"))
            return
        host = self.headers.get("Host", "127.0.0.1:%d" % PORT)
        if not os.path.exists(job.cover):
            for _ in range(20):
                if os.path.exists(job.cover):
                    break
                threading.Event().wait(0.2)
        payload = {
            "audio_url": "http://%s/audio/%s.mp3" % (host, job.key),
            "title": job.title,
        }
        if os.path.exists(job.cover) and os.path.getsize(job.cover) > 100:
            payload["cover_url"] = "http://%s/cover/%s.wide.png" % (host, job.key)
        self._send(200, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def handle_video_search(self, parsed):
        qs = urllib.parse.parse_qs(parsed.query)
        query = normalize((qs.get("song") or [""])[0], (qs.get("artist") or [""])[0])
        if not query:
            self._send(400, "application/json", b'{"error":"thieu ten bai"}')
            return
        job = start_video_job(query)
        with job.cond:
            job.cond.wait_for(lambda: job.found or job.error, 40)
        if not job.found or not job.video_url:
            message = job.error or "Khong lay duoc video"
            self._send(503, "application/json", json.dumps({"error": message}, ensure_ascii=False).encode("utf-8"))
            return
        host = self.headers.get("Host", "127.0.0.1:%d" % PORT)
        payload = {
            "audio_url": "http://%s/audio/%s.mp3" % (host, job.key),
            "video_url": "http://%s/video/%s.mjpg" % (host, job.key),
            "title": job.title,
            "fps": 2,
            "width": 160,
            "height": 120,
        }
        self._send(200, "application/json", json.dumps(payload, ensure_ascii=False).encode("utf-8"))

    def handle_video(self, name):
        key = name[:-5] if name.endswith(".mjpg") else name
        with jobs_lock:
            job = jobs.get(key)
        if job is None or not job.video_url:
            self._send(404, "application/json", b'{"error":"chua co video"}')
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/octet-stream")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        try:
            while True:
                with job.vcond:
                    while not job.vframes and not job.vdone:
                        job.vcond.wait(0.2)
                    frame = job.vframes.popleft() if job.vframes else None
                    done = job.vdone and frame is None
                if frame is None:
                    if done:
                        break
                    continue
                packet = len(frame).to_bytes(4, "big") + frame
                self.wfile.write(("%X\r\n" % len(packet)).encode("ascii"))
                self.wfile.write(packet)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError):
            job.vstop = True
            with job.vcond:
                job.vcond.notify_all()

    def handle_cover(self, name):
        key = name[:-4] if name.endswith(".png") else name
        path = os.path.join(CACHE_DIR, key + ".png")
        if not os.path.exists(path):
            self._send(404, "application/json", b'{"error":"chua co anh bia"}')
            return
        with open(path, "rb") as fh:
            data = fh.read()
        self.send_response(200)
        self.send_header("Content-Type", "image/png")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def handle_audio(self, name):
        key = name[:-4] if name.endswith(".mp3") else name
        path = os.path.join(CACHE_DIR, key + ".mp3")
        part = path + ".part"
        with jobs_lock:
            job = jobs.get(key)
        if job is not None:
            wait_ready(job, 15)
        source = path if os.path.exists(path) else part if os.path.exists(part) else None
        if source is None:
            self._send(404, "application/json", b'{"error":"chua co file"}')
            return
        # File da xong: tra tron ven. File dang ghi: gui dan de Xiaozhi khong cho qua 2 giay.
        if os.path.exists(path):
            data = open(path, "rb")
            size = os.path.getsize(path)
            self.send_response(200)
            self.send_header("Content-Type", "audio/mpeg")
            self.send_header("Content-Length", str(size))
            self.send_header("Accept-Ranges", "bytes")
            self.end_headers()
            try:
                shutil.copyfileobj(data, self.wfile, length=64 * 1024)
            except (BrokenPipeError, ConnectionResetError):
                pass
            finally:
                data.close()
            return
        self.send_response(200)
        self.send_header("Content-Type", "audio/mpeg")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        offset = 0
        try:
            while True:
                source_now = part if os.path.exists(part) else path
                if not os.path.exists(source_now):
                    break
                with open(source_now, "rb") as fh:
                    fh.seek(offset)
                    chunk = fh.read(32 * 1024)
                if chunk:
                    offset += len(chunk)
                    self.wfile.write(("%X\r\n" % len(chunk)).encode("ascii"))
                    self.wfile.write(chunk)
                    self.wfile.write(b"\r\n")
                    self.wfile.flush()
                    continue
                done = job.done if job is not None else os.path.exists(path)
                if done and os.path.exists(path) and os.path.getsize(path) <= offset:
                    break
                threading.Event().wait(0.2)
            self.wfile.write(b"0\r\n\r\n")
        except (BrokenPipeError, ConnectionResetError, FileNotFoundError):
            pass

    def _send(self, status, content_type, body):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def main():
    if shutil.which("yt-dlp") is None or shutil.which("ffmpeg") is None:
        raise SystemExit("Can yt-dlp va ffmpeg trong PATH")
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print("YouTube Music dang nghe cong %d" % PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
