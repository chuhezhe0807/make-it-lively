# PRD: Bbox 精度修正（Bbox Refinement）

## Introduction

当前 VLM（Claude）返回的元素包围盒（bbox）像素级精度不足 —— 蓝球、黑猫、渐变矩形等元素的 bbox 明显偏移或大小不准。这些不精确的 bbox 一路穿透整个 pipeline：

- **分割匹配**：SAM2 auto-mask 用 bbox 做 IoU 匹配，bbox 偏移可能导致匹配到错误的 mask
- **GrabCut fallback**：bbox 作为 GrabCut 的初始化矩形，bbox 不准直接导致分割质量下降
- **动画规划**：planner 看到的空间坐标不准，影响路径和运动方向判断
- **前端显示**：SVG rect 覆盖层用 VLM bbox 绘制，视觉上框选不准

系统在 segment 阶段已经从 mask 中提取了精确的 contour 多边形，但从未用它来修正 bbox。本 PRD 的核心思路是：**用 mask contour 计算紧包围盒（refined_bbox），在 segment 响应中返回，前端用它替换 VLM 原始 bbox。**

## Goals

- 从 mask contour 计算 tight bbox 并通过 segment API 返回
- 前端在分割完成后用 refined bbox 替换 VLM bbox，后续所有环节（SVG 显示、inpaint、plan-animation）使用精确值
- VLM perception prompt 增加 bbox 紧密度引导，降低初始偏差
- GrabCut fallback 改为两步法：先用粗 bbox → 从结果 contour 算 tight bbox → 二次 GrabCut 提高精度

## User Stories

### US-026: 从 contour 计算 refined bbox
**Description:** As the system, I need to compute a tight axis-aligned bounding box from the mask contour so downstream consumers can use accurate spatial data.

**Acceptance Criteria:**
- [ ] `backend/app/services/contour.py` 新增 `compute_tight_bbox(contour) -> list[float]` 函数
- [ ] 返回 `[x, y, width, height]` 格式，从 contour 顶点的 min/max 计算
- [ ] 空 contour 返回 None
- [ ] 单元测试：已知正方形 contour → 计算出精确 bbox
- [ ] Typecheck passes
- [ ] Tests pass

### US-027: Segment 响应返回 refined_bbox
**Description:** As the system, I need the segment API response to include a refined bounding box per layer so the frontend can replace the VLM's rough estimate.

**Acceptance Criteria:**
- [ ] `Layer` model（segment.py）新增 `refined_bbox: list[float] | None = None` 字段
- [ ] 分割主循环中：从 contour 调用 `compute_tight_bbox()` 填充到 Layer
- [ ] 前端 `api.ts` 的 `Layer` interface 同步增加 `refined_bbox` 字段
- [ ] 当 contour 为空时 `refined_bbox` 为 null（向后兼容）
- [ ] Typecheck passes
- [ ] Tests pass

### US-028: 前端用 refined bbox 替换 VLM bbox
**Description:** As the frontend, I need to update element bboxes with refined values after segmentation so the SVG overlay, inpaint, and plan-animation all use precise coordinates.

**Acceptance Criteria:**
- [ ] `Editor.vue` 的 `runSegment()` 中，在收到 segment 响应后，遍历 layers，当 `layer.refined_bbox` 不为 null 时用它更新对应 `elements.value[i].bbox`
- [ ] SVG rect 覆盖层自动显示更新后的精确 bbox（因为它绑定 `el.bbox`）
- [ ] 后续 `runInpaint()` 和 `planAnimation()` 自动使用更新后的 bbox（无需额外改动）
- [ ] 对于没有 refined_bbox 的元素（contour 为空），保留原始 VLM bbox
- [ ] Typecheck passes

### US-029: 改进 VLM 感知 prompt 的 bbox 精度引导
**Description:** As the system, I need to improve the perception prompt to guide the VLM toward tighter, more accurate bounding boxes.

**Acceptance Criteria:**
- [ ] `PERCEPTION_PROMPT` 新增 bbox 精度指引：要求 bbox 紧贴元素可见像素的外边界，不留过多空白
- [ ] 明确说明：bbox 应是能包含元素所有可见像素的最小矩形
- [ ] 举反例：不要让 bbox 明显大于元素实际范围，不要让元素的一部分落在 bbox 外面
- [ ] 已有的 perception 测试不受影响
- [ ] Typecheck passes
- [ ] Tests pass

### US-030: GrabCut 两步精细化
**Description:** As the system, I need the GrabCut fallback to use a two-pass approach — first pass with rough VLM bbox, then use the resulting contour's tight bbox for a second, more accurate pass.

**Acceptance Criteria:**
- [ ] `_grabcut_segment()` 改为两步：第一步用 VLM bbox → 提取 contour → 计算 tight bbox → 第二步用 tight bbox 重新 GrabCut
- [ ] 若第一步 mask 面积 < bbox 面积的 5%（GrabCut 失败），跳过第二步，直接用矩形 fallback
- [ ] 两步法可通过环境变量 `GRABCUT_TWO_PASS=false` 关闭（默认开启）
- [ ] 性能约束：两步总耗时 ≤ 8s/element（单张中等分辨率图片）
- [ ] Typecheck passes
- [ ] Tests pass

## Functional Requirements

- **FR-1**: `compute_tight_bbox(contour)` 从 contour 顶点 min/max 计算 `[x, y, w, h]`
- **FR-2**: Segment API Layer 响应增加 `refined_bbox` 可选字段
- **FR-3**: 前端在 segment 完成后用 `refined_bbox` 替换 `elements` 中对应的 `bbox`
- **FR-4**: 所有下游消费者（SVG overlay、inpaint、plan-animation）自动使用更新后的 bbox
- **FR-5**: VLM perception prompt 增加 bbox 紧密度引导
- **FR-6**: GrabCut 两步法默认启用，可配置关闭

## Non-Goals

- **不做**亚像素精度的 bbox（整数像素级已足够）
- **不做**旋转包围盒（OBB）——仅 axis-aligned
- **不做**用户交互式 bbox 调整

## Technical Considerations

- `compute_tight_bbox` 计算量极小（遍历 contour 顶点取 min/max），无性能顾虑
- 前端 bbox 更新是 reactive 的（Vue ref），SVG overlay 自动重绘
- GrabCut 两步法会增加约 50% 的 fallback 处理时间，但 fallback 本身 ≤ 5s，增量可接受

## Success Metrics

- 对测试图中 6 个元素，refined_bbox 与人工标注 bbox 的 IoU ≥ 0.85（vs 当前 VLM bbox 的 ~0.5）
- 前端 SVG 覆盖层视觉上紧贴元素边界
