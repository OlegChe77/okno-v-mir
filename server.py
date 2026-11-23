"""Окно в мир — локальный сервер.

Собирает каталог бесплатных ТВ-каналов (iptv-org) и открытых камер
(Live-Environment-Streams), раздаёт страницу плеера, проксирует потоки
(чтобы обойти CORS и подставить User-Agent/Referer) и в фоне проверяет,
какие ссылки живые.

Запуск: python server.py   (или start.bat)
"""
import collections
import gzip
import hashlib
import hmac
import http.client
import json
import os
import re
import secrets
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
import zlib
from concurrent.futures import ThreadPoolExecutor
from email.utils import parsedate_to_datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import sources
import updater
import viewcams

HOST = "127.0.0.1"
PORT = int(os.environ.get("PORT", "8765"))
ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")

COUNTRIES_URL = "https://iptv-org.github.io/api/countries.json"
CAMS_URL = ("https://raw.githubusercontent.com/william-ricchiuti/"
            "Live-Environment-Streams/HEAD/streams.geojson")
HLS_JS_URL = "https://cdn.jsdelivr.net/npm/hls.js@1.5.20/dist/hls.min.js"
MPEGTS_JS_URL = "https://cdn.jsdelivr.net/npm/mpegts.js@1.7.3/dist/mpegts.js"  # сырой MPEG-TS по HTTP (без m3u8)

DAY = 24 * 3600
RECHECK_AFTER = 12 * 3600
M3U8_RE = re.compile(r"\.m3u8?(\?|$)", re.I)
GZIP_MAGIC = bytes([0x1F, 0x8B])


def gunzip_partial(data):
    """Распаковывает gzip, в том числе обрезанный; при ошибке отдаёт как есть."""
    try:
        return zlib.decompressobj(31).decompress(data)
    except Exception:
        return data


_HOST_GATES, _GATES_LOCK = {}, threading.Lock()


class HostGate:
    """Не больше 6 одновременных проверок на хост. Если хост ответил 429 (mcquack.net пускает ~1 запрос
    в несколько секунд), проверки к нему идут по очереди с паузой `interval`."""

    def __init__(self):
        self.sem = threading.BoundedSemaphore(6)
        self.lock = threading.Lock()
        self.next = 0.0
        self.interval = 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            t = max(0.0, self.next - now)
            self.next = max(now, self.next) + self.interval
        if t:
            time.sleep(t)


def host_gate(url):
    host = urllib.parse.urlsplit(url).netloc
    with _GATES_LOCK:
        return _HOST_GATES.setdefault(host, HostGate())


BLOCK_RE = re.compile(r"/blocked/|rtk_block|zabava-block", re.I)  # адрес заглушки после редиректа
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

# У многих бесплатных потоков просроченные сертификаты — для видео проверку
# отключаем. Каталог качаем с обычной проверкой.
SSL_STRICT = ssl.create_default_context()
SSL_LAX = ssl._create_unverified_context()

os.makedirs(CACHE, exist_ok=True)
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


def fetch(url, timeout=60, ctx=SSL_STRICT):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
        return r.read()


def cached(name, url, max_age=DAY):
    """Файл из кэша, если свежий; иначе скачать. Без сети — старая копия."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, "rb") as f:
            return f.read()
    try:
        data = fetch(url)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
        return data
    except Exception as e:
        if os.path.exists(path):
            log("нет сети для", name, "- беру старую копию:", e)
            with open(path, "rb") as f:
                return f.read()
        raise


# ---------------------------------------------------------------- каталог

guess_type = sources.guess_type


def parse_cams(geo, code_to_name):
    items = []
    for f in geo.get("features", []):
        p = f.get("properties") or {}
        url = p.get("url")
        if not url or p.get("status") not in (None, "active"):
            continue
        code = (p.get("country_code") or "").upper()
        name = p.get("name") or "Камера"
        # «Germany - Hessen - A3 | Hanau» → «Hessen - A3 | Hanau»
        prefix = (p.get("display_name") or "") + " - "
        if name.startswith(prefix):
            name = name[len(prefix):]
        coords = (f.get("geometry") or {}).get("coordinates") or [None, None]
        items.append({"k": "cam", "cat": "road", "n": name, "c": code,
                      "cn": code_to_name.get(code, code), "u": url,
                      "x": p.get("url_type") or guess_type(url),
                      "g": p.get("environment") or "",
                      "a": p.get("attribution") or "", "s": ["Live-Environment-Streams"],
                      "lon": coords[0], "lat": coords[1]})
    return items


class Catalog:
    def __init__(self):
        self.ready = threading.Event()
        self.items = []
        self.body_gz = b""
        self.body_gz_pub = b""
        self.error = None

    def build(self):
        try:
            countries = json.loads(cached("countries.json", COUNTRIES_URL))
            name_to_code = {c["name"]: c["code"] for c in countries}
            code_to_name = {c["code"]: c["name"] for c in countries}
            tv = sources.load_tv(cached, name_to_code, code_to_name, log)
            cams = parse_cams(json.loads(cached("cams.geojson", CAMS_URL)),
                              code_to_name)
            views = viewcams.build(name_to_code, code_to_name, log)
            extra = sources.load_extra_cams(name_to_code, code_to_name)
            extra += sources.load_m3u_cams(cached, name_to_code, code_to_name, log)
            raw_n = len(tv) + len(cams) + len(views) + len(extra)
            items = sources.merge(tv + cams + extra + views)
            self.items = items
            # ссылки каналов 18+ (основные и запасные): /p и /warm отдают их только после входа
            ADULT_URLS.clear()
            for it in items:
                if it.get("ad"):
                    ADULT_URLS.update(v["u"] for v in variants(it) if v.get("u"))
            body = json.dumps({"items": items, "built": int(time.time())},
                              ensure_ascii=False, separators=(",", ":"))
            self.body_gz = gzip.compress(body.encode("utf-8"), 6)
            # публичная версия: каналы 18+ заменены пустышками (индекс в списке = id, сдвигать нельзя)
            pub = [{"id": it["id"], "k": "tv", "ad": 1, "n": "", "u": "", "x": "hls", "c": "", "t": [], "s": []}
                   if it.get("ad") else it for it in items]
            self.body_gz_pub = gzip.compress(json.dumps({"items": pub, "built": int(time.time())},
                                                        ensure_ascii=False, separators=(",", ":")).encode("utf-8"), 6)
            n_tv = sum(1 for it in items if it["k"] == "tv")
            n_alt = sum(len(it.get("alt", ())) for it in items)
            log(f"каталог: {n_tv} каналов и {len(items) - n_tv} камер "
                f"(из {raw_n} записей после слияния дублей), запасных ссылок {n_alt}")
        except Exception as e:
            self.error = str(e)
            log("не удалось собрать каталог:", e)
        finally:
            self.ready.set()


CATALOG = Catalog()


class Accounts:
    """Простая локальная регистрация для вкладки 18+: имя и пароль живут только в cache/users.json
    на этом компьютере (пароль — соль + PBKDF2, сам пароль нигде не хранится и никуда не уходит).
    Вход помнит cookie (год, продлевается при каждом заходе); в cache/sessions.json лежат только хеши токенов.
    Забыл пароль: при регистрации выдаётся код восстановления (хранится только его хеш), по нему
    пароль меняется без старого. Нет кода — удалить оба файла (reset18.bat); сервер сам заметит это без перезапуска."""

    CODE_ABC = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"

    def __init__(self):
        self.lock = threading.Lock()
        self.upath = os.path.join(CACHE, "users.json")
        self.spath = os.path.join(CACHE, "sessions.json")
        self.users, self.sessions, self.seen = {}, {}, None
        self._sync()

    @staticmethod
    def _load(p):
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}

    @staticmethod
    def _mt(p):
        try:
            return os.path.getmtime(p)
        except OSError:
            return None

    def _sync(self):
        """Файлы могли удалить/изменить снаружи (сброс аккаунта): перечитываем по времени изменения."""
        st = (self._mt(self.upath), self._mt(self.spath))
        if st != self.seen:
            self.users, self.sessions = self._load(self.upath), self._load(self.spath)
            self.seen = st

    def _save(self, p, data):
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, p)
        self.seen = (self._mt(self.upath), self._mt(self.spath))

    @staticmethod
    def _hash(pw, salt):
        return hashlib.pbkdf2_hmac("sha256", pw.encode("utf-8"), bytes.fromhex(salt), 200_000).hex()

    @staticmethod
    def _th(token):
        return hashlib.sha256(token.encode()).hexdigest()

    @staticmethod
    def _norm_code(code):
        return "".join(ch for ch in (code or "").upper() if ch.isalnum())

    def _new_session(self, key):
        token = secrets.token_urlsafe(32)
        self.sessions[self._th(token)] = key
        self._save(self.spath, self.sessions)
        return token

    def _new_code(self, u):
        """Новый код восстановления: показывается один раз, в файле только его хеш."""
        raw = "".join(secrets.choice(self.CODE_ABC) for _ in range(12))
        u["rsalt"] = secrets.token_hex(16)
        u["rhash"] = self._hash(raw, u["rsalt"])
        return "-".join(raw[i:i + 4] for i in (0, 4, 8))

    @staticmethod
    def _check_pw(pw):
        if len(pw or "") < 4 or len(pw) > 200:
            return "Пароль — не короче 4 символов"
        return ""

    def register(self, name, pw):
        """-> ((имя, токен, код восстановления), "") или (None, ошибка)"""
        name = (name or "").strip()
        if not 2 <= len(name) <= 30:
            return None, "Имя — от 2 до 30 символов"
        err = self._check_pw(pw)
        if err:
            return None, err
        key = name.casefold()
        with self.lock:
            self._sync()
            if key in self.users:
                return None, "Такое имя уже занято — нажмите «Войти»"
            salt = secrets.token_hex(16)
            u = {"name": name, "salt": salt, "hash": self._hash(pw, salt)}
            code = self._new_code(u)
            self.users[key] = u
            self._save(self.upath, self.users)
            return (name, self._new_session(key), code), ""

    def login(self, name, pw):
        key = (name or "").strip().casefold()
        with self.lock:
            self._sync()
            u = self.users.get(key)
            if u and hmac.compare_digest(u["hash"], self._hash(pw or "", u["salt"])):
                return (u["name"], self._new_session(key), None), ""
        time.sleep(0.7)  # притормаживаем подбор пароля
        return None, "Неверное имя или пароль"

    def reset(self, name, code, pw):
        """Забыл пароль: имя + код восстановления + новый пароль. Старые входы закрываются, выдаётся новый код."""
        key = (name or "").strip().casefold()
        err = self._check_pw(pw)
        if err:
            return None, err
        with self.lock:
            self._sync()
            u = self.users.get(key)
            if u and not u.get("rhash"):
                return None, "У этого аккаунта нет кода восстановления: запустите reset18.bat и зарегистрируйтесь заново"
            if u and hmac.compare_digest(u["rhash"], self._hash(self._norm_code(code), u["rsalt"])):
                u["salt"] = secrets.token_hex(16)
                u["hash"] = self._hash(pw, u["salt"])
                new_code = self._new_code(u)
                self.users[key] = u
                self._save(self.upath, self.users)
                self.sessions = {h: k for h, k in self.sessions.items() if k != key}
                return (u["name"], self._new_session(key), new_code), ""
        time.sleep(0.7)
        return None, "Неверное имя или код восстановления"

    def user(self, token):
        if not token:
            return None
        with self.lock:
            self._sync()
            u = self.users.get(self.sessions.get(self._th(token), ""))
            return u["name"] if u else None

    def logout(self, token):
        with self.lock:
            self._sync()
            if self.sessions.pop(self._th(token or ""), None) is not None:
                self._save(self.spath, self.sessions)


ACCOUNTS = Accounts()


# ---------------------------------------------------------- проверка ссылок

JPEG_MAGIC = bytes([0xFF, 0xD8])
PNG_MAGIC = bytes([0x89, 0x50, 0x4E, 0x47])


def image_is_fresh(resp, head):
    """Настоящий свежий снимок: JPEG/PNG не меньше 2 КБ (заглушки «нет сигнала» меньше)
    и не старше трёх суток по Last-Modified (у многих камер лежит снимок 2021 года)."""
    if not (head.startswith(JPEG_MAGIC) or head.startswith(PNG_MAGIC)):
        return False
    try:
        size = int(resp.headers.get("Content-Length") or 0)
    except ValueError:
        size = 0
    if len(head) < 2000 and size < 2000:
        return False
    lm = resp.headers.get("Last-Modified")
    if lm:
        try:
            age = time.time() - parsedate_to_datetime(lm).timestamp()
            if age > 3 * DAY:
                return False
        except Exception:
            pass
    return True


ADULT_URLS = set()  # ссылки каналов 18+ (заполняет Catalog.build)


def variants(it):
    """Основная ссылка и запасные: [{u, x, ua, r}, …]. Индекс 0 — основная."""
    return [it] + list(it.get("alt", ()))


def is_mpegts(head):
    """Начало сырого транспортного потока MPEG-TS: байт 0x47 каждые 188 байт (начало может быть со сдвигом)."""
    for i in range(188):
        if i + 376 < len(head) and head[i] == head[i + 188] == head[i + 376] == 0x47:
            return True
    return False


def ts_streams(buf):
    """Типы потоков из PMT: {pid: stream_type} или {} (H.264 = 0x1B, MPEG-2 = 0x02, AAC = 0x0F, MP2/MP3 = 0x03/0x04)."""
    o = next((i for i in range(188) if buf[i:i + 1] == b"G" and buf[i + 188:i + 189] == b"G"), None)
    if o is None:
        return {}
    pmt_pids = set()
    for i in range(o, len(buf) - 187, 188):
        p = buf[i:i + 188]
        pid, pusi = ((p[1] & 0x1F) << 8) | p[2], p[1] & 0x40
        off = 5 + p[4] if p[3] & 0x20 else 4
        if not pusi or off + 12 > 188:
            continue
        sec = p[off + 1:]
        try:
            if pid == 0:
                tl, j = ((sec[1] & 0xF) << 8) | sec[2], 8
                while j < 3 + tl - 4:
                    if (sec[j] << 8) | sec[j + 1]:
                        pmt_pids.add(((sec[j + 2] & 0x1F) << 8) | sec[j + 3])
                    j += 4
            elif pid in pmt_pids:
                tl, pil = ((sec[1] & 0xF) << 8) | sec[2], ((sec[10] & 0xF) << 8) | sec[11]
                j, out = 12 + pil, {}
                while j < 3 + tl - 4:
                    out[((sec[j + 1] & 0x1F) << 8) | sec[j + 2]] = sec[j]
                    j += 5 + (((sec[j + 3] & 0xF) << 8) | sec[j + 4])
                return out
        except IndexError:
            continue
    return {}


def ts_playable(resp, head):
    """Сырой MPEG-TS годится для mpegts.js, если видео H.264 (MPEG-2 браузер не показывает), звук AAC/MP2/MP3
    (или без звука) и скорость не ниже ~50 КБ/с — у медленных источников картинка бы стояла."""
    buf, t0 = bytearray(head), time.time()
    while len(buf) < 300_000 and time.time() - t0 < 5:
        chunk = resp.read(32768)
        if not chunk:
            break
        buf += chunk
    types = set(ts_streams(bytes(buf)).values())
    video = types & {0x1B, 0x02, 0x24}
    audio_ok = not (types & {0x03, 0x04, 0x0F, 0x11, 0x06, 0x81, 0x87}) or bool(types & {0x03, 0x04, 0x0F, 0x11})
    elapsed = max(time.time() - t0, 0.5)
    return video == {0x1B} and audio_ok and (len(buf) - len(head)) / elapsed >= 50_000


class Health:
    """Фоновая проверка: отвечает ли поток. Результат хранится сутки в кэше."""

    def __init__(self):
        self.path = os.path.join(CACHE, "health.json")
        self.lock = threading.Lock()
        self.state = {}  # url -> [ok(0/1), ts]
        self.done = 0
        self.total = 0
        self.running = False
        try:
            with open(self.path, encoding="utf-8") as f:
                self.state = json.load(f)
        except Exception:
            pass

    def save(self):
        with self.lock:
            data = json.dumps(self.state)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(data)
        os.replace(tmp, self.path)

    def mark(self, url, ok, kind=""):
        with self.lock:
            self.state[url] = [1 if ok else 0, int(time.time()), kind]

    def probe(self, it):
        headers = {"User-Agent": clean_ua(it.get("ua"))}
        if it.get("r"):
            headers["Referer"] = it["r"]
        ok, kind = False, ""
        # Источники с лимитом запросов (mcquack.net → 429) проверяем по очереди и повторяем отказ:
        # иначе живые ссылки помечаются мёртвыми.
        gate = host_gate(it["u"])
        with gate.sem:
            for attempt in range(4):
                gate.wait()
                ok, kind, retry = self._probe_once(it, headers)
                if ok or not retry:
                    break
                if retry == 429:
                    gate.interval = 2.0
                time.sleep(1.0)
        self.mark(it["u"], ok, kind)
        with self.lock:
            self.done += 1
            n = self.done
        if n % 1000 == 0:
            self.save()

    def _probe_once(self, it, headers):
        """(ok, kind, стоит ли повторить): повтор — только если источник ответил отказом 403/429/5xx."""
        ok, kind = False, ""
        try:
            req = urllib.request.Request(viewcams.resolve(it["u"]), headers=headers)
            with urllib.request.urlopen(req, timeout=8, context=SSL_LAX) as r:
                head = r.read(4096)
                if head[:2] == GZIP_MAGIC:
                    head = gunzip_partial(head)
                if it["x"] == "hls":
                    # заглушки при блокировке: cinerama.uz → /blocked/, Ростелеком (zabava) → rtk_block.m3u8
                    ok = b"#EXTM3U" in head and not BLOCK_RE.search(r.geturl())
                    if not ok and is_mpegts(head):  # прямой поток MPEG-TS без плейлиста
                        ok = ts_playable(r, head)
                        kind = "ts1" if ok else ""
                elif it["x"] == "jpeg":
                    ok = image_is_fresh(r, head)
                else:
                    ok = len(head) > 0
        except urllib.error.HTTPError as e:
            return False, "", e.code if e.code in (403, 429, 500, 502, 503) else 0
        except Exception:
            return False, "", False
        return ok, kind, False

    def run(self, items):
        now = time.time()
        todo, seen = [], set()
        for it in items:
            if it.get("sch"):  # камеры по расписанию (зоопарки): ночью выключены, не метим мёртвыми
                continue
            for v in variants(it):
                if v["x"] in ("yt", "link") or v["u"] in seen:
                    continue
                seen.add(v["u"])
                st = self.state.get(v["u"], [0, 0])
                # Старые записи «не отвечает» у ссылок без .m3u8 проверяем заново: они могли быть сырым MPEG-TS
                legacy = v["x"] == "hls" and not M3U8_RE.search(v["u"]) and (
                    (len(st) < 3 and not st[0]) or (len(st) > 2 and st[2] == "ts"))
                if now - st[1] > RECHECK_AFTER or legacy:
                    todo.append((it.get("cat") != "view", v))  # видовые камеры первыми
        todo.sort(key=lambda t: t[0])
        todo = [v for _, v in todo]
        self.total, self.done, self.running = len(todo), 0, bool(todo)
        if not todo:
            return
        log(f"проверяю {len(todo)} ссылок в фоне…")
        with ThreadPoolExecutor(max_workers=48) as pool:
            list(pool.map(self.probe, todo))
        self.save()
        self.running = False
        good = sum(1 for it in items if any(self.state.get(v["u"], [0])[0] for v in variants(it)))
        log(f"проверка закончена: живых {good} из {len(items)}")

    def summary(self, items, adult=False):
        """ok — есть рабочая ссылка; bad — проверены все, ни одна не ответила;
        pref — {id: номер варианта}, если основная ссылка не работает, а запасная да.
        Каналы 18+ попадают в ответ только при adult=True (после входа)."""
        with self.lock:
            ok, bad, pref, dv, ts = [], [], {}, [], []
            for it in items:
                if it.get("ad") and not adult:
                    continue
                sts = [self.state.get(v["u"]) for v in variants(it)]
                ts += ["%d:%d" % (it["id"], i) for i, st in enumerate(sts) if st and st[0] and len(st) > 2 and st[2] == "ts1"]
                if len(sts) > 1:  # какие запасные ссылки уже проверены и не отвечают
                    dv += ["%d:%d" % (it["id"], i) for i, st in enumerate(sts) if st and not st[0]]
                good = next((i for i, st in enumerate(sts) if st and st[0]), None)
                if good is not None:
                    ok.append(it["id"])
                    if good:
                        pref[it["id"]] = good
                elif all(sts):
                    bad.append(it["id"])
        return {"running": self.running, "done": self.done,
                "total": self.total, "ok": ok, "bad": bad, "pref": pref, "dv": dv, "ts": ts}


HEALTH = Health()


def startup():
    updater.start_background()  # сверка версии с GitHub — в фоне
    CATALOG.build()
    if CATALOG.items:
        HEALTH.run(CATALOG.items)


# ----------------------------------------------------------------- прокси

URI_ATTR_RE = re.compile(r'URI="([^"]+)"')


def clean_ua(ua):
    """В некоторых записях User-Agent приходит с хвостом '#EXTVLCOPT:http-user-agent='."""
    if ua and "=" in ua and ua.lstrip().startswith("#EXTVLCOPT"):
        ua = ua.split("=", 1)[1]
    return ua or UA


# ------------------------------------------------ соединения к источникам
# Раньше каждый запрос шёл через urllib с «Connection: close»: на каждый манифест
# и каждый сегмент — новое TCP+TLS-рукопожатие (0,3–1 с). Теперь держим
# keep-alive соединения по хостам.

class UpResp:
    def __init__(self, resp, conn, key, url):
        self.resp, self.conn, self.key, self.url = resp, conn, key, url
        self.status, self.headers = resp.status, resp.headers

    def read(self, n=-1):
        return self.resp.read(n)

    def release(self):
        """Полностью прочитанное соединение возвращаем в пул, иначе закрываем."""
        if self.resp.isclosed() and not self.resp.will_close:
            POOL.give(self.key, self.conn)
        else:
            self.conn.close()


class ConnPool:
    IDLE = 20   # секунд: дольше простаивающее соединение источник уже мог закрыть
    PER_HOST = 4

    def __init__(self):
        self.idle, self.lock = {}, threading.Lock()

    def take(self, key):
        now = time.time()
        with self.lock:
            lst = self.idle.get(key, [])
            while lst:
                conn, t = lst.pop()
                if now - t < self.IDLE:
                    return conn
                conn.close()
        return None

    def give(self, key, conn):
        with self.lock:
            lst = self.idle.setdefault(key, [])
            if len(lst) < self.PER_HOST:
                lst.append((conn, time.time()))
                return
        conn.close()


POOL = ConnPool()
PATH_SAFE = "/%:@!$&'()*+,;=~-._?[]"


def upstream_open(url, headers, timeout=15, redirects=5):
    """GET с keep-alive и ручными редиректами. Коды 4xx/5xx отдаём как есть."""
    for _ in range(redirects + 1):
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("bad url")
        key = (parts.scheme, parts.netloc)
        path = urllib.parse.quote((parts.path or "/") + ("?" + parts.query if parts.query else ""),
                                  safe=PATH_SAFE)
        resp = None
        for attempt in (0, 1):
            conn = POOL.take(key) if attempt == 0 else None
            reused = conn is not None
            if conn is None:
                if parts.scheme == "https":
                    conn = http.client.HTTPSConnection(parts.hostname, parts.port,
                                                       timeout=timeout, context=SSL_LAX)
                else:
                    conn = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout)
            try:
                conn.request("GET", path, headers=headers)
                resp = conn.getresponse()
                break
            except (http.client.HTTPException, OSError):
                conn.close()
                if not reused:   # пул отдал уже закрытое источником соединение — пробуем свежее
                    raise
        up = UpResp(resp, conn, key, url)
        loc = resp.getheader("Location")
        if resp.status in (301, 302, 303, 307, 308) and loc:
            try:
                resp.read()
            except Exception:
                pass
            up.release()
            url = urllib.parse.urljoin(url, loc)
            continue
        return up
    raise OSError("too many redirects")


class WarmCache:
    """Предзагруженные ответы (манифесты и первый сегмент). Выдаются один раз:
    живой плейлист не должен «застаиваться», а сегмент плееру нужен однократно."""
    TTL, MAX_BYTES = 25, 48 * 1024 * 1024

    def __init__(self):
        self.d, self.size, self.lock = collections.OrderedDict(), 0, threading.Lock()

    def put(self, key, val):
        with self.lock:
            old = self.d.pop(key, None)
            if old:
                self.size -= len(old[2])
            self.d[key] = (time.time(), val[0], val[1])   # (время, content-type, тело)
            self.size += len(val[1])
            while self.size > self.MAX_BYTES or len(self.d) > 60:
                _, (_, _, b) = self.d.popitem(last=False)
                self.size -= len(b)

    def pop(self, key):
        with self.lock:
            v = self.d.pop(key, None)
            if v:
                self.size -= len(v[2])
        if v and time.time() - v[0] < self.TTL:
            return v[1], v[2]
        return None


WARM = WarmCache()
WARM_SLOTS = threading.Semaphore(3)
WARMING = set()


def read_all(url, ua, ref, limit=8 * 1024 * 1024):
    headers = {"User-Agent": clean_ua(ua), "Accept": "*/*", "Accept-Encoding": "identity"}
    if ref:
        headers["Referer"] = ref
    up = upstream_open(url, headers, timeout=10)
    try:
        if up.status != 200:
            return None
        data = up.read(limit + 1)
        return (up.headers.get("Content-Type", "application/octet-stream"), data, up.url) \
            if len(data) <= limit else None
    finally:
        up.release()


def next_in_playlist(text, base):
    """Что плеер запросит следующим: самый лёгкий уровень (startLevel 0)
    или сегмент, с которого он начнёт (у лайва — третий с конца)."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    if any(l.startswith("#EXT-X-STREAM-INF") for l in lines):
        best, bw = None, None
        for i, l in enumerate(lines):
            if l.startswith("#EXT-X-STREAM-INF") and i + 1 < len(lines):
                m = re.search(r"BANDWIDTH=(\d+)", l)
                b = int(m.group(1)) if m else 0
                if bw is None or b < bw:
                    bw, best = b, lines[i + 1]
        return [urllib.parse.urljoin(base, best)] if best and not best.startswith("#") else None
    if any(l.startswith("#EXT-X-BYTERANGE") for l in lines):
        return None
    segs = [l for l in lines if not l.startswith("#")]
    if not segs:
        return None
    # Лайв: hls.js (проверено замером) берёт предпоследний или третий с конца сегмент, у VOD — первый.
    ids = [0] if "#EXT-X-ENDLIST" in text else sorted({max(0, len(segs) - 2), max(0, len(segs) - 3)})
    return [urllib.parse.urljoin(base, segs[i]) for i in ids]


def warm(url, ua, ref, adult=False):
    """Тянет вперёд цепочку манифест → уровень → сегмент и кладёт в WARM."""
    key0 = (url, ua or "", ref or "")
    if key0 in WARMING:
        return
    if not WARM_SLOTS.acquire(blocking=False):
        return
    WARMING.add(key0)
    try:
        urls = [viewcams.resolve(url)]
        for _ in range(3):
            nxt = []
            for url in urls:
                if not url.startswith(("http://", "https://")):
                    continue
                res = read_all(url, ua, ref)
                if not res:
                    continue
                ctype, data, final = res
                if data.lstrip().startswith(b"#EXTM3U") or "mpegurl" in ctype.lower():
                    text = data.decode("utf-8", "replace")
                    WARM.put((url, ua or "", ref or ""),
                             ("application/vnd.apple.mpegurl", rewrite_playlist(text, final, ua, ref, adult).encode("utf-8")))
                    nxt += next_in_playlist(text, final) or []
                else:
                    WARM.put((url, ua or "", ref or ""), (ctype, data))
            urls = nxt
            if not urls:
                return
    except Exception:
        pass
    finally:
        WARMING.discard(key0)
        WARM_SLOTS.release()


def proxify(url, ua, ref, adult=False):
    q = {"u": url}
    if ua:
        q["ua"] = ua
    if ref:
        q["r"] = ref
    if adult:  # сегменты и вложенные плейлисты канала 18+ тоже требуют входа
        q["a"] = "1"
    return "/p?" + urllib.parse.urlencode(q)


def rewrite_playlist(text, base, ua, ref, adult=False):
    out = []
    for line in text.splitlines():
        s = line.strip()
        if not s:
            out.append(line)
        elif s.startswith("#"):
            if 'URI="' in s:
                s = URI_ATTR_RE.sub(
                    lambda m: 'URI="%s"' % proxify(
                        urllib.parse.urljoin(base, m.group(1)), ua, ref, adult), s)
            out.append(s)
        else:
            out.append(proxify(urllib.parse.urljoin(base, s), ua, ref, adult))
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- сервер

STATIC = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/logo.png": ("logo.png", "image/png"),
    "/favicon.png": ("favicon.png", "image/png"),
    "/favicon.ico": ("app.ico", "image/x-icon"),
}


class Handler(BaseHTTPRequestHandler):
    server_version = "OknoVMir/0.1"

    def log_message(self, fmt, *args):  # не засоряем консоль запросами
        pass

    def send_bytes(self, code, data, ctype, extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)

    def send_json(self, obj, code=200):
        self.send_bytes(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def do_HEAD(self):
        self.do_GET()

    def cookie_val(self, name):
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == name:
                return v
        return ""

    def token(self):
        return self.cookie_val("okno_s")

    def session_user(self):
        return ACCOUNTS.user(self.token())

    def do_POST(self):
        path = urllib.parse.urlparse(self.path).path
        try:
            # защита от чужих сайтов: только с этой же страницы и только JSON
            host = self.headers.get("Host", "").split(":")[0]
            origin = self.headers.get("Origin", "")
            if host not in ("127.0.0.1", "localhost") or (origin and urllib.parse.urlparse(origin).hostname != host) \
                    or "application/json" not in self.headers.get("Content-Type", ""):
                return self.send_json({"error": "запрос отклонён"}, 403)
            n = int(self.headers.get("Content-Length") or 0)
            data = json.loads(self.rfile.read(min(n, 2048)) or b"{}")
            cookie = "okno_s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000"
            if path in ("/api/register", "/api/login", "/api/reset"):
                if path == "/api/register" and data.get("age") is not True:
                    return self.send_json({"error": "Нужно подтвердить, что вам есть 18 лет"}, 400)
                if path == "/api/reset":
                    res, err = ACCOUNTS.reset(str(data.get("name", "")), str(data.get("code", "")), str(data.get("password", "")))
                else:
                    fn = ACCOUNTS.register if path == "/api/register" else ACCOUNTS.login
                    res, err = fn(str(data.get("name", "")), str(data.get("password", "")))
                if not res:
                    return self.send_json({"error": err}, 400)
                out = {"user": res[0]}
                if res[2]:
                    out["code"] = res[2]  # код восстановления: показывается один раз
                body = json.dumps(out, ensure_ascii=False).encode("utf-8")
                return self.send_bytes(200, body, "application/json; charset=utf-8", {"Set-Cookie": cookie % res[1]})
            if path == "/api/update/apply":
                # скачать релиз, заменить файлы, перезапуститься (ответ уходит до перезапуска)
                ver, err = updater.apply()
                if err:
                    return self.send_json({"error": err}, 500)
                threading.Thread(target=restart_program, daemon=True).start()
                return self.send_json({"ok": True, "version": ver})
            if path == "/api/logout":
                ACCOUNTS.logout(self.token())
                return self.send_bytes(200, b'{"user":null}', "application/json; charset=utf-8",
                                       {"Set-Cookie": "okno_s=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"})
            return self.send_json({"error": "нет такого запроса"}, 404)
        except Exception:
            return self.send_json({"error": "некорректный запрос"}, 400)

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path, q = parsed.path, dict(urllib.parse.parse_qsl(parsed.query))
        try:
            if path in STATIC:
                name, ctype = STATIC[path]
                with open(os.path.join(ROOT, name), "rb") as f:
                    return self.send_bytes(200, f.read(), ctype)
            if path in ("/help", "/help.html"):
                with open(os.path.join(ROOT, "help.html"), "rb") as f:
                    return self.send_bytes(200, f.read(), "text/html; charset=utf-8")
            if path == "/hls.min.js":
                data = cached("hls.min.js", HLS_JS_URL, max_age=3650 * DAY)
                return self.send_bytes(200, data, "application/javascript")
            if path == "/mpegts.min.js":
                data = cached("mpegts.js", MPEGTS_JS_URL, max_age=3650 * DAY)
                return self.send_bytes(200, data, "application/javascript")
            if path == "/api/catalog":
                CATALOG.ready.wait(180)
                if not CATALOG.items:
                    return self.send_json({"error": CATALOG.error or "каталог ещё собирается"}, 503)
                body = CATALOG.body_gz if self.session_user() else CATALOG.body_gz_pub
                if "gzip" in self.headers.get("Accept-Encoding", ""):
                    return self.send_bytes(200, body, "application/json; charset=utf-8",
                                           {"Content-Encoding": "gzip"})
                return self.send_bytes(200, gzip.decompress(body),
                                       "application/json; charset=utf-8")
            if path == "/api/me":
                user = self.session_user()
                # продлеваем cookie: вход «запоминается» на год с последнего захода
                extra = {"Set-Cookie": "okno_s=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=31536000" % self.token()} if user else None
                return self.send_bytes(200, json.dumps({"user": user}, ensure_ascii=False).encode("utf-8"),
                                       "application/json; charset=utf-8", extra)
            if path == "/api/update":
                return self.send_json(updater.status())
            if path == "/api/health":
                return self.send_json(HEALTH.summary(CATALOG.items, bool(self.session_user())))
            if path in ("/p", "/warm"):
                # ссылки каналов 18+ (и всё, что от них отдано с a=1) — только после входа
                adult = q.get("u") in ADULT_URLS or q.get("a") == "1"
                if adult and not self.session_user():
                    return self.send_bytes(403, b"login required", "text/plain")
                if path == "/p":
                    return self.proxy(q, adult)
                if q.get("u"):  # «подогрев» канала, который скоро включат
                    threading.Thread(target=warm, args=(q["u"], q.get("ua"), q.get("r"), adult),
                                     daemon=True).start()
                return self.send_bytes(204, b"", "text/plain")
            self.send_bytes(404, b"not found", "text/plain")
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            pass

    def proxy(self, q, adult=False):
        url, ua, ref = q.get("u", ""), q.get("ua"), q.get("r")
        try:
            url = viewcams.resolve(url)
        except Exception:
            return self.send_bytes(502, b"cannot resolve", "text/plain")
        if not url.startswith(("http://", "https://")):
            return self.send_bytes(400, b"bad url", "text/plain")
        rng = self.headers.get("Range")
        if not rng:  # предзагруженный ответ (см. warm) — отдаём без обращения к источнику
            hit = WARM.pop((url, ua or "", ref or ""))
            if hit:
                return self.send_bytes(200, hit[1], hit[0])
        headers = {"User-Agent": clean_ua(ua), "Accept": "*/*", "Accept-Encoding": "identity"}
        if ref:
            headers["Referer"] = ref
        if rng:
            headers["Range"] = rng
        try:
            resp = upstream_open(url, headers)
        except Exception:
            return self.send_bytes(502, b"upstream unreachable", "text/plain")
        if resp.status >= 400:
            resp.release()
            return self.send_bytes(resp.status, b"upstream error", "text/plain")

        try:
            ctype = resp.headers.get("Content-Type", "application/octet-stream")
            first = resp.read(8192)
            if first[:2] == GZIP_MAGIC and ("mpegurl" in ctype.lower() or "text" in ctype.lower()):
                # некоторые источники (mcquack.net) отдают плейлист в gzip, даже когда просят identity
                first = gunzip_partial(first + resp.read(5 * 1024 * 1024))
            looks_playlist = (first.lstrip().startswith(b"#EXTM3U")
                              or "mpegurl" in ctype.lower())
            if looks_playlist:
                rest = resp.read(5 * 1024 * 1024)
                text = (first + rest).decode("utf-8", "replace")
                body = rewrite_playlist(text, resp.url, ua, ref, adult).encode("utf-8")
                return self.send_bytes(200, body, "application/vnd.apple.mpegurl")

            self.send_response(resp.status)
            self.send_header("Content-Type", ctype)
            for h in ("Content-Length", "Content-Range", "Accept-Ranges"):
                if resp.headers.get(h):
                    self.send_header(h, resp.headers[h])
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if self.command == "HEAD":
                return
            self.wfile.write(first)
            while True:  # MJPEG-камеры шлют бесконечный поток
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
        except (http.client.HTTPException, OSError) as e:
            if isinstance(e, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
                raise
        finally:
            resp.release()


RESTART = {"fn": None}  # launcher.py ставит сюда свой перезапуск (с закрытием значка в трее)


def restart_program():
    """Перезапуск после обновления: помощник дожидается нашего выхода и запускает программу заново."""
    time.sleep(0.7)
    fn = RESTART["fn"]
    if fn:
        return fn()
    if sys.platform == "win32":  # запуск из консоли: python server.py
        updater.spawn_relaunch(sys.executable, os.path.join(ROOT, "server.py"), hidden=False)
        os._exit(0)
    os.execv(sys.executable, [sys.executable] + sys.argv)


def main():
    threading.Thread(target=startup, daemon=True).start()
    srv = ThreadingHTTPServer((HOST, PORT), Handler)
    srv.daemon_threads = True
    url = f"http://{HOST}:{PORT}/"
    log("Окно в мир работает:", url, "(закрыть — Ctrl+C)")
    if "--no-browser" not in sys.argv and not updater.restart_flag_fresh():
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
