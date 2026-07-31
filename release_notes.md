# MAF v0.1.0

这是 MAF（Minecraft Automation Framework）的首个公开预览版本。

## 主要功能

- OCR 辅助字幕自动钓鱼与识别记录；
- 自动连点、按键保持、定时指令和快速截图；
- 可编辑、循环和导入导出的任务工作流；
- JourneyMap 实时地图、坐标管理、路径点同步和完整地图导出；
- Minecraft 前台输入保护、全局停止热键、系统托盘与通知；
- 本地配置档案、运行日志和 SQLite 任务历史。

## 安装

1. 下载 `MAF-v0.1.0-windows-x64.zip`；
2. 将 ZIP 完整解压到一个可写目录；
3. 双击 `MAF.exe`。

不要直接在压缩包预览窗口中运行程序，也不要只复制 `MAF.exe`；同目录的 `_internal` 文件夹是程序运行所必需的。

## OCR 额外要求

自动钓鱼和 OCR 工作流需要用户另行安装 Tesseract OCR，以及简体中文语言文件 `chi_sim.traineddata`。其他不依赖 OCR 的功能无需 Tesseract。

## 兼容性

- 构建平台：Windows x64
- 构建 Python：3.10
- JourneyMap：按 5.10 目录结构开发和验证

这是预览版本。执行任何自动化前，请先确认 Minecraft 服务器规则，并熟悉默认的 `F8` 全局停止热键。
