# PRD: 精确轮廓分割（Precise Segmentation）

## Introduction

当前 Make It Lively 的元素检测基于 VLM 返回的包围盒（bounding box），分割阶段仅以 bbox 中心点作为 SAM2 的 `click_coordinates` 单点输入。这导致两个核心问题：

1. **分割精度不足**：单点 click 常常无法精确匹配复杂形状（如展翅的鸟、不规则的云朵），产出的 mask 边缘粗糙或遗漏部分区域。
2. **动画基于矩形**：后续动画的 transform-origin、旋转/缩放锚点都基于 bbox 几何中心而非形状真实质心，导致动画不够生动自然。

本 PRD 的目标是通过增强 SAM2 输入策略 + 轮廓后处理，显著提升元素分割精度，为后续轮廓驱动的动画系统升级打下基础。

> **范围说明**：本 PRD 聚焦 **Phase 1（MVP）— 分割精度提升**。动画系统的轮廓感知改造（形变动画、沿轮廓路径运动等）将作为 Phase 2 单独规划。

## Goals

- SAM2 分割从单点 click 升级为 **box prompt + 多点采样**，mask 精度大幅提升
- 从 mask 中提取 **精确轮廓（contour）** 和 **形状质心（centroid）**，存入元素元数据
- 无 Replicate token 时，fallback 方案从矩形裁切升级为 **OpenCV GrabCut + alpha matting 本地分割**
- 分割结果增加 **alpha 羽化边缘**，消除硬切割的锯齿感
- **向后兼容**：现有 API 契约和前端渲染逻辑无需改动即可工作；轮廓/质心信息作为可选字段增量返回

## User Stories

### US-014: SAM2 Box Prompt 增强
**Description:** As the system, I need to provide SAM2 with the element's bounding box as a box prompt (instead of just a center click) so the mask more precisely covers the actual shape.

**Acceptance Criteria:**
- [ ] `segment.py` 中 SAM2 调用方式从 `click_coordinates` 单点改为 `box` prompt（即 `[x1, y1, x2, y2]` 格式）
- [ ] 同时保留 bbox 中心点作为 positive `click_coordinates`，与 box prompt 组合使用以提高置信度
- [ ] 对于有 `parent_id` 的子部件（如 `robot.right_arm`），使用子部件自身的 bbox 而非父级 bbox
- [ ] 在 3 张以上测试图上验证：mask 边界与实际形状的 IoU 相比改进前提升 ≥ 15%
- [ ] 现有 `/api/segment` 请求/响应格式不变，前端无需改动
- [ ] Typecheck passes
- [ ] Tests pass

### US-015: 多点采样策略
**Description:** As the system, I need to generate multiple sample points within the bounding box to guide SAM2 when the element shape is complex or off-center.

**Acceptance Criteria:**
- [ ] 新增 `_generate_sample_points(bbox, n=5)` 函数，在 bbox 内按网格或随机策略生成 N 个候选点
- [ ] 将中心点 + 额外采样点一起作为 positive points 传给 SAM2
- [ ] 采样点数量可通过配置控制（默认 5 个），避免过多点导致 SAM2 API 超时
- [ ] 对狭长形状（如蛇、绳索）验证：多点采样比单中心点效果更好
- [ ] Tests pass（含采样点生成的单元测试）
- [ ] Typecheck passes

### US-016: Mask 后处理 — 轮廓提取与质心计算
**Description:** As the system, I need to extract the precise contour polygon and shape centroid from the SAM2 mask so downstream systems can use shape-aware information.

**Acceptance Criteria:**
- [ ] 新增 `backend/app/services/contour.py` 模块
- [ ] `extract_contour(mask_image) → list[list[float]]`：从二值 mask 中使用 OpenCV `findContours` 提取最大连通区域的外轮廓，返回简化后的多边形顶点序列 `[[x1,y1], [x2,y2], ...]`
- [ ] `compute_centroid(contour) → [cx, cy]`：计算轮廓多边形的几何质心（使用 `cv2.moments`）
- [ ] 轮廓使用 `cv2.approxPolyDP` 简化，epsilon 可配置（默认 2.0），控制顶点数量在 20~100 范围内
- [ ] 对碎片 mask（多个不连通区域）仅保留面积最大的连通区域，过滤噪声
- [ ] Tests pass（含固定 mask → 轮廓 → 质心的快照测试）
- [ ] Typecheck passes

### US-017: 元素元数据扩展 — contour 与 centroid
**Description:** As the system, I need to persist contour and centroid data alongside each element so the animation planner and frontend can access shape-aware information.

**Acceptance Criteria:**
- [ ] `Element` 模型新增可选字段：`contour: list[list[float]] | None = None`（简化多边形顶点）和 `centroid: list[float] | None = None`（`[cx, cy]` 图像像素坐标）
- [ ] `/api/segment` 响应中每个 layer 增加 `contour` 和 `centroid` 字段
- [ ] 分割完成后，自动调用 US-016 的 `extract_contour` 和 `compute_centroid` 填充这两个字段
- [ ] 前端 `api.ts` 中 `Element` 类型同步更新（可选字段），不影响现有渲染逻辑
- [ ] 缓存格式兼容：旧缓存（无 contour/centroid）可正常读取，字段默认 null
- [ ] Tests pass
- [ ] Typecheck passes

### US-018: Alpha 羽化边缘处理
**Description:** As the system, I need to feather the mask edges so extracted layers blend naturally rather than having harsh pixel-level cutoffs.

**Acceptance Criteria:**
- [ ] 在 `_apply_mask()` 步骤后新增 alpha 羽化处理
- [ ] 使用高斯模糊对 mask 边缘做 feathering（默认 radius=2px），仅影响边缘过渡区域，不模糊内部
- [ ] 羽化半径可通过环境变量 `FEATHER_RADIUS` 配置
- [ ] 视觉验证：提取的图层边缘在画布上与背景衔接自然，无明显白边/锯齿
- [ ] 不影响 mask 内部的不透明度（内部区域 alpha 仍为 255）
- [ ] Tests pass
- [ ] Typecheck passes

### US-019: OpenCV GrabCut 本地 Fallback
**Description:** As the system, I need a local segmentation fallback using OpenCV GrabCut when no Replicate API token is available, replacing the current rectangular crop fallback.

**Acceptance Criteria:**
- [ ] 当 `USE_REPLICATE_FALLBACK=true` 或无 `REPLICATE_API_TOKEN` 时，使用 GrabCut 替代矩形裁切
- [ ] 实现 `_grabcut_segment(image_path, bbox) → PIL.Image`：以 bbox 为初始矩形，调用 `cv2.grabCut` 执行前景分割
- [ ] GrabCut 迭代次数可配置（默认 5 次），平衡精度和性能
- [ ] 分割结果同样经过 US-018 的 alpha 羽化处理
- [ ] 分割结果同样经过 US-016 的轮廓提取和质心计算
- [ ] 在 3 张测试图上验证：GrabCut fallback 相比矩形裁切，边缘精度明显提升
- [ ] 处理时间 ≤ 5s/element（单张中等分辨率图片）
- [ ] Tests pass（含 GrabCut 路径的集成测试）
- [ ] Typecheck passes

### US-020: Inpaint Mask 精确化
**Description:** As the system, I need the inpainting mask to use the precise segmentation contour instead of bounding boxes, so the background reconstruction is cleaner.

**Acceptance Criteria:**
- [ ] `inpaint.py` 中 `_build_combined_mask()` 从使用 `ImageDraw.rectangle()` 改为使用 `cv2.fillPoly()` 基于轮廓多边形填充
- [ ] 当元素无 contour 数据时（旧缓存兼容），回退到 bbox 矩形 mask
- [ ] mask 边缘向外膨胀 3~5px（`cv2.dilate`），确保修补区域完全覆盖元素残影
- [ ] 视觉验证：修补后的背景在元素原始位置无残影
- [ ] Tests pass
- [ ] Typecheck passes

## Functional Requirements

- **FR-1**: SAM2 调用改为 box prompt + 多点正向采样组合输入，替代单点 click
- **FR-2**: 从 SAM2 输出的 mask 中提取简化轮廓多边形（`cv2.approxPolyDP`，20~100 个顶点）
- **FR-3**: 计算轮廓多边形的几何质心作为元素的形状真实中心
- **FR-4**: 对 mask 边缘执行 Gaussian feathering（默认 2px），消除硬边
- **FR-5**: 轮廓和质心数据持久化到分割结果中，通过 API 返回给前端
- **FR-6**: 无 Replicate 时，使用 OpenCV GrabCut 替代矩形裁切作为本地 fallback
- **FR-7**: Inpaint mask 从 bbox 矩形改为基于轮廓多边形填充 + 边缘膨胀
- **FR-8**: 所有新增参数（采样点数、羽化半径、GrabCut 迭代次数、轮廓简化 epsilon）均可通过环境变量或配置控制
- **FR-9**: 向后兼容 — 新增字段均为可选，旧缓存数据可正常读取

## Non-Goals (Out of Scope)

- **不做**动画系统改造（轮廓驱动的 transform-origin、形变动画、沿轮廓路径运动 — 这些属于 Phase 2）
- **不做**前端交互式分割修正（如用户手动画笔修正 mask）
- **不做**实例分割（一个 bbox 内只切一个主体，不做多实例区分）
- **不做**视频/序列帧的分割
- **不做**语义分割（不标注像素类别，只做前景/背景二值分割）

## Design Considerations

- 轮廓数据（`contour` 字段）可能较大（100 个顶点 × 2 坐标），需要在 API 响应中考虑体积。使用 `approxPolyDP` 简化是必要的。
- 前端暂时不消费 `contour`/`centroid` 字段（Phase 1 不改前端渲染），但 TypeScript 类型应同步更新以便 Phase 2 使用。

## Technical Considerations

- **OpenCV 依赖**: 需要在 `pyproject.toml` 中添加 `opencv-python-headless`（无 GUI 依赖，适合服务端）
- **SAM2 API 兼容性**: Replicate 上 `meta/sam-2` 模型需确认 `box` 参数的支持（参考文档：接受 `[x1, y1, x2, y2]` 格式）。如 API 不支持 box prompt，则退回到多点 click 方案。
- **性能**: GrabCut 在高分辨率图片上可能较慢，需要对大图先 resize 到合理尺寸再做 GrabCut，最后将 mask resize 回原始尺寸
- **缓存兼容**: 已有的 `backend/storage/layers/` 和 `backend/storage/perception/` 缓存不需要清除，缺少新字段时回退到默认值
- **numpy/Pillow ↔ OpenCV**: 注意 BGR vs RGB 和 numpy array vs PIL.Image 的转换，封装统一的转换工具函数

## Success Metrics

- 在 10 张测试图上，SAM2 box prompt 分割的 mask IoU（与人工标注对比）≥ 85%，较当前单点 click 方案提升 ≥ 15%
- GrabCut fallback 的 mask 质量主观评分（1-5 分）≥ 3.5，明显优于矩形裁切
- 分割 + 后处理耗时：SAM2 路径 ≤ 当前耗时 × 1.5（多点采样的额外开销可控）；GrabCut 路径 ≤ 5s/element
- 提取的图层在画布上与背景衔接无明显白边、锯齿或残影
- 所有新增代码有对应的单元测试覆盖

## Phase 2 远景（仅记录，不在本 PRD 实施范围内）

以下是分割精度提升后，动画系统可以利用轮廓信息实现的能力升级：

1. **轮廓驱动 transform-origin**：用形状质心替代 bbox 中心计算旋转/缩放锚点，让动画更自然
2. **形变动画原语**：新增 `squash`、`stretch`、`morph` 等基于轮廓的形变动画，让弹性物体（如气球、果冻）有挤压拉伸效果
3. **沿轮廓路径运动**：支持元素沿自身或其他元素的轮廓边缘运动（如蚂蚁沿树叶边缘爬行）
4. **轮廓粒子效果**：基于轮廓生成粒子发射器，实现边缘发光、溶解等特效
5. **Animation planner 轮廓感知**：将 contour + centroid 信息传给动画规划 LLM，让它在编排动画时考虑形状特征

## Open Questions

- SAM2 在 Replicate 上的 `box` prompt 参数是否在当前使用的模型版本中可用？需要实际测试确认，如不可用则改用纯多点方案。
- GrabCut 对半透明物体（如玻璃杯、烟雾）效果较差，是否需要为这类场景准备额外策略？（建议 Phase 1 暂不处理，记录为已知限制）
- 轮廓简化的 epsilon 参数如何在精度和数据量之间取最优平衡？建议先用默认值 2.0，后续根据实际数据调整。
- 是否需要提供一个 `/api/segment` 的 `force_refresh` 参数让用户手动触发重新分割（清除缓存）？
