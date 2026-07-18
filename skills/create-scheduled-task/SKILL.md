---
name: create-scheduled-task
description: "创建当前微信会话中的提醒或定时任务。当用户说“X 分钟后提醒我 Y”“X 分钟提醒我 Y”“过一会提醒我”“每天/每周/工作日几点提醒我”“帮我设个提醒”，或“创建一个……定时任务”时使用。支持一次性延时、每日、每周和中国法定工作日任务，也支持按时生成 AI 内容。"
---

# 创建定时任务

通过 `scripts/create_scheduled_task.py` 调用机器人客户端的定时任务接口。自动从当前会话环境变量推导创建人和发送目标，不要求用户提供微信 ID。

## 执行流程

1. 从用户原话中提取任务名称、执行规则和发送内容。
2. 缺少执行时间或发送内容时，只追问缺失项，不运行脚本。
3. 按下表选择调度类型和参数。
4. 在本 Skill 目录执行 `scripts/create_scheduled_task.py`，且不要在真实请求中传 `--dry-run`。
5. 仅在脚本返回 `ok: true` 后确认创建成功。回复任务名称、执行规则、下一次执行时间、提醒内容和发送位置；群聊任务同时说明触发时会自动 @ 创建人。
6. 脚本失败时立即向用户反馈原始错误含义，不得声称任务已创建。

## 调度规则

| 用户表达 | `--schedule-type` | 必需参数 |
| --- | --- | --- |
| “10 分钟后提醒我喝水”“10 分钟提醒我喝水” | `delay_once` | `--delay-minutes 10` |
| “2 小时后提醒我开会” | `delay_once` | `--delay-hours 2` |
| “明天 08:00 提醒我”且距离现在不超过 24 小时 | `delay_once` | `--run-at 'YYYY-MM-DD HH:mm'` |
| “每天 08:00 提醒我” | `daily` | `--time 08:00` |
| “每周一、周三 09:30 提醒我” | `weekly` | `--time 09:30 --weekdays '[1,3]'` |
| “每个中国法定工作日 09:00 提醒我” | `cn_workday` | `--time 09:00` |

遵守以下语义：

- 将星期一到星期日映射为 `1` 到 `7`。
- 将“工作日”解释为中国法定工作日，包括法定调休补班；用户明确说“周一到周五”时改用 `weekly` 和 `[1,2,3,4,5]`。
- 将缺少“后”但结构为“X 分钟提醒我 Y”的表达解释为 X 分钟后。
- 使用 Asia/Shanghai 时区。每日、每周和法定工作日时间必须是零补齐的 `HH:mm`。
- 中国法定工作日可用年份取决于客户端内置日历。当前配套客户端只包含 2026 年数据；创建 `cn_workday` 任务时提醒用户它在 2027 年前需要随客户端补充日历数据，否则跨年后无法继续计算下一次执行时间。
- 一次性延时必须在 1 秒到 24 小时之间。超过 24 小时的绝对一次性提醒不受后端支持。
- 后端不支持每月、每年、原始 Cron 表达式或“每隔 X 分钟”这类间隔循环。遇到这些请求时说明限制，并请用户改成支持的规则，不要伪造近似任务。
- “创建一个定时任务”但没有给出明确规则或内容时，先追问，不要猜测。

## 内容规则

- 对普通提醒使用 `--content`。默认把“提醒我喝水”整理为面向用户的 `提醒：喝水`，但用户给出精确文案时保持原文。
- 对“每天生成一份早报”这类动态内容使用 `--ai-prompt`，不要把普通固定提醒升级为 AI 任务。
- 可以同时传 `--content` 和 `--ai-prompt`；执行时先发送固定文本，再发送 AI 结果。
- 至少传 `--content` 或 `--ai-prompt` 之一。
- 任务名应简短、可识别，不超过 100 个字符；固定文本不超过 500 个字符。

## 会话目标

脚本自动读取以下环境变量：

- `ROBOT_FROM_WX_ID`：当前私聊好友 ID 或当前群聊 ID。
- `ROBOT_SENDER_WX_ID`：当前消息发送人 ID。
- `ROBOT_WECHAT_CLIENT_PORT`：机器人客户端端口。

私聊中把当前好友作为创建人和发送目标。群聊中把当前发言人作为创建人、当前群聊作为发送目标。因此，群聊里的“提醒我”会在当前群聊发送，并在任务触发后发送的第一条文本中自动 @ 创建人，不会私聊群成员。成功回复时明确说明这一点。不要让用户提供或猜测这些 ID。

## 脚本参数

```text
--name <任务名>                                  必填
--schedule-type <delay_once|daily|weekly|cn_workday> 必填
--content <固定提醒文本>                         与 --ai-prompt 至少一个
--ai-prompt <动态内容提示词>                     与 --content 至少一个
--time <HH:mm>                                   daily/weekly/cn_workday 必填
--weekdays <JSON数组或逗号列表>                  weekly 必填，1=周一，7=周日
--delay-seconds <整数>                           delay_once 四选一
--delay-minutes <整数>                           delay_once 四选一
--delay-hours <整数>                             delay_once 四选一
--run-at <YYYY-MM-DD HH:mm[:ss]>                 delay_once 四选一，须在未来 24 小时内
--dry-run                                        仅供开发校验，真实创建时禁止使用
```

## 调用示例

10 分钟后提醒：

```bash
python3 scripts/create_scheduled_task.py --name '喝水提醒' --schedule-type delay_once --delay-minutes 10 --content '提醒：喝水'
```

每周一和周三提醒：

```bash
python3 scripts/create_scheduled_task.py --name '周会提醒' --schedule-type weekly --time 09:30 --weekdays '[1,3]' --content '提醒：参加周会'
```

法定工作日生成动态内容：

```bash
python3 scripts/create_scheduled_task.py --name '工作日早报' --schedule-type cn_workday --time 08:30 --ai-prompt '生成今天的简短早报，直接给出可发送给用户的正文。'
```

调用 `execute_skill_script` 时，将以上命令中脚本路径之后的部分放入 `args`。

## 成功与失败处理

- 成功 JSON 包含 `task.id`、`task.schedule_summary`、`task.next_run_time` 和 `task.target_type`。使用这些实际返回值回复，不要只复述用户输入。
- 接口业务错误会以“创建定时任务失败：……”返回。说明可操作的原因，例如发送目标不存在、任务数量达到上限、AI 配置缺失或调度器未初始化。
- 创建接口不是幂等接口。发生超时、连接中断、无效响应，或错误中含“任务已保存，但刷新调度器失败”时，任务可能已经入库；明确告知用户先到定时任务列表核对，禁止自动重试，以免创建重复任务。
- 普通好友和普通群成员最多创建 5 个任务；群主、群管理员和后台管理员不受此配额限制。
