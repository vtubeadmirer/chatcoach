# vendored from vtubeadmirer/clipslicer@07d13cc (tools/make_icon.py) — 변경: 그림만 바꿈(남색 배경 + 라벨 태그 + 청록 타임라인 띠·마커 3개), 4배로 그려 줄여 작은 크기도 매끈하게
"""assets/icon.png, assets/icon.ico 생성 (Pillow 필요)."""
import sys

# CI 러너처럼 콘솔 인코딩이 UTF-8 이 아닌 곳에서도 한글 출력이 죽지 않게
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
import os
import sys

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS = os.path.join(ROOT, "assets")

BG = (20, 30, 58, 255)          # 짙은 남색 배경
TAG = (255, 193, 7, 255)        # 라벨 태그 (호박색 — 남색 위에서 잘 보이게)
TAG_EDGE = (255, 224, 130, 255)  # 태그 윗면 밝은 띠
BAND = (38, 222, 214, 255)      # 타임라인 띠 (밝은 청록)
MARK = (255, 255, 255, 255)     # 마커
SS = 4                          # 4배로 그려서 줄인다 (가장자리 계단 줄이기)


def _draw_big(s: int) -> Image.Image:
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, s - 1, s - 1), radius=s * 0.22, fill=BG)
    # 라벨 태그: 왼쪽이 뾰족한 오각형 + 끈 구멍
    top, bot = s * 0.18, s * 0.60
    mid = (top + bot) / 2
    left, shoulder, right = s * 0.10, s * 0.32, s * 0.90
    d.polygon([(left, mid), (shoulder, top), (right, top), (right, bot), (shoulder, bot)], fill=TAG)
    d.rectangle((shoulder, top, right, top + s * 0.035), fill=TAG_EDGE)
    hr = s * 0.055
    hx = shoulder - s * 0.01
    d.ellipse((hx - hr, mid - hr, hx + hr, mid + hr), fill=BG)
    # 아래 타임라인 띠 + 마커 3개
    b_top, b_bot = s * 0.72, s * 0.82
    d.rounded_rectangle((s * 0.10, b_top, s * 0.90, b_bot), radius=(b_bot - b_top) / 2, fill=BAND)
    w = s * 0.035
    for x in (0.30, 0.52, 0.74):
        cx = s * x
        d.rounded_rectangle((cx - w / 2, b_top - s * 0.05, cx + w / 2, b_bot + s * 0.05), radius=w / 2, fill=MARK)
    return img


def draw(size: int) -> Image.Image:
    return _draw_big(size * SS).resize((size, size), Image.LANCZOS)


def main():
    os.makedirs(ASSETS, exist_ok=True)
    big = draw(512)
    big.save(os.path.join(ASSETS, "icon.png"))
    sizes = [16, 24, 32, 48, 64, 128, 256]
    frames = [draw(z) for z in sizes]
    frames[-1].save(os.path.join(ASSETS, "icon.ico"), format="ICO",
                    sizes=[(z, z) for z in sizes], append_images=frames[:-1])
    print("완료:", ASSETS)
    return 0


if __name__ == "__main__":
    sys.exit(main())
