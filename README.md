<div align="center">

<img src="ICON_256.PNG" alt="IPAM Logo" width="120">

# IPAM · IP 地址管理（飞牛 fnOS 版）

**本地部署的 IP 地址与资产台账 · 零第三方运行时依赖 · 数据全部保存在 NAS**

</div>

---

## 📥 下载 FPK 安装包

| 项目 | 内容 |
| --- | --- |
| 当前版本 | **1.0.6** |
| 安装包 | **`IPAM-1.0.6.fpk`** |
| 文件大小 | 241.7 KB（247,510 字节） |
| SHA-256 | `e4ff9397c602c3f5d6009debba32f4e870134196c6e077d6a119534162e2fe5a` |
| 适用平台 | 飞牛 fnOS ≥ 1.2.0（all 架构），需系统 Python 3.8+ |
| 服务端口 | `8765` |

**直接下载地址（推荐，点击即可下载）：**

> ### 👉 https://raw.githubusercontent.com/12031050/ipam/main/IPAM-1.0.6.fpk

其他入口：

- [仓库内安装包文件](IPAM-1.0.6.fpk)（在 GitHub 文件列表中打开后点 `Download raw file`）
- [Tags / Releases](https://github.com/12031050/ipam/releases)：已推送 `v1.0.6` 标签，后续版本将在此发布
- 历史版本与变更说明见 [CHANGELOG.md](CHANGELOG.md)

获取源码：

```bash
git clone https://github.com/12031050/ipam.git
cd ipam
```

下载后可校验完整性（在飞牛 SSH 终端或本机执行）：

```bash
sha256sum IPAM-1.0.6.fpk
# 期望输出：e4ff9397c602c3f5d6009debba32f4e870134196c6e077d6a119534162e2fe5a
```

---

## 项目简介

IPAM 是一套运行在**飞牛 fnOS（NAS）**上的 IP 地址与资产台账系统。它把分散在表格里的 IP 分配信息，收敛成一个可搜索、可统计、可备份的本地 Web 应用：网段怎么划分、哪个地址被谁占用、绑定了哪台资产、还有多少可用地址，一屏可查。

设计原则：

- **数据不出 NAS**：SQLite 单文件数据库落在应用数据目录，不连外部服务、不上传任何数据。
- **零运行时依赖**：后端只用 Python 标准库（`http.server` / `sqlite3` / `ipaddress` / `hashlib`），前端是原生 JS + 自写 CSS，无 CDN、无 npm、无第三方字体图标，**不需要 pip 安装任何包**。
- **原生飞牛应用**：走应用中心安装，具备桌面图标、安装向导、端口服务、标准生命周期脚本（start / stop / status）与升级/卸载回调。
- **独立账号体系**：PBKDF2 密码哈希、会话 Cookie、登录失败锁定、角色与单位数据隔离、审计日志。

> 本项目为独立重写实现，源码、界面、图标与文档均为本项目自有内容，不复用任何第三方代码或素材。

---

## 功能一览

| 模块 | 能力 |
| --- | --- |
| **IP 记录** | 增删改查、多条件搜索/筛选/排序/分页、7 类批量操作（删除、回收、预留、状态、单位、负责人、标签）、在线探测 |
| **子网规划** | CIDR 校验、重叠检测、树形结构、利用率统计、地址空间 |
| **IP 池** | 地址池分配 / 释放、保留地址管理 |
| **VLAN** | VLAN 划分与记录关联 |
| **资产台账** | 资产 CRUD、资产与 IP 绑定、单位 / 部门 / 物理位置 / 标签 / 保存视图 |
| **统计** | 仪表盘、子网利用率、冲突检测、资产分布 |
| **数据流转** | CSV 导入（预览 + 校验 + 修复行）、CSV 导出、数据库备份 / 恢复 / 下载 |
| **系统** | 用户与角色、修改登录密码（用户管理内统一入口）、单位数据隔离、审计日志、字段标签自定义、关于页 |
| **安全** | PBKDF2 密码、会话 Cookie、登录锁定、SQL 参数绑定、输出转义、目录穿越防护 |

---

## 🆕 更新日志

> 完整历史见 [CHANGELOG.md](CHANGELOG.md)，这里只列最近三个版本。

### 1.0.6（2026-11-20）

- **白屏问题可从界面定位了**：新增启动占位层与 8 秒看门狗。脚本加载失败、接口请求失败或超时，页面会直接显示具体原因（例如「无法连接应用服务，请检查服务是否已启动」），不再整页纯白、用户无从判断。
- **接口请求增加 20 秒超时**：服务进程假死时不再无限等待。
- **修复一处会直接导致白屏的服务端缺陷**：请求不存在的静态文件原本会回退返回 `index.html`，浏览器把 HTML 当作 `app.js` 解析即整页空白；现在明确返回 404。
- **页面与脚本改为不缓存**（`no-store`），升级后不会再因旧 `index.html` / `app.js` 残留出现异常；图片仍缓存 5 分钟。
- 登录页脚注会显示服务不可达的原因，便于远程用户反馈。

### 1.0.5（2026-11-14）

- **补全「发布者」信息**：`manifest` 里 `distributor` / `distributor_url` 之前是空值，应用中心详情页的「发布者」因此显示为 `-`。现已填入 **梦非常浪漫** 与开源地址，与「开发者」一致；打包器也会在构建时从 `branding.json` 自动同步这两个字段，后续不会再丢失。
- **「修改登录密码」迁入用户管理**：系统设置页不再保留改密卡片，改密入口统一到 **用户管理 → 修改我的密码**，与账号相关的增删改查集中在一处。新弹窗增加「确认新密码」二次输入，并校验原密码、最少 6 位、两次一致。
- **关于本应用精简**：移除「运行环境（Python 版本）」与「数据存储（SQLite 大小）」两行，只保留应用名称、版本、作者、联系方式、版权声明、开源地址与运行平台。
- 系统设置页现仅保留「字段显示名称」。

### 1.0.4（2026-11-14）

- 修复在线探测误判：改为 **ICMP ping 优先 + 15 个常见端口 TCP 并发兜底**，并探测 `192.0.2.1` 控制组识别透明代理，避免把在线设备判为离线。
- 修复新增 / 编辑对话框点击遮罩层即关闭、丢失已填内容的问题。
- 系统设置页移除「基本设置」与「版权与联系信息」卡片。
- 列表的在线状态区分 **未探测 / 在线 / 离线** 三态。

---

## 🩺 打不开 / 白屏排查

v1.0.6 起页面会直接显示失败原因（启动占位层 + 8 秒看门狗）。若仍为纯白，请按顺序执行下面的命令（飞牛 SSH 终端）：

```bash
# 1. 服务是否在运行（0=运行中，3=未运行）
bash /var/apps/IPAM/target/cmd/main status; echo "退出码=$?"

# 2. 看启动日志（最常见的失败原因都记在这里）
tail -n 50 /var/apps/IPAM/var/ipam.log

# 3. 端口是否在监听
ss -lntp | grep 8765

# 4. 服务端本地是否正常返回页面
curl -s -o /dev/null -w 'HTTP %{http_code}\n' http://127.0.0.1:8765/

# 5. Python 运行时是否存在
/usr/bin/python3 -V
```

对照结论：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `status` 退出码 3，日志有「未找到 Python 3 运行环境」 | 系统缺 Python 运行时 | 安装飞牛应用中心的 Python 运行时，或在应用中心点「启动」重试 |
| 第 4 步非 200 | 服务未启动或端口被占用 | 换端口需重装；否则看第 2 步日志 |
| 第 4 步 200，但桌面窗口仍白 | 浏览器拦截了 iframe（常见于通过 HTTPS / fn Connect 域名访问，而应用走 `http://内网IP:8765`） | 先在浏览器新标签直接打开 `http://<NAS内网IP>:8765` 验证；能打开即确认为混合内容拦截，改用内网 IP 访问飞牛桌面，或等待统一网关版本 |
| 页面显示「加载超时」 | `app.js` 未加载或被拦截 | 强制刷新（Ctrl+F5）；仍失败请附上该提示 |

---

## 安装到飞牛 fnOS

1. 从上面的地址下载 `IPAM-1.0.6.fpk`。
2. 打开飞牛桌面 **应用中心 → 手动安装（或「安装第三方应用」）**，上传该 `.fpk`。
3. 按安装向导设置**管理员账号与初始密码**；作者、邮箱、QQ群、开源地址已预填，可保持默认。
4. 安装完成后，点击桌面图标，或浏览器访问 `http://<NAS_IP>:8765`，用第 3 步的账号登录。

数据位置（升级 / 卸载不会误删业务数据）：

```
/var/apps/IPAM/target/   应用源码（ui/、server/、branding.json）
/var/apps/IPAM/etc/      配置：branding.json
/var/apps/IPAM/var/      数据：ipam.db、backups/、exports/、日志与 PID
```

> ⚠️ 端口固定为 `8765`，向导不提供改端口，避免桌面图标指向与服务监听不一致。安装前请确认该端口未被占用。
>
> 若应用中心对包签名/结构校验报错，可在飞牛设备上用官方工具重新打包：`fnpack build -d <源码目录>`。

---

## 技术架构

```
浏览器（原生 JS 单页）
        │  HTTP /api/*  ·  JSON
        ▼
app/server/server.py     HTTP 服务、静态资源、网关 Unix Socket 适配
        │
        ▼
app/server/router.py     路由分发、鉴权、统一响应、通用 CRUD 资源
        │
        ├── app/server/api.py       业务接口（IP 记录、子网、统计…）
        └── app/server/api_ops.py   批量操作、导入导出、备份、冲突检测
        │
        ▼
app/server/core.py       SQLite 模型、认证、工具函数、品牌配置
        │
        ▼
  TRIM_PKGVAR/ipam.db    （19 张表，WAL 模式）
```

外层是 fnOS 应用框架：`manifest` 声明元信息与端口，`cmd/` 提供生命周期，`wizard/` 收集安装参数，`config/` 声明权限，`app/ui/config` 定义桌面入口。

---

## 目录结构

```
IPAM/
├── README.md                 ← 本文件
├── CHANGELOG.md                 版本历史
├── LICENSE                      许可与免责声明
├── manifest                     fnOS 应用元信息（版本、端口、维护者）
├── branding.json                品牌信息（作者 / 邮箱 / QQ群 / 开源地址 / 免责声明）
├── build_fpk.py                  纯 Python 打包器（生成 .fpk）
├── ICON.PNG / ICON_256.PNG       应用图标 64 / 256
├── IPAM-1.0.6.fpk               ← 可直接安装的成品包
├── assets/ipamlogo.png           Logo 源图（透明底 RGBA）
├── app/
│   ├── ui/                       index.html / app.js / style.css / config / images/
│   └── server/                   server.py / router.py / core.py / api.py / api_ops.py
├── cmd/                          生命周期：main、install_*、upgrade_*、uninstall_*、config_*
├── config/                       privilege（run-as=package）、resource
├── wizard/                       install / config / uninstall 向导
└── docs/
    ├── DEVELOPMENT.md           开发文档（架构、运行、构建、二开）
    └── API.md                   REST API 参考
```

---

## 开发文档

| 文档 | 内容 |
| --- | --- |
| [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) | 开发环境、本地运行、fnOS 应用框架、数据库模型、构建打包、测试、二次开发、发布流程 |
| [docs/API.md](docs/API.md) | 认证约定、统一响应格式、全部 REST 接口清单与调用示例 |
| [CHANGELOG.md](CHANGELOG.md) | 各版本变更记录 |

快速构建（需要 Python 3.8+）：

```bash
python build_fpk.py              # 生成 IPAM-<version>.fpk
python build_fpk.py --check      # 校验已有安装包结构
python build_fpk.py --version 1.0.6   # 指定版本号并同步 manifest
```

---

## 📮 联系方式

| 项目 | 信息 |
| --- | --- |
| 作者 / 维护者 | **梦非常浪漫** |
| 联系邮箱 | **12031050@qq.com** |
| QQ群 | **416290431** |
| 开源地址 | **https://github.com/12031050/ipam.git** |
| 仓库主页 | https://github.com/12031050/ipam |

欢迎提 Issue、反馈问题或交流功能建议；Bug 反馈请附上 fnOS 版本、应用版本与 `$TRIM_PKGVAR/ipam.log` 中的相关片段。

---

## 免责声明

本软件为开源项目，按「现状」（AS IS）提供，作者及贡献者不提供任何明示或暗示的担保，包括但不限于对适销性、特定用途适用性与非侵权性的担保。在任何情况下，作者与贡献者均不对因使用本软件或与之相关的任何直接、间接、偶然、特殊、惩罚性或后果性损害（包括但不限于数据丢失、业务中断、利润损失）承担责任，即使已被告知此类损害的可能性。使用者须自行评估并承担使用风险，仅在合法授权范围内用于 IP 与资产管理，并在生产环境使用前做好数据备份。第三方基于本项目的修改、再分发或商业化行为与本项目作者无关。

---

## 许可

详见 [LICENSE](LICENSE)。应用标识、界面与文档版权归作者所有；请定期备份数据。

---

<div align="center">

© 2026 梦非常浪漫 · IP AM · 飞牛 fnOS 应用

</div>
