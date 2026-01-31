
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

核心需求4：监听模式，用户配置一个 yaml 文件，包含若干 src, dst，类型，链接目录。程序监听 src 的变化，当有新视频文件时，脚本对该视频文件进行扫描。
推荐创建一个队列，src 目录可能持续变化，不断有文件增加和删除，等待 src 一段时间未变化后，将变化文件列表传递给脚本。

核心需求5：支持调用 openai 兼容的 API，使用 llm 从文件名中提取需要的元信息。

进阶需求1：支持 web ui。

- 有一个界面可以看到当前统计信息：各个 src 目录视频文件数，dst 目录视频文件数，识别错误文件数
    - 一个按钮手动触发各个 src 的重新扫描链接
- 一个界面按照文件树展示所有源视频文件（不展示其它无关文件），点击后可以查看和编辑元信息
- 一个界面按照文件树展示所有dst 视频文件，点击后可以查看和编辑元信息
    - 修改完后，触发单个视频的扫描链接

进阶需求2：支持读取 jellyfin 刮削出来的 nfo 和海报图片。在 webui 中展示一个类似的媒体库，点击对应电影，可以展示扫描的元信息，并支持修改。

