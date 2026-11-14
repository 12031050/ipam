# -*- coding: utf-8 -*-
"""IPAM fnOS 版 API 扩展：批量操作、统计、导入导出、备份与设置。"""

import csv
import io
import ipaddress
import json
import os
import platform
import re
import shutil
import socket
import sqlite3
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from core import (
    APP_VERSION, ASSET_STATUS_LABELS, STATUS_LABELS, audit, cidr_info,
    get_setting, load_branding, new_id, norm_mac, now, parse_int, pool_addresses,
    rows_to_dicts, safe_name, save_branding, set_setting, valid_ip,
)
from router import ApiError, RawResponse

# api 模块在本模块被导入时已加载完毕，此处延迟引用避免循环导入问题
import api as main_api


def _ids(req, cap=500):
    ids = req.body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise ApiError("请先选择记录")
    ids = [str(x) for x in ids[:cap]]
    marks = ",".join("?" for _ in ids)
    return ids, marks


def _visible_records(req, conn, ids):
    marks = ",".join("?" for _ in ids)
    rows = conn.execute("SELECT * FROM ip_records WHERE id IN (%s)" % marks, ids).fetchall()
    for row in rows:
        main_api.check_org_scope(req, conn, row["org_id"])
    return rows


# ---------------- 批量记录操作 ----------------

def batch_records(req, conn, action):
    ids, _ = _ids(req)
    rows = _visible_records(req, conn, ids)
    if not rows:
        raise ApiError("未找到可操作的记录")
    id_list = [r["id"] for r in rows]
    marks = ",".join("?" for _ in id_list)
    val = req.body.get("value")
    affected = 0

    if action == "delete":
        conn.execute("DELETE FROM record_tags WHERE record_id IN (%s)" % marks, id_list)
        conn.execute("DELETE FROM asset_ip_bindings WHERE record_id IN (%s)" % marks, id_list)
        conn.execute("DELETE FROM ip_records WHERE id IN (%s)" % marks, id_list)
        affected = len(id_list)
        detail = "批量删除 %d 条记录" % affected
    elif action == "status":
        if val not in STATUS_LABELS:
            raise ApiError("目标状态不合法")
        conn.execute("UPDATE ip_records SET status=?,updated_at=? WHERE id IN (%s)" % marks,
                     [val, now()] + id_list)
        affected = len(id_list)
        detail = "批量修改 %d 条记录状态为 %s" % (affected, STATUS_LABELS[val])
    elif action == "owner":
        if not str(val or "").strip():
            raise ApiError("使用人不能为空")
        conn.execute("UPDATE ip_records SET owner=?,updated_at=? WHERE id IN (%s)" % marks,
                     [str(val).strip(), now()] + id_list)
        affected = len(id_list)
        detail = "批量修改 %d 条记录使用人: %s" % (affected, val)
    elif action == "organization":
        org_id = str(val or "").strip() or None
        if org_id and not conn.execute("SELECT 1 FROM organizations WHERE id=?", (org_id,)).fetchone():
            raise ApiError("目标单位不存在")
        main_api.check_org_scope(req, conn, org_id)
        conn.execute("UPDATE ip_records SET org_id=?,department_id=NULL,updated_at=? WHERE id IN (%s)" % marks,
                     [org_id, now()] + id_list)
        affected = len(id_list)
        detail = "批量修改 %d 条记录的组织归属" % affected
    elif action == "recycle":
        conn.execute("UPDATE ip_records SET status='idle',device_name='',owner='',mac_address='',"
                     "business_system='',is_online=0,updated_at=? WHERE id IN (%s)" % marks,
                     [now()] + id_list)
        affected = len(id_list)
        detail = "批量回收 %d 条记录" % affected
    elif action == "reserve":
        conn.execute("UPDATE ip_records SET status='reserved',updated_at=? WHERE id IN (%s)" % marks,
                     [now()] + id_list)
        affected = len(id_list)
        detail = "批量预留 %d 条记录" % affected
    elif action == "bind_tags":
        tags = req.body.get("tags") or []
        if not isinstance(tags, list) or not tags:
            raise ApiError("请至少选择或新增一个标签")
        for tag_id in tags:
            if not conn.execute("SELECT 1 FROM tags WHERE id=?", (tag_id,)).fetchone():
                raise ApiError("所选标签不存在")
        for rid in id_list:
            for tag_id in tags:
                conn.execute("INSERT OR IGNORE INTO record_tags(record_id,tag_id) VALUES(?,?)", (rid, tag_id))
        affected = len(id_list)
        detail = "批量为 %d 条记录绑定标签" % affected
    else:
        raise ApiError("未知批量操作", 404)

    audit(conn, "批量操作", "", detail, req.actor())
    return {"affected": affected, "detail": detail}


# ---------------- 在线探测 ----------------
# 旧实现只用 TCP 连接记录端口（默认 80）判断存活：PC / 终端既不监听 80，
# 系统防火墙又默认拒绝入站连接，导致能 ping 通的在线设备被误判为离线。
# 现改为「ICMP Echo 优先 + 多常见端口 TCP 兜底」双策略，并并发执行。
PROBE_TCP_PORTS = (80, 443, 22, 3389, 445, 8080, 8443, 139, 135, 21, 23, 53, 5900, 9100, 8291)
PROBE_PING_MS = 1000
PROBE_TCP_TIMEOUT = 0.35
PROBE_WORKERS = 16
# RFC 5737 文档专用地址，公网保证不可路由；用于检测透明代理 / 通配路由
PROBE_CONTROL_IP = "192.0.2.1"


def _ping_command(ip, timeout_ms):
    system = platform.system()
    if system == "Windows":
        return ["ping", "-n", "1", "-w", str(int(timeout_ms)), ip]
    if system == "Darwin":
        return ["ping", "-c", "1", "-W", str(int(timeout_ms)), ip]
    # Linux / fnOS：-W 单位为秒，-n 跳过反向 DNS 解析
    return ["ping", "-n", "-c", "1", "-W", str(max(1, int(timeout_ms / 1000))), ip]


def _icmp_alive(ip):
    """调用系统 ping 判断主机是否响应 ICMP Echo；系统无 ping 时返回 False。"""
    if not shutil.which("ping"):
        return False, "no-ping"
    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    try:
        proc = subprocess.run(
            _ping_command(ip, PROBE_PING_MS),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=PROBE_PING_MS / 1000.0 + 2.0, check=False, **kwargs)
        return (proc.returncode == 0), ("ICMP" if proc.returncode == 0 else "")
    except (OSError, subprocess.SubprocessError):
        return False, ""


def _tcp_alive(ip, ports):
    """并发尝试常见端口，任一连通即判在线（覆盖禁 ping 但开放服务的设备）。"""
    def one(port):
        try:
            with socket.create_connection((ip, port), timeout=PROBE_TCP_TIMEOUT):
                return port
        except (OSError, ValueError):
            return None

    with ThreadPoolExecutor(max_workers=min(len(ports), PROBE_WORKERS)) as pool:
        futures = [pool.submit(one, p) for p in ports]
        for fut in as_completed(futures):
            port = fut.result()
            if port:
                return True, "TCP:%d" % port
    return False, ""


def tcp_trustworthy():
    """控制组：若连一个公网保证不可达的地址都能建立 TCP 连接，
    说明网络存在透明代理 / 通配路由，此时 TCP 结果不可作为在线依据。"""
    reachable, _ = _tcp_alive(PROBE_CONTROL_IP, PROBE_TCP_PORTS)
    return not reachable


def probe_host(ip, port=0, tcp_ok=True):
    """返回 (是否在线, 判定依据)。IP 严格校验后才可进入 subprocess 参数。"""
    ip = (ip or "").strip()
    if not valid_ip(ip):
        return False, "invalid-ip"
    alive, via = _icmp_alive(ip)
    if alive:
        return True, via
    if not tcp_ok:
        return False, "-"
    ports = []
    try:
        own = int(port or 0)
    except (TypeError, ValueError):
        own = 0
    if 0 < own < 65536:
        ports.append(own)
    ports.extend(p for p in PROBE_TCP_PORTS if p not in ports)
    alive, via = _tcp_alive(ip, ports)
    return (True, via) if alive else (False, "-")


def probe_records(req, conn):
    ids, _ = _ids(req, 100)
    rows = _visible_records(req, conn, ids)
    if not rows:
        raise ApiError("未找到可探测的记录")
    targets = [(r["id"], (r["ip_address"] or "").strip(), r["port"] or 0) for r in rows]
    tcp_ok = tcp_trustworthy()

    # 探测放在线程池，SQLite 写回仍在主线程完成（连接不在线程间共享）
    results = {}
    with ThreadPoolExecutor(max_workers=min(PROBE_WORKERS, len(targets))) as pool:
        futures = {pool.submit(probe_host, ip, port, tcp_ok): rid for rid, ip, port in targets}
        for fut in as_completed(futures):
            rid = futures[fut]
            try:
                results[rid] = fut.result()
            except Exception:
                results[rid] = (False, "error")

    scan_time = now()
    online = 0
    methods = {}
    items = []
    for rid, ip, port in targets:
        alive, via = results.get(rid, (False, "error"))
        alive = bool(alive)
        conn.execute("UPDATE ip_records SET is_online=?,last_scan_time=? WHERE id=?",
                     (int(alive), scan_time, rid))
        online += int(alive)
        key = via if alive else "offline"
        methods[key] = methods.get(key, 0) + 1
        items.append({"id": rid, "ip_address": ip, "is_online": int(alive), "via": via})

    icmp_ok = shutil.which("ping") is not None
    tcp_hits = sum(v for k, v in methods.items() if k.startswith("TCP:"))
    detail = ("探测 %d 条：在线 %d 条，离线 %d 条（ICMP %d / TCP %d）"
              % (len(targets), online, len(targets) - online, methods.get("ICMP", 0), tcp_hits))
    if not icmp_ok:
        detail += "；系统无 ping 命令，仅用 TCP 端口探测"
    if not tcp_ok:
        detail += "；检测到网络对不可达地址也接受连接，已禁用 TCP 兜底"
    audit(conn, "在线探测", "", detail, req.actor())
    return {"probed": len(targets), "online": online, "offline": len(targets) - online,
            "detail": detail, "scan_time": scan_time, "icmp_available": icmp_ok,
            "tcp_trustworthy": tcp_ok, "tcp_ports": list(PROBE_TCP_PORTS), "items": items}


# ---------------- 子网 / IP 池 ----------------

def subnet_tree(req, conn):
    rows = rows_to_dicts(conn.execute("SELECT * FROM subnets").fetchall())
    nets = []
    for r in rows:
        try:
            r["_net"] = ipaddress.ip_network(r["cidr"], strict=False)
        except Exception:
            continue
        info, _ = cidr_info(r["cidr"])
        r["total"] = info["total"] if info else 0
        r["used"] = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE subnet_id=?", (r["id"],)).fetchone()["c"]
        nets.append(r)
    nets.sort(key=lambda x: (x["_net"].version, x["_net"].prefixlen, str(x["_net"])))
    nodes = []
    for r in nets:
        parent = None
        for p in nodes:
            if p["_net"].version == r["_net"].version and p["_net"].prefixlen < r["_net"].prefixlen \
                    and r["_net"].subnet_of(p["_net"]):
                parent = p
        node = {"id": r["id"], "cidr": r["cidr"], "name": r["name"], "total": r["total"],
                "used": r["used"], "children": [], "_net": r["_net"]}
        if parent:
            parent["children"].append(node)
        else:
            nodes.append(node)

    def clean(n):
        n.pop("_net", None)
        for c in n["children"]:
            clean(c)
        return n
    return {"items": [clean(n) for n in nodes], "total": len(nets)}


def subnet_overview(req, conn):
    rows = rows_to_dicts(conn.execute("SELECT * FROM subnets").fetchall())
    out = []
    for r in rows:
        info, _ = cidr_info(r["cidr"])
        used = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE subnet_id=?", (r["id"],)).fetchone()["c"]
        reserved = conn.execute("SELECT COUNT(*) c FROM reserved_ips WHERE subnet_id=?", (r["id"],)).fetchone()["c"]
        total = info["total"] if info else 0
        item = {"id": r["id"], "cidr": r["cidr"], "name": r["name"], "total": total,
                "used": used, "reserved": reserved,
                "utilization": round(min(used / total * 100, 100), 1) if total else 0}
        if req.q("subnet_id") == r["id"]:
            taken = {x["ip_address"]: x["status"] for x in conn.execute(
                "SELECT ip_address,status FROM ip_records WHERE subnet_id=?", (r["id"],)).fetchall()}
            res = {x["ip_address"] for x in conn.execute(
                "SELECT ip_address FROM reserved_ips WHERE subnet_id=?", (r["id"],)).fetchall()}
            addrs = []
            if info and total <= 1024:
                net = ipaddress.ip_network(info["cidr"], strict=False)
                for host in net.hosts():
                    if len(addrs) >= 1024:
                        break
                    ip = str(host)
                    addrs.append({"ip": ip, "state": taken.get(ip, "reserved" if ip in res else "free")})
            item["addresses"] = addrs
        out.append(item)
    return {"items": out, "total": len(out)}


def pool_stats(conn, pool_id):
    pool = conn.execute("SELECT * FROM ip_pools WHERE id=?", (pool_id,)).fetchone()
    if not pool:
        raise ApiError("地址池不存在", 404)
    addrs, err = pool_addresses(pool["range_start"], pool["range_end"])
    if err:
        raise ApiError(err)
    taken = {r["ip_address"] for r in conn.execute("SELECT ip_address,status FROM ip_records").fetchall()
             if r["status"] != "idle"}
    reserved = {r["ip_address"] for r in conn.execute("SELECT ip_address FROM reserved_ips").fetchall()}
    free = [a for a in addrs if a not in taken and a not in reserved]
    return pool, addrs, free


def allocate_ip(req, conn, pool_id):
    pool, addrs, free = pool_stats(conn, pool_id)
    if not free:
        raise ApiError("地址池中没有可分配地址")
    ip = free[0]
    cidrs = rows_to_dicts(conn.execute("SELECT id,cidr FROM subnets").fetchall())
    from core import infer_subnet
    rid = new_id()
    try:
        conn.execute("INSERT INTO ip_records(id,ip_address,port,subnet_id,status,device_name,remark,"
                     "created_at,updated_at,created_by) VALUES(?,?,?,?,?,?,?,?,?,?)",
                     (rid, ip, 0, infer_subnet(cidrs, ip) or pool["subnet_id"], "in_use",
                      str(req.v("device_name") or ""), "由地址池 %s 分配" % pool["name"],
                      now(), now(), req.actor()))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ApiError("地址分配冲突，请重试")
        raise ApiError("地址分配失败：%s" % e)
    audit(conn, "地址池分配", ip, pool["name"], req.actor())
    return main_api.record_get(req, conn, rid)


def release_ip(req, conn, pool_id):
    pool, addrs, _ = pool_stats(conn, pool_id)
    ip = str(req.v("ip_address") or "").strip()
    if ip not in addrs:
        raise ApiError("该 IP 不属于当前地址池范围")
    row = conn.execute("SELECT id FROM ip_records WHERE ip_address=? AND status!='idle' ORDER BY created_at DESC LIMIT 1",
                       (ip,)).fetchone()
    if not row:
        raise ApiError("未找到可回收记录")
    conn.execute("UPDATE ip_records SET status='idle',device_name='',owner='',mac_address='',"
                 "business_system='',is_online=0,updated_at=? WHERE id=?", (now(), row["id"]))
    conn.execute("DELETE FROM record_tags WHERE record_id=?", (row["id"],))
    conn.execute("DELETE FROM asset_ip_bindings WHERE record_id=?", (row["id"],))
    audit(conn, "地址池回收", ip, pool["name"], req.actor())
    return {"released": ip}


# ---------------- 资产与 IP 绑定 ----------------

def asset_bindings(req, conn, asset_id):
    if not conn.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
        raise ApiError("资产不存在", 404)
    rows = conn.execute(
        "SELECT b.*, r.ip_address, r.device_name, r.status, r.port "
        "FROM asset_ip_bindings b JOIN ip_records r ON r.id=b.record_id "
        "WHERE b.asset_id=? ORDER BY b.created_at DESC", (asset_id,)).fetchall()
    items = rows_to_dicts(rows)
    for it in items:
        it["status_text"] = STATUS_LABELS.get(it["status"], it["status"])
    return {"items": items, "total": len(items)}


def binding_options(req, conn, asset_id):
    rows = conn.execute(
        "SELECT id,ip_address,port,device_name,status FROM ip_records "
        "WHERE id NOT IN (SELECT record_id FROM asset_ip_bindings WHERE asset_id=?) "
        "ORDER BY created_at DESC LIMIT 500", (asset_id,)).fetchall()
    items = rows_to_dicts(rows)
    return {"items": items, "total": len(items)}


def add_binding(req, conn, asset_id):
    record_id = str(req.v("record_id") or "")
    if not record_id:
        raise ApiError("请选择 IP 记录")
    if not conn.execute("SELECT 1 FROM assets WHERE id=?", (asset_id,)).fetchone():
        raise ApiError("资产不存在", 404)
    row = conn.execute("SELECT org_id FROM ip_records WHERE id=?", (record_id,)).fetchone()
    if not row:
        raise ApiError("未找到对应 IP 记录")
    main_api.check_org_scope(req, conn, row["org_id"])
    if conn.execute("SELECT 1 FROM asset_ip_bindings WHERE asset_id=? AND record_id=?",
                    (asset_id, record_id)).fetchone():
        raise ApiError("该 IP 已绑定到当前资产")
    rid = new_id()
    conn.execute("INSERT INTO asset_ip_bindings(id,asset_id,record_id,binding_type,created_at) VALUES(?,?,?,?,?)",
                 (rid, asset_id, record_id, req.v("binding_type", "primary") or "primary", now()))
    audit(conn, "资产绑定", asset_id, record_id, req.actor())
    return {"id": rid}


def delete_binding(req, conn, asset_id, binding_id):
    cur = conn.execute("DELETE FROM asset_ip_bindings WHERE id=? AND asset_id=?", (binding_id, asset_id))
    if cur.rowcount == 0:
        raise ApiError("绑定不存在", 404)
    audit(conn, "解除绑定", asset_id, binding_id, req.actor())
    return {"deleted": binding_id}


# ---------------- 统计 ----------------

def dashboard_stats(req, conn):
    def c(sql, args=()):
        return conn.execute(sql, args).fetchone()["c"]
    scopesql, scopeargs = main_api.scope_where(req)
    where = ("WHERE 1=1 " + scopesql.replace(" AND ", " AND ")) if scopesql else ""
    records = c("SELECT COUNT(*) c FROM ip_records r " + where, scopeargs)
    in_use = c("SELECT COUNT(*) c FROM ip_records r WHERE r.status='in_use' " + scopesql, scopeargs)
    idle = c("SELECT COUNT(*) c FROM ip_records r WHERE r.status='idle' " + scopesql, scopeargs)
    reserved = c("SELECT COUNT(*) c FROM ip_records r WHERE r.status='reserved' " + scopesql, scopeargs)
    online = c("SELECT COUNT(*) c FROM ip_records r WHERE r.is_online=1 " + scopesql, scopeargs)
    return {
        "records": records, "in_use": in_use, "idle": idle, "reserved": reserved,
        "online": online, "offline": max(records - online, 0),
        "subnets": c("SELECT COUNT(*) c FROM subnets"),
        "vlans": c("SELECT COUNT(*) c FROM vlans"),
        "pools": c("SELECT COUNT(*) c FROM ip_pools"),
        "assets": c("SELECT COUNT(*) c FROM assets"),
        "organizations": c("SELECT COUNT(*) c FROM organizations"),
        "tags": c("SELECT COUNT(*) c FROM tags"),
        "audit_logs": c("SELECT COUNT(*) c FROM audit_logs"),
        "recent_records": rows_to_dicts(conn.execute(
            "SELECT id,ip_address,device_name,status,created_at FROM ip_records "
            "ORDER BY created_at DESC LIMIT 8").fetchall()),
        "recent_logs": rows_to_dicts(conn.execute(
            "SELECT action,detail,operator,created_at FROM audit_logs ORDER BY created_at DESC LIMIT 8").fetchall()),
    }


def utilization_stats(req, conn):
    rows = rows_to_dicts(conn.execute("SELECT * FROM subnets ORDER BY cidr").fetchall())
    out = []
    for r in rows:
        info, _ = cidr_info(r["cidr"])
        total = info["total"] if info else 0
        used = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE subnet_id=? AND status!='idle'", (r["id"],)).fetchone()["c"]
        out.append({"id": r["id"], "cidr": r["cidr"], "name": r["name"], "total": total,
                    "used": used, "free": max(total - used, 0),
                    "utilization": round(min(used / total * 100, 100), 1) if total else 0})
    return {"items": out, "total": len(out),
            "summary": {"total": sum(x["total"] for x in out), "used": sum(x["used"] for x in out)}}


def conflict_stats(req, conn):
    issues = []

    dup = conn.execute("SELECT ip_address,port,COUNT(*) c FROM ip_records "
                       "GROUP BY ip_address,port HAVING c>1 ORDER BY c DESC").fetchall()
    for r in dup:
        issues.append({"type": "duplicate_ip", "level": "high",
                       "detail": "IP %s:%s 存在 %d 条重复记录（不同单位）" % (r["ip_address"], r["port"], r["c"])})

    dupmac = conn.execute("SELECT mac_address,COUNT(*) c FROM ip_records "
                          "WHERE mac_address!='' GROUP BY mac_address HAVING c>1").fetchall()
    for r in dupmac:
        issues.append({"type": "duplicate_mac", "level": "medium",
                       "detail": "MAC %s 被 %d 条记录重复使用" % (r["mac_address"], r["c"])})

    subnets = rows_to_dicts(conn.execute("SELECT id,cidr FROM subnets").fetchall())
    for i, a in enumerate(subnets):
        try:
            na = ipaddress.ip_network(a["cidr"], strict=False)
        except Exception:
            continue
        for b in subnets[i + 1:]:
            try:
                nb = ipaddress.ip_network(b["cidr"], strict=False)
            except Exception:
                continue
            if na.version == nb.version and na.overlaps(nb):
                issues.append({"type": "subnet_overlap", "level": "medium",
                               "detail": "子网 %s 与 %s 重叠" % (a["cidr"], b["cidr"])})

    orphan = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE subnet_id IS NULL OR subnet_id=''").fetchone()["c"]
    if orphan:
        issues.append({"type": "no_subnet", "level": "low", "detail": "有 %d 条 IP 记录未归属任何子网" % orphan})

    for row in conn.execute("SELECT r.ip_address,r.subnet_id,s.cidr FROM ip_records r "
                            "JOIN subnets s ON s.id=r.subnet_id").fetchall():
        try:
            if ipaddress.ip_address(row["ip_address"]) not in ipaddress.ip_network(row["cidr"], strict=False):
                issues.append({"type": "out_of_subnet", "level": "high",
                               "detail": "IP %s 不属于所属子网 %s" % (row["ip_address"], row["cidr"])})
        except Exception:
            pass
        if len(issues) >= 200:
            break

    reserved_hit = conn.execute(
        "SELECT rs.ip_address FROM reserved_ips rs JOIN ip_records r ON r.ip_address=rs.ip_address "
        "WHERE r.status!='reserved' LIMIT 50").fetchall()
    for r in reserved_hit:
        issues.append({"type": "reserved_conflict", "level": "medium",
                       "detail": "保留地址 %s 已被普通记录占用" % r["ip_address"]})

    return {"items": issues[:200], "total": len(issues)}


def asset_distribution(req, conn):
    def group(col):
        return rows_to_dicts(conn.execute(
            "SELECT COALESCE(NULLIF(%s,''),'未填写') AS label, COUNT(*) c FROM assets "
            "GROUP BY label ORDER BY c DESC LIMIT 30" % col).fetchall())
    return {"by_type": group("asset_type"), "by_status": group("status"),
            "by_brand": group("brand"), "by_location": group("location"),
            "status_labels": ASSET_STATUS_LABELS}


def system_stats(req, conn):
    cfg = req.cfg
    size = os.path.getsize(cfg.db_path) if os.path.exists(cfg.db_path) else 0
    tables = {}
    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
        tables[row["name"]] = conn.execute("SELECT COUNT(*) c FROM %s" % row["name"]).fetchone()["c"]
    backups = len([f for f in os.listdir(cfg.backup_dir) if safe_name(f)]) if os.path.isdir(cfg.backup_dir) else 0
    return {"version": APP_VERSION, "python": platform.python_version(),
            "platform": platform.platform(), "db_size": size, "db_size_text": "%.2f MB" % (size / 1048576),
            "tables": tables, "backups": backups, "server_time": now()}


def filter_options(req, conn):
    def simple(sql, key="name"):
        return rows_to_dicts(conn.execute(sql).fetchall())
    return {
        "organizations": simple("SELECT id,name FROM organizations ORDER BY name"),
        "departments": simple("SELECT id,name,org_id FROM departments ORDER BY name"),
        "subnets": simple("SELECT id,cidr,name FROM subnets ORDER BY cidr"),
        "vlans": simple("SELECT id,vlan_id,name FROM vlans ORDER BY vlan_id"),
        "pools": simple("SELECT id,name FROM ip_pools ORDER BY name"),
        "tags": simple("SELECT id,name,color FROM tags ORDER BY name"),
        "assets": simple("SELECT id,asset_name,asset_number FROM assets ORDER BY asset_name"),
        "locations": simple("SELECT id,name,node_type,parent_id FROM location_nodes ORDER BY name"),
        "status": [{"value": k, "label": v} for k, v in STATUS_LABELS.items()],
        "asset_status": [{"value": k, "label": v} for k, v in ASSET_STATUS_LABELS.items()],
        "device_types": [x[0] for x in conn.execute(
            "SELECT DISTINCT device_type FROM ip_records WHERE device_type!='' ORDER BY device_type LIMIT 50").fetchall()],
        "asset_types": [x[0] for x in conn.execute(
            "SELECT DISTINCT asset_type FROM assets WHERE asset_type!='' ORDER BY asset_type LIMIT 50").fetchall()],
    }


# ---------------- CSV 导入 / 导出 ----------------

RECORD_COLS = [
    ("ip_address", "IP地址"), ("port", "端口"), ("device_name", "设备名称"),
    ("device_type", "设备类型"), ("mac_address", "MAC地址"), ("status", "状态"),
    ("owner", "使用人"), ("organization", "单位"), ("department", "部门"),
    ("subnet", "子网"), ("business_system", "业务系统"), ("remark", "备注"),
]
ASSET_COLS = [
    ("asset_number", "资产编号"), ("asset_name", "资产名称"), ("asset_type", "资产类型"),
    ("brand", "品牌"), ("model", "型号"), ("serial_number", "序列号"),
    ("status", "状态"), ("location", "位置"), ("owner", "负责人"),
    ("purchase_date", "购买日期"), ("warranty_expire_at", "保修到期"), ("remark", "备注"),
]
STATUS_REVERSE = {v: k for k, v in STATUS_LABELS.items()}
ASSET_STATUS_REVERSE = {v: k for k, v in ASSET_STATUS_LABELS.items()}


def _decode_csv_bytes(raw):
    for enc in ("utf-8-sig", "utf-8", "gbk", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    raise ApiError("文件编码无法识别")


def _read_csv(text):
    text = text.lstrip("\ufeff")
    try:
        dialect = csv.Sniffer().sniff(text[:2048], delimiters=",;\t")
    except Exception:
        dialect = csv.excel
    return list(csv.DictReader(io.StringIO(text), dialect=dialect))


def _col(row, cn, en):
    for key in (cn, en, cn.replace("地址", "地址"), en):
        if key in row and row[key] is not None:
            return str(row[key]).strip()
    return ""


def _parse_import(req, conn, text, kind):
    cols = RECORD_COLS if kind == "records" else ASSET_COLS
    rows = _read_csv(text)
    if not rows:
        raise ApiError("CSV 内容为空或格式不正确")
    orgs = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM organizations").fetchall()}
    depts = {r["name"]: r["id"] for r in conn.execute("SELECT id,name FROM departments").fetchall()}
    subnets = {r["cidr"]: r["id"] for r in conn.execute("SELECT id,cidr FROM subnets").fetchall()}
    result = []
    for idx, raw in enumerate(rows[:5000], start=2):
        item = {"_line": idx, "_errors": []}
        for en, cn in cols:
            item[en] = _col(raw, cn, en)
        if kind == "records":
            if not item["ip_address"]:
                item["_errors"].append("IP 地址不能为空")
            elif not valid_ip(item["ip_address"]):
                item["_errors"].append("IP 地址格式不正确")
            p = parse_int(item["port"], 0, 0, 65535)
            item["port"] = p
            mac = norm_mac(item["mac_address"])
            if mac is None:
                item["_errors"].append("MAC 地址格式不正确")
            item["mac_address"] = mac or ""
            st = item["status"]
            item["status"] = STATUS_REVERSE.get(st, st if st in STATUS_LABELS else "in_use")
            item["organization_id"] = orgs.get(item["organization"]) if item["organization"] else None
            item["department_id"] = depts.get(item["department"]) if item["department"] else None
            item["subnet_id"] = subnets.get(item["subnet"]) if item["subnet"] else None
            if item["organization"] and not item["organization_id"]:
                item["_errors"].append("单位「%s」不存在，将留空" % item["organization"])
            if item["subnet"] and not item["subnet_id"]:
                item["_errors"].append("子网「%s」不存在，将自动推断" % item["subnet"])
            if not item["subnet_id"]:
                from core import infer_subnet
                cidrs = rows_to_dicts(conn.execute("SELECT id,cidr FROM subnets").fetchall())
                item["subnet_id"] = infer_subnet(cidrs, item["ip_address"])
            main_api.check_org_scope(req, conn, item["organization_id"])
        else:
            if not item["asset_number"] or not item["asset_name"]:
                item["_errors"].append("资产编号和资产名称不能为空")
            st = item["status"]
            item["status"] = ASSET_STATUS_REVERSE.get(st, st if st in ASSET_STATUS_LABELS else "in_use")
        result.append(item)
    valid = [x for x in result if not x["_errors"]]
    return {"columns": [cn for _, cn in cols], "rows": result, "total": len(result),
            "valid": len(valid), "invalid": len(result) - len(valid), "kind": kind}


def import_preview(req, conn):
    kind = req.v("kind", "records") or "records"
    text = req.body.get("csv") or ""
    if not isinstance(text, str) or not text.strip():
        raise ApiError("请选择CSV文件")
    return _parse_import(req, conn, text, kind)


def import_commit(req, conn):
    kind = req.v("kind", "records") or "records"
    rows = req.body.get("rows") or []
    if not isinstance(rows, list) or not rows:
        raise ApiError("没有可导入的数据")
    added, skipped, errors = 0, 0, []
    for item in rows[:5000]:
        if not isinstance(item, dict):
            continue
        if item.get("_errors"):
            skipped += 1
            continue
        try:
            if kind == "records":
                payload = {k: item.get(k, "") for k, _ in RECORD_COLS}
                payload.update({"org_id": item.get("organization_id"), "department_id": item.get("department_id"),
                                "subnet_id": item.get("subnet_id"), "port": item.get("port", 0)})
                out = main_api.validate_record(req, conn, payload, True)
                rid = new_id()
                out.update({"id": rid, "created_at": now(), "updated_at": now(), "created_by": req.actor()})
                keys = list(out.keys())
                conn.execute("INSERT INTO ip_records(%s) VALUES(%s)" %
                             (",".join(keys), ",".join("?" for _ in keys)), list(out.values()))
            else:
                keys = [k for k, _ in ASSET_COLS]
                values = [item.get(k, "") for k in keys]
                conn.execute("INSERT INTO assets(id,%s,created_at,updated_at) VALUES(?,%s,?,?)" %
                             (",".join(keys), ",".join("?" for _ in keys)),
                             [new_id()] + values + [now(), now()])
            added += 1
        except Exception as e:
            skipped += 1
            errors.append("第 %s 行: %s" % (item.get("_line", "?"), e))
    audit(conn, "导入CSV", kind, "新增 %d 条，跳过 %d 条" % (added, skipped), req.actor())
    return {"added": added, "skipped": skipped, "errors": errors[:30]}


def import_fix(req, conn):
    """导出可修复的错误行 CSV。"""
    rows = req.body.get("rows") or []
    cols = [cn for _, cn in (RECORD_COLS if req.v("kind", "records") == "records" else ASSET_COLS)]
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["行号", "错误原因"] + cols)
    for item in rows:
        if not item.get("_errors"):
            continue
        writer.writerow([item.get("_line", ""), "；".join(item["_errors"])] +
                        [item.get(en, "") for en, _ in (RECORD_COLS if req.v("kind", "records") == "records" else ASSET_COLS)])
    return RawResponse(("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="import_fix.csv"'})


def export_csv(req, conn):
    kind = req.q("type", "records")
    if kind not in ("records", "assets"):
        kind = "records"
    buf = io.StringIO()
    if kind == "records":
        cols = RECORD_COLS
        writer = csv.writer(buf)
        writer.writerow([cn for _, cn in cols])
        items = main_api.record_select(conn)
        for it in items:
            writer.writerow([
                it["ip_address"], it["port"], it["device_name"], it["device_type"], it["mac_address"],
                it["status_text"], it["owner"], it["org_name"], it["dept_name"], it["subnet_cidr"],
                it["business_system"], it["remark"]])
    else:
        cols = ASSET_COLS
        writer = csv.writer(buf)
        writer.writerow([cn for _, cn in cols])
        for it in conn.execute("SELECT * FROM assets ORDER BY created_at DESC").fetchall():
            writer.writerow([it[k] if it[k] is not None else "" for k, _ in cols])
    return RawResponse(("\ufeff" + buf.getvalue()).encode("utf-8"), "text/csv; charset=utf-8",
                       {"Content-Disposition": 'attachment; filename="%s_export.csv"' % kind})


# ---------------- 备份 ----------------

def list_backups(req, conn):
    cfg = req.cfg
    items = []
    for name in sorted(os.listdir(cfg.backup_dir), reverse=True) if os.path.isdir(cfg.backup_dir) else []:
        if not safe_name(name):
            continue
        path = os.path.join(cfg.backup_dir, name)
        items.append({"name": name, "size": os.path.getsize(path),
                      "size_text": "%.2f MB" % (os.path.getsize(path) / 1048576),
                      "created_at": datetime.fromtimestamp(os.path.getmtime(path)).strftime("%Y-%m-%d %H:%M:%S")})
    return {"items": items, "total": len(items)}


def create_backup(req, conn):
    cfg = req.cfg
    name = "backup_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(cfg.backup_dir, name)
    if os.path.exists(path):
        raise ApiError("备份已存在，请稍后再试")
    # 源连接必须结束写事务，否则 SQLite backup 在 WAL 下可能反复重启而挂起
    conn.commit()
    dst = sqlite3.connect(path)
    try:
        conn.backup(dst, pages=0, sleep=0.05)
    finally:
        dst.close()
    audit(conn, "备份", name, "创建数据库备份", req.actor())
    return {"name": name, "size": os.path.getsize(path)}


def delete_backup(req, conn, name):
    if not safe_name(name):
        raise ApiError("备份文件名不合法", 400)
    path = os.path.join(req.cfg.backup_dir, name)
    if not os.path.isfile(path):
        raise ApiError("备份不存在", 404)
    os.remove(path)
    audit(conn, "删除备份", name, "", req.actor())
    return {"deleted": name}


def download_backup(req, conn, name):
    if not safe_name(name):
        raise ApiError("备份文件名不合法", 400)
    path = os.path.join(req.cfg.backup_dir, name)
    if not os.path.isfile(path):
        raise ApiError("备份文件不存在", 404)
    with open(path, "rb") as f:
        data = f.read()
    return RawResponse(data, "application/octet-stream",
                       {"Content-Disposition": 'attachment; filename="%s"' % name})


def restore_backup(req, conn, name):
    if not safe_name(name):
        raise ApiError("备份文件名不合法", 400)
    path = os.path.join(req.cfg.backup_dir, name)
    if not os.path.isfile(path):
        raise ApiError("备份不存在", 404)
    safety = os.path.join(req.cfg.backup_dir, "backup_%s.db" % datetime.now().strftime("%Y%m%d_%H%M%S"))
    if not os.path.exists(safety):
        tmp = sqlite3.connect(safety)
        try:
            conn.backup(tmp)
        finally:
            tmp.close()
    conn.commit()
    src = sqlite3.connect(path)
    try:
        src.backup(conn, pages=0, sleep=0.05)
    except Exception as e:
        raise ApiError("恢复失败：%s" % e)
    finally:
        src.close()
    return {"restored": name, "safety_backup": os.path.basename(safety),
            "message": "已从备份恢复，请刷新页面重新登录"}


# ---------------- 审计 / 设置 ----------------

def audit_logs(req, conn):
    page = max(parse_int(req.q("page"), 1, 1), 1)
    per_page = parse_int(req.q("per_page"), 50, 1, 500)
    parts, args = [], []
    if req.q("q"):
        parts.append("(action LIKE ? OR detail LIKE ? OR operator LIKE ? OR target LIKE ?)")
        args += ["%%%s%%" % req.q("q")] * 4
    if req.q("action"):
        parts.append("action=?")
        args.append(req.q("action"))
    where = ("WHERE " + " AND ".join(parts)) if parts else ""
    total = conn.execute("SELECT COUNT(*) c FROM audit_logs " + where, args).fetchone()["c"]
    rows = conn.execute("SELECT * FROM audit_logs " + where + " ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                        args + [per_page, (page - 1) * per_page]).fetchall()
    audit_actions = [r[0] for r in conn.execute("SELECT DISTINCT action FROM audit_logs ORDER BY action LIMIT 60").fetchall()]
    return {"items": rows_to_dicts(rows), "total": total, "page": page, "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page), "actions": audit_actions}


def clear_audit_logs(req, conn):
    conn.execute("DELETE FROM audit_logs")
    audit(conn, "清空审计日志", "", "", req.actor())
    return {"cleared": True}


def settings_info(req, conn):
    branding = load_branding(req.cfg)
    return {"app_name": get_setting(conn, "app_name", "") or branding["app_name"],
            "field_labels": json.loads(get_setting(conn, "field_labels", "{}") or "{}"),
            "system_mode": get_setting(conn, "system_mode", "normal"),
            "branding": branding, "version": APP_VERSION}


def set_app_name(req, conn):
    name = str(req.v("app_name") or "").strip()
    if not name or len(name) > 60:
        raise ApiError("系统名称不能为空且不能超过 60 个字符")
    set_setting(conn, "app_name", name)
    save_branding(req.cfg, {"app_name": name})
    audit(conn, "更新系统名称", "", name, req.actor())
    return {"app_name": name}


def set_field_labels(req, conn):
    labels = req.body.get("field_labels")
    if not isinstance(labels, dict):
        raise ApiError("字段标签格式不正确")
    clean = {str(k)[:40]: str(v)[:40] for k, v in labels.items()}
    set_setting(conn, "field_labels", json.dumps(clean, ensure_ascii=False))
    audit(conn, "更新字段显示名称", "", "", req.actor())
    return clean


def set_system_mode(req, conn):
    mode = str(req.v("mode") or "").strip()
    if mode not in ("normal", "compact", "readonly"):
        raise ApiError("模式不合法")
    set_setting(conn, "system_mode", mode)
    audit(conn, "切换模式", "", mode, req.actor())
    return {"mode": mode}


def set_branding(req, conn):
    data = save_branding(req.cfg, req.body)
    audit(conn, "更新版权与联系信息", "", data.get("owner_name", ""), req.actor())
    return data


# ---------------- 路由注册 ----------------

def register_ops(r):
    r.add("POST", "^/api/records/batch_(?P<action>delete|status|organization|owner|recycle|reserve|bind_tags)$", batch_records)
    r.add("POST", "^/api/records/probe$", probe_records, admin=True)

    r.add("GET", "^/api/subnets/tree$", subnet_tree)
    r.add("GET", "^/api/subnet/overview$", subnet_overview)
    r.add("POST", "^/api/ip_pools/(?P<pool_id>[^/]+)/allocate$", allocate_ip)
    r.add("POST", "^/api/ip_pools/(?P<pool_id>[^/]+)/release$", release_ip)

    r.add("GET", "^/api/assets/(?P<asset_id>[^/]+)/bindings$", asset_bindings)
    r.add("POST", "^/api/assets/(?P<asset_id>[^/]+)/bindings$", add_binding)
    r.add("GET", "^/api/assets/(?P<asset_id>[^/]+)/bindings/options$", binding_options)
    r.add("DELETE", "^/api/assets/(?P<asset_id>[^/]+)/bindings/(?P<binding_id>[^/]+)$", delete_binding)

    r.add("GET", "^/api/dashboard/stats$", dashboard_stats)
    r.add("GET", "^/api/system/stats$", system_stats)
    r.add("GET", "^/api/filter_options$", filter_options)
    r.add("GET", "^/api/statistics/utilization$", utilization_stats)
    r.add("GET", "^/api/statistics/conflicts$", conflict_stats)
    r.add("GET", "^/api/ipam/conflicts$", conflict_stats)
    r.add("GET", "^/api/statistics/asset_distribution$", asset_distribution)

    r.add("POST", "^/api/import_csv/preview$", import_preview)
    r.add("POST", "^/api/import_csv$", import_commit)
    r.add("POST", "^/api/import_csv/fix$", import_fix)
    r.add("POST", "^/api/assets/import_csv$", lambda req, conn: import_preview(_with_kind(req, "assets"), conn))
    r.add("GET", "^/api/export_csv$", export_csv)
    r.add("GET", "^/api/assets/export_csv$", lambda req, conn: export_csv(_with_type(req, "assets"), conn))

    r.add("GET", "^/api/backups$", list_backups)
    r.add("POST", "^/api/backup$", create_backup)
    r.add("GET", "^/api/backup/(?P<name>[^/]+)$", download_backup)
    r.add("DELETE", "^/api/backup/(?P<name>[^/]+)$", delete_backup)
    r.add("POST", "^/api/backup/(?P<name>[^/]+)/restore$", restore_backup)

    r.add("GET", "^/api/audit_logs$", audit_logs)
    r.add("POST", "^/api/audit_logs/clear$", clear_audit_logs, admin=True)

    r.add("GET", "^/api/settings/info$", settings_info)
    r.add("POST", "^/api/settings/app_name$", set_app_name, admin=True)
    r.add("POST", "^/api/settings/field_labels$", set_field_labels, admin=True)
    r.add("POST", "^/api/settings/system_mode$", set_system_mode, admin=True)
    r.add("POST", "^/api/settings/branding$", set_branding, admin=True)


def _with_kind(req, kind):
    body = dict(req.body)
    body["kind"] = kind
    req._body = body
    return req


def _with_type(req, kind):
    req.query["type"] = kind
    return req
