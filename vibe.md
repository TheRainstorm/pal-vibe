
介绍：我想要实现一个项目 pal(parse and link)，从杂乱的目录中扫描所有视频文件，然后从文件名中识别出它们的标题和其它元信息（重点在于不需要联网获取元信息），然后按照一定的链接规则（遵守jellyfin的目录格式），将视频文件链接到目标目录（支持软链接和硬链接）。从而实现 jellyfin 对目录下的电影和剧集完美的刮削效果。


核心方法：

- 扫描 src 目录，递归找出所有视频文件
- 按照指定的类型格式，扫描视频元信息
- 按照链接规则，将文件链接到 dst 目录

示例
```
# 将TV目录下的所有视频文件链接到links目录的剧集目录下(默认为TV)，识别类型为TV(-t 0)，使用软链接(-S)
python src/pal.py -s "/mnt/Disk2/BT/downloads/Video/TV" -d "/mnt/Disk2/BT/links/" -t 0 -S
python src/pal.py -s "/mnt/Disk2/BT/downloads/Video/Movie" -d "/mnt/Disk2/BT/links/" -t 1 -S
python src/pal.py -s "/mnt/Disk2/BT/downloads/Video/TV_anime" -d "/mnt/Disk2/BT/links/" --tv-folder "TV_anime" -t 0 -S
python src/pal.py -s "/mnt/Disk2/BT/downloads/Video/Movie_anime" -d "/mnt/Disk2/BT/links/" --movie-folder "Movie_anime" -t 1 -S
```

最后生成如下目录结构
```
├── links
│  ├── Movie
│  ├── Movie_anime
│  ├── TV
│  └── TV_anime
```


核心需求1：支持 Movie, TV 几种核心类型格式，从文件名中提取最关键的 title 信息，以及使用 ffmpeg 从文件中获取视频元信息。然后按照链接规则整理。

核心格式类型：

- Movie
    - 说明：电影。可能位于根目录，也可能位于一个电影目录下
    - 扫描元信息
        - title：从文件名中识别出电影名
        - year (optional)：文件名存在则添加，不存在时为空
        - ffmpeg 扫描的视频信息
            - 分辨率 width
            - 分辨率 height
            - 帧率
            - HDR
    - 链接规则
        - title (year)/title.mkv：普通的 1080p SDR
        - title (year)/title - version_str.mkv：4K HDR 等少部分格式需要添加版本信息
            - version_str：2160p, HDR 等不同属性拼接
- TV
    - 一个目录，下面有剧集文件，不同剧集位于同一个目录，文件名包含集数
    - 季信息位于文件名或者公共的目录中
    - 扫描元信息
        - title: 剧集系列标题
        - season：空时默认为 1
        - ep title (optional)：集标题
        - ffmpeg 扫描的视频信息
    - 链接规则
        - title (year)/Season 1/ep title-S01E01.mkv
- WebDL
    - 说明：一般的网络下载资源，当做movie 处理，只不过 tile 只能从文件名获取
    - 扫描元信息
        - title：从文件名中提取的尽可能可读的内容作为标题
        - ffmpeg
    - 链接规则
        - title/title.mkv

核心需求2：识别错误时，方便的手动编辑功能。维护一个 yaml 数据库，记录了每一个视频文件识别的元信息和当前链接的目标地址。

- 再次运行脚本时，如果扫描的视频文件已经存在，可以不重新扫描元数据。
- 每个视频记录一个元数据的hash 码，如果结果对不上，说明手动修改了。此时判断链接目标是否发生了改变，重新链接（并且还要清楚原本的链接目录）

核心需求3：扫描时，发现数据库中的源文件不存在时，把链接目录也删除


核心需求5：支持调用 openai 兼容的 API，使用 llm 从文件名中提取需要的元信息。

核心需求4：监听模式，用户配置一个 yaml 文件，包含若干 src, dst，类型，链接目录。程序监听 src 的变化，当有新视频文件时，脚本对该视频文件进行扫描。
推荐创建一个队列，src 目录可能持续变化，不断有文件增加和删除，等待 src 一段时间未变化后，将变化文件列表传递给脚本。
核心需求6：监听模式2,监听 dst 目录，如果 dst 目录删除了，则把源目录文件也删除。参考 /home/yfy/repo/pal-vibe/tmp/parse-and-link2/monitor.py 实现

监听模式已经集成完毕。

   1. `src/monitor.py`:
       * 实现了 MonitorManager，管理 watchdog 观察者。
       * BatchQueue 实现了防抖动（默认5秒）。
       * SrcHandler 监听源目录，触发 PROCESS 事件。
       * DstHandler 监听目标目录，触发 DELETE 事件（如果链接被删除，自动删除源文件）。
   2. `src/pal.py`:
       * 重构了任务解析逻辑，将“运行一次”和“监听”分开。
       * 添加了 --monitor 参数。
       * 在监听模式下，会将所有解析出的任务注册到 MonitorManager。

  现在，您可以通过以下命令启动监听模式：

   1 # 使用配置文件
   2 python3 src/pal.py -c config.yaml --monitor
   3
   4 # 或者单任务监听 (不推荐，但支持)
   5 python3 src/pal.py -s ... -d ... -t ... --monitor

fix1: 1）能不能把监听 src 和 监听 dst 分开成两个选项来单独启用。2）在配置文件中，可以对每个 task 控制是否需要监听 src 和 dst。

设计方案：

   1. 命令行参数:
       * 移除 --monitor（因为它太笼统）。
       * 新增 --monitor-src: 启用源目录监听（新增/修改）。
       * 新增 --monitor-dst: 启用目标目录监听（反向删除）。
       * 为了方便，如果只指定 --monitor，可以默认启用两者（或者只启用 src，更安全）。这里我建议：保留 --monitor 作为“全开”的快捷方式，或者将其设为等同于 --monitor-src。但为了明确性，最好是用新的参数。
       * 我们可以让 --monitor 作为一个基础开关，如果指定了，但没有指定子开关，默认行为是什么？
       * 更好方案：
           * --monitor: 开启监听模式。
           * --no-monitor-src: 禁用源监听。
           * --no-monitor-dst: 禁用目标监听。
           * 默认：如果开启 --monitor，默认两者都启用（遵循之前的行为）。或者，我们可以更显式：--monitor 仅仅是开启监听循环，具体监听什么由 Config 或其他参数决定。

   2. 配置文件 (`config.yaml`):
      在每个 task 中增加控制字段：

   1     tasks:
   2       - src: "..."
   3         dst: "..."
   4         monitor_src: true  # 默认 true
   5         monitor_dst: false # 默认 false (为了安全，反向删除最好默认关闭)

   3. 优先级逻辑:
       * Task Config: 最细粒度的控制。
       * CLI Args: 全局覆盖。例如，如果 CLI 指定了 --monitor-src-only，则忽略 Config 中的 monitor_dst: true。
       * 为了简化，我们可以规定：CLI 参数决定是否进入监听模式，Config 决定具体监听哪些目录。



feat:

1. 识别错误时，将文件添加到数据库专门一个错误列表中，方便用户查看和修改。扫描时，跳过错误列表中的文件。
2. ffmpeg 只在第一次扫描时调用，后续只要文件名没有变化，就不再调用 ffmpeg，提高效率。
3. 目前脚本通过指定 -s, -d 将 src 链接到 dst，并且 -d 指定数据库。我希望通过 yaml 文件一次性配置多个 src, dst，type。并且所有“链接对”共用一个数据库。因此数据库应该要区分不同 src 的文件，否则会冲突。

有没有办法方便筛选出不同 src 的部分呢？之后可能需要支持处理：1）单个文件。2）文件列表。3）某个src 目录这三个层级。因为不同 src 的处理是完全独立的，是不是分开管理更好？
并且有没有办法支持一些 src 指定自己的 db 文件，一些 src 共用一个 db 文件？这部分你再好好考虑一下怎么设计

4. 增加一个模式：先使用 guessit 分析，分析失败时使用 llm 分析。该模式实现复杂吗
是不是可以增加灵活性，定义一个处理链，这样所有模式都可以用处理链统一标识
需要兼容旧参。2）llm 表示一种方法，能不能支持多个 llm，每个都可以定义 base, apikey,model
5. 现在打印的信息太少了，使用日志打印，并且支持不同日志级别。补充一些必要的日志打印

fix:

1. 现在 metadata 类是所有媒体格式共同使用，但是不同格式可能有不一样的元信息。我希望提取元信息，和最后使用元信息生成链接是低耦合的，用户可以轻松实现不同格式插件。
2. 不要所有东西都放在一个文件里，按功能拆分
3. hash 比较逻辑。我修改了 src/base.py 的 process_file 逻辑，你总结下现在的处理流程。我之前的需求是，用户可以手动修正 yaml 格式的数据库，重新运行脚本时，会自动检测是否修改。修改了的话大概率要重新链接，还要删除原本的链接目录。这下你明白我意思了吗

  现在的逻辑非常精准地实现了您的需求：以数据库为准。

   * 只要数据库里有记录，并且哈希校验通过（说明没被改），就直接信任数据库，跳过所有扫描。
   * 只要用户改了数据库（导致计算出的哈希与存储的哈希不一致），程序就能检测到，并直接使用用户修改后的数据来重新整理链接，同时清理旧链接。
   * 只有全新的文件才会走完整的扫描流程。

  这正是您想要的“智能检测修改”和“自动修复链接”的功能。

4. 识别 TV 是，title 和 epsiode 是必要的，否则看作识别失败。另外如果链接路径时发现一样的路径已经存在了，则也应该判定为errorfile。你的errorfile 里面最好也添加上错误原因。是缺失了什么信息或者重复导致的  
5. 数据库设计：现在识别错误的文件，仅把文件名添加到 error_files 中。1）能不能也保留已经识别出的元信息，这样我手动修改时就可以仅填充信息了。2）并且我也不想手动复制到files 里，能不能直接在 files 里保留一份错误文件的元信息，这样，我修改后，脚本识别到它的信息是完全的，就可以自动把它从 error_files 里删除，并且重新链接。
6. 我希望无论如何数据库中都使用相对路径，视频文件使用相对 src 的相对路径，target_path 使用相对 dst 的相对路径
7. 多了太多正则匹配的日志了，另外除了 -v，没办法显示指定 logger 级别吗？
8. tv_folder 和 movie_folder 参数，统一为一个参数，每个格式类型都可以指定自己的 folder 名称



进阶需求1：支持 web ui。

- 有一个界面可以看到当前统计信息：各个 src 目录视频文件数，dst 目录视频文件数，识别错误文件数
    - 一个按钮手动触发各个 src 的重新扫描链接
- 一个界面按照文件树展示所有源视频文件（不展示其它无关文件），点击后可以查看和编辑元信息
- 一个界面按照文件树展示所有dst 视频文件，点击后可以查看和编辑元信息
    - 修改完后，触发单个视频的扫描链接


fix:

1. pal.py 重复了 __main__ 部分，导致参数选项丢失，我已修复
2. base.py:process_file:127-129 会判断 db_entry.get("source_root") 是否等于当前 source_roo太，但是实际 db_entry.get("source_root") 不存在，导致反复 Updating source_root for {filepath}，我我删除了该部分
3. server.py:update_metadata 中错误的更新了数据库为新 hash，导致 plugin.process_file 中判断 hash 不一致时，清理旧的连接目录功能失效，我已修复。

进阶需求2：支持读取 jellyfin 刮削出来的 nfo 和海报图片。在 webui 中展示一个类似的媒体库，点击对应电影，可以展示扫描的元信息，并支持修改。


## stage2

1. 现在已经又很多功能了，能不能写一个 readme.md 介绍整个项目，并提供一个 quick start 指南