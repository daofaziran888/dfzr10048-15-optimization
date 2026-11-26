# 拾影工坊 · 音视频采集与转写结构化平台

面向音视频媒体源接入、分片切分、转码作业、转写任务、语音分段、说话人标注、字幕对齐、时间轴标注、术语抽取、转写版本、置信复核、纠错回路、字幕包交付、分片水位与失败重投的采集结构化平台。

这是一个前后端分离的管理平台：前端 Vue 3 + Vite + TypeScript，后端 FastAPI（Python）。
两边各自独立启动，前端 dev server 已关掉自动打开页面，启动后按终端打印的地址手工打开。

## 目录结构

```text
.
├── frontend/                 Vue 3 + Vite + TypeScript 前端
│   ├── src/views/WorkbenchView.vue|tsx  全部模块共享工作台
│   ├── src/data/modules.ts              18个业务模块配置
│   ├── src/api/              统一请求封装
│   └── vite.config.ts        dev server 配置（open: false）
├── backend/                  FastAPI（Python） 后端
│   ├── app/routers/resource.py  通用查询/导出/登记/动作接口
│   ├── app/services/<key>.py    每个模块独立领域逻辑
│   ├── app/services/base.py     共享服务骨架
│   └── app/store.py          内存数据仓库与示例数据
├── .gitignore
└── docker-compose.yml
```

## 启动

### 后端

```bash
cd backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
./run.sh
```

健康检查：`curl http://127.0.0.1:8005/api/health`

### 前端

```bash
cd frontend
npm ci
npm run dev
```

前端默认监听 `http://127.0.0.1:5178/`，dev server 不会自动打开浏览器，
需要自己访问。`/api` 由 vite 代理到后端 `http://127.0.0.1:8005`。

## 业务模块

| 模块 | 目录 | 业务对象 | 主要字段 |
| --- | --- | --- | --- |
| 媒体源 | `media_source` | 媒体源 | 媒体源编号、来源渠道、媒体标题 |
| 采集通道 | `capture_channel` | 通道配置 | 通道编号、关联媒体源、并发上限 |
| 媒体分片 | `media_shard` | 分片记录 | 分片编号、关联通道、起始秒 |
| 转码作业 | `transcode_job` | 转码作业 | 作业编号、关联分片、目标编码 |
| 转写任务 | `transcribe_task` | 转写任务 | 任务编号、关联分片、转写引擎 |
| 语音分段 | `speech_segment` | 语音段 | 分段编号、关联任务、起始毫秒 |
| 说话人标注 | `speaker_label` | 标注条目 | 标注编号、关联分段、说话人标识 |
| 字幕对齐 | `subtitle_align` | 对齐结果 | 对齐编号、关联分段、字幕行号 |
| 时间轴标注 | `timeline_mark` | 标注点 | 标注点编号、关联媒体源、标注毫秒 |
| 术语抽取 | `term_extract` | 术语条目 | 术语编号、关联任务、术语文本 |
| 转写版本 | `transcript_version` | 版本记录 | 版本编号、关联任务、版本号 |
| 置信复核 | `confidence_review` | 复核条目 | 复核编号、关联分段、段置信度 |
| 纠错回路 | `correction_loop` | 纠错单 | 纠错编号、关联版本、纠错范围毫秒 |
| 字幕包 | `subtitle_pack` | 字幕包 | 字幕包编号、关联媒体源、绑定版本 |
| 媒体索引 | `media_index` | 索引条目 | 索引编号、关联媒体源、索引词 |
| 分片水位 | `shard_watermark` | 水位读数 | 水位编号、关联媒体源、已处理分片序号 |
| 失败重投 | `fail_redeliver` | 重投单 | 重投编号、关联分片、失败阶段 |
| 转写配额 | `transcribe_quota` | 配额条目 | 配额编号、来源渠道、转写引擎 |
| 抽样质检 | `sample_qc` | 质检样本 | 样本编号、关联任务、抽样比例 |
| 交付回执 | `delivery_receipt` | 回执记录 | 回执编号、关联字幕包、接收方标识 |
| 归档架位 | `archive_shelf` | 归档条目 | 归档编号、关联媒体源、架位标识 |
| 采集审计 | `media_audit` | 审计条目 | 审计编号、关联对象、操作类型 |

## 约定

- 全部模块共享`frontend/src/views/WorkbenchView.vue|tsx`和一套资源路由。
- 每个模块仍保留独立`backend/app/services/<模块>.py`；纯前端保留
  `frontend/src/domain/<模块>.ts`，用于承载真实业务差异并维持36题位容量。
- 列表接口统一返回 `{ items, total, page, size }`，动作接口统一返回 `{ ok, message }`。
- 状态流转只允许在 `app/services` 里改，路由层不做业务判断。

## 缺片核对（批次缺片重扫与重投去重）

缺片核对页（`/missing-scan`）跨媒体分片、失败重投与转写任务三个模块，按批次编排：

- `POST /api/missing-scan/check` 批量检查片号：登记应收与已到片号，重算缺片并逐条
  关联未结补采单；批次独立（异批同号不抵消），重复扫描不新增相同未结单。
- `GET /api/missing-scan/batches/{批次}` 整组检查结果，缺片可分别领取。
- `POST /api/missing-scan/batches/{批次}/items/{片号}/claim` 领取缺片，复用同一张补采单。
- `POST /api/missing-scan/batches/{批次}/items/{片号}/arrivals` 上报到片；同区间不同
  摘要的多份到片进入待确认。
- `POST /api/missing-scan/batches/{批次}/items/{片号}/confirm` 确认采纳：结算补采单
  （重投页可见已补齐）并产出采纳回执；重报采纳返回既有回执不重复。
- `GET /api/missing-scan/receipts/by-task/{任务}` 从转写任务查回执：实际所用字节与
  落选录音出处。

核对状态落盘在 `backend/data/missing_scan_state.json`（可用环境变量
`MISSING_SCAN_STATE_FILE` 覆盖），重启后恢复，重扫与重报采纳均不重复。

领域：数据工程
子方向：数据采集、爬虫与RPA
