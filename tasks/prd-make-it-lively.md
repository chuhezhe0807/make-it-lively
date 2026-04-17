# PRD: Make It Lively

## Introduction

Make It Lively 是一个 AI agent 应用：用户上传一张静态图片，agent 识别图中元素、将每个元素作为独立图层提取出来，在画布上重绘，并根据用户的自然语言描述为元素添加动画，让静态图片"活过来"。M1 MVP 目标是端到端打通"图片 → 分层元素 → 动画预览 → GIF 导出"的最小链路。

## Goals

- 用户上传图片后，在 20 秒内看到元素识别结果
- 支持至少 5 个动画原语（translate / rotate / scale / opacity / path-follow）
- 自然语言描述可正确映射到图中元素（"让小鸟飞起来" → 定位到 bird 元素）
- 可导出 GIF 分享
- 前后端分离架构，便于独立迭代

## User Stories

### US-001: 搭建 FastAPI 后端骨架
**Description:** As a developer, I need a FastAPI backend skeleton with health check so subsequent endpoints can be added.

**Acceptance Criteria:**
- [ ] `backend/` 目录包含 FastAPI app + `pyproject.toml`（使用 uv 或 poetry）
- [ ] `/health` 返回 `{"status": "ok"}`
- [ ] `uvicorn app.main:app --reload` 可启动
- [ ] CORS 中间件已配置允许 `http://localhost:5173`
- [ ] `README.md` 说明启动方式
- [ ] Typecheck (mypy/ruff) 通过

### US-002: 实现图片上传接口
**Description:** As a user, I want to upload an image so the system can process it.

**Acceptance Criteria:**
- [ ] `POST /api/upload` 接受 multipart/form-data，字段名 `file`
- [ ] 支持 PNG / JPG / WebP，≤10MB，其他格式返回 400
- [ ] 图片存储到 `backend/storage/images/{uuid}.{ext}`
- [ ] 返回 `{"image_id": "uuid", "width": int, "height": int}`
- [ ] 包含 pytest 用例覆盖成功/超大/错误格式
- [ ] Tests pass
- [ ] Typecheck passes

### US-003: VLM 元素识别接口
**Description:** As the system, I need to call Claude VLM to identify semantic elements in the uploaded image.

**Acceptance Criteria:**
- [ ] `POST /api/perception` 接受 `{"image_id": "..."}`
- [ ] 使用 Anthropic SDK 调 Claude（model: claude-opus-4-7 或 claude-sonnet-4-6）返回元素列表
- [ ] 提示词要求 VLM 以 JSON 返回 `[{"id", "label", "bbox": [x,y,w,h], "z_order"}]`，用 prefill/tool_use 强制 JSON 结构
- [ ] 结果缓存到 `backend/storage/perception/{image_id}.json`，重复请求直接读缓存
- [ ] API 密钥从环境变量 `ANTHROPIC_API_KEY` 读取
- [ ] Tests pass（用 mock 的 Anthropic 响应）
- [ ] Typecheck passes

### US-004: SAM2 分割接口
**Description:** As the system, I need to segment each identified element into a transparent PNG layer using Replicate SAM2.

**Acceptance Criteria:**
- [ ] `POST /api/segment` 接受 `{"image_id": "...", "elements": [...]}`（元素含 bbox）
- [ ] 对每个元素 bbox 调用 Replicate SAM2 (`meta/sam-2`) 生成 mask
- [ ] 使用 mask 切出透明 PNG，保存到 `backend/storage/layers/{image_id}/{element_id}.png`
- [ ] 返回每个元素的 layer URL
- [ ] Replicate token 从环境变量 `REPLICATE_API_TOKEN` 读取
- [ ] Tests pass（mock Replicate client）
- [ ] Typecheck passes

### US-005: 背景修补接口
**Description:** As the system, I need to inpaint the holes left after extracting elements so the background layer is complete.

**Acceptance Criteria:**
- [ ] `POST /api/inpaint` 接受 `{"image_id": "...", "masks": [...]}`
- [ ] 使用 Replicate `lucataco/sdxl-inpainting` 或 `zylim0702/sd-inpainting` 修补合并 mask 区域
- [ ] 保存到 `backend/storage/layers/{image_id}/background.png`
- [ ] 返回背景图 URL
- [ ] Tests pass
- [ ] Typecheck passes

### US-006: 动画 DSL 规划接口
**Description:** As a user, I want my natural-language description to be converted into a per-element animation plan.

**Acceptance Criteria:**
- [ ] `POST /api/plan-animation` 接受 `{"image_id": "...", "elements": [...], "prompt": "让小鸟飞起来，云朵缓慢飘动"}`
- [ ] 调用 Claude，prompt 中附带元素列表（id + label），返回 GSAP-compatible DSL：`[{element_id, timeline: [...], easing, loop, duration_ms}]`
- [ ] DSL schema 定义在 `backend/app/schemas/animation.py`（Pydantic）
- [ ] 包含 5 个动画原语：translate / rotate / scale / opacity / path-follow
- [ ] 校验：所有 element_id 必须在输入 elements 中存在
- [ ] Tests pass
- [ ] Typecheck passes

### US-007: 搭建 Vite + Vue3 前端骨架
**Description:** As a developer, I need a Vite + Vue3 frontend skeleton with routing and basic layout.

**Acceptance Criteria:**
- [ ] `frontend/` 使用 Vite + Vue3 + TypeScript + Tailwind
- [ ] 配置 Vue Router：`/` 首页、`/editor/:imageId` 编辑器页
- [ ] API 客户端封装（axios 或 fetch wrapper），base URL 指向 `http://localhost:8000`
- [ ] `npm run dev` 可启动在 5173
- [ ] Typecheck (vue-tsc) 通过
- [ ] Verify in browser using dev-browser skill

### US-008: 图片上传页面
**Description:** As a user, I want a drag-and-drop upload page so I can start processing my image.

**Acceptance Criteria:**
- [ ] 首页有拖拽/点击上传区域，显示文件大小限制提示
- [ ] 上传后调用 `/api/upload`，成功后跳转到 `/editor/:imageId`
- [ ] 显示上传进度条
- [ ] 错误提示（格式/大小/网络错误）
- [ ] Typecheck passes
- [ ] Verify in browser using dev-browser skill

### US-009: 元素列表展示面板
**Description:** As a user, I want to see what elements the AI identified so I can trust / correct them.

**Acceptance Criteria:**
- [ ] 编辑器页左侧面板列出所有元素（label + 缩略图）
- [ ] 进入页面后自动依次触发 `/api/perception` → `/api/segment` → `/api/inpaint`（三连，确保 background.png 生成供 US-010 使用）
- [ ] 加载期间显示 skeleton loading，每一步有单独的进度提示
- [ ] 元素可以点击高亮（在画布上用边框标出对应元素）
- [ ] 三个 API 的失败状态分别处理，可重试
- [ ] Typecheck passes
- [ ] Verify in browser using dev-browser skill

### US-010: 分层画布渲染器
**Description:** As a user, I want the image redrawn as stacked layers so each element can be animated independently.

**Acceptance Criteria:**
- [ ] 编辑器中央显示画布（Canvas 或 DOM-based，尺寸匹配原图）
- [ ] 背景层 + 每个元素层按 z_order 叠加显示
- [ ] 视觉上与原图几乎一致（边界不穿帮）
- [ ] 组件封装在 `frontend/src/components/LayeredCanvas.vue`
- [ ] Typecheck passes
- [ ] Verify in browser using dev-browser skill

### US-011a: DSL → GSAP 映射库
**Description:** As a developer, I need a tested animator library that turns DSL into GSAP timelines, so the UI story can focus on wiring.

**Acceptance Criteria:**
- [ ] GSAP 已作为依赖安装
- [ ] `frontend/src/lib/animator.ts` 导出 `buildTimeline(dsl, layerRefs): gsap.core.Timeline`
- [ ] 实现 5 个原语映射：translate / rotate / scale / opacity / path-follow
- [ ] 每个原语支持 easing 和 loop 字段
- [ ] 单元测试（vitest）覆盖每个原语：喂固定 DSL，断言生成的 timeline 关键属性（duration、目标值、repeat）
- [ ] 未知原语或 element_id 不存在时抛出可识别错误
- [ ] Tests pass
- [ ] Typecheck passes

### US-011b: 动画 prompt 输入 + 播放控制
**Description:** As a user, I want to type what I want the elements to do and see them animate.

**Acceptance Criteria:**
- [ ] 右侧面板有文本输入框 + "让它活起来"按钮
- [ ] 点击按钮调用 `/api/plan-animation`，返回的 DSL 通过 US-011a 的 `buildTimeline` 应用到 LayeredCanvas 图层
- [ ] 支持播放 / 暂停 / 重置按钮，状态正确（播放中禁用"播放"按钮等）
- [ ] 调用失败时展示错误，不影响已有画布状态
- [ ] Typecheck passes
- [ ] Verify in browser using dev-browser skill

### US-012: GIF 导出
**Description:** As a user, I want to export the animation as a GIF so I can share it.

**Acceptance Criteria:**
- [ ] "导出 GIF" 按钮
- [ ] 使用 `gif.js` 或 `gifenc` 在前端录制 canvas 帧（≥2s，15fps）
- [ ] 导出后自动下载 `make-it-lively.gif`
- [ ] 录制期间显示进度
- [ ] Typecheck passes
- [ ] Verify in browser using dev-browser skill

### US-013: 后端 pipeline 集成测试
**Description:** As a developer, I want a pytest that runs the full backend pipeline with mocked external calls, plus a manual e2e checklist for live runs.

**Acceptance Criteria:**
- [ ] `tests/e2e/smoke.md` 记录人工验证步骤（固定参考图 + 固定 prompt），用于带真实 key 的验收
- [ ] 后端对参考图的 perception / segment / plan-animation 结果有 fixture 或快照
- [ ] 一条 `pytest` 用例跑完后端完整 pipeline（upload → perception → segment → inpaint → plan-animation），外部调用用 mock 或 VCR.py 录制
- [ ] Tests pass
- [ ] Typecheck passes

## Functional Requirements

- FR-1: 后端使用 FastAPI + Pydantic + httpx
- FR-2: 前端使用 Vite + Vue3 + TypeScript + Tailwind + GSAP
- FR-3: 所有 AI 调用（VLM / SAM / inpaint）都有结果缓存，避免重复消耗
- FR-4: 动画使用 JSON DSL 而非像素帧，Agent 只生成编排
- FR-5: DSL 必须包含：element_id / timeline（关键帧）/ easing / loop / duration_ms
- FR-6: API 错误统一返回 `{"error": str, "code": str}` 格式
- FR-7: 所有外部 API key 通过环境变量，不硬编码
- FR-8: 前后端支持本地一条命令启动（提供 `dev.sh` 或 Makefile）

## Non-Goals (Out of Scope)

- 不做用户账户 / 登录 / 付费
- 不做多图输入或视频输入
- 不做 AI 风格化再创作（不"把图变成吉卜力风"）
- 不做移动端专门适配（M1 仅保证桌面 Chrome）
- 不做 MP4 / Lottie 导出（M2 再说）
- 不做对话式多轮动画调整（M2）
- 不做时间轴可视化编辑器（M2）

## Technical Considerations

- **模型选择**: VLM 用 claude-opus-4-7（精度）或 claude-sonnet-4-6（速度/成本平衡）
- **JSON 稳定性**: VLM 调用使用 tool_use 强制结构化输出，避免 JSON parse 失败
- **成本控制**: perception / segment 结果都缓存到磁盘；同一 image_id 不重复调用
- **Replicate 异步**: SAM2 和 inpaint 调用耗时较长，后端需支持轮询或 webhook（M1 用同步阻塞 + 前端 loading 即可）
- **CORS**: 开发环境前端 5173，后端 8000，需允许跨域
- **存储**: M1 用本地文件系统 `backend/storage/`，后续可替换为 S3

## Success Metrics

- 从上传到看到动画播放 ≤ 60s（含所有 AI 调用）
- 元素识别准确率 ≥ 80%（人工在 10 张测试图上评估）
- 5 个动画原语全部可通过自然语言触发
- GIF 导出在 15s 内完成

## Open Questions

- 元素识别错误时的兜底 UX？（M1 暂不做手动修补，用户只能重传）
- 动画 prompt 过于模糊时（"让它有活力"），是否返回预设风格 or 追问？M1 先默认套"全部元素轻微呼吸/摇摆"
- 是否需要每一步的中间状态页（perception 结果预览 vs 直接跳到编辑器）？当前方案是直接跳到编辑器边加载边显示
