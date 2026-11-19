"""Рисование значков «Окна в мир» (PIL + numpy). Все координаты — доли от размера холста (0..1)."""
import math
import numpy as np
from PIL import Image, ImageDraw, ImageFilter

S = 2048                      # холст; потом уменьшаем (суперсэмплинг)
BG0, BG1 = (22, 34, 58), (9, 13, 22)
ORANGE = [(0.0, (255, 214, 130)), (0.5, (255, 181, 71)), (1.0, (255, 122, 89))]
RED = (255, 59, 78)
LINE = (255, 200, 120)


def P(v):
    return v * S


def grad(stops, direction="diag", size=S):
    """Градиент по списку (позиция, цвет); direction: diag | v."""
    y, x = np.mgrid[0:size, 0:size].astype(np.float32) / (size - 1)
    t = (x + y) / 2 if direction == "diag" else y
    out = np.zeros((size, size, 3), np.float32)
    pos = [p for p, _ in stops]
    for ch in range(3):
        out[..., ch] = np.interp(t, pos, [c[ch] for _, c in stops])
    return Image.fromarray(out.astype(np.uint8), "RGB")


def new_mask():
    return Image.new("L", (S, S), 0)


def paste_grad(img, mask, stops, direction="diag"):
    img.paste(grad(stops, direction), (0, 0), mask)


def circle_mask(cx, cy, r):
    m = new_mask()
    ImageDraw.Draw(m).ellipse([P(cx - r), P(cy - r), P(cx + r), P(cy + r)], fill=255)
    return m


def tile():
    """Тёмная скруглённая плитка — основа большинства значков."""
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    m = new_mask()
    ImageDraw.Draw(m).rounded_rectangle([P(.04), P(.04), P(.96), P(.96)], radius=P(.22), fill=255)
    paste_grad(img, m, [(0, BG0), (1, BG1)], "v")
    ring = new_mask()
    d = ImageDraw.Draw(ring)
    d.rounded_rectangle([P(.04), P(.04), P(.96), P(.96)], radius=P(.22), outline=255, width=int(P(.008)))
    img.paste(Image.new("RGB", (S, S), (50, 66, 100)), (0, 0), ring)
    return img


def globe_lines(layer_draw, cx, cy, r, color, lw, fill=None):
    """Сетка глобуса: экватор, параллели, меридианы. Рисуется на переданном ImageDraw."""
    w = int(P(lw))
    for k in (0.42, 0.8):                                             # меридианы
        layer_draw.ellipse([P(cx - r * k), P(cy - r), P(cx + r * k), P(cy + r)], outline=color, width=w)
    layer_draw.line([P(cx), P(cy - r), P(cx), P(cy + r)], fill=color, width=w)
    layer_draw.line([P(cx - r), P(cy), P(cx + r), P(cy)], fill=color, width=w)  # экватор
    for off in (-0.5, 0.5):                                           # параллели
        dy = r * off
        dx = math.sqrt(r * r - dy * dy)
        layer_draw.line([P(cx - dx), P(cy + dy), P(cx + dx), P(cy + dy)], fill=color, width=w)


def lines_in_circle(img, cx, cy, r, color, lw):
    lay = new_mask()
    globe_lines(ImageDraw.Draw(lay), cx, cy, r, 255, lw)
    lay = Image.composite(lay, new_mask(), circle_mask(cx, cy, r))
    img.paste(Image.new("RGB", (S, S), color), (0, 0), lay)


def glow(img, mask, color, radius, alpha=0.5):
    g = mask.filter(ImageFilter.GaussianBlur(P(radius)))
    g = g.point(lambda v: int(v * alpha))
    img.paste(Image.new("RGB", (S, S), color), (0, 0), g)


def ring_mask(cx, cy, r, w):
    m = new_mask()
    ImageDraw.Draw(m).ellipse([P(cx - r), P(cy - r), P(cx + r), P(cy + r)], outline=255, width=int(P(w)))
    return m


def play_triangle(d, cx, cy, h, fill):
    """Треугольник «пуск», центр тяжести — (cx, cy)."""
    w = h * 0.87
    d.polygon([(P(cx - w / 3), P(cy - h / 2)), (P(cx - w / 3), P(cy + h / 2)), (P(cx + 2 * w / 3), P(cy))], fill=fill)


def live_dot(img, cx, cy, r):
    m = circle_mask(cx, cy, r)
    glow(img, m, RED, r * 1.1, 0.8)
    img.paste(Image.new("RGB", (S, S), RED), (0, 0), m)
    inner = circle_mask(cx, cy, r * 0.42)
    img.paste(Image.new("RGB", (S, S), (255, 214, 214)), (0, 0), inner)


def finish(img, size):
    return img.resize((size, size), Image.LANCZOS)


# ---------- варианты ----------

def v1():
    """Глобус в круглом окне: оранжевое кольцо, внутри синий шар с сеткой."""
    img = tile()
    cx = cy = 0.5
    glow(img, circle_mask(cx, cy, .36), (255, 140, 70), .05, .55)
    paste_grad(img, circle_mask(cx, cy, .36), ORANGE)
    paste_grad(img, circle_mask(cx, cy, .30), [(0, (40, 62, 128)), (1, (14, 22, 52))], "v")
    lines_in_circle(img, cx, cy, .30, (255, 186, 90), .014)
    hi = new_mask()
    ImageDraw.Draw(hi).ellipse([P(.30), P(.25), P(.46), P(.36)], fill=70)
    img.paste(Image.new("RGB", (S, S), (255, 255, 255)), (0, 0), hi.filter(ImageFilter.GaussianBlur(P(.02))))
    return img


def v2():
    """Окно с крестовиной: в четырёх стёклах — закат над планетой."""
    img = tile()
    fx0, fy0, fx1, fy1 = .2, .2, .8, .8
    fr = new_mask()
    ImageDraw.Draw(fr).rounded_rectangle([P(fx0), P(fy0), P(fx1), P(fy1)], radius=P(.07), fill=255)
    glow(img, fr, (255, 140, 70), .04, .5)
    paste_grad(img, fr, ORANGE)
    # сцена
    sc = grad([(0, (28, 40, 100)), (.55, (120, 64, 150)), (1, (255, 122, 89))], "v")
    d = ImageDraw.Draw(sc)
    d.ellipse([P(.15), P(.58), P(.85), P(1.3)], fill=(16, 22, 48))
    arc = new_mask()
    ImageDraw.Draw(arc).ellipse([P(.15), P(.58), P(.85), P(1.3)], outline=255, width=int(P(.012)))
    sc.paste(Image.new("RGB", (S, S), (255, 214, 130)), (0, 0), arc)
    # стёкла
    pad, gap = .045, .03
    ix0, iy0, ix1, iy1 = fx0 + pad, fy0 + pad, fx1 - pad, fy1 - pad
    mw, mh = (ix1 - ix0 - gap) / 2, (iy1 - iy0 - gap) / 2
    panes = new_mask()
    pd = ImageDraw.Draw(panes)
    for i in range(2):
        for j in range(2):
            x0 = ix0 + i * (mw + gap)
            y0 = iy0 + j * (mh + gap)
            pd.rounded_rectangle([P(x0), P(y0), P(x0 + mw), P(y0 + mh)], radius=P(.03), fill=255)
    img.paste(sc, (0, 0), panes)
    # звёзды
    sd = ImageDraw.Draw(img)
    for (x, y, r) in [(.33, .3, .008), (.42, .4, .006), (.62, .32, .009), (.7, .42, .006), (.55, .26, .005)]:
        sd.ellipse([P(x - r), P(y - r), P(x + r), P(y + r)], fill=(255, 255, 255))
    return img


def v3():
    """Глобус-«пуск»: оранжевый шар с тёмной сеткой и кнопкой play в центре."""
    img = tile()
    cx = cy = 0.5
    glow(img, circle_mask(cx, cy, .37), (255, 130, 70), .06, .6)
    paste_grad(img, circle_mask(cx, cy, .37), ORANGE)
    lines_in_circle(img, cx, cy, .37, (150, 70, 60), .016)
    paste_grad(img, circle_mask(cx, cy, .19), [(0, (22, 34, 58)), (1, (9, 13, 22))], "v")
    d = ImageDraw.Draw(img)
    play_triangle(d, .515, .5, .17, (255, 190, 90))
    img.paste(grad(ORANGE), (0, 0), _tri_mask(.515, .5, .17))
    return img


def _tri_mask(cx, cy, h):
    m = new_mask()
    play_triangle(ImageDraw.Draw(m), cx, cy, h, 255)
    return m


def v4():
    """Телевизор с антеннами, на экране — глобус и красная точка «прямой эфир»."""
    img = tile()
    d = ImageDraw.Draw(img)
    # антенны
    am = new_mask()
    ad = ImageDraw.Draw(am)
    for (x, y) in [(.34, .15), (.66, .15)]:
        ad.line([P(.5), P(.34), P(x), P(y)], fill=255, width=int(P(.03)))
        ad.ellipse([P(x - .028), P(y - .028), P(x + .028), P(y + .028)], fill=255)
    paste_grad(img, am, ORANGE)
    body = new_mask()
    ImageDraw.Draw(body).rounded_rectangle([P(.13), P(.31), P(.87), P(.80)], radius=P(.09), fill=255)
    glow(img, body, (255, 140, 70), .04, .45)
    paste_grad(img, body, ORANGE)
    scr = new_mask()
    ImageDraw.Draw(scr).rounded_rectangle([P(.19), P(.37), P(.81), P(.74)], radius=P(.05), fill=255)
    paste_grad(img, scr, [(0, (30, 46, 100)), (1, (12, 18, 44))], "v")
    sc_cx, sc_cy, r = .5, .555, .14
    paste_grad(img, circle_mask(sc_cx, sc_cy, r), ORANGE)
    lines_in_circle(img, sc_cx, sc_cy, r, (150, 70, 60), .008)
    # ножки
    fm = new_mask()
    fd = ImageDraw.Draw(fm)
    fd.rounded_rectangle([P(.25), P(.80), P(.33), P(.86)], radius=P(.02), fill=255)
    fd.rounded_rectangle([P(.67), P(.80), P(.75), P(.86)], radius=P(.02), fill=255)
    paste_grad(img, fm, ORANGE)
    live_dot(img, .725, .43, .035)
    return img


def v5():
    """Иллюминатор: металлическое кольцо с болтами, внутри — планета над горизонтом и красный «live»."""
    img = tile()
    cx = cy = .5
    glow(img, circle_mask(cx, cy, .39), (255, 130, 70), .05, .5)
    paste_grad(img, circle_mask(cx, cy, .39), ORANGE)
    # сцена внутри
    sc = grad([(0, (8, 12, 30)), (1, (40, 30, 90))], "v")
    d = ImageDraw.Draw(sc)
    rng = np.random.default_rng(7)
    for _ in range(60):
        x, y = rng.uniform(.2, .8), rng.uniform(.15, .6)
        r = rng.uniform(.002, .006)
        d.ellipse([P(x - r), P(y - r), P(x + r), P(y + r)], fill=(255, 255, 255))
    planet = circle_mask(.5, 1.12, .55)
    sc.paste(grad([(0, (255, 214, 130)), (.35, (255, 122, 89)), (1, (60, 30, 100))], "v"), (0, 0), planet)
    atm = ring_mask(.5, 1.12, .55, .018)
    glow(sc, atm, (255, 214, 140), .02, .8)
    sc.paste(Image.new("RGB", (S, S), (255, 232, 180)), (0, 0), atm)
    img.paste(sc, (0, 0), circle_mask(cx, cy, .31))
    # болты
    bd = ImageDraw.Draw(img)
    for k in range(8):
        a = k * math.pi / 4 + math.pi / 8
        bx, by = cx + .35 * math.cos(a), cy + .35 * math.sin(a)
        bd.ellipse([P(bx - .017), P(by - .017), P(bx + .017), P(by + .017)], fill=(120, 60, 50))
    live_dot(img, .66, .33, .04)
    return img


VARIANTS = [("1 Глобус в круге", v1), ("2 Окно-закат", v2), ("3 Глобус-пуск", v3),
            ("4 Телевизор", v4), ("5 Иллюминатор", v5)]
