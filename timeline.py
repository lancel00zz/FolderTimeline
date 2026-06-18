#!/usr/bin/env python3
"""
Folder Timeline Viewer — pywebview variant
Identical to timeline.py except the window is opened with pywebview instead
of Safari, giving a native, chrome-free WKWebView panel with no address bar,
no tab bar, and no Start Page flash.

Prerequisites (one-time install):
  pip3 install pywebview --break-system-packages

Requires macOS 12+ and Python 3.9+.
"""

import calendar
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlparse

_MISSING = object()  # sentinel for unset thumbnail cache entries

# ── Date parsing ──────────────────────────────────────────────────────────────

_RE_FULL  = re.compile(r'^(\d{4})[.\-](\d{2})[.\-](\d{2})')
_RE_MONTH = re.compile(r'^(\d{4})[.\-](\d{2})(?![.\-\d])')
_RE_YEAR  = re.compile(r'^(\d{4})(?![\d.\-])')

def _parse_date(name: str, birthtime: float) -> datetime.date:
    """Return a date for a filename using spec priority order."""
    m = _RE_FULL.match(name)
    if m:
        try:
            return datetime.date(int(m[1]), int(m[2]), int(m[3]))
        except ValueError:
            pass

    m = _RE_MONTH.match(name)
    if m:
        y, mo = int(m[1]), int(m[2])
        try:
            return datetime.date(y, mo, calendar.monthrange(y, mo)[1])
        except ValueError:
            pass

    m = _RE_YEAR.match(name)
    if m:
        try:
            return datetime.date(int(m[1]), 1, 1)
        except ValueError:
            pass

    return datetime.date.fromtimestamp(birthtime)


# ── Folder scanner ────────────────────────────────────────────────────────────

def _scan(folder: str) -> list[dict]:
    """Scan top-level folder contents, skipping hidden (.) and utility (_) items."""
    items = []
    with os.scandir(folder) as it:
        for e in it:
            if e.name.startswith('.') or e.name.startswith('_'):
                continue
            st = e.stat(follow_symlinks=False)
            birth = getattr(st, 'st_birthtime', st.st_mtime)
            items.append({
                'date':     _parse_date(e.name, birth).isoformat(),
                'name':     e.name,
                'path':     e.path,
                'is_dir':   e.is_dir(follow_symlinks=False),
                'birth_ts': birth,
            })
    return sorted(items, key=lambda x: x['date'])


# ── Quick Look thumbnail helper ───────────────────────────────────────────────

def _make_thumb(path: str) -> bytes | None:
    """Generate a high-res Quick Look thumbnail; return PNG bytes or None."""
    try:
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(
                ['/usr/bin/qlmanage', '-t', '-s', '800', '-o', d, path],
                capture_output=True, timeout=10, check=False,
            )
            for fname in os.listdir(d):
                if fname.lower().endswith('.png'):
                    with open(os.path.join(d, fname), 'rb') as f:
                        return f.read()
    except Exception:
        pass
    return None


# ── HTTP helper ───────────────────────────────────────────────────────────────

class _Handler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.server._touch()
        parsed = urlparse(self.path)
        qs = parse_qs(parsed.query)

        if parsed.path == '/':
            html = self.server._get_html()
            self.send_response(200)
            self.send_header('Content-Type',   'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(html)))
            self.end_headers()
            self.wfile.write(html)

        elif parsed.path == '/open':
            raw = qs.get('path', [None])[0]
            if raw is None:
                return self._reply(400, 'missing path')
            path = unquote(raw)
            real = os.path.realpath(path)
            root = os.path.realpath(self.server.allowed_root)
            if not (real == root or real.startswith(root + os.sep)):
                return self._reply(403, 'forbidden')
            # Open file or folder with its default application.
            # Using plain `open` avoids any Accessibility permission requirements.
            subprocess.run(['open', path], check=False)
            self._reply(200, 'ok')

        elif parsed.path == '/thumb':
            raw = qs.get('path', [None])[0]
            if raw is None:
                return self._reply(400, 'missing path')
            path = unquote(raw)
            real = os.path.realpath(path)
            root = os.path.realpath(self.server.allowed_root)
            if not (real == root or real.startswith(root + os.sep)):
                return self._reply(403, 'forbidden')
            if os.path.isdir(path):
                return self._reply(404, 'no thumb for dirs')
            thumb = self.server.get_thumb(path)
            if thumb is None:
                return self._reply(404, 'no thumb')
            self.send_response(200)
            self.send_header('Content-Type',   'image/png')
            self.send_header('Content-Length', str(len(thumb)))
            self.send_header('Access-Control-Allow-Origin', '*')
            self.end_headers()
            self.wfile.write(thumb)

        elif parsed.path == '/close':
            self._reply(200, 'ok')
            close_fn = getattr(self.server, '_close_window', None)
            if close_fn:
                threading.Thread(target=close_fn, daemon=True).start()
            else:
                threading.Thread(target=self.server._stop, daemon=True).start()

        elif parsed.path == '/minimize':
            self._reply(200, 'ok')
            fn = getattr(self.server, '_minimize_window', None)
            if fn:
                threading.Thread(target=fn, daemon=True).start()

        elif parsed.path == '/fullscreen':
            self._reply(200, 'ok')
            fn = getattr(self.server, '_fullscreen_window', None)
            if fn:
                threading.Thread(target=fn, daemon=True).start()

        elif parsed.path == '/shutdown':
            self._reply(200, 'ok')
            threading.Thread(target=self.server._stop, daemon=True).start()

        else:
            self._reply(404, 'not found')

    def _reply(self, code: int, body: str):
        data = body.encode()
        self.send_response(code)
        self.send_header('Content-Type',   'text/plain')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Access-Control-Allow-Origin', '*')
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_):
        pass  # silence server logs


class _Server:
    IDLE = 300  # seconds of inactivity before self-shutdown

    def __init__(self, folder: str, allowed_root: str | None = None):
        self.folder       = folder
        self.allowed_root = allowed_root or folder
        self._html        = b''
        # ThreadingHTTPServer spawns a new thread per request so thumbnail
        # generation never blocks the callout or open handlers.
        self._httpd   = ThreadingHTTPServer(('127.0.0.1', 0), _Handler)
        self._httpd.folder       = folder
        self._httpd.allowed_root = self.allowed_root
        self._httpd._touch       = self._touch
        self._httpd._stop        = self._stop
        self._httpd._get_html    = lambda: self._html
        self._httpd.get_thumb    = self.get_thumb
        self.port       = self._httpd.server_address[1]
        self._last      = time.monotonic()
        self._done      = threading.Event()
        self._thumb_cache: dict = {}   # path → bytes | None
        self._thumb_lock  = threading.Lock()

    def _touch(self):
        self._last = time.monotonic()

    def _stop(self):
        self._done.set()

    def get_thumb(self, path: str) -> bytes | None:
        """Return cached PNG bytes for path, generating on first call.

        Thread-safe: the first caller generates the thumbnail; concurrent
        callers block on an Event and share the result instead of racing.
        """
        with self._thumb_lock:
            entry = self._thumb_cache.get(path, _MISSING)
            if entry is _MISSING:
                evt = threading.Event()
                self._thumb_cache[path] = evt   # reserve the slot
                generate = True
            elif isinstance(entry, threading.Event):
                evt = entry
                generate = False
            else:
                return entry                    # bytes or None (no thumb)

        if not generate:
            evt.wait(timeout=15)
            with self._thumb_lock:
                result = self._thumb_cache.get(path, _MISSING)
            return None if (result is _MISSING or isinstance(result, threading.Event)) else result

        thumb = _make_thumb(path)
        with self._thumb_lock:
            stored_evt = self._thumb_cache[path]
            self._thumb_cache[path] = thumb
        stored_evt.set()
        return thumb

    def prefetch_thumbs(self, items: list) -> None:
        """Kick off background thumbnail generation for all files at startup."""
        for item in items:
            if not item['is_dir']:
                threading.Thread(
                    target=self.get_thumb,
                    args=(item['path'],),
                    daemon=True,
                ).start()

    def start(self):
        self._httpd.timeout = 5

        def _serve():
            while not self._done.is_set():
                if time.monotonic() - self._last > self.IDLE:
                    self._done.set()
                    break
                self._httpd.handle_request()

        threading.Thread(target=_serve, daemon=True).start()


# ── HTML / chart generator ────────────────────────────────────────────────────

_MONTHS_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
                 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']


def _build_items_data(items: list[dict]) -> list[dict]:
    """Return a flat list of items for the front-end, using the
    filename-parsed date (not filesystem birthtime) as the display date."""
    result = []
    for item in items:
        d          = datetime.date.fromisoformat(item['date'])
        date_label = f"{d.day} {_MONTHS_SHORT[d.month - 1]} {d.year}"
        name       = item['name'] + ('/' if item['is_dir'] else '')
        result.append({
            'name':       name,
            'path':       item['path'],
            'date':       item['date'],
            'date_label': date_label,
            'is_dir':     item['is_dir'],
        })
    return result


def _generate_html(folder: str, title: str, items: list[dict], port: int) -> str:
    now          = datetime.datetime.now()
    generated    = now.strftime('%-d %B %Y at %H:%M')
    items_json   = json.dumps(_build_items_data(items), ensure_ascii=False)
    items_count  = len(items)
    item_word    = 'item' if items_count == 1 else 'items'
    # For multi-file titles like "8 items — Folder", extract just "Folder";
    # for single-directory titles it's already just the folder name.
    if '\u2014' in title and title[0].isdigit():
        folder_label = title.split('\u2014', 1)[1].strip()
    else:
        folder_label = title

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Timeline \u2014 {title}</title>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  /* Invisible drag strip replaces the native title bar (frameless mode) */
  #drag-strip {{
    position: fixed;
    top: 0; left: 0; right: 0;
    height: 28px;
    -webkit-app-region: drag;
    z-index: 200;
  }}
  /* macOS traffic-light window buttons */
  #wm-buttons {{
    position: fixed;
    top: 8px; left: 12px;
    display: flex; gap: 8px;
    z-index: 201;
    -webkit-app-region: no-drag;
  }}
  #wm-buttons button {{
    width: 12px; height: 12px;
    border-radius: 50%;
    border: 0.5px solid rgba(0,0,0,.15);
    padding: 0; cursor: pointer;
    display: flex; align-items: center; justify-content: center;
    font-size: 0;
  }}
  #btn-close      {{ background: #FF5F57; }}
  #btn-minimize   {{ background: #FFBD2E; }}
  #btn-fullscreen {{ background: #28C840; }}
  #wm-buttons:hover button {{ font-size: 8px; font-weight: 900; color: rgba(0,0,0,.5); line-height: 1; }}
  #wm-buttons:hover #btn-close::before      {{ content: '✕'; }}
  #wm-buttons:hover #btn-minimize::before   {{ content: '−'; }}
  #wm-buttons:hover #btn-fullscreen::before {{ content: '+'; }}
  body {{
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: #fff;
    color: #222;
    padding: 40px 32px 48px; /* top raised by 28 px to clear the drag strip */
    overflow-x: hidden; /* prevent button row from enforcing a window minimum width */
  }}
  .path {{
    font-size: 12px;
    color: #bbb;
    margin-bottom: 4px;
    word-break: break-all;
  }}
  h1 {{
    font-size: 18px;
    font-weight: 600;
    margin-bottom: 32px;
  }}
  #chart-wrap {{ position: relative; width: 100%; overflow-x: auto; }}
  svg {{ display: block; }}

  /* Controls row: filter on the left, granularity buttons on the right */
  #controls-row {{
    display: flex;
    align-items: center;
    justify-content: space-between;
    margin-bottom: 8px;
  }}
  #filter-row {{
    display: flex;
    align-items: center;
  }}
  #filter-input {{
    font-size: 12px;
    padding: 3px 10px;
    border: 1px solid #ddd;
    border-radius: 4px;
    outline: none;
    width: 200px;
    color: #444;
    background: #fff;
  }}
  #filter-input:focus {{ border-color: #bbb; }}
  #filter-clear {{
    font-size: 14px;
    line-height: 1;
    padding: 2px 8px 3px;
    border: 1px solid #ddd;
    border-radius: 0 4px 4px 0;
    background: #f5f5f5;
    color: #999;
    cursor: pointer;
    visibility: hidden;
  }}
  #filter-clear:hover {{ background: #ebebeb; color: #555; }}

  /* Granularity controls */
  #gran-controls {{
    display: flex;
    gap: 5px;
  }}
  .gran-btn {{
    font-size: 11px;
    font-weight: 500;
    padding: 3px 12px;
    border-radius: 4px;
    border: 1px solid #ddd;
    background: #f5f5f5;
    color: #aaa;
    cursor: pointer;
    letter-spacing: .02em;
    transition: background .1s, color .1s, border-color .1s;
  }}
  .gran-btn:hover {{
    background: #ebebeb;
    color: #555;
  }}
  .gran-btn.active {{
    background: #632CA6;
    color: #fff;
    border-color: #632CA6;
  }}

  /* Bars */
  .bar-file  {{ fill: #632CA6; }}
  .bar-dir   {{ fill: #2D9CDB; }}
  .bar-hover {{ fill: transparent; cursor: pointer; }}
  .bar-hover:hover ~ .bar-file {{ opacity: .75; }}
  .bar-hover:hover ~ .bar-dir  {{ opacity: .75; }}

  /* Callout */
  #callout {{
    display: none;
    position: fixed;
    background: #fff;
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 11px 15px 10px;
    box-shadow: 0 4px 18px rgba(0,0,0,.11);
    font-size: 13px;
    min-width: 200px;
    max-width: 360px;
    z-index: 999;
    pointer-events: auto;
  }}
  /* Arrow direction is set by JS via data-dir="above|right|left" */
  #callout[data-dir="above"]::after {{
    content: '';
    position: absolute;
    top: 100%; left: var(--arrow-x, 50%);
    transform: translateX(-50%);
    border: 7px solid transparent;
    border-top: 7px solid #fff;
    z-index: 1;
  }}
  #callout[data-dir="above"]::before {{
    content: '';
    position: absolute;
    top: 100%; left: var(--arrow-x, 50%);
    transform: translateX(-50%);
    border: 8px solid transparent;
    border-top: 8px solid #ddd;
  }}
  #callout[data-dir="right"]::after {{
    content: '';
    position: absolute;
    right: 100%; top: var(--arrow-y, 50%);
    transform: translateY(-50%);
    border: 7px solid transparent;
    border-right: 7px solid #fff;
    z-index: 1;
  }}
  #callout[data-dir="right"]::before {{
    content: '';
    position: absolute;
    right: 100%; top: var(--arrow-y, 50%);
    transform: translateY(-50%);
    border: 8px solid transparent;
    border-right: 8px solid #ddd;
  }}
  #callout[data-dir="left"]::after {{
    content: '';
    position: absolute;
    left: 100%; top: var(--arrow-y, 50%);
    transform: translateY(-50%);
    border: 7px solid transparent;
    border-left: 7px solid #fff;
    z-index: 1;
  }}
  #callout[data-dir="left"]::before {{
    content: '';
    position: absolute;
    left: 100%; top: var(--arrow-y, 50%);
    transform: translateY(-50%);
    border: 8px solid transparent;
    border-left: 8px solid #ddd;
  }}

  .ct-item {{
    padding: 5px 0;
    border-bottom: 1px solid #f2f2f2;
  }}
  .ct-item:last-child {{ border-bottom: none; padding-bottom: 0; }}
  .ct-item:first-child {{ padding-top: 0; }}

  .ct-name {{
    display: block;
    text-decoration: none;
    color: #222;
    font-size: 13px;
    white-space: normal;
    word-break: break-word;
    max-width: 320px;
  }}
  .ct-name:hover {{ text-decoration: underline; }}
  .ct-name.is-dir  {{ color: #2D9CDB; font-weight: 600; }}
  .ct-name.is-file {{ color: #632CA6; }}

  .ct-meta {{
    display: flex;
    gap: 8px;
    align-items: center;
    margin-top: 3px;
  }}

  /* Eye preview icon */
  .ct-eye-wrap {{
    display: inline-flex;
    align-items: center;
    margin-left: auto;
    padding-left: 8px;
    cursor: pointer;
    color: #ccc;
    flex-shrink: 0;
    transition: color .15s;
  }}
  .ct-eye-wrap.is-file:hover {{ color: #632CA6; }}
  .ct-eye-wrap.is-dir        {{ color: #ccc; }}
  .ct-eye-wrap.is-dir:hover  {{ color: #2D9CDB; }}

  /* Floating thumbnail preview */
  #preview-float {{
    display: none;
    position: fixed;
    background: #fff;
    border: 1px solid #e0e0e0;
    border-radius: 10px;
    box-shadow: 0 8px 32px rgba(0,0,0,.18);
    padding: 8px;
    z-index: 1000;
    pointer-events: none;
  }}
  #preview-float img {{
    display: block;
    max-width: 500px;
    max-height: 500px;
    border-radius: 4px;
    object-fit: contain;
  }}

  .ct-badge {{
    font-size: 10px;
    font-weight: 600;
    letter-spacing: .04em;
    text-transform: uppercase;
    padding: 1px 5px;
    border-radius: 3px;
    color: #fff;
  }}
  .ct-badge.is-file {{ background: #632CA6; }}
  .ct-badge.is-dir  {{ background: #2D9CDB; }}
  .ct-created {{
    font-size: 11px;
    color: #bbb;
  }}

  .legend {{
    display: flex;
    gap: 20px;
    margin-top: 14px;
    font-size: 12px;
    color: #888;
  }}
  .ldot {{
    display: inline-block;
    width: 10px; height: 10px;
    border-radius: 2px;
    margin-right: 5px;
    vertical-align: middle;
  }}
  footer {{
    margin-top: 28px;
    font-size: 11px;
    color: #ccc;
  }}
</style>
</head>
<body>
<div id="drag-strip"></div>
<div id="wm-buttons">
  <button id="btn-close"      title="Close"       onclick="fetch('http://127.0.0.1:{port}/close').catch(()=>{{}})"></button>
  <button id="btn-minimize"   title="Minimize"    onclick="fetch('http://127.0.0.1:{port}/minimize').catch(()=>{{}})"></button>
  <button id="btn-fullscreen" title="Fullscreen"  onclick="fetch('http://127.0.0.1:{port}/fullscreen').catch(()=>{{}})"></button>
</div>
<p class="path">{folder}</p>
<h1 id="timeline-title">Timeline \u2014 {items_count} {item_word} \u2014 {folder_label}</h1>
<div id="controls-row">
  <div id="filter-row">
    <input id="filter-input" type="text" placeholder="Filter by name\u2026"
           autocomplete="off" spellcheck="false">
    <button id="filter-clear" title="Clear filter">&times;</button>
  </div>
  <div id="gran-controls">
    <button class="gran-btn" data-gran="year">Year</button>
    <button class="gran-btn" data-gran="quarter">Quarter</button>
    <button class="gran-btn" data-gran="month">Month</button>
    <button class="gran-btn" data-gran="day">Day</button>
  </div>
</div>
<div id="chart-wrap"><svg id="chart"></svg></div>
<div class="legend">
  <span><span class="ldot" style="background:#632CA6"></span>File</span>
  <span><span class="ldot" style="background:#2D9CDB"></span>Folder</span>
</div>
<footer>Click any item to open.&nbsp; This view was generated on {generated}.</footer>
<div id="callout"></div>
<div id="preview-float"><img src="" alt="preview"></div>

<script>
const ITEMS        = {items_json};
const PORT         = {port};
const FOLDER_LABEL = {json.dumps(folder_label)};

const MONTHS = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
const M   = {{ top: 24, right: 32, bottom: 52, left: 54 }};
const SH  = 420;
let BAR = 12;
const NS  = 'http://www.w3.org/2000/svg';

let currentGran   = 'year'; // start with the at-a-glance year view
let _scrollRight  = true;   // snap to most-recent on first render & gran switch
let currentFilter = '';
let filteredItems = ITEMS;

function svgEl(tag, attrs) {{
  const e = document.createElementNS(NS, tag);
  for (const [k, v] of Object.entries(attrs)) e.setAttribute(k, v);
  return e;
}}

function tsOf(iso) {{
  return new Date(iso + 'T12:00:00Z').getTime();
}}

// ── Granularity helpers ───────────────────────────────────────────────────────

function granKey(iso, gran) {{
  const [y, m] = iso.split('-').map(Number);
  if (gran === 'day')     return iso;
  if (gran === 'month')   return `${{y}}-${{String(m).padStart(2, '0')}}`;
  if (gran === 'quarter') return `${{y}}-Q${{Math.ceil(m / 3)}}`;
  return `${{y}}`;
}}

function granCenter(key, gran) {{
  if (gran === 'day') return tsOf(key);
  if (gran === 'month') {{
    const [y, m] = key.split('-').map(Number);
    return Date.UTC(y, m - 1, 15);
  }}
  if (gran === 'quarter') {{
    const [y, q] = key.split('-Q').map(Number);
    return Date.UTC(y, (q - 1) * 3 + 1, 15);
  }}
  return Date.UTC(parseInt(key), 6, 1);
}}

function chooseGranularity() {{
  // Pick the finest level whose natural chart width stays under 5000 px.
  // Window width is no longer the constraint — the chart scrolls if needed.
  const MAX_NATURAL_W = 5000;
  const MIN_SPACING   = 26;
  for (const gran of ['day', 'month', 'quarter', 'year']) {{
    const keys = new Set(filteredItems.map(it => granKey(it.date, gran)));
    if (keys.size * MIN_SPACING <= MAX_NATURAL_W) return gran;
  }}
  return 'year';
}}

function groupItems(gran) {{
  const map = new Map();
  for (const item of filteredItems) {{
    const key = granKey(item.date, gran);
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(item);
  }}
  return new Map([...map.entries()].sort((a, b) => a[0] < b[0] ? -1 : 1));
}}

// ── Scale ─────────────────────────────────────────────────────────────────────

function buildScale(w, gran) {{
  const allTs = filteredItems.map(it => tsOf(it.date));
  const dMin  = new Date(Math.min(...allTs));
  const dMax  = new Date(Math.max(...allTs));

  // Align tStart/tEnd to full period boundaries so bars centred at
  // mid-year or mid-quarter are never clipped at the chart edges.
  let tStart, tEnd;
  if (gran === 'year') {{
    tStart = Date.UTC(dMin.getUTCFullYear(), 0, 1);
    tEnd   = Date.UTC(dMax.getUTCFullYear() + 1, 0, 1);
  }} else if (gran === 'quarter') {{
    tStart = Date.UTC(dMin.getUTCFullYear(), Math.floor(dMin.getUTCMonth() / 3) * 3,     1);
    tEnd   = Date.UTC(dMax.getUTCFullYear(), Math.floor(dMax.getUTCMonth() / 3) * 3 + 3, 1);
  }} else {{
    tStart = Date.UTC(dMin.getUTCFullYear(), dMin.getUTCMonth(), 1);
    tEnd   = Date.UTC(dMax.getUTCFullYear(), dMax.getUTCMonth() + 1, 1);
  }}

  const span        = tEnd - tStart || 1;
  const totalMonths = (dMax.getUTCFullYear() - dMin.getUTCFullYear()) * 12
                    + (dMax.getUTCMonth()    - dMin.getUTCMonth())    + 1;
  return {{ xOf: ts => (ts - tStart) / span * w, tStart, tEnd, totalMonths }};
}}

// ── Render ────────────────────────────────────────────────────────────────────

function renderChart() {{
  const wrap    = document.getElementById('chart-wrap');
  const windowW = wrap.clientWidth || 900;
  const h       = SH - M.top  - M.bottom;

  // Build into a fresh detached SVG, then swap atomically.
  // This avoids the blank-frame flicker that occurs when clearing svg.innerHTML
  // in-place — the browser never sees an intermediate empty state.
  const svg  = document.createElementNS(NS, 'svg');
  svg.id     = 'chart';
  svg.style.display = 'block';

  // Folder icon symbol — referenced by <use> inside directory bars.
  const _defs = document.createElementNS(NS, 'defs');
  const _sym  = document.createElementNS(NS, 'symbol');
  _sym.id = 'icon-folder'; _sym.setAttribute('viewBox', '0 0 24 24');
  const _ip = document.createElementNS(NS, 'path');
  _ip.setAttribute('d', 'M2 7C2 5.9 2.9 5 4 5L9.5 5L11.5 7L20 7C21.1 7 22 7.9 22 9L22 18C22 19.1 21.1 20 20 20L4 20C2.9 20 2 19.1 2 18Z');
  _sym.appendChild(_ip); _defs.appendChild(_sym); svg.appendChild(_defs);

  const g = svgEl('g', {{ transform: `translate(${{M.left}},${{M.top}})` }});
  svg.appendChild(g);

  if (filteredItems.length === 0) {{
    svg.setAttribute('width',   windowW);
    svg.setAttribute('height',  SH);
    svg.setAttribute('viewBox', `0 0 ${{windowW}} ${{SH}}`);
    const msg = ITEMS.length === 0 ? 'No items found in this folder.'
                                   : 'No items match your filter.';
    const t = svgEl('text', {{ x: windowW/2, y: SH/2, 'text-anchor': 'middle', fill: '#bbb', 'font-size': 14 }});
    t.textContent = msg;
    svg.appendChild(t);
    document.getElementById('chart').replaceWith(svg);
    return;
  }}

  if (currentGran === null) currentGran = chooseGranularity();
  const gran   = currentGran;
  const groups = groupItems(gran);

  // Natural chart width: reserve 26 px per axis stride so the 80%-bar formula
  // always produces a comfortable bar.
  //
  // For Year + Quarter we count ALL periods in the time span (including empty
  // ones), because bars are positioned on a continuous time axis — empty
  // quarters still occupy real space.  Month / Day keep using occupied-only
  // groups.size to avoid absurdly wide charts.
  const _allTs2  = filteredItems.map(it => tsOf(it.date));
  const _spanMin = new Date(Math.min(..._allTs2));
  const _spanMax = new Date(Math.max(..._allTs2));
  const _moSpan  = (_spanMax.getUTCFullYear() - _spanMin.getUTCFullYear()) * 12
                 + (_spanMax.getUTCMonth()    - _spanMin.getUTCMonth())    + 1;
  // Total days from start of first month to end of last month (Day view natural width)
  const _dayStart    = Date.UTC(_spanMin.getUTCFullYear(), _spanMin.getUTCMonth(), 1);
  const _dayEnd      = Date.UTC(_spanMax.getUTCFullYear(), _spanMax.getUTCMonth() + 1, 1);
  const _daySpan     = Math.round((_dayEnd - _dayStart) / 86400000);
  const _naturalCols = gran === 'year'    ? (_spanMax.getUTCFullYear() - _spanMin.getUTCFullYear() + 1)
                     : gran === 'quarter' ? Math.ceil(_moSpan / 3)
                     : gran === 'month'   ? _moSpan
                     :                     _daySpan;
  const W = Math.max(_naturalCols * 26 + M.left + M.right, windowW);
  const w = W - M.left - M.right;

  svg.setAttribute('width',   W);
  svg.setAttribute('height',  SH);
  svg.setAttribute('viewBox', `0 0 ${{W}} ${{SH}}`);

  const scale  = buildScale(w, gran);

  // Bar width = 90 % of one axis-stride column, measured directly from the
  // time scale (linear in ms → every stride of the same type is identical px).
  //
  // We align the reference point to the START of the period that contains
  // tStart — not tStart itself — so we always measure a full stride, even
  // when the earliest item falls mid-quarter or mid-year.
  const _stRaw = new Date(scale.tStart);
  const _stA   = gran === 'year'    ? new Date(Date.UTC(_stRaw.getUTCFullYear(), 0, 1))
               : gran === 'quarter' ? new Date(Date.UTC(_stRaw.getUTCFullYear(), Math.floor(_stRaw.getUTCMonth() / 3) * 3, 1))
               : gran === 'month'   ? new Date(Date.UTC(_stRaw.getUTCFullYear(), _stRaw.getUTCMonth(), 1))
               :                     _stRaw;
  const _stB   = gran === 'year'    ? new Date(Date.UTC(_stA.getUTCFullYear() + 1, 0, 1))
               : gran === 'quarter' ? new Date(Date.UTC(_stA.getUTCFullYear(), _stA.getUTCMonth() + 3, 1))
               : gran === 'month'   ? new Date(Date.UTC(_stA.getUTCFullYear(), _stA.getUTCMonth() + 1, 1))
               :                     new Date(_stA.getTime() + 86400000);
  BAR = Math.max(4, Math.floor((scale.xOf(_stB.getTime()) - scale.xOf(_stA.getTime())) * 0.9));

  // Highlight the active granularity button
  document.querySelectorAll('.gran-btn').forEach(btn => {{
    btn.classList.toggle('active', btn.dataset.gran === gran);
  }});

  const maxCount = Math.max(...[...groups.values()].map(v => v.length));
  const yS       = v => h - (v / (maxCount + 1)) * h;

  // ── X axis: alternating bands + tick lines + labels (granularity-aware) ─────
  //
  // Axis stride: one band = one year  (Year / Quarter views)
  //              one band = one month (Month / Day views)
  // This ensures Year view shows exactly N year-wide bands and N tick lines,
  // not hundreds of monthly stripes.

  const {{ totalMonths }} = scale;
  const axisUnit = gran === 'year'    ? 'year'
                 : gran === 'quarter' ? 'quarter'
                 : gran === 'month'   ? 'month'
                 :                     'day';

  // Align cursor to the stride boundary that contains tStart
  const _s0 = new Date(scale.tStart);
  let cur;
  if (axisUnit === 'year') {{
    cur = new Date(Date.UTC(_s0.getUTCFullYear(), 0, 1));
  }} else if (axisUnit === 'quarter') {{
    cur = new Date(Date.UTC(_s0.getUTCFullYear(), Math.floor(_s0.getUTCMonth() / 3) * 3, 1));
  }} else {{
    cur = new Date(scale.tStart);
  }}

  // Label interval for month / day views
  const lblInterval = totalMonths <= 6  ? 1
                    : totalMonths <= 18 ? 3
                    : totalMonths <= 36 ? 6
                    : 12;

  let alt = false;
  while (cur.getTime() < scale.tEnd) {{
    const yr   = cur.getUTCFullYear();
    const mo   = cur.getUTCMonth();
    const next = axisUnit === 'year'    ? new Date(Date.UTC(yr + 1, 0, 1))
               : axisUnit === 'quarter' ? new Date(Date.UTC(yr, mo + 3, 1))
               : axisUnit === 'month'   ? new Date(Date.UTC(yr, mo + 1, 1))
               :                         new Date(cur.getTime() + 86400000);

    const x0   = scale.xOf(cur.getTime());
    const x1   = scale.xOf(next.getTime());
    const xMid = (Math.max(x0, 0) + Math.min(x1, w)) / 2;

    // Alternating band
    if (alt) g.appendChild(svgEl('rect', {{
      x: Math.max(x0, 0), y: 0,
      width: Math.max(0, Math.min(x1, w) - Math.max(x0, 0)), height: h,
      fill: '#F9F9F9',
    }}));
    alt = !alt;

    // Tick line at left boundary (skip the very first edge)
    if (x0 > 0 && x0 < w)
      g.appendChild(svgEl('line', {{ x1: x0, x2: x0, y1: 0, y2: h + 6, stroke: '#DCDCDC', 'stroke-width': 1 }}));

    // Label centred in this band
    let label = null;
    if (gran === 'year') {{
      label = String(yr);
    }} else if (gran === 'quarter') {{
      // Top row shows year number only — at Q1, or at the very first quarter
      // of the span. The secondary row (Q1–Q4) handles quarter identification.
      if (mo === 0 || cur.getTime() === scale.tStart) {{
        label = String(yr);
      }}
    }} else if (gran === 'month') {{
      // Top row shows year number only — at January, or at the very first
      // month of the span if the data doesn't start in January.
      // The secondary row (month numbers 1–12) handles month identification.
      if (mo === 0 || cur.getTime() === scale.tStart) {{
        label = String(yr);
      }}
    }} else {{
      // Day view — top row: "Jan 2024" at the first day of each month.
      // The secondary row (day numbers 1–31) handles day identification.
      const dayOfMonth = cur.getUTCDate();
      if (dayOfMonth === 1 || cur.getTime() === scale.tStart) {{
        label = MONTHS[mo] + ' ' + String(yr);
      }}
    }}

    if (label !== null && xMid >= 0 && xMid <= w) {{
      const lbl = svgEl('text', {{ x: xMid, y: h + 22, 'text-anchor': 'middle', 'font-size': 11, fill: '#999' }});
      lbl.textContent = label;
      g.appendChild(lbl);
    }}

    // Secondary labels on a second row: Q1–Q4 for Quarter view, 1–12 for Month view
    if (gran === 'quarter' && xMid >= 0 && xMid <= w) {{
      const q  = Math.floor(mo / 3) + 1;
      const ql = svgEl('text', {{ x: xMid, y: h + 38, 'text-anchor': 'middle', 'font-size': 9, fill: '#bbb' }});
      ql.textContent = `Q${{q}}`;
      g.appendChild(ql);
    }}
    if (gran === 'month' && xMid >= 0 && xMid <= w) {{
      const mn = svgEl('text', {{ x: xMid, y: h + 38, 'text-anchor': 'middle', 'font-size': 9, fill: '#bbb' }});
      mn.textContent = String(mo + 1);
      g.appendChild(mn);
    }}
    if (gran === 'day' && xMid >= 0 && xMid <= w) {{
      const dn = svgEl('text', {{ x: xMid, y: h + 38, 'text-anchor': 'middle', 'font-size': 9, fill: '#bbb' }});
      dn.textContent = String(cur.getUTCDate());
      g.appendChild(dn);
    }}

    cur = next;
  }}

  g.appendChild(svgEl('line', {{ x1: 0, x2: w, y1: h, y2: h, stroke: '#DCDCDC', 'stroke-width': 1 }}));

  // Y-axis overlay — a sibling of g (direct child of svg) so its transform
  // can track scrollLeft independently and keep tick labels pinned to the
  // left edge during horizontal scroll.
  const yg = svgEl('g', {{ id: 'yaxis-g', transform: `translate(${{M.left}},${{M.top}})` }});

  // Opaque white backing masks chart content that scrolls beneath the axis.
  yg.appendChild(svgEl('rect', {{
    x: -M.left, y: -M.top, width: M.left + 2, height: SH, fill: '#fff',
  }}));

  for (let tick = 1; tick <= maxCount; tick++) {{
    const y = yS(tick);
    const t = svgEl('text', {{ x: -8, y: y + 4, 'text-anchor': 'end', 'font-size': 11, fill: '#aaa' }});
    t.textContent = tick;
    yg.appendChild(t);
  }}

  const yt = svgEl('text', {{
    transform: `translate(-42,${{h / 2}}) rotate(-90)`,
    'text-anchor': 'middle', 'font-size': 12, fill: '#bbb',
  }});
  yt.textContent = 'Documents';
  yg.appendChild(yt);

  svg.appendChild(yg);  // appended after main g so it paints on top

  // ── Bars ──────────────────────────────────────────────────────────────────
  const UNIT_GAP = 2;

  groups.forEach((items, key) => {{
    const cx    = scale.xOf(granCenter(key, gran));
    const x     = cx - BAR / 2;
    const units = [
      ...items.filter(it => !it.is_dir),
      ...items.filter(it =>  it.is_dir),
    ];

    units.forEach((item, idx) => {{
      const n    = idx + 1;
      const barY = yS(n) + UNIT_GAP;
      const barH = yS(n - 1) - yS(n) - UNIT_GAP;

      g.appendChild(svgEl('rect', {{
        x, y: barY, width: BAR, height: barH,
        rx: 2, class: item.is_dir ? 'bar-dir' : 'bar-file',
      }}));

      if (item.is_dir && barH >= 14 && BAR >= 12) {{
        const ic = Math.min(BAR * 0.68, barH * 0.68, 18);
        g.appendChild(svgEl('use', {{
          href: '#icon-folder',
          x: x + (BAR - ic) / 2, y: barY + (barH - ic) / 2,
          width: ic, height: ic, fill: 'rgba(255,255,255,0.75)',
        }}));
      }}

      const hz = svgEl('rect', {{
        x: x - 4, y: barY, width: BAR + 8, height: barH, class: 'bar-hover',
      }});
      hz.addEventListener('mouseenter', e => {{
        const svgRect = document.getElementById('chart').getBoundingClientRect();
        showCallout(item, units.length, svgRect.left + M.left + cx, e.clientY);
      }});
      hz.addEventListener('mousemove',  e => scheduleReposition(units.length, e.clientY));
      hz.addEventListener('mouseleave', scheduleDismiss);
      hz.addEventListener('click', () => {{
        const qs2 = item.is_dir ? '&dir=1' : '';
        fetch(`http://127.0.0.1:${{PORT}}/open?path=${{encodeURIComponent(item.path)}}${{qs2}}`)
          .catch(() => {{}});
      }});
      g.appendChild(hz);
    }});
  }});

  // Swap the new SVG in atomically — the old one is removed and the new one
  // inserted in a single DOM operation, so the browser never paints a blank state.
  document.getElementById('chart').replaceWith(svg);

  // If the chart was already scrolled (e.g. granularity switch while scrolled),
  // immediately position the Y-axis overlay at the correct offset.
  if (wrap.scrollLeft > 0) {{
    document.getElementById('yaxis-g').setAttribute('transform',
      `translate(${{M.left + wrap.scrollLeft}},${{M.top}})`);
  }}

  // Snap to the most-recent (right) end on first render and after every
  // granularity switch; leave scroll position alone on plain window resize.
  if (_scrollRight) {{
    requestAnimationFrame(() => {{ wrap.scrollLeft = wrap.scrollWidth; }});
    _scrollRight = false;
  }}
}}

// ── Callout ───────────────────────────────────────────────────────────────────

let _hideTimer   = null;
let _ctW         = 0;
let _ctH         = 0;
let _rafId       = null;
let _pendingMove = null;
let _barScreenX  = 0;
let _currentItem = null;

function showCallout(item, stackSize, barScreenX, mouseY) {{
  clearTimeout(_hideTimer);
  _barScreenX  = barScreenX;
  _currentItem = item;

  const ct = document.getElementById('callout');
  ct.innerHTML = '';

  const wrap = document.createElement('div');
  wrap.className = 'ct-item';

  const a = document.createElement('a');
  a.className   = 'ct-name ' + (item.is_dir ? 'is-dir' : 'is-file');
  a.textContent = item.name;
  a.href        = '#';
  a.title       = item.name;
  a.addEventListener('click', ev => ev.preventDefault());

  const meta = document.createElement('div');
  meta.className = 'ct-meta';

  const badge = document.createElement('span');
  badge.className   = 'ct-badge ' + (item.is_dir ? 'is-dir' : 'is-file');
  badge.textContent = item.is_dir ? 'Folder' : 'File';

  const dated = document.createElement('span');
  dated.className   = 'ct-created';
  dated.textContent = item.date_label;

  const eyeWrap = document.createElement('span');
  eyeWrap.className = 'ct-eye-wrap ' + (item.is_dir ? 'is-dir' : 'is-file');
  eyeWrap.innerHTML = '<svg viewBox="0 0 24 24" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/></svg>';

  meta.appendChild(badge);
  meta.appendChild(dated);
  meta.appendChild(eyeWrap);
  wrap.appendChild(a);
  wrap.appendChild(meta);
  ct.appendChild(wrap);

  ct.style.visibility = 'hidden';
  ct.style.left = '-9999px';
  ct.style.top  = '-9999px';
  ct.style.display = 'block';
  _ctW = ct.offsetWidth;
  _ctH = ct.offsetHeight;
  ct.style.visibility = '';
  positionCallout(stackSize, mouseY);
}}

function scheduleReposition(stackSize, mouseY) {{
  _pendingMove = [stackSize, mouseY];
  if (_rafId === null) {{
    _rafId = requestAnimationFrame(() => {{
      _rafId = null;
      if (_pendingMove) {{ positionCallout(..._pendingMove); _pendingMove = null; }}
    }});
  }}
}}

function positionCallout(stackSize, mouseY) {{
  const ct  = document.getElementById('callout');
  if (ct.style.display === 'none') return;
  const vw  = window.innerWidth;
  const vh  = window.innerHeight;
  const GAP = 14;
  const bx  = _barScreenX;

  if (stackSize === 1) {{
    ct.setAttribute('data-dir', 'above');
    let left = bx - _ctW / 2;
    let top  = mouseY - _ctH - GAP;
    if (left + _ctW > vw - 8) left = vw - _ctW - 8;
    if (left < 8)              left = 8;
    if (top  < 8)              top  = mouseY + GAP + 10;
    ct.style.setProperty('--arrow-x', Math.min(Math.max(bx - left, 16), _ctW - 16) + 'px');
    ct.style.left = left + 'px';
    ct.style.top  = top  + 'px';
  }} else {{
    const halfBar        = BAR / 2;
    const wouldClipRight = bx + halfBar + GAP + _ctW > vw - 8;
    const wouldClipLeft  = bx - halfBar - GAP - _ctW < 8;
    const dir  = (wouldClipRight && !wouldClipLeft) ? 'left' : 'right';
    const left = dir === 'left' ? bx - halfBar - GAP - _ctW : bx + halfBar + GAP;
    let   top  = mouseY - _ctH / 2;
    if (top + _ctH > vh - 8) top = vh - _ctH - 8;
    if (top < 8)              top = 8;
    ct.style.setProperty('--arrow-y', Math.min(Math.max(mouseY - top, 16), _ctH - 16) + 'px');
    ct.setAttribute('data-dir', dir);
    ct.style.left = left + 'px';
    ct.style.top  = top  + 'px';
  }}
}}

function scheduleDismiss() {{
  _hideTimer = setTimeout(() => {{
    const ct = document.getElementById('callout');
    if (!ct.matches(':hover')) {{ hidePreview(); ct.style.display = 'none'; }}
  }}, 320);
}}

// ── Floating thumbnail preview ────────────────────────────────────────────────

function showPreview(path) {{
  const pf  = document.getElementById('preview-float');
  const img = pf.querySelector('img');
  const url = `http://127.0.0.1:${{PORT}}/thumb?path=${{encodeURIComponent(path)}}`;

  const doPosition = () => {{
    pf.style.display = 'block';
    const ctRect = document.getElementById('callout').getBoundingClientRect();
    const pfW = pf.offsetWidth, pfH = pf.offsetHeight;
    const vw  = window.innerWidth, vh = window.innerHeight;
    let left = ctRect.right + 12;
    if (left + pfW > vw - 8) left = ctRect.left - pfW - 12;
    let top = ctRect.top;
    if (top + pfH > vh - 8) top = vh - pfH - 8;
    if (top < 8)             top = 8;
    pf.style.left = left + 'px';
    pf.style.top  = top  + 'px';
  }};

  if (img.src === url && img.complete && img.naturalWidth > 0) {{ doPosition(); return; }}
  img.onload  = null;
  img.onerror = null;
  img.onload  = doPosition;
  img.onerror = () => {{ pf.style.display = 'none'; }};
  img.src     = url;
}}

function hidePreview() {{
  document.getElementById('preview-float').style.display = 'none';
}}

const ct = document.getElementById('callout');
ct.addEventListener('mouseenter', () => {{
  clearTimeout(_hideTimer);
  if (_currentItem && !_currentItem.is_dir) showPreview(_currentItem.path);
}});
ct.addEventListener('mouseleave', () => {{
  hidePreview();
  _hideTimer = setTimeout(() => {{ ct.style.display = 'none'; }}, 150);
}});
ct.addEventListener('click', () => {{
  if (!_currentItem) return;
  hidePreview();
  const qs2 = _currentItem.is_dir ? '&dir=1' : '';
  fetch(`http://127.0.0.1:${{PORT}}/open?path=${{encodeURIComponent(_currentItem.path)}}${{qs2}}`)
    .catch(() => {{}});
}});

window.addEventListener('beforeunload', () => {{
  navigator.sendBeacon(`http://127.0.0.1:${{PORT}}/shutdown`);
}});

// Keep Y-axis pinned to the left edge during horizontal scroll
document.getElementById('chart-wrap').addEventListener('scroll', function() {{
  const yg = document.getElementById('yaxis-g');
  if (yg) yg.setAttribute('transform', `translate(${{M.left + this.scrollLeft}},${{M.top}})`);
}});

// Granularity buttons
document.querySelectorAll('.gran-btn').forEach(btn => {{
  btn.addEventListener('click', () => {{
    currentGran  = btn.dataset.gran;
    _scrollRight = true;   // always snap to most-recent when switching view
    renderChart();
    filterInput.focus();   // keep cursor in filter box after switching views
  }});
}});

let _resizeDebounce = null;
window.addEventListener('resize', () => {{
  document.getElementById('chart-wrap').style.overflowX = 'hidden';
  clearTimeout(_resizeDebounce);
  _resizeDebounce = setTimeout(() => {{
    document.getElementById('chart-wrap').style.overflowX = '';
    renderChart();
  }}, 80);
}});

// Close the window when it loses focus (click outside = dismiss overlay).
// Guard with a short delay so the initial window-activation handshake doesn't
// fire an immediate close before the user ever sees the chart.
let _blurEnabled = false;
setTimeout(() => {{ _blurEnabled = true; }}, 600);
window.addEventListener('blur', () => {{
  if (_blurEnabled) fetch(`http://127.0.0.1:${{PORT}}/close`).catch(() => {{}});
}});

// ── Filter ────────────────────────────────────────────────────────────────────

function updateTitleCount(n) {{
  const word    = n === 1 ? 'item' : 'items';
  const q       = currentFilter.trim();
  const titleEl = document.getElementById('timeline-title');
  const safeFL  = FOLDER_LABEL.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
  if (q) {{
    const safeQ     = q.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
    const countPart = `${{n}} ${{word}} matching &ldquo;${{safeQ}}&rdquo;`;
    titleEl.innerHTML = `Timeline \u2014 <span style="color:#2A9D5C">${{countPart}}</span> \u2014 ${{safeFL}}`;
  }} else {{
    titleEl.textContent = `Timeline \u2014 ${{n}} ${{word}} \u2014 ${{FOLDER_LABEL}}`;
  }}
}}

function applyFilter() {{
  const q = currentFilter.toLowerCase().trim();
  filteredItems = q
    ? ITEMS.filter(it => it.name.toLowerCase().includes(q))
    : ITEMS;
  updateTitleCount(filteredItems.length);
  renderChart();
}}

const filterInput = document.getElementById('filter-input');
const filterClear = document.getElementById('filter-clear');

function setFilterActive(active) {{
  if (active) {{
    filterClear.style.visibility  = 'visible';
    filterInput.style.borderRight = 'none';
    filterInput.style.borderRadius = '4px 0 0 4px';
  }} else {{
    filterClear.style.visibility  = 'hidden';
    filterInput.style.borderRight = '1px solid #ddd';
    filterInput.style.borderRadius = '4px';
  }}
}}

filterInput.addEventListener('input', () => {{
  currentFilter = filterInput.value;
  setFilterActive(!!currentFilter);
  applyFilter();
}});

filterInput.addEventListener('keydown', e => {{
  if (e.key === 'Escape') {{
    if (currentFilter) {{
      filterInput.value = '';
      currentFilter = '';
      setFilterActive(false);
      applyFilter();
    }} else {{
      fetch(`http://127.0.0.1:${{PORT}}/close`).catch(() => {{}});  // filter already empty — close the window
    }}
  }}
}});

// Also handle Escape at the document level in case focus drifts elsewhere
document.addEventListener('keydown', e => {{
  if (e.key === 'Escape' && document.activeElement !== filterInput) {{
    if (currentFilter) {{
      filterInput.value = '';
      currentFilter = '';
      setFilterActive(false);
      applyFilter();
      filterInput.focus();
    }} else {{
      fetch(`http://127.0.0.1:${{PORT}}/close`).catch(() => {{}});
    }}
  }}
}});

filterClear.addEventListener('click', () => {{
  filterInput.value = '';
  currentFilter = '';
  setFilterActive(false);
  applyFilter();
  filterInput.focus();
}});

// Auto-focus: deferred so WKWebView has time to become interactive
setTimeout(() => filterInput.focus(), 150);
renderChart();
</script>
</body>
</html>"""


# ── pywebview launcher ────────────────────────────────────────────────────────

def _open_with_pywebview(folder_name: str, url: str, server: '_Server') -> None:
    """Open *url* in a native WKWebView window — no address bar, no Start Page."""
    try:
        import webview
    except ImportError:
        print(
            '\npywebview is not installed. Run this once, then try again:\n\n'
            '  pip3 install pywebview --break-system-packages\n',
            file=sys.stderr,
        )
        sys.exit(1)

    window = webview.create_window(
        f'Timeline \u2014 {folder_name}',
        url,
        width=1120,
        height=720,
        resizable=True,
        background_color='#ffffff',
        frameless=True,
    )

    # Stop the HTTP server when the native window is closed
    window.events.closed += server._stop

    # Expose window controls to the HTTP handler so JS buttons can drive the
    # native window (WKWebView blocks window.close() / window.minimize() directly).
    server._httpd._close_window    = window.destroy
    server._httpd._minimize_window  = window.minimize
    server._httpd._fullscreen_window = window.toggle_fullscreen

    # webview.start() takes over the main thread as the UI run loop.
    # It returns only after the window is closed.
    webview.start()


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print('Usage: timeline.py <folder-or-files...>', file=sys.stderr)
        sys.exit(1)

    raw_paths = [os.path.abspath(p) for p in sys.argv[1:]]
    paths     = [p for p in raw_paths if os.path.exists(p)]
    if not paths:
        print('Error: none of the specified paths exist.', file=sys.stderr)
        sys.exit(1)

    # ── Single directory → existing scan behaviour ─────────────────────────
    if len(paths) == 1 and os.path.isdir(paths[0]):
        folder       = paths[0]
        title        = os.path.basename(folder)
        display_path = folder
        allowed_root = folder
        items        = _scan(folder)

    # ── Explicit selection (files, folders, or a mix) ──────────────────────
    else:
        items = []
        for path in paths:
            name = os.path.basename(path)
            if name.startswith('.') or name.startswith('_'):
                continue
            st    = os.stat(path, follow_symlinks=False)
            birth = getattr(st, 'st_birthtime', st.st_mtime)
            items.append({
                'date':     _parse_date(name, birth).isoformat(),
                'name':     name,
                'path':     path,
                'is_dir':   os.path.isdir(path),
                'birth_ts': birth,
            })
        items.sort(key=lambda x: x['date'])

        # Common ancestor — used for both display and the HTTP security check
        allowed_root = (os.path.commonpath(paths) if len(paths) > 1
                        else os.path.dirname(paths[0]))
        if os.path.isfile(allowed_root):
            allowed_root = os.path.dirname(allowed_root)

        parent_name  = os.path.basename(allowed_root) or allowed_root
        n            = len(items)
        title        = f'{n} item{"s" if n != 1 else ""} — {parent_name}'
        display_path = allowed_root
        folder       = allowed_root

    server = _Server(folder, allowed_root)
    server.start()

    html = _generate_html(display_path, title, items, server.port)
    server._html = html.encode('utf-8')

    # Pre-generate thumbnails in the background while the user browses —
    # most will be ready by the time they first hover over an eye icon.
    server.prefetch_thumbs(items)

    _open_with_pywebview(title, f'http://127.0.0.1:{server.port}/', server)


if __name__ == '__main__':
    main()
