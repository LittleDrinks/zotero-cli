---
name: zotero-cli
description: "Manage a local Zotero library from the command line via SQLite direct read/write — search, dedup, import PDFs/arXiv papers with API-verified metadata, export BibTeX. Use when importing papers into Zotero, checking if a paper is already in the library, or managing Zotero collections without the desktop app."
version: 1.0.0
author: q2635
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [zotero, bibtex, papers, import, dedup, arxiv]
    related_skills: [obsidian-research, inbox-webclip]
---

# zotero-cli：命令行管理本地 Zotero 库

单文件 CLI，SQLite 直读直写。**读操作不需要 Zotero 开着；写操作要求 Zotero 关闭**（SQLite 写锁安全）。

## 触发条件

- 用户要求"导入这篇论文到 Zotero"（PDF / arXiv 号 / DOI）
- 用户要求"查一下这篇在不在库里"（查重）
- 用户要求整理分类、扫缺元数据条目、导出 BibTeX

## 安装与位置

- 仓库：`~/wsl-workspace/zotero-cli/`（GitHub: LittleDrinks/zotero-cli）
- 主程序：`zotero.py`，零第三方依赖（stdlib only）
- 依赖外部命令 `pdftotext`（poppler-utils）提取 PDF 首页标题
- 默认库：`/mnt/e/LittleDrinks/zotero/zotero.sqlite`，默认 storage：`/mnt/e/LittleDrinks/zotero/storage`（可用 `--db`/`--storage` 或环境变量覆盖）

## 命令速查

```bash
python3 ~/wsl-workspace/zotero-cli/zotero.py status            # 库健康：db 可读、Zotero 是否运行、条目数
python3 ~/wsl-workspace/zotero-cli/zotero.py search "关键词"    # 标题搜索（查重前置）
python3 ~/wsl-workspace/zotero-cli/zotero.py collections       # 列分类
python3 ~/wsl-workspace/zotero-cli/zotero.py import pdf a.pdf b.pdf --collection "分类名"
python3 ~/wsl-workspace/zotero-cli/zotero.py import arxiv 2608.04003 --collection "分类名"
python3 ~/wsl-workspace/zotero-cli/zotero.py meta-check        # 扫缺 date 的条目
python3 ~/wsl-workspace/zotero-cli/zotero.py export-bibtex --out refs.bib
```

## 导入流程（内建全部踩坑经验）

1. **校验 PDF**：`%PDF-` magic + 大小 >50KB（HTML 错误页/占位文件直接拒绝）
2. **标题提取**：pdftotext 读首页——真实标题，不信文件名（文件名是谎言）
3. **查重**：标题 2-3 个长词拼 LIKE 短语精确匹配，命中"标题完全一致且已有 PDF 附件"才跳过
4. **arXiv 元数据**：文件名或首页文本里找 arXiv ID → API `id_list` 验证标题/作者（**严禁凭记忆编 ID**）
5. **入库**：主条目 + 附件条目两条 items 行；storage 目录名 = **附件条目 key**（不是主条目 key）；作者按 last/first 拆分（rsplit 顺序坑已处理）
6. **分类**：按名找或创建 collection 后挂载

## 关键踩坑（已内建，勿重复踩）

- **Zotero 运行检测**：tasklist.exe 输出是 GBK 编码，必须 bytes 匹配或 decode('gbk')——UTF-8 解码崩溃会被 except 吞掉导致误判
- **arXiv 直连**：ProxyHandler({}) 禁用代理——走代理 SSL EOF；**OpenReview 相反走代理**（本工具不含 OpenReview）
- **附件 key ≠ 主条目 key**：storage 目录名必须等于附件条目的 key（Zotero 约定），验证用 `SELECT i.key FROM items i JOIN itemAttachments ia ON ia.itemID=i.itemID WHERE ia.parentItemID=<主条目>`
- **9p 挂载缓存**：写完 storage 后 WSL 侧 os.path.exists 可能间歇 False——以 Windows 侧 cmd.exe 验证为准
- **附件条目不级联删除**：删主条目不会删附件条目（itemAttachments 的 itemID 是附件自己的主键，parentItemID 才是外键）——手动先删 itemAttachments 行再删附件 items 行
- **写前必须确认 Zotero 关闭**：工具已内建检查，若报 "Zotero is running" 请用户先关
- **任务级清理**：测试导入后要删干净（主条目 + 附件条目 + storage 目录 + 临时分类），别污染用户库

## 与 obsidian-research 的关系

- 批量元数据核对、OpenReview 反爬下载、Zotero storage 修链等复杂场景 → 走 obsidian-research skill
- 日常单篇/小批量导入、查重、分类 → 本 skill 的 zotero.py 更快更稳
- 读论文（六问模板）→ obsidian-research 的 paper-reading-strategy.md

## 局限

- 只处理 journalArticle 类型；book/conferencePaper 等需手动 meta 扩展
- 导入元数据依赖 arXiv API；DOI 论文暂不支持自动元数据（手动 `--date`）
- 扫描版 PDF 无文字层 → pdftotext 提不出标题，会 SKIP 并提示
- SQLite schema 是 Zotero 内部实现，Zotero 大版本升级需适配
