"""Видовые камеры (природа, города, порты, пляжи, горы) — не дорожные.

Источники (только то, что владельцы сами выложили в открытый доступ):
  * EarthCam — открытый JSON карты (без ключа), снимки JPEG;
  * Open-Eagle-Eye — реестр публичных камер, берём только видовые категории;
  * lukarne — ежедневный каталог круглосуточных YouTube-трансляций (по желанию в списке);
  * Windy Webcams и OpenWebcamDB — по API-ключу (WINDY_API_KEY / OPENWEBCAMDB_API_KEY
    или файл keys.json: {"windy": "...", "openwebcamdb": "..."}). Без ключа пропускаются.

Каждый источник собирается отдельно: упавший не ломает остальные.
Типы потоков (поле x): jpeg — снимок, который плеер обновляет раз в несколько
секунд; yt — YouTube-встройка; hls/mp4/mjpeg — как у остальных камер.
Поле cat: "view" — вид, "road" — дорога.
"""
import json
import os
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(ROOT, "cache")
DAY = 24 * 3600
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0 Safari/537.36")

EARTHCAM_URL = "https://www.earthcam.com/api/mapsearch/get_locations_network.php?a=fetch&r=ecn"
OEE_URL = "https://raw.githubusercontent.com/stuchapin909/Open-Eagle-Eye/master/cameras.json"
LUKARNE_URL = ("https://raw.githubusercontent.com/storagebits/lukarne/HEAD/"
               "app/src/main/assets/catalog.json")

# Категории Open-Eagle-Eye, которые считаем видами. highway/city/traffic там —
# дорожные камеры, airport/construction — не вид; их не берём.
OEE_VIEW = {"beach", "port", "ferry", "volcano", "wildlife", "aurora", "nature",
            "dam", "stadium"}
OEE_SKIP_HOSTS = {"images.wsdot.wa.gov"}  # паромные очереди и взлётки — по сути дороги
OEE_SITE = {
    "cdn.skylinewebcams.com": "SkylineWebcams", "camstills.cdn-surfline.com": "Surfline",
    "images.mybeachcams.com": "MyBeachCams", "images.pictimo.com": "Pictimo",
    "volcanoes.usgs.gov": "USGS", "usgs-nims-images.s3.amazonaws.com": "USGS",
    "www.nps.gov": "NPS", "storage.hdontap.com": "HDOnTap",
    "images.geonet.org.nz": "GeoNet", "www.yr.no": "Yr",
    "www.foto-webcam.eu": "foto-webcam.eu", "ccimg.bcferries.com": "BC Ferries",
    "www.bcferries.com": "BC Ferries", "files.explore.org": "explore.org",
}
COUNTRY_ALIAS = {
    "england": "GB", "wales": "GB", "scotland": "GB", "northern ireland": "GB",
    "united states": "US", "usa": "US", "virgin islands, u.s.": "VI",
    "virgin islands": "VI", "tanzania, united republic of": "TZ",
    "south korea": "KR", "russia": "RU", "czech republic": "CZ", "taiwan": "TW",
    "vietnam": "VN", "russian federation": "RU", "viet nam": "VN",
    "korea, republic of": "KR", "iran, islamic republic of": "IR",
    "moldova, republic of": "MD", "syrian arab republic": "SY", "bahamas": "BS", "puerto rico": "PR", "uk": "GB",
}


def _get(url, headers=None, timeout=60):
    h = {"User-Agent": UA}
    h.update(headers or {})
    with urllib.request.urlopen(urllib.request.Request(url, headers=h), timeout=timeout) as r:
        return r.read()


def _daily(name, producer, max_age=DAY):
    """JSON из кэша, если свежий; иначе producer(). Без сети — старая копия."""
    path = os.path.join(CACHE, name)
    if os.path.exists(path) and time.time() - os.path.getmtime(path) < max_age:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    try:
        data = producer()
    except Exception:
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        raise
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, path)
    return data


def load_keys():
    keys = {}
    try:
        with open(os.path.join(ROOT, "keys.json"), encoding="utf-8") as f:
            keys = json.load(f)
    except Exception:
        pass
    keys["windy"] = os.environ.get("WINDY_API_KEY") or keys.get("windy") or ""
    keys["openwebcamdb"] = (os.environ.get("OPENWEBCAMDB_API_KEY")
                            or keys.get("openwebcamdb") or "")
    return keys


def _cc(name, name_to_code):
    key = (name or "").strip()
    if not key:
        return ""
    if len(key) == 2 and key.isalpha():
        return key.upper()
    return name_to_code.get(key) or COUNTRY_ALIAS.get(key.lower(), "")


def _cam(name, code, code_to_name, url, x, g, attr, lat=None, lon=None, lc="", ri=0):
    it = {"k": "cam", "cat": "view", "n": name.strip() or "Камера", "c": code,
          "cn": code_to_name.get(code, code), "u": url, "x": x, "g": g, "a": attr,
          "lon": lon, "lat": lat}
    if lc:
        it["lc"] = lc
    if ri:
        it["ri"] = ri
    return it


def _float(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# ------------------------------------------------------------------ EarthCam

def earthcam(name_to_code, code_to_name):
    raw = _daily("earthcam.json", lambda: json.loads(_get(EARTHCAM_URL)))
    out = []
    for grp in raw.get("data") or []:
        for p in grp.get("places") or []:
            url = p.get("image")
            if not url:
                continue
            posn = p.get("posn") or [None, None]
            out.append(_cam(p.get("name") or "EarthCam",
                            _cc(p.get("country"), name_to_code), code_to_name,
                            url, "jpeg", "city", "EarthCam",
                            _float(posn[0]), _float(posn[1]),
                            p.get("city") or p.get("location") or "", ri=6))
    return out


# ------------------------------------------------------------ Open-Eagle-Eye

def open_eagle_eye(name_to_code, code_to_name):
    def produce():  # реестр весит 13 МБ, в кэш кладём только видовые камеры
        out = []
        for c in json.loads(_get(OEE_URL, timeout=120)):
            url = c.get("url") or ""
            ok_cat = (c.get("category") in OEE_VIEW
                      or (c.get("country") == "RU"  # российские берём шире: стройки, города
                          and c.get("category") not in ("highway", "traffic", "airport")))
            if (ok_cat and not c.get("auth") and c.get("verified") is not False
                    and url.startswith("http")
                    and urllib.parse.urlparse(url).netloc not in OEE_SKIP_HOSTS):
                out.append(c)
        return out

    out = []
    for c in _daily("oee_view.json", produce):
        url = c["url"]
        host = urllib.parse.urlparse(url).netloc
        co = c.get("coordinates") or {}
        if not (co.get("lat") or co.get("lng")):  # 0,0 у них значит «неизвестно»
            co = {}
        loc = c.get("location") or ""
        out.append(_cam(c.get("name") or "Камера",
                        _cc(c.get("country"), name_to_code), code_to_name,
                        url, "jpeg", c["category"],
                        OEE_SITE.get(host) or host.removeprefix("www."),
                        _float(co.get("lat")), _float(co.get("lng")),
                        "" if loc == c.get("name") else loc, ri=6))
    return out


# ------------------------------------------------------------------- YouTube

def lukarne_youtube(name_to_code, code_to_name):
    raw = _daily("lukarne.json", lambda: json.loads(_get(LUKARNE_URL, timeout=90)))
    out = []
    for c in raw.get("cams") or []:
        if c.get("kind") != "youtube" or not c.get("stream"):
            continue
        out.append(_cam(c.get("place") or "YouTube-камера",
                        (c.get("country") or "").upper(), code_to_name,
                        "https://www.youtube.com/watch?v=" + c["stream"], "yt", "live",
                        "YouTube", _float(c.get("lat")), _float(c.get("lon")),
                        c.get("locality") or ""))
    return out


# -------------------------------------------------------------------- Windy

WINDY_API = "https://api.windy.com/webcams/api/v3"
_windy_img = {}  # id -> (время, ссылка): бесплатные ссылки Windy живут недолго


def _windy(path, key):
    return json.loads(_get(WINDY_API + path, {"X-WINDY-API-KEY": key}, timeout=30))


def windy(key, name_to_code, code_to_name):
    def produce():
        cats = _windy("/categories?lang=en", key)
        ids = [c["id"] for c in cats if c.get("id") not in ("traffic", "indoor")]
        res, offset = [], 0
        while offset < 1000:
            data = _windy("/webcams?lang=en&limit=50&offset=%d&include=location,categories"
                          "&categories=%s" % (offset, ",".join(ids)), key)
            cams = data.get("webcams") or []
            if not cams:
                break
            res += cams
            offset += 50
        return res

    out = []
    for w in _daily("windy.json", produce):
        loc = w.get("location") or {}
        cats = w.get("categories") or [{}]
        out.append(_cam(w.get("title") or "Windy", (loc.get("country_code") or "").upper(),
                        code_to_name, "windy:%s" % w["webcamId"], "jpeg",
                        cats[0].get("id") or "landscape", "Windy Webcams",
                        _float(loc.get("latitude")), _float(loc.get("longitude")),
                        loc.get("city") or "", ri=60))
    return out


def resolve(url):
    """windy:ID → свежая ссылка на снимок. Остальные ссылки возвращаются как есть."""
    if not url.startswith("windy:"):
        return url
    wid = url[6:]
    hit = _windy_img.get(wid)
    if hit and time.time() - hit[0] < 240:
        return hit[1]
    key = load_keys()["windy"]
    if not key:
        raise RuntimeError("нет ключа Windy")
    cur = (_windy("/webcams/%s?include=images" % urllib.parse.quote(wid), key)
           .get("images") or {}).get("current") or {}
    link = cur.get("preview") or cur.get("thumbnail") or cur.get("icon")
    if not link:
        raise RuntimeError("у камеры нет снимка")
    _windy_img[wid] = (time.time(), link)
    return link


# -------------------------------------------------------------- OpenWebcamDB

def openwebcamdb(key, name_to_code, code_to_name):
    def produce():
        res, page = [], 1
        while page <= 20:
            data = json.loads(_get("https://openwebcamdb.com/api/v1/webcams?page=%d&per_page=100" % page,
                                   {"Authorization": "Bearer " + key}, timeout=30))
            rows = data.get("data") or []
            if not rows:
                break
            res += rows
            if page >= (data.get("total_pages") or 1):
                break
            page += 1
        return res

    out = []
    for w in _daily("openwebcamdb.json", produce):
        yt = w.get("youtube_url")
        img = w.get("image_url") or w.get("imageUrl")
        if not (yt or img):
            continue
        out.append(_cam(w.get("name") or w.get("title") or "OpenWebcamDB",
                        (w.get("country_code") or w.get("countryCode") or "").upper(),
                        code_to_name, yt or img, "yt" if yt else "jpeg",
                        (w.get("category") or "landscape") if not isinstance(w.get("category"), dict) else "landscape",
                        "OpenWebcamDB", _float(w.get("latitude", w.get("lat"))),
                        _float(w.get("longitude", w.get("lng"))),
                        w.get("city") or w.get("location") or "", ri=0 if yt else 30))
    return out


# --------------------------------------------------------------------- сборка

def build(name_to_code, code_to_name, log):
    keys = load_keys()
    sources = [("EarthCam", lambda: earthcam(name_to_code, code_to_name)),
               ("Open-Eagle-Eye", lambda: open_eagle_eye(name_to_code, code_to_name)),
               ("lukarne (YouTube)", lambda: lukarne_youtube(name_to_code, code_to_name))]
    if keys["windy"]:
        sources.append(("Windy", lambda: windy(keys["windy"], name_to_code, code_to_name)))
    if keys["openwebcamdb"]:
        sources.append(("OpenWebcamDB",
                        lambda: openwebcamdb(keys["openwebcamdb"], name_to_code, code_to_name)))
    seen, out = set(), []
    for title, fn in sources:
        try:
            got = [it for it in fn() if it["u"] not in seen]
            for it in got:
                it["s"] = [title]
        except Exception as e:
            log("видовые камеры:", title, "не загрузились:", e)
            continue
        seen.update(it["u"] for it in got)
        out += got
        log(f"видовые камеры: {title} — {len(got)}")
    return out
