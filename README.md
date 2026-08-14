# 线报酷 → WordPress 自动采集发布（xianbao）

自动把线报酷的「京东好价」和「银行活动」线报，过滤掉图片/媒体后，
发到你的 WordPress（12313.icu）：
- 带京东真实商品链接的内容 → 类目 **1（京东好价线报）**
- 银行活动类内容 → 类目 **10（活动信息）**
- 其余内容 → 不发布

> ⚠️ **运行时已迁移（2026-08）**：为保证活动线报的实效性，本项目现在推荐跑在你自己的
> 国内服务器上（每 5 分钟精准执行），而不是 GitHub Actions 定时。
> 原因与部署方法见 👉 [README-服务器部署.md](./README-服务器部署.md)。
> GitHub Actions 的 `schedule` 已默认关闭，仅保留手动触发作为应急探针。

全程跑在 **GitHub Actions 免费额度**内（或你自有的服务器上），零成本。

---

## 一、安全提醒（务必先做）

你在别处贴出的 GitHub 令牌（PAT）和 WordPress 应用密码 **已视为泄露**：
1. 去 GitHub → Settings → Developer settings → Personal access tokens → **吊销旧令牌**，新建一个（公开仓库只需 `public_repo` 权限，其实本项目用不到，状态提交用内置 GITHUB_TOKEN）。
2. 去 WordPress 后台 → 用户 → 应用密码 → **撤销旧密码**，新建一个。
3. 新密码只填进 GitHub Secrets（见下文），**不要写进任何文件或聊天**。

### 数据安全设计（本项目的防盗用保障）
- **零明文**：所有密钥（WP 站点/账号/应用密码）只通过 GitHub Secrets 注入运行时环境变量，代码与仓库内**无任何明文凭据**。
- **最小暴露面**：状态文件 `posted.json` 的提交用仓库内置 `GITHUB_TOKEN`（仅 `contents: write`），**不需要你的 PAT**；即使 PAT 泄露也已吊销，无影响。
- **公开库也安全**：代码本身不含密钥，设为公开库不会泄露任何敏感信息。
- **专属密码**：WP 应用密码是「应用级」独立密码，泄露可单独吊销，不影响你 WP 后台登录密码。
- **自查清单**：① 旧 PAT 已吊销 ② 旧 WP 应用密码已撤销 ③ 仅在 GitHub Secrets 填新值 ④ 不把密钥贴进任何聊天/文档。

---

## 二、部署步骤（点按式，无需命令行）

### 第 1 步：把文件放进 GitHub 仓库
推荐用网页上传（最省事）：
1. 打开 https://github.com/17678319606/xianbao
2. 点 **Add file → Upload files**
3. 把本目录这些文件拖进去（保持目录结构）：
   - `main.py`
   - `requirements.txt`
   - `posted.json`
   - `.gitignore`
   - `.github/workflows/publish.yml`（注意 `.github` 是隐藏文件夹，也要传）
4. 点 **Commit changes** 提交。

（如果你会用 git，也可以本地 `git clone` 后放文件再 `git push`，效果一样。）

### 第 2 步：配置 3 个密钥（Secrets）
1. 仓库页面 → **Settings → Secrets and variables → Actions → New repository secret**
2. 依次添加：
   - `WP_SITE` = `https://12313.icu`（带 https，不带末尾斜杠）
   - `WP_USER` = `tougao`
   - `WP_APP_PASSWORD` = 你的 WordPress 应用密码（ newly created，形如 `xxxx xxxx xxxx xxxx`）
3. 保存。

> 不需要配 GitHub PAT——状态文件提交用仓库内置的 GITHUB_TOKEN。

### 第 3 步：开启并测试
1. 仓库页面 → **Actions** 标签，若提示启用，点 **Enable**。
2. 点 **xianbao-push** 工作流 → **Run workflow** 手动跑一次（相当于探针）。
3. 看运行日志：出现 `[DONE] new published=N` 且没有 `[ERROR] credentials missing` 即成功。
4. 之后每 5 分钟会自动跑（GitHub 定时，高峰期可能延迟，属正常）。

### 第 4 步：确认直接发布
脚本已设为 `PUBLISH_STATUS = "publish"`，线报**直接发布到对应类目**（类目1京东好价线报 / 类目10活动信息），无需后台手动放行。
- 如果想改回先发草稿再人工审核：把 `main.py` 里 `PUBLISH_STATUS = "publish"` 改成 `"draft"` 再提交。

---

## 三、合规说明
- **数据源合规**：只调用线报酷官方 JSON 接口（`/plus/json/push.json`），不爬 HTML 详情页（站规禁止且会被封 IP）。
- **回链合规**：每篇自动带「来源：线报酷 + 原文链接」，满足站方"调用须回链"要求。
- **频率护栏**：本工作流每次运行只 GET 一次 push.json，默认每 5 分钟跑一次——远低于线报酷"持续轮询≥5秒"的下限，不会封 IP，合规且最快。请勿在脚本内或仓库内私自加速到秒级轮询。

---

## 四、调整与排查
- **改频率（最短合规 = 每5分钟）**：编辑 `.github/workflows/publish.yml` 里的 `cron`。
  - `*/5 * * * *` = 每5分钟（**GitHub 支持的最短间隔**；公开库无限分钟，且对源站完全合规——每5分钟才请求一次，远低于线报酷 5 秒下限，不会封 IP）。**推荐。**
  - `0 * * * *` = 每小时（仅当你把仓库设为**私有**时使用，避免超 2000 分/月免费额度）。
  - 注：GitHub cron 用 UTC，但"每 N 分钟"是间隔类，无需换算时区，全球统一每5分钟跑一次。
- **类目对不上**：确认 WP 后台类目 id 1 / 10 存在（已验证存在：1=京东好价线报，10=活动信息）。
- **没发出来**：日志查 `[ERROR]`；常见是 Secrets 填错、应用密码无 `edit_posts` 权限、或网络问题。
- **京东链接判错**：白名单含 `u.jd.com / item.jd.com / *.jd.com / 3.cn`，并会 follow 短链确认落地 jd 域；非京东链接不会进类目 1。
- **仓库变大**：30 天前的去重记录会自动清理，`posted.json` 始终很小，500MB 额度用不到零头。

---

## 五、文件说明
| 文件 | 作用 |
|------|------|
| `main.py` | 采集→过滤→京东校验→去重→发布→清理 主脚本 |
| `requirements.txt` | 仅依赖 requests + beautifulsoup4 |
| `posted.json` | 已发线报的去重状态（自动维护，勿手改） |
| `.github/workflows/publish.yml` | 定时自动运行的工作流 |

> 所有密钥均以环境变量注入，代码与仓库内无任何明文密码。
