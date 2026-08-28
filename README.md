# pywangpan

网盘分享链接解析与高速下载的 **Python 实现**（参考 Android 版 YunX，移植其协议与下载引擎，可在 Windows / macOS / Linux PC 上运行）。

已实现 **夸克 / UC / 百度 / 139 / 123 / 迅雷** 六条链路：识别分享链接 → 解析 →（按平台需要）转存临时目录 → 取直链 → 分片并发下载 → 清理临时转存。提供 **桌面 GUI（tkinter）** 与 **交互式 TUI（rich）** 两种界面，并可打包成单文件 `pywangpan.exe` 双击运行。

> 免责声明：仅供个人学习与技术交流。网盘协议接口会随官方调整而失效，请以实际运行结果为准。**不建议用百度网盘**，频繁操作可能导致账号被风控。

---

## 功能特性

- **6 平台**分享链接解析：夸克、UC（天猫网盘）、百度、139 和彩云、123 云盘、迅雷网盘
- **增量/分片并发下载**：主池+弹性区、断点续传、Range 忽略回退、合并前完整性校验、实时速度
- **两种界面**：桌面 GUI 窗口（推荐日常用）+ 终端 TUI
- **便捷登录态保持**：Cookie / Token 首次输入后保存到本地 `~/.pywangpan/config.json`，后续自动复用
- **工具内浏览器登录（自动取 Cookie）**：夸克 / UC / 百度 / 139 可直接在工具里打开浏览器登录，登录完点一下自动抓取 Cookie，不用手动复制粘贴

---

## 安装依赖

要求 **Python 3.10+**。

```bash
pip install -r requirements.txt
```

- `requests`、`rich`：必需。
- `playwright`：**可选**，仅用于"工具内打开浏览器登录并自动取 Cookie"。系统需装有任一浏览器：
  - **Microsoft Edge（Windows 10/11 自带）** 或 **Google Chrome** 之一即可，无需额外下载浏览器。
  - 若机器上两者都没有，才需要额外安装一次 Playwright 自带的 Chromium：
    ```bash
    python -m playwright install chromium      # 仅在无 Edge/Chrome 时才需要
    ```

**打包后的 `pywangpan.exe`** 已内置这些依赖（含 Playwright 驱动），装好 exe 的机器只需**装有 Edge 或 Chrome** 即可使用全部功能，无需装 Python。

---

## 使用方式

> 项目根目录 `D:\zy\小工具\pywangpan\` 里包含一个同名 `pywangpan` 包和 `pywangpan_gui.py`
> 入口。请在**项目根目录**下运行（PyCharm 也直接打开这个目录即可，`pywangpan` 已被设为
> 源码根）：`cd D:\zy\小工具\pywangpan`。

### 方式一：桌面 GUI（推荐）

```bash
python -m pywangpan.cli --gui
```

或双击打包好的 `pywangpan.exe`（已建桌面快捷方式"pywangpan 网盘下载"）。

界面操作：

1. 在「分享链接」输入框粘贴链接（自动识别平台）；
2. 点「解析」；
3. 按弹窗提示提供登录态（详见下方"各平台登录方式"）；
4. 在弹出的文件浏览器里选文件/进目录（双击目录进入、上级返回、选中文件即下载）；
5. 后台线程分片并**发**下载，底部实时显示进度条、速度与日志；
6. 顶部可设置**并发线程数**与**输出目录**。

### 方式二：交互式 TUI（无桌面环境 / 终端风格）

```bash
python -m pywangpan.cli
```

1. 选择「解析分享链接并下载」，粘贴分享链接；
2. 按提示登录（TUI 下浏览器登录会另开一个浏览器窗口，登录完成后回到终端**回车**确认）；
3. 目录导航选择文件：输入序号选文件/进目录，`b` 上级、`q` 取消；
4. 分片并发下载，rich 实时进度条。

### 方式三：一次性脚本（夸克单平台）

```bash
# 从浏览器复制夸克 Cookie 字符串
python -m pywangpan.cli \
  --cookie "你的夸克Cookie" \
  --url "https://pan.quark.cn/s/xxxxxx" \
  --pwd "提取码(可省)" \
  --threads 16 \
  --out ./downloads
```

也可把 Cookie 存进文件：`--cookie-file cookie.txt`。

---

## 各平台登录方式

| 平台 | 登录方式 | 保存的凭据 |
|------|----------|-----------|
| 夸克 | ① 工具内浏览器登录（自动取 Cookie）② 手动粘贴 Cookie | `quark` Cookie |
| UC | ① 工具内浏览器登录 ② 手动粘贴 Cookie | `uc` Cookie |
| 百度 | ① 工具内浏览器登录 ② 手动粘贴 Cookie（须含 `BDUSS`）| `baidu` Cookie |
| 139 和彩云 | ① 工具内浏览器登录 ② 手动粘贴 Cookie | `c139` Cookie |
| 123 云盘 | 手机号 + 密码（登录拿 Token）| `pan123` Token |
| 迅雷网盘 | 密码 或 短信验证码 + 验证码（完整登录，自动刷新）| `xunlei` Token |

**工具内浏览器登录**（夸克/UC/百度/139）使用步骤：

1. 选择「在工具内打开浏览器登录」；
2. 工具用系统 Edge / Chrome 打开一个独立的浏览器窗口，并停留到登录页；
3. 你在该窗口内正常登录（扫码/账号密码均可）；
4. 登录完成后：
   - **GUI**：回到工具点击弹窗里的「已登录，读取 Cookie」；
   - **TUI**：回到终端按回车确认；
5. 工具自动从浏览器会话读取 Cookie 并保存，之后即可解析下载；下次用已保存的 Cookie，无需再登录。

> 首次取得的 Cookie/Token 会写入 `~/.pywangpan/config.json`（默认仅本机权限）。再解析同一平台时会询问"使用已保存的登录态吗？"，选是即可免登录。

---

## 打包成单文件 exe

```bash
python -m PyInstaller --clean --noconfirm pywangpan.spec
```

产物：`dist/pywangpan.exe`（单文件、无控制台窗口，约 58MB，已内置 Python、依赖与 Playwright 驱动）。

- 分发时**无需**安装 Python、也无需安装 Playwright 自带浏览器；
- 使用目标机器只需装有 **Edge 或 Chrome** 中的任意一个即可用"工具内浏览器登录"；
- 依赖打包与 `pywangpan.spec` 一起管理；`pywangpan_gui.py` 是打包入口（双击/打包用）。

---

## 目录结构

```
pywangpan/                 ← 项目根目录（PyCharm 打开此目录，pywangpan 设为源码根）
  pywangpan/                       ← 主包 pywangpan
    __init__.py
    cli.py                命令行入口（--gui 进窗口 / 无参数进 TUI / 带参数走一次性流程）
    gui.py                tkinter 桌面窗口（后台线程 + 主线程弹窗桥接）
    tui.py                交互式 TUI（rich 菜单/目录导航/进度条）
    ui.py                 UI 抽象协议 + rich 实现（TUI 与 GUI 共用 handler）
    webview.py            Playwright 封装：工具内浏览器登录 + 自动读取 Cookie
    config.py             本地登录态存储（Cookie/Token 持久化 JSON）
    pan/
      parser.py           分享链接解析（正则提取 share_id / 提取码 / 平台）
      cookie.py           __puus/__pus cookie 合并保鲜
      models.py           数据模型（ShareFile / ShareSession / DownloadLink）
      quark.py            夸克 API 封装
      resolver.py         夸克解析流程编排（转存/取链/清理）
      baidu.py            百度 API 封装（BDUSS / verify / transfer / locatedownload）
      baidu_resolver.py   百度流程编排（appall 直链，取链即删转存）
      xunlei.py           迅雷 API + 登录（captcha/短信/token 交换）+ 自动刷新
      xunlei_fingerprint.py 迅雷设备指纹生成与持久化
      xunlei_resolver.py  迅雷流程编排（Bearer 认证）
      uc.py               UC API 封装（复用 cookie.py 保鲜）
      uc_resolver.py      UC 流程编排（官方下载/视频 preview）
      pan123.py           123 API（CRC32 签名 / download-v2 Base64 / redirect 探测）
      pan123_resolver.py  123 流程编排（免转存直取 + copy_save 转存回退）
      c139.py             139 API（AES-CBC 加密 / mcloud-sign / 渠道头）
      c139_resolver.py    139 流程编排（linkID 直取，无需转存）
    downloader/
      chunk_downloader.py  Range 分片下载器（断点续传/校验）
      downloader.py        任务管理（主池+弹性区/回退单流/合并）
  tests/                  单元测试
  pywangpan_gui.py      PyInstaller 打包入口（双击/打包用）
  pywangpan.spec        PyInstaller 打包配置（onefile、含 Playwright 驱动）
  requirements.txt      依赖清单
  README.md
```

---

## 下载引擎要点（对齐 Android 版）

- **分片规划**：主池 70% 等分 + 弹性区 30% 按字节序领 4MB 块，保证物理相邻、平滑转场
- **断点续传**：`part_i` / `seg_起_止.part` 保留，按磁盘已有长度续传；分片计划签名（`plan.txt`）变化则清空重下
- **Range 忽略回退**：服务器返回 200 整文件时回退单流，避免逐片重复下载整文件
- **完整性校验**：合并前校验 `分段之和 == total`，拒绝损坏文件
- **速度统计 / 进度回调**：`DownloadProgress` 提供 `downloaded / total / speed / done`

---

## 测试

```bash
python tests/test_parser.py      # 解析器
python tests/test_download.py    # 下载引擎（本地 Range 服务器，校验 md5）
python tests/test_platforms.py   # 百度/迅雷/夸克纯逻辑 + 请求构造（mock，无网络）
python tests/test_platforms3.py  # UC/123/139 纯逻辑 + 加密/签名（mock，无网络）
python tests/test_tui.py         # TUI 菜单/目录导航/配置存储 + 浏览器登录流程（mock）
python tests/test_gui.py         # GUI TkUI 主线程桥接 + 格式函数（需桌面环境）
python tests/test_webview.py     # webview：登录取 Cookie（真实 Edge/Chromium headless）
```

---

## 扩展其它平台

以 `pan/quark.py` 为模板，实现 `pan/xxx.py`（对应 Android 的 `XxxApi.kt`）与对应的 `xxx_resolver.py` 流程编排即可。各平台差异备忘：

| 平台 | 特殊处理 |
|------|---------|
| 百度 | surl 去掉开头 `1`；转存后取链，用完清理 |
| 迅雷 | 单文件 Range 并发上限约 8，需封顶 |
| UC | 官方下载免转存；视频走 video_preview 绕过会员墙；部分流需 HLS 合并 |
| 123 | CRC32 签名头；分享免转存直取；S3KeyFlag 转存到 mshare 回退 |
| 139 | 分享接口 AES-CBC 加密 + mcloud-sign 明文签名；linkID 免转存直取 |
