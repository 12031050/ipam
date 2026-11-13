# -*- coding: utf-8 -*-
"""IPAM fnOS 版核心层：配置、SQLite、鉴权与通用工具。仅依赖 Python 标准库。"""

import hashlib
import hmac
import ipaddress
import json
import os
import re
import secrets
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta

APP_VERSION = "1.0.3"
PBKDF2_ROUNDS = 200000
SESSION_DAYS = 7
DEFAULT_PORT = 8765
MAX_POOL_SIZE = 65536

STATUS_LABELS = {
    "in_use": "使用中",
    "idle": "空闲",
    "reserved": "预留",
    "disabled": "停用",
    "retired": "已退役",
}
ASSET_STATUS_LABELS = {
    "in_use": "使用中", "idle": "空闲", "standby": "备用",
    "pending": "待部署", "retired": "已退役",
}
NODE_TYPE_LABELS = {
    "campus": "园区", "building": "楼栋", "floor": "楼层",
    "room": "机房", "cabinet": "机柜",
}
DEFAULT_FIELD_LABELS = {
    "ip_address": "IP 地址", "port": "端口", "device_name": "设备名称",
    "device_type": "设备类型", "mac_address": "MAC 地址", "owner": "使用人",
    "organization": "单位", "department": "部门", "status": "状态",
    "business_system": "业务系统", "subnet": "子网", "remark": "备注",
}
DEFAULT_BRANDING = {
    "app_name": "IP 地址管理",
    "owner_name": "梦非常浪漫",
    "contact_email": "12031050@qq.com",
    "contact": "QQ群：416290431",
    "website": "",
    "source_url": "https://github.com/12031050/ipam.git",
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

BRANDING_MAX_LEN = 1000


def now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def new_id():
    return uuid.uuid4().hex[:16]


def env(name, default=""):
    return os.environ.get(name) or default


class Config:
    def __init__(self):
        here = os.path.dirname(os.path.abspath(__file__))
        self.dest = env("IPAM_APPDEST", env("TRIM_APPDEST", os.path.dirname(here)))
        self.var = env("IPAM_DATA_DIR", env("TRIM_PKGVAR", os.path.join(os.path.dirname(here), "data")))
        self.etc = env("IPAM_ETC_DIR", env("TRIM_PKGETC", os.path.join(os.path.dirname(here), "etc")))
        self.ui = os.path.join(self.dest, "ui")
        self.db_path = os.path.join(self.var, "ipam.db")
        self.backup_dir = os.path.join(self.var, "backups")
        self.export_dir = os.path.join(self.var, "exports")
        self.branding_path = os.path.join(self.etc, "branding.json")
        self.port = int(env("IPAM_PORT", env("TRIM_SERVICE_PORT", str(DEFAULT_PORT))))
        self.host = env("IPAM_HOST", "0.0.0.0")
        self.socket_path = env("IPAM_SOCKET_PATH", "")
        self.trust_gateway = bool(self.socket_path) and env("IPAM_TRUST_GATEWAY", "1") == "1"
        for d in (self.var, self.etc, self.backup_dir, self.export_dir):
            os.makedirs(d, exist_ok=True)


SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE, password_hash TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'user', org_id TEXT, is_active INTEGER NOT NULL DEFAULT 1,
  last_login TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
  token TEXT PRIMARY KEY, user_id TEXT NOT NULL, created_at TEXT NOT NULL, expires_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_attempts (
  username TEXT PRIMARY KEY, failures INTEGER NOT NULL DEFAULT 0, locked_until TEXT
);
CREATE TABLE IF NOT EXISTS organizations (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS departments (
  id TEXT PRIMARY KEY, org_id TEXT, name TEXT NOT NULL, created_at TEXT NOT NULL,
  UNIQUE(org_id, name)
);
CREATE TABLE IF NOT EXISTS location_nodes (
  id TEXT PRIMARY KEY, parent_id TEXT, name TEXT NOT NULL, node_type TEXT DEFAULT 'room',
  description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS vlans (
  id TEXT PRIMARY KEY, vlan_id INTEGER NOT NULL UNIQUE, name TEXT DEFAULT '',
  purpose TEXT DEFAULT '', description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS subnets (
  id TEXT PRIMARY KEY, cidr TEXT NOT NULL UNIQUE, name TEXT DEFAULT '', vlan_id TEXT,
  gateway TEXT DEFAULT '', description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS address_spaces (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, cidr TEXT NOT NULL,
  description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS ip_pools (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, range_start TEXT NOT NULL,
  range_end TEXT NOT NULL, subnet_id TEXT, description TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS reserved_ips (
  id TEXT PRIMARY KEY, ip_address TEXT NOT NULL, subnet_id TEXT, purpose TEXT DEFAULT '',
  remark TEXT DEFAULT '', created_at TEXT NOT NULL, UNIQUE(ip_address, subnet_id)
);
CREATE TABLE IF NOT EXISTS ip_records (
  id TEXT PRIMARY KEY, ip_address TEXT NOT NULL, port INTEGER NOT NULL DEFAULT 0,
  subnet_id TEXT, org_id TEXT, department_id TEXT, status TEXT NOT NULL DEFAULT 'in_use',
  device_name TEXT DEFAULT '', device_type TEXT DEFAULT '', mac_address TEXT DEFAULT '',
  owner TEXT DEFAULT '', business_system TEXT DEFAULT '', remark TEXT DEFAULT '',
  is_online INTEGER DEFAULT 0, last_scan_time TEXT, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL, created_by TEXT DEFAULT '',
  UNIQUE(ip_address, port, org_id)
);
CREATE TABLE IF NOT EXISTS assets (
  id TEXT PRIMARY KEY, asset_number TEXT NOT NULL UNIQUE, asset_name TEXT NOT NULL,
  asset_type TEXT DEFAULT '', brand TEXT DEFAULT '', model TEXT DEFAULT '',
  serial_number TEXT DEFAULT '', status TEXT DEFAULT 'in_use', location TEXT DEFAULT '',
  owner TEXT DEFAULT '', purchase_date TEXT DEFAULT '', warranty_expire_at TEXT DEFAULT '',
  remark TEXT DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS asset_ip_bindings (
  id TEXT PRIMARY KEY, asset_id TEXT NOT NULL, record_id TEXT NOT NULL,
  binding_type TEXT DEFAULT 'primary', created_at TEXT NOT NULL,
  UNIQUE(asset_id, record_id)
);
CREATE TABLE IF NOT EXISTS tags (
  id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE, color TEXT DEFAULT '#38bdf8', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS record_tags (
  record_id TEXT NOT NULL, tag_id TEXT NOT NULL, UNIQUE(record_id, tag_id)
);
CREATE TABLE IF NOT EXISTS saved_views (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, scope TEXT DEFAULT 'personal',
  filters_json TEXT DEFAULT '{}', owner TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
  id TEXT PRIMARY KEY, action TEXT NOT NULL, target TEXT DEFAULT '', detail TEXT DEFAULT '',
  operator TEXT DEFAULT '', created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_records_ip ON ip_records(ip_address);
CREATE INDEX IF NOT EXISTS idx_records_subnet ON ip_records(subnet_id);
CREATE INDEX IF NOT EXISTS idx_records_org ON ip_records(org_id);
CREATE INDEX IF NOT EXISTS idx_records_status ON ip_records(status);
CREATE INDEX IF NOT EXISTS idx_records_device ON ip_records(device_name);
CREATE INDEX IF NOT EXISTS idx_audit_time ON audit_logs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_bindings_asset ON asset_ip_bindings(asset_id);
"""


def connect(cfg):
    conn = sqlite3.connect(cfg.db_path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.create_function("ipkey", 1, ip_key, deterministic=True)
    conn.execute("PRAGMA foreign_keys=OFF")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


@contextmanager
def db(cfg, write=False):
    conn = connect(cfg)
    try:
        if write:
            conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(cfg):
    with db(cfg, write=True) as conn:
        conn.executescript(SCHEMA)
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('field_labels',?)",
                     (json.dumps(DEFAULT_FIELD_LABELS, ensure_ascii=False),))
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('system_mode','normal')")
        conn.execute("INSERT OR IGNORE INTO settings(key,value) VALUES('schema_version','1')")
    c = connect(cfg)
    try:
        c.execute("PRAGMA journal_mode=WAL")
    finally:
        c.close()


def init_admin(cfg, username, password):
    """安装向导调用：创建初始管理员；已存在用户时不覆盖。"""
    username = (username or "admin").strip()
    if len(username) < 2 or len(username) > 32:
        raise ValueError("用户名长度需为 2-32 个字符")
    if len(password or "") < 6:
        raise ValueError("初始密码至少 6 位")
    with db(cfg, write=True) as conn:
        row = conn.execute("SELECT id FROM users LIMIT 1").fetchone()
        if row:
            return False
        conn.execute(
            "INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
            (new_id(), username, hash_password(password), "admin", now()))
        conn.execute("INSERT INTO audit_logs(id,action,detail,operator,created_at) VALUES(?,?,?,?,?)",
                     (new_id(), "初始化", "创建初始管理员账号", username, now()))
    return True


def hash_password(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ROUNDS)
    return "pbkdf2$%d$%s$%s" % (PBKDF2_ROUNDS, salt.hex(), digest.hex())


def verify_password(password, stored):
    try:
        _, rounds, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"),
                                     bytes.fromhex(salt_hex), int(rounds))
        return hmac.compare_digest(digest.hex(), digest_hex)
    except Exception:
        return False


def login(cfg, username, password, remote_addr="", conn=None):
    """校验密码并创建会话。调用方若已持有写连接，必须把 conn 传入以免自锁。"""
    if conn is None:
        with db(cfg, write=True) as c:
            return login(cfg, username, password, remote_addr, conn=c)
    username = (username or "").strip()
    lock = conn.execute("SELECT failures,locked_until FROM login_attempts WHERE username=?", (username,)).fetchone()
    if lock and lock["locked_until"] and lock["locked_until"] > now():
        return None, "失败次数过多，请 15 分钟后再试"
    user = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
    if not user or not verify_password(password or "", user["password_hash"]):
        failures = (lock["failures"] if lock else 0) + 1
        locked = (datetime.now() + timedelta(minutes=15)).strftime("%Y-%m-%d %H:%M:%S") if failures >= 5 else None
        conn.execute("INSERT INTO login_attempts(username,failures,locked_until) VALUES(?,?,?) "
                     "ON CONFLICT(username) DO UPDATE SET failures=excluded.failures,locked_until=excluded.locked_until",
                     (username, failures, locked))
        return None, "用户名或密码错误"
    conn.execute("DELETE FROM login_attempts WHERE username=?", (username,))
    conn.execute("UPDATE users SET last_login=? WHERE id=?", (now(), user["id"]))
    token = secrets.token_urlsafe(32)
    expires = (datetime.now() + timedelta(days=SESSION_DAYS)).strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("INSERT INTO sessions(token,user_id,created_at,expires_at) VALUES(?,?,?,?)",
                 (token, user["id"], now(), expires))
    conn.execute("INSERT INTO audit_logs(id,action,detail,operator,created_at) VALUES(?,?,?,?,?)",
                 (new_id(), "登录", "登录成功 (%s)" % remote_addr, username, now()))
    return token, None


def session_user(cfg, token):
    if not token:
        return None
    with db(cfg) as conn:
        row = conn.execute(
            "SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id "
            "WHERE s.token=? AND s.expires_at>? AND u.is_active=1", (token, now())).fetchone()
        return dict(row) if row else None


def gateway_user(cfg, username, is_admin):
    """Unix Socket 模式下信任网关注入的用户身份（自动建号）。"""
    username = (username or "").strip()
    if not username or not re.fullmatch(r"[A-Za-z0-9_.@\-]{1,64}", username):
        return None
    role = "admin" if str(is_admin).lower() == "true" else "user"
    with db(cfg, write=True) as conn:
        row = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        if not row:
            conn.execute("INSERT INTO users(id,username,password_hash,role,is_active,created_at) VALUES(?,?,?,?,1,?)",
                         (new_id(), username, "!", role, now()))
        elif role == "admin" and row["role"] != "admin":
            conn.execute("UPDATE users SET role='admin' WHERE id=?", (row["id"],))
        row = conn.execute("SELECT * FROM users WHERE username=? AND is_active=1", (username,)).fetchone()
        return dict(row) if row else None


def logout(cfg, token, conn=None):
    if conn is None:
        with db(cfg, write=True) as c:
            return logout(cfg, token, conn=c)
    conn.execute("DELETE FROM sessions WHERE token=?", (token,))


def audit(conn, action, target="", detail="", operator=""):
    conn.execute("INSERT INTO audit_logs(id,action,target,detail,operator,created_at) VALUES(?,?,?,?,?,?)",
                 (new_id(), action, str(target)[:200], str(detail)[:1000], operator, now()))


def get_setting(conn, key, default=""):
    row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn, key, value):
    conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                 "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def load_branding(cfg):
    data = dict(DEFAULT_BRANDING)
    try:
        with open(cfg.branding_path, "r", encoding="utf-8") as f:
            saved = json.load(f)
        if isinstance(saved, dict):
            for k in DEFAULT_BRANDING:
                # 旧版本遗留的空字段不覆盖内置作者信息，避免出现空白版权占位
                if isinstance(saved.get(k), str) and saved[k].strip():
                    data[k] = saved[k][:BRANDING_MAX_LEN]
    except (OSError, ValueError):
        pass
    if not data["copyright"]:
        year = datetime.now().year
        owner = data["owner_name"].strip() or "梦非常浪漫"
        data["copyright"] = "© %d %s" % (year, owner)
    return data


def save_branding(cfg, values):
    data = load_branding(cfg)
    for k in ("app_name", "owner_name", "contact_email", "contact", "website",
              "source_url", "copyright", "disclaimer"):
        if k in values and isinstance(values[k], str):
            data[k] = values[k].strip()[:BRANDING_MAX_LEN]
    tmp = cfg.branding_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    os.replace(tmp, cfg.branding_path)
    return data


def norm_mac(value):
    v = (value or "").strip().upper().replace("-", ":")
    if not v:
        return ""
    return v if re.fullmatch(r"([0-9A-F]{2}:){5}[0-9A-F]{2}", v) else None


def ip_key(value):
    try:
        return int(ipaddress.ip_address((value or "").strip()))
    except Exception:
        return 0


def valid_ip(value):
    try:
        ipaddress.ip_address((value or "").strip())
        return True
    except Exception:
        return False


def cidr_info(cidr):
    try:
        net = ipaddress.ip_network((cidr or "").strip(), strict=False)
    except Exception as e:
        return None, "CIDR 格式不正确：%s" % e
    hosts = (net.num_addresses - 2) if net.version == 4 and net.prefixlen < 31 else net.num_addresses
    return {
        "cidr": str(net), "version": net.version, "prefixlen": net.prefixlen,
        "network": str(net.network_address), "broadcast": str(net.broadcast_address),
        "total": max(hosts, 0), "first_host": str(net.network_address + 1) if net.num_addresses > 2 else str(net.network_address),
        "last_host": str(net.broadcast_address - 1) if net.num_addresses > 2 else str(net.broadcast_address),
    }, None


def subnet_overlap(cidr, existing, exclude_id=None):
    try:
        net = ipaddress.ip_network(cidr.strip(), strict=False)
    except Exception:
        return None
    hits = []
    for row in existing:
        if exclude_id and row["id"] == exclude_id:
            continue
        try:
            other = ipaddress.ip_network(row["cidr"], strict=False)
        except Exception:
            continue
        if net.version == other.version and net.overlaps(other):
            hits.append(row["cidr"])
    return hits


def infer_subnet(cidr_list, ip_value):
    try:
        ip = ipaddress.ip_address((ip_value or "").strip())
    except Exception:
        return None
    best = None
    for row in cidr_list:
        try:
            net = ipaddress.ip_network(row["cidr"], strict=False)
        except Exception:
            continue
        if ip.version == net.version and ip in net:
            if best is None or net.prefixlen > best[1]:
                best = (row["id"], net.prefixlen)
    return best[0] if best else None


def pool_addresses(start, end):
    try:
        a = ipaddress.ip_address(start.strip())
        b = ipaddress.ip_address(end.strip())
    except Exception:
        return None, "IP 范围格式不正确"
    if a.version != b.version:
        return None, "起止地址必须是同一协议版本"
    if int(b) < int(a):
        return None, "结束地址不能小于开始地址"
    if int(b) - int(a) + 1 > MAX_POOL_SIZE:
        return None, "地址池范围过大（上限 %d 个地址）" % MAX_POOL_SIZE
    out = []
    cur = a
    while int(cur) <= int(b):
        out.append(str(cur))
        if len(out) >= MAX_POOL_SIZE:
            break
        cur = cur + 1
    return out, None


def safe_name(name):
    """备份文件名白名单校验，防目录穿越。"""
    if not name or not re.fullmatch(r"backup_[0-9]{8}_[0-9]{6}\.db", name):
        return None
    return name


def rows_to_dicts(rows):
    return [dict(r) for r in rows]


def parse_int(value, default=0, low=None, high=None):
    try:
        n = int(value)
    except Exception:
        return default
    if low is not None and n < low:
        return low
    if high is not None and n > high:
        return high
    return n


def json_default(obj):
    if isinstance(obj, (bytes, bytearray)):
        return obj.decode("utf-8", "replace")
    return str(obj)
