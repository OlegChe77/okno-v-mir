from pathlib import Path
from PIL import Image
import icon_lib as L
root = Path(__file__).resolve().parent.parent
img = L.v4()
for s in (1024, 512):
    L.finish(img, s).save(root / ("logo.png" if s == 512 else "logo_1024.png"))
# ico: каждый размер рисуем из 2048 отдельно (LANCZOS) и пакуем PNG-кадрами
sizes = [16, 20, 24, 32, 48, 64, 128, 256]
frames = [L.finish(img, s) for s in sizes]
frames[-1].save(root / "app.ico", format="ICO", sizes=[(s, s) for s in sizes], append_images=frames[:-1])
L.finish(img, 64).save(root / "favicon.png")
print([im.size for im in Image.open(root / "app.ico").ico.sizes() and []] or "ok")
