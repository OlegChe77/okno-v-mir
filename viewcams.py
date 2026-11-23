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
Поле cat: "view" — вид, "road" — дорога, "animal" — животные (зоопарки, заповедники), "feeder" —
кормушки, гнёзда и дворы, "under" — подводный мир, "volcano" — вулканы и гейзеры, "sky" — небо
(сияние, звёзды, шторма), "space" — космос (МКС, запуски, обсерватории); в разделах только видео, снимков нет; "explore" — ссылки на сайт explore.org (x:"link": не поток, а
адрес страницы; правила сайта запрещают ретрансляцию, поэтому плеер только открывает сайт).
Поле sch:1 — эфир по расписанию (зоопарки): фоновая проверка такие камеры пропускает, плеер не
запоминает их мёртвыми на сутки (иначе ночная неудача спрятала бы камеру на весь день).
Адрес вида rutube:ID разворачивается в свежую подписанную ссылку HLS через API при запуске (resolve).
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
        it = _cam(c.get("name") or "Камера",
                  _cc(c.get("country"), name_to_code), code_to_name,
                  url, "jpeg", c["category"],
                  OEE_SITE.get(host) or host.removeprefix("www."),
                  _float(co.get("lat")), _float(co.get("lng")),
                  "" if loc == c.get("name") else loc, ri=6)
        out.append(it)
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


# ------------------------------------------------------------------ животные

SI_NAME = "Smithsonian's National Zoo"
# (название, путь на сервере Wowza). Обе площадки (wowza01 и зеркало wowza02) отдают
# одинаковое; сегмент 1080p весит 1,4–2,9 МБ, поэтому плеер стартует с лёгкого уровня.
SI_CAMS = [
    ("Панды: камера 1", "live_edge_panda25/smil:panda125_01.smil"),
    ("Панды: камера 2", "live_edge_panda25/smil:panda125_02.smil"),
    ("Львы", "live_edge_lion/smil:lion01_all.smil"),
    ("Слониха Зикси", "live_edge_elephant_zixi/elephant_zixi.smil"),
    ("Гепарды: камера 1", "live_edge_cheetah_03/cheetah_y14_01_1080_all.smil"),
    ("Гепарды: камера 2", "live_edge_cheetah_04/cheetah_y14_02_1080_all.smil"),
    ("Голые землекопы: камера 1", "live_edge_nmr/nmr_1080_all.smil"),
    ("Голые землекопы: камера 2", "live_edge_nmr_02/nmr_02_1080_all.smil"),
]
RUTUBE_ZOO = [  # эфиры Калининградского зоопарка на Rutube
    ("трубкозуб Теренс", "99e5a191615a398b5fd3c1d6020c07f1"),
    ("выдры", "e00423314d16d4696640a39963cc8be3"),
    ("тигры Тигран и Лея", "d5ce56186f86c6349c0b10f11b4eaa40"),
]
# explore.org: открытый список потоков есть, но правила сайта запрещают ретрансляцию → только ссылки
# на страницы сайта (проверены: раздел существует; сами потоки сюда не берём).
EXPLORE_LINKS = [
    ("Белоголовые орланы (Channel Islands, Sauces)", "https://explore.org/livecams/bald-eagles", "US"),
    ("Белые медведи (Tundra Buggy)", "https://explore.org/livecams/polar-bears", "CA"),
    ("Котята-сироты (FeLV Room)", "https://explore.org/livecams/kitten-rescue", "US"),
    ("Пингвины (Tova)", "https://explore.org/livecams/penguins", "US"),
]


def _animal(name, code, code_to_name, url, x, g, attr, lc="", lat=None, lon=None):
    it = _cam(name, code, code_to_name, url, x, g, attr, lat, lon, lc)
    it["cat"] = "animal"
    it["sch"] = 1  # эфир не круглосуточный (ночью зоопарк выключает камеры): проверка живости не помечает мёртвой
    return it


def smithsonian(name_to_code, code_to_name):
    out = []
    for n, path in SI_CAMS:
        it = _animal("Нац. зоопарк США: " + n, "US", code_to_name,
                     "https://nzp-wowza01.si.edu/%s/playlist.m3u8" % path, "hls", "zoo", SI_NAME,
                     "Вашингтон", 38.9296, -77.0498)
        it["alt"] = [{"u": "https://nzp-wowza02.si.edu/%s/playlist.m3u8" % path, "x": "hls", "s": [SI_NAME]}]
        out.append(it)
    return out


def kaliningrad_zoo(name_to_code, code_to_name):
    return [_animal("Калининградский зоопарк: " + n, "RU", code_to_name, "rutube:" + i, "hls", "zoo",
                    "Rutube", "Калининград") for n, i in RUTUBE_ZOO]


def explore_links(name_to_code, code_to_name):
    out = []
    for n, url, cc in EXPLORE_LINKS:
        it = _animal(n, cc, code_to_name, url, "link", "wildlife", "explore.org")
        it["cat"] = "explore"
        it.pop("sch", None)
        out.append(it)
    return out


# ------------------------------------------------------------------- разделы «Тип камер» (только видео)
# Трансляции YouTube по разделам, найдены поиском «в эфире» 23.11.2026 и проверены на isLiveNow и
# playableInEmbed. (раздел cat, подвид g, название, страна, id видео, место, источник).
# Это круглосуточные эфиры организаций; если трансляцию перезапустят, id сменится и камера замолчит.
YT_CAMS = [
    ("feeder", "nest", "Белоголовые орланы, гнездо (Big Bear), камера 1", "US", "B4-L2nfGcuE", "Биг-Бэр, Калифорния", "FOBBV"),
    ("feeder", "nest", "Белоголовые орланы, гнездо (Big Bear), общий план", "US", "41eq4VzCYc4", "Биг-Бэр, Калифорния", "FOBBV"),
    ("feeder", "nest", "Белоголовые орланы, гнездо (NEFL), камера 1", "US", "UANvTm4J18M", "Флорида", "American Eagle Foundation"),
    ("feeder", "nest", "Белоголовые орланы, гнездо (NEFL), камера 2", "US", "JfI_WNnE8fo", "Флорида", "American Eagle Foundation"),
    ("feeder", "nest", "Орланы Декора, северное гнездо", "US", "GGIE1E-kaMQ", "Айова", "explore.org"),
    ("feeder", "nest", "Орланы Сауцес (Channel Islands)", "US", "rnTsOesC6hE", "Калифорния", "explore.org"),
    ("feeder", "nest", "Орланы, остров Каталина (западный конец)", "US", "aT1adgYSfbc", "Калифорния", "explore.org"),
    ("feeder", "nest", "Орланы Winter Park, верхняя камера", "US", "ADIMaoywNuw", "Флорида", "Winter Park Eagle Cam"),
    ("feeder", "nest", "Орланы Trempealeau", "US", "8bMrSm0Ap20", "Висконсин", "Trempealeau Eagles"),
    ("feeder", "nest", "Скопы Hellgate (Монтана)", "US", "J7HO0dkkomE", "Монтана", "Cornell Lab"),
    ("feeder", "nest", "Скопы Manton Bay (Рутленд)", "GB", "V08ET209nvU", "Рутленд", "Rutland Osprey Project"),
    ("feeder", "nest", "Скопы Glaslyn (Уэльс)", "GB", "8PHA8Ti4ids", "Уэльс", "Glaslyn Wildlife"),
    ("feeder", "nest", "Скопы Порт-Линкольн", "AU", "xW1YcHVp7Ko", "Порт-Линкольн", "Port Lincoln Osprey"),
    ("feeder", "nest", "Скопы, Ойстер-Бэй", "US", "ZzW9-t5bYJE", "Нью-Йорк", "PSEG Long Island"),
    ("feeder", "nest", "Сапсаны, ящик (FalconCam)", "AU", "gnF7_gRHHMQ", "Мельбурн", "FalconCam Project"),
    ("feeder", "nest", "Гнездо аистов, Бад-Зальцунген", "DE", "Dr5zebXpO-M", "Бад-Зальцунген", "Stadtverwaltung Bad Salzungen"),
    ("feeder", "nest", "Гнездо аистов, Кирхцартен", "DE", "W2CicgFq5oo", "Кирхцартен", "regiowebcam"),
    ("feeder", "nest", "Орлан-белохвост (Sea-EagleCAM4)", "", "LWYBNX02Q-A", "", "Sea-EagleCAM"),
    ("feeder", "nest", "Королевский альбатрос, колония", "NZ", "Mm_zVDDUeNA", "Данидин", "Cornell Lab / DOC NZ"),
    ("feeder", "nest", "Роющие совы, гнездо (Орегон)", "US", "ZGAf77sGw6Q", "Орегон", "explore.org"),
    ("feeder", "feeder", "Кормушка в Сэпсакер-Вудс (Cornell FeederWatch)", "US", "x10vL6_47Dw", "Итака, Нью-Йорк", "Cornell Lab"),
    ("feeder", "feeder", "Кормушка для птиц, Геттисберг", "US", "y9t1g8Ike6g", "Пенсильвания", "Gettysburg LiveCam"),
    ("feeder", "feeder", "Кормушка для птиц, Нью-Джерси", "US", "efsZblNShVo", "Нью-Джерси", "Holy Mount Athos Voices"),
    ("feeder", "feeder", "Кормушка и белки, Огайо", "US", "b6-zNf3BGy0", "Огайо", "Bird Watching HQ"),
    ("feeder", "feeder", "Кормушка с фруктами, Панама", "PA", "WtoxxHADnGk", "Панама", "Cornell Lab"),
    ("feeder", "feeder", "Колибри, Панама", "PA", "0xFARCdZ3vA", "Панама", "Cornell Lab"),
    ("feeder", "feeder", "Колибри, Сими-Вэлли", "US", "pXe8MpU7uzk", "Калифорния", "Hummingbird Spot"),
    ("feeder", "feeder", "Кормушка и водопой, Тихоокеанский пролётный путь", "US", "1aPhtRjjzb0", "Сакраменто", "explore.org"),
    ("feeder", "feeder", "Кормушка с птицами, Южная Африка", "ZA", "JtRWAKUmd2U", "Южная Африка", "Bird Watching HQ"),
    ("feeder", "feeder", "Кормушка в африканском буше", "ZA", "hi8xV_EhY4k", "Южная Африка", "Allen Birdcam"),
    ("feeder", "feeder", "Птицы и рыжие белки (Red Squirrel Studios)", "", "tm3fMnPT6d8", "", "Red Squirrel Studios"),
    ("feeder", "feeder", "Дикая природа в лесу (Nature Live Camera)", "", "F0GOOP82094", "", "Nature Live Camera"),
    ("feeder", "farm", "Приют кошек Friends of Felines", "US", "iNhKMpI9Hak", "", "Friends of Felines"),
    ("feeder", "farm", "Приют кошек Friends of Felines, Kabana", "US", "6Aq-E-6juTE", "", "Friends of Felines"),
    ("feeder", "farm", "Приют кошек Furball Farm", "US", "JmrzmmUD_es", "", "Furball Farm Cat Sanctuary"),
    ("feeder", "farm", "Котята TinyKittens, Crow's Nest", "CA", "7jxSXfdK0Jg", "", "TinyKittens"),
    ("feeder", "farm", "Kitten Academy", "US", "lSGLULlTZHk", "", "Kitten Academy"),
    ("feeder", "farm", "Спасённые котята (explore.org)", "US", "OOT6YlOYgaU", "", "explore.org"),
    ("feeder", "farm", "Козы на ферме Syman Says", "US", "xNw-aQ3RwQI", "Коннектикут", "Syman Says Farms"),
    ("feeder", "farm", "Овцы на ферме-приюте (Farm Sanctuary)", "US", "SnHke968zAA", "", "explore.org"),
    ("feeder", "farm", "Курятник Hungry Hens", "US", "c2SXDkaNOU4", "", "How Joe Did It"),
    ("animal", "safari", "Африка, лагерь ol Donyo (Кения)", "KE", "XsOU8JnEpNM", "Кения", "Africam"),
    ("animal", "safari", "Африка, 24/7 сафари (Africam)", "", "qpukdDslCjk", "", "Africam"),
    ("animal", "safari", "Водопой в пустыне Намиб", "NA", "ydYDqZQpim8", "Намибия", "NamibiaCam"),
    ("animal", "zoo", "Волки, Международный центр волка", "US", "5e4lsEe4Vew", "Миннесота", "explore.org"),
    ("animal", "zoo", "Аллигаторы, ферма в Сент-Огастине", "US", "LHtzZf4T7xw", "Флорида", "See St. Augustine"),
    ("under", "aquarium", "Морские выдры и котики, Сиэтлский аквариум", "US", "NqOmHpwMUxs", "Сиэтл", "Seattle Aquarium"),
    ("under", "aquarium", "Медузы, Монтерейский аквариум", "US", "eQ_foBERmzA", "Монтерей", "Monterey Bay Aquarium"),
    ("under", "aquarium", "Акулы, Монтерейский аквариум", "US", "tEtg5Kg3voQ", "Монтерей", "Monterey Bay Aquarium"),
    ("under", "aquarium", "Открытое море, Монтерейский аквариум", "US", "n_GpVsz4nHU", "Монтерей", "Monterey Bay Aquarium"),
    ("under", "aquarium", "Морские выдры, аквариум Тоба", "JP", "YonXVFE9C3g", "Тоба", "Toba Aquarium"),
    ("under", "aquarium", "Морские выдры, Ванкуверский аквариум", "CA", "9mg9PoFEX2U", "Ванкувер", "Vancouver Aquarium"),
    ("under", "aquarium", "Морские выдры 2, Ванкуверский аквариум", "CA", "_KXHUb0wFRE", "Ванкувер", "Vancouver Aquarium"),
    ("under", "aquarium", "Пингвины, Moody Gardens", "US", "HHp4rjhJsWI", "Галвестон", "Moody Gardens"),
    ("under", "aquarium", "Карибский аквариум, Moody Gardens", "US", "IQH9o1FVz48", "Галвестон", "Moody Gardens"),
    ("under", "aquarium", "Медузы, Аквариум Тихого океана", "US", "IYG9fnz40-E", "Лонг-Бич", "explore.org"),
    ("under", "aquarium", "Голубая пещера, Аквариум Тихого океана", "US", "H59B9Uoewwg", "Лонг-Бич", "explore.org"),
    ("under", "aquarium", "Гавайские тюлени-монахи, аквариум Вайкики", "US", "DFO1bS546Z0", "Гонолулу", "Waikiki Aquarium"),
    ("under", "aquarium", "Коралловый аквариум Curaçao Sea Aquarium", "CW", "P9gFih_9T6k", "Кюрасао", "SHOWME Caribbean"),
    ("under", "reef", "Тропический риф (explore.org)", "", "DHUnz4dyb54", "", "explore.org"),
    ("under", "reef", "Риф Utopia Village: стена", "HN", "1zcIUk66HX4", "Роатан", "explore.org"),
    ("under", "reef", "Риф Utopia Village: песчаный канал", "HN", "jzx_n25g3kA", "Роатан", "explore.org"),
    ("under", "reef", "Риф Utopia Village: причал", "HN", "Kf-x20Yq0_A", "Роатан", "explore.org"),
    ("under", "reef", "Коралловый город, Майами (Coral Morphologic)", "US", "7i8ARjIeM2k", "Майами", "Coral Morphologic"),
    ("under", "reef", "Подводная камера Wahoo Bay", "", "oBTGY6Ax870", "", "Wahoo Bay"),
    ("under", "sea", "Косатки, наблюдательный пост OrcaLab", "CA", "hTOmWcmr2Tc", "Британская Колумбия", "explore.org"),
    ("under", "sea", "Морские львы, Британская Колумбия", "CA", "-uooI2satIQ", "Британская Колумбия", "explore.org"),
    ("under", "sea", "Киты и побережье, Патагония", "AR", "BlpneEUfK-0", "Патагония", "explore.org"),
    ("under", "sea", "Белухи, устье реки Кенай", "US", "w7BLcV9ksFQ", "Аляска", "Alaska Wildlife Alliance"),
    ("under", "sea", "Белухи, причал на Кенае", "US", "tNerDaJKjLA", "Аляска", "Alaska Wildlife Alliance"),
    ("under", "manatee", "Ламантины под водой, Хомосасса", "US", "Fz6sl9YJZE0", "Флорида", "explore.org"),
    ("under", "manatee", "Ламантины под водой, Хомосасса (Adopt a Manatee)", "US", "YGD4JppXDD8", "Флорида", "Save the Manatee"),
    ("under", "manatee", "Ламантины, Силвер-Спрингс", "US", "9k-KU8Xzei0", "Флорида", "Save the Manatee"),
    ("under", "manatee", "Ламантины под водой, Блю-Спринг", "US", "MchNXlFpYIM", "Флорида", "explore.org"),
    ("animal", "zoo", "Медведи у водопада Брукс (Катмай, Аляска)", "US", "J7ZrIDvqlic", "Аляска", "explore.org"),
    ("animal", "zoo", "Волки, логово Zeab (Колорадо)", "US", "CYcOoLIU8wA", "Колорадо", "explore.org"),
    ("animal", "zoo", "Бобры, хатка (National Trust)", "GB", "2mMGOAjWTnc", "Холникот", "National Trust"),
    ("animal", "zoo", "Выдры, зоопарк Цинциннати", "US", "-VfIE3__PR0", "Цинциннати", "Cincinnati Zoo"),
    ("animal", "zoo", "Выдры, музей Калверт", "US", "3wLJoSa5KBE", "Мэриленд", "Calvert Marine Museum"),
    ("volcano", "volcano", "Килауэа: прямой эфир с камер USGS", "US", "RKJgisxzBTs", "Гавайи", "Lava Watchers"),
    ("volcano", "volcano", "Вулканы мира: Этна, Килауэа и другие (Volcoholics)", "", "eGWaJYQgYIE", "", "Volcoholics"),
    ("under", "aquarium", "Пингвины, пляж Аквариума Тихого океана", "US", "GSxpCbXsvtI", "Лонг-Бич", "explore.org"),
    ("animal", "zoo", "Пингвины Магеллана, остров Това (Патагония)", "AR", "mrJ4JF2MovU", "Патагония", "explore.org"),
    ("animal", "zoo", "Тупики, остров Скомер", "GB", "Cj4bM-OoW9g", "Скомер, Уэльс", "Wildlife Trust"),
    ("animal", "zoo", "Тупики, остров Скомер: берег", "GB", "1pFTuCQGVZ0", "Скомер, Уэльс", "Wildlife Trust"),
    ("animal", "zoo", "Бурые медведи, Катмай: Riffles", "US", "z7_GhJeFxQI", "Аляска", "explore.org"),
    ("animal", "zoo", "Бурые медведи, Катмай: River Watch", "US", "wkVLYfU-Kew", "Аляска", "explore.org"),
    ("animal", "zoo", "Белые медведи, Tundra Buggy Lodge", "CA", "3aA44Sy_l2o", "Черчилл", "explore.org"),
    ("animal", "zoo", "Белые медведи, Buggy One", "CA", "8MoaBHl8U3A", "Черчилл", "explore.org"),
    ("animal", "safari", "Водопой Окаукуэджо, Этоша (Намибия)", "NA", "JMMoRwYo5kE", "Этоша", "Namibia Wildlife Resorts"),
    ("animal", "safari", "Водопой Окаукуэджо, Этоша (NamibiaCam)", "NA", "AeMUdOPFcXI", "Этоша", "NamibiaCam"),
    ("animal", "safari", "Слоны, парк Тембе (ЮАР)", "ZA", "gdrNUUf-cQw", "Тембе", "Africam"),
    ("animal", "safari", "Сафари, Масаи-Мара (Кения)", "KE", "xXZqU5vnEug", "Масаи-Мара", "Bushtops Safaris"),
    ("animal", "safari", "Река Мара: переправа антилоп гну", "KE", "ACc7IkdOF-Y", "Масаи-Мара", "Africam"),
    ("animal", "safari", "Водопой Линкваша, Хванге (Зимбабве)", "ZW", "-rXriX4SiQk", "Хванге", "Africam"),
    ("animal", "safari", "Водопой в Амбосели с горой Килиманджаро", "KE", "XyPU5-pNg5E", "Амбосели", "Africam"),
    ("animal", "safari", "Водопой Camelthorn, Хванге (Зимбабве)", "ZW", "mk02pEy3FdA", "Хванге", "Africam"),
    ("sky", "aurora", "Северное сияние, курорт Апукка (Рованиеми)", "FI", "wT8Zhh9MBEE", "Рованиеми", "Apukka Resort"),
    ("sky", "aurora", "Северное сияние, Леви (Финляндия)", "FI", "rKfecmmzzw0", "Леви", "Starlapland"),
    ("sky", "aurora", "Северное сияние, Соданкюля (Финляндия)", "FI", "dnlQtDad6Dk", "Соданкюля", "Starlapland"),
    ("sky", "aurora", "Северное сияние, Оулу (Финляндия)", "FI", "tugnxHwOdrQ", "Оулу", "Samimaka"),
    ("sky", "aurora", "Северное сияние, Эмблтон (Англия)", "GB", "JZ38uyr-J6o", "Нортумберленд", "Northumberland Astronomical"),
    ("sky", "stars", "Метеоры и ночное небо, залив Будл (Англия)", "GB", "1E9GeddwfgY", "Нортумберленд", "Northumberland Astronomical"),
    ("sky", "stars", "Звёздное небо над Мауна-Кеа (Гавайи)", "US", "qk4HJUDBGJw", "Мауна-Кеа", "Higashiyama's Labo"),
    ("sky", "stars", "Метеоры, обсерватория Кисо (Япония)", "JP", "_HZDjlfTuvQ", "Кисо", "Asahi Astro LIVE"),
    ("volcano", "volcano", "Килауэа: Халемаумау, западная часть (USGS V1cam)", "US", "HggWKlZv9yk", "Гавайи", "USGS"),
    ("volcano", "volcano", "Килауэа: Халемаумау, южная часть (USGS V3cam)", "US", "gXKuUyKt8mc", "Гавайи", "USGS"),
    ("volcano", "volcano", "Килауэа: прямой эфир (afarTV)", "US", "6IaMqotNF_s", "Гавайи", "afarTV"),
    ("volcano", "volcano", "Килауэа: несколько камер (afarTV)", "US", "FVdmnpJ2kM0", "Гавайи", "afarTV"),
    ("volcano", "volcano", "Вулкан Майон (Филиппины)", "PH", "UDAZWxehMAI", "Албай", "afarTV"),
    ("volcano", "volcano", "Вулкан Майон: мониторинг", "PH", "wLf3IMoxEWA", "Албай", "MayonStream Lab"),
    ("volcano", "volcano", "Вулкан Этна (Сицилия)", "IT", "l7dshNvAvpw", "Сицилия", "afarTV"),
    ("volcano", "volcano", "Вулкан Везувий (Неаполь)", "IT", "RbI8JwrBZQA", "Неаполь", "Paesaggi Digitali"),
    ("space", "iss", "Земля и космос в 4K: прямой эфир с МКС (Sen)", "", "fO9e9jnhYK8", "", "Sen"),
    ("space", "iss", "Земля из космоса: NASA Live (МКС, afarTV)", "US", "tj4knR4r1UU", "", "afarTV"),
    ("space", "iss", "МКС: вид на Землю в высоком разрешении (NASA)", "US", "ROboUHfPUrU", "", "NASA"),
    ("space", "iss", "МКС: прямая трансляция с борта (NASA)", "US", "M3HKLzjvKPc", "", "NASA"),
    ("space", "iss", "МКС: обзорная камера, вид Земли (afarTV)", "", "OKQEMp2555A", "", "afarTV"),
    ("space", "iss", "МКС: северное сияние со станции", "", "I3sa2kiEpkA", "", "Dream Trips"),
    ("space", "launch", "Starbase: стенд Starship и Super Heavy 24/7", "US", "mhJRzQsLZGg", "Старбейс, Техас", "NASASpaceflight"),
    ("space", "launch", "Космический берег: Кеннеди и Канаверал 24/7", "US", "Jm8wRjD3xVA", "Флорида", "NASASpaceflight"),
    ("space", "launch", "Макгрегор: испытания двигателей SpaceX", "US", "cOmmvhDQ2HM", "Техас", "NASASpaceflight"),
    ("space", "launch", "Стартовые площадки Falcon и Starship (Spaceflight Now)", "US", "thfYPsRqxmw", "Флорида", "Spaceflight Now"),
    ("space", "stars", "Обсерватория ALMA и звёздное небо (Атакама)", "CL", "BVKRAzTSnB0", "Атакама", "Asahi Astro LIVE"),
    ("space", "stars", "Мауна-Кеа: телескоп Субару, восточный вид", "US", "OVcVOJYXUsc", "Гавайи", "Asahi Astro LIVE"),
    ("sky", "aurora", "Северное сияние, Фэрбенкс (Аляска)", "US", "O52zDyxg5QI", "Фэрбенкс", "explore.org"),
    ("sky", "aurora", "Северное сияние, фьорд Нуук (Гренландия)", "GL", "wN5qtfbWd0Y", "Нуук", "Malik Hansen"),
]


def youtube_sections(name_to_code, code_to_name):
    out = []
    for cat, g, name, cc, vid, lc, attr in YT_CAMS:
        it = _cam(name, cc, code_to_name, "https://www.youtube.com/watch?v=" + vid, "yt", g,
                  "YouTube · " + attr, None, None, lc)
        it["cat"] = cat
        out.append(it)
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


_rutube = {}  # id -> (время, ссылка): адрес подписан, берём заново при запуске, но не на каждый запрос
RUTUBE_API = "https://rutube.ru/api/play/options/%s/?no_404=true&referer=https%%3A%%2F%%2Frutube.ru"


def resolve(url):
    """windy:ID → свежая ссылка на снимок, rutube:ID → свежая подписанная ссылка HLS. Остальное как есть."""
    if url.startswith("rutube:"):
        rid = url[7:]
        hit = _rutube.get(rid)
        if hit and time.time() - hit[0] < 240:
            return hit[1]
        opts = json.loads(_get(RUTUBE_API % urllib.parse.quote(rid), timeout=15))
        hls = ((opts.get("live_streams") or {}).get("hls") or [{}])[0].get("url")
        if not hls:  # эфира сейчас нет (video_balancer пуст)
            raise RuntimeError("у трансляции Rutube нет потока")
        _rutube[rid] = (time.time(), hls)
        return hls
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
               ("lukarne (YouTube)", lambda: lukarne_youtube(name_to_code, code_to_name)),
               (SI_NAME, lambda: smithsonian(name_to_code, code_to_name)),
               ("Калининградский зоопарк (Rutube)", lambda: kaliningrad_zoo(name_to_code, code_to_name)),
               ("explore.org (ссылки на сайт)", lambda: explore_links(name_to_code, code_to_name)),
               ("YouTube: гнёзда, кормушки, космос, природа", lambda: youtube_sections(name_to_code, code_to_name))]
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
