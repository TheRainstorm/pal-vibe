# Pal-Vibe: Media Library Organizer

Pal（parse-and-link） 是一个强大的媒体文件整理工具，旨在实现电影和电视剧文件的自动化管理。它能扫描杂乱的源目录，从文件名提取标题、年份和集数等元数据，并在目标目录生成结构化的软/硬链接，从而大幅提高 Jellyfin/Plex/Emby 等媒体服务器刮削的准确率。

## 🚀 核心特性

*   **非侵入式**: 仅在目标目录生成链接（Symlink/Hardlink），**不用修改或移动源文件**，对于 BT/PT 做种友好。
*   **通过Plugin扩展视频类型**: 代码面向插件设计，内置 Movie, TV, WebDL 三种 plugin，可通过重载元信息提取函数、链接目标等函数实现其它类型视频插件。
*   **智能元数据提取**: 程序支持使用 GuessIt 规则库实现简单的元信息提取。同时也支持使用强大的 LLM 进行元数据识别（较大的模型基本可以实现完美的识别效果）。并且还可以通过定义处理链（Processing Chain）灵活组合不同方法和 LLM 模型。
*   **增量更新**: 识别结果保存到数据库中，重复运行时，仅处理新增或修改的文件。
*   **手动修正设计**: 用户可直接修改 yaml 格式的数据库，程序会通过对元数据进行哈希校验，自动对手动修改的文件重新链接。
*   **实时监听**: 支持监听源目录（自动处理新文件）和目标目录（删除链接时反向同步删除源文件）。
*   **Web Dashboard**: 提供可视化界面查看任务状态、文件树，并支持在线编辑元数据。
*   **Batch 处理**: 将多个文件作为一个batch一次性识别元数据，大幅提升 LLM 处理速度，降低 LLM 调用成本，还能提升 TV 等类型的识别准确率（剧集间有关联）。

**TODO**

- [ ] 智能识别动画特典内容（SP,OP,ED,CM,PV 等资源）


## 🛠️ 安装

### 前置要求

*   Python 3.10+
*   FFmpeg (用于获取视频技术参数，如分辨率/HDR)

### 步骤

1.  **克隆仓库**
    ```bash
    git clone https://github.com/your-repo/pal-vibe.git
    cd pal-vibe
    ```

2.  **创建虚拟环境并安装依赖**
    ```bash
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt # 如果有，或者如下手动安装
    pip install pyyaml guessit openai fastapi uvicorn python-multipart watchdog
    ```

## ⚡ 快速开始 (Quick Start)

### 1. 命令行模式 (CLI)

最简单的用法是直接通过命令行处理单个目录：

```bash
# 整理电影目录
python3 src/pal.py -s "/mnt/downloads/movies" -d "/mnt/media/movies" -t movie -S

# 整理电视剧目录
python3 src/pal.py -s "/mnt/downloads/tv" -d "/mnt/media/tv" -t tv -S
```

*   `-s`: 源目录
*   `-d`: 目标目录
*   `-t`: 类型 (movie, tv, webdl)
*   `-S`: 使用软链接 (推荐)

### 2. 配置文件模式 (推荐)

对于复杂的媒体库，推荐使用 `config.yaml` 进行批量管理。

创建一个 `config.yaml` 文件：

```yaml
defaults:
  db: "pal.yaml"
  soft_link: true
  
# 定义 LLM 提供商 (可选)
providers:
  deepseek:
    type: "llm"
    api_key: "sk-xxxx"
    base_url: "https://api.deepseek.com"
    model: "deepseek-coder"

tasks:
  - src: "/mnt/data/Movies"
    dst: "/mnt/links/Movies"
    type: "movie"
    chain: ["guessit"] # 默认使用 guessit

  - src: "/mnt/data/Anime"
    dst: "/mnt/links/Anime"
    type: "tv"
    # 处理链：先尝试 GuessIt，失败则使用 DeepSeek LLM
    chain: ["guessit", "deepseek"] 
    monitor_src: true # 开启源目录监听
    monitor_dst: false
```

运行：
```bash
python3 src/pal.py -c config.yaml
```

### 3. 启动 Web UI

Web UI 提供了一个直观的仪表盘来管理任务和修复错误。

```bash
./start_web.sh
```
访问浏览器: `http://localhost:8000`

*   **Stats**: 查看总文件数和错误数。
*   **Tasks**: 选择左侧任务查看文件树。
*   **Metadata Editor**: 点击文件，修改识别错误的 Title 或 Episode，点击 "Save & Relink"，系统会自动修正链接。

## 📖 高级功能

### 监听模式 (Monitor Mode)
实时监控文件变动。

```bash
python3 src/pal.py -c config.yaml --monitor
```
*   **Monitor Src**: 当源目录有新文件或文件被修改时，自动加入处理队列（带防抖）。
*   **Monitor Dst**: (慎用) 当目标目录的链接被删除时，**自动删除源文件**。需在 config 中显式配置 `monitor_dst: true`。

### LLM 集成
如果文件名太复杂，GuessIt 搞不定，可以配置 LLM。

```yaml
providers:
  openai:
    type: "llm"
    api_key: "sk-..."
    base_url: "https://api.openai.com/v1"
    model: "gpt-3.5-turbo"

tasks:
  - src: "..."
    chain: ["guessit", "openai"] # 优先 GuessIt，失败回退到 OpenAI
```

## 📁 目录结构

*   `src/pal.py`: 核心入口
*   `src/web/`: Web 服务器及前端代码
*   `src/plugins/`: 格式处理插件 (Movie, TV, WebDL)
*   `src/database.py`: 数据库管理
*   `src/monitor.py`: 文件监听逻辑

## License
MIT
