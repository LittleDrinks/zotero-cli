---
name: zotero-cli
description: "Import papers into Zotero or check the library via SQLite CLI."
version: 3.0.0
author: q2635
license: MIT
platforms: [linux, macos, windows]
metadata:
  hermes:
    tags: [zotero, bibtex, papers, import, dedup, arxiv]
    related_skills: [obsidian-research, odl-pdf]
---

# zotero-cli

SQLite 直读直写的 Zotero 库管理 CLI。**只做库操作，不做 PDF 解析**。读操作不需要 Zotero 开着；写操作要求 Zotero 关闭。

仓库：https://github.com/LittleDrinks/zotero-cli

## 触发条件

用户要求导入论文到 Zotero、查论文是否已在库、整理分类、导出 BibTeX。

## 组合模式（PDF 提取 → 传参）

zotero-cli 不内置 PDF 解析。导入 PDF 前先用外部 PDF skill 提取标题和 arXiv ID：

1. 用 odl-pdf skill 提取：`opendataloader-pdf paper.pdf --to-stdout -f markdown --pages 1`（`# ` 行是标题，`## arXiv:` 行是 ID）
2. 传给 zotero-cli：`import pdf paper.pdf --title "..." --arxiv-id "..." --collection "..."`

## 命令

```bash
python3 <仓库路径>/zotero.py status            # 确认库可读、Zotero 未运行
python3 <仓库路径>/zotero.py search "关键词"    # 查重/搜索
python3 <仓库路径>/zotero.py collections       # 列分类
python3 <仓库路径>/zotero.py import pdf a.pdf --title "T" --collection "分类名"
python3 <仓库路径>/zotero.py import arxiv 2608.04003 --collection "分类名"
python3 <仓库路径>/zotero.py meta-check        # 扫缺 date 的条目
python3 <仓库路径>/zotero.py export-bibtex --out refs.bib
```

## 按输出继续

- **import 报 "Zotero is running"**：请用户关闭 Zotero 后重跑
- **import 输出 SKIP（--title required）**：先用 odl-pdf 提取标题再重跑
- **import 输出 SKIP（already in library）**：条目已在库，向用户说明
- **import 输出 IMPORTED**：向用户报告条目 key 和标题
- **任务含测试**：完成后清理测试条目（主条目 + 附件条目 + storage 目录 + 临时分类）

## 注意

- 复杂场景（批量元数据核对、OpenReview 下载、storage 修链）走 obsidian-research skill
- PDF 提取选型由用户习惯决定（odl-pdf / marker / 其他），zotero-cli 不绑定

## 局限

- 只 journalArticle 类型；DOI 论文无自动元数据；扫描版 PDF 需先 OCR 提取标题

完整说明见仓库 README.md。
