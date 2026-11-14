# REST API 参考

> 配套文档：[README.md](../README.md) · [DEVELOPMENT.md](DEVELOPMENT.md)
> 适用性：IPAM 1.0.4

---

## 1. 基本约定

| 项目 | 说明 |
| --- | --- |
| Base URL | `http://<NAS_IP>:8765/api`（端口服务模式） |
| 网关模式 | 走统一网关时路径前加 `gatewayPrefix`，例如 `/app/IPAM/api/...` |
| 请求体 | `application/json`；最大 24 MB |
| 字符编码 | UTF-8 |
| 成功响应 | `{"ok": true, "data": <对象/数组/标量>}` |
| 失败响应 | `{"ok": false, "error": "错误说明"}` |
| 静态资源 | `/`、`/app.js`、`/style.css`、`/images/*`，仅放行扩展名白名单 |

常见状态码：

| 状态码 | 含义 |
| --- | --- |
| `200` | 成功 |
| `400` | 参数校验失败、业务规则不满足 |
| `401` | 未登录或会话失效 |
| `403` | 权限不足（需要管理员） |
| `404` | 接口或资源不存在 |

---

## 2. 认证

- `GET /api/ping`、`POST /api/login` **无需认证**，其余接口均需登录态。
- 登录成功后服务端下发 Cookie：

  ```
  Set-Cookie: ipam_session=<token>; Path=/; HttpOnly; SameSite=Lax; Max-Age=604800
  ```

- 后续请求携带该 Cookie 即可；`POST /api/logout` 会清除会话。
- 密码以 PBKDF2 哈希存储；连续 5 次失败锁定 15 分钟。
- 角色：`admin` 可管理用户与单位并执行管理员写操作；普通用户受单位数据隔离限制（`scope_where` 在 SQL 层下推）。

```bash
# 登录（-i 用于查看 Set-Cookie）
curl -i -X POST http://127.0.0.1:8765/api/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"YourPass123"}'

# 之后带 Cookie 调用
curl -s -H 'Cookie: ipam_session=<token>' http://127.0.0.1:8765/api/session
```

---

## 3. 通用约定

### 分页与列表

`GET /api/records` 等列表接口支持：

| 参数 | 默认 | 说明 |
| --- | --- | --- |
| `page` | `1` | 页码，从 1 起 |
| `per_page` | `50` | 每页条数，最大 500 |
| `q` | 空 | 关键字模糊搜索 |
| `sort` / `order` | `created_at` / `desc` | 排序字段与方向（`asc` / `desc`） |
| `status`、`org_id`、`department_id`、`subnet_id`、`device_type`、`is_online`、`tag_id` | 空 | 精确筛选 |

IP 列表返回结构：

```json
{
  "ok": true,
  "data": {
    "items": [ { "id": "...", "ip_address": "192.168.0.10", "status": "in_use" } ],
    "total": 128, "page": 1, "per_page": 50, "pages": 3
  }
}
```

`ip_address` 排序使用 SQLite 自定义函数 `ipkey()`，是**数值序**而非字典序。状态枚举以 `GET /api/session` 返回的 `status_labels` / `asset_status_labels` 为准。

### 统一响应包装

业务函数返回的 dict 会被 `router.py` 自动包成 `{"ok": true, "data": ...}`；CSV 导出、备份下载等接口返回 `RawResponse`，直接是文件流而非 JSON。

---

## 4. 接口清单

### 4.1 公开 / 会话 / 系统

| 方法 | 路径 | 认证 | 说明 |
| --- | --- | --- | --- |
| GET | `/api/ping` | 否 | 存活探测；返回版本、是否已初始化、`branding` |
| POST | `/api/login` | 否 | 登录，下发会话 Cookie |
| POST | `/api/logout` | 是 | 退出并清除会话 |
| GET | `/api/session` | 是 | 当前用户、应用名、字段标签、系统模式、品牌信息、状态枚举、版本 |
| POST | `/api/change_password` | 是 | 修改当前登录密码 |
| GET | `/api/settings/info` | 是 | 系统名称、模式、字段标签、品牌信息 |
| POST | `/api/settings/app_name` | 是（管理员） | 修改系统名称 |
| POST | `/api/settings/system_mode` | 是（管理员） | 修改界面模式（normal / compact / readonly） |
| POST | `/api/settings/field_labels` | 是（管理员） | 自定义字段显示名 |
| POST | `/api/settings/branding` | 是（管理员） | 修改作者 / 邮箱 / QQ群 / 版权 / 开源地址 / 免责声明 |
| GET | `/api/system/stats` | 是 | 版本、Python 版本、各表记录数、数据库大小 |
| GET | `/api/filter_options` | 是 | 筛选下拉选项（单位、部门、子网、标签等） |
| GET | `/api/audit_logs` | 是 | 审计日志列表 |
| POST | `/api/audit_logs/clear` | 是（管理员） | 清空审计日志 |

### 4.2 IP 记录

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/records` | 列表：搜索 / 筛选 / 排序 / 分页 |
| POST | `/api/records` | 新增记录 |
| GET | `/api/records/{row_id}` | 单条详情（含标签、绑定资产） |
| PUT | `/api/records/{row_id}` | 修改记录 |
| DELETE | `/api/records/{row_id}` | 删除记录 |
| POST | `/api/records/probe` | 在线探测（ICMP + 多端口 TCP 双策略，见 4.2.1） |
| POST | `/api/records/batch_{action}` | 批量操作，见下表 |

`batch_{action}` 可选值：`delete`（删除）、`recycle`（回收）、`reserve`（预留）、`status`（改状态）、`organization`（改单位）、`owner`（改负责人）、`bind_tags`（绑定标签）。

记录主要字段：`ip_address`、`port`、`subnet_id`、`org_id`、`department_id`、`status`、`device_name`、`device_type`、`mac_address`、`owner`、`business_system`、`remark`、`is_online`、`last_scan_time`、`tags[]`。同一单位下 `(ip_address, port, org_id)` 唯一。

#### 4.2.1 在线探测（1.0.4 重写）

`POST /api/records/probe`（仅管理员），请求体 `{"ids": ["<id1>", "<id2>"]}`，单次最多 100 条。

判定策略：

1. 先用系统 `ping` 发一个 ICMP Echo（Linux `-n -c 1 -W 1`，Windows `-n 1 -w 1000`），有响应即判在线。
2. 无响应时并发探测 15 个常见端口（记录自身 `port` 优先，其后 `80/443/22/3389/445/8080/8443/139/135/21/23/53/5900/9100/8291`），任一连通判在线。
3. 每次探测先检查控制组 `192.0.2.1`（RFC 5737 保留地址）：若它也能建立 TCP 连接，说明网络存在透明代理 / 通配路由，本次自动禁用 TCP 兜底，只以 ICMP 为准，避免把不存在的 IP 全部判成在线。

> 1.0.3 及以前只连记录端口（默认 80）一次；PC / 终端不监听 80 且系统防火墙拒绝入站时，能 ping 通的在线设备会被误判为离线。1.0.4 已修复。

响应字段：`probed` / `online` / `offline` / `detail`（含判定方式统计）/ `scan_time` / `icmp_available` / `tcp_trustworthy` / `tcp_ports` / `items[]`（逐条 `id`、`ip_address`、`is_online`、`via`）。结果写入 `ip_records.is_online` 与 `last_scan_time`；列表 `online_text` 三态为 `未探测` / `在线` / `离线`。

环境要求：系统需有 `ping` 命令且网络放行 ICMP；无 `ping` 时退化为纯 TCP 端口探测（`icmp_available=false`）。跨网段需保证 NAS 到目标路由可达。

```bash
curl -s -X POST -H "$CK" -H 'Content-Type: application/json' \
  -d '{"ids":["<id1>","<id2>"]}' \
  $BASE/records/probe
```

### 4.3 子网 / VLAN / 地址池

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/subnets/tree` | 子网树形结构 |
| GET | `/api/subnet/overview` | 子网概览与利用率 |
| GET | `/api/ipam/conflicts` | 冲突检测（IP 重复、子网重叠、孤儿记录等） |
| POST | `/api/ip_pools/{pool_id}/allocate` | 从地址池分配一个 IP |
| POST | `/api/ip_pools/{pool_id}/release` | 释放地址池中的 IP |

子网、VLAN、地址空间、地址池、保留地址的增删改查走 §5 的通用 CRUD 路由。

### 4.4 资产与绑定

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/assets/{asset_id}/bindings` | 资产已绑定的 IP 记录 |
| GET | `/api/assets/{asset_id}/bindings/options` | 可绑定的记录选项 |
| POST | `/api/assets/{asset_id}/bindings` | 新建绑定 |
| DELETE | `/api/assets/{asset_id}/bindings/{binding_id}` | 解绑 |
| GET | `/api/assets/export_csv` | 资产 CSV 导出（文件流） |
| POST | `/api/assets/import_csv` | 资产 CSV 导入 |

资产本体走通用 CRUD：`/api/assets`。

### 4.5 统计

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/dashboard/stats` | 仪表盘汇总数字 |
| GET | `/api/statistics/utilization` | 子网 / 地址利用率 |
| GET | `/api/statistics/conflicts` | 冲突统计 |
| GET | `/api/statistics/asset_distribution` | 资产分布 |

### 4.6 导入导出 / 备份

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/export_csv` | 导出 IP 记录 CSV（文件流） |
| POST | `/api/import_csv/preview` | 导入前预览与逐行校验 |
| POST | `/api/import_csv/fix` | 修复问题行后导入 |
| POST | `/api/import_csv` | 直接导入 CSV |
| GET | `/api/backups` | 备份文件列表 |
| POST | `/api/backup` | 创建数据库备份（使用 `sqlite3.Connection.backup()`） |
| GET | `/api/backup/{name}` | 下载备份（文件流） |
| POST | `/api/backup/{name}/restore` | 从备份恢复 |
| DELETE | `/api/backup/{name}` | 删除备份 |

导入流程建议：`preview` → 展示校验结果 → 用户修正 → `fix` 或 `import_csv`。备份文件名由服务端生成并对路径做安全校验，不接受 `..` 穿越。

---

## 5. 通用 CRUD 资源

以下 12 类资源由 `register_crud()` 自动生成统一路由，形状一致：

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/{prefix}` | 列表（支持 `q` 搜索与分页） |
| POST | `/api/{prefix}` | 新增 |
| GET | `/api/{prefix}/{row_id}` | 详情 |
| PUT | `/api/{prefix}/{row_id}` | 修改 |
| DELETE | `/api/{prefix}/{row_id}` | 删除 |

| prefix | 资源 | 写操作权限 |
| --- | --- | --- |
| `organizations` | 单位 | **仅管理员** |
| `departments` | 部门 | 登录用户 |
| `vlans` | VLAN | 登录用户 |
| `subnets` | 子网（CIDR 校验 + 重叠检测） | 登录用户 |
| `address_spaces` | 地址空间 | 登录用户 |
| `ip_pools` | IP 地址池 | 登录用户 |
| `reserved_ips` | 保留地址 | 登录用户 |
| `assets` | 资产台账 | 登录用户 |
| `tags` | 标签 | 登录用户 |
| `saved_views` | 保存的筛选视图 | 登录用户 |
| `location_nodes` | 物理位置节点（树形） | 登录用户 |
| `users` | 用户与角色 | **仅管理员** |

> 自定义路径（如 `/api/subnets/tree`）注册在通用 CRUD **之前**，否则会被 `/{row_id}` 抢先匹配。新增接口时注意这个顺序。

---

## 6. 调用示例

```bash
BASE=http://127.0.0.1:8765/api
TOKEN='<从登录响应的 Set-Cookie 中取 ipam_session 的值>'
CK="Cookie: ipam_session=$TOKEN"

# 存活探测（无需登录）
curl -s $BASE/ping

# 搜索 192.168 网段、按 IP 数值序升序、取第 1 页
curl -s -H "$CK" "$BASE/records?q=192.168&sort=ip_address&order=asc&page=1&per_page=50"

# 新增一条 IP 记录
curl -s -X POST -H "$CK" -H 'Content-Type: application/json' \
  -d '{"ip_address":"192.168.1.20","device_name":"NAS-01","owner":"张三","status":"in_use"}' \
  $BASE/records

# 批量把选中记录改为预留（ids + value，状态值见 session 的 status_labels）
curl -s -X POST -H "$CK" -H 'Content-Type: application/json' \
  -d '{"ids":["<id1>","<id2>"],"value":"reserved"}' \
  $BASE/records/batch_status

# 创建备份并列出
curl -s -X POST -H "$CK" $BASE/backup
curl -s -H "$CK" $BASE/backups

# 导出 CSV（直接落盘）
curl -s -H "$CK" $BASE/export_csv -o records.csv
```

---

## 7. 安全说明

- 所有 SQL 使用参数绑定，动态排序列来自白名单映射。
- 用户提交内容在前端展示前统一转义；外链只允许 `http/https`。
- 静态资源路径经 `unquote` + 目录穿越校验，只放行 `MIME` 白名单扩展名。
- 响应头带 `X-Content-Type-Options: nosniff`、`Referrer-Policy: no-referrer`；JSON 响应 `Cache-Control: no-store`。
- 仅在 Unix Socket 统一网关模式下信任 `X-Trim-*` 请求头；TCP 模式下客户端自报身份一律不可信。

---

## 8. 联系方式

- 邮箱：**12031050@qq.com**
- QQ群：**416290431**
- 开源地址：**https://github.com/12031050/ipam.git**
