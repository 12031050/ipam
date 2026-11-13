# -*- coding: utf-8 -*-
"""极简 HTTP API 路由层（无第三方依赖）。"""

import json
import re
import urllib.parse

from core import audit, db, json_default, new_id, now, parse_int, rows_to_dicts


class ApiError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.message = message
        self.status = status


class RawResponse:
    def __init__(self, data, content_type="application/octet-stream", headers=None):
        self.data = data
        self.content_type = content_type
        self.headers = headers or {}


class Request:
    def __init__(self, cfg, method, path, query, headers, cookies, raw_body, remote, base=""):
        self.cfg = cfg
        self.method = method
        self.path = path
        self.query = query
        self.headers = headers
        self.cookies = cookies
        self.raw_body = raw_body
        self.remote = remote
        self.base = base
        self.user = None
        self._body = None

    @property
    def body(self):
        if self._body is None:
            ctype = self.headers.get("content-type", "")
            if "application/json" in ctype or (self.raw_body[:1] in (b"{", b"[")):
                try:
                    self._body = json.loads(self.raw_body.decode("utf-8")) if self.raw_body else {}
                except (ValueError, UnicodeDecodeError):
                    raise ApiError("请求体 JSON 格式不正确")
            elif "application/x-www-form-urlencoded" in ctype:
                self._body = {k: v[-1] for k, v in urllib.parse.parse_qs(
                    self.raw_body.decode("utf-8", "replace"), keep_blank_values=True).items()}
            else:
                self._body = {}
            if not isinstance(self._body, dict):
                self._body = {}
        return self._body

    def q(self, key, default=""):
        return self.query.get(key, default)

    def v(self, key, default=""):
        val = self.body.get(key, default)
        return val.strip() if isinstance(val, str) else val

    def actor(self):
        return self.user["username"] if self.user else ""


class Router:
    def __init__(self, cfg):
        self.cfg = cfg
        self.routes = []

    def add(self, method, pattern, func, auth=True, admin=False):
        self.routes.append((method.upper(), re.compile(pattern), func, auth, admin))

    def route(self, method, pattern, **kwargs):
        def deco(func):
            self.add(method, pattern, func, **kwargs)
            return func
        return deco

    def match(self, method, path):
        for m, regex, func, auth, admin in self.routes:
            if m != method:
                continue
            hit = regex.match(path)
            if hit:
                return func, auth, admin, hit.groupdict()
        return None

    def handle(self, req):
        try:
            path = req.path
            if req.base and path.startswith(req.base):
                path = path[len(req.base):] or "/"
            req.path = path
            found = self.match(req.method, path)
            if not found:
                raise ApiError("接口不存在: %s %s" % (req.method, path), 404)
            func, need_auth, need_admin, params = found
            if need_auth and not req.user:
                raise ApiError("请先登录", 401)
            if need_admin and (not req.user or req.user.get("role") != "admin"):
                raise ApiError("权限不足，仅管理员可执行此操作", 403)
            write = req.method in ("POST", "PUT", "DELETE", "PATCH")
            with db(self.cfg, write=write) as conn:
                result = func(req, conn, **params)
            if isinstance(result, RawResponse):
                return 200, result.headers, result
            headers = {}
            if isinstance(result, dict) and "_cookie" in result:
                headers["Set-Cookie"] = result.pop("_cookie")
            return 200, headers, {"ok": True, "data": result}
        except ApiError as e:
            return e.status, {}, {"ok": False, "error": e.message}
        except Exception as e:
            import traceback
            traceback.print_exc()
            return 500, {}, {"ok": False, "error": "服务器内部错误：%s" % e}


def json_bytes(obj):
    return json.dumps(obj, ensure_ascii=False, default=json_default).encode("utf-8")


class Resource:
    """通用 CRUD 资源：list / create / update / delete。"""

    table = ""
    fields = ()
    required = ()
    order = "created_at DESC"
    searchable = ()
    label = "记录"

    def parse(self, req, conn, data, creating):
        out = {}
        for f in self.fields:
            if f in data:
                val = data.get(f)
                out[f] = val.strip() if isinstance(val, str) else val
        for f in self.required:
            if creating and not str(out.get(f, "")).strip():
                raise ApiError("%s 不能为空" % f)
        return out

    def before_save(self, req, conn, out, creating, row_id=None):
        return out

    def before_delete(self, req, conn, row_id):
        pass

    def decorate(self, conn, items):
        return items

    def extra_where(self, req, conn):
        return "", []

    def list(self, req, conn):
        page = max(parse_int(req.q("page"), 1, 1), 1)
        per_page = parse_int(req.q("per_page"), 50, 1, 500)
        where, args = self.extra_where(req, conn)
        q = req.q("q").strip()
        if q and self.searchable:
            clause = " AND ".join("COALESCE(%s,'') LIKE ?" % f for f in self.searchable)
            where += (" AND " if where else "") + "(" + clause + ")"
            args += ["%%%s%%" % q] * len(self.searchable)
        sql_where = ("WHERE " + where) if where else ""
        total = conn.execute("SELECT COUNT(*) c FROM %s %s" % (self.table, sql_where), args).fetchone()["c"]
        sort = req.q("sort", "")
        if sort not in self.fields and sort not in ("id", "created_at", "updated_at", "name", "cidr", "ip_address"):
            sort = ""
        direction = "ASC" if req.q("order", "desc").lower() == "asc" else "DESC"
        order = ("%s %s" % (sort, direction)) if sort else self.order
        rows = conn.execute("SELECT * FROM %s %s ORDER BY %s LIMIT ? OFFSET ?" %
                            (self.table, sql_where, order), args + [per_page, (page - 1) * per_page]).fetchall()
        items = self.decorate(conn, rows_to_dicts(rows))
        return {"items": items, "total": total, "page": page, "per_page": per_page,
                "pages": max(1, (total + per_page - 1) // per_page)}

    def get_one(self, req, conn, row_id):
        row = conn.execute("SELECT * FROM %s WHERE id=?" % self.table, (row_id,)).fetchone()
        if not row:
            raise ApiError("%s 不存在" % self.label, 404)
        return dict(row)

    def create(self, req, conn):
        out = self.parse(req, conn, req.body, True)
        out = self.before_save(req, conn, out, True)
        rid = out.pop("id", None) or new_id()
        out.setdefault("created_at", now())
        if "updated_at" in self.fields:
            out.setdefault("updated_at", now())
        cols = ["id"] + list(out.keys())
        marks = ",".join("?" for _ in cols)
        try:
            conn.execute("INSERT INTO %s(%s) VALUES(%s)" %
                         (self.table, ",".join(cols), marks), [rid] + list(out.values()))
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ApiError("%s 已存在" % self.label)
            raise ApiError("创建失败：%s" % e)
        from core import audit
        audit(conn, "新增" + self.label, rid, str(req.v("name") or req.v("ip_address") or ""), req.actor())
        return self.get_one(req, conn, rid)

    def update(self, req, conn, row_id):
        self.get_one(req, conn, row_id)
        out = self.parse(req, conn, req.body, False)
        out = self.before_save(req, conn, out, False, row_id)
        if "updated_at" in self.fields:
            out["updated_at"] = now()
        if not out:
            raise ApiError("请填写至少一项修改内容")
        try:
            conn.execute("UPDATE %s SET %s WHERE id=?" %
                         (self.table, ",".join("%s=?" % k for k in out)),
                         list(out.values()) + [row_id])
        except Exception as e:
            if "UNIQUE" in str(e):
                raise ApiError("%s 已存在" % self.label)
            raise ApiError("更新失败：%s" % e)
        from core import audit
        audit(conn, "更新" + self.label, row_id, ",".join(out.keys()), req.actor())
        return self.get_one(req, conn, row_id)

    def delete(self, req, conn, row_id):
        self.get_one(req, conn, row_id)
        self.before_delete(req, conn, row_id)
        conn.execute("DELETE FROM %s WHERE id=?" % self.table, (row_id,))
        from core import audit
        audit(conn, "删除" + self.label, row_id, "", req.actor())
        return {"deleted": row_id}


def register_crud(router, prefix, resource, auth=True, admin_write=False):
    base = "^/api/%s$" % prefix
    one = "^/api/%s/(?P<row_id>[^/]+)$" % prefix
    router.add("GET", base, lambda req, conn: resource.list(req, conn), auth=auth)
    router.add("POST", base, lambda req, conn: resource.create(req, conn), auth=auth, admin=admin_write)
    router.add("GET", one, lambda req, conn, row_id: resource.get_one(req, conn, row_id), auth=auth)
    router.add("PUT", one, lambda req, conn, row_id: resource.update(req, conn, row_id), auth=auth, admin=admin_write)
    router.add("DELETE", one, lambda req, conn, row_id: resource.delete(req, conn, row_id), auth=auth, admin=admin_write)
