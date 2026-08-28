# pywangpan

网盘分享链接解析与高速下载的 **Python 实现**（参考 Android 版 YunX，仅移植协议与下载引擎，跨平台 PC 可用）。

已实现 **夸克 / 百度 / 迅雷 / UC / 123 / 139** 六条链路：解析 → 转存临时目录（部分平台可免转存直取）→ 取直链 → 分片并发下载 → 清理临时转存。
分享解析、转存、直链逻辑已全部接入，但多数平台仍需真实账号 Cookie / token 才能联调，建议先本地验证。

> 免责声明：仅供个人学习与技术交流。协议接口会随官方调整而失效，请以实际运行结果为准。不建议用百度网盘，可能导致账号被风控。

## 依赖

- Python 3.10+
- `pip install -r requirements.txt`（`requests`、`rich`）

## 使用

**交互式 TUI（推荐，支持 6 平台）**：

```bash
python -m pywangpan.cli
```

进入主菜单后：
1. 选择「解析分享链接并下载」，粘贴分享链接（自动识别平台）；
2. 按提示提供登录态：夸克/UC/百度/139 粘贴 Cookie，123 输入手机号+密码（登录拿 Token），迅雷走完整登录（密码或短信+验证码）；
3. 首次输入的 Cookie/Token 会保存到 `~/.pywangpan/config.json`，后续可复用；
4. 目录导航选择文件（`b` 上级、`q` 取消），即可分片并发下载（rich 进度条）。

**桌面 GUI（tkinter 窗口，推荐有桌面环境时使用，支持 6 平台）**：

```bash
python -m pywangpan.cli --gui
```

窗口内：粘贴分享链接 → 点「解析」→ 按提示弹窗收集登录态 → 目录/文件浏选取 →
后台线程分片并发下载，实时进度条 + 日志。线程数与输出目录可在窗口内设置。
tkinter 随 Python 自带，无需额外安装。

**一次性参数（夸克单平台，供脚本）**：

```bash
# 夸克 Web 登录后，从浏览器复制 Cookie 字符串
python -m pywangpan.cli \
  --cookie "你的夸克Cookie" \
  --url "https://pan.quark.cn/s/xxxxxx" \
  --pwd "提取码(可省)" \
  --threads 16 \
  --out ./downloads
```

也可把 Cookie 存到文件：`--cookie-file cookie.txt`。

## 目录结构

```
pywangpan/
  pan/
    parser.py          分享链接解析（正则提取 share_id/提取码/平台）
    cookie.py          __puus/__pus 合并保鲜
    models.py          数据模型
    quark.py           夸克 API 封装
    resolver.py        夸克解析流程编排（转存/取链/清理）
    baidu.py           百度 API 封装（BDUSS Cookie / verify / transfer / locatedownload）
    baidu_resolver.py  百度流程编排（appall 直链，取链即删转存）
    xunlei.py          迅雷 API + 登录（captcha/短信/token 交换）+ 自动刷新
    xunlei_fingerprint.py  迅雷设备指纹生成与持久化
    xunlei_resolver.py 迅雷流程编排（Bearer 认证）
    uc.py              UC API 封装（复用 cookie.py 保鲜）
    uc_resolver.py     UC 流程编排（官方下载/视频 preview）
    pan123.py          123 API（CRC32 签名 / download-v2 Base64 / redirect 探测）
    pan123_resolver.py 123 流程编排（免转存直取 + copy_save 转存回退）
    c139.py            139 API（AES-CBC 加密 / mcloud-sign / 渠道头）
    c139_resolver.py   139 流程编排（linkID 直取，无需转存）
  downloader/
    chunk_downloader.py   Range 分片下载器（断点续传/校验）
    downloader.py         任务管理（主池+弹性区/回退单流/合并）
  tui.py              交互式 TUI（rich 菜单/目录导航/进度条）
  ui.py               UI 抽象协议 + rich 实现（TUI 与 GUI 共用 handler）
  gui.py              tkinter 桌面窗口（后台线程 + 主线程弹窗桥接）
  config.py           本地登录态存储（Cookie/Token 持久化 JSON）
  cli.py              命令行入口（--gui 进窗口，无参数进 TUI，带参数走一次性流程）
  tests/              单元测试（本地模拟 Range 服务器校验）
```

## 下载引擎要点（对齐 Android 版）

- **分片规划**：主池 70% 等分 + 弹性区 30% 按字节序领 4MB 块，保证物理相邻、平滑转场
- **断点续传**：`part_i` / `seg_起_止.part` 保留，按磁盘已有长度续传；分片计划签名（`plan.txt`）变化则清空重下
- **Range 忽略回退**：服务器返回 200 整文件时回退单流，避免逐片重复下载整文件
- **完整性校验**：合并前校验 `分段之和 == total`，拒绝损坏文件
- **速度统计 / 进度回调**：`DownloadProgress` 提供 `downloaded/total/speed/done`

## 测试

```bash
python tests/test_parser.py     # 解析器
python tests/test_download.py   # 下载引擎（本地 Range 服务器，校验 md5）
python tests/test_platforms.py  # 百度/迅雷/夸克纯逻辑 + 请求构造（mock，无网络）
python tests/test_platforms3.py # UC/123/139 纯逻辑 + 加密/签名（mock，无网络）
python tests/test_tui.py        # TUI 菜单/目录导航/配置存储（mock 输入，无网络）
python tests/test_gui.py        # GUI TkUI 主线程桥接 + 格式函数（需桌面环境）
```

## 扩展其它平台

以 `pan/quark.py` 为模板，实现 `pan/xxx.py`（对应 Android 的 `XxxApi.kt`）与对应的 `xxx_resolver.py` 流程编排即可。各平台差异备忘：

| 平台 | 特殊处理 |
|------|---------|
| 百度 | surl 去掉开头 `1`；转存后取链，用完清理 |
| 迅雷 | 单文件 Range 并发上限约 8，需封顶 |
| UC | 官方下载免转存；视频走 video_preview 绕过会员墙；部分流需 HLS 合并 |
| 123 | CRC32 签名头；分享免转存直取；S3KeyFlag 转存到 mshare 回退 |
| 139 | 分享接口 AES-CBC 加密 + mcloud-sign 明文签名；linkID 免转存直取 |
