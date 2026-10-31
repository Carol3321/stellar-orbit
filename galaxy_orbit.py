# -*- coding: utf-8 -*-
"""星河轨道 · 螺旋星系粒子动画（Pygame）

纯自动播放，无需任何交互：关闭窗口或按 ESC 即退出。
依赖：pygame（pip install pygame）
运行：python galaxy_orbit.py
自检：python galaxy_orbit.py --selftest --frames 300 --shot out.png（无窗口跑 300 帧并截图）
参数：全部集中在下方「可配置参数」区，改完直接重跑。
"""

import math
import os
import random
import sys
import time

import pygame

TAU = math.tau

# ============================ 可配置参数 ============================
WIDTH, HEIGHT = 800, 600          # 窗口尺寸
FULLSCREEN = False                # True = 全屏播放
TARGET_FPS = 60

PARTICLE_COUNT = 4800             # 粒子总数（建议 2000 - 5000）
ARM_COUNT = 3                     # 旋臂数量（2 - 4）
ARM_TWIST = 2.4                   # 旋臂由内到外缠绕的总弧度
ARM_SPREAD = 0.30                 # 旋臂宽度（弧度），随半径变宽
FIELD_RATIO = 0.12                # 不落在旋臂上的弥散粒子比例
FIELD_DIM = 0.35                  # 弥散粒子亮度系数，压暗后旋臂更清晰
BULGE_RATIO = 0.18                # 核球星团占粒子总数的比例
BULGE_RADIUS = 0.20               # 核球半径（归一化）
GALAXY_RADIUS_RATIO = 0.50        # 星系半径 = 屏幕短边 × 该比例
DISK_TILT_DEG = 42                # 盘面倾角：0 正视，90 侧视
DISK_ROLL_DEG = -14               # 盘面在屏幕上的滚转角
INNER_RADIUS = 0.22               # 旋臂起始半径（归一化），中心留给核心
INNER_FADE = 0.45                 # 旋臂内端淡出所需的半径跨度，避免生硬断口
OUTER_FADE = 0.45                 # 外缘淡出跨度，让星盘自然消散、没有硬边

ROTATION_SPEED = 0.25             # 星系整体角速度（弧度/秒）
CORE_PULSE_SPEED = 1.8            # 中心核心呼吸频率
CORE_PULSE_AMOUNT = 0.12          # 中心核心呼吸幅度
TRAIL_DECAY = 0.82                # 拖尾衰减：每帧保留的亮度（1 = 不衰减）
TRAIL_FLOOR = 2                   # 每帧额外减去的亮度，抹掉整盘的低亮度雾底

# 颜色渐变：中心亮白/金黄 → 外围粉/紫/蓝
PALETTE_STOPS = (
    (0.00, (255, 250, 235)),
    (0.10, (255, 226, 150)),
    (0.28, (255, 176, 110)),
    (0.45, (255, 118, 182)),
    (0.62, (188, 108, 255)),
    (0.80, (118, 150, 255)),
    (1.00, (64, 86, 226)),
)
PALETTE_STEPS = 28                       # 调色板量化档数
SIZE_PX = (2, 3, 4, 6, 8, 11, 15, 21)    # 粒子贴图尺寸档位（近大远小在运行时缩放）
BRIGHTNESS = (0.15, 0.21, 0.27, 0.36)    # 亮度档位（加法混合会累积，需偏暗）

CORE_RADIUS_RATIO = 0.24          # 核心辉光半径系数（相对星系半径）
# 中心核心：多层辉光 (半径系数, 颜色, 亮度, 衰减指数)；指数越小光晕越柔
CORE_LAYERS = (
    (2.6, (255, 150, 60), 0.55, 1.8),
    (1.2, (255, 210, 150), 0.90, 2.0),
    (0.45, (255, 255, 255), 1.00, 2.0),
)


# ============================ 工具函数 ============================
def _mix(a, b, t):
    """两个 RGB 颜色按 t 线性插值。"""
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _palette(steps):
    """按 PALETTE_STOPS 生成 steps 档连续渐变色。"""
    out = []
    for i in range(steps):
        r = i / (steps - 1)
        for j in range(len(PALETTE_STOPS) - 1):
            p0, c0 = PALETTE_STOPS[j]
            p1, c1 = PALETTE_STOPS[j + 1]
            if r <= p1:
                out.append(_mix(c0, c1, (r - p0) / (p1 - p0)))
                break
    return out


def _radial_glow(size, power=2.0):
    """生成 size×size 的白色径向辉光贴图（中心亮、边缘黑），加法混合用。"""
    surf = pygame.Surface((size, size))
    surf.fill((0, 0, 0))
    mid = size // 2
    for d in range(mid, 0, -1):
        v = int(255 * (1.0 - d / mid) ** power)
        pygame.draw.circle(surf, (v, v, v), (mid, mid), d)
    return surf


# ============================ 粒子 ============================
class Particle:
    """单个恒星粒子：极坐标轨道 + 外观参数，绕星系中心做圆周运动。"""

    def __init__(self, orbit, theta, omega, size, bright, color):
        self.orbit = orbit        # 轨道半径（像素，已按屏幕缩放）
        self.theta = theta        # 当前角度
        self.omega = omega        # 角速度（弧度/秒）
        self.size = size          # 基础尺寸（像素）
        self.bright = bright      # 基础亮度 0-1
        self.color = color        # 调色板索引

    def update(self, dt):
        self.theta = (self.theta + self.omega * dt) % TAU


# ============================ 星系 ============================
class Galaxy:
    """星系整体：生成粒子、烘焙发光贴图、逐帧推进并绘制。"""

    def __init__(self, width, height, seed=20261017):
        self.w, self.h = width, height
        self.cx, self.cy = width * 0.5, height * 0.5
        self.radius = min(width, height) * GALAXY_RADIUS_RATIO
        self.cos_tilt = math.cos(math.radians(DISK_TILT_DEG))
        self.roll_cos = math.cos(math.radians(DISK_ROLL_DEG))
        self.roll_sin = math.sin(math.radians(DISK_ROLL_DEG))
        self.t = 0.0
        self.rng = random.Random(seed)

        self.colors = _palette(PALETTE_STEPS)
        self.particles = self._build_particles()
        self.atlas = self._build_atlas()
        self.core = self._build_core()
        self.core_cache = {}
        self.stars = self._build_stars()

        # 拖尾画布：每帧只做衰减 + 叠加，不清屏，于是粒子留下光轨
        self.canvas = pygame.Surface((width, height)).convert()
        self.canvas.fill((0, 0, 0))

    # -------- 生成 --------
    def _build_particles(self):
        """撒点生成三类星：核球星团、旋臂星、以及少量弥散星。"""
        rng = self.rng
        particles = []
        log_norm = math.log(1.0 + 2.0)  # 螺旋角归一化
        for i in range(PARTICLE_COUNT):
            roll = rng.random()
            ridge = 1.0
            if roll < BULGE_RATIO:                            # 核球：包裹恒星的密集星团
                r = BULGE_RADIUS * rng.random() ** 1.8
                theta = rng.uniform(0.0, TAU)
                rmin, dim, bulge = 0.0, 1.0, True
            else:
                rmin = INNER_RADIUS * rng.uniform(0.55, 1.5)  # 每颗星的起始半径略有差异
                r = rmin + (1.0 - rmin) * rng.random() ** 1.35
                bulge = False
                if roll < BULGE_RATIO + FIELD_RATIO:          # 弥散星：压暗铺底
                    theta = rng.uniform(0.0, TAU)
                    dim = FIELD_DIM
                else:                                          # 旋臂星
                    dim = 1.0
                    arm = i % ARM_COUNT
                    # 对数螺旋：内圈收得紧，外圈散得开
                    theta = ARM_TWIST * math.log(1.0 + 2.0 * r) / log_norm
                    theta += arm * TAU / ARM_COUNT
                    # 三条均匀随机数相加近似高斯，让旋臂有厚度
                    spread = (rng.random() + rng.random() + rng.random() - 1.5) / 1.5
                    theta += spread * ARM_SPREAD * (0.3 + 0.7 * r)
                    # 越靠近旋臂中线越亮，旋臂才有一条清晰的光脊
                    ridge = 0.45 + 0.55 * (1.0 - abs(spread))
            omega = ROTATION_SPEED                  # 同一角速度，旋臂形状永不松散
            color = min(PALETTE_STEPS - 1, max(0, int(r * PALETTE_STEPS + rng.uniform(-1.2, 1.2))))
            size = 1.8 + 12.0 * rng.random() ** 2.4          # 少数大粒子点缀
            bright = (0.55 + 0.45 * rng.random()) * (0.80 + 0.35 * (1.0 - r))
            if bulge:
                bright *= 0.45 + 0.55 * rng.random()
            else:
                # 内端平滑淡出，让核心辉光自然接管中心，而不出现生硬断口
                fade = min(1.0, (r - rmin) / INNER_FADE)
                bright *= 0.10 + 0.90 * fade * fade * (3.0 - 2.0 * fade)
                edge = min(1.0, (1.0 - r) / OUTER_FADE)      # 外缘渐隐
                bright *= edge * edge * (3.0 - 2.0 * edge) * dim * ridge
            particles.append(Particle(r * self.radius, theta, omega, size, min(bright, 1.0), color))
        return particles

    def _build_atlas(self):
        """预烘焙 颜色×尺寸×亮度 的辉光贴图，运行时只做 blit，保证 60fps。"""
        master = _radial_glow(128)
        sizes = [pygame.transform.smoothscale(master, (px, px)) for px in SIZE_PX]
        atlas = []
        for color in self.colors:
            for b in BRIGHTNESS:
                tint = tuple(int(c * b) for c in color)
                for base in sizes:
                    sprite = base.copy()
                    sprite.fill(tint, special_flags=pygame.BLEND_RGB_MULT)
                    atlas.append((sprite, sprite.get_width() // 2, sprite.get_height() // 2))
        return atlas

    def _build_core(self):
        """中心恒星的多层辉光母版，每层单独控制衰减指数。"""
        base = self.radius * CORE_RADIUS_RATIO
        out = []
        for factor, color, bright, power in CORE_LAYERS:
            px = max(8, int(base * factor))
            sprite = _radial_glow(px, power=power)
            sprite.fill(tuple(int(c * bright) for c in color), special_flags=pygame.BLEND_RGB_MULT)
            out.append(sprite)
        return out

    def _build_stars(self):
        """背景恒定星场：位置 + 闪烁相位 + 亮度档。"""
        rng = self.rng
        stars = []
        for _ in range(180):
            stars.append((rng.uniform(0, self.w), rng.uniform(0, self.h),
                          rng.uniform(0.0, TAU), rng.uniform(0.5, 1.7)))
        self.star_sprites = [pygame.transform.smoothscale(_radial_glow(16), (s, s))
                             for s in (3, 4, 5)]
        for s in self.star_sprites:
            s.fill((110, 130, 200), special_flags=pygame.BLEND_RGB_MULT)
        return stars

    # -------- 每帧 --------
    def update(self, dt):
        self.t += dt
        for p in self.particles:
            p.update(dt)

    def draw(self, screen):
        """衰减旧帧 → 叠加粒子 → 贴到屏幕 → 叠加核心与星场。"""
        c = self.canvas
        decay = int(255 * TRAIL_DECAY)
        if TRAIL_FLOOR:
            c.fill((TRAIL_FLOOR, TRAIL_FLOOR, TRAIL_FLOOR), special_flags=pygame.BLEND_RGB_SUB)
        c.fill((decay, decay, decay), special_flags=pygame.BLEND_RGB_MULT)

        cx, cy = self.cx, self.cy
        ct, rc, rs = self.cos_tilt, self.roll_cos, self.roll_sin
        atlas = self.atlas
        blit = c.blit
        add = pygame.BLEND_RGB_ADD
        sin, cos = math.sin, math.cos
        n_size, n_bright = len(SIZE_PX), len(BRIGHTNESS)

        for p in self.particles:
            a = p.theta
            s = sin(a)
            x = cos(a) * p.orbit
            y = s * p.orbit * ct
            depth = s * 0.5 + 0.5                     # 0 = 远侧, 1 = 近侧
            # 近大远小 + 远处偏暗，强化立体感
            size = p.size * (0.62 + 0.38 * depth)
            si = int(size * 0.38)                     # 尺寸档位索引，1px ≈ 0.38 档
            if si > n_size - 1:
                si = n_size - 1
            b = p.bright * (0.55 + 0.45 * depth)
            bi = int(b * n_bright)
            if bi > n_bright - 1:
                bi = n_bright - 1
            sprite, hw, hh = atlas[(p.color * n_size + si) * n_bright + bi]
            # 盘面绕屏幕滚转，做出斜置的星系观感
            px = cx + x * rc - y * rs
            py = cy + x * rs + y * rc
            blit(sprite, (int(px) - hw, int(py) - hh), special_flags=add)

        screen.blit(c, (0, 0))
        self._draw_core(screen)   # 核心静止，直接画到屏幕上，避免拖尾累积过曝
        self._draw_stars(screen)

    def _draw_core(self, surface):
        """中心恒星呼吸：半径随正弦脉动。"""
        pulse = 1.0 + CORE_PULSE_AMOUNT * math.sin(self.t * CORE_PULSE_SPEED)
        for i, master in enumerate(self.core):
            key = (i, int(pulse * 100))
            sprite = self.core_cache.get(key)
            if sprite is None:
                w = max(2, int(master.get_width() * pulse))
                sprite = pygame.transform.smoothscale(master, (w, w))
                self.core_cache[key] = sprite
            surface.blit(sprite, (int(self.cx) - sprite.get_width() // 2,
                                  int(self.cy) - sprite.get_height() // 2),
                         special_flags=pygame.BLEND_RGB_ADD)

    def _draw_stars(self, surface):
        """缓慢闪烁的背景星，压低亮度避免抢戏。"""
        blit = surface.blit
        add = pygame.BLEND_RGB_ADD
        for x, y, phase, speed in self.stars:
            level = int((0.6 + 0.4 * math.sin(self.t * speed + phase)) * 2.99)
            sprite = self.star_sprites[level if level >= 0 else 0]
            blit(sprite, (int(x), int(y)), special_flags=add)


# ============================ 主循环 ============================
def main():
    argv = sys.argv[1:]
    selftest = "--selftest" in argv
    shot = argv[argv.index("--shot") + 1] if "--shot" in argv else None
    if selftest:
        os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

    pygame.init()
    pygame.display.set_caption("星河轨道 · Stellar Orbit")
    flags = pygame.FULLSCREEN if FULLSCREEN else 0
    screen = pygame.display.set_mode((WIDTH, HEIGHT), flags)
    pygame.mouse.set_visible(False)
    width, height = screen.get_size()

    galaxy = Galaxy(width, height)
    clock = pygame.time.Clock()
    running = True
    frames, cost = 0, 0.0
    limit = int(argv[argv.index("--frames") + 1]) if "--frames" in argv else 120

    while running:
        if selftest:
            dt = 1.0 / TARGET_FPS
        else:
            dt = min(clock.tick(TARGET_FPS) / 1000.0, 0.05)  # 拖窗后防止大跳
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN and event.key == pygame.K_ESCAPE:
                running = False

        t0 = time.perf_counter()
        galaxy.update(dt)
        galaxy.draw(screen)
        pygame.display.flip()
        if selftest:
            cost += time.perf_counter() - t0

        frames += 1
        if selftest and frames >= limit:
            break

    if selftest:
        ms = cost / frames * 1000
        print(f"selftest: {frames} 帧, 平均 {ms:.2f} ms/帧, 约 {1000 / ms:.0f} fps")
        if shot:
            pygame.image.save(screen, shot)
            print("screenshot ->", shot)

    pygame.quit()


if __name__ == "__main__":
    main()
