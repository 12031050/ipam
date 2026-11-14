# -*- coding: utf-8 -*-
"""IPAM fnOS 版 API：资源定义与核心业务路由。"""

import json
import re

from core import (
    APP_VERSION, ASSET_STATUS_LABELS, NODE_TYPE_LABELS, STATUS_LABELS,
    audit, cidr_info, get_setting, hash_password, infer_subnet, ip_key,
    load_branding, login, logout, new_id, norm_mac, now, parse_int,
    pool_addresses, rows_to_dicts, safe_name, save_branding, session_user,
    set_setting, subnet_overlap, valid_ip, verify_password,
)
from router import ApiError, RawResponse, Resource, Router, register_crud


# ---------------- 通用资源 ----------------

class Organizations(Resource):
    table = "organizations"
    fields = ("name", "description")
    required = ("name",)
    searchable = ("name", "description")
    order = "name ASC"
    label = "单位"

    def decorate(self, conn, items):
        for it in items:
            it["dept_count"] = conn.execute("SELECT COUNT(*) c FROM departments WHERE org_id=?", (it["id"],)).fetchone()["c"]
            it["record_count"] = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE org_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("UPDATE ip_records SET org_id=NULL, department_id=NULL WHERE org_id=?", (row_id,))
        conn.execute("DELETE FROM departments WHERE org_id=?", (row_id,))


class Departments(Resource):
    table = "departments"
    fields = ("org_id", "name")
    required = ("name",)
    searchable = ("name",)
    order = "name ASC"
    label = "部门"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if out.get("org_id") == "":
            out["org_id"] = None
        if out.get("org_id") and not conn.execute("SELECT 1 FROM organizations WHERE id=?", (out["org_id"],)).fetchone():
            raise ApiError("所选单位不存在")
        return out

    def extra_where(self, req, conn):
        if req.q("org_id"):
            return "org_id=?", [req.q("org_id")]
        return "", []

    def decorate(self, conn, items):
        for it in items:
            row = conn.execute("SELECT name FROM organizations WHERE id=?", (it["org_id"] or "",)).fetchone()
            it["org_name"] = row["name"] if row else ""
            it["record_count"] = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE department_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("UPDATE ip_records SET department_id=NULL WHERE department_id=?", (row_id,))


class Vlans(Resource):
    table = "vlans"
    fields = ("vlan_id", "name", "purpose", "description")
    required = ("vlan_id",)
    searchable = ("name", "purpose", "description")
    order = "vlan_id ASC"
    label = "VLAN"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if "vlan_id" in out:
            vid = parse_int(out["vlan_id"], -1)
            if vid < 1 or vid > 4094:
                raise ApiError("VLAN 范围必须是 1-4094")
            out["vlan_id"] = vid
        return out

    def decorate(self, conn, items):
        for it in items:
            it["subnet_count"] = conn.execute("SELECT COUNT(*) c FROM subnets WHERE vlan_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("UPDATE subnets SET vlan_id=NULL WHERE vlan_id=?", (row_id,))


class Subnets(Resource):
    table = "subnets"
    fields = ("cidr", "name", "vlan_id", "gateway", "description")
    required = ("cidr",)
    searchable = ("cidr", "name", "description", "gateway")
    order = "cidr ASC"
    label = "子网"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if "cidr" in out:
            info, err = cidr_info(out["cidr"])
            if err:
                raise ApiError(err)
            out["cidr"] = info["cidr"]
            existing = rows_to_dicts(conn.execute("SELECT id,cidr FROM subnets").fetchall())
            hits = subnet_overlap(out["cidr"], existing, exclude_id=req.query.get("_id"))
            if hits:
                raise ApiError("子网与现有网段重叠: %s" % ", ".join(hits))
        if out.get("gateway") and not valid_ip(out["gateway"]):
            raise ApiError("网关地址格式不正确")
        if out.get("vlan_id") == "":
            out["vlan_id"] = None
        return out

    def update(self, req, conn, row_id):
        req.query["_id"] = row_id
        return super().update(req, conn, row_id)

    def decorate(self, conn, items):
        for it in items:
            info, _ = cidr_info(it["cidr"])
            it["total"] = info["total"] if info else 0
            it["network"] = info["network"] if info else ""
            it["broadcast"] = info["broadcast"] if info else ""
            it["version"] = info["version"] if info else 4
            it["used"] = conn.execute("SELECT COUNT(*) c FROM ip_records WHERE subnet_id=?", (it["id"],)).fetchone()["c"]
            it["reserved"] = conn.execute("SELECT COUNT(*) c FROM reserved_ips WHERE subnet_id=?", (it["id"],)).fetchone()["c"]
            it["utilization"] = round(min(it["used"] / it["total"] * 100, 100), 1) if it["total"] else 0
            v = conn.execute("SELECT vlan_id,name FROM vlans WHERE id=?", (it["vlan_id"] or "",)).fetchone()
            it["vlan_number"] = v["vlan_id"] if v else ""
            it["vlan_name"] = v["name"] if v else ""
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("UPDATE ip_records SET subnet_id=NULL WHERE subnet_id=?", (row_id,))
        conn.execute("UPDATE reserved_ips SET subnet_id=NULL WHERE subnet_id=?", (row_id,))
        conn.execute("UPDATE ip_pools SET subnet_id=NULL WHERE subnet_id=?", (row_id,))


class AddressSpaces(Resource):
    table = "address_spaces"
    fields = ("name", "cidr", "description")
    required = ("name", "cidr")
    searchable = ("name", "cidr", "description")
    order = "name ASC"
    label = "地址空间"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if "cidr" in out:
            info, err = cidr_info(out["cidr"])
            if err:
                raise ApiError(err)
            out["cidr"] = info["cidr"]
        return out

    def decorate(self, conn, items):
        for it in items:
            info, _ = cidr_info(it["cidr"])
            it["total"] = info["total"] if info else 0
            it["subnet_count"] = 0
            if info:
                import ipaddress
                net = ipaddress.ip_network(info["cidr"], strict=False)
                for row in conn.execute("SELECT cidr FROM subnets").fetchall():
                    try:
                        if ipaddress.ip_network(row["cidr"], strict=False).subnet_of(net):
                            it["subnet_count"] += 1
                    except Exception:
                        pass
        return items


class IpPools(Resource):
    table = "ip_pools"
    fields = ("name", "range_start", "range_end", "subnet_id", "description")
    required = ("name", "range_start", "range_end")
    searchable = ("name", "range_start", "range_end", "description")
    order = "name ASC"
    label = "地址池"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if "range_start" in out or "range_end" in out:
            _, err = pool_addresses(out.get("range_start", ""), out.get("range_end", ""))
            if err:
                raise ApiError(err)
        if out.get("subnet_id") == "":
            out["subnet_id"] = None
        return out

    def decorate(self, conn, items):
        for it in items:
            addrs, _ = pool_addresses(it["range_start"], it["range_end"])
            pool = set(addrs or [])
            used = conn.execute("SELECT ip_address, status FROM ip_records").fetchall()
            it["total"] = len(pool)
            it["allocated"] = sum(1 for u in used if u["ip_address"] in pool and u["status"] != "idle")
            it["available"] = max(it["total"] - it["allocated"], 0)
            row = conn.execute("SELECT cidr FROM subnets WHERE id=?", (it["subnet_id"] or "",)).fetchone()
            it["subnet_cidr"] = row["cidr"] if row else ""
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("DELETE FROM ip_records WHERE remark LIKE ?", ["%地址池 %s%" % row_id])


class ReservedIps(Resource):
    table = "reserved_ips"
    fields = ("ip_address", "subnet_id", "purpose", "remark")
    required = ("ip_address",)
    searchable = ("ip_address", "purpose", "remark")
    order = "ip_address ASC"
    label = "保留地址"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if "ip_address" in out and not valid_ip(out["ip_address"]):
            raise ApiError("IP 地址格式不正确")
        if out.get("subnet_id") == "":
            out["subnet_id"] = None
        return out

    def decorate(self, conn, items):
        for it in items:
            row = conn.execute("SELECT cidr FROM subnets WHERE id=?", (it["subnet_id"] or "",)).fetchone()
            it["subnet_cidr"] = row["cidr"] if row else ""
        return items


class Assets(Resource):
    table = "assets"
    fields = ("asset_number", "asset_name", "asset_type", "brand", "model", "serial_number",
              "status", "location", "owner", "purchase_date", "warranty_expire_at", "remark", "updated_at")
    required = ("asset_number", "asset_name")
    searchable = ("asset_number", "asset_name", "asset_type", "brand", "model", "serial_number", "location", "owner")
    order = "created_at DESC"
    label = "资产"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if out.get("status") and out["status"] not in ASSET_STATUS_LABELS:
            raise ApiError("资产状态不合法")
        return out

    def extra_where(self, req, conn):
        parts, args = [], []
        for key in ("status", "asset_type", "brand"):
            if req.q(key):
                parts.append("%s=?" % key)
                args.append(req.q(key))
        return " AND ".join(parts), args

    def decorate(self, conn, items):
        for it in items:
            it["status_text"] = ASSET_STATUS_LABELS.get(it["status"], it["status"])
            it["binding_count"] = conn.execute("SELECT COUNT(*) c FROM asset_ip_bindings WHERE asset_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("DELETE FROM asset_ip_bindings WHERE asset_id=?", (row_id,))


class Tags(Resource):
    table = "tags"
    fields = ("name", "color")
    required = ("name",)
    searchable = ("name",)
    order = "name ASC"
    label = "标签"

    def decorate(self, conn, items):
        for it in items:
            it["record_count"] = conn.execute("SELECT COUNT(*) c FROM record_tags WHERE tag_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("DELETE FROM record_tags WHERE tag_id=?", (row_id,))


class SavedViews(Resource):
    table = "saved_views"
    fields = ("name", "scope", "filters_json", "owner")
    required = ("name",)
    searchable = ("name",)
    order = "created_at DESC"
    label = "视图"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if out.get("scope") and out["scope"] not in ("personal", "public"):
            raise ApiError("视图范围不合法")
        if out.get("scope") == "public" and req.user.get("role") != "admin":
            raise ApiError("仅管理员可保存公共视图", 403)
        if creating:
            out.setdefault("owner", req.actor())
            out.setdefault("filters_json", "{}")
        return out

    def update(self, req, conn, row_id):
        row = self.get_one(req, conn, row_id)
        if row["scope"] == "public" and req.user.get("role") != "admin":
            raise ApiError("仅管理员可修改公共视图", 403)
        return super().update(req, conn, row_id)

    def delete(self, req, conn, row_id):
        row = self.get_one(req, conn, row_id)
        if row["scope"] == "public" and req.user.get("role") != "admin":
            raise ApiError("仅管理员可删除公共视图", 403)
        if row["scope"] == "personal" and row["owner"] != req.actor() and req.user.get("role") != "admin":
            raise ApiError("无权删除他人个人视图", 403)
        return super().delete(req, conn, row_id)


class LocationNodes(Resource):
    table = "location_nodes"
    fields = ("parent_id", "name", "node_type", "description")
    required = ("name",)
    searchable = ("name", "description")
    order = "name ASC"
    label = "位置节点"

    def parse(self, req, conn, data, creating):
        out = super().parse(req, conn, data, creating)
        if out.get("parent_id") == "":
            out["parent_id"] = None
        if out.get("node_type") and out["node_type"] not in NODE_TYPE_LABELS:
            raise ApiError("位置类型不合法")
        return out

    def decorate(self, conn, items):
        for it in items:
            it["node_type_label"] = NODE_TYPE_LABELS.get(it["node_type"], it["node_type"])
            parent = conn.execute("SELECT name FROM location_nodes WHERE id=?", (it["parent_id"] or "",)).fetchone()
            it["parent_name"] = parent["name"] if parent else ""
            it["child_count"] = conn.execute("SELECT COUNT(*) c FROM location_nodes WHERE parent_id=?", (it["id"],)).fetchone()["c"]
        return items

    def before_delete(self, req, conn, row_id):
        conn.execute("UPDATE location_nodes SET parent_id=NULL WHERE parent_id=?", (row_id,))


class Users(Resource):
    table = "users"
    fields = ("username", "password", "role", "org_id", "is_active", "last_login", "created_at")
    required = ("username",)
    searchable = ("username",)
    order = "created_at DESC"
    label = "用户"

    def parse(self, req, conn, data, creating):
        out = {}
        for f in ("username", "role", "org_id", "is_active"):
            if f in data:
                out[f] = data[f].strip() if isinstance(data[f], str) else data[f]
        if creating and not out.get("username", "").strip():
            raise ApiError("用户名不能为空")
        if "username" in out and not re.fullmatch(r"[A-Za-z0-9_.@\-]{2,32}", out["username"]):
            raise ApiError("用户名仅支持 2-32 位字母、数字、._@-")
        if out.get("role") and out["role"] not in ("admin", "user"):
            raise ApiError("角色不合法")
        if out.get("org_id") == "":
            out["org_id"] = None
        if "password" in data and str(data["password"]).strip():
            if len(str(data["password"])) < 6:
                raise ApiError("密码至少 6 位")
            out["password_hash"] = hash_password(str(data["password"]))
        elif creating:
            raise ApiError("新用户必须设置密码（至少 6 位）")
        return out

    def list(self, req, conn):
        result = super().list(req, conn)
        for it in result["items"]:
            it.pop("password_hash", None)
            org = conn.execute("SELECT name FROM organizations WHERE id=?", (it["org_id"] or "",)).fetchone()
            it["org_name"] = org["name"] if org else ""
        return result

    def get_one(self, req, conn, row_id):
        row = super().get_one(req, conn, row_id)
        row.pop("password_hash", None)
        return row

    def create(self, req, conn):
        out = self.parse(req, conn, req.body, True)
        out.setdefault("role", "user")
        out.setdefault("is_active", 1)
        out["created_at"] = now()
        rid = new_id()
        try:
            conn.execute("INSERT INTO users(id,%s) VALUES(?,%s)" %
                         (",".join(out.keys()), ",".join("?" for _ in out)), [rid] + list(out.values()))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ApiError("用户名已存在")
            raise ApiError("创建失败：%s" % e)
        audit(conn, "新增用户", rid, out.get("username", ""), req.actor())
        return self.get_one(req, conn, rid)

    def update(self, req, conn, row_id):
        self.get_one(req, conn, row_id)
        out = self.parse(req, conn, req.body, False)
        if row_id == req.user["id"] and (out.get("role") == "user" or out.get("is_active") in (0, "0", False)):
            raise ApiError("不能降级或停用当前登录用户")
        if not out:
            raise ApiError("请填写至少一项修改内容")
        conn.execute("UPDATE users SET %s WHERE id=?" % ",".join("%s=?" % k for k in out),
                     list(out.values()) + [row_id])
        audit(conn, "更新用户", row_id, ",".join(out.keys()), req.actor())
        return self.get_one(req, conn, row_id)

    def delete(self, req, conn, row_id):
        row = self.get_one(req, conn, row_id)
        if row_id == req.user["id"]:
            raise ApiError("不能删除当前登录用户")
        if row["role"] == "admin":
            n = conn.execute("SELECT COUNT(*) c FROM users WHERE role='admin' AND is_active=1").fetchone()["c"]
            if n <= 1:
                raise ApiError("不能删除最后一个管理员")
        conn.execute("DELETE FROM sessions WHERE user_id=?", (row_id,))
        conn.execute("DELETE FROM users WHERE id=?", (row_id,))
        audit(conn, "删除用户", row_id, row["username"], req.actor())
        return {"deleted": row_id}


# ---------------- IP 记录 ----------------

RECORD_FIELDS = ("ip_address", "port", "subnet_id", "org_id", "department_id", "status",
                 "device_name", "device_type", "mac_address", "owner", "business_system",
                 "remark", "is_online", "last_scan_time")


def scope_where(req, alias="r"):
    """非管理员且绑定单位时，限制数据可见范围。"""
    user = req.user or {}
    if user.get("role") != "admin" and user.get("org_id"):
        return " AND %s.org_id=?" % alias, [user["org_id"]]
    return "", []


def check_org_scope(req, conn, org_id):
    user = req.user or {}
    if user.get("role") != "admin" and user.get("org_id"):
        if org_id != user["org_id"]:
            raise ApiError("无权访问该单位的数据", 403)


def validate_record(req, conn, data, creating, exclude_id=None):
    out = {}
    for f in RECORD_FIELDS:
        if f in data:
            val = data[f]
            out[f] = val.strip() if isinstance(val, str) else val
    if creating and not out.get("ip_address", "").strip():
        raise ApiError("IP 地址不能为空")
    if "ip_address" in out and not valid_ip(out["ip_address"]):
        raise ApiError("IP 地址格式不正确")
    if "port" in out:
        out["port"] = parse_int(out["port"], 0, 0, 65535)
    if "mac_address" in out:
        mac = norm_mac(out["mac_address"])
        if mac is None:
            raise ApiError("MAC 地址格式不正确（如 AA:BB:CC:DD:EE:FF）")
        out["mac_address"] = mac
    if "status" in out and out["status"] not in STATUS_LABELS:
        raise ApiError("目标状态不合法")
    if creating:
        out.setdefault("status", "in_use")
        out.setdefault("port", 0)
    for key, table, label in (("subnet_id", "subnets", "子网"), ("org_id", "organizations", "单位"),
                              ("department_id", "departments", "部门")):
        if out.get(key) == "":
            out[key] = None
        elif out.get(key) and not conn.execute("SELECT 1 FROM %s WHERE id=?" % table, (out[key],)).fetchone():
            raise ApiError("所选%s不存在" % label)
    if "org_id" in out:
        check_org_scope(req, conn, out.get("org_id"))
    if creating and not out.get("subnet_id"):
        cidrs = rows_to_dicts(conn.execute("SELECT id,cidr FROM subnets").fetchall())
        out["subnet_id"] = infer_subnet(cidrs, out["ip_address"])
    return out


def record_select(conn, where="", args=(), order="r.created_at DESC", limit=None, offset=0):
    sql = ("SELECT r.*, s.cidr AS subnet_cidr, o.name AS org_name, d.name AS dept_name, "
           "(SELECT group_concat(t.name, ', ') FROM record_tags rt JOIN tags t ON t.id=rt.tag_id "
           " WHERE rt.record_id=r.id) AS tag_names "
           "FROM ip_records r "
           "LEFT JOIN subnets s ON s.id=r.subnet_id "
           "LEFT JOIN organizations o ON o.id=r.org_id "
           "LEFT JOIN departments d ON d.id=r.department_id ")
    if where:
        sql += "WHERE " + where + " "
    sql += "ORDER BY %s " % order
    if limit is not None:
        sql += "LIMIT ? OFFSET ?"
        args = list(args) + [limit, offset]
    rows = conn.execute(sql, args).fetchall()
    items = rows_to_dicts(rows)
    for it in items:
        it["status_text"] = STATUS_LABELS.get(it["status"], it["status"])
        it["online_text"] = ("在线" if it["is_online"]
                             else ("离线" if it.get("last_scan_time") else "未探测"))
        it["endpoint_display"] = it["ip_address"] + (":%d" % it["port"] if it["port"] else "")
    return items


def list_records(req, conn):
    page = max(parse_int(req.q("page"), 1, 1), 1)
    per_page = parse_int(req.q("per_page"), 50, 1, 500)
    parts, args = [], []
    q = req.q("q").strip()
    if q:
        parts.append("(r.ip_address LIKE ? OR r.device_name LIKE ? OR r.mac_address LIKE ? "
                     "OR r.owner LIKE ? OR r.business_system LIKE ? OR r.remark LIKE ? "
                     "OR o.name LIKE ? OR s.cidr LIKE ?)")
        args += ["%%%s%%" % q] * 8
    for key, col in (("status", "r.status"), ("org_id", "r.org_id"), ("department_id", "r.department_id"),
                     ("subnet_id", "r.subnet_id"), ("device_type", "r.device_type"), ("is_online", "r.is_online")):
        if req.q(key) != "":
            parts.append("%s=?" % col)
            args.append(req.q(key))
    if req.q("tag_id"):
        parts.append("EXISTS(SELECT 1 FROM record_tags rt WHERE rt.record_id=r.id AND rt.tag_id=?)")
        args.append(req.q("tag_id"))
    sw, sargs = scope_where(req)
    if sw:
        parts.append(sw.lstrip(" AND "))
        args += sargs
    where = " AND ".join(parts)
    total = conn.execute("SELECT COUNT(*) c FROM ip_records r "
                         "LEFT JOIN organizations o ON o.id=r.org_id "
                         "LEFT JOIN subnets s ON s.id=r.subnet_id " +
                         ("WHERE " + where if where else ""), args).fetchone()["c"]
    sort = req.q("sort", "")
    order_map = {"ip_address": "ipkey(r.ip_address)", "device_name": "r.device_name",
                 "status": "r.status", "owner": "r.owner", "created_at": "r.created_at",
                 "updated_at": "r.updated_at", "port": "r.port"}
    direction = "ASC" if req.q("order", "desc").lower() == "asc" else "DESC"
    order = "%s %s" % (order_map[sort], direction) if sort in order_map else "r.created_at DESC"
    items = record_select(conn, where, args, order, per_page, (page - 1) * per_page)
    return {"items": items, "total": total, "page": page, "per_page": per_page,
            "pages": max(1, (total + per_page - 1) // per_page)}


def create_record(req, conn):
    out = validate_record(req, conn, req.body, True)
    rid = new_id()
    out.update({"id": rid, "created_at": now(), "updated_at": now(), "created_by": req.actor()})
    cols = list(out.keys())
    try:
        conn.execute("INSERT INTO ip_records(%s) VALUES(%s)" %
                     (",".join(cols), ",".join("?" for _ in cols)), list(out.values()))
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ApiError("该 IP 记录在所选单位下已存在")
        raise ApiError("创建失败：%s" % e)
    tags = req.body.get("tags") or []
    for tag_id in tags if isinstance(tags, list) else []:
        conn.execute("INSERT OR IGNORE INTO record_tags(record_id,tag_id) VALUES(?,?)", (rid, tag_id))
    audit(conn, "新增记录", out["ip_address"], out.get("device_name", ""), req.actor())
    return record_get(req, conn, rid)


def record_get(req, conn, row_id):
    items = record_select(conn, "r.id=?", (row_id,), limit=1)
    if not items:
        raise ApiError("IP 记录不存在", 404)
    item = items[0]
    check_org_scope(req, conn, item["org_id"])
    item["tags"] = rows_to_dicts(conn.execute(
        "SELECT t.* FROM tags t JOIN record_tags rt ON rt.tag_id=t.id WHERE rt.record_id=?", (row_id,)).fetchall())
    return item


def update_record(req, conn, row_id):
    old = record_get(req, conn, row_id)
    out = validate_record(req, conn, req.body, False, exclude_id=row_id)
    if "org_id" not in out:
        check_org_scope(req, conn, old["org_id"])
    out["updated_at"] = now()
    if not out or list(out.keys()) == ["updated_at"]:
        raise ApiError("请填写至少一项修改内容")
    try:
        conn.execute("UPDATE ip_records SET %s WHERE id=?" % ",".join("%s=?" % k for k in out),
                     list(out.values()) + [row_id])
    except Exception as e:
        if "UNIQUE" in str(e):
            raise ApiError("该 IP 记录在所选单位下已存在")
        raise ApiError("更新失败：%s" % e)
    if "tags" in req.body and isinstance(req.body["tags"], list):
        conn.execute("DELETE FROM record_tags WHERE record_id=?", (row_id,))
        for tag_id in req.body["tags"]:
            conn.execute("INSERT OR IGNORE INTO record_tags(record_id,tag_id) VALUES(?,?)", (row_id, tag_id))
    audit(conn, "更新记录", old["ip_address"], ",".join(k for k in out if k != "updated_at"), req.actor())
    return record_get(req, conn, row_id)


def delete_record(req, conn, row_id):
    item = record_get(req, conn, row_id)
    conn.execute("DELETE FROM record_tags WHERE record_id=?", (row_id,))
    conn.execute("DELETE FROM asset_ip_bindings WHERE record_id=?", (row_id,))
    conn.execute("DELETE FROM ip_records WHERE id=?", (row_id,))
    audit(conn, "删除记录", item["ip_address"], item["device_name"], req.actor())
    return {"deleted": row_id}


# ---------------- 会话与基础 ----------------

def api_ping(req, conn):
    has_user = conn.execute("SELECT COUNT(*) c FROM users").fetchone()["c"] > 0
    return {"app": "IPAM fnOS", "version": APP_VERSION, "initialized": has_user,
            "branding": load_branding(req.cfg)}


def api_login(req, conn):
    token, err = login(req.cfg, req.v("username"), req.v("password"), req.remote, conn=conn)
    if err:
        # 失败计数需落库，先提交再抛错（外层回滚对已提交事务无影响）
        conn.commit()
        raise ApiError(err, 401)
    return {"_cookie": "ipam_session=%s; Path=/; HttpOnly; SameSite=Lax; Max-Age=%d" %
            (token, 7 * 86400), "username": req.v("username")}


def api_logout(req, conn):
    logout(req.cfg, req.cookies.get("ipam_session", ""), conn=conn)
    return {"_cookie": "ipam_session=; Path=/; HttpOnly; Max-Age=0", "logged_out": True}


def api_session(req, conn):
    branding = load_branding(req.cfg)
    return {
        "user": {k: req.user[k] for k in ("id", "username", "role", "org_id", "last_login")},
        "app_name": get_setting(conn, "app_name", "") or branding["app_name"],
        "field_labels": json.loads(get_setting(conn, "field_labels", "{}") or "{}"),
        "system_mode": get_setting(conn, "system_mode", "normal"),
        "branding": branding,
        "version": APP_VERSION,
        "status_labels": STATUS_LABELS,
        "asset_status_labels": ASSET_STATUS_LABELS,
    }


def api_change_password(req, conn):
    old, new = req.v("old_password"), req.v("new_password")
    if len(new) < 6:
        raise ApiError("新密码至少 6 位")
    if not verify_password(old, req.user["password_hash"]):
        raise ApiError("原密码错误")
    conn.execute("UPDATE users SET password_hash=? WHERE id=?", (hash_password(new), req.user["id"]))
    audit(conn, "修改密码", req.user["username"], "", req.actor())
    return {"changed": True}


# ---------------- 注册 ----------------

def build_router(cfg):
    r = Router(cfg)
    # 扩展路由先注册，避免 /api/subnets/tree 之类被 /api/subnets/<id> 抢先匹配
    from api_ops import register_ops
    register_ops(r)
    r.add("GET", "^/api/ping$", api_ping, auth=False)
    r.add("POST", "^/api/login$", api_login, auth=False)
    r.add("POST", "^/api/logout$", api_logout, auth=True)
    r.add("GET", "^/api/session$", api_session, auth=True)
    r.add("POST", "^/api/change_password$", api_change_password, auth=True)

    for prefix, cls, admin in (
        ("organizations", Organizations, True), ("departments", Departments, False),
        ("vlans", Vlans, False), ("subnets", Subnets, False),
        ("address_spaces", AddressSpaces, False), ("ip_pools", IpPools, False),
        ("reserved_ips", ReservedIps, False), ("assets", Assets, False),
        ("tags", Tags, False), ("saved_views", SavedViews, False),
        ("location_nodes", LocationNodes, False), ("users", Users, True),
    ):
        register_crud(r, prefix, cls(), admin_write=admin)

    r.add("GET", "^/api/records$", list_records)
    r.add("POST", "^/api/records$", create_record)
    r.add("GET", "^/api/records/(?P<row_id>[^/]+)$", record_get)
    r.add("PUT", "^/api/records/(?P<row_id>[^/]+)$", update_record)
    r.add("DELETE", "^/api/records/(?P<row_id>[^/]+)$", delete_record)
    return r
