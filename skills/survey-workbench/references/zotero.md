# 机构权限与 Zotero 通道

覆盖「闭源文献怎么合法拿到」这一件事。原则：**用用户自己的订阅，不用任何绕过手段**。
skill 不接触账号密码、不存 cookie、不代过人机验证。

## 0. 先探测 Zotero MCP（可选分支）

接活时先探测一次，按结果选路线：

| 探测结果 | 路线 | 产出 |
|---|---|---|
| 有 Zotero MCP（名含 `zotero`） | 用 MCP 的 add item / attach PDF 接口 | 条目与 PDF 直接进 Zotero，itemKey 记进笔记元数据 |
| 无 MCP，有本地 Zotero | `zotero_bridge.py --status` → `--import` | 条目与 PDF 经本地 API/sqlite 导入 |
| 都没有 | 只用 `待手动获取` 清单 | 如实交付 DOI 与链接 |

探测靠 agent 的工具发现能力（不同平台入口不同），脚本不假装能做 MCP 调用。
本机实测：2026-11-25 无任何已安装连接器，所以走本地桥；不要为了这个临时装未知 MCP。

## 1. 三条合法通道

| 通道 | 适用 | 谁来做 |
|---|---|---|
| **CARSI / Shibboleth 联邦认证** | 绝大多数英文出版社（Elsevier、Springer、Wiley、IEEE、AIAA、ASME…） | 用户在浏览器点几下滑到 PDF，或让 Zotero 代抓 |
| **EZproxy / WebVPN** | 学校提供的校外访问代理 | 用户配置一次，之后全程有效 |
| **图书馆文献传递** | 学校没订阅的刊、中文老文献 | 提交 DOI，等邮件（1–2 工作日） |

中文文献（CNKI/万方）不走上面这套：它们极少 OA，机构通道通常也覆盖不到，
如实告诉用户「建议知网手动下载」，不要给假希望。

## 2. Zotero + sci-pdf + 茉莉花

这是把「用户在浏览器里的几次点击」变成「批量结果」的关键，值得花 10 分钟配一次。

| 组件 | 作用 | 地址 |
|---|---|---|
| Zotero 7 | 文献管理器，管条目和 PDF | https://www.zotero.org/download/ |
| **sci-pdf** | 按 DOI 自动抓 PDF，走你已登录的机构会话 | https://github.com/MuiseDestiny/zotero-sci-pdf |
| **茉莉花 Jasminum** | 中文文献元数据补全、知网辅助 | https://github.com/lifanxi/zotero-jasminum |

配置要点（详细步骤 `python <S>/zotero_bridge.py --guide`）：

1. 先让 Zotero 能走机构权限：首选项 → 通用 → 代理，填学校 EZproxy 地址；或先在浏览器完成 CARSI 登录
2. 选中条目 → sci-pdf 的 Update Sci-PDF → 条目下出现 PDF 图标即成功
3. 回到 skill：
   ```bash
   python <S>/zotero_bridge.py --status     # sqlite: True 说明读得到库
   python <S>/zotero_bridge.py --import --root <库目录> --query "关键词"
   python <S>/organize.py --root <库目录>
   ```
   `--import` 会把条目写成 `acquired.json` 格式并把 PDF 复制进 `.cache/staging/`，
   之后完全走正常的归档/解析流程。

## 3. Zotero 桥的实现细节

两种接入，按可用性自动选：

| 方式 | 条件 | 说明 |
|---|---|---|
| 本地 API `127.0.0.1:23119` | Zotero 7 开启「允许其他应用访问」 | 优先，实时 |
| 直接读 `zotero.sqlite` | 有本地库文件 | 复制到临时目录再读，避免被 Zotero 锁住 |

字段映射：`itemData` 里的 `title/date/DOI/publicationTitle/url/abstractNote`，
作者取 `itemCreators` 顺序，PDF 取 `itemAttachments.path = storage:<file>` 对应的
`storage/<attachmentKey>/<file>`。

已知限制：
- 只导 PDF 附件，不导 CAJ
- 分组库（group library）和其它用户的库不在默认读取范围内
- Zotero 正在跑且库被独占时，复制可能失败 → 关掉 Zotero 再试

## 4. 给用户的措辞

不要写「下载失败」。写成：

> 这 9 篇是商业出版社的闭源文献，公开渠道拿不到全文。已按出版方分好通道：
> 6 篇走机构 CARSI（Elsevier/AIAA），2 篇 SSRN 预印本需要登录后手动下，
> 1 篇建议图书馆文献传递。每条的操作步骤在 `索引表.xlsx → 待手动获取` 里，
> DOI 都填好了，点开就能用。

这样用户拿到的是可执行的下一步，而不是一句失败通知。
