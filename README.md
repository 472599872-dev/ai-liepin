# 猎聘招聘智能体工作台

这是一个给猎头使用的本地招聘自动化工作台。目标是：多账号维护、多岗位配置、多任务队列、候选人库、自动抓取简历、自动评分、自动生成话术，并且只有在系统无法继续时提醒用户处理。

默认是安全模式 dry-run：只会生成分析结果和问候语，不会真的点击发送。

## 当前能力

- 账号管理：每个账号使用独立本地浏览器 Profile，避免频繁退出/登录切换。
- 岗位配置：JD、关键词、必备项、加分项、排除项、评分阈值、默认话术、dry-run。
- 任务列表：任务绑定账号和岗位，有状态、步骤、候选人上限。
- 候选人清单：候选人按岗位保存，记录简历状态、评分、沟通状态和来源 URL。
- 提醒中心：遇到登录失效、验证码、附件简历需人工决策、页面结构异常等情况时生成提醒。
- 过程日志：记录任务执行步骤、页面 URL、抓取状态和异常。
- 猎聘详情页抓取：自动展开“显示其他 N 段项目经历”，抓取展开后的正文。
- 附件简历：只标记“需索要/未授权”，不会自动点击索要附件。

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[desktop,dev]"
```

复制环境变量。大模型配置是可选的；不配置时使用本地规则评分。

```bash
cp .env.example .env
```

不要把 `.env` 发给任何人，也不要提交到 Git。猎聘账号不再写入配置文件，建议在桌面应用里为每个账号创建独立 Profile 后手动登录一次。

## 运行

### 本地桌面应用

启动应用：

```bash
liepin-agent-desktop
```

应用左侧是工作台：

- `账号`：维护多个猎聘账号，每个账号一个独立 Profile。
- `岗位`：配置 JD、关键词、评分规则和话术。
- `任务`：创建绑定账号和岗位的任务。
- `候选人`：查看每个岗位下的候选人清单。
- `提醒`：查看需要人工处理的异常节点。
- `日志`：查看执行过程。

右侧是内嵌猎聘页面和任务操作区。你可以先手动登录账号，之后任务会复用该账号的本地登录态。

默认 dry-run，只会生成/填充话术，不会点击最终发送。

## Windows 打包

桌面版使用 PySide6/Qt WebEngine，Windows 可执行文件需要在 Windows 电脑上打包。推荐使用 Windows 10/11、Python 3.11 64 位。

### GitHub 自动打包

仓库已配置 GitHub Actions：

```text
.github/workflows/windows-build.yml
```

配置好 GitHub 仓库后，有两种触发方式：

1. 在 GitHub 页面进入 `Actions`，选择 `Build Windows Packages`，点击 `Run workflow`。
2. 推送版本 tag，例如：

```bash
git tag v0.1.12
git push origin v0.1.12
```

Actions 会在 Windows 环境自动执行打包，产出：

```text
LiepinRecruitingAgent-win64.zip
LiepinRecruitingAgent-update-win64.zip
update.json
```

如果需要让打包成品自带千问 Key，请在 GitHub 仓库设置里配置：

```text
Settings -> Secrets and variables -> Actions -> Repository secrets
```

建议添加：

```text
QWEN_API_KEY
```

可选变量：

```text
QWEN_MODEL
QWEN_BASE_URL
OPENAI_API_KEY
OPENAI_MODEL
OPENAI_BASE_URL
```

注意：不要把 `.env`、`license.json`、`data/app.db`、`profiles`、`secrets` 提交到 GitHub。

### 本地 Windows 打包

在项目根目录打开 PowerShell：

```powershell
Set-ExecutionPolicy -Scope Process Bypass
.\scripts\build_windows.ps1 -Clean
```

打包完成后会生成：

```text
dist\LiepinRecruitingAgent\
dist\LiepinRecruitingAgent-win64.zip
dist\LiepinRecruitingAgent-update-win64.zip
```

把 `LiepinRecruitingAgent-win64.zip` 解压到 Windows 电脑后，双击：

```text
LiepinRecruitingAgent.exe
```

### 更新包

已有旧版本时，优先使用更新包：

```text
dist\LiepinRecruitingAgent-update-win64.zip
```

更新方式：

1. 关闭正在运行的 `LiepinRecruitingAgent.exe`。
2. 把更新包解压到旧软件目录，也就是原来 `LiepinRecruitingAgent.exe` 所在目录。
3. Windows 提示覆盖同名文件时选择全部覆盖。
4. 重新启动应用。

更新包只覆盖程序文件和静态查询配置，不包含也不会主动覆盖：

```text
data\app.db
profiles\app
.env
license.json
data\license.json
logs
```

不要先删除旧目录再解压更新包，否则历史候选人、账号、任务、登录态和授权会一起丢失。全新电脑才使用完整包 `LiepinRecruitingAgent-win64.zip`。

### API Key

打包脚本会把项目根目录的 `.env` 复制到成品目录，因此千问/OpenAI API Key 会随软件包一起带上。请不要把包含 `.env` 的压缩包发给无关人员。

如果 Windows 打包机器没有 `.env`，软件仍能启动，但不会自带大模型 Key；可以在成品目录手动放一个 `.env`。

### 授权文件

应用启动时会校验 `license.json`。没有授权或授权不属于当前电脑时，应用会弹出机器码并停止启动。

给新电脑授权的流程：

```bash
# 1. 在新电脑上打开应用，复制弹窗里的机器码

# 2. 在授权管理员电脑的项目根目录生成 license.json
python scripts/license_tool.py generate \
  --machine-id LPA-XXXXXXXX-XXXXXXXX-XXXXXXXX-XXXXXXXX \
  --customer 客户名称 \
  --expires-at 2026-12-31 \
  --output license.json

# 3. 把生成的 license.json 放到新电脑软件目录
#    LiepinRecruitingAgent\license.json
#    或 LiepinRecruitingAgent\data\license.json
```

授权私钥保存在：

```text
secrets/license_private_key.pem
```

这个私钥只应由授权管理员保存，不能放进 Windows 成品包，也不能发给客户。应用内只包含公钥，客户无法自行伪造授权。

### 数据和登录态

成品默认是干净数据：

```text
data\app.db
profiles\app
```

第一次启动会自动创建数据库。猎聘账号登录态会保存在 `profiles\app`。

如需迁移已有本机数据，可以把旧机器的 `data` 和 `profiles` 目录复制到成品目录下覆盖。注意这些目录可能包含候选人数据、账号信息和登录态。

## 推荐流程

1. 在 `账号` 页新增账号备注。
2. 选择账号，点击“打开登录/找人页”，在右侧页面手动完成登录。
3. 在 `岗位` 页新增岗位和评分规则。
4. 在 `任务` 页创建任务，绑定岗位和账号。
5. 点击“运行选中任务”，系统切换账号 Profile 并打开猎聘找人页。
6. 右侧可以先执行“填入搜索词”“抓取列表候选人”。
7. 打开候选人详情页后，点击“分析当前简历”，系统会展开项目经历、保存全文、评分、生成话术并写入候选人清单。

后续任务执行器会继续补齐批量打开候选人详情、批量评分和自动打招呼。

## 数据存储

主数据保存在：

```text
data/app.db
```

主要表：

```text
accounts
jobs
tasks
candidates
resume_snapshots
score_results
greeting_logs
execution_logs
alerts
```

## 自动化边界

系统默认自动执行可以安全处理的步骤：打开账号 Profile、进入找人页、填搜索词、抓列表摘要、打开/分析详情页、展开项目经历、保存简历、评分、生成话术。

遇到系统不应该或无法自动处理的节点，会进入提醒中心：登录失效、验证码/短信/扫码、安全提醒、附件简历需索要、页面结构变化、权益不足、连续失败、或任何可能产生不可逆外部动作的情况。

## 合规提醒

请确保你的使用方式符合猎聘平台规则、候选人隐私要求和你所在地区的招聘合规要求。建议先用 dry-run 人工复核，控制频率，并保留发送日志。
