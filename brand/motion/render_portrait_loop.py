#!/usr/bin/env python3
"""
Hero loop for Ratiocination: animates source/portrait.png into an 8 s, 24 fps seamless loop.

The portrait is never repainted, only resampled:
- a face rig moves the gaze, adds one blink, a skeptical squint and a restrained brow;
- cigarette smoke is a particle simulation in a flow that repeats every 8 s, so the last frame leads
  straight back into the first;
- between 2 and 4 s the smoke curls into a question mark, which dissolves into a graphite knowledge
  graph between 4 and 6 s; the graph fades and the smoke rises freely again by 6-8 s;
- the camera pushes in 2 % and returns; film grain is added last, never on the white background.

Usage (from this folder, with the repo's .venv):
  python render_portrait_loop.py stills 0 1.1 3.2 4.8     # PNG stills in ./stills
  python render_portrait_loop.py raw  | ./encode_h264 ratiocination-loop.mp4 1080 1486 24 9000000
  python render_portrait_loop.py jpeg | ffmpeg -f image2pipe -c:v mjpeg -framerate 24 -i - ... .webm
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source" / "portrait.png"

T = 8.0  # loop length (s)
FPS = 24
FRAMES = int(T * FPS)
SRC_W, SRC_H = 735, 1011
OUT_W, OUT_H = 1080, 1486
KX, KY = OUT_W / SRC_W, OUT_H / SRC_H


# ---- timing -----------------------------------------------------------------------------------


def smooth(x):
    x = np.clip(x, 0.0, 1.0)
    return x * x * (3.0 - 2.0 * x)


def ramp(t, a, b):
    return smooth((t - a) / (b - a))


def keyed(t: float, keys: list[tuple[float, float]]) -> float:
    """Value at time t from (time, value) keys, easing in and out of every key."""
    for (t0, v0), (t1, v1) in zip(keys, keys[1:]):
        if t0 <= t <= t1:
            return v0 + (v1 - v0) * float(smooth((t - t0) / (t1 - t0)))
    return keys[-1][1]


def camera_scale(t: float) -> float:
    return 1.0 + 0.02 * keyed(t, [(0.0, 0.0), (2.0, 1.0), (6.0, 1.0), (8.0, 0.0)])


# Gaze in source px (negative: towards the left of the frame). Saccades are quick, fixations hold:
# a left-to-right scan, one blink, a long look, a glance at the graph, then back home.
GAZE = [(0.0, 0.0), (0.50, 0.0), (0.58, -1.5), (1.38, -1.5), (1.47, 1.5), (3.95, 1.5), (4.05, -1.3),
        (5.30, -1.3), (5.40, 0.5), (6.55, 0.5), (6.65, 0.0), (8.0, 0.0)]
BLINK = [(0.0, 0.0), (1.02, 0.0), (1.09, 1.0), (1.13, 1.0), (1.27, 0.0), (8.0, 0.0)]
SQUINT = [(0.0, 0.0), (2.1, 0.0), (2.75, 1.0), (5.6, 1.0), (6.6, 0.0), (8.0, 0.0)]
BROW = [(0.0, 0.0), (2.2, 0.0), (2.9, 1.0), (5.5, 1.0), (6.5, 0.0), (8.0, 0.0)]


# ---- face rig (source px) ---------------------------------------------------------------------

# Each eye: the opening's top and bottom edge, sampled across its width (measured on the source).
EYES = [
    dict(xs=[491, 497, 505, 513, 520, 528, 535, 541, 547],
         top=[597.5, 594.5, 593.2, 592.8, 592.6, 594.0, 596.5, 598.5, 602.5],
         bottom=[597.5, 599.5, 600.6, 601.8, 602.5, 602.6, 602.6, 602.6, 602.5]),
    dict(xs=[619, 622, 626, 632, 640, 648, 655, 661, 666, 669],
         top=[609.0, 604.5, 602.0, 600.8, 600.5, 600.5, 599.6, 598.6, 597.8, 598.0],
         bottom=[609.0, 609.5, 609.0, 608.0, 607.6, 607.2, 604.5, 602.5, 600.0, 598.0]),
]
LID = 5.0  # rows of skin above an eye that stretch down over it
LOWER_LID = 3.0
EYE_BOXES = [(486, 582, 553, 609), (614, 590, 674, 614)]  # x0, y0, x1, y1: supersampled

# Brows: (cx, cy, rx, ry, dx, dy) -- content moves down and inwards by (dx, dy) at full frown.
BROWS = [(530, 580, 52, 11, 0.9, 1.5), (646, 588, 40, 10, -0.9, 1.3), (604, 588, 16, 16, 0.0, 0.9)]


def eye_map(x, y, gaze, upper, lower):
    """Where each output point samples the source, given gaze (px) and lid closure (0..1)."""
    sx, sy = x.copy(), y.copy()
    lower = min(lower, 1.0 - upper)
    for eye in EYES:
        xs = np.asarray(eye["xs"], float)
        inside = (x > xs[0]) & (x < xs[-1])
        if not inside.any():
            continue
        xi, yi = x[inside], y[inside]
        top = np.interp(xi, xs, eye["top"])
        bottom = np.interp(xi, xs, eye["bottom"])
        height = bottom - top
        travel = upper * (height + 2.4)  # a closing lid passes the opening so no white shows under it
        lash = 1.6 * min(1.0, 4.0 * upper)  # and its edge is the lash line, never the white's top edge
        edge_top = top + travel
        edge_bottom = np.maximum(bottom - lower * height, edge_top)
        ys = yi.copy()
        lid = (yi >= top - LID) & (yi <= edge_top)
        ys[lid] = (top - LID)[lid] + (yi[lid] - (top - LID)[lid]) * (LID - lash) / (LID + travel[lid])
        low = (yi >= edge_bottom) & (yi <= bottom + LOWER_LID)
        far = (bottom + LOWER_LID)[low]
        ys[low] = far - (far - yi[low]) * LOWER_LID / (LOWER_LID + lower * height[low])
        # Gaze: slide what is visible of the eye sideways, pinned at both corners.
        u = (xi - xs[0]) / (xs[-1] - xs[0])
        across = np.sin(np.pi * u) ** 0.7
        visible = smooth((yi - edge_top + 0.6) / 1.6) * (1.0 - smooth((yi - edge_bottom + 1.0) / 1.6))
        sx[inside] = xi - gaze * across * visible
        sy[inside] = ys
    return sx, sy


def brow_shift(x, y, amount):
    dx = np.zeros_like(x)
    dy = np.zeros_like(y)
    if amount <= 0:
        return dx, dy
    for cx, cy, rx, ry, mx, my in BROWS:
        w = np.exp(-(((x - cx) / rx) ** 2 + ((y - cy) / ry) ** 2)) * amount
        dx += w * mx
        dy += w * my
    return dx, dy


# ---- smoke ------------------------------------------------------------------------------------

TIP = np.array([418.0, 800.0])  # the cigarette's burning end
SIM_FPS = 96
DT = 1.0 / SIM_FPS
WARMUP = 5.0
LIFETIME = 4.6
STRANDS = 6
Q_START, Q_END = 0.95, 2.45  # births that become the question mark
DOT_START, DOT_END = 2.75, 2.95  # births that become its dot


class Flow:
    """Divergence-free noise: the curl of a sum of travelling sine waves, periodic in T."""

    def __init__(self, seed: int, modes: int, speed: float, shortest: float = 38.0, longest: float = 170.0):
        r = np.random.default_rng(seed)
        wavelength = np.exp(r.uniform(np.log(shortest), np.log(longest), modes))
        angle = r.uniform(0.0, 2.0 * np.pi, modes)
        k = 2.0 * np.pi / wavelength
        self.kx, self.ky = k * np.cos(angle), k * np.sin(angle)
        self.omega = 2.0 * np.pi / T * r.integers(1, 4, modes) * r.choice([-1, 1], modes)
        self.phase = r.uniform(0.0, 2.0 * np.pi, modes)
        self.amp = speed / np.sqrt(modes) / k * r.uniform(0.6, 1.4, modes)

    def __call__(self, x, y, t):
        arg = np.outer(x, self.kx) + np.outer(y, self.ky) + (self.omega * t + self.phase)
        c = np.cos(arg) * self.amp
        return c @ self.ky, -(c @ self.kx)


MAIN_FLOW = Flow(11, 12, 44.0)
STRAND_FLOWS = [Flow(100 + s, 8, 15.0, 18.0, 70.0) for s in range(STRANDS)]
STRAND_OFFSETS = np.random.default_rng(5).uniform(-0.7, 0.7, (STRANDS, 2))


def velocity(x, y, t, strand, age):
    height = np.maximum(0.0, TIP[1] - y)
    vx = -10.0 - 14.0 * smooth(height / 300.0)
    vy = -(44.0 + 32.0 * smooth(height / 160.0))
    turbulence = smooth((height - 8.0) / 110.0) ** 1.1
    tx, ty = MAIN_FLOW(x, y, t)
    sx, sy = STRAND_FLOWS[strand](x, y, t)
    spread = smooth((age - 0.15) / 1.2)
    vx = vx + turbulence * tx + spread * sx - 30.0 * smooth((x - 405.0) / 40.0)  # keep off the face
    vy = vy + turbulence * ty + spread * sy
    return vx, vy


def emission(birth):
    """Smoke density by birth time: a gap after the question mark and a thin wisp until 5.8 s."""
    b = np.mod(birth, T)
    return np.select(
        [(b >= Q_START) & (b < Q_END), (b >= Q_END) & (b < DOT_START), (b >= DOT_START) & (b < DOT_END), (b >= DOT_END) & (b < 4.6), (b >= 4.6) & (b < 5.8)],
        [1.25, 0.0, 1.2, 0.22, 0.22 + 0.78 * smooth((b - 4.6) / 1.2)],
        1.0,
    )


def simulate():
    """Positions and birth times of every live smoke particle at each frame, per strand."""
    steps = int(round((WARMUP + T) * SIM_FPS))
    births = -WARMUP + np.arange(steps) * DT
    alive_steps = int(LIFETIME * SIM_FPS)
    frames = [[None] * STRANDS for _ in range(FRAMES)]
    for strand in range(STRANDS):
        pos = np.zeros((steps, 2))
        for j in range(steps):
            t = births[j]
            pos[j] = TIP + STRAND_OFFSETS[strand]
            lo = max(0, j - alive_steps)
            p = pos[lo : j + 1]
            age = t - births[lo : j + 1]
            # Midpoint (RK2) step.
            vx, vy = velocity(p[:, 0], p[:, 1], t, strand, age)
            mx, my = p[:, 0] + 0.5 * DT * vx, p[:, 1] + 0.5 * DT * vy
            vx, vy = velocity(mx, my, t + 0.5 * DT, strand, age + 0.5 * DT)
            p[:, 0] += DT * vx
            p[:, 1] += DT * vy
            frame = (j + 1) - int(WARMUP * SIM_FPS)
            if frame >= 0 and frame % (SIM_FPS // FPS) == 0 and frame // (SIM_FPS // FPS) < FRAMES:
                frames[frame // (SIM_FPS // FPS)][strand] = (p.copy(), births[lo : j + 1].copy())
    return frames


# ---- question mark and graph (source px) ------------------------------------------------------

HOOK_CENTER = np.array([352.0, 640.0])
HOOK_RADIUS = 30.0
DOT = np.array([355.0, 733.0])


def _question_curve(samples: int = 2000):
    angles = np.radians([172, 205, 240, 270, 300, 330, 360, 390, 420])
    pts = [HOOK_CENTER + HOOK_RADIUS * np.array([np.cos(a), np.sin(a)]) for a in angles]
    pts += [np.array(p) for p in [(358.0, 674.0), (354.5, 685.0), (354.0, 698.0), (354.5, 711.0)]]
    pts = np.array(pts)
    padded = np.vstack([2 * pts[0] - pts[1], pts, 2 * pts[-1] - pts[-2]])
    dense = []
    for i in range(1, len(padded) - 2):  # Catmull-Rom
        p0, p1, p2, p3 = padded[i - 1 : i + 3]
        s = np.linspace(0, 1, 60, endpoint=False)[:, None]
        dense.append(0.5 * ((2 * p1) + (-p0 + p2) * s + (2 * p0 - 5 * p1 + 4 * p2 - p3) * s**2 + (-p0 + 3 * p1 - 3 * p2 + p3) * s**3))
    dense.append(pts[-1:])
    dense = np.vstack(dense)
    arc = np.concatenate([[0.0], np.cumsum(np.hypot(*np.diff(dense, axis=0).T))])
    u = np.linspace(0.0, 1.0, samples)
    curve = np.stack([np.interp(u * arc[-1], arc, dense[:, 0]), np.interp(u * arc[-1], arc, dense[:, 1])], axis=1)
    tangent = np.gradient(curve, axis=0)
    tangent /= np.linalg.norm(tangent, axis=1, keepdims=True)
    return curve, np.stack([-tangent[:, 1], tangent[:, 0]], axis=1)


Q_CURVE, Q_NORMAL = _question_curve()


def question_point(u, t):
    i = np.clip((np.asarray(u) * (len(Q_CURVE) - 1)).round().astype(int), 0, len(Q_CURVE) - 1)
    wobble = 1.4 * np.stack([np.sin(2 * np.pi * (1.6 * u + 0.33 * t) + 0.7), np.sin(2 * np.pi * (1.2 * u - 0.29 * t) + 2.1)], axis=-1)
    drift = np.array([-1.5, -6.0]) * max(0.0, t - 2.6)
    return Q_CURVE[i] + wobble + drift, Q_NORMAL[i]


def question_weight(u, t):
    """How far a question-mark particle has left the free flow (hook forms first, leaves first)."""
    form = ramp(t, 2.05 + 0.3 * u, 2.75 + 0.3 * u)
    release = 1.0 - ramp(t, 3.45 + 0.25 * (1.0 - u), 4.15 + 0.25 * (1.0 - u))
    return form * release


GRAPH_FROM = 3.85
NODE_U = [0.0, 0.13, 0.27, 0.40, 0.53, 0.66, 0.79, 0.90, 1.0]
SATELLITES = [(296, 598), (318, 562), (288, 660), (316, 714), (398, 708), (386, 576), (262, 626)]


def _graph():
    start, _ = question_point(np.array(NODE_U), GRAPH_FROM)
    nodes = list(start) + [DOT.copy()] + [np.array(p, float) for p in SATELLITES]
    kinds = ["dot" if i % 2 == 0 else "ring" for i in range(len(NODE_U))] + ["focus"]
    kinds += ["ring" if i % 2 == 0 else "dot" for i in range(len(SATELLITES))]
    nodes = np.array(nodes)
    chain = len(NODE_U)
    edges = {(i, i + 1) for i in range(chain)}  # along the question mark, into its dot
    for s in range(chain + 1, len(nodes)):
        d = np.hypot(*(nodes - nodes[s]).T)
        d[s] = np.inf
        for j in np.argsort(d)[:2]:
            edges.add((min(s, int(j)), max(s, int(j))))
    return nodes, kinds, sorted(edges)


NODES, NODE_KINDS, EDGES = _graph()
NODE_APPEAR = GRAPH_FROM + 0.045 * np.arange(len(NODES))


def node_positions(t):
    dt = t - GRAPH_FROM
    wobble = 0.8 * np.stack([np.sin(1.7 * t + np.arange(len(NODES))), np.cos(1.3 * t + 2 * np.arange(len(NODES)))], axis=1)
    return NODES + np.array([-3.0, -8.0]) * dt + (NODES - HOOK_CENTER) * 0.012 * dt + wobble


# ---- rendering --------------------------------------------------------------------------------

CENTER = np.array([575.0 * KX, 640.0 * KY])  # the camera pushes in on the face
SMOKE_TONE = 135.0
GRAPHITE = 60.0


def to_screen(points, scale):
    p = np.asarray(points, float) * np.array([KX, KY])
    return CENTER + (p - CENTER) * scale


def splat(grid, pts, w):
    """Add weights at sub-pixel points (bilinear) into a flat H×W grid."""
    x, y = pts[:, 0], pts[:, 1]
    x0, y0 = np.floor(x).astype(int), np.floor(y).astype(int)
    fx, fy = x - x0, y - y0
    for dx, dy, share in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)), (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < OUT_W) & (yi >= 0) & (yi < OUT_H)
        grid += np.bincount(yi[ok] * OUT_W + xi[ok], weights=(w * share)[ok], minlength=OUT_W * OUT_H)


def densify(points, weights, step=0.5, longest=22.0):
    """Samples every `step` px along a polyline; skips long jumps and segments touching zero weight."""
    seg = points[1:] - points[:-1]
    length = np.hypot(seg[:, 0], seg[:, 1])
    ok = (length <= longest) & (weights[:-1].max(axis=1) > 0) & (weights[1:].max(axis=1) > 0)
    idx = np.nonzero(ok)[0]
    if len(idx) == 0:
        return np.zeros((0, 2)), np.zeros((0, weights.shape[1]))
    n = np.maximum(1, np.ceil(length[idx] / step).astype(int))
    rep = np.repeat(idx, n)
    count = np.repeat(n, n)
    f = (np.arange(n.sum()) - np.repeat(np.cumsum(n) - n, n) + 0.5) / count
    pts = points[rep] + seg[rep] * f[:, None]
    wts = weights[rep] * (1 - f)[:, None] + weights[rep + 1] * f[:, None]
    return pts, wts * (length[rep] / count)[:, None]


def smoke_alpha(record, t, scale):
    core = np.zeros(OUT_H * OUT_W)
    haze = np.zeros(OUT_H * OUT_W)
    old_fade = 1.0 - 0.85 * ramp(t, 2.2, 2.8) * (1.0 - ramp(t, 3.8, 4.4))
    q_fade = 1.0 - ramp(t, 3.75, 4.55)
    for strand in range(STRANDS):
        pos, birth = record[strand]
        age = t - birth
        e = emission(birth)
        core_w = e * (1.0 - smooth((age - 0.6) / 2.6))
        haze_w = e * smooth((age - 0.2) / 1.4) * (1.0 - smooth((age - 2.4) / 2.2))
        pos = pos.copy()
        older = birth < Q_START
        core_w = np.where(older, core_w * old_fade, core_w)
        haze_w = np.where(older, haze_w * old_fade, haze_w)
        # The question mark and its dot: particles leave the free flow for the shape, then return.
        q = (birth >= Q_START) & (birth < Q_END)
        d = (birth >= DOT_START) & (birth < DOT_END)
        for mask, is_dot in ((q, False), (d, True)):
            if not mask.any():
                continue
            if is_dot:
                j = np.arange(mask.sum())
                radius = 4.2 * np.sqrt((j + 0.5) / len(j))
                angle = 2.39996 * j + 0.7 * strand
                drift = np.array([-1.5, -6.0]) * max(0.0, t - 2.6)
                target = DOT + drift + np.stack([radius * np.cos(angle), radius * np.sin(angle)], axis=1)
                beta = question_weight(1.0, t) * np.ones(len(j))
                taper = np.ones(len(j))
                glow = np.full(len(j), 0.6)
            else:
                u = (birth[mask] - Q_START) / (Q_END - Q_START)
                target, normal = question_point(u, t)
                # A ribbon of filaments that braid and vary in density, not a pen stroke.
                offset = np.linspace(-3.6, 3.6, STRANDS)[strand] * (0.45 + 0.55 * np.sin(np.pi * u) ** 0.5)
                offset = offset + 1.0 * np.sin(2 * np.pi * (2.3 * u + 0.5 * t) + 1.3 * strand)
                target = target + normal * offset[:, None]
                beta = question_weight(u, t)
                taper = smooth(u / 0.06) * (0.72 + 0.28 * np.sin(2 * np.pi * (2.7 * u - 0.45 * t) + 0.9 * strand))
                glow = np.full(len(u), 0.55)
            pos[mask] = (1.0 - beta)[:, None] * pos[mask] + beta[:, None] * target
            core_w[mask] = e[mask] * q_fade * taper * np.maximum(core_w[mask] / np.maximum(e[mask], 1e-6), beta)
            haze_w[mask] = (haze_w[mask] * (1.0 - beta) + e[mask] * glow * beta) * q_fade
        pts, wts = densify(to_screen(pos, scale), np.stack([core_w, haze_w], axis=1))
        if len(pts):
            splat(core, pts, wts[:, 0])
            splat(haze, pts, wts[:, 1])
    core = core.reshape(OUT_H, OUT_W)
    sharp = ndimage.gaussian_filter(core, 0.6)
    soft = ndimage.gaussian_filter(core, 1.6)
    haze = ndimage.gaussian_filter(haze.reshape(OUT_H, OUT_W), 3.4)
    return 1.0 - np.exp(-(0.24 * sharp + 0.22 * soft + 0.34 * haze))


def graph_alpha(t, scale):
    if not GRAPH_FROM - 0.05 <= t <= 6.8:
        return None
    nodes = to_screen(node_positions(t), scale)
    lines = np.zeros(OUT_H * OUT_W)
    for k, (a, b) in enumerate(EDGES):
        start = max(NODE_APPEAR[a], NODE_APPEAR[b]) + 0.08
        progress = float(smooth((t - start) / 0.4))
        fade = 1.0 - float(ramp(t, 5.45 + 0.02 * k, 6.1 + 0.02 * k))
        if progress <= 0 or fade <= 0:
            continue
        pa, pb = nodes[a], nodes[b]
        length = np.hypot(*(pb - pa))
        normal = np.array([-(pb - pa)[1], (pb - pa)[0]]) / max(length, 1e-6)
        s = np.linspace(0.0, progress, max(2, int(length * progress / 0.35)))[:, None]
        bend = 0.04 * length * (1 if k % 2 else -1)
        pts = pa + (pb - pa) * s + normal * (bend * 4 * s * (1 - s))  # a slight hand-drawn bow
        pressure = 0.8 + 0.2 * np.sin(s[:, 0] * length * 0.21 + k)  # graphite texture
        splat(lines, pts, 0.16 * fade * pressure)
    lines = ndimage.gaussian_filter(lines.reshape(OUT_H, OUT_W), 0.45)
    alpha = 1.0 - np.exp(-1.6 * lines)
    yy, xx = np.mgrid[0:OUT_H, 0:OUT_W]
    for i, (p, kind) in enumerate(zip(nodes, NODE_KINDS)):
        grow = float(smooth((t - NODE_APPEAR[i]) / 0.3))
        fade = 1.0 - float(ramp(t, 5.7 + 0.03 * i, 6.45 + 0.03 * i))
        strength = grow * fade
        if strength <= 0:
            continue
        r = (0.55 + 0.45 * grow) * scale * KX
        x0, y0 = int(p[0]) - 14, int(p[1]) - 14
        box = (slice(max(0, y0), y0 + 29), slice(max(0, x0), x0 + 29))
        dist = np.hypot(xx[box] - p[0], yy[box] - p[1])
        if kind == "dot":
            a = np.clip(2.0 * r - dist + 0.5, 0, 1) * 0.62
        elif kind == "ring":
            a = np.clip(0.55 * r - np.abs(dist - 2.9 * r) + 0.5, 0, 1) * 0.6
        else:  # the question's dot becomes the conclusion: a filled node in a ring
            a = np.maximum(np.clip(3.4 * r - dist + 0.5, 0, 1) * 0.7, np.clip(0.5 * r - np.abs(dist - 6.2 * r) + 0.5, 0, 1) * 0.5)
        alpha[box] = 1.0 - (1.0 - alpha[box]) * (1.0 - a * strength)
    return alpha


class Renderer:
    def __init__(self):
        portrait = Image.open(SOURCE).convert("L").resize((OUT_W, OUT_H), Image.LANCZOS)
        self.coeffs = ndimage.spline_filter(np.asarray(portrait, np.float64), order=3)
        self.grid_y, self.grid_x = np.mgrid[0:OUT_H, 0:OUT_W].astype(np.float64)
        self.smoke = simulate()

    def _sample(self, sx, sy, t):
        """Portrait values at screen points (camera, then face rig)."""
        scale = camera_scale(t)
        wx = (CENTER[0] + (sx - CENTER[0]) / scale) / KX
        wy = (CENTER[1] + (sy - CENTER[1]) / scale) / KY
        ex, ey = eye_map(wx, wy, keyed(t, GAZE), min(1.0, 0.3 * keyed(t, SQUINT) + keyed(t, BLINK)), 0.13 * keyed(t, SQUINT))
        bx, by = brow_shift(ex, ey, keyed(t, BROW))
        return ndimage.map_coordinates(self.coeffs, [(ey - by) * KY, (ex - bx) * KX], order=3, prefilter=False, mode="nearest")

    def frame(self, index: int, grain: bool = True) -> np.ndarray:
        t = index / FPS
        scale = camera_scale(t)
        image = self._sample(self.grid_x, self.grid_y, t)
        # Supersample the eyes 3×3: the lids move across them with hard edges.
        for x0, y0, x1, y1 in EYE_BOXES:
            (a0, b0), (a1, b1) = to_screen([(x0, y0), (x1, y1)], scale)
            ys, xs = slice(int(b0), int(np.ceil(b1))), slice(int(a0), int(np.ceil(a1)))
            gy, gx = self.grid_y[ys, xs], self.grid_x[ys, xs]
            acc = np.zeros_like(gy)
            for oy in (-1 / 3, 0, 1 / 3):
                for ox in (-1 / 3, 0, 1 / 3):
                    acc += self._sample(gx + ox, gy + oy, t)
            image[ys, xs] = acc / 9.0
        smoke = smoke_alpha(self.smoke[index], t, scale)
        image = image * (1.0 - smoke) + SMOKE_TONE * smoke
        graph = graph_alpha(t, scale)
        if graph is not None:
            image = image * (1.0 - graph) + GRAPHITE * graph
        if grain:
            noise = ndimage.gaussian_filter(np.random.default_rng(1000 + index).standard_normal((OUT_H, OUT_W)), 0.6)
            noise /= noise.std()
            image = image + noise * 7.5 * np.clip(1.0 - image / 255.0, 0.0, 1.0) ** 0.65
        return np.clip(image + 0.5, 0, 255).astype(np.uint8)


def main(argv: list[str]) -> None:
    mode = argv[1] if len(argv) > 1 else "stills"
    renderer = Renderer()
    if mode == "stills":
        out = HERE / "stills"
        out.mkdir(exist_ok=True)
        for value in argv[2:] or ["0"]:
            index = int(round(float(value) * FPS)) % FRAMES
            Image.fromarray(renderer.frame(index)).save(out / f"t{index / FPS:05.2f}.png")
            print(f"still t={index / FPS:.2f}s", file=sys.stderr)
        return
    for index in range(FRAMES):
        pixels = renderer.frame(index)
        if mode == "raw":
            sys.stdout.buffer.write(pixels.tobytes())
        else:
            buffer = io.BytesIO()
            Image.fromarray(pixels).save(buffer, "JPEG", quality=96, subsampling=0)
            sys.stdout.buffer.write(buffer.getvalue())
        print(f"frame {index + 1}/{FRAMES}", file=sys.stderr, end="\r")
    print(file=sys.stderr)


if __name__ == "__main__":
    main(sys.argv)
