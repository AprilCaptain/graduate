# 词汇档案结构与更新

## 目录

- [结构选择](#结构选择)
- [稳定记录标识](#稳定记录标识)
- [每日训练文件](#每日训练文件)
- [单词详情文件](#单词详情文件)
- [词汇目录](#词汇目录)
- [来源游标](#来源游标)
- [归档状态](#归档状态)
- [人工内容保护](#人工内容保护)

## 结构选择

先检查项目实际结构并遵守 `AGENTS.md`。已有结构不同时做最小范围兼容更新，不迁移或整体重构。尚无对应结构时可采用：

```text
daily/YYYY/MM/YYYY-MM-DD.md
entries/<首字母>/<word>.md
indexes/<首字母>.md
metadata/extraction-state.yml
metadata/source-cursors.yml
index.md
```

只创建本次归档必需的目录和文件。使用 `assets/daily-template.md`、`assets/word-template.md`、`assets/extraction-state-template.yml` 和 `assets/source-cursors-template.yml` 作为新文件起点；词汇目录由 `scripts/update_index.py` 按固定表格格式维护。

## 稳定记录标识

调用：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/generate_record_id.py \
  --date YYYY-MM-DD \
  --source project/relative/source.md \
  --content-file project/relative/source.md \
  --new-word acquire \
  --weak-word issue \
  --sentence "Students acquire knowledge through reading and practice."
```

直接粘贴内容时可从标准输入读取，并使用 `--source pasted-chat-record`。需要同时取得跨来源去重指纹时使用 `--output json`：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/generate_record_id.py \
  --date YYYY-MM-DD \
  --source chatgpt-share-<share-id> \
  --content-file path/to/exact-unit.txt \
  --output json
```

`record_id` 包含训练日期、来源和准确单元原文，用于同一来源内的稳定标识；`content_fingerprint` 只包含训练日期和规范化后的准确单元原文，不包含来源名、消息 ID、模型摘要或提取后的词表，用于发现同一训练单元经不同链接、导出或粘贴内容重复进入。输入自带可靠批次编号时将其另存为 `external_id`，仍维护内部 `record_id`。不要为缺少可靠日期的草稿生成正式 ID。

## 每日训练文件

默认使用 `daily/YYYY/MM/YYYY-MM-DD.md`，同一天只有一个文件。按首次归档顺序使用“第1次训练”“第2次训练”；不要因重复执行重排章节。每节紧接标题保存：

```markdown
<!-- record_id: YYYY-MM-DD-xxxxxxxx -->
```

每次训练至少包含学习内容、新词、复习词、主要搭配、代表句、薄弱词与易错点、句法问题、阅读问题和下次复习。单词链接必须指向真实文件；从日期文件到默认单词目录通常使用 `../../../entries/<首字母>/<word>.md`。

“新学单词”使用两列表格，不使用项目符号列表：

```markdown
| 单词 | 核心含义 |
|---|---|
| [acquire](../../../entries/a/acquire.md) | 获得；习得 |
```

表格中的每个单词只出现一次，核心含义保持简洁。复习单词沿用项目现有格式，除非用户另行指定。

同一 `record_id` 只能出现一次。重复运行时校验并补全该节，不要重复追加、覆盖其他训练、删除人工总结，或因模板变化重写整份文件。

## 单词详情文件

默认使用 `entries/<首字母>/<word>.md`，以小写可靠原形作为唯一文件标识。首字母使用英文首字母；无法可靠确定原形或路径时列为待确认。

新文件 YAML 至少包含：

```yaml
---
word: acquire
first_learned: 2026-07-20
last_updated: 2026-07-20
---
```

不要添加 `status`、`mastery_level`、正确率或复习状态。保留其他原有 YAML 字段和人工内容。文件至少维护核心词义、常用搭配、句子训练和按日期列出的学习记录。核心词义表只保留“词性”和“核心含义”两列，不添加“本次语境”列；单词详情不创建独立的“易错点”章节，有复习价值的错误提醒汇总到每日训练记录。学习记录关联 `record_id`，以日期、来源和规范化内容去重。

先创建单词文件，再写每日文件中的链接。更新 `last_updated` 只反映实际归档的训练日期；不要用运行日期。

## 词汇目录

根目录 `index.md` 只维护不超过 26 行的首字母导航表和每个分片的单词数量；实际单词目录使用 `indexes/<首字母>.md`。每个字母分片使用表格，组内忽略大小写按字母排序：

```markdown
| 单词 | 核心含义 |
|---|---|
| [acquire](../entries/a/acquire.md) | 获得；习得 |
```

新增或更新单词时：

1. 不要读取完整总目录或无关字母分片。
2. 已知单词、核心义和真实详情路径时，直接调用：

   ```bash
   python3 .agents/skills/vocabulary-session-extractor/scripts/update_index.py \
     --word acquire \
     --meaning "获得；习得"
   ```

3. 单个词时脚本只读取和更新目标字母分片，再更新小型总目录中的该字母计数；重复执行不得产生差异。
4. 每个单词只出现一次，只展示 1—2 个核心含义。不要在目录展开搭配或例句。
5. 保留分片表格标记之外的人工内容；检查总目录、字母分片和单词详情之间的链接。

一次归档多个单词时，避免逐词重复更新根目录。将变更整理为 JSONL，并从标准输入交给：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/update_indexes_batch.py <<'JSONL'
{"word":"acquire","meaning":"获得；习得"}
{"word":"context","meaning":"上下文；语境"}
JSONL
```

批量脚本预先验证全部详情文件和受影响表格，只读取受影响的字母分片，每个分片最多写入一次，最后只更新一次根目录。相同批次可用 `--check` 验证幂等性。

## 来源游标

默认使用 `metadata/source-cursors.yml`，按稳定来源名维护独立游标。ChatGPT 分享来源可记录：

```yaml
version: 2

sources:
  chatgpt-share-<share-id>:
    kind: chatgpt-share
    location: https://chatgpt.com/share/<share-id>
    last_message_id: <completed-summary-message-id>
    last_message_sha256: <sha256>
    last_record_id: YYYY-MM-DD-xxxxxxxx
    last_training_date: YYYY-MM-DD
```

外部链接可以保存为 `location`；本地文件只能保存项目内相对路径，不得保存用户计算机的绝对路径。不同来源分别维护，不因标题相同而合并，也不因用户切换来源而删除旧游标。

追加型 Markdown、文本或导出文件使用字节游标：

```yaml
sources:
  exported-vocabulary-session:
    kind: append-text
    location: sources/session.md
    last_byte_offset: 12345
    last_prefix_sha256: <sha256>
    last_record_id: YYYY-MM-DD-xxxxxxxx
    last_training_date: YYYY-MM-DD
```

获取或验证游标：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/extract_incremental_text.py \
  --input-file sources/session.md \
  --cursor-info

python3 .agents/skills/vocabulary-session-extractor/scripts/extract_incremental_text.py \
  --input-file sources/session.md \
  --after-byte-offset 12345 \
  --after-prefix-sha256 <sha256>
```

字节偏移必须位于 UTF-8 字符边界；偏移前任意内容变化都会报错，不得自动输出完整文件。

ChatGPT 分享页增量提取示例：

```bash
curl -L --compressed -s "https://chatgpt.com/share/<share-id>" |
  python3 .agents/skills/vocabulary-session-extractor/scripts/extract_chatgpt_share.py \
    --after-message-id "<message-id>" \
    --after-content-sha256 "<sha256>"
```

先查看已完成单元的边界和未完成尾部，不把正文送入模型：

```bash
curl -L --compressed -s "https://chatgpt.com/share/<share-id>" |
  python3 .agents/skills/vocabulary-session-extractor/scripts/extract_chatgpt_share.py \
    --after-message-id "<message-id>" \
    --after-content-sha256 "<sha256>" \
    --format jsonl |
  python3 .agents/skills/vocabulary-session-extractor/scripts/split_training_units.py \
    --manifest
```

确认单元编号后，只输出一个单元：

```bash
curl -L --compressed -s "https://chatgpt.com/share/<share-id>" |
  python3 .agents/skills/vocabulary-session-extractor/scripts/extract_chatgpt_share.py \
    --after-message-id "<message-id>" \
    --after-content-sha256 "<sha256>" \
    --format jsonl |
  python3 .agents/skills/vocabulary-session-extractor/scripts/split_training_units.py \
    --unit-index 1 \
    --format markdown
```

新分享链接没有独立游标、但可能来自既有长期会话时，可重复传入其他来源的候选边界：

```bash
curl -L --compressed -s "https://chatgpt.com/share/<new-share-id>" |
  python3 .agents/skills/vocabulary-session-extractor/scripts/extract_chatgpt_share.py \
    --candidate-cursor "<message-id-1>:<sha256-1>" \
    --candidate-cursor "<message-id-2>:<sha256-2>"
```

脚本只接受消息 ID 与内容哈希同时匹配的最靠后边界；传入候选但没有匹配时必须失败，不得自动回退为完整输出。确认是全新来源后，才可不带游标重新执行。

原始页面可在工具或脚本内部完整获取，但只有裁剪后的新增消息可以进入模型上下文。可用 `--cursor-info <message-id>` 取得任意已完成总结消息的精确 ID 与哈希，不要求它是会话最后一条消息。完成内容、目录和归档状态验证后，最后推进来源游标；未完成训练不得推进。

## 归档状态

默认使用版本 2 的 `metadata/extraction-state.yml` 和 `metadata/source-cursors.yml`，以内部 `record_id` 为 `processed_records` 的键。每项至少记录训练日期、真实来源、每日文件、归档状态、`content_fingerprint` 和 `source_boundary_kind`。消息型来源保存起止消息 ID、各自内容哈希和消息数量；追加文本来源保存起止字节偏移及结束前缀哈希。可选保存 `external_id`。来源文件只存项目内相对路径；粘贴来源使用 `pasted-chat-record`。

元数据只通过脚本查询，不把完整状态文件送入模型：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/query_metadata.py \
  --source chatgpt-share-<share-id>

python3 .agents/skills/vocabulary-session-extractor/scripts/query_metadata.py \
  --fingerprint sha256:<sha256>
```

将状态文件和来源游标作为最后的元数据更新。只有相关单词文件、每日文件和目录成功更新并验证后，才运行：

```bash
python3 .agents/skills/vocabulary-session-extractor/scripts/finalize_metadata.py \
  --record-id YYYY-MM-DD-xxxxxxxx \
  --date YYYY-MM-DD \
  --source chatgpt-share-<share-id> \
  --daily-file daily/YYYY/MM/YYYY-MM-DD.md \
  --content-fingerprint sha256:<sha256> \
  --source-start-message-id <message-id> \
  --source-start-sha256 <sha256> \
  --source-end-message-id <completed-summary-message-id> \
  --source-end-sha256 <sha256> \
  --source-message-count <positive-integer> \
  --kind chatgpt-share \
  --location https://chatgpt.com/share/<share-id>
```

推进已有消息来源时额外传入 `--expected-last-message-id` 和 `--expected-last-message-sha256`；追加文本来源改用 `--source-start-byte-offset`、`--source-end-byte-offset`、`--source-end-prefix-sha256` 以及对应的 `--expected-last-byte-offset`、`--expected-last-prefix-sha256`。脚本同时核对游标位置与哈希，防止并发或陈旧输入越过边界。脚本先验证内容、目录和链接，再写入 `status: completed` 并推进来源游标；重复执行相同记录不会产生文件差异。日期未确认的草稿不得登记完成。

## 人工内容保护

保护用户添加或修改的备注、译文、释义、章节、标签、学习记录及非标准但有效的内容。使用局部插入或合并：

- 不用模板覆盖整个已有文件；
- 不删除无法识别的章节；
- 不自动替换人工措辞；
- 不无故批量重排文件；
- 不移动现有档案；
- 不修改项目根目录之外的内容。
