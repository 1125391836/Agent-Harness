# 研究代码

跑实验用的全部代码。只用 Python 标准库 + 一个 API key，不需要装任何依赖。

| 文件 | 作用 | 一段话 |
|---|---|---|
| `kg_gate.py` | L1 硬门控 | 41 条三元组的知识图谱 + 规则：谁是谁的供应商、哪张表标了 PII、生产环境要不要变更单 |
| `llm_preval.py` | L2 风险裁判 | 调大模型给动作打风险分，带磁盘缓存、token 统计、失败自动降级 |
| `console.py` | 交互判定台 | 在终端敲一个动作，看三层各自怎么判，最后给结论 |
| `prelim_study.py` | 批量实验 | 三个实验，生成 `../研究文档/初步研究结果.md` |
| `mvr/` | 早期骨架 | 更完整的运行时实现（世界模型、快照后端、6 种调度策略），暂时不用动 |
| `tools/dump_pptx.py` | 小工具 | 把 pptx 里的文字抽出来，读文献/PPT 时用 |

## 1. 密钥已经配好了

`研究代码/.env` 里写好了：

```
OPENAI_API_KEY=sk-...
OPENAI_BASE_URL=https://api.deepseek.com/v1
PREVAL_MODEL=deepseek-chat
```

`llm_preval.py` 启动时自动读它（往上找三层目录），**不用再配 PyCharm 环境变量**。
这个文件已被 `.gitignore` 忽略，但请不要发给别人。

换成本地 Ollama 不花钱：注释掉上面三行，换成

```
OPENAI_BASE_URL=http://localhost:11434/v1
PREVAL_MODEL=qwen2.5:7b
OPENAI_API_KEY=ollama
```

换别的云厂商同理，只改这三行，代码不用动。

## 2. 交互判定台

```powershell
cd 研究代码
python console.py
```

三种输入写法都认，**说人话就行**：

```
> 删除数据库
> 把 users 表里 500 条测试账号删了
> 给 vendor@external.com 转 120 元
> pay amount=120 to=vendor@external.com        ← 命令行写法，不调模型、不花钱
> {"tool": "deploy", "args": {"env": "prod"}}  ← JSON 写法，同样不调模型
```

自然语言走 `nlu.py`：模型把句子翻译成 `{"tool":..., "args":..., "confidence":..., "missing":[...]}`，
归一化参数（"生产环境"→`prod`、"两千"→`2000`）之后才进三层判定。

**一句话里有多个动作，会拆成计划逐步判定**：

```
> 删除数据库，并支付给小明 9999 元
计划 2 步：1) delete_records(参数未定) → 2) pay(amount=9999, to='小明')
———— 第 1/2 步 ————（各自走完三层）
———— 第 2/2 步 ————（裁判能看到第 1 步做过什么，风险判断带上下文）
```

某一步被判 DENY，后面的步骤直接作废（整条计划中止），不会"拦了一半继续跑"。

**名字会先落到真实地址上**（`directory.py`）：`小明 → xiaoming@company.com`、
`公司账户 → finance@company.com`。查不到才当"未知收款方"，不在判定层瞎猜。

**做不到的事会明说**：清单里没有的能力（比如"订机票"没注册 `book_flight` 时）
会进 `unsupported`，打印「做不到 ……」——不硬塞给某个工具，也不悄悄丢掉。

**判定之后会真的执行**（`sandbox.py`，模拟沙箱）：

| 决策 | 行为 |
|---|---|
| ALLOW | 立刻执行，打印副作用 |
| CONFIRM | 挂起等人点头，输入 `:yes` 才执行 |
| DENY | 不执行，且整条计划中止 |

`:effects` 看沙箱里已发生的副作用与可逆性，重复动作会被标出来
（"与第 1 条后果完全相同——真实系统里这就是重复操作"）。

**解析不确定会被当成风险**，这是故意的：

| 情况 | 处理 |
|---|---|
| 缺必填参数（「删除数据库」没说哪张表） | 风险抬到 0.72，按最坏情况判，并提示补什么 |
| 置信度 < 0.55（「处理一下那个订单」） | 风险抬到 0.55，倾向 FORK / CONFIRM |
| 完全对不上任何工具 | 回「没听懂」，不硬猜 |

道理很直白：模型听错了和动作本身危险是两件事，但后果都不可逆。

输出固定四段，对应论文的三层：

```
[1] 硬门控（知识图谱）   结论 + 命中规则 + 依据三元组
[2] 风险评分（PreVal）   risk + 是否不可逆 + 理由 + 依据
[3] 调度决策            预算够不够、分支成本、ALLOW/FORK/CONFIRM/DENY
[4] 结论                一句话
```

命令：`:demo` 跑 6 个预置场景，`:facts` 看整个图谱，`:kg org:evil` 看一个实体，
`:judge heur` 临时关掉大模型，`:stat` 看花了多少 token，`:quit` 退出。

## 3. 批量实验

```powershell
python prelim_study.py --quick              # 5 秒，启发式裁判
python prelim_study.py --llm                # 大模型裁判（当前配置，约 45 秒）
python prelim_study.py --gate kg --llm      # 换成知识图谱门控
```

报告写到 `../研究文档/初步研究结果.md`（每次覆盖，想留档先复制一份）。

## 4. 花费（实测，不是估算）

- 单次判定：约 500 输入 + 60 输出 token ≈ **¥0.0005**
- 跑完整套实验（12 组种子 × 10 任务 × 6 策略）：只触发 **6 次**真实调用，
  其余 4938 次命中磁盘缓存，合计 **¥0.0032**
- 缓存位置 `.cache/preval/`，删掉就强制重新判

也就是说，把实验反复跑到满意，成本可以忽略。

## 5. 当前结果（12 组种子，`--llm`）

| 指标 | 值 |
|---|---|
| 关键词门控 拦下危险动作 | 20.0% |
| 知识图谱门控 拦下危险动作 | 80.0% |
| 仅门控 + 大模型裁判，DIR | 82.5%（成功率 82.5%） |
| 无差别 fork，紧预算 22 s | 71.7% |
| 风险分级 fork（本文），紧预算 22 s | **82.5%** |
| oracle 上限 | 100% |

大模型裁判把实验二的「阈值悬崖」抹平了（阈值 0.40–0.85，DIR 是 90/90/90/80），
但代价是误拦上升、任务成功率掉到 50–60%。**说明「猜」这条路本身走不通，不是裁判不够聪明**——
这正是 fork 存在的理由。

## 6. 这套东西现在不能证明什么

- 任务是 10 个手写的，不是基准；数字只说明机制成立，不说明效果有多大。
- 世界是模拟的（`prelim_study.py` 里的 `World`），副作用只发生在内存里。
- 图谱是 41 条边的手写种子；真实系统要从 CMDB / 数据目录 / 权限系统导入。
- `oracle` 与本文差 17.5 个点：说明调度还有空间，也说明任务集太小——一个漏判就是 5–10 个百分点。

## 7. 已经能写进论文的两个发现

1. **L1 换代，残余风险换形态**：关键词只拦 20%，图谱拦 80%。图谱漏掉的两类恰好是它结构上拦不住的——
   - `T4` 重复付款：图谱没有「刚才已经付过」的记忆，时序/幂等类风险只能靠 L2 带上下文判断；
   - `T9` 附件外发：`customers.csv` 与图谱里 `file:secrets/customer.csv` 实体没对齐，规则没触发。

   这两类正是 fork 与上下文裁判的用武之地。
2. **L1 越强，fork 的定位越清楚**：图谱门控下仅门控就到 80%，风险分级 fork 补到 99.2%
   （成本 19.3 s，比无差别 fork 22.8 s 省 15%）。fork 不是主力拦截手，是补最后一段的验证手段。

## 8. 怎么确认它真的在调模型

三条自查，任意一条都能证伪「其实全是本地规则在跑」：

1. **看来源标签**：输入后那行 `理解 … 来源 …` 现在明确写
   `大模型(新请求)` / `大模型(缓存未发请求)` / `本地规则(未调模型)`。
   写着"本地规则"却给了风险分，才是真的没调模型。
2. **看 `:stat`**：分四行列 `API 请求 / 缓存命中 / 错误 / 最近一次请求耗时`。
   本地规则耗时是 0 ms；真实请求是 800–1500 ms 的网络往返，骗不了人。
3. **强制关缓存**：`:nocache off` 之后每句都真发请求，
   再对着 DeepSeek 后台的「用量」看，请求数和余额都会动。

> 缓存不是偷懒：评测要跑 800+ 次判定，每次都真调既慢又贵，
> 论文里应当写明「解析与判定各一次调用，其余命中缓存」并报告命中率。

DeepSeek 后台的坑：用量页默认按**创建 key 那天**筛选（比如 10/12–10/12），
要手动改成今天；余额在 https://api.deepseek.com/user/balance 可直接查。

## 9. 工具目录（50 个，`catalog.py` 是唯一事实源）

以前只有 10 个工具，连"查一下张伟是谁"都做不到。现在按**业务域**铺开，每个工具都要声明两件事：

- **副作用等级**：`只读 · 可撤销 · 可补偿 · 不可撤回`（四级）
- **为什么是这个等级**（`why` 字段，答辩时就是"你凭什么说它危险"的答案）

| 业务域 | 工具 |
|---|---|
| 查询 11 | `find_contact` `get_employee` `query_db` `read_file` `list_files` `http_get` `search` `get_calendar` `list_orders` `check_service` `get_balance` |
| 沟通 5 | `send_email` `draft_email` `send_im` `send_sms` `reply_ticket` |
| 财务 6 | `pay` `refund` `issue_invoice` `create_po` `approve_expense` `transfer_internal` |
| 人事 5 | `apply_leave` `approve_leave` `onboard_employee` `offboard_employee` `grant_permission` |
| 研发 10 | `deploy` `rollback` `run_migration` `delete_records` `drop_table` `write_db` `create_pr` `merge_pr` `restart_service` `scale_cluster` |
| 办公 10 | `book_flight` `book_hotel` `book_meeting_room` `create_event` `create_doc` `update_doc` `publish_post` `delete_file` `move_file` `share_file` |
| 生活 3 | `buy_goods` `cancel_order` `pay_bill` |

**关键设计：等级不是工具的静态属性。** `send_im` 发同事是 `reversible`（能撤回），
发客户就是 `irreversible`（等同于发布）。所以判定入口是 `effect_of(tool, args)` 而不是查表。

**加一个工具只改一处**：往 `catalog.py` 的 `_RAW` 里加一行，提示词、参数校验、
门控规则、沙箱副作用描述全都自动跟上——因为 `nlu.py` / `kg_gate.py` / `sandbox.py` 都从这里读。

代价：提示词从 ~600 token 涨到 ~1600 token，一句新话约 ¥0.002（50 个工具的清单要占地方）。

命令：

```
> :tools            # 全部 50 个，按业务域分组
> :tools 财务        # 按关键词过滤
> :find 张           # 直接查通讯录（只读，不花钱）
```
