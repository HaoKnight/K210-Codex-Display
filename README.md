<div align="center">
  <img src="./icons/AppIcon-source.png" alt="K210 Codex Display Hub 图标" width="112" height="112" />
  <h1>K210 Codex Display Hub</h1>
  <p>在 K210 小屏和本机网页查看 Codex 额度、任务状态、时间与天气。</p>
  <p>
    <img src="https://img.shields.io/badge/version-1.3.0-2ea44f" alt="版本 1.3.0" />
    <img src="https://img.shields.io/badge/host-macOS-000000?logo=apple" alt="上位机平台 macOS" />
    <img src="https://img.shields.io/badge/device-K210%20%2F%20CanMV-17A398" alt="K210 和 CanMV" />
  </p>
  <p><a href="#features">功能</a> · <a href="#preview">预览</a> · <a href="#start">运行</a> · <a href="#device">连接设备</a></p>
</div>

K210 Codex Display Hub 包含 macOS 上位机、网页控制台和 K210 板端程序。上位机从本机 Codex 日志或 Codex 服务获取额度，通过 Type-C 串口更新设备画面；没有设备时也能使用网页预览和演示模式。

<a id="features"></a>

## ✨ 功能特性

- **Codex 状态**：显示 5 小时与 7 天额度、当前任务状态和模型。任务状态根据本机日志推断，启动时恢复长会话的任务开始和模型记录，排除内部自动审核，并按任务 ID 确认完成。
- **重置预告**：右下角模型位显示公开重置或发卡消息及倒计时；没有有效消息时恢复模型名，点击模型名或消息徽标都可查看重置信息。网页提供原帖链接和消息开关。
- **两种桌面**：在 Codex 信息页与数字时钟页之间切换；同时显示时间和天气。
- **网页预览**：在浏览器查看并点击 K210 画面，设置串口、数据来源、时区和天气。
- **触屏设置**：在 K210 上切换桌面、翻转屏幕、调节亮度并查看设备信息。
- **同步与更新**：支持手动同步、设备端点击 7D 额度圈同步，以及通过串口更新 `main.py`。
- **菜单栏应用**：打开控制台、查看连接状态、同步、重连和设置开机自启。

<a id="preview"></a>

## 📸 界面预览

下图是实际运行的演示模式；额度和天气是模拟数据，未连接 K210。

<div align="center">
  <img src="./screenshot/dashboard.png" alt="演示模式下的 K210 预览和网页控制台" width="100%" />
</div>

<a id="start"></a>

## 📦 安装与运行

上位机需要 macOS 和 Python 3。使用真实数据需要本机 Codex 日志或已登录的 Codex 命令行程序；演示模式不需要 K210 或 Codex 账号。在项目根目录运行：

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python run.py --source demo --serial '' --open
```

控制台默认地址为 <http://127.0.0.1:2100>；端口可在配置中修改。读取真实数据时运行 `.venv/bin/python run.py`。从源码启动 macOS 菜单栏应用可运行 `.venv/bin/python tray_app.py`。

点击应用图标会启动后台并自动打开控制台；软件已运行时，再次点击会打开现有后台的控制台，不重复启动后台。开机自动启动保持后台运行，手动点击图标时再打开网页。

<a id="device"></a>

## 🔌 连接 K210

1. 用 CanMV IDE 将 `K210/main.py`、`K210/program_update.py` 和 `K210/font.bin` 保存到开发板同一目录：使用 MicroSD 时存入 `/sd/`，否则存入 `/flash/`，然后重启设备。主程序将在线更新代码延后加载，以给屏幕画布留出启动内存。
2. 断开 CanMV IDE 对串口的占用。在网页中选择“USB 连接（115200）”，扫描并选择 K210 的 Type-C 串口，然后点击“连接”。
3. 点击“连接”，等待页面显示“已收到设备确认”。

### 外接蓝牙串口（HC-05 等）

P7 是 K210 的 IO7/TX，接 HC-05 的 RX；P6 是 IO6/RX，接 HC-05 的 TX；两端 GND 必须相连。HC-05 的 VCC 应按所购模块底板的标注供电，TX/RX 使用 3.3V 逻辑电平。

### 在线更新主程序

连接设备并收到确认后，在控制台“K210 主程序更新”中选择 `K210/main.py`，点击“更新 main.py”。更新会覆盖设备主程序，请确认所选文件并保持供电。小屏幕显示更新进度，后台查询与预览暂时暂停，更新完成或失败后自动恢复。本次详情页字体更新需先通过 CanMV IDE 同步 `K210/font.bin`，再更新主程序；以后的主程序更新可继续使用这份配套字体。

## 📰 重置消息

点击右下角的模型名或消息徽标可查看详情。确认重置或发卡的消息在查看后标记为已读，首页恢复模型名；新确认消息会再次提醒。已读记录支持网页与设备同步，重启后保留。

详情页顶部直接显示“公开确认”“发放预告”或“重置预告”，使用 25 像素标题；正文和时间使用 21 像素较粗字形，中英文数字按同一视觉基线对齐，底栏保持 15 像素。公开确认显示绿色，预告显示琥珀色，陈旧数据显示灰色，消息类型、时间和说明分三行居中。

详情页大字号字形追加保存在 `font.bin` 中，不改变原有字形偏移，设备按需读取并解压，避免内嵌字形挤占 512 KB 启动堆。

网页“重置消息”区域可关闭消息显示。“预览”开关可模拟预告、已确认或暂无消息，修改内容后自动生效，关闭后恢复实时画面；模拟仅影响网页，不覆盖真实消息或发送到设备。

## ⚙️ 配置与开发

源码运行时，设置保存在项目根目录的 `config.local.json`；打包应用保存在 `~/Library/Application Support/K210 Codex Display Hub/config.json`。配置模板见 `config.example.json`。默认数据来源为本机日志，天气默认自动定位；网页中可改为手动城市。`config.local.json` 已被 Git 忽略。

使用 Codex 服务时，上位机会从 PATH、常见 CLI 安装目录及 `/Applications`、`~/Applications` 中的 ChatGPT/Codex 应用包查找 `codex`。菜单栏启动不继承终端 PATH；若使用其他安装位置，可将配置中的 `codex_bin` 设为可执行文件的绝对路径，然后重启上位机。天气与额度独立查询；天气错误会区分自动定位、城市查询和天气查询，并提示超时、DNS 或 HTTPS 等原因。

测试与 macOS 打包：

```bash
.venv/bin/python -m pip install -r requirements-build.txt
.venv/bin/python -B -m unittest discover -s tests -v
.venv/bin/python build.py
```

打包结果位于 `dist/K210 Codex Display Hub.app`。修改板端字体后，可用 `.venv/bin/python tools/generate_fonts.py --check` 核对两份 K210 程序的字形。

## 🔐 数据说明

- **Codex**：读取本机 `~/.codex` 会话日志，或通过本机 `codex app-server` 查询额度。
- **天气与定位**：天气来自 Open-Meteo；自动定位使用 `ipwho.is`，该服务会接收公网 IP。
- **重置消息**：来自 [AIhot](https://aihot.news/codex-reset) 的公开信息，不使用 Codex 登录凭据、不改变账户额度；公开确认不代表个人账户已经到账。
- **访问范围**：网页仅监听 `127.0.0.1`，接口没有登录认证，请勿将端口公开。
