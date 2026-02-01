# PAL (Parse and Link) 设计文档

## 1. 核心目标
支持灵活的视频文件扫描、元数据提取和链接生成，允许通过配置文件管理多个独立的同步任务，并支持不同的数据库隔离策略。

## 2. 配置文件结构
支持 YAML 格式配置文件，定义默认值和任务列表。

```yaml
# config.yaml
defaults:
  db: "global_pal.yaml" # 默认数据库路径
  soft_link: true       # 默认链接类型

tasks:
  - src: "/mnt/Disk1/Video/Movie"
    dst: "/mnt/Disk1/Links/Movie"
    type: "movie"       # 类型：movie, tv, webdl (不区分大小写)
    # 使用默认 db

  - src: "/mnt/Disk2/Anime"
    dst: "/mnt/Disk2/Links/Anime"
    type: "tv"
    db: "anime_db.yaml" # 指定独立数据库
    tv_folder: "Anime"  # 覆盖默认子目录名
```

## 3. 数据库设计
为了支持多任务共用数据库，同时保持对文件所属源目录的追踪，数据库条目增加 `source_root` 字段。

**Schema:**
```yaml
files:
  "/absolute/path/to/video.mp4":
    source_root: "/mnt/Disk1/Video/Movie" # 标记该文件属于哪个任务根目录
    metadata:
      title: "Video Title"
      year: "2024"
      ...
    target_path: "/mnt/Disk1/Links/Movie/Video Title (2024)/Video Title.mp4"
    metadata_hash: "sha256..."

error_files:
  - "/path/to/bad/file.mp4"
```

*   **`source_root` 的作用**: 允许程序只清理属于当前任务的失效链接，而不会误删同一数据库中其他任务的记录。

## 4. 架构调整

### 4.1 模块职责
*   **`src/pal.py`**: 入口脚本。负责读取配置、实例化数据库（支持多实例缓存）、调度 `run_task`。
*   **`src/database.py`**: 提供 `get_files_by_source_root` 方法，支持按源目录筛选记录。
*   **`src/plugins/base.py`**: `process_file` 更新，需知晓当前任务的 `source_root`，并在更新数据库时写入。

### 4.2 处理流程 (Per Task)
1.  **初始化**: 加载指定的 DB。
2.  **清理**:
    *   从 DB 中获取 `source_root` 等于当前 `task.src` 的所有记录。
    *   扫描磁盘上的 `task.src`。
    *   找出 DB 中存在但磁盘上不存在的文件，执行清理（删除链接、移除 DB 条目）。
3.  **处理**:
    *   遍历磁盘文件。
    *   调用插件 `process_file`。
    *   插件在保存数据时，将 `task.src` 作为 `source_root` 写入。

## 5. 类型定义
不再使用数字 `0, 1, 2`，改为字符串：
*   `"movie"` -> `MoviePlugin`
*   `"tv"` -> `TVPlugin`
*   `"webdl"` -> `WebDLPlugin`
