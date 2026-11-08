"""
MOONLIT NIGHT
"""
import math
import random
import time
from collections import deque
import tkinter as tk

# ------------------------------- Config --------------------------------
W, H = 960, 640
HORIZON = 400
LAKE_BOTTOM = 520
TAG = "dyn"

KA, KD = 0.10, 0.95
MOON_Z = 260.0          # artistic: the moon floats a bit towards the viewer so objects are lit
FIREFLY_RATIOS = [(1, 2), (2, 3), (3, 2), (3, 4), (1, 3), (4, 5), (5, 4), (3, 5)]


# ------------------------------- Math helpers ---------------------------
def clamp(v, lo=0.0, hi=1.0):
    return max(lo, min(hi, v))


def to_hex(c):
    return "#%02x%02x%02x" % (int(clamp(c[0], 0, 255)), int(clamp(c[1], 0, 255)),
                              int(clamp(c[2], 0, 255)))


def lerp(c1, c2, t):
    return tuple(c1[i] + (c2[i] - c1[i]) * t for i in range(3))


def dot(a, b):
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def normalize(v):
    length = math.sqrt(dot(v, v)) or 1.0
    return (v[0] / length, v[1] / length, v[2] / length)


def mat_mul(A, B):
    n, m, k = len(A), len(B[0]), len(B)
    return [[sum(A[i][t] * B[t][j] for t in range(k)) for j in range(m)] for i in range(n)]


def mv3(M, v):
    return (M[0][0] * v[0] + M[0][1] * v[1] + M[0][2] * v[2],
            M[1][0] * v[0] + M[1][1] * v[1] + M[1][2] * v[2],
            M[2][0] * v[0] + M[2][1] * v[1] + M[2][2] * v[2])


def rot_x(a):
    c, s = math.cos(a), math.sin(a)
    return [[1, 0, 0], [0, c, -s], [0, s, c]]


def rot_y(a):
    c, s = math.cos(a), math.sin(a)
    return [[c, 0, s], [0, 1, 0], [-s, 0, c]]


def tf_matrix(tx, ty, ang=0.0, sx=1.0, sy=1.0):
    """2D homogeneous transform  M = T * R * S."""
    c, s = math.cos(ang), math.sin(ang)
    T = [[1, 0, tx], [0, 1, ty], [0, 0, 1]]
    R = [[c, -s, 0], [s, c, 0], [0, 0, 1]]
    S = [[sx, 0, 0], [0, sy, 0], [0, 0, 1]]
    return mat_mul(mat_mul(T, R), S)


def apply(M, pt):
    return (M[0][0] * pt[0] + M[0][1] * pt[1] + M[0][2],
            M[1][0] * pt[0] + M[1][1] * pt[1] + M[1][2])


def poly(M, pts):
    out = []
    for p in pts:
        x, y = apply(M, p)
        out += [x, y]
    return out


def sky_color(y):
    return lerp((4, 7, 22), (32, 50, 96), clamp(y / HORIZON) ** 1.3)


def lake_color(y):
    return lerp((22, 36, 70), (5, 10, 26), clamp((y - HORIZON) / (LAKE_BOTTOM - HORIZON)))


# ------------------------------- Sphere meshes --------------------------
CRATERS = [((0.35, 0.30, 0.89), 0.62), ((-0.40, 0.15, 0.90), 0.55), ((0.05, -0.45, 0.89), 0.50),
           ((0.55, -0.25, 0.79), 0.35), ((-0.15, 0.62, 0.77), 0.30), ((-0.60, -0.30, 0.74), 0.30)]


def build_mesh(nl, nlo, seed):
    rnd = random.Random(seed)
    verts, faces, moonf, rockf = [], [], [], []
    for i in range(nl + 1):
        th = math.pi * i / nl
        for j in range(nlo + 1):
            ph = 2 * math.pi * j / nlo
            verts.append((math.sin(th) * math.cos(ph), math.cos(th), math.sin(th) * math.sin(ph)))
    craters = [(normalize(d), r) for d, r in CRATERS]
    for i in range(nl):
        for j in range(nlo):
            a = i * (nlo + 1) + j
            f = (a, a + nlo + 1, a + nlo + 2, a + 1)
            faces.append(f)
            cen = normalize((sum(verts[k][0] for k in f), sum(verts[k][1] for k in f),
                             sum(verts[k][2] for k in f)))
            fac = 1.0
            for d, r in craters:                      # dark "maria" patches on the moon
                ang = math.acos(clamp(dot(cen, d), -1, 1))
                if ang < r:
                    fac = min(fac, 1 - 0.5 * (1 - ang / r))
            moonf.append(fac)
            rockf.append(rnd.uniform(0.72, 1.0))      # rough rock facets for the stones
    return verts, faces, moonf, rockf


COARSE = build_mesh(8, 12, 1)    # Flat + Gouraud
FINE = build_mesh(12, 20, 2)     # Phong
POOL = 240


class Sphere:
    """A pooled, persistent set of polygons so the shaded sphere is cheap to redraw."""

    def __init__(self, canvas):
        self.c = canvas
        self.items = [canvas.create_polygon(0, 0, 0, 0, 0, 0, state="hidden") for _ in range(POOL)]
        self.used = 0

    def hide(self):
        for k in range(self.used):
            self.c.itemconfigure(self.items[k], state="hidden")
        self.used = 0

    def render(self, cx, cy, r, Rm, L, base, ka, kd, ks, shine, mode, which, gain=1.0):
        c = self.c
        verts, faces, moonf, rockf = FINE if mode == "phong" else COARSE
        pats = moonf if which == "moon" else rockf
        RV = [mv3(Rm, v) for v in verts]             # unit sphere: position == normal
        Hh = normalize((L[0], L[1], L[2] + 1.0))     # Blinn half-vector (viewer on +z)

        def shade(n):
            ndl = dot(n, L)
            if ndl < 0:
                ndl = 0.0
            spec = 0.0
            if ndl > 0 and ks > 0:
                ndh = dot(n, Hh)
                if ndh > 0:
                    spec = ndh ** shine
            return ka + kd * ndl * gain, 255 * ks * spec * gain

        used, vcache = 0, {}
        for fi, f in enumerate(faces):
            v0, v1, v2, v3 = RV[f[0]], RV[f[1]], RV[f[2]], RV[f[3]]
            nz = v0[2] + v1[2] + v2[2] + v3[2]
            if nz <= 0:
                continue                               # back-face culling
            if mode == "gouraud":
                kk = sp = 0.0
                for vi in f:
                    if vi not in vcache:
                        vcache[vi] = shade(RV[vi])
                    kk += vcache[vi][0]
                    sp += vcache[vi][1]
                kk /= 4
                sp /= 4
            else:   # flat: true face normal.  phong: interpolated normal on a finer mesh
                kk, sp = shade(normalize((v0[0] + v1[0] + v2[0] + v3[0],
                                          v0[1] + v1[1] + v2[1] + v3[1], nz)))
            pf = pats[fi]
            col = to_hex((base[0] * kk * pf + sp, base[1] * kk * pf + sp, base[2] * kk * pf + sp))
            it = self.items[used]
            c.coords(it, cx + v0[0] * r, cy - v0[1] * r, cx + v1[0] * r, cy - v1[1] * r,
                     cx + v2[0] * r, cy - v2[1] * r, cx + v3[0] * r, cy - v3[1] * r)
            c.itemconfigure(it, fill=col, outline=col, state="normal")
            used += 1
        for k in range(used, self.used):
            c.itemconfigure(self.items[k], state="hidden")
        self.used = used


# ------------------------------- Scene ----------------------------------
class Scene:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Moonlit Night")
        self.root.resizable(False, False)
        self.c = tk.Canvas(self.root, width=W, height=H, bg="#04071a", highlightthickness=0)
        self.c.pack()
        self.rnd = random.Random(11)
        self.build_static()

        # persistent items: lissajous paths, moon halo, shaded spheres
        self.paths = []
        self.moon_path = self.c.create_line(0, 0, 0, 0, fill="#1a2a4a", width=1, dash=(3, 5))
        self.paths.append(self.moon_path)
        pts = []
        for k in range(241):
            tt = 2 * math.pi * k / 240
            pts += [480 + 330 * math.sin(tt + math.pi / 5), 150 + 85 * math.sin(2 * tt)]
        self.c.coords(self.moon_path, *pts)

        self.fireflies = []
        for _ in range(9):
            a, b = self.rnd.choice(FIREFLY_RATIOS)
            f = dict(cx=self.rnd.uniform(110, W - 110), cy=self.rnd.uniform(440, 560),
                     ax=self.rnd.uniform(50, 130), ay=self.rnd.uniform(18, 48), a=a, b=b,
                     d=self.rnd.uniform(0, 6.28), t=self.rnd.uniform(0, 6.28),
                     sp=self.rnd.uniform(0.35, 0.7), ph=self.rnd.uniform(0, 6.28),
                     trail=deque(maxlen=16))
            f["ax"] = min(f["ax"], f["cx"] - 40, W - f["cx"] - 40)
            pts = []
            for k in range(181):
                tt = 2 * math.pi * k / 180
                pts += [f["cx"] + f["ax"] * math.sin(a * tt + f["d"]),
                        f["cy"] + f["ay"] * math.sin(b * tt)]
            f["path"] = self.c.create_line(pts, fill="#1d2b1c", width=1)
            self.paths.append(f["path"])
            self.fireflies.append(f)

        self.halo = [self.c.create_oval(0, 0, 0, 0, outline="") for _ in range(5)]
        self.moon = Sphere(self.c)
        self.stones = [dict(x=300, y=588, r=42, rx=0.5, ry=0.0, vrx=0.35, vry=0.5, s=Sphere(self.c)),
                       dict(x=668, y=598, r=33, rx=1.2, ry=2.0, vrx=-0.4, vry=0.3, s=Sphere(self.c))]

        # world state
        self.mode = "gouraud"
        self.clock = 0.0
        self.tm = 0.0
        self.moon_manual = None
        self.phase = 50.0            # degrees: 0 = full moon, 180 = new moon
        self.auto_phase = False
        self.paused = False
        self.show_paths = True
        self.blade = 0.0
        self.tb = 0.0
        self.smoke, self.smoke_cd = [], 0.0
        self.star = None
        self.next_star = 3.0
        self.stars = [(self.rnd.uniform(0, W), self.rnd.uniform(0, HORIZON - 30) ** 1 * 0.97,
                       self.rnd.uniform(0, 6.28), self.rnd.choice((1, 1, 2)))
                      for _ in range(85)]
        self.big_stars = [(self.rnd.uniform(40, W - 40), self.rnd.uniform(20, 300),
                           self.rnd.uniform(0, 6.28), self.rnd.uniform(5, 9)) for _ in range(14)]
        self.clouds = [dict(x=120, y=210, sp=16, ph=0.3, sc=1.1),
                       dict(x=520, y=120, sp=11, ph=1.7, sc=0.9),
                       dict(x=830, y=265, sp=21, ph=3.1, sc=1.3)]
        self.blobs = [(-46, 6, 40, 15), (-14, -8, 38, 20), (22, -4, 40, 19), (52, 6, 34, 14),
                      (4, 8, 60, 13)]

        r = self.root
        r.bind("<KeyPress>", self.on_key)
        self.c.bind("<ButtonPress-1>", self.on_click)
        self.c.bind("<B1-Motion>", self.on_click)
        self.last = time.perf_counter()
        self.tick()

    # ---------------------------- static background ----------------------------
    def build_static(self):
        c = self.c
        for y in range(0, HORIZON, 8):
            c.create_rectangle(0, y, W, y + 9, fill=to_hex(sky_color(y + 4)), outline="")
        far = [(0, 400), (0, 352), (90, 332), (200, 346), (320, 322), (450, 340), (580, 326),
               (700, 346), (830, 318), (960, 342), (960, 400)]
        near = [(0, 400), (0, 372), (130, 352), (260, 372), (420, 358), (600, 376), (760, 350),
                (900, 368), (960, 360), (960, 400)]
        c.create_polygon(far, fill="#0c1530", outline="")
        c.create_polygon(near, fill="#080f22", outline="")
        for y in range(HORIZON, LAKE_BOTTOM, 6):
            c.create_rectangle(0, y, W, y + 7, fill=to_hex(lake_color(y + 3)), outline="")
        # trees on the near hill (static silhouettes)
        for tx, ty, s in ((40, 378, 1.0), (72, 372, 1.3), (330, 372, 0.9), (360, 376, 1.2),
                          (620, 380, 1.1), (905, 366, 1.2), (935, 364, 1.0), (880, 368, 0.8)):
            for k in range(3):
                w = (24 - k * 6) * s
                c.create_polygon(tx - w, ty - k * 15 * s, tx + w, ty - k * 15 * s,
                                 tx, ty - (k + 1.2) * 17 * s, fill="#060c1c", outline="")
            c.create_rectangle(tx - 2 * s, ty, tx + 2 * s, ty + 8 * s, fill="#060c1c", outline="")
        ground = [(0, 640), (0, 530), (80, 520), (200, 528), (340, 518), (480, 526), (620, 516),
                  (760, 526), (880, 518), (960, 524), (960, 640)]
        c.create_polygon(ground, fill="#09151c", outline="")
        for i in range(60):   # grass tufts
            gx = self.rnd.uniform(0, W)
            gy = self.rnd.uniform(530, 635)
            c.create_line(gx, gy, gx - 3, gy - 9, fill="#10242a")
            c.create_line(gx, gy, gx + 3, gy - 8, fill="#10242a")

    # ---------------------------- input ----------------------------
    def on_key(self, e):
        k = e.keysym.lower()
        if k == "escape":
            self.root.destroy()
        elif k == "1":
            self.mode = "flat"
        elif k == "2":
            self.mode = "gouraud"
        elif k == "3":
            self.mode = "phong"
        elif k == "left":
            self.phase = (self.phase - 10) % 360
        elif k == "right":
            self.phase = (self.phase + 10) % 360
        elif k == "f":
            self.auto_phase = not self.auto_phase
        elif k == "m":
            self.moon_manual = None
        elif k == "p":
            self.paused = not self.paused
        elif k == "t":
            self.show_paths = not self.show_paths
            for p in self.paths:
                self.c.itemconfigure(p, state="normal" if self.show_paths else "hidden")

    def on_click(self, e):
        self.moon_manual = (clamp(e.x, 40, W - 40), clamp(e.y, 50, 370))

    # ---------------------------- helpers ----------------------------
    def moon_pos(self):
        if self.moon_manual:
            return self.moon_manual
        t = self.tm
        return 480 + 330 * math.sin(t + math.pi / 5), 150 + 85 * math.sin(2 * t)

    def moon_radius(self, my):
        return 46 * (0.9 + 0.45 * clamp((my - 65) / 170))     # moon grows near the horizon

    def illum(self):
        return clamp((1 + math.cos(math.radians(self.phase))) / 2, 0.12, 1.0)

    # ---------------------------- update ----------------------------
    def update(self, dt):
        if self.paused:
            return
        self.clock += dt
        self.tb += dt
        self.blade += dt * 0.9
        if self.moon_manual is None:
            self.tm += dt * 0.09
        if self.auto_phase:
            self.phase = (self.phase + 12 * dt) % 360
        for s in self.stones:
            s["rx"] += s["vrx"] * dt
            s["ry"] += s["vry"] * dt
        for f in self.fireflies:
            f["t"] += dt * f["sp"]
            x = f["cx"] + f["ax"] * math.sin(f["a"] * f["t"] + f["d"])
            y = f["cy"] + f["ay"] * math.sin(f["b"] * f["t"])
            f["x"], f["y"] = x, y
            f["trail"].append((x, y))
        for cl in self.clouds:
            cl["x"] += cl["sp"] * dt
            if cl["x"] > W + 160:
                cl["x"] = -160
        self.smoke_cd -= dt
        if self.smoke_cd <= 0:
            self.smoke_cd = 0.5
            self.smoke.append([172.0, 336.0, 0.0])
        for p in self.smoke[:]:
            p[2] += dt
            p[0] += (14 + 10 * math.sin(p[2] * 2.2)) * dt
            p[1] -= 26 * dt
            if p[2] > 3.6:
                self.smoke.remove(p)
        self.next_star -= dt
        if self.next_star <= 0 and self.star is None:
            a = random.uniform(0.35, 0.7)
            self.star = [random.uniform(80, 700), random.uniform(25, 140),
                         math.cos(a) * 650, math.sin(a) * 650, 0.9, a]
            self.next_star = random.uniform(4, 8)
        if self.star:
            st = self.star
            st[0] += st[2] * dt
            st[1] += st[3] * dt
            st[4] -= dt
            if st[4] <= 0:
                self.star = None

    # ---------------------------- render ----------------------------
    def render(self):
        c = self.c
        c.delete(TAG)
        mx, my = self.moon_pos()
        mr = self.moon_radius(my)
        ill = self.illum()
        mode = self.mode
        moonlight = (200, 215, 255)

        # --- halo (persistent items below the moon sphere)
        for i, k in enumerate((3.0, 2.4, 1.9, 1.5, 1.2)):
            rr = mr * k
            col = lerp(sky_color(my), (120, 140, 205), (0.10 + 0.13 * i) * ill)
            c.coords(self.halo[i], mx - rr, my - rr, mx + rr, my + rr)
            c.itemconfigure(self.halo[i], fill=to_hex(col))

        # --- moon: lit by the "sun" whose direction is set by the phase; libration = rotation
        a = math.radians(self.phase)
        Lm = normalize((math.sin(a), 0.2, math.cos(a)))
        Rm = mat_mul(rot_y(0.22 * math.sin(self.clock * 0.3)), rot_x(0.10 * math.sin(self.clock * 0.2)))
        self.moon.render(mx, my, mr, Rm, Lm, (238, 234, 214), 0.05, 1.0, 0.0, 10, mode, "moon")

        # --- stones lit by the moonlight (point light at the moon position)
        for s in self.stones:
            L = normalize((mx - s["x"], -(my - s["y"]), MOON_Z))
            Rs = mat_mul(rot_y(s["ry"]), rot_x(s["rx"]))
            s["s"].render(s["x"], s["y"], s["r"], Rs, L, (122, 136, 172), KA, KD, 0.55, 30,
                          mode, "rock", gain=0.25 + 0.75 * ill)

        # --- twinkling stars (scaling + rotation via M = T*R*S)
        halo_r = mr * 3.0
        for (x, y, ph, sz) in self.stars:
            if math.hypot(x - mx, y - my) < halo_r:
                continue
            b = 0.35 + 0.65 * (0.5 + 0.5 * math.sin(self.clock * 2.2 + ph))
            col = to_hex(lerp(sky_color(y), (235, 240, 255), b))
            c.create_oval(x, y, x + sz, y + sz, fill=col, outline="", tags=TAG)
        star_shape = [(0, -1), (0.22, -0.22), (1, 0), (0.22, 0.22), (0, 1), (-0.22, 0.22),
                      (-1, 0), (-0.22, -0.22)]
        for (x, y, ph, sz) in self.big_stars:
            if math.hypot(x - mx, y - my) < halo_r:
                continue
            sc = sz * (0.7 + 0.4 * math.sin(self.clock * 1.8 + ph))
            M = tf_matrix(x, y, self.clock * 0.4 + ph, sc, sc)
            c.create_polygon(poly(M, star_shape), fill="#f4f6ff", outline="", tags=TAG)

        if self.star:
            st = self.star
            fade = clamp(st[4] / 0.9)
            ex, ey = st[0] - math.cos(st[5]) * 110, st[1] - math.sin(st[5]) * 110
            c.create_line(ex, ey, st[0], st[1], fill=to_hex(lerp((20, 30, 60), (255, 255, 255), fade)),
                          width=2, tags=TAG)
            c.create_oval(st[0] - 3, st[1] - 3, st[0] + 3, st[1] + 3, fill="#ffffff", outline="",
                          tags=TAG)

        # --- clouds: drift (translation), bob on a Lissajous-like path, pulse (scaling), moon-lit
        for cl in self.clouds:
            y = cl["y"] + 9 * math.sin(self.clock * 0.4 + cl["ph"])
            sc = cl["sc"] * (1 + 0.03 * math.sin(self.clock * 0.7 + cl["ph"]))
            d = math.hypot(cl["x"] - mx, y - my)
            k = clamp(1 - d / 380) * ill
            col = to_hex(lerp((20, 30, 58), (150, 170, 220), k))
            rim = to_hex(lerp((26, 38, 70), (200, 215, 245), k))
            for (dx, dy, rx, ry) in self.blobs:
                x0, y0 = cl["x"] + dx * sc, y + dy * sc
                c.create_oval(x0 - rx * sc, y0 - ry * sc, x0 + rx * sc, y0 + ry * sc, fill=col,
                              outline="", tags=TAG)
            c.create_line(cl["x"] - 55 * sc, y - 4 * sc, cl["x"] + 45 * sc, y - 4 * sc, fill=rim,
                          width=2, tags=TAG)

        # --- moon reflection on the lake: x = A sin(a t + k*d) shimmer (Lissajous-like)
        if ill > 0.12 and my < 380:
            for k in range(11):
                y = HORIZON + 10 + k * 10.5
                wob = (8 + k * 1.7) * math.sin(2.1 * self.clock + k * 0.8)
                w = (30 + k * 2.6) * (0.65 + 0.35 * math.sin(3.0 * self.clock + k * 1.3 + 1)) * (mr / 46)
                t = k / 11
                col = lerp(lake_color(y), moonlight, (1 - t * 0.8) * ill * 0.9)
                c.create_oval(mx + wob - w, y - 2.2, mx + wob + w, y + 2.2, fill=to_hex(col),
                              outline="", tags=TAG)

        self.draw_windmill(ill)
        self.draw_cottage(ill)
        self.draw_boat(ill)

        # --- chimney smoke: translation + growing scale + fade
        for p in self.smoke:
            age = p[2] / 3.6
            r = 4 + age * 22
            col = to_hex(lerp((70, 82, 118), sky_color(p[1]), age))
            c.create_oval(p[0] - r, p[1] - r, p[0] + r, p[1] + r, fill=col, outline="", tags=TAG)

        # --- fireflies: Lissajous flight paths with fading trails and flickering glow
        for f in self.fireflies:
            tr = list(f["trail"])
            n = len(tr)
            if n > 3:
                for seg in range(3):
                    chunk = tr[seg * 5: seg * 5 + 6]
                    if len(chunk) >= 2:
                        col = to_hex(lerp((9, 21, 28), (150, 200, 90), (seg + 1) / 3 * 0.55))
                        c.create_line([v for p in chunk for v in p], fill=col, width=2, tags=TAG)
            fl = 0.55 + 0.45 * math.sin(self.clock * 5 + f["ph"])
            x, y = f["x"], f["y"]
            for rad, col in ((11 * fl + 3, (30, 52, 22)), (6 * fl + 2, (90, 140, 50))):
                c.create_oval(x - rad, y - rad, x + rad, y + rad, fill=to_hex(col), outline="",
                              tags=TAG)
            c.create_oval(x - 2, y - 2, x + 2, y + 2, fill="#f4ffb0", outline="", tags=TAG)

        if self.paused:
            c.create_text(W / 2, H / 2, text="PAUSED", fill="#ffffff",
                          font=("Segoe UI", 34, "bold"), tags=TAG)

    # ----- scene objects drawn each frame
    def draw_windmill(self, ill):
        c = self.c
        hx, hy = 790, 300
        body = (8, 13, 28)
        edge = to_hex(lerp((22, 32, 58), (110, 130, 185), ill * 0.8))
        c.create_polygon(768, 396, 812, 396, 800, 300, 780, 300, fill=to_hex(body), outline=edge,
                         width=1, tags=TAG)
        c.create_polygon(774, 300, 806, 300, 790, 276, fill=to_hex(body), outline=edge, tags=TAG)
        blade = [(10, -4), (96, -4), (96, 12), (10, 12)]
        for k in range(4):
            M = tf_matrix(hx, hy, self.blade + k * math.pi / 2)    # rotation about the hub
            c.create_polygon(poly(M, blade), fill=to_hex(body), outline=edge, tags=TAG)
            c.create_line(*apply(M, (10, 4)), *apply(M, (96, 4)), fill=edge, tags=TAG)
        c.create_oval(hx - 6, hy - 6, hx + 6, hy + 6, fill=to_hex((14, 22, 42)), outline=edge, tags=TAG)

    def draw_cottage(self, ill):
        c = self.c
        edge = to_hex(lerp((22, 32, 58), (110, 130, 185), ill * 0.8))
        body = to_hex((10, 16, 32))
        c.create_rectangle(110, 352, 190, 396, fill=body, outline=edge, tags=TAG)
        c.create_polygon(100, 352, 200, 352, 150, 316, fill=body, outline=edge, tags=TAG)
        c.create_rectangle(163, 322, 175, 346, fill=body, outline=edge, tags=TAG)       # chimney
        f = 0.85 + 0.15 * math.sin(self.clock * 7) + 0.05 * math.sin(self.clock * 13)   # flicker
        for rad, col in ((30 * f, (46, 36, 20)), (19 * f, (112, 82, 30))):
            c.create_oval(130 - rad, 375 - rad, 130 + rad, 375 + rad, fill=to_hex(col), outline="",
                          tags=TAG)
        c.create_rectangle(122, 366, 138, 384, fill="#ffcc66", outline="#3a2a10", tags=TAG)
        c.create_line(130, 366, 130, 384, fill="#3a2a10", tags=TAG)
        c.create_line(122, 375, 138, 375, fill="#3a2a10", tags=TAG)
        c.create_rectangle(152, 372, 170, 396, fill="#050a18", outline=edge, tags=TAG)

    def draw_boat(self, ill):
        c = self.c
        tb = self.tb
        bx = 480 + 300 * math.sin(0.2 * tb)                       # Lissajous 1:2 sailing path
        by = 478 + 12 * math.sin(0.4 * tb)
        ang = 0.07 * math.sin(1.3 * tb)                           # rocking = rotation
        sx = math.tanh(4 * math.cos(0.2 * tb))                    # turns around: scale x through 0
        M = tf_matrix(bx, by, ang, sx, 1.0)
        M2 = tf_matrix(bx, by + 18, -ang, sx, -0.55)              # mirrored reflection in the lake
        hull = [(-36, -6), (36, -6), (24, 8), (-24, 8)]
        sail = [(2, -8), (2, -50), (26, -10)]
        sail2 = [(-2, -8), (-2, -36), (-22, -10)]
        for T, dim in ((M2, 0.45), (M, 1.0)):
            c.create_polygon(poly(T, hull), fill=to_hex(lerp(lake_color(480), (16, 22, 38), dim)),
                             outline=to_hex(lerp(lake_color(480), (70, 92, 140), dim)), tags=TAG)
            c.create_polygon(poly(T, sail), fill=to_hex(lerp(lake_color(480), lerp((28, 42, 76), (190, 205, 240), ill * 0.9), dim)),
                             outline="", tags=TAG)
            c.create_polygon(poly(T, sail2), fill=to_hex(lerp(lake_color(480), lerp((24, 36, 64), (160, 176, 215), ill * 0.8), dim)),
                             outline="", tags=TAG)
            c.create_line(*apply(T, (0, -6)), *apply(T, (0, -52)),
                          fill=to_hex(lerp(lake_color(480), (60, 78, 120), dim)), width=2, tags=TAG)
        lx, ly = apply(M, (0, -54))
        fl = 0.8 + 0.2 * math.sin(self.clock * 9)
        for rad, col in ((11 * fl, (60, 46, 20)), (5 * fl, (255, 205, 100))):
            c.create_oval(lx - rad, ly - rad, lx + rad, ly + rad, fill=to_hex(col), outline="", tags=TAG)
        return M

    # ---------------------------- loop ----------------------------
    def tick(self):
        now = time.perf_counter()
        dt = min(now - self.last, 0.05)
        self.last = now
        self.update(dt)
        self.render()
        self.root.after(8, self.tick)

    def run(self):
        self.root.mainloop()


if __name__ == "__main__":
    Scene().run()