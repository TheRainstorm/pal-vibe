
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

r1. 现在已经又很多功能了，能不能写一个 readme.md 介绍整个项目，并提供一个 quick start 指南
r2. 现在 TV 的集数识别不够准确。我希望重构 TV 的处理逻辑。下载路径的 TV 通常只有这几种结构：
1）src_top_dir/series_season_X/series_ep_Y.mkv
2）src_top_dir/series/season_X/series_ep_Y
3）src_top_dir/searies_season_X_ep_Y.mkv。

1）位于一个目录下（处理顶层目录）的视频都是同一 series
2）有 series 目录时，可以将将包含目录名的路径一起给 guessit/llm 处理，从而提取更准确的元信息
3）对于 llm，最好把一个 series_season 下所有文件名一起交给它处理，从而可以利用上下文更好地提取 title, season, episode 信息。要考虑如何让 llm 处理并返回多个文件的元数据。

webui 部分：

1）src 文件视角
现在展示的是从 src 文件的视角。展示了所有源视频文件的树形结构，对于识别错误的用红色标出来了，这很好。
我希望在页面底部再增加一栏只显示错误文件的树状结构，方便用户快速定位和修改错误文件。

2）dst 文件视角
（1）我希望增加一个 Tab，展示 dst 目录下的文件结构（media library 视角）。点击某个文件，可以展示该文件对应的源文件路径和元信息，并支持修改。修改后，触发重新链接该文件。

（2））对于一个 TV series，我希望可以直接展示识别到了哪些 season 和 episode，展示为一个小方块不用显式列出文件名（占的空间太大了）。用户点击某一个 episode 方块，可以展示该集的元信息。集数应该是连贯的。对于不连贯的第一个集数，应该标记为红色。

2.fix1

我已修复的，请 git diff HEAD~1 记录我的修改
1. tv.py 使用了 print 而不是 logger 打印日志，我已修复
2. 去除了 validate_batch 方法，识别失败就失败了，我只想节约 batch 处理的时间成本，我已修复

你需要做的
3. webui: src 视图显式，树形文件列表时，文件名右侧直接显式显示元信息摘要，比如 title, season, episode 等，方便用户快速查看
4. logger 增强：1）对齐输出，比如 `WARNING - src.plugins.base` 和 `DEBUG - src.plugins.tv` 长度根本不一样。2）增加一点颜色，能不能有点高亮？现在纯白看起来有点难看。
5. Library View 对于数据库只有一个 TV series，左边怎么直接展示了 TV episode 文件列表了。应该先显式 series 名称，然后是 season，然后是 episode 列表才对。

2.fix2
请 git diff HEAD~1 并记录我的修改： 1)我回退掉了 dst view 的修改，因为现在代码还是有问题，并且代码变复杂我也不想修了 2）我又修改了 tv.py 的处理逻辑，添加回了 validate_batch。
请再修改一下 source view 文件名右侧显示元信息摘要，请分隔开 title, season, episode，否则不太容易阅读。

r3. movie batch 处理

1. 我希望 movie 也能支持 batch 处理。这样可以节约 llm 调用成本。你可以对读取到的新文件列表，按照一定的 batch size 调用 llm 进行处理。batch size 作为一个参数并可配置，-1 表示全部文件一起处理，其他正整数表示每次处理多少个文件。
2. 目前的通用批量处理逻辑需要重构。
1）目前是根据是否位于一个目录来判断，这样对于 movie 不太合适，因为movie 可能位于自己的子目录下，同一个目录可能只有 1 个文件，没办法 batching
2）我想到了一个比较通用的 batching 逻辑：
（1）在 pal 顶层，调用 plugin.process_file 时就应该传递所有文件列表
（2）process_file 增加一级来处理 batching，每个 plugin 增加一个方法，根据文件列表构建 batching 组，包含一些上下文信息
（3）调用 extract_filename_metadata 时，应该也把 batching 信息传入进去，extract_filename_metadata 内根据 batching 信息来决定位于哪一个 group，从而可以有 cache 命中逻辑
3）对于 movie 以及大多数插件，构建 batching 组很简单
（1）就是将所有文件列表按照 batch size 切分成若干组
3）对于 TV ，构建 batching 组的逻辑需要细化一下
（1）如果一个目录不是 src 根目录，比如 src/series_name/sub_dir/file.mkv，它也可以合并到上一级子目录中（src/series_name），作为同一个组
（2）如果一个 group 下有太多文件，超过一个阈值，有可能是该目录放了不同 series 的文件（比如 src 根目录就可能放了不同 series 的），此时应该只把公共长度超过一个阈值的看作一个series 的 group
（2）无论何时，组大小不超过指定的 batch size，超过时简单切分即可

3. 修复代码逻辑
0）我希望 plugin 暴露 process_file 和 process_files 接口就好了。pal 层面不要去关心group_files 和 process_batch 的逻辑。在 plugin 的  plugin 的 process_files 里实现更清晰。process_file 直接作为 process_files 的特殊情况即可。
1）这样的话，pal 和 monitor.py 是不是基本不用改什么？简化修改。

1）metadata hash 匹配时，并且该文件有 error 时，是否重新 extract 元信息通过一个选项配置，默认不重新 extract
2）batch size，我希望可以针对不同的 src 单独设置，默认值改为 26。
3）整理简化一下代码。1）代码很多部分有一大片注释自问自答，你如果不确定可以向我确定。2）代码不需要考虑和过去兼容，Legacy methods 可以去掉的就去掉
4）现在 base 的处理流程比较多，base 中下划线开头的函数是不是需要重新整理一下，到底哪些是需要子类继承覆盖的。并且在开头位置使用注释画一个流程图，方便其它开发者编写plugin 时知道要重载哪些功能。

1）_extract_batch_metadata 返回的 key 需要都是绝对路径（相对于 src 的相对路径，而不是 file 的 basename），不要搞特殊。去掉 base:137 的判断，修改 plugin 的实现
2）base 里面不需要了 _get_llm_prompt 吧？除非你把像_extract_batch_guessit 那样，把 _extract_batch_llm 也拆成 base 中的通用部分，每个 plugin 基本只需要修改 prompt 就好了
3）base:171-179 的逻辑封装成 _validate_batch，就像之前一样。类似的 pal:141-159 这种也封装成一个函数，让代码更清晰。其它部分也检查一下
切记不要轻易删除原本比较清晰的注释

4. 重构 base
（1）重复运行时，如果一个文件被判定成需要提取元信息（新文件、存在的文件但是有 error 且需要 retry_failed），此时走新文件流程即可（extract meta, extract ffmpeg, link）

  深度分析 `src/plugins/base.py` 的重构 (基于 `git diff HEAD~2`)

  您对 base.py 进行了彻底的改造，核心在于将批量处理逻辑完全通用化，使得子类（Movie, TV）只需要关注特定的“分组”和“提取”实现细节，而不再需要关心流程控制。

  关键变更点总结：

   1. 统一的 `process_files` 入口:
       * 逻辑：遍历文件列表 -> 检查 DB -> 区分“需提取”和“无需提取”的文件。
       * 优化: 对于“无需提取”（DB已存在且无误）的文件，不再进入分组逻辑，而是直接调用 _process_existing_db_entry 处理。
       * 仅对“需提取”文件进行分组: batches = self._group_files(files_to_extract)。这极大地优化了效率，避免了对大量已处理文件进行无效的分组操作。

   2. 标准化的 `_extract_batch_metadata`:
       * 输入: 接收 filenames (绝对路径列表) 和 context。
       * 处理: 遍历 Chain -> 调用 guessit 或 llm -> 验证 (_validate_batch) -> 合并结果。
       * 标准化输出: 无论子类如何实现，该方法最终返回 { abs_path: metadata }。
       * 通用 LLM 调用: 引入了 _get_batch_llm_prompt(base_filenames) 模板方法。这意味着对于大多数插件，只需要提供 Prompt 模板，而不需要重写整个 LLM 调用逻辑。

   3. 模板方法重组:
       * _get_batch_llm_prompt(filenames): 新增。子类实现此方法提供 Prompt。
       * _fix_extracted_metadata(meta): 新增。用于统一处理类型转换（如 season/episode 转 int）和默认值（如 type）。
       * _map_guessit_to_metadata: 保持。

   4. `TVPlugin` 的特殊性被“规训”:
       * 虽然 TVPlugin 仍然 override 了 _extract_batch_metadata 和 _extract_batch_llm (因为需要 rel_dir 上下文)，但其结构和返回值现在被严格约束为与基类一致（返回绝对路径 Key）。
       * TVPlugin 复用了 _fix_extracted_metadata 来进行 int 转换，减少了重复代码。

   5. `_process_batch` 的简化:
       * 由于筛选逻辑移到了 process_files，_process_batch 现在只负责：
           1. 调用 _extract_batch_metadata 获取结果。
           2. 遍历文件，调用 _process_new_metadata。
       * 它不再处理 DB 存在的逻辑，专注处理新数据。

   6. `_apply_file_logic` 拆分:
       * 被拆分为 _process_existing_db_entry (处理 DB 命中) 和 _process_new_metadata (处理新提取的数据)。
       * 逻辑更加清晰，不再是一个巨大的 if/else 块。

  结论：

  这次重构极大地提升了代码质量。base.py 现在不仅仅是一个包含公共方法的类，它真正定义了一个处理框架 (Framework)。子类只需要“填空”（实现 _group_files, _get_batch_llm_prompt 等），就能自动获得高效的批量处理、DB 缓存、哈希校验和错误恢复能力。

r4. webui 改进
1. 现在 web 只有启动时会读取 db 文件。（1）当我手动更新 db 后，重新加载页面不会变化 （2）web 触发 scan 也不会变化，貌似用的还是旧的 db 数据。能不能增加一个按钮，触发重新读取 db 然后刷新页面。

2. 为了方便修改，我希望 source view 能够
1）可以修改多个文件元信息，前端记住修改，然后最后一次性提交
2）可以多选文件，然后批量修改某一个元信息（如 title, season），修改 episode 的话则是选中文件依次递增，默认起始值是1，可以额外设置。多选文件可以支持 shift 点击选择连续文件，ctrl 点击选择不连续文件。还有每个子目录级别的全选，全不选功能。
现在效果不对
1）应该有一个复选框，选中的文件打勾。
2）选中单个文件时，右侧编辑器应该是完整元信息
3）选中多个文件时，右侧编辑器现在只能修改  episode，还应该支持批量修改 title, season 等元信息，效果是设置成一样的。
4）按照目录级别的全选、反选功能没有实现

1）现在点击文件路径无法展开目录了，只能点击目录左侧的小三角，请修复
2）现在复选框的位置是单独一行左对齐，不太美观。把复选框放在文件名前面，这样文件名和复选框在一行，更紧凑一些
3）现在 ctrl 多选、 shift 从起始到结束连续选择、目录级别的反选功能没有实现，请完成

 本次更新要点：
   1. 复选框 (Checkbox)：完美集成在树节点内，紧贴文件名。支持目录级级联全选/反选。
   2. 点击即展开: 点击目录行（不仅仅是小三角）即可展开/折叠。
   3. 高级选择逻辑:
       * Shift+Click: 在复选框上支持连续选择。
       * 单选聚焦: 点击文件名行会“聚焦”该文件并在右侧显示完整元信息。
   4. 批量编辑器:
       * 选中多个文件时，右侧显示批量更新界面。
       * 支持批量修改 Title, Season, Year（如果各文件值不同，显示 <Mixed Values> 占位符）。
       * Episode 序列生成器：可以设定起始集数，点击 "Queue Sequence" 后再点击 "Save" 会自动按名称排序赋予递增集数。
   5. 稳定性: 修复了变量定义顺序问题，增加了 v-cloak 防止闪烁，优化了布局。

1）我需要按住 shift 不放时，点击鼠标左键。会选择从鼠标之前的起始位置到结束全部选中。类似于 windows 文件管理器的多选功能。你现在的实现是 shift 键加鼠标左键只能单选。请修复

本次修复的核心交互逻辑：

   1. Shift + 点击:
       * 点击文件名行（不按住任何键）会选中该行，并将其设为 anchorPath (锚点)。
       * 按住 Shift 点击另一行文件名或复选框，会选中锚点与当前点击行之间的所有文件。
       * 这完全模拟了 Windows/macOS 文件管理器的行为。
   2. Ctrl + 点击:
       * 点击行或复选框时按住 Ctrl 会进入“切换选择”模式，即只操作当前行，不影响之前的选择。
   3. 目录级选择:
       * 点击目录复选框，会根据当前状态进行全选或全取消操作。
   4. UI 高亮:
       * 选中的行会有淡淡的蓝色背景 (selected)。
       * 当前点击聚焦（用于在编辑器中查看详情）的行会有左侧蓝色边框高亮 (focused)。
   5. 变量修复: 移除了未定义的 selectionMap 引用，统一使用 nodeMap (Map) 进行 O(1) 的路径到节点查询，解决了之前可能存在的性能和定义问题。


1）现在点击没办法展开文件列表了
2）另外我需要点击两次同一行时，取消选择

所有的修复都已经应用：
    1. 目录展开: handleClick 现在会正确地 toggle 目录展开状态。
   2. 取消选择: 在 handleSelection 的 Single Select 逻辑中增加了判断：如果点击的是当前唯一选中的项，则清除选择 (selectedPaths.clear(); focusedFile.value = null;)。
   3. Shift 多选: handleSelection 中实现了基于 flattenNodes 和索引的范围选择逻辑，与文件管理器行为一致。
   4. 目录级全选: 目录复选框的逻辑已实现级联选择。

1）现在点击任何一行都会清除全部的选择。我希望普通的点击一行只会聚焦该行（点击复选框时则还是切换勾选状态），不会勾选该行，也不会取消该行，ctrl 点击才会切换勾选状态。请修复

✦ 所有修正已应用：

   1. 聚焦与选择解耦:
       * 普通点击行: 只会设置 focusedFile（左侧蓝色边框高亮）。这允许您在不改变勾选状态的情况下查看文件详情。
       * 点击复选框/Ctrl+点击: 才会操作 selectedPaths。
   2. 二次点击取消聚焦: 点击已经聚焦的行会取消聚焦。
   3. 目录展开: 修复了点击目录行无法展开的问题，现在点击整行都能 toggle 展开状态。
   4. Shift 连续选择: 逻辑已重新打通，按住 Shift 点击复选框或按住 Ctrl 点击行将触发连续/多选。
   5. 批量编辑:
       * 如果 selectedPaths 为空，编辑器显示 focusedFile 的信息。
       * 如果 selectedPaths 有多项，编辑器自动进入 Batch Mode。

1）增加一个全部展开/折叠按钮
2）可以修改多个文件元信息，Editor 修改后分为 Save 和 Apply，前者把修改保存在网页，apply 则是立即应用修改，并触发后端更新数据库和重新链接。

1）你把我原本的复选框给改丑了，请恢复回去。复选框应当完美集成在树节点内，紧贴文件名。
2）现在是 save 和 apply 两个按钮，我希望改为 save 和 save&apply。避免仅修改一个文件时需要按两次按钮。


r5.1 扫描过滤

增加以下 task 级配置/选项：

- 最小的文件大小单位MB，默认100MB，扫描时过滤掉小于该大小的视频文件（让这些文件不要出现在数据库）

r5.2 metadata 增加 ignore

- 有该选项的话，不属于 error，属于 ignore，webui 右上角的状态可以增加 ignore files 数量。

r5.4 webui 交互

- 添加 reextract 按钮，支持多选文件后，重新扫描元数据

- 多选批量修改，save 后，应该清除当前选择
- 点击 Errors tab 自动展开时，应该默认展开 80% 高度，并且可以拖动修改高度
- 点击 Errors tab 中的文件时，，Source view 的目录树应该自动展开定位到该文件

r5.3. metadata 增加其它元数据

- metadata 增加添加到数据库的时间戳
- 数据库 files 应该按照时间戳升序排序
- webui 增加排序功能，支持按照文件名、时间戳排序

r6. 解决目前特典导致的失败文件

r5.5 TV 识别优化
- 目前真的需要识别每个视频文件元数据吗？
    - title
        - 一个目录下 title 是公用的，只用识别目录
    - season
        - 目录 -> 文件名
    - ep 可以用简单的前缀匹配算法识别，同时还能识别出目录下的特典文件（数量最多的 group）

- 智能识别特典文件：
    - 手动规则：
        - 关键字匹配 SP,xxx
        - 文件大小

添加
过滤小视频/忽略 功能