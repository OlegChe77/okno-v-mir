import sys
from PIL import Image, ImageDraw, ImageFont
import icon_lib as L
out = sys.argv[1]
font = ImageFont.truetype("C:/Windows/Fonts/segoeui.ttf", 15)
W, H, cw = 5*250+20, 380, 250
sheet = Image.new("RGB", (W, H), (10, 14, 22))
d = ImageDraw.Draw(sheet)
for i, (name, fn) in enumerate(L.VARIANTS):
    big = L.finish(fn(), 512)
    x = 10 + i*cw
    sheet.paste(big.resize((220,220), Image.LANCZOS), (x+10, 10), big.resize((220,220), Image.LANCZOS))
    d.text((x+10, 238), name, fill=(232,237,245), font=font)
    xx = x+10
    for s in (64, 48, 32, 16):
        ic = big.resize((s, s), Image.LANCZOS)
        sheet.paste(ic, (xx, 280 + (64-s)//2), ic)
        xx += s + 14
sheet.save(out)
print(sheet.size)
