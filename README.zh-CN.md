# CNKI Search Skill 中文说明

`cnki-search` 是一个面向 Claude Code 的知网检索 skill。它提供结构化 JSON CLI，用于论文检索、详情抓取、分面发现、引文导出、PDF/CAJ 下载和 workspace 检查。

这个仓库可以直接 clone，然后运行顶层 `install.py` 安装到目标 Claude Code 项目的 `.claude` 目录。

## 安装到 Claude Code

推荐把仓库 clone 到需要安装 skill 的 Claude Code 项目里：

```bash
cd /path/to/your/claude-project
git clone https://github.com/LongMarching/cnki-search-skill.git
cd cnki-search-skill
python install.py
```

运行 `python install.py` 时，安装器会自动识别外层 Claude Code 项目，并把文件安装到外层项目的 `.claude/`，不会安装到 clone 仓库自己的 `.claude/`。

也可以把 clone 放在项目的 `.claude` 目录里：

```bash
cd /path/to/your/claude-project/.claude
git clone https://github.com/LongMarching/cnki-search-skill.git
cd cnki-search-skill
python install.py
```

这种布局仍然会安装到：

```text
/path/to/your/claude-project/.claude/skills/cnki-search/
```

如果 clone 仓库不在目标项目里面，可以显式指定目标项目：

```bash
python /path/to/cnki-search-skill/install.py --target /path/to/your/claude-project
```

安装器会写入或更新：

```text
<claude-project>/
  .claude/
    settings.local.json
    agents/
      cnki-paper-retriever.md
    hooks/
      cnki_search_hook.py
    skills/
      cnki-search/
```

如果 `.claude/settings.local.json` 已存在，安装器会先创建时间戳备份，再把本仓库的 hooks/settings 片段合并进去。安装完成后，重新打开 Claude Code 项目，让本地 settings 重新加载。

## 安装后验证

在目标 Claude Code 项目根目录运行：

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

命令应返回 JSON，其中包含 `status`、`workspace_id`、`run_id`、`summary`、`rows` 等字段。

PowerShell 用户建议先设置 UTF-8 输出：

```powershell
$env:PYTHONIOENCODING = 'utf-8'
python .claude/skills/cnki-search/run.py search "机器学习" --page 1 --output-limit 5 --return-fields search_basic
```

## Claude Code 中如何使用

安装后，Claude Code 可以加载 `Skill("cnki-search")`。配套 agent 文件位于：

```text
.claude/agents/cnki-paper-retriever.md
```

安装器还会安装 hook：

```text
.claude/hooks/cnki_search_hook.py
```

settings 片段会加入 `SessionStart`、`SubagentStart`、`PreToolUse` hooks，用于给 Claude Code 会话提供 cnki-search 路径提示，并为 cnki-search CLI 调用补充安全的默认环境变量。

## 常用命令

所有命令都输出 JSON。不要解析屏幕文本，应读取 JSON 里的 `status`、`workspace_id`、`run_id`、`summary`、`rows`、`warnings` 和行级状态字段。

```bash
python .claude/skills/cnki-search/run.py search "机器学习" --pages 1-2 --sort citations
python .claude/skills/cnki-search/run.py fetch_details --workspace WORKSPACE --run RUN --top 10
python .claude/skills/cnki-search/run.py discover_facets --workspace WORKSPACE --run RUN --group subdiscipline
python .claude/skills/cnki-search/run.py export --workspace WORKSPACE --run RUN --rows 1-5 --mode GBTREFER BibTex
python .claude/skills/cnki-search/run.py download --workspace WORKSPACE --run RUN --rows 1-3 --format pdf
python .claude/skills/cnki-search/run.py inspect --workspace WORKSPACE --run RUN --view rows
```

完整命令说明见：

```text
.claude/skills/cnki-search/SKILL.md
```

## 工作流建议

1. 先运行 `search`，获得 `workspace_id` 和 `run_id`。
2. 后续 `fetch_details`、`export`、`download`、`discover_facets`、`inspect` 都复用同一个 `workspace_id` 和 `run_id`。
3. 多 agent 并行时，父任务先检索一次，然后把相同的 `workspace_id`、精确的 `run_id` 和不重叠的行范围分发给子 agent。
4. 不要让子 agent 重新搜索，除非明确要求刷新检索结果。
5. 遇到 `captcha`、`login_required`、`permission_denied` 等 guarded 状态时，应报告结构化结果，不要绕过访问控制。

## 仓库结构

```text
install.py                         # clone 仓库安装器
.claude/settings.cnki-snippet.json # Claude Code hooks/settings 片段
.claude/agents/                   # Claude Code agent 模板
.claude/hooks/                    # Claude Code hook helper
.claude/skills/cnki-search/       # 完整 skill 源码和 CLI
tools/                            # 可选 bundle 构建和安装工具
tests/                            # 离线测试和受控 live harness
docs/                             # 安装和开发说明
```

## 可选 bundle 构建

通常直接使用顶层 `install.py` 即可。也可以生成独立 bundle：

```bash
python tools/build_claude_bundle.py
```

默认输出：

```text
dist/cnki-claude-bundle/
```

## CNKI 访问边界

本项目不绕过 CNKI 访问控制。它可以使用合法的 CNKI 访问状态，例如 IP 登录、`CNKI_COOKIE` 或 `CNKI_COOKIE_FILE`。

默认 cookie 优先级：

1. CNKI IP-login cookie seed
2. `CNKI_COOKIE`
3. `CNKI_COOKIE_FILE`

常用环境变量：

```bash
export PYTHONIOENCODING=utf-8
export CNKI_WORKSPACE_DIR=/path/to/cnki-workspaces
export CNKI_DOWNLOAD_DIR=/path/to/custom-download-dir
export CNKI_COOKIE_FILE=/path/to/cnki-cookie.txt
```

只有在明确不想使用自动 IP-login 时，才设置：

```bash
export CNKI_AUTO_IP_LOGIN=0
```

## 测试

```bash
python -m unittest discover -s tests -v
python -m py_compile install.py .claude/skills/cnki-search/run.py .claude/skills/cnki-search/scripts/cli.py .claude/skills/cnki-search/src/actions/_workflow_impl.py
```

真实 CNKI live 验证需要显式开启：

```bash
CNKI_LIVE_TEST=1 python tests/live/cnki_search_live.py --help
```

## License

本仓库尚未添加开源许可证。在仓库所有者添加许可证之前，保留所有权利。
