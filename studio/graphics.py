"""Text graphics rendered with Pillow as transparent PNG overlays (titles, chapters, captions, credits)."""
from functools import lru_cache

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from .core import FONTS

FALLBACK = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"


@lru_cache(maxsize=64)
def font(name="Montserrat", size=48, weight="Bold"):
    try:
        f = ImageFont.truetype(str(FONTS / f"{name}.ttf"), size)
        try:
            f.set_variation_by_name(weight)
        except (OSError, ValueError):
            pass
        return f
    except OSError:
        return ImageFont.truetype(FALLBACK, size)


def _spaced(draw, xy, text, fnt, fill, spacing):
    """Draw letter-spaced text (for small uppercase labels); returns width."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + spacing
    return x - xy[0] - spacing


def _spaced_width(draw, text, fnt, spacing):
    return sum(draw.textlength(ch, font=fnt) for ch in text) + spacing * max(0, len(text) - 1)


def _wrap(draw, text, fnt, max_w):
    lines = []
    for para in str(text).split("\n"):
        cur = ""
        for word in para.split():
            test = f"{cur} {word}".strip()
            if draw.textlength(test, font=fnt) <= max_w or not cur:
                cur = test
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def _shadowed(canvas, painter, radius):
    """Paint text onto a layer, add a soft drop shadow beneath it for legibility over footage."""
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    painter(ImageDraw.Draw(layer))
    alpha = layer.getchannel("A")
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow.putalpha(alpha.filter(ImageFilter.GaussianBlur(radius)).point(lambda a: int(a * 0.55)))
    canvas.alpha_composite(shadow)
    canvas.alpha_composite(layer)


ACCENT = (255, 196, 64, 255)


def title_png(path, w, h, title, subtitle=None, label=None, style="main", accent=ACCENT):
    """style: main (opening title), chapter (chapter card), end (centered credits)."""
    s = h / 1080
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)
    accent = tuple(accent)

    if style == "end":
        lines = [title] + ([subtitle] if subtitle else [])
        f_big, f_small = font("Montserrat", int(64 * s), "Bold"), font("Inter", int(34 * s), "Regular")
        body = []
        for i, ln in enumerate(lines):
            fnt = f_big if i == 0 else f_small
            for sub in _wrap(d, ln, fnt, w * 0.8):
                body.append((sub, fnt))
        lh = [int((fnt.size * 1.35)) for _, fnt in body]
        y = (h - sum(lh)) / 2

        def paint(dd):
            yy = y
            for (txt, fnt), hh in zip(body, lh):
                tw = dd.textlength(txt, font=fnt)
                dd.text(((w - tw) / 2, yy), txt, font=fnt, fill=(255, 255, 255, 255))
                yy += hh
        _shadowed(im, paint, 12 * s)
        im.save(path)
        return path

    f_label = font("Inter", int(26 * s), "SemiBold")
    f_title = font("Montserrat", int((112 if style == "main" else 92) * s), "ExtraBold")
    f_sub = font("Inter", int(38 * s), "Regular")
    title_lines = _wrap(d, title, f_title, w * 0.82)
    sub_lines = _wrap(d, subtitle, f_sub, w * 0.7) if subtitle else []
    lab = (label or "").upper()
    sp = 8 * s

    th = int(f_title.size * 1.1)
    sh = int(f_sub.size * 1.4)
    block = (int(f_label.size * 2.2) if lab else 0) + th * len(title_lines) + (int(f_title.size * 0.5) + int(22 * s) + sh * len(sub_lines) if sub_lines else 0)
    y0 = (h - block) / 2

    def paint(dd):
        y = y0
        if lab:
            lw = _spaced_width(dd, lab, f_label, sp)
            _spaced(dd, ((w - lw) / 2, y), lab, f_label, accent, sp)
            y += int(f_label.size * 2.2)
        for ln in title_lines:
            tw = dd.textlength(ln, font=f_title)
            dd.text(((w - tw) / 2, y), ln, font=f_title, fill=(255, 255, 255, 255))
            y += th
        if sub_lines:
            y += int(f_title.size * 0.5)
            lw = int(90 * s)
            dd.rectangle([(w - lw) / 2, y, (w + lw) / 2, y + max(2, int(4 * s))], fill=accent)
            y += int(22 * s)
            for ln in sub_lines:
                tw = dd.textlength(ln, font=f_sub)
                dd.text(((w - tw) / 2, y), ln, font=f_sub, fill=(235, 235, 235, 255))
                y += sh
    _shadowed(im, paint, 14 * s)
    im.save(path)
    return path


def caption_png(path, w, h, text, label=None, position="bottom-left", accent=ACCENT):
    """Lower-third caption: small accent label (date/place) + main line, over a soft gradient."""
    s = h / 1080
    im = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    # bottom gradient for legibility
    grad_h = int(h * 0.38)
    grad = Image.new("L", (1, grad_h))
    for y in range(grad_h):
        grad.putpixel((0, y), int(200 * (y / grad_h) ** 1.4))
    band = Image.new("RGBA", (w, grad_h), (0, 0, 0, 255))
    band.putalpha(grad.resize((w, grad_h)))
    if position.startswith("bottom"):
        im.alpha_composite(band, (0, h - grad_h))
    d = ImageDraw.Draw(im)
    f_label = font("Inter", int(27 * s), "Bold")
    f_text = font("Montserrat", int(46 * s), "SemiBold")
    lines = _wrap(d, text, f_text, w * 0.62)
    lab = (label or "").upper()
    sp = 6 * s
    margin = int(96 * s)
    lh = int(f_text.size * 1.25)
    block = lh * len(lines) + (int(f_label.size * 1.9) if lab else 0)
    y = h - margin - block if position.startswith("bottom") else margin
    centered = position.endswith("center")

    def paint(dd):
        yy = y
        if lab:
            lw = _spaced_width(dd, lab, f_label, sp)
            x = (w - lw) / 2 if centered else margin
            if not centered:
                dd.rectangle([x, yy + f_label.size * 0.15, x + max(3, int(5 * s)), yy + f_label.size * 1.15], fill=accent)
                x += int(18 * s)
            _spaced(dd, (x, yy), lab, f_label, accent, sp)
            yy += int(f_label.size * 1.9)
        for ln in lines:
            tw = dd.textlength(ln, font=f_text)
            dd.text(((w - tw) / 2 if centered else margin, yy), ln, font=f_text, fill=(255, 255, 255, 255))
            yy += lh
    _shadowed(im, paint, 8 * s)
    im.save(path)
    return path


def photo_layers(im, w, h, layout="card", border=None):
    """Static photo presentation without zoom: (background RGB, card RGBA, card x, card y).

    layout 'card'   – framed photo centred on a blurred, dimmed copy of itself
    layout 'person' – framed portrait on the left third (text goes on the right)
    """
    from PIL import ImageOps
    s = w / 1920
    bg = ImageOps.fit(im, (max(1, w // 10), max(1, h // 10))).filter(ImageFilter.GaussianBlur(4)).resize((w, h), Image.BILINEAR)
    bg = Image.eval(bg, lambda v: int(v * 0.42))
    if layout == "person":
        box = (int(w * 0.40), int(h * 0.80))
    else:
        box = (int(w * 0.84), int(h * 0.80))
    photo = ImageOps.contain(im, box, Image.LANCZOS)
    b = border if border is not None else max(4, int(10 * s))
    pad = int(40 * s)
    card = Image.new("RGBA", (photo.width + 2 * b + 2 * pad, photo.height + 2 * b + 2 * pad), (0, 0, 0, 0))
    sh = Image.new("L", card.size, 0)
    ImageDraw.Draw(sh).rectangle([pad, pad + int(10 * s), pad + photo.width + 2 * b, pad + photo.height + 2 * b + int(10 * s)], fill=150)
    card.putalpha(sh.filter(ImageFilter.GaussianBlur(int(18 * s))))
    frame = Image.new("RGBA", (photo.width + 2 * b, photo.height + 2 * b), (245, 245, 242, 255))
    frame.paste(photo, (b, b))
    card.alpha_composite(frame, (pad, pad))
    if layout == "person":
        x = int(w * 0.30) - card.width // 2
    else:
        x = (w - card.width) // 2
    y = (h - card.height) // 2
    return bg, card, x, y


def person_png(path, w, h, name, role=None, accent=ACCENT):
    """Name + role block on the right half, used next to a 'person' photo card."""
    s = w / 1920
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    f_name = font("Montserrat", int(76 * s), "Bold")
    f_role = font("Inter", int(30 * s), "SemiBold")
    x0, max_w = int(w * 0.55), int(w * 0.40)

    def paint(d):
        lines = _wrap(d, name, f_name, max_w)
        lh = int(f_name.size * 1.12)
        total = lh * len(lines) + (int(70 * s) if role else 0)
        y = (h - total) // 2
        d.rectangle([x0, y - int(34 * s), x0 + int(70 * s), y - int(28 * s)], fill=accent)
        for ln in lines:
            d.text((x0, y), ln, font=f_name, fill=(255, 255, 255, 255))
            y += lh
        if role:
            _spaced(d, (x0, y + int(22 * s)), role.upper(), f_role, accent, int(5 * s))

    _shadowed(img, paint, int(8 * s))
    img.save(path)
