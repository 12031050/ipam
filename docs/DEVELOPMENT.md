# 开发文档

面向想本地运行、修改或二次开发 IPAM 的开发者。阅读本文前建议先看一下根目录 [README.md](../README.md)。

---

## 1. 环境要求

| 用途 | 要求 | 是否必需 |
| --- | --- | --- |
| 后端运行 | Python **3.8+**（仅标准库） | 必需 |
| 构建打包 | Python 3.8+，`build_fpk.py` 无第三方依赖 | 打包时必需 |
| JS 语法检查 | Node.js（只用 `node --check`） | 可选 |
| Shell 语法检查 | Bash（`bash -n`） | 可选 |
| 重建 Logo | ImageMagick `magick` 命令 | 仅换图时 |
| 实机验证 | 飞牛 fnOS ≥ 1.2.0，系统 Python 运行时 | 上架/实机时 |
| 图标规范核对 | 64×64 / 256×256 PNG，正方形圆角，单文件 ≤ 1024 KB | 上架时 |

**不需要**：pip 安装任何包、npm、数据库服务、Docker、Web 服务器。

---

## 2. 本地运行（无需飞牛设备）

`Config` 通过环境变量定位目录，因此只要把三个目录指对，普通 Windows / Linux / macOS 上就能直接跑。

```bash
# 在仓库根目录执行（Git Bash / Linux / macOS）
mkdir -p .local/var .local/etc
cp branding.json .local/etc/branding.json

# ① 初始化数据库与管理员（密码从 stdin 读入，不落配置文件）
printf 'YourPass123' | \
  IPAM_APPDEST="$PWD/app" \
  TRIM_PKGVAR="$PWD/.local/var" \
  TRIM_PKGETC="$PWD/.local/etc" \
  python app/server/server.py --init --admin admin --password-stdin

# ② 启动服务
IPAM_APPDEST="$PWD/app" \
TRIM_PKGVAR="$PWD/.local/var" \
TRIM_PKGETC="$PWD/.local/etc" \
IPAM_PORT=8765 IPAM_HOST=127.0.0.1 \
  python app/server/server.py --port 8765 --host 127.0.0.1

# ③ 浏览器打开 http://127.0.0.1:8765 ，用 admin / YourPass123 登录
```

数据库会生成在 `.local/var/ipam.db`（WAL 模式），备份在 `.local/var/backups/`，导出在 `.local/var/exports/`。`.local/` 已被 `.gitignore` 排除。

### 环境变量

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `IPAM_APPDEST` / `TRIM_APPDEST` | `server/` 的上级目录 | 应用目标目录，其下需有 `ui/` 与 `server/` |
| `TRIM_PKGVAR` / `IPAM_DATA_DIR` | 上级目录 `/data` | 数据库、备份、导出、日志、PID |
| `TRIM_PKGETC` / `IPAM_ETC_DIR` | 上级目录 `/etc` | `branding.json` 所在目录 |
| `IPAM_PORT` / `TRIM_SERVICE_PORT` | `8765` | 监听端口 |
| `IPAM_HOST` | `0.0.0.0` | 监听地址，本地开发建议 `127.0.0.1` |
| `IPAM_SOCKET_PATH` | 空 | 统一网关 Unix Socket 路径；设置后走 Socket 而非 TCP |
| `IPAM_BASE` | 空 | 网关路径前缀（如 `/app/IPAM`） |
| `IPAM_TRUST_GATEWAY` | `1` | 仅 Socket 模式下是否信任 `X-Trim-*` 请求头 |
| `TRIM_TEMP_LOGFILE` | `/dev/null` | 生命周期脚本把用户可见错误写入此文件 |

> ⚠️ 安全边界：只有在 `IPAM_SOCKET_PATH` 存在（统一网关模式）时才会读取 `X-Trim-Userid` / `X-Trim-Isadmin` / `X-Trim-Username`。TCP 端口服务模式下这些请求头一律视为不可信输入，登录态完全由应用自己的会话 Cookie 判定。

### 命令行参数（`app/server/server.py`）

| 参数 | 说明 |
| --- | --- |
| `--init` | 初始化数据库与管理员后退出 |
| `--admin NAME` | 初始管理员用户名，默认 `admin` |
| `--password-stdin` | 从标准输入读取初始密码（避免密码出现在命令行或配置里） |
| `--port N` | 监听端口 |
| `--host ADDR` | 监听地址 |
| `--socket PATH` | 使用 Unix Socket（统一网关模式） |

---

## 3. 代码分层

```
浏览器（app/ui，原生 JS 单页）
    │  HTTP  /api/*  JSON；静态资源由 server.py 直出
    ▼
server.py      HTTP/1.1 服务、静态资源白名单、Cookie 解析、Socket 适配
    ▼
router.py      正则路由表、鉴权、事务边界、统一响应、通用 CRUD 资源基类
    ▼
api.py         核心业务接口（IP 记录、子网、统计、设置、会话）
api_ops.py     批量操作、导入导出、备份恢复、冲突检测、审计
    ▼
core.py        Config、SCHEMA、认证与会话、SQL 工具函数、品牌配置
    ▼
SQLite（19 张表，WAL）
```

| 文件 | 职责 |
| --- | --- |
| `app/server/server.py` | HTTP 服务入口、静态资源（扩展名白名单 + 目录穿越防护）、`--init` 初始化、TCP / Unix Socket 双模式 |
| `app/server/router.py` | `Router.add()` 正则路由、`Request` 封装、`ApiError`、统一 `{"ok":...}` 响应、`Resource` + `register_crud()` 通用 CRUD |
| `app/server/core.py` | `Config`、`SCHEMA`、`init_db`、PBKDF2 密码、会话与登录锁定、IP/MAC 校验、审计、品牌读写 |
| `app/server/api.py` | 资源类定义、`list_records` 等核心业务、`build_router()` 统一注册 |
| `app/server/api_ops.py` | 7 类批量操作、CSV 导入预览/修复、导出、备份/恢复/下载、冲突与利用率统计 |
| `app/ui/index.html` | 单页骨架：登录层、侧栏、主视图容器 |
| `app/ui/app.js` | 前端全部逻辑：`api()` 请求封装、hash 路由、各视图渲染、表单与弹窗 |
| `app/ui/style.css` | 深浅主题 CSS 变量、布局、组件样式；无预处理器、无构建步骤 |
| `build_fpk.py` | 纯 Python 打包器：生成图标兜底、品牌同步、app.tgz、外层 fpk、结构校验 |

**事务约定**：`router.py` 按请求方法自动决定是否使用写事务（`BEGIN IMMEDIATE`），业务函数只管执行 SQL；失败统一回滚并转成 `ApiError`。因此业务代码里不要再自行开启第二个写连接（会与 SQLite 写锁互等造成死锁）。

---

## 4. fnOS 应用框架

### 4.1 `manifest` 关键字段

| 字段 | 当前值 | 说明 |
| --- | --- | --- |
| `appname` | `IPAM` | 安装目录名 `/var/apps/IPAM`，不可随意改 |
| `version` | `1.0.4` | 与 `APP_VERSION` 同步 |
| `display_name` | `IP 地址管理` | 桌面显示名 |
| `source` | `thirdparty` | 第三方应用 |
| `platform` / `os_min_version` | `all` / `1.2.0` | 平台与最低系统版本 |
| `service_port` / `checkport` | `8765` / `true` | 端口服务与安装前端口检查 |
| `maintainer` / `maintainer_url` | `梦非常浪漫` / GitHub 仓库地址 | 由 `branding.json` 自动同步 |
| `desktop_uidir` / `desktop_applaunchname` | `ui` / `IPAM.Application` | 桌面入口目录与配置项 |
| `ctl_stop` / `disable_authorization_path` | `true` | 允许停止、关闭默认鉴权路径 |
| `changelog` | … | 单行版本变更说明 |

### 4.2 生命周期脚本 `cmd/`

| 脚本 | 触发时机 | 主要动作 |
| --- | --- | --- |
| `cmd/main start\|stop\|status` | 服务启停 | 找 Python、拉起 `server.py`、PID 文件、端口就绪探测 |
| `install_init` | 安装前 | 检查 Python 版本与文件完整性 |
| `install_callback` | 安装后 | 写 `etc/branding.json`、初始化数据库与管理员 |
| `upgrade_init` / `upgrade_callback` | 升级前 / 后 | 兼容性检查、配置迁移与重启 |
| `uninstall_init` / `uninstall_callback` | 卸载前 / 后 | 停服务、清理运行态（业务数据保留在 var） |
| `config_init` / `config_callback` | 配置变更 | 刷新品牌信息并重启服务 |

约定：

- `main status` 退出码：**0 运行中 / 3 未运行 / 1 失败**，脚本需幂等。
- 用户可见错误必须写入 `$TRIM_TEMP_LOGFILE`。
- 所有路径只用 `TRIM_APPDEST` / `TRIM_PKGVAR` / `TRIM_PKGETC` 等环境变量，**禁止硬编码 `/var/apps/...`**。
- 全部脚本为 **LF 换行 + 0755 权限**，由 `build_fpk.py` 在打包时统一校正。

### 4.3 安装向导 `wizard/`

向导字段值会成为同名环境变量（自定义字段统一加 `wizard_` 前缀）：

| 字段 | 用途 |
| --- | --- |
| `wizard_admin` / `wizard_password` | 初始管理员账号与密码 |
| `wizard_app_name` / `wizard_owner_name` | 应用显示名、作者 |
| `wizard_contact_email` / `wizard_contact` | 联系邮箱、QQ群 |
| `wizard_source_url` | 开源地址 |

`install_callback` / `config_callback` 的合并规则：**包内 `branding.json` 默认值 → 用户已保存配置 → 向导非空值优先**；留空不会把内置作者信息覆盖成空白。

### 4.4 权限与入口

- `config/privilege`：`run-as=package`，独立低权限账号 `ipam_user`，不申请 root。
- `config/resource`：当前为空 `{}`（不需要共享目录、Docker 项目或系统 API Scope）。
- `app/ui/config`：`IPAM.Application` 以 `iframe` + `http` + 端口 `8765` 打开 `/`，`allUsers=true`；图标 `images/icon_{0}.png` 对应 64 / 256 两套。

### 4.5 图标

| 文件 | 尺寸 | 用途 |
| --- | --- | --- |
| `ICON.PNG` / `app/ui/images/icon_64.png` | 64×64 | 应用中心小图标、侧栏、登录页 |
| `ICON_256.PNG` / `app/ui/images/icon_256.png` | 256×256 | 应用中心大图标 |
| `assets/ipamlogo.png` | 原图 | 透明底 RGBA 源图 |

换 Logo：替换 `assets/ipamlogo.png` 后执行 `python build_fpk.py --refresh-icons`（需 `magick`），会重建四张 PNG 并顺带打包。

---

## 5. 数据库模型

SQLite 单文件 `ipam.db`，WAL 模式，19 张表：

| 分类 | 表 | 说明 |
| --- | --- | --- |
| 账号与权限 | `users`、`sessions`、`login_attempts` | 用户/角色、会话令牌、登录失败锁定 |
| 组织架构 | `organizations`、`departments`、`location_nodes` | 单位、部门、物理位置（树形） |
| 网络规划 | `subnets`、`vlans`、`address_spaces`、`ip_pools`、`reserved_ips` | 子网/VLAN/地址空间/地址池/保留地址 |
| 台账 | `ip_records`、`assets`、`asset_ip_bindings` | IP 记录、资产、资产↔IP 绑定 |
| 分类与视图 | `tags`、`record_tags`、`saved_views` | 标签、记录标签关联、保存的筛选视图 |
| 系统 | `settings`、`audit_logs` | 键值配置、审计日志 |

要点：

- 主键统一为随机文本 ID（`new_id()`），时间用 `YYYY-MM-DD HH:MM:SS` 字符串。
- IP 排序通过 SQLite 自定义函数 `ipkey()` 实现**数值序**，避免 `192.168.0.10` 排在 `192.168.0.2` 前面的字典序问题。
- 所有 SQL 使用**参数绑定**；动态列名来自白名单映射，不拼接用户输入。
- 单位数据隔离由 `scope_where(req)` 在 SQL 层下推过滤，不是查出全表后在应用层筛。
- 备份 / 恢复使用 `sqlite3.Connection.backup()`，避免直接拷贝文件导致 WAL 不一致。

---

## 6. 前端

- **无构建步骤**：`index.html` 直接引 `style.css` 与 `app.js`，改完刷新即可。
- 全局状态 `S`，视图通过 `location.hash` 切换（如 `#records`、`#subnets`），`SPECIAL` 映射特殊视图渲染函数。
- 请求统一走 `api(method, path, body)`，自动带 `credentials: same-origin`，解析 `{"ok":true,"data":...}`。
- 所有用户输入展示经 `esc()` / `attr()` 转义；外部链接经 `isHttpUrl()` 白名单（仅 `http/https`）+ `linkHtml()` 转义后渲染，防止 `javascript:` 与 HTML 注入。
- 主题用 `html[data-theme=dark|light]` + CSS 变量，偏好存 `localStorage`。

---

## 7. 构建与打包

```bash
python build_fpk.py                       # 按 manifest.version 生成 IPAM-<version>.fpk
python build_fpk.py --version 1.0.4       # 指定版本并同步写回 manifest
python build_fpk.py --check               # 仅校验已有 fpk 结构
python build_fpk.py --file xxx.fpk --check
python build_fpk.py --refresh-icons       # 重建四张图标（需 magick）
```

`.fpk` 本质是固定结构的 tar.gz：

```
IPAM-<version>.fpk
├── manifest, LICENSE, branding.json
├── ICON.PNG, ICON_256.PNG
├── app.tgz                ← app/ 目录内容（server/、ui/、branding.json）
├── cmd/                    生命周期脚本（LF + 0755）
├── config/                 privilege、resource
├── wizard/                 install、config、uninstall
└── ui/config               桌面入口
```

打包器会：规范化脚本换行与权限、把 `branding.json` 的署名同步到 `manifest`、把品牌文件同时打进外层与 `app.tgz`、校验图标尺寸与体积，最后逐项检查包结构。1.0.4 包为外层 31 项、`app.tgz` 内 15 项。

> 若飞牛应用中心对包校验报错，可在设备上了用官方工具重打包：`fnpack build -d <源码目录>`。

---

## 8. 验证

项目目前采用**脚本化冒烟验证**而非单元测试框架，发布前建议全部跑一遍：

```bash
# 语法
python -m py_compile app/server/*.py build_fpk.py
node --check app/ui/app.js
for f in cmd/*; do bash -n "$f" || exit 1; done

# 配置格式
python -c "import json,glob; [json.load(open(p,encoding='utf-8')) for p in
  ['branding.json','config/privilege','config/resource','app/ui/config',
   'wizard/install','wizard/config','wizard/uninstall']]"

# 包结构
python build_fpk.py --check
```

### 在线探测实现要点（1.0.4）

`app/server/api_ops.py` 中 `probe_host()` 是纯函数，可单独 import 做回归测试；`probe_records()` 负责并发调度、控制组校验与 SQLite 写回。ICMP 通过 `subprocess.run` 以参数列表调用系统 `ping`，IP 先经 `valid_ip()` 严格校验且不经过 shell；线程池只做网络探测，数据库连接不跨线程。判定策略与响应字段见 [API.md](API.md#421-在线探测104-重写)。

已验证过的范围（截至 1.0.4）：

| 验证 | 结果 |
| --- | --- |
| 后端接口冒烟（1.0.0 起） | 50 项通过 |
| 静态资源 / 网关前缀 / 登录态 / 目录穿越（1.0.0 起） | 15 项通过 |
| 模拟 fnOS 安装生命周期（1.0.0 ~ 1.0.3） | `install_init → install_callback → main start → HTTP → stop`，退出码正确 |
| 1.0.3 端到端接口与静态资源（历史） | 14 项通过 |
| 链接渲染 XSS 断言（1.0.3） | 5 项通过 |
| FPK 包内一致性 | 17 项通过 |
| 桌面浏览器渲染（1.0.3） | headless Chrome 截图目视通过 |
| 1.0.4 探测引擎回归：ICMP 判活 / 不可达判离线 / 注入输入拒绝 / 透明代理控制组 / TCP 兜底 / 耗时 | 10 项通过 |
| 1.0.4 语法与包结构 | Python / JS / Bash / JSON 通过，`build_fpk.py --check` 通过 |

> 1.0.4 未重新执行「模拟 fnOS 安装生命周期」（本机环境安全审批拦截该命令）。探测逻辑由独立回归测试覆盖，包内源码内容由静态校验覆盖；**真实飞牛设备上的安装验证仍待完成**。

模拟安装的关键点：把 `app/`、`cmd/`、`branding.json` 复制到一个临时 `target/`，设置 `TRIM_APPDEST` / `TRIM_PKGVAR` / `TRIM_PKGETC` / `TRIM_SERVICE_PORT` 后依次调用脚本。**`TRIM_APPDEST` 必须用绝对路径**（`cmd/main` 会 `cd` 切换工作目录）。

---

## 9. 二次开发指引

### 新增一个 CRUD 资源

1. 在 `app/server/api.py` 定义 `Resource` 子类，声明 `table`、`fields`、`required`、`searchable`、`label`。
2. 在 `core.py` 的 `SCHEMA` 增加建表语句（用 `CREATE TABLE IF NOT EXISTS`，保证升级幂等）。
3. 在 `build_router()` 的 `for prefix, cls, admin in (...)` 元组里注册一行；需要管理员才能写就把 `admin` 设为 `True`。
4. 前端：`app.js` 增加渲染函数、挂到 `SPECIAL`、在 `NAV` 配置里加入口。

`register_crud()` 会自动生成 `GET/POST /api/<prefix>` 与 `GET/PUT/DELETE /api/<prefix>/<id>` 五条路由。若某个前缀需要自定义路径（例如 `/api/subnets/tree`），**必须注册在通用 CRUD 之前**，否则会被 `/<id>` 抢先匹配——`build_router()` 里 `register_ops(r)` 放在前面就是这个原因。

### 新增业务接口

在 `api.py` 或 `api_ops.py` 写函数，签名 `(req, conn, **路径参数)`，返回可 JSON 序列化的 dict；用 `r.add(METHOD, 正则, handler, auth=True/False, admin=True/False)` 注册。抛 `ApiError(message, status)` 表示业务错误。

### 新增前端页面

`app/ui/app.js` 里加渲染函数 → 注册到 `SPECIAL` 或通用表格配置 → 在导航数组加一项；样式写进 `style.css`，遵循现有 CSS 变量，深浅主题都要能看清。

### 改品牌 / 联系信息

改 `branding.json` → `python build_fpk.py` 会自动同步 `manifest.maintainer` / `maintainer_url` / `display_name`；应用内「系统设置」也可修改，写入 `etc/branding.json`。字段长度上限 1000。

### 开发纪律（务必遵守）

- SQL 一律参数绑定；动态排序列走白名单映射。
- 过滤条件下推到 SQL，不要全表捞出后在 Python 里筛。
- 输出到 HTML 的内容一律 `esc()`；URL 只允许 `http/https`。
- 静态资源路径做目录穿越校验；只放行 `MIME` 白名单里的扩展名。
- 网关请求头只在 Socket 模式可信；业务鉴权由应用自己负责。
- 业务函数不要自行开第二个写连接（事务由 router 统一管理）。
- `cmd/` 脚本保持 LF + 0755，路径只用 `TRIM_*` 环境变量。

---

## 10. 版本发布流程

1. 同步版本号：`manifest.version`、`manifest.changelog`、`core.APP_VERSION`、`server.py` 启动日志默认值。
2. 更新 [CHANGELOG.md](../CHANGELOG.md)。
3. `python build_fpk.py --version <新版本>` 生成新包，`python build_fpk.py --check` 校验。
4. 更新 README 下载区：版本号、文件名、字节数、SHA-256、直链。
   ```bash
   python -c "import hashlib,pathlib; b=pathlib.Path('IPAM-<ver>.fpk').read_bytes(); print(len(b), hashlib.sha256(b).hexdigest())"
   ```
5. 跑一遍第 8 节全部验证。
6. `git add -A && git commit -m "release: v<版本>" && git tag v<版本> && git push origin main --tags`。
7. （可选）在 GitHub Releases 上传 `.fpk` 作为附件，README 里已预留 Releases 入口。

---

## 11. 已知限制

- **未在真实飞牛设备上实机安装验证**；`.fpk` 由自写打包器按官方公开的 tar.gz 结构生成，若应用中心校验报错请用官方 `fnpack` 重打包。
- 依赖系统 Python 3.8+；检测不到时安装脚本会给出明确错误。
- 端口固定 `8765`，向导不提供改端口，避免桌面图标指向与监听不一致。
- 不包含原同类工具中的工作流审批、扫描任务管理、字典项、维护 PDF 报表、设备密码加密查看（本版不存储设备密码）。
- 目前没有单元测试框架，验证依赖脚本化冒烟；后续可引入 `pytest` + `unittest.mock` 覆盖路由层。

---

## 12. 联系方式

- 作者 / 维护者：**梦非常浪漫**
- 联系邮箱：**12031050@qq.com**
- QQ群：**416290431**
- 开源地址：**https://github.com/12031050/ipam.git**

问题反馈请附 fnOS 版本、应用版本与 `$TRIM_PKGVAR/ipam.log` 相关片段。
