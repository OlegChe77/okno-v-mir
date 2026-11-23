"""Источники ТВ-каналов и слияние каталога.

ТВ берём из нескольких открытых списков (у каждого свои пробелы и блокировки):
  * iptv-org: списки по странам, по языкам, по темам и «база потоков» (streams.json);
  * Free-TV/IPTV (каналы, которые вещатели сами отдают бесплатно);
  * русскоязычные списки с GitHub (RU_LISTS: Dimonovich, Karnei4 с группой «Для взрослых», LoganetX и её 18+);
  * свои списки из extra_sources.json (по желанию).

Всё сливается: одинаковые ссылки склеиваются, одинаковые названия (в одной стране)
становятся запасными вариантами одного канала (`alt`). У канала и у каждого
варианта помним, из каких списков он взят (`s`). Плеер и проверка ссылок
пробуют все варианты и показывают тот, что отвечает.

Каналы 18+ (is_nsfw / категория xxx) отдаются с полем ad:1 и темой «adult»; с обычными не склеиваются.
Поля элемента каталога: k, n, c, cn, u, x, [l, q, lb, ua, r, ad, lon, lat…],
s — списки-источники, t — темы, alt — запасные варианты [{u, x, s, [ua, r, q]}].
"""
import json
import os
import re
import urllib.parse

ROOT = os.path.dirname(os.path.abspath(__file__))

IPTV = "https://iptv-org.github.io"
SRC_COUNTRY = ("iptv-org: страны", IPTV + "/iptv/index.country.m3u")
SRC_LANG = ("iptv-org: языки", IPTV + "/iptv/index.language.m3u")
SRC_CAT = ("iptv-org: темы", IPTV + "/iptv/index.category.m3u")
SRC_BASE = "iptv-org: база потоков"
SRC_FREETV = ("Free-TV", "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlist.m3u8")
SRC_EXTRA = "свой список"

# Русскоязычные списки с GitHub. Поля: название, файл кэша, ссылка, страна по умолчанию,
# 18+, брать только эти группы (None — все). Страна из флага в группе («Италия 🇮🇹»),
# кроме групп с «VPN» (там флаг — страна VPN, а не канала).
GH = "https://raw.githubusercontent.com/"
RU_LISTS = [
    ("Dimonovich: ТВ", "ru_dim_tv.m3u", GH + "Dimonovich/TV/Dimonovich/FREE/TV", "RU", False, None),
    ("Dimonovich: сайты", "ru_dim_sites.m3u", GH + "Dimonovich/TV/Dimonovich/FREE/.m3u", "RU", False, None),
    ("Dimonovich: зарубежные", "ru_dim_zarub.m3u", GH + "Dimonovich/TV/Dimonovich/FREE/ZARUB", "", False, None),
    # Вторая ветка репозитория (Dimonovich-patch-1): старые версии тех же списков, в них много каналов, которых нет в основной (18.11.2026)
    ("Dimonovich: ТВ (patch-1)", "ru_dim_tv_p1.m3u", GH + "Dimonovich/TV/Dimonovich-patch-1/TV", "RU", False, None),
    ("Dimonovich: сайты (patch-1)", "ru_dim_sites_p1.m3u", GH + "Dimonovich/TV/Dimonovich-patch-1/.m3u", "RU", False, None),
    ("Dimonovich: зарубежные (patch-1)", "ru_dim_zarub_p1.m3u", GH + "Dimonovich/TV/Dimonovich-patch-1/ZARUB", "", False, None),
    ("Karnei4", "ru_karnei4.m3u", GH + "Karnei4/Karnei4.github.io/master/s/Mediacentr/Zabava-FREE-Karnei4.m3u",
     "RU", False, None),
    ("LoganetX", "ru_loganetx.m3u", GH + "blackbirdstudiorus/LoganetXIPTV/main/LoganetXAll.m3u", "RU", False, None),
    # ngrch/iptv: свежий список «живых» ссылок (много запасных вариантов; Узбекистан cinerama.uz — Discovery, Nat Geo, Animal Planet…)
    ("ngrch", "ru_ngrch.m3u", GH + "ngrch/iptv/HEAD/ru.m3u", "RU", False, None),
    # troypaha-netizen: Viasat/viju, познавательные (часть групп украинская — «| UKR» → UA)
    ("troypaha", "ru_troypaha.m3u", GH + "troypaha-netizen/my-iptv-playlist/HEAD/my-iptv.m3u", "RU", False, None),
    ("tv-rus", "ru_tvrus.m3u", GH + "tv-rus/tv-rus.github.io/HEAD/docs/playlists/all.m3u", "RU", False, None),
    ("LoganetX 18+", "ru_loganetx_18.m3u", GH + "blackbirdstudiorus/LoganetXIPTV/main/LoganetXStrawberry.m3u",
     "", True, {"Эротика"}),
    # loganettv/playlists (m3u.su/lgx): тот же автор, свежая версия списка 18+ (18.11.2026)
    ("LoganetX 18+ (strawberry)", "ru_loganetx_strawberry.m3u", GH + "loganettv/playlists/refs/heads/main/strawberry.m3u",
     "", True, {"Эротика"}),
    # Открытые списки 18+ (18.11.2026): официальный плейлист adultiptv.net (MyCamTV, AdultIPTV.net) и два GitHub-списка
    # без логинов/паролей в ссылках (Miami TV, Fashion TV Midnight Secrets, Jasmin, Pink'O, Redlight, Babestation…)
    ("AdultIPTV.net", "ru_adultiptv.m3u", "https://adultiptv.net/playlist.m3u8", "", True, None),
    ("THEMMMINAT XXX", "ru_themm_xxx.m3u", GH + "THEMMMINAT/xxx-IPTV/HEAD/XXX.m3u", "", True, None),
    ("Izanami1618 +18", "ru_izanami_18.m3u", GH + "Izanami1618/AdultChannels/HEAD/%2B18%20Channels", "", True, None),
]
CINERAMA_MIRRORS = ("stream1.cinerama.uz", "cache1.cinerama.uz", "stream2.cinerama.uz")
# Группы 18+ в любом из этих списков (Karnei4: «#EXTGRP:Для взрослых»): помечаем 18+, страну по умолчанию не ставим
RU_ADULT_GRP = re.compile(r"для взрослых|эроти|18\s*\+|xxx|adult", re.I)
# Служебные группы и ссылки этих списков (заставки, тесты, Ace Stream, ссылки на Telegram)
RU_SKIP_GRP = re.compile(r"INFO|TEST|Торрент", re.I)
RU_SKIP_URL = re.compile(r"//(127\.0\.0\.1|localhost|t\.me|loganettv\.vercel\.app)[:/]|194\.26\.229\.213/(zastavka|intro)/")
# Группы-темы этих списков
RU_GROUP_TOPIC = [
    ("movies", r"кино"), ("music", r"музык"), ("sports", r"спорт"), ("kids", r"дет"),
    ("news", r"новост|информац"), ("documentary", r"знания|познават"), ("entertainment", r"развлеч"),
    ("religious", r"религ"), ("relax", r"релакс|relax"), ("lifestyle", r"хобби"),
    ("documentary", r"документальн|пізнавальн|природа и животные"), ("travel", r"путешеств"),
    ("outdoor", r"охота и рыбалка"), ("series", r"серіал"), ("animation", r"мультфильм|мультфільм"),
    ("cooking", r"кулинар"),
]
RU_GROUP_TOPIC = [(t, re.compile(p, re.I)) for t, p in RU_GROUP_TOPIC]
UKR_GRP = re.compile(r"\|\s*UKR\s*$")
RU_GROUP_COUNTRY = {"Велком": "BY", "Turon Media (VPN)": "UZ"}


def flag_country(text):
    """Код страны из эмодзи-флага: 🇮🇹 → IT."""
    m = re.search("([\U0001F1E6-\U0001F1FF])([\U0001F1E6-\U0001F1FF])", text or "")
    return "".join(chr(ord(ch) - 0x1F1E6 + 65) for ch in m.groups()) if m else ""

STREAMS_URL = IPTV + "/api/streams.json"
CHANNELS_URL = IPTV + "/api/channels.json"

MAX_ALT = 14  # запасных вариантов на канал

ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
QUAL_RE = re.compile(r"\s*\((\d{3,4}p)\)")
LABEL_RE = re.compile(r"\s*\[([^\]]+)\]")
MARK_RE = re.compile(r"[ⓈⓉⒼⓎ]")
ID_CC_RE = re.compile(r"\.([a-z]{2})(?:@|_|$)")

# Названия стран в Free-TV, которых нет в списке стран iptv-org
GROUP_ALIAS = {"UK": "GB", "USA": "US", "Korea": "KR", "日本 / Japan": "JP",
               "Trinidad": "TT", "International": "INT"}
# Темы в Free-TV, у которых в группе стоит тема, а не страна
GROUP_TOPIC = {"news": "news", "business": "business"}

# Тема по ключевым словам в названии — только если у канала нет темы из списков
NAME_TOPICS = [
    ("news", r"news|новост|info|24|вести|euronews|cnn|bbc world|al jazeera|рбк|rt\b"),
    ("sports", r"sport|спорт|футбол|match|матч|football|golf|tennis|racing"),
    ("kids", r"kids|детск|дети|мульт|cartoon|baby|junior|nick"),
    ("music", r"music|музык|mtv|radio|радио|hits|fm\b"),
    ("movies", r"movie|кино|film|cinema|фильм|сериал"),
    ("religious", r"church|церк|islam|quran|god|христ|religio|bible|православ|спас"),
    ("weather", r"weather|погод|meteo"),
    ("science", r"science|наука|discovery|nature|nat geo|history|истор"),
]
NAME_TOPICS = [(t, re.compile(p, re.I)) for t, p in NAME_TOPICS]

# Для камер: подвид → тема. Дороги (traffic/highway) — одна тема «road».
CAM_TOPIC = {"traffic": "road", "highway": "road", "aurora": "mountain"}


def log_noop(*a):
    pass


# --------------------------------------------------------------- разбор m3u

def guess_type(url):
    path = urllib.parse.urlparse(url).path.lower()
    if path.endswith(".mp4"):
        return "mp4"
    if path.endswith(".mpd"):
        return "dash"
    return "hls"


def url_key(u):
    return re.sub(r"^https?://(www\.)?", "", u.strip().lower()).rstrip("/")


NAME_ALIAS = {  # один канал под разными названиями в разных списках
    "discoverychannel": "discovery", "natgeo": "nationalgeographic", "natgeowild": "nationalgeographicwild",
    "viasatexplore": "vijuexplore", "viasathistory": "vijuhistory", "viasatnature": "vijunature",
    "nanoнано": "nano", "нано": "nano", "idxtra": "investigationdiscovery",
}


def name_key(name):
    s = name.lower().replace("ё", "е")
    s = re.sub(r"\s*\(\d\)\s*$", " ", s)  # «Discovery (2)» — второй вариант того же канала
    s = re.sub(r"\([^\d)]*\)", " ", s)  # «Fox (Russia)» = «Fox»; «(+2)» и «(720p)» остаются
    s = re.sub(r"\b(hd|fhd|uhd|sd|4k|hq|lq)\b", " ", s)
    k = re.sub(r"[\W_]+", "", s)
    return NAME_ALIAS.get(k, k)


def id_country(tvg_id):
    m = ID_CC_RE.search(tvg_id or "")
    return m.group(1).upper() if m else ""


def id_base(tvg_id):
    return (tvg_id or "").split("@", 1)[0]


def parse_m3u(text):
    """Сырые записи списка: {n, u, x, id, grp, tc, l, q, lb, ua, r}."""
    out, attrs, name, ua, ref = [], None, "", None, None
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#EXTINF"):
            attrs = dict(ATTR_RE.findall(line))
            rest = ATTR_RE.sub("", line)
            name = rest.split(",", 1)[1].strip() if "," in rest else ""
            if '"' in name:  # битая запись: название после последней запятой
                name = name.rsplit(",", 1)[-1].strip()
            ua = ref = None
        elif line.startswith("#EXTVLCOPT:http-user-agent="):
            ua = line.split("=", 1)[1]
        elif line.startswith("#EXTVLCOPT:http-referrer="):
            ref = line.split("=", 1)[1]
        elif line.startswith("#EXTGRP:") and attrs is not None and not attrs.get("group-title"):
            attrs["group-title"] = line.split(":", 1)[1].strip()  # группа отдельной строкой (Karnei4)
        elif line and not line.startswith("#") and attrs is not None:
            host = urllib.parse.urlparse(line).netloc.lower()
            if (line.startswith(("http://", "https://")) and guess_type(line) != "dash"
                    and "youtube.com" not in host and "youtu.be" not in host):
                q = QUAL_RE.search(name)
                labels = LABEL_RE.findall(name)
                clean = MARK_RE.sub("", LABEL_RE.sub("", QUAL_RE.sub("", name))).strip()
                it = {"n": clean or name, "u": line, "x": guess_type(line),
                      "id": attrs.get("tvg-id", ""), "grp": attrs.get("group-title", ""),
                      "tc": (attrs.get("tvg-country") or "").split(";")[0].strip().upper()}
                if attrs.get("tvg-logo"):
                    it["l"] = attrs["tvg-logo"]
                if q:
                    it["q"] = q.group(1)
                if labels:
                    it["lb"] = labels
                if ua:
                    it["ua"] = ua
                if ref:
                    it["r"] = ref
                out.append(it)
            attrs = None
    return out


RU_DISPLAY = {"scifi": "Sci-Fi (Сайфай)"}  # русскоязычные каналы, которые ищут по русскому написанию


def make_tv(raw, src, country, code_to_name):
    name = raw["n"].lstrip(". ") or raw["n"]  # в списках бывает «.Sci-Fi», «.Black»
    if country == "RU":
        name = RU_DISPLAY.get(name_key(name), name)
    it = {"k": "tv", "n": name, "c": country,
          "cn": code_to_name.get(country) or ("Международные" if country == "INT" else raw.get("grp", "")),
          "u": raw["u"], "x": raw["x"], "s": [src]}
    for f in ("l", "q", "lb", "ua", "r"):
        if raw.get(f):
            it[f] = raw[f]
    return it


# ------------------------------------------------------------- загрузка ТВ

def load_tv(cached, name_to_code, code_to_name, log=log_noop):
    """Все ТВ-списки по порядку приоритета. Каждый — в своём try."""
    items = []
    topics_by_url, topics_by_id = {}, {}
    adult_urls = set()  # ссылки каналов 18+: отдаём с пометкой ad

    def text(name, url):
        return cached(name, url).decode("utf-8", "replace")

    # 1. По темам (сначала: из него берём темы для остальных списков)
    cat_raw = []
    try:
        cat_raw = parse_m3u(text("tv_category.m3u", SRC_CAT[1]))
        for r in cat_raw:
            g = r["grp"].strip().lower()
            if g == "xxx":
                adult_urls.add(url_key(r["u"]))
                continue
            if g in ("", "undefined"):
                continue
            topics_by_url.setdefault(url_key(r["u"]), set()).add(g)
            if r["id"]:
                topics_by_id.setdefault(id_base(r["id"]), set()).add(g)
    except Exception as e:
        log("ТВ: темы iptv-org не загрузились:", e)

    def topics_for(raw):
        t = set(topics_by_url.get(url_key(raw["u"]), ()))
        if not t and raw.get("id"):
            t = set(topics_by_id.get(id_base(raw["id"]), ()))
        return t

    def push(raw, src, country):
        it = make_tv(raw, src, country, code_to_name)
        if raw.get("_ad") or url_key(raw["u"]) in adult_urls:
            it["ad"] = 1
        t = topics_for(raw)
        if raw.get("_topic"):
            t.add(raw["_topic"])
        if t:
            it["t"] = sorted(t)
        items.append(it)

    # 2. Страны (основной список): страна — из названия группы
    try:
        n0 = len(items)
        for r in parse_m3u(text("tv.m3u", SRC_COUNTRY[1])):
            c = name_to_code.get(r["grp"]) or ("INT" if r["grp"] == "International" else "") \
                or id_country(r["id"])
            push(r, SRC_COUNTRY[0], c)
        log(f"ТВ: {SRC_COUNTRY[0]} — {len(items) - n0}")
    except Exception as e:
        log("ТВ:", SRC_COUNTRY[0], "не загрузился:", e)

    # 3. Языки: страна — по суффиксу tvg-id (например, «.ru»)
    try:
        n0 = len(items)
        for r in parse_m3u(text("tv_language.m3u", SRC_LANG[1])):
            push(r, SRC_LANG[0], id_country(r["id"]))
        log(f"ТВ: {SRC_LANG[0]} — {len(items) - n0}")
    except Exception as e:
        log("ТВ:", SRC_LANG[0], "не загрузился:", e)

    # 4. Темы как источник ссылок
    try:
        n0 = len(items)
        for r in cat_raw:
            push(r, SRC_CAT[0], id_country(r["id"]))
        log(f"ТВ: {SRC_CAT[0]} — {len(items) - n0}")
    except Exception as e:
        log("ТВ:", SRC_CAT[0], "не загрузился:", e)

    # 5. База потоков iptv-org: в ней есть и те ссылки, что выпали из готовых списков
    try:
        n0 = len(items)
        streams = json.loads(cached("iptv_streams.json", STREAMS_URL))
        chans = json.loads(cached("iptv_channels.json", CHANNELS_URL))
        meta = {c["id"]: c for c in chans}
        for s in streams:
            ch = meta.get(s.get("channel"))
            u = s.get("url") or ""
            if (not ch or ch.get("closed")
                    or not u.startswith(("http://", "https://")) or guess_type(u) == "dash"):
                continue
            host = urllib.parse.urlparse(u).netloc.lower()
            if "youtube.com" in host or "youtu.be" in host:
                continue
            raw = {"n": ch["name"], "u": u, "x": guess_type(u), "id": ch["id"], "grp": ""}
            if s.get("quality"):
                raw["q"] = s["quality"]
            if s.get("labels"):
                raw["lb"] = s["labels"]
            if s.get("user_agent"):
                raw["ua"] = s["user_agent"]
            if s.get("referrer"):
                raw["r"] = s["referrer"]
            it = make_tv(raw, SRC_BASE, (ch.get("country") or "").upper(), code_to_name)
            cats = [c for c in (ch.get("categories") or []) if c != "undefined"]
            # is_nsfw в базе потоков ошибочно стоит у обычных каналов (Atlantic TV, Retroclub, Tele Posadas): не учитываем
            if "xxx" in cats:
                it["ad"] = 1
                adult_urls.add(url_key(u))
                cats = [c for c in cats if c != "xxx"]
            if cats:
                it["t"] = sorted(cats)
            items.append(it)
        log(f"ТВ: {SRC_BASE} — {len(items) - n0}")
    except Exception as e:
        log("ТВ:", SRC_BASE, "не загрузилась:", e)

    # 6. Free-TV/IPTV
    try:
        n0 = len(items)
        for r in parse_m3u(text("tv_freetv.m3u", SRC_FREETV[1])):
            grp = r["grp"]
            if grp.upper().startswith("VOD"):
                continue
            c = r["tc"] or id_country(r["id"]) or name_to_code.get(grp) or GROUP_ALIAS.get(grp, "")
            r["grp"] = code_to_name.get(c, grp) if c and c != "INT" else grp
            topic = GROUP_TOPIC.get(re.sub(r"\s*\(.*\)", "", grp).strip().lower())
            if topic:
                r["_topic"] = topic
                c = r["tc"] or id_country(r["id"])
            push(r, SRC_FREETV[0], c)
        log(f"ТВ: {SRC_FREETV[0]} — {len(items) - n0}")
    except Exception as e:
        log("ТВ:", SRC_FREETV[0], "не загрузился:", e)

    # 7. Русскоязычные списки с GitHub (российские каналы, 18+)
    for name, fname, url, country, adult, groups in RU_LISTS:
        try:
            n0 = len(items)
            for r in parse_m3u(text(fname, url)):
                grp = r["grp"].strip()
                if (groups is not None and grp not in groups) or RU_SKIP_GRP.search(grp) \
                        or RU_SKIP_URL.search(r["u"]) or r["n"].startswith("Обновление"):
                    continue
                r["n"] = re.sub(r"^[,\s]+|\s*\|\s*HD\s*$", "", r["n"]) or r["n"]  # «,VENUS - USA (+18) | HD» → «VENUS - USA (+18)»
                ad = adult or bool(RU_ADULT_GRP.search(grp))
                c = RU_GROUP_COUNTRY.get(grp) or ("UA" if UKR_GRP.search(grp) else "") or ("" if "VPN" in grp else flag_country(grp)) \
                    or r["tc"] or id_country(r["id"]) or ("" if ad else country)
                if ad:
                    r["_ad"] = True
                    adult_urls.add(url_key(r["u"]))
                else:
                    for t, rx in RU_GROUP_TOPIC:
                        if rx.search(grp):
                            r["_topic"] = t
                            break
                if c and c in code_to_name:
                    r["grp"] = code_to_name[c]
                push(r, name, c)
            log(f"ТВ: {name} — {len(items) - n0}")
        except Exception as e:
            log("ТВ:", name, "не загрузился:", e)

    # 8. Свои списки
    try:
        extra = load_extra()
        for lst in extra.get("m3u", []):
            n0 = len(items)
            body = cached("extra_" + re.sub(r"\W+", "_", lst["name"]) + ".m3u", lst["url"]).decode("utf-8", "replace")
            for r in parse_m3u(body):
                if lst.get("adult"):
                    r["_ad"] = True
                c = (lst.get("country") or "").upper() or r["tc"] or id_country(r["id"]) \
                    or name_to_code.get(r["grp"], "")
                push(r, lst["name"], c)
            log(f"ТВ: {lst['name']} — {len(items) - n0}")
    except Exception as e:
        log("ТВ: свои списки:", e)

    # Зеркала cinerama.uz: списки дают только stream8 (его блокирует /blocked/), а stream1/cache1/stream2
    # отдают те же номера каналов. Зеркала идут перед оригиналом: первым пробуется рабочий.
    mirrored = []
    for it in items:
        if "//stream8.cinerama.uz/" in it["u"]:
            for h in CINERAMA_MIRRORS:
                m = {k: (list(v) if isinstance(v, list) else v) for k, v in it.items()}
                m["u"] = it["u"].replace("stream8.cinerama.uz", h)
                mirrored.append(m)
        mirrored.append(it)
    items[:] = mirrored

    # 18+: ссылка помечена в любом списке — помечаем везде, тема «18+»
    for it in items:
        if url_key(it["u"]) in adult_urls:
            it["ad"] = 1
        if it.get("ad"):
            it["t"] = ["adult"]
    # темы по названию — тем, у кого их нет
    for it in items:
        if not it.get("t"):
            for t, rx in NAME_TOPICS:
                if rx.search(it["n"]):
                    it["t"] = [t]
                    break
    return items


def load_extra():
    """extra_sources.json: {"m3u": [{"name": "…", "url": "…", "country": "RU", "adult": true}], "cams": [{...}]}."""
    try:
        with open(os.path.join(ROOT, "extra_sources.json"), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


# Списки камер в формате m3u: (название, файл кэша, ссылка). Группа = «Страна 🇷🇺», название — камера.
_WC = GH + "Belfagor2005/WorldCam/main/usr/lib/enigma2/python/Plugins/Extensions/WorldCam/Playlists/"
CAM_LISTS = [
    ("Dimonovich: камеры", "cam_dim.m3u", GH + "Dimonovich/TV/Dimonovich/FREE/CAMERA"),
    ("mirostep: webcams", "cam_mirostep.m3u", GH + "mirostep/webcams/main/webcams.m3u"),
    # WorldCam (Enigma2): строки «название###ссылка», без групп; страна — по названию
    ("WorldCam: веб-камеры мира", "cam_wc_world.txt", _WC + "HasBahCa_WEBCAMS.txt"),
    ("WorldCam: Россия", "cam_wc_rus.txt", _WC + "Userlist-RUS.txt"),
    ("WorldCam: Италия", "cam_wc_italy.txt", _WC + "Italy_46_cam.txt"),
    ("WorldCam: США", "cam_wc_usa.txt", _WC + "USA_54_cam.txt"),
]
# страна по умолчанию у списков без групп и без страны в названии
_CAM_DEFAULT = {"WorldCam: Россия": "RU", "WorldCam: Италия": "IT", "WorldCam: США": "US"}
_CAM_NAME_CC = {"Польша": "PL", "Люксембург": "LU", "Стрый": "UA", "Istanbul": "TR", "Praia": "CV",
                 "Czech": "CZ", "Brazil": "BR", "Germany": "DE", "Poland": "PL", "Slovakia": "SK"}


def _hash_to_m3u(text):
    """«название###ссылка» → m3u (WorldCam)."""
    out = ["#EXTM3U"]
    for line in text.splitlines():
        if "###" in line:
            n, u = line.split("###", 1)
            out += ["#EXTINF:-1," + n.strip(), u.strip()]
    return "\n".join(out)


def _cam_name_country(name, name_to_code):
    """Страна из названия: «Country - город», «Country: город», известные города, кириллица = Россия."""
    n = re.sub(r"^RUS Webcam\s+", "", name)
    m = re.search(r"\(([A-Z]{2})\)\s*$", n)  # «Hamburg (DE)»
    if m:
        return m.group(1)
    parts = [re.sub(r"\s*\d+$", "", p).strip() for p in re.split(r"\s+[-–]\s+|:|_", n)]
    first = parts[0]
    for p in parts:  # «Czech - Ratibor», «Jelsa - Croatia»
        if p in name_to_code:
            return name_to_code[p]
        if p in _CAM_NAME_CC:
            return _CAM_NAME_CC[p]
    w = first.split(" ")[0]
    if w in _CAM_NAME_CC:
        return _CAM_NAME_CC[w]
    return "RU" if re.search("[А-Яа-я]", first) else ""
_CAM_G = [("beach", r"пляж|beach|surf|(?<!\w)море|coast"), ("port", r"(?<!\w)порт(?!\w)|(?<!\w)port(?!\w)|гавань|harbou?r"),
          ("mountain", r"гор[аыу]|горн|mountain|ski|лыж|volcan|вулкан"), ("road", r"трасс|дорог|перекр[её]с|шоссе|road|traffic|(?<!\w)мост|bridge")]
_CAM_G = [(g, re.compile(p, re.I)) for g, p in _CAM_G]


def load_m3u_cams(cached, name_to_code, code_to_name, log=log_noop):
    out = []
    for name, fname, url in CAM_LISTS:
        try:
            n0 = len(out)
            body = cached(fname, url).decode("utf-8", "replace")
            if fname.endswith(".txt"):
                body = _hash_to_m3u(body)
            for r in parse_m3u(body):
                grp = r["grp"].strip()
                if RU_SKIP_GRP.search(grp) or RU_SKIP_URL.search(r["u"]) or not r["u"].startswith("http"):
                    continue
                x = guess_type(r["u"])
                if x == "dash":
                    continue
                code = flag_country(grp) or name_to_code.get(re.sub(r"\s*[🇦-🇿]{2}\s*$", "", grp), "") \
                    or _cam_name_country(r["n"], name_to_code) or _CAM_DEFAULT.get(name, "")
                g = next((g for g, rx in _CAM_G if rx.search(r["n"])), "city")
                cam = {"k": "cam", "cat": "road" if g == "road" else "view", "n": r["n"], "c": code,
                       "cn": code_to_name.get(code, ""), "u": r["u"], "x": x, "g": g, "a": name,
                       "lat": None, "lon": None, "lc": "", "ri": 0, "s": [name]}
                for f in ("ua", "r"):
                    if r.get(f):
                        cam[f] = r[f]
                out.append(cam)
            log(f"камеры: {name} — {len(out) - n0}")
        except Exception as e:
            log("камеры:", name, "не загрузились:", e)
    return out


def load_extra_cams(name_to_code, code_to_name):
    out = []
    for c in load_extra().get("cams", []):
        if not c.get("u") or not c.get("n"):
            continue
        code = (c.get("c") or "").upper()
        out.append({"k": "cam", "cat": c.get("cat") or "view", "n": c["n"], "c": code,
                    "cn": code_to_name.get(code, code), "u": c["u"],
                    "x": c.get("x") or guess_type(c["u"]), "g": c.get("g") or "",
                    "a": c.get("a") or "", "lat": c.get("lat"), "lon": c.get("lon"),
                    "lc": c.get("lc") or "", "ri": c.get("ri") or 0, "s": [SRC_EXTRA]})
    return out


# ------------------------------------------------------------------- слияние

def _variant(it):
    v = {"u": it["u"], "x": it["x"], "s": list(it.get("s") or [])}
    for f in ("ua", "r", "q"):
        if it.get(f):
            v[f] = it[f]
    return v


def _add_sources(dst, src):
    for s in src:
        if s not in dst:
            dst.append(s)


def _absorb(target, it):
    """Ссылки и сведения it добавляем в target. Если ссылка уже есть — только источники."""
    known = {target["u"]: target["s"]}
    for a in target.get("alt", []):
        known[a["u"]] = a["s"]
    for v in [_variant(it)] + list(it.get("alt", [])):
        if v["u"] in known:
            _add_sources(known[v["u"]], v["s"])
        elif len(target.setdefault("alt", [])) < MAX_ALT:
            target["alt"].append(v)
            known[v["u"]] = v["s"]
    if it.get("ad"):
        target["ad"] = 1
        target["t"] = ["adult"]
    for f in ("l", "q", "lb", "lat", "lon", "lc"):
        if it.get(f) and not target.get(f):
            target[f] = it[f]
    if it.get("t") and not target.get("ad"):
        target["t"] = sorted(set(target.get("t", [])) | set(it["t"]))
    if not target.get("alt"):
        target.pop("alt", None)


def _cam_key(it):
    if it.get("lat") is None or it.get("lon") is None:
        return None
    try:
        return ("cam", it.get("c", ""), name_key(it["n"]),
                round(float(it["lat"]), 2), round(float(it["lon"]), 2))
    except (TypeError, ValueError):
        return None


# «Познавательное»: каналы серии Discovery/Fox/TLC и т.п. на русском. Тема `know`, `kr` — порядок в подборке.
KNOW_COUNTRIES = {"RU", "BY", "KZ", "UA", "UZ", "MD", "KG", "AM", "AZ", "GE"}
KNOW_BRANDS = [re.compile(p, re.I) for p in (
    r"discovery(?!.*(kids|\bee\b))|дискавери",
    r"nat(ional)?\.? ?geo",
    r"animal planet|анимал",
    r"\bhistory\b|^истори|viasat history|viju history",
    r"(viasat|viju\+?) ?(explore|nature|planet)",
    r"\btlc\b",
    r"travel ?(channel|\+ ?adventure|xp|mix)|^travel\b|путешеств|моя планета|глазами туриста",
    r"^fox( life| crime)?( \(.*\))?$",
    r"^nano|нано|da vinci|да винчи|^наука|живая планета|живая природа|неизвестная планета|тайны галактики|"
    r"первый космический|love nature|зоопарк|в мире животных|^дикий|^дикая|охот|рыбал|ocean|cbs reality|техно 24|точка отрыва",
)]
KNOW_RU_LIKE = {"UZ", "BY", "KZ", "MD", "KG", "AM", "AZ", "GE"}  # их ленты с русским звуком склеиваем с российским каналом
KNOW_TOPICS = {"documentary", "science", "travel", "outdoor", "education"}


def know_rank(it):
    """Порядковый номер бренда для подборки «Познавательное» или None."""
    if it.get("k") != "tv" or it.get("ad") or it.get("c") not in KNOW_COUNTRIES:
        return None
    for i, rx in enumerate(KNOW_BRANDS):
        if rx.search(it["n"]):
            return i
    if KNOW_TOPICS & set(it.get("t") or ()):
        return len(KNOW_BRANDS)
    return None


def cam_topic(it):
    g = it.get("g") or ""
    if it.get("cat") == "road":
        return CAM_TOPIC.get(g, g) if g in ("landmark", "lake") else "road"
    return CAM_TOPIC.get(g, g) or "landscape"


def merge(items):
    """Склеивает дубли (по ссылке и по названию), расставляет id и темы камер."""
    out, by_url, by_name = [], {}, {}
    for it in items:
        key_u = url_key(it["u"])
        hit = by_url.get(key_u)
        if hit is None and it["k"] == "tv" and name_key(it["n"]):
            hit = by_name.get(("tv", it.get("c", ""), name_key(it["n"]), bool(it.get("ad"))))
        elif hit is None:
            ck = _cam_key(it)
            hit = by_name.get(ck) if ck else None
        if hit is not None:
            _absorb(hit, it)
            by_url[key_u] = hit
            for a in it.get("alt", []):
                by_url.setdefault(url_key(a["u"]), hit)
            continue
        out.append(it)
        by_url[key_u] = it
        for a in it.get("alt", []):
            by_url.setdefault(url_key(a["u"]), it)
        if it["k"] == "tv":
            if name_key(it["n"]):
                by_name[("tv", it.get("c", ""), name_key(it["n"]), bool(it.get("ad")))] = it
        else:
            ck = _cam_key(it)
            if ck:
                by_name[ck] = it

    # Канал без страны, у которого есть однозначный «тёзка» со страной, — вариант этого тёзки
    by_nk = {}
    for it in out:
        if it["k"] == "tv" and it.get("c") and name_key(it["n"]):
            by_nk.setdefault((name_key(it["n"]), bool(it.get("ad"))), []).append(it)
    final = []
    for it in out:
        if it["k"] == "tv" and not it.get("c") and name_key(it["n"]):
            cand = by_nk.get((name_key(it["n"]), bool(it.get("ad"))), [])
            if len(cand) == 1:
                _absorb(cand[0], it)
                continue
        final.append(it)

    # Русскоязычные ленты других стран (UZ, BY, KZ…) с тем же названием, что у российского познавательного канала, — его варианты
    ru_by = {name_key(it["n"]): it for it in final
             if it["k"] == "tv" and it.get("c") == "RU" and not it.get("ad") and name_key(it["n"])}
    kept = []
    for it in final:
        if it["k"] == "tv" and it.get("c") in KNOW_RU_LIKE and not it.get("ad") and name_key(it["n"]) in ru_by \
                and know_rank(dict(it, c="RU")) is not None:
            _absorb(ru_by[name_key(it["n"])], it)
            continue
        kept.append(it)
    final = kept

    for i, it in enumerate(final):
        it["id"] = i
        if it["k"] == "cam":
            it["t"] = [cam_topic(it)]
        elif not it.get("t"):
            it.pop("t", None)
        kr = know_rank(it)
        if kr is not None:
            it["t"] = [t for t in it.get("t", []) if t != "know"] + ["know"]
            it["kr"] = kr
    return final
