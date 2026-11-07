# API nhạc Xiaozhi

Bản đang chạy trên máy chủ: Zing ở cổng **5006**, YouTube Music ở cổng **5007**.

| Thư mục | Việc | Cổng |
| --- | --- | --- |
| `opt/music/mp3-api` | Lấy bài từ Zing | 5555, chỉ trong máy |
| `opt/music/adapter` | Cổng Zing cho ESP32, kiểm tra mã VIP | 5006 |
| `opt/youtube-music` | YouTube Music, MP3 mono 44,1 kHz, 96 kbps | 5007 |

Mã VIP là 6 ký tự, ví dụ `RBA1B2`. Danh sách mã được phép nằm ở `opt/music/adapter/allowed-codes.txt`.

Cache bài YouTube không nằm trong git. Máy mới tự tải lại khi có người nghe.

## Máy mới cần gì

- Ubuntu
- Node.js 18
- Python 3.11 trở lên, `ffmpeg`, Deno 2.3 trở lên (YouTube cần Deno)
- `git`

## Cài

Đứng trong thư mục vừa `git clone`, chạy bằng root:

```bash
apt-get update
apt-get install -y nodejs npm python3 python3-venv ffmpeg ca-certificates curl

curl -fsSL -o /tmp/deno.zip https://github.com/denoland/deno/releases/latest/download/deno-x86_64-unknown-linux-gnu.zip
python3 -c "import zipfile; zipfile.ZipFile('/tmp/deno.zip').extractall('/usr/local/bin')"
chmod +x /usr/local/bin/deno

cp -a opt/music /opt/music
cp -a opt/youtube-music /opt/youtube-music
mkdir -p /var/lib/youtube-music

cd /opt/music/mp3-api && npm ci --omit=dev
cd /opt/music/adapter && npm ci --omit=dev

python3 -m venv /opt/youtube-music/venv
/opt/youtube-music/venv/bin/pip install -U -r /opt/youtube-music/requirements.txt

cp etc/systemd/system/mp3-api.service /etc/systemd/system/
cp etc/systemd/system/music-adapter.service /etc/systemd/system/
cp etc/systemd/system/youtube-music.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now mp3-api music-adapter youtube-music
```

Kiểm tra:

```bash
curl -s http://127.0.0.1:5555/health
curl -s http://127.0.0.1:5007/health
```

YouTube khỏe thì trả `{"ok":true,"service":"youtube-music","video":false}`.

Sửa mã VIP xong thì `systemctl restart music-adapter`. Sửa `server.py` xong thì `systemctl restart youtube-music`.
