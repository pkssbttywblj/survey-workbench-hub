# 数据源能力与坑

只写「实测过」的信息。新增源之前先看本文末的接入约定。
所有连通性实测时间：**2026-11-25**，命令统一用 `curl --noproxy '*'`（绕开代理，模拟国内直连）。

## 1. 脚本可自动检索的源

| 源 | 覆盖 | 免key | 国内直连 | 返回里有 PDF 直链 | 实测坑 |
|---|---|---|---|---|---|
| **Crossref** | 元数据权威，DOI 最全 | ✅ | ✅ 200 | 少 | 摘要常带 JATS 标签，要剥；`select` 减小响应 |
| **arXiv** | CS/物理/数学预印本 | ✅ | ✅ 200 | ✅ 必有 | 引号短语检索极易 0 命中，用 `all:A AND all:B`；必须 https |
| **Europe PMC** | 生物医学，等效 PubMed | ✅ | ✅ 200 | 部分 | 学科偏生命科学，工程类召回一般 |
| **DOAJ** | 纯 OA 期刊 | ✅ | ✅ 200 | 部分 | 只有 OA，天然缺闭源；`bibjson` 结构特殊 |
| **PLOS** | PLOS 全系列 OA 期刊 | ✅ | ✅ 200 | ✅ 可拼 | 学科偏生命/材料/社科，工程类召回有限；`id` 就是 DOI |
| **DataCite** | DOI 注册库：数据集/软件/报告/学位论文 | ✅ | ✅ 200 | 无 | **相关性排序很松**（查 detonation 会返回小说），脚本已按标题/摘要过滤；它不是全文库 |
| **OpenAlex** | 2.5 亿工作项，跨学科最全 | ✅ | ⚠️ 本机 200，部分国内网络需代理 | 部分 | `abstract_inverted_index` 是倒排索引；FAIL 时剔除，别重试 |
| **Semantic Scholar** | 引用关系好 | ⚠️ 无 key 限流 | ❌ 429 | 部分 | 429 属正常，不要反复重试 |
| **Zenodo** | 幻灯片/海报/数据集/软件 | ✅ | ❌ 直连 000（本机走代理才通） | ✅ 在 `files[].links.self` | 失败属已知降级 |
| **Figshare** | 海报/数据集/预印本 | ✅ | ❌ 403 WAF | 部分 | 不可靠，失败属正常 |

用户点名的库怎么映射：

| 用户说 | 怎么做 | 说明 |
|---|---|---|
| Google Scholar | `openalex,crossref,semantic` 合并 | 没有官方 API，多源近似；引用数取最大 |
| Web of Science / Scopus / EI | `crossref` 近似 + `channels.py` 给机构库入口 | 无授权就拿不到真数据，要明确说明是近似 |
| PubMed | `europepmc` | 覆盖面等效 |
| PLOS / 公共科学图书馆 | `plos` | 国内直连，OA 全文 |
| 数据集、软件、科技报告 | `datacite` + `zenodo` | DataCite 给 DOI，Zenodo 给文件 |

## 2. 国内/机构检索渠道（脚本不自动抓）

这些站点实测是验证码、412、JS 渲染或需要机构登录，`search_all.py` 抓不到；
但**用户在浏览器里点一下就能检索**。由 `channels.py` 生成一键检索 URL + 逐渠道下载步骤。

| 渠道 | 分组 | 实测状态 | 授权 | 定位 |
|---|---|---|---|---|
| 知网 CNKI | 中文库 | 搜索页跳滑块验证码 | 需要 | 中文期刊/硕博论文主力 |
| 万方 | 中文库 | 站内检索页 200，全文需登录 | 需要 | 中文期刊/学位/会议 |
| 维普 | 中文库 | 直连 412 | 需要 | 中文期刊补充 |
| 百度学术 | 中文聚合 | 安全验证页 | 不需要 | 聚合题录，找 DOI 与各库入口 |
| ChinaXiv | 中文预印本 | 首页 200，POST 搜索 403 | 不需要 | 中科院预印本，多可免费下 |
| 中国科技论文在线 | 中文预印本 | 结果页 JS 渲染 | 不需要 | 首发论文，不少全文免费 |
| NSTL | 文献传递 | 检索页 200 | 不需要 | 外文期刊/会议/专利/标准原文传递 |
| 国家科技报告服务系统 | 中文报告 | 首页 200 | 不需要 | 科技报告，多可在线阅览 |
| 国家哲社文献中心 | 中文库 | 200 | 不需要 | 社科补充 |
| CBM 中国生物医学 | 中文库 | 302→200 | 不需要 | 生物医学补充 |
| 读秀 / 超星发现 | 中文图书 | 需机构登录 | 需要 | 专著与图书章节 |
| WoS / Scopus / EI | 机构库 | 需订阅 | 需要 | 引文与工程索引 |
| ScienceDirect / Springer / Wiley | 机构库 | CARSI 通常覆盖 | 需要 | 商业出版社全文 |
| AIAA / ASME / IEEE / ACM | 机构库 | 需订阅或会员 | 需要 | 会议与专业学会论文 |
| arXiv / PubMed / NASA NTRS / DTIC / ResearchGate | 开放/作者 | 公开 | 不需要 | 预印本、机构报告、作者索取 |

**为什么不自动抓**：绕过验证码和登录墙既不稳当也不合规；
把一键链接和可照做的步骤交付出去，比假装「已检索全部数据库」诚实得多。

**扩展检索的正确顺序**：

1. `search_all.py` 的开放源先跑一遍（能自动下载的直接入库）
2. 中文主题用 `--query-zh` 再跑一遍，合并
3. `channels.py` 选 2–4 个契合的中文/机构库，把同一检索式给用户点开补检索
4. 对仍拿不到全文的条目，`channels.py ask --root <R>` 生成授权询问与获取指引

## 3. 下载成功率

同一批检索结果，按内容类型看命中率（真实测试，RDE 主题，12 条样本）：

| 类型 | 成功率 | 原因 |
|---|---|---|
| arXiv 预印本 | 高 | 直链稳定 |
| PLOS / DOAJ / MDPI / Frontiers / PLOS | 高 | 站点对爬虫友好，OA |
| 商业出版社（Elsevier/Wiley/Springer 非OA/AIAA/ASME） | 低 | 403 反爬或登录墙，属于正常结果 |
| SSRN | 低 | 人机校验 + 代理对本域名 CONNECT 失败 |
| 中文期刊（CNKI/万方/维普） | 极低 | 无 OA，需机构通道 |

**下载成功率不是质量指标，覆盖率才是。** 闭源文献以「待手动获取 + DOI + 一键检索 + 通道」
的形式交付，本身就是有效产出。

## 4. 网络环境

三层通道按顺序降级（`_common.fetch_bytes`）：

1. 走环境变量代理（企业网/校园网常需要）
2. 直连（`ProxyHandler({})`）—— 代理把出版社域名 CONNECT 掉时救回结果
3. `curl_cffi` 伪装 Chrome TLS 指纹 —— 应对 Cloudflare/Akamai 的 UA 拦截

诊断某个地址到底卡在哪：

```bash
curl --noproxy '*' -s -o /dev/null -m 20 -w "%{http_code}\n" \
  "https://api.plos.org/search?q=detonation&rows=1&wt=json"
curl -s -o /dev/null -m 20 -w "%{http_code}\n" "https://doi.org/10.xxxx/xxxx"
```

`python <S>/check_env.py` 会分两张表打印：
`数据源`（脚本自动检索的源）和 `国内/机构检索入口`（只测可达，不自动抓）。
前者 FAIL 就从 `--sources` 剔除；后者 FAIL 只说明入口不通，不代表资料不存在。

## 5. 新增一个源要满足的约定

写一个 `search_xxx(query, limit, from_date, **_)` 函数，返回 record 列表，字段：

```python
{
  "title": str, "authors": [str], "year": int|None, "venue": str,
  "type": str,           # 尽量用 OpenAlex 的 type 词汇，便于 TYPE_CODE 映射
  "doi": str, "url": str, "pdf_url": str, "is_oa": bool,
  "citations": int, "abstract": str, "language": "" | "中文", "source": ["Xxx"]
}
```

注册进 `DISPATCH`。去重、排序、ID 分配都由后续脚本统一处理，**不要在源函数里做**——
判定逻辑两处实现迟早漂移。唯一例外是源自身的**噪声过滤**（如 DataCite 的标题命中），
那属于取数质量，必须留在源函数里。

## 6. 时效与排序

`--sort balanced`（默认）：`相关度×150 + 近5年衰减(450..0) + 被引对数(≤900)`。
调这三个权重只改 `search_all.score_records`，别在别处再写一套排序。
`--sort date` 给「只要最新」，`--sort citations` 给「找经典」。
