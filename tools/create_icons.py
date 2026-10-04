"""Build the application and status-bar icons from the selected visual identity."""
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter


ROOT = Path(__file__).resolve().parents[1]
ICONS = ROOT / "icons"
APP_ICON_SOURCE = ICONS / "AppIcon-source.png"


def menu_icon():
    """Monochrome connector mark for the macOS status bar."""
    scale = 4
    p = lambda value: int(round(value * scale))
    image = Image.new("RGBA", (36 * scale, 36 * scale), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    ink = (0, 0, 0, 255)

    # Compact hardware connector: the upper port represents the K210 serial
    # interface, while the curved lower lead keeps the mark visually balanced.
    draw.rounded_rectangle((p(8), p(5), p(28), p(17)), radius=p(3.5),
                           outline=ink, width=p(2.6))
    draw.line((p(13), p(9), p(13), p(13)), fill=ink, width=p(2.2))
    draw.line((p(23), p(9), p(23), p(13)), fill=ink, width=p(2.2))
    draw.line((p(18), p(17), p(18), p(25)), fill=ink, width=p(2.7))
    draw.arc((p(11), p(21), p(25), p(34)), 0, 180, fill=ink, width=p(2.7))
    draw.ellipse((p(9.65), p(26.15), p(12.35), p(28.85)), fill=ink)
    draw.ellipse((p(23.65), p(26.15), p(26.35), p(28.85)), fill=ink)

    image.resize((36, 36), Image.Resampling.LANCZOS).save(ICONS / "menuTemplate.png")


def app_icon():
    """Create a native full-size ICNS without a pre-rendered outer mask."""
    if not APP_ICON_SOURCE.exists():
        raise FileNotFoundError("Missing application icon source: " + str(APP_ICON_SOURCE))
    source = Image.open(APP_ICON_SOURCE).convert("RGBA")
    side = min(source.size)
    left = (source.width - side) // 2
    top = (source.height - side) // 2
    source = source.crop((left, top, left + side, top + side))

    # The source has a pre-rendered rounded tile. macOS 27 treats that as a
    # legacy icon and places it on a pale system tile. Crop through the tile's
    # rounded perimeter so the supplied artwork becomes an edge-to-edge square;
    # macOS can then apply its own single rounded mask.
    opaque = Image.new("RGBA", source.size, (5, 14, 24, 255))
    opaque.alpha_composite(source)
    inset = round(side * 0.12)
    artwork = opaque.crop((inset, inset, side-inset, side-inset))
    artwork = artwork.resize((1024, 1024), Image.Resampling.LANCZOS)

    # Keep the dark background full bleed, while shrinking only the luminous
    # ring and waves into a comfortable safe area.
    icon = Image.new("RGBA", (1024, 1024), (4, 13, 23, 255))
    glow = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow)
    glow_draw.ellipse((-160, -180, 1184, 1120), fill=(26, 57, 80, 135))
    icon = Image.alpha_composite(icon, glow.filter(ImageFilter.GaussianBlur(210)))

    red, green, blue, _ = artwork.split()
    brightness = ImageChops.lighter(ImageChops.lighter(red, green), blue)
    foreground_mask = brightness.point(
        lambda value: 0 if value <= 52 else 255 if value >= 92
        else round((value - 52) * 255 / 40)
    ).filter(ImageFilter.GaussianBlur(1.4))
    mark_size = 860
    offset = (1024 - mark_size) // 2
    mark = artwork.resize((mark_size, mark_size), Image.Resampling.LANCZOS)
    mark_mask = foreground_mask.resize((mark_size, mark_size), Image.Resampling.LANCZOS)
    transparent = Image.new("RGBA", mark.size, (0, 0, 0, 0))
    icon.alpha_composite(Image.composite(mark, transparent, mark_mask),
                         (offset, offset))
    icon.save(ICONS / "AppIcon.icns", format="ICNS")


if __name__ == "__main__":
    ICONS.mkdir(exist_ok=True)
    menu_icon()
    app_icon()
