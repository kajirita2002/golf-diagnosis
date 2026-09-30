"""合成の棒人間の動画を作る（画面の確認 ui_check.py・通しの確認で使う。本物の動画・人は使わない）。

analysis/tests/synthetic_swing.py の棒人間（P1〜P7 のコマ）を、1つの P を 0.5 秒ずつ映す動画にする。
画面の確認では、この動画を選んで P1〜P7 を手で選び、window.__FAKE_POSE が時刻 → P の点を返す
（MediaPipe の代わり。棒人間では本物の姿勢推定は点を返さないため）。

  testdata/synthetic/stick_dtl.webm   後ろから・60fps・3.5秒（P2 のクラブの先が内側）
  testdata/synthetic/stick_fo.webm    正面から・60fps・3.5秒
  testdata/synthetic/stick_dtl_240.mp4 後ろから・240fps・0.5秒（H.264・moov が末尾。コンテナの fps の読み取りの確認用）
  testdata/synthetic/stick_motion_dtl.webm 後ろから・30fps・約13秒。スイング3本（2本目は素振り＝ボールが動かない）。
      自動の取り出し（段2b）の確認用。棒人間の点は synthetic_swing.motion と同じ（画面の確認は window.__FAKE_POSE に同じ点を渡す）

使い方: FFMPEG=/path/to/ffmpeg python3 scripts/make_synthetic_video.py
（ffmpeg が無ければ pip の imageio-ffmpeg を入れて、その実行ファイルを FFMPEG に渡す）
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "analysis", "tests"))
import synthetic_swing as syn  # noqa: E402

OUT = os.path.join(ROOT, "testdata", "synthetic")
SEG = [("left_shoulder", "left_elbow"), ("left_elbow", "left_wrist"), ("right_shoulder", "right_elbow"), ("right_elbow", "right_wrist"),
       ("left_shoulder", "right_shoulder"), ("left_hip", "right_hip"), ("left_hip", "left_knee"), ("left_knee", "left_ankle"),
       ("right_hip", "right_knee"), ("right_knee", "right_ankle"), ("left_heel", "left_toe"), ("right_heel", "right_toe")]


def png(path: str, img: np.ndarray) -> None:
    h, w, _ = img.shape
    raw = b"".join(b"\x00" + img[y].tobytes() for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def draw(view: str, p: str, faults=()) -> np.ndarray:
    img = np.full((syn.H, syn.W, 3), (238, 242, 236), dtype=np.uint8)
    img[640:, :] = (120, 150, 100)  # 地面
    yy, xx = np.mgrid[0:syn.H, 0:syn.W]

    def line(a, b, r, color):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / 2) + 2
        for t in np.linspace(0, 1, n):
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            x0, x1, y0, y1 = int(max(0, x - r)), int(min(syn.W, x + r + 1)), int(max(0, y - r)), int(min(syn.H, y + r + 1))
            m = (xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2 <= r * r
            img[y0:y1, x0:x1][m] = color

    pts, taps = (syn._dtl if view == "dtl" else syn._fo)(p, set(faults))
    for a, b in SEG:
        line(pts[a], pts[b], 6, (40, 50, 45))
    sm = ((pts["left_shoulder"][0] + pts["right_shoulder"][0]) / 2, (pts["left_shoulder"][1] + pts["right_shoulder"][1]) / 2)
    hm = ((pts["left_hip"][0] + pts["right_hip"][0]) / 2, (pts["left_hip"][1] + pts["right_hip"][1]) / 2)
    line(sm, hm, 7, (40, 50, 45))
    nx, ny = pts["nose"]
    line((nx - 6, ny - 6), (nx - 6, ny - 6), 20, (40, 50, 45))
    if "grip" in taps and "head" in taps:
        line(taps["grip"], taps["head"], 3, (29, 78, 137))
        line(taps["head"], taps["head"], 6, (29, 78, 137))
    bx = 840 if view == "dtl" else 648
    line((bx, 640), (bx, 640), syn.BALL_D / 2, (255, 255, 255))
    return img


def make(ffmpeg: str, view: str, name: str, fps: int, ps, seg_s: float, faults=(), codec=("-c:v", "libvpx", "-b:v", "400k", "-auto-alt-ref", "0")) -> None:
    tmp = tempfile.mkdtemp()
    lst = os.path.join(tmp, "list.txt")
    with open(lst, "w") as f:
        for i, p in enumerate(ps):
            fn = os.path.join(tmp, f"{i}.png")
            png(fn, draw(view, p, faults))
            f.write(f"file '{fn}'\nduration {seg_s}\n")
        f.write(f"file '{os.path.join(tmp, f'{len(ps) - 1}.png')}'\n")
    out = os.path.join(OUT, name)
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst, "-vf", f"fps={fps},format=yuv420p", "-r", str(fps), *codec, out], check=True)
    shutil.rmtree(tmp)
    print(out, os.path.getsize(out))


MOTION, MOTION_GROUND, MOTION_BALL = syn.MOTION, syn.MOTION_GROUND, syn.MOTION_BALL


def draw_pose(pose: dict, ball: bool) -> np.ndarray:
    img = np.full((syn.H, syn.W, 3), (238, 242, 236), dtype=np.uint8)
    img[MOTION_GROUND:, :] = (120, 150, 100)
    yy, xx = np.mgrid[0:syn.H, 0:syn.W]

    def line(a, b, r, color):
        n = int(max(abs(b[0] - a[0]), abs(b[1] - a[1])) / 2) + 2
        for t in np.linspace(0, 1, n):
            x, y = a[0] + (b[0] - a[0]) * t, a[1] + (b[1] - a[1]) * t
            x0, x1, y0, y1 = int(max(0, x - r)), int(min(syn.W, x + r + 1)), int(max(0, y - r)), int(min(syn.H, y + r + 1))
            m = (xx[y0:y1, x0:x1] - x) ** 2 + (yy[y0:y1, x0:x1] - y) ** 2 <= r * r
            img[y0:y1, x0:x1][m] = color

    for a, b in SEG:
        line(pose[a], pose[b], 6, (40, 50, 45))
    if ball:
        line(MOTION_BALL, MOTION_BALL, syn.BALL_D / 2, (255, 255, 255))
    return img


def make_motion(ffmpeg: str) -> None:
    body, _ = syn.motion(MOTION["view"], fps=MOTION["fps"], n_swings=MOTION["n_swings"], practice=MOTION["practice"])
    tmp = tempfile.mkdtemp()
    for k, f in enumerate(body["frames"]):
        pose = {name: (f["lm"][syn.IDX[name]][0] * syn.W, f["lm"][syn.IDX[name]][1] * syn.H) for name in syn.IDX}
        png(os.path.join(tmp, f"{k:05d}.png"), draw_pose(pose, body["roi"][k] < 0.25))
    out = os.path.join(OUT, "stick_motion_dtl.webm")
    subprocess.run([ffmpeg, "-y", "-loglevel", "error", "-framerate", str(int(MOTION["fps"])), "-i", os.path.join(tmp, "%05d.png"),
                    "-vf", "format=yuv420p", "-c:v", "libvpx", "-b:v", "600k", "-auto-alt-ref", "0", out], check=True)
    shutil.rmtree(tmp)
    print(out, os.path.getsize(out))


def main() -> None:
    if "--motion" in sys.argv:
        ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
        os.makedirs(OUT, exist_ok=True)
        make_motion(ffmpeg)
        return
    ffmpeg = os.environ.get("FFMPEG") or shutil.which("ffmpeg")
    if not ffmpeg:
        sys.exit("ffmpeg がありません（FFMPEG に実行ファイルを渡す）")
    os.makedirs(OUT, exist_ok=True)
    make(ffmpeg, "dtl", "stick_dtl.webm", 60, syn.P_ORDER[:7], 0.5, faults=("p2_inside",))
    make(ffmpeg, "fo", "stick_fo.webm", 60, syn.P_ORDER[:7], 0.5)
    make(ffmpeg, "dtl", "stick_dtl_240.mp4", 240, ("P1", "P2"), 0.25, codec=("-c:v", "libx264", "-preset", "veryfast", "-crf", "30"))
    make_motion(ffmpeg)


if __name__ == "__main__":
    main()
