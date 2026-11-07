const express = require('express');
const path = require('path');
const { ZingMp3 } = require('./dist');

const app = express();
const PORT = process.env.PORT || 5555;

app.use(express.json());
app.use(express.static(path.join(__dirname, 'public')));

// health
app.get('/health', (_req, res) => {
  res.json({ ok: true });
});

// basic demos
app.get('/api/top100', async (_req, res) => {
  try {
    const data = await ZingMp3.getTop100();
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/home', async (_req, res) => {
  try {
    const data = await ZingMp3.getHome();
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

// full list according to README
app.get('/api/song', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getSong(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

// redirect to stream URL for simple playback (avoid CORS)
app.get('/api/song/stream', async (req, res) => {
  try {
    const { id, quality = '128' } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const response = await ZingMp3.getSong(String(id));
    const url = response?.data?.[String(quality)];
    if (!url || typeof url !== 'string') {
      return res.status(404).json({ error: `Stream URL not found for quality ${quality}` });
    }
    res.redirect(url);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/detail-playlist', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getDetailPlaylist(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/chart-home', async (_req, res) => {
  try {
    const data = await ZingMp3.getChartHome();
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/newrelease-chart', async (_req, res) => {
  try {
    const data = await ZingMp3.getNewReleaseChart();
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/info-song', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getInfoSong(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/artist', async (req, res) => {
  try {
    const { name } = req.query;
    if (!name) return res.status(400).json({ error: 'Missing name' });
    const data = await ZingMp3.getArtist(String(name));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/artist-songs', async (req, res) => {
  try {
    const { id, page = '1', count = '15' } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getListArtistSong(String(id), String(page), String(count));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/lyric', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getLyric(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/search', async (req, res) => {
  try {
    const { q } = req.query;
    if (!q) return res.status(400).json({ error: 'Missing q' });
    const data = await ZingMp3.search(String(q));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/list-mv', async (req, res) => {
  try {
    const { id, page = '1', count = '15' } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getListMV(String(id), String(page), String(count));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/category-mv', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getCategoryMV(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.get('/api/video', async (req, res) => {
  try {
    const { id } = req.query;
    if (!id) return res.status(400).json({ error: 'Missing id' });
    const data = await ZingMp3.getVideo(String(id));
    res.json(data);
  } catch (e) {
    res.status(e?.response?.status || 500).json({ error: e?.message || 'Internal Error' });
  }
});

app.listen(PORT, () => {
  console.log(`Server running at http://localhost:${PORT}`);
});
