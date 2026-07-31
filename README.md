# MAF

**Minecraft Auto Fishing** — 面向 Minecraft Java 版的 Windows 自动钓鱼与桌面自动化工具。

> 严格来说，现在的 MAF 还应该叫 **Minecraft Auto Fishing**。不过没关系，缩写先占着——以后肯定会一路长成真正的 **Minecraft Automation Framework**。

在自动钓鱼之外，MAF 已经塞进了键鼠操作、OCR 字幕识别、任务编排和 JourneyMap 数据管理。所有自动化均通过屏幕截图和系统级键鼠输入完成，不修改 Minecraft 客户端，也不注入游戏进程。

> 当前界面语言为简体中文，仅支持 Windows。可以直接使用 Windows release，也可以从 Python 源码运行。

## 功能概览

| 模块 | 能力 |
| --- | --- |
| 自动钓鱼 | OCR 识别 Minecraft 辅助字幕中的水花提示，自动收竿、重新抛竿并记录本次统计 |
| 自动连点 | 支持左键或右键、点击间隔、执行次数和启动倒计时 |
| 按键保持 | 持续按住移动、潜行、跳跃等按键，停止任务时自动释放 |
| 任务编排 | 组合等待、点击、按键、截图、发送指令、等待字幕和 OCR 分支，支持循环、预设及 JSON 导入导出 |
| 定时指令 | 按指定间隔打开聊天框并发送 ASCII 指令，例如 `/home` 或 `/spawn` |
| 地图与坐标 | 实时浏览 JourneyMap 地图瓦片，管理路径点，双向同步并导出 CSV、JourneyMap JSON 或完整地图 PNG |
| 便捷工具 | 定时提醒、快速截图、可配置全局停止热键、系统托盘和 Windows 通知 |
| 运行记录 | 实时日志、任务状态和本地 SQLite 历史记录 |

MAF 还提供白天/黑夜主题、配置档案，以及 Minecraft 前台保护。默认情况下，切换到其他窗口会暂停键鼠输入和 OCR；切回游戏后继续执行。

## 环境要求

- Windows 10 或 Windows 11
- Python 3.10 或更高版本
- Minecraft Java Edition
- 使用自动钓鱼或 OCR 工作流时：Tesseract OCR 及 `chi_sim` 简体中文语言数据
- 使用地图功能时：JourneyMap（按 JourneyMap 5.10 的目录结构开发和验证）

## 使用 Windows release

1. 下载 `MAF-v0.1.0-windows-x64.zip`；
2. 将 ZIP 完整解压到一个可写目录；
3. 双击 `MAF.exe`。

不要直接在压缩包预览窗口中运行程序，也不要只复制 `MAF.exe`；同目录的 `_internal` 文件夹是程序运行所必需的。

自动钓鱼和 OCR 工作流仍需另行安装 Tesseract OCR 与 `chi_sim` 语言数据。其他功能不需要 Python 或 Tesseract。

## 从源码运行

下载或克隆仓库后，在项目目录打开 PowerShell：

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
python maf.py
```

如果依赖已经安装到系统 Python，也可以直接双击 `start_maf.bat`。

启动后建议先在“便捷工具”中确认全局停止热键。默认热键为 `F8`，可修改为 `F6`–`F12`。PyAutoGUI 的故障保护也保持启用：将鼠标快速移到屏幕左上角可以中止自动输入。

## 配置 OCR

自动钓鱼依赖 Tesseract 读取 Minecraft 辅助字幕。MAF 会优先从系统 `PATH` 查找 Tesseract，也会检查默认位置：

```text
C:\Program Files\Tesseract-OCR\tesseract.exe
```

安装时请同时准备简体中文语言文件：

```text
C:\Program Files\Tesseract-OCR\tessdata\chi_sim.traineddata
```

在 Minecraft 中使用自动钓鱼：

1. 在“选项 → 音乐和声音”中开启辅助字幕。
2. 进入 MAF 的“自动钓鱼”页，点击“框选字幕区域”。
3. 只框选右下角字幕文字所在的小块区域，减少无关画面。
4. 点击“测试一次 OCR”，检查是否能够识别“浮漂”“溅起”“水花”等关键词。
5. 点击开始，在倒计时结束前切回 Minecraft。

识别不稳定时，可打开“OCR 图像预览”，对比原始截图与二值化后的图像，并调整框选范围或 OCR 缩放倍率。

![Minecraft 水花辅助字幕示例](bobber_splash_template.png)

## JourneyMap 地图与坐标

在“地图与坐标”页选择 JourneyMap 根目录。正确的目录应直接包含 `data` 文件夹，例如：

```text
<Minecraft 实例目录>\journeymap\
└── data\
    ├── mp\
    └── sp\
```

扫描后可以选择存档或服务器、维度和图层：

- 按住鼠标左键拖动地图，使用滚轮围绕指针缩放；
- 从路径点列表定位标记，或在 MAF 中创建、复制和删除坐标；
- 每 2 秒发现新增或更新的地图瓦片，只加载当前视野内的 PNG；
- 手动或每 5 秒自动同步 JourneyMap 路径点；
- 导出坐标 CSV、JourneyMap JSON，以及带坐标元数据的完整地图 PNG。

路径点冲突时保留修改时间较新的版本。自动同步只传播新增和更新，不会把任意一侧的文件删除自动扩散到另一侧；在 MAF 中确认删除 JourneyMap 路径点时，才会同时删除对应 JSON。

批量保存或删除路径点前，建议先关闭游戏内的 JourneyMap 路径点管理界面，避免其缓存覆盖文件。

## 任务编排

任务编排器支持以下步骤：

- 等待；
- 鼠标点击；
- 按键；
- 按键保持；
- 发送 ASCII 指令；
- 截图；
- 等待字幕；
- OCR 条件分支。

步骤可以编辑、复制、拖拽排序、单步运行或从选中位置开始执行。循环次数设为 `0` 时会持续运行，直到按下全局停止热键。

工作流可以保存为命名预设，也可以导入或导出版本化 JSON。导入时会校验步骤类型和参数，不会执行未知动作。

## 数据与隐私

MAF 不需要网络服务，配置和任务记录保存在本机：

| 数据 | 默认位置 |
| --- | --- |
| 应用设置 | `%APPDATA%\MAF\settings.json` |
| 任务历史 | `%APPDATA%\MAF\data\history.db` |
| 快速截图 | `%USERPROFILE%\Pictures\MAF` |

OCR、地图和键鼠自动化均在本机运行。请注意，快速截图和 OCR 预览可能包含当前屏幕内容，分享文件前请自行检查。

从早期版本升级时，MAF 会尝试迁移项目目录中的旧设置和 SQLite 历史记录。

## 安全说明

- 默认启用 Minecraft 前台保护，避免切换窗口后继续向其他应用发送输入。
- 停止任务时会释放由 MAF 保持的按键。
- 全局停止热键即使焦点仍在 Minecraft 中也有效。
- JourneyMap 写入使用同目录临时文件和原子替换，降低文件中途损坏的风险。
- 完整地图导出有像素上限；地图过大时需要降低导出比例。

自动化可能违反部分服务器的规则。请仅在单人世界、私人服务器或明确允许相关行为的环境中使用。使用者需要自行承担因自动化操作产生的风险。

## 已知限制

- 当前仅支持 Windows，前台窗口检测、通知和全局热键都依赖 Windows API。
- 定时指令和工作流的自动文本输入仅支持 ASCII，不适合直接输入中文聊天内容。
- OCR 效果会受到界面缩放、资源包、字幕背景、分辨率和 Tesseract 语言数据影响。
- release 是免安装的 Windows x64 目录版，暂未提供安装向导或代码签名。

## 项目结构

```text
maf.py                          公共启动入口
maf_app.py                      Tkinter 图形界面与任务执行器
minecraft_auto_fish_subtitle.py OCR 截图、预处理和字幕匹配
workflow_model.py               工作流构建、校验与导入导出
journeymap_sync.py              JourneyMap 路径点读取和同步
journeymap_tiles.py             地图瓦片分析与完整 PNG 导出
journeymap_viewer.py            实时地图画布
window_guard.py                 Minecraft 前台窗口检测
profile_store.py                配置档案规范化
history_store.py                SQLite 任务历史
notification_service.py         Windows 通知及提示音回退
```

## 常见问题

**提示找不到 Tesseract 或 `chi_sim`**

确认 `tesseract.exe` 已加入 `PATH`，或安装在默认目录；然后检查 `tessdata` 中是否存在 `chi_sim.traineddata`。

**双击启动后立刻退出**

在 PowerShell 中执行 `python maf.py` 查看完整报错，并确认已经运行 `python -m pip install -r requirements.txt`。

**切回游戏后任务没有继续**

确认当前窗口是 Minecraft Java 游戏窗口；如果使用了修改窗口标题的启动器或模组，可临时关闭“仅在 Minecraft 前台时执行输入”进行排查。

**地图页找不到存档或服务器**

应选择包含 `data` 的 JourneyMap 根目录，而不是某个具体服务器、维度或 `waypoints` 子目录。

## 参与开发

欢迎通过 Issue 报告问题或提出功能建议。提交问题时，建议附上：

- Windows、Python、Minecraft 和 JourneyMap 版本；
- 可复现的操作步骤；
- MAF 运行日志中的错误信息；
- 与 OCR 有关的问题可附脱敏后的原图和预处理图。

提交代码前，请至少运行：

```powershell
python -m compileall -q .
```

请勿提交个人配置、任务历史、完整游戏地图、服务器地址或包含隐私信息的截图。

## 构建 release

安装构建依赖并执行脚本：

```powershell
py -3 -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install -r requirements.txt -r requirements-build.txt
powershell -NoProfile -ExecutionPolicy Bypass -File .\build_release.ps1 -PythonExe .\.venv-release\Scripts\python.exe
```

脚本会在 `release` 目录中生成 Windows x64 ZIP 和对应的 SHA-256 文件，并自动收集打包依赖的第三方许可证。构建过程完全在本机进行，不会上传文件。

## 许可证

MAF 使用宽松的 [MIT License](LICENSE)。release 中捆绑的第三方组件仍分别遵循各自的许可证，相关文本位于发布包的 `THIRD_PARTY_LICENSES` 目录。
