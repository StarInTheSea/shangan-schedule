# 上岸时间表 Skill

`shangan-schedule` 是一个可安装的 Agent Skill，用来从官方公告采集、审核并生成中国公务员招录时间表。它覆盖国考、省考、独立市考和面向应届生的选调生招录，并把未经确认的数据隔离在候选层。核心运行只依赖 Python 标准库，不绑定某一个 Agent 产品。

## 能做什么

- 按招录年度重复运行，例如 2026、2027。
- 按年度和考试类型维护覆盖矩阵；2027 全国选调生默认是 31 个省级地区。
- 仅接受官方来源白名单中的公告链接。
- 校验日期、来源证据、精确公告链接、覆盖计数、事件 ID 与审核状态。
- 生成候选数据与确定性差异报告，等待人工确认。
- 晋升前占用唯一审核批次，保留日期变更前快照及可恢复审核回执。
- 生成无需数据库的移动端静态网站，时间线和月历都显示月精度事件。

## 安装

### 环境要求

- Python 3.10 或更新版本；脚本只依赖标准库，不需要 `pip install`。
- 支持 `SKILL.md`、浏览网页和执行本地脚本的 Agent。Skill 本身不需要 Token、密码或 API Key；宿主 Agent 的账号由使用者自行配置。
- 离线时可以校验已有数据、构建网站，采集最新公告需要联网。下载包不附带实时考试数据。

### 下载并安装

下载发布附件 `shangan-schedule.zip`，解压后得到 `shangan-schedule/`，保留整个文件夹。将它放到所用 Agent 的 Skill 目录，随后重新加载 Skill 或开启新会话。不同 Agent 的目录以其配置为准。

对于使用 `$CODEX_HOME/skills` 的环境，可在解压位置运行以下命令（未设置 `CODEX_HOME` 时使用 `~/.codex`；已存在同名 Skill 时先自行备份，不覆盖）：

```bash
python3 --version
skill_parent="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$skill_parent"
if [ ! -e "$skill_parent/shangan-schedule" ]; then
  cp -R shangan-schedule "$skill_parent/shangan-schedule"
fi
```

下载 GitHub 源码 ZIP 的用户，应把解压后的仓库目录改名为 `shangan-schedule`。仓库根目录就是 Skill 根目录，不能只复制 `SKILL.md`。

安装后可以使用自然语言调用，例如：

```text
请使用 shangan-schedule Skill，扫描官方来源白名单并更新 2027 年全国选调生上岸时间表。先生成候选差异并等待我审核，不要预测尚未公布的日期；得到我批准后再生成本地网站。
```

页面上的“复制更新提示词”会根据当前年度、类型和地区筛选自动生成同类指令，可以粘贴给任意兼容 Agent。静态网页本身不会自动联网。

## 可重复运行

```bash
python3 scripts/shangan_schedule.py init \
  --year 2027 --type selected_graduate \
  --sources references/official-sources.json \
  --output .shangan-schedule/2027/candidates.json

python3 scripts/shangan_schedule.py review \
  --candidate .shangan-schedule/2027/candidates.json \
  --published .shangan-schedule/published.json \
  --sources references/official-sources.json \
  --output .shangan-schedule/2027/review.md
```

`init` 不覆盖已有文件。使用 `--region` 初始化的局部刷新会标记为 `partial`，晋升时只能合并进已有完整范围，不能把全国矩阵错误缩成 1/1。Agent 负责浏览与提取官方公告；脚本负责初始化、校验、差异、审核晋升和静态网站构建。新公告先停留在 `candidate`，必须经过用户明确批准才能进入正式数据。

## 本地验证

```bash
python3 -m unittest discover -s tests -v
```

真实抓取数据、审核记录和生成网站默认写入 `.shangan-schedule/` 或 `.local-output/`，不会进入本开源仓库。

## 许可证

代码、模板和文档使用 [MIT License](LICENSE)。官方公告内容及第三方网站不属于本项目授权范围；使用者应遵守来源网站的访问规则。

## 制作分享包

```bash
python3 scripts/package_skill.py
```

输出 `dist/shangan-schedule.zip` 和 SHA-256 校验文件。打包器只收录代码中明确列出的文件，拒绝符号链接，并检查常见密钥、个人目录、邮箱和手机号模式。`.git`、采集数据、审核历史、缓存和环境配置不会打包；ZIP 的文件时间和权限使用固定值，不附带本机所有者信息。扫描是辅助检查，不能保证识别所有形式的敏感信息。

公开分享请使用此 ZIP 的内容创建新仓库或作为附件发布。直接推送已有仓库会包含历史提交的作者、邮箱和旧文件；`.gitignore` 无法清除历史。上传前另行检查 GitHub 账号、提交署名与邮箱的公开设置。

测试夹具仅用于验证，日期及审核标记不构成当前招录信息。真实采集文件应保存在使用者工作目录中；若该目录受 Git 管理，需要在该项目里单独忽略 `.shangan-schedule/`。
