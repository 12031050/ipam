# -*- coding: utf-8 -*-
"""IPAM fnOS 安装包构建器。

.fpk 本质是带固定结构的 tar.gz：manifest、双尺寸图标、cmd/、config/、
wizard/、ui/、app.tgz（app/ 目录内容的 tar.gz）。
本脚本不依赖任何第三方库；若在飞牛设备上构建，也可直接使用官方 fnpack。

用法：
    python build_fpk.py                    # 生成 IPAM-<version>.fpk
    python build_fpk.py --version 1.0.1    # 指定版本号（同步写回 manifest）
    python build_fpk.py --check            # 仅校验已有 fpk 包结构
"""

import argparse
import io
import json
import os
import re
import struct
import sys
import tarfile
import zlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent
APP = ROOT / "app"
CMD_FILES = ("main", "install_init", "install_callback", "upgrade_init", "upgrade_callback",
             "uninstall_init", "uninstall_callback", "config_init", "config_callback")

FONT = {
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"],
    "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
}


def read_manifest():
    data = {}
    for line in (ROOT / "manifest").read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        data[k.strip()] = v.strip()
    return data


def make_icon(size):
    """纯 Python 生成圆角矩形渐变图标 + IP 点阵字，返回 PNG 字节。"""
    w = h = size
    px = [[(0, 0, 0, 0) for _ in range(w)] for _ in range(h)]
    margin = max(2, int(size * 0.055))
    radius = int(size * 0.22)
    x0, y0, x1, y1 = margin, margin, size - margin, size - margin

    def inside_round(x, y):
        if x < x0 or x > x1 or y < y0 or y > y1:
            return False
        cx = min(max(x, x0 + radius), x1 - radius)
        cy = min(max(y, y0 + radius), y1 - radius)
        return (x - cx) ** 2 + (y - cy) ** 2 <= radius ** 2

    for y in range(h):
        for x in range(w):
            if not inside_round(x, y):
                continue
            t = min(1.0, max(0.0, (x + y) / (2.0 * size) * 1.35))
            r = int(11 + (45 - 11) * t)
            g = int(148 + (189 - 148) * t)
            b = int(136 + (248 - 136) * t)
            px[y][x] = (r, g, b, 255)

    scale = max(2, size // 23)
    gap = max(2, scale)
    text_w = 5 * scale * 2 + gap
    text_h = 7 * scale
    ox = (size - text_w) // 2
    oy = (size - text_h) // 2 - int(size * 0.035)
    for idx, ch in enumerate("IP"):
        base_x = ox + idx * (5 * scale + gap)
        for row, bits in enumerate(FONT[ch]):
            for col, bit in enumerate(bits):
                if bit != "1":
                    continue
                for dy in range(scale):
                    for dx in range(scale):
                        xx, yy = base_x + col * scale + dx, oy + row * scale + dy
                        if 0 <= xx < w and 0 <= yy < h:
                            px[yy][xx] = (248, 253, 255, 255)

    # 底部网络节点装饰
    node_y = int(size * 0.79)
    node_r = max(1, int(size * 0.018))
    for i, fx in enumerate((0.30, 0.50, 0.70)):
        cx = int(size * fx)
        for dy in range(-node_r, node_r + 1):
            for dx in range(-node_r, node_r + 1):
                if dx * dx + dy * dy <= node_r * node_r:
                    xx, yy = cx + dx, node_y + dy
                    if 0 <= xx < w and 0 <= yy < h:
                        px[yy][xx] = (230, 250, 255, 220)
    for cx_a, cx_b in ((0.30, 0.50), (0.50, 0.70)):
        a, b = int(size * cx_a), int(size * cx_b)
        for xx in range(a, b + 1):
            yy = node_y
            if 0 <= xx < w:
                px[yy][xx] = (230, 250, 255, 150)

    raw = bytearray()
    for y in range(h):
        raw.append(0)
        for x in range(w):
            raw.extend(px[y][x])

    def chunk(kind, payload):
        return (struct.pack(">I", len(payload)) + kind + payload +
                struct.pack(">I", zlib.crc32(kind + payload) & 0xffffffff))

    png = b"\x89PNG\r\n\x1a\n"
    png += chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 6, 0, 0, 0))
    png += chunk(b"IDAT", zlib.compress(bytes(raw), 9))
    png += chunk(b"IEND", b"")
    return png


def ensure_icons():
    targets = [
        (ROOT / "ICON.PNG", 64), (ROOT / "ICON_256.PNG", 256),
        (APP / "ui" / "images" / "icon_64.png", 64),
        (APP / "ui" / "images" / "icon_256.png", 256),
    ]
    for path, size in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists() or path.stat().st_size == 0:
            path.write_bytes(make_icon(size))
            print("  生成图标 %s (%dx%d)" % (path.relative_to(ROOT), size, size))
    for path, size in targets[:2]:
        data = path.read_bytes()
        if len(data) > 1024 * 1024:
            raise RuntimeError("图标超过 1024 KB：%s" % path)


DEFAULT_BRANDING = {
    "app_name": "IP 地址管理", "owner_name": "梦非常浪漫",
    "contact_email": "12031050@qq.com", "contact": "QQ群：416290431",
    "website": "", "source_url": "https://github.com/12031050/ipam.git",
    "copyright": "© 2026 梦非常浪漫",
    "disclaimer": (
        "本软件为开源项目，按「现状」（AS IS）提供，作者及贡献者不提供任何明示或暗示的担保，"
        "包括但不限于对适销性、特定用途适用性与非侵权性的担保。在任何情况下，作者与贡献者均不对"
        "因使用本软件或与之相关的任何直接、间接、偶然、特殊、惩罚性或后果性损害（包括但不限于"
        "数据丢失、业务中断、利润损失）承担责任，即使已被告知此类损害的可能性。使用者须自行评估"
        "并承担使用风险，仅在合法授权范围内用于 IP 与资产管理，并在生产环境使用前做好数据备份。"
        "第三方基于本项目的修改、再分发或商业化行为与本项目作者无关。"
    ),
}


def ensure_branding_default():
    path = ROOT / "branding.json"
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_BRANDING, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print("  生成 branding.json（已内置作者信息，可直接修改）")
    data = json.loads(path.read_text(encoding="utf-8"))
    for k, v in DEFAULT_BRANDING.items():
        data.setdefault(k, v)
    return data


def apply_branding_to_manifest(branding):
    """把 branding.json 中的署名同步到 manifest（幂等）。"""
    owner = str(branding.get("owner_name", "")).strip()
    website = str(branding.get("website", "")).strip()
    source_url = str(branding.get("source_url", "")).strip()
    app_name = str(branding.get("app_name", "")).strip() or "IP 地址管理"
    text = (ROOT / "manifest").read_text(encoding="utf-8")
    text = re.sub(r"^maintainer=.*$", "maintainer=" + (owner or "梦非常浪漫"), text, flags=re.M)
    text = re.sub(r"^maintainer_url=.*$", "maintainer_url=" + (website or source_url), text, flags=re.M)
    text = re.sub(r"^display_name=.*$", "display_name=" + app_name, text, flags=re.M)
    (ROOT / "manifest").write_bytes(text.encode("utf-8"))
    return text


def ensure_license():
    path = ROOT / "LICENSE"
    if path.exists():
        return
    path.write_text(
        "IPAM for fnOS\n\n"
        "本应用为本地自部署工具，源码与数据由本机使用者持有与维护。\n"
        "应用不包含任何第三方代码、字体、图标或在线服务依赖；全部实现基于 Python 标准库与原生 JavaScript。\n"
        "软件按现状提供，仅供合法授权范围内的 IP 与资产管理使用。\n"
        "请定期备份数据；因数据丢失或误用造成的后果由使用者自行承担。\n",
        encoding="utf-8")
    print("  生成 LICENSE")


def normalize_line_endings():
    """确保所有文本/脚本文件为 LF，避免 Windows 构建的 CRLF 让 Linux shebang 失效。"""
    candidates = [ROOT / "manifest", ROOT / "LICENSE"]
    candidates += [ROOT / "cmd" / n for n in CMD_FILES]
    candidates += list((ROOT / "config").glob("*")) + list((ROOT / "wizard").glob("*"))
    candidates += list((APP / "server").glob("*.py"))
    candidates += [APP / "ui" / "index.html", APP / "ui" / "app.js",
                   APP / "ui" / "style.css", APP / "ui" / "config"]
    changed = 0
    for path in candidates:
        if not path.is_file():
            continue
        data = path.read_bytes()
        fixed = data.replace(b"\r\n", b"\n")
        if fixed != data:
            path.write_bytes(fixed)
            changed += 1
    if changed:
        print("  规范化换行符：%d 个文件 → LF" % changed)


def perm_filter(info):
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    info.mode = 0o755 if (info.isdir() or info.name.startswith("cmd/")) else 0o644
    return info


def add_bytes(tf, name, data):
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = 0o644
    info.uid = info.gid = 0
    info.uname = info.gname = "root"
    tf.addfile(info, io.BytesIO(data))


def make_app_tgz():
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT) as inner:
        for item in sorted(APP.rglob("*")):
            if "__pycache__" in item.parts or item.suffix == ".pyc":
                continue
            arc = item.relative_to(APP).as_posix()
            inner.add(str(item), arcname=arc, recursive=False, filter=perm_filter)
        add_bytes(inner, "branding.json", (ROOT / "branding.json").read_bytes())
    return buf.getvalue()


def build(version=None):
    normalize_line_endings()
    ensure_icons()
    ensure_license()
    apply_branding_to_manifest(ensure_branding_default())
    manifest = read_manifest()
    if version:
        manifest["version"] = version
        text = (ROOT / "manifest").read_text(encoding="utf-8")
        text = re.sub(r"^version=.*$", "version=" + version, text, flags=re.M)
        (ROOT / "manifest").write_text(text, encoding="utf-8")
    ver = manifest["version"]
    out = ROOT / ("IPAM-%s.fpk" % ver)

    required = [ROOT / "manifest", ROOT / "ICON.PNG", ROOT / "ICON_256.PNG",
                ROOT / "LICENSE", APP / "server" / "server.py", APP / "ui" / "index.html",
                APP / "ui" / "app.js", APP / "ui" / "style.css", APP / "ui" / "config"]
    for f in required:
        if not f.is_file():
            raise RuntimeError("缺少必需文件：%s" % f)
    for name in CMD_FILES:
        if not (ROOT / "cmd" / name).is_file():
            raise RuntimeError("缺少生命周期脚本：cmd/%s" % name)
    for name in ("privilege", "resource"):
        if not (ROOT / "config" / name).is_file():
            raise RuntimeError("缺少配置：config/%s" % name)

    app_tgz = make_app_tgz()
    with tarfile.open(str(out), "w:gz", format=tarfile.GNU_FORMAT) as tf:
        for name in ("manifest", "ICON.PNG", "ICON_256.PNG", "LICENSE", "branding.json"):
            tf.add(str(ROOT / name), name, filter=perm_filter)
        for folder in ("cmd", "config", "wizard"):
            tf.add(str(ROOT / folder), folder, filter=perm_filter)
        # 兼容不同 fnpack 版本的 UI 位置：外层与 app.tgz 内各放一份
        tf.add(str(APP / "ui"), "ui", filter=perm_filter)
        add_bytes(tf, "app.tgz", app_tgz)

    print("\n已生成 %s (%.1f KB)" % (out.name, out.stat().st_size / 1024))
    return out


def check(path=None):
    manifest = read_manifest()
    path = Path(path) if path else ROOT / ("IPAM-%s.fpk" % manifest["version"])
    if not path.is_file():
        raise RuntimeError("找不到安装包：%s" % path)
    with tarfile.open(str(path), "r:gz") as tf:
        names = tf.getnames()
        members = {m.name: m for m in tf.getmembers()}
    need = ["manifest", "ICON.PNG", "ICON_256.PNG", "app.tgz", "branding.json",
            "cmd/main", "config/privilege", "config/resource", "wizard/install", "ui/config"]
    missing = [n for n in need if n not in names]
    if missing:
        raise RuntimeError("安装包缺少文件：%s" % ", ".join(missing))
    if oct(members["cmd/main"].mode & 0o777) != "0o755":
        raise RuntimeError("cmd/main 缺少可执行权限")
    with tarfile.open(str(path), "r:gz") as tf:
        inner_bytes = tf.extractfile("app.tgz").read()
    with tarfile.open(fileobj=io.BytesIO(inner_bytes), mode="r:gz") as inner:
        inner_names = inner.getnames()
    for n in ("server/server.py", "ui/index.html", "ui/app.js", "ui/style.css", "ui/config", "branding.json"):
        if n not in inner_names:
            raise RuntimeError("app.tgz 缺少 %s" % n)
    print("校验通过：%s" % path.name)
    print("  外层 %d 项，app.tgz 内 %d 项" % (len(names), len(inner_names)))
    print("  version=%s  appname=%s  port=%s" %
          (manifest.get("version"), manifest.get("appname"), manifest.get("service_port")))
    return path


def refresh_icons():
    """用作者提供的 logo 源图重建 4 张图标（仅构建机需要 ImageMagick）。

    源图：assets/ipamlogo.png（推荐，已处理圆角外透明）；兼容 .jpg。
    应用运行与打包本身不依赖 ImageMagick，仓库内已包含生成好的 PNG，
    普通 build 不会调用本函数。
    """
    import shutil
    import subprocess
    candidates = [ROOT / "assets" / "ipamlogo.png", ROOT / "assets" / "ipamlogo.jpg"]
    source = next((p for p in candidates if p.is_file()), None)
    if source is None:
        raise RuntimeError("找不到 logo 源图：assets/ipamlogo.png 或 assets/ipamlogo.jpg")
    tool = shutil.which("magick")
    if not tool:
        raise RuntimeError("重建图标需要 ImageMagick 的 magick 命令；也可直接替换 4 张 PNG。")
    targets = ((ROOT / "ICON.PNG", 64), (ROOT / "ICON_256.PNG", 256),
               (APP / "ui" / "images" / "icon_64.png", 64),
               (APP / "ui" / "images" / "icon_256.png", 256))
    built = {}
    for size in (64, 256):
        out = ROOT / ("_icon_%d.tmp.png" % size)
        subprocess.run([tool, str(source), "-auto-orient", "-strip", "-filter", "Lanczos",
                        "-resize", "%dx%d!" % (size, size), "-unsharp", "0x0.5",
                        "PNG32:" + str(out)], check=True)
        built[size] = out.read_bytes()
        out.unlink()
    for path, size in targets:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(built[size])
        print("  更新图标 %s (%dx%d)" % (path.relative_to(ROOT), size, size))


def main():
    ap = argparse.ArgumentParser(description="构建 IPAM fnOS 安装包")
    ap.add_argument("--version", help="指定版本号并同步写入 manifest")
    ap.add_argument("--check", action="store_true", help="仅校验已有安装包")
    ap.add_argument("--file", help="校验指定 fpk 路径")
    ap.add_argument("--refresh-icons", action="store_true",
                    help="用 assets/ipamlogo.png 重建全部图标（需 ImageMagick）")
    args = ap.parse_args()
    try:
        if args.refresh_icons:
            refresh_icons()
        if args.check:
            check(args.file)
        else:
            out = build(args.version)
            check(out)
    except Exception as e:
        print("构建失败：%s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
