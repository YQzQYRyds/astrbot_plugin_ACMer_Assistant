# ACMer 全网算法赛历

AstrBot 算法竞赛插件：Codeforces、AtCoder、牛客、LeetCode 中国站、洛谷公开赛历，支持主动查询、多时间点提醒和每日汇总。所有网络调用、后台调度与数据库操作均为异步；HTML 解析在线程执行。

## 安装与首次启用

要求 Python 3.10+、AstrBot 4.9.2+（4.x）。按当前官方 `Star.initialize()` / `terminate()` 生命周期实现；不依赖旧的 `register` 装饰器。

1. 将压缩包中的文件解压到 AstrBot 的 `data/plugins/astrbot_plugin_acmer_assistant/`，确保该目录直接包含 `main.py`、`metadata.yaml`、`_conf_schema.json`。
2. 使用 **AstrBot 自己的 Python 环境**安装 `requirements.txt`，然后在 WebUI 重载插件。平台支持上传插件 ZIP 时也可以上传 `dist/astrbot_plugin_acmer_assistant.zip`。
3. 打开插件配置，在 **启用的会话 ID** 中添加群聊或私聊，保存并重载即可启用。
4. 在已启用的聊天里发送 `/比赛` 或 `/赛历`。默认展示未来 7 天内最近 10 项比赛。
5. 取消时移除相应会话并重载；空列表表示全部停用。无需绑定或解绑。

## 会话 ID 怎么填写

同一列表 `enabled_session_ids` 同时控制指令和自动推送：

| 填写方式 | 含义 |
|---|---|
| `group:123456789` | 启用该群聊 |
| `private:987654321` | 启用与该用户的私聊 |
| `123456789` | 兼容旧版纯群号，相当于 `group:123456789` |
| `平台实例ID:GroupMessage:群号` | 精确指定某个平台实例上的群聊 |
| `平台实例ID:FriendMessage:用户ID` | 精确指定某个平台实例上的私聊 |

完整 ID 中的平台实例 ID 是 AstrBot 中实际配置的机器人实例 ID，不一定是 `qq` 或 `telegram`；请使用实际值。`private:` 与 `group:` 显式区分用户和群，避免相同数字串误启用另一类聊天。私聊使用 `FriendMessage`，不要写 `PrivateMessage`。未列入名单的会话不响应本插件指令，也不自动推送。管理员指令仍需 AstrBot 管理员权限。

QQ / OneBot v11（NapCat、Lagrange 等）自动查询已连接机器人的群列表、好友列表，因此已有群和好友不需要先发消息或绑定。临时会话、非好友私聊取决于协议端能力，可使用完整会话 ID；其他平台推荐直接填写完整会话 ID，或让已启用的会话发一条普通消息以自动识别路由。

同一简写会话在多个机器人下可达时按稳定的平台 ID 选择一个推送。完整会话 ID 可以指定机器人实例。同一个实际目标以简写和完整 ID 重复填写时自动去重。平台必须支持主动发送，QQ 官方接口等仍受其能力限制。

## 指令

| 指令 | 权限 | 用途 |
|---|---|---|
| `/赛历`、`/近期比赛`、`/比赛` | 所有人 | 查询未来数日；到同步间隔才请求刷新 |
| `/刷新赛历` | AstrBot 管理员 | 强制刷新全部已启用数据源 |
| `/赛历状态` | AstrBot 管理员 | 查看更新时间、失败源、目标数和异常通知 ID |
| `/处理赛历通知 记录ID retry` | AstrBot 管理员 | 允许重试异常记录，仍须处于有效窗口 |
| `/处理赛历通知 记录ID sent` | AstrBot 管理员 | 人工确认已发送，停止重试 |

所有指令仅在启用的会话生效；管理员指令也遵守该限制。指令前缀以 AstrBot 配置为准。

## 配置面板

`_conf_schema.json` 是默认值的唯一来源。配置保存后重载插件生效，不需要修改 Python 文件。

| 配置 | 默认值 | 说明 |
|---|---|---|
| `enabled_session_ids` | `[]` | 群聊、私聊通用启用列表，支持上面的简写和完整 ID |
| `advance_notice_minutes` | `[60,30]` | 正整数分钟，允许整数字符串；空数组关闭赛前提醒 |
| `daily_push_enabled` | `true` | 日报开关 |
| `daily_push_time` | `08:30` | 配置时区的本地时间，严格 HH:MM |
| `digest_days` | `7` | 包含今天的日历日数量，1–30 |
| `digest_max_contests` | `10` | 最多显示 1–100 项；按时间取最近的，CF 合并分组算一项 |
| `enable_codeforces` | `true` | 启用 Codeforces |
| `enable_atcoder` | `true` | 启用 AtCoder |
| `enable_nowcoder` | `true` | 启用牛客 |
| `enable_leetcode` | `true` | 启用 LeetCode |
| `enable_luogu` | `true` | 启用洛谷 |
| `sync_interval_minutes` | `30` | 后台同步间隔 |
| `timezone` | `Asia/Shanghai` | 展示、分组、日报时区，也可设为 `UTC` |
| `scheduler_tick_seconds` | `20` | 提醒检查周期，发送精度受此周期及网络耗时影响 |
| `reminder_catchup_minutes` | `120` | 单个提醒节点允许迟到的分钟数 |
| `daily_catchup_minutes` | `180` | 当日日报允许迟到的分钟数 |
| `cache_max_age_hours` | `24` | 超期缓存仍供查询并标注，不再自动推送 |
| `request_timeout_seconds` | `20` | HTTP 单次请求超时 |
| `http_retries` | `2` | 失败后的额外重试次数 |
| `retry_base_seconds` | `2` | 指数退避基数，附加随机抖动 |
| `trust_env_proxy` | `false` | 是否使用 HTTP_PROXY / HTTPS_PROXY |
| `max_response_bytes` | `8000000` | HTTP 响应大小上限 |
| `send_timeout_seconds` | `30` | 单条发送超时，还受该消息有效期约束 |
| `send_retry_seconds` | `60` | 明确失败/允许重试后的等待时间 |
| `send_max_attempts` | `5` | 单条发送最大尝试数 |
| `uncertain_delivery_policy` | `hold` | 发送结果不确定：`hold` 暂停人工处置，`retry` 自动重试 |
| `message_max_chars` | `1800` | 日报/查询/状态的分段字符上限 |
| `state_retention_days` | `45` | 消息有效期结束后保留记录的天数 |
| `luogu_max_pages` | `3` | 洛谷列表请求最大页数 |
| `*_url` | 各平台官网地址 | 自定义源地址，返回格式须与对应解析器兼容 |

时区通过 `zoneinfo` 转换，`tzdata` 确保 Windows 也有时区数据库。时间均保存为 UTC Unix 秒，牛客毫秒会统一转换。无密钥、无账号、无第三方聚合站依赖。

## 赛历展示

查询和日报共用同一排版：日期之间留空行，日期含中文星期，比赛附平台标签、时间、易读时长及直达链接；普通聊天中 URL 可直接点击，不依赖 Markdown 渲染。

```text
🏆 【近期算法赛事周报】

📅 09-25（周五）
• [牛客] 牛客挑战赛92
⏰ 19:00（时长 3小时）
🔗 https://ac.nowcoder.com/acm/contest/140237

📅 09-26（周六）
• [AtCoder] ABC 477
⏰ 20:00（时长 1小时40分）
🔗 https://atcoder.jp/contests/abc477

• [Codeforces] Round 1124
⏰ 22:35（时长 2小时30分）
🔗 Div.1: https://codeforces.com/contest/2268
🔗 Div.2: https://codeforces.com/contest/2269

💡 记得提前报名参赛，祝大家把把上分、轻松 AC！
```

上面是排版示例，不是实时赛事公告。超过一天的时长如 `10天/240h`；AtCoder 已知赛事系列显示为 `ABC 477`、`AHC 072` 等短名称。CF 仅在带数字的轮次名称、开始时间、时长完全匹配，且恰有 Div.1 与 Div.2 两组时合并，不会仅因同一时间开赛而误合并。先按日历日筛选，再按开赛时间排序、合并分组，最后应用数量上限；被省略的数量会显示在末尾。

数量上限只约束查询与日报展示，**不会裁剪缓存或阻止其他比赛的赛前提醒**。分页尽量保留完整比赛条目，新页重复日期标记。

## 数据源与边界

| 平台 | 获取方式 | 范围 |
|---|---|---|
| Codeforces | 官方 `contest.list?gym=false` | `BEFORE`，不含 Gym |
| AtCoder | 官方 `/contests/` 页面 | upcoming 表格 |
| 牛客 | 官方 VIP 赛历 HTML 内嵌 JSON | 页面发布的官方/VIP 列表，不包含全部用户自建赛 |
| LeetCode | 中国站 GraphQL `contestUpcomingContests` | 官方公布的近期比赛 |
| 洛谷 | 官方列表 Lentille content-only JSON | 默认 `type=SCP` 精选比赛，最多三页；可改 URL / 页数 |

“全网”指多平台聚合，不保证覆盖所有私人比赛、全部历史页面或尚未公开的比赛。五个解析器相互隔离，某个平台失败不会清空其缓存或阻塞其他源；成功快照会替换该平台旧赛历，比赛撤销不会一直留存。HTML/非公开稳定协议可能改变；结构异常视为源失败，不会伪装成“没有比赛”。单条必需字段损坏时保留整个平台旧快照，以免静默丢赛。

上游成功返回空列表会清空该源赛历。牛客页面完全缺少比赛节点时无法区分空列表与页面变更，因此保守保留缓存并提示异常。

## 提醒与持久化语义

数据存储于 `data/plugin_data/astrbot_plugin_acmer_assistant/calendar.sqlite3`，不放在插件代码目录。SQLite 使用 WAL、FULL 同步及事务，表包括 `snapshots`、`group_routes`、`notified_events`。备份时停用插件后复制数据库，或使用 SQLite 备份接口；运行中的 WAL 文件不能随意丢弃。同一数据目录只运行一个插件实例，不支持多个 AstrBot 进程共享此数据库。

- 赛前唯一键包含平台、比赛 ID、开始时间、提醒阈值和目标会话摘要；改期开赛后会生成新的提醒。
- 同一比赛在 60、30 分钟节点各提醒一次。若重启时仅剩 20 分钟，只补最近的 30 分钟节点，不连发两条；超出补发窗口或已经开赛不会补。
- 每个目标单独标记成功。明确未找到发送平台（返回 `False`）会按间隔重试；重试耗尽进入 `failed`。
- 日报按本地日期和目标防重，不补往日日报。日报分页在事务内一次保存，已成功的页不重发，重试使用首次生成的固定内容。
- 当前全部平台无可信快照时暂缓日报；部分失败时照常推送可用部分并附带缺失/缓存说明。
- 超时、发送异常以及进程在发送途中终止，均可能处于“已发送但没有可靠确认”的状态。默认 `hold` 会保留为 `uncertain`，由管理员查看状态后处理；选 `retry` 则优先自动补发，但可能重复。

**不能承诺跨消息平台的严格 exactly-once。** SQLite 提交与外部消息投递不在同一事务中，AstrBot 的发送接口也未提供通用幂等键。正常轮询、已确认发送后的重启能防重；断电窗口要在“可能重复”和“可能漏发”之间选择。网络离线超过有效窗口、平台尚未发布赛程、数据源过期也可能无法提醒。`sent` 表示适配器调用成功，不代表用户已阅读。

日报固定分页的内容不会在重试中重排；修改平台白名单、展示天数或数量上限后，已生成的当日日报仍保持原内容，新日报和查询采用新配置。改动目标后会根据当前目标列表决定是否继续发送。

## 模块结构

```text
main.py                     AstrBot 指令、权限、生命周期、消息适配
_conf_schema.json            配置面板与默认值
calendar_core/
  config.py                 校验并生成配置快照
  models.py                 统一比赛对象
  http.py                   异步请求、超时、退避重试
  fetchers.py               五个平台的纯解析器和抓取入口
  service.py                并行同步、缓存、白名单和过期策略
  storage.py                SQLite 快照、自动识别路由、通知状态
  routing.py                自动发现群/好友列表、通用会话启用与路由
  migration.py              插件改名后安全复制旧数据库
  scheduler.py              多阈值提醒、日报、持久化发送队列
  formatting.py             时区展示、日期分组和长消息分段
tests/                      离线业务测试与 AstrBot 契约替身测试
scripts/smoke_sources.py     可选实时源检查，不发送群消息
scripts/package.py           生成安装 ZIP
```

## 开发验证

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements-dev.txt
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\ruff check .
.\.venv\Scripts\ruff format --check .
.\.venv\Scripts\python scripts/smoke_sources.py
.\.venv\Scripts\python scripts/package.py
```

联网检查可加 `--proxy-env` 使用环境代理。测试不依赖 AstrBot 完整安装，不发送真实消息；生命周期及权限测试使用当前 API 的替身。部署后需在真实 AstrBot 与目标聊天适配器完成一次发送联调。配置错误会阻止启动并给出明确字段提示；停用/重载会取消并等待三个后台任务，然后关闭 HTTP 会话和数据库。

## 官方依据

- [插件开发入口](https://docs.astrbot.app/dev/star/plugin-new.html)
- [配置面板](https://docs.astrbot.app/dev/star/guides/plugin-config.html)
- [主动消息](https://docs.astrbot.app/dev/star/guides/send-message.html)
- [插件存储](https://docs.astrbot.app/dev/star/guides/storage.html)
- [Star 生命周期源码](https://github.com/AstrBotDevs/AstrBot/blob/master/astrbot/core/star/base.py)
- [Codeforces API](https://codeforces.com/apiHelp/methods#contest.list)

MIT 许可。正式发布到插件市场前，将 metadata.yaml 的作者改为实际维护者，并补充真实的 `repo` 仓库链接。
"# astrbot_plugin_ACMer_Assistant" 

## v1.2.0 改名与升级

插件名称、安装目录名、ZIP 文件名统一为 **astrbot_plugin_acmer_assistant**。

1. 先停用旧的 `astrbot_plugin_acmer_calendar`，避免新旧两份同时推送。
2. 安装新版，在“启用的会话 ID”中填写原群号（可以直接沿用纯群号），按需添加 `private:用户ID`。
3. 用五个独立平台开关选择赛事来源。它们同时控制抓取、查询、日报筛选和赛前提醒。
4. 新版第一次启动、且新数据库不存在时，会通过 SQLite backup 将旧目录 `data/plugin_data/astrbot_plugin_acmer_calendar/calendar.sqlite3` 复制到新插件的数据目录。旧文件保留，新库存在时不覆盖，缓存与已通知状态均迁移。

AstrBot 按插件名分开保存配置。若需迁移旧配置文件，可在安装新版前运行包内工具：

```text
python scripts/migrate_config.py 旧配置.json 新配置.json
```

目标应是 AstrBot 的新插件配置路径（通常为 `data/config/astrbot_plugin_acmer_assistant_config.json`，以实际安装为准）。工具把旧 `target_groups` 转换为 `enabled_session_ids`，将旧 `enabled_platforms` 转为五个布尔开关，并保留其他配置；原文件不改，目标已存在时拒绝覆盖。若已安装并产生新配置，可直接在面板填写，或将转换结果保存到临时文件后核对应用。配置类也支持旧字段，但显式提供的新字段优先，包括空会话列表和关闭的平台开关。

不读取更早版本已经停用的 `target_sessions` 字段。历史手动绑定表保留但不使用。
