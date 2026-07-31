# HR 法律法规知识库

独立网站：`https://hosamzj.github.io/hr-compliance/`

## 功能

- 跟踪中国大陆核心劳动、用工、社保相关法律法规
- 每月自动检查官方来源是否有更新
- 有更新时通过微信 + 邮件通知（通知邮箱已配置，不在网站公开展示）
- 邮件发送账号：`hosamzj@163.com`（通过 Himalaya CLI 配置）

## 监控法规

1. 中华人民共和国劳动法
2. 中华人民共和国劳动合同法
3. 中华人民共和国社会保险法
4. 中华人民共和国就业促进法
5. 中华人民共和国劳动争议调解仲裁法
6. 工伤保险条例
7. 职工带薪年休假条例

## 实现说明

v1 采用页面哈希监控方案：每月抓取国务院政策库首页（`https://www.gov.cn/zhengce/index.htm`）的 HTML 内容并比对哈希，若页面内容变化则发送通知。后续版本将尝试接入官方 API 或浏览器渲染，以精确到单部法规/政策条目。

## 目录结构

```
.
├── index.html              # 网站首页
├── data/
│   ├── laws.json           # 法规清单与状态
│   ├── history.json        # 更新记录
│   └── state.json          # 页面内容哈希（用于比对）
├── scripts/
│   └── check_updates.py    # 月度检查脚本
└── README.md
```

## 本地检查

```bash
cd /Users/huosam/github/hr-compliance
python3 scripts/check_updates.py
```

## 定时任务

每月 1 日 09:00 自动运行 `scripts/check_updates.py`。

---

由 Hermes Agent 维护。
