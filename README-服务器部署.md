# 线报酷 → WordPress 自动采集发布 · 服务器部署版

> 运行时已迁移到你的 1H1G 国内服务器（宝塔计划任务 / systemd），每 5 分钟精准执行。
> GitHub Actions 定时**默认关闭**（见文末「为什么迁移 / GitHub 自动禁用」）。

---

## 一、环境要求

- 常开的 Linux 服务器（腾讯云 1H1G 轻量云 + 宝塔面板，国内）
- 系统自带 `python3`（≥ 3.8 即可，脚本极轻量）
- 能访问 `news.ixbk.net`（国内源，本机跑比 GitHub 美区 runner 更快更稳）
- 能访问你的 WP `12313.icu`

> 资源占用：每次运行约 5~15 秒、峰值内存 ~50MB，跑完即退出，**不常驻**。
> 对 1H1G 毫无压力，也不消耗任何"免费额度"（这是你自己的服务器）。

---

## 二、采集范围（分类规则）

| 类目 id | WP 类目 | 命中规则（优先级从上到下） |
|---|---|---|
| **1** | 京东好价 | 正文出现**真实京东链接**（u.jd.com / item.jd.com / 3.cn 等，已 follow 跳转确认落地 jd 域） |
| **10** | 优惠活动 | 命中以下任一关键词组（非京东时）： |
| | | ① **银行活动**：银行卡/信用卡/银联/各大银行简称（招行、工行…）/理财存款等 |
| | | ② **通信运营商活动**：中国移动 / 中国电信 / 中国联通 / 中国广电 的话费、流量、套餐、宽带、权益 |
| | | ③ **移动支付平台活动**：微信支付 / 支付宝 / 云闪付(银联) / 抖音支付 / 美团支付 / 京东支付 / 翼支付 / 数字人民币 等的**满减、立减、红包、立减金、消费券** |
| — | 丢弃 | 以上都不命中（纯无关内容） |

> 建议：把 WP 里原「银行活动」类目改名为「**优惠活动**」更贴切；不改也行，代码只用类目 id=10。
> 关键词宁可稍宽不可漏；若某类活动没被收，多半是缺对应关键词，在 `main.py` 的
> `BANK_KEYWORDS` / `TELECOM_KEYWORDS` / `PAYMENT_KEYWORDS` 补一条即可。

---

## 三、部署步骤（宝塔用户：计划任务）

### 第 1 步：上传代码
在服务器建目录（放在网站目录之外，避免被 Web 访问）：
```bash
mkdir -p /www/wwwroot/xianbao
# 把本仓库全部文件传进去，或 git clone：
# git clone https://github.com/17678319606/xianbao.git /www/wwwroot/xianbao
```
需包含：`main.py` `alert.py` `requirements.txt` `posted.json` `run.sh` `install.sh`
`.env.example` `monitor.sh` `xianbao.service` `keepalive.sh`。

### 第 2 步：一键装依赖 + 生成 .env
```bash
cd /www/wwwroot/xianbao
bash install.sh
vi /www/wwwroot/xianbao/.env      # 填 WP_APP_PASSWORD（WordPress 后台→用户→应用密码 生成）
```

### 第 3 步：配置告警（建议，见第五节）并 dry-run 验证
```bash
/www/wwwroot/xianbao/.venv/bin/python main.py --dry-run
```
应看到 `[DRY] would publish cat=1 ...` / `cat=10 ...` 一堆行，说明采集/分类/连通正常。
若报 `[ERROR] WP credentials missing` 说明 .env 没填好。

### 第 4 步：宝塔「计划任务」每 5 分钟跑一次（核心采集）★

**面板路径**：宝塔 → **计划任务** → **添加任务**

| 配置项 | 填法 |
|---|---|
| 任务类型 | **Shell 脚本** |
| 任务名称 | `xianbao采集`（随意） |
| 执行周期 | 选 **「N分钟」** → 填 **`5`**（即每 5 分钟） |
| 脚本内容 | `bash /www/wwwroot/xianbao/run.sh` |
| 保存 | 点 **添加** |

> 周期也可直接选「分钟」里的高级 cron 填 `*/5 * * * *`。
> 宝塔计划任务由 `crond` 守护，**开机自启、崩溃自动重试、不会被销毁**，
> 精准每 5 分钟执行——这就是你要的"稳定存在、准时、零额外费用"。

### 第 5 步：心跳巡检每 30 分钟（防静默停摆 + 自动告警）★

**面板路径**：宝塔 → **计划任务** → **添加任务**

| 配置项 | 填法 |
|---|---|
| 任务类型 | **Shell 脚本** |
| 任务名称 | `xianbao监控` |
| 执行周期 | 选 **「N分钟」** → 填 **`30`** |
| 脚本内容 | `bash /www/wwwroot/xianbao/monitor.sh` |
| 保存 | 点 **添加** |

`monitor.sh` 检查 `last_run.json`：超过 15 分钟没跑成功 → 输出 `ALERT` 并**自动调用
`alert.py` 发企业微信机器人 + 邮件**（见第五节）。alert.py 自带 6 小时冷却，不会刷屏。

### 第 6 步：确认在跑
- 心跳：`cat /www/wwwroot/xianbao/last_run.json` → `{"ts":...,"status":"OK","new":N,...}`
- 日志：`tail -f /www/wwwroot/xianbao/xianbao.log`
- WP 后台：类目 1（京东好价）/ 10（优惠活动）应每几分钟多出线报。

---

## 四、可选：systemd 常驻（替代计划任务）

```bash
cp /www/wwwroot/xianbao/xianbao.service /etc/systemd/system/xianbao.service
# 修改 service 里的 WorkingDirectory / ExecStart / 运行用户
systemctl daemon-reload
systemctl enable --now xianbao
systemctl status xianbao
```
> 计划任务 与 systemd **二选一，不要同时开**（会双发；WP 兜底去重可缓解但不建议）。

---

## 五、失败告警（企业微信机器人 + 邮件）

`.env` 中配置（见 `.env.example`）：

```ini
# 企业微信群机器人 webhook（群设置→群机器人→添加 获取）
WXWORK_WEBHOOK=https://qyapi.weixin.qq.com/cgi-bin/webhook/send?key=ca5e918f-8e98-4c96-9734-8e5d27b298d0
# 邮件告警收件人 + SMTP 发信配置
ALERT_EMAIL=weixinkaifa@jinbufenzi.work
SMTP_HOST=smtp.example.com
SMTP_PORT=465
SMTP_TLS=1
SMTP_USER=alert@your-domain.com
SMTP_PASS=你的SMTP授权码
```

触发时机（均带 6 小时冷却，避免刷屏）：
- 线报酷源站连续 3 次拉取失败 → `线报采集源故障`
- 本轮有文章发布失败（WP 凭证失效/接口异常） → `WP 文章发布失败`
- `--loop` 常驻模式未捕获异常崩溃 → `采集进程异常崩溃`
- 监控任务发现超过 15 分钟没跑 → `采集任务停摆`

> 邮件 SMTP 按你的服务商填（如腾讯企业邮、阿里云邮等）。只配 webhook、不配 SMTP 也能用。

---

## 六、如何避免"重复发文"（混合触发/双跑安全）

无论你用**宝塔计划任务**、**systemd**、还是以后**重新开启 GitHub Actions**，都靠两层去重保证不重复：

1. **本地状态 `posted.json`**：已发 id 直接跳过（每次仅发布成功才写入）。
2. **WP 侧精确去重**：发布时为每篇文章写入自定义字段 `xianbao_item_id`（=源站线报 id）；
   发之前先按该 meta 查 WP，命中即跳过。即使 `posted.json` 丢失/多机/多触发各跑各的，
   **WP 是唯一真相源**，也不会出现重复文章。

> 因此"混合触发"不会造成内容重复：服务器和 GitHub（若开启）读到的是同一个 WP，
> meta 去重天然幂等。这也是为什么 GitHub 定时可安全作为备用源。

---

## 七、GitHub 自动禁用 & 保活（你的顾虑）

**结论**：运行时已迁到服务器，**根本不依赖 GitHub 定时**，所以 GitHub「60 天无活动禁用定时」
**不可能影响你的发布**。该机制只禁用 *scheduled workflows*，而我们已把 `publish.yml` 的
`schedule` 默认注释掉（仅留 `workflow_dispatch` 手动触发作探针）。

若你**以后想重新把 GitHub 当备用源**：
1. 取消 `publish.yml` 里 `schedule` 注释即可（WP meta 去重保证不与服务器双发）；
2. 但 GitHub 会在 60 天无仓库活动后禁用该定时——用 `keepalive.sh` 保活：
   - `.env` 里填 `GH_TOKEN=<你的GitHub令牌>`（需 repo 写权限）；
   - 宝塔加一个**每周**计划任务：`bash /www/wwwroot/xianbao/keepalive.sh`
   - 它会空提交一次并 push，使仓库保持活跃，定时永不被禁用。

> 你提供的 GitHub 永久令牌已用于把本次优化**推送到仓库**（保持仓库活跃 + 交付代码）。
> 服务器上的 `.env` 若需保活，请单独填入 `GH_TOKEN`（不会随代码提交，已被 .gitignore 忽略）。

---

## 八、合规与去重

- 仅调用线报酷官方 `push.json`，每次运行只 GET 1 次、每 5 分钟 1 次，低于其轮询下限，不封 IP。
- 每篇带"来源：线报酷 + 原文链接"回链，满足站规。
- 去重双保险：`posted.json`（本地）+ WP `xianbao_item_id` meta（跨触发精确去重）。
- `posted.json` 自带近 30 天种子，**首次部署不会把历史线报批量重发**。

---

## 九、为什么要迁移（根因）

原方案用 GitHub Actions `*/5 * * * *` 定时，实测它不是可靠实时定时器：

1. **数据源是滚动窗口，仅最新 20 条**：某次运行被推迟，新线报挤出旧条 → **永久漏发**（主因）。
2. **GitHub cron 尽力而为**：高峰期延 15~30 分钟甚至跳过；60 天无活动**自动禁用定时**。
3. **GitHub runner 在境外**：抓国内源慢/超时，fetch 失败率更高。

搬到国内服务器后：每 5 分钟精准、抓国内源快、无额度限制、由宝塔/crond 守护不被销毁，
**活动信息实效性**得到保证。
